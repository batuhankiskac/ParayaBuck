"""Mixed-position risk math tests (Phase 4).

External inventory is immutable and must never be scaled as part of the
bot's managed allocation. When managed exposure exceeds the managed limit,
only the managed desired weight of each symbol is scaled:
final_total = external_floor + scaled_managed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight


def _position(symbol: str, qty: float, managed_qty: float, price: float = 100.0):
    return PositionSnapshot(
        symbol,
        qty,
        price,
        price,
        Market.EQUITY,
        qty_available=qty,
        ownership="external" if managed_qty <= 0 else "managed",
        managed_qty=managed_qty,
    )


def _target(weights: dict[str, float]) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="fixed",
        strategy_version="1.0.0",
        decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        weights=tuple(TargetWeight(symbol, w) for symbol, w in weights.items()),
        cash_weight=1.0 - sum(weights.values()),
        universe_version="test",
    )


def _apply(
    target: TargetPortfolio,
    account: AccountSnapshot,
    *,
    limits: PortfolioRiskLimits | None = None,
    maximum_allocation: float = 0.40,
):
    if limits is None:
        limits = PortfolioRiskLimits(maximum_equity_asset=0.30, maximum_equity=0.50)
    return PortfolioRiskOverlay(limits).apply(
        target,
        account,
        market=Market.EQUITY,
        approved_symbols=tuple(w.symbol for w in target.weights),
        maximum_allocation=maximum_allocation,
        mode="paper",
    )


def test_mission_example_scales_only_managed_desired() -> None:
    """AAPL 10% external + 20% managed, QQQ 30% managed, 40% managed limit:
    AAPL final = 26%, QQQ = 24%, managed exposure = 40%, gross = 50%."""
    account = AccountSnapshot(
        cash=60_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0.0,
        positions=(
            _position("AAPL", 300, 200),  # 30k total, 10k external, 20k managed
        ),
    )
    decision = _apply(_target({"AAPL": 0.30, "QQQ": 0.30}), account)
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    assert weights["AAPL"] == pytest.approx(0.26)
    assert weights["QQQ"] == pytest.approx(0.24)
    managed = sum(weights.values()) - 0.10  # external AAPL floor
    assert managed == pytest.approx(0.40)
    assert decision.gross_after == pytest.approx(0.50)
    assert "exposure_scaled_to_limit" in decision.reasons


def test_external_floor_never_scaled() -> None:
    """The old bug scaled the TOTAL weight: AAPL would have landed at 24%.
    The floor must stay at 10% and only the managed 20% scales to 16%."""
    account = AccountSnapshot(
        cash=60_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0.0,
        positions=(_position("AAPL", 300, 200),),
    )
    decision = _apply(_target({"AAPL": 0.30, "QQQ": 0.30}), account)
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    assert weights["AAPL"] == pytest.approx(0.26)
    assert weights["AAPL"] != pytest.approx(0.24)


def test_no_scaling_when_managed_within_limit() -> None:
    account = AccountSnapshot(
        cash=60_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0.0,
        positions=(_position("AAPL", 300, 200),),
    )
    decision = _apply(_target({"AAPL": 0.30, "QQQ": 0.10}), account)
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    assert weights["AAPL"] == pytest.approx(0.30)
    assert weights["QQQ"] == pytest.approx(0.10)
    assert "exposure_scaled_to_limit" not in decision.reasons


def test_external_over_asset_limit_blocks_managed_additions_and_surfaces() -> None:
    """A total-per-symbol limit exceeded solely by immutable manual inventory
    must not sell manual inventory; managed additions are blocked and the
    violation is surfaced."""
    limits = PortfolioRiskLimits(maximum_equity_asset=0.05)
    account = AccountSnapshot(
        cash=90_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0.0,
        positions=(_position("AAPL", 100, 30),),  # 10% total, 7% external
    )
    decision = _apply(_target({"AAPL": 0.10, "QQQ": 0.05}), account, limits=limits)
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    # AAPL stays at its external floor (0.07): no new managed addition to the
    # over-limit symbol. QQQ is an independent position and stays.
    assert weights["AAPL"] == pytest.approx(0.07)
    assert weights["QQQ"] == pytest.approx(0.05)
    assert any(reason.startswith("external_exceeds_asset_limit:AAPL") for reason in decision.reasons)


def test_mixed_position_keeps_floor_and_blocks_new_managed() -> None:
    """AAPL 10% external + managed target that would exceed the per-asset
    limit: total stays capped at the asset limit with the floor intact."""
    limits = PortfolioRiskLimits(maximum_equity_asset=0.15)
    account = AccountSnapshot(
        cash=60_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0.0,
        positions=(_position("AAPL", 300, 200),),
    )
    decision = _apply(_target({"AAPL": 0.30}), account, limits=limits)
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    # min(0.30, 0.15) = 0.15, floored at external 0.10.
    assert weights["AAPL"] == pytest.approx(0.15)
    managed = weights["AAPL"] - 0.10
    assert managed == pytest.approx(0.05)


def test_pure_managed_scaling_matches_mission_semantics() -> None:
    """No external inventory at all: scaling the whole target is unchanged."""
    account = AccountSnapshot(
        cash=100_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0.0,
        positions=(),
    )
    decision = _apply(_target({"AAPL": 0.30, "QQQ": 0.30}), account)
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    assert weights["AAPL"] == pytest.approx(0.20)
    assert weights["QQQ"] == pytest.approx(0.20)
    assert decision.gross_after == pytest.approx(0.40)


def test_execution_policy_defaults() -> None:
    """Shared policy defaults: 50 bps band, $10 minimum notional."""
    policy = PortfolioExecutionPolicy()
    assert policy.rebalance_band_pct == 0.005
    assert policy.minimum_order_notional == 10.0
    with pytest.raises(ValueError):
        PortfolioExecutionPolicy(rebalance_band_pct=1.0)
