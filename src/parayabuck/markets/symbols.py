from __future__ import annotations

from dataclasses import dataclass

from parayabuck.core.models import Market

_CRYPTO_QUOTES = (
    "USDT",
    "USDC",
    "USD",
    "BTC",
    "ETH",
    "EUR",
    "GBP",
)
_KNOWN_CRYPTO_BASES = {
    "AAVE",
    "ADA",
    "AVAX",
    "BCH",
    "BTC",
    "DOGE",
    "DOT",
    "ETH",
    "LINK",
    "LTC",
    "SHIB",
    "SOL",
    "UNI",
    "XRP",
}

STABLECOIN_BASES = frozenset(
    {
        "USD",
        "USDT",
        "USDC",
        "DAI",
        "FDUSD",
        "TUSD",
        "USDP",
        "PYUSD",
        "GUSD",
        "BUSD",
        "EURC",
    }
)


@dataclass(frozen=True)
class SymbolIdentity:
    canonical: str
    market: Market
    market_data_symbol: str
    trading_symbol: str
    asset_lookup_symbol: str


def normalize_symbol(symbol: str, market: Market | None = None) -> SymbolIdentity:
    value = "".join(symbol.strip().upper().split())
    if not value:
        raise ValueError("Symbol must not be empty")

    inferred_market = market
    if inferred_market is None:
        inferred_market = (
            Market.CRYPTO if "/" in value or _split_legacy_pair(value) else Market.EQUITY
        )

    if inferred_market == Market.EQUITY:
        if "/" in value:
            raise ValueError(f"Equity symbol cannot contain '/': {symbol!r}")
        return SymbolIdentity(value, Market.EQUITY, value, value, value)

    if "/" in value:
        parts = value.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"Invalid crypto pair: {symbol!r}")
        base, quote = parts
    else:
        pair = _split_legacy_pair(value)
        if pair is None:
            raise ValueError(f"Cannot infer crypto base/quote for {symbol!r}; use BASE/QUOTE form")
        base, quote = pair
    canonical = f"{base}/{quote}"
    return SymbolIdentity(
        canonical=canonical,
        market=Market.CRYPTO,
        market_data_symbol=canonical,
        trading_symbol=canonical,
        asset_lookup_symbol=f"{base}{quote}",
    )


def canonical_symbol(symbol: str, market: Market | None = None) -> str:
    return normalize_symbol(symbol, market).canonical


def split_crypto_pair(symbol: str) -> tuple[str, str]:
    canonical = canonical_symbol(symbol, Market.CRYPTO)
    base, quote = canonical.split("/", 1)
    return base, quote


def is_stablecoin_pair(symbol: str) -> bool:
    base, _quote = split_crypto_pair(symbol)
    return base in STABLECOIN_BASES


def same_symbol(left: str, right: str, market: Market | None = None) -> bool:
    try:
        if market is not None:
            return canonical_symbol(left, market) == canonical_symbol(right, market)
        left_identity = normalize_symbol(left)
        right_identity = normalize_symbol(right)
        return (
            left_identity.market == right_identity.market
            and left_identity.canonical == right_identity.canonical
        )
    except ValueError:
        return False


def _split_legacy_pair(value: str) -> tuple[str, str] | None:
    for quote in _CRYPTO_QUOTES:
        base = value[: -len(quote)] if value.endswith(quote) else ""
        if base in _KNOWN_CRYPTO_BASES:
            return base, quote
    return None


__all__ = [
    "STABLECOIN_BASES",
    "SymbolIdentity",
    "canonical_symbol",
    "is_stablecoin_pair",
    "normalize_symbol",
    "same_symbol",
    "split_crypto_pair",
]
