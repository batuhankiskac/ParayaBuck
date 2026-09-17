"""Reproducible research campaign for equity_v3_volatility_managed_v1.

This module is intentionally outside the production package.  It prepares
one frozen point-in-time experiment, runs A/B/C under the existing portfolio
engine, and leaves a complete receipt for the independent verifier.
"""

from __future__ import annotations

import bisect
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from parayabuck.backtest.numeric_history import build_numeric_history
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
from parayabuck.backtest.portfolio_report import build_portfolio_report
from parayabuck.backtest.product import _prepared_window, build_product_universe, hash_point_in_time_universe
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio import rebalance_policy_for, portfolio_strategy_from

try:
    from .candidate import (
        AuditedControlV3,
        CostStressPortfolioBacktestEngine,
        DynamicAllocationRiskOverlay,
        ScalerObservation,
        V3_MAXIMUM_ALLOCATION,
        VolatilityManagedEquityV3,
        daily_spy_volatility_observations,
        scaler_for_decision,
    )
    from .data import (
        latest_bar_by_symbol,
        load_symmetric_equity_bars,
        load_terminal_lifecycles,
        load_v5_dividend_actions,
        sha256_file,
    )
    from .metrics import (
        capture_diagnostics,
        drawdown_episodes,
        exposure_timing_diagnostics,
        full_metrics,
        metrics_for_period,
        payload_hash,
    )
except ImportError:  # pragma: no cover - direct script fallback
    from research.v3_volatility_managed_v1.candidate import (
        AuditedControlV3,
        CostStressPortfolioBacktestEngine,
        DynamicAllocationRiskOverlay,
        ScalerObservation,
        V3_MAXIMUM_ALLOCATION,
        VolatilityManagedEquityV3,
        daily_spy_volatility_observations,
        scaler_for_decision,
    )
    from research.v3_volatility_managed_v1.data import (
        latest_bar_by_symbol,
        load_symmetric_equity_bars,
        load_terminal_lifecycles,
        load_v5_dividend_actions,
        sha256_file,
    )
    from research.v3_volatility_managed_v1.metrics import (
        capture_diagnostics,
        drawdown_episodes,
        exposure_timing_diagnostics,
        full_metrics,
        metrics_for_period,
        payload_hash,
    )


RESEARCH_DIR = Path(__file__).resolve().parent
ROOT = RESEARCH_DIR.parents[1]
DATA_DIR = ROOT / "data" / "historical"
CONFIG_PATH = ROOT / "config" / "settings.yaml"
BAR_PATH = DATA_DIR / "equity_pit_symmetric_2016_2026_v5.csv"
LIFETIME_PATH = DATA_DIR / "equity_entity_lifetimes_full_v5.csv"
ACTION_PATH = DATA_DIR / "equity_survivorship_full_corporate_actions_v5.csv"
PROTOCOL_PATH = RESEARCH_DIR / "FROZEN_PROTOCOL.md"

EXPECTED_MAIN_SHA = "5175a58b2fb4084e5c5a00854d3c3c94b0e819a1"
EXPECTED_BAR_SHA = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_LIFETIME_SHA = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_ACTION_SHA = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"

FULL_START = datetime(2017, 1, 3, tzinfo=UTC)
DEV_START = datetime(2017, 1, 3, tzinfo=UTC)
DEV_END = datetime(2021, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
VAL_START = datetime(2022, 1, 3, tzinfo=UTC)
VAL_END = datetime(2026, 7, 31, 23, 59, 59, 999999, tzinfo=UTC)
FULL_END = VAL_END
PERIODS = (("development", DEV_START, DEV_END), ("validation", VAL_START, VAL_END), ("full", FULL_START, FULL_END))
ARM_NAMES = ("A_control", "B_treatment", "C_static_comparator")
COST_MULTIPLIERS = (1.0, 2.0, 3.0)
RISK_METRICS = ("sharpe", "sortino", "calmar")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _period_for(timestamp: datetime) -> str:
    if DEV_START <= timestamp <= DEV_END:
        return "development"
    if VAL_START <= timestamp <= VAL_END:
        return "validation"
    return "outside_window"


def _snapshot_at(universe: object, timestamp: datetime) -> object | None:
    snapshots = tuple(getattr(universe, "snapshots", ()))
    if not snapshots:
        return None
    effective = [snapshot.effective_at for snapshot in snapshots]
    index = bisect.bisect_right(effective, timestamp) - 1
    return snapshots[index] if index >= 0 else None


def _compact_universe_receipt(universe: object) -> dict[str, object]:
    """Persist the hash-bearing PIT fields without scanner debug bulk."""
    raw = universe.to_dict()
    snapshots = []
    for snapshot in raw["snapshots"]:
        snapshots.append(
            {
                "effective_at": snapshot["effective_at"],
                "symbols": snapshot["symbols"],
                "observation_set": snapshot["observation_set"],
                "maximum_stream_symbols": snapshot["maximum_stream_symbols"],
                "pinned_symbols": snapshot["pinned_symbols"],
                "observation_strategy_name": snapshot["observation_strategy_name"],
                "observation_strategy_version": snapshot["observation_strategy_version"],
                "scanner_version": snapshot["scanner_version"],
                "config_digest": snapshot["config_digest"],
                "candidate_count": snapshot["candidate_count"],
                "sector_metadata_coverage": snapshot["sector_metadata_coverage"],
            }
        )
    return {
        "market": raw["market"],
        "methodology": raw["methodology"],
        "sector_metadata_coverage": raw["sector_metadata_coverage"],
        "snapshots": snapshots,
    }


def weekly_decision_timestamps(bars: Sequence[Bar], start: datetime, end: datetime) -> tuple[datetime, ...]:
    """Mirror the engine's first-observed-bar-per-ISO-week rule."""

    timestamps = sorted({bar.timestamp for bar in bars if start <= bar.timestamp <= end})
    seen: set[tuple[int, int]] = set()
    decisions: list[datetime] = []
    for timestamp in timestamps:
        key = (timestamp.isocalendar().year, timestamp.isocalendar().week)
        if key in seen:
            continue
        seen.add(key)
        decisions.append(timestamp)
    return tuple(decisions)


def _schedule_eligibility(
    bars: Sequence[Bar],
    universe: object,
    schedule: Sequence[datetime],
) -> tuple[list[dict[str, object]], dict[datetime, dict[str, object]]]:
    schedule_set = set(schedule)
    needed: set[str] = set()
    snapshots_by_timestamp: dict[datetime, object | None] = {}
    for timestamp in schedule:
        snapshot = _snapshot_at(universe, timestamp)
        snapshots_by_timestamp[timestamp] = snapshot
        if snapshot is not None:
            expected = tuple(snapshot.observation_set) or tuple(sorted(snapshot.symbols))
            needed.update(expected)
    available: dict[str, set[datetime]] = defaultdict(set)
    for bar in bars:
        if bar.timestamp not in schedule_set:
            continue
        symbol = canonical_symbol(bar.symbol, Market.EQUITY)
        if symbol in needed:
            available[symbol].add(bar.timestamp)
    rows: list[dict[str, object]] = []
    by_timestamp: dict[datetime, dict[str, object]] = {}
    for timestamp in schedule:
        snapshot = snapshots_by_timestamp[timestamp]
        if snapshot is None:
            row = {
                "decision_timestamp": timestamp.isoformat(),
                "period": _period_for(timestamp),
                "eligible": False,
                "reason": "no_pit_universe_snapshot",
                "expected_symbol_count": 0,
                "missing_symbols": [],
            }
        else:
            expected = tuple(snapshot.observation_set) or tuple(sorted(snapshot.symbols))
            missing = sorted(symbol for symbol in expected if timestamp not in available.get(symbol, set()))
            row = {
                "decision_timestamp": timestamp.isoformat(),
                "period": _period_for(timestamp),
                "eligible": not missing,
                "reason": "eligible" if not missing else "missing_completed_observation_bar",
                "expected_symbol_count": len(expected),
                "missing_symbols": missing,
                "universe_observation_set_hash": payload_hash(list(expected)),
            }
        rows.append(row)
        by_timestamp[timestamp] = row
    return rows, by_timestamp


def _coverage_for_result(
    result: PortfolioBacktestResult,
    schedule_rows: Sequence[dict[str, object]],
) -> dict[str, object]:
    schedule = [datetime.fromisoformat(str(row["decision_timestamp"])) for row in schedule_rows]
    eligible = {
        timestamp
        for timestamp, row in zip(schedule, schedule_rows, strict=True)
        if bool(row["eligible"])
    }
    actual = {record.decision_timestamp for record in result.rebalances}
    unexpected = sorted(actual - set(schedule))
    missing_eligible = sorted(eligible - actual)

    def period_stats(start: datetime, end: datetime) -> dict[str, object]:
        expected = [timestamp for timestamp in schedule if start <= timestamp <= end and timestamp in eligible]
        observed = [timestamp for timestamp in expected if timestamp in actual]
        return {
            "eligible_weeks": len(expected),
            "observed_decisions": len(observed),
            "coverage_ratio": len(observed) / len(expected) if expected else 0.0,
            "first_observed": _iso(min(observed)) if observed else None,
            "last_observed": _iso(max(observed)) if observed else None,
        }

    longest_gap = 0
    current_gap = 0
    for timestamp in sorted(eligible):
        if timestamp in actual:
            current_gap = 0
        else:
            current_gap += 1
            longest_gap = max(longest_gap, current_gap)
    return {
        "schedule_weeks": len(schedule),
        "eligible_weeks": len(eligible),
        "observed_decisions": len(actual & eligible),
        "coverage_ratio": len(actual & eligible) / len(eligible) if eligible else 0.0,
        "unexpected_decisions": [timestamp.isoformat() for timestamp in unexpected],
        "missing_eligible_decisions": [timestamp.isoformat() for timestamp in missing_eligible],
        "longest_unexplained_gap_weeks": longest_gap,
        "development": period_stats(DEV_START, DEV_END),
        "validation": period_stats(VAL_START, VAL_END),
        "pass": (
            period_stats(DEV_START, DEV_END)["coverage_ratio"] >= 0.90
            and period_stats(VAL_START, VAL_END)["coverage_ratio"] >= 0.90
            and longest_gap <= 8
            and not unexpected
        ),
    }


class AuditedEngine(CostStressPortfolioBacktestEngine):
    """Capture daily exposure in addition to the engine's normal state."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.exposure_history: list[tuple[datetime, float, int, float, float]] = []
        super().__init__(*args, **kwargs)

    def _effective_observation_symbols(self, timestamp: datetime) -> tuple[str, ...] | None:
        """Hold the PIT stream constant across A/B/C.

        The production engine reserves managed holdings inside a finite stream.
        That is appropriate for live routing, but would make an exposure-only
        treatment change the observable tail and therefore change V3 alpha.
        The campaign precomputes one PIT observation set from the common
        universe and feeds that identical set to every arm; portfolio slots,
        risk caps, and tradability remain engine-owned and unchanged.
        """
        snapshots = getattr(self.universe, "snapshots", ())
        if not snapshots:
            return None
        index = bisect.bisect_right(snapshots, timestamp, key=lambda item: item.effective_at)
        if index == 0:
            return None
        snapshot = snapshots[index - 1]
        symbols = tuple(snapshot.observation_set) or tuple(sorted(snapshot.symbols))
        return tuple(
            symbol
            for symbol in symbols
            if self.terminal_lifecycles.get(symbol) is None
            or self.terminal_lifecycles[symbol].last_bar >= timestamp
        )

    def _record_holdings_snapshot(self, timestamp: datetime) -> None:
        super()._record_holdings_snapshot(timestamp)
        equity = self._equity()
        values = [
            position.qty * self._last_price.get(position.symbol, position.avg_price)
            for position in self._positions.values()
            if position.qty > 0
        ]
        gross = sum(values) / equity if equity > 0 else 0.0
        concentration = max(values, default=0.0) / equity if equity > 0 else 0.0
        cash_ratio = max(0.0, 1.0 - gross) if equity > 0 else 1.0
        self.exposure_history.append((timestamp, gross, len(values), concentration, cash_ratio))


def _make_engine(
    config: Config,
    strategy: object,
    universe: object,
    universe_hash: str,
    terminal_lifecycles: dict[str, object],
    *,
    cost_multiplier: float,
    maximum_allocation: float,
    dynamic_allocation: bool,
) -> AuditedEngine:
    risk_limits = portfolio_risk_limits_from_config(config.risk)
    engine = AuditedEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps,
        spread_bps=config.backtest.spread_bps,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=universe_hash,
        risk_limits=risk_limits,
        maximum_allocation=maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        terminal_lifecycles=terminal_lifecycles,
        cost_multiplier=cost_multiplier,
    )
    if dynamic_allocation:
        engine.risk_overlay = DynamicAllocationRiskOverlay(risk_limits)
    return engine


def _serialize_fill(fill: object) -> dict[str, object]:
    return {
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
        "client_order_id": fill.client_order_id,
    }


def _serialize_rebalance(record: object) -> dict[str, object]:
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


def _serialize_exposure(rows: Sequence[tuple[datetime, float, int, float, float]]) -> list[dict[str, object]]:
    return [
        {
            "timestamp": timestamp.isoformat(),
            "gross_exposure": gross,
            "position_count": positions,
            "maximum_concentration": concentration,
            "cash_ratio": cash_ratio,
        }
        for timestamp, gross, positions, concentration, cash_ratio in rows
    ]


def _serialize_execution(
    result: PortfolioBacktestResult,
    exposure_history: Sequence[tuple[datetime, float, int, float, float]],
    *,
    include_context: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "starting_equity": result.starting_equity,
        "ending_equity": result.ending_equity,
        "slippage_cost_total": result.slippage_cost_total,
        "spread_cost_total": result.spread_cost_total,
        "fee_total": result.fee_total,
        "equity_curve": [
            {"timestamp": timestamp.isoformat(), "equity": equity}
            for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True)
        ],
        "fills": [_serialize_fill(fill) for fill in result.fills],
        "rebalances": [_serialize_rebalance(record) for record in result.rebalances],
        "terminal_events": [dict(event) for event in result.terminal_events],
        "closed_position_events": [
            {"timestamp": timestamp.isoformat(), "symbol": symbol, "pnl": pnl}
            for timestamp, symbol, pnl in result.closed_position_events
        ],
        "exposure_history": _serialize_exposure(exposure_history),
    }
    if include_context:
        payload["benchmark_bars"] = [
            {"timestamp": bar.timestamp.isoformat(), "symbol": bar.symbol, "close": bar.close}
            for bar in result.benchmark_bars
        ]
        payload["weight_history"] = [
            {"timestamp": timestamp.isoformat(), "weights": weights}
            for timestamp, weights in result.weight_history
        ]
        payload["universe_history"] = [
            {"timestamp": timestamp.isoformat(), "symbols": list(symbols)}
            for timestamp, symbols in result.universe_history
        ]
    return payload


def _run_arm(
    config: Config,
    arm: str,
    universe: object,
    universe_hash: str,
    terminal_lifecycles: dict[str, object],
    prepared: tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]],
    numeric_history: object,
    corporate_actions: Sequence[object],
    scaler_by_decision: dict[datetime, ScalerObservation],
    static_allocation: float,
    *,
    cost_multiplier: float,
    include_context: bool,
) -> dict[str, object]:
    if arm == "A_control":
        strategy = AuditedControlV3(dict(config.strategy.equity.parameters))
        maximum_allocation = V3_MAXIMUM_ALLOCATION
        dynamic_allocation = False
    elif arm == "B_treatment":
        strategy = VolatilityManagedEquityV3(
            dict(config.strategy.equity.parameters), scaler_by_decision=scaler_by_decision
        )
        maximum_allocation = V3_MAXIMUM_ALLOCATION
        dynamic_allocation = True
    elif arm == "C_static_comparator":
        strategy = portfolio_strategy_from(
            "equity_residual_momentum_v3",
            dict(config.strategy.equity.parameters),
            timeframe="1d",
        )
        maximum_allocation = static_allocation
        dynamic_allocation = False
    else:
        raise ValueError(f"Unknown campaign arm: {arm}")
    engine = _make_engine(
        config,
        strategy,
        universe,
        universe_hash,
        terminal_lifecycles,
        cost_multiplier=cost_multiplier,
        maximum_allocation=maximum_allocation,
        dynamic_allocation=dynamic_allocation,
    )
    filtered, warmup, prepared_inputs = prepared
    print(f"RUN {arm} cost={cost_multiplier:g}x", flush=True)
    result = engine.run(
        filtered,
        warmup=warmup,
        liquidate_at_end=True,
        corporate_actions=list(corporate_actions),
        prepared_inputs=prepared_inputs,
        numeric_history=numeric_history,
    )
    metrics = {
        label: metrics_for_period(result, engine.exposure_history, start=start, end=end, label=label)
        for label, start, end in PERIODS
    }
    report = build_portfolio_report(
        result,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
    )
    payload = {
        "arm": arm,
        "strategy_name": strategy.name,
        "strategy_version": strategy.version,
        "cost_multiplier": cost_multiplier,
        "maximum_allocation_input": maximum_allocation,
        "dynamic_allocation": dynamic_allocation,
        "metrics": metrics,
        "report_summary": report.summary,
        "execution": _serialize_execution(result, engine.exposure_history, include_context=include_context),
        "audit_rows": list(getattr(strategy, "audit_rows", ())),
    }
    return payload


def _scaler_rows(
    schedule: Sequence[datetime],
    eligibility: dict[datetime, dict[str, object]],
    observations: Sequence[tuple[datetime, float]],
) -> tuple[list[dict[str, object]], dict[datetime, ScalerObservation]]:
    rows: list[dict[str, object]] = []
    by_decision: dict[datetime, ScalerObservation] = {}
    for timestamp in schedule:
        observation = scaler_for_decision(timestamp, observations)
        by_decision[timestamp] = observation
        row = {
            **observation.to_dict(),
            "period": _period_for(timestamp),
            "eligible": bool(eligibility[timestamp]["eligible"]),
            "eligibility_reason": eligibility[timestamp]["reason"],
        }
        rows.append(row)
    return rows, by_decision


def _scale_diagnostics(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    scales = [float(row["scale"]) for row in rows]
    scaled = [row for row in rows if float(row["scale"]) < 1.0 - 1e-12]
    fallback = [row for row in rows if str(row["status"]).endswith("normal_v3")]
    eligible = [row for row in rows if bool(row["eligible"])]
    eligible_scales = [float(row["scale"]) for row in eligible]
    reduced_flags = [float(row["scale"]) < 1.0 - 1e-12 for row in eligible]
    episode_count = 0
    longest_episode = 0
    current_episode = 0
    for reduced in reduced_flags:
        if reduced:
            if current_episode == 0:
                episode_count += 1
            current_episode += 1
            longest_episode = max(longest_episode, current_episode)
        else:
            current_episode = 0
    return {
        "decision_count": len(rows),
        "eligible_decision_count": len(eligible),
        "scaled_decision_count": len(scaled),
        "scaled_fraction": len(scaled) / len(eligible) if eligible else 0.0,
        "scale_one_fraction": sum(not reduced for reduced in reduced_flags) / len(eligible) if eligible else 0.0,
        "below_0_75_fraction": sum(scale < 0.75 for scale in eligible_scales) / len(eligible) if eligible else 0.0,
        "below_0_50_fraction": sum(scale < 0.50 for scale in eligible_scales) / len(eligible) if eligible else 0.0,
        "below_0_25_fraction": sum(scale < 0.25 for scale in eligible_scales) / len(eligible) if eligible else 0.0,
        "fallback_decision_count": len(fallback),
        "min_scale": min(eligible_scales, default=1.0),
        "median_scale": statistics.median(eligible_scales) if eligible_scales else 1.0,
        "mean_scale": statistics.fmean(eligible_scales) if eligible_scales else 1.0,
        "p10_scale": _linear_percentile(eligible_scales, 10.0),
        "p25_scale": _linear_percentile(eligible_scales, 25.0),
        "p75_scale": _linear_percentile(eligible_scales, 75.0),
        "p90_scale": _linear_percentile(eligible_scales, 90.0),
        "eligible_mean_scale": statistics.fmean(eligible_scales) if eligible_scales else 1.0,
        "eligible_median_scale": statistics.median(eligible_scales) if eligible_scales else 1.0,
        "reduced_exposure_episode_count": episode_count,
        "longest_reduced_exposure_episode_weeks": longest_episode,
        "status_counts": {
            status: sum(1 for row in rows if row["status"] == status)
            for status in sorted({str(row["status"]) for row in rows})
        },
    }


def _linear_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _augment_audit_rows(payload: dict[str, object]) -> None:
    """Join strategy audit rows to post-risk and post-fill engine records."""
    records = {
        str(record["decision_timestamp"]): record
        for record in payload["execution"]["rebalances"]
    }
    augmented: list[dict[str, object]] = []
    for row in payload["audit_rows"]:
        enriched = dict(row)
        record = records.get(str(row["decision_timestamp"]))
        if record is not None:
            target_weights = {str(symbol): float(weight) for symbol, weight in record["target_weights"]}
            actual_weights = {str(symbol): float(weight) for symbol, weight in record["post_fill_actual_weights"]}
            enriched["post_risk_target_weights"] = target_weights
            enriched["post_risk_target_exposure"] = sum(target_weights.values())
            enriched["post_fill_actual_weights"] = actual_weights
            enriched["post_fill_actual_exposure"] = sum(actual_weights.values())
            enriched["risk_overlay_reasons"] = list(record["risk_overlay_reasons"])
        augmented.append(enriched)
    payload["audit_rows"] = augmented


def _gate_table(
    metrics_by_arm: dict[str, dict[str, object]],
    stress: dict[str, dict[str, dict[str, object]]],
    coverage: dict[str, dict[str, object]],
    verification: dict[str, object],
) -> dict[str, object]:
    a_dev = metrics_by_arm["A_control"]["development"]
    b_dev = metrics_by_arm["B_treatment"]["development"]
    c_dev = metrics_by_arm["C_static_comparator"]["development"]
    a_full = metrics_by_arm["A_control"]["full"]
    b_full = metrics_by_arm["B_treatment"]["full"]
    c_full = metrics_by_arm["C_static_comparator"]["full"]
    a_val = metrics_by_arm["A_control"]["validation"]
    b_val = metrics_by_arm["B_treatment"]["validation"]
    a_2x = stress["A_control"]["2x"]["metrics"]["full"]
    b_1x = metrics_by_arm["B_treatment"]["full"]
    b_2x = stress["B_treatment"]["2x"]["metrics"]["full"]
    c_2x = stress["C_static_comparator"]["2x"]["metrics"]["full"]
    b_3x = stress["B_treatment"]["3x"]["metrics"]["full"]

    improvements_a = {metric: float(b_full[metric]) - float(a_full[metric]) for metric in RISK_METRICS}
    improvements_c = {metric: float(b_full[metric]) - float(c_full[metric]) for metric in RISK_METRICS}
    gate_1_coverage = all(bool(item["pass"]) for item in coverage.values())
    gate_1 = bool(verification.get("research_run_valid")) and gate_1_coverage and bool(
        verification.get("structural_valid", False)
    )
    gate_2 = (
        sum(value > 0.05 for value in improvements_a.values()) >= 2
        and all(value >= -0.03 for value in improvements_a.values())
    )
    gate_3 = (
        float(b_full["max_drawdown"]) <= float(a_full["max_drawdown"]) - 0.02
        and float(b_full["max_drawdown"]) <= float(a_full["max_drawdown"]) * 0.90
    )
    gate_4 = float(a_full["cagr"]) - float(b_full["cagr"]) <= 0.01
    gate_5 = (
        sum(value > 0.0 for value in improvements_c.values()) >= 2
        and float(c_full["cagr"]) - float(b_full["cagr"]) <= 0.005
    )
    gate_6 = (
        float(b_val["max_drawdown"]) < float(a_val["max_drawdown"])
        and any(float(b_val[metric]) > float(a_val[metric]) for metric in RISK_METRICS)
        and float(a_val["cagr"]) - float(b_val["cagr"]) <= 0.015
        and bool(coverage["B_treatment"]["pass"])
    )
    gate_7 = (
        all(math.isfinite(float(b_2x[metric])) for metric in RISK_METRICS)
        and float(b_2x["net_total_return"]) > 0.0
        and all(float(b_1x[metric]) <= 0 or float(b_2x[metric]) >= 0 for metric in RISK_METRICS)
        and not all(
            float(b_2x[metric]) < float(a_2x[metric]) and float(b_2x[metric]) < float(c_2x[metric])
            for metric in RISK_METRICS
        )
        and float(b_3x["net_total_return"]) > -0.20
    )
    gate_8 = bool(verification.get("no_new_instruments_or_slots", False)) and bool(
        verification.get("no_promotion_changes", False)
    )
    gates = {
        "gate_1": {
            "pass": gate_1,
            "coverage": coverage,
            "verifier_valid": bool(verification.get("research_run_valid")),
            "structural_valid": bool(verification.get("structural_valid", False)),
        },
        "gate_2": {"pass": gate_2, "full_period_risk_improvements": improvements_a},
        "gate_3": {
            "pass": gate_3,
            "control_max_drawdown": a_full["max_drawdown"],
            "treatment_max_drawdown": b_full["max_drawdown"],
            "absolute_improvement": float(a_full["max_drawdown"]) - float(b_full["max_drawdown"]),
            "relative_improvement": (
                1.0 - float(b_full["max_drawdown"]) / float(a_full["max_drawdown"])
                if float(a_full["max_drawdown"]) > 0
                else 0.0
            ),
        },
        "gate_4": {
            "pass": gate_4,
            "cagr_drag": float(a_full["cagr"]) - float(b_full["cagr"]),
            "threshold": 0.01,
        },
        "gate_5": {"pass": gate_5, "full_period_risk_improvements_vs_static": improvements_c},
        "gate_6": {
            "pass": gate_6,
            "validation_risk_metrics": {
                metric: {"control": a_val[metric], "treatment": b_val[metric]}
                for metric in RISK_METRICS
            },
            "coverage_pass": bool(coverage["B_treatment"]["pass"]),
        },
        "gate_7": {
            "pass": gate_7,
            "treatment_2x_risk_metrics": {metric: b_2x[metric] for metric in RISK_METRICS},
            "treatment_3x_net_total_return": b_3x["net_total_return"],
        },
        "gate_8": {
            "pass": gate_8,
            "no_new_instruments_or_slots": bool(verification.get("no_new_instruments_or_slots", False)),
            "no_promotion_changes": bool(verification.get("no_promotion_changes", False)),
        },
    }
    gates["all_mandatory_gates_pass"] = all(bool(gates[name]["pass"]) for name in gates if name.startswith("gate_"))
    gates["independent_gate_table"] = verification.get("independent_gate_table", {})
    return gates


def _markdown_report(
    decision: dict[str, object],
    manifest: dict[str, object],
    data_receipt: dict[str, object],
    scaler_diagnostics: dict[str, object],
    metrics_by_arm: dict[str, dict[str, object]],
    stress: dict[str, dict[str, dict[str, object]]],
    coverage: dict[str, dict[str, object]],
    gates: dict[str, object],
    verification: dict[str, object],
) -> str:
    def pct(value: object) -> str:
        return f"{float(value) * 100:.2f}%"

    def row(arm: str, period: str) -> str:
        metric = metrics_by_arm[arm][period]
        return (
            f"| {arm} | {period} | {pct(metric['net_total_return'])} | {pct(metric['cagr'])} | "
            f"{pct(metric['max_drawdown'])} | {metric['sharpe']:.3f} | {metric['sortino']:.3f} | "
            f"{metric['calmar']:.3f} | {pct(metric['average_equity_exposure'])} |"
        )

    stress_lines = []
    for multiplier in ("1x", "2x", "3x"):
        for arm in ARM_NAMES:
            source = metrics_by_arm[arm] if multiplier == "1x" else stress[arm][multiplier]["metrics"]
            metric = source["full"]
            stress_lines.append(
                f"| {arm} | {multiplier} | {pct(metric['net_total_return'])} | {pct(metric['max_drawdown'])} | "
                f"{metric['sharpe']:.3f} | {metric['sortino']:.3f} | {metric['calmar']:.3f} | "
                f"{metric['transaction_costs']:.2f} |"
            )
    gate_lines = []
    for name in sorted(name for name in gates if name.startswith("gate_")):
        if name == "gate_1" or name == "gate_8":
            continue
        gate_lines.append(f"| {name} | {'PASS' if gates[name]['pass'] else 'FAIL'} |")
    gate_lines.insert(0, f"| gate_1 | {'PASS' if gates['gate_1']['pass'] else 'FAIL'} |")
    gate_lines.append(f"| gate_8 | {'PASS' if gates['gate_8']['pass'] else 'FAIL'} |")

    return f"""# equity_v3_volatility_managed_v1 research report

## Decision

**{decision['verdict']}**

This is a research-only result. It does not change the configured strategy,
PAPER/LIVE behavior, promotion state, slots, instruments, or risk limits.

## Frozen identity and literature

- Candidate: `equity_v3_volatility_managed_v1`
- Base main SHA: `{manifest['baseline_main_sha']}`
- Research branch: `{manifest['branch']}`
- Protocol SHA-256: `{manifest['protocol_sha256']}`
- Hypothesis: a completed-bar SPY VOL21 scaler can reduce drawdown and improve
  risk-adjusted outcomes without changing V3 selection, ranks, or instruments.
- Mechanism references: [Moreira and Muir](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513),
  [Andersen, Bollerslev, Diebold and Labys](https://onlinelibrary.wiley.com/doi/pdf/10.1111/1468-0262.00418),
  [Daniel and Moskowitz](https://www.nber.org/papers/w20439).

## Data and PIT receipt

- Bars: `{data_receipt['bars']['path']}`; SHA-256 `{data_receipt['bars']['sha256']}`;
  split-adjusted daily SIP package, lexical `EQUITY`/`1Day` normalization only.
- Lifecycles: `{data_receipt['lifecycles']['path']}`; SHA-256 `{data_receipt['lifecycles']['sha256']}`.
- Corporate actions: `{data_receipt['corporate_actions']['path']}`; SHA-256
  `{data_receipt['corporate_actions']['sha256']}`; cash dividends only, splits
  and stock actions ignored because prices are split-adjusted.
- Windows: development `2017-01-03` to `2021-12-31`; validation `2022-01-03`
  to `2026-07-31`; warmup begins with all available prior bars.
- SPY volatility observations: {data_receipt['spy']['volatility_observation_count']};
  scaler mean `{scaler_diagnostics['eligible_mean_scale']:.4f}`, median
  `{scaler_diagnostics['eligible_median_scale']:.4f}`, scaled fraction
  `{pct(scaler_diagnostics['scaled_fraction'])}`.

## A/B/C results

| Arm | Period | Net return | CAGR | Max drawdown | Sharpe | Sortino | Calmar | Avg equity exposure |
|---|---|---:|---:|---:|---:|---:|---:|---:|
{row('A_control', 'development')}
{row('B_treatment', 'development')}
{row('C_static_comparator', 'development')}
{row('A_control', 'validation')}
{row('B_treatment', 'validation')}
{row('C_static_comparator', 'validation')}
{row('A_control', 'full')}
{row('B_treatment', 'full')}
{row('C_static_comparator', 'full')}

## Cost stress

| Arm | Costs | Net return | Max drawdown | Sharpe | Sortino | Calmar | Transaction costs |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(stress_lines)}

## Coverage and diagnostics

| Arm | Development coverage | Validation coverage | Longest unexplained gap | Pass |
|---|---:|---:|---:|---:|
{chr(10).join(f"| {arm} | {coverage[arm]['development']['coverage_ratio']:.3f} | {coverage[arm]['validation']['coverage_ratio']:.3f} | {coverage[arm]['longest_unexplained_gap_weeks']} | {'PASS' if coverage[arm]['pass'] else 'FAIL'} |" for arm in ARM_NAMES)}

- Drawdown episodes: `drawdown_diagnostics.json`.
- Exposure, up/down capture, and reduced-exposure timing: `exposure_diagnostics.json`.
- Full scaler series and statuses: `scaler_time_series.json` and `scaler_audit.json`.
- Static comparator freeze: `STATIC_COMPARATOR_FREEZE.json`.

## Mandatory gates

| Gate | Result |
|---|---|
{chr(10).join(gate_lines)}

## Independent verification

- Research run validity: **{'VALID' if verification.get('research_run_valid') else 'INVALID'}**.
- Structural replay: **{'VALID' if verification.get('structural_valid') else 'INVALID'}**.
- The verifier independently checked repository/data/protocol hashes,
  completed-bar VOL21/no-lookahead behavior, V3 identity and selection parity,
  next-bar execution ordering, lifecycle/NAV accounting, caps, coverage, and
  gate inputs. Details are in `independent_verification.json`.

## Final interpretation

The verdict is determined only after validity checks and all eight frozen gates.
The candidate remains research-only and is not eligible for automatic or
implicit PAPER/LIVE promotion. The exact decision payload is in `decision.json`.
"""


def main() -> int:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    branch = _git("branch", "--show-current")
    main_sha = _git("rev-parse", "main")
    origin_sha = _git("rev-parse", "origin/main")
    current_sha = _git("rev-parse", "HEAD")
    if branch != "research/v3-volatility-managed-v1":
        raise RuntimeError(f"Campaign must run on research/v3-volatility-managed-v1, got {branch}")
    if main_sha != EXPECTED_MAIN_SHA or origin_sha != EXPECTED_MAIN_SHA:
        raise RuntimeError("main/origin/main no longer match the frozen clean baseline")
    protocol_sha, protocol_size = sha256_file(PROTOCOL_PATH)
    if protocol_size == 0:
        raise RuntimeError("FROZEN_PROTOCOL.md is empty")

    print("LOAD CONFIG", flush=True)
    config = load_config(str(CONFIG_PATH))
    if config.strategy.equity.name != "equity_residual_momentum_v3":
        raise RuntimeError("Campaign requires the current configured equity V3")
    if config.strategy.equity.maximum_allocation != V3_MAXIMUM_ALLOCATION:
        raise RuntimeError("Configured V3 allocation is not the frozen 0.55 baseline")
    if config.strategy.equity.parameters.get("rebalance_frequency") != "weekly":
        raise RuntimeError("Configured V3 must be weekly for this campaign")

    file_specs = (
        ("bars", BAR_PATH, EXPECTED_BAR_SHA),
        ("lifecycles", LIFETIME_PATH, EXPECTED_LIFETIME_SHA),
        ("corporate_actions", ACTION_PATH, EXPECTED_ACTION_SHA),
    )
    receipt_files: dict[str, dict[str, object]] = {}
    for name, path, expected in file_specs:
        digest, size = sha256_file(path)
        if digest != expected:
            raise RuntimeError(f"{name} hash mismatch: {digest} != {expected}")
        receipt_files[name] = {"path": str(path), "sha256": digest, "byte_size": size}

    print("LOAD V5 DATA", flush=True)
    bars, bar_stats = load_symmetric_equity_bars(BAR_PATH)
    terminal_lifecycles, lifecycle_stats = load_terminal_lifecycles(LIFETIME_PATH)
    corporate_actions, action_stats = load_v5_dividend_actions(ACTION_PATH, end=FULL_END)
    latest = latest_bar_by_symbol(bars)
    lifecycle_mismatches = [
        symbol
        for symbol, lifecycle in terminal_lifecycles.items()
        if latest.get(symbol) != lifecycle.last_bar
    ]
    if lifecycle_mismatches:
        raise RuntimeError(f"Terminal lifecycle last-bar mismatch for {lifecycle_mismatches[:5]}")

    print("BUILD PIT UNIVERSE", flush=True)
    base_strategy = portfolio_strategy_from(
        "equity_residual_momentum_v3",
        dict(config.strategy.equity.parameters),
        timeframe="1d",
    )
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=FULL_END,
        timeframe="1d",
        observation_strategy=base_strategy,
    )
    universe_hash = hash_point_in_time_universe(universe)
    _write_json(RESEARCH_DIR / "universe_snapshot.json", _compact_universe_receipt(universe))

    print("PREPARE ENGINE INPUTS", flush=True)
    prepared = _prepared_window(bars, Market.EQUITY, "1d", FULL_START, FULL_END)
    combined = (prepared[1] or []) + prepared[2][0]
    numeric_history = build_numeric_history(combined, market=Market.EQUITY, timeframe="1d")
    ordered, decision_bars = prepared[2]
    schedule = weekly_decision_timestamps(decision_bars, FULL_START, FULL_END)
    schedule_rows, eligibility = _schedule_eligibility(bars, universe, schedule)
    spy_bars = [bar for bar in bars if canonical_symbol(bar.symbol, Market.EQUITY) == "SPY"]
    volatility_observations = daily_spy_volatility_observations(spy_bars)
    scaler_rows, scaler_by_decision = _scaler_rows(schedule, eligibility, volatility_observations)
    _write_json(
        RESEARCH_DIR / "spy_signal_bars.json",
        [
            {"timestamp": bar.timestamp.isoformat(), "symbol": "SPY", "close": bar.close}
            for bar in sorted(spy_bars, key=lambda item: item.timestamp)
        ],
    )
    _write_json(RESEARCH_DIR / "volatility_observations.json", [
        {"timestamp": timestamp.isoformat(), "vol21": vol} for timestamp, vol in volatility_observations
    ])
    _write_json(RESEARCH_DIR / "scaler_time_series.json", scaler_rows)
    _write_json(RESEARCH_DIR / "decision_coverage_schedule.json", schedule_rows)
    scaler_diagnostics = _scale_diagnostics(scaler_rows)

    development_scalers = [
        scaler_by_decision[timestamp].scale
        for timestamp in schedule
        if DEV_START <= timestamp <= DEV_END and bool(eligibility[timestamp]["eligible"])
    ]
    if not development_scalers:
        raise RuntimeError("No eligible development scaler observations")
    development_mean_scale = statistics.fmean(development_scalers)
    static_allocation = V3_MAXIMUM_ALLOCATION * development_mean_scale
    static_freeze = {
        "candidate": "equity_v3_volatility_managed_v1",
        "protocol_sha256": protocol_sha,
        "source_main_sha": main_sha,
        "development_decision_count": len(development_scalers),
        "development_decision_timestamps": [
            timestamp.isoformat()
            for timestamp in schedule
            if DEV_START <= timestamp <= DEV_END and bool(eligibility[timestamp]["eligible"])
        ],
        "development_scaler_mean": development_mean_scale,
        "static_allocation": static_allocation,
        "freeze_rule": "0.55 * arithmetic mean of eligible development decision-date scales",
        "frozen_before_economic_outcomes": True,
        "scaler_time_series_sha256": payload_hash(scaler_rows),
    }
    _write_json(RESEARCH_DIR / "STATIC_COMPARATOR_FREEZE.json", static_freeze)

    data_receipt = {
        "candidate": "equity_v3_volatility_managed_v1",
        "bars": {**receipt_files["bars"], "manifest": str(BAR_PATH.with_suffix(".manifest.json")), "stats": bar_stats},
        "lifecycles": {
            **receipt_files["lifecycles"],
            "stats": lifecycle_stats,
            "terminal_last_bar_identity_check": "exact latest bar per inactive symbol",
        },
        "corporate_actions": {**receipt_files["corporate_actions"], "stats": action_stats},
        "adjustment_and_pit": {
            "prices": "split-adjusted",
            "actions_used": "cash_dividends only",
            "actions_ignored": "splits and stock actions",
            "signal_watermark": "completed bars at or before decision timestamp",
            "execution": "next strictly later tradable bar open",
        },
        "spy": {
            "signal_bars_path": str(RESEARCH_DIR / "spy_signal_bars.json"),
            "signal_bars_sha256": sha256_file(RESEARCH_DIR / "spy_signal_bars.json")[0],
            "bar_count": len(spy_bars),
            "first_bar": _iso(min((bar.timestamp for bar in spy_bars), default=None)),
            "last_bar": _iso(max((bar.timestamp for bar in spy_bars), default=None)),
            "volatility_observation_count": len(volatility_observations),
            "first_volatility_timestamp": _iso(volatility_observations[0][0] if volatility_observations else None),
            "last_volatility_timestamp": _iso(volatility_observations[-1][0] if volatility_observations else None),
        },
        "universe": {"hash": universe_hash, "snapshot_count": len(universe.snapshots)},
        "preparation": {
            "warmup_bar_count": len(prepared[1]),
            "ordered_bar_count": len(ordered),
            "decision_bar_count": len(decision_bars),
            "numeric_history_symbol_count": len(numeric_history),
        },
        "economic_outcomes_started": False,
    }
    _write_json(RESEARCH_DIR / "DATA_RECEIPT.json", data_receipt)

    source_files = [
        RESEARCH_DIR / name
        for name in ("candidate.py", "data.py", "metrics.py", "campaign.py", "verify_results.py")
        if (RESEARCH_DIR / name).exists()
    ]
    manifest: dict[str, object] = {
        "candidate": "equity_v3_volatility_managed_v1",
        "branch": branch,
        "baseline_main_sha": main_sha,
        "origin_main_sha": origin_sha,
        "branch_source_sha": current_sha,
        "protocol_sha256": protocol_sha,
        "protocol_size": protocol_size,
        "config_path": str(CONFIG_PATH),
        "config_strategy": config.strategy.equity.name,
        "config_strategy_parameters": dict(config.strategy.equity.parameters),
        "windows": {
            "warmup_before": FULL_START.isoformat(),
            "development_start": DEV_START.isoformat(),
            "development_end": DEV_END.isoformat(),
            "validation_start": VAL_START.isoformat(),
            "validation_end": VAL_END.isoformat(),
        },
        "data_hashes": {name: entry["sha256"] for name, entry in receipt_files.items()},
        "universe_hash": universe_hash,
        "static_comparator_freeze": str(RESEARCH_DIR / "STATIC_COMPARATOR_FREEZE.json"),
        "source_hashes": {str(path.relative_to(ROOT)): sha256_file(path)[0] for path in source_files},
        "old_research_artifacts_used": [],
        "economic_outcomes_started": False,
    }
    _write_json(RESEARCH_DIR / "run_manifest.json", manifest)

    manifest["economic_outcomes_started"] = True
    manifest["economic_outcomes_started_at"] = datetime.now(tz=UTC).isoformat()
    data_receipt["economic_outcomes_started"] = True
    _write_json(RESEARCH_DIR / "run_manifest.json", manifest)
    _write_json(RESEARCH_DIR / "DATA_RECEIPT.json", data_receipt)

    arm_one_x: dict[str, dict[str, object]] = {}
    metrics_by_arm: dict[str, dict[str, object]] = {}
    strategy_audits: dict[str, list[dict[str, object]]] = {}
    for arm in ARM_NAMES:
        payload = _run_arm(
            config,
            arm,
            universe,
            universe_hash,
            terminal_lifecycles,
            prepared,
            numeric_history,
            corporate_actions,
            scaler_by_decision,
            static_allocation,
            cost_multiplier=1.0,
            include_context=True,
        )
        _augment_audit_rows(payload)
        arm_one_x[arm] = payload
        metrics_by_arm[arm] = payload["metrics"]
        strategy_audits[arm] = payload["audit_rows"]
        filename = {"A_control": "control_a.json", "B_treatment": "treatment_b.json", "C_static_comparator": "comparator_c.json"}[arm]
        _write_json(RESEARCH_DIR / filename, payload)

    _write_json(RESEARCH_DIR / "control_audit.json", strategy_audits["A_control"])
    _write_json(RESEARCH_DIR / "scaler_audit.json", strategy_audits["B_treatment"])
    _write_json(RESEARCH_DIR / "scaler_diagnostics.json", scaler_diagnostics)

    stress: dict[str, dict[str, dict[str, object]]] = {arm: {} for arm in ARM_NAMES}
    for multiplier in (2.0, 3.0):
        key = f"{multiplier:g}x"
        for arm in ARM_NAMES:
            payload = _run_arm(
                config,
                arm,
                universe,
                universe_hash,
                terminal_lifecycles,
                prepared,
                numeric_history,
                corporate_actions,
                scaler_by_decision,
                static_allocation,
                cost_multiplier=multiplier,
                include_context=False,
            )
            stress[arm][key] = payload
    _write_json(RESEARCH_DIR / "cost_stress.json", stress)

    fill_keys: set[tuple[str, datetime]] = set()
    fill_requests: set[tuple[str, datetime]] = set()
    for payload in (*arm_one_x.values(), *(stress[arm][key] for arm in ARM_NAMES for key in ("2x", "3x"))):
        for fill in payload["execution"]["fills"]:
            symbol = canonical_symbol(str(fill["symbol"]), Market.EQUITY)
            fill_timestamp = datetime.fromisoformat(str(fill["timestamp"]))
            decision_timestamp = datetime.fromisoformat(str(fill["decision_timestamp"]))
            fill_keys.add((symbol, fill_timestamp))
            fill_requests.add((symbol, decision_timestamp))
    fill_symbols = {symbol for symbol, _timestamp in fill_requests}
    bars_by_symbol: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for bar in ordered:
        symbol = canonical_symbol(bar.symbol, Market.EQUITY)
        if symbol in fill_symbols:
            bars_by_symbol[symbol].append((bar.timestamp, bar.open))
    execution_bar_receipt: dict[str, dict[str, object]] = {}
    for bar in ordered:
        key = (canonical_symbol(bar.symbol, Market.EQUITY), bar.timestamp)
        if key in fill_keys:
            receipt_key = f"{key[0]}|{key[1].isoformat()}"
            execution_bar_receipt[receipt_key] = {
                "symbol": key[0],
                "timestamp": key[1].isoformat(),
                "open": bar.open,
                "close": bar.close,
            }
    for symbol, decision_timestamp in fill_requests:
        series = bars_by_symbol.get(symbol, [])
        index = bisect.bisect_right([timestamp for timestamp, _open in series], decision_timestamp)
        if index >= len(series):
            continue
        first_later_timestamp, first_later_open = series[index]
        receipt_key = f"{symbol}|{first_later_timestamp.isoformat()}"
        if receipt_key in execution_bar_receipt:
            execution_bar_receipt[receipt_key]["first_later_timestamp"] = first_later_timestamp.isoformat()
            execution_bar_receipt[receipt_key]["first_later_open"] = first_later_open
    if len(execution_bar_receipt) != len(fill_keys):
        missing = sorted(f"{symbol}|{timestamp.isoformat()}" for symbol, timestamp in fill_keys if f"{symbol}|{timestamp.isoformat()}" not in execution_bar_receipt)
        raise RuntimeError(f"Could not receipt all fill execution bars: {missing[:5]}")
    _write_json(RESEARCH_DIR / "execution_bar_receipt.json", execution_bar_receipt)

    coverage = {
        arm: _coverage_for_result(
            # The in-memory result is inside the serialized payload only, so
            # coverage is recomputed from its serialized decision records.
            _result_from_payload_for_coverage(arm_one_x[arm]),
            schedule_rows,
        )
        for arm in ARM_NAMES
    }
    _write_json(RESEARCH_DIR / "decision_coverage.json", coverage)

    a_result = _result_from_payload_for_diagnostics(arm_one_x["A_control"])
    b_result = _result_from_payload_for_diagnostics(arm_one_x["B_treatment"])
    c_result = _result_from_payload_for_diagnostics(arm_one_x["C_static_comparator"])
    _write_json(
        RESEARCH_DIR / "drawdown_diagnostics.json",
        {
            arm: {
                period: drawdown_episodes(*_period_curve(arm_one_x[arm], period))
                for period in ("full", "development", "validation")
            }
            for arm in ARM_NAMES
        },
    )
    _write_json(
        RESEARCH_DIR / "exposure_diagnostics.json",
        {
            "scaler": scaler_diagnostics,
            "up_down_capture": capture_diagnostics(a_result, b_result),
            "timing": exposure_timing_diagnostics(a_result, b_result, strategy_audits["B_treatment"]),
            "control_vs_static_full": {
                "control_average_exposure": metrics_by_arm["A_control"]["full"]["average_equity_exposure"],
                "static_average_exposure": metrics_by_arm["C_static_comparator"]["full"]["average_equity_exposure"],
            },
        },
    )

    print("RUN INDEPENDENT VERIFIER", flush=True)
    verifier_process = subprocess.run(
        [sys.executable, str(RESEARCH_DIR / "verify_results.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if verifier_process.stdout:
        print(verifier_process.stdout.rstrip(), flush=True)
    if verifier_process.stderr:
        print(verifier_process.stderr.rstrip(), file=sys.stderr, flush=True)
    verification_path = RESEARCH_DIR / "independent_verification.json"
    if verification_path.exists():
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
    else:
        verification = {
            "research_run_valid": False,
            "structural_valid": False,
            "errors": [f"verifier did not produce a receipt (exit={verifier_process.returncode})"],
            "independent_gate_table": {},
            "no_new_instruments_or_slots": False,
            "no_promotion_changes": False,
        }
    gates = _gate_table(metrics_by_arm, stress, coverage, verification)
    _write_json(RESEARCH_DIR / "gate_table.json", gates)
    if not bool(verification.get("research_run_valid")):
        verdict = "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT"
    elif not bool(gates["all_mandatory_gates_pass"]):
        verdict = "RESEARCH_REJECT"
    else:
        verdict = "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION"
    decision = {
        "candidate": "equity_v3_volatility_managed_v1",
        "verdict": verdict,
        "research_run_valid": bool(verification.get("research_run_valid")),
        "all_mandatory_gates_pass": bool(gates["all_mandatory_gates_pass"]),
        "promotion_performed": False,
        "paper_live_configuration_changed": False,
        "protocol_sha256": protocol_sha,
        "gate_table_sha256": payload_hash(gates),
        "independent_verification_sha256": payload_hash(verification),
        "reason": (
            "Validity failed; economics are not decision-eligible."
            if not bool(verification.get("research_run_valid"))
            else "At least one mandatory frozen gate failed."
            if not bool(gates["all_mandatory_gates_pass"])
            else "All mandatory gates passed; candidate is eligible only for a forward PAPER evaluation."
        ),
    }
    _write_json(RESEARCH_DIR / "decision.json", decision)
    manifest["completed_at"] = datetime.now(tz=UTC).isoformat()
    manifest["branch_sha_at_verification"] = _git("rev-parse", "HEAD")
    manifest["verdict"] = verdict
    _write_json(RESEARCH_DIR / "run_manifest.json", manifest)
    final_report = _markdown_report(
        decision,
        manifest,
        data_receipt,
        scaler_diagnostics,
        metrics_by_arm,
        stress,
        coverage,
        gates,
        verification,
    )
    (RESEARCH_DIR / "FINAL_REPORT.md").write_text(final_report, encoding="utf-8")
    print(f"VERDICT {verdict}", flush=True)
    return 0 if verdict == "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION" else 2


def _curve_timestamps(payload: dict[str, object]) -> list[datetime]:
    execution = payload["execution"]
    return [datetime.fromisoformat(str(row["timestamp"])) for row in execution["equity_curve"]]


def _curve_values(payload: dict[str, object]) -> list[float]:
    execution = payload["execution"]
    return [float(row["equity"]) for row in execution["equity_curve"]]


def _period_curve(payload: dict[str, object], period: str) -> tuple[list[float], list[datetime]]:
    bounds = {
        "development": (DEV_START, DEV_END),
        "validation": (VAL_START, VAL_END),
        "full": (FULL_START, FULL_END),
    }
    start, end = bounds[period]
    timestamps = _curve_timestamps(payload)
    values = _curve_values(payload)
    selected = [index for index, timestamp in enumerate(timestamps) if start <= timestamp <= end]
    if not selected:
        return [], []
    first = selected[0]
    anchor = first - 1 if first > 0 else first
    indices = list(dict.fromkeys([anchor, *selected]))
    return [values[index] for index in indices], [timestamps[index] for index in indices]


def _result_from_payload_for_coverage(payload: dict[str, object]) -> PortfolioBacktestResult:
    """Small result facade for coverage's decision-record access."""

    class _CoverageResult:
        rebalances = tuple(_Record(row) for row in payload["execution"]["rebalances"])

    return _CoverageResult()  # type: ignore[return-value]


def _result_from_payload_for_diagnostics(payload: dict[str, object]) -> PortfolioBacktestResult:
    """Rehydrate only the fields used by capture/timing diagnostics."""

    class _DiagnosticResult:
        curve_timestamps = tuple(_curve_timestamps(payload))
        equity_curve = tuple(_curve_values(payload))
        benchmark_bars = tuple(
            _BarProxy(row) for row in payload["execution"].get("benchmark_bars", ())
        )

    return _DiagnosticResult()  # type: ignore[return-value]


class _Record:
    def __init__(self, row: dict[str, object]) -> None:
        self.decision_timestamp = datetime.fromisoformat(str(row["decision_timestamp"]))


class _BarProxy:
    def __init__(self, row: dict[str, object]) -> None:
        self.timestamp = datetime.fromisoformat(str(row["timestamp"]))
        self.close = float(row["close"])


if __name__ == "__main__":
    raise SystemExit(main())
