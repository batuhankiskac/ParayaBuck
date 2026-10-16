"""IC09 -- cross-sectional correlation sizing. Idea bank `idea_bank_v2` (frozen arm text):

    A  weight *= clip(corr_t/median252(corr), 0.70, 1.00), corr = 20-session mean of the nine
       SPDRs' average pairwise 60-session return correlation
    B  weight *= clip(1 - 0.5*(corr_t/median252(corr) - 1), 0.70, 1.00), same corr
    C  the same multiplier as A applied only while the vote is on (early path unchanged)

Statistic (`research/plugins_c4/signals.py: sector_stats`, registered key `sec_corr_rel` =
`corr / median252(corr)`): daily simple returns of the nine registered sector SPDR adjusted closes
(XLB XLE XLF XLI XLK XLP XLU XLV XLY) on their common sessions; the Pearson correlation of each of
the 36 pairs over the last 60 returns, averaged; its 20-session trailing mean = `corr`; divided by
the trailing 252-session median of `corr` (including the current session). Same-day (the SPDRs
close with the decision), carried forward to every calendar day. Why precomputed rather than taken
over the panel: see `plugins_c4/signals.py`.

**Disclosure -- implemented literally, sign as written.** The bank's mechanism text says the
multiplier "de-risks exactly when internal correlation breaks out", but arm A's formula
`clip(corr/median, 0.70, 1.00)` is 1.0 whenever correlation is *above* its median and cuts the
weight only when correlation is *below* it -- the opposite direction. Arm B's formula has the
mechanism's sign. The arm text is the frozen specification, so A (and C, which reuses A's
multiplier) are implemented exactly as written and the inconsistency is reported, not corrected.

Literal reading: "weight" is production K's whole target weight (A, B). Arm C: "while the vote is
on" = while E2's 2-of-3 vote is on (factor 1); sessions held by the early path (factor 0.5) keep
production's weight.

**Applicability (pre-registered by the bank).** The SPDRs start 1998-12-22, so only `M3`/`M4`/
`M5` are evaluable (reduced 3-window G7); `GSPC_1957_1999`, `M1`, `M2` are refused. The ratio is
defined from 2000-04-13; earlier sessions are neutral (`plugins_c4/common.py`). No engine change.
"""

from __future__ import annotations

import numpy as np

from research.plugins_c4 import common

NOT_EVALUABLE = ("GSPC_1957_1999", "M1", "M2")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "ratio", "lo": 0.70, "hi": 1.00,
          "where": "all"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "inverse_half", "lo": 0.70,
          "hi": 1.00, "where": "all"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "ratio", "lo": 0.70, "hi": 1.00,
          "where": "vote_on"},
}


def multiplier(rel, spec):
    ok = np.isfinite(rel)
    x = np.where(ok, rel, 1.0)
    if spec["mode"] == "ratio":
        m = x
    elif spec["mode"] == "inverse_half":
        m = 1.0 - 0.5 * (x - 1.0)
    else:  # pragma: no cover
        raise ValueError(f"unknown mode {spec['mode']!r}")
    return np.where(ok, np.clip(m, float(spec["lo"]), float(spec["hi"])), 1.0)


def targets(data, spec):
    common.refuse(data, NOT_EVALUABLE, "the sector SPDRs start 1998-12-22, after this window ends")
    rel = common.state(data, "sec_corr_rel")
    e2, _comps, _on, _factor, wants = common.production(data)
    m = multiplier(rel, spec)
    if spec["where"] == "vote_on":
        m = np.where(e2, m, 1.0)
    return wants * m
