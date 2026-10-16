"""IB38 -- tax-unit and tax-regime layer: not expressible as a batch_screen plugin (NEEDS_KERNEL).

The idea bank's own `plugin` model for IB38 is *hybrid*; two of its three arms are marked
`targets_from_data: true, needs_kernel_option: false`, but neither is expressible either: their
variation is not a target-weight rule but the *account* (the tax regime, the capital, how many
taxpayers) the same rule is run in, and an accounting layer that sums several runs. This module
registers the three arms exactly as specified and refuses to simulate them, with the reason below.

    A  regime switch matrix {R2, A1u, H2, K} x {tau = 0, flat 20 %, progressive 2026}
       x {$10k, $100k, $1M} x the six windows -- report-only, the decision input itself.
       Not expressible: `targets` returns weights only. The tax regime and the starting capital
       are *batch-level* settings of the framework's after-tax stages -- `stages.run_one(...,
       mode=...)` with `capital=TAX_CAPITALS[GATE_CAPITAL]`, i.e. progressive at $100k -- with no
       per-arm or per-spec knob, and the four engine rules are the only execution options. A
       plugin that returned production weights would merely re-register the K control.
    B  two $50k accounts / three $33.3k accounts each running K, household CAGR = the per-account
       CAGR. Not expressible: it is two or three *independent runs summed*, i.e. several cash
       accounts and FIFO books; the engine models one account, and the plugin API has no way to
       report a household series.
    C  routed split: two accounts, downsizes routed to the cheaper FIFO head with a 10 pp route
       cap, up-resizes to the account further below target. `needs_kernel_option: true` in the
       bank: two lot books, one cash pool and a routing rule -- a genuinely new mechanism.

The bank's own plugin note says the same thing ("needs a two-book kernel option with a route cap,
and an off/on parity cell proving two unrouted identical accounts reproduce K's NAV exactly"); the
boundary is `WORKER_PROTOCOL.md` / `batch_screen/README.md` §1: a new mechanism is a kernel option
in a copy of the engine plus a parity cell, i.e. its own study.

Parameters are fixed exactly as the bank writes them; nothing here is tuned or approximated.
"""

from __future__ import annotations

from research.batch_screen.api import NeedsKernel

REGIMES = ("tau_0", "flat_20", "progressive_2026")
CAPITALS = ("10k", "100k", "1M")
RULES = ("R2", "A1u", "H2", "K")

REASON_A = (
    "the arm varies the tax regime and the starting capital of the same target series (a "
    "report-only matrix); `targets` returns weights only and the framework's after-tax stages fix "
    "mode = progressive and capital = $100k, so the variation has no expression in the plugin "
    "contract"
)
REASON_B = (
    "the arm sums two or three independent engine runs (two $50k or three $33.3k accounts each "
    "running K) -- several cash accounts and FIFO lot books; the engine models one account and "
    "the plugin API cannot report a household series"
)
REASON_C = (
    "the routed split needs two FIFO books in one cash pool and a routing rule (downsizes to the "
    "cheaper FIFO head, route cap 10 pp); a new mechanism needs a two-book kernel option plus an "
    "off/on parity cell, i.e. its own study"
)

NEEDS_KERNEL = f"IB38 A: {REASON_A}; B: {REASON_B}; C: {REASON_C}"

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "needs_kernel": REASON_A,
          "regimes": list(REGIMES), "capitals": list(CAPITALS), "arms": list(RULES)},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "needs_kernel": REASON_B,
          "splits": [{"accounts": 2, "capital": "50k"}, {"accounts": 3, "capital": "33.3k"}]},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "needs_kernel": REASON_C,
          "accounts": 2, "route_cap_pp": 10.0},
}


def targets(data, spec):
    """Never reached: `resolve_arm` refuses every arm of this module (see `NEEDS_KERNEL`)."""
    raise NeedsKernel(NEEDS_KERNEL)
