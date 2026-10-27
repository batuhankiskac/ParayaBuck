"""Trial-registry backfill (item 25): add the grid-study configurations the ledger never recorded.

Reads, never rewrites:
  research/batch_screen/REGISTRY.jsonl        the append-only ledger (read-only here)
  research/gates_v1/inputs/*_RESULTS.json     committed copies of three grid studies' RESULTS.json

Writes research/gates_v1/REGISTRY_BACKFILL.jsonl: one row per tested configuration that is not
already in the ledger, each with `origin: "backfill"`, the source tag, the source file and its
SHA-256. Configurations are counted, not numeric cells: early_quorum_v1's "1,015 cells" are
verification cells over three arms (Q1, Q2, Q3), so it contributes three trials.

Trial count follows the ledger's own definition, N = distinct (idea, arm, spec_hash). The
correlation-adjusted N_eff is computed in run_gates.py from rebuilt arm returns where they exist
(stats.n_eff_meff on the returns that can be rebuilt; untested-return trials count at full weight).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
LEDGER = ROOT / "research/batch_screen/REGISTRY.jsonl"
OUT = HERE / "REGISTRY_BACKFILL.jsonl"

# study -> (committed RESULTS copy, tag it was taken from, how to enumerate its configurations)
GRIDS = {
    "early_quorum_v1": ("inputs/early_quorum_v1_RESULTS.json", "archive/research/early-quorum-v1-2026-10"),
    "vt_level_tax_v1": ("inputs/vt_level_tax_v1_RESULTS.json", "archive/research/vt-level-tax-v1-2026-10"),
    "leverage_frontier_v1": ("inputs/leverage_frontier_v1_RESULTS.json",
                             "archive/research/leverage-frontier-v1-2026-10"),
}


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def read_ledger(path=LEDGER) -> list:
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ledger_trials(rows) -> set:
    """The ledger's own N definition: distinct (idea, arm, spec_hash)."""
    return {(r["idea"], r["arm"], r["spec_hash"]) for r in rows if all(k in r for k in ("idea", "arm", "spec_hash"))}


def grid_configs(study: str, results: dict) -> list:
    """Configuration ids a grid study actually tested, read from its RESULTS.json."""
    if study == "early_quorum_v1":
        return list(results["params"]["candidates"])
    if study == "vt_level_tax_v1":
        return [a for a in results["params"]["arms"] if a != "C0"]  # C0 is the control
    if study == "leverage_frontier_v1":
        base = set(results["stage1"]["models"]["base"]["bootstrap_m2"]["20"])
        calib = set(results["stage1"]["models"]["calibrated"]["bootstrap_m2"]["20"])
        if base != calib:
            raise ValueError("leverage grid: base and calibrated cell sets differ")
        return sorted(base) + sorted(results["stage2"].keys() - base)
    raise KeyError(study)


def build_backfill(ledger_rows=None) -> dict:
    rows = read_ledger() if ledger_rows is None else ledger_rows
    existing_pairs = {(r["idea"], r["arm"]) for r in rows if "idea" in r and "arm" in r}
    n_ledger = len(ledger_trials(rows))
    added, skipped = [], []
    per_study = {}
    for study, (rel, tag) in GRIDS.items():
        path = HERE / rel
        raw = path.read_bytes()
        results = json.loads(raw)
        cfgs = grid_configs(study, results)
        per_study[study] = len(cfgs)
        digest = hashlib.sha256(raw).hexdigest()
        for cfg in cfgs:
            if (study, cfg) in existing_pairs:
                skipped.append(f"{study}/{cfg}")
                continue
            spec = {"study": study, "arm": cfg}
            added.append({
                "idea": study,
                "arm": cfg,
                "arm_key": f"{study}/{cfg}",
                "spec": spec,
                "spec_hash": _sha(spec),
                "batch": "backfill_v1",
                "origin": "backfill",
                "status": "backfilled",
                "source_tag": tag,
                "source_file": f"research/gates_v1/{rel}",
                "source_sha256": digest,
                "metric": None,
            })
    return {
        "ledger_lines": len(rows),
        "N_ledger": n_ledger,
        "backfill_configs_by_study": per_study,
        "backfill_added": len(added),
        "backfill_skipped_already_in_ledger": skipped,
        "N_corrected_raw": n_ledger + len(added),
        "rows": added,
    }


def main() -> None:
    res = build_backfill()
    with open(OUT, "w") as fh:
        for row in res["rows"]:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    summary = {k: v for k, v in res.items() if k != "rows"}
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
