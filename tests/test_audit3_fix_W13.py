"""Audit wave 3 fixes W13: secret/account redaction and the stream supervisor.

Covers S3-2-1..S3-2-9 (redaction),
C3-5-2 (alert log outside the lock), C3-5-6 (journal writer join outside the
lock), and C3-4-1..C3-4-4 (stream connect deadline, stop during connect, stale
SDK loop, bounded trading stop, reconnect after first-connect failures).

Offline only: fake SDK streams, a real ExecutionLedger under ``tmp_path`` and
fake account ids (``PA1234567890``). No network, no credentials, no data/ paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

import parayabuck.data.alpaca_stream as alpaca_stream
import parayabuck.execution.alpaca_updates as alpaca_updates
from parayabuck.data.alpaca_stream import AlpacaBarStream, supervise_sdk_stream
from parayabuck.execution.alpaca_models import RiskStatePersistenceError
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.risk_peak import RiskPeakService
from parayabuck.logger import RedactingFormatter
from parayabuck.monitoring import alerts, email_alerts
from parayabuck.monitoring import journal as journal_module
from parayabuck.monitoring.incident import IncidentCategory, create_incident
from parayabuck.monitoring.operational_event import (
    account_episode_token,
    mask_account_text,
    redact_text,
)

ACCOUNT = "PA1234567890"
OTHER_ACCOUNT = "PA0000000001"


# ---------------------------------------------------------------------------
# Part A: redaction
# ---------------------------------------------------------------------------


def test_bearer_token_after_authorization_header_is_masked() -> None:
    # S3-2-4: the assignment pass used to mask the word "Bearer" and leave the token.
    scrubbed = redact_text("Authorization: Bearer abc.def.ghi")

    assert "abc.def.ghi" not in scrubbed
    assert scrubbed == "Authorization: Bearer ***REDACTED***"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ('password="my secret phrase" tail', "phrase"),  # S3-2-7: quoted value with spaces
        ("api key: ABCDEF123456", "ABCDEF123456"),  # S3-2-7: "api key" with a space
        ("secret key=ZZZZZZZZZZ", "ZZZZZZZZZZ"),  # S3-2-7: "secret key" with a space
        ("approval_key: QQQQQQQQ", "QQQQQQQQ"),  # S3-2-7: approval keyword
        ("webhook_url=https://hooks.example/x?k=1 end", "hooks.example"),  # S3-2-7: webhook keyword
    ],
)
def test_free_text_secrets_with_spaces_and_new_keywords_are_masked(text: str, secret: str) -> None:
    scrubbed = redact_text(text)

    assert secret not in scrubbed
    assert "***REDACTED***" in scrubbed


def test_quoted_secret_keeps_the_text_after_its_closing_quote() -> None:
    assert redact_text('password="my secret phrase" tail') == 'password="***REDACTED***" tail'


def test_plain_text_and_already_masked_values_are_unchanged() -> None:
    plain = "order filled at 101.5 for 3 shares"
    assert redact_text(plain) == plain
    masked = "password=***REDACTED*** keep"
    assert redact_text(masked) == masked


def test_account_id_in_free_text_is_masked_and_account_key_is_tokenized() -> None:
    # S3-2-1: the episode key must not carry the account digits.
    token = account_episode_token(ACCOUNT)

    assert ACCOUNT not in token
    assert token == account_episode_token(ACCOUNT)  # deterministic
    assert token != account_episode_token(OTHER_ACCOUNT)
    assert account_episode_token(None) == ""
    assert mask_account_text(f"holder {ACCOUNT} rejected", ACCOUNT) == "holder ***7890 rejected"
    assert mask_account_text("unknown account", "unknown") == "unknown account"


def test_incident_episode_key_and_error_never_carry_the_full_account_id() -> None:
    # S3-2-1 and S3-2-2: the episode key is tokenized, the error text is masked.
    record = create_incident(
        IncidentCategory.BROKER_ACCOUNT_STALE,
        account_id=ACCOUNT,
        strategy="vt35",
        market="equity",
        error=f"portfolio_high_water read failed for {ACCOUNT}: timeout",
    )

    assert ACCOUNT not in record.episode_key
    assert ACCOUNT not in (record.error or "")
    assert "***7890" in (record.error or "")
    repeat = create_incident(
        IncidentCategory.BROKER_ACCOUNT_STALE, account_id=ACCOUNT, strategy="vt35", market="equity"
    )
    assert repeat.episode_key == record.episode_key
    other = create_incident(
        IncidentCategory.BROKER_ACCOUNT_STALE, account_id=OTHER_ACCOUNT, strategy="vt35", market="equity"
    )
    assert other.episode_key != record.episode_key


def test_peak_sidecar_mismatch_message_does_not_name_either_account(tmp_path: Any) -> None:
    # S3-2-2 and S3-2-3: the fail-closed message and its log line are masked.
    sidecar = tmp_path / "paper_equity_peak.json"
    sidecar.write_text(
        json.dumps({"account_id": OTHER_ACCOUNT, "peak_equity": 100_000.0, "epoch": 0}),
        encoding="utf-8",
    )
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    service = RiskPeakService(paper=True, ledger=ledger, peak_state_path=sidecar)

    with pytest.raises(RiskStatePersistenceError) as raised:
        service.ensure_reconciled(ACCOUNT)

    message = str(raised.value)
    assert ACCOUNT not in message
    assert OTHER_ACCOUNT not in message
    assert "***7890" in message


def test_persisted_peak_query_error_masks_the_account(tmp_path: Any) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    service = RiskPeakService(paper=True, ledger=ledger, peak_state_path=tmp_path / "peak.json")

    class _BrokenConnection:
        def execute(self, *_args: object) -> None:
            raise RuntimeError("disk I/O error")

    ledger.connection = _BrokenConnection()  # type: ignore[assignment]
    with pytest.raises(RiskStatePersistenceError) as raised:
        service.query_persisted_peak(ACCOUNT)

    assert ACCOUNT not in str(raised.value)
    assert "***7890" in str(raised.value)


def test_email_scrub_error_removes_the_smtp_relay_host() -> None:
    # S3-2-6: a connect error names the relay host; it must not reach the journal.
    settings = SimpleNamespace(
        smtp_host="smtp.relay.example",
        to_addresses=("owner@example.org",),
        from_address="bot@example.org",
    )

    scrubbed = email_alerts._scrub_error("could not reach smtp.relay.example:587", settings, {})

    assert "smtp.relay.example" not in scrubbed
    assert "owner@example.org" not in scrubbed


def test_lastresort_handler_redacts_third_party_warnings() -> None:
    # S3-2-5: records no handler claims go to lastResort, which now redacts.
    record = logging.LogRecord(
        "thirdparty.lib",
        logging.WARNING,
        "f.py",
        1,
        "token: abc123456 and %s",
        ("Authorization: Bearer zzz.yyy",),
        None,
    )

    assert isinstance(logging.lastResort.formatter, RedactingFormatter)
    rendered = logging.lastResort.format(record)

    assert "abc123456" not in rendered
    assert "zzz.yyy" not in rendered


def test_formatter_failure_emits_a_placeholder_not_the_raw_arguments() -> None:
    # S3-2-9: a formatting error used to reach handleError, which prints the raw args.
    class _Unprintable:
        def __str__(self) -> str:
            raise ValueError("boom")

    record = logging.LogRecord(
        "parayabuck.test", logging.WARNING, "f.py", 1, "value %s password=hunter2hunter2", (_Unprintable(),), None
    )

    rendered = RedactingFormatter("%(message)s").format(record)

    assert rendered == "WARNING parayabuck.test: <log record could not be formatted>"
    assert "hunter2" not in rendered


# ---------------------------------------------------------------------------
# Alerts and journal: work done outside the locks
# ---------------------------------------------------------------------------


def test_suppressed_alert_is_logged_outside_the_alert_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    # C3-5-2: a stalled log sink must not hold the global alert lock.
    alerts.reset_for_tests()
    monkeypatch.setattr(alerts, "_throttle_seconds", 60.0)
    held_during_info: list[bool] = []

    def _info(*_args: object, **_kwargs: object) -> None:
        held_during_info.append(alerts._lock.locked())

    monkeypatch.setattr(alerts.log, "info", _info)
    try:
        alerts.emit("paper_incident", message="first", category="x", episode_key="ep-w13")
        alerts.emit("paper_incident", message="second", category="x", episode_key="ep-w13")
    finally:
        alerts.reset_for_tests()

    assert held_during_info == [False]


def test_configure_global_journal_joins_the_replaced_writer_outside_the_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # C3-5-6: the replaced default journal's writer join (up to 2 s) runs without _global_lock.
    lock_state_at_stop: list[bool] = []

    class _FakeJournal:
        def __init__(self, path: object, **_kwargs: object) -> None:
            self.path = path

        def stop(self, **_kwargs: object) -> None:
            lock_state_at_stop.append(journal_module._global_lock.locked())

    replaced = _FakeJournal(journal_module.DEFAULT_JOURNAL_PATH)
    monkeypatch.setattr(journal_module, "EventJournal", _FakeJournal)
    monkeypatch.setattr(journal_module, "_global_journal", replaced)
    monkeypatch.setattr(journal_module, "_global_is_configured", False)

    observability = SimpleNamespace(
        journal_path=str(tmp_path / "observability.sqlite3"),
        event_queue_size=16,
        event_retention_days=1,
        event_max_events=100,
    )
    installed = journal_module.configure_global_journal(observability)  # type: ignore[arg-type]

    assert lock_state_at_stop == [False]
    assert installed is journal_module._global_journal
    assert installed is not replaced


# ---------------------------------------------------------------------------
# Part B: stream supervisor
# ---------------------------------------------------------------------------


class _HangingStream:
    """A stream whose connect never answers (alpaca-py awaits the reply without a timeout)."""

    def __init__(self) -> None:
        self._should_run = True
        self.closed = 0

    async def _start_ws(self) -> None:
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed += 1


async def _no_sleep(_seconds: float) -> None:
    return None


def test_connect_that_never_answers_times_out_and_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # C3-4-1: the attempt is bounded, so the stream does not hang silently.
    monkeypatch.setattr(alpaca_stream, "emit_execution_alert", lambda *a, **k: None)
    stream = _HangingStream()
    supervise_sdk_stream(stream, label="stock data", sleep=_no_sleep, connect_timeout=0.05)

    with pytest.raises(TimeoutError):
        asyncio.run(stream._start_ws())

    assert stream.closed == 1


def test_stop_during_a_hung_connect_interrupts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # C3-4-1: a stop during a reconnect must not wait for the connect's full deadline.
    monkeypatch.setattr(alpaca_stream, "emit_execution_alert", lambda *a, **k: None)
    stream = _HangingStream()
    supervise_sdk_stream(stream, label="trade updates", sleep=_no_sleep, connect_timeout=60.0)

    async def _stop_soon() -> None:
        await asyncio.sleep(0.05)
        stream._should_run = False

    async def _run() -> None:
        stopper = asyncio.ensure_future(_stop_soon())
        try:
            await stream._start_ws()
        finally:
            await stopper

    started = time.monotonic()
    with pytest.raises(ConnectionError):
        asyncio.run(_run())

    assert time.monotonic() - started < 5.0


class _ConnectOnceStream:
    """Fails the first connect, then connects."""

    def __init__(self) -> None:
        self._should_run = True
        self.attempts = 0

    async def _start_ws(self) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionError("first connect refused")

    async def close(self) -> None:
        return None


def test_first_connect_after_failures_fires_the_reconnect_hook() -> None:
    # C3-4-4: updates may have been missed while the first connects were failing.
    calls: list[int] = []
    stream = _ConnectOnceStream()
    supervise_sdk_stream(
        stream, label="trade updates", sleep=_no_sleep, on_reconnect=lambda: calls.append(1), connect_timeout=1.0
    )

    with pytest.raises(ConnectionError):
        asyncio.run(stream._start_ws())
    asyncio.run(stream._start_ws())

    assert calls == [1]


def test_clean_first_connect_does_not_fire_the_reconnect_hook() -> None:
    calls: list[int] = []

    class _Clean:
        _should_run = True

        async def _start_ws(self) -> None:
            return None

        async def close(self) -> None:
            return None

    stream = _Clean()
    supervise_sdk_stream(stream, label="trade updates", sleep=_no_sleep, on_reconnect=lambda: calls.append(1))

    asyncio.run(stream._start_ws())

    assert calls == []


def test_bar_stream_run_clears_the_stale_sdk_loop_before_running() -> None:
    # C3-4-2: alpaca-py keeps the previous run's loop; a racing stop must wait for the new one.
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    sdk = Mock()
    sdk._loop = object()
    seen: list[object] = []
    sdk.run.side_effect = lambda: seen.append(sdk._loop)
    stream.stock = sdk

    stream.run_stock()

    assert seen == [None]


def test_trading_stream_run_clears_the_stale_sdk_loop_before_running() -> None:
    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    sdk = Mock()
    sdk._loop = object()
    seen: list[object] = []
    sdk.run.side_effect = lambda: seen.append(sdk._loop)
    stream._stream = sdk

    stream.run()

    assert seen == [None]


def test_trading_stream_stop_is_bounded_when_the_sdk_stop_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    # C3-4-3: the SDK's stop() waits without a timeout; the wrapper must not.
    monkeypatch.setattr(alpaca_updates, "_STOP_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()
    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    sdk = Mock()
    sdk._loop = object()
    sdk.stop.side_effect = lambda: release.wait(10.0)
    stream._stream = sdk

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            stream.stop()
    finally:
        release.set()

    assert time.monotonic() - started < 5.0


def test_trading_stream_stop_propagates_an_sdk_error() -> None:
    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    sdk = Mock()
    sdk._loop = object()
    sdk.stop.side_effect = ValueError("loop closed")
    stream._stream = sdk

    with pytest.raises(ValueError, match="loop closed"):
        stream.stop()
