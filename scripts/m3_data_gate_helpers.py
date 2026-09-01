"""Pure, fail-closed helpers for the research-only M3 DATA GATE.

The module deliberately has no broker, runtime, cache, or strategy-registry
dependency.  It consumes staged bytes, normalizes only declared source data,
and exposes the small deterministic contracts used by the command-line gate
and its hermetic tests.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import struct
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

REFERENCE_SYMBOLS = ("SPY", "IWM", "QQQ")
CANONICAL_COLUMNS = (
    "session_date",
    "timestamp",
    "symbol",
    "timeframe",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "market",
)
CANONICAL_TIMEFRAME = "1d"
RAW_SOURCE_TIMEFRAME = "1Day"
MARKET = "EQUITY"
EXCHANGE_TIMEZONE = "America/New_York"
SOURCE_START = date(2016, 1, 1)
SOURCE_END = date(2026, 7, 31)
EVALUATION_END = date(2026, 7, 30)
RECENT_START = date(2022, 1, 1)
NORMALIZER_VERSION = "m3-iwm-normalizer-v1"
ROW_ENCODING_VERSION = "m3-normalized-row-v1"
MATRIX_ENCODING_VERSION = "m3-design-matrix-v1"
SESSION_ENCODING_VERSION = "m3-session-set-v1"
REFERENCE_PANEL_ENCODING_VERSION = "m3-reference-panel-v1"

DEFAULT_NUMERICAL_THRESHOLDS: dict[str, int | float] = {
    "matrix_rows": 273,
    "matrix_columns": 3,
    "required_rank": 3,
    "minimum_singular_value_ratio": 1e-6,
    "maximum_condition_number": 1e6,
    "minimum_residual_std": 1e-12,
    "required_common_closes": 274,
    "required_returns": 273,
}


class GateError(ValueError):
    """A classified, fail-closed data-gate error."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        super().__init__(message)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, datetime):
        return canonical_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a value with stable ordering and no platform defaults."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")


def canonical_digest(value: object) -> str:
    return _sha256_bytes(canonical_json_bytes(value))


def _normalised_newline_update(digest: Any, chunk: bytes, pending_cr: bool) -> bool:
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
    return pending_cr


def _stat_identity(path: Path) -> dict[str, int]:
    stat_result = path.stat()
    return {
        "device": int(stat_result.st_dev),
        "inode": int(stat_result.st_ino),
        "size": int(stat_result.st_size),
        "mtime_ns": int(stat_result.st_mtime_ns),
    }


@dataclass(frozen=True)
class StagedArtifact:
    """A content-addressed copy and the identity proof of its source."""

    staged_path: Path
    raw_sha256: str
    crlf_normalized_sha256: str
    byte_size: int
    source_identity_before: dict[str, int]
    source_identity_after: dict[str, int]

    def receipt(self, logical_artifact_id: str, provenance_class: str) -> dict[str, object]:
        return {
            "logical_artifact_id": logical_artifact_id,
            "provenance_class": provenance_class,
            "raw_sha256": self.raw_sha256,
            "crlf_normalized_sha256": self.crlf_normalized_sha256,
            "byte_size": self.byte_size,
            "source_identity_before": self.source_identity_before,
            "source_identity_after": self.source_identity_after,
            "parse_source": "content_addressed_staged_file",
        }


def stage_file(
    source: str | Path,
    staging_dir: str | Path,
    *,
    chunk_size: int = 1 << 20,
    after_copy_hook: Callable[[Path], None] | None = None,
) -> StagedArtifact:
    """Stage, hash, and later parse one exact source byte stream.

    The source is never reopened for parsing.  A second identity check after
    the copy rejects replacement, truncation, or timestamp mutation during
    staging.  ``after_copy_hook`` exists only to make that boundary testable.
    """
    source_path = Path(source)
    if not source_path.is_file():
        raise GateError("missing_artifact", f"missing external artifact: {source_path.name}")
    if source_path.is_symlink():
        raise GateError("mutable_symlink_artifact", f"external artifact must not be a symlink: {source_path.name}")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    target_dir = Path(staging_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    before = _stat_identity(source_path)
    raw_digest = hashlib.sha256()
    normalized_digest = hashlib.sha256()
    pending_cr = False
    temp_path: Path | None = None
    total = 0
    try:
        with source_path.open("rb") as source_handle:
            with tempfile.NamedTemporaryFile(dir=target_dir, prefix="stage-", delete=False) as target_handle:
                temp_path = Path(target_handle.name)
                while chunk := source_handle.read(chunk_size):
                    target_handle.write(chunk)
                    raw_digest.update(chunk)
                    pending_cr = _normalised_newline_update(normalized_digest, chunk, pending_cr)
                    total += len(chunk)
                target_handle.flush()
                os.fsync(target_handle.fileno())
        if pending_cr:
            normalized_digest.update(b"\r")
        if after_copy_hook is not None:
            after_copy_hook(source_path)
        after = _stat_identity(source_path)
        if before != after:
            raise GateError(
                "input_mutated_during_staging", f"external artifact changed while staging: {source_path.name}"
            )
        assert temp_path is not None
        final_path = target_dir / raw_digest.hexdigest()
        os.replace(temp_path, final_path)
        temp_path = None
        return StagedArtifact(
            staged_path=final_path,
            raw_sha256=raw_digest.hexdigest(),
            crlf_normalized_sha256=normalized_digest.hexdigest(),
            byte_size=total,
            source_identity_before=before,
            source_identity_after=after,
        )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GateError("naive_timestamp", "timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: object) -> datetime:
    text = str(value).strip()
    if not text:
        raise GateError("missing_timestamp", "timestamp is empty")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError("invalid_timestamp", f"invalid timestamp: {text}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GateError("naive_timestamp", "timestamp must be timezone-aware")
    return parsed


def session_date_for_timestamp(timestamp: datetime) -> date:
    """Resolve the exchange session from a tz-aware timestamp only."""
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise GateError("naive_timestamp", "cannot derive an exchange session from a naive timestamp")
    return timestamp.astimezone(ZoneInfo(EXCHANGE_TIMEZONE)).date()


def decimal_value(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise GateError("nonfinite_value", f"{field} must be numeric")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise GateError("nonfinite_value", f"{field} is not a decimal") from exc
    if not parsed.is_finite():
        raise GateError("nonfinite_value", f"{field} is non-finite")
    return parsed


def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise GateError("nonfinite_value", "cannot serialize a non-finite decimal")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def normalise_timeframe(token: object, *, raw_source: bool) -> str:
    value = str(token).strip() if token is not None else ""
    if raw_source:
        if value != RAW_SOURCE_TIMEFRAME:
            raise GateError("timeframe_mismatch", f"raw source timeframe must be {RAW_SOURCE_TIMEFRAME}")
        return CANONICAL_TIMEFRAME
    if value != CANONICAL_TIMEFRAME:
        raise GateError("timeframe_mismatch", f"canonical timeframe must be {CANONICAL_TIMEFRAME}")
    return CANONICAL_TIMEFRAME


def _validate_ohlc(open_value: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: Decimal) -> None:
    if any(value <= 0 for value in (open_value, high, low, close)):
        raise GateError("nonpositive_ohlc", "OHLC values must be positive")
    if volume < 0:
        raise GateError("negative_volume", "volume must be non-negative")
    if not low <= open_value <= high or not low <= close <= high or high < low:
        raise GateError("invalid_ohlc", "OHLC ordering is invalid")


@dataclass(frozen=True)
class NormalizedBar:
    session_date: date
    timestamp: datetime
    symbol: str
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    market: str = MARKET

    @property
    def key(self) -> tuple[date, str, str]:
        return self.session_date, self.symbol, self.timeframe

    @property
    def sort_key(self) -> tuple[date, str, str, str]:
        return self.session_date, self.symbol, self.timeframe, canonical_timestamp(self.timestamp)

    def semantic_fields(self) -> tuple[str, ...]:
        return (
            self.session_date.isoformat(),
            canonical_timestamp(self.timestamp),
            self.symbol,
            self.timeframe,
            decimal_text(self.open),
            decimal_text(self.high),
            decimal_text(self.low),
            decimal_text(self.close),
            decimal_text(self.volume),
            self.market,
        )

    def to_csv_row(self) -> dict[str, str]:
        fields = self.semantic_fields()
        return dict(zip(CANONICAL_COLUMNS, fields, strict=True))


def normalize_provider_bar(
    raw: Mapping[str, object],
    *,
    source_start: date = SOURCE_START,
    source_end: date = SOURCE_END,
) -> NormalizedBar:
    source_symbol = raw.get("S", raw.get("symbol"))
    if str(source_symbol or "").strip().upper() != "IWM":
        raise GateError("source_symbol_mismatch", "raw source bar symbol must be exactly IWM")
    if "timeframe" in raw or "tf" in raw:
        token = raw.get("timeframe", raw.get("tf"))
        normalise_timeframe(token, raw_source=True)
    timestamp = parse_timestamp(raw.get("t", raw.get("timestamp")))
    session = session_date_for_timestamp(timestamp)
    if not source_start <= session <= source_end:
        raise GateError("source_range_violation", f"row session {session} is outside the frozen source range")
    open_value = decimal_value(raw.get("o", raw.get("open")), "open")
    high_value = decimal_value(raw.get("h", raw.get("high")), "high")
    low_value = decimal_value(raw.get("l", raw.get("low")), "low")
    close_value = decimal_value(raw.get("c", raw.get("close")), "close")
    volume = decimal_value(raw.get("v", raw.get("volume")), "volume")
    _validate_ohlc(open_value, high_value, low_value, close_value, volume)
    return NormalizedBar(
        session_date=session,
        timestamp=timestamp,
        symbol="IWM",
        timeframe=CANONICAL_TIMEFRAME,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=volume,
    )


def normalize_csv_row(
    raw: Mapping[str, object],
    *,
    source_start: date = SOURCE_START,
    source_end: date = SOURCE_END,
) -> NormalizedBar:
    symbol = str(raw.get("symbol") or "").strip().upper()
    if not symbol:
        raise GateError("missing_symbol", "CSV row symbol is empty")
    timestamp = parse_timestamp(raw.get("timestamp"))
    session = session_date_for_timestamp(timestamp)
    if not source_start <= session <= source_end:
        raise GateError("source_range_violation", f"row session {session} is outside the frozen source range")
    timeframe = normalise_timeframe(raw.get("timeframe"), raw_source=False)
    market = str(raw.get("market") or "").strip().upper()
    if market != MARKET:
        raise GateError("market_mismatch", "canonical reference rows must have market EQUITY")
    open_value = decimal_value(raw.get("open"), "open")
    high_value = decimal_value(raw.get("high"), "high")
    low_value = decimal_value(raw.get("low"), "low")
    close_value = decimal_value(raw.get("close"), "close")
    volume = decimal_value(raw.get("volume") or "0", "volume")
    _validate_ohlc(open_value, high_value, low_value, close_value, volume)
    return NormalizedBar(
        session_date=session,
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=volume,
        market=MARKET,
    )


def _encode_rows(rows: Iterable[NormalizedBar], *, include_header: bool) -> bytes:
    lines: list[str] = []
    if include_header:
        lines.append(ROW_ENCODING_VERSION + "\n")
        lines.append("\t".join(CANONICAL_COLUMNS) + "\n")
    for row in sorted(rows, key=lambda item: item.sort_key):
        lines.append("\t".join(row.semantic_fields()) + "\n")
    return "".join(lines).encode("utf-8")


def normalized_rows_digest(rows: Iterable[NormalizedBar]) -> str:
    return _sha256_bytes(_encode_rows(rows, include_header=True))


def semantic_rows_digest(rows: Iterable[NormalizedBar]) -> str:
    return _sha256_bytes(_encode_rows(rows, include_header=False))


def session_dates_digest(session_dates: Iterable[date | str]) -> str:
    ordered = sorted(str(value) for value in session_dates)
    payload = SESSION_ENCODING_VERSION + "\n" + "\n".join(ordered) + "\n"
    return _sha256_bytes(payload.encode("utf-8"))


def reference_panel_digest(
    rows: Iterable[NormalizedBar],
    *,
    source_identity: Mapping[str, object],
) -> str:
    ordered = [row.semantic_fields() for row in sorted(rows, key=lambda item: item.sort_key)]
    return canonical_digest(
        {
            "encoding": REFERENCE_PANEL_ENCODING_VERSION,
            "source_identity": dict(source_identity),
            "rows": ordered,
        }
    )


def ensure_unique_rows(rows: Sequence[NormalizedBar], *, category: str = "duplicate_bar") -> None:
    seen: dict[tuple[date, str, str], NormalizedBar] = {}
    for row in rows:
        prior = seen.get(row.key)
        if prior is not None:
            if prior == row:
                raise GateError(category, f"identical duplicate bar: {row.symbol} {row.session_date}")
            raise GateError("conflicting_duplicate_bar", f"conflicting duplicate bar: {row.symbol} {row.session_date}")
        seen[row.key] = row


def expected_exchange_sessions(start: date, end: date, *, calendar_name: str = "NYSE") -> tuple[date, ...]:
    """Return real regular U.S. equity sessions from the pinned dependency."""
    if start > end:
        return ()
    try:
        import pandas_market_calendars as market_calendars
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise GateError("calendar_dependency_missing", "pandas-market-calendars is required") from exc
    calendar = market_calendars.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=start.isoformat(), end_date=end.isoformat())
    return tuple(index.date() for index in schedule.index)


def calendar_dependency_identity() -> dict[str, str]:
    try:
        import importlib.metadata

        version = importlib.metadata.version("pandas-market-calendars")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - environment-specific
        version = "unknown"
    return {"name": "pandas_market_calendars", "version": version, "calendar": "NYSE"}


def _max_consecutive_missing(missing: Sequence[date], expected: Sequence[date]) -> int:
    if not missing:
        return 0
    missing_set = set(missing)
    best = 0
    run = 0
    for session in expected:
        if session in missing_set:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _count_duplicate_keys(rows: Sequence[NormalizedBar]) -> tuple[int, int]:
    by_key: dict[tuple[date, str, str], list[NormalizedBar]] = {}
    for row in rows:
        by_key.setdefault(row.key, []).append(row)
    duplicate_count = 0
    conflicting_count = 0
    for values in by_key.values():
        if len(values) <= 1:
            continue
        duplicate_count += len(values) - 1
        if any(value != values[0] for value in values[1:]):
            conflicting_count += len(values) - 1
    return duplicate_count, conflicting_count


def build_reference_inventory(
    rows: Sequence[NormalizedBar],
    *,
    raw_quality: Mapping[str, int] | None = None,
    expected_sessions: Sequence[date],
    evaluation_end: date = EVALUATION_END,
    source_identity: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Summarize one reference without treating total rows as coverage."""
    raw_quality = raw_quality or {}
    ordered = sorted(rows, key=lambda item: item.sort_key)
    in_evaluation = [row for row in ordered if row.session_date <= evaluation_end]
    sessions = {row.session_date for row in in_evaluation}
    missing = sorted(set(expected_sessions) - sessions)
    extra = sorted(sessions - set(expected_sessions))
    duplicate_count, conflicting_count = _count_duplicate_keys(in_evaluation)
    timeframe_values = sorted({row.timeframe for row in ordered})
    market_values = sorted({row.market for row in ordered})
    return {
        "raw_row_count": int(raw_quality.get("raw_row_count", len(rows))),
        "normalized_row_count": len(ordered),
        "unique_session_count": len(sessions),
        "first_raw_timestamp": canonical_timestamp(ordered[0].timestamp) if ordered else None,
        "last_raw_timestamp": canonical_timestamp(ordered[-1].timestamp) if ordered else None,
        "first_session_date": min(sessions).isoformat() if sessions else None,
        "last_session_date": max(sessions).isoformat() if sessions else None,
        "post_evaluation_row_count": sum(row.session_date > evaluation_end for row in ordered),
        "timeframe_values": timeframe_values,
        "market_values": market_values,
        "zero_volume_count": sum(row.volume == 0 for row in ordered),
        "nonfinite_count": int(raw_quality.get("nonfinite_count", 0)),
        "nonpositive_close_count": int(raw_quality.get("nonpositive_close_count", 0)),
        "invalid_ohlc_count": int(raw_quality.get("invalid_ohlc_count", 0)),
        "negative_volume_count": int(raw_quality.get("negative_volume_count", 0)),
        "duplicate_count": duplicate_count,
        "conflicting_duplicate_count": conflicting_count,
        "missing_expected_sessions": [item.isoformat() for item in missing],
        "extra_off_calendar_sessions": [item.isoformat() for item in extra],
        "maximum_consecutive_missing_session_gap": _max_consecutive_missing(missing, expected_sessions),
        "feed": (source_identity or {}).get("feed"),
        "adjustment": (source_identity or {}).get("adjustment"),
        "source_identity": dict(source_identity or {}),
    }


def common_session_report(
    rows_by_symbol: Mapping[str, Sequence[NormalizedBar]],
    *,
    expected_sessions: Sequence[date],
    evaluation_end: date = EVALUATION_END,
) -> dict[str, object]:
    session_sets = {
        symbol: {row.session_date for row in rows if row.session_date <= evaluation_end}
        for symbol, rows in rows_by_symbol.items()
    }
    for symbol in REFERENCE_SYMBOLS:
        session_sets.setdefault(symbol, set())
    common = set.intersection(*(session_sets[symbol] for symbol in REFERENCE_SYMBOLS))
    expected = set(expected_sessions)
    pairwise = {
        f"{left}_{right}": len(session_sets[left] & session_sets[right])
        for index, left in enumerate(REFERENCE_SYMBOLS)
        for right in REFERENCE_SYMBOLS[index + 1 :]
    }
    missing_by_reference = {
        symbol: [item.isoformat() for item in sorted(expected - session_sets[symbol])] for symbol in REFERENCE_SYMBOLS
    }
    extra_by_reference = {
        symbol: [item.isoformat() for item in sorted(session_sets[symbol] - expected)] for symbol in REFERENCE_SYMBOLS
    }
    return {
        "common_session_count": len(common),
        "first_common_session": min(common).isoformat() if common else None,
        "last_common_session": max(common).isoformat() if common else None,
        "expected_common_sessions": len(expected),
        "missing_by_reference": missing_by_reference,
        "extra_by_reference": extra_by_reference,
        "pairwise_overlap_counts": pairwise,
        "triple_overlap_count": len(common),
        "common_sessions": [item.isoformat() for item in sorted(common)],
    }


def resolve_first_session(requested: date, expected_sessions: Sequence[date]) -> date | None:
    return next((session for session in expected_sessions if session >= requested), None)


def weekly_decision_sessions(start: date, end: date, expected_sessions: Sequence[date]) -> tuple[date, ...]:
    first_by_week: dict[tuple[int, int], date] = {}
    for session in expected_sessions:
        if start <= session <= end:
            key = session.isocalendar()[:2]
            first_by_week.setdefault((int(key[0]), int(key[1])), session)
    return tuple(first_by_week[key] for key in sorted(first_by_week))


def weekly_decision_coverage(
    rows_by_symbol: Mapping[str, Sequence[NormalizedBar]],
    *,
    expected_sessions: Sequence[date],
    start: date,
    end: date,
    required_common_closes: int = 274,
) -> dict[str, object]:
    common = set.intersection(
        *(
            {row.session_date for row in rows_by_symbol.get(symbol, ()) if row.session_date <= end}
            for symbol in REFERENCE_SYMBOLS
        )
    )
    decisions: list[dict[str, object]] = []
    for decision in weekly_decision_sessions(start, end, expected_sessions):
        visible = sorted(session for session in common if session <= decision)
        selected = visible[-required_common_closes:] if len(visible) >= required_common_closes else []
        latest_by_reference = {
            symbol: max(
                (row.session_date for row in rows_by_symbol.get(symbol, ()) if row.session_date <= decision),
                default=None,
            )
            for symbol in REFERENCE_SYMBOLS
        }
        aligned = len(set(latest_by_reference.values())) == 1 and None not in latest_by_reference.values()
        accepted = len(selected) == required_common_closes and aligned
        if len(visible) < required_common_closes:
            reason = (
                "initial_warmup_insufficient" if len(visible) < required_common_closes else "common_coverage_shortfall"
            )
        elif not aligned:
            reason = "stale_or_unaligned_reference"
        else:
            reason = "accepted"
        decisions.append(
            {
                "decision_session": decision.isoformat(),
                "latest_close_session_visible": max(visible).isoformat() if visible else None,
                "common_closes_available": len(visible),
                "trailing_common_close_count": len(selected),
                "exact_trailing_window": len(selected) == required_common_closes,
                "stale_reference": not aligned,
                "aligned": aligned,
                "accepted": accepted,
                "reason": reason,
                "trailing_sessions": [item.isoformat() for item in selected],
            }
        )
    initial_warmup = sum(item["reason"] == "initial_warmup_insufficient" for item in decisions)
    failures = [item for item in decisions if not item["accepted"] and item["reason"] != "initial_warmup_insufficient"]
    accepted = [item for item in decisions if item["accepted"]]
    return {
        "requested_start": start.isoformat(),
        "resolved_start": resolve_first_session(start, expected_sessions).isoformat()
        if resolve_first_session(start, expected_sessions)
        else None,
        "decision_count": len(decisions),
        "accepted_decision_count": len(accepted),
        "initial_warmup_decision_count": initial_warmup,
        "failed_after_warmup_count": len(failures),
        "decisions": decisions,
    }


def _float_array(values: Sequence[object]) -> np.ndarray:
    converted: list[float] = []
    for value in values:
        try:
            converted.append(float(value))
        except (TypeError, ValueError, OverflowError):
            # A malformed cell is represented as a non-finite input so the
            # matrix is recorded as a failed audit instead of being skipped.
            converted.append(float("nan"))
    return np.asarray(converted, dtype=np.float64)


def design_matrix_digest(
    candidate_symbol: str,
    decision_session: date | str,
    session_dates: Sequence[date | str],
    stock_returns: Sequence[float],
    spy_returns: Sequence[float],
    size_returns: Sequence[float],
    growth_returns: Sequence[float],
) -> str:
    """Digest the exact ordered close-derived vectors using big-endian doubles."""
    digest = hashlib.sha256()

    def update_bytes(value: bytes) -> None:
        digest.update(struct.pack(">Q", len(value)))
        digest.update(value)

    update_bytes(MATRIX_ENCODING_VERSION.encode("ascii"))
    update_bytes(str(candidate_symbol).encode("utf-8"))
    update_bytes(str(decision_session).encode("utf-8"))
    update_bytes("\n".join(str(item) for item in session_dates).encode("utf-8"))
    for vector in (stock_returns, spy_returns, size_returns, growth_returns):
        digest.update(struct.pack(">Q", len(vector)))
        for value in vector:
            digest.update(struct.pack(">d", float(value)))
    digest.update(struct.pack(">II", len(stock_returns), 3))
    return digest.hexdigest()


def audit_design_matrix(
    candidate_symbol: str,
    decision_session: date | str,
    session_dates: Sequence[date | str],
    closes: Mapping[str, Sequence[object]],
    *,
    thresholds: Mapping[str, int | float] = DEFAULT_NUMERICAL_THRESHOLDS,
) -> dict[str, object]:
    required_closes = int(thresholds["required_common_closes"])
    failure_categories: list[str] = []
    if len(session_dates) != required_closes:
        failure_categories.append("common_close_count")
    arrays = {symbol: _float_array(closes.get(symbol, ())) for symbol in (candidate_symbol, "SPY", "IWM", "QQQ")}
    if any(len(values) != required_closes for values in arrays.values()):
        failure_categories.append("input_close_count")
    input_finite = all(bool(np.all(np.isfinite(values))) for values in arrays.values())
    input_positive = all(bool(np.all(values > 0)) for values in arrays.values())
    if not input_finite:
        failure_categories.append("nonfinite_input")
    if not input_positive:
        failure_categories.append("nonpositive_input_close")
    if any(len(values) < 2 for values in arrays.values()):
        stock_returns = spy_returns = size_returns = growth_returns = np.asarray([], dtype=np.float64)
    else:
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            stock_returns = arrays[candidate_symbol][1:] / arrays[candidate_symbol][:-1] - 1.0
            spy_returns = arrays["SPY"][1:] / arrays["SPY"][:-1] - 1.0
            iwm_returns = arrays["IWM"][1:] / arrays["IWM"][:-1] - 1.0
            qqq_returns = arrays["QQQ"][1:] / arrays["QQQ"][:-1] - 1.0
            size_returns = iwm_returns - spy_returns
            growth_returns = qqq_returns - spy_returns
    required_returns = int(thresholds["required_returns"])
    if len(stock_returns) != required_returns:
        failure_categories.append("return_count")
    return_lengths = {len(stock_returns), len(spy_returns), len(size_returns), len(growth_returns)}
    if len(return_lengths) != 1 or next(iter(return_lengths), 0) != required_returns:
        failure_categories.append("return_count")
    all_returns = np.concatenate((stock_returns, spy_returns, size_returns, growth_returns))
    if not bool(np.all(np.isfinite(all_returns))):
        failure_categories.append("nonfinite_input_returns")

    shape = [int(len(stock_returns)), 3]
    rank = 0
    singular_values = np.asarray([], dtype=np.float64)
    coefficients = np.asarray([], dtype=np.float64)
    condition_number = float("inf")
    singular_ratio = 0.0
    residuals = np.asarray([], dtype=np.float64)
    implicit_intercept = float("nan")
    if return_lengths == {required_returns} and bool(np.all(np.isfinite(all_returns))):
        factors = np.column_stack((spy_returns, size_returns, growth_returns))
        demeaned_factors = factors - np.mean(factors, axis=0)
        demeaned_stock = stock_returns - np.mean(stock_returns)
        try:
            coefficients, _residual_sum, rank_value, singular_values = np.linalg.lstsq(
                demeaned_factors, demeaned_stock, rcond=None
            )
            rank = int(rank_value)
            singular_values = np.asarray(singular_values, dtype=np.float64)
            if len(singular_values) == 3 and np.all(np.isfinite(singular_values)) and singular_values[0] > 0:
                singular_ratio = float(singular_values[-1] / singular_values[0])
                condition_number = (
                    float(singular_values[0] / singular_values[-1]) if singular_values[-1] > 0 else float("inf")
                )
            residuals = stock_returns - factors @ coefficients
            implicit_intercept = float(np.mean(stock_returns) - np.mean(factors, axis=0) @ coefficients)
        except np.linalg.LinAlgError:
            failure_categories.append("linalg_error")
    if rank != int(thresholds["required_rank"]):
        failure_categories.append("rank_deficient")
    if len(singular_values) != 3 or not bool(np.all(np.isfinite(singular_values))):
        failure_categories.append("nonfinite_singular_values")
    if singular_ratio < float(thresholds["minimum_singular_value_ratio"]):
        failure_categories.append("ill_conditioned")
    if condition_number > float(thresholds["maximum_condition_number"]):
        if "ill_conditioned" not in failure_categories:
            failure_categories.append("ill_conditioned")
    coefficient_finite = bool(len(coefficients) == 3 and np.all(np.isfinite(coefficients)))
    if not coefficient_finite:
        failure_categories.append("nonfinite_coefficients")
    residual_finite = bool(len(residuals) == required_returns and np.all(np.isfinite(residuals)))
    if not residual_finite:
        failure_categories.append("nonfinite_residuals")
    residual_std = float(np.std(residuals)) if residual_finite else float("nan")
    if not math.isfinite(residual_std) or residual_std <= float(thresholds["minimum_residual_std"]):
        failure_categories.append("degenerate_residual")
    matrix_digest = design_matrix_digest(
        candidate_symbol,
        decision_session,
        session_dates,
        stock_returns,
        spy_returns,
        size_returns,
        growth_returns,
    )
    return {
        "candidate_symbol": candidate_symbol,
        "decision_session": str(decision_session),
        "session_count": len(session_dates),
        "return_count": len(stock_returns),
        "matrix_shape": shape,
        "input_returns_finite": bool(np.all(np.isfinite(all_returns))) if len(all_returns) else False,
        "matrix_rank": rank,
        "singular_values": [float(value) for value in singular_values],
        "condition_number": condition_number,
        "singular_value_ratio": singular_ratio,
        "coefficients": [float(value) for value in coefficients],
        "coefficients_finite": coefficient_finite,
        "coefficient_norm": float(np.linalg.norm(coefficients)) if coefficient_finite else float("nan"),
        "implicit_intercept": implicit_intercept,
        "residual_count": len(residuals),
        "residuals_finite": residual_finite,
        "residual_mean": float(np.mean(residuals)) if residual_finite else float("nan"),
        "residual_std": residual_std,
        "residual_min": float(np.min(residuals)) if residual_finite else float("nan"),
        "residual_max": float(np.max(residuals)) if residual_finite else float("nan"),
        "matrix_digest_sha256": matrix_digest,
        "passed": not failure_categories,
        "failure_categories": sorted(set(failure_categories)),
        "residual_convention": "alpha_retaining: demeaned fit, non-demeaned application, no intercept",
        "skip_window_convention": "latest 21 residual returns omitted from momentum; beta fit retains all 273 returns",
    }


def merkle_root(digests: Sequence[str]) -> str:
    """Build a deterministic binary Merkle root over sorted hexadecimal leaves."""
    if not digests:
        return _sha256_bytes(b"m3-merkle-empty-v1")
    level = [bytes.fromhex(value) for value in sorted(digests)]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            hashlib.sha256(b"m3-merkle-node-v1" + left + right).digest()
            for left, right in zip(level[::2], level[1::2], strict=True)
        ]
    return level[0].hex()


@dataclass(frozen=True)
class ResearchReferenceOverlay:
    reference_observation_symbols: frozenset[str]
    configured_trade_symbols: frozenset[str]
    tradable_candidate_symbols: frozenset[str]
    target_symbols: frozenset[str]
    executable_symbols: frozenset[str]
    maximum_observation_capacity: int
    reference_pin_count: int
    managed_position_reservations: int
    open_order_reservations: int

    def __post_init__(self) -> None:
        intersections = {
            "candidate_reference_overlap": self.tradable_candidate_symbols & self.reference_observation_symbols,
            "target_reference_overlap": self.target_symbols & self.reference_observation_symbols,
            "execution_reference_overlap": self.executable_symbols & self.reference_observation_symbols,
        }
        if any(intersections.values()):
            raise GateError("reference_role_overlap", f"reference symbols leaked into a tradable role: {intersections}")
        if self.reference_pin_count != len(self.reference_observation_symbols):
            raise GateError("reference_pin_count_mismatch", "reference pin count does not match reference symbols")
        if self.maximum_observation_capacity < 0:
            raise GateError("observation_capacity_exceeded", "observation capacity cannot be negative")
        if self.maximum_observation_capacity < self.reference_pin_count:
            raise GateError("observation_capacity_exceeded", "reference pins exceed observation capacity")

    @property
    def effective_tradable_observation_capacity(self) -> int:
        return (
            self.maximum_observation_capacity
            - self.reference_pin_count
            - self.managed_position_reservations
            - self.open_order_reservations
        )

    def filter_candidates(self, symbols: Iterable[str]) -> tuple[str, ...]:
        normalized = {str(symbol).strip().upper() for symbol in symbols}
        return tuple(sorted(normalized & set(self.tradable_candidate_symbols)))

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_observation_symbols": sorted(self.reference_observation_symbols),
            "configured_trade_symbols": sorted(self.configured_trade_symbols),
            "tradable_candidate_symbols": sorted(self.tradable_candidate_symbols),
            "target_symbols": sorted(self.target_symbols),
            "executable_symbols": sorted(self.executable_symbols),
            "maximum_observation_capacity": self.maximum_observation_capacity,
            "reference_pin_count": self.reference_pin_count,
            "effective_tradable_observation_capacity": self.effective_tradable_observation_capacity,
            "managed_position_reservations": self.managed_position_reservations,
            "open_order_reservations": self.open_order_reservations,
            "runtime_state_used": False,
        }


def build_reference_overlay(
    config_data: Mapping[str, object],
    *,
    references: Sequence[str] = REFERENCE_SYMBOLS,
) -> ResearchReferenceOverlay:
    universe = config_data.get("universe")
    equity = universe.get("equity") if isinstance(universe, Mapping) else None
    if not isinstance(equity, Mapping):
        raise GateError("config_overlay_missing", "config overlay cannot find universe.equity")
    configured = {
        str(symbol).strip().upper()
        for field in ("fallback_trade_symbols", "trade_symbols")
        for symbol in (equity.get(field) if isinstance(equity.get(field), (list, tuple)) else ())
    }
    reference_set = frozenset(str(symbol).strip().upper() for symbol in references)
    tradable = frozenset(configured - set(reference_set))
    capacity = int(equity.get("maximum_stream_symbols", 0))
    return ResearchReferenceOverlay(
        reference_observation_symbols=reference_set,
        configured_trade_symbols=tradable,
        tradable_candidate_symbols=tradable,
        target_symbols=tradable,
        executable_symbols=tradable,
        maximum_observation_capacity=capacity,
        reference_pin_count=len(reference_set),
        managed_position_reservations=0,
        open_order_reservations=0,
    )


def _page_path(bundle: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise GateError("invalid_page_path", "page paths must be relative to the IWM bundle")
    resolved_bundle = bundle.resolve()
    resolved_page = (bundle / candidate).resolve()
    if resolved_page != resolved_bundle and resolved_bundle not in resolved_page.parents:
        raise GateError("invalid_page_path", "page path escapes the IWM bundle")
    return bundle / candidate


def _require_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GateError("source_contract_missing", f"source receipt requires {key}")
    return value.strip()


def _nonnegative_int(mapping: Mapping[str, object], key: str, *, category: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GateError(category, f"source receipt field {key} must be a non-negative integer")
    return value


def _validate_source_receipt(receipt: Mapping[str, object]) -> list[dict[str, object]]:
    if str(receipt.get("schema_version", "")) != "1.0.0":
        raise GateError("source_contract_mismatch", "unsupported IWM source receipt schema")
    _require_text(receipt, "provider")
    _require_text(receipt, "provider_api_product_version")
    if str(receipt.get("asset_class", "")) != "us_equity":
        raise GateError("asset_class_mismatch", "IWM source asset class must be us_equity")
    if str(receipt.get("symbol", "")).strip().upper() != "IWM":
        raise GateError("source_symbol_mismatch", "IWM source receipt symbol must be IWM")
    _require_text(receipt, "endpoint_identity")
    acquisition = parse_timestamp(receipt.get("acquisition_timestamp_utc"))
    if acquisition.utcoffset() is None or acquisition.utcoffset().total_seconds() != 0:
        raise GateError("source_contract_mismatch", "acquisition_timestamp_utc must be UTC")
    request = receipt.get("request")
    if not isinstance(request, Mapping):
        raise GateError("source_contract_missing", "source receipt request contract is missing")
    expected_request = {
        "start": "2016-01-01",
        "end": "2026-07-31",
        "timeframe": RAW_SOURCE_TIMEFRAME,
        "feed": "sip",
        "adjustment": "split",
        "sort": "asc",
    }
    for key, expected in expected_request.items():
        if str(request.get(key, "")) != expected:
            raise GateError("source_contract_mismatch", f"source request {key} must be {expected}")
    if not str(receipt.get("entitlement_feed_assertion", "")).strip():
        raise GateError("missing_entitlement_assertion", "entitlement/feed assertion is required")
    if receipt.get("no_fallback_assertion") is not True:
        raise GateError("feed_fallback", "source receipt must assert no feed fallback")
    retention = receipt.get("license_retention") or receipt.get("operator_attestation")
    if not retention:
        raise GateError(
            "missing_license_retention_attestation", "license/retention metadata or attestation is required"
        )
    if str(receipt.get("normalizer_version", "")) != NORMALIZER_VERSION:
        raise GateError("normalizer_version_mismatch", "source receipt normalizer version is not frozen")
    pages = receipt.get("pages")
    if not isinstance(pages, list) or not pages:
        raise GateError("source_contract_missing", "source receipt must list pages")
    if _nonnegative_int(receipt, "page_count", category="page_count_mismatch") != len(pages):
        raise GateError("page_count_mismatch", "source receipt page_count does not match pages")
    page_paths: list[str] = []
    validated: list[dict[str, object]] = []
    for page in pages:
        if not isinstance(page, Mapping):
            raise GateError("source_contract_mismatch", "each page entry must be an object")
        relative = _require_text(page, "relative_path").replace("\\", "/")
        if relative in page_paths:
            raise GateError("duplicate_page", f"duplicate page entry: {relative}")
        page_paths.append(relative)
        raw_sha = str(page.get("raw_sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", raw_sha):
            raise GateError("source_contract_mismatch", f"page {relative} has no valid raw hash")
        for field in ("raw_byte_size", "row_count"):
            _nonnegative_int(page, field, category="source_contract_mismatch")
        if "input_page_token" not in page or "output_next_page_token" not in page:
            raise GateError("source_contract_missing", f"page {relative} token chain fields are required")
        validated.append(dict(page))
    if page_paths != sorted(page_paths):
        raise GateError("page_order", "source receipt pages must be in deterministic lexical order")
    hashes = [str(page["raw_sha256"]) for page in validated]
    if len(set(hashes)) != len(hashes):
        raise GateError("duplicate_page", "source receipt contains duplicate raw page hashes")
    first_token = validated[0]["input_page_token"]
    if first_token not in (None, ""):
        raise GateError("pagination_chain", "first page must have an empty input token")
    for previous, current in zip(validated[:-1], validated[1:], strict=True):
        if current["input_page_token"] != previous["output_next_page_token"]:
            raise GateError("pagination_chain", "page token chain is not contiguous")
    if validated[-1]["output_next_page_token"] not in (None, ""):
        raise GateError("partial_pagination_chain", "terminal page must not contain a continuation token")
    return validated


def validate_iwm_bundle(
    bundle: str | Path, staging_dir: str | Path
) -> tuple[tuple[NormalizedBar, ...], dict[str, object]]:
    """Validate a complete raw provider page bundle and normalize its rows."""
    bundle_path = Path(bundle)
    if bundle_path.is_file():
        raise GateError("normalized_only_iwm", "an IWM CSV is partial evidence; a raw page bundle is required")
    if not bundle_path.is_dir():
        raise GateError("missing_iwm_bundle", "IWM raw source bundle is missing")
    receipt_path = bundle_path / "source_receipt.json"
    if not receipt_path.is_file():
        raise GateError("missing_iwm_source_receipt", "IWM bundle source_receipt.json is missing")
    receipt_artifact = stage_file(receipt_path, staging_dir)
    try:
        receipt = json.loads(receipt_artifact.staged_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError("invalid_source_receipt", "IWM source receipt is not valid UTF-8 JSON") from exc
    if not isinstance(receipt, Mapping):
        raise GateError("invalid_source_receipt", "IWM source receipt must be an object")
    pages = _validate_source_receipt(receipt)
    staged_pages: list[dict[str, object]] = []
    normalized: list[NormalizedBar] = []
    for page in pages:
        relative = str(page["relative_path"])
        path = _page_path(bundle_path, relative)
        if not path.is_file():
            raise GateError("missing_page", f"listed IWM page is missing: {relative}")
        artifact = stage_file(path, staging_dir)
        if artifact.raw_sha256 != page["raw_sha256"] or artifact.byte_size != page["raw_byte_size"]:
            raise GateError("page_hash_mismatch", f"IWM page hash/size mismatch: {relative}")
        try:
            payload = json.loads(
                artifact.staged_path.read_text(encoding="utf-8"), parse_float=Decimal, parse_int=Decimal
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GateError("invalid_page_json", f"IWM page is not valid UTF-8 JSON: {relative}") from exc
        if not isinstance(payload, Mapping):
            raise GateError("provider_payload_error", f"IWM page payload is not an object: {relative}")
        if (
            payload.get("error")
            or payload.get("errors")
            or str(payload.get("status", "")).lower() in {"error", "failed"}
        ):
            raise GateError("provider_payload_error", f"provider error embedded in IWM page: {relative}")
        bars = payload.get("bars")
        if not isinstance(bars, Mapping) or set(str(key) for key in bars) != {"IWM"}:
            raise GateError("source_symbol_mismatch", f"IWM page contains a non-IWM or missing symbol: {relative}")
        entries = bars["IWM"]
        if not isinstance(entries, list):
            raise GateError("provider_payload_error", f"IWM page bars must be a list: {relative}")
        if len(entries) != int(page["row_count"]):
            raise GateError("row_count_mismatch", f"IWM page row count mismatch: {relative}")
        page_rows: list[NormalizedBar] = []
        for raw in entries:
            if not isinstance(raw, Mapping):
                raise GateError("provider_payload_error", f"IWM page bar is not an object: {relative}")
            page_rows.append(normalize_provider_bar(raw))
        ensure_unique_rows(page_rows, category="identical_duplicate_bar")
        if page_rows != sorted(page_rows, key=lambda row: row.sort_key):
            raise GateError("source_order", f"IWM page is not sorted ascending: {relative}")
        if normalized and page_rows and page_rows[0].sort_key <= normalized[-1].sort_key:
            raise GateError("source_order", f"IWM page order is not strictly ascending: {relative}")
        normalized.extend(page_rows)
        staged_pages.append(
            {
                "relative_path": relative,
                "raw_sha256": artifact.raw_sha256,
                "crlf_normalized_sha256": artifact.crlf_normalized_sha256,
                "byte_size": artifact.byte_size,
                "row_count": len(page_rows),
            }
        )
    ensure_unique_rows(normalized, category="identical_duplicate_bar")
    ordered = tuple(sorted(normalized, key=lambda row: row.sort_key))
    bundle_digest_payload = {
        "encoding": "m3-raw-bundle-v1",
        "source_receipt_sha256": receipt_artifact.raw_sha256,
        "source_receipt_crlf_normalized_sha256": receipt_artifact.crlf_normalized_sha256,
        "pages": staged_pages,
    }
    return ordered, {
        "source_receipt": {
            "raw_sha256": receipt_artifact.raw_sha256,
            "crlf_normalized_sha256": receipt_artifact.crlf_normalized_sha256,
            "byte_size": receipt_artifact.byte_size,
        },
        "pages": staged_pages,
        "raw_bundle_sha256": canonical_digest(bundle_digest_payload),
        "normalized_artifact_sha256": normalized_rows_digest(ordered),
        "normalized_semantic_row_sha256": semantic_rows_digest(ordered),
        "normalized_row_count": len(ordered),
        "source_identity": {
            "provider": str(receipt["provider"]),
            "provider_api_product_version": str(receipt["provider_api_product_version"]),
            "endpoint_identity": str(receipt["endpoint_identity"]),
            "feed": "sip",
            "adjustment": "split",
            "source_timeframe": RAW_SOURCE_TIMEFRAME,
        },
    }


def load_csv_reference_rows(
    path: str | Path,
    *,
    reference_symbols: Sequence[str] = REFERENCE_SYMBOLS,
    source_start: date = SOURCE_START,
    source_end: date = SOURCE_END,
) -> tuple[dict[str, list[NormalizedBar]], dict[str, dict[str, int]]]:
    """Read only selected reference rows from a staged canonical CSV."""
    wanted = {symbol.upper() for symbol in reference_symbols}
    rows: dict[str, list[NormalizedBar]] = {symbol: [] for symbol in wanted}
    quality: dict[str, dict[str, int]] = {
        symbol: {
            "raw_row_count": 0,
            "nonfinite_count": 0,
            "nonpositive_close_count": 0,
            "invalid_ohlc_count": 0,
            "negative_volume_count": 0,
            "duplicate_count": 0,
            "conflicting_duplicate_count": 0,
        }
        for symbol in wanted
    }
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not set(CANONICAL_COLUMNS[1:]).issubset(reader.fieldnames or ()):
            raise GateError("canonical_schema_mismatch", "canonical CSV is missing required columns")
        for raw in reader:
            symbol = str(raw.get("symbol") or "").strip().upper()
            if symbol not in wanted:
                continue
            quality[symbol]["raw_row_count"] += 1
            try:
                row = normalize_csv_row(raw, source_start=source_start, source_end=source_end)
            except GateError as exc:
                if exc.category == "nonfinite_value":
                    quality[symbol]["nonfinite_count"] += 1
                elif exc.category == "nonpositive_ohlc":
                    quality[symbol]["nonpositive_close_count"] += 1
                elif exc.category == "invalid_ohlc":
                    quality[symbol]["invalid_ohlc_count"] += 1
                elif exc.category == "negative_volume":
                    quality[symbol]["negative_volume_count"] += 1
                else:
                    quality[symbol].setdefault(exc.category, 0)
                    quality[symbol][exc.category] += 1
                continue
            rows[symbol].append(row)
    for symbol, values in rows.items():
        duplicate_count, conflicting_count = _count_duplicate_keys(values)
        quality[symbol]["duplicate_count"] = duplicate_count
        quality[symbol]["conflicting_duplicate_count"] = conflicting_count
    return rows, quality


def merge_normalized_rows(
    canonical_rows: Sequence[NormalizedBar],
    iwm_rows: Sequence[NormalizedBar],
) -> tuple[tuple[NormalizedBar, ...], dict[str, str]]:
    """Small-fixture merge primitive; never mutates either input sequence."""
    ensure_unique_rows(canonical_rows)
    ensure_unique_rows(iwm_rows, category="identical_duplicate_bar")
    existing_iwm = {row.key: row for row in canonical_rows if row.symbol == "IWM"}
    additions = [row for row in iwm_rows if row.key not in existing_iwm]
    for row in iwm_rows:
        prior = existing_iwm.get(row.key)
        if prior is not None and prior != row:
            raise GateError("existing_iwm_conflict", f"canonical IWM row conflicts at {row.session_date}")
    merged = tuple(sorted((*canonical_rows, *additions), key=lambda row: row.sort_key))
    ensure_unique_rows(merged)
    before = [row for row in canonical_rows if row.symbol != "IWM"]
    after = [row for row in merged if row.symbol != "IWM"]
    hashes = {
        "pre_merge_non_iwm_semantic_sha256": semantic_rows_digest(before),
        "post_merge_non_iwm_semantic_sha256": semantic_rows_digest(after),
        "merged_normalized_sha256": normalized_rows_digest(merged),
        "merged_semantic_sha256": semantic_rows_digest(merged),
    }
    if hashes["pre_merge_non_iwm_semantic_sha256"] != hashes["post_merge_non_iwm_semantic_sha256"]:
        raise GateError("non_iwm_merge_drift", "non-IWM semantic rows changed during merge")
    return merged, hashes


def config_semantic_digest(config_data: Mapping[str, object]) -> str:
    relevant = {key: config_data.get(key) for key in ("data", "universe", "strategy", "risk", "backtest", "execution")}
    return canonical_digest(relevant)


FORBIDDEN_RECEIPT_KEYS = {
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
}
ALLOWED_NON_ECONOMIC_KEYS = {
    "candidate_economics_run",
    "economic_metrics_present",
    "target_symbols",
    "executable_symbols",
    "tradable_candidate_symbols",
}


def forbidden_receipt_keys(value: object) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normalized = str(key).lower()
                if normalized in FORBIDDEN_RECEIPT_KEYS and normalized not in ALLOWED_NON_ECONOMIC_KEYS:
                    found.add(str(key))
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(found))


__all__ = [
    "CANONICAL_COLUMNS",
    "CANONICAL_TIMEFRAME",
    "DEFAULT_NUMERICAL_THRESHOLDS",
    "EVALUATION_END",
    "EXCHANGE_TIMEZONE",
    "GateError",
    "MARKET",
    "MATRIX_ENCODING_VERSION",
    "NORMALIZER_VERSION",
    "NormalizedBar",
    "RAW_SOURCE_TIMEFRAME",
    "RECENT_START",
    "REFERENCE_SYMBOLS",
    "REFERENCE_PANEL_ENCODING_VERSION",
    "ResearchReferenceOverlay",
    "SESSION_ENCODING_VERSION",
    "SOURCE_END",
    "SOURCE_START",
    "StagedArtifact",
    "audit_design_matrix",
    "build_reference_inventory",
    "build_reference_overlay",
    "calendar_dependency_identity",
    "canonical_digest",
    "canonical_json_bytes",
    "canonical_timestamp",
    "common_session_report",
    "config_semantic_digest",
    "design_matrix_digest",
    "expected_exchange_sessions",
    "forbidden_receipt_keys",
    "load_csv_reference_rows",
    "merge_normalized_rows",
    "merkle_root",
    "normalize_csv_row",
    "normalize_provider_bar",
    "normalized_rows_digest",
    "resolve_first_session",
    "reference_panel_digest",
    "semantic_rows_digest",
    "session_dates_digest",
    "session_date_for_timestamp",
    "stage_file",
    "validate_iwm_bundle",
    "weekly_decision_coverage",
    "weekly_decision_sessions",
]
