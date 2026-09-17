"""Data and PIT receipt helpers for the V3 volatility campaign."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.portfolio_engine import TerminalLifecycle
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.markets.symbols import canonical_symbol


def sha256_file(path: str | Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _parse_timestamp(raw: str) -> datetime:
    timestamp = datetime.fromisoformat(raw)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp


def load_symmetric_equity_bars(path: str | Path) -> tuple[list[Bar], dict[str, object]]:
    """Load the v5 package while normalizing only its lexical enum casing."""

    bars: list[Bar] = []
    market_values: Counter[str] = Counter()
    timeframe_values: Counter[str] = Counter()
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    spy_count = 0
    symbols: set[str] = set()
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"Equity dataset is missing columns: {sorted(required - set(reader.fieldnames or ())) }")
        for raw in reader:
            timestamp = _parse_timestamp(str(raw["timestamp"]))
            raw_market = str(raw.get("market") or "").strip()
            raw_timeframe = str(raw.get("timeframe") or "").strip()
            market_values[raw_market] += 1
            timeframe_values[raw_timeframe] += 1
            if raw_market.lower() != "equity":
                raise ValueError(f"Unexpected market {raw_market!r}")
            timeframe = raw_timeframe.lower()
            if timeframe == "1day":
                timeframe = "1d"
            if timeframe != "1d":
                raise ValueError(f"Unexpected equity timeframe {raw_timeframe!r}")
            if timestamp.weekday() >= 5:
                raise ValueError(f"Equity dataset contains a weekend bar: {timestamp.isoformat()}")
            symbol = str(raw["symbol"]).strip()
            bars.append(
                Bar(
                    timestamp=timestamp,
                    symbol=symbol,
                    timeframe="1d",
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw.get("volume") or 0.0),
                    market=Market.EQUITY,
                )
            )
            symbols.add(symbol)
            first_timestamp = timestamp if first_timestamp is None else min(first_timestamp, timestamp)
            last_timestamp = timestamp if last_timestamp is None else max(last_timestamp, timestamp)
            if symbol == "SPY":
                spy_count += 1
    return bars, {
        "row_count": len(bars),
        "symbol_count": len(symbols),
        "spy_bar_count": spy_count,
        "first_timestamp": first_timestamp.isoformat() if first_timestamp else None,
        "last_timestamp": last_timestamp.isoformat() if last_timestamp else None,
        "raw_market_values": dict(market_values),
        "raw_timeframe_values": dict(timeframe_values),
        "normalization": {"EQUITY": "equity", "1Day": "1d"},
    }


def load_v5_dividend_actions(
    path: str | Path,
    *,
    end: datetime,
) -> tuple[list[CorporateAction], dict[str, object]]:
    """Load only cash dividends from the split-adjusted v5 action package."""

    actions: list[CorporateAction] = []
    raw_type_counts: Counter[str] = Counter()
    used_type_counts: Counter[str] = Counter()
    first_ex: datetime | None = None
    last_ex: datetime | None = None
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            action_type = str(raw.get("action_type") or "")
            raw_type_counts[action_type] += 1
            if action_type != "cash_dividends":
                continue
            ex_date = datetime.fromisoformat(str(raw["ex_date"])).replace(tzinfo=UTC)
            if ex_date > end:
                continue
            value = float(raw.get("rate") or 0.0)
            if value <= 0:
                continue
            actions.append(CorporateAction(ex_date, str(raw["symbol"]), CorporateActionType.DIVIDEND, value))
            used_type_counts[action_type] += 1
            first_ex = ex_date if first_ex is None else min(first_ex, ex_date)
            last_ex = ex_date if last_ex is None else max(last_ex, ex_date)
    return actions, {
        "raw_action_type_counts": dict(raw_type_counts),
        "used_action_type_counts": dict(used_type_counts),
        "used_action_count": len(actions),
        "used_first_ex_date": first_ex.date().isoformat() if first_ex else None,
        "used_last_ex_date": last_ex.date().isoformat() if last_ex else None,
        "ignored_action_types": [
            "forward_splits",
            "reverse_splits",
            "stock_dividends",
            "unit_splits",
            "stock_mergers",
            "cash_mergers",
            "spinoffs",
            "name_changes",
        ],
    }


def load_terminal_lifecycles(path: str | Path) -> tuple[dict[str, TerminalLifecycle], dict[str, object]]:
    lifecycles: dict[str, TerminalLifecycle] = {}
    status_counts: Counter[str] = Counter()
    evidence_counts: Counter[str] = Counter()
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            status = str(raw.get("current_status") or "")
            evidence = str(raw.get("evidence_class") or "")
            status_counts[status] += 1
            evidence_counts[evidence] += 1
            if status != "inactive":
                continue
            symbol = canonical_symbol(str(raw["canonical_symbol"]), Market.EQUITY)
            if symbol in lifecycles:
                raise ValueError(f"Duplicate terminal lifecycle for {symbol}")
            lifecycles[symbol] = TerminalLifecycle(
                _parse_timestamp(str(raw["last_observed_bar"])),
                str(raw.get("terminal_event_class") or evidence or "POSSIBLE_DELISTING"),
            )
    return lifecycles, {
        "row_count": sum(status_counts.values()),
        "status_counts": dict(status_counts),
        "evidence_counts": dict(evidence_counts),
        "terminal_lifecycle_count": len(lifecycles),
    }


def latest_bar_by_symbol(bars: Iterable[Bar]) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for bar in bars:
        symbol = canonical_symbol(bar.symbol, bar.market)
        latest[symbol] = max(latest.get(symbol, bar.timestamp), bar.timestamp)
    return latest


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "latest_bar_by_symbol",
    "load_json",
    "load_symmetric_equity_bars",
    "load_terminal_lifecycles",
    "load_v5_dividend_actions",
    "sha256_file",
]
