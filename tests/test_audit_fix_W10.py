"""Audit fix W10: web services, schemas and redaction (regression tests).

Synthetic fixtures only: tmp_path databases and config files, no network,
no credentials, no production paths.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from parayabuck.web.routers.settings import _NoEchoValidationRoute
from parayabuck.web.schemas.settings import LiveConfirmationPayload, SettingsPayload
from parayabuck.web.security import TicketRedactionFilter, install_ticket_log_redaction
from parayabuck.web.services import dashboard as dash
from parayabuck.web.services import ops as ops_svc
from parayabuck.web.services import status as status_svc


def _valid_settings(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"daily_loss_limit_pct": 5.0, "max_drawdown_pct": 20.0}
    body.update(overrides)
    return body


# --- Perf 2: /api/status reads config once per file version and probes health once ----


@dataclass
class _Health:
    state: str
    healthy: bool = True


class _Runtime:
    def __init__(self) -> None:
        self.calls = 0

    def health(self) -> _Health:
        self.calls += 1
        return _Health(state="running")


class _Manager:
    def __init__(self, config_path: str, runtime: Any) -> None:
        self.config_path = config_path
        self.runtime = runtime
        self.state_calls = 0

    def get_credentials(self) -> tuple[str, str]:
        return ("k", "s")

    def current_engine_state(self) -> str:
        self.state_calls += 1
        return "stopped"

    def is_paper_mode(self) -> bool:
        return True


def _cfg_stub() -> Any:
    equity = SimpleNamespace(enabled=False, symbols=[])
    return SimpleNamespace(universe=SimpleNamespace(equity=equity))


def test_status_probes_health_once_and_takes_state_from_it(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "settings.yaml"
    config_path.write_text("runtime: {}\n", encoding="utf-8")
    loads: list[str] = []

    def fake_load(path: str) -> Any:
        loads.append(path)
        return _cfg_stub()

    monkeypatch.setattr(status_svc, "_load_config", fake_load)
    monkeypatch.setattr(status_svc, "strategy_metadata", lambda cfg: {"equity": None})
    runtime = _Runtime()
    manager = _Manager(str(config_path), runtime)

    snapshot = status_svc.build_status_snapshot(manager)

    assert runtime.calls == 1
    assert manager.state_calls == 0
    assert snapshot["state"] == "running"
    assert snapshot["runtime"]["state"] == "running"


def test_status_reuses_config_until_settings_file_changes(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "settings.yaml"
    config_path.write_text("a: 1\n", encoding="utf-8")
    loads: list[str] = []

    def fake_load(path: str) -> Any:
        loads.append(path)
        return _cfg_stub()

    monkeypatch.setattr(status_svc, "_load_config", fake_load)
    monkeypatch.setattr(status_svc, "strategy_metadata", lambda cfg: {"equity": None})
    manager = _Manager(str(config_path), None)

    status_svc.build_status_snapshot(manager)
    status_svc.build_status_snapshot(manager)
    assert len(loads) == 1

    config_path.write_text("a: 2222\n", encoding="utf-8")
    # Bump the mtime explicitly so the test does not depend on filesystem timestamp granularity.
    import os

    stat = config_path.stat()
    os.utime(config_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    status_svc.build_status_snapshot(manager)
    assert len(loads) == 2


# --- Q9-4 / Q9-6 / F9-11: ops reads fail loudly and scope incidents to one account ----


def _incident_db(path) -> SimpleNamespace:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE safety_incidents (id INTEGER PRIMARY KEY, account_id TEXT, reason TEXT, "
            "scope TEXT, activated_at TEXT, updated_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO safety_incidents (account_id, reason, scope, activated_at, updated_at) VALUES (?,?,?,?,?)",
            [
                ("acct-current", "drawdown", "account", "2026-10-26T10:00:00", "2026-10-26T10:00:00"),
                ("acct-old", "ownership_drift", "account", "2026-10-27T10:00:00", "2026-10-27T10:00:00"),
            ],
        )
    return SimpleNamespace(runtime=SimpleNamespace(decision_db_path=str(path)))


def test_incidents_are_scoped_to_the_current_account(tmp_path) -> None:
    config = _incident_db(tmp_path / "ledger.sqlite3")

    rows = ops_svc.get_incidents(10, config, account_id="acct-current")

    assert [row["account_id"] for row in rows] == ["acct-current"]


def test_incidents_read_failure_raises_instead_of_empty_list(tmp_path) -> None:
    broken = tmp_path / "ledger.sqlite3"
    broken.write_bytes(b"this is not an sqlite database" * 100)
    config = SimpleNamespace(runtime=SimpleNamespace(decision_db_path=str(broken)))

    with pytest.raises(ops_svc.OpsReadUnavailable):
        ops_svc.get_incidents(10, config, account_id="acct-current")


def test_ops_incidents_route_answers_503_unavailable_body(tmp_path, monkeypatch) -> None:
    from parayabuck.web.routers import ops as ops_router

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise ops_svc.OpsReadUnavailable("incidents query failed")

    monkeypatch.setattr(ops_router.svc, "get_incidents", boom)
    monkeypatch.setattr(ops_router, "resolve_web_state", lambda request: SimpleNamespace(engine_manager=None))
    monkeypatch.setattr(ops_router.dashboard, "account_id_for", lambda state: "acct-current")
    monkeypatch.setattr(ops_router.svc, "manager_config", lambda manager: None)
    monkeypatch.setattr(ops_router, "get_engine_manager", lambda request: None)
    app = FastAPI()
    app.include_router(ops_router.router)

    response = TestClient(app).get("/api/ops/incidents")

    assert response.status_code == 503
    assert response.json()["unavailable"] is True
    assert response.json()["incidents"] == []


def test_ops_status_error_text_is_generic(monkeypatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret path /var/lib/x")

    import parayabuck.monitoring.health as health_mod

    monkeypatch.setattr(health_mod, "evaluate_health", boom)

    body = ops_svc.get_ops_status(SimpleNamespace(observability=None, runtime=None))

    assert body["error"] == "ops status probe failed"
    assert "secret" not in body["error"]


# --- Q9-6 / D7-9 / F9-3 / F9-9 / F10-6 / Q8-1: dashboard payload pieces --------------


def test_failed_dashboard_panel_does_not_echo_exception_text() -> None:
    def boom() -> dict[str, Any]:
        raise ValueError("internal detail /opt/parayabuck")

    panel = dash._dashboard_safe_panel("risk", boom)

    assert panel == {"unavailable": True, "error": "panel unavailable"}


def test_managed_share_counts_only_bot_quantity() -> None:
    assert dash._managed_share({"total_qty": 10, "managed_qty": 4, "market_value": 100.0}) == (4.0, 40.0)
    assert dash._managed_share({"total_qty": 10, "managed_qty": 10, "market_value": 100.0}) == (10.0, 100.0)
    assert dash._managed_share({"total_qty": 5, "market_value": 50.0}) == (5.0, 50.0)


def _strategy_cfg() -> Any:
    selection = SimpleNamespace(enabled=True, name="trend", maximum_allocation=0.98)
    return SimpleNamespace(strategy=SimpleNamespace(equity=selection))


def test_strategy_card_excludes_external_only_and_prorates_mixed_positions() -> None:
    account = {
        "equity": 1000.0,
        "positions": [
            {"symbol": "QLD", "market": "equity", "total_qty": 10, "managed_qty": 0, "market_value": 300.0},
            {"symbol": "QQQ", "market": "equity", "total_qty": 10, "managed_qty": 5, "market_value": 200.0},
        ],
    }

    card = dash._dashboard_market_card(
        "equity",
        cfg=_strategy_cfg(),
        health=None,
        account=account,
        meta={},
        decision=None,
        engine_state="stopped",
    )

    assert card["positions_count"] == 1
    assert [holding["symbol"] for holding in card["holdings"]] == ["QQQ"]
    assert card["holdings"][0]["market_value"] == pytest.approx(100.0)
    assert card["current_allocation_pct"] == pytest.approx(10.0)


def test_trend_panel_carries_the_last_decision_time() -> None:
    panel = dash._trend_panel({}, {}, {"timestamp": "2026-10-20T20:00:00+00:00"})
    assert panel["as_of"] == "2026-10-20T20:00:00+00:00"


def test_ownership_panel_lists_only_outstanding_adjustments(tmp_path, monkeypatch) -> None:
    db = sqlite3.connect(tmp_path / "ledger.sqlite3")
    db.execute(
        "CREATE TABLE portfolio_ownership_adjustments (adjustment_id TEXT PRIMARY KEY, account_id TEXT, "
        "symbol TEXT, market TEXT, quantity REAL, sign REAL, kind TEXT, note TEXT, created_at TEXT, "
        "consumed_quantity REAL)"
    )
    db.executemany(
        "INSERT INTO portfolio_ownership_adjustments VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("a1", "acct", "QLD", "equity", 10.0, 1.0, "split", "", "2026-10-26", 0.0),
            ("a2", "acct", "QLD", "equity", 4.0, -1.0, "reduction", "", "2026-10-25", 4.0),
            ("a3", "other", "QQQ", "equity", 3.0, -1.0, "reduction", "", "2026-10-24", 0.0),
        ],
    )
    monkeypatch.setattr(dash, "_dashboard_readonly_connection", lambda: db)

    panel = dash._dashboard_ownership("acct")

    assert [(row["symbol"], row["outstanding_quantity"]) for row in panel["adjustments"]] == [("QLD", 10.0)]
    db.close()


def test_health_flags_every_reconciliation_breaker_reason(monkeypatch) -> None:
    monkeypatch.setattr(dash, "_dashboard_restart_count", lambda: 0)
    monkeypatch.setattr(dash, "_dashboard_database_state", lambda: {"exists": False, "size_bytes": None})
    monkeypatch.setattr(dash, "_manager_engine_state", lambda: "halted")
    monkeypatch.setattr(dash, "_dashboard_degraded_reasons", lambda health: [])
    beat = dash.datetime(2026, 10, 27, 12, 0, 0, 500000, tzinfo=dash.UTC)
    health = {
        "circuit_breaker_reason": "protection_failure",
        "workers": {"w1": {"last_heartbeat": beat}, "w2": {"last_heartbeat": beat.replace(microsecond=0)}},
    }

    panel = dash._dashboard_health(health, {})

    assert panel["reconciliation"]["failed"] is True
    assert panel["last_heartbeat"] == beat.isoformat()


# --- S6-4: websockets loggers are redacted too -----------------------------------------


def test_websocket_loggers_get_the_ticket_redaction_filter() -> None:
    install_ticket_log_redaction()
    for name in ("websockets", "websockets.server", "websockets.client"):
        assert any(isinstance(f, TicketRedactionFilter) for f in logging.getLogger(name).filters), name

    record = logging.LogRecord(
        "websockets.server", logging.DEBUG, __file__, 1, "GET /ws/logs?ticket=abc123 HTTP/1.1", None, None
    )
    TicketRedactionFilter().filter(record)
    assert "abc123" not in record.getMessage()


# --- S6-5: settings 422 never echoes the submitted body ---------------------------------


def test_settings_validation_error_does_not_echo_secrets() -> None:
    router = APIRouter(route_class=_NoEchoValidationRoute)

    @router.post("/probe")
    async def probe(payload: SettingsPayload) -> dict[str, str]:
        return {"ok": "yes"}

    app = FastAPI()
    app.include_router(router)
    secret = "SUPER-SECRET-VALUE-123"

    response = TestClient(app).post("/probe", json={"secret_key": secret, "port": 0, "unknown_field": 1})

    assert response.status_code == 422
    assert secret not in response.text
    assert all("input" not in entry for entry in response.json()["detail"])


# --- Q9-9 / D9-7 / Q8-3 / orchestrator add-on: schema bounds and fail-closed defaults ----


def test_settings_schema_omitted_limits_are_not_invented() -> None:
    payload = SettingsPayload.model_validate({})
    assert payload.daily_loss_limit_pct is None
    assert payload.max_drawdown_pct is None
    assert "daily_loss_limit_pct" not in payload.model_fields_set
    with pytest.raises(ValidationError):
        SettingsPayload.model_validate({"daily_loss_limit_pct": None})
    with pytest.raises(ValidationError):
        SettingsPayload.model_validate({"max_drawdown_pct": 0.0})


def test_settings_schema_caps_loss_and_drawdown_percent() -> None:
    assert SettingsPayload.model_validate(_valid_settings(daily_loss_limit_pct=50.0)).daily_loss_limit_pct == 50.0
    with pytest.raises(ValidationError):
        SettingsPayload.model_validate(_valid_settings(daily_loss_limit_pct=50.5))
    with pytest.raises(ValidationError):
        SettingsPayload.model_validate(_valid_settings(max_drawdown_pct=90.5))


def test_settings_schema_bounds_free_text_and_dollar_cap() -> None:
    with pytest.raises(ValidationError):
        SettingsPayload.model_validate(_valid_settings(api_key="x" * 257))
    with pytest.raises(ValidationError):
        SettingsPayload.model_validate(_valid_settings(live_dollar_cap=2_000_000.0))
    with pytest.raises(ValidationError):
        SettingsPayload.model_validate(_valid_settings(live_dollar_cap=float("inf")))
    with pytest.raises(ValidationError):
        LiveConfirmationPayload.model_validate({"phrase": "y" * 257})


def test_settings_save_does_not_restart_the_engine_by_default() -> None:
    payload = SettingsPayload.model_validate(_valid_settings())
    assert payload.restart_engine is False


# --- add-on N-3: tax YTD passes an error status through, neither raising nor caching it ---


def test_tax_error_status_passes_through_and_is_not_cached(monkeypatch) -> None:
    import parayabuck.tax.export as export_mod

    calls: list[str] = []
    payloads = [
        {"status": "error", "error": "defter hatası"},
        {"status": "ok", "income_declaration_status": "veri eksik"},
    ]

    def fake_status(connection: Any, account_id: str, year: int) -> dict[str, Any]:
        calls.append(account_id)
        return dict(payloads[len(calls) - 1])

    monkeypatch.setattr(export_mod, "ytd_status", fake_status)
    monkeypatch.setattr(dash, "_dashboard_readonly_connection", lambda: None)
    dash._dashboard_tax_cache.clear()

    first = dash._dashboard_tax_ytd("acct-tax-n3")
    second = dash._dashboard_tax_ytd("acct-tax-n3")

    assert first["status"] == "error"
    assert second["status"] == "ok"
    assert second["income_declaration_status"] == "veri eksik"
    assert len(calls) == 2  # the error was recomputed, not served from the cache


def test_tax_status_exception_becomes_a_status_payload(monkeypatch) -> None:
    import parayabuck.tax.export as export_mod

    def boom(connection: Any, account_id: str, year: int) -> dict[str, Any]:
        raise RuntimeError("ledger locked")

    monkeypatch.setattr(export_mod, "ytd_status", boom)
    monkeypatch.setattr(dash, "_dashboard_readonly_connection", lambda: None)
    dash._dashboard_tax_cache.clear()

    result = dash._dashboard_tax_ytd("acct-tax-raise")

    assert result["status"] == "error"
    assert "ledger locked" not in str(result)
