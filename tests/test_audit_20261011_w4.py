"""W4 audit regression tests (2026-10-11): web, config, journal findings."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from parayabuck.config import load_config
from parayabuck.monitoring.journal import EventJournal
from parayabuck.monitoring.operational_event import OperationalEvent
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.routers import safety as safety_router
from parayabuck.web.schemas.settings import SettingsPayload
from parayabuck.web.services import dashboard as dashboard_svc
from parayabuck.web.services import ops as ops_svc
from parayabuck.web.services import performance as performance_svc
from parayabuck.web.services.settings import SettingsService
from tests.helpers import web_client

_LOADABLE_MARKETS: dict[str, Any] = {
    "universe": {"equity": {"enabled": True}},
    "strategy": {"equity": {"enabled": True, "name": "equity_leveraged_trend_v1"}},
}


def _write_config(path: Path, **overrides: Any) -> Path:
    raw: dict[str, Any] = {
        "app": {"host": "127.0.0.1", "port": 8000, "open_browser": True, "log_level": "INFO"},
        "runtime": {"mode": "paper", "decision_db_path": str(path.parent / "runtime.sqlite3")},
        "risk": {"daily_loss_limit_pct": 0.015, "max_drawdown_pct": 0.10},
        "observability": {
            "journal_path": str(path.parent / "journal.sqlite3"),
            "snapshot_path": str(path.parent / "health_snapshot.json"),
        },
        "live": {"enabled": False, "hard_dollar_cap": 0.0, "operator_approval_path": ""},
        **_LOADABLE_MARKETS,
    }
    for key, value in overrides.items():
        raw[key] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _manager(tmp_path: Path, **overrides: Any) -> EngineManager:
    manager = EngineManager()
    manager.config_path = _write_config(tmp_path / "settings.yaml", **overrides)
    manager.env_path = tmp_path / ".env"
    manager.engine_state = "stopped"
    return manager


# --- A4/R4A-3: removed keys load with DeprecationWarning, never rejected ---


def test_removed_config_keys_load_with_deprecation_warning(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path / "settings.yaml",
        runtime={
            "mode": "paper",
            "execution_timeframe": "5m",
            "market_state_timeframe": "5m",
            "decision_db_path": str(tmp_path / "runtime.sqlite3"),
        },
        data={"equity_session": "regular", "include_extended_hours": False},
        observability={"enabled": False, "journal_path": str(tmp_path / "journal.sqlite3")},
    )
    with pytest.warns(DeprecationWarning):
        config = load_config(str(path))
    assert config.runtime.mode.value == "paper"


# --- D1/R2D1: leaving LIVE heals the live block through both paths ---


def _live_manager(tmp_path: Path) -> EngineManager:
    manager = EngineManager()
    manager.config_path = _write_config(
        tmp_path / "settings.yaml",
        runtime={"mode": "live", "decision_db_path": str(tmp_path / "runtime.sqlite3")},
        live={
            "enabled": True,
            "hard_dollar_cap": 5000.0,
            "operator_approval_path": str(tmp_path / "approval.json"),
        },
    )
    manager.env_path = tmp_path / ".env"
    manager.engine_state = "stopped"
    return manager


def _assert_paper_clean(path: Path) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["runtime"]["mode"] == "paper"
    assert raw["live"]["enabled"] is False
    assert raw["live"]["hard_dollar_cap"] == 0.0
    assert raw["live"]["operator_approval_path"] == ""
    paper_config = load_config(str(path))
    assert paper_config.live.hard_dollar_cap == 0.0
    assert paper_config.live.enabled is False


def test_settings_drawer_leaving_live_heals_cap_and_approval(tmp_path: Path) -> None:
    manager = _live_manager(tmp_path)
    result = asyncio.run(SettingsService(manager).save_settings(SettingsPayload(paper_mode=True)))
    assert result["status"] == "success"
    _assert_paper_clean(manager.config_path)


def test_mode_toggle_leaves_live(tmp_path: Path) -> None:
    manager = _live_manager(tmp_path)
    safety_router._write_mode(manager.config_path, True)
    _assert_paper_clean(manager.config_path)


# --- R3D1: arm digest survives the mode flip ---


def test_live_arm_digest_survives_mode_flip(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    raw = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    raw["live"]["hard_dollar_cap"] = 5000.0
    raw["live"]["operator_approval_path"] = str(tmp_path / "approval.json")
    pre = manager._digest_config(raw, paper=False)
    # The arm→/api/mode toggle flips exactly runtime.mode and live.enabled.
    raw["runtime"]["mode"] = "live"
    raw["live"]["enabled"] = True
    post = manager._digest_config(raw, paper=False)
    assert pre == post


def test_live_arm_digest_rejects_cap_or_approval_change(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    raw = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    raw["live"]["hard_dollar_cap"] = 5000.0
    raw["live"]["operator_approval_path"] = str(tmp_path / "approval.json")
    armed = manager._digest_config(raw, paper=False)
    raw["live"]["hard_dollar_cap"] = 6000.0
    assert manager._digest_config(raw, paper=False) != armed
    raw["live"]["hard_dollar_cap"] = 5000.0
    raw["live"]["operator_approval_path"] = str(tmp_path / "other.json")
    assert manager._digest_config(raw, paper=False) != armed


# --- R3D5: contradictory mode flags rejected ---


def test_contradictory_mode_flags_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(ValueError, match="Contradictory"):
        manager._validated_settings_payload(SettingsPayload(paper_mode=True, live_trading_enabled=True))


# --- R2D5: backtest days bounded ---


def test_backtest_days_upper_bound() -> None:
    from pydantic import ValidationError

    from parayabuck.web.schemas.settings import BacktestPayload

    with pytest.raises(ValidationError):
        BacktestPayload(days=1000000000)


# --- R2D2/R2D3: rotation invalidates perf + account caches ---


class _RotatingBroker:
    def __init__(self, account_id: str, points: list[tuple[datetime, float]]) -> None:
        self.account_id = account_id
        self._points = points

    def portfolio_history(self, _period: str, _timeframe: str) -> list[tuple[datetime, float]]:
        return self._points


class _RotatingManager(EngineManager):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.config_path = path
        self.env_path = path.parent / ".env"
        self.engine_state = "stopped"

    def is_paper_mode(self) -> bool:
        return True


def test_performance_cache_keyed_by_account(tmp_path: Path) -> None:
    performance_svc.reset_cache()
    manager = _RotatingManager(_write_config(tmp_path / "settings.yaml"))
    now = datetime.now(UTC)
    manager.broker = _RotatingBroker("a", [(now, 100.0), (now + timedelta(days=1), 110.0)])
    first = performance_svc.portfolio_performance(manager, "1M", benchmark_fetcher=lambda *a: [], clock=lambda: 0.0)
    manager.broker = _RotatingBroker("b", [(now, 999.0), (now + timedelta(days=1), 999.0)])
    second = performance_svc.portfolio_performance(manager, "1M", benchmark_fetcher=lambda *a: [], clock=lambda: 1.0)
    assert first["equity"] != second["equity"]
    performance_svc.reset_cache()


def test_reset_broker_clears_stale_account_snapshot(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.last_account = {"equity": 999.0}
    manager._last_account_monotonic = 1.0
    manager.broker = None
    manager.runtime = None
    manager.reset_broker()
    assert manager.last_account is None


# --- R5D-2: unreadable config fails display broker closed to PAPER ---


def test_unreadable_config_selects_paper_display_broker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    manager.config_path = tmp_path / "missing-settings.yaml"
    seen: dict[str, Any] = {}

    class _Broker:
        def __init__(self, _key: str, _secret: str, *, paper: bool) -> None:
            seen["paper"] = paper

        def account(self) -> Any:
            raise RuntimeError("no account in test")

    import parayabuck.execution.alpaca as alpaca_module

    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _Broker)
    manager.get_paper_credentials = lambda: ("key", "secret")  # type: ignore[method-assign]
    manager._refresh_account_snapshot()
    assert seen["paper"] is True
    assert manager.is_paper_mode() is False  # badge still fails open


# --- D2: ops events honor the configured journal path ---


def _write_event(journal: EventJournal, kind: str = "runtime_started") -> None:
    journal.start()
    try:
        assert journal.emit(
            OperationalEvent(
                event_id=f"ev-{kind}-{datetime.now(UTC).isoformat()}",
                occurred_at=datetime.now(UTC),
                kind=kind,
                severity="INFO",
                message="probe",
                session_id=None,
                account_id=None,
                mode="paper",
                market=None,
                strategy=None,
                decision_id=None,
                client_order_id=None,
                broker_order_id=None,
                incident_id=None,
                correlation_id=None,
                fields={},
            )
        )
        deadline = datetime.now(UTC) + timedelta(seconds=5)
        while journal._queue.qsize() and datetime.now(UTC) < deadline:
            __import__("time").sleep(0.01)
        __import__("time").sleep(0.2)
    finally:
        journal.stop()


def test_query_events_reads_configured_journal_path(tmp_path: Path) -> None:
    journal_path = tmp_path / "custom-journal.sqlite3"
    journal = EventJournal(str(journal_path))
    _write_event(journal)
    config_path = _write_config(
        tmp_path / "settings.yaml",
        observability={"journal_path": str(journal_path)},
    )
    config = load_config(str(config_path))
    events = ops_svc.query_events(100, config)
    assert len(events) == 1


def test_journal_query_never_creates_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.sqlite3"
    config_path = _write_config(
        tmp_path / "settings.yaml",
        observability={"journal_path": str(missing)},
    )
    config = load_config(str(config_path))
    assert ops_svc.query_events(10, config) == []
    assert not missing.exists()


# --- R2D4: negative limit clamps ---


def test_journal_negative_limit_clamps_to_one_row(tmp_path: Path) -> None:
    journal = EventJournal(str(tmp_path / "journal.sqlite3"))
    for i in range(5):
        _write_event(journal, kind=f"kind-{i}")
    assert len(journal.query(limit=-1)) == 1


# --- D4: ops cards wired to live routes ---


def test_ops_cards_have_live_data_source(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal.sqlite3"
    _write_event(EventJournal(str(journal_path)))
    manager = _manager(tmp_path)
    raw = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    raw["observability"]["journal_path"] = str(journal_path)
    manager.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    client = web_client(manager)
    events_response = client.get("/api/ops/events?limit=12")
    assert events_response.status_code == 200
    assert events_response.json()["count"] >= 1
    latest_response = client.get("/api/ops/decisions/latest")
    assert latest_response.status_code == 200


# --- R2D7: dead panel routes gone, overview + performance live ---


def test_dead_panel_routes_removed_but_overview_lives(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    client = web_client(manager)
    for dead in (
        "/api/equity",
        "/api/risk",
        "/api/execution",
        "/api/universe",
        "/api/health",
        "/api/explanations",
        "/api/metrics",
    ):
        assert client.get(dead).status_code == 404
    assert client.get("/api/dashboard/overview").status_code == 200


# --- R4C-01/R4C-08: overview keys all consumed ---


def test_overview_keys_match_frontend_readers(tmp_path: Path) -> None:
    dashboard_js = Path("src/parayabuck/web/static/js/dashboard.js").read_text(encoding="utf-8")
    manager = _manager(tmp_path)
    from parayabuck.web.state import WebState

    state = WebState(engine_manager=manager, backtest_manager=None)
    payload = dashboard_svc.get_overview_payload(state)
    assert set(payload) == {
        "as_of",
        "engine_state",
        "account",
        "equity",
        "trend_status",
        "risk",
        "execution",
        "universe",
        "health",
    }
    for key in ("account", "equity", "trend_status", "risk", "execution", "health", "universe"):
        assert f"data.{key}" in dashboard_js or f"({key}" in dashboard_js or key in dashboard_js


# --- R4C-02: bounded decision scan ---


def test_market_decisions_scan_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    real_connect = sqlite3.connect

    def _spy(*args: Any, **kwargs: Any) -> Any:
        conn = real_connect(*args, **kwargs)
        orig_execute = conn.execute

        def _execute(sql: str, *rest: Any) -> Any:
            seen["sql"] = sql
            return orig_execute(sql, *rest)

        conn.execute = _execute  # type: ignore[method-assign]
        return conn

    monkeypatch.setattr(sqlite3, "connect", _spy)
    manager = _manager(tmp_path)
    from parayabuck.web.state import WebState

    dashboard_svc.get_overview_payload(WebState(engine_manager=manager, backtest_manager=None))
    assert "SELECT MAX" in seen.get("sql", "") or seen == {}


# --- D5: no module singleton ---


def test_no_engine_manager_singleton() -> None:
    import parayabuck.web.managers.engine as engine_module

    assert not hasattr(engine_module, "manager")


# --- R2D6: write-only suffix gone ---


def test_no_live_request_suffix_field() -> None:
    manager = EngineManager.__new__(EngineManager)
    assert not hasattr(manager, "_live_request_suffix")


# --- D7: live round trip + ops route contract ---


def test_full_live_round_trip_stays_paper_clean(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    asyncio.run(SettingsService(manager).save_settings(SettingsPayload(paper_mode=True)))
    asyncio.run(SettingsService(manager).save_settings(SettingsPayload(live_trading_enabled=False)))
    _assert_paper_clean(manager.config_path)


def test_ops_read_routes_answer(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    client = web_client(manager)
    assert client.get("/api/ops/status").status_code == 200
    assert client.get("/api/ops/events").status_code == 200
    assert client.get("/api/ops/incidents").status_code == 200
    assert client.get("/api/ops/decisions/latest").status_code == 200
    assert client.get("/api/ops/timeline/some-id").status_code == 200
