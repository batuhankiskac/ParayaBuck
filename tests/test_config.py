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
        "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
        "symbols": ["AAPL", "BTC/USD"],
        "engine": {},
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


def test_backtest_is_rejected_as_execution_runtime_mode(tmp_path: Path) -> None:
    raw = _settings()
    raw["runtime"] = {"mode": "backtest"}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="CLI-only"):
        load_config(str(path))


def test_strategy_and_operational_timeframes_have_explicit_roles(tmp_path: Path) -> None:
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
    assert config.runtime.execution_timeframe == "5m"
    assert config.runtime.market_state_timeframe == "5m"
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
        RiskLimits(max_position_pct=math.nan)
    with pytest.raises(ValueError, match="timing"):
        EngineConfig(event_grace_seconds=math.nan)


def test_obsolete_timeframe_and_placebo_risk_fields_get_migration_error(tmp_path: Path) -> None:
    raw = _settings()
    raw["engine"] = {"higher_timeframe": "1h"}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="Obsolete engine timeframe"):
        load_config(str(path))

    raw["engine"] = {}
    raw["risk"] = {"risk_per_trade_pct": 0.01}
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="Obsolete risk settings"):
        load_config(str(path))


def test_shipped_container_config_stays_paper_even_when_credentials_exist(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "live-looking-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "live-looking-secret")
    path = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"

    config = load_config(str(path))

    assert config.runtime.mode == RuntimeMode.PAPER
    assert not config.live.enabled


def test_deprecated_risk_monitor_timeframe_still_loads_and_maps(tmp_path) -> None:
    """A pre-v1 settings.yaml with runtime.risk_monitor_timeframe must keep
    loading after the market_state_timeframe rename instead of crashing."""
    path = tmp_path / "settings.yaml"
    path.write_text(
        "runtime:\n  mode: paper\n  execution_timeframe: 5m\n"
        "  risk_monitor_timeframe: 5m\n  decision_db_path: data/runtime/test.sqlite3\n"
        "universe:\n  equity:\n    enabled: true\n"
        "strategy:\n  equity:\n    enabled: true\n",
        encoding="utf-8",
    )
    with pytest.warns(DeprecationWarning, match="risk_monitor_timeframe"):
        config = load_config(str(path))
    assert config.runtime.market_state_timeframe == "5m"
    assert config.runtime.execution_timeframe == "5m"
