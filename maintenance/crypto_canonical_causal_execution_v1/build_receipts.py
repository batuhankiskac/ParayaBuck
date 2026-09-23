#!/usr/bin/env python3
"""Build reproducible comparison receipts from frozen old and causal controls."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from maintenance.crypto_canonical_causal_execution_v1.run_causal import canonical_bytes, sha  # noqa: E402
from maintenance.crypto_canonical_causal_execution_v1.verify_causal import verify  # noqa: E402

BASE = Path(__file__).resolve().parent
OLD_FILL_SHA = "c8bf27f1d21846bf754a53a72e5da29416c79dd817162606cecc6ce19fe1e07b"


def read(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return cast(dict[str, Any], json.load(handle))


def write(name: str, value: dict[str, Any]) -> None:
    (BASE / name).write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def first_difference(old: list[dict[str, Any]], new: list[dict[str, Any]], old_key: str, new_key: str) -> Any:
    for index, (left, right) in enumerate(zip(old, new, strict=False)):
        if left[old_key] != right[new_key]:
            return {"index": index, "old_label": left["timestamp"], "corrected_label": right["label_time"]}
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", type=Path, required=True)
    args = parser.parse_args()
    old = read(args.old)
    a_path = BASE / "CORRECTED_REPLAY_A.json.gz"
    b_path = BASE / "CORRECTED_REPLAY_B.json.gz"
    a_bytes, b_bytes = a_path.read_bytes(), b_path.read_bytes()
    if a_bytes != b_bytes:
        raise AssertionError("Repeated gzip outputs differ")
    new = read(a_path)
    if old["hashes"]["fills"] != OLD_FILL_SHA or sha(old["daily_bars"]) != sha(new["daily_bars"]):
        raise AssertionError("Frozen control/input mismatch")
    if old["universe_hash"] != new["universe_sha256"]:
        raise AssertionError("PIT universe identity changed")
    checks = verify(new)
    old_decisions, new_decisions = old["decisions"], new["decisions"]
    common = list(zip(old_decisions, new_decisions, strict=False))
    if any(x["timestamp"] != y["label_time"] for x, y in common):
        raise AssertionError("Original decision label cadence changed")
    eligibility_equal = sum(x["eligible_symbols"] == y["eligible_symbols"] for x, y in common)
    rank_equal = sum(x["scores"] == y["scores"] for x, y in common)
    alpha_equal = sum(x["alpha_target"]["weights"] == y["alpha_target"]["weights"] for x, y in common)
    target_equal = sum(x["target"]["weights"] == y["target"]["weights"] for x, y in common)
    signal = {
        "classification": "SIGNAL_SEMANTICS_CHANGED",
        "meaning": "Outputs changed via execution-dependent holdings and observation pins; alpha formula and parameters did not change.",
        "daily_bar_sha256": new["hashes"]["daily_bars"],
        "old_universe_sha256": old["universe_hash"],
        "corrected_universe_sha256": new["universe_sha256"],
        "old_decisions": len(old_decisions),
        "corrected_decisions": len(new_decisions),
        "common_decision_labels": len(common),
        "equal_eligible_observation_contexts": eligibility_equal,
        "equal_raw_rank_lists": rank_equal,
        "equal_alpha_target_weights": alpha_equal,
        "equal_risk_adjusted_target_weights": target_equal,
        "first_rank_difference": first_difference(old_decisions, new_decisions, "scores", "scores"),
        "first_alpha_target_difference": next(
            (
                {
                    "label": x["timestamp"],
                    "old_selected": x["alpha_target"]["metadata"].get("selected_symbols"),
                    "corrected_selected": y["alpha_target"]["metadata"].get("selected_symbols"),
                    "old_holdings": x["holdings"],
                    "corrected_holdings": y["holdings"],
                }
                for x, y in common
                if x["alpha_target"]["weights"] != y["alpha_target"]["weights"]
            ),
            None,
        ),
        "terminal_window_note": "One fewer decision because the source-common terminal boundary is 2026-07-31T00:00Z.",
    }
    write("SIGNAL_PARITY_AUDIT.json", signal)
    old_btc_eth = [fill for fill in old["fills"] if fill["decision_timestamp"].startswith("2021-04-01")]
    new_btc_eth = [fill for fill in new["fills"] if fill["decision_timestamp"].startswith("2021-04-01")]
    by_order = Counter(fill["client_order_id"] for fill in new["fills"])
    divergence = {
        "first_decision_label": "2021-04-01T00:00:00+00:00",
        "complete_daily_signal_available_at": "2021-04-02T00:00:00+00:00",
        "old_order_created_at": old_decisions[0]["orders"][0]["created_at"],
        "corrected_order_created_at": new["orders"][0]["created_at"],
        "old_first_fills": old_btc_eth,
        "corrected_first_fills": new_btc_eth,
        "corrected_source_events": [
            event
            for event in new["price_events"]
            if event["client_order_id"] in {fill["client_order_id"] for fill in new_btc_eth}
        ],
        "first_divergence": "The old control fills BTC and ETH at Apr 2 00:00 opening prices; corrected orders cannot exist before that boundary and fill at Apr 2 01:00 hourly opens.",
        "old_fill_count": len(old["fills"]),
        "corrected_fill_count": len(new["fills"]),
        "corrected_orders_with_multiple_fills": sum(count > 1 for count in by_order.values()),
        "corrected_orders_with_one_fill": sum(count == 1 for count in by_order.values()),
    }
    write("FIRST_EXECUTION_DIVERGENCE_AUDIT.json", divergence)
    payload_hash = hashlib.sha256(canonical_bytes(new)).hexdigest()
    hashes = {
        "gzip_sha256": hashlib.sha256(a_bytes).hexdigest(),
        "full_payload_sha256": payload_hash,
        "sections": new["hashes"],
    }
    if any(sha(new[key]) != digest for key, digest in new["hashes"].items()):
        raise AssertionError("Payload section hash mismatch")
    write(
        "INDEPENDENT_CAUSALITY_VERIFICATION.json",
        {
            **checks,
            "repeat_byte_identical": True,
            "hashes": hashes,
            "old_control_fill_sha256": OLD_FILL_SHA,
            "old_control_full_payload_sha256": hashlib.sha256(gzip.decompress(args.old.read_bytes())).hexdigest(),
            "freeze_commit": new["contract_git_sha"],
        },
    )
    write(
        "CANONICAL_BASELINE_DECISION.json",
        {
            "decision": "CRYPTO_CANONICAL_BASELINE_CAUSAL_REPAIR_ACCEPTED",
            "production_classification": "PRODUCTION_CAUSAL_HISTORICAL_FILL_PROXY_ONLY_BROKEN",
            "old_classification": "CRYPTO_CANONICAL_BASELINE_V2_SUPERSEDED_BY_CAUSAL_EXECUTION_REPAIR",
            "new_control_full_payload_sha256": payload_hash,
            "new_control_gzip_sha256": hashes["gzip_sha256"],
            "new_control_section_hashes": new["hashes"],
            "next_action": "RESTART_PHYSICAL_AVAILABILITY_CLOCK_WITH_CORRECTED_CRYPTO_BASELINE",
        },
    )
    write(
        "PHYSICAL_CLOCK_HANDOFF_RECEIPT.json",
        {
            "status": "RESTART_PHYSICAL_AVAILABILITY_CLOCK_WITH_CORRECTED_CRYPTO_BASELINE",
            "blocked_clock_commit": "377d260835c5a5f8ea23b61e16bf8103a842b0e0",
            "source_sha256": new["source_sha256"],
            "contract_git_sha": new["contract_git_sha"],
            "daily_available_at": "daily label + 24 hours after all 24 source hours complete",
            "decision_at": "daily_available_at on original 96h label cadence",
            "new_order_execution": "first same-symbol hourly open strictly later than both daily boundary and order creation",
            "terminal_at": new["terminal_at"],
            "counts": new["counts"],
            "control_hashes": hashes,
            "required_next_steps": [
                "fresh equity physical-clock parity",
                "fresh corrected crypto parity",
                "combined cap-6 shared-account replay",
                "determinism and account-ledger verification",
                "only then reconsider cap-8 migration prerequisites",
            ],
        },
    )
    print(json.dumps({"status": "PASS", "hashes": hashes, "signal": signal}, sort_keys=True))


if __name__ == "__main__":
    main()
