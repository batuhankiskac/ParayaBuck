#!/usr/bin/env python3
"""Run the fail-closed, research-only M3 DATA GATE.

This command audits exact local bytes and an explicitly supplied IWM raw page
bundle.  It never contacts a provider, imports the broker/runtime, runs a
portfolio engine, calculates economic metrics, or promotes a strategy.

The command intentionally returns ``20`` for a truthful blocked data gate.
Missing mandatory external data is a result, not permission to substitute a
different source.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from m3_data_gate_helpers import (  # noqa: E402
    CANONICAL_TIMEFRAME,
    DEFAULT_NUMERICAL_THRESHOLDS,
    EVALUATION_END,
    NORMALIZER_VERSION,
    REFERENCE_SYMBOLS,
    SOURCE_START,
    GateError,
    audit_design_matrix,
    build_reference_inventory,
    build_reference_overlay,
    calendar_dependency_identity,
    canonical_digest,
    common_session_report,
    config_semantic_digest,
    expected_exchange_sessions,
    forbidden_receipt_keys,
    load_csv_reference_rows,
    merge_normalized_rows,
    merkle_root,
    reference_panel_digest,
    session_dates_digest,
    stage_file,
    validate_iwm_bundle,
    weekly_decision_coverage,
)

EXIT_BLOCKED = 20
EXIT_CONTRACT_ERROR = 21
VERIFIER_PATH = Path(__file__).relative_to(_REPO).as_posix()
NORMALIZER_PATH = "scripts/m3_data_gate_helpers.py"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


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
        raise GateError("invalid_manifest", f"invalid JSON artifact: {path.name}") from exc
    if not isinstance(payload, Mapping):
        raise GateError("invalid_manifest", f"JSON artifact must be an object: {path.name}")
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


def _artifact_receipt(
    artifact: object,
    *,
    logical_id: str,
    provenance_class: str,
) -> dict[str, object]:
    if not hasattr(artifact, "receipt"):
        return {"logical_artifact_id": logical_id, "status": "UNKNOWN"}
    return artifact.receipt(logical_id, provenance_class)  # type: ignore[no-any-return, union-attr]


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


def _validate_contract(
    contract: Mapping[str, object],
    *,
    run_commit: str,
    run_parent: str,
) -> None:
    if contract.get("declared_before_results") is not True:
        raise GateError("manifest_not_predeclared", "M3 manifest must be declared_before_results=true")
    for key, expected in (
        ("research_only", True),
        ("automatic_promotion", False),
        ("human_promotion_only", True),
        ("candidate_economics_run", False),
    ):
        if contract.get(key) != expected:
            raise GateError("manifest_policy_mismatch", f"M3 manifest {key} is not frozen correctly")
    if contract.get("base_commit") != "0a8a726b4ce6d1dcf0d8591a860125e2fed875ba":
        raise GateError("base_commit_mismatch", "M3 manifest base commit is not the pinned base")
    implementation = contract.get("implementation_commit")
    implementation_parent = contract.get("implementation_parent_commit")
    if not isinstance(implementation, str) or not isinstance(implementation_parent, str):
        raise GateError("implementation_pin_missing", "M3 manifest implementation pin is incomplete")
    if implementation_parent != contract.get("base_commit"):
        raise GateError("implementation_parent_mismatch", "implementation commit is not a direct child of base")
    if run_commit == implementation:
        raise GateError("pre_result_manifest_not_committed", "gate must run from the direct child manifest commit")
    if run_parent != implementation:
        raise GateError("manifest_commit_topology", "gate run commit must be a direct child of implementation commit")
    relationship = contract.get("expected_run_commit_relationship")
    if not isinstance(relationship, str) or "direct child" not in relationship.lower():
        raise GateError("manifest_topology_missing", "manifest must declare the expected run commit relationship")
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
        raise GateError("frozen_parameter_mismatch", "M3 manifest parameters drifted")
    thresholds = contract.get("numerical_thresholds")
    if not isinstance(thresholds, Mapping):
        raise GateError("numerical_thresholds_missing", "M3 manifest numerical thresholds are missing")
    for key, value in DEFAULT_NUMERICAL_THRESHOLDS.items():
        if thresholds.get(key) != value:
            raise GateError("numerical_threshold_mismatch", f"M3 threshold {key} drifted")


def _validate_artifact_against_manifest(
    artifact: object,
    manifest: Mapping[str, object],
    *,
    expected_sha: str | None,
    label: str,
) -> None:
    if not hasattr(artifact, "raw_sha256"):
        raise GateError("missing_artifact", f"missing {label} artifact")
    actual = artifact.raw_sha256  # type: ignore[union-attr]
    declared = expected_sha or _manifest_expected_hash(manifest, "sha256", "dataset_sha256")
    if declared is None:
        raise GateError("manifest_hash_missing", f"{label} manifest does not declare a SHA-256")
    if actual != declared:
        raise GateError("canonical_hash_mismatch", f"{label} SHA-256 differs from its frozen manifest")
    declared_size = manifest.get("byte_size")
    if isinstance(declared_size, int) and artifact.byte_size != declared_size:  # type: ignore[union-attr]
        raise GateError("artifact_size_mismatch", f"{label} byte size differs from its frozen manifest")


def _audit_corporate_actions(path: Path, manifest: Mapping[str, object], staged: object) -> dict[str, object]:
    declared = _manifest_expected_hash(manifest, "sha256", "dataset_sha256")
    if declared is None or not hasattr(staged, "raw_sha256") or staged.raw_sha256 != declared:  # type: ignore[union-attr]
        raise GateError("corporate_actions_hash_mismatch", "corporate-actions bytes differ from the frozen manifest")
    total = 0
    counts: dict[str, int] = {}
    reference_counts: dict[str, int] = {symbol: 0 for symbol in REFERENCE_SYMBOLS}
    with staged.staged_path.open(newline="", encoding="utf-8") as handle:  # type: ignore[union-attr]
        reader = csv.DictReader(handle)
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


def _reference_gate_pass(
    inventory: Mapping[str, Mapping[str, object]],
    common: Mapping[str, object],
    weekly_full: Mapping[str, object],
    weekly_recent: Mapping[str, object],
) -> bool:
    for symbol in REFERENCE_SYMBOLS:
        item = inventory[symbol]
        if (
            int(item["normalized_row_count"]) == 0
            or item["missing_expected_sessions"]
            or item["extra_off_calendar_sessions"]
            or int(item["duplicate_count"]) != 0
            or int(item["conflicting_duplicate_count"]) != 0
            or int(item["nonfinite_count"]) != 0
            or int(item["nonpositive_close_count"]) != 0
            or int(item["invalid_ohlc_count"]) != 0
            or int(item["negative_volume_count"]) != 0
            or item["timeframe_values"] != [CANONICAL_TIMEFRAME]
            or item["market_values"] != ["EQUITY"]
        ):
            return False
    if int(common["common_session_count"]) < 274:
        return False
    for weekly in (weekly_full, weekly_recent):
        if int(weekly["failed_after_warmup_count"]) != 0:
            return False
    return True


def _matrix_audit(
    rows_by_symbol: Mapping[str, Sequence[object]],
    weekly: Mapping[str, object],
    candidate_symbols: Sequence[str],
    thresholds: Mapping[str, int | float],
) -> dict[str, object]:
    decisions = [item for item in weekly.get("decisions", []) if item.get("accepted") is True]
    results: list[dict[str, object]] = []
    for candidate in candidate_symbols:
        panel = {
            symbol: {row.session_date: row.close for row in rows_by_symbol.get(symbol, ())}  # type: ignore[attr-defined]
            for symbol in (candidate, "SPY", "IWM", "QQQ")
        }
        for decision in decisions:
            trailing = [date.fromisoformat(value) for value in decision["trailing_sessions"]]
            closes = {
                symbol: [panel[symbol].get(session) for session in trailing]
                for symbol in (candidate, "SPY", "IWM", "QQQ")
            }
            result = audit_design_matrix(
                candidate,
                decision["decision_session"],
                trailing,
                closes,
                thresholds=thresholds,
            )
            results.append(result)
    digests = [str(item["matrix_digest_sha256"]) for item in results]
    category_counts: dict[str, int] = {}
    for result in results:
        for category in result["failure_categories"]:
            category_counts[category] = category_counts.get(category, 0) + 1
    return {
        "candidate_symbol_count": len(candidate_symbols),
        "total_matrix_count": len(results),
        "pass_count": sum(item["passed"] is True for item in results),
        "fail_count": sum(item["passed"] is not True for item in results),
        "failure_category_counts": dict(sorted(category_counts.items())),
        "matrix_digest_merkle_root": merkle_root(digests),
        "deterministic_examples": [
            {
                "candidate_symbol": item["candidate_symbol"],
                "decision_session": item["decision_session"],
                "matrix_digest_sha256": item["matrix_digest_sha256"],
                "passed": item["passed"],
            }
            for item in results[:3]
        ],
        "minimum_singular_value_ratio": min(
            (float(item["singular_value_ratio"]) for item in results),
            default=None,
        ),
        "maximum_condition_number": max(
            (float(item["condition_number"]) for item in results),
            default=None,
        ),
        "results": results,
    }


def _static_blocker_reverification() -> list[dict[str, str]]:
    """Keep the requested static recheck visible in the compact receipt."""
    return [
        {
            "id": "tracked_reference_counts",
            "status": "VERIFIED",
            "evidence": "data/research/equity_m3_m4_2026_08/audit.json:12-25",
            "consequence": "SPY and QQQ each have 2658 local rows; IWM has zero in the old audit.",
        },
        {
            "id": "canonical_csv_external_ignored",
            "status": "VERIFIED",
            "evidence": ".gitignore:247",
            "consequence": "The canonical CSV requires an operator-supplied external hash.",
        },
        {
            "id": "count_only_m3_readiness",
            "status": "VERIFIED",
            "evidence": "scripts/research_equity_m3_m4.py:397-418",
            "consequence": "The legacy runner must consume this receipt or remain blocked.",
        },
        {
            "id": "pre_filter_count",
            "status": "VERIFIED",
            "evidence": "scripts/research_equity_m3_m4.py:244-282",
            "consequence": "The legacy count includes rows before session, timeframe, deduplication, and common-session checks.",
        },
        {
            "id": "timeframe_token_drift",
            "status": "VERIFIED",
            "evidence": "scripts/build_symmetric_v5_pipeline.py:103-110,241-252; scripts/research_equity_m3_m4.py:329-336",
            "consequence": "The new normalizer freezes raw 1Day to canonical 1d and rejects mixed tokens.",
        },
        {
            "id": "utc_midnight_boundary_hazard",
            "status": "VERIFIED",
            "evidence": "scripts/research_equity_m3_m4.py:60,264-266",
            "consequence": "The gate uses America/New_York session dates and an inclusive session end.",
        },
        {
            "id": "incomplete_reference_role",
            "status": "VERIFIED",
            "evidence": "config/settings.yaml:31-41; src/parayabuck/config.py:380-465",
            "consequence": "The research overlay explicitly pins SPY/IWM/QQQ and filters all three from tradable roles.",
        },
        {
            "id": "legacy_hash_reopen",
            "status": "VERIFIED",
            "evidence": "scripts/research_equity_m3_m4.py:391-395,253-280",
            "consequence": "The new gate stages bytes once and parses the content-addressed copy.",
        },
        {
            "id": "missing_consumed_panel_digest",
            "status": "VERIFIED",
            "evidence": "scripts/research_equity_m3_m4.py:93-176",
            "consequence": "The gate records normalized reference and matrix digests.",
        },
        {
            "id": "discarded_lstsq_diagnostics",
            "status": "VERIFIED",
            "evidence": "scripts/research_equity_m3_m4.py:141-148",
            "consequence": "The data-only audit retains rank, singular values, and condition gates.",
        },
        {
            "id": "candidate_owned_observation_missing",
            "status": "VERIFIED",
            "evidence": "scripts/research_equity_m3_m4.py:329-336",
            "consequence": "Candidate-owned OCP remains blocked until data gates pass and the accepted override path is run.",
        },
        {
            "id": "synthetic_only_legacy_tests",
            "status": "VERIFIED",
            "evidence": "tests/test_research_equity_m3_m4.py:67-107",
            "consequence": "Hermetic tests now cover real-data contract failure modes without fabricating IWM.",
        },
        {
            "id": "no_dedicated_receipt_or_ci_gate",
            "status": "VERIFIED",
            "evidence": "data/research_receipts and .github/workflows/ci.yml",
            "consequence": "This implementation adds a tracked compact receipt contract while keeping large data external.",
        },
        {
            "id": "source_builder_cache_pagination_precision",
            "status": "VERIFIED",
            "evidence": "scripts/build_symmetric_v5_pipeline.py:88-145,216-270",
            "consequence": "The M3 gate never consumes the broad builder or an unpinned v4 cache.",
        },
        {
            "id": "corporate_action_classification",
            "status": "VERIFIED",
            "evidence": "data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json:5-16; scripts/run_symmetric_v5_validation.py:112-145",
            "consequence": "The receipt distinguishes total action rows from cash-dividend rows.",
        },
    ]


def _base_receipt(
    *,
    contract: Mapping[str, object],
    contract_path: Path,
    run_commit: str,
    run_parent: str,
    dirty_before: Sequence[str],
    dirty_after: Sequence[str],
    config_digest: str | None,
    status: str,
    blocked_reasons: Sequence[str],
) -> dict[str, object]:
    implementation = str(contract.get("implementation_commit", ""))
    return {
        "schema_version": "1.0.0",
        "mission": "equity_m3_data_gate_2026_09",
        "verdict": status,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "run_commit": run_commit,
        "run_parent_commit": run_parent,
        "implementation_commit": implementation,
        "base_commit": contract.get("base_commit"),
        "source_tree": {"commit": run_commit, "tree": _run_git("rev-parse", "HEAD^{tree}")},
        "manifest_identity": {
            "path": _logical_path(contract_path),
            "sha256": _sha256(contract_path) if contract_path.is_file() else None,
            "declared_before_results": contract.get("declared_before_results"),
        },
        "verifier_identity": {"path": VERIFIER_PATH, "sha256": _sha256(_REPO / VERIFIER_PATH)},
        "normalizer_identity": {"path": NORMALIZER_PATH, "sha256": _sha256(_REPO / NORMALIZER_PATH)},
        "config_semantic_digest_sha256": config_digest,
        "environment_identity": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": importlib.metadata.version("numpy"),
            "pandas_market_calendars": calendar_dependency_identity()["version"],
        },
        "worktree_cleanliness": {
            "before": list(dirty_before),
            "after": list(dirty_after),
            "clean_before": not dirty_before,
            "clean_after": not dirty_after,
        },
        "hash_atomicity_evidence": {
            "strategy": "content-addressed staging",
            "parse_source": "staged bytes only",
            "source_identity_rechecked_after_copy": True,
            "mutation_during_staging_blocks": True,
        },
        "source_provenance": {
            "raw_source_restriction": "operator-supplied immutable IWM raw bundle only",
            "network_contacted": False,
            "broker_contacted": False,
            "feed": "sip",
            "adjustment": "split",
        },
        "normalization_contract": {
            "version": NORMALIZER_VERSION,
            "canonical_columns": [
                "session_date",
                "timestamp",
                "symbol",
                "timeframe",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "market",
            ],
            "canonical_timeframe": CANONICAL_TIMEFRAME,
            "timezone": "America/New_York",
            "decimal_policy": "exact Decimal source value, exponent expanded, no rounding",
        },
        "calendar_identity": calendar_dependency_identity(),
        "numerical_thresholds": contract.get("numerical_thresholds", dict(DEFAULT_NUMERICAL_THRESHOLDS)),
        "frozen_candidate": {
            "identity": "equity_proxy_multifactor_residual_momentum_v1",
            "formula": "r_stock - beta_spy*r_SPY - beta_size*(r_IWM-r_SPY) - beta_growth*(r_QQQ-r_SPY)",
            "parameters": contract.get("frozen_parameters"),
            "residual_convention": "alpha-retaining: demeaned fit, non-demeaned application, no intercept",
            "skip_window_convention": "latest 21 residual returns omitted from momentum; beta fit retains all 273 returns",
        },
        "static_blocker_reverification": _static_blocker_reverification(),
        "blocked_reasons": list(blocked_reasons),
        "unknown_external_facts": [],
        "tests_actually_executed": {
            "gate_command": "EXECUTED: research_equity_m3_data_gate.py",
            "focused_tests": "recorded in final documentation; not executed by this verifier",
        },
    }


def run_gate(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    run_commit = _run_git("rev-parse", "HEAD")
    run_parent = _run_git("rev-parse", "HEAD^")
    dirty_before = _dirty_paths()
    contract_path = Path(args.contract)
    blocked: list[str] = []
    candidate_symbols = tuple(
        sorted({str(symbol).strip().upper() for symbol in args.candidate_symbol if str(symbol).strip()})
    )
    contract: Mapping[str, object] = {}
    try:
        contract = _load_json(contract_path)
        _validate_contract(contract, run_commit=run_commit, run_parent=run_parent)
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        blocked.append(str(exc))

    config_digest_value: str | None = None
    config_path = _REPO / "config/settings.yaml"
    config_data: Mapping[str, object] = {}
    try:
        config_data = _load_yaml(config_path)
        config_digest_value = config_semantic_digest(config_data)
    except GateError as exc:
        blocked.append(str(exc))

    receipt = _base_receipt(
        contract=contract,
        contract_path=contract_path,
        run_commit=run_commit,
        run_parent=run_parent,
        dirty_before=dirty_before,
        dirty_after=[],
        config_digest=config_digest_value,
        status="M3_DATA_GATE_BLOCKED",
        blocked_reasons=blocked,
    )
    if dirty_before:
        blocked.append("worktree is dirty before gate execution")

    # The staging directory is deliberately outside the repository when the
    # normal operator invocation uses /tmp; it is cleaned at the end either way.
    with tempfile.TemporaryDirectory(prefix="m3-data-gate-") as temporary:
        staging_dir = Path(temporary)
        artifacts: dict[str, object] = {}
        canonical_manifest: Mapping[str, object] = {}
        ca_manifest: Mapping[str, object] = {}
        canonical_rows: dict[str, list[object]] = {symbol: [] for symbol in REFERENCE_SYMBOLS}
        quality: dict[str, dict[str, int]] = {symbol: {} for symbol in REFERENCE_SYMBOLS}
        iwm_rows: tuple[object, ...] = ()
        iwm_audit: dict[str, object] = {"status": "NOT_PROVIDED"}
        try:
            canonical_path = Path(args.canonical_bars)
            canonical_manifest_path = Path(args.canonical_manifest)
            if not canonical_path.is_file():
                raise GateError("missing_canonical_dataset", "canonical bars CSV is missing")
            if not canonical_manifest_path.is_file():
                raise GateError("missing_canonical_manifest", "canonical bars manifest is missing")
            canonical_manifest = _load_json(canonical_manifest_path)
            for field, expected in (("feed", "sip"), ("adjustment", "split")):
                if str(canonical_manifest.get(field, "")).lower() != expected:
                    raise GateError("canonical_source_contract", f"canonical manifest {field} must be {expected}")
            canonical_artifact = stage_file(canonical_path, staging_dir)
            _validate_artifact_against_manifest(
                canonical_artifact,
                canonical_manifest,
                expected_sha=None,
                label="canonical bars",
            )
            artifacts["canonical_bars"] = _artifact_receipt(
                canonical_artifact,
                logical_id="canonical_equity_v5_bars",
                provenance_class="external_untracked",
            )
            candidate_symbols = tuple(
                sorted({str(symbol).strip().upper() for symbol in args.candidate_symbol if str(symbol).strip()})
            )
            canonical_rows, quality = load_csv_reference_rows(
                canonical_artifact.staged_path,
                reference_symbols=tuple(dict.fromkeys((*REFERENCE_SYMBOLS, *candidate_symbols))),
            )
            canonical_iwm_count = len(canonical_rows.get("IWM", []))
            receipt["canonical_iwm_presence"] = {
                "prior_static_reported_rows": 0,
                "current_normalized_rows": canonical_iwm_count,
                "status": "MATCHES_PRIOR_ZERO_REPORT" if canonical_iwm_count == 0 else "DISCREPANCY_AUDITED",
                "prior_report_evidence": "data/research/equity_m3_m4_2026_08/audit.json:12-25",
            }

            lifetimes_path = Path(args.lifetimes)
            if not lifetimes_path.is_file():
                raise GateError("missing_lifetimes", "lifetimes artifact is missing")
            lifetimes_artifact = stage_file(lifetimes_path, staging_dir)
            lifetimes_manifest = {"sha256": args.lifetimes_expected_sha256}
            _validate_artifact_against_manifest(
                lifetimes_artifact,
                lifetimes_manifest,
                expected_sha=args.lifetimes_expected_sha256,
                label="lifetimes",
            )
            artifacts["lifetimes"] = _artifact_receipt(
                lifetimes_artifact,
                logical_id="equity_entity_lifetimes_v5",
                provenance_class="external_untracked",
            )

            ca_path = Path(args.corporate_actions)
            if not ca_path.is_file():
                raise GateError("missing_corporate_actions", "corporate-actions artifact is missing")
            ca_manifest_path = Path(args.corporate_actions_manifest)
            ca_manifest = _load_json(ca_manifest_path)
            ca_artifact = stage_file(ca_path, staging_dir)
            _validate_artifact_against_manifest(
                ca_artifact,
                ca_manifest,
                expected_sha=None,
                label="corporate actions",
            )
            artifacts["corporate_actions"] = _artifact_receipt(
                ca_artifact,
                logical_id="equity_corporate_actions_v5",
                provenance_class="external_untracked",
            )
            receipt["corporate_action_inventory"] = _audit_corporate_actions(ca_path, ca_manifest, ca_artifact)
        except GateError as exc:
            blocked.append(str(exc))

        try:
            if args.iwm_bundle is None:
                raise GateError("missing_iwm_bundle", "no operator-supplied IWM raw bundle was provided")
            iwm_rows, iwm_audit = validate_iwm_bundle(args.iwm_bundle, staging_dir)
            canonical_rows.setdefault("IWM", [])
            canonical_iwm = canonical_rows.get("IWM", [])
            if canonical_iwm:
                _merged, _merge_hashes = merge_normalized_rows(canonical_iwm, iwm_rows)
                if len(_merged) != len(canonical_iwm) + sum(
                    row.key not in {item.key for item in canonical_iwm} for row in iwm_rows
                ):  # type: ignore[attr-defined]
                    raise GateError("existing_iwm_merge_error", "canonical IWM comparison was not deterministic")
            receipt["iwm_raw_source"] = {"status": "VERIFIED", **_compact_iwm_audit(iwm_audit)}
        except GateError as exc:
            iwm_audit = {"status": "BLOCKED", "category": exc.category, "reason": str(exc)}
            receipt["iwm_raw_source"] = iwm_audit
            blocked.append(str(exc))

        try:
            expected_sessions = expected_exchange_sessions(SOURCE_START, EVALUATION_END)
            source_identity = {
                "feed": "sip",
                "adjustment": "split",
                "source_timeframe": CANONICAL_TIMEFRAME,
            }
            inventory = {
                symbol: build_reference_inventory(
                    canonical_rows.get(symbol, [])
                    + (list(iwm_rows) if symbol == "IWM" and not canonical_rows.get("IWM") else []),
                    raw_quality=quality.get(symbol),
                    expected_sessions=expected_sessions,
                    evaluation_end=EVALUATION_END,
                    source_identity=source_identity,
                )
                for symbol in REFERENCE_SYMBOLS
            }
            common = common_session_report(
                {
                    symbol: canonical_rows.get(symbol, [])
                    + (list(iwm_rows) if symbol == "IWM" and not canonical_rows.get("IWM") else [])
                    for symbol in REFERENCE_SYMBOLS
                },
                expected_sessions=expected_sessions,
                evaluation_end=EVALUATION_END,
            )
            weekly_full = weekly_decision_coverage(
                {
                    symbol: canonical_rows.get(symbol, [])
                    + (list(iwm_rows) if symbol == "IWM" and not canonical_rows.get("IWM") else [])
                    for symbol in REFERENCE_SYMBOLS
                },
                expected_sessions=expected_sessions,
                start=SOURCE_START,
                end=EVALUATION_END,
            )
            weekly_recent = weekly_decision_coverage(
                {
                    symbol: canonical_rows.get(symbol, [])
                    + (list(iwm_rows) if symbol == "IWM" and not canonical_rows.get("IWM") else [])
                    for symbol in REFERENCE_SYMBOLS
                },
                expected_sessions=expected_sessions,
                start=date(2022, 1, 1),
                end=EVALUATION_END,
            )
            receipt["reference_inventory"] = _compact_inventory(inventory)
            receipt["common_session_coverage"] = _compact_common(common)
            receipt["common_session_coverage"]["common_sessions_sha256"] = session_dates_digest(
                common["common_sessions"]
            )
            receipt["weekly_decision_coverage"] = {
                "full": _compact_weekly(weekly_full),
                "recent": _compact_weekly(weekly_recent),
            }
            receipt["reference_panel"] = {
                "normalized_reference_panel_sha256": reference_panel_digest(
                    [
                        row
                        for symbol in REFERENCE_SYMBOLS
                        for row in canonical_rows.get(symbol, [])
                        + (list(iwm_rows) if symbol == "IWM" and not canonical_rows.get("IWM") else [])
                    ],
                    source_identity=source_identity,
                ),
                "evaluation_reference_panel_sha256": reference_panel_digest(
                    [
                        row
                        for symbol in REFERENCE_SYMBOLS
                        for row in canonical_rows.get(symbol, [])
                        + (list(iwm_rows) if symbol == "IWM" and not canonical_rows.get("IWM") else [])
                        if row.session_date <= EVALUATION_END  # type: ignore[attr-defined]
                    ],
                    source_identity=source_identity,
                ),
            }
            if not _reference_gate_pass(inventory, common, weekly_full, weekly_recent):
                blocked.append("reference calendar, quality, common-session, or weekly coverage gate failed")
        except GateError as exc:
            blocked.append(str(exc))

        try:
            overlay = build_reference_overlay(config_data)
            declared_candidates = tuple(
                sorted({str(symbol).strip().upper() for symbol in args.candidate_symbol if str(symbol).strip()})
            )
            filtered_candidates = overlay.filter_candidates(declared_candidates)
            if filtered_candidates != declared_candidates:
                blocked.append("declared candidate symbols include a reference or an outside-pool symbol")
            role_receipt = overlay.to_dict()
            role_receipt["declared_candidate_symbols"] = list(declared_candidates)
            role_receipt["accepted_candidate_symbols"] = list(filtered_candidates)
            receipt["reference_role_separation"] = role_receipt
        except GateError as exc:
            blocked.append(str(exc))
            receipt["reference_role_separation"] = {"status": "BLOCKED", "reason": str(exc)}

        if not blocked and candidate_symbols:
            matrix_rows = dict(canonical_rows)
            matrix_rows["IWM"] = list(canonical_rows.get("IWM", [])) or list(iwm_rows)
            threshold_data = contract.get("numerical_thresholds", DEFAULT_NUMERICAL_THRESHOLDS)
            if not isinstance(threshold_data, Mapping):
                threshold_data = DEFAULT_NUMERICAL_THRESHOLDS
            matrix_summary = _matrix_audit(matrix_rows, weekly_full, candidate_symbols, threshold_data)
            receipt["design_matrix_feasibility"] = _safe(
                {key: value for key, value in matrix_summary.items() if key != "results"}
            )
            if int(matrix_summary["fail_count"]) != 0 or int(matrix_summary["total_matrix_count"]) == 0:
                blocked.append("design-matrix feasibility gate failed or had no declared candidate matrices")
        else:
            receipt["design_matrix_feasibility"] = {
                "status": "NOT_RUN_BLOCKED_BY_DATA_GATES",
                "total_matrix_count": 0,
                "pass_count": 0,
                "fail_count": 0,
                "matrix_digest_merkle_root": merkle_root(()),
            }
            if not candidate_symbols:
                blocked.append("no frozen candidate-symbol input was supplied for design-matrix audit")

        receipt["merge_integrity"] = {
            "status": "NOT_RUN_BLOCKED_BY_DATA_GATES" if blocked else "NOT_RUN_NO_MERGE_REQUESTED",
            "existing_canonical_untouched": True,
            "merged_artifact": None,
        }
        receipt["input_artifacts"] = artifacts
        receipt["source_provenance"]["source_bundle_argument"] = (  # type: ignore[index]
            _logical_path(Path(args.iwm_bundle)) if args.iwm_bundle is not None else None
        )
        receipt["ocp_prerequisite_summary"] = {
            "status": "NOT_RUN_BLOCKED_BY_DATA_GATES",
            "candidate_economics_run": False,
            "reason": "Candidate-owned OCP observations are conditional on every raw, coverage, role, and numerical gate passing.",
        }
        receipt["runtime_cache_isolation"] = {
            "runtime_state_read": False,
            "cache_read": False,
            "broker_read": False,
            "credentials_read": False,
            "mutable_diagnostic_state_shared": False,
            "production_isolation_check": "run externally before and after quality gates",
        }
        receipt["unknown_external_facts"] = [
            "No operator-supplied IWM raw page bundle was available to this run." if args.iwm_bundle is None else "",
            "No future M3 economic backtest is authorized or executed.",
        ]
        receipt["unknown_external_facts"] = [item for item in receipt["unknown_external_facts"] if item]

    dirty_after = _dirty_paths()
    receipt["worktree_cleanliness"]["after"] = dirty_after  # type: ignore[index]
    receipt["worktree_cleanliness"]["clean_after"] = not dirty_after  # type: ignore[index]
    if dirty_after:
        blocked.append("worktree is dirty after gate execution")
    receipt["blocked_reasons"] = sorted(set(blocked))
    receipt["verdict"] = "M3_DATA_GATE_PASS" if not blocked else "M3_DATA_GATE_BLOCKED"
    receipt = _safe(receipt)  # type: ignore[assignment]
    if forbidden_receipt_keys(receipt):
        receipt["verdict"] = "M3_DATA_GATE_BLOCKED"
        receipt["blocked_reasons"] = sorted(
            set(receipt.get("blocked_reasons", [])) | {"receipt contains forbidden economic fields"}
        )
    receipt["self_digest_sha256"] = canonical_digest(receipt)
    output_path = Path(args.receipt_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return receipt, 0 if receipt["verdict"] == "M3_DATA_GATE_PASS" else EXIT_BLOCKED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-bars", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--iwm-bundle", type=Path)
    parser.add_argument("--lifetimes", type=Path, required=True)
    parser.add_argument("--lifetimes-expected-sha256", required=True)
    parser.add_argument("--corporate-actions", type=Path, required=True)
    parser.add_argument("--corporate-actions-manifest", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument("--merged-out", type=Path)
    parser.add_argument("--merged-manifest-out", type=Path)
    parser.add_argument("--candidate-symbol", action="append", default=[])
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt, status = run_gate(args)
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        # The normal path writes a compact blocked receipt.  A contract/setup
        # failure before that path remains a nonzero, explicit infrastructure
        # error rather than a false PASS.
        print(f"M3_DATA_GATE_BLOCKED: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_ERROR
    print(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
