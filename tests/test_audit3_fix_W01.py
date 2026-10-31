"""Audit wave 3 (W01): session-close basis embargo, SIP fallback budget, readiness session alignment.

Covers T3-5-2 / P3-5-1 (the equity basis verifier ran on the partial decision-day
provider bar at close+30 s and latched the book every session), T3-5-1 (the same
gate must hold with the cross-check off), T3-2-2 (the SIP session-close fallback
gave up after four one-minute attempts) and T3-5-3 (readiness compared raw bar
labels, so adopted provider bars never matched the stream's 09:30 ET label).

All clocks are fixed: the runtime's ``datetime.now`` is pinned to a fixed session
(Tue 2026-09-29, close 20:00 UTC). No network, no credentials, no data/ paths.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import EngineConfig, SignalCrossCheckConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.runtime import EngineRuntime, _AccountSnapshotLease, _SessionCloseFallback

# 2026-09-29 is a Tuesday; the stream's session-close daily bar is labelled 09:30 ET.
SESSION_LABEL = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
SESSION_CLOSE = datetime(2026, 9, 29, 16, 0, tzinfo=NEW_YORK).astimezone(UTC)
SYMBOLS = ("SPY", "QQQ", "QLD")


class _Clock:
    """Pins ``parayabuck.runtime.datetime.now`` (same pattern as test_runtime_decision_retry)."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def freeze(self, moment: datetime) -> None:
        self.now = moment

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = self

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return clock.now.astimezone(tz) if tz is not None else clock.now

        monkeypatch.setattr(runtime_module, "datetime", _Frozen)


@pytest.fixture(autouse=True)
def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **fields: events.append((kind, fields)))
    return events


def _bars(close: float = 100.0) -> list[Bar]:
    return [
        Bar(SESSION_LABEL, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY) for symbol in SYMBOLS
    ]


def _result() -> SimpleNamespace:
    return SimpleNamespace(status="submitted", decision_id="dec-1", target=None, orders=(), submitted_order_ids=())


def _engine(process: Any) -> EngineRuntime:
    runtime = SimpleNamespace(
        market=Market.EQUITY,
        symbols=SYMBOLS,
        strategy=SimpleNamespace(timeframe="1d", name="vt35"),
        process_closed_bars=process,
    )
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.config = EngineConfig()
    # Cross-check OFF: the basis gate must hold on its own (T3-5-1).
    engine.app_config = SimpleNamespace(signal_cross_check=SignalCrossCheckConfig(enabled=False))
    engine.broker = SimpleNamespace(account_id="acct")
    engine._warming_up = False
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._state = "running"
    engine._state_lock = threading.RLock()
    engine._portfolio_lock = threading.RLock()
    engine._entry_risk_latch = {Market.EQUITY: None}
    engine._market_entry_halts = {Market.EQUITY: None}
    engine._portfolio_runtimes = (runtime,)
    engine._workers = {}
    engine._last_account_at = SESSION_CLOSE
    engine._signal_cross_check_step = lambda *_args, **_kwargs: False
    engine._deferred_portfolio_batches = {}
    engine._entry_readiness_reasons = lambda *_args, **_kwargs: ()
    # generation=None: the "no cache metadata" lease, valid for every check.
    engine._fresh_account_snapshot_lease = lambda **_kwargs: _AccountSnapshotLease(
        AccountSnapshot(100_000, 100_000, 100_000, 0.0), None
    )
    engine._last_portfolio_status = {}
    return engine


def test_equity_session_close_decision_waits_for_the_sip_embargo(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    """At close+30 s the decision-day provider bar is partial: no basis check, no alert."""
    decided: list[list[Bar]] = []
    engine = _engine(lambda bars, **_kwargs: (decided.append(list(bars)), _result())[1])
    basis_calls: list[datetime | None] = []
    engine._equity_signal_basis_latch = lambda ts=None: (basis_calls.append(ts), None)[1]  # type: ignore[method-assign]
    clock = _Clock(SESSION_CLOSE + timedelta(seconds=30))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False

    key = (Market.EQUITY, SESSION_LABEL)
    _parked, retry_at = engine._deferred_portfolio_batches[key]
    assert retry_at == SESSION_CLOSE + timedelta(minutes=15)
    assert basis_calls == []
    assert decided == []
    assert _capture_alerts == []

    # Once the SIP embargo has passed, the due-runner decides the batch and the
    # basis verifier runs for the decision bar exactly once.
    clock.freeze(SESSION_CLOSE + timedelta(minutes=16))
    engine._run_due_deferred_portfolio_batches(now=clock.now)
    assert basis_calls == [SESSION_LABEL]
    assert len(decided) == 1
    assert engine._deferred_portfolio_batches == {}
    assert _capture_alerts == []


def test_final_session_decision_is_not_parked(monkeypatch: pytest.MonkeyPatch) -> None:
    """After close+embargo the decision runs on the first pass: nothing is parked."""
    decided: list[list[Bar]] = []
    engine = _engine(lambda bars, **_kwargs: (decided.append(list(bars)), _result())[1])
    basis_calls: list[datetime | None] = []
    engine._equity_signal_basis_latch = lambda ts=None: (basis_calls.append(ts), None)[1]  # type: ignore[method-assign]
    _Clock(SESSION_CLOSE + timedelta(minutes=16)).install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is True
    assert basis_calls == [SESSION_LABEL]
    assert len(decided) == 1
    assert engine._deferred_portfolio_batches == {}


def test_session_close_fallback_stays_armed_until_the_decision_window_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short provider outage must not exhaust the SIP fallback after four attempts (T3-2-2)."""
    emitted: list[Bar] = []

    class _Down:
        calls = 0

        def get_session_daily_bars(self, *_args: Any, **_kwargs: Any) -> dict[str, Bar]:
            self.calls += 1
            raise ConnectionError("provider down")

    engine = _engine(lambda bars, **_kwargs: _result())
    engine._historical = _Down()
    engine._emit_finalized_bar = lambda _timeframe, bar: emitted.append(bar)  # type: ignore[method-assign]
    engine._session_close_fallback = _SessionCloseFallback(
        session=date(2026, 9, 29),
        symbols=("SPY",),
        retry_at=SESSION_CLOSE + timedelta(minutes=15),
    )
    _Clock(SESSION_CLOSE + timedelta(minutes=15)).install(monkeypatch)

    # One attempt per minute from the embargo until one interval before the
    # 40-minute decision window closes (close + 39 min): far more than four.
    for minute in range(15, 39):
        engine._run_session_close_fallback(SESSION_CLOSE + timedelta(minutes=minute))
    pending = engine._session_close_fallback
    assert pending is not None and pending.attempts == 24
    assert engine._historical.calls == 24  # type: ignore[attr-defined]

    engine._run_session_close_fallback(SESSION_CLOSE + timedelta(minutes=39))
    assert engine._session_close_fallback is None
    assert emitted == []


def test_readiness_compares_the_session_not_the_bar_label() -> None:
    """An adopted provider bar (00:00 ET label) is ready against the stream bar (09:30 ET) (T3-5-3)."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = False
    runtime._state = "warming_up"
    runtime._not_ready_symbols = set()
    stream_day0 = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)  # 09:30 ET label
    stream_day1 = datetime(2025, 1, 3, 14, 30, tzinfo=UTC)
    provider_day0 = datetime(2025, 1, 2, 5, 0, tzinfo=UTC)  # 00:00 ET label
    provider_day1 = datetime(2025, 1, 3, 5, 0, tzinfo=UTC)

    def bar(symbol: str, timestamp: datetime) -> Bar:
        return Bar(timestamp, symbol, "1d", 100, 100, 100, 100, 1_000, Market.EQUITY)

    portfolio_runtime = SimpleNamespace(
        market=Market.EQUITY,
        symbols=("SPY", "QLD"),
        strategy=SimpleNamespace(minimum_history_bars=2),
        history={
            "SPY": [bar("SPY", stream_day0), bar("SPY", stream_day1)],
            "QLD": [bar("QLD", provider_day0), bar("QLD", provider_day1)],
        },
    )
    runtime._portfolio_runtimes = (portfolio_runtime,)
    runtime._refresh_readiness()
    assert runtime._not_ready_symbols == set()

    # The same label gap on a STALE session is still not ready.
    portfolio_runtime.history["QLD"] = [bar("QLD", provider_day0), bar("QLD", provider_day0)]
    runtime._refresh_readiness()
    assert runtime._not_ready_symbols == {"QLD"}
