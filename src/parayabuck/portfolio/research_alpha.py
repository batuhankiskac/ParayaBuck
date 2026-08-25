#!/usr/bin/env python3
"""Pure research helpers for the equity alpha optimization mission (2026-08).

These functions implement the PRE-DECLARED candidate components:

* ``percentile_ranks`` / ``blend_scores`` — robust cross-sectional rank
  normalization + raw/standardized momentum blending (H-family);
* ``horizon_consensus_scores`` — multi-horizon residual-momentum consensus
  (6-1 / 9-1 / 12-1 months), optionally standardized per horizon (MH_STD);
* ``sqrt_ivol_weights`` — sqrt-inverse-volatility position sizing (SQRT);
* ``spy_regime_state`` — SPY trailing-return / realized-vol regime classifier
  with the frozen exposure multiplier (regime scaling);
* ``hysteresis_selection`` — entry-hold-exit hysteresis around the selection
  boundary (CONTROL_HYST).

All functions are PURE, deterministic and lookahead-free (they receive only
point-in-time closes / residuals; every horizon window ends at the declared
skip bar). They are research-only: production strategy code does not import
them. The research harness (``scripts/research_equity_alpha_optimization.py``)
hosts the variant strategy classes that consume them; production strategy
defaults are untouched by construction.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence

NORMAL = "NORMAL"
ELEVATED = "ELEVATED"
# FROZEN for this mission (predeclared; never tuned per result).
RETURN_WINDOW = 252  # SPY trailing return window
VOL_WINDOW = 63  # SPY realized-volatility window
MEDIAN_YEARS = 3  # trailing window for the rolling 63d-vol median
REDUCED_MULTIPLIER = 0.5  # ELEVATED-regime exposure multiplier


def percentile_ranks(scores: Mapping[str, float]) -> dict[str, float]:
    """Robust cross-sectional percentile rank, ties averaged.

    ``pct(s) = (count strictly below + 0.5 * count equal) / N`` — always in
    (0, 1) for a non-empty sample (minimum value -> 0.5/N, maximum value ->
    1 - 0.5/N), deterministic for any input order, scale-free, so raw and
    standardized momentum scores can be blended without unit clashes.
    """
    if not scores:
        return {}
    ordered = sorted(scores.values())
    sizes: dict[float, int] = {}
    for value in ordered:
        sizes[value] = sizes.get(value, 0) + 1
    below: dict[float, int] = {}
    running = 0
    for value, count in sorted(sizes.items()):
        below[value] = running
        running += count
    n = len(ordered)
    return {symbol: (below[scores[symbol]] + 0.5 * sizes[scores[symbol]]) / n for symbol in scores}


def blend_scores(
    raw: Mapping[str, float],
    standardized: Mapping[str, float],
    alpha_raw: float,
) -> dict[str, float]:
    """Blend raw and standardized momentum on their percentile ranks.

    ``final = alpha_raw * pct(raw) + (1 - alpha_raw) * pct(standardized)``.
    Both inputs are rank-converted first so the incompatible raw scales are
    never merged directly. ``alpha_raw=1`` is pure raw, ``=0`` is pure
    standardized (the blend family: H25/H50/H75 declare 0.75/0.50/0.25).
    """
    raw_pct = percentile_ranks(raw)
    std_pct = percentile_ranks(standardized)
    return {symbol: alpha_raw * raw_pct[symbol] + (1.0 - alpha_raw) * std_pct[symbol] for symbol in raw}


def _compounded(residuals: Sequence[float]) -> float:
    """Compounded residual return over the window (deterministic, finite)."""
    value = 1.0
    for residual in residuals:
        value *= 1.0 + residual
        if not math.isfinite(value) or value <= 0:
            return float("-inf")
    return value - 1.0


def _standardized(residuals: Sequence[float]) -> float:
    """Compounded residual return / window stdev (A-family score)."""
    compounded = _compounded(residuals)
    if not math.isfinite(compounded) or len(residuals) < 2:
        return float("-inf")
    mean = statistics.fmean(residuals)
    variance = sum((value - mean) ** 2 for value in residuals) / (len(residuals) - 1)
    if variance <= 0:
        return float("-inf")
    return compounded / math.sqrt(variance)


def horizon_consensus_scores(
    residuals_by_symbol: Mapping[str, Sequence[float]],
    horizons: Sequence[int],
    skip: int,
    *,
    standardized: bool,
) -> dict[str, float]:
    """Multi-horizon residual-momentum consensus score per symbol.

    For every horizon ``h`` the score is computed over residual bars
    ``[-(h + skip), -skip)`` — the last ``skip`` bars are never included, so
    decisions can never use the not-yet-completed skip window (same
    no-lookahead construction as the base momentum window). Each horizon's
    scores are optionally standardized by their own window stdev
    (``standardized=True``), percentile-ranked cross-sectionally, and the
    three rank-normalized scores averaged into the final consensus.

    Symbols that cannot supply a full horizon window are excluded from that
    horizon's percentile (the qualified pool always supplies 273 residuals,
    so in practice this never triggers).
    """
    if not horizons or skip < 1 or skip >= min(horizons):
        raise ValueError("horizons must be non-empty and skip smaller than every horizon")
    horizons_order = sorted(set(horizons))
    if any(horizon < skip for horizon in horizons_order):
        raise ValueError("every horizon must be larger than skip")
    if len(residuals_by_symbol) < 2:
        raise ValueError("multi-horizon consensus needs at least two eligible symbols")
    per_horizon: list[dict[str, float]] = []
    for horizon in horizons_order:
        scores: dict[str, float] = {}
        for symbol, residuals in residuals_by_symbol.items():
            if len(residuals) < horizon + skip:
                continue
            window = residuals[-(horizon + skip) : -skip]
            if standardized:
                score = _standardized(window)
            else:
                score = _compounded(window)
            if math.isfinite(score):
                scores[symbol] = score
        if len(scores) == 1:
            # A single remaining symbol cannot be percentile-ranked
            # meaningfully (both its rank and the consensus would be 1.0);
            # surface the degradation instead of silently emitting 1.0.
            continue
        if not scores:
            return {}
        per_horizon.append(percentile_ranks(scores))
    if len(per_horizon) < 2:
        return {}
    consensus: dict[str, float] = {}
    for symbol in per_horizon[0]:
        consensus[symbol] = sum(horizon[symbol] for horizon in per_horizon) / len(per_horizon)
    return consensus


def sqrt_ivol_weights(
    selected: Sequence[str],
    volatilities: Mapping[str, float],
    *,
    maximum_weight: float,
) -> dict[str, float]:
    """Square-root-inverse-volatility sizes, capped at ``maximum_weight``.

    Raw weight = ``1 / sqrt(annualized residual vol)``; the raw weights are
    normalized to sum to 1 and then each is capped — the unallocated residual
    stays cash (same cap semantics as ``inverse_volatility_weights``). This
    is the intermediate sizing rule between inverse-vol and equal weighting.
    """
    raw: dict[str, float] = {}
    for symbol in selected:
        volatility = volatilities.get(symbol, 0.0)
        raw[symbol] = 1.0 / math.sqrt(volatility) if volatility > 0 else 1.0
    total = sum(raw.values())
    if total <= 0:
        return {}
    weights = {symbol: value / total for symbol, value in raw.items()}
    return {symbol: min(value, maximum_weight) for symbol, value in weights.items()}


def _returns_from_closes(closes: Sequence[float]) -> list[float]:
    return [
        closes[index] / closes[index - 1] - 1
        for index in range(1, len(closes))
        if closes[index - 1] > 0 and closes[index] > 0
    ]


def spy_regime_state(
    closes: Sequence[float],
    *,
    return_window: int = RETURN_WINDOW,
    vol_window: int = VOL_WINDOW,
    median_years: int = MEDIAN_YEARS,
    reduced_multiplier: float = REDUCED_MULTIPLIER,
) -> tuple[str, float]:
    """Classify the SPY market regime from closes available AT THE DECISION.

    ELEVATED when, jointly:
      trailing ``return_window``-day SPY return < 0 AND the trailing
      ``vol_window``-day annualized realized vol > the rolling median of
      ``vol_window``-day vols across the trailing ``median_years *
      return_window`` days.
    NORMAL otherwise.

    With insufficient rolling-median history (early sample) the rule falls
    back conservatively to NORMAL — no exposure scaling — per the predeclared
    spec. No lookahead: every close in the input precedes the decision, and
    the trailing-window constructions use only earlier closes.

    Returns ``("ELEVATED", 0.5)`` or ``("NORMAL", 1.0)``.
    """
    n = len(closes)
    if n < return_window + 2 or n < return_window * median_years:
        # Cannot measure the 252-day return and/or the 3y rolling median yet.
        # Conservative on all sides: no scaling.
        return NORMAL, 1.0
    trailing_return = closes[-1] / closes[-return_window - 1] - 1
    returns = _returns_from_closes(closes)
    recent = returns[-vol_window:]
    recent_vol = statistics.stdev(recent) * math.sqrt(252)
    start = max(0, n - return_window * median_years - vol_window)
    vols: list[float] = []
    for end in range(start + vol_window, n):
        segment = returns[end - vol_window : end]
        if len(segment) >= 2:
            vols.append(statistics.stdev(segment) * math.sqrt(252))
    if not vols:
        return NORMAL, 1.0
    median_vol = statistics.median(vols)
    if trailing_return < 0 and recent_vol > median_vol:
        return ELEVATED, reduced_multiplier
    return NORMAL, 1.0


def hysteresis_selection(
    ranked: Sequence[tuple[float, str]],
    holdings_qty: Mapping[str, float],
    *,
    top_n: int,
    entry_rank: int = 8,
    hold_rank: int = 12,
) -> tuple[str, ...]:
    """Entry-hold-exit hysteresis on the top-N selection boundary.

    * New positions enter only at rank ``<= entry_rank``;
    * positions already held survive while rank ``<= hold_rank`` (wider than
      the entry band);
    * a held name whose rank is worse than ``hold_rank`` — or that is no
      longer in the ranked eligible pool at all (failed an eligibility
      gate) — exits;
    * with fewer than ``top_n`` selected, the free slots fill from the best
      remaining entry-eligible (``rank <= entry_rank``) names.

    ``ranked`` arrives score-descending (as in ``_score_candidates``);
    ``holdings_qty`` is the current book (symbol -> qty, > 0 = held).
    """
    ranks: dict[str, int] = {symbol: index + 1 for index, (_score, symbol) in enumerate(ranked)}
    kept = [
        symbol for symbol in holdings_qty if holdings_qty[symbol] > 0 and symbol in ranks and ranks[symbol] <= hold_rank
    ]
    kept.sort(key=lambda symbol: ranks[symbol])
    selected = kept[:top_n]
    if len(selected) < top_n:
        for _score, symbol in ranked:
            if len(selected) >= top_n:
                break
            if symbol not in selected and ranks[symbol] <= entry_rank:
                selected.append(symbol)
    return tuple(selected)
