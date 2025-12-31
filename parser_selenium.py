"""
Selenium parser for cryptocurrency exchange rates from exnode.ru
Used when the site renders content via JavaScript
"""
from __future__ import annotations

import gc
import os
import re
import logging
import time
import functools
import subprocess
import signal
import platform
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


# Restart browser every N requests to prevent memory leaks
BROWSER_RESTART_INTERVAL = int(os.getenv('BROWSER_RESTART_INTERVAL', '10'))
logger.info(f"Browser will restart every {BROWSER_RESTART_INTERVAL} requests")


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


def kill_chrome_processes():
    """Kill all orphan Chrome and ChromeDriver processes."""
    try:
        if platform.system() == 'Windows':
            # Windows: use taskkill
            subprocess.run(
                ['taskkill', '/F', '/IM', 'chrome.exe', '/T'],
                capture_output=True,
                timeout=10
            )
            subprocess.run(
                ['taskkill', '/F', '/IM', 'chromedriver.exe', '/T'],
                capture_output=True,
                timeout=10
            )
        else:
            # Linux/Mac: use pkill
            subprocess.run(
                ['pkill', '-f', 'chrome'],
                capture_output=True,
                timeout=5
            )
            subprocess.run(
                ['pkill', '-f', 'chromedriver'],
                capture_output=True,
                timeout=5
            )
        logger.debug("Killed orphan Chrome processes")
    except Exception as e:
        logger.debug(f"Error killing Chrome processes: {e}")


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
        self._driver_pid: Optional[int] = None
        self._request_count = 0  # Counter for auto-restart

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

        # Required for Docker/container environments
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-infobars")
        options.add_argument("--remote-debugging-port=9222")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--single-process")

        # Memory management flags to prevent leaks
        options.add_argument("--disable-cache")
        options.add_argument("--disable-application-cache")
        options.add_argument("--disable-offline-load-stale-cache")
        options.add_argument("--disk-cache-size=0")
        options.add_argument("--media-cache-size=0")
        options.add_argument("--aggressive-cache-discard")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-sync")
        options.add_argument("--js-flags=--expose-gc")

        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)

        # Use "eager" page load strategy - don't wait for all resources
        options.page_load_strategy = 'eager'

        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(PAGE_TIMEOUT)
        self._request_count = 0  # Reset counter on new driver
        logger.info("WebDriver initialized")

    def close(self):
        """Close WebDriver and clean up all Chrome processes."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                logger.warning(f"Error closing driver: {e}")
            finally:
                self.driver = None

        # Force kill any remaining Chrome processes
        kill_chrome_processes()
        logger.debug("WebDriver closed and cleaned up")

    def __enter__(self):
        # Clean up any orphan processes before starting
        kill_chrome_processes()
        time.sleep(1)
        self._init_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        # Additional cleanup on error
        if exc_type is not None:
            logger.warning(f"Parser exiting with error: {exc_type.__name__}")
            kill_chrome_processes()

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
            input_elem.send_keys(Keys.CONTROL + "a")
            input_elem.send_keys("1")  # Replace selected text with "1"

            time.sleep(0.5)  # Brief wait for table to update

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

    def _restart_browser(self):
        """Force restart the browser to recover from errors."""
        logger.warning("Restarting browser to recover from errors...")
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None
        kill_chrome_processes()
        time.sleep(2)
        self._init_driver()
        logger.info("Browser restarted successfully")

    def _cleanup_memory(self):
        """Clean up browser memory to prevent leaks."""
        if not self.driver:
            return
        try:
            # Clear cookies and storage
            self.driver.delete_all_cookies()
            self.driver.execute_script("window.localStorage.clear();")
            self.driver.execute_script("window.sessionStorage.clear();")
            # Trigger JavaScript garbage collection if exposed
            self.driver.execute_script("if(window.gc) window.gc();")
            # Python garbage collection
            gc.collect()
            logger.debug("Memory cleanup completed")
        except Exception as e:
            logger.debug(f"Memory cleanup error (non-critical): {e}")

    def _check_restart_needed(self):
        """Check if browser needs restart due to too many requests."""
        self._request_count += 1
        if self._request_count >= BROWSER_RESTART_INTERVAL:
            logger.info(f"Browser restart scheduled after {self._request_count} requests")
            self._restart_browser()
            gc.collect()

    def fetch_exchange_rates(self, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """
        Fetch exchange rates using Selenium with retry logic.

        Args:
            from_currency: Source currency code (e.g., "SBERRUB")
            to_currency: Target currency code (e.g., "BTC")

        Returns:
            List of top exchange rates, or empty list on failure
        """
        # Check if we need to restart browser to prevent memory leaks
        self._check_restart_needed()
        self._init_driver()

        url = build_exchange_url(from_currency, to_currency)
        logger.info(f"Fetching: {from_currency} -> {to_currency}")
        logger.debug(f"URL: {url}")

        # Try to load page with retries
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._load_page(url)
                break
            except Exception as e:
                error_msg = str(e)
                # Check if this is a connection error that requires browser restart
                if 'ERR_CONNECTION' in error_msg or 'net::' in error_msg or 'disconnected' in error_msg.lower():
                    logger.warning(f"Connection error detected, restarting browser...")
                    self._restart_browser()

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

        # Click sorting header to get best rates first (optional retry)
        self._click_sort_header(from_currency, to_currency)
        time.sleep(0.5)

        # Collect rates from page
        html = self.driver.page_source
        rates = self._parse_page(html, from_currency, to_currency)

        if not rates:
            logger.error(f"No exchangers found for {from_currency} -> {to_currency}")
            return []

        # Get top rates (sorted by price)
        buying = is_buying_crypto(from_currency, to_currency)
        top_rates = get_top_rates(rates, TOP_COUNT, buying)

        logger.info(f"Found {len(top_rates)} top exchangers for {from_currency} -> {to_currency}")
        for i, r in enumerate(top_rates, 1):
            logger.info(f"  {i}. {r.exchanger_name}: price={r.price:.4f} RUB")

        # Cleanup after each request to prevent memory buildup
        self._cleanup_memory()

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

                # Calculate price (RUB per 1 unit of crypto)
                buying = is_buying_crypto(from_currency, to_currency)

                # Log raw values for debugging
                logger.debug(f"Raw: {name} | give={give_amount:.4f}, receive={receive_amount:.4f}")

                # The exnode.ru table shows RATE in receive/give columns, not total amount
                # For selling crypto: "Получаете" column shows RUB rate per 1 crypto
                # For buying crypto: "Отдаёте" column shows RUB rate per 1 crypto

                if buying:
                    # Buying crypto (FIAT -> CRYPTO): "Отдаёте" shows RUB per 1 crypto
                    # The give_amount IS the rate, use it directly
                    price = give_amount
                else:
                    # Selling crypto (CRYPTO -> FIAT): "Получаете" shows RUB per 1 crypto
                    # The receive_amount IS the rate, use it directly
                    price = receive_amount

                logger.debug(f"Parsed: {name} | price={price:.4f} RUB (buying={buying})")

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