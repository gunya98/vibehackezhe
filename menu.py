"""Меню бота: кнопки под полем ввода и список команд в синей кнопке «Меню»."""
import html
import json

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, KeyboardButton, Message, ReplyKeyboardMarkup

import profiles
from business import owner_stats

router = Router()

BTN_CONNECT = "🛡 Подключить защиту"
BTN_STATS = "📊 Статистика"
BTN_CAUGHT = "🎣 Пойманные данные"
BTN_PROFILE = "👤 Мой профиль"
BTN_CHECK = "📩 Проверить сообщение"
BTN_HELP = "❓ Как это работает"


def main_menu():
    """Клавиатура-меню, которая всегда видна под полем ввода."""
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text=BTN_CONNECT), KeyboardButton(text=BTN_STATS)],
        [KeyboardButton(text=BTN_CAUGHT), KeyboardButton(text=BTN_PROFILE)],
        [KeyboardButton(text=BTN_CHECK), KeyboardButton(text=BTN_HELP)],
    ])


# Команды для синей кнопки «Меню» слева от поля ввода
COMMANDS = [
    BotCommand(command="menu", description="Открыть меню"),
    BotCommand(command="stats", description="Статистика защиты"),
    BotCommand(command="caught", description="Что выпросили у мошенников"),
    BotCommand(command="profile", description="Кто владелец аккаунта"),
    BotCommand(command="help", description="Как это работает"),
]


@router.message(Command("menu"))
async def show_menu(message: Message):
    """Показывает меню."""
    await message.answer("Меню 👇", reply_markup=main_menu())


@router.message(F.text == BTN_CONNECT)
async def how_to_connect(message: Message):
    """Инструкция: как подключить бота к личному аккаунту."""
    await message.answer(
        "🛡 <b>Как включить защиту лички</b> (нужен Telegram Premium):\n\n"
        "1. Настройки → Telegram для бизнеса → Чат-боты\n"
        "2. Введите имя этого бота\n"
        "3. Доступ: <b>все чаты</b> (иначе пропущу взломанный аккаунт друга)\n"
        "4. Разрешите <b>читать и отвечать на сообщения</b> — это нужно для приманки\n\n"
        "После подключения я пришлю «🛡 Защита включена».", parse_mode="HTML")


@router.message(Command("stats"))
@router.message(F.text == BTN_STATS)
async def stats(message: Message):
    """Статистика защиты владельца."""
    s = owner_stats(message.from_user.id)
    status = "🟢 включена" if s["connected"] else "⚪️ не подключена (или бот перезапускался)"
    await message.answer(
        f"📊 <b>Статистика с последнего запуска бота</b>\n\n"
        f"Защита лички: {status}\n"
        f"Проверено сообщений: <b>{s['checked']}</b>\n"
        f"🔴 Опасных: <b>{s['red']}</b>   🟡 Подозрительных: <b>{s['yellow']}</b>\n"
        f"🎣 Ответов мошенникам в режиме приманки: <b>{s['bait_replies']}</b>\n"
        f"📋 Выпрошено данных мошенников: <b>{len(s['collected'])}</b>", parse_mode="HTML")


@router.message(Command("caught"))
@router.message(F.text == BTN_CAUGHT)
async def caught(message: Message):
    """Что удалось выпросить у мошенников."""
    items = owner_stats(message.from_user.id)["collected"]
    try:
        with open("scam_db.json", encoding="utf-8") as f:
            total = len(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        total = 0
    if not items:
        text = "🎣 В этот запуск у мошенников пока ничего не выпрошено."
    else:
        text = "🎣 <b>Выпрошено у мошенников:</b>\n" + "\n".join(
            f"• {kind}: <code>{html.escape(value)}</code>" for kind, value in items)
    text += f"\n\nВсего в базе (scam_db.json): <b>{total}</b> записей."
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == BTN_PROFILE)
async def my_profile(message: Message):
    """Текущий персонаж приманки и кнопки, чтобы его поменять."""
    p = profiles.get(message.from_user.id)
    name = profiles.persona(p).split(",")[0]
    gender = "мужчина" if p["gender"] == "m" else "женщина"
    await message.answer(f"👤 Сейчас: {gender}, {profiles.AGES[p['age']]}. В приманке я — <b>{name}</b>.\n\n"
                         "Изменить:", parse_mode="HTML")
    await message.answer(profiles.QUESTION, reply_markup=profiles.gender_buttons())


@router.message(F.text == BTN_CHECK)
async def how_to_check(message: Message):
    """Подсказка для ручной проверки."""
    await message.answer("📩 Перешлите мне подозрительное сообщение, ссылку или скриншот — "
                         "я скажу, насколько это похоже на мошенничество, почему и что делать.")


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def help_message(message: Message):
    """Коротко: как работает бот."""
    await message.answer(
        "❓ <b>Как я работаю</b>\n\n"
        "1. Читаю входящие сообщения в вашей личке (через Telegram для бизнеса).\n"
        "2. Проверяю весь диалог: поддельные ссылки, приёмы мошенников, отправителя и ИИ-анализ.\n"
        "3. Если похоже на мошенничество — пишу вам: 🔴/🟡, почему и что делать.\n"
        "4. По кнопке 🎣 могу сам вести диалог с мошенником от вашего имени: вежливо тяну время "
        "и выпрашиваю его данные. Остановить — кнопкой ⏹.\n\n"
        "Я не даю 100% гарантии — это подсказка, а решение всегда за вами.", parse_mode="HTML")
