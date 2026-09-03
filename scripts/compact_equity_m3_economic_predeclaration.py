#!/usr/bin/env python3
"""Create and validate the compact M3 economic predeclaration receipt.

The compactor consumes only the tracked predeclaration manifest, tracked
upstream receipts/manifests, Git topology, and synthetic contract evidence.
It never opens a market-data file or an ignored economic output. C25 is
written as a deliberately blocked pre-CI receipt; D25 replaces only this
receipt and report after C25's remote CI is green.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, NoReturn

_REPO_BOOTSTRAP = Path(__file__).resolve().parents[1]
for _path in (_REPO_BOOTSTRAP / "src", _REPO_BOOTSTRAP / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import research_equity_m3_economic as contract  # noqa: E402
from m3_data_gate_helpers import canonical_digest  # noqa: E402

REPO = contract.REPO
PRE_CI = "NOT_RUN_BLOCKED"
REMOTE_PASS = "PASS"
PENDING_RESULT = contract.PENDING_C25_SELF_COMMIT


class PredeclarationError(ValueError):
    """A fail-closed predeclaration error."""


def _fail(message: str) -> NoReturn:
    raise PredeclarationError(message)


def _require_equal(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        _fail(f"{field} mismatch: expected {expected!r}, got {actual!r}")


def _full_commit(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        _fail(f"{field} must be a full lowercase commit SHA")
    return value


def _manifest_at(manifest_commit: str) -> dict[str, object]:
    return contract._json_blob(manifest_commit, contract.MANIFEST_PATH)  # type: ignore[attr-defined]


def _manifest_identity(manifest_commit: str) -> dict[str, str]:
    return contract.tracked_blob_identity(manifest_commit, contract.MANIFEST_PATH)


def _validate_manifest_chain(manifest_commit: str, implementation_commit: str) -> dict[str, object]:
    _full_commit(manifest_commit, "manifest commit")
    _full_commit(implementation_commit, "implementation commit")
    if contract.git_parents(manifest_commit) != [implementation_commit]:
        _fail("B25 manifest must be a direct child of A25")
    if contract.changed_paths(implementation_commit, manifest_commit) != [contract.MANIFEST_PATH]:
        _fail("B25 must change exactly the economic manifest")
    manifest = _manifest_at(manifest_commit)
    contract.validate_manifest_payload(manifest, implementation_commit, manifest_commit=manifest_commit)
    return manifest


def _validate_c25_result_chain(
    *,
    manifest_commit: str,
    local_result_commit: str | None,
    remote_ci_status: str,
    remote_ci_head_sha: str | None,
) -> None:
    if remote_ci_status == PRE_CI:
        _require_equal(local_result_commit, None, "pre-CI local result")
        _require_equal(remote_ci_head_sha, None, "pre-CI remote head")
        return
    _require_equal(remote_ci_status, REMOTE_PASS, "remote CI status")
    if local_result_commit is None:
        _fail("green CI requires a C25 result commit")
    if contract.git_parents(local_result_commit) != [manifest_commit]:
        _fail("C25 must be a direct child of B25")
    if set(contract.changed_paths(manifest_commit, local_result_commit)) != {
        contract.RECEIPT_PATH,
        contract.REPORT_PATH,
    }:
        _fail("C25 must change exactly the predeclaration receipt and report")
    _full_commit(remote_ci_head_sha, "remote CI head")
    _require_equal(remote_ci_head_sha, local_result_commit, "remote CI head/result")


def _remote_ci_claim(
    *,
    status: Literal["PASS", "NOT_RUN_BLOCKED"],
    run_id: str | None,
    head_sha: str | None,
    local_result_commit: str | None,
) -> dict[str, object]:
    if status == PRE_CI:
        if run_id not in (None, "", PRE_CI) or head_sha not in (None, "", PRE_CI):
            _fail("pre-CI receipt cannot claim a run or head")
        return {"status": PRE_CI, "run_id": None, "head_sha": None}
    if not isinstance(run_id, str) or re.fullmatch(r"[0-9]+", run_id) is None:
        _fail("green CI requires a numeric run ID")
    if local_result_commit is None:
        _fail("green CI requires a local result commit")
    _full_commit(head_sha, "green CI head")
    _require_equal(head_sha, local_result_commit, "green CI head/result")
    return {
        "status": REMOTE_PASS,
        "run_id": run_id,
        "head_sha": head_sha,
        "conclusion": "success",
        "python_matrix": copy.deepcopy(contract.CI_PYTHON_MATRIX),
        "required_stages": copy.deepcopy(contract.CI_REQUIRED_STAGES),
    }


def validate_predeclaration_receipt(
    receipt: Mapping[str, object],
    manifest: Mapping[str, object],
    *,
    expected_implementation_commit: str,
    expected_manifest_commit: str,
    expected_local_result_commit: str | None,
    expected_remote_status: Literal["PASS", "NOT_RUN_BLOCKED"],
) -> None:
    """Validate a compact receipt without observing economic results."""

    required = {
        "schema_version": "1.0.0",
        "mission": contract.MISSION,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "economic_contract_frozen": True,
        "economic_execution_run": False,
        "economic_results_present": False,
        "real_execution_authorized": False,
        "next_required_mission": "M3_ECONOMIC_MISSION_EXECUTION",
        "forbidden_economic_fields": [],
        "receipt_forbidden_real_result_fields": [],
    }
    for field, expected in required.items():
        _require_equal(receipt.get(field), expected, f"receipt {field}")
    if contract._forbidden_keys(receipt):  # type: ignore[attr-defined]
        _fail("predeclaration receipt contains a real economic result field")
    recorded_digest = receipt.get("self_digest_sha256")
    body = dict(receipt)
    body.pop("self_digest_sha256", None)
    _require_equal(recorded_digest, canonical_digest(body), "predeclaration receipt self-digest")

    topology = receipt.get("commit_topology")
    if not isinstance(topology, Mapping):
        _fail("receipt commit topology is missing")
    _require_equal(topology.get("starting_head"), contract.PREDECL_STARTING_HEAD, "receipt starting head")
    _require_equal(topology.get("implementation_commit"), expected_implementation_commit, "receipt implementation")
    _require_equal(topology.get("manifest_commit"), expected_manifest_commit, "receipt manifest")
    _require_equal(topology.get("manifest_parent"), expected_implementation_commit, "receipt manifest parent")
    expected_receipt_result = expected_local_result_commit if expected_remote_status == REMOTE_PASS else PENDING_RESULT
    _require_equal(topology.get("local_result_commit"), expected_receipt_result, "receipt result commit")

    implementation = receipt.get("implementation_identity")
    manifest_identity = receipt.get("manifest_identity")
    if not isinstance(implementation, Mapping) or not isinstance(manifest_identity, Mapping):
        _fail("receipt implementation/manifest identity is incomplete")
    _require_equal(implementation.get("commit"), expected_implementation_commit, "receipt implementation identity")
    actual_manifest = _manifest_identity(expected_manifest_commit)
    _require_equal(manifest_identity.get("commit"), expected_manifest_commit, "receipt manifest identity commit")
    _require_equal(manifest_identity.get("git_blob_id"), actual_manifest["git_blob_id"], "receipt manifest blob")
    _require_equal(manifest_identity.get("raw_sha256"), actual_manifest["raw_sha256"], "receipt manifest SHA")

    contract_fields = (
        ("exact_data_identities", contract.DATA_IDENTITIES),
        ("exact_observation_identities", contract.OBSERVATION_IDENTITIES),
        ("strategy_contract", contract.STRATEGY_CONTRACT),
        ("reference_contract", contract.REFERENCE_CONTRACT),
        ("evaluation_windows", contract.EVALUATION_WINDOWS),
        ("robustness_periods", contract.ROBUSTNESS_PERIODS),
        ("timing_contract", contract.TIMING_CONTRACT),
        ("baseline_configuration", contract.BASELINE_CONFIGURATION),
        ("cost_scenarios", contract.COST_SCENARIOS),
        ("metric_contract", contract.METRIC_CONTRACT),
        ("bootstrap_contract", contract.BOOTSTRAP_CONTRACT),
        ("acceptance_criteria", contract.ACCEPTANCE_CRITERIA),
        ("feed_transferability_boundary", contract.FEED_TRANSFERABILITY_BOUNDARY),
    )
    for field, expected in contract_fields:
        _require_equal(receipt.get(field), expected, f"receipt {field}")
    _require_equal(receipt.get("candidate_ocp_evidence"), contract.CANDIDATE_OCP_EVIDENCE, "receipt OCP evidence")
    gate_value = receipt.get("mandatory_gate_results")
    if not isinstance(gate_value, Mapping):
        _fail("receipt mandatory gates are missing")
    _require_equal(set(gate_value), set(contract.MANDATORY_PREDECLARATION_GATES), "receipt mandatory gate set")
    if any(not isinstance(value, Mapping) or value.get("status") != "PASS" for value in gate_value.values()):
        _fail("predeclaration mandatory gates are not all PASS")

    remote = receipt.get("remote_ci")
    if not isinstance(remote, Mapping):
        _fail("receipt remote CI claim is missing")
    if expected_remote_status == PRE_CI:
        _require_equal(receipt.get("verdict"), contract.BLOCKED_VERDICT, "pre-CI receipt verdict")
        _require_equal(receipt.get("blocked_reasons"), ["remote_ci:NOT_RUN_BLOCKED"], "pre-CI blocked reason")
        _require_equal(dict(remote), {"status": PRE_CI, "run_id": None, "head_sha": None}, "pre-CI remote CI")
    else:
        _require_equal(receipt.get("verdict"), contract.PREDECLARED_VERDICT, "final receipt verdict")
        _require_equal(receipt.get("blocked_reasons"), [], "final blocked reasons")
        _require_equal(remote.get("status"), REMOTE_PASS, "final remote CI status")
        _require_equal(remote.get("conclusion"), "success", "final remote CI conclusion")
        _require_equal(remote.get("head_sha"), expected_local_result_commit, "final remote CI head")
        _require_equal(remote.get("python_matrix"), contract.CI_PYTHON_MATRIX, "final Python matrix")
        _require_equal(remote.get("required_stages"), contract.CI_REQUIRED_STAGES, "final CI stages")

    _require_equal(receipt.get("upstream_data_gate"), manifest.get("upstream_data_gate"), "receipt R5 evidence")
    _require_equal(receipt.get("upstream_ocp_r2"), manifest.get("upstream_ocp_r2"), "receipt R2 evidence")


def _render_report(manifest: Mapping[str, object], receipt: Mapping[str, object]) -> str:
    remote = receipt["remote_ci"]
    status = receipt["verdict"]
    blocked = receipt["blocked_reasons"]
    gates = receipt["mandatory_gate_results"]
    lines = [
        "# M3 Economic Mission Predeclaration R1",
        "",
        "## Predeclaration status",
        "",
        f"- Mission: {contract.MISSION}",
        f"- Verdict: {status}",
        f"- Blocked reasons: {json.dumps(blocked, sort_keys=True)}",
        "- Economic contract frozen: true",
        "- Economic execution run: false",
        "- Economic results present: false",
        "- Real execution authorized: false",
        "- Next required mission: M3_ECONOMIC_MISSION_EXECUTION",
        "",
        "This is a before-results contract. It does not execute the real M3 or",
        "V3 economic backtest and contains no economic result.",
        "",
        "## Accepted upstream gates",
        "",
        f"- R5 data gate: M3_DATA_GATE_PASS, result {contract.R5_RESULT_COMMIT}, "
        f"receipt digest {contract.R5_RECEIPT_DIGEST}, 38/38 gates PASS.",
        f"- Candidate OCP R2: M3_CANDIDATE_OCP_ATTESTATION_R2_PASS, "
        f"receipt digest {contract.R2_RECEIPT_DIGEST}, corrective CI {contract.R2_CORRECTIVE_CI_RUN}.",
        "- The superseded unversioned OCP receipt is rejected and is not an economic prerequisite.",
        "",
        "## Exact data identities",
        "",
        f"{json.dumps(manifest['exact_data_identities'], indent=2, sort_keys=True)}",
        "",
        "## Exact observation identities",
        "",
        f"{json.dumps(manifest['exact_observation_identities'], indent=2, sort_keys=True)}",
        "",
        "C uses C|C with a fresh V3 execution instance. X uses X|X with a fresh",
        "M3 execution instance. X|C is diagnostic-only and cannot enter an",
        "economic comparison. Both runs share one clean commit, byte-identical",
        "inputs, sessions, PIT scanner pool, timestamps, lifetime data,",
        "corporate actions, terminal map, benchmark, capital, risk, allocation,",
        "planner, costs, fees, and terminal policy.",
        "",
        "## Frozen experiment contract",
        "",
        f"- Reference/pin contract: {json.dumps(manifest['reference_contract'], sort_keys=True)}",
        f"- Evaluation windows: {json.dumps(manifest['evaluation_windows'], sort_keys=True)}",
        f"- Robustness periods: {json.dumps(manifest['robustness_periods'], sort_keys=True)}",
        f"- Timing contract: {json.dumps(manifest['timing_contract'], sort_keys=True)}",
        f"- Baseline configuration: {json.dumps(manifest['baseline_configuration'], sort_keys=True)}",
        f"- Cost scenarios: {json.dumps(manifest['cost_scenarios'], sort_keys=True)}",
        f"- Bootstrap: {json.dumps(manifest['bootstrap_contract'], sort_keys=True)}",
        "",
        "The timing freeze is completed-bar signal ingestion, decision at the",
        "session timestamp, PIT snapshot selection at effective_at <= decision",
        "timestamp, order creation at that decision timestamp, no same-bar fill,",
        "next-tradable-bar execution, corporate actions before fills, T1 terminal",
        "handling after the final decision, and final valuation at the last",
        "included session. An order created on 2026-07-30 has no valid next bar and",
        "cannot be silently filled; no post-window bar enters economics.",
        "",
        "Required strategy metrics, candidate-minus-control active metrics,",
        "exposure diagnostics, calendar-year results, all four robustness periods,",
        "and the diagnostic bootstrap are listed in the manifest. Negative or bad",
        "future results must not be omitted. The frozen PASS/FAIL/BLOCKED criteria",
        "are evaluated only by the later economic mission.",
        "",
        "## Runner and authorization boundary",
        "",
        "- Dedicated runner: scripts/research_equity_m3_economic.py",
        "- No-argument invocation is non-executing and unauthorized.",
        "- Parameter, date, cost, arbitrary-strategy, M4, broker, and PAPER/LIVE paths are rejected.",
        "- Real execution additionally requires an explicit flag, a separate authorization artifact,",
        "  a committed manifest, clean topology, exact identities, and a new empty output directory.",
        "- The authorization artifact was not created in this mission.",
        "",
        "Historical feed is sip; configured PAPER/LIVE stock streaming is",
        "iex; feed_match=false. A future economic PASS can establish only",
        "historical research PASS, never PAPER/LIVE or promotion readiness.",
        "",
        "## Mandatory predeclaration gates",
        "",
        "| Gate | Status |",
        "| --- | --- |",
    ]
    if isinstance(gates, Mapping):
        lines.extend(f"| {name} | {value.get('status')} |" for name, value in sorted(gates.items()))
    lines.extend(
        [
            "",
            "Synthetic contract evidence is unit/integration-only: synthetic PASS",
            "returns M3_ECONOMIC_GATE_PASS, threshold failure returns",
            "M3_ECONOMIC_GATE_FAIL, and integrity failure returns",
            "M3_ECONOMIC_GATE_BLOCKED. No real economic result was generated.",
            "",
            "## Append-only publication chain",
            "",
            f"- Required starting head: {contract.PREDECL_STARTING_HEAD}.",
            f"- A25 implementation commit: {receipt['commit_topology']['implementation_commit']}.",
            f"- B25 manifest commit: {receipt['commit_topology']['manifest_commit']}; manifest-only.",
            f"- C25 local result commit: {receipt['commit_topology']['local_result_commit']}.",
            f"- Remote CI claim: {json.dumps(remote, sort_keys=True)}.",
            f"- Receipt self-digest: {receipt['self_digest_sha256']}.",
            "",
            f"Manifest-only proof: B25 changes exactly {contract.MANIFEST_PATH}.",
            f"C25 changes exactly {contract.RECEIPT_PATH} and {contract.REPORT_PATH}.",
            "D25 changes only the receipt and report after C25 CI is fully green.",
            "E25 changes only this report after D25 CI is fully green.",
            "",
            "## Scope confirmations",
            "",
            "No real economic backtest, real P&L, optimization, M4, promotion,",
            "PAPER/LIVE change, production behavior change, market-data access,",
            "credential access, IWM refetch, or history rewrite occurred.",
            "Economic execution remains unauthorized.",
            "",
        ]
    )
    return "\n".join(lines)


def compact(
    *,
    manifest_path: Path = REPO / contract.MANIFEST_PATH,
    receipt_path: Path = REPO / contract.RECEIPT_PATH,
    report_path: Path = REPO / contract.REPORT_PATH,
    implementation_commit: str,
    manifest_commit: str,
    local_result_commit: str | None,
    remote_ci_run: str | None,
    remote_ci_head_sha: str | None,
    remote_ci_status: Literal["PASS", "NOT_RUN_BLOCKED"],
) -> dict[str, object]:
    """Write a blocked C25 or green D25 compact receipt/report."""

    if manifest_path.resolve() != (REPO / contract.MANIFEST_PATH).resolve():
        _fail("manifest path must be the tracked R1 manifest")
    if receipt_path.resolve() != (REPO / contract.RECEIPT_PATH).resolve():
        _fail("receipt path must be the tracked R1 receipt")
    if report_path.resolve() != (REPO / contract.REPORT_PATH).resolve():
        _fail("report path must be the tracked R1 report")
    if contract.git_status():
        _fail("predeclaration compaction requires a clean worktree before output")
    if remote_ci_status not in (PRE_CI, REMOTE_PASS):
        _fail("remote CI status must be PASS or NOT_RUN_BLOCKED")
    current = contract.git_head()
    if remote_ci_status == PRE_CI:
        normalized_result = None
        normalized_run = None
        normalized_head = None
        _require_equal(current, manifest_commit, "C25 current head")
    else:
        normalized_result = _full_commit(local_result_commit, "C25 result commit")
        normalized_run = remote_ci_run
        normalized_head = _full_commit(remote_ci_head_sha, "C25 CI head")
        _require_equal(current, normalized_result, "D25 current head")

    contract._validate_implementation_commit(implementation_commit)  # type: ignore[attr-defined]
    manifest = _validate_manifest_chain(manifest_commit, implementation_commit)
    _validate_c25_result_chain(
        manifest_commit=manifest_commit,
        local_result_commit=normalized_result,
        remote_ci_status=remote_ci_status,
        remote_ci_head_sha=normalized_head,
    )
    upstream = contract.validate_upstream()
    _require_equal(upstream["data_gate"], manifest["upstream_data_gate"], "manifest R5 evidence")
    _require_equal(upstream["ocp_r2"], manifest["upstream_ocp_r2"], "manifest R2 evidence")
    if not contract.old_unversioned_ocp_rejected():
        _fail("old unversioned OCP receipt is accepted")

    manifest_identity = _manifest_identity(manifest_commit)
    local_result_for_receipt = normalized_result if remote_ci_status == REMOTE_PASS else PENDING_RESULT
    remote = _remote_ci_claim(
        status=remote_ci_status,
        run_id=normalized_run,
        head_sha=normalized_head,
        local_result_commit=normalized_result,
    )
    receipt: dict[str, object] = {
        "schema_version": "1.0.0",
        "mission": contract.MISSION,
        "verdict": contract.PREDECLARED_VERDICT if remote_ci_status == REMOTE_PASS else contract.BLOCKED_VERDICT,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "economic_contract_frozen": True,
        "economic_execution_run": False,
        "economic_results_present": False,
        "real_execution_authorized": False,
        "blocked_reasons": [] if remote_ci_status == REMOTE_PASS else ["remote_ci:NOT_RUN_BLOCKED"],
        "upstream_data_gate": copy.deepcopy(upstream["data_gate"]),
        "upstream_ocp_r2": copy.deepcopy(upstream["ocp_r2"]),
        "commit_topology": {
            "starting_head": contract.PREDECL_STARTING_HEAD,
            "implementation_parent": contract.PREDECL_STARTING_HEAD,
            "implementation_commit": implementation_commit,
            "manifest_parent": implementation_commit,
            "manifest_commit": manifest_commit,
            "local_result_commit": local_result_for_receipt,
            "result_parent": manifest_commit if normalized_result is not None else None,
            "manifest_path": contract.MANIFEST_PATH,
            "receipt_path": contract.RECEIPT_PATH,
            "report_path": contract.REPORT_PATH,
        },
        "implementation_identity": {
            "commit": implementation_commit,
            "parent": contract.PREDECL_STARTING_HEAD,
            "changed_paths": sorted(contract.IMPLEMENTATION_PATHS),
            "source_identities": copy.deepcopy(manifest["source_identities"]),
        },
        "manifest_identity": manifest_identity,
        "exact_data_identities": copy.deepcopy(contract.DATA_IDENTITIES),
        "exact_observation_identities": copy.deepcopy(contract.OBSERVATION_IDENTITIES),
        "candidate_ocp_evidence": copy.deepcopy(contract.CANDIDATE_OCP_EVIDENCE),
        "strategy_contract": copy.deepcopy(contract.STRATEGY_CONTRACT),
        "reference_contract": copy.deepcopy(contract.REFERENCE_CONTRACT),
        "evaluation_windows": copy.deepcopy(contract.EVALUATION_WINDOWS),
        "robustness_periods": copy.deepcopy(contract.ROBUSTNESS_PERIODS),
        "timing_contract": copy.deepcopy(contract.TIMING_CONTRACT),
        "baseline_configuration": copy.deepcopy(contract.BASELINE_CONFIGURATION),
        "cost_scenarios": copy.deepcopy(contract.COST_SCENARIOS),
        "metric_contract": copy.deepcopy(contract.METRIC_CONTRACT),
        "bootstrap_contract": copy.deepcopy(contract.BOOTSTRAP_CONTRACT),
        "acceptance_criteria": copy.deepcopy(contract.ACCEPTANCE_CRITERIA),
        "feed_transferability_boundary": copy.deepcopy(contract.FEED_TRANSFERABILITY_BOUNDARY),
        "future_raw_output_contract": copy.deepcopy(contract.RAW_RESULT_CONTRACT),
        "runner_guards": copy.deepcopy(manifest["runner_guards"]),
        "mandatory_gate_results": {name: {"status": "PASS"} for name in contract.MANDATORY_PREDECLARATION_GATES},
        "forbidden_economic_fields": [],
        "receipt_forbidden_real_result_fields": [],
        "remote_ci": remote,
        "next_required_mission": "M3_ECONOMIC_MISSION_EXECUTION",
    }
    receipt["self_digest_sha256"] = canonical_digest(receipt)
    validate_predeclaration_receipt(
        receipt,
        manifest,
        expected_implementation_commit=implementation_commit,
        expected_manifest_commit=manifest_commit,
        expected_local_result_commit=normalized_result if remote_ci_status == REMOTE_PASS else None,
        expected_remote_status=remote_ci_status,
    )
    report = _render_report(manifest, receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--manifest-commit", required=True)
    parser.add_argument("--local-result-commit")
    parser.add_argument("--remote-ci-run")
    parser.add_argument("--remote-ci-head-sha")
    parser.add_argument("--remote-ci-status", choices=(REMOTE_PASS, PRE_CI), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = compact(
            manifest_path=args.manifest,
            receipt_path=args.receipt,
            report_path=args.report,
            implementation_commit=args.implementation_commit,
            manifest_commit=args.manifest_commit,
            local_result_commit=args.local_result_commit,
            remote_ci_run=args.remote_ci_run,
            remote_ci_head_sha=args.remote_ci_head_sha,
            remote_ci_status=args.remote_ci_status,
        )
    except (PredeclarationError, contract.ContractError) as exc:
        print(f"{contract.BLOCKED_VERDICT}: {exc}")
        return contract.BLOCKED_EXIT
    print(
        json.dumps(
            {
                "verdict": receipt["verdict"],
                "receipt": str(args.receipt),
                "self_digest_sha256": receipt["self_digest_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["verdict"] == contract.PREDECLARED_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PRE_CI",
    "REMOTE_PASS",
    "PredeclarationError",
    "compact",
    "main",
    "validate_predeclaration_receipt",
]
