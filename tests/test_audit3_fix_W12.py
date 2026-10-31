"""Audit wave 3, package W12: December deferral and gain-band exits (T3-4-*), the
calendar-fallback wiring (Q3-2-6), the carried cadence watermark across December
(Q3-1-6) and the wording of the calendar-fallback alert.

Every fixture is synthetic and deterministic: fixed timestamps, in-memory planner
inputs or ``tmp_path`` runtime state, no network and no production path.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.config import DownsizeGainBand
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution import portfolio
from parayabuck.execution.portfolio import (
    DECEMBER_CALENDAR_FALLBACK_KEY,
    DECEMBER_DEFERRAL_KEY,
    AssetTradingRules,
    PortfolioExecutionPolicy,
    PortfolioOrderSide,
    TargetToOrderPlanner,
)
from parayabuck.markets.nyse_calendar import NEW_YORK
from parayabuck.monitoring import alerts
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import PortfolioRuntime
from tests.test_december_deferral_h2 import (
    BEFORE_WINDOW,
    EQUITY,
    INSIDE,
    PRICE,
    SYMBOL,
    _december_bars,
    _deferral_policy,
    _runtime,
)

# Mid-year session: outside the December window, so only the gain band can apply.
OUTSIDE_WINDOW = datetime(2026, 6, 15, tzinfo=NEW_YORK)
# A November watermark: the first December session is the period boundary.
NOVEMBER_DECISION = datetime(2025, 11, 26, tzinfo=NEW_YORK)
RULES = {SYMBOL: AssetTradingRules(0.001, 0.001, 1.0)}


def _plan(
    target_weight: float,
    *,
    timestamp: datetime,
    policy: PortfolioExecutionPolicy,
    managed: float,
    external: float = 0.0,
    lots: tuple[tuple[float, float], ...] = (),
    gain_holds: list | None = None,
    fallbacks: list[int] | None = None,
    deferral_holds: list | None = None,
):
    """Plan one target against one position of ``managed`` bot units plus ``external`` manual units."""
    total = managed + external
    position = PositionSnapshot(
        SYMBOL,
        total,
        PRICE,
        PRICE,
        Market.EQUITY,
        qty_available=total,
        ownership="managed",
        managed_qty=managed,
    )
    account = AccountSnapshot(
        cash=0.0,
        equity=EQUITY,
        peak_equity=EQUITY,
        daily_pnl=0.0,
        positions=(position,),
    )
    weights = (TargetWeight(SYMBOL, target_weight),) if target_weight > 0 else ()
    target = TargetPortfolio("vt35", "1.2.0", timestamp, weights, 1.0 - target_weight, universe_version="test")
    return TargetToOrderPlanner(policy).plan(
        target,
        account,
        market=Market.EQUITY,
        prices={SYMBOL: PRICE},
        rules=RULES,
        approved_symbols=(SYMBOL,),
        decision_id="w12-decision",
        open_lots={SYMBOL: lots} if lots else {},
        gain_band_holds=gain_holds,
        december_deferral_holds=deferral_holds,
        december_calendar_fallbacks=fallbacks,
    )


def _unreadable_calendar(year: int) -> tuple:
    raise RuntimeError(f"NYSE calendar unavailable for {year}")


def test_a_mixed_full_exit_is_not_deferred_in_december() -> None:
    """T3-4-1: the bot sells its 50 managed units and keeps 1000 external units.

    The target weight stays positive (the external floor), so the trim test alone
    would defer it. The full exit of the managed units is never deferred.
    """
    deferrals: list = []
    orders = _plan(
        1.0,
        timestamp=INSIDE,
        policy=_deferral_policy(),
        managed=50.0,
        external=1000.0,
        deferral_holds=deferrals,
    )
    assert [(order.side, order.quantity) for order in orders] == [(PortfolioOrderSide.SELL, 50.0)]
    assert deferrals == []


def test_a_mixed_full_exit_is_not_held_by_the_gain_band() -> None:
    """T3-4-2: outside the window the gain band must not hold a mixed full exit.

    The 50 managed units gain 40 per unit (gain fraction 0.4, band 8pp) and sit
    5pp above the 1.0 target, which the band would hold. The exit still executes.
    """
    holds: list = []
    orders = _plan(
        1.0,
        timestamp=OUTSIDE_WINDOW,
        policy=PortfolioExecutionPolicy(downsize_gain_band=DownsizeGainBand()),
        managed=50.0,
        external=1000.0,
        lots=((50.0, 60.0),),
        gain_holds=holds,
    )
    assert [(order.side, order.quantity) for order in orders] == [(PortfolioOrderSide.SELL, 50.0)]
    assert holds == []


@pytest.mark.parametrize(
    ("target_weight", "managed", "external", "expected"),
    [
        # Drift inside the band: the calendar is never read, so nothing is recorded.
        (0.999, 1000.0, 0.0, []),
        # A strategy trim the band holds: the unreadable calendar is recorded.
        (0.9, 1000.0, 0.0, [2025]),
        # A mixed full exit is not a deferral candidate, so it records nothing.
        (1.0, 50.0, 1000.0, []),
    ],
)
def test_calendar_fallback_is_recorded_only_for_a_trim_the_band_would_hold(
    monkeypatch: pytest.MonkeyPatch,
    target_weight: float,
    managed: float,
    external: float,
    expected: list[int],
) -> None:
    """T3-4-3 and the planner side of Q3-2-6: the fallback list receives a year only when the December check matters."""
    monkeypatch.setattr(portfolio, "_cached_december_season_sessions", _unreadable_calendar)
    fallbacks: list[int] = []
    _plan(
        target_weight,
        timestamp=INSIDE,
        policy=_deferral_policy(),
        managed=managed,
        external=external,
        fallbacks=fallbacks,
    )
    assert fallbacks == expected


def test_runtime_records_the_calendar_fallback_and_executes_the_trim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q3-2-6, runtime side: the fallback reaches the decision metadata, one alert is raised, and the trim executes.

    With the calendar unreadable the deferral cannot apply, so the ordinary rule
    executes the December trim instead of holding it.
    """
    monkeypatch.setattr(portfolio, "_cached_december_season_sessions", _unreadable_calendar)
    monkeypatch.setattr(
        runtime_service,
        "_observe",
        SimpleNamespace(on_decision_summary=lambda **fields: None),
    )
    raised: list[tuple[str, list[int]]] = []
    monkeypatch.setattr(
        PortfolioRuntime,
        "_alert_december_calendar_fallback",
        lambda self, decision_id, years: raised.append((decision_id, list(years))),
    )
    runtime, _, broker = _runtime(tmp_path, policy=_deferral_policy())
    result = runtime.process_closed_bars(_december_bars(INSIDE))
    assert result.target is not None
    assert result.target.metadata[DECEMBER_CALENDAR_FALLBACK_KEY] == (2025,)
    assert DECEMBER_DEFERRAL_KEY not in result.target.metadata
    assert [order.side for order in result.orders] == [PortfolioOrderSide.SELL]
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert len(raised) == 1
    assert raised[0][1] == [2025]


def test_calendar_fallback_alert_states_the_ordinary_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T3-4-3 wording: the alert says the deferral was skipped, not that a weekday count was used."""
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(alerts, "emit", lambda kind, **fields: emitted.append({"kind": kind, **fields}))
    runtime, _, _ = _runtime(tmp_path, policy=_deferral_policy())
    runtime._alert_december_calendar_fallback("w12-decision", [2025, 2025])
    assert len(emitted) == 1
    assert emitted[0]["kind"] == "paper_incident"
    message = str(emitted[0]["message"])
    assert "deferral skipped" in message
    assert "ordinary rule applied" in message
    assert "weekday" not in message


def test_carried_november_watermark_defers_the_first_december_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q3-1-6: a carried November watermark makes the first December session a period boundary, so the window applies."""
    monkeypatch.setattr(
        runtime_service,
        "_observe",
        SimpleNamespace(on_decision_summary=lambda **fields: None),
    )
    runtime, _, broker = _runtime(tmp_path, policy=_deferral_policy())
    runtime.previous_timestamp = NOVEMBER_DECISION
    result = runtime.process_closed_bars(_december_bars(INSIDE))
    assert result.orders == ()
    assert broker.submitted == []
    assert result.target is not None
    assert [entry["symbol"] for entry in result.target.metadata[DECEMBER_DEFERRAL_KEY]] == [SYMBOL]


def test_carried_december_watermark_leaves_the_december_session_unscheduled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q3-1-6: a carried watermark from the same December month makes the session no cadence boundary.

    The session is not decided, so the window is not evaluated there and no
    deferral is recorded. The first decision of the period is the one that holds.
    """
    monkeypatch.setattr(
        runtime_service,
        "_observe",
        SimpleNamespace(on_decision_summary=lambda **fields: None),
    )
    runtime, _, broker = _runtime(tmp_path, policy=_deferral_policy())
    runtime.previous_timestamp = BEFORE_WINDOW
    result = runtime.process_closed_bars(_december_bars(INSIDE))
    assert result.status == "not_scheduled"
    assert result.orders == ()
    assert result.target is None
    assert broker.submitted == []
