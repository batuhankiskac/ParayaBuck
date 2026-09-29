"""Static equity universe binding and runtime symbol readiness.

The equity universe is the configured static symbol list (no scanner, no
scheduled refresh); managed positions are pinned at startup only.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.runtime import EngineRuntime
from parayabuck.universe import equity_universe_digest


def test_static_equity_product_universe_identity_follows_the_configured_symbols() -> None:
    """The equity market binds a CONFIGURED snapshot whose identity is the
    static equity policy digest: editing the configured symbols produces a
    different snapshot identity."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    base = Config()
    runtime.app_config = base

    bound = runtime._load_product_universe(base.universe.equity.symbols)
    digest = equity_universe_digest(base.universe.equity)
    assert bound.market == Market.EQUITY
    assert bound.observation_symbols == ("SPY", "QQQ", "QLD")
    assert bound.snapshot_id == f"equity:configured:{digest}"
    assert bound.config_digest == digest

    narrowed = replace(base.universe.equity, symbols=("SPY", "QQQ"))
    runtime.app_config = replace(base, universe=replace(base.universe, equity=narrowed))
    rebound = runtime._load_product_universe(narrowed.symbols)
    assert rebound.config_digest != digest
    assert rebound.observation_symbols == ("SPY", "QQQ")


def test_engine_readiness_requires_every_portfolio_symbol() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.symbols = ["AAPL", "MSFT"]
    runtime._not_ready_symbols = {"MSFT"}
    runtime._portfolio_runtimes = (SimpleNamespace(symbols=("AAPL", "MSFT")),)

    assert runtime._has_ready_symbols() is False

    runtime._not_ready_symbols.clear()
    assert runtime._has_ready_symbols() is True


def test_refresh_readiness_requires_latest_timestamp_alignment() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = False
    runtime._state = "warming_up"
    runtime._not_ready_symbols = set()
    start = datetime(2025, 1, 1, tzinfo=UTC)

    def bar(symbol: str, day: int) -> Bar:
        return Bar(start + timedelta(days=day), symbol, "1d", 100, 100, 100, 100, 1_000, Market.EQUITY)

    portfolio_runtime = SimpleNamespace(
        market=Market.EQUITY,
        symbols=("SPY", "AAPL"),
        strategy=SimpleNamespace(minimum_history_bars=2),
        history={
            "SPY": [bar("SPY", 0), bar("SPY", 1)],
            "AAPL": [bar("AAPL", 0), bar("AAPL", 0)],
        },
    )
    runtime._portfolio_runtimes = (portfolio_runtime,)

    runtime._refresh_readiness()
    assert runtime._not_ready_symbols == {"AAPL"}
    assert runtime._has_ready_symbols() is False

    portfolio_runtime.history["AAPL"][-1] = bar("AAPL", 1)
    runtime._refresh_readiness()
    assert runtime._not_ready_symbols == set()
    assert runtime._has_ready_symbols() is True
