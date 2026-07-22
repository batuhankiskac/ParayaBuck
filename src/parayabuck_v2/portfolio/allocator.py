from __future__ import annotations

from parayabuck_v2.core.models import AccountSnapshot, Signal


class PortfolioAllocator:
    """Select a limited set of non-duplicated opportunities for a cycle."""

    def __init__(self, max_new_positions_per_cycle: int = 1) -> None:
        self.max_new_positions_per_cycle = max_new_positions_per_cycle

    def select(self, ranked: list[Signal], account: AccountSnapshot) -> list[Signal]:
        held = {position.symbol for position in account.positions if not position.is_flat}
        available = [signal for signal in ranked if signal.symbol not in held]
        capacity = max(0, self.max_new_positions_per_cycle)
        return available[:capacity]
