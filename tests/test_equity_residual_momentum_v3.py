"""Production v3 deterministic contract tests.

Given identical ``PortfolioContext`` values, the production
``EquityResidualMomentum1dV3`` must produce deterministic ranking, selection
and target weights. The suite exercises six representative snapshots:

1. ordinary market snapshot;
2. tie-heavy snapshot (identical residual paths);
3. missing-symbol snapshot (short history + not-in-universe);
4. negative-12m candidate (excluded by the positive 12-1 gate, IN ORDER);
5. conflicting 6m vs 12m momentum (12-1 positive but 6-1/9-1 negative);
6. split-adjusted signal history (whole-series price rescale).

The gate-order regression is explicit: a symbol whose 6-1 momentum is
strongly positive but whose 12-1 momentum is negative MUST be excluded by
the production strategy — proving the positive 12-1 gate runs BEFORE the
multi-horizon consensus.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.numeric_history import build_numeric_history
from parayabuck.config import EquityUniverseConfig
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import (
    EquityResidualMomentum1dV3,
    portfolio_strategy_from,
    supported_portfolio_strategies,
)
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.universe.observation_set import build_observation_set

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

RESERVED_EQUITY_ROLES = ("SPY", "SH", "PSQ", "DOG")

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
    return [close * (1.0 + amplitude * math.sin(index * 1.7 + phase)) for index, close in enumerate(closes)]


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


def test_ranking_ordinary_snapshot() -> None:
    paths = _paths()
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV"})
    ranked = _ranked(_production_v3(), context)
    # The market leader leads; the conflict name IS eligible (12-1 positive
    # despite 6-1/9-1 negative); negative-12-1 names are excluded.
    assert ranked[0] == "AAA"
    assert "CCC" in ranked
    assert "BBB" not in ranked
    assert "GATE12" not in ranked


def test_target_ordinary_snapshot() -> None:
    paths = _paths()
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV", "OUTU"})
    target = _production_v3().decide(context)
    selected = tuple(weight.symbol for weight in target.weights)
    assert selected
    assert set(target.metadata["selected_symbols"]) == set(selected)
    assert target.metadata["ranked_symbols"] >= len(selected)
    assert sum(weight.weight for weight in target.weights) <= 1.0


def test_reserved_roles_are_observable_but_not_ordinary_alpha_targets() -> None:
    paths = _paths()
    paths.update({symbol: list(paths["AAA"]) for symbol in ("SH", "PSQ", "DOG")})
    members = set(paths) - {"SHORT", "OUTU"}
    context = _context(paths, members)
    strategy = _production_v3()

    # The raw score pipeline is deliberately unchanged. The corrected role
    # boundary is applied only after factual scoring, so ordinary candidates
    # keep exactly their pre-fix scores and relative order.
    old_scored, _old_vols = strategy._score_candidates(context)
    corrected = replace(context, ordinary_alpha_excluded_symbols=RESERVED_EQUITY_ROLES)
    corrected_scored, _corrected_vols = strategy._score_candidates(corrected)
    assert corrected_scored == old_scored
    expected_ordinary_rank = tuple(symbol for _score, symbol in old_scored if symbol not in RESERVED_EQUITY_ROLES)
    assert strategy.rank_candidates(corrected) == expected_ordinary_rank

    target = strategy.decide(replace(corrected, holdings={"SH": 10.0}))
    assert all(weight.symbol not in RESERVED_EQUITY_ROLES for weight in target.weights)
    assert "AAA" in {weight.symbol for weight in target.weights}
    assert all(symbol in corrected.history for symbol in RESERVED_EQUITY_ROLES)


def test_reserved_observation_pins_survive_alpha_role_filter() -> None:
    paths = _paths()
    paths.update({symbol: list(paths["AAA"]) for symbol in ("SH", "PSQ", "DOG")})
    members = set(paths) - {"SHORT", "OUTU"}
    context = _context(paths, members)
    result = build_observation_set(
        _production_v3(),
        context.history,
        tuple(sorted(members)),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=context.decision_timestamp,
        maximum_stream_symbols=6,
        pinned_symbols=RESERVED_EQUITY_ROLES,
        ordinary_alpha_excluded_symbols=RESERVED_EQUITY_ROLES,
    )
    assert result.observation_set[:4] == RESERVED_EQUITY_ROLES
    assert set(result.observation_set).issuperset(RESERVED_EQUITY_ROLES)
    assert not set(result.ranked_pool).intersection(RESERVED_EQUITY_ROLES)
    assert "AAA" in result.ranked_pool


def test_role_boundary_is_configuration_derived_and_authorized_context_can_opt_in() -> None:
    config = EquityUniverseConfig(
        reference_symbols=("SPY",),
        fallback_trade_symbols=("SH", "PSQ", "DOG"),
        inverse_trade_symbols=("SH", "PSQ", "DOG"),
    )
    assert config.ordinary_alpha_excluded_symbols == RESERVED_EQUITY_ROLES

    paths = _paths()
    paths.update({symbol: list(paths["AAA"]) for symbol in ("SH", "PSQ", "DOG")})
    members = set(paths) - {"SHORT", "OUTU"}
    authorized_context = replace(_context(paths, members), ordinary_alpha_excluded_symbols=())
    authorized_ranked = _production_v3().rank_candidates(authorized_context)
    assert set(authorized_ranked).intersection({"SH", "PSQ", "DOG"})


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


def test_ranking_tie_heavy() -> None:
    paths = _paths()
    context = _context(paths, {"AAA", "DDD", "EEE", "BBB", "CCC", "GATE12", "HYV"})
    ranked = _ranked(_production_v3(), context)
    # Tied residual paths score identically (ties averaged in ranks) and the
    # deterministic sort places the tie block adjacent.
    positions = [ranked.index(name) for name in ("AAA", "DDD", "EEE")]
    assert max(positions) - min(positions) == 2
    assert ranked.count("AAA") == 1


# --------------------------------------------------------------------------- #
# Scenario 3: missing-symbol snapshot
# --------------------------------------------------------------------------- #


def test_ranking_missing_symbols() -> None:
    paths = _paths()
    # SHORT (too little history) and OUTU (not in universe) must drop out
    # of the production ranking.
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV"})
    ranked = _ranked(_production_v3(), context)
    assert "SHORT" not in ranked
    assert "OUTU" not in ranked


def test_numeric_history_cannot_widen_scoped_context() -> None:
    paths = _paths()
    full_history = {symbol: _bars(symbol, closes) for symbol, closes in paths.items()}
    visible = {"SPY", "BBB", "CCC", "GATE12", "HYV"}
    context = _context(paths, set(paths))
    scoped = replace(
        context,
        history={symbol: bars for symbol, bars in full_history.items() if symbol in visible},
    )
    numeric = build_numeric_history(
        [bar for bars in full_history.values() for bar in bars],
        market=Market.EQUITY,
        timeframe="1d",
    )
    strategy = _production_v3()
    bar_rank = strategy.rank_candidates(scoped)
    numeric_rank = strategy.rank_candidates(replace(scoped, numeric_history=numeric))
    assert numeric_rank == bar_rank
    assert "AAA" not in numeric_rank


# --------------------------------------------------------------------------- #
# Scenario 4: negative-12m candidate — gate ordering regression
# --------------------------------------------------------------------------- #


def test_negative_12m_candidate_excluded_before_consensus() -> None:
    """The positive 12-1 gate runs BEFORE the multi-horizon consensus."""
    paths = _paths()
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV"})
    ranked = _ranked(_production_v3(), context)
    # BBB: negative 12-1 AND all shorter horizons negative -> excluded.
    assert "BBB" not in ranked
    # GATE12: 12-1 negative, but 6-1 strongly positive. If the gate ran
    # AFTER the consensus (or were absent), GATE12 would be eligible on 6-1
    # strength. With the frozen order it must be excluded.
    assert "GATE12" not in ranked


# --------------------------------------------------------------------------- #
# Scenario 5: conflicting 6m vs 12m momentum
# --------------------------------------------------------------------------- #


def test_conflicting_6m_vs_12m_passes_gate() -> None:
    paths = _paths()
    context = _context(paths, {"AAA", "BBB", "CCC", "GATE12", "DDD", "EEE", "HYV"})
    ranked = _ranked(_production_v3(), context)
    # CCC has a (thinly) positive 12-1 but negative 9-1/6-1: the gate passes
    # and the consensus ranks it below the clean positive names.
    assert "CCC" in ranked
    assert ranked.index("AAA") < ranked.index("CCC")


# --------------------------------------------------------------------------- #
# Scenario 6: split-adjusted signal history
# --------------------------------------------------------------------------- #


def test_split_adjusted_history_is_scale_invariant() -> None:
    paths = _paths()
    base = _context(paths, {"AAA", "BBB", "DDD", "EEE", "CCC", "GATE12", "HYV"})
    scaled = {symbol: [value * 2.0 for value in closes] for symbol, closes in paths.items()}
    scaled_context = _context(scaled, {"AAA", "BBB", "DDD", "EEE", "CCC", "GATE12", "HYV"})
    base_decision = _decided(_production_v3(), base)
    scaled_decision = _decided(_production_v3(), scaled_context)
    # A whole-series rescale (one split-adjusted basis) must not alter any
    # production decision.
    assert base_decision == scaled_decision


# --------------------------------------------------------------------------- #
# Consensus math
# --------------------------------------------------------------------------- #


def test_consensus_math_is_scale_invariant_and_tie_aware() -> None:
    from parayabuck.portfolio.equity_residual_momentum_v3 import multi_horizon_consensus

    residuals = {f"S{index}": [0.003 * (index + 1) % 7 - 0.002 for _ in range(300)] for index in range(4)}
    production = multi_horizon_consensus(residuals, [126, 189, 252], 21)
    assert set(production) == set(residuals)
    # Rank normalization makes the consensus scale-free.
    rescaled = {symbol: [value * 1000 for value in series] for symbol, series in residuals.items()}
    assert multi_horizon_consensus(rescaled, [126, 189, 252], 21) == production
    # Ties receive the same score and the higher raw score ranks above them.
    tied = {"A": [0.01] * 300, "B": [0.01] * 300, "C": [0.02] * 300}
    tied_consensus = multi_horizon_consensus(tied, [126, 189, 252], 21)
    assert tied_consensus["A"] == pytest.approx(tied_consensus["B"])
    assert tied_consensus["C"] > tied_consensus["A"]


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
