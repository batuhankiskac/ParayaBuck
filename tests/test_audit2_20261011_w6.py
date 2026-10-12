"""W6 regression tests for the second 2026-10-11 audit (backtest workstream).

Covers: a holiday CSV bar never drives a backtest decision (Q2E1/Q2E2), the
per-decision-symbol warmup gate (Q3E-1), the decision-symbol evaluation start
(Q3E-2), engine defaults/validation (Q3E-3, Q3E-7, Q4E-5) and the product
export surface (Q4E-9).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest import product
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import default_evaluation_start, load_csv_bars
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.portfolio.models import RebalancePolicy

_SYMBOLS = ("QQQ", "QLD", "SPY")


def _strategy() -> EquityLeveragedTrendV1:
    return EquityLeveragedTrendV1(
        {
            "signal_symbol": "QQQ",
            "hold_symbol": "QLD",
            "sma_bars": 5,
            "vol_target": 0.0,
            "rebalance_frequency": "hours:24",
        }
    )


def _engine(start: datetime, **overrides: object) -> PortfolioBacktestEngine:
    universe = PointInTimeUniverse(
        Market.EQUITY,
        (
            UniverseSnapshot(
                effective_at=start,
                symbols=frozenset(_SYMBOLS),
                observation_set=_SYMBOLS,
                maximum_stream_symbols=3,
                pinned_symbols=_SYMBOLS,
            ),
        ),
        "w6 audit-2 fixture",
    )
    return PortfolioBacktestEngine(
        _strategy(),
        RebalancePolicy(Market.EQUITY, "hours:24"),
        universe=universe,
        universe_version="w6-audit2",
        allow_static_universe=True,
        **overrides,  # type: ignore[arg-type]
    )


def _sessions(start: datetime, count: int) -> list[datetime]:
    out: list[datetime] = []
    timestamp = start
    while len(out) < count:
        if timestamp.weekday() < 5:
            out.append(timestamp)
        timestamp += timedelta(days=1)
    return out


def _bar(timestamp: datetime, symbol: str, close: float) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close * 1.001, close * 0.999, close, 2_000_000, Market.EQUITY)


def test_holiday_csv_bar_never_drives_an_engine_decision(tmp_path) -> None:
    # Mon 2023-11-13 .. Fri 2023-11-24 with Thanksgiving (Thu 2023-11-23).
    days = [datetime(2023, 11, 13, 5, tzinfo=UTC) + timedelta(days=offset) for offset in range(12)]
    weekdays = [day for day in days if day.weekday() < 5]
    holiday = datetime(2023, 11, 23, 5, tzinfo=UTC)
    assert holiday in weekdays
    lines = ["timestamp,symbol,timeframe,open,high,low,close,volume,market"]
    for index, day in enumerate(weekdays):
        for symbol in _SYMBOLS:
            close = 90.0 if day == holiday else 100.0 + index
            lines.append(f"{day.isoformat()},{symbol},1d,{close},{close},{close},{close},2000000,equity")
    path = tmp_path / "thanksgiving.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    bars = load_csv_bars(path, default_market=Market.EQUITY)
    assert holiday not in {bar.timestamp for bar in bars}
    result = _engine(weekdays[0]).run(bars, liquidate_at_end=False)
    decisions = [record.decision_timestamp for record in result.rebalances]
    assert decisions
    assert holiday not in decisions
    assert holiday not in {fill.timestamp for fill in result.fills}


def test_backtest_waits_for_every_decision_symbol_warmup() -> None:
    """SPY (never read) has full history; QQQ/QLD start late. Live keeps
    QQQ/QLD in ``warming_up`` until each has ``sma_bars`` bars, so the
    backtest must not decide before that either."""
    sessions = _sessions(datetime(2025, 1, 6, 5, tzinfo=UTC), 14)
    late = 6
    bars = [_bar(timestamp, "SPY", 100.0 + index) for index, timestamp in enumerate(sessions)]
    for index, timestamp in enumerate(sessions[late:]):
        bars.append(_bar(timestamp, "QQQ", 100.0 + index))
        bars.append(_bar(timestamp, "QLD", 50.0 + index))
    strategy = _strategy()
    result = _engine(sessions[0]).run(bars, liquidate_at_end=False)
    decisions = [record.decision_timestamp for record in result.rebalances]
    first_decidable = sessions[late + strategy.minimum_history_bars - 1]
    assert decisions
    assert min(decisions) == first_decidable


def test_default_evaluation_start_ignores_a_short_unread_benchmark() -> None:
    sessions = _sessions(datetime(2025, 1, 6, 5, tzinfo=UTC), 12)
    bars = [_bar(timestamp, symbol, 100.0) for timestamp in sessions for symbol in ("QQQ", "QLD")]
    bars += [_bar(timestamp, "SPY", 100.0) for timestamp in sessions[-2:]]
    config = Config()
    assert default_evaluation_start(bars, config, 5) is None
    assert default_evaluation_start(bars, config, 5, _strategy().decision_symbols) == sessions[4]


def test_engine_spread_default_matches_shipped_backtest_config() -> None:
    engine = _engine(datetime(2025, 1, 6, 5, tzinfo=UTC))
    assert engine.spread_bps == Config().backtest.spread_bps == 2.0


@pytest.mark.parametrize("equity", [math.nan, math.inf, -math.inf, 0.0])
def test_engine_rejects_non_finite_or_non_positive_starting_equity(equity: float) -> None:
    with pytest.raises(ValueError, match="Starting equity"):
        _engine(datetime(2025, 1, 6, 5, tzinfo=UTC), starting_equity=equity)


def test_bare_order_policy_params_validated_only_without_execution_policy() -> None:
    start = datetime(2025, 1, 6, 5, tzinfo=UTC)
    with pytest.raises(ValueError, match="Turnover band"):
        _engine(start, turnover_band=1.5)
    with pytest.raises(ValueError, match="Minimum order notional"):
        _engine(start, minimum_order_notional=math.nan)
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.01, minimum_order_notional=25.0)
    engine = _engine(start, turnover_band=1.5, execution_policy=policy)
    assert (engine.turnover_band, engine.minimum_order_notional) == (0.01, 25.0)


def test_product_exports_the_corporate_action_loader() -> None:
    assert "load_csv_corporate_actions" in product.__all__
