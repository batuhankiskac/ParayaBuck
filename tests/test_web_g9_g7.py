from __future__ import annotations

import asyncio
import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

import parayabuck.execution.alpaca as alpaca_module
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import portfolio_risk_limits_from_config
from parayabuck.live_safety import LiveOperatorApproval
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.runtime_service import configuration_digest
from parayabuck.universe import load_active_crypto_symbols, load_active_symbols
from parayabuck.universe.crypto_scanner import crypto_scanner_config_digest
from parayabuck.universe.scanner import scanner_config_digest
from parayabuck.utils.io import atomic_write
from parayabuck.web import app as web_app
from parayabuck.web import server


def _live_config(root: Path) -> Path:
    """Copy the repo config and flip it to a valid live configuration."""
    raw = yaml.safe_load(Path("config/settings.yaml").read_text(encoding="utf-8"))
    raw["runtime"]["mode"] = "live"
    raw["runtime"]["decision_db_path"] = str(root / "runtime.sqlite3")
    raw["runtime"]["scanner_refresh_hours"] = 8760
    raw["live"]["enabled"] = True
    raw["live"]["hard_dollar_cap"] = 1000
    raw["live"]["operator_approval_path"] = str(root / "approval.json")
    config_path = root / "settings.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


def _expected_digests(config_path: Path) -> tuple[str, ...]:
    cfg = load_config(str(config_path))
    digests: list[str] = []
    for market, selection, symbols, universe_version in (
        (
            Market.EQUITY,
            cfg.strategy.equity,
            load_active_symbols(cfg.universe.equity, log_rejection=False),
            scanner_config_digest(cfg.universe.equity),
        ),
        (
            Market.CRYPTO,
            cfg.strategy.crypto,
            load_active_crypto_symbols(cfg.universe.crypto, log_rejection=False),
            crypto_scanner_config_digest(cfg.universe.crypto),
        ),
    ):
        if not selection.enabled or not symbols:
            continue
        strategy = portfolio_strategy_from(selection.name, selection.parameters)
        digests.append(
            configuration_digest(
                strategy,
                "live",
                selection.maximum_allocation,
                universe_version,
                portfolio_risk_limits_from_config(cfg.risk),
            )
        )
    return tuple(digests)


def _write_approval(root: Path, *, account_id: str, key: str, digests: tuple[str, ...]) -> None:
    approval = LiveOperatorApproval(
        operator_id="operator-1",
        account_id=account_id,
        configuration_digests=digests,
        maximum_dollar_exposure=1000,
        approved_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        nonce="n" * 16,
    ).signed(key)
    atomic_write(
        root / "approval.json",
        json.dumps(approval.to_dict(), indent=2, sort_keys=True),
        mode=0o600,
    )


def _manager(root: Path) -> server.EngineManager:
    manager = server.EngineManager()
    manager.config_path = root / "settings.yaml"
    manager.env_path = root / ".env"
    # Never touch the network in tests: credentials are stubbed per-test and
    # the broker probe is mocked below where it is exercised.
    manager.get_credentials = lambda: ("pk-fake-key", "fake-secret-value")  # type: ignore[method-assign]
    return manager


class _FakeLiveBroker:
    account_id = "acct-live-1"

    def __init__(self, key: str, secret: str, paper: bool) -> None:
        pass

    def account(self) -> object:
        return object()

    def close(self) -> None:
        pass


class _UnreachableBroker:
    def __init__(self, key: str, secret: str, paper: bool) -> None:
        raise ConnectionError("live endpoint unreachable")


# --- G9: web bind failure must not orphan the engine -------------------------


def test_probe_web_bind_succeeds_on_free_port() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    web_app._probe_web_bind("127.0.0.1", port)  # must not raise


def test_probe_web_bind_fails_fast_on_occupied_port() -> None:
    occupied = socket.socket()
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    try:
        with pytest.raises(SystemExit) as excinfo:
            web_app._probe_web_bind("127.0.0.1", port)
        message = str(excinfo.value)
        assert "cannot start" in message
        assert "engine was NOT started" in message
        assert "127.0.0.1" in message and str(port) in message
    finally:
        occupied.close()


def test_probe_web_bind_resolves_localhost() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    web_app._probe_web_bind("localhost", port)  # must not raise


# --- G7: live-arming prerequisites checklist ---------------------------------


def test_live_prerequisites_default_config_blocks_live(tmp_path: Path, monkeypatch: Any) -> None:
    root = tmp_path
    (root / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "app": {"auto_start_engine": True},
                "runtime": {"mode": "paper"},
                "live": {"enabled": False, "hard_dollar_cap": 0, "operator_approval_path": ""},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("PARAYABUCK_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", raising=False)

    result = _manager(root).live_prerequisites()
    assert result["all_met"] is False
    by_id = {entry["id"]: entry for entry in result["items"]}
    for required in (
        "config_live_enabled",
        "runtime_mode_live",
        "live_cap",
        "enable_live_env",
        "approval_file",
        "approval_key",
        "approval_signature",
        "approval_binding",
        "web_arm_token",
    ):
        assert by_id[required]["satisfied"] is False, required
        assert by_id[required]["missing"], required
    # engine is idle for a fresh manager: the one thing that is already met.
    assert by_id["engine_stopped"]["satisfied"] is True
    # No secret values may appear in the payload.
    payload = json.dumps(result)
    assert "pk-fake-key" not in payload
    assert "fake-secret-value" not in payload


def test_live_prerequisites_live_config_static_checks(tmp_path: Path, monkeypatch: Any) -> None:
    root = tmp_path
    config_path = _live_config(root)
    key = "k" * 40
    _write_approval(root, account_id="acct-live-1", key=key, digests=("d1", "d2"))
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", key)
    # Live endpoint is not reachable with the stubbed credentials: the
    # account/config binding cannot be verified and must be reported as such.
    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _UnreachableBroker)

    result = _manager(root).live_prerequisites()
    by_id = {entry["id"]: entry for entry in result["items"]}
    for required in (
        "config_live_enabled",
        "runtime_mode_live",
        "live_cap",
        "enable_live_env",
        "approval_file",
        "approval_key",
        "approval_signature",
        "credentials",
        "engine_stopped",
    ):
        assert by_id[required]["satisfied"] is True, (required, by_id[required])
    assert by_id["approval_binding"]["satisfied"] is False
    assert by_id["approval_binding"]["state"] == "unverifiable"
    assert "account binding" in by_id["approval_binding"]["missing"]
    # The operator phrase confirmation is still required in this session.
    assert by_id["web_arm_token"]["satisfied"] is False
    assert config_path.exists()


def test_live_prerequisites_binding_verified_when_account_and_digests_match(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path
    config_path = _live_config(root)
    key = "k" * 40
    digests = _expected_digests(config_path)
    _write_approval(root, account_id="acct-live-1", key=key, digests=digests)
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", key)
    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _FakeLiveBroker)

    result = _manager(root).live_prerequisites()
    by_id = {entry["id"]: entry for entry in result["items"]}
    assert by_id["approval_binding"]["satisfied"] is True, by_id["approval_binding"]
    assert by_id["approval_signature"]["satisfied"] is True
    # Everything is ready except the in-session phrase confirmation.
    unmet = [entry["id"] for entry in result["items"] if not entry["satisfied"]]
    assert unmet == ["web_arm_token"]


def test_live_prerequisites_binding_rejects_wrong_account(tmp_path: Path, monkeypatch: Any) -> None:
    root = tmp_path
    config_path = _live_config(root)
    key = "k" * 40
    _write_approval(
        root, account_id="acct-other-9", key=key, digests=_expected_digests(config_path)
    )
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", key)
    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _FakeLiveBroker)

    result = _manager(root).live_prerequisites()
    by_id = {entry["id"]: entry for entry in result["items"]}
    assert by_id["approval_binding"]["satisfied"] is False
    assert by_id["approval_binding"]["state"] == "missing"
    assert "different Alpaca account" in by_id["approval_binding"]["missing"]


def test_live_prerequisites_binding_rejects_stale_digests(tmp_path: Path, monkeypatch: Any) -> None:
    root = tmp_path
    _live_config(root)  # writes settings.yaml (side effect)
    key = "k" * 40
    _write_approval(root, account_id="acct-live-1", key=key, digests=("stale-digest",))
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", key)
    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _FakeLiveBroker)

    result = _manager(root).live_prerequisites()
    by_id = {entry["id"]: entry for entry in result["items"]}
    assert by_id["approval_binding"]["satisfied"] is False
    assert by_id["approval_binding"]["state"] == "missing"
    assert "configuration digests" in by_id["approval_binding"]["missing"]


def test_live_prerequisites_route_returns_checklist(monkeypatch: Any) -> None:
    class _StubManager:
        def live_prerequisites(self) -> dict[str, Any]:
            return {
                "all_met": False,
                "items": [{"id": "x", "satisfied": False}],
                "config_error": None,
            }

    monkeypatch.setattr(server, "manager", _StubManager())
    result = asyncio.run(server.live_prerequisites())
    assert result["all_met"] is False
    assert result["items"] == [{"id": "x", "satisfied": False}]
