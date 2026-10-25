"""Режим «ИИ-приманка»: бот сам переписывается с мошенником, тянет время и собирает его реквизиты."""
import json
import random
import re
from datetime import datetime


from checks.bait_phrases import DEFAULT, pick_topic
from checks.llm import ask_llm, provider
from profiles import DEFAULT as DEFAULT_PROFILE, feminize, persona, style_examples

DB_FILE = "scam_db.json"

PROMPT = """Ты играешь роль владельца Telegram-аккаунта. Тебе пишет мошенник,
но ты делаешь вид, что ничего не понимаешь: вроде хочешь помочь, но тупишь, отвлекаешься и переспрашиваешь.

КТО ТЫ: {persona}
Говори о себе в соответствующем роде (мужском или женском) и в стиле своего возраста.

ЦЕЛЬ: тянуть время и выманивать у мошенника реквизиты — номер карты или телефона, ссылку, имя, «отдел».

ЖЁСТКИЕ ПРАВИЛА:
- Никогда не называй настоящие данные: коды, номера карт, паспорт, адрес, пароли. Код «не пришёл», «пришло два», «смахнул», «пишет неверный».
- Никогда не говори, что уже перевёл деньги или ввёл данные.
- Не раскрывай, что понял про мошенничество и что ты бот.
- Не повторяй свои прошлые реплики из переписки.

КАК ПИСАТЬ: одно короткое сообщение (обычно 3–15 слов), как в мессенджере, без эмодзи, кавычек и подписей — только текст.

Примеры того, как ты пишешь:
{style}

Подходящие по смыслу реплики (перескажи по-своему, в своём стиле):
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


def fallback_reply(history, profile=None):
    """Ответ без ИИ: берём фразу по теме последнего сообщения мошенника, которой ещё не было в диалоге."""
    last = next((line for line in reversed(history) if not line.startswith("Владелец:")), "")
    phrases = pick_topic(last)
    used = "\n".join(history)
    fresh = [p for p in phrases if p not in used] or [p for p in DEFAULT if p not in used] or phrases
    reply = random.choice(fresh)
    if profile and profile["gender"] == "f":
        reply = feminize(reply)
    return reply


def humanize(text, lowercase=True):
    """Делает ответ ИИ похожим на сообщение живого человека: без кавычек, часто с маленькой буквы, без точки."""
    text = text.strip().strip('"«»').strip()
    text = re.sub(r"^[А-ЯЁA-Z][\w ]{0,25}:\s*", "", text)  # ИИ иногда подписывает реплику: «Серёга: ...»
    text = text.split("\n")[0]  # только первая строка
    if lowercase and text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    if lowercase and random.random() < 0.6 and text[:1].isupper():
        text = text[0].lower() + text[1:]
    return text


def bait_reply(history, profile=None):
    """Просит ИИ придумать следующий ответ мошеннику в роли владельца. Нет ИИ или ошибка — берём заготовку."""
    profile = profile or DEFAULT_PROFILE
    if provider() is None:
        return fallback_reply(history, profile)
    # Подсказываем ИИ стиль: 3 заготовки по теме последнего сообщения мошенника
    last = next((line for line in reversed(history) if not line.startswith("Владелец:")), "")
    examples = "\n".join(f"- {p}" for p in random.sample(pick_topic(last), k=min(3, len(pick_topic(last)))))
    try:
        prompt = PROMPT.format(persona=persona(profile), style="\n".join(f"- {p}" for p in style_examples(profile)),
                               examples=examples, history="\n".join(history))
        reply = ask_llm(prompt, max_tokens=80)
        return humanize(reply, lowercase=profile["age"] in ("young", "adult")) or fallback_reply(history, profile)
    except Exception as e:
        print(f"[ИИ] ошибка приманки: {e}")
        return fallback_reply(history, profile)


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
