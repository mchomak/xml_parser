"""
Parser module for processing exnode.ru data.
Handles various input formats and extracts structured rate information.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedDirection:
    """Parsed exchange direction with normalized fields."""
    from_currency: str
    to_currency: str
    in_amount: float
    out_amount: float
    reserve: Optional[float] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    param: Optional[str] = None
    exchanger_id: Optional[str] = None
    exchanger_name: Optional[str] = None


class ExnodeParser:
    """
    Parser for exnode.ru data formats.

    Supports:
    - JSON API responses
    - HTML page content
    - Various nested data structures
    """

    # Regex patterns for extracting data
    RATE_PATTERNS = [
        # Pattern: "1 USDT = 92.5 RUB"
        re.compile(r'(\d+(?:\.\d+)?)\s*(\w+)\s*[=:→]\s*(\d+(?:\.\d+)?)\s*(\w+)', re.I),
        # Pattern: "USDT/RUB: 92.5"
        re.compile(r'(\w+)\s*/\s*(\w+)\s*[:=]\s*(\d+(?:\.\d+)?)', re.I),
    ]

    CURRENCY_CLEANUP = re.compile(r'[^\w\d]')

    def __init__(self):
        pass

    def parse_json_response(
        self,
        data: dict[str, Any],
        exchanger_id: Optional[str] = None,
        exchanger_name: Optional[str] = None
    ) -> list[ParsedDirection]:
        """
        Parse a JSON API response into ParsedDirection objects.

        Handles various JSON structures commonly used by exchange aggregators.
        """
        directions = []

        # Find the rates array in various possible locations
        rates_data = self._extract_rates_array(data)

        for item in rates_data:
            parsed = self._parse_rate_item(item, exchanger_id, exchanger_name)
            if parsed:
                directions.append(parsed)

        logger.debug(f"Parsed {len(directions)} directions from JSON")
        return directions

    def _extract_rates_array(self, data: Any) -> list[dict]:
        """Extract the rates array from various JSON structures."""
        if isinstance(data, list):
            return data

        if not isinstance(data, dict):
            return []

        # Try common keys in order of preference
        for key in ['rates', 'data', 'items', 'directions', 'result', 'exchanges']:
            if key in data:
                value = data[key]
                if isinstance(value, list):
                    return value
                elif isinstance(value, dict):
                    # Might be nested further
                    return self._extract_rates_array(value)

        # Check for nested exchanger data
        if 'exchanger' in data and isinstance(data['exchanger'], dict):
            return self._extract_rates_array(data['exchanger'])

        if 'exchangers' in data and isinstance(data['exchangers'], list):
            all_rates = []
            for exc in data['exchangers']:
                if isinstance(exc, dict):
                    all_rates.extend(self._extract_rates_array(exc))
            return all_rates

        return []

    def _parse_rate_item(
        self,
        item: dict[str, Any],
        exchanger_id: Optional[str] = None,
        exchanger_name: Optional[str] = None
    ) -> Optional[ParsedDirection]:
        """Parse a single rate item from JSON."""
        if not isinstance(item, dict):
            return None

        # Extract currencies
        from_curr = self._get_field(item, [
            'from', 'from_currency', 'give', 'send', 'source', 'currency_from',
            'fromCurrency', 'giveCurrency', 'in_currency'
        ])
        to_curr = self._get_field(item, [
            'to', 'to_currency', 'get', 'receive', 'target', 'currency_to',
            'toCurrency', 'getCurrency', 'out_currency'
        ])

        if not from_curr or not to_curr:
            return None

        # Extract amounts
        in_amount = self._parse_number(self._get_field(item, [
            'in', 'in_amount', 'inAmount', 'give_amount', 'send_amount'
        ], '1'))

        out_amount = self._parse_number(self._get_field(item, [
            'out', 'out_amount', 'outAmount', 'get_amount', 'receive_amount',
            'rate', 'exchange_rate', 'exchangeRate', 'price'
        ], '0'))

        if out_amount == 0:
            return None

        # Extract optional fields
        reserve = self._parse_number(self._get_field(item, [
            'reserve', 'amount', 'available', 'balance', 'stock'
        ]))

        min_amount = self._parse_number(self._get_field(item, [
            'min', 'min_amount', 'minAmount', 'minamount', 'minimum'
        ]))

        max_amount = self._parse_number(self._get_field(item, [
            'max', 'max_amount', 'maxAmount', 'maxamount', 'maximum'
        ]))

        param = self._get_field(item, [
            'param', 'params', 'flags', 'options', 'settings'
        ])

        # Get exchanger info from item if available
        item_exchanger_id = self._get_field(item, ['exchanger_id', 'exchangerId', 'id'])
        item_exchanger_name = self._get_field(item, ['exchanger_name', 'exchangerName', 'name'])

        return ParsedDirection(
            from_currency=str(from_curr),
            to_currency=str(to_curr),
            in_amount=in_amount if in_amount > 0 else 1.0,
            out_amount=out_amount,
            reserve=reserve,
            min_amount=min_amount,
            max_amount=max_amount,
            param=str(param) if param else None,
            exchanger_id=item_exchanger_id or exchanger_id,
            exchanger_name=item_exchanger_name or exchanger_name
        )

    def _get_field(
        self,
        item: dict,
        keys: list[str],
        default: Any = None
    ) -> Any:
        """Get a field value trying multiple possible key names."""
        for key in keys:
            if key in item and item[key] is not None:
                return item[key]

            # Try case-insensitive match
            lower_key = key.lower()
            for k, v in item.items():
                if k.lower() == lower_key and v is not None:
                    return v

        return default

    def _parse_number(self, value: Any) -> Optional[float]:
        """Parse a number from various formats."""
        if value is None:
            return None

        try:
            if isinstance(value, (int, float)):
                return float(value)

            if isinstance(value, str):
                # Clean up the string
                cleaned = value.strip().replace(',', '.').replace(' ', '')
                # Remove currency symbols and units
                cleaned = re.sub(r'[^\d.\-]', '', cleaned)
                if cleaned:
                    return float(cleaned)

            return None
        except (ValueError, TypeError):
            return None

    def parse_html_table(
        self,
        html: str,
        exchanger_id: Optional[str] = None,
        exchanger_name: Optional[str] = None
    ) -> list[ParsedDirection]:
        """
        Parse exchange rates from HTML table content.

        This is a fallback parser for when JSON APIs are not available.
        """
        directions = []

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("BeautifulSoup not installed, HTML parsing unavailable")
            return directions

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Find rate tables
            tables = self._find_rate_tables(soup)

            for table in tables:
                directions.extend(
                    self._parse_table_rows(table, exchanger_id, exchanger_name)
                )

            # Also try to find embedded JSON data
            directions.extend(
                self._extract_embedded_json(soup, exchanger_id, exchanger_name)
            )

            # Try to find rate data in divs/spans
            directions.extend(
                self._parse_rate_divs(soup, exchanger_id, exchanger_name)
            )

        except Exception as e:
            logger.error(f"Error parsing HTML: {e}")

        logger.debug(f"Parsed {len(directions)} directions from HTML")
        return directions

    def _find_rate_tables(self, soup) -> list:
        """Find tables that likely contain rate data."""
        tables = []

        # Look for tables with rate-related classes
        rate_classes = ['rate', 'exchange', 'direction', 'currency', 'price']
        for cls in rate_classes:
            tables.extend(soup.find_all('table', class_=re.compile(cls, re.I)))

        # Also get tables with rate-related IDs
        for id_pattern in rate_classes:
            tables.extend(soup.find_all('table', id=re.compile(id_pattern, re.I)))

        # If no specific tables found, try all tables
        if not tables:
            tables = soup.find_all('table')

        # Deduplicate
        seen = set()
        unique_tables = []
        for table in tables:
            table_id = id(table)
            if table_id not in seen:
                seen.add(table_id)
                unique_tables.append(table)

        return unique_tables

    def _parse_table_rows(
        self,
        table,
        exchanger_id: Optional[str],
        exchanger_name: Optional[str]
    ) -> list[ParsedDirection]:
        """Parse rows from an HTML table."""
        directions = []
        rows = table.find_all('tr')

        # Try to identify column headers
        headers = []
        header_row = table.find('thead')
        if header_row:
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all(['th', 'td'])]
        elif rows:
            first_row = rows[0]
            if first_row.find('th'):
                headers = [th.get_text(strip=True).lower() for th in first_row.find_all(['th', 'td'])]
                rows = rows[1:]

        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 3:
                continue

            cell_texts = [cell.get_text(strip=True) for cell in cells]

            # Try to map to expected columns based on headers or position
            from_curr = to_curr = rate = None
            reserve = min_amount = max_amount = None

            if headers:
                col_map = {h: i for i, h in enumerate(headers)}
                from_curr = self._get_cell_by_headers(cell_texts, col_map, ['from', 'give', 'send', 'source'])
                to_curr = self._get_cell_by_headers(cell_texts, col_map, ['to', 'get', 'receive', 'target'])
                rate = self._get_cell_by_headers(cell_texts, col_map, ['rate', 'price', 'course', 'out'])
                reserve = self._get_cell_by_headers(cell_texts, col_map, ['reserve', 'amount', 'stock'])
                min_amount = self._get_cell_by_headers(cell_texts, col_map, ['min', 'minimum'])
                max_amount = self._get_cell_by_headers(cell_texts, col_map, ['max', 'maximum'])
            else:
                # Assume positional: from, to, rate, [reserve, min, max]
                if len(cell_texts) >= 3:
                    from_curr = cell_texts[0]
                    to_curr = cell_texts[1]
                    rate = cell_texts[2]
                    if len(cell_texts) >= 4:
                        reserve = cell_texts[3]
                    if len(cell_texts) >= 5:
                        min_amount = cell_texts[4]
                    if len(cell_texts) >= 6:
                        max_amount = cell_texts[5]

            if from_curr and to_curr and rate:
                out_amount = self._parse_number(rate)
                if out_amount and out_amount > 0:
                    directions.append(ParsedDirection(
                        from_currency=from_curr,
                        to_currency=to_curr,
                        in_amount=1.0,
                        out_amount=out_amount,
                        reserve=self._parse_number(reserve),
                        min_amount=self._parse_number(min_amount),
                        max_amount=self._parse_number(max_amount),
                        exchanger_id=exchanger_id,
                        exchanger_name=exchanger_name
                    ))

        return directions

    def _get_cell_by_headers(
        self,
        cells: list[str],
        col_map: dict[str, int],
        possible_headers: list[str]
    ) -> Optional[str]:
        """Get cell value by trying multiple possible header names."""
        for header in possible_headers:
            for h, idx in col_map.items():
                if header in h and idx < len(cells):
                    return cells[idx]
        return None

    def _extract_embedded_json(
        self,
        soup,
        exchanger_id: Optional[str],
        exchanger_name: Optional[str]
    ) -> list[ParsedDirection]:
        """Extract rate data from embedded JSON in script tags."""
        directions = []

        scripts = soup.find_all('script')
        for script in scripts:
            if not script.string:
                continue

            # Look for JSON assignments
            patterns = [
                r'(?:rates|directions|exchangeData|data)\s*[=:]\s*(\[[^\]]+\])',
                r'(?:rates|directions|exchangeData|data)\s*[=:]\s*(\{[^}]+\})',
                r'JSON\.parse\s*\(\s*[\'"](.+?)[\'"]\s*\)',
            ]

            for pattern in patterns:
                matches = re.findall(pattern, script.string, re.DOTALL)
                for match in matches:
                    try:
                        # Unescape if needed
                        json_str = match.replace('\\"', '"').replace("\\'", "'")
                        data = json.loads(json_str)
                        directions.extend(
                            self.parse_json_response(data, exchanger_id, exchanger_name)
                        )
                    except (json.JSONDecodeError, Exception):
                        continue

        return directions

    def _parse_rate_divs(
        self,
        soup,
        exchanger_id: Optional[str],
        exchanger_name: Optional[str]
    ) -> list[ParsedDirection]:
        """Parse rate data from div/span elements with rate classes."""
        directions = []

        # Look for elements with rate-related data attributes
        rate_elements = soup.find_all(attrs={'data-from': True, 'data-to': True})

        for elem in rate_elements:
            from_curr = elem.get('data-from')
            to_curr = elem.get('data-to')
            rate = elem.get('data-rate') or elem.get('data-out') or elem.get_text(strip=True)

            if from_curr and to_curr:
                out_amount = self._parse_number(rate)
                if out_amount and out_amount > 0:
                    directions.append(ParsedDirection(
                        from_currency=from_curr,
                        to_currency=to_curr,
                        in_amount=1.0,
                        out_amount=out_amount,
                        reserve=self._parse_number(elem.get('data-reserve')),
                        min_amount=self._parse_number(elem.get('data-min')),
                        max_amount=self._parse_number(elem.get('data-max')),
                        exchanger_id=exchanger_id,
                        exchanger_name=exchanger_name
                    ))

        return directions

    def parse_text_rates(
        self,
        text: str,
        exchanger_id: Optional[str] = None,
        exchanger_name: Optional[str] = None
    ) -> list[ParsedDirection]:
        """Parse rates from plain text using regex patterns."""
        directions = []

        for pattern in self.RATE_PATTERNS:
            for match in pattern.finditer(text):
                groups = match.groups()

                if len(groups) == 4:
                    # Pattern: amount1 curr1 = amount2 curr2
                    in_amount = self._parse_number(groups[0]) or 1.0
                    from_curr = groups[1]
                    out_amount = self._parse_number(groups[2])
                    to_curr = groups[3]

                    if out_amount and out_amount > 0:
                        directions.append(ParsedDirection(
                            from_currency=from_curr,
                            to_currency=to_curr,
                            in_amount=in_amount,
                            out_amount=out_amount,
                            exchanger_id=exchanger_id,
                            exchanger_name=exchanger_name
                        ))

                elif len(groups) == 3:
                    # Pattern: curr1/curr2: rate
                    from_curr = groups[0]
                    to_curr = groups[1]
                    out_amount = self._parse_number(groups[2])

                    if out_amount and out_amount > 0:
                        directions.append(ParsedDirection(
                            from_currency=from_curr,
                            to_currency=to_curr,
                            in_amount=1.0,
                            out_amount=out_amount,
                            exchanger_id=exchanger_id,
                            exchanger_name=exchanger_name
                        ))

        return directions
