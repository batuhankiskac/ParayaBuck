from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research/information-discreteness-economic-v1/candidate_strategy.py"
RUNNER_PATH = ROOT / "research/information-discreteness-economic-v1/economic_runner.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


candidate_module = _load(MODULE_PATH, "information_discreteness_candidate_tests")
runner_module = _load(RUNNER_PATH, "information_discreteness_runner_tests")


def _bar(timestamp: datetime, symbol: str, close: float) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
        market=Market.EQUITY,
    )


def _context(history: dict[str, tuple[Bar, ...]], timestamp: datetime) -> PortfolioContext:
    return PortfolioContext(
        decision_timestamp=timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=None,
        universe_version="",
        equity=100_000.0,
        holdings={},
        prices={symbol: bars[-1].close for symbol, bars in history.items()},
        benchmark_symbol="SPY",
        ordinary_alpha_excluded_symbols=("SPY", "SH", "PSQ", "DOG"),
    )


def test_frozen_indexing_preserves_zero_returns_and_signs() -> None:
    start = datetime(2020, 1, 2, tzinfo=UTC)
    timestamps = tuple(start + timedelta(days=index) for index in range(274))
    spy = tuple(_bar(timestamp, "SPY", 100.0) for timestamp in timestamps)
    # 252 formation returns: 126 positive, 126 zero; skipped bars are distinct
    # and must not enter the count denominator.
    prices = [100.0]
    for index in range(1, 253):
        prices.append(prices[-1] * (1.01 if index <= 126 else 1.0))
    prices.extend([prices[-1] * 1.1] * 21)
    symbol = tuple(_bar(timestamp, "AAA", close) for timestamp, close in zip(timestamps, prices, strict=True))
    record = candidate_module.EquityInformationDiscretenessContinuous1dV1._score_one("AAA", symbol, spy)
    assert record["scoreable"] is True
    assert record["denominator"] == 252
    assert record["positive_count"] == 126
    assert record["zero_count"] == 126
    assert record["negative_count"] == 0
    assert len(record["formation_sessions"]) == 252
    assert len(record["skipped_sessions"]) == 21
    assert record["qualification_status"] == "qualified_positive_pret"
    assert record["id"] == -(126 / 252)


def test_candidate_selects_six_or_fewer_and_reserves_roles() -> None:
    start = datetime(2020, 1, 2, tzinfo=UTC)
    timestamps = tuple(start + timedelta(days=index) for index in range(274))
    history: dict[str, tuple[Bar, ...]] = {"SPY": tuple(_bar(timestamp, "SPY", 100.0) for timestamp in timestamps)}
    for offset, symbol in enumerate(("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "SH")):
        closes = [100.0]
        for index in range(1, 274):
            closes.append(closes[-1] * (1.001 + offset * 0.0001 if index <= 252 else 1.0))
        history[symbol] = tuple(
            _bar(timestamp, symbol, close) for timestamp, close in zip(timestamps, closes, strict=True)
        )
    timestamp = timestamps[-1]
    strategy = candidate_module.EquityInformationDiscretenessContinuous1dV1()
    target = strategy.decide(_context(history, timestamp))
    assert len(target.weights) == 6
    assert all(weight.weight <= 0.10 for weight in target.weights)
    assert "SH" not in {weight.symbol for weight in target.weights}
    assert target.cash_weight == 1.0 - 6 * (0.55 / 6)
    assert all(
        len(record["formation_sessions"]) == 252
        for record in strategy.audit_events[0]["records"]
        if record.get("scoreable")
    )


def test_last_session_policy_uses_each_month_end_only() -> None:
    start = datetime(2024, 1, 2, tzinfo=UTC)
    bars = []
    for index in range(4):
        timestamp = start + timedelta(days=index)
        bars.append(_bar(timestamp, "SPY", 100.0 + index))
    # The helper uses timestamps, so the last available session in each month
    # is deterministic even in this compact fixture.
    schedule = runner_module.build_last_session_schedule(bars, endpoint=bars[-1].timestamp)
    assert schedule == (bars[-1].timestamp,)
    policy = runner_module.LastCompletedRegularSessionPolicy(frozenset(schedule))
    assert policy.is_period_start(None, bars[-1].timestamp)
    assert not policy.is_period_start(bars[-2].timestamp, bars[-2].timestamp)
