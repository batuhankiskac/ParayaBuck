"""probe: a 10-year-yield-momentum veto on the production target series.

**Engineering probe, not a claimed arm.** Its only job is to exercise the framework's availability
machinery end to end on real data: it reads an extra series (`dgs10`, FRED, 1962 ->) and refuses
the windows that do not carry it, so it is applicable on the main window and on four of the six
OOS windows (`M1`, `M3`, `M4`, `M5`: `M2` is 1935-1956 and `GSPC_1957_1999` starts before 1962)
-- exactly the reduced-holdout case the README's §2 pre-registers.

It is not in `research/ideas/`, so it can never be screened as a trial: `probes/run_probe_batch.py`
points the framework's idea directory here, so the trial ledger stays clean.
"""

from __future__ import annotations

import numpy as np

from research.gate_power_v2.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "yield_veto": {"rule": "K", "leg": "2x", "cash_yield": "none",
                   "min_coverage": 0.9, "chg_max_pp": 1.5, "lookback": 252},
}


def targets(data, spec):
    """Production target series, held only while the 10-year yield is not rising fast."""
    raw = data.extra("dgs10")
    cov = float(np.isfinite(raw).mean())
    if cov < spec["min_coverage"]:
        raise NotApplicable(f"DGS10 covers {cov:.0%} of this window "
                            f"(need {spec['min_coverage']:.0%})")
    yld = data.extra("dgs10", ffill=True)

    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)

    chg = yld - np.roll(yld, int(spec["lookback"]), axis=-1)
    calm = np.nan_to_num(chg, nan=0.0) <= spec["chg_max_pp"]
    return wants * calm
