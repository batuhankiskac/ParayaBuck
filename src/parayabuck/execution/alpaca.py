from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrderByIdRequest,
    GetOrdersRequest,
    MarketOrderRequest,
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
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol, normalize_symbol, same_symbol
from parayabuck.utils import is_crypto
from parayabuck.utils.http import patch_session_timeout

log = get_logger("parayabuck.execution.alpaca")

_CRYPTO_TAKER_FEE_RESERVE = Decimal("0.0025")


class AmbiguousOrderState(RuntimeError):
    pass


class ProtectionReconciliationError(RuntimeError):
    pass


class AlpacaBroker:
    """Alpaca execution boundary using the engine's native models."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True,
        *,
        manage_external_positions: bool = False,
        ledger: Any = None,
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

    @property
    def account_suffix(self) -> str:
        return self._account_id[-4:] if self._account_id else ""

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
        ledger = getattr(self, "ledger", None)
        managed_symbols = (
            set(ledger.managed_for_account(self._account_id or "unknown"))
            if ledger is not None
            else set()
        )
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
            positions.append(
                PositionSnapshot(
                    symbol=canonical_symbol(str(raw.symbol), market),
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
                        if canonical_symbol(str(raw.symbol), market) in managed_symbols
                        else "external"
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
        crypto_status = str(getattr(account, "crypto_status", "ACTIVE")).upper().split(".")[-1]
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
        )

    def submit(self, intent: OrderIntent) -> tuple[OrderState, Fill | None]:
        try:
            self._validate_pre_submit(intent)
            qty, stop_price, target_price = self._validated_values(intent)
            side = OrderSide.BUY if intent.direction == Direction.LONG else OrderSide.SELL
            tif = TimeInForce.GTC if intent.market == Market.CRYPTO else TimeInForce.DAY
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
            result = self._submit_with_recovery(request, intent.client_order_id)
            result_id = self._field(result, "id")
            if not result_id:
                raise RuntimeError(f"Alpaca order response has no id for {intent.symbol}")
            ledger = getattr(self, "ledger", None)
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
                self._client.cancel_order_by_id(result_id)
                state, final_fill = self._wait_for_fill(str(result_id), intent, qty, timeout=2.0)
                if final_fill is not None:
                    fill = final_fill
                if state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
                    raise RuntimeError(
                        f"Crypto entry remained open after cancellation: {intent.symbol}"
                    )
                if fill is None:
                    return OrderState.CANCELLED, None
            if intent.market == Market.CRYPTO and fill is not None:
                protected = self._submit_crypto_stop(intent, fill.qty, stop_price)
                if not protected:
                    self._flatten(intent, fill.qty)
                    return OrderState.REJECTED, fill
            if fill is not None and ledger is not None:
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
            return state, fill
        except ValueError as exc:
            # Closed markets, unsupported shorts, and duplicate orders are
            # expected safety rejections rather than broker failures.
            log.warning("Order rejected: %s", exc)
            return OrderState.REJECTED, None
        except AmbiguousOrderState as exc:
            log.critical(
                "Order state is ambiguous; entries must halt pending reconciliation: %s", exc
            )
            return OrderState.RECONCILIATION_REQUIRED, None
        except Exception as exc:
            log.error("Order rejected: %s", exc)
            return OrderState.REJECTED, None

    def reconcile(self) -> None:
        account = self.account()
        manage_external = bool(getattr(self, "manage_external_positions", True))
        ledger = getattr(self, "ledger", None)
        managed = (
            ledger.managed_for_account(self._account_id or "unknown") if ledger is not None else {}
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
            if ownership is not None:
                stop_price = ownership.original_stop
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
            if position.market == Market.CRYPTO and position.qty_available == 0:
                # Alpaca may deduct the taker fee from the acquired asset. A
                # stop can therefore reserve every sellable unit while being
                # up to 0.25% smaller than the raw position quantity. Only use
                # this tolerance when Alpaca confirms nothing remains
                # available; otherwise reconcile protects the remaining units.
                required_qty = total_qty * float(Decimal("1") - _CRYPTO_TAKER_FEE_RESERVE)
                required_qty = self._round_qty_down(position.symbol, required_qty)
            has_protection = self._has_protective_order(
                position.symbol, is_long, required_qty
            )
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
                if ledger is not None and hasattr(ledger, "managed_entry_broker_order_ids"):
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
                    and hasattr(ledger, "set_protection_state")
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
                    and hasattr(ledger, "set_protection_state")
                ):
                    ledger.set_protection_state(
                        self._account_id or "unknown",
                        ownership.entry_client_order_id,
                        ProtectionState.REQUESTED,
                    )
                self._cancel_protective_orders(position.symbol, is_long)
            qty = min(total_qty, available_qty)
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
            try:
                protected = self._submit_stop(
                    position.symbol,
                    position.market,
                    qty,
                    stop_price,
                    is_long,
                    client_order_id=protective_client_id,
                )
            except TypeError as exc:
                if "client_order_id" not in str(exc):
                    raise
                # Compatibility for injected five-argument test/fake brokers.
                protected = self._submit_stop(
                    position.symbol,
                    position.market,
                    qty,
                    stop_price,
                    is_long,
                )
            if not protected:
                if (
                    ownership is not None
                    and ledger is not None
                    and hasattr(ledger, "set_protection_state")
                ):
                    ledger.set_protection_state(
                        self._account_id or "unknown",
                        ownership.entry_client_order_id,
                        ProtectionState.FAILED,
                    )
                    if hasattr(ledger, "mark_manual_intervention"):
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
                and hasattr(ledger, "set_protection_state")
            ):
                ledger.set_protection_state(
                    self._account_id or "unknown",
                    ownership.entry_client_order_id,
                    ProtectionState.ACTIVE,
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
            side = str(self._field(order, "side", "")).lower().split(".")[-1]
            order_type = str(self._field(order, "type", "")).lower().split(".")[-1]
            stop_price = self._field(order, "stop_price")
            status = str(self._field(order, "status", "")).lower().split(".")[-1]
            if (
                side == closing_side
                and status not in {"canceled", "expired", "filled", "rejected"}
                and (
                    stop_price is not None
                    or order_type in {"stop", "stop_limit", "trailing_stop"}
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
        required = Decimal(str(required_qty))
        tolerance = max(Decimal("0.000000001"), required * Decimal("0.000000001"))
        self._last_protection_mismatch = (
            "missing"
            if protected == 0
            else ("under" if protected < required - tolerance else "over")
        )
        return abs(protected - required) <= tolerance

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
        while time.monotonic() < deadline:
            try:
                order: Any = self._client.get_order_by_id(order_id)
                status = str(self._field(order, "status", "")).lower().split(".")[-1]
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
            except Exception:
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

    def _available_position_qty(
        self,
        symbol: str,
        requested_qty: float,
        *,
        reserve_fee_on_lookup_failure: bool,
        attempts: int = 5,
    ) -> float:
        """Return an increment-safe quantity Alpaca reports as available to close."""
        requested = Decimal(str(requested_qty))
        step_value = getattr(self._asset(symbol), "min_trade_increment", None)
        step = Decimal(str(step_value)) if step_value is not None else Decimal("0.000000001")
        saw_position = False

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
                    return float(qty)
            except AttributeError:
                break
            except Exception:
                pass
            if attempt + 1 < attempts:
                time.sleep(0.2)

        if saw_position:
            return 0.0
        if reserve_fee_on_lookup_failure:
            requested *= Decimal("1") - _CRYPTO_TAKER_FEE_RESERVE
        requested = (requested / step).to_integral_value(rounding=ROUND_DOWN) * step
        return float(requested)

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
                limit_price = self._round_to_step(
                    stop_price * (0.995 if position_is_long else 1.005), step
                )
                if position_is_long and limit_price >= stop_price:
                    limit_price = float(Decimal(str(stop_price)) - step)
                elif not position_is_long and limit_price <= stop_price:
                    limit_price = float(Decimal(str(stop_price)) + step)
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
            self._submit_with_recovery(request, client_order_id)
            return True
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
                        self._submit_with_recovery(retry_request, retry_client_order_id)
                        return True
                    except Exception as retry_exc:
                        exc = retry_exc
            log.error("Protective stop failed for %s: %s", symbol, exc)
            return False

    def _round_qty_down(self, symbol: str, qty: float) -> float:
        increment = getattr(self._asset(symbol), "min_trade_increment", None)
        step = Decimal(str(increment)) if increment is not None else Decimal("0.000000001")
        rounded = (Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_DOWN) * step
        return float(rounded)

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

    def _flatten(self, intent: OrderIntent, qty: float) -> None:
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
            order = self._client.get_order_by_id(order_id)
            status = str(self._field(order, "status", "")).lower().split(".")[-1]
            if status == "filled":
                expected = max(0.0, before_qty - closing_qty)
                after_qty = self._position_qty(intent.symbol)
                tolerance = max(1e-9, closing_qty * 1e-8)
                if after_qty <= expected + tolerance:
                    return
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
            get_by_client_id = getattr(self._client, "get_order_by_client_id", None)
            if get_by_client_id is not None:
                try:
                    recovered: Any = get_by_client_id(client_order_id)
                except Exception:
                    recovered = None
                if recovered is not None:
                    status = str(self._field(recovered, "status", "")).lower().split(".")[-1]
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
        orders: Any = self._client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
        )
        if isinstance(orders, dict):
            return False
        closing_side = "sell" if position_is_long else "buy"
        canonical = canonical_symbol(symbol)
        ledger = getattr(self, "ledger", None)
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

        def protective_qty(order: Any, inherited: bool = False) -> Decimal:
            owned = is_owned(order, inherited)
            side = str(self._field(order, "side", "")).lower().split(".")[-1]
            order_type = str(self._field(order, "type", "")).lower().split(".")[-1]
            stop_price = self._field(order, "stop_price")
            if (
                owned
                and side == closing_side
                and (
                    stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"}
                )
            ):
                return abs(Decimal(str(self._field(order, "qty", 0) or 0)))
            return sum(
                (protective_qty(leg, owned) for leg in (self._field(order, "legs") or ())),
                start=Decimal("0"),
            )

        protected = sum((protective_qty(order) for order in orders), start=Decimal("0"))
        required = Decimal(str(required_qty))
        tolerance = max(Decimal("0.000000001"), required * Decimal("0.000000001"))
        self._last_protection_mismatch = (
            "missing"
            if protected == 0
            else ("under" if protected < required - tolerance else "over")
        )
        return abs(protected - required) <= tolerance

    def _cancel_protective_orders(self, symbol: str, position_is_long: bool) -> None:
        orders: Any = self._client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
        )
        closing_side = "sell" if position_is_long else "buy"
        canonical = canonical_symbol(symbol)
        ledger = getattr(self, "ledger", None)
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

        def visit(order: Any, inherited: bool = False) -> None:
            order_id = str(self._field(order, "id", "") or "")
            client_id = str(self._field(order, "client_order_id", "") or "")
            parent_id = str(self._field(order, "parent_order_id", "") or "")
            owned = (
                inherited
                or legacy_external_management
                or order_id in managed_order_ids
                or parent_id in managed_order_ids
                or client_id in managed_client_ids
                or client_id.startswith("parayabuck-")
            )
            side = str(self._field(order, "side", "")).lower().split(".")[-1]
            order_type = str(self._field(order, "type", "")).lower().split(".")[-1]
            stop_price = self._field(order, "stop_price")
            if (
                owned
                and side == closing_side
                and (
                    stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"}
                )
            ):
                order_id = self._field(order, "id")
                if order_id:
                    self._client.cancel_order_by_id(order_id)
                return
            for leg in self._field(order, "legs") or ():
                visit(leg, owned)

        for order in orders or ():
            visit(order)

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

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


__all__ = ["AlpacaBroker"]
