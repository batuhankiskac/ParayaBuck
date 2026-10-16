"""IC51 -- cross-sectional dispersion regime. Idea bank `idea_bank_v2` (frozen arm text):

    A  weight *= clip(disp_t/median252(disp), 0.70, 1.00), disp = 20-session mean of the
       cross-sectional stdev of the nine SPDRs' daily returns
    B  weight *= clip(median252(corr_t)/corr_t, 0.70, 1.00) (the correlation complement, IC09's
       statistic)
    C  both multipliers of A and B multiplied together

Statistics (`research/plugins_c4/signals.py: sector_stats`): daily simple returns of the nine
registered sector SPDR adjusted closes on their common sessions. `disp` = 20-session trailing mean
of the cross-sectional sample stdev (ddof = 1) of the nine returns; key `sec_disp_rel` = `disp /
median252(disp)`. `corr` is IC09's statistic (20-session mean of the 36-pair average 60-session
Pearson correlation); key `sec_corr_rel` = `corr / median252(corr)`, so arm B's
`median252(corr)/corr` = `1 / sec_corr_rel`. Medians are trailing 252 sessions including the
current one; same-day; carried forward to every calendar day. Why precomputed rather than taken
over the panel: see `plugins_c4/signals.py`.

Literal reading: "weight" is production K's whole target weight (E2 and the early path); arm C's
product is not re-clipped (the product of two multipliers in [0.70, 1.00] lies in [0.49, 1.00],
as written).

**Applicability (pre-registered by the bank).** The SPDRs start 1998-12-22, so only `M3`/`M4`/
`M5` are evaluable (reduced 3-window G7); `GSPC_1957_1999`, `M1`, `M2` are refused. A session
where a multiplier's ratio is undefined (before 2000-01-20 for disp, 2000-04-13 for corr) uses 1.0
for that multiplier (`plugins_c4/common.py`). No engine change.
"""

from __future__ import annotations

import numpy as np

from research.plugins_c4 import common

NOT_EVALUABLE = ("GSPC_1957_1999", "M1", "M2")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "parts": ["disp"], "lo": 0.70,
          "hi": 1.00},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "parts": ["corr_inv"], "lo": 0.70,
          "hi": 1.00},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "parts": ["disp", "corr_inv"],
          "lo": 0.70, "hi": 1.00},
}


def part(data, name, lo, hi):
    if name == "disp":
        rel = common.state(data, "sec_disp_rel")
        ok = np.isfinite(rel) & (rel > 0)
        x = np.where(ok, rel, 1.0)
    elif name == "corr_inv":
        rel = common.state(data, "sec_corr_rel")
        ok = np.isfinite(rel) & (rel > 0)
        x = 1.0 / np.where(ok, rel, 1.0)
    else:  # pragma: no cover
        raise ValueError(f"unknown part {name!r}")
    return np.where(ok, np.clip(x, lo, hi), 1.0)


def targets(data, spec):
    common.refuse(data, NOT_EVALUABLE, "the sector SPDRs start 1998-12-22, after this window ends")
    wants = common.production(data)[-1]
    m = np.ones_like(wants)
    for name in spec["parts"]:
        m = m * part(data, name, float(spec["lo"]), float(spec["hi"]))
    return wants * m
