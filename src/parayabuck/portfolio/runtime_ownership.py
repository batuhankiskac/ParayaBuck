"""Ownership calculations — managed / external inventory.

Extracted from ``PortfolioStateRepository.owned_quantities`` to make the
net-ownership invariant explicit and testable without DB.

The repository still owns persistence; this module owns the pure math.
"""

from __future__ import annotations


def net_owned_quantities(
    order_rows: list[tuple[str, str, float]],
    adjustments: list[tuple[str, float]],
) -> dict[str, float]:
    """Compute net owned quantity per symbol from filled orders and adjustments.

    ``order_rows``: iterable of (symbol, side, filled_qty)
    ``adjustments``: iterable of (symbol, outstanding_adjusted_qty) where
      outstanding = quantity - consumed_quantity for stale_ownership_clear.

    Raises ``RuntimeError`` on negative net ownership (invariant violation).
    Filters out near-zero dust (|qty| <= 1e-12).
    """
    net: dict[str, float] = {}
    for symbol, side, filled in order_rows:
        if filled <= 0:
            continue
        net[symbol] = net.get(symbol, 0.0) + (filled if side == "buy" else -filled)
    for symbol, adjusted in adjustments:
        net[symbol] = net.get(symbol, 0.0) - float(adjusted or 0.0)

    negative = {s: q for s, q in net.items() if q < -1e-9}
    if negative:
        raise RuntimeError(
            "Negative net portfolio ownership (invariant violation): "
            + ", ".join(f"{s}={q:.6f}" for s, q in sorted(negative.items()))
        )
    return {s: q for s, q in net.items() if q > 1e-12}


__all__ = ["net_owned_quantities"]
