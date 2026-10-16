"""IC12rt -- the committed `IC12/A`|`IC12/B` rule on the *real-time* NFCI state (ic12_confirm_v1).

This module is **study-local** (it is not an `research/ideas/` plugin and is not registered in the
batch ledger): it exists so the confirmation study can run the committed rule on a point-in-time
signal without touching `research/ideas/IC12.py` or `research/batch_screen/`.

Nothing but the state series differs from the committed arm:

* `ARMS` re-exports `research.ideas.IC12.ARMS` (imported, not retyped) -- the parameters are
  literally the committed ones;
* the weight construction below is `IC12.targets`'s veto path, line for line, with the state coming
  from `rt_state` (a session-keyed point-in-time series) instead of `native.observations(...)`;
* `targets_live` is the same function on `IC12`'s own state, and exists only so the harness can
  assert `targets_live(data, spec) == IC12.targets(data, spec)` bit for bit -- which is what makes
  "the rule is unchanged" checkable rather than asserted.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.ideas import IC12
from research.plugins_b3 import helpers
from research.plugins_c2 import native

from . import rt_state

ARMS = dict(IC12.ARMS)

_MISSING = ("the NFCI state (156 lagged weeks from 1971-01-08) has no value in this window")


def _state(data, spec, rt: bool) -> np.ndarray:
    """`(P, T)` state at every session of the panel, from the real-time or the committed series."""
    labels = native.session_labels(data)
    if rt:
        dates, state = rt_state.load()
    else:
        dates, vals = native.observations(data, "nfci")
        state = IC12.weekly_states(dates, vals, spec)[0]
    return native.asof(dates, state, labels)


def _wants(data, spec, rt: bool) -> np.ndarray:
    state = _state(data, spec, rt)
    if not np.isfinite(state).any():
        raise NotApplicable(_MISSING)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, fe_signals.e2_up(data.price), comps)
    wants = helpers.capped(on_eff, factor, data.vol)
    stressed = np.isfinite(state) & (np.nan_to_num(state) > 0.5)
    return np.where(stressed, float(spec["mult"]) * wants, wants)


def targets(data, spec):
    """The committed veto target series on the real-time state."""
    return _wants(data, spec, True)


def targets_live(data, spec):
    """The committed veto target series on the committed (current-revision) state."""
    return _wants(data, spec, False)


__all__ = ["ARMS", "targets", "targets_live"]
