from __future__ import annotations

from parayabuck_v2.core.models import AccountSnapshot, Fill, OrderIntent, OrderState


class ShadowBroker:
    """Read-only broker for signal observation; it never creates a real order."""

    def __init__(self, account: AccountSnapshot) -> None:
        self._account = account
        self.observed: list[OrderIntent] = []

    def account(self) -> AccountSnapshot:
        return self._account

    def set_account(self, account: AccountSnapshot) -> None:
        self._account = account

    def submit(self, order: OrderIntent) -> tuple[OrderState, Fill | None]:
        self.observed.append(order)
        return OrderState.ACCEPTED, None

    def reconcile(self) -> None:
        return
