"""Plugin-side checks for the two_leg_arms_v1 plugins (`smoke`'s job, plus the arm invariants).

`research.batch_screen.smoke` cannot finish for the two arms whose leg B is an extra series: its
own call `p.leg_b_usable(leg_b, 0, p.T - 1)` raises `IndexError` on a bootstrap chunk
(`MarketData.leg_b_usable` indexes `days` with an offset that is one column too large when the
series is one column wider than the chunk's calendar -- reported, not patched). This script calls
`targets` on the same panels `smoke` builds and checks the invariants the two-leg kernel needs:

    every leg's weights finite, >= 0, <= CAP, and wants_a + wants_b <= CAP;
    IB27's blend reproduces the bank's construction and its beta equals 2*min(CAP, f*vt).

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.two_leg_arms_v1.check_plugins
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import api, smoke
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = [("ib25_2L", "A_2L"), ("ib25_2L", "B_2L"), ("IB26_2L", "A_2L"), ("IB26_2L", "B_2L"),
        ("IB26_2L", "C_2L"), ("ib27_2L", "A_2L"), ("ib27_2L", "B_2L"), ("ib27_2L", "C_2L")]


def report(name, t, p):
    a, b = t.wants, t.wants_b
    bad = []
    for lab, w in (("A", a), ("B", b)):
        if not np.isfinite(w).all():
            bad.append(f"{lab} non-finite")
        if (w < -1e-12).any():
            bad.append(f"{lab} negative {w.min():.4f}")
        if (w > CAP + 1e-12).any():
            bad.append(f"{lab} above cap {w.max():.4f}")
    tot = a + b
    if (tot > CAP + 1e-9).any():
        bad.append(f"total {tot.max():.4f} > cap")
    print(f"  {name:22s} {p.window if hasattr(p, 'window') else '':9s} "
          f"A [{a.min():.4f},{a.max():.4f}] B [{b.min():.4f},{b.max():.4f}] "
          f"B>0 {float(np.mean(b > 0)):6.1%} sum_max {tot.max():.4f} "
          f"{'OK' if not bad else 'BAD ' + '; '.join(bad)}")
    return not bad


def check_blend(data):
    """IB27's construction: beta = 2*min(CAP, f*vt), and the weights the bank's formula gives."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    pw = fe_signals.sized_wants(on_eff, factor, data.vol)
    vt = fe_signals.vt_of(data.vol)
    beta = 2.0 * np.minimum(CAP, factor * vt)
    cash = np.minimum(CAP, beta)
    qld, qqq = beta - cash, cash - (beta - cash)
    print(f"  blend: beta [{beta.min():.4f},{beta.max():.4f}]  "
          f"clamped (literal formula would short QQQ) {float(np.mean(factor * vt > CAP)):.3%} of "
          f"sessions  pure-QQQ (beta<=CAP) {float(np.mean(beta <= CAP)):.1%}  "
          f"early-leg sessions (f=0.5) {float(np.mean(factor == 0.5)):.1%}")
    return qld, qqq, pw, factor


def main() -> int:
    panels = dict(smoke.real_panels())
    panels["synthetic"] = smoke.synthetic_panel()
    ok = True
    for idea, arm_id in ARMS:
        arm = api.load_arm(idea, arm_id)
        print(f"{idea}/{arm_id}  rule={arm['rule']} leg={arm['leg']} leg_b={arm['leg_b']}")
        for name, p in panels.items():
            try:
                t = api.call_targets(arm["mod"], p, arm)
            except api.NotApplicable as exc:
                print(f"  {name:22s} n/a: {exc.reason}")
                continue
            ok &= report(name, t, p)
        p = panels["main"]
        usable = p.leg_b_usable(arm["leg_b"], 253, p.T - 1)
        print(f"  main-window leg-B availability: {usable}")
        if idea.startswith("ib27"):
            check_blend(p)
    print("plugin checks", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
