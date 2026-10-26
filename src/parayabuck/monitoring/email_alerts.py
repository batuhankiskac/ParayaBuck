"""Opt-in e-mail delivery of operator alerts (``alerting.email``).

The local sink (``monitoring.alerts.emit``: log + event journal) stays the
source of truth. When the owner enables ``alerting.email``, the subset of
kinds in ``monitoring.events.EMAIL_ALERT_KINDS`` (plus ``daily_summary`` when
``alerting.email.daily_summary`` is true) is also e-mailed:

- SMTP over TLS only, stdlib only: STARTTLS (usually port 587) or implicit
  TLS ``ssl`` (usually port 465), certificate-verified with the system trust
  store.
- The SMTP username and password come ONLY from the environment
  (``PARAYABUCK_SMTP_USERNAME`` / ``PARAYABUCK_SMTP_PASSWORD``); they are
  never logged, journaled or put in an e-mail.
- Delivery is asynchronous: ``notify`` only enqueues (bounded, non-blocking)
  and one daemon worker talks to the SMTP server with a socket timeout, so a
  slow, failing or unreachable mail server can never block or crash trading.
- Repeats are coalesced per incident with the alert throttle key (kind +
  correlation + reason + state) over ``min_interval_minutes``, and at most
  ``max_per_hour`` mails leave the host per hour.
- Every failed or dropped delivery is recorded in the local event journal as
  ``alert_delivery_failed`` (never e-mailed itself).

``validate_email_setup`` checks the configuration and the credential
environment without any network access (``parayabuck doctor``);
``send_test_email`` is the explicit, operator-invoked test send
(``parayabuck ops test-email --confirm``).
"""

from __future__ import annotations

import logging
import os
import queue
import smtplib
import socket
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from parayabuck.monitoring.events import EMAIL_ALERT_KINDS

log = logging.getLogger("parayabuck.monitoring.email_alerts")

SMTP_USERNAME_ENV = "PARAYABUCK_SMTP_USERNAME"
SMTP_PASSWORD_ENV = "PARAYABUCK_SMTP_PASSWORD"

_QUEUE_CAPACITY = 64
_FIELD_VALUE_MAX = 400
_THROTTLE_KEYS_MAX = 4096
_DELIVERY_FAILED_KIND = "alert_delivery_failed"
# One mail gets three attempts. Each retry waits longer than the last, so a
# transient SMTP hiccup (timeout, 4xx, dropped connection) clears; a failure
# that survives the last attempt is counted once and journaled, never raised.
_DELIVERY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 4.0)

SmtpFactory = Callable[..., Any]


@dataclass(frozen=True)
class _Outgoing:
    kind: str
    message: str
    fields: dict[str, object]
    created_at: datetime
    test: bool = False


def _smtp_credentials(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    source = os.environ if env is None else env
    return str(source.get(SMTP_USERNAME_ENV, "") or ""), str(source.get(SMTP_PASSWORD_ENV, "") or "")


def validate_email_setup(settings: Any, env: Mapping[str, str] | None = None) -> list[str]:
    """Problems with an ENABLED e-mail configuration; never touches the network.

    The configuration object already validated host/port/addresses on load;
    this adds the credential environment: both variables set (an
    authenticated TLS submission), or both empty (an unauthenticated relay).
    """
    problems: list[str] = []
    if not getattr(settings, "enabled", False):
        return problems
    if not getattr(settings, "smtp_host", ""):
        problems.append("alerting.email.smtp_host is empty")
    if not getattr(settings, "to_addresses", ()):
        problems.append("alerting.email.to_addresses is empty")
    username, password = _smtp_credentials(env)
    if bool(username) != bool(password):
        problems.append(f"set both {SMTP_USERNAME_ENV} and {SMTP_PASSWORD_ENV} (only one is set)")
    elif not username:
        problems.append(f"{SMTP_USERNAME_ENV} / {SMTP_PASSWORD_ENV} are not set (SMTP login required)")
    if any(char in username + password for char in "\r\n"):
        problems.append("SMTP credentials must not contain newlines")
    return problems


def _scrub(text: str, secrets_: tuple[str, ...]) -> str:
    scrubbed = text
    for value in secrets_:
        if value and len(value) >= 3:
            scrubbed = scrubbed.replace(value, "***")
    return scrubbed


def _format_fields(fields: Mapping[str, object]) -> list[str]:
    from parayabuck.monitoring.operational_event import redact_fields

    lines: list[str] = []
    for key, value in sorted(redact_fields(dict(fields)).items()):
        if value is None or value == "":
            continue
        text = str(value)
        if len(text) > _FIELD_VALUE_MAX:
            text = text[:_FIELD_VALUE_MAX] + "..."
        lines.append(f"  {key}: {text}")
    return lines


def build_message(
    settings: Any,
    *,
    kind: str,
    message: str,
    fields: Mapping[str, object],
    mode: str = "",
    created_at: datetime | None = None,
    hostname: str | None = None,
) -> EmailMessage:
    """One plain-text alert e-mail. Never contains credentials."""
    when = (created_at or datetime.now(UTC)).astimezone(UTC)
    host = hostname if hostname is not None else socket.gethostname()
    label = f"ParayaBuck {mode.upper()}".strip() if mode else "ParayaBuck"
    headline = " ".join(str(message).split())
    if len(headline) > 120:
        headline = headline[:117] + "..."
    msg = EmailMessage()
    msg["Subject"] = f"[{label}] {kind}: {headline}"
    msg["From"] = settings.from_address
    msg["To"] = ", ".join(settings.to_addresses)
    msg["Date"] = formatdate(when.timestamp(), usegmt=True)
    msg["Message-ID"] = make_msgid(domain="parayabuck.local")
    body = [
        f"{label} alert",
        "",
        f"Kind:    {kind}",
        f"Time:    {when.isoformat()}",
        f"Host:    {host}",
        "",
        str(message),
    ]
    detail = _format_fields(fields)
    if detail:
        body += ["", "Details:", *detail]
    body += [
        "",
        "Read the full journal on the VM: parayabuck ops events --since 24h",
        "This message was sent automatically by the ParayaBuck engine.",
    ]
    msg.set_content("\n".join(body) + "\n")
    return msg


def send_email_message(
    settings: Any,
    msg: EmailMessage,
    *,
    env: Mapping[str, str] | None = None,
    smtp_factory: SmtpFactory | None = None,
    smtp_ssl_factory: SmtpFactory | None = None,
) -> None:
    """Deliver one message synchronously over TLS. Raises on any failure.

    The error text of a raised exception is scrubbed of the credentials by
    the callers that record it; this function never logs them.
    """
    username, password = _smtp_credentials(env)
    context = ssl.create_default_context()
    timeout = float(settings.timeout_seconds)
    if settings.security == "ssl":
        factory = smtp_ssl_factory or smtplib.SMTP_SSL
        server = factory(settings.smtp_host, int(settings.smtp_port), timeout=timeout, context=context)
    else:
        factory = smtp_factory or smtplib.SMTP
        server = factory(settings.smtp_host, int(settings.smtp_port), timeout=timeout)
    try:
        if settings.security != "ssl":
            server.ehlo()
            # STARTTLS is mandatory: never send credentials or alerts in clear.
            server.starttls(context=context)
            server.ehlo()
        if username:
            server.login(username, password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass


class EmailAlertSink:
    """Bounded queue + one daemon worker; ``notify`` never blocks or raises."""

    def __init__(
        self,
        settings: Any,
        *,
        mode: str = "",
        env: Mapping[str, str] | None = None,
        smtp_factory: SmtpFactory | None = None,
        smtp_ssl_factory: SmtpFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        start_worker: bool = True,
    ) -> None:
        self.settings = settings
        self.mode = mode
        self._env = env
        self._smtp_factory = smtp_factory
        self._smtp_ssl_factory = smtp_ssl_factory
        self._clock = clock
        self._sleep = sleep
        self._queue: queue.Queue[_Outgoing | None] = queue.Queue(maxsize=_QUEUE_CAPACITY)
        self._lock = threading.Lock()
        self._last_sent: dict[str, float] = {}
        self._sent_times: deque[float] = deque()
        self.sent = 0
        self.failed = 0
        self.dropped = 0
        self._closed = False
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(target=self._run, name="pb-email-alerts", daemon=True)
            self._worker.start()

    def accepts(self, kind: str) -> bool:
        if kind == _DELIVERY_FAILED_KIND:
            return False
        if kind == "daily_summary":
            return bool(getattr(self.settings, "daily_summary", False))
        return kind in EMAIL_ALERT_KINDS

    def notify(self, kind: str, message: str, fields: Mapping[str, object], throttle_key: str) -> bool:
        """Enqueue one alert e-mail; False when filtered, coalesced or dropped."""
        if self._closed or not self.accepts(kind):
            return False
        now = self._clock()
        window = max(0.0, float(self.settings.min_interval_minutes)) * 60.0
        with self._lock:
            last = self._last_sent.get(throttle_key)
            if last is not None and window > 0 and now - last < window:
                return False
            while self._sent_times and now - self._sent_times[0] >= 3600.0:
                self._sent_times.popleft()
            if len(self._sent_times) >= int(self.settings.max_per_hour):
                self.dropped += 1
                rate_limited = True
            else:
                rate_limited = False
                self._last_sent[throttle_key] = now
                self._sent_times.append(now)
                if len(self._last_sent) > _THROTTLE_KEYS_MAX:
                    for key in [k for k, t in self._last_sent.items() if now - t >= window]:
                        del self._last_sent[key]
        if rate_limited:
            _record_delivery_failure(kind, "rate_limited", f"more than {self.settings.max_per_hour} e-mails this hour")
            return False
        item = _Outgoing(kind, str(message), dict(fields), datetime.now(UTC))
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self.dropped += 1
            _record_delivery_failure(kind, "queue_full", "e-mail queue is full")
            return False
        return True

    def _deliver(self, item: _Outgoing) -> None:
        msg = build_message(
            self.settings,
            kind=item.kind,
            message=item.message,
            fields=item.fields,
            mode=self.mode,
            created_at=item.created_at,
        )
        send_email_message(
            self.settings,
            msg,
            env=self._env,
            smtp_factory=self._smtp_factory,
            smtp_ssl_factory=self._smtp_ssl_factory,
        )

    def _deliver_with_retry(self, item: _Outgoing) -> None:
        """Up to ``_DELIVERY_ATTEMPTS`` sends with backoff; re-raises the last error.

        A rejected login is not retried: repeating a bad password only risks
        locking the mail account, and it can never succeed on its own.
        """
        for attempt in range(1, _DELIVERY_ATTEMPTS + 1):
            try:
                self._deliver(item)
                return
            except smtplib.SMTPAuthenticationError:
                raise
            except Exception:
                if attempt == _DELIVERY_ATTEMPTS:
                    raise
                delay = _RETRY_BACKOFF_SECONDS[attempt - 1]
                log.info("Alert e-mail for kind=%s attempt %d failed; retrying in %.0fs", item.kind, attempt, delay)
                self._sleep(delay)

    def process_one(self, timeout: float | None = None) -> bool:
        """Deliver the next queued e-mail (worker body; tests drive it)."""
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return False
        try:
            if item is None:
                return False
            try:
                self._deliver_with_retry(item)
                with self._lock:
                    self.sent += 1
            except Exception as exc:
                with self._lock:
                    self.failed += 1
                username, password = _smtp_credentials(self._env)
                detail = _scrub(f"{type(exc).__name__}: {exc}", (password, username))
                log.warning("Alert e-mail for kind=%s not delivered: %s", item.kind, detail)
                _record_delivery_failure(item.kind, type(exc).__name__, detail)
        finally:
            self._queue.task_done()
        return True

    def _run(self) -> None:
        while not self._closed:
            try:
                self.process_one(timeout=1.0)
            except Exception:  # pragma: no cover - the worker must never die
                log.exception("E-mail alert worker iteration failed")

    def flush(self, timeout: float = 30.0) -> bool:
        """Wait until the queue is drained (CLI/test helper)."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def close(self) -> None:
        self._closed = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"sent": self.sent, "failed": self.failed, "dropped": self.dropped}


def _record_delivery_failure(kind: str, reason: str, error: str) -> None:
    """Journal one failed/dropped e-mail (local only; never e-mailed)."""
    try:
        from parayabuck.monitoring import alerts

        alerts.emit(
            _DELIVERY_FAILED_KIND,
            message=f"Alert e-mail for {kind} was not delivered ({reason})",
            channel="email",
            alert_kind=kind,
            reason=reason,
            error=error,
        )
    except Exception:  # pragma: no cover - never raise from a delivery path
        log.warning("Could not journal the e-mail delivery failure", exc_info=True)


_sink_lock = threading.Lock()
_sink: EmailAlertSink | None = None


def configure_email_alerts(settings: Any | None, *, mode: str = "") -> EmailAlertSink | None:
    """Install (enabled) or remove (disabled/None) the process e-mail sink.

    Idempotent for an unchanged configuration; never raises.
    """
    global _sink
    try:
        with _sink_lock:
            current = _sink
            if settings is None or not getattr(settings, "enabled", False):
                _sink = None
                if current is not None:
                    current.close()
                return None
            if current is not None and current.settings == settings and current.mode == mode:
                return current
            _sink = EmailAlertSink(settings, mode=mode)
            if current is not None:
                current.close()
            problems = validate_email_setup(settings)
            if problems:
                log.warning("E-mail alerts are enabled but not deliverable yet: %s", "; ".join(problems))
            else:
                log.info(
                    "E-mail alerts enabled: %d recipient(s) via %s", len(settings.to_addresses), settings.smtp_host
                )
            return _sink
    except Exception:  # pragma: no cover - never raise
        log.exception("E-mail alert configuration ignored")
        return None


def install_sink_for_tests(sink: EmailAlertSink | None) -> None:
    """Replace the process sink (test helper only)."""
    global _sink
    with _sink_lock:
        previous = _sink
        _sink = sink
    if previous is not None and previous is not sink:
        previous.close()


def current_sink() -> EmailAlertSink | None:
    return _sink


def notify(kind: str, message: str, fields: Mapping[str, object], throttle_key: str) -> None:
    """Forward one accepted alert to the e-mail sink, if enabled. Never raises."""
    sink = _sink
    if sink is None:
        return
    try:
        sink.notify(kind, message, fields, throttle_key)
    except Exception:  # pragma: no cover - never raise into the alert path
        log.warning("E-mail alert enqueue failed for kind=%s", kind, exc_info=True)


def send_test_email(settings: Any, *, mode: str = "", env: Mapping[str, str] | None = None) -> None:
    """Send one test e-mail synchronously (explicit operator command only)."""
    problems = validate_email_setup(settings, env)
    if not getattr(settings, "enabled", False):
        raise ValueError("alerting.email.enabled is false")
    if problems:
        raise ValueError("; ".join(problems))
    msg = build_message(
        settings,
        kind="test",
        message="Test e-mail: ParayaBuck alert delivery works.",
        fields={},
        mode=mode,
    )
    try:
        send_email_message(settings, msg, env=env)
    except Exception as exc:
        username, password = _smtp_credentials(env)
        raise RuntimeError(_scrub(f"{type(exc).__name__}: {exc}", (password, username))) from None


def reset_for_tests() -> None:
    install_sink_for_tests(None)


__all__ = [
    "SMTP_PASSWORD_ENV",
    "SMTP_USERNAME_ENV",
    "EmailAlertSink",
    "build_message",
    "configure_email_alerts",
    "current_sink",
    "install_sink_for_tests",
    "notify",
    "reset_for_tests",
    "send_email_message",
    "send_test_email",
    "validate_email_setup",
]
