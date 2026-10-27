"""Audit wave 2 fixes B1: exception paths in rebalance advance and submit (P2-5),
the non-tradable asset cache (C2-8-1) and the operator alert market argument."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.alpaca_client import AlpacaClient
from parayabuck.execution.alpaca_models import DefiniteOrderRejection
from parayabuck.execution.alpaca_orders import OrderService
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
)
from parayabuck.monitoring import alerts
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import REBALANCE_PHASE_BLOCKED, REBALANCE_PHASE_COMPLETE
from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository
from parayabuck.runtime import EngineRuntime, TradingState

_DAY = datetime(2025, 1, 1, tzinfo=UTC)
_TARGETS = {"AAPL": 0.15, "MSFT": 0.20}
_PRE_VT35_LIMITS = PortfolioRiskLimits(
    daily_loss_limit=0.005,
    drawdown_halt=0.05,
    maximum_gross=0.60,
    maximum_equity=0.50,
    maximum_equity_asset=0.10,
    minimum_cash=0.40,
    maximum_positions=6,
)


class _Strategy:
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1
    parameters = {"targets": dict(_TARGETS)}

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(self.parameters.items()))

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=tuple(TargetWeight(symbol, weight) for symbol, weight in _TARGETS.items()),
            cash_weight=1.0 - sum(_TARGETS.values()),
            universe_version="test",
        )


def _position(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol, qty, 100.0, 100.0, Market.EQUITY, qty_available=qty, ownership="managed", managed_qty=qty
    )


class _Broker:
    def __init__(self, positions: list[PositionSnapshot], untradable: frozenset[str] = frozenset()) -> None:
        self.positions = positions
        self.untradable = untradable
        self.open_symbols: set[str] = set()
        self.submitted: list[PortfolioOrderRequest] = []
        self.fail_first_submit = False

    def account(self) -> AccountSnapshot:
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(100_000.0 - value, 100_000.0, 100_000.0, 0.0, tuple(self.positions))

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        if symbol in self.untradable:
            raise ValueError(f"Asset is not tradable: {symbol}")
        return AssetTradingRules(1.0, 1.0, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set(self.open_symbols)

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        if self.fail_first_submit and not self.submitted:
            self.fail_first_submit = False
            raise TimeoutError("broker timeout")
        self.submitted.append(order)
        self.open_symbols.add(order.symbol)
        return "broker-" + order.client_order_id


def _bars(day: datetime) -> list[Bar]:
    return [Bar(day, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in _TARGETS]


def _runtime(tmp_path, broker: _Broker) -> tuple[PortfolioRuntime, PortfolioStateRepository]:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
        risk_limits=_PRE_VT35_LIMITS,
    )
    return runtime, repository


def _phase(repository: PortfolioStateRepository, decision_id: str) -> str:
    row = repository.store.connection.execute(
        "SELECT phase FROM portfolio_rebalance_executions WHERE decision_id = ?", (decision_id,)
    ).fetchone()
    return str(row[0])


@pytest.fixture
def captured(monkeypatch) -> list[dict[str, object]]:
    """Every operator alert emitted through ``alerts.emit`` (kind + fields)."""
    events: list[dict[str, object]] = []

    def emit(kind: str, *, message: str, **fields: object) -> None:
        events.append({"kind": kind, "message": message, **fields})

    monkeypatch.setattr(alerts, "emit", emit)
    return events


def _blocked_rebalance(tmp_path):
    """Submit the SELL leg, then lose its outcome so the rebalance blocks."""
    broker = _Broker([_position("AAPL", 400)])
    runtime, repository = _runtime(tmp_path, broker)
    first = runtime.process_closed_bars(_bars(_DAY))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    repository.update_order(sell.client_order_id, "reconciliation_required")
    assert runtime.advance_rebalance().status == "rebalance_blocked"
    return runtime, repository, first.decision_id


# P2-5-2: SELLs after a failed SELL in the same batch are booked as not sent
# (rejected, so the exit is retried and the operator alerted), not canceled.
def test_untried_sell_after_a_failed_submit_is_rejected_and_alerted_not_canceled(tmp_path, captured) -> None:
    broker = _Broker([_position("AAPL", 400), _position("MSFT", 300)])
    broker.fail_first_submit = True
    runtime, repository = _runtime(tmp_path, broker)

    with pytest.raises(TimeoutError):
        runtime.process_closed_bars(_bars(_DAY))

    decision_id = repository.latest_decision_id("acct", Market.EQUITY)
    assert decision_id is not None
    statuses = {str(row["symbol"]): str(row["status"]) for row in repository.orders_for_decision(decision_id)}
    assert statuses == {"AAPL": "reconciliation_required", "MSFT": "rejected"}
    rejected = [event for event in captured if event["kind"] == "order_rejected"]
    assert [event["symbol"] for event in rejected] == ["MSFT"]
    assert rejected[0]["reason"] == "batch_not_sent"
    assert [order.symbol for order in broker.submitted] == []


# P2-5-4: a held symbol the broker refuses must not fail the decision path.
# Its exit cannot be sent, the other exits still go out, and the operator is
# alerted once with the market passed exactly once (A1).
def test_untradable_held_symbol_does_not_fail_the_decision(tmp_path, captured) -> None:
    broker = _Broker([_position("AAPL", 400), _position("MSFT", 300)], untradable=frozenset({"MSFT"}))
    runtime, _ = _runtime(tmp_path, broker)

    result = runtime.process_closed_bars(_bars(_DAY))

    assert {order.symbol for order in result.orders} <= {"AAPL", "MSFT"}
    assert "MSFT" not in {order.symbol for order in result.orders}
    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.SELL] == ["AAPL"]
    untradable = [event for event in captured if event.get("category") == "asset_untradable"]
    assert len(untradable) == 1
    assert untradable[0]["kind"] == "paper_incident"
    assert untradable[0]["market"] == runtime._market_name()
    assert untradable[0]["symbol"] == "MSFT"


# A1: the operator alert helper passes ``market`` once, even when a caller also
# supplies it. The duplicate used to raise TypeError and lose the alert.
def test_operator_alert_helper_accepts_market_in_fields(tmp_path, captured) -> None:
    runtime, _ = _runtime(tmp_path, _Broker([_position("AAPL", 400)]))

    runtime._raise_operator_alert_once(
        ("MSFT", "asset_untradable"),
        message="Broker refused trading rules for a symbol",
        category="asset_untradable",
        market=runtime._market_name(),
        symbol="MSFT",
    )

    assert [event["kind"] for event in captured] == ["paper_incident"]
    assert captured[0]["market"] == runtime._market_name()


# P2-5-8: a BLOCKED rebalance with no order rows was never closed, so the reset
# could not clear its halt. Nothing of it was sent, so it settles at once.
def test_blocked_rebalance_without_order_rows_closes_and_reconciles(tmp_path, captured) -> None:
    runtime, repository, decision_id = _blocked_rebalance(tmp_path)
    repository.store.connection.execute("DELETE FROM portfolio_orders WHERE decision_id = ?", (decision_id,))
    repository.store.connection.commit()

    engine = object.__new__(EngineRuntime)
    engine._portfolio_repository = repository
    engine.broker = SimpleNamespace(account_id="acct")
    assert EngineRuntime._rebalance_state_reconciled(engine, Market.EQUITY) is True

    assert runtime.advance_rebalance() is None
    assert _phase(repository, decision_id) == REBALANCE_PHASE_COMPLETE


# P2-5-9: a late trade event after the reset (market ACTIVE) closes a settled
# block instead of re-raising the halt. While the market is halted it stays blocked.
def test_settled_block_is_closed_by_an_active_advance_only(tmp_path, captured) -> None:
    runtime, repository, decision_id = _blocked_rebalance(tmp_path)
    sell_rows = repository.orders_for_decision(decision_id, side="sell")
    repository.update_order(str(sell_rows[0]["client_order_id"]), "canceled")

    halted = runtime.advance_rebalance(submit_deferred_buys=False)
    assert halted is not None and halted.status == "rebalance_blocked"
    assert _phase(repository, decision_id) == REBALANCE_PHASE_BLOCKED

    assert runtime.advance_rebalance(submit_deferred_buys=True) is None
    assert _phase(repository, decision_id) == REBALANCE_PHASE_COMPLETE


# P2-5-1: a failed advance is alerted and latches the market pending
# reconciliation; the persisted state is kept, and a later success clears the count.
def test_failed_advance_alerts_and_latches_reconcile_pending(captured) -> None:
    calls: list[str] = []

    class _Runtime:
        market = Market.EQUITY

        def advance_rebalance(self, **kwargs: object) -> None:
            calls.append("advance")
            if len(calls) == 1:
                raise RuntimeError("broker unreachable")
            return None

    engine = object.__new__(EngineRuntime)
    engine._portfolio_lock = threading.RLock()
    engine._state_lock = threading.RLock()
    engine._market_entry_halts = {Market.EQUITY: None}
    engine._portfolio_runtimes = [_Runtime()]
    engine.trading_state = lambda market: TradingState.ACTIVE
    engine._equity_signal_basis_latch = lambda: None

    EngineRuntime._advance_portfolio_rebalance(engine, Market.EQUITY)

    assert engine._market_entry_halts[Market.EQUITY] == "rebalance_reconciliation_required"
    failures = [event for event in captured if event.get("category") == "rebalance_advance_failed"]
    assert len(failures) == 1
    assert failures[0]["kind"] == "paper_incident"
    assert failures[0]["failures"] == 1

    EngineRuntime._advance_portfolio_rebalance(engine, Market.EQUITY)
    assert calls == ["advance", "advance"]
    assert engine._rebalance_advance_failures == {}


# P2-5-7: a rejected-SELL retry that raises after some SELLs were sent still
# invalidates the account snapshot, so the fills are seen by the next decision.
def test_failed_exit_sell_retry_still_invalidates_the_account_snapshot(captured) -> None:
    invalidated: list[bool] = []

    class _Runtime:
        market = Market.EQUITY

        def retry_rejected_exit_sells(self, account: object, **kwargs: object) -> tuple[str, ...]:
            raise RuntimeError("second SELL refused")

    engine = object.__new__(EngineRuntime)
    engine._portfolio_lock = threading.RLock()
    engine._portfolio_runtimes = [_Runtime()]
    engine._exit_sell_retry_blocked = lambda market: False
    engine._exit_sell_retry_candidate = lambda runtime: True
    engine._schedule_reconcile = lambda: None
    engine._invalidate_account_snapshot_after_execution = lambda: invalidated.append(True)

    EngineRuntime._maybe_retry_rejected_exit_sells(engine, object())

    assert invalidated == [True]


# P2-5-3: a failure of the pre-send reads (rules or open-order GET) is a known
# not-sent outcome, never an ambiguous POST, and nothing is submitted.
class _OrderBroker:
    def __init__(self, *, rules_error: Exception | None = None, lookup_error: Exception | None = None) -> None:
        self.rules_error = rules_error
        self.lookup_error = lookup_error
        self.submits: list[object] = []

    def _field(self, value: object, name: str, default: object = None) -> object:
        return getattr(value, name, default)

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        if self.rules_error is not None:
            raise self.rules_error
        return AssetTradingRules(1.0, 1.0, 10.0)

    def _has_open_order(self, symbol: str) -> bool:
        if self.lookup_error is not None:
            raise self.lookup_error
        return False

    def _submit_with_recovery(self, request: object, client_order_id: str) -> object:
        self.submits.append(request)
        return SimpleNamespace(id="broker-1")


def _buy(client_order_id: str = "c-1") -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id=client_order_id,
        decision_id="d-1",
        symbol="AAPL",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=5.0,
        reference_price=100.0,
        decision_timestamp=_DAY,
        strategy_name="fixed_target",
        strategy_version="1.0.0",
    )


@pytest.mark.parametrize(
    "broker",
    [
        _OrderBroker(lookup_error=ConnectionError("open orders unreachable")),
        _OrderBroker(rules_error=ConnectionError("asset lookup unreachable")),
    ],
)
def test_pre_send_read_failure_is_not_sent_not_ambiguous(broker: _OrderBroker) -> None:
    service = OrderService(broker)

    with pytest.raises(DefiniteOrderRejection):
        service.submit_portfolio_order(_buy())
    assert broker.submits == []


# C2-8-1: a non-tradable asset read is re-checked within a minute, while a
# tradable one is kept for the full day.
class _AssetClient:
    def __init__(self, tradable: bool) -> None:
        self.tradable = tradable
        self.calls = 0

    def get_asset(self, symbol: str) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(symbol=symbol, tradable=self.tradable)


def test_non_tradable_asset_is_reread_after_a_minute_and_tradable_kept_a_day() -> None:
    client = _AssetClient(tradable=False)
    adapter = AlpacaClient(client=client)
    now = [1000.0]
    adapter._clock = lambda: now[0]

    adapter._asset("XYZ")
    now[0] += 30.0
    adapter._asset("XYZ")
    assert client.calls == 1

    now[0] += 31.0
    client.tradable = True
    assert adapter._asset("XYZ").tradable is True
    assert client.calls == 2

    now[0] += 60.0 * 60.0
    adapter._asset("XYZ")
    assert client.calls == 2

    now[0] += 24 * 60 * 60.0
    adapter._asset("XYZ")
    assert client.calls == 3


# P2-5-6: the broker accepted the SELL, so a failed "submitted" write is not a
# failed submit. The call returns normally and the row is left for the
# client-id lookup to resolve, not booked as not sent.
def test_accepted_order_survives_a_failed_submitted_write(tmp_path, captured) -> None:
    broker = _Broker([_position("AAPL", 400)])
    runtime, repository = _runtime(tmp_path, broker)
    real_update = repository.update_order

    def failing_update(client_order_id: str, status: str, broker_order_id: str = "") -> None:
        if status == "submitted":
            raise RuntimeError("database is locked")
        real_update(client_order_id, status, broker_order_id)

    repository.update_order = failing_update  # type: ignore[method-assign]
    result = runtime.process_closed_bars(_bars(_DAY))

    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.SELL] == ["AAPL"]
    assert len(result.submitted_order_ids) == 1
    sell_rows = repository.orders_for_decision(result.decision_id, side="sell")
    assert [str(row["status"]) for row in sell_rows] == ["submitting"]
