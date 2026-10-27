"""Operator controls: sticky halt, systemd credentials, strict credential mode, account pin.

Every test works on tmp_path state only: no broker, no network, no production
runtime directory. The settings file is the repository default with the runtime
ledger redirected into tmp_path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from parayabuck import cli
from parayabuck.config import BrokerConfig, credential_file_mode_warning, load_config
from parayabuck.live_safety import (
    HALT_MARKER_FILENAME,
    LiveApprovalError,
    clear_operator_halt,
    mode_credentials,
    operator_halt_active,
    operator_halt_marker_path,
    read_operator_halt,
    verify_account_environment,
    write_operator_halt,
)
from parayabuck.web.managers import engine as engine_module
from parayabuck.web.managers.engine import EngineManager

REPO_SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"


def _settings(tmp_path: Path, *, broker: dict | None = None) -> Path:
    """A loadable settings file whose ledger lives under tmp_path."""
    raw = yaml.safe_load(REPO_SETTINGS.read_text(encoding="utf-8"))
    raw["runtime"]["decision_db_path"] = str(tmp_path / "runtime" / "parayabuck.sqlite3")
    if broker is not None:
        raw["broker"] = broker
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def no_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # load_config reads the .env named by ENV_PATH; point it at nothing.
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))


# --- Task 1: sticky operator halt ------------------------------------------


def test_halt_and_resume_create_and_remove_marker(tmp_path: Path, no_dotenv, capsys) -> None:
    settings = _settings(tmp_path)
    marker = tmp_path / "runtime" / HALT_MARKER_FILENAME
    # halt writes into an existing runtime directory and never creates one (audit F8-5).
    (tmp_path / "runtime").mkdir()

    assert cli.main(["--config", str(settings), "halt", "--reason", "fill mismatch"]) == 0
    assert marker.is_file()
    record = json.loads(marker.read_text(encoding="utf-8"))
    assert record["reason"] == "fill mismatch"
    assert record["user"]
    assert record["created_at"]
    assert "Operator halt set" in capsys.readouterr().out

    assert cli.main(["--config", str(settings), "resume"]) == 0
    assert not marker.exists()
    # Resume with no marker is a clean no-op.
    assert cli.main(["--config", str(settings), "resume"]) == 0
    assert "nothing to clear" in capsys.readouterr().out


def test_halt_marker_path_follows_runtime_ledger_directory(tmp_path: Path) -> None:
    assert operator_halt_marker_path(tmp_path / "runtime") == tmp_path / "runtime" / HALT_MARKER_FILENAME
    assert operator_halt_marker_path(tmp_path / "runtime" / "live").name == "OPERATOR_HALT"


def test_unreadable_marker_still_counts_as_halt(tmp_path: Path) -> None:
    marker = tmp_path / HALT_MARKER_FILENAME
    marker.write_text("{not json", encoding="utf-8")

    assert operator_halt_active(marker) is True
    record = read_operator_halt(marker)
    assert record is not None and "unreadable" in record["reason"]
    assert clear_operator_halt(marker) is True
    assert operator_halt_active(marker) is False


def test_write_and_clear_operator_halt_roundtrip(tmp_path: Path) -> None:
    marker = tmp_path / "nested" / HALT_MARKER_FILENAME

    record = write_operator_halt(marker, reason="", user="")
    assert record["reason"] == "operator halt"
    assert record["user"] == "unknown"
    assert read_operator_halt(marker) == record
    assert clear_operator_halt(marker) is True
    assert clear_operator_halt(marker) is False
    assert read_operator_halt(marker) is None


def test_should_auto_start_refuses_with_marker(tmp_path: Path, monkeypatch, no_dotenv, caplog) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    manager = EngineManager()
    manager.config_path = settings
    write_operator_halt(tmp_path / "runtime" / HALT_MARKER_FILENAME, reason="stop", user="tester")

    with caplog.at_level("ERROR"):
        assert manager.should_auto_start() is False
    assert any("operator halt is in force" in message for message in caplog.messages)


def test_start_engine_refuses_with_marker_and_leaves_state_stopped(tmp_path: Path, monkeypatch, no_dotenv) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    manager = EngineManager()
    manager.config_path = settings
    write_operator_halt(tmp_path / "runtime" / HALT_MARKER_FILENAME, reason="stop", user="tester")

    with pytest.raises(ValueError, match="operator halt is in force"):
        manager.start_engine()
    assert manager.engine_state == "stopped"
    assert manager.runtime is None


def test_no_marker_means_no_halt_refusal(tmp_path: Path, no_dotenv) -> None:
    manager = EngineManager()
    manager.config_path = _settings(tmp_path)
    assert manager._operator_halt_refusal() is None


# --- Task 2: systemd credentials and the strict credential-mode check ------


def test_credentials_directory_overrides_environment(tmp_path: Path) -> None:
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "ALPACA_API_KEY").write_text("file-key\n", encoding="utf-8")
    env = {
        "CREDENTIALS_DIRECTORY": str(creds),
        "ALPACA_API_KEY": "env-key",
        "ALPACA_SECRET_KEY": "env-secret",
    }

    assert mode_credentials(live=False, env=env) == ("file-key", "env-secret")


def test_empty_credential_file_falls_back_to_environment(tmp_path: Path) -> None:
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "ALPACA_API_KEY").write_text("   \n", encoding="utf-8")
    env = {
        "CREDENTIALS_DIRECTORY": str(creds),
        "ALPACA_API_KEY": " env-key ",
        "ALPACA_SECRET_KEY": "env-secret",
    }

    assert mode_credentials(live=False, env=env) == ("env-key", "env-secret")


def test_live_credentials_read_only_the_live_names_from_directory(tmp_path: Path) -> None:
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "ALPACA_LIVE_API_KEY").write_text("live-key", encoding="utf-8")
    (creds / "ALPACA_LIVE_SECRET_KEY").write_text("live-secret", encoding="utf-8")
    env = {"CREDENTIALS_DIRECTORY": str(creds), "ALPACA_API_KEY": "paper-key", "ALPACA_SECRET_KEY": "paper"}

    assert mode_credentials(live=True, env=env) == ("live-key", "live-secret")


def test_strict_doctor_fails_on_loose_credential_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ALPACA_API_KEY=x\n", encoding="utf-8")
    os.chmod(env_file, 0o644)
    warning = credential_file_mode_warning(env_file)
    assert warning is not None

    assert cli._credential_permission_row(warning, strict=True) == ("FAIL", warning)
    assert cli._credential_permission_row(warning, strict=False) == ("WARN", warning)


def test_owner_only_credential_file_passes_strict_doctor(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ALPACA_API_KEY=x\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    warning = credential_file_mode_warning(env_file)

    assert warning is None
    assert cli._credential_permission_row(warning, strict=True) is None


# --- Task 3: broker.expected_account_id pin --------------------------------


def test_broker_config_defaults_to_none_and_normalises_blank(tmp_path: Path, no_dotenv) -> None:
    assert BrokerConfig().expected_account_id is None
    assert BrokerConfig(expected_account_id="   ").expected_account_id is None
    assert load_config(str(_settings(tmp_path))).broker.expected_account_id is None
    pinned = load_config(str(_settings(tmp_path, broker={"expected_account_id": "PA123"})))
    assert pinned.broker.expected_account_id == "PA123"


def test_paper_pin_mismatch_refuses() -> None:
    with pytest.raises(LiveApprovalError, match="does not match broker.expected_account_id"):
        verify_account_environment(live=False, account_number="PA123", api_key="PKabc", expected_account_id="PA999")


def test_paper_pin_match_passes() -> None:
    verify_account_environment(live=False, account_number="PA123", api_key="PKabc", expected_account_id="PA123")


def test_live_pin_mismatch_refuses() -> None:
    with pytest.raises(LiveApprovalError, match="does not match broker.expected_account_id"):
        verify_account_environment(
            live=True, account_number="987654321", api_key="AKxyz", expected_account_id="111111111"
        )


def test_live_pin_match_passes() -> None:
    verify_account_environment(live=True, account_number="987654321", api_key="AKxyz", expected_account_id="987654321")


def test_pin_matches_account_id_when_supplied() -> None:
    verify_account_environment(
        live=False,
        account_number="PA123",
        api_key="PKabc",
        expected_account_id="uuid-1",
        account_id="uuid-1",
    )


def test_pin_fails_closed_without_any_broker_identifier() -> None:
    with pytest.raises(LiveApprovalError, match="could not be read"):
        verify_account_environment(live=False, account_number="", api_key="PKabc", expected_account_id="PA123")


@pytest.mark.parametrize(
    ("live", "account_number", "api_key"),
    [(False, "PA999", "PKabc"), (True, "987654321", "AKxyz")],
)
def test_no_pin_keeps_previous_behaviour(live: bool, account_number: str, api_key: str) -> None:
    verify_account_environment(live=live, account_number=account_number, api_key=api_key, expected_account_id=None)


def test_paper_still_refuses_live_key_with_pin_unset() -> None:
    with pytest.raises(LiveApprovalError, match="is a LIVE key"):
        verify_account_environment(live=False, account_number="987654321", api_key="AKxyz", expected_account_id=None)
