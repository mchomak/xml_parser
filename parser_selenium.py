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
import threading
from datetime import datetime
from typing import Optional, Callable, Any
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.common.keys import Keys

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
BROWSER_RESTART_INTERVAL = int(os.getenv('BROWSER_RESTART_INTERVAL', '5'))

# Maximum time for entire fetch operation (seconds)
MAX_FETCH_TIME = int(os.getenv('MAX_FETCH_TIME', '60'))

# Shorter timeouts for faster failure
FAST_PAGE_TIMEOUT = min(PAGE_TIMEOUT, 20)
FAST_ELEMENT_TIMEOUT = min(ELEMENT_TIMEOUT, 8)


def kill_chrome_processes():
    """Kill all orphan Chrome and ChromeDriver processes."""
    try:
        if platform.system() == 'Windows':
            subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe', '/T'],
                           capture_output=True, timeout=5)
            subprocess.run(['taskkill', '/F', '/IM', 'chromedriver.exe', '/T'],
                           capture_output=True, timeout=5)
        else:
            subprocess.run(['pkill', '-9', '-f', 'chrome'], capture_output=True, timeout=3)
            subprocess.run(['pkill', '-9', '-f', 'chromedriver'], capture_output=True, timeout=3)
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
        "div[class*='body__el']",
    ]

    def __init__(self, headless: bool = True):
        """Initialize parser."""
        self.headless = headless
        self.driver: Optional[webdriver.Chrome] = None
        self._request_count = 0
        self._current_direction = ""
        self._consecutive_failures = 0

        if SAVE_DEBUG_SCREENSHOTS:
            DEBUG_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(f"Parser initialized: restart_interval={BROWSER_RESTART_INTERVAL}, "
                    f"max_fetch_time={MAX_FETCH_TIME}s")

    def _init_driver(self):
        """Initialize WebDriver."""
        if self.driver is not None:
            return

        logger.info("Initializing Selenium WebDriver...")
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")

        # Essential flags for stability
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-infobars")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")

        # Memory optimization
        options.add_argument("--disable-cache")
        options.add_argument("--disable-application-cache")
        options.add_argument("--disk-cache-size=0")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-translate")
        options.add_argument("--metrics-recording-only")
        options.add_argument("--mute-audio")
        options.add_argument("--no-first-run")
        options.add_argument("--safebrowsing-disable-auto-update")

        # Prevent hanging on slow resources
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")

        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)

        # Don't wait for full page load
        options.page_load_strategy = 'eager'

        try:
            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(FAST_PAGE_TIMEOUT)
            self.driver.set_script_timeout(FAST_PAGE_TIMEOUT)
            self._request_count = 0
            logger.info("WebDriver initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            kill_chrome_processes()
            raise

    def close(self):
        """Close WebDriver and clean up."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                logger.debug(f"Error closing driver: {e}")
            finally:
                self.driver = None
        kill_chrome_processes()

    def __enter__(self):
        kill_chrome_processes()
        time.sleep(0.5)
        self._init_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _save_debug_screenshot(self, name: str) -> Optional[str]:
        """Save debug screenshot."""
        if not SAVE_DEBUG_SCREENSHOTS or not self.driver:
            return None

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{self._current_direction}_{name}.png"
            filepath = DEBUG_SCREENSHOTS_DIR / filename
            self.driver.save_screenshot(str(filepath))
            logger.info(f"Screenshot saved: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.debug(f"Failed to save screenshot: {e}")
            return None

    def _restart_browser(self):
        """Force restart browser."""
        logger.warning("Restarting browser...")
        try:
            if self.driver:
                self.driver.quit()
        except:
            pass
        self.driver = None
        kill_chrome_processes()
        time.sleep(1)
        self._init_driver()

    def _is_page_blocked(self) -> bool:
        """Check if page shows Cloudflare or other blocking."""
        if not self.driver:
            return True

        try:
            page_source = self.driver.page_source.lower()
            title = self.driver.title.lower()

            blockers = [
                'checking your browser', 'just a moment', 'cloudflare',
                'ddos protection', 'please wait', 'ray id', 'access denied',
                'challenge-running', 'cf-browser-verification'
            ]

            for blocker in blockers:
                if blocker in page_source or blocker in title:
                    logger.warning(f"Page blocked: '{blocker}' detected")
                    return True

            # Check if page is mostly empty
            if len(page_source) < 1000:
                logger.warning("Page appears empty or blocked")
                return True

            return False
        except Exception as e:
            logger.debug(f"Error checking page: {e}")
            return True

    def _wait_for_content(self, timeout: float = 10) -> bool:
        """Wait for page content to load."""
        start = time.time()

        while time.time() - start < timeout:
            # First check if blocked
            if self._is_page_blocked():
                time.sleep(2)
                continue

            # Try to find any table element
            for selector in self.TABLE_SELECTORS:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        logger.debug(f"Content found with selector: {selector}")
                        return True
                except:
                    continue

            time.sleep(1)

        return False

    def _set_calculator_input(self, from_currency: str, to_currency: str) -> bool:
        """Set calculator input field."""
        buying = is_buying_crypto(from_currency, to_currency)
        input_id = "toInput" if buying else "fromInput"

        # Try direct ID first (fastest)
        try:
            input_elem = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.ID, input_id))
            )

            # Set value using JavaScript (most reliable)
            self.driver.execute_script("""
                arguments[0].value = '1';
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """, input_elem)

            time.sleep(CALCULATOR_WAIT)
            logger.debug(f"Calculator set: {input_id} = 1")
            return True

        except TimeoutException:
            pass
        except Exception as e:
            logger.debug(f"Direct input failed: {e}")

        # Fallback: find by position
        try:
            inputs = self.driver.find_elements(
                By.CSS_SELECTOR,
                "input[type='text'], input[type='number'], input:not([type='hidden'])"
            )

            # Filter visible inputs only
            visible_inputs = []
            for inp in inputs:
                try:
                    if inp.is_displayed() and inp.is_enabled():
                        visible_inputs.append(inp)
                except:
                    continue

            logger.debug(f"Found {len(visible_inputs)} visible inputs")

            if len(visible_inputs) >= 2:
                # fromInput = first, toInput = second
                target_idx = 0 if input_id == "fromInput" else 1
                target = visible_inputs[target_idx]

                self.driver.execute_script("""
                    arguments[0].value = '1';
                    arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                    arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                """, target)

                time.sleep(CALCULATOR_WAIT)
                logger.debug(f"Calculator set by position: index={target_idx}")
                return True

        except Exception as e:
            logger.debug(f"Position-based input failed: {e}")

        self._save_debug_screenshot(f"input_not_found_{input_id}")
        return False

    def _click_sort_header(self, from_currency: str, to_currency: str) -> bool:
        """Click sorting header."""
        buying = is_buying_crypto(from_currency, to_currency)
        header_text = "Отдаете" if buying else "Получаете"

        try:
            xpath = f"//div[contains(@class, 'Table_header')]//p[text()='{header_text}']"
            header = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            header.click()
            time.sleep(0.5)

            if buying:  # Need ascending order, click twice
                header.click()
                time.sleep(0.5)

            return True
        except:
            return False

    def fetch_exchange_rates(self, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """Fetch exchange rates with timeout protection."""
        self._current_direction = f"{from_currency}_to_{to_currency}"
        start_time = time.time()

        # Check if we need browser restart
        self._request_count += 1
        if self._request_count >= BROWSER_RESTART_INTERVAL:
            logger.info(f"Scheduled restart after {self._request_count} requests")
            self._restart_browser()

        # Also restart after consecutive failures
        if self._consecutive_failures >= 2:
            logger.warning(f"Restarting after {self._consecutive_failures} consecutive failures")
            self._restart_browser()
            self._consecutive_failures = 0

        self._init_driver()

        url = build_exchange_url(from_currency, to_currency)
        logger.info(f"Fetching: {from_currency} -> {to_currency}")

        try:
            # Load page with timeout
            logger.debug(f"Loading: {url}")
            self.driver.get(url)

            # Check time limit
            elapsed = time.time() - start_time
            if elapsed > MAX_FETCH_TIME:
                logger.error(f"Timeout: {elapsed:.1f}s exceeded limit")
                self._consecutive_failures += 1
                return []

            # Wait for content
            time.sleep(2)  # Initial JS execution

            if not self._wait_for_content(timeout=15):
                logger.warning("Content not found, saving screenshot")
                self._save_debug_screenshot("no_content")

                # Check if completely blocked
                if self._is_page_blocked():
                    logger.error("Page is blocked, restarting browser")
                    self._restart_browser()
                    self._consecutive_failures += 1
                    return []

            # Check time limit again
            elapsed = time.time() - start_time
            if elapsed > MAX_FETCH_TIME:
                logger.error(f"Timeout after content wait: {elapsed:.1f}s")
                self._consecutive_failures += 1
                return []

            # Try to set calculator (non-critical)
            self._set_calculator_input(from_currency, to_currency)

            # Try to sort (non-critical)
            self._click_sort_header(from_currency, to_currency)

            # Parse page
            html = self.driver.page_source
            rates = self._parse_page(html, from_currency, to_currency)

            if not rates:
                logger.warning(f"No rates found for {from_currency} -> {to_currency}")
                self._save_debug_screenshot("no_rates")
                self._consecutive_failures += 1
                return []

            # Success
            self._consecutive_failures = 0

            buying = is_buying_crypto(from_currency, to_currency)
            top_rates = get_top_rates(rates, TOP_COUNT, buying)

            elapsed = time.time() - start_time
            logger.info(f"Found {len(top_rates)} rates in {elapsed:.1f}s")

            for i, r in enumerate(top_rates, 1):
                logger.info(f"  {i}. {r.exchanger_name}: {r.price:.2f} RUB")

            # Cleanup
            try:
                self.driver.delete_all_cookies()
            except:
                pass
            gc.collect()

            return top_rates

        except TimeoutException as e:
            logger.error(f"Page timeout: {e}")
            self._save_debug_screenshot("timeout")
            self._consecutive_failures += 1
            self._restart_browser()
            return []

        except WebDriverException as e:
            logger.error(f"WebDriver error: {e}")
            self._save_debug_screenshot("webdriver_error")
            self._consecutive_failures += 1
            self._restart_browser()
            return []

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            self._consecutive_failures += 1
            return []

    def _parse_page(self, html: str, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """Parse HTML page."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        rates = []

        # Find exchanger rows
        exchanger_rows = []
        for pattern in [r'Table_body__el__', r'exchanger', r'body__el']:
            exchanger_rows = soup.find_all('div', class_=re.compile(pattern))
            if exchanger_rows:
                break

        logger.debug(f"Found {len(exchanger_rows)} exchanger rows")

        for row in exchanger_rows:
            try:
                # Get name
                name = None
                for pattern in [r'Table_body__el__name', r'name']:
                    elem = row.find('p', class_=re.compile(pattern, re.IGNORECASE))
                    if elem:
                        name = elem.get_text(strip=True)
                        break

                if not name:
                    elem = row.find('p')
                    if elem:
                        name = elem.get_text(strip=True)

                if not name or len(name) < 2:
                    continue

                # Get amounts
                amount_elems = row.find_all('div', class_=re.compile(r'Table_body__amount|amount', re.IGNORECASE))

                if len(amount_elems) < 2:
                    continue

                give_p = amount_elems[0].find('p') or amount_elems[0]
                receive_p = amount_elems[1].find('p') or amount_elems[1]

                give_amount = parse_amount(give_p.get_text())
                receive_amount = parse_amount(receive_p.get_text())

                if not give_amount or not receive_amount or give_amount == 0:
                    continue

                # Calculate price
                buying = is_buying_crypto(from_currency, to_currency)
                price = give_amount if buying else receive_amount

                # Parse limits (optional)
                min_amount = None
                max_amount = None

                for limit_elem in row.find_all('div', class_=re.compile(r'change__el|limit', re.IGNORECASE)):
                    label = limit_elem.find('p')
                    value = limit_elem.find('span')

                    if label and value:
                        label_text = label.get_text(strip=True).lower()
                        val = parse_amount(value.get_text())

                        if any(k in label_text for k in ['от', 'min', 'from']):
                            min_amount = val
                        elif any(k in label_text for k in ['до', 'max', 'to']):
                            max_amount = val

                rates.append(ExchangeRate(
                    exchanger_name=name,
                    from_currency=from_currency,
                    to_currency=to_currency,
                    give_amount=give_amount,
                    receive_amount=receive_amount,
                    price=price,
                    min_amount=min_amount,
                    max_amount=max_amount,
                ))

            except Exception as e:
                logger.debug(f"Parse error: {e}")
                continue

        logger.debug(f"Parsed {len(rates)} rates")
        return rates


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    from config import EXCHANGE_DIRECTIONS

    with SeleniumParser(headless=False) as parser:
        for from_curr, to_curr in EXCHANGE_DIRECTIONS[:2]:
            rates = parser.fetch_exchange_rates(from_curr, to_curr)
            print(f"\nRESULTS for {from_curr} -> {to_curr}:")
            for r in rates:
                print(f"  {r.exchanger_name}: price={r.price:.2f} RUB")
