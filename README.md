# 🛡 ИИ-антифрод для Telegram

Бот подключается к личному аккаунту через **Telegram Business** и в фоне проверяет входящие
сообщения. Если пишет мошенник — присылает владельцу предупреждение 🔴🟡 с объяснением и советом.
По кнопке владельца может сам вести диалог с мошенником (режим приманки) и собирать его реквизиты.

## Запуск (macOS)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # вписать TELEGRAM_TOKEN и ANTHROPIC_API_KEY
python simulate_business.py   # проверка Business-режима без Telegram и Premium
python test_local.py          # проверка анализа текстов (--ai — с Claude)
python app.py                 # запуск бота
```

## Подключение Telegram Business
1. @BotFather → бот → Bot Settings → **Business Mode → Turn on**.
2. Аккаунт владельца с **Telegram Premium**: Настройки → Telegram для бизнеса → Чат-боты →
   `@имя_бота` → доступ ко **всем чатам** → разрешить **отвечать на сообщения**.
3. Бот пришлёт владельцу «🛡 Защита включена».

## Файлы
- `app.py` — запуск бота, личка и группы
- `business.py` — Telegram Business: проверка лички, предупреждения, приманка
- `ui.py` — оформление отчёта
- `scoring.py` — сигналы → 🔴/🟡/🟢 + советы
- `checks/` — проверки: `links.py`, `scam_phrases.py`, `sender.py`, `ai_analysis.py`, `bait.py`
- `simulate_business.py` — имитация Telegram для проверки без интернета
