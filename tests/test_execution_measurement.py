"""Offline contracts for append-only PAPER execution measurement."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from parayabuck.core.models import Market
from parayabuck.execution import telemetry
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream, TradeUpdate
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository


def test_stream_preserves_missing_fee_as_unknown_with_separate_receive_time() -> None:
    captured: list[TradeUpdate] = []

    async def handler(update: TradeUpdate) -> None:
        captured.append(update)

    stream = object.__new__(AlpacaTradingUpdateStream)
    stream._handler = handler
    broker_at = datetime(2026, 9, 4, tzinfo=UTC)
    asyncio.run(
        stream._on_update(
            {
                "event": "fill",
                "timestamp": broker_at,
                "order": {
                    "client_order_id": "measurement-buy",
                    "id": "broker-1",
                    "symbol": "AAPL",
                    "asset_class": "us_equity",
                    "qty": "0.5",
                    "filled_qty": "0.5",
                    "filled_avg_price": "100",
                },
            }
        )
    )
    assert captured[0].fee is None
    assert captured[0].fee_observed is False
    assert captured[0].source == "stream"
    assert captured[0].timestamp == broker_at
    assert captured[0].received_at is not None


def _order(repo: PortfolioStateRepository) -> tuple[str, PortfolioOrderRequest]:
    timestamp = datetime(2026, 9, 4, tzinfo=UTC)
    target = TargetPortfolio("measurement", "1", timestamp, (), 1.0)
    risk = RiskOverlayDecision(target, False, (), 0.0, 0.0)
    session = repo.begin_session("acct", "digest", "paper")
    decision_id = repo.record_decision(session, "acct", "digest", target, risk, Market.EQUITY)
    order = PortfolioOrderRequest(
        client_order_id="measurement-buy",
        decision_id=decision_id,
        symbol="AAPL",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=0.5,
        reference_price=100.0,
        decision_timestamp=timestamp,
        strategy_name="measurement",
        strategy_version="1",
        risk_reducing=False,
    )
    repo.record_order(order, "submitting")
    return decision_id, order


def test_raw_gross_execution_matches_ownership_and_reconstructs_vwap(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    repo = PortfolioStateRepository(path)
    _, order = _order(repo)
    timestamp = order.decision_timestamp

    repo.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-1",
        status="partial_fill",
        cumulative_filled_quantity=0.2,
        average_fill_price=100.0,
        occurred_at=timestamp,
        activity_id="stream-1",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        source="stream",
        received_at=timestamp + timedelta(seconds=1),
        fee_observed=True,
    )
    repo.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-1",
        status="fill",
        cumulative_filled_quantity=0.5,
        average_fill_price=112.0,
        occurred_at=timestamp + timedelta(seconds=2),
        activity_id="stream-2",
        fee=None,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        source="stream",
        received_at=timestamp + timedelta(seconds=3),
        fee_observed=False,
    )

    row = repo.store.connection.execute(
        "SELECT cumulative_filled_quantity FROM portfolio_orders WHERE client_order_id=?",
        (order.client_order_id,),
    ).fetchone()
    assert row == (0.5,)  # ownership behavior remains authoritative and unchanged
    measurement = telemetry.execution_measurement(path)
    observed = measurement["orders"][0]
    assert observed["gross_cumulative_executed_quantity"] == 0.5
    assert observed["gross_fill_ratio"] == 1.0
    assert observed["net_owned_quantity"] == 0.5
    assert observed["net_ownership_ratio"] == 1.0
    assert observed["fee_status"] == "unknown"
    assert observed["comparison_eligibility"] == {
        "final_gross_execution_quantity": True,
        "final_vwap": True,
        "price_only": True,
        "total_cost": False,
    }
    assert [item["incremental_price"] for item in observed["incremental_fills"]] == [100.0, 120.0]


def test_raw_event_source_identity_is_idempotent_and_invalid_sequence_is_retained(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    repo = PortfolioStateRepository(path)
    _, order = _order(repo)
    timestamp = order.decision_timestamp
    kwargs = dict(
        client_order_id=order.client_order_id,
        broker_order_id="broker-1",
        status="fill",
        average_fill_price=100.0,
        occurred_at=timestamp,
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        source="stream",
        received_at=timestamp,
        fee_observed=True,
    )
    repo.apply_trade_update(cumulative_filled_quantity=0.5, activity_id="same-event", **kwargs)
    repo.apply_trade_update(cumulative_filled_quantity=0.5, activity_id="same-event", **kwargs)
    repo.apply_trade_update(cumulative_filled_quantity=0.3, activity_id="late-event", **kwargs)

    events = repo.store.connection.execute(
        "SELECT dedup_identity, measurement_quality FROM portfolio_execution_events ORDER BY id"
    ).fetchall()
    assert events == [
        ("same-event", "first_fill"),
        ("late-event", "out_of_order_or_decreasing_cumulative_quantity"),
    ]
    # An invalid raw sequence is evidence, not a negative ownership fill.
    row = repo.store.connection.execute(
        "SELECT cumulative_filled_quantity FROM portfolio_orders WHERE client_order_id=?",
        (order.client_order_id,),
    ).fetchone()
    assert row == (0.5,)
    measurement = telemetry.execution_measurement(path)["orders"][0]
    assert measurement["comparison_eligibility"] == {
        "final_gross_execution_quantity": False,
        "final_vwap": False,
        "price_only": False,
        "total_cost": False,
    }
    assert measurement["comparison_exclusion_reasons"] == [
        "raw_execution_quality_excluded:out_of_order_or_decreasing_cumulative_quantity"
    ]


def test_adopt_order_quantity_only_raises_the_local_row(tmp_path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _order(repo)

    def quantity() -> float:
        row = repo.store.connection.execute(
            "SELECT quantity FROM portfolio_orders WHERE client_order_id = 'measurement-buy'"
        ).fetchone()
        return float(row[0])

    repo.adopt_order_quantity("measurement-buy", 0.75)
    assert quantity() == 0.75
    repo.adopt_order_quantity("measurement-buy", 0.25)
    assert quantity() == 0.75
    try:
        repo.adopt_order_quantity("unknown-order", 1.0)
    except RuntimeError as exc:
        assert "unknown portfolio order" in str(exc)
    else:  # pragma: no cover - the assertion above must run
        raise AssertionError("unknown order must be rejected")
