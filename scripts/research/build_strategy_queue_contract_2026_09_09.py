"""Freeze the definition side of the 34-card research queue.

This package contains only the 2026-09-06 frozen definitions.  Result status,
coverage, PIT evidence and terminal decisions are deliberately absent; those
belong to the separately pinned current result records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PRIMARY = "docs/research/strategy_queue_full_report_2026_09_06.md"
OUTPUT_DIR = "data/research_runs/strategy_queue_contract_2026_09_09"
CONTRACT_NAME = "candidate_contracts.json"
RECEIPT_NAME = "hash_receipt.json"
PENDING = "PENDING_FREEZE"
UNSPECIFIED = "not_separately_specified_in_frozen_source"
SOURCE_NORMALIZED_SHA256 = "108c909aa485e3656b24fa4a62f8bb6d8168da98e8b9637c2a52e9e76fe45eb5"

EXPECTED_IDS = [*(f"H{i:02d}" for i in range(1, 17)), *(f"K{i:02d}" for i in range(1, 13)), *(f"X{i:02d}" for i in range(1, 7))]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_hash(value: dict[str, Any], field: str) -> str:
    payload = dict(value)
    payload[field] = PENDING
    return hashlib.sha256(canonical(payload)).hexdigest()


CARD_ROWS: dict[str, tuple[str, str]] = {
    "H01": ("Value; Asness, Moskowitz, Pedersen", "No PIT book/market or identifier history; value may complement V3 but overlaps H03/H05"),
    "H02": ("Gross profitability; Novy-Marx", "No PIT accounting concepts or restatement policy; quality/value overlap"),
    "H03": ("Low accruals; Sloan", "No PIT income/cash-flow/asset fields; quality-adjacent"),
    "H04": ("Low asset growth; Cooper, Gulen, Schill", "No PIT balance sheets, M&A/spinoff treatment; investment/value overlap"),
    "H05": ("Net payout yield; Boudoukh et al.", "No PIT buyback/issuance plus dividend action history in one decision-safe feed"),
    "H06": ("Public insider purchases; Cohen, Malloy, Pomorski; SEC Form 4 timing", "No EDGAR acceptance-time archive, transaction taxonomy, or historical insider routine"),
    "H07": ("Analyst forecast revisions; Chan, Jegadeesh, Lakonishok", "No forecast vintages, publication times, estimate horizons"),
    "H08": ("PEAD; Bernard and Thomas", "No earnings release timestamp or pre-release consensus; event adapter absent"),
    "H09": ("Customer/supplier information spillover; Cohen and Frazzini", "No PIT relationship or event feed; likely information-momentum adjacency"),
    "H10": ("Filing-text change / Lazy Prices; Cohen, Malloy, Nguyen", "No original filing vintages, publication timestamps, or text pipeline"),
    "H11": ("Fund-flow forced sales; Coval and Stafford", "No PIT holdings/flow and rebalance-event archive; reversal-adjacent"),
    "H12": ("Index-change price pressure; Greenwood and Sammon", "No announcement/application-time and PIT membership feed; effect may be weaker"),
    "H13": ("Overnight versus intraday; Lou, Polk, Skouras", "No ETF intraday/quote history or opening/closing execution truth"),
    "H14": ("Turn-of-month ETF exposure; McConnell and Xu", "SPY-only candidate and fixed-calendar control completed the frozen 18-cell retrospective diagnostic; QQQ/IWM are context sidecars, not gates; no intraday truth or current-V3 joint NAV"),
    "H15": ("Cash merger arbitrage; Mitchell and Pulvino", "No deal terms, amendment/close/break event archive or terminal lifecycle"),
    "H16": ("Post-spinoff pricing; Amihud, Huh, Subrahmanyam", "No spin-off events, successor mapping, distributions, or lifecycle handling"),
    "K01": ("MVRV; Coin Metrics realized-cap methodology", "No ledger movements, realized cap, chain cutoff, or PIT revisions; overlaps K02/K12"),
    "K02": ("NVT; Coin Metrics adjusted-transfer methodology", "No adjusted transfer value or chain-specific PIT; exchange volume is invalid proxy"),
    "K03": ("Economic network-use growth; Coin Metrics address/transfer methods", "No active/economic addresses, fees, Sybil controls, or metric vintages"),
    "K04": ("Net issuance/burn dilution; Coin Metrics supply methods", "No issuance, burn, current/expected supply, vesting, or migration history"),
    "K05": ("Preannounced token unlocks", "No immutable announcement/vesting archive, publication time, or actual unlock confirmation"),
    "K06": ("Spot crypto ETF net flows; Lim and SEC filing", "No issuer flow history, creation/redemption timestamps, or ETF PIT mapping; short regime"),
    "K07": ("Exchange coin inflow/outflow; Glassnode PIT warning", "No transfers, labels, label vintages, venue scope; overlaps K08"),
    "K08": ("Stablecoin exchange liquidity; Chi et al.", "No token contract, mint/burn/bridge exclusions, labels, or PIT flow"),
    "K09": ("Basis/funding/OI as spot signal; BIS and exchange schemas", "No causal feature sidecar or available-at join. Spot-only output needs no derivative ledger"),
    "K10": ("Spot rebound after liquidation shock; Deribit/Kraken schemas", "No liquidation events, OI, mark/index, side, quantity, or venue aggregation; spot-only test"),
    "K11": ("Signed order flow; Anastasopoulos et al.", "Paper includes long-only analysis, but no local trades/quotes/depth/aggressor data or cost truth"),
    "K12": ("SOPR loss realization; Glassnode/Coin Metrics methods", "No UTXO/account history, entity clusters, or immutable PIT metric; overlaps K01/K10"),
    "X01": ("Bond ETF curve carry/roll-down; Koijen et al.", "No yield curves, duration history, distributions, or ETF carry inputs; direct paper is not ETF proof"),
    "X02": ("Commodity ETF futures-curve carry; Koijen/CME/SEC", "No dated futures curves, contract specs, rolls, collateral, or fund receipts"),
    "X03": ("Country ETF PIT value; Asness et al.", "No country valuation vintages, FX, ETF accounting, or PIT membership; H01 family"),
    "X04": ("Equity pairs/stat arb; Gatev et al.", "Requires short leg, borrow, recall, financing, pair margin, and two-leg fills"),
    "X05": ("Crypto dated-futures cash-and-carry; BIS", "Requires spot/futures legs, basis, expiry, margin, settlement, and derivative history"),
    "X06": ("Spot/perpetual funding carry; Binance/BIS", "Requires perp short, funding, mark/index, margin, liquidation, and two-leg history"),
}


def _definition(candidate_id: str, mechanism: str, feasibility: str, source_sha256: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "mechanism": mechanism,
        "feasibility": feasibility,
        "unspecified_fields": {
            "purpose_definition": None,
            "field_formula": None,
            "window": None,
            "cost_stress": None,
            "controls": None,
            "data_requirements": None,
            "scope": UNSPECIFIED,
        },
        "definition_source": {"path": PRIMARY, "sha256": source_sha256, "section": f"All 34 cards; {candidate_id}"},
    }


DEFINITIONS: dict[str, dict[str, Any]] = {
    candidate_id: _definition(candidate_id, mechanism, feasibility, "")
    for candidate_id, (mechanism, feasibility) in CARD_ROWS.items()
}


def build_contract(repo: Path = REPO) -> dict[str, Any]:
    source_paths = sorted({str(value["definition_source"]["path"]) for value in DEFINITIONS.values()})
    source_pins: dict[str, dict[str, Any]] = {}
    for relative in source_paths:
        path = repo / relative
        if not path.is_file():
            raise ValueError(f"definition_source_missing:{relative}")
        source_pins[relative] = {"sha256": sha256(path), "bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
    candidates: dict[str, dict[str, Any]] = {}
    for candidate_id in EXPECTED_IDS:
        item = json.loads(json.dumps(DEFINITIONS[candidate_id]))
        item["candidate_id"] = candidate_id
        item["definition_source"]["sha256"] = source_pins[item["definition_source"]["path"]]["sha256"]
        candidates[candidate_id] = item
    contract = {
        "schema": "strategy-queue-definition-contract-v1",
        "candidate_count": len(EXPECTED_IDS),
        "candidate_ids": EXPECTED_IDS,
        "candidate_ids_unique": True,
        "source_pins": source_pins,
        "source_normalized_sha256": SOURCE_NORMALIZED_SHA256,
        "scope_metadata": {
            "path": PRIMARY,
            "sha256": source_pins[PRIMARY]["sha256"],
            "section": "Shared queue-level requirements; lines 164-195",
            "candidate_specific": False,
            "status": UNSPECIFIED,
        },
        "candidates": candidates,
        "economic_run": False,
        "promotion": False,
        "contract_sha256": PENDING,
    }
    contract["contract_sha256"] = canonical_hash(contract, "contract_sha256")
    return contract


def verify_contract(contract: dict[str, Any], repo: Path = REPO) -> None:
    if contract.get("contract_sha256") != canonical_hash(contract, "contract_sha256"):
        raise ValueError("contract_self_hash_mismatch")
    if contract.get("candidate_ids") != EXPECTED_IDS or type(contract.get("candidate_count")) is not int or contract.get("candidate_count") != 34 or contract.get("candidate_ids_unique") is not True:
        raise ValueError("contract_inventory_invalid")
    if contract.get("economic_run") is not False or contract.get("promotion") is not False:
        raise ValueError("contract_policy_invalid")
    if contract.get("source_normalized_sha256") != SOURCE_NORMALIZED_SHA256:
        raise ValueError("contract_source_normalized_hash_invalid")
    scope = contract.get("scope_metadata")
    if not isinstance(scope, dict) or scope.get("path") != PRIMARY or scope.get("sha256") != contract["source_pins"].get(PRIMARY, {}).get("sha256") or scope.get("candidate_specific") is not False or scope.get("status") != UNSPECIFIED:
        raise ValueError("contract_scope_metadata_invalid")
    candidates = contract.get("candidates")
    if not isinstance(candidates, dict) or set(candidates) != set(EXPECTED_IDS):
        raise ValueError("contract_candidates_invalid")
    for candidate_id in EXPECTED_IDS:
        item = candidates[candidate_id]
        if item.get("candidate_id") != candidate_id or not isinstance(item.get("mechanism"), str) or not item["mechanism"].strip() or not isinstance(item.get("feasibility"), str) or not item["feasibility"].strip():
            raise ValueError(f"contract_definition_missing:{candidate_id}")
        unspecified = item.get("unspecified_fields")
        required_unspecified = ("purpose_definition", "field_formula", "window", "cost_stress", "controls", "data_requirements", "scope")
        if not isinstance(unspecified, dict) or any(field not in unspecified for field in required_unspecified) or any(unspecified[field] is not None for field in required_unspecified[:-1]) or unspecified["scope"] != UNSPECIFIED:
            raise ValueError(f"contract_unspecified_fields_invalid:{candidate_id}")
        source = item.get("definition_source")
        if not isinstance(source, dict) or set(source) != {"path", "sha256", "section"} or source.get("path") != PRIMARY or source.get("sha256") != contract["source_pins"].get(PRIMARY, {}).get("sha256") or not isinstance(source.get("section"), str) or not source["section"].strip():
            raise ValueError(f"contract_source_binding_invalid:{candidate_id}")
    for relative, pin in contract["source_pins"].items():
        path = repo / relative
        if not path.is_file() or sha256(path) != pin.get("sha256") or path.stat().st_size != pin.get("bytes") or path.stat().st_mtime_ns != pin.get("mtime_ns"):
            raise ValueError(f"contract_source_pin_mismatch:{relative}")


def write_contract(repo: Path = REPO, output_dir: Path | None = None) -> dict[str, Any]:
    output = output_dir or repo / OUTPUT_DIR
    contract_path = output / CONTRACT_NAME
    receipt_path = output / RECEIPT_NAME
    contract = build_contract(repo)
    verify_contract(contract, repo)
    contract_bytes = (json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    contract_sha = hashlib.sha256(contract_bytes).hexdigest()
    try:
        contract_ref = str(contract_path.relative_to(repo))
    except ValueError:
        contract_ref = str(contract_path)
    receipt = {
        "schema": "strategy-queue-definition-contract-receipt-v1",
        "contract": {"path": contract_ref, "sha256": contract_sha},
        "source_pins": contract["source_pins"],
        "candidate_count": 34,
        "candidate_ids": EXPECTED_IDS,
        "receipt_sha256": PENDING,
    }
    receipt["receipt_sha256"] = canonical_hash(receipt, "receipt_sha256")
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    for path, data in ((contract_path, contract_bytes), (receipt_path, receipt_bytes)):
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
    return {"contract": str(contract_path), "receipt": str(receipt_path), "contract_sha256": contract_sha, "receipt_sha256": receipt["receipt_sha256"], "candidate_count": 34}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the frozen 34-card definition contract")
    parser.add_argument("--output-dir", type=Path, default=REPO / OUTPUT_DIR)
    args = parser.parse_args(argv)
    try:
        result = write_contract(REPO, args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "NOT_READY", "error": str(exc), "outputs_written": False}, sort_keys=True))
        return 1
    print(json.dumps({"status": "ALL_34_DEFINITION_BINDING_READY", "outputs_written": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
