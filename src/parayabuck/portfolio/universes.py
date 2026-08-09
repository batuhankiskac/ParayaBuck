from __future__ import annotations

from parayabuck.core.models import Market

# Legacy fixture universe retained only for deterministic accounting tests: broad and sector SPDR
# ETFs with long histories. Individual stocks are excluded from this fixture
# because no point-in-time constituent source exists.
EQUITY_ETF_UNIVERSE: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "IWM",
    "XLB",
    "XLC",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLRE",
    "XLU",
    "XLV",
    "XLY",
)

# Broad-market ETFs that double as sector-ish exposures; used for the sector
# concentration limit.
BROAD_MARKET_ETFS: frozenset[str] = frozenset({"SPY", "QQQ", "IWM"})

EQUITY_UNIVERSE_VERSION = "permanent_etf_core_v1"
CRYPTO_UNIVERSE_VERSION = "crypto_trailing_liquidity_v1"

# Crypto spot pairs actually supported by the execution broker (Alpaca). Only
# these may enter the point-in-time fixture universe.
EXECUTION_SUPPORTED_CRYPTO = (
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "AVAX/USD",
    "LINK/USD",
    "UNI/USD",
    "LTC/USD",
    "BCH/USD",
    "XRP/USD",
    "ADA/USD",
    "DOT/USD",
    "DOGE/USD",
)


def equity_universe_for(market: Market) -> tuple[str, ...]:
    return EQUITY_ETF_UNIVERSE if market == Market.EQUITY else ()


__all__ = [
    "BROAD_MARKET_ETFS",
    "CRYPTO_UNIVERSE_VERSION",
    "EQUITY_ETF_UNIVERSE",
    "EQUITY_UNIVERSE_VERSION",
    "EXECUTION_SUPPORTED_CRYPTO",
]
