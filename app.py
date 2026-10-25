"""Точка входа: запускает Telegram-бот антифрода.

Режимы:
- Telegram Business (business.py) — защита личного аккаунта в фоне. ГЛАВНЫЙ.
- Личка с ботом — пересылаешь подозрительное сообщение, получаешь отчёт.
- Групповой чат — бот молчит и отвечает только на скам.
"""
import asyncio
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()  # читаем ключи из файла .env

import profiles  # noqa: E402
from business import router as business_router  # noqa: E402
from menu import COMMANDS, main_menu, router as menu_router  # noqa: E402
from scoring import analyze, has_anything_to_check  # noqa: E402
from ui import format_report, photo_to_b64  # noqa: E402

bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
dp = Dispatcher()
checks_router = Router()            # проверка присланных сообщений — срабатывает последней
dp.include_router(menu_router)      # меню (раньше остальных, чтобы кнопки не проверялись как сообщения)
dp.include_router(business_router)  # Business-режим

WELCOME = (
    "👋 Я защищаю от мошенников в Telegram.\n\n"
    "🛡 <b>Защита лички (Telegram Premium):</b> Настройки → Telegram для бизнеса → Чат-боты → "
    "добавь меня и разреши отвечать на сообщения. Я буду проверять входящие сообщения в фоне "
    "и предупреждать тебя здесь.\n\n"
    "📩 <b>Без Premium:</b> просто перешли мне подозрительное сообщение или скриншот.\n\n"
    "👤 /profile — кто владелец аккаунта (чтобы я правдоподобно отвечал мошенникам)."
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


@dp.message(CommandStart())
async def start(message: Message):
    """Ответ на команду /start."""
    await message.answer(WELCOME, parse_mode="HTML", reply_markup=main_menu())


@dp.message(Command("profile"))
async def profile(message: Message):
    """Команда /profile: заново выбрать пол и возраст владельца."""
    await message.answer(profiles.QUESTION, reply_markup=profiles.gender_buttons())


@checks_router.message(F.chat.type == "private")
async def check_private(message: Message, bot: Bot):
    """Личка с ботом: проверяем всё, что прислали."""
    text = message.text or message.caption or ""
    await bot.send_chat_action(message.chat.id, "typing")
    image = await photo_to_b64(bot, message)
    # analyze работает медленно (ждёт ИИ), поэтому запускаем в отдельном потоке
    report = await asyncio.to_thread(analyze, text, image, get_origin(message))
    await message.reply(format_report(report), parse_mode="HTML")


@checks_router.message()
async def check_group(message: Message):
    """Групповой чат: молчим, пока не увидим что-то подозрительное."""
    text = message.text or message.caption or ""
    if not has_anything_to_check(text):
        return
    report = await asyncio.to_thread(analyze, text, None, get_origin(message))
    if report["level"] != "green":
        await message.reply(format_report(report), parse_mode="HTML")


dp.include_router(checks_router)  # подключаем после того, как к нему добавлены обработчики


async def main():
    """Запускает бота: он постоянно спрашивает Telegram о новых сообщениях."""
    await bot.set_my_commands(COMMANDS)  # команды в синей кнопке «Меню»
    print("Бот запущен. Остановить: Ctrl+C")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
