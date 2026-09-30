from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from parayabuck.web import server
from parayabuck.web.services import dashboard as svc


def _repo_config(root: Path, *, mode: str = "paper", live_enabled: bool = False) -> Path:
    """Base a settings file on the repo config, flipping mode/live as asked."""
    raw = yaml.safe_load(Path("config/settings.yaml").read_text(encoding="utf-8"))
    raw["runtime"]["mode"] = mode
    raw["runtime"]["decision_db_path"] = str(root / "runtime.sqlite3")
    raw["live"]["enabled"] = live_enabled
    if live_enabled:
        raw["live"]["hard_dollar_cap"] = 1000
        raw["live"]["operator_approval_path"] = str(root / "approval.json")
    config_path = root / "settings.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


def _stub_manager(
    config_path: Path,
    *,
    engine_state: str = "stopped",
    paper: bool = True,
    positions: list[dict[str, Any]] | None = None,
) -> server.EngineManager:
    """EngineManager-shaped stub: no runtime/broker, deterministic account."""

    class _Stub(server.EngineManager):
        def __init__(self) -> None:
            self.config_path = config_path
            self.env_path = config_path.parent / ".env"
            self.engine_state = engine_state
            self.last_engine_error = None
            self.runtime = None
            self.broker = None
            self.security_warning = None
            self.log_queues: set[Any] = set()
            self._log_queues_lock = threading.Lock()
            self._state_lock = threading.Lock()
            self._paper = paper

        def is_paper_mode(self) -> bool:
            return self._paper

        def current_engine_state(self) -> str:
            return self.engine_state

        def fetch_account_snapshot(self) -> dict[str, Any]:
            return {
                "equity": 100000.0,
                "cash": 60000.0,
                "daily_pnl": -120.5,
                "realized_pnl": 500.0,
                "unrealized_pnl": -200.0,
                "drawdown": 1500.0,
                "gross_exposure": 40000.0,
                "positions": positions or [],
                "account_suffix": "abc123",
                "snapshot_at": datetime.now(UTC).isoformat(),
                "age_seconds": 2.0,
                "stale": False,
                "source": "broker",
            }

    return _Stub()


def _install_fake_telemetry_module(monkeypatch: pytest.MonkeyPatch, *, telemetry: bool) -> None:
    """Install (or block) the batch modules the dashboard consumes lazily."""
    import types

    if telemetry:
        fake = types.ModuleType("parayabuck.execution.telemetry")

        def execution_metrics(db_path: str | Path | None = None) -> dict[str, Any]:
            return {"available": True, "orders": 3, "fills": 5}

        def forward_metrics(db_path: str | Path | None = None) -> dict[str, Any]:
            return {"available": True, "decision_count": 1}

        def cost_calibration(db_path: str | Path | None = None, config: object | None = None) -> dict[str, Any]:
            return {"available": True, "median_slippage_bps": 1.0}

        fake.execution_metrics = execution_metrics
        fake.forward_metrics = forward_metrics
        fake.cost_calibration = cost_calibration
        monkeypatch.setitem(sys.modules, "parayabuck.execution.telemetry", fake)
    else:
        monkeypatch.setitem(sys.modules, "parayabuck.execution.telemetry", None)


def _install_fake_metrics_module(monkeypatch: pytest.MonkeyPatch, *, metrics: bool) -> None:
    import types

    if metrics:
        fake = types.ModuleType("parayabuck.monitoring.metrics")

        def runtime_metrics(
            database_path: str | Path | None = None, *, account_id: str | None = None
        ) -> dict[str, Any]:
            return {
                "available": True,
                "restart_count": 2,
                "data_provider_failures": 1,
                "reconciliation_failures": 0,
                "daily_loss_incidents": 0,
                "drawdown_incidents": 1,
                "circuit_breaker_events": 1,
                "entry_block_duration_seconds": 60.0,
            }

        fake.runtime_metrics = runtime_metrics
        monkeypatch.setitem(sys.modules, "parayabuck.monitoring.metrics", fake)
    else:
        monkeypatch.setitem(sys.modules, "parayabuck.monitoring.metrics", None)


# ---------------------------------------------------------------------------
# Overview payload: panels present, PAPER badge, top line
# ---------------------------------------------------------------------------


def test_dashboard_overview_returns_all_panels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))

    overview = asyncio.run(server.dashboard_overview())

    for panel in (
        "mode",
        "account",
        "equity",
        "risk",
        "execution",
        "universe",
        "health",
        "explanations",
        "metrics",
    ):
        assert panel in overview, panel
    assert "crypto" not in overview
    assert overview["engine_state"] == "stopped"

    # Account top line carries every headline number.
    for field in (
        "equity",
        "cash",
        "gross_exposure",
        "daily_pnl",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
        "drawdown",
        "last_reconciliation_at",
    ):
        assert field in overview["account"], field
    assert overview["account"]["total_pnl"] == 300.0


def test_dashboard_paper_badge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path, mode="paper", live_enabled=False)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))

    mode = asyncio.run(server.dashboard_overview())["mode"]
    assert mode["paper"] is True
    assert mode["runtime_mode"] == "paper"
    assert mode["live_enabled"] is False
    assert mode["live_configured"] is False
    assert mode["badge"] == "paper"


def test_dashboard_live_disabled_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A live-configured deployment whose engine is not trading shows LIVE
    clearly disabled, driven by config.live.enabled + runtime.mode."""
    config_path = _repo_config(tmp_path, mode="live", live_enabled=True)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path, engine_state="stopped", paper=False))

    mode = asyncio.run(server.dashboard_overview())["mode"]
    assert mode["paper"] is False
    assert mode["runtime_mode"] == "live"
    assert mode["live_enabled"] is True
    assert mode["live_configured"] is True
    assert mode["live_active"] is False
    assert mode["live_disabled"] is True
    assert mode["badge"] == "live_disabled"


def test_dashboard_live_active_badge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path, mode="live", live_enabled=True)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path, engine_state="running", paper=False))

    mode = asyncio.run(server.dashboard_overview())["mode"]
    assert mode["badge"] == "live"
    assert mode["live_active"] is True
    assert mode["live_disabled"] is False


# ---------------------------------------------------------------------------
# Strategy cards, risk panel, universe and health
# ---------------------------------------------------------------------------


def test_dashboard_strategy_cards_expose_frozen_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))

    overview = asyncio.run(server.dashboard_overview())
    equity = overview["equity"]

    assert equity["name"] == "equity_leveraged_trend_v1"
    assert equity["status"] == "stopped"
    assert equity["strategy"]["display_name"] == "Leveraged index trend v1"
    assert equity["strategy"]["signal_timeframe"] == "1d"
    assert equity["strategy"]["rebalance_cadence"] == "hours:24"
    assert equity["maximum_allocation_pct"] == pytest.approx(98.0)
    assert equity["observation_set_size"] is not None
    assert isinstance(equity["holdings"], list)


def test_dashboard_risk_panel_exposes_policies_and_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))

    risk = asyncio.run(server.dashboard_overview())["risk"]
    assert risk["gross_exposure_pct"] == 40.0
    assert risk["gross_exposure_limit_pct"] == 98.0
    assert risk["cash_pct"] == 60.0
    assert risk["cash_buffer_limit_pct"] == 2.0
    assert not any("crypto" in key for key in risk)
    assert risk["max_drawdown_pct"] == 75.0
    assert risk["daily_loss_limit_pct"] == 20.0
    assert risk["entry_risk_latch"] == {}
    assert risk["circuit_breaker"] is None
    assert isinstance(risk["safety_incidents"], list)
    assert "risk_epoch" in risk
    assert risk["buy_capacity"]["unavailable"] is True


def test_dashboard_risk_exposes_sampled_buy_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))
    monkeypatch.setattr(
        svc,
        "_dashboard_buy_capacity",
        lambda: {
            "positions_count": 5,
            "effective_slots": 6,
            "maximum_positions": 6,
            "broker_working_order_count": 1,
            "available_gross_buy_headroom": 1200.0,
            "committed_buy_notional": 500.0,
            "available_cash_buy_headroom": 1500.0,
            "suppression_reasons": {"equity": "gross_headroom_exhausted"},
            "reconciliation_status": "complete",
        },
    )
    svc._dashboard_snapshot_reset()
    capacity = asyncio.run(server.dashboard_overview())["risk"]["buy_capacity"]
    assert capacity["effective_slots"] == capacity["maximum_positions"] == 6
    assert capacity["broker_working_order_count"] == 1
    assert capacity["committed_buy_notional"] == 500.0
    assert capacity["suppression_reasons"]["equity"] == "gross_headroom_exhausted"


def test_dashboard_universe_and_health_panels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))

    overview = asyncio.run(server.dashboard_overview())
    # Equity is the configured static set: no discovery, no refresh cycle.
    equity = overview["universe"]["equity"]
    assert equity["observation_set"]
    assert equity["observation_set_size"] == len(equity["observation_set"])
    assert equity["scanner_status"] == "static"
    assert "pinned_symbols" in equity

    assert set(overview["universe"]) == {"equity"}

    health = overview["health"]
    assert "workers" in health
    assert "queue" in health
    assert "account_freshness" in health
    assert "reconciliation" in health
    assert "scanner" not in health  # the universe refresh loop was removed
    assert "database" in health
    assert "last_heartbeat" in health
    assert "restart_count" in health
    assert health["database"]["path"].endswith("runtime.sqlite3")


# ---------------------------------------------------------------------------
# Holdings + explanations (per-holding reasons from decision metadata)
# ---------------------------------------------------------------------------


def _equity_decision() -> dict[str, Any]:
    return {
        "timestamp": "2026-08-10T00:00:00+00:00",
        "strategy_name": "equity_leveraged_trend_v1",
        "target": {
            "strategy_name": "equity_leveraged_trend_v1",
            "weights": [
                {"symbol": "QLD", "weight": 0.5, "reason": "trend_on_vol_target"},
            ],
            "cash_weight": 0.5,
            "rebalance_reason": "scheduled",
            "universe_version": "equity-universe-v1",
            "metadata": {
                "qualified_symbols": 2,
                "selected_symbols": ["QLD", "QQQ"],
                "hypothesis": "leveraged index trend: hold QLD above the 200d average",
            },
        },
        "risk": {},
    }


def test_dashboard_holdings_carry_reason_rank_and_weights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path)
    positions = [
        {
            "symbol": "QLD",
            "market": "equity",
            "direction": "LONG",
            "total_qty": 350,
            "qty": 350,
            "market_value": 35050.0,
            "unrealized_pnl": 50.0,
            "protection_state": "protected",
        }
    ]
    monkeypatch.setattr(server, "manager", _stub_manager(config_path, positions=positions))
    monkeypatch.setattr(svc, "_dashboard_market_decisions", lambda _account_id: {"equity": _equity_decision()})

    equity = asyncio.run(server.dashboard_overview())["equity"]
    holding = equity["holdings"][0]
    assert holding["symbol"] == "QLD"
    assert holding["reason"] == "trend_on_vol_target"
    assert holding["rank"] == 1
    assert holding["of_qualified"] == 2
    assert holding["target_weight_pct"] == 50.0
    assert holding["current_weight_pct"] == pytest.approx(35.05)
    assert "realized_vol_22d" not in holding
    assert equity["last_decision_at"] == "2026-08-10T00:00:00+00:00"
    # hours:24 cadence: next boundary = decision + 24 hours
    assert equity["next_rebalance_at"] == "2026-08-11T00:00:00+00:00"


def test_dashboard_explanations_derive_structured_reasons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path)
    positions = [
        {
            "symbol": "QLD",
            "market": "equity",
            "direction": "LONG",
            "total_qty": 350,
            "qty": 350,
            "market_value": 35050.0,
            "unrealized_pnl": 50.0,
        }
    ]
    monkeypatch.setattr(server, "manager", _stub_manager(config_path, positions=positions))
    monkeypatch.setattr(svc, "_dashboard_market_decisions", lambda _account_id: {"equity": _equity_decision()})

    explanations = asyncio.run(server.dashboard_overview())["explanations"]
    assert set(explanations) == {"equity"}
    equity = explanations["equity"]
    assert equity["strategy_name"] == "equity_leveraged_trend_v1"
    assert equity["qualified_symbols"] == 2
    assert equity["selected_symbols"] == ["QLD", "QQQ"]
    assert equity["hypothesis"].startswith("leveraged index trend")
    holding = equity["holdings"][0]
    assert holding["symbol"] == "QLD"
    assert holding["reason"] == "trend_on_vol_target"
    assert holding["rank"] == 1
    assert holding["decision_strategy"] == "equity_leveraged_trend_v1"
    # No debug dumps: every payload must stay JSON-serializable and small.
    payload = json.dumps(explanations)
    assert len(payload) < 4000
    assert "QLD" in payload


# ---------------------------------------------------------------------------
# Metrics: batch modules consumed lazily with explicit degrade
# ---------------------------------------------------------------------------


def test_dashboard_metrics_degrade_to_unavailable_when_modules_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))
    _install_fake_telemetry_module(monkeypatch, telemetry=False)
    _install_fake_metrics_module(monkeypatch, metrics=False)

    metrics = asyncio.run(server.dashboard_overview())["metrics"]
    assert metrics["telemetry"]["unavailable"] is True
    assert metrics["runtime_metrics"]["unavailable"] is True


def test_dashboard_metrics_consume_batch_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))
    _install_fake_telemetry_module(monkeypatch, telemetry=True)
    _install_fake_metrics_module(monkeypatch, metrics=True)

    metrics = asyncio.run(server.dashboard_overview())["metrics"]
    assert metrics["telemetry"]["unavailable"] is False
    assert metrics["telemetry"]["execution_metrics"]["orders"] == 3
    assert metrics["telemetry"]["forward_metrics"]["decision_count"] == 1
    assert metrics["telemetry"]["cost_calibration"]["median_slippage_bps"] == 1.0
    assert metrics["runtime_metrics"]["restart_count"] == 2
    assert metrics["runtime_metrics"]["drawdown_incidents"] == 1
    # The health panel surfaces the same restart count source.
    health = asyncio.run(server.dashboard_overview())["health"]
    assert health["restart_count"] == 2


# ---------------------------------------------------------------------------
# Focused endpoints return slices of the overview payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("endpoint", "panel"),
    [
        ("dashboard_equity", "equity"),
        ("dashboard_risk", "risk"),
        ("dashboard_execution", "execution"),
        ("dashboard_universe", "universe"),
        ("dashboard_health", "health"),
        ("dashboard_explanations", "explanations"),
        ("dashboard_metrics", "metrics"),
    ],
)
def test_focused_endpoints_are_slices(
    endpoint: str, panel: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))
    _install_fake_telemetry_module(monkeypatch, telemetry=True)
    _install_fake_metrics_module(monkeypatch, metrics=True)

    overview = asyncio.run(server.dashboard_overview())
    slice_payload = asyncio.run(getattr(server, endpoint)())
    assert slice_payload == overview[panel]
    assert isinstance(slice_payload, dict)


# ---------------------------------------------------------------------------
# Failure reporting: the real cause must reach the operator
# ---------------------------------------------------------------------------


def test_panel_endpoint_surfaces_the_overview_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing overview must not be reported as a missing panel on all 8 panels."""

    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("ledger locked")

    monkeypatch.setattr(svc, "_dashboard_payload", _boom)

    assert svc.get_overview_payload() == {"unavailable": True, "error": "ledger locked"}
    assert svc.get_panel("equity") == {"unavailable": True, "error": "ledger locked"}


def test_market_decisions_failure_reaches_the_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed decision read must not degrade into a silently empty panel."""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("ledger locked")

    monkeypatch.setattr(svc, "_dashboard_market_decisions", _boom)

    # The server shim delegates to the real reader instead of swallowing.
    with pytest.raises(RuntimeError, match="ledger locked"):
        server._dashboard_market_decisions("acct-1")

    svc._dashboard_snapshot_reset()
    assert svc.get_overview_payload() == {"unavailable": True, "error": "ledger locked"}


# ---------------------------------------------------------------------------
# The HTTP surface still answers, and it answers without duplicating a
# single process-wide object (engine manager / FastAPI app / log handler)
# ---------------------------------------------------------------------------


def test_dashboard_overview_route_answers_from_the_bound_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from parayabuck.web.application import create_app
    from parayabuck.web.managers.backtest import BacktestManager
    from parayabuck.web.state import WebState

    _install_fake_telemetry_module(monkeypatch, telemetry=False)
    _install_fake_metrics_module(monkeypatch, metrics=False)
    config_path = _repo_config(tmp_path)
    engine_manager = _stub_manager(config_path)
    state = WebState(
        engine_manager=engine_manager,
        backtest_manager=BacktestManager(engine_state_provider=engine_manager.current_engine_state),
    )
    state.bind()
    svc._dashboard_snapshot_reset()

    # No ``with`` block: the request must not run the engine lifespan.
    response = TestClient(create_app(state)).get("/api/dashboard/overview")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) >= {
        "equity",
        "risk",
        "execution",
        "universe",
        "health",
        "explanations",
        "metrics",
    }
    assert payload["engine_state"] == "stopped"
    assert payload["mode"]["badge"] == "paper"
    assert payload["account"]["equity"] == 100000.0


_NO_DUPLICATE_SINGLETONS = r'''
import logging
import sys
import tempfile
from pathlib import Path

import yaml
from fastapi import FastAPI

import parayabuck
import parayabuck.monitoring.journal as journal_module
from parayabuck.web.managers.engine import EngineManager

expected_src = Path(sys.argv[1]).resolve()
assert Path(parayabuck.__file__).resolve().is_relative_to(expected_src), parayabuck.__file__

counts = {"engine_manager": 0, "fastapi_app": 0}


def instrument(cls, key):
    original = cls.__init__

    def __init__(self, *args, **kwargs):
        counts[key] += 1
        return original(self, *args, **kwargs)

    cls.__init__ = __init__


instrument(EngineManager, "engine_manager")
instrument(FastAPI, "fastapi_app")
baseline = dict(counts)
app_logger = logging.getLogger("parayabuck")
baseline_handlers = len(app_logger.handlers)


def handler_homes():
    """Every module that still publishes its own log handler class."""
    return sorted(
        name
        for name, module in list(sys.modules.items())
        if name.startswith("parayabuck") and getattr(module, "WebSocketLogHandler", None) is not None
    )


assert handler_homes() == ["parayabuck.web.managers.websocket"], handler_homes()

import parayabuck.web.app  # noqa: E402,F401
import parayabuck.web.services.dashboard as svc  # noqa: E402

assert counts == baseline, f"importing the dashboard built {counts}, baseline {baseline}"
assert len(app_logger.handlers) == baseline_handlers

# Every read below stays inside a temp dir; the optional batch modules stay
# unimported so the payload degrades instead of touching the ledger.
root = Path(tempfile.mkdtemp())
raw = yaml.safe_load(Path("config/settings.yaml").read_text(encoding="utf-8"))
raw["runtime"]["decision_db_path"] = str(root / "runtime.sqlite3")
config_path = root / "settings.yaml"
config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
sys.modules["parayabuck.execution.telemetry"] = None
sys.modules["parayabuck.monitoring.metrics"] = None
journal_module.get_journal = lambda *a, **k: type("J", (), {"path": str(root / "observability.sqlite3")})()


class _StubManager:
    runtime = None
    broker = None
    last_engine_error = None

    def __init__(self):
        self.config_path = config_path

    def is_paper_mode(self):
        return True

    def current_engine_state(self):
        return "stopped"

    def fetch_account_snapshot(self):
        return {
            "equity": 100000.0,
            "cash": 60000.0,
            "daily_pnl": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "drawdown": 0.0,
            "gross_exposure": 0.0,
            "positions": [],
            "account_suffix": "",
            "snapshot_at": None,
            "age_seconds": 0.0,
            "stale": False,
            "source": "broker",
        }


class _State:
    engine_manager = _StubManager()
    backtest_manager = None


payload = svc.get_overview_payload(_State())
assert set(payload) >= {"equity", "risk", "execution", "universe", "health", "explanations", "metrics"}, payload
assert payload["engine_state"] == "stopped"
assert counts == baseline, f"the dashboard build created {counts}, baseline {baseline}"
assert len(app_logger.handlers) == baseline_handlers
assert handler_homes() == ["parayabuck.web.managers.websocket"], handler_homes()
'''


def test_dashboard_import_and_build_construct_no_extra_singletons() -> None:
    """Importing or serving the dashboard must not build a second engine
    manager, FastAPI app or WebSocket log handler: each of those attaches a
    git scan, a route table and a logging fan-out to the process."""
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", _NO_DUPLICATE_SINGLETONS, str(repo / "src")],
        cwd=str(repo),
        env={**os.environ, "PYTHONPATH": str(repo / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stderr
