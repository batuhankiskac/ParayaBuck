"""Static crypto category metadata (22/22 completeness, single source).

Operator-maintained mapping from tradable ``BASE/USD`` symbol to scanner
category. Scanner scoring and portfolio construction rely on this to apply
per-category caps (``maximum_per_category``, ``maximum_meme_symbols``,
``maximum_high_beta_symbols``). The mapping is imported by both
``parayabuck.universe.crypto_scanner`` and ``parayabuck.config`` (validation)
so the vocabulary cannot drift.

Completeness is mandatory: every configured ``universe.crypto.trade_symbols``
must have an explicit category. See ``CryptoUniverseConfig.__post_init__``.
"""

from __future__ import annotations

CRYPTO_CATEGORIES: dict[str, str] = {
    "BTC/USD": "core",
    "ETH/USD": "core",
    "SOL/USD": "layer1",
    "XRP/USD": "layer1",
    "ADA/USD": "layer1",
    "AVAX/USD": "layer1",
    "DOT/USD": "layer1",
    "LINK/USD": "defi",
    "UNI/USD": "defi",
    "AAVE/USD": "defi",
    "ONDO/USD": "defi",
    "CRV/USD": "defi",
    "FIL/USD": "infrastructure",
    "RENDER/USD": "infrastructure",
    "GRT/USD": "infrastructure",
    "LTC/USD": "legacy",
    "BCH/USD": "legacy",
    "ARB/USD": "high_beta",
    "HYPE/USD": "high_beta",
    "DOGE/USD": "meme",
    "SHIB/USD": "meme",
    "PEPE/USD": "meme",
}

__all__ = ["CRYPTO_CATEGORIES"]
