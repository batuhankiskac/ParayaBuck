"""IC49 -- drawdown-budget governor (NAV feedback), batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  sigma_budget = 0.35 x clip((0.35 - |NAV_dd|)/(0.35 - 0.20), 0.25, 1.0);
       vt = min(1, 0.35/vol20, sigma_budget/vol20)
    B  the same with the decline starting at a 15% NAV drawdown and a 0.15 floor
    C  the same as A applied only to upsizes (downsizes keep K's band rule)

Kernel mechanism: `research/fast_engine_opts`'s governor (`dd_start`, `dd_floor`,
`dd_upsize_only`), requested through a registered rule. The governor lives inside the session loop
because it reads the *realised NAV drawdown* -- state the plugin API cannot see -- and it re-sizes
the target the plugin returned: exactly the bank's `sigma_budget` law, with the drawn-down-run
floor at `dd_floor x vol_target` and the 0.98 cap applied to the result exactly as
`fe_signals.sized_wants` does.

Readings fixed here:

* **Arms A and B return the *un-capped* target** `on_eff x factor x min(1, 0.35/vol20)`: the
  kernel's governor caps it (the documented convention of that option, and what the kernel_opts
  fixture does).  The governor is *bit-identical* to production while the NAV drawdown is under
  `dd_start`, so the dead zone is exact.
* **Arm C returns the capped production target** (`sized_wants`).  Its `dd_upsize_only = 1` makes
  the kernel keep the plugin's target on the sessions where the governor would *reduce* it, and
  the kernel's cap then only runs in the branch the governor takes -- an un-capped input would let
  arm C hold 1.00 of NAV where production holds 0.98.  C therefore hands in production's target
  and the two arms differ only in sessions where the governor's ratio is computed from a capped
  input (requires both a >= 20 % NAV drawdown and vol20 <= 0.35, i.e. the governor biting *and* the
  vol target saturated).
* **The sizing vol** is `data.vol` (the held 2x leg's own `vol20`), as production sizes.
* **Applicability**: no extra data; the NAV path exists in every window (the OOS windows, the 21
  ETFs and both bootstrap families are applicable).
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import rules
from research.fast_engine import fe_signals

rules.register_rule("ic49_gov20", band=True, usd_rule=True, defer=True,
                    opts={"dd_start": 0.20, "dd_floor": 0.25})
rules.register_rule("ic49_gov15", band=True, usd_rule=True, defer=True,
                    opts={"dd_start": 0.15, "dd_floor": 0.15})
rules.register_rule("ic49_gov20_up", band=True, usd_rule=True, defer=True,
                    opts={"dd_start": 0.20, "dd_floor": 0.25, "dd_upsize_only": 1})

ARMS = {
    "A": {"rule": "ic49_gov20", "leg": "2x", "cash_yield": "none", "uncapped": True},
    "B": {"rule": "ic49_gov15", "leg": "2x", "cash_yield": "none", "uncapped": True},
    "C": {"rule": "ic49_gov20_up", "leg": "2x", "cash_yield": "none", "uncapped": False},
}

def targets(data, spec):
    """Production's state machine, sized as the governor's convention asks (see the docstring)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    if not spec["uncapped"]:
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    vt = fe_signals.vt_of(data.vol)
    return np.atleast_2d(np.asarray(on_eff, dtype=float)) * np.atleast_2d(factor) * vt
