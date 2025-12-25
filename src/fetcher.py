"""
Asynchronous data fetcher for exnode.ru exchangers.
Supports both JSON API endpoints and HTML parsing fallback.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import aiohttp
from aiohttp import ClientError, ClientTimeout

from .config import Config, ExchangerConfig, get_config
from .utils import async_retry, get_metrics, RateLimiter

logger = logging.getLogger(__name__)


@dataclass
class RawRate:
    """Raw rate data as fetched from the source."""
    exchanger_id: str
    exchanger_name: str
    from_currency: str
    to_currency: str
    in_amount: str
    out_amount: str
    reserve: Optional[str] = None
    min_amount: Optional[str] = None
    max_amount: Optional[str] = None
    param: Optional[str] = None
    fetched_at: datetime = field(default_factory=datetime.now)
    source_url: Optional[str] = None


@dataclass
class FetchResult:
    """Result of a fetch operation."""
    exchanger_id: str
    success: bool
    rates: list[RawRate] = field(default_factory=list)
    error: Optional[str] = None
    fetch_time: datetime = field(default_factory=datetime.now)
    source_url: Optional[str] = None


class ExnodeFetcher:
    """
    Fetches exchange rates from exnode.ru for configured exchangers.

    Supports multiple data sources:
    1. JSON API endpoints (preferred)
    2. XHR/AJAX endpoints
    3. HTML page parsing (fallback)
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limiter = RateLimiter(calls_per_second=5.0)
        self.metrics = get_metrics()
        self._last_successful_results: dict[str, list[RawRate]] = {}

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self) -> None:
        """Initialize the HTTP session."""
        if self.session is None or self.session.closed:
            timeout = ClientTimeout(total=self.config.network.timeout_seconds)
            headers = {
                'User-Agent': self.config.network.user_agent,
                'Accept': 'application/json, text/html, */*',
                'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
            }
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers=headers
            )
            logger.debug("HTTP session initialized")

    async def close(self) -> None:
        """Close the HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.debug("HTTP session closed")

    def _build_url(self, endpoint: str, exchanger: ExchangerConfig) -> str:
        """Build the full URL for an API endpoint."""
        if exchanger.url:
            return exchanger.url

        base = self.config.exnode_base_url.rstrip('/')
        path = endpoint.format(exchanger_id=exchanger.id)
        return f"{base}{path}"

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        """Fetch JSON data from a URL with retry logic."""
        await self.rate_limiter.acquire()

        async def do_fetch():
            async with self.session.get(url) as response:
                response.raise_for_status()
                return await response.json()

        return await async_retry(
            do_fetch,
            max_retries=self.config.network.max_retries,
            base_delay=self.config.network.retry_base_delay,
            max_delay=self.config.network.retry_max_delay,
            retryable_exceptions=(ClientError, asyncio.TimeoutError, Exception)
        )

    async def _fetch_html(self, url: str) -> str:
        """Fetch HTML content from a URL with retry logic."""
        await self.rate_limiter.acquire()

        async def do_fetch():
            async with self.session.get(url) as response:
                response.raise_for_status()
                return await response.text()

        return await async_retry(
            do_fetch,
            max_retries=self.config.network.max_retries,
            base_delay=self.config.network.retry_base_delay,
            max_delay=self.config.network.retry_max_delay,
            retryable_exceptions=(ClientError, asyncio.TimeoutError, Exception)
        )

    def _parse_json_rates(
        self,
        data: dict[str, Any],
        exchanger: ExchangerConfig,
        source_url: str
    ) -> list[RawRate]:
        """
        Parse rates from JSON API response.

        Expected JSON structure (common patterns):
        {
            "rates": [
                {
                    "from": "USDTTRC20",
                    "to": "SBERRUB",
                    "in": "1",
                    "out": "92.5",
                    "reserve": "1000000",
                    "min": "100",
                    "max": "50000"
                },
                ...
            ]
        }

        Or flat array:
        [
            {"from_currency": "...", "to_currency": "...", "rate": "..."},
            ...
        ]
        """
        rates = []

        # Try various common JSON structures
        items = []

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Try common keys for rate arrays
            for key in ['rates', 'data', 'items', 'directions', 'exchanges', 'result']:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break
            # If still empty but has exchanger data, try nested structure
            if not items and 'exchangers' in data:
                for exc in data.get('exchangers', []):
                    if exc.get('id') == exchanger.id or exc.get('name') == exchanger.name:
                        items = exc.get('rates', exc.get('directions', []))
                        break

        for item in items:
            if not isinstance(item, dict):
                continue

            # Extract currency pair - try various field names
            from_curr = (
                item.get('from') or
                item.get('from_currency') or
                item.get('give') or
                item.get('send') or
                item.get('source') or
                item.get('currency_from') or
                ''
            )
            to_curr = (
                item.get('to') or
                item.get('to_currency') or
                item.get('get') or
                item.get('receive') or
                item.get('target') or
                item.get('currency_to') or
                ''
            )

            if not from_curr or not to_curr:
                continue

            # Extract amounts/rates
            in_amount = str(item.get('in', item.get('in_amount', '1')))
            out_amount = str(
                item.get('out') or
                item.get('out_amount') or
                item.get('rate') or
                item.get('exchange_rate') or
                '0'
            )

            # Extract optional fields
            reserve = item.get('reserve', item.get('amount', item.get('available')))
            min_amount = item.get('min', item.get('min_amount', item.get('minamount')))
            max_amount = item.get('max', item.get('max_amount', item.get('maxamount')))
            param = item.get('param', item.get('params', item.get('flags')))

            rates.append(RawRate(
                exchanger_id=exchanger.id,
                exchanger_name=exchanger.name,
                from_currency=str(from_curr),
                to_currency=str(to_curr),
                in_amount=in_amount,
                out_amount=out_amount,
                reserve=str(reserve) if reserve else None,
                min_amount=str(min_amount) if min_amount else None,
                max_amount=str(max_amount) if max_amount else None,
                param=str(param) if param else None,
                source_url=source_url
            ))

        return rates

    def _parse_html_rates(
        self,
        html: str,
        exchanger: ExchangerConfig,
        source_url: str
    ) -> list[RawRate]:
        """
        Parse rates from HTML page content.

        This is a fallback when JSON API is not available.
        Looks for common patterns in exchange rate tables.
        """
        rates = []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            # Look for rate tables
            tables = soup.find_all('table', class_=re.compile(r'rate|exchange|direction', re.I))
            if not tables:
                tables = soup.find_all('table')

            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 4:
                        # Try to extract currency pair and rate
                        text_content = [cell.get_text(strip=True) for cell in cells]

                        # Common patterns: From | To | Rate | Reserve
                        from_curr = text_content[0] if text_content else ''
                        to_curr = text_content[1] if len(text_content) > 1 else ''
                        rate = text_content[2] if len(text_content) > 2 else ''
                        reserve = text_content[3] if len(text_content) > 3 else None

                        if from_curr and to_curr and rate:
                            rates.append(RawRate(
                                exchanger_id=exchanger.id,
                                exchanger_name=exchanger.name,
                                from_currency=from_curr,
                                to_currency=to_curr,
                                in_amount='1',
                                out_amount=rate,
                                reserve=reserve,
                                source_url=source_url
                            ))

            # Also look for data in JavaScript/JSON embedded in the page
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    # Look for JSON data in script tags
                    json_match = re.search(
                        r'(?:rates|directions|data)\s*[=:]\s*(\[[^\]]+\]|\{[^}]+\})',
                        script.string,
                        re.DOTALL
                    )
                    if json_match:
                        try:
                            import json
                            data = json.loads(json_match.group(1))
                            rates.extend(self._parse_json_rates(data, exchanger, source_url))
                        except (json.JSONDecodeError, Exception):
                            pass

        except ImportError:
            logger.warning("BeautifulSoup not installed, HTML parsing unavailable")
        except Exception as e:
            logger.warning(f"HTML parsing failed: {e}")

        return rates

    async def fetch_exchanger_rates(
        self,
        exchanger: ExchangerConfig
    ) -> FetchResult:
        """
        Fetch all rates for a single exchanger.

        Tries JSON API first, falls back to HTML parsing if needed.
        """
        logger.info(f"Fetching rates for exchanger: {exchanger.name} ({exchanger.id})")

        if not exchanger.enabled:
            logger.debug(f"Exchanger {exchanger.id} is disabled, skipping")
            return FetchResult(
                exchanger_id=exchanger.id,
                success=True,
                rates=[]
            )

        # Try JSON API endpoint first
        api_url = self._build_url(self.config.exnode_api_endpoint, exchanger)

        try:
            data = await self._fetch_json(api_url)
            rates = self._parse_json_rates(data, exchanger, api_url)

            if rates:
                logger.info(f"Fetched {len(rates)} rates for {exchanger.name} via JSON API")
                self._last_successful_results[exchanger.id] = rates
                await self.metrics.record_fetch(success=True, items=len(rates))
                return FetchResult(
                    exchanger_id=exchanger.id,
                    success=True,
                    rates=rates,
                    source_url=api_url
                )
        except Exception as e:
            logger.debug(f"JSON API failed for {exchanger.id}: {e}")

        # Try directions endpoint
        directions_url = self._build_url(self.config.exnode_directions_endpoint, exchanger)
        try:
            data = await self._fetch_json(directions_url)
            rates = self._parse_json_rates(data, exchanger, directions_url)

            if rates:
                logger.info(f"Fetched {len(rates)} rates for {exchanger.name} via directions API")
                self._last_successful_results[exchanger.id] = rates
                await self.metrics.record_fetch(success=True, items=len(rates))
                return FetchResult(
                    exchanger_id=exchanger.id,
                    success=True,
                    rates=rates,
                    source_url=directions_url
                )
        except Exception as e:
            logger.debug(f"Directions API failed for {exchanger.id}: {e}")

        # Try HTML fallback
        page_url = exchanger.url or f"{self.config.exnode_base_url}/exchanger/{exchanger.id}"
        try:
            html = await self._fetch_html(page_url)
            rates = self._parse_html_rates(html, exchanger, page_url)

            if rates:
                logger.info(f"Fetched {len(rates)} rates for {exchanger.name} via HTML parsing")
                self._last_successful_results[exchanger.id] = rates
                await self.metrics.record_fetch(success=True, items=len(rates))
                return FetchResult(
                    exchanger_id=exchanger.id,
                    success=True,
                    rates=rates,
                    source_url=page_url
                )
        except Exception as e:
            logger.warning(f"HTML fallback failed for {exchanger.id}: {e}")

        # All methods failed - return last known good data if available
        await self.metrics.record_fetch(success=False)

        if exchanger.id in self._last_successful_results:
            cached_rates = self._last_successful_results[exchanger.id]
            logger.warning(
                f"Using cached data for {exchanger.name} ({len(cached_rates)} rates)"
            )
            return FetchResult(
                exchanger_id=exchanger.id,
                success=False,
                rates=cached_rates,
                error="Fetch failed, using cached data"
            )

        return FetchResult(
            exchanger_id=exchanger.id,
            success=False,
            error="All fetch methods failed and no cached data available"
        )

    async def fetch_all_exchangers(self) -> list[FetchResult]:
        """
        Fetch rates for all configured exchangers concurrently.

        Returns results for all exchangers, even if some fail.
        """
        logger.info(f"Fetching rates for {len(self.config.exchangers)} exchangers")

        tasks = [
            self.fetch_exchanger_rates(exchanger)
            for exchanger in self.config.exchangers
            if exchanger.enabled
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any exceptions that weren't caught
        fetch_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                exchanger = self.config.exchangers[i]
                logger.error(f"Unexpected error fetching {exchanger.id}: {result}")
                fetch_results.append(FetchResult(
                    exchanger_id=exchanger.id,
                    success=False,
                    error=str(result)
                ))
            else:
                fetch_results.append(result)

        total_rates = sum(len(r.rates) for r in fetch_results)
        successful = sum(1 for r in fetch_results if r.success)
        logger.info(
            f"Fetch complete: {successful}/{len(fetch_results)} exchangers successful, "
            f"{total_rates} total rates"
        )

        return fetch_results

    def get_all_cached_rates(self) -> list[RawRate]:
        """Get all cached rates from the last successful fetch."""
        all_rates = []
        for rates in self._last_successful_results.values():
            all_rates.extend(rates)
        return all_rates


async def create_fetcher(config: Optional[Config] = None) -> ExnodeFetcher:
    """Create and initialize a new fetcher instance."""
    fetcher = ExnodeFetcher(config)
    await fetcher.start()
    return fetcher
