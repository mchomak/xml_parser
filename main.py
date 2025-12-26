#!/usr/bin/env python3
"""
0@A5@ :C@A>2 :@8?B>20;NB A exnode.ru

0?CA:05B 15A:>=5G=K9 F8:; >1=>2;5=8O :C@A>2 :064K5 N A5:C=4
8 35=5@8@C5B XML D09; A :C@A0<8 B>?-3 :>=:C@5=B>2.

A?>;L7>20=85:
    python main.py                  # 0?CA: A requests+bs4
    python main.py --selenium       # 0?CA: A Selenium (4;O JS-@5=45@8=30)
    python main.py --once           # 4=>:@0B=K9 70?CA:
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

# 0AB@>9:0 ;>38@>20=8O
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('parser.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

# $;03 4;O graceful shutdown
running = True


def signal_handler(signum, frame):
    """1@01>BG8: A83=0;>2 4;O graceful shutdown"""
    global running
    logger.info(">;CG5= A83=0; >AB0=>2:8. 025@H05< @01>BC...")
    running = False


def collect_all_rates(fetch_func: Callable) -> dict[tuple[str, str], list[ExchangeRate]]:
    """
    !>18@05B :C@AK 4;O 2A5E =0?@02;5=89 >1<5=0.

    Args:
        fetch_func: $C=:F8O 4;O ?>;CG5=8O :C@A>2 (requests 8;8 selenium)

    Returns:
        !;>20@L {(from, to): [rates]}
    """
    all_rates = {}

    for from_currency, to_currency in EXCHANGE_DIRECTIONS:
        try:
            rates = fetch_func(from_currency, to_currency)
            all_rates[(from_currency, to_currency)] = rates

            if rates:
                logger.info(
                    f" {from_currency} -> {to_currency}: "
                    f"{len(rates)} >1<5==8:>2, ;CGH89 :C@A: {rates[0].rate:.8f}"
                )
            else:
                logger.warning(f" {from_currency} -> {to_currency}: =5B 40==KE")

        except Exception as e:
            logger.error(f"H81:0 ?@8 ?0@A8=35 {from_currency} -> {to_currency}: {e}")
            all_rates[(from_currency, to_currency)] = []

    return all_rates


def update_rates_requests():
    """1=>28BL :C@AK 8A?>;L7CO requests + BeautifulSoup"""
    logger.info("=" * 60)
    logger.info(f"0G8=05< >1=>2;5=85 :C@A>2 ({datetime.now().isoformat()})")
    logger.info(" 568<: requests + BeautifulSoup")
    logger.info("=" * 60)

    all_rates = collect_all_rates(fetch_exchange_rates)

    # 3@538@C5< :C@AK 4;O XML
    aggregated_rates = aggregate_rates_for_xml(all_rates)

    if aggregated_rates:
        generate_xml(aggregated_rates, OUTPUT_XML_PATH)
        logger.info(f"XML D09; >1=>2;Q=: {OUTPUT_XML_PATH}")
    else:
        logger.error("5 C40;>AL ?>;CG8BL :C@AK. XML D09; =5 >1=>2;Q=.")


def update_rates_selenium():
    """1=>28BL :C@AK 8A?>;L7CO Selenium"""
    try:
        from parser_selenium import SeleniumParser
    except ImportError:
        logger.error("Selenium =5 CAB0=>2;5=. #AB0=>28B5: pip install selenium")
        return

    logger.info("=" * 60)
    logger.info(f"0G8=05< >1=>2;5=85 :C@A>2 ({datetime.now().isoformat()})")
    logger.info(" 568<: Selenium (headless Chrome)")
    logger.info("=" * 60)

    with SeleniumParser(headless=True) as parser:
        all_rates = collect_all_rates(parser.fetch_exchange_rates)

        # 3@538@C5< :C@AK 4;O XML
        aggregated_rates = aggregate_rates_for_xml(all_rates)

        if aggregated_rates:
            generate_xml(aggregated_rates, OUTPUT_XML_PATH)
            logger.info(f"XML D09; >1=>2;Q=: {OUTPUT_XML_PATH}")
        else:
            logger.error("5 C40;>AL ?>;CG8BL :C@AK. XML D09; =5 >1=>2;Q=.")


def run_loop(update_func: Callable, interval: int = UPDATE_INTERVAL):
    """
    0?CA:05B 15A:>=5G=K9 F8:; >1=>2;5=8O.

    Args:
        update_func: $C=:F8O >1=>2;5=8O :C@A>2
        interval: =B5@20; <564C >1=>2;5=8O<8 2 A5:C=40E
    """
    global running

    #  538AB@8@C5< >1@01>BG8:8 A83=0;>2
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info(f"0?CA: ?0@A5@0 :C@A>2 :@8?B>20;NB")
    logger.info(f"=B5@20; >1=>2;5=8O: {interval} A5:C=4")
    logger.info(f"KE>4=>9 D09;: {OUTPUT_XML_PATH}")
    logger.info(f"0?@02;5=89 >1<5=0: {len(EXCHANGE_DIRECTIONS)}")
    logger.info("-" * 60)

    update_count = 0

    while running:
        try:
            update_func()
            update_count += 1
            logger.info(f"1=>2;5=85 #{update_count} 7025@H5=>. !;54CNI55 G5@57 {interval} A5:.")

        except Exception as e:
            logger.exception(f"@8B8G5A:0O >H81:0 ?@8 >1=>2;5=88: {e}")

        # 4Q< A ?@>25@:>9 D;030 >AB0=>2:8
        for _ in range(interval):
            if not running:
                break
            time.sleep(1)

    logger.info(f"0@A5@ >AB0=>2;5=. A53> >1=>2;5=89: {update_count}")


def main():
    parser = argparse.ArgumentParser(
        description="0@A5@ :C@A>2 :@8?B>20;NB A exnode.ru"
    )

    parser.add_argument(
        '--selenium',
        action='store_true',
        help='A?>;L7>20BL Selenium 2<5AB> requests (4;O JS-@5=45@8=30)'
    )

    parser.add_argument(
        '--once',
        action='store_true',
        help='4=>:@0B=K9 70?CA: (157 F8:;0)'
    )

    parser.add_argument(
        '--interval',
        type=int,
        default=UPDATE_INTERVAL,
        help=f'=B5@20; >1=>2;5=8O 2 A5:C=40E (?> C<>;G0=8N: {UPDATE_INTERVAL})'
    )

    parser.add_argument(
        '--output',
        type=str,
        default=OUTPUT_XML_PATH,
        help=f'CBL : 2KE>4=><C XML D09;C (?> C<>;G0=8N: {OUTPUT_XML_PATH})'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help=':;NG8BL >B;04>G=K5 A>>1I5=8O'
    )

    args = parser.parse_args()

    # 0AB@>9:0 C@>2=O ;>38@>20=8O
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # 1=>2;O5< 3;>10;L=K5 =0AB@>9:8
    import config
    config.OUTPUT_XML_PATH = args.output

    # K18@05< DC=:F8N >1=>2;5=8O
    if args.selenium:
        update_func = update_rates_selenium
    else:
        update_func = update_rates_requests

    # 0?CA:05<
    if args.once:
        logger.info("4=>:@0B=K9 70?CA:")
        update_func()
    else:
        run_loop(update_func, args.interval)


if __name__ == "__main__":
    main()
