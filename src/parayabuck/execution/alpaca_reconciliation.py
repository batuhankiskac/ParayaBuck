"""Reconciliation — 7-bucket, idempotent, fail-closed.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8).

Responsibility:
- Resolve every durable nonterminal portfolio order against the broker
  (``_recover_portfolio_orders``) → ``account()`` → holdings 7-bucket
  (``MATCH`` etc.) of portfolio-owned versus signed broker quantities →
  ``_reconcile_stale_portfolio_ownership`` → OPEN-order identity comparison.
- Idempotent: a second call with the same broker truth yields the same
  buckets and books no fill twice.
- Foreign inventory in the shared account (a crypto holding, or an equity
  symbol outside the bot's tradable universe that no lineage claims) is
  excluded from the ownership buckets: the runtime classifies it, alerts once
  at startup and never trades it. Foreign orders, and foreign inventory on a
  symbol the bot does trade (a short included), stay fail-closed drift.
- Emits observability but never controls trading; monitoring failure does
  not affect reconciliation decisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.alerting import emit_execution_alert
from parayabuck.execution.alpaca_account import flat_broker_reconcile_scope
from parayabuck.execution.alpaca_models import TERMINAL_BROKER_ORDER_STATUSES
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.ownership_rebase import LotBookInconsistency
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.execution.alpaca_reconciliation")

# Broker order states mapped onto the local order row. Terminal states
# (``TERMINAL_BROKER_ORDER_STATUSES``) are kept verbatim; ``calculated`` (done
# for the day, settlement pending) is resolved from the cumulative fill below.
# Valid Alpaca states in which the order may still fill (or is merely paused
# or under review); the row stays working with the broker's own status.
_WORKING_BROKER_STATUSES = frozenset(
    {
        "new",
        "accepted",
        "pending_new",
        "pending_cancel",
        "pending_replace",
        "accepted_for_bidding",
        "held",
        "stopped",
        "suspended",
        "replaced",
        "pending_review",
    }
)

# Absolute floor for repository-vs-broker position quantity agreement.
QUANTITY_MATCH_FLOOR = 1e-8

# Cost-basis drift band: the bot's average cost may differ from the broker's
# avg_entry_price by max(relative, floor) before it is reported. Read-only.
COST_BASIS_DRIFT_RELATIVE = 0.005
COST_BASIS_DRIFT_FLOOR = 0.02
# Informational key in the reconcile result: symbols whose cost basis drifted.
# It is not a drift category, so it never halts orders.
COST_BASIS_DRIFT_KEY = "COST_BASIS_DRIFT"

# A local portfolio order still ``submitting`` or ``reconciliation_required``
# whose client order id Alpaca definitively does not know (404) is closed as
# ``canceled`` once its last write is this old: far beyond any submit call,
# so an in-flight submit (reconcile does not hold the portfolio lock) can
# never be canceled under it. Younger rows keep failing closed as drift.
UNKNOWN_ORDER_CANCEL_AGE_SECONDS = 600.0
_UNKNOWN_ORDER_CANCELABLE_STATUSES = frozenset({"submitting", "reconciliation_required"})

RECONCILIATION_CATEGORIES: tuple[str, ...] = (
    "MATCH",
    "LOCAL_STALE",
    "BROKER_STALE",
    "QUANTITY_MISMATCH",
    "UNKNOWN_ORDER",
    "MISSING_LOCAL_ORDER",
    "MISSING_BROKER_ORDER",
)
RECONCILIATION_DRIFT_CATEGORIES: frozenset[str] = frozenset(
    category for category in RECONCILIATION_CATEGORIES if category != "MATCH"
)


class ReconciliationResultError(ValueError):
    """Raised when a broker reconciliation result cannot be trusted."""


@dataclass(frozen=True)
class ReconciliationReport:
    """Validated, immutable view of the broker reconciliation result."""

    categories: tuple[tuple[str, tuple[str, ...]], ...]
    cost_basis_drift: tuple[str, ...] = ()

    @classmethod
    def from_result(cls, result: object) -> ReconciliationReport:
        if not isinstance(result, Mapping):
            raise ReconciliationResultError("Reconciliation returned a non-mapping result")
        if not set(RECONCILIATION_CATEGORIES) <= set(result) or (
            set(result) - set(RECONCILIATION_CATEGORIES) - {COST_BASIS_DRIFT_KEY}
        ):
            raise ReconciliationResultError("Reconciliation result has an unexpected category shape")
        drifted = result.get(COST_BASIS_DRIFT_KEY, [])
        if not isinstance(drifted, (list, tuple)) or any(
            not isinstance(value, str) or not value.strip() for value in drifted
        ):
            raise ReconciliationResultError("Reconciliation cost-basis drift list is invalid")
        normalized: list[tuple[str, tuple[str, ...]]] = []
        for category in RECONCILIATION_CATEGORIES:
            values = result[category]
            if not isinstance(values, (list, tuple)):
                raise ReconciliationResultError(f"Reconciliation category {category} is not a list")
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ReconciliationResultError(f"Reconciliation category {category} contains an invalid value")
            normalized.append((category, tuple(values)))
        return cls(tuple(normalized), tuple(drifted))

    def as_dict(self) -> dict[str, list[str]]:
        return {category: list(values) for category, values in self.categories}

    @property
    def drift_categories(self) -> dict[str, tuple[str, ...]]:
        return {
            category: values
            for category, values in self.categories
            if category in RECONCILIATION_DRIFT_CATEGORIES and values
        }

    @property
    def has_drift(self) -> bool:
        return bool(self.drift_categories)


__all__ = [
    "RECONCILIATION_CATEGORIES",
    "RECONCILIATION_DRIFT_CATEGORIES",
    "ReconciliationReport",
    "ReconciliationResultError",
    "ReconciliationService",
]


class ReconciliationService:
    """Reconciliation coordinator — production implementation."""

    def __init__(self, broker: Any) -> None:
        self._broker = broker
        # Symbols already alerted for cost-basis drift in this session.
        self._cost_basis_alerted: set[str] = set()

    # -- helpers ---------------------------------------------------------

    def _field(self, value: Any, name: str, default: Any = None) -> Any:
        return self._broker._field(value, name, default)

    def _normalized_field(self, value: Any, name: str) -> str:
        return self._broker._normalized_field(value, name)  # type: ignore[no-any-return]

    def _recover_portfolio_orders(self, broker_orders: Any) -> None:
        """Resolve durable nonterminal orders before comparing ownership."""
        broker = self._broker
        provider = getattr(broker, "portfolio_open_orders", None)
        if provider is None:
            return
        open_by_client: dict[str, Any] = {}
        for order in broker_orders:
            client_id = str(self._field(order, "client_order_id", "") or "").strip()
            if not client_id:
                # Foreign/manual broker orders carry no client id: they cannot
                # back a local recovery, so leave them for the UNKNOWN_ORDER
                # bucket instead of aborting the whole pass.
                continue
            if client_id in open_by_client:
                raise ReconciliationResultError("Broker OPEN list has duplicate client ID")
            open_by_client[client_id] = order
        lookup = getattr(broker, "_fetch_broker_order", None)
        callback = getattr(broker, "portfolio_fill_recovered", None)
        for local in provider() or ():
            client_id = str(getattr(local, "client_order_id", "") or "").strip()
            if not client_id:
                raise ReconciliationResultError("Local nonterminal order has no client ID")
            order = open_by_client.get(client_id)
            if order is None:
                if lookup is None:
                    continue  # The final OPEN-ID comparison still fails closed.
                order = lookup(client_id, str(getattr(local, "broker_order_id", "") or ""))
            if order is None:
                # A targeted 404 alone is not evidence of a terminal outcome;
                # only an aged, never-acknowledged row is closed.
                self._cancel_unknown_portfolio_order(local, client_id, lookup, callback)
                continue
            actual_client_id = str(self._field(order, "client_order_id", "") or "")
            broker_id = str(self._field(order, "id", "") or "")
            if actual_client_id != client_id or not broker_id:
                raise ReconciliationResultError(f"Broker order identity mismatch for {client_id}")
            persisted_id = str(getattr(local, "broker_order_id", "") or "")
            if persisted_id and persisted_id != broker_id:
                raise ReconciliationResultError(f"Broker order ID changed for {client_id}")
            symbol = canonical_symbol(str(self._field(order, "symbol", "") or ""), local.market)
            side = str(self._normalized_field(order, "side") or "").lower()
            if symbol != canonical_symbol(local.symbol, local.market) or side != str(local.side).lower():
                raise ReconciliationResultError(f"Broker order contract mismatch for {client_id}")
            status = str(self._normalized_field(order, "status") or "").lower()
            if (
                status != "partially_filled"
                and status != "calculated"
                and status not in TERMINAL_BROKER_ORDER_STATUSES
                and status not in _WORKING_BROKER_STATUSES
            ):
                # A broker state this code does not know: book nothing for
                # this order (its row stays as is, so any fill it hides still
                # surfaces as ownership or OPEN-ID drift) and let the rest of
                # the pass proceed instead of halting on vocabulary alone.
                self._flag_unrecognized_status(client_id, status)
                continue
            try:
                quantity = float(self._field(order, "filled_qty", 0) or 0)
                average_raw = self._field(order, "filled_avg_price", None)
                average = float(average_raw) if average_raw is not None else None
            except (TypeError, ValueError) as exc:
                raise ReconciliationResultError(f"Unparseable broker fill facts for {client_id}") from exc
            if status == "partially_filled":
                local_status = "partial_fill"
            elif status == "calculated":
                local_status = self._calculated_status(order, quantity)
            else:
                local_status = status
            local_quantity = float(local.quantity)
            if isfinite(quantity) and quantity > local_quantity + max(1e-9, local_quantity * 1e-8):
                local_quantity = self._adopt_broker_order_quantity(broker, order, client_id, quantity, local_quantity)
            if (
                not isfinite(quantity)
                or (average is not None and not isfinite(average))
                or quantity < 0
                or quantity > local_quantity + max(1e-9, local_quantity * 1e-8)
            ):
                raise ReconciliationResultError(f"Invalid broker cumulative fill for {client_id}")
            if (quantity > 0 and (average is None or average <= 0)) or (status == "filled" and quantity <= 0):
                raise ReconciliationResultError(f"Incomplete broker fill for {client_id}")
            if callback is None:
                raise ReconciliationResultError(f"Portfolio order recovery unavailable for {client_id}")
            timestamp_raw = self._field(order, "filled_at", None) or self._field(order, "updated_at", None)
            timestamp = (
                timestamp_raw
                if isinstance(timestamp_raw, datetime)
                else datetime.fromisoformat(str(timestamp_raw))
                if timestamp_raw is not None
                else datetime.now(UTC)
            )
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            callback(
                client_id,
                broker_id,
                quantity,
                average,
                timestamp,
                f"{client_id}:recovered:{quantity}:{local_status}",
                None,
                status=local_status,
            )

    @staticmethod
    def _flag_unrecognized_status(client_id: str, status: str) -> None:
        """Log and alert one order skipped for an unknown broker status."""
        log.error(
            "Unrecognized broker order status %r for %s; order left unchanged for the operator",
            status,
            client_id,
        )
        emit_execution_alert(
            "paper_incident",
            message=f"Broker order {client_id} reports unrecognized status {status!r}; order left unchanged",
            category="unrecognized_broker_order_status",
            incident_id=f"unrecognized_broker_order_status:{client_id}:{status}",
            client_order_id=client_id,
            broker_status=status,
        )

    def _calculated_status(self, order: Any, filled: float) -> str:
        """Final local status of a ``calculated`` order (done for the day).

        Fully filled reads ``filled``; anything less can never fill again and
        reads ``done_for_day``. An order without a readable quantity is
        ``filled`` only when it filled at all.
        """
        ordered_raw = self._field(order, "qty", None)
        try:
            ordered = float(ordered_raw) if ordered_raw is not None else None
        except (TypeError, ValueError):
            ordered = None
        if ordered is None or not isfinite(ordered) or ordered <= 0:
            return "filled" if filled > 0 else "done_for_day"
        return "filled" if filled >= ordered - max(1e-9, ordered * 1e-8) else "done_for_day"

    def _cancel_unknown_portfolio_order(self, local: Any, client_id: str, lookup: Any, callback: Any) -> None:
        """Close a never-accepted portfolio order the broker does not know.

        Called after the targeted lookup returned 404. Only a row still
        ``submitting`` or ``reconciliation_required``, whose last write is at
        least ``UNKNOWN_ORDER_CANCEL_AGE_SECONDS`` old, is booked ``canceled``
        with a zero fill, and only when the lookup went through a client-id
        query (a missing query proves nothing). Anything else is unchanged and
        the final OPEN-ID comparison still reports it as drift.
        """
        status = str(getattr(local, "status", "") or "").lower()
        updated_at = getattr(local, "last_updated_at", None)
        client = getattr(self._broker, "_client", None)
        if (
            status not in _UNKNOWN_ORDER_CANCELABLE_STATUSES
            or not isinstance(updated_at, datetime)
            or lookup is None
            or callback is None
            or getattr(client, "get_order_by_client_id", None) is None
        ):
            return
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - updated_at).total_seconds()
        if age < UNKNOWN_ORDER_CANCEL_AGE_SECONDS:
            return
        callback(
            client_id,
            str(getattr(local, "broker_order_id", "") or ""),
            0.0,
            None,
            datetime.now(UTC),
            f"{client_id}:unknown_at_broker:canceled",
            None,
            status="canceled",
        )
        log.warning(
            "Portfolio order unknown at the broker canceled: client_order_id=%s status=%s age_seconds=%d",
            client_id,
            status,
            round(age),
        )
        # One incident per order: the row is terminal afterwards, and the
        # alert throttle keys paper_incident by its incident id.
        emit_execution_alert(
            "paper_incident",
            message="Portfolio order never accepted by the broker was canceled",
            category="unknown_order_canceled",
            incident_id=f"unknown_order_canceled:{client_id}",
            client_order_id=client_id,
            previous_status=status,
            age_seconds=round(age),
        )

    def _adopt_broker_order_quantity(
        self, broker: Any, order: Any, client_id: str, filled: float, local_quantity: float
    ) -> float:
        """Adopt the broker's ordered quantity for one of our own orders.

        A deterministic client ID can be recovered (Alpaca rejects the reused
        ID and the existing order is returned) after the plan was re-sized, so
        the local row may record less than the broker actually ordered. Identity,
        symbol and side were already verified; the broker is authoritative, so
        the local row takes the broker's ordered quantity when it covers the
        cumulative fill. Anything else stays fail-closed.
        """
        adopt = getattr(broker, "portfolio_order_quantity_adopted", None)
        ordered_raw = self._field(order, "qty", None)
        try:
            ordered = float(ordered_raw) if ordered_raw is not None else float("nan")
        except (TypeError, ValueError):
            ordered = float("nan")
        if adopt is None or not isfinite(ordered) or ordered <= 0 or filled > ordered + max(1e-9, ordered * 1e-8):
            return local_quantity
        adopt(client_id, ordered)
        log.warning(
            "Adopted broker ordered quantity for %s: local %.9g -> broker %.9g",
            client_id,
            local_quantity,
            ordered,
        )
        return ordered

    def _bot_tradable_symbols(self) -> frozenset[str] | None:
        """Canonical symbols this bot may trade, or None when it cannot say.

        The brokerage account is shared, so the bot's own tradable universe is
        the boundary between its positions and someone else's. None (no
        provider, or an unreadable one) is fail-closed: every unowned equity
        position then stays drift, exactly as before.
        """
        provider = getattr(self._broker, "bot_tradable_symbols", None)
        if provider is None:
            return None
        try:
            return frozenset(canonical_symbol(str(symbol)) for symbol in provider() or ())
        except Exception:
            log.warning("Bot tradable symbols unavailable; equity positions stay fail-closed", exc_info=True)
            return None

    def _cost_basis_drift(
        self,
        positions: Sequence[Any],
        matched: Sequence[str],
        foreign_positions: Mapping[str, str],
    ) -> dict[str, dict[str, float]]:
        """Read-only check of the bot's average cost against the broker's.

        Only a MATCH symbol is compared: the bot then owns the whole broker
        position, so no external units distort the broker's avg_entry_price.
        The bot's cost comes from the optional ``portfolio_owned_cost_bases``
        provider ``(symbol, market) -> (fifo, average_cost)``. It is never a
        halt or an order block: a drifted symbol is alerted once per session
        and returned. A lot book that cannot be replayed is a reconciliation
        failure (fail closed), not a drift.
        """
        provider = getattr(self._broker, "portfolio_owned_cost_bases", None)
        if provider is None or not matched:
            return {}
        drift: dict[str, dict[str, float]] = {}
        for symbol in matched:
            if symbol in foreign_positions:
                continue
            equity = [
                position
                for position in positions
                if not position.is_flat
                and position.market == Market.EQUITY
                and canonical_symbol(position.symbol, position.market) == symbol
            ]
            if len(equity) != 1:
                continue
            broker_average = float(equity[0].avg_price)
            if not isfinite(broker_average) or broker_average <= 0:
                continue
            try:
                bases = provider(symbol, Market.EQUITY)
            except LotBookInconsistency as error:
                raise ReconciliationResultError(
                    f"Lot book inconsistent for {symbol}; reconciliation cannot verify cost basis: {error}"
                ) from error
            if not bases or len(bases) < 2:
                continue
            bot_average = float(bases[1])
            if not isfinite(bot_average) or bot_average <= 0:
                continue
            tolerance = max(COST_BASIS_DRIFT_RELATIVE * broker_average, COST_BASIS_DRIFT_FLOOR)
            if abs(bot_average - broker_average) <= tolerance:
                continue
            drift[symbol] = {
                "bot_average_cost": bot_average,
                "broker_avg_entry_price": broker_average,
                "tolerance": tolerance,
            }
            if symbol in self._cost_basis_alerted:
                continue
            self._cost_basis_alerted.add(symbol)
            log.warning(
                "Cost basis drift for %s: bot average %.4f vs broker avg_entry_price %.4f (tolerance %.4f); "
                "orders are not blocked",
                symbol,
                bot_average,
                broker_average,
                tolerance,
            )
            emit_execution_alert(
                "paper_incident",
                message=(
                    f"Cost basis drift for {symbol}: bot average {bot_average:.4f} differs from broker "
                    f"avg_entry_price {broker_average:.4f} by more than {tolerance:.4f}. Read-only; "
                    "orders are not blocked. Verify the fills and any corporate action."
                ),
                category="cost_basis_drift",
                incident_id=f"cost_basis_drift:{symbol}",
                symbol=symbol,
                bot_average_cost=bot_average,
                broker_avg_entry_price=broker_average,
            )
        return drift

    # -- public ----------------------------------------------------------

    def reconcile(self) -> dict[str, list[str]]:
        broker = self._broker
        categories: dict[str, list[str]] = {category: [] for category in RECONCILIATION_CATEGORIES}
        # Bind account identity before reading account-scoped durable orders.
        # account() classifies ownership, so it must run after offline fills
        # have updated local ownership rather than rejecting an explained fill.
        if not getattr(broker, "_account_id", ""):
            raw_account = broker._client.get_account()
            broker._account_id = str(getattr(raw_account, "id", "") or getattr(raw_account, "account_number", ""))
            broker._account_number = str(getattr(raw_account, "account_number", "") or "")
            if not broker._account_id:
                raise ReconciliationResultError("Broker account has no identity")
        broker_orders = broker._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
        self._recover_portfolio_orders(broker_orders)
        # Reconcile must see a flat broker to clear stale ownership.
        with flat_broker_reconcile_scope():
            account: AccountSnapshot = broker.account()
        # One provider read; a failure propagates to the caller's
        # transient-failure path instead of inventing quantities.
        owned_provider = getattr(broker, "portfolio_owned_quantities", None)
        local_quantities: dict[str, float] = (
            {str(symbol): float(quantity) for symbol, quantity in (owned_provider() or {}).items()}
            if owned_provider is not None
            else {}
        )
        broker_symbols = {canonical_symbol(p.symbol, p.market) for p in account.positions if not p.is_flat}
        tradable_symbols = self._bot_tradable_symbols()
        # Signed broker quantity: a short is never owned inventory, so it can
        # never MATCH a long portfolio quantity.
        broker_quantities: dict[str, float] = {}
        # Foreign inventory — a crypto holding, or an equity symbol no bot
        # lineage claims that the strategy does not trade — is observed and
        # alerted once at startup, never ownership drift: the bot neither
        # sized it nor can it legitimately sell it. It still counts toward
        # account gross and slot limits (AccountService). Foreign inventory on
        # a symbol the bot DOES trade keeps fail-closed drift semantics.
        foreign_positions: dict[str, str] = {}
        for position in account.positions:
            if position.is_flat:
                continue
            market = position.market
            canonical = canonical_symbol(position.symbol, market)
            if market != Market.EQUITY or (
                tradable_symbols is not None and canonical not in local_quantities and canonical not in tradable_symbols
            ):
                foreign_positions[canonical] = market.value
                continue
            broker_quantities[canonical] = broker_quantities.get(canonical, 0.0) + float(position.qty)
        if foreign_positions:
            log.warning(
                "Foreign positions excluded from ownership reconciliation: %s",
                ", ".join(f"{symbol} ({market})" for symbol, market in sorted(foreign_positions.items())),
            )
        for symbol in sorted(set(local_quantities) | set(broker_quantities)):
            local_qty = local_quantities.get(symbol, 0.0)
            broker_qty = broker_quantities.get(symbol, 0.0)
            if local_qty > 1e-12 and abs(broker_qty) > 1e-12:
                # Alpaca reports quantities at 9 decimals, so local quantities
                # can differ from the broker by a few units of the 9th decimal.
                tolerance = max(QUANTITY_MATCH_FLOOR, local_qty * 1e-8)
                if abs(local_qty - broker_qty) <= tolerance:
                    categories["MATCH"].append(symbol)
                else:
                    categories["QUANTITY_MISMATCH"].append(symbol)
            elif local_qty > 1e-12:
                categories["LOCAL_STALE"].append(symbol)
                categories["MISSING_BROKER_ORDER"].append(symbol)
            elif abs(broker_qty) > 1e-12:
                categories["BROKER_STALE"].append(symbol)
                categories["MISSING_LOCAL_ORDER"].append(symbol)
        cost_basis_drift = self._cost_basis_drift(account.positions, categories["MATCH"], foreign_positions)
        if categories["QUANTITY_MISMATCH"]:
            # A share count the runtime could not prove: a non-standard
            # corporate action, or a foreign trade on the shared key. Name
            # the symbols and the supported recovery — a bare bucket name
            # sends the operator to SQLite, which the runbook forbids.
            log.warning(
                "Ownership quantity mismatch needs an operator rebase: %s. Confirm the broker "
                "position, then `parayabuck ops rebase-ownership SYMBOL --broker-qty QTY --confirm`.",
                ", ".join(categories["QUANTITY_MISMATCH"]),
            )
        if local_quantities and (flat_reconciler := getattr(broker, "portfolio_flat_reconciled", None)):
            for symbol in local_quantities:
                if symbol in broker_symbols:
                    continue
                market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
                if broker._reconcile_stale_portfolio_ownership(symbol, market):
                    continue
                flat_reconciler(symbol, market)
        local_open_ids: set[str] = set()
        local_provider = getattr(broker, "portfolio_open_orders", None)
        if local_provider is not None:
            for local in local_provider() or ():
                client_id = str(getattr(local, "client_order_id", "") or "").strip()
                if not client_id:
                    raise ReconciliationResultError("Local nonterminal order has no client ID")
                local_open_ids.add(client_id)
        # Order recovery above may have moved rows terminal; compare against a
        # fresh exhaustive list, not the pre-recovery page set.
        current_broker_orders = broker._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
        broker_open_ids: set[str] = set()
        for order in current_broker_orders or ():
            client_id = str(self._field(order, "client_order_id", "") or "").strip()
            if not client_id:
                # A broker OPEN order with no client id is foreign/manual
                # inventory: bucket it by broker id so the pass completes and
                # the operator sees exactly which order needs attention.
                broker_id = str(self._field(order, "id", "") or "").strip()
                categories["UNKNOWN_ORDER"].append(broker_id or "<no-broker-id>")
                continue
            if client_id in broker_open_ids:
                raise ReconciliationResultError("Broker OPEN list has duplicate client ID")
            broker_open_ids.add(client_id)
        for client_id in sorted(broker_open_ids - local_open_ids):
            categories["UNKNOWN_ORDER"].append(client_id)
        for client_id in sorted(local_open_ids - broker_open_ids):
            categories["MISSING_BROKER_ORDER"].append(client_id)
            # Documented compatibility contract: a local open id the broker no
            # longer lists is reported under both keys (see
            # the reconciliation buckets in docs/execution_architecture.md and
            # the recovery steps in docs/operations.md).
            categories["MISSING_LOCAL_ORDER"].append(client_id)
        if cost_basis_drift:
            categories[COST_BASIS_DRIFT_KEY] = sorted(cost_basis_drift)
        return categories
