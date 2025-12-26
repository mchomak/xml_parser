"""
Генератор XML файла с курсами обмена
"""

import logging
from datetime import datetime
from xml.dom.minidom import Document
from typing import Optional

from parser import ExchangeRate
from config import DEFAULT_VALUES, OUTPUT_XML_PATH

logger = logging.getLogger(__name__)


def calculate_out_rate(rate: ExchangeRate) -> float:
    """
    Вычислить значение out для XML.

    В XML формате:
    - from: исходная валюта
    - to: целевая валюта
    - in: 1 (одна единица исходной валюты)
    - out: сколько получаешь целевой валюты за 1 единицу исходной
    """
    return rate.rate


def generate_xml(rates: list[ExchangeRate], output_path: Optional[str] = None) -> str:
    """
    Генерирует XML файл с курсами обмена.

    Формат:
    <?xml version="1.0" ?>
    <rates generated="2025-12-23T18:11:17.800047" count="10">
      <item>
        <from>USDTTRC20</from>
        <to>SBERRUB</to>
        <in>1</in>
        <out>92.5</out>
        <amount>1000000</amount>
        <minamount>100</minamount>
        <maxamount>500000</maxamount>
        <param>0</param>
      </item>
      ...
    </rates>
    """
    if output_path is None:
        output_path = OUTPUT_XML_PATH

    doc = Document()

    # Корневой элемент
    rates_elem = doc.createElement("rates")
    rates_elem.setAttribute("generated", datetime.now().isoformat())
    rates_elem.setAttribute("count", str(len(rates)))
    doc.appendChild(rates_elem)

    for rate in rates:
        item = doc.createElement("item")

        # from - исходная валюта
        from_elem = doc.createElement("from")
        from_elem.appendChild(doc.createTextNode(rate.from_currency))
        item.appendChild(from_elem)

        # to - целевая валюта
        to_elem = doc.createElement("to")
        to_elem.appendChild(doc.createTextNode(rate.to_currency))
        item.appendChild(to_elem)

        # in - всегда 1
        in_elem = doc.createElement("in")
        in_elem.appendChild(doc.createTextNode("1"))
        item.appendChild(in_elem)

        # out - курс (сколько получаешь за 1 единицу)
        out_value = calculate_out_rate(rate)
        out_elem = doc.createElement("out")
        out_elem.appendChild(doc.createTextNode(format_rate(out_value)))
        item.appendChild(out_elem)

        # amount - резерв обменника
        amount_value = rate.reserve if rate.reserve else DEFAULT_VALUES["amount"]
        amount_elem = doc.createElement("amount")
        amount_elem.appendChild(doc.createTextNode(str(int(amount_value))))
        item.appendChild(amount_elem)

        # minamount - минимальная сумма
        minamount_value = rate.min_amount if rate.min_amount else DEFAULT_VALUES["minamount"]
        minamount_elem = doc.createElement("minamount")
        minamount_elem.appendChild(doc.createTextNode(str(int(minamount_value))))
        item.appendChild(minamount_elem)

        # maxamount - максимальная сумма
        maxamount_value = rate.max_amount if rate.max_amount else DEFAULT_VALUES["maxamount"]
        maxamount_elem = doc.createElement("maxamount")
        maxamount_elem.appendChild(doc.createTextNode(str(int(maxamount_value))))
        item.appendChild(maxamount_elem)

        # param - параметр (по умолчанию 0)
        param_elem = doc.createElement("param")
        param_elem.appendChild(doc.createTextNode(str(DEFAULT_VALUES["param"])))
        item.appendChild(param_elem)

        rates_elem.appendChild(item)

    # Генерируем красивый XML
    xml_string = doc.toprettyxml(indent="  ", encoding=None)

    # Сохраняем в файл
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(xml_string)

    logger.info(f"XML файл сохранён: {output_path} ({len(rates)} курсов)")

    return xml_string


def format_rate(rate: float) -> str:
    """
    Форматирует курс для XML.
    Для больших чисел - без дробной части.
    Для маленьких - с достаточной точностью.
    """
    if rate >= 1000:
        return str(int(rate))
    elif rate >= 1:
        return f"{rate:.2f}"
    elif rate >= 0.0001:
        return f"{rate:.5f}"
    else:
        return f"{rate:.10f}"


def aggregate_rates_for_xml(all_rates: dict[tuple[str, str], list[ExchangeRate]]) -> list[ExchangeRate]:
    """
    Агрегирует курсы для XML.

    Для каждого направления берём лучший курс из топ-3 конкурентов
    (точнее, берём курс третьего обменника, чтобы быть чуть лучше него).
    """
    result = []

    for (from_curr, to_curr), rates in all_rates.items():
        if not rates:
            logger.warning(f"Нет курсов для {from_curr} -> {to_curr}")
            continue

        # Берём третий по рейтингу (если есть), иначе последний
        # Это даст нам курс, который нужно немного улучшить
        if len(rates) >= 3:
            target_rate = rates[2]  # Третий в топе
        else:
            target_rate = rates[-1]  # Последний из доступных

        result.append(target_rate)

        logger.debug(
            f"{from_curr} -> {to_curr}: "
            f"использован курс {target_rate.exchanger_name} = {target_rate.rate:.8f}"
        )

    return result


if __name__ == "__main__":
    # Тестовый запуск
    logging.basicConfig(level=logging.DEBUG)

    # Создаём тестовые данные
    test_rates = [
        ExchangeRate(
            exchanger_name="TestExchanger",
            from_currency="USDTTRC20",
            to_currency="SBERRUB",
            rate=92.5,
            min_amount=100,
            max_amount=500000,
            reserve=1000000
        ),
        ExchangeRate(
            exchanger_name="TestExchanger2",
            from_currency="BTC",
            to_currency="SBERRUB",
            rate=8500000,
            min_amount=1000,
            max_amount=10000000,
            reserve=500000000
        ),
    ]

    xml_content = generate_xml(test_rates, "test_rates.xml")
    print(xml_content)
