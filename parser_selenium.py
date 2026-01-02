"""
Selenium parser for cryptocurrency exchange rates from exnode.ru
Uses undetected-chromedriver to bypass Cloudflare protection
"""
from __future__ import annotations

import gc
import os
import re
import logging
import time
import subprocess
import platform
from datetime import datetime
from typing import Optional
from pathlib import Path

# Try to use undetected-chromedriver first, fall back to regular selenium
try:
    import undetected_chromedriver as uc
    USING_UNDETECTED = True
except ImportError:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    USING_UNDETECTED = False

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from parser import ExchangeRate, parse_amount, get_top_rates, is_buying_crypto
from config import (
    TOP_COUNT, build_exchange_url,
    MAX_RETRIES, RETRY_DELAY, PAGE_TIMEOUT, ELEMENT_TIMEOUT, CALCULATOR_WAIT
)

logger = logging.getLogger(__name__)

# Configuration
DEBUG_SCREENSHOTS_DIR = Path(os.getenv('DEBUG_SCREENSHOTS_DIR', '/tmp/parser_screenshots'))
SAVE_DEBUG_SCREENSHOTS = os.getenv('SAVE_DEBUG_SCREENSHOTS', 'true').lower() == 'true'
BROWSER_RESTART_INTERVAL = int(os.getenv('BROWSER_RESTART_INTERVAL', '5'))
MAX_FETCH_TIME = int(os.getenv('MAX_FETCH_TIME', '45'))

# Faster timeouts
FAST_PAGE_TIMEOUT = min(PAGE_TIMEOUT, 15)

logger.info(f"Using {'undetected-chromedriver' if USING_UNDETECTED else 'regular selenium'}")


def kill_chrome_processes():
    """Kill orphan Chrome processes."""
    try:
        if platform.system() != 'Windows':
            subprocess.run(['pkill', '-9', '-f', 'chrome'], capture_output=True, timeout=3)
    except:
        pass


class SeleniumParser:
    """Parser using Selenium/undetected-chromedriver for JavaScript-rendered pages"""

    TABLE_SELECTORS = [
        "[class*='Table_body__el__']",
        "[class*='Table_body__amount']",
        "[class*='exchanger']",
        "div[class*='body__el']",
    ]

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.driver = None
        self._request_count = 0
        self._current_direction = ""
        self._consecutive_failures = 0

        if SAVE_DEBUG_SCREENSHOTS:
            DEBUG_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    def _init_driver(self):
        """Initialize WebDriver."""
        if self.driver is not None:
            return

        logger.info("Initializing WebDriver...")

        if USING_UNDETECTED:
            # Use undetected-chromedriver (bypasses Cloudflare)
            options = uc.ChromeOptions()

            if self.headless:
                options.add_argument("--headless=new")

            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-infobars")

            # Memory optimization
            options.add_argument("--disable-cache")
            options.add_argument("--disk-cache-size=0")
            options.add_argument("--disable-background-networking")
            options.add_argument("--disable-sync")
            options.add_argument("--disable-translate")
            options.add_argument("--mute-audio")
            options.add_argument("--no-first-run")

            try:
                self.driver = uc.Chrome(
                    options=options,
                    use_subprocess=True,
                    version_main=None  # Auto-detect Chrome version
                )
                self.driver.set_page_load_timeout(FAST_PAGE_TIMEOUT)
                logger.info("Undetected-chromedriver initialized")
            except Exception as e:
                logger.error(f"Failed to init undetected-chromedriver: {e}")
                raise

        else:
            # Fallback to regular selenium
            options = Options()

            if self.headless:
                options.add_argument("--headless=new")

            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-cache")
            options.add_argument("--disk-cache-size=0")

            options.add_argument(
                "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
            options.page_load_strategy = 'eager'

            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(FAST_PAGE_TIMEOUT)
            logger.info("Regular Selenium initialized")

        self._request_count = 0

    def close(self):
        """Close WebDriver."""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None
        kill_chrome_processes()

    def __enter__(self):
        kill_chrome_processes()
        time.sleep(0.5)
        self._init_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _save_screenshot(self, name: str) -> Optional[str]:
        """Save debug screenshot."""
        if not SAVE_DEBUG_SCREENSHOTS or not self.driver:
            return None
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{self._current_direction}_{name}.png"
            filepath = DEBUG_SCREENSHOTS_DIR / filename
            self.driver.save_screenshot(str(filepath))
            logger.info(f"Screenshot: {filepath}")
            return str(filepath)
        except:
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

    def _is_blocked(self) -> bool:
        """Check if page is blocked by Cloudflare."""
        if not self.driver:
            return True
        try:
            source = self.driver.page_source.lower()
            title = self.driver.title.lower()

            blockers = [
                'checking your browser', 'just a moment', 'cloudflare',
                'ddos protection', 'please wait', 'ray id', 'access denied',
            ]

            for b in blockers:
                if b in source or b in title:
                    logger.warning(f"Blocked: '{b}'")
                    return True

            if len(source) < 1000:
                return True

            return False
        except:
            return True

    def _wait_for_table(self, timeout: float = 12) -> bool:
        """Wait for exchange table to appear."""
        start = time.time()

        while time.time() - start < timeout:
            # Check for blocking
            if self._is_blocked():
                logger.debug("Waiting for Cloudflare to pass...")
                time.sleep(2)
                continue

            # Try to find table
            for selector in self.TABLE_SELECTORS:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements and len(elements) > 0:
                        logger.debug(f"Table found: {selector}")
                        return True
                except:
                    continue

            time.sleep(1)

        return False

    def _set_calculator(self, from_currency: str, to_currency: str) -> bool:
        """Set calculator input to 1."""
        buying = is_buying_crypto(from_currency, to_currency)
        input_id = "toInput" if buying else "fromInput"

        try:
            elem = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.ID, input_id))
            )

            self.driver.execute_script("""
                arguments[0].value = '1';
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """, elem)

            time.sleep(CALCULATOR_WAIT)
            return True

        except:
            # Try by position
            try:
                inputs = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    "input[type='text'], input[type='number'], input:not([type='hidden'])"
                )
                visible = [i for i in inputs if i.is_displayed() and i.is_enabled()]

                if len(visible) >= 2:
                    idx = 0 if input_id == "fromInput" else 1
                    self.driver.execute_script("""
                        arguments[0].value = '1';
                        arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                    """, visible[idx])
                    time.sleep(CALCULATOR_WAIT)
                    return True
            except:
                pass

        return False

    def _click_sort(self, from_currency: str, to_currency: str):
        """Click sort header."""
        buying = is_buying_crypto(from_currency, to_currency)
        header = "Отдаете" if buying else "Получаете"

        try:
            xpath = f"//div[contains(@class, 'Table_header')]//p[text()='{header}']"
            elem = WebDriverWait(self.driver, 2).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            elem.click()
            time.sleep(0.3)
            if buying:
                elem.click()
                time.sleep(0.3)
        except:
            pass

    def fetch_exchange_rates(self, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """Fetch exchange rates."""
        self._current_direction = f"{from_currency}_to_{to_currency}"
        start_time = time.time()

        # Restart if needed
        self._request_count += 1
        if self._request_count >= BROWSER_RESTART_INTERVAL:
            logger.info(f"Scheduled restart after {self._request_count} requests")
            self._restart_browser()

        if self._consecutive_failures >= 2:
            logger.warning(f"Restart after {self._consecutive_failures} failures")
            self._restart_browser()
            self._consecutive_failures = 0

        self._init_driver()

        url = build_exchange_url(from_currency, to_currency)
        logger.info(f"Fetching: {from_currency} -> {to_currency}")

        try:
            # Load page
            self.driver.get(url)

            # Wait for content
            time.sleep(2)

            if not self._wait_for_table(timeout=15):
                logger.warning("Table not found")
                self._save_screenshot("no_table")

                if self._is_blocked():
                    logger.error("Page blocked, restarting")
                    self._restart_browser()
                    self._consecutive_failures += 1
                    return []

            # Check timeout
            if time.time() - start_time > MAX_FETCH_TIME:
                logger.error("Timeout exceeded")
                self._consecutive_failures += 1
                return []

            # Set calculator and sort
            self._set_calculator(from_currency, to_currency)
            self._click_sort(from_currency, to_currency)

            # Parse
            html = self.driver.page_source
            rates = self._parse_page(html, from_currency, to_currency)

            if not rates:
                logger.warning("No rates parsed")
                self._save_screenshot("no_rates")
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

            gc.collect()
            return top_rates

        except TimeoutException as e:
            logger.error(f"Timeout: {e}")
            self._save_screenshot("timeout")
            self._consecutive_failures += 1
            self._restart_browser()
            return []

        except WebDriverException as e:
            logger.error(f"WebDriver error: {e}")
            self._save_screenshot("error")
            self._consecutive_failures += 1
            self._restart_browser()
            return []

        except Exception as e:
            logger.error(f"Error: {e}")
            self._consecutive_failures += 1
            return []

    def _parse_page(self, html: str, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """Parse exchange rates from HTML."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        rates = []

        # Find rows
        rows = []
        for pattern in [r'Table_body__el__', r'exchanger', r'body__el']:
            rows = soup.find_all('div', class_=re.compile(pattern))
            if rows:
                break

        logger.debug(f"Found {len(rows)} rows")

        for row in rows:
            try:
                # Name
                name = None
                for p in [r'Table_body__el__name', r'name']:
                    elem = row.find('p', class_=re.compile(p, re.IGNORECASE))
                    if elem:
                        name = elem.get_text(strip=True)
                        break

                if not name:
                    elem = row.find('p')
                    if elem:
                        name = elem.get_text(strip=True)

                if not name or len(name) < 2:
                    continue

                # Amounts
                amounts = row.find_all('div', class_=re.compile(r'Table_body__amount|amount', re.IGNORECASE))
                if len(amounts) < 2:
                    continue

                give_p = amounts[0].find('p') or amounts[0]
                recv_p = amounts[1].find('p') or amounts[1]

                give = parse_amount(give_p.get_text())
                recv = parse_amount(recv_p.get_text())

                if not give or not recv or give == 0:
                    continue

                # Price
                buying = is_buying_crypto(from_currency, to_currency)
                price = give if buying else recv

                # Limits
                min_amt = max_amt = None
                for lim in row.find_all('div', class_=re.compile(r'change__el|limit', re.IGNORECASE)):
                    label = lim.find('p')
                    value = lim.find('span')
                    if label and value:
                        lt = label.get_text(strip=True).lower()
                        val = parse_amount(value.get_text())
                        if any(k in lt for k in ['от', 'min']):
                            min_amt = val
                        elif any(k in lt for k in ['до', 'max']):
                            max_amt = val

                rates.append(ExchangeRate(
                    exchanger_name=name,
                    from_currency=from_currency,
                    to_currency=to_currency,
                    give_amount=give,
                    receive_amount=recv,
                    price=price,
                    min_amount=min_amt,
                    max_amount=max_amt,
                ))

            except:
                continue

        logger.debug(f"Parsed {len(rates)} rates")
        return rates


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    from config import EXCHANGE_DIRECTIONS

    with SeleniumParser(headless=False) as parser:
        for fc, tc in EXCHANGE_DIRECTIONS[:2]:
            rates = parser.fetch_exchange_rates(fc, tc)
            print(f"\n{fc} -> {tc}:")
            for r in rates:
                print(f"  {r.exchanger_name}: {r.price:.2f}")
