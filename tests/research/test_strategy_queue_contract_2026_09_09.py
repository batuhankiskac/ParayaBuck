from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.research import build_strategy_queue_contract_2026_09_09 as contract_builder
from scripts.research import verify_all_34_closure_2026_09_09 as closure

REPO = Path(__file__).parents[2]


def test_frozen_contract_is_exact_and_contains_no_result_status_or_fallback() -> None:
    contract = contract_builder.build_contract(REPO)
    contract_builder.verify_contract(contract, REPO)
    assert contract["candidate_ids"] == closure.EXPECTED_IDS
    serialized = json.dumps(contract, ensure_ascii=False)
    assert "frozen research candidate" not in serialized.lower()
    assert "frozen causal field and portfolio-contribution hypothesis" not in serialized.lower()
    assert "source-bound evidence" not in serialized.lower()
    assert "DATA_BLOCKED" not in serialized
    assert "PIT_PENDING" not in serialized
    assert "RETROSPECTIVE_DIAGNOSTIC_COMPLETE" not in serialized
    assert "NEXT_PUBLIC_PROBE" not in serialized


def test_contract_artifact_and_receipt_are_current_and_self_bound() -> None:
    path = REPO / contract_builder.OUTPUT_DIR / contract_builder.CONTRACT_NAME
    receipt_path = REPO / contract_builder.OUTPUT_DIR / contract_builder.RECEIPT_NAME
    contract = json.loads(path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    contract_builder.verify_contract(contract, REPO)
    assert receipt["contract"] == {"path": str(path.relative_to(REPO)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert receipt["receipt_sha256"] == contract_builder.canonical_hash(receipt, "receipt_sha256")
    assert receipt["source_pins"] == contract["source_pins"]


def test_contract_mutation_fails_without_receipt_repin(tmp_path: Path) -> None:
    output = tmp_path / contract_builder.OUTPUT_DIR
    output.mkdir(parents=True)
    shutil.copy2(REPO / contract_builder.OUTPUT_DIR / contract_builder.CONTRACT_NAME, output / contract_builder.CONTRACT_NAME)
    shutil.copy2(REPO / contract_builder.OUTPUT_DIR / contract_builder.RECEIPT_NAME, output / contract_builder.RECEIPT_NAME)
    contract_path = output / contract_builder.CONTRACT_NAME
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["candidates"]["H01"]["mechanism"] = "mutated"
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="contract_self_hash_mismatch"):
        contract_builder.verify_contract(contract, REPO)


def test_candidate_definition_result_and_integration_contracts_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"cards":{"H01":{"status":"PIT_PENDING"}}}\n', encoding="utf-8")
    definition_path = tmp_path / closure.DEFINITION_SOURCE
    definition_path.parent.mkdir(parents=True)
    definition_path.write_text("# frozen definition\n", encoding="utf-8")
    digest = closure.sha256(source)
    definition_digest = closure.sha256(definition_path)
    candidate = {
        "candidate_id": "H01",
        "source_path": "source.json",
        "pointer": ["cards", "H01"],
        "status": "PIT_PENDING",
        "strategy_name": "Value",
        "purpose_definition": None,
        "purpose_definition_status": closure.UNSPECIFIED,
        "mechanism": "Value",
        "feasibility": "PIT value unavailable",
        "unspecified_fields": {"purpose_definition": None, "field_formula": None, "window": None, "cost_stress": None, "controls": None, "data_requirements": None, "scope": closure.UNSPECIFIED},
        "research_status": "PIT_PENDING",
        "evidence_summary": "rows=0",
        "evidence_counts": {"rows": 0},
        "pit_provenance": {"status": "PIT_PENDING", "source_path": "source.json", "source_sha256": digest, "pointer": ["cards", "H01"]},
        "terminal_decision": {"kind": "blocker", "detail": ["PIT gate remains unmet"]},
        "economic_run": False,
        "eligible": False,
        "admitted": 0,
        "promotion": False,
        "economic_violations": [],
        "definition_source": {"path": closure.DEFINITION_SOURCE, "section": "H01 — Value", "sha256": definition_digest},
        "result_source": {"path": "source.json", "sha256": digest, "pointer": ["cards", "H01"]},
        "integration_candidate": False,
    }
    assert closure.validate_candidate_contract("H01", candidate, tmp_path) == []
    for field, value in (("definition_source", None), ("result_source", "wrong"), ("integration_candidate", True), ("purpose_definition", "invented"), ("evidence_summary", "Source-bound evidence for H01"), ("strategy_name", "H01 frozen research candidate")):
        mutated = dict(candidate)
        mutated[field] = value
        assert closure.validate_candidate_contract("H01", mutated, tmp_path)


def test_h14_stale_untested_status_is_rejected() -> None:
    candidate = {"candidate_id": "H14", "status": "RETROSPECTIVE_DIAGNOSTIC_COMPLETE / PORTFOLIO_CONTRIBUTION_UNTESTED"}
    assert any("stale_or_unverified_tested_infeasible_status" in item for item in closure.validate_candidate_contract("H14", candidate, REPO))
