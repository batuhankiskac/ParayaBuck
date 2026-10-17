"""ib38_C -- IB38 arm C, the routed split, on the N-leg kernel (two books of one instrument).

Idea bank `docs/research/idea_bank_v1.json` IB38 (`category: tax-structure`, `strength_rank 2`),
arm C as the bank freezes it:

    C  routed split: two accounts, downsizes routed to the cheaper FIFO head, route cap 10 pp;
       up-resizes to the more-below-target account

**Why a second module.** `research/ideas/ib38.py` carries a **module-level** `NEEDS_KERNEL` (arms A
and B -- the regime/capital matrix and the income-splitting accounting layer -- are not expressible
as `targets`-only plugins), and `api.resolve_arm` applies it to every arm of the file. Arm C is the
one bank arm that a kernel can now express, so it lives here under the module's own name (the
`IC36_N`/`IC59_K` pattern); `ib38.py` is left **byte-for-byte** as committed.

**What changed.** `research/fast_engine3` now holds **five** instruments in one cash pool and carries
`fe2_opts`'s `route_cap` router generalised to N legs, and `batch_screen` accepts a `"legs"` list
that names one instrument twice -- two FIFO books of the same instrument (the kernel keeps one
units/lot book per leg). `"legs": ["2x", "2x"]` is exactly the bank's "two accounts" inside one cash
pool.

Frozen readings (documented, fixed before any run):

* **"two accounts" -> two FIFO books of the *same* instrument in one cash pool.** The bank's arm
  text is two independent accounts each running K; the N-leg kernel has one cash pool, so the
  reading taken here is the one the task freezes: two books of `2x`, each carrying **half** the
  production target, so the split's total is production's own target and the account never exceeds
  its 0.98 cap. Consequence, stated: the 0.5 pp resize band is applied to each book's own
  half-weight, so the split re-balances less often than a single full-weight book would -- the two
  books are *not* a relabelling of one book, and the bank's own parity claim ("two unrouted identical
  accounts reproduce K's NAV exactly") belongs to the two-independent-accounts construction, not to
  this one-cash-pool one. The parity proven here is the kernel's option-off identity (router off ==
  the same split without the router, bit for bit).
* **"the cheaper FIFO head"** is the bank's own IB28/IC41 convention -- the lower **USD** FIFO gain
  fraction -- and the route cap is 10 pp; `route_score = "usd"`. The indexed-TRY score the same
  kernel offers is a different, tax-correct quantity (IC42's), not this arm's, so it is not used.
* **"up-resizes to the more-below-target account"** is realised by construction: each book carries
  its own fixed half-share, so on an up-resize the more-below-target book is exactly the one with
  the larger positive target difference; the router only re-allocates *downsize* units.
* **execution** is production K per book (0.5 pp band, A1u gain band, H2 December deferral, on that
  book's own FIFO lot book, next-open fills, per-book costs), one taxpayer, one cash pool.
* **window**: full -- both books are the panel's own `2x` leg, so no reduced window and no new data.

IB38 arms A (the regime/capital switch matrix) and B (two/three independent $50k accounts summed)
stay `NEEDS_KERNEL` in `ib38.py`: A varies batch-level settings (`targets` returns weights only) and
B is several cash pools summed, neither of which the plugin contract can express.
"""

from __future__ import annotations

from research.fast_engine import fe_signals

ARMS = {
    "C": {"rule": "K", "legs": ["2x", "2x"], "cash_yield": "none", "split": "equal",
          "route_cap_pp": 10.0, "route_score": "usd", "up_resize": "furthest_below_share",
          "kernel_scope": "all_stages"},
}


def production_wants(data):
    """Production's target series: E2's vote plus the half-weight early re-entry, vol-sized."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)


def targets(data, spec):
    """`[book 1, book 2]`: production's target split equally between the two books of `2x`."""
    w = production_wants(data)
    return [0.5 * w, 0.5 * w]


__all__ = ["ARMS", "production_wants", "targets"]
