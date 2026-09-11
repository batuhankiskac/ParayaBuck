"""Build a pinned, data-only NYSE regular-session calendar for R46.

The builder reads the repository's local ``uv.lock`` and the installed
``pandas-market-calendars`` package only.  It does not use a network route,
market data, an economic worker, or PAPER/LIVE configuration.  The default
output path is intentionally only a CLI default; callers can stage an
artifact elsewhere for review before publishing it into the R46 package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

# Keep both ``python -m scripts.research...`` and direct script execution
# rooted at the repository, without consulting an external source.
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research.r46_treasury_auction_readiness_2026_09_10 import (  # noqa: E402
    NYSE_CALENDAR_SCHEMA,
    validate_nyse_calendar,
)

SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_OUTPUT = REPO / "data/research/r46_treasury_auction_unwind_tlt_v1_2026_09_10/nyse_calendar.jsonl"
DEFAULT_RECEIPT = REPO / "data/research/r46_treasury_auction_unwind_tlt_v1_2026_09_10/nyse_calendar.receipt.json"

CALENDAR_NAME = "NYSE"
VENUE = "XNYS"
START_DATE = date(2016, 1, 1)
END_DATE_EXCLUSIVE = date(2026, 8, 1)

DEPENDENCY_NAME = "pandas-market-calendars"
DEPENDENCY_IMPORT_NAME = "pandas_market_calendars"
DEPENDENCY_VERSION = "5.4.0"
DEPENDENCY_BOUNDS = ">=5.4.0,<6"
PINNED_AVAILABLE_AT = "2026-05-27T02:42:06Z"
PINNED_WHEEL_UPLOAD_TIME = "2026-05-27T02:42:06.35Z"
PINNED_WHEEL_SHA256 = "bb2b93b28d496cab173b41c7d120fd5cd9d506b31f3bb0ad3d1d9f2b60d9d9e3"
ARTIFACT_CANONICALIZATION = (
    "UTF-8 JSON, sorted object keys, compact separators, ensure_ascii=false; "
    "omit integrity.artifact_sha256 before hashing"
)
RECEIPT_SCHEMA = "r46-nyse-regular-calendar-receipt-v1"
RECEIPT_CANONICALIZATION = (
    "UTF-8 JSON, sorted object keys, compact separators, ensure_ascii=false; "
    "omit integrity.receipt_sha256 before hashing"
)
RECEIPT_PUBLICATION_SEMANTICS = {
    "order": ["records", "receipt"],
    "receipt_role": "commit_marker",
    "complete_requires": ["records", "receipt"],
    "sigkill_gap": (
        "A process kill after records publication may leave an orphan records file; "
        "without the receipt it is not COMPLETE and is never overwritten automatically."
    ),
}


def sha256_file(path: Path) -> str:
    """Return a file's raw SHA-256 without loading it into a large buffer."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_artifact_hash(payload: Mapping[str, Any]) -> str:
    """Hash an artifact after removing only its nested self-hash field."""

    value = json.loads(json.dumps(payload, ensure_ascii=False))
    integrity = value.get("integrity") if isinstance(value, dict) else None
    if not isinstance(integrity, dict):
        raise ValueError("artifact_integrity_missing")
    integrity.pop("artifact_sha256", None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def canonical_receipt_hash(payload: Mapping[str, Any]) -> str:
    """Hash a receipt after removing only its nested self-hash field."""

    value = json.loads(json.dumps(payload, ensure_ascii=False))
    integrity = value.get("integrity") if isinstance(value, dict) else None
    if not isinstance(integrity, dict):
        raise ValueError("receipt_integrity_missing")
    integrity.pop("receipt_sha256", None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _installed_dependency_version() -> str:
    try:
        return importlib_metadata.version(DEPENDENCY_NAME)
    except importlib_metadata.PackageNotFoundError as exc:
        raise ValueError("dependency_missing:pandas-market-calendars") from exc


def _normalize_upload_time(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("lock_upload_timestamp_missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("lock_upload_timestamp_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("lock_upload_timestamp_timezone_required")
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find_package(lock: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise ValueError("uv_lock_package_table_missing")
    for package in packages:
        if isinstance(package, dict) and package.get("name") == name:
            return package
    raise ValueError(f"uv_lock_package_missing:{name}")


def _lock_bounds(lock: Mapping[str, Any]) -> str:
    root = _find_package(lock, "parayabuck")
    metadata = root.get("metadata")
    requirements = metadata.get("requires-dist") if isinstance(metadata, dict) else None
    if not isinstance(requirements, list):
        raise ValueError("uv_lock_dependency_metadata_missing")
    for requirement in requirements:
        if isinstance(requirement, dict) and requirement.get("name") == DEPENDENCY_NAME:
            specifier = requirement.get("specifier")
            if isinstance(specifier, str) and specifier.strip():
                return specifier
    raise ValueError("uv_lock_dependency_bounds_missing")


def read_dependency_pin(lock_path: Path, *, installed_version: str | None = None) -> dict[str, Any]:
    """Read and validate the exact local dependency pin used by the builder."""

    lock_path = Path(lock_path)
    try:
        lock_text = lock_path.read_text(encoding="utf-8")
        lock = tomllib.loads(lock_text)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"uv_lock_unreadable:{lock_path}") from exc

    package = _find_package(lock, DEPENDENCY_NAME)
    package_version = package.get("version")
    if package_version != DEPENDENCY_VERSION:
        raise ValueError(f"lock_dependency_version_mismatch:{package_version!r}")
    bounds = _lock_bounds(lock)
    if bounds != DEPENDENCY_BOUNDS:
        raise ValueError(f"lock_dependency_bounds_mismatch:{bounds!r}")

    actual_version = _installed_dependency_version() if installed_version is None else installed_version
    if actual_version != DEPENDENCY_VERSION:
        raise ValueError(f"dependency_version_mismatch:{actual_version!r}")

    wheels = package.get("wheels")
    if not isinstance(wheels, list) or not wheels:
        raise ValueError("lock_dependency_wheel_missing")
    wheel = next(
        (
            candidate
            for candidate in wheels
            if isinstance(candidate, dict)
            and isinstance(candidate.get("url"), str)
            and candidate["url"].endswith("pandas_market_calendars-5.4.0-py3-none-any.whl")
        ),
        None,
    )
    if not isinstance(wheel, dict):
        raise ValueError("lock_dependency_wheel_missing")
    wheel_hash = wheel.get("hash")
    if wheel_hash != f"sha256:{PINNED_WHEEL_SHA256}":
        raise ValueError("lock_dependency_wheel_hash_mismatch")
    upload_time = wheel.get("upload-time")
    if upload_time != PINNED_WHEEL_UPLOAD_TIME or _normalize_upload_time(upload_time) != PINNED_AVAILABLE_AT:
        raise ValueError("lock_upload_timestamp_mismatch")

    return {
        "name": DEPENDENCY_NAME,
        "import_name": DEPENDENCY_IMPORT_NAME,
        "version": DEPENDENCY_VERSION,
        "bounds": DEPENDENCY_BOUNDS,
        "lock": {
            "path": lock_path.name,
            "sha256": sha256_file(lock_path),
            "package_version": package_version,
            "wheel_sha256": PINNED_WHEEL_SHA256,
            "wheel_upload_time": upload_time,
            "release_timestamp": PINNED_AVAILABLE_AT,
        },
    }


def _load_calendar_library() -> Any:
    try:
        import pandas_market_calendars as market_calendars
    except ImportError as exc:
        raise ValueError("dependency_missing:pandas-market-calendars") from exc
    return market_calendars


def _utc_timestamp(value: Any, field: str) -> str:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"calendar_{field}_timezone_missing")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_session_rows(
    start: date = START_DATE,
    end_exclusive: date = END_DATE_EXCLUSIVE,
) -> list[dict[str, Any]]:
    """Build one row per NYSE regular session, preserving early closes."""

    if not isinstance(start, date) or not isinstance(end_exclusive, date) or start >= end_exclusive:
        raise ValueError("calendar_window_invalid")
    market_calendars = _load_calendar_library()
    try:
        calendar = market_calendars.get_calendar(CALENDAR_NAME)
        schedule = calendar.schedule(
            start_date=start.isoformat(),
            end_date=(end_exclusive - timedelta(days=1)).isoformat(),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("nyse_schedule_unavailable") from exc

    rows: list[dict[str, Any]] = []
    for session, values in schedule.iterrows():
        session_date = session.date() if hasattr(session, "date") else session
        if not isinstance(session_date, date) or not start <= session_date < end_exclusive:
            continue
        try:
            open_value = values["market_open"]
            close_value = values["market_close"]
        except (KeyError, TypeError) as exc:
            raise ValueError("nyse_schedule_regular_columns_missing") from exc
        rows.append(
            {
                "regular_session_date": session_date.isoformat(),
                "regular_open_timestamp": _utc_timestamp(open_value, "regular_open_timestamp"),
                "regular_close_timestamp": _utc_timestamp(close_value, "regular_close_timestamp"),
                "holiday_or_closure_reason": None,
            }
        )
    if not rows:
        raise ValueError("nyse_schedule_empty")
    return rows


def _path_reference(path: Path, repo: Path) -> str:
    path = path.resolve()
    try:
        return path.relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


def _dependency_provenance(dependency: dict[str, Any], *, lock_path: Path, repo: Path) -> dict[str, Any]:
    generator_path = _path_reference(SCRIPT_PATH, repo)
    generator_sha256 = sha256_file(SCRIPT_PATH)
    lock_reference = _path_reference(lock_path, repo)
    return {
        "dependency": dependency,
        "uv_lock_path": lock_reference,
        "uv_lock_sha256": dependency["lock"]["sha256"],
        "dependency_name": DEPENDENCY_NAME,
        "dependency_import_name": DEPENDENCY_IMPORT_NAME,
        "dependency_version": DEPENDENCY_VERSION,
        "dependency_bounds": DEPENDENCY_BOUNDS,
        "generator": {
            "path": generator_path,
            "sha256": generator_sha256,
            "provenance": "local tracked generator source; no network or runtime timestamp",
        },
        "generator_path": generator_path,
        "generator_sha256": generator_sha256,
        "generator_provenance": "local tracked generator source; no network or runtime timestamp",
        "available_at_basis": (
            "Pinned package wheel upload/release timestamp from uv.lock; "
            "this is source provenance, not artifact generation time"
        ),
        "network_used": False,
    }


def build_calendar_artifact(
    repo: Path = REPO,
    *,
    lock_path: Path | None = None,
    start: date = START_DATE,
    end_exclusive: date = END_DATE_EXCLUSIVE,
) -> dict[str, Any]:
    """Return a deterministic, self-hashed R46 calendar artifact."""

    repo = Path(repo).resolve()
    lock_path = (repo / "uv.lock") if lock_path is None else Path(lock_path).resolve()
    dependency = read_dependency_pin(lock_path)
    rows = build_session_rows(start, end_exclusive)
    validated = validate_nyse_calendar(rows, available_at=PINNED_AVAILABLE_AT)
    if validated["schema"] != NYSE_CALENDAR_SCHEMA or validated["status"] != "COMPLETE":
        raise ValueError("nyse_calendar_validation_failed")

    artifact: dict[str, Any] = {
        "schema": NYSE_CALENDAR_SCHEMA,
        "status": "COMPLETE",
        "calendar": CALENDAR_NAME,
        "venue": VENUE,
        "window": {"start": start.isoformat(), "end_exclusive": end_exclusive.isoformat()},
        "available_at": PINNED_AVAILABLE_AT,
        "row_count": len(rows),
        "records": rows,
        "provenance": _dependency_provenance(dependency, lock_path=lock_path, repo=repo),
        "economic_execution": False,
        "economic_data": False,
        "integrity": {"canonicalization": ARTIFACT_CANONICALIZATION, "artifact_sha256": ""},
    }
    artifact["integrity"]["artifact_sha256"] = canonical_artifact_hash(artifact)
    verify_artifact(artifact, repo)
    return artifact


def verify_artifact(artifact: Mapping[str, Any], repo: Path = REPO) -> dict[str, Any]:
    """Verify metadata, dependency provenance, self-hash, and R46 row schema."""

    if not isinstance(artifact, Mapping):
        raise ValueError("artifact_object_required")
    if artifact.get("schema") != NYSE_CALENDAR_SCHEMA or artifact.get("status") != "COMPLETE":
        raise ValueError("artifact_schema_or_status_invalid")
    if artifact.get("calendar") != CALENDAR_NAME or artifact.get("venue") != VENUE:
        raise ValueError("artifact_calendar_identity_invalid")
    if artifact.get("window") != {"start": START_DATE.isoformat(), "end_exclusive": END_DATE_EXCLUSIVE.isoformat()}:
        raise ValueError("artifact_window_invalid")
    if artifact.get("available_at") != PINNED_AVAILABLE_AT:
        raise ValueError("artifact_available_at_invalid")
    integrity = artifact.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("canonicalization") != ARTIFACT_CANONICALIZATION:
        raise ValueError("artifact_integrity_invalid")
    declared_hash = integrity.get("artifact_sha256")
    if not isinstance(declared_hash, str) or declared_hash != canonical_artifact_hash(artifact):
        raise ValueError("artifact_self_hash_mismatch")

    records = artifact.get("records")
    if not isinstance(records, list) or artifact.get("row_count") != len(records):
        raise ValueError("artifact_row_count_invalid")
    validated = validate_nyse_calendar(records, available_at=PINNED_AVAILABLE_AT)
    if validated["rows"] != len(records):
        raise ValueError("artifact_calendar_row_validation_failed")

    provenance = artifact.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("artifact_provenance_missing")
    dependency = provenance.get("dependency")
    if not isinstance(dependency, Mapping):
        raise ValueError("artifact_dependency_provenance_missing")
    repo = Path(repo).resolve()
    lock_path_value = dependency.get("lock", {}).get("path") if isinstance(dependency.get("lock"), Mapping) else None
    if not isinstance(lock_path_value, str) or Path(lock_path_value).is_absolute() or ".." in Path(lock_path_value).parts:
        raise ValueError("artifact_lock_path_invalid")
    expected_dependency = read_dependency_pin(repo / lock_path_value, installed_version=dependency.get("version"))
    if dict(dependency) != expected_dependency:
        raise ValueError("artifact_dependency_provenance_mismatch")
    generator = provenance.get("generator") if isinstance(provenance, Mapping) else None
    generator_sha256 = sha256_file(SCRIPT_PATH)
    if (
        not isinstance(generator, Mapping)
        or generator.get("sha256") != generator_sha256
        or provenance.get("generator_sha256") != generator_sha256
        or provenance.get("generator_path") != generator.get("path")
        or provenance.get("generator_provenance") != generator.get("provenance")
    ):
        raise ValueError("artifact_generator_provenance_mismatch")
    return {
        "schema": NYSE_CALENDAR_SCHEMA,
        "status": "COMPLETE",
        "rows": len(records),
        "artifact_sha256": declared_hash,
    }


def canonical_jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    """Serialize records exactly as the R46 preflight JSONL reader consumes them."""

    if not isinstance(records, list):
        raise ValueError("jsonl_records_list_required")
    return b"".join(
        (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        for record in records
    )


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"jsonl_unreadable:{path}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"jsonl_invalid:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"jsonl_row_not_object:{line_number}")
        records.append(value)
    return records


def _default_receipt_path(records_path: Path) -> Path:
    return Path(records_path).with_suffix(".receipt.json")


def _resolve_declared_records_path(
    declared_path: Any,
    *,
    repo: Path,
    explicit_path: Path | None,
) -> Path:
    if not isinstance(declared_path, str) or not declared_path.strip():
        raise ValueError("receipt_records_path_missing")
    candidate = Path(declared_path)
    if not candidate.is_absolute() and ".." in candidate.parts:
        raise ValueError("receipt_records_path_unsafe")
    resolved = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
    if explicit_path is not None and resolved != Path(explicit_path).resolve():
        raise ValueError("receipt_records_path_mismatch")
    if not resolved.is_file():
        raise ValueError(f"records_file_missing:{resolved}")
    return resolved


def verify_receipt(
    receipt: Mapping[str, Any],
    records_path: Path | None = None,
    repo: Path = REPO,
) -> dict[str, Any]:
    """Verify a JSONL records file and its hash/provenance-bound receipt."""

    if not isinstance(receipt, Mapping):
        raise ValueError("receipt_object_required")
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("status") != "COMPLETE":
        raise ValueError("receipt_schema_or_status_invalid")
    if receipt.get("artifact_schema") != NYSE_CALENDAR_SCHEMA:
        raise ValueError("receipt_artifact_schema_invalid")
    if receipt.get("calendar") != CALENDAR_NAME or receipt.get("venue") != VENUE:
        raise ValueError("receipt_calendar_identity_invalid")
    if receipt.get("window") != {"start": START_DATE.isoformat(), "end_exclusive": END_DATE_EXCLUSIVE.isoformat()}:
        raise ValueError("receipt_window_invalid")
    if receipt.get("available_at") != PINNED_AVAILABLE_AT:
        raise ValueError("receipt_available_at_invalid")
    if receipt.get("publication") != RECEIPT_PUBLICATION_SEMANTICS:
        raise ValueError("receipt_publication_semantics_invalid")

    integrity = receipt.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("canonicalization") != RECEIPT_CANONICALIZATION:
        raise ValueError("receipt_integrity_invalid")
    declared_receipt_hash = integrity.get("receipt_sha256")
    if not isinstance(declared_receipt_hash, str) or declared_receipt_hash != canonical_receipt_hash(receipt):
        raise ValueError("receipt_self_hash_mismatch")

    record_spec = receipt.get("records")
    if not isinstance(record_spec, Mapping):
        raise ValueError("receipt_records_spec_missing")
    declared_raw_hash = record_spec.get("sha256")
    if record_spec.get("raw_sha256") != declared_raw_hash or not isinstance(declared_raw_hash, str):
        raise ValueError("receipt_records_hash_invalid")
    declared_row_count = record_spec.get("row_count")
    if not isinstance(declared_row_count, int) or isinstance(declared_row_count, bool) or declared_row_count < 1:
        raise ValueError("receipt_records_row_count_invalid")
    repo = Path(repo).resolve()
    records_file = _resolve_declared_records_path(record_spec.get("path"), repo=repo, explicit_path=records_path)
    raw_bytes = records_file.read_bytes()
    if hashlib.sha256(raw_bytes).hexdigest() != declared_raw_hash:
        raise ValueError("records_raw_hash_mismatch")
    records = _read_jsonl_records(records_file)
    if len(records) != declared_row_count:
        raise ValueError("receipt_records_row_count_mismatch")
    validated = validate_nyse_calendar(records, available_at=receipt["available_at"])
    if validated["schema"] != NYSE_CALENDAR_SCHEMA or validated["status"] != "COMPLETE":
        raise ValueError("receipt_records_validation_failed")

    if receipt.get("records_path") != record_spec.get("path") or receipt.get("records_sha256") != declared_raw_hash:
        raise ValueError("receipt_records_alias_mismatch")
    if receipt.get("row_count") != declared_row_count:
        raise ValueError("receipt_row_count_alias_mismatch")
    provenance = receipt.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("receipt_provenance_missing")
    dependency = provenance.get("dependency")
    if not isinstance(dependency, Mapping):
        raise ValueError("receipt_dependency_provenance_missing")
    lock_path_value = dependency.get("lock", {}).get("path") if isinstance(dependency.get("lock"), Mapping) else None
    if not isinstance(lock_path_value, str) or Path(lock_path_value).is_absolute() or ".." in Path(lock_path_value).parts:
        raise ValueError("receipt_lock_path_invalid")
    expected_dependency = read_dependency_pin(repo / lock_path_value, installed_version=dependency.get("version"))
    if dict(dependency) != expected_dependency:
        raise ValueError("receipt_dependency_provenance_mismatch")
    generator = provenance.get("generator") if isinstance(provenance, Mapping) else None
    generator_sha256 = sha256_file(SCRIPT_PATH)
    if (
        not isinstance(generator, Mapping)
        or generator.get("sha256") != generator_sha256
        or provenance.get("generator_sha256") != generator_sha256
        or provenance.get("generator_path") != generator.get("path")
        or provenance.get("generator_provenance") != generator.get("provenance")
    ):
        raise ValueError("receipt_generator_provenance_mismatch")
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "COMPLETE",
        "rows": declared_row_count,
        "records_sha256": declared_raw_hash,
        "receipt_sha256": declared_receipt_hash,
    }


def verify_receipt_file(
    receipt_path: Path,
    records_path: Path | None = None,
    repo: Path = REPO,
) -> dict[str, Any]:
    try:
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"receipt_unreadable:{receipt_path}") from exc
    return verify_receipt(receipt, records_path=records_path, repo=repo)


def verify_artifact_file(
    path: Path,
    repo: Path = REPO,
    *,
    receipt: Path | None = None,
) -> dict[str, Any]:
    """Verify a preflight JSONL calendar and its sidecar receipt."""

    records_path = Path(path)
    receipt_path = _default_receipt_path(records_path) if receipt is None else Path(receipt)
    return verify_receipt_file(receipt_path, records_path=records_path, repo=repo)


def _check_output_pair(output: Path, receipt: Path) -> None:
    output_resolved = output.resolve()
    receipt_resolved = receipt.resolve()
    if output_resolved == receipt_resolved:
        raise ValueError("records_and_receipt_paths_must_differ")
    collisions = [path for path in (output, receipt) if path.exists()]
    if collisions:
        raise FileExistsError(f"refusing to overwrite existing artifact: {collisions[0]}")


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _stage_payload(path: Path, payload: bytes) -> Path:
    """Stage one payload and remove its temporary file on every write failure."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    handle = None
    try:
        handle = os.fdopen(descriptor, "wb")
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        return temporary
    except BaseException:
        if handle is None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        else:
            try:
                handle.close()
            except OSError:
                pass
        _best_effort_unlink(temporary)
        raise


def _fsync_directory(directory: Path) -> None:
    """Durably publish a directory entry before the next commit step."""

    descriptor = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_published_if_ours(target: Path, temporary: Path) -> None:
    """Rollback only a regular file still linked from this writer's temp inode."""

    try:
        target_stat = os.lstat(target)
        temporary_stat = os.stat(temporary)
        if (
            stat.S_ISREG(target_stat.st_mode)
            and target_stat.st_dev == temporary_stat.st_dev
            and target_stat.st_ino == temporary_stat.st_ino
        ):
            target.unlink()
    except OSError:
        pass


def _write_pair_atomically_no_overwrite(targets: Mapping[Path, bytes]) -> None:
    """Publish records first and receipt last; the receipt is the commit marker.

    This is deliberately not a cross-file atomic transaction.  A process kill
    after the durable records publish and before the receipt publish may leave
    an orphan records file.  The verifier requires both files, and the writer
    never removes or overwrites an existing user file.  Ordinary exceptions
    are rolled back for targets this writer successfully linked.
    """

    if len(targets) != 2:
        raise ValueError("records_and_receipt_targets_required")
    staged: list[tuple[Path, Path]] = []
    published: list[tuple[Path, Path]] = []
    try:
        for target, payload in targets.items():
            staged.append((target, _stage_payload(target, payload)))

        records_target, records_temporary = staged[0]
        receipt_target, receipt_temporary = staged[1]
        try:
            os.link(records_temporary, records_target)
        except FileExistsError as exc:
            raise FileExistsError(f"refusing to overwrite existing artifact: {records_target}") from exc
        published.append((records_target, records_temporary))
        _fsync_directory(records_target.parent)

        try:
            os.link(receipt_temporary, receipt_target)
        except FileExistsError as exc:
            raise FileExistsError(f"refusing to overwrite existing artifact: {receipt_target}") from exc
        published.append((receipt_target, receipt_temporary))
        _fsync_directory(receipt_target.parent)
    except BaseException:
        for target, temporary in reversed(published):
            _unlink_published_if_ours(target, temporary)
        raise
    finally:
        for _, temporary in staged:
            _best_effort_unlink(temporary)


def write_artifact(
    output: Path,
    repo: Path = REPO,
    *,
    receipt: Path | None = None,
) -> dict[str, Any]:
    """Create JSONL records, then the receipt commit marker, without replacement.

    Records are linked and directory-synced before the receipt is linked and
    directory-synced.  The two files are therefore not claimed to publish as
    one transaction: a process kill in the gap can leave orphan records, but
    no receipt means verification remains fail-closed and no cleanup can
    overwrite a later user's file.
    """

    output = Path(output)
    receipt_path = _default_receipt_path(output) if receipt is None else Path(receipt)
    _check_output_pair(output, receipt_path)
    artifact = build_calendar_artifact(repo)
    records = artifact["records"]
    records_bytes = canonical_jsonl_bytes(records)
    records_sha256 = hashlib.sha256(records_bytes).hexdigest()
    record_reference = _path_reference(output, Path(repo).resolve())
    receipt_payload: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "COMPLETE",
        "artifact_schema": NYSE_CALENDAR_SCHEMA,
        "calendar": CALENDAR_NAME,
        "venue": VENUE,
        "window": artifact["window"],
        "available_at": artifact["available_at"],
        "records": {
            "path": record_reference,
            "sha256": records_sha256,
            "raw_sha256": records_sha256,
            "row_count": len(records),
            "format": "UTF-8 JSONL",
            "record_schema": NYSE_CALENDAR_SCHEMA,
        },
        "records_path": record_reference,
        "records_sha256": records_sha256,
        "row_count": len(records),
        "provenance": artifact["provenance"],
        "publication": json.loads(json.dumps(RECEIPT_PUBLICATION_SEMANTICS)),
        "economic_execution": False,
        "economic_data": False,
        "network_used": False,
        "integrity": {"canonicalization": RECEIPT_CANONICALIZATION, "receipt_sha256": ""},
    }
    receipt_payload["integrity"]["receipt_sha256"] = canonical_receipt_hash(receipt_payload)
    receipt_bytes = (json.dumps(receipt_payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    _write_pair_atomically_no_overwrite({output: records_bytes, receipt_path: receipt_bytes})
    return {
        "schema": NYSE_CALENDAR_SCHEMA,
        "status": "COMPLETE",
        "path": str(output),
        "receipt_path": str(receipt_path),
        "rows": len(records),
        "records_sha256": records_sha256,
        "receipt_sha256": receipt_payload["integrity"]["receipt_sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the R46 pinned NYSE regular-session calendar")
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help=f"receipt sidecar path (default: {DEFAULT_RECEIPT})",
    )
    args = parser.parse_args(argv)
    try:
        result = write_artifact(args.output, args.repo, receipt=args.receipt)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
