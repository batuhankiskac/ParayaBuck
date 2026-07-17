from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from parayabuck.models import Bar, Order, Position


class Strategy(ABC):
    """Base class for all trading strategies.

    Subclasses implement on_bar() which receives each new bar and the
    current position, and returns an Order (or None to do nothing).

    Multi-symbol strategies (e.g. pairs trading) that need to emit more than
    one order per bar, or that require bars from several symbols, should:
      - set `self.symbols` to the list of symbols they consume, and
      - push extra orders via `self._pending_orders.append(...)` instead of
        returning them. Engines drain pending orders after each on_bar.
    """

    name: str = "base"
    symbols: list[str] | None = None

    def __init__(self, **params: Any) -> None:
        self.params = params
        self._bars_seen = 0
        self._pending_orders: list[Order] = []

    @abstractmethod
    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        """Called for each new bar. Return an Order or None."""
        ...

    def pop_pending_orders(self) -> list[Order]:
        """Return and clear any orders queued via on_bar (multi-leg strategies)."""
        orders = self._pending_orders
        self._pending_orders = []
        return orders

    def on_init(self) -> None:
        """Called once before the first bar. Override for setup."""
        pass

    def on_stop(self) -> None:
        """Called once after the last bar. Override for cleanup."""
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, params={self.params})"
