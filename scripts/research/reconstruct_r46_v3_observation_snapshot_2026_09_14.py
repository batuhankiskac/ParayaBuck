#!/usr/bin/env python3
"""Reconstruct the frozen R46 v3 point-in-time observation snapshots.

This is a read-only provenance diagnostic.  It uses the exact V5 loader and
the exact frozen R46 universe builder, but never constructs an economic
engine, invokes a strategy decision, or emits price-derived results.  The
child-process mode is intentionally small: it returns only structural
universe identities and snapshot metadata so two independent hash seeds can
be compared byte-for-byte.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
V2_MODULE_NAME = "scripts.research.r46_treasury_auction_unwind_tlt_v2_2026_09_10"
V3_RUNNER = Path("scripts/research/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.py")
V2_PACKAGE = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json")
V2_GOVERNANCE = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.json")
V3_CONTRACT = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_execution_contract.json"
)
V3_FAILURE_RECEIPT = Path("data/research_receipts/r46_v3_execution_failure_incident_2026_09_14.json")
V3_PRIOR_DIAGNOSIS = Path("data/research_receipts/r46_v3_incumbent_base_failure_diagnosis_2026_09_14.json")
OUTPUT_RECEIPT = Path("data/research_receipts/r46_v3_observation_snapshot_reconstruction_2026_09_14.json")
OUTPUT_DOC = Path("docs/research/r46_v3_observation_snapshot_reconstruction_2026_09_14.md")

FEB06 = datetime(2017, 2, 6, 5, tzinfo=UTC)
FEB10 = datetime(2017, 2, 10, 5, tzinfo=UTC)
WINDOW_START = datetime(2017, 2, 1, tzinfo=UTC)
WINDOW_END = datetime(2017, 2, 13, 23, 59, 59, tzinfo=UTC)
MINIMUM_HISTORY_BARS = 274
CONTEXT_HISTORY_LIMIT = 274
EXPECTED_CONTRACT_PHYSICAL_SHA256 = "98114b212997f86b72cf0b3b3de6841ef990050ce6401b771b81225ee3d8e825"
EXPECTED_CONTRACT_CANONICAL_SHA256 = "95b18f4f14673448dc584ae614587e9a095b698206a8f2868ad9bef8ff502e58"
EXPECTED_PACKAGE_CANONICAL_SHA256 = "c67cfdfeebc4cbc0847d5e2e6fbdebdf6928e04b3e5103606eb8bd0c3d03f6c6"
EXPECTED_GOVERNANCE_CANONICAL_SHA256 = "92f7ecd5b7f6cdd2428ee221c3b4891f10d5d7ab6f16a5fcb2be952753557c35"
FAILURE_COMMIT = "6d01cade2e284905b13a2cb6e1ef4b325b3134a6"
FAILURE_ERROR = '{"error": "BLOCKED_R46_INCUMBENT_BASE_MISSING", "status": "BLOCKED"}'
FAILURE_EVENT_ID = "2017-02-09:912810RV2"
EXPECTED_BASE_COMMIT = "9d2d055125c41cf6266613c9494c0b30bbede592"
EXPECTED_COST_MULTIPLIERS = (1.0, 2.0, 3.0)

ROOT_CAUSE_CLASSES = (
    "FIRST_SUPPORTED_WEEKLY_CADENCE_SKIPPED_OBSERVATION_COMPLETENESS",
    "FIRST_SUPPORTED_WEEKLY_CADENCE_SKIPPED_PRODUCT_UNIVERSE",
    "FIRST_SUPPORTED_WEEKLY_CADENCE_SKIPPED_SUPPORT",
    "FIRST_SUPPORTED_WEEKLY_CADENCE_SKIPPED_OTHER_PRE_DECIDE_GATE",
    "DETERMINISTIC_RECONSTRUCTION_CONTRADICTS_OBSERVED_EXECUTION",
    "UNIVERSE_RECONSTRUCTION_NONDETERMINISTIC",
    "R46_UNIVERSE_WRAPPER_PARITY_FAILURE",
    "ROOT_CAUSE_STILL_UNPROVEN",
)


class ReconstructionStop(RuntimeError):
    """A fail-closed stop with a user-visible classification."""

    def __init__(self, classification: str, detail: str) -> None:
        super().__init__(f"{classification}: {detail}")
        self.classification = classification
        self.detail = detail


def aware(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return parsed.astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def canonical_without_self(value: Mapping[str, Any], field: str) -> str:
    payload = copy.deepcopy(dict(value))
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("integrity_object_required")
    integrity.pop(field, None)
    return sha256_bytes(payload)


def canonical_with_pending_self(value: Mapping[str, Any], field: str) -> str:
    payload = copy.deepcopy(dict(value))
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("integrity_object_required")
    integrity[field] = "PENDING"
    return sha256_bytes(payload)


def git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _relative(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _drift(detail: str) -> ReconstructionStop:
    return ReconstructionStop("RECONSTRUCTION_SOURCE_DRIFT", detail)


def _verify_declared_file(path: Path, expected: str, root: Path, role: str) -> dict[str, object]:
    if not path.is_file():
        raise _drift(f"{role}:missing:{_relative(path, root)}")
    actual = sha256_file(path)
    if actual != expected:
        raise _drift(f"{role}:sha256:{_relative(path, root)}:{actual}!={expected}")
    return {
        "path": _relative(path, root),
        "expected_sha256": expected,
        "actual_sha256": actual,
        "byte_size": path.stat().st_size,
        "matches": True,
        "role": role,
    }


def verify_source_pins(root: Path) -> dict[str, object]:
    """Verify the contract, V5 data, package, and all frozen source pins."""

    contract_path = (root / V3_CONTRACT).resolve()
    if sha256_file(contract_path) != EXPECTED_CONTRACT_PHYSICAL_SHA256:
        raise _drift("execution_contract:physical_sha256")
    contract = load_json(contract_path)
    if contract.get("schema_version") != "r46-v3-recovery-execution-contract-v1":
        raise _drift("execution_contract:schema")
    integrity = contract.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("contract_sha256") != EXPECTED_CONTRACT_CANONICAL_SHA256:
        raise _drift("execution_contract:declared_canonical_sha256")
    if canonical_without_self(contract, "contract_sha256") != EXPECTED_CONTRACT_CANONICAL_SHA256:
        raise _drift("execution_contract:canonical_sha256")

    source_rows = contract.get("source_files")
    if not isinstance(source_rows, list):
        raise _drift("execution_contract:source_files")
    source_pins: list[dict[str, object]] = []
    source_by_path: dict[str, dict[str, Any]] = {}
    for raw in source_rows:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("path"), str) or not isinstance(raw.get("sha256"), str):
            raise _drift("execution_contract:source_pin_shape")
        relative_path = str(raw["path"])
        pin = _verify_declared_file(root / relative_path, str(raw["sha256"]), root, str(raw.get("role", "source")))
        pin["git_blob_sha256"] = raw.get("git_blob_sha256")
        source_pins.append(pin)
        source_by_path[relative_path] = dict(raw)

    required_sources = {
        str(V3_RUNNER),
        "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "src/parayabuck/backtest/product.py",
        "src/parayabuck/backtest/universe.py",
        "src/parayabuck/backtest/portfolio_engine.py",
        "src/parayabuck/config.py",
        "src/parayabuck/portfolio/equity_residual_momentum_v3.py",
        "src/parayabuck/universe/observation_set.py",
    }
    missing_sources = sorted(required_sources - set(source_by_path))
    if missing_sources:
        raise _drift(f"execution_contract:required_sources_missing:{','.join(missing_sources)}")

    package_path = (root / V2_PACKAGE).resolve()
    package = load_json(package_path)
    package_integrity = package.get("integrity")
    package_hash = (
        canonical_without_self(package, "manifest_sha256")
        if isinstance(package_integrity, Mapping) and isinstance(package_integrity.get("manifest_sha256"), str)
        else ""
    )
    parent_v2 = contract.get("parent_v2_evidence")
    if not isinstance(parent_v2, Mapping):
        raise _drift("parent_v2_evidence:missing")
    if package_hash != EXPECTED_PACKAGE_CANONICAL_SHA256 or package_hash != parent_v2.get("package_sha256"):
        raise _drift("v2_package:canonical_sha256")
    package_pin = {
        "path": _relative(package_path, root),
        "physical_sha256": sha256_file(package_path),
        "canonical_sha256": package_hash,
        "expected_canonical_sha256": EXPECTED_PACKAGE_CANONICAL_SHA256,
        "matches": True,
    }

    governance_path = (root / V2_GOVERNANCE).resolve()
    governance = load_json(governance_path)
    governance_hash = canonical_without_self(governance, "manifest_sha256")
    if governance_hash != EXPECTED_GOVERNANCE_CANONICAL_SHA256 or governance_hash != parent_v2.get("governance_sha256"):
        raise _drift("v2_governance:canonical_sha256")

    package_contract = package.get("incumbent_dependencies")
    governance_data_contract = governance.get("data_contract", {})
    governance_contract = (
        governance_data_contract.get("incumbent_data_contract")
        if isinstance(governance_data_contract, Mapping)
        else None
    )
    if not isinstance(package_contract, Mapping) or package_contract != governance_contract:
        raise _drift("v5_data_contract:package_governance_mismatch")

    data_pins: dict[str, dict[str, object]] = {}
    for key in ("bars", "lifetimes", "corporate_actions", "bars_manifest", "corporate_actions_manifest", "seam"):
        spec = package_contract.get(key)
        if not isinstance(spec, Mapping) or not isinstance(spec.get("path"), str):
            raise _drift(f"v5_data_contract:{key}:shape")
        path = (root / str(spec["path"])).resolve()
        expected = str(spec.get("sha256", spec.get("raw_sha256", "")))
        if not expected:
            raise _drift(f"v5_data_contract:{key}:hash_missing")
        pin = _verify_declared_file(path, expected, root, f"V5 {key}")
        declared_size = spec.get("byte_size")
        if declared_size is not None and pin["byte_size"] != declared_size:
            raise _drift(f"v5_data_contract:{key}:byte_size")
        data_pins[key] = pin

    return {
        "reconstruction_head": git_head(root),
        "required_base_commit": EXPECTED_BASE_COMMIT,
        "execution_contract": {
            "path": _relative(contract_path, root),
            "physical_sha256": sha256_file(contract_path),
            "canonical_sha256": EXPECTED_CONTRACT_CANONICAL_SHA256,
            "source_commit": contract.get("source_commit"),
            "freeze_commit": contract.get("freeze_commit"),
        },
        "source_files": source_pins,
        "v5_inputs": data_pins,
        "v2_package": package_pin,
        "v2_governance": {
            "path": _relative(governance_path, root),
            "physical_sha256": sha256_file(governance_path),
            "canonical_sha256": governance_hash,
        },
        "historical_cutoff": package_contract.get("historical_cutoff"),
        "contract_source_commit": contract.get("source_commit"),
        "contract_freeze_commit": contract.get("freeze_commit"),
    }


def verify_preserved_failure_evidence(root: Path) -> dict[str, object]:
    incident_path = (root / V3_FAILURE_RECEIPT).resolve()
    prior_path = (root / V3_PRIOR_DIAGNOSIS).resolve()
    incident = load_json(incident_path)
    prior = load_json(prior_path)
    incident_integrity = incident.get("integrity")
    prior_integrity = prior.get("integrity")
    if not isinstance(incident_integrity, Mapping) or not isinstance(prior_integrity, Mapping):
        raise ReconstructionStop("PRESERVED_EVIDENCE_DRIFT", "missing preserved receipt integrity")
    incident_field = "incident_sha256"
    incident_declared = incident_integrity.get(incident_field)
    prior_declared = prior_integrity.get("diagnosis_sha256")
    incident_hashes = {
        "pending": canonical_with_pending_self(incident, incident_field),
        "omission": canonical_without_self(incident, incident_field),
    }
    prior_hashes = {
        "pending": canonical_with_pending_self(prior, "diagnosis_sha256"),
        "omission": canonical_without_self(prior, "diagnosis_sha256"),
    }
    incident_self_matches = incident_declared in incident_hashes.values()
    prior_self_matches = prior_declared in prior_hashes.values()
    execution = incident.get("execution")
    accepted = incident.get("accepted_commits")
    prior_root_cause = prior.get("root_cause")
    if not isinstance(execution, Mapping) or not isinstance(accepted, Mapping) or not isinstance(prior_root_cause, Mapping):
        raise ReconstructionStop("PRESERVED_EVIDENCE_DRIFT", "preserved receipt shape")
    matches_failure = (
        accepted.get("C_RESULT") == FAILURE_COMMIT
        and execution.get("terminal_error") == FAILURE_ERROR
        and execution.get("runner_invocation_count") == 1
        and execution.get("persisted_economic_completion") is False
    )
    if not incident_self_matches or not prior_self_matches or not matches_failure:
        raise ReconstructionStop("PRESERVED_EVIDENCE_DRIFT", "preserved incident or prior diagnosis changed")
    if prior_root_cause.get("classification") != "ROOT_CAUSE_NOT_PROVEN":
        raise ReconstructionStop("PRESERVED_EVIDENCE_DRIFT", "prior diagnosis classification changed")
    return {
        "incident_path": _relative(incident_path, root),
        "incident_self_hash_declared": incident_declared,
        "incident_self_hash_matches": incident_self_matches,
        "prior_diagnosis_path": _relative(prior_path, root),
        "prior_diagnosis_self_hash_declared": prior_declared,
        "prior_diagnosis_self_hash_matches": prior_self_matches,
        "prior_diagnosis_classification": prior_root_cause.get("classification"),
        "failure_commit": accepted.get("C_RESULT"),
        "terminal_error": execution.get("terminal_error"),
        "runner_invocation_count": execution.get("runner_invocation_count"),
        "persisted_economic_completion": execution.get("persisted_economic_completion"),
        "real_processing_boundary": execution.get("nonpersisted_historical_processing"),
        "rerun_forbidden": incident.get("governance", {}).get("v3_rerun_forbidden") is True
        if isinstance(incident.get("governance"), Mapping)
        else None,
        "matches_preserved_failure": True,
    }


@dataclass(frozen=True)
class StructuralSnapshot:
    effective_at: datetime
    symbols: tuple[str, ...]
    observation_set: tuple[str, ...]
    maximum_stream_symbols: int
    pinned_symbols: tuple[str, ...]
    observation_strategy_name: str
    observation_strategy_version: str
    scanner_version: str
    config_digest: str
    candidate_count: int


@dataclass(frozen=True)
class StructuralUniverse:
    snapshots: tuple[StructuralSnapshot, ...]
    methodology: str
    full_hash: str

    def contains(self, symbol: str, market: object, timestamp: datetime) -> bool:
        market_value = getattr(market, "value", market)
        if str(market_value) != "equity":
            return False
        snapshot_raw = latest_snapshot_at_or_before(self.snapshots, timestamp)
        if not isinstance(snapshot_raw, StructuralSnapshot):
            return False
        snapshot = snapshot_raw
        return snapshot is not None and symbol.strip().upper() in snapshot.symbols


def snapshot_from_raw(raw: Mapping[str, Any]) -> StructuralSnapshot:
    return StructuralSnapshot(
        effective_at=aware(str(raw["effective_at"])),
        symbols=tuple(str(item) for item in raw.get("symbols", ())),
        observation_set=tuple(str(item) for item in raw.get("observation_set", ())),
        maximum_stream_symbols=int(raw.get("maximum_stream_symbols", 0)),
        pinned_symbols=tuple(str(item) for item in raw.get("pinned_symbols", ())),
        observation_strategy_name=str(raw.get("observation_strategy_name", "")),
        observation_strategy_version=str(raw.get("observation_strategy_version", "")),
        scanner_version=str(raw.get("scanner_version", "")),
        config_digest=str(raw.get("config_digest", "")),
        candidate_count=int(raw.get("candidate_count", 0)),
    )


def snapshot_structural_dict(snapshot: object) -> dict[str, object]:
    effective_at = getattr(snapshot, "effective_at")
    return {
        "effective_at": iso(effective_at),
        "symbols": sorted(str(item) for item in getattr(snapshot, "symbols", ())),
        "observation_set": [str(item) for item in getattr(snapshot, "observation_set", ())],
        "maximum_stream_symbols": int(getattr(snapshot, "maximum_stream_symbols", 0)),
        "pinned_symbols": [str(item) for item in getattr(snapshot, "pinned_symbols", ())],
        "observation_strategy_name": str(getattr(snapshot, "observation_strategy_name", "")),
        "observation_strategy_version": str(getattr(snapshot, "observation_strategy_version", "")),
        "scanner_version": str(getattr(snapshot, "scanner_version", "")),
        "config_digest": str(getattr(snapshot, "config_digest", "")),
        "candidate_count": int(getattr(snapshot, "candidate_count", 0)),
    }


def snapshot_digest(snapshot: object) -> str:
    return sha256_bytes(snapshot_structural_dict(snapshot))


def ordered_observation_digest(observation_set: Sequence[str]) -> str:
    return sha256_bytes([str(symbol) for symbol in observation_set])


def all_observation_digest(snapshots: Sequence[object]) -> str:
    return sha256_bytes(
        [
            [iso(getattr(snapshot, "effective_at")), list(getattr(snapshot, "observation_set", ())) ]
            for snapshot in sorted(snapshots, key=lambda item: getattr(item, "effective_at"))
        ]
    )


def latest_snapshot_at_or_before(
    snapshots: Sequence[object], timestamp: datetime
) -> object | None:
    ordered = tuple(sorted(snapshots, key=lambda item: getattr(item, "effective_at")))
    index = bisect_right(ordered, timestamp, key=lambda item: getattr(item, "effective_at"))
    return ordered[index - 1] if index else None


def serialized_universe(universe: object, full_hash: str) -> dict[str, object]:
    snapshots = tuple(sorted(getattr(universe, "snapshots", ()), key=lambda item: getattr(item, "effective_at")))
    target_snapshots: dict[str, dict[str, object]] = {}
    for requested in (FEB06, FEB10):
        snapshot = latest_snapshot_at_or_before(snapshots, requested)
        if snapshot is None:
            raise ReconstructionStop("RECONSTRUCTION_SOURCE_DRIFT", f"missing snapshot at {iso(requested)}")
        target_snapshots[iso(requested) or ""] = snapshot_structural_dict(snapshot)
    identity = {
        "full_universe_hash": full_hash,
        "snapshot_count": len(snapshots),
        "feb06_snapshot_digest": snapshot_digest(latest_snapshot_at_or_before(snapshots, FEB06)),
        "feb10_snapshot_digest": snapshot_digest(latest_snapshot_at_or_before(snapshots, FEB10)),
        "observation_set_digest": all_observation_digest(snapshots),
    }
    return {
        "identity": identity,
        "methodology": str(getattr(universe, "methodology", "")),
        "snapshot_projection_upper_bound": iso(WINDOW_END),
        "snapshots": [
            snapshot_structural_dict(snapshot)
            for snapshot in snapshots
            if getattr(snapshot, "effective_at") <= WINDOW_END
        ],
        "target_snapshots": target_snapshots,
        "identity_bytes_sha256": sha256_bytes(identity),
        "contains_callable": callable(getattr(universe, "contains", None)),
    }


def direct_universe(v2: Any, incumbent: Any, config: Any) -> object:
    from dataclasses import replace

    from parayabuck.backtest.product import build_product_universe
    from parayabuck.core.models import Market

    bars = [replace(bar, timeframe="1d") for bar in incumbent.bars if bar.symbol != "TLT"]
    end = max(bar.timestamp for bar in bars)
    return build_product_universe(config, bars, market=Market.EQUITY, end=end, timeframe="1d")


def build_worker_universes(root: Path, include_direct: bool) -> dict[str, object]:
    v2 = importlib.import_module(V2_MODULE_NAME)
    load_incumbent = getattr(v2, "load_incumbent_pit_inputs")
    load_config = getattr(v2, "_load_r46_config")
    build_wrapper = getattr(v2, "_build_r46_universe")
    universe_module = importlib.import_module("parayabuck.backtest.universe")
    hash_universe = getattr(universe_module, "hash_point_in_time_universe")
    incumbent = load_incumbent(repo_root=root)
    config = load_config(None, repo_root=root, test_only=False)
    if config is None:
        raise ReconstructionStop("RECONSTRUCTION_SOURCE_DRIFT", "frozen config loader returned None")
    universe, version, status = build_wrapper(incumbent, config=config, test_only=False)
    if universe is None or status != "PIT_V5_PRODUCT_UNIVERSE" or not version:
        raise ReconstructionStop("RECONSTRUCTION_SOURCE_DRIFT", f"PIT universe status={status!r}")
    wrapper = serialized_universe(universe, hash_universe(universe))
    result: dict[str, object] = {"wrapper": wrapper}
    if include_direct:
        direct = direct_universe(v2, incumbent, config)
        result["direct"] = serialized_universe(direct, hash_universe(direct))
    return result


def run_worker(root: Path, seed: int, include_direct: bool) -> dict[str, object]:
    command = [sys.executable, str(Path(__file__).resolve()), "--worker"]
    if include_direct:
        command.append("--include-direct")
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(seed)
    environment["PYTHONUNBUFFERED"] = "1"
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stdout + completed.stderr).strip()[-2000:]
        if "RECONSTRUCTION_SOURCE_DRIFT" in detail:
            raise _drift(f"worker_seed_{seed}:{detail}")
        raise ReconstructionStop("UNIVERSE_RECONSTRUCTION_FAILED", f"worker_seed_{seed}:{detail}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ReconstructionStop("UNIVERSE_RECONSTRUCTION_FAILED", f"worker_seed_{seed}:non_structural_output")
    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ReconstructionStop("UNIVERSE_RECONSTRUCTION_FAILED", f"worker_seed_{seed}:invalid_json") from exc
    if not isinstance(value, dict) or not isinstance(value.get("wrapper"), Mapping):
        raise ReconstructionStop("UNIVERSE_RECONSTRUCTION_FAILED", f"worker_seed_{seed}:identity_missing")
    value["python_hash_seed"] = seed
    return value


def compare_process_identities(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, object]:
    first_wrapper = first.get("wrapper")
    second_wrapper = second.get("wrapper")
    if not isinstance(first_wrapper, Mapping) or not isinstance(second_wrapper, Mapping):
        raise ReconstructionStop("UNIVERSE_RECONSTRUCTION_NONDETERMINISTIC", "worker wrapper identity missing")
    first_identity = first_wrapper.get("identity")
    second_identity = second_wrapper.get("identity")
    if not isinstance(first_identity, Mapping) or not isinstance(second_identity, Mapping):
        raise ReconstructionStop("UNIVERSE_RECONSTRUCTION_NONDETERMINISTIC", "worker identity missing")
    first_bytes = canonical_bytes(first_identity)
    second_bytes = canonical_bytes(second_identity)
    identical = first_bytes == second_bytes
    if not identical:
        raise ReconstructionStop("UNIVERSE_RECONSTRUCTION_NONDETERMINISTIC", "hash-seeded identities differ")
    return {
        "hash_seeds": [first.get("python_hash_seed"), second.get("python_hash_seed")],
        "process_count": 2,
        "identities_byte_identical": True,
        "identity_sha256": hashlib.sha256(first_bytes).hexdigest(),
        "identities": [dict(first_identity), dict(second_identity)],
        "heads": [first.get("head"), second.get("head")],
    }


def verify_direct_parity(first: Mapping[str, Any]) -> dict[str, object]:
    wrapper = first.get("wrapper")
    direct = first.get("direct")
    if not isinstance(wrapper, Mapping) or not isinstance(direct, Mapping):
        raise ReconstructionStop("R46_UNIVERSE_WRAPPER_PARITY_FAILURE", "direct builder output missing")
    wrapper_identity = wrapper.get("identity")
    direct_identity = direct.get("identity")
    wrapper_targets = wrapper.get("target_snapshots")
    direct_targets = direct.get("target_snapshots")
    if not isinstance(wrapper_identity, Mapping) or not isinstance(direct_identity, Mapping):
        raise ReconstructionStop("R46_UNIVERSE_WRAPPER_PARITY_FAILURE", "direct builder identity missing")
    if not isinstance(wrapper_targets, Mapping) or not isinstance(direct_targets, Mapping):
        raise ReconstructionStop("R46_UNIVERSE_WRAPPER_PARITY_FAILURE", "target snapshot missing")
    checks = {
        "full_hash_equal": wrapper_identity.get("full_universe_hash") == direct_identity.get("full_universe_hash"),
        "snapshot_count_equal": wrapper_identity.get("snapshot_count") == direct_identity.get("snapshot_count"),
        "feb06_snapshot_equal": wrapper_targets.get(iso(FEB06)) == direct_targets.get(iso(FEB06)),
        "feb10_snapshot_equal": wrapper_targets.get(iso(FEB10)) == direct_targets.get(iso(FEB10)),
        "observation_set_digest_equal": wrapper_identity.get("observation_set_digest")
        == direct_identity.get("observation_set_digest"),
    }
    if not all(checks.values()):
        raise ReconstructionStop("R46_UNIVERSE_WRAPPER_PARITY_FAILURE", json.dumps(checks, sort_keys=True))
    return {"status": "PASS", "checks": checks, "wrapper_identity": dict(wrapper_identity), "direct_identity": dict(direct_identity)}


def terminal_last_bar(value: object) -> datetime | None:
    if isinstance(value, Mapping):
        raw = value.get("last_bar")
    else:
        raw = getattr(value, "last_bar", None)
    if raw is None:
        return None
    return aware(raw) if isinstance(raw, str) else aware(cast_datetime(raw))


def cast_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("last_bar must be datetime")
    return value


def effective_observation_symbols(
    snapshot: StructuralSnapshot,
    timestamp: datetime,
    terminal_map: Mapping[str, object] | None = None,
    managed_symbols: Sequence[str] = (),
    pending_symbols: Sequence[str] = (),
) -> tuple[str, ...]:
    """Mirror the frozen engine's normal observation membership calculation."""

    terminals = terminal_map or {}

    def is_active(symbol: str) -> bool:
        terminal = terminals.get(symbol)
        last_bar = terminal_last_bar(terminal) if terminal is not None else None
        return last_bar is None or last_bar >= timestamp

    observation = tuple(symbol for symbol in (snapshot.observation_set or ()) if is_active(symbol))
    capacity = int(snapshot.maximum_stream_symbols or 0)
    if capacity <= 0:
        if observation:
            return tuple(sorted(observation))
        return tuple(sorted(symbol for symbol in snapshot.symbols if is_active(symbol)))
    references = tuple(symbol for symbol in snapshot.pinned_symbols if is_active(symbol))
    managed = tuple(
        dict.fromkeys(
            [symbol for symbol in (*managed_symbols, *pending_symbols) if is_active(symbol)]
        )
    )
    reserved = tuple(dict.fromkeys((*references, *managed)))
    if len(reserved) > capacity:
        raise RuntimeError("managed pins exceed historical stream capacity")
    base = observation or tuple(sorted(symbol for symbol in snapshot.symbols if is_active(symbol)))
    tail = tuple(sorted(base)) if managed else base
    return tuple(dict.fromkeys((*reserved, *tail)))[:capacity]


def structural_bar_index(dataset: object, upper_bound: datetime) -> tuple[dict[str, tuple[datetime, ...]], tuple[datetime, ...]]:
    """Index only timestamp/symbol/timeframe fields from the frozen dataset."""

    per_symbol: dict[str, list[datetime]] = {}
    executable_times: set[datetime] = set()
    for bar in getattr(dataset, "signal_bars", ()):
        timestamp = getattr(bar, "timestamp")
        if timestamp > upper_bound or str(getattr(bar, "timeframe")) != "1d":
            continue
        symbol = str(getattr(bar, "symbol")).strip().upper()
        per_symbol.setdefault(symbol, []).append(timestamp)
    for bar in getattr(dataset, "executable_bars", ()):
        timestamp = getattr(bar, "timestamp")
        if timestamp <= upper_bound and str(getattr(bar, "timeframe")) == "1d":
            executable_times.add(timestamp)
    return (
        {symbol: tuple(sorted(set(timestamps))) for symbol, timestamps in per_symbol.items()},
        tuple(sorted(executable_times)),
    )


def history_detail(
    series: Sequence[datetime], timestamp: datetime, symbol: str, *, context_limit: int = CONTEXT_HISTORY_LIMIT
) -> dict[str, object]:
    count = bisect_right(series, timestamp)
    latest = series[count - 1] if count else None
    current_index = bisect_left(series, timestamp, 0, count)
    current = current_index < count and series[current_index] == timestamp
    context_count = count if symbol == "SPY" else min(count, context_limit)
    return {
        "symbol": symbol,
        "history_exists": count > 0,
        "history_count": count,
        "context_history_count": context_count,
        "latest_completed_timestamp": iso(latest),
        "current_bar_exists": current,
    }


def completeness_record(
    expected_symbols: Sequence[str] | None,
    per_symbol: Mapping[str, Sequence[datetime]],
    timestamp: datetime,
    *,
    context_limit: int = CONTEXT_HISTORY_LIMIT,
) -> dict[str, object]:
    if expected_symbols is None:
        return {
            "result": "BYPASSED_R46_EVENT_BOUNDARY_OVERRIDE",
            "expected_count": None,
            "expected_symbols": None,
            "current_bar_count": None,
            "current_bar_symbols": None,
            "complete_count": None,
            "complete_symbols": None,
            "missing_count": None,
            "missing_symbols": None,
            "stale_count": None,
            "stale_symbols": None,
            "per_symbol": None,
        }
    expected = tuple(dict.fromkeys(str(symbol) for symbol in expected_symbols))
    details = [history_detail(per_symbol.get(symbol, ()), timestamp, symbol, context_limit=context_limit) for symbol in expected]
    missing = [
        str(detail["symbol"])
        for detail in details
        if not bool(detail["history_exists"])
    ]
    stale = [
        str(detail["symbol"])
        for detail in details
        if bool(detail["history_exists"])
        and (detail["latest_completed_timestamp"] != iso(timestamp) or not bool(detail["current_bar_exists"]))
    ]
    current = [str(detail["symbol"]) for detail in details if bool(detail["current_bar_exists"])]
    complete = [
        str(detail["symbol"])
        for detail in details
        if str(detail["symbol"]) not in missing and str(detail["symbol"]) not in stale
    ]
    return {
        "result": "PASS" if not missing and not stale else "FAIL",
        "expected_count": len(expected),
        "expected_symbols": list(expected),
        "current_bar_count": len(current),
        "current_bar_symbols": current,
        "complete_count": len(complete),
        "complete_symbols": complete,
        "missing_count": len(missing),
        "missing_symbols": missing,
        "stale_count": len(stale),
        "stale_symbols": stale,
        "per_symbol": details,
    }


def product_guard_record(
    universe: StructuralUniverse,
    snapshot: StructuralSnapshot | None,
    version: str,
    timestamp: datetime,
    expected_symbols: Sequence[str] | None,
) -> dict[str, object]:
    contains_callable = callable(getattr(universe, "contains", None))
    membership = (
        [
            {
                "symbol": symbol,
                "contains_at_or_before": bool(universe.contains(symbol, "equity", timestamp)),
            }
            for symbol in (expected_symbols or ())
        ]
        if contains_callable
        else []
    )
    available = snapshot is not None
    version_present = bool(version)
    membership_pass = all(bool(item["contains_at_or_before"]) for item in membership)
    return {
        "result": "PASS" if available and version_present and contains_callable and membership_pass else "FAIL",
        "snapshot_exists": available,
        "universe_version_nonempty": version_present,
        "contains_callable": contains_callable,
        "membership_semantics": "latest_snapshot_effective_at_or_before_timestamp",
        "membership_checks": membership,
    }


def support_record(
    per_symbol: Mapping[str, Sequence[datetime]],
    visible_symbols: Sequence[str] | None,
    timestamp: datetime,
) -> dict[str, object]:
    symbols = tuple(visible_symbols) if visible_symbols is not None else tuple(sorted(per_symbol))
    details = [history_detail(per_symbol.get(symbol, ()), timestamp, symbol) for symbol in symbols]
    maximum = max((int(str(detail["context_history_count"])) for detail in details), default=0)
    spy_detail = history_detail(per_symbol.get("SPY", ()), timestamp, "SPY")
    return {
        "maximum_history_count": maximum,
        "spy_history_count": spy_detail["context_history_count"],
        "minimum_history_bars": MINIMUM_HISTORY_BARS,
        "supports": maximum >= MINIMUM_HISTORY_BARS,
        "history_scope": "EFFECTIVE_OBSERVATION_SET" if visible_symbols is not None else "ALL_SIGNAL_SYMBOLS",
    }


def event_records_by_timestamp(schedules: Sequence[object]) -> dict[datetime, tuple[dict[str, str], ...]]:
    grouped: dict[datetime, list[dict[str, str]]] = {}
    for schedule in schedules:
        event_id = str(getattr(schedule, "event_id"))
        for timestamp, boundary_type in (
            (getattr(schedule, "entry_decision"), "ENTRY"),
            (getattr(schedule, "exit_decision"), "EXIT"),
        ):
            grouped.setdefault(timestamp, []).append({"event_id": event_id, "boundary_type": boundary_type})
    return {timestamp: tuple(rows) for timestamp, rows in grouped.items()}


def evaluate_cycle(
    timestamp: datetime,
    previous_timestamp: datetime | None,
    universe: StructuralUniverse,
    universe_version: str,
    per_symbol: Mapping[str, Sequence[datetime]],
    events: Mapping[datetime, Sequence[Mapping[str, str]]],
    terminal_map: Mapping[str, object],
    *,
    observed_event_target_missing: bool = False,
) -> dict[str, object]:
    event_rows = tuple(dict(row) for row in events.get(timestamp, ()))
    native_weekly = is_native_weekly_boundary(previous_timestamp, timestamp)
    extra_boundary = bool(event_rows)
    scheduled = native_weekly or extra_boundary
    snapshot_raw = latest_snapshot_at_or_before(universe.snapshots, timestamp)
    snapshot = snapshot_raw if isinstance(snapshot_raw, StructuralSnapshot) else None
    raw_observation = tuple(snapshot.observation_set) if snapshot is not None else ()
    if extra_boundary:
        effective = None
        effective_source = "R46_EVENT_BOUNDARY_OVERRIDE"
        visible = None
    elif snapshot is not None:
        effective = effective_observation_symbols(snapshot, timestamp, terminal_map)
        effective_source = "ENGINE_EFFECTIVE_OBSERVATION_SYMBOLS"
        visible = effective
    else:
        effective = None
        effective_source = "NO_POINT_IN_TIME_SNAPSHOT"
        visible = ()
    product_membership_symbols = effective if effective is not None else raw_observation
    product = product_guard_record(
        universe,
        snapshot,
        universe_version,
        timestamp,
        product_membership_symbols,
    )
    support = support_record(per_symbol, visible, timestamp)
    completeness = completeness_record(effective, per_symbol, timestamp)
    if not scheduled:
        final_enum = "SKIPPED_OTHER_PRE_DECIDE_GATE"
        skip_reason = "NOT_SCHEDULED_ENGINE_EARLY_RETURN"
        would_decide: bool | None = False
        target_possible: bool | None = False
        product_gate = "NOT_EVALUATED_NOT_SCHEDULED"
        support_gate = "NOT_EVALUATED_NOT_SCHEDULED"
        completeness_gate = "NOT_EVALUATED_NOT_SCHEDULED"
    elif product["result"] != "PASS":
        final_enum = "SKIPPED_PRODUCT_UNIVERSE"
        skip_reason = "PRODUCT_UNIVERSE_GUARD"
        would_decide = False
        target_possible = False
        product_gate = str(product["result"])
        support_gate = "NOT_EVALUATED_PRODUCT_UNIVERSE"
        completeness_gate = "NOT_EVALUATED_PRODUCT_UNIVERSE"
    elif not bool(support["supports"]):
        final_enum = "SKIPPED_SUPPORT"
        skip_reason = "STRATEGY_SUPPORTS_FALSE"
        would_decide = False
        target_possible = False
        product_gate = "PASS"
        support_gate = "FAIL"
        completeness_gate = "NOT_EVALUATED_SUPPORT"
    elif extra_boundary:
        final_enum = "DECIDE_REACHED"
        skip_reason = None
        would_decide = True
        target_possible = not observed_event_target_missing
        product_gate = "PASS"
        support_gate = "PASS"
        completeness_gate = "BYPASSED_R46_EVENT_BOUNDARY_OVERRIDE"
    elif completeness["result"] != "PASS":
        final_enum = "SKIPPED_OBSERVATION_COMPLETENESS"
        skip_reason = "HISTORICAL_STRATEGY_BATCH_INCOMPLETE"
        would_decide = False
        target_possible = False
        product_gate = "PASS"
        support_gate = "PASS"
        completeness_gate = "FAIL"
    else:
        final_enum = "DECIDE_REACHED"
        skip_reason = None
        would_decide = True
        target_possible = True
        product_gate = "PASS"
        support_gate = "PASS"
        completeness_gate = "PASS"
    return {
        "timestamp": iso(timestamp),
        "iso_week": list(timestamp.isocalendar()[:2]),
        "r46_boundaries": event_rows,
        "previous_timestamp_before_scheduling": iso(previous_timestamp),
        "native_weekly_scheduled": native_weekly,
        "r46_extra_boundary_scheduled": extra_boundary,
        "scheduled": scheduled,
        "intermediate_risk_gate_matters": False,
        "intermediate_risk_semantics": (
            "NOT_SCHEDULED_ENGINE_EARLY_RETURN" if not scheduled else "IRRELEVANT_SCHEDULED_DECISION"
        ),
        "product_universe_available": snapshot is not None,
        "universe_version": universe_version,
        "product_guard": product_gate,
        "product_guard_probe": product,
        "effective_snapshot_at": iso(snapshot.effective_at) if snapshot is not None else None,
        "raw_snapshot_observation_source": (
            "PIT_SNAPSHOT_OBSERVATION_SET" if raw_observation else "PIT_SNAPSHOT_POOL_FALLBACK"
        ),
        "raw_snapshot_observation_count": len(raw_observation),
        "raw_snapshot_observation_symbols": list(raw_observation),
        "effective_observation_source": effective_source,
        "effective_observation_count": None if effective is None else len(effective),
        "effective_observation_symbols": None if effective is None else list(effective),
        "support_gate": support_gate,
        "support_probe": support,
        "maximum_history_count": support["maximum_history_count"],
        "spy_history_count": support["spy_history_count"],
        "minimum_history_bars": MINIMUM_HISTORY_BARS,
        "current_bar_count": completeness["current_bar_count"],
        "complete_count": completeness["complete_count"],
        "missing_count": completeness["missing_count"],
        "missing_symbols": completeness["missing_symbols"],
        "stale_count": completeness["stale_count"],
        "stale_symbols": completeness["stale_symbols"],
        "completeness_gate": completeness_gate,
        "completeness_probe": completeness,
        "final_decision_enum": final_enum,
        "skip_reason": skip_reason,
        "would_reach_strategy_decide": would_decide,
        "target_could_materialize": target_possible,
        "strategy_decide_invoked_by_diagnostic": False,
        "r46_overlay_event_handling_reached": bool(extra_boundary and would_decide),
        "engine_previous_timestamp_after_cycle": iso(timestamp),
        "last_incumbent_target_state": (
            "NONE_PROVEN_BY_PRESERVED_FAILURE" if observed_event_target_missing else "NOT_OBSERVED"
        ),
        "last_incumbent_target_remains_none": True if observed_event_target_missing else None,
    }


def is_native_weekly_boundary(previous: datetime | None, current: datetime) -> bool:
    return previous is None or previous.isocalendar()[:2] != current.isocalendar()[:2]


def trace_timestamps(
    timestamps: Sequence[datetime],
    universe: StructuralUniverse,
    universe_version: str,
    per_symbol: Mapping[str, Sequence[datetime]],
    events: Mapping[datetime, Sequence[Mapping[str, str]]],
    terminal_map: Mapping[str, object],
) -> list[dict[str, object]]:
    previous: datetime | None = None
    trace: list[dict[str, object]] = []
    for timestamp in timestamps:
        if timestamp < WINDOW_START or timestamp > WINDOW_END:
            previous = timestamp
            continue
        observed_missing = timestamp == FEB10 and any(
            row.get("event_id") == FAILURE_EVENT_ID and row.get("boundary_type") == "ENTRY"
            for row in events.get(timestamp, ())
        )
        trace.append(
            evaluate_cycle(
                timestamp,
                previous,
                universe,
                universe_version,
                per_symbol,
                events,
                terminal_map,
                observed_event_target_missing=observed_missing,
            )
        )
        previous = timestamp
    return trace


def first_native_target_possibility(
    timestamps: Sequence[datetime],
    universe: StructuralUniverse,
    universe_version: str,
    per_symbol: Mapping[str, Sequence[datetime]],
    events: Mapping[datetime, Sequence[Mapping[str, str]]],
    terminal_map: Mapping[str, object],
    upper_bound: datetime,
) -> dict[str, object]:
    previous: datetime | None = None
    first_supported: datetime | None = None
    first_apparent: datetime | None = None
    candidates: list[dict[str, object]] = []
    for timestamp in timestamps:
        if timestamp > upper_bound:
            break
        result = evaluate_cycle(
            timestamp,
            previous,
            universe,
            universe_version,
            per_symbol,
            events,
            terminal_map,
        )
        if first_supported is None and int(str(result["spy_history_count"])) >= MINIMUM_HISTORY_BARS:
            first_supported = timestamp
        if first_supported is not None and first_apparent is None and bool(result["native_weekly_scheduled"]):
            first_apparent = timestamp
        if (
            first_supported is not None
            and bool(result["native_weekly_scheduled"])
            and not bool(result["r46_extra_boundary_scheduled"])
        ):
            candidates.append(result)
        previous = timestamp
    first_materializable = next(
        (
            result["timestamp"]
            for result in candidates
            if result["final_decision_enum"] == "DECIDE_REACHED"
        ),
        None,
    )
    return {
        "first_signal_timestamp": iso(timestamps[0]) if timestamps else None,
        "first_history_supported_timestamp": iso(first_supported),
        "first_apparent_native_weekly_boundary_after_support": iso(first_apparent),
        "first_actual_native_target_materializable_timestamp": first_materializable,
        "native_opportunities_after_support_before_feb10": candidates,
        "prior_incumbent_target_materializable": first_materializable is not None,
        "strategy_decide_invoked_by_diagnostic": False,
    }


def first_event_boundary_after_feb06(
    schedules: Sequence[object],
    trace: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    boundaries = [
        (getattr(schedule, "entry_decision"), "ENTRY", schedule)
        for schedule in schedules
        if getattr(schedule, "entry_decision") >= FEB06
    ] + [
        (getattr(schedule, "exit_decision"), "EXIT", schedule)
        for schedule in schedules
        if getattr(schedule, "exit_decision") >= FEB06
    ]
    if not boundaries:
        raise ReconstructionStop("ROOT_CAUSE_STILL_UNPROVEN", "no R46 event after Feb 6")
    boundary_timestamp, boundary_type, schedule = min(
        boundaries,
        key=lambda item: (item[0], str(getattr(item[2], "event_id")), item[1]),
    )
    matching = next((row for row in trace if row.get("timestamp") == iso(boundary_timestamp)), None)
    if matching is None:
        raise ReconstructionStop("ROOT_CAUSE_STILL_UNPROVEN", "first event boundary is outside trace")
    return {
        "event_id": str(getattr(schedule, "event_id")),
        "boundary_type": boundary_type,
        "timestamp": iso(boundary_timestamp),
        "entry_open": iso(getattr(schedule, "entry_open")),
        "exit_open": iso(getattr(schedule, "exit_open")),
        "native_weekly_scheduled": matching["native_weekly_scheduled"],
        "r46_extra_boundary_scheduled": matching["r46_extra_boundary_scheduled"],
        "effective_observation_source": matching["effective_observation_source"],
        "support_gate": matching["support_gate"],
        "product_guard": matching["product_guard"],
        "completeness_gate": matching["completeness_gate"],
        "r46_overlay_event_handling_reached": matching["r46_overlay_event_handling_reached"],
        "final_decision_enum": matching["final_decision_enum"],
        "would_reach_strategy_decide": matching["would_reach_strategy_decide"],
        "target_could_materialize": matching["target_could_materialize"],
        "terminal_error": FAILURE_ERROR,
        "proven_from_frozen_schedule": True,
    }


def source_computation_cell_proof(root: Path, pins: Mapping[str, object], v2: object) -> dict[str, object]:
    path = (root / V3_RUNNER).resolve()
    source = path.read_text(encoding="utf-8")
    actual_hash = sha256_file(path)
    source_rows = pins.get("source_files")
    expected_hash = next(
        (
            row.get("expected_sha256")
            for row in source_rows
            if isinstance(row, Mapping) and row.get("path") == str(V3_RUNNER)
        ),
        None,
    ) if isinstance(source_rows, list) else None
    if expected_hash != actual_hash:
        raise _drift("v3_runner:sha256")
    loop = source.find("for multiplier in multipliers:")
    candidate = source.find("candidate_result = frozen_v2._run_r46_engine(", loop)
    control = source.find("control_result = frozen_v2._run_r46_engine(", candidate)
    metrics = source.find("frozen_v2._result_metrics(candidate_result)", candidate)
    publication = source.find('"schema_version": ECONOMIC_RUN_SCHEMA', metrics)
    timing = source.find("timing_placebo_cell = frozen_v2._run_timing_placebo_cell(", control)
    if min(loop, candidate, control, metrics, publication, timing) < 0 or not (loop < candidate < control < metrics < timing < publication):
        raise _drift("v3_runner:primary_cost_cell_order")
    multipliers = tuple(float(value) for value in getattr(v2, "COST_MULTIPLIERS"))
    if multipliers != EXPECTED_COST_MULTIPLIERS:
        raise _drift("v2_runner:cost_multipliers")
    return {
        "source_path": str(V3_RUNNER),
        "source_sha256": actual_hash,
        "cost_multiplier_sequence": list(multipliers),
        "first_cost_multiplier": 1.0,
        "first_candidate_engine_invocation_precedes_control": True,
        "result_metrics_after_candidate_invocation": True,
        "timing_placebo_after_all_cost_cells": True,
        "publication_after_candidate_and_control_cells": True,
        "failing_computation_cell": "PRIMARY_CANDIDATE_1X",
        "basis": "frozen source control-flow order; no economic cell was invoked by this diagnostic",
    }


def classify_control_flow_consistency(feb06_reached: bool, later_target_none: bool) -> dict[str, object]:
    if feb06_reached and later_target_none:
        return {
            "status": "DETERMINISTIC_RECONSTRUCTION_CONTRADICTS_OBSERVED_EXECUTION",
            "feb06_decide_reached": True,
            "later_last_incumbent_target_none": True,
            "execution_state_input_missing": True,
            "proof": (
                "A normal non-event R46 decide assigns the inherited target before the first later ENTRY; "
                "the preserved Feb 10 error proves that target was still None."
            ),
        }
    return {
        "status": "CONSISTENT_WITH_PRESERVED_EXECUTION",
        "feb06_decide_reached": feb06_reached,
        "later_last_incumbent_target_none": later_target_none,
        "execution_state_input_missing": False,
        "proof": "The reconstructed pre-decision path does not contradict the preserved failure state.",
    }


def classify_root_cause(
    feb06: Mapping[str, Any],
    determinism: Mapping[str, Any],
    parity: Mapping[str, Any],
    consistency: Mapping[str, Any],
) -> str:
    if determinism.get("identities_byte_identical") is not True:
        return "UNIVERSE_RECONSTRUCTION_NONDETERMINISTIC"
    if parity.get("status") != "PASS":
        return "R46_UNIVERSE_WRAPPER_PARITY_FAILURE"
    final_enum = feb06.get("final_decision_enum")
    if final_enum == "SKIPPED_OBSERVATION_COMPLETENESS":
        return "FIRST_SUPPORTED_WEEKLY_CADENCE_SKIPPED_OBSERVATION_COMPLETENESS"
    if final_enum == "SKIPPED_PRODUCT_UNIVERSE":
        return "FIRST_SUPPORTED_WEEKLY_CADENCE_SKIPPED_PRODUCT_UNIVERSE"
    if final_enum == "SKIPPED_SUPPORT":
        return "FIRST_SUPPORTED_WEEKLY_CADENCE_SKIPPED_SUPPORT"
    if consistency.get("status") == "DETERMINISTIC_RECONSTRUCTION_CONTRADICTS_OBSERVED_EXECUTION":
        return "DETERMINISTIC_RECONSTRUCTION_CONTRADICTS_OBSERVED_EXECUTION"
    if final_enum == "SKIPPED_OTHER_PRE_DECIDE_GATE":
        return "FIRST_SUPPORTED_WEEKLY_CADENCE_SKIPPED_OTHER_PRE_DECIDE_GATE"
    return "ROOT_CAUSE_STILL_UNPROVEN"


def has_forbidden_economic_fields(value: object) -> bool:
    forbidden = {
        "return",
        "returns",
        "pnl",
        "sharpe",
        "sortino",
        "drawdown",
        "price",
        "prices",
        "weights",
        "orders",
        "fills",
        "starting_equity",
        "ending_equity",
        "turnover",
        "performance",
    }
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or has_forbidden_economic_fields(child) for key, child in value.items())
    if isinstance(value, list):
        return any(has_forbidden_economic_fields(child) for child in value)
    return False


def config_summary(config: object) -> dict[str, object]:
    strategy = getattr(getattr(config, "strategy"), "equity")
    universe = getattr(getattr(config, "universe"), "equity")
    return {
        "strategy_name": str(getattr(strategy, "name")),
        "strategy_timeframe": str(getattr(strategy, "timeframe")),
        "strategy_parameters": copy.deepcopy(dict(getattr(strategy, "parameters"))),
        "maximum_stream_symbols": int(getattr(universe, "maximum_stream_symbols")),
        "reference_symbols": list(getattr(universe, "reference_symbols")),
        "inverse_trade_symbols": list(getattr(universe, "inverse_trade_symbols")),
        "target_shortlist": int(getattr(universe, "target_shortlist")),
        "scanner_rebalance": str(getattr(getattr(config, "backtest"), "scanner_rebalance")),
        "rebalance_policy": "weekly",
        "minimum_history_bars": MINIMUM_HISTORY_BARS,
    }


def snapshot_receipt_record(
    requested: datetime,
    snapshot: StructuralSnapshot,
    universe: StructuralUniverse,
    version: str,
    per_symbol: Mapping[str, Sequence[datetime]],
    terminal_map: Mapping[str, object],
    *,
    event_override: bool,
    event_rows: Sequence[Mapping[str, str]],
) -> dict[str, object]:
    raw_observation = tuple(snapshot.observation_set)
    if event_override:
        effective: tuple[str, ...] | None = None
        effective_source = "R46_EVENT_BOUNDARY_OVERRIDE"
        completeness = completeness_record(None, per_symbol, requested)
    else:
        effective = effective_observation_symbols(snapshot, requested, terminal_map)
        effective_source = "ENGINE_EFFECTIVE_OBSERVATION_SYMBOLS"
        completeness = completeness_record(effective, per_symbol, requested)
    return {
        "requested_timestamp": iso(requested),
        "effective_at": iso(snapshot.effective_at),
        "snapshot_digest": snapshot_digest(snapshot),
        "selected_universe_symbol_count": len(snapshot.symbols),
        "selected_universe_symbols_sha256": sha256_bytes(sorted(snapshot.symbols)),
        "raw_observation_set": list(raw_observation),
        "raw_observation_set_count": len(raw_observation),
        "raw_observation_set_sha256": ordered_observation_digest(raw_observation),
        "maximum_stream_symbols": snapshot.maximum_stream_symbols,
        "pinned_symbols": list(snapshot.pinned_symbols),
        "observation_strategy_name": snapshot.observation_strategy_name,
        "observation_strategy_version": snapshot.observation_strategy_version,
        "scanner_version": snapshot.scanner_version,
        "config_digest": snapshot.config_digest,
        "candidate_count": snapshot.candidate_count,
        "universe_hash": version,
        "point_in_time_membership": {
            "contains_callable": callable(getattr(universe, "contains", None)),
            "semantics": "latest_snapshot_effective_at_or_before_timestamp",
            "raw_observation_membership_checks": [
                {
                    "symbol": symbol,
                    "contains_at_or_before": universe.contains(symbol, "equity", requested),
                }
                for symbol in raw_observation
            ],
            "observed_boundary_rows": [dict(row) for row in event_rows],
        },
        "effective_observation": {
            "source": effective_source,
            "count": None if effective is None else len(effective),
            "symbols": None if effective is None else list(effective),
        },
        "completeness": completeness,
    }


def build_document(receipt: Mapping[str, Any]) -> str:
    root_cause = receipt["root_cause"]["classification"]
    feb06 = receipt["feb06_snapshot"]
    feb06_gate = receipt["feb06_gate_audit"]
    feb10_gate = receipt["first_r46_event_boundary"]
    cell = receipt["primary_1x_computation_cell"]
    consistency = receipt["control_flow_consistency"]
    lines = [
        "# R46 v3 Observation Snapshot Reconstruction — 2026-09-14",
        "",
        "This note records a read-only reconstruction of the exact frozen R46 v3 point-in-time universe. "
        "The original runtime snapshot was not persisted, but the frozen loader, config, V5 inputs, and "
        "builder chain were still available and independently pinned.",
        "",
        "## Rejected earlier hypothesis",
        "",
        "The earlier hypothesis that a pre-first-cadence cash repair or an event-boundary ordering path "
        "explained the missing incumbent was not used. The preserved failure occurred after real historical "
        "processing began, and the new reconstruction tests the native universe and decision gates directly.",
        "",
        "## Deterministic builder chain",
        "",
        "The frozen V2 wrapper loaded the V5 incumbent, loaded `config/settings.yaml`, removed TLT from the "
        "incumbent universe input, and called `build_product_universe(...)`. That function selected the frozen "
        "equity scanner configuration, which called `build_trailing_point_in_time_universe(...)`; the latter "
        "called `build_observation_set(...)` with the configured V3 ranker. The ranker was used only to recover "
        "ordered stream membership; no scores were persisted.",
        "",
        f"Two independent Python processes with distinct hash seeds produced byte-identical identities. "
        f"The reconstructed universe contains {receipt['universe']['snapshot_count']} snapshots and has full "
        f"hash `{receipt['universe']['full_universe_hash']}`. Direct-builder parity passed for the full hash "
        "and both audited snapshots.",
        "",
        "## Feb 6 path",
        "",
        f"The requested boundary `{feb06['requested_timestamp']}` resolved to `{feb06['effective_at']}`. "
        f"The selected universe count was {feb06['selected_universe_symbol_count']}; the exact ordered raw "
        f"observation set contains {feb06['raw_observation_set_count']} symbols. The mirrored effective set "
        f"contains {feb06['effective_observation']['count']} symbols and the completeness result is "
        f"`{feb06['completeness']['result']}`. The support gate was `{feb06_gate['support_gate']}` and the "
        f"product guard was `{feb06_gate['product_guard']}`.",
        "",
        "The completeness predicate was evaluated against the exact effective observation set: each expected "
        "symbol had non-empty history and a latest completed bar equal to the decision timestamp. The receipt "
        "contains the ordered expected, complete, missing, stale, and per-symbol structural records.",
        "",
        "## Primary 1x failing cell",
        "",
        f"Frozen V3 source order proves the first cost multiplier was {cell['first_cost_multiplier']}, the "
        "candidate engine invocation preceded the control invocation, and metrics/publication occur only "
        f"after that call. The source-level failing computation cell is `{cell['failing_computation_cell']}`. "
        "This is control-flow evidence, not an economic result.",
        "",
        "## Feb 10 event path and consistency",
        "",
        f"The first frozen event boundary after Feb 6 is `{feb10_gate['timestamp']}` for "
        f"`{feb10_gate['event_id']}` / `{feb10_gate['boundary_type']}`. Native weekly scheduling was false, "
        "the R46 extra boundary was true, support and product checks passed, and the event override bypassed "
        "normal observation completeness. The preserved failure then reached the missing incumbent-base branch "
        f"with `{FAILURE_ERROR}`. The reconstructed Feb 6 path is therefore "
        f"`{consistency['status']}`: a normal non-event decide would have assigned the incumbent target, while "
        "the preserved later event proves that target was still None. The missing item is execution-state "
        "provenance, not a selected repair.",
        "",
        f"Final classification: `{root_cause}`.",
        "",
        "No repair has been selected or implemented.",
        "",
    ]
    return "\n".join(lines)


def make_receipt(root: Path) -> dict[str, Any]:
    pins = verify_source_pins(root)
    preserved = verify_preserved_failure_evidence(root)
    v2 = importlib.import_module(V2_MODULE_NAME)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        incumbent = v2.load_incumbent_pit_inputs(repo_root=root)
        config = v2._load_r46_config(None, repo_root=root, test_only=False)
        package = v2.load_r46_v2_package(root / V2_PACKAGE, repo_root=root)
        dataset = v2.build_r46_market_dataset(package, incumbent)
    if config is None:
        raise _drift("config_loader:none")

    first = run_worker(root, 1, include_direct=True)
    second = run_worker(root, 777, include_direct=False)
    determinism = compare_process_identities(first, second)
    parity = verify_direct_parity(first)
    wrapper = first["wrapper"]
    if not isinstance(wrapper, Mapping):
        raise ReconstructionStop("UNIVERSE_RECONSTRUCTION_FAILED", "wrapper result missing")
    raw_snapshots = wrapper.get("snapshots")
    identity = wrapper.get("identity")
    methodology = wrapper.get("methodology")
    if not isinstance(raw_snapshots, list) or not isinstance(identity, Mapping) or not isinstance(methodology, str):
        raise ReconstructionStop("UNIVERSE_RECONSTRUCTION_FAILED", "wrapper structural data missing")
    structural = StructuralUniverse(
        snapshots=tuple(snapshot_from_raw(raw) for raw in raw_snapshots if isinstance(raw, Mapping)),
        methodology=methodology,
        full_hash=str(identity["full_universe_hash"]),
    )
    universe_version = structural.full_hash
    if not structural.snapshots:
        raise _drift("reconstructed_universe:empty")

    per_symbol, timestamps = structural_bar_index(dataset, FEB10)
    if not timestamps:
        raise _drift("signal_dataset:empty_structural_index")
    events = event_records_by_timestamp(package.schedules)
    timeline = trace_timestamps(
        timestamps,
        structural,
        universe_version,
        per_symbol,
        events,
        incumbent.terminal_map,
    )
    feb06_trace = next(row for row in timeline if row["timestamp"] == iso(FEB06))
    feb10_trace = next(row for row in timeline if row["timestamp"] == iso(FEB10))
    feb06_snapshot_raw = latest_snapshot_at_or_before(structural.snapshots, FEB06)
    feb10_snapshot_raw = latest_snapshot_at_or_before(structural.snapshots, FEB10)
    if not isinstance(feb06_snapshot_raw, StructuralSnapshot) or not isinstance(feb10_snapshot_raw, StructuralSnapshot):
        raise _drift("reconstructed_universe:audited_snapshot_missing")
    feb06_snapshot = snapshot_receipt_record(
        FEB06,
        feb06_snapshot_raw,
        structural,
        universe_version,
        per_symbol,
        incumbent.terminal_map,
        event_override=False,
        event_rows=events.get(FEB06, ()),
    )
    feb10_snapshot = snapshot_receipt_record(
        FEB10,
        feb10_snapshot_raw,
        structural,
        universe_version,
        per_symbol,
        incumbent.terminal_map,
        event_override=True,
        event_rows=events.get(FEB10, ()),
    )
    first_target = first_native_target_possibility(
        timestamps,
        structural,
        universe_version,
        per_symbol,
        events,
        incumbent.terminal_map,
        FEB10,
    )
    first_event = first_event_boundary_after_feb06(package.schedules, timeline)
    consistency = classify_control_flow_consistency(
        feb06_trace["final_decision_enum"] == "DECIDE_REACHED",
        feb10_trace["last_incumbent_target_remains_none"] is True,
    )
    cell = source_computation_cell_proof(root, pins, v2)
    root_cause = classify_root_cause(feb06_trace, determinism, parity, consistency)
    if root_cause not in ROOT_CAUSE_CLASSES:
        raise ReconstructionStop("ROOT_CAUSE_STILL_UNPROVEN", "invalid classification")

    data_contract = pins["v5_inputs"]
    feb06_completeness = feb06_snapshot.get("completeness")
    if not isinstance(feb06_completeness, Mapping):
        raise ReconstructionStop("RECONSTRUCTION_FAILED", "Feb 6 completeness record missing")
    receipt: dict[str, Any] = {
        "schema_version": "r46-v3-observation-snapshot-reconstruction-v1",
        "status": root_cause,
        "scope": {
            "diagnosis_only": True,
            "frozen_v3_rerun": False,
            "economic_engine_invocation": False,
            "strategy_decide_invocation": False,
            "real_target_portfolio_construction": False,
            "economic_outputs": False,
            "returns_or_outcomes": False,
            "price_derived_fields_persisted": False,
            "inspected_dataset_fields": ["timestamp", "symbol", "timeframe"],
        },
        "source_input_pins": pins,
        "preserved_failure_evidence": preserved,
        "reconstruction_methods": {
            "wrapper": "frozen_v2._build_r46_universe(incumbent, config=config, test_only=False)",
            "required_wrapper_status": "PIT_V5_PRODUCT_UNIVERSE",
            "direct_builder": "build_product_universe(config, bars_without_tlt, market=EQUITY, end=max_timestamp, timeframe=1d)",
            "snapshot_lookup": "latest effective_at at or before requested timestamp via bisect_right",
            "observation_builder": "configured V3 rank_candidates path used only for stream membership",
            "effective_observation_semantics": "terminal filtering, configured pins, managed/pending pins, capacity, ordered deduplication",
            "dataset_assembly": "frozen_v2.build_r46_market_dataset(package, incumbent), structural fields only after assembly",
            "data_contract": data_contract,
        },
        "determinism_proof": determinism,
        "direct_builder_parity": parity,
        "universe": {
            "full_universe_hash": universe_version,
            "snapshot_count": identity["snapshot_count"],
            "methodology": structural.methodology,
            "wrapper_status": "PIT_V5_PRODUCT_UNIVERSE",
            "config": config_summary(config),
        },
        "dataset_structure": {
            "market": "equity",
            "timeframe": "1d",
            "signal_bar_count": len(getattr(dataset, "signal_bars")),
            "executable_bar_count": len(getattr(dataset, "executable_bars")),
            "structural_index_upper_bound": iso(FEB10),
            "price_and_volume_fields_omitted": True,
        },
        "feb06_snapshot": feb06_snapshot,
        "feb10_snapshot": feb10_snapshot,
        "timestamp_trace": timeline,
        "feb06_gate_audit": feb06_trace,
        "first_r46_event_boundary": first_event,
        "primary_1x_computation_cell": cell,
        "control_flow_consistency": consistency,
        "prior_target_possibility": first_target,
        "root_cause": {
            "classification": root_cause,
            "proven": root_cause != "ROOT_CAUSE_STILL_UNPROVEN",
            "subclassification": (
                "EXECUTION_STATE_INPUT_NOT_PERSISTED"
                if root_cause == "DETERMINISTIC_RECONSTRUCTION_CONTRADICTS_OBSERVED_EXECUTION"
                else None
            ),
            "feb06_completeness_result": feb06_completeness["result"],
            "feb06_missing_symbols": feb06_completeness["missing_symbols"],
            "feb06_stale_symbols": feb06_completeness["stale_symbols"],
            "repair_selected": False,
        },
        "repair": {
            "selected": False,
            "statement": "No repair has been selected or implemented.",
        },
        "integrity": {"reconstruction_sha256": "PENDING"},
    }
    if has_forbidden_economic_fields(receipt):
        raise ReconstructionStop("RECONSTRUCTION_SOURCE_DRIFT", "economic field entered structural receipt")
    receipt["integrity"]["reconstruction_sha256"] = canonical_with_pending_self(
        receipt, "reconstruction_sha256"
    )
    return receipt


def write_outputs(root: Path, receipt: Mapping[str, Any]) -> None:
    receipt_path = (root / OUTPUT_RECEIPT).resolve()
    doc_path = (root / OUTPUT_DOC).resolve()
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    doc_path.write_text(build_document(receipt), encoding="utf-8")


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help="emit one structural worker identity JSON object")
    parser.add_argument("--include-direct", action="store_true", help="also build the direct product universe in worker mode")
    args = parser.parse_args(argv)
    try:
        if args.worker:
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                verify_source_pins(REPO)
                value = build_worker_universes(REPO, args.include_direct)
            value["head"] = git_head(REPO)
            print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        receipt = make_receipt(REPO)
        write_outputs(REPO, receipt)
        print(
            json.dumps(
                {
                    "status": receipt["status"],
                    "full_universe_hash": receipt["universe"]["full_universe_hash"],
                    "feb06_completeness": receipt["feb06_snapshot"]["completeness"]["result"],
                    "first_actual_native_target": receipt["prior_target_possibility"][
                        "first_actual_native_target_materializable_timestamp"
                    ],
                    "receipt": str(OUTPUT_RECEIPT),
                    "documentation": str(OUTPUT_DOC),
                },
                sort_keys=True,
            )
        )
        return 0
    except ReconstructionStop as exc:
        print(json.dumps({"status": exc.classification, "detail": exc.detail}, sort_keys=True))
        return 1
    except Exception as exc:  # pragma: no cover - fail-closed CLI boundary
        print(json.dumps({"status": "RECONSTRUCTION_FAILED", "detail": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
