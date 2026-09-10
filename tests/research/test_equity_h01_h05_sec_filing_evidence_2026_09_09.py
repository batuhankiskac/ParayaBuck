from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.research import verify_equity_h01_h05_sec_filing_evidence_2026_09_09 as verifier


def test_frozen_sec_filing_receipt_binds_every_body_and_hash() -> None:
    receipt = json.loads(verifier.RECEIPT.read_text())
    assert receipt["status"] == "COMPLETE"
    assert receipt["request_count"] == 98
    assert receipt["download_count"] == 98
    assert all(item["status_code"] == 200 for item in receipt["requests"])
    assert all(item["head_status_code"] == 200 for item in receipt["requests"])
    for item in receipt["requests"]:
        path = verifier.REPO / item["output"]
        assert path.is_file()
        assert item["bytes"] == path.stat().st_size
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        assert item["url"].startswith("https://www.sec.gov/")
    assert verifier.verify_receipt()


def test_filing_receipt_mutations_fail_verification(tmp_path: Path) -> None:
    receipt = json.loads(verifier.RECEIPT.read_text())
    receipt["requests"][0]["url"] = "http://www.sec.gov/Archives/invalid"
    mutated = tmp_path / "receipt.json"
    mutated.write_text(json.dumps(receipt))
    assert verifier.verify_receipt(mutated) is False


def test_filing_evidence_catalog_is_reproducible_and_no_credentials() -> None:
    catalog = json.loads(verifier.OUTPUT.read_text())
    assert catalog["status"] == "COMPLETE"
    assert catalog["request_count"] == 98
    assert catalog["body_count"] == 98
    assert catalog["credential_fields_persisted"] is False
    assert "user_agent" not in json.dumps(catalog).lower()
    assert all(item["body_sha256"] for item in catalog["filings"])
    assert all(item["context_count"] > 0 for item in catalog["filings"])
    assert all(item["issuer_cik_identifiers"] == [item["issuer_cik"]] for item in catalog["filings"])
    assert all(
        item["source_receipt"] == str(verifier.RECEIPT.relative_to(verifier.REPO)) for item in catalog["filings"]
    )


def test_filing_evidence_mutation_fails_verification(tmp_path: Path) -> None:
    catalog = json.loads(verifier.OUTPUT.read_text())
    catalog["filings"][0]["body_sha256"] = "0" * 64
    mutated = tmp_path / "catalog.json"
    mutated.write_text(json.dumps(catalog))
    assert verifier.verify_catalog(mutated) is False


def test_numeric_causal_fact_inventory_has_provenance_and_exact_values() -> None:
    inventory = json.loads(verifier.CAUSAL_OUTPUT.read_text())
    assert inventory["status"] == "COMPLETE"
    assert inventory["fact_count"] == len(inventory["facts"]) == 8520
    assert inventory["admitted_fact_count"] == 2937
    assert inventory["decision_grid_row_count"] == len(inventory["decision_grid_evaluation"]) == 288
    assert sum(item["field_evaluation"]["H02"] == "EXACT_TAG_PAIR_AVAILABLE" for item in inventory["decision_grid_evaluation"]) == 68
    gross_profit = [item for item in inventory["facts"] if item["tag"] == "GrossProfit"]
    assert gross_profit
    assert all(item["value_decimal"] is not None for item in gross_profit)
    assert all(item["source_receipt_sha256"] and item["body_sha256"] for item in gross_profit)
    assert all(item["context_ref"] and item["unit_ref"] and item["admission_status"] for item in inventory["facts"])
    assert verifier.verify_causal_inventory()


def test_numeric_causal_fact_mutations_fail_verification(tmp_path: Path) -> None:
    inventory = json.loads(verifier.CAUSAL_OUTPUT.read_text())
    inventory["facts"][0]["body_sha256"] = "0" * 64
    mutated = tmp_path / "causal_fact_inventory.json"
    mutated.write_text(json.dumps(inventory))
    assert verifier.verify_causal_inventory(mutated) is False


def test_r12_gate_binds_filing_evidence_without_changing_frozen_rows() -> None:
    manifest = json.loads(
        (verifier.REPO / "data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r12.json").read_text()
    )
    assert manifest["filing_evidence_inventory"] == str(verifier.OUTPUT.relative_to(verifier.REPO))
    assert manifest["summary"]["row_count"] == 288
    assert manifest["summary"]["feature_coverage"] == {
        "h01_book_to_market": 288,
        "h02_gross_profitability": 72,
        "h03_accruals": 286,
        "h04_asset_growth": 19,
        "h05_net_payout_yield": 32,
    }


def test_r12_source_inventory_canonical_pin_rejects_mutation() -> None:
    manifest = json.loads(
        (verifier.REPO / "data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r12.json").read_text()
    )
    inventory = json.loads((verifier.REPO / manifest["source_inventory"]).read_text())
    canonical = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert canonical == manifest["source_inventory_sha256"]
    inventory[0]["size"] += 1
    mutated = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(mutated).hexdigest() != manifest["source_inventory_sha256"]


def test_r15_route_closure_binds_cached_routes_and_h04_policy() -> None:
    closure_path = verifier.REPO / "data/research/external/equity_h01_h05_route_closure_2026_09_09/route_closure_receipt.json"
    closure = json.loads(closure_path.read_text())
    assert closure["status"] == "CLOSED_WITH_DATA_AND_PIT_BLOCKERS"
    assert closure["routes"]["sec_numeric_facts"]["facts"] == 8520
    assert closure["routes"]["sec_numeric_facts"]["pit_eligible_facts"] == 2937
    assert closure["routes"]["yahoo_retry"]["status"] == "RETRY_EVIDENCE_CAPTURED_NO_PIT_ADMISSION"
    assert closure["routes"]["yahoo_retry"]["successful_schema_valid_routes"] == 16
    assert closure["routes"]["yahoo_retry"]["source_absence_claim"] is False
    assert closure["routes"]["yahoo_retry"]["source_absence_claim"] is False
    assert closure["h04"]["admitted_rows"] == 19
    assert closure["h04"]["monthly_expansion"].startswith("REJECTED_")
    manifest = json.loads((verifier.REPO / "data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r18.json").read_text())
    assert manifest["public_route_closure_receipt"] == str(closure_path.relative_to(verifier.REPO))
    assert manifest["public_route_closure_receipt_sha256"] == hashlib.sha256(closure_path.read_bytes()).hexdigest()


def test_r18_legacy_yahoo_retry_is_archival_and_current_route_is_not_retry_required() -> None:
    closure_path = verifier.REPO / "data/research/external/equity_h01_h05_route_closure_2026_09_09/route_closure_receipt.json"
    closure = json.loads(closure_path.read_text())
    legacy = closure["routes"]["yahoo_retry_legacy_single_symbol"]
    assert legacy["current"] is False
    assert legacy["superseded_by"] == "yahoo_retry"
    assert legacy["source_absence_claim"] is False
    assert "BLOCKED_RETRY_REQUIRED" not in json.dumps(closure["routes"]["yahoo_retry"])
    assert closure["routes"]["yahoo_retry"]["source_absence_claim"] is False


def test_r18_stage_record_self_hash_and_dependency_pins_are_current() -> None:
    repo = verifier.REPO
    stage_path = repo / "data/research_runs/equity_h01_h05_h14_stage_eligibility_2026_09_09_r18/stage_eligibility_record.json"
    stage = json.loads(stage_path.read_text())
    body = {key: value for key, value in stage.items() if key != "record_sha256"}
    expected_record_sha = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert stage["record_sha256"] == expected_record_sha

    manifest_path = repo / "data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r18.json"
    receipt_path = repo / "data/research/equity_h01_h05_causal_input_gate_2026_09_08_r18/diagnostic_receipt.json"
    report_path = repo / "docs/research/equity_h01_h05_causal_input_gate_2026_09_09_r18.md"
    closure_path = repo / "data/research/external/equity_h01_h05_route_closure_2026_09_09/route_closure_receipt.json"
    assert stage["evidence"]["h01_h05_manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert stage["evidence"]["h01_h05_receipt_sha256"] == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    assert stage["evidence"]["h01_h05_report_sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert stage["evidence"]["h01_h05_public_route_closure_sha256"] == hashlib.sha256(closure_path.read_bytes()).hexdigest()


def test_r18_route_closure_binds_r18_rows_and_rejects_stale_r14_path() -> None:
    repo = verifier.REPO
    closure_path = repo / "data/research/external/equity_h01_h05_route_closure_2026_09_09/route_closure_receipt.json"
    closure = json.loads(closure_path.read_text())
    row_artifact = closure["h04"]["row_artifact"]
    expected_rows = repo / "data/research/equity_h01_h05_causal_input_gate_2026_09_08_r18/causal_input_rows.jsonl"
    assert row_artifact["path"] == str(expected_rows.relative_to(repo))
    assert "_r14/" not in row_artifact["path"]
    assert row_artifact["bytes"] == expected_rows.stat().st_size
    assert row_artifact["sha256"] == hashlib.sha256(expected_rows.read_bytes()).hexdigest()
