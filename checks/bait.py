"""Режим «ИИ-приманка»: бот сам переписывается с мошенником, тянет время и собирает его реквизиты."""
import json
import random
import re
from datetime import datetime


from checks.bait_phrases import DEFAULT, pick_topic
from checks.llm import ask_llm, provider
from profiles import DEFAULT as DEFAULT_PROFILE, feminize, persona, style_examples

DB_FILE = "scam_db.json"

SYSTEM_PROMPT = """Ты — живой человек, владелец этого Telegram-аккаунта. Тебе пишет мошенник.
Ты делаешь вид, что веришь и хочешь помочь, но тупишь, отвлекаешься и переспрашиваешь.
Цель — тянуть время и выманить у него реквизиты: номер карты или телефона, ссылку, имя, «отдел».

КТО ТЫ: {persona}

КАК ТЫ ПИШЕШЬ:
- ОДНО короткое сообщение, 2–12 слов. Никогда не пиши абзацы и списки.
- Как в мессенджере: без приветствий, без «Конечно», «Понимаю», «Спасибо за информацию».
- Не объясняй, не рассуждай, не извиняйся длинно. Просто реагируй, как человек на бегу.
- Без эмодзи, кавычек и подписей. Только текст сообщения.
- Говори в своём роде (мужском или женском) и в стиле своего возраста.

НЕЛЬЗЯ:
- называть коды, номера карт, паспорт, адрес, пароли (код «не пришёл», «пришло два», «смахнул», «пишет неверный»);
- говорить, что уже перевёл деньги или ввёл данные;
- показывать, что понял про мошенничество или что ты бот;
- повторять свои прошлые реплики.

Так ты пишешь:
{style}
{examples}"""

USER_PROMPT = """Переписка («Он» — мошенник, «Ты» — ты):
{history}

Напиши своё следующее сообщение. Коротко, 2–12 слов."""

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


# Канцелярские фразы, которыми ИИ любит начинать ответ, а живые люди так не пишут
FILLERS = r"^(конечно|понимаю|хорошо|спасибо за информацию|извините|здравствуйте|добрый день)[,!.]?\s+"
# Если в предложении есть такое — это «голос робота», выкидываем предложение целиком
ROBOTIC = ("озабочен", "понимаю ваш", "благодарю", "информаци", "к сожалению", "уважаем", "обратите внимание",
           "как языковая", "как ии", "я не могу помочь")


def humanize(text, lowercase=True, max_words=14):
    """Делает ответ ИИ похожим на сообщение живого человека: коротко, без кавычек, подписей и канцелярита."""
    text = text.strip().strip('"«»').strip()
    text = re.sub(r"^[А-ЯЁA-Z][\w ]{0,25}:\s*", "", text)  # ИИ иногда подписывает реплику: «Ты: ...»
    text = text.split("\n")[0].strip()                     # только первая строка
    text = re.sub(FILLERS, "", text, flags=re.IGNORECASE).strip()

    # Длинный ответ режем до первых 1–2 предложений, которые укладываются в лимит слов
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    human = [x for x in sentences if not any(r in x.lower() for r in ROBOTIC)]
    sentences = human or sentences
    short = sentences[0]
    if len(sentences) > 1 and len((short + " " + sentences[1]).split()) <= max_words:
        short += " " + sentences[1]
    words = short.split()
    if len(words) > max_words:
        short = " ".join(words[:max_words])
    text = re.sub(r"^(однако|тем не менее|впрочем|также)[,]?\s+", "", short.strip(), flags=re.IGNORECASE)

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

    # 3 заготовки по теме последнего сообщения мошенника — как пример, что примерно ответить
    last = next((line for line in reversed(history) if not line.startswith("Владелец:")), "")
    topic = pick_topic(last)
    examples = "\n".join(f"- {p}" for p in random.sample(topic, k=min(3, len(topic))))

    # Последние 8 реплик, подписанные коротко и понятно для модели
    dialog = [line.replace("Владелец:", "Ты:").replace("Собеседник:", "Он:").replace("Мошенник:", "Он:")
              for line in history[-8:]]
    system = SYSTEM_PROMPT.format(persona=persona(profile), examples=examples,
                                  style="\n".join(f"- {p}" for p in style_examples(profile)))
    try:
        reply = ask_llm(USER_PROMPT.format(history="\n".join(dialog)), max_tokens=40,
                        system=system, temperature=0.9)
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
