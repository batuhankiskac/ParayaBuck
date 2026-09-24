"""One-shot, no-return physical capacity comparison under the frozen arms."""

from __future__ import annotations

import gc
import gzip
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .build_inputs import build_replay
from .independent_verify import compare_arms, verify_arm
from .no_economic_leakage import assert_no_economics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PARENT = "690ce42e1852ead0ccaaddbf3822b10a65a6a77c"
MAIN = "be440821e34d859f2e693c45532566eb0dafb1f8"
BRANCH = "research/residual-reversal-physical-capacity-replay-v1"
FROZEN = ("FROZEN_CAPACITY_MATERIALITY_SPEC.json", "FROZEN_CAPACITY_MATERIALITY_SPEC.md", "SLOT_ARBITRATION_FREEZE.md")
CAPACITY_OUTPUTS = (
    "CAP6_STRUCTURAL_RESULTS.json",
    "CAP8_STRUCTURAL_RESULTS.json",
    "CAP6_DENIAL_LEDGER.json",
    "CAP8_DENIAL_LEDGER.json",
    "SOURCE_ATTRIBUTION_SUMMARY.json",
    "OVERLAP_CAPACITY_SUMMARY.json",
    "CAPACITY_COMPARISON.json",
    "CAPACITY_COMPARISON.md",
    "DETERMINISM_RECEIPT.json",
    "CAUSALITY_RECEIPT.json",
    "TERMINAL_TAIL_RECEIPT.json",
    "NO_ECONOMIC_LEAKAGE_RECEIPT.json",
    "INDEPENDENT_CAPACITY_VERIFICATION.json",
    "CAPACITY_REPLAY_DECISION.json",
    "FUTURE_FROZEN_ECONOMIC_EXPERIMENT.md",
    "FINAL_PHYSICAL_CAPACITY_REPLAY_REPORT.md",
    "CAP6_STRUCTURAL_TRACE.json.gz",
    "CAP8_STRUCTURAL_TRACE.json.gz",
)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _load(name: str) -> dict[str, Any]:
    value = json.loads((HERE / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected object in {name}")
    assert_no_economics(value)
    return value


def preflight() -> dict[str, Any]:
    """Validate authorities and frozen protocol before any treatment arm."""
    if _git("branch", "--show-current") != BRANCH or _git("rev-parse", "main") != MAIN:
        raise RuntimeError("Wrong research branch or production main authority")
    if subprocess.run(["git", "merge-base", "--is-ancestor", PARENT, "HEAD"], cwd=ROOT, check=False).returncode:
        raise RuntimeError("Research branch does not descend from binding re-entry review")
    for name in FROZEN:
        relative = (HERE / name).relative_to(ROOT)
        committed = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
        if committed != (HERE / name).read_bytes():
            raise RuntimeError(f"Pre-outcome freeze is not committed byte-for-byte: {name}")
    spec = _load("FROZEN_CAPACITY_MATERIALITY_SPEC.json")
    if (
        spec["parent_review_commit"] != PARENT
        or spec["arms"]["A"]["global_cap"] != 6
        or spec["arms"]["B"]["global_cap"] != 8
    ):
        raise RuntimeError("Frozen arm specification changed")
    clock = _load("REVERSAL_PHYSICAL_CLOCK_EQUIVALENCE.json")
    parity = _load("INCUMBENT_PHYSICAL_PARITY_RECEIPT.json")
    if clock.get("classification") != "REVERSAL_PHYSICAL_CLOCK_EQUIVALENT" or clock.get("mismatch_counts") != {}:
        raise RuntimeError("Candidate physical-clock equivalence is not proved")
    if parity.get("status") != "PASS" or parity.get("differences") != []:
        raise RuntimeError("Incumbent physical parity is not proved")
    if any((HERE / name).exists() for name in CAPACITY_OUTPUTS):
        raise RuntimeError("Capacity outputs already exist; one-shot replay will not overwrite them")
    return {"clock": clock["classification"], "incumbent_parity": parity["status"], "frozen_parent": PARENT}


def _write_json(name: str, value: Any) -> None:
    assert_no_economics(value)
    (HERE / name).write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _denials(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": event["timestamp"],
            "market": event["market"],
            "source": event["source"],
            "symbol": event["symbol"],
            "source_rank": event["source_rank"],
            "reason": event["denial_reason"],
            "requested_target_contribution": event["requested_target_contribution"],
            "admitted_target_contribution": event["admitted_target_contribution"],
        }
        for event in events
        if event["event"] == "source_admission" and event["denial_reason"] is not None
    ]


def _frozen_event_window(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Exclude only inert post-terminal V2 feed events beyond the committed cutoff."""
    cutoff = _load("FROZEN_CAPACITY_MATERIALITY_SPEC.json")["period"]["last_precommitted_terminal_event_utc"]
    kept: list[dict[str, Any]] = []
    tail: list[dict[str, Any]] = []
    for event in events:
        if event.get("timestamp", cutoff) > cutoff:
            tail.append(event)
            if event["event"] == "corporate_action":
                if float(event["position_qty_before"]) != 0.0 or float(event["position_qty_after"]) != 0.0:
                    raise RuntimeError("Post-terminal corporate action touched inventory")
            elif event["event"] == "account_mark":
                if event["position_count"] != 0 or event["slot_claim_count"] != 0:
                    raise RuntimeError("Post-terminal account mark has a live claim")
            else:
                raise RuntimeError("Post-terminal event changes the frozen replay window")
        else:
            kept.append(event)
    if not tail or not any(event["event"] == "final_state" for event in kept):
        raise RuntimeError("Expected inert V2 post-terminal tail or final state is absent")
    return kept, {
        "status": "INERT_POST_TERMINAL_TAIL",
        "cutoff": cutoff,
        "excluded_event_count": len(tail),
        "excluded_event_kinds": {
            kind: sum(event["event"] == kind for event in tail) for kind in ("corporate_action", "account_mark")
        },
        "first_excluded_timestamp": tail[0]["timestamp"],
        "last_excluded_timestamp": tail[-1]["timestamp"],
    }


def _run_arm(cap: int) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    replay, identities = build_replay(cap, "treatment")
    result = replay.run()
    full_events = list(result.events)
    full_receipt = verify_arm(full_events, cap=cap)
    if result.event_sha256 != full_receipt["event_sha256"]:
        raise RuntimeError(f"Cap {cap} scheduler and independent event hashes differ")
    if full_receipt["status"] != "PASS":
        _write_trace(f"CAP{cap}_FAILURE_TRACE.json.gz", full_events)
        raise RuntimeError(f"Cap {cap} full physical verifier failed: {full_receipt['errors'][:5]}")
    events, tail = _frozen_event_window(full_events)
    receipt = verify_arm(events, cap=cap)
    if receipt["status"] != "PASS":
        _write_trace(f"CAP{cap}_FAILURE_TRACE.json.gz", events)
        indices = [
            int(match.group(1)) for error in receipt["errors"][:10] if (match := re.match(r"event (\d+)", error))
        ]
        samples = [{"index": index, "events": events[max(0, index - 3) : index + 4]} for index in indices[:5]]
        _write_json(
            f"CAP{cap}_FAILURE_DIAGNOSTIC.json",
            {"status": "FAIL", "errors": receipt["errors"][:20], "structural_event_samples": samples},
        )
        raise RuntimeError(f"Cap {cap} independent verifier failed: {receipt['errors'][:5]}")
    if not all(
        result.invariants.get(field) is True
        for field in (
            "cash_conserved",
            "nav_matches_ledger",
            "position_quantities_reconciled",
            "slot_claims_within_limit",
            "all_reservations_released",
            "no_future_data_access",
            "target_name_weights_within_limits",
            "sleeve_targets_within_limits",
        )
    ):
        raise RuntimeError(f"Cap {cap} scheduler integrity failed")
    del result, replay
    gc.collect()
    return events, receipt, identities, tail


def _write_trace(name: str, events: list[dict[str, Any]]) -> None:
    assert_no_economics(events)
    with gzip.open(HERE / name, "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(events, handle, separators=(",", ":"), sort_keys=True, allow_nan=False)
        handle.write("\n")


def run() -> dict[str, Any]:
    prerequisites = preflight()
    period = _load("FROZEN_CAPACITY_MATERIALITY_SPEC.json")["period"]
    first: dict[int, tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    repeat: dict[int, dict[str, Any]] = {}
    for cap in (6, 8):
        print(f"STRUCTURAL_ARM_START cap={cap} execution=1", flush=True)
        events, receipt, identities, tail = _run_arm(cap)
        print(f"STRUCTURAL_ARM_VERIFIED cap={cap} execution=1 status={receipt['status']}", flush=True)
        if (
            receipt["first_price_event"] != period["first_price_event_utc"]
            or receipt["last_price_event"] != period["last_precommitted_terminal_event_utc"]
        ):
            raise RuntimeError(f"Cap {cap} physical event endpoints differ from the pre-outcome freeze")
        first[cap] = (events, receipt, identities, tail)
        print(f"STRUCTURAL_ARM_START cap={cap} execution=2", flush=True)
        _second_events, second_receipt, second_identities, second_tail = _run_arm(cap)
        print(f"STRUCTURAL_ARM_VERIFIED cap={cap} execution=2 status={second_receipt['status']}", flush=True)
        if identities != second_identities or tail != second_tail:
            raise RuntimeError(f"Cap {cap} input identity changed between executions")
        repeat[cap] = second_receipt
        del _second_events
    six_events, six, six_identity, six_tail = first[6]
    eight_events, eight, eight_identity, eight_tail = first[8]
    if six_identity != eight_identity:
        raise RuntimeError("Canonical source/universe identities changed between cap arms")
    if six_tail != eight_tail:
        raise RuntimeError("Post-terminal event tail differs between cap arms")
    comparison = compare_arms(six, eight, repeat6=repeat[6], repeat8=repeat[8])
    if comparison["status"] == "BLOCKED":
        raise RuntimeError("Independent comparison blocked; no capacity interpretation")
    _write_trace("CAP6_STRUCTURAL_TRACE.json.gz", six_events)
    _write_trace("CAP8_STRUCTURAL_TRACE.json.gz", eight_events)
    _write_json("CAP6_STRUCTURAL_RESULTS.json", six)
    _write_json("CAP8_STRUCTURAL_RESULTS.json", eight)
    _write_json("CAP6_DENIAL_LEDGER.json", _denials(six_events))
    _write_json("CAP8_DENIAL_LEDGER.json", _denials(eight_events))
    _write_json("SOURCE_ATTRIBUTION_SUMMARY.json", {"cap6": six["source_capacity"], "cap8": eight["source_capacity"]})
    _write_json(
        "OVERLAP_CAPACITY_SUMMARY.json",
        {
            "cap6": {
                key: six[key]
                for key in (
                    "equity_overlap_decision_count",
                    "equity_overlap_symbol_event_count",
                    "equity_overlap_slots_saved",
                )
            },
            "cap8": {
                key: eight[key]
                for key in (
                    "equity_overlap_decision_count",
                    "equity_overlap_symbol_event_count",
                    "equity_overlap_slots_saved",
                )
            },
        },
    )
    _write_json("CAPACITY_COMPARISON.json", comparison)
    deterministic = {
        "status": "PASS",
        "cap6_first": six["event_sha256"],
        "cap6_repeat": repeat[6]["event_sha256"],
        "cap8_first": eight["event_sha256"],
        "cap8_repeat": repeat[8]["event_sha256"],
    }
    _write_json("DETERMINISM_RECEIPT.json", deterministic)
    _write_json(
        "CAUSALITY_RECEIPT.json",
        {
            "status": "PASS",
            "noncausal_fill_count": 0,
            "cap6_fill_count": six["fill_count"],
            "cap8_fill_count": eight["fill_count"],
        },
    )
    _write_json("TERMINAL_TAIL_RECEIPT.json", six_tail)
    _write_json(
        "INDEPENDENT_CAPACITY_VERIFICATION.json",
        {
            "status": "PASS",
            "cap6_event_sha256": six["event_sha256"],
            "cap8_event_sha256": eight["event_sha256"],
            "gates": comparison["gates"],
        },
    )
    decision = (
        "RESIDUAL_REVERSAL_CAP8_MATERIALITY_PROVEN"
        if comparison["status"] == "MATERIALITY_PROVEN"
        else "RESIDUAL_REVERSAL_CAP8_MATERIALITY_NOT_PROVEN"
    )
    next_action = (
        "RUN_ONE_FROZEN_RESIDUAL_REVERSAL_CAP8_WHOLE_ACCOUNT_CONTRIBUTION_TEST"
        if comparison["status"] == "MATERIALITY_PROVEN"
        else "KEEP_RESIDUAL_REVERSAL_CLOSED"
    )
    _write_json(
        "CAPACITY_REPLAY_DECISION.json",
        {
            "decision": decision,
            "next_action": next_action,
            "prerequisites": prerequisites,
            "new_economics_calculated": "NONE",
            "production_changes": "NONE",
            "branch_sha_at_replay": _git("rev-parse", "HEAD"),
        },
    )
    comparison_md = (
        "# Physical shared-account structural capacity\n\n"
        + f"Decision: `{decision}`.\n\n"
        + "| Gate | Result |\n| --- | --- |\n"
        + "".join(f"| {gate} | {'PASS' if passed else 'FAIL'} |\n" for gate, passed in comparison["gates"].items())
    )
    (HERE / "CAPACITY_COMPARISON.md").write_text(comparison_md, encoding="utf-8")
    future = (
        "# Frozen future economic experiment\n\n"
        "Control A: V3 0.55, crypto 0.05, global cap 8, crypto cap 2. "
        "Treatment B: V3 0.4125, reversal 0.1375, crypto 0.05, global cap 8, crypto cap 2. "
        "Everything else remains identical. No experiment has been run.\n\n"
        "The one future confirmatory test retains the original gates: capital-neutral whole-account contribution, "
        "full-period risk-adjusted contribution, validation contribution, drawdown, turnover and cost, "
        "concentration, and deterministic architecture compliance. The old historical Gate-4 "
        "portfolio-contribution failure remains binding background evidence; this freeze does not weaken it.\n"
        if comparison["status"] == "MATERIALITY_PROVEN"
        else "# Future economic experiment\n\nNot authorized. Candidate remains closed.\n"
    )
    (HERE / "FUTURE_FROZEN_ECONOMIC_EXPERIMENT.md").write_text(future, encoding="utf-8")
    report = [
        "# Final structural decision",
        "",
        f"`{decision}`.",
        "",
        "# Authorities",
        "",
        f"Current main: `{MAIN}`. Parent re-entry review: `{PARENT}`. "
        "Original candidate: `a3a54cff64a9312300ef700f2fe9409435a064d3`. "
        "Physical replay: `11890e93ad1a5223cb11483488deb4d5a6aec3d3`.",
        "",
        "# Reversal physical-clock equivalence and incumbent parity",
        "",
        f"Candidate: `{prerequisites['clock']}`. Incumbents: `{prerequisites['incumbent_parity']}`.",
        "",
        "# Frozen arbitration and arm identity",
        "",
        "Crypto decides first. V3 and reversal retain source rank and their 0.4125/0.1375 budgets; "
        "overlap occupies one equity slot. Arm A cap 6 and Arm B cap 8 use the same physical scheduler, "
        "source data, strategy parameters, budgets, execution rules and crypto cap 2. The global cap is the only arm change.",
        "",
        "# Cap6 and cap8 source capacity",
        "",
        "| Measure | Cap 6 | Cap 8 |",
        "| --- | ---: | ---: |",
    ]
    for source in ("v3", "reversal", "crypto"):
        for metric in (
            "requested_unique_openings",
            "admitted_unique_openings",
            "requested_target_exposure_sum",
            "admitted_target_exposure_sum",
            "count_induced_unused_target_exposure_sum",
        ):
            report.append(
                f"| {source} {metric.replace('_', ' ')} | {six['source_capacity'][source][metric]} | {eight['source_capacity'][source][metric]} |"
            )
    report += [
        "",
        "# Whole-account slots and crypto consumption",
        "",
        "| Measure | Cap 6 | Cap 8 |",
        "| --- | ---: | ---: |",
    ]
    for label, key in (
        ("Average active positions", "average_active_positions"),
        ("Average effective claims", "average_effective_claims"),
        ("Peak effective claims", "maximum_claims"),
        ("At-cap timestamps", "at_cap_timestamp_count"),
        ("Average active crypto positions", "crypto_average_active_positions"),
    ):
        report.append(f"| {label} | {six[key]} | {eight[key]} |")
    report += ["", "# Overlap, denials and decision-level admission", ""]
    report.append(
        f"Equity overlap saved {six['equity_overlap_slots_saved']} cap6 symbol-event slots and "
        f"{eight['equity_overlap_slots_saved']} cap8 symbol-event slots. "
        f"Reversal exact GLOBAL_POSITION_COUNT denials: {six['global_count_denials']['reversal']} / "
        f"{eight['global_count_denials']['reversal']}. Broad count-related denials: "
        f"{six['all_count_related_denials']['reversal']} / {eight['all_count_related_denials']['reversal']}. "
        f"Count-induced unused reversal target exposure: {six['reversal_count_unused_target_exposure']} / "
        f"{eight['reversal_count_unused_target_exposure']}. Candidate admission changed at "
        f"{comparison['changed_candidate_decision_count']} of {comparison['eligible_candidate_decision_count']} eligible timestamps."
    )
    report += [
        "",
        comparison_md,
        "# Determinism and independent verification",
        "",
        "Both executions of each arm produced the same structural event and denial hashes and final inventory identity. "
        "The independent verifier passed both arms and recalculated M1-M8 from raw structural events.",
        "",
        "# Economic leakage and production boundary",
        "",
        "New economics calculated: `NONE`. Economic leakage audit: `PASS`. Production changes: `NONE`. "
        "Residual reversal remains research-only. LIVE remains disabled.",
        "",
        "# Future frozen economic test",
        "",
        "Authorized by this structural result."
        if comparison["status"] == "MATERIALITY_PROVEN"
        else "Not authorized; candidate remains closed.",
        "",
        "# Exact next action",
        "",
        f"`{next_action}`.",
        "",
        "# Final branch SHA",
        "",
        "The commit containing this report is the final branch SHA; resolve it with `git rev-parse HEAD`.",
        "",
    ]
    (HERE / "FINAL_PHYSICAL_CAPACITY_REPLAY_REPORT.md").write_text("\n".join(report), encoding="utf-8")
    for name in CAPACITY_OUTPUTS:
        if name.endswith(".json") and name != "NO_ECONOMIC_LEAKAGE_RECEIPT.json":
            assert_no_economics(json.loads((HERE / name).read_text(encoding="utf-8")))
    _write_json(
        "NO_ECONOMIC_LEAKAGE_RECEIPT.json",
        {
            "status": "PASS",
            "audited_json_artifacts": sorted(
                name
                for name in CAPACITY_OUTPUTS
                if name.endswith(".json") and name != "NO_ECONOMIC_LEAKAGE_RECEIPT.json"
            ),
            "audited_trace_hashes": {"cap6": six["event_sha256"], "cap8": eight["event_sha256"]},
        },
    )
    return {"decision": decision, "next_action": next_action}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
