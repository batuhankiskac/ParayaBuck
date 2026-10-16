"""IC14 -- initial-claims shock veto. Idea bank `idea_bank_v2` (frozen arm text):

    A  weight *= 0.5 while ICSA_4w_mean / min(ICSA_4w_mean over 26 weeks) - 1 > 0.10
    B  weight = 0 while the same ratio > 0.20
    C  3-of-4 vote {SMA200, TSM252, DON126, the ICSA ratio <= 0.10}, on iff >= 3

Series: FRED `ICSA` (weekly initial claims, seasonally adjusted, dated by week-ending Saturday,
1967-01-07 ->). The ratio is computed on the weekly native observations
(`research/plugins_c4/signals.py: icsa_shock` -- the 4-week mean, its 26-week trailing minimum
including the current week) and is dated **week-end + 7 days** (the bank's "1-week lag": the
Thursday release is usable from the following Saturday, so the first session that sees it is the
next Monday), then carried forward to every calendar day (registered key `icsa_shock`). Why the
statistic is precomputed rather than taken over the panel: see `plugins_c4/signals.py` (a
26-week window inside a 20-session bootstrap block would mix claim levels of different eras).

Literal reading: "weight" is production K's whole target weight (E2 and the early path); arm B
sets it to zero in the state; arm C replaces E2's 2-of-3 vote with the 3-of-4 vote and keeps
production's early re-entry path on top.

**Applicability (pre-registered by the bank).** `M2` (1934-1956) predates ICSA and is refused;
`GSPC_1957_1999` and `M1` are partially covered (claims from 1967-07 on) and follow the neutral
convention before (`plugins_c4/common.py`), as do the main window, the ETFs and the bootstraps.
The non-US windows `M3`/`M4`/`M5` use the US claims series as a global-cycle state (the bank
lists them as evaluable). No engine change.
"""

from __future__ import annotations

import numpy as np

from research.plugins_c4 import common

NOT_EVALUABLE = ("M2",)

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "scale", "thr": 0.10,
          "mult": 0.5},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "scale", "thr": 0.20,
          "mult": 0.0},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote", "thr": 0.10},
}


def targets(data, spec):
    common.refuse(data, NOT_EVALUABLE, "ICSA starts 1967-01-07, after this window ends")
    ratio = common.state(data, "icsa_shock")
    ok = np.isfinite(ratio)
    thr = float(spec["thr"])
    if spec["mode"] == "scale":
        wants = common.production(data)[-1]
        shock = ok & (np.where(ok, ratio, 0.0) > thr)
        return wants * np.where(shock, float(spec["mult"]), 1.0)
    if spec["mode"] == "vote":
        return common.vote4(data, np.where(ok, ratio, np.inf) <= thr, ok)
    raise ValueError(f"unknown mode {spec['mode']!r}")  # pragma: no cover
