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
  episode or correlation + reason + state) over ``min_interval_minutes``.
- At most ``max_per_hour`` mails leave the host per hour. Non-critical kinds
  may use only ``max_per_hour`` minus a reserve (a quarter of the cap), so the
  critical kinds in ``monitoring.events.CRITICAL_EMAIL_KINDS`` (and any
  CRITICAL-severity incident) always find room for a halt mail.
- A failed send is retried in memory, ``_DELIVERY_ATTEMPTS`` times with
  growing waits (about one minute in total). There is no on-disk outbox: a
  mail that still fails is journaled and is not sent again, and mails queued
  when the process stops are lost.
- Every failed or dropped delivery is recorded in the local event journal as
  ``alert_delivery_failed`` (never e-mailed itself), keyed by the failed kind
  and the reason. ``delivery_status`` exposes the sent, failed and dropped
  counters and the age of the last successful send to the engine snapshot.

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

from parayabuck.monitoring.events import CRITICAL_EMAIL_KINDS, EMAIL_ALERT_KINDS

log = logging.getLogger("parayabuck.monitoring.email_alerts")

SMTP_USERNAME_ENV = "PARAYABUCK_SMTP_USERNAME"
SMTP_PASSWORD_ENV = "PARAYABUCK_SMTP_PASSWORD"

_QUEUE_CAPACITY = 64
_FIELD_VALUE_MAX = 400
_SUBJECT_HEADLINE_MAX = 200
_THROTTLE_KEYS_MAX = 4096
_DELIVERY_FAILED_KIND = "alert_delivery_failed"
# One mail gets four attempts. Each retry waits longer than the last, so a
# transient SMTP outage of a minute or so clears; the waits total 65 s, so one
# mail holds the single worker for about 65 s plus the socket timeouts at most.
# A failure that survives the last attempt is counted once and journaled, never
# raised, and never re-sent (there is no on-disk outbox).
_DELIVERY_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0, 45.0)

SmtpFactory = Callable[..., Any]


@dataclass(frozen=True)
class _Outgoing:
    kind: str
    message: str
    fields: dict[str, object]
    created_at: datetime
    test: bool = False
    throttle_key: str = ""


def _smtp_credentials(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """SMTP login: a systemd credential file (``$CREDENTIALS_DIRECTORY``) first, then the env var."""
    from parayabuck.config import credential_from_directory

    source = os.environ if env is None else env

    def _one(name: str) -> str:
        return credential_from_directory(name, source) or str(source.get(name, "") or "")

    return _one(SMTP_USERNAME_ENV), _one(SMTP_PASSWORD_ENV)


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


def _scrub_error(text: str, settings: Any, env: Mapping[str, str] | None) -> str:
    """An SMTP error text without the login or the routing addresses.

    A refusal names the recipients and the sender (``SMTPRecipientsRefused``), and
    that text reaches the journal and the diagnostic bundle, so the addresses are
    scrubbed with the credentials.
    """
    username, password = _smtp_credentials(env)
    addresses = [str(address) for address in getattr(settings, "to_addresses", ()) or ()]
    addresses.append(str(getattr(settings, "from_address", "") or ""))
    return _scrub(text, (password, username, *addresses))


def _render_value(value: object) -> str:
    """Plain text for a field value: lists and dicts as ``a, b`` / ``k=v``, not Python reprs."""
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={_render_value(item)}" for key, item in sorted(value.items(), key=lambda p: str(p[0])))
    if isinstance(value, list | tuple | set | frozenset):
        return ", ".join(_render_value(item) for item in value)
    return str(value)


def _format_fields(fields: Mapping[str, object]) -> list[str]:
    from parayabuck.monitoring.operational_event import redact_fields

    lines: list[str] = []
    # redact_fields walks nested values too, so the rendering below sees the masked copy.
    for key, value in sorted(redact_fields(dict(fields)).items()):
        if value is None or value == "":
            continue
        text = _render_value(value)
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
    # The body carries the full message; the subject keeps the status summary's
    # leading part (a 120-character cut lost the daily summary's status fields).
    if len(headline) > _SUBJECT_HEADLINE_MAX:
        headline = headline[: _SUBJECT_HEADLINE_MAX - 3] + "..."
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


def _is_critical(kind: str, fields: Mapping[str, object]) -> bool:
    """A kind the hourly cap must not drop for a non-critical mail (see CRITICAL_EMAIL_KINDS)."""
    if kind in CRITICAL_EMAIL_KINDS:
        return True
    return str(fields.get("severity", "") or "").strip().lower() == "critical"


def _critical_reserve(max_per_hour: int) -> int:
    """Hourly slots only critical mails may use: a quarter of the cap (0 for a cap below four)."""
    return max(0, max_per_hour // 4)


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
        # Throttle clock per key, started only by a successful send (see process_one).
        self._last_sent: dict[str, float] = {}
        # Keys queued but not yet sent, with the time they were queued: a repeat
        # of one of them inside the window is coalesced, not queued again.
        self._pending: dict[str, float] = {}
        self._sent_times: deque[float] = deque()
        self.sent = 0
        self.failed = 0
        self.dropped = 0
        # Clock reading of the last successful send (None until the first one).
        self._last_success_at: float | None = None
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
        item = _Outgoing(kind, str(message), dict(fields), datetime.now(UTC), throttle_key=throttle_key)
        # The enqueue happens under the same lock as close(): an item either lands
        # before the close marker (and is delivered) or is refused here. Nothing
        # can be stranded in the queue after the worker has exited.
        with self._lock:
            if self._closed:
                return False
            last = self._last_sent.get(throttle_key)
            if last is not None and window > 0 and now - last < window:
                return False
            queued_at = self._pending.get(throttle_key)
            if queued_at is not None and window > 0 and now - queued_at < window:
                return False
            while self._sent_times and now - self._sent_times[0] >= 3600.0:
                self._sent_times.popleft()
            # Critical kinds may use the whole hourly cap; the others stop short of
            # the reserve, so a storm of repeats cannot starve a drawdown_halt.
            limit = int(self.settings.max_per_hour)
            if not _is_critical(kind, fields):
                limit -= _critical_reserve(limit)
            queue_full = False
            if len(self._sent_times) >= limit:
                self.dropped += 1
                rate_limited = True
            else:
                rate_limited = False
                self._sent_times.append(now)
                self._pending[throttle_key] = now
                try:
                    self._queue.put_nowait(item)
                except queue.Full:
                    self.dropped += 1
                    self._pending.pop(throttle_key, None)
                    queue_full = True
        if rate_limited:
            _record_delivery_failure(kind, "rate_limited", f"more than {limit} e-mails this hour")
            return False
        if queue_full:
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
                    # The throttle window starts at a successful send. A failed
                    # delivery leaves the next repeat free to be queued and retried.
                    now = self._clock()
                    self._last_sent[item.throttle_key] = now
                    self._last_success_at = now
                    window = max(0.0, float(self.settings.min_interval_minutes)) * 60.0
                    if len(self._last_sent) > _THROTTLE_KEYS_MAX:
                        for key in [k for k, t in self._last_sent.items() if now - t >= window]:
                            del self._last_sent[key]
            except Exception as exc:
                with self._lock:
                    self.failed += 1
                detail = _scrub_error(f"{type(exc).__name__}: {exc}", self.settings, self._env)
                log.warning("Alert e-mail for kind=%s not delivered: %s", item.kind, detail)
                _record_delivery_failure(item.kind, type(exc).__name__, detail)
        finally:
            if item is not None:
                with self._lock:
                    self._pending.pop(item.throttle_key, None)
            self._queue.task_done()
        return True

    def _run(self) -> None:
        # close() only stops intake: alerts already queued are still delivered
        while not (self._closed and self._queue.empty()):
            try:
                self.process_one(timeout=1.0)
            except Exception:  # pragma: no cover - the worker must never die
                log.exception("E-mail alert worker iteration failed")

    def close(self) -> None:
        with self._lock:
            self._closed = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"sent": self.sent, "failed": self.failed, "dropped": self.dropped}

    def delivery_status(self) -> dict[str, object]:
        """Counters and freshness for the engine snapshot (``alert_stats()["email"]``).

        ``last_success_age_seconds`` is None until the first successful send, so
        a silent channel is visible as a growing age, not a missing field.
        """
        with self._lock:
            last = self._last_success_at
            age = None if last is None else round(max(0.0, self._clock() - last), 1)
            return {
                "sent": self.sent,
                "failed": self.failed,
                "dropped": self.dropped,
                "queued": self._queue.qsize(),
                "last_success_age_seconds": age,
            }


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
                # The SMTP host is operator-private routing (redacted like the addresses): not logged.
                log.info("E-mail alerts enabled: %d recipient(s)", len(settings.to_addresses))
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
        raise RuntimeError(_scrub_error(f"{type(exc).__name__}: {exc}", settings, env)) from None


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
