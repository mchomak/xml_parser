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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify

# Setup logging first
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

# Import config after logging is set up
from config import (
    UPDATE_INTERVAL,
    EXCHANGE_DIRECTIONS,
    OUTPUT_XML_PATH,
    MAX_RETRIES,
    PARALLEL_WORKERS,
)

# Flask app
app = Flask(__name__)

# Parser state
parser_running = False
last_update = None
update_count = 0
last_error = None
previous_rates = None


def fetch_single_direction(args):
    """Fetch a single direction using a new browser instance."""
    from_currency, to_currency = args
    try:
        from parser_selenium import SeleniumParser
        with SeleniumParser(headless=True) as parser:
            rates = parser.fetch_exchange_rates(from_currency, to_currency)
            return (from_currency, to_currency), rates
    except Exception as e:
        logger.error(f"Error fetching {from_currency} -> {to_currency}: {e}")
        return (from_currency, to_currency), []


def update_rates():
    """Update rates using Selenium with parallel fetching."""
    global last_update, update_count, last_error, previous_rates

    try:
        from xml_generator import generate_xml, aggregate_rates_for_xml
    except ImportError as e:
        logger.error(f"Import error: {e}")
        last_error = str(e)
        return False

    logger.info(f"Starting update ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    logger.info(f"Parallel workers: {PARALLEL_WORKERS}")

    all_rates = {}
    failed_directions = []

    # Prepare arguments for parallel fetching
    fetch_args = [(from_curr, to_curr) for from_curr, to_curr in EXCHANGE_DIRECTIONS]

    try:
        # Fetch in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
            futures = {executor.submit(fetch_single_direction, arg): arg for arg in fetch_args}

            for future in as_completed(futures):
                try:
                    direction, rates = future.result()
                    all_rates[direction] = rates

                    if rates:
                        logger.info(f"OK: {direction[0]} -> {direction[1]}: {len(rates)} exchangers")
                    else:
                        logger.warning(f"EMPTY: {direction[0]} -> {direction[1]}")
                        failed_directions.append(direction)

                except Exception as e:
                    arg = futures[future]
                    direction = (arg[0], arg[1])
                    logger.error(f"ERROR: {direction[0]} -> {direction[1]}: {e}")
                    all_rates[direction] = []
                    failed_directions.append(direction)

        # Use previous rates for failed directions
        if previous_rates and failed_directions:
            for direction in failed_directions:
                if direction in previous_rates and previous_rates[direction]:
                    logger.warning(f"Using previous data for {direction[0]} -> {direction[1]}")
                    all_rates[direction] = previous_rates[direction]

        # Store successful rates for future fallback
        if all_rates:
            previous_rates = all_rates.copy()

        aggregated_rates = aggregate_rates_for_xml(all_rates)

        if aggregated_rates:
            generate_xml(aggregated_rates, OUTPUT_XML_PATH)
            last_update = datetime.now()
            update_count += 1
            last_error = None
            logger.info(f"XML updated: {OUTPUT_XML_PATH}")
            return True
        else:
            last_error = "No rates available"
            logger.error("No rates available")
            return False

    except Exception as e:
        last_error = str(e)
        logger.exception(f"Update failed: {e}")
        return False


def parser_loop():
    """Background parser loop."""
    global parser_running

    parser_running = True
    logger.info("Parser thread started")

    # Initial delay to let the server start properly
    time.sleep(5)

    while parser_running:
        try:
            success = update_rates()
            if success:
                logger.info(f"Update #{update_count} complete. Next in {UPDATE_INTERVAL}s")
            else:
                logger.warning(f"Update failed. Retrying in {UPDATE_INTERVAL}s")
        except Exception as e:
            logger.exception(f"Parser loop error: {e}")

        # Wait for next update
        for _ in range(UPDATE_INTERVAL):
            if not parser_running:
                break
            time.sleep(1)

    logger.info("Parser thread stopped")


# Parser thread reference
parser_thread = None


def start_parser():
    """Start the parser background thread."""
    global parser_thread

    if parser_thread is None or not parser_thread.is_alive():
        parser_thread = threading.Thread(target=parser_loop, daemon=True, name="ParserThread")
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
        try:
            with open(xml_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return Response(content, mimetype='application/xml')
        except Exception as e:
            logger.error(f"Error reading XML: {e}")

    # Return empty XML if file doesn't exist yet
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
        'parser_thread_alive': parser_thread.is_alive() if parser_thread else False,
        'last_update': last_update.isoformat() if last_update else None,
        'last_error': last_error,
        'update_count': update_count,
        'update_interval': UPDATE_INTERVAL,
        'directions': len(EXCHANGE_DIRECTIONS),
        'max_retries': MAX_RETRIES,
        'xml_exists': xml_exists,
        'xml_size_bytes': xml_size,
    })


# Only start parser when running with gunicorn or directly
# Don't start on import
def on_starting(server):
    """Gunicorn hook - called when server starts."""
    start_parser()


# For running directly with python server.py
if __name__ == '__main__':
    start_parser()
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting web server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)