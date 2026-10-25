"""Анализ сообщения (и картинки) с помощью ИИ (GigaChat или Claude)."""
import json

from checks.llm import ask_llm, provider


PROMPT = """Ты — специалист по финансовой безопасности и защите пользователей мессенджеров.
Твоя задача — помочь человеку понять, безопасно ли сообщение, которое он получил.
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
    """Отправляет сообщение (и историю диалога) в ИИ и возвращает (сигнал, список советов).
    Если ИИ не подключён — возвращает (None, [])."""
    if provider() is None:
        return None, []

    signals_text = "\n".join(f"- {s['name']}: {s['verdict']} — {s['explanation']}" for s in other_signals) or "- нет"
    history_text = "\n".join(history) if history else "(нет)"
    prompt = PROMPT.format(text=text or "(только картинка)", signals=signals_text, history=history_text)

    try:
        raw = ask_llm(prompt, image_b64)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])  # вырезаем JSON из ответа
    except Exception as e:
        print(f"[ИИ] ошибка: {e}")
        return None, []  # ИИ сломался — не показываем это пользователю, работаем на правилах

    scheme = f" Схема: {data['scheme']}." if data.get("scheme") else ""
    signal = {
        "name": "🤖 ИИ-анализ",
        "verdict": data.get("verdict", "unclear"),
        "confidence": float(data.get("confidence", 0.5)),
        "explanation": data.get("explanation", "") + scheme,
    }
    return signal, data.get("advice", [])
