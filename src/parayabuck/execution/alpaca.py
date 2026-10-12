from __future__ import annotations

import json
import math
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.alerting import emit_execution_alert
from parayabuck.execution.alpaca_client import AlpacaClient
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    BrokerWorkingBuyOrder,
    PortfolioOrderRequest,
)
from parayabuck.execution.risk_peak import RiskPeakService
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol, same_symbol
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.execution.alpaca")

_emit_alert = emit_execution_alert

from parayabuck.execution.alpaca_account import AccountService  # noqa: E402
from parayabuck.execution.alpaca_orders import OrderService  # noqa: E402
from parayabuck.execution.alpaca_reconciliation import ReconciliationService  # noqa: E402

from .alpaca_models import (  # noqa: E402
    DEAD_BROKER_ORDER_STATUSES,
    AmbiguousOrderState,
    DefiniteOrderRejection,
    OwnershipDriftError,
    ProtectionReconciliationError,
    RiskStatePersistenceError,
)


class AlpacaBroker:
    """Alpaca execution boundary using the engine's native models."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True,
        *,
        ledger: ExecutionLedger | None = None,
        portfolio_owned_quantities: Callable[[], dict[str, float]] | None = None,
        portfolio_open_orders: Callable[[], object] | None = None,
        portfolio_fill_recovered: (Callable[..., None] | None) = None,
        portfolio_order_quantity_adopted: (Callable[[str, float], None] | None) = None,
        portfolio_flat_reconciled: (Callable[[str, Market], object] | None) = None,
        portfolio_ownership_resolved: (Callable[[str, Market, float, float | None], float | None] | None) = None,
        bot_tradable_symbols: Callable[[], object] | None = None,
        runtime_state_dir: str | Path | None = None,
        peak_state_path: str | Path | None = None,
        client: Any | None = None,
    ) -> None:
        self._client_adapter = AlpacaClient(api_key, secret_key, paper=paper, client=client)
        self._risk_peak = RiskPeakService(
            paper=paper, ledger=ledger, peak_state_path=peak_state_path, runtime_state_dir=runtime_state_dir
        )
        self.ledger = ledger
        self.portfolio_owned_quantities = portfolio_owned_quantities
        self.portfolio_open_orders = portfolio_open_orders
        self.portfolio_fill_recovered = portfolio_fill_recovered
        self.portfolio_order_quantity_adopted = portfolio_order_quantity_adopted
        self.portfolio_flat_reconciled = portfolio_flat_reconciled
        self.portfolio_ownership_resolved = portfolio_ownership_resolved
        self.bot_tradable_symbols = bot_tradable_symbols
        self._account_id = ""
        # Peak read-modify-write is shared mutable state across the account,
        # trade-update and decision threads: the lock is created here so its
        # first use is never a racy lazy creation.
        self._peak_lock = threading.Lock()
        # Immutable snapshot cache: every fresh account() seeds it; callers
        # that tolerate bounded staleness (config-sanctioned entry-gate
        # freshness) may reuse it via snapshot_if_fresh() instead of issuing
        # another get_account + get_all_positions round-trip.
        self._snapshot_cache: tuple[AccountSnapshot, float] | None = None
        # Composition: services own the heavy lifting; facade delegates.
        self._account_service = AccountService(self)
        self._order_service = OrderService(self)
        self._reconciliation_service = ReconciliationService(self)

    @property
    def _client(self) -> Any:
        return self._client_adapter._client

    @_client.setter
    def _client(self, value: Any) -> None:
        self._client_adapter._client = value
        session = getattr(value, "_session", None)
        if session is not None:
            try:
                from parayabuck.utils.http import patch_session_timeout

                patch_session_timeout(session, timeout=15.0)
            except Exception:
                pass

    # -- Peak sidecar delegation (risk_peak service, facade API unchanged) --
    def _get_peak_service(self) -> RiskPeakService:
        """The peak service, bound to the broker's current ledger and account."""
        rp = self._risk_peak
        rp.ledger = self.ledger
        rp._broker_account_id = self._account_id or None
        return rp

    @property
    def _peak_state_path(self) -> Path:
        return self._risk_peak.peak_state_path

    @_peak_state_path.setter
    def _peak_state_path(self, value: Path) -> None:
        self._risk_peak.peak_state_path = Path(value)

    @property
    def _peak_equity(self) -> float:
        return float(self._risk_peak.peak_equity)

    @_peak_equity.setter
    def _peak_equity(self, value: float) -> None:
        self._risk_peak.peak_equity = float(value)

    @property
    def _peak_epoch(self) -> int:
        return int(self._risk_peak.peak_epoch)

    @_peak_epoch.setter
    def _peak_epoch(self, value: int) -> None:
        self._risk_peak.peak_epoch = int(value)

    @property
    def _peak_account_id(self) -> str | None:
        return self._risk_peak.peak_account_id

    @_peak_account_id.setter
    def _peak_account_id(self, value: str | None) -> None:
        self._risk_peak.peak_account_id = value

    @property
    def _peak_reconciled(self) -> bool:
        return bool(self._risk_peak.peak_reconciled)

    @_peak_reconciled.setter
    def _peak_reconciled(self, value: bool) -> None:
        self._risk_peak.peak_reconciled = bool(value)

    @property
    def account_suffix(self) -> str:
        return self._account_id[-4:] if self._account_id else ""

    def close(self) -> None:
        """Close the Alpaca REST session pool (no-op for fake clients)."""
        try:
            self._client_adapter.close()
        except Exception as exc:
            log.warning("Alpaca session close warning: %s", exc)

    @property
    def account_id(self) -> str:
        return self._account_id

    def portfolio_history(self, period: str, timeframe: str) -> list[tuple[datetime, float]]:
        """Broker equity history for display only; never feeds trading state.

        Points before the account was funded (zero equity) and non-finite
        values are dropped so the series starts at the first real balance.
        """
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        raw: Any = self._client.get_portfolio_history(GetPortfolioHistoryRequest(period=period, timeframe=timeframe))
        stamps = list(getattr(raw, "timestamp", None) or [])
        values = list(getattr(raw, "equity", None) or [])
        points: list[tuple[datetime, float]] = []
        for stamp, value in zip(stamps, values, strict=False):
            if value is None:
                continue
            equity = float(value)
            if not math.isfinite(equity) or equity <= 0:
                continue
            points.append((datetime.fromtimestamp(int(stamp), UTC), equity))
        return points

    def snapshot_if_fresh(self, max_age_seconds: float) -> AccountSnapshot | None:
        """Return the last fetched account snapshot if it is at most
        ``max_age_seconds`` old, else ``None``."""
        return self._account_service.snapshot_if_fresh(max_age_seconds)

    def account(self) -> AccountSnapshot:
        return self._account_service.account()

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        """Broker-authoritative increments introduced by Alpaca asset metadata."""
        asset = self._asset(symbol)
        if not bool(getattr(asset, "tradable", False)):
            raise ValueError(f"Asset is not tradable: {symbol}")
        minimum = float(getattr(asset, "min_order_size", None) or 0.000000001)
        quantity_increment = float(getattr(asset, "min_trade_increment", None) or 0.000000001)
        if getattr(asset, "fractionable", None) is False:
            # A non-fractionable asset only trades whole shares: a fractional
            # order would be refused by the broker after planning.
            minimum = max(minimum, 1.0)
            quantity_increment = max(quantity_increment, 1.0)
        minimum_notional = float(getattr(asset, "min_order_notional", None) or 1.0)
        return AssetTradingRules(minimum, quantity_increment, minimum_notional)

    def all_open_order_symbols(self) -> set[str]:
        """Broker-wide open order symbols (managed and manual).

        This is deliberately NOT the portfolio view: submission guards and
        the planner skip any symbol with a working order, managed or not,
        because the bot must not stack orders onto an existing flow.
        """
        orders = self._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
        return {
            canonical_symbol(str(self._field(order, "symbol", "")))
            for order in orders or ()
            if self._field(order, "symbol", "")
        }

    def _get_open_orders(self, request: Any) -> Any:
        """Use the adapter's exhaustive OPEN-order query for every broker path."""
        return self._client_adapter.get_open_orders(request)

    def working_buy_orders(self) -> tuple[BrokerWorkingBuyOrder, ...]:
        """Exhaustive broker BUY commitments; an unpriced order stays unknown.

        Bot-owned orders carry a ``parayabuck-`` client id. Foreign, manual,
        or identity-less working BUYs are never raised: when a price is
        knowable the order reserves capacity conservatively, otherwise it
        surfaces as unpriced (``reference_price=None``) so admission fails
        closed via the existing ``committed_buy_unpriced`` path. Every
        identity-less row gets its own synthesized id (registered in ``seen``),
        so two manual BUYs on one symbol never collide into a duplicate-ID
        error that would halt the runtime.
        """
        result: list[BrokerWorkingBuyOrder] = []
        seen: set[str] = set()
        for index, order in enumerate(self.working_orders()):
            if self._normalized_field(order, "side") != "buy":
                continue
            client_id = str(self._field(order, "client_order_id", "") or "").strip()
            symbol = str(self._field(order, "symbol", "") or "").strip()
            if not client_id or client_id in seen or not symbol:
                if client_id and client_id in seen:
                    raise ValueError("Invalid broker working BUY identity")
                broker_id = str(self._field(order, "id", "") or "").strip()
                foreign = self._foreign_working_buy(symbol, client_id or None, broker_id or str(index), seen)
                if foreign is not None:
                    seen.add(foreign.client_order_id)
                    result.append(foreign)
                continue
            seen.add(client_id)
            parsed = self._parse_working_buy(order, client_id, symbol)
            if parsed is not None:
                result.append(parsed)
        return tuple(result)

    def _foreign_working_buy(
        self, symbol: str, client_id: str | None, broker_ref: str, seen: set[str]
    ) -> BrokerWorkingBuyOrder | None:
        """Conservatively price an identity-less/foreign working BUY.

        ``None`` when the symbol itself is unusable; otherwise a row with
        ``reference_price=None`` so admission treats it as unpriced and
        blocks new BUYs instead of crashing or silently ignoring it. A row
        without a client id is keyed ``foreign:{symbol}:{broker_ref}`` (the
        broker order id, else its list position), unique against ``seen``.
        """
        if not symbol:
            return None
        try:
            market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
            canonical = canonical_symbol(symbol, market)
        except ValueError:
            log.warning("Skipping working BUY with unusable symbol %r", symbol)
            return None
        log.warning(
            "Foreign working BUY treated as unpriced: symbol=%s client_order_id=%s",
            canonical,
            client_id or "<missing>",
        )
        synthesized = client_id or f"foreign:{canonical}:{broker_ref}"
        suffix = 1
        while synthesized in seen:
            synthesized = f"foreign:{canonical}:{broker_ref}:{suffix}"
            suffix += 1
        return BrokerWorkingBuyOrder(
            client_order_id=synthesized,
            symbol=canonical,
            market=market,
            remaining_quantity=None,
            reference_price=None,
            remaining_notional=None,
        )

    def _parse_working_buy(self, order: Any, client_id: str, symbol: str) -> BrokerWorkingBuyOrder | None:
        """Parse one bot-owned working BUY; unpriceable rows stay unpriced."""
        try:
            market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
            canonical = canonical_symbol(symbol, market)
        except ValueError:
            log.warning("Skipping working BUY with unusable symbol %r", symbol)
            return None
        try:
            filled = float(self._field(order, "filled_qty", 0) or 0)
        except (TypeError, ValueError):
            filled = math.nan
        raw_qty = self._field(order, "qty", None)
        try:
            quantity = float(raw_qty) if raw_qty is not None else None
        except (TypeError, ValueError):
            quantity = math.nan
        raw_notional = self._field(order, "notional", None)
        try:
            notional = float(raw_notional) if raw_notional is not None else None
        except (TypeError, ValueError):
            notional = math.nan
        prices = self._working_buy_prices(order)
        quantity_ok = quantity is None or (
            math.isfinite(quantity) and quantity >= 0 and math.isfinite(filled) and filled >= 0
        )
        if quantity is not None and quantity_ok and math.isfinite(filled):
            quantity_ok = filled <= quantity + max(1e-9, quantity * 1e-8)
        notional_ok = notional is None or (math.isfinite(notional) and notional >= 0)
        filled_ok = math.isfinite(filled) and filled >= 0
        if (
            not filled_ok
            or not quantity_ok
            or not notional_ok
            or any(not math.isfinite(price) or price <= 0 for price in prices)
            or (quantity is None and notional is None)
        ):
            log.warning("Unpriced working BUY treated as unknown commitment: %s", client_id)
            return BrokerWorkingBuyOrder(
                client_order_id=client_id,
                symbol=canonical,
                market=market,
                remaining_quantity=None,
                reference_price=None,
                remaining_notional=None,
            )
        average_raw = self._field(order, "filled_avg_price", None)
        try:
            average = float(average_raw) if average_raw is not None else None
        except (TypeError, ValueError):
            average = math.nan
        if average is not None and (not math.isfinite(average) or average <= 0):
            average = None
        filled_notional = filled * average if average is not None else 0.0
        remaining_notional = max(0.0, float(notional) - filled_notional) if notional is not None else None
        return BrokerWorkingBuyOrder(
            client_order_id=client_id,
            symbol=canonical,
            market=market,
            remaining_quantity=max(0.0, float(quantity) - filled) if quantity is not None else None,
            reference_price=max(prices) if prices else None,
            remaining_notional=remaining_notional,
        )

    def _working_buy_prices(self, order: Any) -> list[float]:
        prices: list[float] = []
        for field in ("limit_price", "stop_price", "filled_avg_price"):
            value = self._field(order, field, None)
            if value is None:
                continue
            try:
                prices.append(float(value))
            except (TypeError, ValueError):
                prices.append(math.nan)
        return prices

    def working_orders(self) -> tuple[Any, ...]:
        """Complete OPEN order collection for operator slot/order counts."""
        return tuple(self._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)))

    def _reconcile_stale_portfolio_ownership(self, symbol: str, market: Market) -> bool:
        """Resolve one stale portfolio symbol against real broker state.

        Returns True when the symbol is handled (real fill recovered, or an
        order is genuinely still working at the broker) and False when the
        caller should record an ownership adjustment.

        The race this handles: the broker filled a stop while the
        TradingStream update was delayed, the broker became flat, and the
        repository still claims ownership with a non-terminal exit order.
        """
        provider = getattr(self, "portfolio_open_orders", None)
        if provider is None:
            return False
        orders = [
            order
            for order in provider() or ()
            if str(getattr(order, "symbol", "")) == symbol
            and getattr(order, "market", None) in {None, market}
            and str(getattr(order, "side", "")).lower() == "sell"
        ]
        if not orders:
            return False
        for order in orders:
            client_order_id = str(getattr(order, "client_order_id", ""))
            broker_order_id = str(getattr(order, "broker_order_id", ""))
            if not client_order_id:
                continue
            broker_order = self._fetch_broker_order(client_order_id, broker_order_id)
            if broker_order is None:
                # No information at the broker about this order; do not guess.
                continue
            status = self._normalized_field(broker_order, "status")
            if status == "filled":
                recovered = self._fill_from_broker_order(broker_order, client_order_id, broker_order_id)
                if recovered is not None:
                    if fill_recovered := getattr(self, "portfolio_fill_recovered", None):
                        fill_recovered(*recovered)
                    return True
                continue
            if status not in DEAD_BROKER_ORDER_STATUSES:
                # The exit is genuinely still working at the broker; keep
                # ownership until it completes.
                return True
            # The order died: book any partial fill it made before dying,
            # then fall through to the adjustment for the remainder.
            self._recover_terminal_partial_fill(broker_order, client_order_id, broker_order_id, status)
        return False

    def _recover_terminal_partial_fill(
        self, broker_order: Any, client_order_id: str, broker_order_id: str, status: str
    ) -> None:
        """Book the real fill of an order that ended terminal after filling.

        A canceled/expired/done_for_day order can still have filled part of
        its quantity; dropping that fill orphans bought units as external
        inventory or leaves sold units owned (ownership drift). The booking
        is cumulative and keyed by the recovered activity id, so a later
        stream or reconcile delivery of the same fill books nothing twice.
        """
        recovered = self._fill_from_broker_order(broker_order, client_order_id, broker_order_id)
        if recovered is None:
            return
        if fill_recovered := getattr(self, "portfolio_fill_recovered", None):
            fill_recovered(*recovered, status=status)

    def _fetch_broker_order(self, client_order_id: str, broker_order_id: str) -> Any | None:
        """Resolve one repo order at Alpaca. A 404/unknown order is ``None``;
        network failures propagate so reconciliation never guesses."""
        get_by_client = getattr(self._client, "get_order_by_client_id", None)
        if get_by_client is not None:
            try:
                order = get_by_client(client_order_id)
            except Exception as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise
                order = None
            if order is not None:
                return order
        if broker_order_id:
            get_by_id = getattr(self._client, "get_order_by_id", None)
            if get_by_id is not None:
                try:
                    return get_by_id(broker_order_id)
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 404:
                        raise
        return None

    def resolve_portfolio_submission(
        self, client_order_id: str, broker_order_id: str = ""
    ) -> tuple[str, str] | str | None:
        """Resolve a portfolio order row left at ``submitting`` by a crash
        between repository persistence and broker acknowledgement.

        The deterministic client order id proves whether the order ever
        reached Alpaca:

        * unknown at the broker (404) -> ``"canceled"``: it can never fill;
        * present and filled -> the real fill is recovered through
          ``portfolio_fill_recovered`` (the same path startup reconciliation
          uses) and ``"filled"`` is returned;
        * present and terminal (canceled/expired/rejected/done_for_day) ->
          that status;
        * present and still working -> ``"submitted"``.

        A status read from a broker order comes back as
        ``(status, broker_order_id)`` whenever the broker reports an id.
        Returns ``None`` when the broker is unreachable or the lookup
        capability is missing; the caller retries later without guessing.
        """
        get_by_client = getattr(self._client, "get_order_by_client_id", None)
        if get_by_client is None:
            return None
        broker_order = self._fetch_broker_order(client_order_id, broker_order_id)
        if broker_order is None:
            return "canceled"
        status = str(self._normalized_field(broker_order, "status") or "").lower()
        # The caller persists the returned broker id verbatim; a crashed
        # submit has none stored, so hand back the broker's own id rather
        # than letting the stored "" erase the id the fill recovery booked.
        broker_id = str(self._field(broker_order, "id", "") or broker_order_id)

        def outcome(resolved: str) -> tuple[str, str] | str:
            return (resolved, broker_id) if broker_id else resolved

        if status == "filled":
            recovered = self._fill_from_broker_order(broker_order, client_order_id, broker_order_id)
            if recovered is not None:
                if fill_recovered := getattr(self, "portfolio_fill_recovered", None):
                    fill_recovered(*recovered)
                return outcome("filled")
            # The order filled but its fill could not be extracted; keep the
            # row non-terminal so ownership is not silently released.
            return outcome("submitted")
        if status in DEAD_BROKER_ORDER_STATUSES:
            self._recover_terminal_partial_fill(broker_order, client_order_id, broker_order_id, status)
            return outcome(status)
        return outcome("submitted")

    def _fill_from_broker_order(
        self, order: Any, client_order_id: str, broker_order_id: str
    ) -> tuple[str, str, float, float, datetime, str, float | None] | None:
        """Extract the real fill from a filled Alpaca order.

        ``None`` when the fill facts are missing, unparseable or non-finite:
        the caller then leaves the row non-terminal instead of guessing.
        """
        try:
            filled_qty = float(self._field(order, "filled_qty", 0) or 0)
            avg_price = float(self._field(order, "filled_avg_price", 0) or 0)
        except (TypeError, ValueError):
            log.warning("Unparseable broker fill facts for %s; fill not extracted", client_order_id)
            return None
        if not math.isfinite(filled_qty) or not math.isfinite(avg_price) or filled_qty <= 0 or avg_price <= 0:
            return None
        # A partially filled order that then died has no ``filled_at``; fall
        # back to its last update rather than parsing ``None``.
        filled_at_raw = (
            self._field(order, "filled_at", None)
            or self._field(order, "updated_at", None)
            or self._field(order, "submitted_at", None)
            or datetime.now(UTC)
        )
        occurred_at = (
            filled_at_raw if isinstance(filled_at_raw, datetime) else datetime.fromisoformat(str(filled_at_raw))
        )
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return (
            client_order_id,
            str(self._field(order, "id", "") or broker_order_id),
            filled_qty,
            avg_price,
            occurred_at,
            f"{client_order_id}:recovered:{filled_qty}",
            # An order lookup does not establish a fee: ``None`` records it as
            # unobserved, where ``0.0`` would claim an observed free fill and
            # corrupt execution-quality measurement.
            None,
        )

    def portfolio_open_order_symbols(self) -> set[str]:
        """Symbols with a non-terminal order owned by the portfolio path.

        Repository/account-aware: manual broker orders never appear here, so
        they cannot pin universe slots or suppress ownership drift.
        """
        provider = getattr(self, "portfolio_open_orders", None)
        if provider is None:
            return set()
        orders = provider() or ()
        return {canonical_symbol(str(getattr(order, "symbol", ""))) for order in orders if getattr(order, "symbol", "")}

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        """Submit one simple long/cash rebalance leg with ambiguity recovery."""
        return self._order_service.submit_portfolio_order(order)

    def reconcile(self) -> dict[str, list[str]]:
        """Synchronize broker and portfolio state after any drift."""
        return self._reconciliation_service.reconcile()

    def _asset(self, symbol: str) -> Any:
        return self._client_adapter._asset(symbol)

    @staticmethod
    def _available_qty_from_error(exc: Exception) -> float:
        """Extract the broker-reported available quantity from a rejection.

        Alpaca rejects an oversized sell with a JSON body whose ``available``
        value lives inside the message TEXT (``{"code": 40211000, "message":
        "insufficient qty available for base (requested: X, available: Y)"}``),
        not as a top-level key. Both shapes are accepted so synthetic and
        broker-native errors resolve identically.
        """
        message = str(exc)
        start = message.find("{")
        if start >= 0:
            try:
                payload = json.loads(message[start:])
                if "available" in payload:
                    return max(0.0, float(payload["available"]))
                message = str(payload.get("message", ""))
            except (TypeError, ValueError):
                pass
        match = re.search(r"available:\s*([0-9]+(?:\.[0-9]+)?)", message)
        return max(0.0, float(match.group(1))) if match else 0.0

    @staticmethod
    def _is_definite_client_rejection(exc: BaseException) -> bool:
        """True when the submit failure carries a broker HTTP 4xx.

        4xx is permanent refusal (validation, entitlement, forbidden) rather
        than an infrastructure blip, so combined with a recovery lookup that
        proves the client order id was never accepted the outcome is KNOWN:
        no order exists at the broker. 429/408 stay retryable and 5xx stays
        transient, so neither is classified as a rejection.
        """
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            status = getattr(current, "status_code", None)
            if status is None:
                status = getattr(getattr(current, "response", None), "status_code", None)
            if status is not None:
                try:
                    code = int(status)
                except (TypeError, ValueError):
                    return False
                return 400 <= code < 500 and code not in {408, 429}
            current = current.__cause__ or current.__context__
        return False

    def _is_definite_refusal(self, exc: Exception) -> bool:
        """A submit failure that proves the broker refused this request: an
        HTTP 4xx, or Alpaca's insufficient-quantity validation body."""
        return self._is_definite_client_rejection(exc) or self._available_qty_from_error(exc) > 0

    def _submit_with_recovery(self, request: Any, client_order_id: str) -> Any:
        try:
            return self._client.submit_order(request)
        except Exception as submit_exc:
            # Every failure — a validation refusal included — is resolved by
            # the deterministic client id first: a timed-out earlier submit
            # may have been accepted, and its working order is what holds the
            # shares an "insufficient qty available" refusal complains about.
            get_by_client_id = getattr(self._client, "get_order_by_client_id", None)
            if get_by_client_id is not None:
                lookup_error: Exception | None = None
                try:
                    recovered: Any = get_by_client_id(client_order_id)
                except Exception as lookup_exc:
                    recovered = None
                    lookup_error = lookup_exc
                if recovered is not None:
                    status = self._normalized_field(recovered, "status")
                    if status in DEAD_BROKER_ORDER_STATUSES:
                        try:
                            filled = float(self._field(recovered, "filled_qty", 0) or 0)
                        except (TypeError, ValueError):
                            filled = math.nan
                        if not math.isfinite(filled):
                            # Unreadable fill facts on a dead order: the fill
                            # outcome is unknown, so the write stays ambiguous.
                            _emit_alert(
                                "ambiguous_order",
                                message=(
                                    f"Terminal broker order {client_order_id} has unreadable fill facts "
                                    f"after submit error: {submit_exc}"
                                ),
                                client_order_id=client_order_id,
                                reason="broker_fill_facts_unreadable",
                            )
                            raise AmbiguousOrderState(
                                f"Terminal broker order {client_order_id} has unreadable filled_qty "
                                f"after submit error: {submit_exc}"
                            ) from submit_exc
                        if filled > 0:
                            # The order died after a partial fill: the fill is
                            # real and must be booked, and the real terminal
                            # order is the outcome (not a rejection).
                            broker_id = str(self._field(recovered, "id", "") or "")
                            self._recover_terminal_partial_fill(recovered, client_order_id, broker_id, status)
                            log.warning(
                                "Recovered terminal Alpaca order %s (%s, filled_qty=%s) after submit error: %s",
                                client_order_id,
                                status,
                                filled,
                                submit_exc,
                            )
                            return recovered
                        # A broker-confirmed terminal response is known, not
                        # ambiguous. A definite refusal is a rejection: the
                        # runtime books it rejected and keeps running instead
                        # of halting on portfolio_runtime_failure.
                        if self._is_definite_refusal(submit_exc):
                            raise DefiniteOrderRejection(
                                f"Broker rejected order {client_order_id} ({status}): {submit_exc}"
                            ) from submit_exc
                        # Otherwise preserve the original submit failure.
                        raise submit_exc
                    log.warning(
                        "Recovered Alpaca order %s after submit error: %s",
                        client_order_id,
                        submit_exc,
                    )
                    return recovered
                if getattr(lookup_error, "status_code", None) == 404:
                    # The deterministic client id was never accepted: the
                    # original submit failure is the definite outcome, not an
                    # ambiguity (mirrors resolve_portfolio_submission).
                    if self._is_definite_refusal(submit_exc):
                        raise DefiniteOrderRejection(
                            f"Broker rejected order {client_order_id}: {submit_exc}"
                        ) from submit_exc
                    raise submit_exc
                if lookup_error is not None:
                    log.warning(
                        "Recovery lookup failed for %s after submit error: %s",
                        client_order_id,
                        lookup_error,
                    )
            _emit_alert(
                "ambiguous_order",
                message=(f"Broker write outcome unknown for client_order_id={client_order_id}: {submit_exc}"),
                client_order_id=client_order_id,
                reason="broker_write_outcome_unknown",
            )
            raise AmbiguousOrderState(
                f"Broker write outcome unknown for client_order_id={client_order_id}: {submit_exc}"
            ) from submit_exc

    def _has_open_order(self, symbol: str) -> bool:
        orders = self._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True))
        if not orders:
            return False
        return any(same_symbol(str(self._field(order, "symbol", symbol)), symbol) for order in orders)

    def _normalized_field(self, value: Any, name: str) -> str:
        return self._client_adapter._normalized_field(value, name)

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        return AlpacaClient._field(value, name, default)

    def _ensure_peak_reconciled(self, account_id: str) -> None:
        svc = self._get_peak_service()
        svc.ensure_reconciled(account_id)

    def _save_peak_equity(self) -> None:
        svc = self._get_peak_service()
        acct = svc.peak_account_id or self._account_id
        svc.save_peak_equity(str(acct) if acct else None)

    def reset_peak_equity(self, equity: float) -> None:
        # Same lock as the account() peak read-modify-write: an operator reset
        # must never interleave with a concurrent poll and be lost (or lose
        # the poll's higher peak).
        with self._peak_lock:
            svc = self._get_peak_service()
            acct = svc.peak_account_id or self._account_id
            svc.reset_peak_equity(equity, str(acct) if acct else None)


__all__ = [
    "AlpacaBroker",
    "AmbiguousOrderState",
    "DefiniteOrderRejection",
    "OwnershipDriftError",
    "ProtectionReconciliationError",
    "RiskStatePersistenceError",
]
