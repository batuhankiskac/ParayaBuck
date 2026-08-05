from __future__ import annotations

from parayabuck.core.models import Market

# Permanent point-in-time universe for equity research: broad and sector SPDR
# ETFs with long histories. Individual stocks are excluded from main research
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

# Cash proxy used when absolute momentum fails everywhere. A full risk-off
# allocation to this symbol is allowed only when the research data provider
# delivers its history; otherwise the residual simply stays in cash.
CASH_PROXY_SYMBOL = "SHY"

EQUITY_UNIVERSE_VERSION = "permanent_etf_core_v1"
CRYPTO_UNIVERSE_VERSION = "crypto_trailing_liquidity_v1"

# Crypto spot pairs actually supported by the execution broker (Alpaca). Only
# these may enter the point-in-time research universe.
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
    "CASH_PROXY_SYMBOL",
    "CRYPTO_UNIVERSE_VERSION",
    "EQUITY_ETF_UNIVERSE",
    "EQUITY_UNIVERSE_VERSION",
    "EXECUTION_SUPPORTED_CRYPTO",
    "equity_universe_for",
]
