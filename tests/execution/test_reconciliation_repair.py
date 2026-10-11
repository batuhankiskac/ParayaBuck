"""Offline restart and portfolio-order reconciliation regressions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.alpaca_reconciliation import ReconciliationResultError, ReconciliationService


class _State:
    def __init__(
        self,
        *,
        broker_status: str = "filled",
        broker_qty: float = 1.0,
        filled_qty: float | None = None,
        side: str = "buy",
    ) -> None:
        self.local_status = "submitted"
        self.local_qty = 0.0
        self.broker_status = broker_status
        self.broker_qty = broker_qty
        self.filled_qty = broker_qty if filled_qty is None and side == "buy" else (filled_qty or 0.0)
        self.side = side
        self.recoveries = 0

    def owned_qty(self) -> float:
        return self.local_qty if self.side == "buy" else max(0.0, 1.0 - self.local_qty)

    def broker(self) -> Any:
        state = self
        order = SimpleNamespace(
            id="broker-1",
            client_order_id="intent-1",
            symbol="AAPL",
            side=self.side,
            status=self.broker_status,
            filled_qty=self.filled_qty,
            filled_avg_price=100.0,
            filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        def account() -> AccountSnapshot:
            positions = (
                (
                    PositionSnapshot(
                        symbol="AAPL",
                        qty=state.broker_qty,
                        avg_price=100,
                        current_price=100,
                        market=Market.EQUITY,
                        managed_qty=min(state.owned_qty(), state.broker_qty),
                    ),
                )
                if state.broker_qty
                else ()
            )
            return AccountSnapshot(cash=0, equity=100, peak_equity=100, daily_pnl=0, positions=positions)

        def open_orders() -> tuple[Any, ...]:
            if state.local_status in {"filled", "canceled", "rejected", "expired"}:
                return ()
            return (
                SimpleNamespace(
                    client_order_id="intent-1",
                    broker_order_id="broker-1",
                    symbol="AAPL",
                    market=Market.EQUITY,
                    side=state.side,
                    quantity=1.0,
                    cumulative_filled_quantity=state.local_qty,
                ),
            )

        def recovered(
            client_order_id: str,
            broker_order_id: str,
            filled_qty: float,
            avg_price: float,
            occurred_at: datetime,
            activity_id: str,
            fee: float | None,
            *,
            status: str = "filled",
        ) -> None:
            del client_order_id, broker_order_id, avg_price, occurred_at, activity_id, fee
            state.recoveries += 1
            state.local_qty = max(state.local_qty, filled_qty)
            state.local_status = status

        return SimpleNamespace(
            account=account,
            _client=SimpleNamespace(get_account=lambda: SimpleNamespace(id="acct")),
            _account_id="acct",
            _field=lambda value, name, default=None: getattr(value, name, default),
            _normalized_field=lambda value, name: str(getattr(value, name)),
            _fetch_broker_order=lambda *_args: order,
            _get_open_orders=lambda _request: (
                (order,) if self.broker_status in {"new", "partially_filled", "pending_cancel"} else ()
            ),
            portfolio_open_orders=open_orders,
            portfolio_owned_quantities=lambda: {"AAPL": state.owned_qty()} if state.owned_qty() else {},
            portfolio_fill_recovered=recovered,
        )


def test_full_buy_fill_while_offline_reconciles_before_drift() -> None:
    state = _State()
    broker = state.broker()
    report = ReconciliationService(broker).reconcile()
    assert report["MATCH"] == ["AAPL"]
    assert not any(values for key, values in report.items() if key != "MATCH")
    assert state.local_status == "filled"
    assert state.local_qty == 1
    assert state.recoveries == 1
    ReconciliationService(broker).reconcile()
    assert state.recoveries == 1


def test_partial_buy_fill_while_offline_retains_order() -> None:
    state = _State(broker_status="partially_filled", broker_qty=0.4, filled_qty=0.4)
    broker = state.broker()
    report = ReconciliationService(broker).reconcile()
    assert report["MATCH"] == ["AAPL"]
    assert report["MISSING_BROKER_ORDER"] == []
    assert state.local_status == "partial_fill"
    assert state.local_qty == 0.4
    assert len(broker.portfolio_open_orders()) == 1


def test_sell_partial_and_full_fill_while_offline() -> None:
    partial = _State(side="sell", broker_status="partially_filled", broker_qty=0.6, filled_qty=0.4)
    report = ReconciliationService(partial.broker()).reconcile()
    assert report["MATCH"] == ["AAPL"]
    assert partial.owned_qty() == 0.6
    assert partial.local_status == "partial_fill"

    full = _State(side="sell", broker_status="filled", broker_qty=0.0, filled_qty=1.0)
    report = ReconciliationService(full.broker()).reconcile()
    assert not any(report.values())
    assert full.owned_qty() == 0.0
    assert full.local_status == "filled"


def test_cancel_reject_and_working_order_states() -> None:
    for status in ("canceled", "rejected", "new", "pending_cancel"):
        state = _State(broker_status=status, broker_qty=0.0)
        broker = state.broker()
        report = ReconciliationService(broker).reconcile()
        assert not any(report.values())
        assert state.local_status == status
        assert bool(broker.portfolio_open_orders()) == (status in {"new", "pending_cancel"})


def test_targeted_lookup_failure_blocks_reconciliation() -> None:
    broker = _State().broker()
    broker._fetch_broker_order = lambda *_args: (_ for _ in ()).throw(TimeoutError("lookup failed"))
    with pytest.raises(TimeoutError, match="lookup failed"):
        ReconciliationService(broker).reconcile()


def test_wrong_broker_order_identity_blocks_reconciliation() -> None:
    broker = _State().broker()
    broker._fetch_broker_order = lambda *_args: SimpleNamespace(id="broker-1", client_order_id="other")
    with pytest.raises(ReconciliationResultError, match="identity mismatch"):
        ReconciliationService(broker).reconcile()


def test_nonfinite_broker_fill_blocks_reconciliation() -> None:
    broker = _State().broker()
    order = broker._fetch_broker_order("intent-1", "broker-1")
    order.filled_qty = float("nan")
    with pytest.raises(ReconciliationResultError, match="Invalid broker cumulative fill"):
        ReconciliationService(broker).reconcile()


def test_local_order_provider_failure_blocks_reconciliation() -> None:
    broker = _State().broker()
    calls = 0
    original = broker.portfolio_open_orders

    def provider() -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("local order read failed")
        return original()

    broker.portfolio_open_orders = provider
    with pytest.raises(OSError, match="local order read failed"):
        ReconciliationService(broker).reconcile()


def test_broker_open_order_without_client_id_is_bucketed_unknown() -> None:
    broker = _State().broker()
    broker._get_open_orders = lambda _request: (SimpleNamespace(id="broker-1", client_order_id=""),)
    report = ReconciliationService(broker).reconcile()
    assert report["UNKNOWN_ORDER"] == ["broker-1"]


def test_unknown_position_still_reports_drift() -> None:
    state = _State(broker_status="filled")
    state.local_status = "filled"
    report = ReconciliationService(state.broker()).reconcile()
    assert report["BROKER_STALE"] == ["AAPL"]


def test_restart_equivalence_for_filled_buy() -> None:
    uninterrupted = _State()
    uninterrupted.local_status = "filled"
    uninterrupted.local_qty = 1.0
    restarted = _State()
    report_a = ReconciliationService(uninterrupted.broker()).reconcile()
    broker_b = restarted.broker()
    report_b = ReconciliationService(broker_b).reconcile()
    assert report_a == report_b
    assert (uninterrupted.local_status, uninterrupted.owned_qty()) == (restarted.local_status, restarted.owned_qty())
    ReconciliationService(broker_b).reconcile()
    assert restarted.recoveries == 1


def _oversized_fill_broker(
    *, ordered_qty: float | None, filled_qty: float = 1.5
) -> tuple[Any, list[tuple[str, float]]]:
    """A recovered deterministic order whose broker quantity exceeds the local row."""
    state = _State(broker_qty=filled_qty)
    broker = state.broker()
    order = broker._fetch_broker_order("intent-1", "broker-1")
    order.filled_qty = filled_qty
    if ordered_qty is not None:
        order.qty = ordered_qty
    adopted: list[tuple[str, float]] = []
    broker.portfolio_order_quantity_adopted = lambda client_id, qty: adopted.append((client_id, qty))
    return broker, adopted


def test_recovered_order_adopts_broker_ordered_quantity() -> None:
    broker, adopted = _oversized_fill_broker(ordered_qty=1.5)
    ReconciliationService(broker).reconcile()
    assert adopted == [("intent-1", 1.5)]


def test_fill_beyond_broker_ordered_quantity_stays_fail_closed() -> None:
    broker, adopted = _oversized_fill_broker(ordered_qty=1.2)
    with pytest.raises(ReconciliationResultError, match="Invalid broker cumulative fill"):
        ReconciliationService(broker).reconcile()
    assert adopted == []


def test_missing_broker_ordered_quantity_stays_fail_closed() -> None:
    broker, adopted = _oversized_fill_broker(ordered_qty=None)
    with pytest.raises(ReconciliationResultError, match="Invalid broker cumulative fill"):
        ReconciliationService(broker).reconcile()
    assert adopted == []


def test_oversized_fill_without_adoption_hook_stays_fail_closed() -> None:
    broker, _adopted = _oversized_fill_broker(ordered_qty=1.5)
    del broker.portfolio_order_quantity_adopted
    with pytest.raises(ReconciliationResultError, match="Invalid broker cumulative fill"):
        ReconciliationService(broker).reconcile()


def test_quantity_within_broker_precision_matches() -> None:
    # Alpaca reports 0.029061298 for a ledger quantity of 0.0290612996625.
    state = _State(broker_qty=0.029061298, filled_qty=0.0290612996625)
    report = ReconciliationService(state.broker()).reconcile()
    assert report["MATCH"] == ["AAPL"]
    assert report["QUANTITY_MISMATCH"] == []


def test_quantity_gap_beyond_broker_precision_still_mismatches() -> None:
    state = _State(broker_qty=0.02906, filled_qty=0.0291)
    report = ReconciliationService(state.broker()).reconcile()
    assert report["QUANTITY_MISMATCH"] == ["AAPL"]


def test_calculated_fully_filled_order_is_booked_filled() -> None:
    """``calculated`` (done for the day, settlement pending) is a valid final
    state, not an unrecognized one: a full fill books ``filled``."""
    state = _State(broker_status="calculated")
    broker = state.broker()
    broker._fetch_broker_order("intent-1", "broker-1").qty = 1.0
    report = ReconciliationService(broker).reconcile()
    assert state.local_status == "filled"
    assert report["MATCH"] == ["AAPL"]


def test_calculated_partially_filled_order_is_booked_done_for_day() -> None:
    state = _State(broker_status="calculated", broker_qty=0.4)
    broker = state.broker()
    broker._fetch_broker_order("intent-1", "broker-1").qty = 1.0
    ReconciliationService(broker).reconcile()
    assert (state.local_status, state.local_qty) == ("done_for_day", 0.4)


@pytest.mark.parametrize("status", ["held", "stopped", "suspended", "replaced", "pending_review"])
def test_paused_or_reviewed_broker_order_stays_working(status: str) -> None:
    """Valid Alpaca states in which the order can still fill are working
    states, never a fatal 'unrecognized status'."""
    state = _State(broker_status=status, broker_qty=0.0, filled_qty=0.0)
    ReconciliationService(state.broker()).reconcile()
    assert state.local_status == status


def test_owned_quantity_provider_failure_propagates_instead_of_inventing_quantities() -> None:
    state = _State()
    broker = state.broker()
    calls = 0

    def failing_provider() -> dict[str, float]:
        nonlocal calls
        calls += 1
        raise RuntimeError("repository unavailable")

    broker.portfolio_owned_quantities = failing_provider
    with pytest.raises(RuntimeError, match="repository unavailable"):
        ReconciliationService(broker).reconcile()
    assert calls == 1


def test_a_broker_short_never_matches_a_long_portfolio_quantity() -> None:
    """Signed quantities: owning 1 share while the broker is short 1 share is
    drift, not a MATCH on abs(qty)."""
    state = _State()
    broker = state.broker()
    broker.portfolio_owned_quantities = lambda: {"AAPL": 1.0}
    broker.account = lambda: AccountSnapshot(
        cash=0,
        equity=100,
        peak_equity=100,
        daily_pnl=0,
        positions=(PositionSnapshot(symbol="AAPL", qty=-1.0, avg_price=100, current_price=100, market=Market.EQUITY),),
    )
    report = ReconciliationService(broker).reconcile()
    assert report["MATCH"] == []
    assert report["QUANTITY_MISMATCH"] == ["AAPL"]
