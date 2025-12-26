"""
Парсер курсов обмена криптовалют с exnode.ru
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
    """Курс обмена от обменника"""
    exchanger_name: str      # Название обменника
    from_currency: str       # Исходная валюта
    to_currency: str         # Целевая валюта
    rate: float              # Курс (сколько получаешь за 1 единицу)
    min_amount: Optional[float] = None   # Минимальная сумма
    max_amount: Optional[float] = None   # Максимальная сумма
    reserve: Optional[float] = None      # Резерв обменника


def parse_amount(text: str) -> Optional[float]:
    """
    Парсит число из текста, убирая пробелы и запятые.
    Примеры: "6 807 113.7810" -> 6807113.7810
             "1" -> 1.0
             "270 000 000" -> 270000000.0
    """
    if not text:
        return None

    # Убираем все кроме цифр, точек и минусов
    cleaned = re.sub(r'[^\d.,\-]', '', text.replace(' ', ''))

    # Заменяем запятую на точку (для европейского формата)
    cleaned = cleaned.replace(',', '.')

    # Если несколько точек - оставляем только последнюю (разделитель тысяч)
    parts = cleaned.split('.')
    if len(parts) > 2:
        cleaned = ''.join(parts[:-1]) + '.' + parts[-1]

    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_page(url: str) -> Optional[str]:
    """Загрузить страницу с помощью requests"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Ошибка загрузки страницы {url}: {e}")
        return None


def parse_exchangers_from_html(html: str, from_currency: str, to_currency: str) -> list[ExchangeRate]:
    """
    Парсит список обменников из HTML страницы.

    Структура HTML (на основе предоставленных данных):
    - Контейнер обменника: div.Table_body__el__IK40q
    - Название: p.Table_body__el__name__9fI44
    - Суммы: div.Table_body__amount____C1r (первый - отдаёте, второй - получаете)
    - Лимиты: div.Table_body__change__el__XwiOv
    """
    soup = BeautifulSoup(html, 'html.parser')
    rates = []

    # Ищем все строки таблицы обменников
    # Пробуем разные селекторы, т.к. классы могут отличаться
    exchanger_rows = soup.find_all('div', class_=re.compile(r'Table_body__el__'))

    if not exchanger_rows:
        # Альтернативный поиск по ID (из примера - id="Insight" это название обменника)
        exchanger_rows = soup.find_all('div', id=True)
        exchanger_rows = [row for row in exchanger_rows if row.find('p', class_=re.compile(r'Table_body__el__name'))]

    logger.info(f"Найдено {len(exchanger_rows)} обменников для {from_currency} -> {to_currency}")

    for row in exchanger_rows:
        try:
            # Название обменника
            name_elem = row.find('p', class_=re.compile(r'Table_body__el__name'))
            if not name_elem:
                # Попробуем найти по ID элемента
                name = row.get('id', '')
                if not name:
                    continue
            else:
                name = name_elem.get_text(strip=True)

            if not name:
                continue

            # Суммы обмена
            amount_elems = row.find_all('div', class_=re.compile(r'Table_body__amount'))

            if len(amount_elems) < 2:
                logger.debug(f"Недостаточно элементов суммы для {name}")
                continue

            # Первый amount - сколько отдаёте
            # Второй amount - сколько получаете
            give_text = amount_elems[0].find('p')
            receive_text = amount_elems[1].find('p')

            if not give_text or not receive_text:
                continue

            give_amount = parse_amount(give_text.get_text())
            receive_amount = parse_amount(receive_text.get_text())

            if give_amount is None or receive_amount is None or give_amount == 0:
                continue

            # Вычисляем курс: сколько получаешь за 1 единицу отдаваемой валюты
            rate = receive_amount / give_amount

            # Лимиты обмена
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
                reserve=give_amount  # Используем сумму "отдаёте" как резерв
            )

            rates.append(exchange_rate)
            logger.debug(f"Обменник {name}: {rate:.8f}")

        except Exception as e:
            logger.warning(f"Ошибка парсинга обменника: {e}")
            continue

    return rates


def get_top_rates(rates: list[ExchangeRate], count: int = TOP_COUNT) -> list[ExchangeRate]:
    """
    Получить топ-N обменников по лучшему курсу.
    Лучший курс = больше получаешь за свои деньги.
    """
    # Сортируем по убыванию курса (чем больше - тем лучше для клиента)
    sorted_rates = sorted(rates, key=lambda r: r.rate, reverse=True)
    return sorted_rates[:count]


def fetch_exchange_rates(from_currency: str, to_currency: str) -> list[ExchangeRate]:
    """
    Получить курсы обмена для направления from -> to.
    Возвращает топ-N обменников.
    """
    url = build_exchange_url(from_currency, to_currency)
    logger.info(f"Загружаем курсы {from_currency} -> {to_currency}: {url}")

    html = fetch_page(url)

    if not html:
        logger.error(f"Не удалось загрузить страницу для {from_currency} -> {to_currency}")
        return []

    # Проверяем, есть ли на странице данные таблицы
    if 'Table_body__el__' not in html and 'Table_body__amount' not in html:
        logger.warning(f"Страница не содержит данных таблицы. Возможно, сайт использует JavaScript для рендеринга.")
        logger.info("Попробуйте использовать Selenium-парсер (parser_selenium.py)")
        return []

    rates = parse_exchangers_from_html(html, from_currency, to_currency)

    if not rates:
        logger.warning(f"Не найдено обменников для {from_currency} -> {to_currency}")
        return []

    top_rates = get_top_rates(rates)
    logger.info(f"Топ-{len(top_rates)} обменников для {from_currency} -> {to_currency}")

    return top_rates


if __name__ == "__main__":
    # Тестовый запуск
    logging.basicConfig(level=logging.DEBUG)

    from config import EXCHANGE_DIRECTIONS

    for from_curr, to_curr in EXCHANGE_DIRECTIONS[:2]:
        rates = fetch_exchange_rates(from_curr, to_curr)
        for rate in rates:
            print(f"{rate.exchanger_name}: 1 {rate.from_currency} = {rate.rate:.8f} {rate.to_currency}")
        print()
