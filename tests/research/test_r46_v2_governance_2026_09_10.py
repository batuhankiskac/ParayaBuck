from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILDER = REPO / "scripts/research/build_r46_v2_governance_2026_09_10.py"
PREFLIGHT = REPO / "scripts/research/r46_v2_preflight_2026_09_10.py"
MANIFEST_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10.json"
STATISTICS_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10_statistics_plan.json"
FAMILY_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10_family_registry.json"
ONE_SHOT_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json"


def _build(root: Path) -> dict[str, Path]:
    result = subprocess.run(
        [sys.executable, str(BUILDER), "--repo-root", str(REPO), "--output-root", str(root)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return {
        "manifest": root / MANIFEST_NAME,
        "statistics": root / STATISTICS_NAME,
        "family": root / FAMILY_NAME,
        "one_shot": root / ONE_SHOT_NAME,
    }


def test_v2_builder_freezes_disclosed_child_contract_without_human_signatures(tmp_path: Path) -> None:
    paths = _build(tmp_path)

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    statistics = json.loads(paths["statistics"].read_text(encoding="utf-8"))
    family = json.loads(paths["family"].read_text(encoding="utf-8"))
    one_shot = json.loads(paths["one_shot"].read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "r46-governance-manifest-v2"
    assert manifest["status"] == "RETROSPECTIVE_ONE_SHOT_DISCLOSED"
    assert manifest["contamination"]["acknowledged"] is True
    assert manifest["contamination"]["confirmatory_claim_forbidden"] is True
    assert manifest["evaluation_contract"]["tuning_forbidden"] is True
    assert manifest["evaluation_contract"]["rerun_forbidden"] is True
    assert manifest["promotion"] is False
    assert manifest["paper_live_untouched"] is True
    assert len(manifest["parent_manifest_sha256"]) == 64
    assert manifest["operator_authorization"]["authorized"] is True
    assert manifest["operator_authorization"]["cryptographic"] is False
    assert manifest["operator_authorization"]["kind"] == "user_scope_authorization"
    assert "sha256" not in manifest["source_provenance"]["independent_software_review"]

    incumbent_dependencies = manifest["source_provenance"]["incumbent_dependencies"]
    assert incumbent_dependencies["historical_cutoff"] == "2026-07-31T23:59:59Z"
    assert set(incumbent_dependencies) >= {
        "bars",
        "lifetimes",
        "corporate_actions",
        "bars_manifest",
        "corporate_actions_manifest",
        "seam",
    }
    for dependency in incumbent_dependencies.values():
        if isinstance(dependency, dict):
            assert dependency["path"]
            assert len(dependency["sha256"]) == 64
            assert dependency["git_blob_sha256"] is None or len(dependency["git_blob_sha256"]) == 64
    for key in ("bars", "corporate_actions", "bars_manifest", "corporate_actions_manifest"):
        dependency = incumbent_dependencies[key]
        assert dependency["byte_size"] > 0
        assert dependency["required_in_source_commit"] is False
        assert dependency["git_blob_sha256"] is None
    for key in ("lifetimes", "seam"):
        dependency = incumbent_dependencies[key]
        assert dependency["byte_size"] > 0
        assert dependency["required_in_source_commit"] is True
        assert dependency["git_blob_sha256"] is None or len(dependency["git_blob_sha256"]) == 64

    assert statistics["schema_version"] == "r46-statistics-plan-v2"
    assert statistics["sample_unit"] == "event_episode"
    assert statistics["primary_estimand"]["estimand"] == "initial_equity_portfolio_contribution"
    assert statistics["primary_estimand"]["horizon"] == "five_open_to_open_intervals"
    assert statistics["resampling"]["bootstrap_seed"] == 4601
    assert statistics["resampling"]["permutation_seed"] == 4602
    assert (
        statistics["resampling"]["permutation_method"]
        == "paired_sign_flip_randomization_under_exchangeability"
    )
    assert statistics["coverage_gates"]["min_event_episodes"] == 24
    assert statistics["approvals"]["statistician"] is None
    assert statistics["approvals"]["operator"] is None

    assert family["schema_version"] == "r46-multiplicity-family-v2"
    assert family["family_size"] == len(family["hypotheses"])
    assert family["variant_inventory_complete"] is True
    assert family["scope"] == "r46_v2_reported_outputs_only"
    assert family["global_prior_search_family_complete"] is False
    assert family["adjusted_p_values_interpretation"] == "descriptive_only"
    assert family["approvals"]["statistician"] is None
    assert family["approvals"]["operator"] is None

    assert one_shot["status"] == "SEALED_UNUSED"
    assert one_shot["prior_access_acknowledged"] is True
    assert one_shot["run_count"] == 0
    assert one_shot["rerun_forbidden"] is True
    assert one_shot["reservation_owner"] == "execution"
    assert one_shot["reservation_state"] == "UNRESERVED"


def test_v2_builder_is_atomic_no_overwrite(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    before = {name: path.read_bytes() for name, path in paths.items()}

    result = subprocess.run(
        [sys.executable, str(BUILDER), "--repo-root", str(REPO), "--output-root", str(tmp_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "no_overwrite" in result.stderr or "FileExistsError" in result.stderr
    assert {name: path.read_bytes() for name, path in paths.items()} == before


def test_v2_builder_preserves_existing_one_shot_state(tmp_path: Path) -> None:
    from scripts.research.build_r46_v2_governance_2026_09_10 import _one_shot_state

    state = _one_shot_state()
    state["operator_owned_marker"] = "must_not_be_reset"
    state["integrity"]["artifact_sha256"] = _canonical_hash(state, "artifact_sha256")
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path = tmp_path / ONE_SHOT_NAME
    state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    before = state_path.read_bytes()

    result = subprocess.run(
        [sys.executable, str(BUILDER), "--repo-root", str(REPO), "--output-root", str(tmp_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert state_path.read_bytes() == before


def test_v2_preflight_reports_explicit_blockers_and_never_emits_economics(tmp_path: Path) -> None:
    paths = _build(tmp_path)

    result = subprocess.run(
        [sys.executable, str(PREFLIGHT), str(paths["manifest"]), "--repo-root", str(tmp_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)

    assert payload["status"] == "BLOCKED"
    assert "data_package_manifest_unreadable" in payload["blockers"]
    assert "SOURCE_COMMIT_PLACEHOLDER" in payload["blockers"]
    assert "STATISTICIAN_APPROVAL_REQUIRED" not in payload["blockers"]
    assert "NATIVE_LEDGER_ATTESTATION_REQUIRED" in payload["blockers"]
    assert payload["economic_execution"] is False
    assert payload["promotion"] is False
    assert payload["paper_live_untouched"] is True

    forbidden = {"returns", "nav", "pnl", "fills", "turnover", "drawdown", "cvar", "sharpe", "p_values"}

    def keys(value: object) -> list[str]:
        if isinstance(value, dict):
            return [key for key, child in value.items() for key in [key, *keys(child)]]
        if isinstance(value, list):
            return [key for child in value for key in keys(child)]
        return []

    assert not forbidden.intersection(key.casefold() for key in keys(payload))


def _canonical_hash(payload: dict[str, object], field: str) -> str:
    value = cast(dict[str, Any], json.loads(json.dumps(payload, ensure_ascii=False)))
    integrity = cast(dict[str, Any], value["integrity"])
    integrity.pop(field, None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_v2_preflight_rejects_a_consumed_one_shot_state(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    state = json.loads(paths["one_shot"].read_text(encoding="utf-8"))
    state["run_count"] = 1
    state["integrity"]["artifact_sha256"] = _canonical_hash(state, "artifact_sha256")
    paths["one_shot"].write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["evaluation_contract"]["one_shot_state_sha256"] = state["integrity"]["artifact_sha256"]
    manifest["integrity"]["manifest_sha256"] = _canonical_hash(manifest, "manifest_sha256")
    paths["manifest"].write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(PREFLIGHT), str(paths["manifest"]), "--repo-root", str(tmp_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "BLOCKED"
    assert "ONE_SHOT_ALREADY_CONSUMED" in payload["blockers"]


def test_v2_preflight_resolves_repo_relative_manifest_argument(tmp_path: Path) -> None:
    paths = _build(tmp_path)

    result = subprocess.run(
        [sys.executable, str(PREFLIGHT), paths["manifest"].name, "--repo-root", str(tmp_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "BLOCKED"
    assert "manifest_unreadable" not in payload["blockers"]


def test_v2_preflight_rejects_v1_data_package_schema(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    package_path = tmp_path / "v1-package.json"
    package_path.write_text(
        json.dumps(
            {
                "schema": "r46-treasury-auction-unwind-data-package-v1",
                "integrity": {"manifest_sha256": ""},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    from scripts.research.r46_v2_preflight_2026_09_10 import _validate_data_package

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    reasons, checked = _validate_data_package(package_path, manifest, paths["manifest"], tmp_path)
    assert checked == 0
    assert "data_package_schema" in reasons


def test_native_attestation_structural_fills_are_not_global_outcome_fields(tmp_path: Path) -> None:
    receipt_path = tmp_path / "native.json"
    receipt_path.write_text(json.dumps({"schema_version": "native", "fills": []}) + "\n", encoding="utf-8")
    from scripts.research.r46_v2_preflight_2026_09_10 import _artifact_contains_forbidden

    assert _artifact_contains_forbidden(receipt_path) is True
    assert _artifact_contains_forbidden(receipt_path, allow_native_fills=True) is False
    receipt_path.write_text(
        json.dumps({"schema_version": "native", "fills": [], "returns": []}) + "\n", encoding="utf-8"
    )
    assert _artifact_contains_forbidden(receipt_path, allow_native_fills=True) is True


def test_preflight_ok_receipt_is_canonical_and_bound_to_both_inputs(tmp_path: Path) -> None:
    from scripts.research.r46_v2_preflight_2026_09_10 import (
        build_preflight_receipt,
        persist_preflight_receipt,
    )

    result: dict[str, Any] = {
        "status": "PRE_RUN_OK",
        "research_id": "r46_treasury_auction_unwind_tlt_v2_2026_09_10",
        "governance_manifest_sha256": "a" * 64,
        "data_package_manifest_sha256": "b" * 64,
        "checked_artifacts": 5,
        "blockers": [],
    }
    result["preflight_receipt"] = build_preflight_receipt(result)
    receipt = cast(dict[str, Any], result["preflight_receipt"])
    assert receipt["status"] == "PRE_RUN_OK"
    assert receipt["governance_manifest_sha256"] == "a" * 64
    assert receipt["data_package_manifest_sha256"] == "b" * 64
    assert receipt["integrity"]["receipt_sha256"] == _canonical_hash(receipt, "receipt_sha256")

    receipt_path = tmp_path / "preflight-receipt.json"
    with pytest.raises(ValueError, match="verified"):
        persist_preflight_receipt(result, receipt_path)


def test_preflight_receipt_persistence_rejects_unverified_forgery(tmp_path: Path) -> None:
    from scripts.research.r46_v2_preflight_2026_09_10 import persist_preflight_receipt

    forged = {
        "status": "PRE_RUN_OK",
        "research_id": "r46_treasury_auction_unwind_tlt_v2_2026_09_10",
        "preflight_receipt": {
            "schema_version": "r46-v2-preflight-receipt-v2",
            "status": "PRE_RUN_OK",
            "research_id": "r46_treasury_auction_unwind_tlt_v2_2026_09_10",
            "candidate_id": "R46",
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
            "manifest_path": "manifest.json",
            "package_path": "package.json",
            "manifest_sha256": "a" * 64,
            "package_sha256": "b" * 64,
            "integrity": {"receipt_sha256": "c" * 64},
        },
    }
    with pytest.raises(ValueError, match="verified"):
        persist_preflight_receipt(forged, tmp_path / "receipt.json")


def test_v2_receipt_schema_matches_runner_and_contains_external_evidence_refs() -> None:
    from scripts.research.r46_v2_preflight_2026_09_10 import PREFLIGHT_RECEIPT_SCHEMA

    assert PREFLIGHT_RECEIPT_SCHEMA == "r46-v2-preflight-receipt-v2"


def test_v2_builder_static_contract_excludes_post_seal_hashes(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    data_contract = manifest["data_contract"]
    execution_contract = manifest["execution_contract"]
    review = manifest["source_provenance"]["independent_software_review"]
    assert "data_package_manifest_sha256" not in data_contract
    assert data_contract.get("pit_source_attestation_sha256") is None
    assert execution_contract.get("native_ledger_attestation_sha256") is None
    assert "sha256" not in review


def test_v2_builder_can_finalize_from_explicit_source_commit(tmp_path: Path) -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--repo-root",
            str(REPO),
            "--output-root",
            str(tmp_path),
            "--source-commit",
            head,
            "--base-commit",
            head,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["source_provenance"]["source_commit"] == head
    assert manifest["integrity"]["manifest_sha256"] == _canonical_hash(manifest, "manifest_sha256")


def test_v2_builder_declares_all_outcome_relevant_source_and_runtime_pins(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    pinned = set(manifest["source_provenance"]["source_files"])
    expected = {
        "scripts/research/acquire_r46_tlt_market_data_2026_09_10.py",
        "scripts/research/acquire_r46_treasury_xml_v2_2026_09_10.py",
        "scripts/research/build_r46_nyse_calendar_2026_09_10.py",
        "scripts/research/build_r46_v2_data_package_2026_09_10.py",
        "scripts/research/build_r46_v2_governance_2026_09_10.py",
        "scripts/research/r46_v2_preflight_2026_09_10.py",
        "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "scripts/research/build_r46_native_ledger_attestation_2026_09_10.py",
        "tests/research/test_r46_v2_execution_2026_09_10.py",
        "tests/research/test_r46_v2_data_package_2026_09_10.py",
        "tests/research/test_r46_v2_governance_2026_09_10.py",
        "tests/research/test_r46_tlt_market_data_2026_09_10.py",
        "tests/research/test_r46_treasury_xml_v2_2026_09_10.py",
        "tests/research/test_r46_nyse_calendar_2026_09_10.py",
        "src/parayabuck/core/models.py",
        "src/parayabuck/data/market_dataset.py",
        "src/parayabuck/portfolio/__init__.py",
        "src/parayabuck/portfolio/base.py",
        "src/parayabuck/portfolio/models.py",
        "src/parayabuck/portfolio/equity_residual_momentum.py",
        "src/parayabuck/portfolio/equity_residual_momentum_v3.py",
    }
    assert expected <= pinned


def test_governance_and_preflight_freeze_one_exact_runner_command() -> None:
    from scripts.research import build_r46_v2_governance_2026_09_10 as governance
    from scripts.research import r46_v2_preflight_2026_09_10 as preflight

    expected = (
        "python scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py "
        "--gate <preflight_receipt> --package <data_package_manifest> "
        "--one-shot-state <one_shot_state> --output-dir <output_dir>"
    )
    assert governance.EXECUTION_COMMAND == expected
    assert preflight.EXECUTION_COMMAND == expected


def test_preflight_freeze_diff_rejects_runtime_source_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from scripts.research import r46_v2_preflight_2026_09_10 as preflight

    monkeypatch.setattr(
        preflight,
        "_git_diff_paths",
        lambda root, source_commit, freeze_commit: [
            "src/parayabuck/portfolio/base.py",
            "data/research_receipts/r46_fixture.json",
        ],
    )
    reasons = preflight._validate_source_to_freeze_diff(tmp_path, "a" * 40, "b" * 40)
    assert "SOURCE_FREEZE_DIFF_FORBIDDEN:src/parayabuck/portfolio/base.py" in reasons
    assert not any("r46_fixture.json" in reason for reason in reasons)


def test_v2_builder_accepts_deterministic_source_overrides(tmp_path: Path) -> None:
    overrides_path = tmp_path / "source-overrides.json"
    overrides_path.write_text(
        json.dumps({"environment": {"command": "pinned-test-command"}}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--repo-root",
            str(REPO),
            "--output-root",
            str(tmp_path / "bundle"),
            "--source-overrides",
            str(overrides_path),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    manifest = json.loads((tmp_path / "bundle" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["source_provenance"]["environment"]["command"] == "pinned-test-command"
    assert manifest["integrity"]["manifest_sha256"] == _canonical_hash(manifest, "manifest_sha256")


def test_family_registry_requires_exact_method_order_and_roles(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    family = json.loads(paths["family"].read_text(encoding="utf-8"))
    family["method"] = "arbitrary_method"
    family["ordering"] = "input_order"
    family["hypotheses"][0]["role"] = "secondary"
    family["integrity"]["artifact_sha256"] = _canonical_hash(family, "artifact_sha256")
    paths["family"].write_text(json.dumps(family, sort_keys=True) + "\n", encoding="utf-8")
    from scripts.research.r46_v2_preflight_2026_09_10 import _validate_family

    reasons = _validate_family(paths["family"], manifest)
    assert "FAMILY_REGISTRY_METHOD_INVALID" in reasons
    assert "FAMILY_REGISTRY_ORDERING_INVALID" in reasons
    assert "FAMILY_REGISTRY_ROLES_INVALID" in reasons


def test_family_registry_separates_reported_outputs_from_inferential_holm_scope(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    family = json.loads(paths["family"].read_text(encoding="utf-8"))
    assert family["reported_output_count"] == 7
    assert family["inferential_family_size"] == 4
    assert family["inferential_ids"] == [
        "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL",
        "R46_INCUMBENT_COMPARISON",
        "R46_COST_2X",
        "R46_COST_3X",
    ]


def test_arbitrary_pit_receipt_is_not_a_validated_source_attestation(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    receipt_path = tmp_path / "pit.json"
    receipt: dict[str, Any] = {
        "schema_version": "arbitrary-pit-schema",
        "research_id": manifest["research_id"],
        "candidate_id": "R46",
        "governance_manifest_sha256": manifest["integrity"]["manifest_sha256"],
        "status": "READY_PIT_SOURCE",
        "self_hash": "",
    }
    receipt["self_hash"] = _top_level_hash(receipt, "self_hash")
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    from scripts.research.r46_v2_preflight_2026_09_10 import _validate_receipt_reference

    reasons = _validate_receipt_reference(
        tmp_path,
        {
            "path": "pit.json",
            "sha256": _sha256(receipt_path),
            "schema_version": "arbitrary-pit-schema",
            "self_hash_field": "self_hash",
            "self_hash": receipt["self_hash"],
        },
        expected_path="pit.json",
        kind="PIT_SOURCE",
        manifest=manifest,
    )
    assert "PIT_SOURCE_ATTESTATION_SCHEMA" in reasons


def test_parent_execution_contract_is_immutable(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["execution_contract"]["max_single_weight"] = 0.99
    parent_path = tmp_path / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json"
    parent_path.parent.mkdir(parents=True, exist_ok=True)
    parent_path.write_bytes(
        (REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json").read_bytes()
    )
    from scripts.research.r46_v2_preflight_2026_09_10 import _manifest_blockers

    reasons = _manifest_blockers(manifest, paths["manifest"], tmp_path)
    assert "V2_PROTOCOL_DRIFT:execution_contract" in reasons


def test_static_evidence_paths_are_crosslinked(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["evidence_contract"]["package_path"] = "other-package.json"
    from scripts.research.r46_v2_preflight_2026_09_10 import _manifest_blockers

    reasons = _manifest_blockers(manifest, paths["manifest"], tmp_path)
    assert "EVIDENCE_PACKAGE_PATH_CROSSLINK" in reasons


def _top_level_hash(payload: dict[str, object], field: str) -> str:
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    value.pop(field, None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_acquisition_parity_is_recomputed_and_tampering_blocks(tmp_path: Path) -> None:
    from scripts.research.r46_v2_preflight_2026_09_10 import _validate_acquisition_receipt

    raw_path = tmp_path / "raw.csv"
    split_path = tmp_path / "split.csv"
    csv_value = "permanent_security_identifier,session_date,regular_open_timestamp\nalpaca:asset-tlt,2024-01-02,2024-01-02T14:30:00Z\n"
    raw_path.write_text(csv_value, encoding="utf-8")
    split_path.write_text(csv_value.replace("2024-01-02T14:30:00Z", "2024-01-03T14:30:00Z"), encoding="utf-8")
    receipt: dict[str, Any] = {
        "schema": "r46-tlt-market-data-acquisition-v2",
        "research_id": "r46_treasury_auction_unwind_tlt_v2_2026_09_10",
        "candidate_id": "R46",
        "status": "COMPLETE",
        "acquisition_mode": "retrospective",
        "retrospective_acquisition": True,
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "economic_execution": False,
        "raw_split_key_parity": True,
        "artifacts": {
            "raw_bars": {"path": "raw.csv", "sha256": _sha256(raw_path)},
            "split_bars": {"path": "split.csv", "sha256": _sha256(split_path)},
        },
        "integrity": {"receipt_sha256": ""},
    }
    receipt["integrity"]["receipt_sha256"] = _canonical_hash(receipt, "receipt_sha256")
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    package: dict[str, Any] = {
        "acquisition": {
            "receipt_path": "receipt.json",
            "receipt_sha256": _sha256(receipt_path),
            "raw_split_key_parity": True,
        }
    }
    reasons = _validate_acquisition_receipt(receipt_path, package, tmp_path)
    assert "PIT_SOURCE_RAW_SPLIT_KEY_PARITY" in reasons


def test_source_provenance_rejects_head_ancestry_and_runtime_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.research.r46_v2_preflight_2026_09_10 as preflight

    monkeypatch.setattr(preflight, "_git_commit_exists", lambda *_args: True)
    monkeypatch.setattr(preflight, "_git_head", lambda _root: "b" * 40)
    monkeypatch.setattr(preflight, "_git_is_ancestor", lambda *_args: False)
    monkeypatch.setattr(preflight, "_git_worktree_clean", lambda _root: True)
    source = {
        "base_commit": "a" * 40,
        "source_commit": "a" * 40,
        "clean_tree_required": True,
        "clean_tree_observed": True,
        "source_files": {},
        "environment": {
            "network": "disabled",
            "python": "wrong-python",
            "platform": "wrong-platform",
            "timezone": "wrong-timezone",
            "locale": "wrong-locale",
            "command": "wrong-command",
            "dependency_lock_path": "missing.lock",
            "dependency_lock_sha256": "a" * 64,
            "dependency_lock_git_blob_sha256": "b" * 64,
        },
    }
    reasons = preflight._validate_source_provenance(source, Path.cwd(), freeze_commit="c" * 40)
    assert "FREEZE_COMMIT_NOT_HEAD" in reasons
    assert "SOURCE_COMMIT_NOT_ANCESTOR_OF_FREEZE" in reasons
    assert "ENVIRONMENT_PYTHON_MISMATCH" in reasons
    assert "ENVIRONMENT_COMMAND_MISMATCH" in reasons


def test_static_governance_hash_does_not_change_when_external_evidence_is_bound(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    static_hash = manifest["integrity"]["manifest_sha256"]
    package = {
        "schema": "r46-treasury-auction-unwind-data-package-v2",
        "governance_manifest_path": MANIFEST_NAME,
        "governance_manifest_sha256": static_hash,
        "integrity": {"manifest_sha256": ""},
    }
    package["integrity"]["manifest_sha256"] = _canonical_hash(package, "manifest_sha256")
    assert manifest["integrity"]["manifest_sha256"] == _canonical_hash(manifest, "manifest_sha256")
    assert package["governance_manifest_sha256"] == static_hash
    assert "data_package_manifest_sha256" not in manifest["data_contract"]


def test_preflight_receipt_round_trips_through_runner_reader(tmp_path: Path) -> None:
    manifest: dict[str, Any] = {
        "schema_version": "r46-governance-manifest-v2",
        "research_id": "r46_treasury_auction_unwind_tlt_v2_2026_09_10",
        "candidate_id": "R46",
        "integrity": {"manifest_sha256": ""},
    }
    package: dict[str, Any] = {
        "schema": "r46-treasury-auction-unwind-data-package-v2",
        "research_id": manifest["research_id"],
        "candidate_id": "R46",
        "integrity": {"manifest_sha256": ""},
    }
    manifest["integrity"]["manifest_sha256"] = _canonical_hash(manifest, "manifest_sha256")
    package["integrity"]["manifest_sha256"] = _canonical_hash(package, "manifest_sha256")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps(package, sort_keys=True) + "\n", encoding="utf-8")
    from scripts.research.r46_v2_preflight_2026_09_10 import build_preflight_receipt

    result: dict[str, Any] = {
        "status": "PRE_RUN_OK",
        "research_id": manifest["research_id"],
        "governance_manifest_path": "manifest.json",
        "governance_manifest_sha256": manifest["integrity"]["manifest_sha256"],
        "governance_manifest_file_sha256": _sha256(tmp_path / "manifest.json"),
        "data_package_manifest_path": "package.json",
        "data_package_manifest_sha256": package["integrity"]["manifest_sha256"],
        "data_package_manifest_file_sha256": _sha256(tmp_path / "package.json"),
        "checked_artifacts": 5,
        "blockers": [],
    }
    receipt = build_preflight_receipt(result)
    receipt_path = tmp_path / "preflight.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    from scripts.research.r46_treasury_auction_unwind_tlt_v2_2026_09_10 import _validate_pre_run_receipt

    assert _validate_pre_run_receipt(receipt_path)["schema_version"] == "r46-v2-preflight-receipt-v2"


def test_v2_builder_freezes_uniform_publication_policy_and_tracked_universe(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    policy = manifest["publication_availability_policy"]

    assert policy["result_availability_policy"] == (
        "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
    )
    assert policy["result_signal_eligibility"] == "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
    assert policy["same_day_signal_allowed"] is False
    assert policy["actual_release_time_role"] == "evidence_only"
    assert policy["server_last_modified_is_publication_proof"] is False
    assert policy["expected_event_count"] == 127
    assert len(policy["expected_event_ids"]) == 127
    assert policy["expected_event_ids"] == sorted(set(policy["expected_event_ids"]))
    assert policy["expected_event_ids_sha256"] == policy["expected_event_ids_digest"]
    assert len(policy["expected_event_ids_sha256"]) == 64
    assert policy["missing_release"]["expected_count"] == 15
    assert len(policy["missing_release"]["expected_event_ids"]) == 15
    assert policy["missing_release"]["expected_event_ids"] == sorted(policy["missing_release"]["expected_event_ids"])
    assert policy["missing_release"]["expected_event_ids_sha256"] == policy["missing_release"]["expected_event_ids_digest"]
    assert policy["missing_release"]["derivation"] == "normalized_result_rows_published_at_null"
    assert policy["source_evidence"]["path"] == "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json"


def test_v2_schema_closes_uniform_publication_policy_block() -> None:
    from scripts.research.build_r46_v2_governance_2026_09_10 import _schema

    policy_schema = _schema()["properties"]["publication_availability_policy"]
    assert set(policy_schema["required"]) >= {
        "result_availability_policy",
        "result_signal_eligibility",
        "same_day_signal_allowed",
        "actual_release_time_role",
        "server_last_modified_is_publication_proof",
        "expected_event_count",
        "expected_event_ids",
        "expected_event_ids_sha256",
        "missing_release",
    }
    assert policy_schema["additionalProperties"] is False
    assert policy_schema["properties"]["same_day_signal_allowed"] == {"const": False}
    assert policy_schema["properties"]["actual_release_time_role"] == {"const": "evidence_only"}
    assert policy_schema["properties"]["server_last_modified_is_publication_proof"] == {"const": False}
    assert policy_schema["properties"]["missing_release"]["additionalProperties"] is False


def test_v2_builder_pins_v1_raw_bytes_and_event_contract_independently(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    preservation = manifest["v1_contract_preservation"]
    assert preservation["manifest_raw_sha256"] == hashlib.sha256(
        (REPO / preservation["manifest_path"]).read_bytes()
    ).hexdigest()
    assert preservation["schema_raw_sha256"] == hashlib.sha256(
        (REPO / preservation["schema_path"]).read_bytes()
    ).hexdigest()
    assert preservation["source_matrix_raw_sha256"] == hashlib.sha256(
        (REPO / preservation["source_matrix_path"]).read_bytes()
    ).hexdigest()
    assert preservation["holdout_contract_raw_sha256"] == hashlib.sha256(
        (REPO / preservation["holdout_contract_path"]).read_bytes()
    ).hexdigest()
    assert len(preservation["event_contract_canonical_sha256"]) == 64
    assert preservation["allowed_v2_overlay"] == ["publication_availability_policy"]


def test_v2_preflight_reads_v1_parent_when_contract_paths_are_repo_relative(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    for relative in (
        "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json",
        "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.schema.json",
        "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_source_matrix.json",
        "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_holdout_custody_contract.json",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, target)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    from scripts.research.r46_v2_preflight_2026_09_10 import _validate_v1_contract_preservation

    reasons = _validate_v1_contract_preservation(manifest, tmp_path)
    assert "V1_CONTRACT_PARENT_UNREADABLE" not in reasons


def test_v2_preflight_rejects_uniform_policy_spoof_even_after_manifest_rehash(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["publication_availability_policy"]["same_day_signal_allowed"] = True
    manifest["integrity"]["manifest_sha256"] = _canonical_hash(manifest, "manifest_sha256")

    from scripts.research.r46_v2_preflight_2026_09_10 import _manifest_blockers

    reasons = _manifest_blockers(manifest, paths["manifest"], tmp_path)
    assert "PUBLICATION_POLICY_MISMATCH:same_day_signal_allowed" in reasons


def test_v2_preflight_recomputes_tracked_event_universe_not_caller_ids(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    receipt_path = tmp_path / "data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json", receipt_path)
    evidence_path = tmp_path / "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json"
    shutil.copy2(
        REPO / "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json",
        evidence_path,
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    policy = manifest["publication_availability_policy"]
    policy["expected_event_ids"] = list(policy["expected_event_ids"][:-1]) + ["caller-forged-id"]
    policy["expected_event_ids_sha256"] = "0" * 64
    policy["expected_event_ids_digest"] = "0" * 64
    manifest["integrity"]["manifest_sha256"] = _canonical_hash(manifest, "manifest_sha256")

    from scripts.research.r46_v2_preflight_2026_09_10 import _manifest_blockers

    reasons = _manifest_blockers(manifest, paths["manifest"], tmp_path)
    assert "PUBLICATION_POLICY_EXPECTED_EVENT_IDS_MISMATCH" in reasons


def _strict_one_shot_chain_fixture(tmp_path: Path) -> dict[str, object]:
    from scripts.research import build_r46_v2_governance_2026_09_10 as governance_builder
    from scripts.research import r46_treasury_auction_unwind_tlt_v2_2026_09_10 as runner

    state_path = tmp_path / "state" / "one-shot.json"
    state_path.parent.mkdir(parents=True)
    state = governance_builder._one_shot_state()
    state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")

    gate_hash = "a" * 64
    manifest_hash = "b" * 64
    package_hash = "c" * 64
    bundle_hash = "d" * 64
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    receipt_path = output_dir / "run_receipt.json"
    runner.reserve_one_shot(
        state_path,
        gate_sha256=gate_hash,
        manifest_sha256=manifest_hash,
        package_sha256=package_hash,
    )
    runner.consume_one_shot(
        state_path,
        bundle_hash,
        gate_sha256=gate_hash,
        manifest_sha256=manifest_hash,
        package_sha256=package_hash,
        output_path=output_dir,
        receipt_path=receipt_path,
    )

    state_reference = state_path.relative_to(tmp_path).as_posix()
    state_raw_hash = hashlib.sha256(state_path.read_bytes()).hexdigest()
    state_canonical_hash = str(state["integrity"]["artifact_sha256"])
    manifest = {
        "evaluation_contract": {
            "one_shot_state_path": state_reference,
            "one_shot_state_sha256": state_canonical_hash,
        },
        "integrity": {"one_shot_state_path": state_reference},
    }
    results = {"one_shot_state_path": state_reference, "one_shot_state_sha256": state_raw_hash}
    ledger = {"one_shot_state_path": state_reference, "one_shot_state_sha256": state_raw_hash}
    receipt = {
        "one_shot": {
            "state_path": state_reference,
            "status": "CONSUMED",
            "output_sha256": bundle_hash,
        }
    }
    return {
        "root": tmp_path,
        "manifest": manifest,
        "results": results,
        "ledger": ledger,
        "receipt": receipt,
        "output_dir": output_dir,
        "receipt_path": receipt_path,
        "bundle_hash": bundle_hash,
        "gate_hash": gate_hash,
        "manifest_hash": manifest_hash,
        "package_hash": package_hash,
        "state_path": state_path,
    }


def _verify_strict_one_shot_chain(fixture: dict[str, object]) -> None:
    from scripts.research import verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 as verifier

    verifier._strict_one_shot_chain(
        root=cast(Path, fixture["root"]),
        manifest=cast(dict[str, object], fixture["manifest"]),
        results=cast(dict[str, object], fixture["results"]),
        ledger=cast(dict[str, object], fixture["ledger"]),
        receipt=cast(dict[str, object], fixture["receipt"]),
        output_dir=cast(Path, fixture["output_dir"]),
        receipt_path=cast(Path, fixture["receipt_path"]),
        bundle_hash=cast(str, fixture["bundle_hash"]),
        gate_hash=cast(str, fixture["gate_hash"]),
        manifest_hash=cast(str, fixture["manifest_hash"]),
        package_hash=cast(str, fixture["package_hash"]),
    )


@pytest.mark.parametrize(
    ("suffix", "field"),
    (
        ("reserved", "one_shot_reserved_transition"),
        ("consumed", "one_shot_consumed_transition"),
    ),
)
def test_strict_one_shot_chain_rejects_external_valid_transition_symlink(
    tmp_path: Path, suffix: str, field: str
) -> None:
    fixture = _strict_one_shot_chain_fixture(tmp_path)
    state_path = cast(Path, fixture["state_path"])
    transition_path = state_path.with_name(f"{state_path.name}.{suffix}.json")
    external_path = tmp_path.parent / f"{tmp_path.name}-{suffix}-transition.json"
    external_path.write_bytes(transition_path.read_bytes())
    transition_path.unlink()
    transition_path.symlink_to(external_path)
    try:
        with pytest.raises(ValueError, match=rf"strict_{field}_symlink"):
            _verify_strict_one_shot_chain(fixture)
    finally:
        external_path.unlink()


def test_strict_contained_file_rejects_parent_component_symlink(tmp_path: Path) -> None:
    from scripts.research import verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 as verifier

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "transition.json").write_text("{}\n", encoding="utf-8")
    alias_dir = tmp_path / "alias"
    alias_dir.symlink_to(target_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="strict_transition_symlink"):
        verifier._strict_contained_file(tmp_path, "alias/transition.json", "transition", relative_only=True)
