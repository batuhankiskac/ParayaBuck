from __future__ import annotations

from dataclasses import dataclass

from parayabuck.logger import get_logger
from parayabuck.models import Order, OrderType, Side, TimeInForce

log = get_logger("parayabuck.execution.bracket")


@dataclass
class BracketOrder:
    """A bracket order: entry + stop-loss + take-profit.

    When the entry fills, the stop and target are activated.
    If either stop or target fills, the other is cancelled (OCO).
    """

    entry: Order
    stop_loss: Order | None = None
    take_profit: Order | None = None
    filled_entry_price: float = 0.0
    active: bool = False
    closed: bool = False

    def on_entry_filled(self, fill_price: float) -> list[Order]:
        """Called when entry fills. Returns the stop & target orders to submit."""
        self.filled_entry_price = fill_price
        self.active = True
        orders: list[Order] = []
        if self.stop_loss:
            orders.append(self.stop_loss)
        if self.take_profit:
            orders.append(self.take_profit)
        return orders

    def on_stop_or_target_filled(self, filled_side: Side) -> None:
        """Called when stop or target fills. Marks bracket as closed."""
        self.active = False
        self.closed = True
        other = "target" if filled_side == Side.SELL else "stop"
        log.info(f"Bracket closed via {other} fill")


class TrailingStopManager:
    """Manages trailing stop-losses for open positions.

    As price moves favorably, the stop moves up (for longs) or down (for shorts).
    The stop never moves against the position.
    """

    def __init__(
        self,
        trail_pct: float = 0.02,
        min_profit_to_activate: float = 0.01,
    ) -> None:
        self.trail_pct = trail_pct
        self.min_profit_to_activate = min_profit_to_activate
        self._stops: dict[str, float] = {}
        self._entry_prices: dict[str, float] = {}

    def set_entry(self, symbol: str, entry_price: float, side: Side) -> None:
        """Record entry price and set initial stop."""
        self._entry_prices[symbol] = entry_price
        if side == Side.BUY:
            self._stops[symbol] = entry_price * (1 - self.trail_pct)
        else:
            self._stops[symbol] = entry_price * (1 + self.trail_pct)

    def update(self, symbol: str, current_price: float, side: Side) -> float | None:
        """Update trailing stop based on current price. Returns new stop or None.

        For longs: stop only moves up
        For shorts: stop only moves down
        """
        if symbol not in self._stops:
            return None

        entry = self._entry_prices.get(symbol, 0)
        if entry == 0:
            return None

        current_stop = self._stops[symbol]

        if side == Side.BUY:
            if current_price < entry * (1 + self.min_profit_to_activate):
                return current_stop
            new_stop = current_price * (1 - self.trail_pct)
            if new_stop > current_stop:
                self._stops[symbol] = new_stop
                log.debug(
                    f"Trailing stop {symbol} LONG: {current_stop:.2f} → {new_stop:.2f} "
                    f"(price={current_price:.2f})"
                )
            return self._stops[symbol]
        else:
            if current_price > entry * (1 - self.min_profit_to_activate):
                return current_stop
            new_stop = current_price * (1 + self.trail_pct)
            if new_stop < current_stop:
                self._stops[symbol] = new_stop
                log.debug(
                    f"Trailing stop {symbol} SHORT: {current_stop:.2f} → {new_stop:.2f} "
                    f"(price={current_price:.2f})"
                )
            return self._stops[symbol]

    def get_stop(self, symbol: str) -> float | None:
        return self._stops.get(symbol)

    def remove(self, symbol: str) -> None:
        self._stops.pop(symbol, None)
        self._entry_prices.pop(symbol, None)

    def should_exit(self, symbol: str, current_price: float, side: Side) -> bool:
        """Check if current price has hit the trailing stop."""
        stop = self._stops.get(symbol)
        if stop is None:
            return False
        if side == Side.BUY:
            return current_price <= stop
        else:
            return current_price >= stop

    def active_symbols(self) -> list[str]:
        return list(self._stops.keys())


def create_bracket(
    symbol: str,
    qty: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
    side: Side = Side.BUY,
) -> BracketOrder:
    """Create a bracket order (entry + stop + target).

    Entry: market order
    Stop: stop market order (opposite side)
    Target: limit order (opposite side)
    """
    exit_side = Side.SELL if side == Side.BUY else Side.BUY

    entry = Order(
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=OrderType.MARKET,
    )

    stop_loss = Order(
        symbol=symbol,
        side=exit_side,
        qty=qty,
        order_type=OrderType.STOP,
        stop_price=stop_price,
        tif=TimeInForce.GTC,
    )

    take_profit = Order(
        symbol=symbol,
        side=exit_side,
        qty=qty,
        order_type=OrderType.LIMIT,
        limit_price=target_price,
        tif=TimeInForce.GTC,
    )

    return BracketOrder(
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
