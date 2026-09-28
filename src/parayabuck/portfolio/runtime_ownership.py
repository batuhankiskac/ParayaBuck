"""Ownership calculations — managed / external inventory.

Extracted from ``PortfolioStateRepository.owned_quantities`` to make the
net-ownership invariant explicit and testable without DB.

The repository still owns persistence; this module owns the pure math.
"""

from __future__ import annotations

from collections.abc import Collection

# Crypto fills are booked fee-net at full float precision (e.g. ETH
# 0.9124956684375) while Alpaca holds and sells positions at 9 decimals, so a
# complete exit can leave a residue of a few units of the 9th decimal that the
# broker neither reports nor lets anyone trade. A crypto net below this floor
# (the reconciler's QUANTITY_MATCH_FLOOR) is flat, not owned inventory.
CRYPTO_OWNERSHIP_DUST = 1e-8


def net_owned_quantities(
    order_rows: list[tuple[str, str, float]],
    adjustments: list[tuple[str, float]],
    crypto_symbols: Collection[str] = (),
) -> dict[str, float]:
    """Compute net owned quantity per symbol from filled orders and adjustments.

    ``order_rows``: iterable of (symbol, side, filled_qty)
    ``adjustments``: iterable of (symbol, outstanding_adjusted_qty) where
      outstanding = quantity - consumed_quantity for stale_ownership_clear.

    Raises ``RuntimeError`` on negative net ownership (invariant violation).
    Filters out near-zero dust (|qty| <= 1e-12), and for ``crypto_symbols``
    any sub-precision residue below ``CRYPTO_OWNERSHIP_DUST``.
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
    return {s: q for s, q in net.items() if q > 1e-12 and not (s in crypto_symbols and q < CRYPTO_OWNERSHIP_DUST)}


__all__ = ["CRYPTO_OWNERSHIP_DUST", "net_owned_quantities"]
