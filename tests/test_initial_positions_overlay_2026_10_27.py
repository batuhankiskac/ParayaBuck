"""Initial positions on the portfolio engine and the PAPER overlay helper.

Audit item 38. Each test pins one invariant on small synthetic bars: the flat
default is unchanged, seeded holdings enter the first equity point and the FIFO
lot book, a sale realises the seeded cost basis, negative cash fails closed,
and the overlay and CSV loader compute the documented statistics. No network,
no production paths, no credentials.
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from types import SimpleNamespace

import pytest

from parayabuck.backtest.paper_overlay import load_paper_equity_csv, paper_overlay
from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import downsize_gain_fraction
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)  # 09:30 New York


def _bar(day: int, symbol: str, open_price: float, close_price: float) -> Bar:
    return Bar(
        START + timedelta(days=day),
        symbol,
        "1d",
        open_price,
        max(open_price, close_price) * 1.001,
        min(open_price, close_price) * 0.999,
        close_price,
        1_000_000.0,
        Market.EQUITY,
    )


def _bars(days: int) -> list[Bar]:
    """QLD opens at 100 + day and closes 1 higher; SPY is flat at 400."""
    bars: list[Bar] = []
    for day in range(days):
        bars.append(_bar(day, "QLD", 100.0 + day, 101.0 + day))
        bars.append(_bar(day, "SPY", 400.0, 400.0))
    return bars


class _ScheduledTargets(PortfolioStrategy):
    """Holds the weights of the latest scheduled day at or before each decision."""

    name = "scheduled_targets_test"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, schedule: dict[int, dict[str, float]]) -> None:
        self._schedule = dict(schedule)
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        day = (context.decision_timestamp - START).days
        due = [key for key in self._schedule if key <= day]
        weights = self._schedule[max(due)] if due else {}
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=tuple(TargetWeight(symbol, weight) for symbol, weight in weights.items()),
            cash_weight=1.0 - sum(weights.values()),
            universe_version="test",
        )


def _engine(
    schedule: dict[int, dict[str, float]],
    *,
    starting_equity: float = 100_000.0,
    initial_positions: dict[str, tuple[float, float]] | None = None,
    initial_cash: float | None = None,
) -> PortfolioBacktestEngine:
    # Zero costs and a 10% turnover band: a 1% weight gap never trades, a
    # 50% target gap always does. Daily decisions via the 24-hour cadence.
    return PortfolioBacktestEngine(
        _ScheduledTargets(schedule),
        RebalancePolicy(Market.EQUITY, "hours:24"),
        starting_equity=starting_equity,
        slippage_bps=0.0,
        spread_bps=0.0,
        commission=0.0,
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        turnover_band=0.1,
        minimum_order_notional=0.0,
        initial_positions=initial_positions,
        initial_cash=initial_cash,
    )


def test_flat_default_and_empty_initial_state_reproduce_the_baseline() -> None:
    bars = _bars(5)
    schedule = {0: {"QLD": 0.5}}
    baseline = _engine(schedule).run(bars, liquidate_at_end=False)
    explicit_none = _engine(schedule, initial_positions=None, initial_cash=None).run(bars, liquidate_at_end=False)
    explicit_empty = _engine(schedule, initial_positions={}).run(bars, liquidate_at_end=False)
    assert explicit_none.equity_curve == baseline.equity_curve
    assert explicit_none.fills == baseline.fills
    assert explicit_empty.equity_curve == baseline.equity_curve
    assert explicit_empty.fills == baseline.fills
    assert baseline.starting_equity == 100_000.0


def test_initial_positions_show_in_the_first_equity_point_without_a_fill() -> None:
    bars = _bars(3)
    # 10 QLD at 50 USD cost. QLD opens at 100 on day 0, so the holdings are worth
    # 1000 at the open and the cash is 99,000 out of the 100,000 starting equity.
    engine = _engine({}, initial_positions={"QLD": (10.0, 50.0)})
    result = engine.run(bars, liquidate_at_end=False)
    assert result.starting_equity == pytest.approx(100_000.0)
    assert result.fills == ()  # no order, fee or slippage for the pre-existing holdings
    # First equity point: 99,000 cash plus 10 shares marked at the day-0 close of 101.
    assert result.equity_curve[0] == pytest.approx(99_000.0 + 10 * 101.0)
    assert result.equity_curve[1] == pytest.approx(99_000.0 + 10 * 102.0)


def test_initial_positions_enter_the_fifo_lot_book_and_gain_api() -> None:
    bars = _bars(4)
    engine = _engine({0: {"QLD": 0.5}}, initial_positions={"QLD": (10.0, 50.0)})
    result = engine.run(bars, liquidate_at_end=False)
    buys = [fill for fill in result.fills if fill.side == OrderSide.BUY]
    assert buys, "the strategy should add QLD after the seeded holding"
    buy_qty, buy_price = buys[0].qty, buys[0].fill_price
    assert buy_price == pytest.approx(101.0)  # zero costs: the next open
    lots = engine._positions["QLD"].lots
    # Oldest first: the seeded lot at its supplied cost, then the bought lot at its fill.
    assert lots == [pytest.approx([10.0, 50.0]), pytest.approx([buy_qty, buy_price])]
    # The gain band's FIFO gain fraction reads the seeded basis for the first 10 units.
    price = 110.0
    units = 12.0
    expected_gain = 10.0 * (price - 50.0) + (units - 10.0) * (price - buy_price)
    assert downsize_gain_fraction(lots, units, price) == pytest.approx(expected_gain / (units * price))
    assert result.state.avg_prices["QLD"] == pytest.approx((10.0 * 50.0 + buy_qty * buy_price) / (10.0 + buy_qty))


def test_full_sale_realises_the_seeded_cost_basis_and_clears_the_lots() -> None:
    bars = _bars(6)
    engine = _engine({0: {"QLD": 0.5}, 3: {}}, initial_positions={"QLD": (10.0, 50.0)})
    result = engine.run(bars, liquidate_at_end=False)
    buys = [fill for fill in result.fills if fill.side == OrderSide.BUY]
    sells = [fill for fill in result.fills if fill.side == OrderSide.SELL]
    assert sells, "the day-3 zero target should exit QLD"
    held = 10.0 + sum(fill.qty for fill in buys)
    sold = sum(fill.qty for fill in sells)
    assert sold == pytest.approx(held)
    average_cost = (10.0 * 50.0 + sum(fill.qty * fill.fill_price for fill in buys)) / held
    sale_price = sells[0].fill_price
    realised = sum(delta for _, symbol, delta in result.realized_pnl_events if symbol == "QLD")
    assert realised == pytest.approx((sale_price - average_cost) * held)
    assert "QLD" not in engine._positions
    assert result.closed_positions == 1


def test_negative_initial_cash_raises() -> None:
    # 100 QLD at an open of 100 costs 10,000 but only 1,000 of equity is available.
    engine = _engine({}, starting_equity=1_000.0, initial_positions={"QLD": (100.0, 50.0)})
    with pytest.raises(ValueError, match="negative"):
        engine.run(_bars(2), liquidate_at_end=False)


def test_explicit_initial_cash_sets_the_starting_equity_from_cash_plus_holdings() -> None:
    engine = _engine({}, initial_positions={"QLD": (10.0, 50.0)}, initial_cash=500.0)
    result = engine.run(_bars(2), liquidate_at_end=False)
    assert result.starting_equity == pytest.approx(500.0 + 10.0 * 100.0)
    assert result.equity_curve[0] == pytest.approx(500.0 + 10.0 * 101.0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"initial_cash": -1.0}, "initial_cash"),
        ({"initial_positions": {"QLD": (0.0, 50.0)}}, "quantity"),
        ({"initial_positions": {"QLD": (1.0, -5.0)}}, "cost basis"),
        ({"initial_positions": {"QLD": (1.0, 50.0), "qld": (2.0, 50.0)}}, "more than once"),
    ],
)
def test_malformed_initial_state_is_rejected(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _engine({}, **kwargs)  # type: ignore[arg-type]


def test_initial_position_without_bars_fails_closed() -> None:
    engine = _engine({}, initial_positions={"NVDA": (1.0, 50.0)})
    with pytest.raises(ValueError, match="no bars"):
        engine.run(_bars(2), liquidate_at_end=False)


def _series(start: date, values: list[float]) -> list[tuple[date, float]]:
    return [(start + timedelta(days=offset), value) for offset, value in enumerate(values)]


def _curve(start: date, values: list[float]) -> SimpleNamespace:
    """A backtest-result stand-in: one 09:30 New York bar per consecutive day."""
    first = datetime(start.year, start.month, start.day, 14, 30, tzinfo=UTC)
    timestamps = tuple(first + timedelta(days=offset) for offset in range(len(values)))
    return SimpleNamespace(equity_curve=tuple(values), curve_timestamps=timestamps)


def test_overlay_tracking_statistics_on_two_synthetic_curves() -> None:
    start = date(2026, 1, 5)
    backtest = _curve(start, [100.0, 101.0, 102.0, 103.0, 104.0])
    paper = _series(start, [100.0, 101.0, 99.0, 103.0, 105.0])
    overlay = paper_overlay(backtest, paper)
    summary = overlay["summary"]
    rows = overlay["rows"]

    assert summary["common_days"] == 5
    assert [row["ratio"] for row in rows[:2]] == pytest.approx([1.0, 1.0])
    assert rows[2]["gap"] == pytest.approx(0.99 / 1.02 - 1.0)
    assert summary["max_abs_gap"] == pytest.approx(abs(0.99 / 1.02 - 1.0))
    assert summary["max_abs_gap_date"] == "2026-01-07"
    assert summary["first_threshold_breach_date"] == "2026-01-07"  # |gap| 2.94% > 2%
    assert summary["tracking_difference"] == pytest.approx(105.0 / 104.0 - 1.0)

    paper_returns = [b / a - 1.0 for a, b in pairwise([100.0, 101.0, 99.0, 103.0, 105.0])]
    bt_returns = [b / a - 1.0 for a, b in pairwise([100.0, 101.0, 102.0, 103.0, 104.0])]
    differences = [p - b for p, b in zip(paper_returns, bt_returns, strict=True)]
    assert summary["tracking_error_annualised"] == pytest.approx(statistics.stdev(differences) * math.sqrt(252.0))


def test_overlay_normalises_on_the_first_common_date_and_breaches_only_above_threshold() -> None:
    start = date(2026, 1, 5)
    backtest = _curve(start, [100.0, 101.0, 102.0, 103.0, 104.0])
    # PAPER starts on day 1 at 202 and is exactly 2x the backtest from day 1 on: no gap.
    paper = _series(start + timedelta(days=1), [202.0, 204.0, 206.0, 208.0])
    overlay = paper_overlay(backtest, paper)
    assert overlay["summary"]["common_days"] == 4
    assert overlay["summary"]["first_common_date"] == "2026-01-06"
    assert overlay["summary"]["max_abs_gap"] == pytest.approx(0.0, abs=1e-9)
    assert overlay["summary"]["first_threshold_breach_date"] is None
    assert overlay["summary"]["tracking_difference"] == pytest.approx(0.0, abs=1e-9)


def test_overlay_rejects_fewer_than_two_common_dates() -> None:
    start = date(2026, 1, 5)
    backtest = _curve(start, [100.0, 101.0])
    with pytest.raises(ValueError, match="at least 2"):
        paper_overlay(backtest, [(date(2026, 3, 1), 100.0), (date(2026, 3, 2), 101.0)])


def test_overlay_rejects_bad_paper_points() -> None:
    start = date(2026, 1, 5)
    backtest = _curve(start, [100.0, 101.0, 102.0])
    with pytest.raises(ValueError, match="duplicate"):
        paper_overlay(backtest, [(start, 100.0), (start, 101.0)])
    with pytest.raises(ValueError, match="positive and finite"):
        paper_overlay(backtest, [(start, 100.0), (start + timedelta(days=1), math.nan)])


def test_csv_loader_reads_a_header_and_plain_rows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "paper.csv"
    path.write_text("date,equity\n2026-01-05,100000\n2026-01-06,100500.5\n\n", encoding="utf-8")
    assert load_paper_equity_csv(path) == [(date(2026, 1, 5), 100_000.0), (date(2026, 1, 6), 100_500.5)]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("2026-01-05\n", "2 columns"),
        ("2026-01-05,100,extra\n", "got 3"),
        ("2026/01/05,100\n", "YYYY-MM-DD"),
        ("2026-01-05,abc\n", "could not convert"),
        ("2026-01-05,-5\n", "positive and finite"),
        ("2026-01-05,nan\n", "positive and finite"),
        ("2026-01-06,100\n2026-01-05,101\n", "strictly increasing"),
        ("2026-01-05,100\n2026-01-05,101\n", "strictly increasing"),
        ("", "no data rows"),
        ("date,equity\n", "no data rows"),
    ],
)
def test_csv_loader_fails_closed_on_malformed_rows(tmp_path, body: str, message: str) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "bad.csv"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_paper_equity_csv(path)
