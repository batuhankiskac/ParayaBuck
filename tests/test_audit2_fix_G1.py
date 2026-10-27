"""Audit wave 2, worker G1: safety journal, redaction and logging sinks (C2-3, S2-2, S2-9).

Offline only: the journal emit is captured by a stub, the SMTP sink is never
started, and no real journal, log file or production path is written.
"""

from __future__ import annotations

import logging
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck import logger as parayabuck_logger
from parayabuck.monitoring import alerts, email_alerts
from parayabuck.monitoring import journal as journal_module
from parayabuck.monitoring.events import EMAIL_ALERT_KINDS
from parayabuck.monitoring.journal import SAFETY_JOURNAL_KINDS, EventJournal
from parayabuck.monitoring.operational_event import (
    OperationalEvent,
    create_event,
    mask_account_number,
    redact_fields,
    redact_text,
    register_secret_text,
)
from parayabuck.portfolio.runtime_repository import _alerts_after_release
from parayabuck.web.security import TicketRedactionFilter

_SECRET = "SUPERSECRETVALUE-123"


@pytest.fixture(autouse=True)
def _clean_alert_state() -> Any:
    alerts.reset_for_tests()
    yield
    alerts.reset_for_tests()


def _capture_journal(monkeypatch: pytest.MonkeyPatch, result: bool = True) -> list[OperationalEvent]:
    events: list[OperationalEvent] = []

    def _fake_emit(event: OperationalEvent, *, path: Any = None) -> bool:
        events.append(event)
        return result

    monkeypatch.setattr(journal_module, "emit_journal", _fake_emit)
    return events


# --- S2-2-1: the top-level account id is masked everywhere it is written --------------


def test_top_level_account_id_is_masked_in_the_event_and_its_dict() -> None:
    event = create_event(kind="drawdown_halt", message="halt", account_id="PA3ACCOUNT5678")

    assert event.account_id == "***5678"
    assert event.to_dict()["account_id"] == "***5678"


def test_journal_stores_the_masked_account_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal = EventJournal(tmp_path / "observability.sqlite3")
    raw = OperationalEvent(
        event_id="e1",
        occurred_at=datetime.now(UTC),
        kind="drawdown_halt",
        severity="CRITICAL",
        message="halt",
        account_id="PA3ACCOUNT5678",
    )

    assert journal.emit(raw) is True
    stored = journal.query(kind="drawdown_halt")

    assert stored and stored[0].account_id == "***5678"


# --- S2-2-5: the key matcher covers the provider spellings that were missed ----------


@pytest.mark.parametrize("key", ["APCA-API-KEY-ID", "apiKey", "private_key", "passphrase", "access_key", "ticket"])
def test_provider_credential_spellings_are_redacted(key: str) -> None:
    assert redact_fields({key: "value-1234"})[key] == "***REDACTED***"


# --- S2-2-3: free text is scrubbed before log, journal and e-mail ---------------------


def test_free_text_assignments_and_bearer_tokens_are_scrubbed() -> None:
    text = (
        'stderr: fetch https://h.example/x?X-Amz-Signature=abc123def and {"secret_key": "k-9876543210"} Bearer tok.en-1'
    )

    scrubbed = redact_text(text)

    assert "abc123def" not in scrubbed
    assert "k-9876543210" not in scrubbed
    assert "tok.en-1" not in scrubbed
    assert "secret_key" in scrubbed  # the name stays readable


def test_redaction_is_idempotent_on_its_own_output() -> None:
    once = redact_text("token=abc12345678 ticket=[redacted]")

    assert redact_text(once) == once
    assert "[redacted]" in once


def test_registered_literal_value_is_scrubbed_from_text() -> None:
    register_secret_text(_SECRET)

    assert _SECRET not in redact_text(f"failed with {_SECRET} in the header")


def test_alert_message_is_scrubbed_before_it_is_journaled(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture_journal(monkeypatch)

    alerts.emit("data_provider_unavailable", message="stderr token=ZZ9-token-000 leaked")

    assert events and "ZZ9-token-000" not in events[0].message


# --- C2-3-7: the throttle key is recorded only once the journal holds the event -------


def test_failed_journal_write_leaves_the_repeat_to_be_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture_journal(monkeypatch, result=False)

    alerts.emit("drawdown_halt", message="halt", reason="x")
    alerts.emit("drawdown_halt", message="halt", reason="x")

    assert len(events) == 2  # the first write failed, so the repeat is not throttled


def test_successful_journal_write_throttles_the_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture_journal(monkeypatch, result=True)

    alerts.emit("drawdown_halt", message="halt", reason="x")
    alerts.emit("drawdown_halt", message="halt", reason="x")

    assert len(events) == 1
    assert alerts.alert_stats()["suppressed"] == 1


# --- F2-7-10: alerts carry the engine mode and account --------------------------------


def test_alert_context_stamps_mode_and_masked_account(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture_journal(monkeypatch)
    alerts.set_alert_context(mode="paper", account_id="PA3ACCOUNT5678")

    alerts.emit("drawdown_halt", message="halt")

    assert events[0].mode == "paper"
    assert events[0].account_id == "***5678"


def test_alert_fields_win_over_the_context(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture_journal(monkeypatch)
    alerts.set_alert_context(mode="paper")

    alerts.emit("drawdown_halt", message="halt", mode="live")

    assert events[0].mode == "live"


# --- C2-3-9 and the safety set: sync writes skip the retention sweep ------------------


def test_synchronous_safety_writes_do_not_run_the_retention_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = EventJournal(tmp_path / "observability.sqlite3", max_events=10)
    sweeps: list[object] = []
    monkeypatch.setattr(journal, "_retention_cleanup", lambda conn: sweeps.append(conn))

    for index in range(120):
        event = create_event(kind="drawdown_halt", message=f"halt {index}", event_id=f"sync-{index}")
        assert journal.emit(event) is True

    assert sweeps == []


def test_safety_set_is_explicit_and_not_the_email_list() -> None:
    assert SAFETY_JOURNAL_KINDS is not EMAIL_ALERT_KINDS
    assert {"drawdown_halt", "backup_failed", "order_rejected"} <= SAFETY_JOURNAL_KINDS
    assert "daily_summary" not in SAFETY_JOURNAL_KINDS


# --- C2-3-8: the e-mail sink refuses intake once closed, and strands nothing ----------


def _sink() -> email_alerts.EmailAlertSink:
    settings = SimpleNamespace(
        enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        security="starttls",
        from_address="bot@example.com",
        to_addresses=("owner@example.com",),
        daily_summary=False,
        min_interval_minutes=0,
        max_per_hour=100,
        timeout_seconds=5,
    )
    return email_alerts.EmailAlertSink(settings, start_worker=False)


def test_notify_after_close_is_refused_and_queues_nothing() -> None:
    sink = _sink()
    assert sink.notify("drawdown_halt", "halt", {}, "k1") is True

    sink.close()

    assert sink.notify("drawdown_halt", "halt again", {}, "k2") is False
    assert sink._queue.qsize() == 2  # the item queued before close and the close marker only


# --- S2-2-2: SMTP error text loses the routing addresses -----------------------------


def test_smtp_error_text_loses_recipients_and_sender() -> None:
    settings = SimpleNamespace(to_addresses=("owner@example.com",), from_address="bot@example.com")
    raw = "SMTPRecipientsRefused: {'owner@example.com': (550, b'no such user')} from bot@example.com"

    scrubbed = email_alerts._scrub_error(raw, settings, {})

    assert "owner@example.com" not in scrubbed
    assert "bot@example.com" not in scrubbed


# --- C2-3-4: a collected alert runs only after the locks are released -----------------


def test_alert_callbacks_run_after_the_lock_is_released() -> None:
    lock = threading.Lock()
    observed: list[bool] = []
    pending: list[Any] = []

    with _alerts_after_release(pending), lock:
        pending.append(lambda: observed.append(lock.locked()))

    assert observed == [False]


# --- S2-9-1 and S2-9-2: logger pinning and the sink-side scrub -----------------------


def test_websockets_loggers_are_pinned_to_warning() -> None:
    child = logging.getLogger("websockets.client")
    child.setLevel(logging.DEBUG)

    parayabuck_logger.setup_logging("INFO")

    assert logging.getLogger("websockets").level == logging.WARNING
    assert child.level == logging.WARNING
    assert logging.getLogger("websockets.server").getEffectiveLevel() == logging.WARNING


def test_redacting_formatter_scrubs_the_traceback_too() -> None:
    register_secret_text(_SECRET)
    try:
        raise ValueError(f"handshake failed for {_SECRET}")
    except ValueError:
        record = logging.LogRecord("parayabuck.x", logging.ERROR, __file__, 1, "order failed", None, sys.exc_info())

    rendered = parayabuck_logger.RedactingFormatter(parayabuck_logger.LOG_FORMAT).format(record)

    assert "Traceback" in rendered
    assert _SECRET not in rendered


def test_ticket_filter_scrubs_the_uvicorn_traceback() -> None:
    register_secret_text(_SECRET)
    try:
        raise RuntimeError(f"bad frame {_SECRET}")
    except RuntimeError:
        record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "ws error", None, sys.exc_info())

    assert TicketRedactionFilter().filter(record) is True
    rendered = logging.Formatter().format(record)

    assert _SECRET not in rendered


def test_mask_account_number_keeps_only_the_last_four() -> None:
    assert mask_account_number("PA3ACCOUNT5678") == "***5678"
    assert mask_account_number("abc") == "***"


# --- D2-8-13 remainder: a rejected SELL's episode names the order and the symbol ------


def test_rejected_exit_sells_of_two_symbols_in_one_decision_are_separate_episodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from parayabuck.portfolio import runtime_service

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(alerts, "emit", lambda kind, **kwargs: captured.append({"kind": kind, **kwargs}))

    for client_order_id, symbol in (("sell-1", "QLD"), ("sell-2", "SSO")):
        runtime_service.emit_exit_sell_rejected_safe(
            client_order_id=client_order_id, symbol=symbol, market="equity", decision_id="d1"
        )

    episodes = [entry["episode_key"] for entry in captured]
    assert episodes[0] != episodes[1]
    assert "sell-1" in episodes[0] and "QLD" in episodes[0]
    assert "sell-2" in episodes[1] and "SSO" in episodes[1]
