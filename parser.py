from __future__ import annotations
"""
Parser for cryptocurrency exchange rates from exnode.ru
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import HEADERS, TOP_COUNT, build_exchange_url, CRYPTO_CURRENCIES, FIAT_CURRENCIES

logger = logging.getLogger(__name__)


def is_expensive_currency(currency: str) -> bool:
    """
    Check if currency is "expensive" (crypto/USDT).
    Expensive currencies: BTC, ETH, USDT, etc.
    Cheap currencies: RUB bank methods (SBERRUB, TCSBRUB, etc.)
    """
    return currency in CRYPTO_CURRENCIES


def is_buying_crypto(from_currency: str, to_currency: str) -> bool:
    """
    Determine if this is a crypto buying operation.
    Buying crypto: FIAT -> CRYPTO (e.g., SBERRUB -> BTC)
    Selling crypto: CRYPTO -> FIAT (e.g., BTC -> SBERRUB)
    """
    from_is_crypto = from_currency in CRYPTO_CURRENCIES
    to_is_crypto = to_currency in CRYPTO_CURRENCIES

    if not from_is_crypto and to_is_crypto:
        return True
    elif from_is_crypto and not to_is_crypto:
        return False
    else:
        return True


@dataclass
class ExchangeRate:
    """Exchange rate from an exchanger"""
    exchanger_name: str           # Exchanger name
    from_currency: str            # Source currency (what you give)
    to_currency: str              # Target currency (what you receive)
    give_amount: float            # Amount you give (from calculator)
    receive_amount: float         # Amount you receive (from calculator)
    price: float                  # Price in RUB for 1 unit of expensive asset
    min_amount: Optional[float] = None   # Minimum exchange amount (from table)
    max_amount: Optional[float] = None   # Maximum exchange amount (from table)

    @property
    def rate(self) -> float:
        """Rate: how much you receive for 1 unit of what you give"""
        if self.give_amount == 0:
            return 0
        return self.receive_amount / self.give_amount

    @property
    def inverse_rate(self) -> float:
        """Inverse rate: how much you give for 1 unit of what you receive"""
        if self.receive_amount == 0:
            return 0
        return self.give_amount / self.receive_amount


def parse_amount(text: str) -> Optional[float]:
    """
    Parse number from text, removing spaces and handling commas.
    Examples: "6 807 113.7810" -> 6807113.7810
              "1" -> 1.0
              "270 000 000" -> 270000000.0
              "0.00489600" -> 0.004896
    """
    if not text:
        return None

    # Remove all whitespace
    cleaned = text.replace(' ', '').replace('\xa0', '').replace('\u00a0', '')

    # Remove all except digits, dots, commas, and minus signs
    cleaned = re.sub(r'[^\d.,\-]', '', cleaned)

    if not cleaned:
        return None

    # Replace comma with dot (for European format)
    cleaned = cleaned.replace(',', '.')

    # If multiple dots - keep only the last one (thousands separator case)
    parts = cleaned.split('.')
    if len(parts) > 2:
        cleaned = ''.join(parts[:-1]) + '.' + parts[-1]

    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_page(url: str) -> Optional[str]:
    """Fetch page using requests"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Error loading page {url}: {e}")
        return None


def parse_exchangers_from_html(html: str, from_currency: str, to_currency: str) -> list[ExchangeRate]:
    """
    Parse list of exchangers from HTML page.
    """
    soup = BeautifulSoup(html, 'html.parser')
    rates = []

    exchanger_rows = soup.find_all('div', class_=re.compile(r'Table_body__el__'))

    if not exchanger_rows:
        exchanger_rows = soup.find_all('div', id=True)
        exchanger_rows = [row for row in exchanger_rows if row.find('p', class_=re.compile(r'Table_body__el__name'))]

    logger.info(f"Found {len(exchanger_rows)} exchangers for {from_currency} -> {to_currency}")

    for row in exchanger_rows:
        try:
            name_elem = row.find('p', class_=re.compile(r'Table_body__el__name'))
            if not name_elem:
                name = row.get('id', '')
                if not name:
                    continue
            else:
                name = name_elem.get_text(strip=True)

            if not name:
                continue

            amount_elems = row.find_all('div', class_=re.compile(r'Table_body__amount'))

            if len(amount_elems) < 2:
                continue

            give_text = amount_elems[0].find('p')
            receive_text = amount_elems[1].find('p')

            if not give_text or not receive_text:
                continue

            give_amount = parse_amount(give_text.get_text())
            receive_amount = parse_amount(receive_text.get_text())

            if give_amount is None or receive_amount is None or give_amount == 0:
                continue

            # Calculate price (RUB per 1 expensive asset)
            buying = is_buying_crypto(from_currency, to_currency)
            if buying:
                # Buying crypto: give RUB, receive crypto -> price = give_amount / receive_amount
                price = give_amount / receive_amount if receive_amount != 0 else 0
            else:
                # Selling crypto: give crypto, receive RUB -> price = receive_amount / give_amount
                price = receive_amount / give_amount if give_amount != 0 else 0

            # Parse limits from "от/до" column
            min_amount = None
            max_amount = None
            limit_elems = row.find_all('div', class_=re.compile(r'Table_body__change__el'))

            for limit_elem in limit_elems:
                label = limit_elem.find('p')
                value = limit_elem.find('span')

                if label and value:
                    label_text = label.get_text(strip=True).lower()
                    value_text = value.get_text(strip=True)
                    value_amount = parse_amount(value_text)

                    if label_text in ['от', 'ot', 'from', 'min']:
                        min_amount = value_amount
                    elif label_text in ['до', 'do', 'to', 'max']:
                        max_amount = value_amount

            exchange_rate = ExchangeRate(
                exchanger_name=name,
                from_currency=from_currency,
                to_currency=to_currency,
                give_amount=give_amount,
                receive_amount=receive_amount,
                price=price,
                min_amount=min_amount,
                max_amount=max_amount,
            )

            rates.append(exchange_rate)
            logger.debug(f"Exchanger {name}: give={give_amount}, receive={receive_amount}, price={price:.4f}")

        except Exception as e:
            logger.warning(f"Error parsing exchanger: {e}")
            continue

    return rates


def get_top_rates(rates: list[ExchangeRate], count: int = TOP_COUNT, buying: bool = True) -> list[ExchangeRate]:
    """
    Get top-N exchangers by best price.

    Args:
        rates: List of rates
        count: How many to return
        buying: True if buying crypto (want min price), False if selling (want max price)
    """
    if buying:
        # Buying crypto: want cheapest price (min)
        sorted_rates = sorted(rates, key=lambda r: r.price)
    else:
        # Selling crypto: want highest price (max)
        sorted_rates = sorted(rates, key=lambda r: r.price, reverse=True)

    return sorted_rates[:count]


def fetch_exchange_rates(from_currency: str, to_currency: str) -> list[ExchangeRate]:
    """
    Fetch exchange rates for direction from -> to.
    Returns top-N exchangers.
    """
    url = build_exchange_url(from_currency, to_currency)
    logger.info(f"Loading rates {from_currency} -> {to_currency}: {url}")

    html = fetch_page(url)

    if not html:
        logger.error(f"Failed to load page for {from_currency} -> {to_currency}")
        return []

    if 'Table_body__el__' not in html and 'Table_body__amount' not in html:
        logger.warning("Page does not contain table data. Site might use JavaScript rendering.")
        logger.info("Try using Selenium parser (parser_selenium.py)")
        return []

    rates = parse_exchangers_from_html(html, from_currency, to_currency)

    if not rates:
        logger.warning(f"No exchangers found for {from_currency} -> {to_currency}")
        return []

    buying = is_buying_crypto(from_currency, to_currency)
    top_rates = get_top_rates(rates, TOP_COUNT, buying)
    logger.info(f"Top-{len(top_rates)} exchangers for {from_currency} -> {to_currency}")

    return top_rates


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    from config import EXCHANGE_DIRECTIONS

    for from_curr, to_curr in EXCHANGE_DIRECTIONS[:2]:
        rates = fetch_exchange_rates(from_curr, to_curr)
        for rate in rates:
            print(f"{rate.exchanger_name}: give {rate.give_amount} {rate.from_currency}, receive {rate.receive_amount} {rate.to_currency}")
            print(f"  Price: {rate.price:.4f} RUB per 1 unit")
            print(f"  Min: {rate.min_amount}, Max: {rate.max_amount}")
        print()