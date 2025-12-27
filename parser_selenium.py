"""
Selenium parser for cryptocurrency exchange rates from exnode.ru
Used when the site renders content via JavaScript
"""

import os
import re
import logging
import time
from datetime import datetime
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from parser import ExchangeRate, parse_amount, get_top_rates
from config import TOP_COUNT, build_exchange_url, CRYPTO_CURRENCIES, FIAT_CURRENCIES

logger = logging.getLogger(__name__)

# Directory for saving HTML files
HTML_DUMP_DIR = "html_dumps"
# File for saving parsed URLs
URLS_LOG_FILE = "parsed_urls.txt"


def ensure_html_dump_dir():
    """Create HTML dump directory if not exists"""
    if not os.path.exists(HTML_DUMP_DIR):
        os.makedirs(HTML_DUMP_DIR)
        logger.info(f"Created HTML dump directory: {HTML_DUMP_DIR}")


def save_html_to_file(html: str, from_currency: str, to_currency: str) -> str:
    """Save HTML content to file for debugging"""
    ensure_html_dump_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{HTML_DUMP_DIR}/{from_currency}_to_{to_currency}_{timestamp}.html"

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html)

    logger.info(f"HTML saved to: {filename}")
    return filename


def save_url_to_log(url: str, from_currency: str, to_currency: str):
    """Save parsed URL to log file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {from_currency} -> {to_currency}: {url}\n"

    with open(URLS_LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_line)

    logger.info(f"URL saved to {URLS_LOG_FILE}")


def is_buying_crypto(from_currency: str, to_currency: str) -> bool:
    """
    Determine if this is a crypto buying operation.

    Buying crypto: FIAT -> CRYPTO (e.g., SBERRUB -> BTC)
    Selling crypto: CRYPTO -> FIAT (e.g., BTC -> SBERRUB)

    Returns True if buying crypto, False if selling.
    """
    from_is_crypto = from_currency in CRYPTO_CURRENCIES
    to_is_crypto = to_currency in CRYPTO_CURRENCIES

    # If from is fiat and to is crypto -> buying crypto
    if not from_is_crypto and to_is_crypto:
        return True
    # If from is crypto and to is fiat -> selling crypto
    elif from_is_crypto and not to_is_crypto:
        return False
    # Edge cases (crypto to crypto, fiat to fiat) - default to buying behavior
    else:
        return True


class SeleniumParser:
    """Parser using Selenium for JavaScript-rendered pages"""

    def __init__(self, headless: bool = False):
        """
        Initialize parser.

        Args:
            headless: If True, run browser in headless mode. Default False (show browser).
        """
        self.headless = headless
        self.driver: Optional[webdriver.Chrome] = None

    def _init_driver(self):
        """Initialize WebDriver"""
        if self.driver is not None:
            return

        logger.info("Initializing Selenium WebDriver...")
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")
            logger.info("Running in HEADLESS mode")
        else:
            logger.info("Running in VISIBLE mode (browser will be displayed)")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Disable automation flags
        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)

        try:
            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(30)
            logger.info("Selenium WebDriver initialized successfully")
        except WebDriverException as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise

    def close(self):
        """Close WebDriver"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            logger.info("Selenium WebDriver closed")

    def __enter__(self):
        self._init_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _click_sort_header(self, from_currency: str, to_currency: str):
        """
        Click on the appropriate sorting header based on operation type.

        When BUYING crypto (FIAT -> CRYPTO):
            - Click "Отдаете" to sort by price ascending (cheapest first)
        When SELLING crypto (CRYPTO -> FIAT):
            - Click "Получаете" to sort by price descending (most expensive first)
        """
        buying = is_buying_crypto(from_currency, to_currency)

        if buying:
            # Buying crypto - sort by "Отдаете" (what you give) ascending
            header_text = "Отдаете"
            sort_type = "ascending (cheapest first)"
        else:
            # Selling crypto - sort by "Получаете" (what you receive) descending
            header_text = "Получаете"
            sort_type = "descending (most expensive first)"

        logger.info(f"Operation: {'BUYING' if buying else 'SELLING'} crypto")
        logger.info(f"Clicking on '{header_text}' header for {sort_type} sorting...")

        try:
            # Find the header element with the text
            header_xpath = f"//div[contains(@class, 'Table_header__el')]//p[text()='{header_text}']"

            wait = WebDriverWait(self.driver, 10)
            header_elem = wait.until(EC.element_to_be_clickable((By.XPATH, header_xpath)))

            # Click the header to sort
            header_elem.click()
            logger.info(f"Clicked '{header_text}' header - first click")
            time.sleep(1)

            # For buying (ascending) - we might need to click twice to get ascending order
            # For selling (descending) - we might need to click twice to get descending order
            # Check if the arrow indicates the right direction and click again if needed

            if buying:
                # For buying, we want ascending (cheapest first)
                # Click again to ensure ascending order
                header_elem.click()
                logger.info(f"Clicked '{header_text}' header - second click for ascending")
                time.sleep(1)
            else:
                # For selling, first click usually gives descending (most expensive first)
                # No additional click needed usually
                pass

            logger.info(f"Sorting applied: {header_text} - {sort_type}")

        except TimeoutException:
            logger.warning(f"Could not find '{header_text}' header for sorting. Continuing without sorting.")
        except Exception as e:
            logger.warning(f"Error clicking sort header: {e}. Continuing without sorting.")

    def fetch_exchange_rates(self, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """
        Fetch exchange rates using Selenium.

        Args:
            from_currency: Source currency code (e.g., "SBERRUB")
            to_currency: Target currency code (e.g., "BTC")

        Returns:
            List of top exchange rates
        """
        self._init_driver()

        url = build_exchange_url(from_currency, to_currency)

        logger.info("=" * 70)
        logger.info(f"FETCHING: {from_currency} -> {to_currency}")
        logger.info(f"URL: {url}")
        logger.info("=" * 70)

        # Save URL to log file
        save_url_to_log(url, from_currency, to_currency)

        try:
            logger.info("Loading page...")
            self.driver.get(url)

            # Wait for table to load
            wait = WebDriverWait(self.driver, 15)

            time.sleep(3)

            # Try different selectors
            selectors = [
                "[class*='Table_body__el__']",
                "[class*='Table_body__amount']",
                ".exchanger-row",
                "[data-exchanger]",
            ]

            table_loaded = False
            for selector in selectors:
                try:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    table_loaded = True
                    logger.info(f"Table found with selector: {selector}")
                    break
                except TimeoutException:
                    logger.debug(f"Selector not found: {selector}")
                    continue

            if not table_loaded:
                logger.warning("Exchange table not found. Will try to parse page as-is.")

            # Click on sorting header
            self._click_sort_header(from_currency, to_currency)

            # Additional wait for page to update after sorting
            logger.info("Waiting 2 seconds for sorting to apply...")
            time.sleep(2)

            # Get rendered HTML
            html = self.driver.page_source
            logger.info(f"Page HTML length: {len(html)} characters")

            # Save HTML to file
            html_file = save_html_to_file(html, from_currency, to_currency)

            # Parse the page
            rates = self._parse_page(html, from_currency, to_currency)

            if not rates:
                logger.warning(f"No exchangers found for {from_currency} -> {to_currency}")
                logger.warning(f"Check saved HTML file: {html_file}")
                return []

            # Get top rates
            top_rates = get_top_rates(rates, TOP_COUNT)

            logger.info("-" * 50)
            logger.info(f"TOP {len(top_rates)} EXCHANGERS for {from_currency} -> {to_currency}:")
            for i, r in enumerate(top_rates, 1):
                logger.info(
                    f"  {i}. {r.exchanger_name}: "
                    f"give {r.give_amount:.4f} {from_currency}, "
                    f"receive {r.receive_amount:.4f} {to_currency} | "
                    f"rate={r.rate:.8f}"
                )
            logger.info("-" * 50)

            return top_rates

        except TimeoutException:
            logger.error(f"Page load timeout: {url}")
            return []
        except WebDriverException as e:
            logger.error(f"WebDriver error: {e}")
            return []

    def _parse_page(self, html: str, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """
        Parse HTML page after JavaScript rendering.

        Args:
            html: Page HTML content
            from_currency: Source currency
            to_currency: Target currency

        Returns:
            List of parsed exchange rates
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        rates = []

        # Find all exchanger rows
        exchanger_rows = soup.find_all('div', class_=re.compile(r'Table_body__el__'))

        logger.info(f"Found {len(exchanger_rows)} exchanger row elements")

        for idx, row in enumerate(exchanger_rows):
            try:
                # Get exchanger name
                name_elem = row.find('p', class_=re.compile(r'Table_body__el__name'))
                if not name_elem:
                    # Try getting name from ID attribute
                    name = row.get('id', '')
                else:
                    name = name_elem.get_text(strip=True)

                if not name:
                    logger.debug(f"Row {idx}: No name found, skipping")
                    continue

                logger.debug(f"Row {idx}: Exchanger name = '{name}'")

                # Find amount elements
                amount_elems = row.find_all('div', class_=re.compile(r'Table_body__amount'))

                logger.debug(f"Row {idx}: Found {len(amount_elems)} amount elements")

                if len(amount_elems) < 2:
                    logger.debug(f"Row {idx}: Not enough amount elements, skipping")
                    continue

                # Parse amounts from <p> elements
                give_p = amount_elems[0].find('p')
                receive_p = amount_elems[1].find('p')

                if not give_p or not receive_p:
                    logger.debug(f"Row {idx}: Missing <p> elements in amounts")
                    continue

                give_text = give_p.get_text()
                receive_text = receive_p.get_text()

                logger.debug(f"Row {idx}: give_text = '{give_text}'")
                logger.debug(f"Row {idx}: receive_text = '{receive_text}'")

                give_amount = parse_amount(give_text)
                receive_amount = parse_amount(receive_text)

                logger.debug(f"Row {idx}: give_amount = {give_amount}, receive_amount = {receive_amount}")

                if give_amount is None or receive_amount is None:
                    logger.debug(f"Row {idx}: Could not parse amounts")
                    continue

                if give_amount == 0:
                    logger.debug(f"Row {idx}: give_amount is zero, skipping")
                    continue

                # Calculate rate
                rate = receive_amount / give_amount

                # Log parsed values
                logger.info(
                    f"PARSED: {name} | "
                    f"give={give_amount:.4f} {from_currency} | "
                    f"receive={receive_amount:.4f} {to_currency} | "
                    f"rate={rate:.8f}"
                )

                # Parse limits
                min_amount = None
                max_amount = None

                limit_elems = row.find_all('div', class_=re.compile(r'Table_body__change__el'))
                for limit_elem in limit_elems:
                    label = limit_elem.find('p')
                    value = limit_elem.find('span')

                    if label and value:
                        label_text = label.get_text(strip=True).lower()
                        value_amount = parse_amount(value.get_text())

                        if 'from' in label_text or 'ot' in label_text or label_text == 'ot':
                            min_amount = value_amount
                        elif 'to' in label_text or 'do' in label_text or label_text == 'do':
                            max_amount = value_amount

                logger.debug(f"Row {idx}: min_amount={min_amount}, max_amount={max_amount}")

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

            except Exception as e:
                logger.warning(f"Row {idx}: Parse error - {e}")
                continue

        logger.info(f"Successfully parsed {len(rates)} exchangers")
        return rates


if __name__ == "__main__":
    # Test run
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    from config import EXCHANGE_DIRECTIONS

    # Run with visible browser for testing
    with SeleniumParser(headless=False) as parser:
        for from_curr, to_curr in EXCHANGE_DIRECTIONS[:1]:
            rates = parser.fetch_exchange_rates(from_curr, to_curr)
            print("\n" + "=" * 60)
            print(f"RESULTS for {from_curr} -> {to_curr}:")
            print("=" * 60)
            for r in rates:
                print(f"  {r.exchanger_name}:")
                print(f"    give {r.give_amount:.4f} {r.from_currency}, receive {r.receive_amount:.4f} {r.to_currency}")
                print(f"    rate = {r.rate:.8f}")
            print()