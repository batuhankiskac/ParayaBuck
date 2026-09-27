from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from parayabuck import Config, EngineConfig, RiskLimits, load_config
from parayabuck.config import AppConfig, DataConfig, RuntimeMode


def _settings() -> dict[str, object]:
    return {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "runtime": {"mode": "paper"},
        "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
        "symbols": ["AAPL", "BTC/USD"],
        "engine": {},
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        "strategy": {
            "equity": {"enabled": True, "name": "equity_leveraged_trend_v1", "maximum_allocation": 0.55},
            "crypto": {"enabled": True, "name": "crypto_low_variance_v2", "maximum_allocation": 0.05},
        },
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


def test_strategy_and_operational_timeframes_have_explicit_roles(tmp_path: Path) -> None:
    raw = _settings()
    raw["strategy"] = {
        "equity": {
            "enabled": True,
            "name": "equity_leveraged_trend_v1",
            "timeframe": "1d",
            "maximum_allocation": 0.5,
        },
        "crypto": {
            "enabled": True,
            "name": "crypto_low_variance_v2",
            "maximum_allocation": 0.05,
        },
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


def test_shipped_paper_config_has_integrated_cap8_with_other_limits_unchanged() -> None:
    config = load_config(str(Path(__file__).resolve().parents[1] / "config" / "settings.yaml"))

    assert config.runtime.mode == RuntimeMode.PAPER
    assert config.live.enabled is False
    assert config.risk.max_positions == 8
    assert config.risk.max_crypto_positions == 2
    assert config.risk.max_gross_exposure_pct == 0.98
    assert config.risk.cash_buffer_pct == 0.02
    assert config.risk.max_position_pct == 0.93
    assert config.risk.max_crypto_position_pct == 0.05
    assert config.risk.max_crypto_exposure_pct == 0.10
    assert config.risk.daily_loss_limit_pct == 0.20
    assert config.risk.max_drawdown_pct == 0.75
    assert config.strategy.equity.name == "equity_leveraged_trend_v1"
    assert config.strategy.equity.maximum_allocation == 0.93
    assert config.strategy.equity.parameters["signal_symbol"] == "QQQ"
    assert config.strategy.equity.parameters["hold_symbol"] == "QLD"
    assert config.strategy.equity.parameters["sma_bars"] == 200
    assert config.strategy.equity.parameters["vol_target"] == 0.35
    assert config.strategy.equity.parameters["vol_window"] == 20
    assert config.strategy.crypto.name == "crypto_low_variance_hysteresis_v1"
    assert config.strategy.crypto.maximum_allocation == 0.05
    assert config.execution.rebalance_band_pct == 0.005


def test_deprecated_risk_monitor_timeframe_still_loads_and_maps(tmp_path) -> None:
    """A pre-v1 settings.yaml with runtime.risk_monitor_timeframe must keep
    loading after the market_state_timeframe rename instead of crashing."""
    path = tmp_path / "settings.yaml"
    path.write_text(
        "runtime:\n  mode: paper\n  execution_timeframe: 5m\n"
        "  risk_monitor_timeframe: 5m\n  decision_db_path: data/runtime/test.sqlite3\n"
        "universe:\n  equity:\n    enabled: true\n"
        "strategy:\n  equity:\n    enabled: true\n    name: equity_leveraged_trend_v1\n",
        encoding="utf-8",
    )
    with pytest.warns(DeprecationWarning, match="risk_monitor_timeframe"):
        config = load_config(str(path))
    assert config.runtime.market_state_timeframe == "5m"
    assert config.runtime.execution_timeframe == "5m"


def test_strategy_supplies_default_timeframe_not_market_default(tmp_path: Path) -> None:
    """The strategy owns its signal timeframe: selecting crypto low-variance
    without a timeframe must resolve to the strategy's PRIMARY (1d), never to
    the old crypto default (1h)."""
    raw = _settings()
    raw["strategy"] = {
        "equity": {"enabled": True, "name": "equity_leveraged_trend_v1"},
        "crypto": {
            "enabled": True,
            "name": "crypto_low_variance_v1",
            "maximum_allocation": 0.1,
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    config = load_config(str(path))
    assert config.strategy.crypto.timeframe == "1d"
    assert config.strategy.equity.timeframe == "1d"


def test_unsupported_strategy_timeframe_is_rejected_at_load(tmp_path: Path) -> None:
    raw = _settings()
    raw["strategy"] = {
        "equity": {"enabled": True, "name": "equity_leveraged_trend_v1"},
        "crypto": {
            "enabled": True,
            "name": "crypto_low_variance_v1",
            "timeframe": "4h",  # low-variance validates only 1d
            "maximum_allocation": 0.1,
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="cannot run on timeframe 4h"):
        load_config(str(path))


def test_present_but_empty_app_and_data_blocks_load_with_defaults(tmp_path: Path) -> None:
    """``app:`` / ``data:`` with no value parse to None; loading must fall back
    to the defaults instead of raising a bare TypeError."""
    raw = _settings()
    raw["app"] = None
    raw["data"] = None
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    config = load_config(str(path))

    assert config.app == AppConfig()
    assert config.data == DataConfig()


@pytest.mark.parametrize(
    "block",
    ["runtime", "engine", "risk", "alpaca", "backtest", "live", "execution", "backup", "alerting", "observability"],
)
def test_present_but_empty_top_level_block_loads_with_defaults(tmp_path: Path, block: str) -> None:
    raw = _settings()
    raw[block] = None
    empty_path = tmp_path / "empty.yaml"
    empty_path.write_text(yaml.safe_dump(raw))
    del raw[block]
    absent_path = tmp_path / "absent.yaml"
    absent_path.write_text(yaml.safe_dump(raw))

    assert load_config(str(empty_path)) == load_config(str(absent_path))


def test_present_but_empty_universe_sub_blocks_load_with_defaults(tmp_path: Path) -> None:
    raw = _settings()
    del raw["symbols"]
    raw["universe"] = {"equity": None, "crypto": {"enabled": True, "trade_symbols": ["BTC/USD"], "scanner": None}}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    config = load_config(str(path))

    assert config.universe.crypto.trade_symbols == ("BTC/USD",)


def test_present_but_empty_enabled_strategy_block_fails_closed_with_message(tmp_path: Path) -> None:
    raw = _settings()
    raw["strategy"] = {"equity": None, "crypto": {"enabled": True, "name": "crypto_low_variance_v2"}}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="strategy.equity block with name is required"):
        load_config(str(path))
