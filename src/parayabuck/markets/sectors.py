from __future__ import annotations

_SECTORS = {
    "AAPL": "technology",
    "MSFT": "technology",
    "GOOGL": "communications",
    "NVDA": "technology",
    "XLK": "etf_technology",
    "AMZN": "consumer_discretionary",
    "TSLA": "consumer_discretionary",
    "KO": "consumer_staples",
    "PEP": "consumer_staples",
    "XLY": "etf_consumer_discretionary",
    "XLP": "etf_consumer_staples",
    "JPM": "financial",
    "XLF": "etf_financial",
    "JNJ": "healthcare",
    "XLV": "etf_healthcare",
    "XOM": "energy",
    "XLE": "etf_energy",
    "XLI": "etf_industrials",
    "XLB": "etf_materials",
    "XLU": "etf_utilities",
    "XLRE": "etf_real_estate",
    "XLC": "etf_communications",
    "SPY": "broad_market_etf",
    "QQQ": "growth_market_etf",
}


def sector_of(symbol: str) -> str:
    from parayabuck.core.models import Market
    from parayabuck.markets.symbols import normalize_symbol

    identity = normalize_symbol(symbol)
    return (
        "crypto" if identity.market == Market.CRYPTO else _SECTORS.get(identity.canonical, "other")
    )
