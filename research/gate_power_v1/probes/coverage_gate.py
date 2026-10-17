"""probe: the published p15 sizing with a per-window input-coverage gate.

**Engineering probe, not a claimed arm.** The target series is `vol_shape_v1`'s I2 arm at p = 1.5
(`c = 0.24593188954326456`, the study's frozen `PRE_C`), i.e. the framework's `vol_shape_p15/p15`
example -- which passes stage 1 and fails stage 2 on G8. The *only* difference is the first four
lines of `targets`: the arm reads one extra series and refuses any window that does not carry
enough of it. So it measures the availability machinery, not a strategy:

* `dgs10_gate` (FRED DGS10, 1962 ->) is applicable on the main window, on `M1`, `M3`, `M4`, `M5`
  and on the 21 ETFs, and *not* on `M2` (1935-1956) or `GSPC_1957_1999` (starts 1957): four
  applicable OOS windows, so G7 is evaluated on those four and G8 on the pooled subset
  `{M1, M3, M4, M5}` with the control re-drawn in the same draw.
* `gld_gate` (GLD, 2004-11 ->) is applicable on the main window and on the 21 ETFs but on only
  `M4` and `M5` -- fewer than three -- so the arm must come out `holdout_insufficient`: stage-1
  survivor, holdout not evaluable, **not** a pass, listed for a manual confirmation study.

Not in `research/ideas/`: `probes/run_probe_batch.py` stages it in a temporary idea directory, so
no trial is registered.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "dgs10_gate": {"rule": "K", "leg": "2x", "cash_yield": "none", "p": 1.5,
                   "c": 0.24593188954326456, "key": "dgs10", "min_coverage": 0.9},
    "gld_gate": {"rule": "K", "leg": "2x", "cash_yield": "none", "p": 1.5,
                 "c": 0.24593188954326456, "key": "gld", "min_coverage": 0.5},
}


def targets(data, spec):
    """`vol_shape_v1`'s I2 sizing, gated on the coverage of one extra series."""
    raw = data.extra(spec["key"])
    cov = float(np.isfinite(raw).mean())
    if cov < spec["min_coverage"]:
        raise NotApplicable(f"{spec['key']} covers {cov:.0%} of this window "
                            f"(need {spec['min_coverage']:.0%})")

    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    vol = data.vol
    pos = vol > 0
    vt = np.where(pos, np.minimum(1.0, spec["c"] / np.where(pos, vol, 1.0) ** spec["p"]), 1.0)
    raw_w = on_eff * factor * vt
    fcap = np.where(raw_w > CAP, CAP / np.where(raw_w > 0, raw_w, 1.0), 1.0)
    return raw_w * fcap
