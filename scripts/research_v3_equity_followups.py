#!/usr/bin/env python3
"""Research-only headline checks for the post-roadmap M5-M11 candidates.

This module deliberately lives outside ``research_v3_equity_roadmap.py`` and
does not register any candidate in the product strategy registry.  It reuses
that runner's canonical symmetric-PIT-v5/T1 preparation and records hashes of
all imported research inputs so a dirty-tree execution is still attributable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import statistics
import subprocess
import sys
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SCRIPTS = REPO / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_v3_equity_roadmap as roadmap  # noqa: E402

from parayabuck.backtest.universe import (  # noqa: E402
    PointInTimeUniverse,
    UniverseSnapshot,
    hash_point_in_time_universe,
)
from parayabuck.config import Config, load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext  # noqa: E402
from parayabuck.portfolio.equity_residual_momentum import _ols_beta  # noqa: E402
from parayabuck.portfolio.equity_residual_momentum_v3 import (  # noqa: E402
    _compounded,
    multi_horizon_consensus,
)

MANIFEST = REPO / "data/research_manifests/equity_v3_followups_2026_08.json"
OUT_DIR = REPO / "data/research/equity_v3_followups_2026_08"
OUT_PATH = OUT_DIR / "headline_results.json"

COVARIANCE_SESSIONS = 126
CLUSTER_SHORTLIST = 20
CLUSTER_THRESHOLD = 0.65
DISPERSION_LOW = 0.05
DISPERSION_HIGH = 0.10
ETF_PANEL = ("SPY", "QQQ", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def _git_dirty() -> list[str]:
    output = subprocess.check_output(["git", "status", "--short"], cwd=REPO, text=True)
    return [line for line in output.splitlines() if line]


def _returns_by_timestamp(context: PortfolioContext, symbol: str, beta_window: int = 273) -> dict[datetime, float]:
    stock = {bar.timestamp: float(bar.close) for bar in context.history.get(symbol, ()) if bar.close > 0}
    benchmark = {
        bar.timestamp: float(bar.close) for bar in context.history.get(context.benchmark_symbol, ()) if bar.close > 0
    }
    common = sorted(set(stock).intersection(benchmark))[-(beta_window + 1) :]
    if len(common) < beta_window + 1:
        return {}
    stock_returns = [stock[right] / stock[left] - 1.0 for left, right in zip(common, common[1:])]
    market_returns = [benchmark[right] / benchmark[left] - 1.0 for left, right in zip(common, common[1:])]
    beta = _ols_beta(stock_returns, market_returns)
    return {
        timestamp: stock_return - beta * market_return
        for timestamp, stock_return, market_return in zip(common[1:], stock_returns, market_returns, strict=True)
    }


def residual_matrix(
    context: PortfolioContext,
    symbols: Sequence[str],
    sessions: int = COVARIANCE_SESSIONS,
) -> np.ndarray | None:
    series = {symbol: _returns_by_timestamp(context, symbol) for symbol in symbols}
    if any(not values for values in series.values()):
        return None
    common = sorted(set.intersection(*(set(values) for values in series.values())))
    if len(common) < sessions:
        return None
    timestamps = common[-sessions:]
    matrix = np.asarray([[series[symbol][timestamp] for symbol in symbols] for timestamp in timestamps], dtype=float)
    if matrix.shape != (sessions, len(symbols)) or not np.all(np.isfinite(matrix)):
        return None
    return matrix


def constant_correlation_shrinkage(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Shrink sample covariance toward its constant-correlation target.

    The intensity is the bounded ratio of estimated covariance-entry sampling
    variance to squared sample/target distance.  This is deterministic and
    data-estimated; no dependency or parameter grid is introduced.
    """
    if returns.ndim != 2 or returns.shape[0] < 3 or returns.shape[1] < 2:
        raise ValueError("shrinkage requires at least 3 observations and 2 assets")
    centered = returns - returns.mean(axis=0, keepdims=True)
    observations = centered.shape[0]
    sample = centered.T @ centered / observations
    variances = np.clip(np.diag(sample), 1e-12, None)
    scales = np.sqrt(variances)
    correlation = sample / np.outer(scales, scales)
    off_diagonal = correlation[np.triu_indices_from(correlation, 1)]
    mean_correlation = float(np.clip(off_diagonal.mean(), -0.99, 0.99))
    target = mean_correlation * np.outer(scales, scales)
    np.fill_diagonal(target, variances)
    products = centered[:, :, None] * centered[:, None, :]
    entry_variance = np.mean((products - sample) ** 2, axis=0) / observations
    numerator = float(entry_variance.sum())
    denominator = float(((sample - target) ** 2).sum())
    intensity = 1.0 if denominator <= 1e-24 else min(1.0, max(0.0, numerator / denominator))
    covariance = intensity * target + (1.0 - intensity) * sample
    covariance = (covariance + covariance.T) / 2.0
    covariance += np.eye(covariance.shape[0]) * 1e-12
    return covariance, intensity


def equal_risk_contribution(covariance: np.ndarray) -> np.ndarray:
    """Solve long-only equal-risk budgets with cyclic coordinate descent."""
    count = covariance.shape[0]
    weights = np.full(count, 1.0 / count)
    budgets = np.full(count, 1.0 / count)
    diagonal = np.clip(np.diag(covariance), 1e-12, None)
    for _ in range(10_000):
        previous = weights.copy()
        for index in range(count):
            cross = float(covariance[index] @ weights - covariance[index, index] * weights[index])
            discriminant = max(0.0, cross * cross + 4.0 * diagonal[index] * budgets[index])
            weights[index] = max(1e-15, (-cross + math.sqrt(discriminant)) / (2.0 * diagonal[index]))
        if float(np.max(np.abs(weights - previous))) < 1e-10:
            break
    return weights / weights.sum()


def _pearson_matrix(matrix: np.ndarray) -> np.ndarray:
    if matrix.shape[0] < 2:
        return np.eye(matrix.shape[1])
    correlation = np.corrcoef(matrix, rowvar=False)
    correlation = np.nan_to_num(correlation, nan=0.0, posinf=0.0, neginf=0.0)
    np.fill_diagonal(correlation, 1.0)
    return correlation


def clustered_selection(symbols: Sequence[str], correlation: np.ndarray, threshold: float) -> list[list[str]]:
    parent = list(range(len(symbols)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left in range(len(symbols)):
        for right in range(left + 1, len(symbols)):
            if correlation[left, right] >= threshold:
                union(left, right)
    groups: dict[int, list[str]] = defaultdict(list)
    for index, symbol in enumerate(symbols):
        groups[find(index)].append(symbol)
    return [groups[key] for key in sorted(groups)]


class DispersionDiagnosticControl(roadmap.RoadmapControl):
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.tail_gap_log: list[dict[str, object]] = []
        super().__init__(*args, **kwargs)

    def _select_names(
        self,
        ranked: tuple[tuple[float, str], ...],
        residual_vols: dict[str, float],
        context: PortfolioContext,
        top_n: int,
    ) -> tuple[str, ...]:
        if len(ranked) >= 12:
            top = ranked[:6]
            next_tier = ranked[6:12]
            self.tail_gap_log.append(
                {
                    "timestamp": context.decision_timestamp,
                    "gap": statistics.fmean(score for score, _symbol in top)
                    - statistics.fmean(score for score, _symbol in next_tier),
                    "top": tuple(symbol for _score, symbol in top),
                    "next": tuple(symbol for _score, symbol in next_tier),
                }
            )
        return super()._select_names(ranked, residual_vols, context, top_n)


class ShrinkageERC(roadmap.RoadmapControl):
    name = "equity_residual_momentum_shrinkage_erc_v1"
    version = "1.0.0"

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.erc_log: list[dict[str, float]] = []
        super().__init__(*args, **kwargs)

    def _select_names(self, ranked, residual_vols, context, top_n):  # type: ignore[no-untyped-def]
        del residual_vols, context, top_n
        return tuple(symbol for _score, symbol in ranked[:6])

    def _position_weights_for_ranked(self, ranked, selected, residual_vols, context):  # type: ignore[no-untyped-def]
        del ranked
        matrix = residual_matrix(context, selected)
        if matrix is None:
            return super()._position_weights_for_ranked((), selected, residual_vols, context)
        covariance, intensity = constant_correlation_shrinkage(matrix)
        raw = equal_risk_contribution(covariance)
        cap = self.param_float("max_single_weight", 0.10)
        weights = {
            symbol: min(cap, float(weight) * roadmap.CURRENT_EQUITY_ALLOCATION)
            for symbol, weight in zip(selected, raw, strict=True)
        }
        condition = float(np.linalg.cond(covariance))
        effective_bets = 1.0 / float(np.square(raw).sum())
        self.erc_log.append(
            {
                "shrinkage_intensity": intensity,
                "condition_number": condition,
                "effective_weight_bets": effective_bets,
                "pre_overlay_exposure": sum(weights.values()),
            }
        )
        self._record_weight_diagnostic(selected, weights, method="constant_correlation_shrinkage_erc")
        return weights


class ClusteredV3(roadmap.RoadmapControl):
    name = "equity_residual_momentum_clustered_v1"
    version = "1.0.0"

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.cluster_log: list[dict[str, float]] = []
        super().__init__(*args, **kwargs)

    def _select_names(self, ranked, residual_vols, context, top_n):  # type: ignore[no-untyped-def]
        del residual_vols, top_n
        shortlist = tuple(symbol for _score, symbol in ranked[:CLUSTER_SHORTLIST])
        matrix = residual_matrix(context, shortlist)
        if matrix is None:
            return shortlist[:6]
        correlation = _pearson_matrix(matrix)
        groups = clustered_selection(shortlist, correlation, CLUSTER_THRESHOLD)
        rank = {symbol: index for index, symbol in enumerate(shortlist)}
        representatives = sorted((min(group, key=rank.get) for group in groups), key=rank.get)
        selected = representatives[:6]
        for symbol in shortlist:
            if len(selected) >= 6:
                break
            if symbol not in selected:
                selected.append(symbol)
        chosen_indices = [rank[symbol] for symbol in selected]
        chosen_corr = correlation[np.ix_(chosen_indices, chosen_indices)]
        off_diag = chosen_corr[np.triu_indices_from(chosen_corr, 1)]
        self.cluster_log.append(
            {
                "cluster_count": float(len(groups)),
                "refill_count": float(max(0, 6 - min(6, len(representatives)))),
                "selected_mean_pair_correlation": float(off_diag.mean()) if len(off_diag) else 0.0,
            }
        )
        return tuple(selected)


class DispersionV3(roadmap.RoadmapControl):
    name = "equity_residual_momentum_dispersion_v1"
    version = "1.0.0"

    def _select_names(self, ranked, residual_vols, context, top_n):  # type: ignore[no-untyped-def]
        del residual_vols, context, top_n
        if len(ranked) < 12:
            return tuple(symbol for _score, symbol in ranked[:4])
        gap = statistics.fmean(score for score, _symbol in ranked[:6]) - statistics.fmean(
            score for score, _symbol in ranked[6:12]
        )
        count = 6 if gap >= DISPERSION_HIGH else 5 if gap >= DISPERSION_LOW else 4
        return tuple(symbol for _score, symbol in ranked[:count])

    def _position_weights_for_ranked(self, ranked, selected, residual_vols, context):  # type: ignore[no-untyped-def]
        del ranked, residual_vols, context
        return dict.fromkeys(selected, 0.10)


class FactorETFMomentum(roadmap.RoadmapControl):
    name = "equity_factor_momentum_etf_v1"
    version = "1.0.0"
    display_name = "Factor ETF Momentum v1 (available canonical panel)"

    def _score_candidates(self, context: PortfolioContext):  # type: ignore[no-untyped-def]
        residuals: dict[str, list[float]] = {}
        for symbol in ETF_PANEL:
            bars = tuple(context.history.get(symbol, ()))
            if len(bars) < 274:
                continue
            returns = [right.close / left.close - 1.0 for left, right in zip(bars, bars[1:]) if left.close > 0]
            if len(returns) < 273:
                continue
            twelve_one = _compounded(returns[-273:-21])
            if not math.isfinite(twelve_one) or twelve_one <= 0:
                continue
            residuals[symbol] = returns
        consensus = multi_horizon_consensus(residuals, (126, 189, 252), 21)
        ranked = tuple(
            sorted(((score, symbol) for symbol, score in consensus.items()), key=lambda item: (-item[0], item[1]))
        )
        return ranked, dict.fromkeys((symbol for _score, symbol in ranked), 1.0)

    def _select_names(self, ranked, residual_vols, context, top_n):  # type: ignore[no-untyped-def]
        del residual_vols, context, top_n
        return tuple(symbol for _score, symbol in ranked[:6])

    def _position_weights_for_ranked(self, ranked, selected, residual_vols, context):  # type: ignore[no-untyped-def]
        del ranked, residual_vols, context
        return dict.fromkeys(selected, 0.10)

    def decide(self, context: PortfolioContext):  # type: ignore[no-untyped-def]
        target = super().decide(context)
        return replace(
            target,
            metadata={
                **target.metadata,
                "hypothesis": "positive 12-1 split-adjusted price momentum; equal 6/9/12 rank consensus; fixed ETF panel",
                "fixed_panel": ETF_PANEL,
            },
        )


class TwiceWeeklyV3(roadmap.RoadmapControl):
    name = "equity_residual_momentum_twice_weekly_v1"
    version = "1.0.0"
    cadence_label = "Monday/Thursday boundary"


@dataclass(frozen=True)
class TwiceWeeklyPolicy:
    market: Market = Market.EQUITY
    frequency: str = "weekdays:0,3"

    def is_period_start(self, previous: datetime | None, current: datetime) -> bool:
        if previous is None:
            return True
        previous_iso = previous.isocalendar()
        current_iso = current.isocalendar()
        previous_half = 0 if previous.weekday() < 3 else 1
        current_half = 0 if current.weekday() < 3 else 1
        return (previous_iso.year, previous_iso.week, previous_half) != (
            current_iso.year,
            current_iso.week,
            current_half,
        )


class _IncompleteBatchCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "Historical strategy batch incomplete" in record.getMessage():
            self.count += 1


def _run_strategy(
    config: Config,
    strategy: roadmap.EquityResidualMomentum1dV3,
    window: roadmap.PreparedWindow,
    terminal_map: Mapping[str, roadmap.TerminalInfo],
    actions: Sequence[roadmap.CorporateAction],
    *,
    policy: object | None = None,
):  # type: ignore[no-untyped-def]
    engine = roadmap.make_engine(config, strategy, window, terminal_map=terminal_map)
    if policy is not None:
        engine.rebalance_policy = policy  # research-only custom cadence
    counter = _IncompleteBatchCounter()
    engine_logger = logging.getLogger("parayabuck.backtest.portfolio_engine")
    engine_logger.addHandler(counter)
    try:
        result = engine.run(
            window.filtered,
            warmup=window.warmup,
            liquidate_at_end=True,
            corporate_actions=list(actions),
            prepared_inputs=window.prepared,
            numeric_history=window.numeric_history,
        )
    finally:
        engine_logger.removeHandler(counter)
    integrity = {
        "incomplete_strategy_batch_count": counter.count,
        "runtime_valid": counter.count == 0,
        "comparability": "COMPARABLE" if counter.count == 0 else "NON_COMPARABLE_INCOMPLETE_BATCH",
    }
    return result, strategy, integrity


def _fixed_panel_window(window: roadmap.PreparedWindow) -> roadmap.PreparedWindow:
    snapshots = tuple(
        UniverseSnapshot(
            effective_at=snapshot.effective_at,
            symbols=frozenset(ETF_PANEL),
            scanner_version="fixed-etf-panel-v1",
            config_digest="equity_factor_momentum_etf_v1",
            candidate_count=len(ETF_PANEL),
            observation_set=ETF_PANEL,
            maximum_stream_symbols=len(ETF_PANEL),
            pinned_symbols=("SPY",),
            observation_strategy_name=FactorETFMomentum.name,
            observation_strategy_version=FactorETFMomentum.version,
        )
        for snapshot in window.universe.snapshots
    )
    universe = PointInTimeUniverse(Market.EQUITY, snapshots, "fixed survivorship-stable ETF panel")
    return replace(window, universe=universe, universe_version=hash_point_in_time_universe(universe))


def _mean_log(items: Iterable[Mapping[str, float]]) -> dict[str, float]:
    rows = list(items)
    if not rows:
        return {}
    return {key: round(statistics.fmean(row[key] for row in rows), 6) for key in rows[0]}


def _result_record(result, control_result, integrity: Mapping[str, object]):  # type: ignore[no-untyped-def]
    stats = roadmap.full_stats(result)
    control = roadmap.full_stats(control_result)
    return {
        **stats,
        "delta_return": round(float(stats["return"]) - float(control["return"]), 6),
        "delta_sharpe": round(float(stats["sharpe"]) - float(control["sharpe"]), 6),
        "delta_max_drawdown": round(float(stats["max_drawdown"]) - float(control["max_drawdown"]), 6),
        "active_return_correlation": round(
            roadmap._pearson(
                roadmap._daily_returns(result.equity_curve), roadmap._daily_returns(control_result.equity_curve)
            ),
            6,
        ),
        "runtime_integrity": dict(integrity),
    }


def _forward_return(bars: Sequence[Bar], timestamp: datetime, sessions: int = 5) -> float | None:
    index = bisect_right(bars, timestamp, key=lambda bar: bar.timestamp)
    if index >= len(bars) or index + sessions - 1 >= len(bars):
        return None
    entry = bars[index].open
    exit_price = bars[index + sessions - 1].close
    if entry <= 0:
        return None
    return exit_price / entry - 1.0


def dispersion_diagnostic(log: Sequence[Mapping[str, object]], bars: Sequence[Bar]) -> dict[str, object]:
    symbols = {symbol for row in log for key in ("top", "next") for symbol in row[key]}  # type: ignore[index]
    by_symbol: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        if bar.symbol in symbols:
            by_symbol[bar.symbol].append(bar)
    bucket_rows: dict[str, list[float]] = defaultdict(list)
    fold_rows: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in log:
        timestamp = row["timestamp"]
        gap = float(row["gap"])
        bucket = "high" if gap >= DISPERSION_HIGH else "medium" if gap >= DISPERSION_LOW else "low"
        top_returns = [_forward_return(by_symbol[symbol], timestamp) for symbol in row["top"]]  # type: ignore[index]
        next_returns = [_forward_return(by_symbol[symbol], timestamp) for symbol in row["next"]]  # type: ignore[index]
        top_valid = [value for value in top_returns if value is not None]
        next_valid = [value for value in next_returns if value is not None]
        if not top_valid or not next_valid:
            continue
        alpha = statistics.fmean(top_valid) - statistics.fmean(next_valid)
        half = 1 if timestamp.month <= 6 else 2  # type: ignore[union-attr]
        fold = f"{timestamp.year}-H{half}"  # type: ignore[union-attr]
        bucket_rows[bucket].append(alpha)
        fold_rows[fold][bucket].append(alpha)
    means = {bucket: statistics.fmean(bucket_rows.get(bucket, [0.0])) for bucket in ("low", "medium", "high")}
    eligible = 0
    monotonic = 0
    for values in fold_rows.values():
        if all(len(values.get(bucket, ())) >= 2 for bucket in ("low", "medium", "high")):
            eligible += 1
            fold_means = [statistics.fmean(values[bucket]) for bucket in ("low", "medium", "high")]
            monotonic += int(fold_means[0] <= fold_means[1] <= fold_means[2])
    gate = means["low"] <= means["medium"] <= means["high"] and eligible > 0 and monotonic > eligible / 2
    return {
        "method": "next-tradable-open to fifth-session-close; top6 mean minus ranks7-12 mean",
        "thresholds": [DISPERSION_LOW, DISPERSION_HIGH],
        "bucket_count": {bucket: len(bucket_rows.get(bucket, ())) for bucket in ("low", "medium", "high")},
        "bucket_mean_forward_alpha": {bucket: round(means[bucket], 6) for bucket in means},
        "eligible_folds": eligible,
        "monotonic_folds": monotonic,
        "gate_passed": gate,
    }


def run() -> dict[str, object]:
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("declared_before_results") is not True:
        raise SystemExit("follow-up manifest must be predeclared")
    config = load_config(str(REPO / "config/settings.yaml"))
    if float(config.strategy.equity.maximum_allocation) != 0.55:
        raise SystemExit("follow-up research requires the current 0.55 equity policy")

    bars = roadmap.load_bars(roadmap.DATASET_V5)
    terminal_map, _lifetimes = roadmap.load_lifetimes()
    actions = roadmap.load_corporate_actions_all()
    full_window = roadmap.prepare_window(config, bars, key="followups-full", start=None, end=roadmap.EVAL_END)

    control_strategy = DispersionDiagnosticControl(dict(roadmap.FROZEN_PARAMETERS))
    control_result, _, control_integrity = _run_strategy(config, control_strategy, full_window, terminal_map, actions)
    records: dict[str, object] = {"control": _result_record(control_result, control_result, control_integrity)}

    erc_result, erc_strategy, erc_integrity = _run_strategy(
        config, ShrinkageERC(dict(roadmap.FROZEN_PARAMETERS)), full_window, terminal_map, actions
    )
    records["erc"] = {
        **_result_record(erc_result, control_result, erc_integrity),
        "diagnostics": _mean_log(erc_strategy.erc_log),
    }

    cluster_result, cluster_strategy, cluster_integrity = _run_strategy(
        config, ClusteredV3(dict(roadmap.FROZEN_PARAMETERS)), full_window, terminal_map, actions
    )
    records["clustered"] = {
        **_result_record(cluster_result, control_result, cluster_integrity),
        "diagnostics": _mean_log(cluster_strategy.cluster_log),
    }

    dispersion = dispersion_diagnostic(control_strategy.tail_gap_log, bars)
    if dispersion["gate_passed"]:
        dispersion_result, _, dispersion_integrity = _run_strategy(
            config, DispersionV3(dict(roadmap.FROZEN_PARAMETERS)), full_window, terminal_map, actions
        )
        dispersion["headline"] = _result_record(dispersion_result, control_result, dispersion_integrity)

    etf_window = _fixed_panel_window(full_window)
    etf_result, _, etf_integrity = _run_strategy(
        config, FactorETFMomentum(dict(roadmap.FROZEN_PARAMETERS)), etf_window, terminal_map, actions
    )
    records["factor_etf"] = {
        **_result_record(etf_result, control_result, etf_integrity),
        "universe_version": etf_window.universe_version,
        "panel": ETF_PANEL,
    }

    twice_result, _, twice_integrity = _run_strategy(
        config,
        # Keep the strategy's persisted/product-compatible parameter tuple at
        # weekly; this research-only run replaces the engine policy below.
        TwiceWeeklyV3(dict(roadmap.FROZEN_PARAMETERS)),
        full_window,
        terminal_map,
        actions,
        policy=TwiceWeeklyPolicy(),
    )
    records["twice_weekly"] = _result_record(twice_result, control_result, twice_integrity)

    etf = records["factor_etf"]
    composite_gate = {
        "positive_standalone_net_economics": bool(etf["return"] > 0),  # type: ignore[index]
        "active_return_correlation_below_0_60": bool(etf["active_return_correlation"] < 0.60),  # type: ignore[index]
        "three_x_costs": "not_run_headline_only",
        "candidate_jackknife": "not_run_headline_only",
        "multiple_fold_incrementality": "not_run_headline_only",
        "passed": False,
        "decision": "DO_NOT_RUN_COMPOSITE",
    }

    payload = {
        "schema_version": "1.0.0",
        "mission": manifest["mission"],
        "receipt": {
            "git_head": _git_head(),
            "dirty_paths": _git_dirty(),
            "script_sha256": _sha256(Path(__file__)),
            "manifest_sha256": _sha256(MANIFEST),
            "base_roadmap_sha256": _sha256(REPO / "scripts/research_v3_equity_roadmap.py"),
            "config_digest_sha256": roadmap.config_digest(config),
            "bars_sha256": _sha256(roadmap.DATASET_V5),
            "lifetimes_sha256": _sha256(roadmap.LIFETIMES_V5),
            "corporate_actions_sha256": _sha256(roadmap.CA_V5),
            "eval_end": roadmap.EVAL_END.isoformat(),
            "allocation": 0.55,
            "terminal_policy": "T1",
        },
        "feasibility": {
            "erc": "RUNNABLE",
            "clustered": "RUNNABLE",
            "dispersion": "DIAGNOSTIC_GATE_FIRST",
            "factor_etf": "RUNNABLE_WITH_AVAILABLE_PANEL_LIMITATION",
            "twice_weekly": "RESEARCH_ONLY_POLICY_RUNNABLE; PRODUCT_POLICY_UNSUPPORTED",
            "pead": {
                "status": "BLOCKED_MISSING_PIT_DATA_AND_FEATURE_VIEW",
                "required_missing": [
                    "stable issuer/security mapping for events",
                    "announcement timestamp and market-session mapping",
                    "actual EPS",
                    "pre-announcement consensus EPS",
                    "revision/restatement lineage",
                    "typed PointInTimeFeatureView for historical/PAPER/LIVE parity",
                ],
                "repository_evidence": "Only data/research_manifests/equity_v3_alpha_roadmap_2026_08.json declares the absence; no matching schema/data implementation exists.",
            },
        },
        "headline": records,
        "dispersion": dispersion,
        "composite_gate": composite_gate,
        "promotion": "NONE_RESEARCH_ONLY",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()
    payload = run()
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"wrote {OUT_PATH}")
        for name, record in payload["headline"].items():
            print(name, record["return"], record["sharpe"], record["max_drawdown"], record["turnover"])
        print("dispersion_gate", payload["dispersion"]["gate_passed"])
        print("composite", payload["composite_gate"]["decision"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
