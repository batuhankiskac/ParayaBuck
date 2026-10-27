"""Audit wave 2 docs/config/backup fixes (worker O1): D2-1-2, D2-2-1, D2-2-3.

* D2-1-2 — the ``backup`` command installs the runtime's e-mail sink, so a
  ``backup_failed`` alert is mailed. The sink is a fake here: no SMTP, no network.
* D2-2-3 — ``alerting.email: false`` is an explicit off switch; ``true`` is still refused.
* D2-2-1 — the gain-band floor error names the two remedies that load.

Offline throughout: tmp_path settings only, no broker, no credentials.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from parayabuck import cli as cli_module
from parayabuck import load_config
from parayabuck.config import AlertingConfig, DownsizeGainBand, EmailAlertConfig, ExecutionConfig, RuntimeMode
from parayabuck.monitoring import email_alerts
from parayabuck.trading_host import TRADING_HOST_REFUSAL

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"


def _shipped_raw() -> dict[str, Any]:
    return yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))


def _load_mutated(tmp_path: Path, mutate: Any) -> Any:
    raw = _shipped_raw()
    mutate(raw)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(str(path))


# -- D2-2-3: alerting.email: false ---------------------------------------------


def test_alerting_email_false_loads_as_disabled(tmp_path: Path) -> None:
    config = _load_mutated(tmp_path, lambda raw: raw.setdefault("alerting", {}).update(email=False))

    assert config.alerting.email.enabled is False


def test_alerting_email_false_constructed_directly_is_disabled() -> None:
    assert AlertingConfig(email=False).email == EmailAlertConfig()


def test_alerting_email_true_is_still_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="alerting.email must be a mapping"):
        _load_mutated(tmp_path, lambda raw: raw.setdefault("alerting", {}).update(email=True))


# -- D2-2-1: gain-band floor and the documented remedies -------------------------


def test_gain_band_floor_error_names_both_remedies() -> None:
    with pytest.raises(ValueError, match="at least rebalance_band_pct") as excinfo:
        ExecutionConfig(rebalance_band_pct=0.01)

    assert "set execution.downsize_gain_band to null" in str(excinfo.value)


def test_documented_one_point_percent_remedy_loads_with_null_schedule() -> None:
    config = ExecutionConfig(rebalance_band_pct=0.01, downsize_gain_band=None)

    assert config.rebalance_band_pct == 0.01


def test_documented_one_point_percent_remedy_loads_with_raised_tiers() -> None:
    config = ExecutionConfig(
        rebalance_band_pct=0.01,
        downsize_gain_band=DownsizeGainBand(bands=(0.01, 0.03, 0.08)),
    )

    assert config.downsize_gain_band is not None
    assert config.downsize_gain_band.bands[0] == 0.01


# -- D2-1-2: backup command installs the runtime e-mail sink ------------------------


class _Worker:
    def __init__(self) -> None:
        self.join_timeouts: list[float | None] = []

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(timeout)


class _FakeSink:
    instances: list[_FakeSink] = []

    def __init__(self, settings: Any, *, mode: str = "") -> None:
        self.settings = settings
        self.mode = mode
        self.closed = False
        self._worker = _Worker()
        _FakeSink.instances.append(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_sink(monkeypatch: pytest.MonkeyPatch) -> Any:
    _FakeSink.instances = []
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setattr(email_alerts, "EmailAlertSink", _FakeSink)
    yield _FakeSink
    email_alerts.reset_for_tests()


def _config_with_email(enabled: bool) -> Any:
    email = EmailAlertConfig(
        enabled=enabled,
        smtp_host="smtp.example.test",
        to_addresses=("ops@example.test",),
        from_address="bot@example.test",
    )
    return SimpleNamespace(
        alerting=SimpleNamespace(email=email),
        runtime=SimpleNamespace(mode=RuntimeMode.PAPER),
    )


def test_backup_command_installs_the_runtime_sink_before_backup_runs(
    monkeypatch: pytest.MonkeyPatch, fake_sink: Any
) -> None:
    config = _config_with_email(enabled=True)
    seen: dict[str, Any] = {}

    def fake_backup(cfg: Any, args: Any) -> int:
        seen["sink"] = email_alerts.current_sink()
        return 1

    monkeypatch.setattr(cli_module, "_run_backup", fake_backup)

    result = cli_module._run_backup_with_email(config, SimpleNamespace())

    assert result == 1
    sink = seen["sink"]
    assert isinstance(sink, fake_sink)
    assert sink.settings is config.alerting.email
    assert sink.mode == "paper"


def test_backup_command_flushes_and_closes_the_sink_on_exit(monkeypatch: pytest.MonkeyPatch, fake_sink: Any) -> None:
    config = _config_with_email(enabled=True)

    def failing_backup(cfg: Any, args: Any) -> int:
        raise SystemExit("Backup failed integrity check")

    monkeypatch.setattr(cli_module, "_run_backup", failing_backup)

    with pytest.raises(SystemExit):
        cli_module._run_backup_with_email(config, SimpleNamespace())

    sink = fake_sink.instances[0]
    assert sink.closed is True
    assert sink._worker.join_timeouts == [cli_module._EMAIL_FLUSH_SECONDS]


def test_backup_command_without_enabled_email_installs_no_sink(monkeypatch: pytest.MonkeyPatch, fake_sink: Any) -> None:
    monkeypatch.setattr(cli_module, "_run_backup", lambda cfg, args: 0)

    assert cli_module._run_backup_with_email(_config_with_email(enabled=False), SimpleNamespace()) == 0
    assert fake_sink.instances == []
    assert email_alerts.current_sink() is None


# -- trading-host refusal text points at the drop-in ---------------------------------


def test_trading_host_refusal_points_to_the_drop_in_not_the_template() -> None:
    assert "deploy/systemd/vm-dropin/trading-host.conf" in TRADING_HOST_REFUSAL
    assert "deploy/systemd/parayabuck.service" not in TRADING_HOST_REFUSAL
    assert "PARAYABUCK_TRADING_HOST=1" in TRADING_HOST_REFUSAL
    assert "not designated as the trading host" in TRADING_HOST_REFUSAL
