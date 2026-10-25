"""Общие функции оформления: их используют и обычный бот (app.py), и Business-режим (business.py)."""
import base64
import html


def format_report(report):
    """Собирает текст ответа из отчёта (HTML-разметка Telegram)."""
    lines = [f"<b>{report['emoji']} {report['title']}</b>",
             f"Уверенность проверки: {int(report['certainty'] * 100)}%", ""]
    for s in report["signals"]:
        lines.append(f"<b>{s['name']}</b>\n{html.escape(s['explanation'])}\n")
    lines.append("<b>✅ Что делать</b>")
    lines += [f"• {html.escape(a)}" for a in report["advice"]]
    lines.append("\n<i>Это подсказка, а не приговор: 100% гарантии не даёт никто.</i>")
    return "\n".join(lines)


async def photo_to_b64(bot, message):
    """Скачивает фото из сообщения и превращает его в base64-строку для Claude (или None, если фото нет)."""
    if not message.photo:
        return None
    file = await bot.download(message.photo[-1])  # [-1] — самое большое разрешение
    return base64.b64encode(file.read()).decode()
