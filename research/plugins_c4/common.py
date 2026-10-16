"""plugins_c4 -- small shared pieces of the IC09/IC14/IC19/IC29/IC51 plugins (pure numpy).

Conventions every plugins_c4 extra-data module follows (the campaign's "unavailable ->
production" rule, `plugins_b3.helpers.nan_clip_ratio`):

* a session whose state series has no value (before the series starts, or before its trailing
  window is full) is *neutral*: multiplier 1.0, the extra vote falls back to production's 2-of-3
  (`plugins_b3.helpers.vote_3_of_4`), an early-entry gate permits the entry -- so the pre-signal
  part of a window is a K clone by construction, not a measured arm effect;
* a window the idea bank does not declare evaluable raises `NotApplicable` by name, before any
  extra series is read; a window whose state series has no value at all raises it too.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers


def window_key(data) -> str:
    """The window an extra key resolves for (`main` inside a main bootstrap chunk, the market's
    key inside an OOS pooled-bootstrap chunk, the window's own name otherwise)."""
    return data.extra_window or data.window


def refuse(data, not_evaluable, why):
    wk = window_key(data)
    if wk in not_evaluable:
        raise NotApplicable(f"{wk}: {why}")


def state(data, key):
    """`data.extra(key)` (already a calendar-day series, so no ffill is needed), NotApplicable if
    the panel has no value at all."""
    v = data.extra(key)
    if not np.isfinite(v).any():
        raise NotApplicable(f"the {key} series has no value in window {window_key(data)!r}")
    return v


def production(data):
    """`(e2, comps, on_eff, factor, wants)` of production K's target series."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return e2, comps, on_eff, factor, helpers.capped(on_eff, factor, data.vol)


def vote4(data, extra_vote, extra_ok):
    """`>= 3 of {SMA200, TSM252, DON126, extra}` (production 2-of-3 where `extra_ok` is False),
    with production's early re-entry path on top; returns the sized target weights."""
    price = data.price
    s = fe_signals.sma_up(price, SMA_N).astype(np.int64)
    t = fe_signals.tsm_up_price(price).astype(np.int64)
    d = fe_signals.don_up(price, DON_N).astype(np.int64)
    e2 = helpers.vote_3_of_4(s, t, d, (extra_ok & extra_vote).astype(np.int64), extra_ok)
    on_eff, factor = fe_signals.r2_state(price, e2, fe_signals.early_components(price))
    return helpers.capped(on_eff, factor, data.vol)


def early_gate(data, gate_ok):
    """Production E2; the early path's entry additionally requires `gate_ok` (True = permitted)."""
    price = data.price
    e2 = fe_signals.e2_up(price)
    comps = fe_signals.early_components(price)
    on_eff, factor = helpers.r2_state_with_entry(price, e2, comps[0] & gate_ok, comps)
    return helpers.capped(on_eff, factor, data.vol)


__all__ = ["early_gate", "production", "refuse", "state", "vote4", "window_key"]
