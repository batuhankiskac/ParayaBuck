"""Gain-scaled downsize band A1u (docs/research/gain_band_v3.md).

A downward resize between two positive weights fills only when its drift
exceeds ``B_down(g_frac)``, ``g_frac`` being the USD FIFO gain fraction of the
units the sell would consume over the bot's own lots. Entries, exits to zero
and up-resizes keep the constant band, and an unavailable lot book falls back
to it exactly. Every fixture is synthetic and deterministic: no network, no
production path, ``tmp_path`` state only.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    _Position,
)
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.config import (
    DownsizeGainBand,
    ExecutionConfig,
    load_config,
)
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.execution.portfolio import (
    GAIN_BAND_BYPASS_KEY,
    AssetTradingRules,
    GainBandHold,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    RiskOverlayDecision,
    TargetToOrderPlanner,
    downsize_gain_band_verdict,
    downsize_gain_fraction,
    gain_band_bypass_metadata,
    gain_band_bypass_symbols,
    portfolio_execution_policy_from_config,
    record_gain_band_holds,
)
from parayabuck.monitoring.daily_summary import build_equity_daily_summary
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.portfolio.runtime_service import PortfolioRuntime, configuration_digest

ACCOUNT = "acct"
SYMBOL = "QLD"
PRICE = 100.0
EQUITY = 100_000.0
QTY = 900.0  # 0.90 weight at PRICE on EQUITY
T0 = datetime(2026, 9, 1, tzinfo=UTC)
SHIPPED = DownsizeGainBand()


def _schedule() -> DownsizeGainBand:
    return DownsizeGainBand()


def _policy(*, enabled: bool = True) -> PortfolioExecutionPolicy:
    return PortfolioExecutionPolicy(downsize_gain_band=_schedule() if enabled else None)


def _target(weight: float) -> TargetPortfolio:
    weights = (TargetWeight(SYMBOL, weight),) if weight > 0 else ()
    return TargetPortfolio("vt35", "1.2.0", T0, weights, 1.0 - weight, universe_version="test")


def _account(*, qty: float = QTY, price: float = PRICE, equity: float = EQUITY) -> AccountSnapshot:
    positions = ()
    if qty != 0:
        positions = (
            PositionSnapshot(
                SYMBOL,
                qty,
                price,
                price,
                Market.EQUITY,
                qty_available=qty,
                ownership="managed",
                managed_qty=qty,
            ),
        )
    return AccountSnapshot(
        cash=equity - qty * price,
        equity=equity,
        peak_equity=equity,
        daily_pnl=0.0,
        positions=positions,
    )


def _plan_target(
    target: TargetPortfolio,
    *,
    lots: object = "default",
    policy: PortfolioExecutionPolicy | None = None,
    qty: float = QTY,
    price: float = PRICE,
) -> tuple[tuple[PortfolioOrderRequest, ...], list[GainBandHold]]:
    """Planner verdict for one target, with the suppressed downsizes collected."""
    holds: list[GainBandHold] = []
    open_lots = {"QLD": [(600.0, 40.0), (300.0, 99.5)]} if lots == "default" else lots
    orders = TargetToOrderPlanner(policy or _policy()).plan(
        target,
        _account(qty=qty, price=price),
        market=Market.EQUITY,
        prices={SYMBOL: price},
        rules={SYMBOL: AssetTradingRules(0.001, 0.001, 1.0)},
        approved_symbols=(SYMBOL,),
        decision_id="a1u-decision",
        open_lots=open_lots,  # type: ignore[arg-type]
        gain_band_holds=holds,
    )
    return orders, holds


def _plan(
    target_weight: float,
    *,
    lots: object = "default",
    policy: PortfolioExecutionPolicy | None = None,
    qty: float = QTY,
    price: float = PRICE,
) -> tuple[tuple[PortfolioOrderRequest, ...], list[GainBandHold]]:
    """Planner verdict for one symbol weight, with the suppressed downsizes collected."""
    return _plan_target(_target(target_weight), lots=lots, policy=policy, qty=qty, price=price)


def _engine(
    *,
    policy: PortfolioExecutionPolicy | None = None,
    qty: float = QTY,
    price: float = PRICE,
    lots: list[list[float]] | None = None,
    equity: float = EQUITY,
) -> PortfolioBacktestEngine:
    strategy = _FixedTargetStrategy(())
    engine = PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=equity,
        slippage_bps=0.0,
        spread_bps=0.0,
        commission=0.0,
        execution_policy=policy or _policy(),
        allow_static_universe=True,
    )
    engine._cash = equity - qty * price
    engine._last_price[SYMBOL] = price
    if qty != 0:
        default_lots = [[600.0, 40.0], [300.0, 99.5]]
        engine._positions[SYMBOL] = _Position(
            SYMBOL,
            Market.EQUITY,
            "1d",
            qty,
            price,
            T0,
            lots=default_lots if lots is None else lots,
        )
    return engine


def _backtest_plan(
    target_weight: float,
    *,
    policy: PortfolioExecutionPolicy | None = None,
    qty: float = QTY,
    lots: list[list[float]] | None = None,
) -> tuple[list[tuple[str, str]], tuple[GainBandHold, ...]]:
    engine = _engine(policy=policy, qty=qty, lots=lots)
    engine._apply_target(_target(target_weight))
    return (
        [(order.symbol, order.side.value) for order in engine._pending],
        tuple(engine._gain_band_holds),
    )


class _FixedTargetStrategy(PortfolioStrategy):
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = dict(targets)
        super().__init__({"targets": dict(self._targets)})

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weights = tuple(TargetWeight(symbol, weight) for symbol, weight in self._targets.items())
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(self._targets.values()),
            universe_version="test",
        )


def _verdict(
    *,
    target_weight: float,
    current_weight: float = 0.90,
    units: float = 20.0,
    lots: object,
    managed_qty: float = QTY,
    schedule: DownsizeGainBand | None = None,
) -> object:
    return downsize_gain_band_verdict(
        _schedule() if schedule is None else schedule,
        target_weight=target_weight,
        current_weight=current_weight,
        units=units,
        price=PRICE,
        lots=lots,  # type: ignore[arg-type]
        managed_qty=managed_qty,
        base_band=0.005,
    )


# ---------------------------------------------------------------------------
# The tier schedule
# ---------------------------------------------------------------------------


def test_band_schedule_tiers_including_the_inclusive_thresholds() -> None:
    """``g_frac <= 1 %`` selects 0.5 pp, ``<= 5 %`` 3 pp, above 8 pp: the
    pre-registered tiers, boundaries inclusive."""
    schedule = _schedule()
    assert schedule.band_for(0.0) == 0.005
    assert schedule.band_for(0.01) == 0.005  # boundary is the LOWER tier
    assert schedule.band_for(0.01 + 1e-12) == 0.03
    assert schedule.band_for(0.05) == 0.03  # boundary is the LOWER tier
    assert schedule.band_for(0.05 + 1e-12) == 0.08
    assert schedule.band_for(0.60) == 0.08


def test_schedule_validation_rejects_malformed_tables() -> None:
    with pytest.raises(ValueError, match="one more band than thresholds"):
        DownsizeGainBand(thresholds=(0.01, 0.05), bands=(0.005, 0.03))
    with pytest.raises(ValueError, match="strictly increasing"):
        DownsizeGainBand(thresholds=(0.05, 0.01), bands=(0.005, 0.03, 0.08))
    with pytest.raises(ValueError, match="thresholds must be in"):
        DownsizeGainBand(thresholds=(0.0, 0.05), bands=(0.005, 0.03, 0.08))
    with pytest.raises(ValueError, match="non-decreasing"):
        DownsizeGainBand(thresholds=(0.01, 0.05), bands=(0.03, 0.005, 0.08))
    with pytest.raises(ValueError, match="bands must be in"):
        DownsizeGainBand(thresholds=(0.01, 0.05), bands=(0.005, 0.03, 1.0))
    with pytest.raises(ValueError, match="schedule or null"):
        ExecutionConfig(downsize_gain_band="0.03")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The gain fraction (research A1u decision state)
# ---------------------------------------------------------------------------


def test_gain_fraction_is_fifo_over_the_oldest_lots() -> None:
    """The sale consumes the oldest lots first, so a cheap old lot sets the
    fraction and the newest (expensive) lot does not dilute it."""
    assert downsize_gain_fraction([(600.0, 40.0), (300.0, 99.5)], 300.0, PRICE) == pytest.approx(0.6)
    assert downsize_gain_fraction([(300.0, 99.5), (600.0, 40.0)], 300.0, PRICE) == pytest.approx(0.005)
    # A sale that spans two lots weights each consumed part: 300x0.5 + 100x60.
    assert downsize_gain_fraction([(300.0, 99.5), (600.0, 40.0)], 400.0, PRICE) == pytest.approx(6150 / 40000)
    # Consuming part of each of two lots weights both: 500x40 + 200x10 = 22000.
    assert downsize_gain_fraction([(500.0, 60.0), (500.0, 90.0)], 700.0, PRICE) == pytest.approx(22000 / 70000)


def test_a_loss_lot_offsets_a_gain_lot_and_the_total_floors_at_zero() -> None:
    """A1u flattens the sum, not each lot (PREREGISTRATION 4.1: "no per-lot
    floor other than gain >= 0"): a loss lot reduces the fraction, and a sale
    that is a net loss reports 0.0, i.e. the first tier."""
    assert downsize_gain_fraction([(100.0, 150.0), (800.0, 40.0)], 300.0, PRICE) == pytest.approx(7000 / 30000)
    assert downsize_gain_fraction([(300.0, 150.0)], 300.0, PRICE) == 0.0
    assert downsize_gain_fraction([(300.0, 40.0)], 300.0, PRICE) == pytest.approx(0.6)
    # An empty or too-small book cannot cover the sale: "not covered", not 0.0.
    assert downsize_gain_fraction([], 10.0, PRICE) is None
    assert downsize_gain_fraction([(5.0, 40.0)], 10.0, PRICE) is None


def test_verdict_reports_every_fallback_reason() -> None:
    """Each way the schedule cannot be evaluated keeps the constant band and
    names itself, so the INFO log and the decision record can explain it."""
    assert _verdict(target_weight=0.88, lots=None).fallback == "lots_unavailable"
    assert _verdict(target_weight=0.88, lots=[]).fallback == "lots_unavailable"
    assert _verdict(target_weight=0.88, lots=[(500.0, 40.0)]).fallback == "lot_book_mismatch"
    assert (
        _verdict(target_weight=0.88, lots=[(100.0, 40.0)], managed_qty=100.0, units=200.0).fallback
        == "lot_book_too_small"
    )
    applied = _verdict(target_weight=0.88, lots=[(900.0, 40.0)], units=20.0)
    assert applied.applied is True
    assert applied.fallback == ""
    assert applied.band == 0.08
    # Not a downward resize between two positive weights: the schedule never runs.
    assert _verdict(target_weight=1.0, lots=[(900.0, 40.0)]).applied is False
    assert _verdict(target_weight=0.0, lots=[(900.0, 40.0)]).applied is False


# ---------------------------------------------------------------------------
# Planner: tiers, unaffected cases, fail-safe
# ---------------------------------------------------------------------------


def test_disabled_policy_is_exactly_the_constant_band() -> None:
    """Without the schedule a 2 pp downsize with a 60 % embedded gain still
    trades, and nothing is recorded."""
    orders, holds = _plan(0.88, policy=PortfolioExecutionPolicy())
    assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
    assert holds == []


def test_first_tier_keeps_the_constant_band_behaviour() -> None:
    """A <= 1 % embedded gain selects 0.5 pp, so a 2 pp downsize still trades."""
    orders, holds = _plan(0.88, lots={"QLD": [(900.0, 99.5)]})
    assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
    assert holds == []


def test_second_tier_suppresses_only_gaps_under_three_percent() -> None:
    """A 3 % gain fraction selects 3 pp: a 2 pp gap is held, a 5 pp gap trades."""
    lots = {"QLD": [(900.0, 97.0)]}
    orders, holds = _plan(0.88, lots=lots)
    assert orders == ()
    assert [(hold.symbol, hold.band) for hold in holds] == [(SYMBOL, 0.03)]
    assert holds[0].g_frac == pytest.approx(0.03)
    assert holds[0].gap == pytest.approx(0.02)

    orders, holds = _plan(0.85, lots=lots)
    assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
    assert holds == []


def test_third_tier_suppresses_only_gaps_under_eight_percent() -> None:
    """A 60 % gain fraction selects 8 pp: 2 pp and 5 pp gaps are held, 9 pp trades."""
    for target_weight in (0.88, 0.85):
        orders, holds = _plan(target_weight)
        assert orders == ()
        assert [hold.band for hold in holds] == [0.08]
    orders, holds = _plan(0.81)
    assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
    assert holds == []


def test_threshold_boundaries_select_the_lower_tier_end_to_end() -> None:
    """Exactly 1 % sits in the first tier (0.5 pp band) and exactly 5 % in the
    second (3 pp): each fills a gap its own tier would have held."""
    at_one, above_one = _plan(0.88, lots={"QLD": [(900.0, 99.0)]})
    assert [order.side for order in at_one] == [PortfolioOrderSide.SELL]
    assert above_one == []
    held, holds = _plan(0.88, lots={"QLD": [(900.0, 98.9)]})
    assert held == ()
    assert [hold.g_frac for hold in holds] == pytest.approx([0.011])
    assert [hold.band for hold in holds] == [0.03]

    at_five, above_five = _plan(0.85, lots={"QLD": [(900.0, 95.0)]})
    assert [order.side for order in at_five] == [PortfolioOrderSide.SELL]
    assert above_five == []
    held, holds = _plan(0.85, lots={"QLD": [(900.0, 94.9)]})
    assert held == ()
    assert [hold.g_frac for hold in holds] == pytest.approx([0.051])
    assert [hold.band for hold in holds] == [0.08]


def test_entry_exit_and_up_resize_are_never_suppressed() -> None:
    """Only a downward resize between two positive weights is affected: an
    entry from flat, an exit to zero and an up-resize all keep 0.5 pp even
    with a 60 % embedded gain."""
    entry, entry_holds = _plan(0.90, qty=0.0, lots={"QLD": []})
    assert [(order.symbol, order.side) for order in entry] == [(SYMBOL, PortfolioOrderSide.BUY)]
    exit_orders, exit_holds = _plan(0.0)
    assert [(order.symbol, order.side) for order in exit_orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
    up, up_holds = _plan(0.98)
    assert [(order.symbol, order.side) for order in up] == [(SYMBOL, PortfolioOrderSide.BUY)]
    assert entry_holds == exit_holds == up_holds == []


def test_gap_below_the_constant_band_is_not_reported_as_a_hold() -> None:
    """A drift the constant band already skips is not attributed to the gain
    band, whatever the embedded gain is."""
    orders, holds = _plan(0.897, lots={"QLD": [(900.0, 40.0)]})
    assert orders == ()
    assert holds == []


def test_missing_or_mismatched_lots_fall_back_to_the_constant_band() -> None:
    """No lot book at all, or one that does not add up to the managed
    quantity, reproduces R2 exactly."""
    for lots in (None, {"QLD": [(500.0, 40.0)]}):
        orders, holds = _plan(0.88, lots=lots)
        assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
        assert holds == []


def test_planner_and_backtest_agree_on_the_gain_band() -> None:
    """The same (target, current, lots) triple gives the same order-existence
    verdict through the planner and through the backtest engine."""
    for target_weight in (0.897, 0.88, 0.86, 0.85, 0.81, 0.0, 0.98):
        planned, plan_holds = _plan(target_weight)
        backtested, engine_holds = _backtest_plan(target_weight)
        assert [(order.symbol, order.side.value) for order in planned] == backtested, target_weight
        assert plan_holds == list(engine_holds), target_weight


def test_backtest_without_a_lot_book_falls_back_to_the_constant_band() -> None:
    """A position the engine did not fill itself (no lots) keeps R2's band."""
    orders, holds = _backtest_plan(0.88, lots=[])
    assert orders == [(SYMBOL, OrderSide.SELL.value)]
    assert holds == ()


# ---------------------------------------------------------------------------
# Risk-driven reductions bypass the band (review fix)
# ---------------------------------------------------------------------------


def _risk_limits(**overrides: object) -> PortfolioRiskLimits:
    """Risk limits that never halt and never scale the sleeve on the fixtures."""
    values: dict[str, object] = {
        "daily_loss_limit": 0.20,
        "drawdown_halt": 0.75,
        "maximum_gross": 0.98,
        "maximum_equity": 0.98,
        "maximum_equity_asset": 0.98,
        "minimum_cash": 0.02,
        "maximum_positions": 8,
    }
    values.update(overrides)
    return PortfolioRiskLimits(**values)  # type: ignore[arg-type]


def _overlay(
    target: TargetPortfolio,
    *,
    limits: PortfolioRiskLimits | None = None,
    account: AccountSnapshot | None = None,
) -> RiskOverlayDecision:
    """The real overlay output for one strategy target."""
    return PortfolioRiskOverlay(limits or _risk_limits()).apply(
        target,
        account or _account(),
        market=Market.EQUITY,
        approved_symbols=(SYMBOL,),
        maximum_allocation=1.0,
        mode="paper",
    )


def test_risk_capped_target_is_not_held_by_the_band() -> None:
    """The overlay lowered the strategy's 0.92 to the 0.84 per-asset cap while
    the account holds 0.90: the 6 pp sell enforces the cap, so the band must
    not hold it. The same 6 pp gap as the strategy's OWN trim is still held."""
    risk = _overlay(_target(0.92), limits=_risk_limits(maximum_equity_asset=0.84))
    assert [weight.weight for weight in risk.target.weights] == [pytest.approx(0.84)]
    assert risk.target.metadata[GAIN_BAND_BYPASS_KEY] == (SYMBOL,)

    orders, holds = _plan_target(risk.target)
    assert [(order.side, order.quantity) for order in orders] == [(PortfolioOrderSide.SELL, 60.0)]
    assert holds == []

    control, control_holds = _plan(0.84)
    assert control == ()
    assert [(hold.symbol, hold.band, hold.gap) for hold in control_holds] == [(SYMBOL, 0.08, pytest.approx(0.06))]


def test_halted_decision_trades_its_own_trim() -> None:
    """A daily-loss breach caps the target at the held weight and forbids new
    risk: while the account is de-risking, its own 5 pp downsize executes
    instead of being deferred for tax. Without the breach the band holds it."""
    breach = replace(_account(), daily_pnl=-5_000.0)
    risk = _overlay(_target(0.85), limits=_risk_limits(daily_loss_limit=0.04), account=breach)
    assert risk.halted is True and risk.reasons == ("daily_loss_limit",)
    assert risk.target.metadata[GAIN_BAND_BYPASS_KEY] == (SYMBOL,)

    orders, holds = _plan_target(risk.target)
    assert [(order.side, order.quantity) for order in orders] == [(PortfolioOrderSide.SELL, 50.0)]
    assert holds == []

    quiet, quiet_holds = _plan(0.85)
    assert quiet == ()
    assert [hold.band for hold in quiet_holds] == [0.08]


def test_overlay_leaves_a_strategy_trim_band_eligible() -> None:
    """The overlay only marks what IT lowered: a cap above the strategy's own
    downsize keeps that downsize in the gain band."""
    risk = _overlay(_target(0.85), limits=_risk_limits(maximum_equity_asset=0.90))
    assert risk.target.metadata.get(GAIN_BAND_BYPASS_KEY) is None
    orders, holds = _plan_target(risk.target)
    assert orders == ()
    assert [hold.symbol for hold in holds] == [SYMBOL]


def test_backtest_engine_applies_the_same_risk_bypass() -> None:
    """Parity: the engine's own target application reads the same metadata, so
    a risk-lowered target trades there too."""
    capped = _overlay(_target(0.92), limits=_risk_limits(maximum_equity_asset=0.84)).target
    engine = _engine()
    engine._apply_target(capped)
    assert [(order.symbol, order.side.value) for order in engine._pending] == [(SYMBOL, OrderSide.SELL.value)]
    assert engine._gain_band_holds == []

    engine = _engine()
    engine._apply_target(_target(0.84))
    assert engine._pending == []
    assert [hold.symbol for hold in engine._gain_band_holds] == [SYMBOL]


def test_risk_reduction_only_construction_marks_its_symbols() -> None:
    """The entry-latch/intermediate-risk construction (min with the held
    weight) marks every symbol it caps: its sells are protective, never
    tax-deferred, whatever the embedded gain."""
    marked = gain_band_bypass_metadata({}, (SYMBOL,), Market.EQUITY)
    assert marked[GAIN_BAND_BYPASS_KEY] == (SYMBOL,)
    assert gain_band_bypass_symbols(_target(0.85), Market.EQUITY) == frozenset()
    bypassed = replace(_target(0.85), metadata=marked)
    assert gain_band_bypass_symbols(bypassed, Market.EQUITY) == frozenset({SYMBOL})
    orders, holds = _plan_target(bypassed)
    assert [(order.side, order.quantity) for order in orders] == [(PortfolioOrderSide.SELL, 50.0)]
    assert holds == []
    # Merging keeps what an earlier layer already marked.
    assert gain_band_bypass_metadata(marked, ("SPY",), Market.EQUITY)[GAIN_BAND_BYPASS_KEY] == ("QLD", "SPY")


# ---------------------------------------------------------------------------
# Backtest lot book
# ---------------------------------------------------------------------------


class _ScriptedStrategy(PortfolioStrategy):
    """One fixed target per scheduled decision, in order (last one repeated)."""

    name = "scripted_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, schedule: tuple[float, ...]) -> None:
        self._schedule = schedule
        self._index = 0
        super().__init__({"schedule": list(schedule)})

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weight = self._schedule[min(self._index, len(self._schedule) - 1)]
        self._index += 1
        weights = (TargetWeight(SYMBOL, weight),) if weight > 0 else ()
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - weight,
            universe_version="test",
        )


# 40 sessions: a flat start, then a one-step jump to 54 that leaves the position
# with an embedded gain and a small (0.7 pp) upward drift. The last target exits
# to zero, which the band never touches.
_SCRIPT_SESSIONS = tuple(T0 + timedelta(days=offset) for offset in range(40))
_SCRIPT_TARGETS = (0.90,) * 39 + (0.0,)
_SCRIPT_TARGETS_HOLD = (0.90,) * 39
_SCRIPT_TARGETS_REBUY = (0.90, 0.50, 0.90)
_SCRIPT_PRICES = (50.0, 50.0) + (54.0,) * (len(_SCRIPT_SESSIONS) - 2)


def _scripted_run(
    policy: PortfolioExecutionPolicy,
    *,
    targets: tuple[float, ...] = _SCRIPT_TARGETS,
    prices: tuple[float, ...] = _SCRIPT_PRICES,
) -> PortfolioBacktestEngine:
    bars = [
        Bar(timestamp, SYMBOL, "1d", close, close, close, close, 1_000_000, Market.EQUITY)
        for timestamp, close in zip(_SCRIPT_SESSIONS, prices, strict=True)
    ]
    bars += [
        Bar(timestamp, "SPY", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY)
        for timestamp in _SCRIPT_SESSIONS
    ]
    engine = PortfolioBacktestEngine(
        _ScriptedStrategy(targets),
        RebalancePolicy(Market.EQUITY, "hours:24"),
        universe=PointInTimeUniverse(
            Market.EQUITY,
            (
                UniverseSnapshot(
                    effective_at=_SCRIPT_SESSIONS[0],
                    symbols=frozenset({SYMBOL, "SPY"}),
                    observation_set=(SYMBOL, "SPY"),
                    maximum_stream_symbols=3,
                    pinned_symbols=(SYMBOL, "SPY"),
                ),
            ),
            "a1u fixture",
        ),
        universe_version="a1u",
        allow_static_universe=True,
        starting_equity=EQUITY,
        slippage_bps=0.0,
        spread_bps=0.0,
        commission=0.0,
        maximum_allocation=1.0,
        execution_policy=policy,
    )
    engine.run(bars, liquidate_at_end=False)
    return engine


def _scripted_position_weight(engine: PortfolioBacktestEngine) -> float:
    return engine._positions[SYMBOL].qty * _SCRIPT_PRICES[-1] / engine._equity()


class _RuntimeBroker:
    """Minimal PAPER broker: one managed QLD position and no working orders."""

    def __init__(self, *, qty: float, price: float, equity: float) -> None:
        self.position = PositionSnapshot(
            SYMBOL,
            qty,
            price,
            price,
            Market.EQUITY,
            qty_available=qty,
            ownership="managed",
            managed_qty=qty,
        )
        self._equity = equity
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        value = self.position.market_value
        return AccountSnapshot(
            cash=self._equity - value,
            equity=self._equity,
            peak_equity=self._equity,
            daily_pnl=0.0,
            positions=(self.position,),
        )

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(0.001, 0.001, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def working_buy_orders(self) -> tuple[()]:
        return ()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


class _TestUniverse:
    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return True


def _runtime_bars(count: int = 3) -> list[Bar]:
    return [
        Bar(
            T0 + timedelta(days=offset),
            symbol,
            "1d",
            PRICE,
            PRICE,
            PRICE,
            PRICE,
            1_000_000,
            Market.EQUITY,
        )
        for offset in range(count)
        for symbol in ("SPY", SYMBOL)
    ]


def _lots_from_fills(fills: tuple[object, ...], symbol: str) -> list[list[float]]:
    """Independent FIFO replay of the engine's own fills, to compare against its book."""
    lots: list[list[float]] = []
    for fill in fills:
        if fill.symbol != symbol:  # type: ignore[attr-defined]
            continue
        notional = fill.qty * fill.fill_price  # type: ignore[attr-defined]
        if fill.side == OrderSide.BUY:  # type: ignore[attr-defined]
            lots.append([fill.qty, (notional + fill.fee) / fill.qty])  # type: ignore[attr-defined]
            continue
        remaining = fill.qty  # type: ignore[attr-defined]
        while remaining > 1e-12 and lots:
            taken = min(remaining, lots[0][0])
            lots[0][0] -= taken
            remaining -= taken
            if lots[0][0] <= 1e-12:
                lots.pop(0)
    return lots


def test_engine_lot_book_matches_its_own_fills_through_partial_sells() -> None:
    """The engine's lot book is exactly its fills replayed FIFO: buys append, a
    partial sell consumes the oldest lot, a re-buy appends a second lot, and the
    sum always equals the position quantity."""
    engine = _scripted_run(
        PortfolioExecutionPolicy(),
        targets=_SCRIPT_TARGETS_REBUY,
    )
    fills = engine._fills
    assert [fill.side for fill in fills] == [OrderSide.BUY, OrderSide.SELL, OrderSide.BUY]
    position = engine._positions[SYMBOL]
    assert len(position.lots) == 2
    assert [value for lot in position.lots for value in lot] == pytest.approx(
        [value for lot in _lots_from_fills(fills, SYMBOL) for value in lot]
    )
    assert sum(lot[0] for lot in position.lots) == pytest.approx(position.qty)
    # The partial sell consumed the oldest lot only: the first lot kept the first
    # (older, cheaper) buy's cost but shrank, and the re-buy is the newer lot.
    assert position.lots[0][1] == pytest.approx(fills[0].fill_price)
    assert position.lots[0][0] < fills[0].qty
    assert position.lots[1][1] == pytest.approx(fills[2].fill_price)


def test_engine_lot_book_rescales_with_a_split_like_the_position() -> None:
    engine = _engine()
    engine._apply_corporate_action(
        CorporateAction(
            timestamp=T0,
            symbol=SYMBOL,
            action_type=CorporateActionType.SPLIT,
            value=2.0,
        )
    )
    position = engine._positions[SYMBOL]
    assert position.qty == pytest.approx(1800.0)
    assert position.avg_price == pytest.approx(50.0)
    assert position.lots == [[1200.0, 20.0], [600.0, 49.75]]
    assert sum(lot[0] for lot in position.lots) == pytest.approx(position.qty)


def test_synthetic_backtest_suppresses_a_downsize_that_the_disabled_run_trades() -> None:
    """A positive-to-positive downsize with an embedded gain is held by the band
    and traded without it: the policy, not the strategy, changed the fills."""
    enabled = _scripted_run(_policy())
    disabled = _scripted_run(PortfolioExecutionPolicy())

    holds = [hold for record in enabled._rebalances for hold in record.gain_band_holds]
    assert holds
    assert {hold.symbol for hold in holds} == {SYMBOL}
    assert {hold.band for hold in holds} == {0.08}
    assert all(hold.gap >= 0.005 for hold in holds)
    assert all(record.gain_band_holds == () for record in disabled._rebalances)

    enabled_sells = [fill for fill in enabled._fills if fill.side == OrderSide.SELL]
    disabled_sells = [fill for fill in disabled._fills if fill.side == OrderSide.SELL]
    # The trim fills only in the disabled run; the exit to zero is planned in
    # both (it is the last session, so no later bar can fill it).
    assert enabled_sells == []
    assert disabled_sells
    assert [order.side for order in enabled._pending] == [OrderSide.SELL]
    assert [order.side for order in disabled._pending] == [OrderSide.SELL]


def test_synthetic_backtest_holds_the_downsize_but_never_the_exit() -> None:
    """With the exit to zero removed, the band holds every trim after the entry:
    the position the constant band would have cut back stays at its full size."""
    engine = _scripted_run(_policy(), targets=_SCRIPT_TARGETS_HOLD)
    assert engine._fills
    assert all(fill.side == OrderSide.BUY for fill in engine._fills)
    records = engine._rebalances
    # Every session the constant band would have traded a downsize is held instead.
    trims = [record for record in records if dict(record.pre_trade_weights).get(SYMBOL, 0.0) - 0.90 >= 0.005]
    assert trims
    assert all(record.gain_band_holds for record in trims)
    assert records[0].gain_band_holds == ()  # the entry is never held
    assert engine._pending == []
    assert _scripted_position_weight(engine) > 0.905


# ---------------------------------------------------------------------------
# Runtime lot reconstruction from the persisted fill ledger
# ---------------------------------------------------------------------------


def _order_fill(
    repo: PortfolioStateRepository,
    side: str,
    quantity: float,
    price: float,
    when: datetime,
    *,
    fee: float = 0.0,
) -> None:
    session = repo.begin_session(ACCOUNT, "digest", "paper")
    decision_id = repo.record_decision_with_orders(
        session,
        ACCOUNT,
        "digest",
        TargetPortfolio("vt35", "1.2.0", when, (), 1.0),
        RiskOverlayDecision(TargetPortfolio("vt35", "1.2.0", when, (), 1.0), False, (), 0.0, 0.0),
        Market.EQUITY,
        (),
    )
    client_order_id = f"cid-{side}-{when.timestamp()}"
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id=client_order_id,
            decision_id=decision_id,
            symbol=SYMBOL,
            market=Market.EQUITY,
            side=PortfolioOrderSide(side),
            quantity=quantity,
            reference_price=price,
            decision_timestamp=when,
            strategy_name="vt35",
            strategy_version="1.2.0",
        ),
        "submitted",
    )
    repo.apply_trade_update(
        client_order_id=client_order_id,
        broker_order_id=f"broker-{client_order_id}",
        status="filled",
        cumulative_filled_quantity=quantity,
        average_fill_price=price,
        occurred_at=when,
        activity_id=f"activity-{client_order_id}",
        fee=fee,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _lot_pairs(lots: tuple[tuple[float, float], ...]) -> list[float]:
    """Flat ``[qty, cost, qty, cost, ...]``: ``pytest.approx`` has no nesting."""
    return [value for lot in lots for value in lot]


def test_runtime_lots_come_from_the_bot_fill_ledger_fifo_and_fee_inclusive(tmp_path: Path) -> None:
    """Lots are this account's own fills: oldest first, consumed by its sells,
    with the buy's fees in the basis, so the live decision reads the same
    fraction as the backtest."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _order_fill(repo, "buy", 100.0, 50.0, T0, fee=5.0)
    _order_fill(repo, "buy", 100.0, 80.0, T0 + timedelta(days=1))
    assert _lot_pairs(repo.owned_fifo_lots(ACCOUNT, SYMBOL, Market.EQUITY)) == pytest.approx(
        [100.0, 50.05, 100.0, 80.0],
    )
    _order_fill(repo, "sell", 150.0, 90.0, T0 + timedelta(days=2))
    assert repo.owned_quantities(ACCOUNT, Market.EQUITY)[SYMBOL] == pytest.approx(50.0)
    assert _lot_pairs(repo.owned_fifo_lots(ACCOUNT, SYMBOL, Market.EQUITY)) == pytest.approx([50.0, 80.0])
    _order_fill(repo, "sell", 50.0, 90.0, T0 + timedelta(days=3))
    assert repo.owned_fifo_lots(ACCOUNT, SYMBOL, Market.EQUITY) == ()


def test_runtime_holds_the_downsize_and_persists_it_with_the_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PAPER/LIVE end to end: the planner reads the bot's own lots from the fill
    ledger, the band holds a profitable downsize, and the persisted decision
    carries the hold for the operator (and the daily summary). The decision
    summary names the hold instead of claiming the target did not change."""
    summaries: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime_service,
        "_observe",
        SimpleNamespace(on_decision_summary=lambda **fields: summaries.append(fields)),
    )
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _order_fill(repo, "buy", 900.0, 50.0, T0)
    broker = _RuntimeBroker(qty=900.0, price=PRICE, equity=EQUITY)
    runtime = PortfolioRuntime(
        _ScriptedStrategy((0.85,)),
        ("SPY", SYMBOL),
        broker,
        repo,
        mode="paper",
        maximum_allocation=1.0,
        allow_static_universe=True,
        account_id=ACCOUNT,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
        risk_limits=PortfolioRiskLimits(
            daily_loss_limit=0.20,
            drawdown_halt=0.75,
            maximum_gross=0.98,
            maximum_equity=0.98,
            maximum_equity_asset=0.98,
            minimum_cash=0.02,
            maximum_positions=8,
        ),
        execution_policy=PortfolioExecutionPolicy(downsize_gain_band=SHIPPED),
    )
    assert runtime._managed_open_lots((SYMBOL,)) == {SYMBOL: ((900.0, 50.0),)}

    # The fixture's seeded fill records a decision under another digest; clear the
    # cadence it carries (R2-8-5) so the decision is judged on its own terms.
    runtime.previous_timestamp = None
    result = runtime.process_closed_bars(_runtime_bars())
    assert result.orders == ()
    # 1000 shares at $100 on $100k is 0.90; a 0.85 target with a 50 % gain is held.
    (hold,) = result.target.metadata["gain_band"]
    assert hold["symbol"] == SYMBOL
    assert hold["g_frac"] == pytest.approx(0.5)
    assert hold["band"] == 0.08
    assert hold["gap"] == pytest.approx(0.05)
    row = repo.decision_row(result.decision_id)
    assert json.loads(row["target_json"])["metadata"]["gain_band"] == list(result.target.metadata["gain_band"])
    assert [summary["no_trade_reason"] for summary in summaries] == ["GAIN_BAND_HOLD"]


def test_runtime_trades_a_risk_capped_downsize(tmp_path: Path) -> None:
    """PAPER end to end: the per-asset cap lowers the strategy's 0.92 target to
    0.84 while the account holds 0.90, and the runtime still submits the 6 pp
    SELL (the band is not given the chance to defer it)."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _order_fill(repo, "buy", 900.0, 50.0, T0)
    broker = _RuntimeBroker(qty=900.0, price=PRICE, equity=EQUITY)
    runtime = PortfolioRuntime(
        _ScriptedStrategy((0.92,)),
        ("SPY", SYMBOL),
        broker,
        repo,
        mode="paper",
        maximum_allocation=1.0,
        allow_static_universe=True,
        account_id=ACCOUNT,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
        risk_limits=_risk_limits(maximum_equity_asset=0.84),
        execution_policy=PortfolioExecutionPolicy(downsize_gain_band=SHIPPED),
    )
    # The fixture's seeded fill records a decision under another digest; clear the
    # cadence it carries (R2-8-5) so the decision is judged on its own terms.
    runtime.previous_timestamp = None
    result = runtime.process_closed_bars(_runtime_bars())
    assert [(order.side, order.quantity) for order in result.orders] == [(PortfolioOrderSide.SELL, 60.0)]
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert result.target.weight_for(SYMBOL, Market.EQUITY) == pytest.approx(0.84)
    metadata = json.loads(repo.decision_row(result.decision_id)["target_json"])["metadata"]
    assert metadata[GAIN_BAND_BYPASS_KEY] == [SYMBOL]
    assert "gain_band" not in metadata


def test_runtime_lots_ignore_another_accounts_fills(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _order_fill(repo, "buy", 100.0, 50.0, T0)
    assert repo.owned_fifo_lots("other-account", SYMBOL, Market.EQUITY) == ()
    assert _lot_pairs(repo.owned_fifo_lots(ACCOUNT, SYMBOL, Market.EQUITY)) == pytest.approx([100.0, 50.0])


# ---------------------------------------------------------------------------
# Configuration, digest and observability
# ---------------------------------------------------------------------------


def test_shipped_config_enables_the_research_schedule() -> None:
    config = load_config(str(Path(__file__).resolve().parents[1] / "config" / "settings.yaml"))
    assert config.execution.downsize_gain_band == SHIPPED
    policy = portfolio_execution_policy_from_config(config.execution)
    assert policy.downsize_gain_band == SHIPPED
    assert policy.rebalance_band_pct == config.execution.rebalance_band_pct


def test_absent_key_keeps_the_shipped_schedule_and_null_disables_it(tmp_path: Path) -> None:
    """A missing/renamed key must not silently drop the shipped rule (the R2
    precedent); ``downsize_gain_band: null`` is the explicit opt-out."""
    import yaml

    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert raw["execution"]["downsize_gain_band"] == {"thresholds": [0.01, 0.05], "bands": [0.005, 0.03, 0.08]}
    path = tmp_path / "settings.yaml"

    del raw["execution"]["downsize_gain_band"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert load_config(str(path)).execution.downsize_gain_band == SHIPPED

    raw["execution"]["downsize_gain_band"] = None
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert load_config(str(path)).execution.downsize_gain_band is None


def test_malformed_shipped_schedule_fails_loudly(tmp_path: Path) -> None:
    import yaml

    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw["execution"]["downsize_gain_band"] = {"thresholds": [0.01, 0.05], "bands": [0.005, 0.03]}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="one more band than thresholds"):
        load_config(str(path))

    raw["execution"]["downsize_gain_band"] = "0.03"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping or null"):
        load_config(str(path))


def test_configuration_digest_changes_when_the_band_is_enabled() -> None:
    """The schedule is part of the live-authorization digest, so an approval
    for the constant-band policy cannot authorize the A1u one."""
    strategy = _FixedTargetStrategy({SYMBOL: 0.90})
    arguments = (strategy, "paper", 0.98, "universe-v1")
    without = configuration_digest(*arguments, execution_policy=PortfolioExecutionPolicy())
    with_band = configuration_digest(*arguments, execution_policy=_policy())
    other_bands = configuration_digest(
        *arguments,
        execution_policy=PortfolioExecutionPolicy(
            downsize_gain_band=DownsizeGainBand(thresholds=(0.02,), bands=(0.005, 0.05))
        ),
    )
    assert len({without, with_band, other_bands}) == 3


def _metadata_target(holds: tuple[GainBandHold, ...]) -> TargetPortfolio:
    return record_gain_band_holds(_target(0.88), holds)


def test_suppressed_hold_reaches_the_decision_metadata_and_the_daily_summary() -> None:
    """An operator sees why the target was not traded down, in the persisted
    metadata and in the one-line summary."""
    hold = GainBandHold(SYMBOL, 0.072, 0.08, 0.02)
    target = _metadata_target((hold,))
    assert target.metadata["gain_band"] == ({"symbol": SYMBOL, "g_frac": 0.072, "band": 0.08, "gap": 0.02},)
    message, fields = build_equity_daily_summary(target, _account(), status="submitted", decision_id="d1")
    assert "gain band held (g 7.2%, band 8pp)" in message
    assert fields["gain_band"] == "gain band held (g 7.2%, band 8pp)"


def test_summary_and_metadata_are_unchanged_without_a_suppressed_hold() -> None:
    target = _target(0.88)
    plain = build_equity_daily_summary(target, _account(), status="submitted", decision_id="d1")
    assert "gain band" not in plain[0]
    assert plain[1]["gain_band"] is None
    assert _metadata_target(()).metadata == target.metadata
    two = _metadata_target((GainBandHold(SYMBOL, 0.072, 0.08, 0.02), GainBandHold("SPY", 0.012, 0.03, 0.02)))
    message, fields = build_equity_daily_summary(two, _account(), status="submitted", decision_id="d1")
    assert "gain band held QLD (g 7.2%, band 8pp); gain band held SPY (g 1.2%, band 3pp)" in message
    assert "QLD" in str(fields["gain_band"])


def test_planner_records_the_hold_it_suppresses() -> None:
    """The planner's diagnostics sink matches the metadata payload."""
    orders, holds = _plan(0.88)
    assert orders == ()
    assert [hold.as_metadata() for hold in holds] == [
        {"symbol": SYMBOL, "g_frac": pytest.approx(0.6), "band": 0.08, "gap": pytest.approx(0.02)}
    ]
    target = record_gain_band_holds(_target(0.88), holds)
    assert target.metadata["gain_band"] == tuple(hold.as_metadata() for hold in holds)
