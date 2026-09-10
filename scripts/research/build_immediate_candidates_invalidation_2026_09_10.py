"""Build the immutable invalidation record for the immediate A01-A06 run.

This is a read-only adjudication package.  It hashes the frozen manifest and
the already-written diagnostic outputs, but never reruns the economic worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO / "data/research_runs/immediate_candidates_invalidation_2026_09_10"
REPORT = REPO / "docs/research/immediate_candidates_invalidation_2026_09_10.md"
MANIFEST = "data/research_manifests/immediate_candidate_freeze_2026_09_09.json"
RESULTS = "data/research_runs/immediate_candidates_2026_09_09/results.json"
RUN_RECEIPT = "data/research_runs/immediate_candidates_2026_09_09/receipt.json"
RUN_REPORT = "docs/research/immediate_candidates_results_2026_09_09.md"
RUN_LEDGER = "data/research_runs/immediate_candidates_2026_09_09/ledger.jsonl"
RUNNER = "scripts/research/immediate_candidates_economic_2026_09_09.py"
RUN_VERIFIER = "scripts/research/verify_immediate_candidates_economic_2026_09_09.py"
EXPECTED_MANIFEST_SHA = "8cec049f1d61d929f001b53af43a7e46ede1619243a1605e30d1dc312dd9965b"
EXPECTED_CANONICAL_SHA = "c6230ec5c8c3ef37a761c30d7425275e523528155ab73232e6d5a351ca2c27c1"
PENDING = "PENDING_SELF_HASH"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: dict[str, Any], field: str) -> str:
    payload = dict(value)
    payload[field] = PENDING
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def pin(repo: Path, relative: str) -> dict[str, Any]:
    path = repo / relative
    if not path.is_file():
        raise ValueError(f"input_missing:{relative}")
    stat = path.stat()
    return {"path": relative, "sha256": sha256(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _review_evidence() -> list[dict[str, Any]]:
    # The two independent NEEDS_FIX findings were supplied to this task.  No
    # local review artifact was present to hash, so the finding text itself is
    # retained as an explicit, separately identifiable evidence statement.
    return [
        {
            "review_id": "independent_review_1",
            "status": "NEEDS_FIX",
            "local_artifact": None,
            "finding": "The current A01-A06 package requires a contract/evidence fix before its metrics can be used as a prespecified run.",
        },
        {
            "review_id": "independent_review_2",
            "status": "NEEDS_FIX",
            "local_artifact": None,
            "finding": "The current A01-A06 package is not admissible for reuse until the scanner contract and evidence boundary are repaired.",
        },
    ]


def build_decision(repo: Path = REPO) -> dict[str, Any]:
    manifest = repo / MANIFEST
    manifest_sha = sha256(manifest)
    if manifest_sha != EXPECTED_MANIFEST_SHA:
        raise ValueError(f"frozen_manifest_changed:{manifest_sha}")
    source_pins = {name: pin(repo, name) for name in (MANIFEST, RESULTS, RUN_RECEIPT, RUN_REPORT, RUN_LEDGER, RUNNER, RUN_VERIFIER)}
    run_receipt = json.loads((repo / RUN_RECEIPT).read_text(encoding="utf-8"))
    if run_receipt.get("manifest_physical_sha256") != EXPECTED_MANIFEST_SHA or run_receipt.get("manifest_canonical_sha256") != EXPECTED_CANONICAL_SHA:
        raise ValueError("run_receipt_manifest_pin_invalid")
    if run_receipt.get("results_sha256") != source_pins[RESULTS]["sha256"] or run_receipt.get("report_sha256") != source_pins[RUN_REPORT]["sha256"] or run_receipt.get("ledger_sha256") != source_pins[RUN_LEDGER]["sha256"]:
        raise ValueError("run_receipt_output_pin_invalid")
    decision = {
        "schema": "immediate-candidates-invalidation-decision-v1",
        "decision_date": "2026-09-10",
        "candidate_ids": [f"A{i:02d}" for i in range(1, 7)],
        "classification": "POST_OUTCOME_PROTOCOL_CHANGE_INVALID",
        "run_status": "INVALIDATED_FOR_PRESPECIFIED_EVIDENCE",
        "diagnostic_metrics_preserved": True,
        "diagnostic_metrics_reuse_prohibited": True,
        "duplicate_dividend_unresolved": True,
        "full_rerun_required": False,
        "full_rerun_reason": "The A01-A06 run is dropped under the zero-wait decision; rerunning the same invalid protocol is unnecessary.",
        "capacity_joint_nav_missing": True,
        "admission": False,
        "integration": False,
        "promotion": False,
        "production_paper_live_untouched": True,
        "network_used": False,
        "economic_work_executed_by_builder": False,
        "frozen_manifest": {"path": MANIFEST, "physical_sha256": manifest_sha, "canonical_protocol_sha256": EXPECTED_CANONICAL_SHA},
        "source_pins": source_pins,
        "review_evidence": _review_evidence(),
        "decisive_findings": [
            "The frozen manifest names a V3-compatible PIT universe but does not normatively pin the scanner implementation, exact gates, or Stage-A ordering.",
            "The current runner contains explicit scanner gates and a Stage-A top-300 shortlist that were added after the first outcome and are not an exact call through the production V3 universe seam.",
            "The runner's 20-bar continuity, all-bar price, and high-low ATR behavior differs from production scanner semantics.",
            "The two independent reviews both returned NEEDS_FIX.",
        ],
        "decision_sha256": PENDING,
    }
    decision["decision_sha256"] = canonical_hash(decision, "decision_sha256")
    return decision


def render_report(decision: dict[str, Any], paths: dict[str, str]) -> str:
    return "\n".join([
        "# Immediate A01-A06 invalidation — 2026-09-10",
        "",
        "Karar: `POST_OUTCOME_PROTOCOL_CHANGE_INVALID`. A01-A06 ekonomik çıktıları fiziksel olarak korunur; prespecified evidence olarak yeniden kullanılamaz.",
        "",
        "Frozen manifest `8cec049f…` değişmemiştir. Üretim config/scanner kurallarının freeze öncesinde mevcut olması, sonradan runner'a eklenen ve üretim seam'ini birebir uygulamayan davranışı bu çalışmaya geriye dönük bağlamaz.",
        "",
        "İki bağımsız review `NEEDS_FIX` sonucundadır. Eksikliği açık kalan duplicate-dividend çözümü bu run için çözülmemiştir; adaylar dropped edildiği için full rerun gerekli değildir.",
        "",
        "Joint NAV/capacity kanıtı eksiktir. Bu paket admission, integration ve promotion kapılarını kapatır. Mevcut metrikler yalnızca tanısal arşivdir.",
        "",
        "## Sabit karar alanları",
        "",
        "| Alan | Değer |",
        "|---|---|",
        "| Sınıflandırma | `POST_OUTCOME_PROTOCOL_CHANGE_INVALID` |",
        "| Adaylar | `A01-A06` |",
        "| Diagnostic metrikler | Korundu, yeniden kullanım yasak |",
        "| Duplicate dividend | Çözülmemiş |",
        "| Full rerun | Gerekli değil; run dropped |",
        "| Joint NAV/capacity | Eksik |",
        "| Admission / integration / promotion | `false / false / false` |",
        "| Production / PAPER / LIVE | Dokunulmadı |",
        "",
        "## Hash kanıtı",
        "",
        *[f"- `{label}`: `{value}`" for label, value in paths.items()],
        "",
        "Bu karar dosyası ile aynı run yeniden ekonomik kanıt olarak kullanılamaz. Yeni bir ekonomik değerlendirme gerekirse yeni freeze, exact production seam pinleri ve bağımsız contract review gerekir.",
        "",
        "IMMEDIATE_CANDIDATES_INVALIDATION_COMPLETE",
        "",
    ])


def write_bundle(repo: Path = REPO, output_dir: Path = OUTPUT_DIR, report_path: Path = REPORT) -> dict[str, Any]:
    decision = build_decision(repo)
    output_dir.mkdir(parents=True, exist_ok=True)
    decision_path = output_dir / "decision.json"
    manifest_path = output_dir / "manifest.json"
    receipt_path = output_dir / "receipt.json"
    decision_path.write_text(json.dumps(decision, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_paths = {"decision": str(decision_path.relative_to(repo)), "manifest": str(manifest_path.relative_to(repo)), "receipt": str(receipt_path.relative_to(repo))}
    report_path.write_text(render_report(decision, {**report_paths, "frozen_manifest_sha256": EXPECTED_MANIFEST_SHA, "results_sha256": decision["source_pins"][RESULTS]["sha256"], "runner_sha256": decision["source_pins"][RUNNER]["sha256"]}), encoding="utf-8")
    manifest = {"schema": "immediate-candidates-invalidation-manifest-v1", "decision": {"path": report_paths["decision"], "sha256": sha256(decision_path)}, "report": {"path": str(report_path.relative_to(repo)), "sha256": sha256(report_path)}, "inputs": decision["source_pins"], "classification": decision["classification"], "manifest_sha256": PENDING}
    manifest["manifest_sha256"] = canonical_hash(manifest, "manifest_sha256")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {"schema": "immediate-candidates-invalidation-receipt-v1", "decision": {"path": report_paths["decision"], "sha256": sha256(decision_path)}, "manifest": {"path": report_paths["manifest"], "sha256": sha256(manifest_path)}, "report": {"path": str(report_path.relative_to(repo)), "sha256": sha256(report_path)}, "classification": decision["classification"], "candidate_count": 6, "diagnostic_metrics_preserved": True, "diagnostic_metrics_reuse_prohibited": True, "duplicate_dividend_unresolved": True, "full_rerun_required": False, "capacity_joint_nav_missing": True, "admission": False, "integration": False, "promotion": False, "production_paper_live_untouched": True, "network_used": False, "receipt_sha256": PENDING}
    receipt["receipt_sha256"] = canonical_hash(receipt, "receipt_sha256")
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"status": "IMMEDIATE_CANDIDATES_INVALIDATION_COMPLETE", "classification": decision["classification"], "decision_sha256": sha256(decision_path), "manifest_sha256": sha256(manifest_path), "receipt_sha256": sha256(receipt_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    try:
        print(json.dumps(write_bundle(args.repo, args.output_dir, args.report), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
