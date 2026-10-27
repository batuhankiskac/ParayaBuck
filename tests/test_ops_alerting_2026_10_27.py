"""Ops alerting (2026-10-27): e-mail kind coverage, send retry, dead-man ping, off-host backup copy.

Offline only: every SMTP server is a fake class, every HTTP call is an injected
fake, every subprocess is an injected fake. No socket, no network, no credential.
"""

from __future__ import annotations

import smtplib
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from parayabuck import backup
from parayabuck.config import EmailAlertConfig
from parayabuck.monitoring import deadman, email_alerts
from parayabuck.monitoring.email_alerts import SMTP_PASSWORD_ENV, SMTP_USERNAME_ENV, EmailAlertSink
from parayabuck.monitoring.events import ALERT_KINDS, EMAIL_ALERT_KINDS

_ENV = {SMTP_USERNAME_ENV: "bot@example.com", SMTP_PASSWORD_ENV: "app-password-123"}


def _settings() -> EmailAlertConfig:
    return EmailAlertConfig(
        enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        security="starttls",
        from_address="bot@example.com",
        to_addresses=["owner@example.com"],
        min_interval_minutes=0,
        max_per_hour=100,
    )


class _CountingSMTP:
    """Counts send attempts; ``failures`` leading attempts raise, the rest succeed."""

    failures_before_success: int = 0
    raise_on_login: Exception | None = None
    attempts: int = 0
    sent: int = 0

    def __init__(self, host: str, port: int, timeout: float = 0.0, context: Any = None) -> None:
        pass

    def ehlo(self) -> None:
        pass

    def starttls(self, context: Any = None) -> None:
        pass

    def login(self, username: str, password: str) -> None:
        if type(self).raise_on_login is not None:
            raise type(self).raise_on_login

    def send_message(self, msg: Any) -> None:
        type(self).attempts += 1
        if type(self).attempts <= type(self).failures_before_success:
            raise ConnectionError("transient SMTP failure")
        type(self).sent += 1

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_smtp_fake() -> Any:
    _CountingSMTP.failures_before_success = 0
    _CountingSMTP.raise_on_login = None
    _CountingSMTP.attempts = 0
    _CountingSMTP.sent = 0
    yield


def _sink(sleeps: list[float], **kwargs: Any) -> EmailAlertSink:
    return EmailAlertSink(
        _settings(),
        mode="paper",
        env=_ENV,
        smtp_factory=_CountingSMTP,
        smtp_ssl_factory=_CountingSMTP,
        sleep=sleeps.append,
        start_worker=False,
        **kwargs,
    )


# --- Task 1: alert kinds forwarded by the e-mail channel --------------------


@pytest.mark.parametrize("kind", ["market_data_degraded", "data_provider_unavailable", "backup_failed"])
def test_new_operator_kinds_are_forwarded_by_email(kind: str) -> None:
    assert kind in ALERT_KINDS
    assert kind in EMAIL_ALERT_KINDS
    assert _sink([]).accepts(kind)


def test_reason_strings_are_not_alert_kinds_and_stay_unforwarded() -> None:
    # protection_failure / ownership_drift / reconciliation_failed are circuit-breaker
    # reasons; their alerts travel as reconciliation_failure / circuit_breaker.
    for reason_only in ("reconciliation_failed", "ownership_drift", "protection_failure"):
        assert reason_only not in ALERT_KINDS
    assert {"reconciliation_failure", "circuit_breaker"} <= EMAIL_ALERT_KINDS


def test_routine_kinds_are_still_not_forwarded() -> None:
    sink = _sink([])
    assert not sink.accepts("runtime_started")
    assert not sink.accepts("alert_delivery_failed")


# --- Task 1: retry with backoff ---------------------------------------------


def test_persistent_failure_is_retried_four_times_then_counted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    journaled: list[str] = []
    monkeypatch.setattr(email_alerts, "_record_delivery_failure", lambda kind, reason, error: journaled.append(kind))
    _CountingSMTP.failures_before_success = 99
    sleeps: list[float] = []
    sink = _sink(sleeps)
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:x") is True
    assert sink.process_one(timeout=0.1) is True
    assert _CountingSMTP.attempts == 4
    # Bounded in-memory retry window: 65 s of waits in total, no outbox.
    assert sleeps == [5.0, 15.0, 45.0]
    assert sink.stats() == {"sent": 0, "failed": 1, "dropped": 0}
    assert journaled == ["circuit_breaker"]


def test_transient_failure_recovers_on_a_later_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_alerts, "_record_delivery_failure", lambda *args: pytest.fail("no journal expected"))
    _CountingSMTP.failures_before_success = 1
    sleeps: list[float] = []
    sink = _sink(sleeps)
    sink.notify("backup_failed", "m", {}, "backup_failed:x")
    assert sink.process_one(timeout=0.1) is True
    assert _CountingSMTP.attempts == 2
    assert sleeps == [5.0]
    assert sink.stats() == {"sent": 1, "failed": 0, "dropped": 0}


def test_rejected_login_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_alerts, "_record_delivery_failure", lambda *args: None)
    _CountingSMTP.raise_on_login = smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials")
    sleeps: list[float] = []
    sink = _sink(sleeps)
    sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:y")
    assert sink.process_one(timeout=0.1) is True
    assert sleeps == []
    assert sink.stats()["failed"] == 1


# --- Task 2: dead-man switch -------------------------------------------------


class _Http:
    """Records (url, timeout) pairs and answers with a scripted status or error."""

    def __init__(self, readyz: int | Exception, ping_error: Exception | None = None) -> None:
        self.readyz = readyz
        self.ping_error = ping_error
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> int:
        self.calls.append((url, timeout))
        if url.endswith("/readyz"):
            if isinstance(self.readyz, Exception):
                raise self.readyz
            return self.readyz
        if self.ping_error is not None:
            raise self.ping_error
        return 200


_HC = "https://hc-ping.example/0000-test-uuid"


def test_deadman_fails_loudly_without_a_url(capsys: pytest.CaptureFixture[str]) -> None:
    """DD5-3: no heartbeat URL is a configuration error (non-zero exit), never a silent no-op."""
    http = _Http(200)
    assert deadman.run({}, http=http, credentials_dir="") == 1
    assert http.calls == []
    assert "no heartbeat URL" in capsys.readouterr().err


def test_deadman_pings_success_when_readiness_is_200() -> None:
    http = _Http(200)
    assert deadman.run({deadman.HEALTHCHECK_URL_ENV: _HC}, http=http, credentials_dir="") == 0
    assert [url for url, _ in http.calls] == [deadman.DEFAULT_READYZ_URL, _HC]
    assert all(timeout == deadman.REQUEST_TIMEOUT_SECONDS for _, timeout in http.calls)


def test_deadman_pings_fail_when_readiness_is_503() -> None:
    http = _Http(503)
    assert deadman.run({deadman.HEALTHCHECK_URL_ENV: _HC + "/"}, http=http, credentials_dir="") == 0
    assert http.calls[-1][0] == _HC + "/fail"


def test_deadman_pings_fail_when_the_console_is_unreachable() -> None:
    http = _Http(ConnectionRefusedError("refused"))
    env = {deadman.HEALTHCHECK_URL_ENV: _HC, deadman.READYZ_URL_ENV: "http://127.0.0.1:9999/readyz"}
    assert deadman.run(env, http=http, credentials_dir="") == 0
    assert http.calls[0] == ("http://127.0.0.1:9999/readyz", deadman.REQUEST_TIMEOUT_SECONDS)
    assert http.calls[-1][0] == _HC + "/fail"


def test_deadman_exits_non_zero_when_the_heartbeat_itself_is_unreachable() -> None:
    http = _Http(200, ping_error=OSError("no route"))
    assert deadman.run({deadman.HEALTHCHECK_URL_ENV: _HC}, http=http, credentials_dir="") == 1


def test_deadman_credential_file_takes_precedence_over_the_environment(tmp_path: Path) -> None:
    (tmp_path / deadman.HEALTHCHECK_URL_ENV).write_text(f"{_HC}\n", encoding="utf-8")
    http = _Http(200)
    env = {deadman.HEALTHCHECK_URL_ENV: "https://other.example/ignored"}
    assert deadman.run(env, http=http, credentials_dir=str(tmp_path)) == 0
    assert http.calls[-1][0] == _HC


def test_deadman_rejects_a_non_http_url_without_sending_anything() -> None:
    http = _Http(200)
    assert deadman.run({deadman.HEALTHCHECK_URL_ENV: "file:///etc/passwd"}, http=http, credentials_dir="") == 1
    assert http.calls == []


# --- Task 3: off-host backup copy ---------------------------------------------


class _Completed:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


def test_offhost_copy_is_skipped_when_unset(tmp_path: Path) -> None:
    calls: list[Any] = []
    assert backup.copy_offhost(tmp_path / "s.sqlite3", env={}, runner=lambda *a, **k: calls.append(a)) is False
    assert calls == []


def test_offhost_copy_runs_the_formatted_command_as_an_argv_list(tmp_path: Path) -> None:
    snapshot = tmp_path / "with space" / "parayabuck-1.sqlite3"
    seen: dict[str, Any] = {}

    def runner(argv: list[str], **kwargs: Any) -> _Completed:
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Completed(0)

    env = {backup.OFFHOST_CMD_ENV: "gsutil cp {path} gs://bucket/"}
    assert backup.copy_offhost(snapshot, env=env, runner=runner, timeout=60.0) is True
    assert seen["argv"] == ["gsutil", "cp", str(snapshot), "gs://bucket/"]
    assert seen["kwargs"]["timeout"] == 60.0
    assert seen["kwargs"].get("shell", False) is False


def test_offhost_copy_non_zero_exit_raises_with_stderr(tmp_path: Path) -> None:
    env = {backup.OFFHOST_CMD_ENV: "gsutil cp {path} gs://bucket/"}
    with pytest.raises(backup.OffhostCopyError, match="exited 1: AccessDeniedException"):
        backup.copy_offhost(
            tmp_path / "s.sqlite3",
            env=env,
            runner=lambda argv, **kwargs: _Completed(1, "AccessDeniedException: 403"),
        )


def test_offhost_copy_timeout_and_malformed_template_are_failures(tmp_path: Path) -> None:
    def hangs(argv: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(backup.OffhostCopyError, match="timed out"):
        backup.copy_offhost(
            tmp_path / "s.sqlite3", env={backup.OFFHOST_CMD_ENV: "cp {path} /x"}, runner=hangs, timeout=5.0
        )
    with pytest.raises(backup.OffhostCopyError, match="invalid placeholder"):
        backup.copy_offhost(tmp_path / "s.sqlite3", env={backup.OFFHOST_CMD_ENV: "cp {dest} /x"}, runner=hangs)
    with pytest.raises(backup.OffhostCopyError, match="not a valid command"):
        backup.copy_offhost(tmp_path / "s.sqlite3", env={backup.OFFHOST_CMD_ENV: "cp 'unterminated {path}"})


def _make_source_db(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY, note TEXT)")
        connection.execute("INSERT INTO decisions (note) VALUES ('x')")
    return path


def test_backup_records_backup_failed_when_the_offhost_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source_db(tmp_path / "decisions.sqlite3")
    alerts_seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "parayabuck.monitoring.alerts.emit",
        lambda kind, *, message, **fields: alerts_seen.append({"kind": kind, **fields}),
    )
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda argv, **kwargs: _Completed(2, "network down"),
    )
    monkeypatch.setenv(backup.OFFHOST_CMD_ENV, "gsutil cp {path} gs://bucket/")
    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7)
    # The verified local snapshot is kept; the failure is an alert, not a lost backup.
    assert destination.is_file() and backup.verify_backup(destination)
    assert alerts_seen == [{"kind": "backup_failed", "reason": "offhost_copy_failed", "backup_path": str(destination)}]


def test_backup_runs_no_copy_and_emits_nothing_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _make_source_db(tmp_path / "decisions.sqlite3")
    monkeypatch.delenv(backup.OFFHOST_CMD_ENV, raising=False)

    def no_subprocess(*args: Any, **kwargs: Any) -> None:
        pytest.fail("subprocess must not run when the off-host copy is unset")

    monkeypatch.setattr(backup.subprocess, "run", no_subprocess)
    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7)
    assert backup.verify_backup(destination)
