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
- Anything else stays ``UNKNOWN``: a shortfall halts with an actionable
  incident, an unconfirmed surplus raises an operator alert (the extra shares
  stay external), and the operator rebases explicitly
  (``parayabuck ops rebase-ownership``).

Our own cost basis is the CURRENT position's, never the average of every BUY
ever filled: the broker's ``avg_entry_price`` describes the open lots only,
so after a few rebalances an all-history average would never corroborate a
real split. :func:`open_position_cost_bases` replays fills since the position
was last flat under both FIFO lot depletion and the average-cost method.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
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
    "ADJUST",
    "BUY",
    "COST_BASIS_TOLERANCE",
    "FOREIGN_SELL",
    "FOREIGN_SELL_KIND",
    "KNOWN_SPLIT_RATIOS",
    "MATCH",
    "QUANTITY_TOLERANCE",
    "SELL",
    "SPLIT",
    "SPLIT_ADJUST",
    "SPLIT_REBASE_KIND",
    "UNKNOWN",
    "ShareChange",
    "classify_share_change",
    "known_split_ratio",
    "open_position_cost_bases",
]

BUY = "buy"
SELL = "sell"
# ``adjust`` is an audited ownership adjustment (signed quantity, no price).
# A plain ``adjust`` reduction is a foreign sale or a stale clear and
# depletes like a sell; ``split_adjust`` is a reverse-split rebase and
# rescales the open lots (fewer shares, same total cost).
ADJUST = "adjust"
SPLIT_ADJUST = "split_adjust"


def open_position_cost_bases(events: Iterable[tuple[str, float, float | None]]) -> tuple[float, ...]:
    """Average entry price of the CURRENT position, under FIFO and average cost.

    ``events`` are chronological ``(kind, quantity, price)`` tuples: ``buy``
    and ``sell`` fills (positive quantity, fill price) and ``adjust`` for an
    audited ownership adjustment (signed quantity, no price). An addition is
    a split rebase and rescales the open lots (more shares, same total
    cost); a ``split_adjust`` decrease (a reverse split) rescales them the
    same way (fewer shares, same total cost). Any other reduction depletes
    like a sell. Lots are discarded whenever the position goes flat.
    Returns ``(fifo, average_cost)`` — the same value twice when no sell
    touched the open lots — or ``()`` when nothing is open.
    """
    lots: list[list[float]] = []  # FIFO [quantity, price]
    quantity = 0.0
    cost = 0.0  # average-cost method total
    for kind, raw_quantity, price in events:
        amount = float(raw_quantity)
        if kind == ADJUST and amount > 0:
            if quantity <= 1e-12:
                continue
            ratio = (quantity + amount) / quantity
            lots = [[lot_quantity * ratio, lot_price / ratio] for lot_quantity, lot_price in lots]
            quantity += amount
            continue
        if kind == SPLIT_ADJUST and amount < 0 and quantity > 1e-12 and quantity + amount > 1e-12:
            # Reverse split: fewer shares, same total cost. Rescale the open
            # lots (price by the inverse ratio) instead of depleting them
            # like a sale, which would leave the pre-split basis behind.
            ratio = quantity / (quantity + amount)
            lots = [[lot_quantity / ratio, lot_price * ratio] for lot_quantity, lot_price in lots]
            quantity += amount
            continue
        if kind == BUY:
            if price is None or price <= 0 or amount <= 0:
                continue
            lots.append([amount, float(price)])
            quantity += amount
            cost += amount * float(price)
            continue
        # sell, or a reducing adjustment
        reduce = min(abs(amount), quantity)
        if reduce <= 0:
            continue
        cost -= cost / quantity * reduce
        quantity -= reduce
        remaining = reduce
        while remaining > 1e-12 and lots:
            taken = min(remaining, lots[0][0])
            lots[0][0] -= taken
            remaining -= taken
            if lots[0][0] <= 1e-12:
                lots.pop(0)
        if quantity <= 1e-9:
            lots, quantity, cost = [], 0.0, 0.0
    if quantity <= 1e-9:
        return ()
    fifo_quantity = sum(lot_quantity for lot_quantity, _ in lots)
    if fifo_quantity <= 1e-12:
        return ()
    fifo = sum(lot_quantity * lot_price for lot_quantity, lot_price in lots) / fifo_quantity
    return (fifo, cost / quantity)


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
    owned_average_costs: Sequence[float | None],
    broker_average_cost: float | None,
    ratio: float,
) -> bool:
    """Whether the broker's average entry price agrees with ``owned / ratio``
    for any of our cost-basis candidates (FIFO, average cost)."""
    if broker_average_cost is None or not math.isfinite(broker_average_cost) or broker_average_cost <= 0:
        return False
    for owned in owned_average_costs:
        if owned is None or not math.isfinite(owned) or owned <= 0:
            continue
        expected = owned / ratio
        if abs(broker_average_cost - expected) <= COST_BASIS_TOLERANCE * expected:
            return True
    return False


def classify_share_change(
    owned_quantity: float,
    broker_quantity: float,
    *,
    owned_average_cost: float | None = None,
    broker_average_cost: float | None = None,
    owned_average_cost_candidates: Sequence[float] = (),
) -> ShareChange:
    """Classify one symbol's owned-vs-broker share count difference.

    ``owned_average_cost`` is the repository's average entry price of the
    current position (``owned_average_cost_candidates`` may add the other
    lot method's value); ``broker_average_cost`` is the broker position's
    ``avg_entry_price``. A share *increase* needs both sides to be accepted
    as a split; a share *decrease* at a standard split ratio needs the
    broker basis to corroborate it, otherwise it is a foreign sell (an
    unchanged broker basis proves no corporate action occurred).
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
        (owned_average_cost, *owned_average_cost_candidates), broker_average_cost, ratio_match
    )
    # The ratio reported on the result is the standard one when it matched, so
    # the audit row shows 2 rather than 1.9999998.
    matched = ratio if ratio_match is None else ratio_match
    if broker < owned:
        if ratio_match is not None and corroborated:
            return ShareChange(
                SPLIT,
                owned,
                broker,
                ratio_match,
                f"{ratio_match:g}-for-1 reverse split confirmed by the broker average entry price",
            )
        if ratio_match is not None:
            return ShareChange(
                FOREIGN_SELL,
                owned,
                broker,
                ratio,
                f"broker holds fewer shares than owned at a {ratio_match:g}x ratio but its average entry price "
                "does not corroborate a reverse split",
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
