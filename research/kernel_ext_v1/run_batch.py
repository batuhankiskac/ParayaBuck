"""research/kernel_ext_v1/run_batch.py -- screen ONLY the newly-runnable arms as batch
`kernel_ext_v1`.

`research.batch_screen.run.run_batch` screens **every** arm of every listed idea module
(`run.plan` iterates `mod.ARMS`), so it cannot express "the new arms only": over the three modules
it would also re-simulate and re-register the pinned old arms. This thin driver restricts the
registered/simulated set to the new arms and otherwise calls the framework's own
`plan`-equivalent, control, worker, scoreboard and deflation code unchanged -- **nothing in
`research/batch_screen/` is edited by this file** (the module's `plan` global is rebound in this
process only; the worker processes call `run._arm_task` exactly as the CLI does).

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.kernel_ext_v1.run_batch --workers 6
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.kernel_ext_v1.run_batch --check
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

import research.batch_screen.run as R
from research.batch_screen import api, registry

BATCH = "kernel_ext_v1"
#: the idea modules this batch reads, and the *new* arms it screens out of each (the old arms of
#: these modules are pinned in `REGISTRY.jsonl` and must not be re-run)
SELECTED = {
    "IC36_N": ("B5_N",),
    "IC41_R": ("A_N_R", "B_N_R", "C_N_R"),
    "ib38_C": ("C",),
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
    """The properties this batch's arms assert about themselves (no metric, no ledger)."""
    from research.batch_screen import stages, windows
    from research.fast_engine3 import bridge3

    from research.ideas import IC36_N, IC41_R, ib38_C

    out = {}
    # (1) every selected arm resolves, routes to the N-leg kernel and needs the mechanism it does
    routed, refused = {}, {}
    for idea_id, arms in SELECTED.items():
        for arm_id in arms:
            key = f"{idea_id}/{arm_id}"
            try:
                arm = api.load_arm(idea_id, arm_id)
            except api.NeedsKernel as exc:
                refused[key] = str(exc)[:100]
                continue
            routed[key] = {
                "legs": list(arm["legs"]), "n_legs": arm["n_legs"],
                "route_cap_pp": arm["route_cap_pp"], "route_score": arm["route_score"],
                "margin_spread": arm["margin_spread"],
                "needs_kernel3": bridge3.needs_kernel3(arm),
                "window": windows.arm_window(arm).tag}
    out["routed"] = routed
    out["refused"] = refused
    # (2) the pinned twins are still refused exactly where the bank recorded them
    pinned = {}
    for idea_id, arm_id in (("IC36_N", "B_N"), ("IC41", "A_N"), ("IC41", "B_N"), ("IC41", "C_N"),
                            ("ib38", "C")):
        try:
            api.load_arm(idea_id, arm_id)
            pinned[f"{idea_id}/{arm_id}"] = "RESOLVED (unexpected)"
        except api.NeedsKernel as exc:
            pinned[f"{idea_id}/{arm_id}"] = str(exc)[:60]
    out["pinned_twins_still_refused"] = pinned
    # (3) B5_N really declares five instruments (the capacity extension) and the router arms a
    #     10 pp cap; the two two-books arms declare one instrument twice
    decl = {}
    for key, arm_id in (("IC36_N", "B5_N"), ("IC41_R", "A_N_R"), ("IC41_R", "B_N_R"),
                        ("IC41_R", "C_N_R"), ("ib38_C", "C")):
        a = api.load_arm(key, arm_id)
        decl[key] = {"legs": list(a["legs"]), "route_cap_pp": a["route_cap_pp"],
                     "route_score": a["route_score"]}
    out["declarations"] = decl
    # (4) each arm's *first* run reaches the kernel it should, on its own window, finite and with
    #     the router / the second book really active
    runs = {}
    for idea_id, arm_id in (("IC36_N", "B5_N"), ("IC41_R", "A_N_R"), ("IC41_R", "B_N_R"),
                            ("IC41_R", "C_N_R"), ("ib38_C", "C")):
        arm = api.load_arm(idea_id, arm_id)
        win = windows.arm_window(arm)
        days, p, _i0, i1 = windows.main_panel(0.0, leg=stages.panel_leg(arm))
        env = windows.try_env(days, win.i0, i1, "none")
        r = stages.run_one(arm, p, env, win.i0, i1, "progressive", 2, 100_000.0)
        runs[f"{idea_id}/{arm_id}"] = {
            "window": win.tag, "cagr": float(r["cagr_usd_liquidated"]),
            "legs_traded": {k: int(v["trades"]) for k, v in r["_legs"].items()},
            "turnover_by_leg": {k: float(v["turnover_total"]) for k, v in r["_legs"].items()},
        }
    out["runs"] = runs
    # every pinned twin must still be refused (never a resolution), and every selected arm must
    # route, run finite, and carry the mechanism it declares
    out["pass"] = bool(
        not refused
        and all(v["needs_kernel3"] for v in routed.values())
        and len(pinned) == 5
        and all("RESOLVED" not in v for v in pinned.values())
        and runs["IC36_N/B5_N"]["legs_traded"].get("E", 0) > 0
        and all(np.isfinite(v["cagr"]) for v in runs.values())
        and all(max(v["turnover_by_leg"].values()) > 0 for v in runs.values()))
    print(json.dumps(out, indent=1, sort_keys=True))
    print(f"check {'PASS' if out['pass'] else 'FAIL'}", flush=True)
    return 0 if out["pass"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.kernel_ext_v1.run_batch")
    ap.add_argument("--batch", default=BATCH)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--check", action="store_true", help="the arms' own property checks only")
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
