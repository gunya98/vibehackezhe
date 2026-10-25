"""Анализ сообщения (и картинки) с помощью Claude API."""
import json
import os

from anthropic import Anthropic

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")

PROMPT = """Ты — эксперт по мошенничеству в Telegram и мессенджерах в России.
Тебе переслали подозрительное сообщение (и, возможно, картинку). Также даны сигналы простых проверок.

Оцени, похоже ли это на мошенничество. Не будь категоричен: если данных мало — честно скажи «unclear».
Учитывай схемы: «проголосуй за племянницу», «код из СМС», фейковый Telegram Premium и розыгрыши,
взломанный аккаунт знакомого просит денег, «служба безопасности банка» и «безопасный счёт»,
фейковые Госуслуги, лёгкий заработок и инвестиции, предоплата за аренду или товар, .apk-файлы.

Предыдущая переписка (может быть пустой). Мошенничество часто — сценарий из нескольких шагов:
сначала знакомство и доверие, потом просьба о коде, деньгах или переходе по ссылке.
{history}

Новое сообщение:
\"\"\"{text}\"\"\"

Сигналы простых проверок:
{signals}

Ответь ТОЛЬКО JSON без пояснений вокруг:
{{"verdict": "scam" | "suspicious" | "safe" | "unclear",
  "confidence": число от 0 до 1,
  "scheme": "название схемы коротко или пусто",
  "explanation": "1-2 предложения простым языком, почему",
  "advice": ["шаг 1", "шаг 2", "шаг 3"]}}
Советы — конкретные действия для обычного человека (что НЕ делать, как проверить, например позвонить знакомому)."""


def analyze_with_ai(text, other_signals, image_b64=None, history=None):
    """Отправляет сообщение (и историю диалога) в Claude и возвращает (сигнал, список советов).
    Без ключа возвращает (None, [])."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None, []

    signals_text = "\n".join(f"- {s['name']}: {s['verdict']} — {s['explanation']}" for s in other_signals) or "- нет"
    history_text = "\n".join(history) if history else "(нет)"
    prompt = PROMPT.format(text=text or "(только картинка)", signals=signals_text, history=history_text)
    content = [{"type": "text", "text": prompt}]
    if image_b64:
        content.insert(0, {"type": "image",
                           "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}})

    try:
        client = Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])  # вырезаем JSON из ответа
    except Exception as e:
        return {"name": "🤖 ИИ-анализ", "verdict": "unclear", "confidence": 0.0,
                "explanation": f"ИИ недоступен: {e}"}, []

    scheme = f" Схема: {data['scheme']}." if data.get("scheme") else ""
    signal = {
        "name": "🤖 ИИ-анализ",
        "verdict": data.get("verdict", "unclear"),
        "confidence": float(data.get("confidence", 0.5)),
        "explanation": data.get("explanation", "") + scheme,
    }
    return signal, data.get("advice", [])
