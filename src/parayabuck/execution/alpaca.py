from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide
from alpaca.trading.enums import TimeInForce as AlpacaTIF
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

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

log = get_logger("parayabuck.execution.alpaca")

_SIDE_MAP = {Side.BUY: OrderSide.BUY, Side.SELL: OrderSide.SELL}
_TIF_MAP = {
    TimeInForce.DAY: AlpacaTIF.DAY,
    TimeInForce.GTC: AlpacaTIF.GTC,
    TimeInForce.IOC: AlpacaTIF.IOC,
}


class AlpacaBroker(Broker):
    """Live/paper broker via Alpaca Trading API."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        self._client = TradingClient(api_key, secret_key, paper=paper)
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

        if order.order_type == PBOderType.MARKET:
            req: MarketOrderRequest | LimitOrderRequest = MarketOrderRequest(
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
        else:
            order.status = OrderStatus.REJECTED
            return order

        try:
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

    def _wait_for_fill(self, order: Order, timeout: float = 5.0) -> Order:
        if order.order_id is None:
            return order

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                alpaca_order: Any = self._client.get_order_by_id(order.order_id)
                status = str(alpaca_order.status)

                if status in ("filled", "partially_filled"):
                    order.filled_price = float(alpaca_order.filled_avg_price or 0)
                    order.filled_qty = int(alpaca_order.filled_qty or 0)
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
