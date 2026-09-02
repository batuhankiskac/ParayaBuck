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
import shutil
import sys
import tempfile
import time
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
    SOURCE_ADJUSTMENT,
    SOURCE_API_FAMILY_VERSION,
    SOURCE_END,
    SOURCE_ENDPOINT,
    SOURCE_FEED,
    SOURCE_PROVIDER,
    SOURCE_SORT,
    SOURCE_START,
    GateError,
    canonical_digest,
    canonical_timestamp,
    normalize_provider_bar,
    validate_iwm_bundle,
)

HOST = "data.alpaca.markets"
BASE_URL = f"https://{HOST}{SOURCE_ENDPOINT}"
ALLOWED_SYMBOL = "IWM"
REQUEST_PARAMS: dict[str, str | int] = {
    "symbols": ALLOWED_SYMBOL,
    "timeframe": "1Day",
    "start": SOURCE_START.isoformat(),
    "end": SOURCE_END.isoformat(),
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


def _parse_page(
    body: bytes, *, input_page_token: str | None
) -> tuple[Mapping[str, object], tuple[Any, ...], str | None]:
    try:
        # Decimal parsing keeps the exact provider numeric lexemes available
        # to the repository normalizer; the raw page itself is still written
        # byte-for-byte and is never reserialized.
        payload = json.loads(body.decode("utf-8"), parse_float=Decimal, parse_int=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcquisitionError("malformed_json") from exc
    if not isinstance(payload, Mapping):
        raise AcquisitionError("response_schema_mismatch")
    if payload.get("error") or payload.get("errors") or str(payload.get("status", "")).lower() in {"error", "failed"}:
        raise AcquisitionError("provider_error_payload")
    if "next_page_token" not in payload:
        raise AcquisitionError("missing_next_page_token")
    output_token = _continuation_token(payload.get("next_page_token"))
    bars = payload.get("bars")
    if not isinstance(bars, Mapping) or set(str(key) for key in bars) != {ALLOWED_SYMBOL}:
        raise AcquisitionError("response_schema_mismatch")
    entries = bars.get(ALLOWED_SYMBOL)
    if not isinstance(entries, list):
        raise AcquisitionError("response_schema_mismatch")
    normalized: list[Any] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise AcquisitionError("response_schema_mismatch")
        try:
            normalized.append(normalize_provider_bar(entry))
        except GateError as exc:
            raise AcquisitionError("response_schema_mismatch") from exc
    if tuple(normalized) != tuple(sorted(normalized, key=lambda row: row.sort_key)):
        raise AcquisitionError("source_order")
    if input_page_token is not None and not input_page_token:
        raise AcquisitionError("pagination_schema_mismatch")
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


def _blocked_receipt(path: Path, category: str) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "mission": "equity_m3_iwm_acquisition_2026_09_r3",
        "status": "M3_DATA_GATE_BLOCKED",
        "category": category,
        "host": HOST,
        "endpoint": SOURCE_ENDPOINT,
        "symbol": ALLOWED_SYMBOL,
        "feed": SOURCE_FEED,
        "output_bundle_name": path.name,
        "credentials_disclosed": False,
        "raw_pages_promoted": False,
    }


def _source_receipt(
    *,
    acquisition_timestamp_utc: str,
    pages: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "provider": SOURCE_PROVIDER,
        "api_family_version": SOURCE_API_FAMILY_VERSION,
        "provider_api_product_version": "market_data_v2_stocks_bars",
        "asset_class": "us_equity",
        "symbol": ALLOWED_SYMBOL,
        "endpoint_identity": SOURCE_ENDPOINT,
        "acquisition_timestamp_utc": acquisition_timestamp_utc,
        "request": {
            "start": SOURCE_START.isoformat(),
            "end": SOURCE_END.isoformat(),
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


def acquire_bundle(
    output: str | Path,
    *,
    env_file: str | Path | None = None,
    transport: Transport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    timestamp_utc: Callable[[], datetime] = lambda: datetime.now(UTC),
    blocked_receipt_out: str | Path | None = None,
) -> dict[str, object]:
    """Acquire, self-validate, and atomically publish one IWM raw bundle."""
    final_path = Path(output)
    blocked_path = (
        Path(blocked_receipt_out)
        if blocked_receipt_out is not None
        else final_path.with_name(final_path.name + ".blocked.json")
    )
    parent = final_path.parent
    temporary_path: Path | None = None
    try:
        if final_path.exists() or final_path.is_symlink():
            raise AcquisitionError("existing_bundle_conflict")
        api_key, secret_key = load_credentials(env_file)
        parent.mkdir(parents=True, exist_ok=True)
        pages_dir = Path(tempfile.mkdtemp(prefix=f".{final_path.name}.tmp-", dir=str(parent))) / "pages"
        temporary_path = pages_dir.parent
        pages_dir.mkdir(parents=True, exist_ok=True)
        active_transport = transport or RequestsTransport()
        input_token: str | None = None
        page_records: list[dict[str, object]] = []
        prior_row_keys: set[tuple[object, ...]] = set()
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
            _payload, normalized, output_token = _parse_page(body, input_page_token=input_token)
            row_keys = {row.key for row in normalized}
            if len(row_keys) != len(normalized) or prior_row_keys & row_keys:
                raise AcquisitionError("duplicate_page_or_session")
            prior_row_keys.update(row_keys)
            page_index += 1
            relative = f"pages/{page_index:06d}.json"
            page_path = temporary_path / relative
            page_path.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes_atomically(page_path, body)
            page_records.append(
                {
                    "relative_path": relative,
                    "raw_sha256": sha256(body).hexdigest(),
                    "raw_byte_size": len(body),
                    "row_count": len(normalized),
                    "input_page_token": input_token,
                    "output_next_page_token": output_token,
                    "http_status": response.status_code,
                    "retry_count": retry_count,
                }
            )
            if output_token is None:
                break
            input_token = output_token

        receipt = _source_receipt(
            acquisition_timestamp_utc=canonical_timestamp(timestamp_utc()),
            pages=page_records,
        )
        _write_json_atomically(temporary_path / "source_receipt.json", receipt)
        try:
            normalized_rows, audit = validate_iwm_bundle(temporary_path, temporary_path / "validation-stage")
        except (GateError, OSError) as exc:
            raise AcquisitionError("bundle_validation_failure") from exc
        if not normalized_rows:
            raise AcquisitionError("empty_iwm_bundle")
        os.replace(temporary_path, final_path)
        temporary_path = None
        source_receipt_bytes = (final_path / "source_receipt.json").read_bytes()
        pages = audit.get("pages", [])
        page_chain_digest = canonical_digest(pages) if isinstance(pages, list) else ""
        return {
            "schema_version": "1.0.0",
            "mission": "equity_m3_iwm_acquisition_2026_09_r3",
            "status": "ACQUIRED",
            "bundle_name": final_path.name,
            "host": HOST,
            "endpoint": SOURCE_ENDPOINT,
            "symbol": ALLOWED_SYMBOL,
            "feed": SOURCE_FEED,
            "adjustment": SOURCE_ADJUSTMENT,
            "page_count": len(audit.get("pages", ())),
            "row_count": len(normalized_rows),
            "first_timestamp": canonical_timestamp(normalized_rows[0].timestamp),
            "last_timestamp": canonical_timestamp(normalized_rows[-1].timestamp),
            "raw_bundle_sha256": audit["raw_bundle_sha256"],
            "source_receipt_sha256": sha256(source_receipt_bytes).hexdigest(),
            "page_chain_digest_sha256": page_chain_digest,
            "credentials_disclosed": False,
            "raw_tokens_in_result": False,
        }
    except AcquisitionError as exc:
        if temporary_path is not None:
            shutil.rmtree(temporary_path, ignore_errors=True)
        try:
            blocked_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(blocked_path, _blocked_receipt(final_path, exc.category))
        except (AcquisitionError, OSError):
            pass
        raise
    except (OSError, ValueError) as exc:
        if temporary_path is not None:
            shutil.rmtree(temporary_path, ignore_errors=True)
        try:
            blocked_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(blocked_path, _blocked_receipt(final_path, "disk_or_contract_failure"))
        except (AcquisitionError, OSError):
            pass
        raise AcquisitionError("disk_or_contract_failure") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/research/external/m3_iwm_raw_2026_09_r3"),
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--blocked-receipt-out", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = acquire_bundle(
            args.output,
            env_file=args.env_file,
            blocked_receipt_out=args.blocked_receipt_out,
        )
    except AcquisitionError as exc:
        print(f"M3_IWM_ACQUISITION_BLOCKED category={exc.category}", file=sys.stderr)
        return 20
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
