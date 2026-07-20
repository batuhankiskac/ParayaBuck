from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus
from alpaca.trading.enums import TimeInForce as AlpacaTIF
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
    TrailingStopOrderRequest,
)

from parayabuck.execution.base import Broker
from parayabuck.logger import get_logger
from parayabuck.models import (
    Account,
    Order,
    OrderStatus,
    Position,
    Side,
    TimeInForce,
    Trade,
)
from parayabuck.models import OrderType as PBOderType
from parayabuck.utils import is_crypto
from parayabuck.utils.http import patch_session_timeout

log = get_logger("parayabuck.execution.alpaca")

_SIDE_MAP = {Side.BUY: OrderSide.BUY, Side.SELL: OrderSide.SELL}
_TIF_MAP = {
    TimeInForce.DAY: AlpacaTIF.DAY,
    TimeInForce.GTC: AlpacaTIF.GTC,
    TimeInForce.IOC: AlpacaTIF.IOC,
}


class AlpacaBroker(Broker):
    """Live/paper broker via Alpaca Trading API.

    Supports native bracket orders: when an Order carries both `stop_price`
    and `limit_price` (treated as the take-profit limit) and is a MARKET
    entry, the broker submits it as a single Alpaca bracket order with OCO
    stop-loss + take-profit legs. This means the protective orders live on
    Alpaca's servers and fire even if the bot is offline — a critical safety
    property that software-only stops don't have.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True,
        peak_state_path: str | Path | None = None,
    ) -> None:
        self._client = TradingClient(api_key, secret_key, paper=paper)
        # alpaca-py never sets an HTTP timeout → a slow API hangs the engine
        # for tens of minutes. Patch the underlying session so every call is
        # bounded; the engine's per-cycle try/except then handles the error.
        patch_session_timeout(self._client._session, timeout=15.0)
        self._market_prices: dict[str, float] = {}
        self._current_time: datetime = datetime.now(UTC)
        self._peak_state_path = Path(peak_state_path) if peak_state_path else None
        self._peak_equity = self._load_peak_equity()
        self._asset_cache: dict[str, Any] = {}
        self._market_clock_cache: tuple[float, bool] | None = None

    def _load_peak_equity(self) -> float:
        if self._peak_state_path is None or not self._peak_state_path.exists():
            return 0.0
        try:
            payload = json.loads(self._peak_state_path.read_text())
            return max(0.0, float(payload.get("peak_equity", 0.0)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            log.warning("Could not read persisted equity peak; starting from current equity")
            return 0.0

    def _save_peak_equity(self) -> None:
        if self._peak_state_path is None:
            return
        self._peak_state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._peak_state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"peak_equity": self._peak_equity}))
        temporary.replace(self._peak_state_path)

    def update_market_price(
        self, symbol: str, price: float, timestamp: datetime | None = None
    ) -> None:
        self._market_prices[symbol] = price
        if timestamp is not None:
            self._current_time = timestamp

    def submit_order(self, order: Order) -> Order:
        side = _SIDE_MAP[order.side]
        effective_tif = order.tif
        if is_crypto(order.symbol) and effective_tif == TimeInForce.DAY:
            effective_tif = TimeInForce.GTC
        tif = _TIF_MAP[effective_tif]
        order.client_order_id = order.client_order_id or f"parayabuck-{uuid.uuid4().hex}"

        try:
            self._validate_asset(order)
            req: Any
            if order.order_type == PBOderType.MARKET:
                # If the order carries both stop_price (stop-loss) and
                # limit_price (take-profit), submit as a native bracket
                # order so protective legs live on Alpaca's servers and
                # fire even if the bot crashes.
                if order.stop_price is not None and order.limit_price is not None:
                    req = self._build_bracket_request(order, side, tif)
                    log.info(
                        f"Bracket order submitted: {order.side.value} {order.qty} "
                        f"{order.symbol} stop={order.stop_price:.2f} "
                        f"tp={order.limit_price:.2f}"
                    )
                else:
                    req = MarketOrderRequest(
                        symbol=order.symbol,
                        qty=order.qty,
                        side=side,
                        time_in_force=tif,
                        client_order_id=order.client_order_id,
                    )
            elif order.order_type == PBOderType.LIMIT:
                req = LimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=side,
                    time_in_force=tif,
                    limit_price=order.limit_price,
                    client_order_id=order.client_order_id,
                )
            elif order.order_type == PBOderType.STOP:
                req = StopOrderRequest(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=side,
                    time_in_force=tif,
                    stop_price=order.stop_price,
                    client_order_id=order.client_order_id,
                )
            elif order.order_type == PBOderType.STOP_LIMIT:
                req = StopLimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=side,
                    time_in_force=tif,
                    stop_price=order.stop_price,
                    limit_price=order.limit_price,
                    client_order_id=order.client_order_id,
                )
            else:
                order.status = OrderStatus.REJECTED
                return order

            result: Any = self._client.submit_order(req)
            order.order_id = str(result.id)
            order.status = OrderStatus.PENDING
            log.info(
                f"Order submitted: {order.side.value} {order.qty} {order.symbol} → id={result.id}"
            )
            order = self._wait_for_fill(order)
        except Exception as e:
            log.error(f"Order rejected: {e}")
            order.status = OrderStatus.REJECTED
        return order

    def _validate_asset(self, order: Order) -> None:
        asset = self._asset_cache.get(order.symbol)
        if asset is None:
            asset = self._client.get_asset(order.symbol)
            self._asset_cache[order.symbol] = asset
        if not bool(getattr(asset, "tradable", False)):
            raise ValueError(f"Asset is not tradable: {order.symbol}")
        if not is_crypto(order.symbol) and not float(order.qty).is_integer():
            if not bool(getattr(asset, "fractionable", False)):
                raise ValueError(f"Asset is not fractionable: {order.symbol}")
        min_size = getattr(asset, "min_order_size", None)
        qty_increment = getattr(asset, "min_trade_increment", None)
        price_increment = getattr(asset, "price_increment", None)
        if min_size is not None and Decimal(str(order.qty)) < Decimal(str(min_size)):
            raise ValueError(f"Order is below minimum size for {order.symbol}: {min_size}")
        if qty_increment is not None:
            order.qty = float(
                (Decimal(str(order.qty)) / Decimal(str(qty_increment))).to_integral_value(
                    rounding=ROUND_DOWN
                )
                * Decimal(str(qty_increment))
            )
            if order.qty <= 0:
                raise ValueError(f"Order rounds to zero for {order.symbol}")
        if price_increment is not None:
            increment = Decimal(str(price_increment))
            if order.limit_price is not None:
                order.limit_price = float(
                    (Decimal(str(order.limit_price)) / increment).to_integral_value(
                        rounding=ROUND_HALF_UP
                    )
                    * increment
                )
            if order.stop_price is not None:
                order.stop_price = float(
                    (Decimal(str(order.stop_price)) / increment).to_integral_value(
                        rounding=ROUND_HALF_UP
                    )
                    * increment
                )

    @staticmethod
    def _build_bracket_request(
        order: Order, side: OrderSide, tif: AlpacaTIF
    ) -> MarketOrderRequest:
        """Build a native Alpaca bracket order request.

        The entry is a MARKET order; the stop-loss and take-profit legs are
        attached as `stop_loss` and `take_profit` and become OCO once the
        entry fills. Alpaca handles the OCO cancellation server-side, so
        when one protective leg fills the other is automatically cancelled.
        """
        from alpaca.trading.requests import StopLossRequest, TakeProfitRequest

        # Alpaca expects stop_loss / take_profit as the exit side's orders.
        # The StopLossRequest/TakeProfitRequest don't take a side — Alpaca
        # infers it from the entry side automatically. The outer caller has
        # already checked that both prices are non-None; assert defensively.
        assert order.stop_price is not None
        assert order.limit_price is not None
        stop_loss = StopLossRequest(stop_price=order.stop_price)
        take_profit = TakeProfitRequest(limit_price=order.limit_price)
        return MarketOrderRequest(
            symbol=order.symbol,
            qty=order.qty,
            side=side,
            time_in_force=tif,
            order_class=OrderClass.BRACKET,
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=order.client_order_id,
        )

    def submit_trailing_stop(
        self, symbol: str, qty: float, trail_pct: float, side: Side
    ) -> Order | None:
        """Submit a native Alpaca trailing-stop order.

        Alpaca maintains the stop on its servers and trails the highest/lowest
        price by `trail_pct` percent. Returns the local Order object with
        order_id populated, or None on failure.
        """
        try:
            req = TrailingStopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=_SIDE_MAP[side],
                time_in_force=AlpacaTIF.GTC,
                trail_percent=trail_pct * 100,
            )
            result: Any = self._client.submit_order(req)
            log.info(
                f"Trailing stop submitted: {side.value} {qty} {symbol} "
                f"trail={trail_pct:.1%} → id={result.id}"
            )
            return Order(
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=PBOderType.STOP,
                stop_price=0.0,  # trailing — no fixed price
                tif=TimeInForce.GTC,
                order_id=str(result.id),
                status=OrderStatus.PENDING,
            )
        except Exception as e:
            log.error(f"Trailing stop failed for {symbol}: {e}")
            return None

    def submit_crypto_protective_stop(
        self, symbol: str, qty: float, stop_price: float, position_is_long: bool
    ) -> Order:
        """Place Alpaca's supported GTC crypto stop-limit protection."""
        side = Side.SELL if position_is_long else Side.BUY
        precision = 6 if stop_price < 1 else 2
        stop_price = round(stop_price, precision)
        limit_price = round(stop_price * (0.995 if position_is_long else 1.005), precision)
        protective = Order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=PBOderType.STOP_LIMIT,
            stop_price=stop_price,
            limit_price=limit_price,
            tif=TimeInForce.GTC,
        )
        return self.submit_order(protective)

    def submit_protective_stop(
        self, symbol: str, qty: float, stop_price: float, position_is_long: bool
    ) -> Order:
        """Place a broker-resident stop for a restored position."""
        if is_crypto(symbol):
            return self.submit_crypto_protective_stop(
                symbol, qty, stop_price, position_is_long
            )
        return self.submit_order(
            Order(
                symbol=symbol,
                side=Side.SELL if position_is_long else Side.BUY,
                qty=qty,
                order_type=PBOderType.STOP,
                stop_price=stop_price,
                tif=TimeInForce.GTC,
            )
        )

    def _wait_for_fill(self, order: Order, timeout: float = 5.0) -> Order:
        if order.order_id is None:
            return order

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                alpaca_order: Any = self._client.get_order_by_id(order.order_id)
                status = str(alpaca_order.status).lower().split(".")[-1]
                if status == "filled":
                    order.filled_price = float(alpaca_order.filled_avg_price or 0)
                    order.filled_qty = float(alpaca_order.filled_qty or 0)
                    order.status = OrderStatus.FILLED
                    order.filled_at = datetime.now(UTC)
                    log.info(f"Filled: {order.symbol} {order.qty}@{order.filled_price}")
                    return order
                elif status == "partially_filled":
                    order.filled_price = float(alpaca_order.filled_avg_price or 0)
                    order.filled_qty = float(alpaca_order.filled_qty or 0)
                elif any(value in status for value in ("canceled", "cancelled")):
                    order.status = OrderStatus.CANCELLED
                    log.warning(f"Order {order.order_id} {status}")
                    return order
                elif any(value in status for value in ("rejected", "expired")):
                    order.status = OrderStatus.REJECTED
                    log.warning(f"Order {order.order_id} {status}")
                    return order
            except Exception:
                pass
            time.sleep(0.5)

        log.warning(f"Order {order.order_id} not filled within {timeout}s")
        return order

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._client.cancel_order_by_id(order_id)
            return True
        except Exception:
            return False

    def get_position(self, symbol: str) -> Position:
        try:
            pos: Any = self._client.get_open_position(symbol)
            qty = float(pos.qty)
            avg = float(pos.avg_entry_price)
            return Position(symbol=symbol, qty=qty, avg_price=avg)
        except APIError as exc:
            if getattr(exc, "status_code", None) == 404:
                return Position(symbol=symbol)
            raise

    def get_account(self) -> Account:
        acct: Any = self._client.get_account()
        if bool(getattr(acct, "account_blocked", False)) or bool(
            getattr(acct, "trading_blocked", False)
        ):
            raise RuntimeError("Alpaca account is blocked from trading")
        cash = float(acct.cash or 0)
        equity = float(acct.equity or 0)
        last_equity = float(acct.last_equity or equity)

        unreal = 0.0
        unreal_intraday = 0.0
        positions: dict[str, Position] = {}
        alpaca_positions: Any = self._client.get_all_positions()
        for p in alpaca_positions:
            qty = float(p.qty)
            avg = float(p.avg_entry_price)
            current = float(p.current_price)
            pos = Position(symbol=p.symbol, qty=qty, avg_price=avg, current_price=current)
            unreal += float(p.unrealized_pl or 0)
            unreal_intraday += float(p.unrealized_intraday_pl or 0)
            positions[p.symbol] = pos

        daily_pnl = equity - last_equity
        realized_today = daily_pnl - unreal_intraday

        previous_peak = self._peak_equity
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity != previous_peak:
            self._save_peak_equity()
        return Account(
            balance=cash,
            equity=equity,
            peak_equity=self._peak_equity,
            realized_pnl=realized_today,
            unrealized_pnl=unreal,
            daily_pnl=daily_pnl,
            positions=positions,
        )

    def get_trades(self) -> list[Trade]:
        """Get recent fills from Alpaca (not yet fully implemented)."""
        return []

    def is_market_open(self) -> bool:
        """Use Alpaca's calendar-aware clock (holidays and early closes included)."""
        now = time.monotonic()
        if self._market_clock_cache and now - self._market_clock_cache[0] < 10:
            return self._market_clock_cache[1]
        clock: Any = self._client.get_clock()
        is_open = bool(clock.get("is_open", False) if isinstance(clock, dict) else clock.is_open)
        self._market_clock_cache = (now, is_open)
        return is_open

    def has_open_order(self, symbol: str) -> bool:
        orders: Any = self._client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
        )
        return bool(orders)

    def cancel_open_orders(self, symbol: str, timeout: float = 3.0) -> bool:
        """Cancel and confirm all open orders before an external position exit."""
        orders: Any = self._client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
        )
        for open_order in orders:
            try:
                self._client.cancel_order_by_id(open_order.id)
            except APIError as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.has_open_order(symbol):
                return True
            time.sleep(0.2)
        return not self.has_open_order(symbol)
