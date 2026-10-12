"""Intra-session restart must never decide on the forming daily bar.

Regression: when the service restarts while the New York session is still
trading, the split-basis verification fetches the provider's daily history
up to *now*. The provider already lists today's session with an intraday
close; adopting it made the startup catch-up decide on a partial bar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository

# Provider daily labels are 00:00 America/New_York (04:00 UTC during EDT).
_YESTERDAY = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)  # Monday, completed
_TODAY = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)  # Tuesday, forming
_INTRA_SESSION = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)  # 11:00 ET
_AFTER_CLOSE = datetime(2026, 9, 29, 20, 30, tzinfo=UTC)  # 16:30 ET


class _Broker:
    def __init__(self) -> None:
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(100_000, 100_000, 100_000, 0.0)

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(0.01, 0.01, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


class _Universe:
    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return True


def _bar(symbol: str, timestamp: datetime, close: float) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)


def _completed_history(sessions: int = 30) -> dict[str, list[Bar]]:
    days: list[datetime] = []
    day = _YESTERDAY
    while len(days) < sessions:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    return {
        "QQQ": [_bar("QQQ", d, 400.0 + index) for index, d in enumerate(days)],
        "QLD": [_bar("QLD", d, 100.0 + index * 0.5) for index, d in enumerate(days)],
    }


def _runtime(tmp_path) -> tuple[PortfolioRuntime, _Broker]:
    broker = _Broker()
    runtime = PortfolioRuntime(
        EquityLeveragedTrendV1({"signal_symbol": "QQQ", "hold_symbol": "QLD", "sma_bars": 5}),
        ("QQQ", "QLD"),
        broker,
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
        universe=_Universe(),
        universe_version="test-universe-v1",
    )
    runtime.history = _completed_history()
    return runtime, broker


def _forming_provider() -> dict[str, list[Bar]]:
    """Provider splits series through today's FORMING session: QQQ is
    crashing intraday far below its SMA, which would flip the trend off."""
    history = _completed_history()
    return {
        "QQQ": [*history["QQQ"], _bar("QQQ", _TODAY, 300.0)],
        "QLD": [*history["QLD"], _bar("QLD", _TODAY, 80.0)],
    }


def test_intra_session_restart_catch_up_decides_on_last_completed_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: _INTRA_SESSION)
    runtime, broker = _runtime(tmp_path)

    # Startup verification runs mid-session and sees today's forming bar.
    runtime.adopt_authoritative_signal_history(_forming_provider(), {"QQQ": _TODAY, "QLD": _TODAY})
    assert runtime.history["QQQ"][-1].timestamp == _YESTERDAY
    assert runtime.history["QLD"][-1].timestamp == _YESTERDAY

    result = runtime.startup_catch_up()
    assert result.status == "submitted"
    assert result.target is not None
    assert result.target.decision_timestamp == _YESTERDAY
    assert result.target.metadata["trend_state"] == "uptrend"
    assert result.target.metadata["signal_close"] == pytest.approx(429.0)
    assert {weight.symbol for weight in result.target.weights} == {"QLD"}
    assert broker.submitted and all(order.symbol == "QLD" for order in broker.submitted)


def test_forming_tail_already_in_history_is_ignored_by_the_decision(tmp_path, monkeypatch) -> None:
    """A forming bar that reached the history before this guard existed (or
    via any other path) is pruned before the catch-up reads the tail."""
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: _INTRA_SESSION)
    runtime, _broker = _runtime(tmp_path)
    runtime.history = {symbol: list(series) for symbol, series in _forming_provider().items()}

    result = runtime.startup_catch_up()
    assert result.target is not None
    assert result.target.decision_timestamp == _YESTERDAY
    assert result.target.metadata["trend_state"] == "uptrend"
    assert all(series[-1].timestamp == _YESTERDAY for series in runtime.history.values())


def test_forming_bar_passed_to_process_closed_bars_is_not_a_decision_bar(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: _INTRA_SESSION)
    runtime, broker = _runtime(tmp_path)
    forming = [_bar("QQQ", _TODAY, 300.0), _bar("QLD", _TODAY, 80.0)]

    result = runtime.process_closed_bars(forming)
    assert result.status == "no_eligible_bars"
    assert not broker.submitted
    assert all(series[-1].timestamp == _YESTERDAY for series in runtime.history.values())


def test_same_session_bar_is_adopted_once_the_session_has_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: _AFTER_CLOSE)
    runtime, _broker = _runtime(tmp_path)
    runtime.adopt_authoritative_signal_history(_forming_provider(), {"QQQ": _TODAY, "QLD": _TODAY})
    assert runtime.history["QQQ"][-1].timestamp == _TODAY
    assert runtime.prune_unfinished_signal_sessions() == 0
