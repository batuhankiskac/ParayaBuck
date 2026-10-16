"""IC29 -- emerging-market relative strength (EEM vs SPY). Idea bank `idea_bank_v2` (frozen):

    A  3-of-4 vote {SMA200, TSM252, DON126, r_EEM(126) - r_SPY(126) > 0}, on iff >= 3
    B  weight *= 0.75 while r_EEM(126) - r_SPY(126) < -5 pp
    C  early-path entry additionally requires r_EEM(126) - r_SPY(126) > 0

Series: Yahoo chart API `EEM` (2003-04-14 ->) and `SPY`, **adjusted closes** (the bank's
"total-return closes"). SPY is downloaded together with EEM (same source, same adjustment, same
fetch date) rather than taken from `stress_robustness_v1_daily.csv`, so both legs of the spread
share one convention; both are registered (`spy`, `eem`). The spread is the difference of the two
126-session returns on their common NYSE sessions (`research/plugins_c4/signals.py:
rel_strength`), same-day, carried forward to every calendar day (key `eem_spy_rs126`). Why
precomputed rather than taken over the panel: see `plugins_c4/signals.py`.

Literal reading: arm A replaces E2's vote (production's early re-entry on top); arm B scales
production K's whole target weight; arm C gates only the early path's entry (production's exit and
vote override unchanged). Before the spread exists (EEM + 126 sessions = 2003-10-13) every arm is
neutral -- the bank's "neutral before, a fixed convention" (`plugins_c4/common.py`).

**Applicability (pre-registered by the bank).** EEM starts 2003, so only `M3`/`M4`/`M5` are
evaluable (reduced 3-window G7); `GSPC_1957_1999`, `M1`, `M2` end before EEM exists and are
refused. No engine change.
"""

from __future__ import annotations

import numpy as np

from research.plugins_c4 import common

NOT_EVALUABLE = ("GSPC_1957_1999", "M1", "M2")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote", "thr": 0.0},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "scale", "thr": -0.05,
          "mult": 0.75},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "early_gate", "thr": 0.0},
}


def targets(data, spec):
    common.refuse(data, NOT_EVALUABLE, "EEM starts 2003-04-14, after this window ends")
    rs = common.state(data, "eem_spy_rs126")
    ok = np.isfinite(rs)
    v = np.where(ok, rs, 0.0)
    thr = float(spec["thr"])
    mode = spec["mode"]
    if mode == "vote":
        return common.vote4(data, v > thr, ok)
    if mode == "scale":
        wants = common.production(data)[-1]
        return wants * np.where(ok & (v < thr), float(spec["mult"]), 1.0)
    if mode == "early_gate":
        return common.early_gate(data, ~ok | (v > thr))
    raise ValueError(f"unknown mode {mode!r}")  # pragma: no cover
