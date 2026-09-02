#!/usr/bin/env python3
"""Compact and validate the ignored real-data M3 OCP run artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from parayabuck.backtest.observation_parity import canonical_digest

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RAW = REPO / "data/research/generated/equity_m3_candidate_ocp_2026_09/ocp_run.json"
DEFAULT_OUTPUT = REPO / "data/research_receipts/equity_m3_candidate_ocp_2026_09.json"
MISSION = "M3_CANDIDATE_OCP_GATE"
PASS = "M3_CANDIDATE_OCP_GATE_PASS"
BLOCKED = "M3_CANDIDATE_OCP_GATE_BLOCKED"

FORBIDDEN_ECONOMIC_FIELDS = frozenset(
    {
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
        "score",
        "scores",
        "raw_scores",
        "candidate_scores",
        "economic_scores",
        "ranked_scores",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _forbidden_keys(value: object) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key).lower() in FORBIDDEN_ECONOMIC_FIELDS:
                    found.add(str(key))
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(found))


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("raw OCP artifact must be a JSON object")
    return value


def validate_raw(raw: Mapping[str, object]) -> None:
    if raw.get("mission") != MISSION:
        raise ValueError("raw OCP mission mismatch")
    if raw.get("research_only") is not True or raw.get("candidate_economics_run") is not False:
        raise ValueError("raw OCP artifact is not research-only")
    if (
        raw.get("economic_metrics_present") is not False
        or raw.get("economic_mission_authorized") is not False
        or raw.get("economic_mission_run", False) is not False
    ):
        raise ValueError("raw OCP artifact crosses the economic boundary")
    if raw.get("verdict") not in {PASS, BLOCKED}:
        raise ValueError("raw OCP verdict is missing or invalid")
    declared = raw.get("raw_digest_sha256")
    body = dict(raw)
    body.pop("raw_digest_sha256", None)
    if not isinstance(declared, str) or canonical_digest(body) != declared:
        raise ValueError("raw OCP canonical digest mismatch")
    forbidden = _forbidden_keys(raw)
    if forbidden:
        raise ValueError(f"raw OCP contains forbidden economic fields: {forbidden}")
    gates = raw.get("mandatory_gate_results")
    if not isinstance(gates, Mapping):
        raise ValueError("raw OCP mandatory gate map is missing")
    required = {
        "commit_topology",
        "implementation_blob_identity",
        "manifest_only_commit",
        "clean_worktree_at_start",
        "production_isolation",
        "r5_data_gate_receipt_identity",
        "r5_data_gate_verdict",
        "r5_all_mandatory_gates",
        "r5_upstream_erratum_acknowledged",
        "r4_raw_bundle_identity",
        "r4_sidecar_identity",
        "r4_composite_identity",
        "r5_projection_identity",
        "scanner_identity",
        "scanner_pool_identity",
        "scanner_fallback_identity",
        "exact_snapshot_alignment",
        "reference_role_contract",
        "reference_pin_order",
        "observation_capacity",
        "strictly_prior_history",
        "no_future_bar_leak",
        "no_outside_pool_observation",
        "no_duplicate_observation",
        "no_stale_observation",
        "C_control_strategy_identity",
        "C_control_repeat_determinism",
        "C_control_self_parity",
        "X_candidate_strategy_identity",
        "X_observer_execution_identity",
        "X_fresh_instance_separation",
        "X_repeat_determinism",
        "X_direct_builder_parity",
        "X_candidate_owned_basis",
        "X_vs_C_scanner_metadata_parity",
        "X_vs_C_nonzero_observation_change",
        "C_X_boundary_contract",
        "raw_artifact_identity",
        "compact_receipt_integrity",
        "forbidden_economic_fields",
        "no_economic_execution",
        "no_network_or_credentials",
    }
    if set(gates) != required:
        raise ValueError("raw OCP mandatory gate set differs from the frozen contract")
    statuses = []
    for value in gates.values():
        if not isinstance(value, Mapping) or value.get("status") not in {"PASS", "FAIL", "UNKNOWN", "NOT_RUN_BLOCKED"}:
            raise ValueError("raw OCP contains an invalid mandatory gate state")
        statuses.append(value.get("status"))
    if raw.get("verdict") == PASS and any(status != "PASS" for status in statuses):
        raise ValueError("raw OCP PASS contains a failed, unknown, or not-run mandatory gate")

    separation = raw.get("strategy_identities")
    boundaries = {name: raw.get(name) for name in ("C|C", "X|C", "X|X")}
    if raw.get("verdict") == PASS or isinstance(separation, Mapping):
        if not isinstance(separation, Mapping):
            raise ValueError("strategy identity block is missing")
        object_separation = separation.get("object_instance_separation")
        if not isinstance(object_separation, Mapping):
            raise ValueError("strategy object separation block is missing")
        if object_separation.get("C_observer_1_is_C_execution") is not False:
            raise ValueError("C observer/execution objects were reused")
        if object_separation.get("X_observer_1_is_X_execution") is not False:
            raise ValueError("X observer/execution objects were reused")
    if raw.get("verdict") == PASS or any(isinstance(value, Mapping) for value in boundaries.values()):
        c_control = boundaries["C|C"]
        x_control = boundaries["X|C"]
        x_candidate = boundaries["X|X"]
        if not isinstance(c_control, Mapping) or c_control.get("identities_equal") is not True:
            raise ValueError("C|C identity boundary is incomplete")
        if not isinstance(x_control, Mapping):
            raise ValueError("X|C diagnostic boundary is incomplete")
        if x_control.get("identities_intentionally_different") is not True:
            raise ValueError("X|C diagnostic boundary is incomplete")
        if x_control.get("diagnostic_only") is not True:
            raise ValueError("X|C must remain diagnostic-only")
        if not isinstance(x_candidate, Mapping) or x_candidate.get("identities_equal") is not True:
            raise ValueError("X|X identity boundary is incomplete")


def _compact_boundary(raw: Mapping[str, object], name: str) -> dict[str, object]:
    value = raw.get(name)
    if not isinstance(value, Mapping):
        if raw.get("verdict") == BLOCKED:
            return {"status": "NOT_RUN_BLOCKED", "reason": f"boundary not reached: {name}"}
        raise ValueError(f"raw OCP boundary is missing: {name}")
    return dict(value)


def compact(
    raw_path: Path = DEFAULT_RAW,
    output: Path = DEFAULT_OUTPUT,
    *,
    remote_ci_status: str = "NOT_RUN_BLOCKED",
    remote_ci_run: str | None = None,
) -> dict[str, object]:
    raw_path = raw_path if raw_path.is_absolute() else REPO / raw_path
    output = output if output.is_absolute() else REPO / output
    raw = _load(raw_path)
    validate_raw(raw)
    c_control = raw.get("C_control")
    x_candidate = raw.get("X_candidate")
    if raw.get("verdict") == PASS and (not isinstance(c_control, Mapping) or not isinstance(x_candidate, Mapping)):
        raise ValueError("raw OCP strategy basis blocks are missing")
    c_control = c_control if isinstance(c_control, Mapping) else {}
    x_candidate = x_candidate if isinstance(x_candidate, Mapping) else {}
    input_artifacts = raw.get("input_artifacts")
    raw_outputs = input_artifacts.get("raw_outputs", {}) if isinstance(input_artifacts, Mapping) else {}
    if not isinstance(raw_outputs, Mapping):
        raw_outputs = {}
    blocked_value = raw.get("blocked_reasons", ())
    if not isinstance(blocked_value, (list, tuple)):
        raise ValueError("raw OCP blocked-reason block is invalid")
    blocked_reasons = [str(reason) for reason in blocked_value]
    if remote_ci_status != "PASS":
        blocked_reasons.append(f"remote_ci:{remote_ci_status}")
    verdict = PASS if not blocked_reasons else BLOCKED
    receipt: dict[str, object] = {
        "schema_version": "1.0.0",
        "mission": MISSION,
        "verdict": verdict,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_authorized": False,
        "upstream_r5": raw.get("upstream_r5", {}),
        "accepted_upstream_errata": raw.get("accepted_upstream_errata", {}),
        "commit_topology": {"run_commit": raw.get("run_commit"), "manifest_identity": raw.get("manifest_identity")},
        "implementation_identity": raw.get("implementation_identity", {}),
        "manifest_identity": raw.get("manifest_identity", {}),
        "input_artifacts": raw.get("input_artifacts", {}),
        "strategy_identities": raw.get("strategy_identities", {}),
        "reference_contract": raw.get("reference_contract", {}),
        "capacity_contract": raw.get("capacity_contract", {}),
        "scanner_basis": raw.get("scanner_basis", {}),
        "snapshot_coverage": raw.get("snapshot_coverage", {}),
        "C_control": {
            "observer_identity": c_control.get("observer_identity"),
            "execution_identity": c_control.get("execution_identity"),
            "semantic_universe_hash": c_control.get("semantic_universe_hash"),
            "ordered_observation_merkle_root": c_control.get("ordered_observation_merkle_root"),
            "repeat_semantic_universe_hash": c_control.get("repeat_semantic_universe_hash"),
            "repeat_observation_merkle_root": c_control.get("repeat_observation_merkle_root"),
            "snapshot_count": c_control.get("snapshot_count"),
            "warmup_snapshot_count": c_control.get("warmup_snapshot_count"),
            "fallback_snapshot_count": c_control.get("fallback_snapshot_count"),
            "full_capacity_snapshot_count": c_control.get("full_capacity_snapshot_count"),
        },
        "X_candidate": {
            "observer_identity": x_candidate.get("observer_identity"),
            "execution_identity": x_candidate.get("execution_identity"),
            "semantic_universe_hash": x_candidate.get("semantic_universe_hash"),
            "ordered_observation_merkle_root": x_candidate.get("ordered_observation_merkle_root"),
            "repeat_semantic_universe_hash": x_candidate.get("repeat_semantic_universe_hash"),
            "repeat_observation_merkle_root": x_candidate.get("repeat_observation_merkle_root"),
            "snapshot_count": x_candidate.get("snapshot_count"),
            "warmup_snapshot_count": x_candidate.get("warmup_snapshot_count"),
            "fallback_snapshot_count": x_candidate.get("fallback_snapshot_count"),
            "full_capacity_snapshot_count": x_candidate.get("full_capacity_snapshot_count"),
        },
        "C|C": _compact_boundary(raw, "C|C"),
        "X|C": _compact_boundary(raw, "X|C"),
        "X|X": _compact_boundary(raw, "X|X"),
        "direct_builder_parity": raw.get("direct_builder_parity", {}),
        "determinism": raw.get("determinism", {}),
        "future_bar_tests": raw.get("future_bar_tests", {}),
        "observation_change_summary": raw.get("observation_change_summary", {}),
        "raw_artifact_hashes": dict(raw_outputs),
        "mandatory_gate_results": raw.get("mandatory_gate_results", {}),
        "forbidden_economic_fields": sorted(FORBIDDEN_ECONOMIC_FIELDS),
        "blocked_reasons": blocked_reasons,
        "remote_ci": {"status": remote_ci_status, "run_id": remote_ci_run},
        "next_required_mission": "M3_ECONOMIC_MISSION_PREDECLARATION"
        if verdict == PASS
        else "M3_CANDIDATE_OCP_GATE_REMOTE_CLOSURE",
    }
    receipt["self_digest_sha256"] = canonical_digest(receipt)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes((json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--remote-ci-status", default="NOT_RUN_BLOCKED")
    parser.add_argument("--remote-ci-run")
    args = parser.parse_args()
    receipt = compact(args.raw, args.output, remote_ci_status=args.remote_ci_status, remote_ci_run=args.remote_ci_run)
    print(
        json.dumps(
            {
                "verdict": receipt["verdict"],
                "output": str(args.output),
                "self_digest_sha256": receipt["self_digest_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["verdict"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BLOCKED", "FORBIDDEN_ECONOMIC_FIELDS", "PASS", "compact", "validate_raw"]
