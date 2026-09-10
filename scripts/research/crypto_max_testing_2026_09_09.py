"""Freeze and verify the K01-K12 maximum-testing contract.

This research-only bundle records the exact pre-result protocol for every
crypto card and binds its terminal data-gate evidence to the current closure
sources.  It deliberately produces no returns, portfolio result, or
promotion decision.  A card is eligible for a later economic runner only if
its pinned source reports causal, point-in-time rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research_runs/crypto_max_testing_2026_09_09"
MANIFEST = OUT / "protocol_manifest.json"
ASSESSMENT = OUT / "terminal_gate_assessment.json"
RECEIPT = OUT / "validation_receipt.json"
ROUTE_MANIFEST = OUT / "routes/route_manifest.json"
ROUTE_ASSESSMENT = OUT / "routes/route_assessment.json"
ROUTE_RECEIPT = OUT / "routes/validation_receipt.json"
CLOSURE = REPO / "data/research_runs/all_34_research_closure_2026_09_09/closure_record.json"
QUEUE_REVIEW = REPO / "docs/research/strategy_queue_crypto_review_2026_09_06.md"
QUEUE_PROTOCOL = REPO / "docs/research/crypto_queue_causal_protocol_2026_09_08.md"
SECONDARY_PROTOCOL = REPO / "docs/research/crypto_secondary_causal_protocol_2026_09_08.md"
CUTOFF = "2026-07-31T23:59:59Z"
WINDOWS = {
    "full": "2022-01-01..2026-07-31",
    "recent": "2024-01-01..2026-07-31",
    "holdout": "UNSPECIFIED_UNTIL_FROZEN_SOURCE_PROVIDES_DATES",
}
COMMON_COSTS = ["1x", "2x", "3x"]
COMMON_CONTROLS = ["incumbent_crypto", "isolated_candidate", "passive_cash", "matched_exposure"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object_required:{path}")
    return value


def _protocols() -> list[dict[str, Any]]:
    common = {
        "decision_clock": "completed native observation; one-period lag where specified; next tradable spot open",
        "availability_boundary": "row-level timezone-aware available_at <= decision timestamp; event/publication/vintage retained separately; retrieval time never substitutes",
        "execution_shift": "first eligible next spot open after the causal decision interval",
        "rebalance_hold": "daily completed observations; one-period lag; long/cash unless card-specific source freezes otherwise",
        "missing_data": "drop the affected signal decision and fail closed; no forward fill, current-label backfill, or OHLCV substitution",
        "cost_components": ["fees", "slippage", "funding_if_applicable", "borrow_if_applicable"],
        "cost_stress": COMMON_COSTS,
        "controls": COMMON_CONTROLS,
        "windows": WINDOWS,
        "holdout_policy": "Do not infer dates. K06 requires an untouched post-2024 holdout; other cards remain unspecified until their frozen source supplies dates.",
        "portfolio_scope": "complementary diagnostic only: 95% current V3 + 5% candidate; crypto <=5%, max crypto positions=2, total positions=6, gross<=60%, cash>=40%",
    }
    specs = [
        ("K01", "MVRV; Coin Metrics realized-cap methodology", "Coin Metrics CapMVRVCur; CapMrktCurUSD audit field", "MVRV_t = CapMrkt_t / CapReal_t (consume native CapMVRVCur directly)", "within-asset historical deviation; direction/threshold unspecified", "BTC and ETH configured assets; daily completed", "realized-cap definition, chain cutoff, revision/PIT availability, symbol identity"),
        ("K02", "NVT; Coin Metrics adjusted-transfer methodology", "Coin Metrics adjusted transfer value and market value", "NVTAdj_t = market_value_t / adjusted_transfer_value_t", "long/cash; smoothing, threshold and exact direction unspecified", "BTC and ETH configured assets; daily completed", "adjusted-transfer version, self-churn/early-spend policy, chain coverage, zero denominator, PIT availability"),
        ("K03", "Economic network-use growth; Coin Metrics address/transfer methods", "Coin Metrics FeeTotNtv; active/economic addresses remain descriptive", "log1p(FeeTotNtv_t) - log1p(FeeTotNtv_t-30d); 31 consecutive observations", "within-asset fee-growth direction/threshold unspecified; never call native fees Sybil-adjusted usage", "BTC and ETH configured assets; daily completed", "chain identity, fee metric vintage, spam/Sybil policy, complete 31-day window, PIT availability"),
        ("K04", "Net issuance/burn dilution; Coin Metrics supply methods", "IssTotNtv and SplyCur retained separately; burn and migration fields required", "audit ratio = IssTotNtv_t / SplyCur_(t-1); not net dilution", "long/cash; net direction/threshold unspecified until burn and supply policy exist", "BTC and ETH configured assets; daily completed", "issuance/burn/supply policy, vesting/migration, prior-supply alignment, PIT availability"),
        ("K05", "Preannounced token unlocks", "issuer/governance announcement, vesting schedule, actual on-chain unlock transfer", "event-only: published announcement before decision, then scheduled unlock/confirmed transfer; exact event window and direction unspecified", "long/cash; no universal sign assumption", "frozen configured 22-pair pool; asset-specific primary evidence", "canonical immutable URL, publication/scheduled time, units/percent, beneficiary class, transfer ID, amendments/migrations"),
        ("K06", "Spot crypto ETF net flows; Lim and SEC filing", "issuer-published BTC/ETH ETF creations/redemptions aggregated by underlying", "net creations minus redemptions after issuer publication and one decision interval", "long/cash; ETF set and event window frozen before results", "BTC and ETH; daily; short post-2024 regime", "issuer/SEC identity, correction policy, cash/in-kind treatment, exchange calendar, publication time, untouched post-2024 holdout"),
        ("K07", "Exchange coin inflow/outflow; Glassnode PIT warning", "labeled centralized-exchange wallet inflow minus outflow; native and USD series separate", "one-period-lagged net flow; sign frozen with metric version; no universal sign assumption", "long/cash; sign is frozen with the metric version", "BTC/ETH pilot; daily, or hourly only with provider availability", "PIT endpoint/immutable snapshot, label-set hash, confirmations, venue scope, internal-transfer exclusions, latency"),
        ("K08", "Stablecoin exchange liquidity; Chi et al.", "USDT and USDC net exchange flow, contract and exchange labels frozen", "one-period-lagged net exchange flow; mint/burn/bridge excluded", "long/cash; interval and direction frozen before results", "BTC/ETH spot target; completed interval; no minting-as-buying shortcut", "contract identity, mint/burn/bridge filters, label vintages, PIT time, cross-venue coverage, one-hour latency"),
        ("K09", "Basis/funding/OI as spot signal; BIS and exchange schemas", "venue/instrument-preserved spot, futures, mark, index, funding and OI observations", "spot-only signal from synchronized basis/funding/OI sidecar; no derivative execution or funding P&L", "long/cash spot; exact direction/window unspecified until causal join exists", "Binance USD-M BTCUSDT and ETHUSDT pilot; completed bars/intervals", "venue/contract identity, OI units, funding settlement semantics, stale intervals, publication/available_at, spot join"),
        ("K10", "Spot rebound after liquidation shock; Deribit/Kraken schemas", "venue-tagged liquidation volume with side/quantity, OI delta, mark/index and spot context", "trigger only after available liquidation event; fixed wait/hold interval frozen before results", "long/cash spot; no forced assumption from ordinary volume", "BTC/ETH spot target; venue aggregation frozen", "full vs incremental semantics, duplicate/event ID, OI units, venue aggregation, latency, shock fill cost"),
        ("K11", "Signed order flow; Anastasopoulos et al.", "single-venue aggregate trades with deterministic aggressor sign; no depth inference", "signed_notional = price * quantity * (+1 if is_buyer_maker=false else -1); first strictly later causal spot open", "long/cash spot; aggregation interval and threshold unspecified until PIT rows exist", "Binance USD-M BTCUSDT pilot; immutable full-day archive", "contiguous/complete trade IDs, exact venue/instrument, explicit available_at/publication, duplicate handling, spot-bar join"),
        ("K12", "SOPR loss realization; Glassnode/Coin Metrics methods", "spent-output value / creation value; entity-adjusted variant retains clustering policy", "SOPR_t = spent_output_value_t / creation_value_t; single level or fixed deviation-from-one", "long/cash; one-period lag; direction/threshold fixed before results", "BTC first, then only same-semantics assets; daily completed", "immutable PIT metric, entity-cluster version, UTXO/account treatment, internal transfers, chain coverage, publication lag"),
    ]
    fields = ["native_fields", "formula", "direction", "universe_sampling", "gate_requirements"]
    result = []
    for card, mechanism, native, formula, direction, universe, gate in specs:
        row = dict(common)
        row.update({"candidate_id": card, "mechanism": mechanism, "native_fields": native, "formula": formula, "direction": direction, "universe_sampling": universe, "gate_requirements": gate, "protocol_status": "FROZEN_BEFORE_OUTCOME"})
        if any(not row[name] for name in fields):
            raise ValueError(f"protocol_field_empty:{card}")
        result.append(row)
    return result


def _current_rows() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    closure = read_json(CLOSURE)
    candidates = closure.get("candidates")
    if not isinstance(candidates, dict):
        raise ValueError("closure_candidates_missing")
    rows = []
    for card in (f"K{i:02d}" for i in range(1, 13)):
        current = candidates.get(card)
        if not isinstance(current, dict):
            raise ValueError(f"closure_card_missing:{card}")
        source = current.get("result_source")
        if not isinstance(source, dict) or not source.get("path") or not source.get("sha256"):
            raise ValueError(f"result_source_unbound:{card}")
        source_path = REPO / source["path"]
        if not source_path.is_file() or sha256(source_path) != source["sha256"]:
            raise ValueError(f"result_source_hash_mismatch:{card}")
        for key in ("research_status", "evidence_counts", "feasibility", "terminal_decision"):
            if key not in current or current[key] in (None, "", []):
                raise ValueError(f"current_result_field_missing:{card}:{key}")
        rows.append({
            "candidate_id": card,
            "research_status": current["research_status"],
            "evidence_counts": current["evidence_counts"],
            "evidence_summary": current.get("evidence_summary", ""),
            "feasibility": current["feasibility"],
            "terminal_decision": current["terminal_decision"],
            "result_source": source,
            "economic_run": False,
            "economic_eligible": False,
            "admitted": 0,
            "integration_candidate": False,
            "promotion": False,
        })
    return closure, rows


def build() -> dict[str, Any]:
    closure, rows = _current_rows()
    source_files = [QUEUE_REVIEW, QUEUE_PROTOCOL, SECONDARY_PROTOCOL, CLOSURE]
    manifest = {
        "schema": "crypto-max-testing-protocol-v1",
        "version": "2026-09-09",
        "historical_cutoff": CUTOFF,
        "research_only": True,
        "network_used": False,
        "economic_run": False,
        "economic_eligible_count": 0,
        "admitted_count": 0,
        "integration_candidate_count": 0,
        "promotion": False,
        "protocol_sources": [{"path": str(path.relative_to(REPO)), "sha256": sha256(path)} for path in source_files],
        "route_acquisition": {
            "manifest": {"path": str(ROUTE_MANIFEST.relative_to(REPO)), "sha256": sha256(ROUTE_MANIFEST)},
            "assessment": {"path": str(ROUTE_ASSESSMENT.relative_to(REPO)), "sha256": sha256(ROUTE_ASSESSMENT)},
            "receipt": {"path": str(ROUTE_RECEIPT.relative_to(REPO)), "sha256": sha256(ROUTE_RECEIPT)},
            "admitted_rows": 0,
            "economic_run": False,
        },
        "protocols": _protocols(),
        "current_gate_snapshot": rows,
        "manifest_sha256": None,
    }
    manifest["manifest_sha256"] = hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"})).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_bytes(canonical(manifest))
    assessment = {
        "schema": "crypto-max-testing-terminal-assessment-v1",
        "manifest_sha256": manifest["manifest_sha256"],
        "candidate_count": len(rows),
        "economic_run": False,
        "economic_eligible_count": 0,
        "admitted_count": 0,
        "integration_candidate_count": 0,
        "portfolio_contribution_run": False,
        "next_stage_approval": "NOT_APPROVED",
        "rows": rows,
    }
    ASSESSMENT.write_bytes(canonical(assessment))
    receipt = {
        "schema": "crypto-max-testing-validation-receipt-v1",
        "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": sha256(MANIFEST)},
        "assessment": {"path": str(ASSESSMENT.relative_to(REPO)), "sha256": sha256(ASSESSMENT)},
        "route_manifest": {"path": str(ROUTE_MANIFEST.relative_to(REPO)), "sha256": sha256(ROUTE_MANIFEST)},
        "protocol_count": len(manifest["protocols"]),
        "candidate_count": len(rows),
        "economic_run": False,
        "network_used": False,
        "next_stage_approval": "NOT_APPROVED",
    }
    RECEIPT.write_bytes(canonical(receipt))
    return {"status": "CRYPTO_MAX_TESTING_PROTOCOL_FROZEN", "candidate_count": len(rows), "manifest_sha256": manifest["manifest_sha256"], "economic_run": False}


def verify(path: Path = MANIFEST) -> dict[str, Any]:
    payload = read_json(path)
    declared = payload.get("manifest_sha256")
    actual = hashlib.sha256(canonical({k: v for k, v in payload.items() if k != "manifest_sha256"})).hexdigest()
    if declared != actual:
        raise ValueError("manifest_canonical_hash_mismatch")
    if payload.get("candidate_count") is not None:
        raise ValueError("candidate_count_must_be_derived")
    protocols = payload.get("protocols")
    rows = payload.get("current_gate_snapshot")
    if not isinstance(protocols, list) or len(protocols) != 12 or not isinstance(rows, list) or len(rows) != 12:
        raise ValueError("candidate_count_or_protocols_invalid")
    ids = [row.get("candidate_id") for row in protocols]
    if ids != [f"K{i:02d}" for i in range(1, 13)] or {row.get("candidate_id") for row in rows} != set(ids):
        raise ValueError("candidate_ids_not_exact")
    if any(row.get("protocol_status") != "FROZEN_BEFORE_OUTCOME" for row in protocols):
        raise ValueError("protocol_not_frozen")
    if payload.get("economic_run") is not False or payload.get("promotion") is not False:
        raise ValueError("economic_or_promotion_flag")
    route = payload.get("route_acquisition")
    if not isinstance(route, dict) or route.get("admitted_rows") != 0 or route.get("economic_run") is not False:
        raise ValueError("route_acquisition_flag")
    for key in ("manifest", "assessment", "receipt"):
        declaration = route.get(key)
        if not isinstance(declaration, dict) or sha256(REPO / declaration["path"]) != declaration.get("sha256"):
            raise ValueError(f"route_acquisition_hash_mismatch:{key}")
    for row in rows:
        if any(row.get(key) not in (False, 0) for key in ("economic_run", "economic_eligible", "admitted", "integration_candidate", "promotion")):
            raise ValueError(f"unsafe_result_flag:{row.get('candidate_id')}")
        source = row.get("result_source")
        if not isinstance(source, dict) or sha256(REPO / source["path"]) != source["sha256"]:
            raise ValueError(f"result_source_hash_mismatch:{row.get('candidate_id')}")
    return {"status": "CRYPTO_MAX_TESTING_PROTOCOL_VERIFIED", "candidate_count": 12, "economic_run": False, "read_only": True, "manifest_sha256": actual}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else build(), sort_keys=True))


if __name__ == "__main__":
    main()
