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
  audit row records which. A standard reverse-split ratio within about one
  share is accepted, because a reverse split pays cash for fractional shares.
- **Surplus** (the broker holds more shares than the repository owns) claims
  inventory the bot never bought, so it needs a standard split ratio *and* a
  broker average entry price that matches our total cost over the broker's
  share count. A foreign buy changes the quantity but leaves our own cost
  basis alone, so the quantity gap alone never proves a split.
- Anything else stays ``UNKNOWN``: a shortfall halts with an actionable
  incident, an unconfirmed surplus raises an operator alert (the extra shares
  stay external), and the operator rebases explicitly
  (``parayabuck ops rebase-ownership``).

Our own cost basis is the CURRENT position's, never the average of every BUY
ever filled: the broker's ``avg_entry_price`` describes the open lots only,
so after a few rebalances an all-history average would never corroborate a
real split. :func:`open_position_cost_bases` replays fills since the position
was last flat under both FIFO lot depletion and the average-cost method.

A split rescales only the lots that existed when it took effect. An operator
increase that is not a corroborated split is an acquisition and seeds a new
lot at a stated unit cost; an increase with neither proof is refused.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from parayabuck.core.split_ratios import KNOWN_SPLIT_RATIOS
from parayabuck.logger import get_logger

log = get_logger("parayabuck.portfolio.ownership_rebase")

# Alpaca reports quantities to 9 decimals; a relative band absorbs that noise.
QUANTITY_TOLERANCE = 1e-6

# A reverse split pays cash in lieu for fractional shares, so the broker count
# can sit up to about one share below the exact ratio. Applies to reductions
# only: a forward split does not round holdings down.
FRACTIONAL_SHARE_TOLERANCE = 1.0

# A reduction whose ratio is within this relative band of a standard ratio,
# yet outside the fractional tolerance, is ambiguous (a partial foreign sale
# or an inexact reverse split). It halts for an operator instead of being
# booked as a foreign sale.
NEAR_STANDARD_RELATIVE_BAND = 0.02

# A split divides the average entry price by the same ratio as the share
# count. Partial exits and cash dividends move our own average by far less
# than this band, and a foreign trade leaves it unchanged.
COST_BASIS_TOLERANCE = 0.02

# ``portfolio_ownership_adjustments.kind`` values written by an automatic
# rebase. They surface as reconciliation anomalies, so an operator can see
# every share-count change the runtime absorbed.
SPLIT_REBASE_KIND = "split_rebase"
FOREIGN_SELL_KIND = "foreign_sell_reduction"
# An operator increase the bot did not buy (a foreign purchase on the shared
# key). It seeds a lot at the unit cost stated with the adjustment.
ACQUISITION_KIND = "operator_acquisition"

MATCH = "match"
SPLIT = "split"
FOREIGN_SELL = "foreign_sell"
UNKNOWN = "unknown"

__all__ = [
    "ACQUIRE",
    "ACQUISITION_KIND",
    "ADJUST",
    "BUY",
    "COST_BASIS_TOLERANCE",
    "FOREIGN_SELL",
    "FOREIGN_SELL_KIND",
    "FRACTIONAL_SHARE_TOLERANCE",
    "KNOWN_SPLIT_RATIOS",
    "LotBookInconsistency",
    "MATCH",
    "NEAR_STANDARD_RELATIVE_BAND",
    "QUANTITY_TOLERANCE",
    "SELL",
    "SPLIT",
    "SPLIT_ADJUST",
    "SPLIT_REBASE_KIND",
    "UNKNOWN",
    "ShareChange",
    "classify_share_change",
    "format_quantity",
    "fractional_split_ratio",
    "known_split_ratio",
    "open_position_cost_bases",
    "open_position_lots",
]

BUY = "buy"
SELL = "sell"
# ``adjust`` is an audited ownership adjustment (signed quantity, no price).
# A reducing ``adjust`` is a foreign sale or a stale clear and depletes like
# a sell. An increasing ``adjust`` has no cost basis and is refused by the
# replay. ``split_adjust`` is a split rebase (either sign) and rescales the
# open lots: fewer shares for a reverse split, more for a forward split, with
# the total cost unchanged. ``acquire`` seeds a lot at a stated unit cost.
ADJUST = "adjust"
SPLIT_ADJUST = "split_adjust"
ACQUIRE = "acquire"


def format_quantity(value: float) -> str:
    """Share count as operator-facing text, exact to Alpaca's 9 decimals.

    ``:g`` keeps six significant digits and rounds 1034.567891 to 1034.57, so
    a copy-pasted rebase command would then adopt the wrong count.
    """
    text = f"{float(value):.9f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


class LotBookInconsistency(ValueError):
    """A sale or reducing adjustment exceeds the open lots the bot owns, or an
    event cannot be priced (an unpriced increase, a split with no lots).

    The lot book then cannot be trusted, so nothing derived from it (cost
    basis, gain fraction) may be used. ``symbol`` is empty when the replay was
    not given one; the caller that knows the symbol re-raises with it set.
    """

    def __init__(self, *, sell_quantity: float, open_quantity: float, symbol: str = "", detail: str = "") -> None:
        self.symbol = symbol
        self.sell_quantity = sell_quantity
        self.open_quantity = open_quantity
        self.detail = detail
        subject = f"Lot book inconsistent for {symbol or '<unknown symbol>'}"
        if detail:
            message = f"{subject}: {detail}"
        else:
            message = (
                f"{subject}: a reduction of {format_quantity(sell_quantity)} exceeds the "
                f"{format_quantity(open_quantity)} open lot quantity"
            )
        super().__init__(message)


def _replay_lots(events: Iterable[tuple[str, float, float | None]]) -> tuple[list[list[float]], float, float]:
    """Replay ownership events into ``(open FIFO lots, quantity, average cost total)``.

    ``lots`` are ``[quantity, unit cost]`` pairs, oldest first, exactly as
    :func:`open_position_cost_bases` and :func:`open_position_lots` need them.

    Raises :class:`LotBookInconsistency` when a sale or reducing adjustment
    is larger than the open quantity by more than the relative
    :data:`QUANTITY_TOLERANCE` (with a 1e-9 absolute floor). Such a fill
    cannot come from a consistent book; truncating it silently would hide it.
    An increase without a cost basis, or a split with no open lots, raises too.
    """
    lots: list[list[float]] = []  # FIFO [quantity, price]
    quantity = 0.0
    cost = 0.0  # average-cost method total
    for kind, raw_quantity, price in events:
        amount = float(raw_quantity)
        if kind == SPLIT_ADJUST:
            # A split moves the share count and leaves the total cost alone:
            # every open lot scales by the same ratio, price by its inverse.
            if quantity <= 1e-12 or quantity + amount <= 1e-12:
                raise LotBookInconsistency(
                    sell_quantity=abs(amount),
                    open_quantity=quantity,
                    detail="a split rescale needs open lots and a positive share count after it",
                )
            ratio = (quantity + amount) / quantity
            lots = [[lot_quantity * ratio, lot_price / ratio] for lot_quantity, lot_price in lots]
            quantity += amount
            continue
        if kind == ADJUST and amount > 0:
            # An increase that is not a split has no provenance and no price,
            # so the lots it adds cannot be costed. Refuse instead of guessing.
            raise LotBookInconsistency(
                sell_quantity=amount,
                open_quantity=quantity,
                detail="an unpriced ownership increase has no cost basis; it must be a split or an acquisition",
            )
        if kind == ACQUIRE:
            if price is None or not math.isfinite(float(price)) or float(price) <= 0 or amount <= 0:
                raise LotBookInconsistency(
                    sell_quantity=amount,
                    open_quantity=quantity,
                    detail="an acquisition needs a finite positive unit cost",
                )
            lots.append([amount, float(price)])
            quantity += amount
            cost += amount * float(price)
            continue
        if kind == BUY:
            if price is None or price <= 0 or amount <= 0:
                continue
            lots.append([amount, float(price)])
            quantity += amount
            cost += amount * float(price)
            continue
        # sell, or a reducing adjustment
        requested = abs(amount)
        if requested - quantity > max(1e-9, requested * QUANTITY_TOLERANCE):
            raise LotBookInconsistency(sell_quantity=requested, open_quantity=quantity)
        reduce = min(requested, quantity)
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
    return lots, quantity, cost


def open_position_lots(events: Iterable[tuple[str, float, float | None]]) -> tuple[tuple[float, float], ...]:
    """FIFO open lots of the current position as ``(quantity, unit cost)``.

    Same replay as :func:`open_position_cost_bases` (one buy or acquisition
    appends a lot, a sell or reducing adjustment consumes the oldest lots, a
    split rebase rescales them, a flat position clears them), but the whole
    lot list is returned oldest first instead of two averages. A ``buy``
    event's ``price`` is the lot's effective per-unit cost, so the A1u caller
    passes the fill price plus the buy's fees (the research engine's
    ``usd_price_with_fee``). Returns ``()`` when nothing is open.

    This is the downsize gain band's input and it must never raise into
    planning. An inconsistent lot book (see :class:`LotBookInconsistency`)
    returns ``()`` after logging an error and emitting a ``paper_incident``
    alert, so the band keeps its constant fallback.
    """
    try:
        lots, quantity, _ = _replay_lots(events)
    except LotBookInconsistency as error:
        # Imported here: the execution package imports the reconciler, which
        # imports this module, so a module-level import would cycle.
        from parayabuck.execution.alerting import emit_execution_alert

        log.error("Downsize gain band lot book unavailable: %s; keeping the constant band", error)
        emit_execution_alert(
            "paper_incident",
            message=f"{error}. The downsize gain band falls back to the constant band until the lot book is repaired.",
            category="lot_book_inconsistency",
            incident_id=(
                f"lot_book_inconsistency:{format_quantity(error.sell_quantity)}:{format_quantity(error.open_quantity)}"
            ),
            sell_quantity=error.sell_quantity,
            open_quantity=error.open_quantity,
        )
        return ()
    if quantity <= 1e-9:
        return ()
    return tuple((lot_quantity, lot_price) for lot_quantity, lot_price in lots if lot_quantity > 1e-12)


def open_position_cost_bases(events: Iterable[tuple[str, float, float | None]]) -> tuple[float, ...]:
    """Average entry price of the CURRENT position, under FIFO and average cost.

    ``events`` are chronological ``(kind, quantity, price)`` tuples: ``buy``,
    ``acquire`` and ``sell`` fills and acquisitions (positive quantity, unit
    price) and ``adjust`` / ``split_adjust`` audited ownership adjustments
    (signed quantity, no price). A ``split_adjust`` rescales the open lots in
    either direction (more shares or fewer, same total cost). Any other
    reduction depletes like a sell. Lots are discarded whenever the position
    goes flat. Returns ``(fifo, average_cost)`` — the same value twice when no
    sell touched the open lots — or ``()`` when nothing is open. Raises
    :class:`LotBookInconsistency` when the replay is not self-consistent.
    """
    lots, quantity, cost = _replay_lots(events)
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


def fractional_split_ratio(owned_quantity: float, broker_quantity: float) -> float | None:
    """The standard reverse-split ratio that explains ``broker_quantity`` from
    ``owned_quantity`` within :data:`FRACTIONAL_SHARE_TOLERANCE` shares.

    Only ratios below one qualify: a reverse split is the case that pays cash
    for fractional shares. The closest such ratio wins; None when none fits.
    """
    best: float | None = None
    best_gap = FRACTIONAL_SHARE_TOLERANCE
    for candidate in KNOWN_SPLIT_RATIOS:
        if candidate >= 1:
            continue
        gap = abs(broker_quantity - owned_quantity * candidate)
        if gap <= FRACTIONAL_SHARE_TOLERANCE and (best is None or gap < best_gap):
            best, best_gap = candidate, gap
    return best


def _near_standard_reverse_ratio(ratio: float) -> float | None:
    """A standard reverse-split ratio ``ratio`` sits within the near-standard band of."""
    for candidate in KNOWN_SPLIT_RATIOS:
        if candidate < 1 and abs(ratio - candidate) <= NEAR_STANDARD_RELATIVE_BAND * candidate:
            return candidate
    return None


def _cost_basis_confirms(
    owned_average_costs: Sequence[float | None],
    broker_average_cost: float | None,
    owned_quantity: float,
    broker_quantity: float,
) -> bool:
    """Whether the broker's average entry price equals our total cost over the
    broker's share count, for any of our cost-basis candidates (FIFO, average).

    The broker's average is total cost over its shares, and a split leaves the
    total cost alone. So ``owned_average * owned / broker`` is the average a
    split must produce, including any post-split buys already in the count.
    """
    if broker_average_cost is None or not math.isfinite(broker_average_cost) or broker_average_cost <= 0:
        return False
    if broker_quantity <= 1e-12:
        return False
    for owned in owned_average_costs:
        if owned is None or not math.isfinite(owned) or owned <= 0:
            continue
        expected = owned * owned_quantity / broker_quantity
        if abs(broker_average_cost - expected) <= COST_BASIS_TOLERANCE * expected:
            return True
    return False


def _cost_basis_unchanged(
    owned_average_costs: Sequence[float | None],
    broker_average_cost: float | None,
) -> bool:
    """Whether the broker's average entry price is still our own average cost.

    A corporate action changes the broker basis; a plain sale leaves it where
    it was. Only an unchanged basis proves that no split happened.
    """
    if broker_average_cost is None or not math.isfinite(broker_average_cost) or broker_average_cost <= 0:
        return False
    for owned in owned_average_costs:
        if owned is None or not math.isfinite(owned) or owned <= 0:
            continue
        if abs(broker_average_cost - owned) <= COST_BASIS_TOLERANCE * owned:
            return True
    return False


def classify_share_change(
    owned_quantity: float,
    broker_quantity: float,
    *,
    owned_average_cost: float | None = None,
    broker_average_cost: float | None = None,
    owned_average_cost_candidates: Sequence[float] = (),
    post_boundary_quantity: float = 0.0,
) -> ShareChange:
    """Classify one symbol's owned-vs-broker share count difference.

    ``owned_average_cost`` is the repository's average entry price of the
    whole current position (``owned_average_cost_candidates`` may add the
    other lot method's value); ``broker_average_cost`` is the broker position's
    ``avg_entry_price``. A share *increase* needs both sides to be accepted
    as a split; a share *decrease* at a standard split ratio needs the
    broker basis to corroborate it. An unchanged broker basis proves no
    corporate action occurred and makes it a foreign sell; any other basis
    halts for the operator instead of being booked as a sale.

    ``post_boundary_quantity`` is the owned quantity booked at or after the
    split's effective time (signed; post-split buys are positive). It is held
    out of the ratio, so a fill that landed after the split does not dilute
    it. The cost check still uses the whole position.
    """
    owned = float(owned_quantity)
    broker = float(broker_quantity)
    post = float(post_boundary_quantity)
    if not (math.isfinite(owned) and math.isfinite(broker) and math.isfinite(post)) or owned < 0 or broker < 0:
        raise ValueError("Share quantities must be finite and non-negative")
    if owned <= 1e-12 or broker <= 1e-12 or abs(broker - owned) <= max(1e-12, owned * QUANTITY_TOLERANCE):
        return ShareChange(MATCH, owned, broker, 1.0, "owned quantity matches the broker position")
    whole_ratio = broker / owned
    pre_owned = owned - post
    pre_broker = broker - post
    candidate: float | None = None
    ratio = whole_ratio
    if pre_owned > 1e-12 and pre_broker > 1e-12:
        ratio = pre_broker / pre_owned
        candidate = known_split_ratio(ratio)
        if candidate is None and broker < owned:
            candidate = fractional_split_ratio(pre_owned, pre_broker)
    corroborated = candidate is not None and _cost_basis_confirms(
        (owned_average_cost, *owned_average_cost_candidates), broker_average_cost, owned, broker
    )
    if broker < owned:
        if corroborated and candidate is not None:
            return ShareChange(
                SPLIT,
                owned,
                broker,
                candidate,
                f"{candidate:g}-for-1 reverse split confirmed by the broker average entry price",
            )
        if candidate is None:
            near = _near_standard_reverse_ratio(ratio)
            if near is not None:
                return ShareChange(
                    UNKNOWN,
                    owned,
                    broker,
                    ratio,
                    f"broker holds {ratio:.6f} of the owned quantity, close to a {near:g}x reverse split but "
                    f"outside the {FRACTIONAL_SHARE_TOLERANCE:g}-share fractional tolerance; halting for the operator",
                )
            return ShareChange(
                FOREIGN_SELL,
                owned,
                broker,
                whole_ratio,
                "broker holds fewer shares than owned and no standard split ratio explains the gap",
            )
        if _cost_basis_unchanged((owned_average_cost, *owned_average_cost_candidates), broker_average_cost):
            # The broker basis did not move, so no corporate action happened:
            # this is a real sale that happens to sit at a standard ratio.
            return ShareChange(
                FOREIGN_SELL,
                owned,
                broker,
                whole_ratio,
                f"broker holds fewer shares than owned at a {candidate:g}x ratio with an unchanged average "
                "entry price, so it is a foreign sell",
            )
        # A reverse split with a basis that neither corroborates nor stays put
        # (a cash-in-lieu fraction removed from the broker cost, or no broker
        # basis at all) must not be booked as a sale: it strips the wrong lots'
        # cost. Halt for the operator instead.
        return ShareChange(
            UNKNOWN,
            owned,
            broker,
            candidate,
            f"broker holds {candidate:g}x the owned quantity but its average entry price neither corroborates "
            "a reverse split nor stays unchanged; halting for the operator",
        )
    if corroborated and candidate is not None:
        return ShareChange(
            SPLIT,
            owned,
            broker,
            candidate,
            f"{candidate:g}-for-1 split confirmed by the broker average entry price",
        )
    if candidate is not None:
        return ShareChange(
            UNKNOWN,
            owned,
            broker,
            candidate,
            f"broker holds {candidate:g}x the owned quantity but its average entry price does not corroborate a split",
        )
    return ShareChange(
        UNKNOWN,
        owned,
        broker,
        whole_ratio,
        "broker holds more shares than owned and no standard split ratio explains the gap",
    )
