#!/usr/bin/env python3
"""Compact the executed M3 economic result into the C26/D26/E26 chain.

The raw result is produced only by the explicitly authorized offline runner
and remains ignored.  C26 records a pre-CI blocked receipt, D26 closes that
receipt after C26 remote CI is green, and E26 appends the final documentation
closure after D26 remote CI is green.  This compactor never reads market data
and never reruns an economic cell.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
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
PENDING_RESULT = contract.PENDING_C26_SELF_COMMIT


class EconomicResultError(ValueError):
    """A fail-closed raw-result or publication-chain error."""


def _fail(message: str) -> NoReturn:
    raise EconomicResultError(message)


def _require_equal(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        _fail(f"{field} mismatch: expected {expected!r}, got {actual!r}")


def _full_commit(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        _fail(f"{field} must be a full lowercase commit SHA")
    return value


def _numeric_run(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        _fail(f"{field} must be a numeric GitHub Actions run ID")
    return value


def _tracked_path(path: Path, expected: str, field: str) -> None:
    if path.resolve() != (REPO / expected).resolve():
        _fail(f"{field} must be {expected}")


def _load_json(path: Path, field: str) -> dict[str, object]:
    def reject_constant(value: str) -> NoReturn:
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EconomicResultError(f"{field} is invalid: {path}") from exc
    if not isinstance(value, dict):
        _fail(f"{field} must be an object")
    return value


def _raw_result_path(path: Path) -> None:
    expected = REPO / contract.EXECUTION_RAW_OUTPUT_DIR / contract.EXECUTION_RESULT_FILENAME
    if path.resolve() != expected.resolve():
        _fail("raw result path must be the exact ignored execution result")


def _raw_result_identity(path: Path, result: Mapping[str, object]) -> dict[str, str]:
    body = dict(result)
    raw_digest = body.pop("raw_mission_digest", None)
    _require_equal(raw_digest, canonical_digest(body), "raw mission digest")
    semantic_digest = result.get("semantic_result_digest")
    _require_equal(
        semantic_digest,
        contract._semantic_digest_without_runtime(body),  # type: ignore[attr-defined]
        "semantic result digest",
    )
    return {
        "path": str(path.relative_to(REPO)),
        "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "raw_mission_digest": str(raw_digest),
        "semantic_result_digest": str(semantic_digest),
    }


def _validate_manifest(manifest_commit: str, implementation_commit: str) -> dict[str, object]:
    _full_commit(manifest_commit, "authorization manifest commit")
    _full_commit(implementation_commit, "implementation commit")
    if contract.git_parents(manifest_commit) != [implementation_commit]:
        _fail("B26 authorization manifest must be a direct child of A26")
    if contract.changed_paths(implementation_commit, manifest_commit) != [contract.EXECUTION_MANIFEST_PATH]:
        _fail("B26 must change exactly the execution authorization manifest")
    manifest = contract._json_blob(manifest_commit, contract.EXECUTION_MANIFEST_PATH)  # type: ignore[attr-defined]
    contract.validate_execution_manifest_payload(manifest, implementation_commit, manifest_commit=manifest_commit)
    return manifest


def _validate_raw_result(
    path: Path,
    *,
    manifest_commit: str,
    implementation_commit: str,
) -> tuple[dict[str, object], dict[str, str]]:
    _raw_result_path(path)
    result = _load_json(path, "raw economic result")
    _require_equal(result.get("schema_version"), contract.EXECUTION_RESULT_SCHEMA, "raw result schema")
    _require_equal(result.get("mission"), contract.EXECUTION_MISSION, "raw result mission")
    identity = _raw_result_identity(path, result)
    authorization = result.get("authorization")
    if not isinstance(authorization, Mapping):
        _fail("raw result authorization binding is missing")
    manifest_identity = authorization.get("manifest")
    _require_equal(
        manifest_identity,
        contract.tracked_blob_identity(manifest_commit, contract.EXECUTION_MANIFEST_PATH),
        "raw result authorization manifest identity",
    )
    _require_equal(authorization.get("implementation_commit"), implementation_commit, "raw result implementation")
    if result.get("result_complete") is True:
        required = {
            "verdict": (contract.PASS_VERDICT, contract.FAIL_VERDICT),
            "blocked_reasons": [],
            "economic_execution_run": True,
            "economic_results_present": True,
            "candidate_economics_run": True,
            "economic_metrics_present": True,
            "economic_mission_authorized": True,
        }
        for field, expected in required.items():
            actual = result.get(field)
            if field == "verdict":
                if actual not in expected:
                    _fail(f"raw result {field} is not an economic PASS or FAIL")
            else:
                _require_equal(actual, expected, f"raw result {field}")
        passes = result.get("passes")
        if not isinstance(passes, Mapping) or set(passes) != {"A", "B", "C"}:
            _fail("raw result must contain exactly passes A, B, and C")
        for pass_name, pass_result in passes.items():
            if not isinstance(pass_result, Mapping):
                _fail(f"raw result pass {pass_name} is invalid")
            _require_equal(pass_result.get("strategy_cell_count"), 16, f"raw result pass {pass_name} cells")
            _require_equal(pass_result.get("active_pair_count"), 8, f"raw result pass {pass_name} active pairs")
            cells = pass_result.get("cells")
            active_results = pass_result.get("active_results")
            if not isinstance(cells, Mapping) or len(cells) != 16 or not isinstance(active_results, Mapping):
                _fail(f"raw result pass {pass_name} matrix is incomplete")
        acceptance = result.get("acceptance")
        integrity = result.get("integrity")
        if not isinstance(acceptance, Mapping) or not isinstance(integrity, Mapping):
            _fail("raw result acceptance/integrity sections are missing")
        _require_equal(acceptance.get("verdict"), result.get("verdict"), "raw result acceptance verdict")
        if any(value is not True for value in integrity.values()):
            _fail("raw result marked complete with a failed integrity gate")
    else:
        _require_equal(result.get("verdict"), contract.ECONOMIC_BLOCKED_VERDICT, "blocked raw result verdict")
        if not isinstance(result.get("blocked_reasons"), list) or not result["blocked_reasons"]:
            _fail("blocked raw result must carry a blocker")
        _require_equal(result.get("economic_execution_run"), False, "blocked raw execution flag")
        _require_equal(result.get("economic_results_present"), False, "blocked raw results flag")
    contract._assert_finite_json(result)  # type: ignore[attr-defined]
    return result, identity


def _summary(result: Mapping[str, object]) -> dict[str, object]:
    if result.get("result_complete") is not True:
        return {}
    passes = result.get("passes")
    if not isinstance(passes, Mapping) or not isinstance(passes.get("A"), Mapping):
        return {}
    pass_a = passes["A"]
    cells = pass_a.get("cells")
    active = pass_a.get("active_results")
    if not isinstance(cells, Mapping) or not isinstance(active, Mapping):
        return {}
    baseline_full = active.get("FULL/BASELINE_1X")
    baseline_recent = active.get("RECENT/BASELINE_1X")
    baseline_full_c = cells.get("FULL/BASELINE_1X/C")
    baseline_full_x = cells.get("FULL/BASELINE_1X/X")
    return {
        "baseline_full": copy.deepcopy(baseline_full),
        "baseline_recent": copy.deepcopy(baseline_recent),
        "baseline_full_control_metrics": copy.deepcopy(
            baseline_full_c.get("metrics") if isinstance(baseline_full_c, Mapping) else {}
        ),
        "baseline_full_candidate_metrics": copy.deepcopy(
            baseline_full_x.get("metrics") if isinstance(baseline_full_x, Mapping) else {}
        ),
        "acceptance": copy.deepcopy(result.get("acceptance", {})),
        "integrity": copy.deepcopy(result.get("integrity", {})),
        "bootstrap": copy.deepcopy(result.get("bootstrap", {})),
    }


def _remote_claim(
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
    run = _numeric_run(run_id, "remote CI run")
    head = _full_commit(head_sha, "remote CI head")
    local = _full_commit(local_result_commit, "local result commit")
    _require_equal(head, local, "remote CI head/result")
    return {
        "status": REMOTE_PASS,
        "run_id": run,
        "head_sha": head,
        "conclusion": "success",
        "python_matrix": copy.deepcopy(contract.EXECUTION_CI_PYTHON_MATRIX),
        "required_stages": copy.deepcopy(contract.EXECUTION_CI_REQUIRED_STAGES),
    }


def _build_receipt(
    *,
    manifest: Mapping[str, object],
    implementation_commit: str,
    manifest_commit: str,
    result: Mapping[str, object],
    raw_identity: Mapping[str, str],
    remote_status: Literal["PASS", "NOT_RUN_BLOCKED"],
    remote_run: str | None,
    remote_head: str | None,
    result_commit: str | None,
) -> dict[str, object]:
    complete = result.get("result_complete") is True
    raw_verdict = result.get("verdict")
    if remote_status == PRE_CI:
        compact_verdict = contract.ECONOMIC_BLOCKED_VERDICT
        blocked_reasons: list[str] = ["remote_ci:NOT_RUN_BLOCKED"]
        local_result = PENDING_RESULT
        result_parent: str | None = None
    else:
        compact_verdict = raw_verdict
        raw_blocked = result.get("blocked_reasons", [])
        blocked_reasons = list(raw_blocked) if isinstance(raw_blocked, list) else [str(raw_blocked)]
        local_result = _full_commit(result_commit, "result commit")
        result_parent = manifest_commit
        if contract.git_parents(local_result) != [manifest_commit]:
            _fail("C26 result commit must be a direct child of B26")
        if set(contract.changed_paths(manifest_commit, local_result)) != {
            contract.EXECUTION_RECEIPT_PATH,
            contract.EXECUTION_REPORT_PATH,
        }:
            _fail("C26 must change exactly the execution receipt and report")
        if not isinstance(compact_verdict, str):
            _fail("raw economic verdict is missing")
    implementation_parent = contract.git_parents(implementation_commit)[0]
    remote = _remote_claim(
        status=remote_status,
        run_id=remote_run,
        head_sha=remote_head,
        local_result_commit=result_commit if remote_status == REMOTE_PASS else None,
    )
    topology = {
        "starting_head": contract.STARTING_HEAD,
        "implementation_parent": implementation_parent,
        "implementation_commit": implementation_commit,
        "manifest_parent": implementation_commit,
        "manifest_commit": manifest_commit,
        "result_parent": result_parent,
        "local_result_commit": local_result,
        "authorization_manifest_path": contract.EXECUTION_MANIFEST_PATH,
        "receipt_path": contract.EXECUTION_RECEIPT_PATH,
        "report_path": contract.EXECUTION_REPORT_PATH,
    }
    implementation_identity = manifest.get("implementation_identity")
    if not isinstance(implementation_identity, Mapping):
        _fail("authorization implementation identity is missing")
    manifest_identity = contract.tracked_blob_identity(manifest_commit, contract.EXECUTION_MANIFEST_PATH)
    receipt: dict[str, object] = {
        "schema_version": "1.0.0",
        "mission": contract.EXECUTION_MISSION,
        "verdict": compact_verdict,
        "raw_economic_verdict": raw_verdict,
        "blocked_reasons": blocked_reasons,
        "raw_blocked_reasons": copy.deepcopy(result.get("blocked_reasons", [])),
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "economic_execution_run": complete,
        "economic_results_present": complete,
        "candidate_economics_run": complete,
        "economic_metrics_present": complete,
        "economic_mission_authorized": True,
        "real_execution_authorized": True,
        "authorization_id": manifest.get("authorization_id"),
        "operator_id": manifest.get("operator_id"),
        "commit_topology": topology,
        "implementation_identity": copy.deepcopy(implementation_identity),
        "manifest_identity": manifest_identity,
        "authorization_manifest": {
            "mission": contract.EXECUTION_MISSION,
            "commit": manifest_commit,
            "git_blob_id": manifest_identity["git_blob_id"],
            "raw_sha256": manifest_identity["raw_sha256"],
        },
        "predeclaration_closure": copy.deepcopy(contract.PREDECLARATION_CLOSURE),
        "upstream": copy.deepcopy(result.get("upstream", {})),
        "exact_data_identities": copy.deepcopy(contract.DATA_IDENTITIES),
        "accepted_input_identities": copy.deepcopy(result.get("accepted_input_identities", {})),
        "exact_observation_identities": copy.deepcopy(contract.OBSERVATION_IDENTITIES),
        "strategy_contract": copy.deepcopy(contract.STRATEGY_CONTRACT),
        "reference_contract": copy.deepcopy(contract.REFERENCE_CONTRACT),
        "full_pin_order": list(contract.FULL_PIN_ORDER),
        "tradable_reserved_inverse_hedges": sorted(contract.TRADABLE_RESERVED_INVERSE_HEDGES),
        "execution_addendum": copy.deepcopy(contract.EXECUTION_ADDENDUM),
        "evaluation_windows": copy.deepcopy(contract.EVALUATION_WINDOWS),
        "robustness_periods": copy.deepcopy(contract.ROBUSTNESS_PERIODS),
        "timing_contract": copy.deepcopy(contract.TIMING_CONTRACT),
        "baseline_configuration": copy.deepcopy(contract.BASELINE_CONFIGURATION),
        "cost_scenarios": copy.deepcopy(contract.COST_SCENARIOS),
        "metric_contract": copy.deepcopy(contract.METRIC_CONTRACT),
        "bootstrap_contract": copy.deepcopy(contract.BOOTSTRAP_CONTRACT),
        "acceptance_criteria": copy.deepcopy(contract.ACCEPTANCE_CRITERIA),
        "pass_contract": copy.deepcopy(manifest.get("pass_orders", {})),
        "execution_count": 48,
        "acceptance": copy.deepcopy(result.get("acceptance", {})),
        "integrity": copy.deepcopy(result.get("integrity", {})),
        "economic_summary": _summary(result),
        "bootstrap": copy.deepcopy(result.get("bootstrap", {})),
        "raw_result_identity": dict(raw_identity),
        "feed_transferability_boundary": copy.deepcopy(contract.FEED_TRANSFERABILITY_BOUNDARY),
        "remote_ci": remote,
        "next_required_mission": "M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE",
        "receipt_forbidden_real_result_fields": [],
    }
    receipt["self_digest_sha256"] = canonical_digest(receipt)
    return receipt


def _validate_receipt_self(receipt: Mapping[str, object]) -> None:
    recorded = receipt.get("self_digest_sha256")
    body = dict(receipt)
    body.pop("self_digest_sha256", None)
    _require_equal(recorded, canonical_digest(body), "execution receipt self-digest")


def _render_report(
    manifest: Mapping[str, object],
    receipt: Mapping[str, object],
    *,
    phase: Literal["C", "D"],
) -> str:
    summary = receipt.get("economic_summary")
    acceptance = receipt.get("acceptance")
    integrity = receipt.get("integrity")
    topology = receipt["commit_topology"]
    remote = receipt["remote_ci"]
    lines = [
        "# M3 Economic Mission Execution R1",
        "",
        "## Mission result",
        "",
        f"- Mission: {contract.EXECUTION_MISSION}",
        f"- Compact phase: {phase}26",
        f"- Compact verdict: {receipt['verdict']}",
        f"- Raw economic verdict: {receipt['raw_economic_verdict']}",
        f"- Blocked reasons: {json.dumps(receipt['blocked_reasons'], sort_keys=True)}",
        f"- Economic execution run: {str(receipt['economic_execution_run']).lower()}",
        f"- Candidate economics run: {str(receipt['candidate_economics_run']).lower()}",
        f"- Economic metrics present: {str(receipt['economic_metrics_present']).lower()}",
        f"- Economic mission authorized: {str(receipt['economic_mission_authorized']).lower()}",
        "- Research only: true; automatic promotion: false; human promotion only: true.",
        f"- Next required mission: {receipt['next_required_mission']}",
        "",
        "The raw result is an ignored artifact under the exact predeclared output",
        "directory. This tracked receipt contains the compact provenance binding",
        "and economic summary; it does not rerun any strategy or data gate.",
        "",
        "## Authorization and upstream closure",
        "",
        f"- A26 implementation: {topology['implementation_commit']}",
        f"- B26 authorization manifest: {topology['manifest_commit']}",
        f"- C26 result commit: {topology['local_result_commit']}",
        f"- Authorization ID: {receipt['authorization_id']}; operator: {receipt['operator_id']}.",
        f"- Predeclaration closure: {json.dumps(receipt['predeclaration_closure'], sort_keys=True)}",
        f"- R5/R2 upstream: {json.dumps(receipt['upstream'], sort_keys=True)}",
        f"- Raw result identity: {json.dumps(receipt['raw_result_identity'], sort_keys=True)}",
        "",
        "## Remote CI claim",
        "",
        f"- {json.dumps(remote, sort_keys=True)}",
        "",
        "## Baseline economic summary",
        "",
    ]
    if isinstance(summary, Mapping) and summary:
        full_active = summary.get("baseline_full")
        recent_active = summary.get("baseline_recent")
        lines.extend(
            [
                "| Window | Active total return | CAGR difference | Sharpe difference | Drawdown difference | Turnover ratio |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for label, active in (("FULL", full_active), ("RECENT", recent_active)):
            if isinstance(active, Mapping):
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            label,
                            f"{float(active['active total return']):.12g}",
                            f"{float(active['CAGR difference']):.12g}",
                            f"{float(active['Sharpe difference']):.12g}",
                            f"{float(active['maximum-drawdown difference']):.12g}",
                            f"{float(active['turnover ratio']):.12g}",
                        ]
                    )
                    + " |"
                )
        lines.extend(
            [
                "",
                f"- Acceptance: {json.dumps(acceptance, sort_keys=True)}",
                f"- Integrity: {json.dumps(integrity, sort_keys=True)}",
                f"- Bootstrap: {json.dumps(summary.get('bootstrap', {}), sort_keys=True)}",
            ]
        )
    else:
        lines.append("No complete economic matrix was available; the raw result is blocked.")
    lines.extend(
        [
            "",
            "## Frozen execution contract",
            "",
            f"- Data identities: {json.dumps(receipt['exact_data_identities'], sort_keys=True)}",
            f"- Observation identities: {json.dumps(receipt['exact_observation_identities'], sort_keys=True)}",
            f"- Reference roles: {json.dumps(receipt['reference_contract'], sort_keys=True)}",
            f"- Pass order and count: {json.dumps(receipt['pass_contract'], sort_keys=True)}; 48 executions.",
            f"- Timing: {json.dumps(receipt['timing_contract'], sort_keys=True)}",
            f"- Execution addendum: {json.dumps(receipt['execution_addendum'], sort_keys=True)}",
            "",
            "SPY, IWM, and QQQ are observation-only references. SH, PSQ, and DOG",
            "remain eligible tradable reserved inverse-hedge pins when the frozen",
            "PIT scanner and strategy gates select them. C is C|C and X is X|X;",
            "X|C is not an economic result. The historical feed is SIP and the",
            "configured PAPER/LIVE stream is IEX, so feed_match=false and this result",
            "does not establish PAPER/LIVE or promotion readiness.",
            "",
            "## Scope confirmations",
            "",
            "No market-data network access, broker path, credential access, IWM",
            "refetch, OCP replay, M3 data-gate replay, optimization, tournament,",
            "promotion, PAPER/LIVE change, strategy/risk/config change, production",
            "behavior change, or history rewrite occurred during execution.",
            "",
            f"Receipt self-digest: {receipt['self_digest_sha256']}.",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_common_paths(manifest_path: Path, receipt_path: Path, report_path: Path) -> None:
    _tracked_path(manifest_path, contract.EXECUTION_MANIFEST_PATH, "authorization manifest path")
    _tracked_path(receipt_path, contract.EXECUTION_RECEIPT_PATH, "execution receipt path")
    _tracked_path(report_path, contract.EXECUTION_REPORT_PATH, "execution report path")
    if contract.git_status():
        _fail("economic result compaction requires a clean worktree before output")


def compact_c(
    *,
    manifest_path: Path,
    receipt_path: Path,
    report_path: Path,
    raw_result_path: Path,
    implementation_commit: str,
    manifest_commit: str,
) -> dict[str, object]:
    _validate_common_paths(manifest_path, receipt_path, report_path)
    _require_equal(contract.git_head(), manifest_commit, "C26 current head")
    if receipt_path.exists() or report_path.exists():
        _fail("C26 receipt/report already exists")
    manifest = _validate_manifest(manifest_commit, implementation_commit)
    result, identity = _validate_raw_result(
        raw_result_path,
        manifest_commit=manifest_commit,
        implementation_commit=implementation_commit,
    )
    receipt = _build_receipt(
        manifest=manifest,
        implementation_commit=implementation_commit,
        manifest_commit=manifest_commit,
        result=result,
        raw_identity=identity,
        remote_status=PRE_CI,
        remote_run=None,
        remote_head=None,
        result_commit=None,
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(manifest, receipt, phase="C"), encoding="utf-8")
    _validate_receipt_self(_load_json(receipt_path, "C26 receipt"))
    return receipt


def compact_d(
    *,
    manifest_path: Path,
    receipt_path: Path,
    report_path: Path,
    raw_result_path: Path,
    implementation_commit: str,
    manifest_commit: str,
    result_commit: str,
    remote_ci_run: str,
    remote_ci_head: str,
) -> dict[str, object]:
    _validate_common_paths(manifest_path, receipt_path, report_path)
    _require_equal(contract.git_head(), result_commit, "D26 current head")
    if contract.git_parents(result_commit) != [manifest_commit]:
        _fail("D26 input must be the direct-child C26 result commit")
    if set(contract.changed_paths(manifest_commit, result_commit)) != {
        contract.EXECUTION_RECEIPT_PATH,
        contract.EXECUTION_REPORT_PATH,
    }:
        _fail("D26 input C26 must change exactly the execution receipt and report")
    manifest = _validate_manifest(manifest_commit, implementation_commit)
    prior_receipt = _load_json(receipt_path, "C26 receipt")
    _validate_receipt_self(prior_receipt)
    _require_equal(prior_receipt.get("verdict"), contract.ECONOMIC_BLOCKED_VERDICT, "C26 compact verdict")
    _require_equal(prior_receipt.get("blocked_reasons"), ["remote_ci:NOT_RUN_BLOCKED"], "C26 blocker")
    result, identity = _validate_raw_result(
        raw_result_path,
        manifest_commit=manifest_commit,
        implementation_commit=implementation_commit,
    )
    receipt = _build_receipt(
        manifest=manifest,
        implementation_commit=implementation_commit,
        manifest_commit=manifest_commit,
        result=result,
        raw_identity=identity,
        remote_status=REMOTE_PASS,
        remote_run=remote_ci_run,
        remote_head=remote_ci_head,
        result_commit=result_commit,
    )
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(manifest, receipt, phase="D"), encoding="utf-8")
    _validate_receipt_self(_load_json(receipt_path, "D26 receipt"))
    return receipt


def compact_e(
    *,
    receipt_path: Path,
    report_path: Path,
    c_commit: str,
    c_ci_run: str,
    d_commit: str,
    d_ci_run: str,
) -> None:
    _tracked_path(receipt_path, contract.EXECUTION_RECEIPT_PATH, "execution receipt path")
    _tracked_path(report_path, contract.EXECUTION_REPORT_PATH, "execution report path")
    if contract.git_status():
        _fail("E26 report closure requires a clean worktree before output")
    _require_equal(contract.git_head(), d_commit, "E26 current head")
    c = _full_commit(c_commit, "C26 commit")
    d = _full_commit(d_commit, "D26 commit")
    if contract.git_parents(d) != [c]:
        _fail("D26 must be a direct child of C26")
    if set(contract.changed_paths(c, d)) != {contract.EXECUTION_RECEIPT_PATH, contract.EXECUTION_REPORT_PATH}:
        _fail("D26 must change exactly the execution receipt and report")
    _numeric_run(c_ci_run, "C26 CI run")
    _numeric_run(d_ci_run, "D26 CI run")
    receipt = _load_json(receipt_path, "D26 receipt")
    _validate_receipt_self(receipt)
    topology = receipt.get("commit_topology")
    if not isinstance(topology, Mapping):
        _fail("D26 receipt topology is missing")
    _require_equal(topology.get("local_result_commit"), c, "D26 receipt C26 result")
    remote = receipt.get("remote_ci")
    if not isinstance(remote, Mapping):
        _fail("D26 receipt remote CI is missing")
    _require_equal(remote.get("status"), REMOTE_PASS, "D26 receipt remote status")
    _require_equal(remote.get("head_sha"), c, "D26 receipt remote head")
    report = report_path.read_text(encoding="utf-8")
    if "## E26 final remote closure" in report:
        _fail("E26 final closure already exists")
    closure = [
        "",
        "## E26 final remote closure",
        "",
        f"- C26 full SHA: {c}",
        f"- C26 CI run: {c_ci_run}",
        "- C26 Python 3.12: PASS",
        "- C26 Python 3.13: PASS",
        "- C26 Python 3.14: PASS",
        f"- D26 full SHA: {d}",
        f"- D26 CI run: {d_ci_run}",
        "- D26 Python 3.12: PASS",
        "- D26 Python 3.13: PASS",
        "- D26 Python 3.14: PASS",
        "- Final candidate-OCP economic execution remote closure: complete.",
        "- The receipt is unchanged in E26.",
        "",
    ]
    report_path.write_text(report.rstrip() + "\n" + "\n".join(closure), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("C", "D", "E"), required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--raw-result", type=Path)
    parser.add_argument("--implementation-commit")
    parser.add_argument("--manifest-commit")
    parser.add_argument("--result-commit")
    parser.add_argument("--remote-ci-run")
    parser.add_argument("--remote-ci-head")
    parser.add_argument("--c-commit")
    parser.add_argument("--c-ci-run")
    parser.add_argument("--d-commit")
    parser.add_argument("--d-ci-run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.phase in {"C", "D"}:
            if not all(
                isinstance(value, Path) for value in (args.manifest, args.receipt, args.report, args.raw_result)
            ):
                _fail("C26/D26 require manifest, receipt, report, and raw result paths")
            if not all(
                isinstance(value, str) and value for value in (args.implementation_commit, args.manifest_commit)
            ):
                _fail("C26/D26 require implementation and manifest commits")
            if args.phase == "C":
                receipt = compact_c(
                    manifest_path=args.manifest,
                    receipt_path=args.receipt,
                    report_path=args.report,
                    raw_result_path=args.raw_result,
                    implementation_commit=args.implementation_commit,
                    manifest_commit=args.manifest_commit,
                )
            else:
                if not all(
                    isinstance(value, str) and value
                    for value in (args.result_commit, args.remote_ci_run, args.remote_ci_head)
                ):
                    _fail("D26 requires result commit and remote CI claim")
                receipt = compact_d(
                    manifest_path=args.manifest,
                    receipt_path=args.receipt,
                    report_path=args.report,
                    raw_result_path=args.raw_result,
                    implementation_commit=args.implementation_commit,
                    manifest_commit=args.manifest_commit,
                    result_commit=args.result_commit,
                    remote_ci_run=args.remote_ci_run,
                    remote_ci_head=args.remote_ci_head,
                )
            print(
                json.dumps(
                    {
                        "phase": args.phase,
                        "verdict": receipt["verdict"],
                        "receipt": str(args.receipt),
                        "self_digest_sha256": receipt["self_digest_sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if not all(
            isinstance(value, str) and value for value in (args.c_commit, args.c_ci_run, args.d_commit, args.d_ci_run)
        ):
            _fail("E26 requires C/D commits and CI run IDs")
        compact_e(
            receipt_path=args.receipt,
            report_path=args.report,
            c_commit=args.c_commit,
            c_ci_run=args.c_ci_run,
            d_commit=args.d_commit,
            d_ci_run=args.d_ci_run,
        )
        print(json.dumps({"phase": "E", "report": str(args.report)}))
        return 0
    except (EconomicResultError, contract.ContractError) as exc:
        print(f"{contract.ECONOMIC_BLOCKED_VERDICT}: {exc}")
        return contract.BLOCKED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EconomicResultError", "compact_c", "compact_d", "compact_e", "main"]
