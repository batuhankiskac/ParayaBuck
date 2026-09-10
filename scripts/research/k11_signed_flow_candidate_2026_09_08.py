"""Frozen, research-only K11 spot observation; no strategy registration."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

from scripts.research.crypto_queue_causal_adapters_2026_09_08 import (
    CUTOFF,
    K11,
    REPO,
    read_rows,
    sha256,
    utc,
)

IDENTITY = "crypto_k11_signed_flow_24h_v1"
VERSION = "0.1.0-research"
LOOKBACK_HOURS = 24
EXPECTATION_PATH = REPO / "data/research_manifests/k11_signed_flow_archive_expectations_2026_09_08.json"


def _effective_available(row: dict[str, str]) -> datetime | None:
    raw = row.get("available_at", "").strip()
    return utc(raw) if raw else None


def load_archive_expectation(path: Path = EXPECTATION_PATH) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_archive_expectation(rows: list[dict[str, str]], expectation: dict[str, object]) -> dict[str, object]:
    """Prove that the supplied raw-row set matches its frozen archive contract."""
    identity = expectation["symbol_day"]
    archive = expectation["archive"]
    venue = str(identity["venue"])
    instrument = str(identity["instrument"])
    event_date = str(identity["event_date_utc"])
    if not venue.strip() or not instrument.strip() or not event_date.strip():
        raise ValueError("EXPECTATION_IDENTITY_EMPTY")
    window = expectation.get("window", {})
    if window.get("start_utc", "").strip() == "" or window.get("end_utc", "").strip() == "":
        raise ValueError("EXPECTATION_WINDOW_MISSING")
    window_start = utc(window["start_utc"])
    window_end = utc(window["end_utc"])
    if window_start.date().isoformat() != event_date or window_end != window_start + timedelta(days=1):
        raise ValueError("EXPECTATION_WINDOW_DAY_MISMATCH")
    target = [
        row
        for row in rows
        if row.get("venue") == venue
        and row.get("instrument") == instrument
        and utc(row["event_time_utc"]).date().isoformat() == event_date
    ]
    expected_count = int(archive["expected_row_count"])
    if len(target) != expected_count:
        raise ValueError(f"EXPECTATION_ROW_COUNT_MISMATCH:{len(target)}:{expected_count}")
    ids = [int(row["agg_trade_id"]) for row in target]
    if len(set(ids)) != len(ids):
        raise ValueError("EXPECTATION_DUPLICATE_AGG_TRADE_ID")
    expected_ids = archive.get("expected_id_set")
    if expected_ids is not None:
        if ids != list(map(int, expected_ids)) and sorted(ids) != list(map(int, expected_ids)):
            raise ValueError("EXPECTATION_ID_SET_MISMATCH")
    else:
        first_id = int(archive["first_agg_trade_id"])
        last_id = int(archive["last_agg_trade_id"])
        if sorted(ids) != list(range(first_id, last_id + 1)):
            raise ValueError("EXPECTATION_ID_CONTINUITY_GAP")
    if archive.get("kind") != "synthetic":
        archive_path = REPO / str(archive["path"])
        receipt_path = REPO / str(archive["receipt"])
        if not receipt_path.is_file():
            raise ValueError("EXPECTATION_RECEIPT_MISSING")
        if sha256(receipt_path) != archive["receipt_sha256"]:
            raise ValueError("EXPECTATION_RECEIPT_SHA256_MISMATCH")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("sha256") != archive["sha256"] or receipt.get("status") != 200:
            raise ValueError("EXPECTATION_RECEIPT_ARCHIVE_MISMATCH")
        endpoint = str(receipt.get("endpoint", ""))
        if endpoint != archive["endpoint"] or endpoint.rsplit("/", 1)[-1] != archive_path.name:
            raise ValueError("EXPECTATION_RECEIPT_ENDPOINT_MISMATCH")
        if sha256(archive_path) != archive["sha256"]:
            raise ValueError("EXPECTATION_ARCHIVE_SHA256_MISMATCH")
        with ZipFile(archive_path) as zipped:
            members = zipped.infolist()
            matching = [member for member in members if member.filename == archive["member"]]
            if len(matching) != 1:
                raise ValueError("EXPECTATION_ARCHIVE_MEMBER_MISMATCH")
            member = matching[0]
            if member.CRC != int(archive["member_crc32"]) or member.file_size != int(archive["member_size_bytes"]):
                raise ValueError("EXPECTATION_ARCHIVE_MEMBER_METADATA_MISMATCH")
        expected_source = str(archive_path.relative_to(REPO / "data/research/external/crypto_queue_acquisition_2026_09_07"))
        if any(row.get("source_file") != expected_source or row.get("source_sha256") != archive["sha256"] for row in target):
            raise ValueError("EXPECTATION_ROW_SOURCE_MISMATCH")
    return {
        "instrument": instrument,
        "event_date_utc": event_date,
        "expected_row_count": expected_count,
        "observed_row_count": len(target),
        "first_agg_trade_id": min(ids),
        "last_agg_trade_id": max(ids),
        "id_contract": archive.get("id_contract"),
        "archive_sha256": archive.get("sha256"),
        "window_start_utc": window_start.isoformat().replace("+00:00", "Z"),
        "window_end_utc": window_end.isoformat().replace("+00:00", "Z"),
    }


def next_spot_bar_open(
    spot_bars: list[dict[str, str]],
    instrument: str,
    decision_time: datetime,
) -> dict[str, object]:
    """Find one causal spot bar strictly after a completed signal decision."""
    if not isinstance(instrument, str) or not instrument.strip():
        return {"status": "INVALID_INSTRUMENT", "instrument": instrument, "execution_timestamp": None, "open": None}
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("naive_datetime:decision_time")
    decision_time = decision_time.astimezone(UTC)
    candidates: list[tuple[datetime, dict[str, str]]] = []
    for row in spot_bars:
        bar_instrument = row.get("instrument", "")
        if not isinstance(bar_instrument, str) or not bar_instrument.strip():
            return {"status": "INVALID_INSTRUMENT", "instrument": instrument, "execution_timestamp": None, "open": None}
        if bar_instrument != instrument or row.get("bar_kind") != "spot":
            continue
        event = utc(row["event_time_utc"])
        if event <= decision_time:
            continue
        candidates.append((event, row))
    if not candidates:
        return {"status": "MISSING_SPOT_BAR", "instrument": instrument, "execution_timestamp": None, "open": None}
    candidates.sort(key=lambda item: item[0])
    event = candidates[0][0]
    same_time = [row for timestamp, row in candidates if timestamp == event]
    if len(same_time) != 1:
        return {
            "status": "AMBIGUOUS_SPOT_BAR",
            "instrument": instrument,
            "execution_timestamp": event.isoformat().replace("+00:00", "Z"),
            "open": None,
        }
    row = same_time[0]
    raw_available = row.get("available_at", "").strip()
    if not raw_available:
        return {
            "status": "MISSING_SPOT_BAR_AVAILABILITY",
            "instrument": instrument,
            "execution_timestamp": event.isoformat().replace("+00:00", "Z"),
            "open": None,
        }
    available = utc(raw_available)
    if available > event:
        return {
            "status": "LATE_SPOT_BAR",
            "instrument": instrument,
            "execution_timestamp": event.isoformat().replace("+00:00", "Z"),
            "available_at": available.isoformat().replace("+00:00", "Z"),
            "open": None,
        }
    try:
        opening = float(row["open"])
    except (TypeError, ValueError):
        return {
            "status": "INVALID_SPOT_BAR_OPEN",
            "instrument": instrument,
            "execution_timestamp": event.isoformat().replace("+00:00", "Z"),
            "open": None,
            "reason": "non_numeric_open",
        }
    if not math.isfinite(opening) or opening <= 0:
        return {
            "status": "INVALID_SPOT_BAR_OPEN",
            "instrument": instrument,
            "execution_timestamp": event.isoformat().replace("+00:00", "Z"),
            "open": None,
        }
    return {
        "status": "EXECUTION_READY",
        "instrument": instrument,
        "execution_timestamp": event.isoformat().replace("+00:00", "Z"),
        "available_at": available.isoformat().replace("+00:00", "Z"),
        "open": opening,
    }


def signed_flow_signal(
    rows: list[dict[str, str]],
    instrument: str,
    decision_time: datetime,
    spot_bars: list[dict[str, str]] | None = None,
    expectation: dict[str, object] | None = None,
    venue: str | None = None,
) -> dict[str, object]:
    """Compute the predeclared K11 signal from causal native trade rows."""
    if not isinstance(instrument, str) or not instrument.strip():
        return {"instrument": instrument, "status": "EXPECTATION_BLOCKED", "expectation_error": "INVALID_INSTRUMENT", "signal": "cash", "execution": {"status": "NOT_ATTEMPTED"}}
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("naive_datetime:decision_time")
    decision_time = decision_time.astimezone(UTC)
    expectation = expectation or load_archive_expectation()
    expected_identity = expectation.get("symbol_day", {})
    expected_venue = str(expected_identity.get("venue", ""))
    if venue is not None and (not venue.strip() or venue != expected_venue):
        return {
            "instrument": instrument,
            "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
            "status": "EXPECTATION_BLOCKED",
            "expectation_error": "EXPECTATION_CALLER_VENUE_MISMATCH",
            "signal": "cash",
            "execution": {"status": "NOT_ATTEMPTED"},
        }
    row_venues = {str(row.get("venue", "")).strip() for row in rows if row.get("instrument") == instrument}
    if len(row_venues) != 1 or not expected_venue.strip() or row_venues != {expected_venue}:
        return {
            "instrument": instrument,
            "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
            "status": "EXPECTATION_BLOCKED",
            "expectation_error": "EXPECTATION_CALLER_VENUE_MISMATCH",
            "signal": "cash",
            "execution": {"status": "NOT_ATTEMPTED"},
        }
    end_hour = decision_time.replace(minute=0, second=0, microsecond=0)
    request_start = end_hour - timedelta(hours=LOOKBACK_HOURS)
    request_end = end_hour
    expected_window = expectation.get("window", {})
    if expected_identity.get("instrument") != instrument:
        return {
            "instrument": instrument,
            "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
            "status": "EXPECTATION_BLOCKED",
            "expectation_error": "EXPECTATION_CALLER_SYMBOL_MISMATCH",
            "signal": "cash",
            "execution": {"status": "NOT_ATTEMPTED"},
        }
    if (
        expected_window.get("start_utc") != request_start.isoformat().replace("+00:00", "Z")
        or expected_window.get("end_utc") != request_end.isoformat().replace("+00:00", "Z")
    ):
        return {
            "instrument": instrument,
            "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
            "status": "EXPECTATION_BLOCKED",
            "expectation_error": "EXPECTATION_CALLER_WINDOW_MISMATCH",
            "signal": "cash",
            "execution": {"status": "NOT_ATTEMPTED"},
        }
    try:
        expectation_summary = validate_archive_expectation(rows, expectation)
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "instrument": instrument,
            "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
            "status": "EXPECTATION_BLOCKED",
            "expectation_error": str(exc),
            "signal": "cash",
            "imbalance_24h": None,
            "execution": {"status": "NOT_ATTEMPTED"},
        }
    wanted = [end_hour - timedelta(hours=offset) for offset in range(LOOKBACK_HOURS, 0, -1)]
    raw_window: dict[datetime, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row["instrument"] != instrument:
            continue
        event = utc(row["event_time_utc"])
        if event >= decision_time:
            continue
        hour = event.replace(minute=0, second=0, microsecond=0)
        if hour not in wanted:
            continue
        raw_window[hour].append(row)
    missing_hours = [hour.isoformat().replace("+00:00", "Z") for hour in wanted if not raw_window.get(hour)]
    expected_rows = [row for hour in wanted for row in raw_window.get(hour, [])]
    late_rows = []
    missing_availability_rows = []
    for row in expected_rows:
        available = _effective_available(row)
        if available is None:
            missing_availability_rows.append(row["agg_trade_id"])
        elif available > decision_time:
            late_rows.append(row["agg_trade_id"])
    if missing_hours or late_rows or missing_availability_rows:
        return {
            "instrument": instrument,
            "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
            "status": "INCOMPLETE_CAUSAL_WINDOW",
            "available_hours": sum(1 for hour in wanted if raw_window.get(hour)),
            "expected_raw_rows": len(expected_rows),
            "missing_hours": missing_hours,
            "late_rows": late_rows,
            "missing_availability_rows": missing_availability_rows,
            "signal": "cash",
            "imbalance_24h": None,
            "execution": {"status": "NOT_ATTEMPTED"},
            "expectation": expectation_summary,
        }
    hourly: dict[datetime, dict[str, float]] = defaultdict(lambda: {"signed": 0.0, "gross": 0.0, "trades": 0.0})
    for row in expected_rows:
        event = utc(row["event_time_utc"])
        hour = event.replace(minute=0, second=0, microsecond=0)
        price = float(row["price"])
        quantity = float(row["quantity"])
        if not math.isfinite(price) or price <= 0 or not math.isfinite(quantity) or quantity <= 0:
            raise ValueError("invalid_trade_value")
        gross = price * quantity
        maker = row["is_buyer_maker"].strip().lower()
        if maker not in {"true", "false"}:
            raise ValueError("invalid_is_buyer_maker")
        sign = -1.0 if maker == "true" else 1.0
        hourly[hour]["signed"] += sign * gross
        hourly[hour]["gross"] += gross
        hourly[hour]["trades"] += 1.0
    gross = sum(hourly[hour]["gross"] for hour in wanted)
    signed = sum(hourly[hour]["signed"] for hour in wanted)
    imbalance = signed / gross if gross > 0 else None
    if imbalance is None or not math.isfinite(imbalance):
        return {
            "instrument": instrument,
            "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
            "status": "INVALID_ZERO_GROSS_NOTIONAL",
            "available_hours": LOOKBACK_HOURS,
            "signal": "cash",
            "imbalance_24h": None,
            "execution": {"status": "NOT_ATTEMPTED"},
        }
    execution = next_spot_bar_open(spot_bars or [], instrument, decision_time)
    if execution["status"] != "EXECUTION_READY":
        return {
            "instrument": instrument,
            "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
            "status": "EXECUTION_BLOCKED",
            "available_hours": LOOKBACK_HOURS,
            "expected_raw_rows": len(expected_rows),
            "signed_notional": signed,
            "gross_notional": gross,
            "imbalance_24h": imbalance,
            "signal": "cash",
            "execution": execution,
            "expectation": expectation_summary,
        }
    return {
        "instrument": instrument,
        "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
        "status": "SIGNAL_READY",
        "available_hours": LOOKBACK_HOURS,
        "signed_notional": signed,
        "gross_notional": gross,
        "expected_raw_rows": len(expected_rows),
        "imbalance_24h": imbalance,
        "signal": "long_spot" if imbalance > 0 else "cash",
        "execution": execution,
        "expectation": expectation_summary,
    }


def native_history_declaration(path: Path = K11) -> dict[str, object]:
    rows = read_rows(path)
    events = [utc(row["event_time_utc"]) for row in rows]
    instruments = sorted({row["instrument"] for row in rows})
    return {
        "identity": IDENTITY,
        "version": VERSION,
        "native_source": str(path),
        "native_frequency": "aggregate trades; signal buckets UTC hour",
        "instruments": instruments,
        "event_start": min(events).isoformat().replace("+00:00", "Z"),
        "event_end": max(events).isoformat().replace("+00:00", "Z"),
        "cutoff": CUTOFF.isoformat().replace("+00:00", "Z"),
        "causal_pilot_days": len({event.date().isoformat() for event in events}),
        "full_recent_economic_ready": False,
        "reason": "one BTC day only; ETH historical signed-flow archive absent",
    }


__all__ = [
    "IDENTITY",
    "LOOKBACK_HOURS",
    "VERSION",
    "load_archive_expectation",
    "native_history_declaration",
    "next_spot_bar_open",
    "signed_flow_signal",
    "validate_archive_expectation",
]
