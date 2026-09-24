"""Fail closed if this design branch contains local economic outputs or production edits."""

import csv
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STARTING_BASE = "be440821e34d859f2e693c45532566eb0dafb1f8"
changed = subprocess.check_output(["git", "diff", "--name-only", STARTING_BASE], cwd=ROOT, text=True).splitlines()
untracked = subprocess.check_output(
    ["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True
).splitlines()
files = changed + untracked
assert files, "No research artifacts found"
assert all(path.startswith("research/customer_momentum_design_v1/") for path in files), files
for path in files:
    name = Path(path).name.lower()
    assert not any(term in name for term in ("forward_return", "backtest", "pnl", "supplier_outcome")), path
for name in (
    "HISTORICAL_DISCLOSURE_COVERAGE.json",
    "PUBLIC_GRAPH_STATISTICS.json",
    "STRUCTURAL_MONTHLY_SUPPLY.json",
    "V3_STRUCTURAL_OVERLAP.json",
):
    obj = json.loads((HERE / name).read_text())
    assert obj["status"] == "NOT_MEASURED", name
    assert obj["future_supplier_outcomes_calculated"] is False, name
receipt = json.loads((HERE / "NO_PEEK_RECEIPT.json").read_text())
assert receipt["local_future_supplier_economics"] == "NONE"
assert receipt["supplier_forward_returns_generated"] is False
assert receipt["economic_result_files"] == []
with (HERE / "DETERMINISTIC_AUDIT_SAMPLE.csv").open(newline="") as stream:
    sample = csv.DictReader(stream)
    assert not any("return" in name.lower() or "outcome" in name.lower() for name in sample.fieldnames or [])
    assert sum(1 for _ in sample) == 330
print("NO_PEEK_AUDIT_PASS: research-only paths; no local economic outputs declared")
