"""W4 audit-2 regression tests (2026-10-11): web, config, settings findings."""

from __future__ import annotations

import asyncio
import logging
import warnings
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from parayabuck.config import (
    Config,
    EngineConfig,
    RiskLimits,
    StrategyConfig,
    credential_file_mode_warning,
    load_config,
)
from parayabuck.core.models import Market
from parayabuck.web.application import create_app
from parayabuck.web.managers.backtest import _RETAINED_WEB_RUNS, BacktestManager
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.routers import safety as safety_router
from parayabuck.web.schemas.settings import BacktestPayload
from parayabuck.web.security import TicketRedactionFilter
from parayabuck.web.services import dashboard as dashboard_svc
from parayabuck.web.services import ops as ops_svc
from parayabuck.web.services import status as status_svc
from parayabuck.web.services.settings import SettingsService
from parayabuck.web.state import WebState

REPO = Path(__file__).resolve().parents[1]
SHIPPED = REPO / "config" / "settings.yaml"


def _shipped_raw() -> dict[str, Any]:
    return yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))


def _write_shipped(path: Path, **overrides: Any) -> Path:
    raw = _shipped_raw()
    raw["runtime"]["decision_db_path"] = str(path.parent / "runtime.sqlite3")
    raw.update(overrides)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _manager(tmp_path: Path) -> EngineManager:
    manager = EngineManager()
    manager.config_path = _write_shipped(tmp_path / "settings.yaml")
    manager.env_path = tmp_path / ".env"
    manager.engine_state = "stopped"
    return manager


# --- Q1D-01 / Q1D-07a: a corrupt config keeps the display broker on PAPER ---


def _capture_broker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class _Broker:
        def __init__(self, _key: str, _secret: str, *, paper: bool) -> None:
            seen["paper"] = paper

        def account(self) -> Any:
            raise RuntimeError("no account in test")

    import parayabuck.execution.alpaca as alpaca_module

    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _Broker)
    return seen


def test_corrupt_config_selects_paper_display_broker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    manager.config_path.write_text("runtime: [unclosed\n", encoding="utf-8")
    seen = _capture_broker(monkeypatch)
    # The refresh reads the mode once and takes that mode's pair directly.
    manager.get_paper_credentials = lambda: ("key", "secret")  # type: ignore[method-assign]

    manager._refresh_account_snapshot()

    assert seen["paper"] is True
    assert manager.is_paper_mode() is False  # the badge still shows "not paper"


def test_non_mapping_config_selects_paper_display_broker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    manager.config_path.write_text("- just\n- a list\n", encoding="utf-8")
    seen = _capture_broker(monkeypatch)
    manager.get_paper_credentials = lambda: ("key", "secret")  # type: ignore[method-assign]

    manager._refresh_account_snapshot()

    assert seen["paper"] is True


def test_readable_live_config_still_selects_live_display_broker(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    raw = _shipped_raw()
    raw["runtime"]["mode"] = "live"
    manager.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert manager.display_broker_paper() is False
    assert manager.is_paper_mode() is False


# --- Q1D-05: the account snapshot carries one ownership key ---


def test_account_snapshot_has_single_ownership_key(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    position = SimpleNamespace(
        symbol="QLD",
        market=Market.EQUITY,
        qty=2.0,
        managed_qty=2.0,
        external_qty=0.0,
        ownership="managed",
        avg_price=100.0,
        current_price=101.0,
        market_value=202.0,
        unrealized_pnl=2.0,
        is_flat=False,
    )
    account = SimpleNamespace(
        positions=[position],
        equity=1000.0,
        cash=798.0,
        daily_pnl=0.0,
        realized_pnl=0.0,
        unrealized_pnl=2.0,
        fees=0.0,
        peak_equity=1000.0,
    )
    manager.broker = SimpleNamespace(account=lambda: account, account_suffix="1234")

    snapshot = manager._refresh_account_snapshot()

    row = snapshot["positions"][0]
    assert row["ownership_state"] == "managed"
    assert "ownership" not in row


# --- Q1D-03 / Q1D-07c: a paper->paper toggle keeps staged live config ---


def _staged(path: Path, mode: str) -> Path:
    raw = _shipped_raw()
    raw["runtime"]["decision_db_path"] = str(path.parent / "runtime.sqlite3")
    raw["runtime"]["mode"] = mode
    raw["live"].update(
        {"enabled": mode == "live", "hard_dollar_cap": 5000.0, "operator_approval_path": str(path.parent / "a.json")}
    )
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_paper_to_paper_mode_toggle_preserves_staged_live_config(tmp_path: Path) -> None:
    path = _staged(tmp_path / "settings.yaml", "paper")
    safety_router._write_mode(path, True)
    live = yaml.safe_load(path.read_text(encoding="utf-8"))["live"]
    assert live["hard_dollar_cap"] == 5000.0
    assert live["operator_approval_path"] == str(tmp_path / "a.json")
    assert live["enabled"] is False


def test_live_to_paper_mode_toggle_clears_live_config(tmp_path: Path) -> None:
    path = _staged(tmp_path / "settings.yaml", "live")
    safety_router._write_mode(path, True)
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["runtime"]["mode"] == "paper"
    assert saved["live"]["hard_dollar_cap"] == 0.0
    assert saved["live"]["operator_approval_path"] == ""


# --- Q1D-07b: a fresh-cwd events read never creates the default journal ---


def test_query_events_in_fresh_cwd_creates_no_default_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = Config()
    assert ops_svc.query_events(10, config) == []
    assert not (tmp_path / config.observability.journal_path).exists()
    assert not (tmp_path / "data").exists()


# --- Q1A-3: unknown data: keys warn ---


def test_unknown_data_keys_warn(tmp_path: Path) -> None:
    path = _write_shipped(tmp_path / "settings.yaml", data={"equity_sessoin": "regular", "bogus_key": 1})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_config(str(path))
    messages = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
    assert any("bogus_key" in m and "equity_sessoin" in m for m in messages)


def test_removed_data_keys_alone_do_not_warn_as_unknown(tmp_path: Path) -> None:
    path = _write_shipped(tmp_path / "settings.yaml", data={"equity_session": "regular"})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_config(str(path))
    assert not [w for w in caught if "Unknown data settings" in str(w.message)]


# --- Q3E-5: code defaults equal the shipped identity ---


def test_risk_and_strategy_defaults_match_shipped_identity() -> None:
    raw = _shipped_raw()
    defaults = RiskLimits()
    for key, value in raw["risk"].items():
        assert getattr(defaults, key) == pytest.approx(value), key
    equity = StrategyConfig().equity
    shipped = raw["strategy"]["equity"]
    assert equity.name == shipped["name"]
    assert equity.maximum_allocation == pytest.approx(shipped["maximum_allocation"])
    for key, value in equity.parameters.items():
        assert shipped["parameters"][key] == value, key


def test_deleted_identity_keys_load_as_shipped_identity(tmp_path: Path) -> None:
    raw = _shipped_raw()
    raw["runtime"]["decision_db_path"] = str(tmp_path / "runtime.sqlite3")
    del raw["strategy"]["equity"]["maximum_allocation"]
    del raw["risk"]["daily_loss_limit_pct"]
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(str(path))
    assert config.strategy.equity.maximum_allocation == pytest.approx(0.98)
    assert config.risk.daily_loss_limit_pct == pytest.approx(0.20)


# --- Q3E-7: fail-open risk limits and engine retry bounds are rejected ---


@pytest.mark.parametrize("field_name", ["daily_loss_limit_pct", "max_drawdown_pct"])
def test_full_loss_latch_is_rejected(field_name: str) -> None:
    with pytest.raises(ValueError, match="below 1"):
        RiskLimits(**{field_name: 1.0})


def test_retry_interval_longer_than_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="decision_retry_interval_seconds"):
        EngineConfig(decision_retry_interval_seconds=3600.0, decision_retry_window_minutes=40.0)


# --- Q3E-10: every engine knob is visible in the shipped yaml ---


def test_shipped_engine_block_lists_every_engine_knob_at_its_default() -> None:
    engine = _shipped_raw()["engine"]
    defaults = EngineConfig()
    for item in fields(EngineConfig):
        assert item.name in engine, item.name
        assert engine[item.name] == getattr(defaults, item.name), item.name


def test_shipped_settings_still_load() -> None:
    config = load_config(str(SHIPPED))
    assert config.engine == EngineConfig()
    assert config.risk == RiskLimits()


# --- Q5D-1: no upward .env search ---


def _record_dotenv(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Record load_config's dotenv calls (the conftest guard would hide a bare one)."""
    import parayabuck.config as config_module

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **_kwargs: calls.append(args) or True)
    return calls


def test_load_config_never_searches_upward_for_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plant = tmp_path / "plant"
    sub = plant / "sub"
    sub.mkdir(parents=True)
    (plant / ".env").write_text("CONFIG_PATH=/nonexistent/evil.yaml\n", encoding="utf-8")
    config_path = _write_shipped(sub / "settings.yaml")
    monkeypatch.delenv("ENV_PATH", raising=False)
    monkeypatch.chdir(sub)
    calls = _record_dotenv(monkeypatch)

    load_config(str(config_path))

    # No .env in the working directory: nothing is loaded, and a bare
    # (upward-searching) load_dotenv() call is never made.
    assert calls == []


def test_load_config_loads_only_the_explicit_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    config_path = _write_shipped(tmp_path / "settings.yaml")
    monkeypatch.delenv("ENV_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    calls = _record_dotenv(monkeypatch)

    load_config(str(config_path))

    assert calls == [(Path(".env"),)]


# --- Q5D-2 / Q5D-3: remote peers get no token and no live checklist ---


def _client(manager: EngineManager, peer: str) -> TestClient:
    state = WebState(engine_manager=manager, backtest_manager=BacktestManager())
    return TestClient(create_app(state), raise_server_exceptions=False, client=(peer, 40000))


@pytest.mark.parametrize("page", ["/", "/tools"])
def test_pages_embed_token_only_for_trusted_peer(tmp_path: Path, page: str) -> None:
    manager = _manager(tmp_path)
    remote = _client(manager, "8.8.8.8").get(page, headers={"Host": "127.0.0.1"})
    local = _client(manager, "127.0.0.1").get(page, headers={"Host": "127.0.0.1"})
    assert remote.status_code == local.status_code == 200
    assert manager.control_token not in remote.text
    assert manager.control_token in local.text


def test_live_prerequisites_refuses_remote_peer(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    remote = _client(manager, "8.8.8.8").get("/api/live/prerequisites", headers={"Host": "127.0.0.1"})
    assert remote.status_code == 403
    local = _client(manager, "127.0.0.1").get("/api/live/prerequisites", headers={"Host": "127.0.0.1"})
    assert local.status_code == 200


# --- Q5D-4: backtest input bounds ---


def test_backtest_argv_rejects_too_many_parameters() -> None:
    with pytest.raises(ValueError, match="Too many parameters"):
        BacktestManager._build_argv({"parameters": {f"p{i}": i for i in range(65)}}, "out")


def test_backtest_argv_rejects_oversized_inputs() -> None:
    with pytest.raises(ValueError, match="Symbols list is too long"):
        BacktestManager._build_argv({"symbols": "A," * 3000}, "out")
    with pytest.raises(ValueError, match="Strategy name is too long"):
        BacktestManager._build_argv({"strategy": "s" * 200}, "out")
    big = {f"p{i}": "x" * 4000 for i in range(5)}
    with pytest.raises(ValueError, match="too large in total"):
        BacktestManager._build_argv({"parameters": big}, "out")


def test_backtest_schema_bounds_inputs() -> None:
    with pytest.raises(ValidationError):
        BacktestPayload(strategy="s" * 129)
    with pytest.raises(ValidationError):
        BacktestPayload(symbols="A" * 4097)
    with pytest.raises(ValidationError):
        BacktestPayload(parameters={f"p{i}": i for i in range(65)})
    assert BacktestPayload(parameters={"sma_bars": 200}).parameters == {"sma_bars": 200}


# --- Q2D-5: old reports/web-* runs are pruned on start ---


def test_web_backtest_start_prunes_oldest_runs(tmp_path: Path) -> None:
    root = tmp_path / "reports"
    names = [f"web-202601{day:02d}T000000Z-{day:08x}" for day in range(1, _RETAINED_WEB_RUNS + 6)]
    for name in names:
        (root / name).mkdir(parents=True)
    (root / "keep-me").mkdir()
    manager = BacktestManager(engine_state_provider=lambda: "stopped")
    manager.reports_root = root

    class _NoThread:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

    manager._thread_factory = _NoThread  # type: ignore[assignment]
    manager.start({"market": "equity"})

    remaining = sorted(entry.name for entry in root.iterdir() if entry.name.startswith("web-"))
    assert remaining == names[-(_RETAINED_WEB_RUNS - 1) :]
    assert (root / "keep-me").is_dir()
    assert manager.output_dir == str(root / manager.run_id)


# --- Q3D-5: next check follows the NYSE calendar ---


def test_next_check_skips_the_weekend() -> None:
    friday = datetime(2026, 8, 7, 13, 30, tzinfo=UTC).isoformat()
    assert dashboard_svc._dashboard_next_rebalance_at(friday, "hours:24") == "2026-08-10T20:00:00+00:00"


def test_next_check_uses_the_early_close() -> None:
    # Wednesday before Thanksgiving 2026 -> Friday 2026-11-27 closes 13:00 ET.
    wednesday = datetime(2026, 11, 25, 14, 30, tzinfo=UTC).isoformat()
    assert dashboard_svc._dashboard_next_rebalance_at(wednesday, "hours:24") == "2026-11-27T18:00:00+00:00"


# --- Q4D-02 / Q4D-05: unread status/settings keys are gone ---


def test_status_snapshot_drops_unread_keys(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.get_credentials = lambda: ("", "")  # type: ignore[method-assign]
    status = status_svc.build_status_snapshot(manager)
    for key in ("strategy_status", "active_equity_symbol_count", "ownership_drift_active", "reconciliation_anomalies"):
        assert key not in status, key
    assert "pool_size" not in status["universe"]["equity"]
    meta = status["strategy_metadata"]["equity"]
    assert "supported_timeframes" not in meta and "dynamic_universe_enabled" not in meta


def test_settings_get_returns_only_config_digest(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.get_credentials = lambda: ("", "")  # type: ignore[method-assign]
    payload = asyncio.run(SettingsService(manager).get_settings())
    assert payload["config_digest"]
    assert "config_version" not in payload


# --- Q5D-5: WebSocket tickets are redacted from server logs ---


def test_ticket_is_redacted_from_uvicorn_log_records() -> None:
    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" [accepted]',
        ("1.2.3.4", "/ws/stream?ticket=abc-123_x"),
        None,
    )
    assert TicketRedactionFilter().filter(record) is True
    message = record.getMessage()
    assert "abc-123_x" not in message
    assert "/ws/stream?ticket=[redacted]" in message


def test_create_app_installs_ticket_redaction(tmp_path: Path) -> None:
    _client(_manager(tmp_path), "127.0.0.1")
    for name in ("uvicorn.error", "uvicorn.access"):
        filters = [f for f in logging.getLogger(name).filters if isinstance(f, TicketRedactionFilter)]
        assert len(filters) == 1, name


# --- Q5D-6: a group/world-readable credential file is reported ---


def test_loose_credential_file_is_warned(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=k\nALPACA_SECRET_KEY=s\n", encoding="utf-8")
    env.chmod(0o644)
    assert credential_file_mode_warning(env) is not None
    manager = _manager(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert manager.get_credentials() == ("k", "s")
        manager.get_credentials()
    warned = [r for r in caplog.records if "accessible by group/other" in r.getMessage()]
    assert len(warned) == 1  # once per path and mode, not per poll
    assert manager.security_warning is None  # a warning, never a start refusal
    env.chmod(0o600)
    assert credential_file_mode_warning(env) is None
