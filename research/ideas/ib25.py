"""ib25 -- off-state residual in short-duration Treasuries (SHY) instead of bills (bank A/B).

Arm specs (`docs/research/idea_bank_v1.json`, parameters frozen):

* **A** hold `0.98` in SHY whenever the trend vote is off (production target otherwise)
* **B** hold the residual `0.98 - w` in SHY, re-sized only outside a 10 pp band

**NEEDS_KERNEL (both arms) -- registered, never simulated.** The idea bank itself flags them
`targets_from_data: false, needs_kernel_option: true`, `kernel_scope: all_stages`: both arms hold a
*second instrument* (SHY) with its own position, cost and FIFO/tax lots at the same time as the 2x
leg, and B additionally resizes that sleeve on its own band. The framework's plugin API is a
single-instrument account (`targets` returns one weight for the leg the account holds; the API
raises `NeedsKernel` for a per-session leg selection because "the account holds one instrument, no
portfolio layer"). Expressing the sleeve needs a copy of the engine with a two-leg position book
plus a parity cell -- a separate study, not a plugin. The honest boundary is declared here instead
of being approximated by, say, an equivalent cash weight (which would silently change the financing
and the after-tax lots).

The frozen arm parameters are recorded so the trial is registered with its spec; nothing else is
claimed about them. Data note: the framework already registers the Yahoo `SHY` series
(`batch_screen_v1_shy.csv`, adjclose, 2002-07-30 ->, `data.extra("shy")`), so the follow-up kernel
study needs no new data.
"""

from __future__ import annotations

from research.batch_screen import api

NEEDS_KERNEL = (
    "a second instrument with its own position/FIFO book: holding 0.98 (or the residual 0.98 - w) "
    "in SHY while the trend vote is off is not expressible as one target-weight series for the "
    "single-instrument account -- the framework's plugin API refuses a per-session leg selection "
    "and the engine has no portfolio layer"
)

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "sleeve": "SHY", "weight": 0.98,
          "key": "shy"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "sleeve": "SHY", "residual": True,
          "total": 0.98, "resize_band": 0.10, "key": "shy"},
}


def targets(data, spec):
    """Never reached: the arm is registered `needs_kernel` and refused before simulation."""
    raise api.NeedsKernel(NEEDS_KERNEL)
