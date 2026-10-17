"""gate_power_v3.tally -- the Stage-2 reject tally recomputed from the campaign's records.

Reads `research/batch_screen/results/*/*__*.json` (the campaign's own committed records, the
untouched ledger), keeps the **latest** record per `(idea, arm)` (by date then the batch sequence),
and reports:

* per-gate failure counts over the `reject_stage2` arms;
* the K-clone arms (`|Stage-1 after-tax Δ| < 0.001 pp`) separated from the real near-misses;
* for every real near-miss its three after-tax window Δ vs the batch's own control;
* the "remove gate X alone" counterfactual (which arms fail exactly `{X}`).

`load_campaign_deltas()` returns the latest B1 Δ vector per arm for the DSR audit.

Usage: `python -m research.gate_power_v3.tally`
"""

from __future__ import annotations

import glob
import json
import os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECORDS = os.path.join(ROOT, "research", "batch_screen", "results", "*", "*__*.json")

BATCH_ORDER = ["anchors_v1", "bank_v1", "bank_v2", "bank_v3", "bank_v3b", "ib_fix_v1", "two_leg_v1",
               "opts_fix_v1", "rw_fix_v1"]
TAX_WINDOWS = ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end")
CLONE_PP = 0.001


def _order(batch):
    return BATCH_ORDER.index(batch) if batch in BATCH_ORDER else 999


def load_latest_records():
    """The latest record per `(idea, arm)`, plus the per-batch control records."""
    by_arm = defaultdict(list)
    controls = {}
    for f in glob.glob(RECORDS):
        rec = json.load(open(f))
        if rec.get("control"):
            continue
        by_arm[(rec.get("idea"), rec.get("arm_id", rec.get("arm")))].append(rec)
        batch = rec.get("batch")
        cdir = os.path.dirname(f)
        cfile = os.path.join(cdir, "_control.json")
        if batch not in controls and os.path.exists(cfile):
            controls[batch] = json.load(open(cfile))
    latest = []
    for recs in by_arm.values():
        recs.sort(key=lambda r: (r.get("date", ""), _order(r.get("batch"))))
        latest.append(recs[-1])
    return latest, controls


def load_campaign_deltas():
    """`{arm_id: np.ndarray}` of the latest finite B1 Δ vector of every campaign arm."""
    import numpy as np

    latest, _ = load_latest_records()
    out = {}
    for r in latest:
        v = r.get("b1_delta_pp") or []
        if v and np.isfinite(np.asarray(v, dtype=float)).all():
            out[r["arm"]] = np.asarray(v, dtype=float)
    return out


def after_tax_deltas(rec, ctrl):
    """The three after-tax window Δ (pp) vs the batch's control, or `None` if not present."""
    if ctrl is None or rec.get("stage2") is None:
        return None
    a = rec["stage2"]["after_tax"]
    c = ctrl["record"]["after_tax"]
    return {wn: (a[wn]["after_tax_cagr"] - c[wn]["after_tax_cagr"]) * 100 for wn in TAX_WINDOWS}


def main():
    latest, controls = load_latest_records()
    rs2 = [r for r in latest if r.get("verdict") == "reject_stage2"]
    print(f"records (latest per arm): {len(latest)}; reject_stage2: {len(rs2)}")
    counts = Counter(g for r in rs2 for g in r.get("gates", {}).get("failed", []))
    print("\nper-gate failures (of %d):" % len(rs2))
    for g, n in counts.most_common():
        print(f"  {g:28s} {n}")

    rows = []
    for r in rs2:
        delta = r.get("stage1_gates", {}).get("after_tax_2000_delta_pp")
        d3 = after_tax_deltas(r, controls.get(r.get("batch")))
        rows.append((r["idea"], r["arm"], r.get("batch"), delta, d3,
                     r.get("gates", {}).get("failed", [])))
    clones = [x for x in rows if x[3] is not None and abs(x[3]) < CLONE_PP]
    near = [x for x in rows if not (x[3] is not None and abs(x[3]) < CLONE_PP)]
    print(f"\nK clones (|Stage-1 after-tax Δ| < {CLONE_PP} pp): {len(clones)}")
    for idea, arm, batch, delta, _d3, failed in sorted(clones):
        print(f"  {idea:8s} {arm:2s} {batch:12s} d={delta:+.6f} failed={len(failed)} {failed}")
    print(f"\nreal near-misses: {len(near)}")
    for idea, arm, batch, delta, d3, failed in sorted(
            near, key=lambda x: (x[3] if x[3] is not None else 999)):
        d3s = "" if d3 is None else " AT " + " / ".join(f"{d3[w]:+.3f}" for w in TAX_WINDOWS)
        print(f"  {idea:14s} {arm:3s} {batch:12s} d={delta:+.4f}{d3s} failed={failed}")

    # counterfactual: remove one gate (only arms failing exactly that gate are rescued)
    rescue = defaultdict(list)
    for idea, arm, _b, _d, _d3, failed in rows:
        if len(failed) == 1:
            rescue[failed[0]].append(f"{idea}/{arm}")
    print("\nremove-one-gate counterfactual (arms failing exactly one gate):")
    if not rescue:
        print("  none")
    for g, arms in sorted(rescue.items()):
        print(f"  {g:28s} -> {arms}")

    out = {
        "n_records": len(latest), "n_stage2_rejects": len(rs2),
        "gate_counts": dict(counts),
        "n_clones": len(clones),
        "clones": [{"idea": a, "arm": b, "batch": c, "delta_pp": d, "failed": f}
                   for a, b, c, d, _d3, f in clones],
        "near_misses": [{"idea": a, "arm": b, "batch": c, "delta_pp": d,
                         "at_deltas_pp": _d3, "failed": f} for a, b, c, d, _d3, f in near],
        "remove_one_gate_rescues": {g: sorted(v) for g, v in rescue.items()},
    }
    path = os.path.join(os.path.dirname(__file__), "tally_result.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(f"\nwrote {path}")
    return out


if __name__ == "__main__":
    main()
