"""
Configuration for cryptocurrency exchange rate parser (exnode.ru)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)


def get_env_int(key: str, default: int) -> int:
    """Get integer value from environment"""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_float(key: str, default: float) -> float:
    """Get float value from environment"""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def get_env_bool(key: str, default: bool) -> bool:
    """Get boolean value from environment"""
    value = os.getenv(key)
    if value is None:
        return default
    return value.lower() in ('true', '1', 'yes', 'on')


ONCE = get_env_bool('ONCE', False)

SELENIUM = get_env_int('SELENIUM', True)

# Update interval in seconds
UPDATE_INTERVAL = get_env_int('UPDATE_INTERVAL', 30)

# Number of top exchangers per direction
TOP_COUNT = get_env_int('TOP_COUNT', 3)

# Output XML file path
OUTPUT_XML_PATH = os.getenv('OUTPUT_XML_PATH', 'rates.xml')

# Retry settings
MAX_RETRIES = get_env_int('MAX_RETRIES', 3)
RETRY_DELAY = get_env_float('RETRY_DELAY', 2.0)

# Browser settings
HEADLESS = get_env_bool('HEADLESS', False)

# Logging settings
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = os.getenv('LOG_FILE', 'parser.log')

# Timeouts
PAGE_TIMEOUT = get_env_int('PAGE_TIMEOUT', 30)
ELEMENT_TIMEOUT = get_env_int('ELEMENT_TIMEOUT', 15)
CALCULATOR_WAIT = get_env_float('CALCULATOR_WAIT', 1.5)

# Base URL
BASE_URL = "https://exnode.ru/exchange"

# Request headers (browser emulation)
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
    "ETH": "ethereum_eth-eth",
    "USDTTRC20": "tether_trc20_usdt-usdttrc",
    "USDTERC20": "tether_erc20_usdterc-usdterc",
    "LTC": "litecoin_ltc-ltc",
    "TON": "toncoin_ton-ton",
}

# Маппинг фиатных валют/платежных систем на URL-slugs
FIAT_CURRENCIES = {
    "SBERRUB": "sberbank_sber-sberrub",
    "TCSBRUB": "tinkoff-tcsbrub",
    "VTBRUB": "vtb-tbrub",
    "ACRUB": "alfa_bank-acrub",
    # "QWRUB": "qiwi-qwrub",
    "YAMRUB": "yumoney_yandeks_dengi-yamrub",
    "CASHRUB": "nalichnye_rub-cashrub",
}

# Направления обмена для парсинга
# Формат: (from_currency, to_currency)
# Будем парсить оба направления: крипта -> фиат и фиат -> крипта
EXCHANGE_DIRECTIONS = [
    # USDT TRC20 <-> RUB банки
    ("USDTTRC20", "SBERRUB"),
    ("SBERRUB", "USDTTRC20"),

    # BTC <-> RUB
    ("BTC", "SBERRUB"),
    # ("CASHRUB", "BTC"),

    # ETH <-> RUB
    ("ETH", "TCSBRUB"),

    # USDT ERC20 <-> RUB
    ("USDTERC20", "VTBRUB"),

    # LTC <-> RUB
    ("LTC", "ACRUB"),

    # QIWI -> USDT
    # ("QWRUB", "USDTTRC20"),

    # YooMoney -> BTC
    ("YAMRUB", "BTC"),

    # Наличные -> USDT
    ("CASHRUB", "USDTTRC20"),

    # TON <-> RUB
    ("TON", "SBERRUB"),
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