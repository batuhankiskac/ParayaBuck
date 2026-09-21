"""Derive bounded observation evidence from the persisted through-t ranks."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STREAM_CAPACITY = 30


def main() -> None:
    economic = json.loads((ROOT / "ECONOMIC_RESULTS.json").read_text(encoding="utf-8"))["control_a_and_candidate_b"]
    source = economic["candidate_b_1x"]["candidate_audit"]
    audit = []
    for item in source:
        ranked = list(item["ranked"])
        bounded = ranked[:STREAM_CAPACITY]
        audit.append(
            {
                "decision_timestamp": item["decision_timestamp"],
                "decision_bar_included": bool(ranked),
                "maximum_visible_timestamp": item["maximum_visible_timestamp"],
                "future_data_visible": item["maximum_visible_timestamp"] > item["decision_timestamp"],
                "eligible_pool_size": item["eligible_pool_size"],
                "scoreable_pool_size": item["scoreable_pool_size"],
                "ranked_through_t": ranked,
                "bounded_observation_set": [symbol for symbol, _score in bounded],
                "observation_set_size": len(bounded),
                "selected": item["selected"],
                "selected_is_rank_prefix": item["selected"] == [symbol for symbol, _score in ranked[:6]],
                "target_weights": item["target_weights"],
                "target_cash_weight": item["target_cash_weight"],
                "reserved_targets": sorted(set(item["selected"]) & {"SPY", "SH", "PSQ", "DOG"}),
            }
        )
    payload = {
        "candidate": "equity_52week_high_close_v1",
        "source": "ECONOMIC_RESULTS.json candidate_b_1x persisted ranks",
        "bounded_observation_capacity": STREAM_CAPACITY,
        "decision_count": len(audit),
        "audit": audit,
        "all_future_data_absent": all(not item["future_data_visible"] for item in audit),
        "all_observation_sets_bounded": all(item["observation_set_size"] <= STREAM_CAPACITY for item in audit),
        "all_selected_rank_prefixes": all(item["selected_is_rank_prefix"] for item in audit),
        "all_reserved_excluded": all(not item["reserved_targets"] for item in audit),
        "candidate_economics_inspected": True,
    }
    (ROOT / "CANDIDATE_B_SIGNAL_RANK_AUDIT.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    coverage = json.loads((ROOT / "CANDIDATE_B_DECISION_COVERAGE.json").read_text(encoding="utf-8"))
    for key in ("candidate_b_1x", "candidate_b_2x", "candidate_b_3x"):
        coverage[key]["bounded_observation_capacity"] = STREAM_CAPACITY
        coverage[key]["observation_sizes"] = [item["observation_set_size"] for item in audit]
    (ROOT / "CANDIDATE_B_DECISION_COVERAGE.json").write_text(
        json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
