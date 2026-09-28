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
# Hiding the residue is not enough: it accumulates across round trips. A crypto
# SELL fill that leaves a net in (0, CRYPTO_OWNERSHIP_DUST] therefore books a
# non-consumable adjustment of this kind that zeroes it. It counts in the net
# like any adjustment, but a later real SELL consumes it only by the exact
# shortfall that would otherwise make the net negative (never more, which would
# resurrect the dust), and operator anomaly listings leave it out.
CRYPTO_DUST_CLEAR_KIND = "crypto_dust_clear"
# The mirror case: an exit that sells slightly more than the fee-net booked
# quantity (broker 9-decimal rounding above the ledger) leaves a net in
# [-CRYPTO_OWNERSHIP_DUST, 0) once dust clears are used up. That SELL books a
# credit of this kind that returns the net to exactly 0. A credit ADDS its
# quantity to the net (every other kind subtracts), is never consumed, and is
# left out of operator anomaly listings. A larger shortfall is a real
# oversell and still trips the ownership invariant.
CRYPTO_DUST_CREDIT_KIND = "crypto_dust_credit"
# A crypto stale-clear smaller than this (broker flat, local residue that
# accumulated before the per-exit dust clear existed) is recorded with the
# dust kind too. Real crypto positions are orders of magnitude larger
# (Alpaca's minimum crypto order sizes are >= 1e-5), so only ledger residue
# falls below it.
CRYPTO_STALE_DUST_BAND = 1e-6


def net_owned_quantities(
    order_rows: list[tuple[str, str, float]],
    adjustments: list[tuple[str, float]],
    crypto_symbols: Collection[str] = (),
) -> dict[str, float]:
    """Compute net owned quantity per symbol from filled orders and adjustments.

    ``order_rows``: iterable of (symbol, side, filled_qty)
    ``adjustments``: iterable of (symbol, outstanding_adjusted_qty) where
      outstanding = quantity - consumed_quantity over every adjustment kind,
      negated for ``CRYPTO_DUST_CREDIT_KIND`` (a credit adds to the net).

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


__all__ = [
    "CRYPTO_DUST_CLEAR_KIND",
    "CRYPTO_DUST_CREDIT_KIND",
    "CRYPTO_OWNERSHIP_DUST",
    "CRYPTO_STALE_DUST_BAND",
    "net_owned_quantities",
]
