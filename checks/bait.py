"""Режим «ИИ-приманка»: бот сам переписывается с мошенником, тянет время и собирает его реквизиты."""
import json
import random
import re
from datetime import datetime


from checks.bait_phrases import DEFAULT, pick_topic
from checks.llm import ask_llm, provider

DB_FILE = "scam_db.json"

PROMPT = """Ты — Серёга, 41 год, водитель-экспедитор из Подмосковья. Тебе в Telegram пишет мошенник,
но ты делаешь вид, что ничего не понимаешь. Ты вроде хочешь помочь, но постоянно тупишь, отвлекаешься и переспрашиваешь.
О себе (используй иногда, к месту): жена Света, сын Димка в 5 классе, телефон старый с треснутым экраном,
банковское приложение «вечно глючит», часто за рулём или на погрузке.

ЦЕЛЬ: тянуть время и выманивать у мошенника реквизиты — номер карты или телефона, ссылку, имя, «отдел».

ЖЁСТКИЕ ПРАВИЛА:
- Никогда не называй настоящие данные: коды, номера карт, паспорт, адрес, пароли. Код «не пришёл», «пришло два», «смахнул», «пишет неверный».
- Никогда не говори, что уже перевёл деньги или ввёл данные.
- Не раскрывай, что понял про мошенничество и что ты бот.
- Не повторяй свои прошлые реплики из переписки.

КАК ПИСАТЬ (как живой человек в мессенджере):
- одно короткое сообщение, обычно 3–15 слов;
- часто с маленькой буквы, без точки в конце, запятые можно пропускать;
- разговорные слова: щас, чё, норм, ага, блин, слушай, короче, не понял;
- никаких эмодзи, кавычек, пояснений, подписей — только текст сообщения.

Примеры того, как ты пишешь:
- щас погоди, я за рулём, на светофоре гляну
- а какой код, там два пришло
- блин приложение опять вылетело. на какую карту говоришь?
- слушай а ты из какого отделения вообще
- ссылка не грузит, белый экран. другая есть?
- не понял, мне чё сделать надо
{examples}

Переписка (последнее сообщение — от мошенника):
{history}

Твой ответ:"""

# Что собираем из сообщений мошенника
TROPHY_PATTERNS = {
    "карта": r"\b(?:\d[ -]?){15,18}\d\b",
    "телефон": r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b",
    "ссылка": r"(?:https?://|\b)(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?",
}


def fallback_reply(history):
    """Ответ без ИИ: берём фразу по теме последнего сообщения мошенника, которой ещё не было в диалоге."""
    last = next((line for line in reversed(history) if not line.startswith("Владелец:")), "")
    phrases = pick_topic(last)
    used = "\n".join(history)
    fresh = [p for p in phrases if p not in used] or [p for p in DEFAULT if p not in used] or phrases
    return random.choice(fresh)


def humanize(text):
    """Делает ответ ИИ похожим на сообщение живого человека: без кавычек, часто с маленькой буквы, без точки."""
    text = text.strip().strip('"«»').strip()
    for prefix in ("Серёга:", "Владелец:", "Ответ:"):  # ИИ иногда подписывает реплику
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    text = text.split("\n")[0]  # только первая строка
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    if random.random() < 0.6 and text[:1].isupper():
        text = text[0].lower() + text[1:]
    return text


def bait_reply(history):
    """Просит ИИ придумать следующий ответ мошеннику. Нет ИИ или ошибка — берём заготовку."""
    if provider() is None:
        return fallback_reply(history)
    # Подсказываем ИИ стиль: 3 заготовки по теме последнего сообщения мошенника
    last = next((line for line in reversed(history) if not line.startswith("Владелец:")), "")
    examples = "\n".join(f"- {p}" for p in random.sample(pick_topic(last), k=min(3, len(pick_topic(last)))))
    try:
        reply = ask_llm(PROMPT.format(history="\n".join(history), examples=examples), max_tokens=80)
        return humanize(reply) or fallback_reply(history)
    except Exception as e:
        print(f"[ИИ] ошибка приманки: {e}")
        return fallback_reply(history)


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
