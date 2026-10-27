"""Regression tests for the 2026-10-27 audit fixes in src/parayabuck/monitoring (worker W15).

Offline and deterministic: fake SMTP servers and clocks, tmp_path journals,
injected readiness probes. No socket is opened and no production path is written.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from parayabuck.config import Config
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.monitoring import daily_summary, deadman, email_alerts, health, journal, observe, premarket, receipt
from parayabuck.monitoring.health import HealthStatus, _check_runtime_heartbeat, _heartbeat_tolerance_seconds
from parayabuck.monitoring.journal import EventJournal
from parayabuck.monitoring.operational_event import OperationalEvent, create_event, mask_account_number, redact_fields
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

_ENV: dict[str, str] = {}
_NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


# ---------------------------------------------------------------- e-mail sink


class _SMTP:
    """Fake SMTP server: accepts every call, records the messages it was given."""

    sent: list[Any] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def ehlo(self) -> None:
        pass

    def starttls(self, **kwargs: Any) -> None:
        pass

    def login(self, username: str, password: str) -> None:
        pass

    def send_message(self, msg: Any) -> None:
        _SMTP.sent.append(msg)

    def quit(self) -> None:
        pass


class _BrokenSMTP(_SMTP):
    def send_message(self, msg: Any) -> None:
        raise OSError("connection reset")


def _email_settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "security": "starttls",
        "timeout_seconds": 5.0,
        "from_address": "bot@example.com",
        "to_addresses": ("owner@example.com",),
        "min_interval_minutes": 30,
        "max_per_hour": 10,
        "daily_summary": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _sink(factory: Any, clock: Any) -> email_alerts.EmailAlertSink:
    return email_alerts.EmailAlertSink(
        _email_settings(),
        env=_ENV,
        smtp_factory=factory,
        clock=clock,
        sleep=lambda _seconds: None,
        start_worker=False,
    )


@pytest.fixture
def no_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delivery failures are journaled through alerts; keep that off the real journal."""
    monkeypatch.setattr("parayabuck.monitoring.journal.emit_journal", lambda event: True)


def test_failed_send_does_not_start_the_throttle_window(no_journal: None) -> None:
    """P7-3: a repeat after a failed delivery is queued again, not suppressed."""
    clock = {"now": 1000.0}
    sink = _sink(_BrokenSMTP, lambda: clock["now"])
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:x") is True
    assert sink.process_one(timeout=0.01) is True
    assert sink.stats()["failed"] == 1
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:x") is True


def test_successful_send_starts_the_throttle_window(no_journal: None) -> None:
    """P7-3: after a delivered mail, repeats inside the window are coalesced; after it they are sent."""
    clock = {"now": 1000.0}
    _SMTP.sent = []
    sink = _sink(_SMTP, lambda: clock["now"])
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:x") is True
    assert sink.process_one(timeout=0.01) is True
    assert sink.stats()["sent"] == 1
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:x") is False
    clock["now"] += 31 * 60
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:x") is True


def test_repeat_queued_while_pending_is_coalesced(no_journal: None) -> None:
    """P7-3: an identical alert still waiting in the queue is not queued twice."""
    clock = {"now": 1000.0}
    sink = _sink(_SMTP, lambda: clock["now"])
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:x") is True
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:x") is False
    assert sink.process_one(timeout=0.01) is True
    assert sink.process_one(timeout=0.01) is False


def test_close_keeps_queued_alerts_deliverable(no_journal: None) -> None:
    """C7-5: replacing (closing) the sink must not discard the alerts it already accepted."""
    _SMTP.sent = []
    sink = _sink(_SMTP, lambda: 1000.0)
    assert sink.notify("circuit_breaker", "a", {}, "circuit_breaker:a") is True
    assert sink.notify("drawdown_halt", "b", {}, "drawdown_halt:b") is True
    sink.close()
    assert sink.notify("order_rejected", "c", {}, "order_rejected:c") is False
    assert sink.process_one(timeout=0.01) is True
    assert sink.process_one(timeout=0.01) is True
    assert sink.stats() == {"sent": 2, "failed": 0, "dropped": 0}


# ---------------------------------------------------------------- journal


def _stored(path: Path, sql: str) -> Any:
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        return conn.execute(sql).fetchone()[0]


def test_safety_event_is_committed_before_emit_reports_it(tmp_path: Path) -> None:
    """P7-3: a safety event is on disk when emit returns True (no writer thread involved)."""
    path = tmp_path / "observability.sqlite3"
    journal_handle = EventJournal(path)
    accepted = journal_handle.emit(create_event(kind="circuit_breaker", message="halted"))
    assert accepted is True
    assert _stored(path, "SELECT COUNT(*) FROM operational_events") == 1


def test_safety_event_that_cannot_be_written_is_not_reported_written(tmp_path: Path) -> None:
    """P7-3: a failed safety write returns False instead of a queued-looking True."""
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("file", encoding="utf-8")
    journal_handle = EventJournal(blocker / "observability.sqlite3")
    assert journal_handle.emit(create_event(kind="drawdown_halt", message="halted")) is False


def test_journal_stores_the_redacted_copy_of_nested_secrets(tmp_path: Path) -> None:
    """S6-6: a secret-named key nested in a list is redacted at write time, not only on read."""
    path = tmp_path / "observability.sqlite3"
    journal_handle = EventJournal(path)
    # Built directly: create_event would redact before the journal ever sees the field.
    event = OperationalEvent(
        event_id="evt-redact",
        occurred_at=_NOW,
        kind="circuit_breaker",
        severity="CRITICAL",
        message="halted",
        fields={"hops": [{"bearer": "eyJ-leaked-value", "step": 1}]},
    )
    assert journal_handle.emit(event) is True
    stored = _stored(path, "SELECT fields_json FROM operational_events")
    assert "eyJ-leaked-value" not in stored
    assert "***REDACTED***" in stored


def test_get_journal_reuses_one_handle_per_isolated_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C7-4: repeated get_journal(path) calls share one journal and one writer thread."""
    monkeypatch.setattr(journal, "_global_journal", EventJournal(tmp_path / "global.sqlite3"))
    monkeypatch.setattr(journal, "_isolated_journals", {})
    isolated = tmp_path / "isolated.sqlite3"
    first = journal.get_journal(isolated)
    second = journal.get_journal(isolated)
    assert first is second
    assert first is not journal._global_journal


# ---------------------------------------------------------------- redaction


def test_account_number_is_masked_to_its_last_four_characters() -> None:
    """S6-2: the shipped bundle config keeps the account only as ***NNNN."""
    assert mask_account_number("PA3XK7Q912345678") == "***5678"
    assert mask_account_number("1234") == "***"
    assert mask_account_number("") == ""


def test_bundle_config_drops_email_routing_and_masks_the_account() -> None:
    config = {
        "broker": {"expected_account_id": "PA3XK7Q912345678"},
        "alerting": {
            "email": {
                "smtp_host": "smtp.example.com",
                "from_address": "bot@example.com",
                "to_addresses": ["owner@example.com"],
                "min_interval_minutes": 30,
            }
        },
    }
    redacted = redact_fields(config)
    assert redacted["broker"]["expected_account_id"] == "***5678"
    email = redacted["alerting"]["email"]
    assert email["smtp_host"] == "***REDACTED***"
    assert email["from_address"] == "***REDACTED***"
    assert email["to_addresses"] == "***REDACTED***"
    assert email["min_interval_minutes"] == 30


def test_credential_and_token_keys_are_redacted() -> None:
    """S6-6: token, credential and bearer names join the key list."""
    redacted = redact_fields({"access_token": "t1", "bearer": "b1", "gcp_credential": "c1", "count": 3})
    assert redacted == {
        "access_token": "***REDACTED***",
        "bearer": "***REDACTED***",
        "gcp_credential": "***REDACTED***",
        "count": 3,
    }


# ---------------------------------------------------------------- health


def test_missed_decision_is_never_reported_active() -> None:
    """K-3: a CRITICAL intent watchdog (missed decision) keeps the trading state off ACTIVE."""
    running = {"runtime_state": "running", "healthy": True}
    heartbeat = health.HealthCheck(code="runtime_heartbeat", status=HealthStatus.OK, summary="alive")
    clean = health.HealthCheck(code="intent_watchdog", status=HealthStatus.OK, summary="no alarms")
    missed = health.HealthCheck(code="intent_watchdog", status=HealthStatus.CRITICAL, summary="missed")
    assert health._derive_trading_state(running, [heartbeat, clean]) == health.TradingState.ACTIVE
    assert health._derive_trading_state(running, [heartbeat, missed]) == health.TradingState.UNKNOWN


def test_heartbeat_tolerance_follows_the_snapshot_interval() -> None:
    """D9-6: 90 s floor, or three snapshot intervals when the interval is longer."""

    def config_with(interval: float) -> SimpleNamespace:
        return SimpleNamespace(observability=SimpleNamespace(snapshot_interval_seconds=interval))

    assert _heartbeat_tolerance_seconds(config_with(30.0)) == 90.0
    assert _heartbeat_tolerance_seconds(config_with(120.0)) == 360.0


def test_healthy_runtime_with_a_long_snapshot_interval_is_not_critical(tmp_path: Path) -> None:
    """D9-6: a snapshot 200 s old is fine under a 120 s interval, stale under the old fixed 90 s."""
    path = tmp_path / "health_snapshot.json"
    written = _NOW - timedelta(seconds=200)
    path.write_text(json.dumps({"written_at": written.isoformat(), "runtime_state": "running"}), encoding="utf-8")
    tolerant = _check_runtime_heartbeat(path, _NOW, _heartbeat_tolerance_seconds(_config(120.0)))
    fixed = _check_runtime_heartbeat(path, _NOW, 90.0)
    assert tolerant.status == HealthStatus.OK
    assert fixed.status == HealthStatus.CRITICAL


def _config(interval: float) -> Any:
    base = Config()
    return replace(base, observability=replace(base.observability, snapshot_interval_seconds=interval))


# ---------------------------------------------------------------- dead-man


class _Readyz:
    """Scripted readiness answer and recorded ping URLs; never opens a socket."""

    def __init__(self, status: int, body: dict[str, Any] | None = None, ping_error: Exception | None = None) -> None:
        self.status = status
        self.body = body or {}
        self.ping_error = ping_error
        self.pings: list[str] = []
        self.probed: list[str] = []

    def probe(self, url: str, timeout: float) -> tuple[int, dict[str, Any]]:
        self.probed.append(url)
        return self.status, self.body

    def ping(self, url: str, timeout: float) -> int:
        self.pings.append(url)
        if self.ping_error is not None:
            raise self.ping_error
        return 200


_HEARTBEAT = "https://hc-ping.example/0000-test-uuid"


def _run_deadman(readiness: _Readyz, env: dict[str, str] | None = None) -> int:
    source = {deadman.HEALTHCHECK_URL_ENV: _HEARTBEAT, **(env or {})}
    return deadman.run(source, http=readiness.ping, readyz=readiness.probe, credentials_dir="")


@pytest.mark.parametrize(
    "body",
    [
        {"config_valid": True, "engine_state": "warming_up", "degraded_reasons": []},
        {"config_valid": True, "engine_state": "stopped", "degraded_reasons": []},
        {"config_valid": True, "engine_state": "running", "degraded_reasons": ["equity_signal_source_hold"]},
    ],
)
def test_normal_not_ready_states_ping_success(body: dict[str, Any]) -> None:
    """DD5-1: warm-up, a deliberate stop and a documented hold are not paged."""
    readiness = _Readyz(503, body)
    assert _run_deadman(readiness) == 0
    assert readiness.pings == [_HEARTBEAT]


@pytest.mark.parametrize(
    "body",
    [
        {"config_valid": True, "engine_state": "error", "engine_error": "start failed"},
        {"config_valid": True, "engine_state": "running", "degraded_reasons": ["equity_decision_missing"]},
        {"config_valid": False, "engine_state": "warming_up"},
        {"config_valid": True, "engine_state": "starting", "start_retry_attempt": 4},
    ],
)
def test_real_failures_still_ping_fail(body: dict[str, Any]) -> None:
    """DD5-1: errors, critical alarms and bad config keep paging through /fail."""
    readiness = _Readyz(503, body)
    assert _run_deadman(readiness) == 0
    assert readiness.pings == [_HEARTBEAT + "/fail"]


def test_plain_http_heartbeat_url_is_refused_without_sending() -> None:
    """S-8: the heartbeat URL carries a secret, so only https is accepted."""
    readiness = _Readyz(200)
    env = {deadman.HEALTHCHECK_URL_ENV: "http://hc-ping.example/0000-test-uuid"}
    assert deadman.run(env, http=readiness.ping, readyz=readiness.probe, credentials_dir="") == 1
    assert readiness.probed == [] and readiness.pings == []


def test_console_port_follows_the_environment_then_the_settings_file(tmp_path: Path) -> None:
    """DD5-9: the readiness URL uses PORT, else app.port from the settings, else 8000."""
    settings = tmp_path / "settings.yaml"
    settings.write_text(yaml.safe_dump({"app": {"port": 8123}}), encoding="utf-8")
    assert deadman.console_port({"PORT": "9100"}, str(settings)) == 9100
    assert deadman.console_port({}, str(settings)) == 8123
    assert deadman.console_port({}, str(tmp_path / "missing.yaml")) == 8000


def test_readiness_probe_uses_the_configured_port(tmp_path: Path) -> None:
    """DD5-9: the probed URL is built from PORT when no explicit readyz URL is given."""
    readiness = _Readyz(200)
    env = {deadman.HEALTHCHECK_URL_ENV: _HEARTBEAT, "PORT": "9100"}
    assert deadman.run(env, http=readiness.ping, readyz=readiness.probe, credentials_dir="") == 0
    assert readiness.probed == ["http://127.0.0.1:9100/readyz"]


def test_missing_heartbeat_url_is_a_loud_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """DD5-3: no URL exits 1 with a message on stderr."""
    readiness = _Readyz(200)
    assert deadman.run({}, http=readiness.ping, readyz=readiness.probe, credentials_dir="") == 1
    assert readiness.probed == [] and readiness.pings == []
    assert "no heartbeat URL" in capsys.readouterr().err


# ---------------------------------------------------------------- daily summary


def _target() -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="equity_leveraged_trend_v1",
        strategy_version="1.0.0",
        decision_timestamp=datetime(2026, 9, 29, 13, 30, tzinfo=UTC),
        weights=(TargetWeight("QLD", 0.8, reason="trend_up", rank=1),),
        cash_weight=0.2,
        rebalance_reason=RebalanceReason.SCHEDULED,
        metadata={"hold_symbol": "QLD", "signal_symbol": "QQQ", "trend_state": "uptrend"},
    )


def _account() -> AccountSnapshot:
    return AccountSnapshot(
        cash=30_000.0,
        equity=101_000.0,
        peak_equity=101_000.0,
        daily_pnl=1_000.0,
        positions=(PositionSnapshot("QLD", 700.0, 90.0, 100.0, Market.EQUITY, managed_qty=700.0),),
    )


def test_daily_summary_reports_sells_and_buys_separately() -> None:
    """F9-6: a submitted SELL is not netted against BUYs that were deferred."""
    message, fields = daily_summary.build_equity_daily_summary(
        _target(),
        _account(),
        status="submitted",
        decision_id="d1",
        orders=5,
        submitted=2,
        order_sides={"sell": (2, 2), "buy": (0, 3)},
    )
    assert "orders sell 2/2 buy 0/3" in message
    assert fields["orders_by_side"] == {"sell": {"submitted": 2, "planned": 2}, "buy": {"submitted": 0, "planned": 3}}


def test_daily_summary_keeps_the_combined_count_without_a_side_split() -> None:
    message, fields = daily_summary.build_equity_daily_summary(
        _target(), _account(), status="submitted", decision_id="d1", orders=5, submitted=2
    )
    assert "orders 2/5" in message
    assert "orders_by_side" not in fields


# ---------------------------------------------------------------- receipts


def _receipt_payload(decision_id: str = "abc123") -> dict[str, Any]:
    return {
        "schema_version": 2,
        "decision_id": decision_id,
        "receipt_id": "r1",
        "market": "equity",
        "origin": "scheduled",
        "decision_timestamp": "2026-10-27T19:30:00+00:00",
        "configuration_digest": "digest",
    }


def test_receipt_directory_is_synced_after_the_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S9-2: the receipt directory entry is fsynced once the receipt is linked into place."""
    synced: list[Path] = []
    monkeypatch.setattr(receipt, "_fsync_directory", lambda directory: synced.append(Path(directory)))
    path = receipt.write_decision_receipt_v2(_receipt_payload(), tmp_path / "receipts")
    assert path.exists()
    assert synced == [tmp_path / "receipts"]


# ---------------------------------------------------------------- premarket


class _Broker:
    def __init__(self, next_open: datetime, next_close: datetime) -> None:
        self._clock = SimpleNamespace(timestamp=_NOW, next_open=next_open, next_close=next_close)

    def clock(self) -> Any:
        return self._clock

    def working_orders(self) -> list[Any]:
        return []


def _sessions() -> tuple[tuple[date, datetime, datetime], ...]:
    opened = datetime(2026, 10, 28, 13, 30, tzinfo=UTC)
    closed = datetime(2026, 10, 28, 20, 0, tzinfo=UTC)
    return ((date(2026, 10, 28), opened, closed),)


def test_degraded_calendar_does_not_fail_the_premarket_probe() -> None:
    """T-4: a fallback calendar that counts a holiday as a session must not latch the probe."""
    holiday_open = datetime(2026, 10, 27, 13, 30, tzinfo=UTC)
    broker = _Broker(
        next_open=datetime(2026, 10, 28, 13, 30, tzinfo=UTC), next_close=datetime(2026, 10, 28, 20, 0, tzinfo=UTC)
    )
    fallback = ((date(2026, 10, 27), holiday_open, holiday_open + timedelta(hours=6.5)),)
    result = premarket.premarket_probe(
        broker, now=_NOW, calendar_sessions=fallback + _sessions(), open_orders_local=[], calendar_degraded=True
    )
    assert result.ok, result.reasons


def test_healthy_calendar_still_flags_a_next_open_disagreement() -> None:
    """T-4: with a trusted calendar the same disagreement is still a failure."""
    broker = _Broker(
        next_open=datetime(2026, 10, 29, 13, 30, tzinfo=UTC), next_close=datetime(2026, 10, 29, 20, 0, tzinfo=UTC)
    )
    result = premarket.premarket_probe(
        broker, now=_NOW, calendar_sessions=_sessions(), open_orders_local=[], calendar_degraded=False
    )
    assert not result.ok
    assert any("next_open" in reason for reason in result.reasons)


# ---------------------------------------------------------------- reconciliation journal


def test_clean_reconciliation_is_journaled_once_per_hour_and_again_after_an_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Perf 5: routine success is not journaled every minute; an alert re-arms the next success."""
    emitted: list[str] = []
    monkeypatch.setattr(observe, "emit_journal", lambda event, **_: emitted.append(event.kind) or True)
    clock = {"now": 1000.0}
    monkeypatch.setattr(observe, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    observe.reset_dedup_for_tests()
    observe.on_reconciliation_complete(phase="session")
    clock["now"] += 60
    observe.on_reconciliation_complete(phase="session")
    assert emitted == ["reconciliation_succeeded"]
    observe.note_reconciliation_alert()
    observe.on_reconciliation_complete(phase="session")
    assert emitted == ["reconciliation_succeeded", "reconciliation_succeeded"]
    observe.reset_dedup_for_tests()


def test_alert_kinds_re_arm_the_reconciliation_success_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Perf 5: a reconciliation failure alert clears the hourly dedup (the soak auditor relies on a later success)."""
    from parayabuck.monitoring import alerts

    monkeypatch.setattr(observe, "_reconciliation_success_emitted_at", {"session": 1.0})
    monkeypatch.setattr("parayabuck.monitoring.journal.emit_journal", lambda event: True)
    alerts.reset_for_tests()
    alerts.emit("reconciliation_failure", message="drift", reason="x")
    assert observe._reconciliation_success_emitted_at == {}
    alerts.reset_for_tests()
