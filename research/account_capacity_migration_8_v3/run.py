"""Run one frozen migration arm twice using the tagged physical replay."""

from __future__ import annotations

import argparse
import gc
import gzip
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from parayabuck.config import load_config
from parayabuck.core.models import Market
from research.physical_availability_clock_v2 import shared_replay as shared
from research.physical_availability_clock_v2.run_historical import (
    CONFIG_PATH,
    _causality_receipt,
    _digest,
    _event_hashes,
    _new_replay,
)

OUT = ROOT / "research/account_capacity_migration_8_v3"
FREEZE = json.loads((OUT / "FROZEN_MIGRATION_SPEC_V3.json").read_text())
TAGGED_CAP6_EVENT_SHA256 = "841091dd6ebedbc03e3dde27eaf7bee09e54474e660554c3c4b28423d3eb186d"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _observe_run(
    cap: int,
) -> tuple[shared.SharedReplayResult, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    base = load_config(str(CONFIG_PATH))
    if base.risk.max_positions != 6:
        raise ValueError("Production cap differs from frozen six")
    config = replace(base, risk=replace(base.risk, max_positions=cap))
    replay, identity = _new_replay(config, (Market.EQUITY, Market.CRYPTO))
    risk_rows: list[dict[str, Any]] = []
    admission_rows: list[dict[str, Any]] = []
    original_overlay = shared._SharedOverlay.apply
    original_admit = shared._admit_synthetic_orders

    def observe_overlay(self: Any, target: Any, account: Any, **kwargs: Any) -> Any:
        market = kwargs["market"]
        positions = [
            [key_market.value, symbol, position.qty]
            for (key_market, symbol), position in sorted(
                self._account.positions.items(), key=lambda item: (item[0][0].value, item[0][1])
            )
        ]
        shared_coordination = len(self._account.engines) > 1
        pending = (
            {
                (key_market, order.symbol)
                for key_market in shared.ORDER_MARKETS
                for order in self._account.pending[key_market]
            }
            if shared_coordination
            else set()
        )
        deferred = (
            {
                (key_market, order.symbol)
                for key_market in shared.ORDER_MARKETS
                if key_market != market
                for order in self._account.deferred_buys[key_market]
            }
            if shared_coordination
            else set()
        )
        reserved = [
            [key_market.value, symbol]
            for key_market, symbol in sorted(
                self._account.same_cycle_buy_reservations
                | set(kwargs.get("reserved_position_keys", ()))
                | pending
                | deferred,
                key=lambda item: (item[0].value, item[1]),
            )
        ]
        decided = original_overlay(self, target, account, **kwargs)
        risk_rows.append(
            {
                "market": market.value,
                "label_time": target.decision_timestamp.isoformat(),
                "raw_target": [[weight.symbol, weight.weight, weight.rank] for weight in target.weights],
                "approved_target": [[weight.symbol, weight.weight] for weight in decided.target.weights],
                "reasons": list(decided.reasons),
                "positions": positions,
                "reserved": reserved,
                "equity_at_decision": account.equity,
                "cash_at_decision": account.cash,
            }
        )
        return decided

    def observe_admission(orders: Any, account: Any, maximum_positions: int, prior_keys: Any) -> Any:
        admitted = original_admit(orders, account, maximum_positions, prior_keys)
        admitted_ids = {order.client_order_id for order in admitted}
        for order in orders:
            if order.client_order_id not in admitted_ids:
                admission_rows.append(
                    {
                        "market": order.market.value,
                        "symbol": order.symbol,
                        "decision_label_time": order.decision_timestamp.isoformat(),
                        "order_created_at": order.created_at.isoformat(),
                        "client_order_id": order.client_order_id,
                        "reason": "GLOBAL_POSITION_COUNT",
                        "account_positions": [[p.market.value, p.symbol] for p in account.positions if not p.is_flat],
                        "reserved_position_keys": [[m.value, s] for m, s in prior_keys],
                    }
                )
        return admitted

    with (
        patch.object(shared._SharedOverlay, "apply", observe_overlay),
        patch.object(shared, "_admit_synthetic_orders", observe_admission),
    ):
        result = replay.run(liquidate_at_end=True)
    causality = _causality_receipt(replay, result)
    return result, identity, risk_rows, admission_rows, causality


def _summarize(
    result: shared.SharedReplayResult,
    risk_rows: list[dict[str, Any]],
    admission_rows: list[dict[str, Any]],
    cap: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    decision_at = {
        (str(event["market"]), str(event["label_time"])): str(event["timestamp"])
        for event in result.events
        if event["event"] == "decision"
    }
    denials: list[dict[str, Any]] = []
    requested = approved = 0
    target_requested = target_approved = count_shortfall = 0.0
    for row in risk_rows:
        market = str(row["market"])
        existing = {(p[0], p[1]) for p in row["positions"]}
        raw = {symbol: float(weight) for symbol, weight, _rank in row["raw_target"]}
        adjusted = {symbol: float(weight) for symbol, weight in row["approved_target"]}
        target_requested += sum(raw.values())
        target_approved += sum(adjusted.values())
        opposite = sum(p[0] != market for p in row["positions"]) + sum(p[0] != market for p in row["reserved"])
        total_claims = len(existing | {(p[0], p[1]) for p in row["reserved"]})
        for symbol, weight in raw.items():
            if (market, symbol) in existing or weight <= 0:
                continue
            requested += 1
            if adjusted.get(symbol, 0.0) > 0:
                approved += 1
                continue
            reasons = set(row["reasons"])
            if "position_count_limit" in reasons:
                reason = (
                    "GLOBAL_POSITION_COUNT" if market == "equity" or total_claims >= cap else "CRYPTO_POSITION_COUNT"
                )
            elif "daily_loss_limit" in reasons:
                reason = "DAILY_LOSS_LIMIT"
            elif "exposure_scaled_to_limit" in reasons:
                reason = "GROSS_OR_SLEEVE_OR_CASH_LIMIT"
            else:
                reason = "OTHER_EXPLICIT_POLICY"
            if reason == "GLOBAL_POSITION_COUNT":
                count_shortfall += weight
            denials.append(
                {
                    "arm_cap": cap,
                    "timestamp": decision_at.get((market, row["label_time"]), row["label_time"]),
                    "decision_label_time": row["label_time"],
                    "market": market,
                    "symbol": symbol,
                    "reason": reason,
                    "requested_weight": weight,
                    "approved_weight": adjusted.get(symbol, 0.0),
                    "account_slot_claims_before": total_claims,
                    "opposite_market_claims_before": opposite,
                    "decision_nav": row["equity_at_decision"],
                    "count_shortfall_notional": weight * float(row["equity_at_decision"])
                    if reason == "GLOBAL_POSITION_COUNT"
                    else 0.0,
                    "crowd_out_direction": f"{'CRYPTO' if market == 'equity' else 'EQUITY'}_TO_{market.upper()}"
                    if reason == "GLOBAL_POSITION_COUNT" and opposite
                    else "NONE",
                }
            )
    for row in admission_rows:
        denials.append(
            {
                "arm_cap": cap,
                "timestamp": row["order_created_at"],
                "decision_label_time": row["decision_label_time"],
                "market": row["market"],
                "symbol": row["symbol"],
                "reason": row["reason"],
                "requested_weight": None,
                "approved_weight": None,
                "account_slot_claims_before": len(row["account_positions"]) + len(row["reserved_position_keys"]),
                "opposite_market_claims_before": sum(
                    p[0] != row["market"] for p in row["account_positions"] + row["reserved_position_keys"]
                ),
                "decision_nav": None,
                "count_shortfall_notional": None,
                "crowd_out_direction": "ORDER_ADMISSION",
            }
        )
    qty: dict[tuple[str, str], float] = defaultdict(float)
    marks: dict[tuple[str, str], float] = {}
    position_dist: Counter[int] = Counter()
    equity_dist: Counter[int] = Counter()
    crypto_dist: Counter[int] = Counter()
    claims_dist: Counter[int] = Counter()
    daily: dict[str, dict[str, float]] = {}
    at_cap: set[str] = set()
    max_hhi = max_name = max_working = 0.0
    working: set[tuple[str, str]] = set()
    cost = traded_notional = 0.0
    event_counts: Counter[str] = Counter()
    for event in result.events:
        kind = str(event["event"])
        event_counts[kind] += 1
        if kind == "fill":
            key = (str(event["market"]), str(event["symbol"]))
            sign = 1 if event["side"] == "buy" else -1
            qty[key] += sign * float(event["qty"])
            if qty[key] <= 1e-9:
                qty.pop(key, None)
            marks[key] = float(event["reference_price"])
            cost += sum(float(event[name]) for name in ("slippage_cost", "spread_cost", "fee"))
            traded_notional += float(event["qty"]) * float(event["reference_price"])
        elif kind == "mark":
            marks[(str(event["market"]), str(event["symbol"]))] = float(event["price"])
        elif kind == "corporate_action" and event["split_ratio"] is not None:
            key = (str(event["market"]), str(event["symbol"]))
            if key in qty:
                qty[key] *= float(event["split_ratio"])
        elif kind in {"terminal_liquidation", "final_liquidation"}:
            key = (str(event["market"]), str(event["symbol"]))
            qty.pop(key, None)
            cost += sum(float(event[name]) for name in ("slippage_cost", "spread_cost", "fee"))
        elif kind == "reservation_created":
            working.add((str(event["market"]), str(event["client_order_id"])))
            max_working = max(max_working, float(len(working)))
        elif kind == "reservation_released":
            working.discard((str(event["market"]), str(event["client_order_id"])))
        elif kind == "account_mark":
            count = len(qty)
            if count != int(event["position_count"]):
                raise AssertionError(f"Independent inventory count differs at {event['timestamp']}")
            equity_count = sum(m == "equity" for m, _s in qty)
            crypto_count = count - equity_count
            claims = int(event["slot_claim_count"])
            position_dist[count] += 1
            equity_dist[equity_count] += 1
            crypto_dist[crypto_count] += 1
            claims_dist[claims] += 1
            if claims == cap:
                at_cap.add(str(event["timestamp"]))
            nav = float(event["nav"])
            weights = [value * marks.get(key, 0.0) / nav for key, value in qty.items()] if nav > 0 else []
            hhi = sum(weight * weight for weight in weights)
            max_hhi = max(max_hhi, hhi)
            max_name = max(max_name, max(weights, default=0.0))
            daily[str(event["timestamp"])[:10]] = {
                "nav": nav,
                "cash": float(event["cash"]),
                "gross": float(event["gross"]),
                "positions": float(count),
                "equity_positions": float(equity_count),
                "crypto_positions": float(crypto_count),
                "claims": float(claims),
                "hhi": hhi,
            }
    if working:
        raise AssertionError("Unreleased working orders in event trace")
    values = list(daily.values())

    def average(name: str) -> float:
        return sum(row[name] for row in values) / len(values) if values else 0.0

    average_nav_marks = sum(result.nav_path) / len(result.nav_path)
    return (
        {
            "decisions": event_counts["decision"],
            "equity_decisions": sum(e["event"] == "decision" and e.get("market") == "equity" for e in result.events),
            "crypto_decisions": sum(e["event"] == "decision" and e.get("market") == "crypto" for e in result.events),
            "raw_new_symbol_target_openings": requested,
            "risk_approved_new_symbol_target_openings": approved,
            "global_count_denied_target_openings": sum(
                row["reason"] == "GLOBAL_POSITION_COUNT" and row["requested_weight"] is not None for row in denials
            ),
            "global_count_denied_order_admissions": len(admission_rows),
            "count_target_weight_shortfall_sum": count_shortfall,
            "count_target_notional_shortfall_sum": sum(float(row["count_shortfall_notional"] or 0) for row in denials),
            "target_weight_requested_sum": target_requested,
            "target_weight_approved_sum": target_approved,
            "other_denial_counts": dict(
                Counter(row["reason"] for row in denials if row["reason"] != "GLOBAL_POSITION_COUNT")
            ),
            "crowdout_counts": dict(
                Counter(
                    row["crowd_out_direction"]
                    for row in denials
                    if row["crowd_out_direction"] not in {"NONE", "ORDER_ADMISSION"}
                )
            ),
            "timestamps_at_global_cap": len(at_cap),
            "position_count_distribution": dict(position_dist),
            "equity_position_count_distribution": dict(equity_dist),
            "crypto_position_count_distribution": dict(crypto_dist),
            "occupied_reserved_claim_distribution": dict(claims_dist),
            "maximum_actual_positions": max(position_dist, default=0),
            "maximum_effective_slot_claims": max(claims_dist, default=0),
            "average_positions_daily": average("positions"),
            "average_equity_positions_daily": average("equity_positions"),
            "average_crypto_positions_daily": average("crypto_positions"),
            "maximum_total_account_hhi": max_hhi,
            "maximum_actual_name_weight": max_name,
            "maximum_name_target_weight": result.invariants["maximum_target_name_weight"],
            "average_cash_daily": average("cash"),
            "average_realized_gross_daily": average("gross"),
            "average_hhi_daily": average("hhi"),
            "maximum_realized_gross": result.invariants["maximum_realized_gross_observed"],
            "maximum_target_gross": result.invariants["maximum_target_gross_observed"],
            "fills": event_counts["fill"],
            "orders": event_counts["reservation_created"],
            "reservations_created": event_counts["reservation_created"],
            "reservations_released": event_counts["reservation_released"],
            "pending_conflict_events": event_counts["buy_fill_deferred"] + event_counts["buy_intent_deferred"],
            "peak_working_orders": int(max_working),
            "orders_per_decision": event_counts["reservation_created"] / event_counts["decision"]
            if event_counts["decision"]
            else 0.0,
            "reconciliation_marks": event_counts["account_mark"],
            "corporate_action_events": event_counts["corporate_action"],
            "total_modeled_costs": cost,
            "traded_notional": traded_notional,
            "turnover": traded_notional / average_nav_marks if average_nav_marks > 0 else 0.0,
            "daily_last_marks": daily,
        },
        denials,
    )


def run_arm(cap: int) -> None:
    if cap not in (6, 8):
        raise ValueError("Only frozen cap-six and cap-eight arms exist")
    if cap == 8:
        control = OUT / "CONTROL_CAP6_RESULTS_V3.json"
        if not control.exists() or json.loads(control.read_text()).get("status") != "INTEGRITY_PASS":
            raise RuntimeError("Treatment requires completed validated cap-six control")
    for path, expected in FREEZE["direct_source_sha256"].items():
        if _sha(ROOT / path) != expected:
            raise RuntimeError(f"Frozen source changed: {path}")
    if _sha(CONFIG_PATH) != FREEZE["config_sha256"]:
        raise RuntimeError("Frozen production config changed")
    first, identity, risk_rows, admission_rows, causality = _observe_run(cap)
    if identity != FREEZE["input_identity"]:
        raise RuntimeError("Historical/PIT input identity differs from freeze")
    if cap == 6 and first.event_sha256 != TAGGED_CAP6_EVENT_SHA256:
        raise RuntimeError("Cap-six control diverges from validated physical replay event stream")
    hard = {
        "cash": bool(first.invariants["cash_conserved"]),
        "positions": bool(first.invariants["position_quantities_reconciled"]),
        "nav": bool(first.invariants["nav_matches_ledger"]),
        "slots": bool(first.invariants["slot_claims_within_limit"]),
        "no_slot_violations": first.invariants["slot_claim_violation_count"] == 0,
        "gross": bool(first.invariants["target_gross_within_limit"]),
        "sleeves": bool(first.invariants["sleeve_targets_within_limits"]),
        "name_caps": bool(first.invariants["target_name_weights_within_limits"]),
        "reservations": bool(first.invariants["all_reservations_released"]),
        "fill_ownership": first.invariants["unowned_fill_count"] == 0,
        "duplicate_fills": first.invariants["duplicate_fill_count"] == 0,
        "cash_nonnegative": bool(first.invariants["ending_cash_nonnegative"]),
        "causality": causality["status"] == "PASS",
    }
    if not all(hard.values()):
        raise RuntimeError(f"Arm {cap} failed hard replay gates: {hard}")
    structural, denials = _summarize(first, risk_rows, admission_rows, cap)
    prefix = "CONTROL_CAP6" if cap == 6 else "TREATMENT_CAP8"
    trace_path = OUT / f"{prefix}_TRACE_V3.json.gz"
    with gzip.open(trace_path, "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(
            {
                "schema": "raw-migration-arm-trace-v3",
                "cap": cap,
                "events": first.events,
                "timestamps": [t.isoformat() for t in first.timestamps],
                "nav_path": first.nav_path,
                "cash_path": first.cash_path,
                "risk_observations": risk_rows,
                "admission_observations": admission_rows,
                "input_identity": identity,
            },
            handle,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    first_hashes = _event_hashes(first)
    first_event_hash = first.event_sha256
    first_final = _digest({"cash": first.cash, "nav": first.ending_nav, "positions": first.positions})
    first_invariants = first.invariants
    del first
    gc.collect()
    second, second_identity, _risk2, _admit2, second_causality = _observe_run(cap)
    repeat = {
        "input_identity": second_identity == identity,
        "event_order": second.event_sha256 == first_event_hash,
        "components": _event_hashes(second) == first_hashes,
        "final_state": _digest({"cash": second.cash, "nav": second.ending_nav, "positions": second.positions})
        == first_final,
        "causality": second_causality["status"] == "PASS",
    }
    if not all(repeat.values()):
        raise RuntimeError(f"Arm {cap} nondeterministic: {repeat}")
    outcome = {
        "schema": "migration-arm-v3",
        "status": "INTEGRITY_PASS",
        "arm_cap": cap,
        "freeze_commit_sha": "51b8fa16c77df60729414da17d20d329480e380c",
        "input_identity": identity,
        "hard_gates": hard,
        "causality": causality,
        "invariants": first_invariants,
        "structural": structural,
        "event_sha256": first_event_hash,
        "component_hashes": first_hashes,
        "final_state_sha256": first_final,
        "trace_file": trace_path.name,
        "trace_gzip_sha256": _sha(trace_path),
        "economics_computed": False,
    }
    (OUT / f"{prefix}_RESULTS_V3.json").write_text(
        json.dumps(outcome, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    (OUT / f"DETERMINISM_RECEIPT_CAP{cap}_V3.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "cap": cap,
                "checks": repeat,
                "event_sha256": first_event_hash,
                "component_hashes": first_hashes,
                "final_state_sha256": first_final,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (OUT / f"{prefix}_SLOT_DENIALS_V3.json").write_text(json.dumps(denials, indent=2, sort_keys=True) + "\n")
    print(f"CAP{cap}_INTEGRITY_PASS event_sha256={first_event_hash} trace={trace_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cap", type=int, choices=(6, 8), required=True)
    args = parser.parse_args()
    run_arm(args.cap)


if __name__ == "__main__":
    main()
