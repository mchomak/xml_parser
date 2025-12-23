"""Tests for the XML exporter module."""

import os
import tempfile
import pytest
import sys
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.exporter_xml import XMLExporter, create_minimal_xml, generate_sample_xml
from src.normalizer import NormalizedRate
from src.config import Config


class TestXMLExporter:
    """Tests for XML export functionality."""

    def setup_method(self):
        self.exporter = XMLExporter(Config())

    def test_generate_xml_basic(self):
        """Test basic XML generation."""
        rates = [
            NormalizedRate(
                from_currency="USDTTRC20",
                to_currency="SBERRUB",
                in_amount="1",
                out_amount="92.5",
                amount="1000000",
                min_amount="100",
                max_amount="50000",
                param="0"
            )
        ]

        xml_string = self.exporter.generate_xml(rates)

        # Parse and verify
        root = ET.fromstring(xml_string.encode('utf-8'))
        assert root.tag == 'rates'
        assert root.get('count') == '1'

        items = root.findall('item')
        assert len(items) == 1

        item = items[0]
        assert item.find('from').text == 'USDTTRC20'
        assert item.find('to').text == 'SBERRUB'
        assert item.find('in').text == '1'
        assert item.find('out').text == '92.5'
        assert item.find('amount').text == '1000000'
        assert item.find('minamount').text == '100'
        assert item.find('maxamount').text == '50000'
        assert item.find('param').text == '0'

    def test_generate_xml_multiple_rates(self):
        """Test XML generation with multiple rates."""
        rates = [
            NormalizedRate(
                from_currency="USDTTRC20",
                to_currency="SBERRUB",
                in_amount="1",
                out_amount="92.5",
                amount="1000000",
                min_amount="100",
                max_amount="50000",
                param="0"
            ),
            NormalizedRate(
                from_currency="BTC",
                to_currency="SBERRUB",
                in_amount="1",
                out_amount="8500000",
                amount="500000000",
                min_amount="1000",
                max_amount="10000000",
                param="0"
            ),
        ]

        xml_string = self.exporter.generate_xml(rates)

        root = ET.fromstring(xml_string.encode('utf-8'))
        assert root.get('count') == '2'
        assert len(root.findall('item')) == 2

    def test_generate_xml_empty(self):
        """Test XML generation with no rates."""
        xml_string = self.exporter.generate_xml([])

        root = ET.fromstring(xml_string.encode('utf-8'))
        assert root.get('count') == '0'
        assert len(root.findall('item')) == 0

    def test_validate_xml(self):
        """Test XML validation."""
        valid_xml = '<?xml version="1.0"?><rates><item><from>BTC</from></item></rates>'
        assert self.exporter.validate_xml(valid_xml) is True

        invalid_xml = '<?xml version="1.0"?><rates><item><from>BTC</from></rates>'
        assert self.exporter.validate_xml(invalid_xml) is False

    def test_write_xml_atomic(self):
        """Test atomic write to file."""
        rates = [
            NormalizedRate(
                from_currency="USDTTRC20",
                to_currency="SBERRUB",
                in_amount="1",
                out_amount="92.5",
                amount="1000000",
                min_amount="100",
                max_amount="50000",
                param="0"
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, 'test_output.xml')

            success = self.exporter.write_xml(rates, output_path)

            assert success is True
            assert os.path.exists(output_path)

            # Verify file content
            with open(output_path, 'r', encoding='utf-8') as f:
                content = f.read()

            root = ET.fromstring(content.encode('utf-8'))
            assert root.tag == 'rates'
            assert len(root.findall('item')) == 1

    def test_write_xml_creates_directory(self):
        """Test that write_xml creates parent directories if needed."""
        rates = [
            NormalizedRate(
                from_currency="BTC",
                to_currency="RUB",
                in_amount="1",
                out_amount="8500000",
                amount="100",
                min_amount="1",
                max_amount="100",
                param="0"
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            nested_path = os.path.join(tmpdir, 'nested', 'deep', 'output.xml')

            success = self.exporter.write_xml(rates, nested_path)

            assert success is True
            assert os.path.exists(nested_path)


class TestMinimalXML:
    """Tests for minimal XML generation."""

    def test_create_minimal_xml(self):
        """Test creating minimal XML for error cases."""
        xml_string = create_minimal_xml("Test error message")

        root = ET.fromstring(xml_string.encode('utf-8'))
        assert root.tag == 'rates'
        assert root.get('error') == "Test error message"
        assert root.get('generated') is not None


class TestSampleXML:
    """Tests for sample XML generation."""

    def test_generate_sample_xml(self):
        """Test generating sample XML."""
        xml_string = generate_sample_xml(5)

        root = ET.fromstring(xml_string.encode('utf-8'))
        assert root.tag == 'rates'
        assert len(root.findall('item')) == 5

    def test_generate_sample_xml_default_count(self):
        """Test generating sample XML with default count."""
        xml_string = generate_sample_xml()

        root = ET.fromstring(xml_string.encode('utf-8'))
        assert len(root.findall('item')) == 10


class TestXMLContent:
    """Tests for XML content formatting."""

    def test_xml_has_declaration(self):
        """Test that XML has proper declaration."""
        exporter = XMLExporter()
        xml_string = exporter.generate_xml([])

        assert xml_string.startswith('<?xml version=')

    def test_xml_encoding(self):
        """Test that XML declaration is valid."""
        config = Config(output_encoding='utf-8')
        exporter = XMLExporter(config)
        xml_string = exporter.generate_xml([])

        # XML declaration should exist and be parseable
        assert xml_string.startswith('<?xml version=')
        # Should be valid UTF-8 encoded XML
        root = ET.fromstring(xml_string.encode('utf-8'))
        assert root is not None

    def test_special_characters_escaped(self):
        """Test that special characters in currency names are handled."""
        rates = [
            NormalizedRate(
                from_currency="USDT&TRC20",  # Unusual but test escaping
                to_currency="SBER<RUB>",
                in_amount="1",
                out_amount="92.5",
                amount="1000000",
                min_amount="100",
                max_amount="50000",
                param="0"
            )
        ]

        exporter = XMLExporter()
        xml_string = exporter.generate_xml(rates)

        # Should still be valid XML
        root = ET.fromstring(xml_string.encode('utf-8'))
        assert root is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
