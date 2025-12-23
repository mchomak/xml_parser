"""Tests for the normalizer module."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.normalizer import CurrencyNormalizer, RateNormalizer, NormalizedRate
from src.fetcher import RawRate
from src.config import Config, DefaultFieldsConfig


class TestCurrencyNormalizer:
    """Tests for currency ticker normalization."""

    def setup_method(self):
        self.normalizer = CurrencyNormalizer()

    def test_basic_crypto(self):
        """Test normalization of basic crypto tickers."""
        assert self.normalizer.normalize('BTC') == 'BTC'
        assert self.normalizer.normalize('btc') == 'BTC'
        assert self.normalizer.normalize('Bitcoin') == 'BTC'
        assert self.normalizer.normalize('ETH') == 'ETH'
        assert self.normalizer.normalize('ethereum') == 'ETH'

    def test_usdt_variants(self):
        """Test normalization of USDT variants."""
        assert self.normalizer.normalize('USDTTRC20') == 'USDTTRC20'
        assert self.normalizer.normalize('usdt-trc20') == 'USDTTRC20'
        assert self.normalizer.normalize('USDT_TRC20') == 'USDTTRC20'
        assert self.normalizer.normalize('USDT(TRC20)') == 'USDTTRC20'
        assert self.normalizer.normalize('USDTERC20') == 'USDTERC20'
        assert self.normalizer.normalize('USDT-ERC20') == 'USDTERC20'

    def test_russian_banks(self):
        """Test normalization of Russian bank tickers."""
        assert self.normalizer.normalize('SBERRUB') == 'SBERRUB'
        assert self.normalizer.normalize('Sberbank') == 'SBERRUB'
        assert self.normalizer.normalize('SBER') == 'SBERRUB'
        assert self.normalizer.normalize('TCSBRUB') == 'TCSBRUB'
        assert self.normalizer.normalize('Tinkoff') == 'TCSBRUB'
        assert self.normalizer.normalize('ALFA') == 'ACRUB'
        assert self.normalizer.normalize('VTB') == 'VTBRUB'

    def test_payment_systems(self):
        """Test normalization of payment system tickers."""
        assert self.normalizer.normalize('QIWI') == 'QWRUB'
        assert self.normalizer.normalize('YOOMONEY') == 'YAMRUB'
        assert self.normalizer.normalize('PayPal') == 'PPUSD'
        assert self.normalizer.normalize('PAYEER') == 'PRUSD'

    def test_fiat_currencies(self):
        """Test normalization of fiat currency tickers."""
        assert self.normalizer.normalize('RUB') == 'RUB'
        assert self.normalizer.normalize('RUR') == 'RUB'
        assert self.normalizer.normalize('USD') == 'USD'
        assert self.normalizer.normalize('EUR') == 'EUR'

    def test_unknown_currency(self):
        """Test handling of unknown currency tickers."""
        # Unknown currencies should be returned as-is but uppercased
        assert self.normalizer.normalize('UNKNOWN123') == 'UNKNOWN123'
        assert self.normalizer.normalize('custom-token') == 'CUSTOMTOKEN'

    def test_empty_input(self):
        """Test handling of empty input."""
        assert self.normalizer.normalize('') == ''
        assert self.normalizer.normalize('  ') == ''

    def test_custom_alias(self):
        """Test adding custom aliases."""
        self.normalizer.add_alias('MYCOIN', 'CUSTOMCOIN')
        assert self.normalizer.normalize('mycoin') == 'CUSTOMCOIN'


class TestRateNormalizer:
    """Tests for rate normalization."""

    def setup_method(self):
        config = Config(
            defaults=DefaultFieldsConfig(
                amount="0",
                min_amount="0",
                max_amount="999999999",
                param="0",
                in_amount="1"
            )
        )
        self.normalizer = RateNormalizer(config)

    def test_basic_normalization(self):
        """Test basic rate normalization."""
        raw = RawRate(
            exchanger_id="test",
            exchanger_name="Test Exchanger",
            from_currency="USDT-TRC20",
            to_currency="Sberbank",
            in_amount="1",
            out_amount="92.5",
            reserve="1000000",
            min_amount="100",
            max_amount="50000",
            param="0"
        )

        result = self.normalizer.normalize_rate(raw)

        assert result is not None
        assert result.from_currency == "USDTTRC20"
        assert result.to_currency == "SBERRUB"
        assert result.in_amount == "1"
        assert result.out_amount == "92.5"
        assert result.amount == "1000000.0"
        assert result.min_amount == "100.0"
        assert result.max_amount == "50000.0"

    def test_normalization_with_scaling(self):
        """Test rate normalization when in_amount != 1."""
        raw = RawRate(
            exchanger_id="test",
            exchanger_name="Test",
            from_currency="BTC",
            to_currency="RUB",
            in_amount="0.001",
            out_amount="8500",
            reserve="1000000"
        )

        result = self.normalizer.normalize_rate(raw)

        assert result is not None
        assert result.in_amount == "1"
        # 8500 / 0.001 = 8500000
        assert float(result.out_amount) == 8500000.0

    def test_invalid_rate_rejection(self):
        """Test that invalid rates are rejected."""
        # Same currency
        raw = RawRate(
            exchanger_id="test",
            exchanger_name="Test",
            from_currency="BTC",
            to_currency="BTC",
            in_amount="1",
            out_amount="1"
        )
        assert self.normalizer.normalize_rate(raw) is None

        # Zero out_amount
        raw = RawRate(
            exchanger_id="test",
            exchanger_name="Test",
            from_currency="BTC",
            to_currency="RUB",
            in_amount="1",
            out_amount="0"
        )
        assert self.normalizer.normalize_rate(raw) is None

    def test_deduplication(self):
        """Test rate deduplication."""
        raw_rates = [
            RawRate(
                exchanger_id="test",
                exchanger_name="Test",
                from_currency="USDT",
                to_currency="RUB",
                in_amount="1",
                out_amount="92.5"
            ),
            RawRate(
                exchanger_id="test",
                exchanger_name="Test",
                from_currency="USDT",
                to_currency="RUB",
                in_amount="1",
                out_amount="92.6"  # Duplicate direction
            ),
        ]

        results = self.normalizer.normalize_rates(raw_rates, deduplicate=True)
        assert len(results) == 1

        # Without deduplication
        self.normalizer.reset_deduplication()
        results = self.normalizer.normalize_rates(raw_rates, deduplicate=False)
        assert len(results) == 2

    def test_default_values(self):
        """Test that defaults are applied for missing fields."""
        raw = RawRate(
            exchanger_id="test",
            exchanger_name="Test",
            from_currency="BTC",
            to_currency="RUB",
            in_amount="1",
            out_amount="8500000"
            # No reserve, min_amount, max_amount, param
        )

        result = self.normalizer.normalize_rate(raw)

        assert result is not None
        assert result.amount == "0"  # Default
        assert result.min_amount == "0"  # Default
        assert result.max_amount == "999999999"  # Default
        assert result.param == "0"  # Default


class TestAmountFormatting:
    """Tests for amount formatting."""

    def setup_method(self):
        self.normalizer = RateNormalizer()

    def test_large_numbers(self):
        """Test formatting of large numbers."""
        assert self.normalizer._format_amount(1000000.0) == "1000000"
        assert self.normalizer._format_amount(8500000.12) == "8500000.12"

    def test_small_numbers(self):
        """Test formatting of small numbers."""
        assert self.normalizer._format_amount(0.00001) == "0.00001"
        assert self.normalizer._format_amount(0.00000001) == "0.00000001"

    def test_trailing_zeros(self):
        """Test that trailing zeros are removed."""
        assert self.normalizer._format_amount(92.50000) == "92.5"
        assert self.normalizer._format_amount(1.0) == "1"
        assert self.normalizer._format_amount(100.00) == "100"

    def test_zero(self):
        """Test formatting of zero."""
        assert self.normalizer._format_amount(0) == "0"
        assert self.normalizer._format_amount(0.0) == "0"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
