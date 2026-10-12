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
        "symbols": ["SPY", "QQQ", "QLD"],
        "engine": {},
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        "strategy": {
            "equity": {"enabled": True, "name": "equity_leveraged_trend_v1", "maximum_allocation": 0.55},
        },
    }


def test_loads_single_application_config(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(_settings()))

    config = load_config(str(path))

    assert isinstance(config, Config)
    assert isinstance(config.engine, EngineConfig)
    assert isinstance(config.risk, RiskLimits)
    assert config.symbols == ("SPY", "QQQ", "QLD")
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
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    config = load_config(str(path))
    assert not hasattr(config.runtime, "execution_timeframe")
    assert not hasattr(config.runtime, "market_state_timeframe")
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
    assert config.risk.max_gross_exposure_pct == 0.98
    assert config.risk.cash_buffer_pct == 0.02
    # 2026-09-29 (paper_config_v2): equity 0.98.
    assert config.risk.max_position_pct == 0.98
    assert config.risk.daily_loss_limit_pct == 0.20
    assert config.risk.max_drawdown_pct == 0.75
    assert config.strategy.equity.name == "equity_leveraged_trend_v1"
    assert config.strategy.equity.maximum_allocation == 0.98
    assert "cash_sweep_symbol" not in config.strategy.equity.parameters
    assert config.universe.equity.symbols == ("SPY", "QQQ", "QLD")
    assert config.strategy.equity.parameters["signal_symbol"] == "QQQ"
    assert config.strategy.equity.parameters["hold_symbol"] == "QLD"
    assert config.strategy.equity.parameters["sma_bars"] == 200
    assert config.strategy.equity.parameters["vol_target"] == 0.35
    assert config.strategy.equity.parameters["vol_window"] == 20
    assert config.symbols == ("SPY", "QQQ", "QLD")
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
    with pytest.warns(DeprecationWarning, match="removed and ignored"):
        config = load_config(str(path))
    assert not hasattr(config.runtime, "market_state_timeframe")
    assert not hasattr(config.runtime, "execution_timeframe")


def test_strategy_supplies_default_timeframe(tmp_path: Path) -> None:
    """The strategy owns its signal timeframe: without a configured timeframe
    it resolves to the strategy's PRIMARY (1d)."""
    raw = _settings()
    raw["strategy"] = {"equity": {"enabled": True, "name": "equity_leveraged_trend_v1"}}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    config = load_config(str(path))
    assert config.strategy.equity.timeframe == "1d"


def test_unsupported_strategy_timeframe_is_rejected_at_load(tmp_path: Path) -> None:
    raw = _settings()
    raw["strategy"] = {
        "equity": {
            "enabled": True,
            "name": "equity_leveraged_trend_v1",
            "timeframe": "4h",  # the leveraged trend validates only 1d
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.warns(DeprecationWarning, match="strategy.equity.timeframe"):
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
    raw["universe"] = {"equity": None}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    config = load_config(str(path))

    assert config.universe.equity.symbols == ("SPY", "QQQ", "QLD")


def test_present_but_empty_enabled_strategy_block_fails_closed_with_message(tmp_path: Path) -> None:
    raw = _settings()
    raw["strategy"] = {"equity": None}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="strategy.equity block with name is required"):
        load_config(str(path))


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("strategy", "crypto", {"enabled": False, "name": "crypto_low_variance_hysteresis_v1"}),
        ("universe", "crypto", {"enabled": False}),
        ("risk", "max_crypto_positions", 2),
        ("engine", "crypto_emergency_exit_bps", 75),
        ("backtest", "crypto_taker_fee_bps", 25),
        ("intent_watchdog", "crypto_fill_grace_minutes", 60),
        ("runtime", "scanner_refresh_hours", 24),
        ("backtest", "scanner_rebalance", "daily"),
    ],
)
def test_removed_crypto_settings_fail_loudly(tmp_path: Path, section: str, key: str, value: object) -> None:
    """Crypto support was removed: a stale configuration naming it must not load
    with its crypto intent silently dropped."""
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text())
    raw[section][key] = value
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match=f"Crypto support was removed.*{section}.{key}"):
        load_config(str(path))


def test_legacy_symbol_list_with_a_crypto_pair_is_rejected(tmp_path: Path) -> None:
    raw = _settings()
    raw["symbols"] = ["AAPL", "BTC/USD"]
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="Crypto support was removed.*BTC/USD"):
        load_config(str(path))


def test_equity_allocation_above_gross_limit_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text())
    raw["strategy"]["equity"]["maximum_allocation"] = 0.99
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="Capital invariant violated"):
        load_config(str(path))


def test_removed_manage_external_positions_setting_fails_loudly(tmp_path: Path) -> None:
    """The option that let the bot attach stops to positions it never owned is
    gone; a stale deployment config naming it must be rejected, not silently
    dropped back to the safe default."""
    raw = _settings()
    raw["engine"] = {"manage_external_positions": True}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(TypeError, match="manage_external_positions"):
        load_config(str(path))


@pytest.mark.parametrize("key", ["webhook_url", "enabled", "events"])
def test_removed_alert_delivery_keys_are_rejected(tmp_path: Path, key: str) -> None:
    """Outbound alert delivery is gone; a stale webhook key must fail loudly,
    never load with the operator's intent silently dropped."""
    raw = _settings()
    raw["alerting"] = {"daily_summary": True, key: "" if key == "webhook_url" else []}
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match=rf"alerts stay local.*alerting\.{key}"):
        load_config(str(path))


def test_shipped_settings_carry_no_alert_delivery_keys() -> None:
    settings = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    alerting = yaml.safe_load(settings.read_text(encoding="utf-8"))["alerting"]
    # No webhook keys; the only off-host channel (e-mail) ships disabled.
    assert set(alerting) == {"daily_summary", "email"}
    assert alerting["daily_summary"] is True
    assert alerting["email"]["enabled"] is False
    loaded = load_config(str(settings)).alerting
    assert loaded.daily_summary is True
    assert loaded.email.enabled is False


def test_decision_retry_window_must_fit_the_watchdog_and_cover_the_cross_check() -> None:
    """A deferred session-close decision must still land before the intent
    watchdog calls it missing, and must not expire while a second-source
    deferral is still running."""
    # Defaults: 40 min < equity_decision_grace 45, and >= sip_embargo 15 +
    # retry_window 10.
    assert Config().engine.decision_retry_window_minutes == 40.0

    with pytest.raises(ValueError, match="decision_retry_window_minutes must be below"):
        Config(engine=EngineConfig(decision_retry_window_minutes=45.0))

    with pytest.raises(ValueError, match="decision_retry_window_minutes must cover"):
        Config(engine=EngineConfig(decision_retry_window_minutes=20.0))


def test_reconcile_failure_threshold_must_be_positive() -> None:
    with pytest.raises(ValueError, match="reconcile_failure_threshold"):
        EngineConfig(reconcile_failure_threshold=0)


@pytest.mark.parametrize("key", ["signal_symbol", "hold_symbol"])
def test_strategy_symbol_outside_the_universe_fails_loading(tmp_path: Path, key: str) -> None:
    """E11: a signal/hold symbol the runtime never streams or warms up must be
    a load error, not an engine that silently never becomes ready."""
    raw = _settings()
    raw["strategy"]["equity"]["parameters"] = {key: "TQQQ"}  # type: ignore[index]
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match=f"universe.equity.symbols.*{key}=TQQQ"):
        load_config(str(path))


def test_disabled_strategy_skips_the_universe_symbol_check(tmp_path: Path) -> None:
    raw = _settings()
    raw["strategy"] = {"equity": {"enabled": False, "name": "equity_leveraged_trend_v1", "maximum_allocation": 0.5}}
    raw["symbols"] = ["SPY"]
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))

    assert load_config(str(path)).universe.equity.symbols == ("SPY",)
