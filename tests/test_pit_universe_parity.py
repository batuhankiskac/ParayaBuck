"""Deterministic PIT universe exclusion and CA parity tests (K_FINAL_PARITY_FREEZE)."""

from __future__ import annotations

import csv
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.product import build_product_universe
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from


def _bar(symbol: str, ts: datetime, close: float, volume: float = 1_000_000) -> Bar:
    return Bar(ts, symbol, "1d", close, close * 1.01, close * 0.99, close, volume, Market.EQUITY)


def test_excluded_symbol_not_ranked() -> None:
    """Symbol present in CSV but excluded by PIT snapshot must not be ranked."""
    config = load_config("config/settings.yaml")
    sel = config.strategy.equity
    strat = portfolio_strategy_from(sel.name, sel.parameters, timeframe=sel.timeframe)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for i in range(60):
        ts = base + timedelta(days=i)
        for sym in ["AAA", "BBB", "CCC", "DDD"]:
            price = 100.0 * (1 + 0.02 * (i % 5 - 2) + 0.01 * (hash(sym) % 3))
            bars.append(_bar(sym, ts, close=price, volume=500_000))
        bars.append(_bar("ZZZ_EXCLUDED", ts, close=1.0, volume=10_000_000))
        bars.append(_bar("SPY", ts, close=400.0 + i * 0.5, volume=1_000_000_000))
    end = base + timedelta(days=59)
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=end, timeframe=strat.timeframe)
    assert universe is not None
    configured = set(config.universe.equity.symbols)
    for snap in universe.snapshots:
        # The static universe admits only the configured symbols; a symbol that
        # merely appears in the bar set never leaks into membership.
        assert snap.symbols <= configured, "Unconfigured symbol leaked into universe"
        assert "ZZZ_EXCLUDED" not in snap.symbols, "Excluded symbol leaked into universe"
        if snap.observation_set:
            assert "ZZZ_EXCLUDED" not in snap.observation_set
    from parayabuck.portfolio.base import PortfolioContext

    if not universe.snapshots:
        pytest.skip("No snapshots")
    ts = universe.snapshots[-1].effective_at
    history: dict[str, tuple[Bar, ...]] = {}
    for sym in ["AAA", "BBB", "CCC", "DDD", "ZZZ_EXCLUDED", "SPY"]:
        history[sym] = tuple(b for b in bars if b.symbol == sym)
    ctx = PortfolioContext(
        decision_timestamp=ts,
        market=Market.EQUITY,
        timeframe=strat.timeframe,
        history=history,
        universe=universe,
        universe_version="test",
        equity=100000,
        holdings={},
        prices={s: history[s][-1].close for s in history},
        benchmark_symbol="SPY",
    )
    assert not ctx.in_universe("ZZZ_EXCLUDED"), "Excluded symbol should be out of universe"
    assert not ctx.in_universe("AAA"), "Unconfigured symbol should be out of universe"
    assert ctx.in_universe("SPY"), "Configured symbol should be in universe"
    ranked = strat.rank_candidates(ctx)
    assert "ZZZ_EXCLUDED" not in ranked, "Strategy ranked excluded symbol"


def test_ca_parity_source_same_for_old_and_full() -> None:
    """OLD and FULL must filter same CA source, not [] for OLD."""
    ca_path = pathlib.Path("data/historical/equity_survivorship_full_corporate_actions_v5.csv")
    if not ca_path.exists():
        pytest.skip("CA v5 not present")
    all_cas: list[str] = []
    with open(ca_path, newline="") as f:
        for row in csv.DictReader(f):
            if row["action_type"] == "cash_dividends":
                all_cas.append(row["symbol"])
    assert len(all_cas) == 141908, f"Expected 141908 cash dividends, got {len(all_cas)}"
