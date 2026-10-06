"""Client order ids carry a per-ledger salt so foreign orders are never adopted.

Incident: a fresh DB (or a second process on the same Alpaca key) derived the
same deterministic ``parayabuck-<decision>-<symbol>-<side>`` id as an order
another ledger had already filled, and recovery adopted that fill as its own.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.alpaca_reconciliation import UNKNOWN_ORDER_CANCEL_AGE_SECONDS, ReconciliationService
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    RiskOverlayDecision,
)
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import _decision_id
from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository

_DAY = datetime(2025, 1, 1, tzinfo=UTC)


class _Strategy:
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1
    parameters = {"targets": {"QLD": 0.15}}

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return (("targets", "QLD=0.15"),)

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            self.name,
            self.version,
            context.decision_timestamp,
            (TargetWeight("QLD", 0.15),),
            0.85,
            universe_version="test",
        )


class _Broker:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.submitted: list[str] = []

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(100_000.0, 100_000.0, 100_000.0, 0.0)

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(symbol, market, 1.0, 1.0, 0.01, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        if self.reject:
            raise ValueError("Alpaca rejected the order")
        self.submitted.append(order.client_order_id)
        return "broker-" + order.client_order_id


def _runtime(path: Path, broker: _Broker) -> PortfolioRuntime:
    return PortfolioRuntime(
        _Strategy(),
        ("QLD",),
        broker,
        PortfolioStateRepository(path),
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
    )


def _bars() -> list[Bar]:
    return [Bar(_DAY, "QLD", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY)]


def _legacy_id(decision_id: str) -> str:
    return f"parayabuck-{decision_id}-QLD-buy"


def test_same_ledger_restart_reuses_salt_and_stays_idempotent(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    broker = _Broker()
    first = _runtime(path, broker)
    result = first.process_closed_bars(_bars())
    assert result.status == "submitted"
    [client_order_id] = broker.submitted
    salt = first.repository.client_order_salt(result.decision_id)
    assert client_order_id == f"parayabuck-{result.decision_id}-{salt}-QLD-buy"
    first.close()

    restarted = _runtime(path, broker)
    # The salt is persisted once per ledger, so a replayed plan of the same
    # decision regenerates exactly the persisted id.
    assert restarted.repository.client_order_salt(result.decision_id) == salt
    assert restarted.process_closed_bars(_bars()).status == "duplicate_decision"
    assert broker.submitted == [client_order_id]


def test_ledgers_get_distinct_salts(tmp_path) -> None:
    one = PortfolioStateRepository(tmp_path / "one.sqlite3")
    two = PortfolioStateRepository(tmp_path / "two.sqlite3")
    assert one.client_order_salt("d") != two.client_order_salt("d")


def test_decision_with_legacy_ids_keeps_legacy_form(tmp_path) -> None:
    """An in-flight pre-salt decision re-plans with its persisted ids."""
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    target = TargetPortfolio("fixed", "1", _DAY, (TargetWeight("QLD", 0.15),), 0.85, universe_version="t")
    risk = RiskOverlayDecision(target, False, (), 0.0, 0.15)
    decision_id = _decision_id("acct", "digest", _DAY, Market.EQUITY)
    legacy = PortfolioOrderRequest(
        _legacy_id(decision_id),
        decision_id,
        "QLD",
        Market.EQUITY,
        PortfolioOrderSide.BUY,
        1.0,
        100.0,
        _DAY,
        "fixed",
        "1",
        False,
    )
    assert (
        repository.record_decision_with_orders(session, "acct", "digest", target, risk, Market.EQUITY, (legacy,))
        == decision_id
    )
    assert repository.client_order_salt(decision_id) == ""
    # A decision without persisted legacy rows uses the ledger salt.
    assert repository.client_order_salt("b" * 24)


def _reconcile_broker(repository: PortfolioStateRepository, foreign: Any) -> Any:
    """Flat account whose only OPEN order is another process's legacy-id order."""

    class _NotFound(Exception):
        status_code = 404

    def get_order_by_client_id(client_order_id: str) -> Any:
        raise _NotFound(client_order_id)

    def fetch(client_order_id: str, _broker_order_id: str) -> Any:
        return foreign if client_order_id == foreign.client_order_id else None

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
        _get_open_orders=lambda _request: (foreign,),
        portfolio_open_orders=lambda: repository.open_orders("acct"),
        portfolio_owned_quantities=lambda: repository.owned_quantities("acct"),
        portfolio_fill_recovered=recovered,
    )


def test_fresh_ledger_does_not_adopt_foreign_legacy_id_order(tmp_path) -> None:
    runtime = _runtime(tmp_path / "fresh.sqlite3", _Broker(reject=True))
    repository = runtime.repository
    with pytest.raises(ValueError):
        runtime.process_closed_bars(_bars())
    [own] = repository.open_orders("acct")
    row = repository.store.connection.execute(
        "SELECT decision_id FROM portfolio_orders WHERE client_order_id = ?", (own.client_order_id,)
    ).fetchone()
    foreign_id = _legacy_id(str(row[0]))
    # The id another process derived for the very same decision differs.
    assert own.client_order_id != foreign_id
    foreign = SimpleNamespace(
        id="foreign-broker-1",
        client_order_id=foreign_id,
        symbol="QLD",
        side="buy",
        status="partially_filled",
        filled_qty=50.0,
        filled_avg_price=100.0,
        qty=150.0,
        updated_at=datetime.now(UTC),
    )
    moment = (datetime.now(UTC) - timedelta(seconds=UNKNOWN_ORDER_CANCEL_AGE_SECONDS + 60)).isoformat()
    with repository.store.lock, repository.store.connection:
        repository.store.connection.execute(
            "UPDATE portfolio_orders SET created_at = ?, submit_call_started_at = ?, submit_call_returned_at = ?",
            (moment, moment, moment),
        )

    report = ReconciliationService(_reconcile_broker(repository, foreign)).reconcile()

    assert report["UNKNOWN_ORDER"] == [foreign_id]
    assert repository.owned_quantities("acct") == {}
    assert repository.store.connection.execute("SELECT COUNT(*) FROM portfolio_fills").fetchone()[0] == 0
