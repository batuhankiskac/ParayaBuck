"""Research-only causal adapters for the new crypto queue data paths.

The adapters normalize existing, receipt-backed acquisition files and fail
closed on missing point-in-time availability. They never touch runtime
configuration, strategy registration, PAPER/LIVE state, or frozen candidates.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zipfile import ZipFile

REPO = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07"
PROTOCOL = REPO / "docs/research/crypto_queue_causal_protocol_2026_09_08.md"
MANIFEST = REPO / "data/research_manifests/crypto_queue_causal_adapters_2026_09_08.json"
REPORT = REPO / "data/research_runs/crypto_queue_causal_adapters_2026_09_08/gate_report.json"
K11_CANDIDATE = REPO / "scripts/research/k11_signed_flow_candidate_2026_09_08.py"
K11_BRIEF = REPO / "docs/research/k11_signed_flow_candidate_brief_2026_09_08.md"
K11_TEST = REPO / "tests/research/test_k11_signed_flow_candidate_2026_09_08.py"
ADAPTER_TEST = REPO / "tests/research/test_crypto_queue_causal_adapters_2026_09_08.py"
CUTOFF = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)

K01 = DATA_ROOT / "normalized/K01_MVRV_BTC_ETH_1d.csv"
K03 = DATA_ROOT / "normalized/K03_FEE_USAGE_BTC_ETH_1d.csv"
K04 = DATA_ROOT / "normalized/K04_SUPPLY_ISSUANCE_BTC_ETH_1d.csv"
K09_BARS = DATA_ROOT / "normalized/K09_binance_causal_bars_pilot.csv"
K09_FUNDING = DATA_ROOT / "normalized/K09_binance_funding_archive_pilot.csv"
K09_OI = DATA_ROOT / "normalized/K09_binance_oi_metrics_archive_pilot.csv"
K11 = DATA_ROOT / "normalized/K11_binance_aggtrades_pilot.csv"
K11_EXPECTATION = REPO / "data/research_manifests/k11_signed_flow_archive_expectations_2026_09_08.json"

COINMETRICS_COMMON = ("asset", "event_time", "metric", "value", "unit", "source_file", "source_sha256", "available_at")
K01_METRICS = {"CapMVRVCur", "CapMrktCurUSD"}
K03_METRICS = {"FeeTotNtv"}
K04_METRICS = {"IssTotNtv", "SplyCur"}
_SOURCE_HASH_CACHE: dict[Path, str] = {}
_ARCHIVE_AVAIL_CACHE: dict[str, datetime | None] = {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp_not_string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp_not_timezone_aware:{value}")
    return parsed.astimezone(UTC)


def _source_path(source_file: str) -> Path:
    path = DATA_ROOT / source_file
    if not path.is_file():
        raise ValueError(f"source_file_missing:{source_file}")
    return path


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"empty_input:{path}")
    return rows


def _validate_source(row: dict[str, str]) -> None:
    source = _source_path(row["source_file"])
    observed = _SOURCE_HASH_CACHE.get(source)
    if observed is None:
        observed = sha256(source)
        _SOURCE_HASH_CACHE[source] = observed
    if row["source_sha256"] != observed:
        raise ValueError(f"source_hash_mismatch:{row['source_file']}")


def _availability(row: dict[str, str]) -> datetime | None:
    value = row.get("available_at", "").strip()
    return utc(value) if value else None


def archive_available_at(source_file: str) -> datetime | None:
    """Return the official archive server publication bound, if captured."""
    if source_file in _ARCHIVE_AVAIL_CACHE:
        return _ARCHIVE_AVAIL_CACHE[source_file]
    receipt = DATA_ROOT / f"{source_file}.receipt.json"
    if not receipt.is_file():
        _ARCHIVE_AVAIL_CACHE[source_file] = None
        return None
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    raw = payload.get("response_headers", {}).get("last-modified")
    if not raw:
        _ARCHIVE_AVAIL_CACHE[source_file] = None
        return None
    parsed = parsedate_to_datetime(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp_not_explicit_utc:{raw}")
    value = parsed.astimezone(UTC)
    _ARCHIVE_AVAIL_CACHE[source_file] = value
    return value


def _validate_coinmetrics(
    rows: Iterable[dict[str, str]],
    metrics: set[str],
    *,
    strict_positive_metrics: set[str] | None = None,
) -> dict[str, object]:
    strict_positive_metrics = strict_positive_metrics or set()
    keys: set[tuple[str, str, str]] = set()
    missing_available = 0
    post_cutoff = 0
    for row in rows:
        if tuple(row.get(name, "") for name in COINMETRICS_COMMON) and row["metric"] not in metrics:
            raise ValueError(f"unexpected_metric:{row['metric']}")
        event = utc(row["event_time"])
        if event > CUTOFF:
            post_cutoff += 1
        key = (row["asset"], event.isoformat(), row["metric"])
        if key in keys:
            raise ValueError(f"duplicate_primary_key:{key}")
        keys.add(key)
        try:
            value = float(row["value"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid_numeric_value:{key}") from exc
        if not math.isfinite(value) or (
            row["metric"] in {"CapMVRVCur", "CapMrktCurUSD", "SplyCur"} and value <= 0
        ) or (row["metric"] in strict_positive_metrics and value <= 0):
            raise ValueError(f"invalid_value:{key}")
        _validate_source(row)
        if _availability(row) is None:
            missing_available += 1
    if post_cutoff:
        raise ValueError(f"post_cutoff_rows:{post_cutoff}")
    return {"rows": len(keys), "missing_available_at": missing_available, "post_cutoff_rows": post_cutoff}


def k01_mvrv(path: Path = K01) -> dict[str, object]:
    rows = read_rows(path)
    validation = _validate_coinmetrics(rows, K01_METRICS)
    values = [row for row in rows if row["metric"] == "CapMVRVCur"]
    by_asset: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in values:
        by_asset[row["asset"]].append(
            {
                "asset": row["asset"],
                "event_time": row["event_time"],
                "mvrv": float(row["value"]),
                "available_at": row.get("available_at") or None,
                "source_sha256": row["source_sha256"],
            }
        )
    return {
        "card": "K01",
        "recipe_id": "K01_CM_CapMVRVCur_v1",
        "formula": "CapMVRVCur consumed directly; market cap retained only for audit",
        "validation": validation,
        "assets": {asset: len(points) for asset, points in sorted(by_asset.items())},
        "pit_status": "PIT_PENDING" if validation["missing_available_at"] else "PIT_READY",
        "economic_signal_rows": 0 if validation["missing_available_at"] else sum(len(points) for points in by_asset.values()),
    }


def _daily_points(rows: Iterable[dict[str, str]], metric: str) -> dict[str, dict[datetime, float]]:
    points: dict[str, dict[datetime, float]] = defaultdict(dict)
    for row in rows:
        if row["metric"] == metric:
            points[row["asset"]][utc(row["event_time"])] = float(row["value"])
    return points


def k03_fee_growth(path: Path = K03) -> dict[str, object]:
    rows = read_rows(path)
    validation = _validate_coinmetrics(rows, K03_METRICS, strict_positive_metrics={"FeeTotNtv"})
    points = _daily_points(rows, "FeeTotNtv")
    features: list[dict[str, object]] = []
    for asset, series in sorted(points.items()):
        for event in sorted(series):
            window = [event - timedelta(days=offset) for offset in range(30, -1, -1)]
            if not all(day in series for day in window):
                continue
            first, last = series[window[0]], series[window[-1]]
            if first < 0 or last < 0:
                raise ValueError(f"negative_fee:{asset}:{event.isoformat()}")
            features.append(
                {
                    "asset": asset,
                    "event_time": event.isoformat().replace("+00:00", "Z"),
                    "fee_growth_30d": math.log1p(last) - math.log1p(first),
                    "available_at": None,
                }
            )
    return {
        "card": "K03",
        "recipe_id": "K03_CM_FeeTotNtvGrowth30_v1",
        "metric_semantics": "native transaction fees; not active-address or Sybil-adjusted usage",
        "formula": "log1p(FeeTotNtv_t)-log1p(FeeTotNtv_t-30d)",
        "validation": validation,
        "feature_rows": len(features),
        "feature_preview": features[:2],
        "pit_status": "PIT_PENDING" if validation["missing_available_at"] else "PIT_READY",
        "economic_signal_rows": 0 if validation["missing_available_at"] else len(features),
    }


def k04_supply_audit(path: Path = K04) -> dict[str, object]:
    rows = read_rows(path)
    validation = _validate_coinmetrics(rows, K04_METRICS)
    audit = compute_gross_issuance_audit(rows)
    causal_audit_rows = sum(1 for row in audit if row["causally_available"])
    return {
        "card": "K04",
        "recipe_id": "K04_CM_GrossIssuanceAudit_v1",
        "formula": "IssTotNtv / prior SplyCur; audit ratio only",
        "gross_issuance_is_net_dilution": False,
        "burn_metric_present": False,
        "net_supply_candidate_status": "BLOCKED_BURN_MISSING",
        "validation": validation,
        "gross_audit_rows": len(audit),
        "causal_gross_audit_rows": causal_audit_rows,
        "gross_audit_preview": audit[:2],
        "pit_status": "PIT_PENDING" if validation["missing_available_at"] else "PIT_READY",
        "economic_signal_rows": 0,
    }


def compute_gross_issuance_audit(rows: Iterable[dict[str, str]]) -> list[dict[str, object]]:
    """Compute the aligned gross issuance audit without calling it net dilution."""
    materialized = list(rows)
    issuance = _daily_points(materialized, "IssTotNtv")
    supply = _daily_points(materialized, "SplyCur")
    availability: dict[tuple[str, datetime, str], datetime | None] = {}
    for row in materialized:
        metric = row["metric"]
        if metric in K04_METRICS:
            availability[(row["asset"], utc(row["event_time"]), metric)] = _availability(row)
    audit: list[dict[str, object]] = []
    for asset in sorted(issuance):
        for event in sorted(issuance[asset]):
            prior = event - timedelta(days=1)
            if prior not in supply.get(asset, {}):
                continue
            gross_issuance = issuance[asset][event]
            prior_supply = supply[asset][prior]
            if gross_issuance < 0 or not math.isfinite(gross_issuance):
                raise ValueError(f"invalid_gross_issuance:{asset}:{event.isoformat()}")
            if prior_supply <= 0 or not math.isfinite(prior_supply):
                raise ValueError(f"invalid_prior_supply:{asset}:{prior.isoformat()}")
            issue_available = availability.get((asset, event, "IssTotNtv"))
            supply_available = availability.get((asset, prior, "SplyCur"))
            causally_available = (
                issue_available is not None
                and supply_available is not None
                and supply_available <= issue_available
            )
            audit.append(
                {
                    "asset": asset,
                    "event_time": event.isoformat().replace("+00:00", "Z"),
                    "prior_supply_event_time": prior.isoformat().replace("+00:00", "Z"),
                    "gross_issuance": gross_issuance,
                    "prior_supply": prior_supply,
                    "gross_issuance_over_prior_supply": gross_issuance / prior_supply,
                    "causally_available": causally_available,
                }
            )
    return audit


def validate_binance_row_schema(row: dict[str, str], schema: str) -> None:
    """Validate required Binance fields without consulting source files."""
    required = {"venue", "instrument", "event_time_utc", "source_file", "source_sha256"}
    if schema == "bars":
        required |= {"bar_kind", "open", "high", "low", "close", "close_time_utc"}
    elif schema == "funding":
        required |= {"contract_type", "metric", "value", "unit", "funding_interval_hours"}
    elif schema == "oi":
        required |= {"contract_type", "metric", "value", "unit", "sum_open_interest_value_usd"}
    elif schema == "trades":
        required |= {"agg_trade_id", "price", "quantity", "is_buyer_maker"}
    else:
        raise ValueError(f"unknown_binance_schema:{schema}")
    for field in required:
        raw = row.get(field)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"invalid_empty_field:{field}")
    utc(row["event_time_utc"])
    if schema == "bars":
        if row["bar_kind"].strip() not in {"spot", "futures", "index", "mark", "spot_or_futures"}:
            raise ValueError("invalid_bar_field:bar_kind")
        utc(row["close_time_utc"])
        for field in ("open", "high", "low", "close"):
            try:
                value = float(row[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid_numeric_field:{field}") from exc
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid_bar_field:{field}")
    elif schema in {"funding", "oi"}:
        for field in ("value", "funding_interval_hours") if schema == "funding" else ("value", "sum_open_interest_value_usd"):
            try:
                value = float(row[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid_numeric_field:{field}") from exc
            if not math.isfinite(value) or (field == "funding_interval_hours" and value <= 0) or (
                schema == "oi" and field == "sum_open_interest_value_usd" and value < 0
            ):
                raise ValueError(f"invalid_metric_field:{field}")
        if row.get("publication_time", "").strip():
            utc(row["publication_time"])
    elif schema == "trades":
        try:
            trade_id = int(row["agg_trade_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_numeric_field:agg_trade_id") from exc
        if trade_id < 0:
            raise ValueError("invalid_trade_field:agg_trade_id")
        for field in ("price", "quantity"):
            try:
                value = float(row[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid_numeric_field:{field}") from exc
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid_trade_field:{field}")
        if row["is_buyer_maker"].strip().lower() not in {"true", "false"}:
            raise ValueError("invalid_trade_field:is_buyer_maker")


def _validate_binance(
    rows: Iterable[dict[str, str]],
    key_fields: tuple[str, ...],
    *,
    schema: str = "trades",
) -> dict[str, object]:
    keys: set[tuple[str, ...]] = set()
    missing_available = 0
    post_cutoff = 0
    for row in rows:
        validate_binance_row_schema(row, schema)
        event_key = "event_time_utc"
        event = utc(row[event_key])
        if event > CUTOFF:
            post_cutoff += 1
        normalized_event = event.isoformat()
        key = tuple(normalized_event if field == "event_time_utc" else row.get(field, "") for field in key_fields)
        if key in keys:
            raise ValueError(f"duplicate_primary_key:{key}")
        keys.add(key)
        _validate_source(row)
        if _availability(row) is None:
            missing_available += 1
    if post_cutoff:
        raise ValueError(f"post_cutoff_rows:{post_cutoff}")
    return {"rows": len(keys), "missing_available_at": missing_available, "post_cutoff_rows": post_cutoff}


def k09_archive_join(
    bars_path: Path = K09_BARS,
    funding_path: Path = K09_FUNDING,
    oi_path: Path = K09_OI,
) -> dict[str, object]:
    bars = read_rows(bars_path)
    funding = read_rows(funding_path)
    oi = read_rows(oi_path)
    bar_validation = _validate_binance(bars, ("venue", "instrument", "bar_kind", "event_time_utc"), schema="bars")
    funding_validation = _validate_binance(
        funding, ("venue", "contract_type", "instrument", "metric", "event_time_utc"), schema="funding"
    )
    oi_validation = _validate_binance(
        oi, ("venue", "contract_type", "instrument", "metric", "event_time_utc"), schema="oi"
    )
    bar_kinds = sorted({row["bar_kind"] for row in bars})
    effective_bar_kinds = []
    futures_events: set[tuple[str, datetime]] = set()
    for row in bars:
        kind = row["bar_kind"]
        if kind == "spot_or_futures":
            receipt = DATA_ROOT / f"{row['source_file']}.receipt.json"
            endpoint = json.loads(receipt.read_text(encoding="utf-8")).get("endpoint", "")
            if "/futures/um/daily/klines/" not in endpoint:
                raise ValueError(f"unresolved_bar_kind:{row['source_file']}")
            kind = "futures"
        effective_bar_kinds.append(kind)
        if kind == "futures":
            futures_events.add((row["instrument"], utc(row["event_time_utc"])))
    identity_status = "IDENTITY_OK_SOURCE_VERIFIED"
    archive_available = [archive_available_at(row["source_file"]) for row in bars + funding + oi]
    archive_available = [value for value in archive_available if value is not None]
    within_cutoff = sum(value <= CUTOFF for value in archive_available)
    oi_events: dict[str, list[datetime]] = defaultdict(list)
    for row in oi:
        oi_events[row["instrument"]].append(utc(row["event_time_utc"]))
    for event_list in oi_events.values():
        event_list.sort()
    raw_join_rows = 0
    causal_join_rows = 0
    for row in funding:
        event = utc(row["event_time_utc"])
        instrument = row["instrument"]
        if (instrument, event) not in futures_events:
            continue
        prior_oi = [candidate for candidate in oi_events.get(instrument, []) if candidate <= event]
        if not prior_oi:
            continue
        raw_join_rows += 1
        source_bounds = [
            archive_available_at(row["source_file"]),
            archive_available_at(next(item["source_file"] for item in bars if item["instrument"] == instrument and utc(item["event_time_utc"]) == event and (item["bar_kind"] in {"futures", "spot_or_futures"}))),
            archive_available_at(next(item["source_file"] for item in oi if item["instrument"] == instrument and utc(item["event_time_utc"]) == prior_oi[-1])),
        ]
        if all(bound is not None and bound <= CUTOFF for bound in source_bounds):
            causal_join_rows += 1
    return {
        "card": "K09",
        "recipe_ids": ["K09_Binance_CausalJoin_v1", "K09_Binance_USDM_Funding_v1", "K09_Binance_USDM_OI_v1"],
        "validation": {"bars": bar_validation, "funding": funding_validation, "open_interest": oi_validation},
        "assets": sorted({row["instrument"] for row in bars}),
        "bar_kinds": sorted(set(effective_bar_kinds)),
        "raw_bar_kinds": bar_kinds,
        "identity_status": identity_status,
        "join_status": "COMMON_PILOT_ROWS_PRESENT_FUNDING_AFTER_CUTOFF" if raw_join_rows and not causal_join_rows else "NO_CAUSAL_JOIN_ROWS",
        "raw_join_rows": raw_join_rows,
        "causal_join_rows_by_cutoff": causal_join_rows,
        "archive_publication_bounds": sorted({value.isoformat().replace("+00:00", "Z") for value in archive_available}),
        "archive_rows_available_by_cutoff": within_cutoff,
        "pit_status": "PIT_PENDING",
        "economic_signal_rows": 0,
        "signal_use": "spot-only long/cash observation; no derivative execution or funding P&L",
        "funding_time_semantics": "event_time_utc is realized funding interval time; publication/announcement time unknown and never inferred",
    }


def k11_signed_flow(path: Path = K11) -> dict[str, object]:
    rows = read_rows(path)
    validation = _validate_binance(rows, ("venue", "instrument", "agg_trade_id"), schema="trades")
    import sys

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from scripts.research.k11_signed_flow_candidate_2026_09_08 import (
        load_archive_expectation,
        validate_archive_expectation,
    )

    try:
        expectation_summary = validate_archive_expectation(rows, load_archive_expectation())
        expectation_status = "EXPECTED_ARCHIVE_SET_MATCH"
        expectation_error = None
    except (KeyError, TypeError, ValueError) as exc:
        expectation_summary = None
        expectation_status = "EXPECTED_ARCHIVE_SET_BLOCKED"
        expectation_error = str(exc)
    hourly: dict[tuple[str, str, datetime], dict[str, float]] = defaultdict(lambda: {"signed_notional": 0.0, "buy_notional": 0.0, "sell_notional": 0.0, "trades": 0.0})
    for row in rows:
        event = utc(row["event_time_utc"])
        bucket = event.replace(minute=0, second=0, microsecond=0)
        price = float(row["price"])
        quantity = float(row["quantity"])
        if not math.isfinite(price) or price <= 0 or not math.isfinite(quantity) or quantity <= 0:
            raise ValueError(f"invalid_trade:{row['agg_trade_id']}")
        notional = price * quantity
        key = (row["venue"], row["instrument"], bucket)
        if row["is_buyer_maker"].strip().lower() == "true":
            hourly[key]["signed_notional"] -= notional
            hourly[key]["sell_notional"] += notional
        elif row["is_buyer_maker"].strip().lower() == "false":
            hourly[key]["signed_notional"] += notional
            hourly[key]["buy_notional"] += notional
        else:
            raise ValueError(f"invalid_is_buyer_maker:{row['agg_trade_id']}")
        hourly[key]["trades"] += 1
    preview = [
        {"venue": venue, "instrument": instrument, "event_time_utc": event.isoformat().replace("+00:00", "Z"), **values}
        for (venue, instrument, event), values in sorted(hourly.items())
    ]
    archive_times = sorted({archive_available_at(row["source_file"]) for row in rows if archive_available_at(row["source_file"]) is not None})
    causal_available = archive_times[-1] if len(archive_times) == 1 else None
    effective_available: list[datetime | None] = []
    explicit_late = 0
    explicit_missing = 0
    for row in rows:
        raw_available = row.get("available_at", "").strip()
        if raw_available:
            available = utc(raw_available)
            if available > CUTOFF:
                explicit_late += 1
        else:
            explicit_missing += 1
            available = None
        effective_available.append(available)
    causal_rows = (
        len(rows)
        if expectation_summary and effective_available and all(value is not None and value <= CUTOFF for value in effective_available)
        else 0
    )
    if causal_rows == len(rows) and not explicit_late and not explicit_missing:
        pit_status = "CAUSAL_EXPLICIT_AVAILABLE"
    elif expectation_summary and causal_available is not None and causal_available <= CUTOFF and not explicit_late:
        pit_status = "ARCHIVE_EVIDENCE_ONLY"
    else:
        pit_status = "PIT_PENDING"
    return {
        "card": "K11",
        "recipe_id": "K11_Binance_AggTrades_Signed_v1",
        "formula": "price*quantity*(+1 when is_buyer_maker=false else -1)",
        "validation": validation,
        "hourly_rows": len(preview),
        "preview": preview[:2],
        "pit_status": pit_status,
        "archive_publication_bound": causal_available.isoformat().replace("+00:00", "Z") if causal_available else None,
        "archive_evidence_available_at": causal_available.isoformat().replace("+00:00", "Z") if causal_available else None,
        "archive_evidence_basis": "receipt response_headers.last-modified lower bound; provenance only, never row availability",
        "causal_trade_rows": causal_rows,
        "economic_signal_rows": 0,
        "depth_imbalance_inferred": False,
        "expected_archive_set_status": expectation_status,
        "expected_archive_set": expectation_summary,
        "expected_archive_set_error": expectation_error,
        "explicit_availability_rows_after_cutoff": explicit_late,
        "explicit_availability_rows_missing": explicit_missing,
    }


def build_report() -> dict[str, object]:
    k11_rows = read_rows(K11)
    k11_events = [utc(row["event_time_utc"]) for row in k11_rows]
    k11_native_history = {
        "frequency": "aggregate trades; signal buckets UTC hour",
        "instruments": sorted({row["instrument"] for row in k11_rows}),
        "event_start": min(k11_events).isoformat().replace("+00:00", "Z"),
        "event_end": max(k11_events).isoformat().replace("+00:00", "Z"),
        "causal_pilot_days": len({event.date().isoformat() for event in k11_events}),
        "full_recent_economic_ready": False,
    }
    cards = {"K01": k01_mvrv(), "K03": k03_fee_growth(), "K04": k04_supply_audit(), "K09": k09_archive_join(), "K11": k11_signed_flow()}
    cards["K11"]["native_history"] = k11_native_history
    return {
        "status": "NORMALIZED_SCHEMA_TESTED",
        "economic_run": False,
        "historical_cutoff": CUTOFF.isoformat().replace("+00:00", "Z"),
        "cards": cards,
        "source": {"adapter_sha256": sha256(Path(__file__)), "protocol_sha256": sha256(PROTOCOL)},
        "candidate": {
            "k11_brief_sha256": sha256(K11_BRIEF),
            "k11_adapter_sha256": sha256(K11_CANDIDATE),
            "k11_expectation_sha256": sha256(K11_EXPECTATION),
        },
        "constraints": {"no_runtime_mutation": True, "no_paper_live": True, "no_auto_promotion": True, "post_cutoff_quarantined": True},
    }


def freeze_manifest(path: Path = MANIFEST) -> dict[str, object]:
    input_paths = [K01, K03, K04, K09_BARS, K09_FUNDING, K09_OI, K11]
    archive_paths = {
        DATA_ROOT / row["source_file"]
        for source in (K09_BARS, K09_FUNDING, K09_OI, K11)
        for row in read_rows(source)
    }
    archive_paths.update(path.with_name(f"{path.name}.receipt.json") for path in tuple(archive_paths))
    archive_paths = {path for path in archive_paths if path.is_file()}
    payload: dict[str, object] = {
        "status": "FROZEN_ADAPTER_PROTOCOL",
        "identity": "crypto_queue_causal_adapters_2026_09_08",
        "frozen_at_utc": "2026-09-08T00:00:00Z",
        "historical_cutoff": CUTOFF.isoformat().replace("+00:00", "Z"),
        "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": sha256(PROTOCOL)},
        "adapter": {"path": str(Path(__file__).relative_to(REPO)), "sha256": sha256(Path(__file__))},
        "k11_expectation": {
            "path": str(K11_EXPECTATION.relative_to(REPO)),
            "sha256": sha256(K11_EXPECTATION),
            "status": "FROZEN_EXPECTED_ARCHIVE_SET",
        },
        "artifacts": {
            str(item.relative_to(REPO)): {"sha256": sha256(item), "kind": "first_class_artifact"}
            for item in (PROTOCOL, Path(__file__), K11_BRIEF, K11_CANDIDATE, ADAPTER_TEST, K11_TEST, K11_EXPECTATION, REPORT)
        },
        "candidate": {
            "brief": {"path": str(K11_BRIEF.relative_to(REPO)), "sha256": sha256(K11_BRIEF)},
            "adapter": {"path": str(K11_CANDIDATE.relative_to(REPO)), "sha256": sha256(K11_CANDIDATE)},
            "test": {"path": str(K11_TEST.relative_to(REPO)), "sha256": sha256(K11_TEST)},
            "identity": "crypto_k11_signed_flow_24h_v1",
            "direction": "positive signed imbalance => long spot; otherwise cash",
            "lookback_hours": 24,
            "native_history_status": "CAUSAL_PILOT_ONLY",
        },
        "inputs": {str(item.relative_to(REPO)): {"sha256": sha256(item)} for item in input_paths},
        "official_archive_evidence": {
            str(item.relative_to(REPO)): {"sha256": sha256(item)} for item in sorted(archive_paths)
        },
        "pit_policy": "available_at must be present and <= decision_time; supplied samples retain null and remain PIT_PENDING",
        "archive_availability_policy": "Binance receipt response_headers.last-modified is immutable archive evidence only; it never substitutes for explicit row available_at",
        "economic_run": False,
        "no_auto_promotion": True,
        "source_urls": ["https://data.binance.vision/", "https://community-api.coinmetrics.io/"],
        "manifest_sha256": "PENDING_FREEZE",
        "canonical_hash_policy": "canonical JSON hash excludes only manifest_sha256; every other field, including raw artifact SHA-256 declarations, is hashed",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def verify_frozen_manifest(path: Path = MANIFEST) -> dict[str, object]:
    """Recheck every declared input, receipt, archive ZIP, and manifest hash."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    checked_files = 0
    for section in ("inputs", "official_archive_evidence"):
        for name, declared in payload[section].items():
            item = REPO / name
            if sha256(item) != declared["sha256"]:
                raise ValueError(f"manifest_hash_mismatch:{name}")
            checked_files += 1
            if item.suffix == ".zip":
                with ZipFile(item) as archive:
                    if archive.testzip() is not None:
                        raise ValueError(f"manifest_zip_integrity_failure:{name}")
    for name, declared in payload["artifacts"].items():
        item = REPO / name
        if sha256(item) != declared["sha256"]:
            raise ValueError(f"artifact_hash_mismatch:{name}")
        checked_files += 1
    expectation = payload["k11_expectation"]
    if sha256(REPO / expectation["path"]) != expectation["sha256"]:
        raise ValueError("manifest_hash_mismatch:k11_expectation")
    expectation_payload = json.loads((REPO / expectation["path"]).read_text(encoding="utf-8"))
    archive_decl = expectation_payload["archive"]
    receipt_path = REPO / archive_decl["receipt"]
    archive_path = REPO / archive_decl["path"]
    if not receipt_path.is_file():
        raise ValueError("expectation_receipt_missing")
    if sha256(receipt_path) != archive_decl["receipt_sha256"]:
        raise ValueError("expectation_receipt_sha256_mismatch")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("sha256") != archive_decl["sha256"] or receipt.get("status") != 200:
        raise ValueError("expectation_receipt_archive_mismatch")
    if receipt.get("endpoint") != archive_decl["endpoint"] or receipt["endpoint"].rsplit("/", 1)[-1] != archive_path.name:
        raise ValueError("expectation_receipt_endpoint_mismatch")
    if sha256(archive_path) != archive_decl["sha256"]:
        raise ValueError("expectation_archive_sha256_mismatch")
    with ZipFile(archive_path) as archive:
        members = [member for member in archive.infolist() if member.filename == archive_decl["member"]]
        if len(members) != 1 or members[0].CRC != archive_decl["member_crc32"] or members[0].file_size != archive_decl["member_size_bytes"]:
            raise ValueError("expectation_archive_member_mismatch")
    canonical_payload = dict(payload)
    canonical_payload["manifest_sha256"] = "PENDING_FREEZE"
    canonical = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != payload["manifest_sha256"]:
        raise ValueError("manifest_canonical_hash_mismatch")
    return {
        "status": "FROZEN_MANIFEST_VERIFIED",
        "checked_declared_files": checked_files,
        "checked_zip_integrity": sum(1 for name in payload["official_archive_evidence"] if name.endswith(".zip")),
        "checked_expectation_contract": True,
        "checked_first_class_artifacts": len(payload["artifacts"]),
        "manifest_sha256": payload["manifest_sha256"],
    }


def verify() -> dict[str, object]:
    """Verify the frozen adapter package without rebuilding or writing it."""
    result = verify_frozen_manifest()
    saved_report = json.loads(REPORT.read_text(encoding="utf-8"))
    if saved_report != build_report():
        raise ValueError("saved_report_canonical_mismatch")
    return {**result, "report_sha256": sha256(REPORT), "read_only": True}


def main() -> None:
    if "--verify" in sys.argv[1:]:
        print(json.dumps(verify(), sort_keys=True))
        return
    report = build_report()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    manifest = freeze_manifest()
    print(json.dumps({"status": report["status"], "report": str(REPORT), "manifest": str(MANIFEST), "manifest_sha256": manifest["manifest_sha256"]}))


if __name__ == "__main__":
    main()
