"""Research-only V3 + information-discreteness complementarity runner.

The runner composes the existing point-in-time data loaders and portfolio
engine.  It does not register a production strategy, modify configuration, or
write PAPER/LIVE/runtime state.  B and C use one union-event ledger: a monthly
candidate reservation is processed before a coincident weekly V3 decision.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any

try:
    from .frozen_candidate_strategy import EquityInformationDiscretenessContinuous1dV1
except ImportError:  # pragma: no cover - direct script execution
    from frozen_candidate_strategy import EquityInformationDiscretenessContinuous1dV1

from parayabuck.backtest.lifetimes import load_entity_lifetimes
from parayabuck.backtest.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
    PortfolioFill,
    RebalanceRecord,
)
from parayabuck.backtest.portfolio_report import PortfolioReport, build_portfolio_report
from parayabuck.backtest.product import (
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
    run_product_backtest,
)
from parayabuck.backtest.stats import sample_stddev
from parayabuck.backtest.universe import PointInTimeUniverse, hash_point_in_time_universe
from parayabuck.config import RuntimeMode, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio import PortfolioStrategy, portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
ENDPOINT = datetime.fromisoformat("2026-07-30T04:00:00+00:00")
DEV_START_DATE = date(2017, 1, 3)
DEV_END_DATE = date(2021, 12, 31)
VAL_START_DATE = date(2022, 1, 3)
VAL_END_DATE = date(2026, 7, 30)
DATA_ROOT = ROOT / "data" / "historical"
BARS_PATH = DATA_ROOT / "equity_pit_symmetric_2016_2026_v5.csv"
LIFETIMES_PATH = DATA_ROOT / "equity_entity_lifetimes_full_v5.csv"
ACTIONS_PATH = DATA_ROOT / "equity_survivorship_full_corporate_actions_v5.csv"
EXPECTED_BARS_SHA = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_LIFETIMES_SHA = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_ACTIONS_SHA = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
EXPECTED_MAIN_SHA = "839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de"
EXPECTED_DESIGN_SHA = "a822083eef4123d960ee7886d52951bfb20b29bd"
EXPECTED_ECONOMIC_SHA = "9dbc44ce9f62b6e2f1b5d233eee77a7fff3df9d3"
POLICY_COMMIT = "cb1b554"
CONTROL_NAME = "equity_residual_momentum_v3"
CANDIDATE_NAME = "equity_information_discreteness_continuous_v1"
EQUITY_ALLOCATION = 0.55
CORE_ALLOCATION = 0.4583333333333333
SATELLITE_ALLOCATION = 0.0916666666666667
CORE_MAX_POSITIONS = 5
ACCOUNT_MAX_POSITIONS = 6


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def _as_float(value: object, default: float = 0.0) -> float:
    """Convert JSON-like values without weakening the verifier's type checks."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_int(value: object, default: int = 0) -> int:
    """Convert a JSON-like rank while rejecting non-numeric values."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _assert_starting_state() -> dict[str, object]:
    branch = _git("branch", "--show-current")
    main = _git("rev-parse", "main")
    origin_main = _git("rev-parse", "origin/main")
    if branch != "research/v3-id-complementarity-v1":
        raise RuntimeError(f"Wrong branch: {branch}")
    if main != EXPECTED_MAIN_SHA or origin_main != EXPECTED_MAIN_SHA:
        raise RuntimeError(f"Unexpected main refs: {main} / {origin_main}")
    if (
        subprocess.run(["git", "merge-base", "--is-ancestor", POLICY_COMMIT, "HEAD"], cwd=ROOT, check=False).returncode
        != 0
    ):
        raise RuntimeError("Frozen policy commit is not an ancestor of the current branch")
    status = _git("status", "--porcelain")
    unexpected = [
        line for line in status.splitlines() if line and not line.endswith("research/v3_id_complementarity_v1/")
    ]
    # The exact path test above is intentionally conservative: all current
    # work is under this research directory, and no production path may be dirty.
    if any(" src/" in line or " config/" in line or " data/" in line for line in unexpected):
        raise RuntimeError("Production/config/data paths are dirty: " + " | ".join(unexpected))
    return {
        "branch": branch,
        "main_sha": main,
        "origin_main_sha": origin_main,
        "policy_commit": POLICY_COMMIT,
        "worktree_status": status,
    }


@dataclass(frozen=True)
class UnionEventPolicy:
    """Research cadence: V3 weekly events union frozen ID monthly events."""

    scheduled_timestamps: frozenset[datetime]
    market: Market = Market.EQUITY
    frequency: str = "weekly_plus_monthly_last_completed_session"

    def __post_init__(self) -> None:
        if self.market != Market.EQUITY or not self.scheduled_timestamps:
            raise ValueError("Union policy requires non-empty equity timestamps")
        if any(item.tzinfo is None for item in self.scheduled_timestamps):
            raise ValueError("Union policy timestamps must be timezone-aware")

    def is_period_start(self, _previous: datetime | None, current: datetime) -> bool:
        return current in self.scheduled_timestamps


def build_last_session_schedule(bars: Iterable[Bar], endpoint: datetime = ENDPOINT) -> tuple[datetime, ...]:
    by_month: dict[tuple[int, int], datetime] = {}
    for bar in bars:
        if bar.market == Market.EQUITY and bar.timeframe == "1d" and bar.symbol == "SPY" and bar.timestamp <= endpoint:
            key = (bar.timestamp.year, bar.timestamp.month)
            by_month[key] = max(by_month.get(key, bar.timestamp), bar.timestamp)
    schedule = tuple(sorted(by_month.values()))
    if not schedule or schedule[-1] != endpoint:
        raise RuntimeError("Frozen endpoint is not the final completed SPY session")
    return schedule


def build_weekly_schedule(bars: Iterable[Bar], endpoint: datetime = ENDPOINT) -> tuple[datetime, ...]:
    first_by_week: dict[tuple[int, int], datetime] = {}
    timestamps = sorted(
        {
            bar.timestamp
            for bar in bars
            if bar.market == Market.EQUITY and bar.timeframe == "1d" and bar.timestamp <= endpoint
        }
    )
    for timestamp in timestamps:
        iso = timestamp.isocalendar()
        key = (iso.year, iso.week)
        first_by_week.setdefault(key, timestamp)
    schedule = tuple(sorted(first_by_week.values()))
    if not schedule:
        raise RuntimeError("Weekly schedule is empty")
    return schedule


def build_matched_observation_universe(universe: PointInTimeUniverse) -> PointInTimeUniverse:
    """Keep the frozen 30-symbol observation basis plus managed pins.

    The production stream capacity is 30.  B/C need a research-only parity
    wrapper because C owns one extra managed satellite position; otherwise the
    engine's managed-pin prepend can evict a different ordinary symbol from C's
    context.  A capacity of 64 is still far below the account/data universe,
    preserves all scanner-selected base observations, and leaves room for
    account-managed pins without changing the signal basis.
    """
    snapshots = tuple(
        replace(snapshot, maximum_stream_symbols=max(64, len(snapshot.observation_set)))
        for snapshot in universe.snapshots
    )
    return PointInTimeUniverse(
        universe.market, snapshots, universe.methodology + "; matched_bc_observation_capacity_v1"
    )


class CoreSatelliteStrategy(PortfolioStrategy):
    """Research allocator with shared V3 core and optional ID satellite."""

    name = "v3_id_complementarity_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "V3 core + information-discreteness satellite (research)"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "weekly_plus_monthly_last_completed_session"

    def __init__(
        self,
        *,
        mode: str,
        monthly_schedule: tuple[datetime, ...],
        weekly_schedule: tuple[datetime, ...],
        v3_parameters: dict[str, object],
    ) -> None:
        if mode not in {"B", "C"}:
            raise ValueError("mode must be B or C")
        self.mode = mode
        self.monthly_schedule = frozenset(monthly_schedule)
        self.weekly_schedule = frozenset(weekly_schedule)
        self.v3 = portfolio_strategy_from(CONTROL_NAME, dict(v3_parameters), timeframe="1d")
        self.candidate = EquityInformationDiscretenessContinuous1dV1(
            {
                "formation_bars": 252,
                "skip_bars": 21,
                "top_n": 6,
                "max_single_weight": 0.10,
                "maximum_allocation": 0.55,
            }
        )
        self.reservation_symbol: str | None = None
        self.reservation_record: dict[str, object] | None = None
        self.core_targets: dict[str, TargetWeight] = {}
        self.core_target_history: list[dict[str, object]] = []
        self.reservation_audit: list[dict[str, object]] = []
        self.decision_source_maps: dict[datetime, dict[str, str]] = {}
        self.decision_metadata: list[dict[str, object]] = []
        self._last_reservation_event: datetime | None = None
        super().__init__({"mode": mode, "architecture": "5+1"})

    @property
    def minimum_history_bars(self) -> int:
        return max(int(self.v3.minimum_history_bars), int(self.candidate.minimum_history_bars))

    def _validate_parameters(self) -> None:
        if self.parameters.get("architecture") != "5+1":
            raise ValueError("Research architecture is frozen at 5+1")

    def _update_reservation(self, context: PortfolioContext) -> None:
        candidate_target = self.candidate.decide(context)
        selected_raw = candidate_target.metadata.get("selected_symbols", ())
        selected = tuple(str(symbol) for symbol in selected_raw) if isinstance(selected_raw, (list, tuple)) else ()
        old = self.reservation_symbol
        self.reservation_symbol = selected[0] if selected else None
        records_raw = candidate_target.metadata.get("id_signal_records", ())
        records = records_raw if isinstance(records_raw, list) else []
        row = next(
            (item for item in records if isinstance(item, dict) and item.get("symbol") == self.reservation_symbol), None
        )
        self.reservation_record = dict(row) if row is not None else None
        self._last_reservation_event = context.decision_timestamp
        self.reservation_audit.append(
            {
                "decision_timestamp": context.decision_timestamp.isoformat(),
                "selected_top_six": list(selected),
                "qualified_count": int(candidate_target.metadata.get("qualified_symbols", 0)),
                "previous_reservation": old,
                "reservation_symbol": self.reservation_symbol,
                "reservation_changed": old != self.reservation_symbol,
                "reservation_record": self.reservation_record,
            }
        )

    @staticmethod
    def _signal_context(context: PortfolioContext) -> PortfolioContext:
        """Keep B/C signal inputs identical despite different managed pins.

        The engine correctly prepends managed positions to its live-parity
        observation context. C owns one extra position, so that operational
        pin would otherwise change the V3/ID cross-section and violate the
        matched-control design. Signals use the frozen snapshot observation
        set; the original context remains available to the engine risk overlay
        so managed positions can still be reduced or closed.
        """
        snapshots = getattr(context.universe, "snapshots", ())
        if not snapshots:
            return context
        index = bisect_right(snapshots, context.decision_timestamp, key=lambda item: item.effective_at)
        if index == 0:
            return context
        snapshot = snapshots[index - 1]
        base = set(getattr(snapshot, "observation_set", ()) or snapshot.symbols)
        base.add("SPY")
        history = {symbol: bars for symbol, bars in context.history.items() if symbol in base}
        prices = {symbol: price for symbol, price in context.prices.items() if symbol in base}
        return replace(context, history=history, prices=prices)

    def _core_decision(self, context: PortfolioContext) -> None:
        ranked, residual_vols = self.v3._score_candidates(context)
        filtered = tuple((score, symbol) for score, symbol in ranked if symbol != self.reservation_symbol)
        selected = tuple(symbol for _score, symbol in filtered[:CORE_MAX_POSITIONS])
        raw = dict(self.v3._position_weights(selected, residual_vols))
        raw_total = sum(raw.values())
        scale = min(1.0, CORE_ALLOCATION / raw_total) if raw_total > 0 else 1.0
        source_rank = {symbol: index for index, (_score, symbol) in enumerate(ranked, start=1)}
        self.core_targets = {
            symbol: TargetWeight(
                symbol=symbol,
                weight=min(0.10, weight * scale),
                reason="v3_core_residual_momentum",
                rank=source_rank[symbol],
            )
            for symbol, weight in raw.items()
            if weight > 0
        }
        self.decision_metadata.append(
            {
                "decision_timestamp": context.decision_timestamp.isoformat(),
                "v3_event": True,
                "reservation_symbol": self.reservation_symbol,
                "ranked_count": len(ranked),
                "selected_core_symbols": list(selected),
                "core_raw_weights": raw,
                "core_scale": scale,
                "core_budget": CORE_ALLOCATION,
            }
        )

    def _prune_reserved_core(self) -> None:
        if self.reservation_symbol is not None:
            self.core_targets.pop(self.reservation_symbol, None)

    def _pre_core_weight(self, symbol: str, fallback: float) -> float:
        for meta in reversed(self.decision_metadata):
            raw = meta.get("core_raw_weights")
            if isinstance(raw, dict):
                value = raw.get(symbol)
                if isinstance(value, (int, float)):
                    return float(value)
        return fallback

    def _target(self, context: PortfolioContext, *, monthly: bool, weekly: bool) -> TargetPortfolio:
        weights = dict(self.core_targets)
        source_map = {symbol: "v3_core" for symbol in weights}
        satellite_meta: dict[str, object] = {
            "active": False,
            "target_weight": 0.0,
            "reservation_symbol": self.reservation_symbol,
        }
        if self.mode == "C" and self.reservation_symbol is not None:
            if self.reservation_symbol in weights:
                raise RuntimeError("Reservation overlap with V3 core target")
            satellite_weight = TargetWeight(
                symbol=self.reservation_symbol,
                weight=SATELLITE_ALLOCATION,
                reason="information_discreteness_satellite",
                rank=_as_int(self.reservation_record.get("rank", 0) if self.reservation_record else 0),
            )
            weights[self.reservation_symbol] = satellite_weight
            source_map[self.reservation_symbol] = "id_satellite"
            satellite_meta = {
                "active": True,
                "target_weight": SATELLITE_ALLOCATION,
                "reservation_symbol": self.reservation_symbol,
                "candidate_score": self.reservation_record.get("id") if self.reservation_record else None,
                "id": self.reservation_record.get("id") if self.reservation_record else None,
                "pret": self.reservation_record.get("pret") if self.reservation_record else None,
                "candidate_rank": self.reservation_record.get("rank") if self.reservation_record else None,
            }
        ordered = tuple(sorted(weights.values(), key=lambda item: item.symbol))
        if len(ordered) > ACCOUNT_MAX_POSITIONS:
            raise RuntimeError("5+1 allocator exceeded the six-position account limit")
        total = sum(item.weight for item in ordered)
        target = TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=ordered,
            cash_weight=max(0.0, 1.0 - total),
            rebalance_reason=RebalanceReason.SCHEDULED if ordered else RebalanceReason.RISK_OFF,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "mode": self.mode,
                "event_type": "monthly_weekly" if monthly and weekly else ("monthly" if monthly else "weekly"),
                "reservation_symbol": self.reservation_symbol,
                "v3_core": [
                    {
                        "symbol": item.symbol,
                        "source_rank": item.rank,
                        "pre_core_target_weight": self._pre_core_weight(item.symbol, item.weight),
                        "final_core_target_weight": item.weight,
                    }
                    for item in ordered
                    if source_map[item.symbol] == "v3_core"
                ],
                "satellite": satellite_meta,
                "source_map": source_map,
                "monthly_candidate_decision": monthly,
                "weekly_v3_decision": weekly,
            },
        )
        self.core_target_history.append(
            {
                "decision_timestamp": context.decision_timestamp.isoformat(),
                "core_weights": {symbol: item.weight for symbol, item in sorted(self.core_targets.items())},
                "reservation_symbol": self.reservation_symbol,
                "monthly": monthly,
                "weekly": weekly,
            }
        )
        self.decision_source_maps[context.decision_timestamp] = source_map
        return target

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        timestamp = context.decision_timestamp
        monthly = timestamp in self.monthly_schedule
        weekly = timestamp in self.weekly_schedule
        if not monthly and not weekly:
            raise RuntimeError(f"Union policy called outside a frozen event: {timestamp.isoformat()}")
        # Frozen event order: monthly reservation first, then same-timestamp V3.
        signal_context = self._signal_context(context)
        if monthly:
            self._update_reservation(signal_context)
            self._prune_reserved_core()
        if weekly:
            self._core_decision(signal_context)
        return self._target(context, monthly=monthly, weekly=weekly)

    def audit_payload(self) -> dict[str, object]:
        return {
            "candidate_events": self.candidate.audit_payload(),
            "reservation_audit": self.reservation_audit,
            "core_target_history": self.core_target_history,
            "decision_metadata": self.decision_metadata,
        }


def _bar_window_inputs(bars: list[Bar]) -> tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]], object | None]:
    filtered, warmup, prepared = _prepared_window(bars, Market.EQUITY, "1d", None, ENDPOINT)
    numeric_history: object | None = None
    try:
        from parayabuck.backtest.numeric_history import build_numeric_history

        numeric_history = build_numeric_history(prepared[0], market=Market.EQUITY, timeframe="1d")
    except Exception:
        numeric_history = None
    return filtered, warmup, prepared, numeric_history


def _make_engine(
    config: Any,
    strategy: PortfolioStrategy,
    policy: Any,
    universe: PointInTimeUniverse,
    terminal_lifecycles: Mapping[str, Any],
    cost_multiplier: float,
) -> PortfolioBacktestEngine:
    selection = config.strategy.equity
    return PortfolioBacktestEngine(
        strategy,
        policy,
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        equity_regulatory_fee_multiplier=cost_multiplier,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=selection.maximum_allocation,
        ordinary_alpha_excluded_symbols=config.universe.equity.ordinary_alpha_excluded_symbols,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        terminal_lifecycles=terminal_lifecycles,
    )


def _json_rebalance(record: RebalanceRecord) -> dict[str, object]:
    return {
        "decision_timestamp": record.decision_timestamp.isoformat(),
        "reason": record.reason,
        "target_weights": [[symbol, weight] for symbol, weight in record.target_weights],
        "target_cash_weight": record.target_cash_weight,
        "pre_trade_weights": [[symbol, weight] for symbol, weight in record.pre_trade_weights],
        "signed_trade_weights": [[symbol, weight] for symbol, weight in record.signed_trade_weights],
        "post_fill_actual_weights": [[symbol, weight] for symbol, weight in record.post_fill_actual_weights],
        "target_vs_actual_drift": record.target_vs_actual_drift,
        "unfilled_notional": record.unfilled_notional,
        "rejected_notional": record.rejected_notional,
        "expected_turnover": record.expected_turnover,
        "realized_turnover": record.realized_turnover,
        "cost": record.cost,
        "executed_weights": [[symbol, weight] for symbol, weight in record.executed_weights],
        "universe_version": record.universe_version,
        "universe_snapshot_id": record.universe_snapshot_id,
        "equity_at_decision": record.equity_at_decision,
        "rebalance_number": record.rebalance_number,
        "risk_overlay_halted": record.risk_overlay_halted,
        "risk_overlay_reasons": list(record.risk_overlay_reasons),
    }


def _raw_result_payload(
    result: PortfolioBacktestResult, report: PortfolioReport, *, arm: str, multiplier: float
) -> dict[str, object]:
    return {
        "arm": arm,
        "cost_multiplier": multiplier,
        "summary": report.summary,
        "equity_curve": [
            {"timestamp": timestamp.isoformat(), "equity": equity}
            for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True)
        ],
        "weight_history": [
            {"timestamp": timestamp.isoformat(), "weights": weights} for timestamp, weights in result.weight_history
        ],
        "rebalances": [_json_rebalance(record) for record in result.rebalances],
        "fills": [
            {
                "timestamp": fill.timestamp.isoformat(),
                "symbol": fill.symbol,
                "market": fill.market.value,
                "side": fill.side.value,
                "qty": fill.qty,
                "reference_price": fill.reference_price,
                "fill_price": fill.fill_price,
                "slippage_cost": fill.slippage_cost,
                "spread_cost": fill.spread_cost,
                "fee": fill.fee,
                "decision_timestamp": fill.decision_timestamp.isoformat(),
            }
            for fill in result.fills
        ],
        "closed_position_events": [
            {"timestamp": timestamp.isoformat(), "symbol": symbol, "pnl": pnl}
            for timestamp, symbol, pnl in result.closed_position_events
        ],
        "realized_pnl_events": [
            {"timestamp": timestamp.isoformat(), "symbol": symbol, "pnl": pnl}
            for timestamp, symbol, pnl in result.realized_pnl_events
        ],
        "terminal_events": list(result.terminal_events),
        "per_symbol": [
            {
                "symbol": contribution.symbol,
                "market": contribution.market.value,
                "realized_pnl": contribution.realized_pnl,
                "fees": contribution.fees,
                "final_weight": contribution.final_weight,
            }
            for contribution in result.per_symbol
        ],
        "dividend_total": result.dividend_total,
        "universe_hash": report.summary.get("universe_hash"),
        "benchmark_symbol": result.benchmark_symbol,
    }


def _period_metrics(result: PortfolioBacktestResult, start_date: date, end_date: date) -> dict[str, object]:
    points = [
        (timestamp, value)
        for timestamp, value in zip(result.curve_timestamps, result.equity_curve, strict=True)
        if start_date <= timestamp.date() <= end_date
    ]
    if not points:
        raise RuntimeError(f"No curve points for {start_date}..{end_date}")
    curve = [value for _timestamp, value in points]
    timestamps = [timestamp for timestamp, _value in points]
    returns = [curve[index] / curve[index - 1] - 1.0 for index in range(1, len(curve)) if curve[index - 1] > 0]
    downside = [value for value in returns if value < 0]
    years = max((timestamps[-1] - timestamps[0]).total_seconds() / (365.0 * 86_400), 1.0 / 365.0)
    total_return = curve[-1] / curve[0] - 1.0 if curve[0] > 0 else 0.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1 else 0.0
    std = sample_stddev(returns)
    downside_std = sample_stddev(downside)
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if returns else 1.0
    peak = curve[0]
    max_drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    monthly_ends: dict[tuple[int, int], float] = {}
    for timestamp, value in points:
        monthly_ends[(timestamp.year, timestamp.month)] = value
    monthly_returns: list[float] = []
    previous = curve[0]
    for key in sorted(monthly_ends):
        monthly_returns.append(monthly_ends[key] / previous - 1.0 if previous > 0 else 0.0)
        previous = monthly_ends[key]
    records = [
        record
        for record in result.rebalances
        if start_date <= record.decision_timestamp.date() <= end_date and record.reason == "scheduled"
    ]
    exposures = [sum(weight for weight in dict(record.target_weights).values()) for record in records]
    positions = [sum(weight > 0 for weight in dict(record.target_weights).values()) for record in records]
    turnover = sum(record.realized_turnover for record in records)
    costs = {
        "slippage": sum(fill.slippage_cost for fill in result.fills if start_date <= fill.timestamp.date() <= end_date),
        "spread": sum(fill.spread_cost for fill in result.fills if start_date <= fill.timestamp.date() <= end_date),
        "fees": sum(fill.fee for fill in result.fills if start_date <= fill.timestamp.date() <= end_date),
    }
    return {
        "start": timestamps[0].isoformat(),
        "end": timestamps[-1].isoformat(),
        "start_equity": curve[0],
        "end_equity": curve[-1],
        "net_total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": (sum(returns) / len(returns) / std * annualizer) if returns and std > 0 else 0.0,
        "sortino": (sum(returns) / len(returns) / downside_std * annualizer) if returns and downside_std > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "worst_month": min(monthly_returns, default=0.0),
        "positive_month_ratio": sum(value > 0 for value in monthly_returns) / len(monthly_returns)
        if monthly_returns
        else 0.0,
        "scheduled_decisions": len(records),
        "actual_fills": sum(1 for fill in result.fills if start_date <= fill.timestamp.date() <= end_date),
        "average_exposure": mean(exposures) if exposures else 0.0,
        "average_positions": mean(positions) if positions else 0.0,
        "turnover_one_way_annualized": turnover / years,
        "realized_turnover": turnover,
        "transaction_costs": costs,
        "transaction_cost_total": sum(costs.values()),
        "duration_days": (timestamps[-1] - timestamps[0]).total_seconds() / 86_400,
    }


def _metrics_for_result(result: PortfolioBacktestResult) -> dict[str, dict[str, object]]:
    return {
        "development": _period_metrics(result, DEV_START_DATE, DEV_END_DATE),
        "validation": _period_metrics(result, VAL_START_DATE, VAL_END_DATE),
        "full": _period_metrics(result, DEV_START_DATE, VAL_END_DATE),
    }


def _source_map_for_timestamp(strategy: CoreSatelliteStrategy, timestamp: datetime) -> dict[str, str]:
    candidates = [key for key in strategy.decision_source_maps if key <= timestamp]
    return strategy.decision_source_maps[max(candidates)] if candidates else {}


def _source_attribution(
    result: PortfolioBacktestResult,
    strategy: CoreSatelliteStrategy,
    actions: list[CorporateAction],
) -> dict[str, object]:
    source_realized: dict[str, float] = defaultdict(float)
    source_costs: dict[str, float] = defaultdict(float)
    source_events: list[dict[str, object]] = []
    cost_events: list[dict[str, object]] = []
    for timestamp, symbol, pnl in result.realized_pnl_events:
        source = _source_map_for_timestamp(strategy, timestamp).get(symbol, "v3_core")
        source_realized[source] += pnl
        source_events.append({"timestamp": timestamp.isoformat(), "symbol": symbol, "source": source, "net_pnl": pnl})
    for fill in result.fills:
        source = _source_map_for_timestamp(strategy, fill.decision_timestamp).get(fill.symbol, "v3_core")
        cost = fill.slippage_cost + fill.spread_cost + fill.fee
        source_costs[source] += cost
        cost_events.append(
            {"timestamp": fill.timestamp.isoformat(), "symbol": fill.symbol, "source": source, "cost": cost}
        )
    for event in result.terminal_events:
        timestamp = datetime.fromisoformat(str(event["timestamp"]))
        source = _source_map_for_timestamp(strategy, timestamp).get(str(event["symbol"]), "v3_core")
        cost = (
            float(event.get("slippage_cost", 0.0)) + float(event.get("spread_cost", 0.0)) + float(event.get("fee", 0.0))
        )
        source_costs[source] += cost
        cost_events.append(
            {"timestamp": timestamp.isoformat(), "symbol": event["symbol"], "source": source, "cost": cost}
        )

    # Dividend attribution is reconstructed from the actual fill inventory and
    # source labels; it is not a synthetic sleeve NAV.  Split actions adjust
    # quantities/cost labels before subsequent dividends or fills.
    lots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dividends: list[dict[str, object]] = []
    events: list[tuple[datetime, int, CorporateAction | PortfolioFill]] = []
    for action in actions:
        if action.timestamp <= ENDPOINT:
            events.append((action.timestamp, 0, action))
    for fill in result.fills:
        events.append((fill.timestamp, 1, fill))
    for timestamp, _order, event in sorted(events, key=lambda item: (item[0], item[1])):
        if isinstance(event, CorporateAction):
            symbol = event.symbol
            if event.action_type == CorporateActionType.SPLIT:
                for lot in lots[symbol]:
                    lot["qty"] = float(lot["qty"]) * event.value
                    lot["price"] = float(lot["price"]) / event.value
            elif event.action_type == CorporateActionType.DIVIDEND:
                for lot in lots[symbol]:
                    amount = float(lot["qty"]) * event.value
                    source = str(lot["source"])
                    source_realized[source] += amount
                    dividends.append(
                        {"timestamp": timestamp.isoformat(), "symbol": symbol, "source": source, "amount": amount}
                    )
            continue
        fill = event
        source_now = _source_map_for_timestamp(strategy, fill.decision_timestamp).get(fill.symbol, "v3_core")
        if fill.side.value == "buy":
            lots[fill.symbol].append({"qty": fill.qty, "price": fill.fill_price, "source": source_now})
            continue
        remaining = fill.qty
        while remaining > 1e-10 and lots[fill.symbol]:
            lot = lots[fill.symbol][0]
            take = min(remaining, float(lot["qty"]))
            lot["qty"] = float(lot["qty"]) - take
            remaining -= take
            if float(lot["qty"]) <= 1e-10:
                lots[fill.symbol].pop(0)
    total_realized = sum(source_realized.values())
    expected_realized = sum(pnl for _timestamp, _symbol, pnl in result.realized_pnl_events) + result.dividend_total
    # Small floating point drift is tolerated, but a material mismatch is an
    # invalid attribution rather than a silently discarded source.
    if not math.isclose(total_realized, expected_realized, rel_tol=1e-8, abs_tol=1e-5):
        raise RuntimeError(f"Source attribution mismatch: {total_realized} != {expected_realized}")
    source_rows: dict[str, dict[str, object]] = {}
    source_symbol_pnl: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for event in source_events:
        source_symbol_pnl[str(event["source"])][str(event["symbol"])] += _as_float(event.get("net_pnl"))
    for event in dividends:
        source_symbol_pnl[str(event["source"])][str(event["symbol"])] += _as_float(event.get("amount"))
    for source in ("v3_core", "id_satellite"):
        net = source_realized.get(source, 0.0)
        costs = source_costs.get(source, 0.0)
        source_rows[source] = {
            "net_pnl": net,
            "transaction_costs": costs,
            "gross_trading_contribution": net
            - sum(_as_float(item.get("amount")) for item in dividends if item.get("source") == source)
            + costs,
            "dividend_pnl": sum(_as_float(item.get("amount")) for item in dividends if item.get("source") == source),
            "symbol_net_pnl": dict(sorted(source_symbol_pnl.get(source, {}).items())),
        }
    return {
        "sources": source_rows,
        "realized_events": source_events,
        "dividends": dividends,
        "cost_events": cost_events,
        "total_realized_plus_dividends": total_realized,
        "engine_realized_plus_dividends": expected_realized,
    }


def _exposure_audit(strategy: CoreSatelliteStrategy, result: PortfolioBacktestResult) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for record in strategy.core_target_history:
        raw_core = record.get("core_weights")
        core = (
            {str(symbol): float(value) for symbol, value in raw_core.items() if isinstance(value, (int, float))}
            if isinstance(raw_core, dict)
            else {}
        )
        satellite = record.get("reservation_symbol") if strategy.mode == "C" else None
        rows.append(
            {
                "decision_timestamp": record["decision_timestamp"],
                "intended_v3_core_allocation": sum(float(value) for value in core.values()),
                "realized_v3_core_target_exposure": sum(float(value) for value in core.values()),
                "intended_satellite_allocation": SATELLITE_ALLOCATION if satellite else 0.0,
                "realized_satellite_target_exposure": SATELLITE_ALLOCATION
                if satellite and strategy.mode == "C"
                else 0.0,
                "cash": 1.0
                - sum(float(value) for value in core.values())
                - (SATELLITE_ALLOCATION if satellite and strategy.mode == "C" else 0.0),
                "v3_core_names": len(core),
                "reservation_symbol": satellite,
                "satellite_active": bool(satellite and strategy.mode == "C"),
                "unused_budget_reason": "matched_cash_control"
                if strategy.mode == "B"
                else ("no_valid_reservation" if not satellite else "none"),
            }
        )
    return {
        "mode": strategy.mode,
        "decision_rows": rows,
        "maximum_account_positions": max(
            sum(weight > 0 for weight in weights.values()) for _timestamp, weights in result.weight_history
        ),
        "maximum_target_weight": max(
            max(weights.values(), default=0.0) for _timestamp, weights in result.weight_history
        ),
    }


def _diagnostics(
    a: PortfolioBacktestResult, strategy: CoreSatelliteStrategy, c: PortfolioBacktestResult
) -> dict[str, object]:
    a_returns = {
        timestamp.date(): a.equity_curve[index] / a.equity_curve[index - 1] - 1.0
        for index, timestamp in enumerate(a.curve_timestamps)
        if index > 0 and a.equity_curve[index - 1] > 0
    }
    c_returns = {
        timestamp.date(): c.equity_curve[index] / c.equity_curve[index - 1] - 1.0
        for index, timestamp in enumerate(c.curve_timestamps)
        if index > 0 and c.equity_curve[index - 1] > 0
    }
    common = sorted(set(a_returns) & set(c_returns))
    covariance = 0.0
    corr = 0.0
    if len(common) > 2:
        left = [a_returns[key] for key in common]
        right = [c_returns[key] for key in common]
        lm, rm = mean(left), mean(right)
        denom = math.sqrt(sum((x - lm) ** 2 for x in left) * sum((y - rm) ** 2 for y in right))
        covariance = sum((x - lm) * (y - rm) for x, y in zip(left, right, strict=True)) / (len(common) - 1)
        corr = covariance * (len(common) - 1) / denom if denom else 0.0
    reserved = [row.get("reservation_symbol") for row in strategy.reservation_audit if row.get("reservation_symbol")]
    return {
        "daily_return_correlation_control_a_vs_c": corr,
        "daily_common_observations": len(common),
        "reservation_distinct_symbols": len(set(str(item) for item in reserved)),
        "reservation_symbols": sorted(set(str(item) for item in reserved)),
        "interpretation": "descriptive only; no acceptance gate",
    }


def _write_outputs(
    *,
    config: Any,
    state: dict[str, object],
    universe_a: PointInTimeUniverse,
    universe_bc: PointInTimeUniverse,
    schedules: dict[str, tuple[datetime, ...]],
    metrics: dict[str, dict[str, dict[str, dict[str, object]]]],
    results: dict[str, dict[str, PortfolioBacktestResult]],
    reports: dict[str, dict[str, PortfolioReport]],
    strategies: dict[str, CoreSatelliteStrategy],
    strategy_runs: dict[str, dict[str, CoreSatelliteStrategy]],
    actions: list[CorporateAction],
) -> None:
    _json_dump(
        OUTPUT / "RUN_MANIFEST.json",
        {
            "experiment": "v3_id_complementarity_v1",
            "endpoint": ENDPOINT.isoformat(),
            "development_dates": [DEV_START_DATE.isoformat(), DEV_END_DATE.isoformat()],
            "validation_dates": [VAL_START_DATE.isoformat(), VAL_END_DATE.isoformat()],
            "cost_multipliers": [1.0, 2.0, 3.0],
            "weekly_schedule_count": len(schedules["weekly"]),
            "monthly_schedule_count": len(schedules["monthly"]),
            "schedule_policy": "weekly V3 union last completed regular SPY session monthly ID reservation",
            "paper_identity": config.strategy.equity.name,
            "runtime_mode": config.runtime.mode.value,
            "live_enabled": config.live.enabled,
            "branch_state": state,
            "data": {
                "bars": {"path": str(BARS_PATH), "sha256": _sha256(BARS_PATH)},
                "lifetimes": {"path": str(LIFETIMES_PATH), "sha256": _sha256(LIFETIMES_PATH)},
                "actions": {"path": str(ACTIONS_PATH), "sha256": _sha256(ACTIONS_PATH)},
            },
            "universe_hashes": {
                "control_a_v3": hash_point_in_time_universe(universe_a),
                "control_b_treatment_c_candidate_observation": hash_point_in_time_universe(universe_bc),
            },
            "policy_sha256": _sha256(OUTPUT / "CORE_SATELLITE_COMBINATION_POLICY_V1.md"),
            "candidate_source_sha256": _sha256(OUTPUT / "frozen_candidate_strategy.py"),
            "production_changes": False,
            "paper_live_changes": False,
            "broker_orders": False,
        },
    )
    _json_dump(
        OUTPUT / "SCHEDULE.json", {name: [item.isoformat() for item in values] for name, values in schedules.items()}
    )
    _json_dump(OUTPUT / "METRICS.json", metrics)
    for arm, per_cost in results.items():
        for label, result in per_cost.items():
            _json_dump(
                OUTPUT / f"{arm.upper()}_{label.replace('.', 'P').upper()}_RESULTS.json",
                _raw_result_payload(result, reports[arm][label], arm=arm, multiplier=float(label.rstrip("x"))),
            )
    for mode, strategy in strategies.items():
        _json_dump(OUTPUT / f"{mode}_RESERVATION_AUDIT.json", strategy.reservation_audit)
        _json_dump(OUTPUT / f"{mode}_CORE_TARGET_AUDIT.json", strategy.core_target_history)
        _json_dump(OUTPUT / f"{mode}_DECISION_METADATA.json", strategy.decision_metadata)
        if mode == "C":
            _json_dump(OUTPUT / "ID_SIGNAL_AUDIT.json", strategy.candidate.audit_payload())
    parity_rows: list[dict[str, object]] = []
    b_history: dict[str, dict[str, object]] = {
        str(row["decision_timestamp"]): row for row in strategies["B"].core_target_history
    }
    c_history: dict[str, dict[str, object]] = {
        str(row["decision_timestamp"]): row for row in strategies["C"].core_target_history
    }
    for timestamp in sorted(set(b_history) | set(c_history)):
        b_core = b_history.get(timestamp, {}).get("core_weights", {})
        c_core = c_history.get(timestamp, {}).get("core_weights", {})
        parity_rows.append(
            {
                "decision_timestamp": timestamp,
                "b_core_weights": b_core,
                "c_core_weights": c_core,
                "equal": b_core == c_core,
            }
        )
    _json_dump(
        OUTPUT / "BC_CORE_PARITY.json", {"passed": all(row["equal"] for row in parity_rows), "rows": parity_rows}
    )
    attributions: dict[str, object] = {}
    for label, per_mode in strategy_runs.items():
        attributions[label] = {
            arm: _source_attribution(results["control_b" if arm == "B" else "treatment_c"][label], strategy, actions)
            for arm, strategy in per_mode.items()
        }
    _json_dump(OUTPUT / "SOURCE_ATTRIBUTION.json", attributions)
    exposure = {
        mode: {"1x": _exposure_audit(strategies[mode], results["control_b" if mode == "B" else "treatment_c"]["1x"])}
        for mode in ("B", "C")
    }
    _json_dump(OUTPUT / "EXPOSURE_SLOT_AUDIT.json", exposure)
    _json_dump(
        OUTPUT / "DIVERSIFICATION_DIAGNOSTICS.json",
        _diagnostics(results["control_a"]["1x"], strategies["C"], results["treatment_c"]["1x"]),
    )


def main() -> int:
    state = _assert_starting_state()
    config = load_config()
    if config.runtime.mode != RuntimeMode.PAPER or config.strategy.equity.name != CONTROL_NAME or config.live.enabled:
        raise RuntimeError("PAPER identity or LIVE boundary changed")
    for path, expected in (
        (BARS_PATH, EXPECTED_BARS_SHA),
        (LIFETIMES_PATH, EXPECTED_LIFETIMES_SHA),
        (ACTIONS_PATH, EXPECTED_ACTIONS_SHA),
    ):
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen data hash mismatch: {path} {actual}")
    if (
        _sha256(OUTPUT / "CORE_SATELLITE_COMBINATION_POLICY_V1.md")
        != "e700f1971a8014838184d56930cc50065d864487f77da391b7e56383f105d225"
    ):
        raise RuntimeError("Frozen policy hash changed")
    bars = load_csv_bars(BARS_PATH, default_market=Market.EQUITY)
    print("loaded bars", len(bars), flush=True)
    monthly = build_last_session_schedule(bars)
    weekly = build_weekly_schedule(bars)
    union = tuple(sorted(set(monthly) | set(weekly)))
    lifetimes = load_entity_lifetimes(LIFETIMES_PATH)
    actions = load_csv_corporate_actions(ACTIONS_PATH)
    v3 = portfolio_strategy_from(CONTROL_NAME, config.strategy.equity.parameters, timeframe="1d")
    candidate = EquityInformationDiscretenessContinuous1dV1(
        {
            "formation_bars": 252,
            "skip_bars": 21,
            "top_n": 6,
            "max_single_weight": 0.10,
            "maximum_allocation": 0.55,
        }
    )
    universe_a = build_product_universe(
        config, bars, market=Market.EQUITY, end=ENDPOINT, timeframe="1d", observation_strategy=v3
    )
    print("built control universe", len(universe_a.snapshots), flush=True)
    universe_bc = build_product_universe(
        config, bars, market=Market.EQUITY, end=ENDPOINT, timeframe="1d", observation_strategy=candidate
    )
    universe_bc = build_matched_observation_universe(universe_bc)
    print("built candidate universe", len(universe_bc.snapshots), flush=True)
    prepared = _bar_window_inputs(bars)
    terminal_lifecycles = lifetimes.engine_terminal_lifecycles()
    schedules = {"monthly": monthly, "weekly": weekly, "union": union}
    metrics: dict[str, dict[str, dict[str, dict[str, object]]]] = {
        "control_a": {},
        "control_b": {},
        "treatment_c": {},
    }
    results: dict[str, dict[str, PortfolioBacktestResult]] = {"control_a": {}, "control_b": {}, "treatment_c": {}}
    reports: dict[str, dict[str, PortfolioReport]] = {"control_a": {}, "control_b": {}, "treatment_c": {}}
    strategies: dict[str, CoreSatelliteStrategy] = {}
    strategy_runs: dict[str, dict[str, CoreSatelliteStrategy]] = {}
    for multiplier in (1.0, 2.0, 3.0):
        label = f"{multiplier:g}x"
        print("running", label, flush=True)
        control = run_product_backtest(
            config,
            bars,
            market=Market.EQUITY,
            strategy_name=CONTROL_NAME,
            parameters=config.strategy.equity.parameters,
            strategy_timeframe="1d",
            end=ENDPOINT,
            cost_multiplier=multiplier,
            persist=False,
            persist_artifacts=False,
            corporate_actions=actions,
            universe=universe_a,
            prepared_inputs=prepared[:3],
            numeric_history=prepared[3],
            terminal_lifecycles=terminal_lifecycles,
        )
        results["control_a"][label] = control.result
        reports["control_a"][label] = control.report
        metrics["control_a"][label] = _metrics_for_result(control.result)
        for arm, mode in (("control_b", "B"), ("treatment_c", "C")):
            strategy = CoreSatelliteStrategy(
                mode=mode,
                monthly_schedule=monthly,
                weekly_schedule=weekly,
                v3_parameters=config.strategy.equity.parameters,
            )
            engine = _make_engine(
                config, strategy, UnionEventPolicy(frozenset(union)), universe_bc, terminal_lifecycles, multiplier
            )
            filtered, warmup, inputs, numeric = prepared
            result = engine.run(
                filtered,
                warmup=warmup,
                prepared_inputs=inputs,
                numeric_history=numeric,
                corporate_actions=actions,
            )
            report = build_portfolio_report(result, strategy_name=strategy.name, strategy_version=strategy.version)
            results[arm][label] = result
            reports[arm][label] = report
            metrics[arm][label] = _metrics_for_result(result)
            strategy_runs.setdefault(label, {})[mode] = strategy
            if multiplier == 1.0:
                strategies[mode] = strategy
    _write_outputs(
        config=config,
        state=state,
        universe_a=universe_a,
        universe_bc=universe_bc,
        schedules=schedules,
        metrics=metrics,
        results=results,
        reports=reports,
        strategies=strategies,
        strategy_runs=strategy_runs,
        actions=actions,
    )
    verifier = subprocess.run([sys.executable, str(OUTPUT / "combination_verifier.py")], cwd=ROOT, check=False)
    return verifier.returncode


if __name__ == "__main__":
    raise SystemExit(main())
