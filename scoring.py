"""Собирает сигналы всех проверок в итоговую оценку и совет."""
from checks.ai_analysis import analyze_with_ai
from checks.links import check_links
from checks.scam_phrases import check_phrases
from checks.sender import check_sender

# Сколько «опасности» даёт каждый вердикт
DANGER = {"scam": 1.0, "suspicious": 0.6, "unclear": 0.4, "safe": 0.0}

DEFAULT_ADVICE = {
    "red": ["Не переходи по ссылкам и никому не называй коды из СМС",
            "Если пишет «знакомый» — позвони ему по телефону, а не в Telegram",
            "Пожалуйся на сообщение: ⋮ → Пожаловаться"],
    "yellow": ["Не спеши: настоящие предложения не сгорают за 5 минут",
               "Открой сайт сам, набрав адрес вручную, а не по ссылке",
               "Спроси отправителя о том, что знаете только вы двое"],
    "green": ["Явных признаков мошенничества нет, но коды из СМС всё равно никому не сообщай"],
}

LEVELS = {
    "red": ("🔴", "Похоже на мошенничество"),
    "yellow": ("🟡", "Неясно — будь осторожен"),
    "green": ("🟢", "Явных признаков мошенничества нет"),
}


def analyze(text, image_b64=None, origin=None, use_ai=True, history=None):
    """Главная функция: прогоняет все проверки и возвращает итоговый отчёт (словарь).

    history — список строк с предыдущими сообщениями диалога (для ИИ), может быть None.
    """
    # простые проверки смотрят на новое сообщение и на весь диалог (ссылка могла быть раньше)
    full_text = "\n".join((history or []) + [text])
    signals = [s for s in (check_links(full_text), check_phrases(full_text), check_sender(origin)) if s]

    advice = []
    if use_ai:
        ai_signal, advice = analyze_with_ai(text, signals, image_b64, history)
        if ai_signal:
            signals.append(ai_signal)

    if not signals:
        level, danger, certainty = "green", 0.0, 0.3
    else:
        # Средняя опасность, взвешенная по уверенности каждой проверки
        weights = [max(s["confidence"], 0.1) for s in signals]
        danger = sum(DANGER.get(s["verdict"], 0.4) * w for s, w in zip(signals, weights)) / sum(weights)
        certainty = sum(weights) / len(weights)
        if any(s["verdict"] == "scam" and s["confidence"] >= 0.8 for s in signals):
            danger = max(danger, 0.75)  # одна железная улика (поддельный домен, код из СМС) решает
        level = "red" if danger >= 0.65 else "yellow" if danger >= 0.3 else "green"

    emoji, title = LEVELS[level]
    return {
        "level": level,
        "emoji": emoji,
        "title": title,
        "danger": round(danger, 2),
        "certainty": round(certainty, 2),
        "signals": signals,
        "advice": advice or DEFAULT_ADVICE[level],
    }


def has_anything_to_check(text):
    """Быстрый фильтр для групп: стоит ли вообще реагировать (без вызова ИИ)."""
    return bool(check_links(text)) or bool(check_phrases(text))
