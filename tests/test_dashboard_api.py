from __future__ import annotations

import asyncio
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from parayabuck.web import server


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

        def cost_calibration(
            db_path: str | Path | None = None, config: object | None = None
        ) -> dict[str, Any]:
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
                "scanner_failures": 1,
                "reconciliation_failures": 0,
                "data_gap_events": 3,
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


def test_dashboard_overview_returns_all_panels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))

    overview = asyncio.run(server.dashboard_overview())

    for panel in (
        "mode",
        "account",
        "equity",
        "crypto",
        "risk",
        "execution",
        "universe",
        "health",
        "explanations",
        "metrics",
    ):
        assert panel in overview, panel
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
    monkeypatch.setattr(
        server, "manager", _stub_manager(config_path, engine_state="stopped", paper=False)
    )

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
    monkeypatch.setattr(
        server, "manager", _stub_manager(config_path, engine_state="running", paper=False)
    )

    mode = asyncio.run(server.dashboard_overview())["mode"]
    assert mode["badge"] == "live"
    assert mode["live_active"] is True
    assert mode["live_disabled"] is False


# ---------------------------------------------------------------------------
# Strategy cards, risk panel, universe and health
# ---------------------------------------------------------------------------


def test_dashboard_strategy_cards_expose_frozen_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))

    overview = asyncio.run(server.dashboard_overview())
    equity = overview["equity"]
    crypto = overview["crypto"]

    assert equity["name"] == "equity_residual_momentum_v2"
    assert equity["status"] == "stopped"
    assert equity["strategy"]["display_name"] == "Residual Momentum v2 (weekly)"
    assert equity["strategy"]["signal_timeframe"] == "1d"
    assert equity["strategy"]["rebalance_cadence"] == "weekly"
    assert equity["maximum_allocation_pct"] == 50.0
    assert equity["eligible_pool"] is not None  # shortlist count
    assert equity["observation_set_size"] is not None
    assert isinstance(equity["holdings"], list)

    assert crypto["name"] == "crypto_low_variance_v2"
    assert crypto["strategy"]["display_name"] == "Low Realized Variance v2 (96h)"
    assert crypto["strategy"]["rebalance_cadence"] == "hours:96"
    assert crypto["maximum_allocation_pct"] == 10.0


def test_dashboard_risk_panel_exposes_policies_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))

    risk = asyncio.run(server.dashboard_overview())["risk"]
    assert risk["gross_exposure_pct"] == 40.0
    assert risk["gross_exposure_limit_pct"] == 60.0
    assert risk["cash_pct"] == 60.0
    assert risk["cash_buffer_limit_pct"] == 40.0
    assert risk["crypto_exposure_limit_pct"] == 10.0
    assert risk["max_drawdown_pct"] == 5.0
    assert risk["daily_loss_limit_pct"] == 0.5
    assert risk["entry_risk_latch"] == {}
    assert risk["circuit_breaker"] is None
    assert isinstance(risk["safety_incidents"], list)
    assert "risk_epoch" in risk


def test_dashboard_universe_and_health_panels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _repo_config(tmp_path)
    monkeypatch.setattr(server, "manager", _stub_manager(config_path))

    overview = asyncio.run(server.dashboard_overview())
    for market in ("equity", "crypto"):
        panel = overview["universe"][market]
        assert "broad_discovered_count" in panel
        assert "eligible_pool_size" in panel
        assert "observation_set_size" in panel
        assert "observation_set" in panel
        assert "pinned_symbols" in panel
        assert "last_refresh_at" in panel
        assert "scanner_status" in panel
        assert "next_refresh_at" in panel

    health = overview["health"]
    assert "workers" in health
    assert "queue" in health
    assert "account_freshness" in health
    assert "reconciliation" in health
    assert "scanner" in health
    assert "database" in health
    assert "last_heartbeat" in health
    assert "restart_count" in health
    assert health["database"]["path"].endswith("runtime.sqlite3")


# ---------------------------------------------------------------------------
# Holdings + explanations (per-holding reasons from decision metadata)
# ---------------------------------------------------------------------------


def _crypto_decision() -> dict[str, Any]:
    return {
        "timestamp": "2026-08-10T00:00:00+00:00",
        "strategy_name": "crypto_low_variance_v1",
        "target": {
            "strategy_name": "crypto_low_variance_v1",
            "weights": [
                {"symbol": "BTC/USD", "weight": 0.5, "reason": "low_realized_variance"},
                {"symbol": "ETH/USD", "weight": 0.5, "reason": "low_realized_variance"},
            ],
            "cash_weight": 0.0,
            "rebalance_reason": "scheduled",
            "universe_version": "crypto-universe-v1",
            "metadata": {
                "qualified_symbols": 4,
                "selected_symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD"],
                "hypothesis": "low realized variance: buy the 2 lowest-variance assets",
            },
        },
        "risk": {},
    }


def test_dashboard_holdings_carry_reason_rank_and_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _repo_config(tmp_path)
    positions = [
        {
            "symbol": "BTC/USD",
            "market": "crypto",
            "direction": "LONG",
            "total_qty": 0.5,
            "qty": 0.5,
            "market_value": 35050.0,
            "unrealized_pnl": 50.0,
            "protection_state": "protected",
        }
    ]
    monkeypatch.setattr(server, "manager", _stub_manager(config_path, positions=positions))
    monkeypatch.setattr(
        server, "_dashboard_market_decisions", lambda _account_id: {"crypto": _crypto_decision()}
    )

    crypto = asyncio.run(server.dashboard_overview())["crypto"]
    holding = crypto["holdings"][0]
    assert holding["symbol"] == "BTC/USD"
    assert holding["reason"] == "low_realized_variance"
    assert holding["rank"] == 1
    assert holding["of_qualified"] == 4
    assert holding["target_weight_pct"] == 50.0
    assert holding["current_weight_pct"] == pytest.approx(35.05)
    assert "realized_vol_22d" in holding
    assert crypto["last_decision_at"] == "2026-08-10T00:00:00+00:00"
    # 96h cadence: next boundary = decision + 96 hours
    assert crypto["next_rebalance_at"] == "2026-08-14T00:00:00+00:00"


def test_dashboard_explanations_derive_structured_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _repo_config(tmp_path)
    positions = [
        {
            "symbol": "BTC/USD",
            "market": "crypto",
            "direction": "LONG",
            "total_qty": 0.5,
            "qty": 0.5,
            "market_value": 35050.0,
            "unrealized_pnl": 50.0,
        }
    ]
    monkeypatch.setattr(server, "manager", _stub_manager(config_path, positions=positions))
    monkeypatch.setattr(
        server, "_dashboard_market_decisions", lambda _account_id: {"crypto": _crypto_decision()}
    )

    explanations = asyncio.run(server.dashboard_overview())["explanations"]
    crypto = explanations["crypto"]
    assert crypto["strategy_name"] == "crypto_low_variance_v1"
    assert crypto["qualified_symbols"] == 4
    assert crypto["selected_symbols"] == ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD"]
    assert crypto["hypothesis"].startswith("low realized variance")
    holding = crypto["holdings"][0]
    assert holding["symbol"] == "BTC/USD"
    assert holding["reason"] == "low_realized_variance"
    assert holding["rank"] == 1
    assert holding["decision_strategy"] == "crypto_low_variance_v1"
    # No debug dumps: every payload must stay JSON-serializable and small.
    payload = json.dumps(explanations)
    assert len(payload) < 4000
    assert "BTC/USD" in payload


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


def test_dashboard_metrics_consume_batch_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        ("dashboard_crypto", "crypto"),
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
