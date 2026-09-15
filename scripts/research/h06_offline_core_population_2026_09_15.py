#!/usr/bin/env python3
"""Build the receipt-bound H06 core population without network or outcomes.

The builder reads only the frozen SEC cache and V5 identity lifetimes.  SQLite
is a scalable workspace; canonical table identities are logical row hashes and
never depend on SQLite page bytes.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import pandas_market_calendars as market_calendars
from pandas.tseries.holiday import USFederalHolidayCalendar

try:
    from scripts.research import h06_full_structural_admission_2026_09_14 as policy
except ModuleNotFoundError:  # Direct script execution places this directory on sys.path.
    import h06_full_structural_admission_2026_09_14 as policy

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "data/research/local/h06_full_acquisition_2026_09_14"
POPULATION = REPO / "data/research/local/h06_population_2026_09_15"
TARGET = SOURCE / "relevant_accession_target.tsv"
ACCEPTANCE = SOURCE / "relevant_accession_acceptance_map.tsv"
SUBMISSIONS_ZIP = SOURCE / "sec_submissions_bulk/submissions.zip"
V5 = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
NYSE_CALENDAR = REPO / "data/research/r46_treasury_auction_unwind_tlt_v1_2026_09_10/nyse_calendar.jsonl"
ACQUISITION = REPO / "data/research_manifests/h06_full_official_acquisition_2026_09_14.json"
CORE_MANIFEST = REPO / "data/research_manifests/h06_offline_core_population_2026_09_15.json"
ENRICHMENT_MANIFEST = REPO / "data/research_manifests/h06_late_stage_enrichment_targets_2026_09_15.json"
STRUCTURAL_MANIFEST = REPO / "data/research_manifests/h06_full_structural_admission_2026_09_14.json"
OWNER_MANIFEST = REPO / "data/research_manifests/h06_owner_year_classification_2026_09_14.json"
CORE_RECEIPT = REPO / "data/research_receipts/h06_offline_core_population_receipt_2026_09_15.json"
LINEAGE_RECEIPT = REPO / "data/research_receipts/h06_lineage_population_receipt_2026_09_15.json"
SCHEMA_RECEIPT = REPO / "data/research_receipts/h06_sec_bulk_schema_receipt_2026_09_15.json"
JOINT_RECEIPT = REPO / "data/research_receipts/h06_joint_owner_semantics_receipt_2026_09_15.json"
FOOTNOTE_RECEIPT = REPO / "data/research_receipts/h06_footnote_linkage_receipt_2026_09_15.json"
HOLDOUT_RECEIPT = REPO / "data/research_receipts/h06_true_holdout_signal_custody_2026_09_15.json"
REPORT = REPO / "docs/research/h06_offline_core_population_2026_09_15.md"
STATUS = "H06_OFFLINE_CORE_POPULATION_READY"
NY = ZoneInfo("America/New_York")

EXPECTED = {
    TARGET: "9165d68b4000f20f3a3b64d523232709ddd5a5e6af1d58a9933f5b1ac1930757",
    ACCEPTANCE: "21aadfcce06125b4ee4a3c391c4e9f92a206ef62598c35e18629640c38cec454",
    SUBMISSIONS_ZIP: "d270adfe53bb27e64ea5c87ef6b84f6821c8abb736cd3c0b376daf342fc7b99f",
    V5: "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    NYSE_CALENDAR: "6ae0dc6611729c060e703cd34ee86c84b67249a981b1490df79e1e4fadf155ec",
}

TABLE_SORTS: dict[str, tuple[str, ...]] = {
    "normalized_relevant_transactions": ("row_id",),
    "lineage_versions": ("accession",),
    "routine_history": ("row_id",),
    "owner_year_classification": ("reporting_owner_cik", "classification_year"),
    "signal_core_gates": ("row_id",),
    "monthly_decisions": ("decision_session",),
    "transaction_decision_map": ("row_id", "decision_session"),
    "cover_enrichment_targets": ("issuer_cik", "form4_symbol", "decision_timestamp"),
    "raw_sip_targets": ("symbol", "decision_session"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seal(payload: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(payload)
    value.setdefault("integrity", {}).pop("self_sha256", None)
    payload.setdefault("integrity", {})["self_sha256"] = hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()
    return payload


def verify_seal(payload: Mapping[str, Any]) -> None:
    value = copy.deepcopy(dict(payload))
    expected = value.setdefault("integrity", {}).pop("self_sha256", None)
    actual = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    if expected != actual:
        raise ValueError("self_hash_mismatch")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seal(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _norm_date(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _norm_cik(value: str | None) -> str:
    raw = re.sub(r"\D", "", value or "")
    return raw.zfill(10) if raw else ""


def _bool(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes"}:
        return 1
    if normalized in {"0", "false", "no"}:
        return 0
    return None


def _decimal(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        number = Decimal(value.strip().replace(",", ""))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(node: ElementTree.Element, path: Sequence[str]) -> str | None:
    current: ElementTree.Element | None = node
    for wanted in path:
        current = next((child for child in current or () if _local_name(child.tag) == wanted), None)
        if current is None:
            return None
    assert current is not None
    return (current.text or "").strip() or None


def _all(node: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [item for item in node.iter() if _local_name(item.tag) == name]


def verify_source_custody() -> dict[str, Any]:
    acquisition = json.loads(ACQUISITION.read_text(encoding="utf-8"))
    policy.verify_seal(acquisition)
    if acquisition.get("status") != "H06_OFFICIAL_SOURCE_COVERAGE_COMPLETE":
        raise ValueError("H06_OFFLINE_POPULATION_BLOCKED_SOURCE_DRIFT:status")
    if acquisition.get("remaining_blockers") != []:
        raise ValueError("H06_OFFLINE_POPULATION_BLOCKED_SOURCE_DRIFT:blockers")
    observed = {str(path.relative_to(REPO)): sha256_file(path) for path in EXPECTED}
    for path, expected in EXPECTED.items():
        if observed[str(path.relative_to(REPO))] != expected:
            raise ValueError(f"H06_OFFLINE_POPULATION_BLOCKED_SOURCE_DRIFT:{path.name}")
    quarter_rows = acquisition.get("quarter_sources", [])
    if len(quarter_rows) != 30:
        raise ValueError("H06_OFFLINE_POPULATION_BLOCKED_SOURCE_DRIFT:quarter_count")
    for row in quarter_rows:
        path = REPO / str(row["local_path"])
        if not path.is_file() or path.stat().st_size != int(row["bytes"]) or sha256_file(path) != row["sha256"]:
            raise ValueError(f"H06_OFFLINE_POPULATION_BLOCKED_SOURCE_DRIFT:{row['period']}")
    july_inventory = json.loads((SOURCE / "july_archive_inventory.json").read_text(encoding="utf-8"))
    bodies = july_inventory.get("accessions", [])
    if len(bodies) != 11242:
        raise ValueError("H06_OFFLINE_POPULATION_BLOCKED_SOURCE_DRIFT:july_count")
    for row in bodies:
        path = REPO / str(row["local_path"])
        if not path.is_file() or path.stat().st_size != int(row["bytes"]) or sha256_file(path) != row["sha256"]:
            raise ValueError(f"H06_OFFLINE_POPULATION_BLOCKED_SOURCE_DRIFT:{row['accession']}")
    return {
        "quarter_sources": 30,
        "july_bodies": len(bodies),
        "acceptance_map_rows": int(acquisition["relevant_accession_acceptance_map"]["total_map_rows"]),
        "observed_sha256": observed,
        "network_used": False,
    }


def inspect_bulk_schemas() -> dict[str, Any]:
    expected_tables = ("SUBMISSION.tsv", "REPORTINGOWNER.tsv", "NONDERIV_TRANS.tsv", "FOOTNOTES.tsv", "OWNER_SIGNATURE.tsv", "NONDERIV_HOLDING.tsv")
    headers: dict[str, list[str]] = {}
    variants: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    quarters = sorted((SOURCE / "sec_bulk").glob("*_form345.zip"))
    for path in quarters:
        with zipfile.ZipFile(path) as archive:
            for name in expected_tables:
                header = tuple(archive.read(name).splitlines()[0].decode("utf-8-sig").split("\t"))
                variants[name].add(header)
                headers.setdefault(name, list(header))
    required = {
        "SUBMISSION.tsv": {"ACCESSION_NUMBER", "DOCUMENT_TYPE", "ISSUERCIK"},
        "REPORTINGOWNER.tsv": {"ACCESSION_NUMBER", "RPTOWNERCIK"},
        "NONDERIV_TRANS.tsv": {"ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "TRANS_CODE"},
        "FOOTNOTES.tsv": {"ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"},
        "OWNER_SIGNATURE.tsv": {"ACCESSION_NUMBER", "OWNERSIGNATURENAME"},
        "NONDERIV_HOLDING.tsv": {"ACCESSION_NUMBER", "NONDERIV_HOLDING_SK"},
    }
    for name, fields in required.items():
        if any(not fields.issubset(set(header)) for header in variants[name]):
            raise ValueError(f"bulk_schema_missing_key:{name}")
    return {
        "status": "SEC_BULK_SCHEMA_KEYS_PROVEN",
        "quarter_count": len(quarters),
        "headers": headers,
        "header_variants": {
            name: [list(header) for header in sorted(values)] for name, values in sorted(variants.items())
        },
        "header_variant_counts": {name: len(values) for name, values in variants.items()},
        "keys": {
            "submission": ["ACCESSION_NUMBER"],
            "reporting_owner": ["ACCESSION_NUMBER", "RPTOWNERCIK"],
            "transaction": ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK"],
            "footnote": ["ACCESSION_NUMBER", "FOOTNOTE_ID"],
            "transaction_footnote_references": "all NONDERIV_TRANS *_FN fields, exact token join",
        },
    }


def _create_database(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.executescript(
        """
        CREATE TABLE acceptance(accession TEXT PRIMARY KEY, form TEXT NOT NULL, filing_date TEXT NOT NULL,
          accepted_at TEXT NOT NULL, accepted_at_raw TEXT NOT NULL, authority TEXT NOT NULL, provenance_sha TEXT NOT NULL,
          complete_federal_date TEXT NOT NULL, available_at TEXT NOT NULL);
        CREATE TABLE submissions(accession TEXT PRIMARY KEY, form TEXT NOT NULL, filing_date TEXT,
          period_date TEXT, original_filing_date TEXT, issuer_cik TEXT, issuer_name TEXT, issuer_symbol TEXT,
          remarks TEXT, aff10b5one INTEGER, source_kind TEXT NOT NULL, source_quarter TEXT, source_sha TEXT NOT NULL);
        CREATE TABLE owners(accession TEXT NOT NULL, owner_cik TEXT NOT NULL, owner_name TEXT,
          relationship TEXT, owner_title TEXT, source_row_id TEXT NOT NULL,
          PRIMARY KEY(accession, owner_cik, source_row_id));
        CREATE TABLE footnotes(accession TEXT NOT NULL, footnote_id TEXT NOT NULL, footnote_text TEXT NOT NULL,
          source_kind TEXT NOT NULL, source_quarter TEXT NOT NULL, source_provenance TEXT NOT NULL,
          PRIMARY KEY(accession, footnote_id));
        CREATE TABLE source_transactions(accession TEXT NOT NULL, source_transaction_id TEXT NOT NULL,
          security_title TEXT, transaction_date TEXT, transaction_code TEXT, acquired_disposed TEXT,
          shares TEXT, price TEXT, ownership_form TEXT, post_transaction_shares TEXT,
          footnote_ids TEXT NOT NULL, source_kind TEXT NOT NULL, source_quarter TEXT, source_provenance TEXT NOT NULL,
          PRIMARY KEY(accession, source_transaction_id));
        CREATE TABLE normalized_relevant_transactions(row_id TEXT PRIMARY KEY, accession TEXT NOT NULL,
          source_transaction_id TEXT NOT NULL, reporting_owner_cik TEXT NOT NULL, reporting_owner_name TEXT,
          issuer_cik TEXT NOT NULL, issuer_symbol TEXT, form TEXT NOT NULL, filing_date TEXT,
          transaction_date TEXT, security_title TEXT, transaction_code TEXT, acquired_disposed TEXT,
          shares TEXT, price TEXT, ownership_form TEXT, post_transaction_shares TEXT,
          footnote_ids TEXT NOT NULL, linked_footnote_text TEXT, aff10b5one INTEGER,
          accepted_at TEXT NOT NULL, available_at TEXT NOT NULL, acceptance_authority TEXT NOT NULL,
          source_kind TEXT NOT NULL, source_quarter TEXT, source_provenance TEXT NOT NULL,
          lineage_status TEXT, version_start TEXT, version_end TEXT);
        CREATE TABLE lineage_versions(accession TEXT PRIMARY KEY, form TEXT NOT NULL, original_accession TEXT,
          candidate_original_accessions TEXT NOT NULL, issuer_cik TEXT NOT NULL, reporting_owner_ciks TEXT NOT NULL,
          original_filing_date TEXT, available_at TEXT NOT NULL, version_start TEXT,
          version_end TEXT, lineage_status TEXT NOT NULL, lineage_resolved INTEGER NOT NULL,
          transaction_fingerprint_sha256 TEXT NOT NULL, explanation TEXT);
        CREATE TABLE routine_history(row_id TEXT PRIMARY KEY, reporting_owner_cik TEXT NOT NULL,
          issuer_cik TEXT NOT NULL, accession TEXT NOT NULL, transaction_date TEXT,
          transaction_month INTEGER, transaction_code TEXT, available_at TEXT NOT NULL,
          lineage_status TEXT NOT NULL, version_start TEXT, version_end TEXT, source_provenance TEXT NOT NULL,
          qualifying INTEGER NOT NULL, exclusion_status TEXT);
        CREATE TABLE owner_year_classification(reporting_owner_cik TEXT NOT NULL, classification_year INTEGER NOT NULL,
          count_y3 INTEGER NOT NULL, count_y2 INTEGER NOT NULL, count_y1 INTEGER NOT NULL,
          months_y3 TEXT NOT NULL, months_y2 TEXT NOT NULL, months_y1 TEXT NOT NULL,
          common_month_intersection TEXT NOT NULL, classification TEXT NOT NULL,
          input_row_ids TEXT NOT NULL, input_logical_sha256 TEXT NOT NULL, classification_cutoff TEXT NOT NULL,
          PRIMARY KEY(reporting_owner_cik, classification_year));
        CREATE TABLE signal_core_gates(row_id TEXT PRIMARY KEY, accession TEXT NOT NULL, reporting_owner_cik TEXT NOT NULL,
          issuer_cik TEXT NOT NULL, issuer_symbol TEXT, transaction_date TEXT, available_at TEXT NOT NULL,
          information_session TEXT NOT NULL, version_end TEXT, security_title TEXT,
          owner_classification TEXT NOT NULL, lineage_gate INTEGER NOT NULL, nonderivative_gate INTEGER NOT NULL,
          purchase_gate INTEGER NOT NULL, acquired_gate INTEGER NOT NULL, direct_gate INTEGER NOT NULL,
          shares_gate INTEGER NOT NULL, price_gate INTEGER NOT NULL, opportunistic_gate INTEGER NOT NULL,
          timing_gate INTEGER NOT NULL, ten_b5_status TEXT NOT NULL, open_private_status TEXT NOT NULL,
          primary_exclusion TEXT, core_status TEXT NOT NULL, identity_precheck TEXT NOT NULL,
          v5_candidate_entity_id TEXT, true_holdout INTEGER NOT NULL);
        CREATE TABLE monthly_decisions(calendar_month TEXT PRIMARY KEY, decision_session TEXT NOT NULL,
          decision_timestamp TEXT NOT NULL, prior_21_sessions TEXT NOT NULL);
        CREATE TABLE transaction_decision_map(row_id TEXT NOT NULL, decision_session TEXT NOT NULL,
          decision_timestamp TEXT NOT NULL, information_session TEXT NOT NULL,
          PRIMARY KEY(row_id, decision_session));
        CREATE TABLE cover_enrichment_targets(issuer_cik TEXT NOT NULL, form4_symbol TEXT NOT NULL,
          decision_timestamp TEXT NOT NULL, transaction_security_titles TEXT NOT NULL,
          v5_candidate_entity_id TEXT, identity_precheck TEXT NOT NULL, cover_accession TEXT,
          cover_primary_document TEXT, cover_form TEXT, cover_accepted_at TEXT, cover_available_at TEXT,
          originating_core_pass_row_count INTEGER NOT NULL, true_holdout INTEGER NOT NULL,
          target_reason TEXT NOT NULL, PRIMARY KEY(issuer_cik, form4_symbol, decision_timestamp));
        CREATE TABLE raw_sip_targets(symbol TEXT NOT NULL, decision_session TEXT NOT NULL,
          timeframe TEXT NOT NULL, feed TEXT NOT NULL, adjustment TEXT NOT NULL,
          true_holdout INTEGER NOT NULL, PRIMARY KEY(symbol, decision_session));
        """
    )
    return connection


class TimingCalendar:
    def __init__(self) -> None:
        calendar = market_calendars.get_calendar("NYSE")
        self.schedule = calendar.schedule(start_date="2010-01-01", end_date="2026-09-30")
        self.opens = {
            stamp.date(): value.to_pydatetime().astimezone(UTC)
            for stamp, value in self.schedule["market_open"].items()
        }
        self.closes = {
            stamp.date(): value.to_pydatetime().astimezone(UTC)
            for stamp, value in self.schedule["market_close"].items()
        }
        self.sessions = sorted(self.opens)
        self.frozen_sessions: list[date] = []
        with NYSE_CALENDAR.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                session = date.fromisoformat(row["regular_session_date"])
                self.frozen_sessions.append(session)
                frozen_open = policy.parse_utc(row["regular_open_timestamp"])
                frozen_close = policy.parse_utc(row["regular_close_timestamp"])
                if self.opens.get(session) != frozen_open or self.closes.get(session) != frozen_close:
                    raise ValueError(f"nyse_calendar_library_drift:{session}")
                self.opens[session] = frozen_open
                self.closes[session] = frozen_close
        self.holidays = {
            stamp.date()
            for stamp in USFederalHolidayCalendar().holidays(start="2009-12-01", end="2026-10-31")
        }
        self._availability: dict[date, tuple[str, str]] = {}

    def availability(self, accepted_at: str) -> tuple[str, str]:
        accepted = policy.parse_utc(accepted_at).astimezone(NY)
        if accepted.date() in self._availability:
            return self._availability[accepted.date()]
        complete = accepted.date() + timedelta(days=1)
        while complete.weekday() >= 5 or complete in self.holidays:
            complete += timedelta(days=1)
        session = next((day for day in self.sessions if day > complete), None)
        if session is None:
            raise ValueError("no_nyse_session_after_complete_federal_business_day")
        result = (complete.isoformat(), self.opens[session].isoformat().replace("+00:00", "Z"))
        self._availability[accepted.date()] = result
        return result

    def decisions(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        supported = [day for day in self.frozen_sessions if date(2021, 11, 1) <= day <= date(2026, 7, 31)]
        for year in range(2022, 2027):
            last_month = 7 if year == 2026 else 12
            for month in range(1, last_month + 1):
                eligible = [day for day in supported if day.year == year and day.month == month]
                if not eligible:
                    raise ValueError(f"missing_nyse_month:{year}-{month:02d}")
                decision = eligible[-1]
                index = supported.index(decision)
                prior = supported[index - 20 : index + 1]
                if len(prior) != 21:
                    raise ValueError("decision_window_short")
                rows.append(
                    {
                        "calendar_month": f"{year:04d}-{month:02d}",
                        "decision_session": decision.isoformat(),
                        "decision_timestamp": self.closes[decision].isoformat().replace("+00:00", "Z"),
                        "prior_21_sessions": canonical_json([item.isoformat() for item in prior]),
                    }
                )
        return rows


def _load_acceptance(connection: sqlite3.Connection, timing: TimingCalendar) -> set[str]:
    accessions: set[str] = set()
    rows = []
    with ACCEPTANCE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for raw in reader:
            accession = raw["accession"]
            if accession in accessions:
                raise ValueError(f"duplicate_acceptance:{accession}")
            accessions.add(accession)
            complete, available = timing.availability(raw["accepted_at_utc"])
            provenance = raw["source_member_shas"] or raw["parent_daily_archive_shas"]
            rows.append(
                (
                    accession,
                    raw["form"],
                    raw["filing_date"],
                    raw["accepted_at_utc"],
                    raw["accepted_at_utc"],
                    raw["authority"],
                    provenance,
                    complete,
                    available,
                )
            )
            if len(rows) >= 20000:
                connection.executemany("INSERT INTO acceptance VALUES (?,?,?,?,?,?,?,?,?)", rows)
                rows.clear()
    connection.executemany("INSERT INTO acceptance VALUES (?,?,?,?,?,?,?,?,?)", rows)
    if len(accessions) != 395310:
        raise ValueError(f"acceptance_count:{len(accessions)}")
    return accessions


def _target_flags() -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    with TARGET.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            result[row["accession"]] = (int(row["routine_history_target"]), int(row["signal_structural_target"]))
    if len(result) != 384068:
        raise ValueError(f"target_count:{len(result)}")
    return result


def _zip_reader(archive: zipfile.ZipFile, name: str) -> csv.DictReader[str]:
    return csv.DictReader(io.TextIOWrapper(archive.open(name), encoding="utf-8-sig", newline=""), delimiter="\t")


def _fn_map(row: Mapping[str, str]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for key, value in row.items():
        if not key.endswith("_FN") or not value:
            continue
        refs = sorted({item.upper() for item in re.findall(r"F\d+", value, flags=re.IGNORECASE)})
        if refs:
            values[key] = refs
    return dict(sorted(values.items()))


def _referenced_ids(value: Mapping[str, Sequence[str]]) -> list[str]:
    return sorted({item for refs in value.values() for item in refs})


def _xml_fn_map(node: ElementTree.Element) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)

    def walk(current: ElementTree.Element, path: str) -> None:
        current_path = f"{path}/{_local_name(current.tag)}" if path else _local_name(current.tag)
        identifier = (current.attrib.get("id") or "").upper()
        if identifier.startswith("F"):
            result[current_path].append(identifier)
        for child in current:
            walk(child, current_path)

    walk(node, "")
    return {key: sorted(set(values)) for key, values in sorted(result.items())}


def normalize_quarters(
    connection: sqlite3.Connection, relevant: set[str], quarter_hashes: Mapping[str, str]
) -> list[dict[str, Any]]:
    linkage_canaries: list[dict[str, Any]] = []
    for path in sorted((SOURCE / "sec_bulk").glob("*_form345.zip")):
        quarter = path.name[:6].upper()
        source_sha = quarter_hashes[path.name]
        with zipfile.ZipFile(path) as archive:
            submission_rows = []
            for row in _zip_reader(archive, "SUBMISSION.tsv"):
                accession = row["ACCESSION_NUMBER"]
                if accession not in relevant:
                    continue
                submission_rows.append(
                    (
                        accession,
                        row["DOCUMENT_TYPE"].upper(),
                        _norm_date(row.get("FILING_DATE")),
                        _norm_date(row.get("PERIOD_OF_REPORT")),
                        _norm_date(row.get("DATE_OF_ORIG_SUB")),
                        _norm_cik(row.get("ISSUERCIK")),
                        row.get("ISSUERNAME") or None,
                        (row.get("ISSUERTRADINGSYMBOL") or "").strip().upper() or None,
                        row.get("REMARKS") or None,
                        _bool(row.get("AFF10B5ONE")),
                        "quarterly_tsv",
                        quarter,
                        source_sha,
                    )
                )
            connection.executemany("INSERT OR IGNORE INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", submission_rows)

            owner_rows = []
            for ordinal, row in enumerate(_zip_reader(archive, "REPORTINGOWNER.tsv"), start=2):
                accession = row["ACCESSION_NUMBER"]
                if accession not in relevant:
                    continue
                owner_rows.append(
                    (
                        accession,
                        _norm_cik(row.get("RPTOWNERCIK")),
                        row.get("RPTOWNERNAME") or None,
                        row.get("RPTOWNER_RELATIONSHIP") or None,
                        row.get("RPTOWNER_TITLE") or None,
                        f"{quarter}:REPORTINGOWNER.tsv:{ordinal}",
                    )
                )
            connection.executemany("INSERT OR IGNORE INTO owners VALUES (?,?,?,?,?,?)", owner_rows)

            footnote_rows = []
            for row in _zip_reader(archive, "FOOTNOTES.tsv"):
                accession = row["ACCESSION_NUMBER"]
                if accession in relevant and row.get("FOOTNOTE_ID"):
                    footnote_rows.append((accession, row["FOOTNOTE_ID"].upper(), row.get("FOOTNOTE_TXT") or "", "quarterly_tsv", quarter, f"{path.relative_to(REPO)}#FOOTNOTES.tsv:{row['FOOTNOTE_ID']}"))
            connection.executemany("INSERT OR IGNORE INTO footnotes VALUES (?,?,?,?,?,?)", footnote_rows)

            transaction_rows = []
            for row in _zip_reader(archive, "NONDERIV_TRANS.tsv"):
                accession = row["ACCESSION_NUMBER"]
                if accession not in relevant:
                    continue
                footnote_map = _fn_map(row)
                ids = _referenced_ids(footnote_map)
                transaction_rows.append(
                    (
                        accession,
                        f"SEC_NONDERIV_TRANS_SK:{row['NONDERIV_TRANS_SK']}",
                        row.get("SECURITY_TITLE") or None,
                        _norm_date(row.get("TRANS_DATE")),
                        (row.get("TRANS_CODE") or "").strip().upper() or None,
                        (row.get("TRANS_ACQUIRED_DISP_CD") or "").strip().upper() or None,
                        _decimal(row.get("TRANS_SHARES")),
                        _decimal(row.get("TRANS_PRICEPERSHARE")),
                        (row.get("DIRECT_INDIRECT_OWNERSHIP") or "").strip().upper() or None,
                        _decimal(row.get("SHRS_OWND_FOLWNG_TRANS")),
                        canonical_json(footnote_map),
                        "quarterly_tsv",
                        quarter,
                        f"{path.relative_to(REPO)}#{row['NONDERIV_TRANS_SK']}",
                    )
                )
                if ids and len(linkage_canaries) < 12:
                    linked = sorted(item[1] for item in footnote_rows if item[0] == accession and item[1] in ids)
                    linkage_canaries.append({"accession": accession, "source_transaction_id": row["NONDERIV_TRANS_SK"], "referenced_ids": ids, "resolved_ids": linked})
            connection.executemany("INSERT OR IGNORE INTO source_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", transaction_rows)
        connection.commit()
    return linkage_canaries


def _ownership_xml(raw: bytes) -> ElementTree.Element:
    match = re.search(rb"<ownershipDocument\b.*?</ownershipDocument\s*>", raw, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError("ownership_xml_missing")
    return ElementTree.fromstring(match.group(0))


def normalize_july(connection: sqlite3.Connection, relevant: set[str]) -> list[dict[str, Any]]:
    canaries: list[dict[str, Any]] = []
    for path in sorted((SOURCE / "sec_july_accessions").glob("*.txt")):
        accession = path.stem
        if accession not in relevant:
            raise ValueError(f"july_acceptance_missing:{accession}")
        raw = path.read_bytes()
        source_sha = hashlib.sha256(raw).hexdigest()
        root = _ownership_xml(raw)
        acceptance = connection.execute("SELECT form,filing_date FROM acceptance WHERE accession=?", (accession,)).fetchone()
        if acceptance is None:
            raise ValueError(f"july_acceptance_missing:{accession}")
        issuer = next(iter(_all(root, "issuer")), None)
        issuer_cik = _norm_cik(_child_text(issuer, ["issuerCik"]) if issuer is not None else None)
        symbol = (_child_text(issuer, ["issuerTradingSymbol"]) if issuer is not None else None)
        original = _norm_date(_child_text(root, ["dateOfOriginalSubmission"]) or _child_text(root, ["originalSubmissionDate"]))
        aff = _bool(_child_text(root, ["aff10b5One"]))
        connection.execute(
            "INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                accession,
                str(acceptance[0]).upper(),
                acceptance[1],
                _norm_date(_child_text(root, ["periodOfReport"])),
                original,
                issuer_cik,
                _child_text(issuer, ["issuerName"]) if issuer is not None else None,
                symbol.strip().upper() if symbol else None,
                _child_text(root, ["remarks"]),
                aff,
                "july_complete_submission",
                "2026JULY",
                source_sha,
            ),
        )
        owners = _all(root, "reportingOwner")
        for position, owner in enumerate(owners, start=1):
            owner_cik = _norm_cik(_child_text(owner, ["reportingOwnerId", "rptOwnerCik"]))
            relationship = next(iter(_all(owner, "reportingOwnerRelationship")), None)
            connection.execute(
                "INSERT INTO owners VALUES (?,?,?,?,?,?)",
                (
                    accession,
                    owner_cik,
                    _child_text(owner, ["reportingOwnerId", "rptOwnerName"]),
                    canonical_json({_local_name(item.tag): (item.text or "").strip() for item in relationship or ()}),
                    _child_text(owner, ["reportingOwnerRelationship", "officerTitle"]),
                    f"2026JULY:ownershipDocument/reportingOwner[{position}]",
                ),
            )
        for footnote in _all(root, "footnote"):
            footnote_id = (footnote.attrib.get("id") or "").upper()
            if footnote_id:
                connection.execute("INSERT OR IGNORE INTO footnotes VALUES (?,?,?,?,?,?)", (accession, footnote_id, "".join(footnote.itertext()).strip(), "july_complete_submission", "2026JULY", f"{path.relative_to(REPO)}#ownershipDocument/footnotes/{footnote_id}"))
        transactions = _all(root, "nonDerivativeTransaction")
        occurrence: Counter[str] = Counter()
        for position, transaction in enumerate(transactions, start=1):
            node_bytes = ElementTree.tostring(transaction, encoding="utf-8")
            node_sha = hashlib.sha256(node_bytes).hexdigest()
            occurrence[node_sha] += 1
            source_id = f"XML_XPATH:nonDerivativeTransaction[{position}]:{node_sha}:OCCURRENCE:{occurrence[node_sha]}"
            footnote_map = _xml_fn_map(transaction)
            connection.execute(
                "INSERT INTO source_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    accession,
                    source_id,
                    _child_text(transaction, ["securityTitle", "value"]),
                    _norm_date(_child_text(transaction, ["transactionDate", "value"])),
                    (_child_text(transaction, ["transactionCoding", "transactionCode"]) or "").upper() or None,
                    (_child_text(transaction, ["transactionAmounts", "transactionAcquiredDisposedCode", "value"]) or "").upper() or None,
                    _decimal(_child_text(transaction, ["transactionAmounts", "transactionShares", "value"])),
                    _decimal(_child_text(transaction, ["transactionAmounts", "transactionPricePerShare", "value"])),
                    (_child_text(transaction, ["ownershipNature", "directOrIndirectOwnership", "value"]) or "").upper() or None,
                    _decimal(_child_text(transaction, ["postTransactionAmounts", "sharesOwnedFollowingTransaction", "value"])),
                    canonical_json(footnote_map),
                    "july_complete_submission",
                    "2026JULY",
                    f"{path.relative_to(REPO)}#ownershipDocument/nonDerivativeTable/nonDerivativeTransaction[{position}]@sha256:{node_sha}",
                ),
            )
        if len(owners) > 1 and transactions and len(canaries) < 12:
            canaries.append(
                {
                    "accession": accession,
                    "reporting_owner_ciks": sorted(_norm_cik(_child_text(owner, ["reportingOwnerId", "rptOwnerCik"])) for owner in owners),
                    "transaction_count": len(transactions),
                    "xml_structure": "reportingOwner siblings and document-level nonDerivativeTable",
                    "expected_expanded_rows": len(owners) * len(transactions),
                    "body_sha256": source_sha,
                }
            )
        if int(accession[-2:]) % 20 == 0:
            connection.commit()
    connection.commit()
    if connection.execute("SELECT COUNT(*) FROM submissions WHERE source_kind='july_complete_submission'").fetchone()[0] != 11242:
        raise ValueError("july_normalization_count")
    return canaries


def _transaction_fingerprints(connection: sqlite3.Connection) -> dict[str, str]:
    result: dict[str, str] = {}
    current_accession: str | None = None
    current_rows: list[list[str | None]] = []
    query = """SELECT accession,security_title,transaction_date,transaction_code,acquired_disposed,
               shares,price,ownership_form,post_transaction_shares,footnote_ids
               FROM source_transactions ORDER BY accession,source_transaction_id"""
    for row in connection.execute(query):
        accession = str(row[0])
        if current_accession is not None and accession != current_accession:
            result[current_accession] = hashlib.sha256(canonical_json(current_rows).encode("utf-8")).hexdigest()
            current_rows = []
        current_accession = accession
        current_rows.append(list(row[1:]))
    if current_accession is not None:
        result[current_accession] = hashlib.sha256(canonical_json(current_rows).encode("utf-8")).hexdigest()
    return result


def resolve_lineage(connection: sqlite3.Connection) -> dict[str, Any]:
    owner_members: dict[str, set[str]] = defaultdict(set)
    for accession, owner_cik in connection.execute("SELECT accession,owner_cik FROM owners ORDER BY accession,owner_cik"):
        owner_members[str(accession)].add(str(owner_cik))
    owner_sets = {
        str(accession): canonical_json(sorted(owner_members[str(accession)]))
        for accession, in connection.execute("SELECT accession FROM submissions ORDER BY accession")
    }
    fingerprints = _transaction_fingerprints(connection)
    submissions = [dict(zip(("accession", "form", "filing_date", "original_filing_date", "issuer_cik", "remarks", "available_at"), row, strict=True)) for row in connection.execute(
        """SELECT s.accession,s.form,s.filing_date,s.original_filing_date,s.issuer_cik,s.remarks,a.available_at
           FROM submissions s JOIN acceptance a USING(accession) ORDER BY a.available_at,s.accession"""
    )]
    originals: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    by_accession = {str(row["accession"]): row for row in submissions}
    for row in submissions:
        if str(row["form"]).upper() == "4" and row["filing_date"]:
            originals[(str(row["issuer_cik"]), owner_sets[str(row["accession"])], str(row["filing_date"]))].append(str(row["accession"]))

    amendment_records: dict[str, dict[str, Any]] = {}
    plausible_end: dict[str, str] = {}
    resolved_by_original: dict[str, list[str]] = defaultdict(list)
    for row in submissions:
        accession = str(row["accession"])
        if not str(row["form"]).upper().endswith("/A"):
            continue
        key = (str(row["issuer_cik"]), owner_sets[accession], str(row["original_filing_date"] or ""))
        candidates = sorted(originals.get(key, []))
        official_text = " ".join(
            [str(row.get("remarks") or "")]
            + [str(item[0]) for item in connection.execute("SELECT footnote_text FROM footnotes WHERE accession=? ORDER BY footnote_id", (accession,))]
        )
        explicit = sorted(set(re.findall(r"\b\d{10}-\d{2}-\d{6}\b", official_text)).intersection(candidates))
        if explicit:
            candidates = explicit
        replacement_exact = len(candidates) == 1 and fingerprints.get(candidates[0]) == fingerprints.get(accession)
        resolved = bool(replacement_exact)
        original = candidates[0] if resolved else None
        if resolved and original is not None:
            resolved_by_original[original].append(accession)
        else:
            for candidate in candidates:
                prior = plausible_end.get(candidate)
                available = str(row["available_at"])
                plausible_end[candidate] = min(prior, available) if prior else available
        amendment_records[accession] = {
            "candidates": candidates,
            "original": original,
            "resolved": resolved,
            "status": "AMENDED_VERSION_ACTIVE" if resolved else "AMENDMENT_LINEAGE_UNRESOLVED",
            "explanation": "exact issuer, owner set, original date and complete transaction fingerprint" if resolved else "document candidate or row-effect semantics not uniquely exact; fail closed",
        }

    interval_end: dict[str, str] = dict(plausible_end)
    amendment_end: dict[str, str] = {}
    for original, amendments in resolved_by_original.items():
        ordered = sorted(amendments, key=lambda item: (str(by_accession[item]["available_at"]), item))
        first = str(by_accession[ordered[0]]["available_at"])
        prior = interval_end.get(original)
        interval_end[original] = min(prior, first) if prior else first
        for left, right in zip(ordered, ordered[1:]):
            amendment_end[left] = str(by_accession[right]["available_at"])

    for row in submissions:
        accession = str(row["accession"])
        record = amendment_records.get(accession)
        if record is None:
            status = "SUPERSEDED_AFTER_AMENDMENT" if accession in resolved_by_original else "ORIGINAL_ACTIVE"
            candidate_accessions: list[str] = []
            original = None
            resolved = True
            explanation = "unamended original" if status == "ORIGINAL_ACTIVE" else "original active until exact replacement amendment"
            version_end = interval_end.get(accession)
        else:
            status = str(record["status"])
            candidate_accessions = list(record["candidates"])
            original = record["original"]
            resolved = bool(record["resolved"])
            explanation = str(record["explanation"])
            version_end = amendment_end.get(accession) if resolved else None
        connection.execute(
            "INSERT INTO lineage_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                accession,
                row["form"],
                original,
                canonical_json(candidate_accessions),
                row["issuer_cik"],
                owner_sets[accession],
                row["original_filing_date"],
                row["available_at"],
                row["available_at"] if resolved else None,
                version_end,
                status,
                int(resolved),
                fingerprints.get(accession, hashlib.sha256(b"[]").hexdigest()),
                explanation,
            ),
        )
    connection.commit()
    counts = Counter(str(row[0]) for row in connection.execute("SELECT lineage_status FROM lineage_versions"))
    return {
        "originals": sum(form == "4" for form, in connection.execute("SELECT form FROM submissions")),
        "amendments": sum(str(form).endswith("/A") for form, in connection.execute("SELECT form FROM submissions")),
        "resolved_amendments": counts["AMENDED_VERSION_ACTIVE"],
        "unresolved_amendments": counts["AMENDMENT_LINEAGE_UNRESOLVED"],
        "superseded_versions": counts["SUPERSEDED_AFTER_AMENDMENT"],
    }


def expand_observations(connection: sqlite3.Connection) -> None:
    footnotes: dict[str, dict[str, str]] = defaultdict(dict)
    for accession, identifier, text in connection.execute("SELECT accession,footnote_id,footnote_text FROM footnotes"):
        footnotes[str(accession)][str(identifier)] = str(text)
    owners: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for accession, cik, name in connection.execute("SELECT accession,owner_cik,owner_name FROM owners ORDER BY accession,owner_cik,source_row_id"):
        pair = (str(cik), str(name) if name is not None else None)
        if pair not in owners[str(accession)]:
            owners[str(accession)].append(pair)
    query = """SELECT t.accession,t.source_transaction_id,t.security_title,t.transaction_date,t.transaction_code,
             t.acquired_disposed,t.shares,t.price,t.ownership_form,t.post_transaction_shares,t.footnote_ids,
             t.source_kind,t.source_quarter,t.source_provenance,s.issuer_cik,s.issuer_symbol,s.form,s.filing_date,
             s.aff10b5one,a.accepted_at,a.available_at,a.authority,l.lineage_status,l.lineage_resolved,
             l.version_start,l.version_end
             FROM source_transactions t JOIN submissions s USING(accession) JOIN acceptance a USING(accession)
             JOIN lineage_versions l USING(accession) ORDER BY t.accession,t.source_transaction_id"""
    rows = []
    seen: set[str] = set()
    for raw in connection.execute(query):
        accession = str(raw[0])
        parsed = json.loads(str(raw[10]))
        ids = _referenced_ids(parsed) if isinstance(parsed, dict) else list(parsed)
        linked_text = "\n".join(footnotes[accession][item] for item in ids if item in footnotes[accession]) or None
        for owner_cik, owner_name in owners[accession]:
            row_id = hashlib.sha256(f"{accession}|TABLE_I|{raw[1]}|{owner_cik}".encode()).hexdigest()
            if row_id in seen:
                raise ValueError(f"duplicate_observation_id:{row_id}")
            seen.add(row_id)
            resolved = bool(raw[23])
            observation_status = "AMENDMENT_LINEAGE_UNRESOLVED" if not resolved else ("AMENDED_VERSION_ACTIVE" if str(raw[16]).endswith("/A") else "ORIGINAL_ACTIVE")
            rows.append(
                (
                    row_id, accession, raw[1], owner_cik, owner_name, raw[14], raw[15], raw[16], raw[17],
                    raw[3], raw[2], raw[4], raw[5], raw[6], raw[7], raw[8], raw[9], raw[10], linked_text,
                    raw[18], raw[19], raw[20], raw[21], raw[11], raw[12], raw[13], observation_status,
                    raw[24] if resolved else None, raw[25] if resolved else None,
                )
            )
            if len(rows) >= 20000:
                connection.executemany("INSERT INTO normalized_relevant_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                rows.clear()
    connection.executemany("INSERT INTO normalized_relevant_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.executescript(
        """
        CREATE INDEX normalized_owner_year ON normalized_relevant_transactions(reporting_owner_cik,transaction_date);
        CREATE INDEX normalized_accession ON normalized_relevant_transactions(accession);
        CREATE INDEX normalized_signal_date ON normalized_relevant_transactions(transaction_date);
        """
    )
    connection.commit()


def build_routine_history(connection: sqlite3.Connection) -> dict[str, int]:
    rows = []
    counts: Counter[str] = Counter()
    query = """SELECT row_id,reporting_owner_cik,issuer_cik,accession,transaction_date,transaction_code,
             available_at,lineage_status,version_start,version_end,source_provenance
             FROM normalized_relevant_transactions ORDER BY row_id"""
    for raw in connection.execute(query):
        valid_date = _norm_date(raw[4])
        code = str(raw[5] or "")
        lineage_ok = str(raw[7]) in {"ORIGINAL_ACTIVE", "AMENDED_VERSION_ACTIVE"}
        qualifying = valid_date is not None and code in {"P", "S"} and lineage_ok
        exclusion = None
        if not lineage_ok:
            exclusion = "EXCLUDE_UNRESOLVED_LINEAGE"
            counts["lineage_excluded"] += 1
        elif valid_date is None:
            exclusion = "EXCLUDE_INVALID_TRANSACTION_DATE"
        elif code not in {"P", "S"}:
            exclusion = "EXCLUDE_NOT_P_OR_S"
        if qualifying:
            counts[code] += 1
            counts["qualifying"] += 1
        rows.append((*raw[:4], valid_date, int(valid_date[5:7]) if valid_date else None, code or None, *raw[6:], int(qualifying), exclusion))
        if len(rows) >= 20000:
            connection.executemany("INSERT INTO routine_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            rows.clear()
    connection.executemany("INSERT INTO routine_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.execute("CREATE INDEX routine_owner_date ON routine_history(reporting_owner_cik,transaction_date)")
    connection.commit()
    return dict(counts)


def build_owner_year(connection: sqlite3.Connection) -> dict[int, dict[str, int]]:
    owners = [str(row[0]) for row in connection.execute("SELECT DISTINCT reporting_owner_cik FROM normalized_relevant_transactions ORDER BY reporting_owner_cik")]
    coverage: dict[int, dict[str, int]] = {}
    for year in range(2022, 2027):
        cutoff = datetime(year, 1, 1, tzinfo=NY).astimezone(UTC).isoformat().replace("+00:00", "Z")
        values: dict[str, dict[str, Any]] = {}
        query = """SELECT row_id,reporting_owner_cik,transaction_date,transaction_month
                   FROM routine_history WHERE qualifying=1 AND transaction_date>=? AND transaction_date<?
                   AND version_start<? AND (version_end IS NULL OR version_end>?)
                   ORDER BY reporting_owner_cik,row_id"""
        for row_id, owner, transaction_date, month in connection.execute(
            query, (f"{year - 3}-01-01", f"{year}-01-01", cutoff, cutoff)
        ):
            transaction_year = int(str(transaction_date)[:4])
            item = values.setdefault(str(owner), {"counts": Counter(), "months": defaultdict(set), "ids": []})
            item["counts"][transaction_year] += 1
            item["months"][transaction_year].add(int(month))
            item["ids"].append(str(row_id))
        inserts = []
        class_counts: Counter[str] = Counter()
        for owner in owners:
            item = values.get(owner, {"counts": Counter(), "months": defaultdict(set), "ids": []})
            month_sets = [sorted(item["months"][prior]) for prior in range(year - 3, year)]
            if any(not months for months in month_sets):
                classification = "NON_PARTITIONABLE"
                common: list[int] = []
            else:
                common = sorted(set(month_sets[0]).intersection(*map(set, month_sets[1:])))
                classification = "ROUTINE" if common else "OPPORTUNISTIC"
            ids = sorted(item["ids"])
            class_counts[classification] += 1
            inserts.append(
                (
                    owner,
                    year,
                    int(item["counts"][year - 3]),
                    int(item["counts"][year - 2]),
                    int(item["counts"][year - 1]),
                    canonical_json(month_sets[0]),
                    canonical_json(month_sets[1]),
                    canonical_json(month_sets[2]),
                    canonical_json(common),
                    classification,
                    canonical_json(ids),
                    hashlib.sha256(canonical_json(ids).encode("utf-8")).hexdigest(),
                    cutoff,
                )
            )
            if len(inserts) >= 10000:
                connection.executemany("INSERT INTO owner_year_classification VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", inserts)
                inserts.clear()
        connection.executemany("INSERT INTO owner_year_classification VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", inserts)
        connection.commit()
        coverage[year] = {"total": len(owners), **{key.casefold(): class_counts[key] for key in ("ROUTINE", "OPPORTUNISTIC", "NON_PARTITIONABLE")}}
    return coverage


def load_v5() -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    with V5.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            for symbol in {row["canonical_symbol"], row["historical_symbol"]}:
                if symbol:
                    result[symbol].append(row)
    return result


def _v5_candidates(index: Mapping[str, Sequence[Mapping[str, str]]], symbol: str | None, at: str) -> list[Mapping[str, str]]:
    if not symbol:
        return []
    instant = policy.parse_utc(at)
    matches = []
    for row in index.get(symbol, ()):
        start = policy.parse_utc(str(row["valid_from"]))
        end = policy.parse_utc(str(row["valid_to"])) if row.get("valid_to") else None
        if start <= instant and (end is None or instant <= end):
            matches.append(row)
    unique = {str(row.get("entity_id") or row.get("provider_asset_id")): row for row in matches}
    return [unique[key] for key in sorted(unique)]


def identity_precheck(
    index: Mapping[str, Sequence[Mapping[str, str]]], symbol: str | None, security_title: str | None, at: str
) -> tuple[str, str | None]:
    title = " ".join((security_title or "").casefold().split())
    non_common = any(term in title for term in ("preferred", "option", "warrant", "restricted stock unit", "phantom", "debenture", "note"))
    common = any(term in title for term in ("common stock", "common shares", "ordinary share", "class a", "class b"))
    if non_common or (title and not common):
        return "IDENTITY_PRECHECK_NON_COMMON", None
    if not symbol:
        return "IDENTITY_PRECHECK_MISSING", None
    matches = _v5_candidates(index, symbol, at)
    if len(matches) == 1:
        return "IDENTITY_PRECHECK_UNIQUE_V5_SYMBOL", str(matches[0].get("entity_id") or matches[0].get("provider_asset_id"))
    if len(matches) > 1:
        return "IDENTITY_PRECHECK_AMBIGUOUS", None
    return "IDENTITY_PRECHECK_MISSING", None


def _core_primary(gates: Mapping[str, bool], owner_class: str, ten_b5: str, open_private: str) -> str | None:
    ordered = (
        ("lineage", "EXCLUDE_LINEAGE"),
        ("nonderivative", "EXCLUDE_DERIVATIVE"),
        ("purchase", "EXCLUDE_NOT_PURCHASE_CODE"),
        ("acquired", "EXCLUDE_NOT_ACQUIRED"),
        ("direct", "EXCLUDE_INDIRECT"),
        ("shares", "EXCLUDE_INVALID_SHARES"),
        ("price", "EXCLUDE_INVALID_PRICE"),
        ("opportunistic", "EXCLUDE_OWNER_ROUTINE"),
        ("timing", "EXCLUDE_TIMING"),
        ("ten_b5", "EXCLUDE_10B5_1"),
        ("open_market", "EXCLUDE_OPEN_PRIVATE_AMBIGUOUS"),
    )
    for gate, exclusion in ordered:
        if gates[gate]:
            continue
        if gate == "opportunistic" and owner_class == "NON_PARTITIONABLE":
            return "EXCLUDE_OWNER_NON_PARTITIONABLE"
        if gate == "open_market" and open_private == "PRIVATE_PURCHASE_EXPLICIT":
            return "EXCLUDE_PRIVATE"
        if gate == "ten_b5" and ten_b5 == "EXCLUDE_10B5_1_CHECKED":
            return "EXCLUDE_10B5_1"
        return exclusion
    return None


def build_signal_core(connection: sqlite3.Connection, v5_index: Mapping[str, Sequence[Mapping[str, str]]]) -> dict[str, Any]:
    classifications = {
        (str(owner), int(year)): str(classification)
        for owner, year, classification in connection.execute(
            "SELECT reporting_owner_cik,classification_year,classification FROM owner_year_classification"
        )
    }
    rows = []
    primary_counts: Counter[str] = Counter()
    funnel: Counter[str] = Counter()
    identity_counts: Counter[str] = Counter()
    query = """SELECT row_id,accession,reporting_owner_cik,issuer_cik,issuer_symbol,transaction_date,
             available_at,version_end,security_title,transaction_code,acquired_disposed,ownership_form,
             shares,price,aff10b5one,linked_footnote_text,lineage_status,accepted_at
             FROM normalized_relevant_transactions
             WHERE transaction_date>='2022-01-01' AND transaction_date<='2026-07-31'
             ORDER BY row_id"""
    for raw in connection.execute(query):
        transaction_date = str(raw[5])
        owner_class = classifications[(str(raw[2]), int(transaction_date[:4]))]
        disclosure = None if raw[14] is None else bool(raw[14])
        ten_b5 = policy.ten_b5_status(transaction_date, disclosure, str(raw[15] or ""))
        open_private = policy.open_private_status(str(raw[15] or ""))
        gates = {
            "lineage": str(raw[16]) in {"ORIGINAL_ACTIVE", "AMENDED_VERSION_ACTIVE"},
            "nonderivative": True,
            "purchase": str(raw[9] or "") == "P",
            "acquired": str(raw[10] or "") == "A",
            "direct": str(raw[11] or "") == "D",
            "shares": policy.finite_positive(raw[12]),
            "price": policy.finite_positive(raw[13]),
            "opportunistic": owner_class == "OPPORTUNISTIC",
            "timing": bool(raw[17]) and bool(raw[6]),
            "ten_b5": ten_b5.startswith("PASS_"),
            "open_market": open_private == "OPEN_MARKET_EXPLICIT",
        }
        primary = _core_primary(gates, owner_class, ten_b5, open_private)
        core_status = "CORE_SIGNAL_GATES_PASS_IDENTITY_DENOMINATOR_PENDING" if primary is None else "EXCLUDED"
        precheck, candidate = identity_precheck(v5_index, raw[4], raw[8], raw[6])
        if primary is None:
            identity_counts[precheck] += 1
        primary_counts[primary or "CORE_PASS"] += 1
        funnel["population"] += 1
        for key in ("nonderivative", "purchase", "acquired", "direct", "shares", "price", "opportunistic", "timing", "ten_b5", "open_market"):
            if gates[key]:
                funnel[key] += 1
        information_session = policy.parse_utc(str(raw[6])).astimezone(NY).date().isoformat()
        rows.append(
            (
                *raw[:7], information_session, raw[7], raw[8], owner_class,
                int(gates["lineage"]), 1, int(gates["purchase"]), int(gates["acquired"]),
                int(gates["direct"]), int(gates["shares"]), int(gates["price"]), int(gates["opportunistic"]),
                int(gates["timing"]), ten_b5, open_private, primary, core_status, precheck, candidate,
                int(transaction_date >= "2026-01-01"),
            )
        )
        if len(rows) >= 20000:
            connection.executemany("INSERT INTO signal_core_gates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            rows.clear()
    connection.executemany("INSERT INTO signal_core_gates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.execute("CREATE INDEX signal_core_status ON signal_core_gates(core_status,information_session)")
    connection.commit()
    return {"funnel": dict(funnel), "primary_exclusions": dict(primary_counts), "identity_precheck": dict(identity_counts)}


def build_decisions_and_map(connection: sqlite3.Connection, timing: TimingCalendar) -> dict[str, int]:
    decisions = timing.decisions()
    connection.executemany(
        "INSERT INTO monthly_decisions VALUES (?,?,?,?)",
        [(row["calendar_month"], row["decision_session"], row["decision_timestamp"], row["prior_21_sessions"]) for row in decisions],
    )
    by_session: dict[str, list[dict[str, str]]] = defaultdict(list)
    for decision in decisions:
        for session in json.loads(decision["prior_21_sessions"]):
            by_session[str(session)].append(decision)
    rows = []
    mapped_rows: set[str] = set()
    eligible_rows = 0
    for row_id, information_session, version_end in connection.execute(
        "SELECT row_id,information_session,version_end FROM signal_core_gates WHERE core_status=? ORDER BY row_id",
        ("CORE_SIGNAL_GATES_PASS_IDENTITY_DENOMINATOR_PENDING",),
    ):
        eligible_rows += 1
        for decision in by_session.get(str(information_session), []):
            if version_end is not None and str(decision["decision_timestamp"]) >= str(version_end):
                continue
            rows.append((row_id, decision["decision_session"], decision["decision_timestamp"], information_session))
            mapped_rows.add(str(row_id))
    connection.executemany("INSERT INTO transaction_decision_map VALUES (?,?,?,?)", rows)
    connection.commit()
    return {
        "monthly_decisions": len(decisions),
        "mapped_core_pass_rows": len(mapped_rows),
        "mapping_rows": len(rows),
        "unmapped_window_exclusions": eligible_rows - len(mapped_rows),
    }


def _aligned_rows(payload: Mapping[str, Any]) -> Iterator[dict[str, str]]:
    keys = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "primaryDocument")
    recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), dict) else payload
    if not isinstance(recent, dict):
        return
    count = len(recent.get("accessionNumber", [])) if isinstance(recent.get("accessionNumber"), list) else 0
    for index in range(count):
        yield {
            key: str(recent.get(key, [""] * count)[index]) if index < len(recent.get(key, [])) else ""
            for key in keys
        }


def _cover_inventory(issuer_ciks: Iterable[str], timing: TimingCalendar) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    with zipfile.ZipFile(SUBMISSIONS_ZIP) as archive:
        names = set(archive.namelist())
        for issuer_cik in sorted(set(issuer_ciks)):
            main_name = f"CIK{issuer_cik}.json"
            filings: list[dict[str, str]] = []
            if main_name not in names:
                result[issuer_cik] = []
                continue
            main = json.loads(archive.read(main_name))
            payloads: list[Mapping[str, Any]] = [main]
            for item in main.get("filings", {}).get("files", []):
                name = str(item.get("name", ""))
                if name in names:
                    payloads.append(json.loads(archive.read(name)))
            seen: set[str] = set()
            for payload in payloads:
                for row in _aligned_rows(payload):
                    accession = row["accessionNumber"]
                    if row["form"] not in {"10-K", "10-Q"} or not accession or accession in seen:
                        continue
                    seen.add(accession)
                    accepted = row["acceptanceDateTime"]
                    try:
                        accepted_normalized = policy.parse_utc(accepted).isoformat().replace("+00:00", "Z")
                        _, available = timing.availability(accepted_normalized)
                    except (ValueError, TypeError):
                        continue
                    filings.append({**row, "accepted_at": accepted_normalized, "available_at": available})
            result[issuer_cik] = sorted(filings, key=lambda item: (item["available_at"], item["accessionNumber"]))
    return result


def build_enrichment_targets(
    connection: sqlite3.Connection,
    timing: TimingCalendar,
    v5_index: Mapping[str, Sequence[Mapping[str, str]]],
) -> dict[str, int]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    query = """SELECT g.row_id,g.issuer_cik,g.issuer_symbol,g.security_title,m.decision_session,
             m.decision_timestamp,g.true_holdout
             FROM signal_core_gates g JOIN transaction_decision_map m USING(row_id)
             WHERE g.core_status='CORE_SIGNAL_GATES_PASS_IDENTITY_DENOMINATOR_PENDING'
             ORDER BY g.issuer_cik,g.issuer_symbol,m.decision_timestamp,g.row_id"""
    for row_id, issuer_cik, symbol, title, decision_session, decision_timestamp, holdout in connection.execute(query):
        precheck, candidate = identity_precheck(v5_index, symbol, title, decision_timestamp)
        if precheck not in {"IDENTITY_PRECHECK_UNIQUE_V5_SYMBOL", "IDENTITY_PRECHECK_AMBIGUOUS"} or not symbol:
            continue
        key = (str(issuer_cik), str(symbol), str(decision_timestamp))
        item = grouped.setdefault(
            key,
            {
                "titles": set(),
                "rows": set(),
                "decision_session": str(decision_session),
                "precheck": precheck,
                "candidate": candidate,
                "holdout": int(holdout),
            },
        )
        item["titles"].add(str(title or ""))
        item["rows"].add(str(row_id))
    cover_by_issuer = _cover_inventory((key[0] for key in grouped), timing)
    cover_target_rows = []
    raw_targets: dict[tuple[str, str], tuple[str, str, str, str, str, int]] = {}
    cover_accessions: set[str] = set()
    for (issuer_cik, symbol, decision_timestamp), item in sorted(grouped.items()):
        candidates = [filing for filing in cover_by_issuer.get(issuer_cik, ()) if filing["available_at"] <= decision_timestamp]
        cover = candidates[-1] if candidates else None
        if cover:
            cover_accessions.add(cover["accessionNumber"])
        cover_target_rows.append(
            (
                issuer_cik,
                symbol,
                decision_timestamp,
                canonical_json(sorted(item["titles"])),
                item["candidate"],
                item["precheck"],
                cover["accessionNumber"] if cover else None,
                cover["primaryDocument"] if cover else None,
                cover["form"] if cover else None,
                cover["accepted_at"] if cover else None,
                cover["available_at"] if cover else None,
                len(item["rows"]),
                item["holdout"],
                "final cover security class and filing-specific shares outstanding",
            )
        )
        if item["candidate"]:
            raw_key = (symbol, item["decision_session"])
            raw_targets[raw_key] = (symbol, item["decision_session"], "1Day", "SIP", "raw", item["holdout"])
    connection.executemany("INSERT INTO cover_enrichment_targets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cover_target_rows)
    connection.executemany("INSERT INTO raw_sip_targets VALUES (?,?,?,?,?,?)", [raw_targets[key] for key in sorted(raw_targets)])
    connection.commit()
    return {
        "cover_issuer_month_targets": len(cover_target_rows),
        "unique_issuers": len({row[0] for row in cover_target_rows}),
        "candidate_cover_accessions": len(cover_accessions),
        "raw_sip_targets": len(raw_targets),
        "true_holdout_targets": sum(int(row[12]) for row in cover_target_rows),
    }


def logical_table_hash(connection: sqlite3.Connection, table: str, sort_key: Sequence[str]) -> dict[str, Any]:
    columns = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]
    order = ",".join(f'"{column}"' for column in sort_key)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY {order}'):
        digest.update(canonical_json(list(row)).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return {
        "row_count": count,
        "schema": columns,
        "sort_key": list(sort_key),
        "logical_sha256": digest.hexdigest(),
        "canonicalization": "compact UTF-8 JSON array per row; SQL NULL as JSON null; LF separator",
    }


def logical_hashes(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {table: logical_table_hash(connection, table, sort_key) for table, sort_key in TABLE_SORTS.items()}


def _count(connection: sqlite3.Connection, sql: str, parameters: Sequence[Any] = ()) -> int:
    return int(connection.execute(sql, parameters).fetchone()[0])


def build_database(path: Path, custody: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    timing = TimingCalendar()
    connection = _create_database(path)
    relevant = _load_acceptance(connection, timing)
    targets = _target_flags()
    acquisition = json.loads(ACQUISITION.read_text(encoding="utf-8"))
    quarter_hashes = {Path(str(row["local_path"])).name: str(row["sha256"]) for row in acquisition["quarter_sources"]}
    linkage_canaries = normalize_quarters(connection, set(targets), quarter_hashes)
    july_canaries = normalize_july(connection, relevant)
    quarter_accessions = _count(connection, "SELECT COUNT(*) FROM submissions WHERE source_kind='quarterly_tsv'")
    if quarter_accessions != len(targets):
        raise ValueError(f"quarter_target_normalization:{quarter_accessions}:{len(targets)}")
    if _count(connection, "SELECT COUNT(*) FROM submissions") != len(relevant):
        raise ValueError("normalized_submission_coverage")
    lineage = resolve_lineage(connection)
    expand_observations(connection)
    for canary in july_canaries:
        actual = _count(connection, "SELECT COUNT(*) FROM normalized_relevant_transactions WHERE accession=?", (canary["accession"],))
        if actual != int(canary["expected_expanded_rows"]):
            raise ValueError(f"joint_owner_canary_mismatch:{canary['accession']}")
        canary["actual_expanded_rows"] = actual
    if not linkage_canaries or any(item["resolved_ids"] != item["referenced_ids"] for item in linkage_canaries):
        raise ValueError("quarterly_footnote_linkage_canary_failed")
    routine = build_routine_history(connection)
    owner_year = build_owner_year(connection)
    v5_index = load_v5()
    signal = build_signal_core(connection, v5_index)
    decision_map = build_decisions_and_map(connection, timing)
    enrichment = build_enrichment_targets(connection, timing, v5_index)
    hashes = logical_hashes(connection)
    normalization = {
        "relevant_accessions": len(relevant),
        "normalized_submissions": _count(connection, "SELECT COUNT(*) FROM submissions"),
        "normalized_owners": _count(connection, "SELECT COUNT(*) FROM owners"),
        "normalized_nonderivative_transactions": _count(connection, "SELECT COUNT(*) FROM source_transactions"),
        "normalized_transaction_owner_observations": _count(connection, "SELECT COUNT(*) FROM normalized_relevant_transactions"),
        "july_transaction_rows": _count(connection, "SELECT COUNT(*) FROM source_transactions WHERE source_kind='july_complete_submission'"),
    }
    routine["late_or_not_visible"] = _count(
        connection,
        """SELECT COUNT(*) FROM routine_history WHERE qualifying=1
           AND substr(available_at,1,4)>substr(transaction_date,1,4)""",
    )
    holdout_rows = _count(connection, "SELECT COUNT(*) FROM signal_core_gates WHERE true_holdout=1")
    connection.close()
    return {
        "status": STATUS,
        "database": str(path.relative_to(REPO)),
        "source_custody": dict(custody),
        "schema": dict(schema),
        "normalization": normalization,
        "joint_owner": {
            "status": "JOINT_OWNER_TRANSACTION_EXPANSION_PROVEN",
            "mapping_rule": "each reporting owner jointly reported each filing-level transaction; no owner-specific actor inference",
            "canaries": july_canaries,
        },
        "footnote_linkage": {
            "status": "SEC_BULK_TRANSACTION_FOOTNOTE_LINKAGE_PROVEN",
            "mapping_rule": "exact accession plus *_FN field-specific footnote IDs",
            "canaries": linkage_canaries,
        },
        "lineage": lineage,
        "routine_history": routine,
        "owner_year": owner_year,
        "signal": signal,
        "decision_map": decision_map,
        "enrichment": enrichment,
        "true_holdout": {
            "signal_rows_processed": holdout_rows,
            "outcome_fields_present": False,
            "economics_accessed": False,
            "status": "TRUE_HOLDOUT_SIGNAL_SIDE_ONLY_NO_ECONOMIC_ACCESS",
        },
        "logical_tables": hashes,
        "economic_boundary": {
            "network_access": False,
            "h06_score": False,
            "issuer_purchase_aggregation": False,
            "ranking": False,
            "percentile_or_decile": False,
            "final_identity_enrichment": False,
            "raw_sip_acquisition": False,
            "portfolio_backtest": False,
            "outcomes_or_pnl": False,
            "parameter_tuning": False,
        },
    }


def _artifact_payloads(result: Mapping[str, Any], run_b_hashes: Mapping[str, Any]) -> dict[Path, dict[str, Any]]:
    logical = dict(result["logical_tables"])
    remaining = [
        "final cover/security-class enrichment",
        "exact shares-fact enrichment",
        "raw SIP denominator-price enrichment",
        "final identity/denominator admission",
    ]
    common = {
        "status": STATUS,
        "cutoff": "2026-07-31T23:59:59Z",
        "network_used": False,
        "economic_run": False,
        "outcomes_accessed": False,
    }
    core = {
        **common,
        "schema": "h06-offline-core-population-v1",
        "source_status": "H06_OFFICIAL_SOURCE_COVERAGE_COMPLETE",
        "source_custody": result["source_custody"],
        "normalization": result["normalization"],
        "joint_owner_semantics": result["joint_owner"]["status"],
        "footnote_linkage": result["footnote_linkage"]["status"],
        "lineage": result["lineage"],
        "routine_history": result["routine_history"],
        "owner_year": result["owner_year"],
        "signal_gate_counts": result["signal"],
        "decision_map": result["decision_map"],
        "identity_precheck": result["signal"]["identity_precheck"],
        "enrichment_targets": result["enrichment"],
        "logical_tables": logical,
        "determinism": {"run_a": {key: value["logical_sha256"] for key, value in logical.items()}, "run_b": run_b_hashes, "all_match": True},
        "remaining_blockers": remaining,
        "economic_boundary": result["economic_boundary"],
    }
    enrichment = {
        **common,
        "schema": "h06-late-stage-enrichment-targets-v1",
        "status": "H06_LATE_STAGE_ENRICHMENT_TARGETS_READY_NOT_ACQUIRED",
        "counts": result["enrichment"],
        "cover_targets": logical["cover_enrichment_targets"],
        "raw_sip_targets": logical["raw_sip_targets"],
        "request_policy": {"timeframe": "1Day", "feed": "SIP", "adjustment": "raw"},
        "remaining_work": remaining,
    }
    structural = {
        **common,
        "schema": "h06_full_structural_admission_v1",
        "status": "H06_CORE_POPULATION_READY_LATE_ENRICHMENT_PENDING",
        "upstream_acquisition_status": "H06_OFFICIAL_SOURCE_COVERAGE_COMPLETE",
        "structural_admission_performed": False,
        "core_population_performed": True,
        "signal_gate_counts": result["signal"],
        "primary_exclusions": result["signal"]["primary_exclusions"],
        "decision_map": result["decision_map"],
        "deterministic_outputs": {key: value["logical_sha256"] for key, value in logical.items()},
        "remaining_blockers": remaining,
        "economic_boundary": "NO_H06_SCORE_OR_OUTCOME_ACCESS",
    }
    owner = {
        **common,
        "schema": "h06_owner_year_classification_v1",
        "classification_performed": True,
        "years": result["owner_year"],
        "routine_history": {**result["routine_history"], "logical_sha256": logical["routine_history"]["logical_sha256"]},
        "owner_year_logical_sha256": logical["owner_year_classification"]["logical_sha256"],
        "remaining_blockers": remaining,
        "economic_boundary": "NO_H06_SCORE_OR_OUTCOME_ACCESS",
    }
    receipt = {
        **common,
        "schema": "h06-offline-core-population-receipt-v1",
        "database": result["database"],
        "logical_tables": logical,
        "normalization": result["normalization"],
        "signal": result["signal"],
        "decision_map": result["decision_map"],
        "enrichment": result["enrichment"],
        "local_artifacts_staged": False,
    }
    lineage = {
        **common,
        "schema": "h06-lineage-population-receipt-v1",
        **result["lineage"],
        "logical_table": logical["lineage_versions"],
        "unresolved_policy": "fail closed at and after plausible amendment visibility; preserve earlier original visibility",
    }
    schema = {
        **common,
        "schema": "h06-sec-bulk-schema-receipt-v1",
        **result["schema"],
    }
    joint = {**common, "schema": "h06-joint-owner-semantics-receipt-v1", **result["joint_owner"]}
    footnote = {**common, "schema": "h06-footnote-linkage-receipt-v1", **result["footnote_linkage"]}
    holdout = {**common, "schema": "h06-true-holdout-signal-custody-v1", **result["true_holdout"]}
    return {
        CORE_MANIFEST: core,
        ENRICHMENT_MANIFEST: enrichment,
        STRUCTURAL_MANIFEST: structural,
        OWNER_MANIFEST: owner,
        CORE_RECEIPT: receipt,
        LINEAGE_RECEIPT: lineage,
        SCHEMA_RECEIPT: schema,
        JOINT_RECEIPT: joint,
        FOOTNOTE_RECEIPT: footnote,
        HOLDOUT_RECEIPT: holdout,
    }


def _render_report(result: Mapping[str, Any], run_b_hashes: Mapping[str, str]) -> str:
    normalized = result["normalization"]
    lineage = result["lineage"]
    decision = result["decision_map"]
    enrichment = result["enrichment"]
    lines = [
        "# H06 offline core population — 2026-09-15",
        "",
        f"Status: `{STATUS}`.",
        "",
        "This is a deterministic, offline structural population build. It performs no network acquisition, score, issuer notional aggregation, ranking, return, P&L, or backtest.",
        "",
        "## Population",
        "",
        f"- Relevant accessions: {normalized['relevant_accessions']:,}",
        f"- Normalized submissions: {normalized['normalized_submissions']:,}",
        f"- Reporting-owner rows: {normalized['normalized_owners']:,}",
        f"- Non-derivative source transactions: {normalized['normalized_nonderivative_transactions']:,}",
        f"- Transaction-owner observations: {normalized['normalized_transaction_owner_observations']:,}",
        "",
        "## Lineage and owner semantics",
        "",
        f"- Resolved amendments: {lineage['resolved_amendments']:,}",
        f"- Unresolved amendments: {lineage['unresolved_amendments']:,}; all affected future use fails closed.",
        "- Joint-owner status: `JOINT_OWNER_TRANSACTION_EXPANSION_PROVEN`; expansion means jointly reported, not owner-specific actor attribution.",
        "- Footnote status: `SEC_BULK_TRANSACTION_FOOTNOTE_LINKAGE_PROVEN`; only exact transaction field references are used.",
        "",
        "## Decision and enrichment boundary",
        "",
        f"- Monthly decisions: {decision['monthly_decisions']}",
        f"- Mapped core-pass rows: {decision['mapped_core_pass_rows']:,}",
        f"- Cover targets: {enrichment['cover_issuer_month_targets']:,}",
        f"- Raw SIP targets: {enrichment['raw_sip_targets']:,}",
        "- Final identity, shares, and raw price remain pending and were not acquired.",
        "",
        "## Determinism",
        "",
    ]
    for table, value in result["logical_tables"].items():
        lines.append(f"- `{table}`: `{value['logical_sha256']}` (run B `{run_b_hashes[table]}`)")
    lines.extend(["", "No H06 economic admission or promotion is claimed.", ""])
    return "\n".join(lines)


def build(*, determinism_runs: int = 2) -> dict[str, Any]:
    if determinism_runs != 2:
        raise ValueError("exactly_two_determinism_runs_required")
    custody = verify_source_custody()
    schema = inspect_bulk_schemas()
    POPULATION.mkdir(parents=True, exist_ok=True)
    run_a_path = POPULATION / "h06_population.sqlite3"
    run_b_path = POPULATION / "h06_population_run_b.sqlite3"
    run_a = build_database(run_a_path, custody, schema)
    run_b = build_database(run_b_path, custody, schema)
    run_a_hashes = {key: value["logical_sha256"] for key, value in run_a["logical_tables"].items()}
    run_b_hashes = {key: value["logical_sha256"] for key, value in run_b["logical_tables"].items()}
    if run_a_hashes != run_b_hashes:
        raise ValueError("logical_rebuild_hash_mismatch")
    for path, payload in _artifact_payloads(run_a, run_b_hashes).items():
        _write_json(path, payload)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(_render_report(run_a, run_b_hashes), encoding="utf-8")
    inventory = {
        "schema": "h06-local-population-workspace-v1",
        "status": STATUS,
        "files": [
            {"path": str(path.relative_to(REPO)), "bytes": path.stat().st_size}
            for path in sorted(POPULATION.glob("*"))
            if path.is_file()
        ],
        "logical_tables": run_a["logical_tables"],
        "staged": False,
    }
    (POPULATION / "workspace_inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**run_a, "determinism": {"run_a": run_a_hashes, "run_b": run_b_hashes, "all_match": True}}


def verify_tracked() -> dict[str, Any]:
    for path in (
        CORE_MANIFEST,
        ENRICHMENT_MANIFEST,
        STRUCTURAL_MANIFEST,
        OWNER_MANIFEST,
        CORE_RECEIPT,
        LINEAGE_RECEIPT,
        SCHEMA_RECEIPT,
        JOINT_RECEIPT,
        FOOTNOTE_RECEIPT,
        HOLDOUT_RECEIPT,
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        verify_seal(payload)
    core = json.loads(CORE_MANIFEST.read_text(encoding="utf-8"))
    if core["status"] != STATUS or core["determinism"]["all_match"] is not True:
        raise ValueError("core_manifest_status")
    if any(core["economic_boundary"].values()):
        raise ValueError("economic_boundary_violation")
    return {"status": "H06_OFFLINE_CORE_POPULATION_VERIFIED", "read_only": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--determinism-runs", type=int, default=2)
    args = parser.parse_args()
    if args.build:
        result = build(determinism_runs=args.determinism_runs)
        print(canonical_json({"status": result["status"], "determinism": result["determinism"]}))
        return 0
    if args.verify:
        print(canonical_json(verify_tracked()))
        return 0
    parser.error("choose --build or --verify")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
