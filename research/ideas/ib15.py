"""IB15 -- variance-risk-premium proxy (implied minus realised variance) as a regime filter.

Per `docs/research/idea_bank_v1.json` IB15, with `VRP_t = (VIX_t / 100)^2 - vol20_t^2` (annualised
variances: the implied variance of the ^VIX close minus the realised variance of the *underlying
price* series the production vote runs on, `fe_signals.vol_n(data.price)`):

    A  weight *= 0.5 while VRP_t < 0
    B  weight *= clip(1 + 0.5 * z_t, 0.7, 1.1),  z = (VRP - median252(VRP)) / std252(VRP)
    C  3-of-4 vote {SMA200, TSM252, DON126, VRP > 0}, then production's early re-entry

Arms A/B scale the production target weight (`min(1, 0.35/vol20)` x path factor, rule `K`); the
production total cap 0.98 still binds afterwards (`helpers.scaled`), so arm B's up-side tilt
(1.1) can never lever the account past the cap. Arm C feeds the enlarged vote into the production
early-re-entry state machine. The VRP is
built on `data.price` (the series the vote runs on) because it compares an index-implied variance
against realised index variance; the early path's own `vol20 < median252` condition is untouched
and is still computed on the price series by `fe_signals.early_components`. Fixed parameters: the
0.5 / 0.5 / 0.7 / 1.1 constants and the 252-session window are the arm text's.

**Data availability (pre-registered).** ^VIX starts 1990-01-02, so only `GSPC_1957_1999` has a
VIX-era tail and the idea bank declares M1-M5 not evaluable ("only the 1990-1999 part of the
GSPC_1957_1999 window is evaluable and M1-M5 are not"). This plugin therefore raises
`NotApplicable` on M1-M5, and on `GSPC_1957_1999` -- the one window `vix_filter_v1` itself could
evaluate (its G8 was ^GSPC 1990-03-28 -> 1999-12-31) -- it follows that study's convention that a
missing VIX leaves the filter *inactive*: before 1990 the multiplier is 1.0 (arm A/B) and arm C
falls back to the production 2-of-3 vote, so the pre-VIX part of the window behaves exactly like
the K control instead of manufacturing a cash position out of a data gap. Inside a bootstrap chunk
the VIX value is the drawn source session's, as the framework's extra layer prescribes.

No engine change.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers

#: pre-registered: ^VIX starts 1990-01-02, so these five windows have no VIX coverage in the
#: sample the arm's signal is measured on (the idea bank's IB15 `oos.evaluable` list)
NOT_EVALUABLE = ("M1", "M2", "M3", "M4", "M5")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "halve", "halve": 0.5},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "z", "window": 252,
          "slope": 0.5, "z_lo": 0.7, "z_hi": 1.1},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote"},
}


def vrp(data):
    """`(P, T)` VRP in annualised variance units, NaN where either input is missing."""
    vix = data.extra("vix", ffill=True)
    if not np.isfinite(vix).any():
        raise NotApplicable("the ^VIX series has no value in this window")
    vol20 = fe_signals.vol_n(data.price)
    return (vix / 100.0) ** 2 - vol20**2


def targets(data, spec):
    if data.window in NOT_EVALUABLE:
        raise NotApplicable(
            f"^VIX starts 1990-01-02 and has no coverage in {data.window}; the idea bank "
            "declares M1-M5 not evaluable for this arm"
        )
    price = data.price
    v = vrp(data)
    ok = np.isfinite(v)

    e2 = fe_signals.e2_up(price)
    comps = fe_signals.early_components(price)
    mode = spec["mode"]

    if mode == "vote":
        s = fe_signals.sma_up(price, SMA_N).astype(np.int64)
        t = fe_signals.tsm_up_price(price).astype(np.int64)
        d = fe_signals.don_up(price, DON_N).astype(np.int64)
        vote = (ok & (v > 0)).astype(np.int64)
        e2 = helpers.vote_3_of_4(s, t, d, vote, ok)
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
        return helpers.capped(on_eff, factor, data.vol)

    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    wants = helpers.capped(on_eff, factor, data.vol)
    if mode == "halve":
        return helpers.scaled(wants, np.where(ok & (v < 0), spec["halve"], 1.0))
    if mode == "z":
        med = helpers.trailing_median_full(v, spec["window"])
        sd = helpers.trailing_std_full(v, spec["window"])
        z = np.where(sd > 0, (v - med) / np.where(sd > 0, sd, 1.0), np.nan)
        good = np.isfinite(z)
        mult = np.where(
            good,
            np.clip(1.0 + spec["slope"] * np.where(good, z, 0.0), spec["z_lo"], spec["z_hi"]),
            1.0,
        )
        return helpers.scaled(wants, mult)
    raise ValueError(f"unknown mode {mode!r}")  # pragma: no cover
