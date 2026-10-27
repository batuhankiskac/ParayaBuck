"""W1 regression tests for the second 2026-10-11 audit (engine core).

Deterministic, single-invariant, in-memory fakes only. Each test fails before
its fix and passes after (the boundary tests pin existing ``>=`` semantics).
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import parayabuck.runtime as runtime_module
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.alpaca_updates import TradeUpdate
from parayabuck.execution.portfolio import PortfolioRiskLimits, PortfolioRiskOverlay
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import PortfolioRuntime
from parayabuck.runtime import EngineRuntime

_TS0 = datetime(2025, 6, 2, 4, 0, tzinfo=UTC)


def _bar(symbol: str, timestamp: datetime, close: float = 100.0) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000, Market.EQUITY)


def _engine() -> EngineRuntime:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._warming_up = False
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._state_lock = threading.RLock()
    engine._portfolio_lock = threading.RLock()
    engine._entry_risk_latch = {Market.EQUITY: None}
    engine._market_entry_halts = {Market.EQUITY: None}
    engine._portfolio_runtimes = ()
    return engine


# Q1A-1: the fill-driven / resume advance shares the decision path's basis latch.
class _AdvancePortfolio:
    market = Market.EQUITY
    history: dict[str, list[Bar]] = {}

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    def advance_rebalance(self, **kwargs: Any) -> SimpleNamespace:
        self.seen.update(kwargs)
        return SimpleNamespace(status="buy_ready", decision_id="dec-1")


def test_advance_withholds_deferred_buys_while_signal_basis_unverified() -> None:
    engine = _engine()
    engine._symbols_by_market = {Market.EQUITY: {"QQQ", "QLD"}}
    portfolio = _AdvancePortfolio()
    engine._portfolio_runtimes = (portfolio,)
    assert engine._equity_signal_basis_verified() is False
    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert portfolio.seen["submit_deferred_buys"] is False


def test_advance_submits_deferred_buys_once_signal_basis_verified() -> None:
    engine = _engine()
    engine._symbols_by_market = {Market.EQUITY: {"QQQ", "QLD"}}
    engine._signal_adjustment_state().update({"healthy": True, "last_success_at": datetime.now(UTC)})
    portfolio = _AdvancePortfolio()
    engine._portfolio_runtimes = (portfolio,)
    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert portfolio.seen["submit_deferred_buys"] is True


# Q2A-1 / Q2A-5: hostile non-finite prices are refused at snapshot build.
@pytest.mark.parametrize(
    ("avg_price", "current_price"),
    [(math.inf, 100.0), (100.0, math.nan), (math.nan, -math.inf)],
)
def test_position_snapshot_rejects_non_finite_prices(avg_price: float, current_price: float) -> None:
    with pytest.raises(ValueError, match="prices must be finite"):
        PositionSnapshot("QLD", 10, avg_price, current_price, Market.EQUITY, managed_qty=10)


# Q2C-1 / Q2C-3: the SPY benchmark never gates the QQQ/QLD signal basis.
class _HistoryOnlyFor:
    def __init__(self, splits: dict[str, list[Bar]]) -> None:
        self.splits = splits
        self.requested: list[tuple[str, ...]] = []

    def get_bars_batch(self, symbols, timeframe, *, start, end, adjustment, outcome=None):  # noqa: ANN001
        self.requested.append(tuple(symbols))
        return {symbol: list(self.splits[symbol]) for symbol in symbols if symbol in self.splits}


def test_signal_basis_verifies_decision_symbols_without_the_benchmark() -> None:
    dates = [_TS0 + timedelta(days=index) for index in range(3)]
    universe = ("SPY", "QQQ", "QLD")
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.EQUITY
    runtime.strategy = SimpleNamespace(timeframe="1d", minimum_history_bars=10, decision_symbols=("QQQ", "QLD"))
    runtime.symbols = universe
    runtime.history = {symbol: [_bar(symbol, day) for day in dates] for symbol in universe}
    runtime.signal_verified_through_by_symbol = {}
    runtime.previous_timestamp = None
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._symbols_by_market = {Market.EQUITY: set(universe)}
    engine._portfolio_runtimes = (runtime,)
    provider = _HistoryOnlyFor({symbol: [_bar(symbol, day) for day in dates] for symbol in ("QQQ", "QLD")})
    engine._historical = provider

    assert engine.verify_equity_signal_basis(required_through=dates[-1]) is True
    assert provider.requested == [("QLD", "QQQ")]
    assert engine._equity_signal_basis_latch(dates[-1]) is None


# Q3A-2: a session decided under its other equity label is not deferrable.
def test_session_decided_under_the_other_label_is_not_deferrable() -> None:
    provider_label = datetime(2026, 9, 29, 0, 0, tzinfo=NEW_YORK).astimezone(UTC)
    stream_label = datetime(2026, 9, 29, 9, 30, tzinfo=NEW_YORK).astimezone(UTC)
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.EQUITY
    runtime.strategy = SimpleNamespace(timeframe="1d")
    runtime.account_id = "acct"
    runtime.configuration_digest = "digest"
    runtime.repository = SimpleNamespace(
        has_decision=lambda _account, _digest, timestamp, _market: timestamp == provider_label,
        recent_decision_timestamps=lambda *_args, **_kwargs: (provider_label,),
        # Newest decision of any digest (R-5); this test only exercises the label match.
        latest_decision_target=lambda *_args, **_kwargs: None,
    )
    engine = _engine()
    failure = RequestsConnectionError("broker read reset")
    assert engine._is_deferrable_decision_failure(runtime, [_bar("QLD", stream_label)], failure) is False
    next_session = stream_label + timedelta(days=1)
    assert engine._is_deferrable_decision_failure(runtime, [_bar("QLD", next_session)], failure) is True


# Q3B-2: stream events are normalized before they become an order's status.
class _TradeRepository:
    def __init__(self, quantity: float = 10.0) -> None:
        self.quantity = quantity
        self.statuses: list[str] = []

    def has_order(self, client_order_id: str) -> bool:
        return client_order_id == "sell-1"

    def open_orders(self, _account_id: str = "unknown", market: Market | None = None) -> tuple[Any, ...]:
        return (SimpleNamespace(client_order_id="sell-1", quantity=self.quantity),)

    def apply_trade_update(self, **kwargs: Any) -> bool:
        self.statuses.append(kwargs["status"])
        return True


def _trade_engine(repository: _TradeRepository) -> tuple[EngineRuntime, list[str]]:
    engine = _engine()
    calls: list[str] = []
    fresh = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    engine._portfolio_repository = repository
    engine.broker = SimpleNamespace(account_id="acct", snapshot_if_fresh=lambda _age: fresh, account=lambda: fresh)
    now = datetime.now(UTC)
    engine._last_account_at = now
    engine._last_account_snapshot = fresh
    engine._last_account_snapshot_at = now
    engine.config = SimpleNamespace(account_poll_interval_seconds=15.0, maximum_account_age_for_entry_seconds=30.0)
    engine._persist_risk_state = lambda *_args, **_kwargs: None
    engine._advance_portfolio_rebalance = lambda _market, **_kwargs: calls.append("advance")  # type: ignore[method-assign]
    engine._schedule_reconcile = lambda: calls.append("reconcile")  # type: ignore[method-assign]
    return engine, calls


def _update(event: str, filled: float) -> TradeUpdate:
    return TradeUpdate(
        event=event,
        client_order_id="sell-1",
        broker_order_id="broker-sell-1",
        symbol="QLD",
        market=Market.EQUITY,
        filled_qty=filled,
        average_fill_price=100.0,
        timestamp=datetime.now(UTC),
        fee=0.0,
        activity_id=f"activity-{event}-{filled}",
    )


@pytest.mark.parametrize(("filled", "expected"), [(10.0, "filled"), (4.0, "done_for_day")])
def test_calculated_stream_event_resolves_by_cumulative_fill(filled: float, expected: str) -> None:
    repository = _TradeRepository(quantity=10.0)
    engine, calls = _trade_engine(repository)
    asyncio.run(engine._handle_trade_update(_update("calculated", filled)))
    assert repository.statuses == [expected]
    assert calls == ["advance"]


def test_unknown_stream_event_is_left_to_reconciliation() -> None:
    repository = _TradeRepository()
    engine, calls = _trade_engine(repository)
    asyncio.run(engine._handle_trade_update(_update("restated", 10.0)))
    assert repository.statuses == []
    assert calls == ["reconcile"]


# Q5A-1 / Q5A-3: incident classification of database vs broker failures.
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RuntimeError("Alpaca account is blocked from trading"), False),
        (RuntimeError("database is locked"), True),
        (sqlite3.OperationalError("database is locked"), True),
        (sqlite3.IntegrityError("UNIQUE constraint failed: portfolio_orders.client_order_id"), True),
    ],
)
def test_database_error_classification(exc: BaseException, expected: bool) -> None:
    assert runtime_module._is_database_error(exc) is expected


# Q5A-2 / Q5A-3: a transient SQLite lock on fill booking retries boundedly.
class _LockingRepository:
    def __init__(self, failures: int, error: Exception) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    def apply_trade_update(self, **_kwargs: Any) -> bool:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return True


def test_fill_booking_retries_a_transient_database_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "_TRADE_UPDATE_LOCK_BACKOFF_SECONDS", 0.0)
    repository = _LockingRepository(1, sqlite3.OperationalError("database is locked"))
    asyncio.run(_engine()._apply_trade_update_with_lock_retry(repository, _update("fill", 10.0), "fill"))
    assert repository.calls == 2


def test_fill_booking_raises_once_the_lock_retry_budget_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "_TRADE_UPDATE_LOCK_BACKOFF_SECONDS", 0.0)
    repository = _LockingRepository(99, sqlite3.OperationalError("database is locked"))
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(_engine()._apply_trade_update_with_lock_retry(repository, _update("fill", 10.0), "fill"))
    assert repository.calls == runtime_module._TRADE_UPDATE_LOCK_ATTEMPTS


def test_fill_booking_never_retries_a_non_lock_database_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "_TRADE_UPDATE_LOCK_BACKOFF_SECONDS", 0.0)
    repository = _LockingRepository(99, sqlite3.OperationalError("no such table: portfolio_orders"))
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(_engine()._apply_trade_update_with_lock_retry(repository, _update("fill", 10.0), "fill"))
    assert repository.calls == 1


# Q5E-3: a loss or drawdown of exactly the configured limit trips.
_LIMIT_DAILY = 0.02
_LIMIT_DRAWDOWN = 0.10


def _overlay_reasons(account: AccountSnapshot) -> tuple[str, ...]:
    limits = PortfolioRiskLimits(daily_loss_limit=_LIMIT_DAILY, drawdown_halt=_LIMIT_DRAWDOWN)
    target = TargetPortfolio(
        strategy_name="approved",
        strategy_version="1.0.0",
        decision_timestamp=_TS0,
        weights=(TargetWeight("QLD", 0.05),),
        cash_weight=0.95,
    )
    decision = PortfolioRiskOverlay(limits).apply(
        target,
        account,
        market=Market.EQUITY,
        approved_symbols=("QLD",),
        maximum_allocation=0.50,
        mode="paper",
    )
    return decision.reasons


def test_overlay_trips_at_exactly_the_daily_loss_limit() -> None:
    assert "daily_loss_limit" in _overlay_reasons(AccountSnapshot(98_000, 98_000, 98_000, -2_000))


def test_overlay_trips_at_exactly_the_drawdown_limit() -> None:
    assert "portfolio_drawdown_halt" in _overlay_reasons(AccountSnapshot(90_000, 90_000, 100_000, 0.0))


def _latch(monkeypatch: pytest.MonkeyPatch, account: AccountSnapshot) -> str | None:
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda *_args, **_kwargs: None)
    engine = _engine()
    engine.app_config = SimpleNamespace(
        risk=SimpleNamespace(daily_loss_limit_pct=_LIMIT_DAILY, max_drawdown_pct=_LIMIT_DRAWDOWN)
    )
    engine.broker = SimpleNamespace(account_id="acct")
    engine.ledger = SimpleNamespace(
        save_safety_incident=lambda *_args: None, clear_safety_incident=lambda *_args, **_kwargs: None
    )
    engine._evaluate_entry_risk_latch(account)
    return engine._entry_risk_latch[Market.EQUITY]


def test_entry_latch_trips_at_exactly_the_daily_loss_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _latch(monkeypatch, AccountSnapshot(98_000, 98_000, 98_000, -2_000)) == "daily_loss_limit"


def test_entry_latch_trips_at_exactly_the_drawdown_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _latch(monkeypatch, AccountSnapshot(90_000, 90_000, 100_000, 0.0)) == "portfolio_drawdown_halt"
