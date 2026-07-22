from __future__ import annotations

from typing import Protocol

from parayabuck.core.models import AccountSnapshot, Fill, OrderIntent, OrderState


class ExecutionBroker(Protocol):
    def account(self) -> AccountSnapshot: ...

    def submit(self, order: OrderIntent) -> tuple[OrderState, Fill | None]: ...

    def reconcile(self) -> None: ...
