"""Product-backtest hardening: fail-closed inputs, exposure/benchmark metrics, split accounting.

Every fixture is a small synthetic bar set; nothing touches data/, the network or
runtime state (``tmp_path`` only).
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from parayabuck.backtest import product as product_module
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.portfolio_report import build_portfolio_report
from parayabuck.backtest.product import load_csv_bars, run_product_backtest
from parayabuck.config import Config, StrategyConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.markets.nyse_calendar import nyse_sessions
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_leveraged_trend import MAJORITY_ONLY_PARAMETERS
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

# 05:00 UTC is New York midnight in both EST and EDT, so the stamp's
# New York date is the session date the fixture means.
_SESSION_STAMP_HOUR = 5


def _day_stamp(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, _SESSION_STAMP_HOUR, tzinfo=UTC)


def _daily_bar(day: date, symbol: str, close: float, *, volume: float = 2_000_000.0) -> Bar:
    return Bar(_day_stamp(day), symbol, "1d", close, close, close, close, volume, Market.EQUITY)


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _weekday_bars(days: list[date], symbols: tuple[str, ...]) -> list[Bar]:
    bars: list[Bar] = []
    for index, day in enumerate(days):
        for offset, symbol in enumerate(symbols):
            wobble = 1 + 0.01 * math.sin(index * 0.9 + offset)
            close = 100 * (1 + 0.001 * (offset + 1)) ** index * wobble
            bars.append(_daily_bar(day, symbol, close))
    return bars


def _config(tmp_path) -> Config:
    base = Config()
    parameters = {
        **{key: value for key, value in base.strategy.equity.parameters.items() if key not in MAJORITY_ONLY_PARAMETERS},
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


class _Recorder:
    """Stands in for the product logger so a warning can be asserted directly."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.messages.append(message % args)


# -- Task 1: fail closed ---------------------------------------------------------


def test_strict_calendar_failure_raises_and_nonstrict_loads_with_fallback(tmp_path, monkeypatch) -> None:
    csv_path = tmp_path / "bars.csv"
    rows = ["timestamp,symbol,timeframe,open,high,low,close,volume,market"]
    rows.extend(f"{day.isoformat()},SPY,1d,100,101,99,100,1000000,equity" for day in _weekdays(date(2024, 1, 2), 5))
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def broken_calendar(start: date, end: date):
        raise RuntimeError("calendar backend missing")

    monkeypatch.setattr(product_module, "nyse_sessions", broken_calendar)

    with pytest.raises(ValueError, match="NYSE calendar unavailable"):
        load_csv_bars(csv_path, default_market=Market.EQUITY)
    # Non-strict keeps the degraded per-date lookup: every weekday session loads.
    assert len(load_csv_bars(csv_path, default_market=Market.EQUITY, strict=False)) == 5


def test_missing_warmup_raises_in_strict_and_warns_in_nonstrict(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    days = _weekdays(date(2023, 1, 2), 30)
    # QLD has one bar, far short of the strategy's five-bar warmup.
    bars = _weekday_bars(days, ("SPY", "QQQ")) + [_daily_bar(days[0], "QLD", 100.0)]

    with pytest.raises(ValueError, match="Insufficient strategy warmup for QLD"):
        run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=False)

    recorder = _Recorder()
    monkeypatch.setattr(product_module, "log", recorder)
    run = run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=False, strict=False)

    assert any("No default backtest warmup: QLD" in message for message in recorder.messages)
    assert run.report.summary["evaluation_start_source"] == "full history"


def test_strict_run_rejects_a_gappy_decision_symbol(tmp_path) -> None:
    config = _config(tmp_path)
    days = _weekdays(date(2023, 1, 2), 60)
    gap = {date(2023, 1, 9), date(2023, 1, 10), date(2023, 1, 11)}  # three consecutive sessions
    bars = [
        bar
        for bar in _weekday_bars(days, ("SPY", "QQQ", "QLD"))
        if not (bar.symbol == "QQQ" and bar.timestamp.date() in gap)
    ]

    with pytest.raises(ValueError, match="QQQ is missing 3 of"):
        run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=False)


def test_missing_session_tolerance_is_one_percent_and_at_least_two() -> None:
    sessions = [day for day, _, _ in nyse_sessions(date(2023, 1, 2), date(2024, 6, 28))]
    count = len(sessions)
    # The boundary this test exercises: 3 missing is under 1%, 4 is over it.
    assert 3 <= 0.01 * count < 4, count

    def qqq_bars_missing(missing_count: int) -> list[Bar]:
        missing = set(sessions[10 : 10 + missing_count])
        return [_daily_bar(day, "QQQ", 100.0) for day in sessions if day not in missing]

    for missing_count in (1, 3):  # one session is below the minimum; three is under 1%
        product_module._require_complete_daily_sessions(
            qqq_bars_missing(missing_count), market=Market.EQUITY, timeframe="1d", symbols=("QQQ",)
        )
    with pytest.raises(ValueError, match="QQQ is missing 4 of"):
        product_module._require_complete_daily_sessions(
            qqq_bars_missing(4), market=Market.EQUITY, timeframe="1d", symbols=("QQQ",)
        )


# -- Task 2: exposure and benchmarks -----------------------------------------------


class _ScheduledWeights(PortfolioStrategy):
    """Fixed target weights per calendar month of the run (January, February, ...)."""

    name = "scheduled_weights"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, weights_by_month: tuple[dict[str, float], ...], declared: tuple[str, ...] | None = None) -> None:
        self._weights_by_month = weights_by_month
        self._declared = declared
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

    @property
    def decision_symbols(self) -> tuple[str, ...] | None:
        return self._declared

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        index = min(context.decision_timestamp.month - 1, len(self._weights_by_month) - 1)
        targets = self._weights_by_month[index]
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=tuple(TargetWeight(symbol, weight) for symbol, weight in targets.items()),
            cash_weight=1.0 - sum(targets.values()),
            universe_version="test",
        )


def _engine(strategy: PortfolioStrategy) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(strategy.market, "monthly"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=4.0,
        commission=0.0,
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        turnover_band=0.005,
        minimum_order_notional=10.0,
    )


def test_time_in_market_and_decision_symbol_benchmarks_on_a_hand_case() -> None:
    days = [date(2025, 1, 6) + timedelta(days=index) for index in range(5)]  # Mon..Fri, one month
    closes = {
        "SPY": (100.0, 101.0, 102.0, 103.0, 104.0),
        "AAA": (100.0, 110.0, 120.0, 90.0, 99.0),
        "BBB": (50.0, 55.0, 60.0, 65.0, 70.0),
    }
    bars = [
        _daily_bar(day, symbol, series[index]) for symbol, series in closes.items() for index, day in enumerate(days)
    ]
    # One monthly decision on day 0 buys AAA; it is filled at day 1's open and
    # held to the end, so the five curve points are 0, 1, 1, 1, 1 positions.
    # CCC is declared but has no bars; SPY is declared but is the benchmark.
    strategy = _ScheduledWeights(({"AAA": 0.5},), declared=("AAA", "BBB", "CCC", "SPY"))
    result = _engine(strategy).run(bars)

    assert len(result.equity_curve) == 5
    assert result.time_in_market == pytest.approx(4 / 5)
    # Close-to-close over the curve window: AAA 100 -> 99, BBB 50 -> 70.
    assert result.benchmarks == pytest.approx({"AAA": 99.0 / 100.0 - 1, "BBB": 70.0 / 50.0 - 1})
    assert "SPY" not in result.benchmarks
    assert "CCC" not in result.benchmarks
    assert result.benchmark_return == pytest.approx(104.0 / 100.0 - 1)  # existing SPY field unchanged

    report = build_portfolio_report(result, strategy_name="scheduled_weights", strategy_version="1.0.0")
    assert report.summary["time_in_market"] == pytest.approx(4 / 5)
    assert report.summary["benchmarks"] == pytest.approx(result.benchmarks)


def test_time_in_market_is_zero_when_nothing_is_bought() -> None:
    days = [date(2025, 1, 6) + timedelta(days=index) for index in range(3)]
    bars = [_daily_bar(day, "SPY", 100.0 + index) for index, day in enumerate(days)]
    result = _engine(_ScheduledWeights(({},))).run(bars)

    assert result.time_in_market == 0.0
    assert result.benchmarks == {}


# -- Task 3: splits -------------------------------------------------------------------


def test_raw_bars_with_a_two_for_one_split_match_pre_adjusted_bars() -> None:
    days = _weekdays(date(2025, 1, 27), 10)  # Jan 27 .. Feb 7; split on index 2, rebalance Feb 3
    split_index = 2
    adjusted_aaa = (100.0, 103.0, 101.0, 106.0, 104.0, 109.0, 111.0, 108.0, 115.0, 117.0)
    # Raw prints before the ex-date are twice the split-adjusted series.
    raw_aaa = tuple(price * (2.0 if index < split_index else 1.0) for index, price in enumerate(adjusted_aaa))
    spy = tuple(100.0 for _ in days)

    def bars_for(aaa: tuple[float, ...]) -> list[Bar]:
        return [
            _daily_bar(day, symbol, series[index], volume=10_000_000.0)
            for symbol, series in (("SPY", spy), ("AAA", aaa))
            for index, day in enumerate(days)
        ]

    def strategy() -> _ScheduledWeights:
        return _ScheduledWeights(({"AAA": 0.5}, {"AAA": 0.25}))

    split = CorporateAction(_day_stamp(days[split_index]), "AAA", CorporateActionType.SPLIT, 2.0)
    raw = _engine(strategy()).run(bars_for(raw_aaa), corporate_actions=[split])
    adjusted = _engine(strategy()).run(bars_for(adjusted_aaa))

    assert raw.equity_curve == pytest.approx(adjusted.equity_curve, rel=1e-9)
    assert raw.ending_equity == pytest.approx(adjusted.ending_equity, rel=1e-9)
    assert raw.realized_pnls == pytest.approx(adjusted.realized_pnls, rel=1e-9)
    assert raw.fee_total == pytest.approx(adjusted.fee_total)
    # One buy (January, pre-split) and one sell (February, post-split). The raw
    # buy carries half the adjusted share count; the post-split sell matches.
    assert len(raw.fills) == len(adjusted.fills) == 2
    assert raw.fills[0].qty == pytest.approx(adjusted.fills[0].qty / 2)
    assert raw.fills[-1].qty == pytest.approx(adjusted.fills[-1].qty)


def test_product_run_rejects_split_events_on_adjusted_bars(tmp_path) -> None:
    config = _config(tmp_path)
    days = _weekdays(date(2023, 1, 2), 30)
    bars = _weekday_bars(days, ("SPY", "QQQ", "QLD"))
    split = CorporateAction(_day_stamp(days[10]), "QQQ", CorporateActionType.SPLIT, 2.0)

    with pytest.raises(ValueError, match="split-adjusted bars"):
        run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=False, corporate_actions=[split])

    # Dividend-only actions on the same adjusted bars remain accepted.
    dividend = CorporateAction(_day_stamp(days[10]), "QLD", CorporateActionType.DIVIDEND, 0.5)
    run = run_product_backtest(
        config, bars, market=Market.EQUITY, persist_artifacts=False, corporate_actions=[dividend]
    )
    assert run.report.summary["price_adjustment_model"].startswith("split-adjusted execution/signal prices")
