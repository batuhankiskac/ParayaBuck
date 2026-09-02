#!/usr/bin/env python3
"""Acquire the frozen historical IWM SIP page bundle for the M3 data gate.

This is an explicitly invoked research command.  Importing the module never
contacts a provider, reads credentials, or creates output.  The default
transport is intentionally small and injectable so the full pagination and
failure contract can be tested without network access.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

import requests

_SCRIPT_DIR = Path(__file__).resolve().parent

if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from m3_data_gate_helpers import (  # noqa: E402
    NORMALIZER_VERSION,
    R4_ACQUISITION_MISSION,
    R4_CAPTURE_ENCODING,
    REQUEST_END_BOUNDARY_UTC,
    REQUEST_END_BOUNDARY_UTC_TEXT,
    REQUEST_START_UTC,
    REQUEST_START_UTC_TEXT,
    REQUIRED_FIRST_REFERENCE_SESSION,
    REQUIRED_LAST_REFERENCE_SESSION,
    SOURCE_ADJUSTMENT,
    SOURCE_API_FAMILY_VERSION,
    SOURCE_END,
    SOURCE_ENDPOINT,
    SOURCE_FEED,
    SOURCE_PROVIDER,
    SOURCE_SORT,
    SOURCE_START,
    GateError,
    _validate_capture_journal,
    canonical_digest,
    canonical_timestamp,
    capture_page_hash_digest,
    normalize_provider_bar,
    validate_iwm_bundle,
)

HOST = "data.alpaca.markets"
BASE_URL = f"https://{HOST}{SOURCE_ENDPOINT}"
ALLOWED_SYMBOL = "IWM"
REQUEST_PARAMS: dict[str, str | int] = {
    "symbols": ALLOWED_SYMBOL,
    "timeframe": "1Day",
    "start": REQUEST_START_UTC_TEXT,
    "end": REQUEST_END_BOUNDARY_UTC_TEXT,
    "feed": SOURCE_FEED,
    "adjustment": SOURCE_ADJUSTMENT,
    "sort": SOURCE_SORT,
    "limit": 10000,
}
REQUEST_TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 3
RETRY_AFTER_MAX_SECONDS = 30.0
RETRY_BACKOFF_SECONDS = 1.0
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
OPERATOR_ATTESTATION = (
    "Operator-directed local private-research retention only; raw response pages remain local and untracked "
    "and are not redistributed by this workflow."
)


class AcquisitionError(RuntimeError):
    """A redacted, machine-classified acquisition failure."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    content: bytes
    headers: Mapping[str, str]


class Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class RequestsTransport:
    """The only production network transport used by this command."""

    def __init__(self) -> None:
        self._session = requests.Session()

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse:
        if url != BASE_URL:
            raise AcquisitionError("endpoint_identity_mismatch")
        response = self._session.get(
            url,
            params=dict(params),
            headers=dict(headers),
            timeout=timeout,
            allow_redirects=False,
        )
        return HttpResponse(
            status_code=int(response.status_code),
            content=bytes(response.content),
            headers={str(key): str(value) for key, value in response.headers.items()},
        )


def _parse_env_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AcquisitionError("env_file_read_failure") from exc
    values: dict[str, str] = {}
    allowed = {"ALPACA_API_KEY", "ALPACA_SECRET_KEY"}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in allowed:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_credentials(env_file: str | Path | None = None) -> tuple[str, str]:
    values = _parse_env_file(Path(env_file)) if env_file is not None else {}
    api_key = os.environ.get("ALPACA_API_KEY", values.get("ALPACA_API_KEY", ""))
    secret_key = os.environ.get("ALPACA_SECRET_KEY", values.get("ALPACA_SECRET_KEY", ""))
    if not api_key or not secret_key:
        raise AcquisitionError("missing_credentials")
    return api_key, secret_key


def _retry_after_seconds(headers: Mapping[str, str], *, now: Callable[[], float] = time.time) -> float | None:
    value = next((str(raw) for key, raw in headers.items() if key.lower() == "retry-after"), "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                return None
            seconds = retry_at.timestamp() - now()
        except (TypeError, ValueError, OverflowError):
            return None
    if seconds < 0:
        return 0.0
    return min(seconds, RETRY_AFTER_MAX_SECONDS)


def _sleep_for_retry(
    response: HttpResponse | None,
    retry_number: int,
    *,
    sleeper: Callable[[float], None],
    now: Callable[[], float],
) -> None:
    retry_after = _retry_after_seconds(response.headers, now=now) if response is not None else None
    delay = (
        retry_after
        if retry_after is not None
        else min(RETRY_AFTER_MAX_SECONDS, RETRY_BACKOFF_SECONDS * 2**retry_number)
    )
    sleeper(delay)


def _request_page(
    transport: Transport,
    *,
    input_page_token: str | None,
    api_key: str,
    secret_key: str,
    sleeper: Callable[[float], None],
    now: Callable[[], float],
) -> tuple[HttpResponse, int]:
    params: MutableMapping[str, str | int] = dict(REQUEST_PARAMS)
    if input_page_token is not None:
        params["page_token"] = input_page_token
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    retry_count = 0
    while True:
        try:
            response = transport.get(
                BASE_URL,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except (requests.exceptions.Timeout, TimeoutError, ConnectionError, OSError) as exc:
            if retry_count >= MAX_RETRIES:
                raise AcquisitionError("transient_retry_exhaustion") from exc
            _sleep_for_retry(None, retry_count, sleeper=sleeper, now=now)
            retry_count += 1
            continue
        status = response.status_code
        if status == 200:
            return response, retry_count
        if status in {401, 403}:
            raise AcquisitionError("authentication_or_sip_entitlement_failure")
        if 400 <= status < 500 and status != 429:
            raise AcquisitionError("provider_4xx")
        if status not in TRANSIENT_STATUS_CODES:
            raise AcquisitionError("provider_http_failure")
        if retry_count >= MAX_RETRIES:
            raise AcquisitionError("transient_retry_exhaustion")
        _sleep_for_retry(response, retry_count, sleeper=sleeper, now=now)
        retry_count += 1


def _continuation_token(value: object) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise AcquisitionError("pagination_schema_mismatch")
    return value


def _decode_json_payload(body: bytes) -> Mapping[str, object]:
    try:
        # Decimal parsing keeps the exact provider numeric lexemes available
        # to the repository normalizer; the raw page itself is still written
        # byte-for-byte and is never reserialized.
        payload = json.loads(body.decode("utf-8"), parse_float=Decimal, parse_int=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcquisitionError("malformed_json") from exc
    if not isinstance(payload, Mapping):
        raise AcquisitionError("response_schema_mismatch")
    return payload


def _parse_page_envelope(body: bytes, *, input_page_token: str | None) -> tuple[Mapping[str, object], str | None]:
    """Validate only the minimum envelope required before pagination continues."""
    payload = _decode_json_payload(body)
    if payload.get("error") or payload.get("errors") or str(payload.get("status", "")).lower() in {"error", "failed"}:
        raise AcquisitionError("provider_error_payload")
    if "next_page_token" not in payload:
        raise AcquisitionError("missing_next_page_token")
    output_token = _continuation_token(payload.get("next_page_token"))
    bars = payload.get("bars")
    if not isinstance(bars, Mapping) or set(str(key) for key in bars) != {ALLOWED_SYMBOL}:
        raise AcquisitionError("response_schema_mismatch")
    entries = bars.get(ALLOWED_SYMBOL)
    if not isinstance(entries, list) or any(not isinstance(entry, Mapping) for entry in entries):
        raise AcquisitionError("response_schema_mismatch")
    if input_page_token is not None and not input_page_token:
        raise AcquisitionError("pagination_schema_mismatch")
    return payload, output_token


def _parse_page(
    body: bytes, *, input_page_token: str | None, r4: bool = False
) -> tuple[Mapping[str, object], tuple[Any, ...], str | None]:
    payload, output_token = _parse_page_envelope(body, input_page_token=input_page_token)
    bars = payload["bars"]
    assert isinstance(bars, Mapping)
    entries = bars[ALLOWED_SYMBOL]
    assert isinstance(entries, list)
    normalized: list[Any] = []
    for entry in entries:
        # Alpaca v2 identifies a bar by the surrounding ``bars`` map key; the
        # provider bar object itself does not carry an in-row symbol field.
        # Preserve the exact response bytes and add only that envelope identity
        # to the verifier input.
        normalizer_input = entry if "S" in entry or "symbol" in entry else {**entry, "S": ALLOWED_SYMBOL}
        try:
            normalized.append(
                normalize_provider_bar(
                    normalizer_input,
                    source_start=REQUIRED_FIRST_REFERENCE_SESSION if r4 else SOURCE_START,
                    source_end=REQUIRED_LAST_REFERENCE_SESSION if r4 else SOURCE_END,
                    request_start_utc=REQUEST_START_UTC if r4 else None,
                    request_end_boundary_utc=REQUEST_END_BOUNDARY_UTC if r4 else None,
                )
            )
        except GateError as exc:
            category = (
                exc.category
                if exc.category in {"request_boundary_violation", "source_range_violation"}
                else "response_schema_mismatch"
            )
            raise AcquisitionError(category) from exc
    if tuple(normalized) != tuple(sorted(normalized, key=lambda row: row.sort_key)):
        raise AcquisitionError("source_order")
    return payload, tuple(normalized), output_token


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise AcquisitionError("existing_bundle_conflict")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise AcquisitionError("disk_write_or_fsync_failure") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_json_atomically(path: Path, payload: Mapping[str, object]) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    _write_bytes_atomically(path, data)


def _replace_json_atomically(path: Path, payload: Mapping[str, object]) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise AcquisitionError("disk_write_or_fsync_failure") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_header(headers: Mapping[str, str], name: str) -> str | None:
    value = next((str(raw).strip() for key, raw in headers.items() if key.lower() == name.lower()), "")
    return value[:256] if value else None


def _request_interval() -> dict[str, str]:
    return {
        "start": REQUEST_START_UTC_TEXT,
        "end_boundary": REQUEST_END_BOUNDARY_UTC_TEXT,
        "semantics": "[start,end)",
    }


def _journal_payload(
    *,
    attempt_id: str,
    pages: list[dict[str, object]],
    capture_state: str,
    capture_complete: bool,
    failure_category: str | None = None,
    acquisition_timestamp_utc: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "mission": R4_ACQUISITION_MISSION,
        "attempt_id": attempt_id,
        "capture_encoding": R4_CAPTURE_ENCODING,
        "capture_state": capture_state,
        "capture_complete": capture_complete,
        "request_interval_utc": _request_interval(),
        "request_params": dict(REQUEST_PARAMS),
        "network_allowed": True,
        "allowed_host": HOST,
        "allowed_endpoint": SOURCE_ENDPOINT,
        "allowed_symbol": ALLOWED_SYMBOL,
        "allowed_feed": SOURCE_FEED,
        "page_count": len(pages),
        "aggregate_page_hash_digest": capture_page_hash_digest(pages),
        "pages": pages,
    }
    if failure_category is not None:
        payload["failure_category"] = failure_category
    if acquisition_timestamp_utc is not None:
        payload["acquisition_timestamp_utc"] = acquisition_timestamp_utc
    return payload


def _blocked_receipt(
    path: Path,
    category: str,
    *,
    attempt_id: str | None = None,
    pages: list[Mapping[str, object]] | None = None,
    capture_complete: bool = False,
) -> dict[str, object]:
    page_list = pages or []
    return {
        "schema_version": "1.0.0",
        "mission": R4_ACQUISITION_MISSION,
        "status": "M3_DATA_GATE_BLOCKED",
        "category": category,
        "host": HOST,
        "endpoint": SOURCE_ENDPOINT,
        "symbol": ALLOWED_SYMBOL,
        "feed": SOURCE_FEED,
        "output_bundle_name": path.name,
        "attempt_id": attempt_id,
        "captured_page_count": len(page_list),
        "aggregate_page_hash_digest": capture_page_hash_digest(page_list),
        "capture_complete": capture_complete,
        "credentials_disclosed": False,
        "raw_pages_promoted": False,
        "raw_tokens_in_tracked_receipt": False,
    }


def _source_receipt(
    *,
    acquisition_timestamp_utc: str,
    pages: list[dict[str, object]],
    attempt_id: str,
    capture_state: str,
) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "mission": R4_ACQUISITION_MISSION,
        "provider": SOURCE_PROVIDER,
        "api_family_version": SOURCE_API_FAMILY_VERSION,
        "provider_api_product_version": "market_data_v2_stocks_bars",
        "asset_class": "us_equity",
        "symbol": ALLOWED_SYMBOL,
        "endpoint_identity": SOURCE_ENDPOINT,
        "acquisition_timestamp_utc": acquisition_timestamp_utc,
        "attempt_id": attempt_id,
        "capture_encoding": R4_CAPTURE_ENCODING,
        "capture_state": capture_state,
        "capture_complete": capture_state == "PROMOTED_ACCEPTED_BUNDLE",
        "request_interval_utc": _request_interval(),
        "request": {
            "start": REQUEST_START_UTC_TEXT,
            "end": REQUEST_END_BOUNDARY_UTC_TEXT,
            "timeframe": "1Day",
            "feed": SOURCE_FEED,
            "adjustment": SOURCE_ADJUSTMENT,
            "sort": SOURCE_SORT,
            "limit": 10000,
        },
        "entitlement_feed_assertion": "HTTP 200 was received for the explicit feed=sip request; no fallback request was made.",
        "no_fallback_assertion": True,
        "operator_attestation": OPERATOR_ATTESTATION,
        "normalizer_version": NORMALIZER_VERSION,
        "body_capture_semantics": "exact response body bytes consumed from the HTTP client; no JSON reserialization",
        "retry_policy": {
            "max_retries": MAX_RETRIES,
            "transient_status_codes": sorted(TRANSIENT_STATUS_CODES),
            "retry_after_max_seconds": RETRY_AFTER_MAX_SECONDS,
            "backoff_seconds": RETRY_BACKOFF_SECONDS,
        },
        "page_count": len(pages),
        "pages": pages,
    }


def _new_attempt_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:12]}"


def _update_journal(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        _replace_json_atomically(path, payload)
    else:
        _write_json_atomically(path, payload)


def _journal_failure_details(path: Path, category: str) -> tuple[str | None, list[Mapping[str, object]], bool]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, [], False
    if not isinstance(payload, Mapping):
        return None, [], False
    updated = dict(payload)
    updated["failure_category"] = category
    try:
        _replace_json_atomically(path, updated)
    except (AcquisitionError, OSError):
        pass
    pages = payload.get("pages")
    safe_pages = [page for page in pages if isinstance(page, Mapping)] if isinstance(pages, list) else []
    return (
        str(payload.get("attempt_id")) if payload.get("attempt_id") is not None else None,
        safe_pages,
        payload.get("capture_complete") is True,
    )


def _acquisition_result(
    final_path: Path,
    *,
    attempt_id: str,
    normalized_rows: tuple[Any, ...],
    audit: Mapping[str, object],
    bundle_name: str | None = None,
) -> dict[str, object]:
    source_receipt_bytes = (final_path / "source_receipt.json").read_bytes()
    pages = audit.get("pages", [])
    page_chain_digest = canonical_digest(pages) if isinstance(pages, list) else ""
    return {
        "schema_version": "1.0.0",
        "mission": R4_ACQUISITION_MISSION,
        "status": "ACQUIRED",
        "capture_state": "PROMOTED_ACCEPTED_BUNDLE",
        "attempt_id": attempt_id,
        "bundle_name": bundle_name or final_path.name,
        "host": HOST,
        "endpoint": SOURCE_ENDPOINT,
        "symbol": ALLOWED_SYMBOL,
        "feed": SOURCE_FEED,
        "request_interval_utc": _request_interval(),
        "page_count": len(audit.get("pages", ())),
        "row_count": len(normalized_rows),
        "first_timestamp": canonical_timestamp(normalized_rows[0].timestamp),
        "last_timestamp": canonical_timestamp(normalized_rows[-1].timestamp),
        "first_session": normalized_rows[0].session_date.isoformat(),
        "last_session": normalized_rows[-1].session_date.isoformat(),
        "raw_bundle_sha256": audit["raw_bundle_sha256"],
        "source_receipt_sha256": sha256(source_receipt_bytes).hexdigest(),
        "page_chain_digest_sha256": page_chain_digest,
        "aggregate_page_hash_digest": audit.get("capture_journal", {}).get("aggregate_page_hash_digest")
        if isinstance(audit.get("capture_journal"), Mapping)
        else None,
        "credentials_disclosed": False,
        "raw_tokens_in_result": False,
    }


def acquire_bundle(
    output: str | Path,
    *,
    env_file: str | Path | None = None,
    transport: Transport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    timestamp_utc: Callable[[], datetime] = lambda: datetime.now(UTC),
    blocked_receipt_out: str | Path | None = None,
    result_receipt_out: str | Path | None = None,
    attempt_id: str | None = None,
    quarantine_root: str | Path | None = None,
) -> dict[str, object]:
    """Capture, validate, and atomically publish one R4 IWM raw bundle."""
    final_path = Path(output)
    blocked_path = (
        Path(blocked_receipt_out)
        if blocked_receipt_out is not None
        else final_path.with_name(final_path.name + ".blocked.json")
    )
    quarantine_path: Path | None = None
    journal_path: Path | None = None
    active_attempt_id: str | None = attempt_id
    try:
        if final_path.exists() or final_path.is_symlink():
            raise AcquisitionError("existing_bundle_conflict")
        api_key, secret_key = load_credentials(env_file)
        active_attempt_id = attempt_id or _new_attempt_id()
        if (
            not active_attempt_id.isascii()
            or not active_attempt_id
            or not all(character.isalnum() or character in "_.-" for character in active_attempt_id)
        ):
            raise AcquisitionError("invalid_attempt_id")
        attempts_root = Path(quarantine_root) if quarantine_root is not None else final_path.parent / "m3_iwm_attempts"
        attempts_root.mkdir(parents=True, exist_ok=True)
        quarantine_path = attempts_root / f"r4_{active_attempt_id}"
        if quarantine_path.exists() or quarantine_path.is_symlink():
            raise AcquisitionError("existing_attempt_conflict")
        pages_dir = quarantine_path / "pages"
        pages_dir.mkdir(parents=True, exist_ok=False)
        journal_path = quarantine_path / "attempt_journal.json"
        page_records: list[dict[str, object]] = []
        _update_journal(
            journal_path,
            _journal_payload(
                attempt_id=active_attempt_id,
                pages=page_records,
                capture_state="CAPTURED_UNVERIFIED",
                capture_complete=False,
            ),
        )
        active_transport = transport or RequestsTransport()
        input_token: str | None = None
        page_index = 0
        while True:
            response, retry_count = _request_page(
                active_transport,
                input_page_token=input_token,
                api_key=api_key,
                secret_key=secret_key,
                sleeper=sleeper,
                now=now,
            )
            body = response.content
            page_index += 1
            relative = f"pages/{page_index:06d}.json"
            page_path = quarantine_path / relative
            page_record: dict[str, object] = {
                "relative_path": relative,
                "raw_sha256": sha256(body).hexdigest(),
                "raw_byte_size": len(body),
                "row_count": None,
                "input_page_token": input_token,
                "output_next_page_token": None,
                "http_status": int(response.status_code),
                "content_type": _safe_header(response.headers, "content-type"),
                "response_date": _safe_header(response.headers, "date"),
                "retry_count": retry_count,
            }
            # Capture-first invariant: this durable page write and journal
            # update happen before any body parsing or normalization.
            _write_bytes_atomically(page_path, body)
            page_records.append(page_record)
            _update_journal(
                journal_path,
                _journal_payload(
                    attempt_id=active_attempt_id,
                    pages=page_records,
                    capture_state="CAPTURED_UNVERIFIED",
                    capture_complete=False,
                ),
            )
            _payload, output_token = _parse_page_envelope(body, input_page_token=input_token)
            page_record["output_next_page_token"] = output_token
            _update_journal(
                journal_path,
                _journal_payload(
                    attempt_id=active_attempt_id,
                    pages=page_records,
                    capture_state="CAPTURED_UNVERIFIED",
                    capture_complete=False,
                ),
            )
            if output_token is None:
                break
            input_token = output_token

        acquisition_timestamp = canonical_timestamp(timestamp_utc())
        _update_journal(
            journal_path,
            _journal_payload(
                attempt_id=active_attempt_id,
                pages=page_records,
                capture_state="CAPTURED_UNVERIFIED",
                capture_complete=True,
                acquisition_timestamp_utc=acquisition_timestamp,
            ),
        )
        prior_row_keys: set[tuple[object, ...]] = set()
        for page_record in page_records:
            page_path = quarantine_path / str(page_record["relative_path"])
            _payload, normalized, output_token = _parse_page(
                page_path.read_bytes(),
                input_page_token=page_record["input_page_token"]
                if isinstance(page_record["input_page_token"], str)
                else None,
                r4=True,
            )
            if output_token != page_record["output_next_page_token"]:
                raise AcquisitionError("pagination_payload_mismatch")
            row_keys = {row.key for row in normalized}
            if len(row_keys) != len(normalized) or prior_row_keys & row_keys:
                raise AcquisitionError("duplicate_page_or_session")
            prior_row_keys.update(row_keys)
            page_record["row_count"] = len(normalized)
        _update_journal(
            journal_path,
            _journal_payload(
                attempt_id=active_attempt_id,
                pages=page_records,
                capture_state="CAPTURED_UNVERIFIED",
                capture_complete=True,
                acquisition_timestamp_utc=acquisition_timestamp,
            ),
        )
        receipt = _source_receipt(
            acquisition_timestamp_utc=acquisition_timestamp,
            pages=page_records,
            attempt_id=active_attempt_id,
            capture_state="VALIDATED",
        )
        _write_json_atomically(quarantine_path / "source_receipt.json", receipt)
        try:
            with tempfile.TemporaryDirectory(prefix="m3-iwm-validation-") as validation_stage:
                normalized_rows, audit = validate_iwm_bundle(
                    quarantine_path,
                    Path(validation_stage),
                    r4=True,
                    require_promoted=False,
                )
        except (GateError, OSError) as exc:
            raise AcquisitionError("bundle_validation_failure") from exc
        if not normalized_rows:
            raise AcquisitionError("empty_iwm_bundle")
        _update_journal(
            journal_path,
            _journal_payload(
                attempt_id=active_attempt_id,
                pages=page_records,
                capture_state="PROMOTED_ACCEPTED_BUNDLE",
                capture_complete=True,
                acquisition_timestamp_utc=acquisition_timestamp,
            ),
        )
        promoted_receipt = _source_receipt(
            acquisition_timestamp_utc=str(receipt["acquisition_timestamp_utc"]),
            pages=page_records,
            attempt_id=active_attempt_id,
            capture_state="PROMOTED_ACCEPTED_BUNDLE",
        )
        _replace_json_atomically(quarantine_path / "source_receipt.json", promoted_receipt)
        try:
            with tempfile.TemporaryDirectory(prefix="m3-iwm-final-validation-") as validation_stage:
                normalized_rows, audit = validate_iwm_bundle(
                    quarantine_path,
                    Path(validation_stage),
                    r4=True,
                    require_promoted=True,
                )
        except (GateError, OSError) as exc:
            raise AcquisitionError("bundle_validation_failure") from exc
        result = _acquisition_result(
            quarantine_path,
            attempt_id=active_attempt_id,
            normalized_rows=normalized_rows,
            audit=audit,
            bundle_name=final_path.name,
        )
        if result_receipt_out is not None:
            result_path = Path(result_receipt_out)
            result_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(result_path, result)
        os.replace(quarantine_path, final_path)
        quarantine_path = None
        return result
    except AcquisitionError as exc:
        details = (None, [], False)
        if journal_path is not None and journal_path.is_file():
            details = _journal_failure_details(journal_path, exc.category)
        try:
            blocked_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(
                blocked_path,
                _blocked_receipt(
                    final_path,
                    exc.category,
                    attempt_id=details[0] or active_attempt_id,
                    pages=details[1],
                    capture_complete=details[2],
                ),
            )
        except (AcquisitionError, OSError):
            pass
        raise
    except (OSError, ValueError) as exc:
        details = (None, [], False)
        if journal_path is not None and journal_path.is_file():
            details = _journal_failure_details(journal_path, "disk_or_contract_failure")
        try:
            blocked_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(
                blocked_path,
                _blocked_receipt(
                    final_path,
                    "disk_or_contract_failure",
                    attempt_id=details[0] or active_attempt_id,
                    pages=details[1],
                    capture_complete=details[2],
                ),
            )
        except (AcquisitionError, OSError):
            pass
        raise AcquisitionError("disk_or_contract_failure") from exc


def revalidate_quarantine(
    quarantine: str | Path,
    promote_to: str | Path,
    *,
    blocked_receipt_out: str | Path | None = None,
    result_receipt_out: str | Path | None = None,
) -> dict[str, object]:
    """Replay one captured R4 attempt without credentials or network access."""
    quarantine_path = Path(quarantine)
    final_path = Path(promote_to)
    blocked_path = (
        Path(blocked_receipt_out)
        if blocked_receipt_out is not None
        else final_path.with_name(final_path.name + ".blocked.json")
    )
    journal_path = quarantine_path / "attempt_journal.json"
    attempt_id: str | None = None
    try:
        if final_path.exists() or final_path.is_symlink():
            raise AcquisitionError("existing_bundle_conflict")
        if not quarantine_path.is_dir() or quarantine_path.is_symlink():
            raise AcquisitionError("missing_quarantine")
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AcquisitionError("invalid_capture_journal") from exc
        if not isinstance(journal, Mapping):
            raise AcquisitionError("invalid_capture_journal")
        attempt_id_value = journal.get("attempt_id")
        if (
            not isinstance(attempt_id_value, str)
            or not attempt_id_value
            or not all(character.isalnum() or character in "_.-" for character in attempt_id_value)
        ):
            raise AcquisitionError("capture_journal_identity")
        attempt_id = attempt_id_value
        if journal.get("request_params") != dict(REQUEST_PARAMS):
            raise AcquisitionError("capture_journal_identity")
        if journal.get("allowed_host") != HOST or journal.get("allowed_endpoint") != SOURCE_ENDPOINT:
            raise AcquisitionError("capture_journal_identity")
        pages = journal.get("pages")
        if not isinstance(pages, list) or not pages or journal.get("capture_complete") is not True:
            raise AcquisitionError("capture_incomplete")
        with tempfile.TemporaryDirectory(prefix="m3-iwm-replay-") as staging:
            journal_audit = _validate_capture_journal(
                quarantine_path,
                Path(staging),
                require_promoted=False,
            )
        journal_pages = journal_audit.get("pages")
        if not isinstance(journal_pages, list):
            raise AcquisitionError("capture_journal_identity")
        normalized_rows_list: list[Any] = []
        prior_row_keys: set[tuple[object, ...]] = set()
        for page_record in journal_pages:
            if not isinstance(page_record, Mapping):
                raise AcquisitionError("capture_journal_identity")
            relative = str(page_record["relative_path"])
            page_path = quarantine_path / relative
            body = page_path.read_bytes()
            _payload, output_token = _parse_page_envelope(
                body,
                input_page_token=page_record.get("input_page_token")
                if isinstance(page_record.get("input_page_token"), str)
                else None,
            )
            if output_token != page_record.get("output_next_page_token"):
                raise AcquisitionError("pagination_payload_mismatch")
            _payload, normalized, semantic_token = _parse_page(
                body,
                input_page_token=page_record.get("input_page_token")
                if isinstance(page_record.get("input_page_token"), str)
                else None,
                r4=True,
            )
            if semantic_token != output_token:
                raise AcquisitionError("pagination_payload_mismatch")
            row_keys = {row.key for row in normalized}
            if len(row_keys) != len(normalized) or prior_row_keys & row_keys:
                raise AcquisitionError("duplicate_page_or_session")
            prior_row_keys.update(row_keys)
            page_record["row_count"] = len(normalized)
            normalized_rows_list.extend(normalized)
        if not normalized_rows_list:
            raise AcquisitionError("empty_iwm_bundle")
        acquisition_timestamp = journal.get("acquisition_timestamp_utc")
        if not isinstance(acquisition_timestamp, str):
            raise AcquisitionError("capture_journal_identity")
        try:
            canonical_timestamp(datetime.fromisoformat(acquisition_timestamp.replace("Z", "+00:00")))
        except (GateError, ValueError):
            raise AcquisitionError("capture_journal_identity") from None
        updated_journal = _journal_payload(
            attempt_id=attempt_id,
            pages=[dict(page) for page in journal_pages if isinstance(page, Mapping)],
            capture_state="VALIDATED",
            capture_complete=True,
            acquisition_timestamp_utc=acquisition_timestamp,
        )
        _replace_json_atomically(journal_path, updated_journal)
        receipt = _source_receipt(
            acquisition_timestamp_utc=acquisition_timestamp,
            pages=updated_journal["pages"],  # type: ignore[arg-type]
            attempt_id=attempt_id,
            capture_state="VALIDATED",
        )
        if (quarantine_path / "source_receipt.json").exists():
            _replace_json_atomically(quarantine_path / "source_receipt.json", receipt)
        else:
            _write_json_atomically(quarantine_path / "source_receipt.json", receipt)
        with tempfile.TemporaryDirectory(prefix="m3-iwm-replay-validation-") as staging:
            validate_iwm_bundle(
                quarantine_path,
                Path(staging),
                r4=True,
                require_promoted=False,
            )
        promoted_journal = _journal_payload(
            attempt_id=attempt_id,
            pages=[dict(page) for page in journal_pages if isinstance(page, Mapping)],
            capture_state="PROMOTED_ACCEPTED_BUNDLE",
            capture_complete=True,
            acquisition_timestamp_utc=acquisition_timestamp,
        )
        _replace_json_atomically(journal_path, promoted_journal)
        promoted_receipt = _source_receipt(
            acquisition_timestamp_utc=acquisition_timestamp,
            pages=promoted_journal["pages"],  # type: ignore[arg-type]
            attempt_id=attempt_id,
            capture_state="PROMOTED_ACCEPTED_BUNDLE",
        )
        _replace_json_atomically(quarantine_path / "source_receipt.json", promoted_receipt)
        with tempfile.TemporaryDirectory(prefix="m3-iwm-replay-final-validation-") as staging:
            normalized_rows, audit = validate_iwm_bundle(
                quarantine_path,
                Path(staging),
                r4=True,
                require_promoted=True,
            )
        result = _acquisition_result(
            quarantine_path,
            attempt_id=attempt_id,
            normalized_rows=normalized_rows,
            audit=audit,
            bundle_name=final_path.name,
        )
        if result_receipt_out is not None:
            result_path = Path(result_receipt_out)
            result_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(result_path, result)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(quarantine_path, final_path)
        return result
    except AcquisitionError as exc:
        details = (None, [], False)
        if journal_path.is_file():
            details = _journal_failure_details(journal_path, exc.category)
        try:
            blocked_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(
                blocked_path,
                _blocked_receipt(
                    final_path,
                    exc.category,
                    attempt_id=details[0] or attempt_id,
                    pages=details[1],
                    capture_complete=details[2],
                ),
            )
        except (AcquisitionError, OSError):
            pass
        raise
    except GateError as exc:
        details = (None, [], False)
        if journal_path.is_file():
            details = _journal_failure_details(journal_path, exc.category)
        try:
            blocked_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(
                blocked_path,
                _blocked_receipt(
                    final_path,
                    exc.category,
                    attempt_id=details[0] or attempt_id,
                    pages=details[1],
                    capture_complete=details[2],
                ),
            )
        except (AcquisitionError, OSError):
            pass
        raise AcquisitionError(exc.category) from exc
    except (OSError, ValueError) as exc:
        details = (None, [], False)
        if journal_path.is_file():
            details = _journal_failure_details(journal_path, "disk_or_contract_failure")
        try:
            blocked_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(
                blocked_path,
                _blocked_receipt(
                    final_path,
                    "disk_or_contract_failure",
                    attempt_id=details[0] or attempt_id,
                    pages=details[1],
                    capture_complete=details[2],
                ),
            )
        except (AcquisitionError, OSError):
            pass
        raise AcquisitionError("disk_or_contract_failure") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/research/external/m3_iwm_raw_2026_09_r4"),
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--blocked-receipt-out", type=Path)
    parser.add_argument("--result-receipt-out", type=Path)
    parser.add_argument("--revalidate-quarantine", type=Path)
    parser.add_argument("--promote-to", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if (args.revalidate_quarantine is None) != (args.promote_to is None):
            raise AcquisitionError("replay_arguments_incomplete")
        if args.revalidate_quarantine is not None and args.promote_to is not None:
            result = revalidate_quarantine(
                args.revalidate_quarantine,
                args.promote_to,
                blocked_receipt_out=args.blocked_receipt_out,
                result_receipt_out=args.result_receipt_out,
            )
        else:
            result = acquire_bundle(
                args.output,
                env_file=args.env_file,
                blocked_receipt_out=args.blocked_receipt_out,
                result_receipt_out=args.result_receipt_out,
            )
    except AcquisitionError as exc:
        print(f"M3_IWM_ACQUISITION_BLOCKED category={exc.category}", file=sys.stderr)
        return 20
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
