# 🛡 ИИ-антифрод для Telegram

Перешли боту подозрительное сообщение, ссылку или скриншот — он покажет уровень риска 🔴🟡🟢,
объяснит, на чём основана оценка, и подскажет, что делать. В групповом чате бот молчит,
пока не увидит скам.

Ловит: «проголосуй за племянницу», «код из СМС», «служба безопасности банка», взломанного
«знакомого», который просит денег, фейковые Telegram Premium и розыгрыши, поддельные домены
(telegrarn, 0zon, sberbank-bonus), «лёгкий заработок», .apk-файлы.

## Запуск (macOS)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # вписать токен от @BotFather и ключ Claude
python test_local.py        # проверка без Telegram (добавь --ai, чтобы подключить Claude)
python app.py               # запуск бота
```

## Как устроено
`checks/links.py` (поддельные домены) + `checks/scam_phrases.py` (приёмы мошенников)
+ `checks/sender.py` (кто переслал: новый аккаунт? скрытый?) + `checks/ai_analysis.py` (Claude, текст и фото)
→ `scoring.py` взвешивает сигналы → `app.py` отвечает отчётом с советами.
