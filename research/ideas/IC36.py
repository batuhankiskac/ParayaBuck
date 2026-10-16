"""IC36 -- off-state risk-parity blend (GLD / IEF / SHY) with a monthly rescale. NEEDS_KERNEL.

    A  while off, hold 0.98 * (1/vol20_i) / sum_j(1/vol20_j) in each of {GLD, IEF, SHY},
       recomputed on the first session of each month
    B  the same over {GLD, IEF, SHY, XLU}
    C  the same as A but the whole blend scaled by production vt (0.98 -> vt)

**Registered as `needs_kernel`, not simulated.** The blend holds three (arm B: four) instruments
*at once* inside one cash pool: the two-leg kernel holds an arm's own leg plus exactly one other
book, so the third instrument (and the monthly rescale of all of them together) is a kernel
change -- a copy of the two-leg kernel with a multi-leg book, its own FIFO lots, the per-leg band
and a parity cell -- i.e. a separate study. The bank's own `plugin.detail` for IC36 says the same
("NeedsKernel: state-dependent multi-leg holding"), and `README.md` section 1 lists "three
instruments in one account" among what is still not expressible.

The arms are registered so the trial ledger counts them (N) and so the frozen parameters are on
record -- exactly what `resolve_arm` expects of a `NEEDS_KERNEL` declaration:

* **"while off"** is production's E2 trend vote being off (`fe_signals.e2_up` off), the off-state
  recipe of `README.md` section 1b; while the vote is on, leg A (the 2x panel leg) holds
  production's own target and the blend is zero.
* **blend weights** are the bank's literal inverse-volatility weights: `1/vol20` of each series'
  own price (the `vol20` the framework's `fe_signals.vol_n` computes, annualised), normalised to
  sum to 1 and multiplied by the arm's total (0.98, or production's own `vt` for arm C), recomputed
  on the first session of each month and held through the month -- no selection, no rotation.
* **instrument series**: the registered extra keys `gld`/`ief`/`shy` (and `sec_xlu` for arm B),
  adjusted closes, the same series the bank's `data.in_repo` list names. GLD starts 2004-11-18 and
  IEF/SHY 2002-07-30, so the blend is live from 2004-11 -- a reduced window the bank itself
  pre-registers ("before that the arm holds cash while off").
"""

from __future__ import annotations

from research.batch_screen.api import NeedsKernel

#: the module-level declaration: every arm of this idea is registered and refused, never simulated
NEEDS_KERNEL = ("a three-instrument (arm B: four) inverse-volatility blend held in one account, "
                "rescaled monthly; the two-leg kernel holds at most two books")

#: the blend's instruments per arm (bank: {GLD, IEF, SHY} and {GLD, IEF, SHY, XLU})
ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "assets": ["gld", "ief", "shy"],
          "total": 0.98, "vol_n": 20, "rescale": "monthly"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "assets": ["gld", "ief", "shy",
                                                                     "sec_xlu"],
          "total": 0.98, "vol_n": 20, "rescale": "monthly"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "assets": ["gld", "ief", "shy"],
          "total": "production_vt", "vol_n": 20, "rescale": "monthly"},
}

def targets(data, spec):
    """Refused: this idea's mechanism is a kernel change, not a target-weight panel."""
    raise NeedsKernel(NEEDS_KERNEL)

__all__ = ["ARMS", "NEEDS_KERNEL", "targets"]
