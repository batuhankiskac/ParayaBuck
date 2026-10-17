"""Recompute the gate_power_v1 Stage-2 reject tallies from the committed records.

Reads research/batch_screen/results/*/*__*.json, keeps the latest record per (idea, arm)
(by date then batch name), filters verdict == "reject_stage2", and prints:
  - per-gate failure counts (arms failing each gate)
  - each Stage-2 reject with its Stage-1 after-tax delta (pp) and failed gates
  - split into K-clones (|delta| < 0.001 pp) vs real near-misses

Usage: python -m research.gate_power_v2.tally   (from worktree root)
"""
from __future__ import annotations

import glob
import json
import os
from collections import Counter, defaultdict

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECORDS_GLOB = os.path.join(WT, "research", "batch_screen", "results", "*", "*__*.json")

BATCH_ORDER = [
    "anchors_v1", "bank_v1", "bank_v2", "bank_v3", "bank_v3b",
    "ib_fix_v1", "two_leg_v1", "opts_fix_v1", "rw_fix_v1",
]


def load_latest_records():
    byarm = defaultdict(list)
    for f in glob.glob(RECORDS_GLOB):
        d = json.load(open(f))
        byarm[(d.get("idea"), d.get("arm_id", d.get("arm")))].append(d)
    latest = []
    for key, recs in byarm.items():
        # sort by date then batch order
        def keyfn(r):
            bo = BATCH_ORDER.index(r.get("batch")) if r.get("batch") in BATCH_ORDER else 999
            return (r.get("date", ""), bo)
        recs_sorted = sorted(recs, key=keyfn)
        latest.append(recs_sorted[-1])
    return latest


def main():
    recs = load_latest_records()
    rs2 = [r for r in recs if r.get("verdict") == "reject_stage2"]
    print(f"Stage-2 rejects (latest record per arm): {len(rs2)}")
    print()

    # per-gate counts
    c = Counter()
    for r in rs2:
        for g in r.get("gates", {}).get("failed", []):
            c[g] += 1
    print("Per-gate failure counts (arms failing, of %d):" % len(rs2))
    for g, n in c.most_common():
        print(f"  {g:28s} {n}")
    print()

    rows = []
    for r in rs2:
        s1g = r.get("stage1_gates", {})
        delta = s1g.get("after_tax_2000_delta_pp")
        failed = r.get("gates", {}).get("failed", [])
        rows.append((r.get("idea"), r.get("arm_id"), r.get("batch"), delta, failed))

    clones = [x for x in rows if x[3] is not None and abs(x[3]) < 0.001]
    near = [x for x in rows if not (x[3] is not None and abs(x[3]) < 0.001)]

    print(f"K clones (|Stage-1 after-tax delta| < 0.001 pp): {len(clones)}")
    for idea, arm, batch, delta, failed in sorted(clones):
        print(f"  {idea:8s} {arm:2s} {batch:12s} d={delta:+.6f} failed={failed}")
    print()
    print(f"Real near-misses: {len(near)}")
    for idea, arm, batch, delta, failed in sorted(near, key=lambda x: (x[3] if x[3] is not None else 999)):
        print(f"  {idea:8s} {arm:2s} {batch:12s} d={delta:+.4f} failed={failed}")
    print()

    # write json summary
    out = {
        "n_stage2_rejects": len(rs2),
        "gate_counts": dict(c),
        "clones": [{"idea": a, "arm": b, "batch": c_, "delta_pp": d, "failed": f}
                   for (a, b, c_, d, f) in clones],
        "near_misses": [{"idea": a, "arm": b, "batch": c_, "delta_pp": d, "failed": f}
                        for (a, b, c_, d, f) in near],
    }
    outpath = os.path.join(os.path.dirname(__file__), "tally_result.json")
    with open(outpath, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"Wrote {outpath}")


if __name__ == "__main__":
    main()
