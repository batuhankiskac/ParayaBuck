#!/usr/bin/env python3
"""Run the read-only Alpaca crypto universe scanner."""

from __future__ import annotations

import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.data import AlpacaHistoricalData
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.universe import CryptoUniverseScanner
from parayabuck.universe.crypto_scanner import crypto_candidate_from_bars


def _asset_symbol(asset: Any) -> str | None:
    raw = str(getattr(asset, "symbol", "")).strip().upper()
    if not raw:
        return None
    try:
        return canonical_symbol(raw, Market.CRYPTO)
    except (TypeError, ValueError):
        return f"{raw[:-3]}/USD" if raw.endswith("USD") and len(raw) > 3 else None


def _spread_bps(quote: Any) -> float | None:
    try:
        bid = float(quote.bid_price)
        ask = float(quote.ask_price)
        mid = (bid + ask) / 2
        return ((ask - bid) / mid) * 10_000 if bid >= 0 and ask >= bid and mid > 0 else None
    except (AttributeError, TypeError, ValueError):
        return None


def main() -> int:
    config = load_config()
    crypto = config.universe.crypto
    if not crypto.enabled:
        raise SystemExit("Crypto universe is disabled in config/settings.yaml")
    if not crypto.dynamic_selection_enabled:
        raise SystemExit("universe.crypto.dynamic_selection_enabled is false")
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")

    print(f"Crypto scanner started; candidates={len(crypto.trade_symbols)}")
    trading = TradingClient(key, secret, paper=config.alpaca.paper)
    try:
        assets = trading.get_all_assets(
            GetAssetsRequest(asset_class=AssetClass.CRYPTO, status=AssetStatus.ACTIVE)
        )
    except Exception as exc:
        raise SystemExit(f"Alpaca crypto asset lookup failed: {exc}") from exc
    by_symbol = {
        symbol: asset
        for asset in assets
        if (symbol := _asset_symbol(asset)) is not None and symbol in crypto.trade_symbols
    }

    history = AlpacaHistoricalData(
        key,
        secret,
        stock_feed=config.alpaca.stock_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    end = datetime.now(UTC)
    start = end - timedelta(days=crypto.scanner.lookback_days)
    try:
        bars = history.get_bars_batch(
            list(crypto.trade_symbols),
            crypto.scanner.historical_timeframe,
            start=start,
            end=end,
        )
    except Exception as exc:
        raise SystemExit(
            f"Alpaca crypto historical lookup failed; previous universe preserved: {exc}"
        ) from exc

    quotes: dict[str, Any] = {}
    try:
        quotes = history.get_latest_crypto_quotes(list(crypto.trade_symbols))
    except Exception as exc:
        print(f"Latest quotes unavailable; neutral spread score will be used: {exc}")

    expected = crypto.scanner.lookback_days * 24
    btc_bars = bars.get("BTC/USD", [])
    candidates = []
    for symbol in crypto.trade_symbols:
        asset = by_symbol.get(symbol)
        candidates.append(
            crypto_candidate_from_bars(
                symbol,
                bars.get(symbol, []),
                expected_bar_count=expected,
                tradable=bool(asset is not None and getattr(asset, "tradable", False)),
                status=str(getattr(asset, "status", "inactive")).lower().split(".")[-1],
                btc_bars=btc_bars,
                spread_bps=_spread_bps(quotes.get(symbol)),
            )
        )

    scanner = CryptoUniverseScanner(crypto)
    result = scanner.scan(candidates, data_timestamp=end)
    scanner.persist(result)
    reasons = Counter(result.eliminated.values())
    print(f"Selected ({len(result.selected_symbols)}): {', '.join(result.selected_symbols)}")
    print(
        "Eliminated: "
        + (", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items())) or "none")
    )
    print(f"Persisted: {crypto.active_universe_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
