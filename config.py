"""
Конфигурация парсера курсов криптовалют exnode.ru
"""

# Интервал обновления курсов в секундах
UPDATE_INTERVAL = 30

# Сколько топ-обменников брать для каждого направления
TOP_COUNT = 3

# Путь к выходному XML файлу
OUTPUT_XML_PATH = "rates.xml"

# Базовый URL сайта
BASE_URL = "https://exnode.ru/exchange"

# Заголовки для запросов (эмуляция браузера)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Маппинг криптовалют на URL-slugs exnode.ru
# Формат: "КОД_ВАЛЮТЫ": "url_slug"
CRYPTO_CURRENCIES = {
    "BTC": "bitcoin_btc-btc",
    # "ETH": "ethereum_eth-eth",
    # "USDTTRC20": "tether_trc20-usdttrc20",
    # "USDTERC20": "tether_erc20-usdterc20",
    # "LTC": "litecoin_ltc-ltc",
    # "TON": "toncoin-ton",
}

# Маппинг фиатных валют/платежных систем на URL-slugs
FIAT_CURRENCIES = {
    "SBERRUB": "sberbank_sber-sberrub",
    # "TCSBRUB": "tinkoff_tcs-tcsbrub",
    # "VTBRUB": "vtb-vtbrub",
    # "ACRUB": "alfa_bank-acrub",
    # "QWRUB": "qiwi-qwrub",
    # "YAMRUB": "yoomoney_yandex-yamrub",
    # "CASHRUB": "nalichnye_rub-cashrub",
}

# Направления обмена для парсинга
# Формат: (from_currency, to_currency)
# Будем парсить оба направления: крипта -> фиат и фиат -> крипта
EXCHANGE_DIRECTIONS = [
    # USDT TRC20 <-> RUB банки
    # ("USDTTRC20", "SBERRUB"),
    # ("SBERRUB", "USDTTRC20"),

    # BTC <-> RUB
    ("BTC", "SBERRUB"),
    # ("CASHRUB", "BTC"),

    # # ETH <-> RUB
    # ("ETH", "TCSBRUB"),

    # # USDT ERC20 <-> RUB
    # ("USDTERC20", "VTBRUB"),

    # # LTC <-> RUB
    # ("LTC", "ACRUB"),

    # # QIWI -> USDT
    # ("QWRUB", "USDTTRC20"),

    # # YooMoney -> BTC
    # ("YAMRUB", "BTC"),

    # # Наличные -> USDT
    # ("CASHRUB", "USDTTRC20"),

    # # TON <-> RUB
    # ("TON", "SBERRUB"),
]

# Значения по умолчанию для XML (можно настроить)
DEFAULT_VALUES = {
    "amount": 1000000,      # Резерв
    "minamount": 1000,      # Минимальная сумма
    "maxamount": 500000,    # Максимальная сумма
    "param": 0,             # Параметр
}


def get_currency_slug(currency_code: str) -> str:
    """Получить URL-slug для валюты"""
    if currency_code in CRYPTO_CURRENCIES:
        return CRYPTO_CURRENCIES[currency_code]
    elif currency_code in FIAT_CURRENCIES:
        return FIAT_CURRENCIES[currency_code]
    else:
        raise ValueError(f"Неизвестная валюта: {currency_code}")


def build_exchange_url(from_currency: str, to_currency: str) -> str:
    """
    Построить URL для страницы обмена.
    Формат: https://exnode.ru/exchange/{from_slug}-to-{to_slug}
    """
    from_slug = get_currency_slug(from_currency)
    to_slug = get_currency_slug(to_currency)
    return f"{BASE_URL}/{from_slug}-to-{to_slug}"
