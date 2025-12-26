#!/usr/bin/env python3
"""
Cryptocurrency Exchange Rate Parser for exnode.ru

Runs an infinite loop updating rates every N seconds
and generates XML file with top-3 competitor rates.

Usage:
    python main.py                  # Run with requests+bs4
    python main.py --selenium       # Run with Selenium (for JS rendering)
    python main.py --once           # Single run (no loop)
    python main.py --headless       # Run Selenium in headless mode
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime
from typing import Callable

from config import (
    UPDATE_INTERVAL,
    EXCHANGE_DIRECTIONS,
    OUTPUT_XML_PATH,
)
from parser import ExchangeRate, fetch_exchange_rates
from xml_generator import generate_xml, aggregate_rates_for_xml

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('parser.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

# Flag for graceful shutdown
running = True


def signal_handler(signum, frame):
    """Signal handler for graceful shutdown"""
    global running
    logger.info("Received stop signal. Shutting down...")
    running = False


def collect_all_rates(fetch_func: Callable) -> dict[tuple[str, str], list[ExchangeRate]]:
    """
    Collect rates for all exchange directions.

    Args:
        fetch_func: Function to fetch rates (requests or selenium)

    Returns:
        Dictionary {(from, to): [rates]}
    """
    all_rates = {}

    for from_currency, to_currency in EXCHANGE_DIRECTIONS:
        try:
            logger.info(f"Fetching rates for: {from_currency} -> {to_currency}")
            rates = fetch_func(from_currency, to_currency)
            all_rates[(from_currency, to_currency)] = rates

            if rates:
                logger.info(
                    f"SUCCESS: {from_currency} -> {to_currency}: "
                    f"{len(rates)} exchangers, best rate: {rates[0].rate:.8f}"
                )
            else:
                logger.warning(f"FAILED: {from_currency} -> {to_currency}: no data")

        except Exception as e:
            logger.error(f"ERROR parsing {from_currency} -> {to_currency}: {e}")
            all_rates[(from_currency, to_currency)] = []

    return all_rates


def update_rates_requests():
    """Update rates using requests + BeautifulSoup"""
    logger.info("=" * 70)
    logger.info(f"STARTING RATE UPDATE ({datetime.now().isoformat()})")
    logger.info("Mode: requests + BeautifulSoup")
    logger.info("=" * 70)

    all_rates = collect_all_rates(fetch_exchange_rates)

    # Aggregate rates for XML
    aggregated_rates = aggregate_rates_for_xml(all_rates)

    if aggregated_rates:
        generate_xml(aggregated_rates, OUTPUT_XML_PATH)
        logger.info(f"XML file updated: {OUTPUT_XML_PATH}")
    else:
        logger.error("Failed to get rates. XML file not updated.")


def update_rates_selenium(headless: bool = False):
    """
    Update rates using Selenium.

    Args:
        headless: If True, run browser in headless mode
    """
    try:
        from parser_selenium import SeleniumParser
    except ImportError:
        logger.error("Selenium not installed. Install with: pip install selenium")
        return

    logger.info("=" * 70)
    logger.info(f"STARTING RATE UPDATE ({datetime.now().isoformat()})")
    logger.info(f"Mode: Selenium ({'headless' if headless else 'visible browser'})")
    logger.info("=" * 70)

    with SeleniumParser(headless=headless) as parser:
        all_rates = collect_all_rates(parser.fetch_exchange_rates)

        # Aggregate rates for XML
        aggregated_rates = aggregate_rates_for_xml(all_rates)

        if aggregated_rates:
            generate_xml(aggregated_rates, OUTPUT_XML_PATH)
            logger.info(f"XML file updated: {OUTPUT_XML_PATH}")
        else:
            logger.error("Failed to get rates. XML file not updated.")


def run_loop(update_func: Callable, interval: int = UPDATE_INTERVAL):
    """
    Run infinite update loop.

    Args:
        update_func: Rate update function
        interval: Interval between updates in seconds
    """
    global running

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("=" * 70)
    logger.info("CRYPTOCURRENCY RATE PARSER STARTED")
    logger.info(f"Update interval: {interval} seconds")
    logger.info(f"Output file: {OUTPUT_XML_PATH}")
    logger.info(f"Exchange directions: {len(EXCHANGE_DIRECTIONS)}")
    logger.info("=" * 70)

    update_count = 0

    while running:
        try:
            update_func()
            update_count += 1
            logger.info(f"Update #{update_count} completed. Next update in {interval} seconds.")
            logger.info("-" * 70)

        except Exception as e:
            logger.exception(f"Critical error during update: {e}")

        # Wait with stop flag check
        for _ in range(interval):
            if not running:
                break
            time.sleep(1)

    logger.info(f"Parser stopped. Total updates: {update_count}")


def main():
    import config

    debug = True
    config.OUTPUT_XML_PATH = "rates2.xml"
    selenium = True
    once = True
    headless = False


    if debug:
        logging.getLogger().setLevel(logging.DEBUG)
    

    if selenium:
        # Create a wrapper with headless parameter
        def selenium_updater():
            update_rates_selenium(headless=headless)
        update_func = selenium_updater
    else:
        update_func = update_rates_requests

    if once:
        logger.info("4=>:@0B=K9 70?CA:")
        update_func()
    else:
        run_loop(update_func, UPDATE_INTERVAL)

if __name__ == "__main__":
    main()