"""Share-count changes on bot-owned symbols: splits and foreign sells.

Ownership is net fills, so a corporate action — or another consumer of the
shared Alpaca key — leaves the repository claiming a share count the broker
disagrees with. Nothing downstream can repair that: every decision (exits
included) drops, safety reset needs a clean reconcile, and a clean reconcile is
impossible while the counts differ. This module holds the pure classification
of "the broker's count differs from ours"; the repository books the audited
adjustment and the operator CLI is the fallback for anything not provable here.

A share change is rebased automatically only when it is provable:

- **Shortfall** (the broker holds fewer shares than the repository owns) is
  always adoptable: reducing ownership to the broker's own count can never
  over-claim a share. That is a foreign liquidation or a reverse split; the
  audit row records which.
- **Surplus** (the broker holds more shares than the repository owns) claims
  inventory the bot never bought, so it needs a standard split ratio *and* a
  broker average entry price divided by that same ratio. A foreign buy changes
  the quantity but leaves our own cost basis alone, so the quantity gap alone
  never proves a split.
- Anything else stays ``UNKNOWN``: the caller halts with an actionable
  incident and the operator rebases explicitly
  (``parayabuck ops rebase-ownership``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Standard split and reverse-split ratios a US equity issuer uses (n-for-1
# and its reciprocal). A non-standard ratio is deliberately absent: it must
# halt for an operator, never be guessed at.
KNOWN_SPLIT_RATIOS: tuple[float, ...] = (0.1, 0.125, 0.2, 0.25, 1 / 3, 0.5, 2.0, 3.0, 4.0, 5.0)

# Alpaca reports quantities to 9 decimals; a relative band absorbs that noise.
QUANTITY_TOLERANCE = 1e-6

# A split divides the average entry price by the same ratio as the share
# count. Partial exits and cash dividends move our own average by far less
# than this band, and a foreign trade leaves it unchanged.
COST_BASIS_TOLERANCE = 0.02

# ``portfolio_ownership_adjustments.kind`` values written by an automatic
# rebase. They surface as reconciliation anomalies, so an operator can see
# every share-count change the runtime absorbed.
SPLIT_REBASE_KIND = "split_rebase"
FOREIGN_SELL_KIND = "foreign_sell_reduction"

MATCH = "match"
SPLIT = "split"
FOREIGN_SELL = "foreign_sell"
UNKNOWN = "unknown"

__all__ = [
    "COST_BASIS_TOLERANCE",
    "FOREIGN_SELL",
    "FOREIGN_SELL_KIND",
    "KNOWN_SPLIT_RATIOS",
    "MATCH",
    "QUANTITY_TOLERANCE",
    "SPLIT",
    "SPLIT_REBASE_KIND",
    "UNKNOWN",
    "ShareChange",
    "classify_share_change",
    "known_split_ratio",
]


@dataclass(frozen=True)
class ShareChange:
    """One classified difference between owned and broker share counts."""

    kind: str
    owned_quantity: float
    broker_quantity: float
    ratio: float
    reason: str

    @property
    def adjustment_quantity(self) -> float:
        """Signed quantity that moves ownership onto the broker's count."""
        return self.broker_quantity - self.owned_quantity

    @property
    def rebasable(self) -> bool:
        return self.kind in {SPLIT, FOREIGN_SELL}


def known_split_ratio(ratio: float) -> float | None:
    """The standard split ratio ``ratio`` is within tolerance of, else None."""
    for candidate in KNOWN_SPLIT_RATIOS:
        if abs(ratio - candidate) <= max(1e-9, candidate * QUANTITY_TOLERANCE):
            return candidate
    return None


def _cost_basis_confirms(
    owned_average_cost: float | None,
    broker_average_cost: float | None,
    ratio: float,
) -> bool:
    """Whether the broker's average entry price agrees with ``owned / ratio``."""
    if owned_average_cost is None or broker_average_cost is None:
        return False
    if not math.isfinite(owned_average_cost) or not math.isfinite(broker_average_cost):
        return False
    if owned_average_cost <= 0 or broker_average_cost <= 0:
        return False
    expected = owned_average_cost / ratio
    return abs(broker_average_cost - expected) <= COST_BASIS_TOLERANCE * expected


def classify_share_change(
    owned_quantity: float,
    broker_quantity: float,
    *,
    owned_average_cost: float | None = None,
    broker_average_cost: float | None = None,
) -> ShareChange:
    """Classify one symbol's owned-vs-broker share count difference.

    ``owned_average_cost`` is the repository's own weighted average entry
    price (from BUY fills); ``broker_average_cost`` is the broker position's
    ``avg_entry_price``. Both are needed to accept a share *increase* as a
    split; a share decrease is adoptable without them.
    """
    owned = float(owned_quantity)
    broker = float(broker_quantity)
    if not math.isfinite(owned) or not math.isfinite(broker) or owned < 0 or broker < 0:
        raise ValueError("Share quantities must be finite and non-negative")
    ratio = broker / owned if owned > 1e-12 else 0.0
    if owned <= 1e-12 or broker <= 1e-12 or abs(broker - owned) <= max(1e-12, owned * QUANTITY_TOLERANCE):
        return ShareChange(MATCH, owned, broker, 1.0, "owned quantity matches the broker position")
    ratio_match = known_split_ratio(ratio)
    corroborated = ratio_match is not None and _cost_basis_confirms(
        owned_average_cost, broker_average_cost, ratio_match
    )
    # The ratio reported on the result is the standard one when it matched, so
    # the audit row shows 2 rather than 1.9999998.
    matched = ratio if ratio_match is None else ratio_match
    if broker < owned:
        if ratio_match is not None:
            return ShareChange(
                SPLIT,
                owned,
                broker,
                ratio_match,
                f"{ratio_match:g}-for-1 reverse split"
                + ("" if corroborated else " (broker cost basis does not corroborate it)"),
            )
        return ShareChange(
            FOREIGN_SELL,
            owned,
            broker,
            ratio,
            "broker holds fewer shares than owned and no standard split ratio explains the gap",
        )
    if corroborated:
        return ShareChange(
            SPLIT,
            owned,
            broker,
            matched,
            f"{ratio_match:g}-for-1 split confirmed by the broker average entry price",
        )
    if ratio_match is not None:
        return ShareChange(
            UNKNOWN,
            owned,
            broker,
            matched,
            f"broker holds {ratio_match:g}x the owned quantity but its average entry price does not corroborate a split",
        )
    return ShareChange(
        UNKNOWN,
        owned,
        broker,
        ratio,
        "broker holds more shares than owned and no standard split ratio explains the gap",
    )
