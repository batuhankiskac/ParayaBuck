"""IB27 -- beta-matched QQQ + QLD blend: not expressible as a batch_screen plugin (NEEDS_KERNEL).

The idea bank's own `plugin` model for IB27 is *hybrid* and every one of its three arms is marked
`needs_kernel_option: true` with `kernel_scope: "after_tax_stage_only"`. This module registers the
three arms exactly as specified and refuses to simulate them, with the reason below.

Why it cannot be a plugin: the arm holds **two** Nasdaq instruments at once -- `w_QLD = B - C` and
`w_QQQ = C - w_QLD` with `B = 2 * f * vt` and `C = min(0.98, B)` -- inside one account. The plugin
API returns the target weight of the *one* leg the account holds (`MarketData.leg`, "the account
holds one instrument, there is no portfolio layer", `batch_screen/README.md` §1), and
`research/fast_engine/fe_sim.py` models one instrument per account with one FIFO lot book. The
bank's pre-tax sketch (price the blend as a single synthetic fund) is a disclosed approximation of
the *return* only: it cannot produce the after-tax number the study would be judged on, because
that number depends on the two legs' separate FIFO books and the gain fraction each downsize
consumes (the whole point of the A1u/K band). The honest boundary the framework sets is a new
mechanism -> a two-leg kernel option in a copy of the engine plus an off/on parity cell, i.e. its
own study (`WORKER_PROTOCOL.md`, `batch_screen/README.md` §1).

The arm texts, verbatim from the bank (parameters fixed, nothing tuned):

    A  blend in the on-state only (f = 1); the early leg stays production half-weight QLD
    B  blend everywhere, including the early leg (f = 0.5), which makes the early leg unlevered QQQ
    C  blend only when vt >= 0.60; production QLD otherwise

where `B = 2 * f * vt`, `C = min(0.98, B)`, `w_QLD = B - C`, `w_QQQ = C - w_QLD`.
"""

from __future__ import annotations

from research.batch_screen.api import NeedsKernel
from research.fast_engine import fe_signals

KERNEL_REASON = (
    "the arm holds two legs at once (w_QLD = B - C, w_QQQ = C - w_QLD with B = 2*f*vt, "
    "C = min(0.98, B)); the plugin contract returns the target weight of the one leg the account "
    "holds and the engine models one instrument per account with one FIFO lot book, so the "
    "after-tax cells (the ones the study is judged on) need a two-leg kernel option in a copy of "
    "the engine plus an off/on parity cell -- its own study"
)

NEEDS_KERNEL = KERNEL_REASON

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "blend": "on_state_only",
          "f": 1.0, "needs_kernel": f"{KERNEL_REASON} (scope: the f = 1 on-state only)"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "blend": "everywhere",
          "f": 0.5, "needs_kernel": f"{KERNEL_REASON} (scope: the f = 1 and f = 0.5 legs)"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "blend": "vt_ge_0.60",
          "vt_min": 0.60, "needs_kernel": f"{KERNEL_REASON} (scope: vt >= 0.60 only)"},
}


def targets(data, spec):
    """Never reached: `resolve_arm` refuses every arm of this module (see `NEEDS_KERNEL`).

    Kept as a callable so the module still satisfies the plugin contract, and so the refusal is
    explicit rather than an `AttributeError`.
    """
    raise NeedsKernel(KERNEL_REASON)


#: the production inputs the two-leg arm would blend (documentation only, never executed)
PRODUCTION = fe_signals
