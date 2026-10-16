"""IB26 -- curve-conditioned off-state leg: intermediate Treasuries only when the curve pays.

**NEEDS_KERNEL** -- none of the three arms can be expressed as a plugin.

    A  while off: hold IEF at 0.98 if T10Y3M > 1.0 pp, else cash
    B  as A plus a requirement that the 60-session DGS10 change > -0.50 pp
    C  the mirror: hold IEF while off only if T10Y3M <= 1.0 pp

The mechanism is a *second instrument* (IEF) held in the on-state's place, chosen per session from
the curve -- the idea bank's own classification is `needs_kernel_option: true`,
`kernel_scope: all_stages`, and that is exactly the boundary the framework draws: the account holds
one instrument, `leg` is constant per arm (the two legs the panel offers are the *same* underlying
at 2x and 1x), and a per-session leg selection raises `NeedsKernel` (`api.call_targets`).  An
off-state IEF position also needs its own FIFO book for the Turkish gain accounting and its own
income/dividend convention, i.e. a second instrument inside `fe_tax`/`fe_sim` -- a kernel copy in
`research/<study>/` plus a parity cell, not a target array.

The specs are frozen below (and registered) so the trials are counted in the multiple-testing
ledger even though they are never simulated.  Inputs, had the kernel existed: the registered `ief`
(Yahoo, 2002-07+), `dgs10` and a T10Y3M series (`credit_vote_v1_T10Y3M.csv` on its own branch;
not added here, since the arm cannot run).  The bank's own oracle for the main window is a reduced
start (2003-07, IEF + its 252-session warm-up) and a pre-registered reduced OOS construction.
"""

from __future__ import annotations

from research.batch_screen.api import NeedsKernel

NEEDS_KERNEL = (
    "the off-state leg is a second instrument (IEF) with its own FIFO/gain book, selected per "
    "session from the yield curve; batch_screen's account holds one instrument and `leg` is "
    "constant per arm (the bank's kernel_scope: all_stages)"
)

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "off_leg": "IEF", "ief_weight": 0.98,
          "slope_min": 0.010, "slope_series": "T10Y3M"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "off_leg": "IEF", "ief_weight": 0.98,
          "slope_min": 0.010, "dgs10_change_min": -0.005, "dgs10_lookback": 60},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "off_leg": "IEF", "ief_weight": 0.98,
          "slope_min": 0.010, "mirror": True},
}


def targets(data, spec):
    raise NeedsKernel(NEEDS_KERNEL)


__all__ = ["ARMS", "NEEDS_KERNEL", "targets"]
