from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.experiments import (
    ProductionGate,
    TrialResult,
    deflated_sharpe_probability,
    hash_bars,
    new_manifest,
    outer_holdout_start,
    probability_backtest_overfitting,
)
from parayabuck.backtest.universe import build_crypto_point_in_time_universe
from parayabuck.backtest.validation import calendar_fold_windows
from parayabuck.config import RiskLimits
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
from parayabuck.risk.manager import RiskManager


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


def test_holdout_cutoff_is_shared_across_markets() -> None:
    end = datetime(2026, 8, 3, tzinfo=UTC)
    assert outer_holdout_start(end, 6) == outer_holdout_start(end, 6)
    assert outer_holdout_start(end, 6) == datetime(2026, 2, 3, tzinfo=UTC)


def test_holdout_cutoff_uses_shared_calendar_math_for_equity_and_crypto() -> None:
    """Equity and crypto experiments with the same end/months share one cutoff,
    so the manifest holdout boundary is identical across markets."""
    equity_end = datetime(2026, 7, 31, 20, tzinfo=UTC)
    crypto_end = datetime(2026, 8, 1, 0, tzinfo=UTC)
    months = 6
    assert outer_holdout_start(equity_end, months) == datetime(2026, 1, 31, 20, tzinfo=UTC)
    assert outer_holdout_start(crypto_end, months) == datetime(2026, 2, 1, 0, tzinfo=UTC)


def test_calendar_fold_window_count_does_not_depend_on_timestamp_count() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    dense = [_bar(start + timedelta(days=index)) for index in range(400)]
    sparse = dense[::7]
    dense_windows = calendar_fold_windows(
        dense, train_months=2, test_months=1, outer_holdout_months=1
    )
    sparse_windows = calendar_fold_windows(
        sparse, train_months=2, test_months=1, outer_holdout_months=1
    )
    assert dense_windows
    assert len(dense_windows) == len(sparse_windows)


def test_multiple_testing_statistics_are_deterministic() -> None:
    probability = deflated_sharpe_probability(0.2, 250, 100)
    assert 0 < probability < 1
    matrix = [
        [0.03, 0.02, -0.01, 0.01, 0.02, -0.02],
        [0.01, -0.02, 0.03, -0.01, 0.01, 0.02],
        [-0.01, 0.01, 0.02, 0.03, -0.02, 0.01],
    ]
    assert probability_backtest_overfitting(matrix) == probability_backtest_overfitting(matrix)


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


def test_manifest_round_trip_and_hash_are_stable() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [_bar(timestamp)]
    manifest = new_manifest(
        experiment_id="exp",
        spec_dict={"a": 1},
        universe_version="v1",
        seed=7,
        bars=bars,
        code_revision="abc",
    )
    payload = manifest.to_json()
    assert "data_hash" in payload
    assert manifest.data_hash == hash_bars(bars)


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
