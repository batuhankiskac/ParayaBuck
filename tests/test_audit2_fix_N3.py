"""Operator UX fixes: durable operator Stop, halt status, Turkish owner text (D2-10-1..4, D2-10-9).

Every test works on tmp_path state only: no broker, no network, no production
runtime directory. Credentials are monkeypatched, never read from .env.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from parayabuck.live_safety import HALT_MARKER_FILENAME, write_operator_halt
from parayabuck.web.managers import engine as engine_module
from parayabuck.web.managers.engine import OPERATOR_STOP_MARKER_FILENAME, EngineManager
from parayabuck.web.services.status import build_status_snapshot

REPO = Path(__file__).resolve().parents[1]
REPO_SETTINGS = REPO / "config" / "settings.yaml"
WEB = REPO / "src" / "parayabuck" / "web"


def _settings(tmp_path: Path) -> Path:
    """A loadable paper settings file whose ledger lives under tmp_path."""
    raw = yaml.safe_load(REPO_SETTINGS.read_text(encoding="utf-8"))
    raw["runtime"]["decision_db_path"] = str(tmp_path / "runtime" / "parayabuck.sqlite3")
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineManager:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    monkeypatch.setattr(EngineManager, "get_credentials", lambda self: ("paper-key", "paper-secret"))
    built = EngineManager()
    built.config_path = _settings(tmp_path)
    built.security_warning = None
    return built


def _stop_marker(tmp_path: Path) -> Path:
    return tmp_path / "runtime" / OPERATOR_STOP_MARKER_FILENAME


# --- D2-10-1: a Stop outlives a restart -------------------------------------


def test_stopped_engine_auto_starts_without_a_stop_record(manager: EngineManager) -> None:
    # Control: the same manager auto-starts when no operator Stop exists.
    assert manager.should_auto_start() is True


def test_operator_stop_blocks_auto_start_across_restarts(manager: EngineManager, tmp_path: Path) -> None:
    manager.stop_engine(operator_request=True)

    assert _stop_marker(tmp_path).is_file()
    # A new process reads the same file, so every restart refuses to auto-start.
    assert manager.should_auto_start() is False
    assert manager.should_auto_start() is False


def test_shutdown_style_stop_leaves_auto_start_on(manager: EngineManager, tmp_path: Path) -> None:
    # Lifespan shutdown, settings restarts and deploys stop without the operator flag.
    manager.stop_engine()

    assert not _stop_marker(tmp_path).exists()
    assert manager.should_auto_start() is True


def test_failed_stop_record_write_still_stops_and_alerts(
    manager: EngineManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An operator Stop must never leave the engine trading: the record is a
    # convenience for restarts, so its failure is alerted, not obeyed.
    def refuse(self: EngineManager) -> None:
        raise OSError("disk full")

    alerts: list[dict[str, object]] = []
    monkeypatch.setattr(EngineManager, "_write_operator_stop_marker", refuse)
    monkeypatch.setattr("parayabuck.web.managers.engine._alert_start_failure", lambda **kwargs: alerts.append(kwargs))
    manager.engine_state = "stopped"

    manager.stop_engine(operator_request=True)  # no raise: the stop goes ahead

    assert [alert["reason"] for alert in alerts] == ["operator_stop_unrecorded"]


def test_explicit_start_clears_the_operator_stop(manager: EngineManager, tmp_path: Path) -> None:
    manager.stop_engine(operator_request=True)
    assert _stop_marker(tmp_path).is_file()
    manager._backtest_manager = SimpleNamespace(lock=threading.RLock(), is_running=False)
    manager._run_start_attempt = lambda *, auto_start: None  # no runtime in this test

    manager.start_engine()

    assert not _stop_marker(tmp_path).exists()
    assert manager.engine_state == "starting"


def test_auto_start_attempt_does_not_lift_the_operator_stop(manager: EngineManager, tmp_path: Path) -> None:
    manager.stop_engine(operator_request=True)
    manager._backtest_manager = SimpleNamespace(lock=threading.RLock(), is_running=False)
    manager._run_start_attempt = lambda *, auto_start: None

    manager.start_engine(auto_start=True)

    assert _stop_marker(tmp_path).is_file()


# --- D2-10-4: the operator halt is visible and refused in Turkish -----------


def test_operator_halt_refusal_is_turkish_and_names_the_resume_command(manager: EngineManager, tmp_path: Path) -> None:
    write_operator_halt(tmp_path / "runtime" / HALT_MARKER_FILENAME, reason="fill mismatch", user="tester")

    refusal = manager._operator_halt_refusal()

    assert refusal is not None
    assert "operatör durdurması etkin" in refusal
    assert "fill mismatch" in refusal
    assert "parayabuck resume" in refusal


def test_status_reports_an_active_operator_halt(manager: EngineManager, tmp_path: Path) -> None:
    write_operator_halt(tmp_path / "runtime" / HALT_MARKER_FILENAME, reason="fill mismatch", user="tester")

    snapshot = build_status_snapshot(manager)

    assert snapshot["operator_halt"]["active"] is True
    assert snapshot["operator_halt"]["reason"] == "fill mismatch"


def test_status_reports_no_operator_halt_without_marker(manager: EngineManager) -> None:
    assert build_status_snapshot(manager)["operator_halt"] is None


def test_unreadable_halt_marker_still_reports_active(manager: EngineManager, tmp_path: Path) -> None:
    marker = tmp_path / "runtime" / HALT_MARKER_FILENAME
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("not json", encoding="utf-8")

    halt = build_status_snapshot(manager)["operator_halt"]

    assert halt is not None and halt["active"] is True


# --- D2-10-2/3/4/5 and D2-10-6: owner dialogs and banners (static contract) -


def _ui_source() -> str:
    return (WEB / "static" / "js" / "ui.js").read_text(encoding="utf-8")


def test_restart_prompt_has_three_answers_and_aborts_on_null() -> None:
    source = _ui_source()
    assert 'altLabel: "Hayır, yalnızca kaydet"' in source
    assert 'restart_engine: choice === "ok"' in source
    assert "if (choice === null) return;" in source
    # The save is sent only after the choice is known.
    assert source.index("if (choice === null) return;") < source.index('postControl("/api/settings", payload)')


def test_restart_prompt_button_exists_in_the_shared_dialog() -> None:
    html = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'id="pbDialogAlt"' in html


def test_risk_epoch_dialog_is_danger_styled_and_not_labelled_start() -> None:
    source = _ui_source()
    block = source[source.index("if (epoch) epoch.addEventListener") : source.index("if (chip) chip.addEventListener")]
    assert "danger: true" in block
    assert "Zarar kabul edilsin" in block
    assert 'confirmLabel: "Başlat"' not in block


def test_live_start_confirms_the_account_and_the_cap_before_posting() -> None:
    source = _ui_source()
    assert "await confirmLiveStart()" in source
    block = source[source.index("async function confirmLiveStart") : source.index("function setupControls")]
    assert "live_dollar_cap" in block
    assert "shell.accountSuffix" in block


def test_operator_halt_disables_the_start_button() -> None:
    source = _ui_source()
    assert "if (shell.operatorHalt) start.disabled = true;" in source


def test_security_block_banner_uses_the_danger_style() -> None:
    source = _ui_source()
    assert "messages.push(status.security_warning); danger = true;" in source


# --- D2-10-9: the owner-facing refusals are Turkish --------------------------


def test_mode_toggle_refusal_is_turkish(manager: EngineManager) -> None:
    manager.runtime = object()
    with pytest.raises(ValueError, match="işlem modu değiştirilemez"):
        manager.write_mode_if_idle(True, lambda path, paper: None)
