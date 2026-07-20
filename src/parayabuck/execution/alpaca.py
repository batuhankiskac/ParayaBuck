from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide
from alpaca.trading.enums import TimeInForce as AlpacaTIF
from alpaca.trading.requests import (
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

    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        self._client = TradingClient(api_key, secret_key, paper=paper)
        # alpaca-py never sets an HTTP timeout → a slow API hangs the engine
        # for tens of minutes. Patch the underlying session so every call is
        # bounded; the engine's per-cycle try/except then handles the error.
        patch_session_timeout(self._client._session, timeout=15.0)
        self._market_prices: dict[str, float] = {}
        self._current_time: datetime = datetime.now(UTC)

    def update_market_price(
        self, symbol: str, price: float, timestamp: datetime | None = None
    ) -> None:
        self._market_prices[symbol] = price
        if timestamp is not None:
            self._current_time = timestamp

    def submit_order(self, order: Order) -> Order:
        side = _SIDE_MAP[order.side]
        tif = _TIF_MAP[order.tif]

        try:
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
                    )
            elif order.order_type == PBOderType.LIMIT:
                req = LimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=side,
                    time_in_force=tif,
                    limit_price=order.limit_price,
                )
            elif order.order_type == PBOderType.STOP:
                req = StopOrderRequest(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=side,
                    time_in_force=tif,
                    stop_price=order.stop_price,
                )
            elif order.order_type == PBOderType.STOP_LIMIT:
                req = StopLimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=side,
                    time_in_force=tif,
                    stop_price=order.stop_price,
                    limit_price=order.limit_price,
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

    def _wait_for_fill(self, order: Order, timeout: float = 5.0) -> Order:
        if order.order_id is None:
            return order

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                alpaca_order: Any = self._client.get_order_by_id(order.order_id)
                status = str(alpaca_order.status).lower()
                # Handle both "filled" and "OrderStatus.FILLED" formats
                if "filled" in status:
                    order.filled_price = float(alpaca_order.filled_avg_price or 0)
                    order.filled_qty = float(alpaca_order.filled_qty or 0)
                    order.status = OrderStatus.FILLED
                    order.filled_at = datetime.now(UTC)
                    log.info(f"Filled: {order.symbol} {order.qty}@{order.filled_price}")
                    return order
                elif status in ("canceled", "rejected", "expired"):
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
        except Exception:
            return Position(symbol=symbol)

    def get_account(self) -> Account:
        acct: Any = self._client.get_account()
        cash = float(acct.cash or 0)
        equity = float(acct.equity or 0)
        last_equity = float(acct.last_equity or equity)

        unreal = 0.0
        positions: dict[str, Position] = {}
        try:
            alpaca_positions: Any = self._client.get_all_positions()
            for p in alpaca_positions:
                qty = float(p.qty)
                avg = float(p.avg_entry_price)
                current = float(p.current_price)
                pos = Position(symbol=p.symbol, qty=qty, avg_price=avg)
                unreal += (current - avg) * qty
                positions[p.symbol] = pos
        except Exception:
            pass

        daily_pnl = equity - last_equity

        return Account(
            balance=cash,
            equity=equity,
            peak_equity=equity,
            realized_pnl=0.0,
            unrealized_pnl=unreal,
            daily_pnl=daily_pnl,
            positions=positions,
        )

    def get_trades(self) -> list[Trade]:
        """Get recent fills from Alpaca (not yet fully implemented)."""
        return []
