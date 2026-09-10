"""Build the final 34-candidate research report only after verified closure.

This builder consumes one hash-pinned closure record and the current candidate
sources bound by that record.  It never runs economics, changes runtime state,
or writes partial completion outputs when closure is not exact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research import verify_all_34_closure_2026_09_09 as closure_verifier

CLOSURE_RECORD = REPO / "data/research_runs/all_34_research_closure_2026_09_09/closure_record.json"
EXPECTED_CLOSURE_STATUS = "ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS"
EXPECTED_CLOSURE_CANONICAL_SHA256 = "48b3525cb52a72a540e83e34edea9367ee4265b4ddf1f7be02f970b7b8204cf6"
EXPECTED_CLOSURE_PHYSICAL_SHA256 = "71576a9f0b239940b7ebd6a5d5371b949b30518f468c3f880eb7e3403f95b96a"
EXPECTED_CLOSURE_SUMMARY_SHA256 = "d74c5f0d915ae329215a226a2a661efc331b2cc2a26aebb502e469376cdd2bb5"
SUMMARY_PATH = REPO / "data/research_runs/all_34_research_closure_2026_09_09/closure_summary.md"
DEFINITION_CONTRACT = "data/research_runs/strategy_queue_contract_2026_09_09/candidate_contracts.json"
DEFAULT_REPORT = REPO / "docs/research/strategy_queue_completion_report_2026_09_09.md"
DEFAULT_OUTPUT_DIR = REPO / "data/research_runs/strategy_queue_completion_2026_09_09"
DEFAULT_LEDGER = DEFAULT_OUTPUT_DIR / "closure_ledger.json"
DEFAULT_RECEIPT = DEFAULT_OUTPUT_DIR / "hash_receipt.json"
EXPECTED_IDS = sorted(
    [f"H{i:02d}" for i in range(1, 17)]
    + [f"K{i:02d}" for i in range(1, 13)]
    + [f"X{i:02d}" for i in range(1, 7)]
)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_hash(value: dict[str, Any], field: str = "record_sha256") -> str:
    payload = dict(value)
    payload[field] = closure_verifier.PENDING
    return closure_verifier.canonical_hash(payload)


def _strict_bool(value: Any, label: str, expected: bool = False) -> None:
    if type(value) is not bool or value is not expected:
        raise ValueError(f"strict_boolean_violation:{label}")


def _strict_zero_int(value: Any, label: str) -> None:
    if type(value) is not int or value != 0:
        raise ValueError(f"strict_zero_integer_violation:{label}")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _pointer(source: dict[str, Any], pointer: list[str]) -> dict[str, Any]:
    value: Any = source
    for part in pointer:
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"candidate_pointer_missing:{part}")
        value = value[part]
    if not isinstance(value, dict):
        raise ValueError("candidate_pointer_object_required")
    return value


def _check_no_private_contact(value: Any) -> None:
    if EMAIL_RE.search(json.dumps(value, ensure_ascii=False)):
        raise ValueError("private_contact_in_completion_input")


def _verify_source(repo: Path, relative: str, declaration: dict[str, Any]) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"current_source_path_invalid:{relative}")
    path_tokens = {part.lower() for part in relative_path.parts}
    if any(
        token.startswith(("archive", "archival", "superseded"))
        or ("lifecycle" in token and "metadata" in token)
        for token in path_tokens
    ):
        raise ValueError(f"archival_source_used_as_current:{relative}")
    path = repo / relative
    if not path.is_file():
        raise ValueError(f"closure_source_missing:{relative}")
    if not path.resolve().is_relative_to(repo.resolve()):
        raise ValueError(f"current_source_path_invalid:{relative}")
    if sha256(path) != declaration.get("sha256"):
        raise ValueError(f"closure_source_hash_mismatch:{relative}")
    if path.stat().st_size != declaration.get("bytes") or path.stat().st_mtime_ns != declaration.get("mtime_ns"):
        raise ValueError(f"closure_source_pin_mismatch:{relative}")
    return path


def _verify_binding(repo: Path, binding: Any, source_pins: dict[str, Any], label: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(binding, dict):
        raise ValueError(f"{label}_binding_invalid")
    relative = binding.get("path")
    digest = binding.get("sha256")
    if not isinstance(relative, str) or not isinstance(digest, str):
        raise ValueError(f"{label}_binding_invalid")
    declaration = source_pins.get(relative)
    if not isinstance(declaration, dict) or declaration.get("sha256") != digest:
        raise ValueError(f"{label}_source_pin_mismatch")
    return _verify_source(repo, relative, declaration), declaration


def _validate_definition_fields(candidate: dict[str, Any], candidate_id: str) -> None:
    for field in ("mechanism", "feasibility"):
        _required_text(candidate.get(field), f"{field}:{candidate_id}")
    if candidate.get("strategy_name") != candidate.get("mechanism"):
        raise ValueError(f"mechanism_strategy_binding_mismatch:{candidate_id}")
    if candidate.get("purpose_definition") is not None:
        raise ValueError(f"unspecified_purpose_must_be_null:{candidate_id}")
    if candidate.get("purpose_definition_status") != closure_verifier.UNSPECIFIED:
        raise ValueError(f"unspecified_purpose_scope_missing:{candidate_id}")
    unspecified = candidate.get("unspecified_fields")
    if not isinstance(unspecified, dict) or unspecified.get("scope") != closure_verifier.UNSPECIFIED:
        raise ValueError(f"unspecified_fields_scope_missing:{candidate_id}")
    for field in ("purpose_definition", "field_formula", "window", "cost_stress", "controls"):
        if field not in unspecified or unspecified[field] is not None:
            raise ValueError(f"unspecified_field_not_null:{candidate_id}:{field}")


def _validate_candidate_definition_and_result(repo: Path, candidate_id: str, candidate: dict[str, Any], source_pins: dict[str, Any], sources: dict[str, dict[str, Any]], definitions: dict[str, Any]) -> None:
    _validate_definition_fields(candidate, candidate_id)
    relative = candidate.get("source_path")
    pointer = candidate.get("pointer")
    if not isinstance(relative, str) or not isinstance(pointer, list) or not all(isinstance(item, str) for item in pointer):
        raise ValueError(f"candidate_binding_invalid:{candidate_id}")
    result_source = candidate.get("result_source")
    if not isinstance(result_source, dict) or result_source.get("path") != relative or result_source.get("pointer") != pointer:
        raise ValueError(f"result_source_binding_invalid:{candidate_id}")
    result_path, result_declaration = _verify_binding(repo, result_source, source_pins, f"result:{candidate_id}")
    definition_path, definition_declaration = _verify_binding(repo, candidate.get("definition_source"), source_pins, f"definition:{candidate_id}")
    if definition_path == result_path:
        raise ValueError(f"definition_result_sources_must_be_separate:{candidate_id}")
    result = sources.setdefault(relative, load_json(result_path))
    _check_no_private_contact(definition_path.read_text(encoding="utf-8"))
    _check_no_private_contact(result)
    target = _pointer(result, pointer)
    if candidate.get("status") != candidate.get("research_status"):
        raise ValueError(f"candidate_research_status_mismatch:{candidate_id}")
    candidate_status = _required_text(candidate.get("research_status"), f"research_status:{candidate_id}")
    if _source_status(target, candidate_id) != candidate_status:
        raise ValueError(f"candidate_status_binding_mismatch:{candidate_id}")
    if candidate.get("result_source", {}).get("sha256") != result_declaration["sha256"] or candidate.get("definition_source", {}).get("sha256") != definition_declaration["sha256"]:
        raise ValueError(f"candidate_source_hash_binding_mismatch:{candidate_id}")
    contract_definition = definitions.get(candidate_id)
    if not isinstance(contract_definition, dict) or candidate.get("mechanism") != contract_definition.get("mechanism") or candidate.get("feasibility") != contract_definition.get("feasibility"):
        raise ValueError(f"candidate_definition_contract_mismatch:{candidate_id}")
    if candidate.get("definition_source") != contract_definition.get("definition_source"):
        raise ValueError(f"candidate_definition_source_mismatch:{candidate_id}")
    provenance = candidate.get("pit_provenance")
    if not isinstance(provenance, dict) or provenance.get("source_path") != relative or provenance.get("source_sha256") != result_declaration["sha256"] or provenance.get("pointer") != pointer:
        raise ValueError(f"candidate_provenance_binding_mismatch:{candidate_id}")
    evidence_counts = candidate.get("evidence_counts")
    if not isinstance(evidence_counts, dict) or not evidence_counts or any(type(value) not in (int, float) or isinstance(value, bool) for value in evidence_counts.values()):
        raise ValueError(f"candidate_evidence_counts_invalid:{candidate_id}")
    _required_text(candidate.get("evidence_summary"), f"evidence_summary:{candidate_id}")
    terminal = candidate.get("terminal_decision")
    if not isinstance(terminal, dict) or terminal.get("kind") not in {"blocker", "diagnostic"} or not isinstance(terminal.get("detail"), list) or not terminal["detail"] or not all(isinstance(item, str) and item.strip() for item in terminal["detail"]):
        raise ValueError(f"candidate_terminal_decision_invalid:{candidate_id}")
    _strict_bool(candidate.get("economic_run"), f"candidate:{candidate_id}:economic_run")
    _strict_bool(candidate.get("eligible"), f"candidate:{candidate_id}:eligible")
    _strict_bool(candidate.get("promotion"), f"candidate:{candidate_id}:promotion")
    _strict_bool(candidate.get("integration_candidate"), f"candidate:{candidate_id}:integration_candidate")
    _strict_zero_int(candidate.get("admitted"), f"candidate:{candidate_id}:admitted")
    if candidate.get("economic_violations") != []:
        raise ValueError(f"candidate_economic_violations:{candidate_id}")
    if candidate_id == "H14":
        if candidate_status != "H14_PORTFOLIO_CONTRIBUTION_TESTED_BUT_INFEASIBLE_REMAINS_DIAGNOSTIC":
            raise ValueError("h14_status_invalid")
        if target.get("current_v3_parity") != "PASS" or target.get("current_v3_daily_nav_rows") != 2658 or target.get("correlation_rows") != 18 or target.get("fixed_weighting") != "95% V3 + 5% H14" or target.get("portfolio_contribution_run") is not True or target.get("integration_candidate") is not False:
            raise ValueError("h14_current_evidence_invalid")


def load_verified_inputs(repo: Path = REPO, record_path: Path = CLOSURE_RECORD) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    record = load_json(record_path)
    if record.get("record_sha256") != canonical_hash(record):
        raise ValueError("closure_record_self_hash_mismatch")
    if record.get("status") != EXPECTED_CLOSURE_STATUS:
        raise ValueError("closure_not_closed")
    if record_path.resolve() == CLOSURE_RECORD.resolve() and record.get("record_sha256") != EXPECTED_CLOSURE_CANONICAL_SHA256:
        raise ValueError("closure_record_pin_mismatch")
    if record_path.resolve() == CLOSURE_RECORD.resolve():
        if sha256(record_path) != EXPECTED_CLOSURE_PHYSICAL_SHA256 or not SUMMARY_PATH.is_file() or sha256(SUMMARY_PATH) != EXPECTED_CLOSURE_SUMMARY_SHA256:
            raise ValueError("closure_physical_or_summary_pin_mismatch")
    if record.get("candidate_ids") != EXPECTED_IDS or type(record.get("candidate_count")) is not int or record.get("candidate_count") != 34 or record.get("candidate_ids_unique") is not True or record.get("integration_candidate_count") != 0:
        raise ValueError("closure_candidate_inventory_invalid")
    required = {"local_engineering_gap_count": 0, "economic_eligible_count": 0, "economic_run": False, "economic_admission_count": 0, "portfolio_contribution_run": False, "promotion": False, "research_only": True, "network_used": False, "private_contact_count": 0, "research_path_mutation_count": 0, "verify_mode_hash_mtime_unchanged": True}
    for key, expected in required.items():
        value = record.get(key)
        if isinstance(expected, bool):
            _strict_bool(value, f"record:{key}", expected)
        elif type(value) is not type(expected) or value != expected:
            raise ValueError(f"strict_record_value_violation:{key}")
    if record.get("issues") != [] or record.get("marker_findings") != [] or record.get("h14_diagnostic_infeasible") is not True:
        raise ValueError("closure_completion_conditions_failed")
    _check_no_private_contact(record)
    candidates = record.get("candidates")
    source_pins = record.get("source_pins")
    if not isinstance(candidates, dict) or not isinstance(source_pins, dict) or set(candidates) != set(EXPECTED_IDS) or len(candidates) != len(EXPECTED_IDS):
        raise ValueError("closure_candidate_bindings_invalid")
    definition_declaration = source_pins.get(DEFINITION_CONTRACT)
    if not isinstance(definition_declaration, dict):
        raise ValueError("definition_contract_source_pin_missing")
    _verify_source(repo, DEFINITION_CONTRACT, definition_declaration)
    definitions_contract = load_json(repo / DEFINITION_CONTRACT)
    closure_verifier.definition_builder.verify_contract(definitions_contract, repo)
    definitions = definitions_contract.get("candidates")
    if not isinstance(definitions, dict) or set(definitions) != set(EXPECTED_IDS):
        raise ValueError("definition_contract_inventory_invalid")
    sources: dict[str, dict[str, Any]] = {}
    for candidate_id in EXPECTED_IDS:
        candidate = candidates.get(candidate_id)
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate_record_missing:{candidate_id}")
        candidate_issues = closure_verifier.validate_candidate_contract(candidate_id, candidate, repo)
        if candidate_issues:
            raise ValueError(f"candidate_record_schema_invalid:{';'.join(candidate_issues)}")
        _validate_candidate_definition_and_result(repo, candidate_id, candidate, source_pins, sources, definitions)
    return record, sources


def _target(record: dict[str, Any], sources: dict[str, dict[str, Any]], candidate_id: str) -> tuple[dict[str, Any], str]:
    candidate = record["candidates"][candidate_id]
    relative = str(candidate["source_path"])
    return _pointer(sources[relative], candidate["pointer"]), relative


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"required_nonempty_text:{label}")
    return value.strip()


def _source_status(target: dict[str, Any], candidate_id: str) -> str:
    for field in ("status", "pit_status", "join_status"):
        value = target.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"required_nonempty_text:target_status:{candidate_id}")


def build_rows(record: dict[str, Any], sources: dict[str, dict[str, Any]], repo: Path = REPO) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate_id in EXPECTED_IDS:
        candidate = record["candidates"][candidate_id]
        target, relative = _target(record, sources, candidate_id)
        unspecified = candidate["unspecified_fields"]
        row: dict[str, Any] = {
            "candidate_id": candidate_id,
            "integration_candidate": False,
            "mechanism": candidate["mechanism"],
            "frozen_data_feasibility": candidate["feasibility"],
            "purpose_definition": None,
            "purpose_definition_scope": unspecified["scope"],
            "field_formula": None,
            "field_formula_scope": unspecified["scope"],
            "window": None,
            "window_scope": unspecified["scope"],
            "cost_stress": None,
            "cost_stress_scope": unspecified["scope"],
            "controls": None,
            "controls_scope": unspecified["scope"],
            "research_status": candidate["research_status"],
            "evidence_scope": _required_text(candidate.get("evidence_summary"), f"evidence_summary:{candidate_id}"),
            "evidence_counts": dict(candidate["evidence_counts"]),
            "pit_provenance": dict(candidate["pit_provenance"]),
            "terminal_decision": dict(candidate["terminal_decision"]),
            "definition_source": dict(candidate["definition_source"]),
            "result_source": dict(candidate["result_source"]),
            "economic_decision": "NOT_RUN; economic_eligible=0",
            "economic_run": False,
            "economic_eligible": False,
            "admitted": 0,
            "promotion": False,
            "source_path": relative,
            "source_sha256": sha256(repo / relative),
            "source_pointer": list(candidate["pointer"]),
        }
        if candidate_id == "H14":
            row["current_evidence"] = {
                "current_v3_parity": target["current_v3_parity"],
                "v3_daily_nav_rows": target["current_v3_daily_nav_rows"],
                "h14_cells": target["correlation_rows"],
                "fixed_weighting": target["fixed_weighting"],
                "shared_position_cap": target["shared_position_cap"],
                "shared_position_cap_violations": dict(target["shared_position_cap_violations"]),
            }
        rows.append(row)
    return rows


def render_report(record: dict[str, Any], rows: list[dict[str, Any]], ledger_relative: str, receipt_relative: str) -> str:
    lines = [
        "# 34 aday araştırma kapanış raporu — 2026-09-09",
        "",
        f"Kapanış durumu: `{record['status']}`. 34 adayın tamamı tekil kimlikle doğrulandı; integration candidate `0`, yerel mühendislik açığı `0`, ekonomik uygun aday `0`.",
        "",
        "Bu belge yalnız hash-doğrulanmış kapanış kaydından üretilir. Ekonomik getiri, PAPER/LIVE, işlem, üretim veya otomatik terfi çalıştırılmadı. H14 gerçek V3 test edildi ancak ortak pozisyon sınırı nedeniyle uygulanamaz kaldı ve yalnız tanısaldır; diğer adaylar veri/PIT/şema/yetenek veya tanı engelleriyle blokelidir ve disproven sayılmaz; mevcut equity V3 çıktıları değiştirilmeden korunur.",
        "",
        "Kaynakta ayrı amaç/formül/pencere/maliyet kontrolü tanımlanmadığı için bu alanlar `null` ve her satırda kaynak kapsamı ile birlikte tutulur.",
        "",
        "| ID | Mekanizma | Dondurulmuş veri uygulanabilirliği | Güncel kanıt/durum | PIT | Terminal karar | integration_candidate | Ekonomik karar |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        counts = ", ".join(f"{key}={value}" for key, value in row["evidence_counts"].items())
        current = row.get("current_evidence")
        current_text = row["evidence_scope"]
        if not all(f"{key}={value}" in current_text for key, value in row["evidence_counts"].items()):
            current_text += "; " + counts
        if current is not None:
            current_text += "; " + json.dumps(current, ensure_ascii=False, sort_keys=True)
        terminal = "; ".join(row["terminal_decision"]["detail"])
        lines.append(f"| {row['candidate_id']} | {row['mechanism']} | {row['frozen_data_feasibility']} | {current_text}; status={row['research_status']} | {row['pit_provenance']['status']} | {terminal} | {str(row['integration_candidate']).lower()} | {row['economic_decision']} |")
    lines.extend(["", f"Makine okunur ledger: `{ledger_relative}`", f"Hash receipt: `{receipt_relative}`", ""])
    return "\n".join(lines)


def _atomic_bundle(files: dict[Path, bytes], validate: Any = None) -> None:
    temporary: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path | None]] = []
    try:
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temp.write_bytes(data)
            temporary.append((temp, path))
            backup = None
            if path.exists():
                backup = path.with_name(f".{path.name}.{os.getpid()}.bak")
                shutil.copy2(path, backup)
            backups.append((path, backup))
        for temp, path in temporary:
            os.replace(temp, path)
        if validate is not None:
            validate()
    except Exception:
        for path, backup in backups:
            if backup is None:
                path.unlink(missing_ok=True)
            elif backup.is_file():
                os.replace(backup, path)
        for temp, _ in temporary:
            temp.unlink(missing_ok=True)
        for _, backup in backups:
            if backup is not None:
                backup.unlink(missing_ok=True)
        raise
    finally:
        for _, backup in backups:
            if backup is not None:
                backup.unlink(missing_ok=True)


def _verify_bundle(report_path: Path, ledger_path: Path, receipt_path: Path, record_path: Path, repo: Path) -> None:
    report = report_path.read_bytes()
    ledger = load_json(ledger_path)
    receipt = load_json(receipt_path)
    record, sources = load_verified_inputs(repo, record_path)
    report_sha = hashlib.sha256(report).hexdigest()
    ledger_sha = sha256(ledger_path)
    closure_sha = sha256(record_path)
    expected_report = {"path": str(report_path.relative_to(repo)), "sha256": report_sha}
    closure_record = load_json(record_path)
    expected_closure = {"path": str(record_path.relative_to(repo)), "sha256": closure_sha, "canonical_sha256": closure_record["record_sha256"]}
    if ledger.get("report") != expected_report or ledger.get("closure_record") != expected_closure:
        raise ValueError("completion_ledger_binding_mismatch")
    if receipt.get("report") != {"path": str(report_path.relative_to(repo)), "sha256": report_sha}:
        raise ValueError("completion_report_receipt_mismatch")
    if receipt.get("ledger") != {"path": str(ledger_path.relative_to(repo)), "sha256": ledger_sha}:
        raise ValueError("completion_ledger_receipt_mismatch")
    if receipt.get("closure_record") != expected_closure:
        raise ValueError("completion_closure_receipt_mismatch")
    if receipt.get("receipt_sha256") != canonical_hash(receipt, "receipt_sha256"):
        raise ValueError("completion_receipt_self_hash_mismatch")
    if receipt.get("status") != EXPECTED_CLOSURE_STATUS or ledger.get("status") != EXPECTED_CLOSURE_STATUS or receipt.get("economic_run") is not False or receipt.get("no_auto_promotion") is not True:
        raise ValueError("completion_receipt_policy_mismatch")
    if receipt.get("candidate_count") != 34 or receipt.get("integration_candidate_count") != 0:
        raise ValueError("completion_receipt_inventory_mismatch")
    if ledger.get("candidate_count") != 34 or ledger.get("candidate_ids") != EXPECTED_IDS or ledger.get("integration_candidate_count") != 0 or len(ledger.get("rows", [])) != 34 or [row.get("candidate_id") for row in ledger["rows"] ] != EXPECTED_IDS:
        raise ValueError("completion_ledger_inventory_mismatch")
    if any(type(row.get("integration_candidate")) is not bool or row["integration_candidate"] is not False for row in ledger["rows"]):
        raise ValueError("completion_ledger_integration_policy_mismatch")
    definition_contract = ledger.get("definition_contract")
    if not isinstance(definition_contract, dict) or not isinstance(definition_contract.get("path"), str) or not isinstance(definition_contract.get("sha256"), str):
        raise ValueError("completion_definition_contract_binding_missing")
    definition_path = repo / definition_contract["path"]
    if not definition_path.is_file() or sha256(definition_path) != definition_contract["sha256"]:
        raise ValueError("completion_definition_contract_binding_mismatch")
    if receipt.get("definition_contract") != definition_contract:
        raise ValueError("completion_definition_receipt_mismatch")
    if ledger.get("economic_run") is not False or ledger.get("economic_eligible_count") != 0 or ledger.get("portfolio_contribution_run") is not False:
        raise ValueError("completion_ledger_policy_mismatch")
    expected_rows = build_rows(record, sources, repo)
    if ledger["rows"] != expected_rows:
        raise ValueError("completion_ledger_rows_do_not_match_current_sources")
    expected_report = render_report(record, expected_rows, str(ledger_path.relative_to(repo)), str(receipt_path.relative_to(repo))).encode("utf-8")
    if report != expected_report:
        raise ValueError("completion_report_does_not_match_current_sources")


def write_completion(repo: Path = REPO, record_path: Path | None = None, report_path: Path | None = None, output_dir: Path | None = None) -> dict[str, Any]:
    record_path = record_path or repo / CLOSURE_RECORD.relative_to(REPO)
    report_path = report_path or repo / DEFAULT_REPORT.relative_to(REPO)
    output_dir = output_dir or repo / DEFAULT_OUTPUT_DIR.relative_to(REPO)
    ledger_path = output_dir / "closure_ledger.json"
    receipt_path = output_dir / "hash_receipt.json"
    record, sources = load_verified_inputs(repo, record_path)
    rows = build_rows(record, sources, repo)
    if [row["candidate_id"] for row in rows] != EXPECTED_IDS:
        raise ValueError("ledger_candidate_inventory_invalid")
    ledger_relative = str(ledger_path.relative_to(repo))
    receipt_relative = str(receipt_path.relative_to(repo))
    report_relative = str(report_path.relative_to(repo))
    report_bytes = render_report(record, rows, ledger_relative, receipt_relative).encode("utf-8")
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    definition_contract_path = repo / "data/research_runs/strategy_queue_contract_2026_09_09/candidate_contracts.json"
    definition_contract = {"path": str(definition_contract_path.relative_to(repo)), "sha256": sha256(definition_contract_path)}
    closure_binding = {"path": str(record_path.relative_to(repo)), "sha256": sha256(record_path), "canonical_sha256": record["record_sha256"]}
    ledger = {"schema": "strategy-queue-closure-ledger-v2-definitions", "status": record["status"], "candidate_count": 34, "candidate_ids": EXPECTED_IDS, "integration_candidate_count": 0, "closure_record": closure_binding, "definition_contract": definition_contract, "report": {"path": report_relative, "sha256": report_sha}, "economic_run": False, "economic_eligible_count": 0, "portfolio_contribution_run": False, "rows": rows}
    ledger_bytes = (json.dumps(ledger, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    ledger_sha = hashlib.sha256(ledger_bytes).hexdigest()
    receipt = {"schema": "strategy-queue-completion-hash-receipt-v2-definitions", "status": record["status"], "candidate_count": 34, "integration_candidate_count": 0, "closure_record": closure_binding, "definition_contract": definition_contract, "report": {"path": report_relative, "sha256": report_sha}, "ledger": {"path": ledger_relative, "sha256": ledger_sha}, "economic_run": False, "no_auto_promotion": True, "receipt_sha256": closure_verifier.PENDING}
    receipt["receipt_sha256"] = canonical_hash(receipt, "receipt_sha256")
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_bundle(
        {report_path: report_bytes, ledger_path: ledger_bytes, receipt_path: receipt_bytes},
        validate=lambda: _verify_bundle(report_path, ledger_path, receipt_path, record_path, repo),
    )
    return {"status": record["status"], "report": str(report_path), "ledger": str(ledger_path), "receipt": str(receipt_path), "report_sha256": report_sha, "ledger_sha256": ledger_sha, "receipt_sha256": receipt["receipt_sha256"], "candidate_count": 34, "integration_candidate_count": 0, "closure_record_sha256": record["record_sha256"], "definition_contract_sha256": definition_contract["sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the 34-candidate completion report only after exact closure")
    parser.add_argument("--record", type=Path, default=CLOSURE_RECORD)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    try:
        result = write_completion(REPO, args.record, args.report, args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "NOT_CLOSED", "error": str(exc), "outputs_written": False}, sort_keys=True))
        return 1
    print(json.dumps({**result, "outputs_written": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
