#!/usr/bin/env python3
"""Research-only H14 input adapter and calendar preflight.

This module deliberately stops at a strict, hash-pinned preflight.  It exposes
the native corporate-action objects and exact event tuples needed by a later
isolated native-engine run; it does not alter product configuration or run an
economic backtest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas_market_calendars as mcal

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import (
    CorporateAction,
    CorporateActionType,
)
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason, TargetPortfolio, TargetWeight

REPO = Path(__file__).resolve().parents[1]
SYMBOL = "SPY"
END = date(2026, 7, 30)
CONFIG_PATH = "config/settings.yaml"
CONFIG_SHA256 = "414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353"
CELLS: dict[str, tuple[date, date]] = {
    "full_2022": (date(2022, 1, 3), END),
    "recent_2024": (date(2024, 1, 2), END),
    "long_2016": (date(2016, 1, 4), END),
}
SOURCE_CA_TYPES = {"cash_dividends"}


class H14DataError(ValueError):
    """A substantive H14 data or protocol gate failure."""


@dataclass(frozen=True)
class H14Event:
    month: str
    p0: date
    l0: date
    f1: date
    f2: date
    f3: date
    f4: date

    @property
    def entry_decision(self) -> date:
        return self.p0

    @property
    def entry_fill(self) -> date:
        return self.l0

    @property
    def exit_decision(self) -> date:
        return self.f3

    @property
    def exit_fill(self) -> date:
        return self.f4


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise H14DataError(f"invalid {field}: {value!r}") from exc


def _sessions(start: date, end: date) -> list[date]:
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date=start.isoformat(), end_date=end.isoformat()
    )
    return [item.date() for item in schedule.index]


def _source_path(path: str | Path) -> Path:
    source = Path(path)
    return source if source.is_absolute() else REPO / source


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with _source_path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_manifest(manifest_path: str | Path) -> dict[str, object]:
    """Verify every declared file pin and the frozen H14 identity."""
    manifest_file = _source_path(manifest_path)
    raw = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not str(raw.get("mission", "")).startswith("h14_turn_of_month_protocol_2026_09_06_r"):
        raise H14DataError("wrong H14 manifest mission")
    if raw.get("candidate", {}).get("symbol") != SYMBOL:
        raise H14DataError("H14 candidate must be SPY")
    for item in raw.get("inputs", []):
        path = _source_path(str(item["path"]))
        expected = str(item["sha256"])
        actual = sha256_file(path)
        if actual != expected:
            raise H14DataError(f"input hash mismatch for {path}: {actual} != {expected}")
    return raw


def validate_execution_manifest(manifest_path: str | Path) -> dict[str, object]:
    """Verify the persisted final manifest, including code and engine pins."""
    raw = validate_manifest(manifest_path)
    if not str(raw.get("mission", "")).startswith("h14_turn_of_month_protocol_2026_09_06_r") or str(raw.get("mission", "")).endswith("r2"):
        raise H14DataError("canonical execution requires the final r3 manifest")
    digest_pattern = re.compile(r"^[0-9a-f]{64}$")
    pins = raw.get("frozen_sources")
    if not isinstance(pins, list) or not pins:
        raise H14DataError("r3 manifest has no frozen source pins")
    for item in pins:
        if not isinstance(item, dict) or not digest_pattern.fullmatch(str(item.get("sha256", ""))):
            raise H14DataError("r3 manifest contains a missing or placeholder digest")
        path = _source_path(str(item["path"]))
        actual = sha256_file(path)
        if actual != item["sha256"]:
            raise H14DataError(f"frozen source hash mismatch for {path}: {actual} != {item['sha256']}")
    matrix = raw.get("matrix")
    expected_matrix = {
        "cells": ["full_2022", "recent_2024"],
        "modes": ["candidate", "passive", "calendar_control"],
        "cost_multipliers": [1.0, 2.0, 3.0],
        "cell_count": 18,
    }
    if not isinstance(matrix, dict) or any(matrix.get(key) != value for key, value in expected_matrix.items()):
        raise H14DataError(f"r3 matrix mismatch: {matrix!r}")
    mission = str(raw["mission"])
    expected_output = f"data/research/generated/h14_turn_of_month_2026_09_06_{mission.rsplit('_', 1)[-1]}"
    if raw.get("output_directory") != expected_output:
        raise H14DataError("execution output directory is not the frozen isolated directory")
    return raw


def load_spy_bars(
    path: str | Path, start: date, end: date
) -> tuple[dict[date, dict[str, float]], int]:
    """Load actual SPY bars and return ``(bars_by_session, excluded_post_end_rows)``."""
    source = _source_path(path)
    rows: dict[date, dict[str, float]] = {}
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
    post_end = 0
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(reader.fieldnames or ()):
            raise H14DataError(f"bar CSV missing columns: {sorted(required - set(reader.fieldnames or ()))!r}")
        for raw in reader:
            if str(raw.get("symbol", "")).strip().upper() != SYMBOL:
                continue
            if str(raw.get("timeframe", "1d")) != "1d":
                raise H14DataError("SPY source contains a non-1d bar")
            try:
                timestamp = datetime.fromisoformat(str(raw["timestamp"]))
            except ValueError as exc:
                raise H14DataError(f"invalid SPY timestamp: {raw.get('timestamp')!r}") from exc
            if timestamp.tzinfo is None:
                raise H14DataError("SPY bar timestamp must be timezone-aware")
            session = timestamp.astimezone(UTC).date()
            if session < start:
                continue
            if session > end:
                post_end += 1
                continue
            if session in rows:
                raise H14DataError(f"duplicate SPY session: {session}")
            values: dict[str, float] = {}
            for field in ("open", "high", "low", "close", "volume"):
                try:
                    value = float(raw[field])
                except (TypeError, ValueError) as exc:
                    raise H14DataError(f"invalid SPY {field} on {session}") from exc
                if not math.isfinite(value) or value <= 0:
                    raise H14DataError(f"non-positive/non-finite SPY {field} on {session}")
                values[field] = value
            if not (values["low"] <= values["open"] <= values["high"]):
                raise H14DataError(f"SPY open outside range on {session}")
            if not (values["low"] <= values["close"] <= values["high"]):
                raise H14DataError(f"SPY close outside range on {session}")
            rows[session] = values
    expected = set(_sessions(start, end))
    actual = set(rows)
    if actual != expected:
        raise H14DataError(
            f"SPY session grid mismatch: missing={sorted(expected - actual)!r}, extra={sorted(actual - expected)!r}"
        )
    # The caller needs this explicit fact in its receipt; post-end source rows
    # are intentionally excluded, never silently consumed.
    return rows, post_end


def load_spy_dividend_actions(
    path: str | Path, *, end: date = END
) -> tuple[CorporateAction, ...]:
    """Map the retained source schema to native dividend actions losslessly enough for research.

    Raw metadata remains in the source CSV and its hash. The native engine
    receives ex-date UTC midnight and cash-per-share value, matching its
    before-fill entitlement ordering.
    """
    source = _source_path(path)
    required = {
        "action_type",
        "symbol",
        "ex_date",
        "record_date",
        "payable_date",
        "rate",
        "raw_id",
    }
    actions: list[CorporateAction] = []
    spy_rows = 0
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(reader.fieldnames or ()):
            raise H14DataError(f"CA CSV missing columns: {sorted(required - set(reader.fieldnames or ()))!r}")
        for raw in reader:
            if str(raw.get("symbol", "")).strip().upper() != SYMBOL:
                continue
            spy_rows += 1
            action_type = str(raw.get("action_type", "")).strip()
            if action_type not in SOURCE_CA_TYPES:
                raise H14DataError(f"unsupported SPY corporate action: {action_type!r}")
            if not str(raw.get("raw_id", "")).strip():
                raise H14DataError("SPY corporate action is missing raw_id")
            ex_date = _parse_date(str(raw.get("ex_date", "")), "SPY ex_date")
            try:
                value = float(str(raw.get("rate", "")))
            except ValueError as exc:
                raise H14DataError(f"invalid SPY dividend rate on {ex_date}") from exc
            if not math.isfinite(value) or value <= 0:
                raise H14DataError(f"SPY dividend rate must be positive on {ex_date}")
            if ex_date > end:
                continue
            actions.append(
                CorporateAction(
                    datetime(ex_date.year, ex_date.month, ex_date.day, tzinfo=UTC),
                    SYMBOL,
                    CorporateActionType.DIVIDEND,
                    value,
                )
            )
    if spy_rows != 40:
        raise H14DataError(f"SPY corporate-action row count changed: {spy_rows}")
    if len(actions) != len({item.timestamp for item in actions}):
        raise H14DataError("duplicate SPY ex-date dividend action")
    return tuple(sorted(actions, key=lambda item: item.timestamp))


def event_tuples(start: date, end: date) -> tuple[H14Event, ...]:
    """Build exact calendar tuples without creating or filling market data."""
    calendar = _sessions(start - timedelta(days=7), end + timedelta(days=20))
    by_month: dict[tuple[int, int], list[date]] = {}
    for session in calendar:
        by_month.setdefault((session.year, session.month), []).append(session)
    events: list[H14Event] = []
    for (year, month), sessions in sorted(by_month.items()):
        if not sessions or sessions[-1] < start or sessions[0] > end:
            continue
        next_month = (year + (month == 12), 1 if month == 12 else month + 1)
        following = by_month.get(next_month, [])
        if len(sessions) < 1 or len(following) < 4:
            continue
        l0 = sessions[-1]
        p0_candidates = [item for item in calendar if item < l0]
        if not p0_candidates:
            continue
        event = H14Event(str(year) + f"-{month:02d}", p0_candidates[-1], l0, *following[:4])
        if event.p0 >= start and event.f4 <= end:
            events.append(event)
    return tuple(events)


def preflight_cell(manifest: dict[str, object], cell: str) -> dict[str, object]:
    if cell not in CELLS:
        raise H14DataError(f"unsupported cell {cell!r}; allowed: {sorted(CELLS)}")
    start, end = CELLS[cell]
    bars_path = next(item["path"] for item in manifest["inputs"] if "equity_pit_symmetric" in item["path"] and item["path"].endswith(".csv"))
    ca_path = next(item["path"] for item in manifest["inputs"] if "corporate_actions_v5.csv" in item["path"] and not item["path"].endswith("manifest.json"))
    bars, post_end_rows = load_spy_bars(str(bars_path), start, end)
    actions = load_spy_dividend_actions(str(ca_path), end=end)
    events = event_tuples(start, end)
    for event in events:
        if any(item not in bars for item in (event.p0, event.l0, event.f1, event.f2, event.f3, event.f4)):
            raise H14DataError(f"event tuple is not executable: {event.month}")
    return {
        "status": "PASS",
        "cell": cell,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "spy_sessions": len(bars),
        "source_rows_after_end_excluded": post_end_rows,
        "dividend_actions_through_end": len(actions),
        "events": [asdict(event) for event in events],
        "fresh_cash_at_cell_start": True,
        "native_action_mapping": "ex_date 00:00 UTC -> DIVIDEND value=rate cash/share",
        "economic_run": False,
    }


class H14SchedulePolicy(RebalancePolicy):
    """Research-only policy whose schedule is the frozen event decision set."""

    def __init__(self, decision_dates: Iterable[date]) -> None:
        super().__init__(Market.EQUITY, "monthly")
        object.__setattr__(self, "_decision_dates", frozenset(decision_dates))

    def is_period_start(self, previous: datetime | None, current: datetime) -> bool:
        del previous
        return current.astimezone(UTC).date() in self._decision_dates


class H14TurnOfMonthStrategy(PortfolioStrategy):
    """Fixed-weight H14 signal used only by the research harness."""

    name = "h14_spy_turn_of_month_lastday_first3_v2"
    version = "2.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    rebalance_policy_declared = "research:event_dates"

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def __init__(
        self,
        entry_dates: Iterable[date],
        exit_dates: Iterable[date],
        *,
        mode: str,
        target_weight: float = 0.05,
    ) -> None:
        self._entry_dates = frozenset(entry_dates)
        self._exit_dates = frozenset(exit_dates)
        self._mode = mode
        self._target_weight = target_weight
        super().__init__({"mode": mode, "target_weight": target_weight})

    def _validate_parameters(self) -> None:
        if self._mode not in {"candidate", "passive", "calendar_control"}:
            raise ValueError(f"unsupported H14 mode: {self._mode}")
        if not math.isclose(self._target_weight, 0.05, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("H14 target weight is frozen at 5%")
        if not self._entry_dates or (self._mode != "passive" and not self._exit_dates):
            raise ValueError("H14 strategy requires frozen entry/exit dates")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        decision_date = context.decision_timestamp.astimezone(UTC).date()
        if decision_date in self._entry_dates:
            weights = (TargetWeight(SYMBOL, self._target_weight, reason=f"h14_{self._mode}_entry"),)
            cash_weight = 1.0 - self._target_weight
        elif decision_date in self._exit_dates:
            weights = ()
            cash_weight = 1.0
        else:
            raise H14DataError(f"strategy decided on an unfrozen H14 date: {decision_date}")
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=cash_weight,
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version="h14_static_spy_v2",
            universe_snapshot_id="h14_static_spy_v2",
            parameters=self.parameter_tuple(),
            metadata={"h14_mode": self._mode, "h14_frozen_target_weight": self._target_weight},
        )


def load_native_spy_bars(path: str | Path, start: date, end: date) -> list[Bar]:
    """Load actual split-adjusted SPY bars as native engine inputs."""
    source = _source_path(path)
    bars: list[Bar] = []
    seen: set[date] = set()
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(reader.fieldnames or ()):
            raise H14DataError(f"bar CSV missing columns: {sorted(required - set(reader.fieldnames or ()))!r}")
        for raw in reader:
            if str(raw.get("symbol", "")).strip().upper() != SYMBOL:
                continue
            timestamp = datetime.fromisoformat(str(raw["timestamp"]))
            if timestamp.tzinfo is None:
                raise H14DataError("SPY bar timestamp must be timezone-aware")
            session = timestamp.astimezone(UTC).date()
            if session < start or session > end:
                continue
            if session in seen:
                raise H14DataError(f"duplicate SPY session: {session}")
            seen.add(session)
            values = {field: float(raw[field]) for field in ("open", "high", "low", "close", "volume")}
            if any(not math.isfinite(value) or value <= 0 for value in values.values()):
                raise H14DataError(f"invalid SPY bar on {session}")
            bars.append(
                Bar(
                    timestamp=timestamp,
                    symbol=SYMBOL,
                    timeframe="1d",
                    open=values["open"],
                    high=values["high"],
                    low=values["low"],
                    close=values["close"],
                    volume=values["volume"],
                    market=Market.EQUITY,
                )
            )
    expected = set(_sessions(start, end))
    if seen != expected:
        raise H14DataError(f"native SPY grid mismatch: missing={sorted(expected-seen)!r}")
    return sorted(bars, key=lambda bar: bar.timestamp)


def _control_dates(event: H14Event, sessions: list[date]) -> tuple[date, date]:
    try:
        index = sessions.index(event.f4)
        _m0, _m1, _m2, m3, _m4 = sessions[index : index + 5]
    except (ValueError, IndexError) as exc:
        raise H14DataError(f"insufficient fixed control sessions after {event.f4}") from exc
    return sessions[index - 1], m3


def _native_receipt(result: object, *, actions: tuple[CorporateAction, ...], events: tuple[H14Event, ...], cell: str, mode: str, cost_multiplier: float, launch_started_at: str) -> dict[str, object]:
    result_dict = result.__dict__
    summary_keys = (
        "starting_equity", "ending_equity", "total_return", "gross_return", "net_return",
        "max_drawdown", "duration_days", "start_date", "end_date", "turnover",
        "slippage_cost_total", "spread_cost_total", "fee_total", "dividend_total",
        "rebalance_count", "closed_positions", "benchmark_return",
    )
    def serialise(value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return value.to_dict()
        if hasattr(value, "__dataclass_fields__"):
            return {key: serialise(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {str(key): serialise(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [serialise(item) for item in value]
        return value
    return {
        "status": "PASS",
        "cell": cell,
        "mode": mode,
        "cost_multiplier": cost_multiplier,
        "launch_started_at": launch_started_at,
        "summary": {key: serialise(result_dict[key]) for key in summary_keys},
        "equity_curve": [
            {"timestamp": timestamp.isoformat(), "nav": nav}
            for timestamp, nav in zip(result.curve_timestamps, result.equity_curve)
        ],
        "fills": serialise(result.fills),
        "rebalances": serialise(result.rebalances),
        "marked_positions": [
            {"timestamp": timestamp.isoformat(), "weights": weights}
            for timestamp, weights in result.weight_history
        ],
        "final_state": serialise(result.state),
        "corporate_actions": serialise(actions),
        "event_trace": [asdict(event) for event in events],
        "accounting_convention": "ex-date cash accrual before fills; retrospective NAV accounting; no payment-date claim",
        "native_engine": "PortfolioBacktestEngine",
        "automatic_promotion": False,
    }


def run_native_cell(
    manifest: dict[str, object],
    bars: list[Bar],
    *,
    cell: str,
    mode: str,
    cost_multiplier: float,
    launch_started_at: str,
) -> dict[str, object]:
    if cell not in CELLS:
        raise H14DataError(f"unsupported cell: {cell}")
    if mode not in {"candidate", "passive", "calendar_control"}:
        raise H14DataError(f"unsupported H14 mode: {mode}")
    start, end = CELLS[cell]
    events = event_tuples(start, end)
    sessions = _sessions(start - timedelta(days=7), end + timedelta(days=20))
    entry_dates: set[date] = set()
    exit_dates: set[date] = set()
    if mode == "candidate":
        entry_dates = {event.p0 for event in events}
        exit_dates = {event.f3 for event in events}
        run_start = start
    elif mode == "calendar_control":
        control = [_control_dates(event, sessions) for event in events]
        entry_dates = {item[0] for item in control}
        exit_dates = {item[1] for item in control}
        run_start = start
    else:
        first = min(_sessions(start, end))
        previous = [item for item in sessions if item < first]
        if not previous:
            raise H14DataError("passive control has no prior real session for next-open entry")
        entry_dates = {previous[-1]}
        run_start = previous[-1]
    selected = [bar for bar in bars if run_start <= bar.timestamp.astimezone(UTC).date() <= end]
    if not selected:
        raise H14DataError(f"no native bars for {cell}/{mode}")
    actions = load_spy_dividend_actions(
        next(item["path"] for item in manifest["inputs"] if "corporate_actions_v5.csv" in item["path"] and not item["path"].endswith("manifest.json")),
        end=end,
    )
    strategy = H14TurnOfMonthStrategy(entry_dates, exit_dates, mode=mode)
    engine = PortfolioBacktestEngine(
        strategy,
        H14SchedulePolicy(entry_dates | exit_dates),
        starting_equity=100_000.0,
        slippage_bps=2.0 * cost_multiplier,
        spread_bps=2.0 * cost_multiplier,
        commission=0.0,
        equity_taf_per_share=0.000195,
        equity_taf_cap=9.79,
        equity_cat_per_share=0.000003,
        equity_sec_fee_rate=0.0000206,
        equity_use_effective_dated_fees=True,
        benchmark_symbol=SYMBOL,
        max_participation_pct=1.0,
        liquidity_lookback_bars=1,
        turnover_band=0.0,
        minimum_order_notional=0.0,
        maximum_allocation=1.0,
        allow_static_universe=True,
    )
    result = engine.run(selected, corporate_actions=list(actions), liquidate_at_end=True)
    return _native_receipt(
        result,
        actions=actions,
        events=events,
        cell=cell,
        mode=mode,
        cost_multiplier=cost_multiplier,
        launch_started_at=launch_started_at,
    )


def run_batch(manifest_path: str | Path) -> dict[str, object]:
    """Run the frozen 18-cell research matrix through the native engine."""
    launch_started_at = datetime.now(UTC).isoformat()
    manifest = validate_execution_manifest(manifest_path)
    actual_config_sha = sha256_file(CONFIG_PATH)
    if actual_config_sha != CONFIG_SHA256:
        raise H14DataError(f"current settings.yaml hash differs from frozen cost model: {actual_config_sha}")
    # Run one preflight per primary cell before launching any engine cell.
    preflight = {cell: preflight_cell(manifest, cell) for cell in ("full_2022", "recent_2024")}
    bars_path = next(item["path"] for item in manifest["inputs"] if "equity_pit_symmetric" in item["path"] and item["path"].endswith(".csv"))
    bars = load_native_spy_bars(str(bars_path), date(2016, 1, 4), END)
    cells: list[dict[str, object]] = []
    for cell in ("full_2022", "recent_2024"):
        for mode in ("candidate", "passive", "calendar_control"):
            for cost_multiplier in (1.0, 2.0, 3.0):
                try:
                    cells.append(run_native_cell(manifest, bars, cell=cell, mode=mode, cost_multiplier=cost_multiplier, launch_started_at=launch_started_at))
                except Exception as exc:  # preserve complete cell status without hiding the failure
                    cells.append({"status": "DATA_BLOCKED", "cell": cell, "mode": mode, "cost_multiplier": cost_multiplier, "error": str(exc), "launch_started_at": launch_started_at})
    execution_manifest_path = _source_path(manifest_path).resolve()
    return {
        "schema_version": "h14-native-research-v1",
        "mission": manifest["mission"],
        "base_protocol": manifest.get("protocol_manifest"),
        "execution_manifest": {
            "path": str(execution_manifest_path.relative_to(REPO)),
            "declared_mission": manifest["mission"],
            "sha256": sha256_file(execution_manifest_path),
        },
        "status": "COMPLETE" if all(item.get("status") == "PASS" for item in cells) else "DATA_BLOCKED",
        "economic_claim": False,
        "canonical_run_requires_independent_pass": True,
        "launch_started_at": launch_started_at,
        "source_pins": {
            "execution_manifest": str(execution_manifest_path.relative_to(REPO)),
            "execution_manifest_sha256": sha256_file(execution_manifest_path),
            "runner": str(Path(__file__).resolve().relative_to(REPO)),
            "runner_sha256": sha256_file(__file__),
            "settings": CONFIG_PATH,
            "settings_sha256": actual_config_sha,
            "bars_adjustment": "split; no second split transformation",
            "corporate_action_mapping": "ex_date UTC midnight -> native DIVIDEND value=rate cash/share",
        },
        "matrix": {"cells": ["full_2022", "recent_2024"], "modes": ["candidate", "passive", "calendar_control"], "cost_multipliers": [1.0, 2.0, 3.0], "cell_count": 18},
        "preflight": preflight,
        "cells": cells,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r2.json")
    parser.add_argument("--cell", choices=sorted(CELLS), default="full_2022")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run", action="store_true", help="run the frozen 18-cell native research matrix")
    args = parser.parse_args()
    if args.run:
        result = run_batch(args.manifest)
    else:
        result = preflight_cell(validate_manifest(args.manifest), args.cell)
    payload = json.dumps(result, indent=2, sort_keys=True, default=str)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
