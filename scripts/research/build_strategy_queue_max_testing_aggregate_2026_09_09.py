"""Build and verify the 34-card maximum-testing closure.

This is a research-only aggregation layer.  It reads the independently reviewed
equity, crypto, and cross-asset packages, records both their physical and
canonical hashes, and never writes production, PAPER, or LIVE state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research import build_strategy_queue_contract_2026_09_09 as contract_builder  # noqa: E402

OUT = REPO / "data/research_runs/strategy_queue_max_testing_2026_09_09"
REPORT = REPO / "docs/research/strategy_queue_max_testing_report_2026_09_09.md"
EXPECTED_IDS = [*(f"H{i:02d}" for i in range(1, 17)), *(f"K{i:02d}" for i in range(1, 13)), *(f"X{i:02d}" for i in range(1, 7))]
EQUITY_IDS = [f"H{i:02d}" for i in range(1, 17)]
CRYPTO_IDS = [f"K{i:02d}" for i in range(1, 13)]
CROSSASSET_IDS = [f"X{i:02d}" for i in range(1, 7)]
PENDING = "PENDING_FREEZE"
UNSAFE_RE = re.compile(r"NEXT_PUBLIC_PROBE|BLOCKED_RETRY_REQUIRED|PROBE_REQUEST_ERROR|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)

CONTRACT_PATH = "data/research_runs/strategy_queue_contract_2026_09_09/candidate_contracts.json"
EQUITY_DIR = "data/research_runs/equity_h01_h16_max_testing_verdict_2026_09_09"
LOCAL_EQUITY_DIR = "data/research_runs/equity_local_diagnostics_2026_09_09"
H14C_DIR = "data/research_runs/equity_h14c_posthoc_2026_09_09"
CRYPTO_DIR = "data/research_runs/crypto_max_testing_2026_09_09"
CROSSASSET_DIR = "data/research_runs/crossasset_max_testing_2026_09_09_v3"
PRODUCTION_SCOPE = (
    "config/settings.yaml",
    "src/parayabuck/portfolio/equity_residual_momentum_v3.py",
    "src/parayabuck/portfolio/crypto_low_variance.py",
    "src/parayabuck/execution/portfolio.py",
    "src/parayabuck/research/runner.py",
)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def canonical_hash(value: dict[str, Any], field: str) -> str:
    payload = dict(value)
    payload[field] = PENDING
    return hashlib.sha256(canonical(payload)).hexdigest()


def physical_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(relative: str) -> dict[str, Any]:
    return json.loads((REPO / relative).read_text(encoding="utf-8"))


def source_ref(relative: str, *, logical_sha256: str | None = None) -> dict[str, Any]:
    path = REPO / relative
    if not path.is_file():
        raise ValueError(f"source_missing:{relative}")
    ref = {"path": relative, "bytes": path.stat().st_size, "physical_sha256": physical_hash(path)}
    if logical_sha256 is not None:
        ref["canonical_sha256"] = logical_sha256
    return ref


def verify_source_provenance() -> dict[str, Any]:
    contract = load_json(CONTRACT_PATH)
    contract_builder.verify_contract(contract, REPO)
    if contract.get("contract_sha256") != "9e7b9dc11fd07e93d23d9249696092f62149c8def4f6948b0a72509c11660721":
        raise ValueError("definition_contract_not_current")

    equity_results = load_json(f"{EQUITY_DIR}/results.json")
    equity_manifest = load_json(f"{EQUITY_DIR}/manifest.json")
    equity_receipt = load_json(f"{EQUITY_DIR}/receipt.json")
    equity_result_path = REPO / f"{EQUITY_DIR}/results.json"
    equity_report_path = REPO / f"{EQUITY_DIR}/report.md"
    if equity_manifest["result"]["sha256"] != physical_hash(equity_result_path) or equity_receipt["result"]["sha256"] != physical_hash(equity_result_path):
        raise ValueError("equity_result_pin_mismatch")
    if equity_manifest["report"]["sha256"] != physical_hash(equity_report_path) or equity_receipt["report"]["sha256"] != physical_hash(equity_report_path):
        raise ValueError("equity_report_pin_mismatch")
    if physical_hash(equity_result_path) != "70d771a65dc75f8c23eb8e5c1d8e506a3c9455e449d9640be1ea6261b0a55473":
        raise ValueError("equity_result_not_final_review_pin")
    if equity_results.get("candidate_ids") != [*EQUITY_IDS[:13], "H14", "H15", "H16", "H14C"]:
        raise ValueError("equity_inventory_pin_mismatch")

    crypto_results = load_json(f"{CRYPTO_DIR}/diagnostics/diagnostic_results.json")
    capacity_results = load_json(f"{CRYPTO_DIR}/capacity_diagnostics/capacity_diagnostic_results.json")
    if crypto_results.get("results_sha256") != "b001fc2a5961f5ff73e27061a7dc0ccab85f801b7a0c40e2fc7719dc7b1e289d":
        raise ValueError("crypto_diagnostic_not_final_review_pin")
    if capacity_results.get("results_sha256") != "5b217b17323acec96419d5bb22a3c5f8353b99d95a1d9c947712f11092338df8":
        raise ValueError("crypto_capacity_not_final_review_pin")
    for value, field in ((crypto_results, "results_sha256"), (capacity_results, "results_sha256")):
        if value.get(field) != hashlib.sha256(canonical({key: item for key, item in value.items() if key != field})).hexdigest():
            raise ValueError(f"crypto_{field}_canonical_mismatch")

    cross_matrix = load_json(f"{CROSSASSET_DIR}/verdict_matrix.json")
    cross_results = load_json(f"{CROSSASSET_DIR}/overlay_diagnostic_results.json")
    if cross_matrix.get("matrix_sha256") != "f12f7fa3e8027d256676e23dee31056cb0822799f2cd8199e939cad893125b8a":
        raise ValueError("crossasset_matrix_not_final_review_pin")
    if cross_results.get("results_sha256") != "c489e27670bc6fab74d91c38063166525f962ec407c1ea9c4c55bc89f9ec96aa":
        raise ValueError("crossasset_results_not_final_review_pin")
    if cross_matrix.get("matrix_sha256") != hashlib.sha256(canonical({key: item for key, item in cross_matrix.items() if key != "matrix_sha256"})).hexdigest():
        raise ValueError("crossasset_matrix_canonical_mismatch")
    if cross_results.get("results_sha256") != hashlib.sha256(canonical({key: item for key, item in cross_results.items() if key != "results_sha256"})).hexdigest():
        raise ValueError("crossasset_results_canonical_mismatch")

    paths = [
        CONTRACT_PATH,
        f"{EQUITY_DIR}/results.json", f"{EQUITY_DIR}/report.md", f"{EQUITY_DIR}/manifest.json", f"{EQUITY_DIR}/receipt.json",
        f"{LOCAL_EQUITY_DIR}/diagnostic_results.json", f"{LOCAL_EQUITY_DIR}/diagnostic_report.md", f"{LOCAL_EQUITY_DIR}/manifest.json", f"{LOCAL_EQUITY_DIR}/receipt.json",
        f"{H14C_DIR}/results.json", f"{H14C_DIR}/report.md", f"{H14C_DIR}/manifest.json", f"{H14C_DIR}/receipt.json",
        f"{CRYPTO_DIR}/diagnostics/diagnostic_results.json", f"{CRYPTO_DIR}/diagnostics/diagnostic_report.md", f"{CRYPTO_DIR}/diagnostics/protocol_v2.json", f"{CRYPTO_DIR}/diagnostics/validation_receipt.json",
        f"{CRYPTO_DIR}/capacity_diagnostics/capacity_diagnostic_results.json", f"{CRYPTO_DIR}/capacity_diagnostics/capacity_diagnostic_report.md", f"{CRYPTO_DIR}/capacity_diagnostics/protocol_post_hoc_exploratory.json", f"{CRYPTO_DIR}/capacity_diagnostics/validation_receipt.json",
        f"{CROSSASSET_DIR}/verdict_matrix.json", f"{CROSSASSET_DIR}/verdict_matrix_report.md", f"{CROSSASSET_DIR}/verdict_matrix_manifest.json", f"{CROSSASSET_DIR}/verdict_matrix_receipt.json",
        f"{CROSSASSET_DIR}/overlay_diagnostic_results.json", f"{CROSSASSET_DIR}/overlay_diagnostic_report.md",
    ]
    refs = {relative: source_ref(relative) for relative in paths}
    refs[CONTRACT_PATH]["canonical_sha256"] = contract["contract_sha256"]
    refs[f"{CRYPTO_DIR}/diagnostics/diagnostic_results.json"]["canonical_sha256"] = crypto_results["results_sha256"]
    refs[f"{CRYPTO_DIR}/capacity_diagnostics/capacity_diagnostic_results.json"]["canonical_sha256"] = capacity_results["results_sha256"]
    refs[f"{CROSSASSET_DIR}/verdict_matrix.json"]["canonical_sha256"] = cross_matrix["matrix_sha256"]
    refs[f"{CROSSASSET_DIR}/overlay_diagnostic_results.json"]["canonical_sha256"] = cross_results["results_sha256"]
    return {"definition_contract_sha256": contract["contract_sha256"], "source_artifacts": refs}


def _windows(status: str, *, active: Any = None) -> dict[str, Any]:
    if status == "not_run":
        return {window: {"status": "NOT_RUN"} for window in ("full", "recent", "true_holdout")}
    return {window: {"status": status, "active_days": active.get(window) if isinstance(active, dict) else active} for window in ("full", "recent", "true_holdout")}


def _base_record(cid: str, definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": cid,
        "mechanism": definition["mechanism"],
        "definition_feasibility": definition["feasibility"],
        "current_gate": None,
        "data_collected": None,
        "tests_run": [],
        "standalone": {"status": "NOT_RUN"},
        "complement": {"fixed_95_5": "NOT_RUN", "capacity_aware": "NOT_RUN", "caps": "NOT_RUN"},
        "windows": _windows("not_run"),
        "costs_caps_statistics": "Not run because the causal admission gate was not satisfied.",
        "pit_caveat": "No admissible PIT economic evidence.",
        "current_verdict": None,
        "globally_completely_rejected": False,
        "reopen_exact_evidence": definition["feasibility"],
        "source_artifacts": [],
    }


def _range_from_arms(arms: list[dict[str, Any]], field: str = "active_days") -> dict[str, int] | None:
    values = [int(item[field]) for item in arms if item.get(field) is not None]
    return {"min": min(values), "max": max(values)} if values else None


def build_candidate_verdicts(provenance: dict[str, Any]) -> list[dict[str, Any]]:
    contract = load_json(CONTRACT_PATH)["candidates"]
    equity = {row["candidate_id"]: row for row in load_json(f"{EQUITY_DIR}/results.json")["rows"]}
    crypto = load_json(f"{CRYPTO_DIR}/diagnostics/diagnostic_results.json")["cards"]
    crypto_capacity = load_json(f"{CRYPTO_DIR}/capacity_diagnostics/capacity_diagnostic_results.json")["cards"]
    cross = load_json(f"{CROSSASSET_DIR}/verdict_matrix.json")["cards"]
    records: list[dict[str, Any]] = []
    for cid in EXPECTED_IDS:
        record = _base_record(cid, contract[cid])
        record["source_artifacts"] = [CONTRACT_PATH]
        if cid in EQUITY_IDS:
            row = equity[cid]
            record["current_gate"] = row["authoritative_gate_status"]
            record["data_collected"] = row["evidence"]
            record["current_verdict"] = row["category"]
            record["source_artifacts"] += [f"{EQUITY_DIR}/results.json", f"{EQUITY_DIR}/report.md"]
            record["pit_caveat"] = row["terminal_blocker"]
            if cid in {"H06", "H10"}:
                record["tests_run"] = ["retrospective event/metadata diagnostic", "full/recent/true-holdout join where available", "block bootstrap and +7-day placebo where sample permits"]
                record["standalone"] = {"status": "RETROSPECTIVE_DIAGNOSTIC_ONLY", "economic_admitted": False}
                record["complement"] = {"fixed_95_5": "NOT_RUN", "capacity_aware": "NOT_RUN", "caps": "NOT_RUN"}
                evidence = row["evidence"]
                window_counts = list(evidence.get("full_recent_holdout", [None, None, None]))
                window_counts += [None] * (3 - len(window_counts))
                record["windows"] = _windows("RETROSPECTIVE_DIAGNOSTIC", active={"full": window_counts[0], "recent": window_counts[1], "true_holdout": window_counts[2]})
                record["costs_caps_statistics"] = "H06: 10,000-block bootstrap and 240 fixed +7 placebo rows; no signal-notional/cost ledger or full issuer permutation. H10: three exact pairs, n<21 so bootstrap not run; no cost/portfolio overlay."
                record["current_verdict"] = "RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_ADMISSION"
            elif cid == "H13":
                record["tests_run"] = ["143,446 corporate-action rows checked", "raw/adjusted/factor invariant attempt", "authorized intraday route/access gate"]
                record["current_verdict"] = "DATA_DIAGNOSTIC_BLOCKED_NO_EXECUTABLE_INTRADAY_TEST"
                record["costs_caps_statistics"] = "No return, cost, cap, or statistical test was admissible; paired raw/adjusted rows = 0."
                record["windows"] = _windows("not_run")
            elif cid == "H14":
                e = row["evidence"]
                record["tests_run"] = ["H14a frozen 18-cell fixed 95/5 diagnostic", "H14b strict-prior capacity overlay at 1x/2x/3x", "H14c V3-preserving capacity overlay", "full/recent/true-holdout", "10,000-block bootstrap and paired-sign permutation", "native +7 trading-day schedule placebo"]
                record["standalone"] = {"status": "FROZEN_DIAGNOSTIC_ONLY", "cells": e.get("h14a_cells")}
                record["complement"] = {"fixed_95_5": "TESTED_INFEASIBLE_DIAGNOSTIC", "capacity_aware": "TESTED_DIAGNOSTIC", "caps": e["h14b_cost_metrics"][0]["cap_checks"]}
                record["windows"] = {"full": {"status": "MEASURED_DIAGNOSTIC", "active_days": e.get("h14b_active_days")}, "recent": {"status": "MEASURED_DIAGNOSTIC"}, "true_holdout": {"status": "MEASURED_DIAGNOSTIC", "active_days": 0}}
                record["costs_caps_statistics"] = "Corrected native sleeve scale and strict prior-date lookup. H14b full ΔTR -0.004392 (1x), -0.004479 (2x), -0.004565 (3x); 31 active days/14 months, recent 12, true holdout 0. H14c full ΔTR +0.003236/+0.003150/+0.003063; bootstrap CIs include 0 and paired permutation p≈0.405–0.428; recent +0.000151/+0.000134/+0.000117, p≈0.901–0.917. All caps pass. Native +7 placebo is larger (+0.008274 at 1x), so the small positive is unsupported. Turnover ledger absent."
                record["current_verdict"] = "TESTED_BUT_NO_INTEGRATION_CANDIDATE"
                record["source_artifacts"] += [f"{LOCAL_EQUITY_DIR}/diagnostic_results.json", f"{H14C_DIR}/results.json"]
            elif cid == "H15":
                record["tests_run"] = ["one lifecycle metadata row checked"]
                record["current_verdict"] = "LIFECYCLE_METADATA_ONLY_NO_EVENT_RETURN"
                record["costs_caps_statistics"] = "No event return, cost, cap, or statistical test; announcement and terminal lifecycle fields are absent."
                record["windows"] = _windows("not_run")
            elif cid == "H08":
                record["tests_run"] = ["causal input gate and event adapter feasibility check"]
            else:
                record["tests_run"] = ["frozen definition and causal data/access gate"]
            if cid not in {"H06", "H08", "H10", "H13", "H14", "H15"}:
                record["current_verdict"] = "NOT_TESTABLE_CAUSAL_DATA_OR_ACCESS_BLOCKED"
        elif cid in CRYPTO_IDS:
            card = crypto[cid]
            record["current_gate"] = card["status"]
            record["data_collected"] = {"signal_rows": card.get("signal_rows", 0), "diagnostic_arms": len(card.get("arms", [])), "reason": card.get("reason")}
            record["source_artifacts"] += [f"{CRYPTO_DIR}/diagnostics/diagnostic_results.json", f"{CRYPTO_DIR}/capacity_diagnostics/capacity_diagnostic_results.json"]
            if cid in {"K01", "K03", "K04", "K09", "K11"}:
                arms = card.get("arms", [])
                capacity_card = crypto_capacity.get(cid, {})
                cap_arms = capacity_card.get("arms", [])
                active_by_window = {window: _range_from_arms([a for a in arms if a.get("window") == window]) for window in ("full", "recent", "true_holdout")}
                record["tests_run"] = ["standalone retrospective diagnostics", "fixed 95/5 V3 overlay", "capacity-aware overlay", "lags 1/3/7 and costs 1x/2x/3x", "full/recent/true-holdout", "bootstrap/permutation/regime/drawdown/tail/correlation"]
                record["standalone"] = {"status": "RETROSPECTIVE_ONLY_NO_PIT", "arms": len(arms)}
                record["complement"] = {"fixed_95_5": "MEASURED_RETROSPECTIVE_ONLY", "capacity_aware": "MEASURED_POST_HOC_NO_PIT", "caps": "0 capacity violations" if cap_arms else "NOT_RUN"}
                record["windows"] = {key: {"status": "MEASURED_RETROSPECTIVE_ONLY", "active_days_range": value} for key, value in active_by_window.items()}
                record["costs_caps_statistics"] = "All frozen 1x/2x/3x cost arms and cap checks were run. K01/K04/K09 tiny capacity-aware positives have bootstrap intervals crossing 0 and no PIT admission; K03 is negative; K11 and K09 funding-short branch are inactive."
                record["current_verdict"] = "RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_PIT_NO_ELIGIBILITY"
                if cid == "K09":
                    record["data_collected"]["funding_short_history_branch"] = crypto[cid].get("funding_short_history")
            else:
                record["tests_run"] = ["exact-field/source gate and route exhaustion"]
                record["current_verdict"] = "NOT_TESTABLE_EXACT_CAUSAL_FIELDS_OR_PIT_BLOCKED"
            record["pit_caveat"] = "Reference and feature rows lack row-level available_at; all crypto arms are retrospective/non-PIT and economic_admitted=false."
        else:
            card = cross[cid]
            record["current_gate"] = card["economic_decision"]
            record["data_collected"] = {"signal_rows": card.get("signal_rows", 0), "raw_evidence_rows": card.get("raw_evidence_rows", 0), "available_at_rows": card.get("available_at_rows", 0), "arms": card.get("arms_considered", 0)}
            record["source_artifacts"] += [f"{CROSSASSET_DIR}/verdict_matrix.json", f"{CROSSASSET_DIR}/overlay_diagnostic_results.json"]
            if cid in {"X01", "X05", "X06"}:
                all_arms = [arm for branch in card["branches"] for arm in branch.get("arms", [])]
                active = _range_from_arms(all_arms)
                record["tests_run"] = ["standalone retrospective diagnostic", "fixed 95/5 V3 overlay", "capacity-aware overlay", "lags 1/3/7 and costs 1x/2x/3x", "full/recent/true-holdout", "bootstrap/permutation/regime/drawdown/tail/correlation"]
                record["standalone"] = {"status": "RETROSPECTIVE_NON_PIT_DIAGNOSTIC", "arms": card.get("arms_considered", 0)}
                record["complement"] = {"fixed_95_5": "MEASURED_RETROSPECTIVE_ONLY", "capacity_aware": "MEASURED_RETROSPECTIVE_ONLY", "caps": "capacity branch checked; fixed branch retains reported violations"}
                record["windows"] = {window: {"status": "MEASURED_RETROSPECTIVE_ONLY", "active_days_range": active} for window in ("full", "recent", "true_holdout")}
                record["costs_caps_statistics"] = "All frozen 1x/2x/3x cost arms and bootstrap/permutation/regime/risk fields are retained; no arm is admissible. X01 is a daily Treasury diagnostic, not the original monthly ETF carry/roll-down proof. X05 had 0–1 active days and X06 at most 26 active days in the measured arms."
                record["current_verdict"] = "RETROSPECTIVE_NON_PIT_DIAGNOSTIC_NO_ADMISSION"
            else:
                record["tests_run"] = ["public route exhaustion and exact two-leg/PIT capability gate"]
                record["current_verdict"] = "NOT_TESTABLE_ROUTE_OR_EXECUTION_FIELDS_BLOCKED"
            record["pit_caveat"] = "available_at_rows=0; cross-asset arms are retrospective/non-PIT and economic_eligible=false."
        records.append(record)
    if [item["candidate_id"] for item in records] != EXPECTED_IDS:
        raise ValueError("candidate_order_or_uniqueness_invalid")
    return records


def aggregate_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    actual_diagnostic = {"H06", "H10", "K01", "K03", "K04", "K09", "K11", "X01", "X05", "X06"}
    data_diagnostic = {"H13", "H15"}
    failed_implementation = {"H14"}
    ids = {r["candidate_id"] for r in records}
    if len(ids) != 34 or ids != set(EXPECTED_IDS):
        raise ValueError("aggregate_candidate_set_invalid")
    return {
        "primary_candidates": 34,
        "integration_ready": 0,
        "admissible_economic_evidence": 0,
        "retrospective_economic_diagnostics": len(actual_diagnostic),
        "data_or_lifecycle_diagnostics": len(data_diagnostic),
        "tested_but_failed_implementation_or_feasibility": len(failed_implementation),
        "not_testable_current_data_or_access": 34 - len(actual_diagnostic) - len(data_diagnostic) - len(failed_implementation),
        "globally_completely_rejected": 0,
        "replacement_candidates": 0,
        "economic_admitted": 0,
        "promotion": 0,
        "h14_variants_detail_only": 3,
    }


def build() -> dict[str, Any]:
    provenance = verify_source_provenance()
    records = build_candidate_verdicts(provenance)
    counts = aggregate_counts(records)
    candidate_payload = {"schema": "strategy-queue-max-testing-candidate-verdicts-v1", "candidate_ids": EXPECTED_IDS, "candidate_count": 34, "records": records, "economic_run": False, "promotion": False, "candidate_verdicts_sha256": PENDING}
    candidate_payload["candidate_verdicts_sha256"] = canonical_hash(candidate_payload, "candidate_verdicts_sha256")
    aggregate_payload = {"schema": "strategy-queue-max-testing-aggregate-results-v1", "counts": counts, "source_provenance": provenance, "candidate_verdicts_sha256": candidate_payload["candidate_verdicts_sha256"], "no_best_arm_cherry_pick": True, "v3_replacement": False, "production_paper_live_untouched": True, "economic_run": False, "promotion": False, "aggregate_sha256": PENDING}
    aggregate_payload["aggregate_sha256"] = canonical_hash(aggregate_payload, "aggregate_sha256")

    lines = [
        "# ParayaBuck — 34 aday maksimum test birleşik closure raporu (2026-09-09)",
        "",
        "Bu çalışma mevcut V3 stratejisinin yerine geçecek bir aday aramıyor; V3'ü farklı piyasa koşullarında tamamlayabilecek katkıları ölçüyor. Hiçbir aday production/config/registry/PAPER/LIVE akışına alınmadı.",
        "",
        "## Karar özeti",
        "",
        "- 34 primary aday tam ve tekil işlendi; H14a/H14b/H14c varyantları H14 altında ayrıntıdır ve 35. aday sayılmaz.",
        "- Admissible economic evidence: **0**; integration-ready: **0**; V3 replacement: **0**; promotion: **0**.",
        f"- Retrospective ekonomik diagnostic: **{counts['retrospective_economic_diagnostics']}**; veri/lifecycle diagnostic: **{counts['data_or_lifecycle_diagnostics']}**; test edilip uygulanabilirliği başarısız diagnostic: **{counts['tested_but_failed_implementation_or_feasibility']}**; mevcut veri/erişimle test edilemeyen: **{counts['not_testable_current_data_or_access']}**.",
        "- Strateji ailesini kanıtla çürüten global ret: **0**. Diagnostic başarısızlığı ile global strateji reddi aynı kategori değildir.",
        "- Retrospective sonuçlar PIT available_at, execution, cost ve/veya iki-leg doğrulaması olmadan ekonomik kabul kanıtı sayılmadı.",
        "",
        "## 34 primary aday tablosu",
        "",
        "| ID | Mekanizma | Güncel gate | Toplanan veri / gerçek test | V3 tamamlayıcılık | Full / recent / holdout | Maliyet, cap, istatistik, PIT | Güncel karar | Tamamen reddedildi mi? | Yeniden açmak için eksik kanıt |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    def short(value: Any, limit: int = 280) -> str:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if isinstance(value, (dict, list)) else str(value)
        text = text.replace("|", "\\|").replace("\n", " ")
        return text if len(text) <= limit else text[: limit - 1] + "…"
    for record in records:
        windows = "; ".join(f"{key}: {value.get('status')}" + (f" ({short(value.get('active_days_range') or value.get('active_days'))})" if value.get("active_days_range") is not None or value.get("active_days") is not None else "") for key, value in record["windows"].items())
        lines.append("| " + " | ".join([
            record["candidate_id"], short(record["mechanism"], 100), short(record["current_gate"], 90), short(record["data_collected"], 170), short(record["complement"], 115), short(windows, 120), short(record["costs_caps_statistics"], 180), short(record["current_verdict"], 80), "Hayır", short(record["reopen_exact_evidence"], 150),
        ]) + " |")
    lines += [
        "",
        "## H14 ayrıntısı",
        "",
        "H14a'nın frozen 18-cell sonucu korunmuştur. H14b'de native sleeve double-scale ve aynı gün lookahead düzeltildi: strict-prior kapasiteyle 31 aktif gün/14 ay, recent 12, gerçek holdout 0; fixed 95/5 full ΔTR 1x/2x/3x sırasıyla -0.004392/-0.004479/-0.004565. H14c V3 ağırlıklarını %100 koruyup yalnızca kapasite oldukça ekler: full ΔTR +0.003236/+0.003150/+0.003063; bootstrap aralıkları sıfırı içerir, paired permutation p yaklaşık 0.405–0.428. Recent katkı çok küçüktür (+0.000151/+0.000134/+0.000117; p yaklaşık 0.901–0.917); gerçek holdout aktif gün = 0. Native +7 trading-day placebo full ΔTR yaklaşık +0.008274 (1x) ile gerçek katkıdan büyüktür. Bu nedenle H14 şu an tamamlayıcı entegrasyon adayı değildir; global ret de değildir.",
        "",
        "## Crypto ve cross-asset yorum sınırları",
        "",
        "Crypto K01/K04/K09 kapasite diagnosticlerindeki küçük pozitifler istatistiksel olarak desteklenmedi; K03 negatif, K11 ve K09 funding-short branch inactive kaldı. Capacity caps = 0 olması PIT veya ekonomik kabul anlamına gelmez.",
        "",
        "Cross-asset X01 günlük Treasury teşhisidir; özgün aylık ETF carry/roll-down testinin yerine geçmez. X05 ölçülen arms'ta 0–1 aktif gün, X06 en fazla 26 aktif gün gördü. X01/X05/X06 dahil hiçbir X adayı PIT/admitted değildir; X02/X03/X04 ölçülebilir iki-leg/PIT kanıtına ulaşmadı.",
        "",
        "## Kaynak artifact ve hash pinleri",
        "",
        "Birleşik package, aşağıdaki final ve bağımsız incelemeden geçmiş kaynakları fiziksel SHA ve dosya içi canonical SHA ile bağlar. Crypto ve cross-asset logical pinleri sırasıyla `b001fc2a…`, `5b217b17…`, `f12f7fa3…`, `c489e276…`; equity final result fiziksel SHA `70d771a6…` olarak doğrulandı.",
        "",
        "Kaynak bağımsız inceleme statüleri: equity `EQUITY_INDEPENDENT_REVIEW_PASS`; crypto `CRYPTO_MAX_TESTING_COMPLETE_V3` ve capacity package final; cross-asset `CROSSASSET_MAX_TESTING_COMPLETE_V4`. Bu statüler kaynak package kapanışını gösterir, integration approval göstermez.",
        "",
    ]
    for relative, ref in provenance["source_artifacts"].items():
        logical = f"; canonical `{ref['canonical_sha256']}`" if ref.get("canonical_sha256") else ""
        lines.append(f"- `{relative}` — physical `{ref['physical_sha256']}`{logical}")
    lines += [
        "",
        "## Araştırma statüsü",
        "",
        "Bu closure, outcome-based best-arm seçimi yapmaz. `economic_run=false` burada hesap yapılmadı anlamına gelmez: kaynak package'ların retrospective/post-hoc hesaplarını ayrı ayrı ölçüp, PIT/admission sınırı nedeniyle kabul etmediğimizi belirtir. Üretim, config, registry, PAPER ve LIVE dosyaları bu işlem boyunca untouched kaldı.",
        "",
    ]
    report_bytes = "\n".join(lines).encode("utf-8")
    candidate_path = OUT / "candidate_verdicts.json"
    aggregate_path = OUT / "aggregate_results.json"
    manifest_path = OUT / "manifest.json"
    receipt_path = OUT / "receipt.json"
    manifest = {"schema": "strategy-queue-max-testing-manifest-v1", "candidate_verdicts": {"path": str(candidate_path.relative_to(REPO)), "sha256": physical_hash(candidate_path) if candidate_path.exists() else None}, "aggregate_results": {"path": str(aggregate_path.relative_to(REPO)), "sha256": physical_hash(aggregate_path) if aggregate_path.exists() else None}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest(), "bytes": len(report_bytes)}, "source_provenance": provenance, "counts": counts, "economic_run": False, "promotion": False, "manifest_sha256": PENDING}
    candidate_bytes = canonical(candidate_payload)
    aggregate_bytes = canonical(aggregate_payload)
    manifest["candidate_verdicts"]["sha256"] = hashlib.sha256(candidate_bytes).hexdigest()
    manifest["aggregate_results"]["sha256"] = hashlib.sha256(aggregate_bytes).hexdigest()
    manifest["manifest_sha256"] = canonical_hash(manifest, "manifest_sha256")
    manifest_bytes = canonical(manifest)
    receipt = {"schema": "strategy-queue-max-testing-receipt-v1", "candidate_verdicts": {"path": str(candidate_path.relative_to(REPO)), "sha256": hashlib.sha256(candidate_bytes).hexdigest()}, "aggregate_results": {"path": str(aggregate_path.relative_to(REPO)), "sha256": hashlib.sha256(aggregate_bytes).hexdigest()}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest()}, "manifest": {"path": str(manifest_path.relative_to(REPO)), "sha256": hashlib.sha256(manifest_bytes).hexdigest()}, "source_provenance": provenance, "counts": counts, "network_used": False, "economic_run": False, "promotion": False, "receipt_sha256": PENDING}
    receipt["receipt_sha256"] = canonical_hash(receipt, "receipt_sha256")
    atomic_write(candidate_path, candidate_bytes)
    atomic_write(aggregate_path, aggregate_bytes)
    atomic_write(REPORT, report_bytes)
    atomic_write(manifest_path, canonical(manifest))
    atomic_write(receipt_path, canonical(receipt))
    return {"status": "AGGREGATE_MAX_TESTING_COMPLETE", "candidate_count": 34, "counts": counts, "candidate_verdicts_sha256": candidate_payload["candidate_verdicts_sha256"], "aggregate_sha256": aggregate_payload["aggregate_sha256"], "manifest_sha256": manifest["manifest_sha256"], "receipt_sha256": receipt["receipt_sha256"]}


def verify(base: Path = OUT) -> dict[str, Any]:
    candidate_path = base / "candidate_verdicts.json"
    aggregate_path = base / "aggregate_results.json"
    manifest_path = base / "manifest.json"
    receipt_path = base / "receipt.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if candidate.get("candidate_verdicts_sha256") != canonical_hash(candidate, "candidate_verdicts_sha256"):
        raise ValueError("candidate_verdicts_self_hash_mismatch")
    if aggregate.get("aggregate_sha256") != canonical_hash(aggregate, "aggregate_sha256"):
        raise ValueError("aggregate_self_hash_mismatch")
    if manifest.get("manifest_sha256") != canonical_hash(manifest, "manifest_sha256"):
        raise ValueError("manifest_self_hash_mismatch")
    if receipt.get("receipt_sha256") != canonical_hash(receipt, "receipt_sha256"):
        raise ValueError("receipt_self_hash_mismatch")
    if candidate.get("candidate_ids") != EXPECTED_IDS or candidate.get("candidate_count") != 34 or len(candidate.get("records", [])) != 34:
        raise ValueError("candidate_inventory_mismatch")
    counts = aggregate.get("counts")
    if counts != aggregate_counts(candidate["records"]):
        raise ValueError("aggregate_count_consistency_mismatch")
    if candidate.get("economic_run") is not False or candidate.get("promotion") is not False or aggregate.get("economic_run") is not False or aggregate.get("promotion") is not False or aggregate.get("v3_replacement") is not False:
        raise ValueError("unsafe_aggregate_policy")
    if any(item.get("globally_completely_rejected") is not False for item in candidate["records"]):
        raise ValueError("false_global_rejection")
    for item in candidate["records"]:
        if item["candidate_id"] in EXPECTED_IDS and item.get("candidate_id") == "H14C":
            raise ValueError("h14c_counted_as_primary")
    if UNSAFE_RE.search((REPORT).read_text(encoding="utf-8")):
        raise ValueError("unsafe_marker_or_private_contact_in_report")
    expected_files = {"candidate_verdicts": candidate_path, "aggregate_results": aggregate_path, "report": REPORT, "manifest": manifest_path}
    for key, path in expected_files.items():
        if key in {"candidate_verdicts", "aggregate_results"}:
            if manifest[key]["sha256"] != physical_hash(path):
                raise ValueError(f"manifest_artifact_mismatch:{key}")
        elif key == "report" and manifest[key]["sha256"] != physical_hash(path):
            raise ValueError("manifest_artifact_mismatch:report")
    for key in ("candidate_verdicts", "aggregate_results", "report", "manifest"):
        path = expected_files[key]
        if receipt[key]["sha256"] != physical_hash(path):
            raise ValueError(f"receipt_artifact_mismatch:{key}")
    provenance = verify_source_provenance()
    if manifest.get("source_provenance") != provenance or receipt.get("source_provenance") != provenance:
        raise ValueError("source_provenance_changed")
    return {"status": "AGGREGATE_MAX_TESTING_VERIFIED", "candidate_count": 34, "counts": counts, "network_free": True, "read_only": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else build(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
