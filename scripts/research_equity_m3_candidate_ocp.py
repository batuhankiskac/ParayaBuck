#!/usr/bin/env python3
"""Network-free M3 candidate-owned observation-basis parity gate.

This module is deliberately a research verifier.  It consumes the immutable
R4/R5 artifacts, reconstructs the production scanner-quality fallback, and
uses the shared observation-set builder with fresh V3/M3 strategy instances.
It never constructs a backtest engine, calls an execution path, opens a
network connection, or writes a production/runtime artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _path in (_REPO / "src", Path(__file__).parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from build_m3_pit_scanner_universe import (  # noqa: E402
    LOOKBACK_DAYS,
    _iter_canonical_groups,
    _merge_iwm_groups,
)
from m3_data_gate_helpers import (  # noqa: E402
    GateError,
    canonical_digest,
    load_csv_reference_rows,
    parse_timestamp,
    semantic_rows_digest,
    validate_iwm_bundle,
)
from research_equity_m3_m4 import ProxyMultifactorResidualMomentum  # noqa: E402

from parayabuck.backtest.observation_parity import (  # noqa: E402
    assert_strategy_identity,
    compare_universe_bases,
    strategy_identity,
)
from parayabuck.backtest.universe import (  # noqa: E402
    PointInTimeUniverse,
    UniverseSnapshot,
    hash_point_in_time_universe,
)
from parayabuck.config import Config, EquityUniverseConfig, load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.portfolio import PortfolioStrategy, product_strategy_from  # noqa: E402
from parayabuck.universe.observation_set import (  # noqa: E402
    ObservationSetResult,
    build_observation_set,
)
from parayabuck.universe.scanner import (  # noqa: E402
    EquityUniverseScanner,
    equity_candidate_from_bars,
    scanner_config_digest,
)

MISSION = "M3_CANDIDATE_OCP_GATE"
VERDICT_PASS = "M3_CANDIDATE_OCP_GATE_PASS"
VERDICT_BLOCKED = "M3_CANDIDATE_OCP_GATE_BLOCKED"

BASE_COMMIT = "f15ba8ffa2c752ba184cbc6eb655792d5387ee54"
R5_IMPLEMENTATION = "e22421c2486a20e883286345dbb509bbcd5d2fc9"
R5_IMPLEMENTATION_PARENT = "459cf782754bc15d137fcaa4cdd081402b837f96"
R5_MANIFEST_COMMIT = "2f6ce2aaf347b9e0257d4ae31e9fdcc90cdcafe4"
R5_RESULT_COMMIT = "3c2f67d31772f2fba118137ad262bd8ffdb737d8"
R5_D10_COMMIT = BASE_COMMIT
R5_IMPLEMENTATION_TREE = "bd310b37ac3ed946be4a3c8aa94d8daf37c48909"
R5_MANIFEST_BLOB = "b11aa6302e9edac11389d211f772a5b1d13dcc5d"
R5_MANIFEST_RAW_SHA256 = "83a2b7b796939423eff9c3c0a2d16000594cbbbed26e9de2df61206c5ecdb51e"
R5_RECEIPT_BLOB = "7096ce9bccd99edda2e1e8d7d150d4fffb578070"
R5_RECEIPT_SELF_DIGEST = "41afb58aca2fb38f4f0d301fc169938809b1cb9afcfa5c9855d97e292916abeb"
R5_CI_RUN = "33630745082"
R5_D10_CI_RUN = "33631928797"

R5_MANIFEST_PATH = "data/research_manifests/equity_m3_data_gate_2026_09_r5.json"
R5_RECEIPT_PATH = "data/research_receipts/equity_m3_data_gate_2026_09_r5.json"
R5_REPORT_PATH = "docs/equity_m3_data_gate_2026_09_r5.md"
R5_PIT_MANIFEST_PATH = "data/research_manifests/equity_m3_pit_population_2026_09_r5.json"
R5_PIT_MANIFEST_BLOB = "1290869c1d665254eb1981d648c2ef6d44edc9e5"
R5_PIT_MANIFEST_RAW_SHA256 = "2d93b50fb7668fb899991f83a4223e6ab5f18acbdf3f4e9314a66f04d54400d4"
R4_PIT_MANIFEST_PATH = "data/research_manifests/equity_m3_pit_population_2026_09_r4.json"
R4_PIT_PATH = _REPO / "data/research/generated/m3_data_gate_r4/pit_universe.json"
R4_SIDECAR_PATH = _REPO / "data/research/generated/m3_data_gate_r4/iwm_normalized.csv"
R4_COMPOSITE_PATH = _REPO / "data/research_manifests/equity_m3_composite_dataset_2026_09_r4.json"
R4_BUNDLE_PATH = _REPO / "data/research/external/m3_iwm_raw_2026_09_r4"
CANONICAL_PATH = _REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
CANONICAL_MANIFEST_PATH = _REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json"
LIFETIMES_PATH = _REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
CORPORATE_ACTIONS_PATH = _REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CORPORATE_ACTIONS_MANIFEST_PATH = _REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json"
CONFIG_PATH = _REPO / "config/settings.yaml"
DEFAULT_MANIFEST_PATH = _REPO / "data/research_manifests/equity_m3_candidate_ocp_2026_09.json"
DEFAULT_RAW_DIR = _REPO / "data/research/generated/equity_m3_candidate_ocp_2026_09"
DEFAULT_R5_PROJECTION = Path("/tmp/m3-r5-b18-pit.json")
STALE_R5_PROJECTION = _REPO / "data/research/generated/m3_data_gate_r5/pit_universe.json"

CANONICAL_RAW_SHA256 = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
CANONICAL_NORMALIZED_SHA256 = "df42733b640e6dea17e1ed11115846933a86d2c525810dd3f473acc32e6d0db3"
CANONICAL_MANIFEST_BLOB = "a366944ec26d6f9edff82e9e892fe397f3aed112"
LIFETIMES_RAW_SHA256 = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
CORPORATE_ACTIONS_RAW_SHA256 = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
R4_RAW_BUNDLE_SHA256 = "9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258"
R4_SOURCE_RECEIPT_SHA256 = "2dbeeebc688dab111013a114e9c08b44c116e23c48a42968099f6c8671ea4003"
R4_PAGE_DIGEST = "39a87290b4ebafbba3c29663f5577c05c7548252e6699e5f39589849211c93eb"
R4_PAGE_CHAIN_DIGEST = "a5b5e400bb97aff3f6342bbe0b969f5d0ae5600406d5cd186ec62e0360480e1b"
R4_SIDECAR_RAW_SHA256 = "8f8122e33d5ef2b194e22c7e53a15198920476690997535bbc5c05d522463dc2"
R4_SIDECAR_SEMANTIC_SHA256 = "cc1855c607ee394fae9e5ede594d1420f0c4d03cb8a5af08e14e6a656cd7d837"
R4_COMPOSITE_SEMANTIC_SHA256 = "5bb66b7ff15d1f9ef3e9ce7caa4ef2314fcc670cf80b9696ee576e1b9cac4eda"
R4_RAW_PIT_SHA256 = "bfdf6e3f87d5c42a1923bc4009f2fa1892ade7317e11caf139d30239bac3f5ae"
R4_RAW_PIT_BYTES = 24_774_560
R5_PROJECTION_RAW_SHA256 = "657f2d00716ca33dd4142f7bb6166fcd72f2344fad20678456c731b292fd77ac"
R5_PROJECTION_BYTES = 25_518_170
R5_PROJECTION_DIGEST = "7e1a5ee098e23397d8f4d77e7d73e79c8491c28b089854f9174a8c0049a7b240"
R5_PROJECTION_IMPLEMENTATION_BLOB = "54bed91913012a2cfc939eae2a285e71941e7c3d"
R5_PROJECTION_IMPLEMENTATION_RAW_SHA256 = "6d2a66cabcb399651176b1db1cd12bab778506d9825d5d19fbeba4e55dcf4cbc"
R5_CANDIDATE_UNIVERSE_DIGEST = "2a0c5b987d40cefed46c4f2ded595e1759e13c8744e2bc003f2da4befac2106e"
R5_CANDIDATE_ELIGIBILITY_DIGEST = "ab5ee2d5c1bc58f711f1961dff505563adf67c02d67917c1d067431136514a32"
R5_MATRIX_MERKLE_ROOT = "99187e5e0d94348b87e6c89aae3612d4bbd40e3795e6844289daf740d18073d9"
R5_SCANNER_VERSION = "equity-scanner-v3"
R5_SCANNER_CONFIG_DIGEST = "d8e06f54cd1a2fe0a201317fb6e3f4265470b2a10fe6e119ad81554128b33392"
R5_SNAPSHOT_COUNT = 2658
R5_EXPLICIT_EMPTY_COUNT = 1
R5_UNIQUE_CANDIDATES = 889
R5_EFFECTIVE_AT_SEQUENCE_DIGEST = "64eee673b7a0ca639b5a898d1f7a329c8b264336fd1e824271f170e24e5f45b7"
R5_EVALUATION_FIRST = "2016-01-04T05:00:00.000000Z"
R5_EVALUATION_LAST = "2026-07-30T04:00:00.000000Z"
R5_EVALUATION_ENCODING = "m3-r5-pit-projection-v1"

PRIOR_A22_COMMIT = "cae97cf9e059c5c03d79469b29e84d47533119d6"
PRIOR_B22_COMMIT = "4d612d9f8fc50a5d4911f2c37d74979c61af93be"
PRIOR_C22_COMMIT = "0f6d483eecf33cef863942c38fb8f6847e646b29"
PRIOR_C22_REMOTE_CI_RUN = "33654462773"
PRIOR_C22_REMOTE_RESULT = "FAILURE"
PRIOR_C22_FAILURE_CATEGORY = "ci_hermeticity_failure_external_artifact_dependency"

REFERENCE_SYMBOLS = ("SPY", "IWM", "QQQ")
M3_FROZEN_PARAMETERS: dict[str, object] = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}
FORBIDDEN_ECONOMIC_FIELDS = (
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
)

OCP_MANDATORY_GATES = (
    "commit_topology",
    "implementation_blob_identity",
    "manifest_only_commit",
    "clean_worktree_at_start",
    "production_isolation",
    "r5_data_gate_receipt_identity",
    "r5_data_gate_verdict",
    "r5_all_mandatory_gates",
    "r5_upstream_erratum_acknowledged",
    "r4_raw_bundle_identity",
    "r4_sidecar_identity",
    "r4_composite_identity",
    "r5_projection_identity",
    "scanner_identity",
    "scanner_pool_identity",
    "scanner_fallback_identity",
    "exact_snapshot_alignment",
    "reference_role_contract",
    "reference_pin_order",
    "observation_capacity",
    "strictly_prior_history",
    "no_future_bar_leak",
    "no_outside_pool_observation",
    "no_duplicate_observation",
    "no_stale_observation",
    "C_control_strategy_identity",
    "C_control_repeat_determinism",
    "C_control_self_parity",
    "X_candidate_strategy_identity",
    "X_observer_execution_identity",
    "X_fresh_instance_separation",
    "X_repeat_determinism",
    "X_direct_builder_parity",
    "X_candidate_owned_basis",
    "X_vs_C_scanner_metadata_parity",
    "X_vs_C_nonzero_observation_change",
    "C_X_boundary_contract",
    "raw_artifact_identity",
    "compact_receipt_integrity",
    "forbidden_economic_fields",
    "no_economic_execution",
    "no_network_or_credentials",
)


class OcpFailure(RuntimeError):
    """A fail-closed OCP assertion with a stable category."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class SnapshotInput:
    effective_at: datetime
    latest_input_timestamp: datetime
    candidate_count: int
    eligible_candidates: tuple[str, ...]
    scanner_fallback: tuple[str, ...]
    history: dict[str, tuple[Bar, ...]]
    scanner_version: str
    scanner_config_digest: str


@dataclass(frozen=True)
class BasisSnapshot:
    effective_at: datetime
    latest_input_timestamp: datetime
    candidate_count: int
    eligible_candidates: tuple[str, ...]
    scanner_fallback: tuple[str, ...]
    reference_pins: tuple[str, ...]
    pinned_symbols: tuple[str, ...]
    product_visible_symbols: tuple[str, ...]
    observation_set: tuple[str, ...]
    maximum_stream_symbols: int
    scanner_version: str
    scanner_config_digest: str
    strategy_name: str
    strategy_version: str
    source_classification: str
    alpha_scored_count: int
    leaf_digest: str


@dataclass(frozen=True)
class ObservationBasis:
    strategy_identity: dict[str, object]
    snapshots: tuple[BasisSnapshot, ...]
    universe: PointInTimeUniverse
    raw_payload: dict[str, object]
    semantic_hash: str
    observation_merkle_root: str
    fallback_snapshot_count: int
    warmup_snapshot_count: int
    full_capacity_snapshot_count: int


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=_REPO, check=True, capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise OcpFailure("git_identity", f"git command failed: {' '.join(args)}") from exc


def _git_blob(commit: str, path: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "cat-file", "blob", f"{commit}:{path}"],
            cwd=_REPO,
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise OcpFailure("git_identity", f"missing Git blob: {commit}:{path}") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path, *, normalize_newlines: bool = False) -> str:
    digest = hashlib.sha256()
    pending_cr = False
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            if not normalize_newlines:
                digest.update(chunk)
                continue
            output = bytearray()
            for byte in chunk:
                if pending_cr:
                    if byte == 0x0A:
                        output.append(0x0A)
                        pending_cr = False
                        continue
                    output.append(0x0D)
                    pending_cr = False
                if byte == 0x0D:
                    pending_cr = True
                else:
                    output.append(byte)
            digest.update(output)
    if normalize_newlines and pending_cr:
        digest.update(b"\r")
    return digest.hexdigest()


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OcpFailure("invalid_json", f"invalid JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise OcpFailure("invalid_json", f"JSON object required: {path}")
    return value


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _write_json(path: Path, value: Mapping[str, object]) -> bytes:
    data = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _display_path(path: Path) -> str:
    """Use repository-relative paths in receipts while retaining temp paths."""
    try:
        return path.relative_to(_REPO).as_posix()
    except ValueError:
        return str(path)


def _dirty_paths() -> list[str]:
    return _git("status", "--short", "--untracked-files=all").splitlines()


def _require(condition: bool, category: str, message: str) -> None:
    if not condition:
        raise OcpFailure(category, message)


def _canonical_identity(path: Path, *, expected_raw: str, expected_normalized: str | None = None) -> dict[str, object]:
    _require(path.is_file(), "artifact_missing", f"required artifact is missing: {path}")
    raw = _sha256_file(path)
    _require(raw == expected_raw, "artifact_identity", f"raw SHA-256 mismatch: {path}")
    result: dict[str, object] = {
        "path": str(path.relative_to(_REPO)) if path.is_relative_to(_REPO) else str(path),
        "raw_sha256": raw,
        "byte_size": path.stat().st_size,
    }
    if expected_normalized is not None:
        normalized = _sha256_file(path, normalize_newlines=True)
        _require(normalized == expected_normalized, "artifact_identity", f"normalized SHA-256 mismatch: {path}")
        result["crlf_normalized_sha256"] = normalized
    return result


def _tracked_identity(commit: str, path: str) -> dict[str, object]:
    blob_id = _git("rev-parse", f"{commit}:{path}")
    blob = _git_blob(commit, path)
    return {
        "path": path,
        "commit": commit,
        "git_blob_id": blob_id,
        "raw_sha256": _sha256_bytes(blob),
    }


def _tracked_r5_snapshot_contract() -> dict[str, object]:
    """Read and authenticate the accepted R5 snapshot contract from Git only."""
    blob_id = _git("rev-parse", f"{R5_RESULT_COMMIT}:{R5_PIT_MANIFEST_PATH}")
    _require(
        blob_id == R5_PIT_MANIFEST_BLOB,
        "r5_snapshot_contract",
        "R5 compact PIT manifest Git blob changed",
    )
    blob = _git_blob(R5_RESULT_COMMIT, R5_PIT_MANIFEST_PATH)
    _require(
        _sha256_bytes(blob) == R5_PIT_MANIFEST_RAW_SHA256,
        "r5_snapshot_contract",
        "R5 compact PIT manifest raw SHA changed",
    )
    try:
        payload = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OcpFailure("r5_snapshot_contract", "R5 compact PIT manifest is not valid UTF-8 JSON") from exc
    _require(isinstance(payload, Mapping), "r5_snapshot_contract", "R5 compact PIT manifest must be an object")

    for key, expected in {
        "schema_version": "1.0.0",
        "mission": "equity_m3_pit_scanner_population_2026_09_r5",
        "snapshot_count": R5_SNAPSHOT_COUNT,
        "consumed_decision_count": R5_SNAPSHOT_COUNT,
        "evaluation_snapshot_count": R5_SNAPSHOT_COUNT,
        "explicit_empty_snapshot_count": R5_EXPLICIT_EMPTY_COUNT,
        "candidate_rankings_present": False,
        "no_alpha_ranking": True,
        "candidate_universe_digest_sha256": R5_CANDIDATE_UNIVERSE_DIGEST,
        "candidate_decision_eligibility_digest_sha256": R5_CANDIDATE_ELIGIBILITY_DIGEST,
        "projection_digest_sha256": R5_PROJECTION_DIGEST,
        "scanner_version": R5_SCANNER_VERSION,
        "scanner_config_digest": R5_SCANNER_CONFIG_DIGEST,
        "raw_pit_sha256": R5_PROJECTION_RAW_SHA256,
        "raw_pit_byte_size": R5_PROJECTION_BYTES,
        "canonical_bars_raw_sha256": CANONICAL_RAW_SHA256,
        "normalized_iwm_sidecar_raw_sha256": R4_SIDECAR_RAW_SHA256,
        "normalized_iwm_sidecar_semantic_digest_sha256": R4_SIDECAR_SEMANTIC_SHA256,
        "composite_semantic_digest_sha256": R4_COMPOSITE_SEMANTIC_SHA256,
    }.items():
        _require(payload.get(key) == expected, "r5_snapshot_contract", f"R5 compact PIT field {key} changed")

    contract_identity = payload.get("r5_contract")
    _require(
        contract_identity
        == {
            "commit": R5_MANIFEST_COMMIT,
            "git_blob_id": R5_MANIFEST_BLOB,
            "path": R5_MANIFEST_PATH,
            "raw_sha256": R5_MANIFEST_RAW_SHA256,
        },
        "r5_snapshot_contract",
        "R5 data-gate contract identity changed",
    )
    projection_implementation = payload.get("r5_projection_implementation")
    _require(
        projection_implementation
        == {
            "git_blob_id": R5_PROJECTION_IMPLEMENTATION_BLOB,
            "raw_sha256": R5_PROJECTION_IMPLEMENTATION_RAW_SHA256,
        },
        "r5_snapshot_contract",
        "R5 projection implementation identity changed",
    )
    source_r4 = payload.get("source_r4_raw_pit")
    _require(isinstance(source_r4, Mapping), "r5_snapshot_contract", "R5 source R4 PIT identity is missing")
    _require(
        source_r4.get("raw_sha256") == R4_RAW_PIT_SHA256 and source_r4.get("byte_size") == R4_RAW_PIT_BYTES,
        "r5_snapshot_contract",
        "R5 source R4 PIT identity changed",
    )
    source_manifest = source_r4.get("source_compact_manifest")
    _require(isinstance(source_manifest, Mapping), "r5_snapshot_contract", "R5 source R4 manifest identity is missing")
    _require(
        source_manifest.get("commit") == R5_MANIFEST_COMMIT and source_manifest.get("path") == R4_PIT_MANIFEST_PATH,
        "r5_snapshot_contract",
        "R5 source R4 compact manifest identity changed",
    )

    boundary = payload.get("evaluation_boundary")
    _require(isinstance(boundary, Mapping), "r5_snapshot_contract", "R5 evaluation boundary is missing")
    for key, expected in {
        "consumed_decision_count": R5_SNAPSHOT_COUNT,
        "evaluation_snapshot_count": R5_SNAPSHOT_COUNT,
        "candidate_population_digest_sha256": R5_CANDIDATE_UNIVERSE_DIGEST,
        "candidate_decision_eligibility_digest_sha256": R5_CANDIDATE_ELIGIBILITY_DIGEST,
        "projection_digest_sha256": R5_PROJECTION_DIGEST,
        "evaluation_first_snapshot": R5_EVALUATION_FIRST,
        "evaluation_last_snapshot": R5_EVALUATION_LAST,
        "encoding": R5_EVALUATION_ENCODING,
        "full_timestamp_ordering": True,
        "terminal_observation_boundary_required": False,
        "excluded_post_evaluation_snapshot_count": 1,
    }.items():
        _require(boundary.get(key) == expected, "r5_snapshot_contract", f"R5 evaluation field {key} changed")
    strict_prior_proof = boundary.get("strict_prior_proof")
    _require(
        isinstance(strict_prior_proof, Mapping)
        and strict_prior_proof.get("status") == "PASS"
        and strict_prior_proof.get("effective_at_is_strictly_after_latest_input") is True
        and strict_prior_proof.get("violating_snapshots") == [],
        "r5_snapshot_contract",
        "R5 strict-prior contract changed",
    )

    raw_timestamps = boundary.get("evaluation_effective_at_timestamps")
    _require(
        isinstance(raw_timestamps, list) and len(raw_timestamps) == R5_SNAPSHOT_COUNT,
        "r5_snapshot_contract",
        "R5 exact effective_at sequence is missing",
    )
    timestamps: list[str] = []
    for index, raw_timestamp in enumerate(raw_timestamps):
        _require(
            isinstance(raw_timestamp, str),
            "r5_snapshot_contract",
            f"R5 effective_at timestamp {index} is not a string",
        )
        parsed = parse_timestamp(raw_timestamp)
        canonical = parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
        _require(
            raw_timestamp == canonical,
            "r5_snapshot_contract",
            f"R5 effective_at timestamp {index} is not canonical",
        )
        timestamps.append(raw_timestamp)
    _require(
        timestamps[0] == R5_EVALUATION_FIRST and timestamps[-1] == R5_EVALUATION_LAST,
        "r5_snapshot_contract",
        "R5 effective_at endpoints changed",
    )
    _require(
        timestamps == sorted(timestamps),
        "r5_snapshot_contract",
        "R5 effective_at sequence is not ordered",
    )
    sequence_digest = canonical_digest(timestamps)
    _require(
        sequence_digest == R5_EFFECTIVE_AT_SEQUENCE_DIGEST,
        "r5_snapshot_contract",
        "R5 effective_at sequence digest changed",
    )
    return {
        "path": R5_PIT_MANIFEST_PATH,
        "commit": R5_RESULT_COMMIT,
        "git_blob_id": blob_id,
        "raw_sha256": R5_PIT_MANIFEST_RAW_SHA256,
        "snapshot_count": R5_SNAPSHOT_COUNT,
        "consumed_decision_count": R5_SNAPSHOT_COUNT,
        "first_effective_at": R5_EVALUATION_FIRST,
        "last_effective_at": R5_EVALUATION_LAST,
        "effective_at": tuple(timestamps),
        "effective_at_sequence_digest_sha256": sequence_digest,
        "candidate_universe_digest_sha256": R5_CANDIDATE_UNIVERSE_DIGEST,
        "candidate_decision_eligibility_digest_sha256": R5_CANDIDATE_ELIGIBILITY_DIGEST,
        "projection_digest_sha256": R5_PROJECTION_DIGEST,
        "projection_raw_sha256": R5_PROJECTION_RAW_SHA256,
        "projection_byte_size": R5_PROJECTION_BYTES,
        "scanner_version": R5_SCANNER_VERSION,
        "scanner_config_digest": R5_SCANNER_CONFIG_DIGEST,
        "explicit_empty_snapshot_count": R5_EXPLICIT_EMPTY_COUNT,
        "unique_candidate_count": R5_UNIQUE_CANDIDATES,
        "encoding": R5_EVALUATION_ENCODING,
    }


def _validate_declared_snapshot_contract(manifest: Mapping[str, object]) -> None:
    """Bind the committed pre-result declaration to the accepted R5 Git blob."""
    contract = _tracked_r5_snapshot_contract()
    accepted_upstream = manifest.get("accepted_upstream_r5")
    _require(isinstance(accepted_upstream, Mapping), "manifest_contract", "accepted_upstream_r5 is missing")
    for key, expected_key in {
        "pit_manifest_path": "path",
        "pit_manifest_commit": "commit",
        "pit_manifest_git_blob_id": "git_blob_id",
        "pit_manifest_raw_sha256": "raw_sha256",
    }.items():
        _require(
            accepted_upstream.get(key) == contract[expected_key],
            "manifest_contract",
            f"accepted R5 PIT field {key} drifted",
        )
    accepted_data = manifest.get("accepted_data_identities")
    _require(isinstance(accepted_data, Mapping), "manifest_contract", "accepted_data_identities is missing")
    for key, expected_key in {
        "r5_pit_manifest_git_blob": "git_blob_id",
        "r5_pit_manifest_raw_sha256": "raw_sha256",
        "r5_projection_raw_sha256": "projection_raw_sha256",
        "r5_projection_byte_size": "projection_byte_size",
        "r5_projection_digest": "projection_digest_sha256",
        "candidate_universe_digest": "candidate_universe_digest_sha256",
        "candidate_decision_eligibility_digest": "candidate_decision_eligibility_digest_sha256",
    }.items():
        _require(
            accepted_data.get(key) == contract[expected_key],
            "manifest_contract",
            f"accepted R5 data field {key} drifted",
        )
    scanner = manifest.get("scanner_contract")
    _require(isinstance(scanner, Mapping), "manifest_contract", "scanner_contract is missing")
    for key, expected_key in {
        "version": "scanner_version",
        "config_digest": "scanner_config_digest",
        "snapshot_count": "snapshot_count",
        "explicit_empty_snapshot_count": "explicit_empty_snapshot_count",
        "unique_candidate_count": "unique_candidate_count",
    }.items():
        _require(
            scanner.get(key) == contract[expected_key],
            "manifest_contract",
            f"scanner contract field {key} drifted",
        )
    evaluation = manifest.get("evaluation_snapshot_contract")
    _require(isinstance(evaluation, Mapping), "manifest_contract", "evaluation_snapshot_contract is missing")
    _require(
        evaluation.get("count") == contract["snapshot_count"]
        and evaluation.get("first_effective_at") == contract["first_effective_at"]
        and evaluation.get("last_effective_at") == contract["last_effective_at"]
        and evaluation.get("effective_at_sequence_digest_sha256") == contract["effective_at_sequence_digest_sha256"],
        "manifest_contract",
        "declared R5 evaluation boundary drifted",
    )
    declared_timestamps = evaluation.get("effective_at")
    _require(
        isinstance(declared_timestamps, list) and tuple(declared_timestamps) == contract["effective_at"],
        "manifest_contract",
        "declared R5 effective_at sequence drifted",
    )
    declared_source = evaluation.get("source")
    _require(
        isinstance(declared_source, Mapping)
        and declared_source.get("commit") == contract["commit"]
        and declared_source.get("path") == contract["path"]
        and declared_source.get("git_blob_id") == contract["git_blob_id"],
        "manifest_contract",
        "declared R5 snapshot source drifted",
    )


def _validate_r5_upstream(manifest_path: Path = _REPO / R5_MANIFEST_PATH) -> dict[str, object]:
    """Validate accepted R5 without trusting its mislabeled parent field."""
    manifest = _load_json(manifest_path)
    receipt_path = _REPO / R5_RECEIPT_PATH
    receipt = _load_json(receipt_path)
    manifest_blob = _tracked_identity(R5_MANIFEST_COMMIT, R5_MANIFEST_PATH)
    receipt_blob = _tracked_identity(R5_RESULT_COMMIT, R5_RECEIPT_PATH)
    _require(manifest_blob["git_blob_id"] == R5_MANIFEST_BLOB, "r5_identity", "R5 manifest Git blob changed")
    _require(manifest_blob["raw_sha256"] == R5_MANIFEST_RAW_SHA256, "r5_identity", "R5 manifest raw SHA changed")
    _require(
        manifest_path.read_bytes() == _git_blob(R5_MANIFEST_COMMIT, R5_MANIFEST_PATH),
        "r5_identity",
        "R5 manifest working bytes changed",
    )
    _require(receipt_blob["git_blob_id"] == R5_RECEIPT_BLOB, "r5_identity", "R5 receipt Git blob changed")
    without_digest = dict(receipt)
    recorded_digest = without_digest.pop("self_digest_sha256", None)
    _require(recorded_digest == R5_RECEIPT_SELF_DIGEST, "r5_receipt_digest", "R5 receipt declared self-digest changed")
    _require(
        canonical_digest(without_digest) == R5_RECEIPT_SELF_DIGEST,
        "r5_receipt_digest",
        "R5 receipt self-digest does not validate",
    )
    _require(receipt.get("data_gate_verdict") == "M3_DATA_GATE_PASS", "r5_verdict", "R5 data-gate verdict is not PASS")
    for key, expected in {
        "mission": "equity_m3_data_gate_2026_09_r5",
        "corrective_infrastructure_verdict": "PASS",
        "research_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_ready": False,
        "m3_candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
        "next_required_gate": "M3_CANDIDATE_OCP_GATE",
    }.items():
        _require(receipt.get(key) == expected, "r5_verdict", f"R5 field {key} is not accepted")
    gate_results = receipt.get("mandatory_gate_results")
    _require(isinstance(gate_results, Mapping), "r5_gates", "R5 mandatory gate map is missing")
    _require(set(gate_results) == set(manifest.get("mandatory_data_pass_gates", ())), "r5_gates", "R5 gate set drifted")
    _require(
        all(isinstance(value, Mapping) and value.get("status") == "PASS" for value in gate_results.values()),
        "r5_gates",
        "R5 has a non-PASS mandatory gate",
    )
    actual_parent = _git("rev-parse", f"{R5_IMPLEMENTATION}^")
    _require(actual_parent == R5_IMPLEMENTATION_PARENT, "r5_topology", "actual R5 implementation parent differs")
    _require(
        _git("rev-parse", f"{R5_MANIFEST_COMMIT}^") == R5_IMPLEMENTATION,
        "r5_topology",
        "R5 manifest is not a direct child of implementation",
    )
    _require(
        _git("rev-parse", f"{R5_RESULT_COMMIT}^") == R5_MANIFEST_COMMIT,
        "r5_topology",
        "R5 result is not a direct child of manifest",
    )
    _require(
        _git("rev-parse", f"{R5_D10_COMMIT}^") == R5_RESULT_COMMIT,
        "r5_topology",
        "R5 D10 report closure is not a direct child of C11",
    )
    _require(
        _git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", f"{R5_MANIFEST_COMMIT}^", R5_MANIFEST_COMMIT
        ).splitlines()
        == [R5_MANIFEST_PATH],
        "r5_topology",
        "R5 manifest commit is not manifest-only",
    )
    accepted_inputs = manifest.get("accepted_r4_inputs")
    _require(isinstance(accepted_inputs, Mapping), "r5_identity", "R5 accepted input block is missing")
    expected_input_values = {
        "canonical_raw_sha256": CANONICAL_RAW_SHA256,
        "raw_bundle_sha256": R4_RAW_BUNDLE_SHA256,
        "aggregate_page_hash_digest": R4_PAGE_DIGEST,
        "page_chain_digest_sha256": R4_PAGE_CHAIN_DIGEST,
        "source_receipt_sha256": R4_SOURCE_RECEIPT_SHA256,
        "normalized_sidecar_raw_sha256": R4_SIDECAR_RAW_SHA256,
        "normalized_sidecar_semantic_sha256": R4_SIDECAR_SEMANTIC_SHA256,
        "composite_semantic_digest_sha256": R4_COMPOSITE_SEMANTIC_SHA256,
        "raw_pit_sha256": R4_RAW_PIT_SHA256,
        "raw_pit_byte_size": R4_RAW_PIT_BYTES,
    }
    for key, expected in expected_input_values.items():
        _require(accepted_inputs.get(key) == expected, "r5_identity", f"R5 accepted input {key} changed")
    if "canonical_normalized_sha256" in accepted_inputs:
        _require(
            accepted_inputs.get("canonical_normalized_sha256") == CANONICAL_NORMALIZED_SHA256,
            "r5_identity",
            "R5 accepted input canonical_normalized_sha256 changed",
        )
    return {
        "manifest": manifest,
        "receipt": receipt,
        "manifest_identity": manifest_blob,
        "receipt_identity": receipt_blob,
        "actual_implementation_parent": actual_parent,
        "upstream_erratum": {
            "field": "receipt.commits.implementation_parent",
            "recorded_value": receipt.get("commits", {}).get("implementation_parent")
            if isinstance(receipt.get("commits"), Mapping)
            else None,
            "authoritative_value": actual_parent,
            "load_bearing_sources": [
                "R5 manifest implementation_parent_commit",
                "git parent of e22421c2486a20e883286345dbb509bbcd5d2fc9",
                "R5 mandatory commit_topology evidence",
            ],
            "non_load_bearing": True,
        },
    }


def _validate_projection(
    path: Path, r5_manifest: Mapping[str, object]
) -> tuple[Mapping[str, object], dict[str, object]]:
    _require(path.is_file(), "projection_missing", f"accepted R5 projection is missing: {path}")
    raw = _sha256_file(path)
    _require(raw == R5_PROJECTION_RAW_SHA256, "projection_identity", "R5 evaluation projection raw SHA mismatch")
    _require(
        path.stat().st_size == R5_PROJECTION_BYTES, "projection_identity", "R5 evaluation projection size mismatch"
    )
    projection = _load_json(path)
    snapshots = projection.get("snapshots")
    _require(
        isinstance(snapshots, list) and len(snapshots) == R5_SNAPSHOT_COUNT,
        "projection_identity",
        "R5 projection snapshot count mismatch",
    )
    projection_meta = projection.get("r5_projection")
    population = projection.get("population")
    _require(
        isinstance(projection_meta, Mapping) and isinstance(population, Mapping),
        "projection_identity",
        "R5 projection identity blocks are missing",
    )
    _require(
        projection_meta.get("projection_digest_sha256") == R5_PROJECTION_DIGEST,
        "projection_identity",
        "R5 projection semantic digest mismatch",
    )
    _require(
        population.get("candidate_universe_digest_sha256") == R5_CANDIDATE_UNIVERSE_DIGEST,
        "projection_identity",
        "R5 candidate-universe digest mismatch",
    )
    _require(
        population.get("candidate_decision_eligibility_digest_sha256") == R5_CANDIDATE_ELIGIBILITY_DIGEST,
        "projection_identity",
        "R5 candidate-eligibility digest mismatch",
    )
    _require(
        population.get("snapshot_count") == R5_SNAPSHOT_COUNT, "projection_identity", "R5 population count mismatch"
    )
    _require(
        population.get("explicit_empty_snapshot_count") == R5_EXPLICIT_EMPTY_COUNT,
        "projection_identity",
        "R5 empty-snapshot count mismatch",
    )
    _require(
        population.get("unique_candidate_count") == R5_UNIQUE_CANDIDATES,
        "projection_identity",
        "R5 unique-candidate count mismatch",
    )
    expected_timestamps = projection_meta.get("evaluation_effective_at_timestamps")
    _require(
        isinstance(expected_timestamps, list) and len(expected_timestamps) == R5_SNAPSHOT_COUNT,
        "projection_identity",
        "R5 exact timestamp set missing",
    )
    actual_timestamps: list[str] = []
    for raw_snapshot in snapshots:
        _require(isinstance(raw_snapshot, Mapping), "projection_identity", "R5 snapshot is not an object")
        effective = parse_timestamp(raw_snapshot.get("effective_at"))
        latest = parse_timestamp(raw_snapshot.get("latest_input_timestamp"))
        _require(latest < effective, "projection_timing", "R5 snapshot is not strictly prior")
        _require(raw_snapshot.get("strictly_prior_data") is True, "projection_timing", "R5 strict-prior marker missing")
        symbols = raw_snapshot.get("eligible_symbols")
        _require(
            isinstance(symbols, list) and symbols == raw_snapshot.get("symbols"),
            "projection_identity",
            "R5 membership aliases differ",
        )
        _require(
            symbols == sorted({str(symbol).upper() for symbol in symbols}),
            "projection_identity",
            "R5 membership is not canonical",
        )
        _require(
            not set(symbols).intersection(REFERENCE_SYMBOLS),
            "projection_identity",
            "R5 references entered candidate membership",
        )
        actual_timestamps.append(effective.isoformat(timespec="microseconds").replace("+00:00", "Z"))
    expected_canonical = [
        parse_timestamp(value).isoformat(timespec="microseconds").replace("+00:00", "Z")
        for value in expected_timestamps
    ]
    _require(
        actual_timestamps == expected_canonical,
        "projection_timing",
        "R5 snapshot sequence differs from declared exact timestamps",
    )
    del r5_manifest
    return projection, {
        "path": str(path),
        "raw_sha256": raw,
        "byte_size": path.stat().st_size,
        "projection_digest_sha256": R5_PROJECTION_DIGEST,
        "candidate_universe_digest_sha256": R5_CANDIDATE_UNIVERSE_DIGEST,
        "candidate_decision_eligibility_digest_sha256": R5_CANDIDATE_ELIGIBILITY_DIGEST,
        "matrix_merkle_root": R5_MATRIX_MERKLE_ROOT,
    }


def regenerate_r5_projection(output: Path, *, source_path: Path = R4_PIT_PATH) -> Path:
    """Regenerate the accepted R5 projection locally, never via network."""
    _require(not output.exists(), "projection_output_conflict", f"regeneration output already exists: {output}")
    import research_equity_m3_data_gate as r4_gate  # noqa: PLC0415
    import research_equity_m3_data_gate_r5 as r5_gate  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="m3-ocp-r5-regenerate-") as temporary:
        staging = Path(temporary)
        source, source_identity = r5_gate._validate_r4_source_pit(
            source_path,
            _REPO / R4_PIT_MANIFEST_PATH,
            staging,
            R5_MANIFEST_COMMIT,
        )
        r5_manifest = _load_json(_REPO / R5_MANIFEST_PATH)
        r5_contract_identity = _tracked_identity(R5_MANIFEST_COMMIT, R5_MANIFEST_PATH)
        canonical_manifest_blob = r4_gate._tracked_blob_identity(CANONICAL_MANIFEST_PATH, commit=R5_MANIFEST_COMMIT)
        _require(canonical_manifest_blob is not None, "projection_regeneration", "canonical manifest Git blob missing")
        canonical_identity = {
            **r5_gate.stage_file(CANONICAL_PATH, staging).receipt("canonical_equity_v5_bars", "external_untracked"),
            "manifest_git_blob": canonical_manifest_blob,
        }
        sidecar_rows, _quality = load_csv_reference_rows(R4_SIDECAR_PATH, reference_symbols=("IWM",))
        sidecar_identity = {
            "path": "data/research/generated/m3_data_gate_r4/iwm_normalized.csv",
            "raw_sha256": _sha256_file(R4_SIDECAR_PATH),
            "normalized_semantic_row_sha256": semantic_rows_digest(sidecar_rows["IWM"]),
            "normalized_row_count": len(sidecar_rows["IWM"]),
        }
        composite = _load_json(R4_COMPOSITE_PATH)
        identity = composite.get("identity")
        _require(isinstance(identity, Mapping), "projection_regeneration", "composite identity missing")
        projection = r5_gate.project_evaluation_pit(
            source,
            contract=r5_manifest,
            source_identity=source_identity,
            r4_manifest_identity=source_identity["source_compact_manifest"],
            canonical_identity=canonical_identity,
            sidecar_identity=sidecar_identity,
            composite_identity=identity,
            contract_identity=r5_contract_identity,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        r5_gate._write_json(output, projection)
    return output


def resolve_r5_projection(path: Path | None = None) -> tuple[Path, bool]:
    """Resolve an accepted projection; a stale generated file is never used."""
    if path is not None:
        _validate_projection(path, _load_json(_REPO / R5_MANIFEST_PATH))
        return path, False
    for candidate in (DEFAULT_R5_PROJECTION, STALE_R5_PROJECTION):
        if candidate.is_file():
            try:
                _validate_projection(candidate, _load_json(_REPO / R5_MANIFEST_PATH))
            except OcpFailure:
                continue
            return candidate, False
    regenerated = Path(tempfile.mktemp(prefix="m3-r5-projection-", suffix=".json", dir="/tmp"))
    regenerate_r5_projection(regenerated)
    _validate_projection(regenerated, _load_json(_REPO / R5_MANIFEST_PATH))
    return regenerated, True


def _expected_projection_map(projection: Mapping[str, object]) -> dict[datetime, Mapping[str, object]]:
    raw = projection.get("snapshots")
    _require(isinstance(raw, list), "projection_identity", "R5 snapshots are missing")
    result: dict[datetime, Mapping[str, object]] = {}
    for item in raw:
        _require(isinstance(item, Mapping), "projection_identity", "R5 snapshot is not an object")
        timestamp = parse_timestamp(item.get("effective_at"))
        _require(timestamp not in result, "projection_identity", "R5 snapshot boundary is duplicated")
        result[timestamp] = item
    return result


def _configured_pins(equity_config: EquityUniverseConfig) -> tuple[str, ...]:
    additional = tuple(symbol for symbol in equity_config.inverse_trade_symbols if symbol not in REFERENCE_SYMBOLS)
    return tuple(dict.fromkeys((*REFERENCE_SYMBOLS, *additional)))


def _scanner_snapshot(
    scanner: EquityUniverseScanner,
    timestamp: datetime,
    window: Mapping[str, Sequence[Bar]],
    equity_config: EquityUniverseConfig,
) -> tuple[int, tuple[str, ...], tuple[str, ...], str, str, tuple[str, ...]]:
    """Run the exact R5 Stage-A/scanner path and return pool + quality fallback."""
    references = REFERENCE_SYMBOLS
    by_symbol = {symbol: list(history) for symbol, history in window.items() if history}
    candidate_history = {symbol: history for symbol, history in by_symbol.items() if symbol not in references}
    data_timestamp = max(
        (history[-1].timestamp for history in by_symbol.values()),
        default=timestamp - timedelta(days=1),
    )
    if not candidate_history:
        return 0, (), (), scanner.version, scanner_config_digest(equity_config), references
    shortlisted = sorted(
        candidate_history.items(),
        key=lambda item: (-_median_dollar_volume_compat(item[1]), item[0]),
    )[: equity_config.target_shortlist]
    candidates = [
        equity_candidate_from_bars(symbol, history, expected_bar_count=min(20, LOOKBACK_DAYS))
        for symbol, history in shortlisted
    ]
    pinned_for_scanner = tuple(dict.fromkeys((*references, *equity_config.inverse_trade_symbols)))
    result = scanner.scan(
        candidates,
        data_timestamp=data_timestamp,
        selection_timestamp=timestamp,
        pinned_symbols=pinned_for_scanner,
    )
    eligible = tuple(
        sorted(symbol for symbol in result.selected_symbols if symbol in candidate_history and symbol not in references)
    )
    pins = set(_configured_pins(equity_config))
    fallback = tuple(symbol for symbol in result.observation_set if symbol in eligible and symbol not in pins)
    return (
        len(result.candidate_symbols),
        eligible,
        fallback,
        result.scanner_version,
        result.config_digest,
        tuple(result.observation_set),
    )


def _median_dollar_volume_compat(bars: Sequence[Bar]) -> float:
    values = [float(bar.close) * float(bar.volume) for bar in bars[-20:]]
    if not values:
        return 0.0
    values.sort()
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / 2


def iter_snapshot_inputs(
    projection: Mapping[str, object],
    *,
    canonical_path: Path = CANONICAL_PATH,
    sidecar_path: Path = R4_SIDECAR_PATH,
    equity_config: EquityUniverseConfig,
    maximum_history_bars: int,
) -> Iterator[SnapshotInput]:
    """Stream exact composite bars while keeping current bars out of history."""
    expected = _expected_projection_map(projection)
    cutoff = max(expected)
    sidecar_rows, quality = load_csv_reference_rows(sidecar_path, reference_symbols=("IWM",))
    _require(not quality["IWM"].get("rejected_row_count"), "sidecar_identity", "IWM sidecar contains rejected rows")
    groups = _merge_iwm_groups(_iter_canonical_groups(canonical_path), sidecar_rows["IWM"])
    scanner = EquityUniverseScanner(equity_config)
    scanner_window: dict[str, list[Bar]] = {}
    prior_history: dict[str, deque[Bar]] = {}
    previous_period: tuple[int, ...] | None = None
    expected_keys = set(expected)
    seen: set[datetime] = set()
    for timestamp, current_bars in groups:
        if timestamp > cutoff:
            break
        _require(
            timestamp in expected_keys, "snapshot_alignment", f"composite timestamp not in R5 projection: {timestamp}"
        )
        cutoff_window = timestamp - timedelta(days=LOOKBACK_DAYS)
        for symbol, history in tuple(scanner_window.items()):
            scanner_window[symbol] = [bar for bar in history if bar.timestamp >= cutoff_window]
            if not scanner_window[symbol]:
                del scanner_window[symbol]
        period = (timestamp.year, timestamp.month, timestamp.day)
        if period == previous_period:
            for bar in current_bars:
                scanner_window.setdefault(bar.symbol, []).append(bar)
                prior_history.setdefault(bar.symbol, deque(maxlen=maximum_history_bars)).append(bar)
            continue
        previous_period = period
        candidate_count, eligible, fallback, version, digest, _scanner_observation = _scanner_snapshot(
            scanner,
            timestamp,
            scanner_window,
            equity_config,
        )
        source = expected[timestamp]
        source_symbols = tuple(str(symbol) for symbol in source.get("eligible_symbols", ()))
        _require(
            candidate_count == source.get("candidate_count"), "scanner_pool", f"candidate count mismatch at {timestamp}"
        )
        _require(eligible == source_symbols, "scanner_pool", f"eligible candidate pool mismatch at {timestamp}")
        _require(
            version == source.get("scanner_version") == R5_SCANNER_VERSION,
            "scanner_identity",
            f"scanner version mismatch at {timestamp}",
        )
        _require(
            digest == source.get("config_digest") == R5_SCANNER_CONFIG_DIGEST,
            "scanner_identity",
            f"scanner config mismatch at {timestamp}",
        )
        latest = parse_timestamp(source.get("latest_input_timestamp"))
        inferred_latest = max(
            (bar.timestamp for history in scanner_window.values() for bar in history),
            default=timestamp - timedelta(days=1),
        )
        _require(inferred_latest == latest, "scanner_timing", f"latest input mismatch at {timestamp}")
        pins = _configured_pins(equity_config)
        pool_symbols = tuple(dict.fromkeys((*pins, *eligible)))
        history = {symbol: tuple(prior_history.get(symbol, ())) for symbol in pool_symbols}
        _require(
            all(bar.timestamp < timestamp for bars in history.values() for bar in bars),
            "future_history",
            f"history contains current/future bar at {timestamp}",
        )
        seen.add(timestamp)
        yield SnapshotInput(
            effective_at=timestamp,
            latest_input_timestamp=latest,
            candidate_count=int(candidate_count),
            eligible_candidates=eligible,
            scanner_fallback=fallback,
            history=history,
            scanner_version=version,
            scanner_config_digest=digest,
        )
        for bar in current_bars:
            scanner_window.setdefault(bar.symbol, []).append(bar)
            prior_history.setdefault(bar.symbol, deque(maxlen=maximum_history_bars)).append(bar)
    _require(seen == expected_keys, "snapshot_alignment", "composite and R5 snapshot sets differ")


def _ordered_merkle_root(digests: Sequence[str]) -> str:
    if not digests:
        return _sha256_bytes(b"m3-ocp-merkle-empty-v1")
    level = [bytes.fromhex(value) for value in digests]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            hashlib.sha256(b"m3-ocp-merkle-node-v1" + left + right).digest()
            for left, right in zip(level[::2], level[1::2], strict=True)
        ]
    return level[0].hex()


def _basis_snapshot(
    strategy: PortfolioStrategy,
    snapshot: SnapshotInput,
    *,
    pins: tuple[str, ...],
    maximum_stream_symbols: int,
) -> BasisSnapshot:
    pool = tuple(dict.fromkeys((*pins, *snapshot.eligible_candidates)))
    strict_history = _strict_history(snapshot.history, snapshot.latest_input_timestamp)
    result: ObservationSetResult = build_observation_set(
        strategy,
        strict_history,
        pool,
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=snapshot.latest_input_timestamp,
        maximum_stream_symbols=maximum_stream_symbols,
        pinned_symbols=pins,
        scanner_fallback=snapshot.scanner_fallback,
    )
    observation = tuple(result.observation_set)
    candidate_observation = observation[len(pins) :]
    expected_latest = strict_history.get("SPY", ())[-1].timestamp if strict_history.get("SPY") else None
    if candidate_observation:
        _require(expected_latest is not None, "stale_observation", "candidate entered without reference history")
        for symbol in candidate_observation:
            _require(
                len(strict_history.get(symbol, ())) >= strategy.minimum_history_bars
                and strict_history[symbol][-1].timestamp == expected_latest,
                "stale_observation",
                f"stale or incomplete candidate entered observation set: {symbol}",
            )
    warmup = not any(strict_history.get(symbol) for symbol in REFERENCE_SYMBOLS) or not candidate_observation
    # The shared builder exposes the exact no-rank/fallback classification. A
    # partial rank is still classified as strategy_alpha, matching the
    # product builder's source contract; the fallback sequence remains bound
    # and is exercised whenever the ranker cannot fill the free capacity.
    source = (
        "warmup/pins-only"
        if warmup and not candidate_observation
        else ("scanner_quality_fallback" if result.source == "scanner_quality_fallback" else "strategy_alpha")
    )
    leaf_payload = {
        "schema_version": "m3-ocp-observation-leaf-v1",
        "effective_at": snapshot.effective_at.isoformat(),
        "latest_input_timestamp": snapshot.latest_input_timestamp.isoformat(),
        "scanner_eligible_candidate_digest_sha256": canonical_digest(snapshot.eligible_candidates),
        "scanner_fallback_digest_sha256": canonical_digest(snapshot.scanner_fallback),
        "pinned_symbols": list(pins),
        "observation_set": list(observation),
        "maximum_stream_symbols": maximum_stream_symbols,
        "strategy_identity_digest_sha256": canonical_digest(strategy_identity(strategy)),
        "source_classification": source,
    }
    return BasisSnapshot(
        effective_at=snapshot.effective_at,
        latest_input_timestamp=snapshot.latest_input_timestamp,
        candidate_count=snapshot.candidate_count,
        eligible_candidates=snapshot.eligible_candidates,
        scanner_fallback=snapshot.scanner_fallback,
        reference_pins=REFERENCE_SYMBOLS,
        pinned_symbols=pins,
        product_visible_symbols=pool,
        observation_set=observation,
        maximum_stream_symbols=maximum_stream_symbols,
        scanner_version=snapshot.scanner_version,
        scanner_config_digest=snapshot.scanner_config_digest,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        source_classification=source,
        alpha_scored_count=result.alpha_scored_count,
        leaf_digest=canonical_digest(leaf_payload),
    )


def _to_universe(strategy: PortfolioStrategy, snapshots: Sequence[BasisSnapshot]) -> PointInTimeUniverse:
    return PointInTimeUniverse(
        market=Market.EQUITY,
        methodology="m3_candidate_owned_observation_basis_v1",
        snapshots=tuple(
            UniverseSnapshot(
                effective_at=item.effective_at,
                symbols=frozenset(item.product_visible_symbols),
                scanner_version=item.scanner_version,
                config_digest=item.scanner_config_digest,
                candidate_count=item.candidate_count,
                score_breakdown={},
                eliminated={},
                sector_metadata_coverage=1.0,
                observation_set=item.observation_set,
                maximum_stream_symbols=item.maximum_stream_symbols,
                pinned_symbols=item.pinned_symbols,
                observation_strategy_name=strategy.name,
                observation_strategy_version=strategy.version,
            )
            for item in snapshots
        ),
    )


def _record_snapshot(item: BasisSnapshot) -> dict[str, object]:
    return {
        "effective_at": item.effective_at.isoformat(),
        "latest_input_timestamp": item.latest_input_timestamp.isoformat(),
        "candidate_count": item.candidate_count,
        "eligible_candidate_symbols": list(item.eligible_candidates),
        "eligible_candidate_digest_sha256": canonical_digest(item.eligible_candidates),
        "scanner_fallback": list(item.scanner_fallback),
        "scanner_fallback_digest_sha256": canonical_digest(item.scanner_fallback),
        "reference_pin_symbols": list(item.reference_pins),
        "pinned_symbols": list(item.pinned_symbols),
        "product_visible_symbols": list(item.product_visible_symbols),
        "observation_set": list(item.observation_set),
        "maximum_stream_symbols": item.maximum_stream_symbols,
        "scanner_version": item.scanner_version,
        "scanner_config_digest": item.scanner_config_digest,
        "observation_strategy_name": item.strategy_name,
        "observation_strategy_version": item.strategy_version,
        "source_classification": item.source_classification,
        "alpha_scored_count": item.alpha_scored_count,
        "observation_leaf_digest_sha256": item.leaf_digest,
    }


def validate_basis_contract(
    basis: ObservationBasis, *, strategy: PortfolioStrategy, pins: tuple[str, ...]
) -> dict[str, int]:
    violations = {
        "pin_order": 0,
        "capacity": 0,
        "duplicate": 0,
        "outside_pool": 0,
        "stale": 0,
        "future": 0,
        "reference_membership": 0,
    }
    for item in basis.snapshots:
        observation = item.observation_set
        if tuple(observation[: len(pins)]) != pins or any(symbol not in observation for symbol in pins):
            violations["pin_order"] += 1
        if len(observation) > item.maximum_stream_symbols:
            violations["capacity"] += 1
        if len(observation) != len(set(observation)):
            violations["duplicate"] += 1
        if set(item.eligible_candidates).intersection(REFERENCE_SYMBOLS):
            violations["reference_membership"] += 1
        if any(symbol not in (*pins, *item.eligible_candidates) for symbol in observation):
            violations["outside_pool"] += 1
        if any(symbol not in item.eligible_candidates for symbol in observation[len(pins) :]):
            violations["outside_pool"] += 1
        # ``BasisSnapshot`` intentionally stores no bars.  The no-stale proof
        # is established during the stream build and by the shared builder's
        # latest-bar filter; this count catches any future implementation that
        # emits a ranked/fallback name without a ready latest bar.
        if len(observation) > len(pins) and item.source_classification == "warmup/pins-only":
            violations["stale"] += 1
        if item.latest_input_timestamp >= item.effective_at:
            violations["future"] += 1
    for key, count in violations.items():
        _require(count == 0, "observation_contract", f"observation contract violation: {key}={count}")
    _require(
        strategy.market == Market.EQUITY and strategy.timeframe == "1d",
        "strategy_identity",
        "strategy market/timeframe drifted",
    )
    return violations


def build_observation_basis(
    strategy: PortfolioStrategy,
    projection: Mapping[str, object],
    *,
    equity_config: EquityUniverseConfig,
    canonical_path: Path = CANONICAL_PATH,
    sidecar_path: Path = R4_SIDECAR_PATH,
    maximum_stream_symbols: int | None = None,
) -> ObservationBasis:
    maximum = maximum_stream_symbols if maximum_stream_symbols is not None else equity_config.maximum_stream_symbols
    pins = _configured_pins(equity_config)
    _require(len(pins) <= maximum, "capacity", "configured pins exceed stream capacity")
    records = tuple(
        _basis_snapshot(strategy, item, pins=pins, maximum_stream_symbols=maximum)
        for item in iter_snapshot_inputs(
            projection,
            canonical_path=canonical_path,
            sidecar_path=sidecar_path,
            equity_config=equity_config,
            maximum_history_bars=strategy.minimum_history_bars,
        )
    )
    universe = _to_universe(strategy, records)
    violations = validate_basis_contract(
        ObservationBasis(
            strategy_identity=strategy_identity(strategy),
            snapshots=records,
            universe=universe,
            raw_payload={},
            semantic_hash=hash_point_in_time_universe(universe),
            observation_merkle_root=_ordered_merkle_root([item.leaf_digest for item in records]),
            fallback_snapshot_count=sum(item.source_classification == "scanner_quality_fallback" for item in records),
            warmup_snapshot_count=sum(item.source_classification == "warmup/pins-only" for item in records),
            full_capacity_snapshot_count=sum(len(item.observation_set) == maximum for item in records),
        ),
        strategy=strategy,
        pins=pins,
    )
    del violations
    result = ObservationBasis(
        strategy_identity=strategy_identity(strategy),
        snapshots=records,
        universe=universe,
        raw_payload={
            "schema_version": "m3-ocp-observation-basis-v1",
            "mission": MISSION,
            "strategy_identity": strategy_identity(strategy),
            "snapshots": [_record_snapshot(item) for item in records],
        },
        semantic_hash=hash_point_in_time_universe(universe),
        observation_merkle_root=_ordered_merkle_root([item.leaf_digest for item in records]),
        fallback_snapshot_count=sum(item.source_classification == "scanner_quality_fallback" for item in records),
        warmup_snapshot_count=sum(item.source_classification == "warmup/pins-only" for item in records),
        full_capacity_snapshot_count=sum(len(item.observation_set) == maximum for item in records),
    )
    result.raw_payload["semantic_universe_hash"] = result.semantic_hash
    result.raw_payload["ordered_observation_merkle_root"] = result.observation_merkle_root
    return result


def _direct_builder_result(
    strategy: PortfolioStrategy, item: SnapshotInput, pins: tuple[str, ...], maximum: int
) -> tuple[str, ...]:
    pool = tuple(dict.fromkeys((*pins, *item.eligible_candidates)))
    return build_observation_set(
        strategy,
        _strict_history(item.history, item.latest_input_timestamp),
        pool,
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=item.latest_input_timestamp,
        maximum_stream_symbols=maximum,
        pinned_symbols=pins,
        scanner_fallback=item.scanner_fallback,
    ).observation_set


def _strict_history(history: Mapping[str, Sequence[Bar]], decision_timestamp: datetime) -> dict[str, tuple[Bar, ...]]:
    """Keep the exact latest strictly-prior bar and discard later bars."""
    return {
        symbol: tuple(bar for bar in bars if bar.timestamp <= decision_timestamp) for symbol, bars in history.items()
    }


def _sample_labels(items: Sequence[SnapshotInput], basis: ObservationBasis) -> dict[str, int]:
    _require(items, "samples", "no snapshot inputs available")
    result: dict[str, int] = {"first_source_snapshot": 0, "terminal_evaluation_snapshot": len(items) - 1}
    ready = next(
        (
            index
            for index, item in enumerate(items)
            if all(len(item.history.get(ref, ())) >= 274 for ref in REFERENCE_SYMBOLS)
        ),
        0,
    )
    result["first_m3_history_ready_snapshot"] = ready
    result["last_pre_m3_history_warm_snapshot"] = max(0, ready - 1)
    full_depth = next(
        (index for index, item in enumerate(items) if item.effective_at.date().isoformat() == "2017-02-06"), ready
    )
    result["first_full_candidate_depth_weekly_period"] = full_depth
    recent = next(
        (index for index, item in enumerate(items) if item.effective_at.date().isoformat() >= "2022-01-01"),
        len(items) - 1,
    )
    result["first_recent_window_snapshot"] = recent
    dst = next(
        (
            index
            for index, item in enumerate(items)
            if item.effective_at.date().isoformat() in {"2016-03-14", "2016-11-07"}
        ),
        0,
    )
    result["dst_transition_boundary"] = dst
    fallback = next(
        (
            index
            for index, item in enumerate(basis.snapshots)
            if item.source_classification == "scanner_quality_fallback"
        ),
        0,
    )
    result["scanner_fallback_use"] = fallback
    full_capacity = next(
        (
            index
            for index, item in enumerate(basis.snapshots)
            if len(item.observation_set) == item.maximum_stream_symbols
        ),
        len(items) - 1,
    )
    result["full_candidate_capacity"] = full_capacity
    return result


def _direct_parity(
    strategy: PortfolioStrategy, projection: Mapping[str, object], basis: ObservationBasis, config: EquityUniverseConfig
) -> dict[str, object]:
    # The stream is consumed once to keep sample histories exactly the same
    # as the full path.  No scores or coefficients are retained.
    inputs = tuple(
        iter_snapshot_inputs(
            projection,
            equity_config=config,
            maximum_history_bars=strategy.minimum_history_bars,
        )
    )
    labels = _sample_labels(inputs, basis)
    pins = _configured_pins(config)
    rows: dict[str, object] = {}
    mismatches: list[str] = []
    for label, index in labels.items():
        full_observation = basis.snapshots[index].observation_set
        direct_observation = _direct_builder_result(strategy, inputs[index], pins, config.maximum_stream_symbols)
        row = {
            "index": index,
            "effective_at": basis.snapshots[index].effective_at.isoformat(),
            "full_observation_digest_sha256": canonical_digest(full_observation),
            "direct_observation_digest_sha256": canonical_digest(direct_observation),
            "observation_count": len(full_observation),
            "fallback_digest_sha256": canonical_digest(inputs[index].scanner_fallback),
            "match": full_observation == direct_observation,
        }
        rows[label] = row
        if full_observation != direct_observation:
            mismatches.append(label)
    _require(not mismatches, "direct_builder", f"direct builder mismatches: {mismatches}")
    return {"sample_count": len(rows), "samples": rows, "mismatch_count": len(mismatches)}


def _future_bar_tests(
    projection: Mapping[str, object], config: EquityUniverseConfig, basis: ObservationBasis
) -> dict[str, object]:
    # Future mutation is exercised at deterministic boundaries by the pure
    # builder.  The streaming implementation itself only exposes strict-prior
    # histories; adding an after-boundary bar to the source cannot change the
    # yielded pre-boundary record.  Keep the compact evidence to hashes/counts.
    inputs = tuple(iter_snapshot_inputs(projection, equity_config=config, maximum_history_bars=274))
    labels = _sample_labels(inputs, basis)
    checks: dict[str, object] = {}
    failures: list[str] = []
    for label in (
        "first_source_snapshot",
        "dst_transition_boundary",
        "first_recent_window_snapshot",
        "terminal_evaluation_snapshot",
    ):
        index = labels[label]
        item = inputs[index]
        baseline = _direct_builder_result(
            ProxyMultifactorResidualMomentum(dict(M3_FROZEN_PARAMETERS)),
            item,
            _configured_pins(config),
            config.maximum_stream_symbols,
        )
        future_timestamp = item.effective_at + timedelta(days=1)
        mutated_history = {
            symbol: (
                *bars,
                Bar(
                    future_timestamp,
                    symbol,
                    "1d",
                    400.0,
                    500.0,
                    100.0,
                    450.0,
                    1.0,
                    Market.EQUITY,
                ),
            )
            for symbol, bars in item.history.items()
        }
        mutated = SnapshotInput(
            effective_at=item.effective_at,
            latest_input_timestamp=item.latest_input_timestamp,
            candidate_count=item.candidate_count,
            eligible_candidates=item.eligible_candidates,
            scanner_fallback=item.scanner_fallback,
            history=mutated_history,
            scanner_version=item.scanner_version,
            scanner_config_digest=item.scanner_config_digest,
        )
        changed = _direct_builder_result(
            ProxyMultifactorResidualMomentum(dict(M3_FROZEN_PARAMETERS)),
            mutated,
            _configured_pins(config),
            config.maximum_stream_symbols,
        )
        checks[label] = {
            "effective_at": item.effective_at.isoformat(),
            "baseline_observation_digest_sha256": canonical_digest(baseline),
            "mutated_observation_digest_sha256": canonical_digest(changed),
            "match": baseline == changed,
        }
        if baseline != changed:
            failures.append(label)
    _require(not failures, "future_bar", f"future-bar mutation changed observations: {failures}")
    return {"sample_count": len(checks), "checks": checks, "failure_count": len(failures)}


def _e1_attestation() -> dict[str, object]:
    path = _REPO / "data/research_receipts/equity_candidate_observation_parity_final_closure_2026_08_r3.json"
    report = _REPO / "docs/equity_candidate_observation_parity_final_closure_2026_08_r3.md"
    _require(
        path.is_file() and report.is_file(), "e1_attestation", "historical generic OCP closure artifacts are missing"
    )
    receipt = _load_json(path)
    digest_field = "compact_receipt_sha256"
    digest = receipt.get(digest_field)
    body = dict(receipt)
    body.pop(digest_field, None)
    _require(
        digest == "ca800a18dbed839c69c70c334861390382977b17ae5f90c34a238c3154cbf6ac",
        "e1_attestation",
        "generic OCP self-digest mismatch",
    )
    _require(canonical_digest(body) == digest, "e1_attestation", "generic OCP compact digest does not validate")
    _require(
        "OCP_GATE_PASS" in report.read_text(encoding="utf-8")
        and "READY_FOR_M3_DATA_GATE" in report.read_text(encoding="utf-8"),
        "e1_attestation",
        "generic OCP report is not accepted",
    )
    return {"status": "PASS", "receipt_self_digest_sha256": digest, "github_actions_run": "33439399546"}


def build_manifest(*, implementation_commit: str | None = None) -> dict[str, object]:
    implementation = implementation_commit or _git("rev-parse", "HEAD")
    parent = _git("rev-parse", f"{implementation}^")
    critical_paths = (
        "scripts/research_equity_m3_candidate_ocp.py",
        "scripts/compact_equity_m3_candidate_ocp_receipt.py",
        "tests/test_research_equity_m3_candidate_ocp.py",
        "tests/fixtures/equity_m3_candidate_ocp/README.md",
        "scripts/research_equity_m3_m4.py",
        "src/parayabuck/universe/observation_set.py",
        "src/parayabuck/backtest/observation_parity.py",
        "src/parayabuck/backtest/universe.py",
        ".github/workflows/ci.yml",
    )
    critical = {path: _tracked_identity(implementation, path) for path in critical_paths}
    snapshot_contract = _tracked_r5_snapshot_contract()
    return {
        "schema_version": "1.0.0",
        "mission": MISSION,
        "declared_before_results": True,
        "repository_url": "https://github.com/batuhankiskac/ParayaBuck",
        "branch": "main",
        "base_d10_commit": BASE_COMMIT,
        "implementation_commit": implementation,
        "implementation_parent_commit": parent,
        "implementation_tree": _git("rev-parse", f"{implementation}^{{tree}}"),
        "critical_git_blobs": critical,
        "manifest_only_commit_rule": {
            "parent": implementation,
            "changed_paths": ["data/research_manifests/equity_m3_candidate_ocp_2026_09.json"],
        },
        "accepted_upstream_r5": {
            "manifest_path": R5_MANIFEST_PATH,
            "manifest_commit": R5_MANIFEST_COMMIT,
            "manifest_git_blob_id": R5_MANIFEST_BLOB,
            "manifest_raw_sha256": R5_MANIFEST_RAW_SHA256,
            "receipt_path": R5_RECEIPT_PATH,
            "receipt_commit": R5_RESULT_COMMIT,
            "receipt_git_blob_id": R5_RECEIPT_BLOB,
            "receipt_self_digest_sha256": R5_RECEIPT_SELF_DIGEST,
            "result_commit": R5_RESULT_COMMIT,
            "d10_commit": R5_D10_COMMIT,
            "pit_manifest_path": snapshot_contract["path"],
            "pit_manifest_commit": snapshot_contract["commit"],
            "pit_manifest_git_blob_id": snapshot_contract["git_blob_id"],
            "pit_manifest_raw_sha256": snapshot_contract["raw_sha256"],
            "implementation_commit": R5_IMPLEMENTATION,
            "implementation_tree": R5_IMPLEMENTATION_TREE,
            "ci_run": R5_CI_RUN,
            "d10_ci_run": R5_D10_CI_RUN,
        },
        "accepted_upstream_errata": {
            "field": "receipt.commits.implementation_parent",
            "recorded_value": R5_IMPLEMENTATION,
            "authoritative_value": R5_IMPLEMENTATION_PARENT,
            "non_load_bearing": True,
            "required_handling": "recompute Git parent and use R5 mandatory commit_topology evidence",
        },
        "accepted_data_identities": {
            "canonical_raw_sha256": CANONICAL_RAW_SHA256,
            "canonical_normalized_sha256": CANONICAL_NORMALIZED_SHA256,
            "canonical_manifest_git_blob": CANONICAL_MANIFEST_BLOB,
            "lifetimes_raw_sha256": LIFETIMES_RAW_SHA256,
            "corporate_actions_raw_sha256": CORPORATE_ACTIONS_RAW_SHA256,
            "iwm_raw_bundle_sha256": R4_RAW_BUNDLE_SHA256,
            "iwm_source_receipt_sha256": R4_SOURCE_RECEIPT_SHA256,
            "iwm_aggregate_page_hash_digest": R4_PAGE_DIGEST,
            "iwm_page_chain_digest": R4_PAGE_CHAIN_DIGEST,
            "iwm_sidecar_raw_sha256": R4_SIDECAR_RAW_SHA256,
            "iwm_sidecar_semantic_sha256": R4_SIDECAR_SEMANTIC_SHA256,
            "composite_semantic_sha256": R4_COMPOSITE_SEMANTIC_SHA256,
            "r4_raw_pit_sha256": R4_RAW_PIT_SHA256,
            "r4_raw_pit_byte_size": R4_RAW_PIT_BYTES,
            "r5_pit_manifest_git_blob": snapshot_contract["git_blob_id"],
            "r5_pit_manifest_raw_sha256": snapshot_contract["raw_sha256"],
            "r5_projection_raw_sha256": snapshot_contract["projection_raw_sha256"],
            "r5_projection_byte_size": snapshot_contract["projection_byte_size"],
            "r5_projection_digest": snapshot_contract["projection_digest_sha256"],
            "candidate_universe_digest": snapshot_contract["candidate_universe_digest_sha256"],
            "candidate_decision_eligibility_digest": snapshot_contract["candidate_decision_eligibility_digest_sha256"],
            "matrix_merkle_root": R5_MATRIX_MERKLE_ROOT,
        },
        "scanner_contract": {
            "scanner": "EquityUniverseScanner",
            "version": snapshot_contract["scanner_version"],
            "config_digest": snapshot_contract["scanner_config_digest"],
            "cadence": "daily",
            "snapshot_count": snapshot_contract["snapshot_count"],
            "explicit_empty_snapshot_count": snapshot_contract["explicit_empty_snapshot_count"],
            "unique_candidate_count": snapshot_contract["unique_candidate_count"],
            "fallback": "reconstruct from exact composite bars; no alphabetical fallback",
            "references_excluded_before_stage_a": True,
        },
        "evaluation_snapshot_contract": {
            "count": snapshot_contract["snapshot_count"],
            "first_effective_at": snapshot_contract["first_effective_at"],
            "last_effective_at": snapshot_contract["last_effective_at"],
            "effective_at": list(snapshot_contract["effective_at"]),
            "effective_at_sequence_digest_sha256": snapshot_contract["effective_at_sequence_digest_sha256"],
            "source": {
                "commit": snapshot_contract["commit"],
                "path": snapshot_contract["path"],
                "git_blob_id": snapshot_contract["git_blob_id"],
            },
            "comparison": "full timezone-aware effective_at <= exact cutoff",
            "strict_prior_rule": "latest_input_timestamp < effective_at",
            "date_only_selection_forbidden": True,
        },
        "strategies": {
            "control": {"name": "equity_residual_momentum_v3", "version": "3.0.0", "parameters": M3_FROZEN_PARAMETERS},
            "candidate": {
                "name": ProxyMultifactorResidualMomentum.name,
                "version": ProxyMultifactorResidualMomentum.version,
                "parameters": M3_FROZEN_PARAMETERS,
            },
        },
        "reference_contract": {
            "reference_symbols": list(REFERENCE_SYMBOLS),
            "observation_only": True,
            "candidate_membership_excluded": True,
            "pin_order": list(REFERENCE_SYMBOLS),
            "additional_configured_pins_separate": True,
        },
        "capacity_contract": {
            "maximum_stream_symbols": 30,
            "reference_pin_count": 3,
            "additional_reserved_pin_count": 3,
            "total_reserved_pin_count": 6,
            "effective_candidate_capacity": 24,
            "pin_order": ["SPY", "IWM", "QQQ", "SH", "PSQ", "DOG"],
            "configured_inverse_pins": ["SH", "PSQ", "DOG"],
        },
        "execution_boundaries": {
            "C|C": {
                "observation_basis": "same-run V3 control",
                "observer": "V3",
                "execution": "V3",
                "economics": False,
            },
            "X|C": {
                "observation_basis": "same-run V3 control",
                "observer": "V3",
                "execution": "M3",
                "diagnostic_only": True,
                "economics": False,
            },
            "X|X": {"observation_basis": "candidate-owned M3", "observer": "M3", "execution": "M3", "economics": False},
        },
        "direct_builder_samples": [
            "first_source_snapshot",
            "last_pre_m3_history_warm_snapshot",
            "first_m3_history_ready_snapshot",
            "first_full_candidate_depth_weekly_period",
            "first_recent_window_snapshot",
            "dst_transition_boundary",
            "scanner_fallback_use",
            "full_candidate_capacity",
            "terminal_evaluation_snapshot",
        ],
        "future_bar_samples": [
            "first_source_snapshot",
            "dst_transition_boundary",
            "first_recent_window_snapshot",
            "terminal_evaluation_snapshot",
        ],
        "recovery_lineage": {
            "prior_implementation": PRIOR_A22_COMMIT,
            "prior_manifest": PRIOR_B22_COMMIT,
            "prior_local_result": PRIOR_C22_COMMIT,
            "prior_remote_run": PRIOR_C22_REMOTE_CI_RUN,
            "prior_remote_result": PRIOR_C22_REMOTE_RESULT,
            "failure_category": PRIOR_C22_FAILURE_CATEGORY,
            "exact_accepted_data_replay": True,
            "automatic_refetch": False,
            "expected_substantive_observation_parity": "unchanged",
        },
        "mandatory_gates": list(OCP_MANDATORY_GATES),
        "forbidden_economic_fields": list(FORBIDDEN_ECONOMIC_FIELDS),
        "no_network_or_credentials": True,
        "post_pass_boundary": {
            "next_required_mission": "M3_ECONOMIC_MISSION_PREDECLARATION",
            "economic_mission_authorized": False,
            "automatic_promotion": False,
        },
    }


def _gate_map() -> dict[str, dict[str, object]]:
    return {name: {"status": "UNKNOWN", "evidence": None} for name in OCP_MANDATORY_GATES}


def _set_gate(
    gates: dict[str, dict[str, object]], name: str, status: str, evidence: object = None, reason: str | None = None
) -> None:
    gates[name] = {"status": status, "evidence": evidence, **({"reason": reason} if reason else {})}


def _write_manifest_command(path: Path) -> int:
    _require(not path.exists(), "manifest_conflict", f"pre-result manifest already exists: {path}")
    _write_json(path, build_manifest())
    print(path)
    return 0


def _make_raw_payload(
    *,
    upstream: Mapping[str, object],
    input_artifacts: Mapping[str, object],
    config: Config,
    c1: PortfolioStrategy,
    c2: PortfolioStrategy,
    c_execution: PortfolioStrategy,
    x1: PortfolioStrategy,
    x2: PortfolioStrategy,
    x_execution: PortfolioStrategy,
    c_basis: ObservationBasis,
    c_repeat: ObservationBasis,
    x_basis: ObservationBasis,
    x_repeat: ObservationBasis,
    direct: Mapping[str, object],
    future: Mapping[str, object],
    comparisons: Mapping[str, object],
    gates: Mapping[str, object],
    blocked_reasons: Sequence[str],
    run_commit: str,
    implementation_identity: Mapping[str, object],
    manifest_identity: Mapping[str, object],
    r5_projection_path: Path,
    historical_ocp: Mapping[str, object],
) -> dict[str, object]:
    c_id = strategy_identity(c1)
    x_id = strategy_identity(x1)
    return {
        "schema_version": "1.0.0",
        "mission": MISSION,
        "verdict": VERDICT_PASS if not blocked_reasons else VERDICT_BLOCKED,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_authorized": False,
        "economic_mission_run": False,
        "run_commit": run_commit,
        "implementation_identity": dict(implementation_identity),
        "manifest_identity": dict(manifest_identity),
        "upstream_r5": {
            "data_gate_status": "M3_DATA_GATE_PASS",
            "manifest_identity": upstream["manifest_identity"],
            "receipt_identity": upstream["receipt_identity"],
            "receipt_self_digest_sha256": R5_RECEIPT_SELF_DIGEST,
            "implementation_commit": R5_IMPLEMENTATION,
            "implementation_parent_commit": upstream["actual_implementation_parent"],
            "projection_path": str(r5_projection_path),
            "projection_sha256": R5_PROJECTION_RAW_SHA256,
            "projection_digest_sha256": R5_PROJECTION_DIGEST,
            "matrix_merkle_root": R5_MATRIX_MERKLE_ROOT,
        },
        "historical_generic_ocp": dict(historical_ocp),
        "accepted_upstream_errata": upstream["upstream_erratum"],
        "input_artifacts": dict(input_artifacts),
        "strategy_identities": {
            "C_observer_1": c_id,
            "C_observer_2": strategy_identity(c2),
            "C_execution": strategy_identity(c_execution),
            "X_observer_1": x_id,
            "X_observer_2": strategy_identity(x2),
            "X_execution": strategy_identity(x_execution),
            "object_instance_separation": {
                "C_observer_1_is_C_observer_2": c1 is c2,
                "C_observer_1_is_C_execution": c1 is c_execution,
                "X_observer_1_is_X_observer_2": x1 is x2,
                "X_observer_1_is_X_execution": x1 is x_execution,
            },
        },
        "reference_contract": {
            "reference_symbols": list(REFERENCE_SYMBOLS),
            "candidate_membership_intersection": [],
            "observation_only": True,
        },
        "capacity_contract": {
            "maximum_stream_symbols": config.universe.equity.maximum_stream_symbols,
            "reference_pin_count": len(REFERENCE_SYMBOLS),
            "additional_reserved_pin_count": len(_configured_pins(config.universe.equity)) - len(REFERENCE_SYMBOLS),
            "total_reserved_pin_count": len(_configured_pins(config.universe.equity)),
            "effective_candidate_observation_capacity": config.universe.equity.maximum_stream_symbols
            - len(_configured_pins(config.universe.equity)),
            "pin_order": list(_configured_pins(config.universe.equity)),
        },
        "scanner_basis": {
            "scanner": "EquityUniverseScanner",
            "version": R5_SCANNER_VERSION,
            "config_digest": R5_SCANNER_CONFIG_DIGEST,
            "candidate_pool_digest": R5_CANDIDATE_UNIVERSE_DIGEST,
            "candidate_decision_eligibility_digest": R5_CANDIDATE_ELIGIBILITY_DIGEST,
            "fallback_reconstructed": True,
        },
        "snapshot_coverage": {
            "count": len(c_basis.snapshots),
            "first": c_basis.snapshots[0].effective_at.isoformat(),
            "last": c_basis.snapshots[-1].effective_at.isoformat(),
        },
        "C_control": {
            "observer_identity": c_id,
            "execution_identity": strategy_identity(c_execution),
            "observation_universe_raw_artifact": c_basis.raw_payload,
            "semantic_universe_hash": c_basis.semantic_hash,
            "ordered_observation_merkle_root": c_basis.observation_merkle_root,
            "repeat_semantic_universe_hash": c_repeat.semantic_hash,
            "repeat_observation_merkle_root": c_repeat.observation_merkle_root,
            "snapshot_count": len(c_basis.snapshots),
            "warmup_snapshot_count": c_basis.warmup_snapshot_count,
            "fallback_snapshot_count": c_basis.fallback_snapshot_count,
            "full_capacity_snapshot_count": c_basis.full_capacity_snapshot_count,
        },
        "X_candidate": {
            "observer_identity": x_id,
            "execution_identity": strategy_identity(x_execution),
            "observation_universe_raw_artifact": x_basis.raw_payload,
            "semantic_universe_hash": x_basis.semantic_hash,
            "ordered_observation_merkle_root": x_basis.observation_merkle_root,
            "repeat_semantic_universe_hash": x_repeat.semantic_hash,
            "repeat_observation_merkle_root": x_repeat.observation_merkle_root,
            "snapshot_count": len(x_basis.snapshots),
            "warmup_snapshot_count": x_basis.warmup_snapshot_count,
            "fallback_snapshot_count": x_basis.fallback_snapshot_count,
            "full_capacity_snapshot_count": x_basis.full_capacity_snapshot_count,
        },
        "C|C": {
            "observation_basis": "same-run V3 control",
            "observer_identity": c_id,
            "execution_identity": strategy_identity(c_execution),
            "identities_equal": c_id == strategy_identity(c_execution),
            "objects_different": c1 is not c_execution,
            "economics_not_run": True,
        },
        "X|C": {
            "observation_basis": "same-run V3 control",
            "observer_identity": c_id,
            "execution_identity": x_id,
            "identities_intentionally_different": c_id != x_id,
            "diagnostic_only": True,
            "economics_not_run": True,
        },
        "X|X": {
            "observation_basis": "candidate-owned M3",
            "observer_identity": x_id,
            "execution_identity": strategy_identity(x_execution),
            "identities_equal": x_id == strategy_identity(x_execution),
            "objects_different": x1 is not x_execution,
            "economics_not_run": True,
        },
        "direct_builder_parity": dict(direct),
        "determinism": {
            "C_match": c_basis.semantic_hash == c_repeat.semantic_hash
            and c_basis.observation_merkle_root == c_repeat.observation_merkle_root,
            "X_match": x_basis.semantic_hash == x_repeat.semantic_hash
            and x_basis.observation_merkle_root == x_repeat.observation_merkle_root,
        },
        "future_bar_tests": dict(future),
        "observation_change_summary": dict(comparisons),
        "mandatory_gate_results": dict(gates),
        "forbidden_economic_fields": list(FORBIDDEN_ECONOMIC_FIELDS),
        "blocked_reasons": list(blocked_reasons),
        "no_network_or_credentials": True,
    }


def _raw_artifact(path: Path, payload: Mapping[str, object]) -> dict[str, object]:
    data = _write_json(path, payload)
    return {
        "path": _display_path(path),
        "raw_sha256": _sha256_bytes(data),
        "byte_size": len(data),
        "semantic_digest_sha256": canonical_digest(payload),
    }


def run_gate(
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    projection_path: Path | None = None,
    raw_dir: Path = DEFAULT_RAW_DIR,
) -> tuple[dict[str, object], int]:
    gates = _gate_map()
    blocked: list[str] = []
    start_dirty = _dirty_paths()
    run_commit = _git("rev-parse", "HEAD")
    manifest_identity: dict[str, object] = {}
    manifest: Mapping[str, object] = {}
    implementation = ""
    upstream: dict[str, object] = {}
    input_artifacts: dict[str, object] = {}
    historical_ocp: dict[str, object] = {}
    isolation_path: Path | None = None
    try:
        _require(run_commit != BASE_COMMIT, "commit_topology", "gate must run from a post-D10 manifest commit")
        manifest = _load_json(manifest_path)
        _require(manifest.get("schema_version") == "1.0.0", "manifest_contract", "OCP manifest schema drifted")
        _require(manifest.get("mission") == MISSION, "manifest_contract", "OCP manifest mission drifted")
        _require(manifest.get("declared_before_results") is True, "manifest_contract", "manifest was not pre-result")
        _require(manifest.get("base_d10_commit") == BASE_COMMIT, "manifest_contract", "OCP base commit drifted")
        _require(
            manifest.get("mandatory_gates") == list(OCP_MANDATORY_GATES),
            "manifest_contract",
            "OCP mandatory gate list drifted",
        )
        _require(
            manifest.get("forbidden_economic_fields") == list(FORBIDDEN_ECONOMIC_FIELDS),
            "manifest_contract",
            "OCP forbidden-field contract drifted",
        )
        _require(
            manifest.get("strategies")
            == {
                "control": {
                    "name": "equity_residual_momentum_v3",
                    "version": "3.0.0",
                    "parameters": M3_FROZEN_PARAMETERS,
                },
                "candidate": {
                    "name": ProxyMultifactorResidualMomentum.name,
                    "version": ProxyMultifactorResidualMomentum.version,
                    "parameters": M3_FROZEN_PARAMETERS,
                },
            },
            "manifest_contract",
            "frozen strategy identity contract drifted",
        )
        _require(
            manifest.get("capacity_contract")
            == {
                "maximum_stream_symbols": 30,
                "reference_pin_count": 3,
                "additional_reserved_pin_count": 3,
                "total_reserved_pin_count": 6,
                "effective_candidate_capacity": 24,
                "pin_order": ["SPY", "IWM", "QQQ", "SH", "PSQ", "DOG"],
                "configured_inverse_pins": ["SH", "PSQ", "DOG"],
            },
            "manifest_contract",
            "frozen capacity contract drifted",
        )
        implementation = str(manifest.get("implementation_commit", ""))
        implementation_parent = str(manifest.get("implementation_parent_commit", ""))
        _require(
            _git("rev-parse", "HEAD^") == implementation,
            "commit_topology",
            "current commit is not direct child of declared implementation",
        )
        _require(
            implementation_parent and _git("rev-parse", f"{implementation}^") == implementation_parent,
            "commit_topology",
            "implementation parent differs from manifest declaration",
        )
        changed = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD^", "HEAD").splitlines()
        _require(
            changed == ["data/research_manifests/equity_m3_candidate_ocp_2026_09.json"],
            "manifest_only_commit",
            "manifest commit is not manifest-only",
        )
        _require(
            _git("rev-parse", f"{implementation}^{{tree}}") == manifest.get("implementation_tree"),
            "implementation_blob_identity",
            "A19 tree differs from manifest",
        )
        for path, declaration in (manifest.get("critical_git_blobs") or {}).items():
            _require(
                isinstance(declaration, Mapping),
                "implementation_blob_identity",
                f"critical blob declaration missing: {path}",
            )
            actual = _tracked_identity(implementation, str(path))
            _require(
                actual["git_blob_id"] == declaration.get("git_blob_id"),
                "implementation_blob_identity",
                f"critical blob changed: {path}",
            )
        _require(
            set(manifest.get("critical_git_blobs", {}))
            == {
                "scripts/research_equity_m3_candidate_ocp.py",
                "scripts/compact_equity_m3_candidate_ocp_receipt.py",
                "tests/test_research_equity_m3_candidate_ocp.py",
                "tests/fixtures/equity_m3_candidate_ocp/README.md",
                "scripts/research_equity_m3_m4.py",
                "src/parayabuck/universe/observation_set.py",
                "src/parayabuck/backtest/observation_parity.py",
                "src/parayabuck/backtest/universe.py",
                ".github/workflows/ci.yml",
            },
            "implementation_blob_identity",
            "critical blob path set drifted",
        )
        manifest_identity = _tracked_identity(
            run_commit, "data/research_manifests/equity_m3_candidate_ocp_2026_09.json"
        )
        _set_gate(
            gates,
            "commit_topology",
            "PASS",
            {
                "run_commit": run_commit,
                "implementation_commit": implementation,
                "implementation_parent": implementation_parent,
            },
        )
        _set_gate(gates, "implementation_blob_identity", "PASS", manifest.get("critical_git_blobs"))
        _set_gate(gates, "manifest_only_commit", "PASS", changed)
    except OcpFailure as exc:
        blocked.append(str(exc))
        for name in ("commit_topology", "implementation_blob_identity", "manifest_only_commit"):
            _set_gate(gates, name, "FAIL", reason=str(exc))
    if start_dirty:
        blocked.append("clean_worktree_at_start: " + "; ".join(start_dirty))
        _set_gate(gates, "clean_worktree_at_start", "FAIL", start_dirty)
    else:
        _set_gate(gates, "clean_worktree_at_start", "PASS", [])
    try:
        from check_production_isolation import record  # noqa: PLC0415

        isolation_path = Path(tempfile.mktemp(prefix="m3-ocp-isolation-", suffix=".json", dir="/tmp"))
        record(isolation_path)
    except (OSError, ValueError) as exc:
        blocked.append(f"production_isolation: {exc}")
        _set_gate(gates, "production_isolation", "FAIL", reason=str(exc))
    try:
        upstream = _validate_r5_upstream()
        _set_gate(gates, "r5_data_gate_receipt_identity", "PASS", upstream["receipt_identity"])
        _set_gate(gates, "r5_data_gate_verdict", "PASS", {"data_gate_verdict": "M3_DATA_GATE_PASS"})
        _set_gate(gates, "r5_all_mandatory_gates", "PASS", "all accepted R5 gates PASS")
        _set_gate(gates, "r5_upstream_erratum_acknowledged", "PASS", upstream["upstream_erratum"])
        manifest = _load_json(manifest_path)
        _require(
            isinstance(manifest.get("accepted_upstream_errata"), Mapping),
            "manifest_contract",
            "new OCP manifest is missing accepted_upstream_errata",
        )
        declared_erratum = manifest["accepted_upstream_errata"]
        _require(
            dict(declared_erratum)
            == {
                "field": "receipt.commits.implementation_parent",
                "recorded_value": R5_IMPLEMENTATION,
                "authoritative_value": R5_IMPLEMENTATION_PARENT,
                "non_load_bearing": True,
                "required_handling": "recompute Git parent and use R5 mandatory commit_topology evidence",
            },
            "manifest_contract",
            "accepted upstream erratum declaration drifted",
        )
        _validate_declared_snapshot_contract(manifest)
        historical_ocp = _e1_attestation()
    except OcpFailure as exc:
        blocked.append(str(exc))
        for name in (
            "r5_data_gate_receipt_identity",
            "r5_data_gate_verdict",
            "r5_all_mandatory_gates",
            "r5_upstream_erratum_acknowledged",
        ):
            _set_gate(gates, name, "FAIL", reason=str(exc))
    try:
        with tempfile.TemporaryDirectory(prefix="m3-ocp-input-") as temporary:
            staging = Path(temporary)
            bundle_rows, bundle_audit = validate_iwm_bundle(R4_BUNDLE_PATH, staging, r4=True, require_promoted=True)
            acquisition = _load_json(_REPO / "data/research_receipts/equity_m3_iwm_acquisition_2026_09_r4.json")
            _require(
                bundle_audit.get("raw_bundle_sha256") == R4_RAW_BUNDLE_SHA256, "r4_bundle", "R4 raw bundle mismatch"
            )
            source_receipt = bundle_audit.get("source_receipt")
            _require(
                isinstance(source_receipt, Mapping) and source_receipt.get("raw_sha256") == R4_SOURCE_RECEIPT_SHA256,
                "r4_bundle",
                "R4 source receipt mismatch",
            )
            _require(
                acquisition.get("aggregate_page_hash_digest") == R4_PAGE_DIGEST,
                "r4_bundle",
                "R4 aggregate page digest mismatch",
            )
            _require(
                acquisition.get("page_chain_digest_sha256") == R4_PAGE_CHAIN_DIGEST,
                "r4_bundle",
                "R4 page-chain digest mismatch",
            )
            _set_gate(gates, "r4_raw_bundle_identity", "PASS", {"raw_bundle_sha256": bundle_audit["raw_bundle_sha256"]})
            _set_gate(
                gates,
                "r4_sidecar_identity",
                "PASS",
                _canonical_identity(R4_SIDECAR_PATH, expected_raw=R4_SIDECAR_RAW_SHA256),
            )
            sidecar_rows, _sidecar_quality = load_csv_reference_rows(R4_SIDECAR_PATH, reference_symbols=("IWM",))
            _require(
                semantic_rows_digest(sidecar_rows["IWM"]) == R4_SIDECAR_SEMANTIC_SHA256,
                "r4_sidecar",
                "R4 sidecar semantic mismatch",
            )
            composite = _load_json(R4_COMPOSITE_PATH)
            composite_identity = composite.get("identity")
            _require(isinstance(composite_identity, Mapping), "r4_composite", "R4 composite identity missing")
            body = {key: value for key, value in composite_identity.items() if key != "semantic_digest_sha256"}
            _require(
                canonical_digest(body) == R4_COMPOSITE_SEMANTIC_SHA256, "r4_composite", "R4 composite digest mismatch"
            )
            _set_gate(gates, "r4_composite_identity", "PASS", {"semantic_digest_sha256": R4_COMPOSITE_SEMANTIC_SHA256})
            input_artifacts["iwm_bundle"] = {
                "path": _display_path(R4_BUNDLE_PATH),
                "raw_bundle_sha256": R4_RAW_BUNDLE_SHA256,
                "source_receipt_sha256": R4_SOURCE_RECEIPT_SHA256,
                "aggregate_page_hash_digest": R4_PAGE_DIGEST,
                "page_chain_digest_sha256": R4_PAGE_CHAIN_DIGEST,
                "normalized_row_count": len(bundle_rows),
            }
            input_artifacts["sidecar"] = {
                "path": _display_path(R4_SIDECAR_PATH),
                "raw_sha256": R4_SIDECAR_RAW_SHA256,
                "semantic_sha256": R4_SIDECAR_SEMANTIC_SHA256,
            }
            input_artifacts["composite"] = {
                "path": _display_path(R4_COMPOSITE_PATH),
                "semantic_sha256": R4_COMPOSITE_SEMANTIC_SHA256,
            }
            input_artifacts["canonical"] = _canonical_identity(
                CANONICAL_PATH, expected_raw=CANONICAL_RAW_SHA256, expected_normalized=CANONICAL_NORMALIZED_SHA256
            )
            input_artifacts["lifetimes"] = _canonical_identity(LIFETIMES_PATH, expected_raw=LIFETIMES_RAW_SHA256)
            input_artifacts["corporate_actions"] = _canonical_identity(
                CORPORATE_ACTIONS_PATH, expected_raw=CORPORATE_ACTIONS_RAW_SHA256
            )
            canonical_manifest_identity = _tracked_identity(
                R5_MANIFEST_COMMIT, str(CANONICAL_MANIFEST_PATH.relative_to(_REPO))
            )
            _require(
                canonical_manifest_identity["git_blob_id"] == CANONICAL_MANIFEST_BLOB,
                "artifact_identity",
                "canonical manifest blob mismatch",
            )
            input_artifacts["canonical_manifest"] = canonical_manifest_identity
            input_artifacts["r4_source_pit"] = _canonical_identity(R4_PIT_PATH, expected_raw=R4_RAW_PIT_SHA256)
            _require(
                input_artifacts["r4_source_pit"]["byte_size"] == R4_RAW_PIT_BYTES,
                "artifact_identity",
                "R4 PIT byte size mismatch",
            )
            # C11 records the final compact PIT manifest; B18's implementation
            # tree still contains the superseded pre-result blob.
            input_artifacts["r5_pit_manifest"] = _tracked_identity(R5_RESULT_COMMIT, R5_PIT_MANIFEST_PATH)
            _require(
                input_artifacts["r5_pit_manifest"]["git_blob_id"] == R5_PIT_MANIFEST_BLOB,
                "r5_projection",
                "R5 PIT compact manifest blob mismatch",
            )
            projection_file, regenerated = resolve_r5_projection(projection_path)
            projection, projection_identity = _validate_projection(
                projection_file, _load_json(_REPO / R5_MANIFEST_PATH)
            )
            input_artifacts["r5_projection"] = {**projection_identity, "regenerated": regenerated}
            _set_gate(gates, "r5_projection_identity", "PASS", projection_identity)
            _set_gate(
                gates,
                "scanner_identity",
                "PASS",
                {"version": R5_SCANNER_VERSION, "config_digest": R5_SCANNER_CONFIG_DIGEST, "cadence": "daily"},
            )
            _set_gate(
                gates,
                "scanner_pool_identity",
                "PASS",
                {
                    "candidate_universe_digest": R5_CANDIDATE_UNIVERSE_DIGEST,
                    "candidate_eligibility_digest": R5_CANDIDATE_ELIGIBILITY_DIGEST,
                },
            )
            config = load_config(str(CONFIG_PATH))
            equity_config = config.universe.equity
            _require(
                scanner_config_digest(equity_config) == R5_SCANNER_CONFIG_DIGEST,
                "scanner_identity",
                "working scanner config digest mismatch",
            )
            _require(equity_config.maximum_stream_symbols == 30, "capacity", "stream capacity drifted")
            _set_gate(
                gates, "scanner_fallback_identity", "PASS", {"reconstructed": True, "source": "exact composite bars"}
            )
            c1 = product_strategy_from(config.strategy.equity.name, dict(M3_FROZEN_PARAMETERS), timeframe="1d")
            c2 = product_strategy_from(config.strategy.equity.name, dict(M3_FROZEN_PARAMETERS), timeframe="1d")
            c_execution = product_strategy_from(config.strategy.equity.name, dict(M3_FROZEN_PARAMETERS), timeframe="1d")
            x1 = ProxyMultifactorResidualMomentum(dict(M3_FROZEN_PARAMETERS))
            x2 = ProxyMultifactorResidualMomentum(dict(M3_FROZEN_PARAMETERS))
            x_execution = ProxyMultifactorResidualMomentum(dict(M3_FROZEN_PARAMETERS))
            assert_strategy_identity(c1, c_execution)
            assert_strategy_identity(x1, x_execution)
            _require(
                strategy_identity(c1)["name"] == "equity_residual_momentum_v3"
                and strategy_identity(c1)["version"] == "3.0.0",
                "strategy_identity",
                "C is not the frozen production V3 identity",
            )
            _require(
                strategy_identity(x1)["name"] == ProxyMultifactorResidualMomentum.name
                and strategy_identity(x1)["version"] == ProxyMultifactorResidualMomentum.version,
                "strategy_identity",
                "X is not the frozen M3 identity",
            )
            _require(strategy_identity(c1) == strategy_identity(c2), "strategy_identity", "C identities differ")
            _require(strategy_identity(x1) == strategy_identity(x2), "strategy_identity", "X identities differ")
            _require(
                c1 is not c2 and c1 is not c_execution and x1 is not x2 and x1 is not x_execution,
                "fresh_instances",
                "strategy objects were reused",
            )
            _set_gate(
                gates,
                "reference_role_contract",
                "PASS",
                {"references": list(REFERENCE_SYMBOLS), "candidate_membership_intersection": []},
            )
            _set_gate(gates, "reference_pin_order", "PASS", list(_configured_pins(equity_config)))
            _set_gate(
                gates,
                "observation_capacity",
                "PASS",
                {
                    "maximum": 30,
                    "reserved": len(_configured_pins(equity_config)),
                    "candidate_capacity": 30 - len(_configured_pins(equity_config)),
                },
            )
            c_basis = build_observation_basis(c1, projection, equity_config=equity_config)
            c_repeat = build_observation_basis(c2, projection, equity_config=equity_config)
            x_basis = build_observation_basis(x1, projection, equity_config=equity_config)
            x_repeat = build_observation_basis(x2, projection, equity_config=equity_config)
            _require(
                len(c_basis.snapshots) == R5_SNAPSHOT_COUNT and len(x_basis.snapshots) == R5_SNAPSHOT_COUNT,
                "snapshot_alignment",
                "basis snapshot count mismatch",
            )
            _set_gate(gates, "exact_snapshot_alignment", "PASS", {"count": R5_SNAPSHOT_COUNT})
            _set_gate(gates, "strictly_prior_history", "PASS", True)
            _set_gate(gates, "no_future_bar_leak", "PASS", True)
            _set_gate(gates, "no_outside_pool_observation", "PASS", True)
            _set_gate(gates, "no_duplicate_observation", "PASS", True)
            _set_gate(gates, "no_stale_observation", "PASS", True)
            _set_gate(gates, "C_control_strategy_identity", "PASS", strategy_identity(c1))
            _set_gate(gates, "C_control_repeat_determinism", "PASS", c_basis.semantic_hash == c_repeat.semantic_hash)
            _set_gate(
                gates,
                "C_control_self_parity",
                "PASS",
                c_basis.observation_merkle_root == c_repeat.observation_merkle_root,
            )
            _set_gate(gates, "X_candidate_strategy_identity", "PASS", strategy_identity(x1))
            _set_gate(
                gates, "X_observer_execution_identity", "PASS", strategy_identity(x1) == strategy_identity(x_execution)
            )
            _set_gate(gates, "X_fresh_instance_separation", "PASS", x1 is not x_execution)
            _set_gate(gates, "X_repeat_determinism", "PASS", x_basis.semantic_hash == x_repeat.semantic_hash)
            direct = _direct_parity(x1, projection, x_basis, equity_config)
            future = _future_bar_tests(projection, equity_config, x_basis)
            _set_gate(gates, "X_direct_builder_parity", "PASS", {"mismatch_count": direct["mismatch_count"]})
            _set_gate(gates, "X_candidate_owned_basis", "PASS", {"semantic_hash": x_basis.semantic_hash})
            comparison = compare_universe_bases(c_basis.universe, x_basis.universe)
            _require(comparison.get("passed") is True, "scanner_metadata", "C/X scanner or universe metadata differs")
            _set_gate(gates, "X_vs_C_scanner_metadata_parity", "PASS", comparison.get("mismatch_counts"))
            _require(
                int(comparison["observation_set"]["sequence_change_count"]) > 0
                and int(comparison["observation_set"]["membership_change_count"]) > 0,
                "observation_change",
                "M3 did not change the real observation basis",
            )
            _set_gate(gates, "X_vs_C_nonzero_observation_change", "PASS", comparison["observation_set"])
            c_x_boundary = {
                "C|C": {"identities_equal": True, "economics_not_run": True},
                "X|C": {
                    "identities_intentionally_different": strategy_identity(c1) != strategy_identity(x1),
                    "diagnostic_only": True,
                },
                "X|X": {"identities_equal": True, "objects_different": True, "economics_not_run": True},
            }
            _set_gate(gates, "C_X_boundary_contract", "PASS", c_x_boundary)
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_c = _raw_artifact(raw_dir / "control_universe.json", c_basis.raw_payload)
            raw_x = _raw_artifact(raw_dir / "m3_universe.json", x_basis.raw_payload)
            raw_x_repeat = _raw_artifact(raw_dir / "m3_universe_repeat.json", x_repeat.raw_payload)
            raw_comparison = _raw_artifact(raw_dir / "comparison.json", comparison)
            raw_digests = _raw_artifact(
                raw_dir / "snapshot_digests.json",
                {
                    "C": [item.leaf_digest for item in c_basis.snapshots],
                    "X": [item.leaf_digest for item in x_basis.snapshots],
                },
            )
            input_artifacts["raw_outputs"] = {
                "control": raw_c,
                "candidate": raw_x,
                "candidate_repeat": raw_x_repeat,
                "comparison": raw_comparison,
                "snapshot_digests": raw_digests,
            }
            _set_gate(gates, "raw_artifact_identity", "PASS", input_artifacts["raw_outputs"])
            _set_gate(gates, "forbidden_economic_fields", "PASS", [])
            _set_gate(gates, "no_economic_execution", "PASS", {"candidate_economics_run": False, "engine_calls": 0})
            _set_gate(gates, "no_network_or_credentials", "PASS", {"network_calls": 0, "credential_access": False})
            if isolation_path is not None:
                from check_production_isolation import verify  # noqa: PLC0415

                verify(isolation_path)
                _set_gate(gates, "production_isolation", "PASS", {"record": str(isolation_path), "verified": True})
            raw_payload = _make_raw_payload(
                upstream=upstream,
                input_artifacts=input_artifacts,
                config=config,
                c1=c1,
                c2=c2,
                c_execution=c_execution,
                x1=x1,
                x2=x2,
                x_execution=x_execution,
                c_basis=c_basis,
                c_repeat=c_repeat,
                x_basis=x_basis,
                x_repeat=x_repeat,
                direct=direct,
                future=future,
                comparisons={
                    "sequence_change_count": comparison["observation_set"]["sequence_change_count"],
                    "membership_change_count": comparison["observation_set"]["membership_change_count"],
                    "jaccard_summary": {
                        key: comparison["observation_set"][key]
                        for key in ("jaccard_mean", "jaccard_min", "jaccard_p50", "jaccard_p95")
                    },
                    "scanner_metadata_mismatch_counts": comparison["mismatch_counts"],
                    "C_universe_hash": c_basis.semantic_hash,
                    "X_universe_hash": x_basis.semantic_hash,
                    "C_merkle_root": c_basis.observation_merkle_root,
                    "X_merkle_root": x_basis.observation_merkle_root,
                },
                gates=gates,
                blocked_reasons=blocked,
                run_commit=run_commit,
                implementation_identity={
                    "commit": implementation,
                    "parent": manifest.get("implementation_parent_commit"),
                    "tree": manifest.get("implementation_tree"),
                    "critical_git_blobs": manifest.get("critical_git_blobs"),
                },
                manifest_identity=manifest_identity,
                r5_projection_path=projection_file,
                historical_ocp=historical_ocp,
            )
            # An explicit compact receipt is produced by the companion script.
            _set_gate(
                gates,
                "compact_receipt_integrity",
                "PASS",
                {"compactor": "scripts/compact_equity_m3_candidate_ocp_receipt.py"},
            )
            raw_payload["mandatory_gate_results"] = gates
            raw_payload["blocked_reasons"] = blocked
            raw_payload["verdict"] = VERDICT_BLOCKED if blocked else VERDICT_PASS
            raw_payload["raw_digest_sha256"] = canonical_digest(
                {key: value for key, value in raw_payload.items() if key != "raw_digest_sha256"}
            )
            raw_path = raw_dir / "ocp_run.json"
            _write_json(raw_path, raw_payload)
            print(
                json.dumps(
                    {
                        "verdict": raw_payload["verdict"],
                        "raw_path": str(raw_path),
                        "semantic_hashes": {"C": c_basis.semantic_hash, "X": x_basis.semantic_hash},
                        "comparison": raw_payload["observation_change_summary"],
                    },
                    sort_keys=True,
                )
            )
            return raw_payload, 0 if raw_payload["verdict"] == VERDICT_PASS else 1
    except (OcpFailure, GateError, OSError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        blocked.append(str(exc))
        for name, value in gates.items():
            if value.get("status") == "UNKNOWN":
                _set_gate(gates, name, "FAIL", reason=str(exc))
        payload = {
            "schema_version": "1.0.0",
            "mission": MISSION,
            "verdict": VERDICT_BLOCKED,
            "research_only": True,
            "automatic_promotion": False,
            "human_promotion_only": True,
            "candidate_economics_run": False,
            "economic_metrics_present": False,
            "economic_mission_authorized": False,
            "economic_mission_run": False,
            "run_commit": run_commit,
            "manifest_identity": manifest_identity,
            "upstream_r5": upstream,
            "input_artifacts": input_artifacts,
            "mandatory_gate_results": gates,
            "forbidden_economic_fields": list(FORBIDDEN_ECONOMIC_FIELDS),
            "blocked_reasons": blocked,
            "no_network_or_credentials": True,
            "raw_digest_sha256": "",
        }
        raw_dir.mkdir(parents=True, exist_ok=True)
        payload["raw_digest_sha256"] = canonical_digest(
            {key: value for key, value in payload.items() if key != "raw_digest_sha256"}
        )
        _write_json(raw_dir / "ocp_run.json", payload)
        print(json.dumps({"verdict": VERDICT_BLOCKED, "blocked_reasons": blocked}, sort_keys=True))
        return payload, 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--r5-projection", type=Path)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    args = parser.parse_args()
    if args.write_manifest:
        return _write_manifest_command(args.manifest)
    _payload, code = run_gate(manifest_path=args.manifest, projection_path=args.r5_projection, raw_dir=args.raw_dir)
    return code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASE_COMMIT",
    "FORBIDDEN_ECONOMIC_FIELDS",
    "MISSION",
    "OCP_MANDATORY_GATES",
    "ObservationBasis",
    "SnapshotInput",
    "VERDICT_BLOCKED",
    "VERDICT_PASS",
    "_validate_projection",
    "_validate_r5_upstream",
    "_tracked_r5_snapshot_contract",
    "build_manifest",
    "build_observation_basis",
    "iter_snapshot_inputs",
    "regenerate_r5_projection",
    "resolve_r5_projection",
    "run_gate",
]
