"""Regression tests for the 2026-10-27 audit wave W23 (F9-6 side split, S9-7 settings temp sweep).

Offline and deterministic: fake alert sink, synthetic decision results and
tmp_path state. No broker, no network, no production path is written.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import parayabuck.monitoring.alerts as alerts
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioOrderSide
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.runtime import EngineRuntime
from parayabuck.web import lifespan as lifespan_module

_DECISION_TS = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
_TWO_HOURS_AGO = time.time() - 7200


def _target() -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="equity_leveraged_trend_v1",
        strategy_version="1.0.0",
        decision_timestamp=_DECISION_TS,
        weights=(TargetWeight("QLD", 0.8, reason="trend_up", rank=1),),
        cash_weight=0.2,
        rebalance_reason=RebalanceReason.SCHEDULED,
        metadata={
            "trend_state": "uptrend",
            "signal_symbol": "QQQ",
            "hold_symbol": "QLD",
            "signal_close": 550.0,
            "signal_sma": 500.0,
            "sma_bars": 200,
        },
    )


def _order(client_order_id: str, side: PortfolioOrderSide) -> SimpleNamespace:
    return SimpleNamespace(client_order_id=client_order_id, side=side)


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture every ``daily_summary`` alert the runtime emits."""
    calls: list[dict] = []

    def fake_emit(kind: str, **kwargs: object) -> None:
        calls.append({"kind": kind, **kwargs})

    monkeypatch.setattr(alerts, "emit", fake_emit)
    monkeypatch.setattr(EngineRuntime, "_daily_summary_tax_line", lambda self: "")
    return calls


def _shell() -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.app_config = SimpleNamespace(alerting=SimpleNamespace(daily_summary=True))
    return runtime


# ---------------------------------------------------------------- F9-6


def test_runtime_summary_reports_each_side_submitted_over_planned(emitted: list[dict]) -> None:
    # Two SELLs went out; two BUYs were planned but deferred by the rebalance.
    orders = (
        _order("sell-1", PortfolioOrderSide.SELL),
        _order("sell-2", PortfolioOrderSide.SELL),
        _order("buy-1", PortfolioOrderSide.BUY),
        _order("buy-2", PortfolioOrderSide.BUY),
    )
    result = SimpleNamespace(
        decision_id="d1",
        target=_target(),
        status="submitted",
        orders=orders,
        submitted_order_ids=("sell-1", "sell-2"),
    )

    _shell()._emit_equity_daily_summary(SimpleNamespace(market=Market.EQUITY), result, None)

    assert len(emitted) == 1
    message = emitted[0]["message"]
    assert "submitted orders sell 2/2 buy 0/2" in message
    assert "orders 2/4" not in message
    assert emitted[0]["orders_by_side"] == {
        "sell": {"submitted": 2, "planned": 2},
        "buy": {"submitted": 0, "planned": 2},
    }


def test_runtime_summary_keeps_the_combined_count_when_no_side_is_classified(emitted: list[dict]) -> None:
    # An order without a side cannot be attributed, so the segment keeps the
    # combined submitted/orders count instead of inventing a split.
    result = SimpleNamespace(
        decision_id="d1",
        target=_target(),
        status="submitted",
        orders=(object(),),
        submitted_order_ids=("x",),
    )

    _shell()._emit_equity_daily_summary(SimpleNamespace(market=Market.EQUITY), result, None)

    assert len(emitted) == 1
    assert "submitted orders 1/1" in emitted[0]["message"]
    assert "orders_by_side" not in emitted[0]


# ---------------------------------------------------------------- S9-7


def _file(path: Path, *, old: bool) -> Path:
    """Write a small file; ``old`` backdates it past the sweep's one-hour floor."""
    path.write_text("partial", encoding="utf-8")
    if old:
        os.utime(path, (_TWO_HOURS_AGO, _TWO_HOURS_AGO))
    return path


def _layout(tmp_path: Path) -> SimpleNamespace:
    """Settings and .env files under tmp_path, plus an unrelated data/ directory."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (tmp_path / "data").mkdir()
    config = config_dir / "settings.yaml"
    config.write_text("app: {}\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=test\n", encoding="utf-8")
    return SimpleNamespace(config_path=config, env_path=env)


def test_sweep_removes_only_old_orphans_from_the_settings_directories(tmp_path: Path) -> None:
    manager = _layout(tmp_path)
    config_dir = manager.config_path.parent
    orphan_validate = _file(config_dir / ".settings.yaml.validate", old=True)
    orphan_mkstemp = _file(config_dir / ".settings.yaml.a1b2c3d4", old=True)
    fresh_temp = _file(config_dir / ".settings.yaml.e5f6a7b8", old=False)  # a live writer may own it
    unrelated = _file(config_dir / "notes.txt", old=True)
    orphan_env = _file(tmp_path / "..env.k9x2mq", old=True)
    data_temp = _file(tmp_path / "data" / ".settings.yaml.keep", old=True)

    lifespan_module._sweep_settings_temp_files(manager)

    assert not orphan_validate.exists()
    assert not orphan_mkstemp.exists()
    assert not orphan_env.exists()
    assert fresh_temp.exists()
    assert unrelated.exists()
    assert data_temp.exists()  # outside the two managed directories
    assert manager.config_path.exists()
    assert manager.env_path.exists()


def test_sweep_handles_one_bad_path_without_skipping_the_other(tmp_path: Path) -> None:
    manager = _layout(tmp_path)
    orphan_env = _file(tmp_path / "..env.zz11", old=True)
    broken = SimpleNamespace(config_path=12345, env_path=manager.env_path)  # not a path

    lifespan_module._sweep_settings_temp_files(broken)  # must not raise

    assert not orphan_env.exists()


def test_sweep_ignores_a_manager_without_settings_paths() -> None:
    lifespan_module._sweep_settings_temp_files(SimpleNamespace())
    lifespan_module._sweep_settings_temp_files(object())


def test_lifespan_sweeps_settings_temp_files_once_at_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    manager = _layout(tmp_path)
    orphan = _file(manager.config_path.parent / ".settings.yaml.dead0000", old=True)
    seen_during_startup: list[bool] = []

    class _Manager:
        config_path = manager.config_path
        env_path = manager.env_path
        engine_state = "stopped"

        def should_auto_start(self) -> bool:
            return False

        def stop_engine(self) -> None:
            return None

    lifespan = lifespan_module.create_lifespan(lambda: _Manager())

    async def run() -> None:
        async with lifespan(None):
            seen_during_startup.append(orphan.exists())

    asyncio.run(asyncio.wait_for(run(), 10))

    assert seen_during_startup == [False]
    assert not orphan.exists()
    assert manager.config_path.exists()
