"""Production-capable MH_RAW multi-horizon residual-momentum identity (v3).

``EquityResidualMomentum1dV3`` implements EXACTLY the behavior validated by
the 2026-08 MH_RAW promotion-validation mission:

    positive 12-1 residual momentum gate
    -> multi-horizon rank consensus (6-1 / 9-1 / 12-1 months)
    -> ivol q=0.70 rejection -> top_n=8 -> inverse-vol sizing
    -> production risk overlay (unchanged)

Semantics contract (frozen; see the validation manifest):

* ``_candidate_score`` stays the production DEFAULT 12-1 compounded
  residual (formation 252 / skip 21), so the positive-12m gate in
  ``_score_candidates`` runs BEFORE any cross-sectional re-scoring.
  A symbol whose 12-1 residual momentum is not positive is excluded and
  never reaches the consensus — this is the frozen researched behavior
  (``positive_12m_gate = true``), NOT an unrestricted 6/9/12 cross-section.
* ``_transform_cross_sectional_scores`` replaces the raw score with the
  equal mean of the per-horizon percentile ranks of raw (unstandardized)
  residual momentum at 6-1 / 9-1 / 12-1 months (126 / 189 / 252 residual
  bars, skip 21, exactly like the research ``horizon_consensus_scores``
  with ``standardized=False``).
* All other stages (idiosyncratic-vol rejection quantile 0.70, top_n 8,
  inverse-vol sizing capped at 0.10, SPY exclusion, weekly cadence, risk
  overlay) are inherited unchanged from the validated production base.

This module has NO research-module dependency: the minimal consensus math
is promoted here as pure, deterministic production code (bit-identical to
the researched helper; parity is locked by tests). The strategy is selected
for PAPER in ``config/settings.yaml``; LIVE remains disabled.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from parayabuck.portfolio.equity_residual_momentum import EquityResidualMomentum1dV2

# Horizon lengths in RESIDUAL bars for the 6-1 / 9-1 / 12-1 consensus
# (21 bars = 1 trading month). FROZEN by the MH_RAW promotion mission.
_DEFAULT_HORIZONS = (6 * 21, 9 * 21, 12 * 21)


def percentile_ranks(scores: Mapping[str, float]) -> dict[str, float]:
    """Cross-sectional percentile rank with averaged ties.

    ``pct(s) = (count strictly below + 0.5 * count equal) / N`` — always in
    (0, 1) for a non-empty sample, deterministic for any input order. This
    is the same tie-averaged definition the research consensus uses.
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


def _compounded(residuals: Sequence[float]) -> float:
    """Compounded residual return over the window (deterministic, finite)."""
    value = 1.0
    for residual in residuals:
        value *= 1.0 + residual
        if not math.isfinite(value) or value <= 0:
            return float("-inf")
    return value - 1.0


def multi_horizon_consensus(
    residuals_by_symbol: Mapping[str, Sequence[float]],
    horizons: Sequence[int],
    skip: int,
) -> dict[str, float]:
    """Raw multi-horizon consensus score per symbol.

    For every horizon ``h`` the raw compounded residual-momentum score is
    computed over residual bars ``[-(h + skip), -skip)``, percentile-ranked
    cross-sectionally, and the three rank-normalized scores are averaged
    with EQUAL weight. Mirrors research ``horizon_consensus_scores`` with
    ``standardized=False``: identical windows, identical tie handling,
    identical single-symbol-horizon degradation semantics. The last
    ``skip`` bars never enter any horizon window (no lookahead).
    """
    if not horizons or skip < 1 or skip >= min(horizons):
        raise ValueError("horizons must be non-empty and skip smaller than every horizon")
    horizons_order = sorted(set(horizons))
    if any(horizon < skip for horizon in horizons_order):
        raise ValueError("every horizon must be larger than skip")
    # A qualified pool with fewer than two symbols cannot support a
    # cross-sectional percentile rank.  Return no consensus so the shared
    # portfolio pipeline can take its normal cash/risk-off fallback instead of
    # turning a thin PAPER universe into a runtime exception.
    if len(residuals_by_symbol) < 2:
        return {}
    per_horizon: list[dict[str, float]] = []
    for horizon in horizons_order:
        scores: dict[str, float] = {}
        for symbol, residuals in residuals_by_symbol.items():
            if len(residuals) < horizon + skip:
                continue
            score = _compounded(residuals[-(horizon + skip) : -skip])
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
    common_symbols = set(per_horizon[0]).intersection(*(set(horizon_scores) for horizon_scores in per_horizon[1:]))
    if len(common_symbols) < 2:
        return {}
    consensus: dict[str, float] = {}
    for symbol in sorted(common_symbols):
        consensus[symbol] = sum(horizon[symbol] for horizon in per_horizon) / len(per_horizon)
    return consensus


class EquityResidualMomentum1dV3(EquityResidualMomentum1dV2):
    """MH_RAW consensus identity: 12-1 positive gate + 6/9/12 rank consensus.

    Exact parity with the research MH_RAW variant is locked by
    tests/test_equity_residual_momentum_v3.py — identical ranks, selected
    names and target weights given identical ``PortfolioContext`` values.
    """

    name = "equity_residual_momentum_v3"
    version = "3.0.0"
    display_name = "Residual Momentum v3 (MH-RAW consensus)"
    rebalance_policy_declared = "weekly"
    cadence_label = "weekly"
    _include_paper_telemetry_details = True

    _HORIZONS = _DEFAULT_HORIZONS

    def _transform_cross_sectional_scores(
        self,
        scored: list[tuple[float, str]],
        residuals_by_symbol: dict[str, list[float]],
        residual_vols: dict[str, float],
        *,
        formation: int,
        skip: int,
    ) -> list[tuple[float, str]]:
        consensus = multi_horizon_consensus(residuals_by_symbol, self._HORIZONS, skip)
        # A symbol can be absent from an individual horizon when direct users
        # supply incomplete histories.  Keep only fully rankable names and
        # fail closed to the inherited cash fallback if none remain.
        return [(consensus[symbol], symbol) for _score, symbol in scored if symbol in consensus]

    # ``decide`` is inherited: the positive-12m gate, ivol filter, selection,
    # inverse-vol sizing, risk overlay and explanation metadata are the exact
    # production pipeline; only the cross-sectional ranking differs.

    # NOTE: this class intentionally does not subclass the portfolio
    # registry's hooks beyond the one override above, so all lifecycle
    # (validate, spec, parameter_tuple, explanations) is inherited verbatim.


__all__ = ["EquityResidualMomentum1dV3", "multi_horizon_consensus", "percentile_ranks"]
