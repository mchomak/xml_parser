"""Tests for the utility functions."""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import (
    calculate_backoff_delay,
    async_retry,
    sync_retry,
    sanitize_string,
    format_decimal,
    RateLimiter,
    MetricsCollector
)


class TestBackoffDelay:
    """Tests for exponential backoff calculation."""

    def test_first_attempt(self):
        """Test delay for first attempt."""
        delay = calculate_backoff_delay(0, base_delay=1.0, jitter=False)
        assert delay == 1.0

    def test_exponential_growth(self):
        """Test exponential growth of delay."""
        delay0 = calculate_backoff_delay(0, base_delay=1.0, jitter=False)
        delay1 = calculate_backoff_delay(1, base_delay=1.0, jitter=False)
        delay2 = calculate_backoff_delay(2, base_delay=1.0, jitter=False)

        assert delay0 == 1.0
        assert delay1 == 2.0
        assert delay2 == 4.0

    def test_max_delay_cap(self):
        """Test that delay is capped at max_delay."""
        delay = calculate_backoff_delay(10, base_delay=1.0, max_delay=30.0, jitter=False)
        assert delay == 30.0

    def test_jitter(self):
        """Test that jitter adds variability."""
        delays = set()
        for _ in range(10):
            delay = calculate_backoff_delay(2, base_delay=1.0, jitter=True)
            delays.add(round(delay, 2))

        # With jitter, we should see variation
        assert len(delays) > 1


class TestSyncRetry:
    """Tests for synchronous retry logic."""

    def test_success_first_try(self):
        """Test function that succeeds on first try."""
        call_count = 0

        def func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = sync_retry(func, max_retries=3)

        assert result == "success"
        assert call_count == 1

    def test_success_after_retries(self):
        """Test function that succeeds after retries."""
        call_count = 0

        def func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Not yet")
            return "success"

        result = sync_retry(
            func,
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(ValueError,)
        )

        assert result == "success"
        assert call_count == 3

    def test_all_retries_fail(self):
        """Test function that fails all retries."""
        call_count = 0

        def func():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")

        with pytest.raises(ValueError):
            sync_retry(
                func,
                max_retries=2,
                base_delay=0.01,
                retryable_exceptions=(ValueError,)
            )

        assert call_count == 3  # Initial + 2 retries

    def test_non_retryable_exception(self):
        """Test that non-retryable exceptions are raised immediately."""
        call_count = 0

        def func():
            nonlocal call_count
            call_count += 1
            raise TypeError("Not retryable")

        with pytest.raises(TypeError):
            sync_retry(
                func,
                max_retries=3,
                retryable_exceptions=(ValueError,)
            )

        assert call_count == 1


class TestAsyncRetry:
    """Tests for asynchronous retry logic."""

    @pytest.mark.asyncio
    async def test_async_success_first_try(self):
        """Test async function that succeeds on first try."""
        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await async_retry(func, max_retries=3)

        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_success_after_retries(self):
        """Test async function that succeeds after retries."""
        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Not yet")
            return "success"

        result = await async_retry(
            func,
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(ValueError,)
        )

        assert result == "success"
        assert call_count == 3


class TestSanitizeString:
    """Tests for string sanitization."""

    def test_no_special_chars(self):
        """Test string without special characters."""
        assert sanitize_string("hello world") == "hello world"

    def test_ampersand(self):
        """Test escaping ampersand."""
        assert sanitize_string("A & B") == "A &amp; B"

    def test_less_than(self):
        """Test escaping less than."""
        assert sanitize_string("A < B") == "A &lt; B"

    def test_greater_than(self):
        """Test escaping greater than."""
        assert sanitize_string("A > B") == "A &gt; B"

    def test_quotes(self):
        """Test escaping quotes."""
        assert sanitize_string('say "hello"') == "say &quot;hello&quot;"
        assert sanitize_string("it's") == "it&apos;s"

    def test_empty_string(self):
        """Test empty string."""
        assert sanitize_string("") == ""

    def test_multiple_special_chars(self):
        """Test multiple special characters."""
        result = sanitize_string('<a href="test">Link & Text</a>')
        assert "&lt;" in result
        assert "&gt;" in result
        assert "&amp;" in result


class TestFormatDecimal:
    """Tests for decimal formatting."""

    def test_integer(self):
        """Test formatting integer values."""
        assert format_decimal(100) == "100"
        assert format_decimal(1000000) == "1000000"

    def test_decimal(self):
        """Test formatting decimal values."""
        assert format_decimal(92.5) == "92.5"
        assert format_decimal(0.001) == "0.001"

    def test_trailing_zeros(self):
        """Test removal of trailing zeros."""
        assert format_decimal(92.500000) == "92.5"
        assert format_decimal(1.0) == "1"

    def test_string_input(self):
        """Test string input."""
        assert format_decimal("92.5") == "92.5"
        assert format_decimal("1,5") == "1.5"  # Comma as decimal separator

    def test_invalid_input(self):
        """Test invalid input."""
        assert format_decimal("invalid") == "0"
        assert format_decimal(None) == "0"


class TestRateLimiter:
    """Tests for rate limiting."""

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Test that rate limiting works."""
        limiter = RateLimiter(calls_per_second=100)

        import time
        start = time.monotonic()

        for _ in range(5):
            await limiter.acquire()

        elapsed = time.monotonic() - start

        # Should take at least 4 * 0.01 = 0.04 seconds for 5 calls at 100/sec
        # But we're lenient here since timing can be imprecise
        assert elapsed >= 0.03


class TestMetricsCollector:
    """Tests for metrics collection."""

    @pytest.mark.asyncio
    async def test_record_fetch(self):
        """Test recording fetch operations."""
        metrics = MetricsCollector()

        await metrics.record_fetch(success=True, items=10)
        await metrics.record_fetch(success=True, items=5)
        await metrics.record_fetch(success=False)

        stats = metrics.get_stats()
        assert stats["fetch_count"] == 3
        assert stats["fetch_success_count"] == 2
        assert stats["fetch_error_count"] == 1

    @pytest.mark.asyncio
    async def test_record_export(self):
        """Test recording export operations."""
        metrics = MetricsCollector()

        await metrics.record_export(100)
        await metrics.record_export(50)

        stats = metrics.get_stats()
        assert stats["export_count"] == 2
        assert stats["last_item_count"] == 50
        assert stats["total_items_exported"] == 150

    @pytest.mark.asyncio
    async def test_success_rate(self):
        """Test success rate calculation."""
        metrics = MetricsCollector()

        for _ in range(8):
            await metrics.record_fetch(success=True)
        for _ in range(2):
            await metrics.record_fetch(success=False)

        stats = metrics.get_stats()
        assert stats["success_rate"] == 80.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
