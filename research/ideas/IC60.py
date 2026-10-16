"""IC60 -- December up-resize acceleration / lot ageing (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

* **A** `target = min(0.98, production target + 0.02)` in the last 10 NYSE December sessions
* **B** `target = min(0.98, production target x 1.05)` in the last 10 NYSE December sessions
* **C** `target = min(0.98, production target + 0.02)` in the last 20 NYSE December sessions

Literal reading and the frozen conventions (everything the spec leaves open):

* "the last N NYSE December sessions" is the set H2 itself uses (`fe_sim.december_tail`: the last
  N sessions labelled December of each year of the **account calendar**), built by
  `c1_lib.december_tail_mask` on `c1_lib.account_labels(data)`.  The account calendar is the one the
  engine simulates on -- and the one the Yi-UFE indexation the idea targets is computed on: the
  window's own sessions on a real window, the fixed 2015-09 -> 2026-09 tax calendar in the clean
  bootstrap (B1/B2), the market's own session list in the OOS pooled bootstrap.  It is **not** the
  drawn source sessions of a bootstrap chunk: the idea is a tax-calendar rule, and the chunk's
  after-tax account ages its lots on the account calendar.  A December that the window truncates
  is treated as the engine treats it for H2 (its last N labelled sessions).
* the target array is the decision of each session's close (filled at the next open, as every
  target is), so the raised target is the one *decided* on those sessions -- the lots it buys are
  filled on the following sessions (the last N-1 December sessions and the first January session),
  and the drop back is decided on the first January session.  The mirror H2 keys on the fill
  session; the spec keys this arm on the target, and is read that way.
* arm A/C's `+0.02` is applied to the production target **as written**, including a session whose
  production target is 0 (E2 and the early path both off): the arm then holds 2 % of the leg for
  the window.  Arm B's `x 1.05` leaves a zero target at zero.  `min(0.98, .)` is the spec's cap,
  equal to the engine's total cap.  Outside the December window the target is production's.
* the fill rule is unchanged (`rule = "K"`: A1u's gain-scaled downsize band + H2 still act on the
  arm's own targets), `leg = "2x"`, `cash_yield = "none"`.
* no extra data: every window is applicable (dates only, as the bank records).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP
from research.plugins_c1 import c1_lib

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "tail": 10, "mode": "add", "x": 0.02},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "tail": 10, "mode": "mul", "x": 1.05},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "tail": 20, "mode": "add", "x": 0.02},
}

#: the spec's cap (`min(0.98, .)`), the engine's total cap
assert CAP == 0.98


def targets(data, spec):
    """Production K's target, raised during the December tail of the account calendar."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    base = fe_signals.sized_wants(on_eff, factor, data.vol)
    mask = c1_lib.december_tail_mask(c1_lib.account_labels(data), int(spec["tail"]))[None, :]
    x = float(spec["x"])
    raised = np.minimum(CAP, base + x if spec["mode"] == "add" else base * x)
    return np.where(mask, raised, base)
