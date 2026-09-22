"""Offline comparison of archived and current crypto replay ledgers."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent


def load(name: str) -> dict[str, Any]:
    with gzip.open(ROOT / (name + ".json.gz"), "rt") as handle:
        return cast(dict[str, Any], json.load(handle))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def first_list(left: list[Any], right: list[Any]) -> dict[str, Any] | None:
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        if a != b:
            return {"index": index, "historical": a, "current": b}
    return None


def keyed_diff(left: list[dict[str, Any]], right: list[dict[str, Any]], key: str, fields: list[str]) -> dict[str, Any]:
    lm = {row[key]: row for row in left}
    rm = {row[key]: row for row in right}
    result: dict[str, Any] = {}
    for field in fields:
        changes = []
        for timestamp in sorted(lm.keys() | rm.keys()):
            a = lm.get(timestamp, {}).get(field)
            b = rm.get(timestamp, {}).get(field)
            if a != b:
                changes.append({"timestamp": timestamp, "historical": a, "current": b})
        result[field] = {"mismatch_count": len(changes), "first": changes[0] if changes else None}
    result["missing_timestamps"] = {
        "historical_only": sorted(lm.keys() - rm.keys()),
        "current_only": sorted(rm.keys() - lm.keys()),
    }
    return result


def economic_target(row: dict[str, Any], field: str) -> dict[str, Any]:
    value = row.get(field, {})
    return {
        "weights": value.get("weights"),
        "cash_weight": value.get("cash_weight"),
        "risk_multiplier": value.get("risk_multiplier"),
        "retained": value.get("metadata", {}).get("retained"),
        "selected_symbols": value.get("metadata", {}).get("selected_symbols"),
    }


def compare(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    decision_fields = [
        "eligible_symbols",
        "history",
        "scores",
        "holdings",
        "alpha_economic",
        "target_economic",
        "orders",
        "account",
    ]
    decisions = []
    for source in (left, right):
        decisions.append(
            [
                {
                    **row,
                    "alpha_economic": economic_target(row, "alpha_target"),
                    "target_economic": economic_target(row, "target"),
                }
                for row in source["decisions"]
            ]
        )
    universe_rows = [
        [
            {**row, "observation_membership": sorted(row.get("observation_set", []))}
            for row in source["universe"]["snapshots"]
        ]
        for source in (left, right)
    ]
    universe = keyed_diff(
        universe_rows[0],
        universe_rows[1],
        "effective_at",
        ["symbols", "observation_set", "observation_membership", "score_breakdown", "eliminated"],
    )
    common_times = {row["timestamp"] for row in decisions[0]} & {row["timestamp"] for row in decisions[1]}
    common_decisions = [[row for row in rows if row["timestamp"] in common_times] for rows in decisions]
    common_diff = keyed_diff(
        common_decisions[0], common_decisions[1], "timestamp", [f for f in decision_fields if f != "history"]
    )
    economic_fills = [
        [{key: value for key, value in row.items() if key != "client_order_id"} for row in source["fills"]]
        for source in (left, right)
    ]
    common_universe_times = {row["effective_at"] for row in universe_rows[0]} & {
        row["effective_at"] for row in universe_rows[1]
    }
    filtered_universe = [
        [
            row
            for row in rows
            if row["effective_at"] in common_universe_times and row["effective_at"] >= left["window"]["start"]
        ]
        for rows in universe_rows
    ]
    common_universe = keyed_diff(
        filtered_universe[0],
        filtered_universe[1],
        "effective_at",
        ["symbols", "observation_set", "observation_membership", "score_breakdown", "eliminated"],
    )
    decisions_diff = keyed_diff(decisions[0], decisions[1], "timestamp", decision_fields)
    # Full histories are already persisted in compressed replay files; reference them by identity.
    for field in ["history", "account"]:
        row = decisions_diff[field]["first"]
        if row:
            row["historical_sha256"] = digest(row.pop("historical"))
            row["current_sha256"] = digest(row.pop("current"))
    return {
        "data_identical": left["data"] == right["data"],
        "normalized_daily_bars_identical": left["daily_bars"] == right["daily_bars"],
        "window_identical": left["window"] == right["window"],
        "engine_arguments_identical": left["engine"] == right["engine"],
        "historical_universe_timeframe": left["universe_timeframe"],
        "current_universe_timeframe": right["universe_timeframe"],
        "universe": universe,
        "common_window_universe": common_universe,
        "decisions": decisions_diff,
        "common_timestamp_decisions": common_diff,
        "first_economic_fill_difference": first_list(economic_fills[0], economic_fills[1]),
        "first_fill_difference": first_list(left["fills"], right["fills"]),
        "first_snapshot_difference": first_list(left["snapshots"], right["snapshots"]),
        "historical_metrics": left["metrics"],
        "current_metrics": right["metrics"],
        "terminal_event_counts": [len(left["terminal_events"]), len(right["terminal_events"])],
    }


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            result.update(flatten(item, f"{prefix}.{key}" if prefix else key))
        return result
    return {prefix: value}


def main() -> None:
    historical = load("HISTORICAL_REPLAY")
    current = load("CURRENT_REPLAY_A")
    hourly = load("CURRENT_HOURLY_REPLAY")
    comparisons = {
        "historical_vs_current_same_hourly_adapter": compare(historical, hourly),
        "current_hourly_vs_current_daily_adapter": compare(hourly, current),
        "historical_vs_current_daily_contract": compare(historical, current),
    }
    audit = {
        "schema": "crypto-first-divergence-v1",
        "canonical_comparison": "historical_vs_current_same_hourly_adapter",
        "causal_chain": [
            {
                "timestamp": "2021-04-01T00:00:00+00:00",
                "stage": "context_visibility",
                "finding": "Current context removes noneligible MKR. Inputs and all ranked scores still match; no economic divergence.",
            },
            {
                "timestamp": "2022-09-17T00:00:00+00:00",
                "stage": "observation_membership",
                "finding": "First actual snapshot observation membership change: UNI absent current; eligible pools and scanner scores unchanged.",
            },
            {
                "timestamp": "2023-03-28T00:00:00+00:00",
                "stage": "effective_context_eligibility",
                "finding": "SHIB eligible in same PIT universe but absent current capacity-limited context; both score lists still match because SHIB lacks qualifying observations here.",
            },
            {
                "timestamp": "2023-04-09T00:00:00+00:00",
                "stage": "ranked_candidates",
                "finding": "Historical SHIB volatility .024949160469622127 ranks third; current omits SHIB. Same-symbol input histories and variance formula unchanged.",
            },
            {
                "timestamp": "2023-05-11T00:00:00+00:00",
                "stage": "target_and_order",
                "finding": "Historical SHIB ranks first (.018930346236856514): BTC+SHIB target. Current BTC+ETH. First economically different targets/orders.",
            },
            {
                "timestamp": "2023-05-12T00:00:00+00:00",
                "stage": "fills_costs_positions_nav",
                "finding": "First different buy is SHIB versus ETH; all subsequent state/cost/NAV differences descend from the changed target.",
            },
        ],
        "source_change": {
            "context_visibility_commit": "ac58f34c3d9242150f28c59aedf69a60b6992ab9",
            "observation_identity_commits": [
                "352b6a0bc1568ce840b9c8cf7ccf7eeb9d2975f6",
                "5175a58b2fb4084e5c5a00854d3c3c94b0e819a1",
            ],
            "proof": "ac58f34 changes _context to filter history through _effective_observation_symbols and changes completeness gate to same effective context.",
        },
        "interpretation": {
            "primary": "Historical broad-context backtest predates current runtime-sized observation contract.",
            "unchanged": [
                "dataset bytes",
                "normalized daily bars",
                "common window",
                "hourly scanner eligible pools",
                "hourly scanner scores",
                "low variance formula",
                "retention rule",
                "cost configuration",
                "isolated account mode",
            ],
            "daily_adapter": "Separate noncanonical comparison; switching 1h scanner input to1d alters eligibility and is not used to force parity.",
            "no_downstream_independent_bug_claim": True,
        },
        "comparisons": comparisons,
    }
    (ROOT / "FIRST_DIVERGENCE_AUDIT.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    (ROOT / "UNIVERSE_DIFF.json").write_text(
        json.dumps({name: value["universe"] for name, value in comparisons.items()}, indent=2, sort_keys=True) + "\n"
    )
    a, b = flatten(historical["config"]), flatten(current["config"])
    config = {
        "historical_config_sha256": historical["config_sha256"],
        "current_config_sha256": current["config_sha256"],
        "differences": [
            {"field": key, "historical": a.get(key), "current": b.get(key)}
            for key in sorted(a.keys() | b.keys())
            if a.get(key) != b.get(key)
        ],
        "explicit_historical_strategy_override": historical["decisions"][0]["alpha_target"]["parameters"],
        "current_strategy_parameters": current["decisions"][0]["alpha_target"]["parameters"],
        "engine_arguments": current["engine"],
        "engine_arguments_identical": historical["engine"] == current["engine"],
        "historical_config_strategy_name_is_not_instantiated_strategy": True,
    }
    (ROOT / "CONFIG_DIFF.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    for name, row in comparisons.items():
        print(name)
        print(
            "universe",
            {key: value.get("first") for key, value in row["universe"].items() if key != "missing_timestamps"},
        )
        print(
            "decisions",
            {
                key: value.get("first", {}).get("timestamp") if value.get("first") else None
                for key, value in row["decisions"].items()
                if key != "missing_timestamps"
            },
        )
        print("fill", row["first_fill_difference"])


if __name__ == "__main__":
    main()
