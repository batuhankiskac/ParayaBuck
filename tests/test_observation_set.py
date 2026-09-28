"""Broad-pool alpha-before-stream contract tests.

The stream limit is NOT the opportunity universe: the crypto scanner emits
the eligible POOL (tradability-first), the strategy alpha ranks the pool on
batch history, and the top ``maximum_stream_symbols`` plus pins become the
streamed OBSERVATION SET. The equity universe is static and needs no ranker.
Also covers the required-timeframes aggregation contract.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.config import CryptoUniverseConfig
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.universe import load_active_crypto_symbols, validate_active_crypto_universe
from parayabuck.universe.crypto_scanner import (
    CryptoCandidate,
    CryptoUniverseScanner,
)
from parayabuck.universe.observation_set import build_observation_set


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


def test_strategy_fallback_leaves_capacity_empty_for_young_or_stale_symbols() -> None:
    class _Ranker:
        name = "test_ranker"
        minimum_history_bars = 3

        @staticmethod
        def rank_candidates(_context: PortfolioContext) -> tuple[str, ...]:
            return ("READY",)

    start = datetime(2025, 1, 1, tzinfo=UTC)

    def series(symbol: str, days: tuple[int, ...]) -> tuple[Bar, ...]:
        return tuple(_bar(symbol, start + timedelta(days=day), 100.0 + day, Market.EQUITY) for day in days)

    observation = build_observation_set(
        _Ranker(),  # type: ignore[arg-type]
        {
            "SPY": series("SPY", (0, 1, 2)),
            "READY": series("READY", (0, 1, 2)),
            "YOUNG": series("YOUNG", (1, 2)),
            "STALE": series("STALE", (0, 1, 1)),
        },
        ("SPY", "READY", "YOUNG", "STALE"),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=start + timedelta(days=2),
        maximum_stream_symbols=4,
        pinned_symbols=("SPY",),
        scanner_fallback=("YOUNG", "STALE"),
    )

    assert observation.observation_set == ("SPY", "READY")
    assert observation.source == "strategy_alpha"


def test_strategy_ranked_stale_symbol_is_excluded_but_stale_pin_is_preserved() -> None:
    class _Ranker:
        name = "test_ranker"
        minimum_history_bars = 3

        @staticmethod
        def rank_candidates(_context: PortfolioContext) -> tuple[str, ...]:
            return ("STALE", "READY", "PIN")

    start = datetime(2025, 1, 1, tzinfo=UTC)

    def series(symbol: str, days: tuple[int, ...]) -> tuple[Bar, ...]:
        return tuple(_bar(symbol, start + timedelta(days=day), 100.0 + day, Market.EQUITY) for day in days)

    observation = build_observation_set(
        _Ranker(),  # type: ignore[arg-type]
        {
            "SPY": series("SPY", (0, 1, 2)),
            "READY": series("READY", (0, 1, 2)),
            "STALE": series("STALE", (0, 1, 1)),
            "PIN": series("PIN", (0, 1, 1)),
        },
        ("SPY", "READY", "STALE", "PIN"),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=start + timedelta(days=2),
        maximum_stream_symbols=3,
        pinned_symbols=("SPY", "PIN"),
    )

    assert observation.observation_set == ("SPY", "PIN", "READY")
    assert observation.ranked_pool == ("READY",)
    assert observation.alpha_scored_count == 1
    assert observation.source == "strategy_alpha"


def test_ranked_and_fallback_names_without_history_are_skipped() -> None:
    """A ranker may name a symbol the caller never loaded history for (and a
    strategy may declare no minimum): the name is skipped, not a KeyError."""

    class _Ranker:
        name = "test_ranker"
        minimum_history_bars = 0

        @staticmethod
        def rank_candidates(_context: PortfolioContext) -> tuple[str, ...]:
            return ("GHOST", "READY")

    start = datetime(2025, 1, 1, tzinfo=UTC)

    def series(symbol: str, days: tuple[int, ...]) -> tuple[Bar, ...]:
        return tuple(_bar(symbol, start + timedelta(days=day), 100.0 + day, Market.EQUITY) for day in days)

    observation = build_observation_set(
        _Ranker(),  # type: ignore[arg-type]
        {
            "SPY": series("SPY", (0, 1, 2)),
            "READY": series("READY", (0, 1, 2)),
        },
        ("SPY", "READY", "GHOST"),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=start + timedelta(days=2),
        maximum_stream_symbols=2,
        scanner_fallback=("GHOST", "READY"),
    )

    assert observation.observation_set == ("READY",)
    assert observation.ranked_pool == ("READY",)


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


def test_observation_set_outside_pool_is_rejected(tmp_path) -> None:
    """A persisted observation set that names a symbol outside the selected
    pool must fail closed instead of being streamed."""
    config = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        maximum_stream_symbols=2,
        pinned_symbols=("BTC/USD",),
        trade_symbols=("BTC/USD", "ETH/USD"),
        active_universe_path=str(tmp_path / "active_crypto.json"),
    )
    result = CryptoUniverseScanner(config).scan(
        [
            CryptoCandidate("BTC/USD", 1_000_000, 0.045, 1.0, None, "core"),
            CryptoCandidate("ETH/USD", 900_000, 0.05, 1.0, 0.9, "core"),
        ],
        data_timestamp=datetime.now(UTC),
    )
    CryptoUniverseScanner(config).persist(result)
    raw = json.loads(Path(config.active_universe_path).read_text(encoding="utf-8"))
    raw["observation_set"] = [*result.observation_set, "ZZZ/USD"]  # ZZZ not in the pool
    Path(config.active_universe_path).write_text(json.dumps(raw), encoding="utf-8")
    assert validate_active_crypto_universe(config) == ((), "observation_outside_pool")


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
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1")
    assert strategy.spec().required_timeframes == ()
