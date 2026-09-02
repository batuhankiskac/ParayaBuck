#!/usr/bin/env python3
"""Run the fail-closed, research-only M3 DATA GATE.

This verifier consumes only local, explicitly supplied bytes. It never
contacts a provider, imports the broker/runtime, runs a portfolio engine, or
calculates economic metrics. Missing data and every unexecuted mandatory
phase are represented as blockers, never as an implicit pass.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from m3_data_gate_helpers import (  # noqa: E402
    CANONICAL_COLUMNS,
    CANONICAL_TIMEFRAME,
    DEFAULT_NUMERICAL_THRESHOLDS,
    EVALUATION_END,
    EXCHANGE_TIMEZONE,
    MATRIX_ENCODING_VERSION,
    NORMALIZER_VERSION,
    RAW_SOURCE_TIMEFRAME,
    REFERENCE_SYMBOLS,
    SOURCE_ADJUSTMENT,
    SOURCE_API_FAMILY_VERSION,
    SOURCE_END,
    SOURCE_ENDPOINT,
    SOURCE_FEED,
    SOURCE_PROVIDER,
    SOURCE_SORT,
    SOURCE_START,
    GateError,
    audit_design_matrix,
    build_reference_inventory,
    build_reference_overlay,
    calendar_dependency_identity,
    candidate_symbols_for_decision,
    canonical_digest,
    common_session_report,
    composite_dataset_identity,
    config_semantic_digest,
    deterministic_candidate_population,
    exact_m3_close_panel,
    expected_exchange_sessions,
    forbidden_receipt_keys,
    load_csv_reference_rows,
    merge_normalized_rows,
    merkle_root,
    normalize_csv_row,
    reference_panel_digest,
    session_dates_digest,
    stage_file,
    validate_iwm_bundle,
    validate_pit_reference_boundary,
    weekly_decision_coverage,
    write_normalized_sidecar,
)

EXIT_BLOCKED = 20
EXIT_CONTRACT_ERROR = 21
VERIFIER_PATH = Path(__file__).relative_to(_REPO).as_posix()
NORMALIZER_PATH = "scripts/m3_data_gate_helpers.py"
R2_MANIFEST_PATH = "data/research_manifests/equity_m3_data_gate_2026_09_r2.json"
R3_MANIFEST_PATH = "data/research_manifests/equity_m3_data_gate_2026_09_r3.json"
BASE_COMMIT = "69fc1e11606dec5f5e84b2da68a0bb24fae81fbd"
R3_BASE_COMMIT = "99fa950a3223a8b6cc78c8a92f8fa20aff52b350"
R3_B3_COMMIT = "c4022ae94aa4069893052f63c1700d9d73d2c399"
R3_A3_COMMIT = "f56062b532b15891b6a88eeb4136ddb55ded1be3"
R3_MISSION = "equity_m3_data_gate_2026_09_r3"
R3_RECOVERY_MODE = "A4_B4_CORRECTIVE_AFTER_ABANDONED_A3_B3"
GATE_PASS = "PASS"
GATE_FAIL = "FAIL"
GATE_UNKNOWN = "UNKNOWN"
GATE_NOT_RUN = "NOT_RUN_BLOCKED"
GATE_STATES = frozenset({GATE_PASS, GATE_FAIL, GATE_UNKNOWN, GATE_NOT_RUN})
MANDATORY_GATES = (
    "commit_topology",
    "implementation_blob_identity",
    "clean_worktree",
    "configuration_identity",
    "dependency_identity",
    "canonical_artifact_identity",
    "lifetime_identity",
    "corporate_action_identity",
    "iwm_raw_bundle",
    "iwm_provider_contract",
    "iwm_pagination_chain",
    "iwm_normalization",
    "reference_calendar",
    "reference_row_quality",
    "reference_common_sessions",
    "weekly_coverage",
    "composite_dataset_identity",
    "deterministic_numerical_population",
    "design_matrix_feasibility",
    "reference_role_contract",
    "receipt_forbidden_fields",
)
REQUIRED_FOR_DATA_PASS = MANDATORY_GATES
CRITICAL_GIT_PATHS = (
    "scripts/m3_data_gate_helpers.py",
    "scripts/research_equity_m3_data_gate.py",
    "tests/test_research_equity_m3_data_gate.py",
    ".github/workflows/ci.yml",
)
R3_CRITICAL_GIT_PATHS = (
    ".github/workflows/ci.yml",
    "scripts/m3_data_gate_helpers.py",
    "scripts/research_equity_m3_data_gate.py",
    "scripts/research_equity_m3_m4.py",
    "scripts/acquire_m3_iwm_raw_bundle.py",
    "scripts/build_m3_pit_scanner_universe.py",
    "tests/test_research_equity_m3_data_gate.py",
    "tests/test_m3_iwm_acquisition.py",
    "tests/test_m3_pit_scanner_universe.py",
)
R3_MANDATORY_GATES = (
    "commit_topology",
    "implementation_blob_identity",
    "manifest_only_commit",
    "clean_worktree_at_start",
    "configuration_identity",
    "dependency_identity",
    "acquisition_script_identity",
    "canonical_artifact_identity",
    "lifetime_identity",
    "corporate_action_identity",
    "iwm_raw_bundle",
    "iwm_provider_contract",
    "iwm_pagination_chain",
    "iwm_normalization",
    "iwm_source_completeness",
    "reference_calendar",
    "reference_row_quality",
    "reference_common_sessions",
    "full_weekly_post_warmup_coverage",
    "recent_weekly_coverage",
    "composite_dataset_identity",
    "deterministic_numerical_population",
    "pit_reference_boundary",
    "design_matrix_feasibility",
    "reference_role_contract",
    "receipt_forbidden_fields",
)
R3_REQUIRED_FOR_DATA_PASS = R3_MANDATORY_GATES
R3_PIT_MANIFEST_PATH = "data/research/generated/m3_data_gate_r3/pit_population_manifest.json"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _run_git(*args: str, repo: Path = _REPO) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _run_git_bytes(*args: str, repo: Path = _REPO) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return result.stdout


def _git_blob_id(commit: str, path: str, *, repo: Path = _REPO) -> str:
    return _run_git("rev-parse", f"{commit}:{path}", repo=repo)


def _git_blob_bytes(commit: str, path: str, *, repo: Path = _REPO) -> bytes:
    return _run_git_bytes("cat-file", "blob", f"{commit}:{path}", repo=repo)


def _git_tree_id(commit: str, *, repo: Path = _REPO) -> str:
    return _run_git("rev-parse", f"{commit}^{{tree}}", repo=repo)


def _git_parent(commit: str, *, repo: Path = _REPO) -> str:
    return _run_git("rev-parse", f"{commit}^", repo=repo)


def _git_changed_paths(commit: str, *, repo: Path = _REPO) -> list[str]:
    output = _run_git("diff-tree", "--no-commit-id", "--name-only", "-r", commit, repo=repo)
    return [line for line in output.splitlines() if line]


def _dirty_paths() -> list[str]:
    output = _run_git("status", "--short", "--untracked-files=all")
    return [line for line in output.splitlines() if line]


def _logical_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO.resolve()).as_posix()
    except ValueError:
        return f"<external:{path.name}>"


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError("invalid_json", f"invalid JSON artifact: {path.name}") from exc
    if not isinstance(payload, Mapping):
        raise GateError("invalid_json", f"JSON artifact must be an object: {path.name}")
    return payload


def _load_yaml(path: Path) -> Mapping[str, object]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise GateError("invalid_config", f"invalid YAML config: {path.name}") from exc
    if not isinstance(payload, Mapping):
        raise GateError("invalid_config", "settings YAML must be an object")
    return payload


def _safe(value: object) -> object:
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, Mapping):
        return {str(key): _safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(child) for child in value]
    return value


def _manifest_expected_hash(manifest: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = manifest.get(key)
        if isinstance(value, str) and len(value) == 64:
            return value
    return None


def _artifact_receipt(artifact: object, *, logical_id: str, provenance_class: str) -> dict[str, object]:
    if not hasattr(artifact, "receipt"):
        return {"logical_artifact_id": logical_id, "status": GATE_UNKNOWN}
    return artifact.receipt(logical_id, provenance_class)  # type: ignore[no-any-return,union-attr]


def _compact_dates(values: object) -> dict[str, object]:
    if not isinstance(values, list):
        return {"count": 0, "sha256": session_dates_digest(())}
    ordered = [str(value) for value in values]
    return {
        "count": len(ordered),
        "sha256": session_dates_digest(ordered),
        "first": ordered[0] if ordered else None,
        "last": ordered[-1] if ordered else None,
        "boundary_sample": ordered[:3] + (ordered[-3:] if len(ordered) > 3 else []),
    }


def _compact_inventory(inventory: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    compact: dict[str, object] = {}
    for symbol, item in inventory.items():
        copy = dict(item)
        for field in ("missing_expected_sessions", "extra_off_calendar_sessions"):
            copy[field] = _compact_dates(copy.get(field))
        compact[symbol] = copy
    return compact


def _compact_common(common: Mapping[str, object]) -> dict[str, object]:
    compact = dict(common)
    for field in ("missing_by_reference", "extra_by_reference"):
        values = compact.get(field)
        if isinstance(values, Mapping):
            compact[field] = {str(symbol): _compact_dates(dates) for symbol, dates in values.items()}
    compact.pop("common_sessions", None)
    return compact


def _compact_weekly(weekly: Mapping[str, object]) -> dict[str, object]:
    compact = {key: value for key, value in weekly.items() if key != "decisions"}
    decisions = weekly.get("decisions")
    if isinstance(decisions, list):
        compact["decision_digest_sha256"] = canonical_digest(decisions)
        compact["state_counts"] = {
            state: sum(item.get("state") == state for item in decisions if isinstance(item, Mapping))
            for state in (
                "INITIAL_WARMUP",
                "ACCEPTED",
                "REFERENCE_OUTAGE",
                "STALE_REFERENCE",
                "MISALIGNED_REFERENCE",
                "INSUFFICIENT_COMMON_WINDOW",
                "NO_FEASIBLE_REFERENCE_WINDOW",
            )
        }
        compact["first_decision_session"] = decisions[0].get("decision_session") if decisions else None
        compact["last_decision_session"] = decisions[-1].get("decision_session") if decisions else None
    return compact


def _compact_iwm_audit(audit: Mapping[str, object]) -> dict[str, object]:
    compact = dict(audit)
    pages = compact.pop("pages", None)
    if isinstance(pages, list):
        compact["page_count"] = len(pages)
        compact["page_chain_digest_sha256"] = canonical_digest(pages)
        compact["first_page"] = pages[0] if pages else None
        compact["last_page"] = pages[-1] if pages else None
    return compact


def _gate_map(gate_names: Sequence[str] = MANDATORY_GATES) -> dict[str, dict[str, object]]:
    return {name: {"status": GATE_UNKNOWN, "reason": "not evaluated"} for name in gate_names}


def _set_gate(
    gates: dict[str, dict[str, object]], name: str, status: str, *, reason: str | None = None, **evidence: object
) -> None:
    if name not in gates:
        raise KeyError(name)
    if status not in GATE_STATES:
        raise ValueError(f"unsupported gate state: {status}")
    value: dict[str, object] = {"status": status}
    if reason:
        value["reason"] = reason
    value.update(evidence)
    gates[name] = value


def evaluate_mandatory_gates(
    gates: Mapping[str, Mapping[str, object]], required_gates: Sequence[str] | None = None
) -> str:
    """Return PASS only when every frozen mandatory field is exactly PASS."""
    required = tuple(required_gates or REQUIRED_FOR_DATA_PASS)
    if any(gates.get(name, {}).get("status") != GATE_PASS for name in required):
        return "M3_DATA_GATE_BLOCKED"
    return "M3_DATA_GATE_PASS"


def _load_committed_contract(path: Path, *, run_commit: str) -> tuple[Mapping[str, object], str, bytes]:
    logical = _logical_path(path)
    if logical not in {R2_MANIFEST_PATH, R3_MANIFEST_PATH}:
        raise GateError("manifest_path_mismatch", "gate must use the pinned R2 or R3 manifest path")
    try:
        committed = _git_blob_bytes(run_commit, logical)
        blob_id = _git_blob_id(run_commit, logical)
    except subprocess.CalledProcessError as exc:
        raise GateError("manifest_not_committed", "data-gate manifest is not present in the run commit") from exc
    try:
        working = path.read_bytes()
    except OSError as exc:
        raise GateError("manifest_missing", "data-gate manifest working copy is missing") from exc
    if working != committed:
        raise GateError("mutable_manifest", "working data-gate manifest differs from the committed HEAD blob")
    try:
        payload = json.loads(committed.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError("invalid_manifest", "committed data-gate manifest is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise GateError("invalid_manifest", "committed data-gate manifest must be an object")
    return payload, blob_id, committed


def _validate_r3_contract(contract: Mapping[str, object], *, run_parent: str) -> None:
    """Validate only the predeclared R3 contract; results are never trusted here."""
    if contract.get("schema_version") != "3.0.0":
        raise GateError("manifest_schema_mismatch", "R3 manifest schema is not 3.0.0")
    if contract.get("mission") != R3_MISSION:
        raise GateError("manifest_mission_mismatch", "R3 manifest mission is incorrect")
    if contract.get("repository_url") != "https://github.com/batuhankiskac/ParayaBuck":
        raise GateError("manifest_repository_mismatch", "R3 manifest repository is incorrect")
    if contract.get("branch") != "main" or contract.get("snapshot_date") != "2026-09-01":
        raise GateError("manifest_snapshot_mismatch", "R3 manifest branch or snapshot date drifted")
    if contract.get("declared_before_results") is not True:
        raise GateError("manifest_not_predeclared", "R3 manifest must be predeclared")
    for key, expected in (
        ("research_only", True),
        ("automatic_promotion", False),
        ("human_promotion_only", True),
        ("candidate_economics_run", False),
        ("economic_metrics_present", False),
        ("economic_mission_ready", False),
    ):
        if contract.get(key) != expected:
            raise GateError("manifest_policy_mismatch", f"R3 manifest {key} drifted")
    recovery = contract.get("recovery_lineage")
    if recovery is None:
        expected_base = R3_BASE_COMMIT
        expected_implementation_parent = R3_BASE_COMMIT
    else:
        expected_recovery = {
            "status": "ABANDONED_A3_B3",
            "abandoned_implementation_commit": R3_A3_COMMIT,
            "abandoned_manifest_commit": R3_B3_COMMIT,
            "reason_category": "response_schema_mismatch",
            "reason": "A3 rejected Alpaca v2 bars because the symbol is carried by the bars map key, not the bar object",
            "corrective_base_commit": R3_B3_COMMIT,
        }
        if contract.get("lineage_mode") != R3_RECOVERY_MODE or recovery != expected_recovery:
            raise GateError("recovery_lineage_mismatch", "R3 corrective recovery lineage is not declared exactly")
        expected_base = R3_B3_COMMIT
        expected_implementation_parent = R3_B3_COMMIT
    if contract.get("base_commit") != expected_base:
        raise GateError("base_commit_mismatch", "R3 manifest base commit is not the declared corrective base")
    if contract.get("implementation_parent_commit") != expected_implementation_parent:
        raise GateError(
            "implementation_parent_mismatch", "R3 implementation parent is not the declared corrective base"
        )
    if contract.get("manifest_path") != R3_MANIFEST_PATH:
        raise GateError("manifest_path_mismatch", "R3 manifest path is not frozen")
    if contract.get("manifest_commit_rule") != "manifest-only: exactly the declared R3 manifest path":
        raise GateError("manifest_commit_rule_mismatch", "R3 manifest-only commit rule drifted")
    implementation = contract.get("implementation_commit")
    if not isinstance(implementation, str) or len(implementation) != 40:
        raise GateError("implementation_pin_missing", "R3 implementation commit is missing")
    if run_parent != implementation:
        raise GateError(
            "manifest_commit_topology", "run commit must be the direct child of the declared implementation"
        )
    implementation_tree = contract.get("implementation_tree")
    if not isinstance(implementation_tree, str) or len(implementation_tree) != 40:
        raise GateError("implementation_tree_missing", "R3 implementation tree is missing")
    parameters = {
        "formation_bars": 252,
        "skip_bars": 21,
        "beta_window_bars": 273,
        "beta_window_plus_one_closes": 274,
        "residual_vol_window": 63,
        "top_n": 8,
        "max_single_weight": 0.10,
        "ivol_reject_quantile": 0.70,
        "rebalance_frequency": "weekly",
    }
    if contract.get("frozen_parameters") != parameters:
        raise GateError("frozen_parameter_mismatch", "R3 frozen parameters drifted")
    thresholds = contract.get("numerical_thresholds")
    if not isinstance(thresholds, Mapping):
        raise GateError("numerical_thresholds_missing", "R3 numerical thresholds are missing")
    for key, value in DEFAULT_NUMERICAL_THRESHOLDS.items():
        if thresholds.get(key) != value:
            raise GateError("numerical_threshold_mismatch", f"R3 threshold {key} drifted")
    source = contract.get("iwm_raw_source_contract")
    expected_source = {
        "provider": SOURCE_PROVIDER,
        "api_family_version": SOURCE_API_FAMILY_VERSION,
        "endpoint_identity": SOURCE_ENDPOINT,
        "asset_class": "us_equity",
        "symbol": "IWM",
        "timeframe": RAW_SOURCE_TIMEFRAME,
        "feed": SOURCE_FEED,
        "adjustment": SOURCE_ADJUSTMENT,
        "sort": SOURCE_SORT,
        "request_start": "2016-01-01",
        "request_end": "2026-07-31",
        "network_host": "data.alpaca.markets",
        "acquisition_script_path": "scripts/acquire_m3_iwm_raw_bundle.py",
        "no_fallback_allowed": True,
        "verifier_network_free": True,
        "raw_pages_untracked": True,
        "acquisition_is_read_only": True,
    }
    if source != expected_source:
        raise GateError("source_contract_mismatch", "R3 IWM source contract drifted")
    if contract.get("pit_builder_path") != "scripts/build_m3_pit_scanner_universe.py":
        raise GateError("pit_builder_contract_mismatch", "R3 PIT builder path is not frozen")
    expected_ocp = {
        "observer_execution_strategy_identity_must_match": True,
        "observer_execution_object_instance_must_differ": True,
        "candidate_owned_ocp_required_before_economics": True,
        "m3_candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
        "economic_mission_ready": False,
    }
    if contract.get("ocp_boundary") != expected_ocp:
        raise GateError("ocp_contract_mismatch", "R3 OCP boundary contract drifted")
    if contract.get("mandatory_data_pass_gates") != list(R3_MANDATORY_GATES):
        raise GateError("mandatory_gate_contract_mismatch", "R3 mandatory gate list drifted")
    if contract.get("canonical_manifest_path") != "data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json":
        raise GateError("canonical_manifest_contract_mismatch", "R3 canonical manifest path is not frozen")
    if contract.get("normalization_contract") != {
        "version": NORMALIZER_VERSION,
        "canonical_columns": list(CANONICAL_COLUMNS),
        "canonical_timeframe": CANONICAL_TIMEFRAME,
        "raw_timeframe": RAW_SOURCE_TIMEFRAME,
        "timezone": EXCHANGE_TIMEZONE,
        "calendar": "NYSE",
        "decimal_policy": "finite Decimal parsing; no rounding or synthesis",
    }:
        raise GateError("normalization_contract_mismatch", "R3 normalization contract drifted")
    if contract.get("numerical_contract") != {
        "design_matrix": ["SPY", "IWM-SPY", "QQQ-SPY"],
        "fit": "demean X and y; numpy least squares; retain rank and singular diagnostics",
        "residual": "y - X @ beta using non-demeaned X and no applied intercept",
        "skip_window": "omit latest 21 residual returns from momentum; retain all 273 returns for beta fit",
        "economic_scoring_allowed": False,
    }:
        raise GateError("numerical_contract_mismatch", "R3 numerical contract drifted")
    if contract.get("candidate_population_contract") != {
        "schema_version": "m3-pit-scanner-population-v1",
        "methodology": "strictly_prior_data_frozen_scanner_eligible_universe",
        "observation_strategy": None,
        "manual_candidate_selection_allowed": False,
        "reference_symbols_excluded": list(REFERENCE_SYMBOLS),
    }:
        raise GateError("candidate_population_contract_mismatch", "R3 candidate population contract drifted")
    if contract.get("composite_dataset_identity_contract") != {
        "encoding": "m3-composite-dataset-v1",
        "required": True,
        "canonical_parent": "frozen v5 bytes",
        "iwm_component": "verified normalized sidecar from raw page bundle",
        "normalizer": NORMALIZER_VERSION,
        "calendar": "pandas_market_calendars NYSE exact version",
    }:
        raise GateError("composite_contract_mismatch", "R3 composite identity contract drifted")
    if contract.get("reference_role_contract") != {
        "reference_observation_symbols": list(REFERENCE_SYMBOLS),
        "candidate_membership_allowed": False,
        "target_membership_allowed": False,
        "execution_membership_allowed": False,
        "original_configured_trade_set_recorded_separately": True,
        "effective_research_overlay_recorded_separately": True,
        "runtime_execution_evidence_status": "EXECUTION_NOT_RUN",
    }:
        raise GateError("reference_role_contract_mismatch", "R3 reference-role contract drifted")
    if contract.get("same_run_v3_control_contract") != {
        "required_before_comparison": True,
        "same_data_vintage": True,
        "same_evaluation_sessions": True,
        "same_scanner_policy": True,
        "same_terminal_inputs": True,
        "same_reference_reservation_contract": True,
        "economics_run_in_this_mission": False,
    }:
        raise GateError("same_run_contract_mismatch", "R3 same-run contract drifted")
    forbidden = [
        "return",
        "net_return",
        "gross_return",
        "pnl",
        "cagr",
        "sharpe",
        "sortino",
        "calmar",
        "max_drawdown",
        "turnover",
        "trade_count",
        "fill_count",
        "portfolio_targets",
        "economic_candidate_scores",
        "economic_candidate_ranking",
    ]
    if contract.get("forbidden_economic_fields") != forbidden:
        raise GateError("forbidden_field_contract_mismatch", "R3 forbidden economic field list drifted")
    calendar_identity = contract.get("calendar_identity")
    if not isinstance(calendar_identity, Mapping) or dict(calendar_identity) != calendar_dependency_identity():
        raise GateError("dependency_identity_mismatch", "R3 calendar dependency identity drifted")
    if not isinstance(contract.get("config_semantic_digest_sha256"), str):
        raise GateError("config_digest_missing", "R3 settings semantic digest is missing")


def _validate_contract(contract: Mapping[str, object], *, run_commit: str, run_parent: str) -> None:
    if contract.get("mission") == R3_MISSION:
        _validate_r3_contract(contract, run_parent=run_parent)
        return
    if contract.get("schema_version") != "2.0.0":
        raise GateError("manifest_schema_mismatch", "R2 manifest schema is not 2.0.0")
    if contract.get("mission") != "equity_m3_data_gate_2026_09_r2":
        raise GateError("manifest_mission_mismatch", "R2 manifest mission is incorrect")
    if contract.get("repository_url") != "https://github.com/batuhankiskac/ParayaBuck":
        raise GateError("manifest_repository_mismatch", "R2 manifest repository is incorrect")
    if contract.get("branch") != "main" or contract.get("snapshot_date") != "2026-09-01":
        raise GateError("manifest_snapshot_mismatch", "R2 manifest branch or snapshot date drifted")
    if contract.get("declared_before_results") is not True:
        raise GateError("manifest_not_predeclared", "R2 manifest must be predeclared")
    for key, expected in (
        ("research_only", True),
        ("automatic_promotion", False),
        ("human_promotion_only", True),
        ("candidate_economics_run", False),
    ):
        if contract.get(key) != expected:
            raise GateError("manifest_policy_mismatch", f"R2 manifest {key} drifted")
    if contract.get("base_commit") != BASE_COMMIT:
        raise GateError("base_commit_mismatch", "R2 manifest base commit is not the pinned R1 base")
    if contract.get("manifest_path") != R2_MANIFEST_PATH:
        raise GateError("manifest_path_mismatch", "R2 manifest path is not frozen")
    if contract.get("implementation_parent_commit") != BASE_COMMIT:
        raise GateError("implementation_parent_mismatch", "R2 implementation parent is not the pinned base")
    if contract.get("manifest_commit_rule") != "manifest-only: exactly the declared R2 manifest path":
        raise GateError("manifest_commit_rule_mismatch", "R2 manifest-only commit rule drifted")
    implementation = contract.get("implementation_commit")
    if not isinstance(implementation, str) or len(implementation) != 40:
        raise GateError("implementation_pin_missing", "R2 implementation commit is missing")
    if run_parent != implementation:
        raise GateError("manifest_commit_topology", "run commit must be the direct child of A2")
    parameters = contract.get("frozen_parameters")
    required_parameters = {
        "formation_bars": 252,
        "skip_bars": 21,
        "beta_window_bars": 273,
        "beta_window_plus_one_closes": 274,
        "residual_vol_window": 63,
        "top_n": 8,
        "max_single_weight": 0.10,
        "ivol_reject_quantile": 0.70,
        "rebalance_frequency": "weekly",
    }
    if parameters != required_parameters:
        raise GateError("frozen_parameter_mismatch", "R2 frozen parameters drifted")
    thresholds = contract.get("numerical_thresholds")
    if not isinstance(thresholds, Mapping):
        raise GateError("numerical_thresholds_missing", "R2 numerical thresholds are missing")
    for key, value in DEFAULT_NUMERICAL_THRESHOLDS.items():
        if thresholds.get(key) != value:
            raise GateError("numerical_threshold_mismatch", f"R2 threshold {key} drifted")
    source = contract.get("iwm_raw_source_contract")
    if not isinstance(source, Mapping):
        raise GateError("source_contract_missing", "R2 IWM source contract is missing")
    for key, expected in (
        ("provider", SOURCE_PROVIDER),
        ("api_family_version", SOURCE_API_FAMILY_VERSION),
        ("endpoint_identity", SOURCE_ENDPOINT),
        ("asset_class", "us_equity"),
        ("symbol", "IWM"),
        ("timeframe", RAW_SOURCE_TIMEFRAME),
        ("feed", SOURCE_FEED),
        ("adjustment", SOURCE_ADJUSTMENT),
        ("sort", SOURCE_SORT),
        ("request_start", "2016-01-01"),
        ("request_end", "2026-07-31"),
    ):
        if source.get(key) != expected:
            raise GateError("source_contract_mismatch", f"R2 source contract {key} drifted")
    if source.get("no_fallback_allowed") is not True:
        raise GateError("source_contract_mismatch", "R2 source contract must forbid fallback")
    ocp = contract.get("ocp_boundary")
    expected_ocp = {
        "observer_execution_strategy_identity_must_match": True,
        "observer_execution_object_instance_must_differ": True,
        "candidate_owned_ocp_required_before_economics": True,
        "m3_candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
        "economic_mission_ready": False,
    }
    if ocp != expected_ocp:
        raise GateError("ocp_contract_mismatch", "R2 OCP boundary contract drifted")
    if contract.get("mandatory_data_pass_gates") != list(MANDATORY_GATES):
        raise GateError("mandatory_gate_contract_mismatch", "R2 mandatory gate list drifted")
    if contract.get("canonical_manifest_path") != "data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json":
        raise GateError("canonical_manifest_contract_mismatch", "canonical manifest path is not frozen")
    if contract.get("normalization_contract") != {
        "version": NORMALIZER_VERSION,
        "canonical_columns": list(CANONICAL_COLUMNS),
        "canonical_timeframe": CANONICAL_TIMEFRAME,
        "raw_timeframe": RAW_SOURCE_TIMEFRAME,
        "timezone": EXCHANGE_TIMEZONE,
        "calendar": "NYSE",
        "decimal_policy": "finite Decimal parsing; no rounding or synthesis",
    }:
        raise GateError("normalization_contract_mismatch", "R2 normalization contract drifted")
    if contract.get("numerical_contract") != {
        "design_matrix": ["SPY", "IWM-SPY", "QQQ-SPY"],
        "fit": "demean X and y; numpy least squares; retain rank and singular diagnostics",
        "residual": "y - X @ beta using non-demeaned X and no applied intercept",
        "skip_window": "omit latest 21 residual returns from momentum; retain all 273 returns for beta fit",
        "economic_scoring_allowed": False,
    }:
        raise GateError("numerical_contract_mismatch", "R2 numerical contract drifted")
    if contract.get("candidate_population_contract") != {
        "schema_version": "m3-pit-scanner-population-v1",
        "methodology": "strictly_prior_data_frozen_scanner_eligible_universe",
        "observation_strategy": None,
        "manual_candidate_selection_allowed": False,
        "reference_symbols_excluded": list(REFERENCE_SYMBOLS),
    }:
        raise GateError("candidate_population_contract_mismatch", "R2 candidate population contract drifted")
    if contract.get("composite_dataset_identity_contract") != {
        "encoding": "m3-composite-dataset-v1",
        "required": True,
        "canonical_parent": "frozen v5 bytes",
        "iwm_component": "verified normalized sidecar from raw page bundle",
        "normalizer": NORMALIZER_VERSION,
        "calendar": "pandas_market_calendars NYSE exact version",
    }:
        raise GateError("composite_contract_mismatch", "R2 composite identity contract drifted")
    if contract.get("same_run_v3_control_contract") != {
        "required_before_comparison": True,
        "same_data_vintage": True,
        "same_evaluation_sessions": True,
        "same_scanner_policy": True,
        "same_terminal_inputs": True,
        "same_reference_reservation_contract": True,
        "economics_run_in_this_mission": False,
    }:
        raise GateError("same_run_contract_mismatch", "R2 same-run V3 control contract drifted")
    if contract.get("forbidden_economic_fields") != [
        "return",
        "net_return",
        "gross_return",
        "pnl",
        "cagr",
        "sharpe",
        "sortino",
        "calmar",
        "max_drawdown",
        "turnover",
        "trade_count",
        "fill_count",
        "portfolio_targets",
        "economic_candidate_scores",
        "economic_candidate_ranking",
    ]:
        raise GateError("forbidden_field_contract_mismatch", "R2 forbidden economic field list drifted")
    calendar_identity = contract.get("calendar_identity")
    if not isinstance(calendar_identity, Mapping) or dict(calendar_identity) != calendar_dependency_identity():
        raise GateError("dependency_identity_mismatch", "R2 calendar dependency identity drifted")
    if not isinstance(contract.get("config_semantic_digest_sha256"), str):
        raise GateError("config_digest_missing", "R2 settings semantic digest is missing")


def _validate_r3_commit_provenance(
    contract: Mapping[str, object],
    *,
    manifest_path: str,
    run_commit: str,
    run_parent: str,
    repo: Path = _REPO,
) -> dict[str, object]:
    implementation = str(contract["implementation_commit"])
    base = str(contract["base_commit"])
    if _git_parent(implementation, repo=repo) != base:
        raise GateError("implementation_parent_mismatch", "A3 is not a direct child of D2")
    if _git_parent(run_commit, repo=repo) != implementation or run_parent != implementation:
        raise GateError("manifest_commit_topology", "B3 is not a direct child of A3")
    if manifest_path != R3_MANIFEST_PATH:
        raise GateError("manifest_path_mismatch", "R3 provenance must use the pinned R3 manifest path")
    if _git_changed_paths(run_commit, repo=repo) != [manifest_path]:
        raise GateError(
            "manifest_commit_not_manifest_only", "manifest-only commit changed paths other than the R3 manifest"
        )
    try:
        manifest_blob = _git_blob_id(run_commit, manifest_path, repo=repo)
    except subprocess.CalledProcessError as exc:
        raise GateError("manifest_blob_missing", "R3 manifest Git blob is missing") from exc
    implementation_tree = _git_tree_id(implementation, repo=repo)
    if contract.get("implementation_tree") != implementation_tree:
        raise GateError("implementation_tree_mismatch", "A3 tree differs from the R3 manifest")
    raw_critical = contract.get("critical_git_blobs")
    if not isinstance(raw_critical, Mapping) or set(raw_critical) != set(R3_CRITICAL_GIT_PATHS):
        raise GateError("critical_blob_pin_invalid", "R3 critical Git blob set is incomplete or contains extras")
    critical: dict[str, object] = {}
    for path, declaration in raw_critical.items():
        if not isinstance(path, str) or not isinstance(declaration, Mapping):
            raise GateError("critical_blob_pin_invalid", "R3 critical Git blob declaration is invalid")
        try:
            implementation_blob = _git_blob_id(implementation, path, repo=repo)
            run_blob = _git_blob_id(run_commit, path, repo=repo)
            blob_bytes = _git_blob_bytes(implementation, path, repo=repo)
        except subprocess.CalledProcessError as exc:
            raise GateError("critical_blob_missing", f"R3 critical Git blob missing: {path}") from exc
        if implementation_blob != run_blob:
            raise GateError("implementation_blob_mismatch", f"critical blob changed between A3 and B3: {path}")
        if declaration.get("git_blob_id") != implementation_blob:
            raise GateError("implementation_blob_mismatch", f"R3 critical blob pin differs: {path}")
        if declaration.get("raw_sha256") != _sha256_bytes(blob_bytes):
            raise GateError("implementation_blob_hash_mismatch", f"R3 critical blob raw hash differs: {path}")
        critical[path] = {"git_blob_id": implementation_blob, "raw_sha256": _sha256_bytes(blob_bytes)}
    recovery = contract.get("recovery_lineage")
    if recovery is not None:
        if not isinstance(recovery, Mapping):
            raise GateError("recovery_lineage_mismatch", "R3 corrective recovery lineage is not an object")
        abandoned_implementation = str(recovery.get("abandoned_implementation_commit", ""))
        abandoned_manifest = str(recovery.get("abandoned_manifest_commit", ""))
        try:
            if _git_parent(abandoned_implementation, repo=repo) != R3_BASE_COMMIT:
                raise GateError("recovery_lineage_mismatch", "abandoned A3 does not descend from D2")
            if _git_parent(abandoned_manifest, repo=repo) != abandoned_implementation:
                raise GateError("recovery_lineage_mismatch", "abandoned B3 is not a direct child of A3")
            if _git_changed_paths(abandoned_manifest, repo=repo) != [manifest_path]:
                raise GateError("recovery_lineage_mismatch", "abandoned B3 was not manifest-only")
        except subprocess.CalledProcessError as exc:
            raise GateError("recovery_lineage_mismatch", "abandoned A3/B3 commits are unavailable") from exc
        if base != abandoned_manifest:
            raise GateError("recovery_lineage_mismatch", "A4 corrective base is not the abandoned B3 commit")
    return {
        "manifest_commit": run_commit,
        "manifest_git_blob_id": manifest_blob,
        "implementation_commit": implementation,
        "implementation_tree": implementation_tree,
        "base_commit": base,
        "critical_git_blobs": critical,
        "changed_paths": [manifest_path],
    }


def _validate_commit_provenance(
    contract: Mapping[str, object],
    *,
    manifest_path: str,
    run_commit: str,
    run_parent: str,
    repo: Path = _REPO,
) -> dict[str, object]:
    if contract.get("mission") == R3_MISSION:
        return _validate_r3_commit_provenance(
            contract,
            manifest_path=manifest_path,
            run_commit=run_commit,
            run_parent=run_parent,
            repo=repo,
        )
    implementation = str(contract["implementation_commit"])
    base = str(contract["base_commit"])
    if _git_parent(implementation, repo=repo) != base:
        raise GateError("implementation_parent_mismatch", "A2 is not a direct child of the corrective base")
    if _git_parent(run_commit, repo=repo) != implementation or run_parent != implementation:
        raise GateError("manifest_commit_topology", "B2 is not a direct child of A2")
    if _git_changed_paths(run_commit, repo=repo) != [manifest_path]:
        raise GateError(
            "manifest_commit_not_manifest_only", "manifest-only commit changed paths other than the R2 manifest"
        )
    try:
        manifest_blob = _git_blob_id(run_commit, manifest_path, repo=repo)
    except subprocess.CalledProcessError as exc:
        raise GateError("manifest_blob_missing", "R2 manifest Git blob is missing") from exc
    implementation_tree = _git_tree_id(implementation, repo=repo)
    if contract.get("implementation_tree") != implementation_tree:
        raise GateError("implementation_tree_mismatch", "A2 tree differs from the R2 manifest")
    raw_critical = contract.get("critical_git_blobs")
    if not isinstance(raw_critical, Mapping) or not raw_critical:
        raise GateError("critical_blob_pin_missing", "R2 critical Git blob pins are missing")
    if set(raw_critical) != set(CRITICAL_GIT_PATHS):
        raise GateError("critical_blob_pin_invalid", "R2 critical Git blob set is incomplete or contains extras")
    critical: dict[str, object] = {}
    for path, declaration in raw_critical.items():
        if not isinstance(path, str) or not isinstance(declaration, Mapping):
            raise GateError("critical_blob_pin_invalid", "R2 critical Git blob declaration is invalid")
        try:
            implementation_blob = _git_blob_id(implementation, path, repo=repo)
            run_blob = _git_blob_id(run_commit, path, repo=repo)
            blob_bytes = _git_blob_bytes(implementation, path, repo=repo)
        except subprocess.CalledProcessError as exc:
            raise GateError("critical_blob_missing", f"critical Git blob missing: {path}") from exc
        if implementation_blob != run_blob:
            raise GateError("implementation_blob_mismatch", f"critical blob changed between A2 and B2: {path}")
        if declaration.get("git_blob_id") != implementation_blob:
            raise GateError("implementation_blob_mismatch", f"critical blob pin differs: {path}")
        if declaration.get("raw_sha256") != _sha256_bytes(blob_bytes):
            raise GateError("implementation_blob_hash_mismatch", f"critical blob raw hash differs: {path}")
        critical[path] = {"git_blob_id": implementation_blob, "raw_sha256": _sha256_bytes(blob_bytes)}
    return {
        "manifest_commit": run_commit,
        "manifest_git_blob_id": manifest_blob,
        "implementation_commit": implementation,
        "implementation_tree": implementation_tree,
        "base_commit": base,
        "critical_git_blobs": critical,
        "changed_paths": [manifest_path],
    }


def _tracked_blob_identity(path: Path, *, commit: str = "HEAD") -> dict[str, object] | None:
    logical = _logical_path(path)
    if logical.startswith("<external:"):
        return None
    try:
        blob_id = _git_blob_id(commit, logical)
        blob_bytes = _git_blob_bytes(commit, logical)
    except subprocess.CalledProcessError:
        return None
    return {
        "path": logical,
        "commit": commit,
        "git_blob_id": blob_id,
        "git_blob_raw_sha256": _sha256_bytes(blob_bytes),
        "git_blob_normalized_sha256": _sha256_bytes(blob_bytes.replace(b"\r\n", b"\n")),
    }


def _validate_artifact_against_manifest(
    artifact: object, manifest: Mapping[str, object], *, expected_sha: str | None, label: str
) -> None:
    if not hasattr(artifact, "raw_sha256"):
        raise GateError("missing_artifact", f"missing {label} artifact")
    actual = artifact.raw_sha256  # type: ignore[union-attr]
    declared = expected_sha or _manifest_expected_hash(manifest, "sha256", "dataset_sha256")
    if declared is None:
        raise GateError("manifest_hash_missing", f"{label} manifest does not declare a SHA-256")
    if actual != declared:
        raise GateError("artifact_hash_mismatch", f"{label} SHA-256 differs from its frozen manifest")
    declared_size = manifest.get("byte_size")
    if isinstance(declared_size, int) and artifact.byte_size != declared_size:  # type: ignore[union-attr]
        raise GateError("artifact_size_mismatch", f"{label} byte size differs from its frozen manifest")


def _audit_corporate_actions(path: Path, manifest: Mapping[str, object], staged: object) -> dict[str, object]:
    declared = _manifest_expected_hash(manifest, "sha256", "dataset_sha256")
    if declared is None or not hasattr(staged, "raw_sha256") or staged.raw_sha256 != declared:  # type: ignore[union-attr]
        raise GateError("corporate_actions_hash_mismatch", "corporate-actions bytes differ from the frozen manifest")
    total = 0
    counts: dict[str, int] = {}
    reference_counts = {symbol: 0 for symbol in REFERENCE_SYMBOLS}
    with staged.staged_path.open(newline="", encoding="utf-8") as handle:  # type: ignore[union-attr]
        reader = csv.DictReader(handle)
        expected_columns = {
            "action_type",
            "symbol",
            "ex_date",
            "record_date",
            "payable_date",
            "rate",
            "old_rate",
            "new_rate",
            "source_symbol",
            "target_symbol",
            "raw_id",
        }
        if not expected_columns.issubset(reader.fieldnames or ()):
            raise GateError("corporate_actions_schema_mismatch", "corporate-actions CSV schema is incomplete")
        for row in reader:
            total += 1
            action_type = str(row.get("action_type") or "unknown")
            counts[action_type] = counts.get(action_type, 0) + 1
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol in reference_counts:
                reference_counts[symbol] += 1
    declared_count = manifest.get("row_count")
    if isinstance(declared_count, int) and declared_count != total:
        raise GateError("corporate_actions_row_count_mismatch", "corporate-actions row count differs from its manifest")
    return {
        "total_rows": total,
        "counts_by_action_type": dict(sorted(counts.items())),
        "reference_rows": reference_counts,
        "semantic_note": "cash_dividends is one action class; it is not the total corporate-action row count",
        "source_sha256": staged.raw_sha256,  # type: ignore[union-attr]
        "logical_path": _logical_path(path),
    }


def _source_completeness(
    rows_by_symbol: Mapping[str, Sequence[object]], *, start: date, end: date
) -> dict[str, object]:
    expected = expected_exchange_sessions(start, end)
    expected_set = set(expected)
    references: dict[str, object] = {}
    for symbol in REFERENCE_SYMBOLS:
        actual = {row.session_date for row in rows_by_symbol.get(symbol, ()) if row.session_date <= end}  # type: ignore[attr-defined]
        missing = sorted(expected_set - actual)
        extra = sorted(actual - expected_set)
        references[symbol] = {
            "observed_session_count": len(actual),
            "missing_sessions": _compact_dates([item.isoformat() for item in missing]),
            "extra_sessions": _compact_dates([item.isoformat() for item in extra]),
            "complete": not missing and not extra,
        }
    return {
        "source_start": start.isoformat(),
        "source_end": end.isoformat(),
        "expected_session_count": len(expected),
        "references": references,
        "complete": all(bool(item["complete"]) for item in references.values()),  # type: ignore[index]
    }


def _reference_gate_pass(
    inventory: Mapping[str, Mapping[str, object]],
    common: Mapping[str, object],
    weekly_full: Mapping[str, object],
    weekly_recent: Mapping[str, object],
) -> bool:
    required_zero = (
        "rejected_row_count",
        "nonfinite_count",
        "nonpositive_close_count",
        "nonpositive_ohlc_count",
        "invalid_ohlc_count",
        "negative_volume_count",
        "zero_volume_count",
        "duplicate_count",
        "conflicting_duplicate_count",
        "timeframe_mismatch_count",
        "market_mismatch_count",
        "naive_timestamp_count",
        "invalid_timestamp_count",
        "source_range_violation_count",
        "unexpected_schema_count",
    )
    for symbol in REFERENCE_SYMBOLS:
        item = inventory.get(symbol, {})
        if int(item.get("normalized_row_count", 0)) == 0:
            return False
        if item.get("missing_expected_sessions") or item.get("extra_off_calendar_sessions"):
            return False
        if any(int(item.get(field, 0)) != 0 for field in required_zero):
            return False
        if item.get("timeframe_values") != [CANONICAL_TIMEFRAME] or item.get("market_values") != ["EQUITY"]:
            return False
        row_accounting = item.get("row_accounting", {})
        if isinstance(row_accounting, Mapping) and not bool(row_accounting.get("identity_holds", True)):
            return False
    if int(common.get("common_session_count", 0)) < int(DEFAULT_NUMERICAL_THRESHOLDS["required_common_closes"]):
        return False

    def full_window_passes(weekly: Mapping[str, object]) -> bool:
        if not bool(weekly.get("ever_feasible")):
            return False
        if int(weekly.get("initial_warmup_decision_count", 0)) < 0:
            return False
        if (
            "first_accepted_decision" in weekly
            and "first_feasible_decision" in weekly
            and weekly.get("first_accepted_decision") != weekly.get("first_feasible_decision")
        ):
            return False
        if int(weekly.get("post_feasibility_failure_count", 0)) != 0:
            return False
        if int(weekly.get("post_feasibility_jointly_stale_count", 0)) != 0:
            return False
        if int(weekly.get("post_feasibility_misaligned_count", 0)) != 0:
            return False
        if int(weekly.get("post_feasibility_reference_outage_count", 0)) != 0:
            return False
        accepted_plus_warmup = int(
            weekly.get(
                "accepted_plus_initial_warmup_count",
                int(weekly.get("accepted_decision_count", 0)) + int(weekly.get("initial_warmup_decision_count", 0)),
            )
        )
        if accepted_plus_warmup != int(weekly.get("decision_count", 0)):
            return False
        return int(weekly.get("failed_after_warmup_count", 0)) == 0

    def recent_window_passes(weekly: Mapping[str, object]) -> bool:
        if int(weekly.get("initial_warmup_decision_count", 0)) != 0:
            return False
        if not bool(weekly.get("ever_feasible")):
            return False
        if int(weekly.get("post_feasibility_failure_count", 0)) != 0:
            return False
        if int(weekly.get("accepted_decision_count", 0)) != int(weekly.get("decision_count", 0)):
            return False
        return bool(weekly.get("recent_window_accepted_covers_every_required_weekly_decision", False))

    return full_window_passes(weekly_full) and recent_window_passes(weekly_recent)


def _matrix_audit(
    rows_by_symbol: Mapping[str, Sequence[object]],
    weekly: Mapping[str, object],
    candidate_symbols: Sequence[str],
    thresholds: Mapping[str, int | float],
    population: Mapping[str, object] | None = None,
) -> dict[str, object]:
    decisions = [
        item for item in weekly.get("decisions", ()) if isinstance(item, Mapping) and item.get("accepted") is True
    ]
    candidates = tuple(sorted({str(symbol).strip().upper() for symbol in candidate_symbols if str(symbol).strip()}))
    results: list[dict[str, object]] = []
    for decision in decisions:
        decision_date = str(decision["decision_session"])
        decision_candidates = (
            candidate_symbols_for_decision(population, decision_date) if population is not None else candidates
        )
        for candidate in decision_candidates:
            if candidate in REFERENCE_SYMBOLS:
                continue
            try:
                selected, closes = exact_m3_close_panel(candidate, decision_date, rows_by_symbol)  # type: ignore[arg-type]
                result = audit_design_matrix(candidate, decision_date, selected, closes, thresholds=thresholds)
            except GateError as exc:
                result = {
                    "candidate_symbol": candidate,
                    "decision_session": decision_date,
                    "session_count": 0,
                    "return_count": 0,
                    "matrix_shape": [0, 3],
                    "matrix_digest_sha256": canonical_digest(
                        {"encoding": MATRIX_ENCODING_VERSION, "candidate": candidate, "decision": decision_date}
                    ),
                    "passed": False,
                    "failure_categories": [exc.category],
                }
            results.append(result)
    return _matrix_summary(candidates, results)


def _matrix_summary(candidates: Sequence[str], results: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Summarize matrix audits without retaining source-bar history."""
    digests = [str(item["matrix_digest_sha256"]) for item in results]
    category_counts: dict[str, int] = {}
    for result in results:
        for category in result.get("failure_categories", ()):
            category_counts[str(category)] = category_counts.get(str(category), 0) + 1
    return {
        "population_methodology": "deterministic PIT scanner-eligible membership; no alpha ranking",
        "candidate_symbol_count": len(candidates),
        "total_matrix_count": len(results),
        "pass_count": sum(item.get("passed") is True for item in results),
        "fail_count": sum(item.get("passed") is not True for item in results),
        "failure_category_counts": dict(sorted(category_counts.items())),
        "matrix_digest_merkle_root": merkle_root(digests),
        "deterministic_examples": [
            {
                "candidate_symbol": item.get("candidate_symbol"),
                "decision_session": item.get("decision_session"),
                "matrix_digest_sha256": item.get("matrix_digest_sha256"),
                "passed": item.get("passed"),
            }
            for item in results[:3]
        ],
        "minimum_singular_value_ratio": min(
            (float(item["singular_value_ratio"]) for item in results if "singular_value_ratio" in item), default=None
        ),
        "maximum_condition_number": max(
            (float(item["condition_number"]) for item in results if "condition_number" in item), default=None
        ),
        "results": list(results),
    }


@dataclass(frozen=True)
class _CloseOnlyBar:
    """Small matrix-only view of a canonical bar."""

    session_date: date
    close: Decimal


def _r3_matrix_audit_stream(
    canonical_path: Path,
    panel_rows: Mapping[str, Sequence[object]],
    weekly: Mapping[str, object],
    population: Mapping[str, object],
    thresholds: Mapping[str, int | float],
) -> dict[str, object]:
    """Audit every accepted PIT decision while retaining only 274 closes/symbol.

    The canonical CSV is already content-addressed and the PIT builder has
    validated its complete order.  This pass parses only candidate symbols and
    keeps the exact candidate/reference intersection window needed by M3,
    avoiding a second in-memory copy of the full canonical dataset.
    """
    decisions = [
        item for item in weekly.get("decisions", ()) if isinstance(item, Mapping) and item.get("accepted") is True
    ]
    candidates = tuple(sorted({str(symbol).strip().upper() for symbol in population["candidate_symbols"]}))
    required_closes = int(thresholds["required_common_closes"])
    accepted_candidates = {
        decision["decision_session"]: candidate_symbols_for_decision(population, decision["decision_session"])
        for decision in decisions
    }
    wanted = set().union(*(set(symbols) for symbols in accepted_candidates.values())) - set(REFERENCE_SYMBOLS)
    reference_sessions = set.intersection(
        *(set(row.session_date for row in panel_rows.get(symbol, ())) for symbol in REFERENCE_SYMBOLS)
    )
    histories: dict[str, deque[tuple[date, Decimal]]] = {symbol: deque(maxlen=required_closes) for symbol in wanted}
    last_seen: dict[str, date] = {}
    results: list[Mapping[str, object]] = []
    decision_index = 0

    def flush_through(session: date) -> None:
        nonlocal decision_index
        while decision_index < len(decisions):
            decision = decisions[decision_index]
            decision_date = date.fromisoformat(str(decision["decision_session"]))
            if decision_date >= session:
                break
            results.extend(_audit_stream_decision(decision))
            decision_index += 1

    def _audit_stream_decision(decision: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
        decision_date = str(decision["decision_session"])
        decision_candidates = accepted_candidates[decision_date]
        rows: dict[str, Sequence[object]] = {symbol: panel_rows.get(symbol, ()) for symbol in REFERENCE_SYMBOLS}
        rows.update(
            {
                candidate: tuple(
                    _CloseOnlyBar(session_date=session_date, close=close)
                    for session_date, close in histories.get(candidate, ())
                )
                for candidate in decision_candidates
                if candidate not in REFERENCE_SYMBOLS
            }
        )
        summary = _matrix_audit(rows, {"decisions": [decision]}, candidates, thresholds, population)
        return tuple(item for item in summary["results"] if isinstance(item, Mapping))

    with canonical_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not set(CANONICAL_COLUMNS[1:]).issubset(reader.fieldnames or ()):
            raise GateError("canonical_schema_mismatch", "canonical CSV is missing required columns")
        for raw in reader:
            symbol = str(raw.get("symbol") or "").strip().upper()
            if symbol not in wanted:
                continue
            row = normalize_csv_row(raw)
            flush_through(row.session_date)
            prior_session = last_seen.get(symbol)
            if prior_session is not None and row.session_date <= prior_session:
                category = "candidate_duplicate" if row.session_date == prior_session else "candidate_order"
                raise GateError(category, f"candidate canonical rows are not strictly ordered: {symbol}")
            last_seen[symbol] = row.session_date
            if row.session_date in reference_sessions and decision_index < len(decisions):
                histories[symbol].append((row.session_date, row.close))
            if decision_index >= len(decisions):
                break
    if decisions:
        for decision in decisions[decision_index:]:
            results.extend(_audit_stream_decision(decision))
    return _matrix_summary(candidates, results)


def _load_pit_population(path: Path, staging_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
    staged = stage_file(path, staging_dir)
    payload = _load_json(staged.staged_path)
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, list):
        raise GateError("invalid_pit_universe", "PIT universe must contain a snapshots list")
    population = deterministic_candidate_population(snapshots)
    role = validate_pit_reference_boundary(population)
    population["input_artifact"] = {
        "path": _logical_path(path),
        "raw_sha256": staged.raw_sha256,
        "crlf_normalized_sha256": staged.crlf_normalized_sha256,
        "byte_size": staged.byte_size,
    }
    population["reference_boundary"] = role
    return population, _artifact_receipt(staged, logical_id="m3_pit_universe", provenance_class="external_untracked")


def _base_receipt(
    *,
    contract: Mapping[str, object],
    contract_path: Path,
    run_commit: str,
    run_parent: str,
    dirty_before: Sequence[str],
    config_digest: str | None,
    provenance: Mapping[str, object],
) -> dict[str, object]:
    r3 = contract.get("mission") == R3_MISSION
    critical = provenance.get("critical_git_blobs", {})
    if r3 and contract.get("recovery_lineage") is not None:
        lineage = {
            "lineage_mode": R3_RECOVERY_MODE,
            "corrective_base_commit": contract.get("base_commit"),
            "implementation_a4_commit": contract.get("implementation_commit"),
            "run_b4_commit": run_commit,
            "c4_commit": None,
            "abandoned_a3_b3": contract.get("recovery_lineage"),
        }
    elif r3:
        lineage = {
            "corrective_base_commit": contract.get("base_commit"),
            "implementation_a3_commit": contract.get("implementation_commit"),
            "run_b3_commit": run_commit,
            "c3_commit": None,
        }
    else:
        lineage = {
            "corrective_base_commit": contract.get("base_commit"),
            "implementation_a2_commit": contract.get("implementation_commit"),
            "run_b2_commit": run_commit,
            "c2_commit": None,
        }
    return {
        "schema_version": "3.0.0" if r3 else "2.0.0",
        "mission": R3_MISSION if r3 else "equity_m3_data_gate_2026_09_r2",
        "data_gate_verdict": "M3_DATA_GATE_BLOCKED",
        "corrective_infrastructure_verdict": "PASS",
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_ready": False,
        "next_required_gate": "M3_DATA_GATE_COMPLETION",
        "m3_candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
        "base_lineage": lineage,
        "implementation_tree": provenance.get("implementation_tree"),
        "critical_git_blobs": critical,
        "manifest_identity": {
            "path": _logical_path(contract_path),
            "git_blob_id": provenance.get("manifest_git_blob_id"),
            "raw_sha256": None,
            "declared_before_results": contract.get("declared_before_results"),
        },
        "verifier_identity": {"path": VERIFIER_PATH, "git_blob": critical.get(VERIFIER_PATH)},
        "normalizer_identity": {"path": NORMALIZER_PATH, "git_blob": critical.get(NORMALIZER_PATH)},
        "config_semantic_digest_sha256": config_digest,
        "environment_identity": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": importlib.metadata.version("numpy"),
            "pandas_market_calendars": calendar_dependency_identity()["version"],
        },
        "remote_ci_status": "NOT_YET_OBSERVED",
        "worktree_cleanliness": {
            "before": list(dirty_before),
            "after": [],
            "clean_before": not dirty_before,
            "clean_after": True,
        },
        "runtime_cache_isolation": {
            "runtime_state_read": False,
            "cache_read": False,
            "broker_read": False,
            "credentials_read": False,
            "network_contacted": False,
            "parse_source": "content_addressed_staged_files_only",
        },
        "source_provenance": {
            "provider": SOURCE_PROVIDER,
            "api_family_version": SOURCE_API_FAMILY_VERSION,
            "endpoint_identity": SOURCE_ENDPOINT,
            "feed": SOURCE_FEED,
            "adjustment": SOURCE_ADJUSTMENT,
            "raw_source_restriction": "operator-supplied raw IWM page bundle only",
            "fallback_allowed": False,
        },
        "normalization_contract": {
            "version": NORMALIZER_VERSION,
            "canonical_timeframe": CANONICAL_TIMEFRAME,
            "raw_timeframe": RAW_SOURCE_TIMEFRAME,
            "timezone": EXCHANGE_TIMEZONE,
            "calendar": calendar_dependency_identity(),
            "no_rounding_or_synthesis": True,
        },
        "numerical_contract": {
            "design_matrix": ["SPY", "IWM-SPY", "QQQ-SPY"],
            "matrix_encoding": MATRIX_ENCODING_VERSION,
            "residual_convention": "alpha_retaining: demeaned fit, non-demeaned application, no intercept",
            "skip_window_convention": "latest 21 residual returns omitted from momentum; beta fit retains all 273 returns",
        },
        "same_run_v3_control_input_readiness": {
            "status": "NOT_RUN_OUT_OF_SCOPE",
            "required_before_comparison": True,
            "same_data_vintage": False,
            "same_evaluation_sessions": False,
            "same_scanner_policy": False,
            "same_terminal_inputs": False,
            "same_reference_reservation_contract": False,
            "economics_run": False,
        },
        "mandatory_gate_results": _gate_map(R3_MANDATORY_GATES if r3 else MANDATORY_GATES),
        "input_provenance": {},
        "composite_dataset_identity": {"status": GATE_NOT_RUN},
        "reference_inventory": {},
        "row_rejection_inventory": {},
        "calendar_identity": calendar_dependency_identity(),
        "common_session_coverage": {},
        "weekly_coverage": {},
        "deterministic_numerical_population": {"status": GATE_NOT_RUN},
        "design_matrix_summary": {"status": GATE_NOT_RUN, "total_matrix_count": 0, "pass_count": 0, "fail_count": 0},
        "reference_role_evidence_levels": {
            "contract": "CONTRACT_VERIFIED",
            "pit_universe": "NOT_RUN",
            "ocp": "OCP_NOT_RUN",
            "execution": "EXECUTION_NOT_RUN",
        },
        "blocked_reasons": [],
        "forbidden_economic_fields": [],
        "tests_actually_executed": {"gate": "EXECUTED"},
    }


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _r3_load_pit_population(
    path: Path,
    staging_dir: Path,
    *,
    pit_manifest_path: Path | None,
    expected_scanner_config_digest: str | None,
) -> tuple[dict[str, object], dict[str, object], Mapping[str, object]]:
    staged = stage_file(path, staging_dir)
    payload = _load_json(staged.staged_path)
    if payload.get("schema_version") != "m3-pit-scanner-population-v1":
        raise GateError("pit_schema_mismatch", "R3 PIT population schema is not frozen")
    if payload.get("mission") != "equity_m3_pit_scanner_population_2026_09_r3":
        raise GateError("pit_mission_mismatch", "R3 PIT population mission is incorrect")
    if payload.get("market") != "EQUITY" or payload.get("timeframe") != CANONICAL_TIMEFRAME:
        raise GateError("pit_market_timeframe_mismatch", "R3 PIT population must be EQUITY 1d")
    if payload.get("observation_strategy") is not None or payload.get("alpha_ranking_used") is not False:
        raise GateError("candidate_population_alpha_leak", "R3 PIT population is alpha-ranked")
    no_alpha = payload.get("no_alpha_assertion")
    if not isinstance(no_alpha, Mapping) or any(
        no_alpha.get(key) is not expected
        for key, expected in (
            ("observation_strategy_is_none", True),
            ("candidate_rankings_present", False),
            ("economic_ranking_present", False),
        )
    ):
        raise GateError("candidate_population_alpha_leak", "R3 PIT no-alpha assertion is incomplete")
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, list):
        raise GateError("invalid_pit_universe", "R3 PIT universe must contain a snapshots list")
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            raise GateError("invalid_pit_snapshot", "R3 PIT snapshot must be an object")
        if snapshot.get("strictly_prior_data") is not True:
            raise GateError("pit_future_leak", "R3 PIT snapshot lacks strict-prior evidence")
        effective = snapshot.get("effective_at")
        latest = snapshot.get("latest_input_timestamp")
        if effective is None or latest is None:
            raise GateError("pit_future_leak", "R3 PIT snapshot lacks timestamp boundary evidence")
        try:
            effective_at = datetime.fromisoformat(str(effective).replace("Z", "+00:00"))
            latest_input = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
        except ValueError as exc:
            raise GateError("invalid_pit_snapshot", "R3 PIT timestamp evidence is invalid") from exc
        if effective_at.tzinfo is None or latest_input.tzinfo is None or latest_input >= effective_at:
            raise GateError("pit_future_leak", "R3 PIT snapshot contains data at or after effective_at")
        if not isinstance(snapshot.get("symbols"), list):
            raise GateError("invalid_pit_snapshot", "R3 PIT snapshot symbols must be a list")
        if snapshot.get("scanner_version") != "equity-scanner-v3":
            raise GateError("pit_scanner_identity_mismatch", "R3 PIT snapshot scanner version is not production v3")
        if expected_scanner_config_digest and snapshot.get("config_digest") != expected_scanner_config_digest:
            raise GateError("pit_scanner_identity_mismatch", "R3 PIT snapshot scanner config digest drifted")
    population = deterministic_candidate_population(snapshots)
    if int(population.get("unique_candidate_count", 0)) <= 0:
        raise GateError("empty_candidate_population", "R3 PIT population contains no non-reference candidates")
    role = validate_pit_reference_boundary(population)
    population["reference_boundary"] = role
    population["input_artifact"] = {
        "path": _logical_path(path),
        "raw_sha256": staged.raw_sha256,
        "crlf_normalized_sha256": staged.crlf_normalized_sha256,
        "byte_size": staged.byte_size,
    }
    summary = payload.get("population")
    if not isinstance(summary, Mapping):
        raise GateError("pit_summary_missing", "R3 PIT population summary is missing")
    for key in (
        "candidate_universe_digest_sha256",
        "candidate_decision_eligibility_digest_sha256",
        "snapshot_count",
        "unique_candidate_count",
    ):
        if summary.get(key) != population.get(key):
            raise GateError("pit_digest_mismatch", f"R3 PIT population summary field differs: {key}")
    strict_prior = payload.get("strict_prior_proof")
    if not isinstance(strict_prior, Mapping) or strict_prior.get("status") != "PASS":
        raise GateError("pit_future_leak", "R3 PIT strict-prior proof is not PASS")
    if pit_manifest_path is not None:
        pit_manifest = _load_json(stage_file(pit_manifest_path, staging_dir).staged_path)
        if pit_manifest.get("mission") != "equity_m3_pit_scanner_population_2026_09_r3":
            raise GateError("pit_manifest_mission_mismatch", "compact PIT manifest mission is incorrect")
        if pit_manifest.get("raw_pit_sha256") != staged.raw_sha256:
            raise GateError("pit_manifest_digest_mismatch", "compact PIT manifest raw hash differs")
        for key in (
            "semantic_population_digest_sha256",
            "candidate_decision_eligibility_digest_sha256",
            "snapshot_count",
            "unique_candidate_count",
        ):
            if pit_manifest.get(key) != population.get(
                "candidate_universe_digest_sha256" if key == "semantic_population_digest_sha256" else key
            ):
                raise GateError("pit_manifest_digest_mismatch", f"compact PIT manifest field differs: {key}")
        if (
            pit_manifest.get("no_alpha_ranking") is not True
            or pit_manifest.get("candidate_rankings_present") is not False
        ):
            raise GateError("candidate_population_alpha_leak", "compact PIT manifest permits alpha ranking")
        population["compact_manifest"] = {
            "path": _logical_path(pit_manifest_path),
            "raw_sha256": _sha256(pit_manifest_path),
            "byte_size": pit_manifest_path.stat().st_size,
        }
    else:
        population["compact_manifest"] = {"status": GATE_NOT_RUN}
    return (
        population,
        _artifact_receipt(staged, logical_id="m3_pit_universe", provenance_class="external_untracked"),
        payload,
    )


def _r3_write_json_immutable(path: Path, payload: Mapping[str, object]) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise GateError("result_artifact_conflict", f"result artifact already differs: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _r3_weekly_full_pass(weekly: Mapping[str, object]) -> bool:
    if not bool(weekly.get("ever_feasible")):
        return False
    if "first_accepted_decision" in weekly and "first_feasible_decision" in weekly:
        if weekly.get("first_accepted_decision") != weekly.get("first_feasible_decision"):
            return False
    if int(weekly.get("post_feasibility_failure_count", 0)) != 0:
        return False
    if int(weekly.get("post_feasibility_jointly_stale_count", 0)) != 0:
        return False
    if int(weekly.get("post_feasibility_misaligned_count", 0)) != 0:
        return False
    if int(weekly.get("post_feasibility_reference_outage_count", 0)) != 0:
        return False
    if int(
        weekly.get(
            "accepted_plus_initial_warmup_count",
            int(weekly.get("accepted_decision_count", 0)) + int(weekly.get("initial_warmup_decision_count", 0)),
        )
    ) != int(weekly.get("decision_count", 0)):
        return False
    return int(weekly.get("failed_after_warmup_count", 0)) == 0


def _r3_weekly_recent_pass(weekly: Mapping[str, object]) -> bool:
    return (
        int(weekly.get("initial_warmup_decision_count", 0)) == 0
        and bool(weekly.get("ever_feasible"))
        and int(weekly.get("post_feasibility_failure_count", 0)) == 0
        and int(weekly.get("accepted_decision_count", 0)) == int(weekly.get("decision_count", 0))
        and bool(weekly.get("recent_window_accepted_covers_every_required_weekly_decision", False))
    )


def _r3_inventory_quality_pass(inventory: Mapping[str, Mapping[str, object]]) -> bool:
    required_zero = (
        "rejected_row_count",
        "nonfinite_count",
        "nonpositive_close_count",
        "nonpositive_ohlc_count",
        "invalid_ohlc_count",
        "negative_volume_count",
        "zero_volume_count",
        "duplicate_count",
        "conflicting_duplicate_count",
        "timeframe_mismatch_count",
        "market_mismatch_count",
        "naive_timestamp_count",
        "invalid_timestamp_count",
        "source_range_violation_count",
        "unexpected_schema_count",
    )
    for symbol in REFERENCE_SYMBOLS:
        item = inventory.get(symbol, {})
        if int(item.get("normalized_row_count", 0)) == 0:
            return False
        if item.get("missing_expected_sessions") or item.get("extra_off_calendar_sessions"):
            return False
        if any(int(item.get(field, 0)) != 0 for field in required_zero):
            return False
        if item.get("timeframe_values") != [CANONICAL_TIMEFRAME] or item.get("market_values") != ["EQUITY"]:
            return False
        accounting = item.get("row_accounting", {})
        if isinstance(accounting, Mapping) and accounting.get("identity_holds") is False:
            return False
    return True


def _run_r3_gate(
    args: argparse.Namespace,
    *,
    contract: Mapping[str, object],
    provenance: Mapping[str, object],
    manifest_blob_bytes: bytes,
    run_commit: str,
    run_parent: str,
    dirty_before: Sequence[str],
    contract_path: Path,
) -> tuple[dict[str, object], int]:
    gates = _gate_map(R3_MANDATORY_GATES)
    reasons: list[str] = []
    _set_gate(gates, "commit_topology", GATE_PASS, evidence=provenance)
    _set_gate(gates, "implementation_blob_identity", GATE_PASS, evidence=provenance["critical_git_blobs"])
    _set_gate(gates, "manifest_only_commit", GATE_PASS, evidence={"changed_paths": provenance["changed_paths"]})
    _set_gate(
        gates,
        "clean_worktree_at_start",
        GATE_PASS if not dirty_before else GATE_FAIL,
        reason=None if not dirty_before else "worktree is dirty before the R3 gate",
        paths=list(dirty_before),
    )
    if dirty_before:
        _add_reason(reasons, "worktree is dirty before the R3 gate")
    receipt = _base_receipt(
        contract=contract,
        contract_path=contract_path,
        run_commit=run_commit,
        run_parent=run_parent,
        dirty_before=dirty_before,
        config_digest=None,
        provenance=provenance,
    )
    receipt["manifest_identity"]["raw_sha256"] = _sha256_bytes(manifest_blob_bytes)  # type: ignore[index]
    critical = provenance.get("critical_git_blobs", {})
    receipt["acquisition_script_identity"] = (
        critical.get("scripts/acquire_m3_iwm_raw_bundle.py") if isinstance(critical, Mapping) else {}
    )
    receipt["pit_builder_identity"] = (
        critical.get("scripts/build_m3_pit_scanner_universe.py") if isinstance(critical, Mapping) else {}
    )
    receipt["mandatory_gate_results"] = gates

    config_data: Mapping[str, object] = {}
    config_digest_value: str | None = None
    try:
        config_data = _load_yaml(_REPO / "config/settings.yaml")
        config_digest_value = config_semantic_digest(config_data)
        if contract.get("config_semantic_digest_sha256") != config_digest_value:
            raise GateError("config_digest_mismatch", "settings semantic digest differs from the R3 manifest")
        _set_gate(gates, "configuration_identity", GATE_PASS, evidence={"semantic_digest_sha256": config_digest_value})
    except GateError as exc:
        _set_gate(gates, "configuration_identity", GATE_FAIL, reason=str(exc))
        _add_reason(reasons, str(exc))
    try:
        actual_calendar = calendar_dependency_identity()
        if contract.get("calendar_identity") != actual_calendar:
            raise GateError("dependency_identity_mismatch", "calendar dependency identity differs from R3 manifest")
        _set_gate(gates, "dependency_identity", GATE_PASS, evidence=actual_calendar)
    except GateError as exc:
        _set_gate(gates, "dependency_identity", GATE_FAIL, reason=str(exc))
        _add_reason(reasons, str(exc))
    acquisition_identity = (
        critical.get("scripts/acquire_m3_iwm_raw_bundle.py") if isinstance(critical, Mapping) else None
    )
    pit_identity = critical.get("scripts/build_m3_pit_scanner_universe.py") if isinstance(critical, Mapping) else None
    if isinstance(acquisition_identity, Mapping) and isinstance(pit_identity, Mapping):
        _set_gate(gates, "acquisition_script_identity", GATE_PASS, evidence=acquisition_identity)
    else:
        _set_gate(
            gates,
            "acquisition_script_identity",
            GATE_FAIL,
            reason="R3 acquisition/PIT implementation blob pins are missing",
        )
        _add_reason(reasons, "R3 acquisition/PIT implementation blob pins are missing")

    receipt["config_semantic_digest_sha256"] = config_digest_value
    with tempfile.TemporaryDirectory(prefix="m3-data-gate-r3-") as temporary:
        staging_dir = Path(temporary)
        artifacts: dict[str, object] = {}
        canonical_rows: dict[str, list[object]] = {symbol: [] for symbol in REFERENCE_SYMBOLS}
        quality: dict[str, dict[str, int]] = {symbol: {} for symbol in REFERENCE_SYMBOLS}
        canonical_artifact: object | None = None
        iwm_rows: tuple[object, ...] = ()
        iwm_audit: dict[str, object] = {"status": GATE_NOT_RUN}
        population: dict[str, object] | None = None
        pit_artifact: dict[str, object] = {}
        pit_payload: Mapping[str, object] | None = None

        try:
            canonical_path = Path(args.canonical_bars)
            canonical_manifest_path = Path(args.canonical_manifest)
            if not canonical_path.is_file() or not canonical_manifest_path.is_file():
                raise GateError("missing_canonical_artifact", "canonical bars CSV and manifest are required")
            canonical_manifest = _load_json(canonical_manifest_path)
            if str(canonical_manifest.get("feed", "")).lower() != SOURCE_FEED:
                raise GateError("canonical_source_contract", "canonical manifest feed must be sip")
            if str(canonical_manifest.get("adjustment", "")).lower() != SOURCE_ADJUSTMENT:
                raise GateError("canonical_source_contract", "canonical manifest adjustment must be split")
            canonical_manifest_identity = _tracked_blob_identity(canonical_manifest_path)
            if canonical_manifest_identity is None:
                raise GateError("canonical_manifest_provenance", "canonical manifest must be Git tracked")
            canonical_artifact = stage_file(canonical_path, staging_dir)
            _validate_artifact_against_manifest(
                canonical_artifact, canonical_manifest, expected_sha=None, label="canonical bars"
            )
            artifacts["canonical_bars"] = {
                **_artifact_receipt(
                    canonical_artifact, logical_id="canonical_equity_v5_bars", provenance_class="external_untracked"
                ),
                "manifest_git_blob": canonical_manifest_identity,
            }
            canonical_rows, quality = load_csv_reference_rows(
                canonical_artifact.staged_path, reference_symbols=REFERENCE_SYMBOLS
            )
            _set_gate(gates, "canonical_artifact_identity", GATE_PASS, evidence=artifacts["canonical_bars"])
        except (GateError, OSError) as exc:
            _set_gate(gates, "canonical_artifact_identity", GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))

        try:
            lifetimes_path = Path(args.lifetimes)
            if not lifetimes_path.is_file():
                raise GateError("missing_lifetimes", "lifetimes artifact is missing")
            lifetimes_artifact = stage_file(lifetimes_path, staging_dir)
            tracked = _tracked_blob_identity(lifetimes_path)
            if tracked is None:
                raise GateError("lifetime_provenance", "tracked v5 lifetime table must be proven as a Git blob")
            if lifetimes_artifact.crlf_normalized_sha256 != tracked["git_blob_raw_sha256"]:
                raise GateError("lifetime_blob_mismatch", "working lifetime bytes differ from the pinned Git blob")
            if args.lifetimes_expected_sha256 and lifetimes_artifact.raw_sha256 != args.lifetimes_expected_sha256:
                raise GateError("lifetime_hash_mismatch", "lifetime CLI hash differs from the Git blob")
            artifacts["lifetimes"] = {
                **_artifact_receipt(
                    lifetimes_artifact, logical_id="equity_entity_lifetimes_v5", provenance_class="git_blob"
                ),
                **tracked,
                "working_raw_sha256": lifetimes_artifact.raw_sha256,
                "working_normalized_sha256": lifetimes_artifact.crlf_normalized_sha256,
            }
            _set_gate(gates, "lifetime_identity", GATE_PASS, evidence=artifacts["lifetimes"])
        except (GateError, OSError) as exc:
            _set_gate(gates, "lifetime_identity", GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))

        try:
            ca_path = Path(args.corporate_actions)
            ca_manifest_path = Path(args.corporate_actions_manifest)
            if not ca_path.is_file() or not ca_manifest_path.is_file():
                raise GateError("missing_corporate_actions", "corporate-actions artifact and manifest are required")
            ca_manifest = _load_json(ca_manifest_path)
            ca_artifact = stage_file(ca_path, staging_dir)
            _validate_artifact_against_manifest(ca_artifact, ca_manifest, expected_sha=None, label="corporate actions")
            artifacts["corporate_actions"] = {
                **_artifact_receipt(
                    ca_artifact, logical_id="equity_corporate_actions_v5", provenance_class="external_untracked"
                ),
                "manifest_git_blob": _tracked_blob_identity(ca_manifest_path),
            }
            receipt["corporate_action_inventory"] = _audit_corporate_actions(ca_path, ca_manifest, ca_artifact)
            _set_gate(gates, "corporate_action_identity", GATE_PASS, evidence=artifacts["corporate_actions"])
        except (GateError, OSError) as exc:
            _set_gate(gates, "corporate_action_identity", GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))

        if args.iwm_bundle is None:
            reason = "no operator-supplied IWM raw bundle was provided"
            for name in (
                "iwm_raw_bundle",
                "iwm_provider_contract",
                "iwm_pagination_chain",
                "iwm_normalization",
                "iwm_source_completeness",
            ):
                _set_gate(gates, name, GATE_NOT_RUN, reason=reason)
            receipt["iwm_raw_source"] = {"status": GATE_NOT_RUN, "reason": reason}
            _add_reason(reasons, reason)
        else:
            try:
                iwm_rows, iwm_audit = validate_iwm_bundle(args.iwm_bundle, staging_dir)
                receipt["iwm_raw_source"] = {"status": GATE_PASS, **_compact_iwm_audit(iwm_audit)}
                for name in ("iwm_raw_bundle", "iwm_provider_contract", "iwm_pagination_chain", "iwm_normalization"):
                    _set_gate(gates, name, GATE_PASS, evidence={"source_identity": iwm_audit.get("source_identity")})
            except (GateError, OSError) as exc:
                iwm_audit = {
                    "status": GATE_FAIL,
                    "category": getattr(exc, "category", "source_error"),
                    "reason": str(exc),
                }
                receipt["iwm_raw_source"] = iwm_audit
                for name in (
                    "iwm_raw_bundle",
                    "iwm_provider_contract",
                    "iwm_pagination_chain",
                    "iwm_normalization",
                    "iwm_source_completeness",
                ):
                    _set_gate(gates, name, GATE_FAIL, reason=str(exc))
                _add_reason(reasons, str(exc))

        scanner_digest_value: str | None = None
        if config_data:
            try:
                from parayabuck.config import EquityScannerConfig, EquityUniverseConfig  # noqa: PLC0415
                from parayabuck.universe.scanner import scanner_config_digest  # noqa: PLC0415

                universe = config_data.get("universe")
                equity_raw = universe.get("equity") if isinstance(universe, Mapping) else None
                if not isinstance(equity_raw, Mapping):
                    raise GateError("equity_config_missing", "settings universe.equity is missing")
                equity_values = dict(equity_raw)
                scanner_raw = equity_values.pop("scanner", {})
                if not isinstance(scanner_raw, Mapping):
                    raise GateError("equity_scanner_config_missing", "settings universe.equity.scanner is missing")
                equity_config = EquityUniverseConfig(scanner=EquityScannerConfig(**dict(scanner_raw)), **equity_values)
                scanner_digest_value = scanner_config_digest(equity_config)
            except (GateError, TypeError, ValueError) as exc:
                _add_reason(reasons, str(exc))

        if args.pit_universe is None:
            reason = "no deterministic PIT scanner-universe input was supplied"
            _set_gate(gates, "deterministic_numerical_population", GATE_NOT_RUN, reason=reason)
            _set_gate(gates, "pit_reference_boundary", GATE_NOT_RUN, reason=reason)
            receipt["deterministic_numerical_population"] = {"status": GATE_NOT_RUN, "methodology": "not executed"}
            _add_reason(reasons, reason)
        else:
            try:
                default_pit_manifest = _REPO / R3_PIT_MANIFEST_PATH
                pit_manifest_path = (
                    Path(args.pit_manifest) if getattr(args, "pit_manifest", None) else default_pit_manifest
                )
                if not pit_manifest_path.is_file():
                    raise GateError("missing_pit_manifest", "compact R3 PIT population manifest is required")
                population, pit_artifact, pit_payload = _r3_load_pit_population(
                    Path(args.pit_universe),
                    staging_dir,
                    pit_manifest_path=pit_manifest_path,
                    expected_scanner_config_digest=scanner_digest_value,
                )
                receipt["deterministic_numerical_population"] = {
                    key: value for key, value in population.items() if key not in {"snapshots", "candidate_symbols"}
                }
                receipt["deterministic_numerical_population"]["input_artifact"] = pit_artifact
                _set_gate(
                    gates,
                    "deterministic_numerical_population",
                    GATE_PASS,
                    evidence=receipt["deterministic_numerical_population"],
                )
                _set_gate(gates, "pit_reference_boundary", GATE_PASS, evidence=population["reference_boundary"])
            except (GateError, OSError) as exc:
                _set_gate(gates, "deterministic_numerical_population", GATE_FAIL, reason=str(exc))
                _set_gate(gates, "pit_reference_boundary", GATE_FAIL, reason=str(exc))
                receipt["deterministic_numerical_population"] = {"status": GATE_FAIL, "reason": str(exc)}
                _add_reason(reasons, str(exc))

        panel_rows: dict[str, list[object]] = {
            symbol: list(canonical_rows.get(symbol, ())) for symbol in REFERENCE_SYMBOLS
        }
        if iwm_rows and gates["iwm_raw_bundle"]["status"] == GATE_PASS and canonical_artifact is not None:
            try:
                canonical_iwm = tuple(canonical_rows.get("IWM", ()))
                merged_iwm, merge_hashes = merge_normalized_rows(canonical_iwm, iwm_rows)  # type: ignore[arg-type]
                panel_rows["IWM"] = list(merged_iwm)
                receipt["merge_integrity"] = {
                    "status": GATE_PASS,
                    "existing_canonical_iwm_rows": len(canonical_iwm),
                    "supplied_normalized_iwm_rows": len(iwm_rows),
                    **merge_hashes,
                }
                quality["IWM"] = {
                    "raw_row_count": len(merged_iwm),
                    "raw_reference_rows": len(merged_iwm),
                    "normalized_reference_rows": len(merged_iwm),
                    "post_evaluation_valid_rows": sum(row.session_date > EVALUATION_END for row in merged_iwm),  # type: ignore[attr-defined]
                    "rejected_row_count": 0,
                }
            except GateError as exc:
                receipt["merge_integrity"] = {"status": GATE_FAIL, "reason": str(exc)}
                _set_gate(gates, "composite_dataset_identity", GATE_FAIL, reason=str(exc))
                _add_reason(reasons, str(exc))
        else:
            receipt["merge_integrity"] = {"status": GATE_NOT_RUN, "reason": "IWM source not available"}

        inventory: dict[str, dict[str, object]] = {symbol: {} for symbol in REFERENCE_SYMBOLS}
        common: dict[str, object] = {"common_session_count": 0, "common_sessions": []}
        weekly_full: dict[str, object] = {"decisions": [], "ever_feasible": False}
        weekly_recent: dict[str, object] = {"decisions": [], "ever_feasible": False}
        source_complete = False
        try:
            expected_sessions = expected_exchange_sessions(SOURCE_START, EVALUATION_END)
            source_identity = {
                "provider": SOURCE_PROVIDER,
                "api_family_version": SOURCE_API_FAMILY_VERSION,
                "endpoint_identity": SOURCE_ENDPOINT,
                "feed": SOURCE_FEED,
                "adjustment": SOURCE_ADJUSTMENT,
                "source_timeframe": CANONICAL_TIMEFRAME,
                "sort": SOURCE_SORT,
            }
            inventory = {
                symbol: build_reference_inventory(
                    panel_rows.get(symbol, ()),
                    raw_quality=quality.get(symbol),
                    expected_sessions=expected_sessions,
                    evaluation_end=EVALUATION_END,
                    source_identity=source_identity,
                )
                for symbol in REFERENCE_SYMBOLS
            }
            common = common_session_report(
                panel_rows, expected_sessions=expected_sessions, evaluation_end=EVALUATION_END
            )
            weekly_full = weekly_decision_coverage(
                panel_rows, expected_sessions=expected_sessions, start=SOURCE_START, end=EVALUATION_END
            )
            weekly_recent = weekly_decision_coverage(
                panel_rows, expected_sessions=expected_sessions, start=date(2022, 1, 1), end=EVALUATION_END
            )
            source_completeness = _source_completeness(panel_rows, start=SOURCE_START, end=SOURCE_END)
            source_complete = bool(source_completeness.get("complete"))
            row_quality_ok = _r3_inventory_quality_pass(inventory)
            full_weekly_ok = _r3_weekly_full_pass(weekly_full)
            recent_weekly_ok = _r3_weekly_recent_pass(weekly_recent)
            receipt["reference_inventory"] = _compact_inventory(inventory)
            receipt["row_rejection_inventory"] = {
                symbol: {
                    "raw_reference_rows": item.get("raw_reference_rows", 0),
                    "normalized_reference_rows": item.get("normalized_reference_rows", 0),
                    "post_evaluation_valid_rows": item.get("post_evaluation_valid_rows", 0),
                    "rejected_row_count": item.get("rejected_row_count", 0),
                    "rejection_counts": item.get("rejection_counts", {}),
                }
                for symbol, item in inventory.items()
            }
            receipt["source_completeness_through_2026_07_31"] = source_completeness
            receipt["evaluation_completeness_through_2026_07_30"] = _compact_common(common)
            receipt["common_session_coverage"] = {
                **_compact_common(common),
                "common_sessions_sha256": session_dates_digest(common["common_sessions"]),
            }
            receipt["weekly_coverage"] = {
                "full": _compact_weekly(weekly_full),
                "recent": _compact_weekly(weekly_recent),
            }
            receipt["reference_panel"] = {
                "normalized_reference_panel_sha256": reference_panel_digest(
                    [row for symbol in REFERENCE_SYMBOLS for row in panel_rows.get(symbol, ())],
                    source_identity=source_identity,
                ),
                "evaluation_reference_panel_sha256": reference_panel_digest(
                    [
                        row
                        for symbol in REFERENCE_SYMBOLS
                        for row in panel_rows.get(symbol, ())
                        if row.session_date <= EVALUATION_END
                    ],  # type: ignore[attr-defined]
                    source_identity=source_identity,
                ),
            }
            receipt["calendar_identity"] = calendar_dependency_identity()
            receipt["iwm_source_completeness"] = source_completeness
            _set_gate(
                gates,
                "iwm_source_completeness",
                GATE_PASS if source_complete else GATE_FAIL,
                reason=None if source_complete else "SPY/IWM/QQQ source completeness through 2026-07-31 failed",
                evidence=source_completeness,
            )
            _set_gate(
                gates,
                "reference_calendar",
                GATE_PASS if source_complete else GATE_FAIL,
                reason=None if source_complete else "reference source completeness or calendar failed",
                evidence={"calendar": receipt["calendar_identity"]},
            )
            _set_gate(
                gates,
                "reference_row_quality",
                GATE_PASS if row_quality_ok else GATE_FAIL,
                reason=None if row_quality_ok else "reference row quality failed",
            )
            _set_gate(
                gates,
                "reference_common_sessions",
                GATE_PASS if int(common["common_session_count"]) >= 274 else GATE_FAIL,
                common_session_count=common["common_session_count"],
            )
            _set_gate(
                gates,
                "full_weekly_post_warmup_coverage",
                GATE_PASS if full_weekly_ok else GATE_FAIL,
                reason=None if full_weekly_ok else "full-window post-warmup weekly coverage failed",
            )
            _set_gate(
                gates,
                "recent_weekly_coverage",
                GATE_PASS if recent_weekly_ok else GATE_FAIL,
                reason=None if recent_weekly_ok else "recent weekly coverage failed",
            )
            for failed, label in (
                (not source_complete, "source completeness failed"),
                (not row_quality_ok, "reference row quality failed"),
                (not full_weekly_ok, "full weekly coverage failed"),
                (not recent_weekly_ok, "recent weekly coverage failed"),
            ):
                if failed:
                    _add_reason(reasons, label)
        except (GateError, OSError) as exc:
            for name in (
                "iwm_source_completeness",
                "reference_calendar",
                "reference_row_quality",
                "reference_common_sessions",
                "full_weekly_post_warmup_coverage",
                "recent_weekly_coverage",
            ):
                _set_gate(gates, name, GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))

        if iwm_rows and canonical_artifact is not None and gates["iwm_raw_bundle"]["status"] == GATE_PASS:
            try:
                sidecar_path = Path(args.iwm_normalized_out) if args.iwm_normalized_out else None
                composite_manifest_out = Path(args.composite_manifest_out) if args.composite_manifest_out else None
                if sidecar_path is None or composite_manifest_out is None:
                    raise GateError(
                        "composite_artifact_missing", "R3 composite requires sidecar and manifest output paths"
                    )
                sidecar = write_normalized_sidecar(list(iwm_rows), sidecar_path)  # type: ignore[arg-type]
                sidecar_logical = _logical_path(sidecar_path)
                if sidecar_logical.startswith("<external:"):
                    raise GateError("sidecar_path_unbound", "normalized sidecar must be under the repository")
                sidecar["path"] = sidecar_logical
                canonical_manifest_identity = _tracked_blob_identity(Path(args.canonical_manifest))
                normalizer_blob = critical.get(NORMALIZER_PATH) if isinstance(critical, Mapping) else None
                if canonical_manifest_identity is None or not isinstance(normalizer_blob, Mapping):
                    raise GateError("composite_provenance_missing", "R3 composite Git identities are incomplete")
                identity = composite_dataset_identity(
                    canonical_parent_raw_sha256=canonical_artifact.raw_sha256,  # type: ignore[union-attr]
                    canonical_parent_normalized_sha256=canonical_artifact.crlf_normalized_sha256,  # type: ignore[union-attr]
                    canonical_manifest_git_blob=str(canonical_manifest_identity["git_blob_id"]),
                    iwm_raw_bundle_sha256=str(iwm_audit["raw_bundle_sha256"]),
                    iwm_normalized_raw_sha256=str(sidecar["raw_sha256"]),
                    iwm_normalized_semantic_row_sha256=str(sidecar["normalized_semantic_row_sha256"]),
                    normalizer_git_blob=str(normalizer_blob["git_blob_id"]),
                    normalizer_sha256=str(normalizer_blob["raw_sha256"]),
                    schema_version=NORMALIZER_VERSION,
                    canonical_timeframe=CANONICAL_TIMEFRAME,
                    source_feed=SOURCE_FEED,
                    adjustment_mode=SOURCE_ADJUSTMENT,
                    exchange_timezone=EXCHANGE_TIMEZONE,
                    calendar_identity=calendar_dependency_identity(),
                    source_range={"start": SOURCE_START.isoformat(), "end": SOURCE_END.isoformat()},
                    evaluation_range={"end_inclusive": EVALUATION_END.isoformat()},
                )
                composite_payload = {
                    "schema_version": "1.0.0",
                    "mission": "equity_m3_composite_dataset_2026_09",
                    "parent_dataset": "canonical_equity_v5_bars",
                    "iwm_sidecar": sidecar,
                    "identity": identity,
                }
                _r3_write_json_immutable(composite_manifest_out, composite_payload)
                receipt["composite_dataset_identity"] = {
                    "status": GATE_PASS,
                    "manifest_path": _logical_path(composite_manifest_out),
                    "manifest_sha256": _sha256(composite_manifest_out),
                    "identity": identity,
                    "sidecar": sidecar,
                }
                _set_gate(gates, "composite_dataset_identity", GATE_PASS, evidence=identity)
            except (GateError, OSError) as exc:
                receipt["composite_dataset_identity"] = {"status": GATE_FAIL, "reason": str(exc)}
                _set_gate(gates, "composite_dataset_identity", GATE_FAIL, reason=str(exc))
                _add_reason(reasons, str(exc))
        elif gates["composite_dataset_identity"]["status"] == GATE_UNKNOWN:
            _set_gate(
                gates,
                "composite_dataset_identity",
                GATE_NOT_RUN,
                reason="valid IWM raw bundle and canonical artifact are required",
            )
            receipt["composite_dataset_identity"] = {"status": GATE_NOT_RUN}

        if population is not None and gates["deterministic_numerical_population"]["status"] == GATE_PASS:
            prerequisites = (
                "reference_common_sessions",
                "full_weekly_post_warmup_coverage",
                "recent_weekly_coverage",
                "composite_dataset_identity",
                "pit_reference_boundary",
            )
            if all(gates[name]["status"] == GATE_PASS for name in prerequisites) and canonical_artifact is not None:
                thresholds = contract.get("numerical_thresholds", DEFAULT_NUMERICAL_THRESHOLDS)
                if not isinstance(thresholds, Mapping):
                    thresholds = DEFAULT_NUMERICAL_THRESHOLDS
                matrix_summary = _r3_matrix_audit_stream(
                    canonical_artifact.staged_path,
                    panel_rows,
                    weekly_recent,
                    population,
                    thresholds,
                )
                receipt["design_matrix_summary"] = {
                    key: value for key, value in matrix_summary.items() if key != "results"
                }
                matrix_ok = (
                    int(matrix_summary["total_matrix_count"]) > 0
                    and int(matrix_summary["pass_count"]) == int(matrix_summary["total_matrix_count"])
                    and int(matrix_summary["fail_count"]) == 0
                )
                _set_gate(
                    gates,
                    "design_matrix_feasibility",
                    GATE_PASS if matrix_ok else GATE_FAIL,
                    reason=None if matrix_ok else "matrix population is empty or contains failed audits",
                    total_matrix_count=matrix_summary["total_matrix_count"],
                    pass_count=matrix_summary["pass_count"],
                    fail_count=matrix_summary["fail_count"],
                )
                if not matrix_ok:
                    _add_reason(reasons, "design-matrix feasibility gate failed")
            elif gates["design_matrix_feasibility"]["status"] == GATE_UNKNOWN:
                reason = "design matrix phase not run because prerequisite data gates failed"
                _set_gate(gates, "design_matrix_feasibility", GATE_NOT_RUN, reason=reason)
                _add_reason(reasons, reason)
        else:
            if gates["design_matrix_feasibility"]["status"] == GATE_UNKNOWN:
                reason = "deterministic numerical population or PIT prerequisites are not ready"
                _set_gate(gates, "design_matrix_feasibility", GATE_NOT_RUN, reason=reason)
                _add_reason(reasons, reason)

        try:
            overlay = build_reference_overlay(config_data)
            role_receipt = overlay.to_dict()
            if population is not None:
                role_receipt["pit_universe_evidence"] = validate_pit_reference_boundary(population)
                receipt["reference_role_evidence_levels"]["pit_universe"] = "PIT_UNIVERSE_VERIFIED"  # type: ignore[index]
            receipt["reference_role_separation"] = role_receipt
            _set_gate(gates, "reference_role_contract", GATE_PASS, evidence=role_receipt)
        except GateError as exc:
            receipt["reference_role_separation"] = {"status": GATE_FAIL, "reason": str(exc)}
            _set_gate(gates, "reference_role_contract", GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))

        receipt["input_provenance"] = artifacts
        receipt["diagnostic_only"] = bool(args.diagnostic_candidate_symbol)
        if args.diagnostic_candidate_symbol:
            receipt["diagnostic_candidate_symbols"] = sorted(
                {str(item).strip().upper() for item in args.diagnostic_candidate_symbol if str(item).strip()}
            )
            _add_reason(reasons, "diagnostic candidate symbols cannot contribute to PASS")
        receipt["mandatory_gate_results"] = gates
        receipt["data_gate_verdict"] = evaluate_mandatory_gates(gates, R3_REQUIRED_FOR_DATA_PASS)
        receipt["next_required_gate"] = (
            "M3_CANDIDATE_OCP_GATE"
            if receipt["data_gate_verdict"] == "M3_DATA_GATE_PASS"
            else "M3_DATA_GATE_COMPLETION"
        )
        receipt["m3_candidate_ocp_status"] = "NOT_RUN_OUT_OF_SCOPE"
        receipt["economic_mission_ready"] = False

    dirty_after = _dirty_paths()
    receipt["worktree_cleanliness"]["after"] = dirty_after  # type: ignore[index]
    receipt["worktree_cleanliness"]["clean_after"] = not dirty_after  # type: ignore[index]
    receipt["blocked_reasons"] = sorted(set(reasons))
    if receipt["data_gate_verdict"] != "M3_DATA_GATE_PASS":
        receipt["corrective_infrastructure_verdict"] = (
            "PASS"
            if all(
                gates[name]["status"] == GATE_PASS
                for name in (
                    "commit_topology",
                    "implementation_blob_identity",
                    "manifest_only_commit",
                    "clean_worktree_at_start",
                    "configuration_identity",
                    "dependency_identity",
                    "acquisition_script_identity",
                )
            )
            else "BLOCKED"
        )
    receipt["forbidden_economic_fields"] = list(forbidden_receipt_keys(receipt))
    if receipt["forbidden_economic_fields"]:
        _set_gate(gates, "receipt_forbidden_fields", GATE_FAIL, reason="receipt contains forbidden economic fields")
        receipt["data_gate_verdict"] = "M3_DATA_GATE_BLOCKED"
        _add_reason(reasons, "receipt contains forbidden economic fields")
    else:
        _set_gate(gates, "receipt_forbidden_fields", GATE_PASS, evidence={"fields": []})
    receipt["mandatory_gate_results"] = gates
    receipt["blocked_reasons"] = sorted(set(reasons))
    receipt["self_digest_sha256"] = canonical_digest(receipt)
    output_path = Path(args.receipt_out)
    _r3_write_json_immutable(output_path, _safe(receipt))
    return receipt, 0 if receipt["data_gate_verdict"] == "M3_DATA_GATE_PASS" else EXIT_BLOCKED


def run_gate(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    run_commit = _run_git("rev-parse", "HEAD")
    run_parent = _run_git("rev-parse", "HEAD^")
    dirty_before = _dirty_paths()
    contract_path = Path(args.contract)
    if _logical_path(contract_path) == R3_MANIFEST_PATH:
        contract, _manifest_blob_id, manifest_blob_bytes = _load_committed_contract(
            contract_path, run_commit=run_commit
        )
        _validate_contract(contract, run_commit=run_commit, run_parent=run_parent)
        provenance = _validate_commit_provenance(
            contract,
            manifest_path=R3_MANIFEST_PATH,
            run_commit=run_commit,
            run_parent=run_parent,
        )
        return _run_r3_gate(
            args,
            contract=contract,
            provenance=provenance,
            manifest_blob_bytes=manifest_blob_bytes,
            run_commit=run_commit,
            run_parent=run_parent,
            dirty_before=dirty_before,
            contract_path=contract_path,
        )
    reasons: list[str] = []
    gates = _gate_map()
    contract: Mapping[str, object] = {}
    provenance: dict[str, object] = {}
    manifest_blob_bytes = b""
    try:
        contract, _manifest_blob_id, manifest_blob_bytes = _load_committed_contract(
            contract_path, run_commit=run_commit
        )
        _validate_contract(contract, run_commit=run_commit, run_parent=run_parent)
        provenance = _validate_commit_provenance(
            contract,
            manifest_path=R2_MANIFEST_PATH,
            run_commit=run_commit,
            run_parent=run_parent,
        )
        _set_gate(gates, "commit_topology", GATE_PASS, evidence=provenance)
        _set_gate(gates, "implementation_blob_identity", GATE_PASS, evidence=provenance["critical_git_blobs"])
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        _set_gate(gates, "commit_topology", GATE_FAIL, reason=str(exc))
        _set_gate(gates, "implementation_blob_identity", GATE_FAIL, reason=str(exc))
        _add_reason(reasons, str(exc))
    _set_gate(
        gates,
        "clean_worktree",
        GATE_PASS if not dirty_before else GATE_FAIL,
        reason=None if not dirty_before else "worktree is dirty before gate execution",
        paths=list(dirty_before),
    )
    if dirty_before:
        _add_reason(reasons, "worktree is dirty before gate execution")

    config_digest_value: str | None = None
    config_data: Mapping[str, object] = {}
    try:
        config_path = _REPO / "config/settings.yaml"
        config_data = _load_yaml(config_path)
        config_digest_value = config_semantic_digest(config_data)
        if contract.get("config_semantic_digest_sha256") != config_digest_value:
            raise GateError("config_digest_mismatch", "settings semantic digest differs from R2 manifest")
        _set_gate(gates, "configuration_identity", GATE_PASS, evidence={"semantic_digest_sha256": config_digest_value})
    except GateError as exc:
        _set_gate(gates, "configuration_identity", GATE_FAIL, reason=str(exc))
        _add_reason(reasons, str(exc))
    try:
        declared_calendar = contract.get("calendar_identity")
        actual_calendar = calendar_dependency_identity()
        if not isinstance(declared_calendar, Mapping) or dict(declared_calendar) != actual_calendar:
            raise GateError("dependency_identity_mismatch", "calendar dependency identity differs from R2 manifest")
        _set_gate(gates, "dependency_identity", GATE_PASS, evidence=actual_calendar)
    except GateError as exc:
        _set_gate(gates, "dependency_identity", GATE_FAIL, reason=str(exc))
        _add_reason(reasons, str(exc))

    receipt = _base_receipt(
        contract=contract,
        contract_path=contract_path,
        run_commit=run_commit,
        run_parent=run_parent,
        dirty_before=dirty_before,
        config_digest=config_digest_value,
        provenance=provenance,
    )
    receipt["manifest_identity"]["raw_sha256"] = _sha256_bytes(manifest_blob_bytes) if manifest_blob_bytes else None  # type: ignore[index]
    receipt["mandatory_gate_results"] = gates
    if config_digest_value is None:
        _add_reason(reasons, "settings semantic digest could not be verified")

    with tempfile.TemporaryDirectory(prefix="m3-data-gate-r2-") as temporary:
        staging_dir = Path(temporary)
        artifacts: dict[str, object] = {}
        canonical_rows: dict[str, list[object]] = {symbol: [] for symbol in REFERENCE_SYMBOLS}
        quality: dict[str, dict[str, int]] = {symbol: {} for symbol in REFERENCE_SYMBOLS}
        iwm_rows: tuple[object, ...] = ()
        iwm_audit: dict[str, object] = {"status": GATE_NOT_RUN}
        canonical_artifact: object | None = None
        population: dict[str, object] | None = None
        try:
            canonical_path = Path(args.canonical_bars)
            canonical_manifest_path = Path(args.canonical_manifest)
            if not canonical_path.is_file() or not canonical_manifest_path.is_file():
                raise GateError("missing_canonical_artifact", "canonical bars CSV and manifest are required")
            canonical_manifest = _load_json(canonical_manifest_path)
            if str(canonical_manifest.get("feed", "")).lower() != SOURCE_FEED:
                raise GateError("canonical_source_contract", "canonical manifest feed must be sip")
            if str(canonical_manifest.get("adjustment", "")).lower() != SOURCE_ADJUSTMENT:
                raise GateError("canonical_source_contract", "canonical manifest adjustment must be split")
            canonical_manifest_identity = _tracked_blob_identity(canonical_manifest_path)
            if canonical_manifest_identity is None:
                raise GateError("canonical_manifest_provenance", "canonical manifest must be Git tracked")
            canonical_artifact = stage_file(canonical_path, staging_dir)
            _validate_artifact_against_manifest(
                canonical_artifact, canonical_manifest, expected_sha=None, label="canonical bars"
            )
            artifacts["canonical_bars"] = {
                **_artifact_receipt(
                    canonical_artifact, logical_id="canonical_equity_v5_bars", provenance_class="external_untracked"
                ),
                "manifest_git_blob": canonical_manifest_identity,
            }
            _set_gate(gates, "canonical_artifact_identity", GATE_PASS, evidence=artifacts["canonical_bars"])
            canonical_rows, quality = load_csv_reference_rows(
                canonical_artifact.staged_path, reference_symbols=REFERENCE_SYMBOLS
            )
        except (GateError, OSError) as exc:
            _set_gate(gates, "canonical_artifact_identity", GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))

        try:
            lifetimes_path = Path(args.lifetimes)
            if not lifetimes_path.is_file():
                raise GateError("missing_lifetimes", "lifetimes artifact is missing")
            lifetimes_artifact = stage_file(lifetimes_path, staging_dir)
            tracked = _tracked_blob_identity(lifetimes_path)
            if tracked is None:
                raise GateError("lifetime_provenance", "tracked v5 lifetime table must be proven as a Git blob")
            if lifetimes_artifact.crlf_normalized_sha256 != tracked["git_blob_raw_sha256"]:
                raise GateError(
                    "lifetime_blob_mismatch",
                    "working lifetime bytes differ from the pinned Git blob under newline normalization",
                )
            if args.lifetimes_expected_sha256 and lifetimes_artifact.raw_sha256 != args.lifetimes_expected_sha256:
                raise GateError("lifetime_hash_mismatch", "lifetime CLI hash differs from the Git blob")
            artifacts["lifetimes"] = {
                **_artifact_receipt(
                    lifetimes_artifact, logical_id="equity_entity_lifetimes_v5", provenance_class="git_blob"
                ),
                **tracked,
                "working_raw_sha256": lifetimes_artifact.raw_sha256,
                "working_normalized_sha256": lifetimes_artifact.crlf_normalized_sha256,
            }
            _set_gate(gates, "lifetime_identity", GATE_PASS, evidence=artifacts["lifetimes"])
        except (GateError, OSError) as exc:
            _set_gate(gates, "lifetime_identity", GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))

        try:
            ca_path = Path(args.corporate_actions)
            ca_manifest_path = Path(args.corporate_actions_manifest)
            if not ca_path.is_file() or not ca_manifest_path.is_file():
                raise GateError("missing_corporate_actions", "corporate-actions artifact and manifest are required")
            ca_manifest = _load_json(ca_manifest_path)
            ca_artifact = stage_file(ca_path, staging_dir)
            _validate_artifact_against_manifest(ca_artifact, ca_manifest, expected_sha=None, label="corporate actions")
            artifacts["corporate_actions"] = {
                **_artifact_receipt(
                    ca_artifact, logical_id="equity_corporate_actions_v5", provenance_class="external_untracked"
                ),
                "manifest_git_blob": _tracked_blob_identity(ca_manifest_path),
            }
            receipt["corporate_action_inventory"] = _audit_corporate_actions(ca_path, ca_manifest, ca_artifact)
            _set_gate(gates, "corporate_action_identity", GATE_PASS, evidence=artifacts["corporate_actions"])
        except (GateError, OSError) as exc:
            _set_gate(gates, "corporate_action_identity", GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))

        if args.iwm_bundle is None:
            reason = "no operator-supplied IWM raw bundle was provided"
            for name in ("iwm_raw_bundle", "iwm_provider_contract", "iwm_pagination_chain", "iwm_normalization"):
                _set_gate(gates, name, GATE_NOT_RUN, reason=reason)
            receipt["iwm_raw_source"] = {"status": GATE_NOT_RUN, "reason": reason}
            _add_reason(reasons, reason)
        else:
            try:
                iwm_rows, iwm_audit = validate_iwm_bundle(args.iwm_bundle, staging_dir)
                receipt["iwm_raw_source"] = {"status": GATE_PASS, **_compact_iwm_audit(iwm_audit)}
                for name in ("iwm_raw_bundle", "iwm_provider_contract", "iwm_pagination_chain", "iwm_normalization"):
                    _set_gate(gates, name, GATE_PASS, evidence={"source_identity": iwm_audit.get("source_identity")})
            except (GateError, OSError) as exc:
                iwm_audit = {
                    "status": GATE_FAIL,
                    "category": getattr(exc, "category", "source_error"),
                    "reason": str(exc),
                }
                receipt["iwm_raw_source"] = iwm_audit
                for name in ("iwm_raw_bundle", "iwm_provider_contract", "iwm_pagination_chain", "iwm_normalization"):
                    _set_gate(gates, name, GATE_FAIL, reason=str(exc))
                _add_reason(reasons, str(exc))

        if args.pit_universe is None:
            reason = "no deterministic PIT scanner-universe input was supplied"
            _set_gate(gates, "deterministic_numerical_population", GATE_NOT_RUN, reason=reason)
            receipt["deterministic_numerical_population"] = {"status": GATE_NOT_RUN, "methodology": "not executed"}
            _add_reason(reasons, reason)
        else:
            try:
                population, pit_artifact = _load_pit_population(Path(args.pit_universe), staging_dir)
                receipt["deterministic_numerical_population"] = {
                    key: value for key, value in population.items() if key not in {"snapshots", "candidate_symbols"}
                }
                receipt["deterministic_numerical_population"]["input_artifact"] = pit_artifact
                _set_gate(
                    gates,
                    "deterministic_numerical_population",
                    GATE_PASS,
                    evidence=receipt["deterministic_numerical_population"],
                )
                if canonical_artifact is not None:
                    candidate_symbols = tuple(str(item) for item in population["candidate_symbols"])
                    canonical_rows, quality = load_csv_reference_rows(
                        canonical_artifact.staged_path,
                        reference_symbols=tuple(dict.fromkeys((*REFERENCE_SYMBOLS, *candidate_symbols))),
                    )
            except (GateError, OSError) as exc:
                _set_gate(gates, "deterministic_numerical_population", GATE_FAIL, reason=str(exc))
                receipt["deterministic_numerical_population"] = {"status": GATE_FAIL, "reason": str(exc)}
                _add_reason(reasons, str(exc))

        panel_rows: dict[str, list[object]] = {
            symbol: list(canonical_rows.get(symbol, ())) for symbol in REFERENCE_SYMBOLS
        }
        if iwm_rows:
            canonical_iwm = tuple(canonical_rows.get("IWM", ()))
            try:
                merged_iwm, merge_hashes = merge_normalized_rows(canonical_iwm, iwm_rows)  # type: ignore[arg-type]
                panel_rows["IWM"] = list(merged_iwm)
                receipt["merge_integrity"] = {
                    "status": GATE_PASS,
                    "existing_canonical_iwm_rows": len(canonical_iwm),
                    "supplied_normalized_iwm_rows": len(iwm_rows),
                    **merge_hashes,
                }
            except GateError as exc:
                receipt["merge_integrity"] = {"status": GATE_FAIL, "reason": str(exc)}
                _set_gate(gates, "composite_dataset_identity", GATE_FAIL, reason=str(exc))
                _add_reason(reasons, str(exc))
        else:
            receipt["merge_integrity"] = {"status": GATE_NOT_RUN, "reason": "IWM source not available"}

        try:
            expected_sessions = expected_exchange_sessions(SOURCE_START, EVALUATION_END)
            source_identity = {
                "provider": SOURCE_PROVIDER,
                "api_family_version": SOURCE_API_FAMILY_VERSION,
                "endpoint_identity": SOURCE_ENDPOINT,
                "feed": SOURCE_FEED,
                "adjustment": SOURCE_ADJUSTMENT,
                "source_timeframe": CANONICAL_TIMEFRAME,
                "sort": SOURCE_SORT,
            }
            inventory = {
                symbol: build_reference_inventory(
                    panel_rows.get(symbol, []),
                    raw_quality=quality.get(symbol),
                    expected_sessions=expected_sessions,
                    evaluation_end=EVALUATION_END,
                    source_identity=source_identity,
                )
                for symbol in REFERENCE_SYMBOLS
            }
            common = common_session_report(
                panel_rows, expected_sessions=expected_sessions, evaluation_end=EVALUATION_END
            )
            weekly_full = weekly_decision_coverage(
                panel_rows, expected_sessions=expected_sessions, start=SOURCE_START, end=EVALUATION_END
            )
            weekly_recent = weekly_decision_coverage(
                panel_rows, expected_sessions=expected_sessions, start=date(2022, 1, 1), end=EVALUATION_END
            )
            receipt["reference_inventory"] = _compact_inventory(inventory)
            receipt["row_rejection_inventory"] = {
                symbol: {
                    "raw_reference_rows": item.get("raw_reference_rows", 0),
                    "normalized_reference_rows": item.get("normalized_reference_rows", 0),
                    "post_evaluation_valid_rows": item.get("post_evaluation_valid_rows", 0),
                    "rejected_row_count": item.get("rejected_row_count", 0),
                    "rejection_counts": item.get("rejection_counts", {}),
                }
                for symbol, item in inventory.items()
            }
            receipt["source_completeness_through_2026_07_31"] = _source_completeness(
                panel_rows, start=SOURCE_START, end=SOURCE_END
            )
            receipt["evaluation_completeness_through_2026_07_30"] = _compact_common(common)
            receipt["common_session_coverage"] = {
                **_compact_common(common),
                "common_sessions_sha256": session_dates_digest(common["common_sessions"]),
            }
            receipt["weekly_coverage"] = {
                "full": _compact_weekly(weekly_full),
                "recent": _compact_weekly(weekly_recent),
            }
            receipt["reference_panel"] = {
                "normalized_reference_panel_sha256": reference_panel_digest(
                    [row for symbol in REFERENCE_SYMBOLS for row in panel_rows.get(symbol, ())],
                    source_identity=source_identity,
                ),
                "evaluation_reference_panel_sha256": reference_panel_digest(
                    [
                        row
                        for symbol in REFERENCE_SYMBOLS
                        for row in panel_rows.get(symbol, ())
                        if row.session_date <= EVALUATION_END  # type: ignore[attr-defined]
                    ],
                    source_identity=source_identity,
                ),
            }
            receipt["calendar_identity"] = calendar_dependency_identity()
            source_complete = bool(receipt["source_completeness_through_2026_07_31"]["complete"])
            reference_ok = _reference_gate_pass(inventory, common, weekly_full, weekly_recent) and source_complete
            _set_gate(
                gates,
                "reference_calendar",
                GATE_PASS if source_complete else GATE_FAIL,
                reason=None if source_complete else "reference source completeness through 2026-07-31 failed",
            )
            _set_gate(
                gates,
                "reference_row_quality",
                GATE_PASS if reference_ok else GATE_FAIL,
                reason=None if reference_ok else "reference row quality or source completeness failed",
            )
            _set_gate(
                gates,
                "reference_common_sessions",
                GATE_PASS if int(common["common_session_count"]) >= 274 else GATE_FAIL,
                common_session_count=common["common_session_count"],
            )
            _set_gate(
                gates,
                "weekly_coverage",
                GATE_PASS if reference_ok else GATE_FAIL,
                reason=None if reference_ok else "weekly/common/reference coverage failed",
            )
        except (GateError, OSError) as exc:
            for name in ("reference_calendar", "reference_row_quality", "reference_common_sessions", "weekly_coverage"):
                _set_gate(gates, name, GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))
            inventory = {symbol: {} for symbol in REFERENCE_SYMBOLS}
            common = {"common_session_count": 0, "common_sessions": []}
            weekly_full = {"decisions": [], "ever_feasible": False}
            weekly_recent = {"decisions": [], "ever_feasible": False}

        try:
            overlay = build_reference_overlay(config_data)
            role_receipt = overlay.to_dict()
            role_receipt["original_configured_trade_symbols"] = sorted(overlay.original_configured_trade_symbols)
            role_receipt["research_effective_trade_symbols"] = sorted(overlay.research_effective_trade_symbols)
            role_receipt["removed_reference_symbols"] = sorted(overlay.removed_reference_symbols)
            if population is not None:
                role_receipt["pit_universe_evidence"] = validate_pit_reference_boundary(population)
                receipt["reference_role_evidence_levels"]["pit_universe"] = "PIT_UNIVERSE_VERIFIED"  # type: ignore[index]
            receipt["reference_role_separation"] = role_receipt
            _set_gate(gates, "reference_role_contract", GATE_PASS, evidence=role_receipt)
        except GateError as exc:
            receipt["reference_role_separation"] = {"status": GATE_FAIL, "reason": str(exc)}
            _set_gate(gates, "reference_role_contract", GATE_FAIL, reason=str(exc))
            _add_reason(reasons, str(exc))

        if iwm_rows and gates["iwm_raw_bundle"]["status"] == GATE_PASS and canonical_artifact is not None:
            try:
                sidecar_path = Path(args.iwm_normalized_out) if args.iwm_normalized_out else None
                if sidecar_path is None:
                    raise GateError("normalized_sidecar_missing", "--iwm-normalized-out is required for a composite")
                sidecar = write_normalized_sidecar(list(iwm_rows), sidecar_path)  # type: ignore[arg-type]
                sidecar_logical_path = _logical_path(sidecar_path)
                if sidecar_logical_path.startswith("<external:"):
                    raise GateError("sidecar_path_unbound", "normalized sidecar must be under the repository")
                sidecar["path"] = sidecar_logical_path
                canonical_manifest_identity = _tracked_blob_identity(Path(args.canonical_manifest))
                normalizer_blob = (
                    provenance.get("critical_git_blobs", {}).get(NORMALIZER_PATH)
                    if isinstance(provenance.get("critical_git_blobs"), Mapping)
                    else None
                )
                if canonical_manifest_identity is None or not isinstance(normalizer_blob, Mapping):
                    raise GateError("composite_provenance_missing", "composite Git identities are incomplete")
                identity = composite_dataset_identity(
                    canonical_parent_raw_sha256=canonical_artifact.raw_sha256,  # type: ignore[union-attr]
                    canonical_parent_normalized_sha256=canonical_artifact.crlf_normalized_sha256,  # type: ignore[union-attr]
                    canonical_manifest_git_blob=str(canonical_manifest_identity["git_blob_id"]),
                    iwm_raw_bundle_sha256=str(iwm_audit["raw_bundle_sha256"]),
                    iwm_normalized_raw_sha256=str(sidecar["raw_sha256"]),
                    iwm_normalized_semantic_row_sha256=str(sidecar["normalized_semantic_row_sha256"]),
                    normalizer_git_blob=str(normalizer_blob["git_blob_id"]),
                    normalizer_sha256=str(normalizer_blob["raw_sha256"]),
                    schema_version=NORMALIZER_VERSION,
                    canonical_timeframe=CANONICAL_TIMEFRAME,
                    source_feed=SOURCE_FEED,
                    adjustment_mode=SOURCE_ADJUSTMENT,
                    exchange_timezone=EXCHANGE_TIMEZONE,
                    calendar_identity=calendar_dependency_identity(),
                    source_range={"start": SOURCE_START.isoformat(), "end": SOURCE_END.isoformat()},
                    evaluation_range={"end_inclusive": EVALUATION_END.isoformat()},
                )
                composite_manifest_payload = {
                    "schema_version": "1.0.0",
                    "mission": "equity_m3_composite_dataset_2026_09",
                    "parent_dataset": "canonical_equity_v5_bars",
                    "iwm_sidecar": sidecar,
                    "identity": identity,
                }
                composite_manifest_out = Path(args.composite_manifest_out) if args.composite_manifest_out else None
                if composite_manifest_out is None:
                    raise GateError(
                        "composite_manifest_missing", "--composite-manifest-out is required for a composite"
                    )
                if _logical_path(composite_manifest_out).startswith("<external:"):
                    raise GateError(
                        "composite_manifest_path_unbound", "composite manifest must be under the repository"
                    )
                if composite_manifest_out.exists() and _load_json(composite_manifest_out) != composite_manifest_payload:
                    raise GateError(
                        "composite_manifest_conflict", "composite manifest already contains different identity"
                    )
                if not composite_manifest_out.exists():
                    composite_manifest_out.parent.mkdir(parents=True, exist_ok=True)
                    composite_manifest_out.write_text(
                        json.dumps(composite_manifest_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                    )
                receipt["composite_dataset_identity"] = {
                    "status": GATE_PASS,
                    "manifest_path": _logical_path(composite_manifest_out),
                    "manifest_sha256": _sha256(composite_manifest_out),
                    "identity": identity,
                    "sidecar": sidecar,
                }
                _set_gate(gates, "composite_dataset_identity", GATE_PASS, evidence=identity)
            except (GateError, OSError) as exc:
                receipt["composite_dataset_identity"] = {"status": GATE_FAIL, "reason": str(exc)}
                _set_gate(gates, "composite_dataset_identity", GATE_FAIL, reason=str(exc))
                _add_reason(reasons, str(exc))
        elif gates["composite_dataset_identity"]["status"] == GATE_UNKNOWN:
            _set_gate(gates, "composite_dataset_identity", GATE_NOT_RUN, reason="valid IWM raw bundle is required")
            receipt["composite_dataset_identity"] = {"status": GATE_NOT_RUN}

        if population is not None and gates["deterministic_numerical_population"]["status"] == GATE_PASS:
            candidate_symbols = tuple(str(item) for item in population["candidate_symbols"])
            prerequisite_names = ("reference_common_sessions", "weekly_coverage", "composite_dataset_identity")
            if all(gates[name]["status"] == GATE_PASS for name in prerequisite_names):
                thresholds = contract.get("numerical_thresholds", DEFAULT_NUMERICAL_THRESHOLDS)
                if not isinstance(thresholds, Mapping):
                    thresholds = DEFAULT_NUMERICAL_THRESHOLDS
                matrix_summary = _matrix_audit(panel_rows, weekly_recent, candidate_symbols, thresholds, population)
                receipt["design_matrix_summary"] = {
                    key: value for key, value in matrix_summary.items() if key != "results"
                }
                matrix_ok = (
                    int(matrix_summary["total_matrix_count"]) > 0
                    and int(matrix_summary["pass_count"]) == int(matrix_summary["total_matrix_count"])
                    and int(matrix_summary["fail_count"]) == 0
                )
                _set_gate(
                    gates,
                    "design_matrix_feasibility",
                    GATE_PASS if matrix_ok else GATE_FAIL,
                    reason=None if matrix_ok else "matrix population is empty or contains failed audits",
                    total_matrix_count=matrix_summary["total_matrix_count"],
                    pass_count=matrix_summary["pass_count"],
                    fail_count=matrix_summary["fail_count"],
                )
                if not matrix_ok:
                    _add_reason(reasons, "design-matrix feasibility gate failed")
            else:
                reason = "design matrix phase not run because prerequisite data gates failed"
                _set_gate(gates, "design_matrix_feasibility", GATE_NOT_RUN, reason=reason)
                _add_reason(reasons, reason)
        else:
            reason = "deterministic numerical population or IWM/reference prerequisites are not ready"
            _set_gate(gates, "design_matrix_feasibility", GATE_NOT_RUN, reason=reason)
            _add_reason(reasons, reason)

        receipt["input_provenance"] = artifacts
        if args.diagnostic_candidate_symbol:
            diagnostic = sorted(
                {str(symbol).strip().upper() for symbol in args.diagnostic_candidate_symbol if str(symbol).strip()}
            )
            receipt["diagnostic_only"] = True
            receipt["diagnostic_candidate_symbols"] = diagnostic
            _add_reason(reasons, "diagnostic candidate symbols cannot contribute to PASS")
        else:
            receipt["diagnostic_only"] = False
        receipt["mandatory_gate_results"] = gates
        receipt["data_gate_verdict"] = evaluate_mandatory_gates(gates)
        receipt["next_required_gate"] = (
            "M3_CANDIDATE_OCP_GATE"
            if receipt["data_gate_verdict"] == "M3_DATA_GATE_PASS"
            else "M3_DATA_GATE_COMPLETION"
        )
        receipt["m3_candidate_ocp_status"] = "NOT_RUN_OUT_OF_SCOPE"
        receipt["economic_mission_ready"] = False

    dirty_after = _dirty_paths()
    receipt["worktree_cleanliness"]["after"] = dirty_after  # type: ignore[index]
    receipt["worktree_cleanliness"]["clean_after"] = not dirty_after  # type: ignore[index]
    receipt["blocked_reasons"] = sorted(set(reasons))
    if receipt["data_gate_verdict"] != "M3_DATA_GATE_PASS":
        receipt["corrective_infrastructure_verdict"] = (
            "PASS"
            if all(
                gates[name]["status"] == GATE_PASS
                for name in (
                    "commit_topology",
                    "implementation_blob_identity",
                    "clean_worktree",
                    "configuration_identity",
                    "dependency_identity",
                )
            )
            else "BLOCKED"
        )
    receipt["forbidden_economic_fields"] = list(forbidden_receipt_keys(receipt))
    if receipt["forbidden_economic_fields"]:
        _set_gate(gates, "receipt_forbidden_fields", GATE_FAIL, reason="receipt contains forbidden economic fields")
        receipt["mandatory_gate_results"] = gates
        receipt["data_gate_verdict"] = "M3_DATA_GATE_BLOCKED"
        _add_reason(reasons, "receipt contains forbidden economic fields")
    else:
        _set_gate(gates, "receipt_forbidden_fields", GATE_PASS, evidence={"fields": []})
        receipt["mandatory_gate_results"] = gates
    receipt["blocked_reasons"] = sorted(set(reasons))
    receipt["self_digest_sha256"] = canonical_digest(receipt)
    output_path = Path(args.receipt_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(_safe(receipt), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return receipt, 0 if receipt["data_gate_verdict"] == "M3_DATA_GATE_PASS" else EXIT_BLOCKED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-bars", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--iwm-bundle", type=Path)
    parser.add_argument("--iwm-normalized-out", type=Path)
    parser.add_argument("--composite-manifest-out", type=Path)
    parser.add_argument("--pit-universe", type=Path)
    parser.add_argument("--pit-manifest", type=Path)
    parser.add_argument("--lifetimes", type=Path, required=True)
    parser.add_argument("--lifetimes-expected-sha256")
    parser.add_argument("--corporate-actions", type=Path, required=True)
    parser.add_argument("--corporate-actions-manifest", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument("--diagnostic-candidate-symbol", action="append", default=[])
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt, status = run_gate(args)
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        print(f"M3_DATA_GATE_BLOCKED: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_ERROR
    print(json.dumps(_safe(receipt), indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
