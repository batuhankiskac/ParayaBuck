"""Offline restart and activity completeness regressions."""

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
            _client_adapter=SimpleNamespace(request=lambda *_args, **_kwargs: []),
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
            manage_external_positions=False,
            ledger=SimpleNamespace(
                managed_for_account=lambda _account: {},
                managed_entries_for_account=lambda _account: (),
            ),
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


def test_broker_open_order_without_client_id_blocks_reconciliation() -> None:
    broker = _State().broker()
    broker._get_open_orders = lambda _request: (SimpleNamespace(id="broker-1", client_order_id=""),)
    with pytest.raises(ReconciliationResultError, match="missing or duplicate client ID"):
        ReconciliationService(broker).reconcile()


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


def test_repeated_fill_page_is_failure_without_watermark() -> None:
    page = [
        {
            "id": f"activity-{index:03d}",
            "order_id": f"order-{index:03d}",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "1",
            "price": "1",
            "transaction_time": "2026-01-01T00:00:00+00:00",
        }
        for index in range(100)
    ]
    applied: list[str] = []
    watermarks: list[datetime] = []
    broker = SimpleNamespace(
        _client_adapter=SimpleNamespace(request=lambda *_args, **_kwargs: page),
        _account_id="acct",
        ledger=SimpleNamespace(
            get_reconcile_watermark=lambda *_args: None,
            set_reconcile_watermark=lambda _account, _kind, value: watermarks.append(value),
            apply_fee_activity=lambda _account, **kwargs: applied.append(str(kwargs["activity_id"])),
            entry_order_for_broker_id=lambda *_args: None,
        ),
    )
    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is False
    assert applied == []
    assert watermarks == []


def test_malformed_fill_page_is_failure() -> None:
    broker = SimpleNamespace(
        _client_adapter=SimpleNamespace(request=lambda *_args, **_kwargs: {"bad": "page"}),
        _account_id="acct",
        ledger=SimpleNamespace(get_reconcile_watermark=lambda *_args: None),
    )
    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is False


def test_incomplete_fill_history_blocks_account_drift_and_startup() -> None:
    broker = _State().broker()
    broker._client_adapter.request = lambda *_args, **_kwargs: {"invalid": "page"}
    account_calls = 0
    original_account = broker.account

    def account() -> AccountSnapshot:
        nonlocal account_calls
        account_calls += 1
        return original_account()

    broker.account = account
    with pytest.raises(ReconciliationResultError, match="FILL activity backfill incomplete"):
        ReconciliationService(broker).reconcile()
    assert account_calls == 0


def test_malformed_fill_activity_is_failure() -> None:
    broker = SimpleNamespace(
        _client_adapter=SimpleNamespace(
            request=lambda *_args, **_kwargs: [
                {"id": "activity-1", "order_id": "broker-1", "symbol": "AAPL", "side": "buy", "qty": "1", "price": "10"}
            ]
        ),
        _account_id="acct",
        ledger=SimpleNamespace(get_reconcile_watermark=lambda *_args: None),
    )
    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is False


@pytest.mark.parametrize("count", [0, 1, 100, 101, 201])  # type: ignore[untyped-decorator]
def test_fill_pages_exhaust_to_empty_and_apply_each_activity_once(count: int) -> None:
    activities = [
        {
            "id": f"activity-{index:03d}",
            "order_id": f"order-{index:03d}",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "1",
            "price": "10",
            "transaction_time": "2026-01-01T00:00:00+00:00",
        }
        for index in range(count)
    ]
    applied: list[str] = []
    cursors: list[str | None] = []

    def request(_method: str, _path: str, data: dict[str, object]) -> list[dict[str, str]]:
        cursor = str(data["page_token"]) if "page_token" in data else None
        cursors.append(cursor)
        start = next((i + 1 for i, item in enumerate(activities) if item["id"] == cursor), 0)
        return activities[start : start + 100]

    broker = SimpleNamespace(
        _client_adapter=SimpleNamespace(request=request),
        _account_id="acct",
        ledger=SimpleNamespace(
            get_reconcile_watermark=lambda *_args: None,
            set_reconcile_watermark=lambda *_args: None,
            apply_fee_activity=lambda _account, **kw: applied.append(str(kw["activity_id"])),
            entry_order_for_broker_id=lambda *_args: None,
        ),
    )
    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is True
    assert applied == [item["id"] for item in activities]
    assert len(cursors) == (count + 99) // 100 + 1


@pytest.mark.parametrize("failure", ["exception", "malformed", "missing_id", "duplicate"])  # type: ignore[untyped-decorator]
def test_second_fill_page_failure_never_applies_first_page(failure: str) -> None:
    first_page = [
        {
            "id": f"activity-{index:03d}",
            "order_id": f"order-{index:03d}",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "1",
            "price": "10",
            "transaction_time": "2026-01-01T00:00:00+00:00",
        }
        for index in range(100)
    ]
    applied: list[str] = []
    watermarks: list[datetime] = []

    def request(_method: str, _path: str, data: dict[str, object]) -> object:
        if "page_token" not in data:
            return first_page
        if failure == "exception":
            raise TimeoutError("later page")
        if failure == "malformed":
            return {"invalid": "page"}
        if failure == "missing_id":
            return [{"id": "", **{k: v for k, v in first_page[-1].items() if k != "id"}}]
        return first_page

    broker = SimpleNamespace(
        _client_adapter=SimpleNamespace(request=request),
        _account_id="acct",
        ledger=SimpleNamespace(
            get_reconcile_watermark=lambda *_args: None,
            set_reconcile_watermark=lambda _account, _kind, value: watermarks.append(value),
            apply_fee_activity=lambda _account, **kw: applied.append(str(kw["activity_id"])),
            entry_order_for_broker_id=lambda *_args: None,
        ),
    )
    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is False
    assert applied == []
    assert watermarks == []


def test_three_partial_fills_use_incremental_qty_not_sum_of_cumulative() -> None:
    quantities = (3, 4, 3)
    cumulatives = (3, 7, 10)
    activities = [
        {
            "id": f"partial-{i}",
            "order_id": "broker-1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": str(qty),
            "cum_qty": str(cum),
            "leaves_qty": str(10 - cum),
            "price": "10",
            "transaction_time": f"2026-01-01T00:00:0{i}+00:00",
        }
        for i, (qty, cum) in enumerate(zip(quantities, cumulatives, strict=True))
    ]
    recovered: list[Any] = []
    broker = SimpleNamespace(
        _client_adapter=SimpleNamespace(
            request=lambda _method, _path, data: [] if "page_token" in data else activities
        ),
        _account_id="acct",
        ledger=SimpleNamespace(
            get_reconcile_watermark=lambda *_args: None,
            set_reconcile_watermark=lambda *_args: None,
            apply_fee_activity=lambda *_args, **_kwargs: False,
            entry_order_for_broker_id=lambda *_args: (
                SimpleNamespace(qty=10, client_order_id="intent-1", symbol="AAPL", market=Market.EQUITY),
                0,
            ),
            apply_execution_event=lambda event: recovered.append(event),
        ),
    )
    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is True
    assert len(recovered) == 1
    assert recovered[0].cumulative_filled_qty == 10


def test_overlap_activity_id_across_pages_is_applied_once() -> None:
    first_page = [
        {
            "id": f"activity-{i:03d}",
            "order_id": f"broker-{i:03d}",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "1",
            "price": "10",
            "transaction_time": "2026-01-01T00:00:00+00:00",
        }
        for i in range(100)
    ]
    last_page = [
        first_page[-1],
        {
            "id": "activity-100",
            "order_id": "broker-100",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "1",
            "price": "10",
            "transaction_time": "2026-01-01T00:00:00+00:00",
        },
    ]
    applied: list[str] = []

    def request(_method: str, _path: str, data: dict[str, object]) -> list[dict[str, str]]:
        if "page_token" not in data:
            return first_page
        if data["page_token"] == "activity-099":
            return last_page
        return []

    broker = SimpleNamespace(
        _client_adapter=SimpleNamespace(request=request),
        _account_id="acct",
        ledger=SimpleNamespace(
            get_reconcile_watermark=lambda *_args: None,
            set_reconcile_watermark=lambda *_args: None,
            apply_fee_activity=lambda _account, **kw: applied.append(str(kw["activity_id"])),
            entry_order_for_broker_id=lambda *_args: None,
        ),
    )
    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is True
    assert len(applied) == 101
    assert len(set(applied)) == 101
