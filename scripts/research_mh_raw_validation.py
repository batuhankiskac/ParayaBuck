#!/usr/bin/env python3
"""MH_RAW promotion validation runner — mission 2026-08.

Validates EXACTLY two strategies against each other:

* CONTROL   — production ``equity_residual_momentum_v2`` (weekly), the
              unchanged production semantics;
* MH_RAW    — ``equity_residual_momentum_v3``: positive 12-1 residual
              momentum gate + 6-1/9-1/12-1 percentile-rank consensus.

The committed predeclaration manifest
(``data/research_manifests/equity_mh_raw_promotion_2026_08.json``) is
authoritative: the runner refuses to execute when the strategy identities,
frozen parameters, fold boundaries or evaluation windows drift from it, and
every generated artifact records the running commit as both
``implementation_commit`` and ``research_commit``.

Per-strategy work: full window, exact 20 calendar 6-month walk-forward folds,
annual windows, 2022+ recent window, cost stress 1x/2x/3x/5x,
CANDIDATE-SPECIFIC jackknife (own top-1..top-5 positive-PnL contributors,
own top-3 combined, plus the shared CONTROL-top-3 cross perturbation for
MH_RAW), block bootstrap vs CONTROL, regime decomposition, and the
predeclared temporal sensitivity windows (2017-2021, 2022-2026, 2023-2026,
excluding 2020, excluding 2024).

Usage:
    python scripts/research_mh_raw_validation.py --variants control,mh_raw
    python scripts/research_mh_raw_validation.py --smoke
    python scripts/research_mh_raw_validation.py --aggregate
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from research_helpers import config_digest, dataset_identity, git_head, sha256_of_file

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
from parayabuck.portfolio import (
    EquityResidualMomentum1dV2,
    EquityResidualMomentum1dV3,
    rebalance_policy_for,
)
from parayabuck.portfolio.research_alpha import ELEVATED, NORMAL, spy_regime_state
from parayabuck.validation import (
    FOLD_COUNT_DECLARED,
    assert_fold_contract,
    calendar_fold_windows,
    paired_fold_deltas,
    promotion_gates,
    temporal_diagnostic_windows,
)

REPO = Path(__file__).resolve().parents[1]
EQUITY_CSV = REPO / "data/historical/equity_1200_2016_2026.csv"
DIVIDENDS_CSV = REPO / "data/historical/equity_1200_dividends.csv"
OUT_DIR = REPO / "data/research/mh_raw_promotion_2026_08"
MANIFEST_PATH = REPO / "data/research_manifests/equity_mh_raw_promotion_2026_08.json"
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

# The validated identities. CONTROL is the production v2 class; MH_RAW is the
# v3 production-capable class with native (research-independent) consensus
# math. Reference-only candidates (A3, CONTROL_HYST) are NOT rerun here —
# their prior research artifacts exist under the old fold contract and are
# quoted in the report as reference-only, never for promotion.
VARIANTS: dict[str, tuple[type[EquityResidualMomentum1dV2], dict[str, object]]] = {
    "control": (EquityResidualMomentum1dV2, {}),
    "mh_raw": (EquityResidualMomentum1dV3, {}),
}


# --------------------------------------------------------------------------- #
# Manifest validation
# --------------------------------------------------------------------------- #


def _normalize_parameters(parameters: dict[str, object]) -> dict[str, object]:
    return {
        key: (str(value) if isinstance(value, list) else value) for key, value in parameters.items()
    }


def load_and_validate_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise SystemExit(
            f"predeclaration manifest missing: {MANIFEST_PATH} (commit it; "
            "validation without a predeclared contract is not runnable here)"
        )
    manifest = json.loads(MANIFEST_PATH.read_text())

    declared_variants = manifest.get("variants", {})
    built_in = {
        variant_id: {"base": cls.__name__} for variant_id, (cls, _overrides) in VARIANTS.items()
    }
    normalized = {
        variant_id: {"base": spec["base"]} for variant_id, spec in declared_variants.items()
    }
    if normalized != built_in:
        drift = {
            key
            for key in set(normalized) | set(built_in)
            if normalized.get(key) != built_in.get(key)
        }
        raise SystemExit(f"VARIANTS drifted from the committed predeclaration: {sorted(drift)}")

    frozen = manifest.get("frozen_parameters", {})
    if _normalize_parameters(frozen) != _normalize_parameters(_FROZEN):
        drift = {key for key in set(frozen) | set(_FROZEN) if frozen.get(key) != _FROZEN.get(key)}
        raise SystemExit(f"frozen parameters drifted from the manifest: {sorted(drift)}")

    # Fold contract: the manifest predeclares exact boundaries; the code must
    # compute the same 20 calendar folds or abort.
    declared_folds = manifest.get("walk_forward", {}).get("folds_exact")
    if not declared_folds or not isinstance(declared_folds, list):
        raise SystemExit("manifest walk_forward.folds_exact missing")
    computed = calendar_fold_windows(EVAL_START, EVAL_END, step_months=FOLD_STEP_MONTHS)
    if len(computed) != FOLD_COUNT_DECLARED:
        raise SystemExit(
            f"fold contract violated: expected exactly {FOLD_COUNT_DECLARED} folds, "
            f"computed {len(computed)}"
        )
    declared_parsed = [
        (datetime.fromisoformat(pair[0]), datetime.fromisoformat(pair[1]))
        for pair in declared_folds
    ]
    assert_fold_contract(declared_parsed, computed)

    declared_temporal = manifest.get("temporal_windows", {})
    if set(declared_temporal) != set(
        temporal_diagnostic_windows(EVAL_START, EVAL_END, RECENT_START)
    ):
        raise SystemExit("temporal_windows drifted from the committed predeclaration")
    return manifest


# --------------------------------------------------------------------------- #
# Engine / window helpers
# --------------------------------------------------------------------------- #


def make_engine(
    config: Config,
    strategy: EquityResidualMomentum1dV2,
    universe: PointInTimeUniverse,
    *,
    cost_multiplier: float = 1.0,
    starting_equity: float | None = None,
) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=starting_equity or config.backtest.initial_cash,
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


def build_strategy(variant_id: str) -> EquityResidualMomentum1dV2:
    cls, overrides = VARIANTS[variant_id]
    merged = dict(_FROZEN)
    merged.update(overrides)
    return cls(merged)


def run_window(
    config: Config,
    variant_id: str,
    universe: PointInTimeUniverse,
    bars: list[Bar],
    *,
    start: datetime | None,
    end: datetime | None,
    cost_multiplier: float = 1.0,
    actions: list[CorporateAction] | None = None,
    starting_equity: float | None = None,
) -> tuple[PortfolioBacktestResult, EquityResidualMomentum1dV2]:
    strategy = build_strategy(variant_id)
    available = [
        bar for bar in bars if bar.market == Market.EQUITY and bar.timeframe == strategy.timeframe
    ]
    if end is not None:
        available = [bar for bar in available if bar.timestamp <= end]
    filtered = available if start is None else [bar for bar in available if bar.timestamp >= start]
    warmup = [] if start is None else [bar for bar in available if bar.timestamp < start]
    engine = make_engine(
        config, strategy, universe, cost_multiplier=cost_multiplier, starting_equity=starting_equity
    )
    return engine.run(filtered, warmup=warmup, corporate_actions=actions), strategy


def run_segments(
    config: Config,
    variant_id: str,
    universe: PointInTimeUniverse,
    bars: list[Bar],
    segments: list[tuple[datetime, datetime]],
    *,
    actions: list[CorporateAction] | None,
) -> PortfolioBacktestResult:
    """Run a multi-segment window as a CHAINED backtest.

    Every segment after the first starts its own fresh engine seeded with
    the previous segment's ending equity and warms up on ALL bars before its
    start (the excluded segment between the two is never traded). Returns the
    final segment's result; the reported return is relative to the original
    starting equity.
    """
    result: PortfolioBacktestResult | None = None
    for index, (start, end) in enumerate(segments):
        result, _strategy = run_window(
            config,
            variant_id,
            universe,
            bars,
            start=start,
            end=end,
            actions=actions,
            starting_equity=result.ending_equity if result is not None else None,
        )
    assert result is not None
    return result


# --------------------------------------------------------------------------- #
# Post-run statistics (ported from the phase-2 harness, unchanged math)
# --------------------------------------------------------------------------- #


def _profit_factor(result: PortfolioBacktestResult) -> float:
    gains = sum(pnl for pnl in result.realized_pnls if pnl > 0)
    losses = -sum(pnl for pnl in result.realized_pnls if pnl < 0)
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return round(gains / losses, 4)


def rebalance_stats(result: PortfolioBacktestResult) -> dict[str, object]:
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
# Regime decomposition / bootstrap (ported unchanged from phase 2)
# --------------------------------------------------------------------------- #


def spy_daily_regimes(bars: list[Bar]) -> dict[datetime, str]:
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
    own_top: list[str],
    control_top: list[str] | None,
    spy_daily: dict[datetime, str],
) -> dict[str, object]:
    folds = calendar_fold_windows(EVAL_START, EVAL_END, step_months=FOLD_STEP_MONTHS)
    eval_start = EVAL_START
    eval_end = EVAL_END
    if smoke:
        eval_start = datetime(2024, 1, 2, tzinfo=UTC)
        folds = folds[-2:]

    full, full_strategy = run_window(
        config, variant_id, universe, bars, start=None, end=None, actions=actions
    )

    # Walk-forward folds (anchored: everything before each fold is warmup).
    fold_results: list[tuple[datetime, datetime, PortfolioBacktestResult]] = []
    for fold_start, fold_end in folds:
        if fold_start < eval_start or fold_end > eval_end:
            continue
        result, _strategy = run_window(
            config,
            variant_id,
            universe,
            bars,
            start=fold_start,
            end=fold_end,
            actions=actions,
        )
        fold_results.append((fold_start, fold_end, result))

    # Annual calendar-year windows (warmup = all prior bars, anchored).
    annual: dict[str, object] = {}
    for year in range(EVAL_START.year, EVAL_END.year + 1):
        year_start = datetime(year, 1, 1, tzinfo=UTC)
        year_end = min(datetime(year + 1, 1, 1, tzinfo=UTC), EVAL_END)
        if year_end <= eval_start:
            continue
        result, _strategy = run_window(
            config,
            variant_id,
            universe,
            bars,
            start=year_start,
            end=year_end,
            actions=actions,
        )
        annual[str(year)] = {
            "return": round(result.net_return, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
            "turnover": round(result.turnover, 6),
            "sharpe": round(result.sharpe, 6),
        }

    # Recent window (2022+).
    recent, _strategy = run_window(
        config, variant_id, universe, bars, start=RECENT_START, end=None, actions=actions
    )

    # Cost stress: 1x is the full run above; 2x/3x/5x re-run the window.
    stress: dict[str, object] = {"1x": full_stats(full)}
    if not smoke:
        for multiplier in (2.0, 3.0, 5.0):
            stressed, _strategy = run_window(
                config,
                variant_id,
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

    # CANDIDATE-SPECIFIC jackknife: this strategy's OWN top-5 positive-PnL
    # contributors, each dropped alone, plus the own top-3 combined. The
    # shared CONTROL-top-3 perturbation is a labelled secondary
    # cross-strategy test (kept from the phase-2 harness for continuity).
    jackknife: dict[str, object] = {}
    if not smoke:
        for symbol in own_top[:JACKKNIFE_TOP]:
            reduced = universe_without(universe, frozenset({symbol}))
            result, _strategy = run_window(
                config,
                variant_id,
                reduced,
                bars,
                start=None,
                end=None,
                actions=actions,
            )
            jackknife[f"drop_own_{symbol}"] = {
                "return": round(result.net_return, 6),
                "sharpe": round(result.sharpe, 6),
                "max_drawdown": round(result.max_drawdown, 6),
            }
        combined = frozenset(own_top[:3])
        reduced_combined = universe_without(universe, combined)
        result, _strategy = run_window(
            config,
            variant_id,
            reduced_combined,
            bars,
            start=None,
            end=None,
            actions=actions,
        )
        jackknife["drop_own_top3_combined"] = {
            "return": round(result.net_return, 6),
            "sharpe": round(result.sharpe, 6),
            "max_drawdown": round(result.max_drawdown, 6),
        }
        if control_top is not None:
            reduced_shared = universe_without(universe, frozenset(control_top[:3]))
            result, _strategy = run_window(
                config,
                variant_id,
                reduced_shared,
                bars,
                start=None,
                end=None,
                actions=actions,
            )
            jackknife["drop_control_top3_combined_secondary"] = {
                "return": round(result.net_return, 6),
                "sharpe": round(result.sharpe, 6),
                "max_drawdown": round(result.max_drawdown, 6),
            }

    # Regime: diagnostic decomposition of the full-window curve.
    regime: dict[str, object] = {
        **regime_pnl(full, spy_daily),
        "regime_daily_timeline": regime_timeline(spy_daily),
    }

    # Temporal sensitivity windows (diagnostics only; never optimized).
    temporal: dict[str, object] = {}
    if not smoke:
        for name, segments in temporal_diagnostic_windows(
            EVAL_START, EVAL_END, RECENT_START
        ).items():
            result = run_segments(config, variant_id, universe, bars, segments, actions=actions)
            temporal[name] = {
                "return": round(result.net_return, 6),
                "sharpe": round(result.sharpe, 6),
                "max_drawdown": round(result.max_drawdown, 6),
                "cagr": round(result.cagr, 6),
                "segments": [[s.date().isoformat(), e.date().isoformat()] for s, e in segments],
            }

    artifact = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "variant": variant_id,
        "strategy_name": VARIANTS[variant_id][0].__name__,
        "parameters": {
            key: str(value) if isinstance(value, list) else value
            for key, value in sorted(_FROZEN.items())
        },
        "semantics": {
            "positive_12m_gate": True,
            "multi_horizon_rank_consensus": True,
            "horizons_residual_bars": [126, 189, 252],
            "horizon_labels": ["6-1", "9-1", "12-1"],
            "skip_bars": 21,
            "standardized": False,
            "consensus": "equal mean of per-horizon percentile ranks",
            "note": (
                "MH_RAW = positive 12-1 residual momentum + multi-horizon rank "
                "consensus, NOT an unrestricted 6/9/12 cross-section. The "
                "positive-12m gate (inherited production 12-1 score > 0) runs "
                "BEFORE the consensus in _score_candidates."
            ),
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
        "temporal_windows": temporal,
    }
    return artifact


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
    control = load_variant_artifact("control")
    mh_raw = load_variant_artifact("mh_raw")

    # summary.json — full-period headline table for the two validated strategies.
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
            "recent_return": artifact["recent_period"]["return"],
            "recent_sharpe": artifact["recent_period"]["sharpe"],
            "recent_max_drawdown": artifact["recent_period"]["max_drawdown"],
            "concentration_top1_share": artifact["concentration"]["top1_share_of_positive_pnl"],
        }
        for candidate_id, artifact in {"control": control, "mh_raw": mh_raw}.items()
    }

    # fold_deltas.json — ONE canonical paired comparison via the contract module.
    fold_deltas = {
        "mh_raw": paired_fold_deltas(
            control["walk_forward"]["folds"], mh_raw["walk_forward"]["folds"]
        )
    }
    for candidate_id, summary_row in summary.items():
        fold_entry = fold_deltas.get(candidate_id)
        if fold_entry is not None:
            summary_row["fold_wins_vs_control"] = {
                "return_wins": fold_entry["return_delta"]["wins_over_control"],
                "sharpe_wins": fold_entry["sharpe_delta"]["wins_over_control"],
                "maxDD_wins": fold_entry["maxDD_delta"]["wins_over_control"],
            }

    # recent_period / annual / cost_stress / jackknife / regime / temporal.
    recent_period = {
        candidate_id: artifact["recent_period"]
        for candidate_id, artifact in {"control": control, "mh_raw": mh_raw}.items()
    }
    annual_results = {
        "years": sorted(
            {int(year) for artifact in (control, mh_raw) for year in artifact["annual_windows"]}
        ),
        "candidates": {
            candidate_id: artifact["annual_windows"]
            for candidate_id, artifact in {"control": control, "mh_raw": mh_raw}.items()
        },
    }
    cost_stress = {
        candidate_id: artifact["cost_stress"]
        for candidate_id, artifact in {"control": control, "mh_raw": mh_raw}.items()
    }
    jackknife = {
        candidate_id: artifact["jackknife"]
        for candidate_id, artifact in {"control": control, "mh_raw": mh_raw}.items()
    }
    regime_analysis = {
        candidate_id: artifact["regime"]
        for candidate_id, artifact in {"control": control, "mh_raw": mh_raw}.items()
    }
    temporal_windows = {
        candidate_id: artifact["temporal_windows"]
        for candidate_id, artifact in {"control": control, "mh_raw": mh_raw}.items()
    }

    # bootstrap.json — block-bootstrap deltas vs the CONTROL daily curve.
    control_curve = control["full_equity_curve"]
    bootstrap: dict[str, object] = {
        "method": "block bootstrap, 20-day blocks, with replacement, vs CONTROL",
        "seed": BOOTSTRAP_SEED,
        "block": BOOTSTRAP_BLOCK,
        "resamples": BOOTSTRAP_RESAMPLES,
        "deltas": {
            "mh_raw": block_bootstrap_deltas(control_curve, mh_raw["full_equity_curve"]),
        },
    }

    # promotion_gate.json — the §9 rubric evaluated from the numbers above.
    control_yearly = control["yearly_returns"]
    mh_yearly = mh_raw["yearly_returns"]
    yearly_wins = sum(
        1 for year in mh_yearly if year in control_yearly and mh_yearly[year] > control_yearly[year]
    )
    yearly_years = len([year for year in mh_yearly if year in control_yearly])

    def _minimal(artifact: dict, *, exclude_2020: dict, exclude_2024: dict) -> dict[str, object]:
        return {
            "full_period": artifact["full_period"],
            "recent_period": artifact["recent_period"],
            "cost_stress": artifact["cost_stress"],
            "own_top3_drop": artifact["jackknife"].get("drop_own_top3_combined", {}),
            "yearly_wins": yearly_wins,
            "yearly_years": yearly_years,
            "exclude_2020": exclude_2020,
            "exclude_2024": exclude_2024,
            "fold_deltas": fold_deltas.get(artifact["variant"]),
        }

    control_min = _minimal(
        control,
        exclude_2020=control["temporal_windows"]["exclude_2020"],
        exclude_2024=control["temporal_windows"]["exclude_2024"],
    )
    mh_min = _minimal(
        mh_raw,
        exclude_2020=mh_raw["temporal_windows"]["exclude_2020"],
        exclude_2024=mh_raw["temporal_windows"]["exclude_2024"],
    )
    gates = {
        "rubric": manifest["promotion_rubric"],
        "gates": promotion_gates(control=control_min, candidate=mh_min),
        "passed_all": all(
            item["passed"] for item in promotion_gates(control=control_min, candidate=mh_min)
        ),
    }

    json.dump(summary, open(OUT_DIR / "summary.json", "w"), indent=1)
    json.dump(fold_deltas, open(OUT_DIR / "fold_deltas.json", "w"), indent=1)
    json.dump(recent_period, open(OUT_DIR / "recent_period.json", "w"), indent=1)
    json.dump(annual_results, open(OUT_DIR / "annual_results.json", "w"), indent=1)
    json.dump(cost_stress, open(OUT_DIR / "cost_stress.json", "w"), indent=1)
    json.dump(jackknife, open(OUT_DIR / "jackknife.json", "w"), indent=1)
    json.dump(regime_analysis, open(OUT_DIR / "regime_analysis.json", "w"), indent=1)
    json.dump(temporal_windows, open(OUT_DIR / "temporal_windows.json", "w"), indent=1)
    json.dump(bootstrap, open(OUT_DIR / "bootstrap.json", "w"), indent=1)
    json.dump(gates, open(OUT_DIR / "promotion_gate.json", "w"), indent=1)
    for name in (
        "summary.json",
        "fold_deltas.json",
        "recent_period.json",
        "annual_results.json",
        "cost_stress.json",
        "jackknife.json",
        "regime_analysis.json",
        "temporal_windows.json",
        "bootstrap.json",
        "promotion_gate.json",
    ):
        path = OUT_DIR / name
        path.write_text(json.dumps(json.loads(path.read_text()), indent=1) + "\n")
    print("aggregate artifacts written to", OUT_DIR)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variants",
        default="control,mh_raw",
        help="comma-separated subset of: control,mh_raw",
    )
    parser.add_argument("--aggregate", action="store_true", help="write aggregate artifacts")
    parser.add_argument(
        "--smoke", action="store_true", help="short smoke run (no jackknife/stress)"
    )
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
    # Assert the dataset against the manifest so a silent data swap aborts.
    declared_bars = manifest["dataset"]["bars_sha256"]
    declared_dividends = manifest["dataset"]["dividends_sha256"]
    actual_bars = sha256_of_file(EQUITY_CSV)
    actual_dividends = sha256_of_file(DIVIDENDS_CSV)
    if actual_bars != declared_bars or actual_dividends != declared_dividends:
        raise SystemExit(
            "dataset drifted from the predeclaration: "
            f"bars {actual_bars[:12]} vs declared {declared_bars[:12]}, "
            f"dividends {actual_dividends[:12]} vs declared {declared_dividends[:12]}"
        )
    print("building PIT universe...", flush=True)
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EVAL_END)
    spy_daily = spy_daily_regimes(bars)
    print(f"universe ready; {len(spy_daily)} SPY days classified", flush=True)

    own_top: dict[str, list[str]] = {}
    control_top: list[str] | None = None
    control_artifact_path = OUT_DIR / "control.json"
    for variant_id in requested:
        # Resume-friendly: reuse an existing artifact's concentration (the
        # full run is long; rerunning CONTROL for its own-top list alone
        # would double the wall time). The probe is deterministic, so an
        # artifact produced by the SAME commit is bit-identical to a fresh
        # probe — and the jackknife runs full windows anyway.
        existing = OUT_DIR / f"{variant_id}.json"
        if variant_id not in own_top:
            if existing.exists():
                payload = json.loads(existing.read_text())
                own_top[variant_id] = list(payload["concentration"]["top_symbols"])[:JACKKNIFE_TOP]
            else:
                probe, _strategy = run_window(
                    config, variant_id, universe, bars, start=None, end=None, actions=actions
                )
                own_top[variant_id] = list(concentration(probe)["top_symbols"])[:JACKKNIFE_TOP]
        if variant_id == "control":
            control_top = own_top["control"]
        elif control_top is None and control_artifact_path.exists():
            # Secondary cross-strategy perturbation (drop CONTROL top-3) for
            # MH_RAW when CONTROL is not part of this run.
            payload = json.loads(control_artifact_path.read_text())
            control_top = list(payload["concentration"]["top_symbols"])[:JACKKNIFE_TOP]

    for variant_id in requested:
        started = datetime.now(UTC)
        print(f"=== {variant_id} ===", flush=True)
        artifact = run_variant(
            variant_id,
            config,
            bars,
            universe,
            actions,
            smoke=args.smoke,
            own_top=own_top[variant_id],
            control_top=control_top if variant_id != "control" else None,
            spy_daily=spy_daily,
        )
        artifact["identity"] = {
            "mission": manifest["mission"],
            # COMMIT A contract: the checkout running the validation IS the
            # implementation commit; both fields record it (fixes the phase-2
            # provenance gap where the manifest pointed at 3c5e57d while the
            # harness lived in 81b4fac).
            "implementation_commit": git_head(),
            "research_commit": git_head(),
            "python_version": platform.python_version(),
            "dataset": dataset_identity(),
            "config_digest_sha256": config_digest(config),
            "generated_at": datetime.now(UTC).isoformat(),
            "eval_start": EVAL_START.date().isoformat(),
            "eval_end": EVAL_END.date().isoformat(),
            "recent_start": RECENT_START.date().isoformat(),
            "fold_step_months": FOLD_STEP_MONTHS,
            "fold_count_declared": FOLD_COUNT_DECLARED,
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
            "temporal_windows.json",
            "bootstrap.json",
            "promotion_gate.json",
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
