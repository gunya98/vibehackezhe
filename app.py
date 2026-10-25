"""Telegram-бот антифрод: перешли подозрительное сообщение — получи оценку и совет."""
import asyncio
import base64
import html
import os
import random
from collections import defaultdict, deque

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import BusinessConnection, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()  # читаем ключи из файла .env

from checks.bait import bait_reply, extract_trophies, save_trophies  # noqa: E402
from scoring import analyze, has_anything_to_check  # noqa: E402

bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
dp = Dispatcher()

WELCOME = (
    "👋 Я проверяю сообщения на мошенничество.\n\n"
    "Перешли мне подозрительное сообщение, ссылку или скриншот — "
    "я скажу, насколько это похоже на развод, почему и что делать.\n\n"
    "Можно добавить меня в групповой чат: я буду предупреждать, если там появится скам."
)


def get_origin(message):
    """Достаёт из пересланного сообщения информацию об отправителе (или None)."""
    o = message.forward_origin
    if o is None:
        return None
    if o.type == "user":
        return {"type": "user", "id": o.sender_user.id, "name": o.sender_user.full_name}
    if o.type == "hidden_user":
        return {"type": "hidden_user", "id": 0, "name": o.sender_user_name}
    if o.type == "channel":
        return {"type": "channel", "id": o.chat.id, "name": o.chat.title}
    return {"type": "chat", "id": o.sender_chat.id, "name": o.sender_chat.title}


async def get_photo_b64(message):
    """Скачивает фото из сообщения и превращает его в base64-строку для Claude (или None)."""
    if not message.photo:
        return None
    file = await bot.download(message.photo[-1])  # [-1] — самое большое разрешение
    return base64.b64encode(file.read()).decode()


def format_report(report):
    """Собирает текст ответа из отчёта (HTML-разметка Telegram)."""
    lines = [f"<b>{report['emoji']} {report['title']}</b>",
             f"Уверенность проверки: {int(report['certainty'] * 100)}%", ""]
    for s in report["signals"]:
        lines.append(f"<b>{s['name']}</b>\n{html.escape(s['explanation'])}\n")
    lines.append("<b>✅ Что делать</b>")
    lines += [f"• {html.escape(a)}" for a in report["advice"]]
    lines.append("\n<i>Это подсказка, а не приговор: 100% гарантии не даёт никто.</i>")
    return "\n".join(lines)


@dp.message(CommandStart())
async def start(message: Message):
    """Ответ на команду /start."""
    await message.answer(WELCOME)


@dp.message(F.chat.type == "private")
async def check_private(message: Message):
    """Личный чат: проверяем всё, что прислали."""
    text = message.text or message.caption or ""
    await bot.send_chat_action(message.chat.id, "typing")
    image = await get_photo_b64(message)
    # analyze работает медленно (ждёт ИИ), поэтому запускаем в отдельном потоке
    report = await asyncio.to_thread(analyze, text, image, get_origin(message))
    await message.reply(format_report(report), parse_mode="HTML")


@dp.message()
async def check_group(message: Message):
    """Групповой чат: молчим, пока не увидим что-то подозрительное."""
    text = message.text or message.caption or ""
    if not has_anything_to_check(text):
        return
    report = await asyncio.to_thread(analyze, text, None, get_origin(message))
    if report["level"] != "green":
        await message.reply(format_report(report), parse_mode="HTML")


# ---------- Telegram Business: защита личного аккаунта ----------

@dp.business_connection()
async def on_business_connect(connection: BusinessConnection):
    """Срабатывает, когда человек подключил (или отключил) бота к своему аккаунту в Telegram Business."""
    if connection.is_enabled:
        await bot.send_message(connection.user_chat_id,
                               "🛡 Защита включена. Я проверяю входящие сообщения в твоей личке "
                               "и предупрежу здесь, если тебе напишет мошенник.")


# Память диалогов: для каждого чата храним последние 10 сообщений
HISTORY = defaultdict(lambda: deque(maxlen=10))
# Какой уровень угрозы мы уже сообщили владельцу по каждому чату
WARNED = {}
RANK = {"green": 0, "yellow": 1, "red": 2}
# Чаты, где включена приманка: chat_id -> business_connection_id
BAIT = {}


def bait_buttons(chat_id):
    """Кнопки под предупреждением: включить приманку или игнорировать."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎣 Пусть бот ведёт диалог", callback_data=f"bait:{chat_id}"),
        InlineKeyboardButton(text="🙅 Не надо", callback_data="ignore"),
    ]])


async def bait_step(message: Message, history):
    """Режим приманки: собираем реквизиты мошенника и отвечаем ему от имени владельца."""
    chat_id = message.chat.id
    text = message.text or message.caption or ""

    trophies = extract_trophies(text)
    if trophies:
        save_trophies(trophies, text)
        owner_chat = (await bot.get_business_connection(BAIT[chat_id])).user_chat_id
        found = "\n".join(f"• {kind}: <code>{html.escape(value)}</code>" for kind, value in trophies)
        await bot.send_message(owner_chat, f"🎣 Мошенник выдал реквизиты, сохранил в базу:\n{found}",
                               parse_mode="HTML")

    reply = await asyncio.to_thread(bait_reply, history + [f"Мошенник: {text}"])
    await asyncio.sleep(random.uniform(3, 7))  # пауза, чтобы было похоже на живого человека
    await bot.send_message(chat_id, reply, business_connection_id=BAIT[chat_id])
    HISTORY[chat_id].append(f"Владелец: {reply}")


@dp.callback_query(F.data.startswith("bait:"))
async def start_bait(callback: CallbackQuery):
    """Владелец нажал «Пусть бот ведёт диалог» — включаем приманку для этого чата."""
    chat_id = int(callback.data.split(":")[1])
    BAIT[chat_id] = PENDING_CONNECTION[chat_id]
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("🎣 Приманка включена. Бот будет отвечать мошеннику и присылать сюда "
                                  "всё, что тот выдаст. Чтобы остановить — просто напиши в тот чат сам.")
    await callback.answer()


@dp.callback_query(F.data == "ignore")
async def ignore_bait(callback: CallbackQuery):
    """Владелец отказался от приманки."""
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Хорошо, просто не отвечай ему")


# Для кнопки: из какого business-подключения пришёл подозрительный чат
PENDING_CONNECTION = {}


@dp.business_message()
@dp.edited_business_message()
async def check_business(message: Message):
    """Новое (или отредактированное) сообщение в личке владельца: проверяем весь диалог и предупреждаем."""
    if message.sender_business_bot:
        return  # это наш же ответ в режиме приманки — уже записан в историю

    connection = await bot.get_business_connection(message.business_connection_id)
    text = message.text or message.caption or ""
    chat_id = message.chat.id

    if message.from_user.id == connection.user.id:
        HISTORY[chat_id].append(f"Владелец: {text}")  # ответы владельца — тоже контекст
        if chat_id in BAIT:
            del BAIT[chat_id]  # владелец взял диалог на себя — приманка выключается
            await bot.send_message(connection.user_chat_id, "✋ Ты ответил сам — приманка выключена.")
        return

    history = list(HISTORY[chat_id])
    HISTORY[chat_id].append(f"Собеседник: {text}")

    if chat_id in BAIT:
        await bait_step(message, history)
        return

    if not text:
        return

    report = await asyncio.to_thread(analyze, text, None, None, True, history)

    # Предупреждаем один раз на чат и повторно — только если угроза выросла
    if RANK[report["level"]] <= RANK[WARNED.get(chat_id, "green")]:
        return
    WARNED[chat_id] = report["level"]

    sender = html.escape(message.from_user.full_name)
    preview = html.escape(text[:150])
    edited = " (отредактировано)" if message.edit_date else ""
    warning = f"⚠️ <b>Сообщение от {sender}{edited}</b>:\n<i>«{preview}»</i>\n\n" + format_report(report)
    # Кнопку приманки предлагаем только при 🔴 и если владелец разрешил боту отвечать
    buttons = None
    if report["level"] == "red" and connection.can_reply:
        PENDING_CONNECTION[chat_id] = message.business_connection_id
        buttons = bait_buttons(chat_id)
    await bot.send_message(connection.user_chat_id, warning, parse_mode="HTML", reply_markup=buttons)


async def main():
    """Запускает бота: он постоянно спрашивает Telegram о новых сообщениях."""
    print("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
