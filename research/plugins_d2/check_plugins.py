"""Plugin-side checks for the plugins_d2 plugins (`smoke`'s job plus the two-leg invariants).

`research.batch_screen.smoke` calls every arm's `targets` on the synthetic panel, the arm's own main
window, the `M2` OOS window, the `XLK` instrument window and a real bootstrap chunk and reports
shapes, legs and extra coverage -- but no invariant of the *two-leg* contract. This script calls the
same `targets` on the same panels and checks

* every leg's weights finite, `>= 0`, `<= CAP`, and `wants_a + wants_b <= CAP` -- the account's own
  cap, which is exactly what the arms this branch declares `needs_kernel` for would break;
* the blend arms reproduce IB27's construction, from an independent re-derivation of it here:
  `2*w_QLD + w_QQQ == beta` and `w_QLD + w_QQQ == min(CAP, beta)` with `beta = 2*min(CAP, f*vt)`
  (exactly, to 1e-12), so the two books deliver one index exposure inside the cap;
* the off-state arms hold leg A only while the production path is on and leg B only while it is off
  (`wants_a * wants_b == 0`) where the arm's rule says so, and `wants_a + wants_b <= CAP` on the
  residual-sleeve arm (`ib25_RW/B_RW`) where both legs are held at once;
* the declared fill knobs reach the kernel with the arm's own `kernel_scope`
  (`stages.fill_knobs_of`): `after_tax_stage_only` (IC42/IC43) gives `None` on a pre-tax stage and
  the knobs on a taxed one, `all_stages` (IC44) gives the knobs on both;
* the `needs_kernel` declarations refuse the arm (`IC41/*`, `IC45/C`, `IC46/C`).

It computes no return, no NAV and no gate: only `targets`, weight identities and the framework's own
routing helper. From the worktree root:

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_d2.check_plugins
"""

from __future__ import annotations

import sys

import numpy as np

from research.batch_screen import api, smoke, stages, windows
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

#: (idea, arm) of every runnable arm of this branch
RUNNABLE = [("IC42", "A"), ("IC42", "B"), ("IC42", "C"), ("IC43", "A"), ("IC43", "B"),
            ("IC43", "C"), ("IC44", "A"), ("IC44", "B"), ("IC44", "C"), ("IC45", "A"),
            ("IC45", "B"), ("IC46", "A"), ("IC46", "B"), ("ib25_RW", "A_RW"), ("ib25_RW", "B_RW"),
            ("IB26_RW", "A_RW"), ("IB26_RW", "B_RW"), ("IB26_RW", "C_RW")]
#: (idea, arm) the branch declares `needs_kernel`, with the mechanism that is missing
NEEDS_KERNEL = [("IC41", "A"), ("IC41", "B"), ("IC41", "C"), ("IC45", "C"), ("IC46", "C")]

#: the ideas whose two books are IB27's beta-matched QQQ + QLD blend
BLEND = ("IC42", "IC43", "IC44")
#: the arms whose sleeve is held only while the production path is off (leg A and leg B never overlap)
NO_OVERLAP = (("ib25_RW", "A_RW"), ("IB26_RW", "A_RW"), ("IB26_RW", "B_RW"), ("IB26_RW", "C_RW"))

def ib27_beta(factor, vt):
    """The reference `beta = 2*min(CAP, f*vt)`: production's beta, long-only capped at 1.96."""
    return 2.0 * np.minimum(CAP, factor * vt)

def weight_checks(t, name):
    """The `(P, T)` leg invariants; returns the list of problems found."""
    bad = []
    for lab, w in (("A", t.wants), ("B", t.wants_b)):
        if not np.isfinite(w).all():
            bad.append(f"{name}: leg {lab} has non-finite weights")
        elif (w < -1e-12).any() or (w > CAP + 1e-12).any():
            bad.append(f"{name}: leg {lab} outside [0, {CAP}] ({w.min():.4f}..{w.max():.4f})")
    total = t.wants + t.wants_b
    if np.isfinite(total).all() and (total > CAP + 1e-9).any():
        bad.append(f"{name}: wants_a + wants_b = {total.max():.4f} > CAP ({CAP})")
    return bad

def blend_checks(t, panel, name):
    """IB27's identity on the arm's own target panel: two books, one beta, inside the cap."""
    e2 = fe_signals.e2_up(panel.price)
    comps = fe_signals.early_components(panel.price)
    _on, factor = fe_signals.r2_state(panel.price, e2, comps)
    beta = ib27_beta(factor, fe_signals.vt_of(panel.vol))
    a, b = t.wants, t.wants_b
    bad = []
    if np.abs(2.0 * a + b - beta).max() > 1e-12:
        bad.append(f"{name}: 2*w_QLD + w_QQQ != beta (max {np.abs(2.0 * a + b - beta).max():.2e})")
    if np.abs(a + b - np.minimum(CAP, beta)).max() > 1e-12:
        bad.append(f"{name}: w_QLD + w_QQQ != min(CAP, beta)")
    return bad

def knob_checks(arm, name):
    """The arm's `kernel_scope` reaches the kernel: pre-tax off for `after_tax_stage_only`."""
    if arm.get("fill_knobs") is None:
        return []
    pre = stages.fill_knobs_of(arm, "pre_tax")
    tax = stages.fill_knobs_of(arm, "progressive")
    if tax != arm["fill_knobs"]:
        return [f"{name}: the knobs do not reach the taxed stage"]
    expect_pre = bool(arm["fill_knobs_pretax"])
    if (pre is not None) != expect_pre:
        return [f"{name}: kernel_scope {arm['kernel_scope']} gives pre-tax knobs {pre is not None}"]
    return []

def check_arm(idea_id, arm_id):
    """Run one runnable arm on its own panels; print the summary; return the problems."""
    mod = api.load_idea(idea_id)
    arm = api.resolve_arm(mod, arm_id)
    win = windows.arm_window(arm)
    problems = knob_checks(arm, f"{idea_id}/{arm_id}")
    rows, t = [], None
    for name, panel in smoke.smoke_panels(arm["leg"], win).items():
        t = api.call_targets(mod, panel, arm)
        if not t.two_leg or t.leg_b != arm["leg_b"]:
            problems.append(f"{idea_id}/{arm_id} {name}: two_leg={t.two_leg}, "
                            f"leg_b={t.leg_b!r} != {arm['leg_b']!r}")
            continue
        problems += weight_checks(t, name)
        if idea_id in BLEND:
            problems += blend_checks(t, panel, name)
        if (idea_id, arm_id) in NO_OVERLAP and (t.wants * t.wants_b > 1e-12).any():
            problems.append(f"{idea_id}/{arm_id} {name}: the off-state sleeve overlaps leg A")
        j0 = win.i0 if name == "main" else 253
        if not panel.leg_b_usable(t.leg_b, j0, panel.T - 1)[0]:
            rows.append(f"{name} (leg B absent)")
        else:
            rows.append(name)
    print(f"  {idea_id + '/' + arm_id:20s} [{win.tag if win.reduced else 'default':12s}] "
          f"{' | '.join(rows)}")
    print(f"    A [{t.wants.min():.4f},{t.wants.max():.4f}]  B [{t.wants_b.min():.4f},"
          f"{t.wants_b.max():.4f}]  B>0 {float(np.mean(t.wants_b > 0)):6.1%}  "
          f"sum_max {float((t.wants + t.wants_b).max()):.4f}   "
          f"{'OK' if not problems else 'BAD: ' + '; '.join(problems)}")
    return problems

def main() -> int:
    problems = []
    print("runnable arms -- weights finite / in [0, CAP] / sum <= CAP, blend identity, knobs reach "
          "their stages:")
    for idea_id, arm_id in RUNNABLE:
        problems += check_arm(idea_id, arm_id)
    print("needs_kernel arms -- refused with their reason:")
    for idea_id, arm_id in NEEDS_KERNEL:
        name = f"{idea_id}/{arm_id}"
        try:
            api.load_arm(idea_id, arm_id)
        except api.NeedsKernel as exc:
            print(f"  {name:20s} NEEDS_KERNEL  {str(exc)[:88]}...")
        else:
            problems.append(f"{name}: resolved, expected NeedsKernel")
    if problems:
        print("\nFAIL:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"\nOK: {len(RUNNABLE)} runnable arms pass every invariant, "
          f"{len(NEEDS_KERNEL)} arms declared needs_kernel")
    return 0

if __name__ == "__main__":
    sys.exit(main())
