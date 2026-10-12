"""Reconciliation closes aged portfolio orders the broker never accepted."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution import alpaca_reconciliation
from parayabuck.execution.alpaca_reconciliation import UNKNOWN_ORDER_CANCEL_AGE_SECONDS, ReconciliationService
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest, PortfolioOrderSide
from parayabuck.monitoring import alerts
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import REBALANCE_PHASE_COMPLETE
from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository
from parayabuck.runtime import EngineRuntime

_DAY = datetime(2025, 1, 1, tzinfo=UTC)


class _Strategy:
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self.targets = targets
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
            weights=tuple(TargetWeight(symbol, weight) for symbol, weight in self.targets.items()),
            cash_weight=1.0 - sum(self.targets.values()),
            universe_version="test",
        )


class _TradingBroker:
    """Portfolio-runtime broker whose submits the broker definitively rejects."""

    def __init__(self, positions: tuple[PositionSnapshot, ...] = ()) -> None:
        self.positions = positions

    def account(self) -> AccountSnapshot:
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(100_000.0 - value, 100_000.0, 100_000.0, 0.0, self.positions)

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(1.0, 1.0, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        raise ValueError("Alpaca rejected the order")


def _bars(symbols: tuple[str, ...], day: datetime) -> list[Bar]:
    return [Bar(day, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in symbols]


def _runtime(tmp_path: Path, targets: dict[str, float], broker: _TradingBroker):
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = PortfolioRuntime(
        _Strategy(targets),
        tuple(sorted(targets)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
    )
    return runtime, repository


def _rejected_direct_buy(tmp_path: Path):
    """A pure-BUY decision whose submit Alpaca rejected (never accepted)."""
    runtime, repository = _runtime(tmp_path, {"AAPL": 0.15}, _TradingBroker())
    with pytest.raises(ValueError):
        runtime.process_closed_bars(_bars(("AAPL",), _DAY))
    [order] = repository.open_orders("acct")
    assert order.status == "reconciliation_required"
    return runtime, repository, order.client_order_id


def _age(repository: PortfolioStateRepository, seconds: float) -> None:
    moment = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    with repository.store.lock, repository.store.connection:
        repository.store.connection.execute(
            """UPDATE portfolio_orders SET created_at = ?,
               submit_call_started_at = ?, submit_call_returned_at = ?""",
            (moment, moment, moment),
        )


def _status(repository: PortfolioStateRepository, client_order_id: str) -> str:
    row = repository.store.connection.execute(
        "SELECT status FROM portfolio_orders WHERE client_order_id = ?", (client_order_id,)
    ).fetchone()
    return str(row[0])


class _NotFound(Exception):
    status_code = 404


def _reconcile_broker(repository: PortfolioStateRepository, *, known: Any = None, lookup_error: bool = False) -> Any:
    """Reconciliation-side broker: flat account, the repository as the local ledger."""

    def get_order_by_client_id(client_order_id: str) -> Any:
        raise _NotFound(client_order_id)

    def fetch(client_order_id: str, broker_order_id: str) -> Any:
        if lookup_error:
            raise TimeoutError("lookup failed")
        return known

    def recovered(
        client_order_id: str,
        broker_order_id: str,
        filled_qty: float,
        avg_price: float | None,
        occurred_at: datetime,
        activity_id: str,
        fee: float | None,
        *,
        status: str = "filled",
    ) -> None:
        assert repository.apply_trade_update(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            status=status,
            cumulative_filled_quantity=filled_qty,
            average_fill_price=avg_price,
            occurred_at=occurred_at,
            activity_id=activity_id,
            fee=fee,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            source="recovered_submission",
        )

    return SimpleNamespace(
        account=lambda: AccountSnapshot(cash=100_000, equity=100_000, peak_equity=100_000, daily_pnl=0, positions=()),
        account_id="acct",
        _client=SimpleNamespace(
            get_account=lambda: SimpleNamespace(id="acct"), get_order_by_client_id=get_order_by_client_id
        ),
        _account_id="acct",
        _field=lambda value, name, default=None: getattr(value, name, default),
        _normalized_field=lambda value, name: str(getattr(value, name)),
        _fetch_broker_order=fetch,
        _get_open_orders=lambda _request: (known,) if known is not None else (),
        portfolio_open_orders=lambda: repository.open_orders("acct"),
        portfolio_owned_quantities=lambda: repository.owned_quantities("acct"),
        portfolio_fill_recovered=recovered,
    )


@pytest.fixture
def incidents(monkeypatch) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    def emit(kind: str, *, message: str, **fields: object) -> None:
        if fields.get("category") == "unknown_order_canceled":
            captured.append({"kind": kind, **fields})

    monkeypatch.setattr(alerts, "emit", emit)
    return captured


def _engine(tmp_path: Path, broker: Any) -> EngineRuntime:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.broker = SimpleNamespace(account_id="acct", reconcile=lambda: ReconciliationService(broker).reconcile())  # type: ignore[attr-defined]
    engine.ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))  # type: ignore[attr-defined]
    engine._reconcile_lock = threading.Lock()
    engine._reconcile_failed = False
    engine._circuit_breaker_reason = None
    engine._last_reconciliation_at = None
    engine._last_account_at = None
    engine._generic_reconcile_failures = 0
    engine._state = "running"
    engine._state_lock = threading.RLock()
    return engine


def test_aged_rejected_order_is_canceled_and_the_breaker_clears(tmp_path, incidents) -> None:
    _runtime_, repository, client_order_id = _rejected_direct_buy(tmp_path)
    engine = _engine(tmp_path, _reconcile_broker(repository))

    # Within the age gate: unchanged, still drift, the breaker is raised.
    engine._reconcile_safely()
    assert _status(repository, client_order_id) == "reconciliation_required"
    assert engine._circuit_breaker_reason == "reconciliation_failed"

    _age(repository, UNKNOWN_ORDER_CANCEL_AGE_SECONDS + 60)
    engine._reconcile_safely()
    assert _status(repository, client_order_id) == "canceled"
    assert engine._circuit_breaker_reason is None
    assert engine._reconcile_failed is False
    assert repository.open_orders("acct") == ()
    assert repository.owned_quantities("acct") == {}
    engine._reconcile_safely()
    assert [item["client_order_id"] for item in incidents] == [client_order_id]
    engine.ledger.close()


def test_order_within_the_age_gate_stays_drift(tmp_path, incidents) -> None:
    _runtime_, repository, client_order_id = _rejected_direct_buy(tmp_path)
    _age(repository, UNKNOWN_ORDER_CANCEL_AGE_SECONDS - 60)
    report = ReconciliationService(_reconcile_broker(repository)).reconcile()
    assert report["MISSING_BROKER_ORDER"] == [client_order_id]
    assert _status(repository, client_order_id) == "reconciliation_required"
    assert incidents == []


def test_lookup_error_leaves_the_order_unchanged(tmp_path, incidents) -> None:
    _runtime_, repository, client_order_id = _rejected_direct_buy(tmp_path)
    _age(repository, UNKNOWN_ORDER_CANCEL_AGE_SECONDS + 60)
    with pytest.raises(TimeoutError, match="lookup failed"):
        ReconciliationService(_reconcile_broker(repository, lookup_error=True)).reconcile()
    assert _status(repository, client_order_id) == "reconciliation_required"
    assert incidents == []


def test_lookup_without_a_client_id_query_proves_nothing(tmp_path, incidents) -> None:
    _runtime_, repository, client_order_id = _rejected_direct_buy(tmp_path)
    _age(repository, UNKNOWN_ORDER_CANCEL_AGE_SECONDS + 60)
    broker = _reconcile_broker(repository)
    broker._client = SimpleNamespace(get_account=lambda: SimpleNamespace(id="acct"))
    report = ReconciliationService(broker).reconcile()
    assert report["MISSING_BROKER_ORDER"] == [client_order_id]
    assert _status(repository, client_order_id) == "reconciliation_required"


def test_broker_known_order_is_resolved_not_canceled(tmp_path, incidents) -> None:
    _runtime_, repository, client_order_id = _rejected_direct_buy(tmp_path)
    _age(repository, UNKNOWN_ORDER_CANCEL_AGE_SECONDS + 60)
    known = SimpleNamespace(
        id="broker-1",
        client_order_id=client_order_id,
        symbol="AAPL",
        side="buy",
        status="new",
        filled_qty=0.0,
        filled_avg_price=None,
        qty=150.0,
        updated_at=datetime.now(UTC),
    )
    report = ReconciliationService(_reconcile_broker(repository, known=known)).reconcile()
    assert not any(report.values())
    assert _status(repository, client_order_id) == "new"
    assert incidents == []


def test_blocked_rebalance_closes_after_its_unknown_sell_is_canceled(tmp_path, incidents) -> None:
    position = PositionSnapshot(
        "AAPL", 400, 100.0, 100.0, Market.EQUITY, qty_available=400, ownership="managed", managed_qty=400
    )
    trading = _TradingBroker((position,))
    runtime, repository = _runtime(tmp_path, {"AAPL": 0.15, "MSFT": 0.20}, trading)
    with pytest.raises(ValueError):
        runtime.process_closed_bars(_bars(("AAPL", "MSFT"), _DAY))
    [sell] = repository.open_orders("acct")
    assert (sell.side, sell.status) == (PortfolioOrderSide.SELL.value, "reconciliation_required")
    blocked = runtime.advance_rebalance()
    assert blocked is not None and blocked.status == "rebalance_blocked"
    owned_before = repository.owned_quantities("acct")

    _age(repository, UNKNOWN_ORDER_CANCEL_AGE_SECONDS + 60)
    report = ReconciliationService(_reconcile_broker(repository)).reconcile()
    assert report["MISSING_BROKER_ORDER"] == []
    assert _status(repository, sell.client_order_id) == "canceled"
    assert repository.owned_quantities("acct") == owned_before

    # The existing BLOCKED close rule now applies: every order is terminal.
    trading.submit_portfolio_order = lambda order: "broker-" + order.client_order_id  # type: ignore[method-assign]
    runtime.process_closed_bars(_bars(("AAPL", "MSFT"), _DAY + timedelta(days=1)), force_scheduled=True)
    closed = repository.store.connection.execute(
        "SELECT phase, status FROM portfolio_rebalance_executions WHERE decision_id = ?",
        (blocked.decision_id,),
    ).fetchone()
    assert tuple(closed) == (REBALANCE_PHASE_COMPLETE, "closed_unresolved")


def test_age_gate_is_far_beyond_any_submit_call() -> None:
    assert alpaca_reconciliation.UNKNOWN_ORDER_CANCEL_AGE_SECONDS >= 600
