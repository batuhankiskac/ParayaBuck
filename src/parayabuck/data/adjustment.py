"""Corporate-action basis authority for the strategy's SIGNAL history.

Two price domains exist and must never be conflated:

* EXECUTION domain: RAW as-traded stream bars (market state, gap repair,
  order sizing) — untouched by this module.
* SIGNAL domain: the provider's SPLIT-ADJUSTED daily history. The provider
  rebases that series retroactively on every corporate action, so it is the
  only authority on what the strategy should have seen historically.

Design v3:

* The verifier fetches ONE batched splits request covering the ENTIRE
  required strategy history window (max minimum_history_bars + margin), so
  a corporate action that retroactively rebased ANY bar the strategy reads
  is always inside authoritative coverage.
* ``merge_provider_authority`` overrides stored bars wherever the provider
  speaks, retains stored bars STRICTLY NEWER than the provider tail
  (provisional stream bars), and RETAINS stored bars OLDER than the
  provider window verbatim (the window spans every bar the strategy reads,
  so they are never decision inputs). A successful verification can
  therefore never shorten a healthy 274+ bar history.
* Adequacy is checked per symbol BEFORE anything is adopted: a partial or
  empty provider response fails closed instead of marking global success.
* Daily bars are identified by their New York SESSION DATE, never by the
  raw label instant: the runtime's stream aggregator labels a session at its
  09:30 ET open (13:30/14:30 UTC) while the provider labels the same session
  at 00:00 ET (04:00/05:00 UTC). Comparing instants would make a same-day
  provider bar never "cover" the stream decision bar and would keep both
  labels of one session side by side in the signal history.

All functions here are pure and deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from parayabuck.core.models import Bar
from parayabuck.core.split_ratios import KNOWN_SPLIT_RATIOS
from parayabuck.markets.timeframes import NEW_YORK

# A split-day move moves the ratio off the exact n-for-1 value (a 2-for-1 with
# a 12% rally reads 1.79), so the band is wide enough for ordinary split-day
# moves. An ordinary overnight close-to-close move reads near 1, far from every
# standard ratio, and would need a drop of about 40% or more to read as 2.0.
SPLIT_CONTINUITY_RELATIVE_BAND = 0.15
# A consecutive provider jump at or beyond this ratio (old/new, either way) is
# examined at all. A jump that is near a standard ratio is a breach; any other
# jump at this size (a 3:2 split, a crash day) is an operator anomaly only.
# It sits above the one-session drops a 2x index ETF takes on a bad day
# (about 25%), so ordinary moves are never examined.
SPLIT_CONTINUITY_JUMP_RATIO = 1.4


def signal_session_date(timestamp: datetime) -> date:
    """New York session date of an equity daily signal bar (label-agnostic)."""
    return timestamp.astimezone(NEW_YORK).date()


def covers_session(verified_through: datetime, required: datetime) -> bool:
    """Whether a provider watermark covers the session of ``required``.

    A provider bar for session D (labeled 00:00 ET) covers a decision bar for
    the same session D (labeled 09:30 ET). The provider publishes D's bar
    while D is still forming; that is sufficient here because this check
    guards the corporate-action BASIS of the signal (a split is effective
    from D's open), not the finality of D's close. A provider that has not
    published D at all still leaves D uncovered.
    """
    return signal_session_date(verified_through) >= signal_session_date(required)


def _coverage_span(provider: Sequence[Bar]) -> tuple[datetime, datetime] | None:
    """(oldest, newest) timestamps of a provider series, or None if empty."""
    if not provider:
        return None
    timestamps = [bar.timestamp for bar in provider]
    return min(timestamps), max(timestamps)


def adequate_coverage(
    provider: Sequence[Bar],
    *,
    required_oldest: datetime,
    required_newest: datetime | None = None,
    stored: Sequence[Bar] | None = None,
) -> bool:
    """Whether one symbol's provider series covers the REQUIRED window.

    ``required_oldest`` bounds the strategy's full history window;
    ``required_newest`` (a decision-day timestamp) must have its New York
    session present-or-exceeded (see ``covers_session``) before that day may
    be traded on this basis. When ``stored`` is given, every stored in-span
    session must also be present in the provider set: a mid-window provider
    hole is incompleteness (fail closed), not an authoritative gap — the
    merge would otherwise drop a good stored bar and advance the watermark
    past the hole.
    """
    span = _coverage_span(provider)
    if span is None:
        return False
    oldest, newest = span
    if oldest > required_oldest:
        return False
    if required_newest is not None and not covers_session(newest, required_newest):
        return False
    if stored is not None:
        provider_sessions = {signal_session_date(bar.timestamp) for bar in provider}
        oldest_session = signal_session_date(oldest)
        newest_session = signal_session_date(newest)
        for bar in stored:
            session = signal_session_date(bar.timestamp)
            if oldest_session <= session <= newest_session and session not in provider_sessions:
                return False
    return True


def _rebased_by_known_ratio(provider_close: float, stored_close: float) -> bool:
    """Whether a stored/provider close pair differs by a standard split ratio.

    Only the ratio decides: a stream-built close that differs from the
    provider by aggregation noise is not evidence of a rebase.
    """
    if not (math.isfinite(provider_close) and provider_close > 0 and math.isfinite(stored_close) and stored_close > 0):
        return False
    ratio = provider_close / stored_close
    return any(abs(ratio - candidate) <= SPLIT_CONTINUITY_RELATIVE_BAND * candidate for candidate in KNOWN_SPLIT_RATIOS)


@dataclass(frozen=True)
class SplitContinuityJump:
    """One consecutive provider jump that the stored history shares a pre-jump session with.

    ``ratio`` is the earlier close over the later close. ``standard`` is True
    when the ratio sits within the relative band of a KNOWN split ratio: only
    those jumps are a basis breach. A non-standard jump (a crash day, a 3:2
    split) is reported, never halted on.
    """

    previous_session: date
    session: date
    ratio: float
    standard: bool


def _continuity_jumps(provider: Sequence[Bar], stored: Sequence[Bar]) -> tuple[bool, tuple[SplitContinuityJump, ...]]:
    """(rebased, jumps) for one symbol's provider window against its stored history.

    ``rebased`` is True when a stored session sits at a standard split ratio
    from the provider close: the provider rebased its priors, a consistent
    basis, and no jump is a basis question. Otherwise every consecutive pair of
    final provider closes at or beyond ``SPLIT_CONTINUITY_JUMP_RATIO`` (either
    direction) is returned, when the stored history shares a session on its
    pre-jump side. Empty input or no shared session gives (False, ()).
    """
    if not provider or not stored:
        return False, ()
    provider_close: dict[date, float] = {}
    for bar in sorted(provider, key=lambda item: item.timestamp):
        provider_close[signal_session_date(bar.timestamp)] = bar.close
    stored_close: dict[date, float] = {}
    for bar in sorted(stored, key=lambda item: item.timestamp):
        stored_close[signal_session_date(bar.timestamp)] = bar.close
    overlap = sorted(session for session in provider_close if session in stored_close)
    if not overlap:
        return False, ()
    for session in overlap:
        if _rebased_by_known_ratio(provider_close[session], stored_close[session]):
            return True, ()
    jumps: list[SplitContinuityJump] = []
    ordered = sorted(provider_close.items())
    for (earlier, earlier_close), (later, later_close) in zip(ordered, ordered[1:]):
        if not (math.isfinite(earlier_close) and earlier_close > 0 and math.isfinite(later_close) and later_close > 0):
            continue
        ratio = earlier_close / later_close
        if not (ratio >= SPLIT_CONTINUITY_JUMP_RATIO or ratio <= 1.0 / SPLIT_CONTINUITY_JUMP_RATIO):
            continue
        # The jump is a basis question only when the stored history shares a
        # session on its unrebased pre-jump side.
        if any(session <= earlier for session in overlap):
            standard = any(
                abs(ratio - candidate) <= SPLIT_CONTINUITY_RELATIVE_BAND * candidate for candidate in KNOWN_SPLIT_RATIOS
            )
            jumps.append(SplitContinuityJump(earlier, later, ratio, standard))
    return False, tuple(jumps)


def split_continuity_breach(provider: Sequence[Bar], stored: Sequence[Bar]) -> bool:
    """Whether a provider window looks like a split it has not rebased yet.

    A split leaves the post-split closes on the new basis while the prior
    sessions still carry the pre-split basis. Adopting such a window pairs
    post-split closes with pre-split history, and a held position reads as a
    crash (a spurious SELL). Every consecutive pair of provider closes is
    scanned, not only the newest one: a stored post-split tail moves the split
    out of the newest position while the jump stays inside the window.

    A breach is a consecutive jump whose ratio sits near a KNOWN split ratio,
    on a window that has not been rebased. A non-standard jump (a 3:2 split, or
    a large one-day move such as a crash day) is NOT a breach: it is reported
    by ``split_continuity_anomaly`` for the operator, and never halts the book.
    """
    rebased, jumps = _continuity_jumps(provider, stored)
    return not rebased and any(jump.standard for jump in jumps)


def split_continuity_anomaly(provider: Sequence[Bar], stored: Sequence[Bar]) -> tuple[SplitContinuityJump, ...]:
    """Non-standard provider jumps on an unrebased window, for an operator warning.

    Empty for a rebased window, for a window with no stored overlap, and for
    jumps that are a standard split ratio (those are a breach, see
    ``split_continuity_breach``).
    """
    rebased, jumps = _continuity_jumps(provider, stored)
    if rebased:
        return ()
    return tuple(jump for jump in jumps if not jump.standard)


def merge_provider_authority(
    stored: Sequence[Bar],
    provider_splits: Sequence[Bar],
) -> tuple[tuple[Bar, ...], bool]:
    """Merge stored signal history with provider authority WITHOUT shrinking
    a long healthy history.

    Bars are matched by New York session date (``signal_session_date``), so a
    stream bar labeled at the 09:30 ET open and a provider bar labeled at
    00:00 ET for the same session are ONE bar, never two. Rules, in priority
    order:

    1. Provider-covered sessions take the PROVIDER bar verbatim (the
       retroactively rebased authority).
    2. Stored bars on sessions strictly NEWER than the provider's newest
       survive as the provisional stream tail (their basis is post-action
       once every covered session was rebased).
    3. Stored bars strictly OLDER than the provider's oldest are RETAINED
       verbatim — they are outside the verified window. Retention is sound
       because the verifier's provider window spans the strategy's whole
       required history (``adequate_coverage``), so any retained bar is one
       the strategy no longer reads; the result length therefore never drops
       below ``len(stored)`` minus in-span holes the provider explicitly
       does not list.
    4. Stored bars INSIDE the provider span that the provider does not list
       are dropped (provider holes are authoritative gaps).

    Returns ``(merged, changed)`` where ``changed`` compares timestamps and
    overlapping closes against ``stored``.
    """
    provider_by_ts = {bar.timestamp: bar for bar in provider_splits}
    if not provider_by_ts:
        return tuple(stored), False
    provider_sessions = {signal_session_date(timestamp) for timestamp in provider_by_ts}
    newest_provider = max(provider_sessions)
    oldest_provider = min(provider_sessions)

    merged: dict[datetime, Bar] = {}
    for bar in stored:
        session = signal_session_date(bar.timestamp)
        if session in provider_sessions:
            continue  # provider wins, whatever the stored bar's label
        if session > newest_provider or session < oldest_provider:
            merged[bar.timestamp] = bar
    merged.update(provider_by_ts)
    ordered = tuple(merged[key] for key in sorted(merged))
    changed = len(ordered) != len(stored)
    if not changed:
        stored_by_ts = {bar.timestamp: bar for bar in stored}
        for fresh in ordered:
            old = stored_by_ts.get(fresh.timestamp)
            if old is None:
                changed = True
                break
            # A same-label revision is a real change, whatever its size: the
            # provider republishes a session's bar when it stops being
            # partial, and that close is the decision input. The split
            # tolerance is for comparing DIFFERENT bases, not for hiding one
            # provider's own correction of its own label.
            if fresh is not old and fresh.close != old.close:
                changed = True
                break
    return ordered, changed


__all__ = [
    "adequate_coverage",
    "covers_session",
    "merge_provider_authority",
    "SplitContinuityJump",
    "signal_session_date",
    "split_continuity_anomaly",
    "split_continuity_breach",
]
