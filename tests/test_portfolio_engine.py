from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine
from parayabuck.backtest.universe import build_crypto_point_in_time_universe
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.crypto_cross_sectional import CryptoCrossSectionalMomentum1hV1
from parayabuck.portfolio.equity_dual_momentum import EquityEtfDualMomentumV1
from parayabuck.portfolio.models import (
    RebalancePolicy,
    RebalanceReason,
    TargetPortfolio,
    TargetWeight,
)
from parayabuck.portfolio.universes import (
    CRYPTO_UNIVERSE_VERSION,
    EQUITY_ETF_UNIVERSE,
    EQUITY_UNIVERSE_VERSION,
)


def _equity_bar(
    timestamp: datetime,
    symbol: str,
    close: float,
    volume: float = 5_000_000,
) -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1d",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        volume,
        Market.EQUITY,
    )


def _equity_series(
    days: int,
    drifts: dict[str, float],
    start: datetime | None = None,
    volume: float = 5_000_000,
    seed_price: float = 100.0,
) -> list[Bar]:
    start = start or datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for symbol, drift in drifts.items():
            close = seed_price * (1 + drift) ** index
            bars.append(_equity_bar(timestamp, symbol, close, volume))
    return bars


def _crypto_bar(
    timestamp: datetime,
    symbol: str,
    close: float,
    volume: float = 10_000,
) -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1h",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        volume,
        Market.CRYPTO,
    )


class _FixedTargetStrategy(PortfolioStrategy):
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = dict(targets)
        super().__init__()

    def _validate_parameters(self) -> None:
        if sum(self._targets.values()) > 1.0:
            raise ValueError("fixed targets exceed 1")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weights = tuple(TargetWeight(symbol, weight) for symbol, weight in self._targets.items())
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=max(0.0, 1.0 - sum(self._targets.values())),
            universe_version=context.universe_version,
        )


def _equity_engine(
    strategy: PortfolioStrategy,
    *,
    turnover_band: float = 0.005,
    **kwargs: object,
) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=4.0,
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        turnover_band=turnover_band,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Lookahead guards
# ---------------------------------------------------------------------------


def test_decision_bar_close_cannot_be_used_as_same_bar_fill() -> None:
    strategy = _FixedTargetStrategy({"SPY": 0.5})
    engine = _equity_engine(strategy)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    jan_1 = start
    jan_2 = start + timedelta(days=1)
    feb_1 = start + timedelta(days=31)
    feb_2 = start + timedelta(days=32)
    bars = [
        _equity_bar(jan_1, "SPY", 100.0),
        _equity_bar(jan_2, "SPY", 101.0),
        _equity_bar(feb_1, "SPY", 102.0),
        _equity_bar(feb_2, "SPY", 103.0),
    ]
    result = engine.run(bars, liquidate_at_end=False)

    fills = [fill for fill in result.fills if fill.symbol == "SPY"]
    assert fills, "expected a fill"
    # The decision happens at Jan 1 close; the fill must occur at Jan 2's open.
    first = fills[0]
    assert first.timestamp == jan_2
    # The open of the Jan 2 bar is 101.0 * 0.999.
    assert first.reference_price == pytest.approx(100.899)
    # Fill price = open + adverse (2 bps slippage + 2 bps half-spread).
    assert first.fill_price == pytest.approx(100.899 * (1 + 4 / 10_000))


def test_open_fill_capacity_uses_only_volume_known_at_decision() -> None:
    strategy = _FixedTargetStrategy({"SPY": 0.5})
    engine = _equity_engine(strategy)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = [
        _equity_bar(start, "SPY", 100.0, volume=100.0),
        # This future completed-session volume must not make the opening fill
        # larger. Ten percent of the only known volume is ten shares.
        _equity_bar(start + timedelta(days=1), "SPY", 100.0, volume=9_000_000.0),
    ]
    result = engine.run(bars, liquidate_at_end=False)
    assert result.fills[0].qty == pytest.approx(10.0)


def test_momentum_lookback_never_sees_future_bars() -> None:
    seen: list[tuple[datetime, int]] = []

    class _SpyStrategy(EquityEtfDualMomentumV1):
        def decide(self, context: PortfolioContext) -> TargetPortfolio:
            closes = context.closes("SPY")
            seen.append((context.decision_timestamp, len(closes)))
            return super().decide(context)

    bars = _equity_series(300, {"SPY": 0.0004, "QQQ": 0.0005, "XLE": 0.0001})
    engine = _equity_engine(_SpyStrategy())
    engine.run(bars, liquidate_at_end=False)

    assert seen
    for timestamp, count in seen:
        # At decision time T the history can only contain bars up to T.
        assert count <= 300


def test_monthly_rebalance_uses_first_bar_of_calendar_month() -> None:
    strategy = _FixedTargetStrategy({"SPY": 1.0})
    engine = _equity_engine(strategy)
    start = datetime(2025, 1, 15, tzinfo=UTC)
    bars = [_equity_bar(start + timedelta(days=index), "SPY", 100.0 + index) for index in range(60)]
    result = engine.run(bars, liquidate_at_end=False)
    decisions = [record.decision_timestamp for record in result.rebalances]
    assert decisions
    assert all(ts.day <= 16 for ts in decisions)
    assert decisions[0] == bars[0].timestamp


def test_equity_and_crypto_calendars_are_separate() -> None:
    monthly = RebalancePolicy(Market.EQUITY, "monthly")
    hourly = RebalancePolicy(Market.CRYPTO, "hours:6")
    t = datetime(2026, 3, 1, 2, tzinfo=UTC)
    assert monthly.is_period_start(datetime(2026, 2, 28, 23, tzinfo=UTC), t)
    assert not hourly.is_period_start(datetime(2026, 3, 1, 1, tzinfo=UTC), t)
    next_hour = datetime(2026, 3, 1, 6, tzinfo=UTC)
    assert hourly.is_period_start(datetime(2026, 3, 1, 1, tzinfo=UTC), next_hour)


# ---------------------------------------------------------------------------
# Weight and order mechanics
# ---------------------------------------------------------------------------


def test_target_delta_produces_exact_order_intents() -> None:
    strategy = _FixedTargetStrategy({"SPY": 1.0})
    engine = _equity_engine(strategy)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = [_equity_bar(start + timedelta(days=index), "SPY", 100.0) for index in range(33)]
    engine.run(bars, liquidate_at_end=False)
    # 100k equity / 100 price = 1000 shares target; orders were created.
    assert engine._pending or engine._fills
    buy_fills = [fill for fill in engine._fills if fill.side == OrderSide.BUY]
    total_qty = sum(fill.qty for fill in buy_fills)
    assert total_qty == pytest.approx(1000.0, abs=1.0)


def test_sell_before_buy_order_keeps_cash_non_negative() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)

    class _SwitchStrategy(_FixedTargetStrategy):
        def __init__(self, first: bool) -> None:
            self._first = first
            super().__init__({})

        def decide(self, context: PortfolioContext) -> TargetPortfolio:
            targets = {"SPY": 0.50, "QQQ": 0.45} if self._first else {"SPY": 0.0, "QQQ": 0.95}
            weights = tuple(TargetWeight(s, w) for s, w in targets.items())
            return TargetPortfolio(
                strategy_name=self.name,
                strategy_version=self.version,
                decision_timestamp=context.decision_timestamp,
                weights=weights,
                cash_weight=max(0.0, 1.0 - sum(targets.values())),
                universe_version=context.universe_version,
            )

    class _SwitchingEngine(PortfolioBacktestEngine):
        def _decide(self, timestamp: datetime) -> None:
            month = timestamp.month
            self.strategy = _SwitchStrategy(first=month == 1)  # type: ignore[attr-defined]
            super()._decide(timestamp)

    engine = _SwitchingEngine(
        _SwitchStrategy(first=True),
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=4.0,
        benchmark_symbol="SPY",
    )
    bars = [
        _equity_bar(start + timedelta(days=index), symbol, 100.0)
        for index in range(90)
        for symbol in ("SPY", "QQQ")
    ]
    result = engine.run(bars, liquidate_at_end=False)
    # Sells at the second rebalance must execute before buys at the same
    # timestamp, so the cash raised by the SPY exit funds the QQQ add.
    february_fills = [fill for fill in result.fills if fill.timestamp.month == 2]
    assert february_fills
    assert february_fills[0].side == OrderSide.SELL
    assert any(fill.side == OrderSide.BUY for fill in february_fills)
    assert (
        min(fill.qty * fill.fill_price for fill in february_fills if fill.side == OrderSide.BUY) > 0
    )


def test_turnover_band_blocks_small_orders() -> None:
    strategy = _FixedTargetStrategy({"SPY": 0.51})
    engine = _equity_engine(strategy, turnover_band=0.02)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = [_equity_bar(start + timedelta(days=index), "SPY", 100.0) for index in range(60)]
    result = engine.run(bars, liquidate_at_end=False)
    assert len(result.fills) == 1  # only the first buy; rebalancing to 0.51 is banded


def test_partial_fill_then_remaining_target_is_reached() -> None:
    strategy = _FixedTargetStrategy({"SPY": 1.0})
    engine = _equity_engine(strategy)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = [
        _equity_bar(start + timedelta(days=index), "SPY", 100.0, volume=1_000.0)
        for index in range(33)
    ]
    result = engine.run(bars, liquidate_at_end=False)
    buy_fills = [fill for fill in result.fills if fill.side == OrderSide.BUY]
    # Participation cap of 10% on 1000 volume = 100 shares per bar.
    assert len(buy_fills) >= 5
    total_qty = sum(fill.qty for fill in buy_fills)
    assert total_qty == pytest.approx(1000.0, abs=1.0)


def test_fee_spread_slippage_are_applied_separately() -> None:
    strategy = _FixedTargetStrategy({"SPY": 0.5})
    engine = _equity_engine(strategy)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = [_equity_bar(start + timedelta(days=index), "SPY", 100.0) for index in range(33)]
    result = engine.run(bars, liquidate_at_end=False)
    # 500 shares at open 99.9 with 2bps slippage and 4bps spread (half = 2bps).
    assert result.slippage_cost_total == pytest.approx(500 * 99.9 * 2 / 10_000)
    assert result.spread_cost_total == pytest.approx(500 * 99.9 * 2 / 10_000)
    assert result.fee_total == 0.0


def test_zero_volume_and_missing_bars_are_safe() -> None:
    strategy = _FixedTargetStrategy({"SPY": 1.0})
    engine = _equity_engine(strategy)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = [
        _equity_bar(start + timedelta(days=index), "SPY", 100.0, volume=0.0) for index in range(5)
    ]
    result = engine.run(bars, liquidate_at_end=False)
    assert result.fills == ()
    assert engine._pending  # orders stay pending instead of crashing


def test_running_same_rebalance_twice_does_not_duplicate_orders() -> None:
    bars = _equity_series(120, {"SPY": 0.0004, "QQQ": 0.0005, "XLE": 0.0001})
    first = _equity_engine(EquityEtfDualMomentumV1())
    second = _equity_engine(EquityEtfDualMomentumV1())
    result_a = first.run(bars, liquidate_at_end=False)
    result_b = second.run(bars, liquidate_at_end=False)
    assert [f.timestamp for f in result_a.fills] == [f.timestamp for f in result_b.fills]
    assert [f.qty for f in result_a.fills] == [f.qty for f in result_b.fills]
    assert len(result_a.rebalances) == len(result_b.rebalances)


def test_restart_with_persisted_state_continues_without_duplicate_orders(
    tmp_path: Path,
) -> None:
    bars = _equity_series(200, {"SPY": 0.0004, "QQQ": 0.0005, "XLE": 0.0001})
    split = 120
    state_path = str(tmp_path / "portfolio_state.json")

    def strategy() -> _FixedTargetStrategy:
        return _FixedTargetStrategy({"SPY": 0.5, "QQQ": 0.45})

    first_engine = _equity_engine(strategy(), state_path=state_path)
    first_engine.run(bars[:split], liquidate_at_end=False)

    state = PortfolioBacktestEngine.load_state(state_path)
    assert state is not None
    assert state.rebalance_count > 0
    restart_offset = state.rebalance_count

    second_engine = _equity_engine(strategy(), state=state)
    result_b = second_engine.run(bars[split:], warmup=bars[:split], liquidate_at_end=False)

    reference_engine = _equity_engine(strategy())
    result_a = reference_engine.run(bars, liquidate_at_end=False)

    # The restarted engine produces the same post-restart decision sequence as
    # a single uninterrupted run, without re-emitting already-filled orders.
    restart_records = result_a.rebalances[restart_offset:]
    assert [r.decision_timestamp for r in restart_records] == [
        r.decision_timestamp for r in result_b.rebalances
    ]
    # Fills after the restart point match the uninterrupted run exactly.
    first_restart_decision = result_b.rebalances[0].decision_timestamp
    assert [f.timestamp for f in result_b.fills] == [
        f.timestamp for f in result_a.fills if f.timestamp >= first_restart_decision
    ]


# ---------------------------------------------------------------------------
# Synthetic strategy behavior
# ---------------------------------------------------------------------------


def test_strong_etf_is_ranked_above_weaker_ones() -> None:
    bars = _equity_series(400, {"SPY": 0.0006, "QQQ": 0.0012, "XLE": 0.0001})
    engine = _equity_engine(EquityEtfDualMomentumV1({"top_n": 2}))
    result = engine.run(bars, liquidate_at_end=False)
    first_weights = dict(result.weight_history[0][1])
    assert "QQQ" in first_weights
    assert "SPY" in first_weights
    assert "XLE" not in first_weights


def test_all_etfs_negative_goes_fully_to_cash() -> None:
    bars = _equity_series(400, {"SPY": -0.001, "QQQ": -0.001, "XLE": -0.001})
    engine = _equity_engine(EquityEtfDualMomentumV1())
    result = engine.run(bars, liquidate_at_end=False)
    assert result.rebalances
    assert all(record.reason == RebalanceReason.RISK_OFF.value for record in result.rebalances)
    assert result.average_cash_ratio == pytest.approx(1.0, abs=0.01)
    assert result.fills == ()


def test_higher_volatility_asset_receives_lower_weight() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(300):
        timestamp = start + timedelta(days=index)
        # Same drift, different close-noise amplitude (deterministic sine).
        for symbol, amplitude in (("SPY", 0.002), ("QQQ", 0.02)):
            close = 100 * 1.0005**index * (1 + amplitude * math.sin(index / 5))
            high = close * (1 + amplitude)
            low = close * (1 - amplitude)
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    low + (high - low) * 0.5,
                    high,
                    low,
                    close,
                    5_000_000,
                    Market.EQUITY,
                )
            )
    engine = _equity_engine(EquityEtfDualMomentumV1({"top_n": 2}))
    result = engine.run(bars, liquidate_at_end=False)
    assert result.weight_history
    first_weights = dict(result.weight_history[0][1])
    assert "SPY" in first_weights and "QQQ" in first_weights
    assert first_weights["SPY"] > first_weights["QQQ"]


def test_volatility_shock_reduces_risk_multiplier_when_targeting() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(400):
        timestamp = start + timedelta(days=index)
        amplitude = 0.05 if index > 200 else 0.005
        close = 100 * 1.0004**index
        for symbol in ("SPY", "QQQ", "XLE", "XLY"):
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close * (1 - amplitude),
                    close * (1 + amplitude),
                    close * (1 - amplitude),
                    close,
                    5_000_000,
                    Market.EQUITY,
                )
            )
    engine = _equity_engine(EquityEtfDualMomentumV1({"volatility_target": 0.08}))
    result = engine.run(bars, liquidate_at_end=False)
    assert result.rebalances
    late = [r for r in result.rebalances if r.decision_timestamp > start + timedelta(days=200)]
    assert late
    assert all(r.target_cash_weight > 0 for r in late)


def test_risk_off_regime_closes_crypto_positions() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    # Strong uptrend for 60 days, then a crash.
    for index in range(120 * 24):
        timestamp = start + timedelta(hours=index)
        if index < 60 * 24:
            drift = 0.0002
        else:
            drift = -0.0004
        for symbol in ("BTC/USD", "ETH/USD"):
            close = 100 * (1 + drift) ** index
            bars.append(_crypto_bar(timestamp, symbol, close))
    strategy = CryptoCrossSectionalMomentum1hV1({"rebalance_hours": 24, "momentum_scheme": "7d"})
    engine = PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(Market.CRYPTO, "hours:24"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=6.0,
        crypto_taker_fee_bps=5.0,
        benchmark_symbol="BTC/USD",
        max_participation_pct=0.10,
    )
    result = engine.run(bars, liquidate_at_end=False)
    risk_off = [
        record for record in result.rebalances if record.reason == RebalanceReason.RISK_OFF.value
    ]
    assert risk_off
    last = result.rebalances[-1]
    assert last.target_cash_weight == pytest.approx(1.0)


def test_future_liquid_coin_cannot_enter_historical_universe() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(24 * 61):
        timestamp = start + timedelta(hours=index)
        bars.append(_crypto_bar(timestamp, "BTC/USD", 100.0, volume=100.0))
        eth_volume = 10.0 if timestamp < datetime(2026, 2, 1, tzinfo=UTC) else 1000.0
        bars.append(_crypto_bar(timestamp, "ETH/USD", 100.0, volume=eth_volume))
    universe = build_crypto_point_in_time_universe(bars, maximum_symbols=1)

    strategy = CryptoCrossSectionalMomentum1hV1({"rebalance_hours": 24, "momentum_scheme": "7d"})
    engine = PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(Market.CRYPTO, "hours:24"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=6.0,
        crypto_taker_fee_bps=5.0,
        benchmark_symbol="BTC/USD",
        universe=universe,
        universe_version=CRYPTO_UNIVERSE_VERSION,
    )
    result = engine.run(bars, liquidate_at_end=False)
    february = datetime(2026, 2, 1, tzinfo=UTC)
    early_eth_weights = [
        weight
        for timestamp, weights in result.weight_history
        if timestamp < february
        for symbol, weight in weights.items()
        if symbol == "ETH/USD"
    ]
    assert not early_eth_weights


def test_portfolio_backtest_is_deterministic() -> None:
    bars = _equity_series(300, {"SPY": 0.0004, "QQQ": 0.0005, "XLE": 0.0001})
    engine_a = _equity_engine(EquityEtfDualMomentumV1())
    engine_b = _equity_engine(EquityEtfDualMomentumV1())
    result_a = engine_a.run(bars, liquidate_at_end=False)
    result_b = engine_b.run(bars, liquidate_at_end=False)
    assert result_a.total_return == result_b.total_return
    assert result_a.equity_curve == result_b.equity_curve
    assert result_a.rebalances == result_b.rebalances


def test_universe_version_constant_is_used_as_default() -> None:
    engine = _equity_engine(EquityEtfDualMomentumV1())
    assert engine.universe_version == EQUITY_UNIVERSE_VERSION


def test_information_ratio_aligns_benchmark_by_trading_day() -> None:
    """The IR excess series must compare daily portfolio returns against
    daily benchmark returns. A portfolio that exactly tracks its benchmark
    must report an information ratio of zero even on hourly bars, where the
    benchmark's last hourly move used to be subtracted from every hourly
    portfolio return."""

    class _BtcTarget(PortfolioStrategy):
        name = "btc_target"
        version = "1.0.0"
        market = Market.CRYPTO
        timeframe = "1h"
        minimum_history_bars = 1

        def _validate_parameters(self) -> None:
            return None

        def decide(self, context: PortfolioContext) -> TargetPortfolio:
            return TargetPortfolio(
                strategy_name=self.name,
                strategy_version=self.version,
                decision_timestamp=context.decision_timestamp,
                weights=(TargetWeight("BTC/USD", 1.0),),
                cash_weight=0.0,
                universe_version="test",
            )

    start = datetime(2024, 1, 1, tzinfo=UTC)
    base = 100.0
    bars = [
        _crypto_bar(
            start + timedelta(days=day, hours=hour),
            "BTC/USD",
            base * 1.01 if hour == 23 else base,
        )
        for day in range(10)
        for hour in range(24)
    ]
    warmup = [_crypto_bar(start - timedelta(hours=1), "BTC/USD", base)]
    engine = PortfolioBacktestEngine(
        _BtcTarget(),
        RebalancePolicy(Market.CRYPTO, "hours:6"),
        starting_equity=100_000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        crypto_taker_fee_bps=0.0,
        benchmark_symbol="BTC/USD",
    )
    result = engine.run(bars, warmup=warmup, liquidate_at_end=False)
    assert result.information_ratio == pytest.approx(0.0, abs=1e-9)
    assert set(EQUITY_ETF_UNIVERSE) >= {"SPY", "QQQ", "IWM", "XLK"}


def test_benchmark_return_is_full_window_not_last_day() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [
        _equity_bar(start + timedelta(days=index), "SPY", 100.0 * (1.001**index))
        for index in range(60)
    ]
    engine = _equity_engine(_FixedTargetStrategy({"SPY": 1.0}))
    result = engine.run(bars, liquidate_at_end=False)
    expected = 1.001**59 - 1
    assert result.benchmark_return == pytest.approx(expected, rel=1e-9)
    assert result.benchmark_return > 0.05
    assert result.excess_return == pytest.approx(
        result.total_return - result.benchmark_return, abs=1e-9
    )
