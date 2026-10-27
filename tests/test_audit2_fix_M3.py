"""Audit wave 2 fix M3: web input validation, WebSocket caller checks, CLI and
forensics bounds, and the engine's read-only restore points (S2-1, S2-6, S2-10-5).

Deterministic: tmp_path state, synthetic payloads and stubs. No network, no
credentials, no production paths.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.responses import JSONResponse

from parayabuck import cli
from parayabuck.config import LiveConfig, load_config
from parayabuck.monitoring import forensics
from parayabuck.web import security
from parayabuck.web.managers import engine as engine_module
from parayabuck.web.managers.backtest import BacktestManager
from parayabuck.web.managers.engine import EngineManager, LiveArmToken, _atomic_update_credentials
from parayabuck.web.routers import websocket as websocket_router
from parayabuck.web.schemas.settings import SettingsPayload
from parayabuck.web.services import performance

REPO = Path(__file__).resolve().parents[1]
REPO_SETTINGS = REPO / "config" / "settings.yaml"
ENGINE_UNIT = REPO / "deploy" / "systemd" / "parayabuck.service"
VM_PATHS = REPO / "deploy" / "systemd" / "vm-dropin" / "vm-paths.conf"

_KEY = "PKkey123456789"
_SECRET = "PKsecret12345"


def _manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineManager:
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setattr(engine_module, "_scan_tracked_secrets", lambda root: None)
    monkeypatch.setattr(
        engine_module,
        "load_config",
        lambda _path: SimpleNamespace(runtime=SimpleNamespace(mode=SimpleNamespace(value="paper"))),
    )
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.env_path = tmp_path / ".env"
    manager.security_warning = None
    return manager


def _settings_copy(tmp_path: Path, mutate: Any) -> Path:
    raw = yaml.safe_load(REPO_SETTINGS.read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _directives(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    ]


# --- S2-1-1: credentials are printable ASCII; the .env rewrite splits on dotenv breaks ---


@pytest.mark.parametrize("bad_key", ["PKkey tail", "PKkey\x0btail", "PKké123456789", "PKkey 123456"])
def test_credential_save_refuses_control_and_non_ascii_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_key: str
) -> None:
    manager = _manager(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="ASCII"):
        manager._validated_settings_payload(SettingsPayload(api_key=bad_key, secret_key=_SECRET))


def test_env_rewrite_keeps_non_dotenv_separators_inside_their_line(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEEP=a ALPACA_API_KEY=old\nALPACA_SECRET_KEY=old2\n", encoding="utf-8")

    _atomic_update_credentials(env, _KEY, _SECRET)

    assert env.read_text(encoding="utf-8") == (
        'KEEP=a ALPACA_API_KEY=old\nALPACA_SECRET_KEY="PKsecret12345"\nALPACA_API_KEY="PKkey123456789"\n'
    )


# --- S2-1-2: upstream error text on the performance payload is redacted ---------------


def test_portfolio_history_error_text_is_redacted() -> None:
    performance.reset_cache()

    class _Broker:
        account_id = "acct-redaction"

        def portfolio_history(self, *_args: Any) -> list[Any]:
            raise RuntimeError("upstream rejected key PKkey123456789")

    class _Manager:
        broker = _Broker()

        def is_paper_mode(self) -> bool:
            return True

        def redact_secrets(self, text: str) -> str:
            return text.replace("PKkey123456789", "[redacted]")

    payload = performance.portfolio_performance(_Manager(), "1M", benchmark_fetcher=lambda *_a: [])
    performance.reset_cache()

    assert payload["available"] is False
    assert "PKkey123456789" not in payload["reason"]
    assert "[redacted]" in payload["reason"]


# --- S2-1-3: body-length checks never crash on odd digits and refuse unbounded bodies --


def _run_protect(method: str, headers: dict[str, str]) -> JSONResponse:
    middleware = security.build_protect_middleware(lambda _request: "tok")
    request = SimpleNamespace(
        method=method,
        headers=headers,
        url=SimpleNamespace(path="/api/settings"),
        client=SimpleNamespace(host="127.0.0.1"),
    )

    async def call_next(_request: Any) -> JSONResponse:
        return JSONResponse({"ok": True})

    return asyncio.run(middleware(request, call_next))


def test_superscript_content_length_is_a_refusal_not_a_crash() -> None:
    response = _run_protect(
        "POST",
        {"host": "127.0.0.1", "content-length": "²", "x-parayabuck-control-token": "tok"},
    )

    assert response.status_code == 400


def test_chunked_body_is_refused_before_the_token_check() -> None:
    response = _run_protect("POST", {"host": "127.0.0.1", "transfer-encoding": "chunked"})

    assert response.status_code == 411


def test_declared_oversize_body_is_still_refused() -> None:
    response = _run_protect(
        "POST",
        {"host": "127.0.0.1", "content-length": str(64 * 1024 + 1), "x-parayabuck-control-token": "tok"},
    )

    assert response.status_code == 413


def test_plain_post_with_the_token_still_passes() -> None:
    response = _run_protect(
        "POST",
        {"host": "127.0.0.1", "content-length": "12", "x-parayabuck-control-token": "tok"},
    )

    assert response.status_code == 200


# --- S2-1-4: a systemd credential file cannot silently shadow a rotated .env key -------


def test_key_rotation_is_refused_while_a_systemd_credential_shadows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "ALPACA_API_KEY").write_text("PKshadowkey1234\n", encoding="utf-8")
    manager = _manager(tmp_path, monkeypatch)
    # Set after the fixture: _manager clears CREDENTIALS_DIRECTORY for isolation.
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))

    with pytest.raises(ValueError, match="systemd"):
        manager._validated_settings_payload(SettingsPayload(api_key="PKnewkey1234567", secret_key=_SECRET))


# --- S2-1-5: redaction fails closed when the credential source is unreadable ----------


def test_redact_secrets_withholds_text_when_credentials_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)

    def _unreadable() -> tuple[str | None, str | None]:
        raise OSError("env file unreadable")

    monkeypatch.setattr(manager, "get_paper_credentials", _unreadable)

    assert manager.redact_secrets("failed with PKkey123456789") == "[redacted: credential source unreadable]"


# --- S2-1-6: WebSocket routes apply the loopback Host and peer checks -------------------


class _TicketManager:
    def __init__(self) -> None:
        self.consumed: list[str] = []

    def consume_ws_ticket(self, ticket: str, kind: str) -> bool:
        self.consumed.append(kind)
        return True


class _Socket:
    def __init__(self, host_header: str, client_host: str | None, manager: _TicketManager) -> None:
        self.headers = {"host": host_header}
        self.client = SimpleNamespace(host=client_host) if client_host is not None else None
        self.query_params = {"ticket": "ticket-value"}
        self.app = SimpleNamespace(state=SimpleNamespace(web=SimpleNamespace(engine_manager=manager)))
        self.closed_with: int | None = None
        self.accepted = False

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    async def accept(self) -> None:
        self.accepted = True


@pytest.mark.parametrize(
    ("host_header", "client_host"),
    [("evil.example", "127.0.0.1"), ("127.0.0.1", "203.0.113.9")],
)
def test_websocket_refuses_untrusted_host_or_peer_without_consuming_the_ticket(
    host_header: str, client_host: str
) -> None:
    manager = _TicketManager()
    socket = _Socket(host_header, client_host, manager)

    asyncio.run(websocket_router.websocket_stream(socket))

    assert socket.closed_with == 1008
    assert socket.accepted is False
    assert manager.consumed == []


def test_websocket_loopback_caller_is_trusted() -> None:
    socket = _Socket("127.0.0.1:8000", "127.0.0.1", _TicketManager())

    assert websocket_router._trusted_websocket_caller(socket) is True


def test_websocket_without_a_peer_address_is_untrusted() -> None:
    socket = _Socket("127.0.0.1", None, _TicketManager())

    assert websocket_router._trusted_websocket_caller(socket) is False


# --- S2-6-1: enabled flags must be real booleans ----------------------------------------


def test_live_enabled_string_is_refused_instead_of_read_as_enabled() -> None:
    with pytest.raises(ValueError, match="live.enabled must be a boolean"):
        LiveConfig(enabled="false", hard_dollar_cap=100.0, operator_approval_path="approval.json")


def test_yaml_quoted_live_enabled_fails_the_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))

    def _quoted_live(raw: dict[str, Any]) -> None:
        raw["live"]["enabled"] = "false"

    with pytest.raises(ValueError, match="live.enabled must be a boolean"):
        load_config(str(_settings_copy(tmp_path, _quoted_live)))


def test_yaml_quoted_equity_enabled_fails_the_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))

    def _quoted_equity(raw: dict[str, Any]) -> None:
        raw["strategy"]["equity"]["enabled"] = "false"

    with pytest.raises(ValueError, match="strategy.equity.enabled must be a boolean"):
        load_config(str(_settings_copy(tmp_path, _quoted_equity)))


# --- S2-6-2: a misspelled top-level block is an error, not silently defaulted -----------


def test_misspelled_top_level_block_fails_the_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))

    def _typo(raw: dict[str, Any]) -> None:
        raw["alertng"] = {"email": {"enabled": True}}

    with pytest.raises(ValueError, match="alertng"):
        load_config(str(_settings_copy(tmp_path, _typo)))


def test_shipped_settings_still_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))

    assert load_config(str(REPO_SETTINGS)) is not None


# --- S2-6-3: a lone-surrogate arming phrase is a refusal that invalidates the arm -------


def test_lone_surrogate_arming_phrase_invalidates_the_pending_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "get_live_credentials", lambda: (_KEY, _SECRET))
    now = datetime.now(UTC)
    manager._pending_live_arm = LiveArmToken(
        account_id="acct-live",
        account_suffix="1234",
        credential_fingerprint=manager._credential_fingerprint(_KEY, _SECRET),
        config_digest=manager._config_digest(paper=False),
        issued_at=now,
        expires_at=now + timedelta(minutes=2),
    )

    with pytest.raises(ValueError, match="eşleşmiyor"):
        manager.confirm_live_arming("LIVE 1234\ud800")

    assert manager._pending_live_arm is None


# --- S2-6-4: a rejected backtest request keeps the previous run's published status -------


def test_rejected_backtest_request_keeps_the_previous_status() -> None:
    manager = BacktestManager(engine_state_provider=lambda: "stopped")
    manager.state = "done"
    manager.run_id = "web-20261027T000000Z-abcdef12"
    manager.summary = {"cagr": 0.1}

    with pytest.raises(ValueError, match="Invalid parameter name"):
        manager.start({"parameters": {"bad name": 1}})

    assert manager.state == "done"
    assert manager.run_id == "web-20261027T000000Z-abcdef12"
    assert manager.summary == {"cagr": 0.1}


# --- S2-6-5: JSON float notation from the web backtest reads as a float -----------------


def test_json_float_override_reads_as_a_float() -> None:
    value = cli._override_scalar("5e-05")

    assert isinstance(value, float)
    assert value == 5e-05
    assert cli._override_scalar("0.5") == 0.5
    assert cli._override_scalar("true") is True
    assert cli._override_scalar("abc") == "abc"


def test_grid_values_accept_json_float_notation() -> None:
    assert cli._parse_grid(["alpha=5e-05,1"]) == {"alpha": [5e-05, 1]}


# --- S2-6-6: a V1 receipt needs a whole, long-enough decision id to match ---------------


def test_v1_receipt_is_not_attached_to_a_short_or_partial_decision_id(tmp_path: Path) -> None:
    legacy = {"action": "hold", "note": "run 0123456789abcdef0123abcd"}
    (tmp_path / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")

    assert forensics._find_receipt(tmp_path, "abcd") is None
    assert forensics._find_receipt(tmp_path, "456789abcdef") is None


def test_v1_receipt_still_matches_its_whole_decision_id(tmp_path: Path) -> None:
    legacy = {"action": "hold", "note": "run 0123456789abcdef0123abcd"}
    (tmp_path / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")

    match = forensics._find_receipt(tmp_path, "0123456789abcdef0123abcd")

    assert match is not None
    assert match[1]["action"] == "hold"


# --- S2-6-7: --limit is bounded and an oversized --since window is clamped ---------------


def test_ops_limit_is_bounded_to_the_web_maximum() -> None:
    assert cli._ops_limit_arg("50") == 50
    with pytest.raises(argparse.ArgumentTypeError):
        cli._ops_limit_arg("5000")
    with pytest.raises(argparse.ArgumentTypeError):
        cli._ops_limit_arg("0")


def test_oversized_relative_since_window_covers_every_event() -> None:
    assert cli._parse_time_arg("99999999999d") == datetime.min.replace(tzinfo=UTC)


# --- S2-10-5: the engine mounts the restore points read-only ----------------------------


def test_engine_unit_mounts_the_backup_path_read_only() -> None:
    lines = _directives(ENGINE_UNIT)

    assert "ReadOnlyPaths=-/opt/parayabuck/data/backups" in lines
    assert "ReadWritePaths=/opt/parayabuck/config /opt/parayabuck/data -/opt/parayabuck/reports" in lines


def test_vm_drop_in_resets_and_mounts_the_home_backup_path_read_only() -> None:
    lines = _directives(VM_PATHS)

    assert "ReadOnlyPaths=" in lines
    assert "ReadOnlyPaths=-/home/batuhankiskac/ParayaBuck/data/backups" in lines


def test_doctor_reports_a_read_only_backup_path_as_a_warning_not_a_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))
    backups = tmp_path / "backups"
    backups.mkdir()
    real_access = os.access

    def _read_only_mount(path: Any, mode: int, **kwargs: Any) -> bool:
        if str(path) == str(backups) and mode & os.W_OK:
            return False
        return real_access(path, mode, **kwargs)

    monkeypatch.setattr(os, "access", _read_only_mount)
    raw = yaml.safe_load(REPO_SETTINGS.read_text(encoding="utf-8"))
    raw["runtime"]["decision_db_path"] = str(tmp_path / "runtime" / "parayabuck.sqlite3")
    raw["backtest"]["output_dir"] = str(tmp_path / "reports")
    raw["backup"]["path"] = str(backups)
    settings = tmp_path / "settings.yaml"
    settings.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    cli.main(["--config", str(settings), "doctor", "--offline"])
    out = capsys.readouterr().out

    assert "WARN backup path writable:" in out
    assert "FAIL backup path writable" not in out
