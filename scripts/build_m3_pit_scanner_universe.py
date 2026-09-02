#!/usr/bin/env python3
"""Build the research-only, no-alpha M3 PIT scanner population.

The command consumes already acquired local artifacts.  It has no broker or
network dependency and never constructs an observation strategy.  The large
canonical CSV is processed as a timestamp-ordered stream; the scanner itself
is the production :class:`EquityUniverseScanner`, not a research replacement.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from m3_data_gate_helpers import (  # noqa: E402
    CANONICAL_COLUMNS,
    CANONICAL_TIMEFRAME,
    EXCHANGE_TIMEZONE,
    NORMALIZER_VERSION,
    PIT_POPULATION_SCHEMA_VERSION,
    REFERENCE_SYMBOLS,
    SOURCE_ADJUSTMENT,
    SOURCE_FEED,
    GateError,
    canonical_digest,
    canonical_timestamp,
    config_semantic_digest,
    deterministic_candidate_population,
    load_csv_reference_rows,
    semantic_rows_digest,
    stage_file,
    validate_pit_reference_boundary,
)

from parayabuck.backtest.universe import _median_dollar_volume, _period_key  # noqa: E402
from parayabuck.config import EquityScannerConfig, EquityUniverseConfig  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.markets.symbols import canonical_symbol  # noqa: E402
from parayabuck.universe.scanner import (  # noqa: E402
    EquityUniverseScanner,
    equity_candidate_from_bars,
    scanner_config_digest,
)

R3_MISSION = "equity_m3_pit_scanner_population_2026_09_r3"
R3_DATA_GATE_MISSION = "equity_m3_data_gate_2026_09_r3"
R3_CONTRACT_PATH = "data/research_manifests/equity_m3_data_gate_2026_09_r3.json"
DEFAULT_CONFIG_PATH = _REPO / "config/settings.yaml"
DEFAULT_OUTPUT_PATH = _REPO / "data/research/generated/m3_data_gate_r3/pit_universe.json"
DEFAULT_MANIFEST_PATH = _REPO / "data/research/generated/m3_data_gate_r3/pit_population_manifest.json"
LOOKBACK_DAYS = 60


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _run_git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=_REPO, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _git_blob_bytes(commit: str, path: str) -> bytes:
    result = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], cwd=_REPO, check=True, capture_output=True)
    return result.stdout


def _logical_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO.resolve()).as_posix()
    except ValueError:
        return f"<external:{path.name}>"


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError("invalid_json", f"invalid JSON artifact: {path.name}") from exc
    if not isinstance(value, Mapping):
        raise GateError("invalid_json", f"JSON artifact must be an object: {path.name}")
    return value


def _load_yaml_bytes(path: Path) -> Mapping[str, object]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise GateError("invalid_config", f"invalid YAML config: {path.name}") from exc
    if not isinstance(value, Mapping):
        raise GateError("invalid_config", "settings YAML must be an object")
    return value


def _tracked_file_identity(path: Path, *, commit: str = "HEAD") -> dict[str, object]:
    logical = _logical_path(path)
    if logical.startswith("<external:"):
        raise GateError("tracked_identity_missing", f"artifact is outside the repository: {path.name}")
    try:
        blob_id = _run_git("rev-parse", f"{commit}:{logical}")
        blob_bytes = _git_blob_bytes(commit, logical)
    except subprocess.CalledProcessError as exc:
        raise GateError("tracked_identity_missing", f"artifact is not in the committed tree: {logical}") from exc
    try:
        working = path.read_bytes()
    except OSError as exc:
        raise GateError("artifact_missing", f"artifact is missing: {logical}") from exc
    if working != blob_bytes:
        raise GateError("mutable_tracked_artifact", f"working bytes differ from the committed blob: {logical}")
    return {
        "path": logical,
        "commit": commit,
        "git_blob_id": blob_id,
        "raw_sha256": _sha256_bytes(blob_bytes),
    }


def _artifact_receipt(artifact: object, *, logical_id: str, provenance_class: str) -> dict[str, object]:
    if not hasattr(artifact, "receipt"):
        raise GateError("artifact_identity_missing", f"artifact receipt is unavailable: {logical_id}")
    return artifact.receipt(logical_id, provenance_class)  # type: ignore[no-any-return,union-attr]


def _manifest_hash(manifest: Mapping[str, object]) -> str:
    value = manifest.get("sha256", manifest.get("dataset_sha256"))
    if not isinstance(value, str) or len(value) != 64:
        raise GateError("manifest_hash_missing", "canonical manifest does not declare a SHA-256")
    return value


def _verify_canonical_inputs(
    canonical_bars: Path,
    canonical_manifest: Path,
    contract: Mapping[str, object],
    staging_dir: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    manifest_artifact = stage_file(canonical_manifest, staging_dir)
    committed_manifest = _tracked_file_identity(canonical_manifest)
    manifest = _load_json(manifest_artifact.staged_path)
    if manifest_artifact.raw_sha256 != str(committed_manifest["raw_sha256"]):
        raise GateError("canonical_manifest_hash_mismatch", "canonical manifest staging identity changed")
    expected_path = contract.get("canonical_manifest_path")
    if expected_path is not None and expected_path != _logical_path(canonical_manifest):
        raise GateError("canonical_manifest_path_mismatch", "canonical manifest path differs from the R3 contract")
    if str(manifest.get("feed", "")).lower() != SOURCE_FEED:
        raise GateError("canonical_source_contract", "canonical manifest feed must be sip")
    if str(manifest.get("adjustment", "")).lower() != SOURCE_ADJUSTMENT:
        raise GateError("canonical_source_contract", "canonical manifest adjustment must be split")
    bars_artifact = stage_file(canonical_bars, staging_dir)
    if bars_artifact.raw_sha256 != _manifest_hash(manifest):
        raise GateError("canonical_hash_mismatch", "canonical bars bytes differ from the frozen manifest")
    declared_size = manifest.get("byte_size")
    if isinstance(declared_size, int) and bars_artifact.byte_size != declared_size:
        raise GateError("canonical_size_mismatch", "canonical bars size differs from the frozen manifest")
    return (
        bars_artifact.staged_path,
        {
            "path": _logical_path(canonical_bars),
            "raw_sha256": bars_artifact.raw_sha256,
            "crlf_normalized_sha256": bars_artifact.crlf_normalized_sha256,
            "byte_size": bars_artifact.byte_size,
            "manifest_git_blob": committed_manifest,
        },
        manifest,
    )


def _verify_contract(contract_path: Path, staging_dir: Path) -> tuple[Mapping[str, object], dict[str, object]]:
    contract_artifact = stage_file(contract_path, staging_dir)
    identity = _tracked_file_identity(contract_path)
    if contract_artifact.raw_sha256 != str(identity["raw_sha256"]):
        raise GateError("contract_hash_mismatch", "working R3 contract bytes changed during staging")
    contract = _load_json(contract_artifact.staged_path)
    if contract.get("mission") != R3_DATA_GATE_MISSION:
        raise GateError("contract_mission_mismatch", "PIT builder requires the R3 data-gate contract")
    if contract.get("manifest_path") != R3_CONTRACT_PATH:
        raise GateError("contract_path_mismatch", "R3 contract path is not frozen")
    population_contract = contract.get("candidate_population_contract")
    expected_population_contract = {
        "schema_version": PIT_POPULATION_SCHEMA_VERSION,
        "methodology": "strictly_prior_data_frozen_scanner_eligible_universe",
        "observation_strategy": None,
        "manual_candidate_selection_allowed": False,
        "reference_symbols_excluded": list(REFERENCE_SYMBOLS),
    }
    if population_contract != expected_population_contract:
        raise GateError("population_contract_mismatch", "R3 candidate-population contract is not frozen")
    return contract, identity


def _equity_config(config_data: Mapping[str, object]) -> EquityUniverseConfig:
    universe = config_data.get("universe")
    equity_raw = universe.get("equity") if isinstance(universe, Mapping) else None
    if not isinstance(equity_raw, Mapping):
        raise GateError("equity_config_missing", "settings YAML does not contain universe.equity")
    values = dict(equity_raw)
    scanner_raw = values.pop("scanner", {})
    if not isinstance(scanner_raw, Mapping):
        raise GateError("equity_scanner_config_missing", "universe.equity.scanner must be an object")
    try:
        return EquityUniverseConfig(scanner=EquityScannerConfig(**dict(scanner_raw)), **values)
    except (TypeError, ValueError) as exc:
        raise GateError("equity_config_invalid", "settings universe.equity is not valid") from exc


def _verify_composite_inputs(
    sidecar_path: Path,
    composite_path: Path,
    canonical_identity: Mapping[str, object],
    contract: Mapping[str, object],
    staging_dir: Path,
) -> tuple[list[object], dict[str, object], Mapping[str, object]]:
    composite_artifact = stage_file(composite_path, staging_dir)
    composite = _load_json(composite_artifact.staged_path)
    identity = composite.get("identity")
    sidecar_declaration = composite.get("iwm_sidecar")
    if not isinstance(identity, Mapping) or not isinstance(sidecar_declaration, Mapping):
        raise GateError("composite_schema_mismatch", "composite manifest requires identity and iwm_sidecar")
    identity_payload = {key: value for key, value in identity.items() if key != "semantic_digest_sha256"}
    if identity.get("semantic_digest_sha256") != canonical_digest(identity_payload):
        raise GateError("composite_digest_mismatch", "composite identity digest does not match its content")
    if identity.get("canonical_parent_raw_sha256") != canonical_identity.get("raw_sha256"):
        raise GateError("composite_canonical_mismatch", "composite does not bind the staged canonical bytes")
    if identity.get("canonical_parent_normalized_sha256") != canonical_identity.get("crlf_normalized_sha256"):
        raise GateError("composite_canonical_mismatch", "composite does not bind the normalized canonical bytes")
    manifest_identity = canonical_identity.get("manifest_git_blob")
    if not isinstance(manifest_identity, Mapping) or identity.get(
        "canonical_manifest_git_blob"
    ) != manifest_identity.get("git_blob_id"):
        raise GateError("composite_manifest_mismatch", "composite does not bind the canonical manifest Git blob")
    expected_normalizer = contract.get("critical_git_blobs", {})
    normalizer_declaration = (
        expected_normalizer.get("scripts/m3_data_gate_helpers.py") if isinstance(expected_normalizer, Mapping) else None
    )
    if isinstance(normalizer_declaration, Mapping):
        if identity.get("normalizer_git_blob") != normalizer_declaration.get("git_blob_id"):
            raise GateError("composite_normalizer_mismatch", "composite normalizer Git blob differs from the R3 pin")
        if identity.get("normalizer_sha256") != normalizer_declaration.get("raw_sha256"):
            raise GateError("composite_normalizer_mismatch", "composite normalizer hash differs from the R3 pin")
    if identity.get("schema_version") != NORMALIZER_VERSION:
        raise GateError("composite_schema_mismatch", "composite normalizer schema differs from the R3 contract")
    if identity.get("canonical_timeframe") != CANONICAL_TIMEFRAME:
        raise GateError("composite_schema_mismatch", "composite timeframe is not 1d")
    if identity.get("source_feed") != SOURCE_FEED or identity.get("adjustment_mode") != SOURCE_ADJUSTMENT:
        raise GateError("composite_source_mismatch", "composite source feed or adjustment differs from the contract")
    if identity.get("exchange_timezone") != EXCHANGE_TIMEZONE:
        raise GateError("composite_timezone_mismatch", "composite exchange timezone differs from the contract")
    expected_sidecar_hash = sidecar_declaration.get("raw_sha256")
    expected_sidecar_size = sidecar_declaration.get("byte_size")
    sidecar_artifact = stage_file(sidecar_path, staging_dir)
    if sidecar_artifact.raw_sha256 != expected_sidecar_hash:
        raise GateError("sidecar_hash_mismatch", "normalized IWM sidecar differs from the composite manifest")
    if isinstance(expected_sidecar_size, int) and sidecar_artifact.byte_size != expected_sidecar_size:
        raise GateError("sidecar_size_mismatch", "normalized IWM sidecar size differs from the composite manifest")
    rows, quality = load_csv_reference_rows(sidecar_artifact.staged_path, reference_symbols=("IWM",))
    iwm_quality = quality.get("IWM", {})
    if int(iwm_quality.get("rejected_row_count", 0)) or int(iwm_quality.get("duplicate_count", 0)):
        raise GateError("sidecar_quality_failure", "normalized IWM sidecar contains rejected or duplicate rows")
    iwm_rows = rows.get("IWM", [])
    if not iwm_rows:
        raise GateError("empty_iwm_sidecar", "normalized IWM sidecar is empty")
    if sidecar_declaration.get("normalized_row_count") != len(iwm_rows):
        raise GateError("sidecar_row_count_mismatch", "normalized IWM sidecar row count differs from the composite")
    if sidecar_declaration.get("normalized_semantic_row_sha256") != semantic_rows_digest(iwm_rows):
        raise GateError(
            "sidecar_semantic_mismatch", "normalized IWM sidecar semantic digest differs from the composite"
        )
    if identity.get("iwm_normalized_raw_sha256") != sidecar_artifact.raw_sha256:
        raise GateError("sidecar_identity_mismatch", "composite IWM raw sidecar hash is incorrect")
    if identity.get("iwm_normalized_semantic_row_sha256") != sidecar_declaration.get("normalized_semantic_row_sha256"):
        raise GateError("sidecar_identity_mismatch", "composite IWM semantic sidecar hash is incorrect")
    return (
        list(iwm_rows),
        {
            "path": _logical_path(sidecar_path),
            "raw_sha256": sidecar_artifact.raw_sha256,
            "crlf_normalized_sha256": sidecar_artifact.crlf_normalized_sha256,
            "byte_size": sidecar_artifact.byte_size,
            "normalized_row_count": len(iwm_rows),
            "normalized_semantic_row_sha256": sidecar_declaration.get("normalized_semantic_row_sha256"),
        },
        composite,
    )


def _parse_bar(raw: Mapping[str, str], row_number: int) -> Bar:
    try:
        timestamp = datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("naive timestamp")
        symbol = canonical_symbol(str(raw["symbol"]), Market.EQUITY)
        if str(raw["timeframe"]) != CANONICAL_TIMEFRAME:
            raise ValueError("timeframe mismatch")
        if str(raw["market"]).lower() != Market.EQUITY.value:
            raise ValueError("market mismatch")
        return Bar(
            timestamp=timestamp,
            symbol=symbol,
            timeframe=CANONICAL_TIMEFRAME,
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=float(raw["volume"]),
            market=Market.EQUITY,
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise GateError("canonical_row_invalid", f"canonical row {row_number} is not a valid 1d equity bar") from exc


def _iter_canonical_groups(path: Path) -> Iterator[tuple[datetime, list[Bar]]]:
    required = set(CANONICAL_COLUMNS[1:])
    previous_key: tuple[datetime, str] | None = None
    current_timestamp: datetime | None = None
    current: list[Bar] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(reader.fieldnames or ()):
            raise GateError("canonical_schema_mismatch", "canonical CSV is missing required columns")
        for row_number, raw in enumerate(reader, start=2):
            bar = _parse_bar(raw, row_number)
            key = (bar.timestamp, bar.symbol)
            if previous_key is not None and key <= previous_key:
                category = "canonical_duplicate" if key == previous_key else "canonical_order"
                raise GateError(category, "canonical CSV must be strictly ordered by timestamp and symbol")
            previous_key = key
            if current_timestamp is not None and bar.timestamp != current_timestamp:
                yield current_timestamp, current
                current = []
            current_timestamp = bar.timestamp
            current.append(bar)
    if current_timestamp is not None:
        yield current_timestamp, current


def _bar_from_normalized(row: object) -> Bar:
    try:
        return Bar(
            timestamp=row.timestamp,  # type: ignore[attr-defined]
            symbol=str(row.symbol),  # type: ignore[attr-defined]
            timeframe=CANONICAL_TIMEFRAME,
            open=float(row.open),  # type: ignore[attr-defined]
            high=float(row.high),  # type: ignore[attr-defined]
            low=float(row.low),  # type: ignore[attr-defined]
            close=float(row.close),  # type: ignore[attr-defined]
            volume=float(row.volume),  # type: ignore[attr-defined]
            market=Market.EQUITY,
        )
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise GateError("sidecar_row_invalid", "normalized sidecar row cannot become a production equity bar") from exc


def _merge_iwm_groups(
    canonical_groups: Iterator[tuple[datetime, list[Bar]]], iwm_rows: Sequence[object]
) -> Iterator[tuple[datetime, list[Bar]]]:
    sidecar: dict[datetime, list[Bar]] = {}
    for row in iwm_rows:
        bar = _bar_from_normalized(row)
        sidecar.setdefault(bar.timestamp, []).append(bar)
    for values in sidecar.values():
        values.sort(key=lambda item: item.symbol)
    side_timestamps = iter(sorted(sidecar))
    side_timestamp = next(side_timestamps, None)
    for timestamp, bars in canonical_groups:
        while side_timestamp is not None and side_timestamp < timestamp:
            yield side_timestamp, sidecar[side_timestamp]
            side_timestamp = next(side_timestamps, None)
        if side_timestamp == timestamp:
            by_symbol = {bar.symbol: bar for bar in bars}
            for bar in sidecar[timestamp]:
                prior = by_symbol.get(bar.symbol)
                if prior is not None:
                    if prior != bar:
                        raise GateError(
                            "composite_duplicate_conflict", f"canonical and sidecar disagree for {bar.symbol}"
                        )
                    continue
                by_symbol[bar.symbol] = bar
            yield timestamp, sorted(by_symbol.values(), key=lambda item: item.symbol)
            side_timestamp = next(side_timestamps, None)
        else:
            yield timestamp, bars
    while side_timestamp is not None:
        yield side_timestamp, sidecar[side_timestamp]
        side_timestamp = next(side_timestamps, None)


def _scan_groups(
    groups: Iterable[tuple[datetime, Sequence[Bar]]],
    *,
    equity_config: EquityUniverseConfig,
    rebalance_frequency: str,
) -> list[dict[str, object]]:
    if rebalance_frequency not in {"daily", "weekly", "monthly"}:
        raise GateError("scanner_rebalance_invalid", "backtest.scanner_rebalance must be daily, weekly, or monthly")
    scanner = EquityUniverseScanner(equity_config)
    pinned = (*equity_config.reference_symbols, *equity_config.inverse_trade_symbols)
    expected_history_bars = LOOKBACK_DAYS
    duration = timedelta(days=1)
    window: dict[str, list[Bar]] = {}
    previous_period: tuple[int, ...] | None = None
    snapshots: list[dict[str, object]] = []
    for timestamp, current_bars in groups:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise GateError("naive_timestamp", "PIT snapshot timestamp must be timezone-aware")
        cutoff = timestamp - timedelta(days=LOOKBACK_DAYS)
        for symbol, history in tuple(window.items()):
            window[symbol] = [bar for bar in history if bar.timestamp >= cutoff]
            if not window[symbol]:
                del window[symbol]
        period = _period_key(timestamp, rebalance_frequency)
        if period != previous_period:
            previous_period = period
            by_symbol = {symbol: list(history) for symbol, history in window.items() if history}
            data_timestamp = max(
                (history[-1].timestamp for history in by_symbol.values()),
                default=timestamp - duration,
            )
            if by_symbol:
                shortlisted = sorted(
                    by_symbol.items(),
                    key=lambda item: (-_median_dollar_volume(item[1]), item[0]),
                )[: equity_config.target_shortlist]
                candidates = [
                    equity_candidate_from_bars(symbol, history, expected_bar_count=min(20, expected_history_bars))
                    for symbol, history in shortlisted
                ]
                result = scanner.scan(
                    candidates,
                    data_timestamp=data_timestamp,
                    selection_timestamp=timestamp,
                    pinned_symbols=pinned,
                )
                selected = sorted(symbol for symbol in result.selected_symbols if symbol in by_symbol)
                if selected or result.candidate_symbols:
                    snapshots.append(
                        {
                            "effective_at": canonical_timestamp(timestamp),
                            "symbols": selected,
                            "scanner_version": result.scanner_version,
                            "config_digest": result.config_digest,
                            "candidate_count": len(result.candidate_symbols),
                            "strictly_prior_data": True,
                            "latest_input_timestamp": canonical_timestamp(data_timestamp),
                            "prior_bar_count": sum(len(history) for history in by_symbol.values()),
                        }
                    )
        for bar in current_bars:
            symbol = canonical_symbol(str(bar.symbol), Market.EQUITY)
            window.setdefault(symbol, []).append(bar)
    return snapshots


def build_population_from_bars(
    bars: Sequence[Bar],
    *,
    equity_config: EquityUniverseConfig,
    rebalance_frequency: str = "daily",
    reference_symbols: Sequence[str] = REFERENCE_SYMBOLS,
) -> dict[str, object]:
    """Build a population from fixtures while preserving the production path.

    This list entry point deliberately sorts its input, allowing parity tests
    to prove permutation invariance.  The command-line path uses the
    timestamp-ordered canonical stream and rejects disorder in the frozen
    dataset rather than silently sorting hundreds of megabytes.
    """
    selected = [bar for bar in bars if bar.market == Market.EQUITY]
    if any(bar.timeframe != CANONICAL_TIMEFRAME for bar in selected):
        raise GateError("canonical_timeframe_mismatch", "PIT builder requires equity bars at timeframe 1d")
    selected = sorted(
        selected,
        key=lambda item: (item.timestamp, item.symbol),
    )
    groups: list[tuple[datetime, list[Bar]]] = []
    for bar in selected:
        if groups and groups[-1][0] == bar.timestamp:
            groups[-1][1].append(bar)
        else:
            groups.append((bar.timestamp, [bar]))
    snapshots = _scan_groups(groups, equity_config=equity_config, rebalance_frequency=rebalance_frequency)
    return _population_payload(
        snapshots,
        config_digest=scanner_config_digest(equity_config),
        rebalance_frequency=rebalance_frequency,
        reference_symbols=reference_symbols,
        input_identity={"source": "fixture_bars", "row_order_normalized": True},
    )


def _population_payload(
    snapshots: Sequence[Mapping[str, object]],
    *,
    config_digest: str,
    rebalance_frequency: str,
    reference_symbols: Sequence[str],
    input_identity: Mapping[str, object],
) -> dict[str, object]:
    normalized_snapshots = [dict(snapshot) for snapshot in snapshots]
    population = deterministic_candidate_population(normalized_snapshots, reference_symbols=reference_symbols)
    role = validate_pit_reference_boundary(population, reference_symbols=reference_symbols)
    if not population.get("candidate_symbols"):
        raise GateError("empty_candidate_population", "PIT scanner population contains no non-reference candidates")
    scanner_versions = sorted({str(item.get("scanner_version")) for item in normalized_snapshots})
    scanner_digests = sorted({str(item.get("config_digest")) for item in normalized_snapshots})
    counts = [int(item["eligible_count"]) for item in population["snapshots"]]  # type: ignore[index]
    strict_prior_failures: list[str] = []
    for snapshot in normalized_snapshots:
        if snapshot.get("strictly_prior_data") is not True:
            strict_prior_failures.append(str(snapshot.get("effective_at")))
            continue
        latest = snapshot.get("latest_input_timestamp")
        effective = snapshot.get("effective_at")
        if (
            latest is not None
            and effective is not None
            and datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
            >= datetime.fromisoformat(str(effective).replace("Z", "+00:00"))
        ):
            strict_prior_failures.append(str(effective))
    if strict_prior_failures:
        raise GateError("pit_future_leak", "PIT snapshot contains data at or after its effective timestamp")
    return {
        "schema_version": PIT_POPULATION_SCHEMA_VERSION,
        "mission": R3_MISSION,
        "market": Market.EQUITY.value.upper(),
        "timeframe": CANONICAL_TIMEFRAME,
        "methodology": "strictly_prior_data_production_scanner_eligible_universe",
        "observation_strategy": None,
        "alpha_ranking_used": False,
        "reference_symbols": sorted({str(symbol).strip().upper() for symbol in reference_symbols}),
        "scanner_rebalance": rebalance_frequency,
        "scanner_versions": scanner_versions,
        "scanner_config_digests": scanner_digests,
        "input_identity": dict(input_identity),
        "no_alpha_assertion": {
            "observation_strategy_is_none": True,
            "candidate_rankings_present": False,
            "economic_ranking_present": False,
            "serialized_membership": "UniverseSnapshot.symbols / scanner selected_symbols",
        },
        "strict_prior_proof": {
            "status": "PASS",
            "effective_at_is_strictly_after_latest_input": True,
            "violating_snapshots": [],
        },
        "snapshots": normalized_snapshots,
        "population": {
            "candidate_universe_digest_sha256": population["candidate_universe_digest_sha256"],
            "candidate_decision_eligibility_digest_sha256": population["candidate_decision_eligibility_digest_sha256"],
            "snapshot_count": population["snapshot_count"],
            "unique_candidate_count": population["unique_candidate_count"],
            "minimum_eligible_count": min(counts),
            "maximum_eligible_count": max(counts),
            "median_eligible_count": statistics.median(counts),
            "reference_exclusion_counts": population["exclusion_counts"],
            "reference_boundary": role,
        },
    }


def _write_json_immutable(path: Path, payload: Mapping[str, object]) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise GateError("output_conflict", f"output already exists with different bytes: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_population(
    canonical_bars: Path,
    canonical_manifest: Path,
    iwm_sidecar: Path,
    composite_manifest: Path,
    config_path: Path,
    contract_path: Path,
    output_path: Path,
    manifest_path: Path,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="m3-pit-builder-") as temporary:
        staging_dir = Path(temporary)
        contract, contract_identity = _verify_contract(contract_path, staging_dir)
        config_artifact = stage_file(config_path, staging_dir)
        config_data = _load_yaml_bytes(config_artifact.staged_path)
        config_digest = config_semantic_digest(config_data)
        if contract.get("config_semantic_digest_sha256") != config_digest:
            raise GateError("config_digest_mismatch", "settings semantic digest differs from the R3 contract")
        equity_config = _equity_config(config_data)
        canonical_staged_path, canonical_identity, _canonical_manifest = _verify_canonical_inputs(
            canonical_bars, canonical_manifest, contract, staging_dir
        )
        iwm_rows, sidecar_identity, composite = _verify_composite_inputs(
            iwm_sidecar, composite_manifest, canonical_identity, contract, staging_dir
        )
        _ = contract_identity
        groups = _merge_iwm_groups(_iter_canonical_groups(canonical_staged_path), iwm_rows)
        snapshots = _scan_groups(
            groups,
            equity_config=equity_config,
            rebalance_frequency=str(
                (config_data.get("backtest") or {}).get("scanner_rebalance", "daily")
                if isinstance(config_data.get("backtest"), Mapping)
                else "daily"
            ),
        )
        input_identity = {
            "canonical_bars": canonical_identity,
            "canonical_manifest_git_blob": canonical_identity["manifest_git_blob"],
            "iwm_sidecar": sidecar_identity,
            "composite_identity": composite.get("identity"),
            "config_semantic_digest_sha256": config_digest,
            "r3_contract": contract_identity,
        }
        payload = _population_payload(
            snapshots,
            config_digest=scanner_config_digest(equity_config),
            rebalance_frequency=str(
                (config_data.get("backtest") or {}).get("scanner_rebalance", "daily")
                if isinstance(config_data.get("backtest"), Mapping)
                else "daily"
            ),
            reference_symbols=REFERENCE_SYMBOLS,
            input_identity=input_identity,
        )
        _write_json_immutable(output_path, payload)
        raw_bytes = output_path.read_bytes()
        population = payload["population"]
        if not isinstance(population, Mapping):
            raise GateError("population_summary_missing", "PIT payload population summary is missing")
        manifest = {
            "schema_version": "1.0.0",
            "mission": R3_MISSION,
            "raw_pit_path": _logical_path(output_path),
            "raw_pit_sha256": _sha256_bytes(raw_bytes),
            "raw_pit_byte_size": len(raw_bytes),
            "semantic_population_digest_sha256": population["candidate_universe_digest_sha256"],
            "candidate_universe_digest_sha256": population["candidate_universe_digest_sha256"],
            "candidate_decision_eligibility_digest_sha256": population["candidate_decision_eligibility_digest_sha256"],
            "snapshot_count": population["snapshot_count"],
            "unique_candidate_count": population["unique_candidate_count"],
            "minimum_eligible_count": population["minimum_eligible_count"],
            "maximum_eligible_count": population["maximum_eligible_count"],
            "median_eligible_count": population["median_eligible_count"],
            "reference_exclusion_counts": population["reference_exclusion_counts"],
            "no_alpha_ranking": True,
            "candidate_rankings_present": False,
            "observation_strategy": None,
            "scanner_config_digest": scanner_config_digest(equity_config),
            "scanner_versions": payload["scanner_versions"],
            "strict_prior_proof": payload["strict_prior_proof"],
            "input_identities": input_identity,
        }
        _write_json_immutable(manifest_path, manifest)
        return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-bars", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument(
        "--iwm-normalized-sidecar", "--iwm-normalized-out", dest="iwm_sidecar", type=Path, required=True
    )
    parser.add_argument(
        "--composite-manifest", "--composite-manifest-in", dest="composite_manifest", type=Path, required=True
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--contract", type=Path, default=_REPO / R3_CONTRACT_PATH)
    parser.add_argument("--output", "--pit-universe-out", dest="output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--manifest-out", type=Path, default=DEFAULT_MANIFEST_PATH)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = build_population(
            args.canonical_bars,
            args.canonical_manifest,
            args.iwm_sidecar,
            args.composite_manifest,
            args.config,
            args.contract,
            args.output,
            args.manifest_out,
        )
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        print(f"M3_PIT_POPULATION_BLOCKED: {getattr(exc, 'category', 'input_failure')}", file=sys.stderr)
        return 20
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
