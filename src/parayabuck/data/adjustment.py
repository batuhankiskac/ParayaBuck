"""Corporate-action basis authority for the strategy's SIGNAL history.

Two price domains exist and must never be conflated:

* EXECUTION domain: RAW as-traded stream bars (market state, gap repair,
  order sizing) — untouched by this module.
* SIGNAL domain: the provider's SPLIT-ADJUSTED daily history. The provider
  rebases that series retroactively on every corporate action, so it is the
  only authority on what the strategy should have seen historically.

Design (v2, replacing the fragile cumulative-factor transformation of raw
stream bars):

* Warmup seeds the runtime signal history from ``adjustment="splits"``.
* Stream-aggregated RAW bars are appended PROVISIONALLY so intraday state
  works; they equal the current provider basis except on a split day.
* Before any finalized equity 1d decision is allowed through, ONE batched
  pair of fetches (raw + splits tails for every active symbol) either
  VERIFIES the provisional history against the provider or REBUILDS it
  from the provider (authoritative), absorbing any number of splits,
  forward or reverse, that happened since the last verification.
* If the provider cannot be reached, the basis is UNVERIFIABLE: new equity
  entries fail closed while risk-reducing exits keep flowing.

All functions here are pure and deterministic.
"""

from __future__ import annotations

from datetime import datetime

from parayabuck.core.models import Bar

# A raw vs split-adjusted close difference within this relative band is
# provider rounding noise, not a corporate action.
SPLIT_RATIO_TOLERANCE = 0.005


def close_matches(left: float, right: float, tolerance: float = SPLIT_RATIO_TOLERANCE) -> bool:
    if left <= 0 or right <= 0:
        return False
    return abs(left - right) / right <= tolerance


def detect_basis_break(
    stored: tuple[Bar, ...],
    provider_splits: list[Bar] | tuple[Bar, ...],
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


def authoritative_signal_history(
    stored: tuple[Bar, ...],
    provider_splits: list[Bar] | tuple[Bar, ...],
) -> tuple[tuple[Bar, ...], bool]:
    """Rebuild the strategy signal history with the PROVIDER as authority.

    Returns ``(merged, changed)``. Every timestamp the provider covers is
    taken verbatim from the provider (retroactively rebased, so any number
    of forward/reverse splits since the warmup download is absorbed by
    construction); stored bars STRICTLY NEWER than the newest provider bar
    survive as provisional tail bars — their basis is post-action by
    definition once every covered timestamp is rebased. ``changed`` is True
    whenever the rebuilt series differs from ``stored`` in timestamps or
    overlapping closes (repairs, replacements, or stale provisional bars
    the provider no longer lists).
    """
    provider_by_ts: dict[datetime, Bar] = {bar.timestamp: bar for bar in provider_splits}
    newest_provider = max(provider_by_ts) if provider_by_ts else None
    tail = tuple(
        bar for bar in stored if newest_provider is not None and bar.timestamp > newest_provider
    )
    ordered_provider = [provider_by_ts[key] for key in sorted(provider_by_ts)]
    merged = tuple([*ordered_provider, *tail])
    changed = len(merged) != len(stored)
    if not changed:
        for fresh, old in zip(merged, stored):
            if fresh.timestamp != old.timestamp or not close_matches(fresh.close, old.close):
                changed = True
                break
    return merged, changed


def verify_against_provider(
    stored: tuple[Bar, ...],
    provider_splits: list[Bar] | tuple[Bar, ...],
    tolerance: float = SPLIT_RATIO_TOLERANCE,
) -> bool:
    """Whether EVERY overlapping stored bar matches the provider basis."""
    return not detect_basis_break(stored, provider_splits, tolerance)


__all__ = [
    "SPLIT_RATIO_TOLERANCE",
    "authoritative_signal_history",
    "close_matches",
    "detect_basis_break",
    "verify_against_provider",
]
