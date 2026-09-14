from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import scripts.research.r46_treasury_auction_unwind_tlt_v2_2026_09_10 as v2
import scripts.research.r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14 as v3
import scripts.research.verify_r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14 as verifier
from tests.research.test_r46_v2_execution_2026_09_10 import _native_event_fixture


def _canonical(payload: dict[str, Any], field: str) -> str:
    copied = copy.deepcopy(payload)
    copied["integrity"].pop(field, None)
    return hashlib.sha256(
        json.dumps(copied, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _v2_fixture_gate(tmp_path: Path, package: Any) -> tuple[Path, Any]:
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": v2.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")
    state_path = tmp_path / "v2-state.json"
    _write_json(state_path, state)
    manifest: dict[str, Any] = {
        "schema_version": "r46-governance-manifest-v2",
        "research_id": v2.RESEARCH_ID,
        "candidate_id": "R46",
        "evaluation_contract": {
            "one_shot_state_path": state_path.name,
            "one_shot_state_sha256": state["integrity"]["artifact_sha256"],
        },
        "integrity": {"manifest_sha256": ""},
    }
    manifest["integrity"]["manifest_sha256"] = _canonical(manifest, "manifest_sha256")
    manifest_path = tmp_path / "v2-manifest.json"
    _write_json(manifest_path, manifest)
    package_payload: dict[str, Any] = {
        "schema": "r46-treasury-auction-unwind-data-package-v2",
        "research_id": v2.RESEARCH_ID,
        "candidate_id": "R46",
        "integrity": {"manifest_sha256": ""},
    }
    package_payload["integrity"]["manifest_sha256"] = _canonical(package_payload, "manifest_sha256")
    package_path = tmp_path / "v2-package.json"
    _write_json(package_path, package_payload)
    package = replace(package, package_path=package_path, package=package_payload)
    gate: dict[str, Any] = {
        "schema_version": "r46-v2-preflight-receipt-v2",
        "status": v2.PRE_RUN_OK,
        "research_id": v2.RESEARCH_ID,
        "candidate_id": "R46",
        "manifest_path": manifest_path.name,
        "manifest_sha256": manifest["integrity"]["manifest_sha256"],
        "manifest_file_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "package_path": package_path.name,
        "package_sha256": package_payload["integrity"]["manifest_sha256"],
        "package_file_sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
        "source_commit": "1" * 40,
        "freeze_commit": "2" * 40,
        "research_only": True,
        "read_only": True,
        "economic_execution": False,
        "economic_run": False,
        "economic_outcomes_accessed": False,
        "outcome_artifacts_present": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "integration_candidate": False,
        "promotion": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "integrity": {"receipt_sha256": ""},
    }
    gate["integrity"]["receipt_sha256"] = _canonical(gate, "receipt_sha256")
    gate_path = tmp_path / "v2-gate.json"
    _write_json(gate_path, gate)
    return gate_path, package


def _without_provenance(value: object) -> object:
    keys = {
        "schema_version",
        "research_id",
        "candidate_id",
        "recovery_generation",
        "claim_policy",
        "parent_research_id",
        "v3_governance_canonical_sha256",
        "execution_contract_sha256",
        "incident_receipt_canonical_sha256",
        "parent_v2_reservation_physical_sha256",
        "parent_v2_reservation_transition_sha256",
        "parent_v2_governance_canonical_sha256",
        "parent_v2_package_canonical_sha256",
        "manifest_canonical_sha256",
        "package_canonical_sha256",
        "preflight_receipt_sha256",
        "authorization_sha256",
        "governance_manifest_path",
        "execution_contract_path",
        "data_package_manifest_path",
        "preflight_receipt_path",
        "authorization_path",
        "incident_receipt_path",
        "source_commit",
        "freeze_commit",
        "one_shot_state_path",
        "one_shot_state_sha256",
        "v3_state_canonical_sha256",
        "canonical_output_directory",
        "output_directory",
        "output_files",
        "runner_sha256",
        "verifier_sha256",
        "source_sha256",
        "receipt_sha256",
        "ledger_sha256",
    }
    if isinstance(value, dict):
        return {
            key: _without_provenance(child)
            for key, child in value.items()
            if key not in keys and key != "status"
        }
    if isinstance(value, list):
        return [_without_provenance(child) for child in value]
    return value


def test_v2_and_v3_test_only_paths_have_exact_economic_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, incumbent = _native_event_fixture(tmp_path)
    gate_path, package = _v2_fixture_gate(tmp_path, package)
    stats_pins = {
        "statistics_plan_path": "fixture-plan",
        "statistics_plan_sha256": "3" * 64,
        "family_registry_path": "fixture-family",
        "family_registry_sha256": "4" * 64,
        "family_size": 7,
        "reported_output_count": 7,
        "inferential_family_size": 4,
        "inferential_ids": list(v2.INFERENTIAL_STATISTIC_IDS),
        "statistic_ids": list(v2.STATISTIC_IDS),
    }
    monkeypatch.setattr(v2, "load_r46_v2_package", lambda *args, **kwargs: package)
    monkeypatch.setattr(v2, "load_incumbent_pit_inputs", lambda **kwargs: incumbent)
    monkeypatch.setattr(v2, "_manifest_statistics_pins", lambda *args, **kwargs: stats_pins)
    monkeypatch.setattr(
        v2,
        "_verify_persisted_preflight",
        lambda **kwargs: {
            "status": v2.PRE_RUN_OK,
            "preflight_receipt": json.loads(gate_path.read_text(encoding="utf-8")),
            "source_commit": "1" * 40,
            "freeze_commit": "2" * 40,
        },
    )
    v2_result = v2.run_package_backed_test_only(
        gate=gate_path,
        output_dir=tmp_path / "v2-output",
        repo_root=tmp_path,
        incumbent_inputs=incumbent,
        config=None,
    )
    v3_result = v3.run_recovery_test_only(
        package=package,
        incumbent_inputs=incumbent,
        output_dir=tmp_path / "v3-output",
        repo_root=tmp_path,
        one_shot_state_path=tmp_path / "v3-state.json",
        manifest_sha256="a" * 64,
        preflight_receipt_sha256="b" * 64,
        execution_contract_sha256="c" * 64,
        governance_sha256="a" * 64,
        incident_sha256="d" * 64,
        source_commit="1" * 40,
        freeze_commit="2" * 40,
        statistics_pins=stats_pins,
    )
    assert v2_result["status"] == v3_result["status"]
    assert _without_provenance(v2_result["results"]) == _without_provenance(v3_result["results"])
    assert _without_provenance(v2_result["ledger"]) == _without_provenance(v3_result["ledger"])
    assert _without_provenance(v2_result["report"]) == _without_provenance(v3_result["report"])
    verified = verifier.verify_economic_payloads_test_only(
        v3_result["results"], v3_result["ledger"], v3_result["report"]
    )
    assert verified["status"] == "VERIFIED_ECONOMIC_RUN"

    for mutate in (
        lambda payload: payload["ledger"]["cells"][0]["candidate"]["fills"][0].__setitem__(
            "fee", payload["ledger"]["cells"][0]["candidate"]["fills"][0]["fee"] + 1.0
        ),
        lambda payload: payload["ledger"]["cells"][0]["candidate"]["decisions"][0][
            "ownership_attribution"
        ].__setitem__("r46", ["forged"]),
        lambda payload: payload["results"]["statistics"]["primary_estimand"].__setitem__(
            "equal_weight_delta", 123.0
        ),
    ):
        tampered = copy.deepcopy(v3_result)
        mutate(tampered)
        with pytest.raises(ValueError):
            verifier.verify_economic_payloads_test_only(
                tampered["results"], tampered["ledger"], tampered["report"]
            )
