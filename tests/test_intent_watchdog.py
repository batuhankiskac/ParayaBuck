"""Intent-vs-reality watchdog (deterministic, offline, pinned clocks)."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.monitoring.alerts as alerts
from parayabuck.config import Config, IntentWatchdogConfig
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.monitoring.intent_watchdog import (
    IntentWatchdogSettings,
    LatestDecision,
    evaluate_equity,
    last_due_equity_session,
    over_target_tolerance,
)
from parayabuck.portfolio.models import RebalancePolicy
from parayabuck.runtime import EngineRuntime

pytest.importorskip("pandas_market_calendars")

_SETTINGS = IntentWatchdogSettings()


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _decision(session_label_utc: datetime, weights: dict[str, float] | None = None) -> LatestDecision:
    return LatestDecision(session_label_utc, dict(weights or {}))


def _account(positions: tuple[PositionSnapshot, ...] = (), equity: float = 100_000.0) -> AccountSnapshot:
    return AccountSnapshot(cash=equity, equity=equity, peak_equity=equity, daily_pnl=0.0, positions=positions)


def _qld(value: float) -> PositionSnapshot:
    return PositionSnapshot("QLD", value / 100.0, 100.0, 100.0, Market.EQUITY, managed_qty=value / 100.0)


def _codes(alarms: list[Any]) -> list[str]:
    return sorted(alarm.code for alarm in alarms)


# --------------------------------------------------------------------------- #
# (a) equity decision missing
# --------------------------------------------------------------------------- #


def test_four_silent_sessions_raise_equity_decision_missing() -> None:
    """The 2026-09-25..29 incident: last decision Thursday 09-24, now Tuesday
    09-29 after the close + grace."""
    alarms = evaluate_equity(
        _utc(2026, 9, 29, 21, 0),
        _decision(_utc(2026, 9, 24, 13, 30)),
        _account(),
        running_since=_utc(2026, 9, 20),
        settings=_SETTINGS,
    )
    assert _codes(alarms) == ["equity_decision_missing"]
    assert "2026-09-29" in alarms[0].message and "2026-09-24" in alarms[0].message


@pytest.mark.parametrize(
    ("now", "latest_label"),
    [
        # 16:30 ET Tuesday: inside the 45 min grace -> Monday is the due session.
        (_utc(2026, 9, 29, 20, 30), _utc(2026, 9, 28, 13, 30)),
        # After the grace, Tuesday's decision exists (provider 00:00 ET label).
        (_utc(2026, 9, 29, 21, 0), _utc(2026, 9, 29, 4, 0)),
        # Saturday: Friday's session is the last one due.
        (_utc(2026, 10, 3, 15, 0), _utc(2026, 10, 2, 13, 30)),
        # Thanksgiving 2026-11-26 is a holiday: Wednesday stays the due session.
        (_utc(2026, 11, 26, 23, 0), _utc(2026, 11, 25, 14, 30)),
        # Early close (13:00 ET) on 11-27: the decision comes at 13:00 ET, so
        # a 13:01 ET decision for that session is already on record.
        (_utc(2026, 11, 27, 19, 0), _utc(2026, 11, 27, 18, 1)),
    ],
)
def test_no_missing_alarm_when_the_due_session_is_decided(now: datetime, latest_label: datetime) -> None:
    alarms = evaluate_equity(now, _decision(latest_label), _account(), running_since=None, settings=_SETTINGS)
    assert alarms == []


def test_early_close_session_is_due_after_its_own_close_plus_grace() -> None:
    """2026-11-27 closes at 13:00 ET (18:00 UTC): the decision is expected
    there, not at the 16:00 ET floor a weekday-window calendar implies."""
    session, deadline = last_due_equity_session(_utc(2026, 11, 27, 18, 50), timedelta(minutes=45))  # type: ignore[misc]
    assert session.isoformat() == "2026-11-27"
    assert deadline == _utc(2026, 11, 27, 18, 45)
    # Still inside the grace at 18:40 UTC, so the previous session is the
    # newest due one (Thanksgiving 2026-11-26 is not a session at all).
    assert last_due_equity_session(_utc(2026, 11, 27, 18, 40), timedelta(minutes=45)) == (  # type: ignore[misc]
        date(2026, 11, 25),
        _utc(2026, 11, 25, 21, 45),
    )


def test_no_history_alarms_only_when_the_runtime_was_up_for_the_decision() -> None:
    now = _utc(2026, 9, 29, 21, 0)
    up_before = evaluate_equity(now, None, _account(), running_since=_utc(2026, 9, 29, 13, 0), settings=_SETTINGS)
    fresh_deploy = evaluate_equity(now, None, _account(), running_since=_utc(2026, 9, 29, 20, 55), settings=_SETTINGS)
    assert _codes(up_before) == ["equity_decision_missing"]
    assert fresh_deploy == []


# --------------------------------------------------------------------------- #
# (b) equity target weight not reflected after the fill window
# --------------------------------------------------------------------------- #


def test_equity_under_target_only_after_next_open_plus_grace() -> None:
    latest = _decision(_utc(2026, 9, 28, 13, 30), {"QLD": 0.8})
    flat = _account()
    # Tuesday 09:59 ET: still inside next open (09:30) + 30 min.
    assert evaluate_equity(_utc(2026, 9, 29, 13, 59), latest, flat, running_since=None, settings=_SETTINGS) == []
    late = evaluate_equity(_utc(2026, 9, 29, 14, 5), latest, flat, running_since=None, settings=_SETTINGS)
    assert _codes(late) == ["equity_position_under_target"]
    assert "QLD held 0.0% vs target 80.0%" in late[0].message
    # Holding most of the target is not an alarm; well under half is.
    held = _account((_qld(75_000.0),))
    assert evaluate_equity(_utc(2026, 9, 29, 14, 5), latest, held, running_since=None, settings=_SETTINGS) == []
    small = _account((_qld(30_000.0),))
    assert _codes(evaluate_equity(_utc(2026, 9, 29, 14, 5), latest, small, running_since=None, settings=_SETTINGS)) == [
        "equity_position_under_target"
    ]


def test_equity_cash_target_or_unknown_account_never_under_target() -> None:
    now = _utc(2026, 9, 29, 15, 0)
    cash = _decision(_utc(2026, 9, 28, 13, 30), {})
    assert evaluate_equity(now, cash, _account(), running_since=None, settings=_SETTINGS) == []
    invested = _decision(_utc(2026, 9, 28, 13, 30), {"QLD": 0.8})
    assert evaluate_equity(now, invested, None, running_since=None, settings=_SETTINGS) == []


# --------------------------------------------------------------------------- #
# (c) equity target exceeded — a cash decision whose exit never filled
# --------------------------------------------------------------------------- #


def test_failed_exit_raises_over_target_after_the_fill_window() -> None:
    """A DAY SELL that expired leaves the whole position; nothing under-target
    catches it because the decision targeted cash (weight 0)."""
    cash = _decision(_utc(2026, 9, 28, 13, 30), {})
    still_holding = _account((_qld(98_000.0),))
    # Inside the fill window the exit has not expired yet.
    assert evaluate_equity(_utc(2026, 9, 29, 13, 59), cash, still_holding, running_since=None, settings=_SETTINGS) == []
    alarms = evaluate_equity(_utc(2026, 9, 29, 14, 5), cash, still_holding, running_since=None, settings=_SETTINGS)
    assert _codes(alarms) == ["equity_position_over_target"]
    assert "QLD held 98.0% vs target 0.0%" in alarms[0].message


def test_over_target_ignores_exit_dust_and_the_untouched_target() -> None:
    cash = _decision(_utc(2026, 9, 28, 13, 30), {})
    late = _utc(2026, 9, 29, 14, 5)
    # A partially filled exit leaves rounding dust, not a position.
    assert evaluate_equity(late, cash, _account((_qld(500.0),)), running_since=None, settings=_SETTINGS) == []
    # A target that was met is not "over".
    invested = _decision(_utc(2026, 9, 28, 13, 30), {"QLD": 0.8})
    assert evaluate_equity(late, invested, _account((_qld(80_000.0),)), running_since=None, settings=_SETTINGS) == []


def test_over_target_tolerates_price_drift_of_a_partial_target() -> None:
    """A 0.90 target that drifted to 0.93 on an up day is not a failed order;
    the old flat 1% tolerance failed readiness for it."""
    late = _utc(2026, 9, 29, 14, 5)
    partial = _decision(_utc(2026, 9, 28, 13, 30), {"QLD": 0.9})
    assert evaluate_equity(late, partial, _account((_qld(93_000.0),)), running_since=None, settings=_SETTINGS) == []
    # A position far beyond any drift (double fill, or a missed reduction) still alarms
    half = _decision(_utc(2026, 9, 28, 13, 30), {"QLD": 0.5})
    alarms = evaluate_equity(late, half, _account((_qld(60_000.0),)), running_since=None, settings=_SETTINGS)
    assert _codes(alarms) == ["equity_position_over_target"]


def test_over_target_never_fires_without_an_account() -> None:
    cash = _decision(_utc(2026, 9, 28, 13, 30), {})
    assert evaluate_equity(_utc(2026, 9, 29, 14, 5), cash, None, running_since=None, settings=_SETTINGS) == []


# --------------------------------------------------------------------------- #
# (d) equity target exceeded by a recorded gain-band hold (A1u)
# --------------------------------------------------------------------------- #


def _band_hold(symbol: str = "QLD", band: float = 0.08) -> tuple[dict[str, object], ...]:
    """One ``metadata["gain_band"]`` entry, as the planner writes it."""
    return ({"symbol": symbol, "g_frac": 0.07, "band": band, "gap": 0.07},)


def test_over_target_tolerance_widens_by_the_recorded_band() -> None:
    """The band is room the decision deliberately left; it is added to the
    tolerance on top of the ordinary relative drift slack of the target."""
    assert over_target_tolerance(0.40) == pytest.approx(0.04)
    assert over_target_tolerance(0.40, 0.08) == pytest.approx(0.12)
    assert over_target_tolerance(0.0, 0.08) == pytest.approx(0.08)
    # Never narrower than the constant tolerance.
    assert over_target_tolerance(0.90, 0.005) == pytest.approx(0.095)


def test_over_target_tolerates_a_recorded_gain_band_hold() -> None:
    """Target 0.40, held 0.47: the latest decision recorded a held downsize for
    QLD, so the gap is the recorded intent, not a failed order. Without the
    record the same account is the everyday unfilled trim, and a position
    past the recorded band is beyond what the hold justified."""
    late = _utc(2026, 9, 29, 14, 5)
    held = _account((_qld(47_000.0),))
    recorded = LatestDecision(_utc(2026, 9, 28, 13, 30), {"QLD": 0.40}, None, _band_hold())
    assert evaluate_equity(late, recorded, held, running_since=None, settings=_SETTINGS) == []
    blind = _decision(_utc(2026, 9, 28, 13, 30), {"QLD": 0.40})
    assert _codes(evaluate_equity(late, blind, held, running_since=None, settings=_SETTINGS)) == [
        "equity_position_over_target"
    ]
    beyond = _account((_qld(56_000.0),))
    assert _codes(evaluate_equity(late, recorded, beyond, running_since=None, settings=_SETTINGS)) == [
        "equity_position_over_target"
    ]


def test_over_target_ignores_a_band_record_malformed_or_for_another_symbol() -> None:
    """Only a well-formed record for the same symbol widens the tolerance: a
    foreign symbol, a missing band and a non-numeric band change nothing."""
    late = _utc(2026, 9, 29, 14, 5)
    held = _account((_qld(47_000.0),))
    for entries in (
        ({"symbol": "QQQ", "band": 0.08},),
        ({"symbol": "QLD"},),
        ({"symbol": "QLD", "band": "wide"},),
        ({"band": 0.08},),
    ):
        decision = LatestDecision(_utc(2026, 9, 28, 13, 30), {"QLD": 0.40}, None, entries)  # type: ignore[arg-type]
        assert _codes(evaluate_equity(late, decision, held, running_since=None, settings=_SETTINGS)) == [
            "equity_position_over_target"
        ]


def test_over_target_still_alarms_for_an_unfilled_exit_with_a_band_record() -> None:
    """The band can never hold an exit, so a cash decision whose DAY SELL did
    not fill alarms even when the metadata carries an entry for that symbol."""
    cash = LatestDecision(_utc(2026, 9, 28, 13, 30), {}, None, _band_hold())
    alarms = evaluate_equity(
        _utc(2026, 9, 29, 14, 5), cash, _account((_qld(98_000.0),)), running_since=None, settings=_SETTINGS
    )
    assert _codes(alarms) == ["equity_position_over_target"]
    assert "QLD held 98.0% vs target 0.0%" in alarms[0].message


@pytest.mark.parametrize(
    ("early_close_session", "next_session"),
    [
        # Friday after Thanksgiving: 13:00 ET close, then the weekend.
        (date(2026, 11, 27), date(2026, 11, 30)),
        # Christmas Eve: 13:00 ET close, the 25th is a holiday, then Monday.
        (date(2026, 12, 24), date(2026, 12, 28)),
    ],
)
def test_over_target_waits_for_the_next_session_open_after_an_early_close(
    early_close_session: date,
    next_session: date,
) -> None:
    """The fill window opens at the next NYSE session open, not at the early
    close that produced the decision.

    An exit decided at a 13:00 ET early close cannot be expected to have
    filled minutes later, nor over the weekend/holiday that follows: the
    window is next open + ``equity_fill_grace_minutes`` (30) and the next open
    is a real session. A window anchored on the decision's own close would
    alarm at 13:10 ET with the DAY SELL still legitimately working.
    """
    cash = _decision(_utc(early_close_session.year, early_close_session.month, early_close_session.day, 18, 0))
    still_holding = _account((_qld(98_000.0),))
    # 18:10 UTC = 13:10 ET, ten minutes after the early close: the fill
    # window has not even opened yet.
    assert (
        evaluate_equity(
            _utc(early_close_session.year, early_close_session.month, early_close_session.day, 18, 10),
            cash,
            still_holding,
            running_since=None,
            settings=_SETTINGS,
        )
        == []
    )
    # 14:59 UTC = 09:59 ET on the next session: the grace ends at the 09:30 ET
    # open + 30 min, so this is one minute inside the fill window.
    assert (
        evaluate_equity(
            _utc(next_session.year, next_session.month, next_session.day, 14, 59),
            cash,
            still_holding,
            running_since=None,
            settings=_SETTINGS,
        )
        == []
    )
    alarms = evaluate_equity(
        _utc(next_session.year, next_session.month, next_session.day, 15, 5),
        cash,
        still_holding,
        running_since=None,
        settings=_SETTINGS,
    )
    assert _codes(alarms) == ["equity_position_over_target"]
    assert "QLD held 98.0% vs target 0.0%" in alarms[0].message
    # The window is anchored to the next session's open, not to the early
    # close the decision was taken at.
    assert alarms[0].due_at == f"{next_session.isoformat()}T15:00:00+00:00"


# --------------------------------------------------------------------------- #
# Runtime wiring: edge-triggered alerts + health surface, never trading gates
# --------------------------------------------------------------------------- #


class _Repo:
    def __init__(
        self,
        latest: tuple[datetime, dict[str, float]] | None,
        gain_band: tuple[dict[str, object], ...] | None = None,
    ) -> None:
        self.latest = latest
        self.gain_band = gain_band

    def latest_decision_target(self, account_id: str, market: Market) -> tuple[datetime, dict[str, float]] | None:
        return self.latest

    def latest_decision_gain_band(self, account_id: str, market: Market) -> tuple[dict[str, object], ...] | None:
        return self.gain_band


def _engine(repo: _Repo, *, enabled: bool = True) -> EngineRuntime:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.app_config = SimpleNamespace(intent_watchdog=IntentWatchdogConfig(enabled=enabled))
    engine._startup_committed = True
    engine._intent_watchdog_running_since = _utc(2026, 9, 20)
    engine._account_snapshot_lock = threading.Lock()
    engine._last_account_snapshot = _account()
    engine._last_account_snapshot_at = _utc(2026, 9, 29, 20, 59)
    engine._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            account_id="acct",
            repository=repo,
            policy=RebalancePolicy(Market.EQUITY, "hours:24"),
            strategy=SimpleNamespace(timeframe="1d"),
        ),
    )
    engine._state = "running"
    return engine


@pytest.fixture()
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        SimpleNamespace(emit=lambda kind, **fields: events.append((kind, fields))),
    )
    return events


def test_runtime_pass_is_edge_triggered_and_recovers(emitted) -> None:
    repo = _Repo((_utc(2026, 9, 24, 13, 30), {}))
    engine = _engine(repo)
    now = _utc(2026, 9, 29, 21, 0)

    engine._intent_watchdog_pass(now)
    engine._intent_watchdog_pass(now + timedelta(minutes=1))
    assert [kind for kind, _ in emitted] == ["intent_alarm"]
    assert emitted[0][1]["category"] == "equity_decision_missing"
    status = engine.intent_watchdog_status()
    assert status["degraded_reasons"] == ["equity_decision_missing"]
    # Trading state is untouched: the watchdog only observes.
    assert engine._state == "running"

    repo.latest = (_utc(2026, 9, 29, 13, 30), {})
    engine._intent_watchdog_pass(now + timedelta(minutes=2))
    assert [kind for kind, _ in emitted] == ["intent_alarm", "intent_recovered"]
    assert engine.intent_watchdog_status()["degraded_reasons"] == []


def test_runtime_pass_reads_the_recorded_gain_band_hold(emitted) -> None:
    """The persisted hold reaches the check through the repository reader: an
    account 7 pp above a target the band deliberately held raises no alarm,
    while the same account without the record is the everyday failed trim."""
    latest = (_utc(2026, 9, 28, 13, 30), {"QLD": 0.40})
    held = _account((_qld(47_000.0),))
    recorded = _engine(_Repo(latest, _band_hold()))
    recorded._last_account_snapshot = held
    assert recorded._intent_watchdog_pass(_utc(2026, 9, 29, 14, 5)) == []
    assert recorded.intent_watchdog_status()["degraded_reasons"] == []

    blind = _engine(_Repo(latest))
    blind._last_account_snapshot = held
    alarms = blind._intent_watchdog_pass(_utc(2026, 9, 29, 14, 5))
    assert [alarm["code"] for alarm in alarms] == ["equity_position_over_target"]
    # Only the blind engine alarmed, and it did so through the operator path.
    assert [kind for kind, _ in emitted] == ["intent_alarm"]
    assert emitted[0][1]["category"] == "equity_position_over_target"


def test_runtime_pass_is_inert_when_disabled_or_before_startup(emitted) -> None:
    repo = _Repo((_utc(2026, 9, 24, 13, 30), {}))
    disabled = _engine(repo, enabled=False)
    assert disabled._intent_watchdog_pass(_utc(2026, 9, 29, 21, 0)) == []
    starting = _engine(repo)
    starting._startup_committed = False
    assert starting._intent_watchdog_pass(_utc(2026, 9, 29, 21, 0)) == []
    assert emitted == []
    assert starting.intent_watchdog_status()["degraded_reasons"] == []


def test_watchdog_alert_kinds_are_known_and_alarm_is_a_warning() -> None:
    from parayabuck.monitoring.events import ALERT_KINDS, WARNING_KINDS

    assert {"intent_alarm", "intent_recovered"} <= ALERT_KINDS
    assert "intent_alarm" in WARNING_KINDS
    alerts.reset_for_tests()


# --------------------------------------------------------------------------- #
# Readiness: degraded reason, not a crash
# --------------------------------------------------------------------------- #


def _manager(reasons: list[str]) -> Any:
    health = SimpleNamespace(state="running", healthy=True, intent_watchdog={"degraded_reasons": reasons, "alarms": []})

    class _Manager:
        config_path = None
        runtime = SimpleNamespace(health=lambda: health)

        def current_engine_state(self) -> str:
            return "running"

        def has_active_runtime(self) -> bool:
            return True

    return _Manager()


@pytest.mark.parametrize(
    ("reasons", "fail_readiness", "status", "ready"),
    [
        ([], True, 200, True),
        (["equity_decision_missing"], True, 503, False),
        (["equity_decision_missing"], False, 200, True),
    ],
)
def test_readyz_reports_intent_alarms_as_degraded_reasons(
    monkeypatch, reasons: list[str], fail_readiness: bool, status: int, ready: bool
) -> None:
    from parayabuck.web.routers import health as health_router
    from tests.helpers import web_client

    config = Config(intent_watchdog=IntentWatchdogConfig(fail_readiness=fail_readiness))
    monkeypatch.setattr(health_router, "load_config", lambda _path: config)
    response = web_client(_manager(reasons)).get("/readyz")
    payload = response.json()
    assert response.status_code == status
    assert payload["ready"] is ready
    assert payload["trading_ready"] is True
    assert payload["degraded_reasons"] == reasons


def test_config_rejects_invalid_watchdog_thresholds() -> None:
    with pytest.raises(ValueError):
        IntentWatchdogConfig(equity_decision_grace_minutes=-1)
    with pytest.raises(ValueError):
        IntentWatchdogConfig(minimum_held_fraction=0)


def test_repository_latest_decision_target_reads_persisted_weights(tmp_path, monkeypatch) -> None:
    from parayabuck.core.models import Bar
    from parayabuck.execution.portfolio import AssetTradingRules
    from parayabuck.portfolio import runtime_service
    from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
    from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository

    class _Broker:
        def account(self) -> AccountSnapshot:
            return _account()

        def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
            return AssetTradingRules(0.01, 0.01, 1.0)

        def all_open_order_symbols(self) -> set[str]:
            return set()

        def submit_portfolio_order(self, order: Any) -> str:
            return "broker-" + order.client_order_id

    class _Universe:
        def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
            return True

    monkeypatch.setattr(runtime_service, "_utcnow", lambda: _utc(2026, 9, 29, 21, 0))
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    assert repository.latest_decision_target("acct", Market.EQUITY) is None
    runtime = PortfolioRuntime(
        EquityLeveragedTrendV1({"signal_symbol": "QQQ", "hold_symbol": "QLD", "sma_bars": 5}),
        ("QQQ", "QLD"),
        _Broker(),
        repository,
        mode="paper",
        maximum_allocation=0.5,
        account_id="acct",
        universe=_Universe(),
        universe_version="test-universe-v1",
    )
    days = [_utc(2026, 9, 14, 4, 0) + timedelta(days=offset) for offset in range(16)]
    days = [day for day in days if day.weekday() < 5]
    runtime.history = {
        symbol: [
            Bar(day, symbol, "1d", 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1_000, Market.EQUITY)
            for i, day in enumerate(days)
        ]
        for symbol in ("QQQ", "QLD")
    }
    result = runtime.startup_catch_up()
    assert result.status == "submitted"
    latest = repository.latest_decision_target("acct", Market.EQUITY)
    assert latest is not None
    assert latest[0] == days[-1]
    assert latest[1] == {w.symbol: pytest.approx(w.weight) for w in result.target.weights}
    assert latest[1]["QLD"] > 0
    assert repository.latest_decision_target("acct", Market.CRYPTO) is None
