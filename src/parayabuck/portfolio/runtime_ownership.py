"""Ownership calculations — managed / external inventory.

Extracted from ``PortfolioStateRepository.owned_quantities`` to make the
net-ownership invariant explicit and testable without DB.

The repository still owns persistence; this module owns the pure math.
"""

from __future__ import annotations

from parayabuck.portfolio.ownership_rebase import QUANTITY_TOLERANCE


class OwnershipInconsistency(RuntimeError):
    """Net ownership is negative for one or more symbols (an invariant violation).

    ``net`` maps each negative symbol to its net quantity. It is a
    ``RuntimeError`` so existing handlers keep catching it; the operator
    rebase reads the raw net instead, so the recovery path is not blocked.
    """

    def __init__(self, net: dict[str, float]) -> None:
        self.net = dict(net)
        super().__init__(
            "Negative net portfolio ownership (invariant violation): "
            + ", ".join(f"{s}={q:.6f}" for s, q in sorted(net.items()))
        )


def net_owned_quantities(
    order_rows: list[tuple[str, str, float]],
    adjustments: list[tuple[str, float]],
    *,
    allow_negative: bool = False,
) -> dict[str, float]:
    """Compute net owned quantity per symbol from filled orders and adjustments.

    ``order_rows``: iterable of (symbol, side, filled_qty)
    ``adjustments``: iterable of (symbol, signed_outstanding_qty) where
      outstanding = quantity - consumed_quantity and the sign is -1 for a
      reduction (a liquidation, a stale clear, a rebase down onto the
      broker's count) or +1 for an increase (a split rebase or an acquisition).

    Raises :class:`OwnershipInconsistency` on negative net ownership (invariant
    violation) unless ``allow_negative`` is set, which keeps the negative
    entries for an operator who must see them to recover.
    Filters out near-zero dust (|qty| <= 1e-12).
    """
    net: dict[str, float] = {}
    gross: dict[str, float] = {}
    for symbol, side, filled in order_rows:
        if filled <= 0:
            continue
        net[symbol] = net.get(symbol, 0.0) + (filled if side == "buy" else -filled)
        gross[symbol] = gross.get(symbol, 0.0) + filled
    for symbol, adjusted in adjustments:
        net[symbol] = net.get(symbol, 0.0) + float(adjusted or 0.0)

    # The same tolerance the lot band uses (F3-4-2): float noise that the band
    # absorbs must not trip the net guard, so a negative is measured against
    # the symbol's gross filled quantity, relative to QUANTITY_TOLERANCE, with
    # the 1e-9 absolute floor.
    def below_zero(symbol: str) -> bool:
        return net[symbol] < -max(1e-9, gross.get(symbol, 0.0) * QUANTITY_TOLERANCE)

    negative = {s: q for s, q in net.items() if below_zero(s)}
    if negative and not allow_negative:
        raise OwnershipInconsistency(negative)
    if allow_negative:
        return {s: q for s, q in net.items() if q > 1e-12 or below_zero(s)}
    return {s: q for s, q in net.items() if q > 1e-12}


__all__ = ["OwnershipInconsistency", "net_owned_quantities"]
