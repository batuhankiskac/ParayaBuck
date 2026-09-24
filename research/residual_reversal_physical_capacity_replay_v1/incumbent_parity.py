"""No-return parity gate for the incumbent-only physical scheduler."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import yaml

from parayabuck.core.models import Market

from .build_inputs import ROOT, build_replay, crypto_input, equity_input, frozen_config
from .physical_scheduler import SharedAccountReplay

OUT = Path(__file__).resolve().parent
V2 = "11890e93ad1a5223cb11483488deb4d5a6aec3d3"
CRYPTO_CAUSAL = "b135d73743ec2652f79b8c840df50bd3f67166e8"
V2_DIR = "research/physical_availability_clock_v2/"


def _tagged_json(path: str) -> dict[str, Any]:
    raw = subprocess.check_output(["git", "show", f"{V2}:{V2_DIR}{path}"], cwd=ROOT)
    value: dict[str, Any] = json.loads(raw)
    return value


def _tagged_config() -> dict[str, Any]:
    raw = subprocess.check_output(["git", "show", f"{V2}:config/settings.yaml"], cwd=ROOT)
    value: dict[str, Any] = yaml.safe_load(raw)
    return value


def static_identity() -> dict[str, Any]:
    expected = _tagged_config()
    current: dict[str, Any] = yaml.safe_load((ROOT / "config/settings.yaml").read_text(encoding="utf-8"))
    changed = []
    for section in sorted(set(expected) | set(current)):
        left, right = expected.get(section), current.get(section)
        if section == "risk" and isinstance(left, dict) and isinstance(right, dict):
            left, right = dict(left), dict(right)
            left["max_positions"] = right["max_positions"] = 6
        if left != right:
            changed.append(section)
    authority_timing = subprocess.check_output(["git", "show", f"{V2}:{V2_DIR}timing.py"], cwd=ROOT)
    timing_match = (
        hashlib.sha256(authority_timing).hexdigest() == hashlib.sha256((OUT / "timing.py").read_bytes()).hexdigest()
    )
    config = frozen_config(6)
    return {
        "config_sections_differing_after_cap6_normalization": changed,
        "timing_byte_identical": timing_match,
        "equity_exclusions": list(config.universe.equity.ordinary_alpha_excluded_symbols),
    }


def compare(authority: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Compare only structural fields; never read authority economic fields."""
    fields = (
        "event_order_sha256",
        "fill_count",
        "future_data_access_checks",
        "maximum_observed_position_count",
        "maximum_observed_slot_claim_count",
        "over_cap_timestamp_count",
        "reservation_created",
        "reservation_released",
    )
    return [name for name in fields if authority[name] != actual.get(name)]


def terminal_count(events: tuple[dict[str, Any], ...]) -> int:
    return sum(row["event"] in {"terminal_liquidation", "final_liquidation"} for row in events)


def run() -> dict[str, Any]:
    combined_v2 = _tagged_json("COMBINED_CAP6_PHYSICAL_INVARIANTS_V2.json")
    equity_v2 = _tagged_json("EQUITY_PHYSICAL_PARITY_V2.json")
    crypto_v2 = _tagged_json("CRYPTO_PHYSICAL_PARITY_V2.json")
    identity = static_identity()
    status: dict[str, Any] = {
        "schema": "residual-reversal-incumbent-physical-parity-v1",
        "physical_v2_commit": V2,
        "corrected_crypto_commit": CRYPTO_CAUSAL,
        "tagged_crypto_reference_commit_match": crypto_v2["reference_commit"] == CRYPTO_CAUSAL,
        "tagged_crypto_causal_status": crypto_v2["status"],
        "tagged_equity_physical_status": equity_v2["status"],
        "static_identity": identity,
    }
    if identity["config_sections_differing_after_cap6_normalization"] or not identity["timing_byte_identical"]:
        status.update(status="BLOCKED_STATIC_IDENTITY", differences=["config_or_timing"])
        return status
    if (
        crypto_v2["status"] != "PASS"
        or equity_v2["status"] != "PASS"
        or not status["tagged_crypto_reference_commit_match"]
    ):
        status.update(status="BLOCKED_TAGGED_AUTHORITY", differences=["tagged_incumbent_authority"])
        return status

    config = frozen_config(6)
    isolated: dict[str, dict[str, Any]] = {}
    for market in (Market.CRYPTO, Market.EQUITY):
        replay = SharedAccountReplay(
            starting_nav=config.backtest.initial_cash,
            maximum_positions=6,
            allow_test_strategies=True,
        )
        replay_input, source = (
            crypto_input(config, replay) if market == Market.CRYPTO else equity_input(config, replay, "incumbent")
        )
        if market == Market.EQUITY and replay_input.engine.ordinary_alpha_excluded_symbols != tuple(
            identity["equity_exclusions"]
        ):
            status.update(status="BLOCKED_ENGINE_IDENTITY", differences=["equity_exclusions"])
            return status
        replay.add_market(replay_input)
        result = replay.run()
        events = result.events
        observed = {
            "decision_count": sum(row["event"] == "decision" for row in events),
            "fill_count": sum(row["event"] == "fill" for row in events),
            "terminal_event_count": terminal_count(events),
            "source_sha256": source["source_sha256"],
            "universe_sha256": source["universe_sha256"],
        }
        authority = crypto_v2 if market == Market.CRYPTO else equity_v2
        expected_count = (
            authority["target_count"]
            if market == Market.CRYPTO
            else authority["matched_counts"]["target_decision_count"]
        )
        observed["checks"] = {
            "decision_count": observed["decision_count"] == expected_count,
            "fill_count": observed["fill_count"]
            == (authority["fill_count"] if market == Market.CRYPTO else authority["matched_counts"]["fill_count"]),
            "universe_identity": observed["universe_sha256"] == authority["input_identity"]["universe_sha256"],
            "source_identity": observed["source_sha256"]
            == authority["input_identity"]["hourly_bars_sha256" if market == Market.CRYPTO else "bars_sha256"],
        }
        if market == Market.EQUITY:
            observed["checks"]["terminal_event_count"] = (
                observed["terminal_event_count"] == authority["matched_counts"]["terminal_event_count"]
            )
        isolated[market.value] = observed
        del replay_input, replay, result, events
        gc.collect()
    status["isolated"] = isolated

    replay, sources = build_replay(6, "incumbent")
    result = replay.run()
    events = result.events
    invariants = result.invariants
    combined = {
        "event_order_sha256": replay.internal_event_sha256,
        "fill_count": sum(row["event"] == "fill" for row in events),
        "future_data_access_checks": invariants["future_data_access_checks"],
        "maximum_observed_position_count": invariants["max_observed_position_count"],
        "maximum_observed_slot_claim_count": invariants["max_observed_slot_claim_count"],
        "over_cap_timestamp_count": invariants["slot_claim_violation_count"],
        "reservation_created": invariants["reservation_create_count"],
        "reservation_released": invariants["reservation_release_count"],
    }
    status["combined"] = combined
    status["combined_input_identity"] = sources
    differences = compare(combined_v2, combined)
    for market_name, raw_source_identity in sources.items():
        source_identity = cast(dict[str, Any], raw_source_identity)
        reference = combined_v2["input_identity"][market_name]
        if source_identity["universe_sha256"] != reference["universe_sha256"]:
            differences.append(f"{market_name}_universe_identity")
        reference_key = "hourly_bars_sha256" if market_name == "crypto" else "bars_sha256"
        if source_identity["source_sha256"] != reference[reference_key]:
            differences.append(f"{market_name}_source_identity")
    if not all(all(item.values()) for item in (row["checks"] for row in isolated.values())):
        differences.append("isolated_incumbent_structural_parity")
    status["differences"] = differences
    status["status"] = "PASS" if not differences else "FAIL"
    return status


def recheck_equity_terminal() -> dict[str, Any]:
    """Recheck one receipt-counting error without rerunning the exact combined proof."""
    receipt_path = OUT / "INCUMBENT_PHYSICAL_PARITY_RECEIPT.json"
    receipt: dict[str, Any] = json.loads(receipt_path.read_text(encoding="utf-8"))
    authority = _tagged_json("COMBINED_CAP6_PHYSICAL_INVARIANTS_V2.json")
    equity_v2 = _tagged_json("EQUITY_PHYSICAL_PARITY_V2.json")
    if compare(authority, receipt["combined"]) or not all(receipt["isolated"]["crypto"]["checks"].values()):
        raise ValueError("Existing combined or crypto parity proof is not valid")
    existing_equity = receipt["isolated"]["equity"]
    if not all(value for key, value in existing_equity["checks"].items() if key != "terminal_event_count"):
        raise ValueError("Existing isolated equity proof has another difference")
    config = frozen_config(6)
    replay = SharedAccountReplay(
        starting_nav=config.backtest.initial_cash,
        maximum_positions=6,
        allow_test_strategies=True,
    )
    replay_input, source = equity_input(config, replay, "incumbent")
    replay.add_market(replay_input)
    events = replay.run().events
    observed = {
        "decision_count": sum(row["event"] == "decision" for row in events),
        "fill_count": sum(row["event"] == "fill" for row in events),
        "terminal_event_count": terminal_count(events),
        "source_sha256": source["source_sha256"],
        "universe_sha256": source["universe_sha256"],
    }
    checks = {
        "decision_count": observed["decision_count"] == equity_v2["matched_counts"]["target_decision_count"],
        "fill_count": observed["fill_count"] == equity_v2["matched_counts"]["fill_count"],
        "terminal_event_count": observed["terminal_event_count"] == equity_v2["matched_counts"]["terminal_event_count"],
        "source_identity": observed["source_sha256"] == equity_v2["input_identity"]["bars_sha256"],
        "universe_identity": observed["universe_sha256"] == equity_v2["input_identity"]["universe_sha256"],
    }
    observed["checks"] = checks
    receipt["isolated"]["equity"] = observed
    receipt["correction_note"] = (
        "Original receipt counted only inactive-entity terminal_liquidation events (2); "
        "tagged V2 terminal_event_count also includes final_liquidation events. "
        "Only isolated V3 was rerun; the prior exact combined hash and crypto checks were reused."
    )
    receipt["differences"] = [] if all(checks.values()) else ["isolated_equity_structural_parity"]
    receipt["status"] = "PASS" if not receipt["differences"] else "FAIL"
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="run incumbent-only physical replays")
    parser.add_argument(
        "--equity-terminal-recheck", action="store_true", help="rerun isolated V3 after terminal-count correction"
    )
    args = parser.parse_args()
    if args.execute and args.equity_terminal_recheck:
        parser.error("Choose one execution mode")
    receipt = (
        run()
        if args.execute
        else recheck_equity_terminal()
        if args.equity_terminal_recheck
        else {"status": "NOT_RUN", "static_identity": static_identity()}
    )
    (OUT / "INCUMBENT_PHYSICAL_PARITY_RECEIPT.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": receipt["status"], "differences": receipt.get("differences", [])}, sort_keys=True))


if __name__ == "__main__":
    main()
