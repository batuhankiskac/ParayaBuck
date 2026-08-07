from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrderByIdRequest,
    GetOrdersRequest,
    MarketOrderRequest,
    ReplaceOrderRequest,
    StopLimitOrderRequest,
    StopLossRequest,
    StopOrderRequest,
    TakeProfitRequest,
)

from parayabuck.core.models import (
    AccountSnapshot,
    Direction,
    ExecutionEvent,
    Fill,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
    PositionSnapshot,
    ProtectionState,
)
from parayabuck.execution.ledger import ExecutionLedger, ManagedPosition
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol, normalize_symbol, same_symbol
from parayabuck.utils import is_crypto
from parayabuck.utils.http import patch_session_timeout
from parayabuck.utils.objects import field_value

log = get_logger("parayabuck.execution.alpaca")

_CRYPTO_TAKER_FEE_RESERVE = Decimal("0.0025")


class AmbiguousOrderState(RuntimeError):
    pass


class ProtectionReconciliationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CryptoPriceObservation:
    symbol: str
    high: float
    low: float
    source: str
    timestamp: datetime


@dataclass(frozen=True)
class QuantityResolution:
    verified: bool
    available_qty: float
    source: str


class AlpacaBroker:
    """Alpaca execution boundary using the engine's native models."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True,
        *,
        manage_external_positions: bool = False,
        ledger: ExecutionLedger | None = None,
        crypto_stop_limit_offset_bps: float = 25.0,
        crypto_emergency_exit_bps: float = 75.0,
        crypto_stop_cancel_timeout_seconds: float = 5.0,
        crypto_stop_cancel_poll_interval_seconds: float = 0.25,
    ) -> None:
        self._client = TradingClient(api_key, secret_key, paper=paper)
        patch_session_timeout(self._client._session, timeout=15.0)
        self._peak_state_path = Path("data/runtime") / (
            "paper_equity_peak.json" if paper else "live_equity_peak.json"
        )
        self._peak_equity = self._load_peak_equity()
        self._asset_cache: dict[str, Any] = {}
        self.manage_external_positions = manage_external_positions
        self.ledger = ledger
        self._account_id = ""
        self.crypto_stop_limit_offset_bps = crypto_stop_limit_offset_bps
        self.crypto_emergency_exit_bps = crypto_emergency_exit_bps
        self.crypto_stop_cancel_timeout_seconds = crypto_stop_cancel_timeout_seconds
        self.crypto_stop_cancel_poll_interval_seconds = crypto_stop_cancel_poll_interval_seconds
        self._symbol_exit_locks: dict[str, threading.Lock] = {}

    @property
    def account_suffix(self) -> str:
        return self._account_id[-4:] if self._account_id else ""

    def close(self) -> None:
        """Close the Alpaca REST session pool (no-op for fake clients)."""
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

    def account(self) -> AccountSnapshot:
        account: Any = self._client.get_account()
        self._account_id = str(getattr(account, "id", "") or getattr(account, "account_number", ""))
        if (
            bool(getattr(account, "account_blocked", False))
            or bool(getattr(account, "trading_blocked", False))
            or bool(getattr(account, "trade_suspended_by_user", False))
        ):
            raise RuntimeError("Alpaca account is blocked from trading")
        equity = float(account.equity or 0)
        previous_equity = float(account.last_equity or equity)
        positions: list[PositionSnapshot] = []
        ledger = self.ledger
        managed_positions = (
            ledger.managed_for_account(self._account_id or "unknown")
            if ledger is not None
            else {}
        )
        managed_symbols = set(managed_positions)
        unrealized = 0.0
        intraday_unrealized = 0.0
        raw_positions: Any = self._client.get_all_positions()
        for raw in raw_positions:
            current_price = float(raw.current_price)
            unrealized += float(raw.unrealized_pl or 0)
            intraday_unrealized += float(raw.unrealized_intraday_pl or 0)
            asset_class = str(getattr(raw, "asset_class", "")).lower()
            market = (
                Market.CRYPTO
                if "crypto" in asset_class or is_crypto(str(raw.symbol))
                else Market.EQUITY
            )
            canonical = canonical_symbol(str(raw.symbol), market)
            managed = managed_positions.get(canonical)
            positions.append(
                PositionSnapshot(
                    symbol=canonical,
                    qty=float(raw.qty),
                    avg_price=float(raw.avg_entry_price),
                    current_price=current_price,
                    market=market,
                    qty_available=(
                        float(raw.qty_available)
                        if getattr(raw, "qty_available", None) is not None
                        else None
                    ),
                    ownership=(
                        "managed"
                        if canonical in managed_symbols
                        else "external"
                    ),
                    stop_price=(
                        managed.current_stop or managed.original_stop
                        if managed is not None
                        else None
                    ),
                    initial_risk=(
                        abs(float(raw.avg_entry_price) - managed.original_stop)
                        if managed is not None
                        else None
                    ),
                )
            )
        old_peak = self._peak_equity
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity != old_peak:
            self._save_peak_equity()
        daily_pnl = equity - previous_equity
        cash = float(account.cash or 0)
        crypto_buying_power_raw = getattr(account, "non_marginable_buying_power", None)
        crypto_status = str(getattr(account, "crypto_status", "")).upper().split(".")[-1]
        return AccountSnapshot(
            cash=cash,
            equity=equity,
            peak_equity=self._peak_equity,
            daily_pnl=daily_pnl,
            positions=tuple(positions),
            realized_pnl=daily_pnl - intraday_unrealized,
            unrealized_pnl=unrealized,
            crypto_buying_power=(
                float(crypto_buying_power_raw) if crypto_buying_power_raw is not None else cash
            ),
            crypto_trading_enabled=crypto_status == "ACTIVE",
            fees=(
                ledger.total_fees_for_account(self._account_id or "unknown")
                if ledger is not None
                else 0.0
            ),
        )

    def submit(self, intent: OrderIntent) -> tuple[OrderState, Fill | None]:
        """Submit an entry and drive it to a terminal synchronous outcome.

        Equity entries are accepted broker-side (bracket protection) and
        return ``ACCEPTED``; later state is authoritative from TradingStream.
        Crypto entries are polled to a terminal state; if protection cannot
        be installed the position is flattened and the result is
        ``REJECTED`` together with a non-None fill (callers must not treat a
        non-None fill as proof of entry without checking the state, and a
        rejection with a fill means the entry was closed, not that nothing
        happened). Exceptions after broker acceptance degrade to
        ``RECONCILIATION_REQUIRED``, never to a silent rejection.
        """
        entry_registered = False
        broker_write_attempted = False
        broker_order_id = ""
        try:
            self._validate_pre_submit(intent)
            ledger = self.ledger
            if ledger is not None:
                # Reserve the symbol in the durable ledger before the broker
                # write.  Previously this happened after submit(), leaving a
                # window in which a second cycle could buy the same symbol.
                ledger.register_entry_order(self._account_id or "unknown", intent)
                entry_registered = True
            qty, stop_price, target_price = self._validated_values(intent)
            side = OrderSide.BUY if intent.direction == Direction.LONG else OrderSide.SELL
            # DAY bracket legs expire at the close and leave overnight equity
            # positions without their take-profit. Alpaca supports GTC bracket
            # orders, keeping both OCO legs intact across sessions.
            tif = TimeInForce.GTC
            if intent.market == Market.EQUITY:
                request: Any = MarketOrderRequest(
                    symbol=intent.symbol,
                    qty=qty,
                    side=side,
                    time_in_force=tif,
                    order_class=OrderClass.BRACKET,
                    stop_loss=StopLossRequest(stop_price=stop_price),
                    take_profit=TakeProfitRequest(limit_price=target_price),
                    client_order_id=intent.client_order_id,
                )
            else:
                request = MarketOrderRequest(
                    symbol=intent.symbol,
                    qty=qty,
                    side=side,
                    time_in_force=tif,
                    client_order_id=intent.client_order_id,
                )
            broker_write_attempted = True
            result = self._submit_with_recovery(request, intent.client_order_id)
            result_id = self._field(result, "id")
            if not result_id:
                raise AmbiguousOrderState(f"Alpaca order response has no id for {intent.symbol}")
            broker_order_id = str(result_id)
            if ledger is not None:
                ledger.register_entry_order(
                    self._account_id or "unknown",
                    intent,
                    str(result_id),
                )
            if intent.market == Market.EQUITY:
                # Bracket protection is broker-side. TradingStream is the
                # authoritative source for later partial/fill/terminal state.
                return OrderState.ACCEPTED, None
            state, fill = self._wait_for_fill(str(result_id), intent, qty)
            if intent.market == Market.CRYPTO and state in {
                OrderState.ACCEPTED,
                OrderState.PARTIALLY_FILLED,
            }:
                log.warning(
                    "Entry cancel requested: market=crypto symbol=%s "
                    "entry_client_order_id=%s broker_order_id=%s recovery_action=bounded_poll",
                    intent.symbol,
                    intent.client_order_id,
                    result_id,
                )
                try:
                    self._client.cancel_order_by_id(result_id)
                except Exception as exc:
                    # The request may have reached Alpaca. Resolve the existing
                    # order instead of converting uncertainty into rejection.
                    log.warning(
                        "Entry cancel response ambiguous: market=crypto symbol=%s "
                        "entry_client_order_id=%s broker_order_id=%s error=%s",
                        intent.symbol,
                        intent.client_order_id,
                        result_id,
                        exc,
                    )
                state, final_fill = self._wait_for_fill(
                    str(result_id),
                    intent,
                    qty,
                    timeout=getattr(self, "crypto_stop_cancel_timeout_seconds", 5.0),
                )
                if final_fill is not None:
                    fill = final_fill
                if state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
                    raise AmbiguousOrderState(
                        "Crypto entry cancel outcome unresolved for "
                        f"client_order_id={intent.client_order_id}"
                    )
                if fill is None:
                    self._record_entry_terminal(intent, state, str(result_id))
                    return state, None
            if intent.market == Market.CRYPTO and fill is not None:
                if ledger is not None:
                    ledger.apply_execution_event(
                        ExecutionEvent(
                            account_id=self._account_id or "unknown",
                            client_order_id=intent.client_order_id,
                            broker_order_id=str(result_id),
                            parent_order_id=None,
                            role=OrderRole.ENTRY,
                            symbol=intent.symbol,
                            market=intent.market,
                            state=state,
                            requested_qty=intent.qty,
                            cumulative_filled_qty=fill.qty,
                            average_fill_price=fill.price,
                            occurred_at=fill.timestamp,
                        )
                    )
                protected = self._submit_crypto_stop(intent, fill.qty, stop_price)
                if not protected:
                    flatten_result = self._flatten(intent, fill.qty)
                    if ledger is not None and flatten_result is not None:
                        exit_fill, exit_broker_id = flatten_result
                        ledger.apply_execution_event(
                            ExecutionEvent(
                                account_id=self._account_id or "unknown",
                                client_order_id=exit_fill.client_order_id,
                                broker_order_id=exit_broker_id,
                                parent_order_id=None,
                                role=OrderRole.EMERGENCY_EXIT,
                                symbol=intent.symbol,
                                market=intent.market,
                                state=OrderState.FILLED,
                                requested_qty=exit_fill.qty,
                                cumulative_filled_qty=exit_fill.qty,
                                average_fill_price=exit_fill.price,
                                occurred_at=exit_fill.timestamp,
                            )
                        )
                    return OrderState.REJECTED, fill
            if fill is None and state in {
                OrderState.CANCELLED,
                OrderState.REJECTED,
                OrderState.EXPIRED,
            }:
                self._record_entry_terminal(intent, state, str(result_id))
            return state, fill
        except ValueError as exc:
            # Closed markets, unsupported shorts, and duplicate orders are
            # expected safety rejections rather than broker failures.
            if broker_order_id:
                ledger = self.ledger
                if ledger is not None:
                    ledger.mark_manual_intervention(
                        self._account_id or "unknown", intent.client_order_id
                    )
                log.critical(
                    "Order processing failed after broker acceptance; "
                    "reconciliation is required: %s",
                    exc,
                )
                return OrderState.RECONCILIATION_REQUIRED, None
            if entry_registered:
                self._record_entry_terminal(intent, OrderState.REJECTED)
            log.warning("Order rejected: %s", exc)
            return OrderState.REJECTED, None
        except AmbiguousOrderState as exc:
            ledger = self.ledger
            if ledger is not None:
                ledger.mark_manual_intervention(
                    self._account_id or "unknown", intent.client_order_id
                )
            log.critical(
                "Order state is ambiguous; entries must halt pending reconciliation: %s", exc
            )
            return OrderState.RECONCILIATION_REQUIRED, None
        except Exception as exc:
            if entry_registered and not broker_write_attempted:
                self._record_entry_terminal(intent, OrderState.REJECTED)
            if broker_write_attempted:
                ledger = self.ledger
                if ledger is not None:
                    ledger.mark_manual_intervention(
                        self._account_id or "unknown", intent.client_order_id
                    )
                log.critical(
                    "Order processing failed after broker write; reconciliation is required: %s",
                    exc,
                )
                return OrderState.RECONCILIATION_REQUIRED, None
            log.error("Order rejected: %s", exc)
            return OrderState.REJECTED, None

    def _record_entry_terminal(
        self,
        intent: OrderIntent,
        state: OrderState,
        broker_order_id: str = "",
    ) -> None:
        """Persist a confirmed terminal entry state and release its reservation."""
        ledger = self.ledger
        if ledger is None:
            return
        ledger.apply_execution_event(
            ExecutionEvent(
                account_id=self._account_id or "unknown",
                client_order_id=intent.client_order_id,
                broker_order_id=broker_order_id,
                parent_order_id=None,
                role=OrderRole.ENTRY,
                symbol=intent.symbol,
                market=intent.market,
                state=state,
                requested_qty=intent.qty,
                cumulative_filled_qty=0,
                average_fill_price=None,
                occurred_at=datetime.now(UTC),
            )
        )

    def reconcile(self) -> None:
        """Synchronize broker, stream and ledger state after any drift.

        Passes (in order): fill-activity fee backfill and offline-fill
        recovery, pending crypto exit / stop-failover completion, stale
        managed entries proven flat by the position snapshot, and protective
        stop (re)submission for every managed or opted-in position. Any
        failure raises so the caller can halt entries until the drift is
        understood — the ledger is the durable source of truth for owned
        quantities, never the broker snapshot alone.
        """
        account = self.account()
        manage_external = bool(getattr(self, "manage_external_positions", True))
        ledger = self.ledger
        managed = (
            ledger.managed_for_account(self._account_id or "unknown") if ledger is not None else {}
        )
        fill_activities_reconciled = True
        if ledger is not None:
            fill_activities_reconciled = self._reconcile_fill_activity_fees()
            # Fill recovery can create managed ownership that was absent from
            # the snapshot taken above. Refresh it before protection checks.
            managed = ledger.managed_for_account(self._account_id or "unknown")
        broker_symbols = {
            canonical_symbol(position.symbol, position.market)
            for position in account.positions
            if not position.is_flat
        }
        if ledger is not None:
            self._reconcile_crypto_exit_state(ledger)
            self._reconcile_stale_ledger_entries(
                ledger, broker_symbols, fill_activities_reconciled
            )
        for position in account.positions:
            is_long = position.qty > 0
            if position.is_flat:
                continue
            symbol = canonical_symbol(position.symbol, position.market)
            ownership = managed.get(symbol)
            if ownership is None and not manage_external:
                log.info("External position left unchanged during reconciliation: %s", symbol)
                continue
            broker_qty_is_fee_adjusted = False
            if ownership is not None:
                tolerance = max(1e-9, ownership.owned_qty * 1e-8)
                minimum_fee_adjusted_qty = ownership.owned_qty
                if position.market == Market.CRYPTO:
                    minimum_fee_adjusted_qty *= float(Decimal("1") - _CRYPTO_TAKER_FEE_RESERVE)
                if (
                    position.market == Market.CRYPTO
                    and abs(position.qty) + tolerance < minimum_fee_adjusted_qty
                ):
                    if ledger is not None:
                        ledger.mark_manual_intervention(
                            self._account_id or "unknown",
                            ownership.entry_client_order_id,
                        )
                    raise ProtectionReconciliationError(
                        f"Broker quantity is below managed ledger quantity for {symbol}"
                    )
                if (
                    position.market == Market.CRYPTO
                    and abs(position.qty) + tolerance < ownership.owned_qty
                ):
                    broker_qty_is_fee_adjusted = True
                    log.info(
                        "Accepted fee-adjusted crypto ownership: symbol=%s ledger_qty=%s "
                        "broker_qty=%s",
                        symbol,
                        ownership.owned_qty,
                        abs(position.qty),
                    )
                stop_price = (
                    getattr(ownership, "current_stop", None) or ownership.original_stop
                )
                total_qty = min(abs(position.qty), ownership.owned_qty)
            else:
                # Backwards-compatible opt-in policy for explicitly managed
                # external positions. Runtime defaults this off.
                stop_pct = 0.025 if position.market == Market.CRYPTO else 0.04
                stop_price = position.avg_price * (1 - stop_pct if is_long else 1 + stop_pct)
                total_qty = abs(position.qty)
            available_qty = (
                abs(position.qty_available) if position.qty_available is not None else total_qty
            )
            required_qty = total_qty
            if (
                position.market == Market.CRYPTO
                and position.qty_available == 0
                and not broker_qty_is_fee_adjusted
            ):
                # Alpaca may deduct the taker fee from the acquired asset. A
                # stop can therefore reserve every sellable unit while being
                # up to 0.25% smaller than the raw position quantity. Only use
                # this tolerance when Alpaca confirms nothing remains
                # available; otherwise reconcile protects the remaining units.
                required_qty = total_qty * float(Decimal("1") - _CRYPTO_TAKER_FEE_RESERVE)
                required_qty = self._round_qty_down(position.symbol, required_qty)
            has_protection = self._has_protective_order(position.symbol, is_long, required_qty)
            if (
                not has_protection
                and ownership is not None
                and ownership.broker_order_id
                and available_qty <= 0
            ):
                # Alpaca's open-orders endpoint can return only the active
                # take-profit leg of a filled bracket. Its paired stop remains
                # HELD and is visible only when the filled parent is fetched
                # with nested legs. Treat that held stop as active protection;
                # otherwise reconciliation tries to submit a second stop while
                # the bracket has reserved the entire position.
                parent_order_ids: str | set[str] = ownership.broker_order_id
                if ledger is not None:
                    parent_order_ids = ledger.managed_entry_broker_order_ids(
                        self._account_id or "unknown",
                        symbol,
                    )
                has_protection = self._has_nested_bracket_protection(
                    parent_order_ids,
                    position.symbol,
                    is_long,
                    required_qty,
                )
            if has_protection:
                if (
                    ownership is not None
                    and ledger is not None
                ):
                    ledger.set_protection_state(
                        self._account_id or "unknown",
                        ownership.entry_client_order_id,
                        ProtectionState.ACTIVE,
                    )
                continue
            mismatch = getattr(self, "_last_protection_mismatch", "missing")
            if mismatch in {"under", "over"}:
                if (
                    ownership is not None
                    and ledger is not None
                ):
                    ledger.set_protection_state(
                        self._account_id or "unknown",
                        ownership.entry_client_order_id,
                        ProtectionState.REQUESTED,
                    )
                # Add only the currently available remainder when protection is
                # undersized. Cancelling the existing stop first creates a
                # needless unprotected window and can churn fee-adjusted crypto
                # stops on every reconciliation cycle. Oversized protection
                # must be cancelled and broker-confirmed before replacement.
                if mismatch == "over":
                    self._cancel_protective_orders(position.symbol, is_long)
            qty = min(total_qty, available_qty)
            if mismatch == "under":
                protected_qty = float(getattr(self, "_last_protected_qty", 0.0))
                qty = min(qty, max(0.0, required_qty - protected_qty))
            if position.market == Market.CRYPTO:
                qty = self._available_position_qty(
                    position.symbol,
                    qty,
                    reserve_fee_on_lookup_failure=False,
                )
                if qty <= 0:
                    raise RuntimeError(f"No available quantity to protect for {position.symbol}")
            elif qty <= 0:
                raise RuntimeError(f"No available quantity to protect for {position.symbol}")
            protective_client_id = (
                f"{ownership.entry_client_order_id}-stop-{uuid4().hex[:8]}"
                if ownership is not None
                else None
            )
            protected = self._submit_stop(
                position.symbol,
                position.market,
                qty,
                stop_price,
                is_long,
                client_order_id=protective_client_id,
            )
            if not protected:
                if (
                    ownership is not None
                    and ledger is not None
                ):
                    ledger.set_protection_state(
                        self._account_id or "unknown",
                        ownership.entry_client_order_id,
                        ProtectionState.FAILED,
                    )
                    if ledger is not None:
                        ledger.mark_manual_intervention(
                            self._account_id or "unknown",
                            ownership.entry_client_order_id,
                        )
                raise ProtectionReconciliationError(
                    f"Could not restore managed protection for {position.symbol}"
                )
            if (
                ownership is not None
                and ledger is not None
            ):
                ledger.set_protection_state(
                    self._account_id or "unknown",
                    ownership.entry_client_order_id,
                    ProtectionState.ACTIVE,
                )

    def _reconcile_crypto_exit_state(self, ledger: Any) -> None:
        """Drive pending crypto exits and stop-failover paths to completion."""
        for entry in ledger.managed_entries_for_account(self._account_id or "unknown"):
            if entry.market != Market.CRYPTO:
                continue
            if entry.exit_client_order_id:
                self._reconcile_crypto_exit(entry)
            elif entry.stop_state == "cancel_requested" and entry.target_state in {
                "target_triggered",
                "stop_failover_triggered",
            }:
                self._cancel_stop_then_exit(
                    entry,
                    "target" if entry.target_state == "target_triggered" else "stop_failover",
                )

    def _reconcile_stale_ledger_entries(
        self,
        ledger: Any,
        broker_symbols: set[str],
        fill_activities_reconciled: bool,
    ) -> None:
        """Close managed entries the broker snapshot proves flat."""
        for entry in ledger.managed_entries_for_account(self._account_id or "unknown"):
            if entry.owned_qty <= 0 or entry.canonical_symbol in broker_symbols:
                continue
            if entry.exit_client_order_id:
                self._reconcile_crypto_exit(entry)
                continue
            if entry.market == Market.EQUITY and not fill_activities_reconciled:
                ledger.mark_manual_intervention(
                    self._account_id or "unknown",
                    entry.entry_client_order_id,
                )
                log.warning(
                    "Managed ledger close deferred: market=equity symbol=%s "
                    "entry_client_order_id=%s "
                    "recovery_action=await_fill_activity_reconciliation",
                    entry.canonical_symbol,
                    entry.entry_client_order_id,
                )
                continue
            # ``account()`` has successfully returned Alpaca's complete
            # position snapshot.  A managed symbol absent from that
            # snapshot is flat at the broker (for example, after a
            # bracket leg filled while the trading stream was offline).
            # Keep the durable ownership ledger in sync instead of
            # repeatedly halting all reconciliation on a stale entry.
            ledger.mark_position_closed(
                self._account_id or "unknown", entry.entry_client_order_id
            )
            log.warning(
                "Stale managed ledger entry closed: market=%s symbol=%s "
                "entry_client_order_id=%s managed_qty=%s "
                "recovery_action=broker_position_snapshot_confirmed_flat",
                entry.market.value,
                entry.canonical_symbol,
                entry.entry_client_order_id,
                entry.owned_qty,
            )

    def _reconcile_fill_activity_fees(self) -> bool:
        """Backfill fees when TradingStream fill payloads omit commission."""
        request = getattr(getattr(self, "_client", None), "_request", None)
        ledger = self.ledger
        if request is None or ledger is None:
            return False
        activities: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        try:
            while True:
                params = {"direction": "desc", "page_size": 100}
                if page_token is not None:
                    params["page_token"] = page_token
                payload = request("GET", "/account/activities/FILL", data=params)
                page = payload if isinstance(payload, list) else []
                activities.extend(item for item in page if isinstance(item, dict))
                if len(page) < 100:
                    break
                next_token = str(page[-1].get("id", "") or "")
                if not next_token or next_token in seen_tokens:
                    # The API did not advance the cursor (or returned a
                    # repeated page): the loop must stop, but silently
                    # stopping would drop fee attribution and offline-fill
                    # recovery for every activity past this point.
                    log.warning(
                        "Fill activity pagination stopped at %d activities: "
                        "page cursor was not honored; reconciliation of "
                        "older fill activities is skipped",
                        len(activities),
                    )
                    break
                seen_tokens.add(next_token)
                page_token = next_token
        except Exception as exc:
            log.warning("Fill activity fee reconciliation deferred: %s", exc)
            return False
        activities.sort(
            key=lambda item: str(item.get("transaction_time") or item.get("date") or "")
        )
        recovered_buy_fills: dict[str, list[tuple[float, float, datetime, str, str]]] = {}
        for activity in activities:
            fee_raw = activity.get("commission", activity.get("fee", 0))
            try:
                fee = abs(float(fee_raw or 0))
            except (TypeError, ValueError):
                continue
            timestamp_raw = (
                activity.get("transaction_time")
                or activity.get("date")
                or datetime.now(UTC).isoformat()
            )
            try:
                occurred_at = datetime.fromisoformat(str(timestamp_raw).replace("Z", "+00:00"))
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=UTC)
            except ValueError:
                occurred_at = datetime.now(UTC)
            activity_id = str(activity.get("id", "") or "")
            broker_order_id = str(activity.get("order_id", "") or "")
            side = str(activity.get("side", "") or "").lower().split(".")[-1]
            symbol = str(activity.get("symbol", "") or "")
            try:
                qty = abs(float(activity.get("qty", 0) or 0))
                price = float(activity.get("price", 0) or 0)
            except (TypeError, ValueError):
                qty = 0.0
                price = 0.0
            # Recover an exit that filled while TradingStream was offline.
            # A stable activity id makes this idempotent across reconciliations.
            if (
                activity_id
                and broker_order_id
                and symbol
                and side == "sell"
                and qty > 0
                and price > 0
            ):
                market = Market.CRYPTO if "/" in symbol else Market.EQUITY
                ledger.apply_execution_event(
                    ExecutionEvent(
                        account_id=self._account_id or "unknown",
                        client_order_id=f"alpaca-fill-activity-{activity_id}",
                        broker_order_id=broker_order_id,
                        parent_order_id=None,
                        role=OrderRole.MANUAL_EXIT,
                        symbol=canonical_symbol(symbol, market),
                        market=market,
                        state=OrderState.FILLED,
                        requested_qty=qty,
                        cumulative_filled_qty=qty,
                        average_fill_price=price,
                        occurred_at=occurred_at,
                        fee=fee,
                        activity_id=activity_id,
                    )
                )
            if (
                activity_id
                and broker_order_id
                and symbol
                and side == "buy"
                and qty > 0
                and price > 0
            ):
                recovered_buy_fills.setdefault(broker_order_id, []).append(
                    (qty, price, occurred_at, activity_id, symbol)
                )
            applied = ledger.apply_fee_activity(
                self._account_id or "unknown",
                activity_id=activity_id,
                broker_order_id=broker_order_id,
                fee=fee,
                occurred_at=occurred_at,
            )
            if applied:
                log.info(
                    "Fee activity applied: broker_order_id=%s fee=%s "
                    "recovery_action=fill_activity_reconciliation",
                    activity.get("order_id"),
                    fee,
                )
        entry_lookup = getattr(ledger, "entry_order_for_broker_id", None)
        if entry_lookup is not None:
            for broker_order_id, fills in recovered_buy_fills.items():
                recovered = entry_lookup(
                    self._account_id or "unknown",
                    broker_order_id,
                )
                if recovered is None:
                    continue
                order, known_cumulative = recovered
                observed_qty = sum(item[0] for item in fills)
                cumulative = min(order.qty, max(known_cumulative, observed_qty))
                if cumulative <= known_cumulative:
                    continue
                observed_notional = sum(item[0] * item[1] for item in fills)
                average_price = observed_notional / observed_qty
                latest = max(fills, key=lambda item: item[2])
                state = (
                    OrderState.FILLED
                    if cumulative >= order.qty - max(1e-9, order.qty * 1e-8)
                    else OrderState.PARTIALLY_FILLED
                )
                ledger.apply_execution_event(
                    ExecutionEvent(
                        account_id=self._account_id or "unknown",
                        client_order_id=order.client_order_id,
                        broker_order_id=broker_order_id,
                        parent_order_id=None,
                        role=OrderRole.ENTRY,
                        symbol=order.symbol,
                        market=order.market,
                        state=state,
                        requested_qty=order.qty,
                        cumulative_filled_qty=cumulative,
                        average_fill_price=average_price,
                        occurred_at=latest[2],
                        activity_id=latest[3],
                    )
                )
                log.warning(
                    "Entry fill recovered from account activity: market=%s symbol=%s "
                    "entry_client_order_id=%s broker_order_id=%s cumulative_qty=%s",
                    order.market.value,
                    order.symbol,
                    order.client_order_id,
                    broker_order_id,
                    cumulative,
                )
        return True

    def _reconcile_crypto_exit(self, position: ManagedPosition) -> None:
        ledger = self.ledger
        if ledger is None:
            raise ProtectionReconciliationError(
                f"Cannot recover crypto exit for {position.canonical_symbol}: no ledger"
            )
        get_by_client_id = getattr(self._client, "get_order_by_client_id", None)
        if get_by_client_id is None or not position.exit_client_order_id:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise ProtectionReconciliationError(
                f"Cannot recover crypto exit for {position.canonical_symbol}"
            )
        try:
            order = get_by_client_id(position.exit_client_order_id)
        except Exception as exc:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise ProtectionReconciliationError(
                f"Crypto exit lookup is ambiguous for {position.canonical_symbol}"
            ) from exc
        status_name = self._normalized_field(order, "status")
        state = {
            "filled": OrderState.FILLED,
            "partially_filled": OrderState.PARTIALLY_FILLED,
            "canceled": OrderState.CANCELLED,
            "cancelled": OrderState.CANCELLED,
            "rejected": OrderState.REJECTED,
            "expired": OrderState.EXPIRED,
        }.get(status_name, OrderState.ACCEPTED)
        qty = float(self._field(order, "qty", position.owned_qty) or position.owned_qty)
        filled_qty = float(self._field(order, "filled_qty", 0) or 0)
        average = self._field(order, "filled_avg_price")
        role = (
            OrderRole.TAKE_PROFIT
            if "-target-exit" in position.exit_client_order_id
            else OrderRole.EMERGENCY_EXIT
        )
        if filled_qty > 0 and average is not None:
            ledger.apply_execution_event(
                ExecutionEvent(
                    self._account_id,
                    position.exit_client_order_id,
                    str(self._field(order, "id", "") or ""),
                    None,
                    role,
                    position.canonical_symbol,
                    Market.CRYPTO,
                    state,
                    qty,
                    filled_qty,
                    float(average),
                    datetime.now(UTC),
                )
            )
        if state == OrderState.FILLED:
            ledger.set_exit_state(
                self._account_id,
                position.entry_client_order_id,
                target_state=(
                    "target_exit_filled"
                    if role == OrderRole.TAKE_PROFIT
                    else "emergency_exit_filled"
                ),
            )
            return
        if state in {OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}:
            self._reprotect_after_terminal_exit(position)
            return
        ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
        raise ProtectionReconciliationError(
            f"Crypto exit remains {state.value} for {position.canonical_symbol}"
        )

    def _reprotect_after_terminal_exit(self, position: ManagedPosition) -> None:
        ledger = self.ledger
        if ledger is None:
            raise ProtectionReconciliationError(
                f"Cannot re-protect {position.canonical_symbol}: no ledger"
            )
        current = next(
            (
                item
                for item in ledger.managed_entries_for_account(self._account_id)
                if item.entry_client_order_id == position.entry_client_order_id
            ),
            None,
        )
        if current is None or current.owned_qty <= 0:
            return
        resolution = self.resolve_available_position_qty(
            current.canonical_symbol,
            current.owned_qty,
            reserve_fee_on_lookup_failure=False,
        )
        if not resolution.verified or resolution.available_qty <= 0:
            ledger.mark_manual_intervention(self._account_id, current.entry_client_order_id)
            raise ProtectionReconciliationError(
                f"Remaining exit quantity is unverified for {current.canonical_symbol}"
            )
        client_id = f"{current.entry_client_order_id}-stop-reprotect"
        log.warning(
            "Re-protection requested: market=crypto symbol=%s entry_client_order_id=%s "
            "managed_qty=%s verified_available_qty=%s quantity_source=%s",
            current.canonical_symbol,
            current.entry_client_order_id,
            current.owned_qty,
            resolution.available_qty,
            resolution.source,
        )
        protected = self._submit_stop(
            current.canonical_symbol,
            Market.CRYPTO,
            resolution.available_qty,
            current.current_stop or current.original_stop,
            True,
            client_order_id=client_id,
        )
        if not protected:
            if self._has_protective_order(current.canonical_symbol, True, resolution.available_qty):
                ledger.mark_manual_intervention(
                    self._account_id, current.entry_client_order_id
                )
                raise AmbiguousOrderState(
                    f"Protective stop exists while re-protection failed for "
                    f"{current.canonical_symbol}"
                )
            intent = OrderIntent(
                current.source_signal_id,
                current.canonical_symbol,
                Market.CRYPTO,
                Direction.LONG,
                resolution.available_qty,
                current.filled_avg_price,
                current.current_stop or current.original_stop,
                current.original_target,
                current.strategy,
                client_order_id=current.entry_client_order_id,
            )
            exit_fill, exit_broker_id = self._flatten(intent, resolution.available_qty)
            # Record the flatten like submit() does: without this the managed
            # position stays open in the ledger with owned_qty > 0 although
            # the broker is flat, and the exit PnL is never attributed.
            ledger.apply_execution_event(
                ExecutionEvent(
                    account_id=self._account_id or "unknown",
                    client_order_id=exit_fill.client_order_id,
                    broker_order_id=exit_broker_id,
                    parent_order_id=None,
                    role=OrderRole.EMERGENCY_EXIT,
                    symbol=current.canonical_symbol,
                    market=Market.CRYPTO,
                    state=OrderState.FILLED,
                    requested_qty=exit_fill.qty,
                    cumulative_filled_qty=exit_fill.qty,
                    average_fill_price=exit_fill.price,
                    occurred_at=exit_fill.timestamp,
                )
            )
            ledger.set_exit_state(
                self._account_id or "unknown",
                current.entry_client_order_id,
                stop_state="open",
                target_state="emergency_exit_filled",
                clear_exit_order=True,
            )
            return
        ledger.set_exit_state(
            self._account_id,
            current.entry_client_order_id,
            stop_state="open",
            target_state="reprotected_after_terminal_exit",
            clear_exit_order=True,
        )
        log.warning(
            "Re-protection active: market=crypto symbol=%s entry_client_order_id=%s "
            "order_client_id=%s managed_qty=%s",
            current.canonical_symbol,
            current.entry_client_order_id,
            client_id,
            resolution.available_qty,
        )

    def _has_nested_bracket_protection(
        self,
        parent_order_ids: str | set[str],
        symbol: str,
        position_is_long: bool,
        required_qty: float,
    ) -> bool:
        closing_side = "sell" if position_is_long else "buy"

        def protective_qty(order: Any) -> Decimal:
            side = self._normalized_field(order, "side")
            order_type = self._normalized_field(order, "type")
            stop_price = self._field(order, "stop_price")
            status = self._normalized_field(order, "status")
            if (
                side == closing_side
                and status not in {"canceled", "expired", "filled", "rejected"}
                and (
                    stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"}
                )
            ):
                return abs(Decimal(str(self._field(order, "qty", 0) or 0)))
            return sum(
                (protective_qty(leg) for leg in (self._field(order, "legs") or ())),
                start=Decimal("0"),
            )

        ids = {parent_order_ids} if isinstance(parent_order_ids, str) else parent_order_ids
        protected = Decimal("0")
        for parent_order_id in ids:
            parent: Any = self._client.get_order_by_id(
                parent_order_id,
                GetOrderByIdRequest(nested=True),
            )
            if same_symbol(str(self._field(parent, "symbol", symbol)), symbol):
                protected += protective_qty(parent)
        matches, self._last_protection_mismatch = self._protection_matches(
            protected, Decimal(str(required_qty))
        )
        self._last_protected_qty = float(protected)
        return matches

    def _asset(self, symbol: str) -> Any:
        identity = normalize_symbol(symbol)
        cache_key = identity.canonical
        asset = (
            self._asset_cache.get(cache_key)
            or self._asset_cache.get(symbol)
            or self._asset_cache.get(identity.asset_lookup_symbol)
        )
        if asset is None:
            asset = self._client.get_asset(identity.asset_lookup_symbol)
            self._asset_cache[cache_key] = asset
        return asset

    def _price_step(self, symbol: str, market: Market, reference_price: float) -> Decimal:
        increment = getattr(self._asset(symbol), "price_increment", None)
        if increment is not None:
            return Decimal(str(increment))
        if market == Market.EQUITY:
            # Alpaca: stocks >= $1 trade in pennies, sub-dollar stocks in 1/10000.
            return Decimal("0.01") if reference_price >= 1 else Decimal("0.0001")
        return Decimal("0.01") if reference_price >= 1 else Decimal("0.000001")

    @staticmethod
    def _round_to_step(value: float, step: Decimal) -> float:
        rounded = (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_HALF_UP) * step
        return float(rounded)

    def _validated_values(self, intent: OrderIntent) -> tuple[float, float, float]:
        asset = self._asset(intent.symbol)
        if not bool(getattr(asset, "tradable", False)):
            raise ValueError(f"Asset is not tradable: {intent.symbol}")
        if intent.market == Market.CRYPTO and intent.direction == Direction.SHORT:
            raise ValueError("Alpaca does not support short selling crypto")
        if intent.market == Market.EQUITY and intent.direction == Direction.SHORT:
            if not bool(getattr(asset, "shortable", False)) or not bool(
                getattr(asset, "easy_to_borrow", False)
            ):
                raise ValueError(f"Asset is not available to short: {intent.symbol}")
        qty = Decimal(str(intent.qty))
        if intent.market == Market.EQUITY:
            # Bracket orders (take_profit/stop_loss legs) require whole shares;
            # fractional equity qty is only allowed for simple market orders.
            qty = qty.to_integral_value(rounding=ROUND_DOWN)
        minimum = getattr(asset, "min_order_size", None)
        increment = getattr(asset, "min_trade_increment", None)
        if increment is not None:
            step = Decimal(str(increment))
            qty = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
        if intent.market == Market.EQUITY:
            qty = qty.to_integral_value(rounding=ROUND_DOWN)
        if qty <= 0:
            raise ValueError(f"Order rounds to zero for {intent.symbol}")
        if minimum is not None and qty < Decimal(str(minimum)):
            raise ValueError(
                f"Order quantity {qty} is below the minimum {minimum} for {intent.symbol}"
            )
        stop_price = self._round_to_step(
            intent.stop_price, self._price_step(intent.symbol, intent.market, intent.stop_price)
        )
        target_price = self._round_to_step(
            intent.target_price,
            self._price_step(intent.symbol, intent.market, intent.target_price),
        )
        if intent.direction == Direction.LONG and not (
            stop_price < intent.entry_price < target_price
        ):
            raise ValueError(
                f"Bracket legs inverted for {intent.symbol}: "
                f"stop={stop_price} entry={intent.entry_price} target={target_price}"
            )
        if intent.direction == Direction.SHORT and not (
            target_price < intent.entry_price < stop_price
        ):
            raise ValueError(
                f"Bracket legs inverted for {intent.symbol}: "
                f"target={target_price} entry={intent.entry_price} stop={stop_price}"
            )
        qty_number: float = int(qty) if intent.market == Market.EQUITY else float(qty)
        return qty_number, stop_price, target_price

    def _wait_for_fill(
        self, order_id: str, intent: OrderIntent, qty: float, timeout: float = 5.0
    ) -> tuple[OrderState, Fill | None]:
        deadline = time.monotonic() + timeout
        fill: Fill | None = None
        state = OrderState.ACCEPTED
        while time.monotonic() < deadline:
            try:
                order: Any = self._client.get_order_by_id(order_id)
                status = self._normalized_field(order, "status")
                filled_qty = float(self._field(order, "filled_qty", 0) or 0)
                filled_price = float(self._field(order, "filled_avg_price", 0) or 0)
                if filled_qty > 0 and filled_price > 0:
                    fill = Fill(
                        intent.client_order_id,
                        intent.symbol,
                        filled_qty,
                        filled_price,
                        intent.direction,
                        datetime.now(UTC),
                        commission=float(self._field(order, "fees", 0) or 0),
                    )
                if status == "filled":
                    return OrderState.FILLED, fill
                if status == "partially_filled":
                    state = OrderState.PARTIALLY_FILLED
                elif status in {"canceled", "cancelled"}:
                    return OrderState.CANCELLED, fill
                elif status == "expired":
                    return OrderState.EXPIRED, fill
                elif status == "rejected":
                    return OrderState.REJECTED, fill
                else:
                    state = OrderState.ACCEPTED
            except Exception as exc:
                log.warning("Order status lookup failed for %s: %s", order_id, exc)
                state = OrderState.ACCEPTED
            time.sleep(0.5)
        log.warning("Order %s not filled within %.1fs", order_id, timeout)
        return state, fill

    def _submit_crypto_stop(self, intent: OrderIntent, qty: float, stop: float) -> bool:
        qty = self._available_position_qty(
            intent.symbol,
            qty,
            reserve_fee_on_lookup_failure=intent.direction == Direction.LONG,
        )
        if qty <= 0:
            log.error("Protective stop has no available quantity for %s", intent.symbol)
            return False
        return self._submit_stop(
            intent.symbol,
            intent.market,
            qty,
            stop,
            intent.direction == Direction.LONG,
            client_order_id=f"{intent.client_order_id}-stop",
        )

    def protect_recovered_crypto_entry(self, intent: OrderIntent) -> bool:
        """Protect a fill discovered after an ambiguous entry-cancel outcome."""
        ledger = self.ledger
        if ledger is None:
            return False
        current = next(
            (
                item
                for item in ledger.managed_entries_for_account(self._account_id or "unknown")
                if item.entry_client_order_id == intent.client_order_id
            ),
            None,
        )
        if current is None or current.owned_qty <= 0:
            return False
        if current.protective_stop_broker_order_id and current.stop_state == "open":
            return True
        protected = self._submit_crypto_stop(
            intent,
            current.owned_qty,
            current.current_stop or current.original_stop,
        )
        if not protected:
            ledger.mark_manual_intervention(self._account_id or "unknown", intent.client_order_id)
        return protected

    def replace_protective_stop(
        self,
        entry_client_order_id: str,
        requested_stop: float,
    ) -> bool:
        """Atomically tighten a managed stop and persist the acknowledged order.

        Alpaca's replace endpoint is used directly. The method never cancels the
        active order first, so an API failure leaves the existing stop in force.
        """
        ledger = self.ledger
        account_id = self._account_id
        if ledger is None or not account_id or not math.isfinite(requested_stop):
            return False
        current = next(
            (
                item
                for item in ledger.managed_entries_for_account(account_id)
                if item.entry_client_order_id == entry_client_order_id
            ),
            None,
        )
        if current is None or not current.protective_stop_broker_order_id:
            return False
        active_stop = current.current_stop or current.original_stop
        # Direction comes from the bracket geometry (stop < target means a
        # long entry), never from the fill price: an accepted-but-unfilled
        # entry has filled_avg_price == 0 and would misclassify as short.
        # A row without a target (legacy data) cannot prove direction:
        # refuse rather than risk moving a stop the wrong way.
        if current.original_target <= 0:
            return False
        position_is_long = current.original_stop < current.original_target
        tighter = requested_stop > active_stop if position_is_long else requested_stop < active_stop
        if requested_stop <= 0 or not tighter:
            return False

        step = self._price_step(current.canonical_symbol, current.market, requested_stop)
        stop_price = self._round_to_step(requested_stop, step)
        rounded_tighter = (
            stop_price > active_stop if position_is_long else stop_price < active_stop
        )
        if stop_price <= 0 or not rounded_tighter:
            return False
        request_values: dict[str, float] = {"stop_price": stop_price}
        if current.market == Market.CRYPTO:
            offset = Decimal(str(self.crypto_stop_limit_offset_bps)) / Decimal("10000")
            stop_decimal = Decimal(str(stop_price))
            distance_steps = max(
                1,
                int((stop_decimal * offset / step).to_integral_value(rounding=ROUND_CEILING)),
            )
            limit_decimal = stop_decimal + (
                -distance_steps * step if position_is_long else distance_steps * step
            )
            if limit_decimal <= 0:
                return False
            request_values["limit_price"] = float(limit_decimal)

        try:
            result = self._client.replace_order_by_id(
                current.protective_stop_broker_order_id,
                ReplaceOrderRequest(**request_values),
            )
        except Exception as exc:
            log.error(
                "Atomic protective stop replacement failed for %s: %s",
                current.canonical_symbol,
                exc,
            )
            return False
        status = self._normalized_field(result, "status")
        if status in {"canceled", "cancelled", "expired", "rejected"}:
            return False
        broker_order_id = str(self._field(result, "id", "") or "")
        client_order_id = str(
            self._field(result, "client_order_id", "")
            or current.protective_stop_client_order_id
            or ""
        )
        if not broker_order_id or not client_order_id:
            ledger.mark_manual_intervention(account_id, entry_client_order_id)
            log.critical(
                "Replacement acknowledged without stable identifiers: symbol=%s",
                current.canonical_symbol,
            )
            return False
        try:
            ledger.register_protective_stop_replacement(
                account_id,
                entry_client_order_id,
                stop_price=stop_price,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
            )
        except Exception:
            ledger.mark_manual_intervention(account_id, entry_client_order_id)
            log.exception(
                "Replacement stop could not be persisted: symbol=%s",
                current.canonical_symbol,
            )
            return False
        return True

    def _available_position_qty(
        self,
        symbol: str,
        requested_qty: float,
        *,
        reserve_fee_on_lookup_failure: bool,
        attempts: int = 5,
    ) -> float:
        resolution = self.resolve_available_position_qty(
            symbol,
            requested_qty,
            reserve_fee_on_lookup_failure=reserve_fee_on_lookup_failure,
            attempts=attempts,
        )
        return resolution.available_qty

    def resolve_available_position_qty(
        self,
        symbol: str,
        requested_qty: float,
        *,
        reserve_fee_on_lookup_failure: bool = False,
        attempts: int = 5,
    ) -> QuantityResolution:
        """Resolve an increment-safe close quantity and its verification source.

        The fee-reserve fallback is permitted only immediately after a verified
        crypto entry fill while protection is being installed. Exit paths must
        use the default fail-closed policy.
        """
        requested = Decimal(str(requested_qty))
        step = self._qty_step(symbol)
        saw_position = False
        attempted_logged = False

        for attempt in range(attempts):
            try:
                position: Any = self._client.get_open_position(symbol)
                saw_position = True
                raw_available = (
                    position.get("qty_available")
                    if isinstance(position, dict)
                    else getattr(position, "qty_available", None)
                )
                if raw_available is None:
                    raw_available = (
                        position.get("qty", 0)
                        if isinstance(position, dict)
                        else getattr(position, "qty", 0)
                    )
                available = abs(Decimal(str(raw_available or 0)))
                qty = min(requested, available)
                qty = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
                if qty > 0:
                    if qty < requested:
                        log.info(
                            "Adjusted closing quantity for %s from %s to available %s",
                            symbol,
                            requested,
                            qty,
                        )
                    return QuantityResolution(True, float(qty), "broker_position")
            except AttributeError:
                # The SDK payload shape changed or the position vanished;
                # a lookup failure must fail closed but stay observable.
                log.warning(
                    "Position payload for %s is missing expected fields on attempt %d/%d",
                    symbol,
                    attempt + 1,
                    attempts,
                )
                break
            except Exception as exc:
                if not attempted_logged:
                    log.warning(
                        "Quantity lookup failed for %s on attempt %d/%d: %s",
                        symbol,
                        attempt + 1,
                        attempts,
                        exc,
                    )
                    attempted_logged = True
            if attempt + 1 < attempts:
                time.sleep(0.2)

        if saw_position:
            return QuantityResolution(True, 0.0, "broker_position_no_available_qty")
        if not reserve_fee_on_lookup_failure:
            return QuantityResolution(False, 0.0, "broker_lookup_failed")
        requested *= Decimal("1") - _CRYPTO_TAKER_FEE_RESERVE
        requested = (requested / step).to_integral_value(rounding=ROUND_DOWN) * step
        return QuantityResolution(False, float(requested), "verified_fill_fee_reserve")

    def _submit_stop(
        self,
        symbol: str,
        market: Market,
        qty: float,
        stop_price: float,
        position_is_long: bool,
        client_order_id: str | None = None,
    ) -> bool:
        client_order_id = client_order_id or f"parayabuck-stop-{uuid4().hex}"
        side = OrderSide.SELL if position_is_long else OrderSide.BUY
        step = self._price_step(symbol, market, stop_price)
        stop_price = self._round_to_step(stop_price, step)
        qty_number: float = qty
        equity_tif = TimeInForce.GTC
        if market == Market.EQUITY:
            qty_decimal = Decimal(str(qty))
            if qty_decimal == qty_decimal.to_integral_value():
                qty_number = int(qty_decimal)
            else:
                # Alpaca supports fractional equity stops only with DAY time-in-force.
                qty_number = float(
                    qty_decimal.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
                )
                equity_tif = TimeInForce.DAY
        if qty_number <= 0 or stop_price <= 0:
            log.warning(
                "Skipping protective stop for %s: qty=%s stop=%s rounds to zero",
                symbol,
                qty,
                stop_price,
            )
            return False
        try:
            if market == Market.CRYPTO:
                offset = Decimal(
                    str(getattr(self, "crypto_stop_limit_offset_bps", 25.0))
                ) / Decimal("10000")
                stop_decimal = Decimal(str(stop_price))
                distance_steps = max(
                    1,
                    int((stop_decimal * offset / step).to_integral_value(rounding=ROUND_CEILING)),
                )
                limit_decimal = stop_decimal + (
                    -distance_steps * step if position_is_long else distance_steps * step
                )
                limit_price = float(limit_decimal)
                if limit_price <= 0:
                    log.warning(
                        "Skipping protective stop for %s: limit price rounds to %s",
                        symbol,
                        limit_price,
                    )
                    return False
                request: Any = StopLimitOrderRequest(
                    symbol=symbol,
                    qty=qty_number,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                    stop_price=stop_price,
                    limit_price=limit_price,
                    client_order_id=client_order_id,
                )
            else:
                request = StopOrderRequest(
                    symbol=symbol,
                    qty=qty_number,
                    side=side,
                    time_in_force=equity_tif,
                    stop_price=stop_price,
                    client_order_id=client_order_id,
                )
            result = self._submit_with_recovery(request, client_order_id)
            if market == Market.CRYPTO:
                broker_id = str(self._field(result, "id", "") or "")
                ledger = self.ledger
                if broker_id and ledger is not None and "-stop" in client_order_id:
                    entry_client_order_id = client_order_id.rsplit("-stop", 1)[0]
                    ledger.register_protective_stop(
                        self._account_id or "unknown",
                        entry_client_order_id,
                        client_order_id,
                        broker_id,
                    )
            return True
        except AmbiguousOrderState:
            log.critical(
                "Stop submit ambiguous: market=%s symbol=%s order_client_id=%s "
                "recovery_action=protection_reconciliation_required",
                market.value,
                symbol,
                client_order_id,
            )
            raise
        except Exception as exc:
            available = self._available_qty_from_error(exc)
            if market == Market.CRYPTO and 0 < available < qty_number:
                available = self._round_qty_down(symbol, available)
                if available > 0:
                    log.warning(
                        "Retrying protective stop for %s with Alpaca available qty %s",
                        symbol,
                        available,
                    )
                    try:
                        retry_client_order_id = f"parayabuck-stop-{uuid4().hex}"
                        retry_request = StopLimitOrderRequest(
                            symbol=symbol,
                            qty=available,
                            side=side,
                            time_in_force=TimeInForce.GTC,
                            stop_price=stop_price,
                            limit_price=limit_price,
                            client_order_id=retry_client_order_id,
                        )
                        result = self._submit_with_recovery(retry_request, retry_client_order_id)
                        ledger = self.ledger
                        broker_id = str(self._field(result, "id", "") or "")
                        if ledger is not None and broker_id and "-stop" in client_order_id:
                            ledger.register_protective_stop(
                                self._account_id or "unknown",
                                client_order_id.rsplit("-stop", 1)[0],
                                retry_client_order_id,
                                broker_id,
                            )
                        return True
                    except AmbiguousOrderState:
                        raise
                    except Exception as retry_exc:
                        exc = retry_exc
            log.error("Protective stop failed for %s: %s", symbol, exc)
            return False

    def _round_qty_down(self, symbol: str, qty: float) -> float:
        step = self._qty_step(symbol)
        return float((Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_DOWN) * step)

    def _qty_step(self, symbol: str) -> Decimal:
        """The asset's minimum trade increment, or a conservative default."""
        increment = getattr(self._asset(symbol), "min_trade_increment", None)
        return Decimal(str(increment)) if increment is not None else Decimal("0.000000001")

    @staticmethod
    def _available_qty_from_error(exc: Exception) -> float:
        message = str(exc)
        start = message.find("{")
        if start < 0:
            return 0.0
        try:
            payload = json.loads(message[start:])
            return max(0.0, float(payload.get("available", 0)))
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0.0

    def _flatten(self, intent: OrderIntent, qty: float) -> tuple[Fill, str]:
        # Never call close_position(symbol): it closes the account's full
        # quantity and can consume manually-owned units of the same asset.
        side = OrderSide.SELL if intent.direction == Direction.LONG else OrderSide.BUY
        closing_qty = qty
        if intent.market == Market.CRYPTO:
            closing_qty = self._available_position_qty(
                intent.symbol,
                qty,
                reserve_fee_on_lookup_failure=intent.direction == Direction.LONG,
            )
        if closing_qty <= 0:
            raise RuntimeError(f"No available quantity to flatten for {intent.symbol}")
        before_qty = self._position_qty(intent.symbol)
        exit_client_order_id = f"{intent.client_order_id}-emergency-exit"
        result = self._submit_with_recovery(
            MarketOrderRequest(
                symbol=normalize_symbol(intent.symbol, intent.market).trading_symbol,
                qty=closing_qty,
                side=side,
                time_in_force=TimeInForce.GTC,
                client_order_id=exit_client_order_id,
            ),
            exit_client_order_id,
        )
        order_id = self._field(result, "id")
        if not order_id:
            raise AmbiguousOrderState(f"Emergency exit has no broker id for {intent.symbol}")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                order = self._client.get_order_by_id(order_id)
            except Exception as exc:
                # An emergency exit is exactly where a transient lookup error
                # must not abort the poll: keep polling like _wait_for_fill.
                log.warning("Emergency exit status lookup failed for %s: %s", order_id, exc)
                time.sleep(0.25)
                continue
            status = self._normalized_field(order, "status")
            if status == "filled":
                expected = max(0.0, before_qty - closing_qty)
                after_qty = self._position_qty(intent.symbol)
                tolerance = max(1e-9, closing_qty * 1e-8)
                if after_qty <= expected + tolerance:
                    price = float(self._field(order, "filled_avg_price", 0) or 0)
                    if price <= 0:
                        raise AmbiguousOrderState(
                            f"Emergency exit fill price is unverified for {intent.symbol}"
                        )
                    return (
                        Fill(
                            exit_client_order_id,
                            intent.symbol,
                            closing_qty,
                            price,
                            Direction.FLAT,
                            datetime.now(UTC),
                        ),
                        str(order_id),
                    )
                raise AmbiguousOrderState(
                    f"Emergency exit filled but position quantity is unverified for {intent.symbol}"
                )
            if status in {"rejected", "canceled", "cancelled", "expired"}:
                raise RuntimeError(
                    f"Emergency exit {status} for exact managed quantity {intent.symbol}"
                )
            time.sleep(0.25)
        raise AmbiguousOrderState(f"Emergency exit timed out for {intent.symbol}")

    def _position_qty(self, symbol: str) -> float:
        try:
            position = self._client.get_open_position(normalize_symbol(symbol).trading_symbol)
        except Exception as exc:
            message = str(exc).lower()
            if "position does not exist" in message or "404" in message:
                return 0.0
            raise
        return abs(float(self._field(position, "qty", 0) or 0))

    def process_crypto_price(self, observation: CryptoPriceObservation) -> str | None:
        """Drive synthetic crypto OCO using an idempotent ledger claim.

        The broker-side stop always remains active until a target or failover
        path has atomically won the exit claim and its cancellation is confirmed.
        """
        ledger = self.ledger
        if ledger is None or not self._account_id:
            return None
        symbol = canonical_symbol(observation.symbol, Market.CRYPTO)
        lock = self._symbol_exit_locks.setdefault(symbol, threading.Lock())
        if not lock.acquire(blocking=False):
            log.warning(
                "Crypto exit observation deferred because symbol lock is busy: "
                "symbol=%s source=%s observed_at=%s",
                symbol,
                observation.source,
                observation.timestamp.isoformat(),
            )
            return None
        try:
            entries = [
                item
                for item in ledger.managed_entries_for_account(self._account_id)
                if item.market == Market.CRYPTO and item.canonical_symbol == symbol
            ]
            for item in entries:
                reason: str | None = None
                target_seen = observation.high >= item.original_target
                active_stop = item.current_stop or item.original_stop
                stop_seen = observation.low <= active_stop
                if target_seen and stop_seen:
                    log.critical(
                        "Intrabar path ambiguous: market=crypto symbol=%s "
                        "entry_client_order_id=%s stop_state=%s target_state=%s "
                        "recovery_action=resolve_broker_stop",
                        symbol,
                        item.entry_client_order_id,
                        item.stop_state,
                        item.target_state,
                    )
                    if not item.protective_stop_broker_order_id:
                        ledger.mark_manual_intervention(
                            self._account_id, item.entry_client_order_id
                        )
                        raise AmbiguousOrderState(
                            f"Intrabar stop/target order is unknown for {symbol}"
                        )
                    stop = self._client.get_order_by_id(item.protective_stop_broker_order_id)
                    stop_status = self._normalized_field(stop, "status")
                    if stop_status == "filled":
                        claimed = ledger.claim_symbol_exit(
                            self._account_id,
                            item.entry_client_order_id,
                            reason="intrabar_stop",
                        )
                        if claimed is not None:
                            self._cancel_stop_then_exit(claimed, "intrabar_stop")
                        return "stop"
                    ledger.mark_manual_intervention(self._account_id, item.entry_client_order_id)
                    raise AmbiguousOrderState(
                        f"Intrabar stop/target path unresolved for {symbol}: {stop_status}"
                    )
                if target_seen:
                    reason = "target"
                else:
                    emergency_price = active_stop * (
                        1 - self.crypto_emergency_exit_bps / 10_000
                    )
                    if observation.low <= emergency_price:
                        reason = "stop_failover"
                    elif observation.low <= active_stop and item.stop_state == "open":
                        ledger.set_exit_state(
                            self._account_id,
                            item.entry_client_order_id,
                            stop_state="triggered_unfilled",
                        )
                        log.warning(
                            "Crypto stop triggered but remains unfilled: market=crypto "
                            "symbol=%s source=%s",
                            symbol,
                            observation.source,
                        )
                if reason is None:
                    continue
                claimed = ledger.claim_symbol_exit(
                    self._account_id,
                    item.entry_client_order_id,
                    reason=reason,
                )
                if claimed is None:
                    continue
                log.warning(
                    "Crypto %s triggered: market=crypto symbol=%s managed_qty=%s source=%s",
                    reason,
                    symbol,
                    claimed.owned_qty,
                    observation.source,
                )
                self._cancel_stop_then_exit(claimed, reason)
                return reason
            return None
        finally:
            lock.release()

    def _cancel_stop_then_exit(self, position: ManagedPosition, reason: str) -> None:
        ledger = self.ledger
        if ledger is None:
            raise AmbiguousOrderState(
                f"Cannot cancel stop for {position.canonical_symbol}: no ledger"
            )
        stop_id = position.protective_stop_broker_order_id
        if not stop_id:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(
                f"Protective stop id is unknown for {position.canonical_symbol}"
            )
        try:
            self._client.cancel_order_by_id(stop_id)
        except Exception as exc:
            # A timeout can mean either cancel succeeded or the stop filled;
            # _poll_stop_cancellation disambiguates. Log the cause for the
            # operator instead of hiding it.
            log.warning(
                "Cancel request for stop %s of %s reported an error: %s",
                stop_id,
                position.canonical_symbol,
                exc,
            )
        stop, stop_status = self._poll_stop_cancellation(position, stop_id)
        if stop_status == "filled":
            filled_qty = float(self._field(stop, "filled_qty", 0) or 0)
            filled_price = float(self._field(stop, "filled_avg_price", 0) or 0)
            if filled_qty > 0 and filled_price > 0:
                ledger.apply_execution_event(
                    ExecutionEvent(
                        self._account_id,
                        position.protective_stop_client_order_id
                        or f"{position.entry_client_order_id}-stop",
                        stop_id,
                        None,
                        OrderRole.PROTECTIVE_STOP,
                        position.canonical_symbol,
                        Market.CRYPTO,
                        OrderState.FILLED,
                        position.owned_qty,
                        filled_qty,
                        filled_price,
                        datetime.now(UTC),
                    )
                )
            ledger.set_exit_state(
                self._account_id,
                position.entry_client_order_id,
                stop_state="filled",
                target_state="cancelled_by_stop_fill",
            )
            return
        if stop_status not in {"canceled", "cancelled", "expired"}:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(
                f"Stop cancellation is unconfirmed for {position.canonical_symbol}: "
                f"{stop_status or 'unknown'}"
            )
        ledger.set_exit_state(
            self._account_id,
            position.entry_client_order_id,
            stop_state="cancelled",
        )
        qty = self._available_position_qty(
            position.canonical_symbol,
            position.owned_qty,
            reserve_fee_on_lookup_failure=False,
        )
        if qty <= 0:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(
                f"No verified managed quantity available for {position.canonical_symbol}"
            )
        suffix = "target-exit" if reason == "target" else "emergency-exit"
        client_id = f"{position.entry_client_order_id}-{suffix}"
        result = self._submit_with_recovery(
            MarketOrderRequest(
                symbol=normalize_symbol(position.canonical_symbol, Market.CRYPTO).trading_symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                client_order_id=client_id,
            ),
            client_id,
        )
        broker_id = str(self._field(result, "id", "") or "")
        if not broker_id:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(f"Exit broker id is unknown for {position.canonical_symbol}")
        ledger.set_exit_state(
            self._account_id,
            position.entry_client_order_id,
            target_state="target_exit_submitted"
            if reason == "target"
            else "emergency_exit_submitted",
            exit_client_order_id=client_id,
            exit_broker_order_id=broker_id,
        )
        intent = OrderIntent(
            position.source_signal_id,
            position.canonical_symbol,
            Market.CRYPTO,
            Direction.LONG,
            qty,
            position.filled_avg_price,
            position.original_stop,
            position.original_target,
            position.strategy,
            client_order_id=client_id,
        )
        state, fill = self._wait_for_fill(broker_id, intent, qty, timeout=5.0)
        if fill is not None:
            ledger.apply_execution_event(
                ExecutionEvent(
                    self._account_id,
                    client_id,
                    broker_id,
                    None,
                    OrderRole.TAKE_PROFIT if reason == "target" else OrderRole.EMERGENCY_EXIT,
                    position.canonical_symbol,
                    Market.CRYPTO,
                    state,
                    qty,
                    fill.qty,
                    fill.price,
                    fill.timestamp,
                )
            )
        if state == OrderState.FILLED:
            ledger.set_exit_state(
                self._account_id,
                position.entry_client_order_id,
                target_state="target_exit_filled"
                if reason == "target"
                else "emergency_exit_filled",
            )
            return
        if state == OrderState.PARTIALLY_FILLED:
            ledger.set_exit_state(
                self._account_id,
                position.entry_client_order_id,
                target_state="target_exit_partially_filled",
            )
            try:
                self._client.cancel_order_by_id(broker_id)
            except Exception as exc:
                log.warning(
                    "Partial crypto exit cancel response ambiguous: broker_order_id=%s error=%s",
                    broker_id,
                    exc,
                )
            terminal_state, final_fill = self._wait_for_fill(
                broker_id,
                intent,
                qty,
                timeout=getattr(self, "crypto_stop_cancel_timeout_seconds", 5.0),
            )
            if final_fill is not None:
                ledger.apply_execution_event(
                    ExecutionEvent(
                        self._account_id,
                        client_id,
                        broker_id,
                        None,
                        OrderRole.TAKE_PROFIT if reason == "target" else OrderRole.EMERGENCY_EXIT,
                        position.canonical_symbol,
                        Market.CRYPTO,
                        terminal_state,
                        qty,
                        final_fill.qty,
                        final_fill.price,
                        final_fill.timestamp,
                    )
                )
            if terminal_state == OrderState.FILLED:
                ledger.set_exit_state(
                    self._account_id,
                    position.entry_client_order_id,
                    target_state="target_exit_filled"
                    if reason == "target"
                    else "emergency_exit_filled",
                )
                return
            if terminal_state not in {
                OrderState.CANCELLED,
                OrderState.REJECTED,
                OrderState.EXPIRED,
            }:
                ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
                raise AmbiguousOrderState(
                    "Partial crypto exit cancellation is unconfirmed for "
                    f"{position.canonical_symbol}: {terminal_state.value}"
                )
            self._reprotect_after_terminal_exit(position)
            return
        ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
        raise AmbiguousOrderState(
            f"Crypto {reason} exit is not terminal for {position.canonical_symbol}: {state.value}"
        )

    def _poll_stop_cancellation(self, position: Any, stop_id: str) -> tuple[Any, str]:
        ledger = self.ledger
        if ledger is None:
            raise AmbiguousOrderState(
                f"Stop cancellation is unverified for {position.canonical_symbol}: no ledger"
            )
        deadline = time.monotonic() + getattr(self, "crypto_stop_cancel_timeout_seconds", 5.0)
        last_stop: Any = None
        last_status = ""
        cancellation_lookup_logged = False
        while time.monotonic() < deadline:
            try:
                last_stop = self._client.get_order_by_id(stop_id)
                last_status = self._normalized_field(last_stop, "status")
                filled_qty = float(self._field(last_stop, "filled_qty", 0) or 0)
                filled_price = float(self._field(last_stop, "filled_avg_price", 0) or 0)
                if filled_qty > 0 and filled_price > 0:
                    state = (
                        OrderState.FILLED
                        if last_status == "filled"
                        else OrderState.PARTIALLY_FILLED
                    )
                    ledger.apply_execution_event(
                        ExecutionEvent(
                            self._account_id,
                            position.protective_stop_client_order_id
                            or f"{position.entry_client_order_id}-stop",
                            stop_id,
                            None,
                            OrderRole.PROTECTIVE_STOP,
                            position.canonical_symbol,
                            Market.CRYPTO,
                            state,
                            position.owned_qty,
                            filled_qty,
                            filled_price,
                            datetime.now(UTC),
                        )
                    )
                if last_status in {"filled", "canceled", "cancelled", "expired"}:
                    log.info(
                        "Stop cancellation confirmed: market=crypto symbol=%s "
                        "order_client_id=%s broker_order_id=%s stop_state=%s",
                        position.canonical_symbol,
                        position.protective_stop_client_order_id,
                        stop_id,
                        last_status,
                    )
                    return last_stop, last_status
                if last_status == "rejected":
                    break
            except Exception as exc:
                # A read failure is not evidence that the stop disappeared.
                # Log the first failure so a permanently failing endpoint is
                # not indistinguishable from a slow one.
                if not cancellation_lookup_logged:
                    log.warning(
                        "Stop cancellation lookup failed for %s (%s): %s",
                        position.canonical_symbol,
                        stop_id,
                        exc,
                    )
                    cancellation_lookup_logged = True
            time.sleep(getattr(self, "crypto_stop_cancel_poll_interval_seconds", 0.01))
        ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
        raise AmbiguousOrderState(
            f"Stop cancellation is unconfirmed for {position.canonical_symbol}: "
            f"{last_status or 'lookup_failed'}"
        )

    def _validate_pre_submit(self, intent: OrderIntent) -> None:
        if intent.market == Market.EQUITY:
            get_clock = getattr(self._client, "get_clock", None)
            if get_clock is not None:
                clock: Any = get_clock()
                is_open = clock.get("is_open", False) if isinstance(clock, dict) else clock.is_open
                if not bool(is_open):
                    raise ValueError(
                        "Equity market is closed; refusing to queue stale order for "
                        f"{intent.symbol}"
                    )
        if getattr(self._client, "get_orders", None) is not None and self._has_open_order(
            intent.symbol
        ):
            raise ValueError(f"Open order already exists for {intent.symbol}")

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
                try:
                    recovered: Any = get_by_client_id(client_order_id)
                except Exception:
                    recovered = None
                if recovered is not None:
                    status = self._normalized_field(recovered, "status")
                    if status in {"rejected", "canceled", "cancelled", "expired"}:
                        # A broker-confirmed terminal response is known, not
                        # ambiguous. Preserve the original submit failure.
                        raise submit_exc
                    log.warning(
                        "Recovered Alpaca order %s after submit error: %s",
                        client_order_id,
                        submit_exc,
                    )
                    return recovered
            raise AmbiguousOrderState(
                f"Broker write outcome unknown for client_order_id={client_order_id}: {submit_exc}"
            ) from submit_exc

    def _has_open_order(self, symbol: str) -> bool:
        orders = self._client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
        )
        if not orders:
            return False
        return any(
            same_symbol(str(self._field(order, "symbol", symbol)), symbol) for order in orders
        )

    def _has_protective_order(
        self, symbol: str, position_is_long: bool, required_qty: float
    ) -> bool:
        protective_orders = self._open_protective_orders(symbol, position_is_long)
        protected = sum(
            (abs(Decimal(str(self._field(order, "qty", 0) or 0))) for order in protective_orders),
            start=Decimal("0"),
        )
        matches, self._last_protection_mismatch = self._protection_matches(
            protected, Decimal(str(required_qty))
        )
        self._last_protected_qty = float(protected)
        return matches

    def _open_protective_orders(self, symbol: str, position_is_long: bool) -> list[Any]:
        orders: Any = self._client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
        )
        if isinstance(orders, dict):
            return []
        closing_side = "sell" if position_is_long else "buy"
        canonical = canonical_symbol(symbol)
        ledger = self.ledger
        account_id = getattr(self, "_account_id", "") or "unknown"
        legacy_external_management = ledger is None and bool(
            getattr(self, "manage_external_positions", True)
        )
        managed_order_ids = (
            ledger.managed_order_ids(account_id, canonical) if ledger is not None else set()
        )
        managed_client_ids = (
            ledger.managed_client_order_ids(account_id, canonical) if ledger is not None else set()
        )

        def is_owned(order: Any, inherited: bool = False) -> bool:
            order_id = str(self._field(order, "id", "") or "")
            client_id = str(self._field(order, "client_order_id", "") or "")
            parent_id = str(self._field(order, "parent_order_id", "") or "")
            return (
                inherited
                or legacy_external_management
                or order_id in managed_order_ids
                or parent_id in managed_order_ids
                or client_id in managed_client_ids
                or client_id.startswith("parayabuck-")
            )

        protective: list[Any] = []

        def visit(order: Any, inherited: bool = False) -> None:
            owned = is_owned(order, inherited)
            side = self._normalized_field(order, "side")
            order_type = self._normalized_field(order, "type")
            stop_price = self._field(order, "stop_price")
            status = self._normalized_field(order, "status")
            if (
                owned
                and side == closing_side
                and status
                not in {
                    "canceled",
                    "cancelled",
                    "done_for_day",
                    "expired",
                    "filled",
                    "rejected",
                    "replaced",
                    "stopped",
                }
                and (
                    stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"}
                )
            ):
                protective.append(order)
                return
            for leg in self._field(order, "legs") or ():
                visit(leg, owned)

        for order in orders or ():
            visit(order)
        return protective

    def _cancel_protective_orders(self, symbol: str, position_is_long: bool) -> None:
        for order in self._open_protective_orders(symbol, position_is_long):
            order_id = self._field(order, "id")
            if order_id:
                self._client.cancel_order_by_id(order_id)
                self._wait_for_cancel_confirmation(str(order_id), symbol)

    def _wait_for_cancel_confirmation(self, order_id: str, symbol: str) -> None:
        deadline = time.monotonic() + getattr(self, "crypto_stop_cancel_timeout_seconds", 5.0)
        last_status = ""
        while time.monotonic() < deadline:
            try:
                order = self._client.get_order_by_id(order_id)
                last_status = self._normalized_field(order, "status")
                if last_status in {"canceled", "cancelled", "expired", "rejected"}:
                    return
                if last_status == "filled":
                    raise AmbiguousOrderState(
                        f"Protective order filled while cancellation was pending for {symbol}"
                    )
            except AmbiguousOrderState:
                raise
            except Exception:
                # A lookup failure is not proof that the protective order was
                # cancelled. Keep polling until the bounded deadline.
                pass
            time.sleep(getattr(self, "crypto_stop_cancel_poll_interval_seconds", 0.25))
        raise AmbiguousOrderState(
            f"Protective order cancellation is unconfirmed for {symbol}: "
            f"{last_status or 'lookup_failed'}"
        )

    @staticmethod
    def _protection_matches(protected: Decimal, required: Decimal) -> tuple[bool, str]:
        tolerance = max(Decimal("0.000000001"), required * Decimal("0.000000001"))
        mismatch = (
            "missing"
            if protected == 0
            else ("under" if protected < required - tolerance else "over")
        )
        return abs(protected - required) <= tolerance, mismatch

    def _normalized_field(self, value: Any, name: str) -> str:
        return str(self._field(value, name, "")).lower().split(".")[-1]

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        return field_value(value, name, default)

    def _load_peak_equity(self) -> float:
        if not self._peak_state_path.exists():
            return 0.0
        try:
            return max(0.0, float(json.loads(self._peak_state_path.read_text())["peak_equity"]))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            log.warning("Could not read persisted equity peak; starting from current equity")
            return 0.0

    def _save_peak_equity(self) -> None:
        self._peak_state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._peak_state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"peak_equity": self._peak_equity}))
        temporary.replace(self._peak_state_path)


__all__ = ["AlpacaBroker", "CryptoPriceObservation"]
