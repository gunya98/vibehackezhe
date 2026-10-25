"""Проверка подключения ИИ: python check_ai.py"""
import os

from dotenv import load_dotenv

load_dotenv()

from checks.llm import ask_llm, provider  # noqa: E402

name = provider()
if name is None:
    print("❌ ИИ не подключён: в .env нет GIGACHAT_CREDENTIALS или ANTHROPIC_API_KEY")
    raise SystemExit

if name == "gigachat":
    # Показываем, какие модели доступны по вашему ключу — их имя можно вписать в GIGACHAT_MODEL
    from gigachat import GigaChat
    try:
        with GigaChat(credentials=os.getenv("GIGACHAT_CREDENTIALS").strip(),
                      scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"), verify_ssl_certs=False) as giga:
            print("Доступные модели:", ", ".join(m.id_ for m in giga.get_models().data))
    except Exception as e:
        print(f"Не удалось получить список моделей: {e}")
    print("Сейчас выбрана:", os.getenv("GIGACHAT_MODEL", "GigaChat-2"))

print(f"Подключён: {name}. Отправляю тестовый вопрос...")
try:
    print("✅ Ответ ИИ:", ask_llm("Ответь одним словом: какой город — столица России?", max_tokens=20))
except Exception as e:
    print(f"❌ Ошибка: {str(e)[:300]}")
