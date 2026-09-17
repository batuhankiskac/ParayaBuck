"""Deterministic input normalization and point-in-time data receipts.

The production CSV loader intentionally preserves its historical aliases and
timeframe string.  This campaign owns the frozen v5 normalization boundary so
the economic run cannot accidentally mix ``1d`` with ``1Day`` or use a UTC
date as an exchange session date.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from parayabuck.backtest.portfolio_engine import TerminalLifecycle
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import NEW_YORK

EXPECTED_BAR_ROWS = 11_686_832
EXPECTED_BAR_SHA256 = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_LIFETIME_ROWS = 9_010
EXPECTED_LIFETIME_SHA256 = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_CORPORATE_ACTION_ROWS = 143_446
EXPECTED_CORPORATE_ACTION_SHA256 = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
EXPECTED_SPLIT_ROWS = 1_477
EXPECTED_DIVIDEND_ROWS = 141_908


class DataValidityError(RuntimeError):
    """Raised when frozen input data cannot support a valid economic run."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_aware_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def session_date(timestamp: datetime) -> date:
    return timestamp.astimezone(NEW_YORK).date()


@dataclass(frozen=True)
class EntityLifetime:
    symbol: str
    valid_from: datetime
    valid_to: datetime | None
    first_observed_bar: datetime
    last_observed_bar: datetime
    exchange: str
    current_status: str
    terminal_event_class: str

    def valid_at(self, timestamp: datetime) -> bool:
        return self.valid_from <= timestamp and (self.valid_to is None or timestamp <= self.valid_to)


@dataclass(frozen=True)
class NormalizedDataset:
    bars: list[Bar]
    bars_by_symbol: dict[str, tuple[Bar, ...]]
    session_dates_by_symbol: dict[str, tuple[date, ...]]
    spy_session_dates: tuple[date, ...]
    lifetimes: dict[str, EntityLifetime]
    terminal_lifecycles: dict[str, TerminalLifecycle]
    corporate_actions: list[CorporateAction]
    structural_audit: dict[str, object]
    source_path: str
    lifetime_path: str
    corporate_action_path: str

    def bar_for(self, symbol: str, day: date) -> Bar | None:
        canonical = canonical_symbol(symbol, Market.EQUITY)
        dates = self.session_dates_by_symbol.get(canonical, ())
        if not dates:
            return None
        # The loader rejects canonical duplicate session rows, so at most one
        # bar can occupy this exact date.
        from bisect import bisect_left

        index = bisect_left(dates, day)
        if index >= len(dates) or dates[index] != day:
            return None
        return self.bars_by_symbol[canonical][index]

    def previous_spy_date(self, day: date) -> date | None:
        from bisect import bisect_left

        index = bisect_left(self.spy_session_dates, day) - 1
        return self.spy_session_dates[index] if index >= 0 else None


def _load_lifetimes(
    path: str | Path,
) -> tuple[dict[str, EntityLifetime], dict[str, TerminalLifecycle], dict[str, object]]:
    source = Path(path)
    lifetimes: dict[str, EntityLifetime] = {}
    row_count = 0
    invalid_rows = 0
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "canonical_symbol",
            "valid_from",
            "valid_to",
            "first_observed_bar",
            "last_observed_bar",
            "exchange",
            "current_status",
            "terminal_event_class",
        }
        if not required.issubset(reader.fieldnames or ()):
            raise DataValidityError("Lifetime table is missing required columns")
        for raw in reader:
            row_count += 1
            try:
                symbol = canonical_symbol(str(raw["canonical_symbol"]), Market.EQUITY)
                valid_from = parse_aware_timestamp(str(raw["valid_from"]))
                valid_to_raw = str(raw.get("valid_to") or "").strip()
                valid_to = parse_aware_timestamp(valid_to_raw) if valid_to_raw else None
                first_observed = parse_aware_timestamp(str(raw["first_observed_bar"]))
                last_observed = parse_aware_timestamp(str(raw["last_observed_bar"]))
                if valid_to is not None and valid_to < valid_from:
                    raise ValueError("valid_to precedes valid_from")
                if first_observed > last_observed or first_observed < valid_from:
                    raise ValueError("invalid observed-bar interval")
                if symbol in lifetimes:
                    raise ValueError(f"duplicate canonical lifetime: {symbol}")
                lifetimes[symbol] = EntityLifetime(
                    symbol=symbol,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    first_observed_bar=first_observed,
                    last_observed_bar=last_observed,
                    exchange=str(raw.get("exchange") or "").strip(),
                    current_status=str(raw.get("current_status") or "").strip(),
                    terminal_event_class=str(raw.get("terminal_event_class") or "FINAL_SURVIVOR").strip()
                    or "FINAL_SURVIVOR",
                )
            except (KeyError, ValueError, TypeError) as exc:
                invalid_rows += 1
                raise DataValidityError(f"Invalid lifetime row {row_count}: {exc}") from exc
    if row_count != EXPECTED_LIFETIME_ROWS or sha256_file(source) != EXPECTED_LIFETIME_SHA256:
        raise DataValidityError("Pinned lifetime table identity changed")
    terminals = {
        symbol: TerminalLifecycle(lifetime.last_observed_bar, lifetime.terminal_event_class)
        for symbol, lifetime in lifetimes.items()
    }
    return (
        lifetimes,
        terminals,
        {
            "path": str(source),
            "sha256": sha256_file(source),
            "rows": row_count,
            "invalid_rows": invalid_rows,
            "symbol_count": len(lifetimes),
            "pit_quality": "valid_from_valid_to_join",
        },
    )


def _load_dividend_actions(
    path: str | Path,
    lifetimes: dict[str, EntityLifetime],
) -> tuple[list[CorporateAction], dict[str, object]]:
    source = Path(path)
    actions: list[CorporateAction] = []
    counts: Counter[str] = Counter()
    row_count = 0
    invalid_rows = 0
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "ex_date", "action_type", "rate"}
        if not required.issubset(reader.fieldnames or ()):
            raise DataValidityError("Corporate-action table is missing required columns")
        for raw in reader:
            row_count += 1
            action_type = str(raw.get("action_type") or "").strip().lower()
            counts[action_type] += 1
            try:
                symbol = canonical_symbol(str(raw["symbol"]), Market.EQUITY)
                if symbol not in lifetimes:
                    raise ValueError(f"corporate action symbol missing from lifetimes: {symbol}")
                timestamp = parse_aware_timestamp(str(raw["ex_date"]))
                if action_type == "cash_dividends":
                    value = float(raw["rate"])
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError("cash dividend rate must be positive and finite")
                    actions.append(CorporateAction(timestamp, symbol, CorporateActionType.DIVIDEND, value))
                elif action_type in {"forward_splits", "reverse_splits", "stock_dividends"}:
                    # v5 prices are split-adjusted. Split actions are audited
                    # but deliberately never passed to the production engine.
                    continue
                else:
                    raise ValueError(f"unsupported corporate action type: {action_type}")
            except (KeyError, ValueError, TypeError) as exc:
                invalid_rows += 1
                raise DataValidityError(f"Invalid corporate-action row {row_count}: {exc}") from exc
    digest = sha256_file(source)
    if row_count != EXPECTED_CORPORATE_ACTION_ROWS or digest != EXPECTED_CORPORATE_ACTION_SHA256:
        raise DataValidityError("Pinned corporate-action table identity changed")
    if (
        counts["cash_dividends"] != EXPECTED_DIVIDEND_ROWS
        or sum(counts[item] for item in ("forward_splits", "reverse_splits", "stock_dividends"))
        != EXPECTED_SPLIT_ROWS + counts["stock_dividends"]
    ):
        raise DataValidityError("Pinned corporate-action inventory changed")
    return (
        sorted(actions, key=lambda item: (item.timestamp, item.symbol)),
        {
            "path": str(source),
            "sha256": digest,
            "rows": row_count,
            "invalid_rows": invalid_rows,
            "action_type_counts": dict(sorted(counts.items())),
            "cash_dividend_rows": counts["cash_dividends"],
            "split_action_rows": counts["forward_splits"] + counts["reverse_splits"],
            "stock_dividend_rows": counts["stock_dividends"],
            "applied_action_rows": len(actions),
            "split_actions_applied": False,
        },
    )


def load_normalized_dataset(
    bars_path: str | Path,
    lifetime_path: str | Path,
    corporate_action_path: str | Path,
) -> NormalizedDataset:
    """Load and validate the pinned v5 package, failing closed on defects."""
    source = Path(bars_path)
    lifetimes, terminals, lifetime_audit = _load_lifetimes(lifetime_path)
    corporate_actions, corporate_audit = _load_dividend_actions(corporate_action_path, lifetimes)
    digest = sha256_file(source)
    byte_size = source.stat().st_size
    bars: list[Bar] = []
    by_symbol: dict[str, list[Bar]] = {}
    raw_market_values: Counter[str] = Counter()
    raw_timeframe_values: Counter[str] = Counter()
    dataset_dates: set[date] = set()
    spy_dates: set[date] = set()
    duplicate_rows = 0
    conflict_rows = 0
    raw_order_violations = 0
    lifecycle_invalid_rows = 0
    missing_lifetime_rows = 0
    invalid_open = 0
    invalid_close = 0
    nonfinite_rows = 0
    nonpositive_open = 0
    nonpositive_close = 0
    previous_key: tuple[date, str, str, str] | None = None
    timestamp_cache: dict[datetime, datetime] = {}
    symbol_cache: dict[str, str] = {}
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"}
        if not required.issubset(reader.fieldnames or ()):
            raise DataValidityError("Bars table is missing required columns")
        for raw in reader:
            try:
                raw_market = str(raw["market"] or "").strip()
                raw_timeframe = str(raw["timeframe"] or "").strip()
                raw_market_values[raw_market] += 1
                raw_timeframe_values[raw_timeframe] += 1
                market = raw_market.lower()
                timeframe_alias = raw_timeframe.lower()
                if market != Market.EQUITY.value:
                    raise ValueError(f"unexpected market alias: {raw_market}")
                if timeframe_alias not in {"1d", "1day"}:
                    raise ValueError(f"unexpected timeframe alias: {raw_timeframe}")
                timeframe = "1d"
                raw_symbol = str(raw["symbol"] or "").strip()
                symbol = symbol_cache.setdefault(raw_symbol, canonical_symbol(raw_symbol, Market.EQUITY))
                timestamp_raw = parse_aware_timestamp(str(raw["timestamp"]))
                timestamp = timestamp_cache.setdefault(timestamp_raw, timestamp_raw)
                day = session_date(timestamp)
                key = (day, symbol, market, timeframe)
                if previous_key is not None:
                    if key < previous_key:
                        raw_order_violations += 1
                previous_key = key
                numeric_values = tuple(float(raw[field]) for field in ("open", "high", "low", "close", "volume"))
                if not all(math.isfinite(value) for value in numeric_values):
                    nonfinite_rows += 1
                open_price, high, low, close, volume = numeric_values
                if open_price <= 0:
                    nonpositive_open += 1
                if close <= 0:
                    nonpositive_close += 1
                if not math.isfinite(open_price) or open_price <= 0:
                    invalid_open += 1
                if not math.isfinite(close) or close <= 0:
                    invalid_close += 1
                lifetime = lifetimes.get(symbol)
                if lifetime is None:
                    missing_lifetime_rows += 1
                elif not lifetime.valid_at(timestamp):
                    lifecycle_invalid_rows += 1
                bar = Bar(
                    timestamp=timestamp,
                    symbol=symbol,
                    timeframe=timeframe,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    market=Market.EQUITY,
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise DataValidityError(f"Invalid bar row {len(bars) + 2}: {exc}") from exc
            bars.append(bar)
            by_symbol.setdefault(symbol, []).append(bar)
            dataset_dates.add(day)
            if symbol == "SPY":
                spy_dates.add(day)

    observed_rows = len(bars)
    # The source package intentionally contains two concatenated provider
    # alias blocks, so raw CSV row order is not a structural invariant. The
    # canonical per-symbol ordering below is the invariant used by the
    # strategy and explicitly catches duplicate/conflicting session rows.
    duplicate_rows = 0
    conflict_rows = 0
    order_violations = 0
    ordered_by_symbol: dict[str, tuple[Bar, ...]] = {}
    dates_by_symbol: dict[str, tuple[date, ...]] = {}
    for symbol, symbol_bars in by_symbol.items():
        ordered = tuple(sorted(symbol_bars, key=lambda item: item.timestamp))
        for previous_bar, current_bar in zip(ordered, ordered[1:]):
            if current_bar.timestamp <= previous_bar.timestamp:
                order_violations += 1
            if session_date(current_bar.timestamp) == session_date(previous_bar.timestamp):
                duplicate_rows += 1
                if current_bar != previous_bar:
                    conflict_rows += 1
        ordered_by_symbol[symbol] = ordered
        dates_by_symbol[symbol] = tuple(session_date(item.timestamp) for item in ordered)

    if (
        digest != EXPECTED_BAR_SHA256
        or observed_rows != EXPECTED_BAR_ROWS
        or byte_size != 882_354_549
        or duplicate_rows != 0
        or conflict_rows != 0
        or order_violations != 0
        or lifecycle_invalid_rows != 0
        or missing_lifetime_rows != 0
        or nonfinite_rows != 0
        or invalid_open != 0
        or invalid_close != 0
        or nonpositive_open != 0
        or nonpositive_close != 0
    ):
        raise DataValidityError(
            "Pinned normalized bars failed structural validation: "
            f"rows={observed_rows}, sha256={digest}, duplicates={duplicate_rows}, "
            f"order_violations={order_violations}, lifecycle_invalid={lifecycle_invalid_rows}"
        )

    spy_dates_ordered = tuple(sorted(spy_dates))
    structural_audit: dict[str, object] = {
        "bars": {
            "path": str(source),
            "sha256": digest,
            "byte_size": byte_size,
            "rows": observed_rows,
            "unique_symbols": len(ordered_by_symbol),
            "session_dates": len(dataset_dates),
            "first_session_date": min(dataset_dates).isoformat(),
            "last_session_date": max(dataset_dates).isoformat(),
            "raw_market_values": dict(sorted(raw_market_values.items())),
            "raw_timeframe_values": dict(sorted(raw_timeframe_values.items())),
            "normalized_market": "equity",
            "normalized_timeframe": "1d",
            "duplicate_symbol_session_rows": duplicate_rows,
            "conflicting_duplicate_rows": conflict_rows,
            "order_violations": order_violations,
            "raw_order_violations": raw_order_violations,
            "nonfinite_rows": nonfinite_rows,
            "nonpositive_open_count": nonpositive_open,
            "nonpositive_close_count": nonpositive_close,
            "invalid_open_count": invalid_open,
            "invalid_close_count": invalid_close,
        },
        "spy": {
            "bars": len(ordered_by_symbol.get("SPY", ())),
            "session_dates": len(spy_dates_ordered),
            "first_session_date": spy_dates_ordered[0].isoformat() if spy_dates_ordered else None,
            "last_session_date": spy_dates_ordered[-1].isoformat() if spy_dates_ordered else None,
            "dataset_session_date_coverage_pct": round(100 * len(spy_dates_ordered) / len(dataset_dates), 6),
        },
        "lifecycle": lifetime_audit,
        "corporate_actions": corporate_audit,
        "session_rule": "America/New_York derived session date; explicit common SPY session join",
        "price_rule": "split-adjusted bars; dividend cash actions only; split actions not applied",
        "evaluation_end": "2026-07-30",
    }
    return NormalizedDataset(
        bars=bars,
        bars_by_symbol=ordered_by_symbol,
        session_dates_by_symbol=dates_by_symbol,
        spy_session_dates=spy_dates_ordered,
        lifetimes=lifetimes,
        terminal_lifecycles=terminals,
        corporate_actions=corporate_actions,
        structural_audit=structural_audit,
        source_path=str(source),
        lifetime_path=str(lifetime_path),
        corporate_action_path=str(corporate_action_path),
    )


__all__ = [
    "EXPECTED_BAR_ROWS",
    "EXPECTED_BAR_SHA256",
    "EXPECTED_CORPORATE_ACTION_ROWS",
    "EXPECTED_CORPORATE_ACTION_SHA256",
    "EXPECTED_DIVIDEND_ROWS",
    "EXPECTED_LIFETIME_ROWS",
    "EXPECTED_LIFETIME_SHA256",
    "EXPECTED_SPLIT_ROWS",
    "DataValidityError",
    "EntityLifetime",
    "NormalizedDataset",
    "load_normalized_dataset",
    "parse_aware_timestamp",
    "session_date",
    "sha256_file",
]
