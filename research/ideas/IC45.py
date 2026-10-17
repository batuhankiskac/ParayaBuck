"""IC45 -- permanent small gold sleeve funded by a lower equity target (bank arms A/B/C).

Per `docs/research/idea_bank_v2.json` IC45 (`category: two-leg`, `second_symbol=GLD`,
`main_window: partial: GLD starts 2004-11`, arms frozen, nothing tuned):

    A  0.90 x production target in the QLD book + 0.08 in GLD, rebalanced only outside a 2 pp band
    B  0.80 x production target + 0.18 GLD, same band
    C  0.88 x production target + a gold sleeve sized 0.10 x clip(vol20(QLD)/vol20(GLD), 0.5, 2.0)

**A and B are screened** as two-leg arms (`research/fast_engine2.fe2_sim.simulate2`): leg A = the
panel's synthetic 2x QLD* held at `equity x fe_signals.sized_wants(...)` of the production path,
leg B = the registered Yahoo GLD adjusted close (the key `data.extra("gld")` / `leg_b = "gld"`
resolves to) at the arm's fixed sleeve weight. One cash pool, a FIFO lot book per leg, one taxpayer,
and K's 0.5 pp band / A1u gain band / H2 December deferral applied per leg on that leg's own book.
The equity is scaled by 0.90 / 0.80 exactly as the arm text says, so the two targets sum to at most
0.98 (0.90 x 0.98 + 0.08 = 0.962; 0.80 x 0.98 + 0.18 = 0.964): the sleeve is funded by the lower
equity target and the account never borrows.

**Reduced window (`start = "auto"`).** GLD starts 2004-11-18, so the arm declares the window the
framework derives from its own leg-B series: session 1434 (2004-11-19) -> 2026-09-29, with K
re-simulated on the same window as the paired control (`_control_w20041119.json`), the three
after-tax windows clipped up to it, the January rolling starts that begin before it dropped and both
block bootstraps drawn from the window's own sessions. The six OOS windows and the 21 ETFs are other
markets and are unchanged -- GLD has no price on them, so `leg_b_usable` refuses the window
(`NotApplicable`); the bank's own note is "GLD 2004+ -> M3/M4/M5 only; reduced 3-window G7".

**The 2 pp rebalance band (documented deviation, fixed before any run).** The arm's band is a band
on the *held* sleeve weight. The kernel's resize trigger is the engine's fixed 0.5 pp drift
(`fe_sim.C0_BAND`; the rule table's three booleans carry no width) with K's A1u gain band on top,
which already blocks a profitable downsize below 3 / 8 pp; a target-weight plugin cannot observe the
held weight, so a held-weight band is not expressible (the same limitation `ib32`'s C arm and
`ib25_2L`'s B_2L document, there on the target side). The arms are therefore screened with the
bank's fixed weights and the engine's own band -- the sleeve's realisations stay damped by K's A1u
gain band, which is the substance of the idea -- and no parameter is added to compensate.

**Arm C is `needs_kernel`.** Its frozen formula is not fundable inside the account's own 0.98 cap:
the ratio `vol20(QLD)/vol20(GLD)` sits at the clip's upper bound over most of the window, so the
sleeve is 0.20, and `0.88 x production + sleeve` exceeds 0.98 in **44.5%** of the window's sessions
(mean excess 2.6 pp of NAV). The kernel buys that with negative cash -- `fe2_sim._buy_leg`
decrements the one cash pool with no clamp and no interest -- i.e. an interest-free loan, the
cash-loan boundary `framework_v2`/§6.2 names for IC39. Screening C as written would hand it a
phantom financing advantage, so it is registered with its frozen spec and never simulated; making
it runnable needs a loan/interest mechanism in a kernel copy or a *funded* variant of the formula,
and both change the frozen parameters.

**Arm C on the N-leg kernel (`C_N`, batch_bank_v4b).** `research/fast_engine3` now holds the arm's
own leg plus a second book **and** charges interest on a negative cash balance
(`fe3_sim.sim3`'s `mrate`, ported from IC39's option), so the frozen sleeve formula is fundable
exactly as written: leg A = `0.88 x production` and leg B = the frozen gold sleeve, with the excess
over the 0.98 cap borrowed. The loan rate is IC39's frozen `margin_spread` of **1.5 pp/yr over the
window's own DTB3 column** (`MARGIN_SPREAD_PP`, `research/ideas/IC39.py`); the bank's arm text fixes
no spread of its own, so that is the convention used, and it is the only value the arm carries. The
sleeve weight is the bank's literal `0.10 x clip(vol20(QLD)/vol20(GLD), 0.5, 2.0)` computed on the
panel's own series (`data.vol` for the 2x leg the account holds; the GLD leg's own price path for
the denominator). Where the 20-session vol does not exist yet -- the reduced window starts one
session after GLD's first (2004-11-19), so the denominator's first 19 sessions have no estimate --
the *sleeve* is held in cash (weight 0) rather than inventing a vol; that is a fixed convention, not
a tuned parameter, and it affects only those 19 sessions.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import api
from research.fast_engine import fe_signals

NEEDS_KERNEL_C = (
    "the frozen sleeve formula (0.10 x clip(vol20(QLD)/vol20(GLD), 0.5, 2.0)) on top of 0.88 x "
    "production holds more than the account's 0.98 cap in 44.5% of the window's sessions (mean "
    "excess 2.6 pp of NAV); the kernel funds the excess with negative cash and no interest, i.e. a "
    "cash loan -- the IC39 boundary"
)

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "gld", "cash_yield": "none", "start": "auto",
          "equity": 0.90, "sleeve": 0.08},
    "B": {"rule": "K", "leg": "2x", "leg_b": "gld", "cash_yield": "none", "start": "auto",
          "equity": 0.80, "sleeve": 0.18},
    "C": {"rule": "K", "leg": "2x", "leg_b": "gld", "cash_yield": "none", "start": "auto",
          "needs_kernel": NEEDS_KERNEL_C, "inputs": ["gld"], "equity": 0.88, "sleeve_coef": 0.10,
          "sleeve_clip": [0.5, 2.0], "sleeve_vol_num": "QLD", "sleeve_vol_den": "GLD"},
    # --- batch_bank_v4b: the *same* IC45-C arm on the N-leg kernel, whose `margin_spread` charges
    #     interest on the negative cash balance the frozen formula creates. A new arm name (`C_N`)
    #     keeps the pinned `C` spec byte-identical.
    #     `margin_spread` (the loan rate over the window's own DTB3 column): the bank's arm text
    #     fixes no spread, so IC39's frozen broker-rate convention is used -- `MARGIN_SPREAD_PP` =
    #     0.015 (1.5 pp/yr over DTB3), the kernel_opts package's reading of "the live broker's
    #     published margin rate" (`research/ideas/IC39.py`).
    "C_N": {"rule": "K", "legs": ["2x", "gld"], "cash_yield": "none", "start": "auto",
            "inputs": ["gld"], "margin_spread": 0.015, "equity": 0.88, "sleeve_coef": 0.10,
            "sleeve_clip": [0.5, 2.0]},
}

def production_wants(data):
    """The production E2 + early-re-entry target series (`k_control`'s `targets`)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)

def targets(data, spec):
    """`equity x production` in the QLD* book and the fixed GLD sleeve in the second book."""
    if spec.get("legs"):
        return _sleeve_targets(data, spec)
    if spec.get("needs_kernel"):
        raise api.NeedsKernel(spec["needs_kernel"])
    wants = production_wants(data)
    return float(spec["equity"]) * wants, np.full_like(wants, float(spec["sleeve"])), spec["leg_b"]

def _roll_vol(a, n=20):
    """The annualised sample vol of the n returns ending at t (NaN until n finite returns exist).

    `fe_signals.vol_n` bit for bit on a gap-free series, but a *leading* gap does not poison every
    later column: the sums skip non-finite returns and the cell is NaN unless all n are finite
    (GLD first trades 2004-11-18, 19 sessions before the reduced window's first fill).
    """
    a = np.atleast_2d(np.asarray(a, dtype=np.float64))
    r = a[:, 1:] / a[:, :-1] - 1.0
    ok = np.isfinite(r)
    z = np.zeros((a.shape[0], 1))
    s1 = np.concatenate([z, np.cumsum(np.where(ok, r, 0.0), axis=1)], axis=1)
    s2 = np.concatenate([z, np.cumsum(np.where(ok, r * r, 0.0), axis=1)], axis=1)
    c = np.concatenate([z, np.cumsum(ok, axis=1)], axis=1)
    w1 = s1[:, n:] - s1[:, :-n]
    w2 = s2[:, n:] - s2[:, :-n]
    wc = c[:, n:] - c[:, :-n]
    var = np.maximum(w2 - w1 * w1 / n, 0.0) / (n - 1)
    out = np.full(a.shape, np.nan)
    out[:, n:] = np.where(wc == n, np.sqrt(var * 252.0), np.nan)
    return out

def _sleeve_targets(data, spec):
    """`C_N`: the frozen sleeve formula on the N-leg kernel, as `{leg: wants}` (the excess borrows).

    `0.88 x production` in the QLD* book plus the bank's gold sleeve
    `0.10 x clip(vol20(QLD)/vol20(GLD), 0.5, 2.0)`; the sleeve is 0 while its vol estimate does not
    exist (see the module docstring).
    """
    wants = production_wants(data)
    den = _roll_vol(data.leg_series("gld")[1])
    lo, hi = (float(x) for x in spec["sleeve_clip"])
    ratio = np.where(np.isfinite(den) & (den > 0) & np.isfinite(data.vol) & (data.vol > 0),
                     data.vol / np.where(np.isfinite(den) & (den > 0), den, 1.0), 0.0)
    sleeve = float(spec["sleeve_coef"]) * np.clip(ratio, lo, hi)
    return {"2x": float(spec["equity"]) * wants, "gld": sleeve}
