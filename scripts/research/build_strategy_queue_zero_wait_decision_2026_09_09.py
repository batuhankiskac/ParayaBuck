"""Build the zero-wait decision for the frozen 34-candidate research queue.

This is a research-only closure.  It consumes the already completed aggregate
and candidate verdicts, performs no acquisition or economic run, and writes a
hash-pinned decision bundle plus a short human-readable report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO / "data/research_runs/strategy_queue_zero_wait_2026_09_09"
DEFAULT_REPORT = REPO / "docs/research/strategy_queue_zero_wait_decision_2026_09_09.md"
AGGREGATE = "data/research_runs/strategy_queue_max_testing_2026_09_09/aggregate_results.json"
CANDIDATES = "data/research_runs/strategy_queue_max_testing_2026_09_09/candidate_verdicts.json"
DECISION_NAME = "decision.json"
MANIFEST_NAME = "manifest.json"
RECEIPT_NAME = "receipt.json"
PENDING = "PENDING_FREEZE"
EXPECTED_IDS = sorted(
    [f"H{i:02d}" for i in range(1, 17)]
    + [f"K{i:02d}" for i in range(1, 13)]
    + [f"X{i:02d}" for i in range(1, 7)]
)

CLOSE_ZERO_WAIT = {"H14", "K11"}
CLOSE_NOT_IMMEDIATE = {"H06", "H10", "H13", "K10", "X04", "X05", "X06"}
CLOSE_UNAUTHORIZED_PAID_DATA = set(EXPECTED_IDS) - CLOSE_ZERO_WAIT - CLOSE_NOT_IMMEDIATE
EXPECTED_CLASSIFICATION = {
    **{candidate: "CLOSE_ZERO_WAIT" for candidate in CLOSE_ZERO_WAIT},
    **{candidate: "CLOSE_NOT_IMMEDIATE" for candidate in CLOSE_NOT_IMMEDIATE},
    **{candidate: "CLOSE_UNAUTHORIZED_PAID_DATA" for candidate in CLOSE_UNAUTHORIZED_PAID_DATA},
}

DETAILS = {
    "H06": "Local Form 4 history is receipt-backed, but signal rank, selected notional, cost ledger, and V3 overlay admission are absent; the diagnostic cannot be treated as an actionable strategy.",
    "H10": "Only three exact filing revision pairs exist and no rank, selected notional, cost ledger, or V3 overlay is present; the diagnostic cannot reach the real strategy screen today.",
    "H13": "Daily bars do not provide intraday or auction execution truth; a real test still requires intraday data and execution capability.",
    "K10": "The evidence is one pilot day with missing available_at, duplicate events, and no complete liquidation/mark/index join; this needs more data and execution engineering.",
    "X04": "Pairs trading requires short borrow, recall, financing, margin, and two-leg fill truth, none of which is available in the current authorized scope.",
    "X05": "Cash-and-carry requires dated futures and spot legs, expiry, basis, margin, settlement, costs, and two-leg fills; the current sample is insufficient.",
    "X06": "Funding carry requires a PIT liquidation/revision ledger and executable perp-short plus spot legs; current evidence has zero admitted rows.",
    "H14": "The retrospective diagnostic is not actionable: the frozen shared position cap is violated and the matrix has no true holdout. Only a forward collector could create the missing evidence.",
    "K11": "The archive contains no local trade/quote/depth/aggressor availability truth. A forward collector and shadow book are required; waiting months is outside this decision.",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any, field: str) -> str:
    payload = dict(value)
    payload[field] = PENDING
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def pin(repo: Path, relative: str) -> dict[str, Any]:
    path = repo / relative
    if not path.is_file():
        raise ValueError(f"input_missing:{relative}")
    stat = path.stat()
    return {"path": relative, "sha256": sha256(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _evidence(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_gate": record.get("current_gate"),
        "current_verdict": record.get("current_verdict"),
        "data_collected": record.get("data_collected", {}),
        "standalone": record.get("standalone", {}),
        "windows": record.get("windows", {}),
        "tests_run": record.get("tests_run", []),
    }


def build_decision(repo: Path = REPO) -> dict[str, Any]:
    aggregate_path = repo / AGGREGATE
    candidate_path = repo / CANDIDATES
    aggregate = load_json(aggregate_path)
    candidates = load_json(candidate_path)
    records = candidates.get("records")
    if aggregate.get("counts", {}).get("primary_candidates") != 34 or not isinstance(records, list) or len(records) != 34:
        raise ValueError("aggregate_inventory_invalid")
    by_id = {row.get("candidate_id"): row for row in records if isinstance(row, dict)}
    if set(by_id) != set(EXPECTED_IDS):
        raise ValueError("candidate_inventory_invalid")
    source_pins = {AGGREGATE: pin(repo, AGGREGATE), CANDIDATES: pin(repo, CANDIDATES)}
    rows: list[dict[str, Any]] = []
    for candidate_id in EXPECTED_IDS:
        source = by_id[candidate_id]
        classification = EXPECTED_CLASSIFICATION[candidate_id]
        rows.append(
            {
                "candidate_id": candidate_id,
                "classification": classification,
                "immediate_retest": False,
                "economic_run": False,
                "economic_eligible": False,
                "promotion": False,
                "admitted": 0,
                "detail": DETAILS.get(candidate_id, "The required PIT or historical licensed data is not authorized or available today."),
                "current_gate": source.get("current_gate"),
                "current_verdict": source.get("current_verdict"),
                "evidence": _evidence(source),
            }
        )
    decision = {
        "schema": "strategy-queue-zero-wait-decision-v1",
        "decision_date": "2026-09-09",
        "research_only": True,
        "network_used": False,
        "production_paper_live_untouched": True,
        "no_auto_promotion": True,
        "candidate_count": 34,
        "candidate_ids": EXPECTED_IDS,
        "candidate_ids_unique": True,
        "immediate_retest_count": 0,
        "counts": {
            "CLOSE_ZERO_WAIT": len(CLOSE_ZERO_WAIT),
            "CLOSE_NOT_IMMEDIATE": len(CLOSE_NOT_IMMEDIATE),
            "CLOSE_UNAUTHORIZED_PAID_DATA": len(CLOSE_UNAUTHORIZED_PAID_DATA),
            "IMMEDIATE_RETEST": 0,
        },
        "decisions": rows,
        "source_pins": source_pins,
        "decision_sha256": PENDING,
    }
    decision["decision_sha256"] = canonical_hash(decision, "decision_sha256")
    return decision


def render_report(decision: dict[str, Any], decision_relative: str, manifest_relative: str, receipt_relative: str) -> str:
    lines = [
        "# Strategy queue zero-wait kararı — 2026-09-09",
        "",
        "Kullanıcının aylarca forward veri beklememe kısıtı uygulanmıştır. Bu belge araştırma kapanışıdır; Strateji ailesi hakkında global çürütme iddiası değildir.",
        "",
        f"34 adayın bugün gerçek `true holdout + cost + caps + V3 complement` sınavına ulaşan sayısı `{decision['immediate_retest_count']}`. Hiçbir aday ekonomik olarak kabul edilmedi, PAPER/LIVE veya production değişmedi.",
        "",
        "## Sayılar",
        "",
        "| Karar | Sayı |",
        "|---|---:|",
        f"| `CLOSE_ZERO_WAIT` | {decision['counts']['CLOSE_ZERO_WAIT']} |",
        f"| `CLOSE_NOT_IMMEDIATE` | {decision['counts']['CLOSE_NOT_IMMEDIATE']} |",
        f"| `CLOSE_UNAUTHORIZED_PAID_DATA` | {decision['counts']['CLOSE_UNAUTHORIZED_PAID_DATA']} |",
        f"| `IMMEDIATE_RETEST` | {decision['counts']['IMMEDIATE_RETEST']} |",
        "",
        f"`CLOSE_ZERO_WAIT`: {', '.join(sorted(CLOSE_ZERO_WAIT))}. Bunlar ileriye dönük collector/shadow book olmadan bugün tamamlanamaz; kullanıcı zaman kısıtı altında kuyruktan kapatılmıştır.",
        "",
        f"`CLOSE_NOT_IMMEDIATE`: {', '.join(sorted(CLOSE_NOT_IMMEDIATE))}. H06/H10'da diagnostic çıktı bulunması yeterli değildir; rank/notional ve gerçek overlay sınavı eksiktir. H13/K10/X04/X05/X06'da execution ve veri kimliği bağımlılıkları bugün tamamlanamaz.",
        "",
        f"`CLOSE_UNAUTHORIZED_PAID_DATA`: {', '.join(sorted(CLOSE_UNAUTHORIZED_PAID_DATA))}. Bu adaylar mevcut authorized/local/free kapsam ile bugün tamamlanamaz; gerekli tarihsel PIT, lisanslı veya yetkilendirilmiş kaynak bu çalışmada kullanıma açık değildir.",
        "",
        "Bu sınıflandırma aynı backtesti tekrarlamaz: örnek/kol değişmeden tekrarlama PIT, holdout, execution veya veri kimliği açığını onarmaz.",
        "",
        "## Kanıt bağlantıları",
        "",
        f"Karar JSON: `{decision_relative}`; manifest: `{manifest_relative}`; receipt: `{receipt_relative}`.",
        "",
        "ZERO_WAIT_PRUNE_COMPLETE",
    ]
    return "\n".join(lines) + "\n"


def write_bundle(repo: Path = REPO, output_dir: Path = DEFAULT_OUTPUT_DIR, report_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    decision = build_decision(repo)
    output_dir.mkdir(parents=True, exist_ok=True)
    decision_path = output_dir / DECISION_NAME
    manifest_path = output_dir / MANIFEST_NAME
    receipt_path = output_dir / RECEIPT_NAME
    decision_path.write_text(json.dumps(decision, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(decision, str(decision_path.relative_to(repo)), str(manifest_path.relative_to(repo)), str(receipt_path.relative_to(repo))), encoding="utf-8")
    manifest = {
        "schema": "strategy-queue-zero-wait-manifest-v1",
        "decision": {"path": str(decision_path.relative_to(repo)), "sha256": sha256(decision_path)},
        "report": {"path": str(report_path.relative_to(repo)), "sha256": sha256(report_path)},
        "inputs": decision["source_pins"],
        "counts": decision["counts"],
        "immediate_retest_count": 0,
        "manifest_sha256": PENDING,
    }
    manifest["manifest_sha256"] = canonical_hash(manifest, "manifest_sha256")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {
        "schema": "strategy-queue-zero-wait-receipt-v1",
        "decision": {"path": str(decision_path.relative_to(repo)), "sha256": sha256(decision_path)},
        "manifest": {"path": str(manifest_path.relative_to(repo)), "sha256": sha256(manifest_path)},
        "report": {"path": str(report_path.relative_to(repo)), "sha256": sha256(report_path)},
        "candidate_count": 34,
        "immediate_retest_count": 0,
        "counts": decision["counts"],
        "economic_run": False,
        "promotion": False,
        "network_used": False,
        "receipt_sha256": PENDING,
    }
    receipt["receipt_sha256"] = canonical_hash(receipt, "receipt_sha256")
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"status": "ZERO_WAIT_PRUNE_COMPLETE", "candidate_count": 34, "counts": decision["counts"], "decision_sha256": sha256(decision_path), "manifest_sha256": sha256(manifest_path), "receipt_sha256": sha256(receipt_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        print(json.dumps(write_bundle(args.repo, args.output_dir, args.report), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc), "outputs_written": False}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
