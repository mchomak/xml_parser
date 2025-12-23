"""
Utility functions for the rate exporter.
Includes retry logic, exponential backoff, and logging helpers.
"""

import asyncio
import functools
import logging
import random
import time
from datetime import datetime
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')


def setup_logging(level: str = "INFO", log_format: Optional[str] = None) -> None:
    """Configure application logging."""
    log_format = log_format or "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format=log_format,
        handlers=[
            logging.StreamHandler(),
        ]
    )

    # Reduce noise from third-party libraries
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True
) -> float:
    """
    Calculate exponential backoff delay with optional jitter.

    Args:
        attempt: The current attempt number (0-indexed)
        base_delay: The base delay in seconds
        max_delay: The maximum delay cap
        jitter: Whether to add random jitter

    Returns:
        The calculated delay in seconds
    """
    # Exponential backoff: base_delay * 2^attempt
    delay = min(base_delay * (2 ** attempt), max_delay)

    if jitter:
        # Add up to 25% jitter
        delay = delay * (0.75 + random.random() * 0.5)

    return delay


async def async_retry(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple = (Exception,),
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    **kwargs: Any
) -> T:
    """
    Retry an async function with exponential backoff.

    Args:
        func: The async function to call
        max_retries: Maximum number of retry attempts
        base_delay: Base delay between retries
        max_delay: Maximum delay between retries
        retryable_exceptions: Tuple of exceptions that trigger a retry
        on_retry: Optional callback called on each retry
        *args, **kwargs: Arguments to pass to the function

    Returns:
        The result of the function call

    Raises:
        The last exception if all retries fail
    """
    last_exception: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as e:
            last_exception = e

            if attempt >= max_retries:
                logger.error(f"All {max_retries + 1} attempts failed: {e}")
                raise

            delay = calculate_backoff_delay(attempt, base_delay, max_delay)
            logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay:.2f}s...")

            if on_retry:
                on_retry(attempt, e)

            await asyncio.sleep(delay)

    # Should not reach here, but just in case
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected retry loop exit")


def sync_retry(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple = (Exception,),
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    **kwargs: Any
) -> T:
    """
    Retry a synchronous function with exponential backoff.
    Same interface as async_retry but for sync functions.
    """
    last_exception: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except retryable_exceptions as e:
            last_exception = e

            if attempt >= max_retries:
                logger.error(f"All {max_retries + 1} attempts failed: {e}")
                raise

            delay = calculate_backoff_delay(attempt, base_delay, max_delay)
            logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay:.2f}s...")

            if on_retry:
                on_retry(attempt, e)

            time.sleep(delay)

    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected retry loop exit")


def retry_decorator(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple = (Exception,)
):
    """
    Decorator for adding retry logic to functions.
    Works with both sync and async functions.
    """
    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await async_retry(
                    func, *args, **kwargs,
                    max_retries=max_retries,
                    base_delay=base_delay,
                    max_delay=max_delay,
                    retryable_exceptions=retryable_exceptions
                )
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                return sync_retry(
                    func, *args, **kwargs,
                    max_retries=max_retries,
                    base_delay=base_delay,
                    max_delay=max_delay,
                    retryable_exceptions=retryable_exceptions
                )
            return sync_wrapper
    return decorator


class RateLimiter:
    """Simple rate limiter for API calls."""

    def __init__(self, calls_per_second: float = 10.0):
        self.min_interval = 1.0 / calls_per_second
        self.last_call_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait if necessary to respect rate limit."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_call_time

            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)

            self.last_call_time = time.monotonic()


class MetricsCollector:
    """Simple metrics collector for monitoring."""

    def __init__(self):
        self.fetch_count = 0
        self.fetch_success_count = 0
        self.fetch_error_count = 0
        self.export_count = 0
        self.last_fetch_time: Optional[datetime] = None
        self.last_export_time: Optional[datetime] = None
        self.last_item_count = 0
        self.total_items_exported = 0
        self._lock = asyncio.Lock()

    async def record_fetch(self, success: bool, items: int = 0) -> None:
        """Record a fetch attempt."""
        async with self._lock:
            self.fetch_count += 1
            if success:
                self.fetch_success_count += 1
            else:
                self.fetch_error_count += 1
            self.last_fetch_time = datetime.now()

    async def record_export(self, item_count: int) -> None:
        """Record an export operation."""
        async with self._lock:
            self.export_count += 1
            self.last_export_time = datetime.now()
            self.last_item_count = item_count
            self.total_items_exported += item_count

    def get_stats(self) -> dict[str, Any]:
        """Get current metrics as a dictionary."""
        return {
            "fetch_count": self.fetch_count,
            "fetch_success_count": self.fetch_success_count,
            "fetch_error_count": self.fetch_error_count,
            "export_count": self.export_count,
            "last_fetch_time": self.last_fetch_time.isoformat() if self.last_fetch_time else None,
            "last_export_time": self.last_export_time.isoformat() if self.last_export_time else None,
            "last_item_count": self.last_item_count,
            "total_items_exported": self.total_items_exported,
            "success_rate": (
                self.fetch_success_count / self.fetch_count * 100
                if self.fetch_count > 0 else 0
            ),
        }


def format_timestamp(dt: Optional[datetime] = None) -> str:
    """Format a datetime as an ISO timestamp."""
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def sanitize_string(value: str) -> str:
    """Sanitize a string for safe XML inclusion."""
    if not value:
        return ""
    # Remove or replace problematic characters
    value = str(value).strip()
    # Replace common problematic characters
    replacements = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&apos;',
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def format_decimal(value: Any, precision: int = 8) -> str:
    """Format a number with consistent decimal precision."""
    try:
        if isinstance(value, str):
            value = float(value.replace(',', '.'))
        return f"{float(value):.{precision}f}".rstrip('0').rstrip('.')
    except (ValueError, TypeError):
        return "0"


# Global metrics instance
_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics
