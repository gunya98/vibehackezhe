"""Telegram Business: бот подключён к личному аккаунту и защищает его переписки в фоне."""
import asyncio
import html
import random
import re
from collections import defaultdict, deque

from aiogram import Bot, F, Router
from aiogram.types import BusinessConnection, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import profiles
from checks.rudeness import check_rudeness
from checks.bait import WANTED, bait_reply, extract_trophies, hook_reply, save_trophies
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
HOOK_AFTER = (150, 300)  # если мошенник молчит столько секунд — пишем ему сами, чтобы вернуть
MAX_HOOKS = 2            # не больше стольких «крючков» подряд, чтобы не выглядеть навязчиво

LAST_TEXT = {}                 # (подключение, чат) -> последнее сообщение собеседника (чтобы не отвечать на дубли)
LAST_MSG_ID = {}               # (подключение, чат) -> номер последнего сообщения собеседника (чтобы отметить прочитанным)
READ_DELAY = (2, 6)            # через сколько секунд после сообщения мошенника «прочитать» его
REPLY_TASKS = {}               # (подключение, чат) -> запланированный ответ приманки
HOOK_TASKS = {}                # (подключение, чат) -> запланированный «крючок»
BAIT_REPLIES = defaultdict(int)  # (подключение, чат) -> сколько раз приманка уже ответила
STATS = defaultdict(lambda: {"checked": 0, "red": 0, "yellow": 0})  # id владельца -> счётчики для /stats
COLLECTED = defaultdict(list)    # (подключение, чат) -> что выпросили у мошенника: [(тип, значение), ...]
HOOKS_SENT = defaultdict(int)    # (подключение, чат) -> сколько «крючков» отправлено с последнего ответа мошенника


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
    normalized = re.sub(r"\W+", " ", text.lower()).strip()
    if normalized and normalized == LAST_TEXT.get(key):
        print(f"[business] повтор сообщения — не отвечаем: «{text[:40]}»")
        return
    LAST_TEXT[key] = normalized
    LAST_MSG_ID[key] = message.message_id
    cancel(HOOK_TASKS, key)  # мошенник ответил — «крючок» не нужен
    HOOKS_SENT[key] = 0

    history = list(HISTORY[key])
    HISTORY[key].append(f"Собеседник: {text or '(картинка)'}")

    if key in BAIT:
        rudeness = check_rudeness(text)
        if rudeness:  # собеседник грубит или угрожает — бот выходит из разговора и отдаёт его человеку
            await hand_over(bot, connection, key, rudeness, text)
            return
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
    STATS[connection.user.id]["checked"] += 1
    if report["level"] in ("red", "yellow"):
        STATS[connection.user.id][report["level"]] += 1

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

    key = (connection.id, message.chat.id)
    new = [t for t in extract_trophies(text) if t not in COLLECTED[key]]  # только то, чего ещё не было
    if new:
        save_trophies(new, text)
        COLLECTED[key] += new
        found = "\n".join(f"• {kind}: <code>{html.escape(value)}</code>" for kind, value in new)
        missing = [k for k in WANTED if k not in {kind for kind, _ in COLLECTED[key]}]
        status = ("\n\n✅ Все данные собраны, дальше просто тяну время." if not missing
                  else "\n\nЕщё выпрашиваю: " + ", ".join(missing))
        await bot.send_message(connection.user_chat_id,
                               f"🎣 Мошенник выдал данные, сохранил в базу:\n{found}{status}", parse_mode="HTML",
                               reply_markup=stop_button(BAIT[key]))

    schedule_reply(bot, connection, message.chat.id)


def cancel(tasks, key):
    """Отменяет запланированное действие (ответ или «крючок»), если оно ещё не выполнено."""
    task = tasks.pop(key, None)
    if task and not task.done():
        task.cancel()


def schedule_reply(bot: Bot, connection, chat_id):
    """Планирует ответ мошеннику. Если он пишет ещё до нашего ответа — старый план отменяется,
    и мы отвечаем один раз на всю пачку его сообщений."""
    key = (connection.id, chat_id)
    cancel(REPLY_TASKS, key)
    REPLY_TASKS[key] = asyncio.create_task(send_bait_reply(bot, connection, chat_id))


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


async def mark_read(bot: Bot, connection, chat_id):
    """Отмечает сообщения мошенника прочитанными (две галочки), как сделал бы человек.
    Нужно право «читать сообщения» в настройках чат-бота Telegram для бизнеса."""
    key = (connection.id, chat_id)
    if key not in LAST_MSG_ID:
        return
    try:
        await bot.read_business_message(business_connection_id=connection.id, chat_id=chat_id,
                                        message_id=LAST_MSG_ID[key])
    except Exception as e:
        print(f"[business] не смог отметить прочитанным (проверь право «читать сообщения»): {e}")


async def send_bait_reply(bot: Bot, connection, chat_id, hook=False):
    """Отвечает мошеннику от имени владельца, как живой человек: «читает и думает», «печатает…», отвечает.
    hook=True — это не ответ, а сообщение, чтобы вернуть замолчавшего мошенника."""
    key = (connection.id, chat_id)
    if not hook:
        if BAIT_DELAY[1]:  # в имитации паузы выключены
            await asyncio.sleep(random.uniform(*READ_DELAY))
        await mark_read(bot, connection, chat_id)         # у мошенника появятся две галочки
        await asyncio.sleep(random.uniform(*BAIT_DELAY))  # «думаем»; за это время мошенник может дописать ещё
    history = list(HISTORY[key])                           # берём историю уже со всеми его сообщениями
    profile = profiles.get(connection.user.id)
    if hook:
        reply = await asyncio.to_thread(hook_reply, history, profile)
    else:
        reply = await asyncio.to_thread(bait_reply, history, profile, BAIT_REPLIES[key])
    if BAIT_DELAY[1]:  # в имитации паузы выключены
        await show_typing(bot, chat_id, connection.id, min(len(reply) / TYPING_SPEED, MAX_TYPING))
    if key not in BAIT:
        return  # пока «думали», владелец нажал «Остановить»
    try:
        await bot.send_message(chat_id, reply, business_connection_id=connection.id)
    except Exception as e:
        print(f"[business] не смог ответить: {e}")
        await bot.send_message(connection.user_chat_id, f"Не получилось ответить мошеннику: {e}\n"
                                                        "Проверь, что у бота есть право отвечать на сообщения.")
        return
    HISTORY[key].append(f"Владелец: {reply}")
    BAIT_REPLIES[key] += 1
    print(f"[business] приманка {'крючок' if hook else 'ответила'} (№{BAIT_REPLIES[key]}): «{reply}»")
    if HOOKS_SENT[key] < MAX_HOOKS:
        HOOK_TASKS[key] = asyncio.create_task(hook_later(bot, connection, chat_id))


async def hook_later(bot: Bot, connection, chat_id):
    """Ждёт; если мошенник так и не ответил — пишет ему сам («Вы тут? Я почти всё сделал»)."""
    await asyncio.sleep(random.uniform(*HOOK_AFTER))
    key = (connection.id, chat_id)
    if key in BAIT:
        HOOKS_SENT[key] += 1
        await send_bait_reply(bot, connection, chat_id, hook=True)


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
    schedule_reply(bot, connection, chat_id)


@router.callback_query(F.data.startswith("stop:"))
async def on_stop_button(callback: CallbackQuery):
    """Владелец нажал «Остановить приманку»."""
    key = PENDING.get(int(callback.data.split(":")[1]))
    if key not in BAIT:
        await callback.answer("Приманка уже выключена")
        return
    del BAIT[key]
    cancel(REPLY_TASKS, key)
    cancel(HOOK_TASKS, key)
    await callback.answer("⏹ Приманка выключена")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("⏹ Приманка выключена. Дальше переписку ведёшь ты — я снова только проверяю "
                                  "сообщения и предупреждаю.\n\n" + summary(key), parse_mode="HTML")


HAND_OVER_ADVICE = {
    "угрозы": "Ему не отвечайте. Сохраните скриншоты переписки, заблокируйте аккаунт и пожалуйтесь на него. "
              "Если угрозы касаются вас или близких — обратитесь в полицию (102).",
    "мат": "Можно не отвечать: заблокируйте аккаунт и пожалуйтесь на него (⋮ → Пожаловаться).",
    "оскорбления": "Можно не отвечать: заблокируйте аккаунт и пожалуйтесь на него (⋮ → Пожаловаться).",
}


async def hand_over(bot: Bot, connection, key, rudeness, text):
    """Собеседник начал грубить — выключаем приманку и передаём разговор владельцу."""
    BAIT.pop(key, None)
    cancel(REPLY_TASKS, key)
    cancel(HOOK_TASKS, key)
    print(f"[business] собеседник перешёл на {rudeness} — приманка выключена, разговор передан владельцу")
    await bot.send_message(
        connection.user_chat_id,
        f"🛑 <b>Собеседник перешёл на {rudeness}</b> — я прекратил отвечать и передаю разговор вам.\n"
        f"<i>«{html.escape(text[:150])}»</i>\n\n"
        f"{HAND_OVER_ADVICE[rudeness]}\n\n" + summary(key), parse_mode="HTML")


def owner_stats(owner_id):
    """Статистика защиты для владельца: сколько проверено, сколько угроз, что выпрошено у мошенников."""
    keys = [key for key in set(BAIT_REPLIES) | set(COLLECTED) if CONNECTIONS.get(key[0]) and
            CONNECTIONS[key[0]].user.id == owner_id]
    return {**STATS[owner_id],
            "bait_replies": sum(BAIT_REPLIES[k] for k in keys),
            "collected": [item for k in keys for item in COLLECTED[k]],
            "connected": any(c.user.id == owner_id and c.is_enabled for c in CONNECTIONS.values())}


def summary(key):
    """Итог приманки: сколько раз ответили и что выпросили у мошенника."""
    lines = [f"📋 <b>Итог:</b> бот ответил мошеннику {BAIT_REPLIES[key]} раз(а)."]
    if COLLECTED[key]:
        lines.append("Собрано:")
        lines += [f"• {kind}: <code>{html.escape(value)}</code>" for kind, value in COLLECTED[key]]
        lines.append("Эти данные можно передать в банк и пожаловаться на аккаунт в Telegram.")
    else:
        lines.append("Данных выпросить не удалось.")
    return "\n".join(lines)


@router.callback_query(F.data == "ignore")
async def on_ignore_button(callback: CallbackQuery):
    """Владелец отказался от приманки."""
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Хорошо. Просто не отвечай ему и не переходи по ссылкам")
