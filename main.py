#!/usr/bin/env python3
"""
Cryptocurrency Exchange Rate Parser for exnode.ru

Runs an infinite loop updating rates every N seconds
and generates XML file with top-3 competitor rates.
"""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Callable, Optional

from config import (
    UPDATE_INTERVAL,
    EXCHANGE_DIRECTIONS,
    OUTPUT_XML_PATH,
    HEADLESS,
    LOG_LEVEL,
    LOG_FILE,
    MAX_RETRIES,
    SELENIUM,
    ONCE,
)
from parser import ExchangeRate, fetch_exchange_rates
from xml_generator import generate_xml, aggregate_rates_for_xml


def setup_logging(level: str = None, log_file: str = None):
    """Setup logging configuration"""
    if level is None:
        level = LOG_LEVEL
    if log_file is None:
        log_file = LOG_FILE

    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding='utf-8')
        ]
    )


logger = logging.getLogger(__name__)

# Flag for graceful shutdown
running = True

# Store previous rates for fallback on errors
previous_rates: Optional[dict[tuple[str, str], list[ExchangeRate]]] = None


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
    global previous_rates
    all_rates = {}
    failed_directions = []

    for from_currency, to_currency in EXCHANGE_DIRECTIONS:
        try:
            rates = fetch_func(from_currency, to_currency)
            all_rates[(from_currency, to_currency)] = rates

            if rates:
                logger.info(f"OK: {from_currency} -> {to_currency}: {len(rates)} exchangers")
            else:
                logger.warning(f"EMPTY: {from_currency} -> {to_currency}: no data")
                failed_directions.append((from_currency, to_currency))

        except Exception as e:
            logger.error(f"ERROR: {from_currency} -> {to_currency}: {e}")
            all_rates[(from_currency, to_currency)] = []
            failed_directions.append((from_currency, to_currency))

    # Use previous rates for failed directions
    if previous_rates and failed_directions:
        for direction in failed_directions:
            if direction in previous_rates and previous_rates[direction]:
                logger.warning(f"Using previous data for {direction[0]} -> {direction[1]}")
                all_rates[direction] = previous_rates[direction]

    # Store successful rates for future fallback
    if all_rates:
        previous_rates = all_rates.copy()

    return all_rates


def update_rates_requests():
    """Update rates using requests + BeautifulSoup"""
    logger.info(f"Starting update ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    logger.info("Mode: requests + BeautifulSoup")

    all_rates = collect_all_rates(fetch_exchange_rates)
    aggregated_rates = aggregate_rates_for_xml(all_rates)

    if aggregated_rates:
        generate_xml(aggregated_rates, OUTPUT_XML_PATH)
        logger.info(f"XML updated: {OUTPUT_XML_PATH}")
    else:
        logger.error("No rates available. XML not updated.")


def update_rates_selenium(headless: bool = None):
    """
    Update rates using Selenium.

    Args:
        headless: If True, run browser in headless mode
    """
    try:
        from parser_selenium import SeleniumParser
    except ImportError:
        logger.error("Selenium not installed. Run: pip install selenium")
        return

    logger.info(f"Starting update ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    logger.info(f"Mode: Selenium ({'headless' if headless else 'visible'})")

    with SeleniumParser(headless=headless) as parser:
        all_rates = collect_all_rates(parser.fetch_exchange_rates)
        aggregated_rates = aggregate_rates_for_xml(all_rates)

        if aggregated_rates:
            generate_xml(aggregated_rates, OUTPUT_XML_PATH)
            logger.info(f"XML updated: {OUTPUT_XML_PATH}")
        else:
            logger.error("No rates available. XML not updated.")


def run_loop(update_func: Callable, interval: int = None):
    """
    Run infinite update loop.

    Args:
        update_func: Rate update function
        interval: Interval between updates in seconds
    """
    global running

    if interval is None:
        interval = UPDATE_INTERVAL

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("=" * 60)
    logger.info("CRYPTOCURRENCY RATE PARSER STARTED")
    logger.info(f"Update interval: {interval}s")
    logger.info(f"Output file: {OUTPUT_XML_PATH}")
    logger.info(f"Directions: {len(EXCHANGE_DIRECTIONS)}")
    logger.info(f"Max retries: {MAX_RETRIES}")
    logger.info("=" * 60)

    update_count = 0

    while running:
        try:
            update_func()
            update_count += 1
            logger.info(f"Update #{update_count} complete. Next in {interval}s")

        except Exception as e:
            logger.exception(f"Critical error: {e}")

        for _ in range(interval):
            if not running:
                break
            time.sleep(1)

    logger.info(f"Parser stopped. Total updates: {update_count}")


def main():
    # Setup logging
    setup_logging(level=LOG_LEVEL)

    # Select update function
    if SELENIUM:
        def selenium_updater():
            update_rates_selenium(headless=True)
        update_func = selenium_updater
    else:
        update_func = update_rates_requests

    # Run
    if ONCE:
        logger.info("Single run mode")
        update_func()
    else:
        run_loop(update_func, UPDATE_INTERVAL)


if __name__ == "__main__":
    main()