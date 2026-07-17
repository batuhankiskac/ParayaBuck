from __future__ import annotations


def is_crypto(symbol: str) -> bool:
    """Return True if the symbol is a crypto pair (e.g. 'BTC/USD', 'ETH/USD')."""
    return "/" in symbol


__all__ = ["is_crypto"]
