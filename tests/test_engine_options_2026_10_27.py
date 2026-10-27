"""Execution-model options on the portfolio engine, on small synthetic bars.

Each test pins one invariant: defaults reproduce the baseline exactly, interest
accrues by hand, costs/lag/basis/same-bar/impact act as documented, and the
product report helpers return their keys. No network, files or credentials.
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from parayabuck.backtest import product as product_module
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)  # 09:30 New York; all days stay in January
DATES = [date(2026, 1, 5 + offset) for offset in range(6)]


def _bar(day: int, symbol: str, open_price: float, close_price: float, volume: float = 1_000_000.0) -> Bar:
    high = max(open_price, close_price) * 1.001
    low = min(open_price, close_price) * 0.999
    return Bar(
        START + timedelta(days=day),
        symbol,
        "1d",
        open_price,
        high,
        low,
        close_price,
        volume,
        Market.EQUITY,
    )


def _bars(days: int) -> list[Bar]:
    """QLD opens at 100 + day and closes 1 higher; SPY is flat at 400."""
    bars: list[Bar] = []
    for day in range(days):
        bars.append(_bar(day, "QLD", 100.0 + day, 101.0 + day))
        bars.append(_bar(day, "SPY", 400.0, 400.0))
    return bars


def _warmup(count: int) -> list[Bar]:
    """QLD bars before START with alternating closes 101 / 99 (non-zero vol)."""
    closes = [100.0 + (1.0 if index % 2 == 0 else -1.0) for index in range(count)]
    return [_bar(index - count, "QLD", closes[index], closes[index]) for index in range(count)]


class _TargetStrategy(PortfolioStrategy):
    """Returns the same target weights at every decision."""

    name = "target_test"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, weights: dict[str, float]) -> None:
        self._weights = dict(weights)
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=tuple(TargetWeight(symbol, weight) for symbol, weight in self._weights.items()),
            cash_weight=1.0 - sum(self._weights.values()),
            universe_version="test",
        )


def _engine(weights: dict[str, float], **options: object) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        _TargetStrategy(weights),
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=2.0,
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        turnover_band=0.005,
        minimum_order_notional=10.0,
        **options,
    )


def _run(weights: dict[str, float], bars: list[Bar], warmup: list[Bar] | None = None, **options: object):  # type: ignore[no-untyped-def]
    return _engine(weights, **options).run(bars, warmup=warmup, liquidate_at_end=False)


def _first_fill(result, symbol: str = "QLD"):  # type: ignore[no-untyped-def]
    return next(fill for fill in result.fills if fill.symbol == symbol)


def test_default_and_zero_options_reproduce_the_baseline_exactly() -> None:
    bars = _bars(5)
    baseline = _run({"QLD": 0.5}, bars)
    explicit = _run(
        {"QLD": 0.5},
        bars,
        cash_rate_series=None,
        cash_rate_spread=0.0,
        cost_bps_per_side=None,
        fill_lag_sessions=0,
        fill_basis="open",
        allow_same_bar_close=False,
        impact_coefficient=0.0,
    )
    zero_rate = _run({"QLD": 0.5}, bars, cash_rate_series={day: 0.0 for day in DATES})
    assert explicit.equity_curve == baseline.equity_curve
    assert explicit.fills == baseline.fills
    assert zero_rate.equity_curve == baseline.equity_curve
    assert zero_rate.fills == baseline.fills
    assert baseline.cash_interest == 0.0
    assert zero_rate.cash_interest == 0.0


def test_idle_cash_interest_compounds_daily_on_the_hand_case() -> None:
    bars = _bars(3)
    series = {day: 0.0252 for day in DATES}  # 252 sessions a year: 0.0001 per session
    run = _run({}, bars, cash_rate_series=series)
    factor = 1 + 0.0252 / 252
    assert run.equity_curve == pytest.approx([100_000 * factor, 100_000 * factor**2, 100_000 * factor**3])
    assert run.cash_interest == pytest.approx(100_000 * (factor**3 - 1))


def test_cash_rate_spread_is_subtracted_and_floored_at_zero() -> None:
    bars = _bars(3)
    series = {day: 0.0252 for day in DATES}
    spread_run = _run({}, bars, cash_rate_series=series, cash_rate_spread=0.0152)  # effective 1 percent
    assert spread_run.cash_interest == pytest.approx(100_000 * ((1 + 0.01 / 252) ** 3 - 1))
    floored = _run({}, bars, cash_rate_series=series, cash_rate_spread=0.05)
    assert floored.cash_interest == 0.0
    assert floored.equity_curve == pytest.approx([100_000.0] * 3)


def test_cost_override_replaces_slippage_and_half_spread_on_the_fill() -> None:
    bars = _bars(3)
    default = _first_fill(_run({"QLD": 0.5}, bars))
    override = _first_fill(_run({"QLD": 0.5}, bars, cost_bps_per_side=10.0))
    reference = 101.0  # day-1 open
    assert default.reference_price == pytest.approx(reference)
    assert default.fill_price == pytest.approx(reference * (1 + 3 / 10_000))  # 2 + 2/2 bps
    assert override.fill_price == pytest.approx(reference * (1 + 10 / 10_000))
    assert override.qty == pytest.approx(default.qty)
    assert override.slippage_cost == pytest.approx(override.qty * reference * 10 / 10_000)
    assert override.spread_cost == 0.0


def test_fill_lag_one_session_fills_the_second_bar_after_the_decision() -> None:
    bars = _bars(4)
    lagged = _first_fill(_run({"QLD": 0.5}, bars, fill_lag_sessions=1))
    assert lagged.timestamp == START + timedelta(days=2)
    assert lagged.reference_price == pytest.approx(102.0)  # day-2 open
    assert lagged.decision_timestamp == START
    next_open = _first_fill(_run({"QLD": 0.5}, bars))
    assert next_open.timestamp == START + timedelta(days=1)


def test_close_basis_fills_at_the_bar_close() -> None:
    bars = _bars(3)
    close_fill = _first_fill(_run({"QLD": 0.5}, bars, fill_basis="close"))
    assert close_fill.timestamp == START + timedelta(days=1)
    assert close_fill.reference_price == pytest.approx(102.0)  # day-1 close
    assert close_fill.fill_price == pytest.approx(102.0 * (1 + 3 / 10_000))


def test_same_bar_close_fills_only_with_the_explicit_flag() -> None:
    bars = _bars(3)
    default = _run({"QLD": 0.5}, bars)
    assert default.fills
    assert all(fill.timestamp > fill.decision_timestamp for fill in default.fills)

    flagged = _run({"QLD": 0.5}, bars, fill_basis="close", allow_same_bar_close=True)
    same_bar = _first_fill(flagged)
    assert same_bar.timestamp == START  # the decision bar itself
    assert same_bar.reference_price == pytest.approx(101.0)  # its close

    with pytest.raises(ValueError):
        _engine({"QLD": 0.5}, fill_basis="open", allow_same_bar_close=True)
    with pytest.raises(ValueError):
        _engine({"QLD": 0.5}, fill_lag_sessions=1, fill_basis="close", allow_same_bar_close=True)


def test_square_root_impact_grows_with_order_size() -> None:
    warmup = _warmup(25)
    bars = _bars(3)
    # The 21 closes known at the decision (last 20 warmup closes plus the
    # decision-bar close 101) give 20 returns; the impact uses their stdev.
    closes = [bar.close for bar in warmup][-20:] + [101.0]
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
    daily_vol = statistics.stdev(returns)
    impacts: list[float] = []
    for weight in (0.05, 0.40):
        plain = _first_fill(_run({"QLD": weight}, bars, warmup=warmup))
        impacted = _first_fill(_run({"QLD": weight}, bars, warmup=warmup, impact_coefficient=1.0))
        expected = daily_vol * math.sqrt(plain.qty / 1_000_000.0)  # ADV is 1e6 on every bar
        assert impacted.fill_price - plain.fill_price == pytest.approx(101.0 * expected)
        assert impacted.slippage_cost > plain.slippage_cost
        impacts.append(impacted.fill_price - plain.fill_price)
    assert impacts[1] > impacts[0] > 0


def test_impact_needs_twenty_known_returns_and_otherwise_blocks_the_fill() -> None:
    bars = _bars(3)
    result = _run({"QLD": 0.5}, bars, impact_coefficient=1.0)  # no warmup: 0 returns known
    assert not [fill for fill in result.fills if fill.symbol == "QLD"]
    assert result.fee_total == 0.0


def test_report_helpers_return_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _bars(3)
    engine_result = _run({"QLD": 0.5}, bars)
    calls: list[dict[str, object]] = []

    def fake_run(config, bars_arg, *, market, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return SimpleNamespace(result=engine_result)

    monkeypatch.setattr(product_module, "run_product_backtest", fake_run)
    rows = product_module.cost_sensitivity(None, bars, market=Market.EQUITY, grid=(0.0, 10.0), multipliers=(2.0,))
    assert [row["setting"] for row in rows] == [
        "baseline",
        "0 bps per side",
        "10 bps per side",
        "2x configured costs",
    ]
    for row in rows:
        assert {"cagr", "max_drawdown", "sells_per_year", "turnover"} <= set(row)
    assert calls[1]["cost_bps_per_side"] == 0.0
    assert calls[3]["cost_multiplier"] == 2.0
    with pytest.raises(ValueError):
        product_module.cost_sensitivity(None, bars, market=Market.EQUITY, cost_multiplier=2.0)

    timing = product_module.fill_timing_report(None, bars, market=Market.EQUITY)
    assert list(timing) == ["same_close", "next_open", "next_close", "two_session_delay"]
    for row in timing.values():
        assert {
            "fill_lag_sessions",
            "fill_basis",
            "allow_same_bar_close",
            "cagr",
            "max_drawdown",
            "turnover",
            "sells_per_year",
        } <= set(row)
    assert timing["two_session_delay"]["fill_lag_sessions"] == 1
    assert timing["same_close"]["allow_same_bar_close"] is True
    assert timing["next_close"]["fill_basis"] == "close"
