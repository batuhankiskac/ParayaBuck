from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from parayabuck.broker_models import Account, Order, Position


class Broker(ABC):
    """Abstract broker interface for order execution."""

    @abstractmethod
    def submit_order(self, order: Order) -> Order:
        """Submit an order and return the updated order with status."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. Return True if cancelled."""
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> Position:
        """Return current position for a symbol."""
        ...

    @abstractmethod
    def get_account(self) -> Account:
        """Return current account state."""
        ...

    @abstractmethod
    def update_market_price(
        self, symbol: str, price: float, timestamp: datetime | None = None
    ) -> None:
        """Update the current market price (used for unrealized PnL)."""
        ...
