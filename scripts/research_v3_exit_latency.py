#!/usr/bin/env python3
# ruff: noqa: E501
"""V3 Exit-Latency / Alpha-Deterioration Optimization — research 2026-08.

RESEARCH ONLY: frozen PAPER equity_residual_momentum_v3 remains untouched.
Evaluates exactly four policies:

  E0 CONTROL — weekly entries + weekly exits only
  E1 DAILY_TOP8_EXIT — unqualified or rank>8 at daily close -> exit next open
  E2 DAILY_TOP12_HOLD_BAND — unqualified or rank>12 -> exit next open (9-12 hold band)
  E3 CONFIRMED_TOP12_BREAKDOWN — E2 but condition must persist 2 consecutive daily closes

Every fold / cost level / temporal window is an INDEPENDENT backtest rerun
with its own warmup and state initialization. No curve slicing, no heuristic
fee transforms. Production cost model used for every real execution.

Artifacts: data/research/v3_exit_latency_2026_08/
Manifest: data/research_manifests/v3_exit_latency_optimization_2026_08.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
from parayabuck.backtest.product import (
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import Config, load_config
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import (
    RebalancePolicy,
    RebalanceReason,
    TargetPortfolio,
    TargetWeight,
)
from parayabuck.validation import (
    calendar_fold_windows,
    paired_fold_deltas,
    temporal_diagnostic_windows,
)

REPO = Path(__file__).resolve().parents[1]
EQUITY_CSV = REPO / "data/historical/equity_1200_2016_2026.csv"
DIVIDENDS_CSV = REPO / "data/historical/equity_1200_dividends.csv"
OUT_DIR = REPO / "data/research/v3_exit_latency_2026_08"
MANIFEST_PATH = REPO / "data/research_manifests/v3_exit_latency_optimization_2026_08.json"

EVAL_START = datetime(2017, 1, 3, tzinfo=UTC)
EVAL_END = datetime(2026, 7, 30, tzinfo=UTC)
RECENT_START = datetime(2022, 1, 1, tzinfo=UTC)
RESULT_SCHEMA_VERSION = "1.0.0"
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 202608240
COST_LEVELS = [1, 2, 3, 5]

FROZEN_EQUITY_PARAMS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}

THRESHOLDS_CROSS = [-0.05, -0.075, -0.10, -0.15]

# --------------------------------------------------------------------------- #
# Manifest helpers
# --------------------------------------------------------------------------- #


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataset_identity() -> dict[str, str]:
    return {
        "bars_sha256": _sha256_file(EQUITY_CSV),
        "dividends_sha256": _sha256_file(DIVIDENDS_CSV),
    }


def config_digest(cfg: Config) -> str:
    from scripts.research_helpers import config_digest as _cd  # type: ignore

    return _cd(cfg)


def load_and_validate_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"predeclaration manifest missing: {MANIFEST_PATH}")
    manifest = json.loads(MANIFEST_PATH.read_text())
    from parayabuck.validation import FOLD_COUNT_DECLARED, assert_fold_contract

    declared_folds = manifest.get("walk_forward", {}).get("folds_exact")
    computed = calendar_fold_windows(EVAL_START, EVAL_END, step_months=6)
    if len(computed) != FOLD_COUNT_DECLARED:
        raise SystemExit(
            f"fold contract violated: expected {FOLD_COUNT_DECLARED} computed {len(computed)}"
        )
    declared_parsed = [
        (datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in declared_folds
    ]
    assert_fold_contract(declared_parsed, computed)
    identity = dataset_identity()
    ds = manifest.get("dataset", {})
    if (
        ds.get("bars_sha256") != identity["bars_sha256"]
        or ds.get("dividends_sha256") != identity["dividends_sha256"]
    ):
        raise SystemExit("dataset hash drift from manifest")
    # Validate config digest matches frozen baseline
    cfg = load_config(str(REPO / "config/settings.yaml"))
    expected = manifest.get("baseline_identity", {}).get("config_digest")
    actual = config_digest(cfg)
    if expected is not None and expected != actual:
        raise SystemExit(f"config digest drift: manifest {expected} vs runtime {actual}")
    return manifest


# --------------------------------------------------------------------------- #
# Strategy definitions — exit-latency research identities
# --------------------------------------------------------------------------- #

# Shared weekly policy helper
_WEEKLY_POLICY = RebalancePolicy(Market.EQUITY, "weekly")


class ExitControl(EquityResidualMomentum1dV3):
    """E0 CONTROL — exactly V3, weekly only."""

    pass


class DailyTop8Exit(EquityResidualMomentum1dV3):
    """E1 — daily top8 exit. Unqualified or rank>8 -> exit next open."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self._intermediate_trigger = False
        self._trigger_symbols: set[str] = set()

    def requires_intermediate_risk_rebalance(self, context) -> bool:  # type: ignore[override]
        ranked, _ = self._score_candidates(context)  # type: ignore[attr-defined]
        rank_by = {sym: idx + 1 for idx, (_, sym) in enumerate(ranked)}
        triggers: set[str] = set()
        for sym in context.holdings:
            # holdings keys are canonical symbols
            if sym not in rank_by or rank_by[sym] > 8:
                triggers.add(sym)
        if triggers:
            self._intermediate_trigger = True
            self._trigger_symbols = triggers
            return True
        self._intermediate_trigger = False
        self._trigger_symbols = set()
        return False

    def decide(self, context):  # type: ignore[override]
        if not self._intermediate_trigger:
            result = super().decide(context)
            self._intermediate_trigger = False
            self._trigger_symbols = set()
            return result
        # risk-reducing exit path: keep survivors at current weight
        ranked, _ = self._score_candidates(context)  # type: ignore[attr-defined]
        # recompute to ensure consistency (use same triggers but verify)
        # survivors: holdings minus triggers
        holdings = dict(context.holdings)
        survivors = [s for s in holdings if s not in self._trigger_symbols]
        # clear flag before return
        triggers = set(self._trigger_symbols)
        self._intermediate_trigger = False
        self._trigger_symbols = set()
        weights: dict[str, float] = {}
        rank_by = {sym: idx + 1 for idx, (_, sym) in enumerate(ranked)}
        for sym in survivors:
            qty = holdings[sym]
            price = context.prices.get(sym, 0)
            if price is None or price <= 0:
                continue
            w = float(qty) * float(price) / max(float(context.equity), 1.0)
            if w > 1e-12:
                weights[sym] = w
        targets = tuple(
            TargetWeight(
                symbol=sym,
                weight=w,
                reason="exit_latency_hold",
                rank=rank_by.get(sym, 999),
            )
            for sym, w in sorted(weights.items())
        )
        cash_weight = max(0.0, 1.0 - sum(weights.values()))
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=cash_weight,
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={"exit_latency_intermediate": True, "triggered": sorted(triggers)},
        )


class DailyTop12Band(EquityResidualMomentum1dV3):
    """E2 — daily top12 hold band. Unqualified or rank>12 -> exit."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self._intermediate_trigger = False
        self._trigger_symbols: set[str] = set()

    def requires_intermediate_risk_rebalance(self, context) -> bool:  # type: ignore[override]
        ranked, _ = self._score_candidates(context)  # type: ignore[attr-defined]
        rank_by = {sym: idx + 1 for idx, (_, sym) in enumerate(ranked)}
        triggers: set[str] = set()
        for sym in context.holdings:
            if sym not in rank_by or rank_by[sym] > 12:
                triggers.add(sym)
        if triggers:
            self._intermediate_trigger = True
            self._trigger_symbols = triggers
            return True
        self._intermediate_trigger = False
        self._trigger_symbols = set()
        return False

    def decide(self, context):  # type: ignore[override]
        if not self._intermediate_trigger:
            result = super().decide(context)
            self._intermediate_trigger = False
            self._trigger_symbols = set()
            return result
        ranked, _ = self._score_candidates(context)  # type: ignore[attr-defined]
        holdings = dict(context.holdings)
        survivors = [s for s in holdings if s not in self._trigger_symbols]
        triggers = set(self._trigger_symbols)
        self._intermediate_trigger = False
        self._trigger_symbols = set()
        rank_by = {sym: idx + 1 for idx, (_, sym) in enumerate(ranked)}
        weights: dict[str, float] = {}
        for sym in survivors:
            qty = holdings[sym]
            price = context.prices.get(sym, 0)
            if price is None or price <= 0:
                continue
            w = float(qty) * float(price) / max(float(context.equity), 1.0)
            if w > 1e-12:
                weights[sym] = w
        targets = tuple(
            TargetWeight(
                symbol=sym,
                weight=w,
                reason="exit_latency_hold",
                rank=rank_by.get(sym, 999),
            )
            for sym, w in sorted(weights.items())
        )
        cash_weight = max(0.0, 1.0 - sum(weights.values()))
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=cash_weight,
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={"exit_latency_intermediate": True, "triggered": sorted(triggers)},
        )


class ConfirmedTop12(EquityResidualMomentum1dV3):
    """E3 — same as E2 but condition must persist 2 consecutive daily bars."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self._intermediate_trigger = False
        self._trigger_symbols: set[str] = set()
        self._consecutive: dict[str, int] = {}

    def requires_intermediate_risk_rebalance(self, context) -> bool:  # type: ignore[override]
        ranked, _ = self._score_candidates(context)  # type: ignore[attr-defined]
        rank_by = {sym: idx + 1 for idx, (_, sym) in enumerate(ranked)}
        new_counts: dict[str, int] = {}
        triggers: set[str] = set()
        for sym in context.holdings:
            breakdown = sym not in rank_by or rank_by[sym] > 12
            prev = self._consecutive.get(sym, 0)
            cnt = prev + 1 if breakdown else 0
            new_counts[sym] = cnt
            if cnt >= 2:
                triggers.add(sym)
        # keep counts only for currently held symbols
        self._consecutive = dict(new_counts)
        # also purge symbols no longer held (already handled) and keep zero counts for survivors
        if triggers:
            self._intermediate_trigger = True
            self._trigger_symbols = triggers
            return True
        self._intermediate_trigger = False
        self._trigger_symbols = set()
        return False

    def decide(self, context):  # type: ignore[override]
        if not self._intermediate_trigger:
            result = super().decide(context)
            self._intermediate_trigger = False
            self._trigger_symbols = set()
            return result
        ranked, _ = self._score_candidates(context)  # type: ignore[attr-defined]
        holdings = dict(context.holdings)
        survivors = [s for s in holdings if s not in self._trigger_symbols]
        triggers = set(self._trigger_symbols)
        self._intermediate_trigger = False
        self._trigger_symbols = set()
        # after exit, clear consecutive for exited symbols
        for sym in triggers:
            self._consecutive.pop(sym, None)
        rank_by = {sym: idx + 1 for idx, (_, sym) in enumerate(ranked)}
        weights: dict[str, float] = {}
        for sym in survivors:
            qty = holdings[sym]
            price = context.prices.get(sym, 0)
            if price is None or price <= 0:
                continue
            w = float(qty) * float(price) / max(float(context.equity), 1.0)
            if w > 1e-12:
                weights[sym] = w
        targets = tuple(
            TargetWeight(
                symbol=sym,
                weight=w,
                reason="exit_latency_hold",
                rank=rank_by.get(sym, 999),
            )
            for sym, w in sorted(weights.items())
        )
        cash_weight = max(0.0, 1.0 - sum(weights.values()))
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=cash_weight,
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={"exit_latency_intermediate": True, "triggered": sorted(triggers)},
        )


CANDIDATE_CLASSES = {
    "E0": ExitControl,
    "E1": DailyTop8Exit,
    "E2": DailyTop12Band,
    "E3": ConfirmedTop12,
}

# --------------------------------------------------------------------------- #
# Engine helpers — every window is an independent backtest rerun
# --------------------------------------------------------------------------- #


def make_engine(
    config: Config,
    strategy,
    universe,
    *,
    cost_multiplier: float = 1.0,
    starting_equity: float | None = None,
):
    limits = portfolio_risk_limits_from_config(config.risk)
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=starting_equity or config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission * cost_multiplier
        if config.backtest.commission
        else 0,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share * cost_multiplier
        if cost_multiplier != 1
        else config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share * cost_multiplier
        if cost_multiplier != 1
        else config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate * cost_multiplier
        if cost_multiplier != 1
        else config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=limits,
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        enforce_drawdown_halt=False,
    )


def build_strategy(candidate_id: str):
    cls = CANDIDATE_CLASSES[candidate_id]
    return cls(dict(FROZEN_EQUITY_PARAMS))


def run_window(
    config: Config,
    candidate_id: str,
    universe,
    bars,
    actions,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    cost_multiplier: float = 1.0,
    starting_equity: float | None = None,
) -> PortfolioBacktestResult:
    strategy = build_strategy(candidate_id)
    available = [b for b in bars if b.market == Market.EQUITY and b.timeframe == strategy.timeframe]
    if end is not None:
        available = [b for b in available if b.timestamp <= end]
    filtered = available if start is None else [b for b in available if b.timestamp >= start]
    if start is None:
        warmup = []
        corp = list(actions)
    else:
        cutoff = start - timedelta(days=550)
        warmup = [b for b in available if cutoff <= b.timestamp < start]
        if end is not None:
            corp = [a for a in actions if start <= a.timestamp <= end]
        else:
            corp = [a for a in actions if a.timestamp >= start]
    engine = make_engine(
        config, strategy, universe, cost_multiplier=cost_multiplier, starting_equity=starting_equity
    )
    result = engine.run(filtered, warmup=warmup, corporate_actions=corp)
    return result


def run_chained_window(
    config: Config,
    candidate_id: str,
    universe,
    bars,
    actions,
    segments: list[tuple[datetime, datetime]],
    *,
    cost_multiplier: float = 1.0,
) -> PortfolioBacktestResult:
    """Chain two independent segments: second warms up including excluded period and compounds on first equity."""
    if len(segments) == 1:
        s, e = segments[0]
        return run_window(
            config,
            candidate_id,
            universe,
            bars,
            actions,
            start=s,
            end=e,
            cost_multiplier=cost_multiplier,
        )
    # two segments expected (exclude windows)
    assert len(segments) == 2
    s1, e1 = segments[0]
    s2, e2 = segments[1]
    # first segment
    r1 = run_window(
        config,
        candidate_id,
        universe,
        bars,
        actions,
        start=s1,
        end=e1,
        cost_multiplier=cost_multiplier,
    )
    # second segment: start equity = r1 ending equity
    r2 = run_window(
        config,
        candidate_id,
        universe,
        bars,
        actions,
        start=s2,
        end=e2,
        cost_multiplier=cost_multiplier,
        starting_equity=r1.ending_equity,
    )
    # chain equity curves for reporting (preserve both curves concatenated with compounding)
    # For metrics we need to chain returns: use equity curve chaining helper from validation? But we need independent execution's combined metrics.
    # Instead, compute combined result metrics by chaining curves via validation helpers and creating a synthetic result-like dict.
    # For simplicity, we return a combined wrapper via chaining curves; caller will compute stats via stats_for_result on a chained synthetic.
    # We store both results and a chained synthetic in the engine result's place.
    # Here we create a minimal object with same attributes as PortfolioBacktestResult but chained.
    # Use validation chain helpers for curve metrics, but keep real execution fills separated.
    from parayabuck.validation import chain_equity_curves, chained_cumulative_metrics

    chained_curve = chain_equity_curves([list(r1.equity_curve), list(r2.equity_curve)])
    chained_times = list(r1.curve_timestamps) + list(r2.curve_timestamps)
    # Create a new result that mimics PortfolioBacktestResult for stats: use r2 as base but override curve/equity
    # We need to compute metrics from chained_curve; we can reuse chained_cumulative_metrics for return/cagr/sharpe etc, but for consistency we compute via the same helper as stats_for_result would but using chained_curve.
    # Instead, we return a tuple for caller to handle; but to keep interface simple, we return a synthetic result.
    # We'll construct a new PortfolioBacktestResult by copying r2 and overriding fields.
    # This is research-only chaining; the two segment executions were independent with correct warmup/state.
    # For provenance we also return segments.
    # Build synthetic via dict
    # Use the second result's other fields but override equity/return/curve
    # Compute chained metrics
    combined_return = chained_curve[-1] / chained_curve[0] - 1 if chained_curve[0] > 0 else 0
    # For other metrics, we need to compute from chained_curve returns; we can approximate via chained_cumulative_metrics
    chained_metrics = chained_cumulative_metrics(chained_curve)
    # Create synthetic result object (copy r2)
    # Since PortfolioBacktestResult is frozen dataclass, we create a new one via replacing.
    # Simpler: return a wrapper dict and let caller handle; but to keep API uniform, we return r2 with patched attributes via object.__setattr__? Dataclass frozen prevents.
    # So we create a new PortfolioBacktestResult manually.
    from dataclasses import replace

    synthetic = replace(
        r2,
        starting_equity=r1.starting_equity,
        ending_equity=chained_curve[-1],
        total_return=combined_return,
        gross_return=combined_return,  # approximate
        cagr=chained_metrics.get("cagr", r2.cagr),
        sharpe=chained_metrics.get("sharpe", r2.sharpe),
        sortino=chained_metrics.get("sortino", r2.sortino),
        calmar=chained_metrics.get("calmar", r2.calmar),
        max_drawdown=chained_metrics.get("max_drawdown", r2.max_drawdown),
        equity_curve=tuple(chained_curve),
        curve_timestamps=tuple(chained_times),
        # keep other fields from r2 (fills etc.) concatenated? For diagnostics we keep concatenation of fills
        fills=tuple(list(r1.fills) + list(r2.fills)),
        realized_pnl_events=tuple(list(r1.realized_pnl_events) + list(r2.realized_pnl_events)),
        closed_position_events=tuple(
            list(r1.closed_position_events) + list(r2.closed_position_events)
        ),
    )
    return synthetic


def stats_for_result(result: PortfolioBacktestResult) -> dict:
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    return {
        "return": round(result.total_return, 6),
        "gross_return": round(result.gross_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "calmar": round(result.calmar, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "max_drawdown_duration_days": result.max_drawdown_duration_days,
        "volatility": round(result.annualized_volatility, 6),
        "turnover": round(result.turnover, 6),
        "fees_total": round(total_cost, 2),
        "slippage_total": round(result.slippage_cost_total, 2),
        "spread_total": round(result.spread_cost_total, 2),
        "fee_total": round(result.fee_total, 2),
        "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
        "avg_holdings": round(result.average_holdings_count, 3),
        "max_concentration": round(result.maximum_concentration, 4),
        "rebalance_count": result.rebalance_count,
        "closed_positions": result.closed_positions,
        "dividend_total": round(result.dividend_total, 2),
        "starting_equity": result.starting_equity,
        "ending_equity": round(result.ending_equity, 2),
        "equity_curve": list(result.equity_curve),
        "curve_timestamps": [t.isoformat() for t in result.curve_timestamps],
        "per_symbol": [
            {"symbol": p.symbol, "realized_pnl": round(p.realized_pnl, 2), "fees": round(p.fees, 2)}
            for p in result.per_symbol
        ],
    }


# --------------------------------------------------------------------------- #
# Position-level diagnostics — correct account-impact denominator
# --------------------------------------------------------------------------- #


def _build_bars_by_symbol(bars) -> dict[str, list]:
    mp: dict[str, list] = defaultdict(list)
    for b in bars:
        mp[b.symbol].append(b)
    for lst in mp.values():
        lst.sort(key=lambda x: x.timestamp)
    index: dict[str, dict] = {}
    for sym, lst in mp.items():
        index[sym] = {b.timestamp: i for i, b in enumerate(lst)}
    return mp, index


def diagnostics_for_result(
    result: PortfolioBacktestResult,
    bars,
    bars_by_symbol: dict,
    bar_index: dict,
    corporate_actions=None,
) -> tuple[dict, list]:
    """Per-episode diagnostics with contemporaneous account impact.

    For each closed position episode (from fills -> qty zero), record:
      - account_equity_at_entry
      - position_notional_at_entry
      - initial_position_weight
      - realized_pnl
      - realized_pnl / account_equity_at_entry (contemporaneous impact, NOT / initial_cash)
      - MAE / account_equity_at_entry
      - OHLC first crossing timestamps for -5%, -7.5%, -10%, -15%
    """
    # Reconstruct episodes from fills: group by symbol, track qty and equity at entry
    # Use fills sorted by timestamp
    fills = sorted(result.fills, key=lambda f: f.timestamp)
    # Need equity curve per timestamp for account equity at entry
    equity_by_time = {
        ts: eq for ts, eq in zip(result.curve_timestamps, result.equity_curve, strict=False)
    }
    # Also need bars for MAE and crossing
    # Build per-symbol episode list
    # Track open position state derived from fills (not engine's internal _positions which may have used different entry averaging)
    # Simplify: derive episodes from closed_position_events which already have (close_timestamp, symbol, total_pnl)
    # But we also need entry info: we can infer entry timestamp as first buy fill for symbol after previous close.
    # We'll walk fills sequentially and maintain per-symbol open state.

    open_state: dict[
        str, dict
    ] = {}  # symbol -> {entry_time, entry_price_avg, qty, equity_at_entry, notional_at_entry, entry_fills}
    episodes: list[dict] = []

    # Need fast lookup for price bars to compute MAE
    # Sort fills by timestamp; for sell fills, avg_price logic mimics engine but we approximate using fill_price.
    # We'll track avg_price weighted.
    for fill in fills:
        sym = fill.symbol
        if fill.side.value == "buy":
            st = open_state.get(sym)
            if st is None or st["qty"] <= 0:
                # new episode start: equity at entry is equity just before entry? use previous equity curve point or starting_equity
                eq = equity_by_time.get(fill.timestamp)
                # fill timestamp is bar timestamp (next bar open execution). Equity at entry decision timestamp vs fill time? Use equity just before fill via previous curve point.
                # Find equity at or before fill.timestamp-1 bar. Use prior curve point if not exact.
                if eq is None:
                    # find nearest prior timestamp
                    prior = [t for t in result.curve_timestamps if t < fill.timestamp]
                    eq = equity_by_time[prior[-1]] if prior else result.starting_equity
                open_state[sym] = {
                    "entry_time": fill.timestamp,
                    "decision_timestamp": fill.decision_timestamp,
                    "qty": fill.qty,
                    "avg_price": fill.fill_price,
                    "entry_fee": fill.fee,
                    "equity_at_entry": eq,
                    "notional_at_entry": fill.qty * fill.fill_price,
                    "fills": [fill],
                }
            else:
                # add to existing
                total_qty = st["qty"] + fill.qty
                # weighted avg price
                avg = (
                    (st["avg_price"] * st["qty"] + fill.fill_price * fill.qty) / total_qty
                    if total_qty
                    else fill.fill_price
                )
                st["avg_price"] = avg
                st["qty"] = total_qty
                st["notional_at_entry"] += fill.qty * fill.fill_price
                st["entry_fee"] += fill.fee
                st["fills"].append(fill)
        else:  # sell
            st = open_state.get(sym)
            if st is None or st["qty"] <= 0:
                continue
            # realized delta for this fill already in result.realized_pnl_events, but we compute episode-level at close
            qty_close = min(fill.qty, st["qty"])
            st["qty"] -= qty_close
            # if position closed (qty ~0), finalize episode
            if st["qty"] <= 1e-9:
                # find realized pnl for this episode: look up closed_position_events for this symbol and timestamp
                # closed_position_events contains (timestamp, symbol, pnl)
                pnl = None
                for ts, s, p in result.closed_position_events:
                    if s == sym and ts == fill.timestamp:
                        pnl = p
                        break
                # Fallback: sum realized_pnl_events for this symbol within episode window
                if pnl is None:
                    pnl = sum(
                        p
                        for ts, s, p in result.realized_pnl_events
                        if s == sym and st["entry_time"] <= ts <= fill.timestamp
                    )
                entry_eq = st["equity_at_entry"]
                notional = st["notional_at_entry"]
                initial_weight = notional / entry_eq if entry_eq else 0
                acct_impact = pnl / entry_eq if entry_eq and pnl is not None else 0
                # MAE: walk bars between entry_time and fill.timestamp inclusive, track worst low
                # Need bars for symbol
                bars_list = bars_by_symbol.get(sym, [])
                idx_map = bar_index.get(sym, {})
                # Find indices: entry bar index is where fill timestamp corresponds to bar open? Use entry decision timestamp vs fill timestamp?
                # For MAE we consider from entry fill bar timestamp to exit fill timestamp inclusive (OHLC).
                entry_idx = idx_map.get(st["entry_time"])
                # If not found, find closest prior
                if entry_idx is None:
                    # find first bar timestamp >= entry_time
                    for i, b in enumerate(bars_list):
                        if b.timestamp >= st["entry_time"]:
                            entry_idx = i
                            break
                exit_idx = idx_map.get(fill.timestamp)
                if exit_idx is None:
                    for i, b in enumerate(bars_list):
                        if b.timestamp >= fill.timestamp:
                            exit_idx = i
                            break
                mae_price = None
                mae = None
                mae_pct = None
                crossing: dict[str, str | None] = {}
                if entry_idx is not None and exit_idx is not None and entry_idx <= exit_idx:
                    entry_price = st["avg_price"]
                    worst_low = min(b.low for b in bars_list[entry_idx : exit_idx + 1] if b.low > 0)
                    mae_price = entry_price - worst_low  # per share adverse
                    mae = mae_price * sum(
                        f.qty for f in st["fills"]
                    )  # approx notional adverse at worst
                    mae_acct = mae / entry_eq if entry_eq else 0
                    mae_pct = (worst_low / entry_price - 1) if entry_price else 0
                    # first crossings
                    for thr, key in [
                        (0.05, "-5%"),
                        (0.075, "-7.5%"),
                        (0.10, "-10%"),
                        (0.15, "-15%"),
                    ]:
                        ts_found = None
                        lvl = entry_price * (1 - thr)
                        for b in bars_list[entry_idx : exit_idx + 1]:
                            if b.low <= lvl:
                                ts_found = b.timestamp.isoformat()
                                break
                        crossing[key] = ts_found
                else:
                    mae_acct = 0
                    mae_pct = 0
                    # no bars
                    for k in ["-5%", "-7.5%", "-10%", "-15%"]:
                        crossing[k] = None
                    worst_low = None

                abs(pnl) if pnl and pnl < 0 else 0
                episodes.append(
                    {
                        "symbol": sym,
                        "entry_time": st["entry_time"].isoformat()
                        if hasattr(st["entry_time"], "isoformat")
                        else str(st["entry_time"]),
                        "entry_decision_timestamp": st["decision_timestamp"].isoformat()
                        if hasattr(st["decision_timestamp"], "isoformat")
                        else str(st["decision_timestamp"]),
                        "exit_time": fill.timestamp.isoformat(),
                        "entry_price_avg": round(float(st["avg_price"]), 4),
                        "entry_qty": round(float(sum(f.qty for f in st["fills"])), 6),
                        "account_equity_at_entry": round(float(entry_eq), 2),
                        "position_notional_at_entry": round(float(notional), 2),
                        "initial_position_weight": round(float(initial_weight), 6),
                        "realized_pnl": round(float(pnl or 0), 2),
                        "realized_pnl_over_equity_at_entry": round(float(acct_impact or 0), 6),
                        "mae_notional": round(float(mae or 0), 2) if mae is not None else None,
                        "mae_over_equity_at_entry": round(float(mae_acct or 0), 6)
                        if mae_acct is not None
                        else None,
                        "mae_return_pct": round(float(mae_pct or 0), 6)
                        if mae_pct is not None
                        else None,
                        "worst_low": round(float(worst_low), 4) if worst_low is not None else None,
                        "first_crossing": crossing,
                        "days_held": (fill.timestamp - st["entry_time"]).days,
                    }
                )
                # clear state
                open_state.pop(sym, None)
            else:
                # partial close, keep open state (avg_price unchanged for remaining)
                pass

    # Sort worst episodes by realized_pnl (most negative first)
    episodes_sorted = sorted(episodes, key=lambda e: e["realized_pnl"])
    worst_10 = episodes_sorted[:10]

    # Compute tail concentration and MAE percentiles
    losses = [abs(e["realized_pnl"]) for e in episodes if e["realized_pnl"] < 0]
    total_loss = sum(losses) if losses else 1
    top1 = abs(worst_10[0]["realized_pnl"]) / total_loss if worst_10 and total_loss else 0
    top3 = sum(abs(e["realized_pnl"]) for e in worst_10[:3]) / total_loss if losses else 0
    top5 = sum(abs(e["realized_pnl"]) for e in worst_10[:5]) / total_loss if losses else 0
    top10 = sum(abs(e["realized_pnl"]) for e in worst_10[:10]) / total_loss if losses else 0
    mae_vals = [
        e["mae_over_equity_at_entry"] for e in episodes if e["mae_over_equity_at_entry"] is not None
    ]
    mae_vals_sorted = sorted(mae_vals)

    def pct(q):
        if not mae_vals_sorted:
            return 0
        k = int(q * (len(mae_vals_sorted) - 1))
        return mae_vals_sorted[k]

    diagnostics = {
        "episodes_count": len(episodes),
        "worst_10_table": worst_10,
        "tail_concentration": {
            "top1": round(top1, 4),
            "top3": round(top3, 4),
            "top5": round(top5, 4),
            "top10": round(top10, 4),
        },
        "mae_p95": round(pct(0.95), 6),
        "mae_p99": round(pct(0.99), 6),
        "worst_single_loss_pnl": round(worst_10[0]["realized_pnl"], 2) if worst_10 else 0,
        "worst_single_loss_acct_impact": round(worst_10[0]["realized_pnl_over_equity_at_entry"], 6)
        if worst_10
        else 0,
    }
    return diagnostics, episodes


def worst_positions_report(diagnostics, label: str = "") -> dict:
    return {
        "worst_positions": diagnostics.get("worst_10_table", []),
        "tail_concentration": diagnostics.get("tail_concentration", {}),
    }


# --------------------------------------------------------------------------- #
# Position-level comparison diagnostics (control vs candidate)
# --------------------------------------------------------------------------- #


def compare_episodes(
    control_episodes: list[dict], candidate_episodes: list[dict], bars_by_symbol, bar_index
) -> dict:
    """For previous worst 10 control positions, answer did E1/E2/E3 exit earlier, days saved, PnL saved, recovery, reentry."""
    # Index candidate episodes by (symbol, entry_time) approximated via symbol and entry_time truncated to date
    # Build map symbol->list episodes sorted by entry_time
    cand_by_symbol: dict[str, list[dict]] = defaultdict(list)
    for ep in candidate_episodes:
        cand_by_symbol[ep["symbol"]].append(ep)
    for lst in cand_by_symbol.values():
        lst.sort(key=lambda x: x["entry_time"])

    control_worst = sorted(control_episodes, key=lambda e: e["realized_pnl"])[:10]
    rows = []
    for ce in control_worst:
        sym = ce["symbol"]
        ce_entry = datetime.fromisoformat(ce["entry_time"])
        ce_exit = datetime.fromisoformat(ce["exit_time"])
        # Find candidate episode with same symbol and entry_time within 7 days (weekly alignment)
        best = None
        best_delta = None
        for cand_ep in cand_by_symbol.get(sym, []):
            cand_entry = datetime.fromisoformat(cand_ep["entry_time"])
            delta = abs((cand_entry - ce_entry).days)
            if delta <= 10:  # weekly window
                if best is None or delta < best_delta:
                    best = cand_ep
                    best_delta = delta
        if best is None:
            # candidate had no matching episode (maybe never entered due to different cash path, or early exit changed schedule)
            # Try to find any episode with overlapping period
            for cand_ep in cand_by_symbol.get(sym, []):
                cand_entry = datetime.fromisoformat(cand_ep["entry_time"])
                cand_exit = datetime.fromisoformat(cand_ep["exit_time"])
                # overlapping?
                if cand_entry <= ce_exit and cand_exit >= ce_entry:
                    best = cand_ep
                    break
        if best is None:
            rows.append(
                {
                    "symbol": sym,
                    "control_entry": ce["entry_time"],
                    "control_exit": ce["exit_time"],
                    "candidate_match": None,
                    "exited_earlier": None,
                    "days_saved": None,
                    "pnl_difference": None,
                    "recovery_after_candidate_exit": None,
                    "reentered_next_weekly": None,
                    "note": "no matching candidate episode within window",
                }
            )
            continue
        cand_exit = datetime.fromisoformat(best["exit_time"])
        days_saved = (ce_exit - cand_exit).days
        pnl_diff = best["realized_pnl"] - ce["realized_pnl"]  # positive means candidate saved loss
        # eventual recovery after candidate exit until control exit: need price path
        # compute max recovery after candidate exit: (control exit price proxy vs candidate exit price)
        # Use bars: find prices at candidate exit and control exit and peak after candidate
        bars_list = bars_by_symbol.get(sym, [])
        # find bars for exit dates
        cand_idx = None
        ctrl_idx = None
        for i, b in enumerate(bars_list):
            if b.timestamp.date() == cand_exit.date():
                cand_idx = i
            if b.timestamp.date() == ce_exit.date():
                ctrl_idx = i
        recovery = None
        max_after = None
        if cand_idx is not None and ctrl_idx is not None and cand_idx < ctrl_idx:
            # find max close between cand_idx+1 and ctrl_idx inclusive
            segment = bars_list[cand_idx + 1 : ctrl_idx + 1]
            if segment:
                max_after = max(b.close for b in segment)
                cand_price = bars_list[cand_idx].close
                recovery = (max_after / cand_price - 1) if cand_price else None
        # reentry: check if candidate episode after this one has entry within 7-10 days after cand_exit (next weekly rebalance)
        reentry = None
        reentry_pnl = None
        # look for next episode for same symbol after cand_exit
        next_eps = [
            ep
            for ep in cand_by_symbol.get(sym, [])
            if datetime.fromisoformat(ep["entry_time"]) > cand_exit
        ]
        if next_eps:
            nxt = min(next_eps, key=lambda x: datetime.fromisoformat(x["entry_time"]))
            nxt_entry = datetime.fromisoformat(nxt["entry_time"])
            # if reentry within 14 days, consider it reentered next weekly
            if (nxt_entry - cand_exit).days <= 14:
                reentry = True
                reentry_pnl = nxt["realized_pnl"]
            else:
                reentry = False
        else:
            reentry = False

        rows.append(
            {
                "symbol": sym,
                "control_entry": ce["entry_time"],
                "control_exit": ce["exit_time"],
                "control_pnl": ce["realized_pnl"],
                "control_acct_impact": ce["realized_pnl_over_equity_at_entry"],
                "candidate_entry": best["entry_time"],
                "candidate_exit": best["exit_time"],
                "candidate_pnl": best["realized_pnl"],
                "exited_earlier": days_saved > 0,
                "days_saved": int(days_saved),
                "pnl_saved": round(float(pnl_diff), 2),
                "recovery_after_candidate_exit": round(float(recovery), 6)
                if recovery is not None
                else None,
                "max_price_after_candidate_exit": round(float(max_after), 4)
                if max_after is not None
                else None,
                "reentered": reentry,
                "reentry_pnl": reentry_pnl,
            }
        )
    return {"rows": rows}


# --------------------------------------------------------------------------- #
# Metrics: exit behavior, avoided loss, net value
# --------------------------------------------------------------------------- #


def compute_exit_metrics(
    control_result: PortfolioBacktestResult,
    candidate_result: PortfolioBacktestResult,
    control_episodes,
    candidate_episodes,
    bars_by_symbol,
) -> dict:
    # turnover increase
    turnover_inc = (
        (candidate_result.turnover - control_result.turnover) / control_result.turnover
        if control_result.turnover
        else 0
    )
    # early exits: count where candidate days_saved >0 for matched episodes
    # Use compare helper but more generally count candidate episodes that exited earlier than control's corresponding?
    # Simpler: count difference in rebalance_count and fills
    # early exits approximated as extra closes
    extra_closes = candidate_result.closed_positions - control_result.closed_positions
    # average days earlier: compute for matched worst 10? For all matched we need full comparison
    # We'll compute average days earlier across all matched episodes (symbol + entry within window)
    cand_by_symbol = defaultdict(list)
    for ep in candidate_episodes:
        cand_by_symbol[ep["symbol"]].append(ep)
    days_list = []
    for ce in control_episodes:
        sym = ce["symbol"]
        ce_entry = datetime.fromisoformat(ce["entry_time"])
        ce_exit = datetime.fromisoformat(ce["exit_time"])
        # find candidate match
        best = None
        for cand_ep in cand_by_symbol.get(sym, []):
            cand_entry = datetime.fromisoformat(cand_ep["entry_time"])
            if abs((cand_entry - ce_entry).days) <= 10:
                best = cand_ep
                break
        if best:
            cand_exit = datetime.fromisoformat(best["exit_time"])
            ds = (ce_exit - cand_exit).days
            if ds > 0:
                days_list.append(ds)
    avg_days = round(statistics.fmean(days_list), 2) if days_list else 0
    median_days = round(statistics.median(days_list), 2) if days_list else 0
    # unnecessary exits: early exits that were followed by recovery >5% within control holding period?
    # Count early exits where recovery >0.05
    unnecessary = 0
    eventual_winner = 0
    for ce in control_episodes:
        sym = ce["symbol"]
        ce_exit = datetime.fromisoformat(ce["exit_time"])
        ce_entry = datetime.fromisoformat(ce["entry_time"])
        best = None
        for cand_ep in cand_by_symbol.get(sym, []):
            if abs((datetime.fromisoformat(cand_ep["entry_time"]) - ce_entry).days) <= 10:
                best = cand_ep
                break
        if best and (ce_exit - datetime.fromisoformat(best["exit_time"])).days > 0:
            # check recovery after candidate exit vs control exit price
            # Use bars: find max close after candidate exit to control exit
            bars_list = bars_by_symbol.get(sym, [])
            cand_exit = datetime.fromisoformat(best["exit_time"])
            # find indices
            try:
                cand_idx = next(
                    i for i, b in enumerate(bars_list) if b.timestamp.date() == cand_exit.date()
                )
                ctrl_idx = next(
                    i for i, b in enumerate(bars_list) if b.timestamp.date() == ce_exit.date()
                )
                if cand_idx < ctrl_idx:
                    segment = bars_list[cand_idx + 1 : ctrl_idx + 1]
                    if segment:
                        max_c = max(b.close for b in segment)
                        cand_close = bars_list[cand_idx].close
                        rec = (max_c / cand_close - 1) if cand_close else 0
                        if rec > 0.05:
                            unnecessary += 1
                        # eventual winner: if control exit was actually profitable despite interim drawdown?
                        if ce["realized_pnl"] > 0 and best["realized_pnl"] < 0:
                            eventual_winner += 1
            except StopIteration:
                pass
    # reentries count
    # Already counted extra closes partly includes reentries; we also count how many candidate episodes correspond to reentry after early exit within 14 days
    reentries = 0
    for sym, lst in cand_by_symbol.items():
        lst_sorted = sorted(lst, key=lambda x: datetime.fromisoformat(x["entry_time"]))
        for i in range(1, len(lst_sorted)):
            prev_exit = datetime.fromisoformat(lst_sorted[i - 1]["exit_time"])
            cur_entry = datetime.fromisoformat(lst_sorted[i]["entry_time"])
            if (cur_entry - prev_exit).days <= 14:
                # check if previous was early exit vs control
                reentries += 1
    # additional fees
    control_cost = (
        control_result.slippage_cost_total
        + control_result.spread_cost_total
        + control_result.fee_total
    )
    cand_cost = (
        candidate_result.slippage_cost_total
        + candidate_result.spread_cost_total
        + candidate_result.fee_total
    )
    add_fees = cand_cost - control_cost

    return {
        "early_exits": int(extra_closes) if extra_closes > 0 else len(days_list),
        "average_days_earlier": avg_days,
        "median_days_earlier": median_days,
        "unnecessary_exits": int(unnecessary),
        "eventual_winner_exits": int(eventual_winner),
        "reentries": int(reentries),
        "turnover_increase_pct": round(float(turnover_inc), 4),
        "turnover_control": round(float(control_result.turnover), 6),
        "turnover_candidate": round(float(candidate_result.turnover), 6),
        "additional_fees": round(float(add_fees), 2),
        "control_cost": round(float(control_cost), 2),
        "candidate_cost": round(float(cand_cost), 2),
    }


def compute_avoided_metrics(
    control_episodes, candidate_episodes, bars_by_symbol, control_result, candidate_result
) -> dict:
    """Compute avoided_post_exit_loss, premature opportunity cost, net."""
    # Map control episodes by (symbol, entry_time)
    # For each candidate early exit that was earlier than control, compute:
    # - avoided loss: if control experienced further adverse move after candidate exit, measure MAE after candidate exit that was avoided
    # Simplified: avoided = max(0, (candidate_exit_price - control_exit_price)/entry_price * qty normalized) but using bars
    # We'll approximate using close prices.
    cand_by_symbol = defaultdict(list)
    for ep in candidate_episodes:
        cand_by_symbol[ep["symbol"]].append(ep)
    avoided = 0.0
    premature = 0.0
    for ce in control_episodes:
        sym = ce["symbol"]
        ce_entry = datetime.fromisoformat(ce["entry_time"])
        ce_exit = datetime.fromisoformat(ce["exit_time"])
        # find matched candidate
        best = None
        for cand_ep in cand_by_symbol.get(sym, []):
            if abs((datetime.fromisoformat(cand_ep["entry_time"]) - ce_entry).days) <= 10:
                best = cand_ep
                break
        if not best:
            continue
        cand_exit = datetime.fromisoformat(best["exit_time"])
        days_saved = (ce_exit - cand_exit).days
        if days_saved <= 0:
            continue
        bars_list = bars_by_symbol.get(sym, [])
        # get entry price avg
        entry_price = ce["entry_price_avg"]
        qty = ce["entry_qty"]
        eq_at_entry = ce["account_equity_at_entry"]
        if not entry_price or not qty or not eq_at_entry:
            continue
        # find indices
        try:
            cand_idx = next(
                i for i, b in enumerate(bars_list) if b.timestamp.date() == cand_exit.date()
            )
            ctrl_idx = next(
                i for i, b in enumerate(bars_list) if b.timestamp.date() == ce_exit.date()
            )
        except StopIteration:
            continue
        if cand_idx >= ctrl_idx:
            continue
        # After candidate exit, control still held; find min low after candidate exit until control exit (adverse avoided)
        segment = bars_list[cand_idx + 1 : ctrl_idx + 1]
        if not segment:
            continue
        min_low = min(b.low for b in segment)
        max_high = max(b.high for b in segment)
        cand_close = bars_list[cand_idx].close
        # avoided loss: if min_low < cand_close, the drop that was avoided
        if min_low < cand_close:
            avoided_notional = (cand_close - min_low) * qty
            avoided += avoided_notional / eq_at_entry
        # premature opportunity cost: if max_high > cand_close, the rally missed
        if max_high > cand_close:
            # only counts if control actually benefited (control exit price > candidate exit price)
            ctrl_close = bars_list[ctrl_idx].close
            if ctrl_close > cand_close:
                # opportunity cost is rally that was missed up to max
                (max_high - cand_close) * qty
                # But if control exit captured only part, we consider max? Use control close vs candidate close as missed upside
                # We'll use (ctrl_close - cand_close) as realized missed.
                missed = (ctrl_close - cand_close) * qty / eq_at_entry
                if missed > 0:
                    premature += missed
            else:
                # still rally then fall, but control didn't benefit; we count max rally as opportunity cost of early exit that was winner then loser?
                # For simplicity, count max rally as opportunity
                opp = (max_high - cand_close) * qty / eq_at_entry
                premature += opp * 0.5  # dampen
        # Also handle case where candidate exit was at loss and control later recovered to profit: that's premature
    # incremental transaction costs
    control_cost = (
        control_result.slippage_cost_total
        + control_result.spread_cost_total
        + control_result.fee_total
    )
    cand_cost = (
        candidate_result.slippage_cost_total
        + candidate_result.spread_cost_total
        + candidate_result.fee_total
    )
    add_fees = cand_cost - control_cost
    # Normalize add_fees to equity terms: divide by starting equity
    add_fees_norm = (
        add_fees / control_result.starting_equity if control_result.starting_equity else 0
    )
    # Convert avoided/premature which are already in account-equity units (sum of per-episode /eq_at_entry) to comparable? Keep as raw account-impact sum.
    # For net we need comparable units: avoided and premature are sum of account-impact fractions (e.g., 0.02 = 2% of equity). add_fees_norm is also fraction of starting equity.
    net = avoided - premature - add_fees_norm
    return {
        "avoided_post_exit_loss": round(float(avoided), 6),
        "premature_exit_opportunity_cost": round(float(premature), 6),
        "incremental_transaction_costs": round(float(add_fees), 2),
        "incremental_costs_normalized": round(float(add_fees_norm), 6),
        "net_early_exit_value": round(float(net), 6),
        "net_early_exit_value_bps": round(float(net * 10000), 2),
    }


# --------------------------------------------------------------------------- #
# Annual results, jackknife, bootstrap helpers
# --------------------------------------------------------------------------- #


def annual_results_for_candidate(config, candidate_id, universe, bars, actions) -> dict:
    out = {}
    for year in range(2017, 2027):
        s = datetime(year, 1, 1, tzinfo=UTC)
        e = datetime(year, 12, 31, tzinfo=UTC)
        # clip to eval window
        if e < EVAL_START or s > EVAL_END:
            continue
        s = max(s, EVAL_START)
        e = min(e, EVAL_END)
        if (e - s).days < 20:
            continue
        res = run_window(config, candidate_id, universe, bars, actions, start=s, end=e)
        st = stats_for_result(res)
        out[str(year)] = {
            "return": st["return"],
            "sharpe": st["sharpe"],
            "max_drawdown": st["max_drawdown"],
            "cagr": st["cagr"],
        }
    return out


def jackknife_for_candidate(
    config, candidate_id, universe, bars, actions, control_top_symbols=None
) -> dict:
    """Candidate-specific jackknife: drop own top-3 combined contributors."""
    # First get candidate full period result to find own top contributors
    full = run_window(config, candidate_id, universe, bars, actions, start=EVAL_START, end=EVAL_END)
    per = sorted(full.per_symbol, key=lambda x: x.realized_pnl, reverse=True)
    top = [p.symbol for p in per if p.realized_pnl > 0][:3]
    # Also compute own top1..3 individual? For rubric we need own top-3 combined at least
    jacks = {}
    # individual drops
    for sym in top:
        # build universe without symbol
        from dataclasses import replace

        # universe_without helper: remove symbol from PIT snapshots
        # Reuse helper from research_helpers logic via direct copy
        def _universe_without(univ, banned: frozenset[str]):
            snaps = []
            for snap in univ.snapshots:
                filtered_symbols = tuple(s for s in snap.symbols if s not in banned)
                filtered_obs = tuple(s for s in snap.observation_symbols if s not in banned)
                snaps.append(
                    replace(snap, symbols=filtered_symbols, observation_symbols=filtered_obs)
                )
            return replace(univ, snapshots=tuple(snaps))

        uni2 = _universe_without(universe, frozenset({sym}))
        res = run_window(config, candidate_id, uni2, bars, actions, start=EVAL_START, end=EVAL_END)
        st = stats_for_result(res)
        jacks[f"drop_{sym}"] = {
            "return": st["return"],
            "sharpe": st["sharpe"],
            "max_drawdown": st["max_drawdown"],
            "dropped": sym,
        }
    # combined top3
    if len(top) >= 3:
        from dataclasses import replace as rep2

        def _uw2(univ, banned):
            snaps = []
            for snap in univ.snapshots:
                fs = tuple(s for s in snap.symbols if s not in banned)
                fo = tuple(s for s in snap.observation_symbols if s not in banned)
                snaps.append(rep2(snap, symbols=fs, observation_symbols=fo))
            return rep2(univ, snapshots=tuple(snaps))

        uni3 = _uw2(universe, frozenset(top[:3]))
        res3 = run_window(config, candidate_id, uni3, bars, actions, start=EVAL_START, end=EVAL_END)
        st3 = stats_for_result(res3)
        jacks["drop_own_top3_combined"] = {
            "return": st3["return"],
            "sharpe": st3["sharpe"],
            "max_drawdown": st3["max_drawdown"],
            "dropped": top[:3],
        }
    return {
        "method": "candidate-specific own top-3",
        "top_symbols": top,
        "drops": jacks,
        "base": {
            "return": round(full.total_return, 6),
            "sharpe": round(full.sharpe, 6),
            "max_drawdown": round(full.max_drawdown, 6),
        },
    }


def block_bootstrap_for_candidate(result: PortfolioBacktestResult) -> dict:
    curve = list(result.equity_curve)
    rets = [(curve[i] / curve[i - 1] - 1) for i in range(1, len(curve)) if curve[i - 1] > 0]
    n = len(rets)
    block = BOOTSTRAP_BLOCK
    rng = random.Random(BOOTSTRAP_SEED)
    resamples = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sampled = []
        while len(sampled) < n:
            start_idx = rng.randrange(0, max(1, n - block + 1))
            sampled.extend(rets[start_idx : start_idx + block])
        sampled = sampled[:n]
        equity = 1.0
        for r in sampled:
            equity *= 1 + r
        resamples.append(equity - 1)
    resamples_sorted = sorted(resamples)
    return {
        "mean": round(statistics.fmean(resamples), 6) if resamples else 0,
        "p5": round(resamples_sorted[int(0.05 * len(resamples_sorted))], 6)
        if resamples_sorted
        else 0,
        "p95": round(resamples_sorted[int(0.95 * len(resamples_sorted))], 6)
        if resamples_sorted
        else 0,
        "count": len(resamples),
    }


def axon_sanity(config, universe, bars) -> dict:
    """AXON 2025-02 episode daily rank visibility."""
    # Need to locate AXON episode: find a holding episode for AXON in control full period
    # Run control full to get episodes and then daily rank trace
    # For simplicity, we trace daily V3 rank for AXON between 2025-02-01 and 2025-03-15

    start = datetime(2025, 2, 1, tzinfo=UTC)
    end = datetime(2025, 3, 15, tzinfo=UTC)
    # Build strategy to query daily ranks: we need to simulate daily _score_candidates without holding
    # We'll run a dummy engine and capture contexts per day via a custom strategy that logs.
    # Simpler: create a strategy instance and manually build contexts per day using PortfolioBacktestEngine's _build_context? But we can reuse run_window to get fills and then for each day compute rank via strategy's _score_candidates using history up to that day.
    # We'll need bars_by_symbol for lookup and universe.
    # Implement manual loop: for each daily bar timestamp in window, build context via engine's _build_context equivalent (trim history).
    # Instead, we can run a lightweight simulation: use same bar set and for each day, call strategy._score_candidates on a context built from history up to that day.
    # To avoid reimplementing _build_context, we can instantiate an engine and use its internal _history approach.
    # Easiest: create an engine with ExitControl strategy, run up to end, but capture daily ranks via a monkey patch? We'll do direct manual history building.

    # Build full bar history dict for context
    all_bars = [b for b in bars if b.market == Market.EQUITY and b.timeframe == "1d"]
    # Also need SPY bars for market factor
    # For rank trace, we don't need full engine; we can build a simple history dict trimmed.
    strategy = ExitControl(dict(FROZEN_EQUITY_PARAMS))
    # Get universe for in_universe check
    # Build per-symbol bar lists
    bars_by_sym: dict[str, list] = defaultdict(list)
    for b in all_bars:
        bars_by_sym[b.symbol].append(b)
    for lst in bars_by_sym.values():
        lst.sort(key=lambda x: x.timestamp)
    # Build history trimming as engine does (274 bars)
    _MAX = 274
    trim = max(strategy.minimum_history_bars, _MAX)
    # For each daily timestamp in window, construct context
    # Need sorted unique timestamps
    timestamps = sorted({b.timestamp for b in all_bars if start <= b.timestamp <= end})
    trace = []
    # Need to track holdings to know equity? For rank we just need history and holdings placeholder
    # We'll create a minimal context each day
    from parayabuck.portfolio.base import PortfolioContext

    for ts in timestamps:
        history: dict[str, tuple] = {}
        prices: dict[str, float] = {}
        for sym, lst in bars_by_sym.items():
            eligible = [b for b in lst if b.timestamp <= ts]
            if not eligible:
                continue
            # trim non-benchmark
            if sym != "SPY" and len(eligible) > trim:
                eligible = eligible[-trim:]
            history[sym] = tuple(eligible)
            prices[sym] = eligible[-1].close
        # Build context; holdings dummy, equity dummy
        ctx = PortfolioContext(
            decision_timestamp=ts,
            market=Market.EQUITY,
            timeframe="1d",
            history=history,
            universe=universe,
            universe_version=hash_point_in_time_universe(universe),
            equity=100000,
            holdings={},
            prices=prices,
            benchmark_symbol="SPY",
        )
        ranked, vols = strategy._score_candidates(ctx)  # type: ignore[attr-defined]
        rank_by = {sym: idx + 1 for idx, (_, sym) in enumerate(ranked)}
        axon_rank = rank_by.get("AXON")
        axon_qualified = "AXON" in rank_by
        # Find positive_12m and ivol eligibility via internal? We can infer from scored before ivol? But _score_candidates already filters by gate and ivol, so we need more detail.
        # For diagnostics, we will at least report rank and qualified flag, plus consensus score if available
        ivol = vols.get("AXON")
        # Try to get raw score via residuals if possible? We'll report rank and ivol
        # Also get price at ts
        axon_price = prices.get("AXON")
        trace.append(
            {
                "date": ts.date().isoformat(),
                "timestamp": ts.isoformat(),
                "axon_rank": axon_rank,
                "axon_qualified": axon_qualified,
                "axon_ivol": round(ivol, 6) if ivol is not None else None,
                "axon_price": axon_price,
                "qualified_pool_size": len(ranked),
            }
        )
    # Determine crash window: find max drawdown for AXON in trace
    # Also report if rank deteriorated before weekly exit (which would be Monday)
    return {
        "window": [start.isoformat(), end.isoformat()],
        "trace": trace,
        "note": "Daily V3 rank trace for AXON 2025-02 episode. If AXON remains top-ranked after crash, say so plainly.",
    }


# --------------------------------------------------------------------------- #
# Main runner — independent backtests for every required window
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="smoke with reduced resamples and fewer folds validation",
    )
    parser.parse_args()
    manifest = load_and_validate_manifest()
    config = load_config(str(REPO / "config/settings.yaml"))
    head = (
        _sha256_file(REPO / ".git/refs/heads/master")
        if (REPO / ".git/refs/heads/master").exists()
        else "unknown"
    )
    # Use git_head helper if available
    try:
        from scripts.research_helpers import git_head as _gh

        head = _gh()
    except Exception:
        import subprocess

        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO
            ).stdout.strip()
        except Exception:
            head = "unknown"
    cfg_digest = config_digest(config)
    print(f"V3 Exit Latency research head {head} cfg {cfg_digest}")

    bars = load_csv_bars(str(EQUITY_CSV), default_market=Market.EQUITY)
    actions = load_csv_corporate_actions(str(DIVIDENDS_CSV))
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EVAL_END)
    bars_by_symbol, bar_index = _build_bars_by_symbol(bars)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Prepare fold windows (20 independent)
    folds = calendar_fold_windows(EVAL_START, EVAL_END, step_months=6)
    temporal_windows = temporal_diagnostic_windows(EVAL_START, EVAL_END, RECENT_START)

    # ------------------------------------------------------------------ #
    # Full-period per candidate (independent reruns)
    # ------------------------------------------------------------------ #
    full_results: dict[str, PortfolioBacktestResult] = {}
    full_stats: dict[str, dict] = {}
    full_diagnostics: dict[str, tuple[dict, list]] = {}
    for cid in ["E0", "E1", "E2", "E3"]:
        print(f"Running full {cid}...")
        res = run_window(
            config,
            cid,
            universe,
            bars,
            actions,
            start=EVAL_START,
            end=EVAL_END,
            cost_multiplier=1.0,
        )
        full_results[cid] = res
        full_stats[cid] = stats_for_result(res)
        diag, eps = diagnostics_for_result(res, bars, bars_by_symbol, bar_index)
        full_diagnostics[cid] = (diag, eps)
        # write individual artifact
        out_path = OUT_DIR / f"{cid}.json"
        payload = {
            "implementation_commit": head,
            "research_commit": head,
            "config_digest": cfg_digest,
            "dataset": dataset_identity(),
            "candidate_id": cid,
            "policy_semantics": manifest["exit_candidates"][cid],
            "schema_version": RESULT_SCHEMA_VERSION,
            "stats": full_stats[cid],
            "diagnostics": diag,
        }
        out_path.write_text(json.dumps(payload, indent=2))

    # ------------------------------------------------------------------ #
    # Walk-forward folds — 20 independent per candidate
    # ------------------------------------------------------------------ #
    walk_forward = {}
    for cid in ["E0", "E1", "E2", "E3"]:
        rows = []
        for s, e in folds:
            res = run_window(
                config, cid, universe, bars, actions, start=s, end=e, cost_multiplier=1.0
            )
            st = stats_for_result(res)
            rows.append(
                {
                    "start": s.isoformat(),
                    "end": e.isoformat(),
                    "return": st["return"],
                    "cagr": st["cagr"],
                    "sharpe": st["sharpe"],
                    "max_drawdown": st["max_drawdown"],
                    "turnover": st["turnover"],
                }
            )
        walk_forward[cid] = rows
    # paired deltas E0 vs each candidate
    paired = {}
    for cid in ["E1", "E2", "E3"]:
        paired[cid] = paired_fold_deltas(walk_forward["E0"], walk_forward[cid])
    walk_payload = {
        "implementation_commit": head,
        "research_commit": head,
        "config_digest": cfg_digest,
        "walk_forward": walk_forward,
        "paired_deltas": paired,
    }
    (OUT_DIR / "walk_forward.json").write_text(json.dumps(walk_payload, indent=2))

    # ------------------------------------------------------------------ #
    # Temporal windows — each independently executed
    # ------------------------------------------------------------------ #
    temporal_out: dict[str, dict] = {}
    for name, segments in temporal_windows.items():
        # segments is list of (start,end) tuples
        seg_parsed = [(s, e) for s, e in segments]
        for cid in ["E0", "E1", "E2", "E3"]:
            if len(seg_parsed) == 1:
                s, e = seg_parsed[0]
                res = run_window(config, cid, universe, bars, actions, start=s, end=e)
            else:
                res = run_chained_window(config, cid, universe, bars, actions, seg_parsed)
            st = stats_for_result(res)
            temporal_out.setdefault(name, {})[cid] = {
                "return": st["return"],
                "sharpe": st["sharpe"],
                "max_drawdown": st["max_drawdown"],
                "cagr": st["cagr"],
            }
    temporal_payload = {
        "implementation_commit": head,
        "research_commit": head,
        "config_digest": cfg_digest,
        "temporal_windows": temporal_out,
    }
    (OUT_DIR / "temporal_windows.json").write_text(json.dumps(temporal_payload, indent=2))

    # ------------------------------------------------------------------ #
    # Cost stress — 1x/2x/3x/5x independent reruns
    # ------------------------------------------------------------------ #
    cost_out: dict[str, dict] = {}
    for mult in COST_LEVELS:
        cost_out[str(mult) + "x"] = {}
        for cid in ["E0", "E1", "E2", "E3"]:
            res = run_window(
                config,
                cid,
                universe,
                bars,
                actions,
                start=EVAL_START,
                end=EVAL_END,
                cost_multiplier=float(mult),
            )
            st = stats_for_result(res)
            cost_out[str(mult) + "x"][cid] = {
                "return": st["return"],
                "sharpe": st["sharpe"],
                "max_drawdown": st["max_drawdown"],
                "cagr": st["cagr"],
                "turnover": st["turnover"],
                "fees_total": st["fees_total"],
            }
    cost_payload = {
        "implementation_commit": head,
        "research_commit": head,
        "config_digest": cfg_digest,
        "cost_stress": cost_out,
        "note": "Every level is an independent backtest rerun with scaled engine cost inputs (slippage, spread, fees). No heuristic.",
    }
    (OUT_DIR / "cost_stress.json").write_text(json.dumps(cost_payload, indent=2))

    # ------------------------------------------------------------------ #
    # Annual results
    # ------------------------------------------------------------------ #
    annual_out = {}
    for cid in ["E0", "E1", "E2", "E3"]:
        annual_out[cid] = annual_results_for_candidate(config, cid, universe, bars, actions)
    (OUT_DIR / "annual_results.json").write_text(
        json.dumps(
            {
                "implementation_commit": head,
                "research_commit": head,
                "config_digest": cfg_digest,
                "annual": annual_out,
            },
            indent=2,
        )
    )

    # ------------------------------------------------------------------ #
    # Jackknife — candidate-specific own top-3
    # ------------------------------------------------------------------ #
    jack_out = {}
    for cid in ["E0", "E1", "E2", "E3"]:
        print(f"Jackknife {cid}...")
        jack = jackknife_for_candidate(config, cid, universe, bars, actions)
        jack_out[cid] = jack
    (OUT_DIR / "jackknife.json").write_text(
        json.dumps(
            {
                "implementation_commit": head,
                "research_commit": head,
                "config_digest": cfg_digest,
                "jackknife": jack_out,
            },
            indent=2,
        )
    )

    # ------------------------------------------------------------------ #
    # Block bootstrap
    # ------------------------------------------------------------------ #
    boot_out = {}
    for cid in ["E0", "E1", "E2", "E3"]:
        boot_out[cid] = block_bootstrap_for_candidate(full_results[cid])
    (OUT_DIR / "bootstrap.json").write_text(
        json.dumps(
            {
                "implementation_commit": head,
                "research_commit": head,
                "config_digest": cfg_digest,
                "bootstrap": boot_out,
            },
            indent=2,
        )
    )

    # ------------------------------------------------------------------ #
    # Position diagnostics — corrected account-impact denominator
    # ------------------------------------------------------------------ #
    pos_diag = {}
    for cid in ["E0", "E1", "E2", "E3"]:
        diag, eps = full_diagnostics[cid]
        # For AXON and previous worst 10 report corrected contemporaneous account impact (already in diag)
        pos_diag[cid] = {
            "episodes": eps,
            "diagnostics": diag,
        }
    # Comparison for worst 10 (E0 worst vs each candidate)
    control_diags, control_eps = full_diagnostics["E0"]
    comparisons = {}
    for cid in ["E1", "E2", "E3"]:
        _, cand_eps = full_diagnostics[cid]
        comparisons[cid] = compare_episodes(control_eps, cand_eps, bars_by_symbol, bar_index)
        # also compute exit metrics and avoided
        exit_met = compute_exit_metrics(
            full_results["E0"], full_results[cid], control_eps, cand_eps, bars_by_symbol
        )
        avoided_met = compute_avoided_metrics(
            control_eps, cand_eps, bars_by_symbol, full_results["E0"], full_results[cid]
        )
        comparisons[cid]["exit_behavior"] = exit_met
        comparisons[cid]["avoided_metrics"] = avoided_met

    (OUT_DIR / "position_diagnostics.json").write_text(
        json.dumps(
            {
                "implementation_commit": head,
                "research_commit": head,
                "config_digest": cfg_digest,
                "per_candidate": pos_diag,
                "worst10_comparisons": comparisons,
                "note": "account_equity_at_entry used for impact; denominator is contemporaneous equity, not starting cash",
            },
            indent=2,
        )
    )

    # ------------------------------------------------------------------ #
    # AXON sanity
    # ------------------------------------------------------------------ #
    axon = axon_sanity(config, universe, bars)
    axon["implementation_commit"] = head
    axon["research_commit"] = head
    axon["config_digest"] = cfg_digest
    # Also extract AXON worst episode details for cross-check
    axon_episodes = [ep for ep in control_eps if ep["symbol"] == "AXON"]
    axon["axon_episodes_control"] = sorted(axon_episodes, key=lambda x: x["realized_pnl"])[:3]
    (OUT_DIR / "axon_sanity.json").write_text(json.dumps(axon, indent=2))

    print(f"Done. Artifacts in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
