"""Audit fixes for the backtest engine, reports, PAPER overlay and R2 replay (W16).

Each test pins one invariant on tiny synthetic bars: pre-existing holdings are
marked at the first close, the cash buffer binds full and trimmed BUYs alike,
actions before the first bar and SPLIT events are refused, a final-bar decision
is terminal, report labels and the yearly fraction keys, the PAPER overlay flags
calendar gaps, engine fee defaults follow the shipped config, the R2 replay caches
return what recomputation returns, and a failed product run leaves no directory.
No network, no production paths, no credentials.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.backtest.paper_overlay import paper_overlay
from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine
from parayabuck.backtest.portfolio_report import PortfolioReport, build_portfolio_report
from parayabuck.backtest.product import run_product_backtest
from parayabuck.config import BacktestConfig, Config, StrategyConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.portfolio import equity_leveraged_trend as trend
from parayabuck.portfolio.base import PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)  # 09:30 New York

ZERO_FEES = {
    "commission": 0.0,
    "equity_use_effective_dated_fees": False,
    "equity_taf_per_share": 0.0,
    "equity_taf_cap": 0.0,
    "equity_cat_per_share": 0.0,
    "equity_sec_fee_rate": 0.0,
}


def _bar(day: int, symbol: str, open_price: float, close_price: float, volume: float = 1_000_000.0) -> Bar:
    return Bar(
        START + timedelta(days=day),
        symbol,
        "1d",
        open_price,
        max(open_price, close_price) * 1.001,
        min(open_price, close_price) * 0.999,
        close_price,
        volume,
        Market.EQUITY,
    )


class _ScheduledTargets(PortfolioStrategy):
    """Holds the weights of the latest scheduled day at or before each decision."""

    name = "w16_scheduled_targets"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, schedule: dict[int, dict[str, float]]) -> None:
        self._schedule = dict(schedule)
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context):  # type: ignore[no-untyped-def]
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


def _engine(schedule: dict[int, dict[str, float]], **overrides) -> PortfolioBacktestEngine:
    options = {
        "starting_equity": 100_000.0,
        "slippage_bps": 0.0,
        "spread_bps": 0.0,
        "benchmark_symbol": "SPY",
        "max_participation_pct": 0.10,
        "turnover_band": 0.005,
        "minimum_order_notional": 0.0,
        **ZERO_FEES,
        **overrides,
    }
    return PortfolioBacktestEngine(_ScheduledTargets(schedule), RebalancePolicy(Market.EQUITY, "hours:24"), **options)


def _flat_days(count: int, *, qld_open: float = 100.0, qld_close: float = 100.0) -> list[Bar]:
    bars: list[Bar] = []
    for day in range(count):
        bars.append(_bar(day, "QLD", qld_open, qld_close))
        bars.append(_bar(day, "SPY", 400.0, 400.0))
    return bars


# T8-1: yearly rows are fractions and their keys say so.
def test_yearly_returns_are_exported_with_fraction_keys() -> None:
    report = PortfolioReport(
        summary={},
        rebalances=(),
        fills=(),
        weight_history=(),
        universe_history=(),
        monthly_returns={},
        yearly_returns=((2025, 0.25, -0.1),),
        regime_performance={},
        rolling_beta=(),
        per_symbol=(),
        equity_curve=(),
        drawdowns=(),
    )
    assert report.to_dict()["yearly_returns"] == [{"year": 2025, "return_fraction": 0.25, "benchmark_fraction": -0.1}]


# T8-2: holdings are marked at the first close, the same mark as the first curve point.
def test_initial_holdings_are_marked_at_the_first_close_not_the_open() -> None:
    bars = [_bar(0, "QLD", 100.0, 110.0), _bar(0, "SPY", 400.0, 400.0), _bar(1, "QLD", 110.0, 111.0)]
    bars.append(_bar(1, "SPY", 400.0, 400.0))
    # A 1% target sits inside the 10% band, so the holding is kept and no order is made.
    engine = _engine({0: {"QLD": 0.01}}, initial_positions={"QLD": (10.0, 50.0)}, turnover_band=0.1)
    result = engine.run(bars, liquidate_at_end=False)
    # A first-day move from open 100 to close 110 must not be booked as return.
    assert result.equity_curve[0] == pytest.approx(100_000.0)
    assert result.fills == ()


# T8-3: the cash buffer governs a full BUY and a trimmed BUY alike, so the
# filled quantity is non-decreasing in the order size and never breaches it.
def test_cash_buffer_binds_full_and_trimmed_buys_monotonically() -> None:
    buffer = 0.02
    bought: dict[float, float] = {}
    for target in (0.97, 0.99, 1.0):
        engine = _engine({0: {"QLD": target}})
        engine.minimum_cash = buffer
        result = engine.run(_flat_days(3), liquidate_at_end=False)
        buys = [fill for fill in result.fills if fill.side == OrderSide.BUY]
        bought[target] = sum(fill.qty for fill in buys)
        spent = sum(fill.qty * fill.fill_price for fill in buys)
        assert 100_000.0 - spent >= buffer * 100_000.0 - 1e-6
    assert bought[0.97] == pytest.approx(970.0)
    # 0.99 used to fill 990 shares and leave 1,000 of cash; the buffer now trims it.
    assert bought[0.99] == pytest.approx(980.0)
    assert bought[0.97] <= bought[0.99] <= bought[1.0]
    assert bought[1.0] == pytest.approx(bought[0.99])


# T8-4: with initial holdings, an action dated before the first bar would be
# credited to holders who already had it; refuse it. An action on the first bar is held.
def test_pre_start_actions_are_refused_when_initial_positions_are_set() -> None:
    bars = _flat_days(2)
    early = CorporateAction(START - timedelta(days=3), "QLD", CorporateActionType.DIVIDEND, 0.5)
    with pytest.raises(ValueError, match="before the first bar"):
        _engine({}, initial_positions={"QLD": (10.0, 50.0)}).run(
            bars, corporate_actions=[early], liquidate_at_end=False
        )
    on_start = CorporateAction(START, "QLD", CorporateActionType.DIVIDEND, 0.5)
    result = _engine({}, initial_positions={"QLD": (10.0, 50.0)}).run(
        bars, corporate_actions=[on_start], liquidate_at_end=False
    )
    assert result.dividend_total == pytest.approx(5.0)


# T8-6: the engine rescales quantities but not the price history, so SPLIT is refused.
def test_engine_refuses_split_events() -> None:
    split = CorporateAction(START + timedelta(days=1), "QLD", CorporateActionType.SPLIT, 2.0)
    with pytest.raises(ValueError, match="refuses SPLIT"):
        _engine({0: {"QLD": 0.5}}).run(_flat_days(3), corporate_actions=[split], liquidate_at_end=False)


# T8-7: a decision on the final bar has no later bar to fill it. It stays
# visible as a decision but is terminal: no unfilled or rejected notional and no rebalance count.
def test_final_bar_decision_is_terminal_and_not_counted_as_a_rebalance() -> None:
    engine = _engine({0: {"QLD": 0.5}, 3: {"QLD": 0.8}})
    result = engine.run(_flat_days(4), liquidate_at_end=False)
    last = result.rebalances[-1]
    assert last.decision_timestamp == START + timedelta(days=3)
    assert last.terminal
    assert last.unfilled_notional == 0.0
    assert last.rejected_notional == 0.0
    assert all(not record.terminal for record in result.rebalances[:-1])
    assert result.rebalance_count == len(result.rebalances) - 1
    assert all(fill.decision_timestamp != last.decision_timestamp for fill in result.fills)


# T8-7 (control): with the research same-bar pass the final-bar decision does fill, so it is not terminal.
def test_final_bar_decision_fills_in_the_same_bar_research_pass() -> None:
    engine = _engine({0: {"QLD": 0.5}, 3: {"QLD": 0.8}}, allow_same_bar_close=True, fill_basis="close")
    result = engine.run(_flat_days(4), liquidate_at_end=False)
    assert not result.rebalances[-1].terminal
    assert result.rebalance_count == len(result.rebalances)


# T8-5, T8-8, T8-10: the report states the dividend basis and attribution, the
# turnover basis, and the Sharpe risk-free assumption.
def test_report_labels_turnover_sharpe_and_dividend_treatment() -> None:
    result = _engine({0: {"QLD": 0.5}}).run(_flat_days(3), liquidate_at_end=False)
    summary = build_portfolio_report(result, strategy_name="w16", strategy_version="1.0.0").summary
    assert summary["turnover_basis"].startswith("two-sided")
    assert "risk-free rate 0" in summary["sharpe_basis"]
    treatment = summary["dividend_treatment"]
    assert treatment["total"] == pytest.approx(result.dividend_total)
    assert "gross" in treatment["basis"]
    assert "no withholding" in treatment["basis"]
    assert "excluded from realized PnL" in treatment["attribution"]


def _backtest_curve(days: list[date]) -> SimpleNamespace:
    return SimpleNamespace(
        curve_timestamps=[datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC) for day in days],
        equity_curve=[100_000.0 + 100.0 * index for index in range(len(days))],
    )


# T8-9: a spacing longer than a regular weekend spans missing sessions; flag it.
def test_paper_overlay_flags_calendar_gaps_in_the_tracking_error() -> None:
    week = [date(2026, 1, 5) + timedelta(days=offset) for offset in range(5)]
    gapped = [*week, date(2026, 1, 19)]
    paper = [(day, 100_000.0 + 90.0 * index) for index, day in enumerate(gapped)]
    flagged = paper_overlay(_backtest_curve(gapped), paper)["summary"]
    assert flagged["tracking_error_date_gap_count"] == 1
    assert flagged["max_date_gap_days"] == 10
    assert flagged["tracking_error_annualised"] is not None

    regular = paper_overlay(_backtest_curve(week), [(day, 100_000.0 + 90.0 * index) for index, day in enumerate(week)])[
        "summary"
    ]
    assert regular["tracking_error_date_gap_count"] == 0
    assert regular["max_date_gap_days"] == 1


# D9-5: direct engine construction charges the shipped backtest fees.
def test_engine_fee_defaults_come_from_the_backtest_config() -> None:
    parameters = inspect.signature(PortfolioBacktestEngine).parameters
    config = BacktestConfig()
    assert parameters["commission"].default == config.commission
    assert parameters["equity_taf_per_share"].default == config.equity_taf_per_share
    assert parameters["equity_taf_cap"].default == config.equity_taf_cap
    assert parameters["equity_cat_per_share"].default == config.equity_cat_per_share
    assert parameters["equity_sec_fee_rate"].default == config.equity_sec_fee_rate
    assert parameters["equity_use_effective_dated_fees"].default == config.equity_use_effective_dated_fees


# Perf 9: the R2 caches are keyed on window content and return exactly what a
# recomputation returns (bit for bit), including for a sliding history.
def test_r2_volatility_cache_is_bit_identical_and_hits_on_a_sliding_window() -> None:
    trend._window_volatility.cache_clear()
    closes = [100.0 + 3.0 * math.sin(index / 7.0) + 0.05 * index for index in range(420)]
    strategy = trend.EquityLeveragedTrendV1()
    for index in range(20, len(closes)):
        window = tuple(closes[index - 20 : index + 1])
        assert trend._window_volatility(window) == trend._window_volatility.__wrapped__(window)
        assert strategy.window_volatility(closes, index) == trend._window_volatility.__wrapped__(window)

    trend._window_volatility.cache_clear()
    strategy.window_volatility(closes, 100)
    # The same 21 closes one index later in the sliding history: a content hit.
    assert strategy.window_volatility(closes[1:], 99) == strategy.window_volatility(closes, 100)
    assert trend._window_volatility.cache_info().hits >= 1


def test_r2_median_cache_is_bit_identical() -> None:
    trend._median_of_volatilities.cache_clear()
    volatilities = tuple(0.1 + (index % 17) / 10.0 for index in range(252))
    cached = trend._median_of_volatilities(volatilities)
    assert cached == trend._median_of_volatilities.__wrapped__(volatilities)
    assert trend._median_of_volatilities(volatilities) == cached
    assert trend._median_of_volatilities.cache_info().hits >= 1


# S9-4: a run whose artefacts fail to write leaves no run directory behind.
def _product_config(tmp_path) -> Config:  # type: ignore[no-untyped-def]
    base = Config()
    parameters = {
        **{
            key: value
            for key, value in base.strategy.equity.parameters.items()
            if key not in trend.MAJORITY_ONLY_PARAMETERS
        },
        "sma_bars": 5,
        "vol_target": 0.0,
        "trend_rule": "sma",
    }
    return replace(
        base,
        runtime=replace(base.runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")),
        backtest=replace(base.backtest, output_dir=str(tmp_path / "reports")),
        strategy=StrategyConfig(equity=replace(base.strategy.equity, parameters=parameters)),
    )


def _weekday_bars(count: int) -> list[Bar]:
    days: list[date] = []
    cursor = date(2023, 1, 2)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    bars: list[Bar] = []
    for index, day in enumerate(days):
        for offset, symbol in enumerate(("SPY", "QQQ", "QLD")):
            wobble = 1 + 0.01 * math.sin(index * 0.9 + offset)
            close = 100 * (1 + 0.001 * (offset + 1)) ** index * wobble
            stamp = datetime(day.year, day.month, day.day, 5, tzinfo=UTC)
            bars.append(Bar(stamp, symbol, "1d", close, close, close, close, 2_000_000.0, Market.EQUITY))
    return bars


def test_failed_product_run_leaves_no_run_directory(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = _product_config(tmp_path)
    bars = _weekday_bars(30)
    runs = tmp_path / "runs"

    def fail_csv(self, output_dir):  # type: ignore[no-untyped-def]
        raise OSError("simulated disk failure")

    monkeypatch.setattr(PortfolioReport, "write_csvs", fail_csv)
    with pytest.raises(OSError, match="simulated disk failure"):
        run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=True, output_dir=str(runs))
    assert list(runs.iterdir()) == []


def test_successful_product_run_persists_a_complete_run_directory(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _product_config(tmp_path)
    runs = tmp_path / "runs"
    run = run_product_backtest(
        config, _weekday_bars(30), market=Market.EQUITY, persist_artifacts=True, output_dir=str(runs)
    )
    assert (Path(run.output_dir) / "report.json").is_file()
    assert (Path(run.output_dir) / "universe.json").is_file()
