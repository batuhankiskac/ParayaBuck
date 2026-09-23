"""Rebuild attribution from immutable raw traces after resolving overlapping risk reasons."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.account_capacity_migration_8_v3.run import _summarize  # noqa: E402

OUT = Path(__file__).resolve().parent


def main() -> None:
    for cap, prefix in ((6, "CONTROL_CAP6"), (8, "TREATMENT_CAP8")):
        result_path = OUT / f"{prefix}_RESULTS_V3.json"
        if not result_path.exists():
            continue
        outcome = json.loads(result_path.read_text())
        with gzip.open(OUT / f"{prefix}_TRACE_V3.json.gz", "rt") as handle:
            trace = json.load(handle)
        replay = SimpleNamespace(events=trace["events"], nav_path=trace["nav_path"], invariants=outcome["invariants"])
        structural, denials = _summarize(replay, trace["risk_observations"], trace["admission_observations"], cap)
        outcome["structural"] = structural
        result_path.write_text(json.dumps(outcome, indent=2, sort_keys=True, allow_nan=False) + "\n")
        (OUT / f"{prefix}_SLOT_DENIALS_V3.json").write_text(json.dumps(denials, indent=2, sort_keys=True) + "\n")
        print(cap, structural["global_count_denied_target_openings"])


if __name__ == "__main__":
    main()
