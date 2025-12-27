"""
Selenium parser for cryptocurrency exchange rates from exnode.ru
Used when the site renders content via JavaScript
"""

import os
import re
import logging
import time
import functools
from datetime import datetime
from typing import Optional, Callable, Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.keys import Keys

from parser import ExchangeRate, parse_amount, get_top_rates, is_buying_crypto
from config import (
    TOP_COUNT, build_exchange_url, CRYPTO_CURRENCIES, FIAT_CURRENCIES,
    MAX_RETRIES, RETRY_DELAY, PAGE_TIMEOUT, ELEMENT_TIMEOUT, CALCULATOR_WAIT
)

logger = logging.getLogger(__name__)

# File for saving parsed URLs
URLS_LOG_FILE = "parsed_urls.txt"


def retry_on_failure(max_retries: int = None, delay: float = None):
    """
    Decorator for retrying operations on failure.

    Args:
        max_retries: Maximum number of retry attempts (default from config)
        delay: Base delay between retries in seconds (doubles on each retry)
    """
    if max_retries is None:
        max_retries = MAX_RETRIES
    if delay is None:
        delay = RETRY_DELAY

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            current_delay = delay

            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            f"{func.__name__}: Attempt {attempt}/{max_retries} failed: {e}. "
                            f"Retrying in {current_delay}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= 2  # Exponential backoff
                    else:
                        logger.error(
                            f"{func.__name__}: All {max_retries} attempts failed. Last error: {e}"
                        )

            raise last_exception

        return wrapper
    return decorator


def save_url_to_log(url: str, from_currency: str, to_currency: str):
    """Save parsed URL to log file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {from_currency} -> {to_currency}: {url}\n"

    with open(URLS_LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_line)

    logger.debug(f"URL logged to {URLS_LOG_FILE}")


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

    @retry_on_failure()
    def _init_driver(self):
        """Initialize WebDriver with retry on failure"""
        if self.driver is not None:
            return

        logger.info("Initializing Selenium WebDriver...")
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")
            logger.debug("Running in HEADLESS mode")
        else:
            logger.debug("Running in VISIBLE mode")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)

        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(PAGE_TIMEOUT)
        logger.info("WebDriver initialized")

    def close(self):
        """Close WebDriver"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            logger.debug("WebDriver closed")

    def __enter__(self):
        self._init_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _click_sort_header(self, from_currency: str, to_currency: str) -> bool:
        """
        Click on the appropriate sorting header based on operation type.
        Returns True if successful, False otherwise.
        """
        buying = is_buying_crypto(from_currency, to_currency)
        header_text = "Отдаете" if buying else "Получаете"
        sort_type = "ascending" if buying else "descending"

        logger.debug(f"{'BUYING' if buying else 'SELLING'} crypto, sorting by {header_text} ({sort_type})")

        try:
            header_xpath = f"//div[contains(@class, 'Table_header__el')]//p[text()='{header_text}']"
            wait = WebDriverWait(self.driver, ELEMENT_TIMEOUT)
            header_elem = wait.until(EC.element_to_be_clickable((By.XPATH, header_xpath)))

            header_elem.click()
            time.sleep(1)

            if buying:
                header_elem.click()
                time.sleep(1)

            logger.debug(f"Sorting applied: {header_text}")
            return True

        except TimeoutException:
            logger.warning(f"Sort header '{header_text}' not found")
            return False
        except Exception as e:
            logger.warning(f"Error clicking sort header: {e}")
            return False

    def _set_calculator_input(self, from_currency: str, to_currency: str) -> bool:
        """
        Set calculator input field to 1 in the "expensive" currency field.
        Returns True if successful, False otherwise.
        """
        buying = is_buying_crypto(from_currency, to_currency)
        input_id = "toInput" if buying else "fromInput"
        expensive_currency = to_currency if buying else from_currency

        logger.debug(f"Setting 1 {expensive_currency} in #{input_id}")

        try:
            wait = WebDriverWait(self.driver, ELEMENT_TIMEOUT)
            input_elem = wait.until(EC.presence_of_element_located((By.ID, input_id)))

            input_elem.click()
            time.sleep(0.3)
            input_elem.send_keys(Keys.CONTROL + "a")
            time.sleep(0.1)
            input_elem.send_keys(Keys.DELETE)
            time.sleep(0.1)
            input_elem.send_keys("1")

            time.sleep(CALCULATOR_WAIT)

            other_input_id = "fromInput" if input_id == "toInput" else "toInput"
            other_input = self.driver.find_element(By.ID, other_input_id)
            calculated_value = other_input.get_attribute("value")

            logger.debug(f"Calculator: 1 {expensive_currency} = {calculated_value}")
            return True

        except TimeoutException:
            logger.warning(f"Calculator input #{input_id} not found")
            return False
        except Exception as e:
            logger.warning(f"Error setting calculator input: {e}")
            return False

    def _load_page(self, url: str) -> bool:
        """Load page with retry logic. Returns True if successful."""
        try:
            self.driver.get(url)

            wait = WebDriverWait(self.driver, ELEMENT_TIMEOUT)
            selectors = [
                "[class*='Table_body__el__']",
                "[class*='Table_body__amount']",
                ".exchanger-row",
            ]

            for selector in selectors:
                try:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    logger.debug(f"Table loaded (selector: {selector})")
                    return True
                except TimeoutException:
                    continue

            logger.warning("Table not found, will try to parse page as-is")
            return True

        except TimeoutException:
            raise TimeoutException(f"Page load timeout: {url}")
        except WebDriverException as e:
            raise WebDriverException(f"WebDriver error: {e}")

    def fetch_exchange_rates(self, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """
        Fetch exchange rates using Selenium with retry logic.

        Args:
            from_currency: Source currency code (e.g., "SBERRUB")
            to_currency: Target currency code (e.g., "BTC")

        Returns:
            List of top exchange rates, or empty list on failure
        """
        self._init_driver()

        url = build_exchange_url(from_currency, to_currency)
        logger.info(f"Fetching: {from_currency} -> {to_currency}")
        logger.debug(f"URL: {url}")

        save_url_to_log(url, from_currency, to_currency)

        # Try to load page with retries
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._load_page(url)
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAY * (2 ** (attempt - 1))
                    logger.warning(f"Page load attempt {attempt}/{MAX_RETRIES} failed: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"Failed to load page after {MAX_RETRIES} attempts: {url}")
                    return []

        # Set calculator input (with retry)
        calculator_success = False
        for attempt in range(1, MAX_RETRIES + 1):
            if self._set_calculator_input(from_currency, to_currency):
                calculator_success = True
                break
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning(f"Calculator input attempt {attempt}/{MAX_RETRIES} failed. Retrying in {delay}s...")
                time.sleep(delay)

        if not calculator_success:
            logger.error(f"Failed to set calculator input after {MAX_RETRIES} attempts")
            return []

        time.sleep(1)

        # Collect prices BEFORE sorting
        logger.debug("Collecting prices before sorting...")
        html_before = self.driver.page_source
        rates_before = self._parse_page(html_before, from_currency, to_currency)

        # Click sorting header (with retry)
        for attempt in range(1, MAX_RETRIES + 1):
            if self._click_sort_header(from_currency, to_currency):
                break
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning(f"Sort header click attempt {attempt}/{MAX_RETRIES} failed. Retrying in {delay}s...")
                time.sleep(delay)

        time.sleep(2)

        # Collect prices AFTER sorting
        logger.debug("Collecting prices after sorting...")
        html_after = self.driver.page_source
        rates_after = self._parse_page(html_after, from_currency, to_currency)

        # Merge rates from before and after
        all_rates_dict = {}
        for r in rates_before + rates_after:
            if r.exchanger_name not in all_rates_dict:
                all_rates_dict[r.exchanger_name] = r

        rates = list(all_rates_dict.values())

        if not rates:
            logger.error(f"No exchangers found for {from_currency} -> {to_currency}")
            return []

        # Get top rates (sorted by price)
        buying = is_buying_crypto(from_currency, to_currency)
        top_rates = get_top_rates(rates, TOP_COUNT, buying)

        logger.info(f"Found {len(top_rates)} top exchangers for {from_currency} -> {to_currency}")
        for i, r in enumerate(top_rates, 1):
            logger.info(f"  {i}. {r.exchanger_name}: price={r.price:.2f} RUB")

        return top_rates

    def _parse_page(self, html: str, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """Parse HTML page after JavaScript rendering."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        rates = []

        exchanger_rows = soup.find_all('div', class_=re.compile(r'Table_body__el__'))
        logger.debug(f"Found {len(exchanger_rows)} exchanger rows")

        for idx, row in enumerate(exchanger_rows):
            try:
                # Get exchanger name
                name_elem = row.find('p', class_=re.compile(r'Table_body__el__name'))
                name = name_elem.get_text(strip=True) if name_elem else row.get('id', '')

                if not name:
                    continue

                # Find amount elements
                amount_elems = row.find_all('div', class_=re.compile(r'Table_body__amount'))
                if len(amount_elems) < 2:
                    continue

                give_p = amount_elems[0].find('p')
                receive_p = amount_elems[1].find('p')

                if not give_p or not receive_p:
                    continue

                give_amount = parse_amount(give_p.get_text())
                receive_amount = parse_amount(receive_p.get_text())

                if give_amount is None or receive_amount is None or give_amount == 0:
                    continue

                # Calculate price
                buying = is_buying_crypto(from_currency, to_currency)
                if buying:
                    price = give_amount / receive_amount if receive_amount != 0 else 0
                else:
                    price = receive_amount / give_amount if give_amount != 0 else 0

                logger.debug(f"Parsed: {name} | price={price:.2f} RUB")

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

            except Exception as e:
                logger.debug(f"Row {idx}: Parse error - {e}")
                continue

        logger.debug(f"Parsed {len(rates)} exchangers")
        return rates


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    from config import EXCHANGE_DIRECTIONS

    with SeleniumParser(headless=False) as parser:
        for from_curr, to_curr in EXCHANGE_DIRECTIONS[:1]:
            rates = parser.fetch_exchange_rates(from_curr, to_curr)
            print(f"\nRESULTS for {from_curr} -> {to_curr}:")
            for r in rates:
                print(f"  {r.exchanger_name}: price={r.price:.2f} RUB")