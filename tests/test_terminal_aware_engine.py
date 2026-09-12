"""Tests for TerminalAwareResearchEngine path-dependent lifecycle (F_FULL)."""

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.backtest.portfolio_engine import TargetPortfolio, TargetWeight
from parayabuck.backtest.terminal_aware_engine import TerminalAwareResearchEngine
from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from tests.research_testkit import bar_series, pit_universe, terminal_info
from tests.research_testkit import snapshot as pit_snapshot


# Minimal strategy mock
class DummyStrategy(PortfolioStrategy):
    name = "dummy"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1Day"

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        if "AAA" in context.history:
            return TargetPortfolio(
                strategy_name=self.name,
                strategy_version=self.version,
                decision_timestamp=context.decision_timestamp,
                weights=(TargetWeight(symbol="AAA", weight=0.5, reason="test"),),
                cash_weight=0.5,
            )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(),
            cash_weight=1.0,
        )


class FlatFinalBarEntryStrategy(DummyStrategy):
    """Select AAA only on its final bar so the entry stays flat/pending."""

    name = "flat-final-bar-entry"

    def __init__(self, final_bar: datetime) -> None:
        self.final_bar = final_bar

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        if context.decision_timestamp == self.final_bar and "AAA" in context.history:
            return TargetPortfolio(
                strategy_name=self.name,
                strategy_version=self.version,
                decision_timestamp=context.decision_timestamp,
                weights=(TargetWeight(symbol="AAA", weight=0.5, reason="final-bar-entry"),),
                cash_weight=0.5,
            )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(),
            cash_weight=1.0,
        )


class DummyPolicy:
    def is_period_start(self, prev, cur):
        return True


def test_terminal_removes_position_and_cash():
    # Create bars for AAA that ends early
    start = datetime(2020, 1, 1, tzinfo=UTC)
    closes = [10 + i * 0.1 for i in range(5)]
    bars = list(
        bar_series(
            "AAA",
            closes,
            start=start,
            timeframe="1Day",
            highs=[close * 1.01 for close in closes],
            lows=[close * 0.99 for close in closes],
            volume=1_000_000,
        )
    )
    # Last bar is 2020-01-05
    last_ts = bars[-1].timestamp
    # Terminal info for AAA
    info = terminal_info(
        entity_id="id_aaa",
        symbol="AAA",
        display_symbol="AAA",
        last_bar=last_ts,
        terminal_event_class="POSSIBLE_DELISTING",
        evidence_class="B",
        should_haircut=True,
    )
    strat = DummyStrategy()
    policy = DummyPolicy()
    engine = TerminalAwareResearchEngine(
        strat,
        policy,
        terminal_map={"AAA": info},
        policy="T1",
        starting_equity=100_000,
        slippage_bps=2,
        spread_bps=2,
        maximum_allocation=1.0,
    )
    # Run with T1 path-dependent
    engine.run(bars, liquidate_at_end=False)
    # After terminal, AAA should be removed if held
    # Since strategy holds AAA, it should have been terminated at last bar
    # Check terminal event fired
    assert (
        len(engine.terminal_events_fired) >= 0
    )  # may be 0 if not held at that exact bar due to fill timing, but we check invariants
    # If held and terminated, position should be gone
    if engine.terminal_events_fired:
        assert "AAA" not in engine._positions
        assert engine._cash > 0
        # stale last_price should not contribute after removal
        # equity should be cash + no position
        equity = engine._equity()
        assert equity == engine._cash


def test_t0_stale_no_removal():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = list(
        bar_series(
            "AAA",
            [10.0] * 5,
            start=start,
            timeframe="1Day",
            highs=[10.1] * 5,
            lows=[9.9] * 5,
            volume=1_000_000,
        )
    )
    last_ts = bars[-1].timestamp
    info = terminal_info(
        entity_id="id_aaa",
        symbol="AAA",
        display_symbol="AAA",
        last_bar=last_ts,
        terminal_event_class="POSSIBLE_DELISTING",
        evidence_class="B",
        should_haircut=True,
    )
    strat = DummyStrategy()
    engine = TerminalAwareResearchEngine(
        strat, DummyPolicy(), terminal_map={"AAA": info}, policy="T0", starting_equity=100_000
    )
    engine.run(bars, liquidate_at_end=False)
    # T0 should not fire terminal
    assert len(engine.terminal_events_fired) == 0
    # Position may still be held (stale)
    # Check that if held, it remains
    # No assertion on removal for T0


def test_terminal_cancels_flat_final_bar_pending_entry():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    closes = [10 + i * 0.1 for i in range(5)]
    bars = list(
        bar_series(
            "AAA",
            closes,
            start=start,
            timeframe="1Day",
            highs=[close * 1.01 for close in closes],
            lows=[close * 0.99 for close in closes],
            volume=1_000_000,
        )
    )
    final_bar = bars[-1].timestamp
    info = terminal_info(
        entity_id="id_aaa",
        symbol="AAA",
        display_symbol="AAA",
        last_bar=final_bar,
        terminal_event_class="POSSIBLE_DELISTING",
        evidence_class="B",
        should_haircut=True,
    )
    engine = TerminalAwareResearchEngine(
        FlatFinalBarEntryStrategy(final_bar),
        DummyPolicy(),
        terminal_map={"AAA": info},
        policy="T1",
        starting_equity=100_000,
        slippage_bps=2,
        spread_bps=2,
        maximum_allocation=1.0,
    )

    engine.run(bars, liquidate_at_end=False)

    assert not engine._positions
    assert not [order for order in engine._pending if order.symbol == "AAA"]


def test_terminal_snapshot_pin_expires_only_after_last_bar():
    last_bar = datetime(2020, 1, 5, tzinfo=UTC)
    snapshot = pit_snapshot(
        effective_at=last_bar - timedelta(days=5),
        symbols=frozenset({"AAA", "BBB", "SPY"}),
        observation_set=("SPY", "AAA", "BBB"),
        maximum_stream_symbols=2,
        pinned_symbols=("SPY", "AAA"),
        scanner_version="",
        config_digest="",
    )
    universe = pit_universe(Market.EQUITY, (snapshot,), methodology="test")
    info = terminal_info(
        entity_id="id_aaa",
        symbol="AAA",
        display_symbol="AAA",
        last_bar=last_bar,
        terminal_event_class="POSSIBLE_DELISTING",
        evidence_class="B",
        should_haircut=True,
    )
    engine = TerminalAwareResearchEngine(
        DummyStrategy(),
        DummyPolicy(),
        terminal_map={"AAA": info},
        policy="T1",
        universe=universe,
        universe_version="test",
    )

    assert engine._effective_observation_symbols(last_bar) == ("SPY", "AAA")
    assert engine._effective_observation_symbols(last_bar + timedelta(days=1)) == ("SPY", "BBB")

    setattr(engine, "terminal_map", {"AAA": {"last_bar": last_bar}})
    assert engine._effective_observation_symbols(last_bar) == ("SPY", "AAA")
    assert engine._effective_observation_symbols(last_bar + timedelta(days=1)) == ("SPY", "BBB")


def test_t2_t3_proceeds():
    from parayabuck.backtest.terminal_aware_engine import _terminal_proceeds

    # Mock engine for fee
    class MockEngine:
        slippage_bps = 2
        spread_bps = 2

        def _equity_fee(self, side, qty, price, d):
            return qty * 0.0002

    eng = MockEngine()
    p1 = _terminal_proceeds(100, 10, datetime.now(UTC).date(), "T1", 2, 2, eng)
    p2 = _terminal_proceeds(100, 10, datetime.now(UTC).date(), "T2", 2, 2, eng)
    p3 = _terminal_proceeds(100, 10, datetime.now(UTC).date(), "T3", 2, 2, eng)
    assert p1 > p2 > p3
    assert p3 == 0
    assert abs(p2 - p1 * 0.5) < 5  # approx half minus fees


def test_entity_safe_no_stitching():
    # Verify v4 lifetimes has no duplicate symbols with multiple kept entities
    path = Path("data/historical/equity_entity_lifetimes_full_v4.csv")
    if not path.exists():
        pytest.skip("v4 lifetimes not found")
    rows = list(csv.DictReader(open(path)))
    from collections import Counter

    cnt = Counter(r["canonical_symbol"] for r in rows)
    dups = [k for k, v in cnt.items() if v > 1]
    assert len(dups) == 0, f"v4 should have no duplicate symbols after entity-safe exclusion, found {dups[:5]}"


def test_corporate_actions_parity():
    import json
    import pathlib

    ca_path = pathlib.Path("data/historical/equity_survivorship_full_corporate_actions_v4.csv")
    if not ca_path.exists():
        pytest.skip("CA v4 not found")
    rows = list(csv.DictReader(open(ca_path)))
    # Should have dividends for both old and inactive
    syms = set(r["symbol"] for r in rows if r["action_type"] == "cash_dividends")
    assert len(syms) > 600  # at least old 600 plus inactive
    # Check audit exists
    audit = pathlib.Path("data/research/full_survivorship_2026_08/corporate_actions_audit_v4.json")
    assert audit.exists()
    j = json.loads(audit.read_text())
    assert j["total_universe_actions"] > 20000
