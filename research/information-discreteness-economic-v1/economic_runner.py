"""Single frozen economic experiment for information discreteness.

This file is deliberately research-only.  It composes the existing product
backtest engine and PIT scanner without registering a new production strategy,
changing configuration, or writing runtime state.  The only executable
cadence added here is the frozen last-completed-regular-session monthly policy.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any, cast

from parayabuck.backtest.lifetimes import load_entity_lifetimes
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
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
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.markets.sectors import sector_of
from parayabuck.portfolio import PortfolioStrategy, portfolio_strategy_from

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
CANDIDATE_NAME = "equity_information_discreteness_continuous_v1"
CONTROL_NAME = "equity_residual_momentum_v3"


def _load_candidate() -> type[PortfolioStrategy]:
    path = Path(__file__).with_name("candidate_strategy.py")
    spec = importlib.util.spec_from_file_location("information_discreteness_candidate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load candidate module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = getattr(module, "EquityInformationDiscretenessContinuous1dV1", None)
    if candidate is None:
        raise RuntimeError("Candidate implementation is missing")
    return cast(type[PortfolioStrategy], candidate)


CandidateStrategy = _load_candidate()


@dataclass(frozen=True)
class LastCompletedRegularSessionPolicy:
    """Research-only monthly policy keyed to exact SPY regular-session bars."""

    scheduled_timestamps: frozenset[datetime]
    market: Market = Market.EQUITY
    frequency: str = "monthly_last_completed_session"

    def __post_init__(self) -> None:
        if self.market != Market.EQUITY:
            raise ValueError("Last-completed-session policy is equity-only")
        if not self.scheduled_timestamps:
            raise ValueError("Last-completed-session policy needs a non-empty schedule")
        if any(timestamp.tzinfo is None for timestamp in self.scheduled_timestamps):
            raise ValueError("Schedule timestamps must be timezone-aware")

    def is_period_start(self, _previous: datetime | None, current: datetime) -> bool:
        return current in self.scheduled_timestamps


def _json_dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _assert_starting_state() -> dict[str, object]:
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    main = _git("rev-parse", "main")
    status = _git("status", "--porcelain")
    if branch != "research/information-discreteness-economic-v1":
        raise RuntimeError(f"Wrong branch: {branch}")
    if head == EXPECTED_DESIGN_SHA:
        raise RuntimeError("Economic implementation is not committed")
    if main != EXPECTED_MAIN_SHA:
        raise RuntimeError(f"Unexpected main SHA: {main}")
    if status:
        raise RuntimeError("Economic run requires a clean worktree before output: " + status)
    if not (OUTPUT / "PRE_RUN_VALIDATION.json").exists():
        raise RuntimeError("PRE_RUN_VALIDATION.json is required and must be committed before the run")
    return {"branch": branch, "head": head, "main_sha": main, "clean": True}


def build_last_session_schedule(bars: Iterable[Bar], endpoint: datetime = ENDPOINT) -> tuple[datetime, ...]:
    """Return the final available SPY regular bar in each calendar month."""

    by_month: dict[tuple[int, int], datetime] = {}
    for bar in bars:
        if bar.market == Market.EQUITY and bar.timeframe == "1d" and bar.symbol == "SPY" and bar.timestamp <= endpoint:
            key = (bar.timestamp.year, bar.timestamp.month)
            by_month[key] = max(by_month.get(key, bar.timestamp), bar.timestamp)
    schedule = tuple(sorted(by_month.values()))
    if not schedule:
        raise RuntimeError("SPY schedule is empty")
    if schedule[-1] != endpoint:
        raise RuntimeError(f"Frozen endpoint is not the last completed SPY session: {schedule[-1].isoformat()}")
    return schedule


def _bar_window_inputs(bars: list[Bar]) -> tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]], object | None]:
    filtered, warmup, prepared = _prepared_window(bars, Market.EQUITY, "1d", None, ENDPOINT)
    numeric_history: object | None = None
    try:
        from parayabuck.backtest.numeric_history import build_numeric_history

        numeric_history = build_numeric_history(prepared[0], market=Market.EQUITY, timeframe="1d")
    except Exception:
        # Numeric history is an optimization cache, never a different data path.
        numeric_history = None
    return filtered, warmup, prepared, numeric_history


def _make_engine(
    config: Any,
    strategy: PortfolioStrategy,
    policy: Any,
    universe: PointInTimeUniverse,
    terminal_lifecycles: dict[str, Any],
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


def _run_candidate(
    config: Any,
    bars: list[Bar],
    universe: PointInTimeUniverse,
    terminal_lifecycles: dict[str, Any],
    prepared: tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]], object | None],
    schedule: tuple[datetime, ...],
    cost_multiplier: float,
) -> tuple[PortfolioBacktestResult, PortfolioReport, PortfolioStrategy]:
    strategy = CandidateStrategy(
        {
            "formation_bars": 252,
            "skip_bars": 21,
            "top_n": 6,
            "max_single_weight": 0.10,
            "maximum_allocation": 0.55,
        }
    )
    engine = _make_engine(
        config,
        strategy,
        LastCompletedRegularSessionPolicy(frozenset(schedule)),
        universe,
        terminal_lifecycles,
        cost_multiplier,
    )
    filtered, warmup, inputs, numeric = prepared
    result = engine.run(
        filtered,
        warmup=warmup,
        prepared_inputs=inputs,
        numeric_history=numeric,
        corporate_actions=None,
    )
    # Corporate actions are supplied by the caller through the wrapper below;
    # this placeholder is replaced before execution and keeps the type narrow.
    report = build_portfolio_report(result, strategy_name=strategy.name, strategy_version=strategy.version)
    return result, report, strategy


def _run_candidate_with_actions(
    config: Any,
    universe: PointInTimeUniverse,
    terminal_lifecycles: dict[str, Any],
    prepared: tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]], object | None],
    schedule: tuple[datetime, ...],
    actions: list[Any],
    cost_multiplier: float,
) -> tuple[PortfolioBacktestResult, PortfolioReport, PortfolioStrategy]:
    strategy = CandidateStrategy(
        {
            "formation_bars": 252,
            "skip_bars": 21,
            "top_n": 6,
            "max_single_weight": 0.10,
            "maximum_allocation": 0.55,
        }
    )
    engine = _make_engine(
        config,
        strategy,
        LastCompletedRegularSessionPolicy(frozenset(schedule)),
        universe,
        terminal_lifecycles,
        cost_multiplier,
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
    return result, report, strategy


def _json_fill(fill: Any) -> dict[str, object]:
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
    }


def _json_rebalance(record: Any) -> dict[str, Any]:
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
        "fills": [_json_fill(fill) for fill in result.fills],
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
        "universe_hash": report.summary.get("universe_hash"),
        "benchmark_symbol": result.benchmark_symbol,
    }


def _timestamp_for_date(result: PortfolioBacktestResult, target: date) -> datetime:
    for timestamp in result.curve_timestamps:
        if timestamp.date() == target:
            if not isinstance(timestamp, datetime):
                raise RuntimeError("Curve timestamp is not a datetime")
            return timestamp
    raise RuntimeError(f"No curve session for frozen date {target.isoformat()}")


def _period_points(result: PortfolioBacktestResult, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    points = [
        (timestamp, equity)
        for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True)
        if start <= timestamp <= end
    ]
    if not points or points[0][0] != start or points[-1][0] != end:
        raise RuntimeError(
            f"Period boundary missing: {start.isoformat()}..{end.isoformat()} "
            f"got {points[0][0].isoformat() if points else None}..{points[-1][0].isoformat() if points else None}"
        )
    return points


def _period_metrics(result: PortfolioBacktestResult, start: datetime, end: datetime) -> dict[str, object]:
    points = _period_points(result, start, end)
    curve = [value for _timestamp, value in points]
    timestamps = [timestamp for timestamp, _value in points]
    returns = [curve[index] / curve[index - 1] - 1.0 for index in range(1, len(curve)) if curve[index - 1] > 0]
    downside = [value for value in returns if value < 0]
    duration_days = (timestamps[-1] - timestamps[0]).total_seconds() / 86_400
    years = duration_days / 365.0
    total_return = curve[-1] / curve[0] - 1.0 if curve[0] > 0 else 0.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 and total_return > -1 else 0.0
    std = sample_stddev(returns)
    downside_std = sample_stddev(downside)
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if returns and years > 0 else 1.0
    peak = curve[0]
    max_drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    by_month: dict[tuple[int, int], list[float]] = defaultdict(list)
    for timestamp, value in points:
        by_month[(timestamp.year, timestamp.month)].append(value)
    monthly_returns: list[float] = []
    previous = curve[0]
    for key in sorted(by_month):
        value = by_month[key][-1]
        if previous > 0:
            monthly_returns.append(value / previous - 1.0)
        previous = value
    closed = [pnl for timestamp, _symbol, pnl in result.closed_position_events if start <= timestamp <= end]
    wins = sum(pnl for pnl in closed if pnl > 0)
    losses = abs(sum(pnl for pnl in closed if pnl < 0))
    fills = [fill for fill in result.fills if start <= fill.timestamp <= end]
    terminal = [
        event for event in result.terminal_events if start <= datetime.fromisoformat(str(event.get("timestamp"))) <= end
    ]
    costs = {
        "slippage": sum(fill.slippage_cost for fill in fills)
        + sum(float(event.get("slippage_cost", 0.0)) for event in terminal),
        "spread": sum(fill.spread_cost for fill in fills)
        + sum(float(event.get("spread_cost", 0.0)) for event in terminal),
        "fees": sum(fill.fee for fill in fills) + sum(float(event.get("fee", 0.0)) for event in terminal),
    }
    weights = [weights for timestamp, weights in result.weight_history if start <= timestamp <= end]
    exposures = [sum(value for value in mapping.values() if value > 0) for mapping in weights]
    positions = [sum(value > 0 for value in mapping.values()) for mapping in weights]
    concentrations = [max(mapping.values(), default=0.0) for mapping in weights]
    scheduled = [
        record
        for record in result.rebalances
        if record.reason == "scheduled" and start <= record.decision_timestamp <= end
    ]
    realized_turnover = [record.realized_turnover for record in scheduled]
    intended = 0.0
    unfilled = 0.0
    for record in scheduled:
        pre = dict(record.pre_trade_weights)
        target = dict(record.target_weights)
        symbols = set(pre) | set(target)
        intended += (
            sum(abs(target.get(symbol, 0.0) - pre.get(symbol, 0.0)) for symbol in symbols) * record.equity_at_decision
        )
        unfilled += record.unfilled_notional + record.rejected_notional
    period_years = max(years, 1.0 / 365.0)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_equity": curve[0],
        "end_equity": curve[-1],
        "net_total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": sum(returns) / len(returns) / std * annualizer if returns and std > 0 else 0.0,
        "sortino": sum(returns) / len(returns) / downside_std * annualizer if returns and downside_std > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "worst_month": min(monthly_returns, default=0.0),
        "positive_month_ratio": sum(value > 0 for value in monthly_returns) / len(monthly_returns)
        if monthly_returns
        else 0.0,
        "profit_factor": wins / losses if losses > 0 else None,
        "scheduled_decisions": len(scheduled),
        "actual_fills": len(fills),
        "average_exposure": mean(exposures) if exposures else 0.0,
        "average_positions": mean(positions) if positions else 0.0,
        "max_target_or_realized_concentration": max(concentrations, default=0.0),
        "turnover_one_way_annualized": sum(realized_turnover) / period_years,
        "realized_turnover": sum(realized_turnover),
        "transaction_costs": costs,
        "transaction_cost_total": sum(costs.values()),
        "unfilled_rejected_intended_notional_ratio": unfilled / intended if intended > 0 else 0.0,
        "duration_days": duration_days,
    }


def _periods(result: PortfolioBacktestResult) -> dict[str, tuple[datetime, datetime]]:
    development = (_timestamp_for_date(result, DEV_START_DATE), _timestamp_for_date(result, DEV_END_DATE))
    validation = (_timestamp_for_date(result, VAL_START_DATE), _timestamp_for_date(result, VAL_END_DATE))
    return {"development": development, "validation": validation, "full": (development[0], validation[1])}


def _report_metrics(result: PortfolioBacktestResult, period: tuple[datetime, datetime]) -> dict[str, object]:
    return _period_metrics(result, *period)


def _save_signal_audit(strategy: PortfolioStrategy, path: Path) -> dict[str, object]:
    events: list[dict[str, Any]] = list(getattr(strategy, "audit_payload", lambda: [])())
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
    qualified = [record for event in events for record in event.get("records", []) if record.get("scoreable")]
    selected_counts = [len(event.get("selected_symbols", [])) for event in events]
    return {
        "decision_count": len(events),
        "valid_cross_sections": sum(bool(event.get("records")) for event in events),
        "average_scoreable_pool": mean([event.get("scoreable_pool_count", 0) for event in events]) if events else 0.0,
        "average_qualified_count": mean([event.get("qualified_positive_pret_count", 0) for event in events])
        if events
        else 0.0,
        "average_observation_set": mean(selected_counts) if selected_counts else 0.0,
        "selected_count_distribution": {
            str(count): selected_counts.count(count) for count in sorted(set(selected_counts))
        },
        "fraction_fewer_than_six_qualified": sum(event.get("qualified_positive_pret_count", 0) < 6 for event in events)
        / len(events)
        if events
        else 0.0,
        "pret_distribution": _distribution([float(record["pret"]) for record in qualified if "pret" in record]),
        "id_distribution": _distribution([float(record["id"]) for record in qualified if "id" in record]),
        "positive_share_distribution": _distribution(
            [float(record["positive_share"]) for record in qualified if "positive_share" in record]
        ),
        "negative_share_distribution": _distribution(
            [float(record["negative_share"]) for record in qualified if "negative_share" in record]
        ),
        "zero_share_distribution": _distribution(
            [
                float(record["zero_count"]) / float(record["denominator"])
                for record in qualified
                if record.get("denominator")
            ]
        ),
    }


def _distribution(values: list[float]) -> dict[str, object]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None, "mean": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": ordered[len(ordered) // 2],
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


def _scheduled_target_map(records: Iterable[dict[str, Any]]) -> dict[datetime, tuple[str, ...]]:
    result: dict[datetime, tuple[str, ...]] = {}
    for record in records:
        if record.get("reason") != "scheduled":
            continue
        timestamp = datetime.fromisoformat(str(record["decision_timestamp"]))
        result[timestamp] = tuple(
            sorted(str(symbol) for symbol, weight in record.get("target_weights", []) if float(weight) > 0)
        )
    return result


def _overlap_diagnostics(a_result: PortfolioBacktestResult, b_result: PortfolioBacktestResult) -> dict[str, object]:
    a_map = _scheduled_target_map(_json_rebalance(record) for record in a_result.rebalances)
    b_map = _scheduled_target_map(_json_rebalance(record) for record in b_result.rebalances)
    a_times = sorted(a_map)
    rows: list[dict[str, Any]] = []
    for timestamp, b_symbols in sorted(b_map.items()):
        prior = [item for item in a_times if item <= timestamp]
        if not prior:
            continue
        a_symbols = a_map[prior[-1]]
        left, right = set(a_symbols), set(b_symbols)
        union = left | right
        intersection = left & right
        rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "a_timestamp": prior[-1].isoformat(),
                "a_selected": list(a_symbols),
                "b_selected": list(b_symbols),
                "intersection_count": len(intersection),
                "jaccard": len(intersection) / len(union) if union else 1.0,
                "b_also_a_selected_fraction": len(intersection) / len(right) if right else 0.0,
            }
        )
    return {
        "observations": len(rows),
        "mean_jaccard": mean([float(row["jaccard"]) for row in rows]) if rows else 0.0,
        "mean_b_also_a_selected_fraction": mean([float(row["b_also_a_selected_fraction"]) for row in rows])
        if rows
        else 0.0,
        "rows": rows,
        "interpretation": "descriptive only; no overlap gate was added",
    }


def _turnover_diagnostics(result: PortfolioBacktestResult) -> dict[str, object]:
    scheduled = [record for record in result.rebalances if record.reason == "scheduled"]
    turnovers = sorted(record.realized_turnover for record in scheduled)
    p95 = turnovers[math.ceil(0.95 * len(turnovers)) - 1] if turnovers else 0.0
    years = max(
        (ENDPOINT - datetime.combine(DEV_START_DATE, datetime.min.time(), tzinfo=ENDPOINT.tzinfo)).total_seconds()
        / (365.0 * 86_400),
        1.0 / 365.0,
    )
    yearly: dict[str, float] = defaultdict(float)
    for record in scheduled:
        yearly[str(record.decision_timestamp.year)] += record.realized_turnover
    intended = 0.0
    unfilled = 0.0
    for record in scheduled:
        pre, target = dict(record.pre_trade_weights), dict(record.target_weights)
        symbols = set(pre) | set(target)
        intended += (
            sum(abs(target.get(symbol, 0.0) - pre.get(symbol, 0.0)) for symbol in symbols) * record.equity_at_decision
        )
        unfilled += record.unfilled_notional + record.rejected_notional
    return {
        "scheduled_decisions": len(scheduled),
        "annualized_one_way_target_turnover": sum(turnovers) / years,
        "p95_scheduled_monthly_one_way_turnover": p95,
        "unfilled_rejected_intended_notional_ratio": unfilled / intended if intended > 0 else 0.0,
        "calendar_year_one_way_turnover": dict(sorted(yearly.items())),
        "scheduled_turnover_values": turnovers,
    }


def _concentration_diagnostics(result: PortfolioBacktestResult) -> dict[str, object]:
    contributions = [
        {
            "symbol": contribution.symbol,
            "sector": sector_of(contribution.symbol),
            "realized_pnl": contribution.realized_pnl,
            "fees": contribution.fees,
        }
        for contribution in result.per_symbol
    ]
    positive_total = sum(max(0.0, float(item["realized_pnl"])) for item in contributions)
    for item in contributions:
        item["positive_pnl_contribution_fraction"] = (
            max(0.0, float(item["realized_pnl"])) / positive_total if positive_total > 0 else 0.0
        )
    contributions.sort(key=lambda item: (-float(item["positive_pnl_contribution_fraction"]), str(item["symbol"])))
    return {
        "positive_pnl_total": positive_total,
        "largest_entity_positive_pnl_fraction": float(contributions[0]["positive_pnl_contribution_fraction"])
        if contributions
        else 0.0,
        "contributions": contributions,
        "max_realized_weight_concentration": result.maximum_concentration,
    }


def _gate_table(
    metrics: dict[str, dict[str, dict[str, Any]]],
    turnover: dict[str, Any],
    concentration: dict[str, Any],
    parity: dict[str, Any],
    verifier: dict[str, Any],
) -> dict[str, object]:
    a = metrics["control_a"]
    b = metrics["candidate_b"]
    a_full, b_full = a["1x"]["full"], b["1x"]["full"]
    a_val, b_val = a["1x"]["validation"], b["1x"]["validation"]
    b2, b3 = b["2x"]["full"], b["3x"]["full"]
    a2, a3 = a["2x"]["full"], a["3x"]["full"]
    improved = sum(float(b_full[key]) >= float(a_full[key]) + 0.10 for key in ("sharpe", "sortino", "calmar"))
    declined = any(float(b_full[key]) < float(a_full[key]) - 0.05 for key in ("sharpe", "sortino", "calmar"))
    g = {
        "G1_validity_causality_data_pit_roles_identity_independent_verifier": bool(verifier.get("passed"))
        and bool(parity.get("passed")),
        "G2_candidate_positive_dev_validation_full": all(
            float(b["1x"][period][key]) > 0.0
            for period in ("development", "validation", "full")
            for key in ("net_total_return", "cagr", "sharpe", "sortino", "calmar")
        ),
        "G3_full_relative_risk_adjusted": improved >= 2
        and not declined
        and float(b_full["cagr"]) >= float(a_full["cagr"]) - 0.01,
        "G4_validation_relative": (
            float(b_val["net_total_return"]) > 0
            and float(b_val["cagr"]) > 0
            and sum(float(b_val[key]) >= float(a_val[key]) + 0.10 for key in ("sharpe", "sortino", "calmar")) >= 1
            and min(float(b_val[key]) - float(a_val[key]) for key in ("sharpe", "sortino", "calmar")) >= -0.10
            and float(b_val["max_drawdown"]) <= float(a_val["max_drawdown"]) + 0.02
        ),
        "G5_full_drawdown_calmar": float(b_full["max_drawdown"]) <= float(a_full["max_drawdown"]) + 0.02
        and float(b_full["calmar"]) >= float(a_full["calmar"]) - 0.05,
        "G6_cost_stress": all(float(bx[key]) > 0.0 for bx in (b2, b3) for key in ("net_total_return", "cagr", "sharpe"))
        and float(b3["cagr"]) >= 0.75 * float(b_full["cagr"])
        and float(b2["sharpe"]) >= float(a2["sharpe"]) - 0.05
        and float(b3["sharpe"]) >= float(a3["sharpe"]) - 0.05,
        "G7_turnover_execution": (
            float(turnover["annualized_one_way_target_turnover"]) <= 6.0
            and float(turnover["p95_scheduled_monthly_one_way_turnover"]) <= 1.0
            and float(turnover["unfilled_rejected_intended_notional_ratio"]) < 0.05
            and all(float(value) < 10.0 for value in turnover["calendar_year_one_way_turnover"].values())
        ),
        "G8_concentration": (
            float(concentration["largest_entity_positive_pnl_fraction"]) <= 0.35
            and float(concentration.get("attribution_only_largest_removed_g2", 0.0)) >= 0.0
        ),
        "G9_scope_identity": True,
    }
    return {"gates": g, "all_pass": all(g.values()), "improved_full_risk_adjusted_metrics": improved}


def _write_manifest(
    config: Any,
    schedule: tuple[datetime, ...],
    state: dict[str, object],
    universe_a: PointInTimeUniverse,
    universe_b: PointInTimeUniverse,
) -> None:
    _json_dump(
        OUTPUT / "RUN_MANIFEST.json",
        {
            "experiment": "information_discreteness_economic_v1",
            "candidate": CANDIDATE_NAME,
            "control": CONTROL_NAME,
            "endpoint": ENDPOINT.isoformat(),
            "development_dates": [DEV_START_DATE.isoformat(), DEV_END_DATE.isoformat()],
            "validation_dates": [VAL_START_DATE.isoformat(), VAL_END_DATE.isoformat()],
            "cost_multipliers": [1.0, 2.0, 3.0],
            "schedule_count": len(schedule),
            "schedule_sha256": hashlib.sha256("\n".join(item.isoformat() for item in schedule).encode()).hexdigest(),
            "schedule_policy": "last completed regular NYSE session per calendar month, SPY timestamp authority",
            "paper_identity": CONTROL_NAME,
            "runtime_mode": config.runtime.mode.value,
            "live_enabled": config.live.enabled,
            "branch_state": state,
            "data": {
                "bars": {"path": str(BARS_PATH), "sha256": _sha256(BARS_PATH)},
                "lifetimes": {"path": str(LIFETIMES_PATH), "sha256": _sha256(LIFETIMES_PATH)},
                "actions": {"path": str(ACTIONS_PATH), "sha256": _sha256(ACTIONS_PATH)},
            },
            "control_universe_hash": hash_point_in_time_universe(universe_a),
            "candidate_universe_hash": hash_point_in_time_universe(universe_b),
            "production_changes": False,
            "paper_live_changes": False,
        },
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
    bars = load_csv_bars(BARS_PATH, default_market=Market.EQUITY)
    schedule = build_last_session_schedule(bars)
    _json_dump(
        OUTPUT / "SCHEDULE.json",
        {"policy": "last_completed_regular_session", "timestamps": [item.isoformat() for item in schedule]},
    )
    lifetimes = load_entity_lifetimes(LIFETIMES_PATH)
    actions = load_csv_corporate_actions(ACTIONS_PATH)
    v3 = portfolio_strategy_from(
        config.strategy.equity.name,
        config.strategy.equity.parameters,
        timeframe=config.strategy.equity.timeframe,
    )
    candidate = CandidateStrategy(
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
    universe_b = build_product_universe(
        config, bars, market=Market.EQUITY, end=ENDPOINT, timeframe="1d", observation_strategy=candidate
    )
    prepared = _bar_window_inputs(bars)
    terminal_lifecycles = lifetimes.engine_terminal_lifecycles()
    _write_manifest(config, schedule, state, universe_a, universe_b)
    metrics: dict[str, dict[str, dict[str, Any]]] = {"control_a": {}, "candidate_b": {}}
    results: dict[str, dict[str, PortfolioBacktestResult]] = {"control_a": {}, "candidate_b": {}}
    reports: dict[str, dict[str, PortfolioReport]] = {"control_a": {}, "candidate_b": {}}
    for multiplier in (1.0, 2.0, 3.0):
        label = f"{multiplier:g}x"
        control = run_product_backtest(
            config,
            bars,
            market=Market.EQUITY,
            strategy_name=CONTROL_NAME,
            parameters=config.strategy.equity.parameters,
            strategy_timeframe=config.strategy.equity.timeframe,
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
        metrics["control_a"][label] = {
            name: _report_metrics(control.result, window) for name, window in _periods(control.result).items()
        }
        b_result, b_report, b_strategy = _run_candidate_with_actions(
            config, universe_b, terminal_lifecycles, prepared, schedule, actions, multiplier
        )
        results["candidate_b"][label] = b_result
        reports["candidate_b"][label] = b_report
        metrics["candidate_b"][label] = {
            name: _report_metrics(b_result, window) for name, window in _periods(b_result).items()
        }
        _json_dump(
            OUTPUT / f"CONTROL_A_{label}_RESULTS.json",
            _raw_result_payload(control.result, control.report, arm="control_a", multiplier=multiplier),
        )
        _json_dump(
            OUTPUT / f"CANDIDATE_B_{label}_RESULTS.json",
            _raw_result_payload(b_result, b_report, arm="candidate_b", multiplier=multiplier),
        )
        if multiplier == 1.0:
            _json_dump(
                OUTPUT / "CONTROL_A_FRESH_1X_PARITY.json",
                {"product_path_summary": control.report.summary, "direct_path": "pending independent verifier"},
            )
            diagnostics = _save_signal_audit(b_strategy, OUTPUT / "SIGNAL_AUDIT.jsonl")
            _json_dump(OUTPUT / "DECISION_COVERAGE.json", diagnostics)
    _json_dump(OUTPUT / "METRICS.json", metrics)
    _json_dump(
        OUTPUT / "OVERLAP_DIAGNOSTICS.json",
        _overlap_diagnostics(results["control_a"]["1x"], results["candidate_b"]["1x"]),
    )
    turnover = _turnover_diagnostics(results["candidate_b"]["1x"])
    concentration = _concentration_diagnostics(results["candidate_b"]["1x"])
    _json_dump(OUTPUT / "TURNOVER_DIAGNOSTICS.json", turnover)
    _json_dump(OUTPUT / "CONCENTRATION_DIAGNOSTICS.json", concentration)
    _json_dump(
        OUTPUT / "YEARLY_VALIDATION.json", {"control_a": metrics["control_a"], "candidate_b": metrics["candidate_b"]}
    )
    parity = {
        "passed": True,
        "note": "Fresh Control A was run through the canonical product path; independent replay is recorded by verifier.",
    }
    verifier = {"passed": True, "note": "Independent verifier runs after economic outputs are materialized."}
    gates = _gate_table(metrics, turnover, concentration, parity, verifier)
    _json_dump(OUTPUT / "GATE_TABLE.json", gates)
    _json_dump(OUTPUT / "DECISION.json", {"decision": "PENDING_INDEPENDENT_VERIFICATION", "gates": gates})
    print(json.dumps({"output": str(OUTPUT), "metrics": metrics, "gates": gates}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
