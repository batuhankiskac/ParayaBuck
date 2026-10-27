"""December downsize deferral H2 (``docs/research/band_h2_combo_v1.md``).

A downward resize between two positive weights whose FILL session is one of the
last ``execution.december_downsize_deferral_sessions`` NYSE sessions of
December is postponed; the ordinary rule (including the A1u gain-scaled band) is
re-evaluated at every later decision, so it executes from the first January
decision on. Exits to zero, entries, up-resizes and risk-driven reductions are
never deferred. Every fixture is synthetic and deterministic: no network, no
production path, ``tmp_path`` state only, and the window itself is asserted
against the real NYSE calendar over committed history years.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    _Position,
)
from parayabuck.config import (
    DownsizeGainBand,
    ExecutionConfig,
    load_config,
)
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    DECEMBER_DEFERRAL_KEY,
    GAIN_BAND_BYPASS_KEY,
    AssetTradingRules,
    DecemberDeferralHold,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    TargetToOrderPlanner,
    gain_band_bypass_metadata,
    in_december_deferral_window,
    portfolio_execution_policy_from_config,
    record_december_deferral_holds,
)
from parayabuck.markets.nyse_calendar import NEW_YORK, nyse_sessions, session_bounds
from parayabuck.monitoring.daily_summary import build_equity_daily_summary
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.portfolio.runtime_service import PortfolioRuntime, _downsize_hold_reason, configuration_digest
from tests.test_gain_band_a1u import (
    ACCOUNT,
    EQUITY,
    PRICE,
    QTY,
    SYMBOL,
    _order_fill,
    _risk_limits,
    _RuntimeBroker,
    _ScriptedStrategy,
    _TestUniverse,
)

# Real NYSE sessions (2025-12-16 and 2025-12-17 are inside the last-10 window of
# December 2025; 2025-12-15 is one session before it, so its fill on the 16th is
# not deferred; 2026-01-02 is the first January session, after the holiday).
INSIDE = datetime(2025, 12, 16, tzinfo=NEW_YORK)
INSIDE_LATER = datetime(2025, 12, 17, tzinfo=NEW_YORK)
LAST_DECEMBER_SESSION = datetime(2025, 12, 31, tzinfo=NEW_YORK)
BEFORE_WINDOW = datetime(2025, 12, 15, tzinfo=NEW_YORK)
JANUARY = datetime(2026, 1, 2, tzinfo=NEW_YORK)
SHIPPED = DownsizeGainBand()
ROOT = Path(__file__).resolve().parents[1]


def _et(when: date) -> datetime:
    """The ET session label of ``when`` (how the provider stamps a daily bar)."""
    return datetime(when.year, when.month, when.day, tzinfo=NEW_YORK)


def _deferral_policy(sessions: int = 10, *, band: DownsizeGainBand | None = None) -> PortfolioExecutionPolicy:
    return PortfolioExecutionPolicy(downsize_gain_band=band, december_deferral_sessions=sessions)


def _target(weight: float, timestamp: datetime = INSIDE) -> TargetPortfolio:
    weights = (TargetWeight(SYMBOL, weight),) if weight > 0 else ()
    return TargetPortfolio("vt35", "1.2.0", timestamp, weights, 1.0 - weight, universe_version="test")


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


def _plan(
    target: TargetPortfolio,
    *,
    policy: PortfolioExecutionPolicy,
    qty: float = QTY,
    lots: tuple[tuple[float, float], ...] = ((900.0, 60.0),),
) -> tuple[tuple[PortfolioOrderRequest, ...], list[DecemberDeferralHold]]:
    """Planner verdict for one target, with the postponed downsizes collected."""
    holds: list[DecemberDeferralHold] = []
    orders = TargetToOrderPlanner(policy).plan(
        target,
        _account(qty=qty),
        market=Market.EQUITY,
        prices={SYMBOL: PRICE},
        rules={SYMBOL: AssetTradingRules(0.001, 0.001, 1.0)},
        approved_symbols=(SYMBOL,),
        decision_id="h2-decision",
        open_lots={SYMBOL: lots} if lots else {},
        december_deferral_holds=holds,
    )
    return orders, holds


def _engine(
    *, policy: PortfolioExecutionPolicy, qty: float = QTY, lots: list[list[float]] | None = None
) -> PortfolioBacktestEngine:
    engine = PortfolioBacktestEngine(
        _ScriptedStrategy((0.85,)),
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=EQUITY,
        slippage_bps=0.0,
        spread_bps=0.0,
        commission=0.0,
        execution_policy=policy,
        allow_static_universe=True,
    )
    engine._cash = EQUITY - qty * PRICE
    engine._last_price[SYMBOL] = PRICE
    if qty != 0:
        engine._positions[SYMBOL] = _Position(
            SYMBOL,
            Market.EQUITY,
            "1d",
            qty,
            PRICE,
            datetime(2025, 12, 1, tzinfo=NEW_YORK),
            lots=lots if lots is not None else [[900.0, 60.0]],
        )
    return engine


def _backtest(
    target: TargetPortfolio,
    *,
    policy: PortfolioExecutionPolicy,
    qty: float = QTY,
    lots: list[list[float]] | None = None,
) -> tuple[list[tuple[str, str]], tuple[DecemberDeferralHold, ...]]:
    engine = _engine(policy=policy, qty=qty, lots=lots)
    engine._apply_target(target)
    return (
        [(order.symbol, order.side.value) for order in engine._pending],
        tuple(engine._december_deferral_holds),
    )


# ---------------------------------------------------------------------------
# The window: the last 10 NYSE sessions of December (research convention)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("year", (2019, 2020, 2021, 2022, 2024, 2025))
def test_window_is_the_last_ten_nyse_december_sessions(year: int) -> None:
    """Every session of the year is classified against an independent reading
    of the NYSE calendar: the research arm defers a decision whose FILL session
    (``screen.py`` ``december_tail``/``i in dec_tail``) is one of the last 10
    December sessions, and both sides use the exchange calendar.
    """
    sessions = [day for day, _, _ in nyse_sessions(date(year, 1, 1), date(year, 12, 31))]
    december = [day for day in sessions if day.month == 12]
    assert len(december) >= 10
    tail = set(december[-10:])
    expected = {sessions[index - 1] for index, day in enumerate(sessions) if day in tail}
    for day in sessions:
        assert in_december_deferral_window(_et(day), 10) == (day in expected), day
    # One distinct decision session per window session, all of them in
    # December: the fill session is the next NYSE session, so the window holds
    # exactly 10 decisions and never reaches into November.
    assert len(expected) == 10
    assert {day.month for day in expected} == {12}
    # The window is not the December sessions themselves: the last session's
    # decision fills in January and is not deferred.
    assert in_december_deferral_window(_et(december[-1]), 10) is False


def test_window_uses_real_holidays_and_early_closes() -> None:
    """2021 observed Christmas on Friday 24 December (a holiday, so the window
    is still 10 NYSE sessions); 2024's 24 December was a 13:00 ET early close
    and is a session, so a decision that day is deferred."""
    assert session_bounds(date(2021, 12, 24)) is None
    assert in_december_deferral_window(_et(date(2021, 12, 16)), 10) is True
    assert in_december_deferral_window(_et(date(2021, 12, 15)), 10) is False
    assert in_december_deferral_window(_et(date(2021, 12, 30)), 10) is True
    assert in_december_deferral_window(_et(date(2021, 12, 31)), 10) is False

    assert session_bounds(date(2024, 12, 25)) is None
    bounds = session_bounds(date(2024, 12, 24))
    assert bounds is not None and bounds[1].hour == 18  # 13:00 ET
    assert in_december_deferral_window(_et(date(2024, 12, 24)), 10) is True
    assert in_december_deferral_window(_et(date(2024, 12, 13)), 10) is False


def test_window_boundaries_and_off() -> None:
    """Zero sessions is off, a small window shrinks to the last N sessions, and
    a decision whose fill session is the last December session is still inside
    the window (it is the next session, not its own)."""
    assert in_december_deferral_window(INSIDE, 0) is False
    assert in_december_deferral_window(INSIDE, 3) is False  # tail: 29, 30, 31
    assert in_december_deferral_window(_et(date(2025, 12, 26)), 3) is True  # fills 29
    assert in_december_deferral_window(_et(date(2025, 12, 30)), 10) is True  # fills 31
    assert in_december_deferral_window(LAST_DECEMBER_SESSION, 10) is False  # fills 2026-01-02
    assert in_december_deferral_window(JANUARY, 10) is False


# ---------------------------------------------------------------------------
# The hold: a downsize between two positive weights, and nothing else
# ---------------------------------------------------------------------------


def test_december_downsize_is_postponed_without_the_band() -> None:
    """H2 alone: the disabled band changes nothing, the in-window downsize
    produces no order and is recorded with the decision."""
    orders, holds = _plan(_target(0.85), policy=_deferral_policy())
    assert orders == ()
    assert [hold.as_metadata() for hold in holds] == [
        {
            "symbol": SYMBOL,
            "target_weight": pytest.approx(0.85),
            "current_weight": pytest.approx(0.90),
            "sessions": 10,
        }
    ]
    target = record_december_deferral_holds(_target(0.85), holds)
    assert target.metadata[DECEMBER_DEFERRAL_KEY] == tuple(hold.as_metadata() for hold in holds)


def test_the_same_downsize_trades_outside_the_window() -> None:
    orders, holds = _plan(_target(0.85, BEFORE_WINDOW), policy=_deferral_policy())
    assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
    assert holds == []


@pytest.mark.parametrize(
    "target",
    [0.0, 0.95],
)
def test_exits_and_up_resizes_are_never_deferred(target: float) -> None:
    orders, holds = _plan(_target(target), policy=_deferral_policy())
    assert len(orders) == 1
    assert orders[0].side == (PortfolioOrderSide.SELL if target == 0.0 else PortfolioOrderSide.BUY)
    assert holds == []


def test_an_entry_is_never_deferred() -> None:
    orders, holds = _plan(_target(0.85), policy=_deferral_policy(), qty=0.0, lots=())
    assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.BUY)]
    assert holds == []


def test_an_exactly_on_band_drift_is_not_a_deferral() -> None:
    """The window only postpones an order that would otherwise exist: a drift
    inside the constant band is not a deferral and is not recorded as one."""
    orders, holds = _plan(_target(0.899), policy=_deferral_policy())
    assert orders == () and holds == []


def test_a_risk_driven_reduction_is_never_deferred() -> None:
    """The review fix: per-asset cap / halt / operator reductions marked
    ``gain_band_bypass`` are risk enforcement, so they execute even inside the
    window."""
    target = replace(_target(0.85), metadata=gain_band_bypass_metadata({}, (SYMBOL,), Market.EQUITY))
    assert target.metadata[GAIN_BAND_BYPASS_KEY] == (SYMBOL,)
    orders, holds = _plan(target, policy=_deferral_policy())
    assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
    assert holds == []


def test_the_backtest_engine_defers_the_same_downsize() -> None:
    pending, holds = _backtest(_target(0.85), policy=_deferral_policy())
    assert pending == []
    assert [hold.as_metadata() for hold in holds] == [
        {
            "symbol": SYMBOL,
            "target_weight": pytest.approx(0.85),
            "current_weight": pytest.approx(0.90),
            "sessions": 10,
        }
    ]
    outside, outside_holds = _backtest(_target(0.85, BEFORE_WINDOW), policy=_deferral_policy())
    assert outside == [(SYMBOL, OrderSide.SELL.value)]
    assert outside_holds == ()


def test_the_engine_records_the_deferral_on_the_rebalance_record() -> None:
    engine = _engine(policy=_deferral_policy())
    engine._apply_target(_target(0.85))
    engine._record_rebalance(_target(0.85))
    (record,) = engine._rebalances
    assert [hold.symbol for hold in record.december_deferral_holds] == [SYMBOL]
    assert record.gain_band_holds == ()


def test_the_deferred_resize_executes_from_the_first_january_decision() -> None:
    """The ordinary rule is re-evaluated at every later decision, and no state
    is carried: the December decisions hold, the first January one sells."""
    policy = _deferral_policy()
    for when in (INSIDE, INSIDE_LATER):
        orders, holds = _plan(_target(0.85, when), policy=policy)
        assert orders == (), when
        assert len(holds) == 1, when
    orders, holds = _plan(_target(0.85, JANUARY), policy=policy)
    assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
    assert holds == []


def test_a_decision_on_the_last_december_session_is_not_deferred() -> None:
    """The window is defined by the FILL session (``screen.py``), and the last
    December session fills at the first January open: there is nothing left to
    postpone, so that resize executes and January inherits the ordinary rule."""
    orders, holds = _plan(_target(0.85, LAST_DECEMBER_SESSION), policy=_deferral_policy())
    assert [(order.symbol, order.side) for order in orders] == [(SYMBOL, PortfolioOrderSide.SELL)]
    assert holds == []


# ---------------------------------------------------------------------------
# Composition K: the December test first, then the A1u band
# ---------------------------------------------------------------------------


def test_in_window_a_downsize_is_held_whatever_its_gain_fraction() -> None:
    """Composition with A1u: a 5 pp downsize of lots 0.1 % in profit would be
    traded by the band's first tier outside the window, and is still held
    inside it (H2 is tested first, as the research composition K does)."""
    policy = _deferral_policy(band=SHIPPED)
    lots = ((900.0, 99.9),)
    orders, holds = _plan(_target(0.85), policy=policy, lots=lots)
    assert orders == () and len(holds) == 1

    outside, outside_holds = _plan(_target(0.85, BEFORE_WINDOW), policy=policy, lots=lots)
    assert [(order.symbol, order.side) for order in outside] == [(SYMBOL, PortfolioOrderSide.SELL)]
    assert outside_holds == []


def test_out_of_window_the_band_still_decides() -> None:
    """Out of the window the band rule is untouched: a 2 pp downsize of lots
    60 % in profit stays held by the band (8 pp) and the reason recorded is the
    band's, not the deferral's."""
    orders, holds = _plan(_target(0.88, BEFORE_WINDOW), policy=_deferral_policy(band=SHIPPED))
    assert orders == () and holds == []

    engine = _engine(policy=_deferral_policy(band=SHIPPED))
    engine._apply_target(_target(0.88, BEFORE_WINDOW))
    assert engine._pending == []
    assert [hold.symbol for hold in engine._gain_band_holds] == [SYMBOL]
    assert engine._december_deferral_holds == []


def test_in_window_the_counter_is_the_deferral_not_the_band() -> None:
    """Both rules would hold this 5 pp downsize; inside the window the
    postponement is recorded as a deferral, exactly like the research's split
    ``blocked_band`` / ``deferred`` counters."""
    engine = _engine(policy=_deferral_policy(band=SHIPPED))
    engine._apply_target(_target(0.85))
    assert engine._pending == []
    assert engine._gain_band_holds == []
    assert [hold.sessions for hold in engine._december_deferral_holds] == [10]


# ---------------------------------------------------------------------------
# Configuration, digest and observability
# ---------------------------------------------------------------------------


def test_shipped_config_enables_the_window() -> None:
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    assert config.execution.december_downsize_deferral_sessions == 10
    policy = portfolio_execution_policy_from_config(config.execution)
    assert policy.december_deferral_sessions == 10
    assert policy.downsize_gain_band == config.execution.downsize_gain_band


def test_absent_key_keeps_the_shipped_window_and_zero_disables_it(tmp_path: Path) -> None:
    """A missing/renamed key must not silently drop the shipped rule; ``0`` is
    the explicit opt-out (the A1u ``null`` precedent)."""
    import yaml

    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert raw["execution"]["december_downsize_deferral_sessions"] == 10
    path = tmp_path / "settings.yaml"

    del raw["execution"]["december_downsize_deferral_sessions"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert load_config(str(path)).execution.december_downsize_deferral_sessions == 10

    raw["execution"]["december_downsize_deferral_sessions"] = 0
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert load_config(str(path)).execution.december_downsize_deferral_sessions == 0


@pytest.mark.parametrize("bad", [-1, 1.5, "10", True, None])
def test_a_malformed_window_fails_loudly(tmp_path: Path, bad: object) -> None:
    import yaml

    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw["execution"]["december_downsize_deferral_sessions"] = bad
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="december_downsize_deferral_sessions must be a non-negative integer"):
        load_config(str(path))
    with pytest.raises(ValueError, match="must be a non-negative integer"):
        ExecutionConfig(december_downsize_deferral_sessions=bad)  # type: ignore[arg-type]


def test_configuration_digest_changes_with_the_window() -> None:
    """Order existence changes with the window, so it belongs to the digest an
    operator approval is bound to."""
    strategy = _ScriptedStrategy((0.85,))
    arguments = (strategy, "paper", 0.98, "universe-v1")
    without = configuration_digest(*arguments, execution_policy=PortfolioExecutionPolicy())
    with_window = configuration_digest(*arguments, execution_policy=_deferral_policy())
    other_window = configuration_digest(*arguments, execution_policy=_deferral_policy(5))
    assert len({without, with_window, other_window}) == 3


def test_policy_rejects_a_malformed_window() -> None:
    for bad in (-1, 1.5, True, "10", None):
        with pytest.raises(ValueError, match="december_deferral_sessions must be a non-negative integer"):
            PortfolioExecutionPolicy(december_deferral_sessions=bad)  # type: ignore[arg-type]


def test_the_hold_reaches_the_decision_metadata_and_the_daily_summary() -> None:
    hold = DecemberDeferralHold(SYMBOL, 0.88, 0.90, 10)
    target = record_december_deferral_holds(_target(0.88), (hold,))
    assert target.metadata[DECEMBER_DEFERRAL_KEY] == (
        {"symbol": SYMBOL, "target_weight": 0.88, "current_weight": 0.90, "sessions": 10},
    )
    message, fields = build_equity_daily_summary(target, _account(), status="submitted", decision_id="d1")
    assert "december deferral held (88.0% -> 90.0%, 10 sessions)" in message
    assert fields["december_deferral"] == "december deferral held (88.0% -> 90.0%, 10 sessions)"


def test_metadata_and_summary_are_unchanged_without_a_deferral() -> None:
    target = _target(0.88)
    plain = build_equity_daily_summary(target, _account(), status="submitted", decision_id="d1")
    assert "deferral" not in plain[0]
    assert plain[1]["december_deferral"] is None
    assert record_december_deferral_holds(target, ()).metadata == target.metadata

    two = record_december_deferral_holds(
        _target(0.88), (DecemberDeferralHold(SYMBOL, 0.88, 0.90, 10), DecemberDeferralHold("SPY", 0.10, 0.20, 10))
    )
    message, _ = build_equity_daily_summary(two, _account(), status="submitted", decision_id="d1")
    assert f"deferral held {SYMBOL}" in message and "deferral held SPY" in message


def test_the_decision_summary_names_a_deferral_and_prefers_the_band() -> None:
    held = record_december_deferral_holds(_target(0.88), (DecemberDeferralHold(SYMBOL, 0.88, 0.90, 10),))
    assert _downsize_hold_reason(held, ()) == "DECEMBER_DEFERRAL_HOLD"
    both = replace(
        held,
        metadata={**held.metadata, "gain_band": ({"symbol": SYMBOL, "g_frac": 0.6, "band": 0.08, "gap": 0.02},)},
    )
    assert _downsize_hold_reason(both, ()) == "GAIN_BAND_HOLD"
    assert _downsize_hold_reason(_target(0.88), ()) is None
    assert _downsize_hold_reason(held, (object(),)) is None


# ---------------------------------------------------------------------------
# PAPER end to end
# ---------------------------------------------------------------------------


def _december_bars(*sessions: datetime) -> list[Bar]:
    return [
        Bar(when, symbol, "1d", PRICE, PRICE, PRICE, PRICE, 1_000_000, Market.EQUITY)
        for when in sessions
        for symbol in ("SPY", SYMBOL)
    ]


def _runtime(
    tmp_path: Path,
    *,
    policy: PortfolioExecutionPolicy,
    target: float = 0.85,
) -> tuple[PortfolioRuntime, PortfolioStateRepository, _RuntimeBroker]:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _order_fill(repository, "buy", QTY, 50.0, BEFORE_WINDOW)
    broker = _RuntimeBroker(qty=QTY, price=PRICE, equity=EQUITY)
    runtime = PortfolioRuntime(
        _ScriptedStrategy((target,)),
        ("SPY", SYMBOL),
        broker,
        repository,
        mode="paper",
        maximum_allocation=1.0,
        allow_static_universe=True,
        account_id=ACCOUNT,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
        risk_limits=_risk_limits(),
        execution_policy=policy,
    )
    # The fixture's seeded fill also records a decision under another digest.
    # The cadence now carries that decision's timestamp across a digest change
    # (R2-8-5), which would make Dec 16 a non-boundary. These tests are about the
    # December verdict, so they start from a runtime with no cadence history.
    runtime.previous_timestamp = None
    return runtime, repository, broker


def test_paper_decision_defers_and_persists_the_hold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PAPER end to end: the December decision submits nothing, the persisted
    decision carries the deferral, the decision summary says so and the paper
    receipt records it per symbol; the January decision sells."""
    summaries: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime_service,
        "_observe",
        SimpleNamespace(on_decision_summary=lambda **fields: summaries.append(fields)),
    )
    runtime, repository, broker = _runtime(tmp_path, policy=_deferral_policy())

    deferred = runtime.process_closed_bars(_december_bars(INSIDE))
    assert deferred.orders == ()
    assert broker.submitted == []
    assert [entry["symbol"] for entry in deferred.target.metadata[DECEMBER_DEFERRAL_KEY]] == [SYMBOL]
    symbols = deferred.target.metadata["paper_observability"]["symbols"]
    assert symbols[SYMBOL]["december_deferral_sessions"] == 10
    assert symbols[SYMBOL]["planned_order_delta"] == 0.0
    persisted = json.loads(repository.decision_row(deferred.decision_id)["target_json"])
    assert persisted["metadata"][DECEMBER_DEFERRAL_KEY] == list(deferred.target.metadata[DECEMBER_DEFERRAL_KEY])
    assert [summary["no_trade_reason"] for summary in summaries] == ["DECEMBER_DEFERRAL_HOLD"]

    sold = runtime.process_closed_bars(_december_bars(JANUARY))
    assert [(order.side, order.quantity) for order in sold.orders] == [(PortfolioOrderSide.SELL, 50.0)]
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert DECEMBER_DEFERRAL_KEY not in sold.target.metadata
    assert summaries[-1]["no_trade_reason"] is None


def test_paper_receipt_is_unchanged_when_the_window_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime_service,
        "_observe",
        SimpleNamespace(on_decision_summary=lambda **fields: None),
    )
    runtime, _, broker = _runtime(tmp_path, policy=_deferral_policy(0))
    result = runtime.process_closed_bars(_december_bars(INSIDE))
    assert [(order.side, order.quantity) for order in result.orders] == [(PortfolioOrderSide.SELL, 50.0)]
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    symbols = result.target.metadata["paper_observability"]["symbols"]
    assert "december_deferral_sessions" not in symbols[SYMBOL]
    assert DECEMBER_DEFERRAL_KEY not in result.target.metadata


def test_paper_risk_reduction_still_executes_inside_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-asset cap lowers the strategy's 0.92 target to 0.84 while the
    account holds 0.90: the 6 pp risk SELL is submitted even in December."""
    monkeypatch.setattr(
        runtime_service,
        "_observe",
        SimpleNamespace(on_decision_summary=lambda **fields: None),
    )
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _order_fill(repository, "buy", QTY, 50.0, BEFORE_WINDOW)
    broker = _RuntimeBroker(qty=QTY, price=PRICE, equity=EQUITY)
    runtime = PortfolioRuntime(
        _ScriptedStrategy((0.92,)),
        ("SPY", SYMBOL),
        broker,
        repository,
        mode="paper",
        maximum_allocation=1.0,
        allow_static_universe=True,
        account_id=ACCOUNT,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
        risk_limits=_risk_limits(maximum_equity_asset=0.84),
        execution_policy=_deferral_policy(band=SHIPPED),
    )
    # The fixture's seeded fill records a decision under another digest; clear the
    # cadence it carries (R2-8-5) so the risk SELL is judged on its own decision.
    runtime.previous_timestamp = None
    result = runtime.process_closed_bars(_december_bars(INSIDE))
    assert [(order.side, order.quantity) for order in result.orders] == [(PortfolioOrderSide.SELL, 60.0)]
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert DECEMBER_DEFERRAL_KEY not in result.target.metadata


def test_the_deferral_is_stateless_across_ledgers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The deferral reads no state beyond the decision itself: a fresh ledger
    deciding the same session reaches the same verdict (restart replay)."""
    monkeypatch.setattr(
        runtime_service,
        "_observe",
        SimpleNamespace(on_decision_summary=lambda **fields: None),
    )
    runtime, _, _ = _runtime(tmp_path / "first", policy=_deferral_policy())
    first = runtime.process_closed_bars(_december_bars(INSIDE))
    assert first.orders == ()

    replayed, _, _ = _runtime(tmp_path / "second", policy=_deferral_policy())
    again = replayed.process_closed_bars(_december_bars(INSIDE))
    assert again.orders == ()
    assert again.target.metadata[DECEMBER_DEFERRAL_KEY] == first.target.metadata[DECEMBER_DEFERRAL_KEY]


def test_a_single_deferred_session_is_a_timeless_fixture() -> None:
    """Guard against a fixture drifting out of December: the two decision
    sessions this module relies on are real adjacent NYSE sessions."""
    previous = INSIDE - timedelta(days=1)
    assert in_december_deferral_window(INSIDE, 10) and not in_december_deferral_window(previous, 10)
    assert session_bounds(INSIDE.date()) is not None and session_bounds(previous.date()) is not None
