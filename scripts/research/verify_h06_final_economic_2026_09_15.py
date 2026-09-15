#!/usr/bin/env python3
"""Independent file-based verifier for the frozen H06 one-shot result."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from parayabuck.backtest.portfolio_engine import OrderSide, equity_regulatory_fee
from parayabuck.backtest.regulatory_fees import rates_for_trade_date

REPO = Path(__file__).resolve().parents[2]
STRUCTURAL = REPO / "data/research_manifests/h06_full_structural_admission_2026_09_14.json"
PROTOCOL = REPO / "data/research_manifests/h06_final_economic_protocol_2026_09_15.json"
STATE = REPO / "data/research_manifests/h06_final_one_shot_state_2026_09_15.json"
AUTH = REPO / "data/research_receipts/h06_final_economic_authorization_2026_09_15.json"
V3 = REPO / "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json"
OUT = REPO / "data/research/runs/h06_final_2026_09_15"
RESULTS = OUT / "results.json"
TRADES = OUT / "trade_ledger.jsonl"
DAILY = OUT / "daily_nav.jsonl"
STATISTICS = OUT / "statistics.json"
RECEIPT = OUT / "run_receipt.json"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def self_hash(value: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.setdefault("integrity", {}).pop("self_sha256", None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def verify_seal(value: Mapping[str, Any]) -> None:
    if value.get("integrity", {}).get("self_sha256") != self_hash(value):
        raise ValueError("self_hash_mismatch")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(values: Sequence[float], probability: float) -> float:
    position = (len(values) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(values[lower])
    weight = position - lower
    return float(values[lower] * (1 - weight) + values[upper] * weight)


def bootstrap(values: Sequence[float]) -> dict[str, Any]:
    rng = random.Random(20260909)
    estimates = []
    for _ in range(10000):
        sample: list[float] = []
        while len(sample) < len(values):
            start = rng.randrange(len(values) - 20)
            sample.extend(values[start : start + 21])
        estimates.append(statistics.fmean(sample[: len(values)]))
    estimates.sort()
    return {"status": "COMPLETE", "resamples": 10000, "block_sessions": 21, "seed": 20260909,
            "mean": statistics.fmean(estimates), "median": statistics.median(estimates),
            "ci95": [percentile(estimates, 0.025), percentile(estimates, 0.975)]}


def permutation(values: Sequence[float]) -> dict[str, Any]:
    observed = statistics.fmean(values)
    rng = random.Random(20260910)
    null = [statistics.fmean([value if rng.getrandbits(1) else -value for value in values]) for _ in range(10000)]
    return {"status": "COMPLETE", "draws": 10000, "seed": 20260910, "observed_mean": observed,
            "two_sided_pvalue": (sum(abs(value) >= abs(observed) for value in null) + 1) / 10001,
            "one_sided_positive_pvalue": (sum(value >= observed for value in null) + 1) / 10001}


def recompute_selections(structural: Mapping[str, Any], protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    unique, caps = {}, {}
    for row in structural["economic_input_rows"]:
        if not row["final_admitted"]:
            continue
        unique.setdefault(row["economic_transaction_id"], row)
        caps[(row["issuer_cik"], row["issuer_symbol"], row["decision_session"])] = float(row["market_cap"])
    result = []
    for decision in protocol["monthly_decisions"]:
        prior = set(decision["prior_21_sessions"])
        notionals, ids = defaultdict(float), defaultdict(list)
        for identity, row in unique.items():
            if row["information_session"] not in prior:
                continue
            key = row["issuer_cik"], row["issuer_symbol"]
            notionals[key] += float(row["shares"]) * float(row["price"])
            ids[key].append(identity)
        scores = []
        for (issuer, symbol), notional in notionals.items():
            cap = caps.get((issuer, symbol, decision["decision_session"]))
            if cap:
                scores.append({"issuer_cik": issuer, "symbol": symbol, "score": notional / cap,
                               "purchase_value": notional, "market_cap": cap,
                               "economic_transaction_ids": sorted(ids[(issuer, symbol)])})
        scores.sort(key=lambda row: (-row["score"], row["issuer_cik"], row["symbol"]))
        selected = scores[: max(1, math.ceil(len(scores) / 10))] if scores else []
        result.append({"decision_session": decision["decision_session"], "decision_timestamp": decision["decision_timestamp"],
                       "positive_score_count": len(scores), "selected": selected})
    return result


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def verify() -> dict[str, Any]:
    paths = (STRUCTURAL, PROTOCOL, STATE, AUTH, RESULTS, STATISTICS, RECEIPT)
    values = {path: json.loads(path.read_text(encoding="utf-8")) for path in paths}
    for value in values.values():
        verify_seal(value)
    structural, protocol, results, stats, receipt = (values[path] for path in (STRUCTURAL, PROTOCOL, RESULTS, STATISTICS, RECEIPT))
    for relative, expected in receipt["files"].items():
        if sha256_file(REPO / relative) != expected:
            raise ValueError(f"result_file_hash_mismatch:{relative}")
    bundle_sha = hashlib.sha256(canonical(receipt["files"])).hexdigest()
    if bundle_sha != receipt["bundle_sha256"]:
        raise ValueError("bundle_hash_mismatch")
    consumed_path = STATE.with_name(f"{STATE.name}.consumed.json")
    consumed = json.loads(consumed_path.read_text(encoding="utf-8"))
    verify_seal(consumed)
    if consumed["status"] != "CONSUMED" or consumed["run_count"] != 1 or consumed["rerun_count"] != 0 or consumed["output_bundle_sha256"] != bundle_sha:
        raise ValueError("one_shot_transition_invalid")
    if recompute_selections(structural, protocol) != results["primary_selections"]:
        raise ValueError("selection_recomputation_mismatch")
    trades, daily = read_jsonl(TRADES), read_jsonl(DAILY)
    for row in trades:
        cost = int(row["cost_multiplier"])
        reference, qty = float(row["reference_price"]), float(row["qty"])
        adverse = (2 * cost + cost) / 10000
        expected_fill = reference * (1 + adverse if row["side"] == "buy" else 1 - adverse)
        if not math.isclose(float(row["fill_price"]), expected_fill, rel_tol=1e-12):
            raise ValueError("fill_cost_mismatch")
        rates = rates_for_trade_date(date.fromisoformat(row["session"]))
        fee = equity_regulatory_fee(OrderSide(row["side"]), qty, expected_fill, taf_per_share=rates.taf_per_share,
                                    taf_cap=rates.taf_cap, cat_per_share=rates.cat_per_share, sec_fee_rate=rates.sec_fee_rate)
        if not math.isclose(float(row["fee"]), fee, rel_tol=1e-12, abs_tol=1e-10):
            raise ValueError("regulatory_fee_mismatch")
    if any(str(row["session"]) > "2026-07-31" for row in daily + trades):
        raise ValueError("post_cutoff_data_use")
    grouped = defaultdict(list)
    for row in daily:
        grouped[(row["portfolio"], row["cost_multiplier"])].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["session"])
        for previous, current in zip(rows, rows[1:]):
            if not math.isclose(float(current["return"]), float(current["nav"]) / float(previous["nav"]) - 1, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError("daily_nav_return_chain_invalid")
    v3 = json.loads(V3.read_text(encoding="utf-8"))
    v3_map = {str(row["date"])[:10]: float(row["nav"]) for row in v3["daily_nav"]}
    for cost in (1, 2, 3):
        rows = grouped[("shared_overlay", cost)]
        first = rows[0]["session"]
        v3_base = v3_map[first]
        for row in rows:
            expected = 0.95 * (100000 * v3_map[row["session"]] / v3_base) + 0.05 * float(row["candidate_nav"])
            if not math.isclose(float(row["nav"]), expected, rel_tol=1e-12, abs_tol=1e-8):
                raise ValueError("overlay_arithmetic_invalid")
            if float(row["exposure"]) > 0.55 + 1e-10 or int(row["holdings"]) > 6:
                raise ValueError("overlay_cap_violation")
    full_start, full_end = date(2022, 1, 1), date(2025, 12, 31)
    standalone = [float(row["return"]) for row in grouped[("standalone", 1)] if full_start <= date.fromisoformat(row["session"]) <= full_end]
    overlay = [row for row in grouped[("shared_overlay", 1)] if full_start <= date.fromisoformat(row["session"]) <= full_end]
    delta = [float(row["return"]) - float(row["v3_return"]) for row in overlay]
    if bootstrap(standalone) != stats["standalone_full_mean_return_bootstrap"] or bootstrap(delta) != stats["overlay_full_mean_delta_bootstrap"] or permutation(delta) != stats["overlay_paired_sign_permutation"]:
        raise ValueError("statistics_recomputation_mismatch")
    gates = results["directional_gates"]
    recomputed = {
        "1": results["standalone"]["1"]["full"]["total_return"] > 0,
        "2": (results["standalone"]["1"]["full"]["sharpe"] or 0) > 0,
        "3": results["standalone"]["1"]["recent"]["total_return"] > 0,
        "4": results["standalone"]["1"]["true_holdout"]["total_return"] > 0,
        "5": results["standalone"]["3"]["full"]["total_return"] > 0,
        "6": results["shared_overlay"]["1"]["full"]["return_delta"] > 0,
        "7": results["shared_overlay"]["1"]["recent"]["return_delta"] > 0,
        "8": results["shared_overlay"]["1"]["true_holdout"]["return_delta"] >= 0,
        "9": results["shared_overlay"]["3"]["full"]["return_delta"] > 0,
        "10": results["cap_violations"] == 0,
    }
    if gates != recomputed:
        raise ValueError("directional_gate_mismatch")
    activity = results["selection_activity"]
    if activity["full"]["selected_issuer_months"] == 0 or activity["true_holdout"]["selected_issuer_months"] == 0:
        verdict = "H06_RESEARCH_INSUFFICIENT_ECONOMIC_ACTIVITY"
    elif not all(recomputed.values()):
        verdict = "H06_RESEARCH_REJECT_ECONOMIC"
    elif stats["overlay_full_mean_delta_bootstrap"]["ci95"][0] > 0 and stats["overlay_paired_sign_permutation"]["one_sided_positive_pvalue"] <= 0.05:
        verdict = "H06_RESEARCH_ADMIT_STRONG"
    else:
        verdict = "H06_RESEARCH_ADMIT_PROVISIONAL"
    if verdict != results["final_verdict"]:
        raise ValueError("verdict_rule_mismatch")
    return {"status": "H06_FINAL_RESULT_VERIFIED", "verdict": verdict, "run_count": 1,
            "accounting": "PASS", "caps": "PASS", "post_cutoff": "PASS", "one_shot": "CONSUMED"}


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
