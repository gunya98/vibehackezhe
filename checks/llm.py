"""Единая точка доступа к языковой модели (ИИ).

Какой ИИ используется, решает файл .env:
- есть GIGACHAT_CREDENTIALS → GigaChat от Сбера (бесплатный тариф для физлиц);
- иначе есть ANTHROPIC_API_KEY (начинается с sk-) → Claude;
- иначе ничего → функции возвращают None, и бот работает без ИИ (на правилах и заготовках).
"""
import os


def provider():
    """Возвращает название доступного ИИ: 'gigachat', 'claude' или None."""
    if os.getenv("GIGACHAT_CREDENTIALS", "").strip():
        return "gigachat"
    if os.getenv("ANTHROPIC_API_KEY", "").startswith("sk-"):
        return "claude"
    return None


def ask_llm(prompt, image_b64=None, max_tokens=600):
    """Отправляет текст (и, для Claude, картинку) в ИИ и возвращает ответ строкой. Нет ИИ — None.
    Ошибки сети/ключа не глушим: их обрабатывает тот, кто вызвал."""
    name = provider()

    if name == "gigachat":
        from gigachat import GigaChat
        # verify_ssl_certs=False: у Сбера сертификат Минцифры, которого нет в стандартном наборе Mac.
        # Для хакатона это допустимо; в настоящем продукте нужно установить сертификат.
        with GigaChat(credentials=os.getenv("GIGACHAT_CREDENTIALS").strip(),
                      scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
                      model=os.getenv("GIGACHAT_MODEL", "GigaChat-2"),
                      verify_ssl_certs=False) as giga:
            response = giga.chat({"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens})
        return response.choices[0].message.content

    if name == "claude":
        from anthropic import Anthropic
        content = [{"type": "text", "text": prompt}]
        if image_b64:
            content.insert(0, {"type": "image",
                               "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}})
        response = Anthropic().messages.create(model=os.getenv("CLAUDE_MODEL", "claude-haiku-5-5"),
                                               max_tokens=max_tokens,
                                               messages=[{"role": "user", "content": content}])
        return response.content[0].text

    return None
