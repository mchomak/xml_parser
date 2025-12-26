"""
Selenium-парсер курсов обмена криптовалют с exnode.ru
Используется когда сайт рендерится через JavaScript
"""

import re
import logging
import time
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from parser import ExchangeRate, parse_amount, get_top_rates
from config import TOP_COUNT, build_exchange_url

logger = logging.getLogger(__name__)


class SeleniumParser:
    """Парсер с использованием Selenium для JavaScript-рендеринга"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.driver: Optional[webdriver.Chrome] = None

    def _init_driver(self):
        """Инициализировать WebDriver"""
        if self.driver is not None:
            return

        options = Options()

        if self.headless:
            options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Отключаем логи
        options.add_experimental_option('excludeSwitches', ['enable-logging'])

        try:
            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(30)
            logger.info("Selenium WebDriver инициализирован")
        except WebDriverException as e:
            logger.error(f"Не удалось инициализировать WebDriver: {e}")
            raise

    def close(self):
        """Закрыть WebDriver"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            logger.info("Selenium WebDriver закрыт")

    def __enter__(self):
        self._init_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def fetch_exchange_rates(self, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """
        Получить курсы обмена с использованием Selenium.
        """
        self._init_driver()

        url = build_exchange_url(from_currency, to_currency)
        logger.info(f"[Selenium] Загружаем {from_currency} -> {to_currency}: {url}")

        try:
            self.driver.get(url)

            # Ждём загрузки таблицы обменников
            wait = WebDriverWait(self.driver, 15)

            # Пробуем разные селекторы
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
                    logger.debug(f"Таблица найдена по селектору: {selector}")
                    break
                except TimeoutException:
                    continue

            if not table_loaded:
                logger.warning("Таблица обменников не найдена. Пробуем парсить страницу как есть.")

            # Дополнительная пауза для полной загрузки
            time.sleep(2)

            # Получаем HTML после рендеринга
            html = self.driver.page_source

            rates = self._parse_page(html, from_currency, to_currency)

            if not rates:
                logger.warning(f"Не найдено обменников для {from_currency} -> {to_currency}")
                return []

            top_rates = get_top_rates(rates, TOP_COUNT)
            logger.info(f"Топ-{len(top_rates)} обменников для {from_currency} -> {to_currency}")

            return top_rates

        except TimeoutException:
            logger.error(f"Таймаут загрузки страницы {url}")
            return []
        except WebDriverException as e:
            logger.error(f"Ошибка WebDriver: {e}")
            return []

    def _parse_page(self, html: str, from_currency: str, to_currency: str) -> list[ExchangeRate]:
        """Парсить HTML страницу после рендеринга JavaScript"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        rates = []

        # Ищем строки таблицы
        exchanger_rows = soup.find_all('div', class_=re.compile(r'Table_body__el__'))

        logger.info(f"Найдено {len(exchanger_rows)} элементов обменников")

        for row in exchanger_rows:
            try:
                # Название обменника
                name_elem = row.find('p', class_=re.compile(r'Table_body__el__name'))
                if not name_elem:
                    # Попробуем найти по ID
                    name = row.get('id', '')
                else:
                    name = name_elem.get_text(strip=True)

                if not name:
                    continue

                # Суммы обмена
                amount_elems = row.find_all('div', class_=re.compile(r'Table_body__amount'))

                if len(amount_elems) < 2:
                    continue

                # Парсим текст из первого <p> в каждом amount
                give_p = amount_elems[0].find('p')
                receive_p = amount_elems[1].find('p')

                if not give_p or not receive_p:
                    continue

                give_text = give_p.get_text()
                receive_text = receive_p.get_text()

                give_amount = parse_amount(give_text)
                receive_amount = parse_amount(receive_text)

                if give_amount is None or receive_amount is None or give_amount == 0:
                    continue

                # Курс: сколько получаешь за 1 единицу
                rate = receive_amount / give_amount

                # Лимиты
                min_amount = None
                max_amount = None

                limit_elems = row.find_all('div', class_=re.compile(r'Table_body__change__el'))
                for limit_elem in limit_elems:
                    label = limit_elem.find('p')
                    value = limit_elem.find('span')

                    if label and value:
                        label_text = label.get_text(strip=True).lower()
                        value_amount = parse_amount(value.get_text())

                        if 'от' in label_text:
                            min_amount = value_amount
                        elif 'до' in label_text:
                            max_amount = value_amount

                exchange_rate = ExchangeRate(
                    exchanger_name=name,
                    from_currency=from_currency,
                    to_currency=to_currency,
                    rate=rate,
                    min_amount=min_amount,
                    max_amount=max_amount,
                    reserve=give_amount
                )

                rates.append(exchange_rate)
                logger.debug(f"Обменник {name}: {rate:.8f}")

            except Exception as e:
                logger.warning(f"Ошибка парсинга элемента: {e}")
                continue

        return rates


if __name__ == "__main__":
    # Тестовый запуск
    logging.basicConfig(level=logging.DEBUG)

    from config import EXCHANGE_DIRECTIONS

    with SeleniumParser(headless=True) as parser:
        for from_curr, to_curr in EXCHANGE_DIRECTIONS[:2]:
            rates = parser.fetch_exchange_rates(from_curr, to_curr)
            for rate in rates:
                print(f"{rate.exchanger_name}: 1 {rate.from_currency} = {rate.rate:.8f} {rate.to_currency}")
            print()
