"""A final portfolio order status and its fill price never regress."""

from __future__ import annotations

from datetime import UTC, datetime

from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository

_AT = datetime(2026, 9, 4, 15, tzinfo=UTC)


def _submitted_order(tmp_path) -> tuple[PortfolioStateRepository, PortfolioOrderRequest]:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    target = TargetPortfolio("monotonic", "1", _AT, (), 1.0)
    session = repo.begin_session("acct", "digest", "paper")
    decision_id = repo.record_decision_with_orders(
        session, "acct", "digest", target, RiskOverlayDecision(target, False, (), 0.0, 0.0), Market.EQUITY, ()
    )
    order = PortfolioOrderRequest(
        client_order_id="monotonic-buy",
        decision_id=decision_id,
        symbol="QLD",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=10.0,
        reference_price=100.0,
        decision_timestamp=_AT,
        strategy_name="monotonic",
        strategy_version="1",
    )
    repo.record_order(order, "submitting")
    return repo, order


def _update(repo: PortfolioStateRepository, status: str, quantity: float, price: float | None, activity: str) -> None:
    repo.apply_trade_update(
        client_order_id="monotonic-buy",
        broker_order_id="broker-1",
        status=status,
        cumulative_filled_quantity=quantity,
        average_fill_price=price,
        occurred_at=_AT,
        activity_id=activity,
        fee=None,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _row(repo: PortfolioStateRepository) -> dict[str, object]:
    decision_id = repo.store.connection.execute(
        "SELECT decision_id FROM portfolio_orders WHERE client_order_id = 'monotonic-buy'"
    ).fetchone()[0]
    (row,) = repo.orders_for_decision(decision_id)
    return row


def test_submit_ack_after_stream_fill_keeps_order_filled(tmp_path) -> None:
    # The stream fill can be applied before the submit path records its ack.
    repo, order = _submitted_order(tmp_path)
    _update(repo, "fill", 10.0, 101.0, "fill-1")

    repo.update_order(order.client_order_id, "submitted", "broker-1")

    assert _row(repo)["status"] == "fill"
    assert repo.open_orders("acct") == ()


def test_stale_working_event_does_not_reopen_filled_order(tmp_path) -> None:
    repo, _ = _submitted_order(tmp_path)
    _update(repo, "fill", 10.0, 101.0, "fill-1")

    _update(repo, "partial_fill", 4.0, 100.5, "partial-late")

    row = _row(repo)
    assert row["status"] == "fill"
    assert row["cumulative_filled_quantity"] == 10.0
    assert repo.open_orders("acct") == ()


def test_working_and_terminal_transitions_still_apply(tmp_path) -> None:
    repo, order = _submitted_order(tmp_path)
    repo.update_order(order.client_order_id, "submitted", "broker-1")
    _update(repo, "partial_fill", 4.0, 100.5, "partial-1")
    assert _row(repo)["status"] == "partial_fill"
    _update(repo, "canceled", 4.0, 100.5, "cancel-1")
    assert _row(repo)["status"] == "canceled"


def test_done_for_day_order_can_resume_working(tmp_path) -> None:
    # Alpaca resumes a done_for_day order on the next session.
    repo, _ = _submitted_order(tmp_path)
    _update(repo, "done_for_day", 0.0, None, "dfd-1")
    _update(repo, "new", 0.0, None, "new-1")
    assert _row(repo)["status"] == "new"


def test_update_without_price_keeps_persisted_fill_price(tmp_path) -> None:
    repo, _ = _submitted_order(tmp_path)
    _update(repo, "partial_fill", 4.0, 100.5, "partial-1")

    _update(repo, "pending_cancel", 4.0, None, "pending-cancel-1")

    row = _row(repo)
    assert row["status"] == "pending_cancel"
    assert row["average_fill_price"] == 100.5


def test_status_update_without_broker_id_keeps_recorded_broker_id(tmp_path) -> None:
    repo, order = _submitted_order(tmp_path)
    repo.update_order(order.client_order_id, "submitted", "broker-1")

    repo.update_order(order.client_order_id, "new")

    row = _row(repo)
    assert row["status"] == "new"
    assert row["broker_order_id"] == "broker-1"
