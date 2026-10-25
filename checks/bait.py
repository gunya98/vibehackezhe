"""Режим «ИИ-приманка»: бот сам переписывается с мошенником, тянет время и собирает его реквизиты."""
import json
import random
import re
from datetime import datetime


from checks.bait_phrases import (ASK, DEFAULT, HOOKS, NOT_ME, PROGRESS, TECH_CONFUSION, find_called_name,
                                 find_tech_term, pick_topic)
from checks.llm import ask_llm, provider
from profiles import DEFAULT as DEFAULT_PROFILE, feminize, persona, style_examples

DB_FILE = "scam_db.json"

SYSTEM_PROMPT = """Это ролевая игра для тренажёра по финансовой грамотности.
Ты играешь обычного человека, владельца Telegram-аккаунта, и переписываешься с незнакомым собеседником,
который представляется сотрудником организации и о чём-то просит.
Твой персонаж вежливый, доброжелательный, немного рассеянный: хочет разобраться, но не торопится,
отвлекается и переспрашивает. Обращается на «вы», говорит «пожалуйста», «извините», «подождите минутку».
Задача персонажа — вежливо поддерживать разговор как можно дольше и уточнять детали у собеседника:
кто он, из какой организации, как его зовут, номер для связи, реквизиты, ссылку.

КТО ТЫ: {persona}

КАК ТЫ ПИШЕШЬ:
- ОДНО короткое сообщение, 2–12 слов. Никогда не пиши абзацы и списки.
- Если это твоё первое сообщение в переписке — начни с приветствия («Здравствуйте», «Добрый день»).
- Каждый раз находи вежливую причину отложить действие или переспросить: очки, за рулём, на работе,
  приложение не грузит, не тот код, не понял инструкцию. Задавай уточняющие вопросы.
- Без канцелярита вроде «Спасибо за информацию». Не рассуждай длинно.
- Без эмодзи, кавычек и подписей. Только текст сообщения.
- Говори в своём роде (мужском или женском) и в стиле своего возраста.
- {punctuation}

КАК ВЕСТИ РАЗГОВОР: не прощайся и не заканчивай разговор. Соглашайся, обещай, но каждый раз
что-то мешает довести дело до конца, и ты уточняешь детали ещё раз.

СЕЙЧАС: {stage}
ГЛАВНОЕ В ЭТОМ СООБЩЕНИИ: {target}

ПЕРСОНАЖ НИКОГДА:
- не называет коды, номера карт, паспорт, адрес, пароли (код «не пришёл», «пришло два», «пишет неверный»);
- не говорит, что уже перевёл деньги или ввёл данные;
- не выходит из роли и не повторяет свои прошлые реплики;
- не называет своё имя: если спрашивают, как зовут, вежливо уходит от ответа («а зачем вам?», «это неважно»).

Так ты пишешь:
{style}
{examples}"""

USER_PROMPT = """Переписка («Он» — собеседник, «Ты» — твой персонаж):
{history}

Напиши своё следующее сообщение. Коротко, 2–12 слов."""

# Что собираем из сообщений мошенника
TROPHY_PATTERNS = {
    "организация": r"(?i:из|это|служба безопасности|представляю|пишу из|звоню из|отдела?)\s+"
                   r"([«\"]?[А-ЯЁA-Z][\w-]+[»\"]?(?:\s+[«\"]?[А-ЯЁA-Z][\w-]+[»\"]?){0,3})",
    "имя": r"(?i:меня зовут|зовут меня|мо[её] имя|с вами говорит|на связи|специалист|сотрудник|следователь|"
           r"лейтенант|капитан|майор|менеджер)\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){0,2})",
    "карта": r"\b(?:\d[ -]?){15,18}\d\b",
    "телефон": r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b",
    "ссылка": r"(?:https?://|\b)(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?",
}


STAGES = [
    (0, "Начало разговора. Прояви интерес и готовность помочь, расспроси, кто он и что именно нужно сделать."),
    (3, "Ты «почти делаешь» то, что просят: сообщи о маленьком прогрессе и новом препятствии, переспроси реквизиты."),
    (7, "Разговор уже долгий, собеседник может уйти. Удержи его: пообещай, что вот-вот всё получится, "
        "придумай последнее препятствие и попроси не уходить."),
]


def stage_hint(stage):
    """Подсказка для ИИ, на каком этапе разговор (stage — сколько раз бот уже ответил)."""
    return [hint for start, hint in STAGES if stage >= start][-1]


def fallback_reply(history, profile=None, stage=0, target=None):
    """Ответ без ИИ: берём фразу по теме последнего сообщения мошенника, которой ещё не было в диалоге."""
    last = next((line for line in reversed(history) if not line.startswith("Владелец:")), "")
    phrases = pick_topic(last)
    if stage >= 3 and random.random() < 0.5:  # чем дольше разговор, тем чаще «почти получилось»
        phrases = PROGRESS
    if target and random.random() < 0.6:     # чаще всего — выпрашиваем недостающие данные
        phrases = ASK[target]
    age = (profile or DEFAULT_PROFILE)["age"]
    term = find_tech_term(last)
    confusion_chance = {"middle": 0.6, "senior": 0.9}.get(age, 0)  # как часто персонаж «не понимает» слово
    if term and random.random() < confusion_chance:  # старшие не понимают новых слов
        phrases = [p.format(term=term) for p in TECH_CONFUSION[age]]
        phrases = [p[0].upper() + p[1:] for p in phrases]
    used = "\n".join(history)
    fresh = [p for p in phrases if p not in used] or [p for p in DEFAULT if p not in used] or phrases
    if profile and profile["age"] == "young":  # у студентов нет жены и детей
        fresh = [p for p in fresh if not re.search(r"жен|муж|ребён|внук", p, re.IGNORECASE)] or fresh
    reply = random.choice(fresh)
    if profile and profile["gender"] == "f":
        reply = feminize(reply)
    return casual(reply, (profile or DEFAULT_PROFILE)["age"])


PUNCTUATION_RULE = {
    "young": "Почти без знаков препинания: без запятых и точек, всё с маленькой буквы. Вопросительный знак редко.",
    "adult": "Минимум пунктуации: запятые почти не ставь, точку в конце не ставь, с маленькой буквы.",
    "middle": "Пиши обычными предложениями, но без точки в конце.",
    "senior": "Пиши полными предложениями, можно с многоточиями.",
}


def casual(text, age):
    """Убирает лишнюю пунктуацию, как в живой переписке. Чем моложе персонаж, тем меньше знаков."""
    if age in ("middle", "senior"):
        return text if age == "senior" else text.rstrip(".")  # старшие пишут «правильно»
    keep_question = random.random() < (0.3 if age == "young" else 0.6)
    # Новое предложение после точки/вопроса продолжаем с маленькой буквы: «код? Там» -> «код? там»
    text = re.sub(r"([.?!…])\s+([А-ЯЁA-Z])", lambda m: m.group(1) + " " + m.group(2).lower(), text)
    text = text.replace("«", "").replace("»", "").replace('"', "")  # ёлочки в мессенджере никто не набирает
    text = re.sub(r"\s*[—–-]\s+", " ", text)                    # тире
    text = re.sub(r"[;:]", "", text)
    text = re.sub(r",\s*", " ", text) if age == "young" or random.random() < 0.8 else text  # запятые
    text = re.sub(r"(?<!\.)\.(?!\.)\s*", " ", text)           # одиночные точки (многоточия не трогаем)
    if not keep_question:
        text = text.replace("?", "")
    text = text.replace("!", "")
    text = re.sub(r"\s+", " ", text).strip()
    if text[:1].isupper() and (age == "young" or random.random() < 0.8):
        text = text[0].lower() + text[1:]
    return text


# Канцелярские фразы, которыми ИИ любит начинать ответ, а живые люди так не пишут
FILLERS = r"^(конечно|спасибо за информацию)[,!.]?\s+"
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


GREETINGS = {"young": "здравствуйте", "adult": "Добрый день", "middle": "Добрый день", "senior": "Здравствуйте"}


def add_greeting(reply, history, age):
    """Если это первое сообщение владельца в переписке — вежливо здороваемся."""
    already_wrote = any(line.startswith("Владелец:") for line in history)
    if already_wrote or re.match(r"(здравствуй|добр|привет)", reply, re.IGNORECASE):
        return reply
    greeting = GREETINGS[age]
    if age in ("middle", "senior"):
        return f"{greeting}. {reply[0].upper() + reply[1:]}"
    return f"{greeting}, {reply[0].lower() + reply[1:]}"


def not_me_reply(history, profile):
    """Если собеседник назвал владельца по имени — отвечаем, что это не он (разговор не обрываем). Иначе None."""
    last = next((line for line in reversed(history) if not line.startswith("Владелец:")), "")
    name = find_called_name(last.split(":", 1)[-1])
    if not name:
        return None
    used = "\n".join(history)
    options = [p.format(name=name) for p in NOT_ME]
    reply = random.choice([p for p in options if p not in used] or options)
    return add_greeting(casual(reply, profile["age"]), history, profile["age"])


def says_own_name(reply, profile):
    """Проговорился ли бот, назвав имя своего персонажа (например, «Это Серёга»)."""
    first_name = persona(profile).split(",")[0].split()[0]  # «Николай Петрович» -> «Николай»
    return first_name.lower()[:4] in reply.lower()            # ловим и «Серёга», и «Серёжа»


def bait_reply(history, profile=None, stage=0):
    """Просит ИИ придумать следующий ответ мошеннику в роли владельца. Нет ИИ или ошибка — берём заготовку.
    stage — сколько раз бот уже ответил в этой переписке (от этого зависит тактика)."""
    profile = profile or DEFAULT_PROFILE
    denial = not_me_reply(history, profile)
    if denial:
        return denial
    target = next_target(history)
    if provider() is None:
        return add_greeting(fallback_reply(history, profile, stage, target), history, profile["age"])

    # 3 заготовки по теме последнего сообщения мошенника — как пример, что примерно ответить
    last = next((line for line in reversed(history) if not line.startswith("Владелец:")), "")
    topic = pick_topic(last)
    examples = "\n".join(f"- {p}" for p in random.sample(topic, k=min(3, len(topic))))

    # Последние 8 реплик, подписанные коротко и понятно для модели
    dialog = [line.replace("Владелец:", "Ты:").replace("Собеседник:", "Он:").replace("Мошенник:", "Он:")
              for line in history[-8:]]
    system = SYSTEM_PROMPT.format(persona=persona(profile), examples=examples, stage=stage_hint(stage), target=target_hint(target),
                                  punctuation=PUNCTUATION_RULE[profile["age"]],
                                  style="\n".join(f"- {p}" for p in style_examples(profile)))
    try:
        reply = ask_llm(USER_PROMPT.format(history="\n".join(dialog)), max_tokens=40,
                        system=system, temperature=0.9)
        reply = humanize(reply, lowercase=profile["age"] in ("young", "adult"))
        reply = casual(reply, profile["age"]) if reply else fallback_reply(history, profile, stage, target)
        if any(reply.lower() == line[len("Владелец: "):].lower() for line in history if line.startswith("Владелец:")):
            reply = fallback_reply(history, profile, stage, target)  # ИИ повторился — берём новую заготовку
        if says_own_name(reply, profile):
            reply = fallback_reply(history, profile, stage, target)  # ИИ назвал имя персонажа — не отправляем
        return add_greeting(reply, history, profile["age"])
    except Exception as e:
        print(f"[ИИ] ошибка приманки: {e}")
        return add_greeting(fallback_reply(history, profile, stage, target), history, profile["age"])


HOOK_REQUEST = """Собеседник замолчал и давно не отвечает. Напиши ему сам короткое вежливое сообщение,
чтобы он вернулся в разговор: например, что ты почти всё сделал и спрашиваешь, что дальше. 2–12 слов."""


def hook_reply(history, profile=None):
    """Сообщение, чтобы вернуть замолчавшего мошенника («Вы тут? Я почти всё сделал»)."""
    profile = profile or DEFAULT_PROFILE
    used = "\n".join(history)
    fallback = random.choice([h for h in HOOKS if h not in used] or HOOKS)
    if profile["gender"] == "f":
        fallback = feminize(fallback)
    fallback = casual(fallback, profile["age"])
    if provider() is None:
        return fallback
    dialog = [line.replace("Владелец:", "Ты:").replace("Собеседник:", "Он:") for line in history[-8:]]
    system = SYSTEM_PROMPT.format(persona=persona(profile), examples="\n".join(f"- {h}" for h in HOOKS[:3]),
                                  stage=stage_hint(7), target=target_hint(next_target(history)), punctuation=PUNCTUATION_RULE[profile["age"]],
                                  style="\n".join(f"- {p}" for p in style_examples(profile)))
    try:
        reply = ask_llm("Переписка:\n" + "\n".join(dialog) + "\n\n" + HOOK_REQUEST, max_tokens=40,
                        system=system, temperature=0.9)
        reply = humanize(reply, lowercase=profile["age"] in ("young", "adult"))
        return casual(reply, profile["age"]) if reply else fallback
    except Exception as e:
        print(f"[ИИ] ошибка крючка: {e}")
        return fallback


def extract_trophies(text):
    """Находит в сообщении мошенника карты, телефоны и ссылки. Возвращает список (тип, значение)."""
    found = []
    for kind, pattern in TROPHY_PATTERNS.items():
        flags = 0 if kind in ("организация", "имя") else re.IGNORECASE  # там важны заглавные буквы
        for match in re.findall(pattern, text, flags=flags):
            found.append((kind, match.strip(' «»"')))
    return found


# Что пытаемся выпросить у мошенника — по порядку
WANTED = ["организация", "имя", "телефон", "карта"]
TARGET_HINT = {
    "организация": "из какой он организации и отдела (банк, служба, полиция) — например, «чтобы записать»",
    "имя": "его имя и фамилию или должность — например, «если связь прервётся, кого спросить»",
    "телефон": "его номер телефона — «перезвоню, связь плохая» или «можно перевести по номеру?»",
    "карта": "номер карты или счёта для перевода — «напишите цифрами, перепишу», «прошлую не принимает»",
    "ссылка": "ссылку — «старая не открывается, пришлите ещё раз»",
}


def next_target(history):
    """Какие данные мошенник ещё не выдал — их и будем выпрашивать (или None, если всё есть)."""
    scam_lines = [line for line in history if not line.startswith("Владелец:")]
    got = {kind for line in scam_lines for kind, _ in extract_trophies(line)}
    wanted = WANTED + (["ссылка"] if re.search(r"ссылк|сайт|перейд|http", " ".join(scam_lines), re.I) else [])
    return next((kind for kind in wanted if kind not in got), None)


def target_hint(target):
    """Подсказка ИИ, что сейчас выпросить."""
    if target is None:
        return "Все детали уже известны. Вежливо поддерживай разговор и попроси повторить реквизиты."
    return "Вежливо и естественно уточни у собеседника " + TARGET_HINT[target] + "."


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
