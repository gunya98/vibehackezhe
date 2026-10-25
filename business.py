"""Telegram Business: бот подключён к личному аккаунту и защищает его переписки в фоне."""
import asyncio
import html
import random
from collections import defaultdict, deque

from aiogram import Bot, F, Router
from aiogram.types import BusinessConnection, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from checks.bait import bait_reply, extract_trophies, save_trophies
from scoring import analyze
from ui import format_report, photo_to_b64

router = Router()  # «кусок бота», который подключается к главному диспетчеру в app.py

# Все словари ниже живут в памяти: после перезапуска бота они очищаются.
CONNECTIONS = {}                              # id подключения -> данные (кто владелец, какие права)
HISTORY = defaultdict(lambda: deque(maxlen=10))  # (подключение, чат) -> последние 10 сообщений
WARNED = {}                                   # (подключение, чат) -> о каком уровне уже предупредили
BAIT = set()                                  # (подключение, чат), где сейчас работает приманка
PENDING = {}                                  # номер кнопки -> (подключение, чат)
RANK = {"green": 0, "yellow": 1, "red": 2}
BAIT_DELAY = (3, 7)  # сколько секунд «думает» бот перед ответом мошеннику


async def get_connection(bot: Bot, connection_id):
    """Возвращает данные подключения: из памяти, а если их нет — спрашивает у Telegram."""
    if connection_id not in CONNECTIONS:
        CONNECTIONS[connection_id] = await bot.get_business_connection(connection_id)
    return CONNECTIONS[connection_id]


def can_reply(connection):
    """Разрешил ли владелец боту отвечать от его имени."""
    if connection.rights is not None:
        return bool(connection.rights.can_reply)
    return bool(connection.can_reply)  # старый вариант поля, на всякий случай


# ---------- подключение ----------

@router.business_connection()
async def on_connect(connection: BusinessConnection, bot: Bot):
    """Человек подключил или отключил бота в «Настройки → Telegram для бизнеса → Чат-боты»."""
    CONNECTIONS[connection.id] = connection
    print(f"[business] подключение {connection.user.full_name}: enabled={connection.is_enabled}")
    if connection.is_enabled:
        text = ("🛡 <b>Защита включена.</b>\nЯ проверяю входящие сообщения в твоей личке "
                "и напишу сюда, если тебе напишет мошенник.")
        if not can_reply(connection):
            text += "\n\nЧтобы работал режим приманки, разреши мне отвечать на сообщения в настройках бота."
    else:
        text = "Защита выключена. Чтобы включить снова — подключи меня в настройках Telegram для бизнеса."
    await bot.send_message(connection.user_chat_id, text, parse_mode="HTML")


# ---------- главное: каждое сообщение в личке владельца ----------

@router.business_message()
@router.edited_business_message()
async def on_message(message: Message, bot: Bot):
    """Новое или отредактированное сообщение в личке владельца."""
    if message.sender_business_bot:
        return  # это наш же ответ в режиме приманки

    connection = await get_connection(bot, message.business_connection_id)
    key = (connection.id, message.chat.id)
    text = message.text or message.caption or ""

    # 1. Пишет сам владелец: запоминаем как контекст; если шла приманка — он перехватил диалог
    if message.from_user.id == connection.user.id:
        HISTORY[key].append(f"Владелец: {text}")
        if key in BAIT:
            BAIT.discard(key)
            await bot.send_message(connection.user_chat_id, "✋ Ты ответил сам — приманка выключена.")
        return

    # 2. Пишет собеседник
    history = list(HISTORY[key])
    HISTORY[key].append(f"Собеседник: {text or '(картинка)'}")

    if key in BAIT:
        await bait_step(bot, message, connection, history)
        return

    image = await photo_to_b64(bot, message)
    if not text and not image:
        return  # стикеры, голосовые и т.п. пока не проверяем

    try:
        report = await asyncio.to_thread(analyze, text, image, None, True, history)
    except Exception as e:  # бот не должен падать из-за одной ошибки
        print(f"[business] ошибка анализа: {e}")
        return
    print(f"[business] {message.from_user.full_name}: {report['emoji']} «{text[:50]}»")

    # 3. Предупреждаем один раз на чат и повторно — только если угроза выросла
    if RANK[report["level"]] <= RANK[WARNED.get(key, "green")]:
        return
    WARNED[key] = report["level"]
    await send_warning(bot, message, connection, report, text)


async def send_warning(bot: Bot, message: Message, connection, report, text):
    """Отправляет владельцу предупреждение (и кнопки приманки, если угроза красная)."""
    sender = html.escape(message.from_user.full_name)
    preview = html.escape(text[:150]) if text else "(картинка)"
    edited = " (отредактировано)" if message.edit_date else ""
    warning = f"⚠️ <b>Сообщение от {sender}{edited}</b>:\n<i>«{preview}»</i>\n\n" + format_report(report)

    buttons = None
    if report["level"] == "red" and can_reply(connection):
        number = len(PENDING) + 1  # короткий номер для кнопки (в кнопку влезает максимум 64 символа)
        PENDING[number] = (connection.id, message.chat.id)
        buttons = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎣 Пусть бот ведёт диалог", callback_data=f"bait:{number}"),
            InlineKeyboardButton(text="🙅 Не надо", callback_data="ignore"),
        ]])
    await bot.send_message(connection.user_chat_id, warning, parse_mode="HTML", reply_markup=buttons)


# ---------- режим приманки ----------

async def bait_step(bot: Bot, message: Message, connection, history):
    """Один шаг приманки: забираем реквизиты мошенника и отвечаем ему от имени владельца."""
    text = message.text or message.caption or ""

    trophies = extract_trophies(text)
    if trophies:
        save_trophies(trophies, text)
        found = "\n".join(f"• {kind}: <code>{html.escape(value)}</code>" for kind, value in trophies)
        await bot.send_message(connection.user_chat_id,
                               f"🎣 Мошенник выдал реквизиты, сохранил в базу:\n{found}", parse_mode="HTML")

    await send_bait_reply(bot, connection, message.chat.id, history + [f"Мошенник: {text}"])


async def send_bait_reply(bot: Bot, connection, chat_id, history):
    """Придумывает реплику «доверчивого человека» и отправляет её мошеннику от имени владельца."""
    reply = await asyncio.to_thread(bait_reply, history)
    await asyncio.sleep(random.uniform(*BAIT_DELAY))  # пауза, чтобы было похоже на живого человека
    try:
        await bot.send_message(chat_id, reply, business_connection_id=connection.id)
        HISTORY[(connection.id, chat_id)].append(f"Владелец: {reply}")
        print(f"[business] приманка ответила: «{reply}»")
    except Exception as e:
        print(f"[business] не смог ответить: {e}")
        await bot.send_message(connection.user_chat_id, f"Не получилось ответить мошеннику: {e}\n"
                                                        "Проверь, что у бота есть право отвечать на сообщения.")


@router.callback_query(F.data.startswith("bait:"))
async def on_bait_button(callback: CallbackQuery, bot: Bot):
    """Владелец нажал «Пусть бот ведёт диалог»: включаем приманку и сразу отвечаем мошеннику."""
    key = PENDING.get(int(callback.data.split(":")[1]))
    if key is None:
        await callback.answer("Кнопка устарела (бот перезапускался)", show_alert=True)
        return
    BAIT.add(key)
    await callback.answer("🎣 Приманка включена")  # сразу, чтобы кнопка не «висела»
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("🎣 Приманка включена. Сейчас отвечу мошеннику и дальше буду вести диалог, "
                                  "а сюда присылать всё, что он выдаст. Чтобы остановить — напиши в тот чат сам.")

    connection_id, chat_id = key
    connection = await get_connection(bot, connection_id)
    await send_bait_reply(bot, connection, chat_id, list(HISTORY[key]))


@router.callback_query(F.data == "ignore")
async def on_ignore_button(callback: CallbackQuery):
    """Владелец отказался от приманки."""
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Хорошо. Просто не отвечай ему и не переходи по ссылкам")
