"""Build a no-cherry-pick verdict matrix from the frozen V3 diagnostic grid."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research_runs/crossasset_max_testing_2026_09_09_v3"
PROTOCOL = REPO / "data/research_manifests/crossasset_max_testing_protocol_2026_09_09_v3.json"
RESULTS = OUT / "overlay_diagnostic_results.json"
MATRIX = OUT / "verdict_matrix.json"
REPORT = OUT / "verdict_matrix_report.md"
MANIFEST = OUT / "verdict_matrix_manifest.json"
RECEIPT = OUT / "verdict_matrix_receipt.json"
CARDS = ("X01", "X02", "X03", "X04", "X05", "X06")
MEASURED = ("X01", "X05", "X06")
MODES = ("fixed_95_5", "capacity_aware")
WINDOWS = ("full", "recent", "true_holdout")
LAGS = (1, 3, 7)
COSTS = (1, 2, 3)
ECONOMIC_DECISIONS = {
    "X01": "NOT_ELIGIBLE_MISSING_PIT_AVAILABILITY",
    "X02": "NOT_ELIGIBLE_MISSING_DATED_CONTRACT_AND_EXECUTION_FIELDS",
    "X03": "NOT_ELIGIBLE_MISSING_PIT_VALUATION_AND_MEMBERSHIP_HISTORY",
    "X04": "NOT_ELIGIBLE_MISSING_BORROW_HISTORY_AND_LONG_SHORT_CAPABILITY",
    "X05": "NOT_ELIGIBLE_MISSING_PIT_AVAILABILITY_AND_TWO_LEG_EXECUTION_FIELDS",
    "X06": "NOT_ELIGIBLE_MISSING_PIT_AVAILABILITY_AND_TWO_LEG_EXECUTION_FIELDS",
}


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_protocol() -> dict[str, Any]:
    value = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    expected = value.get("protocol_sha256")
    actual = digest_bytes(canonical({key: item for key, item in value.items() if key != "protocol_sha256"}))
    if expected != actual or value.get("frozen_before_outcomes") is not True:
        raise ValueError("protocol_not_frozen_or_hash_mismatch")
    return value


def load_results() -> dict[str, Any]:
    value = json.loads(RESULTS.read_text(encoding="utf-8"))
    actual = digest_bytes(canonical({key: item for key, item in value.items() if key != "results_sha256"}))
    if value.get("results_sha256") != actual:
        raise ValueError("results_canonical_hash_mismatch")
    if value.get("counts") != {"cards": 6, "measured_cards": 3, "arms": 162, "economic_run": 0, "admitted_rows": 0, "economic_eligible": 0, "promotion": 0}:
        raise ValueError("results_count_grid_mismatch")
    return value


def arm_key(arm: dict[str, Any]) -> tuple[str, str, int, int]:
    return (arm["mode"], arm["window"], arm["lag_days"], arm["cost_multiplier"])


def compact_arm(arm: dict[str, Any]) -> dict[str, Any]:
    """Retain every measured field while making the matrix's branch explicit."""
    return {
        "lag_days": arm["lag_days"],
        "cost_multiplier": arm["cost_multiplier"],
        "active_days": arm["active_days"],
        "active_day_fraction": arm["active_day_fraction"],
        "turnover": arm["turnover"],
        "standalone_metrics": arm["standalone_metrics"],
        "standalone_bootstrap": arm["standalone_bootstrap"],
        "candidate_metrics": arm["candidate_metrics"],
        "portfolio_metrics": arm["portfolio_metrics"],
        "bootstrap": arm["bootstrap"],
        "correlation": arm["correlation"],
        "permutation_placebo": arm["permutation_placebo"],
        "regimes": arm["regimes"],
        "caps": arm["caps"],
        "status": arm["status"],
        "causal_admissible": arm["causal_admissible"],
        "economic_eligible": arm["economic_eligible"],
        "admitted_rows": arm["admitted_rows"],
    }


def card_context(card: str, source: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    formula = protocol["formulae"].get(card)
    if card == "X01":
        direction = "negative slope selects TLT; positive selects SHY; zero selects IEF"
        caveat = "Yahoo adjclose is used only as retrospective outcome; raw-close versus adjusted-close tradable basis and PIT distributions remain unresolved."
        blocker = "no available_at on signal/outcome rows; admitted_rows=0 and economic_eligible=false"
    elif card == "X05":
        direction = "negative cross-sectional z-score selects the lower exact-time OI asset"
        caveat = "OI is exact venue/instrument data but only sparse dates have evidence; no available_at, continuous history, settlement, margin or liquidation provenance."
        blocker = "no available_at on OI or PriceUSD rows; admitted_rows=0 and economic_eligible=false"
    elif card == "X06":
        direction = "negative cross-sectional z-score selects the lower mean eight-hour funding asset"
        caveat = "Funding direction is frozen and joins exact asset/date, but the sample is sparse and lacks available_at, revision, margin, liquidation and borrow provenance."
        blocker = "no available_at on funding or PriceUSD rows; admitted_rows=0 and economic_eligible=false"
    else:
        direction = None
        caveat = None
        blocker = source.get("blocker") or source.get("status")
    return {"formula": formula, "direction": direction, "caveat": caveat, "binding_blocker": blocker}


def branch_rows(card: str, source: dict[str, Any], protocol: dict[str, Any]) -> list[dict[str, Any]]:
    if card not in MEASURED:
        return [{"card": card, "branch": "blocked", "mode": None, "window": None, "status": source["status"], "arms": [], "binding_blocker": source.get("blocker", source["status"]), "admitted_rows": 0, "economic_eligible": False}]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for arm in source["arms"]:
        grouped.setdefault((arm["mode"], arm["window"]), []).append(arm)
    rows = []
    for mode in MODES:
        for window in WINDOWS:
            arms = sorted(grouped[(mode, window)], key=lambda item: (item["lag_days"], item["cost_multiplier"]))
            if {(item["lag_days"], item["cost_multiplier"]) for item in arms} != {(lag, cost) for lag in LAGS for cost in COSTS}:
                raise ValueError(f"incomplete_branch:{card}:{mode}:{window}")
            rows.append({
                "card": card,
                "branch": f"{mode}:{window}",
                "mode": mode,
                "window": window,
                "status": source["status"],
                "arms": [compact_arm(arm) for arm in arms],
                "cost_stability": [{"cost_multiplier": cost, "arms": [compact_arm(arm) for arm in arms if arm["cost_multiplier"] == cost]} for cost in COSTS],
                "all_arm_count": len(arms),
                "no_best_arm_selection": True,
                "admitted_rows": 0,
                "economic_eligible": False,
                "cap_violations_total": {key: sum(arm["caps"][key] for arm in arms) for key in ("position_violations", "equity_violations", "crypto_violations", "gross_violations", "cash_violations")},
                "cap_violations_max_per_arm": {key: max(arm["caps"][key] for arm in arms) for key in ("position_violations", "equity_violations", "crypto_violations", "gross_violations", "cash_violations")},
            })
    return rows


def build_payload(protocol: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    cards = {}
    branches = []
    for card in CARDS:
        source = results["cards"][card]
        context = card_context(card, source, protocol)
        card_branches = branch_rows(card, source, protocol)
        branches.extend(card_branches)
        active = sum(arm["active_days"] for arm in source.get("arms", []))
        cards[card] = {
            "card": card,
            "formula_and_direction": context,
            "signal_rows": source.get("signal_rows", 0),
            "raw_evidence_rows": source.get("raw_evidence_rows", 0),
            "available_at_rows": source.get("available_at_rows", 0),
            "branch_count": len(card_branches),
            "arms_considered": len(source.get("arms", [])),
            "active_days_across_all_arms": active,
            "branches": card_branches,
            "research_verdict": "interesting but unverified" if card in MEASURED and active > 0 else "blocked before measurable diagnostic",
            "economic_decision": ECONOMIC_DECISIONS[card],
            "portfolio_decision": "NO_PORTFOLIO_CONTRIBUTION_ELIGIBLE",
            "admitted_rows": 0,
            "economic_eligible": False,
            "no_best_arm_cherry_pick": True,
        }
    return {
        "schema": "crossasset-max-testing-verdict-matrix-v4",
        "protocol_sha256": protocol["protocol_sha256"],
        "results_sha256": results["results_sha256"],
        "cards": cards,
        "branches": branches,
        "counts": {"cards": 6, "measured_cards": 3, "blocked_cards": 3, "branches": len(branches), "arms": sum(len(item["arms"]) for item in branches), "admitted_rows": 0, "economic_eligible": 0, "integration_candidate": 0},
        "selection_policy": "All 162 frozen arms are retained; no best-arm selection or outcome-based cherry-pick.",
        "overall_decision": "CROSSASSET_MAX_TESTING_COMPLETE_V4",
        "economic_run": False,
        "promotion": False,
        "matrix_sha256": None,
    }


def build() -> dict[str, Any]:
    frozen = load_protocol()
    results = load_results()
    payload = build_payload(frozen, results)
    payload["matrix_sha256"] = digest_bytes(canonical({key: value for key, value in payload.items() if key != "matrix_sha256"}))
    matrix_bytes = canonical(payload)
    lines = [
        "# Crossasset maximum-testing V4 verdict matrix — 2026-09-09",
        "",
        "All frozen lag/cost/window/mode arms are retained. The matrix does not select a best arm.",
        "",
        f"Protocol SHA: `{frozen['protocol_sha256']}`  ",
        f"V3 results SHA: `{results['results_sha256']}`  ",
        f"Matrix SHA: `{payload['matrix_sha256']}`  ",
        "",
        "| Card | Signal rows | Branches | Arms | Verdict | Economic decision |",
        "|---|---:|---:|---:|---|---|",
    ]
    for card in CARDS:
        item = payload["cards"][card]
        lines.append(f"| {card} | {item['signal_rows']} | {item['branch_count']} | {item['arms_considered']} | {item['research_verdict']} | {item['economic_decision']} |")
    lines.extend(["", "All measured cards have admitted_rows=0, economic_eligible=false and integration_candidate=0 because signal/outcome evidence lacks PIT available_at. H14/V3 remains the incumbent diagnostic; no candidate was promoted or tested as a production strategy.", ""])
    for card in MEASURED:
        context = payload["cards"][card]["formula_and_direction"]
        lines.extend([f"## {card}", "", f"Formula: `{context['formula']}`; direction: {context['direction']}.", context["caveat"], ""])
        lines.extend(["| Branch | Lag | Cost | Active | Turnover | Standalone CAGR | Contribution CAGR | Portfolio CAGR | MaxDD | CVaR05 | Standalone CI | Contribution CI | Corr | Placebo p | Regime down/up | Caps (position/equity/crypto/gross/cash) |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"])
        for branch in payload["cards"][card]["branches"]:
            lines.append(f"- `{branch['branch']}`: 9 arms; cap violations total {branch['cap_violations_total']}; max per arm {branch['cap_violations_max_per_arm']}.")
            for arm in branch["arms"]:
                def shown(value: Any) -> str:
                    return "NA" if value is None else f"{value:.6g}" if isinstance(value, float) else str(value)

                lines.append(
                    f"| `{branch['branch']}` | {arm['lag_days']} | {arm['cost_multiplier']} | {arm['active_days']} | {shown(arm['turnover'])} | {shown(arm['standalone_metrics']['cagr'])} | {shown(arm['candidate_metrics']['cagr'])} | {shown(arm['portfolio_metrics']['cagr'])} | {shown(arm['portfolio_metrics']['max_drawdown'])} | {shown(arm['portfolio_metrics']['cvar_p05'])} | [{shown(arm['standalone_bootstrap']['mean_ci_low'])}, {shown(arm['standalone_bootstrap']['mean_ci_high'])}] | [{shown(arm['bootstrap']['mean_ci_low'])}, {shown(arm['bootstrap']['mean_ci_high'])}] | {shown(arm['correlation'])} | {shown(arm['permutation_placebo']['absolute_corr_pvalue'])} | {shown(arm['regimes']['v3_down_mean_contribution'])}/{shown(arm['regimes']['v3_up_or_flat_mean_contribution'])} | {arm['caps']['position_violations']}/{arm['caps']['equity_violations']}/{arm['caps']['crypto_violations']}/{arm['caps']['gross_violations']}/{arm['caps']['cash_violations']} |"
                )
        lines.append("")
    report_bytes = "\n".join(lines).encode()
    manifest = {
        "schema": "crossasset-max-testing-verdict-manifest-v4",
        "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": digest(PROTOCOL)},
        "results": {"path": str(RESULTS.relative_to(REPO)), "sha256": digest(RESULTS)},
        "artifacts": {
            str(MATRIX.relative_to(REPO)): {"sha256": digest_bytes(matrix_bytes), "bytes": len(matrix_bytes)},
            str(REPORT.relative_to(REPO)): {"sha256": digest_bytes(report_bytes), "bytes": len(report_bytes)},
        },
        "counts": payload["counts"],
        "economic_run": False,
        "promotion": False,
        "manifest_sha256": "PENDING_FREEZE",
    }
    manifest["manifest_sha256"] = digest_bytes(canonical({key: value for key, value in manifest.items() if key != "manifest_sha256"}))
    manifest_bytes = canonical(manifest)
    receipt = {
        "schema": "crossasset-max-testing-verdict-receipt-v4",
        "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": digest(PROTOCOL)},
        "results": {"path": str(RESULTS.relative_to(REPO)), "sha256": digest(RESULTS)},
        "matrix": {"path": str(MATRIX.relative_to(REPO)), "sha256": digest_bytes(matrix_bytes)},
        "report": {"path": str(REPORT.relative_to(REPO)), "sha256": digest_bytes(report_bytes)},
        "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": digest_bytes(manifest_bytes)},
        "counts": payload["counts"],
        "network_used": False,
        "economic_run": False,
        "promotion": False,
        "receipt_sha256": "PENDING_FREEZE",
    }
    receipt["receipt_sha256"] = digest_bytes(canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"}))
    atomic_write(MATRIX, matrix_bytes)
    atomic_write(REPORT, report_bytes)
    atomic_write(MANIFEST, manifest_bytes)
    atomic_write(RECEIPT, canonical(receipt))
    return {"status": payload["overall_decision"], "matrix_sha256": payload["matrix_sha256"], "manifest_sha256": manifest["manifest_sha256"], "receipt_sha256": receipt["receipt_sha256"], "branches": 21, "arms": 162}


def verify(base: Path = OUT) -> dict[str, Any]:
    matrix = base / MATRIX.name
    report = base / REPORT.name
    manifest_path = base / MANIFEST.name
    receipt_path = base / RECEIPT.name
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    if payload.get("matrix_sha256") != digest_bytes(canonical({key: value for key, value in payload.items() if key != "matrix_sha256"})):
        raise ValueError("matrix_hash_mismatch")
    if payload.get("counts") != {"cards": 6, "measured_cards": 3, "blocked_cards": 3, "branches": 21, "arms": 162, "admitted_rows": 0, "economic_eligible": 0, "integration_candidate": 0}:
        raise ValueError("matrix_count_mismatch")
    if payload.get("economic_run") is not False or payload.get("promotion") is not False:
        raise ValueError("unsafe_matrix")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_sha256") != digest_bytes(canonical({key: value for key, value in manifest.items() if key != "manifest_sha256"})):
        raise ValueError("manifest_hash_mismatch")
    if manifest["artifacts"][str(MATRIX.relative_to(REPO))]["sha256"] != digest(matrix) or manifest["artifacts"][str(REPORT.relative_to(REPO))]["sha256"] != digest(report):
        raise ValueError("manifest_artifact_mismatch")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("receipt_sha256") != digest_bytes(canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"})):
        raise ValueError("receipt_hash_mismatch")
    for key, path in (("protocol", PROTOCOL), ("results", RESULTS), ("matrix", matrix), ("report", report), ("manifest", manifest_path)):
        if receipt.get(key, {}).get("sha256") != digest(path):
            raise ValueError(f"receipt_binding_mismatch:{key}")
    return {"status": "CROSSASSET_MAX_TESTING_V4_VERIFIED", "branches": 21, "arms": 162, "read_only": True, "network_free": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else build(), sort_keys=True))


if __name__ == "__main__":
    main()
