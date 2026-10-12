"""W6 audit regression tests (2026-10-11 workstream).

Covers the backtest/live-parity fixes owned by W6: the decision-symbol
completeness gate (E01/E05), the single-run engine state reset (E02) and the
NYSE-holiday CSV guard (R2E3). The decision-time signed-trade-weight record
(R2E2) is pinned in ``tests/test_portfolio_engine_accounting.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import load_csv_bars
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.portfolio.models import RebalancePolicy

_START = datetime(2025, 1, 6, 5, tzinfo=UTC)  # a Monday


def _trend_bars(*, skip: tuple[int, str] | None = None, days: int = 22) -> list[Bar]:
    out: list[Bar] = []
    timestamp = _START
    made = 0
    while made < days:
        if timestamp.weekday() < 5:
            for offset, symbol in enumerate(("QQQ", "QLD", "SPY")):
                if skip == (made, symbol):
                    continue
                close = 100 * (1.001 ** (made + offset))
                out.append(
                    Bar(
                        timestamp,
                        symbol,
                        "1d",
                        close * 0.999,
                        close * 1.001,
                        close * 0.999,
                        close,
                        2_000_000,
                        Market.EQUITY,
                    )
                )
            made += 1
        timestamp += timedelta(days=1)
    return out


def _universe() -> PointInTimeUniverse:
    return PointInTimeUniverse(
        Market.EQUITY,
        (
            UniverseSnapshot(
                effective_at=_START,
                symbols=frozenset({"QQQ", "QLD", "SPY"}),
                observation_set=("QQQ", "QLD", "SPY"),
                maximum_stream_symbols=3,
                pinned_symbols=("QQQ", "QLD", "SPY"),
            ),
        ),
        "w6 gate fixture",
    )


def _engine() -> PortfolioBacktestEngine:
    strategy = EquityLeveragedTrendV1(
        {
            "signal_symbol": "QQQ",
            "hold_symbol": "QLD",
            "sma_bars": 5,
            "vol_target": 0.0,
            "rebalance_frequency": "hours:24",
        }
    )
    return PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(Market.EQUITY, "hours:24"),
        universe=_universe(),
        universe_version="w6",
        allow_static_universe=True,
    )


def test_missing_benchmark_bar_does_not_skip_the_session() -> None:
    full = _engine().run(_trend_bars(), liquidate_at_end=False)
    hole = _engine().run(_trend_bars(skip=(10, "SPY")), liquidate_at_end=False)
    assert len(hole.rebalances) == len(full.rebalances)


def test_missing_signal_bar_still_skips_the_session() -> None:
    full = _engine().run(_trend_bars(), liquidate_at_end=False)
    hole = _engine().run(_trend_bars(skip=(10, "QQQ")), liquidate_at_end=False)
    assert len(hole.rebalances) == len(full.rebalances) - 1


def test_engine_rerun_starts_from_a_fresh_book() -> None:
    bars = _trend_bars()
    engine = _engine()
    first = engine.run(bars, liquidate_at_end=False)
    second = engine.run(bars, liquidate_at_end=False)
    assert len(second.fills) == len(first.fills)
    assert second.rebalances[0].rebalance_number == 1
    assert [fill.qty for fill in second.fills] == [fill.qty for fill in first.fills]


def test_holiday_csv_bar_warns_and_is_dropped(tmp_path) -> None:
    path = tmp_path / "holiday.csv"
    path.write_text(
        "timestamp,symbol,timeframe,open,high,low,close,volume,market\n"
        "2023-11-23T05:00:00+00:00,SPY,1d,100,101,99,100,1000,equity\n",
        encoding="utf-8",
    )
    import logging

    records: list[str] = []

    class _Probe(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("parayabuck.backtest.product")
    probe = _Probe()
    logger.addHandler(probe)
    try:
        bars = load_csv_bars(path, default_market=Market.EQUITY)
    finally:
        logger.removeHandler(probe)
    assert bars == []
    assert any("non-session bar" in message for message in records)
