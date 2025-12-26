"""
XML Generator for exchange rates
"""

import logging
from datetime import datetime
from xml.dom.minidom import Document
from typing import Optional

from parser import ExchangeRate
from config import DEFAULT_VALUES, OUTPUT_XML_PATH

logger = logging.getLogger(__name__)


def generate_xml(rates: list[ExchangeRate], output_path: Optional[str] = None) -> str:
    """
    Generate XML file with exchange rates.

    Format:
    <?xml version="1.0" ?>
    <rates generated="2025-12-23T18:11:17.800047" count="10">
      <item>
        <from>BTC</from>
        <to>SBERRUB</to>
        <in>1</in>
        <out>7053614</out>
        <amount>1000000</amount>
        <minamount>100</minamount>
        <maxamount>500000</maxamount>
        <param>0</param>
      </item>
      ...
    </rates>

    Note: We invert the direction so that crypto is "from" and fiat is "to".
    This gives us rates like "1 BTC = 7053614 RUB" which is more readable.
    """
    if output_path is None:
        output_path = OUTPUT_XML_PATH

    doc = Document()

    # Root element
    rates_elem = doc.createElement("rates")
    rates_elem.setAttribute("generated", datetime.now().isoformat())
    rates_elem.setAttribute("count", str(len(rates)))
    doc.appendChild(rates_elem)

    for rate in rates:
        item = doc.createElement("item")

        # INVERTED DIRECTION:
        # Original: from_currency (RUB) -> to_currency (BTC)
        # XML: from (BTC) -> to (RUB)
        # This makes the rate human-readable: 1 BTC = X RUB

        # from - what you exchange (crypto)
        from_elem = doc.createElement("from")
        from_elem.appendChild(doc.createTextNode(rate.to_currency))
        item.appendChild(from_elem)

        # to - what you receive (fiat)
        to_elem = doc.createElement("to")
        to_elem.appendChild(doc.createTextNode(rate.from_currency))
        item.appendChild(to_elem)

        # in - always 1
        in_elem = doc.createElement("in")
        in_elem.appendChild(doc.createTextNode("1"))
        item.appendChild(in_elem)

        # out - inverse rate (how much fiat for 1 crypto)
        # inverse_rate = give_amount / receive_amount
        out_value = rate.inverse_rate
        out_elem = doc.createElement("out")
        out_elem.appendChild(doc.createTextNode(format_rate(out_value)))
        item.appendChild(out_elem)

        # amount - reserve (give_amount as it represents exchanger's capacity)
        amount_value = rate.give_amount if rate.give_amount else DEFAULT_VALUES["amount"]
        amount_elem = doc.createElement("amount")
        amount_elem.appendChild(doc.createTextNode(str(int(amount_value))))
        item.appendChild(amount_elem)

        # minamount - minimum amount
        minamount_value = rate.min_amount if rate.min_amount else DEFAULT_VALUES["minamount"]
        minamount_elem = doc.createElement("minamount")
        minamount_elem.appendChild(doc.createTextNode(str(int(minamount_value))))
        item.appendChild(minamount_elem)

        # maxamount - maximum amount
        maxamount_value = rate.max_amount if rate.max_amount else DEFAULT_VALUES["maxamount"]
        maxamount_elem = doc.createElement("maxamount")
        maxamount_elem.appendChild(doc.createTextNode(str(int(maxamount_value))))
        item.appendChild(maxamount_elem)

        # param - parameter (default 0)
        param_elem = doc.createElement("param")
        param_elem.appendChild(doc.createTextNode(str(DEFAULT_VALUES["param"])))
        item.appendChild(param_elem)

        rates_elem.appendChild(item)

        # Log what we're writing
        logger.info(
            f"XML: {rate.to_currency} -> {rate.from_currency}: "
            f"in=1, out={format_rate(out_value)} (from exchanger: {rate.exchanger_name})"
        )

    # Generate pretty XML
    xml_string = doc.toprettyxml(indent="  ", encoding=None)

    # Save to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(xml_string)

    logger.info(f"XML file saved: {output_path} ({len(rates)} rates)")

    return xml_string


def format_rate(rate: float) -> str:
    """
    Format rate for XML.
    For large numbers - no decimal part.
    For small numbers - with sufficient precision.
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
    Aggregate rates for XML.

    For each direction, we take the third-best rate (to be slightly better than it).
    """
    result = []

    for (from_curr, to_curr), rates in all_rates.items():
        if not rates:
            logger.warning(f"No rates for {from_curr} -> {to_curr}")
            continue

        # Take third in ranking (if available), otherwise the last one
        # This gives us the rate we need to slightly improve
        if len(rates) >= 3:
            target_rate = rates[2]  # Third in top
        else:
            target_rate = rates[-1]  # Last available

        result.append(target_rate)

        logger.info(
            f"Selected for XML: {from_curr} -> {to_curr}: "
            f"{target_rate.exchanger_name} | "
            f"give={target_rate.give_amount:.4f}, receive={target_rate.receive_amount:.4f} | "
            f"inverse_rate={target_rate.inverse_rate:.4f}"
        )

    return result


if __name__ == "__main__":
    # Test run
    logging.basicConfig(level=logging.DEBUG)

    # Create test data
    test_rates = [
        ExchangeRate(
            exchanger_name="TestExchanger",
            from_currency="SBERRUB",
            to_currency="BTC",
            give_amount=7053614.9476,
            receive_amount=1.0,
            min_amount=1000,
            max_amount=100000000,
        ),
        ExchangeRate(
            exchanger_name="TestExchanger2",
            from_currency="SBERRUB",
            to_currency="USDTTRC20",
            give_amount=94.5,
            receive_amount=1.0,
            min_amount=100,
            max_amount=500000,
        ),
    ]

    xml_content = generate_xml(test_rates, "test_rates.xml")
    print(xml_content)