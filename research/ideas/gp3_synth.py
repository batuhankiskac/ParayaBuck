"""gp3_synth -- gate_power_v3's synthetic-power arms (the third-pass audit).

Every arm holds production K's **own target series, unchanged**; the frozen edge is the arm's
`leg`, one of the pinned `research.gate_power_v3.api.SYNTH_LEGS` instruments (a plain drift, a
regime drift, a pre-2013 drift, or mirrored i.i.d. noise). The `leg` *name* is the whole
specification of the arm's edge -- its parameters are read from `SYNTH_LEGS` in the panel
builder, never from a batch-level flag -- so they are pinned by the committed `spec_hash`.

`syn_null0` (leg `2x`) and `syn_d0` (leg `syn_all_0000`, a zero-magnitude drift) must reproduce
production K bit for bit: that is the identity cell of the audit's per-arm drift machinery.

Not a trading idea.
"""

from __future__ import annotations

from research.fast_engine import fe_signals

ARMS = {
    "syn_null0":   {"rule": "K", "leg": "2x",           "cash_yield": "none"},
    "syn_d0":      {"rule": "K", "leg": "syn_all_0000", "cash_yield": "none"},
    "syn_d025":    {"rule": "K", "leg": "syn_all_0250", "cash_yield": "none"},
    "syn_d050":    {"rule": "K", "leg": "syn_all_0500", "cash_yield": "none"},
    "syn_d100":    {"rule": "K", "leg": "syn_all_1000", "cash_yield": "none"},
    "syn_d200":    {"rule": "K", "leg": "syn_all_2000", "cash_yield": "none"},
    "syn_inv200":  {"rule": "K", "leg": "syn_inv_2000", "cash_yield": "none"},
    "syn_pre200":  {"rule": "K", "leg": "syn_pre_2000", "cash_yield": "none"},
    "syn_noise_p": {"rule": "K", "leg": "syn_np_0500",  "cash_yield": "none"},
    "syn_noise_m": {"rule": "K", "leg": "syn_nm_0500",  "cash_yield": "none"},
}


def targets(data, spec):
    """Production K's target series (`fe_signals` is the production pipeline)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
