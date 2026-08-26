from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from parayabuck import load_config
from parayabuck.portfolio import PRODUCT_STRATEGY_NAMES, portfolio_strategy_from, product_strategy_from


def test_generic_factory_can_construct_research_only() -> None:
    # Research-only hysteresis is constructible via the generic factory
    # (FACTORY includes it) even though it is NOT in PRODUCT surface pre-promotion.
    assert "crypto_low_variance_hysteresis_v1" not in PRODUCT_STRATEGY_NAMES
    strategy = portfolio_strategy_from("crypto_low_variance_hysteresis_v1")
    assert strategy.name == "crypto_low_variance_hysteresis_v1"
    assert strategy.timeframe == "1d"


def test_product_factory_rejects_research_only() -> None:
    with pytest.raises(ValueError, match="Unsupported product strategy"):
        product_strategy_from("crypto_low_variance_hysteresis_v1")

    # Product surface members are still constructible via product factory
    for name in PRODUCT_STRATEGY_NAMES:
        s = product_strategy_from(name)
        assert s.name == name


def test_product_factory_delegates_parameters_and_timeframe() -> None:
    # Validate delegation including timeframe handling
    s = product_strategy_from("crypto_low_variance_v2", parameters={"top_n": 2}, timeframe="1d")
    assert s.name == "crypto_low_variance_v2"
    # Unsupported timeframe should still fail via delegation
    with pytest.raises(ValueError, match="cannot run on timeframe"):
        product_strategy_from("crypto_low_variance_v2", timeframe="4h")


def test_config_loader_rejects_research_only_strategy_identity(tmp_path: Path) -> None:
    raw: dict[str, object] = {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "runtime": {"mode": "paper"},
        "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        "engine": {},
        "universe": {
            "equity": {"enabled": False},
            "crypto": {"enabled": True, "trade_symbols": ["BTC/USD"]},
        },
        "strategy": {
            "crypto": {"enabled": True, "name": "crypto_low_variance_hysteresis_v1", "maximum_allocation": 0.05},
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="Unsupported product strategy"):
        load_config(str(path))


def test_config_loader_rejects_research_only_equity_identity(tmp_path: Path) -> None:
    # Generic factory would allow hysteresis on crypto only; test equity research rejection if ever added
    # Here we test that crypto hysteresis is rejected even when equity also present
    raw: dict[str, object] = {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "runtime": {"mode": "paper"},
        "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        "engine": {},
        "universe": {
            "equity": {"enabled": True, "fallback_trade_symbols": ["AAPL"]},
            "crypto": {"enabled": True, "trade_symbols": ["BTC/USD"]},
        },
        "strategy": {
            "equity": {"enabled": True, "name": "equity_residual_momentum_v3", "maximum_allocation": 0.60},
            "crypto": {"enabled": True, "name": "crypto_low_variance_hysteresis_v1", "maximum_allocation": 0.05},
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="Unsupported product strategy"):
        load_config(str(path))


def test_missing_strategy_block_raises(tmp_path: Path) -> None:
    # universe.equity.enabled True but strategy.equity missing -> ValueError
    raw: dict[str, object] = {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "runtime": {"mode": "paper"},
        "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        "engine": {},
        "universe": {
            "equity": {"enabled": True, "fallback_trade_symbols": ["AAPL"]},
            "crypto": {"enabled": False},
        },
        # strategy block intentionally missing equity
        "strategy": {
            "crypto": {"enabled": False, "name": "crypto_low_variance_v1", "maximum_allocation": 0.05},
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="strategy.equity block with name is required"):
        load_config(str(path))

    # universe.crypto.enabled True but strategy.crypto missing -> ValueError
    raw2: dict[str, object] = {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "runtime": {"mode": "paper"},
        "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        "engine": {},
        "universe": {
            "equity": {"enabled": False},
            "crypto": {"enabled": True, "trade_symbols": ["BTC/USD"]},
        },
        "strategy": {
            "equity": {"enabled": False, "name": "equity_residual_momentum_v1", "maximum_allocation": 0.5},
        },
    }
    path.write_text(yaml.safe_dump(raw2))
    with pytest.raises(ValueError, match="strategy.crypto block with name is required"):
        load_config(str(path))

    # Missing name inside present block also fails
    raw3: dict[str, object] = {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "runtime": {"mode": "paper"},
        "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        "engine": {},
        "universe": {
            "equity": {"enabled": True, "fallback_trade_symbols": ["AAPL"]},
            "crypto": {"enabled": False},
        },
        "strategy": {
            "equity": {"enabled": True, "maximum_allocation": 0.6},  # missing name
        },
    }
    path.write_text(yaml.safe_dump(raw3))
    with pytest.raises(ValueError, match="strategy.equity block with name is required"):
        load_config(str(path))


def test_missing_both_blocks_when_both_universes_enabled(tmp_path: Path) -> None:
    raw: dict[str, object] = {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "runtime": {"mode": "paper"},
        "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        "engine": {},
        "universe": {
            "equity": {"enabled": True, "fallback_trade_symbols": ["AAPL"]},
            "crypto": {"enabled": True, "trade_symbols": ["BTC/USD"]},
        },
        "strategy": {},
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="strategy.equity block with name is required"):
        load_config(str(path))


def test_dataclass_defaults_still_usable_without_yaml() -> None:
    # Keep dataclass defaults for unit tests: direct construction without YAML must still work
    from parayabuck.config import Config, StrategyConfig

    cfg = Config()
    # defaults contain v1 names which are product-supported
    assert cfg.strategy.equity.name in PRODUCT_STRATEGY_NAMES
    assert cfg.strategy.crypto.name in PRODUCT_STRATEGY_NAMES
    # Direct StrategyConfig defaults also usable
    sc = StrategyConfig()
    assert sc.equity.name == "equity_residual_momentum_v1"
