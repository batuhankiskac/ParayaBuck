"""Static equity universe.

The equity strategy reads a fixed signal/hold pair, so the equity universe is
the configured ``universe.equity.symbols``. There is no equity scanner; the
runtime pins managed positions outside this set until they are sold.
"""

from __future__ import annotations

import hashlib
import json

from parayabuck.config import EquityUniverseConfig

STATIC_EQUITY_UNIVERSE_VERSION = "equity-static-v1"


def equity_universe_digest(config: EquityUniverseConfig) -> str:
    """Deterministic identity of the static equity universe policy."""
    payload = {
        "version": STATIC_EQUITY_UNIVERSE_VERSION,
        "symbols": list(config.symbols),
        "maximum_stream_symbols": config.maximum_stream_symbols,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = ["STATIC_EQUITY_UNIVERSE_VERSION", "equity_universe_digest"]
