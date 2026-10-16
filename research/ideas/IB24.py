"""IB24 -- T-bill sleeve with an entry/exit hysteresis (NEEDS_KERNEL; not implemented).

Idea bank `docs/research/idea_bank_v1.json` (main), three arms, parameters frozen by the spec:

    A  enter the BIL* sleeve after 5 consecutive off sessions, exit on re-entry
    B  the same with N = 10 consecutive off sessions
    C  enter on the first off session, exit only after 5 consecutive on sessions

**Why this cannot be a `batch_screen` plugin.**  The sleeve is a *second asset*: the equity leg's
target weight is unchanged while the sleeve is on, and what changes is the yield of the *residual
cash* -- which the framework's target-weight plugin API cannot express:

* `targets(data, spec)` returns the held leg's target weights (or `(wants, leg)`) and nothing else;
  the residual is cash, and its yield is not a weight (`fe_sim.sim_one` accrues it from
  `RunEnv.cash_rate`).
* The framework's only cash channel is the **batch-level** `cash_yield` flag ("none" | "tbill",
  `run.py` refuses an arm whose declaration differs from `--cash-yield`), and `windows.try_env` /
  `boot.BootWindow` build that schedule from `rules.cash_rates(days)` -- a constant function of the
  window, not of an arm's state.  Declaring `cash_yield: "tbill"` would therefore implement
  `tbill_sweep_v1`'s S1 (the sleeve on for *every* off session) and lose exactly the entry/exit
  hysteresis that is this idea's whole content; that would be an approximation, not the arm.
* The idea's own `plugin` note ("a cash modifier on the target series in the pre-tax stage; the
  after-tax stage needs the existing engine's cash-yield knob") is not achievable either: the
  pre-tax NAV is `units x close + cash`, so a conditional bill yield on the residual is a cash-rate
  mechanism in both stages, not a weight.

**What a kernel option would need** (a separate study, per WORKER_PROTOCOL): either
`targets` returning a per-session cash-rate panel next to the weights, or an arm-level
`RunEnv(cash_rate=...)` built from the arm's own state (the N-session off/on counters), with a
parity cell proving the option switched off reproduces `fe_sim.sim_one` bit for bit.  The three
arms are registered with `needs_kernel` (the registry counts them; the runner refuses to simulate
them) rather than approximated.

The BIL* series itself is the idea's own construction from the engine's DTB3/TB3MS schedule
(`research/tbill_sweep_v1`: ``BIL* = DTB3 - 0.1356 %/yr``), not a new download.
"""

from __future__ import annotations

from research.batch_screen.api import NeedsKernel

_KERNEL = ("the sleeve is a per-session conditional cash rate; the plugin API returns target "
           "weights only and cash_yield is a batch-level flag, so it needs a framework/engine "
           "option (a cash-rate panel from targets, or an arm-level RunEnv(cash_rate=...)) plus a "
           "parity cell -- see this module's docstring")

NEEDS_KERNEL = _KERNEL

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "needs_kernel": _KERNEL,
          "entry_off_sessions": 5, "exit_on_reentry": True},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "needs_kernel": _KERNEL,
          "entry_off_sessions": 10, "exit_on_reentry": True},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "needs_kernel": _KERNEL,
          "entry_off_sessions": 1, "exit_on_sessions": 5},
}


def targets(data, spec):
    """Never reached: the arm is refused at `resolve_arm`; kept explicit for the smoke."""
    raise NeedsKernel(_KERNEL)
