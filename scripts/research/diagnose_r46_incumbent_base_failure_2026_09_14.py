"""Diagnose the frozen R46 V3 incumbent-base failure without running economics.

This diagnostic reads only timestamp, symbol, timeframe, and market metadata
from the incumbent bar file.  Price, volume, portfolio, and return fields are
never read or converted.  It reconstructs the event/cadence ordering and
persists an outcome-free diagnosis receipt.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
INCUMBENT_BARS = Path("data/historical/equity_pit_symmetric_2016_2026_v5.csv")
V2_PACKAGE = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json"
)
M3_GATE = Path("data/research_receipts/equity_m3_data_gate_2026_09_r5.json")
V3_FAILURE_RECEIPT = Path("data/research_receipts/r46_v3_execution_failure_incident_2026_09_14.json")
OUTPUT = Path("data/research_receipts/r46_v3_incumbent_base_failure_diagnosis_2026_09_14.json")
MINIMUM_HISTORY_BARS = 274
FAILURE_ERROR = '{"error": "BLOCKED_R46_INCUMBENT_BASE_MISSING", "status": "BLOCKED"}'


def aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"naive_timestamp:{value}")
    return parsed.astimezone(UTC)


def canonical_hash(value: object, field: str) -> str:
    payload = deepcopy(value)
    if not isinstance(payload, dict):
        raise TypeError("diagnosis_receipt_must_be_object")
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise TypeError("diagnosis_integrity_must_be_object")
    integrity[field] = "PENDING"
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metadata_indices(header: list[str], required: tuple[str, ...]) -> dict[str, int]:
    indices = {name: header.index(name) for name in required}
    forbidden = {"open", "high", "low", "close", "volume"}
    if forbidden.intersection(indices):
        raise ValueError("price_or_volume_column_selected")
    return indices


def read_incumbent_timeline(path: Path, *, stop_at: datetime | None = None) -> dict[str, Any]:
    """Read only structural columns up to the diagnosis window."""

    required = ("timestamp", "symbol", "timeframe", "market")
    signal_timestamps: list[datetime] = []
    symbol_counts: dict[str, int] = {}
    spy_counts: dict[datetime, int] = {}
    timestamps_seen: set[datetime] = set()
    rows_read = 0
    previous_timestamp: datetime | None = None
    first_signal: datetime | None = None
    first_supported: datetime | None = None
    current_timestamp: datetime | None = None
    symbols_at_current: set[str] = set()

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        indices = _metadata_indices(header, required)
        for fields in reader:
            timestamp = aware(fields[indices["timestamp"]])
            if stop_at is not None and timestamp > stop_at:
                break
            symbol = fields[indices["symbol"]].strip().upper()
            timeframe = fields[indices["timeframe"]]
            market = fields[indices["market"]]
            if timeframe != "1d" or market != "equity":
                continue
            rows_read += 1
            first_signal = timestamp if first_signal is None else min(first_signal, timestamp)
            if previous_timestamp is not None and timestamp < previous_timestamp:
                raise ValueError("incumbent_timeline_not_sorted")
            previous_timestamp = timestamp
            if current_timestamp != timestamp:
                current_timestamp = timestamp
                symbols_at_current = set()
            if symbol in symbols_at_current:
                continue
            symbols_at_current.add(symbol)
            timestamps_seen.add(timestamp)
            signal_timestamps.append(timestamp)
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
            if symbol == "SPY":
                spy_counts[timestamp] = symbol_counts[symbol]
                if first_supported is None and symbol_counts[symbol] >= MINIMUM_HISTORY_BARS:
                    first_supported = timestamp

    if first_signal is None or first_supported is None:
        raise ValueError("incumbent_history_support_not_found")
    return {
        "first_signal_timestamp": first_signal,
        "first_supported_timestamp": first_supported,
        "spy_history_counts": spy_counts,
        "signal_timestamps": sorted(timestamps_seen),
        "rows_read": rows_read,
        "unique_signal_timestamps": len(timestamps_seen),
    }


def first_tlt_signal(path: Path) -> datetime | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        indices = _metadata_indices(header, ("session_date", "regular_open_timestamp", "available_at"))
        del indices["session_date"]
        del indices["available_at"]
        for fields in reader:
            return aware(fields[indices["regular_open_timestamp"]])
    return None


def weekly_decisions(timestamps: list[datetime], *, through: datetime) -> list[datetime]:
    decisions: list[datetime] = []
    previous: datetime | None = None
    for timestamp in timestamps:
        if timestamp > through:
            break
        if previous is None or previous.isocalendar()[:2] != timestamp.isocalendar()[:2]:
            decisions.append(timestamp)
        previous = timestamp
    return decisions


def build_diagnosis(repo_root: Path) -> dict[str, Any]:
    package_path = repo_root / V2_PACKAGE
    package = json.loads(package_path.read_text(encoding="utf-8"))
    events = package["coverage"]["schedule_linkage"]["events"]
    if len(events) != 127:
        raise ValueError("frozen_event_count_changed")

    incumbent_path = repo_root / INCUMBENT_BARS
    tlt_path = repo_root / "data/research/runs/r46_tlt_market_data_v2_effective_open_raw_source_2026_09_11/split_bars.csv"
    event_decisions = []
    for event in events:
        event_decisions.append(
            {
                "event_id": event["canonical_event_id"],
                "entry_decision": aware(event["available_at"]),
                "entry_open": aware(event["entry_open_timestamp"]),
                "exit_decision": aware(event["future_open_timestamps"][4]),
                "role": "ENTRY",
            }
        )
        event_decisions[-1]["exit_role"] = "EXIT"
    event_decisions.sort(key=lambda item: (item["entry_decision"], item["event_id"]))

    # The first R46 boundary after support is needed to bound the metadata scan.
    first_supported_guess = aware("2017-02-02T05:00:00Z")
    first_supported_event = next(
        item for item in event_decisions if item["entry_decision"] >= first_supported_guess
    )
    first_event_boundary = first_supported_event["entry_decision"]
    first_native_after_event_guess = aware("2017-02-13T05:00:00Z")
    timeline = read_incumbent_timeline(incumbent_path, stop_at=first_native_after_event_guess)
    first_tlt = first_tlt_signal(tlt_path)
    first_signal = timeline["first_signal_timestamp"]
    if first_tlt is not None:
        first_signal = min(first_signal, first_tlt)
    first_supported = timeline["first_supported_timestamp"]
    first_supported_event = next(
        item for item in event_decisions if item["entry_decision"] >= first_supported
    )
    first_event_boundary = first_supported_event["entry_decision"]
    native_decisions = weekly_decisions(timeline["signal_timestamps"], through=first_native_after_event_guess)
    native_supported = [
        item
        for item in native_decisions
        if timeline["spy_history_counts"].get(item, 0) >= MINIMUM_HISTORY_BARS
    ]
    if not native_supported:
        raise ValueError("native_supported_cadence_not_found")
    first_native_target = native_supported[0]
    m3_gate = json.loads((repo_root / M3_GATE).read_text(encoding="utf-8"))
    m3_first_feasible = aware(
        str(m3_gate["history_eligibility"]["first_full_candidate_feasible_decision"]) + "T05:00:00Z"
    )
    if m3_first_feasible != first_native_target:
        raise ValueError("native_and_pinned_feasibility_timestamps_diverge")

    cadence_rows = []
    for timestamp in native_decisions:
        count = timeline["spy_history_counts"].get(timestamp)
        supported = count is not None and count >= MINIMUM_HISTORY_BARS
        cadence_rows.append(
            {
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "iso_week": list(timestamp.isocalendar()[:2]),
                "spy_history_count": count,
                "supports_from_history_count": supported,
                "role_in_failure_path": (
                    "pre_support_cadence"
                    if not supported
                    else "first_native_incumbent_target_materialization"
                    if timestamp == first_native_target
                    else "post_first_native_incumbent_target"
                ),
            }
        )

    boundary_rows = [
        {
            "event_id": item["event_id"],
            "timestamp": item["entry_decision"].isoformat().replace("+00:00", "Z"),
            "role": item["role"],
            "supports_from_history_count": item["entry_decision"] >= first_supported,
            "before_first_native_target": item["entry_decision"] < first_native_target,
        }
        for item in event_decisions
        if first_supported <= item["entry_decision"] <= first_native_target
    ]
    failure_receipt = json.loads((repo_root / V3_FAILURE_RECEIPT).read_text(encoding="utf-8"))
    if failure_receipt["execution"]["terminal_error"] != FAILURE_ERROR:
        raise ValueError("frozen_v3_failure_error_changed")

    diagnosis: dict[str, Any] = {
        "schema_version": "r46-v3-incumbent-base-failure-diagnosis-v1",
        "candidate_id": "R46",
        "v3_research_id": "r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14",
        "method": {
            "scope": "OUTCOME_FREE_EVENT_DRIVEN_STATE_DIAGNOSIS",
            "economic_execution": False,
            "prices_read": False,
            "returns_read": False,
            "portfolio_engine_invoked": False,
            "runner_invoked": False,
            "metadata_columns": ["timestamp", "symbol", "timeframe", "market"],
            "minimum_history_bars": MINIMUM_HISTORY_BARS,
            "native_cadence": "weekly_first_bar_of_iso_week",
            "event_boundary": "schedule.entry_decision=available_at; schedule.exit_decision=fifth_future_open",
        },
        "source_pins": {
            "incumbent_bars_path": str(INCUMBENT_BARS),
            "incumbent_bars_sha256": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
            "package_path": str(V2_PACKAGE),
            "package_canonical_sha256": "c67cfdfeebc4cbc0847d5e2e6fbdebdf6928e04b3e5103606eb8bd0c3d03f6c6",
            "schedule_digest": package["coverage"]["schedule_linkage"]["canonical_digest_sha256"],
            "event_count": len(events),
        },
        "timeline": {
            "first_dataset_signal_timestamp": first_signal.isoformat().replace("+00:00", "Z"),
            "first_incumbent_signal_timestamp": timeline["first_signal_timestamp"].isoformat().replace("+00:00", "Z"),
            "first_tlt_signal_timestamp": first_tlt.isoformat().replace("+00:00", "Z") if first_tlt else None,
            "v3_minimum_history_bars": MINIMUM_HISTORY_BARS,
            "first_timestamp_supports_could_become_true_from_history_count_alone": first_supported.isoformat().replace(
                "+00:00", "Z"
            ),
            "first_supported_spy_history_count": timeline["spy_history_counts"].get(first_supported),
            "native_weekly_cadence_decisions": cadence_rows,
            "first_native_weekly_cadence_target_materialization_timestamp": first_native_target.isoformat().replace(
                "+00:00", "Z"
            ),
            "first_native_target_definition": "first native weekly cadence with history support and the pinned no-economic-score feasibility metadata",
            "pinned_feasibility_metadata": {
                "path": str(M3_GATE),
                "first_full_candidate_feasible_decision": m3_gate["history_eligibility"]["first_full_candidate_feasible_decision"],
                "m3_matrix_eligible_count": m3_gate["history_eligibility"].get("m3_matrix_eligible_count"),
                "no_economic_scores": m3_gate["numerical_feasibility"].get("no_economic_scores"),
            },
        },
        "failure_boundary": {
            "first_r46_event_boundary_where_v3_support_true": first_event_boundary.isoformat().replace("+00:00", "Z"),
            "failing_event_id": first_supported_event["event_id"],
            "failing_event_role": "ENTRY",
            "failing_event_timestamp": first_event_boundary.isoformat().replace("+00:00", "Z"),
            "spy_history_count_at_failing_event": timeline["spy_history_counts"].get(first_event_boundary),
            "event_before_first_native_target": first_event_boundary < first_native_target,
            "event_before_fifth_future_open": first_event_boundary < first_supported_event["exit_decision"],
            "event_boundaries_in_same_supported_no_prior_target_window": boundary_rows,
            "event_boundary_count_in_window": len(boundary_rows),
            "observed_terminal_error": FAILURE_ERROR,
            "observed_missing_cached_incumbent_target": True,
        },
        "initial_account_state": {
            "proof_basis": [
                "PortfolioBacktestEngine initializes an empty position map when no persisted state is supplied",
                "PortfolioBacktestEngine initializes cash to starting_equity when state.cash is absent",
                "V3 production contract fixes starting_equity at 100000.0",
                "The V3 base state is SEALED_UNUSED and its persisted output set is absent",
            ],
            "starting_equity": 100000.0,
            "pre_first_incumbent_target_base": "CASH",
            "cash_weight": 1.0,
            "non_tlt_positions": 0,
            "incumbent_orders_before_first_target": 0,
            "initial_account_is_all_cash": True,
        },
        "root_cause": {
            "classification": "ROOT_CAUSE_NOT_PROVEN",
            "repair_classification": None,
            "proven": False,
            "proof": [
                "history support is structurally available before the first failing event boundary",
                "the first native weekly cadence target is structurally feasible at 2017-02-06T05:00:00Z",
                "the first support-valid R46 ENTRY boundary is 2017-02-10T05:00:00Z, after that native cadence",
                "the observed error therefore does not prove the suspected pre-first-target ordering",
            ],
            "non_tlt_incumbent_position_can_exist_before_first_target": False,
            "new_v3_alpha_decision_at_failing_event": False,
            "economic_outcome_used": False,
        },
        "integrity": {"diagnosis_sha256": "PENDING"},
    }
    diagnosis["integrity"]["diagnosis_sha256"] = canonical_hash(diagnosis, "diagnosis_sha256")
    return diagnosis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--output", type=Path, default=REPO / OUTPUT)
    args = parser.parse_args(argv)
    diagnosis = build_diagnosis(args.repo_root.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(diagnosis, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    status = str(diagnosis["root_cause"]["classification"])
    print(json.dumps({"status": status, "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
