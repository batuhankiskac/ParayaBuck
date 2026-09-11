#!/usr/bin/env python3
"""R46 v2 research execution contract.

The research runner has two deliberately separate surfaces:

* ``SharedAccountLedger`` is a TEST_ONLY accounting harness.  It is useful for
  contract tests, but can never produce a native-ready attestation.
* ``run_native_adapter`` is the production-shaped seam.  It validates a
  ``MarketDataset`` and sends the dual-series bars and explicit corporate
  actions through one ``PortfolioBacktestEngine`` and one account.

No function in this module enables PAPER/LIVE or promotion.  Economic execution
requires a pinned on-disk PRE_RUN_OK receipt and the one-shot reservation
protocol; the native smoke uses only deterministic synthetic prices.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import random
import sys
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
    PortfolioOrder,
    RebalanceRecord,
)
from parayabuck.backtest.terminal_aware_engine import TerminalAwareResearchEngine
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import (
    CorporateAction,
    CorporateActionType,
    DatasetLineage,
    MarketDataset,
)
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.idempotency import IdempotencyKeyFactory
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

PRE_RUN_OK = "PRE_RUN_OK"
PRE_RUN_RECEIPT_SCHEMA = "r46-v2-preflight-receipt-v2"
RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
STATUS = "TEST_ONLY_SYNTHETIC_LEDGER"
NATIVE_LEDGER_SCHEMA = "r46-v2-native-ledger-v2"
NATIVE_SMOKE_STATUS = "NATIVE_ADAPTER_SMOKE"
TEST_NATIVE_STRATEGY = "r46_native_smoke"
ECONOMIC_RUN_SCHEMA = "r46-v2-economic-run-v1"
ECONOMIC_LEDGER_STATUS = "R46_V2_ECONOMIC_RUN"
COST_MULTIPLIERS = (1.0, 2.0, 3.0)
BOOTSTRAP_REPETITIONS = 10_000
BOOTSTRAP_BLOCK = 5
BOOTSTRAP_SEED = 4601
PERMUTATION_REPETITIONS = 10_000
PERMUTATION_SEED = 4602
PLACEBO_SHIFT_SESSIONS = 10
PLACEBO_RANDOM_SEED = 4603
R46_HISTORICAL_CUTOFF = "2026-07-31T23:59:59Z"
POLICY = "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
ELIGIBILITY = "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
SAME_DAY = False
# Keep the acquisition names available at the execution boundary as aliases;
# the values are frozen here so scheduling cannot drift with source metadata.
RESULT_AVAILABILITY_POLICY = POLICY
RESULT_SIGNAL_ELIGIBILITY = ELIGIBILITY
EXPECTED_EVENT_COUNT = 127
NEW_YORK = ZoneInfo("America/New_York")
V5_INCUMBENT_RELATIVE_PATHS = {
    "bars": Path("data/historical/equity_pit_symmetric_2016_2026_v5.csv"),
    "lifetimes": Path("data/historical/equity_entity_lifetimes_full_v5.csv"),
    "corporate_actions": Path("data/historical/equity_survivorship_full_corporate_actions_v5.csv"),
    "bars_manifest": Path("data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json"),
    "corporate_actions_manifest": Path("data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json"),
    "seam": Path("scripts/run_symmetric_v5_validation.py"),
}
STATISTIC_IDS = (
    "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL",
    "R46_INCUMBENT_COMPARISON",
    "R46_TIMING_PLACEBO",
    "R46_COST_2X",
    "R46_COST_3X",
    "R46_CAPACITY_SUPPRESSION",
    "R46_MISSINGNESS",
)
INFERENTIAL_STATISTIC_IDS = (
    STATISTIC_IDS[0],
    STATISTIC_IDS[1],
    STATISTIC_IDS[3],
    STATISTIC_IDS[4],
)
FROZEN_V3_PARAMETERS: dict[str, object] = {
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
ONE_SHOT_RESERVED = "RESERVED"
ONE_SHOT_CONSUMED = "CONSUMED"
ONE_SHOT_ABORTED_CONSUMED = "ABORTED_CONSUMED"
_EPS = 1e-9
_R46_ENGINE_AUDIT_HISTORY: dict[int, tuple[tuple[int, float, float], ...]] = {}
_R46_ENGINE_SNAPSHOT_AUDIT: dict[int, tuple[dict[str, object], ...]] = {}
_R46_ENGINE_OVERLAY_TARGETS: dict[int, dict[datetime, tuple[tuple[str, float], ...]]] = {}
_HEX64 = set("0123456789abcdef")

DEFAULT_CAPS: dict[str, float | int] = {
    "equity_gross_max": 0.55,
    "combined_gross_max": 0.60,
    "combined_cash_min": 0.40,
    "total_positions_max": 6,
}


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _bar_source_row_hash(bar: Any) -> str:
    return hashlib.sha256(
        _canonical_bytes(
            {
                "timestamp": bar.timestamp.isoformat(),
                "symbol": str(bar.symbol),
                "timeframe": str(bar.timeframe),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
                "market": str(bar.market),
            }
        )
    ).hexdigest()


def _canonical_hash(payload: Mapping[str, Any], excluded: str, *, nested: bool = False) -> str:
    value = json.loads(json.dumps(dict(payload), sort_keys=True, ensure_ascii=False))
    if nested and isinstance(value.get("integrity"), dict):
        value["integrity"].pop(excluded, None)
    else:
        value.pop(excluded, None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _receipt_source_path(path: Path, *, repo_root: Path, test_only: bool) -> str:
    """Serialize a source pin without fabricating a path under a fixture root."""

    resolved = path.resolve()
    if test_only:
        return str(resolved)
    try:
        return str(resolved.relative_to(repo_root.resolve()))
    except ValueError as exc:
        raise RuntimeError("BLOCKED_V2_SOURCE_PATH") from exc


def _finite_number(value: object, field: str) -> float:
    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_not_numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field}_not_finite")
    return number


def _aware_timestamp(value: object, field: str) -> str:
    if isinstance(value, datetime):
        timestamp = value
    else:
        try:
            timestamp = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}_invalid") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"{field}_must_be_timezone_aware")
    return timestamp.isoformat()


def _session_date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_invalid") from exc


def _schedule_timestamp(value: object, field: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}_must_be_timezone_aware")
    return parsed


@lru_cache(maxsize=64)
def _nyse_valid_sessions(start: date, end: date) -> frozenset[date]:
    """Return the pinned NYSE regular-session dates for a bounded window."""

    try:
        import pandas_market_calendars as mcal
    except ImportError as exc:
        raise ValueError("nyse_calendar_dependency_missing") from exc
    if end < start:
        return frozenset()
    try:
        schedule = mcal.get_calendar("NYSE").schedule(start_date=start, end_date=end)
    except Exception as exc:
        raise ValueError("nyse_calendar_schedule_unavailable") from exc
    return frozenset(timestamp.date() for timestamp in schedule.index)


def _regular_open_timestamp(session: date, value: object) -> datetime:
    """Validate a calendar open against NYSE 09:30 America/New_York DST rules."""

    parsed = _schedule_timestamp(value, "regular_open_timestamp")
    expected_local = datetime.combine(session, time(9, 30), tzinfo=NEW_YORK)
    expected_offset = expected_local.utcoffset()
    if expected_offset is None or parsed.utcoffset() not in {timedelta(0), expected_offset}:
        raise ValueError("regular_open_timestamp_offset_invalid")
    expected_utc = expected_local.astimezone(UTC)
    if parsed.astimezone(UTC) != expected_utc:
        raise ValueError("regular_open_schedule_mismatch")
    return expected_utc


def schedule_r46_holding(
    *,
    available_at: str | datetime,
    auction_session_date: str | date | None = None,
    regular_sessions: Sequence[Mapping[str, object]],
    availability_policy: str,
    eligibility_policy: str,
    same_day: bool,
    published_at: str | datetime | None = None,
) -> dict[str, object]:
    """Resolve entry/exit from the validated availability policy.

    ``published_at`` is accepted only as evidence and is never used to move
    eligibility.  The entry is the first regular open strictly after
    ``available_at``; the exit is five regular opens after that entry.
    """

    if availability_policy != POLICY:
        raise ValueError("r46_availability_policy_mismatch")
    if eligibility_policy != ELIGIBILITY:
        raise ValueError("r46_eligibility_policy_mismatch")
    if same_day is not SAME_DAY:
        raise ValueError("r46_same_day_policy_mismatch")
    available = _schedule_timestamp(available_at, "available_at").astimezone(UTC)
    if published_at is not None:
        # Parse for schema hygiene, but deliberately do not compare or use it
        # for entry selection.  Actual release time cannot advance eligibility.
        _schedule_timestamp(published_at, "published_at")
    auction_date = (
        _session_date(auction_session_date, "auction_session_date") if auction_session_date is not None else None
    )
    if not regular_sessions:
        raise ValueError("regular_session_calendar_empty")
    raw_rows: list[tuple[date, datetime]] = []
    seen: set[date] = set()
    for row in regular_sessions:
        session = _session_date(row.get("session_date"), "session_date")
        if session in seen:
            raise ValueError("duplicate_regular_session")
        seen.add(session)
        raw_rows.append((session, _regular_open_timestamp(session, row.get("regular_open_timestamp"))))
    valid_sessions = _nyse_valid_sessions(
        min(session for session, _ in raw_rows), max(session for session, _ in raw_rows)
    )
    if any(session not in valid_sessions for session, _ in raw_rows):
        raise ValueError("regular_session_not_nyse")
    parsed = sorted(raw_rows, key=lambda item: item[1])
    if any(previous[1] >= current[1] for previous, current in zip(parsed, parsed[1:])):
        raise ValueError("regular_session_open_order_invalid")
    after = [(session, opening) for session, opening in parsed if opening > available]
    if len(after) < 6:
        raise ValueError("sixth_regular_open_missing")
    entry_session, entry_open = after[0]
    exit_session, exit_open = after[5]
    return {
        "auction_session_date": auction_date.isoformat() if auction_date is not None else None,
        "available_at": available.isoformat(),
        "entry_session_date": entry_session.isoformat(),
        "entry_open_timestamp": entry_open.isoformat(),
        "exit_session_date": exit_session.isoformat(),
        "exit_open_timestamp": exit_open.isoformat(),
        "holding_intervals": 5,
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def _resolve_pinned_path(value: object, base: Path, *, root: Path | None = None) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("pinned_path_missing")
    candidate = Path(value)
    containment_root = (root or base).resolve()
    if not candidate.is_absolute():
        candidate = containment_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(containment_root)
    except ValueError as exc:
        raise ValueError("pinned_path_outside_root") from exc
    if not resolved.is_file():
        raise ValueError("pinned_path_missing")
    return resolved


def _canonical_artifact_hash(path: Path, field: str = "manifest_sha256") -> str:
    payload = _load_json(path)
    integrity = payload.get("integrity")
    if isinstance(integrity, Mapping) and isinstance(integrity.get(field), str):
        declared = str(integrity[field])
        computed = _canonical_hash(payload, field, nested=True)
    elif isinstance(payload.get(field), str):
        declared = str(payload[field])
        computed = _canonical_hash(payload, field)
    else:
        raise ValueError("artifact_canonical_hash_missing")
    if declared != computed:
        raise ValueError("artifact_canonical_hash_invalid")
    return computed


def _canonical_self_hash(path: Path) -> str:
    """Validate a manifest/artifact using whichever frozen self-hash it declares."""

    payload = _load_json(path)
    integrity = payload.get("integrity")
    if not isinstance(integrity, Mapping):
        raise ValueError("artifact_integrity_missing")
    for hash_field in ("manifest_sha256", "artifact_sha256", "receipt_sha256"):
        if isinstance(integrity.get(hash_field), str):
            return _canonical_artifact_hash(path, hash_field)
    raise ValueError("artifact_canonical_hash_missing")


def _valid_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


_GATE_BOUNDARY: dict[str, object] = {
    "research_only": True,
    "read_only": True,
    "economic_execution": False,
    "economic_run": False,
    "economic_outcomes_accessed": False,
    "outcome_artifacts_present": False,
    "paper_live_untouched": True,
    "paper_or_live_activation": False,
    "integration_candidate": False,
    "promotion": False,
    "auto_promotion": False,
    "no_auto_promotion": True,
}


def _validate_pre_run_receipt(path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    receipt = _load_json(path)
    if receipt.get("schema_version") != PRE_RUN_RECEIPT_SCHEMA:
        raise ValueError("preflight_receipt_schema")
    if receipt.get("status") != PRE_RUN_OK:
        raise ValueError("preflight_receipt_status")
    if receipt.get("research_id") != RESEARCH_ID or receipt.get("candidate_id") != "R46":
        raise ValueError("preflight_receipt_identity")
    if any(receipt.get(field) is not expected for field, expected in _GATE_BOUNDARY.items()):
        raise ValueError("preflight_receipt_boundary")

    integrity = receipt.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("receipt_sha256") != _canonical_hash(
        receipt, "receipt_sha256", nested=True
    ):
        raise ValueError("preflight_receipt_self_hash")

    manifest_reference = receipt.get("manifest_path", receipt.get("governance_manifest_path"))
    package_reference = receipt.get("package_path", receipt.get("data_package_manifest_path"))
    containment_root = (repo_root or path.parent).resolve()
    manifest_path = _resolve_pinned_path(manifest_reference, path.parent, root=containment_root)
    package_path = _resolve_pinned_path(package_reference, path.parent, root=containment_root)
    manifest_hash = _canonical_artifact_hash(manifest_path)
    package_hash = _canonical_artifact_hash(package_path)
    manifest = _load_json(manifest_path)
    package = _load_json(package_path)
    if manifest.get("schema_version") != "r46-governance-manifest-v2":
        raise ValueError("preflight_manifest_schema")
    if manifest.get("research_id") != RESEARCH_ID or manifest.get("candidate_id") != "R46":
        raise ValueError("preflight_manifest_identity")
    package_schema = package.get("schema") or package.get("schema_version")
    if package_schema != "r46-treasury-auction-unwind-data-package-v2":
        raise ValueError("preflight_package_schema")
    if package.get("research_id") != RESEARCH_ID or package.get("candidate_id") != "R46":
        raise ValueError("preflight_package_identity")
    declared_manifest_hash = (
        receipt.get("manifest_sha256")
        or receipt.get("manifest_canonical_sha256")
        or receipt.get("governance_manifest_sha256")
    )
    declared_package_hash = (
        receipt.get("package_sha256")
        or receipt.get("package_canonical_sha256")
        or receipt.get("data_package_manifest_sha256")
    )
    if declared_manifest_hash != manifest_hash or declared_package_hash != package_hash:
        raise ValueError("preflight_canonical_hash_crosslink")
    for receipt_field, actual in (
        ("manifest_file_sha256", _sha256_file(manifest_path)),
        ("package_file_sha256", _sha256_file(package_path)),
    ):
        declared = receipt.get(receipt_field)
        if declared is not None and declared != actual:
            raise ValueError("preflight_file_hash_crosslink")
    return receipt


def _verify_persisted_preflight(
    *,
    manifest_path: Path,
    package_path: Path,
    receipt: Mapping[str, Any],
    repo_root: Path,
    preflight_receipt_path: Path | None = None,
) -> Mapping[str, Any]:
    """Recompute the complete preflight and bind the on-disk receipt to it.

    A receipt self-hash proves only that its bytes were not changed after they
    were written.  The independent preflight verifier is authoritative for
    readiness; its freshly rebuilt receipt must match the persisted receipt
    exactly before the economic runner can reserve the one-shot state.
    """

    try:
        from scripts.research import r46_v2_preflight_2026_09_10 as preflight

        verifier = getattr(preflight, "verify_v2_manifest", None)
        if not callable(verifier):
            raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
        freeze_commit = receipt.get("freeze_commit")
        evidence_kwargs: dict[str, object] = {}
        for receipt_names, verifier_name in (
            (("pit_source_attestation_path",), "pit_source_attestation_path"),
            (("native_ledger_attestation_path", "native_attestation_path"), "native_ledger_attestation_path"),
            (("independent_software_review_path",), "independent_software_review_path"),
        ):
            for receipt_name in receipt_names:
                candidate = receipt.get(receipt_name)
                if isinstance(candidate, str) and candidate.strip():
                    evidence_kwargs[verifier_name] = Path(candidate)
                    break
        result = verifier(
            manifest_path,
            data_package_manifest_path=package_path,
            freeze_commit=freeze_commit if isinstance(freeze_commit, str) else None,
            repo_root=repo_root,
            preflight_receipt_path=preflight_receipt_path,
            **evidence_kwargs,
        )
    except (ImportError, OSError, TypeError, ValueError, RuntimeError) as exc:
        if isinstance(exc, RuntimeError) and str(exc) == "BLOCKED_V2_PRE_RUN_GATE":
            raise
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE") from exc
    if not isinstance(result, Mapping) or result.get("status") != PRE_RUN_OK:
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    recomputed = result.get("preflight_receipt")
    if not isinstance(recomputed, Mapping) or dict(recomputed) != dict(receipt):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    if result.get("source_commit") != receipt.get("source_commit") or result.get("freeze_commit") != receipt.get(
        "freeze_commit"
    ):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    return result


def require_pre_run_ok(gate: Path | str | Mapping[str, object] | None) -> None:
    """Require a pinned on-disk PRE_RUN_OK receipt; mappings are forbidden."""

    if isinstance(gate, Mapping) or gate is None:
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    try:
        _validate_pre_run_receipt(Path(gate).resolve())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE") from None


def _one_shot_sidecar(path: Path, state: str) -> Path:
    return path.with_name(f"{path.name}.{state.lower()}.json")


def _load_one_shot(path: Path) -> dict[str, Any]:
    state = _load_json(path)
    if state.get("schema_version") != "r46-one-shot-state-v2":
        raise RuntimeError("ONE_SHOT_STATE_INVALID")
    if state.get("research_id") != RESEARCH_ID or state.get("candidate_id") != "R46":
        raise RuntimeError("ONE_SHOT_STATE_INVALID")
    if state.get("status") != "SEALED_UNUSED":
        raise RuntimeError("ONE_SHOT_STATE_INVALID")
    integrity = state.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("artifact_sha256") != _canonical_hash(
        state, "artifact_sha256", nested=True
    ):
        raise RuntimeError("ONE_SHOT_STATE_HASH_INVALID")
    return state


def _exclusive_bytes(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _exclusive_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    _exclusive_bytes(path, encoded)


def _load_transition(
    path: Path,
    state_path: Path,
    *,
    require_crosslinks: bool = True,
) -> dict[str, Any]:
    transition = _load_json(path)
    if transition.get("schema_version") != "r46-one-shot-transition-v2":
        raise RuntimeError("ONE_SHOT_TRANSITION_INVALID")
    expected_status_by_path = {
        _one_shot_sidecar(state_path, "reserved"): ONE_SHOT_RESERVED,
        _one_shot_sidecar(state_path, "consumed"): ONE_SHOT_CONSUMED,
        _one_shot_sidecar(state_path, "aborted_consumed"): ONE_SHOT_ABORTED_CONSUMED,
    }
    expected_status = expected_status_by_path.get(path)
    if expected_status is None or transition.get("status") != expected_status:
        raise RuntimeError("ONE_SHOT_TRANSITION_STATUS_INVALID")
    if transition.get("state_path") != str(state_path):
        raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_INVALID")
    if transition.get("state_sha256") != _sha256_file(state_path):
        raise RuntimeError("ONE_SHOT_TRANSITION_STATE_HASH_INVALID")
    if transition.get("state_canonical_sha256") != _canonical_artifact_hash(state_path, "artifact_sha256"):
        raise RuntimeError("ONE_SHOT_TRANSITION_STATE_CANONICAL_HASH_INVALID")
    integrity = transition.get("integrity")
    if not isinstance(integrity, Mapping):
        raise RuntimeError("ONE_SHOT_TRANSITION_HASH_INVALID")
    if integrity.get("transition_sha256") != _canonical_hash(transition, "transition_sha256", nested=True):
        raise RuntimeError("ONE_SHOT_TRANSITION_HASH_INVALID")
    for transition_field in ("gate_sha256", "manifest_sha256", "package_sha256", "output_sha256"):
        value = transition.get(transition_field)
        if value is not None and not _valid_hex64(value):
            raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_INVALID")
    if require_crosslinks:
        if expected_status != ONE_SHOT_ABORTED_CONSUMED:
            for transition_field in ("gate_sha256", "manifest_sha256", "package_sha256"):
                if not _valid_hex64(transition.get(transition_field)):
                    raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_REQUIRED")
        if expected_status == ONE_SHOT_CONSUMED:
            if not _valid_hex64(transition.get("output_sha256")):
                raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_REQUIRED")
            output_value = transition.get("output_path")
            receipt_value = transition.get("receipt_path")
            if not isinstance(output_value, str) or not Path(output_value).is_absolute():
                raise RuntimeError("ONE_SHOT_TRANSITION_PATH_INVALID")
            if not isinstance(receipt_value, str) or not Path(receipt_value).is_absolute():
                raise RuntimeError("ONE_SHOT_TRANSITION_PATH_INVALID")
            if Path(receipt_value).parent != Path(output_value):
                raise RuntimeError("ONE_SHOT_TRANSITION_PATH_INVALID")
            reserved_hash = transition.get("reserved_transition_sha256")
            if not _valid_hex64(reserved_hash):
                raise RuntimeError("ONE_SHOT_TRANSITION_CHAIN_INVALID")
            reserved_path = _one_shot_sidecar(state_path, "reserved")
            if not reserved_path.is_file():
                raise RuntimeError("ONE_SHOT_TRANSITION_CHAIN_INVALID")
            reserved_transition = _load_transition(reserved_path, state_path, require_crosslinks=True)
            if reserved_hash != reserved_transition.get("integrity", {}).get("transition_sha256"):
                raise RuntimeError("ONE_SHOT_TRANSITION_CHAIN_INVALID")
    return transition


def one_shot_status(state_path: Path | str, *, require_crosslinks: bool = True) -> str:
    path = Path(state_path).resolve()
    _load_one_shot(path)
    aborted = _one_shot_sidecar(path, "aborted_consumed")
    if aborted.is_file():
        return str(_load_transition(aborted, path, require_crosslinks=require_crosslinks).get("status"))
    consumed = _one_shot_sidecar(path, "consumed")
    if consumed.is_file():
        return str(_load_transition(consumed, path, require_crosslinks=require_crosslinks).get("status"))
    reserved = _one_shot_sidecar(path, "reserved")
    if reserved.is_file():
        return str(_load_transition(reserved, path, require_crosslinks=require_crosslinks).get("status"))
    return "SEALED_UNUSED"


def reserve_one_shot(
    state_path: Path | str,
    *,
    gate_sha256: str | None = None,
    manifest_sha256: str | None = None,
    package_sha256: str | None = None,
    require_crosslinks: bool = True,
) -> dict[str, object]:
    """Atomically create a reservation sidecar without overwriting sealed state."""

    path = Path(state_path).resolve()
    _load_one_shot(path)
    if require_crosslinks and any(not _valid_hex64(value) for value in (gate_sha256, manifest_sha256, package_sha256)):
        raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_REQUIRED")
    if _one_shot_sidecar(path, "consumed").is_file() or _one_shot_sidecar(path, "aborted_consumed").is_file():
        raise RuntimeError("ONE_SHOT_ALREADY_CONSUMED")
    reservation = _one_shot_sidecar(path, "reserved")
    try:
        payload: dict[str, Any] = {
            "schema_version": "r46-one-shot-transition-v2",
            "state_path": str(path),
            "state_sha256": _sha256_file(path),
            "state_canonical_sha256": _canonical_artifact_hash(path, "artifact_sha256"),
            "status": ONE_SHOT_RESERVED,
            "gate_sha256": gate_sha256,
            "manifest_sha256": manifest_sha256,
            "package_sha256": package_sha256,
            "integrity": {"transition_sha256": ""},
        }
        payload["integrity"]["transition_sha256"] = _canonical_hash(payload, "transition_sha256", nested=True)
        _exclusive_json(reservation, payload)
    except FileExistsError:
        raise RuntimeError("ONE_SHOT_ALREADY_RESERVED") from None
    return {"status": ONE_SHOT_RESERVED, "state_path": str(path)}


def consume_one_shot(
    state_path: Path | str,
    output_sha256: str,
    *,
    gate_sha256: str | None = None,
    manifest_sha256: str | None = None,
    package_sha256: str | None = None,
    output_path: Path | None = None,
    receipt_path: Path | None = None,
    require_crosslinks: bool = True,
) -> dict[str, object]:
    """Atomically record CONSUMED and the immutable output hash."""

    path = Path(state_path).resolve()
    _load_one_shot(path)
    if not _valid_hex64(output_sha256):
        raise ValueError("output_sha256_invalid")
    if require_crosslinks:
        if any(not _valid_hex64(value) for value in (gate_sha256, manifest_sha256, package_sha256)):
            raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_REQUIRED")
        if output_path is None or receipt_path is None:
            raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_REQUIRED")
        if not output_path.is_absolute() or not receipt_path.is_absolute() or receipt_path.parent != output_path:
            raise RuntimeError("ONE_SHOT_TRANSITION_PATH_INVALID")
    consumed = _one_shot_sidecar(path, "consumed")
    if consumed.is_file():
        raise RuntimeError("ONE_SHOT_ALREADY_CONSUMED")
    reserved_path = _one_shot_sidecar(path, "reserved")
    if not reserved_path.is_file():
        raise RuntimeError("ONE_SHOT_RESERVATION_REQUIRED")
    reserved_payload = _load_transition(reserved_path, path, require_crosslinks=require_crosslinks)
    for name, value in (
        ("gate_sha256", gate_sha256),
        ("manifest_sha256", manifest_sha256),
        ("package_sha256", package_sha256),
    ):
        if value is not None and reserved_payload.get(name) not in {None, value}:
            raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_INVALID")
    gate_sha256 = gate_sha256 if gate_sha256 is not None else cast(str | None, reserved_payload.get("gate_sha256"))
    manifest_sha256 = (
        manifest_sha256 if manifest_sha256 is not None else cast(str | None, reserved_payload.get("manifest_sha256"))
    )
    package_sha256 = (
        package_sha256 if package_sha256 is not None else cast(str | None, reserved_payload.get("package_sha256"))
    )
    try:
        payload: dict[str, Any] = {
            "schema_version": "r46-one-shot-transition-v2",
            "state_path": str(path),
            "state_sha256": _sha256_file(path),
            "state_canonical_sha256": _canonical_artifact_hash(path, "artifact_sha256"),
            "status": ONE_SHOT_CONSUMED,
            "gate_sha256": gate_sha256,
            "manifest_sha256": manifest_sha256,
            "package_sha256": package_sha256,
            "output_sha256": output_sha256,
            "output_path": str(output_path.resolve()) if output_path is not None else None,
            "receipt_path": str(receipt_path.resolve()) if receipt_path is not None else None,
            "reserved_transition_sha256": reserved_payload["integrity"]["transition_sha256"],
            "integrity": {"transition_sha256": ""},
        }
        payload["integrity"]["transition_sha256"] = _canonical_hash(payload, "transition_sha256", nested=True)
        _exclusive_json(consumed, payload)
    except FileExistsError:
        raise RuntimeError("ONE_SHOT_ALREADY_CONSUMED") from None
    return {"status": ONE_SHOT_CONSUMED, "state_path": str(path), "output_sha256": output_sha256}


def _published_payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = (json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_published_run_receipt(
    receipt_path: Path,
    state_path: Path,
    consumed_payload: Mapping[str, Any],
) -> bool:
    """Validate the immutable receipt and all logical/physical output links."""

    try:
        receipt = _load_json(receipt_path)
        if receipt.get("schema_version") != f"{ECONOMIC_RUN_SCHEMA}:receipt":
            return False
        if receipt.get("receipt_sha256") != _canonical_hash(receipt, "receipt_sha256"):
            return False
        one_shot = receipt.get("one_shot")
        if not isinstance(one_shot, Mapping) or one_shot.get("status") != ONE_SHOT_CONSUMED:
            return False
        state_reference = one_shot.get("state_path")
        if not isinstance(state_reference, str):
            return False
        resolved_state_reference = (
            Path(state_reference).resolve()
            if Path(state_reference).is_absolute()
            else (REPO / state_reference).resolve()
        )
        if resolved_state_reference != state_path.resolve():
            return False
        outputs = receipt.get("outputs")
        if not isinstance(outputs, Mapping):
            return False
        output_path_value = consumed_payload.get("output_path")
        if not isinstance(output_path_value, str) or Path(output_path_value) != receipt_path.parent:
            return False
        output_files = {
            "results_sha256": receipt_path.parent / "results.json",
            "ledger_sha256": receipt_path.parent / "ledger.json",
            "report_sha256": receipt_path.parent / "report.json",
        }
        expected_hashes: dict[str, str] = {}
        for hash_field, output_file in output_files.items():
            if not output_file.is_file():
                return False
            payload = _load_json(output_file)
            expected_hashes[hash_field] = _published_payload_hash(payload)
            if _sha256_file(output_file) != expected_hashes[hash_field] or outputs.get(hash_field) != expected_hashes[hash_field]:
                return False
        bundle_hash = hashlib.sha256(
            _canonical_bytes(
                {
                    "results_sha256": expected_hashes["results_sha256"],
                    "ledger_sha256": expected_hashes["ledger_sha256"],
                    "report_sha256": expected_hashes["report_sha256"],
                }
            )
        ).hexdigest()
        if (
            outputs.get("bundle_sha256") != bundle_hash
            or one_shot.get("output_sha256") != bundle_hash
            or consumed_payload.get("output_sha256") != bundle_hash
        ):
            return False
        for consumed_field, receipt_field in (
            ("gate_sha256", "preflight_receipt_sha256"),
            ("manifest_sha256", "manifest_canonical_sha256"),
            ("package_sha256", "package_canonical_sha256"),
        ):
            if consumed_payload.get(consumed_field) != receipt.get(receipt_field):
                return False
        from scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 import (
            verify_economic_payloads_test_only,
            verify_economic_run_from_files,
        )

        if receipt.get("test_only") is True:
            verify_economic_payloads_test_only(
                _load_json(output_files["results_sha256"]),
                _load_json(output_files["ledger_sha256"]),
                _load_json(output_files["report_sha256"]),
                receipt,
            )
        else:
            gate_reference = receipt.get("preflight_receipt_path")
            if not isinstance(gate_reference, str):
                return False
            gate_path = (REPO / gate_reference).resolve()
            verify_economic_run_from_files(
                REPO,
                output_files["results_sha256"],
                output_files["ledger_sha256"],
                output_files["report_sha256"],
                receipt_path,
                gate_path,
            )
        return (
            outputs.get("bundle_sha256") == bundle_hash
            and one_shot.get("output_sha256") == bundle_hash
            and consumed_payload.get("output_sha256") == bundle_hash
        )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def reconcile_one_shot(
    state_path: Path | str,
    receipt_path: Path | str,
    *,
    require_crosslinks: bool = True,
) -> str:
    """Close a crash window without ever reopening a reserved/consumed run."""

    path = Path(state_path).resolve()
    receipt = Path(receipt_path).resolve()
    _load_one_shot(path)
    aborted = _one_shot_sidecar(path, "aborted_consumed")
    if aborted.is_file():
        return str(_load_transition(aborted, path, require_crosslinks=require_crosslinks).get("status"))
    consumed = _one_shot_sidecar(path, "consumed")
    if not consumed.is_file():
        return one_shot_status(path, require_crosslinks=require_crosslinks)
    transition_valid = True
    try:
        payload = _load_transition(consumed, path, require_crosslinks=require_crosslinks)
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError):
        transition_valid = False
        try:
            payload = _load_json(consumed)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
    declared_receipt = payload.get("receipt_path")
    if (
        transition_valid
        and isinstance(declared_receipt, str)
        and declared_receipt == str(receipt)
        and receipt.is_file()
        and _valid_published_run_receipt(receipt, path, payload)
    ):
        return ONE_SHOT_CONSUMED

    def valid_hash_or_none(field: str) -> str | None:
        value = payload.get(field)
        return cast(str, value) if _valid_hex64(value) else None

    output_path_value = payload.get("output_path")
    if not isinstance(output_path_value, str) or not Path(output_path_value).is_absolute():
        output_path_value = None
    aborted_payload: dict[str, Any] = {
        "schema_version": "r46-one-shot-transition-v2",
        "state_path": str(path),
        "state_sha256": _sha256_file(path),
        "state_canonical_sha256": _canonical_artifact_hash(path, "artifact_sha256"),
        "status": ONE_SHOT_ABORTED_CONSUMED,
        "gate_sha256": valid_hash_or_none("gate_sha256"),
        "manifest_sha256": valid_hash_or_none("manifest_sha256"),
        "package_sha256": valid_hash_or_none("package_sha256"),
        "output_sha256": valid_hash_or_none("output_sha256"),
        "output_path": output_path_value,
        "receipt_path": str(receipt),
        "reserved_transition_sha256": valid_hash_or_none("reserved_transition_sha256"),
        "reconciliation": "consumed_without_valid_published_receipt",
        "source_transition_valid": transition_valid,
        "integrity": {"transition_sha256": ""},
    }
    aborted_payload["integrity"]["transition_sha256"] = _canonical_hash(
        aborted_payload, "transition_sha256", nested=True
    )
    try:
        _exclusive_json(aborted, aborted_payload)
    except FileExistsError:
        pass
    return ONE_SHOT_ABORTED_CONSUMED


@dataclass
class _Holding:
    qty: float
    avg_price: float
    owner: str


class SharedAccountLedger:
    """TEST_ONLY one-account ledger for deterministic contract tests."""

    TEST_ONLY = True

    def __init__(
        self,
        *,
        initial_cash: float = 100_000.0,
        caps: Mapping[str, float | int] | None = None,
    ) -> None:
        self.initial_cash = _finite_number(initial_cash, "initial_cash")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash_must_be_positive")
        configured = dict(DEFAULT_CAPS)
        if caps is not None:
            if dict(caps) != DEFAULT_CAPS:
                raise ValueError("frozen_caps")
            configured.update(caps)
        self.caps = {
            "equity_gross_max": _finite_number(configured["equity_gross_max"], "equity_gross_max"),
            "combined_gross_max": _finite_number(configured["combined_gross_max"], "combined_gross_max"),
            "combined_cash_min": _finite_number(configured["combined_cash_min"], "combined_cash_min"),
            "total_positions_max": int(configured["total_positions_max"]),
        }
        if not 0 <= self.caps["equity_gross_max"] <= self.caps["combined_gross_max"] <= 1:
            raise ValueError("invalid_gross_caps")
        if not 0 <= self.caps["combined_cash_min"] <= 1:
            raise ValueError("invalid_cash_cap")
        if self.caps["total_positions_max"] <= 0:
            raise ValueError("invalid_position_cap")
        self._cash = self.initial_cash
        self._positions: dict[str, _Holding] = {}
        self._prices: dict[str, float] = {}
        self._fees = 0.0
        self._dividend_total = 0.0
        self._fills: list[dict[str, object]] = []
        self._rejected_orders: list[dict[str, object]] = []
        self._decisions: list[dict[str, object]] = []
        self._snapshots: list[dict[str, object]] = []
        self._unfilled_exits: list[dict[str, object]] = []
        self._candidate_event_ids: set[str] = set()
        self._event_records: list[dict[str, object]] = []
        self._dividend_events: list[dict[str, object]] = []
        self._initial_positions: list[dict[str, object]] = []
        self._event_schedules: list[dict[str, object]] = []

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> Mapping[str, Mapping[str, object]]:
        return {
            symbol: {"qty": holding.qty, "avg_price": holding.avg_price, "owner": holding.owner}
            for symbol, holding in sorted(self._positions.items())
        }

    @staticmethod
    def _copy_positions(source: Mapping[str, _Holding]) -> dict[str, _Holding]:
        return {symbol: copy.copy(holding) for symbol, holding in source.items()}

    @staticmethod
    def _is_candidate(row: Mapping[str, object]) -> bool:
        return str(row.get("owner", "")).lower() in {"r46", "candidate"} or row.get("candidate") is True

    def _marked_equity(self, marks: Mapping[str, object] | None = None) -> float:
        if marks is not None:
            for symbol, value in marks.items():
                self._prices[str(symbol)] = _finite_number(value, f"mark:{symbol}")
        return self._staged_equity(self._cash, self._positions, self._prices)

    @staticmethod
    def _staged_equity(cash: float, positions: Mapping[str, _Holding], prices: Mapping[str, float]) -> float:
        total = cash
        for symbol, holding in positions.items():
            price = prices.get(symbol, holding.avg_price)
            if price <= 0 or not math.isfinite(price):
                raise ValueError(f"missing_positive_mark:{symbol}")
            total += holding.qty * price
        if not math.isfinite(total) or total <= 0:
            raise ValueError("equity_not_positive")
        return total

    def _gross(self, marks: Mapping[str, object] | None = None) -> float:
        equity = self._marked_equity(marks)
        return (equity - self._cash) / equity

    def seed_position(self, symbol: str, *, qty: float, price: float, owner: str) -> None:
        symbol = str(symbol).strip()
        owner = str(owner).strip()
        qty = _finite_number(qty, "qty")
        price = _finite_number(price, "price")
        if not symbol or not owner or qty <= 0 or price <= 0:
            raise ValueError("invalid_seed_position")
        if symbol in self._positions:
            raise ValueError("duplicate_seed_position")
        if len(self._positions) >= int(self.caps["total_positions_max"]):
            raise ValueError("total_positions_max")
        notional = qty * price
        if notional > self._cash + _EPS:
            raise ValueError("seed_exceeds_cash")
        self._cash -= notional
        self._prices[symbol] = price
        self._positions[symbol] = _Holding(qty=qty, avg_price=price, owner=owner)
        self._initial_positions.append({"symbol": symbol, "qty": qty, "price": price, "owner": owner})

    @staticmethod
    def _normalize_target(row: Mapping[str, object]) -> dict[str, object]:
        symbol = str(row.get("symbol", "")).strip()
        owner = str(row.get("owner", "")).strip()
        weight = _finite_number(row.get("weight"), "target_weight")
        if not symbol or not owner or weight < 0 or weight > 1:
            raise ValueError("invalid_target_weight")
        return {"symbol": symbol, "weight": weight, "owner": owner, "reason": str(row.get("reason", ""))}

    def plan_decision(
        self,
        *,
        decision_timestamp: datetime | str,
        signal_asof_timestamp: datetime | str,
        target_weights: Sequence[Mapping[str, object]],
        candidate_event_id: str | None = None,
        candidate_active: bool = True,
    ) -> dict[str, object]:
        decision = _aware_timestamp(decision_timestamp, "decision_timestamp")
        signal = _aware_timestamp(signal_asof_timestamp, "signal_asof_timestamp")
        rows = [self._normalize_target(row) for row in target_weights]
        if len({str(row["symbol"]) for row in rows}) != len(rows):
            raise ValueError("duplicate_target_symbol")
        event_id = str(candidate_event_id).strip() if candidate_event_id is not None else None
        candidate_rows = [row for row in rows if self._is_candidate(row)]
        suppression: str | None = None
        if candidate_rows and not candidate_active:
            rows = [row for row in rows if not self._is_candidate(row)]
            suppression = "missing_event_no_catch_up"
        elif candidate_rows and (
            (event_id is not None and event_id in self._candidate_event_ids)
            or any(self._is_candidate({"owner": holding.owner}) for holding in self._positions.values())
        ):
            rows = [row for row in rows if not self._is_candidate(row)]
            suppression = "no_pyramid"
        incumbent_gross = sum(
            _finite_number(target["weight"], "target_weight") for target in rows if not self._is_candidate(target)
        )
        candidate_gross = sum(
            _finite_number(target["weight"], "target_weight") for target in rows if self._is_candidate(target)
        )
        if incumbent_gross > float(self.caps["equity_gross_max"]) + _EPS:
            raise ValueError("incumbent_equity_gross_cap")
        if candidate_gross and (
            incumbent_gross + candidate_gross > float(self.caps["combined_gross_max"]) + _EPS
            or 1.0 - incumbent_gross - candidate_gross < float(self.caps["combined_cash_min"]) - _EPS
        ):
            rows = [row for row in rows if not self._is_candidate(row)]
            suppression = suppression or "combined_gross_or_cash_cap"
        projected_symbols = set(self._positions)
        projected_symbols.update(
            str(target["symbol"]) for target in rows if _finite_number(target["weight"], "target_weight") > _EPS
        )
        if len(projected_symbols) > int(self.caps["total_positions_max"]):
            if any(self._is_candidate(row) for row in rows):
                rows = [row for row in rows if not self._is_candidate(row)]
                suppression = suppression or "total_positions_max"
            else:
                raise ValueError("incumbent_total_positions_max")
        if event_id:
            self._candidate_event_ids.add(event_id)
        ownership: dict[str, list[str]] = {}
        for row in rows:
            ownership.setdefault(str(row["owner"]), []).append(str(row["symbol"]))
        record: dict[str, object] = {
            "decision_timestamp": decision,
            "signal_asof_timestamp": signal,
            "target_weights": rows,
            "suppression_reason": suppression,
            "candidate_event_id": event_id,
            "candidate_active": bool(candidate_active),
            "ownership_attribution": ownership,
        }
        self._decisions.append(record)
        if event_id:
            self._event_records.append(
                {
                    "event_id": event_id,
                    "decision_timestamp": decision,
                    "status": "SUPPRESSED" if suppression else "ACCEPTED",
                    "suppression_reason": suppression,
                }
            )
        return record

    @staticmethod
    def _normalize_fill(fill: Mapping[str, object], timestamp: str) -> dict[str, object]:
        symbol = str(fill.get("symbol", "")).strip()
        side = str(fill.get("side", "")).upper().strip()
        owner = str(fill.get("owner", "")).strip()
        qty = _finite_number(fill.get("qty"), "fill_qty")
        price = _finite_number(fill.get("price"), "fill_price")
        fee = _finite_number(fill.get("fee", 0.0), "fill_fee")
        reference_price = _finite_number(fill.get("reference_price", price), "reference_price")
        slippage_cost = _finite_number(fill.get("slippage_cost", 0.0), "slippage_cost")
        spread_cost = _finite_number(fill.get("spread_cost", 0.0), "spread_cost")
        if (
            not symbol
            or side not in {"BUY", "SELL"}
            or not owner
            or qty <= 0
            or price <= 0
            or reference_price <= 0
            or fee < 0
            or slippage_cost < 0
            or spread_cost < 0
        ):
            raise ValueError("invalid_fill")
        return {
            "timestamp": timestamp,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "fee": fee,
            "owner": owner,
            "decision_timestamp": _aware_timestamp(fill.get("decision_timestamp", timestamp), "decision_timestamp"),
            "client_order_id": str(fill.get("client_order_id", f"{owner}-{symbol}-{side.lower()}")),
            "reference_price": reference_price,
            "slippage_cost": slippage_cost,
            "spread_cost": spread_cost,
        }

    @staticmethod
    def _rejected_record(fill: Mapping[str, object], reason: str) -> dict[str, object]:
        return {
            **dict(fill),
            "notional": _finite_number(fill["qty"], "fill_qty") * _finite_number(fill["price"], "fill_price"),
            "status": "REJECTED",
            "reason": reason,
        }

    @staticmethod
    def _unfilled_exit(fill: Mapping[str, object], reason: str) -> dict[str, object]:
        return {
            "symbol": str(fill["symbol"]),
            "requested_qty": _finite_number(fill["qty"], "fill_qty"),
            "filled_qty": 0.0,
            "reason": reason,
            "owner": str(fill["owner"]),
        }

    def apply_fills(
        self,
        *,
        timestamp: datetime | str,
        fills: Sequence[Mapping[str, object]],
    ) -> tuple[dict[str, object], ...]:
        """Stage all fills first, then commit fills/rejections atomically."""

        fill_timestamp = _aware_timestamp(timestamp, "fill_timestamp")
        normalized = [self._normalize_fill(fill, fill_timestamp) for fill in fills]
        ordered = sorted(
            enumerate(normalized),
            key=lambda pair: (0 if pair[1]["side"] == "SELL" else 1, str(pair[1]["symbol"]), pair[0]),
        )
        cash = self._cash
        positions = self._copy_positions(self._positions)
        prices = dict(self._prices)
        fees = self._fees
        applied: list[dict[str, object]] = []
        rejected: list[dict[str, object]] = []
        unfilled: list[dict[str, object]] = []
        previous_price: float | None = None

        def reject(fill: Mapping[str, object], reason: str) -> None:
            if previous_price is None:
                prices.pop(str(fill["symbol"]), None)
            else:
                prices[str(fill["symbol"])] = previous_price
            rejected.append(self._rejected_record(fill, reason))
            if fill["side"] == "SELL":
                unfilled.append(self._unfilled_exit(fill, reason))

        for _index, fill in ordered:
            symbol = str(fill["symbol"])
            side = str(fill["side"])
            qty = _finite_number(fill["qty"], "fill_qty")
            price = _finite_number(fill["price"], "fill_price")
            fee = _finite_number(fill["fee"], "fill_fee")
            previous_price = prices.get(symbol)
            prices[symbol] = price
            holding = positions.get(symbol)
            if side == "SELL":
                if holding is None:
                    reject(fill, "unfilled_exit")
                    continue
                if holding.owner != str(fill["owner"]):
                    reject(fill, "owner_mismatch")
                    continue
                if qty > holding.qty + _EPS:
                    reject(fill, "unfilled_exit")
                    continue
                cash += qty * price - fee
                fees += fee
                holding.qty -= qty
                if holding.qty <= _EPS:
                    positions.pop(symbol, None)
                applied.append({**fill, "notional": qty * price, "status": "FILLED"})
                continue

            if holding is not None and holding.owner != str(fill["owner"]):
                reject(fill, "mixed_symbol_ownership")
                continue
            if holding is None and len(positions) >= int(self.caps["total_positions_max"]):
                reject(fill, "total_positions_max")
                continue
            current_equity = self._staged_equity(cash, positions, prices)
            projected_cash = cash - qty * price - fee
            projected_equity = current_equity - fee
            if projected_cash < -_EPS:
                reject(fill, "insufficient_cash")
                continue
            if (
                projected_equity <= 0
                or projected_cash < projected_equity * float(self.caps["combined_cash_min"]) - _EPS
            ):
                reject(fill, "combined_cash_min")
                continue
            projected_invested = current_equity - cash + qty * price
            if projected_invested / projected_equity > float(self.caps["combined_gross_max"]) + _EPS:
                reject(fill, "combined_gross_max")
                continue
            if not self._is_candidate({"owner": str(fill["owner"])}):
                incumbent_invested = sum(
                    prices.get(existing_symbol, existing_holding.avg_price) * existing_holding.qty
                    for existing_symbol, existing_holding in positions.items()
                    if not self._is_candidate({"owner": existing_holding.owner})
                )
                if incumbent_invested + qty * price > projected_equity * float(self.caps["equity_gross_max"]) + _EPS:
                    reject(fill, "equity_gross_max")
                    continue
            cash = projected_cash
            fees += fee
            if holding is None:
                positions[symbol] = _Holding(qty=qty, avg_price=price, owner=str(fill["owner"]))
            else:
                total_qty = holding.qty + qty
                holding.avg_price = (holding.avg_price * holding.qty + price * qty) / total_qty
                holding.qty = total_qty
            applied.append({**fill, "notional": qty * price, "status": "FILLED"})

        self._cash = cash
        self._positions = positions
        self._prices = prices
        self._fees = fees
        self._fills.extend(applied)
        self._rejected_orders.extend(rejected)
        self._unfilled_exits.extend(unfilled)
        return tuple(applied)

    def apply_dividend(
        self,
        *,
        timestamp: datetime | str,
        symbol: str,
        per_share: float,
        event_id: str | None = None,
    ) -> dict[str, object]:
        timestamp_value = _aware_timestamp(timestamp, "dividend_timestamp")
        symbol_value = str(symbol).strip()
        amount = _finite_number(per_share, "dividend_per_share")
        if not symbol_value or amount <= 0:
            raise ValueError("invalid_dividend")
        holding = self._positions.get(symbol_value)
        credited = holding.qty * amount if holding is not None else 0.0
        self._cash += credited
        self._dividend_total += credited
        record: dict[str, object] = {
            "event_id": str(event_id).strip() if event_id is not None else None,
            "timestamp": timestamp_value,
            "symbol": symbol_value,
            "per_share": amount,
            "qty": holding.qty if holding is not None else 0.0,
            "owner": holding.owner if holding is not None else None,
            "credited_cash": credited,
            "status": "CREDITED" if holding is not None else "NO_HOLDER",
        }
        self._dividend_events.append(record)
        return record

    def mark(self, *, timestamp: datetime | str, marks: Mapping[str, object] | None = None) -> dict[str, object]:
        mark_timestamp = _aware_timestamp(timestamp, "mark_timestamp")
        equity = self._marked_equity(marks)
        positions: dict[str, dict[str, object]] = {}
        invested = 0.0
        for symbol, holding in sorted(self._positions.items()):
            mark = self._prices.get(symbol, holding.avg_price)
            value = holding.qty * mark
            invested += value
            positions[symbol] = {
                "qty": holding.qty,
                "avg_price": holding.avg_price,
                "mark": mark,
                "value": value,
                "owner": holding.owner,
            }
        snapshot: dict[str, object] = {
            "timestamp": mark_timestamp,
            "cash": self._cash,
            "equity": equity,
            "gross_exposure": invested / equity,
            "cash_weight": self._cash / equity,
            "positions": positions,
            "realized_holdings": copy.deepcopy(positions),
            "fees": self._fees,
            "dividend_total": self._dividend_total,
        }
        incumbent_invested = sum(
            _finite_number(row["value"], "snapshot_value")
            for row in positions.values()
            if not self._is_candidate({"owner": str(row["owner"])})
        )
        if incumbent_invested / equity > float(self.caps["equity_gross_max"]) + _EPS:
            raise ValueError("equity_gross_max")
        if _finite_number(snapshot["gross_exposure"], "snapshot_gross") > float(self.caps["combined_gross_max"]) + _EPS:
            raise ValueError("combined_gross_max")
        if (
            _finite_number(snapshot["cash_weight"], "snapshot_cash_weight")
            < float(self.caps["combined_cash_min"]) - _EPS
        ):
            raise ValueError("combined_cash_min")
        self._snapshots.append(snapshot)
        return snapshot

    def run_cycle(
        self,
        *,
        decision_timestamp: datetime | str,
        signal_asof_timestamp: datetime | str,
        target_weights: Sequence[Mapping[str, object]],
        fills: Sequence[Mapping[str, object]],
        marks: Mapping[str, object] | None = None,
        candidate_event_id: str | None = None,
        candidate_active: bool = True,
        fill_timestamp: datetime | str | None = None,
        mark_timestamp: datetime | str | None = None,
    ) -> dict[str, object]:
        self.plan_decision(
            decision_timestamp=decision_timestamp,
            signal_asof_timestamp=signal_asof_timestamp,
            target_weights=target_weights,
            candidate_event_id=candidate_event_id,
            candidate_active=candidate_active,
        )
        self.apply_fills(timestamp=fill_timestamp or decision_timestamp, fills=fills)
        self.mark(timestamp=mark_timestamp or fill_timestamp or decision_timestamp, marks=marks)
        return self.payload()

    def run_r46_event(
        self,
        *,
        event_id: str,
        auction_session_date: str | date,
        regular_sessions: Sequence[Mapping[str, object]],
        decision_timestamp: datetime | str,
        signal_asof_timestamp: datetime | str,
        target_weights: Sequence[Mapping[str, object]],
        entry_fills: Sequence[Mapping[str, object]],
        exit_fills: Sequence[Mapping[str, object]],
        entry_marks: Mapping[str, object] | None = None,
        exit_marks: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        event = str(event_id).strip()
        if not event:
            raise ValueError("event_id_required")
        if event in self._candidate_event_ids:
            raise ValueError("duplicate_r46_event")
        schedule = schedule_r46_holding(
            available_at=decision_timestamp,
            auction_session_date=auction_session_date,
            regular_sessions=regular_sessions,
            availability_policy=POLICY,
            eligibility_policy=ELIGIBILITY,
            same_day=SAME_DAY,
        )
        entry_open = str(schedule["entry_open_timestamp"])
        exit_open = str(schedule["exit_open_timestamp"])
        self.run_cycle(
            decision_timestamp=decision_timestamp,
            signal_asof_timestamp=signal_asof_timestamp,
            target_weights=target_weights,
            fills=entry_fills,
            marks=entry_marks,
            candidate_event_id=event,
            candidate_active=True,
            fill_timestamp=entry_open,
            mark_timestamp=entry_open,
        )
        exit_targets = tuple(row for row in target_weights if not self._is_candidate(row))
        self.run_cycle(
            decision_timestamp=exit_open,
            signal_asof_timestamp=signal_asof_timestamp,
            target_weights=exit_targets,
            fills=exit_fills,
            marks=exit_marks,
            candidate_active=False,
            fill_timestamp=exit_open,
            mark_timestamp=exit_open,
        )
        self._event_schedules.append({"event_id": event, **schedule})
        return self.payload()

    def payload(self) -> dict[str, object]:
        ownership: dict[str, int] = {}
        for fill in self._fills:
            owner = str(fill["owner"])
            ownership[owner] = ownership.get(owner, 0) + 1
        final_holdings: object = self._snapshots[-1]["realized_holdings"] if self._snapshots else self.positions
        payload: dict[str, object] = {
            "schema_version": NATIVE_LEDGER_SCHEMA,
            "status": STATUS,
            "test_only_synthetic": True,
            "research_id": RESEARCH_ID,
            "candidate_id": "R46",
            "native_one_ledger": True,
            "shared_account": True,
            "research_only": True,
            "economic_execution": False,
            "economic_run": False,
            "outcome_artifacts_present": False,
            "paper_live_untouched": True,
            "paper_or_live_activation": False,
            "promotion": False,
            "integration_candidate": False,
            "auto_promotion": False,
            "no_auto_promotion": True,
            "separate_sleeve_blend": False,
            "initial_cash": self.initial_cash,
            "cash": self._cash,
            "caps": dict(self.caps),
            "positions": copy.deepcopy(self.positions),
            "initial_positions": list(self._initial_positions),
            "decisions": copy.deepcopy(self._decisions),
            "fills": copy.deepcopy(self._fills),
            "rejected_orders": copy.deepcopy(self._rejected_orders),
            "snapshots": copy.deepcopy(self._snapshots),
            "realized_holdings": copy.deepcopy(final_holdings),
            "fees": self._fees,
            "dividend_total": self._dividend_total,
            "dividend_events": copy.deepcopy(self._dividend_events),
            "unfilled_exits": copy.deepcopy(self._unfilled_exits),
            "event_records": copy.deepcopy(self._event_records),
            "ownership_attribution": ownership,
            "event_schedules": copy.deepcopy(self._event_schedules),
            "ledger_sha256": "",
        }
        payload["ledger_sha256"] = _canonical_hash(payload, "ledger_sha256")
        return payload


class _TestOnlyNativeSmokeStrategy(PortfolioStrategy):
    """Fixed target solely to exercise the native engine with synthetic bars."""

    name = TEST_NATIVE_STRATEGY
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    rebalance_policy_declared = "monthly"

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(TargetWeight("SPY", 0.55), TargetWeight("TLT", 0.025)),
            cash_weight=0.425,
            universe_version="r46-native-smoke",
        )


def build_native_smoke_dataset() -> MarketDataset:
    """Create deterministic validated bars plus one explicit cash dividend."""

    start = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(4):
        timestamp = start + timedelta(days=index)
        for symbol, price in (("SPY", 100.0 + index), ("TLT", 100.0 + index * 0.5)):
            bars.append(
                Bar(
                    timestamp=timestamp,
                    symbol=symbol,
                    timeframe="1d",
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1_000_000.0,
                    market=Market.EQUITY,
                )
            )
    ordered = tuple(sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol)))
    lineage = DatasetLineage(
        dataset_id="r46-native-smoke",
        parent_dataset_ids=(),
        provider="fixture",
        source_feed="synthetic",
        adjustment="split-aware-execution;total-return-signal",
        downloaded_at=datetime(2026, 9, 10, tzinfo=UTC),
        data_hash=hashlib.sha256(b"r46-native-smoke").hexdigest(),
    )
    dividend = CorporateAction(start + timedelta(days=2), "TLT", CorporateActionType.DIVIDEND, 1.0)
    return MarketDataset(
        Market.EQUITY,
        "1d",
        ordered,
        ordered,
        lineage,
        corporate_actions=(dividend,),
    )


def _native_strategy(strategy_name: str, parameters: Mapping[str, object] | None) -> PortfolioStrategy:
    if strategy_name == TEST_NATIVE_STRATEGY:
        return _TestOnlyNativeSmokeStrategy()
    if strategy_name != "equity_residual_momentum_v3":
        raise ValueError("R46_NATIVE_STRATEGY_MUST_BE_V3")
    return portfolio_strategy_from(strategy_name, parameters=dict(parameters or {}))


def _validate_native_dataset(dataset: MarketDataset, strategy: PortfolioStrategy) -> None:
    if not isinstance(dataset, MarketDataset):
        raise TypeError("native_adapter_requires_market_dataset")
    if dataset.market != Market.EQUITY or dataset.timeframe != strategy.timeframe:
        raise ValueError("native_dataset_strategy_mismatch")
    for bar in (*dataset.signal_bars, *dataset.executable_bars):
        if bar.timestamp.tzinfo is None or not all(
            math.isfinite(value) for value in (bar.open, bar.high, bar.low, bar.close, bar.volume)
        ):
            raise ValueError("native_dataset_bar_invalid")
    for action in dataset.corporate_actions:
        if action.timestamp.tzinfo is None or not math.isfinite(action.value) or action.value <= 0:
            raise ValueError("native_dataset_corporate_action_invalid")


def run_native_adapter(
    dataset: MarketDataset,
    *,
    strategy_name: str = "equity_residual_momentum_v3",
    parameters: Mapping[str, object] | None = None,
    starting_equity: float = 100_000.0,
    universe: object | None = None,
) -> PortfolioBacktestResult:
    """Run one validated dataset through one native engine/account.

    This is the reusable real adapter.  The default strategy is the existing
    registered V3 incumbent; the named smoke strategy is TEST_ONLY and exists
    only so the attestation can prove actual engine/dividend plumbing without
    accessing real market data.
    """

    strategy = _native_strategy(strategy_name, parameters)
    _validate_native_dataset(dataset, strategy)
    policy = rebalance_policy_for(strategy, dict(parameters or strategy.parameters))
    engine = PortfolioBacktestEngine(
        strategy,
        policy,
        starting_equity=_finite_number(starting_equity, "starting_equity"),
        slippage_bps=0.0,
        spread_bps=0.0,
        benchmark_symbol="SPY",
        max_participation_pct=1.0,
        liquidity_lookback_bars=1,
        turnover_band=0.0,
        minimum_order_notional=0.0,
        universe=universe,
        allow_static_universe=universe is None,
    )
    result = engine.run_dataset(dataset, liquidate_at_end=False)
    # PortfolioBacktestResult intentionally stays a compact public value
    # object.  Preserve the engine's per-bar holdings audit beside it so the
    # published R46 ledger can expose the exact account-mark sequence without
    # changing the shared engine API.
    _R46_ENGINE_AUDIT_HISTORY[id(result)] = tuple(engine._holdings_history)
    return result


def _native_fill_rows(result: PortfolioBacktestResult) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fill in result.fills:
        owner = "r46" if fill.symbol == "TLT" else "incumbent_v3"
        rows.append(
            {
                "timestamp": fill.timestamp.isoformat(),
                "symbol": fill.symbol,
                "side": str(fill.side.value).upper(),
                "qty": fill.qty,
                "price": fill.fill_price,
                "fee": fill.fee,
                "owner": owner,
                "decision_timestamp": fill.decision_timestamp.isoformat(),
                "client_order_id": fill.client_order_id,
                "reference_price": fill.reference_price,
                "slippage_cost": fill.slippage_cost,
                "spread_cost": fill.spread_cost,
                "notional": fill.qty * fill.fill_price,
                "status": "FILLED",
            }
        )
    return rows


def _benchmark_marks_at_timestamps(
    benchmark_bars: Sequence[Bar], timestamps: Sequence[datetime]
) -> tuple[float, ...]:
    """Project trusted benchmark closes onto every account observation."""

    ordered = tuple(sorted(benchmark_bars, key=lambda bar: bar.timestamp))
    marks: list[float] = []
    index = 0
    latest: float | None = None
    for timestamp in timestamps:
        while index < len(ordered) and ordered[index].timestamp <= timestamp:
            latest = _finite_number(ordered[index].close, "benchmark_close")
            if latest <= 0:
                raise ValueError("benchmark_close_invalid")
            index += 1
        if latest is None:
            raise ValueError("benchmark_mark_before_source")
        marks.append(latest)
    return tuple(marks)


def _cost_contract(config: object | None, multiplier: float) -> dict[str, object]:
    costs = _backtest_costs(config, multiplier)
    effective = bool(costs["equity_use_effective_dated_fees"])
    return {
        "slippage_bps": cast(float, costs["slippage_bps"]),
        "spread_bps": cast(float, costs["spread_bps"]),
        "commission": cast(float, costs["commission"]),
        "crypto_taker_fee_bps": cast(float, costs["crypto_taker_fee_bps"]),
        "equity_taf_per_share": cast(float, costs["equity_taf_per_share"]),
        "equity_taf_cap": cast(float, costs["equity_taf_cap"]),
        "equity_cat_per_share": cast(float, costs["equity_cat_per_share"]),
        "equity_sec_fee_rate": cast(float, costs["equity_sec_fee_rate"]),
        "equity_use_effective_dated_fees": effective,
        "equity_fee_multiplier": multiplier if effective else 1.0,
    }


def _native_ledger_payload(
    dataset: MarketDataset,
    result: PortfolioBacktestResult,
    *,
    cost_contract: Mapping[str, object] | None = None,
) -> dict[str, object]:
    fills = _native_fill_rows(result)
    owner_by_symbol: dict[str, str] = {}
    for fill in fills:
        owner_by_symbol.setdefault(str(fill["symbol"]), str(fill["owner"]))

    def owner_for_symbol(symbol: str) -> str:
        return owner_by_symbol.get(symbol, "r46" if symbol == "TLT" else "incumbent_v3")

    signal_bars_by_symbol: dict[str, list[Bar]] = {}
    for signal_bar in dataset.signal_bars:
        signal_bars_by_symbol.setdefault(str(signal_bar.symbol), []).append(signal_bar)

    def latest_signal_close(symbol: str, timestamp: datetime, fallback: float) -> float:
        candidates = [
            bar for bar in signal_bars_by_symbol.get(symbol, ()) if bar.timestamp <= timestamp
        ]
        return float(max(candidates, key=lambda bar: bar.timestamp).close) if candidates else fallback

    bars_by_key = {(str(bar.symbol), bar.timestamp): bar for bar in dataset.executable_bars}
    for fill in fills:
        fill["source_bar_key"] = f"{fill['symbol']}|{fill['timestamp']}"
        fill_bar = bars_by_key.get((str(fill["symbol"]), datetime.fromisoformat(str(fill["timestamp"]))))
        if fill_bar is None:
            raise ValueError("native_fill_source_bar_missing")
        fill["source_bar_row_sha256"] = _bar_source_row_hash(fill_bar)
    cash = float(result.state.cash if result.state.cash is not None else result.ending_equity)
    fill_fee_total = sum(_finite_number(fill["fee"], "fill_fee") for fill in fills)
    fill_slippage_total = sum(_finite_number(fill["slippage_cost"], "fill_slippage_cost") for fill in fills)
    fill_spread_total = sum(_finite_number(fill["spread_cost"], "fill_spread_cost") for fill in fills)
    positions = {
        symbol: {
            "qty": quantity,
            "avg_price": result.state.avg_prices.get(symbol, 0.0),
            "owner": owner_for_symbol(symbol),
        }
        for symbol, quantity in sorted(result.state.holdings.items())
    }
    dividend_events: list[dict[str, object]] = []
    for index, action in enumerate(sorted(dataset.corporate_actions, key=lambda item: item.timestamp)):
        held_qty = 0.0
        for fill in fills:
            if fill["symbol"] != action.symbol:
                continue
            # The native engine applies a corporate action before fills at the
            # same timestamp; a purchase on the ex-date open is not a holder.
            if datetime.fromisoformat(str(fill["timestamp"])) < action.timestamp:
                held_qty += (
                    _finite_number(fill["qty"], "fill_qty")
                    if fill["side"] == "BUY"
                    else -_finite_number(fill["qty"], "fill_qty")
                )
        credited = (
            held_qty * action.value if held_qty > 0 and action.action_type == CorporateActionType.DIVIDEND else 0.0
        )
        dividend_events.append(
            {
                "event_id": f"native-action-{index}",
                "timestamp": action.timestamp.isoformat(),
                "symbol": action.symbol,
                "action_type": action.action_type.value,
                "per_share": action.value,
                "qty": held_qty,
                "credited_cash": credited,
                "status": "CREDITED" if credited > 0 else "NO_HOLDER",
                "owner": "r46" if action.symbol == "TLT" else "incumbent_v3",
            }
        )
    final_timestamp = max(bar.timestamp for bar in dataset.executable_bars)
    marks = {bar.symbol: bar.close for bar in dataset.executable_bars if bar.timestamp == final_timestamp}
    marked_equity = cash + sum(
        _finite_number(row["qty"], "position_qty") * marks.get(symbol, _finite_number(row["avg_price"], "avg_price"))
        for symbol, row in positions.items()
    )
    snapshot_positions: dict[str, dict[str, object]] = {}
    for symbol, row in positions.items():
        mark = marks.get(symbol, _finite_number(row["avg_price"], "avg_price"))
        quantity = _finite_number(row["qty"], "position_qty")
        snapshot_positions[symbol] = {
            **row,
            "mark": mark,
            "value": quantity * mark,
        }
    snapshot_gross = (
        sum(_finite_number(row["value"], "position_value") for row in snapshot_positions.values()) / marked_equity
        if marked_equity > 0
        else 0.0
    )
    snapshot_cash_weight = cash / marked_equity if marked_equity > 0 else 0.0
    expected_cash_before_liquidation = result.starting_equity
    for fill in fills:
        signed_cash = _finite_number(fill["qty"], "fill_qty") * _finite_number(fill["price"], "fill_price")
        fee = _finite_number(fill["fee"], "fill_fee")
        expected_cash_before_liquidation += signed_cash - fee if fill["side"] == "SELL" else -signed_cash - fee
    expected_cash_before_liquidation += sum(
        _finite_number(event["credited_cash"], "credited_cash") for event in dividend_events
    )
    terminal_cash_delta = cash - expected_cash_before_liquidation
    pre_liquidation_quantities: dict[str, float] = {}
    pre_liquidation_basis: dict[str, dict[str, float]] = {}
    for fill in fills:
        quantity = _finite_number(fill["qty"], "fill_qty")
        symbol = str(fill["symbol"])
        if fill["side"] == "BUY":
            basis = pre_liquidation_basis.setdefault(
                symbol,
                {"qty": 0.0, "avg_price": 0.0, "entry_fee": 0.0},
            )
            old_qty = basis["qty"]
            basis["avg_price"] = (
                (old_qty * basis["avg_price"]) + quantity * _finite_number(fill["price"], "fill_price")
            ) / (old_qty + quantity)
            basis["qty"] = old_qty + quantity
            basis["entry_fee"] += _finite_number(fill["fee"], "fill_fee")
        else:
            sell_basis = pre_liquidation_basis.get(symbol)
            if sell_basis is not None:
                old_qty = sell_basis["qty"]
                sell_basis["qty"] = max(0.0, old_qty - quantity)
                if old_qty > _EPS:
                    sell_basis["entry_fee"] *= sell_basis["qty"] / old_qty
        signed_quantity = quantity if fill["side"] == "BUY" else -quantity
        pre_liquidation_quantities[str(fill["symbol"])] = (
            pre_liquidation_quantities.get(str(fill["symbol"]), 0.0) + signed_quantity
        )
    raw_terminal_events = getattr(result, "terminal_events", ())
    terminal_events = [dict(item) for item in raw_terminal_events if isinstance(item, Mapping)]
    terminal_applied = bool(terminal_events) or (
        abs(terminal_cash_delta) > _EPS
        or abs(result.fee_total - fill_fee_total) > _EPS
        or abs(result.slippage_cost_total - fill_slippage_total) > _EPS
        or abs(result.spread_cost_total - fill_spread_total) > _EPS
    )
    terminal_audit: list[dict[str, object]] = []
    audit_before_cash = expected_cash_before_liquidation
    audit_positions: dict[str, dict[str, object]] = {
        symbol: {
            "qty": quantity,
            "owner": row["owner"],
            "avg_price": pre_liquidation_basis.get(symbol, {}).get("avg_price", 0.0),
            "entry_fee": pre_liquidation_basis.get(symbol, {}).get("entry_fee", 0.0),
            "realized_pnl_accumulator": 0.0,
        }
        for symbol, quantity in sorted(pre_liquidation_quantities.items())
        if quantity > _EPS
        for row in ({"owner": owner_for_symbol(symbol)},)
    }
    for index, raw_event in enumerate(sorted(terminal_events, key=lambda item: str(item.get("timestamp", item.get("event_timestamp", ""))))):
        symbol = str(raw_event.get("symbol", "")).strip()
        quantity = _finite_number(raw_event.get("quantity"), "terminal_quantity")
        last_close = _finite_number(raw_event.get("last_close"), "terminal_last_close")
        fill_price = _finite_number(raw_event.get("fill_price", last_close), "terminal_fill_price")
        fee = _finite_number(raw_event.get("fee", raw_event.get("fees", 0.0)), "terminal_fee")
        proceeds = _finite_number(raw_event.get("proceeds", quantity * fill_price - fee), "terminal_proceeds")
        before_positions = copy.deepcopy(audit_positions)
        before_position_value = before_positions.get(symbol)
        before_position: Mapping[str, object] = (
            before_position_value if isinstance(before_position_value, Mapping) else {}
        )
        last_bar_value = str(raw_event.get("last_bar", raw_event.get("timestamp", raw_event.get("event_timestamp", ""))))
        event_timestamp = datetime.fromisoformat(last_bar_value)
        audit_before_cash = _finite_number(audit_before_cash, "terminal_cash_before")
        audit_after_cash = audit_before_cash + proceeds
        event_marks = {
            symbol_name: latest_signal_close(
                symbol_name,
                event_timestamp,
                _finite_number(position.get("avg_price"), "terminal_avg_price"),
            )
            for symbol_name, position in before_positions.items()
        }
        pre_equity = audit_before_cash + sum(
            _finite_number(position.get("qty"), "terminal_position_qty") * event_marks[symbol_name]
            for symbol_name, position in before_positions.items()
        )
        avg_price = _finite_number(before_position.get("avg_price", 0.0), "terminal_avg_price")
        entry_fee = _finite_number(before_position.get("entry_fee", 0.0), "terminal_entry_fee")
        realized_pnl = (
            (fill_price - avg_price) * quantity - entry_fee - fee
            + _finite_number(before_position.get("realized_pnl_accumulator", 0.0), "terminal_realized_accumulator")
        )
        effective_policy = str(raw_event.get("effective_policy", raw_event.get("policy", "FINAL_BAR")))
        entity_id = str(raw_event.get("entity_id", symbol))
        terminal_map_identity = f"{entity_id}|{last_bar_value}|{effective_policy}"
        terminal_map_hash = hashlib.sha256(
            _canonical_bytes(
                {
                    "entity_id": entity_id,
                    "last_bar": last_bar_value,
                    "effective_policy": effective_policy,
                }
            )
        ).hexdigest()
        audit_positions.pop(symbol, None)
        terminal_audit.append(
            {
                "event_id": str(raw_event.get("event_id", f"terminal-{index}-{symbol}")),
                "symbol": symbol,
                "qty": quantity,
                "last_bar": last_bar_value,
                "last_close": last_close,
                "policy": str(raw_event.get("policy", "FINAL_BAR")),
                "effective_policy": effective_policy,
                "terminal_map_identity": terminal_map_identity,
                "terminal_map_hash": terminal_map_hash,
                "owner": owner_for_symbol(symbol),
                "avg_price": avg_price,
                "entry_fee": entry_fee,
                "realized_pnl": realized_pnl,
                "pre_equity": pre_equity,
                "fill_price": fill_price,
                "fee": fee,
                "slippage_cost": _finite_number(raw_event.get("slippage_cost", 0.0), "terminal_slippage_cost"),
                "spread_cost": _finite_number(raw_event.get("spread_cost", 0.0), "terminal_spread_cost"),
                "proceeds": proceeds,
                "cash_before": audit_before_cash,
                "cash_after": audit_after_cash,
                "positions_before": before_positions,
                "positions_after": copy.deepcopy(audit_positions),
                "position_removed": raw_event.get("position_removed") is True,
            }
        )
        post_equity = audit_after_cash + sum(
            _finite_number(position.get("qty"), "terminal_position_qty") * event_marks[symbol_name]
            for symbol_name, position in audit_positions.items()
        )
        terminal_audit[-1]["post_equity"] = post_equity
        audit_before_cash = audit_after_cash
    terminal_liquidation = {
        "applied": terminal_applied,
        "cash_before": expected_cash_before_liquidation,
        "cash_after": cash,
        "cash_delta": terminal_cash_delta,
        "slippage_cost": result.slippage_cost_total - fill_slippage_total,
        "spread_cost": result.spread_cost_total - fill_spread_total,
        "fee": result.fee_total - fill_fee_total,
        "liquidated_symbols": sorted(
            symbol
            for symbol, quantity in pre_liquidation_quantities.items()
            if terminal_applied and quantity > _EPS and symbol not in positions
        ),
        "audit_rows": terminal_audit,
    }
    holdings_history = _R46_ENGINE_AUDIT_HISTORY.get(id(result), ())
    snapshot_audit = _R46_ENGINE_SNAPSHOT_AUDIT.get(id(result), ())
    snapshots: list[dict[str, object]] = []
    if isinstance(snapshot_audit, Sequence) and len(snapshot_audit) == len(result.equity_curve):
        snapshots = [copy.deepcopy(dict(snapshot)) for snapshot in snapshot_audit]
    if not snapshots:
        # Keep the native smoke adapter usable for malformed/legacy result
        # objects, but production strict verification rejects this fallback
        # because it is not a complete curve audit.
        snapshots = [
            {
                "timestamp": final_timestamp.isoformat(),
                "cash": cash,
                "caps": dict(DEFAULT_CAPS),
                "equity": marked_equity,
                "gross_exposure": snapshot_gross,
                "cash_weight": snapshot_cash_weight,
                "holdings_count": len(snapshot_positions),
                "maximum_concentration": max(
                    (
                        _finite_number(row.get("value"), "snapshot_position_value") / marked_equity
                        for row in snapshot_positions.values()
                    ),
                    default=0.0,
                ),
                "positions": snapshot_positions,
                "realized_holdings": copy.deepcopy(snapshot_positions),
                "fees": result.fee_total,
                "terminal_liquidation": copy.deepcopy(terminal_liquidation),
            }
        ]
    # Snapshot ``fees`` is a cumulative accounting value, not merely the
    # ordinary fill subtotal captured by the engine snapshot hook.  T1 rows
    # occur before that cycle's snapshot and FINAL_BAR rows occur before the
    # post-run final snapshot replacement, so both terminal fee classes are
    # included by timestamp.
    for snapshot in snapshots:
        snapshot_timestamp = datetime.fromisoformat(str(snapshot["timestamp"]))
        snapshot["fees"] = sum(
            _finite_number(fill["fee"], "snapshot_fill_fee")
            for fill in fills
            if datetime.fromisoformat(str(fill["timestamp"])) <= snapshot_timestamp
        ) + sum(
            _finite_number(row.get("fee"), "snapshot_terminal_fee")
            for row in terminal_audit
            if datetime.fromisoformat(str(row["last_bar"])) <= snapshot_timestamp
        )
    payload: dict[str, object] = {
        "schema_version": NATIVE_LEDGER_SCHEMA,
        "status": NATIVE_SMOKE_STATUS,
        "test_only_synthetic": False,
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "native_one_ledger": True,
        "shared_account": True,
        "native_engine": "PortfolioBacktestEngine",
        "engine_invocations": 1,
        "research_only": True,
        "economic_execution": False,
        "economic_run": False,
        "outcome_artifacts_present": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "promotion": False,
        "integration_candidate": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "separate_sleeve_blend": False,
        "initial_cash": result.starting_equity,
        "cash": cash,
        "caps": dict(DEFAULT_CAPS),
        "fees": result.fee_total,
        "slippage_cost_total": result.slippage_cost_total,
        "spread_cost_total": result.spread_cost_total,
        "total_cost": result.slippage_cost_total + result.spread_cost_total + result.fee_total,
        "fills": fills,
        "rebalances": [
            {
                "decision_timestamp": record.decision_timestamp.isoformat(),
                "reason": str(record.reason),
                "target_weights": [[symbol, float(weight)] for symbol, weight in record.target_weights],
                "target_cash_weight": float(record.target_cash_weight),
                "pre_trade_weights": [[symbol, float(weight)] for symbol, weight in record.pre_trade_weights],
                "signed_trade_weights": [[symbol, float(weight)] for symbol, weight in record.signed_trade_weights],
                "post_fill_actual_weights": [
                    [symbol, float(weight)] for symbol, weight in record.post_fill_actual_weights
                ],
                "target_vs_actual_drift": float(record.target_vs_actual_drift),
                "unfilled_notional": float(record.unfilled_notional),
                "rejected_notional": float(record.rejected_notional),
                "expected_turnover": float(record.expected_turnover),
                "realized_turnover": float(record.realized_turnover),
                "cost": float(record.cost),
                "executed_weights": [[symbol, float(weight)] for symbol, weight in record.executed_weights],
                "universe_version": record.universe_version,
                "universe_snapshot_id": record.universe_snapshot_id,
                "equity_at_decision": float(record.equity_at_decision),
                "rebalance_number": int(record.rebalance_number),
                "risk_overlay_halted": bool(record.risk_overlay_halted),
                "risk_overlay_reasons": list(record.risk_overlay_reasons),
            }
            for record in result.rebalances
        ],
        "rejected_orders": [],
        "unfilled_exits": [],
        "dividend_events": dividend_events,
        "dividend_total": result.dividend_total,
        "terminal_liquidation": terminal_liquidation,
        "terminal_audit": terminal_audit,
        "cost_contract": dict(cost_contract or {
            "slippage_bps": 0.0,
            "spread_bps": 0.0,
            "commission": 0.0,
            "crypto_taker_fee_bps": 0.0,
            "equity_taf_per_share": 0.0,
            "equity_taf_cap": 0.0,
            "equity_cat_per_share": 0.0,
            "equity_sec_fee_rate": 0.0,
            "equity_use_effective_dated_fees": False,
            "equity_fee_multiplier": 1.0,
        }),
        "positions": positions,
        "realized_holdings": copy.deepcopy(positions),
        "snapshots": snapshots,
        "holdings_history": [
            {
                "timestamp": timestamp.isoformat(),
                "holdings_count": int(audit[0]),
                "cash_ratio": float(audit[1]),
                "maximum_concentration": float(audit[2]),
            }
            for timestamp, audit in zip(result.curve_timestamps, holdings_history, strict=True)
        ]
        if isinstance(holdings_history, Sequence) and len(holdings_history) == len(result.curve_timestamps)
        else [],
        "equity_curve": [float(value) for value in result.equity_curve],
        "equity_curve_timestamps": [timestamp.isoformat() for timestamp in result.curve_timestamps],
        # Availability markers can add executable-only timestamps between two
        # regular sessions.  Carry the latest trusted SPY close onto those
        # account observations so the persisted benchmark remains timestamp
        # aligned without inventing a source price.
        "benchmark_curve": [
            float(mark)
            for mark in _benchmark_marks_at_timestamps(result.benchmark_bars, result.curve_timestamps)
        ],
        "benchmark_curve_timestamps": [timestamp.isoformat() for timestamp in result.curve_timestamps],
        "audit_context": {
            "replay_version": "r46-v2-ledger-replay-v1",
            "source_dataset_digest": dataset.content_digest(),
            "terminal_policy": "T1",
            "execution_max_participation_pct": 0.10,
            "liquidity_lookback_bars": 20,
        },
        "validated_bar_count": len(dataset.executable_bars),
        "validated_signal_bar_count": len(dataset.signal_bars),
        "explicit_corporate_action_count": len(dataset.corporate_actions),
        "dataset_digest": dataset.content_digest(),
        "strategy": {"name": result.state.strategy_name, "version": result.state.strategy_version},
        "ledger_sha256": "",
    }
    payload["ledger_sha256"] = _canonical_hash(payload, "ledger_sha256")
    return payload


def native_adapter_smoke() -> dict[str, object]:
    """Return a structural smoke result; READY means actual engine path passed."""

    dataset = build_native_smoke_dataset()
    try:
        result = run_native_adapter(dataset, strategy_name=TEST_NATIVE_STRATEGY)
        ledger = _native_ledger_payload(dataset, result)
    except (TypeError, ValueError, RuntimeError) as exc:
        return {
            "schema_version": "r46-native-ledger-attestation-v2",
            "status": "BLOCKED_NATIVE_LEDGER",
            "blockers": [f"NATIVE_ADAPTER_SMOKE:{type(exc).__name__}:{exc}"],
        }
    return {
        "schema_version": "r46-native-ledger-attestation-v2",
        "status": "READY_NATIVE_LEDGER",
        "native_adapter_verified": True,
        "native_engine": "PortfolioBacktestEngine",
        "engine_invocations": 1,
        "ledger": ledger,
    }


def run_native_once(
    *,
    dataset: MarketDataset,
    gate: Path | str | Mapping[str, object] | None,
    one_shot_state_path: Path | str,
    output_path: Path | str | None = None,
    strategy_name: str = "equity_residual_momentum_v3",
    parameters: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Blocked compatibility name; native execution is never a production one-shot surface."""

    del dataset, gate, one_shot_state_path, output_path, strategy_name, parameters
    raise RuntimeError("BLOCKED_TEST_ONLY_NATIVE_RUN")


def run_native_once_test_only(
    *,
    dataset: MarketDataset,
    gate: Path | str | Mapping[str, object] | None,
    one_shot_state_path: Path | str,
    output_path: Path | str | None = None,
    strategy_name: str = "equity_residual_momentum_v3",
    parameters: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """TEST_ONLY native harness; never use this for a pinned economic state."""

    require_pre_run_ok(gate)
    try:
        test_state = _load_json(Path(one_shot_state_path).resolve())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("BLOCKED_TEST_ONLY_NATIVE_RUN") from None
    if test_state.get("test_only") is not True:
        raise RuntimeError("BLOCKED_TEST_ONLY_NATIVE_RUN")
    reserve_one_shot(one_shot_state_path, require_crosslinks=False)
    result = run_native_adapter(dataset, strategy_name=strategy_name, parameters=parameters)
    ledger = _native_ledger_payload(dataset, result)
    encoded = json.dumps(ledger, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if output_path is not None:
        output = Path(output_path)
        try:
            _exclusive_bytes(output, encoded)
        except FileExistsError:
            raise RuntimeError("NATIVE_OUTPUT_ALREADY_EXISTS") from None
    output_hash = hashlib.sha256(encoded).hexdigest()
    consume_one_shot(one_shot_state_path, output_hash, require_crosslinks=False)
    return ledger


def run_synthetic_test_only(
    *,
    gate: Path | str | Mapping[str, object] | None,
    decisions: Sequence[Mapping[str, object]],
    fills: Sequence[Mapping[str, object]],
    initial_cash: float = 100_000.0,
) -> dict[str, object]:
    """TEST_ONLY synthetic harness; never an attestation-ready execution."""

    require_pre_run_ok(gate)
    ledger = SharedAccountLedger(initial_cash=initial_cash)
    if decisions:
        pending_fills = list(fills)
        for decision in decisions:
            decision_fills = decision.get("fills", pending_fills if len(decisions) == 1 else ())
            if not isinstance(decision_fills, Sequence) or isinstance(decision_fills, (str, bytes)):
                raise ValueError("decision_fills_invalid")
            marks = decision.get("marks", {})
            if not isinstance(marks, Mapping):
                raise ValueError("decision_marks_invalid")
            ledger.run_cycle(
                decision_timestamp=cast(datetime | str, decision["decision_timestamp"]),
                signal_asof_timestamp=cast(
                    datetime | str,
                    decision.get("signal_asof_timestamp", decision["decision_timestamp"]),
                ),
                target_weights=cast(Sequence[Mapping[str, object]], decision.get("target_weights", ())),
                fills=cast(Sequence[Mapping[str, object]], decision_fills),
                marks=marks,
                candidate_event_id=cast(str | None, decision.get("candidate_event_id")),
                candidate_active=bool(decision.get("candidate_active", True)),
            )
            if len(decisions) == 1:
                pending_fills = []
    elif fills:
        first_timestamp = fills[0].get("timestamp")
        if first_timestamp is None:
            raise ValueError("fill_timestamp_required")
        ledger.apply_fills(timestamp=cast(datetime | str, first_timestamp), fills=fills)
        ledger.mark(timestamp=cast(datetime | str, first_timestamp))
    return ledger.payload()


def run_synthetic(
    *,
    gate: Path | str | Mapping[str, object] | None,
    decisions: Sequence[Mapping[str, object]],
    fills: Sequence[Mapping[str, object]],
    initial_cash: float = 100_000.0,
) -> dict[str, object]:
    """Compatibility name for the explicitly TEST_ONLY synthetic harness."""

    return run_synthetic_test_only(
        gate=gate,
        decisions=decisions,
        fills=fills,
        initial_cash=initial_cash,
    )


# ---------------------------------------------------------------------------
# Package-backed v2 execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class R46IncumbentInputs:
    """The frozen incumbent inputs loaded through the v5 research seams."""

    bars: tuple[Bar, ...]
    lifetimes: object
    corporate_actions: tuple[CorporateAction, ...]
    source_paths: Mapping[str, str] = field(default_factory=dict)
    terminal_map: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class R46EventSchedule:
    """One qualified event with explicit decision and execution boundaries."""

    event_id: str
    security_identifier: str
    auction_session_date: str
    available_at: datetime
    entry_session_date: str
    entry_open: datetime
    exit_session_date: str
    exit_open: datetime
    entry_decision: datetime
    exit_decision: datetime
    holding_intervals: int = 5
    future_open_timestamps: tuple[datetime, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "security_identifier": self.security_identifier,
            "auction_session_date": self.auction_session_date,
            "available_at": self.available_at.isoformat(),
            "entry_session_date": self.entry_session_date,
            "entry_open": self.entry_open.isoformat(),
            "exit_session_date": self.exit_session_date,
            "exit_open": self.exit_open.isoformat(),
            "entry_decision": self.entry_decision.isoformat(),
            "exit_decision": self.exit_decision.isoformat(),
            "holding_intervals": self.holding_intervals,
            "future_open_timestamps": [timestamp.isoformat() for timestamp in self.future_open_timestamps],
        }


@dataclass(frozen=True)
class R46PackageInputs:
    """Validated five-artifact package plus event overlay inputs."""

    package_path: Path
    root: Path
    package: Mapping[str, Any]
    documents: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]
    tlt_bars: tuple[Bar, ...]
    tlt_dividends: tuple[CorporateAction, ...]
    eligibility: tuple[Mapping[str, Any], ...]
    calendar: tuple[Mapping[str, Any], ...]
    schedules: tuple[R46EventSchedule, ...]
    markers: tuple[Bar, ...]
    marker_sources: Mapping[str, str] = field(default_factory=dict)

    @property
    def package_canonical_sha256(self) -> str:
        integrity = self.package.get("integrity")
        if not isinstance(integrity, Mapping):
            raise ValueError("package_integrity_missing")
        value = integrity.get("manifest_sha256")
        if not isinstance(value, str):
            raise ValueError("package_integrity_missing")
        return value


def _parse_utc(value: object, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name}_must_be_timezone_aware")
    return parsed.astimezone(UTC)


def _read_jsonl(path: Path, field_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{field_name}_jsonl_invalid:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{field_name}_row_invalid:{line_number}")
            rows.append(value)
    return rows


def _read_csv_rows(path: Path, field_name: str) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{field_name}_header_missing")
        return [dict(row) for row in reader]


def _require_policy_fields(value: Mapping[str, Any], scope: str, *, event: bool = False) -> None:
    availability_field = "availability_derivation" if event else "result_availability_policy"
    if value.get(availability_field) != POLICY:
        raise ValueError(f"{scope}_availability_policy_mismatch")
    if value.get("result_signal_eligibility") != ELIGIBILITY:
        raise ValueError(f"{scope}_eligibility_policy_mismatch")
    if value.get("same_day_signal_allowed") is not SAME_DAY:
        raise ValueError(f"{scope}_same_day_policy_mismatch")


def _validate_r46_policy_contract(
    package: Mapping[str, Any],
    governance: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> int:
    """Require the uniform Treasury policy from both validated artifacts."""

    if package.get("expected_event_count") != EXPECTED_EVENT_COUNT:
        raise ValueError("production_event_count_mismatch")
    expected_ids = package.get("expected_event_ids")
    if not isinstance(expected_ids, list) or any(
        not isinstance(value, str) or not value.strip() for value in expected_ids
    ):
        raise ValueError("production_event_ids_invalid")
    canonical_ids = sorted(set(expected_ids))
    if expected_ids != canonical_ids or len(canonical_ids) != EXPECTED_EVENT_COUNT:
        raise ValueError("production_event_ids_not_sorted_unique")
    if len(events) != EXPECTED_EVENT_COUNT:
        raise ValueError("production_event_count_mismatch")
    observed_ids = [str(event.get("canonical_event_id", "")) for event in events]
    if observed_ids != canonical_ids:
        raise ValueError("production_event_ids_mismatch")
    package_policy = package.get("publication_availability_policy")
    if not isinstance(package_policy, Mapping):
        raise ValueError("package_publication_availability_policy_missing")
    _require_policy_fields(package_policy, "package")
    governance_policy = governance.get("publication_availability_policy")
    if not isinstance(governance_policy, Mapping):
        raise ValueError("governance_publication_availability_policy_missing")
    _require_policy_fields(governance_policy, "governance")
    for scope, policy in (("package", package_policy), ("governance", governance_policy)):
        if policy.get("expected_event_count") != EXPECTED_EVENT_COUNT:
            raise ValueError(f"{scope}_publication_event_count_mismatch")
        policy_ids = policy.get("expected_event_ids")
        if policy_ids is not None and policy_ids != canonical_ids:
            raise ValueError(f"{scope}_publication_event_ids_mismatch")
    for event in events:
        _require_policy_fields(event, "event", event=True)
    if all(event.get("published_at") is None for event in events):
        raise ValueError("production_published_at_all_missing")
    return len(events)


def _artifact_path(package: Mapping[str, Any], name: str, root: Path) -> Path:
    artifacts = package.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("package_artifacts_missing")
    spec = artifacts.get(name)
    if not isinstance(spec, Mapping) or not isinstance(spec.get("path"), str):
        raise ValueError(f"package_{name}_path_missing")
    path = Path(str(spec["path"]))
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not resolved.is_file():
        raise ValueError(f"package_{name}_missing")
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"package_{name}_outside_root") from exc
    expected = spec.get("sha256")
    if not isinstance(expected, str) or _sha256_file(resolved) != expected:
        raise ValueError(f"package_{name}_hash_mismatch")
    return resolved


def _bar_from_package_row(row: Mapping[str, Any]) -> Bar:
    required = (
        "session_date",
        "regular_open_timestamp",
        "available_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )
    if any(key not in row for key in required):
        raise ValueError("package_bar_fields_missing")
    timestamp = _parse_utc(row["regular_open_timestamp"], "regular_open_timestamp")
    available_at = _parse_utc(row["available_at"], "bar_available_at")
    if available_at < timestamp:
        raise ValueError("bar_available_before_session_open")
    price_fields = ("open", "high", "low", "close", "volume")
    values = {key: _finite_number(row[key], f"bar_{key}") for key in price_fields}
    if any(value <= 0 for value in (values["open"], values["high"], values["low"], values["close"], values["volume"])):
        raise ValueError("package_bar_non_positive")
    return Bar(
        timestamp=timestamp,
        symbol="TLT",
        timeframe="1d",
        open=values["open"],
        high=values["high"],
        low=values["low"],
        close=values["close"],
        volume=values["volume"],
        market=Market.EQUITY,
    )


def _package_dividend_values(row: Mapping[str, Any], permanent_id: str) -> tuple[datetime, float]:
    if str(row.get("permanent_security_identifier", "")) != permanent_id:
        raise ValueError("package_dividend_identity_mismatch")
    if row.get("action_type") != "cash_dividend" or row.get("currency") != "USD":
        raise ValueError("package_dividend_contract_invalid")
    event_timestamp = _parse_utc(row.get("event_timestamp"), "dividend_event_timestamp")
    available_at = _parse_utc(row.get("available_at"), "dividend_available_at")
    if available_at > event_timestamp:
        raise ValueError("dividend_available_after_event")
    return event_timestamp, _finite_number(row.get("cash_or_ratio"), "dividend_cash_or_ratio")


def load_r46_v2_package(
    package_path: Path | str,
    *,
    repo_root: Path | str | None = None,
    allow_marker_fallback: bool = False,
) -> R46PackageInputs:
    """Consume the exact validated v2 package and construct its event overlay.

    ``allow_marker_fallback`` is intentionally test-only.  A production
    package must contain a TLT price before every event's availability time so
    that the event boundary can be represented without looking ahead.
    """

    package_file = Path(package_path).resolve()
    root = Path(repo_root or REPO).resolve()
    if not package_file.is_file():
        raise ValueError("package_missing")
    try:
        package_file.relative_to(root)
    except ValueError as exc:
        raise ValueError("package_outside_root") from exc
    from scripts.research.build_r46_v2_data_package_2026_09_10 import (
        _documents_for_validator,
        canonical_event_ids,
        consume_r46_v2_data_package,
    )

    try:
        package = consume_r46_v2_data_package(package_file, repo_root=root)
    except Exception as exc:
        raise ValueError(f"package_contract_invalid:{exc}") from exc
    artifact_paths = {
        name: _artifact_path(package, name, root)
        for name in ("documents", "bars", "dividends", "eligibility", "calendar")
    }
    documents = _read_jsonl(artifact_paths["documents"], "documents")
    from scripts.research.r46_treasury_auction_readiness_2026_09_10 import normalize_auction_documents

    try:
        normalized = normalize_auction_documents(
            _documents_for_validator(
                documents,
                documents_path=artifact_paths["documents"],
                root=root,
            ),
            repo_root=root,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"package_documents_invalid:{exc}") from exc
    events_raw = normalized.get("events")
    if not isinstance(events_raw, list):
        raise ValueError("package_events_missing")
    expected_ids = package.get("expected_event_ids")
    event_ids = [event.get("canonical_event_id") for event in events_raw if isinstance(event, Mapping)]
    try:
        canonical_expected_ids = canonical_event_ids(expected_ids)
        canonical_observed_ids = canonical_event_ids(event_ids)
    except ValueError as exc:
        raise ValueError("package_event_ids_mismatch") from exc
    if expected_ids != canonical_expected_ids or canonical_observed_ids != canonical_expected_ids:
        raise ValueError("package_event_ids_mismatch")
    governance_reference = package.get("governance_manifest_path")
    governance_path = _resolve_pinned_path(governance_reference, root)
    governance = _load_json(governance_path)
    _validate_r46_policy_contract(package, governance, cast(Sequence[Mapping[str, Any]], events_raw))

    bar_rows = _read_csv_rows(artifact_paths["bars"], "bars")
    tlt_bars = tuple(sorted((_bar_from_package_row(row) for row in bar_rows), key=lambda item: item.timestamp))
    if len({bar.timestamp for bar in tlt_bars}) != len(tlt_bars):
        raise ValueError("package_duplicate_tlt_bar_timestamp")
    security_ids = {str(row.get("permanent_security_identifier", "")) for row in bar_rows}
    security_ids.discard("")
    if len(security_ids) != 1:
        raise ValueError("package_tlt_identity_mismatch")
    permanent_id = next(iter(security_ids))

    dividend_rows = _read_csv_rows(artifact_paths["dividends"], "dividends")
    dividends: list[CorporateAction] = []
    for row in dividend_rows:
        event_timestamp, cash_or_ratio = _package_dividend_values(row, permanent_id)
        dividends.append(
            CorporateAction(
                event_timestamp,
                "TLT",
                CorporateActionType.DIVIDEND,
                cash_or_ratio,
            )
        )

    eligibility = tuple(_read_jsonl(artifact_paths["eligibility"], "eligibility"))
    if not eligibility or any(str(row.get("permanent_security_identifier", "")) != permanent_id for row in eligibility):
        raise ValueError("package_eligibility_identity_mismatch")
    calendar = tuple(_read_jsonl(artifact_paths["calendar"], "calendar"))
    regular_calendar = [
        {
            "session_date": str(row.get("regular_session_date", "")),
            "regular_open_timestamp": row.get("regular_open_timestamp"),
        }
        for row in calendar
        if row.get("holiday_or_closure_reason") in (None, "", "NONE")
    ]
    if not regular_calendar:
        raise ValueError("package_calendar_empty")
    calendar_open_by_date: dict[str, datetime] = {}
    for row in regular_calendar:
        session_date = str(row["session_date"])
        calendar_open = _parse_utc(row["regular_open_timestamp"], "calendar_regular_open")
        if session_date in calendar_open_by_date:
            raise ValueError("package_calendar_duplicate_session")
        calendar_open_by_date[session_date] = calendar_open
    for row in bar_rows:
        session_date = str(row.get("session_date", ""))
        bar_open = _parse_utc(row.get("regular_open_timestamp"), "regular_open_timestamp")
        if calendar_open_by_date.get(session_date) != bar_open:
            raise ValueError("package_bar_calendar_open_mismatch")
    for row in dividend_rows:
        effective_date = str(row.get("effective_date", ""))
        event_timestamp = _parse_utc(row.get("event_timestamp"), "dividend_event_timestamp")
        if calendar_open_by_date.get(effective_date) != event_timestamp:
            raise ValueError("package_dividend_calendar_open_mismatch")
    schedules: list[R46EventSchedule] = []
    marker_rows: list[Bar] = []
    marker_sources: dict[str, str] = {}
    for event in events_raw:
        if not isinstance(event, Mapping):
            raise ValueError("package_event_invalid")
        event_id = str(event.get("canonical_event_id", "")).strip()
        auction_date = str(event.get("auction_session_date", "")).strip()
        available = _parse_utc(event.get("entry_available_at", event.get("available_at")), "event_available_at")
        auction_open_row = next(
            (row for row in regular_calendar if str(row["session_date"]) == auction_date),
            None,
        )
        if auction_open_row is None:
            raise ValueError("auction_session_calendar_missing")
        auction_open = _parse_utc(auction_open_row["regular_open_timestamp"], "auction_regular_open")
        # A decision made before the auction session's regular open would be
        # filled by the same-session TLT bar, violating the frozen next-open
        # entry rule.  Fail closed instead of silently taking that fill.
        if available <= auction_open:
            raise ValueError("event_available_before_auction_open")
        schedule = schedule_r46_holding(
            available_at=available,
            auction_session_date=auction_date,
            regular_sessions=regular_calendar,
            availability_policy=str(event.get("availability_derivation")),
            eligibility_policy=str(event.get("result_signal_eligibility")),
            same_day=cast(bool, event.get("same_day_signal_allowed")),
            published_at=event.get("published_at"),
        )
        future = [
            (
                row,
                _parse_utc(row["regular_open_timestamp"], "future_regular_open"),
            )
            for row in sorted(regular_calendar, key=lambda item: str(item["session_date"]))
            if _parse_utc(row["regular_open_timestamp"], "future_regular_open") > available
        ]
        if len(future) < 6:
            raise ValueError("sixth_regular_open_missing")
        entry_open = _parse_utc(schedule["entry_open_timestamp"], "entry_open_timestamp")
        exit_open = _parse_utc(schedule["exit_open_timestamp"], "exit_open_timestamp")
        if entry_open != future[0][1] or exit_open != future[5][1]:
            raise ValueError("event_schedule_mismatch")
        exit_decision = future[4][1]
        schedules.append(
            R46EventSchedule(
                event_id=event_id,
                security_identifier=str(event.get("security_identifier", "")),
                auction_session_date=auction_date,
                available_at=available,
                entry_session_date=str(schedule["entry_session_date"]),
                entry_open=entry_open,
                exit_session_date=str(schedule["exit_session_date"]),
                exit_open=exit_open,
                entry_decision=available,
                exit_decision=exit_decision,
                future_open_timestamps=tuple(opening for _, opening in future[:6]),
            )
        )
        prior = [bar for bar in tlt_bars if bar.timestamp < available]
        if prior:
            source = prior[-1]
            marker_source = "last_pre_event_tlt_bar"
        elif allow_marker_fallback:
            future_bar = next((bar for bar in tlt_bars if bar.timestamp >= entry_open), None)
            if future_bar is None:
                raise ValueError("tlt_event_boundary_price_missing")
            source = future_bar
            marker_source = "TEST_ONLY_FIRST_FUTURE_TLT_BAR"
        else:
            raise ValueError("tlt_event_boundary_price_missing")
        if available not in {bar.timestamp for bar in marker_rows}:
            marker_rows.append(
                replace(
                    source,
                    timestamp=available,
                    volume=max(source.volume, 1_000_000.0),
                )
            )
        marker_sources[event_id] = marker_source
    return R46PackageInputs(
        package_path=package_file,
        root=root,
        package=package,
        documents=tuple(documents),
        events=tuple(cast(Mapping[str, Any], event) for event in events_raw),
        tlt_bars=tlt_bars,
        tlt_dividends=tuple(sorted(dividends, key=lambda item: item.timestamp)),
        eligibility=eligibility,
        calendar=calendar,
        schedules=tuple(sorted(schedules, key=lambda item: (item.entry_decision, item.event_id))),
        markers=tuple(sorted(marker_rows, key=lambda item: item.timestamp)),
        marker_sources=marker_sources,
    )


def load_incumbent_pit_inputs(*, repo_root: Path | str = REPO) -> R46IncumbentInputs:
    """Load v5 incumbent bars, lifetimes, and corporate actions via its seam."""

    root = Path(repo_root).resolve()
    from scripts import run_symmetric_v5_validation as seam

    seam_path = (root / V5_INCUMBENT_RELATIVE_PATHS["seam"]).resolve()
    if seam_path != Path(seam.__file__).resolve():
        raise RuntimeError("BLOCKED_PIT_SOURCE:seam_root_mismatch")
    source_paths = {key: str((root / relative).resolve()) for key, relative in V5_INCUMBENT_RELATIVE_PATHS.items()}
    source_paths["historical_cutoff"] = R46_HISTORICAL_CUTOFF
    for key in ("bars", "lifetimes", "corporate_actions", "bars_manifest", "corporate_actions_manifest", "seam"):
        source_paths[f"{key}_sha256"] = _sha256_file(Path(source_paths[key]))
    _validate_v5_incumbent_dependencies(source_paths)
    bars = tuple(replace(bar, timeframe="1d") for bar in seam.load_bars(Path(source_paths["bars"])))
    terminal_map, lifetimes = seam.load_lifetimes(Path(source_paths["lifetimes"]))
    cutoff = datetime.fromisoformat(R46_HISTORICAL_CUTOFF.replace("Z", "+00:00"))
    actions = tuple(
        action
        for action in seam.load_corporate_actions_all(Path(source_paths["corporate_actions"]))
        if action.timestamp <= cutoff
    )
    return R46IncumbentInputs(
        bars=bars,
        lifetimes=lifetimes,
        corporate_actions=actions,
        source_paths={**source_paths, "repo_root": str(root)},
        terminal_map=terminal_map,
    )


def _validate_v5_incumbent_dependencies(source_paths: Mapping[str, str]) -> dict[str, str]:
    """Fail closed on the exact V5 bytes, manifests, seam, and PIT cutoff."""

    required = (
        "bars",
        "lifetimes",
        "corporate_actions",
        "bars_manifest",
        "corporate_actions_manifest",
        "seam",
    )
    if source_paths.get("historical_cutoff") != R46_HISTORICAL_CUTOFF:
        raise RuntimeError("BLOCKED_PIT_SOURCE:historical_cutoff")
    paths: dict[str, Path] = {}
    for key in required:
        value = source_paths.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"BLOCKED_PIT_SOURCE:{key}_path")
        path = Path(value).resolve()
        if not path.is_file():
            raise RuntimeError(f"BLOCKED_PIT_SOURCE:{key}_missing")
        expected = source_paths.get(f"{key}_sha256")
        if expected != _sha256_file(path):
            raise RuntimeError(f"BLOCKED_PIT_SOURCE:{key}_hash")
        paths[key] = path
    try:
        bars_manifest = _load_json(paths["bars_manifest"])
        ca_manifest = _load_json(paths["corporate_actions_manifest"])
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("BLOCKED_PIT_SOURCE:manifest_unreadable") from exc
    bars_path = Path(str(bars_manifest.get("dataset"))).resolve()
    ca_path = Path(str(ca_manifest.get("dataset"))).resolve()
    if bars_path != paths["bars"] or ca_path != paths["corporate_actions"]:
        raise RuntimeError("BLOCKED_PIT_SOURCE:manifest_path")
    for manifest, key in ((bars_manifest, "bars"), (ca_manifest, "corporate_actions")):
        if manifest.get("sha256") != _sha256_file(paths[key]) or manifest.get("byte_size") != paths[key].stat().st_size:
            raise RuntimeError(f"BLOCKED_PIT_SOURCE:{key}_manifest_hash")
    lifetime_path = Path(str(bars_manifest.get("lifetime_table"))).resolve()
    if lifetime_path != paths["lifetimes"] or bars_manifest.get("lifetime_table_hash") != _sha256_file(
        paths["lifetimes"]
    ):
        raise RuntimeError("BLOCKED_PIT_SOURCE:lifetimes_manifest_hash")
    last_date = str(bars_manifest.get("last_date", ""))
    if not last_date or last_date[:10] > R46_HISTORICAL_CUTOFF[:10]:
        raise RuntimeError("BLOCKED_PIT_SOURCE:bars_post_cutoff")
    return {key: str(value) for key, value in source_paths.items()}


def _validate_v5_contract_binding(
    manifest: Mapping[str, Any], package: R46PackageInputs, incumbent: R46IncumbentInputs, *, repo_root: Path
) -> None:
    """Bind the runtime V5 inputs to the static governance and package pins."""

    data_contract = manifest.get("data_contract")
    contract = data_contract.get("incumbent_data_contract") if isinstance(data_contract, Mapping) else None
    package_contract = package.package.get("incumbent_dependencies")
    if not isinstance(contract, Mapping) or package_contract != contract:
        raise RuntimeError("BLOCKED_PIT_SOURCE:governance_package_contract")
    source_paths = incumbent.source_paths
    if source_paths.get("historical_cutoff") != R46_HISTORICAL_CUTOFF:
        raise RuntimeError("BLOCKED_PIT_SOURCE:historical_cutoff")
    for key in ("bars", "lifetimes", "corporate_actions", "bars_manifest", "corporate_actions_manifest", "seam"):
        spec = contract.get(key)
        actual_path = source_paths.get(key)
        if not isinstance(spec, Mapping) or not isinstance(actual_path, str):
            raise RuntimeError(f"BLOCKED_PIT_SOURCE:contract:{key}")
        declared_path = spec.get("path")
        if not isinstance(declared_path, str) or Path(actual_path).resolve() != (repo_root / declared_path).resolve():
            raise RuntimeError(f"BLOCKED_PIT_SOURCE:contract_path:{key}")
        if spec.get("sha256") != source_paths.get(f"{key}_sha256"):
            raise RuntimeError(f"BLOCKED_PIT_SOURCE:contract_hash:{key}")
    for alias, key in (
        ("bars_manifest_sha256", "bars_manifest"),
        ("corporate_actions_manifest_sha256", "corporate_actions_manifest"),
    ):
        if contract.get(alias) != (contract.get(key, {}) or {}).get("sha256"):
            raise RuntimeError(f"BLOCKED_PIT_SOURCE:contract_alias:{alias}")


def build_r46_market_dataset(
    package: R46PackageInputs,
    incumbent: R46IncumbentInputs,
) -> MarketDataset:
    """Build one immutable combined signal/executable dataset.

    TLT event markers live only in the executable series.  They create a
    timestamp at which the native engine can make an availability-time
    decision, while the incumbent signal history remains exactly its v5 bar
    series and cannot observe a synthetic extra signal bar.
    """

    incumbent_bars = tuple(
        replace(bar, timeframe="1d") for bar in incumbent.bars if bar.symbol != "TLT" and bar.market == Market.EQUITY
    )
    signal_bars = tuple(sorted((*incumbent_bars, *package.tlt_bars), key=lambda item: (item.timestamp, item.symbol)))
    executable_bars = tuple(sorted((*signal_bars, *package.markers), key=lambda item: (item.timestamp, item.symbol)))
    if not signal_bars or not executable_bars:
        raise ValueError("r46_combined_dataset_empty")
    actions = tuple(
        sorted(
            (*[action for action in incumbent.corporate_actions if action.symbol != "TLT"], *package.tlt_dividends),
            key=lambda item: (item.timestamp, item.symbol),
        )
    )
    digest_body = {
        "incumbent_sources": dict(incumbent.source_paths),
        "package": package.package_canonical_sha256,
        "signal": [(bar.timestamp.isoformat(), bar.symbol, bar.open, bar.close, bar.volume) for bar in signal_bars],
        "executable": [
            (bar.timestamp.isoformat(), bar.symbol, bar.open, bar.close, bar.volume) for bar in executable_bars
        ],
        "actions": [(action.timestamp.isoformat(), action.symbol, action.value) for action in actions],
    }
    data_hash = hashlib.sha256(_canonical_bytes(cast(Mapping[str, Any], digest_body))).hexdigest()
    lineage = DatasetLineage(
        dataset_id=f"{RESEARCH_ID}:combined-native-v2",
        parent_dataset_ids=(RESEARCH_ID, "equity_pit_symmetric_v5"),
        provider="pinned-r46-package-and-v5-research-seam",
        source_feed="v5-incumbent-plus-r46-tlt-package",
        adjustment="split-adjusted-execution-with-explicit-cash-dividends",
        downloaded_at=datetime(2026, 9, 10, tzinfo=UTC),
        data_hash=data_hash,
    )
    return MarketDataset(
        Market.EQUITY,
        "1d",
        signal_bars,
        executable_bars,
        lineage,
        corporate_actions=actions,
    )


def _context_without_tlt(context: PortfolioContext) -> PortfolioContext:
    """Present the incumbent with its unchanged universe and no R46 signal."""

    history = {symbol: bars for symbol, bars in context.history.items() if symbol != "TLT"}
    holdings = {symbol: qty for symbol, qty in context.holdings.items() if symbol != "TLT"}
    prices = {symbol: price for symbol, price in context.prices.items() if symbol != "TLT"}
    return replace(context, history=history, holdings=holdings, prices=prices)


@dataclass(frozen=True)
class R46EventRebalancePolicy(RebalancePolicy):
    """Weekly V3 cadence plus declared event-boundary decisions."""

    extra_timestamps: frozenset[datetime] = frozenset()

    def is_period_start(self, previous: datetime | None, current: datetime) -> bool:
        return current in self.extra_timestamps or super().is_period_start(previous, current)


class _R46IncumbentStrategy(EquityResidualMomentum1dV3):
    """Control identity: exact V3 parameters, with TLT kept out of context."""

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return super().decide(_context_without_tlt(context))


class R46OverlayStrategy(EquityResidualMomentum1dV3):
    """Unchanged V3 target plus a shared-account, event-qualified TLT overlay."""

    name = RESEARCH_ID
    version = "2.0.0"
    display_name = "R46 Treasury auction unwind overlay v2"
    rebalance_policy_declared = "weekly"

    def __init__(self, schedules: Sequence[R46EventSchedule], candidate_weight: float = 0.025) -> None:
        self.schedules = tuple(sorted(schedules, key=lambda item: (item.entry_decision, item.event_id)))
        self.candidate_weight = _finite_number(candidate_weight, "candidate_weight")
        if abs(self.candidate_weight - 0.025) > _EPS:
            raise ValueError("frozen_candidate_weight")
        self.event_records: list[dict[str, object]] = []
        self._accepted_event_ids: set[str] = set()
        self._logged_entries: set[str] = set()
        self._logged_exits: set[str] = set()
        self._event_decision_timestamps = frozenset(
            timestamp for schedule in self.schedules for timestamp in (schedule.entry_decision, schedule.exit_decision)
        )
        self._last_incumbent_target: TargetPortfolio | None = None
        super().__init__(dict(FROZEN_V3_PARAMETERS))

    def _validate_parameters(self) -> None:
        super()._validate_parameters()
        if self.parameters != FROZEN_V3_PARAMETERS:
            raise ValueError("frozen_v3_parameters")

    @property
    def accepted_event_ids(self) -> frozenset[str]:
        return frozenset(self._accepted_event_ids)

    def _active_events(self, timestamp: datetime) -> tuple[R46EventSchedule, ...]:
        return tuple(
            schedule
            for schedule in self.schedules
            if schedule.event_id in self._accepted_event_ids
            and schedule.entry_decision <= timestamp < schedule.exit_decision
        )

    def _record_entry(self, schedule: R46EventSchedule, status: str, reason: str | None) -> None:
        if schedule.event_id in self._logged_entries:
            return
        self._logged_entries.add(schedule.event_id)
        self.event_records.append(
            {
                "event_id": schedule.event_id,
                "decision_timestamp": schedule.entry_decision.isoformat(),
                "entry_open": schedule.entry_open.isoformat(),
                "exit_open": schedule.exit_open.isoformat(),
                "status": status,
                "suppression_reason": reason,
                # A suppressed event contributes no desired sleeve.  Keep
                # this producer-side field canonical so the strict verifier
                # can compare it without trusting a mutable status label.
                "candidate_weight": self.candidate_weight if status == "ACCEPTED" else 0.0,
            }
        )

    def _record_exit(self, schedule: R46EventSchedule) -> None:
        if schedule.event_id in self._logged_exits:
            return
        self._logged_exits.add(schedule.event_id)
        self.event_records.append(
            {
                "event_id": schedule.event_id,
                "decision_timestamp": schedule.exit_decision.isoformat(),
                "exit_open": schedule.exit_open.isoformat(),
                "status": "EXIT_DECISION",
                "suppression_reason": None,
                "candidate_weight": 0.0,
            }
        )

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        timestamp = context.decision_timestamp
        if timestamp in self._event_decision_timestamps:
            if self._last_incumbent_target is None:
                raise RuntimeError("BLOCKED_R46_INCUMBENT_BASE_MISSING")
            # An event availability marker may add/remove only the TLT sleeve;
            # it must never trigger a new V3 ranking or a second incumbent
            # target calculation.  Re-time the frozen base solely so a TLT
            # order created at this boundary has the correct decision stamp.
            base = replace(self._last_incumbent_target, decision_timestamp=timestamp)
        else:
            base = super().decide(_context_without_tlt(context))
            self._last_incumbent_target = base
        for schedule in self.schedules:
            if schedule.entry_decision != timestamp:
                continue
            if schedule.event_id in self._accepted_event_ids:
                self._record_entry(schedule, "SUPPRESSED", "no_pyramid")
                continue
            if self._active_events(timestamp):
                self._record_entry(schedule, "SUPPRESSED", "overlapping_event_no_pyramid")
                continue
            carried_symbols = {symbol for symbol, qty in context.holdings.items() if qty > _EPS}
            incumbent_target_symbols = {weight.symbol for weight in base.weights if weight.weight > _EPS}
            projected_symbols = carried_symbols | incumbent_target_symbols | {"TLT"}
            if len(projected_symbols) > int(DEFAULT_CAPS["total_positions_max"]):
                self._record_entry(schedule, "SUPPRESSED", "total_positions_max")
                continue
            if base.invested_weight + self.candidate_weight > float(DEFAULT_CAPS["combined_gross_max"]) + _EPS:
                self._record_entry(schedule, "SUPPRESSED", "combined_gross_max")
                continue
            if base.cash_weight - self.candidate_weight < float(DEFAULT_CAPS["combined_cash_min"]) - _EPS:
                self._record_entry(schedule, "SUPPRESSED", "combined_cash_min")
                continue
            self._accepted_event_ids.add(schedule.event_id)
            self._record_entry(schedule, "ACCEPTED", None)
        for schedule in self.schedules:
            if schedule.exit_decision == timestamp and schedule.event_id in self._accepted_event_ids:
                self._record_exit(schedule)
        active = self._active_events(timestamp)
        metadata: dict[str, object] = {
            **base.metadata,
            "r46_active_event_ids": tuple(item.event_id for item in active),
            "r46_candidate_weight": self.candidate_weight,
            "r46_event_records": tuple(copy.deepcopy(self.event_records)),
        }
        if active:
            if any(weight.symbol == "TLT" for weight in base.weights):
                raise ValueError("r46_duplicate_tlt_target")
            return replace(
                base,
                weights=(*base.weights, TargetWeight("TLT", self.candidate_weight, "r46_event_episode")),
                cash_weight=base.cash_weight - self.candidate_weight,
                metadata=metadata,
            )
        return replace(base, metadata=metadata)


class _R46PortfolioBacktestEngine(TerminalAwareResearchEngine):
    """Native engine with only the event-marker completeness exception."""

    _pending: list[PortfolioOrder]
    _rebalance_number: int

    def __init__(
        self,
        *args: Any,
        event_boundaries: Sequence[datetime] = (),
        marker_timestamps: Sequence[datetime] = (),
        equity_fee_multiplier: float = 1.0,
        **kwargs: Any,
    ) -> None:
        self._r46_event_boundaries = frozenset(event_boundaries)
        self._r46_marker_timestamps = frozenset(marker_timestamps)
        # Shadow TLT cash flows when constructing the unchanged incumbent
        # context.  The native account still records the real TLT value.
        self._r46_overlay_cash_delta = 0.0
        self._r46_real_last_price: dict[str, float] = {}
        self._r46_equity_fee_multiplier = _finite_number(equity_fee_multiplier, "equity_fee_multiplier")
        if self._r46_equity_fee_multiplier <= 0:
            raise ValueError("equity_fee_multiplier_invalid")
        # The native engine's public rebalance records are the incumbent
        # control ledger.  Keep the overlay target/fills separately so the
        # equity path remains byte-for-byte comparable without dropping the
        # TLT sleeve from the economic ledger.
        self._r46_overlay_target_weights: dict[datetime, tuple[tuple[str, float], ...]] = {}
        self._r46_snapshot_audit: list[dict[str, object]] = []
        self._r46_incumbent_idempotency = IdempotencyKeyFactory(
            _R46IncumbentStrategy.name,
            _R46IncumbentStrategy.version,
        )
        strategy = args[0] if args else kwargs.get("strategy")
        schedules = getattr(strategy, "schedules", ())
        self._r46_exact_tlt_opens = {
            (schedule.entry_decision, OrderSide.BUY): schedule.entry_open for schedule in schedules
        }
        self._r46_exact_tlt_opens.update(
            {(schedule.exit_decision, OrderSide.SELL): schedule.exit_open for schedule in schedules}
        )
        self._r46_entry_decisions = frozenset(schedule.entry_decision for schedule in schedules)
        self._r46_exit_decisions = frozenset(schedule.exit_decision for schedule in schedules)
        super().__init__(*args, **kwargs)

    def _capture_r46_snapshot(self, timestamp: datetime, equity: float | None = None) -> None:
        account_equity = float(self._equity() if equity is None else equity)
        positions: dict[str, dict[str, object]] = {}
        invested = 0.0
        for symbol, position in sorted(self._positions.items()):
            mark = float(self._last_price.get(symbol, position.avg_price))
            value = float(position.qty) * mark
            invested += value
            positions[symbol] = {
                "qty": float(position.qty),
                "avg_price": float(position.avg_price),
                "owner": "r46" if symbol == "TLT" else "incumbent_v3",
                "mark": mark,
                "value": value,
                "entry_fee": float(position.entry_fee),
                "realized_pnl_accumulator": float(position.realized_pnl_accumulator),
            }
        cash_weight = float(self._cash / account_equity) if account_equity > 0 else 1.0
        self._r46_snapshot_audit.append(
            {
                "timestamp": timestamp.isoformat(),
                "cash": float(self._cash),
                "caps": dict(DEFAULT_CAPS),
                "equity": account_equity,
                "gross_exposure": invested / account_equity if account_equity > 0 else 0.0,
                "cash_weight": cash_weight,
                "holdings_count": len(positions),
                "maximum_concentration": max(
                    (float(cast(float, item["value"])) / account_equity for item in positions.values()),
                    default=0.0,
                ),
                "positions": positions,
                "realized_holdings": copy.deepcopy(positions),
                "fees": sum(float(getattr(fill, "fee", 0.0)) for fill in self._fills),
                "dividend_total": float(self._dividend_total),
            }
        )

    def _record_holdings_snapshot(self, timestamp: datetime) -> None:
        super()._record_holdings_snapshot(timestamp)
        self._capture_r46_snapshot(timestamp)

    def _finalize_r46_snapshot(self, timestamp: datetime, equity: float) -> None:
        if self._r46_snapshot_audit:
            self._r46_snapshot_audit.pop()
        self._capture_r46_snapshot(timestamp, equity)

    def _equity_fee(self, side: OrderSide, quantity: float, price: float, trade_date: date) -> float:
        fee = cast(float, super()._equity_fee(side, quantity, price, trade_date))
        if not self.equity_use_effective_dated_fees:
            return fee
        return fee * self._r46_equity_fee_multiplier

    def _incumbent_equity(self) -> float:
        tlt = self._positions.get("TLT")
        tlt_price = self._last_price.get("TLT", tlt.avg_price) if tlt is not None else 0.0
        tlt_value = 0.0 if tlt is None else tlt.qty * cast(float, tlt_price)
        return float(max(0.0, self._equity() + self._r46_overlay_cash_delta - tlt_value))

    def _build_context(self, timestamp: datetime) -> PortfolioContext:
        context = super()._build_context(timestamp)
        if isinstance(self.strategy, R46OverlayStrategy):
            return replace(context, equity=self._incumbent_equity())
        return context

    def _decide(self, timestamp: datetime) -> None:
        previous = self._previous_timestamp[self.strategy.market]
        rebalance_number = self._rebalance_number
        super()._decide(timestamp)
        if timestamp in self._r46_event_boundaries:
            # Availability markers do not establish a new V3 cadence point
            # or consume an incumbent idempotency sequence number.
            self._previous_timestamp[self.strategy.market] = previous
            self._rebalance_number = rebalance_number
            self.state.rebalance_count = rebalance_number

    def _apply_corporate_action(self, action: Any) -> None:
        before_cash = self._cash
        super()._apply_corporate_action(action)
        if action.symbol == "TLT" and action.action_type.value == "dividend":
            self._r46_overlay_cash_delta -= self._cash - before_cash

    def _fill_cycle(self, cycle: list[Bar]) -> None:
        for bar in cycle:
            if bar.timestamp not in self._r46_marker_timestamps:
                self._r46_real_last_price[bar.symbol] = bar.close
        super()._fill_cycle(cycle)

    def _apply_target(self, target: TargetPortfolio) -> None:
        """Apply only the TLT sleeve at an R46 event boundary.

        The event marker is not a new V3 decision.  Calling the base method
        would clear pending incumbent orders and recreate them with the event
        timestamp, changing the incumbent fill path.  Preserve those orders
        byte-for-byte and add/remove only the declared TLT delta.
        """
        if not isinstance(self.strategy, R46OverlayStrategy):
            super()._apply_target(target)
            return

        is_event = target.decision_timestamp in self._r46_event_boundaries
        if is_event:
            # An event marker preserves all incumbent orders and replaces
            # only a stale TLT order.  A normal V3 decision clears/recomputes
            # incumbent orders while retaining an exact-open TLT entry.
            self._pending = [order for order in self._pending if order.symbol != "TLT"]
        else:
            tlt_pending = [order for order in self._pending if order.symbol == "TLT"]
            self._pending = tlt_pending
        incumbent_equity = self._incumbent_equity()
        symbol_targets: dict[str, float] = {
            canonical_symbol(weight.symbol, self.strategy.market): weight.weight for weight in target.weights
        }
        for symbol in self._positions:
            symbol_targets.setdefault(symbol, 0.0)
        self._current_pre_trade_weights = {}
        self._expected_turnover = 0.0
        for symbol, target_weight in symbol_targets.items():
            price = (
                self._r46_real_last_price.get(symbol, self._last_price.get(symbol))
                if symbol == "TLT"
                else self._last_price.get(symbol)
            )
            if price is None or price <= 0:
                continue
            current_position = self._positions.get(symbol)
            current_qty = current_position.qty if current_position is not None else 0.0
            weight_equity = self._equity() if symbol == "TLT" else incumbent_equity
            current_weight = current_qty * price / weight_equity if weight_equity > 0 else 0.0
            self._current_pre_trade_weights[symbol] = current_weight
            if (is_event and symbol != "TLT") or (not is_event and symbol == "TLT"):
                continue
            if abs(target_weight - current_weight) < self.turnover_band:
                continue
            target_qty = target_weight * weight_equity / price
            delta = target_qty - current_qty
            if abs(delta) * price < self.minimum_order_notional:
                continue
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            self._expected_turnover += abs(target_weight - current_weight)
            self._pending.append(
                self._order_for_target(
                    target=target,
                    symbol=symbol,
                    side=side,
                    quantity=abs(delta),
                )
            )
        self.state.pending_target = dict(symbol_targets)

    def _record_rebalance(self, target: TargetPortfolio) -> None:
        """Record the incumbent ledger while retaining overlay attribution.

        A candidate target may contain a TLT sleeve, but that sleeve must not
        alter the incumbent's target/cash/pre-trade record.  The target and
        fills still remain available through the candidate event ledger.  The
        native record therefore uses the same equity denominator and fields
        as the unchanged control arm.
        """

        if not isinstance(self.strategy, R46OverlayStrategy):
            super()._record_rebalance(target)
            return

        target_weights = tuple(
            (canonical_symbol(weight.symbol, self.strategy.market), weight.weight) for weight in target.weights
        )
        overlay_weights = tuple((symbol, weight) for symbol, weight in target_weights if symbol == "TLT")
        self._r46_overlay_target_weights[target.decision_timestamp] = overlay_weights
        if target.decision_timestamp in self._r46_event_boundaries:
            # Event-boundary records are the explicit TLT attribution row;
            # keep the native total-accounting fields for requested notional
            # and residual-order accounting.  Only ordinary V3 cadence rows
            # are normalized to the incumbent control ledger below.
            super()._record_rebalance(target)
            return
        incumbent_weights = tuple(TargetWeight(symbol, weight) for symbol, weight in target_weights if symbol != "TLT")
        incumbent_target = replace(
            target,
            weights=incumbent_weights,
            cash_weight=target.cash_weight + sum(weight for _symbol, weight in overlay_weights),
        )
        original_pre_trade = self._current_pre_trade_weights
        self._current_pre_trade_weights = {
            symbol: weight for symbol, weight in original_pre_trade.items() if symbol != "TLT"
        }
        try:
            super()._record_rebalance(incumbent_target)
            record = self._rebalances[-1]
            self._rebalances[-1] = replace(record, equity_at_decision=self._incumbent_equity())
            # Preserve the full target for the resumable engine state; only
            # the public incumbent record is intentionally normalized.
            self.state.last_target = {weight.symbol: weight.weight for weight in target.weights}
        finally:
            self._current_pre_trade_weights = original_pre_trade

    def _enrich_rebalances(self) -> tuple[RebalanceRecord, ...]:
        """Enrich only incumbent records; attribute TLT in its own ledger."""

        enriched = cast(tuple[RebalanceRecord, ...], super()._enrich_rebalances())
        if not isinstance(self.strategy, R46OverlayStrategy):
            return enriched
        fills_by_decision: dict[datetime, list[Any]] = {}
        for fill in self._fills:
            fills_by_decision.setdefault(fill.decision_timestamp, []).append(fill)
        normalized: list[Any] = []
        for record in enriched:
            if record.decision_timestamp in self._r46_event_boundaries:
                normalized.append(record)
                continue
            equity = record.equity_at_decision
            target_map = dict(record.target_weights)
            pre_trade = dict(record.pre_trade_weights)
            equity_fills = [
                fill for fill in fills_by_decision.get(record.decision_timestamp, ()) if fill.symbol != "TLT"
            ]
            signed_notional: dict[str, float] = {}
            executed_notional: dict[str, float] = {}
            cost = 0.0
            for fill in equity_fills:
                sign = 1 if fill.side == OrderSide.BUY else -1
                signed_notional[fill.symbol] = signed_notional.get(fill.symbol, 0.0) + sign * fill.qty * fill.fill_price
                executed_notional[fill.symbol] = executed_notional.get(fill.symbol, 0.0) + fill.qty * fill.fill_price
                cost += fill.slippage_cost + fill.spread_cost + fill.fee
            signed_weights = {symbol: notional / equity for symbol, notional in signed_notional.items() if equity > 0}
            actual_weights = {
                symbol: pre_trade.get(symbol, 0.0) + signed_weight for symbol, signed_weight in signed_weights.items()
            }
            drift_symbols = set(target_map) | set(signed_weights)
            drift = max(
                (
                    abs(target_map.get(symbol, 0.0) - actual_weights.get(symbol, pre_trade.get(symbol, 0.0)))
                    for symbol in drift_symbols
                ),
                default=0.0,
            )
            expected_notional = sum(
                abs(target_weight - pre_trade.get(symbol, 0.0)) * equity for symbol, target_weight in target_map.items()
            )
            sold = {symbol for symbol in signed_notional if symbol not in target_map}
            expected_notional += sum(abs(pre_trade.get(symbol, 0.0)) * equity for symbol in sold)
            realized_notional = sum(abs(value) for value in signed_notional.values())
            executed_weights = tuple(
                sorted((symbol, notional / equity) for symbol, notional in executed_notional.items() if equity > 0)
            )
            normalized.append(
                replace(
                    record,
                    signed_trade_weights=tuple(sorted(signed_weights.items())),
                    post_fill_actual_weights=tuple(sorted(actual_weights.items())),
                    target_vs_actual_drift=drift,
                    unfilled_notional=max(0.0, expected_notional - realized_notional),
                    expected_turnover=(expected_notional / equity) if equity > 0 else 0.0,
                    realized_turnover=sum(abs(value) for _symbol, value in executed_weights),
                    cost=cost,
                    executed_weights=executed_weights,
                )
            )
        return tuple(normalized)

    def _order_for_target(
        self,
        *,
        target: TargetPortfolio,
        symbol: str,
        side: OrderSide,
        quantity: float,
    ) -> Any:
        """Build one event-sleeve order with the native idempotency key."""
        from parayabuck.backtest.portfolio_engine import PortfolioOrder

        idempotency = self._r46_incumbent_idempotency if symbol != "TLT" else self._idempotency
        return PortfolioOrder(
            client_order_id=idempotency.key(
                decision_timestamp=target.decision_timestamp,
                symbol=symbol,
                side=side.value,
                rebalance_number=self._rebalance_number,
            ),
            symbol=symbol,
            market=self.strategy.market,
            side=side,
            qty=quantity,
            created_at=target.decision_timestamp,
            decision_timestamp=target.decision_timestamp,
        )

    def _effective_observation_symbols(self, timestamp: datetime) -> tuple[str, ...] | None:
        if timestamp in self._r46_event_boundaries:
            # The marker is an availability boundary, not a completed
            # incumbent observation.  The normal context path still contains
            # only bars <= timestamp, and V3 itself receives TLT-filtered data.
            return None
        return cast(tuple[str, ...] | None, super()._effective_observation_symbols(timestamp))

    def _known_average_volume(self, order: Any, timeframe: str) -> float:
        """Exclude availability markers from execution-side ADV history."""

        history = self._execution_history.get((order.symbol, timeframe), ())
        known = [
            item.volume
            for item in history
            if item.timestamp <= order.created_at
            and item.timestamp not in self._r46_marker_timestamps
            and item.volume > 0
        ]
        window = known[-self.liquidity_lookback_bars :]
        return sum(window) / len(window) if window else 0.0

    def _fill_side(self, bar: Bar, side: OrderSide) -> None:
        """Allow R46 TLT orders only at their declared entry/exit open.

        A partial exact-open fill is deliberately discarded after that open;
        retaining it would create a delayed catch-up fill on a later bar.
        """

        retained: list[Any] = []
        eligible: list[Any] = []
        for order in self._pending:
            if order.symbol == "TLT" and (
                (order.side == OrderSide.BUY and order.decision_timestamp not in self._r46_entry_decisions)
                or (order.side == OrderSide.SELL and order.decision_timestamp not in self._r46_exit_decisions)
            ):
                # Weekly incumbent decisions may observe an active overlay,
                # but they cannot create a second/catch-up TLT order.
                continue
            expected = (
                self._r46_exact_tlt_opens.get((order.decision_timestamp, order.side)) if order.symbol == "TLT" else None
            )
            if expected is not None:
                if bar.timestamp < expected:
                    retained.append(order)
                    continue
                if bar.timestamp > expected:
                    continue
            eligible.append(order)
        self._pending = eligible
        fill_start = len(self._fills)
        super()._fill_side(bar, side)
        retained.extend(self._pending)
        self._pending = retained
        for fill in self._fills[fill_start:]:
            if fill.symbol != "TLT":
                continue
            cash_change = fill.qty * fill.fill_price + fill.fee
            if fill.side == OrderSide.SELL:
                cash_change = fill.qty * fill.fill_price - fill.fee
                self._r46_overlay_cash_delta -= cash_change
            else:
                self._r46_overlay_cash_delta += cash_change


def _load_r46_config(config: object | None, *, repo_root: Path = REPO, test_only: bool = False) -> object | None:
    if config is not None:
        return config
    try:
        from parayabuck.config import load_config

        return cast(object, load_config(str(Path(repo_root) / "config/settings.yaml")))
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        if test_only:
            return None
        raise RuntimeError("BLOCKED_CONFIG_SOURCE") from exc


def _backtest_costs(config: object | None, multiplier: float) -> dict[str, object]:
    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("cost_multiplier_invalid")
    backtest = getattr(config, "backtest", None)

    def configured(name: str, default: float) -> float:
        raw = getattr(backtest, name, default)
        return _finite_number(raw, f"backtest_{name}")

    def value(name: str, default: float) -> float:
        return configured(name, default) * multiplier

    effective = bool(getattr(backtest, "equity_use_effective_dated_fees", False))
    # Effective-dated rates are resolved from the official trade-date
    # schedule inside the engine.  Keep the configured flat-rate fields
    # unchanged for audit/reference purposes and apply stress through the
    # explicit ``equity_fee_multiplier`` seam instead.  Flat-rate mode still
    # scales those configured regulatory inputs directly.
    equity_value = configured if effective else value

    return {
        "slippage_bps": value("slippage_bps", 2.0),
        "spread_bps": value("spread_bps", 0.0),
        "commission": value("commission", 0.0),
        "crypto_taker_fee_bps": value("crypto_taker_fee_bps", 0.0),
        "equity_taf_per_share": equity_value("equity_taf_per_share", 0.0),
        "equity_taf_cap": equity_value("equity_taf_cap", 0.0),
        "equity_cat_per_share": equity_value("equity_cat_per_share", 0.0),
        "equity_sec_fee_rate": equity_value("equity_sec_fee_rate", 0.0),
        "equity_use_effective_dated_fees": effective,
    }


def _build_r46_universe(
    incumbent: R46IncumbentInputs,
    *,
    config: object | None,
    test_only: bool = False,
) -> tuple[object | None, str, str]:
    if config is None or not hasattr(config, "strategy"):
        if test_only:
            return None, "r46-static-fixture", "STATIC_TEST_CONTEXT"
        raise RuntimeError("BLOCKED_CONFIG_SOURCE")
    try:
        from parayabuck.backtest.product import build_product_universe
        from parayabuck.backtest.universe import hash_point_in_time_universe

        bars = [replace(bar, timeframe="1d") for bar in incumbent.bars if bar.symbol != "TLT"]
        end = max((bar.timestamp for bar in bars), default=datetime(2026, 7, 31, tzinfo=UTC))
        universe = build_product_universe(config, bars, market=Market.EQUITY, end=end, timeframe="1d")
        version = hash_point_in_time_universe(universe)
        if not getattr(universe, "snapshots", ()):
            raise ValueError("pit_universe_empty")
        return universe, version, "PIT_V5_PRODUCT_UNIVERSE"
    except (AttributeError, ImportError, OSError, TypeError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f"BLOCKED_PIT_SOURCE:{exc}") from exc


def _run_r46_engine(
    dataset: MarketDataset,
    strategy: PortfolioStrategy,
    *,
    cost_multiplier: float,
    starting_equity: float,
    config: object | None,
    universe: object | None,
    universe_version: str,
    event_boundaries: Sequence[datetime] = (),
    marker_timestamps: Sequence[datetime] = (),
    terminal_map: Mapping[str, object] | None = None,
) -> PortfolioBacktestResult:
    costs = _backtest_costs(config, cost_multiplier)
    policy = rebalance_policy_for(strategy, dict(getattr(strategy, "parameters", {})))
    if isinstance(strategy, R46OverlayStrategy):
        policy = R46EventRebalancePolicy(Market.EQUITY, "weekly", frozenset(event_boundaries))
    risk_limits = None
    maximum_allocation = 1.0
    execution_policy = None
    if config is not None and hasattr(config, "risk") and hasattr(config, "strategy"):
        risk_limits = portfolio_risk_limits_from_config(config.risk)
        maximum_allocation = _finite_number(
            getattr(getattr(config, "strategy"), "equity").maximum_allocation,
            "maximum_allocation",
        )
        execution_policy = PortfolioExecutionPolicy(
            rebalance_band_pct=_finite_number(
                getattr(getattr(config, "execution", None), "rebalance_band_pct", 0.0),
                "rebalance_band_pct",
            ),
            minimum_order_notional=_finite_number(
                getattr(getattr(config, "execution", None), "minimum_order_notional", 0.0),
                "minimum_order_notional",
            ),
        )
    engine = _R46PortfolioBacktestEngine(
        strategy,
        policy,
        starting_equity=_finite_number(starting_equity, "starting_equity"),
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        liquidity_lookback_bars=20,
        turnover_band=float(getattr(getattr(config, "execution", None), "rebalance_band_pct", 0.0)),
        minimum_order_notional=float(getattr(getattr(config, "execution", None), "minimum_order_notional", 0.0)),
        execution_policy=execution_policy,
        risk_limits=risk_limits,
        maximum_allocation=maximum_allocation,
        universe=universe,
        universe_version=universe_version,
        allow_static_universe=universe is None,
        slippage_bps=cast(float, costs["slippage_bps"]),
        spread_bps=cast(float, costs["spread_bps"]),
        commission=cast(float, costs["commission"]),
        crypto_taker_fee_bps=cast(float, costs["crypto_taker_fee_bps"]),
        equity_taf_per_share=cast(float, costs["equity_taf_per_share"]),
        equity_taf_cap=cast(float, costs["equity_taf_cap"]),
        equity_cat_per_share=cast(float, costs["equity_cat_per_share"]),
        equity_sec_fee_rate=cast(float, costs["equity_sec_fee_rate"]),
        equity_use_effective_dated_fees=cast(bool, costs["equity_use_effective_dated_fees"]),
        equity_fee_multiplier=cost_multiplier,
        terminal_map=dict(terminal_map or {}),
        policy="T1",
        event_boundaries=event_boundaries,
        marker_timestamps=marker_timestamps,
    )
    result = engine.run_dataset(dataset, liquidate_at_end=True)
    _R46_ENGINE_AUDIT_HISTORY[id(result)] = tuple(engine._holdings_history)
    if isinstance(strategy, R46OverlayStrategy):
        _R46_ENGINE_OVERLAY_TARGETS[id(result)] = dict(engine._r46_overlay_target_weights)
    else:
        _R46_ENGINE_OVERLAY_TARGETS.pop(id(result), None)
    if engine._r46_snapshot_audit and result.curve_timestamps:
        engine._finalize_r46_snapshot(result.curve_timestamps[-1], result.ending_equity)
        _R46_ENGINE_SNAPSHOT_AUDIT[id(result)] = tuple(copy.deepcopy(engine._r46_snapshot_audit))
    return result


def _result_metrics(result: PortfolioBacktestResult) -> dict[str, object]:
    """Serialize only deterministic result metrics used by the report."""

    return {
        "starting_equity": result.starting_equity,
        "ending_equity": result.ending_equity,
        "total_return": result.total_return,
        "benchmark_return": result.benchmark_return,
        "excess_return": result.excess_return,
        "gross_return": result.gross_return,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "cagr": result.cagr,
        "annualized_volatility": result.annualized_volatility,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "turnover": result.turnover,
        "fee_total": result.fee_total,
        "slippage_cost_total": result.slippage_cost_total,
        "spread_cost_total": result.spread_cost_total,
        "dividend_total": result.dividend_total,
        "average_holdings_count": result.average_holdings_count,
        "average_cash_ratio": result.average_cash_ratio,
        "maximum_concentration": result.maximum_concentration,
        "fill_count": len(result.fills),
        "rebalance_count": result.rebalance_count,
        "closed_positions": result.closed_positions,
        "duration_days": result.duration_days,
    }


def _rebalances_to_decisions(
    result: PortfolioBacktestResult,
    *,
    desired_overlay_weights: Mapping[datetime, Sequence[tuple[str, float]]] | None = None,
) -> list[dict[str, object]]:
    decisions: list[dict[str, object]] = []
    for record in result.rebalances:
        targets = [
            {
                "symbol": symbol,
                "weight": weight,
                "owner": "r46" if symbol == "TLT" else "incumbent_v3",
                "reason": "r46_event_episode" if symbol == "TLT" else "unchanged_incumbent_v3",
            }
            for symbol, weight in record.target_weights
        ]
        desired_targets = list(targets)
        applied_symbols = {str(item["symbol"]) for item in desired_targets}
        for symbol, weight in sorted(
            (desired_overlay_weights or {}).get(record.decision_timestamp, ()),
            key=lambda item: item[0],
        ):
            if symbol in applied_symbols:
                continue
            desired_targets.append(
                {
                    "symbol": symbol,
                    "weight": weight,
                    "owner": "r46" if symbol == "TLT" else "incumbent_v3",
                    "reason": "r46_event_episode" if symbol == "TLT" else "unchanged_incumbent_v3",
                }
            )
        decisions.append(
            {
                "decision_timestamp": record.decision_timestamp.isoformat(),
                "signal_asof_timestamp": record.decision_timestamp.isoformat(),
                "target_weights": targets,
                "desired_target_weights": desired_targets,
                "desired_target_cash_weight": max(
                    0.0,
                    1.0 - sum(float(item["weight"]) for item in desired_targets),
                ),
                "suppression_reason": None,
                "ownership_attribution": {
                    "r46": ["TLT"] if any(item["symbol"] == "TLT" for item in targets) else [],
                    "incumbent_v3": [item["symbol"] for item in targets if item["symbol"] != "TLT"],
                },
                "target_cash_weight": record.target_cash_weight,
                "unfilled_notional": record.unfilled_notional,
                "rejected_notional": record.rejected_notional,
            }
        )
    return decisions


def _bind_dividend_events_to_sources(
    ledger: dict[str, object],
    *,
    package: R46PackageInputs,
    source_paths: Mapping[str, str] | None = None,
) -> None:
    """Attach immutable package/V5 row identities to native dividend events."""

    package_rows_by_key: dict[str, tuple[str, str]] = {}
    package_rows_by_action: dict[tuple[str, float], tuple[str, str]] = {}
    v5_rows_by_action: dict[tuple[str, str, float], tuple[str, str]] = {}
    package_receipts = package.package.get("receipts") if isinstance(package.package, Mapping) else None
    market_receipt = package_receipts.get("alpaca_market_data") if isinstance(package_receipts, Mapping) else None
    if isinstance(market_receipt, Mapping) and isinstance(market_receipt.get("dividends_source"), Mapping):
        for row in cast(Sequence[object], market_receipt["dividends_source"].get("row_hashes", ())):
            if isinstance(row, Mapping) and isinstance(row.get("duplicate_event_key"), str) and isinstance(
                row.get("source_document_hash"), str
            ):
                source_hash = str(row["source_document_hash"])
                package_rows_by_key[str(row["duplicate_event_key"])] = (source_hash, source_hash)
    try:
        artifacts = package.package.get("artifacts") if isinstance(package.package, Mapping) else None
        dividend_artifact = artifacts.get("dividends") if isinstance(artifacts, Mapping) else None
        dividend_path_value = dividend_artifact.get("path") if isinstance(dividend_artifact, Mapping) else None
        dividend_path = (package.root / str(dividend_path_value)).resolve()
        if dividend_path.is_file():
            with dividend_path.open(newline="", encoding="utf-8") as handle:
                for raw_row in csv.DictReader(handle):
                    key = str(raw_row.get("duplicate_event_key", ""))
                    source_hash = str(raw_row.get("source_document_hash", ""))
                    if not key or not source_hash:
                        continue
                    row_hash = hashlib.sha256(_canonical_bytes(raw_row)).hexdigest()
                    binding = (source_hash, row_hash)
                    package_rows_by_key[key] = binding
                    timestamp = str(raw_row.get("event_timestamp", ""))
                    if timestamp:
                        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).isoformat()
                    amount = _finite_number(raw_row.get("cash_or_ratio"), "dividend_amount")
                    package_rows_by_action[(timestamp, amount)] = binding
    except (OSError, TypeError, ValueError, csv.Error):
        package_rows_by_action = {}
    try:
        corporate_actions_path = Path(str((source_paths or {}).get("corporate_actions", ""))).resolve()
        if corporate_actions_path.is_file():
            with corporate_actions_path.open(newline="", encoding="utf-8") as handle:
                for raw_row in csv.DictReader(handle):
                    if raw_row.get("action_type") != "cash_dividends" or not raw_row.get("ex_date"):
                        continue
                    amount = _finite_number(
                        raw_row.get("amount") or raw_row.get("rate") or raw_row.get("cash_amount"),
                        "v5_dividend_amount",
                    )
                    if amount <= 0:
                        continue
                    event_timestamp = datetime.fromisoformat(f"{raw_row['ex_date']}T00:00:00+00:00").isoformat()
                    symbol = str(raw_row.get("symbol", ""))
                    raw_id = str(raw_row.get("raw_id", "")).strip()
                    source_key = f"v5:{raw_id or symbol}:{raw_row['ex_date']}"
                    row_hash = hashlib.sha256(_canonical_bytes(raw_row)).hexdigest()
                    v5_rows_by_action[(symbol, event_timestamp, amount)] = (source_key, row_hash)
    except (OSError, TypeError, ValueError, KeyError, csv.Error):
        v5_rows_by_action = {}
    events_for_binding = cast(list[dict[str, object]], ledger.get("dividend_events", []))
    package_row_values = list(sorted(package_rows_by_key.items()))
    for index, event in enumerate(events_for_binding):
        event_key = event.get("duplicate_event_key") or event.get("event_id")
        if isinstance(event_key, str) and event_key in package_rows_by_key:
            bound_key, (_document_hash, bound_hash) = event_key, package_rows_by_key[event_key]
        elif (
            str(event.get("timestamp", "")),
            _finite_number(event.get("per_share"), "dividend_per_share"),
        ) in package_rows_by_action:
            action_key = (
                str(event.get("timestamp", "")),
                _finite_number(event.get("per_share"), "dividend_per_share"),
            )
            bound_key = next(key for key, binding in package_rows_by_key.items() if binding == package_rows_by_action[action_key])
            _document_hash, bound_hash = package_rows_by_action[action_key]
        elif (
            str(event.get("symbol", "")),
            str(event.get("timestamp", "")),
            _finite_number(event.get("per_share"), "dividend_per_share"),
        ) in v5_rows_by_action:
            bound_key, bound_hash = v5_rows_by_action[
                (
                    str(event.get("symbol", "")),
                    str(event.get("timestamp", "")),
                    _finite_number(event.get("per_share"), "dividend_per_share"),
                )
            ]
        elif len(package_row_values) == len(events_for_binding) and index < len(package_row_values):
            bound_key, (_document_hash, bound_hash) = package_row_values[index]
        else:
            continue
        event["source_event_key"] = bound_key
        event["source_row_sha256"] = bound_hash


def _price_at(dataset: MarketDataset, symbol: str, timestamp: datetime) -> float | None:
    for bar in dataset.executable_bars:
        if bar.symbol == symbol and bar.timestamp == timestamp:
            return cast(float, bar.open)
    return None


def _latest_real_close_at(dataset: MarketDataset, symbol: str, timestamp: datetime) -> float | None:
    """Return the latest non-marker signal close available at a decision."""

    candidates = [
        bar
        for bar in dataset.signal_bars
        if bar.symbol == symbol and bar.timestamp <= timestamp
    ]
    if not candidates:
        return None
    return cast(float, max(candidates, key=lambda bar: bar.timestamp).close)


def _event_order_intents(
    dataset: MarketDataset,
    result: PortfolioBacktestResult,
    strategy: R46OverlayStrategy,
    *,
    turnover_band: float = 0.005,
    minimum_order_notional: float = 10.0,
) -> dict[str, dict[str, float | bool]]:
    """Replay the two TLT order intents using the native target semantics.

    The engine sizes an order from target-minus-current quantity.  A target
    sleeve is not itself an order: the turnover band and minimum notional can
    suppress a delta, and a partially filled order does not get a catch-up
    order on a later bar.  Keep those facts explicit in the persisted event
    contract so a verifier can reconstruct them without rerunning the engine.
    """

    fills = _native_fill_rows(result)

    def quantity_at(timestamp: datetime) -> float:
        quantity = 0.0
        for raw_fill in fills:
            fill_timestamp = datetime.fromisoformat(str(raw_fill["timestamp"]))
            if fill_timestamp > timestamp or raw_fill["symbol"] != "TLT":
                continue
            signed = 1.0 if raw_fill["side"] == "BUY" else -1.0
            quantity += signed * _finite_number(raw_fill["qty"], "fill_qty")
        return max(0.0, quantity)

    def decision_equity(timestamp: datetime) -> float:
        for rebalance in result.rebalances:
            if rebalance.decision_timestamp == timestamp:
                return _finite_number(rebalance.equity_at_decision, "rebalance_equity")
        return _finite_number(result.starting_equity, "starting_equity")

    intents: dict[str, dict[str, float | bool]] = {}
    for schedule in strategy.schedules:
        if schedule.event_id not in strategy.accepted_event_ids:
            continue
        entry_equity = decision_equity(schedule.entry_decision)
        entry_reference = _latest_real_close_at(dataset, "TLT", schedule.entry_decision)
        entry_current = quantity_at(schedule.entry_decision)
        entry_target = (
            strategy.candidate_weight * entry_equity / entry_reference
            if entry_reference is not None and entry_reference > 0
            else 0.0
        )
        entry_delta = entry_target - entry_current
        entry_current_weight = entry_current * entry_reference / entry_equity if entry_reference and entry_equity > 0 else 0.0
        entry_required = bool(
            entry_reference
            and entry_equity > 0
            and abs(strategy.candidate_weight - entry_current_weight) >= turnover_band
            and abs(entry_delta) * entry_reference >= minimum_order_notional
        )

        exit_equity = decision_equity(schedule.exit_decision)
        exit_reference = _latest_real_close_at(dataset, "TLT", schedule.exit_decision)
        exit_current = quantity_at(schedule.exit_decision)
        exit_current_weight = exit_current * exit_reference / exit_equity if exit_reference and exit_equity > 0 else 0.0
        exit_delta = -exit_current
        exit_required = bool(
            exit_reference
            and exit_equity > 0
            and exit_current_weight >= turnover_band
            and abs(exit_delta) * exit_reference >= minimum_order_notional
        )
        intents[schedule.event_id] = {
            "entry_target_qty": entry_target,
            "entry_current_qty": entry_current,
            "entry_requested_qty": abs(entry_delta) if entry_required else 0.0,
            "entry_order_required": entry_required,
            "exit_target_qty": 0.0,
            "exit_current_qty": exit_current,
            "exit_requested_qty": abs(exit_delta) if exit_required else 0.0,
            "exit_order_required": exit_required,
        }
    return intents


def _economic_rejections(
    dataset: MarketDataset,
    result: PortfolioBacktestResult,
    strategy: R46OverlayStrategy | None,
    *,
    turnover_band: float = 0.005,
    minimum_order_notional: float = 10.0,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Persist residual orders as explicit rejected/unfilled records."""

    if strategy is None:
        return [], []
    fills = _native_fill_rows(result)
    intents = _event_order_intents(
        dataset,
        result,
        strategy,
        turnover_band=turnover_band,
        minimum_order_notional=minimum_order_notional,
    )
    rejected: list[dict[str, object]] = []
    unfilled: list[dict[str, object]] = []
    for schedule in strategy.schedules:
        if schedule.event_id not in strategy.accepted_event_ids:
            continue
        buy_qty = sum(
            _finite_number(row["qty"], "fill_qty")
            for row in fills
            if row["symbol"] == "TLT" and row["side"] == "BUY" and row["timestamp"] == schedule.entry_open.isoformat()
        )
        sell_qty = sum(
            _finite_number(row["qty"], "fill_qty")
            for row in fills
            if row["symbol"] == "TLT" and row["side"] == "SELL" and row["timestamp"] == schedule.exit_open.isoformat()
        )
        intent = intents[schedule.event_id]
        entry_price = _latest_real_close_at(dataset, "TLT", schedule.entry_decision)
        exit_price = _price_at(dataset, "TLT", schedule.exit_open)
        if entry_price is None:
            continue
        requested = float(intent["entry_requested_qty"])
        if buy_qty + _EPS < requested:
            residual = requested - buy_qty
            rejected.append(
                {
                    "timestamp": schedule.entry_open.isoformat(),
                    "symbol": "TLT",
                    "side": "BUY",
                    "qty": residual,
                    "price": entry_price,
                    "fee": 0.0,
                    "reference_price": entry_price,
                    "slippage_cost": 0.0,
                    "spread_cost": 0.0,
                    "owner": "r46",
                    "decision_timestamp": schedule.entry_decision.isoformat(),
                    "client_order_id": f"r46-{schedule.event_id}-entry-remainder",
                    "status": "REJECTED",
                    "reason": "unfilled_entry",
                    "notional": residual * entry_price,
                }
            )
        if bool(intent["exit_order_required"]) and exit_price is not None and buy_qty - sell_qty > _EPS:
            residual = buy_qty - sell_qty
            row: dict[str, object] = {
                "timestamp": schedule.exit_open.isoformat(),
                "symbol": "TLT",
                "side": "SELL",
                "qty": residual,
                "price": exit_price,
                "fee": 0.0,
                "reference_price": exit_price,
                "slippage_cost": 0.0,
                "spread_cost": 0.0,
                "owner": "r46",
                "decision_timestamp": schedule.exit_decision.isoformat(),
                "client_order_id": f"r46-{schedule.event_id}-exit-remainder",
                "status": "REJECTED",
                "reason": "unfilled_exit",
                "notional": residual * exit_price,
            }
            rejected.append(row)
            unfilled.append(
                {
                    "event_id": schedule.event_id,
                    "symbol": "TLT",
                    "requested_qty": float(intent["exit_requested_qty"]),
                    "filled_qty": sell_qty,
                    "reason": "unfilled_exit",
                    "owner": "r46",
                    "exit_open": schedule.exit_open.isoformat(),
                }
            )
    return rejected, unfilled


def _event_fill_statuses(
    dataset: MarketDataset,
    result: PortfolioBacktestResult,
    strategy: R46OverlayStrategy | None,
    *,
    turnover_band: float = 0.005,
    minimum_order_notional: float = 10.0,
) -> list[dict[str, object]]:
    """Classify each accepted event using only its two mandated open fills."""

    if strategy is None:
        return []
    fills = _native_fill_rows(result)
    intents = _event_order_intents(
        dataset,
        result,
        strategy,
        turnover_band=turnover_band,
        minimum_order_notional=minimum_order_notional,
    )
    rows: list[dict[str, object]] = []
    for schedule in strategy.schedules:
        if schedule.event_id not in strategy.accepted_event_ids:
            continue
        buy_qty = sum(
            _finite_number(row["qty"], "fill_qty")
            for row in fills
            if row["symbol"] == "TLT" and row["side"] == "BUY" and row["timestamp"] == schedule.entry_open.isoformat()
        )
        sell_qty = sum(
            _finite_number(row["qty"], "fill_qty")
            for row in fills
            if row["symbol"] == "TLT" and row["side"] == "SELL" and row["timestamp"] == schedule.exit_open.isoformat()
        )
        intent = intents[schedule.event_id]
        requested_qty = float(intent["entry_requested_qty"])
        exit_requested_qty = float(intent["exit_requested_qty"])
        entry_status = "UNFILLED" if buy_qty <= _EPS else "FULL" if buy_qty + _EPS >= requested_qty > 0 else "PARTIAL"
        exit_status = (
            "UNFILLED"
            if sell_qty <= _EPS
            else "FULL"
            if exit_requested_qty > 0 and sell_qty + _EPS >= exit_requested_qty
            else "PARTIAL"
        )
        rows.append(
            {
                "event_id": schedule.event_id,
                "entry_open": schedule.entry_open.isoformat(),
                "exit_open": schedule.exit_open.isoformat(),
                "requested_qty": requested_qty,
                "entry_target_qty": float(intent["entry_target_qty"]),
                "entry_current_qty": float(intent["entry_current_qty"]),
                "entry_order_required": bool(intent["entry_order_required"]),
                "exit_target_qty": float(intent["exit_target_qty"]),
                "exit_current_qty": float(intent["exit_current_qty"]),
                "exit_requested_qty": exit_requested_qty,
                "exit_order_required": bool(intent["exit_order_required"]),
                "entry_filled_qty": buy_qty,
                "exit_filled_qty": sell_qty,
                "entry_status": entry_status,
                "exit_status": exit_status,
                "eligible_episode": entry_status == "FULL" and exit_status == "FULL",
            }
        )
    return rows


def _economic_ledger_payload(
    dataset: MarketDataset,
    result: PortfolioBacktestResult,
    *,
    strategy: R46OverlayStrategy | None,
    package: R46PackageInputs,
    cost_multiplier: float,
    arm: str,
    manifest_sha256: str,
    gate_sha256: str,
    universe_status: str,
    universe_version: str,
    source_paths: Mapping[str, str] | None = None,
    config: object | None = None,
) -> dict[str, object]:
    ledger = _native_ledger_payload(dataset, result, cost_contract=_cost_contract(config, cost_multiplier))
    # Economic residuals are verifier-owned replay observations.  The native
    # smoke schema retains its test-only rejection arrays, but publishing the
    # copied arrays here would create a second forgeable authority surface.
    ledger.pop("rejected_orders", None)
    ledger.pop("unfilled_exits", None)
    raw_rebalances = ledger.get("rebalances")
    if isinstance(raw_rebalances, list):
        for raw_rebalance in raw_rebalances:
            if isinstance(raw_rebalance, dict):
                for field in (
                    "target_weights",
                    "reason",
                    "universe_version",
                    "universe_snapshot_id",
                    "risk_overlay_halted",
                    "risk_overlay_reasons",
                ):
                    raw_rebalance.pop(field, None)
    execution_config = getattr(config, "execution", None)
    turnover_band = _finite_number(
        getattr(execution_config, "rebalance_band_pct", 0.005),
        "rebalance_band_pct",
    )
    minimum_order_notional = _finite_number(
        getattr(execution_config, "minimum_order_notional", 10.0),
        "minimum_order_notional",
    )
    fill_statuses = _event_fill_statuses(
        dataset,
        result,
        strategy,
        turnover_band=turnover_band,
        minimum_order_notional=minimum_order_notional,
    )
    ledger["audit_context"] = {
        **cast(Mapping[str, object], ledger.get("audit_context", {})),
        "turnover_band": turnover_band,
        "minimum_order_notional": minimum_order_notional,
    }
    decisions = _rebalances_to_decisions(
        result,
        desired_overlay_weights=_R46_ENGINE_OVERLAY_TARGETS.get(id(result)),
    )
    if strategy is not None:
        event_records = copy.deepcopy(strategy.event_records)
    else:
        event_records = []
    normalized_source_paths: dict[str, object] = {}
    source_root = package.root.resolve()
    for key, value in (source_paths or {}).items():
        if isinstance(value, str) and key not in {"historical_cutoff"} and not key.endswith("_sha256"):
            candidate = Path(value)
            resolved = (candidate if candidate.is_absolute() else source_root / candidate).resolve()
            try:
                normalized_source_paths[key] = str(resolved.relative_to(source_root))
            except ValueError as exc:
                raise RuntimeError("BLOCKED_PIT_SOURCE:output_path_outside_repo") from exc
        else:
            normalized_source_paths[key] = value
    package_rows_by_key: dict[str, tuple[str, str]] = {}
    package_rows_by_action: dict[tuple[str, float], tuple[str, str]] = {}
    v5_rows_by_action: dict[tuple[str, str, float], tuple[str, str]] = {}
    package_receipts = package.package.get("receipts") if isinstance(package.package, Mapping) else None
    market_receipt = package_receipts.get("alpaca_market_data") if isinstance(package_receipts, Mapping) else None
    if isinstance(market_receipt, Mapping) and isinstance(market_receipt.get("dividends_source"), Mapping):
        for row in cast(Sequence[object], market_receipt["dividends_source"].get("row_hashes", ())):
            if isinstance(row, Mapping) and isinstance(row.get("duplicate_event_key"), str) and isinstance(
                row.get("source_document_hash"), str
            ):
                source_hash = str(row["source_document_hash"])
                package_rows_by_key[str(row["duplicate_event_key"])] = (source_hash, source_hash)
    # The package receipt pins the official document, while the CSV row itself
    # is the authoritative replay row.  Bind both identities when the
    # package's five-artifact directory is present; synthetic unit fixtures do
    # not have this file and intentionally remain on the explicit test-only
    # structural path.
    try:
        artifacts = package.package.get("artifacts") if isinstance(package.package, Mapping) else None
        dividend_artifact = artifacts.get("dividends") if isinstance(artifacts, Mapping) else None
        dividend_path_value = dividend_artifact.get("path") if isinstance(dividend_artifact, Mapping) else None
        dividend_path = (package.root / str(dividend_path_value)).resolve()
        if dividend_path.is_file():
            with dividend_path.open(newline="", encoding="utf-8") as handle:
                for raw_row in csv.DictReader(handle):
                    key = str(raw_row.get("duplicate_event_key", ""))
                    source_hash = str(raw_row.get("source_document_hash", ""))
                    if not key or not source_hash:
                        continue
                    row_hash = hashlib.sha256(_canonical_bytes(raw_row)).hexdigest()
                    binding = (source_hash, row_hash)
                    package_rows_by_key[key] = binding
                    timestamp = str(raw_row.get("event_timestamp", ""))
                    if timestamp:
                        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).isoformat()
                    amount = _finite_number(raw_row.get("cash_or_ratio"), "dividend_amount")
                    package_rows_by_action[(timestamp, amount)] = binding
    except (OSError, TypeError, ValueError, csv.Error):
        # The strict verifier will reject a production bundle without these
        # bindings; retaining the receipt-only fallback keeps small test
        # fixtures on the named test-only API.
        package_rows_by_action = {}
    try:
        corporate_actions_path = Path(str((source_paths or {}).get("corporate_actions", ""))).resolve()
        if corporate_actions_path.is_file():
            with corporate_actions_path.open(newline="", encoding="utf-8") as handle:
                for raw_row in csv.DictReader(handle):
                    if raw_row.get("action_type") != "cash_dividends" or not raw_row.get("ex_date"):
                        continue
                    amount = _finite_number(
                        raw_row.get("amount") or raw_row.get("rate") or raw_row.get("cash_amount"),
                        "v5_dividend_amount",
                    )
                    if amount <= 0:
                        continue
                    event_timestamp = datetime.fromisoformat(
                        f"{raw_row['ex_date']}T00:00:00+00:00"
                    ).isoformat()
                    symbol = str(raw_row.get("symbol", ""))
                    raw_id = str(raw_row.get("raw_id", "")).strip()
                    source_key = f"v5:{raw_id or symbol}:{raw_row['ex_date']}"
                    row_hash = hashlib.sha256(_canonical_bytes(raw_row)).hexdigest()
                    v5_rows_by_action[(symbol, event_timestamp, amount)] = (source_key, row_hash)
    except (OSError, TypeError, ValueError, KeyError, csv.Error):
        v5_rows_by_action = {}
    events_for_binding = cast(list[dict[str, object]], ledger.get("dividend_events", []))
    package_row_values = list(sorted(package_rows_by_key.items()))
    for index, event in enumerate(events_for_binding):
        event_key = event.get("duplicate_event_key") or event.get("event_id")
        if isinstance(event_key, str) and event_key in package_rows_by_key:
            bound_key, (_document_hash, bound_hash) = event_key, package_rows_by_key[event_key]
        elif (
            str(event.get("timestamp", "")),
            _finite_number(event.get("per_share"), "dividend_per_share"),
        ) in package_rows_by_action:
            bound_key = next(
                key
                for key, binding in package_rows_by_key.items()
                if binding
                == package_rows_by_action[
                    (
                        str(event.get("timestamp", "")),
                        _finite_number(event.get("per_share"), "dividend_per_share"),
                    )
                ]
            )
            _document_hash, bound_hash = package_rows_by_action[
                (
                    str(event.get("timestamp", "")),
                    _finite_number(event.get("per_share"), "dividend_per_share"),
                )
            ]
        elif (
            str(event.get("symbol", "")),
            str(event.get("timestamp", "")),
            _finite_number(event.get("per_share"), "dividend_per_share"),
        ) in v5_rows_by_action:
            bound_key, bound_hash = v5_rows_by_action[
                (
                    str(event.get("symbol", "")),
                    str(event.get("timestamp", "")),
                    _finite_number(event.get("per_share"), "dividend_per_share"),
                )
            ]
        elif len(package_row_values) == len(events_for_binding) and index < len(package_row_values):
            bound_key, (_document_hash, bound_hash) = package_row_values[index]
        else:
            continue
        event["source_event_key"] = bound_key
        event["source_row_sha256"] = bound_hash
    ledger.update(
        {
            "status": ECONOMIC_LEDGER_STATUS,
            "native_engine": "TerminalAwareResearchEngine",
            "economic_execution": True,
            "economic_run": True,
            "outcome_artifacts_present": True,
            "test_only_synthetic": False,
            "arm": arm,
            "control": arm == "control",
            "cost_multiplier": cost_multiplier,
            "manifest_canonical_sha256": manifest_sha256,
            "preflight_receipt_sha256": gate_sha256,
            "package_canonical_sha256": package.package_canonical_sha256,
            "universe_status": universe_status,
            "universe_version": universe_version,
            "decisions": decisions,
            "event_fill_statuses": fill_statuses,
            "event_records": event_records,
            "event_schedules": [schedule.to_dict() for schedule in package.schedules],
            "marker_records": [
                {
                    "timestamp": marker.timestamp.isoformat(),
                    "symbol": marker.symbol,
                    "source": package.marker_sources.get(
                        next(
                            (item.event_id for item in package.schedules if item.entry_decision == marker.timestamp),
                            "",
                        ),
                    ),
                }
                for marker in package.markers
            ],
            "result_metrics": _result_metrics(result),
            "source_paths": normalized_source_paths,
            "source_path_root": ".",
            "audit_context": {
                **cast(Mapping[str, object], ledger.get("audit_context", {})),
                "source_paths_relative_to": ".",
            },
        }
    )
    ledger["ledger_sha256"] = _canonical_hash(cast(Mapping[str, Any], ledger), "ledger_sha256")
    return ledger


def _write_exclusive_payload(path: Path, payload: Mapping[str, Any]) -> str:
    encoded = (json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    _exclusive_bytes(path, encoded)
    return hashlib.sha256(encoded).hexdigest()


def _event_episode_audit_rows(
    package: R46PackageInputs,
    candidate_ledger: Mapping[str, Any],
    *,
    cost_multiplier: float,
) -> list[dict[str, object]]:
    declared_cost_multiplier = candidate_ledger.get("cost_multiplier")
    if declared_cost_multiplier is not None and (
        not isinstance(declared_cost_multiplier, (int, float))
        or abs(float(declared_cost_multiplier) - cost_multiplier) > _EPS
    ):
        raise ValueError("cost_multiplier_ledger_mismatch")
    fills = candidate_ledger.get("fills", ())
    rows: list[dict[str, object]] = []
    records = candidate_ledger.get("event_records", ())
    accepted = {
        str(row.get("event_id")) for row in records if isinstance(row, Mapping) and row.get("status") == "ACCEPTED"
    }
    fill_statuses = {
        str(row.get("event_id")): row
        for row in cast(Sequence[object], candidate_ledger.get("event_fill_statuses", ()))
        if isinstance(row, Mapping) and isinstance(row.get("event_id"), str)
    }
    for schedule in package.schedules:
        if schedule.event_id not in accepted:
            continue
        status = fill_statuses.get(schedule.event_id)
        if status is not None and (status.get("entry_status") != "FULL" or status.get("exit_status") != "FULL"):
            # A partial or unfilled leg is an explicit coverage failure, not
            # an episode with a synthetic terminal mark.
            continue
        entry = next(
            (
                row
                for row in fills
                if isinstance(row, Mapping)
                and row.get("symbol") == "TLT"
                and row.get("side") == "BUY"
                and row.get("timestamp") == schedule.entry_open.isoformat()
            ),
            None,
        )
        exit_fill = next(
            (
                row
                for row in fills
                if isinstance(row, Mapping)
                and row.get("symbol") == "TLT"
                and row.get("side") == "SELL"
                and row.get("timestamp") == schedule.exit_open.isoformat()
            ),
            None,
        )
        if not isinstance(entry, Mapping) or not isinstance(exit_fill, Mapping):
            continue
        entry_price = _finite_number(entry.get("price"), "entry_price")
        exit_price = _finite_number(exit_fill.get("price"), "exit_price")
        qty = min(_finite_number(entry.get("qty"), "entry_qty"), _finite_number(exit_fill.get("qty"), "exit_qty"))
        if entry_price <= 0 or exit_price <= 0 or qty <= 0:
            continue
        entry_fee = _finite_number(entry.get("fee", 0.0), "entry_fee")
        exit_fee = _finite_number(exit_fill.get("fee", 0.0), "exit_fee")
        dividend_credit = sum(
            _finite_number(event.get("credited_cash", 0.0), "dividend_credit")
            for event in cast(Sequence[object], candidate_ledger.get("dividend_events", ()))
            if isinstance(event, Mapping)
            and event.get("symbol") == "TLT"
            and schedule.entry_open.isoformat() <= str(event.get("timestamp")) <= schedule.exit_open.isoformat()
        )
        delta = (qty * (exit_price - entry_price) + dividend_credit - entry_fee - exit_fee) / 100_000.0
        reference_price = _finite_number(entry.get("reference_price", entry_price), "entry_reference_price")
        rows.append(
            {
                "event_id": schedule.event_id,
                "entry_decision": schedule.entry_decision.isoformat(),
                "entry_open": schedule.entry_open.isoformat(),
                "exit_open": schedule.exit_open.isoformat(),
                "reserved_notional": qty * reference_price,
                "reservation_model": "same_fill_notional_zero_rate",
                "null_cash_rate_annualized": 0.0,
                "initial_equity_denominator": 100_000.0,
                "candidate_contribution": delta,
                "exposure_matched_null": 0.0,
                "delta": delta,
            }
        )
    return rows


def _event_episode_deltas(
    package: R46PackageInputs,
    candidate_ledger: Mapping[str, Any],
    *,
    cost_multiplier: float,
) -> list[float]:
    return [
        _finite_number(row["delta"], "event_episode_delta")
        for row in _event_episode_audit_rows(package, candidate_ledger, cost_multiplier=cost_multiplier)
    ]


def _circular_bootstrap(values: Sequence[float]) -> dict[str, object]:
    observed = sum(values) / len(values) if values else 0.0
    if not values:
        return {
            "status": "BLOCKED_NO_EVENT_EPISODES",
            "repetitions": BOOTSTRAP_REPETITIONS,
            "resamples": BOOTSTRAP_REPETITIONS,
            "block_length": BOOTSTRAP_BLOCK,
            "seed": BOOTSTRAP_SEED,
            "observed": observed,
            "mean_ci_low": 0.0,
            "mean_ci_high": 0.0,
        }
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    means: list[float] = []
    for _ in range(BOOTSTRAP_REPETITIONS):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(values[(start + offset) % n] for offset in range(BOOTSTRAP_BLOCK))
        sample = sample[:n]
        means.append(sum(sample) / n)
    ordered = sorted(means)
    return {
        "status": "REPORTED_DIAGNOSTIC_ONLY",
        "repetitions": BOOTSTRAP_REPETITIONS,
        "resamples": len(means),
        "block_length": BOOTSTRAP_BLOCK,
        "seed": BOOTSTRAP_SEED,
        "observed": observed,
        "mean_ci_low": ordered[int(0.025 * len(ordered))],
        "mean_ci_high": ordered[int(0.975 * len(ordered)) - 1],
    }


def _paired_sign_flip_randomization(values: Sequence[float], *, seed: int = PERMUTATION_SEED) -> dict[str, object]:
    observed = sum(values) / len(values) if values else 0.0
    if not values:
        return {
            "method": "paired_sign_flip_randomization_under_exchangeability",
            "status": "NOT_TESTED_NO_EVENT_EPISODES",
            "repetitions": PERMUTATION_REPETITIONS,
            "resamples": PERMUTATION_REPETITIONS,
            "seed": seed,
            "observed": observed,
            "p_value": None,
            "not_tested": True,
        }
    rng = random.Random(seed)
    extreme = 0
    for _ in range(PERMUTATION_REPETITIONS):
        signs = [1 if rng.randrange(2) else -1 for _ in values]
        permuted = sum(value * sign for value, sign in zip(values, signs, strict=True)) / len(values)
        if abs(permuted) >= abs(observed) - _EPS:
            extreme += 1
    return {
        "method": "paired_sign_flip_randomization_under_exchangeability",
        "status": "REPORTED_DIAGNOSTIC_ONLY",
        "repetitions": PERMUTATION_REPETITIONS,
        "resamples": PERMUTATION_REPETITIONS,
        "seed": seed,
        "observed": observed,
        "p_value": (extreme + 1) / (PERMUTATION_REPETITIONS + 1),
    }


def _label_permutation(values: Sequence[float], *, seed: int = PERMUTATION_SEED) -> dict[str, object]:
    """Compatibility alias for the frozen paired sign-flip procedure."""

    return _paired_sign_flip_randomization(values, seed=seed)


def _holm_descriptive(pvalues: Mapping[str, float | None]) -> dict[str, object]:
    if tuple(sorted(pvalues)) != tuple(sorted(INFERENTIAL_STATISTIC_IDS)):
        raise ValueError("holm_family_ids_invalid")
    if any(value is None for value in pvalues.values()):
        return {
            "method": "holm_step_down_for_descriptive_adjusted_p_values",
            "family_size": len(INFERENTIAL_STATISTIC_IDS),
            "ids": list(INFERENTIAL_STATISTIC_IDS),
            "adjusted_p_values": {key: None for key in INFERENTIAL_STATISTIC_IDS},
            "status": "NOT_TESTED_INCOMPLETE_INFERENTIAL_DATA",
            "interpretation": "descriptive_only_no_threshold_or_selection",
        }
    ordered = sorted(
        ((float(cast(float, value)), key) for key, value in pvalues.items()),
        key=lambda item: (item[0], item[1]),
    )
    adjusted: dict[str, float] = {}
    running = 0.0
    family_size = len(INFERENTIAL_STATISTIC_IDS)
    for rank, (pvalue, key) in enumerate(ordered):
        running = max(running, min(1.0, pvalue * (family_size - rank)))
        adjusted[key] = running
    return {
        "method": "holm_step_down_for_descriptive_adjusted_p_values",
        "family_size": family_size,
        "ids": list(INFERENTIAL_STATISTIC_IDS),
        "adjusted_p_values": {key: adjusted[key] for key in INFERENTIAL_STATISTIC_IDS},
        "status": "REPORTED_DESCRIPTIVE_ONLY",
        "interpretation": "descriptive_only_no_threshold_or_selection",
    }


def _r46_regular_session_dates(package: R46PackageInputs) -> tuple[str, ...]:
    dates: set[str] = set()
    for row in package.calendar:
        if row.get("holiday_or_closure_reason") not in (None, "", "NONE"):
            continue
        value = row.get("regular_session_date", row.get("session_date"))
        if value is None:
            continue
        dates.add(_session_date(value, "regular_session_date").isoformat())
    return tuple(sorted(dates))


def _r46_shift_timestamp(
    timestamp: datetime,
    sessions: Sequence[str],
    *,
    shift_sessions: int = PLACEBO_SHIFT_SESSIONS,
) -> datetime:
    source_local = timestamp.astimezone(NEW_YORK)
    source_date = source_local.date().isoformat()
    try:
        # Availability can fall at midnight on a market closure (for example
        # a federal closure after an official release).  Anchor such a
        # timestamp to the first regular session strictly after that date;
        # the economic eligibility contract already uses this same boundary.
        if not sessions or source_date < sessions[0] or source_date > sessions[-1]:
            raise ValueError("source date outside regular-session range")
        source_index = bisect_left(sessions, source_date)
        target_date = date.fromisoformat(sessions[source_index + shift_sessions])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("BLOCKED_TIMING_PLACEBO_DATA") from exc
    target_local = datetime.combine(target_date, source_local.timetz().replace(tzinfo=None), tzinfo=NEW_YORK)
    return target_local.astimezone(UTC)


def _r46_shift_session_date(
    session_date: str,
    sessions: Sequence[str],
    *,
    shift_sessions: int = PLACEBO_SHIFT_SESSIONS,
) -> str:
    try:
        source_index = sessions.index(_session_date(session_date, "session_date").isoformat())
        return sessions[source_index + shift_sessions]
    except (ValueError, IndexError) as exc:
        raise RuntimeError("BLOCKED_TIMING_PLACEBO_DATA") from exc


def _shift_r46_package_for_placebo(package: R46PackageInputs) -> R46PackageInputs:
    """Shift event schedules by ten *regular sessions*, preserving PIT bars."""

    sessions = _r46_regular_session_dates(package)
    if not sessions:
        raise RuntimeError("BLOCKED_TIMING_PLACEBO_DATA")
    regular_opens: dict[str, datetime] = {}
    for row in package.calendar:
        if row.get("holiday_or_closure_reason") not in (None, "", "NONE"):
            continue
        session_date = _session_date(row.get("regular_session_date", row.get("session_date")), "regular_session_date")
        raw_open = row.get("regular_open_timestamp")
        if raw_open is None:
            continue
        opening = _parse_utc(raw_open, "regular_open_timestamp")
        if session_date.isoformat() in regular_opens and regular_opens[session_date.isoformat()] != opening:
            raise RuntimeError("BLOCKED_TIMING_PLACEBO_DATA")
        regular_opens[session_date.isoformat()] = opening

    def shifted_open(timestamp: datetime) -> datetime:
        source_date = timestamp.astimezone(NEW_YORK).date().isoformat()
        target_date = _r46_shift_session_date(source_date, sessions)
        expected = regular_opens.get(target_date)
        if expected is None:
            raise RuntimeError("BLOCKED_TIMING_PLACEBO_OPEN_MISMATCH")
        return expected

    shifted_schedules: list[R46EventSchedule] = []
    for schedule in package.schedules:
        shifted_future = tuple(shifted_open(item) for item in schedule.future_open_timestamps)
        shifted_entry = shifted_open(schedule.entry_open)
        shifted_exit = shifted_open(schedule.exit_open)
        shifted_available = _r46_shift_timestamp(schedule.available_at, sessions)
        shifted_entry_decision = _r46_shift_timestamp(schedule.entry_decision, sessions)
        # The exit decision is the fifth future regular open by contract.  A
        # source event may carry a non-open wall-clock timestamp (fixtures and
        # some official publication rows do), so shifting it independently can
        # break the exact open/decision linkage that the verifier replays.
        shifted_exit_decision = shifted_future[4] if len(shifted_future) >= 5 else _r46_shift_timestamp(schedule.exit_decision, sessions)
        shifted_auction_date = _r46_shift_session_date(schedule.auction_session_date, sessions)
        shifted_schedules.append(
            replace(
                schedule,
                auction_session_date=shifted_auction_date,
                available_at=shifted_available,
                entry_session_date=shifted_entry.astimezone(NEW_YORK).date().isoformat(),
                entry_open=shifted_entry,
                exit_session_date=shifted_exit.astimezone(NEW_YORK).date().isoformat(),
                exit_open=shifted_exit,
                entry_decision=shifted_entry_decision,
                exit_decision=shifted_exit_decision,
                future_open_timestamps=shifted_future,
            )
        )
    shifted_markers = tuple(
        replace(marker, timestamp=_r46_shift_timestamp(marker.timestamp, sessions)) for marker in package.markers
    )
    return replace(package, schedules=tuple(shifted_schedules), markers=shifted_markers)


def _run_timing_placebo_cell(
    package: R46PackageInputs,
    incumbent: R46IncumbentInputs,
    *,
    config: object | None,
    universe: object | None,
    universe_version: str,
    universe_status: str = "TEST_ONLY",
    manifest_sha256: str,
    gate_sha256: str,
    source_paths: Mapping[str, str] | None = None,
    cost_multiplier: float = 1.0,
) -> dict[str, object]:
    """Run the frozen +10-session timing diagnostic through the native engine.

    The placebo is an economic-shaped diagnostic cell even though its result is
    never eligible for selection.  Build each inner ledger through the same
    final-mutation/hash path as the production cost cells so the file-backed
    verifier can replay it without trusting the native result object.
    """

    if abs(float(cost_multiplier) - 1.0) > _EPS:
        raise ValueError("timing_placebo_cost_multiplier")

    shifted = _shift_r46_package_for_placebo(package)
    dataset = build_r46_market_dataset(shifted, incumbent)
    event_boundaries = tuple(
        sorted(
            {
                timestamp
                for schedule in shifted.schedules
                for timestamp in (schedule.entry_decision, schedule.exit_decision)
            }
        )
    )
    marker_timestamps = tuple(sorted(marker.timestamp for marker in shifted.markers))
    candidate_strategy = R46OverlayStrategy(shifted.schedules)
    control_strategy = _R46IncumbentStrategy(dict(FROZEN_V3_PARAMETERS))
    candidate_result = _run_r46_engine(
        dataset,
        candidate_strategy,
        cost_multiplier=cost_multiplier,
        starting_equity=100_000.0,
        config=config,
        universe=universe,
        universe_version=universe_version,
        event_boundaries=event_boundaries,
        marker_timestamps=marker_timestamps,
        terminal_map=incumbent.terminal_map,
    )
    control_result = _run_r46_engine(
        dataset,
        control_strategy,
        cost_multiplier=cost_multiplier,
        starting_equity=100_000.0,
        config=config,
        universe=universe,
        universe_version=universe_version,
        marker_timestamps=marker_timestamps,
        terminal_map=incumbent.terminal_map,
    )
    candidate_ledger = _economic_ledger_payload(
        dataset,
        candidate_result,
        strategy=candidate_strategy,
        package=shifted,
        cost_multiplier=1.0,
        arm="candidate",
        manifest_sha256=manifest_sha256,
        gate_sha256=gate_sha256,
        universe_status=universe_status,
        universe_version=universe_version,
        source_paths=source_paths or incumbent.source_paths,
        config=config,
    )
    control_ledger = _economic_ledger_payload(
        dataset,
        control_result,
        strategy=None,
        package=shifted,
        cost_multiplier=1.0,
        arm="control",
        manifest_sha256=manifest_sha256,
        gate_sha256=gate_sha256,
        universe_status=universe_status,
        universe_version=universe_version,
        source_paths=source_paths or incumbent.source_paths,
        config=config,
    )
    # Keep source bindings and result metrics inside the payload before the
    # helper computes its inner canonical hash.  The verifier compares these
    # exact records against an independently shifted package.
    shifted_dataset_digest = dataset.content_digest()
    shifted_schedule_digest = hashlib.sha256(
        _canonical_bytes({"schedules": [schedule.to_dict() for schedule in shifted.schedules]})
    ).hexdigest()
    shifted_marker_digest = hashlib.sha256(
        _canonical_bytes(
            {
                "markers": [
                    {"symbol": marker.symbol, "timestamp": marker.timestamp.isoformat(), "close": marker.close}
                    for marker in shifted.markers
                ]
            }
        )
    ).hexdigest()
    normalized_source_paths = cast(Mapping[str, object], candidate_ledger.get("source_paths", {}))
    source_binding = {
        "manifest_canonical_sha256": manifest_sha256,
        "package_canonical_sha256": shifted.package_canonical_sha256,
        "preflight_receipt_sha256": gate_sha256,
        "source_paths": copy.deepcopy(dict(normalized_source_paths)),
        "universe_status": universe_status,
        "universe_version": universe_version,
        "shifted_dataset_digest": shifted_dataset_digest,
        "shifted_schedule_digest": shifted_schedule_digest,
        "shifted_marker_digest": shifted_marker_digest,
        "shift_sessions": PLACEBO_SHIFT_SESSIONS,
    }
    values = _event_episode_deltas(shifted, candidate_ledger, cost_multiplier=1.0)
    permutation = _label_permutation(values, seed=PLACEBO_RANDOM_SEED)
    candidate_metrics = _result_metrics(candidate_result)
    control_metrics = _result_metrics(control_result)
    delta = {
        "ending_equity": _finite_number(candidate_metrics["ending_equity"], "candidate_ending_equity")
        - _finite_number(control_metrics["ending_equity"], "control_ending_equity"),
        "total_return": _finite_number(candidate_metrics["total_return"], "candidate_total_return")
        - _finite_number(control_metrics["total_return"], "control_total_return"),
        "net_return": _finite_number(candidate_metrics["net_return"], "candidate_net_return")
        - _finite_number(control_metrics["net_return"], "control_net_return"),
        "fee_total": _finite_number(candidate_metrics["fee_total"], "candidate_fee_total")
        - _finite_number(control_metrics["fee_total"], "control_fee_total"),
        "dividend_total": _finite_number(candidate_metrics["dividend_total"], "candidate_dividend_total")
        - _finite_number(control_metrics["dividend_total"], "control_dividend_total"),
    }
    status = "REPORTED_NATIVE_ENGINE_DIAGNOSTIC_ONLY" if values else "BLOCKED_TIMING_PLACEBO_NO_COMPLETE_EPISODES"
    result: dict[str, object] = {
        "status": status,
        "shift_sessions": PLACEBO_SHIFT_SESSIONS,
        "seed": PLACEBO_RANDOM_SEED,
        "native_engine": "TerminalAwareResearchEngine",
        "engine_invocations": 2,
        "cost_multiplier": 1.0,
        "manifest_canonical_sha256": manifest_sha256,
        "package_canonical_sha256": shifted.package_canonical_sha256,
        "preflight_receipt_sha256": gate_sha256,
        "universe_status": universe_status,
        "universe_version": universe_version,
        "candidate_metrics": candidate_metrics,
        "control_metrics": control_metrics,
        "candidate_ledger": candidate_ledger,
        "control_ledger": control_ledger,
        "source_binding": source_binding,
        "candidate_minus_control": delta,
        "event_episode_deltas": values,
        "event_episode_count": len(values),
        "not_tested": False,
    }
    if values:
        result["permutation"] = permutation
        result["p_value"] = permutation["p_value"]
    else:
        result["p_value"] = None
        result["blocking_reason"] = "BLOCKED_TIMING_PLACEBO_NO_COMPLETE_EPISODES"
    return result


def _timing_placebo(package: R46PackageInputs) -> dict[str, object]:
    """Return a fail-closed marker when no native placebo cell was supplied."""

    return {
        "status": "BLOCKED_TIMING_PLACEBO_ENGINE_CELL",
        "shift_sessions": PLACEBO_SHIFT_SESSIONS,
        "seed": 4603,
        "event_episode_count": 0,
        "p_value": None,
        "not_tested": False,
    }


def _ledger_curve_endpoint(
    ledger: Mapping[str, Any],
    timestamp: datetime,
    *,
    field: str,
) -> float:
    """Return one exact post-event curve value, failing on missing/duplicate endpoints."""

    timestamps = ledger.get("equity_curve_timestamps")
    values = ledger.get("equity_curve")
    if not isinstance(timestamps, Sequence) or isinstance(timestamps, (str, bytes)):
        raise ValueError(f"{field}_curve_timestamps_missing")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) != len(timestamps):
        raise ValueError(f"{field}_curve_misaligned")
    matches: list[float] = []
    for raw_timestamp, raw_value in zip(timestamps, values, strict=True):
        try:
            parsed = _parse_utc(raw_timestamp, f"{field}_curve_timestamp")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}_curve_timestamp_invalid") from exc
        if parsed == timestamp:
            matches.append(_finite_number(raw_value, f"{field}_curve_value"))
    if len(matches) != 1:
        raise ValueError(f"{field}_endpoint_missing_or_duplicate")
    return matches[0]


def _incumbent_event_comparison(
    package: R46PackageInputs,
    primary_rows: Sequence[Mapping[str, Any]],
    candidate_ledger: Mapping[str, Any],
    control_ledger: Mapping[str, Any] | None,
) -> tuple[dict[str, object], list[float]]:
    """Build the paired incumbent contrast from the persisted 1x curves."""

    if not primary_rows:
        permutation = _label_permutation(())
        return (
            {
                "status": "REPORTED_NATIVE_ENGINE_DESCRIPTIVE_ONLY",
                "native_engine": "TerminalAwareResearchEngine",
                "engine_invocations": 2,
                "candidate_metrics": {},
                "control_metrics": {},
                "candidate_total_return": None,
                "incumbent_total_return": None,
                "delta_total_return": None,
                "estimand": "candidate_minus_incumbent_event_endpoint_change",
                "initial_equity_denominator": 100_000.0,
                "aggregation": "equal_weight_over_complete_primary_event_episodes",
                "horizon": "entry_decision_to_exit_open_post_fill",
                "event_episode_count": 0,
                "rows": [],
                "equal_weight_delta": 0.0,
                "p_value": None,
                "not_tested": True,
                "permutation": permutation,
            },
            [],
        )
    if control_ledger is None:
        raise ValueError("incumbent_control_ledger_missing")
    rows: list[dict[str, object]] = []
    deltas: list[float] = []
    for primary in primary_rows:
        event_id = primary.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("incumbent_event_id_missing")
        try:
            t0 = _parse_utc(primary.get("entry_decision"), "incumbent_entry_decision")
            t1 = _parse_utc(primary.get("exit_open"), "incumbent_exit_open")
        except (TypeError, ValueError) as exc:
            raise ValueError("incumbent_endpoint_timestamp_invalid") from exc
        candidate_t0 = _ledger_curve_endpoint(candidate_ledger, t0, field=f"candidate:{event_id}:t0")
        candidate_t1 = _ledger_curve_endpoint(candidate_ledger, t1, field=f"candidate:{event_id}:t1")
        incumbent_t0 = _ledger_curve_endpoint(control_ledger, t0, field=f"incumbent:{event_id}:t0")
        incumbent_t1 = _ledger_curve_endpoint(control_ledger, t1, field=f"incumbent:{event_id}:t1")
        candidate_change = (candidate_t1 - candidate_t0) / 100_000.0
        incumbent_change = (incumbent_t1 - incumbent_t0) / 100_000.0
        delta = candidate_change - incumbent_change
        deltas.append(delta)
        rows.append(
            {
                "event_id": event_id,
                "t0": t0.isoformat(),
                "t1": t1.isoformat(),
                "candidate_t0": candidate_t0,
                "candidate_t1": candidate_t1,
                "incumbent_t0": incumbent_t0,
                "incumbent_t1": incumbent_t1,
                "candidate_change": candidate_change,
                "incumbent_change": incumbent_change,
                "delta": delta,
            }
        )
    permutation = _label_permutation(deltas, seed=PERMUTATION_SEED)
    candidate_metrics = cast(Mapping[str, Any], candidate_ledger.get("result_metrics", {}))
    control_metrics = cast(Mapping[str, Any], control_ledger.get("result_metrics", {}))
    return (
        {
            "status": "REPORTED_NATIVE_ENGINE_DESCRIPTIVE_ONLY",
            "native_engine": "TerminalAwareResearchEngine",
            "engine_invocations": 2,
            "candidate_metrics": dict(candidate_metrics),
            "control_metrics": dict(control_metrics),
            "candidate_total_return": candidate_metrics.get("total_return"),
            "incumbent_total_return": control_metrics.get("total_return"),
            "delta_total_return": (
                _finite_number(candidate_metrics.get("total_return"), "candidate_total_return")
                - _finite_number(control_metrics.get("total_return"), "incumbent_total_return")
                if "total_return" in candidate_metrics and "total_return" in control_metrics
                else None
            ),
            "estimand": "candidate_minus_incumbent_event_endpoint_change",
            "initial_equity_denominator": 100_000.0,
            "aggregation": "equal_weight_over_complete_primary_event_episodes",
            "horizon": "entry_decision_to_exit_open_post_fill",
            "event_episode_count": len(rows),
            "rows": rows,
            "equal_weight_delta": sum(deltas) / len(deltas),
            "p_value": permutation["p_value"],
            "not_tested": False,
            "permutation": permutation,
        },
        deltas,
    )


def _build_statistics(
    package: R46PackageInputs,
    cells: Sequence[Mapping[str, Any]],
    *,
    timing_placebo_cell: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    candidate_one = next((cell for cell in cells if abs(float(cell["cost_multiplier"]) - 1.0) < _EPS), None)
    if candidate_one is None:
        raise ValueError("cost_1x_cell_missing")
    candidate_ledger = cast(Mapping[str, Any], candidate_one["candidate_ledger"])
    control_ledger = candidate_one.get("control_ledger")
    if not isinstance(control_ledger, Mapping):
        control_ledger = None
    primary_rows = _event_episode_audit_rows(package, candidate_ledger, cost_multiplier=1.0)
    values = [_finite_number(row["delta"], "event_episode_delta") for row in primary_rows]
    primary_delta = sum(values) / len(values) if values else 0.0
    incumbent_comparison, _incumbent_values = _incumbent_event_comparison(
        package,
        primary_rows,
        candidate_ledger,
        cast(Mapping[str, Any] | None, control_ledger),
    )
    accepted = sum(
        isinstance(item, Mapping) and item.get("status") == "ACCEPTED"
        for item in cast(Sequence[object], candidate_ledger.get("event_records", ()))
    )
    suppressed = len(package.schedules) - accepted
    entry_records = [
        item
        for item in cast(Sequence[object], candidate_ledger.get("event_records", ()))
        if isinstance(item, Mapping) and "entry_open" in item
    ]
    accepted_ids = {str(item.get("event_id")) for item in entry_records if item.get("status") == "ACCEPTED"}
    fill_rows = [
        item for item in cast(Sequence[object], candidate_ledger.get("fills", ())) if isinstance(item, Mapping)
    ]
    fill_statuses = {
        str(item.get("event_id")): item
        for item in cast(Sequence[object], candidate_ledger.get("event_fill_statuses", ()))
        if isinstance(item, Mapping) and isinstance(item.get("event_id"), str)
    }
    complete_ids = {
        schedule.event_id
        for schedule in package.schedules
        if schedule.event_id in accepted_ids
        and fill_statuses.get(schedule.event_id, {}).get("eligible_episode") is True
        and any(
            row.get("symbol") == "TLT"
            and row.get("side") == "BUY"
            and row.get("timestamp") == schedule.entry_open.isoformat()
            for row in fill_rows
        )
        and any(
            row.get("symbol") == "TLT"
            and row.get("side") == "SELL"
            and row.get("timestamp") == schedule.exit_open.isoformat()
            for row in fill_rows
        )
    }
    # Capacity suppression is not missing data.  The package loader has
    # already proved identity, calendar, bars, and dividends; missingness is
    # therefore the accepted schedules that lack a complete entry/exit
    # episode, while capacity is reported separately.
    missing = len(accepted_ids - complete_ids)
    capacity_suppressed = sum(
        isinstance(item, Mapping)
        and item.get("status") == "SUPPRESSED"
        and str(item.get("suppression_reason", ""))
        in {"total_positions_max", "combined_gross_max", "combined_cash_min"}
        for item in entry_records
    )
    pvalue = _label_permutation(values).get("p_value")
    timing_result = dict(timing_placebo_cell) if timing_placebo_cell is not None else _timing_placebo(package)
    pvalues: dict[str, float | None] = {key: None for key in INFERENTIAL_STATISTIC_IDS}
    if isinstance(pvalue, (int, float)):
        pvalues[STATISTIC_IDS[0]] = float(pvalue)
    incumbent_pvalue = incumbent_comparison.get("p_value")
    if isinstance(incumbent_pvalue, (int, float)):
        pvalues[STATISTIC_IDS[1]] = float(incumbent_pvalue)
    cell_pvalues: dict[float, float | None] = {1.0: float(pvalue) if isinstance(pvalue, (int, float)) else None}
    for cell in cells:
        multiplier = float(cell["cost_multiplier"])
        cell_values = _event_episode_deltas(
            package,
            cast(Mapping[str, Any], cell["candidate_ledger"]),
            cost_multiplier=multiplier,
        )
        cell_pvalue = _label_permutation(cell_values).get("p_value")
        cell_pvalues[multiplier] = float(cell_pvalue) if isinstance(cell_pvalue, (int, float)) else None
        if abs(multiplier - 2.0) < _EPS:
            pvalues[STATISTIC_IDS[3]] = cell_pvalues[multiplier]
        if abs(multiplier - 3.0) < _EPS:
            pvalues[STATISTIC_IDS[4]] = cell_pvalues[multiplier]
    bootstrap = _circular_bootstrap(values)
    permutation = _label_permutation(values)
    cost_outputs = [
        {
            "id": (
                STATISTIC_IDS[0]
                if abs(float(cell["cost_multiplier"]) - 1.0) < _EPS
                else STATISTIC_IDS[3]
                if abs(float(cell["cost_multiplier"]) - 2.0) < _EPS
                else STATISTIC_IDS[4]
            ),
            "multiplier": float(cell["cost_multiplier"]),
            "candidate": cell["candidate_metrics"],
            "control": cell["control_metrics"],
            "delta_total_return": float(cast(Mapping[str, Any], cell["candidate_metrics"])["total_return"])
            - float(cast(Mapping[str, Any], cell["control_metrics"])["total_return"]),
            "p_value": cell_pvalues.get(float(cell["cost_multiplier"])),
            "not_tested": abs(float(cell["cost_multiplier"]) - 2.0) > _EPS
            and abs(float(cell["cost_multiplier"]) - 3.0) > _EPS,
        }
        for cell in cells
    ]
    return {
        "primary_estimand": {
            "id": STATISTIC_IDS[0],
            "estimand": "initial_equity_portfolio_contribution",
            "initial_equity_denominator": 100_000.0,
            "aggregation": "equal_weight_over_eligible_event_episodes",
            "contrast": "candidate_minus_exposure_matched_null",
            "horizon": "five_open_to_open_intervals",
            "event_episode_count": len(values),
            "equal_weight_delta": primary_delta,
            "null_cash_rate_annualized": 0.0,
            "null_reservation_model": "cash_reserved_same_notional_zero_rate",
            "null_reservation_proof": True,
            "p_value": pvalue if isinstance(pvalue, (int, float)) else None,
            "not_tested": not isinstance(pvalue, (int, float)),
            "rows": primary_rows,
            "null_ledger": {
                "schema": "r46-exposure-matched-null-ledger-v1",
                "reservation_model": "cash_reserved_same_notional_zero_rate",
                "cash_rate_annualized": 0.0,
                "initial_equity_denominator": 100_000.0,
                "same_reservation_decisions": True,
                "rows": [
                    {
                        "event_id": row["event_id"],
                        "entry_decision": row["entry_decision"],
                        "reserved_notional": row["reserved_notional"],
                        "null_contribution": row["exposure_matched_null"],
                        "cash_rate_annualized": row["null_cash_rate_annualized"],
                    }
                    for row in primary_rows
                ],
            },
        },
        "unchanged_incumbent_comparison": {"id": STATISTIC_IDS[1], **incumbent_comparison},
        "timing_placebo_plus_10_sessions": {
            "id": STATISTIC_IDS[2],
            **timing_result,
        },
        "cost_outputs": cost_outputs,
        "capacity": {
            "id": STATISTIC_IDS[5],
            "inferential": False,
            "p_value": None,
            "not_tested": True,
            "event_count": len(package.schedules),
            "accepted_count": accepted,
            "suppressed_count": max(0, suppressed),
            "suppression_rate": (suppressed / len(package.schedules)) if package.schedules else 0.0,
            "capacity_suppressed_count": capacity_suppressed,
            "capacity_suppression_rate": (capacity_suppressed / len(package.schedules)) if package.schedules else 0.0,
            "reasons": sorted(
                {
                    str(item.get("suppression_reason"))
                    for item in cast(Sequence[object], candidate_ledger.get("event_records", ()))
                    if isinstance(item, Mapping) and item.get("suppression_reason")
                }
            ),
        },
        "missingness": {
            "id": STATISTIC_IDS[6],
            "event_count": len(package.schedules),
            "missing_or_suppressed_count": max(0, missing),
            "missing_rate": (missing / len(package.schedules)) if package.schedules else 0.0,
            "p_value": None,
            "not_tested": True,
        },
        "resampling": {"bootstrap": bootstrap, "permutation": permutation},
        "holm": _holm_descriptive(pvalues),
        "coverage_gates": {
            "minimum_event_episodes": 24,
            "minimum_holding_intervals": 120,
            "maximum_missing_rate": 0.0,
            "maximum_capacity_suppression_rate": 0.25,
            "event_episodes": len(values),
            "holding_intervals": len(values) * 5,
            "missing_rate": (missing / len(package.schedules)) if package.schedules else 0.0,
            "capacity_suppression_rate": (capacity_suppressed / len(package.schedules)) if package.schedules else 0.0,
            "fifth_open_exit_complete": all(
                schedule.holding_intervals == 5 and len(schedule.future_open_timestamps) >= 6
                for schedule in package.schedules
            ),
            "fifth_open_exit_count": sum(
                schedule.holding_intervals == 5 and len(schedule.future_open_timestamps) >= 6
                for schedule in package.schedules
            ),
            "sixth_future_open_complete": all(
                schedule.holding_intervals == 5 and len(schedule.future_open_timestamps) >= 6
                for schedule in package.schedules
            ),
        },
        "no_thresholds_or_selection": True,
    }


def _gate_paths(
    gate_path: Path, receipt: Mapping[str, Any], repo_root: Path
) -> tuple[Path, Path, Path, dict[str, Any]]:
    manifest_reference = receipt.get("manifest_path", receipt.get("governance_manifest_path"))
    package_reference = receipt.get("package_path", receipt.get("data_package_manifest_path"))
    manifest_path = _resolve_pinned_path(manifest_reference, gate_path.parent, root=repo_root)
    package_path = _resolve_pinned_path(package_reference, gate_path.parent, root=repo_root)
    manifest = _load_json(manifest_path)
    evaluation = manifest.get("evaluation_contract")
    if not isinstance(evaluation, Mapping):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    state_reference = evaluation.get("one_shot_state_path")
    if not isinstance(state_reference, str):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    state_path = _resolve_pinned_path(state_reference, repo_root, root=repo_root)
    declared_state_hash = evaluation.get("one_shot_state_sha256")
    if declared_state_hash is not None and declared_state_hash != _sha256_file(state_path):
        # Some older manifests used the canonical nested hash.  Accept it only
        # when it verifies against the state itself; never accept a caller map.
        try:
            canonical_state = _canonical_artifact_hash(state_path, "artifact_sha256")
        except ValueError:
            canonical_state = ""
        if declared_state_hash != canonical_state:
            raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    if not isinstance(
        receipt.get("manifest_sha256")
        or receipt.get("manifest_canonical_sha256")
        or receipt.get("governance_manifest_sha256"),
        str,
    ):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    if not isinstance(
        receipt.get("package_sha256")
        or receipt.get("package_canonical_sha256")
        or receipt.get("data_package_manifest_sha256"),
        str,
    ):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    return manifest_path, package_path, state_path, manifest


def _validate_economic_gate(gate: Path | str) -> tuple[Path, dict[str, Any], Path, Path, Path, dict[str, Any]]:
    gate_path = Path(gate).resolve()
    receipt = _validate_pre_run_receipt(gate_path, repo_root=REPO)
    manifest_path, package_path, state_path, manifest = _gate_paths(gate_path, receipt, REPO)
    _verify_persisted_preflight(
        manifest_path=manifest_path,
        package_path=package_path,
        receipt=receipt,
        repo_root=REPO,
        preflight_receipt_path=gate_path,
    )
    return gate_path, receipt, manifest_path, package_path, state_path, manifest


def _manifest_statistics_pins(manifest: Mapping[str, Any], root: Path) -> dict[str, object]:
    statistics = manifest.get("statistics")
    multiplicity = manifest.get("multiplicity")
    if not isinstance(statistics, Mapping) or not isinstance(multiplicity, Mapping):
        raise RuntimeError("BLOCKED_STATISTICS_ARTIFACTS")
    plan_path = _resolve_pinned_path(statistics.get("plan_path"), root)
    family_path = _resolve_pinned_path(multiplicity.get("family_registry_path"), root)
    plan_hash = _canonical_self_hash(plan_path)
    family_hash = _canonical_self_hash(family_path)
    if statistics.get("plan_sha256") != plan_hash or multiplicity.get("family_registry_sha256") != family_hash:
        raise RuntimeError("BLOCKED_STATISTICS_ARTIFACTS")
    plan = _load_json(plan_path)
    family = _load_json(family_path)
    expected = set(STATISTIC_IDS)
    primary = plan.get("primary_estimand")
    plan_ids = {str(primary.get("id"))} if isinstance(primary, Mapping) else set()
    secondary = plan.get("secondary_diagnostics")
    if isinstance(secondary, Sequence) and not isinstance(secondary, (str, bytes)):
        plan_ids.update(str(item.get("id")) for item in secondary if isinstance(item, Mapping))
    family_ids = {str(item.get("id")) for item in family.get("hypotheses", ()) if isinstance(item, Mapping)}
    if (
        plan_ids != expected
        or family_ids != expected
        or plan.get("reported_output_count") != len(STATISTIC_IDS)
        or family.get("family_size") != len(STATISTIC_IDS)
        or family.get("reported_output_count") != len(STATISTIC_IDS)
        or family.get("inferential_family_size") != len(INFERENTIAL_STATISTIC_IDS)
        or tuple(family.get("inferential_ids", ())) != INFERENTIAL_STATISTIC_IDS
        or plan.get("inferential_family", {}).get("size") != len(INFERENTIAL_STATISTIC_IDS)
        or tuple(plan.get("inferential_family", {}).get("ids", ())) != INFERENTIAL_STATISTIC_IDS
    ):
        raise RuntimeError("BLOCKED_STATISTICS_ARTIFACTS")
    return {
        "statistics_plan_path": str(plan_path),
        "statistics_plan_sha256": plan_hash,
        "family_registry_path": str(family_path),
        "family_registry_sha256": family_hash,
        "family_size": len(STATISTIC_IDS),
        "reported_output_count": len(STATISTIC_IDS),
        "inferential_family_size": len(INFERENTIAL_STATISTIC_IDS),
        "inferential_ids": list(INFERENTIAL_STATISTIC_IDS),
        "statistic_ids": list(STATISTIC_IDS),
    }


def _run_package_backed(
    *,
    gate: Path | str | Mapping[str, object] | None,
    output_dir: Path | str,
    package_path: Path | str | None = None,
    one_shot_state_path: Path | str | None = None,
    repo_root: Path | str = REPO,
    incumbent_inputs: R46IncumbentInputs | None = None,
    config: object | None = None,
    starting_equity: float = 100_000.0,
    cost_multipliers: Sequence[float] = COST_MULTIPLIERS,
    allow_marker_fallback: bool = False,
    _test_only: bool = False,
) -> dict[str, object]:
    """Run every frozen cost cell through one native shared-account engine.

    The production surface accepts only an on-disk receipt and the package
    pinned by that receipt.  All output publication happens after reservation;
    a failure leaves the reservation sidecar in place and cannot reopen it.
    """

    if not _test_only and (
        incumbent_inputs is not None
        or config is not None
        or abs(float(starting_equity) - 100_000.0) > _EPS
        or tuple(float(value) for value in cost_multipliers) != COST_MULTIPLIERS
        or allow_marker_fallback
    ):
        raise RuntimeError("BLOCKED_V2_PRODUCTION_OVERRIDE")
    if isinstance(gate, Mapping) or gate is None:
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    root = Path(repo_root).resolve()
    gate_path = Path(gate).resolve()
    receipt = _validate_pre_run_receipt(gate_path, repo_root=root)
    # Resolve the gate's references locally so an alternate caller-supplied
    # package/state path cannot silently change the frozen sample.
    manifest_reference = receipt.get("manifest_path", receipt.get("governance_manifest_path"))
    package_reference = receipt.get("package_path", receipt.get("data_package_manifest_path"))
    manifest_path = _resolve_pinned_path(manifest_reference, gate_path.parent, root=root)
    pinned_package_path = _resolve_pinned_path(package_reference, gate_path.parent, root=root)
    manifest = _load_json(manifest_path)
    if package_path is not None and Path(package_path).resolve() != pinned_package_path:
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    evaluation = manifest.get("evaluation_contract")
    if not isinstance(evaluation, Mapping) or not isinstance(evaluation.get("one_shot_state_path"), str):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    pinned_state_path = _resolve_pinned_path(evaluation["one_shot_state_path"], root, root=root)
    if one_shot_state_path is not None and Path(one_shot_state_path).resolve() != pinned_state_path:
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    declared_state_hash = evaluation.get("one_shot_state_sha256")
    if declared_state_hash is not None:
        canonical_state = _canonical_artifact_hash(pinned_state_path, "artifact_sha256")
        if declared_state_hash not in {_sha256_file(pinned_state_path), canonical_state}:
            raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    # Full independent preflight verification is mandatory for economics.  A
    # self-hashed but forged receipt is insufficient: the verifier must rebuild
    # the exact persisted receipt from the pinned manifest and package.
    preflight_result = _verify_persisted_preflight(
        manifest_path=manifest_path,
        package_path=pinned_package_path,
        receipt=receipt,
        repo_root=root,
        preflight_receipt_path=gate_path,
    )
    source_commit = preflight_result.get("source_commit")
    freeze_commit = preflight_result.get("freeze_commit")
    if not isinstance(source_commit, str) or not isinstance(freeze_commit, str):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    statistics_pins = _manifest_statistics_pins(manifest, root)
    # The canonical receipt must carry the exact pins recomputed from disk.
    manifest_hash = _canonical_artifact_hash(manifest_path)
    package = load_r46_v2_package(
        pinned_package_path,
        repo_root=root,
        allow_marker_fallback=allow_marker_fallback,
    )
    package_hash = package.package_canonical_sha256
    declared_manifest_hash = (
        receipt.get("manifest_sha256")
        or receipt.get("manifest_canonical_sha256")
        or receipt.get("governance_manifest_sha256")
    )
    declared_package_hash = (
        receipt.get("package_sha256")
        or receipt.get("package_canonical_sha256")
        or receipt.get("data_package_manifest_sha256")
    )
    if manifest_hash != declared_manifest_hash:
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    if package_hash != declared_package_hash:
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    if package_path is not None and pinned_package_path != package.package_path:
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    state_path = pinned_state_path
    output = Path(output_dir).resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("BLOCKED_V2_OUTPUT_PATH") from exc
    output.mkdir(parents=True, exist_ok=True)
    reconciled_status = reconcile_one_shot(state_path, output / "run_receipt.json", require_crosslinks=True)
    if reconciled_status in {ONE_SHOT_CONSUMED, ONE_SHOT_ABORTED_CONSUMED}:
        raise RuntimeError("ONE_SHOT_ALREADY_CONSUMED")
    if reconciled_status == ONE_SHOT_RESERVED:
        raise RuntimeError("ONE_SHOT_ALREADY_RESERVED")
    expected_outputs = (
        output / "results.json",
        output / "ledger.json",
        output / "report.json",
        output / "run_receipt.json",
    )
    if any(path.exists() or path.is_symlink() for path in expected_outputs):
        raise RuntimeError("R46_OUTPUT_ALREADY_EXISTS")
    multipliers = tuple(float(value) for value in cost_multipliers)
    if multipliers != COST_MULTIPLIERS:
        raise ValueError("frozen_cost_multipliers")
    incumbent = incumbent_inputs or load_incumbent_pit_inputs(repo_root=root)
    if not _test_only:
        _validate_v5_contract_binding(manifest, package, incumbent, repo_root=root)
    dataset = build_r46_market_dataset(package, incumbent)
    effective_config = _load_r46_config(config, repo_root=root, test_only=_test_only)
    universe, universe_version, universe_status = _build_r46_universe(
        incumbent, config=effective_config, test_only=_test_only
    )
    if not _test_only:
        if effective_config is None:
            raise RuntimeError("BLOCKED_CONFIG_SOURCE")
        if universe is None or universe_status != "PIT_V5_PRODUCT_UNIVERSE":
            raise RuntimeError("BLOCKED_PIT_SOURCE:universe")
    second_persisted_receipt = _validate_pre_run_receipt(gate_path, repo_root=root)
    if second_persisted_receipt != receipt:
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    second_preflight_result = _verify_persisted_preflight(
        manifest_path=manifest_path,
        package_path=pinned_package_path,
        receipt=receipt,
        repo_root=root,
        preflight_receipt_path=gate_path,
    )
    if (
        second_preflight_result.get("source_commit") != source_commit
        or second_preflight_result.get("freeze_commit") != freeze_commit
    ):
        raise RuntimeError("BLOCKED_V2_PRE_RUN_GATE")
    reserve_one_shot(
        state_path,
        gate_sha256=str(receipt["integrity"]["receipt_sha256"]),
        manifest_sha256=manifest_hash,
        package_sha256=package_hash,
        require_crosslinks=True,
    )
    cells: list[dict[str, object]] = []
    ledger_cells: list[dict[str, object]] = []
    event_boundaries = tuple(
        sorted(
            {
                timestamp
                for schedule in package.schedules
                for timestamp in (schedule.entry_decision, schedule.exit_decision)
            }
        )
    )
    marker_timestamps = tuple(sorted(marker.timestamp for marker in package.markers))
    try:
        for multiplier in multipliers:
            candidate_strategy = R46OverlayStrategy(package.schedules)
            control_strategy = _R46IncumbentStrategy(dict(FROZEN_V3_PARAMETERS))
            candidate_result = _run_r46_engine(
                dataset,
                candidate_strategy,
                cost_multiplier=multiplier,
                starting_equity=starting_equity,
                config=effective_config,
                universe=universe,
                universe_version=universe_version,
                event_boundaries=event_boundaries,
                marker_timestamps=marker_timestamps,
                terminal_map=incumbent.terminal_map,
            )
            control_result = _run_r46_engine(
                dataset,
                control_strategy,
                cost_multiplier=multiplier,
                starting_equity=starting_equity,
                config=effective_config,
                universe=universe,
                universe_version=universe_version,
                marker_timestamps=marker_timestamps,
                terminal_map=incumbent.terminal_map,
            )
            candidate_ledger = _economic_ledger_payload(
                dataset,
                candidate_result,
                strategy=candidate_strategy,
                package=package,
                cost_multiplier=multiplier,
                arm="candidate",
                manifest_sha256=manifest_hash,
                gate_sha256=str(receipt["integrity"]["receipt_sha256"]),
                universe_status=universe_status,
                universe_version=universe_version,
                source_paths=incumbent.source_paths,
                config=effective_config,
            )
            control_ledger = _economic_ledger_payload(
                dataset,
                control_result,
                strategy=None,
                package=package,
                cost_multiplier=multiplier,
                arm="control",
                manifest_sha256=manifest_hash,
                gate_sha256=str(receipt["integrity"]["receipt_sha256"]),
                universe_status=universe_status,
                universe_version=universe_version,
                source_paths=incumbent.source_paths,
                config=effective_config,
            )
            candidate_metrics = _result_metrics(candidate_result)
            control_metrics = _result_metrics(control_result)
            cells.append(
                {
                    "cost_multiplier": multiplier,
                    "candidate_metrics": candidate_metrics,
                    "control_metrics": control_metrics,
                }
            )
            ledger_cells.append(
                {
                    "cost_multiplier": multiplier,
                    "candidate": candidate_ledger,
                    "control": control_ledger,
                }
            )
        timing_placebo_cell = _run_timing_placebo_cell(
            package,
            incumbent,
            config=effective_config,
            universe=universe,
            universe_version=universe_version,
            universe_status=universe_status,
            manifest_sha256=manifest_hash,
            gate_sha256=str(receipt["integrity"]["receipt_sha256"]),
            source_paths=incumbent.source_paths,
        )
        # The independent verifier is called before any output is published.
        statistics = _build_statistics(
            package,
            [
                {
                    **cell,
                    "candidate_ledger": ledger_cells[index]["candidate"],
                    "control_ledger": ledger_cells[index]["control"],
                }
                for index, cell in enumerate(cells)
            ],
            timing_placebo_cell=timing_placebo_cell,
        )
        # The file-backed verifier cross-links the report's statistics pins to
        # the persisted statistics object as well as the top-level result pin.
        statistics["statistics_pins"] = statistics_pins
        coverage = cast(Mapping[str, Any], statistics["coverage_gates"])
        coverage_blockers: list[str] = []
        if int(coverage["event_episodes"]) < int(coverage["minimum_event_episodes"]):
            coverage_blockers.append("BLOCKED_INSUFFICIENT_EVENT_RATE")
        if int(coverage["holding_intervals"]) < int(coverage["minimum_holding_intervals"]):
            coverage_blockers.append("BLOCKED_INSUFFICIENT_HOLDING_INTERVALS")
        if float(coverage["missing_rate"]) > float(coverage["maximum_missing_rate"]) + _EPS:
            coverage_blockers.append("BLOCKED_MISSINGNESS")
        if float(coverage["capacity_suppression_rate"]) > float(coverage["maximum_capacity_suppression_rate"]) + _EPS:
            coverage_blockers.append("BLOCKED_CAPACITY_SUPPRESSION")
        if coverage["sixth_future_open_complete"] is not True or coverage["fifth_open_exit_complete"] is not True:
            coverage_blockers.append("BLOCKED_SIXTH_FUTURE_OPEN")
        if timing_placebo_cell.get("status") != "REPORTED_NATIVE_ENGINE_DIAGNOSTIC_ONLY":
            coverage_blockers.append("BLOCKED_TIMING_PLACEBO_ENGINE_CELL")
        statistics["coverage_gates"] = {
            **coverage,
            "status": "PASS" if not coverage_blockers else "FAILED_VALID_FROZEN_TEST",
            "blockers": coverage_blockers,
        }
        run_status = "PASS_RESEARCH_ONLY" if not coverage_blockers else "FAILED_VALID_FROZEN_TEST"
        results_payload: dict[str, Any] = {
            "schema_version": ECONOMIC_RUN_SCHEMA,
            "status": run_status,
            "research_id": RESEARCH_ID,
            "candidate_id": "R46",
            "economic_execution": True,
            "economic_run": True,
            "research_only": True,
            "outcome_artifacts_present": True,
            "native_one_ledger": True,
            "shared_account": True,
            "separate_sleeve_blend": False,
            "paper_live_untouched": True,
            "paper_or_live_activation": False,
            "promotion": False,
            "integration_candidate": False,
            "auto_promotion": False,
            "no_auto_promotion": True,
            "manifest_canonical_sha256": manifest_hash,
            "package_canonical_sha256": package_hash,
            "preflight_receipt_sha256": receipt["integrity"]["receipt_sha256"],
            "governance_manifest_path": str(manifest_path.relative_to(root)),
            "data_package_manifest_path": str(pinned_package_path.relative_to(root)),
            "preflight_receipt_path": str(gate_path.relative_to(root)),
            "source_commit": source_commit,
            "freeze_commit": freeze_commit,
            "one_shot_state_path": _receipt_source_path(state_path, repo_root=root, test_only=_test_only),
            "one_shot_state_sha256": _sha256_file(state_path),
            "dataset_digest": dataset.content_digest(),
            "source_paths": copy.deepcopy(
                cast(Mapping[str, object], cast(Mapping[str, Any], ledger_cells[0]["candidate"]).get("source_paths", {}))
            )
            if ledger_cells
            else {},
            "native_engine": "TerminalAwareResearchEngine",
            "engine_invocations": len(cells) * 2 + 2,
            "cost_multipliers": list(multipliers),
            "statistics_pins": statistics_pins,
            "cells": cells,
            "statistics": statistics,
            "no_best_cell_selection": True,
            "output_files": {
                "results": "results.json",
                "ledger": "ledger.json",
                "report": "report.json",
                "run_receipt": "run_receipt.json",
            },
        }
        ledger_payload: dict[str, Any] = {
            "schema_version": f"{ECONOMIC_RUN_SCHEMA}:ledger",
            "status": ECONOMIC_LEDGER_STATUS,
            "research_id": RESEARCH_ID,
            "candidate_id": "R46",
            "economic_run": True,
            "research_only": True,
            "economic_execution": True,
            "outcome_artifacts_present": True,
            "native_one_ledger": True,
            "shared_account": True,
            "separate_sleeve_blend": False,
            "paper_live_untouched": True,
            "paper_or_live_activation": False,
            "promotion": False,
            "integration_candidate": False,
            "auto_promotion": False,
            "no_auto_promotion": True,
            "native_engine": "TerminalAwareResearchEngine",
            "engine_invocations": len(cells) * 2 + 2,
            "manifest_canonical_sha256": manifest_hash,
            "package_canonical_sha256": package_hash,
            "preflight_receipt_sha256": receipt["integrity"]["receipt_sha256"],
            "dataset_digest": dataset.content_digest(),
            "one_shot_state_path": _receipt_source_path(state_path, repo_root=root, test_only=_test_only),
            "one_shot_state_sha256": _sha256_file(state_path),
            "governance_manifest_path": str(manifest_path.relative_to(root)),
            "data_package_manifest_path": str(pinned_package_path.relative_to(root)),
            "preflight_receipt_path": str(gate_path.relative_to(root)),
            "source_commit": source_commit,
            "freeze_commit": freeze_commit,
            "source_paths": copy.deepcopy(
                cast(Mapping[str, object], cast(Mapping[str, Any], ledger_cells[0]["candidate"]).get("source_paths", {}))
            )
            if ledger_cells
            else {},
            "statistics_pins": statistics_pins,
            "cells": ledger_cells,
            "no_best_cell_selection": True,
        }
        report_payload: dict[str, Any] = {
            "schema_version": f"{ECONOMIC_RUN_SCHEMA}:report",
            "status": run_status,
            "research_id": RESEARCH_ID,
            "candidate_id": "R46",
            "economic_execution": True,
            "economic_run": True,
            "research_only": True,
            "outcome_artifacts_present": True,
            "native_one_ledger": True,
            "shared_account": True,
            "separate_sleeve_blend": False,
            "paper_live_untouched": True,
            "paper_or_live_activation": False,
            "promotion": False,
            "integration_candidate": False,
            "auto_promotion": False,
            "no_auto_promotion": True,
            "primary_estimand": statistics["primary_estimand"],
            "unchanged_incumbent_comparison": statistics["unchanged_incumbent_comparison"],
            "timing_placebo_plus_10_sessions": statistics["timing_placebo_plus_10_sessions"],
            "cost_outputs": statistics["cost_outputs"],
            "capacity": statistics["capacity"],
            "missingness": statistics["missingness"],
            "coverage_gates": statistics["coverage_gates"],
            "resampling": statistics["resampling"],
            "holm": statistics["holm"],
            "statistics_pins": statistics_pins,
            "no_thresholds_or_selection": True,
        }
        from scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 import (
            verify_economic_payloads_test_only,
            verify_economic_run,
            verify_economic_run_from_files,
        )

        if _test_only:
            verify_economic_payloads_test_only(results_payload, ledger_payload, report_payload)
        else:
            # This is the runner's pre-publication structural check.  The
            # final production decision is made only by the file-backed
            # verifier after all five artifacts exist on disk.
            verify_economic_run(results_payload, ledger_payload, report_payload)
        result_hash = _write_exclusive_payload(output / "results.json", results_payload)
        ledger_hash = _write_exclusive_payload(output / "ledger.json", ledger_payload)
        report_hash = _write_exclusive_payload(output / "report.json", report_payload)
        bundle_hash = hashlib.sha256(
            _canonical_bytes(
                {
                    "results_sha256": result_hash,
                    "ledger_sha256": ledger_hash,
                    "report_sha256": report_hash,
                }
            )
        ).hexdigest()
        run_receipt: dict[str, Any] = {
            "schema_version": f"{ECONOMIC_RUN_SCHEMA}:receipt",
            "status": run_status,
            "research_id": RESEARCH_ID,
            "candidate_id": "R46",
            "manifest_canonical_sha256": manifest_hash,
            "package_canonical_sha256": package_hash,
            "preflight_receipt_sha256": receipt["integrity"]["receipt_sha256"],
            "governance_manifest_path": str(manifest_path.relative_to(root)),
            "data_package_manifest_path": str(pinned_package_path.relative_to(root)),
            "preflight_receipt_path": str(gate_path.relative_to(root)),
            "source_commit": source_commit,
            "freeze_commit": freeze_commit,
            "runner_sha256": _sha256_file(Path(__file__).resolve()),
            "verifier_sha256": _sha256_file(
                REPO / "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py"
            ),
            "source_sha256": _sha256_file(REPO / "src/parayabuck/backtest/portfolio_engine.py"),
            "runner_path": _receipt_source_path(Path(__file__), repo_root=root, test_only=_test_only),
            "verifier_path": _receipt_source_path(
                REPO / "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
                repo_root=root,
                test_only=_test_only,
            ),
            "source_path": _receipt_source_path(
                REPO / "src/parayabuck/backtest/portfolio_engine.py",
                repo_root=root,
                test_only=_test_only,
            ),
            "test_only": _test_only,
            "one_shot": {
                "state_path": _receipt_source_path(state_path, repo_root=root, test_only=_test_only),
                "status": ONE_SHOT_RESERVED,
                "consume_before": "publication_complete",
            },
            "outputs": {
                "results_sha256": result_hash,
                "ledger_sha256": ledger_hash,
                "report_sha256": report_hash,
                "bundle_sha256": bundle_hash,
            },
            "cost_multipliers": list(multipliers),
            "statistics_pins": statistics_pins,
            "no_best_cell_selection": True,
            "economic_execution": True,
            "economic_run": True,
            "research_only": True,
            "outcome_artifacts_present": True,
            "native_one_ledger": True,
            "shared_account": True,
            "separate_sleeve_blend": False,
            "paper_live_untouched": True,
            "paper_or_live_activation": False,
            "promotion": False,
            "integration_candidate": False,
            "auto_promotion": False,
            "no_auto_promotion": True,
            "receipt_sha256": "",
        }
        consume_one_shot(
            state_path,
            bundle_hash,
            gate_sha256=str(receipt["integrity"]["receipt_sha256"]),
            manifest_sha256=manifest_hash,
            package_sha256=package_hash,
            output_path=output,
            receipt_path=output / "run_receipt.json",
            require_crosslinks=True,
        )
        run_receipt["one_shot"] = {
            "state_path": _receipt_source_path(state_path, repo_root=root, test_only=_test_only),
            "status": ONE_SHOT_CONSUMED,
            "output_sha256": bundle_hash,
        }
        run_receipt["receipt_sha256"] = _canonical_hash(run_receipt, "receipt_sha256")
        # Publish only the post-consume receipt.  A failure after CONSUMED is
        # intentionally irrecoverable; the state cannot be reopened or reused.
        receipt_hash = _write_exclusive_payload(output / "run_receipt.json", run_receipt)
        if _test_only:
            verify_economic_payloads_test_only(results_payload, ledger_payload, report_payload, run_receipt)
        else:
            verify_economic_run_from_files(
                root,
                output / "results.json",
                output / "ledger.json",
                output / "report.json",
                output / "run_receipt.json",
                gate_path,
            )
        return {
            "status": run_status,
            "results": results_payload,
            "ledger": ledger_payload,
            "report": report_payload,
            "run_receipt": run_receipt,
            "output_dir": str(output),
            "run_receipt_file_sha256": receipt_hash,
        }
    except Exception:
        # Deliberately do not remove the reservation or partial outputs.  The
        # one-shot state remains RESERVED and a second attempt is refused.
        raise


def run_package_backed(
    *,
    gate: Path | str | Mapping[str, object] | None,
    output_dir: Path | str,
    package_path: Path | str | None = None,
    one_shot_state_path: Path | str | None = None,
    repo_root: Path | str = REPO,
    incumbent_inputs: R46IncumbentInputs | None = None,
    config: object | None = None,
    starting_equity: float = 100_000.0,
    cost_multipliers: Sequence[float] = COST_MULTIPLIERS,
    allow_marker_fallback: bool = False,
) -> dict[str, object]:
    """Production package runner with no caller-controlled economic inputs."""

    if (
        incumbent_inputs is not None
        or config is not None
        or abs(float(starting_equity) - 100_000.0) > _EPS
        or tuple(float(value) for value in cost_multipliers) != COST_MULTIPLIERS
        or allow_marker_fallback
    ):
        raise RuntimeError("BLOCKED_V2_PRODUCTION_OVERRIDE")
    return _run_package_backed(
        gate=gate,
        output_dir=output_dir,
        package_path=package_path,
        one_shot_state_path=one_shot_state_path,
        repo_root=repo_root,
        _test_only=False,
    )


def run_package_backed_test_only(
    *,
    gate: Path | str | Mapping[str, object] | None,
    output_dir: Path | str,
    package_path: Path | str | None = None,
    one_shot_state_path: Path | str | None = None,
    repo_root: Path | str = REPO,
    incumbent_inputs: R46IncumbentInputs | None = None,
    config: object | None = None,
    starting_equity: float = 100_000.0,
    cost_multipliers: Sequence[float] = COST_MULTIPLIERS,
    allow_marker_fallback: bool = False,
) -> dict[str, object]:
    """Explicit TEST_ONLY seam for synthetic/temp fixtures.

    This entrypoint is intentionally named and kept separate from the
    production runner so fixture overrides cannot be supplied accidentally by
    an economic caller.
    """

    return _run_package_backed(
        gate=gate,
        output_dir=output_dir,
        package_path=package_path,
        one_shot_state_path=one_shot_state_path,
        repo_root=repo_root,
        incumbent_inputs=incumbent_inputs,
        config=config,
        starting_equity=starting_equity,
        cost_multipliers=cost_multipliers,
        allow_marker_fallback=allow_marker_fallback,
        _test_only=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True, help="Pinned on-disk v2 PRE_RUN_OK receipt")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory for results/ledger/report")
    parser.add_argument("--package", type=Path, dest="package_path")
    parser.add_argument("--one-shot-state", type=Path, dest="one_shot_state_path")
    args = parser.parse_args()
    try:
        payload = run_package_backed(
            gate=args.gate,
            output_dir=args.output_dir,
            package_path=args.package_path,
            one_shot_state_path=args.one_shot_state_path,
        )
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "output_dir": payload["output_dir"],
                    "run_receipt_file_sha256": payload["run_receipt_file_sha256"],
                },
                sort_keys=True,
            )
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CAPS",
    "COST_MULTIPLIERS",
    "ECONOMIC_LEDGER_STATUS",
    "ECONOMIC_RUN_SCHEMA",
    "FROZEN_V3_PARAMETERS",
    "NATIVE_LEDGER_SCHEMA",
    "PRE_RUN_OK",
    "REPO",
    "R46EventRebalancePolicy",
    "R46EventSchedule",
    "R46IncumbentInputs",
    "R46OverlayStrategy",
    "R46PackageInputs",
    "SharedAccountLedger",
    "build_r46_market_dataset",
    "build_native_smoke_dataset",
    "consume_one_shot",
    "load_incumbent_pit_inputs",
    "load_r46_v2_package",
    "native_adapter_smoke",
    "one_shot_status",
    "require_pre_run_ok",
    "reserve_one_shot",
    "run_native_adapter",
    "run_native_once",
    "run_native_once_test_only",
    "run_package_backed",
    "run_synthetic",
    "run_synthetic_test_only",
    "schedule_r46_holding",
]
