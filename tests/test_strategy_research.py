from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.backtest.broker import BacktestBroker
from parayabuck.backtest.challengers import (
    crypto_cross_sectional_momentum,
    dual_momentum_weights,
    time_series_momentum_target,
)
from parayabuck.backtest.engine import BacktestResult
from parayabuck.backtest.experiments import (
    ExperimentSpec,
    ProductionGate,
    StrategyCandidateSpace,
    TrialResult,
    deflated_sharpe_probability,
    hash_bars,
    probability_backtest_overfitting,
)
from parayabuck.backtest.research import run_nested_experiment
from parayabuck.backtest.universe import build_crypto_point_in_time_universe
from parayabuck.backtest.validation import calendar_fold_windows
from parayabuck.config import EngineConfig, RiskLimits
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    ExitPolicy,
    Market,
    PositionSnapshot,
    Signal,
    TrailingStopType,
)
from parayabuck.data.aggregator import aggregate_historical_bars
from parayabuck.engine import TradingEngine
from parayabuck.execution.protective_stops import safely_replace_stop
from parayabuck.execution.rollout import (
    RolloutStage,
    RolloutState,
    evaluate_rollout,
)
from parayabuck.risk.manager import RiskManager
from parayabuck.strategies.specs import CryptoTrendParams, StockTrendParams


def _bar(
    timestamp: datetime,
    *,
    symbol: str = "BTC/USD",
    timeframe: str = "1h",
    volume: float = 100.0,
    market: Market = Market.CRYPTO,
) -> Bar:
    return Bar(timestamp, symbol, timeframe, 100, 101, 99, 100, volume, market)


def test_exit_policy_rejects_incomplete_trailing_configuration() -> None:
    with pytest.raises(ValueError, match="requires a trailing multiple"):
        ExitPolicy(policy_id="bad", trailing_type=TrailingStopType.CHANDELIER)


def test_strategy_parameter_boundaries_are_fail_closed() -> None:
    with pytest.raises(ValueError, match="Fast EMA"):
        StockTrendParams(fast_ema_period=50, slow_ema_period=20)
    with pytest.raises(ValueError, match="Donchian|donchian"):
        CryptoTrendParams(donchian_period=0)


def test_historical_aggregation_drops_incomplete_target_bucket() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        _bar(start + timedelta(minutes=5 * index), timeframe="5m") for index in range(23)
    ]
    aggregated = aggregate_historical_bars(bars, "1h")
    assert len(aggregated) == 1
    assert aggregated[0].timestamp == start
    assert aggregated[0].volume == 1200


def test_calendar_folds_reserve_holdout_and_apply_embargo() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [_bar(start + timedelta(days=index)) for index in range(730)]
    windows = calendar_fold_windows(
        bars,
        train_months=9,
        test_months=1,
        outer_holdout_months=6,
        embargo=timedelta(days=3),
    )
    assert windows
    first = windows[0]
    assert first.train_start == datetime(2024, 1, 1, tzinfo=UTC)
    assert first.test_start == datetime(2024, 10, 1, tzinfo=UTC)
    assert first.train_end == first.test_start - timedelta(days=3)
    assert windows[-1].test_end <= datetime(2025, 8, 1, tzinfo=UTC)


def test_multiple_testing_statistics_are_deterministic() -> None:
    probability = deflated_sharpe_probability(0.2, 250, 100)
    assert 0 < probability < 1
    matrix = [
        [0.03, 0.02, -0.01, 0.01, 0.02, -0.02],
        [0.01, -0.02, 0.03, -0.01, 0.01, 0.02],
        [-0.01, 0.01, 0.02, 0.03, -0.02, 0.01],
    ]
    assert probability_backtest_overfitting(matrix) == probability_backtest_overfitting(matrix)


def test_experiment_manifest_has_bounded_concrete_candidate_space() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = ExperimentSpec.from_file(root / "config/experiments/strategy_1h_v1.yaml")
    assert sum(space.combinations for space in spec.candidate_spaces) == 144
    assert sum(len(space.exit_policies) for space in spec.candidate_spaces) <= 30
    assert spec.alpha_risk_per_trade_pct == 0.0025
    assert spec.outer_holdout_months == 6


def test_data_hash_changes_when_market_data_changes() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [_bar(timestamp)]
    changed = [replace(bars[0], close=100.5, high=101.5)]
    assert hash_bars(bars) == hash_bars(list(reversed(bars)))
    assert hash_bars(bars) != hash_bars(changed)


def test_crypto_universe_uses_only_trailing_month_information() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(24 * 61):
        timestamp = start + timedelta(hours=index)
        bars.append(_bar(timestamp, symbol="BTC/USD", volume=100.0))
        eth_volume = 10.0 if timestamp < datetime(2026, 2, 1, tzinfo=UTC) else 1000.0
        bars.append(_bar(timestamp, symbol="ETH/USD", volume=eth_volume))
    universe = build_crypto_point_in_time_universe(bars, maximum_symbols=1)
    february = datetime(2026, 2, 1, tzinfo=UTC)
    march = datetime(2026, 3, 1, tzinfo=UTC)
    assert universe.contains("BTC/USD", Market.CRYPTO, february)
    assert not universe.contains("ETH/USD", Market.CRYPTO, february)
    assert universe.contains("ETH/USD", Market.CRYPTO, march)


def test_strict_production_gate_requires_every_condition() -> None:
    trial = TrialResult(
        trial_id="ok",
        strategy_name="test",
        strategy_version="1",
        parameters=(),
        fold_results=(),
        total_trades=120,
        profit_factor=1.2,
        sharpe=0.8,
        calmar=1.0,
        expectancy=1.0,
        deflated_sharpe_probability=0.96,
        probability_backtest_overfitting=0.1,
        max_drawdown=0.08,
        positive_fold_ratio=0.7,
        stress_profit_factor=1.01,
        stress_expectancy=0.01,
        gross_profit_by_symbol=(("A", 25), ("B", 25), ("C", 25), ("D", 25)),
        neighborhood_robust=True,
        holdout_evaluations=1,
    )
    assert ProductionGate().evaluate(trial).passed
    failed = ProductionGate().evaluate(replace(trial, stress_expectancy=-0.01))
    assert not failed.passed
    assert "negative_stress_expectancy" in failed.failures


def test_strategy_specific_mean_reversion_rr_can_be_below_global_trend_gate() -> None:
    signal = Signal(
        "SPY",
        Market.EQUITY,
        Direction.LONG,
        "stock_mean_reversion",
        75,
        0.75,
        100,
        98,
        102.6,
        datetime.now(UTC),
        minimum_risk_reward=1.2,
    )
    decision = RiskManager(RiskLimits(minimum_risk_reward=1.8)).evaluate(
        signal, AccountSnapshot(10_000, 10_000, 10_000, 0)
    )
    assert decision.approved


def test_open_stop_risk_heat_blocks_new_entries() -> None:
    signal = Signal(
        "QQQ",
        Market.EQUITY,
        Direction.LONG,
        "test",
        75,
        0.75,
        100,
        98,
        104,
        datetime.now(UTC),
    )
    held = PositionSnapshot(
        "SPY",
        15,
        100,
        100,
        Market.EQUITY,
        stop_price=90,
    )
    account = AccountSnapshot(8_500, 10_000, 10_000, 0, positions=(held,))
    decision = RiskManager(RiskLimits(max_open_risk_pct=0.015)).evaluate(signal, account)
    assert not decision.approved
    assert decision.reason == "max_open_stop_risk"


def test_volatility_targeting_only_reduces_trade_risk() -> None:
    manager = RiskManager(
        RiskLimits(
            risk_per_trade_pct=0.005,
            volatility_targeting_enabled=True,
            annualized_volatility_target=0.08,
        )
    )
    assert manager.set_realized_volatility(0.16) == pytest.approx(0.5)
    signal = Signal(
        "SPY",
        Market.EQUITY,
        Direction.LONG,
        "test",
        75,
        0.75,
        100,
        99,
        102,
        datetime.now(UTC),
    )
    decision = manager.evaluate(signal, AccountSnapshot(10_000, 10_000, 10_000, 0))
    assert decision.approved
    assert decision.risk_amount == pytest.approx(25.0)


def test_dual_momentum_is_long_or_cash_and_weights_sum_to_one() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(280):
        timestamp = start + timedelta(days=index)
        for symbol, drift in (("SPY", 0.1), ("QQQ", 0.2), ("XLE", -0.1)):
            close = 100 + drift * index
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close,
                    close + 1,
                    close - 1,
                    close,
                    1000,
                    Market.EQUITY,
                )
            )
    selection = dual_momentum_weights(bars, start + timedelta(days=280), top_n=2)
    assert {symbol for symbol, _ in selection.weights} == {"SPY", "QQQ"}
    assert sum(weight for _, weight in selection.weights) == pytest.approx(1.0)


def test_crypto_momentum_obeys_btc_regime_gate() -> None:
    assert crypto_cross_sectional_momentum(
        [], datetime.now(UTC), btc_regime_positive=False
    ).weights == ()


def test_time_series_momentum_never_uses_leverage() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [
        Bar(
            start + timedelta(days=index),
            "SPY",
            "1d",
            100 + index,
            101 + index,
            99 + index,
            100 + index,
            1000,
            Market.EQUITY,
        )
        for index in range(280)
    ]
    exposures = time_series_momentum_target(bars, start + timedelta(days=280))
    assert exposures
    assert all(0 <= exposure <= 1 for _, exposure in exposures)


def test_rollout_halts_only_new_entries_on_live_drift() -> None:
    decision = evaluate_rollout(
        RolloutState(
            stage=RolloutStage.CANARY,
            stage_started=date(2026, 1, 1),
            as_of=date(2026, 2, 1),
            fills=50,
            trades=50,
            expected_slippage_bps=2,
            realized_slippage_bps=3.1,
            rolling_50_profit_factor=0.85,
            realized_drawdown=0.16,
            validated_drawdown=0.10,
        )
    )
    assert decision.halt_new_entries
    assert not decision.eligible_for_next_stage
    assert "realized_slippage_above_limit" in decision.reasons


def test_failed_live_stop_replace_keeps_old_protection() -> None:
    result = safely_replace_stop(
        direction=Direction.LONG,
        current_stop=98,
        requested_stop=101,
        replace=lambda _price: False,
    )
    assert not result.succeeded
    assert result.active_stop == 98
    assert result.reason == "atomic_replace_failed"


def test_nested_experiment_keeps_alpha_risk_fixed_and_holdout_locked() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2025, 7, 1, tzinfo=UTC)
    bars = [
        Bar(
            start + timedelta(days=index),
            "SPY",
            "1h",
            100,
            101,
            99,
            100,
            1000,
            Market.EQUITY,
        )
        for index in range((end - start).days)
    ]
    space = StrategyCandidateSpace(
        "stock_trend_pullback",
        "2.0.0",
        Market.EQUITY,
        "1h",
        (("rsi_ceiling", (65.0, 68.0)),),
    )
    experiment = ExperimentSpec(
        "test_nested",
        start,
        end,
        ("SPY",),
        "test_v1",
        candidate_spaces=(space,),
        train_months=9,
        validation_months=1,
        outer_holdout_months=1,
        embargo_bars=1,
        max_parameter_trials=2,
        max_holdout_finalists=1,
    )
    observed_risks: list[float] = []

    class FakeEngine:
        def __init__(self, config: EngineConfig) -> None:
            observed_risks.append(config.risk.risk_per_trade_pct)
            self.broker = type("Broker", (), {"trades": ()})()

        def run(self, test: list[Bar], warmup: list[Bar] | None = None) -> BacktestResult:
            del warmup
            return BacktestResult(
                100_000,
                100_100,
                0.001,
                0.0,
                0.001,
                0.01,
                10,
                0.6,
                1.2,
                False,
                10,
                0.8,
                1.0,
                (100_000, 100_100),
                (10.0,) * 6 + (-5.0,) * 4,
                0,
                0,
                0,
                False,
                start_date=test[0].timestamp.date().isoformat(),
                end_date=test[-1].timestamp.date().isoformat(),
                duration_days=max(1, (test[-1].timestamp - test[0].timestamp).days),
                calmar=0.1,
            )

    trials = run_nested_experiment(
        bars,
        experiment,
        EngineConfig(timeframe="1h", higher_timeframe="1d"),
        lambda config, _strategies, _cost: FakeEngine(config),
    )
    assert len(trials) == 2
    assert set(observed_risks) == {0.0025}
    assert all(trial.holdout_evaluations == 0 for trial in trials)


def test_unvalidated_strategy_version_is_fail_closed_for_live_profile() -> None:
    config = EngineConfig(live_trading_enabled=True)
    engine = TradingEngine(
        BacktestBroker(10_000),
        config,
        enforce_live_safety=True,
        paper_mode=False,
    )
    signal = Signal(
        "SPY",
        Market.EQUITY,
        Direction.LONG,
        "stock_trend_pullback",
        80,
        0.8,
        100,
        98,
        104,
        datetime.now(UTC),
        strategy_version="2.0.0",
    )
    assert engine._execution_gate_reason(signal) == "strategy_version_not_live_validated"
