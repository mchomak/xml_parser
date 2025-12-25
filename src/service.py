"""
Service runner for the rate exporter.
Handles scheduling, HTTP serving, and graceful shutdown.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime
from typing import Optional

from .config import Config, get_config, set_config, load_config
from .fetcher import ExnodeFetcher, RawRate
from .normalizer import RateNormalizer, NormalizedRate
from .exporter_xml import XMLExporter, generate_sample_xml
from .utils import setup_logging, get_metrics

logger = logging.getLogger(__name__)


class RateExporterService:
    """
    Main service that orchestrates the rate export process.

    Responsibilities:
    - Periodic fetching of rates from exnode.ru
    - Normalization and deduplication
    - XML generation and atomic file writing
    - Optional HTTP serving of the XML
    - Graceful shutdown handling
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.fetcher: Optional[ExnodeFetcher] = None
        self.normalizer = RateNormalizer(self.config)
        self.exporter = XMLExporter(self.config)
        self.metrics = get_metrics()

        self._running = False
        self._shutdown_event = asyncio.Event()
        self._last_rates: list[NormalizedRate] = []
        self._http_server = None
        self._http_runner = None

    async def start(self) -> None:
        """Initialize and start the service."""
        logger.info("Starting Rate Exporter Service")
        logger.info(f"Configured exchangers: {[e.id for e in self.config.exchangers]}")
        logger.info(f"Update interval: {self.config.update_interval_seconds}s")
        logger.info(f"Output path: {self.config.output_path}")

        # Initialize fetcher
        self.fetcher = ExnodeFetcher(self.config)
        await self.fetcher.start()

        self._running = True

        # Start HTTP server if enabled
        if self.config.http_enabled:
            await self._start_http_server()

        logger.info("Service started successfully")

    async def stop(self) -> None:
        """Gracefully stop the service."""
        logger.info("Stopping Rate Exporter Service")
        self._running = False
        self._shutdown_event.set()

        # Stop HTTP server
        if self._http_runner:
            await self._http_runner.cleanup()

        # Close fetcher
        if self.fetcher:
            await self.fetcher.close()

        logger.info("Service stopped")

    async def run_once(self) -> bool:
        """
        Run a single fetch-normalize-export cycle.

        Returns True on success, False on failure.
        """
        cycle_start = datetime.now()
        logger.info("Starting fetch cycle")

        try:
            # Fetch rates from all exchangers
            fetch_results = await self.fetcher.fetch_all_exchangers()

            # Collect all raw rates
            all_raw_rates: list[RawRate] = []
            for result in fetch_results:
                if result.rates:
                    all_raw_rates.extend(result.rates)

            if not all_raw_rates:
                logger.warning("No rates fetched, using cached data if available")
                cached_rates = self.fetcher.get_all_cached_rates()
                if cached_rates:
                    all_raw_rates = cached_rates
                else:
                    logger.error("No rates available")
                    return False

            # Normalize rates
            self.normalizer.reset_deduplication()
            normalized_rates = self.normalizer.normalize_rates(all_raw_rates)

            if not normalized_rates:
                logger.warning("No valid rates after normalization")
                if self._last_rates:
                    logger.info("Using previously cached normalized rates")
                    normalized_rates = self._last_rates
                else:
                    return False

            # Store for potential future use
            self._last_rates = normalized_rates

            # Export to XML
            success = await self.exporter.write_xml_async(normalized_rates)

            cycle_duration = (datetime.now() - cycle_start).total_seconds()
            logger.info(
                f"Cycle completed in {cycle_duration:.2f}s: "
                f"{len(normalized_rates)} rates exported"
            )

            return success

        except Exception as e:
            logger.error(f"Cycle failed: {e}", exc_info=True)
            return False

    async def run_loop(self) -> None:
        """
        Run the main service loop with periodic updates.
        """
        logger.info(f"Starting main loop (interval: {self.config.update_interval_seconds}s)")

        while self._running:
            try:
                # Run a fetch cycle
                await self.run_once()

                # Wait for next interval or shutdown
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.config.update_interval_seconds
                    )
                    # If we get here, shutdown was requested
                    break
                except asyncio.TimeoutError:
                    # Normal timeout, continue to next cycle
                    pass

            except asyncio.CancelledError:
                logger.info("Main loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                # Wait a bit before retrying after error
                await asyncio.sleep(5)

        logger.info("Main loop ended")

    async def _start_http_server(self) -> None:
        """Start the optional HTTP server for serving XML."""
        try:
            from aiohttp import web

            async def handle_xml(request):
                """Handle requests for the XML feed."""
                xml_content = self.exporter.get_last_valid_xml()
                if not xml_content:
                    # Generate minimal response if no data yet
                    xml_content = generate_sample_xml() if not self._last_rates else ''
                    if not xml_content:
                        return web.Response(
                            text='<?xml version="1.0"?><rates/>',
                            content_type='application/xml',
                            status=503
                        )

                return web.Response(
                    text=xml_content,
                    content_type='application/xml',
                    headers={
                        'Cache-Control': 'max-age=30',
                        'X-Generated-At': (
                            self.exporter.get_last_export_time().isoformat()
                            if self.exporter.get_last_export_time()
                            else datetime.now().isoformat()
                        ),
                        'X-Item-Count': str(len(self._last_rates)),
                    }
                )

            async def handle_health(request):
                """Health check endpoint."""
                stats = self.metrics.get_stats()
                return web.json_response({
                    'status': 'healthy' if self._running else 'stopping',
                    'metrics': stats
                })

            async def handle_metrics(request):
                """Metrics endpoint."""
                return web.json_response(self.metrics.get_stats())

            app = web.Application()
            app.router.add_get('/', handle_xml)
            app.router.add_get('/request-exportxml.xml', handle_xml)
            app.router.add_get('/rates.xml', handle_xml)
            app.router.add_get('/health', handle_health)
            app.router.add_get('/metrics', handle_metrics)

            self._http_runner = web.AppRunner(app)
            await self._http_runner.setup()

            site = web.TCPSite(
                self._http_runner,
                self.config.http_host,
                self.config.http_port
            )
            await site.start()

            logger.info(
                f"HTTP server started on {self.config.http_host}:{self.config.http_port}"
            )

        except ImportError:
            logger.warning("aiohttp not installed, HTTP server unavailable")
        except Exception as e:
            logger.error(f"Failed to start HTTP server: {e}")


def setup_signal_handlers(service: RateExporterService) -> None:
    """Set up signal handlers for graceful shutdown."""
    loop = asyncio.get_event_loop()

    def handle_signal(sig):
        logger.info(f"Received signal {sig.name}, initiating shutdown")
        asyncio.create_task(service.stop())

    # Handle SIGINT (Ctrl+C) and SIGTERM
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(sig, lambda s, f, svc=service: asyncio.create_task(svc.stop()))


async def async_main(
    config_file: Optional[str] = None,
    run_once: bool = False
) -> int:
    """
    Async entry point for the service.

    Args:
        config_file: Optional path to config file
        run_once: If True, run only one cycle and exit

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    # Load configuration
    config = load_config(config_file=config_file)
    set_config(config)

    # Setup logging
    setup_logging(config.log_level, config.log_format)

    # Create and start service
    service = RateExporterService(config)

    try:
        await service.start()

        if run_once:
            # Single execution mode
            success = await service.run_once()
            return 0 if success else 1
        else:
            # Setup signal handlers for graceful shutdown
            setup_signal_handlers(service)

            # Run the main loop
            await service.run_loop()
            return 0

    except Exception as e:
        logger.error(f"Service failed: {e}", exc_info=True)
        return 1
    finally:
        await service.stop()


def main() -> int:
    """
    CLI entry point.

    Parses command-line arguments and runs the service.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description='Exnode Rate Exporter - Export exchange rates as BestChange XML'
    )
    parser.add_argument(
        '-c', '--config',
        help='Path to configuration file (YAML or JSON)'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run once and exit (useful for cron)'
    )
    parser.add_argument(
        '--sample',
        action='store_true',
        help='Generate sample XML and exit'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Handle sample generation
    if args.sample:
        print(generate_sample_xml(10))
        return 0

    # Set verbose logging if requested
    if args.verbose:
        import os
        os.environ['LOG_LEVEL'] = 'DEBUG'

    # Run the async main
    try:
        return asyncio.run(async_main(
            config_file=args.config,
            run_once=args.once
        ))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0


if __name__ == '__main__':
    sys.exit(main())
