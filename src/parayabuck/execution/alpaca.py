from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from parayabuck.core.models import AccountSnapshot, Market
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

from parayabuck.execution.alpaca_account import AccountService  # noqa: E402
from parayabuck.execution.alpaca_orders import OrderService  # noqa: E402
from parayabuck.execution.alpaca_protection import ProtectionService  # noqa: E402
from parayabuck.execution.alpaca_reconciliation import ReconciliationService  # noqa: E402

from .alpaca_models import (  # noqa: E402
    AmbiguousOrderState,
    DefiniteOrderRejection,
    OwnershipDriftError,
    ProtectionReconciliationError,
    RiskStatePersistenceError,
)

# Broker order states that can never fill again (``rejected`` never filled).
_DEAD_ORDER_STATUSES = frozenset({"canceled", "cancelled", "expired", "rejected", "done_for_day"})


def _emit_alert(kind: str, *, message: str, **fields: object) -> None:
    """Emit one execution alert without ever affecting order flow.

    The ``parayabuck.monitoring.alerts`` import is guarded so a missing (or
    broken) alerts module degrades to a logged no-op instead of failing a
    submission or a fill.
    """
    try:
        from parayabuck.monitoring import alerts
    except ImportError:
        log.debug("alerts module unavailable; skipping %s alert", kind)
        return
    try:
        alerts.emit(kind, message=message, **fields)
    except Exception:
        log.warning("Alert emission failed for kind=%s (order flow unaffected)", kind, exc_info=True)


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
        client_adapter: Any | None = None,
    ) -> None:
        if client_adapter is not None:
            self._client_adapter = client_adapter
        elif client is not None:
            self._client_adapter = AlpacaClient(api_key, secret_key, paper=paper, client=client)
        else:
            self._client_adapter = AlpacaClient(api_key, secret_key, paper=paper)
        self._paper = paper
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
        # Immutable snapshot cache: every fresh account() seeds it; callers
        # that tolerate bounded staleness (config-sanctioned entry-gate
        # freshness) may reuse it via snapshot_if_fresh() instead of issuing
        # another get_account + get_all_positions round-trip.
        self._snapshot_cache: AccountSnapshot | None = None
        self._snapshot_cached_at: float | None = None
        # Composition: services own the heavy lifting; facade delegates.
        self._account_service = AccountService(self)
        self._order_service = OrderService(self)
        self._protection_service = ProtectionService(self)
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
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            try:
                rp.ledger = self.__dict__.get("ledger", self.__dict__.get("_ledger"))
            except Exception:
                pass
            try:
                rp._paper = bool(self.__dict__.get("_paper", True))
            except Exception:
                pass
            try:
                acct = self.__dict__.get("_account_id", "")
                rp._broker_account_id = acct if acct else None
            except Exception:
                pass
            return rp  # type: ignore[no-any-return]
        paper = bool(self.__dict__.get("_paper", True))
        ledger = self.__dict__.get("ledger", self.__dict__.get("_ledger"))
        peak_path = self.__dict__.get("_peak_state_path")
        if peak_path is None and "_peak_state_path" in self.__dict__:
            peak_path = self.__dict__["_peak_state_path"]
        rp = RiskPeakService(paper=paper, ledger=ledger, peak_state_path=peak_path)
        # Override eager-loaded state with broker's own attributes (for __new__ test isolation).
        # Base __new__ brokers start with no peak binding; service eager load would otherwise
        # retain stale file account_id (e.g. 4edcc...) and cause false fail-closed.
        for attr, svc_attr in [
            ("_peak_equity", "_peak_equity"),
            ("_peak_epoch", "_peak_epoch"),
            ("_peak_account_id", "_peak_account_id"),
            ("_peak_reconciled", "_peak_reconciled"),
        ]:
            if attr in self.__dict__:
                try:
                    setattr(rp, svc_attr, self.__dict__[attr])
                except Exception:
                    pass
            else:
                # No broker attribute -> reset to clean defaults, discarding eager file load
                # (matches base __new__ semantics where _peak_account_id is None)
                try:
                    if svc_attr == "_peak_equity":
                        rp._peak_equity = 0.0
                    elif svc_attr == "_peak_epoch":
                        rp._peak_epoch = 0
                    elif svc_attr == "_peak_account_id":
                        rp._peak_account_id = None
                    elif svc_attr == "_peak_reconciled":
                        rp._peak_reconciled = False
                except Exception:
                    pass
        try:
            acct = self.__dict__.get("_account_id", "")
            rp._broker_account_id = acct if acct else None
        except Exception:
            pass
        self.__dict__["_risk_peak"] = rp
        return rp

    @property
    def _peak_state_path(self) -> Path:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return rp.peak_state_path  # type: ignore[no-any-return]
        return self.__dict__.get("_peak_state_path", Path("data/runtime/paper_equity_peak.json"))  # type: ignore[no-any-return]

    @_peak_state_path.setter
    def _peak_state_path(self, value: Path) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_state_path = Path(value)
        else:
            self.__dict__["_peak_state_path"] = Path(value)

    @property
    def _peak_equity(self) -> float:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return float(rp.peak_equity)
        return float(self.__dict__.get("_peak_equity", 0.0))

    @_peak_equity.setter
    def _peak_equity(self, value: float) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_equity = float(value)
        else:
            self.__dict__["_peak_equity"] = float(value)

    @property
    def _peak_epoch(self) -> int:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return int(rp.peak_epoch)
        return int(self.__dict__.get("_peak_epoch", 0))

    @_peak_epoch.setter
    def _peak_epoch(self, value: int) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_epoch = int(value)
        else:
            self.__dict__["_peak_epoch"] = int(value)

    @property
    def _peak_account_id(self) -> str | None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return rp.peak_account_id  # type: ignore[no-any-return]
        return self.__dict__.get("_peak_account_id")

    @_peak_account_id.setter
    def _peak_account_id(self, value: str | None) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_account_id = value
        else:
            self.__dict__["_peak_account_id"] = value

    @property
    def _peak_reconciled(self) -> bool:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return bool(rp.peak_reconciled)
        return bool(self.__dict__.get("_peak_reconciled", False))

    @_peak_reconciled.setter
    def _peak_reconciled(self, value: bool) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_reconciled = bool(value)
        else:
            self.__dict__["_peak_reconciled"] = bool(value)

    @property
    def account_suffix(self) -> str:
        return self._account_id[-4:] if self._account_id else ""

    def close(self) -> None:
        """Close the Alpaca REST session pool (no-op for fake clients)."""
        adapter = self.__dict__.get("_client_adapter")
        if adapter is not None:
            try:
                adapter.close()
            except Exception as exc:
                log.warning("Alpaca session close warning: %s", exc)
            return
        session = getattr(self._client, "_session", None)
        if session is None:
            return
        try:
            session.close()
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
        price_increment = float(getattr(asset, "price_increment", None) or 0.01)
        minimum_notional = float(getattr(asset, "min_order_notional", None) or 1.0)
        return AssetTradingRules(
            canonical_symbol(symbol, market),
            market,
            minimum,
            quantity_increment,
            price_increment,
            minimum_notional,
        )

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
        adapter = self.__dict__.get("_client_adapter")
        getter = getattr(adapter, "get_open_orders", None) if adapter is not None else None
        if getter is None:
            raise RuntimeError("Alpaca adapter does not support exhaustive open-order retrieval")
        return getter(request)

    def working_buy_orders(self) -> tuple[BrokerWorkingBuyOrder, ...]:
        """Exhaustive broker BUY commitments; an unpriced order stays unknown."""
        result: list[BrokerWorkingBuyOrder] = []
        seen: set[str] = set()
        for order in self.working_orders():
            if self._normalized_field(order, "side") != "buy":
                continue
            client_id = str(self._field(order, "client_order_id", "") or "").strip()
            symbol = str(self._field(order, "symbol", "") or "").strip()
            if not client_id or client_id in seen or not symbol:
                raise ValueError("Invalid broker working BUY identity")
            seen.add(client_id)
            market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
            filled = float(self._field(order, "filled_qty", 0) or 0)
            raw_qty = self._field(order, "qty", None)
            quantity = float(raw_qty) if raw_qty is not None else None
            raw_notional = self._field(order, "notional", None)
            notional = float(raw_notional) if raw_notional is not None else None
            prices = [
                float(value)
                for field in ("limit_price", "stop_price", "filled_avg_price")
                if (value := self._field(order, field, None)) is not None
            ]
            if (
                not math.isfinite(filled)
                or filled < 0
                or (quantity is not None and (not math.isfinite(quantity) or quantity < filled))
                or (notional is not None and (not math.isfinite(notional) or notional < 0))
                or any(not math.isfinite(price) or price <= 0 for price in prices)
                or (quantity is None and notional is None)
            ):
                raise ValueError(f"Invalid broker working BUY quantity or price for {client_id}")
            average = self._field(order, "filled_avg_price", None)
            remaining_notional = (
                max(0.0, notional - filled * float(average))
                if notional is not None and (filled == 0 or average is not None)
                else None
            )
            result.append(
                BrokerWorkingBuyOrder(
                    client_order_id=client_id,
                    symbol=canonical_symbol(symbol, market),
                    market=market,
                    remaining_quantity=max(0.0, quantity - filled) if quantity is not None else None,
                    reference_price=max(prices) if prices else None,
                    remaining_notional=remaining_notional,
                )
            )
        return tuple(result)

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
            if status not in _DEAD_ORDER_STATUSES:
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
        if status in _DEAD_ORDER_STATUSES:
            self._recover_terminal_partial_fill(broker_order, client_order_id, broker_order_id, status)
            return outcome(status)
        return outcome("submitted")

    def _fill_from_broker_order(
        self, order: Any, client_order_id: str, broker_order_id: str
    ) -> tuple[str, str, float, float, datetime, str, float | None] | None:
        """Extract the real fill from a filled Alpaca order."""
        filled_qty = float(self._field(order, "filled_qty", 0) or 0)
        avg_price = float(self._field(order, "filled_avg_price", 0) or 0)
        if filled_qty <= 0 or avg_price <= 0:
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
        svc = self.__dict__.get("_order_service")
        if svc is None:
            svc = OrderService(self)
            self.__dict__["_order_service"] = svc
        return svc.submit_portfolio_order(order)

    def reconcile(self) -> dict[str, list[str]]:
        """Synchronize broker, stream and ledger state after any drift."""
        svc = self.__dict__.get("_reconciliation_service")
        if svc is None:
            svc = ReconciliationService(self)
            self.__dict__["_reconciliation_service"] = svc
        return svc.reconcile()

    def _has_nested_bracket_protection(
        self,
        parent_order_ids: str | set[str],
        symbol: str,
        position_is_long: bool,
        required_qty: float,
    ) -> bool:
        svc = self.__dict__.get("_protection_service") or ProtectionService(self)
        return svc._has_nested_bracket_protection(parent_order_ids, symbol, position_is_long, required_qty)

    def _asset(self, symbol: str) -> Any:
        return self._client_adapter._asset(symbol)

    def _price_step(self, symbol: str, market: Market, reference_price: float) -> Decimal:
        return self._client_adapter._price_step(symbol, market, reference_price)  # type: ignore[no-any-return]

    @staticmethod
    def _round_to_step(value: float, step: Decimal) -> float:
        return AlpacaClient._round_to_step(value, step)

    def _submit_stop(
        self,
        symbol: str,
        market: Market,
        qty: float,
        stop_price: float,
        position_is_long: bool,
        client_order_id: str | None = None,
    ) -> str | None:
        """Submit an equity protective stop; return its client order id or ``None``."""
        svc = self.__dict__.get("_protection_service") or ProtectionService(self)
        return svc.submit_stop(symbol, market, qty, stop_price, position_is_long, client_order_id=client_order_id)

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

    def _submit_with_recovery(self, request: Any, client_order_id: str) -> Any:
        try:
            return self._client.submit_order(request)
        except Exception as submit_exc:
            # Structured broker validation failures are definite rejections:
            # Alpaca did not accept the requested quantity, so recovery may
            # safely size down without risking a duplicate order.
            if self._available_qty_from_error(submit_exc) > 0:
                raise
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
                    if status in {"rejected", "canceled", "cancelled", "expired", "done_for_day"}:
                        # A broker-confirmed terminal response is known, not
                        # ambiguous. A 4xx refusal is a DEFINITE rejection:
                        # the runtime books it rejected and keeps running
                        # instead of halting on portfolio_runtime_failure.
                        if self._is_definite_client_rejection(submit_exc):
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
                    if self._is_definite_client_rejection(submit_exc):
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

    def _has_protective_order(
        self,
        symbol: str,
        position_is_long: bool,
        required_qty: float,
        open_orders: Any = None,
    ) -> bool:
        svc = self.__dict__.get("_protection_service") or ProtectionService(self)
        return svc._has_protective_order(symbol, position_is_long, required_qty, open_orders)

    def _cancel_protective_orders(self, symbol: str, position_is_long: bool) -> None:
        svc = self.__dict__.get("_protection_service") or ProtectionService(self)
        svc._cancel_protective_orders(symbol, position_is_long)

    def _normalized_field(self, value: Any, name: str) -> str:
        return self._client_adapter._normalized_field(value, name)  # type: ignore[no-any-return]

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        try:
            return AlpacaClient._field(value, name, default)
        except Exception:
            from parayabuck.utils.objects import field_value as _fv

            return _fv(value, name, default)

    def _ensure_peak_reconciled(self, account_id: str, current_equity: float) -> None:
        svc = self._get_peak_service()
        svc.ensure_reconciled(account_id, current_equity)

    def _save_peak_equity(self) -> None:
        svc = self._get_peak_service()
        try:
            svc._broker_account_id = self.__dict__.get("_account_id") or None
        except Exception:
            pass
        acct = getattr(svc, "_peak_account_id", None) or self.__dict__.get("_account_id")
        svc.save_peak_equity(str(acct) if acct else None)
        try:
            self.__dict__["_peak_equity"] = float(svc.peak_equity)
            self.__dict__["_peak_epoch"] = int(svc.peak_epoch)
            if svc.peak_account_id is not None:
                self.__dict__["_peak_account_id"] = svc.peak_account_id
            self.__dict__["_peak_reconciled"] = bool(svc.peak_reconciled)
        except Exception:
            pass

    def reset_peak_equity(self, equity: float) -> None:
        svc = self._get_peak_service()
        try:
            svc._broker_account_id = self.__dict__.get("_account_id") or None
        except Exception:
            pass
        acct = getattr(svc, "_peak_account_id", None) or self.__dict__.get("_account_id")
        svc.reset_peak_equity(equity, str(acct) if acct else None)
        try:
            self.__dict__["_peak_equity"] = float(svc.peak_equity)
            self.__dict__["_peak_epoch"] = int(svc.peak_epoch)
            if svc.peak_account_id is not None:
                self.__dict__["_peak_account_id"] = svc.peak_account_id
            self.__dict__["_peak_reconciled"] = bool(svc.peak_reconciled)
        except Exception:
            pass


__all__ = [
    "AlpacaBroker",
    "AmbiguousOrderState",
    "DefiniteOrderRejection",
    "OwnershipDriftError",
    "ProtectionReconciliationError",
    "RiskStatePersistenceError",
]
