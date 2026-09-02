#!/usr/bin/env python3
"""Run the network-free R5 M3 data gate.

R5 is intentionally a new verifier.  It reuses the accepted R4 bytes and
the existing reference/matrix primitives, but it does not alter the R4
verifier, its artifacts, or production strategy code.  The only numerical
work here is the frozen M3 design-matrix feasibility check; no economic score
or portfolio result is produced.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import yaml

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

import research_equity_m3_data_gate as r4_gate  # noqa: E402
from m3_data_gate_helpers import (  # noqa: E402
    CANONICAL_TIMEFRAME,
    EVALUATION_END_SESSION,
    EXCHANGE_TIMEZONE,
    EXPECTED_REQUIRED_REFERENCE_SESSIONS,
    REFERENCE_SYMBOLS,
    REQUEST_END_BOUNDARY_UTC,
    REQUEST_START_UTC,
    REQUIRED_FIRST_REFERENCE_SESSION,
    REQUIRED_LAST_REFERENCE_SESSION,
    SOURCE_ADJUSTMENT,
    SOURCE_FEED,
    GateError,
    audit_design_matrix,
    build_reference_inventory,
    build_reference_overlay,
    calendar_dependency_identity,
    canonical_digest,
    canonical_timestamp,
    common_session_report,
    config_semantic_digest,
    deterministic_candidate_population,
    expected_exchange_sessions,
    forbidden_receipt_keys,
    merkle_root,
    parse_timestamp,
    semantic_rows_digest,
    session_date_for_timestamp,
    session_dates_digest,
    stage_file,
    validate_iwm_bundle,
    validate_pit_reference_boundary,
    weekly_decision_coverage,
)

R5_MISSION = "equity_m3_data_gate_2026_09_r5"
R5_PIT_MISSION = "equity_m3_pit_scanner_population_2026_09_r5"
R5_BASE_COMMIT = "b0cd5546f93f63d162de1ded3871b4431b08b89e"
R5_INITIAL_IMPLEMENTATION_COMMIT = "193182fa9ebf297fe623395476a7cdcd9c14c12f"
R5_INITIAL_MANIFEST_COMMIT = "ad65703453f56737a097bb043dec6646d902bcfb"
R5_SECOND_IMPLEMENTATION_COMMIT = "787e78bcf6070278c071a6ca9f18f6ee4dc0db6b"
R5_SECOND_MANIFEST_COMMIT = "d6106a785c5fc324f0d25e555ebed1599f7cef29"
R5_CANONICAL_FIX_COMMIT = "d52b578c21176dd72ff4d01b51b39c6f4d15267f"
R5_THIRD_IMPLEMENTATION_COMMIT = "136cb93b35e46ba356a88727aaf0c12574f4ba74"
R5_THIRD_MANIFEST_COMMIT = "6c20ddfc5bdd98df6d4a5fad5cf3dc841ac3b85f"
R5_FOURTH_IMPLEMENTATION_COMMIT = "773966f3cfdb33bf584e98343dc4839c59deb224"
R5_FOURTH_MANIFEST_COMMIT = "76393dc775a48ec8e89b5118ddfb5dba7b0b8da1"
R5_SIXTH_IMPLEMENTATION_COMMIT = "79ffcb178845f64f3f2cc036b9e1919cc15891e7"
R5_SIXTH_MANIFEST_COMMIT = "5aeb2058b0027432de45d9db8fdeba20120d4d2c"
R5_SEVENTH_IMPLEMENTATION_COMMIT = "2f49c2bc6e6f8cec295ed0f0abd13f934a7059a6"
R5_SEVENTH_MANIFEST_COMMIT = "a6ceda31f95f3b50f8a5bfcf1f0e04efa36d90d4"
R5_C10_RESULT_COMMIT = "459cf782754bc15d137fcaa4cdd081402b837f96"
R5_MANIFEST_PATH = "data/research_manifests/equity_m3_data_gate_2026_09_r5.json"
R5_PIT_MANIFEST_PATH = "data/research_manifests/equity_m3_pit_population_2026_09_r5.json"
R5_RECEIPT_PATH = "data/research_receipts/equity_m3_data_gate_2026_09_r5.json"
R5_REPORT_PATH = "docs/equity_m3_data_gate_2026_09_r5.md"
R5_PROJECTION_DEFAULT = _REPO / "data/research/generated/m3_data_gate_r5/pit_universe.json"
R5_PIT_MANIFEST_DEFAULT = _REPO / "data/research/generated/m3_data_gate_r5/pit_population_manifest.json"

R4_MANIFEST_PATH = "data/research_manifests/equity_m3_data_gate_2026_09_r4.json"
R4_PIT_PATH = "data/research/generated/m3_data_gate_r4/pit_universe.json"
R4_PIT_MANIFEST_PATH = "data/research_manifests/equity_m3_pit_population_2026_09_r4.json"
R4_COMPOSITE_PATH = "data/research_manifests/equity_m3_composite_dataset_2026_09_r4.json"
R4_SIDECAR_PATH = "data/research/generated/m3_data_gate_r4/iwm_normalized.csv"
R4_BUNDLE_PATH = "data/research/external/m3_iwm_raw_2026_09_r4"

CANONICAL_PATH = _REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
CANONICAL_MANIFEST_PATH = _REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json"
LIFETIMES_PATH = _REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
CORPORATE_ACTIONS_PATH = _REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CORPORATE_ACTIONS_MANIFEST_PATH = _REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json"
CONFIG_PATH = _REPO / "config/settings.yaml"

R4_RAW_BUNDLE_SHA256 = "9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258"
R4_PAGE_DIGEST = "39a87290b4ebafbba3c29663f5577c05c7548252e6699e5f39589849211c93eb"
R4_PAGE_CHAIN_DIGEST = "a5b5e400bb97aff3f6342bbe0b969f5d0ae5600406d5cd186ec62e0360480e1b"
R4_SOURCE_RECEIPT_SHA256 = "2dbeeebc688dab111013a114e9c08b44c116e23c48a42968099f6c8671ea4003"
R4_SIDECAR_RAW_SHA256 = "8f8122e33d5ef2b194e22c7e53a15198920476690997535bbc5c05d522463dc2"
R4_SIDECAR_SEMANTIC_SHA256 = "cc1855c607ee394fae9e5ede594d1420f0c4d03cb8a5af08e14e6a656cd7d837"
R4_COMPOSITE_SEMANTIC_SHA256 = "5bb66b7ff15d1f9ef3e9ce7caa4ef2314fcc670cf80b9696ee576e1b9cac4eda"
R4_RAW_PIT_SHA256 = "bfdf6e3f87d5c42a1923bc4009f2fa1892ade7317e11caf139d30239bac3f5ae"
R4_RAW_PIT_BYTES = 24_774_560
R4_PIT_SNAPSHOT_COUNT = 2659
R4_SOURCE_RECEIPT_PATH = f"{R4_BUNDLE_PATH}/source_receipt.json"
R4_LIFETIME_RAW_SHA256 = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"

R5_SCANNER_VERSION = "equity-scanner-v3"
R5_SCANNER_CONFIG_DIGEST = "d8e06f54cd1a2fe0a201317fb6e3f4265470b2a10fe6e119ad81554128b33392"
R5_TOP_N = 8
R5_REQUIRED_COMMON_CLOSES = 274
R5_REQUIRED_MATRIX_ROWS = 273
R5_MINIMUM_HISTORY_BARS = 274
R5_RECENT_START = date(2022, 1, 1)
R5_GATE_PASS = "PASS"
R5_GATE_FAIL = "FAIL"
R5_GATE_UNKNOWN = "UNKNOWN"
R5_GATE_NOT_RUN = "NOT_RUN_BLOCKED"

R5_MANDATORY_GATES = (
    "commit_topology",
    "implementation_blob_identity",
    "manifest_only_commit",
    "clean_worktree_at_start",
    "configuration_identity",
    "dependency_identity",
    "r4_acquisition_identity",
    "r4_raw_bundle_replay_identity",
    "canonical_artifact_identity",
    "lifetime_identity",
    "corporate_action_identity",
    "iwm_raw_bundle",
    "iwm_provider_contract",
    "iwm_pagination_chain",
    "iwm_normalization",
    "request_boundary_conformance",
    "required_reference_session_completeness",
    "reference_calendar",
    "reference_row_quality",
    "reference_common_sessions",
    "full_weekly_post_warmup_coverage",
    "recent_weekly_coverage",
    "composite_dataset_identity",
    "pit_source_artifact_identity",
    "pit_evaluation_projection_identity",
    "pit_manifest_identity",
    "pit_reference_boundary",
    "pit_snapshot_cadence",
    "pit_terminal_boundary",
    "strict_prior_population",
    "full_timestamp_snapshot_selection",
    "deterministic_numerical_population",
    "m3_history_eligibility_parity",
    "full_candidate_depth_feasibility",
    "recent_candidate_depth_feasibility",
    "design_matrix_feasibility",
    "reference_role_contract",
    "receipt_forbidden_fields",
)

R5_CRITICAL_GIT_PATHS = (
    ".github/workflows/ci.yml",
    "scripts/m3_data_gate_helpers.py",
    "scripts/research_equity_m3_data_gate_r5.py",
    "scripts/research_equity_m3_m4.py",
    "scripts/build_m3_pit_scanner_universe.py",
    "src/parayabuck/backtest/universe.py",
    "src/parayabuck/backtest/portfolio_engine.py",
    "tests/test_research_equity_m3_r5.py",
)

R5_FROZEN_PARAMETERS: dict[str, object] = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "beta_window_plus_one_closes": 274,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}
R5_NUMERICAL_THRESHOLDS: dict[str, int | float] = {
    "matrix_rows": 273,
    "matrix_columns": 3,
    "required_rank": 3,
    "minimum_singular_value_ratio": 1e-6,
    "maximum_condition_number": 1e6,
    "minimum_residual_std": 1e-12,
    "required_common_closes": 274,
    "required_returns": 273,
}
R5_FORBIDDEN_ECONOMIC_FIELDS = [
    "return",
    "net_return",
    "gross_return",
    "pnl",
    "cagr",
    "sharpe",
    "sortino",
    "calmar",
    "max_drawdown",
    "turnover",
    "trade_count",
    "fill_count",
    "portfolio_targets",
    "economic_candidate_scores",
    "economic_candidate_ranking",
]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=_REPO, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _git_bytes(*args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=_REPO, check=True, capture_output=True)
    return result.stdout


def _logical_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO.resolve()).as_posix()
    except ValueError:
        return f"<external:{path.name}>"


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError("invalid_json", f"invalid JSON artifact: {path.name}") from exc
    if not isinstance(payload, Mapping):
        raise GateError("invalid_json", f"JSON artifact must be an object: {path.name}")
    return payload


def _load_yaml(path: Path) -> Mapping[str, object]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise GateError("invalid_config", f"invalid YAML config: {path.name}") from exc
    if not isinstance(payload, Mapping):
        raise GateError("invalid_config", "settings YAML must be an object")
    return payload


def _safe(value: object) -> object:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, Mapping):
        return {str(key): _safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(child) for child in value]
    return value


def _write_json(path: Path, payload: Mapping[str, object]) -> bytes:
    data = (json.dumps(_safe(payload), indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise GateError("output_conflict", f"output already exists with different bytes: {path.name}")
        return data
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            import os

            os.fsync(handle.fileno())
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return data


def _gate_map() -> dict[str, dict[str, object]]:
    return {name: {"status": R5_GATE_UNKNOWN, "reason": "not evaluated"} for name in R5_MANDATORY_GATES}


def _set_gate(gates: dict[str, dict[str, object]], name: str, status: str, **evidence: object) -> None:
    if status not in {R5_GATE_PASS, R5_GATE_FAIL, R5_GATE_UNKNOWN, R5_GATE_NOT_RUN}:
        raise ValueError(f"unsupported R5 gate state: {status}")
    gates[name] = {"status": status, **evidence}


def evaluate_mandatory_gates(
    gates: Mapping[str, Mapping[str, object]], required: Sequence[str] = R5_MANDATORY_GATES
) -> str:
    """Return PASS only when every required state is exactly PASS."""
    return (
        "M3_DATA_GATE_PASS"
        if all(gates.get(name, {}).get("status") == R5_GATE_PASS for name in required)
        else "M3_DATA_GATE_BLOCKED"
    )


def timestamp_for_session(session: date) -> datetime:
    """Return the exact daily bar boundary used by the frozen dataset."""
    return datetime(session.year, session.month, session.day, tzinfo=ZoneInfo(EXCHANGE_TIMEZONE)).astimezone(UTC)


def expected_evaluation_timestamps(sessions: Sequence[date]) -> tuple[str, ...]:
    """Encode the complete NYSE session set as timezone-aware timestamps."""
    return tuple(canonical_timestamp(timestamp_for_session(session)) for session in sessions)


def effective_timestamp_digest(timestamps: Sequence[str]) -> str:
    return canonical_digest({"encoding": "m3-r5-effective-at-sequence-v1", "timestamps": list(timestamps)})


def _contract_expected_timestamps(
    selection: Mapping[str, object], *, require_complete: bool = False
) -> tuple[str, ...]:
    """Resolve and validate the pre-result exact evaluation timestamp set."""
    declared = selection.get("expected_effective_at_timestamps")
    if declared is not None:
        if not isinstance(declared, list) or not all(isinstance(value, str) for value in declared):
            raise GateError("evaluation_contract_missing", "R5 exact evaluation timestamp set is invalid")
        expected = tuple(declared)
    else:
        compact = selection.get("expected_effective_at_set")
        if not isinstance(compact, Mapping):
            raise GateError("evaluation_contract_missing", "R5 exact evaluation timestamp set is missing")
        sessions = expected_exchange_sessions(REQUIRED_FIRST_REFERENCE_SESSION, EVALUATION_END_SESSION)
        expected = expected_evaluation_timestamps(sessions)
        if (
            compact.get("count") != len(expected)
            or compact.get("first") != expected[0]
            or compact.get("last") != expected[-1]
            or compact.get("digest_sha256") != effective_timestamp_digest(expected)
        ):
            raise GateError("evaluation_contract_mismatch", "R5 compact evaluation timestamp set drifted")
    if not expected:
        raise GateError("evaluation_contract_missing", "R5 exact evaluation timestamp set is empty")
    if require_complete and len(expected) != EXPECTED_REQUIRED_REFERENCE_SESSIONS:
        raise GateError("evaluation_contract_missing", "R5 exact evaluation timestamp set is incomplete")
    try:
        canonical_expected = tuple(canonical_timestamp(parse_timestamp(value)) for value in expected)
    except (TypeError, ValueError) as exc:
        raise GateError("evaluation_contract_mismatch", "R5 evaluation timestamps are not canonical UTC") from exc
    if canonical_expected != expected:
        raise GateError("evaluation_contract_mismatch", "R5 evaluation timestamps are not canonical UTC")
    if expected != tuple(sorted(expected)) or len(set(expected)) != len(expected):
        raise GateError("evaluation_contract_mismatch", "R5 expected timestamp set is not ordered and unique")
    if selection.get("expected_effective_at_digest_sha256") != effective_timestamp_digest(expected):
        raise GateError("evaluation_contract_digest_mismatch", "R5 expected timestamp-set digest drifted")
    return expected


def select_snapshot_for_cutoff(
    population: Mapping[str, object], cutoff_timestamp: datetime | str
) -> Mapping[str, object]:
    """Select the latest PIT snapshot with a full timestamp <= the cutoff."""
    cutoff = parse_timestamp(cutoff_timestamp)
    raw_snapshots = population.get("snapshots")
    if not isinstance(raw_snapshots, list):
        raise GateError("invalid_pit_universe", "PIT population snapshots must be a list")
    ordered: list[tuple[datetime, Mapping[str, object]]] = []
    seen: set[str] = set()
    for raw in raw_snapshots:
        if not isinstance(raw, Mapping):
            raise GateError("invalid_pit_snapshot", "PIT snapshot must be an object")
        effective = parse_timestamp(raw.get("effective_at"))
        text = canonical_timestamp(effective)
        if text in seen:
            raise GateError("duplicate_pit_snapshot", f"duplicate effective timestamp: {text}")
        seen.add(text)
        ordered.append((effective, raw))
    ordered.sort(key=lambda item: item[0])
    selected = [item for item in ordered if item[0] <= cutoff]
    if not selected:
        raise GateError("missing_pit_snapshot", "no PIT snapshot is effective at or before the decision cutoff")
    return selected[-1][1]


def candidate_symbols_for_timestamp(
    population: Mapping[str, object], cutoff_timestamp: datetime | str
) -> tuple[str, ...]:
    """Return candidate membership selected by full timestamp, never date-only."""
    snapshot = select_snapshot_for_cutoff(population, cutoff_timestamp)
    symbols = snapshot.get("eligible_symbols", snapshot.get("symbols", ()))
    if not isinstance(symbols, (list, tuple)):
        raise GateError("invalid_pit_snapshot", "PIT membership must be a sequence")
    return tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}))


def _timestamp_values(values: Sequence[datetime | str]) -> tuple[datetime, ...]:
    return tuple(sorted(parse_timestamp(value) for value in values))


def classify_candidate_history(
    *,
    candidate_symbol: str,
    scanner_eligible: bool,
    stock_timestamps: Sequence[datetime | str],
    reference_timestamps: Mapping[str, Sequence[datetime | str]],
    decision_cutoff: datetime | str,
    minimum_history_bars: int,
    required_common_closes: int = R5_REQUIRED_COMMON_CLOSES,
) -> dict[str, object]:
    """Mirror the M3 scorer's pre-numerical history predicate exactly."""
    candidate = str(candidate_symbol).strip().upper()
    cutoff = parse_timestamp(decision_cutoff)
    stock = tuple(value for value in _timestamp_values(stock_timestamps) if value <= cutoff)
    base = {
        "candidate_symbol": candidate,
        "scanner_eligible": scanner_eligible,
        "history_bar_count": len(stock),
        "required_history_bars": minimum_history_bars,
        "required_common_closes": required_common_closes,
    }
    if not scanner_eligible:
        return {**base, "state": "NOT_SCANNER_ELIGIBLE", "common_close_count": 0}
    if candidate in REFERENCE_SYMBOLS:
        return {**base, "state": "M3_MATRIX_NUMERICAL_FAIL", "common_close_count": 0, "reason": "reference_symbol"}
    if len(stock) < minimum_history_bars:
        return {**base, "state": "SCANNER_ELIGIBLE_M3_NOT_WARM", "common_close_count": 0}
    # ProxyMultifactorResidualMomentum takes stock_bars[-274:] before its
    # exact timestamp intersection.  Preserve that subtle ordering here.
    stock_window = stock[-minimum_history_bars:]
    common = set(stock_window)
    for symbol in REFERENCE_SYMBOLS:
        common &= {value for value in _timestamp_values(reference_timestamps.get(symbol, ())) if value <= cutoff}
    common_values = tuple(sorted(common))
    if len(common_values) < required_common_closes:
        return {
            **base,
            "state": "SCANNER_ELIGIBLE_M3_NOT_WARM",
            "common_close_count": len(common_values),
        }
    return {
        **base,
        "state": "M3_MATRIX_ELIGIBLE",
        "common_close_count": len(common_values),
        "selected_timestamps": tuple(canonical_timestamp(value) for value in common_values[-required_common_closes:]),
    }


def _validate_source_snapshot(snapshot: Mapping[str, object]) -> tuple[datetime, datetime]:
    effective = parse_timestamp(snapshot.get("effective_at"))
    latest = parse_timestamp(snapshot.get("latest_input_timestamp"))
    if latest >= effective or snapshot.get("strictly_prior_data") is not True:
        raise GateError("pit_future_leak", "PIT snapshot is not strictly prior")
    if snapshot.get("scanner_version") != R5_SCANNER_VERSION:
        raise GateError("pit_scanner_identity_mismatch", "PIT scanner version drifted")
    symbols = snapshot.get("eligible_symbols")
    legacy = snapshot.get("symbols")
    if not isinstance(symbols, list) or not isinstance(legacy, list) or symbols != legacy:
        raise GateError("pit_membership_mismatch", "PIT membership aliases are not identical canonical lists")
    normalized = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if normalized != symbols:
        raise GateError("pit_membership_mismatch", "PIT membership is not sorted and canonical")
    if snapshot.get("eligible_count") != len(symbols):
        raise GateError("pit_membership_mismatch", "PIT eligible count differs from membership")
    if snapshot.get("scanner_result_empty") is not (len(symbols) == 0):
        raise GateError("pit_snapshot_cadence", "PIT empty-snapshot flag differs from membership")
    return effective, latest


def _source_population_identity(source: Mapping[str, object]) -> dict[str, object]:
    snapshots = source.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise GateError("invalid_pit_universe", "R4 PIT source must contain snapshots")
    effective_values: list[str] = []
    for item in snapshots:
        if not isinstance(item, Mapping):
            raise GateError("invalid_pit_snapshot", "R4 PIT source snapshot is not an object")
        effective, _latest = _validate_source_snapshot(item)
        effective_values.append(canonical_timestamp(effective))
    if effective_values != sorted(effective_values) or len(set(effective_values)) != len(effective_values):
        raise GateError("pit_snapshot_cadence", "R4 PIT source timestamps are not strictly ordered")
    population = deterministic_candidate_population(snapshots, reference_symbols=REFERENCE_SYMBOLS)
    declared = source.get("population")
    if not isinstance(declared, Mapping):
        raise GateError("pit_summary_missing", "R4 PIT source population summary is missing")
    for key in (
        "candidate_universe_digest_sha256",
        "candidate_decision_eligibility_digest_sha256",
        "snapshot_count",
        "unique_candidate_count",
    ):
        if declared.get(key) != population.get(key):
            raise GateError("pit_digest_mismatch", f"R4 PIT source summary differs: {key}")
    strict_prior_proof = source.get("strict_prior_proof")
    if not isinstance(strict_prior_proof, Mapping) or strict_prior_proof.get("status") != "PASS":
        raise GateError("pit_future_leak", "R4 PIT source strict-prior proof is not PASS")
    validate_pit_reference_boundary(population)
    return {
        "effective_timestamps": tuple(effective_values),
        "population": population,
        "strict_prior_proof": strict_prior_proof,
    }


def project_evaluation_pit(
    source: Mapping[str, object],
    *,
    contract: Mapping[str, object],
    source_identity: Mapping[str, object],
    r4_manifest_identity: Mapping[str, object],
    canonical_identity: Mapping[str, object],
    sidecar_identity: Mapping[str, object],
    composite_identity: Mapping[str, object],
    contract_identity: Mapping[str, object],
) -> dict[str, object]:
    """Create a new deterministic evaluation projection without changing R4."""
    source_info = _source_population_identity(source)
    source_timestamps = tuple(source_info["effective_timestamps"])
    selection = contract.get("evaluation_snapshot_contract")
    if not isinstance(selection, Mapping):
        raise GateError("evaluation_contract_missing", "R5 evaluation snapshot contract is missing")
    expected_timestamps = _contract_expected_timestamps(selection)
    source_set = set(source_timestamps)
    expected_set = set(expected_timestamps)
    missing = sorted(expected_set - source_set)
    if missing:
        raise GateError("missing_evaluation_snapshot", f"R5 required evaluation snapshot is missing: {missing[0]}")
    source_by_timestamp = {
        canonical_timestamp(parse_timestamp(item.get("effective_at"))): item for item in source["snapshots"]
    }  # type: ignore[index]
    consumed_source = [source_by_timestamp[value] for value in expected_timestamps]
    cutoff = parse_timestamp(str(selection["evaluation_decision_cutoff_timestamp"]))
    extra_consumed = sorted(
        timestamp for timestamp in source_set if parse_timestamp(timestamp) <= cutoff and timestamp not in expected_set
    )
    if extra_consumed:
        raise GateError("extra_evaluation_snapshot", f"R5 unexpected consumed snapshot: {extra_consumed[0]}")
    post_evaluation = [
        item
        for timestamp, item in zip(source_timestamps, source["snapshots"], strict=True)
        if parse_timestamp(timestamp) > cutoff  # type: ignore[index]
    ]
    if set(source_timestamps) != expected_set | {
        canonical_timestamp(parse_timestamp(item.get("effective_at"))) for item in post_evaluation
    }:
        raise GateError("evaluation_snapshot_set_mismatch", "R5 source snapshot partition is not deterministic")
    projection_snapshots = [dict(item) for item in consumed_source]
    population = deterministic_candidate_population(projection_snapshots, reference_symbols=REFERENCE_SYMBOLS)
    role = validate_pit_reference_boundary(population)
    strict_violations = []
    for item in projection_snapshots:
        effective, latest = _validate_source_snapshot(item)
        if latest >= effective:
            strict_violations.append(canonical_timestamp(effective))
    if strict_violations:
        raise GateError("pit_future_leak", "R5 projection contains non-prior input")
    excluded_digest = canonical_digest({"encoding": "m3-r5-excluded-source-snapshots-v1", "snapshots": post_evaluation})
    projection_core: dict[str, object] = {
        "encoding": "m3-r5-pit-projection-v1",
        "source_snapshot_count": len(source_timestamps),
        "evaluation_snapshot_count": len(projection_snapshots),
        "source_first_snapshot": source_timestamps[0],
        "source_last_snapshot": source_timestamps[-1],
        "evaluation_first_snapshot": expected_timestamps[0],
        "evaluation_last_snapshot": expected_timestamps[-1],
        "evaluation_effective_at_timestamps": list(expected_timestamps),
        "excluded_post_evaluation_snapshot_count": len(post_evaluation),
        "excluded_post_evaluation_snapshot_digest_sha256": excluded_digest,
        "excluded_post_evaluation_reason": "source snapshot effective after evaluation decision cutoff; not consumed",
        "consumed_decision_count": len(expected_timestamps),
        "strict_prior_proof": {
            "status": "PASS",
            "effective_at_is_strictly_after_latest_input": True,
            "violating_snapshots": strict_violations,
        },
        "candidate_population_digest_sha256": population["candidate_universe_digest_sha256"],
        "candidate_decision_eligibility_digest_sha256": population["candidate_decision_eligibility_digest_sha256"],
        "full_timestamp_ordering": True,
        "reference_boundary": role,
    }
    projection_digest = canonical_digest(projection_core | {"snapshots": projection_snapshots})
    return {
        "schema_version": "m3-pit-scanner-population-v1",
        "mission": R5_PIT_MISSION,
        "market": "EQUITY",
        "timeframe": CANONICAL_TIMEFRAME,
        "methodology": "strictly_prior_data_production_scanner_eligible_universe",
        "observation_strategy": None,
        "alpha_ranking_used": False,
        "reference_symbols": list(REFERENCE_SYMBOLS),
        "scanner_rebalance": "daily",
        "scanner_rebalance_cadence": "daily",
        "scanner_versions": [R5_SCANNER_VERSION],
        "scanner_config_digests": [R5_SCANNER_CONFIG_DIGEST],
        "input_identity": {
            "r4_raw_pit": dict(source_identity),
            "r4_pit_manifest": dict(r4_manifest_identity),
            "canonical_bars": dict(canonical_identity),
            "r4_normalized_iwm_sidecar": dict(sidecar_identity),
            "r4_composite": dict(composite_identity),
            "r5_projection_implementation": dict(
                contract.get("critical_git_blobs", {}).get(  # type: ignore[union-attr]
                    "scripts/research_equity_m3_data_gate_r5.py", {}
                )
            ),
            "r5_contract": dict(contract_identity),
        },
        "no_alpha_assertion": {
            "observation_strategy_is_none": True,
            "candidate_rankings_present": False,
            "economic_ranking_present": False,
        },
        "strict_prior_proof": projection_core["strict_prior_proof"],
        "snapshots": projection_snapshots,
        "population": {
            "candidate_universe_digest_sha256": population["candidate_universe_digest_sha256"],
            "candidate_decision_eligibility_digest_sha256": population["candidate_decision_eligibility_digest_sha256"],
            "snapshot_count": population["snapshot_count"],
            "unique_candidate_count": population["unique_candidate_count"],
            "minimum_eligible_count": min(population["per_snapshot_eligible_count"]),  # type: ignore[arg-type]
            "maximum_eligible_count": max(population["per_snapshot_eligible_count"]),  # type: ignore[arg-type]
            "median_eligible_count": statistics.median(population["per_snapshot_eligible_count"]),  # type: ignore[arg-type]
            "explicit_empty_snapshot_count": sum(
                item.get("scanner_result_empty") is True for item in projection_snapshots
            ),
            "reference_boundary": role,
        },
        "r5_projection": {
            **projection_core,
            "projection_digest_sha256": projection_digest,
            "evaluation_decision_session": selection["evaluation_decision_session"],
            "evaluation_decision_cutoff_timestamp": selection["evaluation_decision_cutoff_timestamp"],
            "terminal_observation_boundary_required": selection["terminal_observation_boundary_required"],
        },
        "source_snapshot_timing": _build_source_timing(source),
    }


def frozen_m3_minimum_history_bars(parameters: Mapping[str, object] = R5_FROZEN_PARAMETERS) -> int:
    """Read the minimum-history value from a fresh frozen M3 strategy."""
    from research_equity_m3_m4 import ProxyMultifactorResidualMomentum  # noqa: PLC0415

    return int(ProxyMultifactorResidualMomentum(dict(parameters)).minimum_history_bars)


def _compact_matrix(result: Mapping[str, object]) -> dict[str, object]:
    return {
        "candidate_symbol": result.get("candidate_symbol"),
        "decision_session": result.get("decision_session"),
        "session_count": result.get("session_count"),
        "matrix_return_count": result.get("return_count"),
        "matrix_shape": result.get("matrix_shape"),
        "matrix_rank": result.get("matrix_rank"),
        "condition_number": result.get("condition_number"),
        "singular_value_ratio": result.get("singular_value_ratio"),
        "matrix_digest_sha256": result.get("matrix_digest_sha256"),
        "passed": result.get("passed"),
        "failure_categories": result.get("failure_categories", []),
    }


def _classify_array_candidate(
    *,
    candidate: str,
    scanner_eligible: bool,
    cutoff_index: int,
    cutoff_timestamp: int,
    timestamps: Mapping[str, np.ndarray],
    minimum_history_bars: int,
) -> tuple[dict[str, object], np.ndarray | None]:
    stock_values = timestamps[candidate]
    stock_visible = stock_values[(stock_values > 0) & (stock_values <= cutoff_timestamp)]
    base = {
        "candidate_symbol": candidate,
        "scanner_eligible": scanner_eligible,
        "history_bar_count": int(len(stock_visible)),
        "required_history_bars": minimum_history_bars,
        "required_common_closes": R5_REQUIRED_COMMON_CLOSES,
    }
    if not scanner_eligible:
        return ({**base, "state": "NOT_SCANNER_ELIGIBLE", "common_close_count": 0}, None)
    if len(stock_visible) < minimum_history_bars:
        return ({**base, "state": "SCANNER_ELIGIBLE_M3_NOT_WARM", "common_close_count": 0}, None)
    stock_window = stock_visible[-minimum_history_bars:]
    common = set(int(value) for value in stock_window)
    for reference in REFERENCE_SYMBOLS:
        common &= {int(value) for value in timestamps[reference] if value > 0 and value <= cutoff_timestamp}
    selected = np.asarray(sorted(common)[-R5_REQUIRED_COMMON_CLOSES:], dtype=np.int64)
    if len(selected) < R5_REQUIRED_COMMON_CLOSES:
        return (
            {**base, "state": "SCANNER_ELIGIBLE_M3_NOT_WARM", "common_close_count": len(common)},
            None,
        )
    # ``cutoff_index`` is included in the signature to make the visibility
    # boundary explicit at the call site and to guard against accidental date
    # based selection in future edits.
    del cutoff_index
    return (
        {
            **base,
            "state": "M3_MATRIX_ELIGIBLE",
            "common_close_count": len(common),
            "selected_timestamps": [str(int(value)) for value in selected],
        },
        selected,
    )


def _load_close_matrix(
    path: Path,
    *,
    symbols: Sequence[str],
    sessions: Sequence[date],
    sidecar_rows: Sequence[Any],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, object]]:
    """Load only the scanner population into compact, timestamped arrays."""
    unique_symbols = tuple(sorted({str(symbol).strip().upper() for symbol in symbols} | set(REFERENCE_SYMBOLS)))
    session_index = {session: index for index, session in enumerate(sessions)}
    close_arrays = {symbol: np.full(len(sessions), np.nan, dtype=np.float64) for symbol in unique_symbols}
    timestamp_arrays = {symbol: np.zeros(len(sessions), dtype=np.int64) for symbol in unique_symbols}
    duplicate_count = 0
    invalid_count = 0
    out_of_range_count = 0
    row_count = 0
    selected = set(unique_symbols) - {"IWM"}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != ["timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"]:
            raise GateError("canonical_schema_mismatch", "canonical CSV header differs from the frozen schema")
        for raw in reader:
            if len(raw) != 9:
                invalid_count += 1
                continue
            symbol = raw[1].strip().upper()
            if symbol not in selected:
                continue
            row_count += 1
            try:
                timestamp = parse_timestamp(raw[0])
                session = session_date_for_timestamp(timestamp)
                index = session_index.get(session)
                if index is None:
                    out_of_range_count += 1
                    continue
                if raw[2].strip().lower() not in {"1d", "1day"} or raw[8].strip().lower() != "equity":
                    invalid_count += 1
                    continue
                close = float(raw[6])
                if not math.isfinite(close) or close <= 0:
                    invalid_count += 1
                    continue
                timestamp_value = int(round(timestamp.timestamp() * 1_000_000))
                if timestamp_arrays[symbol][index] != 0:
                    duplicate_count += 1
                    continue
                close_arrays[symbol][index] = close
                timestamp_arrays[symbol][index] = timestamp_value
            except (TypeError, ValueError, OverflowError):
                invalid_count += 1
    iwm_index = {row.session_date: index for index, row in enumerate(sidecar_rows)}
    for row in sidecar_rows:
        index = session_index.get(row.session_date)
        if index is None:
            invalid_count += 1
            continue
        timestamp_value = int(round(row.timestamp.timestamp() * 1_000_000))
        if timestamp_arrays["IWM"][index] != 0:
            duplicate_count += 1
            continue
        close_arrays["IWM"][index] = float(row.close)
        timestamp_arrays["IWM"][index] = timestamp_value
    del iwm_index
    return (
        close_arrays,
        timestamp_arrays,
        {
            "selected_symbol_count": len(unique_symbols),
            "selected_canonical_row_count": row_count,
            "invalid_selected_row_count": invalid_count,
            "duplicate_selected_row_count": duplicate_count,
            "selected_canonical_out_of_range_row_count": out_of_range_count,
            "missing_timestamp_cells": int(sum(np.count_nonzero(values == 0) for values in timestamp_arrays.values())),
        },
    )


def candidate_depth_feasibility(
    decision_rows: Sequence[Mapping[str, object]],
    *,
    recent_decision_sessions: Sequence[date],
    top_n: int = R5_TOP_N,
) -> dict[str, object]:
    """Apply the full warm-up and no-warm-up recent depth rules."""
    rows = list(decision_rows)
    recent_set = set(recent_decision_sessions)
    feasible_indices = [index for index, row in enumerate(rows) if int(row.get("m3_matrix_eligible_count", 0)) >= top_n]
    first_index = feasible_indices[0] if feasible_indices else None
    post_rows = rows[first_index:] if first_index is not None else []
    recent_rows = [row for row in rows if date.fromisoformat(str(row["decision_session"])) in recent_set]
    post_failures = sum(
        int(row.get("m3_matrix_eligible_count", 0)) < top_n or int(row.get("numerical_fail_count", 0)) != 0
        for row in post_rows
    )
    recent_failures = sum(
        int(row.get("m3_matrix_eligible_count", 0)) < top_n or int(row.get("numerical_fail_count", 0)) != 0
        for row in recent_rows
    )
    return {
        "first_full_candidate_feasible_decision": (
            str(rows[first_index]["decision_session"]) if first_index is not None else None
        ),
        "full_initial_candidate_warmup_decision_count": first_index if first_index is not None else len(rows),
        "full_post_feasibility_failure_count": post_failures if first_index is not None else len(rows),
        "minimum_recent_eligible_depth": min(
            (int(row.get("m3_matrix_eligible_count", 0)) for row in recent_rows), default=0
        ),
        "recent_candidate_depth_failure_count": recent_failures,
        "full_candidate_depth_pass": first_index is not None and post_failures == 0,
        "recent_candidate_depth_pass": bool(recent_rows)
        and recent_failures == 0
        and min(int(row.get("m3_matrix_eligible_count", 0)) for row in recent_rows) >= top_n,
    }


def _array_panel(
    *,
    candidate: str,
    selected_timestamps: np.ndarray,
    close_arrays: Mapping[str, np.ndarray],
    timestamp_arrays: Mapping[str, np.ndarray],
) -> tuple[tuple[date, ...], dict[str, list[float]]]:
    dates: list[date] = []
    indices_by_symbol: dict[str, dict[int, int]] = {}
    for symbol in (candidate, *REFERENCE_SYMBOLS):
        indices_by_symbol[symbol] = {
            int(timestamp): index for index, timestamp in enumerate(timestamp_arrays[symbol]) if timestamp != 0
        }
    for timestamp_value in selected_timestamps:
        timestamp_int = int(timestamp_value)
        index = indices_by_symbol["SPY"].get(timestamp_int)
        if index is None:
            raise GateError("matrix_timestamp_alignment", "selected timestamp is absent from SPY")
        timestamp = datetime.fromtimestamp(timestamp_int / 1_000_000, tz=UTC)
        dates.append(session_date_for_timestamp(timestamp))
    closes = {
        symbol: [
            float(close_arrays[symbol][indices_by_symbol[symbol][int(timestamp_value)]])
            for timestamp_value in selected_timestamps
        ]
        for symbol in (candidate, *REFERENCE_SYMBOLS)
    }
    return tuple(dates), closes


def audit_matrix_population(
    *,
    projection: Mapping[str, object],
    full_decisions: Sequence[date],
    recent_decisions: Sequence[date],
    close_arrays: Mapping[str, np.ndarray],
    timestamp_arrays: Mapping[str, np.ndarray],
    sessions: Sequence[date],
    thresholds: Mapping[str, int | float] = R5_NUMERICAL_THRESHOLDS,
    minimum_history_bars: int = R5_MINIMUM_HISTORY_BARS,
) -> dict[str, object]:
    session_index = {session: index for index, session in enumerate(sessions)}
    timestamps_by_session = {session: int(timestamp_arrays["SPY"][index]) for session, index in session_index.items()}
    all_candidates = tuple(
        sorted(
            {
                str(symbol).strip().upper()
                for snapshot in projection.get("snapshots", ())
                if isinstance(snapshot, Mapping)
                for symbol in snapshot.get("eligible_symbols", ())
                if str(symbol).strip()
            }
        )
    )
    decision_rows: list[dict[str, object]] = []
    matrix_digests: list[str] = []
    failure_categories: dict[str, int] = {}
    total_pairs = not_warm = eligible_count = numerical_pass = numerical_fail = 0
    by_session: dict[str, dict[str, object]] = {}
    for decision in full_decisions:
        cutoff_value = timestamps_by_session.get(decision)
        if not cutoff_value:
            raise GateError("missing_reference_timestamp", f"missing exact cutoff timestamp: {decision}")
        cutoff_dt = datetime.fromtimestamp(cutoff_value / 1_000_000, tz=UTC)
        snapshot = select_snapshot_for_cutoff(projection, cutoff_dt)
        scanner_symbols = set(str(item).strip().upper() for item in snapshot.get("eligible_symbols", ()))
        counts = {
            "decision_session": decision.isoformat(),
            "decision_cutoff_timestamp": canonical_timestamp(cutoff_dt),
            "selected_snapshot_effective_at": canonical_timestamp(parse_timestamp(snapshot["effective_at"])),
            "selected_snapshot_latest_input_timestamp": canonical_timestamp(
                parse_timestamp(snapshot["latest_input_timestamp"])
            ),
            "strict_prior": parse_timestamp(snapshot["latest_input_timestamp"])
            < parse_timestamp(snapshot["effective_at"]),
            "scanner_candidate_count": len(scanner_symbols),
            "m3_not_warm_count": 0,
            "m3_matrix_eligible_count": 0,
            "numerical_pass_count": 0,
            "numerical_fail_count": 0,
            "candidate_pair_count": len(scanner_symbols),
        }
        total_pairs += len(scanner_symbols)
        for candidate in sorted(scanner_symbols):
            eligibility, selected = _classify_array_candidate(
                candidate=candidate,
                scanner_eligible=True,
                cutoff_index=session_index[decision],
                cutoff_timestamp=cutoff_value,
                timestamps=timestamp_arrays,
                minimum_history_bars=minimum_history_bars,
            )
            if eligibility["state"] == "SCANNER_ELIGIBLE_M3_NOT_WARM":
                not_warm += 1
                counts["m3_not_warm_count"] = int(counts["m3_not_warm_count"]) + 1
                continue
            if eligibility["state"] != "M3_MATRIX_ELIGIBLE" or selected is None:
                raise GateError("history_eligibility_state", "eligible scanner candidate has an invalid state")
            eligible_count += 1
            counts["m3_matrix_eligible_count"] = int(counts["m3_matrix_eligible_count"]) + 1
            selected_dates, closes = _array_panel(
                candidate=candidate,
                selected_timestamps=selected,
                close_arrays=close_arrays,
                timestamp_arrays=timestamp_arrays,
            )
            result = audit_design_matrix(candidate, decision, selected_dates, closes, thresholds=thresholds)
            digest = str(result["matrix_digest_sha256"])
            matrix_digests.append(digest)
            if result.get("passed") is True:
                numerical_pass += 1
                counts["numerical_pass_count"] = int(counts["numerical_pass_count"]) + 1
            else:
                numerical_fail += 1
                counts["numerical_fail_count"] = int(counts["numerical_fail_count"]) + 1
                for category in result.get("failure_categories", ()):
                    key = str(category)
                    failure_categories[key] = failure_categories.get(key, 0) + 1
        by_session[decision.isoformat()] = counts
        decision_rows.append(counts)
    recent_rows = [
        row for row in decision_rows if date.fromisoformat(str(row["decision_session"])) in set(recent_decisions)
    ]
    depth = candidate_depth_feasibility(
        decision_rows,
        recent_decision_sessions=recent_decisions,
    )
    return {
        "scanner_candidate_pair_count": total_pairs,
        "m3_not_warm_pair_count": not_warm,
        "total_eligible_matrix_count": eligible_count,
        "numerical_pass_count": numerical_pass,
        "numerical_fail_count": numerical_fail,
        "matrix_digest_merkle_root": merkle_root(matrix_digests),
        "failure_category_counts": dict(sorted(failure_categories.items())),
        "all_decisions": decision_rows,
        "weekly_eligibility_by_session": by_session,
        **depth,
        "recent_decision_count": len(recent_rows),
        "matrix_examples": [],
        "no_economic_scores": True,
        "full_decision_count": len(full_decisions),
        "full_decision_sessions_digest_sha256": session_dates_digest([item.isoformat() for item in full_decisions]),
        "recent_decision_sessions_digest_sha256": session_dates_digest([item.isoformat() for item in recent_decisions]),
        "candidate_symbol_count": len(all_candidates),
        "candidate_symbol_digest_sha256": canonical_digest(all_candidates),
        "full_decisions_are_subset": set(full_decisions) >= set(recent_decisions),
    }


def _timing_table(
    *,
    projection: Mapping[str, object],
    reference_rows: Mapping[str, Sequence[Any]],
    sessions: Sequence[date],
) -> list[dict[str, object]]:
    by_session = {row.session_date: row for row in reference_rows["SPY"]}
    candidates = [sessions[0], sessions[-2], sessions[-1]]
    source_snapshots = projection.get("source_snapshot_timing", ())
    source_by_session = {
        date.fromisoformat(str(item["session_date"])): item for item in source_snapshots if isinstance(item, Mapping)
    }
    rows: list[dict[str, object]] = []
    for session in candidates:
        row = by_session[session]
        snapshot = select_snapshot_for_cutoff(projection, row.timestamp)
        rows.append(
            {
                "session_date": session.isoformat(),
                "daily_bar_timestamp": canonical_timestamp(row.timestamp),
                "latest_scanner_input_timestamp": snapshot["latest_input_timestamp"],
                "pit_snapshot_effective_at": snapshot["effective_at"],
                "candidate_membership_effective_boundary": snapshot["effective_at"],
                "m3_matrix_final_close_session": session.isoformat(),
                "economic_decision_timestamp": canonical_timestamp(row.timestamp),
                "snapshot_consumed_by_decision": True,
                "strict_prior": parse_timestamp(snapshot["latest_input_timestamp"])
                < parse_timestamp(snapshot["effective_at"]),
            }
        )
    excluded = source_by_session.get(date(2026, 7, 31))
    if excluded is not None:
        rows.append(
            {
                "session_date": "2026-07-31",
                "daily_bar_timestamp": excluded["daily_bar_timestamp"],
                "latest_scanner_input_timestamp": excluded["latest_scanner_input_timestamp"],
                "pit_snapshot_effective_at": excluded["pit_snapshot_effective_at"],
                "candidate_membership_effective_boundary": excluded["pit_snapshot_effective_at"],
                "m3_matrix_final_close_session": "NOT_IN_EVALUATION",
                "economic_decision_timestamp": "NOT_RUN_OUT_OF_SCOPE",
                "snapshot_consumed_by_decision": False,
                "strict_prior": True,
            }
        )
    return rows


def _build_source_timing(source: Mapping[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw in source.get("snapshots", ()):
        if not isinstance(raw, Mapping):
            continue
        effective = parse_timestamp(raw["effective_at"])
        rows.append(
            {
                "session_date": session_date_for_timestamp(effective).isoformat(),
                "daily_bar_timestamp": canonical_timestamp(effective),
                "latest_scanner_input_timestamp": raw["latest_input_timestamp"],
                "pit_snapshot_effective_at": raw["effective_at"],
            }
        )
    return rows


def _r5_manifest_identity(path: Path, commit: str) -> dict[str, object]:
    logical = _logical_path(path)
    try:
        blob_id = _git("rev-parse", f"{commit}:{logical}")
        blob = _git_bytes("cat-file", "blob", f"{commit}:{logical}")
    except subprocess.CalledProcessError as exc:
        raise GateError("manifest_not_committed", f"R5 contract is not in {commit}: {logical}") from exc
    try:
        working = path.read_bytes()
    except OSError as exc:
        raise GateError("manifest_missing", f"R5 contract working copy is missing: {logical}") from exc
    if working != blob:
        raise GateError("mutable_manifest", "R5 working contract differs from its committed blob")
    return {"path": logical, "commit": commit, "git_blob_id": blob_id, "raw_sha256": _sha256_bytes(blob)}


def _load_r5_contract(path: Path, run_commit: str) -> tuple[Mapping[str, object], dict[str, object]]:
    identity = _r5_manifest_identity(path, run_commit)
    payload = _load_json(path)
    validate_r5_contract(payload)
    return payload, identity


def validate_r5_contract(contract: Mapping[str, object]) -> None:
    """Validate the immutable pre-result R5 contract, including no HEAD pins."""
    if contract.get("schema_version") != "5.0.0" or contract.get("mission") != R5_MISSION:
        raise GateError("manifest_schema_mismatch", "R5 contract schema or mission is incorrect")
    if contract.get("repository_url") != "https://github.com/batuhankiskac/ParayaBuck":
        raise GateError("manifest_repository_mismatch", "R5 repository is incorrect")
    if contract.get("branch") != "main" or contract.get("snapshot_date") != "2026-09-02":
        raise GateError("manifest_snapshot_mismatch", "R5 branch or snapshot date drifted")
    if contract.get("declared_before_results") is not True:
        raise GateError("manifest_not_predeclared", "R5 contract must be predeclared")
    for key, expected in (
        ("research_only", True),
        ("automatic_promotion", False),
        ("human_promotion_only", True),
        ("candidate_economics_run", False),
        ("economic_metrics_present", False),
        ("economic_mission_ready", False),
    ):
        if contract.get(key) != expected:
            raise GateError("manifest_policy_mismatch", f"R5 policy field drifted: {key}")
    if contract.get("base_commit") != R5_BASE_COMMIT:
        raise GateError("base_commit_mismatch", "R5 implementation must descend directly from D6")
    implementation_parent = contract.get("implementation_parent_commit")
    if implementation_parent == R5_BASE_COMMIT:
        if contract.get("recovery_lineage") is not None:
            raise GateError("recovery_lineage_mismatch", "initial R5 contract cannot declare recovery lineage")
    elif implementation_parent == R5_INITIAL_MANIFEST_COMMIT:
        expected_recovery = {
            "status": "ABANDONED_A10_B10",
            "abandoned_implementation_commit": R5_INITIAL_IMPLEMENTATION_COMMIT,
            "abandoned_manifest_commit": R5_INITIAL_MANIFEST_COMMIT,
            "reason_category": "runtime_helper_missing",
            "reason": "A10 R5 gate called a non-existent R4 config helper before replay",
            "corrective_base_commit": R5_INITIAL_MANIFEST_COMMIT,
            "replay_exact_captured_bytes": True,
            "automatic_refetch_allowed": False,
        }
        if contract.get("recovery_lineage") != expected_recovery:
            raise GateError("recovery_lineage_mismatch", "R5 A11/B11 recovery lineage is not declared exactly")
    elif implementation_parent == R5_SECOND_MANIFEST_COMMIT:
        expected_recovery = {
            "status": "ABANDONED_A11_B11",
            "abandoned_implementation_commit": R5_SECOND_IMPLEMENTATION_COMMIT,
            "abandoned_manifest_commit": R5_SECOND_MANIFEST_COMMIT,
            "reason_category": "canonical_helper_missing",
            "reason": "B11 R5 gate called a non-existent R4 canonical-input helper before replay",
            "corrective_base_commit": R5_SECOND_MANIFEST_COMMIT,
            "replay_exact_captured_bytes": True,
            "automatic_refetch_allowed": False,
        }
        if contract.get("recovery_lineage") != expected_recovery:
            raise GateError("recovery_lineage_mismatch", "R5 A12/B12 recovery lineage is not declared exactly")
    elif implementation_parent == R5_CANONICAL_FIX_COMMIT:
        expected_recovery = {
            "status": "ABANDONED_A11_B11",
            "abandoned_implementation_commit": R5_SECOND_IMPLEMENTATION_COMMIT,
            "abandoned_manifest_commit": R5_SECOND_MANIFEST_COMMIT,
            "reason_category": "canonical_helper_missing",
            "reason": "B11 R5 gate called a non-existent R4 canonical-input helper before replay",
            "corrective_base_commit": R5_CANONICAL_FIX_COMMIT,
            "replay_exact_captured_bytes": True,
            "automatic_refetch_allowed": False,
        }
        if contract.get("recovery_lineage") != expected_recovery:
            raise GateError("recovery_lineage_mismatch", "R5 A13/B13 recovery lineage is not declared exactly")
    elif implementation_parent == R5_THIRD_MANIFEST_COMMIT:
        expected_recovery = {
            "status": "ABANDONED_A13_B13",
            "abandoned_implementation_commit": R5_THIRD_IMPLEMENTATION_COMMIT,
            "abandoned_manifest_commit": R5_THIRD_MANIFEST_COMMIT,
            "reason_category": "sidecar_helper_missing",
            "reason": "B13 R5 gate called a non-existent R4 sidecar semantic helper before replay",
            "corrective_base_commit": R5_THIRD_MANIFEST_COMMIT,
            "replay_exact_captured_bytes": True,
            "automatic_refetch_allowed": False,
        }
        if contract.get("recovery_lineage") != expected_recovery:
            raise GateError("recovery_lineage_mismatch", "R5 A14/B14 recovery lineage is not declared exactly")
    elif implementation_parent == R5_FOURTH_MANIFEST_COMMIT:
        expected_recovery = {
            "status": "ABANDONED_A14_B14",
            "abandoned_implementation_commit": R5_FOURTH_IMPLEMENTATION_COMMIT,
            "abandoned_manifest_commit": R5_FOURTH_MANIFEST_COMMIT,
            "reason_category": "post_cutoff_candidate_rows",
            "reason": "B14 R5 gate counted valid post-cutoff canonical rows as invalid candidate rows before replay",
            "corrective_base_commit": R5_FOURTH_MANIFEST_COMMIT,
            "replay_exact_captured_bytes": True,
            "automatic_refetch_allowed": False,
        }
        if contract.get("recovery_lineage") != expected_recovery:
            raise GateError("recovery_lineage_mismatch", "R5 A15/B15 recovery lineage is not declared exactly")
    elif implementation_parent == R5_SIXTH_MANIFEST_COMMIT:
        expected_recovery = {
            "status": "ABANDONED_A16_B16",
            "abandoned_implementation_commit": R5_SIXTH_IMPLEMENTATION_COMMIT,
            "abandoned_manifest_commit": R5_SIXTH_MANIFEST_COMMIT,
            "reason_category": "lineage_validator_missing",
            "reason": "B16 R5 manifest failed because A16 lacked the B15 recovery branch before replay",
            "corrective_base_commit": R5_SIXTH_MANIFEST_COMMIT,
            "replay_exact_captured_bytes": True,
            "automatic_refetch_allowed": False,
        }
        if contract.get("recovery_lineage") != expected_recovery:
            raise GateError("recovery_lineage_mismatch", "R5 A17/B17 recovery lineage is not declared exactly")
    elif implementation_parent == R5_C10_RESULT_COMMIT:
        expected_recovery = {
            "status": "SUPERSEDED_A17_B17",
            "abandoned_implementation_commit": R5_SEVENTH_IMPLEMENTATION_COMMIT,
            "abandoned_manifest_commit": R5_SEVENTH_MANIFEST_COMMIT,
            "reason_category": "pre_result_test_coverage_gap",
            "reason": "C10 R5 result lacked explicit clean-run and complete synthetic-fixture regression coverage before test hardening",
            "corrective_base_commit": R5_C10_RESULT_COMMIT,
            "replay_exact_captured_bytes": True,
            "automatic_refetch_allowed": False,
        }
        if contract.get("recovery_lineage") != expected_recovery:
            raise GateError(
                "recovery_lineage_mismatch", "R5 A18/B18 test-hardening recovery lineage is not declared exactly"
            )
    else:
        raise GateError(
            "implementation_parent_mismatch", "R5 implementation parent is not D6 or an accepted recovery result"
        )
    if contract.get("manifest_path") != R5_MANIFEST_PATH:
        raise GateError("manifest_path_mismatch", "R5 contract path drifted")
    if contract.get("manifest_commit_rule") != "manifest-only: exactly the declared R5 manifest path":
        raise GateError("manifest_commit_rule_mismatch", "R5 manifest-only rule drifted")
    implementation = contract.get("implementation_commit")
    if (
        not isinstance(implementation, str)
        or not re.fullmatch(r"[0-9a-f]{40}", implementation)
        or implementation == "HEAD"
    ):
        raise GateError("implementation_pin_missing", "R5 implementation commit must be an exact SHA")
    tree = contract.get("implementation_tree")
    if not isinstance(tree, str) or not re.fullmatch(r"[0-9a-f]{40}", tree) or tree == "HEAD":
        raise GateError("implementation_tree_missing", "R5 implementation tree must be an exact SHA")
    critical = contract.get("critical_git_blobs")
    if not isinstance(critical, Mapping) or set(critical) != set(R5_CRITICAL_GIT_PATHS):
        raise GateError("critical_blob_pin_invalid", "R5 critical Git blob set is incomplete")
    for path, declaration in critical.items():
        if not isinstance(declaration, Mapping):
            raise GateError("critical_blob_pin_invalid", f"R5 blob declaration is invalid: {path}")
        if (
            not re.fullmatch(r"[0-9a-f]{40}", str(declaration.get("git_blob_id", "")))
            or str(declaration.get("git_blob_id")) == "HEAD"
        ):
            raise GateError("symbolic_provenance", f"R5 critical blob is symbolic: {path}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(declaration.get("raw_sha256", ""))):
            raise GateError("critical_blob_pin_invalid", f"R5 blob hash is invalid: {path}")
    if contract.get("frozen_parameters") != R5_FROZEN_PARAMETERS:
        raise GateError("frozen_parameter_mismatch", "R5 frozen M3 parameters drifted")
    if contract.get("numerical_thresholds") != R5_NUMERICAL_THRESHOLDS:
        raise GateError("numerical_threshold_mismatch", "R5 numerical thresholds drifted")
    if contract.get("mandatory_data_pass_gates") != list(R5_MANDATORY_GATES):
        raise GateError("mandatory_gate_contract_mismatch", "R5 mandatory gate list drifted")
    timing = contract.get("evaluation_snapshot_contract")
    if not isinstance(timing, Mapping):
        raise GateError("evaluation_contract_missing", "R5 evaluation snapshot contract is missing")
    if timing.get("evaluation_decision_session") != EVALUATION_END_SESSION.isoformat():
        raise GateError("evaluation_contract_mismatch", "R5 evaluation session drifted")
    if timing.get("terminal_observation_boundary_required") is not False:
        raise GateError("terminal_boundary_ambiguous", "R5 must freeze the non-post-evaluation terminal interpretation")
    timestamps = _contract_expected_timestamps(timing, require_complete=True)
    if timing.get("evaluation_decision_cutoff_timestamp") != timestamps[-1]:
        raise GateError("evaluation_contract_mismatch", "R5 decision cutoff is not the terminal evaluation timestamp")
    if contract.get("no_network_policy") != {
        "network_allowed": False,
        "credential_reads_allowed": False,
        "accepted_source": "exact accepted R4 captured bytes only",
        "refetch_allowed": False,
    }:
        raise GateError("network_contract_mismatch", "R5 no-network policy drifted")
    if contract.get("economic_boundary") != {
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "alpha_ranking_allowed": False,
        "optimization_allowed": False,
        "candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
        "next_required_gate": "M3_CANDIDATE_OCP_GATE",
    }:
        raise GateError("economic_boundary_mismatch", "R5 economic/OCP boundary drifted")
    if contract.get("forbidden_economic_fields") != R5_FORBIDDEN_ECONOMIC_FIELDS:
        raise GateError("forbidden_field_contract_mismatch", "R5 forbidden field list drifted")
    if any(_contains_symbolic_head(value) for value in (contract.get("immutable_provenance"),)):
        raise GateError("symbolic_provenance", "R5 immutable provenance contains symbolic HEAD")


def _contains_symbolic_head(value: object) -> bool:
    if isinstance(value, str):
        return value == "HEAD"
    if isinstance(value, Mapping):
        return any(_contains_symbolic_head(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_symbolic_head(child) for child in value)
    return False


def _validate_r5_provenance(contract: Mapping[str, object], run_commit: str, run_parent: str) -> dict[str, object]:
    implementation = str(contract["implementation_commit"])
    implementation_parent = _git("rev-parse", f"{implementation}^")
    if implementation_parent != contract["implementation_parent_commit"]:
        raise GateError("implementation_parent_mismatch", "R5 implementation parent differs from the contract")
    if run_parent != implementation:
        raise GateError("manifest_commit_topology", "B10 must be a direct child of A10")
    changed = _git("diff-tree", "--no-commit-id", "--name-only", "-r", run_commit).splitlines()
    if changed != [R5_MANIFEST_PATH]:
        raise GateError("manifest_commit_not_manifest_only", "B10 changed paths are not exactly the R5 manifest")
    implementation_tree = _git("rev-parse", f"{implementation}^{{tree}}")
    if implementation_tree != contract["implementation_tree"]:
        raise GateError("implementation_tree_mismatch", "R5 implementation tree differs from contract")
    checked: dict[str, object] = {}
    critical = contract["critical_git_blobs"]
    assert isinstance(critical, Mapping)
    for path, declaration in critical.items():
        if not isinstance(declaration, Mapping):
            raise GateError("critical_blob_pin_invalid", f"R5 critical declaration invalid: {path}")
        implementation_blob = _git("rev-parse", f"{implementation}:{path}")
        run_blob = _git("rev-parse", f"{run_commit}:{path}")
        blob_bytes = _git_bytes("cat-file", "blob", f"{implementation}:{path}")
        if implementation_blob != run_blob or declaration.get("git_blob_id") != implementation_blob:
            raise GateError("implementation_blob_mismatch", f"R5 critical blob drifted: {path}")
        if declaration.get("raw_sha256") != _sha256_bytes(blob_bytes):
            raise GateError("implementation_blob_hash_mismatch", f"R5 critical blob hash drifted: {path}")
        checked[path] = {"git_blob_id": implementation_blob, "raw_sha256": _sha256_bytes(blob_bytes)}
    return {
        "base_commit": R5_BASE_COMMIT,
        "implementation_commit": implementation,
        "implementation_tree": implementation_tree,
        "manifest_commit": run_commit,
        "manifest_git_blob_id": _git("rev-parse", f"{run_commit}:{R5_MANIFEST_PATH}"),
        "critical_git_blobs": checked,
        "changed_paths": changed,
    }


def _artifact_identity(path: Path, staging_dir: Path, *, logical_id: str, provenance: str) -> dict[str, object]:
    staged = stage_file(path, staging_dir)
    return {"path": _logical_path(path), **staged.receipt(logical_id, provenance)}


def _r4_pit_manifest_identity(path: Path, run_commit: str) -> dict[str, object]:
    identity = r4_gate._tracked_blob_identity(path, commit=run_commit)
    if identity is None:
        raise GateError("r4_manifest_identity", "R4 compact PIT manifest is not tracked")
    payload = _load_json(path)
    if payload.get("raw_pit_sha256") != R4_RAW_PIT_SHA256 or payload.get("raw_pit_byte_size") != R4_RAW_PIT_BYTES:
        raise GateError("r4_pit_identity", "R4 compact PIT identity drifted")
    return identity


def _validate_r4_source_pit(
    path: Path, pit_manifest_path: Path, staging_dir: Path, run_commit: str
) -> tuple[Mapping[str, object], dict[str, object]]:
    staged = stage_file(path, staging_dir, expected_sha256=R4_RAW_PIT_SHA256)
    if staged.byte_size != R4_RAW_PIT_BYTES:
        raise GateError("r4_pit_identity", "R4 raw PIT byte size drifted")
    source = _load_json(staged.staged_path)
    if source.get("mission") != "equity_m3_pit_scanner_population_2026_09_r4":
        raise GateError("r4_pit_identity", "R4 raw PIT mission drifted")
    identity = {
        "path": _logical_path(path),
        "raw_sha256": staged.raw_sha256,
        "byte_size": staged.byte_size,
        "source_compact_manifest": _r4_pit_manifest_identity(pit_manifest_path, run_commit),
    }
    return source, identity


def _r4_source_timing_table(source: Mapping[str, object]) -> list[dict[str, object]]:
    return _build_source_timing(source)


def _validate_r4_contract_identity(run_commit: str) -> dict[str, object]:
    path = _REPO / R4_MANIFEST_PATH
    identity = r4_gate._tracked_blob_identity(path, commit=run_commit)
    if identity is None:
        raise GateError("r4_manifest_identity", "R4 manifest is not tracked")
    payload = _load_json(path)
    if payload.get("mission") != "equity_m3_data_gate_2026_09_r4":
        raise GateError("r4_manifest_identity", "R4 manifest mission drifted")
    return identity


def _validate_contract_r4_values(contract: Mapping[str, object], r4_contract_identity: Mapping[str, object]) -> None:
    inputs = contract.get("accepted_r4_inputs")
    if not isinstance(inputs, Mapping):
        raise GateError("r4_identity_contract_missing", "R5 accepted R4 input identity is missing")
    expected = {
        "raw_bundle_sha256": R4_RAW_BUNDLE_SHA256,
        "aggregate_page_hash_digest": R4_PAGE_DIGEST,
        "page_chain_digest_sha256": R4_PAGE_CHAIN_DIGEST,
        "source_receipt_sha256": R4_SOURCE_RECEIPT_SHA256,
        "normalized_sidecar_raw_sha256": R4_SIDECAR_RAW_SHA256,
        "normalized_sidecar_semantic_sha256": R4_SIDECAR_SEMANTIC_SHA256,
        "composite_semantic_digest_sha256": R4_COMPOSITE_SEMANTIC_SHA256,
        "raw_pit_sha256": R4_RAW_PIT_SHA256,
        "raw_pit_byte_size": R4_RAW_PIT_BYTES,
        "r4_manifest_git_blob_id": r4_contract_identity["git_blob_id"],
    }
    if any(inputs.get(key) != value for key, value in expected.items()):
        raise GateError("r4_identity_contract_mismatch", "R5 accepted R4 identity differs")


def _config_equity(config_data: Mapping[str, object]) -> Any:
    universe = config_data.get("universe")
    equity_raw = universe.get("equity") if isinstance(universe, Mapping) else None
    if not isinstance(equity_raw, Mapping):
        raise GateError("equity_config_missing", "settings universe.equity is missing")
    equity_values = dict(equity_raw)
    scanner_raw = equity_values.pop("scanner", {})
    if not isinstance(scanner_raw, Mapping):
        raise GateError("equity_scanner_config_missing", "settings universe.equity.scanner is missing")
    from parayabuck.config import EquityScannerConfig, EquityUniverseConfig  # noqa: PLC0415

    return EquityUniverseConfig(scanner=EquityScannerConfig(**dict(scanner_raw)), **equity_values)


def _reference_inputs(
    canonical_path: Path, sidecar_rows: Sequence[Any], staging_dir: Path
) -> tuple[dict[str, list[Any]], dict[str, dict[str, int]], dict[str, object], dict[str, object], dict[str, object]]:
    rows, quality = r4_gate.load_csv_reference_rows(
        canonical_path,
        reference_symbols=("SPY", "QQQ"),
        source_start=REQUIRED_FIRST_REFERENCE_SESSION,
        source_end=REQUIRED_LAST_REFERENCE_SESSION,
        request_start_utc=REQUEST_START_UTC,
        request_end_boundary_utc=REQUEST_END_BOUNDARY_UTC,
    )
    rows["IWM"] = list(sidecar_rows)
    quality["IWM"] = {
        "raw_row_count": len(sidecar_rows),
        "raw_reference_rows": len(sidecar_rows),
        "normalized_reference_rows": len(sidecar_rows),
        "rejected_row_count": 0,
        "post_evaluation_valid_rows": 0,
        "zero_volume_count": sum(row.volume == 0 for row in sidecar_rows),
        "duplicate_count": 0,
        "conflicting_duplicate_count": 0,
    }
    expected = expected_exchange_sessions(REQUIRED_FIRST_REFERENCE_SESSION, REQUIRED_LAST_REFERENCE_SESSION)
    source_identity = {
        "provider": "Alpaca Market Data API",
        "api_family_version": "v2 stocks bars",
        "endpoint_identity": "/v2/stocks/bars",
        "feed": SOURCE_FEED,
        "adjustment": SOURCE_ADJUSTMENT,
        "source_timeframe": CANONICAL_TIMEFRAME,
        "sort": "asc",
    }
    inventory = {
        symbol: build_reference_inventory(
            rows.get(symbol, ()),
            raw_quality=quality.get(symbol),
            expected_sessions=expected,
            evaluation_end=EVALUATION_END_SESSION,
            source_identity=source_identity,
        )
        for symbol in REFERENCE_SYMBOLS
    }
    common = common_session_report(rows, expected_sessions=expected, evaluation_end=EVALUATION_END_SESSION)
    weekly_full = weekly_decision_coverage(
        rows,
        expected_sessions=expected,
        start=REQUIRED_FIRST_REFERENCE_SESSION,
        end=EVALUATION_END_SESSION,
    )
    weekly_recent = weekly_decision_coverage(
        rows,
        expected_sessions=expected,
        start=R5_RECENT_START,
        end=EVALUATION_END_SESSION,
    )
    del staging_dir
    return rows, quality, inventory, common, {"full": weekly_full, "recent": weekly_recent}


def _validate_references(
    inventory: Mapping[str, Mapping[str, object]],
    common: Mapping[str, object],
    weekly: Mapping[str, Mapping[str, object]],
) -> tuple[bool, bool, bool, bool, bool]:
    source_complete = all(
        int(inventory.get(symbol, {}).get("normalized_row_count", 0)) == EXPECTED_REQUIRED_REFERENCE_SESSIONS
        and not inventory.get(symbol, {}).get("missing_expected_sessions")
        and not inventory.get(symbol, {}).get("extra_off_calendar_sessions")
        for symbol in REFERENCE_SYMBOLS
    )
    quality_ok = r4_gate._r4_inventory_quality_pass(inventory)
    common_ok = int(common.get("common_session_count", 0)) == EXPECTED_REQUIRED_REFERENCE_SESSIONS
    full_ok = r4_gate._r3_weekly_full_pass(weekly["full"])
    recent_ok = r4_gate._r3_weekly_recent_pass(weekly["recent"])
    return source_complete, quality_ok, common_ok, full_ok, recent_ok


def _render_report(receipt: Mapping[str, object]) -> str:
    verdict = str(receipt.get("data_gate_verdict", "M3_DATA_GATE_BLOCKED"))
    boundary = receipt.get("boundary", {})
    timing = receipt.get("pit_timing_table", [])
    gates = receipt.get("mandatory_gate_results", {})
    commits = receipt.get("commits", {})
    population = receipt.get("pit_population", {})
    matrices = receipt.get("numerical_feasibility", {})
    lines = [
        "# Equity M3 data gate R5",
        "",
        "## 1. Executive verdict",
        "",
        f"`{verdict}`",
        "",
        "## 2. Boundary",
        "",
        f"- Corrective infrastructure: `{receipt.get('corrective_infrastructure_verdict')}`",
        f"- Candidate OCP: `{boundary.get('m3_candidate_ocp_status')}`",
        f"- Economic readiness: `{boundary.get('economic_mission_ready')}`",
        f"- Next required gate: `{boundary.get('next_required_gate')}`",
        "",
        "## 3. A10/B10/C10/D10 commits",
        "",
        f"`{json.dumps(commits, sort_keys=True)}`",
        "",
        "## 4. B10 manifest-only proof",
        "",
        f"`{json.dumps(receipt.get('manifest_only_proof', {}), sort_keys=True)}`",
        "",
        "## 5. Exact files changed",
        "",
        "R5 implementation, tests, the pre-result manifest, and compact result artifacts are isolated to the declared append-only chain.",
        "",
        "## 6. R4 immutable input verification",
        "",
        f"`{json.dumps(receipt.get('r4_immutable_inputs', {}), sort_keys=True)}`",
        "",
        "## 7. Network statement",
        "",
        "No network calls or credential reads occurred; only exact accepted R4 bytes were replayed.",
        "",
        "## 8. PIT timing proof table",
        "",
        "| session | daily timestamp | scanner input | PIT effective | membership boundary | M3 matrix session | decision timestamp | consumed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in timing:
        lines.append(
            "| {session_date} | {daily_bar_timestamp} | {latest_scanner_input_timestamp} | {pit_snapshot_effective_at} | {candidate_membership_effective_boundary} | {m3_matrix_final_close_session} | {economic_decision_timestamp} | {snapshot_consumed_by_decision} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## 9. Terminal snapshot decision",
            "",
            f"`{json.dumps(receipt.get('terminal_snapshot_decision', {}), sort_keys=True)}`",
            "",
            "## 10. Clean-run proof",
            "",
            f"`{json.dumps(receipt.get('worktree_cleanliness', {}), sort_keys=True)}`",
            "",
            "## 11. PIT population",
            "",
            f"`{json.dumps(population, sort_keys=True)}`",
            "",
            "## 12. M3 history eligibility",
            "",
            f"`{json.dumps(receipt.get('history_eligibility', {}), sort_keys=True)}`",
            "",
            "## 13. Numerical feasibility",
            "",
            f"`{json.dumps(matrices, sort_keys=True)}`",
            "",
            "## 14. Mandatory gate table",
            "",
            "| gate | status |",
            "|---|---|",
        ]
    )
    if isinstance(gates, Mapping):
        for name in R5_MANDATORY_GATES:
            item = gates.get(name, {})
            status = item.get("status") if isinstance(item, Mapping) else "NOT_RUN_BLOCKED"
            lines.append(f"| {name} | {status} |")
    lines.extend(
        [
            "",
            "## 15. Commands actually executed",
            "",
            f"`{json.dumps(receipt.get('commands_actually_executed', []), sort_keys=True)}`",
            "",
            "## 16. Local and remote CI",
            "",
            f"`{json.dumps(receipt.get('ci', {}), sort_keys=True)}`",
            "",
            "## 17. Tracked versus ignored artifacts",
            "",
            "Compact C10 artifacts are tracked; raw R4 pages, the R4 sidecar, the R4 full PIT artifact, and the R5 full projection remain ignored external/derived inputs.",
            "",
            "## 18. Remaining blockers",
            "",
            f"`{json.dumps(receipt.get('blocked_reasons', []), sort_keys=True)}`",
            "",
            "## 19. Explicit confirmations",
            "",
            "No refetch, fabricated/substituted/filled IWM, credential access, economic P&L, alpha ranking, optimization, candidate OCP, promotion, PAPER/LIVE change, V3 re-freeze, or history rewrite occurred.",
            "",
            verdict,
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def run_gate(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    """Run R5 from a clean B10 and write only externally supplied outputs."""
    run_commit = _git("rev-parse", "HEAD")
    run_parent = _git("rev-parse", "HEAD^")
    dirty_before = _git("status", "--short", "--untracked-files=all").splitlines()
    gates = _gate_map()
    reasons: list[str] = []
    contract: Mapping[str, object] = {}
    contract_identity: dict[str, object] = {}
    provenance: dict[str, object] = {}
    projection: dict[str, object] | None = None
    matrices: dict[str, object] = {}
    reference_rows: dict[str, list[Any]] = {}
    source: Mapping[str, object] | None = None
    weekly_raw: dict[str, Mapping[str, object]] = {}
    worktree = {"before": dirty_before, "clean_worktree_at_start": not dirty_before}
    if dirty_before:
        reasons.append("pre-existing tracked worktree changes at gate start")
        _set_gate(gates, "clean_worktree_at_start", R5_GATE_FAIL, paths=dirty_before)
    else:
        _set_gate(gates, "clean_worktree_at_start", R5_GATE_PASS, paths=[])
    try:
        contract, contract_identity = _load_r5_contract(Path(args.contract), run_commit)
        provenance = _validate_r5_provenance(contract, run_commit, run_parent)
        _set_gate(gates, "commit_topology", R5_GATE_PASS, evidence=provenance)
        _set_gate(gates, "implementation_blob_identity", R5_GATE_PASS, evidence=provenance["critical_git_blobs"])
        _set_gate(gates, "manifest_only_commit", R5_GATE_PASS, changed_paths=provenance["changed_paths"])
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        reasons.append(str(exc))
        _set_gate(gates, "commit_topology", R5_GATE_FAIL, reason=str(exc))
        _set_gate(gates, "implementation_blob_identity", R5_GATE_FAIL, reason=str(exc))
        _set_gate(gates, "manifest_only_commit", R5_GATE_FAIL, reason=str(exc))

    receipt: dict[str, object] = {
        "schema_version": "5.0.0",
        "mission": R5_MISSION,
        "run_commit": run_commit,
        "run_parent": run_parent,
        "commits": {"d6_base": R5_BASE_COMMIT, "b10": run_commit, "a10": contract.get("implementation_commit")},
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_ready": False,
        "m3_candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
        "next_required_gate": "M3_CANDIDATE_OCP_GATE",
        "worktree_cleanliness": worktree,
        "mandatory_gate_results": gates,
        "commands_actually_executed": list(sys.argv),
    }

    with tempfile.TemporaryDirectory(prefix="m3-data-gate-r5-") as temporary:
        staging_dir = Path(temporary)
        r4_contract_identity: dict[str, object] = {}
        canonical_identity: dict[str, object] = {}
        sidecar_identity: dict[str, object] = {}
        composite_identity: dict[str, object] = {}
        iwm_audit: dict[str, object] = {}
        try:
            config_data = _load_yaml(CONFIG_PATH)
            config_digest = config_semantic_digest(config_data)
            if config_digest != contract.get("config_semantic_digest_sha256"):
                raise GateError("config_digest_mismatch", "settings semantic digest differs from R5 contract")
            equity_config = _config_equity(config_data)
            from parayabuck.universe.scanner import scanner_config_digest  # noqa: PLC0415

            scanner_digest = scanner_config_digest(equity_config)
            if scanner_digest != R5_SCANNER_CONFIG_DIGEST or scanner_digest != contract.get("scanner_config_digest"):
                raise GateError("scanner_config_mismatch", "scanner config digest differs from R5 contract")
            _set_gate(
                gates,
                "configuration_identity",
                R5_GATE_PASS,
                semantic_digest_sha256=config_digest,
                scanner_config_digest=scanner_digest,
            )
            if contract.get("frozen_parameters") != R5_FROZEN_PARAMETERS:
                raise GateError("frozen_parameter_mismatch", "R5 frozen parameters drifted")
            _set_gate(gates, "dependency_identity", R5_GATE_PASS, calendar=calendar_dependency_identity())
        except (GateError, OSError, TypeError, ValueError) as exc:
            reasons.append(str(exc))
            _set_gate(gates, "configuration_identity", R5_GATE_FAIL, reason=str(exc))
            _set_gate(gates, "dependency_identity", R5_GATE_FAIL, reason=str(exc))

        try:
            r4_contract_identity = _validate_r4_contract_identity(run_commit)
            _validate_contract_r4_values(contract, r4_contract_identity)
            _set_gate(gates, "r4_acquisition_identity", R5_GATE_PASS, evidence=contract["accepted_r4_inputs"])
        except (GateError, OSError, subprocess.CalledProcessError) as exc:
            reasons.append(str(exc))
            _set_gate(gates, "r4_acquisition_identity", R5_GATE_FAIL, reason=str(exc))

        canonical_staged: Path | None = None
        try:
            canonical_path = Path(args.canonical_bars)
            canonical_manifest_path = Path(args.canonical_manifest)
            if not canonical_path.is_file() or not canonical_manifest_path.is_file():
                raise GateError("missing_canonical_artifact", "canonical bars CSV and manifest are required")
            canonical_manifest = _load_json(canonical_manifest_path)
            if str(canonical_manifest.get("feed", "")).lower() != SOURCE_FEED:
                raise GateError("canonical_source_contract", "canonical manifest feed must be sip")
            if str(canonical_manifest.get("adjustment", "")).lower() != SOURCE_ADJUSTMENT:
                raise GateError("canonical_source_contract", "canonical manifest adjustment must be split")
            canonical_manifest_identity = r4_gate._tracked_blob_identity(canonical_manifest_path, commit=run_commit)
            if canonical_manifest_identity is None:
                raise GateError("canonical_manifest_provenance", "canonical manifest must be Git tracked")
            canonical_artifact = stage_file(canonical_path, staging_dir)
            r4_gate._validate_artifact_against_manifest(
                canonical_artifact, canonical_manifest, expected_sha=None, label="canonical bars"
            )
            canonical_staged = canonical_artifact.staged_path
            canonical_identity = {
                **canonical_artifact.receipt("canonical_equity_v5_bars", "external_untracked"),
                "manifest_git_blob": canonical_manifest_identity,
            }
            if canonical_identity.get("raw_sha256") != contract.get("accepted_r4_inputs", {}).get(
                "canonical_raw_sha256"
            ):
                raise GateError("canonical_identity", "canonical bytes differ from accepted R4 identity")
            _set_gate(gates, "canonical_artifact_identity", R5_GATE_PASS, evidence=canonical_identity)
        except (GateError, OSError, subprocess.CalledProcessError) as exc:
            reasons.append(str(exc))
            _set_gate(gates, "canonical_artifact_identity", R5_GATE_FAIL, reason=str(exc))

        try:
            lifetime_staged = stage_file(Path(args.lifetimes), staging_dir)
            lifetime_tracked = r4_gate._tracked_blob_identity(Path(args.lifetimes), commit=run_commit)
            if (
                lifetime_tracked is None
                or lifetime_staged.crlf_normalized_sha256 != lifetime_tracked["git_blob_raw_sha256"]
            ):
                raise GateError("lifetime_identity", "lifetime working bytes differ from the committed blob")
            if lifetime_staged.raw_sha256 != R4_LIFETIME_RAW_SHA256:
                raise GateError("lifetime_identity", "lifetime raw hash differs from accepted identity")
            _set_gate(gates, "lifetime_identity", R5_GATE_PASS, evidence=lifetime_tracked)
        except (GateError, OSError) as exc:
            reasons.append(str(exc))
            _set_gate(gates, "lifetime_identity", R5_GATE_FAIL, reason=str(exc))

        try:
            ca_manifest = _load_json(Path(args.corporate_actions_manifest))
            ca_staged = stage_file(Path(args.corporate_actions), staging_dir)
            r4_gate._validate_artifact_against_manifest(
                ca_staged, ca_manifest, expected_sha=None, label="corporate actions"
            )
            ca_inventory = r4_gate._audit_corporate_actions(Path(args.corporate_actions), ca_manifest, ca_staged)
            _set_gate(gates, "corporate_action_identity", R5_GATE_PASS, evidence=ca_inventory)
        except (GateError, OSError) as exc:
            reasons.append(str(exc))
            _set_gate(gates, "corporate_action_identity", R5_GATE_FAIL, reason=str(exc))

        try:
            iwm_rows, iwm_audit = validate_iwm_bundle(
                Path(args.iwm_bundle), staging_dir, r4=True, require_promoted=True
            )
            if (
                iwm_audit.get("raw_bundle_sha256") != R4_RAW_BUNDLE_SHA256
                or iwm_audit.get("normalized_row_count") != 2658
                or iwm_audit.get("normalized_semantic_row_sha256") != R4_SIDECAR_SEMANTIC_SHA256
                or iwm_audit.get("source_receipt", {}).get("raw_sha256") != R4_SOURCE_RECEIPT_SHA256
            ):
                raise GateError("r4_bundle_identity", "accepted R4 bundle replay identity differs")
            receipt["iwm_raw_source"] = {
                "status": R5_GATE_PASS,
                "raw_bundle_sha256": iwm_audit["raw_bundle_sha256"],
                "normalized_row_count": iwm_audit["normalized_row_count"],
            }
            for gate_name in (
                "iwm_raw_bundle",
                "iwm_provider_contract",
                "iwm_pagination_chain",
                "iwm_normalization",
                "r4_raw_bundle_replay_identity",
            ):
                _set_gate(
                    gates, gate_name, R5_GATE_PASS, evidence={"raw_bundle_sha256": iwm_audit["raw_bundle_sha256"]}
                )
            _set_gate(gates, "request_boundary_conformance", R5_GATE_PASS, interval="[start,end)")
        except (GateError, OSError) as exc:
            reasons.append(str(exc))
            receipt["iwm_raw_source"] = {"status": R5_GATE_FAIL, "reason": str(exc)}
            for gate_name in (
                "iwm_raw_bundle",
                "iwm_provider_contract",
                "iwm_pagination_chain",
                "iwm_normalization",
                "r4_raw_bundle_replay_identity",
                "request_boundary_conformance",
            ):
                _set_gate(gates, gate_name, R5_GATE_FAIL, reason=str(exc))
            iwm_rows = ()

        if canonical_staged is not None and iwm_rows:
            try:
                _iwm_sidecar_rows, sidecar_quality = r4_gate.load_csv_reference_rows(
                    Path(args.iwm_sidecar), reference_symbols=("IWM",)
                )
                sidecar_rows = _iwm_sidecar_rows["IWM"]
                sidecar_staged = stage_file(Path(args.iwm_sidecar), staging_dir)
                if sidecar_staged.raw_sha256 != R4_SIDECAR_RAW_SHA256 or sidecar_quality["IWM"].get(
                    "rejected_row_count", 0
                ):
                    raise GateError("sidecar_identity", "R4 normalized sidecar identity or quality differs")
                if semantic_rows_digest(sidecar_rows) != R4_SIDECAR_SEMANTIC_SHA256:
                    raise GateError("sidecar_identity", "R4 normalized sidecar semantic digest differs")
                if tuple(sidecar_rows) != tuple(iwm_rows):
                    raise GateError(
                        "sidecar_identity", "R4 normalized sidecar rows differ from raw-bundle normalization"
                    )
                sidecar_identity = {
                    "path": _logical_path(Path(args.iwm_sidecar)),
                    "raw_sha256": sidecar_staged.raw_sha256,
                    "normalized_semantic_row_sha256": R4_SIDECAR_SEMANTIC_SHA256,
                    "normalized_row_count": len(sidecar_rows),
                }
                composite = _load_json(Path(args.composite_manifest))
                identity = composite.get("identity")
                if (
                    not isinstance(identity, Mapping)
                    or identity.get("semantic_digest_sha256") != R4_COMPOSITE_SEMANTIC_SHA256
                ):
                    raise GateError("composite_identity", "R4 composite semantic identity differs")
                composite_identity = dict(identity)
                _set_gate(gates, "composite_dataset_identity", R5_GATE_PASS, evidence=composite_identity)
            except (GateError, OSError) as exc:
                reasons.append(str(exc))
                sidecar_rows = []
                _set_gate(gates, "composite_dataset_identity", R5_GATE_FAIL, reason=str(exc))
        else:
            sidecar_rows = []
            _set_gate(gates, "composite_dataset_identity", R5_GATE_NOT_RUN, reason="canonical or IWM input unavailable")

        if canonical_staged is not None and sidecar_rows:
            try:
                reference_rows, quality, inventory, common, weekly = _reference_inputs(
                    canonical_staged, sidecar_rows, staging_dir
                )
                weekly_raw = weekly
                source_complete, quality_ok, common_ok, full_ok, recent_ok = _validate_references(
                    inventory, common, weekly
                )
                _set_gate(
                    gates,
                    "required_reference_session_completeness",
                    R5_GATE_PASS if source_complete else R5_GATE_FAIL,
                    evidence={"count": EXPECTED_REQUIRED_REFERENCE_SESSIONS},
                )
                _set_gate(
                    gates,
                    "reference_calendar",
                    R5_GATE_PASS if source_complete else R5_GATE_FAIL,
                    evidence=calendar_dependency_identity(),
                )
                _set_gate(
                    gates, "reference_row_quality", R5_GATE_PASS if quality_ok else R5_GATE_FAIL, evidence=inventory
                )
                _set_gate(
                    gates,
                    "reference_common_sessions",
                    R5_GATE_PASS if common_ok else R5_GATE_FAIL,
                    common_session_count=common.get("common_session_count"),
                )
                _set_gate(
                    gates,
                    "full_weekly_post_warmup_coverage",
                    R5_GATE_PASS if full_ok else R5_GATE_FAIL,
                    evidence=weekly["full"],
                )
                _set_gate(
                    gates,
                    "recent_weekly_coverage",
                    R5_GATE_PASS if recent_ok else R5_GATE_FAIL,
                    evidence=weekly["recent"],
                )
                receipt["reference_inventory"] = inventory
                receipt["reference_common_sessions"] = {
                    key: value for key, value in common.items() if key != "common_sessions"
                }
                receipt["weekly_reference_coverage"] = {
                    "full": r4_gate._compact_weekly(weekly["full"]),
                    "recent": r4_gate._compact_weekly(weekly["recent"]),
                }
            except (GateError, OSError) as exc:
                reasons.append(str(exc))
                for gate_name in (
                    "required_reference_session_completeness",
                    "reference_calendar",
                    "reference_row_quality",
                    "reference_common_sessions",
                    "full_weekly_post_warmup_coverage",
                    "recent_weekly_coverage",
                ):
                    _set_gate(gates, gate_name, R5_GATE_FAIL, reason=str(exc))
        else:
            for gate_name in (
                "required_reference_session_completeness",
                "reference_calendar",
                "reference_row_quality",
                "reference_common_sessions",
                "full_weekly_post_warmup_coverage",
                "recent_weekly_coverage",
            ):
                _set_gate(gates, gate_name, R5_GATE_NOT_RUN, reason="reference inputs unavailable")

        try:
            source, source_identity = _validate_r4_source_pit(
                Path(args.r4_pit_universe),
                Path(args.r5_pit_manifest_source),
                staging_dir,
                run_commit,
            )
            source_timing = _r4_source_timing_table(source)
            receipt["r4_source_pit_identity"] = source_identity
            _set_gate(gates, "pit_source_artifact_identity", R5_GATE_PASS, evidence=source_identity)
            if canonical_identity and sidecar_identity and composite_identity:
                projection = project_evaluation_pit(
                    source,
                    contract=contract,
                    source_identity=source_identity,
                    r4_manifest_identity=source_identity["source_compact_manifest"],
                    canonical_identity=canonical_identity,
                    sidecar_identity=sidecar_identity,
                    composite_identity=composite_identity,
                    contract_identity=contract_identity,
                )
                population_summary = dict(projection["population"])  # type: ignore[arg-type]
                population_summary.update(
                    {
                        "scanner_identity": {
                            "scanner_version": R5_SCANNER_VERSION,
                            "scanner_config_digest": R5_SCANNER_CONFIG_DIGEST,
                        },
                        "strict_prior_result": projection["strict_prior_proof"],
                        "full_timestamp_snapshot_selection": {
                            "status": R5_GATE_PASS,
                            "comparison": "effective_at <= cutoff_timestamp",
                            "evaluation_decision_cutoff_timestamp": projection["r5_projection"][
                                "evaluation_decision_cutoff_timestamp"
                            ],  # type: ignore[index]
                        },
                        "reference_exclusion": projection["r5_projection"]["reference_boundary"],  # type: ignore[index]
                    }
                )
                receipt["pit_population"] = population_summary
                projection_bytes = _write_json(Path(args.projection_out), projection)
                pit_manifest = {
                    "schema_version": "1.0.0",
                    "mission": R5_PIT_MISSION,
                    "raw_pit_path": _logical_path(Path(args.projection_out)),
                    "raw_pit_sha256": _sha256_bytes(projection_bytes),
                    "raw_pit_byte_size": len(projection_bytes),
                    "source_r4_raw_pit": source_identity,
                    "r5_contract": contract_identity,
                    "r5_projection_implementation": contract["critical_git_blobs"][
                        "scripts/research_equity_m3_data_gate_r5.py"
                    ],  # type: ignore[index]
                    "canonical_bars_raw_sha256": canonical_identity.get("raw_sha256"),
                    "normalized_iwm_sidecar_raw_sha256": sidecar_identity.get("raw_sha256"),
                    "normalized_iwm_sidecar_semantic_digest_sha256": sidecar_identity.get(
                        "normalized_semantic_row_sha256"
                    ),
                    "composite_semantic_digest_sha256": composite_identity.get("semantic_digest_sha256"),
                    "scanner_version": R5_SCANNER_VERSION,
                    "scanner_config_digest": R5_SCANNER_CONFIG_DIGEST,
                    "evaluation_boundary": projection["r5_projection"],
                    "candidate_universe_digest_sha256": projection["population"]["candidate_universe_digest_sha256"],  # type: ignore[index]
                    "candidate_decision_eligibility_digest_sha256": projection["population"][
                        "candidate_decision_eligibility_digest_sha256"
                    ],  # type: ignore[index]
                    "projection_digest_sha256": projection["r5_projection"]["projection_digest_sha256"],  # type: ignore[index]
                    "snapshot_count": projection["population"]["snapshot_count"],  # type: ignore[index]
                    "explicit_empty_snapshot_count": projection["population"]["explicit_empty_snapshot_count"],  # type: ignore[index]
                    "source_snapshot_count": projection["r5_projection"]["source_snapshot_count"],  # type: ignore[index]
                    "evaluation_snapshot_count": projection["r5_projection"]["evaluation_snapshot_count"],  # type: ignore[index]
                    "excluded_post_evaluation_snapshot_count": projection["r5_projection"][
                        "excluded_post_evaluation_snapshot_count"
                    ],  # type: ignore[index]
                    "excluded_post_evaluation_snapshot_digest_sha256": projection["r5_projection"][
                        "excluded_post_evaluation_snapshot_digest_sha256"
                    ],  # type: ignore[index]
                    "consumed_decision_count": projection["r5_projection"]["consumed_decision_count"],  # type: ignore[index]
                    "full_timestamp_ordering": True,
                    "strict_prior_proof": projection["strict_prior_proof"],
                    "candidate_rankings_present": False,
                    "no_alpha_ranking": True,
                }
                _write_json(Path(args.pit_manifest_out), pit_manifest)
                pit_manifest_artifact = stage_file(Path(args.pit_manifest_out), staging_dir)
                _set_gate(
                    gates,
                    "pit_evaluation_projection_identity",
                    R5_GATE_PASS,
                    projection_digest_sha256=projection["r5_projection"]["projection_digest_sha256"],
                )  # type: ignore[index]
                _set_gate(gates, "pit_manifest_identity", R5_GATE_PASS, raw_sha256=pit_manifest_artifact.raw_sha256)
                _set_gate(
                    gates,
                    "pit_reference_boundary",
                    R5_GATE_PASS,
                    evidence=projection["r5_projection"]["reference_boundary"],
                )  # type: ignore[index]
                _set_gate(
                    gates,
                    "pit_snapshot_cadence",
                    R5_GATE_PASS,
                    source_count=projection["r5_projection"]["source_snapshot_count"],
                    evaluation_count=projection["r5_projection"]["evaluation_snapshot_count"],
                )  # type: ignore[index]
                _set_gate(gates, "pit_terminal_boundary", R5_GATE_PASS, evidence=projection["r5_projection"])  # type: ignore[index]
                _set_gate(gates, "strict_prior_population", R5_GATE_PASS, evidence=projection["strict_prior_proof"])
                _set_gate(
                    gates,
                    "full_timestamp_snapshot_selection",
                    R5_GATE_PASS,
                    evidence={"comparison": "effective_at <= cutoff_timestamp"},
                )
                _set_gate(gates, "deterministic_numerical_population", R5_GATE_PASS, evidence=projection["population"])
                source_identity["timing_rows"] = source_timing
            else:
                raise GateError("pit_input_missing", "R5 projection requires all authenticated input identities")
        except (GateError, OSError, subprocess.CalledProcessError) as exc:
            reasons.append(str(exc))
            for gate_name in (
                "pit_evaluation_projection_identity",
                "pit_manifest_identity",
                "pit_reference_boundary",
                "pit_snapshot_cadence",
                "pit_terminal_boundary",
                "strict_prior_population",
                "full_timestamp_snapshot_selection",
                "deterministic_numerical_population",
            ):
                _set_gate(gates, gate_name, R5_GATE_FAIL, reason=str(exc))

        if projection is not None and reference_rows:
            try:
                sessions = expected_exchange_sessions(REQUIRED_FIRST_REFERENCE_SESSION, REQUIRED_LAST_REFERENCE_SESSION)
                candidate_symbols = tuple(
                    sorted({symbol for snapshot in projection["snapshots"] for symbol in snapshot["eligible_symbols"]})
                )  # type: ignore[index]
                close_arrays, timestamp_arrays, close_load = _load_close_matrix(
                    canonical_staged,  # type: ignore[arg-type]
                    symbols=candidate_symbols,
                    sessions=sessions,
                    sidecar_rows=reference_rows["IWM"],
                )
                if close_load["invalid_selected_row_count"] or close_load["duplicate_selected_row_count"]:
                    raise GateError(
                        "canonical_candidate_quality", "selected candidate canonical rows are invalid or duplicated"
                    )
                missing_reference_cells = {
                    symbol: int(np.count_nonzero(timestamp_arrays[symbol] == 0)) for symbol in REFERENCE_SYMBOLS
                }
                if any(missing_reference_cells.values()):
                    raise GateError(
                        "reference_timestamp_alignment",
                        f"reference timestamp cells are missing: {missing_reference_cells}",
                    )
                full_decisions = tuple(
                    date.fromisoformat(str(item["decision_session"]))
                    for item in weekly_raw["full"].get("decisions", ())
                    if isinstance(item, Mapping)
                )
                recent_decisions = tuple(
                    date.fromisoformat(str(item["decision_session"]))
                    for item in weekly_raw["recent"].get("decisions", ())
                    if isinstance(item, Mapping)
                )
                minimum_history_bars = frozen_m3_minimum_history_bars(contract["frozen_parameters"])  # type: ignore[arg-type]
                if minimum_history_bars != R5_MINIMUM_HISTORY_BARS:
                    raise GateError("strategy_history_contract", "fresh frozen M3 strategy minimum history drifted")
                matrices = audit_matrix_population(
                    projection=projection,
                    full_decisions=full_decisions,
                    recent_decisions=recent_decisions,
                    close_arrays=close_arrays,
                    timestamp_arrays=timestamp_arrays,
                    sessions=sessions,
                    minimum_history_bars=minimum_history_bars,
                )
                full_ok = (
                    matrices["first_full_candidate_feasible_decision"] is not None
                    and matrices["full_post_feasibility_failure_count"] == 0
                )
                recent_ok = (
                    matrices["recent_candidate_depth_failure_count"] == 0
                    and matrices["minimum_recent_eligible_depth"] >= R5_TOP_N
                )
                numerical_ok = (
                    matrices["total_eligible_matrix_count"] > 0
                    and matrices["numerical_fail_count"] == 0
                    and matrices["numerical_pass_count"] == matrices["total_eligible_matrix_count"]
                )
                _set_gate(
                    gates,
                    "m3_history_eligibility_parity",
                    R5_GATE_PASS,
                    predicate="scanner membership + minimum history + exact timestamp intersection",
                )
                _set_gate(
                    gates,
                    "full_candidate_depth_feasibility",
                    R5_GATE_PASS if full_ok else R5_GATE_FAIL,
                    evidence={
                        "first_feasible": matrices["first_full_candidate_feasible_decision"],
                        "post_failures": matrices["full_post_feasibility_failure_count"],
                    },
                )
                _set_gate(
                    gates,
                    "recent_candidate_depth_feasibility",
                    R5_GATE_PASS if recent_ok else R5_GATE_FAIL,
                    evidence={
                        "minimum_depth": matrices["minimum_recent_eligible_depth"],
                        "failures": matrices["recent_candidate_depth_failure_count"],
                    },
                )
                _set_gate(
                    gates,
                    "design_matrix_feasibility",
                    R5_GATE_PASS if numerical_ok else R5_GATE_FAIL,
                    evidence={
                        key: value
                        for key, value in matrices.items()
                        if key not in {"all_decisions", "weekly_eligibility_by_session"}
                    },
                )
                receipt["numerical_feasibility"] = {
                    key: value
                    for key, value in matrices.items()
                    if key not in {"all_decisions", "weekly_eligibility_by_session"}
                }
                receipt["history_eligibility"] = {
                    key: value
                    for key, value in matrices.items()
                    if key
                    in {
                        "scanner_candidate_pair_count",
                        "m3_not_warm_pair_count",
                        "total_eligible_matrix_count",
                        "first_full_candidate_feasible_decision",
                        "minimum_recent_eligible_depth",
                    }
                }
                receipt["weekly_m3_history_eligibility"] = matrices["weekly_eligibility_by_session"]
            except (GateError, OSError, ValueError) as exc:
                reasons.append(str(exc))
                for gate_name in (
                    "m3_history_eligibility_parity",
                    "full_candidate_depth_feasibility",
                    "recent_candidate_depth_feasibility",
                    "design_matrix_feasibility",
                ):
                    _set_gate(gates, gate_name, R5_GATE_FAIL, reason=str(exc))
        else:
            for gate_name in (
                "m3_history_eligibility_parity",
                "full_candidate_depth_feasibility",
                "recent_candidate_depth_feasibility",
                "design_matrix_feasibility",
            ):
                _set_gate(gates, gate_name, R5_GATE_NOT_RUN, reason="projection or reference inputs unavailable")

        try:
            overlay = build_reference_overlay(_load_yaml(CONFIG_PATH))
            role = overlay.to_dict()
            role["pit_reference_evidence"] = validate_pit_reference_boundary(
                deterministic_candidate_population(projection["snapshots"]) if projection is not None else {}
            )
            _set_gate(gates, "reference_role_contract", R5_GATE_PASS, evidence=role)
        except (GateError, OSError) as exc:
            reasons.append(str(exc))
            _set_gate(gates, "reference_role_contract", R5_GATE_FAIL, reason=str(exc))

    worktree_after = _git("status", "--short", "--untracked-files=all").splitlines()
    worktree["after"] = worktree_after
    worktree["clean_after_external_outputs"] = not worktree_after
    receipt["worktree_cleanliness"] = worktree
    receipt["pit_timing_table"] = (
        _timing_table(
            projection=projection,
            reference_rows=reference_rows,
            sessions=expected_exchange_sessions(REQUIRED_FIRST_REFERENCE_SESSION, REQUIRED_LAST_REFERENCE_SESSION),
        )
        if projection is not None and reference_rows
        else []
    )
    receipt["terminal_snapshot_decision"] = {
        "interpretation": "evaluation projection excludes source snapshots effective after the exact 2026-07-30 decision cutoff",
        "terminal_observation_boundary_required": False,
        "source_snapshot_count": projection.get("r5_projection", {}).get("source_snapshot_count")
        if projection
        else None,
        "evaluation_snapshot_count": projection.get("r5_projection", {}).get("evaluation_snapshot_count")
        if projection
        else None,
        "excluded_post_evaluation_snapshot_count": projection.get("r5_projection", {}).get(
            "excluded_post_evaluation_snapshot_count"
        )
        if projection
        else None,
        "excluded_post_evaluation_snapshot_digest_sha256": projection.get("r5_projection", {}).get(
            "excluded_post_evaluation_snapshot_digest_sha256"
        )
        if projection
        else None,
        "projection_digest_sha256": projection.get("r5_projection", {}).get("projection_digest_sha256")
        if projection
        else None,
    }
    receipt["commits"] = {
        "d6_base": R5_BASE_COMMIT,
        "implementation_parent": run_parent,
        "implementation": contract.get("implementation_commit"),
        "manifest": run_commit,
        "recovery_lineage": contract.get("recovery_lineage"),
        "immutable_provenance": contract.get("immutable_provenance", {}),
    }
    receipt["manifest_only_proof"] = {
        "manifest_path": R5_MANIFEST_PATH,
        "manifest_commit": run_commit,
        "parent_commit": run_parent,
        "changed_paths": provenance.get("changed_paths", []),
        "exactly_manifest_only": provenance.get("changed_paths") == [R5_MANIFEST_PATH],
        "manifest_git_blob_id": provenance.get("manifest_git_blob_id"),
        "manifest_raw_sha256": contract_identity.get("raw_sha256"),
    }
    receipt["r4_immutable_inputs"] = {
        "r4_manifest": r4_contract_identity,
        "raw_bundle": {
            "path": R4_BUNDLE_PATH,
            "raw_bundle_sha256": iwm_audit.get("raw_bundle_sha256"),
            "aggregate_page_hash_digest": R4_PAGE_DIGEST,
            "page_chain_digest_sha256": R4_PAGE_CHAIN_DIGEST,
        },
        "source_receipt": {
            "path": R4_SOURCE_RECEIPT_PATH,
            "raw_sha256": iwm_audit.get("source_receipt", {}).get("raw_sha256")
            if isinstance(iwm_audit.get("source_receipt"), Mapping)
            else None,
        },
        "normalized_sidecar": sidecar_identity,
        "composite": composite_identity,
        "raw_pit": {
            "path": source_identity.get("path") if isinstance(source_identity, Mapping) else None,
            "raw_sha256": source_identity.get("raw_sha256") if isinstance(source_identity, Mapping) else None,
            "byte_size": source_identity.get("byte_size") if isinstance(source_identity, Mapping) else None,
        },
    }
    receipt["boundary"] = {
        "corrective_infrastructure_verdict": "PASS"
        if all(
            gates[name].get("status") == R5_GATE_PASS
            for name in (
                "commit_topology",
                "implementation_blob_identity",
                "manifest_only_commit",
                "clean_worktree_at_start",
            )
        )
        else "BLOCKED",
        "m3_candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
        "economic_mission_ready": False,
        "next_required_gate": "M3_CANDIDATE_OCP_GATE",
    }
    receipt["corrective_infrastructure_verdict"] = receipt["boundary"]["corrective_infrastructure_verdict"]
    for name in R5_MANDATORY_GATES:
        if gates[name].get("status") == R5_GATE_UNKNOWN:
            _set_gate(gates, name, R5_GATE_NOT_RUN, reason="phase was not executed")
    receipt["blocked_reasons"] = sorted(set(reasons))
    receipt["mandatory_gate_results"] = gates
    receipt["data_gate_verdict"] = evaluate_mandatory_gates(gates)
    receipt["forbidden_economic_fields"] = list(forbidden_receipt_keys(receipt))
    if receipt["forbidden_economic_fields"]:
        _set_gate(gates, "receipt_forbidden_fields", R5_GATE_FAIL, reason="forbidden economic field present")
        receipt["blocked_reasons"] = sorted(set([*reasons, "receipt contains forbidden economic fields"]))
    else:
        _set_gate(gates, "receipt_forbidden_fields", R5_GATE_PASS, fields=[])
    receipt["mandatory_gate_results"] = gates
    receipt["data_gate_verdict"] = evaluate_mandatory_gates(gates)
    receipt["self_digest_sha256"] = canonical_digest(receipt)
    report = _render_report(receipt)
    _write_json(Path(args.receipt_out), receipt)
    Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
    if Path(args.report_out).exists() and Path(args.report_out).read_text(encoding="utf-8") != report:
        raise GateError("output_conflict", "R5 report output already exists with different bytes")
    Path(args.report_out).write_text(report, encoding="utf-8")
    return receipt, 0 if receipt["data_gate_verdict"] == "M3_DATA_GATE_PASS" else 20


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--canonical-bars", type=Path, default=CANONICAL_PATH)
    parser.add_argument("--canonical-manifest", type=Path, default=CANONICAL_MANIFEST_PATH)
    parser.add_argument("--lifetimes", type=Path, default=LIFETIMES_PATH)
    parser.add_argument("--corporate-actions", type=Path, default=CORPORATE_ACTIONS_PATH)
    parser.add_argument("--corporate-actions-manifest", type=Path, default=CORPORATE_ACTIONS_MANIFEST_PATH)
    parser.add_argument("--iwm-bundle", type=Path, default=_REPO / R4_BUNDLE_PATH)
    parser.add_argument("--iwm-sidecar", type=Path, default=_REPO / R4_SIDECAR_PATH)
    parser.add_argument("--composite-manifest", type=Path, default=_REPO / R4_COMPOSITE_PATH)
    parser.add_argument("--r4-pit-universe", type=Path, default=_REPO / R4_PIT_PATH)
    parser.add_argument("--r5-pit-manifest-source", type=Path, default=_REPO / R4_PIT_MANIFEST_PATH)
    parser.add_argument("--projection-out", type=Path, default=R5_PROJECTION_DEFAULT)
    parser.add_argument("--pit-manifest-out", type=Path, default=R5_PIT_MANIFEST_DEFAULT)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt, status = run_gate(args)
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        print(f"M3_DATA_GATE_BLOCKED: {exc}", file=sys.stderr)
        return 21
    print(json.dumps(_safe(receipt), indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
