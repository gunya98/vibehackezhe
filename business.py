"""Telegram Business: бот подключён к личному аккаунту и защищает его переписки в фоне."""
import asyncio
import html
import random
from collections import defaultdict, deque

from aiogram import Bot, F, Router
from aiogram.types import BusinessConnection, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import profiles
from checks.bait import bait_reply, extract_trophies, save_trophies
from scoring import analyze
from ui import format_report, photo_to_b64

router = Router()  # «кусок бота», который подключается к главному диспетчеру в app.py

# Все словари ниже живут в памяти: после перезапуска бота они очищаются.
CONNECTIONS = {}                              # id подключения -> данные (кто владелец, какие права)
HISTORY = defaultdict(lambda: deque(maxlen=10))  # (подключение, чат) -> последние 10 сообщений
WARNED = {}                                   # (подключение, чат) -> о каком уровне уже предупредили
BAIT = {}                                     # (подключение, чат) -> номер кнопки, где сейчас работает приманка
PENDING = {}                                  # номер кнопки -> (подключение, чат)
RANK = {"green": 0, "yellow": 1, "red": 2}
BAIT_DELAY = (15, 30)  # сколько секунд бот «читает и думает», прежде чем начать «печатать»
TYPING_SPEED = 5       # символов в секунду, с которыми «печатает» бот (человек с телефона — 3–7)
MAX_TYPING = 25        # дольше стольких секунд «печатать» не будем
# Итого: ответ приходит примерно через 25–55 секунд. Для быстрого демо поставьте BAIT_DELAY = (3, 6).


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
    if connection.is_enabled and str(connection.user.id) not in profiles.PROFILES:
        await bot.send_message(connection.user_chat_id, profiles.QUESTION, reply_markup=profiles.gender_buttons())


# ---------- профиль владельца: пол и возраст ----------

@router.callback_query(F.data.startswith("prof:g:"))
async def on_gender(callback: CallbackQuery):
    """Владелец выбрал пол — спрашиваем возраст."""
    profiles.save(callback.from_user.id, gender=callback.data.split(":")[2])
    await callback.answer()
    await callback.message.edit_text("👤 Сколько лет владельцу аккаунта?", reply_markup=profiles.age_buttons())


@router.callback_query(F.data.startswith("prof:a:"))
async def on_age(callback: CallbackQuery):
    """Владелец выбрал возраст — профиль готов."""
    profiles.save(callback.from_user.id, age=callback.data.split(":")[2])
    name = profiles.persona(profiles.get(callback.from_user.id)).split(",")[0]
    await callback.answer("Сохранено")
    await callback.message.edit_text(f"✅ Готово. В режиме приманки я буду отвечать мошенникам как <b>{name}</b> — "
                                     "в твоём стиле и от твоего лица.\nИзменить: /profile", parse_mode="HTML")


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

    # 1. Пишет сам владелец: просто запоминаем как контекст (приманка выключается кнопкой)
    if message.from_user.id == connection.user.id:
        HISTORY[key].append(f"Владелец: {text}")
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
        key = (connection.id, message.chat.id)
        await bot.send_message(connection.user_chat_id,
                               f"🎣 Мошенник выдал реквизиты, сохранил в базу:\n{found}", parse_mode="HTML",
                               reply_markup=stop_button(BAIT[key]))

    await send_bait_reply(bot, connection, message.chat.id, history + [f"Мошенник: {text}"])


def stop_button(number):
    """Кнопка «Остановить приманку» под сообщениями владельцу."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏹ Остановить приманку", callback_data=f"stop:{number}"),
    ]])


async def show_typing(bot: Bot, chat_id, connection_id, seconds):
    """Показывает мошеннику «печатает…» нужное время.
    Telegram сам гасит этот значок через ~5 секунд, поэтому повторяем его каждые 4 секунды."""
    left = seconds
    while left > 0:
        try:
            await bot.send_chat_action(chat_id, "typing", business_connection_id=connection_id)
        except Exception:
            pass  # «печатает…» — просто украшение, без него тоже работаем
        await asyncio.sleep(min(4, left))
        left -= 4


async def send_bait_reply(bot: Bot, connection, chat_id, history):
    """Придумывает реплику «доверчивого человека» и отправляет её мошеннику от имени владельца."""
    reply = await asyncio.to_thread(bait_reply, history, profiles.get(connection.user.id))
    # Как живой человек: сначала «прочитал и думает», потом «печатает…» — дольше для длинных ответов
    await asyncio.sleep(random.uniform(*BAIT_DELAY))
    if BAIT_DELAY[1]:  # в имитации паузы выключены
        typing_time = min(len(reply) / TYPING_SPEED, MAX_TYPING)
        await show_typing(bot, chat_id, connection.id, typing_time)
    if (connection.id, chat_id) not in BAIT:
        return  # пока «думали», владелец нажал «Остановить»
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
    number = int(callback.data.split(":")[1])
    key = PENDING.get(number)
    if key is None:
        await callback.answer("Кнопка устарела (бот перезапускался)", show_alert=True)
        return
    BAIT[key] = number
    await callback.answer("🎣 Приманка включена")  # сразу, чтобы кнопка не «висела»
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("🎣 Приманка включена. Сейчас отвечу мошеннику и дальше буду вести диалог, "
                                  "а сюда присылать всё, что он выдаст.", reply_markup=stop_button(number))

    connection_id, chat_id = key
    connection = await get_connection(bot, connection_id)
    await send_bait_reply(bot, connection, chat_id, list(HISTORY[key]))


@router.callback_query(F.data.startswith("stop:"))
async def on_stop_button(callback: CallbackQuery):
    """Владелец нажал «Остановить приманку»."""
    key = PENDING.get(int(callback.data.split(":")[1]))
    if key not in BAIT:
        await callback.answer("Приманка уже выключена")
        return
    del BAIT[key]
    await callback.answer("⏹ Приманка выключена")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("⏹ Приманка выключена. Дальше переписку ведёшь ты — я снова только проверяю "
                                  "сообщения и предупреждаю.")


@router.callback_query(F.data == "ignore")
async def on_ignore_button(callback: CallbackQuery):
    """Владелец отказался от приманки."""
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Хорошо. Просто не отвечай ему и не переходи по ссылкам")
