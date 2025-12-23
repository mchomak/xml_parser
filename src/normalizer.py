"""
Normalizer module for standardizing currency tickers and rate data.
Ensures consistent formatting across all exchange directions.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .config import Config, DefaultFieldsConfig, get_config
from .fetcher import RawRate

logger = logging.getLogger(__name__)


@dataclass
class NormalizedRate:
    """Normalized exchange rate ready for XML export."""
    from_currency: str
    to_currency: str
    in_amount: str
    out_amount: str
    amount: str  # reserve
    min_amount: str
    max_amount: str
    param: str
    exchanger_id: Optional[str] = None
    exchanger_name: Optional[str] = None


# Currency ticker normalization mappings
CURRENCY_ALIASES = {
    # Tether variants
    'USDT': 'USDT',
    'TETHER': 'USDT',
    'USDTTRC': 'USDTTRC20',
    'USDTTRC20': 'USDTTRC20',
    'USDT-TRC20': 'USDTTRC20',
    'USDT_TRC20': 'USDTTRC20',
    'USDT(TRC20)': 'USDTTRC20',
    'USDTERC': 'USDTERC20',
    'USDTERC20': 'USDTERC20',
    'USDT-ERC20': 'USDTERC20',
    'USDT_ERC20': 'USDTERC20',
    'USDT(ERC20)': 'USDTERC20',
    'USDTBEP20': 'USDTBEP20',
    'USDT-BEP20': 'USDTBEP20',
    'USDT_BEP20': 'USDTBEP20',
    'USDT(BEP20)': 'USDTBEP20',
    'USDTSOL': 'USDTSOL',
    'USDT-SOL': 'USDTSOL',

    # Russian bank cards
    'SBER': 'SBERRUB',
    'SBERBANK': 'SBERRUB',
    'SBERRUB': 'SBERRUB',
    'SBER-RUB': 'SBERRUB',
    'SBER_RUB': 'SBERRUB',
    'TINK': 'TCSBRUB',
    'TINKOFF': 'TCSBRUB',
    'TCSB': 'TCSBRUB',
    'TCSBRUB': 'TCSBRUB',
    'TINKOFF-RUB': 'TCSBRUB',
    'ALFA': 'ACRUB',
    'ALFABANK': 'ACRUB',
    'ACRUB': 'ACRUB',
    'ALFA-RUB': 'ACRUB',
    'VTB': 'VTBRUB',
    'VTBRUB': 'VTBRUB',
    'VTB-RUB': 'VTBRUB',
    'RAIFF': 'RFBRUB',
    'RAIFFEISEN': 'RFBRUB',
    'RFBRUB': 'RFBRUB',
    'GAZPROM': 'GPBRUB',
    'GPBRUB': 'GPBRUB',
    'ROSBANK': 'ROSBANKRUB',
    'ROSBANKRUB': 'ROSBANKRUB',
    'OTKRITIE': 'OPNBNKRUB',
    'OPNBNKRUB': 'OPNBNKRUB',
    'MKB': 'MKBRUB',
    'MKBRUB': 'MKBRUB',
    'POST': 'POSTRUB',
    'POSTBANK': 'POSTRUB',
    'POSTRUB': 'POSTRUB',
    'QIWI': 'QWRUB',
    'QWRUB': 'QWRUB',
    'QIWI-RUB': 'QWRUB',
    'YOOMONEY': 'YAMRUB',
    'YAMRUB': 'YAMRUB',
    'YANDEX': 'YAMRUB',

    # Ukrainian banks
    'PRIVAT': 'PUAH',
    'PRIVATBANK': 'PUAH',
    'PUAH': 'PUAH',
    'PRIVAT24': 'PUAH',
    'MONO': 'MONOBUAH',
    'MONOBANK': 'MONOBUAH',
    'MONOBUAH': 'MONOBUAH',

    # Crypto
    'BTC': 'BTC',
    'BITCOIN': 'BTC',
    'ETH': 'ETH',
    'ETHEREUM': 'ETH',
    'LTC': 'LTC',
    'LITECOIN': 'LTC',
    'XRP': 'XRP',
    'RIPPLE': 'XRP',
    'DOGE': 'DOGE',
    'DOGECOIN': 'DOGE',
    'TRX': 'TRX',
    'TRON': 'TRX',
    'SOL': 'SOL',
    'SOLANA': 'SOL',
    'BNB': 'BNB',
    'BINANCECOIN': 'BNB',
    'MATIC': 'MATIC',
    'POLYGON': 'MATIC',
    'TON': 'TON',
    'TONCOIN': 'TON',
    'NOT': 'NOT',
    'NOTCOIN': 'NOT',

    # Stablecoins
    'USDC': 'USDC',
    'USDCOIN': 'USDC',
    'BUSD': 'BUSD',
    'DAI': 'DAI',
    'TUSD': 'TUSD',
    'USDP': 'USDP',

    # Fiat
    'RUB': 'RUB',
    'RUR': 'RUB',
    'USD': 'USD',
    'EUR': 'EUR',
    'UAH': 'UAH',
    'KZT': 'KZT',
    'GEL': 'GEL',
    'TRY': 'TRY',
    'AZN': 'AZN',
    'BYN': 'BYN',
    'AMD': 'AMD',
    'UZS': 'UZS',

    # Payment systems
    'PAYPAL': 'PPUSD',
    'PPUSD': 'PPUSD',
    'PAYEER': 'PRUSD',
    'PRUSD': 'PRUSD',
    'PRRUB': 'PRRUB',
    'ADVCASH': 'ADVCUSD',
    'ADVCUSD': 'ADVCUSD',
    'ADVCRUB': 'ADVCRUB',
    'PERFECT': 'PMUSD',
    'PERFECTMONEY': 'PMUSD',
    'PMUSD': 'PMUSD',
    'PMEUR': 'PMEUR',
    'SKRILL': 'SKRUSD',
    'SKRUSD': 'SKRUSD',
    'NETELLER': 'NTUSD',
    'NTUSD': 'NTUSD',
    'WEBMONEY': 'WMZ',
    'WMZ': 'WMZ',
    'WMR': 'WMR',
    'WISE': 'WISEUSD',
    'WISEUSD': 'WISEUSD',
    'WISEEUR': 'WISEEUR',
    'REVOLUT': 'RVLTUSD',
    'RVLTUSD': 'RVLTUSD',

    # Cash
    'CASHRUB': 'CASHRUB',
    'CASHUSD': 'CASHUSD',
    'CASHEUR': 'CASHEUR',
    'CASH-RUB': 'CASHRUB',
    'CASH-USD': 'CASHUSD',
}

# Network suffixes that should be preserved
NETWORK_SUFFIXES = [
    'TRC20', 'ERC20', 'BEP20', 'SOL', 'POLYGON', 'ARBITRUM', 'OPTIMISM',
    'AVAX', 'FTM', 'MATIC', 'BSC', 'BASE', 'TON', 'TRON'
]


class CurrencyNormalizer:
    """Normalizes currency tickers to standard BestChange format."""

    def __init__(self, custom_aliases: Optional[dict[str, str]] = None):
        self.aliases = CURRENCY_ALIASES.copy()
        if custom_aliases:
            self.aliases.update(custom_aliases)

    def normalize(self, ticker: str) -> str:
        """
        Normalize a currency ticker to standard format.

        Examples:
            'usdt-trc20' -> 'USDTTRC20'
            'Sberbank RUB' -> 'SBERRUB'
            'tether trc-20' -> 'USDTTRC20'
        """
        if not ticker:
            return ''

        # Clean and uppercase
        original = ticker
        ticker = ticker.strip().upper()

        # Remove common separators and spaces
        ticker = re.sub(r'[\s\-_./()]+', '', ticker)

        # Direct alias lookup
        if ticker in self.aliases:
            return self.aliases[ticker]

        # Try with common variations
        for separator in ['', '-', '_']:
            test_key = ticker.replace(separator, '')
            if test_key in self.aliases:
                return self.aliases[test_key]

        # Handle "CURRENCY NETWORK" format (e.g., "USDT TRC20")
        for network in NETWORK_SUFFIXES:
            if ticker.endswith(network):
                base = ticker[:-len(network)]
                combined = f"{base}{network}"
                if combined in self.aliases:
                    return self.aliases[combined]
                # Return normalized version
                return combined

        # Handle currency + fiat combinations (e.g., "SBER RUB" -> "SBERRUB")
        for fiat in ['RUB', 'USD', 'EUR', 'UAH', 'KZT']:
            if ticker.endswith(fiat) and len(ticker) > len(fiat):
                base = ticker[:-len(fiat)]
                combined = f"{base}{fiat}"
                if combined in self.aliases:
                    return self.aliases[combined]

        logger.debug(f"Unknown currency ticker: {original} (cleaned: {ticker})")
        return ticker

    def add_alias(self, alias: str, canonical: str) -> None:
        """Add a custom currency alias."""
        self.aliases[alias.upper()] = canonical.upper()


class RateNormalizer:
    """Normalizes raw rates into standardized format for XML export."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.currency_normalizer = CurrencyNormalizer()
        self._seen_pairs: set[tuple[str, str, str]] = set()  # (from, to, exchanger)

    def normalize_rate(self, raw: RawRate) -> Optional[NormalizedRate]:
        """
        Normalize a single raw rate.

        Returns None if the rate is invalid or a duplicate.
        """
        # Normalize currencies
        from_curr = self.currency_normalizer.normalize(raw.from_currency)
        to_curr = self.currency_normalizer.normalize(raw.to_currency)

        if not from_curr or not to_curr:
            logger.debug(f"Invalid currencies: {raw.from_currency} -> {raw.to_currency}")
            return None

        if from_curr == to_curr:
            logger.debug(f"Same currency: {from_curr}")
            return None

        # Parse and validate amounts
        in_amount = self._parse_amount(raw.in_amount)
        out_amount = self._parse_amount(raw.out_amount)

        if in_amount <= 0:
            in_amount = 1.0

        if out_amount <= 0:
            logger.debug(f"Invalid out_amount: {raw.out_amount}")
            return None

        # Normalize to in_amount = 1
        if in_amount != 1.0:
            out_amount = out_amount / in_amount
            in_amount = 1.0

        # Get optional fields with defaults
        defaults = self.config.defaults

        reserve = self._parse_amount(raw.reserve)
        amount = str(reserve) if reserve is not None else defaults.amount

        min_amount = raw.min_amount
        if min_amount is None:
            min_amount = defaults.min_amount
        else:
            parsed_min = self._parse_amount(min_amount)
            min_amount = str(parsed_min) if parsed_min is not None else defaults.min_amount

        max_amount = raw.max_amount
        if max_amount is None:
            max_amount = defaults.max_amount
        else:
            parsed_max = self._parse_amount(max_amount)
            max_amount = str(parsed_max) if parsed_max is not None else defaults.max_amount

        param = raw.param if raw.param else defaults.param

        return NormalizedRate(
            from_currency=from_curr,
            to_currency=to_curr,
            in_amount=self._format_amount(in_amount),
            out_amount=self._format_amount(out_amount),
            amount=amount,
            min_amount=min_amount,
            max_amount=max_amount,
            param=param,
            exchanger_id=raw.exchanger_id,
            exchanger_name=raw.exchanger_name
        )

    def normalize_rates(
        self,
        raw_rates: list[RawRate],
        deduplicate: bool = True
    ) -> list[NormalizedRate]:
        """
        Normalize a list of raw rates.

        If deduplicate is True, removes duplicate direction pairs.
        """
        normalized = []
        seen = set() if deduplicate else None

        for raw in raw_rates:
            try:
                rate = self.normalize_rate(raw)
                if rate is None:
                    continue

                if deduplicate:
                    key = (rate.from_currency, rate.to_currency, rate.exchanger_id or '')
                    if key in seen:
                        logger.debug(f"Skipping duplicate: {key}")
                        continue
                    seen.add(key)

                normalized.append(rate)
            except Exception as e:
                logger.warning(f"Error normalizing rate: {e}")

        logger.info(f"Normalized {len(normalized)} rates from {len(raw_rates)} raw rates")
        return normalized

    def _parse_amount(self, value: Optional[str]) -> Optional[float]:
        """Parse an amount string to float."""
        if value is None:
            return None

        try:
            # Handle various formats
            cleaned = str(value).strip()
            cleaned = cleaned.replace(',', '.').replace(' ', '')
            cleaned = re.sub(r'[^\d.\-]', '', cleaned)

            if not cleaned or cleaned == '-':
                return None

            return float(cleaned)
        except (ValueError, TypeError):
            return None

    def _format_amount(self, value: float, max_decimals: int = 8) -> str:
        """Format an amount with appropriate precision."""
        if value == 0:
            return "0"

        # Use appropriate precision
        if value >= 1:
            # For larger values, fewer decimals
            if value >= 1000:
                formatted = f"{value:.2f}"
            elif value >= 1:
                formatted = f"{value:.6f}"
            else:
                formatted = f"{value:.8f}"
        else:
            # For small values, more precision
            formatted = f"{value:.8f}"

        # Remove trailing zeros
        if '.' in formatted:
            formatted = formatted.rstrip('0').rstrip('.')

        return formatted

    def reset_deduplication(self) -> None:
        """Reset the deduplication cache."""
        self._seen_pairs.clear()
