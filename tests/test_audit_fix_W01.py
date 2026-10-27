"""Audit fixes W01: execution/portfolio.py (F-1, T-1, D9-1, P-3, Q7-3..Q7-6).

Synthetic fixtures only: no network, no credentials, no production paths.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime

import pytest

from parayabuck.config import DownsizeGainBand, RiskLimits
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution import portfolio as portfolio_module
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    TargetToOrderPlanner,
    portfolio_risk_limits_from_config,
    validate_order_batch,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

TIMESTAMP = datetime(2025, 3, 3, tzinfo=UTC)


def _order(quantity: float, side: PortfolioOrderSide, *, symbol: str = "QLD", price: float = 100.0):
    return PortfolioOrderRequest(
        client_order_id=f"id-{side.value}-{quantity!r}",
        decision_id="decision",
        symbol=symbol,
        market=Market.EQUITY,
        side=side,
        quantity=quantity,
        reference_price=price,
        decision_timestamp=TIMESTAMP,
        strategy_name="test",
        strategy_version="1",
    )


# F-1: the increment grid is checked on exact decimals.


@pytest.mark.parametrize("side", [PortfolioOrderSide.BUY, PortfolioOrderSide.SELL])
@pytest.mark.parametrize("quantity", [10.000000001, 11.123456789, 532.987654321, 4999.999999999])
def test_nine_decimal_quantities_above_ten_shares_pass_a_1e9_grid(quantity: float, side) -> None:
    rules = {"QLD": AssetTradingRules(0.0, 1e-9, 0.0)}
    assert validate_order_batch((_order(quantity, side),), rules, minimum_order_notional=0.0) == []


def test_seeded_sweep_of_nine_decimal_quantities_is_never_rejected_as_off_grid() -> None:
    rng = random.Random(20261027)
    rules = {"QLD": AssetTradingRules(0.0, 1e-9, 0.0)}
    quantities = [round(rng.uniform(10.0, 5000.0), 9) for _ in range(500)]
    for index, quantity in enumerate(quantities):
        side = PortfolioOrderSide.SELL if index % 2 else PortfolioOrderSide.BUY
        assert validate_order_batch((_order(quantity, side),), rules, minimum_order_notional=0.0) == [], quantity


def test_a_quantity_off_the_1e9_grid_is_still_flagged() -> None:
    rules = {"QLD": AssetTradingRules(0.0, 1e-9, 0.0)}
    problems = validate_order_batch((_order(10.0000000005, PortfolioOrderSide.SELL),), rules, minimum_order_notional=0.0)
    assert len(problems) == 1
    assert "is not a multiple of increment" in problems[0][1]


def test_float_noise_off_the_decimal_grid_is_flagged() -> None:
    # 0.1 + 0.2 is 0.30000000000000004: off the 0.1 grid, so it is rejected.
    rules = {"BTC": AssetTradingRules(0.0001, 0.1, 1.0)}
    problems = validate_order_batch((_order(0.1 + 0.2, PortfolioOrderSide.BUY, symbol="BTC"),), rules, minimum_order_notional=1.0)
    assert len(problems) == 1 and "is not a multiple of increment" in problems[0][1]


def test_a_whole_share_increment_still_flags_a_fractional_quantity() -> None:
    rules = {"QLD": AssetTradingRules(0.0, 1.0, 0.0)}
    problems = validate_order_batch((_order(10.5, PortfolioOrderSide.BUY),), rules, minimum_order_notional=0.0)
    assert len(problems) == 1 and "is not a multiple of increment" in problems[0][1]


# T-1: a failed calendar read is not cached.


def test_a_failed_calendar_read_is_not_cached(monkeypatch) -> None:
    real_sessions = portfolio_module.nyse_sessions
    calls: list[tuple[date, date]] = []

    def flaky_sessions(start: date, end: date):
        calls.append((start, end))
        if len(calls) == 1:
            raise RuntimeError("calendar unavailable")
        return real_sessions(start, end)

    monkeypatch.setattr(portfolio_module, "nyse_sessions", flaky_sessions)
    portfolio_module._cached_december_season_sessions.cache_clear()
    try:
        assert portfolio_module._december_season_sessions(2025) == ()
        recovered = portfolio_module._december_season_sessions(2025)
        assert recovered[0] == date(2025, 11, 3)
        assert recovered[-1] == date(2025, 12, 31)
        assert len(calls) == 2
        # A successful read is cached: no third calendar call.
        assert portfolio_module._december_season_sessions(2025) == recovered
        assert len(calls) == 2
    finally:
        portfolio_module._cached_december_season_sessions.cache_clear()


def test_an_empty_calendar_read_is_not_cached(monkeypatch) -> None:
    calls: list[tuple[date, date]] = []

    def empty_sessions(start: date, end: date):
        calls.append((start, end))
        return ()

    monkeypatch.setattr(portfolio_module, "nyse_sessions", empty_sessions)
    portfolio_module._cached_december_season_sessions.cache_clear()
    try:
        assert portfolio_module._december_season_sessions(2025) == ()
        assert portfolio_module._december_season_sessions(2025) == ()
        assert len(calls) == 2
    finally:
        portfolio_module._cached_december_season_sessions.cache_clear()


# D9-1: omitted risk limits are the shipped limits.


def test_omitted_risk_limits_are_the_shipped_configuration() -> None:
    assert PortfolioRiskLimits() == portfolio_risk_limits_from_config(RiskLimits())
    assert PortfolioRiskOverlay().limits == portfolio_risk_limits_from_config(RiskLimits())


# P-3: a BUY carries the price its runtime sizing used.


def _spy_account(*, qty: float, current_price: float, equity: float = 10_000.0) -> AccountSnapshot:
    position = PositionSnapshot(
        "SPY",
        qty,
        100.0,
        current_price,
        Market.EQUITY,
        qty_available=qty,
        ownership="managed",
        managed_qty=qty,
    )
    return AccountSnapshot(
        cash=equity - qty * current_price,
        equity=equity,
        peak_equity=equity,
        daily_pnl=0.0,
        positions=(position,),
    )


def _spy_target(weight: float) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="test",
        strategy_version="1",
        decision_timestamp=TIMESTAMP,
        weights=(TargetWeight("SPY", weight),),
        cash_weight=1.0 - weight,
        universe_version="test",
    )


def _plan_spy(policy: PortfolioExecutionPolicy, target: TargetPortfolio, account: AccountSnapshot):
    return TargetToOrderPlanner(policy).plan(
        target,
        account,
        market=Market.EQUITY,
        prices={"SPY": 100.0},
        rules={"SPY": AssetTradingRules(0.01, 0.01, 1.0)},
        approved_symbols=("SPY",),
        decision_id="decision",
    )


def test_a_buy_reference_price_is_the_max_of_reference_and_mark() -> None:
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.005, minimum_order_notional=10.0)
    account = _spy_account(qty=10.0, current_price=120.0)
    (buy,) = _plan_spy(policy, _spy_target(0.5), account)
    assert buy.side == PortfolioOrderSide.BUY
    assert buy.reference_price == 120.0
    assert validate_order_batch((buy,), {"SPY": AssetTradingRules(0.01, 0.01, 1.0)}, minimum_order_notional=10.0) == []


def test_a_sell_reference_price_stays_the_planner_reference() -> None:
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.005, minimum_order_notional=10.0)
    account = _spy_account(qty=10.0, current_price=120.0)
    (sell,) = _plan_spy(policy, _spy_target(0.05), account)
    assert sell.side == PortfolioOrderSide.SELL
    assert sell.reference_price == 100.0


# Q7-3: daily-loss denominator is the day-start equity, and the test is `>=`.


def _overlay_reasons(
    *, equity: float, limits: PortfolioRiskLimits, daily_pnl: float = 0.0, peak: float | None = None
) -> tuple[str, ...]:
    account = AccountSnapshot(
        cash=equity,
        equity=equity,
        peak_equity=equity if peak is None else peak,
        daily_pnl=daily_pnl,
        positions=(),
    )
    decision = PortfolioRiskOverlay(limits).apply(
        _spy_target(0.0),
        account,
        market=Market.EQUITY,
        approved_symbols=("SPY",),
        maximum_allocation=0.50,
        mode="paper",
    )
    return decision.reasons


def test_daily_loss_at_exactly_the_limit_of_day_start_equity_halts() -> None:
    # Day-start equity 1000, loss 5: exactly 0.5 %.
    limits = PortfolioRiskLimits(daily_loss_limit=0.005)
    assert "daily_loss_limit" in _overlay_reasons(equity=995.0, daily_pnl=-5.0, limits=limits)


def test_daily_loss_is_measured_against_day_start_not_current_equity() -> None:
    # Loss 5 on day-start equity 1005 is 0.4975 %, under the limit. Measured
    # against the current equity 1000 it would be exactly 0.5 % and halt.
    limits = PortfolioRiskLimits(daily_loss_limit=0.005)
    assert "daily_loss_limit" not in _overlay_reasons(equity=1000.0, daily_pnl=-5.0, limits=limits)


# Q7-4: drawdown halt is `>=`.


def test_drawdown_at_exactly_the_halt_threshold_halts() -> None:
    # Peak 1000, equity 950: drawdown exactly 5 %.
    limits = PortfolioRiskLimits(drawdown_halt=0.05)
    assert "portfolio_drawdown_halt" in _overlay_reasons(equity=950.0, peak=1000.0, limits=limits)


def test_drawdown_just_under_the_halt_threshold_does_not_halt() -> None:
    limits = PortfolioRiskLimits(drawdown_halt=0.05)
    assert "portfolio_drawdown_halt" not in _overlay_reasons(equity=950.5, peak=1000.0, limits=limits)


# Q7-5: rebalance band is strict `<`: a gap of exactly the band still trades.


def test_a_gap_of_exactly_the_rebalance_band_trades() -> None:
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.25, minimum_order_notional=10.0)
    # 25 shares of 10 000 at 100 = weight 0.25; target 0.50 = gap 0.25 exactly.
    (buy,) = _plan_spy(policy, _spy_target(0.5), _spy_account(qty=25.0, current_price=100.0))
    assert buy.side == PortfolioOrderSide.BUY
    assert buy.quantity == 25.0


def test_a_gap_just_under_the_rebalance_band_does_not_trade() -> None:
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.25, minimum_order_notional=10.0)
    assert _plan_spy(policy, _spy_target(0.4999), _spy_account(qty=25.0, current_price=100.0)) == ()


# Q7-6: gain band hold is strict `gap < band`: a gap of exactly the band sells.


def _gain_band_policy() -> PortfolioExecutionPolicy:
    return PortfolioExecutionPolicy(
        rebalance_band_pct=0.005,
        minimum_order_notional=10.0,
        downsize_gain_band=DownsizeGainBand(thresholds=(0.01,), bands=(0.25, 0.25)),
    )


def test_a_downsize_gap_of_exactly_the_gain_band_sells() -> None:
    holds: list = []
    # 50 shares of 10 000 at 100 = weight 0.50 -> target 0.25: gap 0.25 exactly.
    # The lot is bought at the sell price, so the gain fraction is 0 and the band is 0.25.
    orders = TargetToOrderPlanner(_gain_band_policy()).plan(
        _spy_target(0.25),
        _spy_account(qty=50.0, current_price=100.0),
        market=Market.EQUITY,
        prices={"SPY": 100.0},
        rules={"SPY": AssetTradingRules(0.01, 0.01, 1.0)},
        approved_symbols=("SPY",),
        decision_id="decision",
        open_lots={"SPY": [(50.0, 100.0)]},
        gain_band_holds=holds,
    )
    assert [(order.side, order.quantity) for order in orders] == [(PortfolioOrderSide.SELL, 25.0)]
    assert holds == []


def test_a_downsize_gap_just_under_the_gain_band_is_held() -> None:
    holds: list = []
    orders = TargetToOrderPlanner(_gain_band_policy()).plan(
        _spy_target(0.2501),
        _spy_account(qty=50.0, current_price=100.0),
        market=Market.EQUITY,
        prices={"SPY": 100.0},
        rules={"SPY": AssetTradingRules(0.01, 0.01, 1.0)},
        approved_symbols=("SPY",),
        decision_id="decision",
        open_lots={"SPY": [(50.0, 100.0)]},
        gain_band_holds=holds,
    )
    assert orders == ()
    assert len(holds) == 1 and holds[0].symbol == "SPY"
