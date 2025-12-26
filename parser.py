"""
Parser for cryptocurrency exchange rates from exnode.ru
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import HEADERS, TOP_COUNT, build_exchange_url

logger = logging.getLogger(__name__)


@dataclass
class ExchangeRate:
    """Exchange rate from an exchanger"""
    exchanger_name: str           # Exchanger name
    from_currency: str            # Source currency (what you give)
    to_currency: str              # Target currency (what you receive)
    give_amount: float            # Amount you give (e.g., 7053614 RUB)
    receive_amount: float         # Amount you receive (e.g., 1 BTC)
    min_amount: Optional[float] = None   # Minimum exchange amount
    max_amount: Optional[float] = None   # Maximum exchange amount

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
    """
    if not text:
        return None

    # Remove all except digits, dots, and minus signs
    cleaned = re.sub(r'[^\d.,\-]', '', text.replace(' ', ''))

    # Replace comma with dot (for European format)
    cleaned = cleaned.replace(',', '.')

    # If multiple dots - keep only the last one (thousands separator)
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

    HTML structure (based on provided data):
    - Exchanger container: div.Table_body__el__IK40q
    - Name: p.Table_body__el__name__9fI44
    - Amounts: div.Table_body__amount____C1r (first - give, second - receive)
    - Limits: div.Table_body__change__el__XwiOv
    """
    soup = BeautifulSoup(html, 'html.parser')
    rates = []

    # Find all exchanger rows
    exchanger_rows = soup.find_all('div', class_=re.compile(r'Table_body__el__'))

    if not exchanger_rows:
        # Alternative search by ID
        exchanger_rows = soup.find_all('div', id=True)
        exchanger_rows = [row for row in exchanger_rows if row.find('p', class_=re.compile(r'Table_body__el__name'))]

    logger.info(f"Found {len(exchanger_rows)} exchangers for {from_currency} -> {to_currency}")

    for row in exchanger_rows:
        try:
            # Exchanger name
            name_elem = row.find('p', class_=re.compile(r'Table_body__el__name'))
            if not name_elem:
                name = row.get('id', '')
                if not name:
                    continue
            else:
                name = name_elem.get_text(strip=True)

            if not name:
                continue

            # Exchange amounts
            amount_elems = row.find_all('div', class_=re.compile(r'Table_body__amount'))

            if len(amount_elems) < 2:
                logger.debug(f"Not enough amount elements for {name}")
                continue

            # First amount - what you give
            # Second amount - what you receive
            give_text = amount_elems[0].find('p')
            receive_text = amount_elems[1].find('p')

            if not give_text or not receive_text:
                continue

            give_amount = parse_amount(give_text.get_text())
            receive_amount = parse_amount(receive_text.get_text())

            if give_amount is None or receive_amount is None or give_amount == 0:
                continue

            # Exchange limits
            min_amount = None
            max_amount = None
            limit_elems = row.find_all('div', class_=re.compile(r'Table_body__change__el'))

            for limit_elem in limit_elems:
                label = limit_elem.find('p')
                value = limit_elem.find('span')

                if label and value:
                    label_text = label.get_text(strip=True).lower()
                    value_amount = parse_amount(value.get_text())

                    # Support both Russian and English labels
                    if 'ot' in label_text or 'from' in label_text or label_text == 'ot':
                        min_amount = value_amount
                    elif 'do' in label_text or 'to' in label_text or label_text == 'do':
                        max_amount = value_amount

            exchange_rate = ExchangeRate(
                exchanger_name=name,
                from_currency=from_currency,
                to_currency=to_currency,
                give_amount=give_amount,
                receive_amount=receive_amount,
                min_amount=min_amount,
                max_amount=max_amount,
            )

            rates.append(exchange_rate)
            logger.debug(f"Exchanger {name}: give={give_amount}, receive={receive_amount}, rate={exchange_rate.rate:.8f}")

        except Exception as e:
            logger.warning(f"Error parsing exchanger: {e}")
            continue

    return rates


def get_top_rates(rates: list[ExchangeRate], count: int = TOP_COUNT) -> list[ExchangeRate]:
    """
    Get top-N exchangers by best rate.
    Best rate = you receive more for your money (higher rate).
    """
    # Sort by descending rate (higher is better for customer)
    sorted_rates = sorted(rates, key=lambda r: r.rate, reverse=True)
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

    # Check if page contains table data
    if 'Table_body__el__' not in html and 'Table_body__amount' not in html:
        logger.warning("Page does not contain table data. Site might use JavaScript rendering.")
        logger.info("Try using Selenium parser (parser_selenium.py)")
        return []

    rates = parse_exchangers_from_html(html, from_currency, to_currency)

    if not rates:
        logger.warning(f"No exchangers found for {from_currency} -> {to_currency}")
        return []

    top_rates = get_top_rates(rates)
    logger.info(f"Top-{len(top_rates)} exchangers for {from_currency} -> {to_currency}")

    return top_rates


if __name__ == "__main__":
    # Test run
    logging.basicConfig(level=logging.DEBUG)

    from config import EXCHANGE_DIRECTIONS

    for from_curr, to_curr in EXCHANGE_DIRECTIONS[:2]:
        rates = fetch_exchange_rates(from_curr, to_curr)
        for rate in rates:
            print(f"{rate.exchanger_name}: give {rate.give_amount} {rate.from_currency}, receive {rate.receive_amount} {rate.to_currency}")
            print(f"  Rate: 1 {rate.from_currency} = {rate.rate:.8f} {rate.to_currency}")
            print(f"  Inverse: 1 {rate.to_currency} = {rate.inverse_rate:.2f} {rate.from_currency}")
        print()