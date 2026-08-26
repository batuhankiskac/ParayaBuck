#!/usr/bin/env python3
# ruff: noqa: E501
"""Crypto Candidate Tournament T0-T6 — research harness 2026-08.

Single harness implementing T0 control + T1 hysteresis, T2 consensus 22/63,
T3 downside, T4 top5 correlation, T5 inverse-vol, T6 combo under identical
PIT/costs/folds. Production-parity: same 22-symbol filter, 1h→1d
require_complete, PIT via build_product_universe, costs 25/2/2, band 0.005,
allocation 0.05. Reuses validated scaffolding from
research_crypto_residual_low_variance.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from parayabuck.backtest.product import build_product_universe, load_csv_bars, run_product_backtest  # noqa: E402
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext  # noqa: E402
from parayabuck.portfolio.crypto_low_variance import CryptoLowVariance1dV2  # noqa: E402
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight  # noqa: E402
from parayabuck.portfolio.momentum import realized_volatility  # noqa: E402

DATASET_PATH = REPO / "data/historical/crypto_expanded_1h.csv"
DATASET_SHA256 = "73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e"
OUT_DIR = REPO / "data/research/crypto_candidate_tournament_2026_08"

FULL_START = datetime(2021, 4, 1, tzinfo=UTC)
FULL_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)

WALK_FOLDS = [
    ("2022-H1", datetime(2022, 1, 1, tzinfo=UTC), datetime(2022, 6, 30, 23, 59, 59, tzinfo=UTC)),
    ("2022-H2", datetime(2022, 7, 1, tzinfo=UTC), datetime(2022, 12, 31, 23, 59, 59, tzinfo=UTC)),
    ("2023-H1", datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 6, 30, 23, 59, 59, tzinfo=UTC)),
    ("2023-H2", datetime(2023, 7, 1, tzinfo=UTC), datetime(2023, 12, 31, 23, 59, 59, tzinfo=UTC)),
    ("2024-H1", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC)),
    ("2024-H2", datetime(2024, 7, 1, tzinfo=UTC), datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)),
    ("2025-H1", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 6, 30, 23, 59, 59, tzinfo=UTC)),
    ("2025-H2", datetime(2025, 7, 1, tzinfo=UTC), datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)),
    ("2026-H1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)),
]
COMPARABLE_FOLD_IDS = [fid for fid, _, _ in WALK_FOLDS]

# Candidate specs — tournament plan T0-T6
T0_PARAMS = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96}
T1_PARAMS = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96, "hysteresis_rank_threshold": 4}
T2_PARAMS = {"top_n": 2, "rebalance_hours": 96}
T3_PARAMS = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96}
T4_PARAMS = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96}
T5_PARAMS = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96}
T6_PARAMS = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96, "hysteresis_rank_threshold": 4}

CANDIDATES = {
    "T0": {"label": "CONTROL low-variance 22d 96h", "params": T0_PARAMS},
    "T1": {"label": "T1 hysteresis retention rank<=4", "params": T1_PARAMS},
    "T2": {"label": "T2 consensus 22/63 percentile mean", "params": T2_PARAMS},
    "T3": {"label": "T3 downside deviation 22d", "params": T3_PARAMS},
    "T4": {"label": "T4 correlation-aware top5", "params": T4_PARAMS},
    "T5": {"label": "T5 inverse-vol weighting", "params": T5_PARAMS},
    "T6": {"label": "T6 hysteresis + inverse-vol", "params": T6_PARAMS},
}

# ---------------------------------------------------------------------------
# Pure arithmetic helpers — testable in isolation
# ---------------------------------------------------------------------------


def sample_std(values: list[float]) -> float | None:
    """Sample std (n-1). None if <2 or non-finite."""
    if len(values) < 2:
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    mean = statistics.fmean(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    if not math.isfinite(var) or var < 0:
        return None
    return math.sqrt(var)


def daily_simple_returns(closes: list[float]) -> list[float] | None:
    """Simple returns r_t = close_t/close_{t-1} -1. None if any close <=0/non-finite."""
    if len(closes) < 2:
        return None
    if not all(math.isfinite(c) and c > 0 for c in closes):
        return None
    result = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    if not all(math.isfinite(r) for r in result):
        return None
    return result


def is_consecutive_daily_bars(bars: list[Bar], lookback: int) -> bool:
    """Check last lookback+1 bars span exactly lookback calendar days."""
    if len(bars) < lookback + 1:
        return False
    window = bars[-(lookback + 1) :]
    return window[-1].timestamp - window[0].timestamp == timedelta(days=lookback)


# alias for tests
def is_consecutive(bars: list[Bar], lookback: int) -> bool:
    return is_consecutive_daily_bars(bars, lookback)


def downside_deviation(returns: list[float]) -> float | None:
    """Downside deviation = sqrt(mean(min(r,0)^2)). None if empty or non-finite."""
    if not returns:
        return None
    if not all(math.isfinite(r) for r in returns):
        return None
    # no mean subtraction: sqrt(mean(min(r,0)^2))
    sq = [min(r, 0.0) ** 2 for r in returns]
    mean_sq = statistics.fmean(sq)
    if not math.isfinite(mean_sq) or mean_sq < 0:
        return None
    return math.sqrt(mean_sq)


def percentile_ranks(score_map: dict[str, float]) -> dict[str, float]:
    """Percentile rank (rank-1)/(n-1) ascending score, tie broken by symbol.

    Deterministic: sorts by (score, symbol).
    """
    if not score_map:
        return {}
    eligible = sorted(score_map, key=lambda s: (score_map[s], s))
    n = len(eligible)
    if n == 1:
        return {eligible[0]: 0.0}
    out: dict[str, float] = {}
    for idx, sym in enumerate(eligible):
        out[sym] = idx / (n - 1)
    return out


# aliases expected by TestsDesign
def percentile_rank(score_map: dict[str, float]) -> dict[str, float]:
    return percentile_ranks(score_map)


def mean_percentile_score(pr_a: dict[str, float], pr_b: dict[str, float]) -> dict[str, float]:
    """Mean of two percentile rank maps over intersection."""
    keys = set(pr_a) & set(pr_b)
    return {k: (pr_a[k] + pr_b[k]) / 2.0 for k in keys}


def inverse_vol_weights(sigmas: dict[str, float]) -> dict[str, float]:
    """Inverse-vol weights normalized to sum 1. Skips non-positive/non-finite sigma."""
    filtered = {s: v for s, v in sigmas.items() if math.isfinite(v) and v > 1e-12}
    if not filtered:
        return {}
    inv = {s: 1.0 / v for s, v in filtered.items()}
    total = sum(inv.values())
    if total <= 0 or not math.isfinite(total):
        return {}
    return {s: w / total for s, w in inv.items()}


def correlation(a_returns: list[float], b_returns: list[float]) -> float | None:
    """Sample Pearson correlation. None if <2, non-finite, or zero variance."""
    if len(a_returns) != len(b_returns) or len(a_returns) < 2:
        return None
    if not all(math.isfinite(x) for x in a_returns) or not all(math.isfinite(x) for x in b_returns):
        return None
    mean_a = statistics.fmean(a_returns)
    mean_b = statistics.fmean(b_returns)
    var_a = sum((x - mean_a) ** 2 for x in a_returns)
    var_b = sum((x - mean_b) ** 2 for x in b_returns)
    if var_a < 1e-18 or var_b < 1e-18:
        return None
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a_returns, b_returns, strict=True))
    corr = cov / math.sqrt(var_a * var_b)
    if not math.isfinite(corr):
        return None
    # clamp
    return max(-1.0, min(1.0, corr))


# aliases
def pearson_correlation(a: list[float], b: list[float]) -> float | None:
    return correlation(a, b)


def sample_correlation(a: list[float], b: list[float]) -> float | None:
    return correlation(a, b)


def hysteresis_should_retain(rank: int | None, in_universe: bool) -> bool:
    """Retain if in_universe and rank <=4 (1-indexed). None rank -> False."""
    if not in_universe:
        return False
    if rank is None:
        return False
    return rank <= 4


def select_hysteresis(
    ranked: list[str],
    holdings: set[str],
    in_universe: set[str],
    top_n: int = 2,
    threshold: int = 4,
) -> list[str]:
    """Hysteresis selection: retain holdings with rank<=threshold, fill from rank order.

    ranked: ordered best-first.
    holdings: currently held symbols (weight>0).
    in_universe: symbols present in PIT universe at decision.
    """
    rank_map = {s: i + 1 for i, s in enumerate(ranked)}
    retained: list[str] = []
    for h in sorted(holdings):
        if h in in_universe and rank_map.get(h, 999) <= threshold:
            retained.append(h)
    # retain order by rank (lowest sigma first) for determinism
    retained.sort(key=lambda s: (rank_map.get(s, 999), s))
    # fill
    selected: list[str] = list(retained[:top_n])
    for sym in ranked:
        if len(selected) >= top_n:
            break
        if sym not in selected:
            selected.append(sym)
    return selected[:top_n]


# alias
def hysteresis_select(ranked: list[str], holdings: set[str], in_universe: set[str], top_n: int = 2) -> list[str]:
    return select_hysteresis(ranked, holdings, in_universe, top_n=top_n)


def select_correlation_pair(
    ranked_top5: list[str],
    corr_map: dict[tuple[str, str], float],
) -> tuple[str, str]:
    """Pick #1 = ranked_top5[0], #2 = lowest correlation to #1 among 2..5.

    corr_map keys are (candidate, anchor) with candidate != anchor.
    Fallback to ranked_top5[1] if no valid corr.
    """
    if not ranked_top5:
        return ("", "")
    if len(ranked_top5) == 1:
        return (ranked_top5[0], ranked_top5[0])
    anchor = ranked_top5[0]
    candidates = ranked_top5[1:5]
    best: str | None = None
    best_corr: float | None = None
    # need rank order for tie break: lower rank (earlier in ranked_top5) wins
    rank_order = {s: i for i, s in enumerate(ranked_top5)}
    for c in candidates:
        corr = corr_map.get((c, anchor))
        if corr is None or not math.isfinite(corr):
            continue
        if (
            best is None
            or corr < best_corr - 1e-12
            or (abs(corr - best_corr) < 1e-12 and rank_order[c] < rank_order[best])
        ):
            best = c
            best_corr = corr
    if best is None:
        return (anchor, ranked_top5[1])
    return (anchor, best)


# ---------------------------------------------------------------------------
# Strategy implementations T0-T6
# ---------------------------------------------------------------------------


class CryptoTournamentT1Hysteresis(CryptoLowVariance1dV2):
    """T1 hysteresis retention: keep held if rank<=4 else exit, fill from rank order."""

    name = "crypto_tournament_t1_hysteresis"
    version = "1.0.0"
    display_name = "Tournament T1 Hysteresis"
    default_rebalance_hours = 96
    rebalance_policy_declared = "hours:96"
    cadence_label = "96h"

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        top_n = self.param_int("top_n", 2)
        ranked_scored = self._score_candidates(context)
        ranked = [sym for _sigma, sym in ranked_scored]
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_T1")
        rank_map = {sym: i + 1 for i, sym in enumerate(ranked)}
        in_univ = {s for s in ranked if context.in_universe(s)}
        # holdings with positive weight
        held = {s for s, w in context.holdings.items() if w > 1e-12}
        retained: list[str] = []
        for h in held:
            if hysteresis_should_retain(rank_map.get(h), h in in_univ):
                retained.append(h)
        retained.sort(key=lambda s: (rank_map.get(s, 999), s))
        retained = retained[:top_n]
        selected: list[str] = list(retained)
        for sym in ranked:
            if len(selected) >= top_n:
                break
            if sym not in selected:
                selected.append(sym)
        selected = selected[:top_n]
        if not selected:
            return self._cash_portfolio(context, "hysteresis_empty")
        weight = 1.0 / len(selected)
        targets = tuple(TargetWeight(s, weight, reason="t1_hysteresis_low_var") for s in sorted(selected))
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(w.weight for w in targets)),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(ranked),
                "selected_symbols": tuple(selected),
                "retained": tuple(retained),
                "hypothesis": "t1 hysteresis low-var",
                "explanations": {
                    sym: {
                        "alpha_rank": rank_map.get(sym),
                        "retained": sym in retained,
                        "target_weight": weight,
                        "current_weight": float(context.holdings.get(sym, 0.0)),
                    }
                    for sym in selected
                },
            },
        )


class CryptoTournamentT2Consensus(CryptoLowVariance1dV2):
    """T2 consensus 22/63 percentile mean."""

    name = "crypto_tournament_t2_consensus"
    version = "1.0.0"
    display_name = "Tournament T2 Consensus 22/63"
    default_rebalance_hours = 96
    rebalance_policy_declared = "hours:96"
    cadence_label = "96h"

    def _score_candidates(self, context: PortfolioContext) -> tuple[tuple[float, str], ...]:
        per22: dict[str, float] = {}
        per63: dict[str, float] = {}
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            bars = context.history[symbol]
            # need both windows
            if len(closes) < 64 or len(bars) < 64:
                continue
            # 22 check
            w22_bars = bars[-23:]
            if w22_bars[-1].timestamp - w22_bars[0].timestamp != timedelta(days=22):
                continue
            w22_closes = closes[-23:]
            if not all(math.isfinite(c) and c > 0 for c in w22_closes):
                continue
            # 63 check
            w63_bars = bars[-64:]
            if w63_bars[-1].timestamp - w63_bars[0].timestamp != timedelta(days=63):
                continue
            w63_closes = closes[-64:]
            if not all(math.isfinite(c) and c > 0 for c in w63_closes):
                continue
            sigma22 = realized_volatility(closes, 22)
            sigma63 = realized_volatility(closes, 63)
            if not math.isfinite(sigma22) or not math.isfinite(sigma63):
                continue
            per22[symbol] = sigma22
            per63[symbol] = sigma63
        eligible = sorted(set(per22) & set(per63))
        if len(eligible) < 2:
            return tuple()
        pr22 = percentile_ranks({s: per22[s] for s in eligible})
        pr63 = percentile_ranks({s: per63[s] for s in eligible})
        scored: list[tuple[float, str]] = []
        for sym in eligible:
            mean_pr = (pr22[sym] + pr63[sym]) / 2.0
            scored.append((mean_pr, sym))
        scored.sort(key=lambda item: (item[0], item[1]))
        return tuple(scored)


class CryptoTournamentT3Downside(CryptoLowVariance1dV2):
    """T3 downside deviation ranking."""

    name = "crypto_tournament_t3_downside"
    version = "1.0.0"
    display_name = "Tournament T3 Downside"
    default_rebalance_hours = 96
    rebalance_policy_declared = "hours:96"
    cadence_label = "96h"

    def _score_candidates(self, context: PortfolioContext) -> tuple[tuple[float, str], ...]:
        lookback = 22
        scored: list[tuple[float, str]] = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            bars = context.history[symbol]
            if len(closes) < lookback + 1 or len(bars) < lookback + 1:
                continue
            window_bars = bars[-(lookback + 1) :]
            if window_bars[-1].timestamp - window_bars[0].timestamp != timedelta(days=lookback):
                continue
            window_closes = closes[-(lookback + 1) :]
            if not all(math.isfinite(c) and c > 0 for c in window_closes):
                continue
            rets = daily_simple_returns(window_closes)
            if rets is None or len(rets) != lookback:
                continue
            dd = downside_deviation(rets)
            if dd is None or not math.isfinite(dd):
                continue
            scored.append((dd, symbol))
        scored.sort(key=lambda item: (item[0], item[1]))
        return tuple(scored)


class CryptoTournamentT4Correlation(CryptoLowVariance1dV2):
    """T4 correlation-aware: top5 vol, pick #1 vol rank1, #2 lowest corr to #1."""

    name = "crypto_tournament_t4_correlation"
    version = "1.0.0"
    display_name = "Tournament T4 Correlation-aware"
    default_rebalance_hours = 96
    rebalance_policy_declared = "hours:96"
    cadence_label = "96h"

    def _t0_ranking(self, context: PortfolioContext) -> list[tuple[float, str]]:
        # replicate T0 scoring
        lookback = self.param_int("variance_lookback_days", 22)
        qualified: list[tuple[float, str]] = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            bars = context.history[symbol]
            if len(closes) < lookback + 1:
                continue
            window_bars = bars[-(lookback + 1) :]
            if window_bars[-1].timestamp - window_bars[0].timestamp != timedelta(days=lookback):
                continue
            window_closes = closes[-(lookback + 1) :]
            if not all(math.isfinite(c) and c > 0 for c in window_closes):
                continue
            sigma = realized_volatility(closes, lookback)
            if not math.isfinite(sigma):
                continue
            qualified.append((sigma, symbol))
        qualified.sort(key=lambda item: (item[0], item[1]))
        return qualified

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        top_n = self.param_int("top_n", 2)
        t0_ranked = self._t0_ranking(context)
        if not t0_ranked:
            return self._cash_portfolio(context, "no_qualified_T4")
        ranked_symbols = [s for _, s in t0_ranked]
        shortlist = ranked_symbols[:5]
        if len(shortlist) == 1:
            selected = (shortlist[0],)
        elif len(shortlist) >= 2:
            anchor = shortlist[0]
            # compute correlations anchor vs each candidate 2..5
            corr_map: dict[tuple[str, str], float] = {}
            # need aligned returns over 22 days
            for cand in shortlist[1:]:
                # timestamp-aligned returns between anchor and cand
                anchor_bars = context.history.get(anchor, ())
                cand_bars = context.history.get(cand, ())
                if not anchor_bars or not cand_bars:
                    continue
                # build timestamp->close maps
                anchor_map = {b.timestamp: b.close for b in anchor_bars}
                cand_map = {b.timestamp: b.close for b in cand_bars}
                common = sorted(set(anchor_map) & set(cand_map))
                if len(common) < 23:
                    continue
                # take last 23 common timestamps
                last_common = common[-23:]
                # check consecutive days span 22
                if last_common[-1] - last_common[0] != timedelta(days=22):
                    continue
                # also need consecutive day-to-day (no gap)
                consecutive_ok = all(
                    last_common[i] - last_common[i - 1] == timedelta(days=1) for i in range(1, len(last_common))
                )
                if not consecutive_ok:
                    continue
                anchor_closes = [anchor_map[ts] for ts in last_common]
                cand_closes = [cand_map[ts] for ts in last_common]
                if not all(math.isfinite(c) and c > 0 for c in anchor_closes + cand_closes):
                    continue
                a_rets = daily_simple_returns(anchor_closes)
                c_rets = daily_simple_returns(cand_closes)
                if a_rets is None or c_rets is None or len(a_rets) != 22 or len(c_rets) != 22:
                    continue
                corr = correlation(c_rets, a_rets)
                if corr is None:
                    continue
                corr_map[(cand, anchor)] = corr
            # use helper to pick
            anchor_sel, second_sel = select_correlation_pair(shortlist, corr_map)
            if second_sel == "":
                second_sel = shortlist[1]
            selected = (anchor_sel, second_sel)[:top_n] if top_n == 2 else (anchor_sel,)
            # if top_n 2 but helper returns pair, ensure both in shortlist
        else:
            selected = tuple(ranked_symbols[:top_n])
        if not selected:
            return self._cash_portfolio(context, "no_selection_T4")
        weight = 1.0 / len(selected)
        targets = tuple(TargetWeight(s, weight, reason="t4_correlation_aware") for s in sorted(selected))
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(w.weight for w in targets)),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(t0_ranked),
                "selected_symbols": tuple(selected),
                "hypothesis": "t4 correlation-aware low-var",
                "shortlist": tuple(shortlist),
                "correlations": {f"{k[0]}:{k[1]}": v for k, v in corr_map.items()} if "corr_map" in locals() else {},
            },
        )


class CryptoTournamentT5InverseVol(CryptoLowVariance1dV2):
    """T5 inverse-vol weighting: selection T0 top2, weight 1/sigma."""

    name = "crypto_tournament_t5_inverse_vol"
    version = "1.0.0"
    display_name = "Tournament T5 Inverse Vol"
    default_rebalance_hours = 96
    rebalance_policy_declared = "hours:96"
    cadence_label = "96h"

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        ranked = self._score_candidates(context)
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_T5")
        top_n = self.param_int("top_n", 2)
        selected_scored = ranked[:top_n]
        selected = tuple(sym for _, sym in selected_scored)
        sigmas = {sym: sigma for sigma, sym in selected_scored}
        weights = inverse_vol_weights(sigmas)
        # fallback to equal if inverse fails (should not)
        if not weights or set(weights) != set(selected):
            weight_eq = 1.0 / len(selected)
            targets = tuple(TargetWeight(s, weight_eq, reason="t5_inverse_vol_fallback") for s in sorted(selected))
        else:
            targets = tuple(TargetWeight(s, weights[s], reason="t5_inverse_vol") for s in sorted(selected))
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(w.weight for w in targets)),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(ranked),
                "selected_symbols": selected,
                "sigmas": sigmas,
                "weights": {s: w.weight for s, w in zip(sorted(selected), targets, strict=False)}
                if "weights" in locals()
                else {},
                "hypothesis": "t5 inverse-vol weighting",
            },
        )


class CryptoTournamentT6HysteresisInverseVol(CryptoLowVariance1dV2):
    """T6 hysteresis selection + inverse-vol sizing."""

    name = "crypto_tournament_t6_hysteresis_inverse"
    version = "1.0.0"
    display_name = "Tournament T6 Hysteresis+InvVol"
    default_rebalance_hours = 96
    rebalance_policy_declared = "hours:96"
    cadence_label = "96h"

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        top_n = self.param_int("top_n", 2)
        ranked_scored = self._score_candidates(context)
        ranked = [sym for _, sym in ranked_scored]
        sigma_map = {sym: sigma for sigma, sym in ranked_scored}
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_T6")
        rank_map = {sym: i + 1 for i, sym in enumerate(ranked)}
        in_univ = {s for s in ranked if context.in_universe(s)}
        held = {s for s, w in context.holdings.items() if w > 1e-12}
        retained: list[str] = []
        for h in held:
            if hysteresis_should_retain(rank_map.get(h), h in in_univ):
                retained.append(h)
        retained.sort(key=lambda s: (rank_map.get(s, 999), s))
        retained = retained[:top_n]
        selected: list[str] = list(retained)
        for sym in ranked:
            if len(selected) >= top_n:
                break
            if sym not in selected:
                selected.append(sym)
        selected = selected[:top_n]
        if not selected:
            return self._cash_portfolio(context, "hysteresis_empty_T6")
        sigmas_sel = {s: sigma_map[s] for s in selected if s in sigma_map}
        weights = inverse_vol_weights(sigmas_sel)
        if not weights or set(weights) != set(selected):
            weight_eq = 1.0 / len(selected)
            targets = tuple(TargetWeight(s, weight_eq, reason="t6_hysteresis_fallback") for s in sorted(selected))
        else:
            targets = tuple(TargetWeight(s, weights[s], reason="t6_hysteresis_inverse_vol") for s in sorted(selected))
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(w.weight for w in targets)),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(ranked),
                "selected_symbols": tuple(selected),
                "retained": tuple(retained),
                "sigmas": sigmas_sel,
                "hypothesis": "t6 hysteresis + inverse-vol",
            },
        )


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------


def _avg_equity(result) -> float:  # type: ignore[no-untyped-def]
    curve = result.equity_curve
    return sum(curve) / len(curve) if curve else result.starting_equity


def _profit_factor(result) -> float:  # type: ignore[no-untyped-def]
    gp = sum(p for p in result.realized_pnls if p > 0)
    gl = abs(sum(p for p in result.realized_pnls if p < 0))
    if gl > 0:
        return gp / gl
    return float("inf") if gp > 0 else 0.0


def _detailed_metrics(result, bars_1d) -> dict:  # type: ignore[no-untyped-def]
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    avg_equity = _avg_equity(result)
    curve = list(result.equity_curve)
    timestamps = list(result.curve_timestamps)
    peak = curve[0] if curve else 0
    peak_idx = 0
    max_duration = 0
    for idx, val in enumerate(curve):
        if val > peak:
            peak = val
            peak_idx = idx
        elif idx > peak_idx:
            dur = (
                (timestamps[idx] - timestamps[peak_idx]).days
                if idx < len(timestamps) and peak_idx < len(timestamps)
                else idx - peak_idx
            )
            max_duration = max(max_duration, dur)
    fills = len(result.fills)
    pos_periods = 0
    total_periods = max(0, len(curve) - 1)
    for i in range(1, len(curve)):
        if curve[i] > curve[i - 1]:
            pos_periods += 1
    pos_ratio = pos_periods / total_periods if total_periods else 0.0
    return {
        "total_return": round(result.total_return, 6),
        "cagr": round(result.cagr, 6),
        "annualized_volatility": round(result.annualized_volatility, 6)
        if hasattr(result, "annualized_volatility")
        else None,
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "calmar": round(result.calmar, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "max_drawdown_duration_days": max_duration,
        "turnover": round(result.turnover, 6),
        "fees": {
            "slippage": round(result.slippage_cost_total, 2),
            "spread": round(result.spread_cost_total, 2),
            "fee": round(result.fee_total, 2),
            "total": round(total_cost, 2),
            "fees_pct_of_avg_equity": round(total_cost / avg_equity, 6) if avg_equity else 0.0,
        },
        "number_of_fills": fills,
        "rebalance_count": result.rebalance_count,
        "trade_count": len(result.closed_position_events),
        "average_exposure": round(1.0 - result.average_cash_ratio, 6),
        "max_exposure": round(result.maximum_exposure, 6) if hasattr(result, "maximum_exposure") else None,
        "average_holdings": round(result.average_holdings_count, 6),
        "positive_period_ratio": round(pos_ratio, 4),
        "equity_start": result.starting_equity,
        "equity_end": round(curve[-1], 2) if curve else result.starting_equity,
        "gross_return": round(result.gross_return, 6) if hasattr(result, "gross_return") else None,
        "profit_factor": round(_profit_factor(result), 4),
    }


def _concentration_metrics(result) -> dict:  # type: ignore[no-untyped-def]
    per = sorted(result.per_symbol, key=lambda x: -x.realized_pnl)
    total_pos = sum(p.realized_pnl for p in per if p.realized_pnl > 0)

    def entry(p):  # type: ignore[no-untyped-def]
        return {"symbol": p.symbol, "realized_pnl": round(p.realized_pnl, 2)}

    top = per[:3]
    return {
        "top_1": entry(top[0]) if len(top) > 0 else None,
        "top_2": entry(top[1]) if len(top) > 1 else None,
        "top_3": entry(top[2]) if len(top) > 2 else None,
        "top1_share_of_positive_pnl": round(top[0].realized_pnl / total_pos, 4)
        if total_pos > 0 and len(top) > 0
        else 0.0,
        "top2_share_of_positive_pnl": round(sum(p.realized_pnl for p in top[:2]) / total_pos, 4)
        if total_pos > 0
        else 0.0,
        "top3_share_of_positive_pnl": round(sum(p.realized_pnl for p in top[:3]) / total_pos, 4)
        if total_pos > 0
        else 0.0,
        "per_symbol": [
            {
                "symbol": p.symbol,
                "realized_pnl": round(p.realized_pnl, 2),
                "trades": p.trade_count if hasattr(p, "trade_count") else None,
            }
            for p in per
        ],
        "max_single_weight": round(result.maximum_concentration, 4)
        if hasattr(result, "maximum_concentration")
        else None,
        "symbol_count": len(per),
    }


def _turnover_analysis(result) -> dict:  # type: ignore[no-untyped-def]
    rebalances = list(result.rebalances)
    selections: list[tuple[datetime, frozenset[str]]] = []
    weight_snapshots: list[dict[str, float]] = []
    for rb in rebalances:
        syms = frozenset(s for s, _w in rb.target_weights)
        selections.append((rb.decision_timestamp, syms))
        weight_snapshots.append({s: float(w) for s, w in rb.target_weights})
    jaccards: list[float] = []
    for i in range(1, len(selections)):
        a = selections[i - 1][1]
        b = selections[i][1]
        if not a and not b:
            jaccards.append(1.0)
        elif not a or not b:
            jaccards.append(0.0)
        else:
            inter = len(a & b)
            union = len(a | b)
            jaccards.append(inter / union if union else 0.0)
    # weight turnover: L1 distance between consecutive weight vectors
    weight_turnovers: list[float] = []
    for i in range(1, len(weight_snapshots)):
        prev = weight_snapshots[i - 1]
        cur = weight_snapshots[i]
        keys = set(prev) | set(cur)
        l1 = sum(abs(cur.get(k, 0.0) - prev.get(k, 0.0)) for k in keys)
        weight_turnovers.append(l1 / 2.0)  # half L1 is turnover notion
    fills = sorted(result.fills, key=lambda f: (f.timestamp, f.symbol))
    qty: dict[str, float] = {}
    open_at: dict[str, datetime] = {}
    intervals: list[tuple[str, datetime, datetime]] = []
    for f in fills:
        sym = f.symbol
        if f.side.value == "buy":
            if qty.get(sym, 0) <= 1e-12:
                open_at[sym] = f.timestamp
            qty[sym] = qty.get(sym, 0) + f.qty
        else:
            before = qty.get(sym, 0)
            qty[sym] = max(0.0, before - f.qty)
            if before > 1e-12 and qty[sym] <= 1e-12:
                intervals.append((sym, open_at.get(sym, f.timestamp), f.timestamp))
    durations_days = [(close - open_).total_seconds() / 86400.0 for _, open_, close in intervals]
    turnover = 1.0 - statistics.fmean(jaccards) if jaccards else 0.0
    changes = sum(1 for i in range(1, len(selections)) if selections[i][1] != selections[i - 1][1])
    repl_freq = changes / max(1, len(selections) - 1)
    return {
        "selection_turnover": round(turnover, 4),
        "jaccard_overlap": {
            "mean": round(statistics.fmean(jaccards), 4) if jaccards else None,
            "median": round(statistics.median(jaccards), 4) if jaccards else None,
            "min": round(min(jaccards), 4) if jaccards else None,
            "max": round(max(jaccards), 4) if jaccards else None,
        },
        "weight_turnover": {
            "mean": round(statistics.fmean(weight_turnovers), 4) if weight_turnovers else None,
            "median": round(statistics.median(weight_turnovers), 4) if weight_turnovers else None,
            "total": round(sum(weight_turnovers), 4) if weight_turnovers else 0.0,
        },
        "holding_duration_days": {
            "mean": round(statistics.fmean(durations_days), 2) if durations_days else None,
            "median": round(statistics.median(durations_days), 2) if durations_days else None,
            "min": round(min(durations_days), 2) if durations_days else None,
            "max": round(max(durations_days), 2) if durations_days else None,
        },
        "replacement_frequency": round(repl_freq, 4),
        "rebalance_count": len(selections),
        "intervals": len(intervals),
        "methodology": "Jaccard on 96h target selection sets; weight turnover half-L1; holding duration from fills",
    }


def _regime_analysis(result, bars_1d: list[Bar]) -> dict:  # type: ignore[no-untyped-def]
    btc_by_ts: dict[datetime, float] = {}
    for bar in bars_1d:
        if bar.symbol == "BTC/USD":
            btc_by_ts[bar.timestamp] = bar.close
    sorted_ts = sorted(btc_by_ts)
    regime_returns: dict[str, list[float]] = {"BULL": [], "NEUTRAL": [], "BEAR": []}
    curve_ts = list(result.curve_timestamps)
    curve_eq = list(result.equity_curve)
    import bisect

    for i in range(1, len(curve_eq)):
        ts = curve_ts[i]
        if ts not in btc_by_ts:
            idx = bisect.bisect_right(sorted_ts, ts) - 1
            if idx < 0:
                continue
            ts_btc = sorted_ts[idx]
        else:
            ts_btc = ts
        btc_now = btc_by_ts.get(ts_btc)
        target = ts_btc - timedelta(days=126)
        idx2 = bisect.bisect_right(sorted_ts, target) - 1
        if idx2 < 0:
            continue
        btc_then = btc_by_ts[sorted_ts[idx2]]
        if btc_then <= 0 or btc_now is None:
            continue
        btc_ret_126 = btc_now / btc_then - 1.0
        if btc_ret_126 > 0.15:
            regime = "BULL"
        elif btc_ret_126 < -0.15:
            regime = "BEAR"
        else:
            regime = "NEUTRAL"
        strat_ret = curve_eq[i] / curve_eq[i - 1] - 1.0 if curve_eq[i - 1] > 0 else 0.0
        regime_returns[regime].append(strat_ret)
    out = {}
    for regime, rets in regime_returns.items():
        if not rets:
            out[regime] = {"days": 0, "total_return": 0.0, "sharpe": None, "mean_daily": None}
        else:
            cum = 1.0
            for r in rets:
                cum *= 1 + r
            total = cum - 1
            mean = statistics.fmean(rets)
            std = statistics.stdev(rets) if len(rets) > 1 else 0.0
            sharpe = (mean / std * math.sqrt(365)) if std > 1e-12 else None
            out[regime] = {
                "days": len(rets),
                "total_return": round(total, 6),
                "mean_daily": round(mean, 6),
                "sharpe": round(sharpe, 4) if sharpe is not None and math.isfinite(sharpe) else None,
            }
    return {
        "classification": "BTC trailing 126d return >+15% BULL, -15..+15 NEUTRAL, <-15 BEAR",
        "regimes": out,
        "not_exclusively_one_regime": sum(1 for r in out.values() if r["total_return"] > 0) >= 2,  # type: ignore[attr-defined]
    }


def _cost_stress_metrics(config, bars_1d, universe, strategy_name, parameters: dict) -> dict:  # type: ignore[no-untyped-def]
    from scripts.research_helpers import _cost_stress as _helper_cost_stress

    return _helper_cost_stress(
        config,
        bars_1d,
        market=Market.CRYPTO,
        strategy_name=strategy_name,
        parameters=parameters,
        strategy_timeframe="1d",
        start=FULL_START,
        end=FULL_END,
        universe=universe,
    )


def _fold_dependence(walk_forward: dict, control_id: str = "T0") -> dict:  # type: ignore[no-untyped-def]
    """Excess vs T0 per fold, largest positive excess / total positive excess >50% flag."""
    out: dict[str, dict] = {}
    control_rets = {
        s["fold_id"]: s["return"] for s in walk_forward[control_id]["folds"] if s["fold_id"] in COMPARABLE_FOLD_IDS
    }
    for cid, wf in walk_forward.items():
        if cid == control_id:
            continue
        excesses: list[tuple[str, float]] = []
        for s in wf["folds"]:
            fid = s["fold_id"]
            if fid not in COMPARABLE_FOLD_IDS:
                continue
            cr = control_rets.get(fid, 0.0)
            excess = s["return"] - cr
            excesses.append((fid, excess))
        positive = [(fid, e) for fid, e in excesses if e > 0]
        total_pos = sum(e for _, e in positive)
        largest_pos = max((e for _, e in positive), default=0.0)
        largest_fid = next((fid for fid, e in positive if e == largest_pos), None) if positive else None
        ratio = (largest_pos / total_pos) if total_pos > 1e-12 else 0.0
        flagged = ratio > 0.5 and len(positive) >= 2
        # also check any fold explains >50% of total excess PnL proxy (use total_return excess)
        out[cid] = {
            "candidate_id": cid,
            "excess_per_fold": {fid: round(e, 6) for fid, e in excesses},
            "total_positive_excess": round(total_pos, 6),
            "largest_positive_excess": round(largest_pos, 6),
            "largest_positive_fold": largest_fid,
            "largest_over_total_positive": round(ratio, 4),
            "flag_dependent_on_single_fold": flagged,
            "methodology": "excess vs T0 per comparable 6-month fold; flag if largest positive excess / total positive excess >50%",
        }
    return out


def _fee_savings_analysis(full_results: dict) -> dict:  # type: ignore[no-untyped-def]
    """Fee savings for T1/T6 vs T0 (turnover reduction proxy)."""
    base_fees = None
    base_turnover = None
    if "T0" in full_results:
        # raw result stored as _raw_result in caller; here we expect metrics dict with fees
        # We'll handle both: if metrics contains fees total
        base_metrics = full_results["T0"]["metrics"] if "metrics" in full_results["T0"] else full_results["T0"]
        base_fees = base_metrics.get("fees", {}).get("total") if isinstance(base_metrics.get("fees"), dict) else None
        base_turnover = base_metrics.get("turnover")
    out: dict[str, dict] = {}
    for cid, data in full_results.items():
        if cid == "T0":
            continue
        metrics = data.get("metrics", data) if isinstance(data, dict) else {}
        fees = metrics.get("fees", {}).get("total") if isinstance(metrics.get("fees"), dict) else None
        turnover = metrics.get("turnover")
        saving = (base_fees - fees) if (base_fees is not None and fees is not None) else None
        saving_pct = (saving / base_fees) if (saving is not None and base_fees and base_fees != 0) else None
        out[cid] = {
            "candidate_id": cid,
            "fees_total": fees,
            "base_fees_total": base_fees,
            "fee_saving_vs_T0": round(saving, 2) if saving is not None else None,
            "fee_saving_pct_vs_T0": round(saving_pct, 4) if saving_pct is not None else None,
            "turnover": turnover,
            "base_turnover": base_turnover,
            "turnover_reduction_pct": round((base_turnover - turnover) / base_turnover, 4)
            if (base_turnover and turnover is not None and base_turnover != 0)
            else None,
            "is_hysteresis": cid in ("T1", "T6"),
        }
    return out


# ---------------------------------------------------------------------------
# Main research orchestration
# ---------------------------------------------------------------------------


def load_and_prepare(config) -> tuple[list[Bar], list[Bar], object, str]:  # type: ignore[no-untyped-def]
    print("Loading 1h bars...")
    bars_1h = load_csv_bars(str(DATASET_PATH), default_market=Market.CRYPTO)
    print(f"  {len(bars_1h):,} 1h bars")
    actual_sha = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    assert actual_sha == DATASET_SHA256, f"dataset SHA mismatch {actual_sha} != {DATASET_SHA256}"
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    print(f"  aggregated {len(bars_1d):,} 1d bars (require_complete=True)")
    universe = build_product_universe(config, bars_1h, market=Market.CRYPTO, end=FULL_END, timeframe="1h")
    h = hash_point_in_time_universe(universe)
    print(f"  universe snapshots={len(universe.snapshots)} hash={h[:16]}...")
    return bars_1h, bars_1d, universe, h


def run_candidate(
    config,
    bars_1d,
    universe,
    candidate_id: str,
    strategy_obj,
    start: datetime,
    end: datetime,
    cost_multiplier: float = 1.0,
):  # type: ignore[no-untyped-def]
    """Run a single candidate over a window. T0 via product helper, T1-T6 via direct engine."""
    if candidate_id == "T0":
        return run_product_backtest(
            config,
            bars_1d,
            market=Market.CRYPTO,
            strategy_name="crypto_low_variance_v2",
            parameters=dict(T0_PARAMS),
            strategy_timeframe="1d",
            start=start,
            end=end,
            cost_multiplier=cost_multiplier,
            persist=False,
            persist_artifacts=False,
            universe=universe,
        )
    from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
    from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
    from parayabuck.portfolio import rebalance_policy_for

    available = [
        b for b in bars_1d if b.market == Market.CRYPTO and b.timeframe == "1d" and (end is None or b.timestamp <= end)
    ]
    filtered = [b for b in available if start is None or b.timestamp >= start]
    if not filtered:
        raise ValueError(f"No CRYPTO 1d bars in requested period {start}..{end}")
    warmup = [b for b in available if start is not None and b.timestamp < start]
    risk = portfolio_risk_limits_from_config(config.risk)
    policy = rebalance_policy_for(strategy_obj, dict(strategy_obj.parameters))
    engine = PortfolioBacktestEngine(
        strategy_obj,
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
        benchmark_symbol="BTC/USD",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=risk,
        maximum_allocation=config.strategy.crypto.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
    )
    result = engine.run(filtered, warmup=warmup, corporate_actions=[], cash_rates=[])

    class _Wrap:
        def __init__(self, res):  # type: ignore[no-untyped-def]
            self.result = res

    return _Wrap(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parity-only", action="store_true", help="only run control parity check")
    parser.add_argument("--full-only", action="store_true", help="only full window, skip walk-forward")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config(str(REPO / "config/settings.yaml"))
    assert config.strategy.crypto.name == "crypto_low_variance_v2", "PAPER crypto must remain v2"
    assert config.strategy.equity.name == "equity_residual_momentum_v3", "equity unchanged"
    assert config.runtime.mode.value == "paper", "mode must be paper"
    assert config.live.enabled is False, "live must be disabled"
    assert config.strategy.crypto.maximum_allocation == 0.05

    bars_1h, bars_1d, universe, universe_hash = load_and_prepare(config)

    effective_cost = {
        "taker_fee_bps": config.backtest.crypto_taker_fee_bps,
        "slippage_bps": config.backtest.slippage_bps,
        "spread_bps": config.backtest.spread_bps,
        "commission": config.backtest.commission,
        "rebalance_band_pct": config.execution.rebalance_band_pct,
        "minimum_order_notional": config.execution.minimum_order_notional,
        "cost_multiplier_levels": [1, 2, 3],
        "maximum_allocation": config.strategy.crypto.maximum_allocation,
    }

    strat_t0 = CryptoLowVariance1dV2(dict(T0_PARAMS))
    strat_t1 = CryptoTournamentT1Hysteresis(dict(T1_PARAMS))
    strat_t2 = CryptoTournamentT2Consensus(dict(T2_PARAMS))
    strat_t3 = CryptoTournamentT3Downside(dict(T3_PARAMS))
    strat_t4 = CryptoTournamentT4Correlation(dict(T4_PARAMS))
    strat_t5 = CryptoTournamentT5InverseVol(dict(T5_PARAMS))
    strat_t6 = CryptoTournamentT6HysteresisInverseVol(dict(T6_PARAMS))

    candidates_map = {
        "T0": strat_t0,
        "T1": strat_t1,
        "T2": strat_t2,
        "T3": strat_t3,
        "T4": strat_t4,
        "T5": strat_t5,
        "T6": strat_t6,
    }

    print("\n=== CONTROL PARITY (T0) ===")
    parity_run = run_candidate(config, bars_1d, universe, "T0", strat_t0, FULL_START, FULL_END, cost_multiplier=1.0)
    parity_result = parity_run.result
    parity_metrics = _detailed_metrics(parity_result, bars_1d)
    print(
        f"  T0 full window return={parity_metrics['total_return']:.4%} cagr={parity_metrics['cagr']:.4%} sharpe={parity_metrics['sharpe']:.3f} mdd={parity_metrics['max_drawdown']:.2%} turnover={parity_metrics['turnover']:.2f} fills={parity_metrics['number_of_fills']}"
    )
    all_selected_symbols: set[str] = set()
    for rb in parity_result.rebalances:
        for sym, _w in rb.target_weights:
            all_selected_symbols.add(sym)
    for bad in ("MKR/USD", "NEAR/USD", "TRX/USD"):
        assert bad not in all_selected_symbols, f"PIT parity violation: {bad} selected in CONTROL"
    print(f"  PIT parity OK: selected universe {sorted(all_selected_symbols)} ; no dataset-only symbols")
    parity_payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {"path": str(DATASET_PATH), "sha256": DATASET_SHA256},
        "universe_hash": universe_hash,
        "universe_snapshots": len(universe.snapshots),
        "control": {
            "name": strat_t0.name,
            "version": strat_t0.version,
            "parameters": T0_PARAMS,
            "maximum_allocation": config.strategy.crypto.maximum_allocation,
        },
        "effective_cost": effective_cost,
        "full_window": parity_metrics,
        "pit_check": {"selected_symbols": sorted(all_selected_symbols), "contains_dataset_only": False},
        "pass": True,
    }
    (OUT_DIR / "control_parity.json").write_text(json.dumps(parity_payload, indent=2, sort_keys=True), encoding="utf-8")
    print("  -> data/research/crypto_candidate_tournament_2026_08/control_parity.json")

    if args.parity_only:
        # also quick smoke for T1-T6 when parity-only? spec says can run parity for T0 and quick smoke for T1-T6
        # keep parity-only truly only T0 unless caller wants smoke; we do minimal extra check: run each T1-T6 for one short window to validate non-zero fills
        print("\n=== QUICK SMOKE T1-T6 (parity-only mode: short window) ===")
        smoke_start = datetime(2024, 1, 1, tzinfo=UTC)
        smoke_end = datetime(2024, 3, 31, 23, 59, 59, tzinfo=UTC)
        for cid in ("T1", "T2", "T3", "T4", "T5", "T6"):
            strat = candidates_map[cid]
            run = run_candidate(config, bars_1d, universe, cid, strat, smoke_start, smoke_end, cost_multiplier=1.0)
            res = run.result
            metrics = _detailed_metrics(res, bars_1d)
            # basic plausibility checks
            assert metrics["number_of_fills"] > 0, f"{cid} smoke fills==0"
            # PIT violation check
            sel = {s for rb in res.rebalances for s, _w in rb.target_weights}
            for bad in ("MKR/USD", "NEAR/USD", "TRX/USD"):
                assert bad not in sel, f"PIT violation {cid} {bad}"
            print(
                f"  {cid} smoke return={metrics['total_return']:.2%} fills={metrics['number_of_fills']} holdings={metrics['average_holdings']:.2f}"
            )
        return 0

    print("\n=== FULL-WINDOW T0-T6 ===")
    full_results: dict[str, dict] = {}
    for cid, strat in candidates_map.items():
        label = CANDIDATES[cid]["label"]
        print(f"  {cid} {label} ...")
        run = run_candidate(config, bars_1d, universe, cid, strat, FULL_START, FULL_END, cost_multiplier=1.0)
        res = run.result
        metrics = _detailed_metrics(res, bars_1d)
        conc = _concentration_metrics(res)
        selected_syms: set[str] = set()
        for rb in res.rebalances:
            for s, _w in rb.target_weights:
                selected_syms.add(s)
        for bad in ("MKR/USD", "NEAR/USD", "TRX/USD"):
            assert bad not in selected_syms, f"PIT parity violation {cid}: {bad}"
        full_results[cid] = {
            "candidate_id": cid,
            "label": label,
            "parameters": CANDIDATES[cid]["params"],
            "strategy_name": strat.name,
            "strategy_version": strat.version,
            "metrics": metrics,
            "concentration": conc,
            "selected_symbols": sorted(selected_syms),
            "_raw_result": res,
        }
        print(
            f"    -> return {metrics['total_return']:.2%} sharpe {metrics['sharpe']:.2f} mdd {metrics['max_drawdown']:.2%} holds {metrics['average_holdings']:.2f} fees {metrics['fees']['total']:.2f}"
        )

    serializable_full = {
        cid: {k: v for k, v in data.items() if k != "_raw_result"} for cid, data in full_results.items()
    }
    (OUT_DIR / "full_window_results.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "window": [FULL_START.isoformat(), FULL_END.isoformat()],
                "universe_hash": universe_hash,
                "effective_cost": effective_cost,
                "candidates": serializable_full,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("  -> full_window_results.json")

    print("\n=== WALK-FORWARD (9 comparable folds) ===")
    walk_forward: dict[str, dict] = {}
    for cid, strat in candidates_map.items():
        label = CANDIDATES[cid]["label"]
        fold_stats_list = []
        for fid, fstart, fend in WALK_FOLDS:
            run = run_candidate(config, bars_1d, universe, cid, strat, fstart, fend, cost_multiplier=1.0)
            res = run.result
            stats = {
                "fold_id": fid,
                "start": fstart.isoformat(),
                "end": fend.isoformat(),
                "return": round(res.total_return, 6),
                "cagr": round(res.cagr, 6),
                "sharpe": round(res.sharpe, 6),
                "sortino": round(res.sortino, 6) if hasattr(res, "sortino") else None,
                "calmar": round(res.calmar, 6) if hasattr(res, "calmar") else None,
                "max_drawdown": round(res.max_drawdown, 6),
                "turnover": round(res.turnover, 6),
                "trade_count": len(res.closed_position_events),
                "rebalance_count": res.rebalance_count,
            }
            fold_stats_list.append(stats)
            print(f"    {cid} {fid} return={stats['return']:.2%} sharpe={stats['sharpe']:.2f}")
        comparable = [s for s in fold_stats_list if s["fold_id"] in COMPARABLE_FOLD_IDS]
        rets = [s["return"] for s in comparable]
        sharpes = [s["sharpe"] for s in comparable if s["sharpe"] is not None]
        positive = sum(1 for r in rets if r > 0)
        walk_forward[cid] = {
            "candidate_id": cid,
            "label": label,
            "folds": fold_stats_list,
            "comparable_folds": COMPARABLE_FOLD_IDS,
            "positive_fold_count": positive,
            "negative_fold_count": len(rets) - positive,
            "total_comparable_folds": len(rets),
            "positive_fold_ratio": round(positive / len(rets), 4) if rets else 0.0,
            "mean_fold_return": round(statistics.fmean(rets), 6) if rets else None,
            "median_fold_return": round(statistics.median(rets), 6) if rets else None,
            "median_fold_sharpe": round(statistics.median(sharpes), 4) if sharpes else None,
            "worst_fold": min(comparable, key=lambda x: x["return"]) if comparable else None,
            "best_fold": max(comparable, key=lambda x: x["return"]) if comparable else None,
            "methodology": "independent calendar 6-month folds, fresh engine state, own PIT warmup, trailing only",
        }
    control_rets = {
        s["fold_id"]: s["return"] for s in walk_forward["T0"]["folds"] if s["fold_id"] in COMPARABLE_FOLD_IDS
    }
    for cid in ("T1", "T2", "T3", "T4", "T5", "T6"):
        beats = sum(
            1
            for s in walk_forward[cid]["folds"]
            if s["fold_id"] in COMPARABLE_FOLD_IDS and s["return"] > control_rets.get(s["fold_id"], float("-inf"))
        )
        walk_forward[cid]["beats_control"] = f"{beats}/{len(COMPARABLE_FOLD_IDS)}"
        walk_forward[cid]["beats_control_ratio"] = round(beats / len(COMPARABLE_FOLD_IDS), 4)
    (OUT_DIR / "walk_forward.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "window": [FULL_START.isoformat(), FULL_END.isoformat()],
                "universe_hash": universe_hash,
                "walk_forward": walk_forward,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("  -> walk_forward.json")

    if args.full_only:
        return 0

    print("\n=== COST STRESS ===")
    cost_stress: dict[str, dict] = {}
    for cid, strat in candidates_map.items():
        label = CANDIDATES[cid]["label"]
        per_mult: dict[str, dict] = {}
        for mult in (1.0, 2.0, 3.0):
            run = run_candidate(config, bars_1d, universe, cid, strat, FULL_START, FULL_END, cost_multiplier=mult)
            res = run.result
            total_cost = res.slippage_cost_total + res.spread_cost_total + res.fee_total
            per_mult[f"{mult:g}x"] = {
                "return": round(res.total_return, 6),
                "cagr": round(res.cagr, 6),
                "sharpe": round(res.sharpe, 6),
                "max_drawdown": round(res.max_drawdown, 6),
                "turnover": round(res.turnover, 6),
                "fees_total": round(total_cost, 2),
                "trade_count": len(res.closed_position_events),
                "net_positive": res.total_return > 0,
            }
        cost_stress[cid] = {"candidate_id": cid, "label": label, "stress": per_mult}
        print(
            f"  {cid} 1x {per_mult['1x']['return']:.2%} 2x {per_mult['2x']['return']:.2%} 3x {per_mult['3x']['return']:.2%}"
        )
    (OUT_DIR / "cost_stress.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "effective_cost": effective_cost,
                "cost_stress": cost_stress,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("  -> cost_stress.json")

    print("\n=== REGIME ANALYSIS ===")
    regime_analysis: dict[str, dict] = {}
    for cid, data in full_results.items():
        res = data["_raw_result"]
        regime = _regime_analysis(res, bars_1d)
        regime_analysis[cid] = {"candidate_id": cid, "label": CANDIDATES[cid]["label"], "regime": regime}
        print(
            f"  {cid} BULL {regime['regimes']['BULL']['total_return']:.2%} NEUTRAL {regime['regimes']['NEUTRAL']['total_return']:.2%} BEAR {regime['regimes']['BEAR']['total_return']:.2%}"
        )
    (OUT_DIR / "regime_analysis.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(), "regime_analysis": regime_analysis},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("  -> regime_analysis.json")

    print("\n=== PNL CONCENTRATION ===")
    pnl_concentration: dict[str, dict] = {}
    for cid, data in full_results.items():
        res = data["_raw_result"]
        conc = _concentration_metrics(res)
        pnl_concentration[cid] = {"candidate_id": cid, "label": CANDIDATES[cid]["label"], "concentration": conc}
        print(f"  {cid} top1 {conc['top1_share_of_positive_pnl']:.1%} top2 {conc['top2_share_of_positive_pnl']:.1%}")
    (OUT_DIR / "pnl_concentration.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(), "pnl_concentration": pnl_concentration},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("  -> pnl_concentration.json")

    print("\n=== JACKKNIFE ===")
    jackknife: dict[str, dict] = {}
    for cid, data in full_results.items():
        label = CANDIDATES[cid]["label"]
        conc = pnl_concentration[cid]["concentration"]
        top_sym = conc["top_1"]["symbol"] if conc["top_1"] else None
        symbols_to_test = ["BTC/USD", "ETH/USD", "SOL/USD", "SHIB/USD"]
        if top_sym and top_sym not in symbols_to_test:
            symbols_to_test.append(top_sym)
        per_symbol_result: dict[str, dict] = {}
        for sym in symbols_to_test:
            filtered_bars = [b for b in bars_1d if b.symbol != sym]
            run = run_candidate(
                config, filtered_bars, universe, cid, candidates_map[cid], FULL_START, FULL_END, cost_multiplier=1.0
            )
            res = run.result
            metrics = _detailed_metrics(res, filtered_bars)
            per_symbol_result[f"without_{sym.replace('/', '_')}"] = {
                "removed": sym,
                "return": metrics["total_return"],
                "cagr": metrics["cagr"],
                "sharpe": metrics["sharpe"],
                "max_drawdown": metrics["max_drawdown"],
                "directionally_positive": metrics["total_return"] > 0 and metrics["sharpe"] > 0,
            }
            print(f"  {cid} without {sym}: return {metrics['total_return']:.2%}")
        jackknife[cid] = {"candidate_id": cid, "label": label, "leave_one_out": per_symbol_result}
    (OUT_DIR / "jackknife.json").write_text(
        json.dumps({"generated_at": datetime.now(UTC).isoformat(), "jackknife": jackknife}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("  -> jackknife.json")

    print("\n=== TURNOVER ANALYSIS ===")
    turnover_analysis: dict[str, dict] = {}
    for cid, data in full_results.items():
        res = data["_raw_result"]
        ta = _turnover_analysis(res)
        turnover_analysis[cid] = {"candidate_id": cid, "label": CANDIDATES[cid]["label"], "turnover": ta}
        print(
            f"  {cid} turnover {ta['selection_turnover']:.3f} jaccard {ta['jaccard_overlap']['mean']} repl {ta['replacement_frequency']:.2%} weight {ta['weight_turnover']['mean']}"
        )
    (OUT_DIR / "turnover_analysis.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(), "turnover_analysis": turnover_analysis},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("  -> turnover_analysis.json")

    print("\n=== FEE SAVINGS (T1/T6) ===")
    fee_savings = _fee_savings_analysis(full_results)
    for cid, fs in fee_savings.items():
        print(
            f"  {cid} saving {fs['fee_saving_vs_T0']} ({fs['fee_saving_pct_vs_T0']}) turnover_red {fs['turnover_reduction_pct']}"
        )
    (OUT_DIR / "fee_savings.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "fee_savings": fee_savings,
                "methodology": "fees total vs T0 full window; hysteresis expected to reduce turnover/fees",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("  -> fee_savings.json")

    print("\n=== WEIGHT TURNOVER ===")
    weight_turnover: dict[str, dict] = {}
    for cid, ta in turnover_analysis.items():
        weight_turnover[cid] = {
            "candidate_id": cid,
            "label": CANDIDATES[cid]["label"],
            "weight_turnover": ta["turnover"]["weight_turnover"],
        }
    (OUT_DIR / "weight_turnover.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(), "weight_turnover": weight_turnover},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("  -> weight_turnover.json")

    print("\n=== FOLD DEPENDENCE ===")
    fold_dependence = _fold_dependence(walk_forward, control_id="T0")
    for cid, fd in fold_dependence.items():
        print(
            f"  {cid} largest {fd['largest_positive_fold']} {fd['largest_positive_excess']:.2%} ratio {fd['largest_over_total_positive']:.2%} flagged {fd['flag_dependent_on_single_fold']}"
        )
    (OUT_DIR / "fold_dependence.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(), "fold_dependence": fold_dependence},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("  -> fold_dependence.json")

    print("\n=== CONCENTRATION ===")
    concentration: dict[str, dict] = {}
    for cid, data in full_results.items():
        concentration[cid] = {
            "candidate_id": cid,
            "label": CANDIDATES[cid]["label"],
            "concentration": pnl_concentration[cid]["concentration"],
        }
    (OUT_DIR / "concentration.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(), "concentration": concentration}, indent=2, sort_keys=True
        ),
        encoding="utf-8",
    )
    print("  -> concentration.json")

    print("\n=== UNIVERSE IDENTITY ===")
    # config digest
    try:
        from scripts.research_helpers import config_digest

        cd = config_digest(config)
    except Exception:
        cd = "unknown"
    universe_identity = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {"path": str(DATASET_PATH), "sha256": DATASET_SHA256},
        "universe_hash": universe_hash,
        "universe_snapshots": len(universe.snapshots),
        "config_digest": cd,
        "full_window": [FULL_START.isoformat(), FULL_END.isoformat()],
        "effective_cost": effective_cost,
    }
    (OUT_DIR / "universe_identity.json").write_text(
        json.dumps(universe_identity, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("  -> universe_identity.json")

    print("\n=== FINAL VERDICT ===")
    # simple gates: check per-candidate beats control on sharpe and not dependent
    gates: dict[str, dict[str, bool]] = {}
    for cid in ("T1", "T2", "T3", "T4", "T5", "T6"):
        c_metrics = full_results[cid]["metrics"]
        t0_metrics = full_results["T0"]["metrics"]
        gates[cid] = {
            "sharpe_gt_control": c_metrics["sharpe"] > t0_metrics["sharpe"],
            "cagr_gt_control_or_lower_dd": (c_metrics["cagr"] > t0_metrics["cagr"])
            or (
                c_metrics["max_drawdown"] < t0_metrics["max_drawdown"] * 0.85
                and c_metrics["cagr"] >= t0_metrics["cagr"] * 0.95
            ),
            "not_fold_dependent": not fold_dependence.get(cid, {}).get("flag_dependent_on_single_fold", False),
            "pit_parity": True,
        }
    verdict = "TOURNAMENT COMPLETE — see per-candidate gates"
    (OUT_DIR / "final_verdict.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "universe_hash": universe_hash,
                "gates": gates,
                "verdict": verdict,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"  -> final_verdict.json : {verdict}")

    print("\nDone. All artifacts in", OUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
