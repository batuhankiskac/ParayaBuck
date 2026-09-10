"""Build the immutable zero-wait research closure bundle.

This builder only hashes and reads existing research artifacts.  It never
reruns an economic worker and it never changes production, PAPER, or LIVE
configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO / "data/research_runs/zero_wait_final_decision_2026_09_10"
REPORT = REPO / "docs/research/zero_wait_final_decision_2026_09_10.md"

EXPECTED = {
    "data/research_manifests/zero_wait_overlay_freeze_2026_09_10.json": "8376b8dd9f8e17235395ad66e952802ab61e75dba0e3a0f5f5ff9072e8a70b4e",
    "data/research_runs/zero_wait_overlays_2026_09_10/results.json": "f2665c9d48b5993d1d219a79637559a0241345c6ffbaf13baa826d6c0c91d70f",
    "data/research_runs/zero_wait_overlays_2026_09_10/receipt.json": "26540d87b9723f969d70f5f730e5370e63d6d5e4010480beaa949674db4940c3",
    "data/research_runs/zero_wait_overlays_2026_09_10/daily_nav.csv": "d9e88ddac95cb891a2c38d3edede7b8886697d4f22565dad82debee672204c18",
    "data/research_runs/zero_wait_overlays_2026_09_10/exposure_ledger.jsonl": "55a41145ed0796ac96de87e431e8f5ae9f3e20dc58324e9ade667455ec5e7de8",
    "docs/research/zero_wait_overlay_results_2026_09_10.md": "e75691cc168d9884e75e89e1968dc57becf632e40a56bce619be70bf4a7c980c",
    "scripts/research/zero_wait_overlay_economic_2026_09_10.py": "0e330b567097ac27cb5370896752a372fe9eb107564184e462a926a80f001197",
    "scripts/research/verify_zero_wait_overlay_economic_2026_09_10.py": "93eb42aeddd33b1f9240f74de9082db3d586b96c7063da3eacca57db4261a0d1",
    "data/research/equity_v3_current_parity_control_2026_08/control_r3.json": "dd79160c6ba063572cc680f299568a0936dc53786e35f05ef98db5a7cde18634",
    "data/research_runs/all_34_research_closure_2026_09_09/closure_record.json": "71576a9f0b239940b7ebd6a5d5371b949b30518f468c3f880eb7e3403f95b96a",
    "data/research_runs/all_34_research_closure_2026_09_09/closure_summary.md": "d74c5f0d915ae329215a226a2a661efc331b2cc2a26aebb502e469376cdd2bb5",
    "data/research_runs/strategy_queue_zero_wait_2026_09_09/decision.json": "1f2d57a412d0bfc19a2d5b1a4e06945221b8ae3bea6c2a3152d2f9f742bc5b65",
    "data/research_runs/strategy_queue_zero_wait_2026_09_09/manifest.json": "140b4210ab3346b842168f0fd18b56722b2b14fdfe7d1ad0b6cfa63cfd18e6b3",
    "data/research_runs/strategy_queue_zero_wait_2026_09_09/receipt.json": "0a2a1cab8860b57334c3d7400d298a65e62a97db05148ff2d1a79fb48298e708",
    "docs/research/strategy_queue_zero_wait_decision_2026_09_09.md": "0db464f8eca8fa1bf0f123477775965fecd2abce1945a087b42df76c5d5dcb7f",
    "data/research_runs/immediate_candidates_invalidation_2026_09_10/decision.json": "1e215f325a05293683c5181bfd609954c631b6f09613c8487e062b2c01ca404e",
    "data/research_runs/immediate_candidates_invalidation_2026_09_10/manifest.json": "786a1f071d9dd2caeb67008eff3770cf116085335a735266c14f49ab73c83c81",
    "data/research_runs/immediate_candidates_invalidation_2026_09_10/receipt.json": "329cc38714d0a4ea04b609c442bb614bd4bcf8bcee0b9d7c14fad95326065918",
    "docs/research/immediate_candidates_invalidation_2026_09_10.md": "95bb98f6675eed6f3637f437996700765b28a744b9c5852191d323cbb6c40495",
}

MANIFEST_CANONICAL = "cbdb2262c03d5e9880455ea765869f25948951e98e2fa753400091bd488e9691"
ZWO_CONTROL_EMBEDDED = "d4860412bf7650732fab228a7fac7110d0cf56e48bce436160d134040d83840e"
HEAD = "63cb510198a5c4e1f213ad4505ff901f35d4bbae"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pin(repo: Path, relative: str) -> dict[str, Any]:
    path = repo / relative
    if not path.is_file():
        raise ValueError(f"input_missing:{relative}")
    stat = path.stat()
    actual = sha256(path)
    expected = EXPECTED.get(relative)
    if expected is not None and actual != expected:
        raise ValueError(f"input_changed:{relative}:{actual}")
    return {"path": relative, "sha256": actual, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def self_hash(payload: dict[str, Any], field: str) -> str:
    copy = json.loads(json.dumps(payload, ensure_ascii=False))
    copy[field] = ""
    return hashlib.sha256(json.dumps(copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def build_decision(repo: Path = REPO) -> dict[str, Any]:
    inputs = {path: pin(repo, path) for path in EXPECTED}
    zwo_results = json.loads((repo / "data/research_runs/zero_wait_overlays_2026_09_10/results.json").read_text(encoding="utf-8"))
    if zwo_results.get("status") != "ECONOMIC_EXECUTION_COMPLETE" or zwo_results.get("no_promotion") is not True:
        raise ValueError("zwo_result_status_invalid")
    if len(zwo_results.get("cases", [])) != 45 or len(zwo_results.get("primary_true_holdout_2x", {})) != 5:
        raise ValueError("zwo_case_count_invalid")
    if zwo_results.get("holm_20", {}).get("family_size") != 20:
        raise ValueError("zwo_holm_family_invalid")
    zwo_receipt = json.loads((repo / "data/research_runs/zero_wait_overlays_2026_09_10/receipt.json").read_text(encoding="utf-8"))
    if zwo_receipt.get("case_count") != 45 or zwo_receipt.get("candidate_count") != 5 or zwo_receipt.get("promotion") != "DISABLED":
        raise ValueError("zwo_receipt_invalid")
    decision = {
        "schema": "zero-wait-final-decision-v1",
        "decision_date": "2026-09-10",
        "status": "CLOSED_ZERO_WAIT_CONSTRAINT",
        "user_constraint": {
            "zero_wait": True,
            "paid_or_licensed_data": False,
            "months_long_collection": False,
            "closure_basis": "operational closure under the stated no-wait/no-paid constraint",
        },
        "original_34": {
            "candidate_count": 34,
            "immediate_count": 0,
            "counts": {"CLOSE_ZERO_WAIT": 2, "CLOSE_NOT_IMMEDIATE": 7, "CLOSE_UNAUTHORIZED_PAID_DATA": 25, "IMMEDIATE_RETEST": 0},
            "source": "data/research_runs/strategy_queue_zero_wait_2026_09_09/decision.json",
        },
        "a01_a06": {
            "candidate_ids": ["A01", "A02", "A03", "A04", "A05", "A06"],
            "candidate_count": 6,
            "classification": "POST_OUTCOME_PROTOCOL_CHANGE_INVALID",
            "admitted": 0,
            "integration": 0,
            "dropped": 6,
            "full_rerun_required": False,
            "duplicate_dividend_unresolved": True,
            "capacity_joint_nav_missing": True,
            "review_statuses": ["NEEDS_FIX", "NEEDS_FIX"],
            "diagnostic_metrics_preserved": True,
            "diagnostic_metrics_reuse_prohibited": True,
            "source": "data/research_runs/immediate_candidates_invalidation_2026_09_10/decision.json",
        },
        "zero_wait_overlay": {
            "candidate_ids": ["ZWO_SMA126_BINARY", "ZWO_SMA200_BINARY", "ZWO_DRAWDOWN10_BINARY", "ZWO_VOL15_TARGET20", "ZWO_SMA200_OR_DRAWDOWN10"],
            "candidate_count": 5,
            "classification": "CLOSE_ZERO_WAIT_TESTED_FAIL_AND_INVALID_EVIDENCE",
            "admitted": 0,
            "integration": 0,
            "dropped": 5,
            "case_count": 45,
            "primary_gate_fail_count": 5,
            "primary_cell": "single_use_true_holdout x 2x",
            "all_primary_gates": "FAIL",
            "diagnostic_metrics_preserved": True,
            "diagnostic_metrics_reuse_prohibited": True,
            "no_promotion": True,
            "review_evidence": [
                {"review_id": "zero_wait_overlay_second_review", "status": "NEEDS_FIX", "finding": "CVaR implementation uses 5 percent of negative observations instead of the frozen worst 5 percent of all exact-date daily returns; the economic runner is not pinned in the freeze; output artifacts were rewritten during review."},
                {"review_id": "zero_wait_overlay_primary_review", "status": "NEEDS_FIX", "finding": "The results are structurally complete but cannot be reused as frozen-contract evidence until metric semantics and runner/artifact provenance are repaired."},
            ],
            "decisive_reasons": [
                "The five primary gate rows are all FAIL.",
                "Reported daily_CVaR_95 is not the frozen endpoint: the runner averages the worst five percent of negative observations only.",
                "The economic runner and verifier are untracked/unpinned relative to the frozen manifest, so outcome-driven implementation drift cannot be ruled out.",
                "Results/report artifacts were rewritten during review; the current receipt is internally consistent but does not establish immutable run provenance.",
            ],
            "source": "data/research_runs/zero_wait_overlays_2026_09_10/results.json",
        },
        "aggregate": {
            "primary_attempt_count": 45,
            "admitted": 0,
            "integration_ready": 0,
            "promotion": 0,
            "family_globally_disproven": False,
            "interpretation": "This closes the currently tested routes under the user no-wait/no-paid constraint; it does not globally disprove any strategy family.",
        },
        "closure_policy": {
            "same_sample_rerun_required": False,
            "same_sample_rerun_reason": "A01-A06 is invalidated and dropped; ZWO requires a new corrected freeze rather than another run of the mutable/incorrect evidence package.",
            "no_pending_work_marker": True,
            "future_reopen_condition": "Only a changed user constraint plus a new freeze, pinned runner, corrected endpoint semantics, and independent review may reopen research.",
            "production_config_paper_live_untouched": True,
            "network_used_by_closure_builder": False,
        },
        "source_pins": inputs,
        "frozen_manifest": {"path": "data/research_manifests/zero_wait_overlay_freeze_2026_09_10.json", "physical_sha256": inputs["data/research_manifests/zero_wait_overlay_freeze_2026_09_10.json"]["sha256"], "canonical_sha256": MANIFEST_CANONICAL},
        "control_receipt": {"path": "data/research/equity_v3_current_parity_control_2026_08/control_r3.json", "physical_sha256": inputs["data/research/equity_v3_current_parity_control_2026_08/control_r3.json"]["sha256"], "embedded_sha256": ZWO_CONTROL_EMBEDDED, "source_commit": HEAD, "counts": {"rebalance_decisions": 495, "fills": 1890, "terminal_events": 12, "incomplete_strategy_batches": 0}},
        "decision_sha256": "",
    }
    decision["decision_sha256"] = self_hash(decision, "decision_sha256")
    return decision


def render_report(decision: dict[str, Any], paths: dict[str, str]) -> str:
    zwo = decision["zero_wait_overlay"]
    return "\n".join([
        "# Zero-wait final decision — 2026-09-10",
        "",
        "Karar: `CLOSED_ZERO_WAIT_CONSTRAINT`. Kullanıcının hemen sonuç ve no-wait/no-paid-data kısıtı altında mevcut araştırma yolları kesin olarak kapatılmıştır.",
        "",
        "Bu kapanış herhangi bir strateji ailesini küresel olarak çürütmez. Yalnızca bugün test edilen yolların kanıt, zaman ve yetki sınırları altında admission/integration için uygun olmadığını kaydeder.",
        "",
        "## Kapsam ve sayımlar",
        "",
        "- Original 34: immediate `0`; `CLOSE_ZERO_WAIT=2`, `CLOSE_NOT_IMMEDIATE=7`, `CLOSE_UNAUTHORIZED_PAID_DATA=25`; immediate retest `0`.",
        "- A01-A06: `POST_OUTCOME_PROTOCOL_CHANGE_INVALID`; admitted `0`, integration `0`, dropped `6`. Duplicate dividend ve joint NAV/capacity kanıtı çözülmemiştir; iki review `NEEDS_FIX` verdi.",
        f"- Zero-wait overlays: `{zwo['classification']}`; admitted `0`, integration `0`, dropped `5`; `45` vaka ve primary holdout x 2x altında beş adayın tamamı `FAIL`.",
        "- Tüm 45 primary attempt için integration-ready `0`; promotion `0`.",
        "",
        "## Zero-wait overlay hükmü",
        "",
        "Metrikler tanısal olarak korunur ve tekrar admission kanıtı olarak kullanılamaz. CVaR metriği frozen tanımla eşleşmiyor: runner negatif gözlemlerin yüzde beşini kullanıyor; exact-date serisinin en kötü yüzde beşini kullanması gerekiyordu. Ekonomik runner/verifier freeze içinde hash ile bağlanmamış ve sonuç artefaktları review sırasında yeniden yazılmıştır. Bu nedenle mevcut sonuç paketi kanıt olarak geçersizdir.",
        "",
        "Aynı örneklem üzerinde yeni ekonomik rerun yapılmayacaktır. A01-A06 zaten invalidated/dropped durumundadır; ZWO için aynı paketi yeniden çalıştırmak endpoint ve provenance kusurlarını çözmez. İleride kısıtlar değişirse yeni freeze, runner hash'i, düzeltilmiş CVaR ve bağımsız review gerekir.",
        "",
        "## Değişmeyen sınırlar",
        "",
        "Admission, integration ve promotion kapalıdır. Production config, PAPER ve LIVE dokunulmadan bırakılmıştır. Bu closure builder ekonomik worker çalıştırmadı ve ağ kullanmadı.",
        "",
        "## Hash kanıtı",
        "",
        *[f"- `{k}`: `{v}`" for k, v in paths.items()],
        "",
        "ZERO_WAIT_FINAL_CLOSURE_COMPLETE",
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
    report_path.write_text(render_report(decision, {"decision": str(decision_path.relative_to(repo)), "manifest": str(manifest_path.relative_to(repo)), "receipt": str(receipt_path.relative_to(repo)), "zwo_results_sha256": EXPECTED["data/research_runs/zero_wait_overlays_2026_09_10/results.json"], "zwo_report_sha256": EXPECTED["docs/research/zero_wait_overlay_results_2026_09_10.md"], "a01_invalidation_sha256": EXPECTED["data/research_runs/immediate_candidates_invalidation_2026_09_10/decision.json"]}), encoding="utf-8")
    manifest = {"schema": "zero-wait-final-decision-manifest-v1", "decision": {"path": str(decision_path.relative_to(repo)), "sha256": sha256(decision_path)}, "report": {"path": str(report_path.relative_to(repo)), "sha256": sha256(report_path)}, "inputs": decision["source_pins"], "scope": {"original_34": 34, "a01_a06": 6, "zero_wait_overlay": 5, "primary_attempts": 45, "integration_ready": 0}, "classification": "CLOSED_ZERO_WAIT_CONSTRAINT", "manifest_sha256": ""}
    manifest["manifest_sha256"] = self_hash(manifest, "manifest_sha256")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {"schema": "zero-wait-final-decision-receipt-v1", "status": "ZERO_WAIT_FINAL_CLOSURE_COMPLETE", "decision": {"path": str(decision_path.relative_to(repo)), "sha256": sha256(decision_path)}, "manifest": {"path": str(manifest_path.relative_to(repo)), "sha256": sha256(manifest_path)}, "report": {"path": str(report_path.relative_to(repo)), "sha256": sha256(report_path)}, "counts": {"original_34": 34, "original_immediate": 0, "original_close_zero_wait": 2, "original_close_not_immediate": 7, "original_close_unauthorized_paid": 25, "a01_a06_dropped": 6, "zwo_dropped": 5, "primary_attempts": 45, "integration_ready": 0}, "admission": False, "integration": False, "promotion": False, "diagnostic_metrics_preserved": True, "diagnostic_metrics_reuse_prohibited": True, "family_globally_disproven": False, "same_sample_rerun_required": False, "production_paper_live_untouched": True, "network_used": False, "receipt_sha256": ""}
    receipt["receipt_sha256"] = self_hash(receipt, "receipt_sha256")
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"status": "ZERO_WAIT_FINAL_CLOSURE_COMPLETE", "decision_sha256": sha256(decision_path), "manifest_sha256": sha256(manifest_path), "receipt_sha256": sha256(receipt_path), "report_sha256": sha256(report_path)}


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
