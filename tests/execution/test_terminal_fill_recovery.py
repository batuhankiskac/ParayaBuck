"""Recovery of fills made by orders that then died, and FILL-activity backfill rows.

Synthetic ``__new__`` brokers over ``tmp_path`` SQLite state: no network, no
credentials, no production paths.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution import telemetry
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository

_DECISION_AT = datetime(2026, 1, 2, 14, 0, tzinfo=UTC)


def _repository(tmp_path: Path) -> tuple[PortfolioStateRepository, str]:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session_id = repo.begin_session("acct", "digest", "paper")
    target = TargetPortfolio(
        weights=(), cash_weight=1.0, decision_timestamp=_DECISION_AT, strategy_name="eq", strategy_version="1"
    )
    decision_id = repo.record_decision(
        session_id, "acct", "digest", target, RiskOverlayDecision(target, False, (), 0.0, 0.0), Market.EQUITY
    )
    return repo, decision_id


def _record(repo: PortfolioStateRepository, decision_id: str, client_order_id: str, side: PortfolioOrderSide) -> None:
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id=client_order_id,
            decision_id=decision_id,
            symbol="QLD",
            market=Market.EQUITY,
            side=side,
            quantity=10.0,
            reference_price=100.0,
            decision_timestamp=_DECISION_AT,
            strategy_name="eq",
            strategy_version="1",
            risk_reducing=side == PortfolioOrderSide.SELL,
        ),
        "submitting",
    )


def _broker(repo: PortfolioStateRepository, broker_order: Any) -> AlpacaBroker:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(get_order_by_client_id=lambda _cid: broker_order)  # type: ignore[attr-defined]

    def recover(
        client_order_id: str,
        broker_order_id: str,
        qty: float,
        price: float | None,
        occurred_at: datetime,
        activity_id: str,
        fee: float | None,
        *,
        status: str = "filled",
    ) -> None:
        assert repo.apply_trade_update(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            status=status,
            cumulative_filled_quantity=qty,
            average_fill_price=price,
            occurred_at=occurred_at,
            activity_id=activity_id,
            fee=fee,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )

    broker.portfolio_fill_recovered = recover
    return broker


def _dead_partial(client_order_id: str, side: str) -> SimpleNamespace:
    # A partially filled order that then died carries no ``filled_at``.
    return SimpleNamespace(
        id="b-1",
        client_order_id=client_order_id,
        status="canceled",
        side=side,
        filled_qty="4",
        filled_avg_price="101.0",
        filled_at=None,
        updated_at="2026-01-02T15:00:00+00:00",
    )


def _fills(repo: PortfolioStateRepository, client_order_id: str) -> list[tuple[Any, ...]]:
    with repo.store.lock:
        return repo.store.connection.execute(
            "SELECT quantity, price FROM portfolio_fills WHERE client_order_id=?", (client_order_id,)
        ).fetchall()


def test_crashed_buy_that_partially_filled_then_died_books_its_fill(tmp_path: Path) -> None:
    repo, decision_id = _repository(tmp_path)
    _record(repo, decision_id, "pb-buy", PortfolioOrderSide.BUY)
    broker = _broker(repo, _dead_partial("pb-buy", "buy"))

    assert broker.resolve_portfolio_submission("pb-buy", "") == "canceled"

    assert _fills(repo, "pb-buy") == [(4.0, 101.0)]
    assert repo.owned_quantities("acct") == {"QLD": 4.0}


def test_stale_sell_that_partially_filled_then_died_books_its_fill(tmp_path: Path) -> None:
    repo, decision_id = _repository(tmp_path)
    _record(repo, decision_id, "pb-buy", PortfolioOrderSide.BUY)
    repo.apply_trade_update(
        client_order_id="pb-buy",
        broker_order_id="b-0",
        status="fill",
        cumulative_filled_quantity=10.0,
        average_fill_price=100.0,
        occurred_at=_DECISION_AT,
        activity_id="buy-fill",
        fee=None,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    _record(repo, decision_id, "pb-sell", PortfolioOrderSide.SELL)
    broker = _broker(repo, _dead_partial("pb-sell", "sell"))
    broker.portfolio_open_orders = lambda: repo.open_orders("acct")

    # The order died, so the remainder still falls through to the adjustment.
    assert broker._reconcile_stale_portfolio_ownership("QLD", Market.EQUITY) is False

    assert _fills(repo, "pb-sell") == [(4.0, 101.0)]
    assert repo.owned_quantities("acct") == {"QLD": 6.0}


def test_recovered_terminal_fill_is_not_booked_twice_by_a_later_delivery(tmp_path: Path) -> None:
    repo, decision_id = _repository(tmp_path)
    _record(repo, decision_id, "pb-buy", PortfolioOrderSide.BUY)
    broker = _broker(repo, _dead_partial("pb-buy", "buy"))

    broker.resolve_portfolio_submission("pb-buy", "")
    broker.resolve_portfolio_submission("pb-buy", "")
    # The stream later delivers the same cumulative fill under its own id.
    repo.apply_trade_update(
        client_order_id="pb-buy",
        broker_order_id="b-1",
        status="canceled",
        cumulative_filled_quantity=4.0,
        average_fill_price=101.0,
        occurred_at=datetime(2026, 1, 2, 15, 0, tzinfo=UTC),
        activity_id="stream-execution-1",
        fee=None,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )

    assert _fills(repo, "pb-buy") == [(4.0, 101.0)]
    assert repo.owned_quantities("acct") == {"QLD": 4.0}


def test_dead_order_without_fill_books_nothing(tmp_path: Path) -> None:
    repo, decision_id = _repository(tmp_path)
    _record(repo, decision_id, "pb-buy", PortfolioOrderSide.BUY)
    order = SimpleNamespace(id="b-1", status="expired", filled_qty="0", filled_avg_price=None)
    broker = _broker(repo, order)

    assert broker.resolve_portfolio_submission("pb-buy", "") == "expired"
    assert _fills(repo, "pb-buy") == []


# -- FILL-activity backfill rows ---------------------------------------------


class _ActivityAdapter:
    def __init__(self, activities: list[dict[str, str]]) -> None:
        self._activities = activities

    def request(self, _method: str, _path: str, data: dict[str, object] | None = None) -> list[dict[str, str]]:
        # One page, then the empty page that ends pagination.
        return [] if data and "page_token" in data else list(self._activities)


def _activity(activity_id: str, qty: str, price: str, at: str, *, commission: str = "0") -> dict[str, str]:
    return {
        "id": activity_id,
        "side": "sell",
        "qty": qty,
        "price": price,
        "transaction_time": at,
        "order_id": "b-sell",
        "symbol": "QLD",
        "commission": commission,
    }


def _backfill(ledger: ExecutionLedger, activities: list[dict[str, str]]) -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.__dict__["_client_adapter"] = _ActivityAdapter(activities)
    broker.ledger = ledger
    broker._account_id = "acct"
    assert broker._reconcile_fill_activity_fees() is True


def test_backfilled_portfolio_sell_row_is_terminal_quiet_and_not_open(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = tmp_path / "runtime.sqlite3"
    ledger = ExecutionLedger(str(db))
    try:
        with caplog.at_level(logging.WARNING, logger="parayabuck.execution.ledger"):
            _backfill(ledger, [_activity("a1", "5", "100", "2026-09-01T15:00:00+00:00")])
        row = ledger.connection.execute(
            "SELECT terminal_state, cumulative_filled_qty FROM orders WHERE client_order_id=?",
            ("alpaca-fill-activity-b-sell",),
        ).fetchone()
    finally:
        ledger.close()

    assert row == ("filled", 5.0)
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert telemetry.execution_metrics(db)["orders"]["open"] == 0


def test_backfill_sums_every_partial_sell_fill_and_replays_idempotently(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "runtime.sqlite3"))
    activities = [
        _activity("a1", "3", "100", "2026-09-01T15:00:00+00:00", commission="0.3"),
        _activity("a2", "2", "105", "2026-09-01T15:00:01+00:00", commission="0.2"),
    ]
    try:
        _backfill(ledger, activities)
        _backfill(ledger, activities)
        row = ledger.connection.execute(
            "SELECT cumulative_filled_qty, average_fill_price, actual_fee FROM orders WHERE client_order_id=?",
            ("alpaca-fill-activity-b-sell",),
        ).fetchone()
        fees = ledger.connection.execute("SELECT activity_id, fee FROM fee_activities ORDER BY activity_id").fetchall()
    finally:
        ledger.close()

    assert row[0] == 5.0
    assert row[1] == pytest.approx(102.0)  # (3 * 100 + 2 * 105) / 5
    assert row[2] == pytest.approx(0.5)
    assert fees == [("a1", 0.3), ("a2", 0.2)]


def test_activity_fee_already_observed_by_the_portfolio_journal_counts_once(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    repo, decision_id = _repository(tmp_path)
    _record(repo, decision_id, "pb-sell", PortfolioOrderSide.SELL)
    repo.apply_trade_update(
        client_order_id="pb-sell",
        broker_order_id="b-sell",
        status="fill",
        cumulative_filled_quantity=5.0,
        average_fill_price=100.0,
        occurred_at=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
        activity_id="stream-execution-1",
        fee=0.5,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    ledger = ExecutionLedger(str(db))
    try:
        _backfill(ledger, [_activity("a1", "5", "100", "2026-09-01T15:00:00+00:00", commission="0.5")])
    finally:
        ledger.close()

    metrics = telemetry.execution_metrics(db)
    assert metrics["fees"]["total"] == pytest.approx(0.5)
    assert telemetry.cost_calibration(db)["observed"]["total_fees"] == pytest.approx(0.5)


def test_activity_fee_is_kept_when_the_portfolio_journal_saw_no_fee(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    repo, decision_id = _repository(tmp_path)
    _record(repo, decision_id, "pb-sell", PortfolioOrderSide.SELL)
    repo.apply_trade_update(
        client_order_id="pb-sell",
        broker_order_id="b-sell",
        status="fill",
        cumulative_filled_quantity=5.0,
        average_fill_price=100.0,
        occurred_at=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
        activity_id="stream-execution-1",
        fee=None,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    ledger = ExecutionLedger(str(db))
    try:
        _backfill(ledger, [_activity("a1", "5", "100", "2026-09-01T15:00:00+00:00", commission="0.5")])
    finally:
        ledger.close()

    assert telemetry.execution_metrics(db)["fees"]["total"] == pytest.approx(0.5)
