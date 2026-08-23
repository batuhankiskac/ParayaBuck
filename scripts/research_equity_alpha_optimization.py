#!/usr/bin/env python3
"""Equity alpha optimization research harness — mission 2026-08 (Phase 2).

Predeclared residual-momentum alpha research on the CORRECTED control. The
committed manifest (``data/research_manifests/equity_alpha_optimization_2026_08.json``)
is authoritative: the runner refuses to execute when its built-in VARIANTS or
frozen research parameters drift from the declaration.

Candidate families (all research-only strategies; production untouched):

* CONTROL       corrected production-v2 residual momentum (reference);
* A3            standardized score + equal sizing (prior RESEARCH PASS);
* H25/H50/H75   raw + standardized blend at 0.75/0.50/0.25 raw weight;
* MH_RAW/MH_STD multi-horizon consensus (6-1/9-1/12-1 months), raw or
                per-horizon standardized, rank-normalized per horizon;
* *_SQRT       sqrt-inverse-vol sizing (CONTROL/H50/MHSTD rankings);
* *_REGIME     SPY regime derating (exposure cut to 0.5 in the frozen
                ELEVATED regime) on CONTROL/A_/H50/MHSTD;
* CONTROL_HYST entry rank<=8 / hold rank<=12 selection hysteresis.

Per-variant stages: full window, 20 anchored 6-month walk-forward folds,
calendar-year windows, 2022+ recent window, cost stress 1x/2x/3x/5x, regime
scrub (all variants) + regime decision log (*_REGIME), concentration,
jackknife (drop each of CONTROL's top-5 PnL names + the combined top-3),
and a 20-day block bootstrap over the daily equity returns.

Usage:
    python scripts/research_equity_alpha_optimization.py \
        --variants control,h25,h50 [--skip-jackknife] [--smoke]
    python scripts/research_equity_alpha_optimization.py --aggregate
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research_helpers import (
    config_digest,
    dataset_identity,
    git_head,
    sha256_of_file,
)

from parayabuck.backtest.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
)
from parayabuck.backtest.product import (
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    UniverseSnapshot,
    hash_point_in_time_universe,
)
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_residual_momentum import (
    EquityResidualMomentum1dV2,
)
from parayabuck.portfolio.research_alpha import (
    ELEVATED,
    NORMAL,
    _compounded,
    _standardized,
    blend_scores,
    horizon_consensus_scores,
    hysteresis_selection,
    spy_regime_state,
    sqrt_ivol_weights,
)

REPO = Path(__file__).resolve().parents[1]
EQUITY_CSV = REPO / "data/historical/equity_1200_2016_2026.csv"
DIVIDENDS_CSV = REPO / "data/historical/equity_1200_dividends.csv"
OUT_DIR = REPO / "data/research/equity_alpha_optimization_2026_08"
MANIFEST_PATH = REPO / "data/research_manifests/equity_alpha_optimization_2026_08.json"
RESULT_SCHEMA_VERSION = "1.0.0"

EVAL_START = datetime(2017, 1, 3, tzinfo=UTC)
EVAL_END = datetime(2026, 7, 30, tzinfo=UTC)
FOLD_STEP_MONTHS = 6
RECENT_START = datetime(2022, 1, 1, tzinfo=UTC)
JACKKNIFE_TOP = 5
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 202608230

_FROZEN: dict[str, object] = {
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


def _variant_class(
    name: str, base: type[EquityResidualMomentum1dV2]
) -> type[EquityResidualMomentum1dV2]:
    return type(name, (base,), {"name": name.lower(), "version": "1.0.0", "display_name": name})


# --------------------------------------------------------------------------- #
# Research identities (each a DISTINCT name; production untouched)
# --------------------------------------------------------------------------- #


class AlphaResearchControl(EquityResidualMomentum1dV2):
    """CONTROL identity under corrected semantics (production math)."""


class AlphaResearchStandardized(AlphaResearchControl):
    """A-family score: compounded residual return standardized by the
    formation-window residual stdev (Blitz-Huij-Martens style)."""

    def _candidate_score(self, residuals: list[float], *, formation: int, skip: int) -> float:
        return _standardized(residuals[-formation - skip : -skip])


class AlphaResearchA3Equal(AlphaResearchStandardized):
    """A: standardized score + EQUAL weights (isolates selection from
    inverse-vol sizing)."""

    def _position_weights(
        self, selected: tuple[str, ...], residual_vols: dict[str, float]
    ) -> dict[str, float]:
        cap = self.param_float("max_single_weight", 0.10)
        weight = min(1.0 / max(1, len(selected)), cap)
        return {symbol: weight for symbol in selected}


class AlphaResearchBlend(AlphaResearchControl):
    """C1: raw/standardized blend on cross-sectional percentile ranks."""

    def _transform_cross_sectional_scores(
        self,
        scored: list[tuple[float, str]],
        residuals_by_symbol: dict[str, list[float]],
        residual_vols: dict[str, float],
        *,
        formation: int,
        skip: int,
    ) -> list[tuple[float, str]]:
        alpha_raw = self.param_float("blend_alpha_raw", 0.50)
        raw: dict[str, float] = {}
        std: dict[str, float] = {}
        for symbol, residuals in residuals_by_symbol.items():
            window = residuals[-formation - skip : -skip]
            raw[symbol] = _compounded(window)
            std[symbol] = _standardized(window)
        blended = blend_scores(raw, std, alpha_raw)
        return [(blended[symbol], symbol) for _score, symbol in scored]


class AlphaResearchMultiHorizon(AlphaResearchControl):
    """Multi-horizon consensus: 6-1/9-1/12-1 month residual momentum,
    rank-normalized per horizon and averaged (MH_RAW / MH_STD)."""

    _HORIZONS = (6 * 21, 9 * 21, 12 * 21)

    def _transform_cross_sectional_scores(
        self,
        scored: list[tuple[float, str]],
        residuals_by_symbol: dict[str, list[float]],
        residual_vols: dict[str, float],
        *,
        formation: int,
        skip: int,
    ) -> list[tuple[float, str]]:
        standardized = bool(self.parameters.get("multi_horizon_standardized", False))
        consensus = horizon_consensus_scores(
            residuals_by_symbol, self._HORIZONS, skip, standardized=standardized
        )
        return [(consensus[symbol], symbol) for _score, symbol in scored]


class AlphaResearchSqrtIvol(AlphaResearchControl):
    """SQRT-inverse-vol sizing (intermediate between inverse-vol and equal)."""

    def _position_weights(
        self, selected: tuple[str, ...], residual_vols: dict[str, float]
    ) -> dict[str, float]:
        return dict(
            sqrt_ivol_weights(
                selected, residual_vols, maximum_weight=self.param_float("max_single_weight", 0.10)
            )
        )


class AlphaResearchRegime(AlphaResearchControl):
    """SPY-regime exposure derating: after alpha target weights are set,
    scale to 0.5 in the ELEVATED regime (see research_alpha.spy_regime_state)."""

    def _post_target_weights(
        self,
        weights: dict[str, float],
        context: PortfolioContext,
    ) -> dict[str, float]:
        regime, multiplier = spy_regime_state(context.closes(context.benchmark_symbol))
        events = getattr(self, "_regime_events", None)
        if events is None:
            events = []
            self._regime_events = events
        events.append((context.decision_timestamp, regime, multiplier))
        if multiplier == 1.0:
            return weights
        return {symbol: weight * multiplier for symbol, weight in weights.items()}


class AlphaResearchHysteresis(AlphaResearchControl):
    """CONTROL_HYST: entry <= 8 / hold <= 12 / fill from best eligible."""

    def _select_names(
        self,
        ranked: tuple[tuple[float, str], ...],
        residual_vols: dict[str, float],
        context: PortfolioContext,
        top_n: int,
    ) -> tuple[str, ...]:
        return hysteresis_selection(
            list(ranked),
            context.holdings,
            top_n=top_n,
            entry_rank=self.param_int("hyst_entry_rank", 8),
            hold_rank=self.param_int("hyst_hold_rank", 12),
        )


# Composite identities (the mission's fixed combination set — nothing else).
def _blend_sqrt() -> type[EquityResidualMomentum1dV2]:
    return type(
        "AlphaResearchBlendSqrt",
        (AlphaResearchBlend, AlphaResearchSqrtIvol),
        {"name": "alpha_research_blend_sqrt", "version": "1.0.0", "display_name": "Blend+SQRT"},
    )


def _blend_regime() -> type[EquityResidualMomentum1dV2]:
    return type(
        "AlphaResearchBlendRegime",
        (AlphaResearchBlend, AlphaResearchRegime),
        {"name": "alpha_research_blend_regime", "version": "1.0.0", "display_name": "Blend+Regime"},
    )


def _multi_horizon_sqrt() -> type[EquityResidualMomentum1dV2]:
    return type(
        "AlphaResearchMultiHorizonSqrt",
        (AlphaResearchMultiHorizon, AlphaResearchSqrtIvol),
        {"name": "alpha_research_mh_sqrt", "version": "1.0.0", "display_name": "MHSTD+SQRT"},
    )


def _multi_horizon_regime() -> type[EquityResidualMomentum1dV2]:
    return type(
        "AlphaResearchMultiHorizonRegime",
        (AlphaResearchMultiHorizon, AlphaResearchRegime),
        {"name": "alpha_research_mh_regime", "version": "1.0.0", "display_name": "MH+Regime"},
    )


def _a3_regime() -> type[EquityResidualMomentum1dV2]:
    return type(
        "AlphaResearchA3Regime",
        (AlphaResearchA3Equal, AlphaResearchRegime),
        {"name": "alpha_research_a3_regime", "version": "1.0.0", "display_name": "A3+Regime"},
    )


VARIANTS: dict[str, tuple[type[EquityResidualMomentum1dV2], dict[str, object]]] = {
    "control": (AlphaResearchControl, {}),
    "a3": (_variant_class("AlphaResearchA3", AlphaResearchA3Equal), {}),
    "h25": (_variant_class("AlphaResearchH25", AlphaResearchBlend), {"blend_alpha_raw": 0.75}),
    "h50": (_variant_class("AlphaResearchH50", AlphaResearchBlend), {"blend_alpha_raw": 0.50}),
    "h75": (_variant_class("AlphaResearchH75", AlphaResearchBlend), {"blend_alpha_raw": 0.25}),
    "mh_raw": (
        _variant_class("AlphaResearchMHRaw", AlphaResearchMultiHorizon),
        {"multi_horizon_standardized": False},
    ),
    "mh_std": (
        _variant_class("AlphaResearchMHStd", AlphaResearchMultiHorizon),
        {"multi_horizon_standardized": True},
    ),
    "control_sqrt": (_variant_class("AlphaResearchControlSqrt", AlphaResearchSqrtIvol), {}),
    "h50_sqrt": (_variant_class("AlphaResearchH50Sqrt", _blend_sqrt()), {"blend_alpha_raw": 0.50}),
    "mhstd_sqrt": (
        _variant_class("AlphaResearchMHStdSqrt", _multi_horizon_sqrt()),
        {"multi_horizon_standardized": True},
    ),
    "control_regime": (_variant_class("AlphaResearchControlRegime", AlphaResearchRegime), {}),
    "a3_regime": (_variant_class("AlphaResearchA3Regime", _a3_regime()), {}),
    "h50_regime": (
        _variant_class("AlphaResearchH50Regime", _blend_regime()),
        {"blend_alpha_raw": 0.50},
    ),
    "mhstd_regime": (
        _variant_class("AlphaResearchMHStdRegime", _multi_horizon_regime()),
        {"multi_horizon_standardized": True},
    ),
    "control_hyst": (_variant_class("AlphaResearchControlHyst", AlphaResearchHysteresis), {}),
}


# --------------------------------------------------------------------------- #
# Walk-forward / stage definitions
# --------------------------------------------------------------------------- #


def fold_windows() -> list[tuple[datetime, datetime]]:
    folds: list[tuple[datetime, datetime]] = []
    cursor = EVAL_START
    while cursor < EVAL_END:
        fold_end = min(cursor + timedelta(days=30 * FOLD_STEP_MONTHS), EVAL_END)
        if fold_end - cursor >= timedelta(days=20):
            folds.append((cursor, fold_end))
        cursor = fold_end
    return folds


def _annual_windows() -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    for year in range(EVAL_START.year, EVAL_END.year + 1):
        start = datetime(year, 1, 1, tzinfo=UTC)
        end = min(datetime(year + 1, 1, 1, tzinfo=UTC), EVAL_END)
        if start >= EVAL_END:
            break
        windows.append((start, end))
    return windows


def make_engine(
    config: Config,
    strategy: EquityResidualMomentum1dV2,
    universe: PointInTimeUniverse,
    *,
    cost_multiplier: float = 1.0,
) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
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
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
    )


def build_strategy(variant_id: str, parameters: dict[str, object]) -> EquityResidualMomentum1dV2:
    cls, overrides = VARIANTS[variant_id]
    merged = dict(parameters)
    merged.update(overrides)
    return cls(merged)


def run_window(
    config: Config,
    variant_id: str,
    parameters: dict[str, object],
    universe: PointInTimeUniverse,
    bars: list[Bar],
    *,
    start: datetime | None,
    end: datetime | None,
    cost_multiplier: float = 1.0,
    actions: list[CorporateAction] | None = None,
) -> tuple[PortfolioBacktestResult, EquityResidualMomentum1dV2]:
    strategy = build_strategy(variant_id, parameters)
    available = [
        bar for bar in bars if bar.market == Market.EQUITY and bar.timeframe == strategy.timeframe
    ]
    if end is not None:
        available = [bar for bar in available if bar.timestamp <= end]
    filtered = available if start is None else [bar for bar in available if bar.timestamp >= start]
    warmup = [] if start is None else [bar for bar in available if bar.timestamp < start]
    engine = make_engine(config, strategy, universe, cost_multiplier=cost_multiplier)
    return engine.run(filtered, warmup=warmup, corporate_actions=actions), strategy


# --------------------------------------------------------------------------- #
# Post-run statistics
# --------------------------------------------------------------------------- #


def _profit_factor(result: PortfolioBacktestResult) -> float:
    gains = sum(pnl for pnl in result.realized_pnls if pnl > 0)
    losses = -sum(pnl for pnl in result.realized_pnls if pnl < 0)
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return round(gains / losses, 4)


def rebalance_stats(result: PortfolioBacktestResult) -> dict[str, object]:
    """Win rate / average winning & losing rebalance from the equity curve."""
    curve = result.equity_curve
    times = result.curve_timestamps
    decisions = [record.decision_timestamp for record in result.rebalances]
    if not decisions or not curve:
        return {"rebalance_win_rate": 0.0}
    index_by_time = {timestamp: index for index, timestamp in enumerate(times)}
    picks = [index_by_time[t] for t in decisions if t in index_by_time]
    picks = sorted(set([0] + picks))
    interval_returns: list[float] = []
    for left, right in zip(picks, picks[1:]):
        if 0 < curve[left] and 0 < curve[right]:
            interval_returns.append(curve[right] / curve[left] - 1)
    wins = [value for value in interval_returns if value > 0]
    losses = [value for value in interval_returns if value <= 0]
    return {
        "rebalance_win_rate": round(len(wins) / len(interval_returns), 4)
        if interval_returns
        else 0.0,
        "avg_winning_rebalance": round(statistics.fmean(wins), 6) if wins else 0.0,
        "avg_losing_rebalance": round(statistics.fmean(losses), 6) if losses else 0.0,
        "profit_factor": _profit_factor(result),
    }


def full_stats(result: PortfolioBacktestResult) -> dict[str, float | int | None]:
    gross = result.gross_return
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    avg_equity = sum(result.equity_curve) / len(result.equity_curve) if result.equity_curve else 0.0
    gross_pnl = result.starting_equity * gross
    return {
        "return": round(result.net_return, 6),
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
        "fees_pct_avg_equity": round(total_cost / avg_equity, 6) if avg_equity > 0 else 0.0,
        "fees_pct_gross_pnl": (
            round(total_cost / abs(gross_pnl), 6) if abs(gross_pnl) > 1e-9 else None
        ),
        "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
        "avg_holdings": round(result.average_holdings_count, 3),
        "max_concentration_weight": round(result.maximum_concentration, 4),
        "rebalance_count": result.rebalance_count,
        "closed_positions": result.closed_positions,
        "dividend_total": round(result.dividend_total, 2),
        "worst_year": round(result.worst_year_return, 6),
        "worst_month": round(result.worst_month_return, 6),
        "positive_month_ratio": round(result.positive_month_ratio, 4),
        **rebalance_stats(result),
    }


def yearly_returns(result: PortfolioBacktestResult) -> dict[str, float]:
    buckets: dict[int, tuple[float, float]] = {}
    for equity, timestamp in zip(result.equity_curve, result.curve_timestamps):
        first, last = buckets.get(timestamp.year, (equity, equity))
        buckets[timestamp.year] = (first, equity)
    out: dict[str, float] = {}
    previous = result.starting_equity
    for year in sorted(buckets):
        _first, last = buckets[year]
        out[str(year)] = round(last / previous - 1, 6) if previous > 0 else 0.0
        previous = last
    return out


def concentration(result: PortfolioBacktestResult, *, top: int = 5) -> dict[str, object]:
    contributions = sorted(
        ((item.symbol, item.realized_pnl) for item in result.per_symbol),
        key=lambda item: (-item[1], item[0]),
    )
    positive_total = sum(pnl for _symbol, pnl in contributions if pnl > 0)
    top_items = contributions[:top]
    negative_items = [item for item in contributions if item[1] < 0]
    return {
        "top_symbols": [symbol for symbol, _pnl in top_items],
        "top_symbols_pnl": [round(pnl, 2) for _symbol, pnl in top_items],
        "top1_share_of_positive_pnl": (
            round(top_items[0][1] / positive_total, 4) if top_items and positive_total > 0 else 0.0
        ),
        "top3_share_of_positive_pnl": (
            round(sum(item[1] for item in top_items[:3]) / positive_total, 4)
            if top_items and positive_total > 0
            else 0.0
        ),
        "top5_share_of_positive_pnl": (
            round(sum(item[1] for item in top_items[:5]) / positive_total, 4)
            if top_items and positive_total > 0
            else 0.0
        ),
        "largest_negative_contributor": (
            (negative_items[-1][0], round(negative_items[-1][1], 2)) if negative_items else None
        ),
    }


def universe_without(universe: PointInTimeUniverse, banned: frozenset[str]) -> PointInTimeUniverse:
    snapshots = []
    for snapshot in universe.snapshots:
        snapshots.append(
            UniverseSnapshot(
                effective_at=snapshot.effective_at,
                symbols=frozenset(snapshot.symbols - banned),
                scanner_version=snapshot.scanner_version,
                config_digest=snapshot.config_digest,
                candidate_count=snapshot.candidate_count,
                score_breakdown=snapshot.score_breakdown,
                eliminated=snapshot.eliminated,
                sector_metadata_coverage=snapshot.sector_metadata_coverage,
                observation_set=frozenset(snapshot.observation_set - banned),
            )
        )
    return PointInTimeUniverse(
        market=universe.market, snapshots=tuple(snapshots), methodology=universe.methodology
    )


def summarize_folds(
    results: list[tuple[datetime, datetime, PortfolioBacktestResult]],
) -> dict[str, object]:
    stats = [
        {
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "return": round(result.net_return, 6),
            "sharpe": round(result.sharpe, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "turnover": round(result.turnover, 6),
        }
        for start, end, result in results
    ]
    returns = [item["return"] for item in stats]
    sharpes = [item["sharpe"] for item in stats]
    ordered = sorted(returns)
    n = len(ordered)
    p25 = ordered[int(0.25 * (n - 1))] if n else 0.0
    return {
        "folds": stats,
        "fold_count": n,
        "median_fold_return": round(statistics.median(returns), 6) if n else 0.0,
        "mean_fold_return": round(statistics.fmean(returns), 6) if n else 0.0,
        "fold_return_stdev": round(statistics.pstdev(returns), 6) if n else 0.0,
        "25th_percentile_fold_return": round(p25, 6),
        "worst_fold_return": min(returns) if returns else None,
        "best_fold_return": max(returns) if returns else None,
        "positive_fold_ratio": round(sum(1 for value in returns if value > 0) / n, 4) if n else 0.0,
        "median_fold_sharpe": round(statistics.median(sharpes), 6) if sharpes else 0.0,
    }


# --------------------------------------------------------------------------- #
# Regime diagnostics
# --------------------------------------------------------------------------- #


def spy_daily_regimes(bars: list[Bar]) -> dict[datetime, str]:
    """Classify every SPY bar with the frozen regime rule (no lookahead)."""
    spy = [bar for bar in bars if bar.symbol == "SPY" and bar.market == Market.EQUITY]
    spy.sort(key=lambda bar: bar.timestamp)
    closes: list[float] = []
    timestamps: list[datetime] = []
    for bar in spy:
        closes.append(bar.close)
        timestamps.append(bar.timestamp)
    out: dict[datetime, str] = {}
    for index in range(len(timestamps)):
        regime, _multiplier = spy_regime_state(closes[: index + 1])
        out[timestamps[index]] = regime
    return out


def regime_pnl(
    result: PortfolioBacktestResult, spy_daily: dict[datetime, str]
) -> dict[str, object]:
    """Ex-post candidate PnL attribution by SPY regime (diagnostic only)."""
    buckets: dict[str, list[float]] = {NORMAL: [], ELEVATED: []}
    previous: float | None = None
    for equity, timestamp in zip(result.equity_curve, result.curve_timestamps):
        regime = spy_daily.get(timestamp)
        if regime is None:
            continue
        if previous is not None and previous > 0:
            buckets[regime].append(equity / previous - 1)
        previous = equity
    out: dict[str, object] = {}
    for regime in (NORMAL, ELEVATED):
        daily_returns = buckets[regime]
        out[regime] = {
            "days": len(daily_returns),
            "mean_daily_return": round(statistics.fmean(daily_returns), 6)
            if daily_returns
            else 0.0,
            "total_return": round(sum(daily_returns), 6) if daily_returns else 0.0,
            "positive_day_ratio": (
                round(sum(1 for value in daily_returns if value > 0) / len(daily_returns), 4)
                if daily_returns
                else 0.0
            ),
        }
    return out


def regime_timeline(spy_daily: dict[datetime, str]) -> dict[str, object]:
    """Ex-post regime timeline stats from the frozen daily classifier."""
    ordered = sorted(spy_daily.items())
    transitions = 0
    longest = 0
    current = 0
    previous_class: str | None = None
    for _timestamp, regime in ordered:
        if previous_class is not None and regime != previous_class:
            transitions += 1
        if regime == ELEVATED:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
        previous_class = regime
    days = len(ordered)
    reduced_days = sum(1 for _timestamp, regime in ordered if regime == ELEVATED)
    return {
        "trading_days": days,
        "reduced_days": reduced_days,
        "reduced_day_fraction": round(reduced_days / days, 4) if days else 0.0,
        "regime_transitions": transitions,
        "longest_continuous_reduced_episode_days": longest,
    }


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


def block_bootstrap_deltas(
    control_curve: Sequence[float],
    candidate_curve: Sequence[float],
    *,
    block: int = BOOTSTRAP_BLOCK,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, object]:
    """Block bootstrap (fixed 20-trading-day blocks with replacement) over
    daily portfolio returns: candidate-minus-control deltas per resample."""
    control_returns = [
        control_curve[index] / control_curve[index - 1] - 1
        for index in range(1, len(control_curve))
        if control_curve[index - 1] > 0 and control_curve[index] > 0
    ]
    candidate_returns = [
        candidate_curve[index] / candidate_curve[index - 1] - 1
        for index in range(1, len(candidate_curve))
        if candidate_curve[index - 1] > 0 and candidate_curve[index] > 0
    ]
    n = min(len(control_returns), len(candidate_returns))
    rng = random.Random(seed)

    def blocks(returns: list[float]) -> list[list[float]]:
        return [returns[index : index + block] for index in range(0, n, block)]

    control_blocks = blocks(control_returns)
    candidate_blocks = blocks(candidate_returns)

    def metrics(returns: list[float]) -> tuple[float, float, float]:
        if not returns:
            return 0.0, 0.0, 0.0
        days = len(returns)
        years = days / 252.0
        compounded = 1.0
        for value in returns:
            compounded *= 1.0 + value
        annualized = compounded ** (1.0 / years) - 1.0 if years > 0 and compounded > 0 else 0.0
        mean = statistics.fmean(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (days - 1)
        sharpe = mean / math.sqrt(variance) * math.sqrt(252.0) if variance > 0 else 0.0
        peak = 1.0
        drawdown = 0.0
        running = 1.0
        for value in returns:
            running *= 1.0 + value
            peak = max(peak, running)
            drawdown = max(drawdown, (peak - running) / peak)
        return annualized, sharpe, drawdown

    deltas_return: list[float] = []
    deltas_sharpe: list[float] = []
    deltas_dd: list[float] = []
    for _ in range(resamples):
        sampled_c: list[float] = []
        sampled_v: list[float] = []
        while len(sampled_c) < n:
            picked = rng.randrange(len(control_blocks))
            sampled_c.extend(control_blocks[picked])
            sampled_v.extend(candidate_blocks[picked])
        sampled_c = sampled_c[:n]
        sampled_v = sampled_v[:n]
        ret_c, sharpe_c, dd_c = metrics(sampled_c)
        ret_v, sharpe_v, dd_v = metrics(sampled_v)
        deltas_return.append(ret_v - ret_c)
        deltas_sharpe.append(sharpe_v - sharpe_c)
        deltas_dd.append(dd_v - dd_c)
    deltas_return.sort()
    deltas_sharpe.sort()
    deltas_dd.sort()
    p5 = int(0.05 * (resamples - 1))
    p95 = int(0.95 * (resamples - 1))
    return {
        "method": f"block bootstrap, block={block}, resamples={resamples}, seed={seed}",
        "return_delta": {
            "median": round(deltas_return[len(deltas_return) // 2], 6),
            "p5": round(deltas_return[p5], 6),
            "p95": round(deltas_return[p95], 6),
        },
        "sharpe_delta": {
            "median": round(deltas_sharpe[len(deltas_sharpe) // 2], 6),
            "p5": round(deltas_sharpe[p5], 6),
            "p95": round(deltas_sharpe[p95], 6),
        },
        "maxdd_delta": {
            "median": round(deltas_dd[len(deltas_dd) // 2], 6),
            "p5": round(deltas_dd[p5], 6),
            "p95": round(deltas_dd[p95], 6),
        },
        "probability_sharpe_above_control": round(
            sum(1 for value in deltas_sharpe if value > 0) / resamples, 4
        ),
        "probability_return_above_control": round(
            sum(1 for value in deltas_return if value > 0) / resamples, 4
        ),
    }


# --------------------------------------------------------------------------- #
# Variant runner
# --------------------------------------------------------------------------- #


def run_variant(
    variant_id: str,
    config: Config,
    bars: list[Bar],
    universe: PointInTimeUniverse,
    actions: list[CorporateAction],
    *,
    smoke: bool,
    skip_jackknife: bool,
    control_top: list[str] | None,
    spy_daily: dict[datetime, str],
) -> dict[str, object]:
    cls, overrides = VARIANTS[variant_id]
    parameters = dict(_FROZEN)
    parameters.update(overrides)
    eval_start = EVAL_START
    eval_end = EVAL_END
    folds = fold_windows()
    if smoke:
        eval_start = datetime(2024, 1, 2, tzinfo=UTC)
        folds = folds[-4:]

    # Full window.
    full, full_strategy = run_window(
        config, variant_id, parameters, universe, bars, start=None, end=None, actions=actions
    )

    # Walk-forward folds (anchored: everything before each fold is warmup).
    fold_results: list[tuple[datetime, datetime, PortfolioBacktestResult]] = []
    for fold_start, fold_end in folds:
        if fold_start < eval_start or fold_end > eval_end:
            continue
        result, _strategy = run_window(
            config,
            variant_id,
            parameters,
            universe,
            bars,
            start=fold_start,
            end=fold_end,
            actions=actions,
        )
        fold_results.append((fold_start, fold_end, result))

    # Annual calendar-year windows (warmup = all prior bars, anchored).
    annual: dict[str, object] = {}
    for year_start, year_end in _annual_windows():
        if year_end <= eval_start:
            continue
        result, _strategy = run_window(
            config,
            variant_id,
            parameters,
            universe,
            bars,
            start=year_start,
            end=year_end,
            actions=actions,
        )
        annual[str(year_start.year)] = {
            "return": round(result.net_return, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
            "turnover": round(result.turnover, 6),
            "sharpe": round(result.sharpe, 6),
        }

    # Recent window (2022+ — more decision weight given survivorship bias).
    recent, _strategy = run_window(
        config,
        variant_id,
        parameters,
        universe,
        bars,
        start=RECENT_START,
        end=None,
        actions=actions,
    )

    # Cost stress: 1x is the full run above; 2x/3x/5x re-run the window.
    stress: dict[str, object] = {"1x": full_stats(full)}
    if not smoke:
        for multiplier in (2.0, 3.0, 5.0):
            stressed, _strategy = run_window(
                config,
                variant_id,
                parameters,
                universe,
                bars,
                start=None,
                end=None,
                cost_multiplier=multiplier,
                actions=actions,
            )
            stressed_stats = full_stats(stressed)
            stress[f"{multiplier:g}x"] = {
                "return": round(stressed.net_return, 6),
                "gross_return": round(stressed.gross_return, 6),
                "sharpe": round(stressed.sharpe, 6),
                "max_drawdown": round(stressed.max_drawdown, 6),
                "turnover": round(stressed.turnover, 6),
                "fees_total": stressed_stats["fees_total"],
                "fees_pct_gross_pnl": stressed_stats["fees_pct_gross_pnl"],
            }

    # Jackknife: drop each of CONTROL's top-5 realized-PnL symbols, plus the
    # combined top-3, from the same point-in-time universe.
    jackknife: dict[str, object] = {}
    if not smoke and not skip_jackknife and control_top:
        drops = control_top[:JACKKNIFE_TOP]
        for symbol in drops:
            reduced = universe_without(universe, frozenset({symbol}))
            result, _strategy = run_window(
                config,
                variant_id,
                parameters,
                reduced,
                bars,
                start=None,
                end=None,
                actions=actions,
            )
            jackknife[f"drop_{symbol}"] = {
                "return": round(result.net_return, 6),
                "sharpe": round(result.sharpe, 6),
                "max_drawdown": round(result.max_drawdown, 6),
            }
        combined = frozenset(control_top[:3])
        reduced_combined = universe_without(universe, combined)
        result, _strategy = run_window(
            config,
            variant_id,
            parameters,
            reduced_combined,
            bars,
            start=None,
            end=None,
            actions=actions,
        )
        jackknife["drop_top3_combined"] = {
            "return": round(result.net_return, 6),
            "sharpe": round(result.sharpe, 6),
            "max_drawdown": round(result.max_drawdown, 6),
        }

    # Regime: diagnostic decomposition (all) + decision-level info (*_REGIME).
    regime: dict[str, object] = {
        **regime_pnl(full, spy_daily),
        "regime_daily_timeline": regime_timeline(spy_daily),
    }
    strategy = full_strategy
    if hasattr(strategy, "_regime_events"):
        events = [
            {
                "timestamp": timestamp.isoformat(),
                "regime": regime_name,
                "multiplier": multiplier,
            }
            for timestamp, regime_name, multiplier in strategy._regime_events
        ]
        decisions_reduced = sum(1 for event in events if event["multiplier"] < 1.0)
        regime["regime_decisions"] = events
        regime["reduced_decision_fraction"] = (
            round(decisions_reduced / len(events), 4) if events else 0.0
        )
        regime["avg_multiplier"] = (
            round(statistics.fmean(event["multiplier"] for event in events), 4) if events else 1.0
        )

    artifact = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "variant": variant_id,
        "strategy_name": VARIANTS[variant_id][0].__name__,
        "parameters": {
            key: str(value) if isinstance(value, list) else value
            for key, value in sorted(parameters.items())
        },
        "full_period": full_stats(full),
        "concentration": concentration(full),
        "yearly_returns": yearly_returns(full),
        "full_equity_curve": [round(value, 4) for value in full.equity_curve],
        "annual_windows": annual,
        "walk_forward": summarize_folds(fold_results),
        "recent_period": {
            "start": RECENT_START.date().isoformat(),
            "end": EVAL_END.date().isoformat(),
            **full_stats(recent),
        },
        "cost_stress": stress,
        "jackknife": jackknife,
        "regime": regime,
    }
    return artifact


# --------------------------------------------------------------------------- #
# Manifest validation
# --------------------------------------------------------------------------- #


def _normalize_overrides(overrides: dict[str, object]) -> dict[str, object]:
    return {
        key: (str(value) if isinstance(value, bool) else value) for key, value in overrides.items()
    }


def _normalize_parameters(parameters: dict[str, object]) -> dict[str, object]:
    return {
        key: (str(value) if isinstance(value, list) else value) for key, value in parameters.items()
    }


def load_and_validate_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise SystemExit(
            f"predeclaration manifest missing: {MANIFEST_PATH} (commit it; "
            "research without a predeclared contract is not runnable here)"
        )
    manifest = json.loads(MANIFEST_PATH.read_text())
    declared = manifest.get("variants", {})
    built_in = {
        variant_id: {
            "base": cls.__name__,
            "overrides": _normalize_overrides(overrides),
        }
        for variant_id, (cls, overrides) in VARIANTS.items()
    }
    normalized_declared = {
        variant_id: {
            "base": spec["base"],
            "overrides": _normalize_overrides(spec.get("overrides", {})),
        }
        for variant_id, spec in declared.items()
    }
    if normalized_declared != built_in:
        drift = {
            key
            for key in set(normalized_declared) | set(built_in)
            if normalized_declared.get(key) != built_in.get(key)
        }
        raise SystemExit(
            "VARIANTS drifted from the committed predeclaration: "
            f"{sorted(drift)}. Update the code AND the manifest together."
        )
    frozen = manifest.get("frozen_parameters", {})
    if _normalize_parameters(frozen) != _normalize_parameters(_FROZEN):
        drift = {key for key in set(frozen) | set(_FROZEN) if frozen.get(key) != _FROZEN.get(key)}
        raise SystemExit(f"frozen parameters drifted from the manifest: {sorted(drift)}")
    return manifest


# --------------------------------------------------------------------------- #
# Aggregate artifacts
# --------------------------------------------------------------------------- #


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[int(q * (len(ordered) - 1))], 6)


def load_variant_artifact(variant_id: str) -> dict:
    path = OUT_DIR / f"{variant_id}.json"
    if not path.exists():
        raise SystemExit(f"missing artifact {path} — run the variant first")
    return json.loads(path.read_text())


def aggregate() -> int:
    manifest = load_and_validate_manifest()
    declared_ids = list(manifest.get("variants", {}))
    all_artifacts: dict[str, dict] = {"control": load_variant_artifact("control")}
    for variant_id in declared_ids:
        if variant_id != "control":
            all_artifacts[variant_id] = load_variant_artifact(variant_id)

    # summary.json — full-period headline table.
    summary = {
        candidate_id: {
            "return": artifact["full_period"]["return"],
            "cagr": artifact["full_period"]["cagr"],
            "sharpe": artifact["full_period"]["sharpe"],
            "sortino": artifact["full_period"]["sortino"],
            "calmar": artifact["full_period"]["calmar"],
            "max_drawdown": artifact["full_period"]["max_drawdown"],
            "max_drawdown_duration_days": artifact["full_period"]["max_drawdown_duration_days"],
            "avg_exposure": artifact["full_period"]["avg_exposure"],
            "avg_holdings": artifact["full_period"]["avg_holdings"],
            "turnover": artifact["full_period"]["turnover"],
            "fees_pct_gross_pnl": artifact["full_period"]["fees_pct_gross_pnl"],
            "median_fold_return": artifact["walk_forward"]["median_fold_return"],
            "fold_wins_vs_control": None,
            "recent_return": artifact["recent_period"]["return"],
            "recent_sharpe": artifact["recent_period"]["sharpe"],
            "recent_max_drawdown": artifact["recent_period"]["max_drawdown"],
            "concentration_top1_share": artifact["concentration"]["top1_share_of_positive_pnl"],
        }
        for candidate_id, artifact in all_artifacts.items()
    }

    # fold_deltas.json — paired candidate-vs-control fold deltas.
    control_folds = {
        fold["start"]: fold for fold in all_artifacts["control"]["walk_forward"]["folds"]
    }
    fold_deltas: dict[str, object] = {}
    for candidate_id, artifact in all_artifacts.items():
        if candidate_id == "control":
            continue
        deltas_rows = []
        d_returns: list[float] = []
        d_sharpes: list[float] = []
        d_dds: list[float] = []
        for fold in artifact["walk_forward"]["folds"]:
            control_fold = control_folds[fold["start"]]
            delta_return = fold["return"] - control_fold["return"]
            delta_sharpe = fold["sharpe"] - control_fold["sharpe"]
            delta_dd = fold["max_drawdown"] - control_fold["max_drawdown"]
            deltas_rows.append(
                {
                    "start": fold["start"],
                    "end": fold["end"],
                    "candidate_return": fold["return"],
                    "control_return": control_fold["return"],
                    "candidate_sharpe": fold["sharpe"],
                    "control_sharpe": control_fold["sharpe"],
                    "candidate_maxDD": fold["max_drawdown"],
                    "control_maxDD": control_fold["max_drawdown"],
                    "return_delta": round(delta_return, 6),
                    "sharpe_delta": round(delta_sharpe, 6),
                    "maxDD_delta": round(delta_dd, 6),
                }
            )
            d_returns.append(delta_return)
            d_sharpes.append(delta_sharpe)
            d_dds.append(delta_dd)
        fold_deltas[candidate_id] = {
            "fold_count": len(deltas_rows),
            "deltas": deltas_rows,
            "return_delta": {
                "median": round(statistics.median(d_returns), 6) if d_returns else 0.0,
                "mean": round(statistics.fmean(d_returns), 6) if d_returns else 0.0,
                "stdev": round(statistics.pstdev(d_returns), 6) if d_returns else 0.0,
                "p25": _percentile(d_returns, 0.25),
                "wins_over_control": sum(1 for value in d_returns if value > 0),
            },
            "sharpe_delta": {
                "median": round(statistics.median(d_sharpes), 6) if d_sharpes else 0.0,
                "mean": round(statistics.fmean(d_sharpes), 6) if d_sharpes else 0.0,
                "wins_over_control": sum(1 for value in d_sharpes if value > 0),
            },
            "maxDD_delta": {
                "median": round(statistics.median(d_dds), 6) if d_dds else 0.0,
                "mean": round(statistics.fmean(d_dds), 6) if d_dds else 0.0,
                "wins_over_control": sum(1 for value in d_dds if value < 0),
            },
        }

    # recent_period.json / annual_results.json / cost_stress.json / jackknife.json
    recent_period = {
        candidate_id: artifact["recent_period"] for candidate_id, artifact in all_artifacts.items()
    }
    annual_results = {
        "years": sorted(
            {
                int(year)
                for artifact in all_artifacts.values()
                for year in artifact["annual_windows"]
            }
        ),
        "candidates": {
            candidate_id: artifact["annual_windows"]
            for candidate_id, artifact in all_artifacts.items()
        },
    }
    cost_stress = {
        candidate_id: artifact["cost_stress"] for candidate_id, artifact in all_artifacts.items()
    }
    jackknife = {
        candidate_id: artifact["jackknife"] for candidate_id, artifact in all_artifacts.items()
    }
    regime_analysis = {
        candidate_id: artifact["regime"] for candidate_id, artifact in all_artifacts.items()
    }

    # bootstrap.json — block-bootstrap deltas vs the CONTROL daily curve.
    control_curve = all_artifacts["control"]["full_equity_curve"]
    bootstrap: dict[str, object] = {
        "method": "block bootstrap, 20-day blocks, with replacement, vs CONTROL",
        "seed": BOOTSTRAP_SEED,
        "block": BOOTSTRAP_BLOCK,
        "resamples": BOOTSTRAP_RESAMPLES,
        "deltas": {},
    }
    for candidate_id, artifact in all_artifacts.items():
        if candidate_id == "control":
            continue
        bootstrap["deltas"][candidate_id] = block_bootstrap_deltas(
            control_curve, artifact["full_equity_curve"]
        )

    # summary fold-wins from fold_deltas.
    for candidate_id, summary_row in summary.items():
        fold_entry = fold_deltas.get(candidate_id)
        if fold_entry is not None:
            summary_row["fold_wins_vs_control"] = {
                "return_wins": fold_entry["return_delta"]["wins_over_control"],
                "sharpe_wins": fold_entry["sharpe_delta"]["wins_over_control"],
                "maxDD_wins": fold_entry["maxDD_delta"]["wins_over_control"],
            }

    json.dump(summary, open(OUT_DIR / "summary.json", "w"), indent=1)
    json.dump(fold_deltas, open(OUT_DIR / "fold_deltas.json", "w"), indent=1)
    json.dump(recent_period, open(OUT_DIR / "recent_period.json", "w"), indent=1)
    json.dump(annual_results, open(OUT_DIR / "annual_results.json", "w"), indent=1)
    json.dump(cost_stress, open(OUT_DIR / "cost_stress.json", "w"), indent=1)
    json.dump(jackknife, open(OUT_DIR / "jackknife.json", "w"), indent=1)
    json.dump(regime_analysis, open(OUT_DIR / "regime_analysis.json", "w"), indent=1)
    json.dump(bootstrap, open(OUT_DIR / "bootstrap.json", "w"), indent=1)
    for name in (
        "summary.json",
        "fold_deltas.json",
        "recent_period.json",
        "annual_results.json",
        "cost_stress.json",
        "jackknife.json",
        "regime_analysis.json",
        "bootstrap.json",
    ):
        path = OUT_DIR / name
        path.write_text(json.dumps(json.loads(path.read_text()), indent=1) + "\n")
    print("aggregate artifacts written to", OUT_DIR)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variants",
        default=",".join(VARIANTS),
        help="comma-separated subset of: " + ",".join(VARIANTS),
    )
    parser.add_argument("--aggregate", action="store_true", help="write aggregate artifacts")
    parser.add_argument("--skip-jackknife", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--refresh-manifest-hashes",
        action="store_true",
        help="write sha256 of generated artifacts into the manifest",
    )
    args = parser.parse_args()
    manifest = load_and_validate_manifest()

    if args.aggregate:
        return aggregate()

    requested = [item.strip() for item in args.variants.split(",") if item.strip()]
    unknown = [item for item in requested if item not in VARIANTS]
    if unknown:
        raise SystemExit(f"unknown variants: {unknown}")
    missed = [item for item in requested if item not in manifest.get("variants", {})]
    if missed:
        raise SystemExit(f"variants absent from the predeclaration: {missed}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    print("loading dataset...", flush=True)
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    actions = load_csv_corporate_actions(DIVIDENDS_CSV)
    print(f"{len(bars)} bars, {len(actions)} dividend actions", flush=True)
    print("building PIT universe...", flush=True)
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EVAL_END)
    spy_daily = spy_daily_regimes(bars)
    print(f"universe ready; {len(spy_daily)} SPY days classified", flush=True)

    # CONTROL's top-5 contributors define the shared jackknife perturbation.
    control_top: list[str] | None = None
    control_artifact_path = OUT_DIR / ("control.json" if not args.smoke else "control_smoke.json")
    if args.skip_jackknife:
        control_top = None
    elif control_artifact_path.exists():
        payload = json.loads(control_artifact_path.read_text())
        control_top = list(payload["concentration"]["top_symbols"])[:JACKKNIFE_TOP]
    elif "control" not in requested:
        raise SystemExit(
            "control artifact missing; run --variants control first (or --skip-jackknife)"
        )

    for variant_id in requested:
        started = datetime.now(UTC)
        print(f"=== {variant_id} ===", flush=True)
        if control_top is None and not args.skip_jackknife and variant_id == "control":
            probe, _strategy = run_window(
                config,
                variant_id,
                dict(_FROZEN),
                universe,
                bars,
                start=None,
                end=None,
                actions=actions,
            )
            control_top = list(concentration(probe)["top_symbols"])[:JACKKNIFE_TOP]
        artifact = run_variant(
            variant_id,
            config,
            bars,
            universe,
            actions,
            smoke=args.smoke,
            skip_jackknife=args.skip_jackknife,
            control_top=control_top,
            spy_daily=spy_daily,
        )
        artifact["identity"] = {
            "mission": manifest["mission"],
            "research_commit": git_head(),
            "python_version": platform.python_version(),
            "dataset": dataset_identity(),
            "config_digest_sha256": config_digest(config),
            "generated_at": datetime.now(UTC).isoformat(),
            "eval_start": EVAL_START.date().isoformat(),
            "eval_end": EVAL_END.date().isoformat(),
            "recent_start": RECENT_START.date().isoformat(),
            "fold_step_months": FOLD_STEP_MONTHS,
            "schema_version": RESULT_SCHEMA_VERSION,
            "survivorship_bias_limitation": True,
        }
        suffix = "_smoke" if args.smoke else ""
        path = OUT_DIR / f"{variant_id}{suffix}.json"
        path.write_text(json.dumps(artifact, indent=1))
        summary = artifact["full_period"]
        print(
            f"{variant_id}: return={summary['return']:+.4f} sharpe={summary['sharpe']:.3f} "
            f"maxDD={summary['max_drawdown']:.4f} "
            f"medianFold={artifact['walk_forward']['median_fold_return']:+.4f} "
            f"({(datetime.now(UTC) - started).total_seconds():.0f}s) -> {path.name}",
            flush=True,
        )
    if args.refresh_manifest_hashes and not args.smoke:
        results: dict[str, str] = {}
        for variant_id in VARIANTS:
            path = OUT_DIR / f"{variant_id}.json"
            if path.exists():
                results[f"{variant_id}.json"] = sha256_of_file(path)
        for name in (
            "summary.json",
            "fold_deltas.json",
            "recent_period.json",
            "annual_results.json",
            "cost_stress.json",
            "jackknife.json",
            "regime_analysis.json",
            "bootstrap.json",
        ):
            path = OUT_DIR / name
            if path.exists():
                results[name] = sha256_of_file(path)
        manifest = json.loads(MANIFEST_PATH.read_text())
        manifest["results_sha256"] = results
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=1) + "\n")
        print(f"manifest hashes refreshed ({len(results)} artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
