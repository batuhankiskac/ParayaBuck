from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from parayabuck import load_config
from parayabuck.config import EquityUniverseConfig
from parayabuck.portfolio import PRODUCT_STRATEGY_NAMES, portfolio_strategy_from, product_strategy_from


def _raw(universe_equity: dict[str, object], strategy: dict[str, object]) -> dict[str, object]:
    return {
        "app": {"host": "127.0.0.1", "port": 8000, "log_level": "INFO"},
        "runtime": {"mode": "paper"},
        "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
        "risk": {"daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.12},
        "engine": {},
        "universe": {"equity": universe_equity},
        "strategy": strategy,
    }


def test_generic_factory_constructs_product_strategy() -> None:
    assert PRODUCT_STRATEGY_NAMES == ("equity_leveraged_trend_v1",)
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1")
    assert strategy.name == "equity_leveraged_trend_v1"
    assert strategy.timeframe == "1d"


def test_product_factory_rejects_unsupported_strategy() -> None:
    # Use a fake strategy name that is not in the product surface.
    with pytest.raises(ValueError, match="Unsupported product strategy"):
        product_strategy_from("unsupported_strategy_name")
    # The removed crypto identities are no longer product strategies.
    with pytest.raises(ValueError, match="Unsupported product strategy"):
        product_strategy_from("crypto_low_variance_v2")

    # Product surface members are still constructible via product factory
    for name in PRODUCT_STRATEGY_NAMES:
        s = product_strategy_from(name)
        assert s.name == name


def test_product_factory_delegates_parameters_and_timeframe() -> None:
    # Validate delegation including timeframe handling
    s = product_strategy_from("equity_leveraged_trend_v1", parameters={"sma_bars": 150}, timeframe="1d")
    assert s.name == "equity_leveraged_trend_v1"
    assert s.parameters["sma_bars"] == 150
    # Unsupported timeframe should still fail via delegation
    with pytest.raises(ValueError, match="cannot run on timeframe"):
        product_strategy_from("equity_leveraged_trend_v1", timeframe="4h")


def test_config_loader_rejects_unsupported_equity_identity(tmp_path: Path) -> None:
    raw = _raw(
        {"enabled": True, "symbols": ["AAPL"]},
        {"equity": {"enabled": True, "name": "unsupported_equity_identity", "maximum_allocation": 0.55}},
    )
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="Unsupported product strategy"):
        load_config(str(path))


def test_missing_strategy_block_raises(tmp_path: Path) -> None:
    # universe.equity.enabled True but strategy.equity missing -> ValueError
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(_raw({"enabled": True, "symbols": ["AAPL"]}, {})))
    with pytest.raises(ValueError, match="strategy.equity block with name is required"):
        load_config(str(path))

    # Missing name inside present block also fails
    raw = _raw({"enabled": True, "symbols": ["AAPL"]}, {"equity": {"enabled": True, "maximum_allocation": 0.6}})
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="strategy.equity block with name is required"):
        load_config(str(path))


def test_removed_dynamic_equity_universe_keys_fail_to_load(tmp_path: Path) -> None:
    # The equity universe is static: a YAML still carrying the deleted dynamic
    # scanner keys must be rejected outright, never silently reinterpreted.
    for removed in ("dynamic_selection_enabled", "reference_symbols", "fallback_trade_symbols", "scanner"):
        raw = _raw(
            {"enabled": True, "symbols": ["AAPL"], removed: True},
            {"equity": {"enabled": True, "name": "equity_leveraged_trend_v1", "maximum_allocation": 0.5}},
        )
        path = tmp_path / f"settings-{removed}.yaml"
        path.write_text(yaml.safe_dump(raw))
        with pytest.raises(TypeError, match=removed):
            load_config(str(path))


def test_static_equity_universe_rejects_more_symbols_than_stream_capacity() -> None:
    # Every configured equity symbol is streamed, so the configured set can
    # never exceed maximum_stream_symbols.
    with pytest.raises(ValueError, match="exceed maximum_stream_symbols"):
        EquityUniverseConfig(symbols=("SPY", "QQQ", "QLD", "TQQQ"), maximum_stream_symbols=3)
    with pytest.raises(ValueError, match="must be unique"):
        EquityUniverseConfig(symbols=("SPY", "SPY"))


def test_dataclass_defaults_still_usable_without_yaml() -> None:
    # Keep dataclass defaults for unit tests: direct construction without YAML must still work
    from parayabuck.config import Config, StrategyConfig

    cfg = Config()
    assert cfg.strategy.equity.name in PRODUCT_STRATEGY_NAMES
    # Direct StrategyConfig defaults also usable
    sc = StrategyConfig()
    assert sc.equity.name == "equity_leveraged_trend_v1"
