from __future__ import annotations


def is_crypto(symbol: str) -> bool:
    """Return True for canonical or legacy crypto pair symbols."""
    from parayabuck.core.models import Market
    from parayabuck.markets.symbols import normalize_symbol

    try:
        return normalize_symbol(symbol).market == Market.CRYPTO
    except ValueError:
        return False


__all__ = ["is_crypto"]
