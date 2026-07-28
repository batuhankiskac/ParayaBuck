from __future__ import annotations

from dataclasses import dataclass

_ECONOMIC_SECTORS = {
    "AAPL": "technology",
    "MSFT": "technology",
    "GOOGL": "communications",
    "NVDA": "technology",
    "XLK": "technology",
    "AMZN": "consumer_discretionary",
    "TSLA": "consumer_discretionary",
    "KO": "consumer_staples",
    "PEP": "consumer_staples",
    "XLY": "consumer_discretionary",
    "XLP": "consumer_staples",
    "JPM": "financial",
    "XLF": "financial",
    "JNJ": "healthcare",
    "XLV": "healthcare",
    "XOM": "energy",
    "XLE": "energy",
    "XLI": "industrials",
    "XLB": "materials",
    "XLU": "utilities",
    "XLRE": "real_estate",
    "XLC": "communications",
    "SPY": "broad_market",
    "QQQ": "broad_market",
}

_SECTOR_ETFS = {"XLK", "XLY", "XLP", "XLF", "XLV", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"}
_BROAD_MARKET_ETFS = {"SPY", "QQQ"}


@dataclass(frozen=True)
class InstrumentClassification:
    instrument_category: str
    economic_sector: str


def classify_instrument(symbol: str) -> InstrumentClassification:
    from parayabuck.core.models import Market
    from parayabuck.markets.symbols import normalize_symbol

    identity = normalize_symbol(symbol)
    if identity.market == Market.CRYPTO:
        return InstrumentClassification("crypto", "crypto")
    if identity.canonical in _SECTOR_ETFS:
        category = "sector_etf"
    elif identity.canonical in _BROAD_MARKET_ETFS:
        category = "broad_market_etf"
    else:
        category = "equity"
    return InstrumentClassification(
        category,
        _ECONOMIC_SECTORS.get(identity.canonical, "other"),
    )


def sector_of(symbol: str) -> str:
    """Backward-compatible economic-sector lookup used by risk limits."""
    return classify_instrument(symbol).economic_sector


__all__ = ["InstrumentClassification", "classify_instrument", "sector_of"]
