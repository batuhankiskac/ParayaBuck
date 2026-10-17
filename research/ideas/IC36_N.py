"""IC36_N -- the off-state risk-parity blend (IC36 A/B/C) on the N-leg kernel.

Idea bank `docs/research/idea_bank_v2.json` IC36 (`category: off-state`), the *same three specs* as
`research/ideas/IC36.py`, now expressible:

    A_N  while off, hold 0.98 * (1/vol20_i) / sum_j(1/vol20_j) in each of {GLD, IEF, SHY},
         recomputed on the first session of each month
    B_N  the same over {GLD, IEF, SHY, XLU}   -> still NEEDS_KERNEL (five instruments > N_LEGS = 4)
    C_N  the same as A_N but the whole blend scaled by production vt (0.98 -> vt)

**Why a second module.** `research/ideas/IC36.py`'s three arms are pinned in the ledger
(`REGISTRY.jsonl`, status `needs_kernel`) with their spec hashes, and that module carries a
**module-level** `NEEDS_KERNEL`, which `api.resolve_arm` applies to *every* arm of the module. The
module-level declaration therefore cannot stay while new arms in the same file become runnable, and
adding a per-arm `needs_kernel` to the old arms would re-hash their specs -- `verify_ledger` reads
that as `spec_drift` and refuses every batch. The old module is left **byte-for-byte** as it was
committed and the runnable versions of its arms live here under the `_N` names the ledger will
register -- exactly the pattern `research/ideas/IC59_K.py` documents for IC59 (`three_leg_kernel_v1`
§5). Nothing is approximated: the arms' frozen parameters are the bank's.

The mechanism (`research/fast_engine3`, `docs/research/three_leg_kernel_v1.md`): leg A plus the
blend are held in **one** account -- one cash pool, one units/FIFO lot book per instrument, K's
0.5 pp band / A1u gain band / H2 December deferral applied per leg on that leg's own book, one
Turkish taxpayer. `"legs": ["2x", "gld", "ief", "shy"]`, `start = "auto"`.

Readings fixed here (the bank's wording made concrete; nothing is tuned):

* **"while off"** is production's E2 trend vote being off (`fe_signals.e2_up`), the off-state recipe
  of `README.md` §1b: while the vote is on, leg A (the 2x panel leg) holds production's own target
  and the blend is zero; while it is off, leg A is zero and the blend holds the sleeve. As in
  `IC34`/`IC45`/`IC46`, "off" is the E2 vote alone, so a session where the *early re-entry* is live
  is off too and its half-weight QLD path is replaced by the blend.
* **blend weights** are the bank's literal inverse-volatility weights: `1/vol20` of each series' own
  price, normalised to sum to 1 and multiplied by the arm's total (0.98, or production's own `vt`
  for arm C), recomputed on the first session of each month and held through the month -- no
  selection, no rotation. `vol20` is the framework's `fe_signals.vol_n` vol of that series' price
  path (`data.leg_series(key)`, i.e. the very path the kernel trades).
* **total.** Arm A_N's total is the fixed 0.98; arm C_N's is production's own `vt`,
  `min(1, 0.35/vol20(QLD*))` (`fe_signals.vt_of(data.vol)`), applied daily, exactly the "0.98 -> vt"
  the bank writes.
* **warm-up.** The reduced window starts one session after the latest input's first session
  (GLD, 2004-11-18), so the blend's 20-session vol of GLD does not exist for the window's first 19
  sessions. There `1/vol20` is taken as **0** for that instrument (a cash weight) rather than
  inventing a vol -- a fixed convention, documented, affecting only that warm-up, and the panel
  stays finite before the window too (a leading gap must not poison later columns, which is why the
  module carries its own NaN-aware `_roll_vol`: bit-identical to `fe_signals.vol_n` on a gap-free
  series).
* **instrument series**: the registered extra keys `gld`/`ief`/`shy` (and `sec_xlu` for arm B_N),
  adjusted closes, the same series the bank's `data.in_repo` list names.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import api
from research.fast_engine import fe_signals

#: `B_N`'s frozen declaration: leg A plus a four-instrument blend = five instruments in one account,
#: one more than the N-leg kernel's capacity (`api.MAX_LEGS` / `fe3_sim.N_LEGS` == 4).
NEEDS_KERNEL_B = (
    "the {GLD, IEF, SHY, XLU} blend needs five instruments in one cash pool (leg A plus four "
    "sleeves); the N-leg kernel holds at most four (`N_LEGS`), so `_N` reduces the arm to three "
    "sleeves -- the other arm, not this one"
)

#: the blend's instruments per arm (bank: {GLD, IEF, SHY} and {GLD, IEF, SHY, XLU})
ARMS = {
    "A_N": {"rule": "K", "legs": ["2x", "gld", "ief", "shy"], "cash_yield": "none",
            "start": "auto", "inputs": ["gld", "ief", "shy"],
            "assets": ["gld", "ief", "shy"], "total": 0.98, "vol_n": 20, "rescale": "monthly"},
    "B_N": {"rule": "K", "legs": ["2x", "gld", "ief", "shy", "sec_xlu"], "cash_yield": "none",
            "start": "auto", "inputs": ["gld", "ief", "shy", "sec_xlu"],
            "assets": ["gld", "ief", "shy", "sec_xlu"], "total": 0.98, "vol_n": 20,
            "rescale": "monthly", "needs_kernel": NEEDS_KERNEL_B},
    "C_N": {"rule": "K", "legs": ["2x", "gld", "ief", "shy"], "cash_yield": "none",
            "start": "auto", "inputs": ["gld", "ief", "shy"],
            "assets": ["gld", "ief", "shy"], "total": "production_vt", "vol_n": 20,
            "rescale": "monthly"},
}


def production_wants(data):
    """Production's target series: E2's vote plus the half-weight early re-entry, vol-sized."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)


def _roll_vol(a, n):
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


def _inv_vol(a, n):
    """`1/vol_n` of one price panel, 0 where the estimate does not exist (see the docstring)."""
    v = _roll_vol(a, n)
    ok = np.isfinite(v) & (v > 0)
    return np.where(ok, 1.0 / np.where(ok, v, 1.0), 0.0)


def _month_first(month):
    """The column index of each column's month's first session (a monthly hold, vectorised)."""
    first = np.ones(month.shape, dtype=bool)
    first[1:] = month[1:] != month[:-1]
    return np.maximum.accumulate(np.where(first, np.arange(month.size), 0))


def _blend(data, keys, n):
    """`(K, P, T)` inverse-vol shares of `keys`, held from each month's first session."""
    inv = np.stack([_inv_vol(data.leg_series(k)[1], n) for k in keys])
    s = inv.sum(axis=0)
    share = np.where(s > 0, inv / np.where(s > 0, s, 1.0), 0.0)
    return share[:, :, _month_first(data.month)]


def targets(data, spec):
    """`{leg: wants}`: production while the vote is on, the monthly inverse-vol blend while off."""
    if spec.get("needs_kernel"):
        raise api.NeedsKernel(spec["needs_kernel"])
    wants = production_wants(data)
    off = ~fe_signals.e2_up(data.price)
    keys = list(spec["assets"])
    share = _blend(data, keys, int(spec["vol_n"]))
    total = (fe_signals.vt_of(data.vol) if spec["total"] == "production_vt"
             else np.full_like(np.asarray(data.price, dtype=np.float64), float(spec["total"])))
    out = {"2x": wants * (~off)}
    for i, k in enumerate(keys):
        out[k] = share[i] * total * off
    return out


__all__ = ["ARMS", "NEEDS_KERNEL_B", "production_wants", "targets"]
