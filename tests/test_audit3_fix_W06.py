"""Audit wave 3 W06: alert coverage and the trade-feed watchdog.

Deterministic: fixed timestamps, tmp_path state, no network, no credentials.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.config import EmailAlertConfig
from parayabuck.core.models import Market
from parayabuck.data.alpaca import HistoricalProviderCooldown
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring import alerts, email_alerts
from parayabuck.monitoring.events import (
    ALERT_KINDS,
    CRITICAL_EMAIL_KINDS,
    EMAIL_ALERT_KINDS,
    WARNING_KINDS,
)
from parayabuck.monitoring.health import HealthStatus, _check_runtime_heartbeat
from parayabuck.monitoring.incident import RESERVED_INCIDENT_CATEGORIES, IncidentCategory
from parayabuck.monitoring.intent_watchdog import IntentWatchdogSettings, evaluate_equity
from parayabuck.monitoring.journal import SAFETY_JOURNAL_KINDS, EventJournal
from parayabuck.monitoring.operational_event import create_event
from parayabuck.portfolio.runtime_service import _OPERATOR_ALERT_KEYS_MAX, PortfolioRuntime
from parayabuck.runtime import EngineRuntime
from parayabuck.storage import SQLiteStore

# 2026-10-27 is a Tuesday in EDT (UTC-4): the regular session is 13:30-20:00 UTC.
SESSION_OPEN = datetime(2026, 10, 27, 13, 30, tzinfo=UTC)
PREVIOUS_CLOSE_STAMP = datetime(2026, 10, 26, 20, 20, tzinfo=UTC)  # 16:20 ET the day before


def _email_settings() -> EmailAlertConfig:
    return EmailAlertConfig(
        enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        security="starttls",
        from_address="bot@example.com",
        to_addresses=["owner@example.com"],
        min_interval_minutes=30,
        max_per_hour=20,
    )


def _sink() -> email_alerts.EmailAlertSink:
    return email_alerts.EmailAlertSink(
        _email_settings(),
        mode="paper",
        env={},
        smtp_factory=lambda *a, **k: None,
        smtp_ssl_factory=lambda *a, **k: None,
        start_worker=False,
        sleep=lambda _delay: None,
    )


# --- Q3-2-1: a stop_failed snapshot that goes stale is CRITICAL ----------------


def _write_snapshot(path: Path, *, state: str, written_at: datetime) -> None:
    path.write_text(json.dumps({"runtime_state": state, "written_at": written_at.isoformat()}), encoding="utf-8")


def test_stale_stop_failed_snapshot_is_critical(tmp_path: Path) -> None:
    snapshot = tmp_path / "health_snapshot.json"
    _write_snapshot(snapshot, state="stop_failed", written_at=SESSION_OPEN - timedelta(hours=5))

    check = _check_runtime_heartbeat(snapshot, SESSION_OPEN, 120.0)

    assert check.status is HealthStatus.CRITICAL
    assert "kalp atışı" in check.summary and "eski" in check.summary


def test_clean_stop_snapshot_stays_planned_blocked(tmp_path: Path) -> None:
    snapshot = tmp_path / "health_snapshot.json"
    _write_snapshot(snapshot, state="stopped", written_at=SESSION_OPEN - timedelta(hours=5))

    check = _check_runtime_heartbeat(snapshot, SESSION_OPEN, 120.0)

    assert check.status is HealthStatus.BLOCKED


# --- D3-3-2 / D3-3-3 / D3-3-4 / D3-3-8: which kinds reach the e-mail channel ----


def test_expired_decision_window_is_its_own_critical_mailed_kind() -> None:
    kind = "decision_window_expired"
    assert kind in ALERT_KINDS
    assert kind in WARNING_KINDS
    assert kind in EMAIL_ALERT_KINDS
    assert kind in CRITICAL_EMAIL_KINDS
    assert _sink().accepts(kind)


def test_routine_decision_deferral_is_still_not_mailed() -> None:
    assert not _sink().accepts("decision_deferred")


def test_unclean_restart_and_recoveries_are_mailed() -> None:
    sink = _sink()
    for kind in ("runtime_unclean_restart", "reconciliation_recovered", "signal_adjustment_recovery"):
        assert kind in ALERT_KINDS, kind
        assert sink.accepts(kind), kind


def test_secondary_unavailable_cross_check_is_mailed_but_mismatch_is_not() -> None:
    sink = _sink()
    assert sink.accepts("signal_source_mismatch", {"category": "secondary_unavailable"})
    assert not sink.accepts("signal_source_mismatch", {"category": "trend_mismatch"})
    assert not sink.accepts("signal_source_mismatch", {"category": "close_divergence"})
    assert not sink.accepts("signal_source_mismatch")


def test_notify_passes_the_fields_to_the_kind_filter() -> None:
    sink = _sink()
    sent = sink.notify(
        "signal_source_mismatch",
        "second source unavailable",
        {"category": "secondary_unavailable"},
        throttle_key="signal_source_mismatch:secondary_unavailable",
    )
    assert sent is True


# --- D3-3-4: an unended previous session is detected before the new start -----


def test_ledger_lists_only_sessions_without_a_recorded_end(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    store = SQLiteStore(db)
    ledger = ExecutionLedger(str(db), store=store)
    try:
        crashed = ledger.record_runtime_session("acct", "paper")
        clean = ledger.record_runtime_session("acct", "paper")
        ledger.end_runtime_session(clean)

        unended = ledger.unended_runtime_sessions()

        assert [row["session_id"] for row in unended] == [crashed]
        assert unended[0]["mode"] == "paper"
    finally:
        ledger.close()


def test_runtime_helper_reads_the_ledger_and_survives_its_failure() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    row = {"session_id": "s1", "mode": "paper", "started_at": "t"}
    runtime.ledger = SimpleNamespace(unended_runtime_sessions=lambda: [row])
    assert runtime._unended_runtime_sessions() == [row]

    def _boom() -> list[dict[str, str]]:
        raise RuntimeError("db locked")

    runtime.ledger = SimpleNamespace(unended_runtime_sessions=_boom)
    assert runtime._unended_runtime_sessions() == []
    runtime.ledger = SimpleNamespace()
    assert runtime._unended_runtime_sessions() == []


# --- D3-3-10: categories with no raise site are documented as reserved --------


def test_reserved_incident_categories_are_exactly_the_unraised_seven() -> None:
    assert RESERVED_INCIDENT_CATEGORIES == frozenset(
        {
            IncidentCategory.ORDER_DRIFT,
            IncidentCategory.SIGNAL_HISTORY_UNVERIFIED,
            IncidentCategory.STATE_RESTORE_FAILED,
            IncidentCategory.DUPLICATE_DECISION,
            IncidentCategory.DUPLICATE_ORDER_RISK,
            IncidentCategory.CLOCK_OR_SESSION_MISMATCH,
            IncidentCategory.RESTART_RECOVERY_FAILED,
        }
    )


# --- F3-2-1 / Q3-2-3: the per-order silence clock --------------------------------


def _feed_runtime(orders: tuple[Any, ...], stamps: dict[str, datetime]) -> tuple[EngineRuntime, list[str]]:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._trade_feed_probe_at = None
    runtime._order_update_at = dict(stamps)
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime._portfolio_repository = SimpleNamespace(open_orders=lambda _account_id: orders)
    latches: list[str] = []
    runtime._set_reconcile_pending = lambda reason: latches.append(reason)  # type: ignore[method-assign]
    return runtime, latches


def test_an_order_left_working_overnight_does_not_latch_at_the_open() -> None:
    """The stream stamp from 16:20 ET the day before is not silence in this session."""
    runtime, latches = _feed_runtime(
        (SimpleNamespace(client_order_id="day-order"),),
        {"day-order": PREVIOUS_CLOSE_STAMP},
    )

    runtime._maybe_check_trade_feed_freshness(now=SESSION_OPEN + timedelta(seconds=30))

    assert latches == []


def test_silence_inside_the_session_still_latches_from_the_open() -> None:
    runtime, latches = _feed_runtime(
        (SimpleNamespace(client_order_id="day-order"),),
        {"day-order": PREVIOUS_CLOSE_STAMP},
    )

    runtime._maybe_check_trade_feed_freshness(now=SESSION_OPEN + timedelta(minutes=10))

    assert latches == ["trade_feed_stale"]


def test_a_recent_probe_trigger_restarts_the_silence_clock() -> None:
    """Q3-2-3: the trigger branch of max(last_seen, _trade_feed_probe_at)."""
    now = datetime(2026, 10, 27, 16, 0, tzinfo=UTC)
    runtime, latches = _feed_runtime(
        (SimpleNamespace(client_order_id="working"),),
        {"working": SESSION_OPEN},
    )
    # Without the trigger the order would be 2.5 h silent; the probe 2 min ago resets it.
    runtime._trade_feed_probe_at = now - timedelta(minutes=2)

    runtime._maybe_check_trade_feed_freshness(now=now)

    assert latches == []


def test_a_stale_trigger_still_latches_once_the_ttl_passes() -> None:
    now = datetime(2026, 10, 27, 16, 0, tzinfo=UTC)
    runtime, latches = _feed_runtime(
        (SimpleNamespace(client_order_id="working"),),
        {"working": SESSION_OPEN},
    )
    runtime._trade_feed_probe_at = now - timedelta(minutes=30)

    runtime._maybe_check_trade_feed_freshness(now=now)

    assert latches == ["trade_feed_stale"]


# --- F3-2-2: a refused fill frame latches entries -------------------------------


def test_refused_fill_frame_latches_entries_even_while_a_reconcile_runs() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._order_update_at = {}
    runtime._portfolio_repository = SimpleNamespace(has_order=lambda _client_order_id: True)
    update = SimpleNamespace(
        event="fill",
        client_order_id="c1",
        symbol="QQQ",
        timestamp=SESSION_OPEN,
        received_at=SESSION_OPEN,
    )

    async def _refused(*_args: Any, **_kwargs: Any) -> bool:
        return False

    scheduled: list[str] = []
    latches: list[str] = []
    runtime._apply_trade_update_with_lock_retry = _refused  # type: ignore[method-assign]
    runtime._invalidate_account_snapshot_after_execution = lambda: None  # type: ignore[method-assign]
    runtime._schedule_reconcile = lambda: scheduled.append("reconcile")  # type: ignore[method-assign]
    runtime._set_reconcile_pending = lambda reason: latches.append(reason)  # type: ignore[method-assign]

    asyncio.run(runtime._handle_trade_update(update))  # type: ignore[arg-type]

    assert latches == ["refused_fill_frame"]


# --- T3-2-4: a provider cool-down ends the warmup round, and uses no retry slot --


def _warmup_runtime(history: Any) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._strategy_timeframes = {"1d"}
    runtime._portfolio_runtimes = [
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(timeframe="1d", minimum_history_bars=10),
            symbols=["A", "B", "C"],
            seed=lambda _bars: None,
        )
    ]
    runtime._not_ready_symbols = {"A", "B", "C"}
    runtime._state_lock = threading.RLock()
    runtime._portfolio_lock = threading.RLock()
    runtime._historical = history
    return runtime


def test_cooldown_on_the_first_batch_ends_the_round_without_skipping_per_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "_symbol_batches", lambda _symbols: (["A"], ["B"], ["C"]))
    calls: list[list[str]] = []

    def _cooling(symbols: list[str], *_args: Any, **_kwargs: Any) -> Any:
        calls.append(list(symbols))
        raise HistoricalProviderCooldown("cool-down")

    history = SimpleNamespace(get_bars_batch=_cooling)
    runtime = _warmup_runtime(history)

    answered = runtime._warmup_symbols(history, ["A", "B", "C"], retry=True)  # type: ignore[arg-type]

    assert answered is False
    assert calls == [["A"]]


def test_a_cooldown_round_refunds_its_backoff_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _warmup_runtime(SimpleNamespace())
    runtime._warmup_retry_attempt = 3
    runtime._not_ready_snapshot = lambda: frozenset({"A"})  # type: ignore[method-assign]
    runtime._refresh_readiness = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(runtime, "_warmup_symbols", lambda *_args, **_kwargs: False)

    runtime._retry_incomplete_warmup()

    assert runtime._warmup_retry_attempt == 2


def test_a_round_that_reached_the_provider_keeps_its_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _warmup_runtime(SimpleNamespace())
    runtime._warmup_retry_attempt = 3
    runtime._not_ready_snapshot = lambda: frozenset({"A"})  # type: ignore[method-assign]
    runtime._refresh_readiness = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(runtime, "_warmup_symbols", lambda *_args, **_kwargs: True)

    runtime._retry_incomplete_warmup()

    assert runtime._warmup_retry_attempt == 3


# --- C3-5-4: the once-per-decision operator alert keys stay bounded ------------


def test_operator_alert_keys_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list[str] = []
    monkeypatch.setattr(alerts, "emit", lambda kind, **_fields: emitted.append(kind))
    service = PortfolioRuntime.__new__(PortfolioRuntime)
    service._raised_operator_alerts = set()
    service._market_name = lambda: "equity"  # type: ignore[method-assign]

    for index in range(_OPERATOR_ALERT_KEYS_MAX + 40):
        service._raise_operator_alert_once((f"decision-{index}", "december_calendar_fallback"), message="m")

    assert len(service._raised_operator_alerts) <= _OPERATOR_ALERT_KEYS_MAX
    assert len(emitted) == _OPERATOR_ALERT_KEYS_MAX + 40


# --- C3-5-7: a write after the journal stop is not left in an unjoined writer --


def test_emit_after_an_explicit_stop_writes_synchronously(tmp_path: Path) -> None:
    kind = "decision_deferred"
    assert kind not in SAFETY_JOURNAL_KINDS
    journal = EventJournal(tmp_path / "journal.sqlite3")
    journal.start()
    journal.stop(timeout=2.0)

    accepted = journal.emit(create_event(kind=kind, message="feed fell back"))

    assert accepted is True
    assert journal._started is False
    assert journal._writer_thread is None
    assert len(journal.query(kind=kind)) == 1


# --- T3-5-4: the missed-decision alarm is skipped while the calendar is degraded -


def test_missed_equity_decision_is_not_alarmed_while_the_calendar_is_degraded() -> None:
    # Session 2026-10-27 closed at 20:00 UTC; its grace ended well before this moment.
    now = datetime(2026, 10, 27, 22, 0, tzinfo=UTC)
    running_since = datetime(2026, 10, 27, 13, 0, tzinfo=UTC)
    settings = IntentWatchdogSettings()

    healthy = evaluate_equity(now, None, None, running_since=running_since, settings=settings, calendar_degraded=False)
    degraded = evaluate_equity(now, None, None, running_since=running_since, settings=settings, calendar_degraded=True)

    assert [alarm.code for alarm in healthy] == ["equity_decision_missing"]
    assert degraded == []
