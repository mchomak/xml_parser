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
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from parser import ExchangeRate, parse_amount, get_top_rates, is_buying_crypto
from config import (
    TOP_COUNT, build_exchange_url, CRYPTO_CURRENCIES, FIAT_CURRENCIES,
    MAX_RETRIES, RETRY_DELAY, PAGE_TIMEOUT, ELEMENT_TIMEOUT, CALCULATOR_WAIT
)

logger = logging.getLogger(__name__)

# Directory for debug screenshots
DEBUG_SCREENSHOTS_DIR = Path(os.getenv('DEBUG_SCREENSHOTS_DIR', '/tmp/parser_screenshots'))
SAVE_DEBUG_SCREENSHOTS = os.getenv('SAVE_DEBUG_SCREENSHOTS', 'true').lower() == 'true'


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

    # Multiple selectors for table elements (fallback list)
    TABLE_SELECTORS = [
        "[class*='Table_body__el__']",
        "[class*='Table_body__amount']",
        "[class*='exchanger']",
        "[class*='Exchanger']",
        "div[class*='body__el']",
        ".exchange-table",
        "[class*='rates']",
    ]

    # Multiple selectors for calculator inputs (fallback list)
    CALCULATOR_INPUT_SELECTORS = [
        "#{input_id}",  # #fromInput or #toInput
        "input[id='{input_id}']",
        "input[name='{input_id}']",
        "[class*='calculator'] input",
        "[class*='Calculator'] input",
        "[class*='exchange'] input[type='number']",
        "[class*='Exchange'] input[type='text']",
        "input[type='number']",
    ]

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
        self._current_direction = ""  # For screenshot naming

        # Create screenshots directory if enabled
        if SAVE_DEBUG_SCREENSHOTS:
            DEBUG_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

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

    def _save_debug_screenshot(self, name: str) -> Optional[str]:
        """Save debug screenshot for troubleshooting."""
        if not SAVE_DEBUG_SCREENSHOTS or not self.driver:
            return None

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{self._current_direction}_{name}.png"
            filepath = DEBUG_SCREENSHOTS_DIR / filename
            self.driver.save_screenshot(str(filepath))
            logger.debug(f"Debug screenshot saved: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.debug(f"Failed to save screenshot: {e}")
            return None

    def _check_cloudflare_challenge(self) -> bool:
        """Check if page shows Cloudflare challenge or captcha."""
        if not self.driver:
            return False

        try:
            page_source = self.driver.page_source.lower()
            title = self.driver.title.lower()

            cloudflare_indicators = [
                'checking your browser',
                'just a moment',
                'cloudflare',
                'ddos protection',
                'please wait',
                'ray id',
                'cf-browser-verification',
                'challenge-running',
            ]

            for indicator in cloudflare_indicators:
                if indicator in page_source or indicator in title:
                    logger.warning(f"Cloudflare challenge detected: '{indicator}'")
                    return True

            return False
        except Exception as e:
            logger.debug(f"Error checking Cloudflare: {e}")
            return False

    def _wait_for_cloudflare(self, timeout: int = 30) -> bool:
        """Wait for Cloudflare challenge to complete."""
        logger.info("Waiting for Cloudflare challenge to complete...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if not self._check_cloudflare_challenge():
                logger.info("Cloudflare challenge passed")
                return True
            time.sleep(2)

        logger.warning(f"Cloudflare challenge not resolved within {timeout}s")
        self._save_debug_screenshot("cloudflare_timeout")
        return False

    def _find_element_with_fallback(self, selectors: list, timeout: float = 5.0) -> Optional[any]:
        """
        Try to find element using multiple selectors.
        Uses shorter timeout per selector but tries all of them.
        """
        for selector in selectors:
            try:
                wait = WebDriverWait(self.driver, timeout)
                element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                logger.debug(f"Found element with selector: {selector}")
                return element
            except TimeoutException:
                continue
            except Exception as e:
                logger.debug(f"Error with selector {selector}: {e}")
                continue

        return None

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
        other_input_id = "fromInput" if buying else "toInput"
        expensive_currency = to_currency if buying else from_currency

        logger.debug(f"Setting 1 {expensive_currency} in #{input_id}")

        input_elem = None

        # Try multiple selector strategies with shorter timeouts
        per_selector_timeout = min(3.0, ELEMENT_TIMEOUT / 5)

        # Strategy 1: Direct ID lookup
        try:
            wait = WebDriverWait(self.driver, per_selector_timeout)
            input_elem = wait.until(EC.presence_of_element_located((By.ID, input_id)))
            logger.debug(f"Found input by ID: #{input_id}")
        except TimeoutException:
            pass

        # Strategy 2: Try CSS selector with ID
        if not input_elem:
            try:
                wait = WebDriverWait(self.driver, per_selector_timeout)
                input_elem = wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, f"input#{input_id}")
                ))
                logger.debug(f"Found input by CSS: input#{input_id}")
            except TimeoutException:
                pass

        # Strategy 3: Find all inputs and identify by position/context
        if not input_elem:
            try:
                all_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input[type='number'], input:not([type])")
                logger.debug(f"Found {len(all_inputs)} input elements")

                # Log all inputs for debugging
                for idx, inp in enumerate(all_inputs[:10]):
                    inp_id = inp.get_attribute('id') or 'no-id'
                    inp_class = inp.get_attribute('class') or 'no-class'
                    inp_value = inp.get_attribute('value') or ''
                    logger.debug(f"  Input {idx}: id={inp_id}, class={inp_class[:50]}, value={inp_value}")

                    # Check if this is our target input
                    if input_id.lower() in inp_id.lower():
                        input_elem = inp
                        logger.debug(f"Found target input by ID match: {inp_id}")
                        break

                # If still not found, try finding by placeholder or surrounding text
                if not input_elem and all_inputs:
                    # Usually the calculator has 2 main inputs - give and receive
                    # For buying crypto: first input is "give" (fiat), second is "receive" (crypto)
                    # For selling crypto: first input is "give" (crypto), second is "receive" (fiat)
                    if len(all_inputs) >= 2:
                        # fromInput is typically the first calculator input
                        # toInput is typically the second calculator input
                        if input_id == "fromInput":
                            input_elem = all_inputs[0]
                        else:
                            input_elem = all_inputs[1]
                        logger.debug(f"Using input by position: {'first' if input_id == 'fromInput' else 'second'}")

            except Exception as e:
                logger.debug(f"Error finding inputs: {e}")

        if not input_elem:
            logger.warning(f"Calculator input #{input_id} not found with any strategy")
            self._save_debug_screenshot(f"input_not_found_{input_id}")
            return False

        # Try to set the value
        try:
            # Scroll to element first
            self.driver.execute_script("arguments[0].scrollIntoView(true);", input_elem)
            time.sleep(0.3)

            # Clear and set value using multiple methods
            try:
                # Method 1: Click, select all, type
                input_elem.click()
                time.sleep(0.2)
                input_elem.send_keys(Keys.CONTROL + "a")
                input_elem.send_keys("1")
            except Exception:
                try:
                    # Method 2: Use JavaScript to set value
                    self.driver.execute_script("arguments[0].value = '1';", input_elem)
                    # Trigger input event to update the page
                    self.driver.execute_script("""
                        var event = new Event('input', { bubbles: true });
                        arguments[0].dispatchEvent(event);
                    """, input_elem)
                except Exception as e:
                    logger.warning(f"Failed to set input value: {e}")
                    return False

            # Wait for table to update
            time.sleep(CALCULATOR_WAIT)

            # Try to get calculated value from other input
            try:
                other_input = self.driver.find_element(By.ID, other_input_id)
                calculated_value = other_input.get_attribute("value")
                logger.debug(f"Calculator: 1 {expensive_currency} = {calculated_value}")
            except Exception:
                logger.debug("Could not read calculated value (non-critical)")

            return True

        except StaleElementReferenceException:
            logger.warning("Input element became stale, page may have reloaded")
            return False
        except Exception as e:
            logger.warning(f"Error setting calculator input: {e}")
            return False

    def _load_page(self, url: str) -> bool:
        """Load page with retry logic. Returns True if successful."""
        try:
            logger.debug(f"Loading URL: {url}")
            self.driver.get(url)

            # Wait a moment for initial JS execution
            time.sleep(2)

            # Check for Cloudflare challenge
            if self._check_cloudflare_challenge():
                if not self._wait_for_cloudflare(timeout=45):
                    logger.error("Failed to pass Cloudflare challenge")
                    return False

            # Wait for page to stabilize after potential redirects
            time.sleep(1)

            # Try to find table elements using shorter individual timeouts
            # This prevents waiting full ELEMENT_TIMEOUT for each selector
            per_selector_timeout = min(5.0, ELEMENT_TIMEOUT / len(self.TABLE_SELECTORS))

            for selector in self.TABLE_SELECTORS:
                try:
                    wait = WebDriverWait(self.driver, per_selector_timeout)
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    logger.debug(f"Table loaded (selector: {selector})")
                    return True
                except TimeoutException:
                    continue
                except StaleElementReferenceException:
                    # Page might be still loading, wait a bit and retry
                    time.sleep(1)
                    continue

            # Save screenshot for debugging if table not found
            self._save_debug_screenshot("table_not_found")

            # Log page info for debugging
            try:
                page_title = self.driver.title
                current_url = self.driver.current_url
                logger.warning(f"Table not found. Title: '{page_title}', URL: {current_url}")

                # Check if we're on an error page
                page_source = self.driver.page_source.lower()
                if '404' in page_source or 'not found' in page_source:
                    logger.error("Page shows 404 error")
                    return False
                if 'error' in page_source[:500] or 'ошибка' in page_source[:500]:
                    logger.warning("Page may contain error message")
            except Exception as e:
                logger.debug(f"Error getting page info: {e}")

            logger.warning("Table not found, will try to parse page as-is")
            return True

        except TimeoutException:
            self._save_debug_screenshot("page_timeout")
            raise TimeoutException(f"Page load timeout: {url}")
        except WebDriverException as e:
            self._save_debug_screenshot("webdriver_error")
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
        # Store current direction for screenshot naming
        self._current_direction = f"{from_currency}_to_{to_currency}"

        # Check if we need to restart browser to prevent memory leaks
        self._check_restart_needed()
        self._init_driver()

        url = build_exchange_url(from_currency, to_currency)
        logger.info(f"Fetching: {from_currency} -> {to_currency}")
        logger.debug(f"URL: {url}")

        # Try to load page with retries
        page_loaded = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if self._load_page(url):
                    page_loaded = True
                    break
                else:
                    # _load_page returned False (e.g., Cloudflare challenge failed)
                    if attempt < MAX_RETRIES:
                        logger.warning(f"Page load returned False, retrying...")
                        self._restart_browser()
                        time.sleep(RETRY_DELAY)
                    continue
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
                    self._save_debug_screenshot("page_load_failed")
                    return []

        if not page_loaded:
            logger.error(f"Page load unsuccessful after {MAX_RETRIES} attempts: {url}")
            return []

        # Set calculator input (with retry)
        # Try a shorter retry loop with page refresh on failure
        calculator_success = False
        for attempt in range(1, MAX_RETRIES + 1):
            if self._set_calculator_input(from_currency, to_currency):
                calculator_success = True
                break
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning(f"Calculator input attempt {attempt}/{MAX_RETRIES} failed. Retrying in {delay}s...")

                # Try refreshing the page on calculator failure
                try:
                    logger.debug("Refreshing page to retry calculator...")
                    self.driver.refresh()
                    time.sleep(3)  # Wait for page to reload
                except Exception as e:
                    logger.debug(f"Page refresh failed: {e}")

                time.sleep(delay)

        if not calculator_success:
            logger.error(f"Failed to set calculator input after {MAX_RETRIES} attempts")
            # Don't return empty - try to parse without calculator
            logger.warning("Attempting to parse page without calculator adjustment...")

        # Click sorting header to get best rates first (optional retry)
        self._click_sort_header(from_currency, to_currency)
        time.sleep(0.5)

        # Collect rates from page
        html = self.driver.page_source
        rates = self._parse_page(html, from_currency, to_currency)

        if not rates:
            logger.error(f"No exchangers found for {from_currency} -> {to_currency}")
            self._save_debug_screenshot("no_exchangers_found")
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

        # Try multiple selectors for exchanger rows
        row_patterns = [
            r'Table_body__el__',
            r'exchanger',
            r'Exchanger',
            r'body__el',
            r'rate-row',
            r'exchange-row',
        ]

        exchanger_rows = []
        for pattern in row_patterns:
            exchanger_rows = soup.find_all('div', class_=re.compile(pattern))
            if exchanger_rows:
                logger.debug(f"Found {len(exchanger_rows)} rows with pattern '{pattern}'")
                break

        if not exchanger_rows:
            # Last resort: try finding any div with specific child structure
            logger.warning("No exchanger rows found with standard patterns, trying fallback...")
            # Look for divs that contain both name and amount elements
            all_divs = soup.find_all('div')
            for div in all_divs:
                if div.find('p') and len(div.find_all('div')) >= 2:
                    # Could be an exchanger row
                    exchanger_rows.append(div)
            logger.debug(f"Fallback found {len(exchanger_rows)} potential rows")

        logger.debug(f"Total exchanger rows to process: {len(exchanger_rows)}")

        for idx, row in enumerate(exchanger_rows):
            try:
                # Get exchanger name using multiple patterns
                name = None
                name_patterns = [
                    r'Table_body__el__name',
                    r'exchanger.*name',
                    r'name',
                ]

                for pattern in name_patterns:
                    name_elem = row.find('p', class_=re.compile(pattern, re.IGNORECASE))
                    if name_elem:
                        name = name_elem.get_text(strip=True)
                        break

                # Fallback: try first <p> or <span> with text
                if not name:
                    for tag in ['p', 'span', 'a']:
                        elem = row.find(tag)
                        if elem and elem.get_text(strip=True):
                            name = elem.get_text(strip=True)
                            break

                if not name:
                    name = row.get('id', '')

                if not name or len(name) < 2:
                    continue

                # Find amount elements using multiple patterns
                amount_patterns = [
                    r'Table_body__amount',
                    r'amount',
                    r'value',
                    r'price',
                ]

                amount_elems = []
                for pattern in amount_patterns:
                    amount_elems = row.find_all('div', class_=re.compile(pattern, re.IGNORECASE))
                    if len(amount_elems) >= 2:
                        break

                if len(amount_elems) < 2:
                    # Fallback: look for elements containing numeric text
                    potential_amounts = []
                    for elem in row.find_all(['p', 'span', 'div']):
                        text = elem.get_text(strip=True)
                        if text and parse_amount(text) is not None:
                            potential_amounts.append(elem)
                    if len(potential_amounts) >= 2:
                        amount_elems = [potential_amounts[0].parent, potential_amounts[1].parent]
                    else:
                        continue

                give_p = amount_elems[0].find('p') or amount_elems[0]
                receive_p = amount_elems[1].find('p') or amount_elems[1]

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

                # Parse limits using multiple patterns
                min_amount = None
                max_amount = None

                limit_patterns = [
                    r'Table_body__change__el',
                    r'limit',
                    r'range',
                    r'min.*max',
                ]

                limit_elems = []
                for pattern in limit_patterns:
                    limit_elems = row.find_all('div', class_=re.compile(pattern, re.IGNORECASE))
                    if limit_elems:
                        break

                for limit_elem in limit_elems:
                    label = limit_elem.find('p') or limit_elem.find('span')
                    value = limit_elem.find('span') if label and label.name == 'p' else limit_elem.find('p')

                    if label and value:
                        label_text = label.get_text(strip=True).lower()
                        value_amount = parse_amount(value.get_text())

                        if any(kw in label_text for kw in ['от', 'ot', 'from', 'min', 'мин']):
                            min_amount = value_amount
                        elif any(kw in label_text for kw in ['до', 'do', 'to', 'max', 'макс']):
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