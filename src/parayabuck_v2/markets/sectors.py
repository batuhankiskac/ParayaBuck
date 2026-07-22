from __future__ import annotations

_SECTORS = {
    "AAPL": "technology",
    "MSFT": "technology",
    "GOOGL": "technology",
    "NVDA": "technology",
    "XLK": "technology",
    "AMZN": "consumer",
    "TSLA": "consumer",
    "KO": "consumer",
    "PEP": "consumer",
    "XLY": "consumer",
    "XLP": "consumer",
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


def sector_of(symbol: str) -> str:
    return "crypto" if "/" in symbol else _SECTORS.get(symbol, "other")
