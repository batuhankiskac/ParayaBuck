"""One decision per NYSE session, whatever label each symbol's bar carries.

The stream labels a session at its 09:30 ET open while the provider (and the
SIP session-close fallback) labels it at 00:00 ET. A session completed by a
fallback bar must still produce exactly one decision, a benchmark the
strategy never reads must not block it, and a failed broker read must not
consume the session.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest, PortfolioOrderSide
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository

UNIVERSE = ("SPY", "QQQ", "QLD")
# September 2026 is EDT: 00:00 ET = 04:00 UTC, 09:30 ET = 13:30 UTC.
SESSION = date(2026, 9, 21)


class _Universe:
    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return True


class _Broker:
    def __init__(self, account: AccountSnapshot | None = None) -> None:
        self.submitted: list[PortfolioOrderRequest] = []
        self.rules_requested: list[str] = []
        self.account_failures = 0
        self._account = account or AccountSnapshot(100_000, 100_000, 100_000, 0.0)

    def account(self) -> AccountSnapshot:
        if self.account_failures:
            self.account_failures -= 1
            raise ConnectionError("broker unreachable")
        return self._account

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        self.rules_requested.append(symbol)
        if symbol == "SPY":
            raise AssertionError("SPY is never targeted or held; its rules must not be fetched")
        return AssetTradingRules(symbol, market, 0.01, 0.01, 0.01, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


def _sessions(count: int) -> list[date]:
    days: list[date] = []
    day = SESSION
    while len(days) < count:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return sorted(days)


def _bar(symbol: str, timestamp: datetime, close: float) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)


def _provider_label(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 4, 0, tzinfo=UTC)


def _stream_label(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)


def _price(index: int, *, falling: bool = False) -> float:
    return 100.0 * (0.99**index if falling else 1.01**index)


def _runtime(tmp_path, broker: _Broker, *, falling: bool = False) -> PortfolioRuntime:
    runtime = PortfolioRuntime(
        EquityLeveragedTrendV1({"signal_symbol": "QQQ", "hold_symbol": "QLD", "sma_bars": 5}),
        UNIVERSE,
        broker,
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        mode="paper",
        maximum_allocation=0.5,
        universe=_Universe(),
        universe_version="test-universe-v1",
    )
    warmup = [
        _bar(symbol, _provider_label(day), _price(index, falling=falling))
        for index, day in enumerate(_sessions(30))
        for symbol in UNIVERSE
    ]
    runtime.seed(warmup)
    return runtime


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: datetime(2026, 9, 22, 12, 0, tzinfo=UTC))


def _decision_timestamps(tmp_path) -> list[str]:
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        return [row[0] for row in connection.execute("SELECT decision_timestamp FROM portfolio_decisions")]


def test_session_completed_by_a_sip_fallback_bar_decides_exactly_once(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    close = _price(30)

    stream = runtime.process_closed_bars(
        [_bar("QQQ", _stream_label(SESSION), close), _bar("SPY", _stream_label(SESSION), close)]
    )
    assert stream.status == "incomplete_strategy_batch"

    fallback = runtime.process_closed_bars([_bar("QLD", _provider_label(SESSION), close)])
    assert fallback.status == "submitted"
    assert fallback.target is not None
    assert fallback.target.decision_timestamp == _stream_label(SESSION)
    assert [order.symbol for order in fallback.orders] == ["QLD"]

    again = runtime.process_closed_bars([_bar("QLD", _provider_label(SESSION), close)])
    assert again.status in {"duplicate_decision", "not_scheduled"}
    assert _decision_timestamps(tmp_path) == [_stream_label(SESSION).isoformat()]


def test_missing_benchmark_does_not_block_the_decision(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    close = _price(30)

    result = runtime.process_closed_bars(
        [_bar("QQQ", _stream_label(SESSION), close), _bar("QLD", _stream_label(SESSION), close)]
    )

    assert result.status == "submitted"
    assert "SPY" not in broker.rules_requested


def test_failed_broker_read_does_not_consume_the_session(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    previous = runtime.previous_timestamp
    close = _price(30)
    batch = [_bar(symbol, _stream_label(SESSION), close) for symbol in UNIVERSE]
    broker.account_failures = 1

    with pytest.raises(ConnectionError):
        runtime.process_closed_bars(batch)

    assert runtime.previous_timestamp == previous
    assert runtime.process_closed_bars(batch).status == "submitted"


def test_startup_catch_up_decides_a_session_with_mixed_labels(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    close = _price(30)
    runtime.history["QQQ"].append(_bar("QQQ", _stream_label(SESSION), close))
    runtime.history["QLD"].append(_bar("QLD", _provider_label(SESSION), close))
    runtime.previous_timestamp = None

    result = runtime.startup_catch_up()

    assert result.status == "submitted"
    assert _decision_timestamps(tmp_path) == [_stream_label(SESSION).isoformat()]


def test_session_decided_under_the_provider_label_is_not_decided_again(tmp_path) -> None:
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    close = _price(30)
    first = runtime.process_closed_bars([_bar(symbol, _provider_label(SESSION), close) for symbol in UNIVERSE])
    assert first.status == "submitted"

    runtime.previous_timestamp = None  # a restart that lost the high-water
    second = runtime.process_closed_bars([_bar(symbol, _stream_label(SESSION), close) for symbol in UNIVERSE])

    assert second.status == "duplicate_decision"
    assert len(_decision_timestamps(tmp_path)) == 1


def test_unverified_signal_basis_holds_instead_of_selling(tmp_path) -> None:
    held = PositionSnapshot("QLD", 100.0, 70.0, 75.0, Market.EQUITY, ownership="managed", managed_qty=100.0)
    broker = _Broker(AccountSnapshot(92_500, 100_000, 100_000, 0.0, positions=(held,)))
    runtime = _runtime(tmp_path, broker, falling=True)
    close = _price(30, falling=True)
    batch = [_bar(symbol, _stream_label(SESSION), close) for symbol in UNIVERSE]

    result = runtime.process_closed_bars(batch, entry_latch="signal_adjustment_unverified")

    assert result.status == "signal_basis_hold"
    assert result.orders == ()
    assert broker.submitted == []
    assert result.target is not None
    assert result.target.metadata["trend_state"] == "downtrend"
    assert result.target.metadata["signal_basis_hold"] is True
    assert {weight.symbol for weight in result.target.weights} == {"QLD"}


def test_a_verified_downtrend_still_sells(tmp_path) -> None:
    held = PositionSnapshot("QLD", 100.0, 70.0, 75.0, Market.EQUITY, ownership="managed", managed_qty=100.0)
    broker = _Broker(AccountSnapshot(92_500, 100_000, 100_000, 0.0, positions=(held,)))
    runtime = _runtime(tmp_path, broker, falling=True)
    close = _price(30, falling=True)
    batch = [_bar(symbol, _stream_label(SESSION), close) for symbol in UNIVERSE]

    result = runtime.process_closed_bars(batch)

    assert [(order.symbol, order.side) for order in result.orders] == [("QLD", PortfolioOrderSide.SELL)]
