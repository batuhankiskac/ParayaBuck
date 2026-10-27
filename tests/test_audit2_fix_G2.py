"""Audit wave 2, worker G2: alert and e-mail kinds (D2-8-1 .. D2-8-13).

Offline only: every SMTP server is a fake class, the journal is stubbed, and
no clock or sleep is real. Nothing here writes a production path.
"""

from __future__ import annotations

import logging
import threading
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.config import EmailAlertConfig
from parayabuck.monitoring import alerts, email_alerts
from parayabuck.monitoring.email_alerts import (
    SMTP_PASSWORD_ENV,
    SMTP_USERNAME_ENV,
    EmailAlertSink,
    build_message,
)
from parayabuck.monitoring.events import ALERT_KINDS, CRITICAL_EMAIL_KINDS, EMAIL_ALERT_KINDS, WARNING_KINDS
from parayabuck.monitoring.incident import IncidentCategory, create_incident
from parayabuck.monitoring.paper_incident import emit_incident_record

_ENV = {SMTP_USERNAME_ENV: "bot@example.com", SMTP_PASSWORD_ENV: "app-password-123"}


def _settings(**overrides: Any) -> EmailAlertConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "security": "starttls",
        "from_address": "bot@example.com",
        "to_addresses": ["owner@example.com"],
        "min_interval_minutes": 30,
        "max_per_hour": 20,
    }
    values.update(overrides)
    return EmailAlertConfig(**values)


class _OkSMTP:
    """Accepts every message; records nothing that matters here."""

    def __init__(self, host: str, port: int, timeout: float = 0.0, context: Any = None) -> None:
        pass

    def ehlo(self) -> None:
        pass

    def starttls(self, context: Any = None) -> None:
        pass

    def login(self, username: str, password: str) -> None:
        pass

    def send_message(self, msg: Any) -> None:
        pass

    def quit(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    alerts.reset_for_tests()
    email_alerts.reset_for_tests()
    monkeypatch.setattr("parayabuck.monitoring.journal.emit_journal", lambda event: None)
    yield
    email_alerts.reset_for_tests()
    alerts.reset_for_tests()


def _sink(settings: EmailAlertConfig | None = None, **kwargs: Any) -> EmailAlertSink:
    kwargs.setdefault("sleep", lambda _delay: None)
    return EmailAlertSink(
        settings or _settings(),
        mode="paper",
        env=_ENV,
        smtp_factory=_OkSMTP,
        smtp_ssl_factory=_OkSMTP,
        start_worker=False,
        **kwargs,
    )


def _delivered(kind: str) -> int:
    delivered = alerts.alert_stats()["delivered"]
    assert isinstance(delivered, dict)
    return int(delivered.get(kind, 0))


# --- D2-8-1: the hourly cap keeps room for critical kinds --------------------


def test_hourly_cap_keeps_a_reserve_that_non_critical_mail_cannot_use(monkeypatch: pytest.MonkeyPatch) -> None:
    journaled: list[str] = []
    monkeypatch.setattr(email_alerts, "_record_delivery_failure", lambda kind, reason, error: journaled.append(kind))
    sink = _sink(_settings(max_per_hour=20))
    # Non-critical kinds stop at cap minus reserve (20 - 5 = 15).
    for index in range(15):
        assert sink.notify("order_rejected", "m", {}, f"order_rejected:{index}") is True
    assert sink.notify("order_rejected", "m", {}, "order_rejected:late") is False
    assert journaled == ["order_rejected"]
    # The halt still goes out, through the reserved slots.
    assert sink.notify("drawdown_halt", "halt", {}, "drawdown_halt:acct") is True
    assert sink.notify("circuit_breaker", "halt", {}, "circuit_breaker:acct") is True
    assert sink.notify("exit_sell_rejected", "sell", {}, "exit_sell_rejected:QLD") is True
    assert sink.notify("drawdown_halt", "halt", {}, "drawdown_halt:again") is True
    assert sink.notify("risk_incident", "risk", {}, "risk_incident:acct") is True
    # The full hour (20) is spent: even a critical mail is dropped and journaled.
    assert sink.notify("drawdown_halt", "halt", {}, "drawdown_halt:over") is False
    assert journaled[-1] == "drawdown_halt"


def test_a_critical_severity_incident_is_reserved_whatever_its_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_alerts, "_record_delivery_failure", lambda *args: None)
    sink = _sink(_settings(max_per_hour=20))
    for index in range(15):
        sink.notify("order_rejected", "m", {}, f"order_rejected:{index}")
    assert sink.notify("paper_incident", "db", {"severity": "warning"}, "paper_incident:w") is False
    assert sink.notify("paper_incident", "db", {"severity": "critical"}, "paper_incident:c") is True


def test_reserve_is_a_quarter_of_the_cap_and_zero_for_small_caps() -> None:
    assert email_alerts._critical_reserve(20) == 5
    assert email_alerts._critical_reserve(3) == 0
    assert email_alerts._critical_reserve(1) == 0
    assert set(CRITICAL_EMAIL_KINDS) <= EMAIL_ALERT_KINDS


# --- D2-8-5 / D2-8-1: delivery counters and the failure key ------------------


def test_alert_delivery_failed_is_keyed_by_failed_kind_and_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    email_alerts._record_delivery_failure("circuit_breaker", "rate_limited", "cap")
    email_alerts._record_delivery_failure("drawdown_halt", "rate_limited", "cap")
    # Two kinds failing for the same reason inside 60 s are both journaled.
    assert _delivered("alert_delivery_failed") == 2
    # A repeat of the same kind and reason is still coalesced.
    email_alerts._record_delivery_failure("circuit_breaker", "rate_limited", "cap again")
    assert _delivered("alert_delivery_failed") == 2
    # A different reason for the same kind is not swallowed.
    email_alerts._record_delivery_failure("circuit_breaker", "queue_full", "full")
    assert _delivered("alert_delivery_failed") == 3


def test_delivery_status_exposes_counters_and_last_success_age(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_alerts, "_record_delivery_failure", lambda *args: None)
    clock = {"now": 500.0}
    sink = _sink(clock=lambda: clock["now"])
    status = sink.delivery_status()
    assert status["sent"] == 0 and status["failed"] == 0 and status["dropped"] == 0
    assert status["last_success_age_seconds"] is None
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:x") is True
    assert sink.process_one(timeout=0.1) is True
    clock["now"] += 42.0
    status = sink.delivery_status()
    assert status["sent"] == 1
    assert status["last_success_age_seconds"] == pytest.approx(42.0)
    # The engine snapshot carries the same block (alert_stats is what it persists).
    email_alerts.install_sink_for_tests(sink)
    assert alerts.alert_stats()["email"]["sent"] == 1  # type: ignore[index]


def test_alert_stats_has_no_email_block_without_a_sink() -> None:
    assert "email" not in alerts.alert_stats()


# --- D2-8-2 / D2-8-8: stable episode keys and CRITICAL severity -------------


def test_repeats_of_one_incident_episode_coalesce_across_record_uuids() -> None:
    first = create_incident(IncidentCategory.BROKER_ACCOUNT_STALE, account_id="acct")
    second = create_incident(IncidentCategory.BROKER_ACCOUNT_STALE, account_id="acct")
    assert first.incident_id != second.incident_id
    assert first.episode_key == second.episode_key
    emit_incident_record(first)
    emit_incident_record(second)
    assert _delivered("circuit_breaker") == 1
    assert alerts.alert_stats()["suppressed"] == 1


def test_distinct_incident_categories_on_one_account_stay_distinct() -> None:
    emit_incident_record(create_incident(IncidentCategory.POSITION_DRIFT, account_id="acct"))
    emit_incident_record(create_incident(IncidentCategory.ORDER_DRIFT, account_id="acct"))
    assert _delivered("reconciliation_failure") == 2


def test_a_critical_database_error_is_named_at_error_level_not_as_reconciliation(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[dict[str, Any]] = []
    real_emit = alerts.emit

    def capture(kind: str, *, message: str, **fields: object) -> None:
        seen.append({"kind": kind, **fields})
        real_emit(kind, message=message, **fields)

    monkeypatch.setattr(alerts, "emit", capture)
    record = create_incident(IncidentCategory.DATABASE_ERROR, account_id="acct", error="database is locked")
    with caplog.at_level(logging.WARNING, logger="parayabuck.monitoring.alerts"):
        emit_incident_record(record)
    assert seen[0]["kind"] == "paper_incident"
    assert seen[0]["category"] == "DATABASE_ERROR"
    assert seen[0]["severity"] == "critical"
    lines = [r for r in caplog.records if r.getMessage().startswith("ALERT paper_incident")]
    assert lines and lines[0].levelno == logging.ERROR
    assert _delivered("reconciliation_failure") == 0


def test_severity_field_drives_the_log_level() -> None:
    assert alerts._severity("paper_incident", {"severity": "critical"}) == logging.ERROR
    assert alerts._severity("paper_incident", {"severity": "warning"}) == logging.WARNING
    assert alerts._severity("paper_incident") == logging.WARNING


# --- D2-8-3 / D2-8-13: distinct incidents get distinct keys ----------------


def test_reconciliation_incidents_with_distinct_episodes_are_all_delivered() -> None:
    alerts.emit(
        "reconciliation_failure",
        message="Reconciliation drift detected",
        reason="reconciliation_failed",
        episode_key="reconciliation_drift",
    )
    alerts.emit(
        "reconciliation_failure",
        message="Reconciliation returned an invalid result shape",
        reason="reconciliation_failed",
        episode_key="reconciliation_invalid_result",
    )
    alerts.emit(
        "reconciliation_failure",
        message="Position protection check deferred after Alpaca API error",
        reason="reconciliation_failed",
        episode_key="protection_check_api_error",
    )
    assert _delivered("reconciliation_failure") == 3
    assert alerts.alert_stats()["suppressed"] == 0


def test_the_same_reconciliation_episode_is_still_coalesced() -> None:
    for message in ("drift", "drift again"):
        alerts.emit(
            "reconciliation_failure",
            message=message,
            reason="reconciliation_failed",
            episode_key="reconciliation_drift",
        )
    assert _delivered("reconciliation_failure") == 1


def test_risk_incidents_with_different_reasons_are_not_merged() -> None:
    alerts.emit("risk_incident", message="daily loss", reason="daily_loss_limit")
    alerts.emit("risk_incident", message="foreign", reason="foreign_inventory")
    assert _delivered("risk_incident") == 2


# --- D2-8-4 / D2-8-7 / D2-8-11: coverage of the missing kinds --------------


@pytest.mark.parametrize(
    "kind",
    ["live_started", "safety_lock_reset", "risk_epoch_reset", "signal_adjustment_failure", "intent_recovered"],
)
def test_state_change_kinds_are_journaled_and_mailed(kind: str) -> None:
    assert kind in ALERT_KINDS
    assert kind in EMAIL_ALERT_KINDS
    assert _sink().accepts(kind)


def test_signal_adjustment_failure_is_a_warning() -> None:
    assert "signal_adjustment_failure" in WARNING_KINDS
    assert alerts._severity("signal_adjustment_failure") == logging.WARNING


def test_routine_kinds_stay_out_of_the_mail_channel() -> None:
    sink = _sink()
    assert not sink.accepts("runtime_started")
    assert not sink.accepts("alert_delivery_failed")
    assert not sink.accepts("decision_deferred")


# --- D2-8-12: subject and field rendering -----------------------------------


def test_subject_keeps_a_long_headline_and_body_keeps_everything() -> None:
    headline = "Daily summary: " + "status " * 25
    msg: EmailMessage = build_message(_settings(), kind="daily_summary", message=headline, fields={}, hostname="vm")
    assert msg["Subject"].endswith(headline.strip())
    assert headline.strip() in msg.get_content()
    very_long = "x" * 260
    cut = build_message(_settings(), kind="k", message=very_long, fields={}, hostname="vm")
    assert cut["Subject"].endswith("...")
    assert len(cut["Subject"]) < len(very_long) + 40


def test_list_and_dict_fields_are_rendered_as_text_not_reprs() -> None:
    msg = build_message(
        _settings(),
        kind="circuit_breaker",
        message="halt rows migrated",
        fields={"reasons": ["ownership_drift", "protection_failure"], "detail": {"symbol": "QLD", "qty": 3}},
        hostname="vm",
    )
    text = msg.get_content()
    assert "reasons: ownership_drift, protection_failure" in text
    assert "detail: qty=3, symbol=QLD" in text
    assert "[" not in text.split("Details:", 1)[1]
    assert "{" not in text.split("Details:", 1)[1]


# --- D2-8-3 / D2-8-13 (runtime emit arguments are plain strings) ----------


def test_runtime_reconciliation_sites_pass_distinct_episode_keys() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "parayabuck" / "runtime.py").read_text(encoding="utf-8")
    for episode in (
        "reconciliation_drift",
        "reconciliation_network_error",
        "reconciliation_invalid_result",
        "protection_check_api_error",
        "protection_check_generic",
    ):
        assert f'episode_key="{episode}"' in source


# --- D2-8-4: a halt cleared by hand is journaled and mailed ----------------

_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}


def test_operator_safety_lock_reset_is_journaled_as_safety_lock_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parayabuck.core.models import AccountSnapshot, Market
    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.runtime import EngineRuntime

    journaled: list[str] = []
    monkeypatch.setattr("parayabuck.monitoring.journal.emit_journal", lambda event: journaled.append(event.kind))
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "ownership_drift")

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "ownership_drift"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: None}
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),
        account=lambda: account,
    )
    runtime.ledger = ledger
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_risk_latch = {Market.EQUITY: None}

    runtime.reset_safety_lock()

    assert _delivered("safety_lock_reset") == 1
    assert "safety_lock_reset" in journaled
    assert runtime._circuit_breaker_reason is None
    ledger.close()
