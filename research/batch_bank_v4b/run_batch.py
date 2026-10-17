"""research/batch_bank_v4b/run_batch.py -- screen ONLY the new `_N` arms as batch `bank_v4b`.

`research.batch_screen.run.run_batch` screens **every** arm of every listed idea module
(`run.plan` iterates `mod.ARMS`), so it cannot express "the new arms only": over the five modules it
would also re-simulate and re-register the pinned old arms. This thin driver restricts the
registered/simulated set to the new `_N` arms and otherwise calls the framework's own
`plan`-equivalent, control, worker, scoreboard and deflation code unchanged -- **nothing in
`research/batch_screen/` is edited** (the module's `plan` global is rebound in this process only;
the worker processes call `run._arm_task` exactly as the CLI does).

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.batch_bank_v4b.run_batch --workers 6
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.batch_bank_v4b.run_batch --check
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

import research.batch_screen.run as R
from research.batch_screen import api, registry
from research.batch_screen.rules import CONTROL_ARM

BATCH = "bank_v4b"
#: the idea modules this batch reads, and the *new* arms it screens out of each (the old arms of
#: these modules are pinned in `REGISTRY.jsonl` and must not be re-run)
SELECTED = {
    "IC34": ("C_N",),
    "IC36_N": ("A_N", "B_N", "C_N"),
    "IC41": ("A_N", "B_N", "C_N"),
    "IC45": ("C_N",),
    "IC46": ("C_N",),
}
IDEAS = tuple(SELECTED)


def plan_selected(ideas, batch, cash_yield="none", allow_dirty=False):
    """`run.plan`, restricted to `SELECTED[idea]` -- the framework's own registration semantics."""
    out, skipped = [], []
    for idea_id in ideas:
        mod = api.load_idea(idea_id)
        path = api.idea_path(idea_id)
        if allow_dirty:
            git = {"head": registry.head_commit(), "idea_commit": registry.idea_commit(path),
                   "commit_gate": "skipped"}
        else:
            git = registry.require_committed(idea_id, path)
            git["commit_gate"] = "pass"
        c_hash = api.code_hash(idea_id)
        for arm_id in SELECTED.get(idea_id, ()):
            spec = dict(mod.ARMS[arm_id])
            key = api.arm_key(idea_id, arm_id)
            try:
                arm = api.resolve_arm(mod, arm_id)
            except api.NeedsKernel as exc:
                registry.append(registry.entry_for(idea_id, arm_id, spec, api.spec_hash(spec),
                                                   c_hash, batch, "needs_kernel", git, str(exc)))
                skipped.append((key, "needs_kernel", str(exc)))
                continue
            if arm["cash_yield"] != cash_yield:
                raise SystemExit(f"{key}: cash_yield {arm['cash_yield']!r} != the batch's "
                                 f"{cash_yield!r}")
            arm.update({"id": key, "idea": idea_id, "arm_id": arm_id, "mod": mod})
            seen = registry.is_rerun(idea_id, arm_id, api.spec_hash(spec), c_hash)
            registry.append(registry.entry_for(idea_id, arm_id, spec, api.spec_hash(spec), c_hash,
                                               batch, "rerun" if seen else "screened", git))
            out.append(arm)
    return out, skipped


def check() -> int:
    """The two properties this batch's plugins assert about themselves (no metric, no ledger)."""
    from research.batch_screen import stages, windows
    from research.fast_engine import fe_signals
    from research.fast_engine3 import bridge3

    from research.ideas import IC36_N, IC45, IC46

    out = {}
    # (1) the NaN-aware rolling vol is `fe_signals.vol_n` bit for bit on a gap-free series, and it
    #     recovers after a leading gap (where `vol_n`'s cumsum never does)
    rng = np.random.default_rng(20261017)
    a = 100.0 * np.cumprod(1 + rng.normal(0, 0.01, (4, 600)), axis=1)
    ref = fe_signals.vol_n(a)
    out["roll_vol_bit_identical"] = all(
        np.array_equal(m._roll_vol(a, 20), ref, equal_nan=True)
        for m in (IC36_N, IC45, IC46))
    gap = np.full((1, 120), np.nan)
    gap[0, 40:] = 100.0 * np.cumprod(1 + rng.normal(0, 0.01, 80))
    out["roll_vol_survives_a_leading_gap"] = bool(
        np.isfinite(IC45._roll_vol(gap, 20)[0, 60:]).all()
        and not np.isfinite(fe_signals.vol_n(gap)).any())
    # (2) every *screenable* selected arm resolves and routes as the batch assumes
    routed, refused = {}, {}
    for idea_id, arms in SELECTED.items():
        for arm_id in arms:
            try:
                arm = api.load_arm(idea_id, arm_id)
            except api.NeedsKernel as exc:
                refused[f"{idea_id}/{arm_id}"] = str(exc)[:80]
                continue
            routed[f"{idea_id}/{arm_id}"] = {
                "legs": list(arm["legs"]), "n_legs": arm["n_legs"],
                "margin_spread": arm["margin_spread"],
                "needs_kernel3": bridge3.needs_kernel3(arm),
                "window": windows.arm_window(arm).tag}
    out["routed"] = routed
    out["refused"] = refused
    # (3) the two loan arms really borrow and the money is charged
    loans = {}
    for idea_id, arm_id in (("IC45", "C_N"), ("IC46", "C_N")):
        arm = api.load_arm(idea_id, arm_id)
        win = windows.arm_window(arm)
        days, p, _i0, i1 = windows.main_panel(0.0, leg=stages.panel_leg(arm))
        env = windows.try_env(days, _i0, i1, "none")
        r = stages.run_one(arm, p, env, win.i0, i1, "progressive", 2, 100_000.0)
        loans[f"{idea_id}/{arm_id}"] = {
            "margin_usd": float(r["opts_stats"]["margin_usd"]),
            "legs_traded": {k: int(v["trades"]) for k, v in r["_legs"].items()}}
    out["loans"] = loans
    out["pass"] = bool(out["roll_vol_bit_identical"] and out["roll_vol_survives_a_leading_gap"]
                       and all(v["needs_kernel3"] for v in routed.values())
                       and all(v["margin_usd"] > 0 and all(t > 0 for t in v["legs_traded"].values())
                               for v in loans.values()))
    print(json.dumps(out, indent=1, sort_keys=True))
    print(f"check {'PASS' if out['pass'] else 'FAIL'}", flush=True)
    return 0 if out["pass"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.batch_bank_v4b.run_batch")
    ap.add_argument("--batch", default=BATCH)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--check", action="store_true", help="the plugins' own property checks only")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    R.plan = plan_selected
    t0 = time.time()
    print("batch "
          f"{args.batch}: screening ONLY the new arms "
          f"{[f'{i}/{a}' for i in IDEAS for a in SELECTED[i]]} "
          f"(the pinned old arms are not re-run)", flush=True)
    res = R.run_batch(args.batch, list(IDEAS), cash_yield="none", workers=args.workers,
                      stage2=True, allow_dirty=False, quiet=False)
    print(f"total wall {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
