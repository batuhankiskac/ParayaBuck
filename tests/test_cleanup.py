from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from parayabuck.portfolio import portfolio_strategy_from, supported_portfolio_strategies

LEGACY_STRATEGY_NAMES = (
    "stock_trend_pullback",
    "stock_mean_reversion",
    "crypto_trend_breakout",
    "crypto_range_reversion",
)

LEGACY_MODULES = (
    "parayabuck.strategies.stock_trend",
    "parayabuck.strategies.stock_mean_reversion",
    "parayabuck.strategies.crypto_trend",
    "parayabuck.strategies.crypto_range",
    "parayabuck.strategies.specs",
    "parayabuck.backtest.research",
    "parayabuck.backtest.sweep",
    "parayabuck.backtest.challengers",
)


def test_legacy_strategies_are_not_registered() -> None:
    registered = set(supported_portfolio_strategies())
    assert not registered & set(LEGACY_STRATEGY_NAMES)
    for name in LEGACY_STRATEGY_NAMES:
        with pytest.raises(ValueError, match="Unsupported portfolio strategy"):
            portfolio_strategy_from(name)


def test_legacy_strategy_modules_are_gone() -> None:
    for module in LEGACY_MODULES:
        assert importlib.util.find_spec(module) is None, f"{module} still importable"


def test_no_source_imports_of_legacy_strategies_remain() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(name in text for name in (*LEGACY_STRATEGY_NAMES, *LEGACY_MODULES)):
            offenders.append(str(path))
    assert offenders == []


def test_active_configs_do_not_reference_legacy_strategies() -> None:
    root = Path(__file__).resolve().parents[1] / "config"
    offenders: list[str] = []
    for path in root.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        if any(name in text for name in LEGACY_STRATEGY_NAMES):
            offenders.append(str(path))
    assert offenders == []


def test_cli_no_longer_exposes_legacy_strategy_options() -> None:
    from parayabuck.backtest.cli import USAGE

    assert "--sweep" not in USAGE
    assert "--experiment-spec" not in USAGE
    assert "stock_trend_pullback" not in USAGE


def test_new_strategies_are_registered() -> None:
    registered = set(supported_portfolio_strategies())
    assert {
        "equity_etf_dual_momentum_v1",
        "crypto_cross_sectional_momentum_1h_v1",
        "equity_time_series_momentum_1d_v1",
    } <= registered
