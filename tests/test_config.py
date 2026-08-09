from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from parayabuck import Config, EngineConfig, RiskLimits, load_config


def _settings() -> dict[str, object]:
    return {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "alpaca": {"paper": True, "stock_feed": "iex"},
        "symbols": ["AAPL", "BTC/USD"],
        "engine": {
            "timeframe": "5m",
            "higher_timeframe": "1h",
            "warmup_bars": 100,
            "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        },
    }


def test_loads_single_application_config(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(_settings()))

    config = load_config(str(path))

    assert isinstance(config, Config)
    assert isinstance(config.engine, EngineConfig)
    assert isinstance(config.engine.risk, RiskLimits)
    assert config.symbols == ("AAPL", "BTC/USD")
    assert config.engine.risk.daily_loss_limit_pct == 0.02
    assert config.engine.risk.max_drawdown_pct == 0.12
    # Fail-closed default: portfolio approval is off unless explicitly set.
    assert config.engine.approve_portfolio_strategy is False


def test_deprecated_approve_portfolio_strategy_flag_is_rejected(tmp_path: Path) -> None:
    raw = _settings()
    raw["engine"]["approve_portfolio_strategy"] = True
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="deprecated and unsafe"):
        load_config(str(path))


def test_manifest_paths_require_one_separate_checksum(tmp_path: Path) -> None:
    raw = _settings()
    raw["engine"]["approval_manifest_paths"] = ["approval.json"]
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="expected checksum"):
        load_config(str(path))


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
