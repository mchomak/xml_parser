"""
BestChange-compatible XML exporter for exchange rates.
Generates and writes XML feed files with atomic operations.
"""

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET
from xml.dom import minidom

from .config import Config, get_config
from .normalizer import NormalizedRate
from .utils import get_metrics

logger = logging.getLogger(__name__)


class XMLExporter:
    """
    Exports normalized rates to BestChange-compatible XML format.

    Output format:
    <?xml version="1.0" encoding="UTF-8"?>
    <rates>
        <item>
            <from>USDTTRC20</from>
            <to>SBERRUB</to>
            <in>1</in>
            <out>92.5</out>
            <amount>1000000</amount>
            <minamount>100</minamount>
            <maxamount>50000</maxamount>
            <param>0</param>
        </item>
        ...
    </rates>
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.metrics = get_metrics()
        self._last_valid_xml: Optional[str] = None
        self._last_export_time: Optional[datetime] = None

    def generate_xml(self, rates: list[NormalizedRate]) -> str:
        """
        Generate BestChange-compatible XML from normalized rates.

        Returns the XML as a string.
        """
        # Create root element
        root = ET.Element('rates')

        # Optional: Add metadata attributes
        root.set('generated', datetime.now().isoformat())
        root.set('count', str(len(rates)))

        # Add each rate as an item
        for rate in rates:
            item = ET.SubElement(root, 'item')

            # Required fields
            from_elem = ET.SubElement(item, 'from')
            from_elem.text = rate.from_currency

            to_elem = ET.SubElement(item, 'to')
            to_elem.text = rate.to_currency

            in_elem = ET.SubElement(item, 'in')
            in_elem.text = rate.in_amount

            out_elem = ET.SubElement(item, 'out')
            out_elem.text = rate.out_amount

            amount_elem = ET.SubElement(item, 'amount')
            amount_elem.text = rate.amount

            minamount_elem = ET.SubElement(item, 'minamount')
            minamount_elem.text = rate.min_amount

            maxamount_elem = ET.SubElement(item, 'maxamount')
            maxamount_elem.text = rate.max_amount

            param_elem = ET.SubElement(item, 'param')
            param_elem.text = rate.param

        # Convert to string with proper formatting
        xml_string = ET.tostring(root, encoding='unicode')

        # Pretty print with proper indentation
        try:
            dom = minidom.parseString(xml_string)
            pretty_xml = dom.toprettyxml(indent='  ', encoding=None)
            # Remove extra blank lines
            lines = [line for line in pretty_xml.split('\n') if line.strip()]
            xml_string = '\n'.join(lines)
        except Exception as e:
            logger.warning(f"Pretty printing failed: {e}")

        # Add XML declaration if not present
        if not xml_string.startswith('<?xml'):
            xml_string = f'<?xml version="1.0" encoding="{self.config.output_encoding}"?>\n{xml_string}'

        return xml_string

    def validate_xml(self, xml_string: str) -> bool:
        """
        Validate that the XML is well-formed.

        Returns True if valid, False otherwise.
        """
        try:
            ET.fromstring(xml_string.encode(self.config.output_encoding))
            return True
        except ET.ParseError as e:
            logger.error(f"XML validation failed: {e}")
            return False

    def validate_against_xsd(self, xml_string: str, xsd_path: str) -> bool:
        """
        Validate XML against an XSD schema.

        Requires lxml library for XSD validation.
        """
        try:
            from lxml import etree

            # Load XSD schema
            with open(xsd_path, 'rb') as f:
                schema_doc = etree.parse(f)
            schema = etree.XMLSchema(schema_doc)

            # Parse and validate XML
            xml_doc = etree.fromstring(xml_string.encode(self.config.output_encoding))
            return schema.validate(xml_doc)

        except ImportError:
            logger.warning("lxml not installed, XSD validation unavailable")
            return True  # Skip validation if lxml not available
        except Exception as e:
            logger.error(f"XSD validation failed: {e}")
            return False

    def write_xml(
        self,
        rates: list[NormalizedRate],
        output_path: Optional[str] = None
    ) -> bool:
        """
        Generate and write XML to file with atomic write operation.

        Uses write-to-temp-then-rename pattern to ensure the output file
        is never in a partially written state.

        Returns True on success, False on failure.
        """
        output_path = output_path or self.config.output_path

        try:
            # Generate XML
            xml_string = self.generate_xml(rates)

            # Validate if enabled
            if self.config.validate_xml:
                if not self.validate_xml(xml_string):
                    logger.error("Generated XML is not well-formed")
                    return False

                # XSD validation if path provided
                if self.config.xsd_path and os.path.exists(self.config.xsd_path):
                    if not self.validate_against_xsd(xml_string, self.config.xsd_path):
                        logger.error("XML failed XSD validation")
                        return False

            # Store as last valid XML
            self._last_valid_xml = xml_string

            # Atomic write: write to temp file first, then rename
            output_dir = os.path.dirname(os.path.abspath(output_path))
            os.makedirs(output_dir, exist_ok=True)

            # Create temp file in the same directory for atomic rename
            fd, temp_path = tempfile.mkstemp(
                suffix='.xml.tmp',
                dir=output_dir
            )

            try:
                # Write to temp file
                with os.fdopen(fd, 'w', encoding=self.config.output_encoding) as f:
                    f.write(xml_string)

                # Atomic rename
                os.replace(temp_path, output_path)

                self._last_export_time = datetime.now()
                logger.info(f"Exported {len(rates)} rates to {output_path}")

                # Record metrics
                import asyncio
                try:
                    asyncio.get_event_loop().run_until_complete(
                        self.metrics.record_export(len(rates))
                    )
                except RuntimeError:
                    # Event loop already running
                    pass

                return True

            except Exception as e:
                # Clean up temp file on error
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise

        except Exception as e:
            logger.error(f"Failed to write XML: {e}")
            return False

    async def write_xml_async(
        self,
        rates: list[NormalizedRate],
        output_path: Optional[str] = None
    ) -> bool:
        """
        Async version of write_xml for use in async context.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.write_xml,
            rates,
            output_path
        )

    def get_last_valid_xml(self) -> Optional[str]:
        """
        Get the last successfully generated XML.

        Useful for serving cached content when fetch fails.
        """
        return self._last_valid_xml

    def get_last_export_time(self) -> Optional[datetime]:
        """Get the timestamp of the last successful export."""
        return self._last_export_time


def create_minimal_xml(message: str = "No data available") -> str:
    """
    Create a minimal valid XML for error cases.

    Used when no rates are available but we need to serve something.
    """
    root = ET.Element('rates')
    root.set('error', message)
    root.set('generated', datetime.now().isoformat())

    xml_string = ET.tostring(root, encoding='unicode')
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_string}'


def generate_sample_xml(count: int = 10) -> str:
    """
    Generate sample XML with example rates for testing.
    """
    sample_rates = [
        NormalizedRate(
            from_currency="USDTTRC20",
            to_currency="SBERRUB",
            in_amount="1",
            out_amount="92.5",
            amount="1000000",
            min_amount="100",
            max_amount="500000",
            param="0"
        ),
        NormalizedRate(
            from_currency="SBERRUB",
            to_currency="USDTTRC20",
            in_amount="1",
            out_amount="0.01055",
            amount="50000",
            min_amount="1000",
            max_amount="1000000",
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
        NormalizedRate(
            from_currency="ETH",
            to_currency="TCSBRUB",
            in_amount="1",
            out_amount="320000",
            amount="100000000",
            min_amount="500",
            max_amount="5000000",
            param="0"
        ),
        NormalizedRate(
            from_currency="USDTERC20",
            to_currency="VTBRUB",
            in_amount="1",
            out_amount="91.8",
            amount="500000",
            min_amount="100",
            max_amount="300000",
            param="0"
        ),
        NormalizedRate(
            from_currency="LTC",
            to_currency="ACRUB",
            in_amount="1",
            out_amount="9500",
            amount="50000000",
            min_amount="500",
            max_amount="1000000",
            param="0"
        ),
        NormalizedRate(
            from_currency="QWRUB",
            to_currency="USDTTRC20",
            in_amount="1",
            out_amount="0.01048",
            amount="100000",
            min_amount="500",
            max_amount="100000",
            param="0"
        ),
        NormalizedRate(
            from_currency="YAMRUB",
            to_currency="BTC",
            in_amount="1",
            out_amount="0.0000001",
            amount="1000000",
            min_amount="1000",
            max_amount="500000",
            param="0"
        ),
        NormalizedRate(
            from_currency="CASHRUB",
            to_currency="USDTTRC20",
            in_amount="1",
            out_amount="0.01035",
            amount="500000",
            min_amount="5000",
            max_amount="1000000",
            param="0"
        ),
        NormalizedRate(
            from_currency="TON",
            to_currency="SBERRUB",
            in_amount="1",
            out_amount="520",
            amount="10000000",
            min_amount="100",
            max_amount="500000",
            param="0"
        ),
    ]

    exporter = XMLExporter()
    return exporter.generate_xml(sample_rates[:count])
