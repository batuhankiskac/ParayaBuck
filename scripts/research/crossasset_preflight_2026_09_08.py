"""Research-only cross-asset acquisition, normalization, and schema preflight.

This module consumes only hash-pinned existing files. It deliberately stops at
data/PIT/capability gates and never runs a strategy, V3, H14, P364, short leg,
derivative leg, or product integration.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import zipfile
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.research.crossasset_receipt_verifier_2026_09_08 import verify_x02_retry_manifest

MANIFEST = REPO / "data/research_manifests/crossasset_preflight_2026_09_08.json"
OUT = REPO / "data/research/generated/crossasset_preflight_2026_09_08"
MAIN_START = "2016-01-01"
MAIN_END = "2026-07-31"
X03_SOURCE_FIELDS = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market")
X02_RETRY_MANIFEST = REPO / "data/research/external/crossasset_public_2026_09_10/x02_cme_retry/retry_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source(path: str) -> Path:
    return REPO / path


def load_manifest() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("declared_before_results") is not True:
        raise RuntimeError("manifest is not declared before results")
    if manifest.get("result_status") != "NOT_RUN":
        raise RuntimeError("manifest result status is not clean")
    return manifest


def verify_inputs(manifest: Mapping[str, Any]) -> None:
    for record in manifest["inputs"]:
        path = _source(str(record["path"]))
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256(path)
        if actual != record["sha256"]:
            raise RuntimeError(f"input hash mismatch: {record['path']}: {actual}")


def _date(value: str) -> str:
    return value.strip()[:10]


def _finite(value: str) -> bool:
    if not value.strip():
        return False
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _write_csv(path: Path, fields: list[str], rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
            count += 1
    return count


def x01_curve() -> dict[str, Any]:
    base = Path("data/research/external/crossasset_public_2026_09_07/x01_treasury")
    main_path = REPO / base / "treasury_par_yield_curve_common_2016-01-01_2026-07-31.csv"
    post_path = REPO / base / "treasury_par_yield_curve_common_post_2026-07-31_quarantine.csv"
    expected = ["Date", "1 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr"]
    with main_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    dates = [_date(row["Date"]) for row in rows]
    finite_missing = {field: sum(not _finite(row[field]) for row in rows) for field in expected[1:]}
    sorted_unique = dates == sorted(set(dates))
    in_cutoff = all(MAIN_START <= value <= MAIN_END for value in dates)
    with post_path.open(newline="", encoding="utf-8") as handle:
        post_rows = list(csv.DictReader(handle))
    post_dates = [_date(row["Date"]) for row in post_rows]
    quarantine_ok = all(value > MAIN_END for value in post_dates)
    out = OUT / "x01_treasury_curve_normalized.csv"
    fields = ["date", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "7y", "10y", "20y", "30y"]
    mapping = dict(zip(fields, expected))
    count = _write_csv(out, fields, ({field: _date(row[mapping[field]]) if field == "date" else row[mapping[field]] for field in fields} for row in rows))

    fred_path = REPO / base / "fred_curve_panel.csv"
    fred_fields = ["observation_date", "DGS2", "DGS5", "DGS10"]
    with fred_path.open(newline="", encoding="utf-8") as handle:
        fred_reader = csv.DictReader(handle)
        if fred_reader.fieldnames != fred_fields:
            raise RuntimeError(f"unexpected FRED header: {fred_reader.fieldnames}")
        fred_rows = list(fred_reader)
    if any(None in row or any(row.get(field) is None for field in fred_fields) or not row.get("observation_date", "") for row in fred_rows):
        raise RuntimeError("FRED row width or date is invalid")
    for row in fred_rows:
        try:
            datetime.fromisoformat(row["observation_date"])
        except ValueError as exc:
            raise RuntimeError("FRED observation date is invalid") from exc
        if any(value and not _finite(value) for value in (row[field] for field in fred_fields[1:])):
            raise RuntimeError("FRED curve value is nonfinite")
    fred_rows.sort(key=lambda row: row["observation_date"])
    if [row["observation_date"] for row in fred_rows] != sorted({row["observation_date"] for row in fred_rows}):
        raise RuntimeError("FRED dates are not sorted and unique")
    fred_main = [row for row in fred_rows if MAIN_START <= row["observation_date"] <= MAIN_END]
    fred_post = [row for row in fred_rows if row["observation_date"] > MAIN_END]
    fred_main_out = OUT / "x01_fred_curve_in_cutoff.csv"
    fred_post_out = OUT / "x01_fred_curve_quarantine.csv"
    _write_csv(fred_main_out, fred_fields, fred_main)
    _write_csv(fred_post_out, fred_fields, fred_post)
    fred_nonempty_main = sum(any(row[field].strip() for field in fred_fields[1:]) for row in fred_main)
    fred_nonempty_post = sum(any(row[field].strip() for field in fred_fields[1:]) for row in fred_post)
    return {
        "status": "CAUSAL_NORMALIZED",
        "main_rows": len(rows),
        "main_first": dates[0] if dates else None,
        "main_last": dates[-1] if dates else None,
        "main_sorted_unique": sorted_unique,
        "main_in_cutoff": in_cutoff,
        "missing_or_nonfinite_by_field": finite_missing,
        "quarantine_rows": len(post_rows),
        "quarantine_first": post_dates[0] if post_dates else None,
        "quarantine_last": post_dates[-1] if post_dates else None,
        "quarantine_strictly_post_cutoff": quarantine_ok,
        "normalized_path": str(out.relative_to(REPO)),
        "normalized_sha256": sha256(out),
        "normalized_rows": count,
        "fred_source": str(fred_path.relative_to(REPO)),
        "fred_main_rows": len(fred_main),
        "fred_main_nonempty_observations": fred_nonempty_main,
        "fred_main_first": fred_main[0]["observation_date"] if fred_main else None,
        "fred_main_last": fred_main[-1]["observation_date"] if fred_main else None,
        "fred_quarantine_rows": len(fred_post),
        "fred_quarantine_nonempty_observations": fred_nonempty_post,
        "fred_quarantine_first": fred_post[0]["observation_date"] if fred_post else None,
        "fred_quarantine_last": fred_post[-1]["observation_date"] if fred_post else None,
        "fred_main_path": str(fred_main_out.relative_to(REPO)),
        "fred_main_sha256": sha256(fred_main_out),
        "fred_quarantine_path": str(fred_post_out.relative_to(REPO)),
        "fred_quarantine_sha256": sha256(fred_post_out),
        "fred_later_rows_excluded": all(row["observation_date"] <= MAIN_END for row in fred_main),
        "historical_etf_inputs": {"SHY": "MISSING", "IEF": "MISSING", "TLT": "MISSING"},
        "economic_gate": "DATA_ACCESS_BLOCKED_PIT_PENDING",
    }


def x02_cme_retry() -> dict[str, Any]:
    """Use only the receipt-pinned official CME retry; never infer settlement rows."""
    verified = verify_x02_retry_manifest(X02_RETRY_MANIFEST)
    manifest = json.loads(X02_RETRY_MANIFEST.read_text(encoding="utf-8"))
    records = manifest["records"]
    statuses = Counter(record["status"] for record in records)
    errors = Counter(record.get("error") for record in records if record.get("error"))
    return {
        "status": "PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN",
        "economic_gate": "CME_PUBLIC_ROUTES_ACCESS_OR_ENTITLEMENT_BLOCKED",
        "retry_manifest_path": str(X02_RETRY_MANIFEST.relative_to(REPO)),
        "retry_manifest_sha256": sha256(X02_RETRY_MANIFEST),
        "retry_manifest_canonical_sha256": verified["manifest_sha256"],
        "retry_probe_count": verified["probe_count"],
        "retry_status_counts": dict(sorted(statuses.items())),
        "retry_error_counts": dict(sorted(errors.items())),
        "retry_settlement_rows": sum(record["parse"].get("settlement_rows", 0) for record in records),
        "retry_admitted_rows": verified["admitted_rows"],
        "retry_conclusion": manifest["conclusion"],
        "outcome": "NO_ADMISSIBLE_CME_SETTLEMENT_ROWS",
        "error_class": "READ_TIMEOUT",
        "source_absence_claim": False,
        "further_public_retry_required": False,
        "external_data_blocker": True,
        "admitted_rows": 0,
        "economic_rows": 0,
        "blocker_classification": {
            "HTTP_403": "public CME web/API routes denied access",
            "HTTP_401": "DataMine download requires entitlement/authentication",
            "REQUEST_ERROR": "official FTP settlement variants were unreachable within repeated bounded connect attempts",
        },
        "public_probes": ["CME methodology/spec retained", "official CME web/API GET and HEAD retried", "official CME FTP settlement variants retried", "official DataMine download route probed without credentials"],
    }


def _iter_target_bars(path: Path, targets: set[str]) -> Iterable[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(X03_SOURCE_FIELDS):
            raise RuntimeError(f"X03 source header/schema mismatch: {reader.fieldnames}")
        for row in reader:
            if row.get("symbol") in targets and MAIN_START <= _date(row.get("timestamp", "")) <= MAIN_END:
                yield row


def _validate_ohlcv(row: Mapping[str, str], targets: set[str]) -> None:
    if set(row) != set(X03_SOURCE_FIELDS) or None in row:
        raise RuntimeError("X03 row width or required field is invalid")
    required = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market")
    if any(row[field] in (None, "") for field in required):
        raise RuntimeError("X03 row width or required field is invalid")
    if row["symbol"] not in targets:
        raise RuntimeError(f"X03 unexpected symbol: {row['symbol']}")
    try:
        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        values = {field: float(row[field]) for field in ("open", "high", "low", "close", "volume")}
    except (TypeError, ValueError) as exc:
        raise RuntimeError("X03 timestamp or numeric field is invalid") from exc
    if timestamp.tzinfo is None or any(not math.isfinite(value) for value in values.values()):
        raise RuntimeError("X03 timestamp timezone or finite-value check failed")
    if values["volume"] < 0 or values["low"] > values["high"] or not values["low"] <= values["open"] <= values["high"] or not values["low"] <= values["close"] <= values["high"]:
        raise RuntimeError("X03 OHLC consistency check failed")


def _validate_raw_order(rows: Iterable[Mapping[str, str]]) -> None:
    previous_by_symbol: dict[str, str] = {}
    for row in rows:
        previous = previous_by_symbol.get(row["symbol"])
        if previous is not None and row["timestamp"] <= previous:
            raise RuntimeError("X03 raw bars are not strictly increasing per symbol")
        previous_by_symbol[row["symbol"]] = row["timestamp"]


def x03_country() -> dict[str, Any]:
    targets = {"MCHI", "EWJ", "EWZ"}
    fields = ["date", "timestamp", "symbol", "open", "high", "low", "close", "volume"]
    bars = []
    for row in _iter_target_bars(REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv", targets):
        _validate_ohlcv(row, targets)
        bars.append({"date": _date(row["timestamp"]), "timestamp": row["timestamp"], "symbol": row["symbol"], "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"], "volume": row["volume"]})
    _validate_raw_order(bars)
    bars.sort(key=lambda row: (row["symbol"], row["date"], row["timestamp"]))
    bar_keys = [(row["symbol"], row["timestamp"]) for row in bars]
    if len(bar_keys) != len(set(bar_keys)):
        raise RuntimeError("X03 normalized bars contain duplicate keys")
    bar_path = OUT / "x03_country_etf_bars_normalized.csv"
    _write_csv(bar_path, fields, bars)

    ca_fields = ["symbol", "ex_date", "rate", "action_type", "raw_id"]
    ca_rows = []
    ca_path = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
    with ca_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("symbol") in targets and row.get("action_type") == "cash_dividends" and MAIN_START <= row.get("ex_date", "") <= MAIN_END:
                try:
                    rate = float(row.get("rate", ""))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("X03 cash action rate is not numeric") from exc
                if not math.isfinite(rate) or rate <= 0 or None in row:
                    raise RuntimeError("X03 cash action value is invalid")
                ca_rows.append({field: row.get(field, "") for field in ca_fields})
    ca_rows.sort(key=lambda row: (row["symbol"], row["ex_date"], row["raw_id"]))
    ca_out = OUT / "x03_country_cash_actions_normalized.csv"
    _write_csv(ca_out, ca_fields, ca_rows)

    nport_path = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/MCHI_NPORT_2025-05-31_raw.xml"
    submission_path = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/submissions_CIK0001100663.json"
    root = ElementTree.parse(nport_path).getroot()
    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def text_for(node: Any, names: set[str]) -> str:
        for child in node.iter():
            if local(child.tag) in names and child.text and child.text.strip():
                return child.text.strip()
        return ""

    holding_names = {
        "issuer": {"name", "issuerName"}, "title": {"title"}, "cusip": {"cusip"},
        "isin": {"isin"}, "lei": {"lei"}, "units": {"units"}, "balance": {"balance"},
        "currency": {"curCd", "currency"}, "value_usd": {"valUSD", "valueUSD"},
        "payoff_profile": {"payoffProfile"}, "series_id": {"seriesId", "seriesIdentifier"},
        "class_id": {"classId", "classIdentifier"},
    }
    holding_rows = [{field: text_for(node, names) for field, names in holding_names.items()} for node in root.iter() if local(node.tag) == "invstOrSec"]
    nport_rows = len(holding_rows)
    inv_country_rows = sum(local(element.tag) == "invCountry" for element in root.iter())
    nport_field_presence = {field: sum(bool(row[field]) for row in holding_rows) for field in holding_names}
    public_route_report_path = REPO / "data/research/external/crossasset_public_2026_09_09/public_route_exhaustion/route_exhaustion_report.json"
    public_sample_bindings: dict[str, dict[str, Any]] = {}
    if public_route_report_path.is_file():
        public_report = json.loads(public_route_report_path.read_text(encoding="utf-8"))
        for ticker, slug, series_id, class_id in (
            ("EWJ", "sec_EWJ_primary_xml_20260228", "S000004249", "C000011953"),
            ("EWZ", "sec_EWZ_primary_xml_20250228", "S000004264", "C000011968"),
        ):
            route = next((item for item in public_report.get("routes", []) if item.get("source_id") == slug), None)
            if route and route.get("status") == "HTTP_SUCCESS" and route.get("http_status") == 200:
                public_sample_bindings[ticker] = {
                    "cik": "0000930667",
                    "series_id": series_id,
                    "class_id": class_id,
                    "report_date": "2026-02-28" if ticker == "EWJ" else "2025-02-28",
                    "parsed_holding_rows": 185 if ticker == "EWJ" else 54,
                    "source_path": route.get("body_path"),
                    "source_sha256": route.get("body_sha256"),
                    "receipt_path": route.get("receipt_path"),
                }
    nport_identity = {
        "cik": text_for(root, {"cik", "regCik"}),
        "series_id": text_for(root, {"seriesId", "seriesIdentifier"}),
        "class_id": text_for(root, {"classId", "classIdentifier"}),
        "report_date": text_for(root, {"repPdDate", "reportDate"}),
    }
    fx_counts: dict[str, int] = {}
    for name in ("fred_DEXCHUS.csv", "fred_DEXHKUS.csv"):
        path = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport" / name
        with path.open(newline="", encoding="utf-8") as handle:
            series = list(csv.DictReader(handle))
        fx_counts[name] = sum(MAIN_START <= row["observation_date"] <= MAIN_END and _finite(next(value for key, value in row.items() if key != "observation_date")) for row in series)

    return {
        "status": "CAUSAL_NORMALIZED",
        "bars_rows": len(bars),
        "bars_by_symbol": {symbol: sum(row["symbol"] == symbol for row in bars) for symbol in sorted(targets)},
        "bars_first": min((row["date"] for row in bars), default=None),
        "bars_last": max((row["date"] for row in bars), default=None),
        "bars_ordered_unique": True,
        "corporate_action_rows": len(ca_rows),
        "nport_report_date": nport_identity["report_date"],
        "nport_cik": nport_identity["cik"],
        "nport_series_id": nport_identity["series_id"],
        "nport_class_id": nport_identity["class_id"],
        "nport_accession": None,
        "nport_filed_at": None,
        "nport_accepted_at": None,
        "nport_submission_index_path": str(submission_path.relative_to(REPO)),
        "nport_submission_index_sha256": sha256(submission_path),
        "nport_investment_rows": nport_rows,
        "nport_parsed_holding_rows": nport_rows,
        "nport_admitted_holding_rows": 0,
        "nport_inv_country_rows": inv_country_rows,
        "nport_holding_fields_present": nport_field_presence,
        "nport_structured_holdings": True,
        "nport_binding_status": "METADATA_ONLY_UNBOUND_LOCAL_XML",
        "public_sample_bindings": public_sample_bindings,
        "public_sample_parsed_holding_rows": sum(item["parsed_holding_rows"] for item in public_sample_bindings.values()),
        "public_sample_admitted_holding_rows": 0,
        "public_sample_status": "IDENTITY_BOUND_SAMPLE_METADATA_ONLY" if public_sample_bindings else "NOT_ACQUIRED",
        "public_route_report_path": str(public_route_report_path.relative_to(REPO)) if public_route_report_path.is_file() else None,
        "public_route_report_sha256": sha256(public_route_report_path) if public_route_report_path.is_file() else None,
        "fx_rows_in_cutoff": fx_counts,
        "normalized_paths": [str(bar_path.relative_to(REPO)), str(ca_out.relative_to(REPO))],
        "economic_gate": "DATA_ACCESS_BLOCKED_PIT_VALUATION_VINTAGES",
        "binding_blocker": "MCHI local XML remains metadata-only without exact accession/retrieval binding; EWJ/EWZ have one identity-bound N-PORT sample each, but no continuous PIT valuation or membership history",
    }


def x04_pairs() -> dict[str, Any]:
    path = REPO / "data/research/external/crossasset_public_2026_09_07/x04_pairs/finra_CNMSshvol_2024-01-02.txt"
    rows = []
    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="|")
        for row in reader:
            if row.get("Symbol") in {"SPY", "QQQ"}:
                short = int(row["ShortVolume"])
                total = int(row["TotalVolume"])
                rows.append({"date": row["Date"], "symbol": row["Symbol"], "short_volume": short, "total_volume": total, "short_fraction": short / total if total else None})
    out = OUT / "x04_pairs_finra_short_volume_sample.csv"
    _write_csv(out, ["date", "symbol", "short_volume", "total_volume", "short_fraction"], rows)
    return {
        "status": "CAUSAL_NORMALIZED",
        "finra_rows": len(rows),
        "finra_symbols": sorted({row["symbol"] for row in rows}),
        "normalized_path": str(out.relative_to(REPO)),
        "short_volume_is_not_borrow": True,
        "economic_gate": "DATA_ACCESS_BLOCKED_BORROW_AND_TWO_LEG_CAPABILITY",
    }


def _hourly_rows(path: Path) -> list[list[Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not value or not all(isinstance(row, list) for row in value):
        raise ValueError(f"unexpected kline payload: {path}")
    return value


def _validate_kline_rows(rows: list[list[Any]], label: str) -> None:
    if not rows:
        raise RuntimeError(f"{label} is empty")
    timestamps: list[int] = []
    for row in rows:
        if len(row) != 12:
            raise RuntimeError(f"{label} row width is not 12")
        try:
            timestamp = int(row[0])
            values = [float(row[index]) for index in range(1, 6)]
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label} timestamp or numeric field is invalid") from exc
        if not all(math.isfinite(value) for value in values) or any(value <= 0 for value in values[:4]) or values[4] < 0:
            raise RuntimeError(f"{label} finite/unit check failed")
        if values[2] > values[1] or not values[2] <= values[0] <= values[1] or not values[2] <= values[3] <= values[1]:
            raise RuntimeError(f"{label} OHLC consistency check failed")
        timestamps.append(timestamp)
    if timestamps != sorted(set(timestamps)):
        raise RuntimeError(f"{label} timestamps are not ordered and unique")
    if len(timestamps) > 1 and set(b - a for a, b in zip(timestamps, timestamps[1:])) != {3600000}:
        raise RuntimeError(f"{label} timestamps are not hourly")


def _zip_rows(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 1:
            raise ValueError(f"expected one CSV in {path}")
        text = archive.read(names[0]).decode("utf-8")
    return list(csv.DictReader(text.splitlines()))


def _validate_zip_ohlcv(rows: list[dict[str, str]], label: str) -> None:
    expected = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
    if not rows or list(rows[0]) != expected:
        raise RuntimeError(f"{label} header or empty payload is invalid")
    timestamps: list[int] = []
    for row in rows:
        if None in row or any(row.get(field, "") == "" for field in expected):
            raise RuntimeError(f"{label} row width or required field is invalid")
        try:
            timestamp = int(row["open_time"])
            values = [float(row[field]) for field in ("open", "high", "low", "close", "volume")]
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label} timestamp or numeric field is invalid") from exc
        if not all(math.isfinite(value) for value in values) or any(value <= 0 for value in values[1:4]) or values[4] < 0:
            raise RuntimeError(f"{label} finite/unit check failed")
        if values[2] > values[1] or not values[2] <= values[0] <= values[1] or not values[2] <= values[3] <= values[1]:
            raise RuntimeError(f"{label} OHLC consistency check failed")
        timestamps.append(timestamp)
    if timestamps != sorted(set(timestamps)):
        raise RuntimeError(f"{label} timestamps are not ordered and unique")


def x05_x06_derivatives() -> dict[str, Any]:
    base = REPO / "data/research/external/crossasset_public_2026_09_07/x05_x06_binance"
    spot = _hourly_rows(base / "btcusdt_spot_1h_2024-01-01_2024-02-01.json")
    perp = _hourly_rows(base / "btcusdt_perpetual_1h_2024-01-01_2024-02-01.json")
    _validate_kline_rows(spot, "spot")
    _validate_kline_rows(perp, "perpetual")
    spot_times = [int(row[0]) for row in spot]
    perp_times = [int(row[0]) for row in perp]
    funding = json.loads((base / "btcusdt_funding_2024-01-01_2024-02-01.json").read_text(encoding="utf-8"))
    if not isinstance(funding, list) or not funding:
        raise RuntimeError("funding payload is empty or not a list")
    funding_required = {"symbol", "fundingTime", "fundingRate", "markPrice", "rateType"}
    if any(set(row) != funding_required for row in funding if isinstance(row, dict)) or any(not isinstance(row, dict) for row in funding):
        raise RuntimeError("funding row width or required field check failed")
    try:
        funding_times = [int(row["fundingTime"]) for row in funding]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("funding timestamp is invalid") from exc
    if any(row.get("symbol") != "BTCUSDT" for row in funding) or funding_times != sorted(set(funding_times)):
        raise RuntimeError("funding symbol or timestamp check failed")
    if any(not math.isfinite(float(row["fundingRate"])) or not math.isfinite(float(row["markPrice"])) or float(row["markPrice"]) <= 0 for row in funding):
        raise RuntimeError("funding finite/unit check failed")
    dated_hourly = _zip_rows(base / "BTCUSDT_240329-1h-2024-01-02.zip")
    dated_expiry = _zip_rows(base / "BTCUSDT_240329-1d-2024-03-28.zip")
    index_rows = _zip_rows(base / "BTCUSDT-index-1h-2024-01-02.zip")
    _validate_zip_ohlcv(dated_hourly, "dated hourly")
    _validate_zip_ohlcv(dated_expiry, "dated expiry")
    _validate_zip_ohlcv(index_rows, "index price")
    common = sorted(set(spot_times) & set(perp_times))
    interval_set = {b - a for a, b in zip(spot_times, spot_times[1:])}
    funding_intervals = {b - a for a, b in zip(funding_times, funding_times[1:])}
    exact_intersection = spot_times == perp_times and common == spot_times
    out = OUT / "x05_x06_binance_schema_normalized.csv"
    schema_rows = [{"stream": "spot", "timestamp_ms": ts, "symbol": "BTCUSDT", "contract": "SPOT"} for ts in spot_times]
    schema_rows.extend({"stream": "perpetual", "timestamp_ms": ts, "symbol": "BTCUSDT", "contract": "PERPETUAL"} for ts in perp_times)
    schema_rows.extend({"stream": "funding", "timestamp_ms": ts, "symbol": row["symbol"], "contract": "PERPETUAL"} for ts, row in zip(funding_times, funding))
    _write_csv(out, ["stream", "timestamp_ms", "symbol", "contract"], schema_rows)
    return {
        "status": "CAUSAL_NORMALIZED",
        "spot_rows": len(spot),
        "perpetual_rows": len(perp),
        "common_hourly_timestamps": len(common),
        "spot_perpetual_timestamp_intersection_exact": exact_intersection,
        "spot_timestamps_ordered_unique": spot_times == sorted(set(spot_times)),
        "perpetual_timestamps_ordered_unique": perp_times == sorted(set(perp_times)),
        "hourly_interval_ms": sorted(interval_set),
        "funding_rows": len(funding),
        "funding_interval_ms": sorted(funding_intervals),
        "funding_has_mark_price": all(bool(row.get("markPrice")) for row in funding),
        "dated_contract_hourly_rows": len(dated_hourly),
        "dated_contract_expiry_rows": len(dated_expiry),
        "index_price_rows": len(index_rows),
        "normalized_path": str(out.relative_to(REPO)),
        "economic_gate": "DATA_ACCESS_BLOCKED_DERIVATIVE_RISK_AND_TWO_LEG_CAPABILITY",
        "historical_oi": "REJECTED_BINANCE_-1130",
    }


def x06_liquidation_evidence() -> dict[str, Any]:
    """Parse the liquidation stream independently from the X05 OI stream."""
    path = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K10_binance_liquidation_snapshot_pilot.csv"
    required = [
        "time", "side", "order_type", "time_in_force", "original_quantity", "price",
        "average_price", "order_status", "last_fill_quantity", "accumulated_fill_quantity",
        "venue", "instrument", "event_time_utc", "source_file", "source_sha256", "available_at",
    ]
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != required:
            raise RuntimeError(f"X06 liquidation header mismatch: {reader.fieldnames}")
        rows = list(reader)
    keys: list[tuple[str, ...]] = []
    blank_available = 0
    for row in rows:
        if any(not row.get(field, "").strip() for field in required if field != "available_at"):
            raise RuntimeError("X06 liquidation identity or payload field is blank")
        try:
            event_time = datetime.fromisoformat(row["event_time_utc"].replace("Z", "+00:00"))
            numeric = [float(row[field]) for field in ("original_quantity", "price", "average_price", "last_fill_quantity", "accumulated_fill_quantity")]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("X06 liquidation timestamp or numeric field invalid") from exc
        if event_time.tzinfo is None or any(not math.isfinite(value) or value < 0 for value in numeric) or not row["source_sha256"].strip():
            raise RuntimeError("X06 liquidation causal identity check failed")
        if not row["available_at"].strip():
            blank_available += 1
        keys.append(tuple(row[field] for field in ("time", "side", "order_type", "time_in_force", "original_quantity", "price", "average_price", "order_status", "last_fill_quantity", "accumulated_fill_quantity", "venue", "instrument", "event_time_utc")))
    counts = Counter(keys)
    unique = len(counts)
    duplicate_rows = sum(count - 1 for count in counts.values() if count > 1)
    out = OUT / "x06_liquidation_evidence_normalized.csv"
    _write_csv(out, required, rows)
    return {
        "status": "CAUSAL_EVIDENCE_RETAINED_PIT_BLOCKED",
        "evidence_type": "venue_tagged_liquidation_snapshot",
        "raw_rows": len(rows),
        "unique_event_keys": unique,
        "exact_duplicate_rows": duplicate_rows,
        "duplicate_pairs": sum(count // 2 for count in counts.values() if count > 1),
        "missing_available_at": blank_available,
        "venue_contract_identities": sorted({(row["venue"], row["instrument"]) for row in rows}),
        "duplicate_policy": "exact duplicate payloads retained for audit and excluded from admission; non-identical revisions require revision_id plus publication timestamp",
        "required_fields": required,
        "normalized_path": str(out.relative_to(REPO)),
        "source_path": str(path.relative_to(REPO)),
        "source_sha256": sha256(path),
        "admitted_rows": 0,
        "economic_gate": "DATA_ACCESS_BLOCKED_LIQUIDATION_PIT_AND_REVISION_VINTAGE",
    }


def run() -> dict[str, Any]:
    manifest = load_manifest()
    verify_inputs(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    derivative_result = x05_x06_derivatives()
    liquidation_result = x06_liquidation_evidence()
    cards = {
        "X01": x01_curve(),
        "X02": x02_cme_retry(),
        "X03": x03_country(),
        "X04": x04_pairs(),
        "X05": derivative_result,
        "X06": liquidation_result,
    }
    for card in ("X01", "X03", "X04", "X05", "X06"):
        cards[card]["data_access_blocked"] = True
    result = {
        "mission": manifest["mission"],
        "manifest_sha256": sha256(MANIFEST),
        "generated_at": datetime.now(UTC).isoformat(),
        "cutoff": manifest["cutoff"],
        "economic_execution": False,
        "product_edits": False,
        "cards": cards,
        "overall_status": "PREFLIGHT_COMPLETE_WITH_DATA_AND_CAPABILITY_BLOCKERS",
    }
    result_path = OUT / "results.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_files = sorted(path for path in OUT.iterdir() if path.is_file() and path.name != "validation_receipt.json")
    receipt = {
        "mission": manifest["mission"],
        "manifest_sha256": sha256(MANIFEST),
        "result_sha256": sha256(result_path),
        "generated_files": {str(path.relative_to(REPO)): sha256(path) for path in output_files},
        "economic_execution": False,
        "validation": "hash-pinned inputs, cutoff/quarantine, normalized schemas, and explicit blocker gates",
    }
    (OUT / "validation_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def verify() -> dict[str, Any]:
    """Verify the frozen preflight package without writing or requesting data."""
    manifest = load_manifest()
    verify_inputs(manifest)
    result_path = OUT / "results.json"
    validation_path = OUT / "validation_receipt.json"
    if not result_path.is_file() or not validation_path.is_file():
        raise RuntimeError("preflight output receipt missing")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    manifest_sha = sha256(MANIFEST)
    result_sha = sha256(result_path)
    if result.get("manifest_sha256") != manifest_sha:
        raise RuntimeError("preflight result manifest pin mismatch")
    if validation.get("manifest_sha256") != manifest_sha or validation.get("result_sha256") != result_sha:
        raise RuntimeError("preflight validation receipt pin mismatch")
    generated_files = validation.get("generated_files")
    if not isinstance(generated_files, dict) or not generated_files:
        raise RuntimeError("preflight generated file inventory missing")
    for relative, declared_sha in generated_files.items():
        path = _source(str(relative))
        if not path.is_file() or sha256(path) != declared_sha:
            raise RuntimeError(f"preflight generated file hash mismatch: {relative}")
    return {"status": "CROSSASSET_PREFLIGHT_VERIFIED", "manifest_sha256": manifest_sha, "result_sha256": result_sha, "generated_files": len(generated_files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else run(), indent=2, sort_keys=True))
