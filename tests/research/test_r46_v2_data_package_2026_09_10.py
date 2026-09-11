from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.research import build_r46_nyse_calendar_2026_09_10 as calendar_builder
from scripts.research.build_r46_v2_data_package_2026_09_10 import (
    DataPackageContractError,
    build_r46_v2_data_package,
    consume_r46_v2_data_package,
)
from scripts.research.build_r46_v2_data_package_2026_09_10 import (
    main as package_main,
)

CUTOFF = "2026-07-31T23:59:59Z"
AVAILABLE_AT = "2026-07-31T23:00:00Z"
SOURCE_VINTAGE = "alpaca-sip-2026-07-31-close-v1"
RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
REPO = Path(__file__).resolve().parents[2]


def test_expected_event_ids_use_sorted_unique_canonical_semantics() -> None:
    from scripts.research.build_r46_v2_data_package_2026_09_10 import _validate_expected_events

    auction = {
        "events": [
            {"canonical_event_id": "event-b"},
            {"canonical_event_id": "event-a"},
        ]
    }
    assert _validate_expected_events(
        auction,
        expected_event_count=2,
        expected_event_ids=("event-b", "event-a"),
    ) == ["event-a", "event-b"]


def test_dividend_source_validator_rejects_attacker_supplied_row_hash(tmp_path: Path) -> None:
    from scripts.research import build_r46_v2_data_package_2026_09_10 as package_builder

    raw_action = {
        "id": "ca-2024-02-01",
        "ca_type": "dividend",
        "ca_sub_type": "cash",
        "initiating_symbol": "TLT",
        "ex_date": "2024-02-01",
        "cash": "0.28",
        "currency": "USD",
    }
    source_path = tmp_path / "dividends_source.json"
    source_path.write_text(json.dumps({"corporate_actions": [raw_action]}, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    receipt = {
        "dividends_source": {
            "path": source_path.name,
            "sha256": _sha(source_path),
        }
    }
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    row = {
        "permanent_security_identifier": "sec:tlt",
        "action_type": "cash_dividend",
        "effective_date": "2024-02-01",
        "cash_or_ratio": "0.28",
        "source_document_hash": hashlib.sha256(
            json.dumps(raw_action, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "duplicate_event_key": "ca-2024-02-01:2024-02-01",
        "event_timestamp": "2024-02-01T14:30:00+00:00",
        "currency": "USD",
        "available_at": "2024-02-01T14:30:00+00:00",
        "source_row_sha256": "f" * 64,
    }
    with pytest.raises(DataPackageContractError, match="source_row_sha256"):
        package_builder._validate_dividend_raw_hashes(
            receipt,
            receipt_path=receipt_path,
            root=tmp_path,
            dividend_rows=[row],
        )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(payload: dict[str, object], field: str) -> str:
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    value["integrity"].pop(field, None)  # type: ignore[index]
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical_id_digest(ids: list[str]) -> str:
    encoded = json.dumps(sorted(ids), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write(root: Path, relative: str, content: str | bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def _document(root: Path, kind: str, document_id: str, result_status: str | None = None) -> dict[str, object]:
    raw = _write(root, f"raw/{document_id}.pdf", document_id.encode())
    document: dict[str, object] = {
        "document_id": document_id,
        "document_kind": kind,
        "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
        "source_url": f"https://treasurydirect.gov/auctions/{document_id}.pdf",
        "raw_path": str(raw.relative_to(root)),
        "sha256": _sha(raw),
        "security_type": "nominal",
        "instrument_type": "US_TREASURY_SECURITY",
        "issuer_identifier": "US_TREASURY",
        "original_tenor": 30,
        "issue_or_reopening": "original",
        "security_identifier": "912810TM2",
        "auction_session_date": "2024-02-08",
        "published_at": "2024-02-08T13:00:00-05:00" if kind == "announcement" else "2024-02-08T14:00:00-05:00",
        "available_at": "2024-02-08T13:00:00-05:00" if kind == "announcement" else "2024-02-09T05:00:00Z",
        "source_timezone": "America/New_York",
        "publication_at_source": "treasury_document_header" if kind == "announcement" else "official_release_timestamp",
        "retrieved_at": "2026-09-10T08:00:00Z",
        "revision_id": "original",
        "supersedes_document_id": None,
        "correction_or_amendment_chain": [],
        "result_status": result_status if kind == "result" else None,
    }
    if kind == "result":
        document.update(
            {
                "official_publication_date": "2024-02-08",
                "availability_derivation": "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results",
                "result_signal_eligibility": "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT",
                "same_day_signal_allowed": False,
                "publication_time_proven": True,
                "published_at_evidence_only": True,
                "release_time": "14:00",
            }
        )
    return document


def _write_jsonl(root: Path, relative: str, rows: list[dict[str, object]]) -> Path:
    return _write(root, relative, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _write_bars(root: Path, relative: str = "market/split_bars.csv") -> Path:
    rows = []
    for index, day in enumerate(("2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16")):
        open_price = 92.0 + index
        rows.append(
            {
                "permanent_security_identifier": "sec:0001100663:S000004360:C000012090",
                "listing_and_tradability_interval": "2016-02-02/",
                "session_date": day,
                "regular_open_timestamp": f"{day}T14:30:00Z",
                "source_vintage": SOURCE_VINTAGE,
                "available_at": AVAILABLE_AT,
                "adjustment": "split",
                "open": open_price,
                "high": open_price + 1,
                "low": open_price - 1,
                "close": open_price + 0.5,
                "volume": 1_000_000 + index,
            }
        )
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_dividends(root: Path) -> Path:
    path = root / "market/dividends.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_action = {
        "id": "ca-2024-02-01",
        "ca_type": "dividend",
        "ca_sub_type": "cash",
        "initiating_symbol": "TLT",
        "ex_date": "2024-02-01",
        "created_at": "2024-01-20T15:00:00Z",
        "cash": "0.28",
        "currency": "USD",
    }
    raw_action_hash = hashlib.sha256(
        json.dumps(raw_action, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    row = {
        "permanent_security_identifier": "sec:0001100663:S000004360:C000012090",
        "action_type": "cash_dividend",
        "effective_date": "2024-02-01",
        "cash_or_ratio": 0.28,
        "source_document_hash": raw_action_hash,
        "duplicate_event_key": "ca-2024-02-01:2024-02-01",
        "event_timestamp": "2024-02-01T14:30:00Z",
        "currency": "USD",
        "available_at": "2024-02-01T14:30:00Z",
    }
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row), lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
    _write(root, "market/dividends_source.json", json.dumps({"corporate_actions": [raw_action]}, sort_keys=True) + "\n")
    return path


def _write_raw_bars(root: Path) -> Path:
    split = root / "market/split_bars.csv"
    rows = list(csv.DictReader(split.read_text(encoding="utf-8").splitlines()))
    for row in rows:
        row["adjustment"] = "raw"
    path = root / "market/raw_bars.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_treasury_receipt(root: Path, documents: Path) -> Path:
    document_rows = [json.loads(line) for line in documents.read_text(encoding="utf-8").splitlines() if line.strip()]
    xsd = _write(root, "treasury/Auction_v6_0_0.xsd", b"<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema' version='6.0.0'/>")
    inventory_payload: dict[str, object] = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "events": [{"canonical_event_id": "2024-02-08-30Y-ORIGINAL"}],
        "integrity": {"canonicalization": "test", "inventory_sha256": ""},
    }
    inventory_payload["integrity"]["inventory_sha256"] = _canonical_hash(inventory_payload, "inventory_sha256")  # type: ignore[index]
    inventory = _write(root, "treasury/inventory.json", json.dumps(inventory_payload, sort_keys=True) + "\n")
    raw_artifacts = []
    for row in document_rows:
        raw_path = documents.parent / row["raw_path"]
        raw_artifacts.append(
            {
                "document_id": row["document_id"],
                "document_kind": row["document_kind"],
                "canonical_event_id": row["canonical_event_id"],
                "path": row["raw_path"],
                "sha256": _sha(raw_path),
                "source_url": row["source_url"],
                "schema_version": "v6_0_0",
                "revision_id": row["revision_id"],
                "supersedes_document_id": row["supersedes_document_id"],
            }
        )
    payload: dict[str, object] = {
        "schema": "r46-treasury-xml-acquisition-v2",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "status": "COMPLETE",
        "economic_execution": False,
        "economic_run": False,
        "historical_cutoff": CUTOFF,
        "acquisition_time": "2026-09-10T08:00:00Z",
        "inventory": {
            "path": str(inventory.relative_to(root)),
            "sha256": _sha(inventory),
            "schema": "r46-treasury-acquisition-inventory-v1",
            "pinned": True,
        },
        "expected_event_count": 1,
        "expected_event_ids": ["2024-02-08-30Y-ORIGINAL"],
        "documents_path": str(documents.relative_to(root)),
        "documents_sha256": _sha(documents),
        "raw_artifacts": raw_artifacts,
        "xsd": [
            {
                "source_url": "https://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd",
                "path": str(xsd.relative_to(root)),
                "sha256": _sha(xsd),
                "schema_version": "v6_0_0",
                "declared_version": "6.0.0",
                "validation_status": "SCHEMA_VALIDATED",
                "validation_tool": "/usr/bin/xmllint",
                "validation_tool_version": "using libxml version 20913",
                "validation_results": [
                    {"document_id": row["document_id"], "validation_status": "SCHEMA_VALIDATED"}
                    for row in document_rows
                ],
            }
        ],
        "source_routes": [],
        "publication_basis": {
            "announcement": "conservative_end_of_day_preceding_auction_day",
            "result": "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results",
        },
        "publication_evidence": {
            "result_release_time_missing_event_ids": [],
            "result_release_time_missing_event_count": 0,
            "result_release_time_missing_event_ids_canonical_digest_sha256": _canonical_id_digest([]),
            "official_publication_date_basis": "result_XML_AuctionDate",
            "availability_derivation": "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results",
            "server_last_modified_is_publication_proof": False,
            "http_date_is_publication_proof": False,
            "retrieval_time_is_publication_proof": False,
            "next_session_open_policy": "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT",
            "same_day_result_signals_allowed": False,
            "same_day": False,
        },
        "acquisition_audit": {
            "inventory_event_ids": {"ids": ["2024-02-08-30Y-ORIGINAL"], "count": 1, "canonical_digest_sha256": _canonical_id_digest(["2024-02-08-30Y-ORIGINAL"])},
            "announcement_event_ids": {"ids": ["2024-02-08-30Y-ORIGINAL"], "count": 1, "canonical_digest_sha256": _canonical_id_digest(["2024-02-08-30Y-ORIGINAL"])},
            "result_event_ids": {"ids": ["2024-02-08-30Y-ORIGINAL"], "count": 1, "canonical_digest_sha256": _canonical_id_digest(["2024-02-08-30Y-ORIGINAL"])},
            "missing_release_time_event_ids": {"ids": [], "count": 0, "canonical_digest_sha256": _canonical_id_digest([])},
            "event_id_sets_equal": True,
        },
        "economic_fields_filtered": True,
        "integrity": {"canonicalization": "test", "receipt_sha256": ""},
    }
    payload["integrity"]["receipt_sha256"] = _canonical_hash(payload, "receipt_sha256")  # type: ignore[index]
    return _write(root, "treasury/receipt.json", json.dumps(payload, sort_keys=True) + "\n")


def _write_calendar_receipt(root: Path, calendar: Path) -> Path:
    lock_path = root / "uv.lock"
    shutil.copyfile(REPO / "uv.lock", lock_path)
    dependency = calendar_builder.read_dependency_pin(lock_path, installed_version="5.4.0")
    provenance = calendar_builder._dependency_provenance(dependency, lock_path=lock_path, repo=root)
    raw_hash = _sha(calendar)
    payload: dict[str, object] = {
        "schema": "r46-nyse-regular-calendar-receipt-v1",
        "status": "COMPLETE",
        "artifact_schema": "r46-nyse-regular-calendar-v1",
        "calendar": "NYSE",
        "venue": "XNYS",
        "window": {"start": "2016-01-01", "end_exclusive": "2026-08-01"},
        "available_at": "2026-05-27T02:42:06Z",
        "records": {
            "path": str(calendar.relative_to(root)),
            "sha256": raw_hash,
            "raw_sha256": raw_hash,
            "row_count": len([line for line in calendar.read_text(encoding="utf-8").splitlines() if line.strip()]),
            "format": "UTF-8 JSONL",
            "record_schema": "r46-nyse-regular-calendar-v1",
        },
        "records_path": str(calendar.relative_to(root)),
        "records_sha256": raw_hash,
        "row_count": len([line for line in calendar.read_text(encoding="utf-8").splitlines() if line.strip()]),
        "provenance": provenance,
        "publication": json.loads(json.dumps(calendar_builder.RECEIPT_PUBLICATION_SEMANTICS)),
        "economic_execution": False,
        "economic_data": False,
        "network_used": False,
        "integrity": {"canonicalization": calendar_builder.RECEIPT_CANONICALIZATION, "receipt_sha256": ""},
    }
    payload["integrity"]["receipt_sha256"] = calendar_builder.canonical_receipt_hash(payload)  # type: ignore[index]
    return _write(root, "market/calendar.receipt.json", json.dumps(payload, sort_keys=True) + "\n")


def _write_eligibility(root: Path) -> Path:
    common = {
        "canonical_symbol": "TLT",
        "historical_symbol": "TLT",
        "permanent_security_identifier": "sec:0001100663:S000004360:C000012090",
        "provider_asset_id": "asset-tlt",
        "valid_from": "2002-07-22",
        "first_observed_bar": "2016-01-04",
        "last_observed_bar": "2026-07-31",
        "security_identifier": "464287432",
        "available_at": AVAILABLE_AT,
    }
    rows = [
        {
            **common,
            "venue": "NYSE Arca",
            "listing_and_tradability_interval": "2002-07-22/2016-02-02",
            "valid_to": "2016-02-02",
            "identity_basis": "issuer_historical_filing",
            "source_url": "https://www.sec.gov/Archives/edgar/data/1100663/000119312516564893/d177059d485apos.htm",
        },
        {
            **common,
            "venue": "NASDAQ",
            "listing_and_tradability_interval": "2016-02-02/",
            "valid_from": "2016-02-02",
            "valid_to": None,
            "identity_basis": "exchange_archive",
            "source_url": "https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2016-20",
        },
    ]
    return _write_jsonl(root, "market/eligibility.jsonl", rows)


def _write_calendar(root: Path) -> Path:
    days = ("2024-02-01", "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16")
    rows = [
        {
            "regular_session_date": day,
            "regular_open_timestamp": f"{day}T14:30:00Z",
            "regular_close_timestamp": f"{day}T21:00:00Z",
            "holiday_or_closure_reason": None,
        }
        for day in days
    ]
    return _write_jsonl(root, "market/calendar.jsonl", rows)


def _write_identity_source(root: Path, eligibility: Path) -> Path:
    records = [json.loads(line) for line in eligibility.read_text(encoding="utf-8").splitlines() if line.strip()]
    payload: dict[str, object] = {
        "schema": "r46-tlt-historical-identity-evidence-v1",
        "source_vintage": "sec-nasdaq-tlt-2026-07-31-v1",
        "records": records,
        "integrity": {
            "canonicalization": "UTF-8 JSON sorted object keys compact separators ensure_ascii=false; omit integrity.artifact_sha256",
            "artifact_sha256": "",
        },
    }
    payload["integrity"]["artifact_sha256"] = _canonical_hash(payload, "artifact_sha256")  # type: ignore[index]
    return _write(root, "market/identity_source.json", json.dumps(payload, sort_keys=True) + "\n")


def _write_receipt(root: Path, *, parity: bool = True) -> Path:
    raw = _write_raw_bars(root)
    split = root / "market/split_bars.csv"
    assert split.is_file()
    dividends_source = root / "market/dividends_source.json"
    assert dividends_source.is_file()
    identity_source = root / "market/identity_source.json"
    assert identity_source.is_file()
    identity_provider_source = _write(
        root,
        "market/identity_provider_source.json",
        '{"asset":{"exchange":"NASDAQ","id":"asset-tlt","status":"active","symbol":"TLT","tradable":true}}\n',
    )
    payload = {
        "schema": "r46-tlt-market-data-acquisition-v2",
        "status": "COMPLETE",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "acquisition_mode": "retrospective",
        "retrospective_acquisition": True,
        "historical_cutoff": CUTOFF,
        "network_used": True,
        "credentials_used": True,
        "economic_execution": False,
        "raw_split_key_parity": parity,
        "acquisition_time": "2026-09-10T08:00:00Z",
        "availability_basis": {
            "bars": "session_close_from_supplied_nyse_calendar",
            "dividends": "ex_date_regular_open_from_supplied_nyse_calendar",
        },
        "integrity": {"canonicalization": "UTF-8 JSON sorted compact; omit integrity.receipt_sha256", "receipt_sha256": ""},
        "artifacts": {
            "raw_bars": {"path": str(raw.relative_to(root)), "sha256": _sha(raw), "source_vintage": SOURCE_VINTAGE, "available_at": AVAILABLE_AT},
            "split_bars": {"path": str(split.relative_to(root)), "sha256": _sha(split), "source_vintage": SOURCE_VINTAGE, "available_at": AVAILABLE_AT},
            "dividends": {
                "path": str((root / "market/dividends.csv").relative_to(root)),
                "sha256": _sha(root / "market/dividends.csv"),
                "source_vintage": SOURCE_VINTAGE,
                "available_at": AVAILABLE_AT,
            },
            "eligibility": {"source_vintage": "sec-nasdaq-tlt-2026-07-31-v1", "available_at": AVAILABLE_AT},
        },
        "dividends_source": {"path": str(dividends_source.relative_to(root)), "sha256": _sha(dividends_source)},
        "identity_source": {"path": str(identity_source.relative_to(root)), "sha256": _sha(identity_source)},
        "identity_provider_source": {"path": str(identity_provider_source.relative_to(root)), "sha256": _sha(identity_provider_source)},
    }
    payload["integrity"]["receipt_sha256"] = _canonical_hash(payload, "receipt_sha256")  # type: ignore[index]
    return _write(root, "market/acquisition_receipt.json", json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _fixture(root: Path) -> dict[str, Path | dict[str, dict[str, str]]]:
    document_rows = [_document(root, "announcement", "announcement-20240208"), _document(root, "result", "result-20240208", "COMPLETED")]
    for row in document_rows:
        original_raw = root / row["raw_path"]
        output_raw = root / "treasury/raw_xml" / f"{row['document_id']}.xml"
        output_raw.parent.mkdir(parents=True, exist_ok=True)
        output_raw.write_bytes(original_raw.read_bytes())
        row["raw_path"] = str(output_raw.relative_to(root / "treasury"))
        row["sha256"] = _sha(output_raw)
    documents = _write_jsonl(root, "treasury/documents.jsonl", document_rows)
    bars = _write_bars(root)
    dividends = _write_dividends(root)
    eligibility = _write_eligibility(root)
    identity_source = _write_identity_source(root, eligibility)
    calendar = _write_calendar(root)
    receipt = _write_receipt(root)
    treasury_receipt = _write_treasury_receipt(root, documents)
    public_source_receipt = _write(
        root,
        "treasury/public_source_evidence_receipt.json",
        json.dumps(
            {
                "schema": "r46-treasury-public-source-evidence-receipt-v1",
                "events": [{"canonical_event_id": "2024-02-08-30Y-ORIGINAL"}],
            },
            sort_keys=True,
        )
        + "\n",
    )
    evidence_payload: dict[str, object] = {
        "schema": "r46-treasury-publication-availability-evidence-v2",
        "status": "COMPLETE",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "result_availability_policy": "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results",
        "result_signal_eligibility": "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT",
        "same_day_signal_allowed": False,
        "actual_release_time_role": "evidence_only",
        "server_last_modified_is_publication_proof": False,
        "expected_event_count": 1,
        "expected_event_ids": ["2024-02-08-30Y-ORIGINAL"],
        "expected_event_ids_sha256": _canonical_id_digest(["2024-02-08-30Y-ORIGINAL"]),
        "expected_event_ids_digest": _canonical_id_digest(["2024-02-08-30Y-ORIGINAL"]),
        "missing_release": {
            "expected_count": 0,
            "expected_event_ids": [],
            "expected_event_ids_sha256": _canonical_id_digest([]),
            "expected_event_ids_digest": _canonical_id_digest([]),
            "derivation": "normalized_result_rows_published_at_null",
            "source_field": "result.published_at",
        },
        "sources": {
            "receipt": {
                "path": str(treasury_receipt.relative_to(root)),
                "schema": "r46-treasury-xml-acquisition-v2",
                "raw_sha256": _sha(treasury_receipt),
                "byte_size": treasury_receipt.stat().st_size,
            },
            "documents": {
                "path": str(documents.relative_to(root)),
                "schema": "r46-treasury-auction-documents-v2",
                "raw_sha256": _sha(documents),
                "byte_size": documents.stat().st_size,
            },
        },
        "integrity": {"canonicalization": "test", "evidence_sha256": ""},
    }
    evidence_payload["integrity"]["evidence_sha256"] = _canonical_hash(evidence_payload, "evidence_sha256")  # type: ignore[index]
    publication_evidence = _write(
        root,
        "treasury/publication_availability_evidence.json",
        json.dumps(evidence_payload, sort_keys=True) + "\n",
    )
    calendar_receipt = _write_calendar_receipt(root, calendar)
    metadata = {
        "documents": {"source_vintage": "treasury-official-archive-2026-09-10", "available_at": AVAILABLE_AT},
        "bars": {"source_vintage": SOURCE_VINTAGE, "available_at": AVAILABLE_AT},
        "dividends": {"source_vintage": SOURCE_VINTAGE, "available_at": AVAILABLE_AT},
        "eligibility": {"source_vintage": "sec-nasdaq-tlt-2026-07-31-v1", "available_at": AVAILABLE_AT},
        "calendar": {"source_vintage": "pandas-market-calendars-5.4.0", "available_at": "2026-05-27T02:42:06Z"},
    }
    governance_payload: dict[str, object] = {
        "test_fixture": True,
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": CUTOFF,
        "publication_availability_policy": {
            "result_availability_policy": "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results",
            "result_signal_eligibility": "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT",
            "same_day_signal_allowed": False,
            "actual_release_time_role": "evidence_only",
            "server_last_modified_is_publication_proof": False,
            "expected_event_count": 1,
            "expected_event_ids": ["2024-02-08-30Y-ORIGINAL"],
            "expected_event_ids_sha256": _canonical_id_digest(["2024-02-08-30Y-ORIGINAL"]),
            "expected_event_ids_digest": _canonical_id_digest(["2024-02-08-30Y-ORIGINAL"]),
            "missing_release": {
                "expected_count": 0,
                "expected_event_ids": [],
                "expected_event_ids_sha256": _canonical_id_digest([]),
                "expected_event_ids_digest": _canonical_id_digest([]),
                "derivation": "normalized_result_rows_published_at_null",
                "source_field": "result.published_at",
            },
            "source_receipt": {
                "path": str(public_source_receipt.relative_to(root)),
                "schema": "r46-treasury-public-source-evidence-receipt-v1",
                "raw_sha256": _sha(public_source_receipt),
            },
            "source_evidence": {
                "path": str(publication_evidence.relative_to(root)),
                "schema": "r46-treasury-publication-availability-evidence-v2",
                "raw_sha256": _sha(publication_evidence),
                "byte_size": publication_evidence.stat().st_size,
            },
            "canonicalization": "sorted unique event IDs encoded as compact UTF-8 JSON array",
        },
        "integrity": {"canonicalization": "UTF-8 JSON sorted compact; omit integrity.manifest_sha256", "manifest_sha256": ""},
    }
    governance_payload["integrity"]["manifest_sha256"] = _canonical_hash(governance_payload, "manifest_sha256")  # type: ignore[index]
    governance = _write(root, "governance.json", json.dumps(governance_payload) + "\n")
    return {
        "governance": governance,
        "documents": documents,
        "bars": bars,
        "dividends": dividends,
        "eligibility": eligibility,
        "identity_source": identity_source,
        "calendar": calendar,
        "receipt": receipt,
        "treasury_receipt": treasury_receipt,
        "calendar_receipt": calendar_receipt,
        "metadata": metadata,
    }


def test_build_v2_package_hashes_five_artifacts_and_records_retrospective_sources(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "package.json"

    result = build_r46_v2_data_package(
        fixture["governance"],
        documents=fixture["documents"],
        bars=fixture["bars"],
        dividends=fixture["dividends"],
        eligibility=fixture["eligibility"],
        calendar=fixture["calendar"],
        acquisition_receipt=fixture["receipt"],
        treasury_receipt=fixture["treasury_receipt"],
        calendar_receipt=fixture["calendar_receipt"],
        artifact_metadata=fixture["metadata"],
        expected_event_count=1,
        expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
        coverage={
            "event_episodes": 1,
            "holding_intervals": 5,
            "missing_rate": 0.0,
            "fifth_open_exit": 1,
            "capacity": None,
            "capacity_status": "PRE_RUN_CAPACITY_PENDING",
        },
        output=output,
        repo_root=tmp_path,
    )

    assert result["status"] == "DATA_PACKAGE_READY"
    assert set(result["artifacts"]) == {"documents", "bars", "dividends", "eligibility", "calendar"}
    assert result["acquisition"]["mode"] == "retrospective"
    assert result["artifacts"]["bars"]["sha256"] == _sha(fixture["bars"])
    assert result["artifacts"]["bars"]["source_vintage"] == SOURCE_VINTAGE
    assert result["integrity"]["manifest_sha256"]
    assert result["expected_event_count"] == 1
    assert result["expected_event_ids"] == ["2024-02-08-30Y-ORIGINAL"]
    assert result["coverage"]["event_episodes"] == 1
    assert result["coverage"]["holding_intervals"] == 5
    assert result["coverage"]["fifth_open_exit"] == 1
    assert result["coverage"]["capacity"] is None
    assert result["coverage"]["capacity_status"] == "PRE_RUN_CAPACITY_PENDING"
    assert result["publication_availability_policy"] == json.loads(fixture["governance"].read_text(encoding="utf-8"))["publication_availability_policy"]
    assert result["publication_availability_policy"]["source_receipt"]["schema"] == "r46-treasury-public-source-evidence-receipt-v1"
    assert result["publication_availability_policy"]["source_evidence"]["schema"] == "r46-treasury-publication-availability-evidence-v2"
    assert result["receipts"]["treasury_xml"]["acquisition_audit"]["missing_release_time_event_ids"]["ids"] == []
    assert result["coverage"]["schedule_linkage"]["policy"] == "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
    assert result["coverage"]["schedule_linkage"]["event_count"] == 1
    assert result["coverage"]["schedule_linkage"]["events"][0]["entry_open_timestamp"] == "2024-02-09T14:30:00Z"
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["governance_manifest_sha256"] == json.loads(fixture["governance"].read_text())["integrity"]["manifest_sha256"]
    assert saved["artifacts"]["calendar"]["available_at"] == "2026-05-27T02:42:06Z"
    consumed = consume_r46_v2_data_package(output, repo_root=tmp_path)
    assert consumed["schema"] == "r46-treasury-auction-unwind-data-package-v2"
    assert consumed["artifacts"] == saved["artifacts"]


def test_package_accepts_hash_pinned_official_historical_identity_source(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    historical_identity = [json.loads(line) for line in fixture["eligibility"].read_text(encoding="utf-8").splitlines() if line.strip()]
    historical_identity[0]["identity_basis"] = "issuer_historical_filing"
    historical_identity[0]["source_url"] = "https://www.sec.gov/Archives/edgar/data/1100663/000119312516564893/d177059d485apos.htm"
    eligibility = _write_jsonl(tmp_path, "market/historical_eligibility.jsonl", historical_identity)

    result = build_r46_v2_data_package(
        fixture["governance"],
        documents=fixture["documents"],
        bars=fixture["bars"],
        dividends=fixture["dividends"],
        eligibility=eligibility,
        calendar=fixture["calendar"],
        acquisition_receipt=fixture["receipt"],
        treasury_receipt=fixture["treasury_receipt"],
        calendar_receipt=fixture["calendar_receipt"],
        artifact_metadata=fixture["metadata"],
        expected_event_count=1,
        expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
        coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
        output=tmp_path / "historical-package.json",
        repo_root=tmp_path,
    )

    assert result["status"] == "DATA_PACKAGE_READY"
    assert result["coverage"]["tlt_identity_rows"] == 2


def test_package_rejects_treasury_receipt_without_schema_validation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipt = json.loads(fixture["treasury_receipt"].read_text(encoding="utf-8"))
    receipt["xsd"][0]["validation_status"] = "PROVENANCE_PINNED_NOT_SCHEMA_VALIDATED"  # type: ignore[index]
    receipt["integrity"]["receipt_sha256"] = _canonical_hash(receipt, "receipt_sha256")  # type: ignore[index]
    fixture["treasury_receipt"].write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(DataPackageContractError, match="schema validation"):
        build_r46_v2_data_package(
            fixture["governance"],
            documents=fixture["documents"],
            bars=fixture["bars"],
            dividends=fixture["dividends"],
            eligibility=fixture["eligibility"],
            calendar=fixture["calendar"],
            acquisition_receipt=fixture["receipt"],
            treasury_receipt=fixture["treasury_receipt"],
            calendar_receipt=fixture["calendar_receipt"],
            artifact_metadata=fixture["metadata"],
            expected_event_count=1,
            expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
            coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
            output=tmp_path / "package.json",
            repo_root=tmp_path,
        )


def test_package_rejects_treasury_receipt_without_xsd_document_lineage(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipt = json.loads(fixture["treasury_receipt"].read_text(encoding="utf-8"))
    receipt["xsd"][0].pop("validation_results")  # type: ignore[index]
    receipt["integrity"]["receipt_sha256"] = _canonical_hash(receipt, "receipt_sha256")  # type: ignore[index]
    fixture["treasury_receipt"].write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(DataPackageContractError, match="validation lineage"):
        build_r46_v2_data_package(
            fixture["governance"],
            documents=fixture["documents"],
            bars=fixture["bars"],
            dividends=fixture["dividends"],
            eligibility=fixture["eligibility"],
            calendar=fixture["calendar"],
            acquisition_receipt=fixture["receipt"],
            treasury_receipt=fixture["treasury_receipt"],
            calendar_receipt=fixture["calendar_receipt"],
            artifact_metadata=fixture["metadata"],
            expected_event_count=1,
            expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
            coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
            output=tmp_path / "package.json",
            repo_root=tmp_path,
        )


def test_package_recomputes_raw_split_row_key_parity(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    raw_path = tmp_path / "market/raw_bars.csv"
    raw_path.write_text(raw_path.read_text(encoding="utf-8").replace("2024-02-09", "2024-02-08", 1), encoding="utf-8")
    receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
    receipt["artifacts"]["raw_bars"]["sha256"] = _sha(raw_path)  # type: ignore[index]
    receipt["integrity"]["receipt_sha256"] = _canonical_hash(receipt, "receipt_sha256")  # type: ignore[index]
    fixture["receipt"].write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(DataPackageContractError, match="raw_split_key_parity"):
        build_r46_v2_data_package(
            fixture["governance"],
            documents=fixture["documents"],
            bars=fixture["bars"],
            dividends=fixture["dividends"],
            eligibility=fixture["eligibility"],
            calendar=fixture["calendar"],
            acquisition_receipt=fixture["receipt"],
            treasury_receipt=fixture["treasury_receipt"],
            calendar_receipt=fixture["calendar_receipt"],
            artifact_metadata=fixture["metadata"],
            expected_event_count=1,
            expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
            coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
            output=tmp_path / "package.json",
            repo_root=tmp_path,
        )


def test_package_recomputes_corporate_action_raw_row_hashes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    dividends_path = tmp_path / "market/dividends.csv"
    dividend_rows = list(csv.DictReader(dividends_path.read_text(encoding="utf-8").splitlines()))
    dividend_rows[0]["source_document_hash"] = "a" * 64
    with dividends_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(dividend_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(dividend_rows)
    receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
    receipt["artifacts"]["dividends"]["sha256"] = _sha(dividends_path)  # type: ignore[index]
    receipt["integrity"]["receipt_sha256"] = _canonical_hash(receipt, "receipt_sha256")  # type: ignore[index]
    fixture["receipt"].write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(DataPackageContractError, match="dividend raw row hash"):
        build_r46_v2_data_package(
            fixture["governance"],
            documents=fixture["documents"],
            bars=fixture["bars"],
            dividends=fixture["dividends"],
            eligibility=fixture["eligibility"],
            calendar=fixture["calendar"],
            acquisition_receipt=fixture["receipt"],
            treasury_receipt=fixture["treasury_receipt"],
            calendar_receipt=fixture["calendar_receipt"],
            artifact_metadata=fixture["metadata"],
            expected_event_count=1,
            expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
            coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
            output=tmp_path / "package.json",
            repo_root=tmp_path,
        )


def test_package_rejects_missing_retrospective_metadata_before_admission(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    metadata = dict(fixture["metadata"])
    metadata.pop("bars")

    with pytest.raises(DataPackageContractError, match="bars metadata"):
        build_r46_v2_data_package(
            fixture["governance"],
            documents=fixture["documents"],
            bars=fixture["bars"],
            dividends=fixture["dividends"],
            eligibility=fixture["eligibility"],
            calendar=fixture["calendar"],
            acquisition_receipt=fixture["receipt"],
            treasury_receipt=fixture["treasury_receipt"],
            calendar_receipt=fixture["calendar_receipt"],
            artifact_metadata=metadata,
            expected_event_count=1,
            expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
            coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
            output=tmp_path / "package.json",
            repo_root=tmp_path,
        )


def test_package_rejects_nonparity_raw_split_acquisition(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipt = _write_receipt(tmp_path, parity=False)

    with pytest.raises(DataPackageContractError, match="raw_split_key_parity"):
        build_r46_v2_data_package(
            fixture["governance"],
            documents=fixture["documents"],
            bars=fixture["bars"],
            dividends=fixture["dividends"],
            eligibility=fixture["eligibility"],
            calendar=fixture["calendar"],
            acquisition_receipt=receipt,
            treasury_receipt=fixture["treasury_receipt"],
            calendar_receipt=fixture["calendar_receipt"],
            artifact_metadata=fixture["metadata"],
            expected_event_count=1,
            expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
            coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
            output=tmp_path / "package.json",
            repo_root=tmp_path,
        )


def test_package_publication_is_atomic_and_no_overwrite(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    kwargs = {
        "documents": fixture["documents"],
        "bars": fixture["bars"],
        "dividends": fixture["dividends"],
        "eligibility": fixture["eligibility"],
        "calendar": fixture["calendar"],
        "acquisition_receipt": fixture["receipt"],
        "treasury_receipt": fixture["treasury_receipt"],
        "calendar_receipt": fixture["calendar_receipt"],
        "artifact_metadata": fixture["metadata"],
        "expected_event_count": 1,
        "expected_event_ids": ("2024-02-08-30Y-ORIGINAL",),
        "coverage": {"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
        "output": tmp_path / "package.json",
        "repo_root": tmp_path,
    }
    build_r46_v2_data_package(fixture["governance"], **kwargs)
    original = (tmp_path / "package.json").read_bytes()
    with pytest.raises((FileExistsError, DataPackageContractError)):
        build_r46_v2_data_package(fixture["governance"], **kwargs)
    assert (tmp_path / "package.json").read_bytes() == original


def test_consumer_rejects_rehashed_package_with_inconsistent_coverage(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "package.json"
    result = build_r46_v2_data_package(
        fixture["governance"],
        documents=fixture["documents"],
        bars=fixture["bars"],
        dividends=fixture["dividends"],
        eligibility=fixture["eligibility"],
        calendar=fixture["calendar"],
        acquisition_receipt=fixture["receipt"],
        treasury_receipt=fixture["treasury_receipt"],
        calendar_receipt=fixture["calendar_receipt"],
        artifact_metadata=fixture["metadata"],
        expected_event_count=1,
        expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
        coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
        output=output,
        repo_root=tmp_path,
    )
    tampered = json.loads(output.read_text(encoding="utf-8"))
    tampered["coverage"]["event_episodes"] = 99
    tampered["integrity"]["manifest_sha256"] = _canonical_hash(tampered, "manifest_sha256")
    output.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
    assert result["status"] == "DATA_PACKAGE_READY"
    with pytest.raises(DataPackageContractError, match="coverage"):
        consume_r46_v2_data_package(output, repo_root=tmp_path)


def test_consumer_rejects_changed_pinned_receipt(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "package.json"
    build_r46_v2_data_package(
        fixture["governance"],
        documents=fixture["documents"],
        bars=fixture["bars"],
        dividends=fixture["dividends"],
        eligibility=fixture["eligibility"],
        calendar=fixture["calendar"],
        acquisition_receipt=fixture["receipt"],
        treasury_receipt=fixture["treasury_receipt"],
        calendar_receipt=fixture["calendar_receipt"],
        artifact_metadata=fixture["metadata"],
        expected_event_count=1,
        expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
        coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
        output=output,
        repo_root=tmp_path,
    )
    receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
    receipt["network_used"] = False
    receipt["integrity"]["receipt_sha256"] = _canonical_hash(receipt, "receipt_sha256")  # type: ignore[index]
    fixture["receipt"].write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(DataPackageContractError, match="receipt"):
        consume_r46_v2_data_package(output, repo_root=tmp_path)


def test_package_cli_accepts_explicit_five_artifact_metadata(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    metadata_path = _write(tmp_path, "metadata.json", json.dumps(fixture["metadata"], sort_keys=True))
    coverage_path = _write(
        tmp_path,
        "coverage.json",
        json.dumps({"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1}),
    )
    output = tmp_path / "package.json"

    assert package_main(
        [
            str(fixture["governance"]),
            "--documents",
            str(fixture["documents"]),
            "--bars",
            str(fixture["bars"]),
            "--dividends",
            str(fixture["dividends"]),
            "--eligibility",
            str(fixture["eligibility"]),
            "--calendar",
            str(fixture["calendar"]),
            "--acquisition-receipt",
            str(fixture["receipt"]),
            "--treasury-receipt",
            str(fixture["treasury_receipt"]),
            "--calendar-receipt",
            str(fixture["calendar_receipt"]),
            "--expected-event-count",
            "1",
            "--expected-event-id",
            "2024-02-08-30Y-ORIGINAL",
            "--coverage-json",
            str(coverage_path),
            "--artifact-metadata-json",
            str(metadata_path),
            "--output",
            str(output),
            "--repo-root",
            str(tmp_path),
        ]
    ) == 0
    assert output.is_file()


def test_package_rejects_caller_event_universe_alternative_to_governance_pin(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    governance = json.loads(fixture["governance"].read_text(encoding="utf-8"))
    governance["test_fixture"] = False
    governance["event_universe"] = {
        "event_ids": ["2024-02-08-30Y-ORIGINAL"],
        "event_count": 1,
        "canonical_digest_sha256": _canonical_id_digest(["2024-02-08-30Y-ORIGINAL"]),
    }
    governance["integrity"]["manifest_sha256"] = _canonical_hash(governance, "manifest_sha256")  # type: ignore[index]
    fixture["governance"].write_text(json.dumps(governance, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(DataPackageContractError, match="event universe"):
        build_r46_v2_data_package(
            fixture["governance"],
            documents=fixture["documents"],
            bars=fixture["bars"],
            dividends=fixture["dividends"],
            eligibility=fixture["eligibility"],
            calendar=fixture["calendar"],
            acquisition_receipt=fixture["receipt"],
            treasury_receipt=fixture["treasury_receipt"],
            calendar_receipt=fixture["calendar_receipt"],
            artifact_metadata=fixture["metadata"],
            expected_event_count=1,
            expected_event_ids=("caller-chosen-alternative",),
            coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
            output=tmp_path / "package.json",
            repo_root=tmp_path,
        )


def test_consumer_rejects_tampered_publication_policy_crosslink(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "package.json"
    build_r46_v2_data_package(
        fixture["governance"],
        documents=fixture["documents"],
        bars=fixture["bars"],
        dividends=fixture["dividends"],
        eligibility=fixture["eligibility"],
        calendar=fixture["calendar"],
        acquisition_receipt=fixture["receipt"],
        treasury_receipt=fixture["treasury_receipt"],
        calendar_receipt=fixture["calendar_receipt"],
        artifact_metadata=fixture["metadata"],
        expected_event_count=1,
        expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
        coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
        output=output,
        repo_root=tmp_path,
    )
    package = json.loads(output.read_text(encoding="utf-8"))
    package["publication_availability_policy"]["same_day_signal_allowed"] = True
    package["integrity"]["manifest_sha256"] = _canonical_hash(package, "manifest_sha256")  # type: ignore[index]
    output.write_text(json.dumps(package, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(DataPackageContractError, match="publication policy"):
        consume_r46_v2_data_package(output, repo_root=tmp_path)


def test_builder_rejects_publication_evidence_treasury_source_crosslink(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    evidence_path = tmp_path / "treasury/publication_availability_evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["sources"]["receipt"]["path"] = "treasury/public_source_evidence_receipt.json"  # type: ignore[index]
    evidence["integrity"]["evidence_sha256"] = _canonical_hash(evidence, "evidence_sha256")  # type: ignore[index]
    evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
    governance = json.loads(fixture["governance"].read_text(encoding="utf-8"))
    governance["publication_availability_policy"]["source_evidence"]["raw_sha256"] = _sha(evidence_path)  # type: ignore[index]
    governance["publication_availability_policy"]["source_evidence"]["byte_size"] = evidence_path.stat().st_size  # type: ignore[index]
    governance["integrity"]["manifest_sha256"] = _canonical_hash(governance, "manifest_sha256")  # type: ignore[index]
    fixture["governance"].write_text(json.dumps(governance, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(DataPackageContractError, match="source crosslink"):
        build_r46_v2_data_package(
            fixture["governance"],
            documents=fixture["documents"],
            bars=fixture["bars"],
            dividends=fixture["dividends"],
            eligibility=fixture["eligibility"],
            calendar=fixture["calendar"],
            acquisition_receipt=fixture["receipt"],
            treasury_receipt=fixture["treasury_receipt"],
            calendar_receipt=fixture["calendar_receipt"],
            artifact_metadata=fixture["metadata"],
            expected_event_count=1,
            expected_event_ids=("2024-02-08-30Y-ORIGINAL",),
            coverage={"event_episodes": 1, "holding_intervals": 5, "missing_rate": 0.0, "fifth_open_exit": 1},
            output=tmp_path / "package.json",
            repo_root=tmp_path,
        )
