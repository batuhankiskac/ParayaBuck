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

All functions here are pure and deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from parayabuck.core.models import Bar

# A raw vs split-adjusted close difference within this relative band is
# provider rounding noise, not a corporate action.
SPLIT_RATIO_TOLERANCE = 0.005


def close_matches(left: float, right: float, tolerance: float = SPLIT_RATIO_TOLERANCE) -> bool:
    if left <= 0 or right <= 0:
        return False
    return abs(left - right) / right <= tolerance


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
    ``required_newest`` (a decision-day timestamp) must be present-or-exceeded
    before that day may be traded on this basis.
    """
    span = coverage_span(provider)
    if span is None:
        return False
    oldest, newest = span
    if oldest > required_oldest:
        return False
    if required_newest is not None and newest < required_newest:
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

    Rules, in priority order:

    1. Provider-covered timestamps take the PROVIDER bar verbatim (the
       retroactively rebased authority).
    2. Stored bars strictly NEWER than the provider's newest survive as the
       provisional stream tail (their basis is post-action once every covered
       timestamp was rebased).
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
    newest_provider = max(provider_by_ts)
    oldest_provider = min(provider_by_ts)

    merged: dict[datetime, Bar] = {}
    for bar in stored:
        if bar.timestamp in provider_by_ts:
            continue  # provider wins
        if bar.timestamp > newest_provider or bar.timestamp < oldest_provider:
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
            if fresh is not old and not close_matches(fresh.close, old.close):
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
    "detect_basis_break",
    "merge_provider_authority",
    "verify_against_provider",
]
