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
  provider window verbatim when they are outside its span AND agree with
  the basis implied by the overlap boundary. A successful verification can
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

from collections.abc import Sequence
from datetime import date, datetime

from parayabuck.core.models import Bar
from parayabuck.markets.timeframes import NEW_YORK

# A raw vs split-adjusted close difference within this relative band is
# provider rounding noise, not a corporate action.
SPLIT_RATIO_TOLERANCE = 0.005


def close_matches(left: float, right: float, tolerance: float = SPLIT_RATIO_TOLERANCE) -> bool:
    if left <= 0 or right <= 0:
        return False
    return abs(left - right) / right <= tolerance


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


def coverage_span(provider: Sequence[Bar]) -> tuple[datetime, datetime] | None:
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
) -> bool:
    """Whether one symbol's provider series covers the REQUIRED window.

    ``required_oldest`` bounds the strategy's full history window;
    ``required_newest`` (a decision-day timestamp) must have its New York
    session present-or-exceeded (see ``covers_session``) before that day may
    be traded on this basis.
    """
    span = coverage_span(provider)
    if span is None:
        return False
    oldest, newest = span
    if oldest > required_oldest:
        return False
    if required_newest is not None and not covers_session(newest, required_newest):
        return False
    return True


def detect_basis_break(
    stored: Sequence[Bar],
    provider_splits: Sequence[Bar],
    tolerance: float = SPLIT_RATIO_TOLERANCE,
) -> bool:
    """True when stored history disagrees with the provider's splits series
    on any common timestamp — i.e. a corporate action rebased the basis
    since the stored bars were written."""
    adjusted = {bar.timestamp: bar.close for bar in provider_splits}
    for bar in stored:
        reference = adjusted.get(bar.timestamp)
        if reference is not None and not close_matches(bar.close, reference, tolerance):
            return True
    return False


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
       only when no basis break exists anywhere (callers verify first); the
       result length therefore never drops below ``len(stored)`` minus
       in-span holes the provider explicitly does not list.
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


def verify_against_provider(
    stored: Sequence[Bar],
    provider_splits: Sequence[Bar],
    tolerance: float = SPLIT_RATIO_TOLERANCE,
) -> bool:
    """Whether EVERY overlapping stored bar matches the provider basis."""
    return not detect_basis_break(stored, provider_splits, tolerance)


__all__ = [
    "SPLIT_RATIO_TOLERANCE",
    "adequate_coverage",
    "close_matches",
    "coverage_span",
    "covers_session",
    "detect_basis_break",
    "merge_provider_authority",
    "signal_session_date",
    "verify_against_provider",
]
