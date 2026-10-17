"""IC41_R -- IC41/A-C (QLD* + the S&P 500 2x leg) with the gain-aware downsize router.

Idea bank `docs/research/idea_bank_v2.json` IC41 (`category: two-leg`), the *same three specs* the
pinned `research/ideas/IC41.py` registers (`A_N`, `B_N`, `C_N`, all `NEEDS_KERNEL_ROUTER`), now
expressible:

    A_N_R  each leg carries 0.5 of the target; downsizes fill in the leg with the lower **USD**
           FIFO gain fraction (route cap 10 pp), up-resizes in the leg further below its share
    B_N_R  leg weights proportional to 1/vol20 of each index, same routing as A
    C_N_R  as A, but the early path (half weight) is held only in the Nasdaq leg

**Why a second module.** `research/ideas/IC41.py` carries a **module-level** `NEEDS_KERNEL`, which
`api.resolve_arm` applies to *every* arm of that module; it cannot stay while runnable arms live in
the same file, and re-specifying the pinned `A`/`B`/`C`/`A_N`/`B_N`/`C_N` arms would move their
recorded `spec_hash` (`verify_ledger` → `spec_drift`). The old module is therefore left
**byte-for-byte** as committed and the runnable arms live here under `_R` names -- exactly the
`IC36_N` / `IC59_K` pattern.

**What changed.** `research/fast_engine3` now holds **five** instruments in one cash pool and carries
`fe2_opts`'s `route_cap` router generalised to N legs (`route_cap_pp`, `route_score`);
`"legs": ["2x", "2x_spy"]` is two books with one FIFO queue each (`bridge3.prepare` builds the S&P
500 daily-reset 2x leg on demand from the window's own SPY column, IC40's convention). The router's
`indexed` score reproduces `fe2_opts` bit for bit on the two-leg parity cells
(`research/fast_engine3/fe3_parity.py`); the bank/IB28 wording -- "the lower **USD** FIFO gain
fraction" -- is the arms' frozen `route_score`, so these arms select it. Nothing is tuned.

Frozen readings (the bank's wording made concrete; documented here, fixed before any run):

* **"the target"** is production's own E2 + early-re-entry, vol-sized series (`sized_wants`), exactly
  the `k_control` target; the arm splits it between the two books.
* **split = "equal"** is the bank's "each leg carries 0.5 of the target": leg A gets `0.5 * target`,
  leg B `0.5 * target`.
* **split = "inverse_vol20"** is "leg weights proportional to 1/vol20 of each index", normalised to
  sum to 1 and applied to the same target: leg A uses the 2x leg's own `vol20` (`data.vol`), leg B
  the `2x_spy` leg's own `vol20` (`fe_signals.vol_n` of that leg's price path). Before a 20-session
  estimate exists the two weights fall back to 0.5/0.5 (documented; the main window is well past
  its warm-up at its first fill).
* **arm C's early leg** is the bank's "the early path (half weight) is held only in the Nasdaq leg":
  while the E2 vote is on both books hold half the on-state target; while the *early re-entry* is
  live (E2 off, the half-weight path) the whole half-weight position sits in leg A and leg B is
  zero; while fully off both are zero.
* **"up-resizes in the leg further below its share"** is realised by construction: each leg carries
  its own fixed share of the target, so on an up-resize the leg further below its share is exactly
  the one with the larger positive target difference, and the router only re-allocates *downsize*
  units (`fe2_opts`'s semantics, untouched). No extra mechanism is invented.
* **execution** is production K per leg (0.5 pp band, A1u gain band, H2 December deferral, on that
  leg's own FIFO book, next-open fills, per-leg costs), one taxpayer.
* **window**: the main window is full (`2x_spy` is built from the window's own SPY column); on a
  window with no SPY series (the OOS indices and the ETFs) the option leg degenerates to that
  window's own `2x` leg, which is IC40's documented convention and what the bank pre-registers
  ("in the OOS windows the second index is not available, so the arm runs as the local index's 2x in
  one book -- the OOS stage then measures only the routing effect").
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A_N_R": {"rule": "K", "legs": ["2x", "2x_spy"], "cash_yield": "none", "split": "equal",
              "route_cap_pp": 10.0, "route_score": "usd", "up_resize": "furthest_below_share",
              "kernel_scope": "all_stages"},
    "B_N_R": {"rule": "K", "legs": ["2x", "2x_spy"], "cash_yield": "none",
              "split": "inverse_vol20", "vol_n": 20, "route_cap_pp": 10.0, "route_score": "usd",
              "up_resize": "furthest_below_share", "kernel_scope": "all_stages"},
    "C_N_R": {"rule": "K", "legs": ["2x", "2x_spy"], "cash_yield": "none", "split": "equal",
              "early_leg": "nasdaq_only", "route_cap_pp": 10.0, "route_score": "usd",
              "up_resize": "furthest_below_share", "kernel_scope": "all_stages"},
}

#: the second book's instrument (the S&P 500 daily-reset 2x leg, `api.LEG_SPY`)
LEG_B = "2x_spy"


def production_wants(data):
    """Production's target series: E2's vote plus the half-weight early re-entry, vol-sized."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)


def _inv_of(vol):
    """`1/vol` of an **already computed** vol panel, 0 where the estimate does not exist."""
    v = np.asarray(vol, dtype=np.float64)
    ok = np.isfinite(v) & (v > 0)
    return np.where(ok, 1.0 / np.where(ok, v, 1.0), 0.0)


def _inv_vol(a, n):
    """`1/vol_n` of one **price** panel, its own series (0 where the estimate does not exist)."""
    return _inv_of(fe_signals.vol_n(a, n))


def targets(data, spec):
    """`[leg A, leg B]` target panels: production's target split across the two books."""
    w = production_wants(data)
    if spec.get("early_leg") == "nasdaq_only":
        # the early half-weight path is held entirely in the Nasdaq book (bank arm C)
        e2 = fe_signals.e2_up(data.price)
        comps = fe_signals.early_components(data.price)
        on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
        w_on = fe_signals.sized_wants(e2, np.ones_like(factor), data.vol)
        early = on_eff & (~e2)
        wa = 0.5 * w_on * e2 + w * early
        wb = 0.5 * w_on * e2
        return [wa, wb]
    if spec.get("split") == "inverse_vol20":
        n = int(spec["vol_n"])
        ia = _inv_of(data.vol)                      # leg A: the 2x leg's own vol20
        ib = _inv_vol(data.leg_series(LEG_B)[1], n)  # leg B: the 2x_spy leg's own vol20
        s = ia + ib
        ok = s > 0
        sa = np.where(ok, ia / np.where(ok, s, 1.0), 0.5)
        sb = np.where(ok, ib / np.where(ok, s, 1.0), 0.5)
        return [sa * w, sb * w]
    return [0.5 * w, 0.5 * w]


__all__ = ["ARMS", "LEG_B", "production_wants", "targets"]
