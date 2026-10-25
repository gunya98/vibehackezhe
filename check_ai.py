"""Проверка подключения ИИ: python check_ai.py"""
from dotenv import load_dotenv

load_dotenv()

from checks.llm import ask_llm, provider  # noqa: E402

name = provider()
if name is None:
    print("❌ ИИ не подключён: в .env нет GIGACHAT_CREDENTIALS или ANTHROPIC_API_KEY")
else:
    print(f"Подключён: {name}. Отправляю тестовый вопрос...")
    try:
        print("✅ Ответ ИИ:", ask_llm("Ответь одним словом: какой город — столица России?", max_tokens=20))
    except Exception as e:
        print(f"❌ Ошибка: {e}")
