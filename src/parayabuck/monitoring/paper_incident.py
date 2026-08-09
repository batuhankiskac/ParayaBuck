"""Read-only broker/local-ledger reconciliation for paper incidents."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class BrokerOrderRecord:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: float
    filled_quantity: float
    status: str
    created_at: datetime


@dataclass(frozen=True)
class BrokerFillRecord:
    activity_id: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    occurred_at: datetime


@dataclass(frozen=True)
class BrokerPositionRecord:
    symbol: str
    quantity: float
    average_entry_price: float
    current_price: float


@dataclass(frozen=True)
class PaperIncidentSnapshot:
    orders: tuple[BrokerOrderRecord, ...]
    fills: tuple[BrokerFillRecord, ...]
    positions: tuple[BrokerPositionRecord, ...] = ()


@dataclass(frozen=True)
class PaperIncidentReport:
    realized_pnl: float
    unrealized_pnl: float
    slippage: float
    fees: float
    manual_exit_order_ids: tuple[str, ...]
    external_position_symbols: tuple[str, ...]
    broker_orders_missing_locally: tuple[str, ...]
    local_orders_missing_at_broker: tuple[str, ...]
    duplicate_client_order_ids: tuple[str, ...]
    local_closed_positions_with_zero_pnl: int
    local_closed_positions_with_zero_fees: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PaperIncidentReconciler:
    """Reconcile a broker snapshot without mutating either source."""

    def reconcile(
        self, snapshot: PaperIncidentSnapshot, database_path: str | Path
    ) -> PaperIncidentReport:
        local_orders, local_positions = _read_local_ledger(database_path)
        by_broker_id = {
            str(item.get("broker_order_id") or ""): item
            for item in local_orders
            if item.get("broker_order_id")
        }
        by_client_id = {str(item.get("client_order_id") or ""): item for item in local_orders}
        broker_order_ids = {order.order_id for order in snapshot.orders}
        broker_client_ids = [order.client_order_id for order in snapshot.orders]
        missing_local = tuple(
            sorted(
                order.order_id
                for order in snapshot.orders
                if order.order_id not in by_broker_id and order.client_order_id not in by_client_id
            )
        )
        missing_broker = tuple(
            sorted(
                str(order.get("broker_order_id") or order.get("client_order_id") or "")
                for order in local_orders
                if order.get("broker_order_id") not in broker_order_ids
                and order.get("client_order_id") not in broker_client_ids
            )
        )
        duplicates = tuple(
            sorted(
                value
                for value in set(broker_client_ids)
                if value and broker_client_ids.count(value) > 1
            )
        )
        realized, slippage = _fill_accounting(
            snapshot.fills, by_broker_id, by_client_id, snapshot.orders
        )
        unrealized = sum(
            (position.current_price - position.average_entry_price) * position.quantity
            for position in snapshot.positions
        )
        local_symbols = {
            str(position.get("canonical_symbol") or "")
            for position in local_positions
            if str(position.get("status") or "") in {"open", "manual_intervention_required"}
        }
        manual = tuple(
            sorted(
                order.order_id
                for order in snapshot.orders
                if order.side.lower() == "sell"
                and order.order_id not in by_broker_id
                and order.client_order_id not in by_client_id
            )
        )
        closed = [
            position
            for position in local_positions
            if str(position.get("status") or "") == "closed"
        ]
        return PaperIncidentReport(
            realized_pnl=round(realized, 8),
            unrealized_pnl=round(unrealized, 8),
            slippage=round(slippage, 8),
            fees=round(sum(fill.fee for fill in snapshot.fills), 8),
            manual_exit_order_ids=manual,
            external_position_symbols=tuple(
                sorted(
                    position.symbol
                    for position in snapshot.positions
                    if position.symbol not in local_symbols
                )
            ),
            broker_orders_missing_locally=missing_local,
            local_orders_missing_at_broker=missing_broker,
            duplicate_client_order_ids=duplicates,
            local_closed_positions_with_zero_pnl=sum(
                abs(float(str(position.get("realized_pnl") or 0.0))) < 1e-12 for position in closed
            ),
            local_closed_positions_with_zero_fees=sum(
                abs(
                    float(str(position.get("entry_fees") or 0.0))
                    + float(str(position.get("exit_fees") or 0.0))
                )
                < 1e-12
                for position in closed
            ),
        )

    @staticmethod
    def anonymized_replay_fixture(
        snapshot: PaperIncidentSnapshot, report: PaperIncidentReport
    ) -> dict[str, object]:
        def token(value: str) -> str:
            return hashlib.sha256(("parayabuck-replay|" + value).encode()).hexdigest()[:16]

        expected = report.to_dict()
        for key in (
            "manual_exit_order_ids",
            "broker_orders_missing_locally",
            "local_orders_missing_at_broker",
            "duplicate_client_order_ids",
        ):
            raw_values = expected.get(key, ())
            values = raw_values if isinstance(raw_values, list | tuple) else ()
            expected[key] = [token(str(value)) for value in values]
        return {
            "schema_version": 1,
            "fills": [
                {
                    **asdict(fill),
                    "activity_id": token(fill.activity_id),
                    "order_id": token(fill.order_id),
                    "occurred_at": fill.occurred_at.isoformat(),
                }
                for fill in snapshot.fills
            ],
            "positions": [asdict(position) for position in snapshot.positions],
            "expected_incident": expected,
        }


def write_anonymized_fixture(path: str | Path, payload: dict[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _read_local_ledger(
    path: str | Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    source = Path(path).resolve()
    uri = f"file:{source}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        orders = (
            [dict(row) for row in connection.execute("SELECT * FROM orders")]
            if "orders" in tables
            else []
        )
        positions = (
            [dict(row) for row in connection.execute("SELECT * FROM managed_positions")]
            if "managed_positions" in tables
            else []
        )
    return orders, positions


def _fill_accounting(
    fills: tuple[BrokerFillRecord, ...],
    by_broker_id: dict[str, dict[str, object]],
    by_client_id: dict[str, dict[str, object]],
    orders: tuple[BrokerOrderRecord, ...],
) -> tuple[float, float]:
    broker_orders = {order.order_id: order for order in orders}
    quantity: dict[str, float] = {}
    average: dict[str, float] = {}
    realized = 0.0
    slippage = 0.0
    for fill in sorted(fills, key=lambda item: (item.occurred_at, item.activity_id)):
        symbol = fill.symbol
        if fill.side.lower() == "buy":
            old_qty = quantity.get(symbol, 0.0)
            new_qty = old_qty + fill.quantity
            average[symbol] = (
                average.get(symbol, 0.0) * old_qty + fill.price * fill.quantity
            ) / new_qty
            quantity[symbol] = new_qty
        else:
            sold = min(quantity.get(symbol, 0.0), fill.quantity)
            realized += (fill.price - average.get(symbol, fill.price)) * sold
            quantity[symbol] = max(0.0, quantity.get(symbol, 0.0) - sold)
        local = by_broker_id.get(fill.order_id)
        if local is None and fill.order_id in broker_orders:
            local = by_client_id.get(broker_orders[fill.order_id].client_order_id)
        if local is not None:
            reference = float(str(local.get("entry_price") or 0.0))
            if reference > 0:
                direction = 1.0 if fill.side.lower() == "buy" else -1.0
                slippage += (fill.price - reference) * fill.quantity * direction
    # Keep trading PnL and fees as separate decomposition components.  Netting
    # fees here would make the report count them twice (once in realized PnL
    # and once in ``fees``) and obscure the source of the incident loss.
    return realized, slippage


__all__ = [
    "BrokerFillRecord",
    "BrokerOrderRecord",
    "BrokerPositionRecord",
    "PaperIncidentReconciler",
    "PaperIncidentReport",
    "PaperIncidentSnapshot",
    "write_anonymized_fixture",
]
