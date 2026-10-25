"""Имитация Telegram Business без интернета и токена: python simulate_business.py

Подставляет «фальшивый Telegram», прогоняет сценарий мошенника и печатает, что бот отправил бы.
Нужен, чтобы проверить логику Business-режима, пока нет Premium или токена.
"""
import asyncio
import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "123456:TEST_TOKEN_FOR_SIMULATION_ONLY_xxxxxxxx")

from aiogram import Bot  # noqa: E402
from aiogram.methods import ReadBusinessMessage  # noqa: E402
from aiogram.methods import EditMessageText, GetBusinessConnection, SendMessage  # noqa: E402
from aiogram.types import BusinessConnection, Message, Update  # noqa: E402

import app  # noqa: E402
import business  # noqa: E402
import profiles  # noqa: E402

profiles.PROFILES_FILE = "/tmp/sim_profiles.json"  # имитация не трогает настоящие профили
profiles.PROFILES.clear()

OWNER = {"id": 111, "is_bot": False, "first_name": "Владелец"}
SCAMMER = {"id": 7_777_000_001, "is_bot": False, "first_name": "Служба", "last_name": "Безопасности"}
CONN_ID = "test-connection"
NOW = int(datetime.now().timestamp())


class FakeBot(Bot):
    """Бот, который вместо отправки в Telegram печатает запросы на экран."""

    async def __call__(self, method, request_timeout=None):
        if isinstance(method, GetBusinessConnection):
            return BusinessConnection(id=CONN_ID, user=OWNER, user_chat_id=111, date=NOW, is_enabled=True,
                                      rights={"can_reply": True})
        if isinstance(method, SendMessage):
            who = "МОШЕННИКУ от имени владельца" if method.business_connection_id else "ВЛАДЕЛЬЦУ"
            buttons = ""
            if method.reply_markup:
                buttons = " [" + ", ".join(b.text for row in method.reply_markup.inline_keyboard for b in row) + "]"
            print(f"\n  📤 Бот → {who}{buttons}:\n    " + method.text.replace("\n", "\n    ")[:600])
            return Message(message_id=1, date=NOW, chat={"id": method.chat_id, "type": "private"},
                           text=method.text)
        if isinstance(method, ReadBusinessMessage):
            print(f"  ✔✔ Бот отметил прочитанным сообщение №{method.message_id}")
        if isinstance(method, EditMessageText):
            print(f"\n  ✏️ Бот изменил сообщение владельцу:\n    {method.text}")
        return True  # остальные запросы (убрать кнопки, ответить на нажатие) считаем успешными


bot = FakeBot(token=os.environ["TELEGRAM_TOKEN"])
counter = 0


async def send(update_type, payload):
    """Подаёт боту одно событие, как будто оно пришло из Telegram."""
    global counter
    counter += 1
    await app.dp.feed_update(bot, Update.model_validate({"update_id": counter, update_type: payload},
                                                        context={"bot": bot}))
    pending = [t for t in business.REPLY_TASKS.values() if not t.done()]  # ждём запланированные ответы
    await asyncio.gather(*pending, return_exceptions=True)


def msg(sender, text):
    return {"message_id": counter + 100, "date": NOW, "business_connection_id": CONN_ID,
            "chat": {"id": SCAMMER["id"], "type": "private"}, "from": sender, "text": text}


async def main():
    business.BAIT_DELAY = (0, 0)  # без пауз в имитации
    business.HOOK_AFTER = (3600, 3600)  # «крючки» в основной имитации не нужны

    print("▶ Владелец подключает бота")
    await send("business_connection", {"id": CONN_ID, "user": OWNER, "user_chat_id": 111, "date": NOW,
                                       "is_enabled": True, "rights": {"can_reply": True}})

    print("\n▶ Владелец выбирает: Женщина, 65+")
    for i, data in enumerate(["prof:g:f", "prof:a:senior"]):
        await send("callback_query", {"id": f"p{i}", "from": OWNER, "chat_instance": "x", "data": data,
                                      "message": {"message_id": 3, "date": NOW,
                                                  "chat": {"id": 111, "type": "private"}, "text": "q"}})

    for text in ["Здравствуйте! Это служба безопасности Сбербанка.",
                 "По вашей карте подозрительная операция, на вас пытаются оформить кредит.",
                 "Сейчас придёт код из СМС, продиктуйте его, чтобы мы отменили операцию"]:
        print(f"\n▶ Мошенник: {text}")
        await send("business_message", msg(SCAMMER, text))

    print("\n▶ Владелец нажимает «🎣 Пусть бот ведёт диалог»")
    await send("callback_query", {"id": "cb1", "from": OWNER, "chat_instance": "x", "data": "bait:1",
                                  "message": {"message_id": 5, "date": NOW,
                                              "chat": {"id": 111, "type": "private"}, "text": "warning"}})

    text = "Не можете найти код? Тогда переведите деньги на безопасный счёт: карта 2202 2063 1234 5678"
    print(f"\n▶ Мошенник: {text}")
    await send("business_message", msg(SCAMMER, text))

    print("\n▶ Владелец нажимает «⏹ Остановить приманку»")
    await send("callback_query", {"id": "cb2", "from": OWNER, "chat_instance": "x", "data": "stop:1",
                                  "message": {"message_id": 6, "date": NOW,
                                              "chat": {"id": 111, "type": "private"}, "text": "bait"}})

    text = "Алло, вы тут? Переводите быстрее"
    print(f"\n▶ Мошенник: {text}  (бот должен промолчать)")
    await send("business_message", msg(SCAMMER, text))

    print("\n✅ Имитация завершена")


if __name__ == "__main__":
    asyncio.run(main())
