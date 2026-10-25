"""Проверка ссылок: ищем поддельные домены, похожие на Telegram, Госуслуги, банки и маркетплейсы."""
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

# Настоящие домены игровых сервисов
# Настоящие домены сервисов, под которые чаще всего подделываются в Telegram
OFFICIAL_DOMAINS = [
    "telegram.org", "t.me", "telegram.me",
    "gosuslugi.ru", "nalog.gov.ru", "mos.ru",
    "sberbank.ru", "tbank.ru", "tinkoff.ru", "vtb.ru", "alfabank.ru",
    "ozon.ru", "wildberries.ru", "avito.ru", "yandex.ru", "vk.com",
    "steamcommunity.com", "steampowered.com",
]

# Короткие ссылки скрывают настоящий адрес
SHORTENERS = ["bit.ly", "tinyurl.com", "clck.ru", "cutt.ly", "goo.su", "t.ly"]

# Доменные зоны, которые часто покупают мошенники (дешёвые)
CHEAP_ZONES = [".xyz", ".top", ".gift", ".click", ".shop", ".icu", ".fun", ".site"]

# Слова-приманки в названии домена
BAIT_WORDS = ["telegram", "telegrm", "premium", "gosuslug", "sber", "tinkoff", "tbank", "vtb",
             "ozon", "wildberr", "avito", "vote", "golos", "prize", "bonus", "gift", "steam"]

# Ловим и полные ссылки, и «голые» домены вроде steamcommunlty.com/id/123
URL_RE = re.compile(r"(https?://[^\s<>]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>]*)?)", re.IGNORECASE)


def find_urls(text):
    """Находит все ссылки в тексте."""
    return URL_RE.findall(text)


def get_domain(url):
    """Достаёт домен из ссылки: 'https://a.b.com/x' -> 'a.b.com'."""
    if not url.lower().startswith("http"):
        url = "https://" + url
    domain = urlparse(url).netloc.lower()
    return domain.removeprefix("www.").split(":")[0]


def is_official(domain):
    """True, если домен — официальный или его поддомен."""
    return any(domain == d or domain.endswith("." + d) for d in OFFICIAL_DOMAINS)


def most_similar_official(domain):
    """Ищет официальный домен, на который больше всего похож данный. Возвращает (домен, похожесть 0..1)."""
    best, best_score = None, 0.0
    for official in OFFICIAL_DOMAINS:
        score = SequenceMatcher(None, domain, official).ratio()
        if score > best_score:
            best, best_score = official, score
    return best, best_score


# Буквы, которые мошенники подменяют похожими: rn выглядит как m, 0 как o и т.д.
LOOKALIKES = {"rn": "m", "vv": "w", "0": "o", "1": "l", "3": "e", "@": "a"}


def find_brand_twin(domain):
    """Ищет в частях домена подделку под бренд: 'telegrarn-vote.top' -> 'telegram'. Возвращает бренд или None."""
    brands = {d.split(".")[0] for d in OFFICIAL_DOMAINS if len(d.split(".")[0]) >= 4}
    for part in domain.replace("-", ".").split(".")[:-1]:  # последняя часть — зона (.ru, .top)
        normalized = part
        for fake, real in LOOKALIKES.items():
            normalized = normalized.replace(fake, real)
        for brand in brands:
            if part != brand and (normalized == brand or SequenceMatcher(None, part, brand).ratio() >= 0.8):
                return brand
    return None


def check_one_domain(domain):
    """Проверяет один домен и возвращает список найденных проблем (строки)."""
    if is_official(domain):
        return []

    problems = []
    twin, similarity = most_similar_official(domain)
    brand = find_brand_twin(domain)
    if similarity >= 0.75:
        problems.append(f"«{domain}» почти как «{twin}», но это НЕ он — подмена букв")
    elif brand:
        problems.append(f"«{domain}» маскируется под «{brand}» — буквы подменены похожими")
        similarity = 1.0  # чтобы ниже не дублировать «слово-приманку»
    if domain.startswith("xn--") or ".xn--" in domain:
        problems.append(f"«{domain}» использует похожие буквы из других алфавитов")
    if domain in SHORTENERS:
        problems.append(f"«{domain}» — короткая ссылка, настоящий адрес скрыт")
    if any(domain.endswith(zone) for zone in CHEAP_ZONES):
        problems.append(f"«{domain}» в дешёвой доменной зоне, такие часто берут мошенники")
    bait = [w for w in BAIT_WORDS if w in domain]
    if bait and similarity < 0.75:
        problems.append(f"в «{domain}» есть слово-приманка «{bait[0]}», но это не официальный сайт")
    return problems


def check_links(text):
    """Главная функция модуля: проверяет все ссылки в тексте и возвращает сигнал."""
    urls = find_urls(text)
    if not urls:
        return None  # ссылок нет — сигнала нет

    all_problems = []
    for url in urls:
        all_problems += check_one_domain(get_domain(url))

    if not all_problems:
        return {
            "name": "🔗 Ссылки",
            "verdict": "safe",
            "confidence": 0.6,
            "explanation": "Ссылки ведут на официальные или обычные сайты",
        }

    has_twin = any("подмен" in p or "других алфавитов" in p for p in all_problems)
    return {
        "name": "🔗 Ссылки",
        "verdict": "scam" if has_twin else "suspicious",
        "confidence": 0.9 if has_twin else 0.6,
        "explanation": "; ".join(all_problems),
    }
