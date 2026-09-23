"""Independently audit frozen migration traces and publish the one-look comparison."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def close(actual: float, expected: float, name: str, tolerance: float = 1e-5) -> None:
    require(math.isclose(actual, expected, rel_tol=1e-10, abs_tol=tolerance), f"{name}: {actual} != {expected}")


def write_json(name: str, value: Any) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_csv(name: str, rows: list[dict[str, Any]]) -> None:
    require(bool(rows), f"empty {name}")
    with (OUT / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def economics(marks: list[dict[str, Any]], structural: dict[str, Any]) -> dict[str, float]:
    daily: dict[str, float] = {}
    peak = 100_000.0
    drawdown = 0.0
    for mark in marks:
        nav = float(mark["nav"])
        peak = max(peak, nav)
        drawdown = min(drawdown, nav / peak - 1)
        daily[mark["timestamp"][:10]] = nav
    values = list(daily.values())
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    elapsed = datetime.fromisoformat(marks[-1]["timestamp"]) - datetime.fromisoformat(marks[0]["timestamp"])
    years = elapsed.total_seconds() / (365 * 24 * 3600)
    cagr = (values[-1] / 100_000) ** (1 / years) - 1
    mean = statistics.mean(returns)
    volatility = statistics.stdev(returns) * math.sqrt(365) if len(returns) > 1 else 0.0
    downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in returns) / (len(returns) - 1)) * math.sqrt(365)
    return {
        "total_return": values[-1] / 100_000 - 1,
        "cagr": cagr,
        "annualized_volatility": volatility,
        "sharpe": mean * 365 / volatility if volatility else 0.0,
        "sortino": mean * 365 / downside if downside else 0.0,
        "max_drawdown": drawdown,
        "calmar": cagr / abs(drawdown) if drawdown else 0.0,
        "ending_nav": values[-1],
        "turnover": float(structural["turnover"]),
        "total_modeled_costs": float(structural["total_modeled_costs"]),
        "average_cash": float(structural["average_cash_daily"]),
        "average_realized_gross": float(structural["average_realized_gross_daily"]),
    }


def audit_arm(cap: int, freeze: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, float]]:
    prefix = "CONTROL_CAP6" if cap == 6 else "TREATMENT_CAP8"
    outcome = json.loads((OUT / f"{prefix}_RESULTS_V3.json").read_text())
    trace_path = OUT / f"{prefix}_TRACE_V3.json.gz"
    with gzip.open(trace_path, "rt") as handle:
        trace = json.load(handle)
    events = trace["events"]
    require(outcome["status"] == "INTEGRITY_PASS" and trace["cap"] == cap, f"arm {cap} state")
    require(outcome["trace_gzip_sha256"] == file_hash(trace_path), f"arm {cap} trace bytes")
    require(trace["input_identity"] == freeze["input_identity"], f"arm {cap} inputs")
    require(digest(events) == outcome["event_sha256"], f"arm {cap} event hash")
    final_event = events[-1]
    require(final_event["event"] == "final_state", f"arm {cap} final event")
    require(
        digest({field: final_event[field] for field in ("cash", "nav", "positions")}) == outcome["final_state_sha256"],
        f"arm {cap} final-state hash",
    )
    types = {
        "decisions": {"decision"},
        "orders": {"reservation_created"},
        "fills": {"fill"},
        "reservations": {"reservation_created", "reservation_released"},
        "positions": {"account_mark", "final_state"},
    }
    components = {key: digest([event for event in events if event["event"] in kinds]) for key, kinds in types.items()}
    components.update(cash_path=digest(trace["cash_path"]), nav_path=digest(trace["nav_path"]))
    require(components == outcome["component_hashes"], f"arm {cap} component hashes")
    marks = [event for event in events if event["event"] == "account_mark"]
    require([event["nav"] for event in marks] == trace["nav_path"], f"arm {cap} NAV marks")
    require([event["cash"] for event in marks] == trace["cash_path"], f"arm {cap} cash marks")
    require([event["timestamp"] for event in marks] == trace["timestamps"], f"arm {cap} mark times")
    cash = 100_000.0
    qty: dict[tuple[str, str], float] = defaultdict(float)
    prices: dict[tuple[str, str], float] = {}
    live_orders: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    costs = notional = 0.0
    max_claims = max_positions = 0
    concentration_by_day: dict[str, dict[str, float]] = {}
    for event in events:
        kind = event["event"]
        counts[kind] += 1
        key = (str(event.get("market")), str(event.get("symbol")))
        if kind == "fill":
            quantity = float(event["qty"])
            side = 1 if event["side"] == "buy" else -1
            qty[key] += side * quantity
            if abs(qty[key]) <= 1e-9:
                qty.pop(key, None)
            cash -= side * quantity * float(event["fill_price"]) + float(event["fee"])
            prices[key] = float(event["reference_price"])
            costs += sum(float(event[field]) for field in ("fee", "slippage_cost", "spread_cost"))
            notional += quantity * float(event["reference_price"])
            require(event["timestamp"] > event["order_created_at"], f"arm {cap} fill causality")
        elif kind == "decision":
            market = event["market"]
            limit = 0.55 if market == "equity" else 0.05
            name_limit = 0.10 if market == "equity" else 0.05
            weights = [float(weight) for _symbol, weight in event["target_weights"]]
            require(sum(weights) <= limit + 1e-8, f"arm {cap} {market} sleeve")
            require(all(0 <= weight <= name_limit + 1e-8 for weight in weights), f"arm {cap} name limit")
        elif kind == "corporate_action":
            cash += float(event["cash_delta"])
            if event["split_ratio"] is not None and key in qty:
                qty[key] *= float(event["split_ratio"])
        elif kind == "mark":
            prices[key] = float(event["price"])
        elif kind == "terminal_liquidation":
            cash += float(event["proceeds"])
            qty.pop(key, None)
            costs += sum(float(event[field]) for field in ("fee", "slippage_cost", "spread_cost"))
        elif kind == "reservation_created":
            order_key = (event["market"], event["client_order_id"])
            require(order_key not in live_orders, f"arm {cap} duplicate reservation")
            live_orders.add(order_key)
        elif kind == "reservation_released":
            order_key = (event["market"], event["client_order_id"])
            require(order_key in live_orders, f"arm {cap} unowned release")
            live_orders.remove(order_key)
        elif kind == "account_mark":
            close(cash, float(event["cash"]), f"arm {cap} cash at {event['timestamp']}")
            require(len(qty) == event["position_count"], f"arm {cap} inventory count")
            require(event["slot_claim_count"] <= cap, f"arm {cap} slot limit")
            require(float(event["target_gross"]) <= 0.6 + 1e-7, f"arm {cap} target gross limit")
            max_claims = max(max_claims, int(event["slot_claim_count"]))
            max_positions = max(max_positions, len(qty))
            values = {key: amount * prices.get(key, 0.0) for key, amount in qty.items()}
            equity_value = sum(value for key, value in values.items() if key[0] == "equity")
            crypto_value = sum(value for key, value in values.items() if key[0] == "crypto")
            nav = float(event["nav"])
            concentration_by_day[event["timestamp"][:10]] = {
                "total_hhi": sum((value / nav) ** 2 for value in values.values()) if nav > 0 else 0.0,
                "equity_hhi": sum((value / equity_value) ** 2 for key, value in values.items() if key[0] == "equity")
                if equity_value > 0
                else 0.0,
                "crypto_hhi": sum((value / crypto_value) ** 2 for key, value in values.items() if key[0] == "crypto")
                if crypto_value > 0
                else 0.0,
                "equity_active": float(equity_value > 0),
                "crypto_active": float(crypto_value > 0),
            }
        elif kind == "final_state":
            close(cash, float(event["cash"]), f"arm {cap} final cash")
            require(not qty and not event["positions"] and not event["pending"], f"arm {cap} final flat")
    require(not live_orders, f"arm {cap} live reservations")
    structural = outcome["structural"]
    require(counts["fill"] == structural["fills"] and counts["decision"] == structural["decisions"], "counts")
    require(max_claims == structural["maximum_effective_slot_claims"], "claim maximum")
    require(max_positions == structural["maximum_actual_positions"], "position maximum")
    close(costs, float(structural["total_modeled_costs"]), "costs")
    close(notional, float(structural["traded_notional"]), "traded notional")
    average_nav = statistics.mean(trace["nav_path"])
    close(notional / average_nav, float(structural["turnover"]), "turnover")
    risk = trace["risk_observations"]
    raw_new = approved_new = count_denied = 0
    count_weight = count_notional = 0.0
    crowdout: Counter[str] = Counter()
    for row in risk:
        existing = {(market, symbol) for market, symbol, _quantity in row["positions"]}
        reserved = {(market, symbol) for market, symbol in row["reserved"]}
        claims = existing | reserved
        raw = {symbol: float(weight) for symbol, weight, _rank in row["raw_target"]}
        approved = {symbol: float(weight) for symbol, weight in row["approved_target"]}
        ranked = {symbol: int(rank) for symbol, _weight, rank in row["raw_target"]}
        priority = (
            sorted(raw, key=lambda symbol: (ranked[symbol] or float("inf"), -raw[symbol], symbol))
            if any(ranked.values())
            else sorted(raw, key=lambda symbol: (-raw[symbol], symbol))
        )
        fixed_current = {symbol for market, symbol in reserved if market == row["market"]}
        available_without_opposite = max(0, min(cap, 2 if row["market"] == "crypto" else cap) - len(fixed_current))
        counterfactual_kept = (fixed_current & set(raw)) | set(
            [symbol for symbol in priority if symbol not in fixed_current][:available_without_opposite]
        )
        opposite = {key for key in claims if key[0] != row["market"]}
        for symbol, weight in raw.items():
            if weight <= 0 or (row["market"], symbol) in existing:
                continue
            raw_new += 1
            if approved.get(symbol, 0.0) > 0:
                approved_new += 1
            elif (
                "daily_loss_limit" not in row["reasons"]
                and "position_count_limit" in row["reasons"]
                and (row["market"] == "equity" or len(claims) >= cap)
            ):
                count_denied += 1
                count_weight += weight
                count_notional += weight * float(row["equity_at_decision"])
                if opposite and symbol in counterfactual_kept:
                    crowdout[f"{'CRYPTO' if row['market'] == 'equity' else 'EQUITY'}_TO_{row['market'].upper()}"] += 1
    require(raw_new == structural["raw_new_symbol_target_openings"], "raw target openings")
    require(approved_new == structural["risk_approved_new_symbol_target_openings"], "approved openings")
    require(count_denied == structural["global_count_denied_target_openings"], "count denied targets")
    close(count_weight, float(structural["count_target_weight_shortfall_sum"]), "count weight")
    close(count_notional, float(structural["count_target_notional_shortfall_sum"]), "count notional")
    require(dict(crowdout) == structural["crowdout_counts"], "cross-market marginal crowd-out")
    admission = trace["admission_observations"]
    require(len(admission) == structural["global_count_denied_order_admissions"], "admission denials")
    denials = json.loads((OUT / f"{prefix}_SLOT_DENIALS_V3.json").read_text())
    require(len(denials) >= count_denied + len(admission), "denial trace completeness")
    daily = list(structural["daily_last_marks"].values())
    require(len(daily) == len({mark["timestamp"][:10] for mark in marks}), "daily mark count")
    for field, source in (("average_cash_daily", "cash"), ("average_realized_gross_daily", "gross")):
        close(statistics.mean(row[source] for row in daily), float(structural[field]), field)
    close(
        statistics.mean(row["total_hhi"] for row in concentration_by_day.values()),
        float(structural["average_hhi_daily"]),
        "daily HHI",
    )
    write_json(
        f"CONCENTRATION_CAP{cap}_V3.json",
        {
            "total_hhi_daily_average": statistics.mean(row["total_hhi"] for row in concentration_by_day.values()),
            "equity_hhi_active_day_average": statistics.mean(
                row["equity_hhi"] for row in concentration_by_day.values() if row["equity_active"]
            ),
            "crypto_hhi_active_day_average": statistics.mean(
                row["crypto_hhi"] for row in concentration_by_day.values() if row["crypto_active"]
            ),
            "equity_active_days": sum(row["equity_active"] for row in concentration_by_day.values()),
            "crypto_active_days": sum(row["crypto_active"] for row in concentration_by_day.values()),
            "measurement_note": "At fill-time account marks the trace does not expose prior non-held source marks; daily final marks are used for sleeve HHI.",
        },
    )
    econ = economics(marks, structural)
    require(all(outcome["hard_gates"].values()), f"arm {cap} hard gates")
    require(json.loads((OUT / f"DETERMINISM_RECEIPT_CAP{cap}_V3.json").read_text())["status"] == "PASS", "repeat")
    return outcome, denials, econ


def main() -> None:
    freeze = json.loads((OUT / "FROZEN_MIGRATION_SPEC_V3.json").read_text())
    receipt = json.loads((OUT / "PRE_OUTCOME_FREEZE_RECEIPT_V3.json").read_text())
    freeze_sha = receipt["freeze_commit_sha"]
    require(
        subprocess.run(["git", "merge-base", "--is-ancestor", freeze_sha, "HEAD"], cwd=ROOT).returncode == 0,
        "freeze ancestry",
    )
    require(file_hash(OUT / "FROZEN_MIGRATION_SPEC_V3.json") == receipt["freeze_spec_sha256"], "freeze bytes")
    tree = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", freeze_sha], cwd=ROOT, text=True)
    require(
        "CONTROL_CAP6_RESULTS_V3.json" not in tree and "TREATMENT_CAP8_RESULTS_V3.json" not in tree,
        "pre-outcome freeze",
    )
    require(freeze["arms"] == {"A": {"maximum_positions": 6}, "B": {"maximum_positions": 8}}, "single variable")
    require(
        json.loads((OUT / "PHYSICAL_REPLAY_AUTHORITY_RECEIPT.json").read_text())["status"] == "PASS",
        "physical authority",
    )
    require(
        json.loads((OUT / "CRYPTO_CANONICAL_AUTHORITY_RECEIPT.json").read_text())["status"] == "PASS",
        "crypto authority",
    )
    for path, expected in freeze["direct_source_sha256"].items():
        require(file_hash(ROOT / path) == expected, f"source {path}")
    require(file_hash(ROOT / "config/settings.yaml") == freeze["config_sha256"], "configuration")
    results = {cap: audit_arm(cap, freeze) for cap in (6, 8)}
    a, b = results[6][0]["structural"], results[8][0]["structural"]
    count_a = a["global_count_denied_target_openings"] + a["global_count_denied_order_admissions"]
    count_b = b["global_count_denied_target_openings"] + b["global_count_denied_order_admissions"]
    structural_pass = count_a > 0 and count_b < count_a
    write_json(
        "INDEPENDENT_MIGRATION_VERIFICATION_V3.json",
        {
            "schema": "independent-migration-verification-v3",
            "status": "PASS",
            "freeze_commit_sha": freeze_sha,
            "trace_based_checks": [
                "event/component hashes",
                "ledger cash",
                "inventory counts",
                "slot/gross",
                "orders/fills/reservations",
                "direct target denials",
                "costs/turnover",
                "daily NAV economics",
            ],
            "cap6_event_sha256": results[6][0]["event_sha256"],
            "cap8_event_sha256": results[8][0]["event_sha256"],
            "structural_gate_pass": structural_pass,
            "count_denials": {"6": count_a, "8": count_b},
            "limitation": "Event trace omits non-held source marks; independent NAV valuation and per-name HHI at fill-time marks cannot be reconstructed from trace alone. Replay internal NAV reconciliation and daily source mark trace remain separate gates.",
        },
    )
    if not structural_pass:
        decision = "ACCOUNT_ARCHITECTURE_MIGRATION_8_REJECTED"
    else:
        decision = "ACCOUNT_ARCHITECTURE_MIGRATION_8_ACCEPTED"
    write_json(
        "MIGRATION_DECISION_V3.json",
        {
            "decision": decision,
            "structural_gate": "PASS" if structural_pass else "FAIL",
            "count_denials_cap6": count_a,
            "count_denials_cap8": count_b,
            "next_action": "PROCEED_TO_PRODUCTION_PARITY_CHANGE_REVIEW" if structural_pass else "RETAIN_GLOBAL_CAP_6",
            "production_maximum_positions_changed": False,
        },
    )
    for name, fields in {
        "STRUCTURAL_CAPACITY_COMPARISON_V3.csv": [
            "decisions",
            "equity_decisions",
            "crypto_decisions",
            "raw_new_symbol_target_openings",
            "risk_approved_new_symbol_target_openings",
            "global_count_denied_target_openings",
            "global_count_denied_order_admissions",
            "count_target_notional_shortfall_sum",
            "timestamps_at_global_cap",
            "average_positions_daily",
            "average_equity_positions_daily",
            "average_crypto_positions_daily",
            "maximum_effective_slot_claims",
        ],
        "EXPOSURE_COMPARISON_V3.csv": [
            "target_weight_requested_sum",
            "target_weight_approved_sum",
            "average_realized_gross_daily",
            "maximum_realized_gross",
            "maximum_target_gross",
            "average_cash_daily",
        ],
        "CONCENTRATION_COMPARISON_V3.csv": [
            "average_hhi_daily",
            "maximum_total_account_hhi",
            "maximum_actual_name_weight",
            "maximum_name_target_weight",
            "average_positions_daily",
        ],
    }.items():
        write_csv(
            name,
            [
                {"metric": key, "cap6": a[key], "cap8": b[key], "difference_cap8_minus_cap6": b[key] - a[key]}
                for key in fields
            ],
        )
    concentration = {cap: json.loads((OUT / f"CONCENTRATION_CAP{cap}_V3.json").read_text()) for cap in (6, 8)}
    with (OUT / "CONCENTRATION_COMPARISON_V3.csv").open("a", newline="") as handle:
        writer = csv.writer(handle)
        for key in (
            "equity_hhi_active_day_average",
            "crypto_hhi_active_day_average",
            "equity_active_days",
            "crypto_active_days",
        ):
            left, right = concentration[6][key], concentration[8][key]
            writer.writerow((key, left, right, right - left))
    write_csv("SLOT_DENIAL_ATTRIBUTION_V3.csv", [{"cap": cap, **row} for cap in (6, 8) for row in results[cap][1]])
    write_json("ECONOMIC_METRICS_V3.json", {"cap6": results[6][2], "cap8": results[8][2]})
    print(decision, "count_denials", count_a, count_b)


if __name__ == "__main__":
    main()
