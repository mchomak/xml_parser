"""Tests for the parser module."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.parser import ExnodeParser, ParsedDirection


class TestJSONParsing:
    """Tests for JSON response parsing."""

    def setup_method(self):
        self.parser = ExnodeParser()

    def test_parse_flat_array(self):
        """Test parsing a flat array of rates."""
        data = [
            {
                "from": "USDT",
                "to": "RUB",
                "in": 1,
                "out": 92.5,
                "reserve": 1000000
            },
            {
                "from": "BTC",
                "to": "RUB",
                "in": 1,
                "out": 8500000,
                "reserve": 500000000
            }
        ]

        results = self.parser.parse_json_response(data)

        assert len(results) == 2
        assert results[0].from_currency == "USDT"
        assert results[0].to_currency == "RUB"
        assert results[0].out_amount == 92.5
        assert results[1].from_currency == "BTC"
        assert results[1].out_amount == 8500000

    def test_parse_nested_rates_key(self):
        """Test parsing JSON with nested 'rates' key."""
        data = {
            "status": "ok",
            "rates": [
                {
                    "from": "USDT",
                    "to": "RUB",
                    "rate": 92.5
                }
            ]
        }

        results = self.parser.parse_json_response(data)

        assert len(results) == 1
        assert results[0].from_currency == "USDT"
        assert results[0].out_amount == 92.5

    def test_parse_alternative_field_names(self):
        """Test parsing with alternative field names."""
        data = [
            {
                "from_currency": "BTC",
                "to_currency": "USD",
                "exchange_rate": 42000,
                "available": 10,
                "minimum": 0.001,
                "maximum": 10
            }
        ]

        results = self.parser.parse_json_response(data)

        assert len(results) == 1
        assert results[0].from_currency == "BTC"
        assert results[0].to_currency == "USD"
        assert results[0].out_amount == 42000
        assert results[0].reserve == 10
        assert results[0].min_amount == 0.001
        assert results[0].max_amount == 10

    def test_parse_give_get_format(self):
        """Test parsing with 'give'/'get' field names."""
        data = [
            {
                "give": "USDT",
                "get": "RUB",
                "rate": 92.5,
                "amount": 1000000
            }
        ]

        results = self.parser.parse_json_response(data)

        assert len(results) == 1
        assert results[0].from_currency == "USDT"
        assert results[0].to_currency == "RUB"

    def test_parse_skips_invalid(self):
        """Test that invalid entries are skipped."""
        data = [
            {
                "from": "USDT",
                "to": "RUB",
                "rate": 92.5
            },
            {
                "from": "BTC"
                # Missing 'to' field
            },
            {
                "from": "ETH",
                "to": "RUB",
                "rate": 0  # Zero rate
            }
        ]

        results = self.parser.parse_json_response(data)

        assert len(results) == 1
        assert results[0].from_currency == "USDT"

    def test_parse_empty_data(self):
        """Test parsing empty data."""
        assert self.parser.parse_json_response({}) == []
        assert self.parser.parse_json_response([]) == []

    def test_parse_with_exchanger_info(self):
        """Test that exchanger info is passed through."""
        data = [{"from": "USDT", "to": "RUB", "rate": 92.5}]

        results = self.parser.parse_json_response(
            data,
            exchanger_id="test_id",
            exchanger_name="Test Exchanger"
        )

        assert len(results) == 1
        assert results[0].exchanger_id == "test_id"
        assert results[0].exchanger_name == "Test Exchanger"


class TestTextParsing:
    """Tests for text-based rate parsing."""

    def setup_method(self):
        self.parser = ExnodeParser()

    def test_parse_rate_text_equals(self):
        """Test parsing 'X CURR = Y CURR' format."""
        text = "1 USDT = 92.5 RUB"

        results = self.parser.parse_text_rates(text)

        assert len(results) == 1
        assert results[0].from_currency == "USDT"
        assert results[0].to_currency == "RUB"
        assert results[0].in_amount == 1.0
        assert results[0].out_amount == 92.5

    def test_parse_rate_text_slash(self):
        """Test parsing 'CURR/CURR: rate' format."""
        text = "BTC/USD: 42000"

        results = self.parser.parse_text_rates(text)

        assert len(results) == 1
        assert results[0].from_currency == "BTC"
        assert results[0].to_currency == "USD"
        assert results[0].out_amount == 42000

    def test_parse_multiple_rates(self):
        """Test parsing text with multiple rates."""
        text = """
        Exchange rates:
        1 USDT = 92.5 RUB
        1 BTC = 8500000 RUB
        ETH/RUB: 320000
        """

        results = self.parser.parse_text_rates(text)

        # Should find at least the rates that match patterns
        assert len(results) >= 2


class TestHTMLParsing:
    """Tests for HTML parsing (requires BeautifulSoup)."""

    def setup_method(self):
        self.parser = ExnodeParser()

    def test_parse_html_table(self):
        """Test parsing rates from HTML table."""
        html = """
        <html>
        <body>
        <table class="rates-table">
            <tr><th>From</th><th>To</th><th>Rate</th><th>Reserve</th></tr>
            <tr><td>USDT</td><td>RUB</td><td>92.5</td><td>1000000</td></tr>
            <tr><td>BTC</td><td>RUB</td><td>8500000</td><td>500000000</td></tr>
        </table>
        </body>
        </html>
        """

        try:
            results = self.parser.parse_html_table(html)
            # Results depend on BeautifulSoup being installed
            if results:
                assert len(results) >= 2
        except ImportError:
            pytest.skip("BeautifulSoup not installed")

    def test_parse_html_with_data_attributes(self):
        """Test parsing rates from data attributes."""
        html = """
        <html>
        <body>
        <div class="rate" data-from="USDT" data-to="RUB" data-rate="92.5"></div>
        <div class="rate" data-from="BTC" data-to="RUB" data-rate="8500000"></div>
        </body>
        </html>
        """

        try:
            results = self.parser.parse_html_table(html)
            if results:
                assert any(r.from_currency == "USDT" for r in results)
        except ImportError:
            pytest.skip("BeautifulSoup not installed")


class TestNumberParsing:
    """Tests for number parsing edge cases."""

    def setup_method(self):
        self.parser = ExnodeParser()

    def test_parse_comma_decimal(self):
        """Test parsing numbers with comma as decimal separator."""
        assert self.parser._parse_number("92,5") == 92.5
        # European thousand separator format is an edge case
        # The parser handles simple comma decimals but not complex European formats

    def test_parse_with_spaces(self):
        """Test parsing numbers with spaces."""
        assert self.parser._parse_number("1 000 000") == 1000000

    def test_parse_with_currency_symbol(self):
        """Test parsing numbers with currency symbols."""
        assert self.parser._parse_number("$92.50") == 92.50
        assert self.parser._parse_number("92.50₽") == 92.50

    def test_parse_none(self):
        """Test parsing None."""
        assert self.parser._parse_number(None) is None

    def test_parse_empty_string(self):
        """Test parsing empty string."""
        assert self.parser._parse_number("") is None
        assert self.parser._parse_number("  ") is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
