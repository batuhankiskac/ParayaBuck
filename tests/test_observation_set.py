"""Broad-pool alpha-before-stream contract tests.

The stream limit is NOT the opportunity universe: the scanner emits the
eligible POOL (tradability-first), the strategy alpha ranks the pool on batch
history, and the top ``maximum_stream_symbols`` plus pins become the streamed
OBSERVATION SET. Also covers the residual-momentum timestamp alignment
regression and the required-timeframes aggregation contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.config import (
    CryptoUniverseConfig,
    EquityScannerConfig,
    EquityUniverseConfig,
)
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.equity_residual_momentum import (
    _aligned_returns,
    _ols_beta,
)
from parayabuck.universe import (
    load_active_crypto_symbols,
    load_active_symbols,
    validate_active_crypto_universe,
    validate_active_universe,
)
from parayabuck.universe.crypto_scanner import (
    CryptoCandidate,
    CryptoUniverseScanner,
)
from parayabuck.universe.observation_set import build_observation_set
from parayabuck.universe.scanner import (
    EquityCandidate,
    EquityUniverseScanner,
)


def _bar(symbol: str, timestamp: datetime, close: float, market: Market) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close,
        volume=1_000_000.0,
        market=market,
    )


def _series(
    symbol: str, slope: float, days: int = 300, market: Market = Market.EQUITY
) -> tuple[Bar, ...]:
    start = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    return tuple(
        _bar(symbol, start + timedelta(days=index), 100 * (1 + slope) ** index, market)
        for index in range(days)
    )


def _equity_scanner_result() -> EquityUniverseScanner:
    config = EquityUniverseConfig(
        maximum_stream_symbols=3,
        reference_symbols=("SPY",),
        fallback_trade_symbols=("AAPL",),
        scanner=EquityScannerConfig(maximum_per_sector=1),
    )
    return EquityUniverseScanner(config)


def test_scanner_pool_exceeds_streamed_observation_set() -> None:
    scanner = _equity_scanner_result()
    candidates = [
        EquityCandidate("MSFT", 400, 500_000_000, 0.03, 1.0, 2, "technology"),
        EquityCandidate("AAPL", 200, 600_000_000, 0.03, 1.0, 2, "technology"),
        EquityCandidate("JPM", 220, 300_000_000, 0.025, 0.99, 3, "financial"),
    ]
    timestamp = datetime.now(UTC)
    result = scanner.scan(candidates, data_timestamp=timestamp, selection_timestamp=timestamp)
    # The POOL is every eligible candidate plus reserved symbols...
    assert result.selected_symbols == ("SPY", "AAPL", "MSFT", "JPM")
    # ...while the OBSERVATION SET is the streamed subset at the capacity.
    assert len(result.observation_set) == 3
    assert set(result.observation_set) <= set(result.selected_symbols)
    assert result.observation_eliminated["MSFT"] == "sector_concentration"


def test_observation_set_uses_strategy_alpha_and_pins() -> None:
    strategy = portfolio_strategy_from(
        "equity_residual_momentum_v1", {"top_n": 2, "exclude_symbols": ["SPY"]}
    )
    pool = ("SPY", "AAPL", "MSFT", "JPM")
    history = {
        symbol: _series(symbol, slope)
        for symbol, slope in (("AAPL", 0.004), ("MSFT", 0.0035), ("JPM", 0.001), ("SPY", 0.002))
    }
    observation = build_observation_set(
        strategy,
        history,
        pool,
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=datetime.now(UTC),
        maximum_stream_symbols=3,
        pinned_symbols=("SPY",),
    )
    assert observation.source == "strategy_alpha"
    assert observation.observation_set[0] == "SPY"  # pinned first
    # Top residual-momentum names fill the stream slots; JPM (residual < 0
    # vs SPY) is ranked but does not fit the two remaining slots.
    assert observation.observation_set == ("SPY", "AAPL", "MSFT")
    assert observation.alpha_scored_count >= 3


def test_observation_set_falls_back_when_no_ranker() -> None:
    observation = build_observation_set(
        None,
        {},
        ("SPY", "AAPL", "MSFT", "JPM"),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=datetime.now(UTC),
        maximum_stream_symbols=3,
        pinned_symbols=("SPY",),
        scanner_fallback=("SPY", "AAPL", "JPM"),
    )
    assert observation.source == "scanner_quality_fallback"
    assert observation.observation_set == ("SPY", "AAPL", "JPM")


def test_observation_set_respects_capacity_and_rejects_overflow_pins() -> None:
    with pytest.raises(ValueError, match="exceed maximum_stream_symbols"):
        build_observation_set(
            None,
            {},
            ("A", "B"),
            market=Market.EQUITY,
            timeframe="1d",
            decision_timestamp=datetime.now(UTC),
            maximum_stream_symbols=1,
            pinned_symbols=("A", "B"),
        )


def test_validate_and_load_return_the_observation_set(tmp_path) -> None:
    config = EquityUniverseConfig(
        maximum_stream_symbols=3,
        reference_symbols=("SPY",),
        active_universe_path=str(tmp_path / "active.json"),
    )
    result = EquityUniverseScanner(config).scan(
        [
            EquityCandidate("AAPL", 200, 600_000_000, 0.03, 1.0, 2, "technology"),
            EquityCandidate("MSFT", 400, 500_000_000, 0.03, 1.0, 2, "technology"),
            EquityCandidate("JPM", 220, 300_000_000, 0.025, 0.99, 3, "financial"),
        ],
        data_timestamp=datetime.now(UTC),
    )
    EquityUniverseScanner(config).persist(result)
    assert validate_active_universe(config) == (result.observation_set, "success")
    assert load_active_symbols(config) == result.observation_set


def test_observation_set_outside_pool_is_rejected(tmp_path) -> None:
    import json

    config = EquityUniverseConfig(
        maximum_stream_symbols=3,
        reference_symbols=("SPY",),
        active_universe_path=str(tmp_path / "active.json"),
    )
    result = EquityUniverseScanner(config).scan(
        [EquityCandidate("AAPL", 200, 600_000_000, 0.03, 1.0, 2, "technology")],
        data_timestamp=datetime.now(UTC),
    )
    EquityUniverseScanner(config).persist(result)
    raw = json.loads((tmp_path / "active.json").read_text(encoding="utf-8"))
    raw["observation_set"] = ["AAPL", "ZZZ"]  # ZZZ not in the pool
    (tmp_path / "active.json").write_text(json.dumps(raw), encoding="utf-8")
    symbols, status = validate_active_universe(config)
    assert symbols == () and status == "observation_outside_pool"


def test_crypto_pool_and_observation_set_contract(tmp_path) -> None:
    config = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        maximum_stream_symbols=2,
        pinned_symbols=("BTC/USD",),
        trade_symbols=("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD"),
        active_universe_path=str(tmp_path / "active_crypto.json"),
    )
    candidates = [
        CryptoCandidate("BTC/USD", 1_000_000, 0.045, 1.0, None, "core"),
        CryptoCandidate("ETH/USD", 900_000, 0.05, 1.0, 0.9, "core"),
        CryptoCandidate("SOL/USD", 800_000, 0.055, 1.0, 0.9, "layer1"),
        CryptoCandidate("XRP/USD", 700_000, 0.06, 1.0, 0.9, "layer1"),
    ]
    result = CryptoUniverseScanner(config).scan(candidates, data_timestamp=datetime.now(UTC))
    # Pool keeps every eligible pair; the streamed observation set is capped.
    assert len(result.selected_symbols) == 4
    assert len(result.observation_set) == 2
    assert "BTC/USD" in result.observation_set
    CryptoUniverseScanner(config).persist(result)
    assert validate_active_crypto_universe(config) == (result.observation_set, "success")
    assert load_active_crypto_symbols(config) == result.observation_set


def test_residual_momentum_aligns_returns_on_timestamps() -> None:
    """A missing trading day must not shift the beta/residual pairing.

    The stock below moves IDENTICALLY to the market on the days both trade;
    only the market trades on the missing day. Timestamp alignment therefore
    produces beta == 1 and zero residuals, while positional pairing would
    produce spurious residuals.
    """
    start = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    # The market jumps +9% on day 2 (the day the stock does not trade).
    spy_closes = [100.0, 101.0, 110.0, 111.0, 112.0, 113.0]
    spy_bars = tuple(
        _bar("SPY", start + timedelta(days=index), close, Market.EQUITY)
        for index, close in enumerate(spy_closes)
    )
    # The stock trades at the market's exact price on every day BOTH trade
    # (days 0,1,3,4,5 — day 2 is the stock's missing session): its return
    # over [d1,d3] therefore equals the market's over [d1,d3], never [d1,d2].
    stock_bars = tuple(
        _bar("STOCK", start + timedelta(days=day), close, Market.EQUITY)
        for day, close in ((0, 100.0), (1, 101.0), (3, 111.0), (4, 112.0), (5, 113.0))
    )
    # The strategy hoists the market close-by-timestamp lookup once per
    # decision; the contract test builds the same lookup explicitly.
    market_close_by_timestamp = {bar.timestamp: bar.close for bar in spy_bars}
    aligned = _aligned_returns(stock_bars, market_close_by_timestamp, window=len(spy_closes) - 1)
    assert aligned is not None
    stock_returns, market_returns = aligned
    # Every aligned pair is the SAME calendar interval: the stock's return
    # over [d1,d3] (101 -> 111 = +9.9%) pairs with the market's [d1,d3]
    # return, not the market's [d1,d2] jump (+8.9%).
    assert len(stock_returns) == 4
    assert all(abs(s - m) < 1e-9 for s, m in zip(stock_returns, market_returns))
    beta = _ols_beta(stock_returns, market_returns)
    assert beta == pytest.approx(1.0, abs=1e-9)
    residuals = [s - beta * m for s, m in zip(stock_returns, market_returns)]
    assert all(abs(residual) < 1e-9 for residual in residuals)


def test_required_timeframes_join_the_aggregation_union() -> None:
    from parayabuck.runtime import required_aggregation_timeframes

    class FakeStrategy:
        timeframe = "1d"
        required_timeframes = ("1h",)

    class PlainStrategy:
        timeframe = "4h"
        required_timeframes = ()

    strategy_specs = (
        (FakeStrategy(), ("A",), 0.5),
        (PlainStrategy(), ("B",), 0.1),
    )
    timeframes = required_aggregation_timeframes("5m", "5m", strategy_specs)
    assert timeframes == frozenset({"5m", "1d", "1h", "4h"})
    # A strategy that declares required timeframes exposes them in its spec.
    strategy = portfolio_strategy_from("equity_residual_momentum_v1")
    assert strategy.spec().required_timeframes == ()
