r"""IB24_2L -- the T-bill sleeve with an entry/exit hysteresis, as two instruments in one account.

Idea bank `docs/research/idea_bank_v1.json` (IB24, `off-state`), the three arms
`research/ideas/IB24.py` registers as `needs_kernel` because the sleeve is *cash* (its yield is the
account's residual-cash rate, which the plugin API cannot carry):

    A  enter the BIL* sleeve after 5 consecutive off sessions, exit on re-entry
    B  the same with N = 10 consecutive off sessions
    C  enter on the first off session, exit only after 5 consecutive on sessions

**The expression chosen: BIL\* as a second instrument leg** (`leg_b = "bilstar"`), not a
conditional cash-rate option. The arm's own mechanism is the *cost of the sleeve's round trips* --
"enter the bill sleeve only once the vote has been off for N consecutive sessions" is meant to kill
flips that pay a BIL round trip each time (`tbill_sweep_v1`'s post-mortem: S1 turns over 5.8x/yr of
bill notional and its +0.06 pp pre-tax edge goes negative in whipsaw years, failing the block-20
bootstrap). A cash-rate option would make the switch free and tax-free, i.e. it could not price the
very thing the arm varies; held as an instrument, the sleeve pays the campaign's 4 bps x m per side
and its gain is taxed as an instrument gain (YI-UFE-indexed FIFO, one Turkish taxpayer), which is
how `tbill_sweep_v1` and `r2_tbill_combo_v1` modelled BIL (the closest tested study, whose S1 this
arm attacks). The kernel is the two-leg kernel `research.fast_engine2.fe2_sim` (one cash pool, one
units/FIFO book per leg, K's band/A1u/H2 applied per leg on its own book); the BIL* price path is
the registered extra series `bilstar`.

**Weights (fixed reading, documented once).** Leg A is production K's own target series
(`R2`'s target: the E2 vote plus the half-weight early re-entry), untouched; leg B holds the
**residual cash** `max(0, 0.98 - w_A)` while the sleeve state `S` is on, and zero otherwise:

    S_A/B = the vote has been off for >= N consecutive sessions   (exit the moment it is on)
    S_C   = the vote is off, or it has been on for < 5 consecutive sessions and an off session
            preceded this on-run (so the account starts in production, never spuriously in bills)

    w_A = production target,        w_B = max(0, 0.98 - w_A) * S

This is `r2_tbill_combo_v1`'s own construction of the sleeve against R2 ("the arm's own
`active = E2 on OR early path active`; while E2 is off **and** the early path is active, the
remainder `0.98 - w` is idle and goes to BIL*; while fully off, BIL* is held at 0.98"), and it is
what the bank's plugin note describes ("cash-like and never rebalanced against the equity leg ...
a cash modifier on the target series"): the equity leg is never touched, the sleeve is exactly the
money that would otherwise earn nothing, the total never exceeds the 0.98 cap (no loan, no margin
feature), and the *only* new axis is the hysteresis state S. The alternative reading -- the sleeve
at a fixed 0.98 while `S`, with production's target scaled by `(1 - S)` and the early re-entry
suppressed while the sleeve is on -- is not screened: it would delete a production feature
(R2's early path) inside an arm whose registered content is the *timing of the sleeve*, and it is
the `needs_kernel` module's own description of the *cash* that the option was to carry.

A state change trades the sleeve to or from zero, which the kernel always fills (entries and exits
to zero are immediate under K, A1u and H2 alike), so the sleeve's round trips are the published
S1 round trips and the hysteresis is the only thing the arms change.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A_2L": {"rule": "K", "leg": "2x", "leg_b": "bilstar", "cash_yield": "none",
             "entry_off_sessions": 5, "exit_on_reentry": True},
    "B_2L": {"rule": "K", "leg": "2x", "leg_b": "bilstar", "cash_yield": "none",
             "entry_off_sessions": 10, "exit_on_reentry": True},
    "C_2L": {"rule": "K", "leg": "2x", "leg_b": "bilstar", "cash_yield": "none",
             "entry_off_sessions": 1, "exit_after_on_sessions": 5},
}


def runs_of(vote: np.ndarray) -> tuple:
    """`(off_run, on_run, last_off)`: consecutive off/on sessions ending at `t`, 1-based counters.

    `last_off[t]` is the 1-based index of the last off session at or before `t` (0 if there is
    none), i.e. the session before the current on-run's first member -- which is what tells the
    first on-run of the panel (no off session before it) apart from a later one.
    """
    v = np.asarray(vote, dtype=bool)
    P, T = v.shape
    a = np.arange(1, T + 1)
    last_on = np.maximum.accumulate(np.where(v, a, 0), axis=1)
    last_off = np.maximum.accumulate(np.where(~v, a, 0), axis=1)
    off_run = np.where(~v, a - last_on, 0)
    on_run = np.where(v, a - last_off, 0)
    return off_run.astype(np.float64), on_run.astype(np.float64), last_off


def sleeve_state(vote: np.ndarray, spec: dict) -> np.ndarray:
    """`S`, `(P, T)` float 0/1: is the bill sleeve's state on? (the arm's frozen hysteresis.)"""
    off_run, on_run, last_off = runs_of(vote)
    if "exit_after_on_sessions" in spec:
        n = float(spec["exit_after_on_sessions"])
        return ((~np.asarray(vote, dtype=bool)) | ((on_run < n) & (last_off > 0))).astype(float)
    n = float(spec["entry_off_sessions"])
    return ((~np.asarray(vote, dtype=bool)) & (off_run >= n)).astype(float)


def targets(data, spec):
    """`(w_A, w_B, "bilstar")`: production's target, and the residual cash when the sleeve is on."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    w_a = fe_signals.sized_wants(on_eff, factor, data.vol)
    s = sleeve_state(e2, spec)
    w_b = np.maximum(CAP - w_a, 0.0) * s
    return w_a, w_b, "bilstar"
