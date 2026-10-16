"""plugins_d1 -- plugin-side checks: `smoke` plus the invariants the two-leg kernel needs.

`research.batch_screen.smoke` proves the plugin *runs* on every panel kind; this script checks
what the two-leg account requires of the returned target panels, on the same panels and on each
arm's own declared window:

* every leg's weights finite, `>= 0`, `<= CAP` (0.98), and `wants_a + wants_b <= CAP`;
* the sleeve is held **only** while the E2 trend vote is off (`leg B > 0` implies the vote is off,
  and leg B is exactly zero while it is on);
* while the vote is **on**, leg A equals production's own target series exactly (the arms are
  off-state replacements, not on-state re-weightings).

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_d1.check_plugins
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import api, smoke, windows
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

#: every screenable arm of IC31-IC38 (the four `needs_kernel` arms are registered, not simulated)
ARMS = [(f"IC{i}", a) for i in (31, 32, 33, 34, 35, 37, 38) for a in ("A", "B", "C")
        if not (i == 34 and a == "C")]

def production(data):
    """Production's own target series, recomputed here (independent of the plugin's helper)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)

def check(idea, arm_id):
    arm = api.load_arm(idea, arm_id)
    win = windows.arm_window(arm)
    bad = []
    for name, p in smoke.smoke_panels(arm["leg"], win).items():
        try:
            t = api.call_targets(arm["mod"], p, arm)
        except api.NotApplicable:
            continue
        a, b = np.asarray(t.wants), np.asarray(t.wants_b)
        if t.leg_b != arm["leg_b"]:
            bad.append(f"{name}: leg_b {t.leg_b!r}")
        for lab, w in (("A", a), ("B", b)):
            if not np.isfinite(w).all():
                bad.append(f"{name}: leg {lab} non-finite")
            if (w < -1e-12).any():
                bad.append(f"{name}: leg {lab} negative ({w.min():.4f})")
            if (w > CAP + 1e-12).any():
                bad.append(f"{name}: leg {lab} above cap ({w.max():.4f})")
        if (a + b > CAP + 1e-9).any():
            bad.append(f"{name}: total above cap ({(a + b).max():.4f})")
        on = fe_signals.e2_up(p.price)
        if (b[on] > 1e-12).any():
            bad.append(f"{name}: sleeve held while the vote is on ({b[on].max():.4f})")
        pw = production(p)
        if np.abs(a[on] - pw[on]).max() > 1e-12:
            bad.append(f"{name}: leg A differs from production while on")
        print(f"  {f'{idea}/{arm_id}':8s} {name:9s} A [{a.min():.4f},{a.max():.4f}] "
              f"B [{b.min():.4f},{b.max():.4f}] B>0 {float(np.mean(b > 0)):6.1%} "
              f"sum_max {(a + b).max():.4f}")
    if bad:
        print(f"  {idea}/{arm_id}: BAD " + "; ".join(bad))
    return not bad

def main() -> int:
    bad = 0
    for idea, arm_id in ARMS:
        bad += not check(idea, arm_id)
    print(f"check_plugins {'OK' if not bad else 'FAIL'} ({len(ARMS)} arms, {bad} bad)")
    return 1 if bad else 0

if __name__ == "__main__":
    raise SystemExit(main())
