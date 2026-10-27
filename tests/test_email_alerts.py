"""B1: opt-in e-mail alert delivery (docs/live_readiness_v1.md).

Offline only: every SMTP server here is a fake class handed to the sender,
no socket is opened, no credential is real and nothing is sent.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from parayabuck.config import EmailAlertConfig, load_config
from parayabuck.monitoring import alerts, email_alerts
from parayabuck.monitoring.email_alerts import (
    SMTP_PASSWORD_ENV,
    SMTP_USERNAME_ENV,
    EmailAlertSink,
    build_message,
    send_email_message,
    validate_email_setup,
)

_REPO = Path(__file__).resolve().parents[1]
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


class _FakeSMTP:
    """Records the SMTP conversation; never touches the network."""

    instances: list[_FakeSMTP] = []
    fail_with: Exception | None = None

    def __init__(self, host: str, port: int, timeout: float = 0.0, context: Any = None) -> None:
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls: list[str] = []
        self.sent: list[Any] = []
        type(self).instances.append(self)

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, context: Any = None) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append(f"login:{username}")
        if type(self).fail_with is not None:
            raise type(self).fail_with

    def send_message(self, msg: Any) -> None:
        self.calls.append("send")
        self.sent.append(msg)

    def quit(self) -> None:
        self.calls.append("quit")

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    _FakeSMTP.instances = []
    _FakeSMTP.fail_with = None
    alerts.reset_for_tests()
    alerts._throttle_seconds = 0.0
    email_alerts.reset_for_tests()
    yield
    email_alerts.reset_for_tests()
    alerts.reset_for_tests()


def _sink(settings: EmailAlertConfig | None = None, **kwargs: Any) -> EmailAlertSink:
    # Retry waits are injected away: a real failing send would otherwise wait ~65 s.
    kwargs.setdefault("sleep", lambda _delay: None)
    return EmailAlertSink(
        settings or _settings(),
        mode="live",
        env=_ENV,
        smtp_factory=_FakeSMTP,
        smtp_ssl_factory=_FakeSMTP,
        start_worker=False,
        **kwargs,
    )


# --- configuration ---------------------------------------------------------


def test_email_is_disabled_by_default_and_in_the_shipped_settings() -> None:
    assert EmailAlertConfig().enabled is False
    shipped = load_config(str(_REPO / "config" / "settings.yaml"))
    assert shipped.alerting.email.enabled is False
    assert shipped.runtime.mode.value == "paper"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"smtp_host": ""}, "smtp_host"),
        ({"from_address": "not-an-address"}, "from_address"),
        ({"to_addresses": []}, "to_addresses"),
        ({"to_addresses": ["a@b.com", "bad"]}, "invalid address"),
        ({"security": "plain"}, "starttls or ssl"),
        ({"smtp_port": 0}, "smtp_port"),
        ({"max_per_hour": 0}, "max_per_hour"),
    ],
)
def test_enabled_email_config_is_validated(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _settings(**overrides)


def test_email_block_loads_from_yaml_and_webhook_keys_stay_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load((_REPO / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw["alerting"]["email"] = {
        "enabled": True,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "security": "SSL",
        "from_address": "bot@gmail.com",
        "to_addresses": "owner@gmail.com",
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    email = load_config(str(path)).alerting.email
    assert (email.enabled, email.security, email.to_addresses) == (True, "ssl", ("owner@gmail.com",))
    raw["alerting"]["webhook_url"] = "https://hooks.example.com/x"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="alerting.webhook_url"):
        load_config(str(path))


def test_credentials_are_never_read_from_the_settings_file(tmp_path: Path) -> None:
    raw = yaml.safe_load((_REPO / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw["alerting"]["email"] = {"enabled": False, "password": "x"}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(TypeError, match="password"):
        load_config(str(path))


def test_validate_email_setup_checks_the_credential_environment_offline() -> None:
    settings = _settings()
    assert validate_email_setup(settings, env=_ENV) == []
    assert "not set" in validate_email_setup(settings, env={})[0]
    assert "only one" in validate_email_setup(settings, env={SMTP_USERNAME_ENV: "u"})[0]
    assert validate_email_setup(_settings(enabled=False, smtp_host=""), env={}) == []


# --- message + SMTP conversation ------------------------------------------


def test_message_carries_the_alert_but_never_credentials() -> None:
    msg = build_message(
        _settings(),
        kind="exit_sell_rejected",
        message="SELL of QLD rejected",
        fields={"symbol": "QLD", "api_key": "AKSECRET", "smtp_password": "hunter2"},
        mode="live",
        hostname="vm",
    )
    text = msg.as_string()
    assert msg["Subject"].startswith("[ParayaBuck LIVE] exit_sell_rejected: SELL of QLD rejected")
    assert msg["To"] == "owner@example.com"
    assert "symbol: QLD" in text
    assert "AKSECRET" not in text and "hunter2" not in text
    assert "app-password-123" not in text


def test_starttls_runs_before_login_and_send() -> None:
    send_email_message(
        _settings(), build_message(_settings(), kind="k", message="m", fields={}), env=_ENV, smtp_factory=_FakeSMTP
    )
    (server,) = _FakeSMTP.instances
    assert (server.host, server.port, server.timeout) == ("smtp.example.com", 587, 10.0)
    assert server.calls == ["ehlo", "starttls", "ehlo", "login:bot@example.com", "send", "quit"]


def test_ssl_mode_uses_implicit_tls_without_starttls() -> None:
    settings = _settings(security="ssl", smtp_port=465)
    send_email_message(
        settings, build_message(settings, kind="k", message="m", fields={}), env=_ENV, smtp_ssl_factory=_FakeSMTP
    )
    (server,) = _FakeSMTP.instances
    assert server.context is not None
    assert "starttls" not in server.calls and server.calls[-2:] == ["send", "quit"]


# --- routing, throttle, failure isolation ----------------------------------


def test_only_operator_relevant_kinds_are_emailed() -> None:
    sink = _sink()
    for kind in (
        "runtime_stopped_unexpected",
        "runtime_start_failed",
        "circuit_breaker",
        "order_rejected",
        "exit_sell_rejected",
        "intent_alarm",
        "reconciliation_failure",
        "live_rearm_required",
        "live_authorization_expiring",
        "live_authorization_expired",
        "daily_summary",
    ):
        assert sink.accepts(kind), kind
    for kind in ("runtime_started", "paper_receipt", "decision_deferred", "alert_delivery_failed"):
        assert not sink.accepts(kind), kind
    assert not _sink(_settings(daily_summary=False)).accepts("daily_summary")


def test_repeats_are_coalesced_per_throttle_key_and_capped_per_hour() -> None:
    clock = {"now": 1000.0}
    sink = _sink(_settings(min_interval_minutes=30, max_per_hour=3), clock=lambda: clock["now"])
    assert sink.notify("intent_alarm", "m", {}, "intent_alarm:reason=a") is True
    assert sink.notify("intent_alarm", "m", {}, "intent_alarm:reason=a") is False
    assert sink.notify("intent_alarm", "m", {}, "intent_alarm:reason=b") is True
    clock["now"] += 31 * 60
    assert sink.notify("intent_alarm", "m", {}, "intent_alarm:reason=a") is True
    # Hourly ceiling (3): the fourth distinct incident is dropped and journaled.
    journaled: list[str] = []
    original = email_alerts._record_delivery_failure
    email_alerts._record_delivery_failure = lambda kind, reason, error: journaled.append(reason)  # type: ignore[assignment]
    try:
        assert sink.notify("order_rejected", "m", {}, "order_rejected:x") is False
    finally:
        email_alerts._record_delivery_failure = original  # type: ignore[assignment]
    assert journaled == ["rate_limited"]
    assert sink.stats()["dropped"] == 1


def test_smtp_failure_is_journaled_scrubbed_and_never_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    journaled: list[Any] = []
    monkeypatch.setattr("parayabuck.monitoring.journal.emit_journal", lambda event: journaled.append(event))
    _FakeSMTP.fail_with = RuntimeError("535 auth failed for bot@example.com with app-password-123")
    sink = _sink()
    assert sink.notify("circuit_breaker", "halted", {}, "circuit_breaker:reason=x") is True
    assert sink.process_one(timeout=0.1) is True
    assert sink.stats() == {"sent": 0, "failed": 1, "dropped": 0}
    failures = [event for event in journaled if event.kind == "alert_delivery_failed"]
    assert len(failures) == 1
    rendered = repr(failures[0].to_dict())
    assert "app-password-123" not in rendered and "bot@example.com" not in rendered
    assert failures[0].fields["alert_kind"] == "circuit_breaker"


def test_notify_never_blocks_on_a_hung_mail_server() -> None:
    release = threading.Event()

    class _HangingSMTP(_FakeSMTP):
        def ehlo(self) -> None:
            release.wait(5)

    sink = EmailAlertSink(_settings(), env=_ENV, smtp_factory=_HangingSMTP, start_worker=True)
    try:
        started = time.monotonic()
        for index in range(100):
            sink.notify("order_rejected", "m", {}, f"order_rejected:{index}")
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        sink.close()


def test_emit_hands_accepted_alerts_to_the_sink_after_the_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parayabuck.monitoring.journal.emit_journal", lambda event: None)
    sink = _sink()
    email_alerts.install_sink_for_tests(sink)
    alerts.emit("order_rejected", message="rejected", client_order_id="c1", side="sell")
    alerts.emit("runtime_started", message="started")
    assert sink.process_one(timeout=0.1) is True
    assert sink.process_one(timeout=0.01) is False
    (server,) = _FakeSMTP.instances
    assert "order_rejected" in server.sent[0]["Subject"]


def test_configure_installs_only_an_enabled_sink() -> None:
    assert email_alerts.configure_email_alerts(_settings(enabled=False)) is None
    assert email_alerts.current_sink() is None
    sink = email_alerts.configure_email_alerts(_settings(), mode="paper")
    assert sink is not None and email_alerts.current_sink() is sink
    assert email_alerts.configure_email_alerts(_settings(), mode="paper") is sink
    email_alerts.configure_email_alerts(None)
    assert email_alerts.current_sink() is None


# --- doctor + test-email command -------------------------------------------


def test_doctor_email_check_never_sends_and_blocks_only_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck import cli

    monkeypatch.setattr(email_alerts, "send_email_message", lambda *a, **k: pytest.fail("doctor must not send"))
    config = SimpleNamespace(alerting=SimpleNamespace(email=_settings()))
    results: list[tuple[str, bool]] = []
    checks: list[tuple[str, str, str]] = []

    def add(name: str, ok: bool, detail: str = "", is_blocker: bool = True) -> None:
        results.append((name, ok))

    cli._doctor_email_checks(config, add, checks, required=True)  # type: ignore[arg-type]
    assert results == [("e-mail alerts valid", False)]  # no SMTP env in tests
    results.clear()
    cli._doctor_email_checks(config, add, checks, required=False)  # type: ignore[arg-type]
    assert results == [] and checks[-1][1] == "WARN"
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    cli._doctor_email_checks(config, add, checks, required=True)  # type: ignore[arg-type]
    assert results == [("e-mail alerts valid", True)]
    results.clear()
    disabled = SimpleNamespace(alerting=SimpleNamespace(email=EmailAlertConfig()))
    cli._doctor_email_checks(disabled, add, checks, required=True)  # type: ignore[arg-type]
    assert results == [("e-mail alerts enabled (LIVE)", False)]


def test_test_email_command_sends_nothing_without_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck import cli

    raw = yaml.safe_load((_REPO / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw["alerting"]["email"] = {
        "enabled": True,
        "smtp_host": "smtp.example.com",
        "from_address": "bot@example.com",
        "to_addresses": ["owner@example.com"],
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    sent: list[Any] = []
    monkeypatch.setattr(email_alerts, "send_email_message", lambda *a, **k: sent.append(a))
    args = SimpleNamespace(config=str(path), confirm=False)
    assert cli._ops_test_email(args) == 0  # type: ignore[arg-type]
    assert sent == []
    args.confirm = True
    assert cli._ops_test_email(args) == 0  # type: ignore[arg-type]
    assert len(sent) == 1
