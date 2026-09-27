"""Recovery of blocked two-phase rebalances and stuck deferred BUY alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest, PortfolioOrderSide
from parayabuck.monitoring import alerts
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import (
    REBALANCE_PHASE_BLOCKED,
    REBALANCE_PHASE_BUY_PENDING,
    REBALANCE_PHASE_COMPLETE,
)
from parayabuck.portfolio.runtime_service import (
    REBALANCE_STATUS_CLOSED_UNRESOLVED,
    STUCK_DEFERRED_BUY_ALERT_SECONDS,
    PortfolioRuntime,
    PortfolioStateRepository,
)

_DAY = datetime(2025, 1, 1, tzinfo=UTC)
_TARGETS = {"AAPL": 0.15, "MSFT": 0.20}


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


class _Broker:
    def __init__(self) -> None:
        self.positions = [_position("AAPL", 400)]
        self.open_symbols: set[str] = set()
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(100_000.0 - value, 100_000.0, 100_000.0, 0.0, tuple(self.positions))

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(symbol, market, 1.0, 1.0, 0.01, 10.0)

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


def _bars(day: datetime) -> list[Bar]:
    return [Bar(day, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in _TARGETS]


def _settle(repository: PortfolioStateRepository, order: PortfolioOrderRequest, activity_id: str) -> None:
    repository.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-" + order.client_order_id,
        status="fill",
        cumulative_filled_quantity=order.quantity,
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id=activity_id,
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


@pytest.fixture
def raised(monkeypatch) -> list[dict[str, object]]:
    """Operator alerts raised by the rebalance recovery paths under test."""
    captured: list[dict[str, object]] = []

    def emit(kind: str, *, message: str, **fields: object) -> None:
        if fields.get("category") in {"rebalance_unrecoverable", "stuck_deferred_buy"}:
            captured.append({"kind": kind, **fields})

    monkeypatch.setattr(alerts, "emit", emit)
    return captured


def _blocked_rebalance(tmp_path):
    """Submit the SELL leg, then lose its outcome so the rebalance blocks."""
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
    )
    first = runtime.process_closed_bars(_bars(_DAY))
    assert first.status == "sell_phase_submitted"
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    repository.update_order(sell.client_order_id, "reconciliation_required")
    assert runtime.advance_rebalance().status == "rebalance_blocked"
    return runtime, repository, broker, first.decision_id, sell


def _phase(repository: PortfolioStateRepository, decision_id: str) -> tuple[str, str]:
    row = repository.store.connection.execute(
        "SELECT phase, status FROM portfolio_rebalance_executions WHERE decision_id = ?", (decision_id,)
    ).fetchone()
    return str(row[0]), str(row[1])


def test_blocked_rebalance_stays_blocked_while_a_sell_is_unsettled(tmp_path, raised) -> None:
    runtime, repository, broker, decision_id, _ = _blocked_rebalance(tmp_path)

    result = runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)

    assert result.status == "rebalance_blocked"
    assert _phase(repository, decision_id)[0] == REBALANCE_PHASE_BLOCKED
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert raised == []


def test_settled_blocked_rebalance_closes_without_its_deferred_buys(tmp_path, raised) -> None:
    runtime, repository, broker, decision_id, sell = _blocked_rebalance(tmp_path)
    # Reconciliation proves the SELL filled.
    _settle(repository, sell, "sell-recovered")
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")

    result = runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)

    assert _phase(repository, decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)
    # No order of the blocked decision besides its SELL ever reached the broker.
    assert [order for order in broker.submitted if order.decision_id == decision_id] == [sell]
    # The same cycle plans normally from a fresh target under a new decision.
    assert result.decision_id not in {None, decision_id}
    assert result.orders
    assert {order.decision_id for order in broker.submitted[1:]} == {result.decision_id}
    assert raised == []


def test_unrecoverable_blocked_rebalance_stays_blocked_and_alerts_once(tmp_path, raised) -> None:
    runtime, repository, broker, decision_id, sell = _blocked_rebalance(tmp_path)
    _settle(repository, sell, "sell-recovered")
    with repository.store.lock, repository.store.connection:
        repository.store.connection.execute(
            "UPDATE portfolio_decisions SET risk_json = '{}' WHERE decision_id = ?", (decision_id,)
        )

    first = runtime.process_closed_bars(_bars(_DAY + timedelta(days=1)), force_scheduled=True)
    second = runtime.process_closed_bars(_bars(_DAY + timedelta(days=2)), force_scheduled=True)

    assert (first.status, second.status) == ("rebalance_blocked", "rebalance_blocked")
    assert _phase(repository, decision_id)[0] == REBALANCE_PHASE_BLOCKED
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert [(item["kind"], item["category"], item["decision_id"]) for item in raised] == [
        ("paper_incident", "rebalance_unrecoverable", decision_id)
    ]


def test_stuck_deferred_buy_alerts_once_after_threshold_without_phase_change(tmp_path, raised) -> None:
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
    )
    first = runtime.process_closed_bars(_bars(_DAY))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _settle(repository, sell, "sell-fill")
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")
    resumed = runtime.advance_rebalance()
    assert resumed.status == "buy_phase_submitted"
    buy = next(order for order in resumed.orders if order.side == PortfolioOrderSide.BUY)
    repository.update_order(buy.client_order_id, "reconciliation_required")

    # Fresh ambiguity: within the reconciliation window, no alert.
    assert runtime.advance_rebalance() is None
    assert raised == []

    stale = datetime.now(UTC) - timedelta(seconds=STUCK_DEFERRED_BUY_ALERT_SECONDS + 60)
    with repository.store.lock, repository.store.connection:
        repository.store.connection.execute(
            "UPDATE portfolio_rebalance_executions SET updated_at = ? WHERE decision_id = ?",
            (stale.isoformat(), first.decision_id),
        )
    assert runtime.advance_rebalance() is None
    assert runtime.advance_rebalance() is None

    assert [(item["category"], item["client_order_ids"]) for item in raised] == [
        ("stuck_deferred_buy", buy.client_order_id)
    ]
    assert _phase(repository, first.decision_id)[0] == REBALANCE_PHASE_BUY_PENDING

    _settle(repository, buy, "buy-recovered")
    assert runtime.advance_rebalance().status == "rebalance_complete"
    assert len(raised) == 1


def test_deferred_buy_phase_records_admission_evidence(tmp_path, monkeypatch) -> None:
    """The deferred BUY rows exist before the admission observation that
    references them, so the evidence is kept and no false incident fires."""
    incidents: list[str] = []
    monkeypatch.setattr(alerts, "emit", lambda kind, *, message, **fields: incidents.append(message))
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(),
        tuple(sorted(_TARGETS)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
    )
    first = runtime.process_closed_bars(_bars(_DAY))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _settle(repository, sell, "sell-fill")
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")

    resumed = runtime.advance_rebalance()

    assert resumed.status == "buy_phase_submitted"
    buy_ids = sorted(order.client_order_id for order in resumed.orders)
    observed = repository.store.connection.execute(
        "SELECT client_order_id FROM portfolio_buy_admission_observations WHERE decision_id = ?",
        (first.decision_id,),
    ).fetchall()
    assert sorted(str(row[0]) for row in observed) == buy_ids
    assert "Final BUY admission evidence unavailable" not in incidents
    assert [order.client_order_id for order in broker.submitted[1:]] == [
        order.client_order_id for order in resumed.orders
    ]
