"""Acquire and normalize the R46 v2 TLT market-data evidence package.

The module is deliberately credential-safe and fail-closed.  Alpaca raw and
split bar requests are separate, and the normalized split bars, cash
distributions, and historical identity evidence retain the fields required by
the R46 readiness contract.  A caller must provide a source vintage and an
``available_at`` timestamp; neither is inferred from retrieval time.

Network access is opt-in through :func:`acquire_tlt_market_data` (or the
``--fetch`` CLI flag).  Only sanitized request metadata and response bodies
are persisted.  Credentials and HTTP headers never enter a receipt or an
artifact.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse
from zoneinfo import ZoneInfo

import requests

REPO = Path(__file__).resolve().parents[2]
CUTOFF_UTC = "2026-07-31T23:59:59Z"
START = "2016-01-01"
END_EXCLUSIVE = "2026-08-01"
SYMBOL = "TLT"
FEED = "sip"
ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
ALPACA_DIVIDENDS_URL = "https://data.alpaca.markets/v1/corporate-actions"
ALPACA_IDENTITY_URL = "https://paper-api.alpaca.markets/v2/assets/TLT"
ACQUISITION_SCHEMA = "r46-tlt-market-data-acquisition-v2"
TLT_BARS_SCHEMA = "r46-tlt-split-adjusted-bars-v1"
TLT_DIVIDENDS_SCHEMA = "r46-tlt-separate-dividends-v1"
TLT_ELIGIBILITY_SCHEMA = "r46-tlt-pit-eligibility-v1"
HISTORICAL_IDENTITY_SCHEMA = "r46-tlt-historical-identity-evidence-v1"
NEW_YORK = ZoneInfo("America/New_York")
HEX64 = set("0123456789abcdef")
ALLOWED_BAR_FIELDS = frozenset({"t", "o", "h", "l", "c", "v", "n", "vw"})
ALLOWED_PAGE_FIELDS = frozenset({"bars", "next_page_token"})
ALLOWED_DIVIDEND_PAGE_FIELDS = frozenset({"corporate_actions", "dividends", "next_page_token"})
BAR_ROW_FIELDS = (
    "permanent_security_identifier",
    "listing_and_tradability_interval",
    "session_date",
    "regular_open_timestamp",
    "source_vintage",
    "available_at",
    "adjustment",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
DIVIDEND_ROW_FIELDS = (
    "permanent_security_identifier",
    "action_type",
    "effective_date",
    "cash_or_ratio",
    "source_document_hash",
    "duplicate_event_key",
    "event_timestamp",
    "currency",
    "available_at",
)
IDENTITY_FIELDS = (
    "canonical_symbol",
    "historical_symbol",
    "permanent_security_identifier",
    "provider_asset_id",
    "security_identifier",
    "venue",
    "listing_and_tradability_interval",
    "valid_from",
    "valid_to",
    "first_observed_bar",
    "last_observed_bar",
    "identity_basis",
    "source_url",
    "available_at",
)
IDENTITY_BASES = frozenset({"historical_provider_asset_map", "issuer_historical_filing", "exchange_archive"})
IDENTITY_HISTORY_HOSTS = frozenset(
    {
        "sec.gov",
        "www.sec.gov",
        "nasdaqtrader.com",
        "www.nasdaqtrader.com",
        "ir.nasdaq.com",
        "www.ishares.com",
        "data.alpaca.markets",
        "paper-api.alpaca.markets",
    }
)
SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "api_secret",
    "secret_key",
    "password",
    "credential",
    "header",
    "access_token",
    "refresh_token",
)


class AcquisitionContractError(ValueError):
    """Raised when a provider response cannot prove the R46 v2 contract."""


def load_calendar_rows(path: Path) -> Any:
    """Load either the pinned calendar JSONL artifact or a JSON fixture."""

    candidate = Path(path)
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AcquisitionContractError("calendar artifact unreadable") from exc
    if not text.strip():
        raise AcquisitionContractError("calendar artifact empty")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AcquisitionContractError(f"calendar JSONL invalid:{line_number}") from exc
            if not isinstance(value, Mapping):
                raise AcquisitionContractError(f"calendar JSONL row invalid:{line_number}")
            rows.append(dict(value))
        if not rows:
            raise AcquisitionContractError("calendar artifact empty")
        return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_aware(value: Any, field: str, *, cutoff: bool = True) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionContractError(f"{field} missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise AcquisitionContractError(f"{field} timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AcquisitionContractError(f"{field} timezone required")
    parsed = parsed.astimezone(UTC)
    if cutoff and parsed > _parse_aware(CUTOFF_UTC, "historical_cutoff", cutoff=False):
        raise AcquisitionContractError(f"{field} post_cutoff")
    return parsed


def _parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionContractError(f"{field} missing")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise AcquisitionContractError(f"{field} invalid") from exc


def _source_vintage(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionContractError("source_vintage missing")
    normalized = value.strip()
    if normalized.lower() in {"current", "latest", "unknown", "unset", "none"}:
        raise AcquisitionContractError("source_vintage must identify a frozen source vintage")
    return normalized


def _official_url(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or not value.strip():
        raise AcquisitionContractError("source_url invalid")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
        raise AcquisitionContractError("source_url must be HTTPS without credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise AcquisitionContractError("source_url port invalid") from exc
    if port not in (None, 443) or parsed.fragment:
        raise AcquisitionContractError("source_url must not contain query, fragment, or non-HTTPS port")
    if parsed.query:
        historical_hosts = {
            "sec.gov",
            "www.sec.gov",
            "nasdaqtrader.com",
            "www.nasdaqtrader.com",
            "ir.nasdaq.com",
            "www.ishares.com",
        }
        if parsed.hostname not in historical_hosts:
            raise AcquisitionContractError("source_url query allowed only for pinned historical evidence")
        query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        if any(any(part in key for part in SENSITIVE_KEY_PARTS) for key in query_keys):
            raise AcquisitionContractError("source_url query contains credential-like key")
    if parsed.hostname not in {
        "data.alpaca.markets",
        "paper-api.alpaca.markets",
        "www.ishares.com",
        "treasurydirect.gov",
        "www.treasurydirect.gov",
        "sec.gov",
        "www.sec.gov",
        "nasdaqtrader.com",
        "www.nasdaqtrader.com",
        "ir.nasdaq.com",
    }:
        raise AcquisitionContractError("source_url host is not an allowed identity source")
    return value


def _identity_value(identity: Mapping[str, Any], field: str) -> str:
    value = identity.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionContractError(f"identity {field} missing")
    return value.strip()


def _calendar_maps(calendar_rows: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Return validated session-date to regular-open/close mappings.

    Calendar timestamps are deliberately the only source used for normalized
    historical availability.  A mapping containing only opens is accepted for
    request-shape helpers but cannot satisfy the bar/dividend contract.
    """

    if calendar_rows is None:
        raise AcquisitionContractError("calendar rows required for PIT availability")
    if isinstance(calendar_rows, Mapping):
        rows: list[Mapping[str, Any]] = []
        for day, value in calendar_rows.items():
            if isinstance(value, Mapping):
                rows.append({"regular_session_date": day, **value})
            else:
                rows.append({"regular_session_date": day, "regular_open_timestamp": value})
    elif isinstance(calendar_rows, Sequence) and not isinstance(calendar_rows, (str, bytes, bytearray)):
        rows = []
        for row in calendar_rows:
            if not isinstance(row, Mapping):
                raise AcquisitionContractError("calendar row invalid")
            rows.append(row)
    else:
        raise AcquisitionContractError("calendar rows must be a mapping or list")
    opens: dict[str, str] = {}
    closes: dict[str, str] = {}
    seen_dates: set[str] = set()
    for row in rows:
        reason = row.get("holiday_or_closure_reason")
        day = str(row.get("regular_session_date", ""))
        _parse_date(day, "calendar regular_session_date")
        if day in seen_dates:
            raise AcquisitionContractError("calendar duplicate session")
        seen_dates.add(day)
        if reason not in (None, "", "NONE"):
            continue
        parsed_day = _parse_date(day, "calendar regular_session_date")
        open_at = _parse_aware(row.get("regular_open_timestamp"), "calendar regular_open_timestamp")
        close_at = _parse_aware(row.get("regular_close_timestamp"), "calendar regular_close_timestamp")
        local_open = open_at.astimezone(NEW_YORK)
        local_close = close_at.astimezone(NEW_YORK)
        if local_open.date() != parsed_day or local_open.strftime("%H:%M") != "09:30":
            raise AcquisitionContractError("calendar regular open is not 09:30 America/New_York")
        if local_close.date() != parsed_day or close_at <= open_at:
            raise AcquisitionContractError("calendar regular close invalid")
        opens[day] = open_at.isoformat().replace("+00:00", "Z")
        closes[day] = close_at.isoformat().replace("+00:00", "Z")
    if not opens:
        raise AcquisitionContractError("calendar rows contain no open sessions")
    return opens, closes


def _calendar_open_map(calendar_rows: Any) -> dict[str, str]:
    """Backward-compatible open-only view for callers that need session opens."""

    return _calendar_maps(calendar_rows)[0]


def build_alpaca_bars_request(
    adjustment: str,
    *,
    symbol: str = SYMBOL,
    start: str = START,
    end: str = END_EXCLUSIVE,
    feed: str = FEED,
) -> dict[str, Any]:
    """Return one sanitized, explicit Alpaca bars request contract."""

    if adjustment not in {"raw", "split"}:
        raise AcquisitionContractError("adjustment must be raw or split")
    if symbol != SYMBOL:
        raise AcquisitionContractError("R46 v2 market acquisition is TLT-only")
    _parse_date(start, "start")
    _parse_date(end, "end")
    if start >= end:
        raise AcquisitionContractError("bar request window invalid")
    return {
        "method": "GET",
        "url": ALPACA_BARS_URL,
        "params": {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "feed": feed,
            "adjustment": adjustment,
            "limit": 10_000,
            "sort": "asc",
        },
    }


def build_alpaca_dividends_request(
    *,
    symbol: str = SYMBOL,
    start: str = START,
    end: str = END_EXCLUSIVE,
) -> dict[str, Any]:
    """Return the separate corporate-action/dividend request contract."""

    if symbol != SYMBOL:
        raise AcquisitionContractError("R46 v2 market acquisition is TLT-only")
    _parse_date(start, "start")
    _parse_date(end, "end")
    if start >= end:
        raise AcquisitionContractError("dividend request window invalid")
    return {
        "method": "GET",
        "url": ALPACA_DIVIDENDS_URL,
        "params": {
            "symbols": symbol,
            "types": "cash_dividend",
            "start": start,
            "end": end,
            "limit": 1_000,
        },
    }


def build_alpaca_identity_request(*, symbol: str = SYMBOL) -> dict[str, Any]:
    """Return the identity route; current snapshots still need historical fields."""

    if symbol != SYMBOL:
        raise AcquisitionContractError("R46 v2 market acquisition is TLT-only")
    return {"method": "GET", "url": ALPACA_IDENTITY_URL, "params": {}}


def _pages(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        values: Sequence[Any] = [payload]
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        values = payload
    else:
        raise AcquisitionContractError("Alpaca pages must be an object or list of objects")
    result: list[dict[str, Any]] = []
    for page in values:
        if not isinstance(page, Mapping) or not set(page).issubset(ALLOWED_PAGE_FIELDS) or "bars" not in page:
            raise AcquisitionContractError("Alpaca page schema invalid")
        if not isinstance(page["bars"], Mapping):
            raise AcquisitionContractError("Alpaca bars map invalid")
        result.append(dict(page))
    if not result:
        raise AcquisitionContractError("Alpaca pages empty")
    return result


def _identity_records(identity: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(identity, Mapping):
        return [identity]
    if isinstance(identity, Sequence) and not isinstance(identity, (str, bytes, bytearray)):
        records = [row for row in identity if isinstance(row, Mapping)]
        if len(records) != len(identity) or not records:
            raise AcquisitionContractError("identity records invalid")
        return records
    raise AcquisitionContractError("identity records invalid")


def _identity_for_date(identity: Mapping[str, Any] | Sequence[Mapping[str, Any]], value: date) -> Mapping[str, Any]:
    matches: list[Mapping[str, Any]] = []
    for record in _identity_records(identity):
        valid_from = _parse_date(record.get("valid_from"), "identity valid_from")
        valid_to = None if record.get("valid_to") is None else _parse_date(record.get("valid_to"), "identity valid_to")
        if valid_to is not None and valid_to < valid_from:
            raise AcquisitionContractError("identity listing interval reversed")
        if valid_from <= value and (valid_to is None or value < valid_to):
            matches.append(record)
    if len(matches) != 1:
        raise AcquisitionContractError(f"identity interval unavailable:{value.isoformat()}")
    return matches[0]


def normalize_alpaca_bars(
    payload: Any,
    adjustment: str,
    *,
    identity: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    source_vintage: str,
    available_at: str | None = None,
    acquisition_time: str | None = None,
    calendar_rows: Any = None,
    regular_open_timestamps: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize one raw or split Alpaca response into R46 bar rows."""

    if adjustment not in {"raw", "split"}:
        raise AcquisitionContractError("adjustment must be raw or split")
    vintage = _source_vintage(source_vintage)
    acquisition_value = acquisition_time or available_at
    if acquisition_value is None:
        raise AcquisitionContractError("acquisition_time missing")
    _parse_aware(acquisition_value, "acquisition_time", cutoff=False)
    open_timestamps, close_timestamps = _calendar_maps(calendar_rows)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for page in _pages(payload):
        bars_map = page["bars"]
        symbols = set(str(symbol) for symbol in bars_map)
        if symbols != {SYMBOL}:
            raise AcquisitionContractError("Alpaca payload symbols must be exactly TLT")
        values = bars_map.get(SYMBOL)
        if not isinstance(values, list) or not values:
            raise AcquisitionContractError("Alpaca TLT bars are empty")
        for bar in values:
            if not isinstance(bar, Mapping) or not {"t", "o", "h", "l", "c", "v"}.issubset(bar) or not set(bar).issubset(ALLOWED_BAR_FIELDS):
                raise AcquisitionContractError("Alpaca bar schema invalid")
            timestamp = _parse_aware(bar["t"], "regular_open_timestamp")
            local = timestamp.astimezone(NEW_YORK)
            session_date = local.date().isoformat()
            if session_date not in open_timestamps or session_date not in close_timestamps:
                raise AcquisitionContractError("calendar session missing for bar")
            identity_record = _identity_for_date(identity, local.date())
            permanent_id = _identity_value(identity_record, "permanent_security_identifier")
            listing_interval = _identity_value(identity_record, "listing_and_tradability_interval")
            regular_open = _parse_aware(open_timestamps[session_date], "regular_open_timestamp")
            regular_close = _parse_aware(close_timestamps[session_date], "regular_close_timestamp")
            values_float: list[float]
            try:
                values_float = [float(bar[field]) for field in ("o", "h", "l", "c", "v")]
            except (TypeError, ValueError) as exc:
                raise AcquisitionContractError("Alpaca bar values invalid") from exc
            if not all(math.isfinite(value) for value in values_float) or any(value <= 0 for value in values_float[:4]) or values_float[4] < 0:
                raise AcquisitionContractError("Alpaca bar values invalid")
            if not values_float[2] <= values_float[0] <= values_float[1] or not values_float[2] <= values_float[3] <= values_float[1]:
                raise AcquisitionContractError("Alpaca bar OHLC invalid")
            timestamp_text = regular_open.isoformat().replace("+00:00", "Z")
            key = (session_date, timestamp_text)
            if key in seen:
                raise AcquisitionContractError("Alpaca bar duplicate")
            seen.add(key)
            rows.append(
                {
                    "permanent_security_identifier": permanent_id,
                    "listing_and_tradability_interval": listing_interval,
                    "session_date": session_date,
                    "regular_open_timestamp": timestamp_text,
                    "source_vintage": vintage,
                    "available_at": regular_close.isoformat().replace("+00:00", "Z"),
                    "adjustment": adjustment,
                    "open": values_float[0],
                    "high": values_float[1],
                    "low": values_float[2],
                    "close": values_float[3],
                    "volume": values_float[4],
                }
            )
    rows.sort(key=lambda row: (row["session_date"], row["regular_open_timestamp"]))
    return rows


def normalize_alpaca_bars_pair(
    raw_payload: Any,
    split_payload: Any,
    *,
    identity: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    source_vintage: str,
    available_at: str | None = None,
    acquisition_time: str | None = None,
    calendar_rows: Any = None,
    regular_open_timestamps: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize raw and split responses and enforce timestamp-key parity."""

    raw_rows = normalize_alpaca_bars(
        raw_payload,
        "raw",
        identity=identity,
        source_vintage=source_vintage,
        available_at=available_at,
        acquisition_time=acquisition_time,
        calendar_rows=calendar_rows,
        regular_open_timestamps=regular_open_timestamps,
    )
    split_rows = normalize_alpaca_bars(
        split_payload,
        "split",
        identity=identity,
        source_vintage=source_vintage,
        available_at=available_at,
        acquisition_time=acquisition_time,
        calendar_rows=calendar_rows,
        regular_open_timestamps=regular_open_timestamps,
    )
    raw_keys = {(row["session_date"], row["regular_open_timestamp"]) for row in raw_rows}
    split_keys = {(row["session_date"], row["regular_open_timestamp"]) for row in split_rows}
    if raw_keys != split_keys:
        raise AcquisitionContractError("raw_split_key_parity")
    acquisition_value = acquisition_time or available_at
    if acquisition_value is None:
        raise AcquisitionContractError("acquisition_time missing")
    acquisition = _parse_aware(acquisition_value, "acquisition_time", cutoff=False)
    available_values = [row["available_at"] for row in split_rows]
    available_at_value = max(available_values) if available_values else None
    return {
        "schema": TLT_BARS_SCHEMA,
        "status": "COMPLETE",
        "raw_rows": raw_rows,
        "split_rows": split_rows,
        "raw_split_key_parity": True,
        "source_vintage": _source_vintage(source_vintage),
        "available_at": available_at_value,
        "acquisition_time": acquisition.isoformat().replace("+00:00", "Z"),
        "availability_basis": "session_close_from_supplied_nyse_calendar",
    }


def _adapt_current_cash_dividend(action: Mapping[str, Any]) -> dict[str, Any]:
    """Map the current v1 ``cash_dividends`` row into the R46 row contract."""

    if action.get("action_type") == "cash_dividend" or action.get("ca_type") == "dividend":
        return dict(action)
    required = ("id", "symbol", "ex_date", "process_date", "rate")
    if any(not isinstance(action.get(field), str) or not str(action[field]).strip() for field in required[:4]):
        raise AcquisitionContractError("current cash dividend identity fields missing")
    if action.get("symbol") != SYMBOL:
        raise AcquisitionContractError("corporate action symbol must be TLT")
    try:
        effective_date = _parse_date(action["ex_date"], "effective_date")
        process_date = _parse_date(action["process_date"], "process_date")
        amount = float(action["rate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AcquisitionContractError("current cash dividend fields invalid") from exc
    if not math.isfinite(amount) or amount <= 0:
        raise AcquisitionContractError("current cash dividend rate invalid")
    encoded = json.dumps(dict(action), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        **dict(action),
        "action_type": "cash_dividend",
        "amount": amount,
        "currency": "USD",
        "event_timestamp": f"{process_date.isoformat()}T00:00:00Z",
        "source_document_hash": hashlib.sha256(encoded).hexdigest(),
        "duplicate_event_key": f"{str(action['id']).strip()}:{effective_date.isoformat()}",
    }


def normalize_alpaca_dividends(
    payload: Any,
    *,
    identity: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    source_vintage: str,
    available_at: str | None = None,
    acquisition_time: str | None = None,
    calendar_rows: Any = None,
) -> list[dict[str, Any]]:
    """Normalize explicit cash-dividend corporate-action records."""

    _source_vintage(source_vintage)
    acquisition_value = acquisition_time or available_at
    if acquisition_value is None:
        raise AcquisitionContractError("acquisition_time missing")
    _parse_aware(acquisition_value, "acquisition_time", cutoff=False)
    open_timestamps, _ = _calendar_maps(calendar_rows)
    if isinstance(payload, Mapping):
        actions = payload.get("corporate_actions", payload.get("dividends"))
        if isinstance(actions, Mapping):
            action_types = set(actions)
            if action_types != {"cash_dividends"}:
                raise AcquisitionContractError("corporate action type mapping invalid")
            provider_actions = actions.get("cash_dividends")
            if not isinstance(provider_actions, list):
                raise AcquisitionContractError("cash_dividends list missing")
            actions = [_adapt_current_cash_dividend(action) for action in provider_actions]
    else:
        actions = payload
    if not isinstance(actions, list):
        raise AcquisitionContractError("corporate_actions list missing")
    actions = [_adapt_current_cash_dividend(action) if isinstance(action, Mapping) else action for action in actions]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for action in actions:
        if not isinstance(action, Mapping):
            raise AcquisitionContractError("corporate action schema invalid")
        symbol = action.get("symbol") or action.get("initiating_symbol") or action.get("target_symbol")
        if symbol != SYMBOL:
            raise AcquisitionContractError("corporate action symbol must be TLT")
        explicit_action_type = action.get("action_type")
        provider_type = str(action.get("ca_type", "")).casefold()
        provider_subtype = str(action.get("ca_sub_type", "")).casefold()
        if explicit_action_type != "cash_dividend" and not (
            provider_type == "dividend" and provider_subtype in {"cash", "cash_dividend", ""}
        ):
            raise AcquisitionContractError("unsupported corporate action for dividend artifact")
        source_hash = action.get("source_document_hash")
        if source_hash is None:
            encoded_action = json.dumps(dict(action), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            source_hash = hashlib.sha256(encoded_action).hexdigest()
        if not isinstance(source_hash, str) or len(source_hash) != 64 or any(char not in HEX64 for char in source_hash):
            raise AcquisitionContractError("source_document_hash missing or invalid")
        duplicate_key = action.get("duplicate_event_key")
        effective_date = _parse_date(action.get("ex_date"), "effective_date")
        if duplicate_key is None:
            action_id = action.get("id") or action.get("corporate_action_id")
            if not isinstance(action_id, str) or not action_id.strip():
                raise AcquisitionContractError("corporate action id missing")
            duplicate_key = f"{action_id.strip()}:{effective_date.isoformat()}"
        if not isinstance(duplicate_key, str) or not duplicate_key.strip():
            raise AcquisitionContractError("duplicate_event_key missing")
        # Keep parsing the provider timestamp as source evidence, but do not
        # use it as the economic event time.  The current Alpaca corporate-
        # action route exposes ``process_date`` as a date-only field encoded
        # at 00:00 UTC; that instant can fall on the prior America/New_York
        # civil date (and can also be later than the ex-date).  R46's frozen
        # PIT policy is the ex-date regular NYSE open from the supplied
        # calendar, which is also the timestamp consumed by the native
        # corporate-action engine.
        event_timestamp = action.get("event_timestamp") or action.get("created_at") or action.get("updated_at")
        _parse_aware(event_timestamp, "event_timestamp")
        if effective_date.isoformat() not in open_timestamps:
            raise AcquisitionContractError("calendar session missing for dividend")
        row_available = _parse_aware(open_timestamps[effective_date.isoformat()], "dividend available_at")
        identity_record = _identity_for_date(identity, effective_date)
        permanent_id = _identity_value(identity_record, "permanent_security_identifier")
        raw_amount: Any = action.get("amount", action.get("cash"))
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError) as exc:
            raise AcquisitionContractError("cash_or_ratio invalid") from exc
        if not math.isfinite(amount) or amount <= 0:
            raise AcquisitionContractError("cash_or_ratio must be positive")
        timestamp_text = row_available.isoformat().replace("+00:00", "Z")
        key = (permanent_id, timestamp_text)
        if key in seen:
            raise AcquisitionContractError("duplicate dividend event")
        seen.add(key)
        rows.append(
            {
                "permanent_security_identifier": permanent_id,
                "action_type": "cash_dividend",
                "effective_date": effective_date.isoformat(),
                "cash_or_ratio": amount,
                "source_document_hash": source_hash,
                "duplicate_event_key": duplicate_key.strip(),
                "event_timestamp": timestamp_text,
                "currency": action.get("currency"),
                "available_at": row_available.isoformat().replace("+00:00", "Z"),
            }
        )
        if rows[-1]["currency"] != "USD":
            raise AcquisitionContractError("cash dividend currency must be USD")
    rows.sort(key=lambda row: row["event_timestamp"])
    return rows


def normalize_alpaca_identity(
    payload: Any,
    *,
    source_vintage: str,
    available_at: str | None = None,
    acquisition_time: str | None = None,
) -> dict[str, Any]:
    """Normalize historical identity/listing evidence; current-only assets fail."""

    _source_vintage(source_vintage)
    acquisition_value = acquisition_time or available_at
    if acquisition_value is None:
        raise AcquisitionContractError("acquisition_time missing")
    artifact_available = _parse_aware(acquisition_value, "acquisition_time", cutoff=False)
    if isinstance(payload, Mapping) and isinstance(payload.get("asset"), Mapping):
        asset: Mapping[str, Any] = payload["asset"]
    elif isinstance(payload, Mapping):
        asset = payload
    else:
        raise AcquisitionContractError("identity payload must be an object")
    for field in IDENTITY_FIELDS:
        if field not in asset:
            raise AcquisitionContractError(f"identity {field} missing")
    if asset.get("canonical_symbol") != SYMBOL or asset.get("historical_symbol") != SYMBOL:
        raise AcquisitionContractError("identity symbol must be TLT")
    if asset.get("identity_basis") not in IDENTITY_BASES:
        raise AcquisitionContractError("identity_basis must prove historical provider identity")
    security_identifier = asset.get("security_identifier")
    if not isinstance(security_identifier, str) or len(security_identifier) != 9 or not security_identifier.isalnum() or security_identifier != security_identifier.upper():
        raise AcquisitionContractError("security_identifier invalid")
    source_url = _official_url(asset.get("source_url"))
    if urlparse(source_url).hostname not in IDENTITY_HISTORY_HOSTS:
        raise AcquisitionContractError("identity source_url is not an allowed historical source")
    identity_available = _parse_aware(asset.get("available_at"), "identity available_at")
    if identity_available > artifact_available:
        raise AcquisitionContractError("identity available_at after artifact available_at")
    valid_from = _parse_date(asset.get("valid_from"), "valid_from")
    valid_to = None if asset.get("valid_to") is None else _parse_date(asset.get("valid_to"), "valid_to")
    first_observed = _parse_date(asset.get("first_observed_bar"), "first_observed_bar")
    last_observed = _parse_date(asset.get("last_observed_bar"), "last_observed_bar")
    if valid_to is not None and valid_to < valid_from:
        raise AcquisitionContractError("identity listing interval reversed")
    if last_observed < first_observed:
        raise AcquisitionContractError("identity observed interval reversed")
    normalized = {field: asset[field] for field in IDENTITY_FIELDS}
    normalized["source_url"] = source_url
    normalized["available_at"] = identity_available.isoformat().replace("+00:00", "Z")
    return normalized


def _canonical_identity_hash(payload: Mapping[str, Any], field: str) -> str:
    value = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    integrity = value.get("integrity")
    if not isinstance(integrity, dict):
        raise AcquisitionContractError("identity evidence integrity missing")
    integrity.pop(field, None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_alpaca_identity_records(
    payload: Any,
    *,
    source_vintage: str,
    acquisition_time: str,
) -> list[dict[str, Any]]:
    """Normalize one or more cutoff-bounded historical identity intervals.

    A multi-row wrapper must carry its own canonical artifact hash.  The rows
    may cite issuer or exchange evidence; a separately fetched Alpaca asset
    snapshot is attested by the acquisition layer and never replaces them.
    """

    vintage = _source_vintage(source_vintage)
    if isinstance(payload, Mapping) and isinstance(payload.get("records"), list):
        if payload.get("schema") != HISTORICAL_IDENTITY_SCHEMA:
            raise AcquisitionContractError("historical identity evidence schema invalid")
        integrity = payload.get("integrity")
        declared = integrity.get("artifact_sha256") if isinstance(integrity, Mapping) else None
        if not isinstance(declared, str) or len(declared) != 64 or any(char not in HEX64 for char in declared):
            raise AcquisitionContractError("historical identity evidence hash missing")
        if declared != _canonical_identity_hash(payload, "artifact_sha256"):
            raise AcquisitionContractError("historical identity evidence hash mismatch")
        records = payload["records"]
        if not records:
            raise AcquisitionContractError("historical identity records empty")
        wrapper_vintage = payload.get("source_vintage")
        if isinstance(wrapper_vintage, str) and wrapper_vintage.strip():
            vintage = _source_vintage(wrapper_vintage)
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        records = payload
    else:
        records = [payload]
    normalized = [
        normalize_alpaca_identity(record, source_vintage=vintage, acquisition_time=acquisition_time)
        for record in records
    ]
    normalized.sort(key=lambda row: str(row["valid_from"]))
    for previous, current in zip(normalized, normalized[1:]):
        previous_end = previous["valid_to"]
        if previous_end is None or str(previous_end) > str(current["valid_from"]):
            raise AcquisitionContractError("identity intervals overlap")
        for field in ("canonical_symbol", "historical_symbol", "permanent_security_identifier", "provider_asset_id", "security_identifier"):
            if previous[field] != current[field]:
                raise AcquisitionContractError(f"identity continuity mismatch:{field}")
    return normalized


def _provider_identity_attestation(payload: Any) -> dict[str, Any]:
    if isinstance(payload, Mapping) and isinstance(payload.get("asset"), Mapping):
        asset: Mapping[str, Any] = payload["asset"]
    elif isinstance(payload, Mapping):
        asset = payload
    else:
        raise AcquisitionContractError("provider identity payload must be an object")
    symbol = asset.get("symbol") or asset.get("canonical_symbol")
    provider_asset_id = asset.get("id") or asset.get("asset_id") or asset.get("provider_asset_id")
    if symbol != SYMBOL or not isinstance(provider_asset_id, str) or not provider_asset_id.strip():
        raise AcquisitionContractError("provider identity stable symbol/id missing")
    status = asset.get("status")
    if not isinstance(status, str) or not status.strip():
        raise AcquisitionContractError("provider identity status missing")
    return {
        "route": ALPACA_IDENTITY_URL,
        "symbol": SYMBOL,
        "provider_asset_id": provider_asset_id.strip(),
        "exchange": asset.get("exchange"),
        "status": status.strip(),
        "tradable": asset.get("tradable"),
    }


def _assert_provider_identity_matches(rows: Sequence[Mapping[str, Any]], provider: Mapping[str, Any]) -> None:
    provider_id = provider.get("provider_asset_id")
    if not isinstance(provider_id, str) or not provider_id:
        raise AcquisitionContractError("provider identity stable id missing")
    for row in rows:
        if row.get("provider_asset_id") != provider_id:
            raise AcquisitionContractError("provider/historical identity mismatch")


def _response_payload(result: Any) -> tuple[int, Any]:
    if isinstance(result, tuple) and len(result) == 2:
        status, payload = result
        if not isinstance(status, int):
            raise AcquisitionContractError("transport status invalid")
        return status, payload
    status = getattr(result, "status_code", None)
    if not isinstance(status, int):
        raise AcquisitionContractError("transport response status missing")
    try:
        payload = result.json()
    except (AttributeError, ValueError) as exc:
        raise AcquisitionContractError("transport JSON payload missing") from exc
    return status, payload


def _requests_transport(url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: float) -> tuple[int, Any]:
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return response.status_code, payload


def _fetch_pages(
    request: Mapping[str, Any],
    *,
    headers: dict[str, str],
    transport: Callable[..., Any],
    max_pages: int = 1_000,
) -> tuple[list[dict[str, Any]], list[int], list[str]]:
    pages: list[dict[str, Any]] = []
    statuses: list[int] = []
    errors: list[str] = []
    params = dict(request["params"])
    token: Any = None
    for _ in range(max_pages):
        request_params = dict(params)
        if token:
            request_params["page_token"] = token
        try:
            result = transport(str(request["url"]), params=request_params, headers=headers, timeout=45.0)
            status, payload = _response_payload(result)
        except Exception as exc:
            errors.append(type(exc).__name__)
            break
        statuses.append(status)
        if status != 200:
            errors.append(f"HTTP {status}")
            break
        if not isinstance(payload, Mapping):
            errors.append("JSON object missing")
            break
        page = dict(payload)
        pages.append(page)
        token = page.get("next_page_token")
        if not token:
            return pages, statuses, errors
    if token:
        errors.append("pagination_limit")
    return pages, statuses, errors


def _fetch_single(
    request: Mapping[str, Any],
    *,
    headers: dict[str, str],
    transport: Callable[..., Any],
) -> tuple[dict[str, Any] | None, list[int], list[str]]:
    """Fetch one non-paginated provider route without retaining credentials."""

    try:
        result = transport(str(request["url"]), params=dict(request["params"]), headers=headers, timeout=45.0)
        status, payload = _response_payload(result)
    except Exception as exc:
        return None, [], [type(exc).__name__]
    if status != 200:
        return None, [status], [f"HTTP {status}"]
    if not isinstance(payload, Mapping):
        return None, [status], ["JSON object missing"]
    return dict(payload), [status], []


def _validate_dividend_pages(pages: Sequence[Mapping[str, Any]]) -> None:
    for page in pages:
        if not set(page).issubset(ALLOWED_DIVIDEND_PAGE_FIELDS):
            raise AcquisitionContractError("corporate action page schema invalid")
        actions = page.get("corporate_actions", page.get("dividends"))
        if isinstance(actions, Mapping):
            if set(actions) != {"cash_dividends"}:
                raise AcquisitionContractError("corporate action type mapping invalid")
            actions = actions.get("cash_dividends")
        if not isinstance(actions, list):
            raise AcquisitionContractError("corporate_actions list missing")


def _combine_dividend_pages(pages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    _validate_dividend_pages(pages)
    actions: list[Any] = []
    for page in pages:
        values = page.get("corporate_actions", page.get("dividends"))
        if isinstance(values, Mapping):
            values = values.get("cash_dividends")
        if isinstance(values, list):
            # Keep the persisted source payload byte/field-faithful to the
            # provider response.  ``normalize_alpaca_dividends`` performs the
            # current-route adaptation separately; adapting here would hash
            # the derived fields instead of the raw provider action and would
            # make the package raw-hash cross-check impossible.
            actions.extend(values)
    return {"corporate_actions": actions}


def _load_dotenv(path: Path) -> dict[str, str]:
    """Read only simple ``KEY=VALUE`` entries; never echo or persist values."""

    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise AcquisitionContractError("dotenv path invalid")
    values: dict[str, str] = {}
    try:
        lines = candidate.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AcquisitionContractError("dotenv unreadable") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise AcquisitionContractError("dotenv entry invalid")
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            raise AcquisitionContractError("dotenv key invalid")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _credentials_from_environment(dotenv_path: Path | None) -> tuple[str, str]:
    environment = dict(os.environ)
    if dotenv_path is not None:
        for key, value in _load_dotenv(dotenv_path).items():
            environment.setdefault(key, value)
    key_id = environment.get("APCA_API_KEY_ID", environment.get("ALPACA_API_KEY", ""))
    secret_key = environment.get("APCA_API_SECRET_KEY", environment.get("ALPACA_SECRET_KEY", ""))
    return str(key_id or ""), str(secret_key or "")


def _ensure_output_dir(path: Path) -> Path:
    candidate = Path(path)
    cursor = candidate
    while cursor != Path(cursor.anchor):
        if cursor.is_symlink():
            raise AcquisitionContractError("output directory symlink forbidden")
        cursor = cursor.parent
    if candidate.exists() and candidate.is_symlink():
        raise AcquisitionContractError("output directory symlink forbidden")
    candidate.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve(strict=True)
    if resolved == Path(resolved.anchor):
        raise AcquisitionContractError("output directory root forbidden")
    current = resolved
    while current != Path(current.anchor):
        if current.is_symlink():
            raise AcquisitionContractError("output directory symlink forbidden")
        current = current.parent
    return resolved


def _atomic_publish(path: Path, content: bytes) -> Path:
    """Publish atomically, refusing existing files and symlink targets."""

    destination = Path(path)
    cursor = destination
    while cursor != Path(cursor.anchor):
        if cursor.is_symlink():
            raise AcquisitionContractError("publication path symlink forbidden")
        cursor = cursor.parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent))
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_name, destination)
        os.unlink(temporary_name)
        temporary_name = None
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return destination


def _write_json(path: Path, payload: Any) -> None:
    content = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_publish(path, content)


def _sanitize_for_persistence(value: Any) -> Any:
    """Drop credential/header/token keys before any provider body is written."""

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key).lower().replace("-", "_")
            if any(part in name for part in SENSITIVE_KEY_PARTS) or name.endswith("_token") or name == "next_page_token":
                continue
            sanitized[str(key)] = _sanitize_for_persistence(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_persistence(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_persistence(item) for item in value]
    return value


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    content = "".join(json.dumps(dict(row), sort_keys=True, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
    _atomic_publish(path, content)


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    _atomic_publish(path, buffer.getvalue().encode("utf-8"))


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _canonical_self_hash(payload: Mapping[str, Any], field: str) -> str:
    value = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    integrity = value.get("integrity")
    if not isinstance(integrity, dict):
        raise AcquisitionContractError("integrity object missing")
    integrity.pop(field, None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def acquire_tlt_market_data(
    output_dir: Path,
    *,
    identity_payload: Any | None = None,
    dividends_payload: Any | None = None,
    source_vintage: str,
    available_at: str | None = None,
    acquisition_time: str | None = None,
    identity_source_vintage: str | None = None,
    dividends_source_vintage: str | None = None,
    identity_available_at: str | None = None,
    dividends_available_at: str | None = None,
    calendar_rows: Any = None,
    credentials: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
    transport: Callable[..., Any] | None = None,
    start: str = START,
    end: str = END_EXCLUSIVE,
) -> dict[str, Any]:
    """Fetch all Alpaca routes and publish one credential-safe evidence bundle.

    With credentials, raw bars, split bars, v1 corporate actions, and the
    paper-api identity route are all fetched.  Explicit payloads are accepted
    only as offline fixtures/fallbacks; they never replace a successful
    authenticated response.  Historical availability is derived from the
    supplied NYSE calendar, while ``acquisition_time`` is retained separately
    and may be after the historical cutoff.
    """

    output_dir = _ensure_output_dir(Path(output_dir))
    vintage = _source_vintage(source_vintage)
    acquisition_value = acquisition_time or available_at
    if acquisition_value is None:
        raise AcquisitionContractError("acquisition_time missing")
    acquisition = _parse_aware(acquisition_value, "acquisition_time", cutoff=False)
    artifact_available = _parse_aware(available_at or CUTOFF_UTC, "available_at")
    identity_vintage = _source_vintage(identity_source_vintage or vintage)
    dividends_vintage = _source_vintage(dividends_source_vintage or vintage)
    open_timestamps, close_timestamps = _calendar_maps(calendar_rows)
    request_contracts = {
        "raw": build_alpaca_bars_request("raw", start=start, end=end),
        "split": build_alpaca_bars_request("split", start=start, end=end),
        "dividends": build_alpaca_dividends_request(start=start, end=end),
        "identity": build_alpaca_identity_request(),
    }
    if credentials is None:
        key_id, secret_key = _credentials_from_environment(dotenv_path)
    else:
        key_id = str(credentials.get("key_id") or credentials.get("api_key_id") or credentials.get("api_key") or "")
        secret_key = str(credentials.get("secret_key") or credentials.get("api_secret") or credentials.get("secret") or "")
    credentials_used = bool(key_id and secret_key)
    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key} if credentials_used else {}
    fetch = transport or _requests_transport

    if credentials_used:
        raw_pages, raw_statuses, raw_errors = _fetch_pages(request_contracts["raw"], headers=headers, transport=fetch)
        split_pages, split_statuses, split_errors = _fetch_pages(request_contracts["split"], headers=headers, transport=fetch)
        dividends_pages, dividends_statuses, dividends_errors = _fetch_pages(request_contracts["dividends"], headers=headers, transport=fetch)
        fetched_identity, identity_statuses, identity_errors = _fetch_single(request_contracts["identity"], headers=headers, transport=fetch)
    else:
        raw_pages, raw_statuses, raw_errors = [], [], ["credentials_missing"]
        split_pages, split_statuses, split_errors = [], [], ["credentials_missing"]
        dividends_pages, dividends_statuses, dividends_errors = [], [], ["credentials_missing"]
        fetched_identity, identity_statuses, identity_errors = None, [], ["credentials_missing"]
    network_used = bool(raw_statuses or split_statuses or dividends_statuses or identity_statuses)
    fetched_dividends = _combine_dividend_pages(dividends_pages) if dividends_pages and not dividends_errors else None
    effective_identity_payload = identity_payload if identity_payload is not None else fetched_identity
    effective_dividends_payload = fetched_dividends if fetched_dividends is not None and not dividends_errors else dividends_payload
    errors: dict[str, list[str]] = {
        "raw": raw_errors,
        "split": split_errors,
        "dividends": dividends_errors,
        "identity": identity_errors,
    }
    paths: dict[str, Path] = {}
    identity_records: list[dict[str, Any]] = []
    provider_identity: dict[str, Any] | None = None
    dividends: list[dict[str, Any]] = []
    normalization_errors: list[str] = []
    if fetched_identity is not None and not identity_errors:
        try:
            provider_identity = _provider_identity_attestation(fetched_identity)
        except AcquisitionContractError as exc:
            normalization_errors.append(str(exc))
    if effective_identity_payload is not None:
        try:
            identity_records = normalize_alpaca_identity_records(
                effective_identity_payload,
                source_vintage=identity_vintage,
                acquisition_time=acquisition.isoformat().replace("+00:00", "Z"),
            )
            if provider_identity is not None:
                _assert_provider_identity_matches(identity_records, provider_identity)
        except AcquisitionContractError as exc:
            normalization_errors.append(str(exc))
    else:
        normalization_errors.append("identity payload missing")
    if effective_dividends_payload is not None and identity_records:
        try:
            dividends = normalize_alpaca_dividends(
                effective_dividends_payload,
                identity=identity_records,
                source_vintage=dividends_vintage,
                acquisition_time=acquisition.isoformat().replace("+00:00", "Z"),
                calendar_rows=calendar_rows,
            )
        except AcquisitionContractError as exc:
            normalization_errors.append(str(exc))
    elif effective_dividends_payload is None:
        normalization_errors.append("dividends payload missing")
    pair: dict[str, Any] | None = None
    raw_split_key_parity = False
    if raw_pages and split_pages and not raw_errors and not split_errors and identity_records:
        try:
            pair = normalize_alpaca_bars_pair(
                raw_pages,
                split_pages,
                identity=identity_records,
                source_vintage=vintage,
                acquisition_time=acquisition.isoformat().replace("+00:00", "Z"),
                calendar_rows=calendar_rows,
            )
            raw_path = output_dir / "raw_bars.csv"
            split_path = output_dir / "split_bars.csv"
            _write_csv(raw_path, BAR_ROW_FIELDS, pair["raw_rows"])
            _write_csv(split_path, BAR_ROW_FIELDS, pair["split_rows"])
            paths.update({"raw_bars": raw_path, "split_bars": split_path})
            raw_split_key_parity = True
        except AcquisitionContractError as exc:
            normalization_errors.append(str(exc))
    if normalization_errors:
        errors["normalization"] = normalization_errors

    raw_pages_path = output_dir / "raw_pages.json"
    split_pages_path = output_dir / "split_pages.json"
    dividends_source_path = output_dir / "dividends_source.json"
    identity_source_path = output_dir / "identity_source.json"
    identity_provider_source_path = output_dir / "identity_provider_source.json"
    dividends_path = output_dir / "dividends.csv"
    eligibility_path = output_dir / "eligibility.jsonl"
    _write_json(raw_pages_path, _sanitize_for_persistence(raw_pages))
    _write_json(split_pages_path, _sanitize_for_persistence(split_pages))
    _write_json(dividends_source_path, _sanitize_for_persistence(effective_dividends_payload or {}))
    _write_json(identity_source_path, _sanitize_for_persistence(effective_identity_payload or {}))
    _write_json(identity_provider_source_path, _sanitize_for_persistence(fetched_identity or {}))
    _write_csv(dividends_path, DIVIDEND_ROW_FIELDS, dividends)
    _write_jsonl(eligibility_path, identity_records)
    paths.update({"raw_pages": raw_pages_path, "split_pages": split_pages_path, "dividends": dividends_path, "eligibility": eligibility_path})

    def artifact(path: Path, *, source: str, available: str, adjustment: str | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {
            "path": _relative(path, output_dir),
            "sha256": sha256(path),
            "source_vintage": source,
            "available_at": available,
        }
        if adjustment is not None:
            value["adjustment"] = adjustment
        return value

    bars_available = pair["available_at"] if pair is not None else artifact_available.isoformat().replace("+00:00", "Z")
    dividend_available = max((row["available_at"] for row in dividends), default=artifact_available.isoformat().replace("+00:00", "Z"))
    identity_available = max((row["available_at"] for row in identity_records), default=artifact_available.isoformat().replace("+00:00", "Z"))
    complete = raw_split_key_parity and bool(identity_records) and not normalization_errors and not raw_errors and not split_errors and not dividends_errors and not identity_errors
    receipt: dict[str, Any] = {
        "schema": ACQUISITION_SCHEMA,
        "status": "COMPLETE" if complete else "BLOCKED",
        "candidate_id": "R46",
        "symbol": SYMBOL,
        "acquisition_mode": "retrospective",
        "retrospective_acquisition": True,
        "historical_cutoff": CUTOFF_UTC,
        "source_vintage": vintage,
        "available_at": artifact_available.isoformat().replace("+00:00", "Z"),
        "acquisition_time": acquisition.isoformat().replace("+00:00", "Z"),
        "source_vintages": {"bars": vintage, "identity": identity_vintage, "dividends": dividends_vintage},
        "availability_basis": {
            "bars": "session_close_from_supplied_nyse_calendar",
            "dividends": "ex_date_regular_open_from_supplied_nyse_calendar",
        },
        "corporate_action_normalization_basis": {
            "provider_route": ALPACA_DIVIDENDS_URL,
            "container": "corporate_actions.cash_dividends",
            "rate_field": "rate_to_cash_or_ratio",
            "event_timestamp": "effective_date_regular_open_from_supplied_nyse_calendar",
            "currency": "USD_contract_derived",
            "source_document_hash": "sha256_canonical_raw_provider_row",
            "duplicate_event_key": "provider_id_plus_ex_date",
        },
        "credentials_used": credentials_used,
        "network_used": network_used,
        "economic_execution": False,
        "raw_split_key_parity": raw_split_key_parity,
        "requests": request_contracts,
        "responses": {
            "raw": {"http_statuses": raw_statuses, "pages": len(raw_pages), "errors": raw_errors},
            "split": {"http_statuses": split_statuses, "pages": len(split_pages), "errors": split_errors},
            "dividends": {"http_statuses": dividends_statuses, "pages": len(dividends_pages), "errors": dividends_errors},
            "identity": {"http_statuses": identity_statuses, "pages": 1 if fetched_identity is not None else 0, "errors": identity_errors},
        },
        "identity_attestation": provider_identity,
        "errors": errors,
        "artifacts": {
            "raw_bars": artifact(paths["raw_bars"], source=vintage, available=bars_available, adjustment="raw") if "raw_bars" in paths else None,
            "split_bars": artifact(paths["split_bars"], source=vintage, available=bars_available, adjustment="split") if "split_bars" in paths else None,
            "dividends": artifact(paths["dividends"], source=dividends_vintage, available=dividend_available),
            "eligibility": artifact(paths["eligibility"], source=identity_vintage, available=identity_available),
        },
        "raw_pages": {"path": _relative(raw_pages_path, output_dir), "sha256": sha256(raw_pages_path)},
        "split_pages": {"path": _relative(split_pages_path, output_dir), "sha256": sha256(split_pages_path)},
        "dividends_source": {"path": _relative(dividends_source_path, output_dir), "sha256": sha256(dividends_source_path)},
        "identity_source": {"path": _relative(identity_source_path, output_dir), "sha256": sha256(identity_source_path)},
        "identity_provider_source": {"path": _relative(identity_provider_source_path, output_dir), "sha256": sha256(identity_provider_source_path)},
        "integrity": {
            "canonicalization": "UTF-8 JSON sorted object keys compact separators ensure_ascii=false; omit integrity.receipt_sha256",
            "receipt_sha256": "",
        },
    }
    receipt["integrity"]["receipt_sha256"] = _canonical_self_hash(receipt, "receipt_sha256")
    _write_json(output_dir / "receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true", help="Opt into authenticated Alpaca HTTP requests")
    parser.add_argument("--identity-json", type=Path)
    parser.add_argument("--dividends-json", type=Path)
    parser.add_argument("--calendar-json", type=Path)
    parser.add_argument("--dotenv", type=Path)
    parser.add_argument("--source-vintage")
    parser.add_argument("--available-at")
    parser.add_argument("--acquisition-time")
    args = parser.parse_args(argv)
    if not args.fetch:
        print(json.dumps({"schema": ACQUISITION_SCHEMA, "status": "NO_NETWORK", "network_used": False, "requests": {
            "raw": build_alpaca_bars_request("raw"),
            "split": build_alpaca_bars_request("split"),
            "dividends": build_alpaca_dividends_request(),
            "identity": build_alpaca_identity_request(),
        }}, indent=2, sort_keys=True))
        return 0
    if args.calendar_json is None or not args.source_vintage or not args.acquisition_time:
        parser.error("--fetch requires --calendar-json, --source-vintage, and --acquisition-time")
    identity_payload = json.loads(args.identity_json.read_text(encoding="utf-8")) if args.identity_json else None
    dividends_payload = json.loads(args.dividends_json.read_text(encoding="utf-8")) if args.dividends_json else None
    calendar_rows = load_calendar_rows(args.calendar_json)
    receipt = acquire_tlt_market_data(
        args.output_dir,
        identity_payload=identity_payload,
        dividends_payload=dividends_payload,
        source_vintage=args.source_vintage,
        available_at=args.available_at,
        acquisition_time=args.acquisition_time,
        calendar_rows=calendar_rows,
        dotenv_path=args.dotenv,
    )
    print(json.dumps({"schema": receipt["schema"], "status": receipt["status"], "network_used": receipt["network_used"], "credentials_used": receipt["credentials_used"]}, sort_keys=True))
    return 0 if receipt["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
