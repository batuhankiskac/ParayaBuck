"""Audit wave 2 regressions for the two-phase rebalance lifecycle (worker A1).

Covers P2-1-1 (a superseded BUY side is released by the advance paths), P2-1-2
(a dropped re-plan SELL does not hold the BUY side), P2-1-3 (the rejected-SELL
retry skips a leg a successor already carries), P2-2-5 (an untradable target
closes the BUY side) and the Q2-9-1 trade-update chain. Synthetic fixtures only.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.alpaca_updates import TradeUpdate
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest, PortfolioOrderSide
from parayabuck.monitoring import alerts
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import REBALANCE_PHASE_COMPLETE
from parayabuck.portfolio.runtime_service import (
    REBALANCE_STATUS_CLOSED_UNRESOLVED,
    PortfolioRuntime,
    PortfolioStateRepository,
)
from parayabuck.runtime import EngineRuntime

# Monday session close (16:00 ET, EDT), the next session's open, a point in that
# session while it still trades, its close, and a moment after that close.
_SESSION = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)
_NEXT_OPEN = datetime(2026, 9, 22, 13, 30, tzinfo=UTC)
_NEXT_MORNING = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)
_AFTER_NEXT_CLOSE = datetime(2026, 9, 22, 21, 0, tzinfo=UTC)
_TARGETS = {"AAPL": 0.15, "MSFT": 0.20}


@pytest.fixture
def clock(monkeypatch):
    """The wall clock the advance path reads; tests move it explicitly."""
    now = {"value": _NEXT_MORNING}
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: now["value"])
    return now


class _Strategy:
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = dict(targets)
        self.parameters = {"targets": dict(targets)}

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(self.parameters.items()))

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=tuple(TargetWeight(symbol, weight) for symbol, weight in self._targets.items()),
            cash_weight=1.0 - sum(self._targets.values()),
            universe_version="test",
        )


class _Broker:
    """Paper broker double that starts with AAPL 400 managed (a SELL to 150 is owed)."""

    def __init__(self, untradable: tuple[str, ...] = ()) -> None:
        self.positions = [_position("AAPL", 400)]
        self.open_symbols: set[str] = set()
        self.submitted: list[PortfolioOrderRequest] = []
        self.untradable = set(untradable)

    def account(self) -> AccountSnapshot:
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(100_000.0 - value, 100_000.0, 100_000.0, 0.0, tuple(self.positions))

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        if symbol in self.untradable:
            raise ValueError(f"{symbol} is not tradable")
        return AssetTradingRules(1.0, 1.0, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set(self.open_symbols)

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        self.open_symbols.add(order.symbol)
        return "broker-" + order.client_order_id


def _position(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol, qty, 100.0, 100.0, Market.EQUITY, qty_available=qty, ownership="managed", managed_qty=qty
    )


def _bars(day: datetime, symbols: tuple[str, ...] = ("AAPL", "MSFT")) -> list[Bar]:
    return [Bar(day, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in symbols]


def _runtime(
    repository: PortfolioStateRepository,
    broker: _Broker,
    targets: dict[str, float] = _TARGETS,
    symbols: tuple[str, ...] = ("AAPL", "MSFT"),
) -> PortfolioRuntime:
    settings: dict[str, object] = {"mode": "paper", "maximum_allocation": 0.50, "account_id": "acct"}
    return PortfolioRuntime(_Strategy(targets), tuple(sorted(symbols)), broker, repository, **settings)


def _seed_history(runtime: PortfolioRuntime, *days: datetime) -> None:
    """Warmup-style history: the closed bars of each given day, oldest first."""
    for day in days:
        for bar in _bars(day):
            runtime.history[bar.symbol].append(bar)


def _fill(
    repository: PortfolioStateRepository,
    order: PortfolioOrderRequest,
    *,
    cumulative: float | None = None,
    status: str = "fill",
) -> None:
    repository.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-" + order.client_order_id,
        status=status,
        cumulative_filled_quantity=(cumulative if cumulative is not None else order.quantity),
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id="activity-" + order.client_order_id,
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _phase(repository: PortfolioStateRepository, decision_id: str) -> tuple[str, str]:
    row = repository.store.connection.execute(
        "SELECT phase, status FROM portfolio_rebalance_executions WHERE decision_id = ?", (decision_id,)
    ).fetchone()
    return str(row[0]), str(row[1])


def _orders(broker: _Broker, side: PortfolioOrderSide) -> list[PortfolioOrderRequest]:
    return [order for order in broker.submitted if order.side == side]


def _sell_submitted(tmp_path, **options) -> tuple[PortfolioRuntime, PortfolioStateRepository, _Broker, object, object]:
    """Decision on the session close; the AAPL SELL (400 -> 150) is working, the MSFT BUY deferred."""
    broker = _Broker(untradable=options.get("untradable", ()))
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    symbols = options.get("symbols", ("AAPL", "MSFT"))
    runtime = _runtime(repository, broker, options.get("targets", _TARGETS), symbols)
    first = runtime.process_closed_bars(_bars(_SESSION, symbols), force_scheduled=True)
    assert first.status == "sell_phase_submitted"
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    return runtime, repository, broker, first, sell


def _buy_ready(tmp_path):
    """The SELL has settled and the deferred BUY side is withheld (buy_ready)."""
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")
    assert runtime.advance_rebalance(submit_deferred_buys=False) is None
    return runtime, repository, broker, first


def _engine(repository: PortfolioStateRepository, portfolio: PortfolioRuntime, broker: _Broker) -> EngineRuntime:
    """An EngineRuntime with only the attributes the trade-update fill path reads."""
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._running = True
    engine._startup_committed = True
    engine._warming_up = False
    engine._state = "running"
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._state_lock = threading.RLock()
    engine._portfolio_lock = threading.RLock()
    engine._entry_risk_latch = {}
    engine._market_entry_halts = {}
    engine._portfolio_runtimes = (portfolio,)
    engine._portfolio_repository = repository
    engine.broker = SimpleNamespace(
        account_id="acct", account=broker.account, snapshot_if_fresh=lambda _age: broker.account()
    )
    engine.config = SimpleNamespace(account_poll_interval_seconds=15.0, maximum_account_age_for_entry_seconds=30.0)
    engine._last_account_at = None
    engine._last_account_snapshot = None
    engine._last_account_snapshot_at = None
    engine._persist_risk_state = lambda _account: None  # type: ignore[method-assign]
    engine._schedule_reconcile = lambda: None  # type: ignore[method-assign]
    engine._equity_signal_basis_latch = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    return engine


def _sell_fill_update(sell: PortfolioOrderRequest) -> TradeUpdate:
    return TradeUpdate(
        event="fill",
        client_order_id=sell.client_order_id,
        broker_order_id="broker-" + sell.client_order_id,
        symbol=sell.symbol,
        market=Market.EQUITY,
        filled_qty=sell.quantity,
        average_fill_price=sell.reference_price,
        timestamp=_NEXT_MORNING,
        fee=0.0,
        activity_id="activity-fill-" + sell.client_order_id,
    )


# --- P2-1-1 and Q2-9-1: the trade-update advance ----------------------------


def test_terminal_sell_fill_releases_exactly_one_buy_through_the_trade_update(tmp_path, clock) -> None:
    """Q2-9-1: a booked SELL fill through the engine's trade-update handler submits the BUY once."""
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")

    asyncio.run(_engine(repository, runtime, broker)._handle_trade_update(_sell_fill_update(sell)))

    buys = _orders(broker, PortfolioOrderSide.BUY)
    assert [order.symbol for order in buys] == ["MSFT"]
    assert all(order.decision_id == first.decision_id for order in buys)


def test_superseded_buy_side_is_not_released_by_the_trade_update(tmp_path, clock, monkeypatch) -> None:
    """Q2-9-1 stale variant: a newer session has closed, so the trade update sends no BUY."""
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(alerts, "emit", lambda *_args, **kwargs: emitted.append(kwargs))
    clock["value"] = _AFTER_NEXT_CLOSE
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _seed_history(runtime, _NEXT_OPEN)
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")

    asyncio.run(_engine(repository, runtime, broker)._handle_trade_update(_sell_fill_update(sell)))

    assert _orders(broker, PortfolioOrderSide.BUY) == []
    assert _phase(repository, first.decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)
    assert any("deferred BUYs were discarded" in str(kwargs.get("message", "")) for kwargs in emitted)


def test_superseded_buy_side_is_not_released_by_a_restart_resume(tmp_path, clock) -> None:
    """P2-1-1 at restart: a fresh runtime, seeded with a closed later session, does not release the old BUY."""
    clock["value"] = _AFTER_NEXT_CLOSE
    _runtime_, repository, broker, first = _buy_ready(tmp_path)

    restarted = _runtime(repository, broker)
    _seed_history(restarted, _SESSION, _NEXT_OPEN)
    assert restarted.advance_rebalance() is None
    assert _orders(broker, PortfolioOrderSide.BUY) == []
    assert _phase(repository, first.decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)


def test_a_forming_next_session_bar_does_not_supersede_the_buy_side(tmp_path, clock) -> None:
    """The normal T+1 flow: the next session is still trading, so the BUY side is released."""
    runtime, repository, broker, first = _buy_ready(tmp_path)
    _seed_history(runtime, _NEXT_OPEN)

    resumed = runtime.advance_rebalance()

    assert resumed is not None and resumed.status == "buy_phase_submitted"
    assert [order.symbol for order in _orders(broker, PortfolioOrderSide.BUY)] == ["MSFT"]


# --- P2-1-2: a dropped re-plan SELL holds the BUY side ---------------------


def test_a_dropped_replan_sell_holds_the_buy_side(tmp_path, clock, monkeypatch) -> None:
    """A re-plan SELL the fresh-id step drops still owes its exit: no BUY goes out and the rebalance closes."""
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _fill(repository, sell)
    assert runtime.advance_rebalance(submit_deferred_buys=False) is None
    # The re-plan still owes AAPL its exit (the fixture's lot book has no open lots, so the planner would not).
    original_plan = runtime.planner.plan

    def plan_with_exit(*args, **kwargs):
        exit_sell = replace(sell, client_order_id=f"{sell.client_order_id}-replan1")
        return (exit_sell, *original_plan(*args, **kwargs))

    monkeypatch.setattr(runtime.planner, "plan", plan_with_exit)
    monkeypatch.setattr(
        runtime,
        "_replan_orders_with_fresh_ids",
        lambda _decision_id, orders: tuple(order for order in orders if order.side == PortfolioOrderSide.BUY),
    )

    result = runtime.advance_rebalance()

    assert result is not None and result.status == "rebalance_complete"
    assert _orders(broker, PortfolioOrderSide.BUY) == []
    assert _phase(repository, first.decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)


# --- P2-2-5: an untradable target does not close the BUY side --------------


def test_an_untradable_target_does_not_block_the_buy_of_another_target(tmp_path, clock) -> None:
    """Only a held symbol owes an exit; an untradable target leaves the other BUY intact."""
    targets = {"AAPL": 0.15, "MSFT": 0.20, "NVDA": 0.20}
    symbols = ("AAPL", "MSFT", "NVDA")
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path, targets=targets, symbols=symbols)
    # MSFT becomes untradable after the decision (the decision path reads its rules unguarded).
    broker.untradable = {"MSFT"}
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 400 - sell.quantity)]
    broker.open_symbols.discard("AAPL")

    resumed = runtime.advance_rebalance()

    assert resumed is not None and resumed.status == "buy_phase_submitted"
    assert [order.symbol for order in _orders(broker, PortfolioOrderSide.BUY)] == ["NVDA"]


# --- P2-1-3: the rejected-SELL retry skips a leg a successor carries -------


def test_rejected_exit_is_retried_when_no_successor_exists(tmp_path, clock) -> None:
    """Control for the successor rule: a rejected exit with no successor is retried once."""
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _fill(repository, sell, cumulative=0.0, status="rejected")
    broker.open_symbols.discard("AAPL")

    submitted = runtime.retry_rejected_exit_sells(broker.account())

    assert len(submitted) == 1
    assert broker.submitted[-1].client_order_id == f"{sell.client_order_id}-retry1"


def test_rejected_exit_retry_is_skipped_when_a_successor_already_sold_the_symbol(tmp_path, clock) -> None:
    """A re-plan SELL of the same symbol already filled: a retry would sell the shares twice."""
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _fill(repository, sell, cumulative=0.0, status="rejected")
    broker.open_symbols.discard("AAPL")
    successor = replace(sell, client_order_id=f"{sell.client_order_id}-replan1")
    repository.record_order(successor, "filled")

    assert runtime.retry_rejected_exit_sells(broker.account()) == ()
    assert [order.client_order_id for order in _orders(broker, PortfolioOrderSide.SELL)] == [sell.client_order_id]
