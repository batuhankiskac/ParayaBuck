"""Read-only verifier for the immediate candidate economic package."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN = REPO / "data/research_runs/immediate_candidates_2026_09_09"
MANIFEST = REPO / "data/research_manifests/immediate_candidate_freeze_2026_09_09.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> dict[str, object]:
    receipt = json.loads((RUN / "receipt.json").read_text(encoding="utf-8"))
    results = json.loads((RUN / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checks = {
        "status": receipt.get("status") == "COMPLETE_RESEARCH_ONLY" and results.get("status") == "COMPLETE_RESEARCH_ONLY",
        "manifest_physical_pin": receipt.get("manifest_physical_sha256") == sha(MANIFEST) == "8cec049f1d61d929f001b53af43a7e46ede1619243a1605e30d1dc312dd9965b",
        "manifest_canonical_pin": receipt.get("manifest_canonical_sha256") == manifest.get("protocol_sha256") == "c6230ec5c8c3ef37a761c30d7425275e523528155ab73232e6d5a351ca2c27c1",
        "economic_only": results.get("economic_run") is True and results.get("promotion") is False and results.get("integration") is False,
        "input_rows": results.get("bar_row_count_verified") == 11686832 and results.get("post_cutoff_rows") == 0,
        "matrix": results.get("counts", {}).get("candidates") == 6 and results.get("counts", {}).get("cases") == 54,
        "exact_v3_join": results.get("v3_exact_date_join_only") is True and results.get("v3_forward_fill_days") == 0,
        "result_hash": receipt.get("results_sha256") == sha(RUN / "results.json"),
        "ledger_hash": receipt.get("ledger_sha256") == sha(RUN / "ledger.jsonl"),
        "report_hash": receipt.get("report_sha256") == sha(REPO / receipt["report"]),
    }
    ledger_rows = []
    with (RUN / "ledger.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            ledger_rows.append(json.loads(line))
    targets = {
        (row.get("candidate"), int(row.get("lag", -1)), float(row.get("cost", -1)), row.get("fill_day")): row
        for row in ledger_rows
        if row.get("kind") == "target"
    }
    ledger_ok = True
    for row in ledger_rows:
        if row.get("kind") not in {"fill", "terminal", "target"}:
            ledger_ok = False
            continue
        if row.get("kind") in {"fill", "terminal"}:
            ledger_ok = ledger_ok and all(row.get(key) is not None for key in ("decision_day", "signal_day", "fill_day", "symbol", "notional", "reference_price", "slippage", "spread", "fee"))
            ledger_ok = ledger_ok and row["signal_day"] <= row["decision_day"] <= row["fill_day"]
            ledger_ok = ledger_ok and all(math.isfinite(float(row[key])) for key in ("notional", "reference_price", "slippage", "spread", "fee", "qty"))
            if row.get("kind") == "terminal":
                ledger_ok = ledger_ok and row.get("terminal_policy") == "T1_LAST_CLOSE" and row.get("side") == "sell"
            else:
                target = targets.get((row.get("candidate"), int(row.get("lag", -1)), float(row.get("cost", -1)), row.get("fill_day")))
                ledger_ok = ledger_ok and target is not None and row.get("signal_day") == target.get("signal_day") and row.get("decision_day") == target.get("decision_day")
        else:
            ledger_ok = ledger_ok and row.get("target_count") == len(row.get("symbols", [])) <= 8
            ledger_ok = ledger_ok and row.get("signal_day") < row.get("fill_day") and row.get("decision_day") < row.get("fill_day")
    checks["ledger_integrity"] = ledger_ok and len(ledger_rows) == results.get("counts", {}).get("ledger_lines")
    for case in results.get("cases", []):
        for window in case.get("windows", {}).values():
            for key, value in window.items():
                if isinstance(value, (int, float)):
                    checks["finite_metrics"] = checks.get("finite_metrics", True) and math.isfinite(float(value))
    if not all(checks.values()):
        raise RuntimeError(json.dumps(checks, sort_keys=True))
    return {"status": "VERIFY_PASS", "checks": checks, "results_sha256": sha(RUN / "results.json"), "ledger_sha256": sha(RUN / "ledger.jsonl")}


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
