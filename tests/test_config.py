from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from parayabuck import Config, EngineConfig, RiskLimits, load_config
from parayabuck.config import RuntimeMode


def _settings() -> dict[str, object]:
    return {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "runtime": {"mode": "paper"},
        "alpaca": {"stock_feed": "iex"},
        "symbols": ["AAPL", "BTC/USD"],
        "engine": {
            "timeframe": "5m",
            "higher_timeframe": "1h",
            "warmup_bars": 100,
        },
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
    }


def test_loads_single_application_config(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(_settings()))

    config = load_config(str(path))

    assert isinstance(config, Config)
    assert isinstance(config.engine, EngineConfig)
    assert isinstance(config.risk, RiskLimits)
    assert config.symbols == ("AAPL", "BTC/USD")
    assert config.risk.daily_loss_limit_pct == 0.02
    assert config.risk.max_drawdown_pct == 0.12
    assert config.runtime.mode == RuntimeMode.PAPER
    assert config.strategy.equity.timeframe == "1d"


def test_live_mode_requires_explicit_live_enablement(tmp_path: Path) -> None:
    raw = _settings()
    raw["runtime"] = {"mode": "live"}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="live.enabled=true"):
        load_config(str(path))


def test_strategy_timeframe_is_independent_from_execution_timeframe(tmp_path: Path) -> None:
    raw = _settings()
    raw["strategy"] = {
        "equity": {
            "enabled": True,
            "name": "equity_dynamic_trend_1d_v1",
            "timeframe": "1d",
            "maximum_allocation": 0.5,
        }
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    config = load_config(str(path))
    assert config.engine.timeframe == "5m"
    assert config.strategy.equity.timeframe == "1d"


def test_rejects_duplicate_symbols(tmp_path: Path) -> None:
    raw = _settings()
    raw["symbols"] = ["AAPL", "AAPL"]
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="unique"):
        load_config(str(path))


def test_risk_percentages_are_bounded() -> None:
    with pytest.raises(ValueError, match="Risk ratios"):
        RiskLimits(daily_loss_limit_pct=1.01)


def test_non_finite_config_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="Risk ratios"):
        RiskLimits(risk_per_trade_pct=math.nan)
    with pytest.raises(ValueError, match="timing"):
        EngineConfig(event_grace_seconds=math.nan)
