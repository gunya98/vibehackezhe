"""Проверка без Telegram: python test_local.py  (или с --ai, чтобы подключить Claude)."""
import sys

from dotenv import load_dotenv

load_dotenv()

from scoring import analyze  # noqa: E402

EXAMPLES = [
    ("Привет! Проголосуй, пожалуйста, за мою племянницу в конкурсе, очень надо: telegrarn-vote.top/kid23", None),
    ("Здравствуйте, это служба безопасности Сбербанка. На вас оформляют кредит, продиктуйте код из СМС", None),
    ("Слушай, срочно нужны деньги, займи 15000, верну завтра, скинь на карту", {"type": "user", "id": 7_600_000_000, "name": "Мама"}),
    ("Завтра пара в 9:00 в аудитории Б-512, не опаздывайте", None),
    ("Ищем сотрудников, заработок от 5000 в день, без опыта", None),
]

use_ai = "--ai" in sys.argv
for text, origin in EXAMPLES:
    r = analyze(text, origin=origin, use_ai=use_ai)
    print(f"\n{text}\n{r['emoji']} {r['title']}  (опасность {r['danger']}, уверенность {r['certainty']})")
    for s in r["signals"]:
        print(f"   {s['name']}: {s['explanation']}")
    print("   Совет:", " | ".join(r["advice"]))
