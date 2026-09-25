"""Position-count enforcement must not invent a second alpha ranking.

Regression tests for the top_n / max_positions semantic conflict: the risk
overlay's ``maximum_positions`` truncation previously sorted by TARGET
WEIGHT (ties broken by symbol), so after inverse-vol sizing and the
per-asset cap pinned several names at exactly the cap, symbol ordering —
not strategy alpha — decided which positions survived. Strategies now carry
explicit ``TargetWeight.rank`` metadata and the overlay retains the
best-ranked names while keeping the limit itself fully enforced.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits, PortfolioRiskOverlay
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

_TS = datetime(2025, 6, 2, tzinfo=UTC)

# Isolate position-count enforcement: every other limit is wide open.
_LIMITS = PortfolioRiskLimits(
    daily_loss_limit=1.0,
    drawdown_halt=1.0,
    maximum_gross=1.0,
    maximum_equity=1.0,
    maximum_crypto=1.0,
    maximum_equity_asset=0.25,
    maximum_crypto_asset=0.25,
    minimum_cash=0.0,
    maximum_positions=6,
    maximum_crypto_positions=2,
)

_NAMES = ("AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "FFFF", "GGGG", "HHHH")


def _flat_account(equity: float = 100_000.0) -> AccountSnapshot:
    return AccountSnapshot(
        cash=equity,
        equity=equity,
        peak_equity=equity,
        daily_pnl=0.0,
        positions=(),
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        fees=0.0,
    )


def _ranked_target(weights: dict[str, float], ranks: dict[str, int]) -> TargetPortfolio:
    ordered = tuple(
        TargetWeight(symbol, weight, reason="alpha", rank=ranks.get(symbol, 0))
        for symbol, weight in sorted(weights.items())
    )
    return TargetPortfolio(
        strategy_name="equity_leveraged_trend_v1",
        strategy_version="2.0.0",
        decision_timestamp=_TS,
        weights=ordered,
        cash_weight=max(0.0, 1.0 - sum(weights.values())),
        universe_version="test",
    )


def _apply(target: TargetPortfolio, limits: PortfolioRiskLimits = _LIMITS):
    return PortfolioRiskOverlay(limits).apply(
        target,
        _flat_account(),
        market=Market.EQUITY,
        approved_symbols=tuple(weight.symbol for weight in target.weights),
        maximum_allocation=1.0,
        mode="backtest",
    )


def test_position_limit_retains_alpha_ranks_not_heaviest_weights() -> None:
    """Alpha ranks 7..8 must be dropped before ranks 1..2 even though the
    low-rank names carry the LARGEST inverse-vol weights."""
    weights = {
        "AAAA": 0.04,
        "BBBB": 0.04,
        "CCCC": 0.05,
        "DDDD": 0.06,
        "EEEE": 0.07,
        "FFFF": 0.08,
        "GGGG": 0.09,
        "HHHH": 0.10,
    }
    ranks = {symbol: index for index, symbol in enumerate(_NAMES, start=1)}
    decision = _apply(_ranked_target(weights, ranks))
    assert {w.symbol for w in decision.target.weights} == set(_NAMES[:6])
    assert "position_count_limit" in decision.reasons


def test_per_asset_cap_ties_no_longer_decide_survivors_by_symbol_order() -> None:
    """Eight names pinned at exactly the per-asset cap: the survivor set must
    follow alpha rank, never the alphabetical tie-break of weight sorting."""
    # Reverse-alphabetical names so rank order and symbol order disagree.
    names = tuple(reversed(_NAMES))
    weights = {symbol: 0.10 for symbol in names}
    ranks = {symbol: index for index, symbol in enumerate(names, start=1)}
    decision = _apply(_ranked_target(weights, ranks))
    assert {w.symbol for w in decision.target.weights} == set(names[:6])
    # Weights are untouched by count enforcement: still at the cap.
    assert all(weight.weight == 0.10 for weight in decision.target.weights)


def test_unranked_targets_keep_legacy_weight_and_symbol_order() -> None:
    """No declared ranks -> the pre-change ordering applies unchanged
    (weight descending, then symbol ascending)."""
    weights = {symbol: 0.10 for symbol in _NAMES}
    target = TargetPortfolio(
        strategy_name="crypto_low_variance_v2",
        strategy_version="2.0.0",
        decision_timestamp=_TS,
        weights=tuple(TargetWeight(symbol, w) for symbol, w in weights.items()),
        cash_weight=0.2,
        universe_version="test",
    )
    decision = _apply(target)
    assert {w.symbol for w in decision.target.weights} == set(_NAMES[:6])


def test_partial_ranking_puts_unranked_names_last() -> None:
    """A mixed target keeps every ranked name before any unranked one,
    deterministically, even when the unranked weights are heavier."""
    weights = {"AAAA": 0.10, "BBBB": 0.20, "CCCC": 0.05, "DDDD": 0.30}
    ranks = {"AAAA": 1, "BBBB": 2}  # CCCC/DDDD unranked but heavier
    two_positions = PortfolioRiskLimits(**{**_LIMITS.__dict__, "maximum_positions": 2})
    decision = _apply(_ranked_target(weights, ranks), two_positions)
    assert tuple(w.symbol for w in decision.target.weights) == ("AAAA", "BBBB")


def test_rank_round_trips_through_target_portfolio_serialization() -> None:
    """Persisted decisions (rebalance recovery) must restore rank exactly."""
    target = _ranked_target({"AAAA": 0.3, "BBBB": 0.2}, {"AAAA": 1, "BBBB": 2})
    restored = TargetPortfolio.from_dict(target.to_dict())
    assert {w.symbol: w.rank for w in restored.weights} == {"AAAA": 1, "BBBB": 2}


def test_negative_rank_rejected() -> None:
    with pytest.raises(ValueError, match="rank"):
        TargetWeight("AAAA", 0.1, rank=-1)
