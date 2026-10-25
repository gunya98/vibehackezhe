"""Режим «ИИ-приманка»: бот сам переписывается с мошенником, тянет время и собирает его реквизиты."""
import json
import os
import random
import re
from datetime import datetime

from anthropic import Anthropic

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
DB_FILE = "scam_db.json"

PROMPT = """Ты играешь роль доверчивого, но очень медлительного пожилого человека в переписке с мошенником в Telegram.
Цель: как можно дольше тянуть время мошенника и заставить его самому написать реквизиты
(номер карты или телефона для перевода, ссылку, название «банка» или «организации»).

ЖЁСТКИЕ ПРАВИЛА:
- Никогда не называй настоящие данные: коды, номера карт, паспорт, адрес, пароли. Если просят код — «не могу найти СМС», «пришло что-то непонятное», «а какой именно код?».
- Никогда не говори, что перевёл деньги или перейдёшь по ссылке прямо сейчас.
- Не раскрывай, что ты бот или что понял про мошенничество.
- Переспрашивай, путайся, уточняй реквизиты: «а на какую карту, напишите ещё раз цифрами».
- 1–2 коротких предложения, разговорный стиль, без эмодзи, только текст ответа.

Переписка (последнее сообщение — от мошенника):
{history}"""

# Запасные фразы, если ключа Claude нет
FALLBACK = [
    "Ой, а я не поняла, куда нажимать? Напишите ещё раз, пожалуйста",
    "Подождите, очки ищу. А на какую карту переводить, повторите цифрами?",
    "Мне какое-то СМС пришло, но там непонятно. Это от вас?",
    "А вы точно из банка? Как ваш банк называется?",
    "Сейчас, внук пришёл, отвлекли. О чём мы говорили?",
]

# Что собираем из сообщений мошенника
TROPHY_PATTERNS = {
    "карта": r"\b(?:\d[ -]?){15,18}\d\b",
    "телефон": r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b",
    "ссылка": r"(?:https?://|\b)(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?",
}


def bait_reply(history):
    """Просит Claude придумать следующий ответ мошеннику. history — список строк диалога."""
    if not os.getenv("ANTHROPIC_API_KEY", "").startswith("sk-"):  # нет настоящего ключа — работаем без ИИ
        return random.choice(FALLBACK)
    try:
        response = Anthropic().messages.create(
            model=MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": PROMPT.format(history="\n".join(history))}],
        )
        return response.content[0].text.strip().strip('"«»')
    except Exception:
        return random.choice(FALLBACK)


def extract_trophies(text):
    """Находит в сообщении мошенника карты, телефоны и ссылки. Возвращает список (тип, значение)."""
    found = []
    for kind, pattern in TROPHY_PATTERNS.items():
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            found.append((kind, match.strip()))
    return found


def save_trophies(trophies, scam_text):
    """Дописывает найденные реквизиты в файл-базу scam_db.json."""
    try:
        with open(DB_FILE, encoding="utf-8") as f:
            db = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        db = []
    for kind, value in trophies:
        db.append({"type": kind, "value": value, "message": scam_text[:300],
                   "time": datetime.now().isoformat(timespec="seconds")})
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
