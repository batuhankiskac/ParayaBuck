"""Native-context regression tests for the high-proximity history contract.

These tests do not run an economic backtest.  They build a real
``PortfolioBacktestEngine`` context from a small causal bar fixture and expose
the difference between the frozen adapter's inherited 274-bar trim and the
research-only 364-bar history contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import rebalance_policy_for
from scripts.research.high_proximity364_history_fixed_2026_09_05 import (
    HighProximity364HistoryFixedAdapter,
)
from scripts.research.high_proximity364_trace_2026_09_05 import TraceHighProximity364
from scripts.research.portfolio_expansion_crypto_screen_2026_09 import HighProximity364Adapter


DECISION = datetime(2025, 1, 1, tzinfo=UTC)


def _bar(symbol: str, timestamp: datetime, close: float) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        market=Market.CRYPTO,
    )


def _universe() -> PointInTimeUniverse:
    # The raw fixture contains a third symbol, while the observed stream only
    # contains BTC and ETH.  This keeps raw membership and observed membership
    # distinct without mocking PortfolioContext.
    return PointInTimeUniverse(
        market=Market.CRYPTO,
        methodology="native-context-fixture",
        snapshots=(
            UniverseSnapshot(
                effective_at=datetime(2024, 1, 1, tzinfo=UTC),
                symbols=frozenset({"BTC/USD", "ETH/USD", "SOL/USD"}),
                observation_set=("BTC/USD", "ETH/USD"),
            ),
        ),
    )


def _engine(strategy: object) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),  # type: ignore[arg-type]
        benchmark_symbol="BTC/USD",
        universe=_universe(),
        universe_version="fixture-v1",
        allow_static_universe=True,
        state_path=None,
    )


def _bars() -> list[Bar]:
    bars: list[Bar] = []
    start = DECISION - timedelta(days=363)
    for index in range(364):
        timestamp = start + timedelta(days=index)
        # BTC is a steadily rising series; ETH is a distinct rising series so
        # rank direction is observable once the full window is visible.
        bars.extend(
            (
                _bar("BTC/USD", timestamp, 100.0 + index),
                _bar("ETH/USD", timestamp, 200.0 + 2.0 * index),
                _bar("SOL/USD", timestamp, 300.0 + 3.0 * index),
            )
        )
    # A future bar must not leak into a decision at DECISION.
    bars.append(_bar("ETH/USD", DECISION + timedelta(days=1), 9999.0))
    return bars


def _native_context(strategy: object):
    engine = _engine(strategy)
    for bar in _bars():
        engine._ingest(bar)  # native ingestion; no mocked counts or globals
    return engine._build_context(DECISION)


def test_frozen_adapter_exposes_benchmark_exception_and_nonbenchmark_trim() -> None:
    strategy = HighProximity364Adapter({"top_n": 2, "rebalance_hours": 96})
    context = _native_context(strategy)

    assert len(context.history["BTC/USD"]) == 364  # crypto benchmark exempt
    assert len(context.history["ETH/USD"]) == 274  # max(23, native 274 floor)
    assert context.history["ETH/USD"][-1].timestamp == DECISION
    assert tuple(symbol for _score, symbol in strategy._score_candidates(context)) == ("BTC/USD",)
    assert strategy.decide(context).metadata["selected_symbols"] == ("BTC/USD",)


def test_history_fixed_adapter_preserves_causal_window_and_rank_direction() -> None:
    strategy = HighProximity364HistoryFixedAdapter({"top_n": 2, "rebalance_hours": 96})
    context = _native_context(strategy)

    assert strategy.minimum_history_bars == 364
    assert len(context.history["BTC/USD"]) == 364
    assert len(context.history["ETH/USD"]) == 364
    assert context.history["ETH/USD"][-1].timestamp == DECISION
    assert all(bar.timestamp <= DECISION for bars in context.history.values() for bar in bars)
    assert tuple(symbol for _score, symbol in strategy._score_candidates(context)) == ("BTC/USD", "ETH/USD")
    target = strategy.decide(context)
    assert {weight.symbol for weight in target.weights} == {"BTC/USD", "ETH/USD"}
    assert target.parameters == (("rebalance_hours", 96), ("top_n", 2))
    assert target.strategy_name == strategy.name
    assert target.strategy_version == strategy.version


def test_observed_membership_is_smaller_than_raw_membership_without_leaking_sol() -> None:
    strategy = HighProximity364HistoryFixedAdapter({"top_n": 2, "rebalance_hours": 96})
    context = _native_context(strategy)

    assert set(_universe().snapshots[0].symbols) == {"BTC/USD", "ETH/USD", "SOL/USD"}
    assert set(context.history) == {"BTC/USD", "ETH/USD"}
    assert "SOL/USD" not in context.history
    assert context.decision_timestamp == DECISION


def test_forwarding_observer_preserves_original_target_identity_and_return() -> None:
    parameters = {"top_n": 2, "rebalance_hours": 96}
    original = HighProximity364Adapter(parameters)
    forwarding = TraceHighProximity364(parameters)
    original_target = original.decide(_native_context(original))
    forwarding_target = forwarding.decide(_native_context(forwarding))

    assert forwarding.name == original.name
    assert forwarding.version == original.version
    assert forwarding.parameter_tuple() == original.parameter_tuple()
    assert forwarding_target == original_target
    assert forwarding.decision_observations[-1]["target"] == ["BTC/USD"]
    assert [row["symbol"] for row in forwarding.score_observations[-1]["ranked"]] == ["BTC/USD"]
    assert forwarding.score_observations[-1]["ranked"][0]["rank"] == 1
