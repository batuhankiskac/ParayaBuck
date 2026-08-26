"""Fail-closed universe guard regression tests.

Dynamic product execution (PAPER backtest/runtime) must not treat
``universe=None`` as "every symbol is tradable". The permissive
``PortfolioContext.in_universe`` fallback (True when universe is None or
lacks ``contains``) remains for explicitly declared static/non-product
research contexts via ``allow_static_context=True``; product paths must
raise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext, require_product_universe


def _ctx(*, universe, version: str) -> PortfolioContext:
    return PortfolioContext(
        decision_timestamp=datetime(2025, 6, 15, tzinfo=UTC),
        market=Market.CRYPTO,
        timeframe="1d",
        history={"BTC/USD": ()},
        universe=universe,
        universe_version=version,
        equity=100_000,
        holdings={},
        prices={"BTC/USD": 50_000},
        benchmark_symbol="BTC/USD",
    )


class _ValidUniverse:
    def contains(self, symbol: str, market: Market, ts: datetime) -> bool:
        return True


class _NoContains:
    pass


def test_product_guard_rejects_none_universe() -> None:
    ctx = _ctx(universe=None, version="v1")
    assert ctx.in_universe("BTC/USD") is True
    with pytest.raises(RuntimeError, match="universe is None"):
        require_product_universe(ctx)


def test_product_guard_rejects_missing_contains() -> None:
    ctx = _ctx(universe=_NoContains(), version="v1")
    assert ctx.in_universe("BTC/USD") is True
    with pytest.raises(RuntimeError, match="must expose a callable contains"):
        require_product_universe(ctx)


def test_product_guard_rejects_non_callable_contains() -> None:
    ctx = _ctx(universe=SimpleNamespace(contains="not callable"), version="v1")
    with pytest.raises(RuntimeError, match="must expose a callable contains"):
        require_product_universe(ctx)


def test_product_guard_rejects_empty_version() -> None:
    ctx = _ctx(universe=_ValidUniverse(), version="")
    with pytest.raises(RuntimeError, match="universe_version must be a non-empty"):
        require_product_universe(ctx)
    ctx2 = _ctx(universe=_ValidUniverse(), version="   ")
    with pytest.raises(RuntimeError, match="universe_version must be a non-empty"):
        require_product_universe(ctx2)


def test_product_guard_accepts_valid_universe() -> None:
    ctx = _ctx(universe=_ValidUniverse(), version="abc123")
    require_product_universe(ctx)
    assert ctx.in_universe("BTC/USD") is True


def test_static_context_opt_in_retains_permissive_behavior() -> None:
    ctx = _ctx(universe=None, version="")
    require_product_universe(ctx, allow_static_context=True)
    assert ctx.in_universe("ANY") is True
    ctx2 = _ctx(universe=_NoContains(), version="")
    require_product_universe(ctx2, allow_static_context=True)
    assert ctx2.in_universe("ANY") is True


def test_backtest_engine_fail_closes_on_missing_universe() -> None:
    from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
    from parayabuck.portfolio import portfolio_strategy_from
    from parayabuck.portfolio.models import RebalancePolicy

    strat = portfolio_strategy_from("crypto_low_variance_v2", {"top_n": 2, "rebalance_hours": 96})
    engine = PortfolioBacktestEngine(
        strategy=strat,
        rebalance_policy=RebalancePolicy(Market.CRYPTO, "hours:96"),
        universe=None,
        universe_version="",
        starting_equity=100_000,
    )
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    symbols = ["BTC/USD", "ETH/USD", "SOL/USD"]
    for i in range(30):
        ts = start + timedelta(days=i)
        for sym in symbols:
            bars.append(Bar(ts, sym, "1d", 100 + i, 100 + i, 100 + i, 100 + i, 1_000_000, Market.CRYPTO))
    with pytest.raises(RuntimeError, match="Product universe guard"):
        engine.run(bars, liquidate_at_end=False)


def test_runtime_service_fail_closes_on_missing_universe(tmp_path) -> None:
    from parayabuck.core.models import AccountSnapshot
    from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest
    from parayabuck.portfolio import portfolio_strategy_from
    from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository

    strat = portfolio_strategy_from(
        "crypto_low_variance_v2",
        {"top_n": 2, "rebalance_hours": 96, "variance_lookback_days": 22},
    )

    class _Broker:
        def account(self) -> AccountSnapshot:
            return AccountSnapshot(100_000, 100_000, 100_000, 0.0)

        def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
            return AssetTradingRules(symbol, market, 0.01, 0.01, 0.01, 1.0)

        def all_open_order_symbols(self) -> set[str]:
            return set()

        def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
            return "oid"

    repo = PortfolioStateRepository(tmp_path / "guard.sqlite3")
    runtime = PortfolioRuntime(
        strategy=strat,
        symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        broker=_Broker(),  # type: ignore[arg-type]
        repository=repo,
        mode="paper",
        maximum_allocation=0.9,
        universe=None,
        universe_version="",
    )
    # Seed enough history to clear warmup (min 23 bars for lookback 22).
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for i in range(30):
        ts = start + timedelta(days=i)
        for sym in ("BTC/USD", "ETH/USD", "SOL/USD"):
            bars.append(Bar(ts, sym, "1d", 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1_000_000, Market.CRYPTO))
    runtime.seed(bars)
    latest = max(b.timestamp for b in bars)
    deciding_batch = [
        Bar(latest, "BTC/USD", "1d", 110, 110, 110, 110, 1_000_000, Market.CRYPTO),
        Bar(latest, "ETH/USD", "1d", 110, 110, 110, 110, 1_000_000, Market.CRYPTO),
        Bar(latest, "SOL/USD", "1d", 110, 110, 110, 110, 1_000_000, Market.CRYPTO),
    ]
    runtime.previous_timestamp = None
    with pytest.raises(RuntimeError, match="Product universe guard"):
        runtime.process_closed_bars(deciding_batch)
