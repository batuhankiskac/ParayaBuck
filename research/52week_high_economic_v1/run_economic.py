#!/usr/bin/env python3
"""Run one frozen A/B economic experiment on the isolated research branch."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import statistics
import subprocess
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from parayabuck.backtest.lifetimes import load_entity_lifetimes
from parayabuck.backtest.numeric_history import build_numeric_history
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.portfolio_report import build_portfolio_report
from parayabuck.backtest.product import (
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

candidate_module = importlib.import_module("research.52week_high_readiness_v1.candidate")
month_end_module = importlib.import_module("research.52week_high_readiness_v1.month_end")

BARS_PATH = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIMES_PATH = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
ACTIONS_PATH = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CONFIG_PATH = ROOT / "config/settings.yaml"
READINESS_DIR = ROOT / "research/52week_high_readiness_v1"
START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)
RESERVED_ROLES = tuple(sorted(candidate_module.RESERVED_ROLES))
EXPECTED_BARS_SHA256 = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_LIFETIMES_SHA256 = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_ACTIONS_SHA256 = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
EXPECTED_DESIGN_SHA = "72b51a0e5c03f2ce0a8790b197cf360b26b33cd3"
EXPECTED_READINESS_SHA = "339d62f404a0b924ec78e3a67d8cddad7cee24c7"
EXPECTED_MAIN_SHA = "839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


class CandidateMonthEndPolicy:
    """Adapter exposing the verified research scheduler to the engine."""

    def __init__(self) -> None:
        self._policy = month_end_module.NyseLastCompletedSessionPolicy()

    def is_period_start(self, _previous: datetime | None, current: datetime) -> bool:
        return self._policy.should_decide(current)


class CandidateBroadPoolEngine(PortfolioBacktestEngine):
    """Research-only engine view: score scanner symbols through completed t."""

    def _effective_observation_symbols(self, timestamp: datetime) -> tuple[str, ...] | None:
        snapshots = getattr(self.universe, "snapshots", ())
        index = bisect_right(snapshots, timestamp, key=lambda item: item.effective_at)
        if index == 0:
            return ()
        pool = tuple(sorted(snapshots[index - 1].symbols))
        # A missing current bar makes only that entity unscoreable; it must not
        # suppress the remaining broad pool's monthly decision.
        visible: list[str] = []
        for symbol in pool:
            bars = self._history.get((symbol, self.strategy.timeframe), ())
            if bars and bars[-1].timestamp == timestamp:
                visible.append(symbol)
        return tuple(visible)


class Equity52WeekHighCloseV1(PortfolioStrategy):
    """Frozen Candidate B strategy; target construction only, no retuning."""

    name = "equity_52week_high_close_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "52-week high close (frozen research candidate)"
    rebalance_policy_declared = "research-month-end-last-nyse-session"

    def __init__(self, unresolved_symbols: set[str]) -> None:
        self.unresolved_symbols = frozenset(unresolved_symbols)
        self.audit: list[dict[str, object]] = []
        super().__init__({})

    @property
    def minimum_history_bars(self) -> int:
        return candidate_module.LOOKBACK_SESSIONS

    def _validate_parameters(self) -> None:
        if (
            candidate_module.TOP_N != 6
            or candidate_module.MAX_ALLOCATION != 0.55
            or candidate_module.MAX_WEIGHT != 0.10
        ):
            raise ValueError("Frozen candidate constants changed")

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        result = self._rank(context)
        return result.observation_set

    def _rank(self, context: PortfolioContext):
        snapshots = getattr(context.universe, "snapshots", ())
        index = bisect_right(snapshots, context.decision_timestamp, key=lambda item: item.effective_at)
        if index == 0:
            return candidate_module.CandidateRankResult(context.decision_timestamp, (), (), (), (), (), 1.0)
        pool = tuple(sorted(snapshots[index - 1].symbols))
        benchmark = canonical_symbol("SPY", Market.EQUITY)
        benchmark_history = context.history.get(benchmark, ())
        expected = tuple(bar.timestamp for bar in benchmark_history[-candidate_module.LOOKBACK_SESSIONS :])
        histories = {
            symbol: tuple(
                candidate_module.CloseObservation(bar.timestamp, bar.close)
                for bar in context.history.get(symbol, ())[-candidate_module.LOOKBACK_SESSIONS :]
            )
            for symbol in pool
        }
        identities = {symbol: symbol not in self.unresolved_symbols for symbol in pool}
        return candidate_module.rank_broad_pool_through_t(
            pool,
            histories,
            decision_timestamp=context.decision_timestamp,
            expected_session_timestamps=expected,
            identity_resolved=identities,
            observation_capacity=max(len(pool), candidate_module.TOP_N),
        )

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        result = self._rank(context)
        targets = tuple(
            TargetWeight(symbol, weight, "frozen_52week_high_close", rank=index)
            for index, (symbol, weight) in enumerate(result.target_weights, start=1)
        )
        skipped = max(0, len(result.eligible_pool) - len(result.ranked))
        self.audit.append(
            {
                "decision_timestamp": context.decision_timestamp.isoformat(),
                "eligible_pool_size": len(result.eligible_pool),
                "scoreable_pool_size": len(result.ranked),
                "observation_set_size": len(result.observation_set),
                "selected_name_count": len(result.selected),
                "skipped_unscoreable_count": skipped,
                "selected": list(result.selected),
                "ranked": [[symbol, score] for symbol, score in result.ranked],
                "target_weights": [[weight.symbol, weight.weight] for weight in targets],
                "target_cash_weight": result.target_cash_weight,
                "maximum_visible_timestamp": max(
                    (bar.timestamp for bars in context.history.values() for bar in bars),
                    default=context.decision_timestamp,
                ).isoformat(),
            }
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=result.target_cash_weight,
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version,
            universe_snapshot_id=snapshots_id(context),
            parameters=self.parameter_tuple(),
            metadata={
                "candidate": self.name,
                "eligible_pool_size": len(result.eligible_pool),
                "scoreable_pool_size": len(result.ranked),
                "observation_set_size": len(result.observation_set),
                "selected_name_count": len(result.selected),
            },
        )


def snapshots_id(context: PortfolioContext) -> str:
    snapshots = getattr(context.universe, "snapshots", ())
    index = bisect_right(snapshots, context.decision_timestamp, key=lambda item: item.effective_at)
    return snapshots[index - 1].effective_at.isoformat() if index else ""


@dataclass(frozen=True)
class RunArtifacts:
    arm: str
    multiplier: float
    engine: PortfolioBacktestEngine
    result: Any
    audit: list[dict[str, object]]


def make_engine(config: Any, strategy: PortfolioStrategy, universe: Any, lifecycles: dict[str, Any], multiplier: float):
    selection = config.strategy.equity
    cls = CandidateBroadPoolEngine if isinstance(strategy, Equity52WeekHighCloseV1) else PortfolioBacktestEngine
    return cls(
        strategy,
        CandidateMonthEndPolicy() if isinstance(strategy, Equity52WeekHighCloseV1) else rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * multiplier,
        spread_bps=config.backtest.spread_bps * multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        equity_regulatory_fee_multiplier=multiplier,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=selection.maximum_allocation,
        ordinary_alpha_excluded_symbols=tuple(config.universe.equity.ordinary_alpha_excluded_symbols),
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        terminal_lifecycles=lifecycles,
    )


def run_arm(
    config: Any,
    strategy: PortfolioStrategy,
    universe: Any,
    prepared: tuple[list[Any], list[Any], tuple[list[Any], list[Any]]],
    numeric_history: Any,
    actions: list[Any],
    lifecycles: dict[str, Any],
    multiplier: float,
) -> RunArtifacts:
    engine = make_engine(config, strategy, universe, lifecycles, multiplier)
    filtered, warmup, (ordered, decision_bars) = prepared
    result = engine.run(
        filtered,
        warmup=warmup,
        prepared_inputs=(ordered, decision_bars),
        numeric_history=numeric_history,
        corporate_actions=actions,
    )
    audit = list(getattr(strategy, "audit", ()))
    return RunArtifacts(strategy.name, multiplier, engine, result, audit)


def period_indices(timestamps: tuple[datetime, ...], start: datetime, end: datetime) -> list[int]:
    selected = [index for index, timestamp in enumerate(timestamps) if start <= timestamp <= end]
    if not selected:
        raise ValueError(f"No curve points in {start.isoformat()} -> {end.isoformat()}")
    first = selected[0]
    return ([first - 1] if first > 0 else []) + selected


def period_metrics(artifacts: RunArtifacts, start: datetime, end: datetime) -> dict[str, object]:
    result = artifacts.result
    indices = period_indices(result.curve_timestamps, start, end)
    values = [float(result.equity_curve[index]) for index in indices]
    timestamps = [result.curve_timestamps[index] for index in indices]
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]
    duration_days = (timestamps[-1] - timestamps[0]).total_seconds() / 86_400
    years = duration_days / 365.0
    total_return = values[-1] / values[0] - 1
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 and values[-1] > 0 else 0.0
    std = statistics.stdev(returns) if len(returns) >= 2 else 0.0
    downside = [value for value in returns if value < 0]
    downside_std = statistics.stdev(downside) if len(downside) >= 2 else 0.0
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if years > 0 and returns else 1.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak)
    holdings = getattr(artifacts.engine, "_holdings_history", [])
    period_holdings = [holdings[index] for index in indices]
    exposures = [1.0 - float(cash) for _count, cash, _concentration in period_holdings]
    positions = [float(count) for count, _cash, _concentration in period_holdings]
    realized_concentration = max((float(item[2]) for item in period_holdings), default=0.0)
    in_records = [record for record in result.rebalances if start <= record.decision_timestamp <= end]
    in_fills = [fill for fill in result.fills if start <= fill.timestamp <= end]
    target_turnover = sum(record.expected_turnover for record in in_records)
    realized_turnover = sum(record.realized_turnover for record in in_records)
    expected_notional = sum(record.expected_turnover * record.equity_at_decision for record in in_records)
    unfilled = sum(record.unfilled_notional + record.rejected_notional for record in in_records)
    closed = [pnl for timestamp, _symbol, pnl in result.closed_position_events if start <= timestamp <= end]
    wins = sum(value for value in closed if value > 0)
    losses = abs(sum(value for value in closed if value < 0))
    monthly: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index in indices:
        ts = result.curve_timestamps[index]
        monthly[(ts.year, ts.month)].append(index)
    month_returns: list[float] = []
    previous = values[0]
    for key in sorted(monthly):
        month_value = result.equity_curve[monthly[key][-1]]
        month_returns.append(month_value / previous - 1 if previous > 0 else 0.0)
        previous = month_value
    return {
        "net_total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": (sum(returns) / len(returns)) / std * annualizer if returns and std > 0 else 0.0,
        "sortino": (sum(returns) / len(returns)) / downside_std * annualizer if returns and downside_std > 0 else 0.0,
        "maximum_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "worst_month": min(month_returns, default=0.0),
        "positive_month_ratio": sum(value > 0 for value in month_returns) / len(month_returns)
        if month_returns
        else 0.0,
        "profit_factor": wins / losses if losses > 0 else None,
        "annualized_one_way_target_turnover": target_turnover / years if years > 0 else 0.0,
        "annualized_realized_turnover": realized_turnover / years if years > 0 else 0.0,
        "target_turnover_sum": target_turnover,
        "realized_turnover_sum": realized_turnover,
        "total_transaction_costs": sum(fill.slippage_cost + fill.spread_cost + fill.fee for fill in in_fills)
        + sum(
            float(event.get("slippage_cost", 0.0)) + float(event.get("spread_cost", 0.0)) + float(event.get("fee", 0.0))
            for event in result.terminal_events
            if start <= datetime.fromisoformat(str(event["timestamp"])) <= end
        ),
        "fill_count": len(in_fills),
        "scheduled_decision_count": len(in_records),
        "average_exposure": statistics.mean(exposures) if exposures else 0.0,
        "median_exposure": statistics.median(exposures) if exposures else 0.0,
        "average_positions": statistics.mean(positions) if positions else 0.0,
        "maximum_target_concentration": max(
            (max((weight for _symbol, weight in record.target_weights), default=0.0) for record in in_records),
            default=0.0,
        ),
        "maximum_realized_concentration": realized_concentration,
        "unfilled_rejected_intended_notional": unfilled,
        "intended_notional": expected_notional,
        "unfilled_rejected_fraction": unfilled / expected_notional if expected_notional > 0 else 0.0,
        "start_timestamp": timestamps[0].isoformat(),
        "end_timestamp": timestamps[-1].isoformat(),
    }


def yearly_metrics(artifacts: RunArtifacts) -> dict[str, object]:
    windows = {
        "2022": (datetime(2022, 1, 3, tzinfo=UTC), datetime(2022, 12, 30, 23, 59, 59, tzinfo=UTC)),
        "2023": (datetime(2023, 1, 3, tzinfo=UTC), datetime(2023, 12, 29, 23, 59, 59, tzinfo=UTC)),
        "2024": (datetime(2024, 1, 2, tzinfo=UTC), datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)),
        "2025": (datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)),
        "2026_ytd": (datetime(2026, 1, 2, tzinfo=UTC), END),
    }
    return {key: period_metrics(artifacts, *window) for key, window in windows.items()}


def compact_result(artifacts: RunArtifacts) -> dict[str, object]:
    result = artifacts.result
    engine = artifacts.engine
    report = build_portfolio_report(result, strategy_name=artifacts.arm, strategy_version="1.0.0")
    target_hash_payload = [
        {
            "decision_timestamp": record.decision_timestamp.isoformat(),
            "target_weights": dict(record.target_weights),
            "target_cash_weight": record.target_cash_weight,
            "risk_overlay_halted": record.risk_overlay_halted,
            "risk_overlay_reasons": list(record.risk_overlay_reasons),
        }
        for record in result.rebalances
    ]
    fill_hash_payload = [
        {
            "timestamp": fill.timestamp.isoformat(),
            "decision_timestamp": fill.decision_timestamp.isoformat(),
            "symbol": fill.symbol,
            "side": fill.side.value,
            "qty": fill.qty,
            "reference_price": fill.reference_price,
            "fill_price": fill.fill_price,
            "slippage_cost": fill.slippage_cost,
            "spread_cost": fill.spread_cost,
            "fee": fill.fee,
        }
        for fill in result.fills
    ]
    return {
        "arm": artifacts.arm,
        "cost_multiplier": artifacts.multiplier,
        "full_result_summary": report.summary,
        "periods": {
            "development": period_metrics(artifacts, START, DEVELOPMENT_END),
            "validation": period_metrics(artifacts, VALIDATION_START, END),
            "full": period_metrics(artifacts, START, END),
        },
        "validation_years": yearly_metrics(artifacts),
        "counts": {
            "curve_points": len(result.equity_curve),
            "scheduled_decisions": len(result.rebalances),
            "fills": len(result.fills),
            "terminal_events": len(result.terminal_events),
        },
        "hashes": {
            "curve": digest(
                [
                    [ts.isoformat(), value]
                    for ts, value in zip(result.curve_timestamps, result.equity_curve, strict=True)
                ]
            ),
            "targets": digest(target_hash_payload),
            "fills": digest(fill_hash_payload),
            "terminal_events": digest(list(result.terminal_events)),
        },
        "evidence": {
            "equity_curve": [
                [ts.isoformat(), value] for ts, value in zip(result.curve_timestamps, result.equity_curve, strict=True)
            ],
            "holdings_history": list(getattr(engine, "_holdings_history", [])),
            "rebalances": [
                {
                    "decision_timestamp": record.decision_timestamp.isoformat(),
                    "target_weights": list(record.target_weights),
                    "target_cash_weight": record.target_cash_weight,
                    "expected_turnover": record.expected_turnover,
                    "realized_turnover": record.realized_turnover,
                    "unfilled_notional": record.unfilled_notional,
                    "rejected_notional": record.rejected_notional,
                    "equity_at_decision": record.equity_at_decision,
                    "risk_overlay_reasons": list(record.risk_overlay_reasons),
                }
                for record in result.rebalances
            ],
            "fills": [
                {
                    "timestamp": fill.timestamp.isoformat(),
                    "decision_timestamp": fill.decision_timestamp.isoformat(),
                    "symbol": fill.symbol,
                    "side": fill.side.value,
                    "qty": fill.qty,
                    "reference_price": fill.reference_price,
                    "fill_price": fill.fill_price,
                    "slippage_cost": fill.slippage_cost,
                    "spread_cost": fill.spread_cost,
                    "fee": fill.fee,
                }
                for fill in result.fills
            ],
            "terminal_events": list(result.terminal_events),
            "realized_pnl_events": [
                [ts.isoformat(), symbol, value] for ts, symbol, value in result.realized_pnl_events
            ],
            "per_symbol": [
                {
                    "symbol": item.symbol,
                    "realized_pnl": item.realized_pnl,
                    "fees": item.fees,
                    "final_weight": item.final_weight,
                }
                for item in result.per_symbol
            ],
        },
        "candidate_audit": artifacts.audit,
    }


def frozen_input_receipt(
    config: Any, universe: Any, lifetimes: Any, actions: list[Any], bars: list[Any]
) -> dict[str, object]:
    latest: dict[str, datetime] = {}
    for bar in bars:
        if bar.market == Market.EQUITY and bar.timeframe == "1d":
            latest[bar.symbol] = max(latest.get(bar.symbol, bar.timestamp), bar.timestamp)
    common_endpoint = min(latest[symbol] for symbol in RESERVED_ROLES)
    readiness_decision = json.loads((READINESS_DIR / "G1_READINESS_DECISION.json").read_text(encoding="utf-8"))
    readiness_verification = json.loads((READINESS_DIR / "INDEPENDENT_VERIFICATION.json").read_text(encoding="utf-8"))
    return {
        "main_sha": git("rev-parse", "main"),
        "origin_main_sha": git("rev-parse", "origin/main"),
        "design_sha": git("rev-parse", "research/52week-high-design-v1"),
        "readiness_sha": git("rev-parse", "research/52week-high-readiness-v1"),
        "economic_branch_sha_at_start": git("rev-parse", "HEAD"),
        "expected_main_sha": EXPECTED_MAIN_SHA,
        "expected_design_sha": EXPECTED_DESIGN_SHA,
        "expected_readiness_sha": EXPECTED_READINESS_SHA,
        "candidate": "equity_52week_high_close_v1",
        "design_input_hashes": json.loads((READINESS_DIR / "DESIGN_INPUT_RECEIPT.json").read_text(encoding="utf-8")),
        "readiness_decision": readiness_decision["decision"],
        "readiness_verification_passed": readiness_verification["passed"],
        "candidate_economics_existed_before_run": False,
        "bars_sha256": sha256(BARS_PATH),
        "lifetimes_sha256": sha256(LIFETIMES_PATH),
        "corporate_actions_sha256": sha256(ACTIONS_PATH),
        "bar_count": len(bars),
        "lifetime_count": lifetimes.receipt.row_count,
        "action_count_after_dedup": len(actions),
        "common_reserved_endpoint": common_endpoint.isoformat(),
        "paper_strategy": config.strategy.equity.name,
        "live_enabled": config.live.enabled,
        "universe_hash": hash_point_in_time_universe(universe),
    }


def compute_fresh_control_parity(
    control_1x: dict[str, object], expected_parity: dict[str, object]
) -> dict[str, object]:
    fresh_counts = {
        "curve_points": control_1x["counts"]["curve_points"],
        "targets": control_1x["counts"]["scheduled_decisions"],
        "fills": control_1x["counts"]["fills"],
        "terminal_events": control_1x["counts"]["terminal_events"],
    }
    expected_counts = {
        "curve_points": expected_parity["expected_counts"]["curve_points"],
        "targets": expected_parity["expected_counts"]["targets"],
        "fills": expected_parity["expected_counts"]["fills"],
        "terminal_events": expected_parity["expected_counts"]["terminal_events"],
    }
    parity = {
        "fresh_counts": fresh_counts,
        "expected_counts": expected_counts,
        "target_hash": control_1x["hashes"]["targets"],
        "expected_target_hash": expected_parity["hashes"]["expected_targets"],
        "fill_hash": control_1x["hashes"]["fills"],
        "expected_fill_hash": expected_parity["hashes"]["expected_fills"],
        "terminal_hash": control_1x["hashes"]["terminal_events"],
        "expected_terminal_hash": expected_parity["hashes"]["expected_terminal_events"],
        "max_metric_difference_from_readiness": expected_parity["max_absolute_metric_difference"],
    }
    parity["passed"] = (
        fresh_counts == expected_counts
        and parity["target_hash"] == parity["expected_target_hash"]
        and parity["fill_hash"] == parity["expected_fill_hash"]
        and parity["terminal_hash"] == parity["expected_terminal_hash"]
        and parity["max_metric_difference_from_readiness"] <= 1e-8
    )
    return parity


def main() -> None:
    config = load_config(CONFIG_PATH)
    if git("rev-parse", "main") != EXPECTED_MAIN_SHA or git("rev-parse", "origin/main") != EXPECTED_MAIN_SHA:
        raise SystemExit("RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT: corrected main moved")
    if git("rev-parse", "research/52week-high-readiness-v1") != EXPECTED_READINESS_SHA:
        raise SystemExit("RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT: readiness tip moved")
    decision = json.loads((READINESS_DIR / "G1_READINESS_DECISION.json").read_text(encoding="utf-8"))
    verification = json.loads((READINESS_DIR / "INDEPENDENT_VERIFICATION.json").read_text(encoding="utf-8"))
    if decision["decision"] != "G1_READY_FOR_FROZEN_ECONOMIC_TEST" or not verification["passed"]:
        raise SystemExit("RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT: readiness premise failed")
    if config.strategy.equity.name != "equity_residual_momentum_v3" or config.live.enabled:
        raise SystemExit("RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT: production identity changed")
    for source, expected in (
        (BARS_PATH, EXPECTED_BARS_SHA256),
        (LIFETIMES_PATH, EXPECTED_LIFETIMES_SHA256),
        (ACTIONS_PATH, EXPECTED_ACTIONS_SHA256),
    ):
        if sha256(source) != expected:
            raise SystemExit("RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT: canonical source hash changed")
    bars = load_csv_bars(BARS_PATH, default_market=Market.EQUITY)
    lifetimes = load_entity_lifetimes(LIFETIMES_PATH)
    actions = load_csv_corporate_actions(ACTIONS_PATH)
    strategy_a = portfolio_strategy_from(
        config.strategy.equity.name,
        config.strategy.equity.parameters,
        timeframe=config.strategy.equity.timeframe,
    )
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=END,
        timeframe=strategy_a.timeframe,
        observation_strategy=strategy_a,
    )
    prepared = _prepared_window(bars, Market.EQUITY, strategy_a.timeframe, START, END)
    filtered, warmup, (ordered, _decision_bars) = prepared
    numeric = build_numeric_history(warmup + ordered, market=Market.EQUITY, timeframe=strategy_a.timeframe)
    lifecycles = lifetimes.engine_terminal_lifecycles()
    receipt = frozen_input_receipt(config, universe, lifetimes, actions, bars)
    (OUT / "FROZEN_INPUT_RECEIPT.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    expected_parity = json.loads((READINESS_DIR / "V3_CONTROL_PARITY.json").read_text(encoding="utf-8"))
    results: dict[str, dict[str, object]] = {}
    fresh_control_parity: dict[str, object] | None = None
    for multiplier in (1.0, 2.0, 3.0):
        control = run_arm(config, strategy_a, universe, prepared, numeric, actions, lifecycles, multiplier)
        results[f"control_a_{multiplier:g}x"] = compact_result(control)
        if multiplier == 1.0:
            fresh_control_parity = compute_fresh_control_parity(results["control_a_1x"], expected_parity)
            if not fresh_control_parity["passed"]:
                raise SystemExit("RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT")
            (OUT / "PRE_RUN_VALIDATION.json").write_text(
                json.dumps(
                    {"fresh_control_parity": fresh_control_parity, "candidate_run_started": False},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        strategy_b = Equity52WeekHighCloseV1({item.canonical_symbol for item in lifetimes.unresolved})
        candidate = run_arm(config, strategy_b, universe, prepared, None, actions, lifecycles, multiplier)
        results[f"candidate_b_{multiplier:g}x"] = compact_result(candidate)
    if fresh_control_parity is None:
        raise SystemExit("RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT")
    (OUT / "PRE_RUN_VALIDATION.json").write_text(
        json.dumps(
            {"fresh_control_parity": fresh_control_parity, "candidate_run_started": True}, indent=2, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT / "ECONOMIC_RESULTS.json").write_text(
        json.dumps({"control_a_and_candidate_b": results}, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (OUT / "CANDIDATE_B_DECISION_COVERAGE.json").write_text(
        json.dumps(
            {
                key: {
                    "scheduled_decisions": value["counts"]["scheduled_decisions"],
                    "actual_decisions": value["counts"]["scheduled_decisions"],
                    "skipped_decisions": 0,
                    "first_decision": value["candidate_audit"][0]["decision_timestamp"]
                    if value["candidate_audit"]
                    else None,
                    "last_decision": value["candidate_audit"][-1]["decision_timestamp"]
                    if value["candidate_audit"]
                    else None,
                    "decisions_with_fewer_than_six": sum(
                        item["selected_name_count"] < 6 for item in value["candidate_audit"]
                    ),
                    "observation_sizes": sorted({item["observation_set_size"] for item in value["candidate_audit"]}),
                    "scoreable_pool_sizes": sorted({item["scoreable_pool_size"] for item in value["candidate_audit"]}),
                    "target_exposure": sorted(
                        {round(sum(row[1] for row in item["target_weights"]), 12) for item in value["candidate_audit"]}
                    ),
                    "fill_completion": 1.0 - value["periods"]["full"]["unfilled_rejected_fraction"],
                }
                for key, value in results.items()
                if key.startswith("candidate_b_")
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"fresh_control_parity": fresh_control_parity, "result_keys": sorted(results)}, indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
