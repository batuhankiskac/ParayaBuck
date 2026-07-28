#!/usr/bin/env python3
"""Pre-session Alpaca equity universe scan.

This command is read-only at Alpaca. It writes repository-local candidate and
active-universe JSON files with atomic replace and never changes a running
stream subscription.
"""

from __future__ import annotations

import json
import os
import statistics
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from parayabuck.config import load_config
from parayabuck.data import AlpacaHistoricalData
from parayabuck.universe import EquityCandidate, EquityUniverseScanner


def _chunks(values: list[str], size: int = 8) -> list[list[str]]:
    return [values[offset : offset + size] for offset in range(0, len(values), size)]


def _candidate(symbol: str, asset: Any, bars: list[Any]) -> EquityCandidate:
    recent = bars[-20:]
    if not recent:
        return EquityCandidate(symbol, 0, 0, 0, 0)
    dollar_volumes = [bar.close * bar.volume for bar in recent]
    true_ranges: list[float] = []
    for previous, current in zip(recent, recent[1:], strict=False):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    price = recent[-1].close
    name = str(getattr(asset, "name", "")).lower()
    leveraged = any(
        marker in name
        for marker in ("2x", "3x", "-2x", "-3x", "ultra", "inverse", "bear", "short")
    )
    return EquityCandidate(
        symbol=symbol,
        price=price,
        median_dollar_volume_20d=statistics.median(dollar_volumes),
        atr_pct=(statistics.mean(true_ranges) / price if true_ranges and price > 0 else 0),
        data_completeness=min(1.0, len(recent) / 20),
        economic_sector=(
            str(getattr(asset, "sector", "") or getattr(asset, "economic_sector", "")).strip()
            or None
        ),
        exchange=str(getattr(asset, "exchange", "")).split(".")[-1],
        tradable=bool(getattr(asset, "tradable", False)),
        status=str(getattr(asset, "status", "")).lower().split(".")[-1],
        leveraged_or_inverse=leveraged,
    )


def main() -> int:
    config = load_config()
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
    source_path = Path(config.universe.equity.candidate_universe_path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    fallback = {str(item).upper() for item in payload.get("symbols", ()) if isinstance(item, str)}
    client = TradingClient(key, secret, paper=config.alpaca.paper)
    assets = client.get_all_assets(
        GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
    )
    allowed = {
        str(asset.symbol): asset
        for asset in assets
        if str(asset.symbol) in fallback and bool(getattr(asset, "tradable", False))
    }
    history = AlpacaHistoricalData(
        key,
        secret,
        stock_feed=config.alpaca.stock_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    end = datetime.now(UTC)
    start = end - timedelta(days=45)
    candidates: list[EquityCandidate] = []
    for batch in _chunks(sorted(allowed)):
        by_symbol = history.get_bars_batch(batch, "1d", start=start, end=end)
        candidates.extend(
            _candidate(symbol, allowed[symbol], by_symbol.get(symbol, [])) for symbol in batch
        )
    scanner = EquityUniverseScanner(config.universe.equity)
    result = scanner.scan(candidates, data_timestamp=end)
    scanner.persist(result)
    enriched = {
        "generated_at": end.isoformat(),
        "source": "Alpaca active tradable US equity assets + repository fallback",
        "symbols": sorted(fallback),
        "candidates": [asdict(item) for item in candidates],
    }
    temporary = source_path.with_suffix(source_path.suffix + ".tmp")
    temporary.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(source_path)
    print(
        f"Scanner selected {len(result.selected_symbols)} of {len(candidates)} "
        f"candidates at {result.selection_timestamp}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
