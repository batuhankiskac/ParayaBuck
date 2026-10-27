"""Regression tests for audit wave 2 ids T2-2, T2-6 and T2-10 (worker I1).

Synthetic fixtures only: no network, no credentials, no production paths.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    GAIN_BAND_FALLBACK_KEY,
    AssetTradingRules,
    DownsizeGainBand,
    PortfolioExecutionPolicy,
    PortfolioRiskLimits,
    TargetToOrderPlanner,
    record_gain_band_fallbacks,
)
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.base import PortfolioStrategy
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

SYMBOL = "QLD"
NEW_YORK = ZoneInfo("America/New_York")
# A decision on 2025-12-16 fills on 2025-12-17, inside the last 10 December sessions.
DECEMBER = datetime(2025, 12, 16, tzinfo=NEW_YORK)
RULE = AssetTradingRules(0.001, 0.001, 1.0)


def _account(qty: float, *, managed_qty: float | None = None, price: float, equity: float) -> AccountSnapshot:
    managed = qty if managed_qty is None else managed_qty
    positions = (
        PositionSnapshot(
            SYMBOL,
            qty,
            price,
            price,
            Market.EQUITY,
            qty_available=qty,
            ownership="managed",
            managed_qty=managed,
        ),
    )
    return AccountSnapshot(
        cash=equity - qty * price,
        equity=equity,
        peak_equity=equity,
        daily_pnl=0.0,
        positions=positions,
    )


def _target(weight: float) -> TargetPortfolio:
    weights = (TargetWeight(SYMBOL, weight),) if weight > 0 else ()
    return TargetPortfolio("vt35", "1.2.0", DECEMBER, weights, 1.0 - weight, universe_version="test")


def _plan(
    weight: float,
    account: AccountSnapshot,
    *,
    policy: PortfolioExecutionPolicy,
    price: float,
    open_lots: dict[str, list[tuple[float, float]]] | None = None,
    deferral_holds: list | None = None,
    fallbacks: list | None = None,
) -> tuple[object, ...]:
    return TargetToOrderPlanner(policy).plan(
        _target(weight),
        account,
        market=Market.EQUITY,
        prices={SYMBOL: price},
        rules={SYMBOL: RULE},
        approved_symbols=(SYMBOL,),
        decision_id="t2-decision",
        open_lots=open_lots,  # type: ignore[arg-type]
        december_deferral_holds=deferral_holds,
        gain_band_fallbacks=fallbacks,
    )


# --------------------------------------------------------------------------- #
# T2-2-6: a mixed position whose target keeps only the external units exits
# its managed units without the band; an ordinary small trim still waits.
# --------------------------------------------------------------------------- #


def test_mixed_position_full_managed_exit_is_not_held_inside_the_band() -> None:
    # 10 units held, 2 of them managed; the target keeps the 8 external units.
    account = _account(10.0, managed_qty=2.0, price=100.0, equity=100_000.0)
    orders = _plan(0.008, account, policy=PortfolioExecutionPolicy(), price=100.0)
    assert len(orders) == 1
    assert orders[0].side.value == "sell"
    assert orders[0].quantity == pytest.approx(2.0)


def test_ordinary_small_trim_inside_the_band_still_waits() -> None:
    # Fully managed position, target 0.009 (a 0.1 pp trim): the band holds it.
    account = _account(10.0, price=100.0, equity=100_000.0)
    assert _plan(0.009, account, policy=PortfolioExecutionPolicy(), price=100.0) == ()


# --------------------------------------------------------------------------- #
# T2-2-3: a December hold is recorded only for a downsize that would be an order.
# --------------------------------------------------------------------------- #


def test_december_hold_below_the_order_floor_is_not_recorded() -> None:
    # 0.7 units x $10 = $7, below the $10 floor, so no order and no hold.
    account = _account(10.0, price=10.0, equity=1_000.0)
    holds: list = []
    orders = _plan(
        0.093,
        account,
        policy=PortfolioExecutionPolicy(december_deferral_sessions=10),
        price=10.0,
        deferral_holds=holds,
    )
    assert orders == ()
    assert holds == []


def test_december_hold_above_the_order_floor_is_recorded_once() -> None:
    account = _account(10.0, price=10.0, equity=1_000.0)
    holds: list = []
    orders = _plan(
        0.05,
        account,
        policy=PortfolioExecutionPolicy(december_deferral_sessions=10),
        price=10.0,
        deferral_holds=holds,
    )
    assert orders == ()
    assert len(holds) == 1
    assert holds[0].symbol == SYMBOL
    assert holds[0].sessions == 10


def test_december_deferral_is_sign_blind_by_design() -> None:
    # Audit T2-10-1, owner decision 8.3: a downsize is postponed whatever its
    # gain. The planner receives no cost basis at all, so this pins the choice.
    account = _account(10.0, price=10.0, equity=1_000.0)
    holds: list = []
    _plan(
        0.05,
        account,
        policy=PortfolioExecutionPolicy(december_deferral_sessions=10),
        price=10.0,
        deferral_holds=holds,
    )
    assert len(holds) == 1


# --------------------------------------------------------------------------- #
# T2-10-4: a gain-band fallback to the constant band is recorded, not silent.
# --------------------------------------------------------------------------- #


def test_gain_band_lot_book_fallback_is_recorded_and_still_sells() -> None:
    # The lot book sums to 5 units against 900 managed: a mismatch, so the
    # constant band decides and the resize still executes.
    account = _account(900.0, price=100.0, equity=100_000.0)
    policy = PortfolioExecutionPolicy(downsize_gain_band=DownsizeGainBand())
    fallbacks: list = []
    orders = _plan(
        0.5,
        account,
        policy=policy,
        price=100.0,
        open_lots={SYMBOL: [(5.0, 40.0)]},
        fallbacks=fallbacks,
    )
    assert len(orders) == 1
    assert orders[0].side.value == "sell"
    assert [(item.symbol, item.reason) for item in fallbacks] == [(SYMBOL, "lot_book_mismatch")]

    recorded = record_gain_band_fallbacks(_target(0.5), fallbacks)
    assert recorded.metadata[GAIN_BAND_FALLBACK_KEY] == (
        {"symbol": SYMBOL, "reason": "lot_book_mismatch", "band": 0.005},
    )


def test_no_fallback_leaves_decision_metadata_unchanged() -> None:
    target = _target(0.5)
    assert record_gain_band_fallbacks(target, []) is target


# --------------------------------------------------------------------------- #
# T2-2-1: the backtest engine substitutes the shipped limits, as live does.
# --------------------------------------------------------------------------- #


class _Hold(PortfolioStrategy):
    name = "t2_fix_hold"
    version = "1.0.0"
    market = Market.EQUITY
    minimum_history_bars = 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context):  # pragma: no cover - not reached by these tests
        raise AssertionError("no decision expected")


def test_backtest_without_risk_limits_still_runs_the_overlay_and_cash_buffer() -> None:
    engine = PortfolioBacktestEngine(
        _Hold(),
        RebalancePolicy(Market.EQUITY, "hours:24"),
        benchmark_symbol="SPY",
    )
    assert engine.risk_overlay is not None
    assert engine.risk_overlay.limits == PortfolioRiskLimits()
    assert engine.minimum_cash == PortfolioRiskLimits().minimum_cash


# --------------------------------------------------------------------------- #
# T2-2-2: an initial holding is valued only on the first session.
# --------------------------------------------------------------------------- #


def _bar(day: int, symbol: str, close_price: float) -> Bar:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    return Bar(
        start + timedelta(days=day),
        symbol,
        "1d",
        close_price,
        close_price * 1.001,
        close_price * 0.999,
        close_price,
        1_000_000.0,
        Market.EQUITY,
    )


def test_initial_holding_without_a_first_session_bar_is_refused() -> None:
    bars = [_bar(0, "SPY", 400.0), _bar(1, "SPY", 401.0), _bar(1, SYMBOL, 101.0), _bar(2, SYMBOL, 102.0)]
    engine = PortfolioBacktestEngine(
        _Hold(),
        RebalancePolicy(Market.EQUITY, "hours:24"),
        benchmark_symbol="SPY",
        initial_positions={SYMBOL: (1.0, 90.0)},
    )
    with pytest.raises(ValueError, match="first session"):
        engine.run(bars, liquidate_at_end=False)


# --------------------------------------------------------------------------- #
# T2-6-1: the replay SMA vote is undefined while history is shorter than the window.
# --------------------------------------------------------------------------- #


def test_replay_sma_vote_is_undefined_for_a_prefix_shorter_than_the_window() -> None:
    strategy = EquityLeveragedTrendV1(
        {
            "signal_symbol": "QQQ",
            "hold_symbol": SYMBOL,
            "sma_bars": 300,
            "rebalance_frequency": "hours:24",
            "vol_target": 0.0,
            "vol_window": 20,
            "trend_rule": "sma",
        }
    )
    short = [100.0 + index for index in range(299)]
    assert strategy.sma_vote(short) is None
    full = [100.0 + index for index in range(300)]
    assert strategy.sma_vote(full) is True
    assert strategy.sma_vote(full[::-1]) is False


# --------------------------------------------------------------------------- #
# T2-6-2: a failed journal write does not suppress the day's no-trade reason.
# --------------------------------------------------------------------------- #


def test_no_trade_day_is_marked_only_after_the_journal_write_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.monitoring import journal

    calls: list[bool] = []
    results = iter([False, True])

    def fake_emit(event, *, path=None) -> bool:
        calls.append(True)
        return next(results)

    monkeypatch.setattr(journal, "emit_journal", fake_emit)
    runtime_service._no_trade_last.pop("equity:t2_fix:t2_reason", None)
    kwargs = {
        "market": "equity",
        "strategy": "t2_fix",
        "reason": "t2_reason",
        "decision_timestamp": DECEMBER,
    }
    try:
        runtime_service._emit_no_trade_safe(**kwargs)  # failed write: day not marked
        assert "equity:t2_fix:t2_reason" not in runtime_service._no_trade_last
        runtime_service._emit_no_trade_safe(**kwargs)  # successful write: day marked
        assert runtime_service._no_trade_last["equity:t2_fix:t2_reason"] == DECEMBER.date().isoformat()
        runtime_service._emit_no_trade_safe(**kwargs)  # same day: suppressed
    finally:
        runtime_service._no_trade_last.pop("equity:t2_fix:t2_reason", None)
    assert len(calls) == 2
