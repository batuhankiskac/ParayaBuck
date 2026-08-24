"""MH_RAW v3 parity suite: production v3 === research MH_RAW.

Given identical ``PortfolioContext`` values, the production-capable
``EquityResidualMomentum1dV3`` must produce the EXACT ranking, selection and
target weights of the research MH_RAW variant
(``scripts/research_equity_alpha_optimization.py`` ``AlphaResearchMHRaw``).
The suite exercises the mission's six declared snapshots:

1. ordinary market snapshot;
2. tie-heavy snapshot (identical residual paths);
3. missing-symbol snapshot (short history + not-in-universe);
4. negative-12m candidate (excluded by the positive 12-1 gate, IN ORDER);
5. conflicting 6m vs 12m momentum (12-1 positive but 6-1/9-1 negative);
6. split-adjusted signal history (whole-series price rescale).

The gate-order regression is explicit: a symbol whose 6-1 momentum is
strongly positive but whose 12-1 momentum is negative MUST be excluded by
BOTH implementations — proving the positive 12-1 gate runs BEFORE the
multi-horizon consensus (``positive_12m_gate = true``).
"""

from __future__ import annotations

import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import (
    EquityResidualMomentum1dV3,
    portfolio_strategy_from,
    supported_portfolio_strategies,
)
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_residual_momentum import EquityResidualMomentum1dV2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_equity_alpha_optimization as research  # noqa: E402

FROZEN_PARAMS = {
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

START = datetime(2024, 1, 1, tzinfo=UTC)


def _series(segments: list[tuple[int, float]], n: int = 300) -> list[float]:
    """Deterministic price path: consecutive segments of (bars, daily pct)."""
    closes: list[float] = []
    current = 100.0
    for bars, pct in segments:
        for _ in range(bars):
            current *= 1.0 + pct
            closes.append(current)
    while len(closes) < n:
        current *= 1.0 + segments[-1][1]
        closes.append(current)
    return closes[:n]


def _noisy(closes: list[float], phase: float, amplitude: float = 0.004) -> list[float]:
    """Add deterministic daily noise so residual vol is strictly positive.

    The noise is a pure ``sin`` term: identical inputs with identical phase
    produce identical outputs (AAA == DDD == EEE tie block preserved), and a
    whole-series rescale (split basis) leaves returns unchanged, so the
    tie-heavy and split-adjusted scenarios keep their meaning.
    """
    return [
        close * (1.0 + amplitude * math.sin(index * 1.7 + phase))
        for index, close in enumerate(closes)
    ]


def _bars(symbol: str, closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            START + timedelta(days=index),
            symbol,
            "1d",
            close,
            close,
            close,
            close,
            1_000_000,
            Market.EQUITY,
        )
        for index, close in enumerate(closes)
    )


def _paths() -> dict[str, list[float]]:
    flat_spy = [100.0] * 300
    pos = _series([(300, 0.004)])  # strong positive momentum, all horizons
    neg = _series([(300, -0.004)])  # strong negative 12-1 -> gate excluded
    conflict = _series([(140, 0.006), (160, -0.003)])  # 12-1 positive, 6-1/9-1 negative
    gate12 = _series([(180, -0.005), (120, 0.0045)])  # 12-1 negative but 6-1 strongly positive
    return {
        "SPY": flat_spy,
        "AAA": _noisy(pos, 0.0),
        "DDD": _noisy(pos, 0.0),  # exact tie with AAA
        "EEE": _noisy([value * 0.5 for value in pos], 0.0),  # split-adjusted scale of AAA
        "CCC": _noisy(conflict, 1.0),
        "GATE12": _noisy(gate12, 2.0),
        "BBB": _noisy(neg, 3.0),
        "HYV": [100.0 * (1.1 if index % 2 else 0.9) for index in range(300)],
        "SHORT": _noisy(pos, 4.0)[:200],  # insufficient history
        "OUTU": _noisy(pos, 5.0),  # not in universe
    }


class _Universe:
    """Fake PIT universe: ``contains`` answers from a member set."""

    def __init__(self, members: set[str]) -> None:
        self._members = set(members)

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return symbol in self._members


def _context(paths: dict[str, list[float]], universe_members: set[str]) -> PortfolioContext:
    last = max(len(closes) for closes in paths.values()) - 1
    history = {symbol: _bars(symbol, closes) for symbol, closes in paths.items()}
    return PortfolioContext(
        decision_timestamp=START + timedelta(days=last),
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=_Universe(universe_members),
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )


def _research_mh_raw() -> EquityResidualMomentum1dV2:
    return research.build_strategy("mh_raw", dict(FROZEN_PARAMS))


def _production_v3() -> EquityResidualMomentum1dV3:
    return portfolio_strategy_from("equity_residual_momentum_v3", dict(FROZEN_PARAMS))


def _ranked(strategy, context) -> tuple[str, ...]:
    return strategy.rank_candidates(context)


def _decided(strategy, context) -> dict[str, tuple[float, int]]:
    portfolio = strategy.decide(context)
    return {weight.symbol: (round(weight.weight, 10), weight.rank) for weight in portfolio.weights}


# --------------------------------------------------------------------------- #
# Scenario 0 — identity & registration (v3 is PAPER-product enabled)
# --------------------------------------------------------------------------- #


def test_v3_identity_and_product_enabled() -> None:
    v3 = _production_v3()
    assert v3.name == "equity_residual_momentum_v3"
    assert v3.version == "3.0.0"
    assert dict(v3.parameter_tuple())["rebalance_frequency"] == "weekly"
    assert "equity_residual_momentum_v3" in supported_portfolio_strategies()


# --------------------------------------------------------------------------- #
# Scenario 1: ordinary snapshot — every stage identical
# --------------------------------------------------------------------------- #


def test_ranking_parity_ordinary_snapshot() -> None:
    paths = _paths()
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV"})
    research_ranked = _ranked(_research_mh_raw(), context)
    v3_ranked = _ranked(_production_v3(), context)
    assert research_ranked == v3_ranked
    # The market leader leads; the conflict name IS eligible (12-1 positive
    # despite 6-1/9-1 negative); negative-12-1 names are excluded.
    assert research_ranked[0] == "AAA"
    assert "CCC" in research_ranked
    assert "BBB" not in research_ranked
    assert "GATE12" not in research_ranked


def test_target_parity_ordinary_snapshot() -> None:
    paths = _paths()
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV", "OUTU"})
    assert _decided(_research_mh_raw(), context) == _decided(_production_v3(), context)


def test_v3_paper_diagnostics_reuse_final_scoring_outputs() -> None:
    paths = _paths()
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV"})
    target = _production_v3().decide(context)
    assert target.metadata["ranked_symbols"] == target.metadata["qualified_symbols"]
    explanations = target.metadata["explanations"]
    assert isinstance(explanations, dict)
    for symbol, explanation in explanations.items():
        assert explanation["consensus_score"] == explanation["residual_momentum_score"]
        assert explanation["residual_volatility"] > 0.0
        assert explanation["alpha_rank"] >= 1
        assert explanation["target_weight"] == pytest.approx(
            next(weight.weight for weight in target.weights if weight.symbol == symbol)
        )


# --------------------------------------------------------------------------- #
# Scenario 2: tie-heavy snapshot
# --------------------------------------------------------------------------- #


def test_ranking_parity_tie_heavy() -> None:
    paths = _paths()
    context = _context(paths, {"AAA", "DDD", "EEE", "BBB", "CCC", "GATE12", "HYV"})
    research_ranked = _ranked(_research_mh_raw(), context)
    v3_ranked = _ranked(_production_v3(), context)
    assert research_ranked == v3_ranked
    # Tied residual paths score identically (ties averaged in ranks) and the
    # deterministic sort places the tie block adjacent in both.
    positions = [research_ranked.index(name) for name in ("AAA", "DDD", "EEE")]
    assert max(positions) - min(positions) == 2
    assert research_ranked.count("AAA") == 1


# --------------------------------------------------------------------------- #
# Scenario 3: missing-symbol snapshot
# --------------------------------------------------------------------------- #


def test_ranking_parity_missing_symbols() -> None:
    paths = _paths()
    # SHORT (too little history) and OUTU (not in universe) must drop out
    # identically in research and v3.
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV"})
    research_ranked = _ranked(_research_mh_raw(), context)
    assert "SHORT" not in research_ranked
    assert "OUTU" not in research_ranked
    assert _ranked(_production_v3(), context) == research_ranked


# --------------------------------------------------------------------------- #
# Scenario 4: negative-12m candidate — gate ordering regression
# --------------------------------------------------------------------------- #


def test_negative_12m_candidate_excluded_in_both() -> None:
    """The positive 12-1 gate runs BEFORE the multi-horizon consensus."""
    paths = _paths()
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV"})
    research_ranked = _ranked(_research_mh_raw(), context)
    v3_ranked = _ranked(_production_v3(), context)
    assert research_ranked == v3_ranked
    # BBB: negative 12-1 AND all shorter horizons negative -> excluded.
    assert "BBB" not in research_ranked
    # GATE12: 12-1 negative, but 6-1 strongly positive. If the gate ran
    # AFTER the consensus (or were absent), GATE12 would be eligible on 6-1
    # strength. With the frozen order it must be excluded by BOTH.
    assert "GATE12" not in research_ranked
    assert "GATE12" not in v3_ranked


# --------------------------------------------------------------------------- #
# Scenario 5: conflicting 6m vs 12m momentum
# --------------------------------------------------------------------------- #


def test_conflicting_6m_vs_12m_passes_gate_both() -> None:
    paths = _paths()
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV"})
    research_ranked = _ranked(_research_mh_raw(), context)
    v3_ranked = _ranked(_production_v3(), context)
    assert research_ranked == v3_ranked
    # CCC has a (thinly) positive 12-1 but negative 9-1/6-1: the gate passes
    # and the consensus ranks it below the clean positive names.
    assert "CCC" in research_ranked
    assert research_ranked.index("AAA") < research_ranked.index("CCC")


# --------------------------------------------------------------------------- #
# Scenario 6: split-adjusted signal history parity
# --------------------------------------------------------------------------- #


def test_split_adjusted_history_parity() -> None:
    paths = _paths()
    base = _context(paths, {"AAA", "BBB", "DDD", "EEE", "CCC", "GATE12", "HYV"})
    scaled = {symbol: [value * 2.0 for value in closes] for symbol, closes in paths.items()}
    scaled_context = _context(scaled, {"AAA", "BBB", "DDD", "EEE", "CCC", "GATE12", "HYV"})
    research_base = _decided(_research_mh_raw(), base)
    v3_base = _decided(_production_v3(), base)
    research_scaled = _decided(_research_mh_raw(), scaled_context)
    v3_scaled = _decided(_production_v3(), scaled_context)
    # A whole-series rescale (one split-adjusted basis) must not alter any
    # decision in EITHER implementation.
    assert research_base == research_scaled
    assert v3_base == v3_scaled
    assert research_base == v3_base


# --------------------------------------------------------------------------- #
# Consensus math parity (promoted production math vs researched math)
# --------------------------------------------------------------------------- #


def test_consensus_math_parity_research_vs_production() -> None:
    from parayabuck.portfolio.equity_residual_momentum_v3 import multi_horizon_consensus
    from parayabuck.portfolio.research_alpha import horizon_consensus_scores

    residuals = {
        f"S{index}": [0.003 * (index + 1) % 7 - 0.002 for _ in range(300)] for index in range(4)
    }
    production = multi_horizon_consensus(residuals, [126, 189, 252], 21)
    research = horizon_consensus_scores(residuals, [126, 189, 252], 21, standardized=False)
    assert production == research
    # Rank normalization makes the consensus scale-free.
    rescaled = {symbol: [value * 1000 for value in series] for symbol, series in residuals.items()}
    assert multi_horizon_consensus(rescaled, [126, 189, 252], 21) == production
    # Tie handling identical to research (ties averaged).
    tied = {"A": [0.01] * 300, "B": [0.01] * 300, "C": [0.02] * 300}
    assert multi_horizon_consensus(tied, [126, 189, 252], 21) == horizon_consensus_scores(
        tied, [126, 189, 252], 21, standardized=False
    )


def test_consensus_fails_closed_for_thin_or_incomplete_pools() -> None:
    from parayabuck.portfolio.equity_residual_momentum_v3 import multi_horizon_consensus

    assert multi_horizon_consensus({}, [126, 189, 252], 21) == {}
    assert multi_horizon_consensus({"ONLY": [0.001] * 300}, [126, 189, 252], 21) == {}
    incomplete = {
        "A": [0.001] * 300,
        "B": [0.002] * 273,
        "SHORT": [0.003] * 250,
    }
    consensus = multi_horizon_consensus(incomplete, [126, 189, 252], 21)
    assert set(consensus) == {"A", "B"}


def test_v3_thin_qualified_pool_returns_cash_target() -> None:
    context = _context(_paths(), {"AAA"})
    target = _production_v3().decide(context)
    assert target.weights == ()
    assert target.cash_weight == 1.0
    assert target.metadata["risk_off_detail"] == "no_qualified_candidates"
