"""Проверка отправителя пересланного сообщения: кто это вообще?"""


def check_sender(origin):
    """Принимает словарь об отправителе (см. app.py) и возвращает сигнал или None.

    origin = {"type": "user" | "hidden_user" | "channel" | "chat", "id": число, "name": строка}
    """
    if not origin:
        return None

    if origin["type"] == "hidden_user":
        return {
            "name": "👤 Отправитель",
            "verdict": "unclear",
            "confidence": 0.3,
            "explanation": f"«{origin['name']}» скрыл свой аккаунт при пересылке — проверить, кто это, нельзя",
        }

    if origin["type"] == "user":
        # Грубая эвристика: в Telegram ID выдаются по возрастанию,
        # поэтому очень большой ID обычно у недавно созданного аккаунта.
        if origin["id"] > 7_000_000_000:
            return {
                "name": "👤 Отправитель",
                "verdict": "suspicious",
                "confidence": 0.4,
                "explanation": f"Аккаунт «{origin['name']}», судя по номеру, создан недавно — у мошенников часто новые аккаунты",
            }
        return {
            "name": "👤 Отправитель",
            "verdict": "safe",
            "confidence": 0.2,
            "explanation": f"Аккаунт «{origin['name']}» не новый, но взломать могли и старый",
        }

    # канал или чат
    return {
        "name": "📢 Источник",
        "verdict": "unclear",
        "confidence": 0.2,
        "explanation": f"Переслано из «{origin['name']}». Проверь, что это официальный канал, а не копия с похожим названием",
    }
