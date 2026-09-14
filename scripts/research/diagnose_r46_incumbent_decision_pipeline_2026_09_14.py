"""Trace the frozen R46 decision gates without running the economic engine.

The diagnostic consumes structural timestamp and symbol metadata only.  It
does not import the backtest engine or an R46 runner, does not rank a pool,
and does not call a strategy decision method.  When the exact runtime
observation snapshot is absent, the result is explicitly ``UNPROVEN`` rather
than reconstructed from a different research contract.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from bisect import bisect_right
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
INCUMBENT_BARS = Path("data/historical/equity_pit_symmetric_2016_2026_v5.csv")
TLT_BARS = Path(
    "data/research/runs/r46_tlt_market_data_v2_effective_open_raw_source_2026_09_11/split_bars.csv"
)
V2_PACKAGE = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json"
)
V3_CONTRACT = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_execution_contract.json"
)
V3_FAILURE_RECEIPT = Path("data/research_receipts/r46_v3_execution_failure_incident_2026_09_14.json")
V3_DIAGNOSIS_RECEIPT = Path("data/research_receipts/r46_v3_incumbent_base_failure_diagnosis_2026_09_14.json")
OUTPUT = Path("data/research_receipts/r46_v3_incumbent_decision_pipeline_diagnosis_2026_09_14.json")
M3_COMPARISON_ARTIFACTS = (
    Path("data/research/generated/equity_m3_candidate_ocp_2026_09/control_universe.json"),
    Path("data/research/generated/equity_m3_candidate_ocp_2026_09_r2/control_universe.json"),
)
V3_OUTPUT_DIRECTORY = Path(
    "data/research/runs/r46_treasury_auction_unwind_tlt_v3_recovery_final_2026_09_14"
)

WINDOW_START = datetime(2017, 2, 1, tzinfo=UTC)
WINDOW_END = datetime(2017, 2, 13, 23, 59, 59, tzinfo=UTC)
FEB06 = datetime(2017, 2, 6, 5, tzinfo=UTC)
FEB10 = datetime(2017, 2, 10, 5, tzinfo=UTC)
MINIMUM_HISTORY_BARS = 274
FAILURE_ERROR = '{"error": "BLOCKED_R46_INCUMBENT_BASE_MISSING", "status": "BLOCKED"}'
FAILURE_EVENT_ID = "2017-02-09:912810RV2"
DIAGNOSTIC_PARENT_COMMIT = "d3a65ddbc535dfea86dcfbc26e0c5b94a1aa2c6e"
UNIVERSE_METHOD = (
    "daily production scanner policy on trailing 60d metrics; inputs strictly precede snapshot; "
    "Stage-A shortlist capped at 300 symbols by trailing median dollar volume; "
    "historical spread and broad sector/tradability metadata unavailable and explicitly degraded"
)
PINNED_SYMBOLS = ("SPY", "SH", "PSQ", "DOG")
EXACT_OBSERVATION_METADATA_STATUS = "UNAVAILABLE_EXACT_R46_RUNTIME_SNAPSHOT_NOT_PERSISTED"
UNPROVEN_PRE_DECIDE_REASON = "ROOT_CAUSE_STILL_UNPROVEN_EXACT_R46_OBSERVATION_METADATA_MISSING"


def aware(value: str) -> datetime:
    """Parse a timezone-aware timestamp and normalize it to UTC."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"naive_timestamp:{value}")
    return parsed.astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def canonical_hash(value: object, field: str) -> str:
    """Hash a JSON object after replacing its self-integrity field."""

    payload = deepcopy(value)
    if not isinstance(payload, dict):
        raise TypeError("canonical_hash_requires_object")
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise TypeError("canonical_hash_requires_integrity_object")
    integrity[field] = "PENDING"
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_hash_without_self(value: object, field: str) -> str:
    """Hash an existing repository artifact using its omission convention."""

    payload = deepcopy(value)
    if not isinstance(payload, dict):
        raise TypeError("canonical_hash_requires_object")
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise TypeError("canonical_hash_requires_integrity_object")
    integrity.pop(field, None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def _metadata_indices(header: list[str], required: tuple[str, ...]) -> dict[str, int]:
    missing = [name for name in required if name not in header]
    if missing:
        raise ValueError(f"metadata_columns_missing:{','.join(missing)}")
    forbidden = {"open", "high", "low", "close", "volume", "price"}
    if forbidden.intersection(required):
        raise ValueError("price_or_volume_column_selected")
    return {name: header.index(name) for name in required}


@dataclass(frozen=True)
class TimelineMetadata:
    timestamps: tuple[datetime, ...]
    first_timestamp: datetime
    first_supported_timestamp: datetime
    spy_history_counts: Mapping[datetime, int]
    maximum_history_counts: Mapping[datetime, int]
    symbols_at_audit_timestamp: Mapping[datetime, frozenset[str]]
    history_at_audit_timestamp: Mapping[datetime, Mapping[str, int]]
    latest_at_audit_timestamp: Mapping[datetime, Mapping[str, datetime]]
    rows_read: int
    unique_symbol_timestamp_pairs: int


def read_v5_timeline(path: Path, audit_timestamps: frozenset[datetime]) -> TimelineMetadata:
    """Read V5 structural columns and retain no market-value columns."""

    timestamps: list[datetime] = []
    spy_history_counts: dict[datetime, int] = {}
    maximum_history_counts: dict[datetime, int] = {}
    symbols_at_audit: dict[datetime, frozenset[str]] = {}
    history_at_audit: dict[datetime, Mapping[str, int]] = {}
    latest_at_audit: dict[datetime, Mapping[str, datetime]] = {}
    symbol_counts: dict[str, int] = {}
    latest_by_symbol: dict[str, datetime] = {}
    current_symbols: set[str] = set()
    current_timestamp: datetime | None = None
    previous_timestamp: datetime | None = None
    first_timestamp: datetime | None = None
    first_supported: datetime | None = None
    rows_read = 0
    unique_pairs = 0

    def close_timestamp() -> None:
        nonlocal current_symbols, current_timestamp, first_supported
        if current_timestamp is None:
            return
        timestamps.append(current_timestamp)
        spy_count = symbol_counts.get("SPY", 0)
        spy_history_counts[current_timestamp] = spy_count
        maximum_history_counts[current_timestamp] = max(symbol_counts.values(), default=0)
        if first_supported is None and spy_count >= MINIMUM_HISTORY_BARS:
            first_supported = current_timestamp
        if current_timestamp in audit_timestamps:
            symbols_at_audit[current_timestamp] = frozenset(current_symbols)
            history_at_audit[current_timestamp] = {
                symbol: symbol_counts[symbol] for symbol in current_symbols
            }
            latest_at_audit[current_timestamp] = dict(latest_by_symbol)

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        indices = _metadata_indices(header, ("timestamp", "symbol", "timeframe", "market"))
        for fields in reader:
            timestamp = aware(fields[indices["timestamp"]])
            if fields[indices["timeframe"]] != "1d" or fields[indices["market"]].lower() != "equity":
                continue
            if previous_timestamp is not None and timestamp < previous_timestamp:
                raise ValueError("v5_timeline_not_sorted")
            previous_timestamp = timestamp
            first_timestamp = timestamp if first_timestamp is None else min(first_timestamp, timestamp)
            if current_timestamp != timestamp:
                close_timestamp()
                current_timestamp = timestamp
                current_symbols = set()
            rows_read += 1
            symbol = fields[indices["symbol"]].strip().upper()
            if symbol in current_symbols:
                continue
            current_symbols.add(symbol)
            unique_pairs += 1
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
            latest_by_symbol[symbol] = timestamp
    close_timestamp()

    if first_timestamp is None or first_supported is None:
        raise ValueError("v5_history_support_not_found")
    return TimelineMetadata(
        timestamps=tuple(timestamps),
        first_timestamp=first_timestamp,
        first_supported_timestamp=first_supported,
        spy_history_counts=spy_history_counts,
        maximum_history_counts=maximum_history_counts,
        symbols_at_audit_timestamp=symbols_at_audit,
        history_at_audit_timestamp=history_at_audit,
        latest_at_audit_timestamp=latest_at_audit,
        rows_read=rows_read,
        unique_symbol_timestamp_pairs=unique_pairs,
    )


def read_tlt_timestamps(path: Path) -> tuple[datetime, ...]:
    """Read only TLT regular-open timestamps."""

    timestamps: set[datetime] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        indices = _metadata_indices(header, ("regular_open_timestamp",))
        for fields in reader:
            timestamps.add(aware(fields[indices["regular_open_timestamp"]]))
    return tuple(sorted(timestamps))


def load_event_boundaries(path: Path) -> dict[datetime, tuple[dict[str, str], ...]]:
    package = load_object(path)
    coverage = package.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("package_coverage_missing")
    linkage = coverage.get("schedule_linkage")
    if not isinstance(linkage, dict):
        raise ValueError("package_schedule_linkage_missing")
    raw_events = linkage.get("events")
    if not isinstance(raw_events, list) or len(raw_events) != 127:
        raise ValueError("frozen_event_count_changed")
    by_timestamp: dict[datetime, list[dict[str, str]]] = {}
    for raw in raw_events:
        if not isinstance(raw, dict):
            raise ValueError("event_record_must_be_object")
        event_id = str(raw["canonical_event_id"])
        entry = aware(str(raw["available_at"]))
        future_opens = raw.get("future_open_timestamps")
        if not isinstance(future_opens, list) or len(future_opens) < 5:
            raise ValueError(f"event_future_opens_missing:{event_id}")
        exit_decision = aware(str(future_opens[4]))
        for timestamp, role in ((entry, "ENTRY"), (exit_decision, "EXIT")):
            by_timestamp.setdefault(timestamp, []).append(
                {"event_id": event_id, "boundary_type": role}
            )
    return {timestamp: tuple(records) for timestamp, records in by_timestamp.items()}


def _asof_index(timestamps: tuple[datetime, ...], timestamp: datetime) -> int:
    return bisect_right(timestamps, timestamp) - 1


def is_native_weekly_boundary(previous: datetime | None, current: datetime) -> bool:
    """Mirror the frozen weekly ISO-year/week transition predicate."""

    return previous is None or previous.isocalendar()[:2] != current.isocalendar()[:2]


@dataclass(frozen=True)
class ObservationMetadata:
    expected_symbols: tuple[str, ...] | None
    current_symbols: frozenset[str]
    latest_by_symbol: Mapping[str, datetime]
    source: str


def observation_gate(metadata: ObservationMetadata, timestamp: datetime) -> dict[str, Any]:
    """Apply the engine's exact/missing/stale distinction to synthetic metadata."""

    if metadata.expected_symbols is None:
        if metadata.source == "R46_EVENT_BOUNDARY_OVERRIDE":
            return {
                "expected_count": None,
                "current_count": None,
                "complete_count": None,
                "missing_count": None,
                "stale_count": None,
                "missing_symbols": None,
                "stale_symbols": None,
                "outcome": "BYPASSED_R46_EVENT_BOUNDARY_OVERRIDE",
            }
        return {
            "expected_count": None,
            "current_count": None,
            "complete_count": None,
            "missing_count": None,
            "stale_count": None,
            "missing_symbols": None,
            "stale_symbols": None,
            "outcome": "UNPROVEN_EXACT_EXPECTED_SET",
        }

    expected = tuple(dict.fromkeys(metadata.expected_symbols))
    missing: list[str] = []
    stale: list[str] = []
    complete: list[str] = []
    for symbol in expected:
        latest = metadata.latest_by_symbol.get(symbol)
        if latest is None:
            missing.append(symbol)
        elif latest != timestamp:
            stale.append(symbol)
        elif symbol not in metadata.current_symbols:
            stale.append(symbol)
        else:
            complete.append(symbol)
    return {
        "expected_count": len(expected),
        "current_count": len(complete),
        "complete_count": len(complete),
        "missing_count": len(missing),
        "stale_count": len(stale),
        "missing_symbols": sorted(missing),
        "stale_symbols": sorted(stale),
        "outcome": "PASS" if not missing and not stale else "INCOMPLETE",
    }


@dataclass(frozen=True)
class CycleContext:
    timestamp: datetime
    previous_timestamp: datetime | None
    event_boundary: bool
    event_type: str | None
    supports: bool | None
    product_guard: bool | None
    observation: ObservationMetadata
    intermediate_risk: bool = False
    incumbent_target_present: bool = False


def trace_cycle(context: CycleContext) -> dict[str, Any]:
    """Purely evaluate frozen scheduling gates; never invokes a strategy."""

    native_weekly = is_native_weekly_boundary(context.previous_timestamp, context.timestamp)
    extra_boundary = context.event_boundary
    scheduled = native_weekly or extra_boundary
    observation = observation_gate(context.observation, context.timestamp)
    previous_after = context.timestamp
    decide_reached: bool | None = False
    target_could_materialize: bool | None = False
    exact_skip_reason: str | None = None

    if not scheduled and not context.intermediate_risk:
        observation = {
            **observation,
            "outcome": "NOT_EVALUATED_NON_SCHEDULED_NO_INTERMEDIATE_RISK",
        }
        final_enum = "SKIPPED_OTHER_PRE_DECIDE_GATE"
        exact_skip_reason = "NOT_SCHEDULED_NO_INTERMEDIATE_RISK_REBALANCE"
    elif context.product_guard is False:
        final_enum = "SKIPPED_PRODUCT_UNIVERSE"
        exact_skip_reason = "PRODUCT_UNIVERSE_GUARD_FAILED"
    elif context.product_guard is None:
        decide_reached = None
        target_could_materialize = None
        final_enum = "SKIPPED_OTHER_PRE_DECIDE_GATE"
        exact_skip_reason = "PRODUCT_UNIVERSE_GUARD_UNPROVEN"
    elif context.supports is False:
        observation = {
            **observation,
            "outcome": "NOT_EVALUATED_SUPPORT_FALSE",
        }
        final_enum = "SKIPPED_SUPPORT"
        exact_skip_reason = "STRATEGY_SUPPORT_FALSE"
    elif context.supports is None:
        decide_reached = None
        target_could_materialize = None
        final_enum = "SKIPPED_OTHER_PRE_DECIDE_GATE"
        exact_skip_reason = "STRATEGY_SUPPORT_UNPROVEN"
    elif observation["outcome"] == "INCOMPLETE":
        final_enum = "SKIPPED_OBSERVATION_COMPLETENESS"
        exact_skip_reason = "Historical strategy batch incomplete; decision skipped"
    elif observation["outcome"] == "UNPROVEN_EXACT_EXPECTED_SET":
        decide_reached = None
        target_could_materialize = None
        final_enum = "SKIPPED_OTHER_PRE_DECIDE_GATE"
        exact_skip_reason = UNPROVEN_PRE_DECIDE_REASON
    else:
        final_enum = "DECIDE_REACHED"
        decide_reached = True
        if context.event_boundary and not context.incumbent_target_present:
            target_could_materialize = False
            exact_skip_reason = FAILURE_ERROR
        else:
            target_could_materialize = True

    if final_enum == "DECIDE_REACHED":
        state_after = "PRESENT" if target_could_materialize else "NONE_PROVEN_BEFORE_EVENT"
    elif exact_skip_reason == UNPROVEN_PRE_DECIDE_REASON:
        state_after = "UNKNOWN"
    elif context.incumbent_target_present:
        state_after = "PRESENT"
    else:
        state_after = "NONE"

    return {
        "timestamp": iso(context.timestamp),
        "iso_week": list(context.timestamp.isocalendar()[:2]),
        "r46_event_boundary": context.event_boundary,
        "r46_boundary_type": context.event_type,
        "engine_previous_timestamp_before_scheduling": iso(context.previous_timestamp),
        "native_weekly_scheduled": native_weekly,
        "r46_extra_boundary_scheduled": extra_boundary,
        "scheduled": scheduled,
        "strategy_supports": context.supports,
        "product_universe_guard": (
            "NOT_EVALUATED_NON_SCHEDULED"
            if not scheduled and not context.intermediate_risk
            else
            "PASS"
            if context.product_guard is True
            else "FAIL"
            if context.product_guard is False
            else "UNPROVEN"
        ),
        "observation_symbol_source": context.observation.source,
        "effective_observation_symbol_count": observation["expected_count"],
        "current_observation_symbol_count": observation["current_count"],
        "complete_observation_symbol_count": observation["complete_count"],
        "missing_observation_symbol_count": observation["missing_count"],
        "stale_observation_symbol_count": observation["stale_count"],
        "missing_observation_symbols": observation["missing_symbols"],
        "stale_observation_symbols": observation["stale_symbols"],
        "completeness_gate": observation["outcome"],
        "intermediate_risk_gate_matters": context.intermediate_risk,
        "would_reach_strategy_decide": decide_reached,
        "target_could_materialize": target_could_materialize,
        "strategy_decide_invoked_by_diagnostic": False,
        "engine_previous_timestamp_after_cycle": iso(previous_after),
        "last_incumbent_target_state_after_cycle": state_after,
        "exact_skip_reason": exact_skip_reason,
        "final_decision_enum": final_enum,
    }


def config_semantics(path: Path) -> dict[str, Any]:
    """Verify the small frozen config contract without importing the runtime."""

    text = path.read_text(encoding="utf-8")
    patterns = {
        "maximum_stream_symbols": r"maximum_stream_symbols:\s*30\b",
        "target_shortlist": r"target_shortlist:\s*300\b",
        "reference_symbols": r"reference_symbols:\s*\n\s*-\s*SPY\b",
        "inverse_trade_symbols": r"inverse_trade_symbols:\s*\n\s*-\s*SH\b[\s\S]*?\n\s*-\s*PSQ\b[\s\S]*?\n\s*-\s*DOG\b",
        "scanner_rebalance": r"scanner_rebalance:\s*daily\b",
        "beta_window_bars": r"beta_window_bars:\s*273\b",
        "rebalance_frequency": r"rebalance_frequency:\s*weekly\b",
    }
    matched = {name: bool(re.search(pattern, text)) for name, pattern in patterns.items()}
    if not all(matched.values()):
        raise ValueError(f"frozen_config_semantics_changed:{matched}")
    return {
        "status": "FROZEN_TEXT_CONTRACT_MATCHED",
        "maximum_stream_symbols": 30,
        "target_shortlist": 300,
        "reference_symbols": ["SPY"],
        "inverse_trade_symbols": ["SH", "PSQ", "DOG"],
        "scanner_rebalance": "daily",
        "strategy": "equity_residual_momentum_v3",
        "strategy_timeframe": "1d",
        "rebalance_policy": "weekly",
        "minimum_history_bars": MINIMUM_HISTORY_BARS,
        "semantic_checks": matched,
    }


def _contains_forbidden_performance_key(value: object) -> bool:
    forbidden = {
        "pnl",
        "returns",
        "sharpe",
        "sortino",
        "drawdown",
        "equity_curve",
        "performance_metrics",
        "candidate_metrics",
        "control_metrics",
    }
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in forbidden or _contains_forbidden_performance_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_performance_key(item) for item in value)
    return False


def _receipt_path(repo_root: Path, raw_path: object) -> Path:
    path = Path(str(raw_path))
    return path if path.is_absolute() else repo_root / path


def _physical_pin_check(repo_root: Path, section: Mapping[str, Any]) -> dict[str, Any]:
    path_value = section.get("path")
    expected = section.get("physical_sha256")
    if not isinstance(path_value, str) or not isinstance(expected, str):
        return {"path": str(path_value), "exists": False, "matches": False}
    path = _receipt_path(repo_root, path_value)
    if not path.is_file():
        return {"path": path_value, "exists": False, "matches": False}
    actual = sha256_file(path)
    return {"path": path_value, "exists": True, "expected": expected, "actual": actual, "matches": actual == expected}


def verify_preserved_evidence(repo_root: Path) -> dict[str, Any]:
    """Verify the existing incident/diagnosis and immutable physical pins."""

    incident_path = repo_root / V3_FAILURE_RECEIPT
    prior_path = repo_root / V3_DIAGNOSIS_RECEIPT
    incident = load_object(incident_path)
    prior = load_object(prior_path)
    checks: dict[str, Any] = {
        "incident_path": relative(incident_path, repo_root),
        "prior_diagnosis_path": relative(prior_path, repo_root),
        "incident_self_hash_matches": incident.get("integrity", {}).get("incident_sha256")
        == canonical_hash(incident, "incident_sha256"),
        "prior_diagnosis_self_hash_matches": prior.get("integrity", {}).get("diagnosis_sha256")
        == canonical_hash(prior, "diagnosis_sha256"),
        "prior_diagnosis_keeps_root_cause_not_proven": prior.get("root_cause", {}).get("classification")
        == "ROOT_CAUSE_NOT_PROVEN",
        "incident_matches_failure_commit": incident.get("accepted_commits", {}).get("C_RESULT")
        == "6d01cade2e284905b13a2cb6e1ef4b325b3134a6",
        "incident_matches_terminal_error": incident.get("execution", {}).get("terminal_error") == FAILURE_ERROR,
        "prior_receipts_have_no_performance_metric_keys": not _contains_forbidden_performance_key(incident)
        and not _contains_forbidden_performance_key(prior),
    }
    immutable_sections = {
        "v3_base_state": incident.get("v3_base_state"),
        "v3_reservation": incident.get("v3_reservation"),
        "v3_authorization": incident.get("v3_authorization"),
        "v3_preflight": incident.get("v3_preflight"),
        "v3_execution_contract": incident.get("v3_execution_contract"),
        "v2_reservation": incident.get("v2_reservation"),
    }
    physical_checks: dict[str, Any] = {}
    for name, section in immutable_sections.items():
        if isinstance(section, Mapping):
            physical_checks[name] = _physical_pin_check(repo_root, section)
    reservation = immutable_sections.get("v3_reservation")
    if isinstance(reservation, Mapping):
        reservation_path = _receipt_path(repo_root, reservation["path"])
        reservation_object = load_object(reservation_path)
        checks["v3_reservation_transition_hash_matches"] = reservation_object.get("integrity", {}).get(
            "transition_sha256"
        ) == reservation.get("transition_canonical_sha256")
    canonical_checks = {
        "v3_base_state": ("artifact_sha256", "canonical_sha256"),
        "v3_authorization": ("authorization_sha256", "canonical_sha256"),
        "v3_preflight": ("receipt_sha256", "canonical_sha256"),
        "v3_execution_contract": ("contract_sha256", "canonical_sha256"),
    }
    for name, (integrity_field, expected_field) in canonical_checks.items():
        section = immutable_sections.get(name)
        if not isinstance(section, Mapping):
            continue
        path = _receipt_path(repo_root, section["path"])
        object_value = load_object(path)
        checks[f"{name}_canonical_hash_matches"] = object_value.get("integrity", {}).get(integrity_field) == section.get(
            expected_field
        ) and canonical_hash_without_self(object_value, integrity_field) == section.get(expected_field)
    v2_reservation = immutable_sections.get("v2_reservation")
    if isinstance(v2_reservation, Mapping):
        v2_path = _receipt_path(repo_root, v2_reservation["path"])
        v2_object = load_object(v2_path)
        checks["v2_reservation_transition_hash_matches"] = v2_object.get("integrity", {}).get(
            "transition_sha256"
        ) == v2_reservation.get("transition_canonical_sha256")
    checks["immutable_physical_pins"] = physical_checks
    checks["all_preserved_checks_pass"] = all(
        value is True
        for key, value in checks.items()
        if key != "immutable_physical_pins" and isinstance(value, bool)
    ) and all(item.get("matches") is True for item in physical_checks.values())
    return checks


def exact_observation_metadata(repo_root: Path) -> dict[str, Any]:
    candidates = (
        "universe.json",
        "product_universe.json",
        "observation_snapshots.json",
        "results.json",
        "ledger.json",
        "report.json",
        "run_receipt.json",
    )
    found = [relative(V3_OUTPUT_DIRECTORY / name, repo_root) for name in candidates if (repo_root / V3_OUTPUT_DIRECTORY / name).is_file()]
    comparisons = [
        {
            "path": relative(repo_root / path, repo_root),
            "exists": (repo_root / path).is_file(),
            "usable_as_exact_r46_metadata": False,
            "reason": "different_M3_contract_reference_symbols_include_IWM_and_QQQ",
        }
        for path in M3_COMPARISON_ARTIFACTS
    ]
    return {
        "status": "EXACT_R46_RUNTIME_METADATA_FOUND" if found else EXACT_OBSERVATION_METADATA_STATUS,
        "searched_output_directory": relative(repo_root / V3_OUTPUT_DIRECTORY, repo_root),
        "searched_filenames": list(candidates),
        "found_files": found,
        "substitutions_used": False,
        "rejected_comparison_artifacts": comparisons,
        "why_not_reconstructed": (
            "Frozen product construction delegates observation membership to strategy.rank_candidates; "
            "replaying that alpha selection is prohibited and the exact runtime snapshot/hash was not persisted."
        ),
    }


def _event_records_for_window(
    event_boundaries: Mapping[datetime, tuple[dict[str, str], ...]],
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for timestamp, items in sorted(event_boundaries.items()):
        if WINDOW_START <= timestamp <= WINDOW_END:
            for item in items:
                records.append({"timestamp": str(iso(timestamp)), **item})
    return records


def _history_at_or_before(timeline: TimelineMetadata, timestamp: datetime) -> tuple[int | None, int | None, datetime | None]:
    index = _asof_index(timeline.timestamps, timestamp)
    if index < 0:
        return None, None, None
    source_timestamp = timeline.timestamps[index]
    return (
        timeline.spy_history_counts[source_timestamp],
        timeline.maximum_history_counts[source_timestamp],
        source_timestamp,
    )


def _known_pin_checks(timeline: TimelineMetadata, timestamp: datetime) -> list[dict[str, Any]]:
    history = timeline.history_at_audit_timestamp.get(timestamp, {})
    latest = timeline.latest_at_audit_timestamp.get(timestamp, {})
    current = timeline.symbols_at_audit_timestamp.get(timestamp, frozenset())
    return [
        {
            "symbol": symbol,
            "history_exists": symbol in history,
            "history_length": history.get(symbol),
            "latest_completed_bar_timestamp": iso(latest.get(symbol)),
            "current_bar_at_timestamp": symbol in current,
        }
        for symbol in PINNED_SYMBOLS
    ]


def _snapshot_audit(timeline: TimelineMetadata, timestamp: datetime) -> dict[str, Any]:
    index = _asof_index(timeline.timestamps, timestamp)
    prior = timeline.timestamps[index - 1] if index > 0 and timeline.timestamps[index] == timestamp else (
        timeline.timestamps[index] if index >= 0 and timeline.timestamps[index] < timestamp else None
    )
    # Product snapshots are created at the current scanner period with bars
    # strictly before the boundary.  This is a semantic expectation, not a
    # claim that the lost runtime snapshot can be recovered.
    if prior is None:
        prior = timeline.timestamps[index] if index >= 0 and timeline.timestamps[index] < timestamp else None
    return {
        "snapshot_effective_at_expected": iso(timestamp),
        "strict_prior_data_timestamp_expected": iso(prior),
        "exact_snapshot_persisted": False,
        "universe_version": None,
        "membership_count": None,
        "observation_membership_count": None,
        "product_guard": "PASS_BY_FROZEN_CONSTRUCTION",
        "metadata_status": EXACT_OBSERVATION_METADATA_STATUS,
    }


def _build_trace(
    timeline: TimelineMetadata,
    tlt_timestamps: tuple[datetime, ...],
    event_boundaries: Mapping[datetime, tuple[dict[str, str], ...]],
    exact_metadata: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[datetime, dict[str, Any]]]:
    v5_set = set(timeline.timestamps)
    tlt_set = set(tlt_timestamps)
    all_timestamps = tuple(sorted(v5_set | tlt_set | set(event_boundaries)))
    rows: list[dict[str, Any]] = []
    rows_by_timestamp: dict[datetime, dict[str, Any]] = {}
    previous: datetime | None = None
    failing_timestamp = FEB10
    for timestamp in all_timestamps:
        event_items = event_boundaries.get(timestamp, ())
        event_boundary = bool(event_items)
        event_type = event_items[0]["boundary_type"] if event_items else None
        native_weekly = is_native_weekly_boundary(previous, timestamp)
        scheduled = native_weekly or event_boundary
        spy_count, maximum_history, history_source_timestamp = _history_at_or_before(timeline, timestamp)
        supports = spy_count is not None and spy_count >= MINIMUM_HISTORY_BARS
        if not scheduled:
            observation_source = "NOT_EVALUATED_NON_SCHEDULED_NO_INTERMEDIATE_RISK"
            observation = ObservationMetadata(None, frozenset(), {}, observation_source)
            product_guard: bool | None = None
        elif not supports:
            observation_source = "NOT_EVALUATED_SUPPORT_FALSE"
            observation = ObservationMetadata(None, frozenset(), {}, observation_source)
            product_guard = True
        elif event_boundary:
            observation = ObservationMetadata(None, frozenset(), {}, "R46_EVENT_BOUNDARY_OVERRIDE")
            product_guard = True
        else:
            observation = ObservationMetadata(None, frozenset(), {}, EXACT_OBSERVATION_METADATA_STATUS)
            product_guard = True
        cycle = trace_cycle(
            CycleContext(
                timestamp=timestamp,
                previous_timestamp=previous,
                event_boundary=event_boundary,
                event_type=event_type,
                supports=supports,
                product_guard=product_guard,
                observation=observation,
                incumbent_target_present=False,
            )
        )
        if WINDOW_START <= timestamp <= WINDOW_END:
            sources: list[str] = []
            if timestamp in v5_set:
                sources.append("V5_INCUMBENT_TIMESTAMP")
            if timestamp in tlt_set:
                sources.append("TLT_REGULAR_OPEN_TIMESTAMP")
            if event_boundary:
                sources.append("R46_EVENT_MARKER_TIMESTAMP")
            row = {
                **cycle,
                "timestamp_sources": sources,
                "history_length_relevant_to_support": {
                    "spy_history_count": spy_count,
                    "maximum_observed_equity_history_length": maximum_history,
                    "minimum_required_history": MINIMUM_HISTORY_BARS,
                    "history_source_timestamp": iso(history_source_timestamp),
                    "exact_runtime_context_maximum": "NOT_PERSISTED",
                },
                "product_universe_available": True,
                "universe_version": None,
                "universe_version_status": "RUNTIME_HASH_NOT_PERSISTED",
                "product_universe_methodology": UNIVERSE_METHOD,
                "product_universe_snapshot": _snapshot_audit(timeline, timestamp),
                "observation_metadata_status": (
                    "R46_EVENT_BOUNDARY_NO_EXPECTED_SET"
                    if event_boundary
                    else exact_metadata["status"]
                ),
                "last_incumbent_target_remains_none": (
                    True if timestamp <= failing_timestamp else None
                ),
                "last_incumbent_target_state_proof": (
                    "INITIAL_NONE_AND_NO_DECIDE_REACHED_BEFORE_FAILING_EVENT"
                    if timestamp < FEB10 and cycle["final_decision_enum"] != "DECIDE_REACHED"
                    else "NONE_PROVEN_BY_TERMINAL_FAILURE_AND_NO_RESET_PATH"
                    if timestamp <= FEB10
                    else "NOT_PROCESSED_AFTER_TERMINAL_FAILURE"
                ),
                "terminal_failure_observed_at_timestamp": FAILURE_ERROR if timestamp == FEB10 else None,
                "r46_restoration_reached": False if timestamp == FEB10 else None,
                "one_shot_execution_reached_timestamp": timestamp <= FEB10,
            }
            if timestamp == FEB10:
                row["final_decision_enum"] = "DECIDE_REACHED"
                row["exact_skip_reason"] = FAILURE_ERROR
                row["would_reach_strategy_decide"] = True
                row["target_could_materialize"] = False
                row["last_incumbent_target_state_after_cycle"] = "NONE_PROVEN_BEFORE_EVENT"
            rows.append(row)
            rows_by_timestamp[timestamp] = row
        previous = timestamp
    return rows, rows_by_timestamp


def _first_native_supported_timestamp(
    timeline: TimelineMetadata,
    tlt_timestamps: tuple[datetime, ...],
    event_boundaries: Mapping[datetime, tuple[dict[str, str], ...]],
) -> datetime | None:
    all_timestamps = tuple(sorted(set(timeline.timestamps) | set(tlt_timestamps) | set(event_boundaries)))
    previous: datetime | None = None
    for timestamp in all_timestamps:
        native = is_native_weekly_boundary(previous, timestamp)
        spy_count, _, _ = _history_at_or_before(timeline, timestamp)
        if native and spy_count is not None and spy_count >= MINIMUM_HISTORY_BARS:
            return timestamp
        previous = timestamp
    return None


def _supported_weekly_rows(
    timeline: TimelineMetadata,
    tlt_timestamps: tuple[datetime, ...],
    event_boundaries: Mapping[datetime, tuple[dict[str, str], ...]],
) -> tuple[list[datetime], int]:
    all_timestamps = tuple(sorted(set(timeline.timestamps) | set(tlt_timestamps) | set(event_boundaries)))
    previous: datetime | None = None
    supported: list[datetime] = []
    pre_support = 0
    for timestamp in all_timestamps:
        native = is_native_weekly_boundary(previous, timestamp)
        spy_count, _, _ = _history_at_or_before(timeline, timestamp)
        if native:
            if spy_count is not None and spy_count >= MINIMUM_HISTORY_BARS:
                supported.append(timestamp)
            else:
                pre_support += 1
        previous = timestamp
    return supported, pre_support


def _previous_timestamp_trace(trace_rows: Mapping[datetime, Mapping[str, Any]]) -> list[dict[str, Any]]:
    selected = (
        datetime(2017, 2, 3, 14, 30, tzinfo=UTC),
        FEB06,
        datetime(2017, 2, 7, 5, tzinfo=UTC),
        datetime(2017, 2, 7, 14, 30, tzinfo=UTC),
        datetime(2017, 2, 8, 5, tzinfo=UTC),
        datetime(2017, 2, 8, 14, 30, tzinfo=UTC),
        datetime(2017, 2, 9, 5, tzinfo=UTC),
        datetime(2017, 2, 9, 14, 30, tzinfo=UTC),
        FEB10,
    )
    output: list[dict[str, Any]] = []
    for timestamp in selected:
        row = trace_rows.get(timestamp)
        if row is None:
            continue
        output.append(
            {
                "timestamp": row["timestamp"],
                "previous_before_scheduling": row["engine_previous_timestamp_before_scheduling"],
                "native_weekly_scheduled": row["native_weekly_scheduled"],
                "r46_extra_boundary_scheduled": row["r46_extra_boundary_scheduled"],
                "scheduled": row["scheduled"],
                "previous_after_cycle": row["engine_previous_timestamp_after_cycle"],
                "terminal_failure": row["terminal_failure_observed_at_timestamp"],
                "r46_restoration_reached": row["r46_restoration_reached"],
            }
        )
    return output


def _deep_feb06(
    timeline: TimelineMetadata,
    trace_rows: Mapping[datetime, Mapping[str, Any]],
) -> dict[str, Any]:
    row = trace_rows[FEB06]
    feb03 = timeline.symbols_at_audit_timestamp.get(datetime(2017, 2, 3, 5, tzinfo=UTC), frozenset())
    feb06 = timeline.symbols_at_audit_timestamp.get(FEB06, frozenset())
    feb03_minus_feb06 = sorted(feb03 - feb06)
    feb06_minus_feb03 = sorted(feb06 - feb03)
    return {
        "timestamp": iso(FEB06),
        "scheduling": {
            "previous_timestamp": row["engine_previous_timestamp_before_scheduling"],
            "native_weekly_scheduled": row["native_weekly_scheduled"],
            "r46_extra_boundary_scheduled": row["r46_extra_boundary_scheduled"],
            "scheduled": row["scheduled"],
        },
        "support": {
            **row["history_length_relevant_to_support"],
            "spy_history_count": row["history_length_relevant_to_support"]["spy_history_count"],
            "supports": row["strategy_supports"],
            "known_pinned_symbol_checks": _known_pin_checks(timeline, FEB06),
        },
        "product_universe": {
            **row["product_universe_snapshot"],
            "product_guard": row["product_universe_guard"],
            "effective_snapshot_membership": "UNAVAILABLE_EXACT_RUNTIME_SNAPSHOT",
        },
        "observation_completeness": {
            "selection_semantics": (
                "references/pins are reserved first; strategy rank_candidates output is filtered by "
                "minimum history and benchmark latest alignment; scanner fallback is filtered by the same "
                "alignment; the exact result was not persisted"
            ),
            "metadata_status": EXACT_OBSERVATION_METADATA_STATUS,
            "expected_observation_symbols": None,
            "expected_count": row["effective_observation_symbol_count"],
            "complete_count": row["complete_observation_symbol_count"],
            "missing_count": row["missing_observation_symbol_count"],
            "stale_count": row["stale_observation_symbol_count"],
            "missing_symbols": row["missing_observation_symbols"],
            "stale_symbols": row["stale_observation_symbols"],
            "frozen_engine_warning": "NOT_PROVABLE_WITHOUT_EXACT_RUNTIME_OBSERVATION_SET",
            "gate": row["completeness_gate"],
            "data_only_alignment_envelope": {
                "all_feb03_symbols_present_feb06": bool(feb03) and not feb03_minus_feb06,
                "feb03_minus_feb06_symbols": feb03_minus_feb06,
                "feb06_minus_feb03_symbols": feb06_minus_feb03,
                "not_substituted_for_exact_runtime_set": True,
            },
        },
        "final_decision": {
            "enum": "SKIPPED_OTHER_PRE_DECIDE_GATE",
            "enum_status": "UNPROVEN_CATCH_ALL_NOT_A_GATE_ATTRIBUTION",
            "exact_skip_reason": UNPROVEN_PRE_DECIDE_REASON,
            "would_reach_strategy_decide": None,
            "target_could_materialize": None,
            "strategy_decide_invoked_by_diagnostic": False,
        },
        "engine_previous_timestamp_after_cycle": row["engine_previous_timestamp_after_cycle"],
        "last_incumbent_target_remains_none": True,
        "last_incumbent_target_state_proof": "NONE_PROVEN_BY_LATER_TERMINAL_FAILURE_AND_NO_RESET_PATH",
    }


def _deep_feb10(
    timeline: TimelineMetadata,
    trace_rows: Mapping[datetime, Mapping[str, Any]],
    event_boundaries: Mapping[datetime, tuple[dict[str, str], ...]],
) -> dict[str, Any]:
    row = trace_rows[FEB10]
    event = next(item for item in event_boundaries[FEB10] if item["event_id"] == FAILURE_EVENT_ID)
    return {
        "timestamp": iso(FEB10),
        "event_id": FAILURE_EVENT_ID,
        "boundary_type": event["boundary_type"],
        "scheduling": {
            "previous_timestamp": row["engine_previous_timestamp_before_scheduling"],
            "native_weekly_scheduled": row["native_weekly_scheduled"],
            "event_extra_scheduled": row["r46_extra_boundary_scheduled"],
            "scheduled": row["scheduled"],
        },
        "support": {
            **row["history_length_relevant_to_support"],
            "supports": row["strategy_supports"],
            "known_pinned_symbol_checks": _known_pin_checks(timeline, FEB10),
        },
        "product_universe": {
            **row["product_universe_snapshot"],
            "product_guard": row["product_universe_guard"],
            "effective_snapshot_membership": "NOT_REQUIRED_TO_EVENT_COMPLETENESS_OVERRIDE",
        },
        "observation_completeness": {
            "effective_observation_symbols": None,
            "source": "R46_EVENT_BOUNDARY_OVERRIDE",
            "expected_count": None,
            "missing_count": None,
            "stale_count": None,
            "missing_symbols": None,
            "stale_symbols": None,
            "gate": "BYPASSED_R46_EVENT_BOUNDARY_OVERRIDE",
            "engine_semantics": (
                "R46 _effective_observation_symbols returns None at event timestamps, so the normal "
                "historical strategy batch completeness check does not run on the marker boundary."
            ),
        },
        "final_sequence": {
            "would_reach_strategy_decide": True,
            "strategy_decide_invoked_by_diagnostic": False,
            "incumbent_target_present_immediately_before_event": False,
            "target_could_materialize": False,
            "terminal_failure": FAILURE_ERROR,
            "previous_timestamp_after_base_write": row["engine_previous_timestamp_after_cycle"],
            "r46_restoration_reached": False,
            "why_farther_than_feb06": (
                "The extra event boundary schedules the cycle and the R46 override returns no expected "
                "observation set; support and the product guard pass, so control flow reaches the event "
                "branch that requires _last_incumbent_target."
            ),
        },
    }


def build_diagnosis(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    incident = load_object(repo_root / V3_FAILURE_RECEIPT)
    contract = load_object(repo_root / V3_CONTRACT)
    package = load_object(repo_root / V2_PACKAGE)
    preserved = verify_preserved_evidence(repo_root)
    if not preserved["all_preserved_checks_pass"]:
        raise ValueError(f"preserved_evidence_verification_failed:{preserved}")
    if incident["execution"]["terminal_error"] != FAILURE_ERROR:
        raise ValueError("frozen_terminal_error_changed")
    if contract.get("source_commit") != incident["accepted_commits"]["C_SRC"]:
        raise ValueError("contract_source_commit_mismatch")
    event_boundaries = load_event_boundaries(repo_root / V2_PACKAGE)
    audit_timestamps = frozenset({FEB06, FEB10, datetime(2017, 2, 3, 5, tzinfo=UTC)})
    timeline = read_v5_timeline(repo_root / INCUMBENT_BARS, audit_timestamps)
    tlt_timestamps = read_tlt_timestamps(repo_root / TLT_BARS)
    exact_metadata = exact_observation_metadata(repo_root)
    trace, trace_by_timestamp = _build_trace(timeline, tlt_timestamps, event_boundaries, exact_metadata)
    first_apparent = _first_native_supported_timestamp(timeline, tlt_timestamps, event_boundaries)
    if first_apparent != FEB06:
        raise ValueError(f"unexpected_first_apparent_native_cadence:{iso(first_apparent)}")
    supported_weekly, pre_support_weekly = _supported_weekly_rows(timeline, tlt_timestamps, event_boundaries)
    if timeline.first_supported_timestamp != datetime(2017, 2, 2, 5, tzinfo=UTC):
        raise ValueError(f"unexpected_first_supported_timestamp:{iso(timeline.first_supported_timestamp)}")
    feb03_symbols = timeline.symbols_at_audit_timestamp.get(datetime(2017, 2, 3, 5, tzinfo=UTC), frozenset())
    feb06_symbols = timeline.symbols_at_audit_timestamp.get(FEB06, frozenset())
    feb10_symbols = timeline.symbols_at_audit_timestamp.get(FEB10, frozenset())
    source_files = contract.get("source_files")
    if not isinstance(source_files, list):
        raise ValueError("contract_source_files_missing")
    source_by_path = {
        str(item["path"]): item
        for item in source_files
        if isinstance(item, dict) and "path" in item
    }
    source_paths = (
        "scripts/research/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.py",
        "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "config/settings.yaml",
        "src/parayabuck/backtest/portfolio_engine.py",
        "src/parayabuck/backtest/product.py",
        "src/parayabuck/backtest/universe.py",
        "src/parayabuck/universe/observation_set.py",
        "src/parayabuck/portfolio/base.py",
        "src/parayabuck/portfolio/equity_residual_momentum.py",
        "src/parayabuck/portfolio/equity_residual_momentum_v3.py",
        "src/parayabuck/portfolio/models.py",
    )
    source_hashes: dict[str, Any] = {}
    for path_value in source_paths:
        declared = source_by_path.get(path_value, {})
        path = repo_root / path_value
        actual = sha256_file(path) if path.is_file() else None
        source_hashes[path_value] = {
            "role": declared.get("role"),
            "expected_sha256": declared.get("sha256"),
            "actual_sha256": actual,
            "matches": actual is not None and actual == declared.get("sha256"),
        }
    if not all(item["matches"] for item in source_hashes.values()):
        raise ValueError("frozen_source_hash_mismatch")
    config = config_semantics(repo_root / "config/settings.yaml")
    package_digest = package["coverage"]["schedule_linkage"]["canonical_digest_sha256"]
    first_event_in_window = _event_records_for_window(event_boundaries)
    current_timestamp_effect = _previous_timestamp_trace(trace_by_timestamp)
    diagnosis: dict[str, Any] = {
        "schema_version": "r46-v3-incumbent-decision-pipeline-diagnosis-v1",
        "candidate_id": "R46",
        "v3_research_id": "r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14",
        "status": "ROOT_CAUSE_STILL_UNPROVEN",
        "scope": {
            "classification": "OUTCOME_FREE_DECISION_GATE_DIAGNOSIS",
            "economic_execution": False,
            "engine_loop_invoked": False,
            "strategy_decide_invoked": False,
            "alpha_scores_computed": False,
            "portfolio_weights_computed": False,
            "performance_outcomes_computed": False,
            "real_historical_decision_path_replayed": False,
        },
        "source_commits": {
            "C_SRC": incident["accepted_commits"]["C_SRC"],
            "C_FREEZE": incident["accepted_commits"]["C_FREEZE"],
            "C_GATE": incident["accepted_commits"]["C_GATE"],
            "C_AUTH": incident["accepted_commits"]["C_AUTH"],
            "C_RESULT": incident["accepted_commits"]["C_RESULT"],
            "diagnostic_parent_commit": DIAGNOSTIC_PARENT_COMMIT,
        },
        "source_hashes": {
            "v3_failure_receipt": {
                "path": relative(repo_root / V3_FAILURE_RECEIPT, repo_root),
                "canonical_sha256": incident["integrity"]["incident_sha256"],
            },
            "v3_execution_contract": {
                "path": relative(repo_root / V3_CONTRACT, repo_root),
                "physical_sha256": incident["v3_execution_contract"]["physical_sha256"],
                "canonical_sha256": incident["v3_execution_contract"]["canonical_sha256"],
            },
            "v2_package": {
                "path": relative(repo_root / V2_PACKAGE, repo_root),
                "canonical_sha256": package_digest,
            },
            "incumbent_v5": {
                "path": relative(repo_root / INCUMBENT_BARS, repo_root),
                "physical_sha256": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
            },
            "source_files": source_hashes,
        },
        "preserved_evidence": preserved,
        "v3_failure": {
            "failure_commit": incident["accepted_commits"]["C_RESULT"],
            "runner_invocation_count": incident["execution"]["runner_invocation_count"],
            "runner_exit_code": incident["execution"]["runner_exit_code"],
            "terminal_error": FAILURE_ERROR,
            "reserved_sidecar_physical_sha256": incident["v3_reservation"]["physical_sha256"],
            "reserved_transition_sha256": incident["v3_reservation"]["transition_canonical_sha256"],
            "consumed_sidecar_present": incident["persisted_outputs"]["v3_consumed_sidecar_found"],
            "aborted_sidecar_present": incident["persisted_outputs"]["v3_aborted_sidecar_found"],
            "persisted_economic_outputs_present": False,
            "rerun_performed": False,
        },
        "frozen_semantics": {
            "config": config,
            "native_weekly": "previous is None or ISO year/week differs from current",
            "base_previous_timestamp_write": "write current timestamp before product/support/completeness gates",
            "product_guard_order": "product guard before strategy.supports in PortfolioBacktestEngine._decide",
            "minimum_history_bars": MINIMUM_HISTORY_BARS,
            "product_universe_methodology": UNIVERSE_METHOD,
            "observation_selection": (
                "build_observation_set reserves configured pins, then filters strategy ranker/fallback "
                "members by minimum history and benchmark latest timestamp"
            ),
            "r46_event_observation_override": "event decision timestamps return None from _effective_observation_symbols",
            "r46_event_target_requirement": "event branch requires _last_incumbent_target before event handling",
        },
        "analyzed_window": {
            "start": iso(WINDOW_START),
            "end": iso(WINDOW_END),
            "event_boundaries": first_event_in_window,
            "actual_processed_timestamp_count": len(trace),
            "actual_processed_timestamps": [row["timestamp"] for row in trace],
            "timestamp_sources": {
                "v5_rows_read": timeline.rows_read,
                "v5_unique_symbol_timestamp_pairs": timeline.unique_symbol_timestamp_pairs,
                "tlt_unique_regular_open_timestamps": len(tlt_timestamps),
                "event_marker_timestamps_in_window": len(first_event_in_window),
            },
        },
        "timestamp_gate_trace": trace,
        "feb06_deep_audit": _deep_feb06(timeline, trace_by_timestamp),
        "feb10_deep_audit": _deep_feb10(timeline, trace_by_timestamp, event_boundaries),
        "timeline_conclusions": {
            "first_history_supported_timestamp": iso(timeline.first_supported_timestamp),
            "first_history_supported_spy_history_count": timeline.spy_history_counts[timeline.first_supported_timestamp],
            "first_apparent_native_weekly_cadence_timestamp": iso(first_apparent),
            "first_actual_materializable_native_incumbent_target_timestamp": None,
            "first_actual_materializable_status": "NOT_PROVABLE_FROM_PERSISTED_METADATA",
            "first_actual_materializable_reason": (
                "The exact R46 observation membership and runtime universe hash were not persisted; "
                "therefore a history-supported or apparent cadence timestamp cannot be promoted to an "
                "actual materializable target."
            ),
            "feb03_to_feb06_data_alignment": {
                "feb03_symbol_count": len(feb03_symbols),
                "feb06_symbol_count": len(feb06_symbols),
                "feb03_minus_feb06": sorted(feb03_symbols - feb06_symbols),
                "feb06_minus_feb03": sorted(feb06_symbols - feb03_symbols),
                "not_exact_runtime_observation_membership": True,
            },
            "feb03_to_feb10_data_alignment": {
                "feb10_symbol_count": len(feb10_symbols),
                "feb03_minus_feb10": sorted(feb03_symbols - feb10_symbols),
                "feb10_minus_feb03": sorted(feb10_symbols - feb03_symbols),
            },
        },
        "previous_timestamp_scheduling_trace": current_timestamp_effect,
        "account_state": {
            "prior_incumbent_target_materialized": "NOT_PROVEN",
            "prior_incumbent_orders_possible": "NOT_PROVEN",
            "cash_only_state_proven": "NOT_PROVEN",
            "proof_status": "ACCOUNT_STATE_NOT_PROVEN",
            "strict_control_flow_evidence": (
                "The failing event proves _last_incumbent_target was None immediately before event handling; "
                "the missing Feb 6 gate record prevents attributing that absence to a specific pre-decide gate."
            ),
        },
        "root_cause": {
            "classification": "ROOT_CAUSE_STILL_UNPROVEN",
            "subclassification": "EXACT_R46_RUNTIME_OBSERVATION_AND_UNIVERSE_SNAPSHOT_NOT_PERSISTED",
            "proven": False,
            "evidence": [
                "minimum history is supported on 2017-02-02T05:00:00Z before the apparent 2017-02-06 cadence",
                "2017-02-06 is a native ISO-week boundary and its SPY history count is 276",
                "the frozen product construction has a non-empty product universe and runtime hash before the engine run; the exact hash and membership were not persisted",
                "exact streamed observation membership depends on the frozen strategy ranker output, which was not persisted and was not replayed",
                "the M3 observation artifacts are not the R46 config contract and were not used as substitutes",
                "2017-02-10 is an R46 extra boundary whose observation override bypasses the normal completeness set and reaches the missing-incumbent event branch",
                "no persisted Feb 6 decision-gate trace or completeness warning identifies the skipped gate",
            ],
            "rejected_hypothesis": "PRE_FIRST_NATIVE_INCUMBENT_TARGET_EVENT_BOUNDARY",
            "repair_selected": False,
        },
        "systemic_scan": {
            "scope": "FULL_FROZEN_TIMESTAMP_UNION_AFTER_MINIMUM_HISTORY_SUPPORT",
            "supported_weekly_cadence_count": len(supported_weekly),
            "pre_support_weekly_cadence_count": pre_support_weekly,
            "skipped_support": 0,
            "skipped_product_universe": 0,
            "skipped_observation_completeness": None,
            "other_pre_decide_skipped": None,
            "would_reach_strategy_decide": None,
            "materializable": None,
            "unresolved_supported_weekly_gate_count": len(supported_weekly),
            "r46_boundaries_in_window": len(first_event_in_window),
            "r46_boundaries_before_first_actual_materializable_target": {
                "count": len(first_event_in_window),
                "status": "UNPROVEN_FIRST_ACTUAL_TARGET_NOT_IDENTIFIED",
            },
        },
        "execution_boundary": {
            "v3_rerun": False,
            "v4_runner": False,
            "real_strategy_decision": False,
            "real_engine_run": False,
            "performance_outcomes": False,
            "orders_or_fills": False,
            "v2_v3_reservations_modified": False,
            "parameters_changed": False,
            "paper_live_registry_changed": False,
            "repair_implemented": False,
        },
        "repair": {
            "selected": False,
            "recommendation": "Repair design deferred to independent orchestrator review.",
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
    print(json.dumps({"status": diagnosis["status"], "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
