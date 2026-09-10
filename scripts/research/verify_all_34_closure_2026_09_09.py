"""Read-only aggregate closure verifier for the frozen 34-candidate research queue."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research import build_strategy_queue_contract_2026_09_09 as definition_builder  # noqa: E402

DEFAULT_OUTPUT_DIR = REPO / "data/research_runs/all_34_research_closure_2026_09_09"
RECORD_NAME = "closure_record.json"
SUMMARY_NAME = "closure_summary.md"
PENDING = "PENDING_FREEZE"
UNSPECIFIED = "not_separately_specified_in_frozen_source"

EXPECTED_IDS = [*(f"H{i:02d}" for i in range(1, 17)), *(f"K{i:02d}" for i in range(1, 13)), *(f"X{i:02d}" for i in range(1, 7))]
DEFINITION_CONTRACT = "data/research_runs/strategy_queue_contract_2026_09_09/candidate_contracts.json"
DEFINITION_RECEIPT = "data/research_runs/strategy_queue_contract_2026_09_09/hash_receipt.json"
DEFINITION_SOURCE = "docs/research/strategy_queue_full_report_2026_09_06.md"

CURRENT_RECORDS: dict[str, tuple[str, tuple[str, ...]]] = {
    **{f"H{i:02d}": ("data/research_runs/equity_h01_h05_h14_stage_eligibility_2026_09_09_r18/stage_eligibility_record.json", ("frozen_contracts", f"H{i:02d}")) for i in range(1, 6)},
    "H14": ("data/research_runs/equity_h01_h05_h14_stage_eligibility_2026_09_09_r18/stage_eligibility_record.json", ("frozen_contracts", "H14")),
    **{f"H{i:02d}": ("data/research_runs/equity_secondary_causal_gate_2026_09_08/gate_report.json", ("cards", f"H{i:02d}")) for i in (*range(6, 14), 15, 16)},
    **{f"K{i:02d}": ("data/research_runs/crypto_queue_causal_adapters_2026_09_08/gate_report.json", ("cards", f"K{i:02d}")) for i in (1, 3, 4, 9, 11)},
    **{f"K{i:02d}": ("data/research_runs/crypto_secondary_causal_gate_2026_09_08/gate_report.json", ("cards", f"K{i:02d}")) for i in (2, 5, 6, 7, 8, 10, 12)},
    **{f"X{i:02d}": ("data/research/generated/crossasset_preflight_2026_09_08/results.json", ("cards", f"X{i:02d}")) for i in range(1, 7)},
}

CURRENT_FILES = [
    "docs/research/strategy_queue_full_report_2026_09_06.md",
    DEFINITION_CONTRACT,
    DEFINITION_RECEIPT,
    "data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r18.json",
    "data/research_runs/equity_h01_h05_h14_stage_eligibility_2026_09_09_r18/stage_eligibility_record.json",
    "data/research_runs/equity_secondary_causal_gate_2026_09_08/gate_report.json",
    "data/research_manifests/equity_secondary_causal_gate_2026_09_08.json",
    "data/research/external/equity_h06_local_normalization_2026_09_09/form4_transaction_rows.json",
    "data/research/external/equity_h06_local_normalization_2026_09_09/input_inventory.json",
    "data/research/external/equity_h06_local_normalization_2026_09_09/report.json",
    "data/research/external/equity_h06_local_normalization_2026_09_09/manifest.json",
    "data/research/external/equity_secondary_local_normalization_2026_09_09/normalization_manifest.json",
    "data/research_runs/equity_h11_nport_snapshot_2026_09_09/manifest.json",
    "data/research_runs/equity_h11_nport_snapshot_2026_09_09/normalization_report.json",
    "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json",
    "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/focused_test_collection_receipt.json",
    "data/research_manifests/crypto_queue_causal_adapters_2026_09_08.json",
    "data/research_runs/crypto_queue_causal_adapters_2026_09_08/gate_report.json",
    "data/research_manifests/crypto_secondary_causal_gate_2026_09_08.json",
    "data/research_runs/crypto_secondary_causal_gate_2026_09_08/gate_report.json",
    "data/research_runs/crypto_secondary_causal_gate_2026_09_08/validation_receipt.json",
    "data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json",
    "data/research_manifests/crossasset_preflight_2026_09_08.json",
    "data/research/generated/crossasset_preflight_2026_09_08/results.json",
    "data/research/generated/crossasset_preflight_2026_09_08/validation_receipt.json",
    "data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json",
    "data/research_runs/crossasset_x02_x06_2026_09_08/two_leg_ledger_report.json",
    "data/research/external/crossasset_public_2026_09_09/public_route_exhaustion/route_exhaustion_report.json",
]

MANIFESTS = [
    "data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r18.json",
    "data/research_manifests/equity_secondary_causal_gate_2026_09_08.json",
    "data/research_manifests/crypto_queue_causal_adapters_2026_09_08.json",
    "data/research_manifests/crypto_secondary_causal_gate_2026_09_08.json",
    "data/research_runs/equity_h11_nport_snapshot_2026_09_09/manifest.json",
    "data/research/external/equity_h06_local_normalization_2026_09_09/manifest.json",
]

MARKER_RE = re.compile(r"NEXT_PUBLIC_PROBE|BLOCKED_RETRY_REQUIRED|PROBE_REQUEST_ERROR|parser\s+pending|engineering\s+pending|(?<!PIT_)\bPENDING\b", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CANDIDATE_FIELDS = {
    "candidate_id",
    "source_path",
    "pointer",
    "status",
    "strategy_name",
    "purpose_definition",
    "purpose_definition_status",
    "mechanism",
    "feasibility",
    "unspecified_fields",
    "research_status",
    "evidence_summary",
    "evidence_counts",
    "pit_provenance",
    "terminal_decision",
    "economic_run",
    "eligible",
    "admitted",
    "promotion",
    "economic_violations",
    "definition_source",
    "result_source",
    "integration_candidate",
}


def _is_local_engineering_marker(marker: str) -> bool:
    return marker.upper() in {"NEXT_PUBLIC_PROBE", "PARSER PENDING", "ENGINEERING PENDING", "PENDING"}


def _current_retry_issues(repo: Path) -> list[str]:
    """Name current source records whose access result still needs a retry."""
    path = repo / "data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json"
    if not path.is_file():
        return []
    try:
        records = _load(path).get("records", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []
    issues: list[str] = []
    for record in records:
        if not isinstance(record, dict) or record.get("status") not in {"PROBE_REQUEST_ERROR", "BLOCKED_RETRY_REQUIRED"}:
            continue
        issues.append(
            "current_retry_marker:"
            f"{record.get('family')}:{record.get('source_id')}:status={record.get('status')}:"
            f"error={record.get('error_type')}:path={path}"
        )
    return issues


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _rel(repo: Path, path: Path) -> str:
    return str(path.relative_to(repo))


def _snapshot_files(paths: list[Path]) -> dict[str, tuple[str, int, int]]:
    """Capture bytes and metadata without assuming a path is under the repo."""
    snapshot: dict[str, tuple[str, int, int]] = {}
    for path in sorted(set(paths)):
        if path.is_file():
            stat = path.stat()
            snapshot[str(path)] = (sha256(path), stat.st_size, stat.st_mtime_ns)
    return snapshot


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _pointer(value: Any, pointer: tuple[str, ...]) -> Any:
    for key in pointer:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _is_archival(path: Path) -> bool:
    text = str(path).lower()
    return "/archive/" in text or "superseded" in path.name.lower() or "archival" in path.name.lower()


def snapshot_paths(repo: Path, paths: list[str]) -> dict[str, tuple[str, int, int]]:
    snapshot: dict[str, tuple[str, int, int]] = {}
    for relative in sorted(set(paths)):
        path = repo / relative
        if path.is_file():
            stat = path.stat()
            snapshot[relative] = (sha256(path), stat.st_size, stat.st_mtime_ns)
    return snapshot


def scan_markers(roots: tuple[Path, ...]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        paths = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in paths:
            if _is_archival(path) or path.suffix == ".pyc":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for match in MARKER_RE.finditer(line):
                    marker = match.group(0)
                    if marker.upper() == "PENDING" and "PIT_PENDING" in line:
                        continue
                    findings.append({"path": str(path), "line": str(number), "marker": marker})
    return findings


def _private_contact_findings(repo: Path, paths: list[str]) -> list[str]:
    findings: list[str] = []
    for relative in paths:
        path = repo / relative
        if not path.is_file() or path.suffix == ".pyc":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in EMAIL_RE.finditer(text):
            if match.group(0).lower().endswith(".invalid"):
                continue
            findings.append(f"{relative}:private_contact_literal")
    return findings


def verify_declared_hashes(manifest: dict[str, Any], base: Path) -> list[str]:
    mismatches: list[str] = []
    for section in ("inputs", "artifacts"):
        declarations = manifest.get(section, {})
        if not isinstance(declarations, dict):
            continue
        for relative, declaration in declarations.items():
            if not isinstance(declaration, dict) or not isinstance(declaration.get("sha256"), str):
                continue
            path = base / relative
            if not path.is_file() or sha256(path) != declaration["sha256"]:
                mismatches.append(str(relative))
    return mismatches


def _verify_manifest(path: Path, repo: Path) -> list[str]:
    if not path.is_file():
        return [f"missing_manifest:{_rel(repo, path)}"]
    try:
        manifest = _load(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest_read_error:{_rel(repo, path)}:{exc}"]
    issues = [f"manifest_hash_mismatch:{name}" for name in verify_declared_hashes(manifest, repo)]
    declared = manifest.get("manifest_sha256")
    if isinstance(declared, str):
        payload = dict(manifest)
        payload["manifest_sha256"] = PENDING
        candidates = {
            canonical_hash(payload),
            hashlib.sha256((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()).hexdigest(),
        }
        if declared not in candidates:
            issues.append(f"manifest_self_hash_mismatch:{_rel(repo, path)}")
    return issues


def _import(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot_import:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(label: str, function: Callable[[], Any]) -> dict[str, Any]:
    try:
        result = function()
        return {"label": label, "ok": bool(result is not False), "result": result if isinstance(result, (dict, str, int, bool)) else str(result)}
    except Exception as exc:  # noqa: BLE001 - aggregate must report every failed gate
        return {"label": label, "ok": False, "error": f"{type(exc).__name__}:{exc}"}


def component_checks(repo: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    def loaded(relative: str, name: str) -> Any:
        return _import(repo / relative, name)

    h01 = loaded("scripts/research/verify_equity_h01_h05_sec_filing_evidence_2026_09_09.py", "h01_closure_verify")
    checks.extend([
        _call("H01-H05.receipt", h01.verify_receipt),
        _call("H01-H05.catalog", h01.verify_catalog),
        _call("H01-H05.causal_inventory", h01.verify_causal_inventory),
    ])
    h06 = loaded("scripts/research/normalize_equity_h06_form4_2026_09_09.py", "h06_closure_verify")
    checks.append(_call("H06.manifest", h06.verify_manifest))
    local = loaded("scripts/research/normalize_equity_secondary_h08_h15_2026_09_09.py", "h08_h15_closure_verify")
    checks.append(_call("H08-H15.local_package", local.verify_package))
    h11 = loaded("scripts/research/normalize_h11_nport_snapshot_2026_09_09.py", "h11_closure_verify")
    checks.append(_call("H11.manifest", h11.verify))
    equity = loaded("scripts/research/equity_secondary_causal_gate_2026_09_08.py", "equity_gate_closure_verify")
    checks.append(_call("H07-H16.aggregate_manifest", equity.verify_manifest))
    adapter = loaded("scripts/research/crypto_queue_causal_adapters_2026_09_08.py", "crypto_adapter_closure_verify")
    checks.append(_call("K01-K04-K09-K11.adapter", adapter.verify))
    secondary = loaded("scripts/research/crypto_secondary_causal_gate_2026_09_08.py", "crypto_secondary_closure_verify")
    checks.append(_call("K02-K12.secondary_manifest", secondary.verify_manifest))
    preflight = loaded("scripts/research/crossasset_preflight_2026_09_08.py", "crossasset_preflight_closure_verify")
    checks.append(_call("X01-X06.preflight", preflight.verify))
    route = loaded("scripts/research/crossasset_public_route_exhaustion_2026_09_09.py", "crossasset_route_closure_verify")
    checks.append(_call("X01-X06.route_exhaustion", route.verify_report))
    receipt = loaded("scripts/research/crossasset_receipt_verifier_2026_09_08.py", "crossasset_receipt_closure_verify")
    matrix = repo / "data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json"
    checks.append(_call("X02-X06.source_matrix", lambda: receipt.verify_source_matrix(matrix)))
    x01 = repo / "data/research_manifests/crossasset_x01_yahoo_fallback_2026_09_08.json"
    checks.append(_call("X01.manifest", lambda: receipt.verify_x01_manifest(x01)))
    ledger = loaded("scripts/research/crossasset_two_leg_ledger_2026_09_08.py", "crossasset_ledger_closure_verify")
    checks.append(_call("X04-X06.two_leg", ledger.build_ledger_report))
    return checks


def _candidate_payload(record: Any, pointer: tuple[str, ...]) -> Any:
    return _pointer(record, pointer)


def _economic_violations(value: Any, path: str = "") -> list[str]:
    violations: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            lowered = str(key).lower()
            diagnostic_h14 = lowered == "portfolio_contribution_run" and value.get("portfolio_contribution_scope") == "diagnostic only; no economic eligibility or promotion"
            if lowered in {"economic_run", "promotion", "portfolio_contribution_run"} and child is not False and not diagnostic_h14:
                violations.append(child_path)
            if lowered in {"eligible", "economic_eligible"} and child is not False:
                violations.append(child_path)
            if lowered in {"admitted", "economic_admission_count"} or "economic_signal_rows" in lowered or lowered in {"admitted_economic_rows", "admitted_rows", "economic_rows"}:
                if type(child) is not int or child != 0:
                    violations.append(child_path)
            if lowered in {"research_only", "network_used"} and child is not (lowered == "research_only"):
                violations.append(child_path)
            violations.extend(_economic_violations(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(_economic_violations(child, f"{path}[{index}]"))
    return violations


def _numeric_evidence_counts(value: dict[str, Any]) -> dict[str, int | float]:
    """Keep the machine-readable evidence denominator fields, excluding bools."""
    return {
        str(key): child
        for key, child in sorted(value.items())
        if isinstance(child, (int, float)) and not isinstance(child, bool) and (not isinstance(child, float) or math.isfinite(child))
    }


def _load_definition_contract(repo: Path) -> dict[str, Any]:
    path = repo / DEFINITION_CONTRACT
    receipt_path = repo / DEFINITION_RECEIPT
    contract = _load(path)
    definition_builder.verify_contract(contract, repo)
    receipt = _load(receipt_path)
    if receipt.get("receipt_sha256") != definition_builder.canonical_hash(receipt, "receipt_sha256"):
        raise ValueError("definition_contract_receipt_self_hash_mismatch")
    if receipt.get("contract") != {"path": DEFINITION_CONTRACT, "sha256": sha256(path)}:
        raise ValueError("definition_contract_receipt_binding_mismatch")
    if receipt.get("source_pins") != contract.get("source_pins"):
        raise ValueError("definition_contract_receipt_source_binding_mismatch")
    if receipt.get("candidate_ids") != EXPECTED_IDS or receipt.get("candidate_count") != 34:
        raise ValueError("definition_contract_receipt_inventory_invalid")
    return contract


def _result_evidence_summary(candidate_id: str, selected: dict[str, Any]) -> str:
    for key in ("window", "attempted_window", "coverage", "named_index", "card", "source", "field", "matrix"):
        value = selected.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    counts = _numeric_evidence_counts(selected)
    if counts:
        return "; ".join(f"{key}={value}" for key, value in counts.items())
    raise ValueError(f"result_evidence_summary_missing:{candidate_id}")


def _result_terminal_detail(candidate_id: str, selected: dict[str, Any], status: str) -> list[str]:
    blockers = selected.get("blockers", selected.get("blocker"))
    if isinstance(blockers, str):
        blockers = [blockers]
    if isinstance(blockers, list) and all(isinstance(item, str) and item.strip() for item in blockers) and blockers:
        return [item.strip() for item in blockers]
    evidence = {key: selected[key] for key in ("status", "pit_status", "join_status", "economic_gate", "outcome", "error", "data_access_blocked", "available_at_rows", "economic_signal_rows", "admitted_rows", "economic_rows") if key in selected}
    if not evidence:
        raise ValueError(f"result_terminal_detail_missing:{candidate_id}")
    return [f"Current result terminal evidence ({status}): {json.dumps(evidence, sort_keys=True, ensure_ascii=False)}"]


def _candidate_contract(candidate_id: str, selected: dict[str, Any], source: str, pointer: tuple[str, ...], repo: Path, definitions: dict[str, Any]) -> dict[str, Any]:
    """Join immutable definition fields to independently pinned result fields."""
    status = selected.get("status") or selected.get("pit_status") or selected.get("join_status")
    status = status if isinstance(status, str) else ""
    definition = definitions[candidate_id]
    evidence_summary = _result_evidence_summary(candidate_id, selected)
    pit_status = selected.get("pit_status") or selected.get("economic_gate") or status
    pit_status = pit_status if isinstance(pit_status, str) else ""
    blockers = _result_terminal_detail(candidate_id, selected, status)
    result_sha = sha256(repo / source)
    return {
        "candidate_id": candidate_id,
        "source_path": source,
        "pointer": list(pointer),
        "status": status,
        "strategy_name": definition["mechanism"],
        "purpose_definition": None,
        "purpose_definition_status": UNSPECIFIED,
        "mechanism": definition["mechanism"],
        "feasibility": definition["feasibility"],
        "unspecified_fields": definition["unspecified_fields"],
        "research_status": status,
        "evidence_summary": evidence_summary,
        "evidence_counts": _numeric_evidence_counts(selected),
        "pit_provenance": {
            "status": pit_status,
            "source_path": source,
            "source_sha256": result_sha,
            "pointer": list(pointer),
        },
        "terminal_decision": {"kind": "diagnostic" if "DIAGNOSTIC" in status.upper() or "UNTESTED" in status.upper() else "blocker", "detail": blockers},
        "definition_source": definition["definition_source"],
        "result_source": {"path": source, "sha256": result_sha, "pointer": list(pointer)},
        "integration_candidate": False,
        "economic_run": False,
        "eligible": False,
        "admitted": 0,
        "promotion": False,
        "economic_violations": [],
    }


def validate_candidate_contract(candidate_id: str, candidate: Any, repo: Path = REPO) -> list[str]:
    """Fail closed on missing, extra, untyped, or unbound candidate fields."""
    issues: list[str] = []
    if not isinstance(candidate, dict):
        return [f"{candidate_id}:candidate_object_required"]
    if set(candidate) != CANDIDATE_FIELDS:
        issues.append(f"{candidate_id}:candidate_fields_exact_mismatch")
    for field in ("candidate_id", "source_path", "status", "strategy_name", "purpose_definition_status", "mechanism", "feasibility", "research_status", "evidence_summary"):
        if not isinstance(candidate.get(field), str) or not candidate[field].strip():
            issues.append(f"{candidate_id}:{field}_required_nonempty_string")
    for field in ("strategy_name", "mechanism", "feasibility", "evidence_summary"):
        value = candidate.get(field)
        if isinstance(value, str) and ("frozen research candidate" in value.lower() or "frozen causal field and portfolio-contribution hypothesis" in value.lower() or "source-bound evidence" in value.lower()):
            issues.append(f"{candidate_id}:{field}_generic_fallback_forbidden")
    if candidate.get("purpose_definition") is not None or candidate.get("purpose_definition_status") != UNSPECIFIED:
        issues.append(f"{candidate_id}:purpose_definition_must_be_explicitly_unspecified")
    if candidate.get("strategy_name") != candidate.get("mechanism"):
        issues.append(f"{candidate_id}:strategy_name_mechanism_mismatch")
    unspecified = candidate.get("unspecified_fields")
    if not isinstance(unspecified, dict) or unspecified.get("scope") != UNSPECIFIED or any(unspecified.get(key) is not None for key in ("purpose_definition", "field_formula", "window", "cost_stress", "controls", "data_requirements")):
        issues.append(f"{candidate_id}:unspecified_fields_invalid")
    if candidate.get("candidate_id") != candidate_id or candidate.get("status") != candidate.get("research_status"):
        issues.append(f"{candidate_id}:candidate_identity_or_status_mismatch")
    source = candidate.get("source_path")
    if isinstance(source, str) and (_is_archival(Path(source)) or not (repo / source).is_file()):
        issues.append(f"{candidate_id}:current_source_path_invalid")
    pointer = candidate.get("pointer")
    if not isinstance(pointer, list) or not pointer or not all(isinstance(item, str) and item for item in pointer):
        issues.append(f"{candidate_id}:pointer_invalid")
    definition = candidate.get("definition_source")
    if not isinstance(definition, dict) or set(definition) != {"path", "section", "sha256"}:
        issues.append(f"{candidate_id}:definition_source_schema_invalid")
    elif (
        definition.get("path") != DEFINITION_SOURCE
        or not isinstance(definition.get("section"), str)
        or not definition["section"].strip()
        or SHA256_RE.fullmatch(str(definition.get("sha256"))) is None
        or not (repo / str(definition.get("path", ""))).is_file()
        or sha256(repo / str(definition.get("path", ""))) != definition.get("sha256")
    ):
        issues.append(f"{candidate_id}:definition_source_binding_invalid")
    result_source = candidate.get("result_source")
    if not isinstance(result_source, dict) or set(result_source) != {"path", "sha256", "pointer"} or result_source.get("path") != source or result_source.get("sha256") != candidate.get("pit_provenance", {}).get("source_sha256") or result_source.get("pointer") != pointer:
        issues.append(f"{candidate_id}:result_source_binding_invalid")
    counts = candidate.get("evidence_counts")
    if not isinstance(counts, dict) or any(not isinstance(key, str) or not key.strip() or not isinstance(value, (int, float)) or isinstance(value, bool) or (isinstance(value, float) and not math.isfinite(value)) for key, value in (counts.items() if isinstance(counts, dict) else ())):
        issues.append(f"{candidate_id}:evidence_counts_invalid")
    provenance = candidate.get("pit_provenance")
    if not isinstance(provenance, dict) or set(provenance) != {"status", "source_path", "source_sha256", "pointer"}:
        issues.append(f"{candidate_id}:pit_provenance_schema_invalid")
    elif provenance.get("source_path") != source or not isinstance(provenance.get("status"), str) or not provenance["status"].strip() or not isinstance(provenance.get("source_sha256"), str) or SHA256_RE.fullmatch(provenance["source_sha256"]) is None or provenance.get("pointer") != pointer:
        issues.append(f"{candidate_id}:pit_provenance_binding_invalid")
    terminal = candidate.get("terminal_decision")
    if not isinstance(terminal, dict) or set(terminal) != {"kind", "detail"} or terminal.get("kind") not in {"blocker", "diagnostic"} or not isinstance(terminal.get("detail"), list) or not terminal["detail"] or not all(isinstance(item, str) and item.strip() for item in terminal["detail"]):
        issues.append(f"{candidate_id}:terminal_decision_invalid")
    if candidate.get("economic_run") is not False or candidate.get("eligible") is not False or type(candidate.get("admitted")) is not int or candidate.get("admitted") != 0 or candidate.get("promotion") is not False:
        issues.append(f"{candidate_id}:economic_fields_not_strict_zero_false")
    if candidate.get("integration_candidate") is not False:
        issues.append(f"{candidate_id}:integration_candidate_not_strict_false")
    if candidate_id == "H14" and ("H14_PORTFOLIO_CONTRIBUTION_TESTED_BUT_INFEASIBLE_REMAINS_DIAGNOSTIC" not in str(candidate.get("status", "")) or "PORTFOLIO_CONTRIBUTION_UNTESTED" in str(candidate.get("status", "")) or any(marker in json.dumps(candidate.get("terminal_decision", {}), ensure_ascii=False) for marker in ("no reusable current V3", "NOT_COMPARABLE", "PORTFOLIO_CONTRIBUTION_UNTESTED"))):
        issues.append("H14:stale_or_unverified_tested_infeasible_status")
    if not isinstance(candidate.get("economic_violations"), list) or not all(isinstance(item, str) for item in candidate["economic_violations"]):
        issues.append(f"{candidate_id}:economic_violations_invalid")
    return issues


def validate_candidate_inventory(candidates: Any) -> list[str]:
    if not isinstance(candidates, dict):
        return ["candidate_inventory_object_required"]
    expected = set(EXPECTED_IDS)
    actual = set(candidates)
    return ([] if actual == expected else [f"candidate_inventory_exact_mismatch:missing={sorted(expected - actual)}:extra={sorted(actual - expected)}"])


def validate_h06_lineage(report: dict[str, Any]) -> list[str]:
    """Require both document-level and row-level amendment accounting."""
    lineage = report.get("amendment_lineage", {})
    document_level = lineage.get("document_level", {})
    expected_document_counts = {
        "total_documents": 62,
        "uniquely_linked_documents": 26,
        "mixed_documents": 5,
        "entirely_unmatched_documents": 36,
    }
    issues: list[str] = []
    for field, expected in expected_document_counts.items():
        if document_level.get(field) != expected:
            issues.append(f"H06:amendment_document_accounting:{field}={document_level.get(field)!r}; expected={expected}")
    row_level = lineage.get("row_level_unmatched_or_non_unique", {})
    if row_level.get("row_count") != 149 or row_level.get("unique_accession_count") != 41:
        issues.append(
            "H06:amendment_row_accounting:"
            f"{row_level.get('row_count')!r}/{row_level.get('unique_accession_count')!r}; expected=149/41"
        )
    return issues


def build_record(repo: Path = REPO) -> dict[str, Any]:
    candidate_ids = sorted(CURRENT_RECORDS)
    issues: list[str] = []
    candidates: dict[str, dict[str, Any]] = {}
    definition_contract = _load_definition_contract(repo)
    definitions = definition_contract["candidates"]
    for candidate in EXPECTED_IDS:
        source, pointer = CURRENT_RECORDS.get(candidate, ("", ()))
        path = repo / source if source else repo / "missing"
        if not source or not path.is_file():
            issues.append(f"{candidate}:missing_current_record:{source or '<unmapped>'}")
            candidates[candidate] = {"source_path": source, "status": "MISSING_CURRENT_RECORD"}
            continue
        if _is_archival(path):
            issues.append(f"{candidate}:archival_path_selected:{source}")
        try:
            payload = _load(path)
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"{candidate}:record_read_error:{source}:{exc}")
            candidates[candidate] = {"source_path": source, "status": "RECORD_READ_ERROR"}
            continue
        selected = _candidate_payload(payload, pointer)
        if selected is None:
            issues.append(f"{candidate}:pointer_missing:{source}:{'.'.join(pointer)}")
            selected = {"status": "POINTER_MISSING"}
        text = json.dumps(selected, sort_keys=True)
        if "SUPERSEDED" in text.upper() or "ARCHIVAL" in text.upper():
            issues.append(f"{candidate}:superseded_or_archival_current_payload:{source}")
        violations = _economic_violations(selected)
        if violations:
            issues.extend(f"{candidate}:economic_field:{item}" for item in violations)
        candidate_record = _candidate_contract(candidate, selected, source, pointer, repo, definitions)
        candidate_record["economic_violations"] = violations
        candidates[candidate] = candidate_record
        issues.extend(validate_candidate_contract(candidate, candidate_record, repo))

    current_paths = [relative for relative in CURRENT_FILES if (repo / relative).is_file()]
    missing_paths = [relative for relative in CURRENT_FILES if not (repo / relative).is_file()]
    issues.extend(f"missing_current_dependency:{relative}" for relative in missing_paths)
    marker_paths = tuple(repo / relative for relative in current_paths)
    markers = scan_markers(marker_paths)
    issues.extend(f"marker:{item['path']}:{item['line']}:{item['marker']}" for item in markers)
    issues.extend(_current_retry_issues(repo))
    private_contacts = _private_contact_findings(repo, current_paths)
    issues.extend(private_contacts)
    for relative in MANIFESTS:
        issues.extend(_verify_manifest(repo / relative, repo))

    component_results = component_checks(repo)
    for result in component_results:
        if not result["ok"]:
            issues.append(f"component_check_failed:{result['label']}:{result.get('error', result.get('result'))}")

    stage_path = repo / CURRENT_RECORDS["H01"][0]
    if stage_path.is_file():
        stage = _load(stage_path)
        validation = stage.get("validation", {})
        for name, result in validation.items():
            if isinstance(result, dict) and result.get("exit_code") not in (None, 0):
                issues.append(f"H01-H05-H14:failed_recorded_validation:{name}:exit_code={result['exit_code']}")

    h06_report_path = repo / "data/research/external/equity_h06_local_normalization_2026_09_09/report.json"
    if h06_report_path.is_file():
        h06_report = _load(h06_report_path)
        issues.extend(validate_h06_lineage(h06_report))

    h14 = candidates.get("H14", {})
    h14_status = str(h14.get("status", ""))
    h14_infeasible = "H14_PORTFOLIO_CONTRIBUTION_TESTED_BUT_INFEASIBLE_REMAINS_DIAGNOSTIC" in h14_status and "PORTFOLIO_CONTRIBUTION_UNTESTED" not in h14_status
    if not h14_infeasible:
        issues.append("H14:diagnostic_infeasible_status_not_proven")

    source_pins = {
        relative: {"sha256": sha256(repo / relative), "bytes": (repo / relative).stat().st_size, "mtime_ns": (repo / relative).stat().st_mtime_ns}
        for relative in current_paths
    }
    record = {
        "schema": "all-34-research-closure-v1",
        "status": "ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS" if not issues and len(candidate_ids) == 34 and len(set(candidate_ids)) == 34 else "NOT_CLOSED",
        "candidate_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
        "candidate_ids_unique": len(candidate_ids) == len(set(candidate_ids)),
        "candidates": candidates,
        "component_checks": component_results,
        "manifest_checks": {relative: "PASS" if not _verify_manifest(repo / relative, repo) else "FAIL" for relative in MANIFESTS},
        "marker_findings": markers,
        "private_contact_count": len(private_contacts),
        "local_engineering_gap_count": sum(_is_local_engineering_marker(item["marker"]) for item in markers),
        "economic_eligible_count": sum(item.get("eligible") is True for item in candidates.values()),
        "integration_candidate_count": sum(item.get("integration_candidate") is True for item in candidates.values()),
        "economic_run": False,
        "economic_admission_count": 0,
        "portfolio_contribution_run": False,
        "promotion": False,
        "h14_diagnostic_infeasible": h14_infeasible,
        "research_only": True,
        "network_used": False,
        "research_path_mutation_count": 0,
        "verify_mode_hash_mtime_unchanged": True,
        "source_pins": source_pins,
        "issues": sorted(set(issues)),
        "current_selection_policy": "explicit versioned current paths; archival and superseded paths are rejected",
        "archive_policy_path": "docs/research/README.md",
        "record_sha256": PENDING,
    }
    issues.extend(validate_candidate_inventory(candidates))
    record["issues"] = sorted(set(issues))
    record["record_sha256"] = canonical_hash(record)
    return record


def _summary(record: dict[str, Any]) -> str:
    lines = [
        "# All 34 research closure",
        "",
        f"Status: `{record['status']}`",
        "",
        f"Candidates: `{record['candidate_count']}` unique IDs; economic eligible: `{record['economic_eligible_count']}`; local engineering gap: `{record['local_engineering_gap_count']}`.",
        f"Economic run: `{record['economic_run']}`; admission: `{record['economic_admission_count']}`; portfolio contribution: `{record['portfolio_contribution_run']}`; promotion: `{record['promotion']}`.",
        f"H14 diagnostic infeasible: `{record['h14_diagnostic_infeasible']}`; private contact literals: `{record['private_contact_count']}`; research-path mutations: `{record['research_path_mutation_count']}`.",
        "",
        "## Current blockers",
        "",
    ]
    lines.extend(f"- {issue}" for issue in record["issues"])
    if not record["issues"]:
        lines.append("- None")
    lines.extend(["", f"Record SHA-256: `{record['record_sha256']}`", ""])
    return "\n".join(lines)


def write_closure(repo: Path = REPO, output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    before = snapshot_paths(repo, CURRENT_FILES)
    record = build_record(repo)
    after = snapshot_paths(repo, CURRENT_FILES)
    record["research_path_mutation_count"] = sum(before.get(key) != after.get(key) for key in set(before) | set(after))
    record["record_sha256"] = PENDING
    record["record_sha256"] = canonical_hash(record)
    output_dir.mkdir(parents=True, exist_ok=True)
    record_path = output_dir / RECORD_NAME
    summary_path = output_dir / SUMMARY_NAME
    record_bytes = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")
    summary_bytes = _summary(record).encode("utf-8")
    temp_record = record_path.with_name(f".{record_path.name}.{os.getpid()}.tmp")
    temp_summary = summary_path.with_name(f".{summary_path.name}.{os.getpid()}.tmp")
    try:
        temp_record.write_bytes(record_bytes)
        temp_summary.write_bytes(summary_bytes)
        os.replace(temp_record, record_path)
        os.replace(temp_summary, summary_path)
    finally:
        temp_record.unlink(missing_ok=True)
        temp_summary.unlink(missing_ok=True)
    return record


def verify_closure(repo: Path = REPO, output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    record_path = output_dir / RECORD_NAME
    summary_path = output_dir / SUMMARY_NAME
    before = snapshot_paths(repo, CURRENT_FILES)
    output_before = _snapshot_files([record_path, summary_path])
    if not record_path.is_file() or not summary_path.is_file():
        raise ValueError("closure_outputs_missing")
    record = _load(record_path)
    declared = record.get("record_sha256")
    payload = dict(record)
    payload["record_sha256"] = PENDING
    if not isinstance(declared, str) or canonical_hash(payload) != declared:
        raise ValueError("closure_record_self_hash_mismatch")
    if record.get("status") != "ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS":
        raise ValueError("closure_status_not_definitions_bound")
    if record.get("candidate_ids") != EXPECTED_IDS or record.get("candidate_count") != 34 or record.get("candidate_ids_unique") is not True:
        raise ValueError("closure_candidate_inventory_invalid")
    inventory_issues = validate_candidate_inventory(record.get("candidates"))
    for candidate_id in EXPECTED_IDS:
        inventory_issues.extend(validate_candidate_contract(candidate_id, record["candidates"].get(candidate_id), repo))
    if inventory_issues:
        raise ValueError("closure_candidate_schema_invalid:" + ";".join(sorted(set(inventory_issues))))
    required_flags = {
        "local_engineering_gap_count": 0,
        "economic_eligible_count": 0,
        "integration_candidate_count": 0,
        "economic_run": False,
        "economic_admission_count": 0,
        "portfolio_contribution_run": False,
        "promotion": False,
        "h14_diagnostic_infeasible": True,
        "research_only": True,
        "network_used": False,
        "private_contact_count": 0,
        "research_path_mutation_count": 0,
        "verify_mode_hash_mtime_unchanged": True,
    }
    if type(record.get("integration_candidate_count")) is not int or any(record.get(key) != expected for key, expected in required_flags.items()) or record.get("issues") != [] or record.get("marker_findings") != []:
        raise ValueError("closure_completion_conditions_failed")
    for relative, pin in record.get("source_pins", {}).items():
        path = repo / relative
        if not path.is_file() or sha256(path) != pin.get("sha256") or path.stat().st_size != pin.get("bytes") or path.stat().st_mtime_ns != pin.get("mtime_ns"):
            raise ValueError(f"closure_source_pin_mismatch:{relative}")
    if f"{declared}" not in summary_path.read_text(encoding="utf-8"):
        raise ValueError("closure_summary_record_pin_mismatch")
    after = snapshot_paths(repo, CURRENT_FILES)
    output_after = _snapshot_files([record_path, summary_path])
    if before != after or output_before != output_after:
        raise ValueError("verify_mode_mutated_hash_or_mtime")
    return {"status": record["status"], "read_only": True, "record_sha256": declared, "candidate_count": record["candidate_count"], "issues": record["issues"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate read-only verification of the 34 frozen research candidates")
    parser.add_argument("--verify", action="store_true", help="verify an existing closure record without writes or network")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    try:
        result = verify_closure(REPO, args.output_dir) if args.verify else write_closure(REPO, args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "NOT_CLOSED", "error": str(exc), "read_only": bool(args.verify)}, sort_keys=True))
        return 1
    print(json.dumps({"status": result["status"], "candidate_count": result.get("candidate_count", 34), "record_sha256": result.get("record_sha256"), "read_only": bool(args.verify), "issues": result.get("issues", [])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
