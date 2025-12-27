#!/usr/bin/env python3
"""
Web server for serving exchange rates XML file.
Runs the parser in a background thread and serves the XML via HTTP.
"""

import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify

from config import (
    UPDATE_INTERVAL,
    EXCHANGE_DIRECTIONS,
    OUTPUT_XML_PATH,
    HEADLESS,
    LOG_LEVEL,
    LOG_FILE,
    MAX_RETRIES,
)
from parser import ExchangeRate, fetch_exchange_rates
from xml_generator import generate_xml, aggregate_rates_for_xml


# Setup logging
log_level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)

logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

# Parser state
parser_running = False
last_update: datetime = None
update_count = 0
previous_rates = None


def collect_all_rates(fetch_func):
    """Collect rates for all exchange directions."""
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

    if all_rates:
        previous_rates = all_rates.copy()

    return all_rates


def update_rates_selenium(headless: bool = True):
    """Update rates using Selenium."""
    global last_update, update_count

    try:
        from parser_selenium import SeleniumParser
    except ImportError:
        logger.error("Selenium not installed")
        return

    logger.info(f"Starting update ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")

    with SeleniumParser(headless=headless) as parser:
        all_rates = collect_all_rates(parser.fetch_exchange_rates)
        aggregated_rates = aggregate_rates_for_xml(all_rates)

        if aggregated_rates:
            generate_xml(aggregated_rates, OUTPUT_XML_PATH)
            last_update = datetime.now()
            update_count += 1
            logger.info(f"XML updated: {OUTPUT_XML_PATH}")
        else:
            logger.error("No rates available. XML not updated.")


def parser_loop():
    """Background parser loop."""
    global parser_running

    parser_running = True
    logger.info("Parser thread started")

    # Use headless mode for server deployment
    headless = os.getenv('HEADLESS', 'true').lower() in ('true', '1', 'yes')

    while parser_running:
        try:
            update_rates_selenium(headless=headless)
            logger.info(f"Update #{update_count} complete. Next in {UPDATE_INTERVAL}s")
        except Exception as e:
            logger.exception(f"Parser error: {e}")

        # Wait for next update
        for _ in range(UPDATE_INTERVAL):
            if not parser_running:
                break
            time.sleep(1)

    logger.info("Parser thread stopped")


# Start parser thread
parser_thread = None


def start_parser():
    """Start the parser background thread."""
    global parser_thread

    if parser_thread is None or not parser_thread.is_alive():
        parser_thread = threading.Thread(target=parser_loop, daemon=True)
        parser_thread.start()
        logger.info("Parser thread launched")


@app.route('/')
def index():
    """Serve the XML file at root path."""
    return get_xml()


@app.route('/rates.xml')
def rates_xml():
    """Serve the XML file."""
    return get_xml()


@app.route('/rates')
def rates():
    """Serve the XML file (alias)."""
    return get_xml()


def get_xml():
    """Read and return XML file content."""
    xml_path = Path(OUTPUT_XML_PATH)

    if xml_path.exists():
        with open(xml_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return Response(content, mimetype='application/xml')
    else:
        # Return empty rates XML if file doesn't exist yet
        empty_xml = '<?xml version="1.0" ?>\n<rates generated="" count="0"></rates>'
        return Response(empty_xml, mimetype='application/xml')


@app.route('/health')
def health():
    """Health check endpoint for Render."""
    return jsonify({
        'status': 'healthy',
        'parser_running': parser_running,
        'last_update': last_update.isoformat() if last_update else None,
        'update_count': update_count,
        'directions': len(EXCHANGE_DIRECTIONS),
    })


@app.route('/status')
def status():
    """Detailed status endpoint."""
    xml_path = Path(OUTPUT_XML_PATH)
    xml_exists = xml_path.exists()
    xml_size = xml_path.stat().st_size if xml_exists else 0

    return jsonify({
        'status': 'running',
        'parser_running': parser_running,
        'last_update': last_update.isoformat() if last_update else None,
        'update_count': update_count,
        'update_interval': UPDATE_INTERVAL,
        'directions': len(EXCHANGE_DIRECTIONS),
        'max_retries': MAX_RETRIES,
        'xml_file': OUTPUT_XML_PATH,
        'xml_exists': xml_exists,
        'xml_size_bytes': xml_size,
    })


# Start parser when app starts
start_parser()


if __name__ == '__main__':
    # For local development
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')

    logger.info(f"Starting web server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)