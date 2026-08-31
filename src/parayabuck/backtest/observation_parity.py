"""Research-only comparison helpers for candidate-owned PIT observation bases.

The production scanner remains the sole owner of eligibility.  This module
only compares two already-built :class:`PointInTimeUniverse` objects and
provides deterministic strategy identity/digest helpers for research receipts.
It has no runtime or product-registry dependencies.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Any, cast

from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot, hash_point_in_time_universe
from parayabuck.portfolio.base import PortfolioStrategy

_ALLOWED_BASIS_DIFFERENCES = (
    "observation_set",
    "observation_strategy_name",
    "observation_strategy_version",
    "resulting_universe_hash",
)


def _normalise(value: Any) -> Any:
    """Return stable JSON-compatible data for research fingerprints."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).isoformat()
        return value.isoformat()
    if isinstance(value, Enum):
        return _normalise(value.value)
    if dataclasses.is_dataclass(value):
        return _normalise(dataclasses.asdict(cast(Any, value)))
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (set, frozenset)):
        normalised = [_normalise(item) for item in value]
        return sorted(normalised, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    if isinstance(value, (tuple, list)):
        return [_normalise(item) for item in value]
    if isinstance(value, float) and value == 0.0:
        # Canonicalize both signs of zero before serializing a receipt.
        return 0.0
    return value


def canonical_digest(value: Any) -> str:
    """Hash receipt values independently of mapping order or tuple/list form."""
    payload = json.dumps(_normalise(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def strategy_identity(strategy: PortfolioStrategy) -> dict[str, object]:
    """Return the immutable identity/fingerprint used by parity receipts."""
    if not isinstance(strategy, PortfolioStrategy):
        raise TypeError("strategy must be a PortfolioStrategy")
    parameters = strategy.parameter_tuple()
    return {
        "name": strategy.name,
        "version": strategy.version,
        "market": strategy.market.value,
        "timeframe": strategy.timeframe,
        "parameters": _normalise(parameters),
        "parameter_digest_sha256": canonical_digest(parameters),
    }


def assert_strategy_identity(
    observation_strategy: PortfolioStrategy,
    execution_strategy: PortfolioStrategy,
) -> None:
    """Fail closed unless ranker and execution strategy identities are exact."""
    if observation_strategy is execution_strategy:
        raise ValueError("Observation ranker and execution strategy must be fresh instances")
    observation = strategy_identity(observation_strategy)
    execution = strategy_identity(execution_strategy)
    for field in ("name", "version", "market", "timeframe", "parameters", "parameter_digest_sha256"):
        if observation[field] != execution[field]:
            raise ValueError(f"Observation and execution strategy {field} mismatch")


def _snapshot_sequence(universe: object) -> tuple[UniverseSnapshot, ...]:
    snapshots = getattr(universe, "snapshots", ())
    if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
        raise TypeError("Point-in-time universe snapshots must be a sequence")
    return tuple(snapshots)


def _timestamp_key(value: object) -> str:
    if not isinstance(value, datetime):
        raise TypeError("Universe snapshot effective_at must be a datetime")
    return str(_normalise(value))


def _snapshot_field(snapshot: UniverseSnapshot, field: str, default: object = None) -> object:
    return getattr(snapshot, field, default)


def _as_sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, (tuple, list, set, frozenset)):
        return tuple(value)
    return ()


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    return 1.0 if not union else len(left_set & right_set) / len(union)


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 1.0
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _snapshot_quality(snapshot: UniverseSnapshot) -> dict[str, int]:
    observation = tuple(str(item) for item in _as_sequence(_snapshot_field(snapshot, "observation_set", ())))
    symbols = set(_as_sequence(_snapshot_field(snapshot, "symbols", frozenset())))
    pins = tuple(str(item) for item in _as_sequence(_snapshot_field(snapshot, "pinned_symbols", ())))
    raw_capacity = _snapshot_field(snapshot, "maximum_stream_symbols", 0)
    capacity = int(raw_capacity) if isinstance(raw_capacity, (int, float, str)) else 0
    return {
        "observation_duplicate_count": len(observation) - len(set(observation)),
        "observation_outside_pool_count": sum(symbol not in symbols for symbol in observation),
        "observation_over_capacity_count": int(capacity > 0 and len(observation) > capacity),
        "pins_missing_from_observation_count": sum(symbol not in observation for symbol in pins),
        "pins_not_first_count": int(bool(pins) and tuple(observation[: len(pins)]) != pins),
    }


def _identity_from_snapshots(snapshots: Sequence[UniverseSnapshot]) -> dict[str, object]:
    names = sorted({str(_snapshot_field(snapshot, "observation_strategy_name", "")) for snapshot in snapshots})
    versions = sorted({str(_snapshot_field(snapshot, "observation_strategy_version", "")) for snapshot in snapshots})
    return {"names": names, "versions": versions}


def compare_universe_bases(
    control: PointInTimeUniverse,
    candidate: PointInTimeUniverse,
    *,
    control_strategy: PortfolioStrategy | None = None,
    candidate_strategy: PortfolioStrategy | None = None,
) -> dict[str, object]:
    """Compare two PIT universes while allowing only observation-basis drift.

    Snapshot boundaries are matched by their ``effective_at`` timestamp, never
    by list index. Missing, extra, duplicate, or reordered boundaries fail
    closed. Every scanner/pool/metadata/pin/capacity field must be identical;
    only observation membership/order and its recorded strategy identity may
    differ.
    """
    control_snapshots = _snapshot_sequence(control)
    candidate_snapshots = _snapshot_sequence(candidate)
    control_keys = tuple(_timestamp_key(_snapshot_field(snapshot, "effective_at")) for snapshot in control_snapshots)
    candidate_keys = tuple(
        _timestamp_key(_snapshot_field(snapshot, "effective_at")) for snapshot in candidate_snapshots
    )
    control_key_set = set(control_keys)
    candidate_key_set = set(candidate_keys)
    duplicate_control = len(control_keys) != len(control_key_set)
    duplicate_candidate = len(candidate_keys) != len(candidate_key_set)
    missing = tuple(sorted(control_key_set - candidate_key_set))
    extra = tuple(sorted(candidate_key_set - control_key_set))
    reordered = not missing and not extra and control_keys != candidate_keys

    control_by_key = {key: snapshot for key, snapshot in zip(control_keys, control_snapshots, strict=True)}
    candidate_by_key = {key: snapshot for key, snapshot in zip(candidate_keys, candidate_snapshots, strict=True)}
    aligned_keys = tuple(key for key in control_keys if key in candidate_by_key)

    mismatch_counts: dict[str, int] = {
        "market": 0,
        "methodology": 0,
        "eligible_symbols": 0,
        "scanner_version": 0,
        "scanner_config_digest": 0,
        "candidate_count": 0,
        "score_breakdown": 0,
        "eliminated_map": 0,
        "sector_metadata_coverage": 0,
        "configured_pins": 0,
        "maximum_stream_capacity": 0,
        "snapshot_boundaries": int(bool(missing or extra or reordered or duplicate_control or duplicate_candidate)),
    }
    observations_changed = 0
    observation_membership_changed = 0
    jaccards: list[float] = []
    control_quality = {key: 0 for key in _snapshot_quality(control_snapshots[0])} if control_snapshots else {}
    candidate_quality = {key: 0 for key in _snapshot_quality(candidate_snapshots[0])} if candidate_snapshots else {}

    for key in aligned_keys:
        left = control_by_key[key]
        right = candidate_by_key[key]
        comparisons = {
            "eligible_symbols": (
                _snapshot_field(left, "symbols", frozenset()),
                _snapshot_field(right, "symbols", frozenset()),
            ),
            "scanner_version": (
                _snapshot_field(left, "scanner_version", ""),
                _snapshot_field(right, "scanner_version", ""),
            ),
            "scanner_config_digest": (
                _snapshot_field(left, "config_digest", ""),
                _snapshot_field(right, "config_digest", ""),
            ),
            "candidate_count": (
                _snapshot_field(left, "candidate_count", 0),
                _snapshot_field(right, "candidate_count", 0),
            ),
            "score_breakdown": (
                _snapshot_field(left, "score_breakdown", {}),
                _snapshot_field(right, "score_breakdown", {}),
            ),
            "eliminated_map": (_snapshot_field(left, "eliminated", {}), _snapshot_field(right, "eliminated", {})),
            "sector_metadata_coverage": (
                _snapshot_field(left, "sector_metadata_coverage", 1.0),
                _snapshot_field(right, "sector_metadata_coverage", 1.0),
            ),
            "configured_pins": (
                _snapshot_field(left, "pinned_symbols", ()),
                _snapshot_field(right, "pinned_symbols", ()),
            ),
            "maximum_stream_capacity": (
                _snapshot_field(left, "maximum_stream_symbols", 0),
                _snapshot_field(right, "maximum_stream_symbols", 0),
            ),
        }
        for field, (left_value, right_value) in comparisons.items():
            if _normalise(left_value) != _normalise(right_value):
                mismatch_counts[field] += 1
        left_observation = tuple(str(item) for item in _as_sequence(_snapshot_field(left, "observation_set", ())))
        right_observation = tuple(str(item) for item in _as_sequence(_snapshot_field(right, "observation_set", ())))
        if left_observation != right_observation:
            observations_changed += 1
        if set(left_observation) != set(right_observation):
            observation_membership_changed += 1
        jaccards.append(_jaccard(left_observation, right_observation))
        for field, value in _snapshot_quality(left).items():
            control_quality[field] += value
        for field, value in _snapshot_quality(right).items():
            candidate_quality[field] += value

    try:
        control_hash = hash_point_in_time_universe(control)
        candidate_hash = hash_point_in_time_universe(candidate)
    except (AttributeError, TypeError, ValueError):
        control_hash = None
        candidate_hash = None

    market_equal = _normalise(getattr(control, "market", None)) == _normalise(getattr(candidate, "market", None))
    methodology_equal = getattr(control, "methodology", None) == getattr(candidate, "methodology", None)
    if not market_equal:
        mismatch_counts["market"] += 1
    if not methodology_equal:
        mismatch_counts["methodology"] += 1

    control_identity = (
        strategy_identity(control_strategy)
        if control_strategy is not None
        else _identity_from_snapshots(control_snapshots)
    )
    candidate_identity = (
        strategy_identity(candidate_strategy)
        if candidate_strategy is not None
        else _identity_from_snapshots(candidate_snapshots)
    )
    disallowed_mismatches = sum(mismatch_counts.values())
    no_basis_difference = (
        observations_changed == 0
        and control_identity == candidate_identity
        and not (missing or extra or reordered or duplicate_control or duplicate_candidate)
    )
    hash_unexpected = bool(no_basis_difference and control_hash != candidate_hash)
    failure_reasons: list[str] = []
    if mismatch_counts["snapshot_boundaries"]:
        failure_reasons.append("snapshot_boundaries_not_aligned")
    if disallowed_mismatches:
        failure_reasons.append("scanner_or_universe_metadata_changed")
    if any(control_quality.values()) or any(candidate_quality.values()):
        failure_reasons.append("observation_set_contract_invalid")
    if control_hash is None or candidate_hash is None:
        failure_reasons.append("universe_hash_unavailable")
    elif hash_unexpected:
        failure_reasons.append("universe_hash_changed_without_allowed_basis_change")

    return {
        "schema_version": "1.0.0",
        "passed": not failure_reasons,
        "allowed_difference_fields": list(_ALLOWED_BASIS_DIFFERENCES),
        "snapshot_count": {
            "control": len(control_snapshots),
            "candidate": len(candidate_snapshots),
            "aligned": len(aligned_keys),
        },
        "effective_at_alignment": {
            "equal_sequence": control_keys == candidate_keys,
            "missing_from_candidate": list(missing),
            "extra_in_candidate": list(extra),
            "reordered": reordered,
            "duplicate_control": duplicate_control,
            "duplicate_candidate": duplicate_candidate,
        },
        "mismatch_counts": mismatch_counts,
        "observation_set": {
            "sequence_change_count": observations_changed,
            "membership_change_count": observation_membership_changed,
            "jaccard_count": len(jaccards),
            "jaccard_mean": sum(jaccards) / len(jaccards) if jaccards else 1.0,
            "jaccard_min": min(jaccards) if jaccards else 1.0,
            "jaccard_p50": _percentile(jaccards, 0.50),
            "jaccard_p95": _percentile(jaccards, 0.95),
        },
        "observation_contract": {"control": control_quality, "candidate": candidate_quality},
        "control_observation_strategy": control_identity,
        "candidate_observation_strategy": candidate_identity,
        "control_universe_hash": control_hash,
        "candidate_universe_hash": candidate_hash,
        "failure_reasons": failure_reasons,
    }


def compare_point_in_time_universes(
    control: PointInTimeUniverse,
    candidate: PointInTimeUniverse,
    *,
    control_strategy: PortfolioStrategy | None = None,
    candidate_strategy: PortfolioStrategy | None = None,
) -> dict[str, object]:
    """Descriptive alias for :func:`compare_universe_bases`."""
    return compare_universe_bases(
        control,
        candidate,
        control_strategy=control_strategy,
        candidate_strategy=candidate_strategy,
    )


__all__ = [
    "assert_strategy_identity",
    "canonical_digest",
    "compare_point_in_time_universes",
    "compare_universe_bases",
    "strategy_identity",
]
