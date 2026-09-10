from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.research import acquire_r46_treasury_documents_2026_09_10 as acquisition

build_document_record = acquisition.build_document_record
build_tlt_alpaca_query = acquisition.build_tlt_alpaca_query
fetch_official_document = acquisition.fetch_official_document
official_source_routes = acquisition.official_source_routes
sanitized_blocked_receipt = acquisition.sanitized_blocked_receipt


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/research/acquire_r46_treasury_documents_2026_09_10.py"
CAPABILITY_RECEIPT = REPO / "data/research_receipts/r46_treasury_source_capability_2026_09_10.json"
RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v1_2026_09_10"


def _source_body(*, published_at: str = "2024-01-31T08:30:00-05:00", available_at: str = "2024-01-31T08:30:00-05:00") -> bytes:
    return (
        b"%PDF-1.7\n"
        + f"Published At: {published_at}\n".encode()
        + f"Available At: {available_at}\n".encode()
        + b"Auction Date: February 08, 2024\n"
        + b"Original Issue Date: February 15, 2024\n"
        + b"Security Term: 30-Year Bond\n"
        + b"CUSIP Number: 912810TX6\n"
    )


def _metadata(kind: str, identifier: str, *, result_status: str | None = None) -> dict[str, object]:
    return {
        "document_id": identifier,
        "document_kind": kind,
        "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
        "source_timezone": "America/New_York",
        "security_type": "nominal",
        "instrument_type": "US_TREASURY_SECURITY",
        "issuer_identifier": "US_TREASURY",
        "original_tenor": 30,
        "issue_or_reopening": "original",
        "security_identifier": "912810TX6",
        "auction_session_date": "2024-02-08",
        "correction_or_amendment_chain": [],
        "revision_id": "original",
        "supersedes_document_id": None,
        "result_status": result_status,
    }


def test_official_source_routes_are_https_and_cover_archive_query_and_documents() -> None:
    routes = official_source_routes()

    assert routes == {
        "archive": "https://treasurydirect.gov/auctions/announcements-data-results/announcement-results-press-releases/",
        "auction_query": "https://treasurydirect.gov/auctions/auction-query/",
        "fiscal_documents": "https://fiscaldata.treasury.gov/static-data/published-reports/auctions-query/",
    }
    assert all(value.startswith("https://") for value in routes.values())


def test_build_tlt_alpaca_query_freezes_split_and_raw_coverage_without_credentials() -> None:
    query = build_tlt_alpaca_query("split")

    assert query == {
        "symbols": "TLT",
        "timeframe": "1Day",
        "start": "2016-01-01",
        "end": "2026-08-01",
        "feed": "sip",
        "adjustment": "split",
        "limit": 10000,
        "sort": "asc",
    }
    assert "key" not in query and "secret" not in query


def test_build_document_record_keeps_pdf_raw_unadmitted_without_real_extractor(tmp_path: Path) -> None:
    content = _source_body()
    record = build_document_record(
        tmp_path,
        "raw/announcement.pdf",
        content,
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
        retrieved_at="2026-09-10T08:00:00Z",
        **_metadata("announcement", "announcement-20240131"),
    )

    assert record["status"] == "RAW_UNADMITTED"
    assert record["admitted"] is False
    assert record["error_code"] == "EXTRACTOR_UNAVAILABLE"
    assert record["raw_path"] == "raw/announcement.pdf"
    assert record["sha256"] == hashlib.sha256(content).hexdigest()
    assert record["published_at"] is None
    assert record["available_at"] is None
    assert record["timestamp_evidence"] is None
    assert record["auction_session_date"] == "2024-02-08"
    assert record["credential_fields_persisted"] is False


def test_build_document_record_does_not_parse_source_body_cutoff_text(tmp_path: Path) -> None:
    record = build_document_record(
        tmp_path,
        "raw/announcement.pdf",
        _source_body(
            published_at="2024-02-08T16:16:00-05:00",
            available_at="2024-02-08T16:16:00-05:00",
        ),
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
        retrieved_at="2026-09-10T08:00:00Z",
        **_metadata("announcement", "announcement-20240131"),
    )

    assert record["status"] == "RAW_UNADMITTED"
    assert record["admitted"] is False
    assert record["available_at"] is None


def test_build_document_record_marks_missing_extractor_evidence_unadmitted(tmp_path: Path) -> None:
    record = build_document_record(
        tmp_path,
        "raw/result.pdf",
        b"%PDF-1.7\nFor Immediate Release February 08, 2024\n",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        retrieved_at="2026-09-10T08:00:00Z",
        **_metadata("result", "result-20240208", result_status="COMPLETED"),
    )

    assert record["status"] == "RAW_UNADMITTED"
    assert record["admitted"] is False
    assert record["error_code"] == "EXTRACTOR_UNAVAILABLE"
    assert record["published_at"] is None
    assert record["available_at"] is None


def test_build_document_record_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "raw/result.pdf"
    path.parent.mkdir()
    path.write_bytes(b"original")

    with pytest.raises(FileExistsError):
        build_document_record(
            tmp_path,
            "raw/result.pdf",
            _source_body(),
            source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            retrieved_at="2026-09-10T08:00:00Z",
            **_metadata("result", "result-20240208", result_status="COMPLETED"),
        )

    assert path.read_bytes() == b"original"


def test_build_document_record_rejects_resolved_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    os.symlink(outside, root / "raw")

    with pytest.raises(ValueError, match="unsafe"):
        build_document_record(
            root,
            "raw/result.pdf",
            _source_body(),
            source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            retrieved_at="2026-09-10T08:00:00Z",
            **_metadata("result", "result-20240208", result_status="COMPLETED"),
        )


class _Response:
    def __init__(self, content: bytes, *, url: str, content_type: str = "application/pdf", status_code: int = 200) -> None:
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = {"content-type": content_type}


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def get(self, _url: str, *, timeout: float) -> _Response:
        assert timeout == 30.0
        return self.response


def test_fetch_rejects_caller_asserted_timestamps_even_when_present_in_metadata(tmp_path: Path) -> None:
    metadata = _metadata("result", "result-20240208", result_status="COMPLETED")
    metadata.update({"published_at": "2024-02-08T13:00:00-05:00", "available_at": "2024-02-08T13:00:00-05:00"})

    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=metadata,
        session=_Session(_Response(_source_body(), url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf")),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "CALLER_TIMESTAMP_FIELDS_FORBIDDEN"
    assert not (tmp_path / "raw/result.pdf").exists()


def test_fetch_rejects_nested_caller_asserted_timestamps_before_network(tmp_path: Path) -> None:
    metadata = _metadata("result", "result-20240208", result_status="COMPLETED")
    metadata["provenance"] = {"published_at": "2024-02-08T13:00:00-05:00"}

    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=metadata,
        session=_Session(_Response(_source_body(), url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf")),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "CALLER_TIMESTAMP_FIELDS_FORBIDDEN"
    assert result["network_attempted"] is False
    assert not (tmp_path / "raw/result.pdf").exists()


def test_fetch_keeps_source_without_timestamp_evidence_raw_unadmitted(tmp_path: Path) -> None:
    metadata = _metadata("result", "result-20240208", result_status="COMPLETED")

    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=metadata,
        session=_Session(
            _Response(
                b"%PDF-1.7\nFor Immediate Release February 08, 2024\n",
                url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            )
        ),
    )

    assert result["status"] == "RAW_UNADMITTED"
    assert result["error_code"] == "EXTRACTOR_UNAVAILABLE"
    assert result["admitted"] is False
    assert result["network_attempted"] is True
    assert (tmp_path / "raw/result.pdf").exists()


def test_fetch_rejects_redirect_to_non_official_host(tmp_path: Path) -> None:
    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=_metadata("result", "result-20240208", result_status="COMPLETED"),
        session=_Session(_Response(_source_body(), url="https://evil.example/result.pdf")),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "FINAL_URL_NOT_OFFICIAL"
    assert not (tmp_path / "raw/result.pdf").exists()


def test_fetch_rejects_mismatched_content_type(tmp_path: Path) -> None:
    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=_metadata("result", "result-20240208", result_status="COMPLETED"),
        session=_Session(_Response(_source_body(), url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf", content_type="text/html")),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "CONTENT_TYPE_INVALID"


def test_fetch_rejects_mismatched_pdf_signature(tmp_path: Path) -> None:
    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=_metadata("result", "result-20240208", result_status="COMPLETED"),
        session=_Session(
            _Response(
                b"not a pdf",
                url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            )
        ),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "CONTENT_SIGNATURE_INVALID"


def test_fetch_keeps_pdf_raw_unadmitted_without_real_extractor(tmp_path: Path) -> None:
    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=_metadata("result", "result-20240208", result_status="COMPLETED"),
        session=_Session(_Response(_source_body(), url="https://www.treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf")),
    )

    assert result["status"] == "RAW_UNADMITTED"
    assert result["admitted"] is False
    assert result["error_code"] == "EXTRACTOR_UNAVAILABLE"
    assert result["source_url"].startswith("https://www.treasurydirect.gov/")
    assert result["published_at"] is None
    assert result["available_at"] is None
    assert (tmp_path / "raw/result.pdf").read_bytes() == _source_body()


def test_sanitized_blocked_receipt_does_not_persist_credentials() -> None:
    receipt = sanitized_blocked_receipt(
        source_url="https://data.alpaca.markets/v2/stocks/bars",
        status_code=401,
        error="provider rejected credentials",
        retrieved_at="2026-09-10T08:00:00Z",
    )

    serialized = json.dumps(receipt, sort_keys=True)
    assert receipt["status"] == "BLOCKED_HTTP"
    assert receipt["credential_fields_persisted"] is False
    assert "ALPACA_API_KEY" not in serialized
    assert "ALPACA_SECRET_KEY" not in serialized
    assert "provider rejected credentials" not in serialized


def test_load_pinned_inventory_rejects_caller_asserted_timestamps(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
                "published_at": "2024-02-08T13:00:00-05:00",
            }
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    with pytest.raises(ValueError, match="caller timestamp"):
        acquisition.load_pinned_inventory(path)


def test_load_pinned_inventory_rejects_issue_date_as_auction_date(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2024-02-15-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-15",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    with pytest.raises(ValueError, match="auction_session_date must be auction date"):
        acquisition.load_pinned_inventory(path)


def test_load_pinned_inventory_accepts_auction_date_distinct_from_issue_date(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    loaded = acquisition.load_pinned_inventory(path)

    assert loaded["events"][0]["auction_session_date"] == "2024-02-08"
    assert loaded["inventory_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_pinned_inventory_rejects_event_after_historical_cutoff(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2026-08-01-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2026-08-01",
                "issue_date": "2026-08-08",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2026/A_20260801_1.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2026/R_20260801_1.pdf",
            }
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    with pytest.raises(ValueError, match="post_cutoff"):
        acquisition.load_pinned_inventory(path)


def test_build_source_capability_receipt_records_public_timestamp_blocker() -> None:
    receipt = acquisition.build_source_capability_receipt(
        checked_at="2026-09-10T08:00:00Z",
        result_publication_timestamp_found=False,
        complete_inventory_pinned=False,
        tlt_source_status="BLOCKED_NO_AUTHORIZED_CREDENTIALS",
    )

    assert receipt["schema"] == "r46-treasury-source-capability-v1"
    assert receipt["status"] == "CAPABILITY_OR_PAID_BLOCKED"
    assert receipt["r46_admission_status"] == "DATA_ACQUISITION_REQUIRED"
    assert "RESULT_PUBLICATION_TIMESTAMP_UNAVAILABLE" in receipt["blockers"]
    assert "COMPLETE_EVENT_INVENTORY_NOT_PINNED" in receipt["blockers"]
    assert receipt["credentials_used"] is False
    assert receipt["paid_source_used"] is False
    assert receipt["route_capability_status"] == "PUBLIC_ROUTES_IDENTIFIED"
    assert receipt["source_evidence"][0]["checked_at"] == "2026-09-10T08:00:00Z"
    assert receipt["source_evidence"][0]["url"] == acquisition.TREASURY_ARCHIVE_URL
    artifact_hash = receipt["artifact_sha256"]
    unsigned = dict(receipt)
    unsigned.pop("artifact_sha256")
    expected_hash = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert artifact_hash == expected_hash


def test_static_capability_receipt_is_canonical_and_self_hashed() -> None:
    receipt = json.loads(CAPABILITY_RECEIPT.read_text(encoding="utf-8"))
    unsigned = dict(receipt)
    actual_hash = unsigned.pop("artifact_sha256")
    expected_hash = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    assert actual_hash == expected_hash
    assert receipt["route_capability_status"] == "PUBLIC_ROUTES_IDENTIFIED"
    assert receipt["r46_admission_status"] == "DATA_ACQUISITION_REQUIRED"
    assert receipt["status"] == "CAPABILITY_OR_PAID_BLOCKED"


def test_cli_help_works_when_invoked_directly() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--inventory" in result.stdout
    assert "--fetch" in result.stdout


def test_cli_does_not_network_without_explicit_fetch(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(inventory_path), "--output-root", str(tmp_path / "run")],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "CAPABILITY_OR_PAID_BLOCKED"
    assert payload["inventory_pin_status"] == "UNPINNED_OFFICIAL_EXPORT"
    assert "COMPLETE_EVENT_INVENTORY_NOT_HASH_PINNED" in payload["blockers"]
    assert payload["network_attempted"] is False


class _InventorySession:
    def get(self, url: str, *, timeout: float) -> _Response:
        assert timeout == 30.0
        identifier = "A_20240131_2" if "/A_" in url else "R_20240208_3"
        return _Response(
            _source_body(),
            url=f"https://treasurydirect.gov/instit/annceresult/press/preanre/2024/{identifier}.pdf",
        )


def test_acquire_pinned_inventory_never_reports_acquired_from_fetcher_admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    export_path = tmp_path / "official-export.csv"
    coverage_path = tmp_path / "annual-coverage-2024.html"
    export_path.write_bytes(b"fixture export bound to an official URL\n")
    coverage_path.write_bytes(b"fixture annual coverage bound to an official URL\n")
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "official_inventory_pin": {
            "export_url": "https://fiscaldata.treasury.gov/static-data/published-reports/auctions-query/",
            "export_path": export_path.name,
            "export_sha256": hashlib.sha256(export_path.read_bytes()).hexdigest(),
            "annual_coverage": [
                {
                    "year": 2024,
                    "url": "https://treasurydirect.gov/auctions/announcements-data-results/announcement-results-press-releases/",
                    "path": coverage_path.name,
                    "sha256": hashlib.sha256(coverage_path.read_bytes()).hexdigest(),
                }
            ],
        },
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    def _admitted_fetcher(**_: Any) -> dict[str, Any]:
        return {"document_id": "synthetic", "status": "ADMITTED", "admitted": True}

    monkeypatch.setattr(acquisition, "fetch_official_document", _admitted_fetcher)
    receipt = acquisition.acquire_pinned_inventory(
        inventory_path,
        output_root=tmp_path / "run",
        fetch=True,
    )

    assert receipt["status"] == "CAPABILITY_OR_PAID_BLOCKED"
    assert receipt["admitted_document_count"] == 0
    assert receipt["admitted_event_count"] == 0
    assert receipt["events"] == []


def test_acquire_pinned_inventory_fetches_only_with_opt_in(tmp_path: Path) -> None:
    export_path = tmp_path / "official-export.csv"
    coverage_path = tmp_path / "annual-coverage-2024.html"
    export_path.write_bytes(b"fixture export bound to an official URL\n")
    coverage_path.write_bytes(b"fixture annual coverage bound to an official URL\n")
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "official_inventory_pin": {
            "export_url": "https://fiscaldata.treasury.gov/static-data/published-reports/auctions-query/",
            "export_path": export_path.name,
            "export_sha256": "a" * 64,
            "annual_coverage": [
                {
                    "year": 2024,
                    "url": "https://treasurydirect.gov/auctions/announcements-data-results/announcement-results-press-releases/",
                    "path": coverage_path.name,
                    "sha256": "b" * 64,
                }
            ],
        },
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    inventory["official_inventory_pin"]["export_sha256"] = hashlib.sha256(export_path.read_bytes()).hexdigest()
    inventory["official_inventory_pin"]["annual_coverage"][0]["sha256"] = hashlib.sha256(coverage_path.read_bytes()).hexdigest()
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    receipt = acquisition.acquire_pinned_inventory(
        inventory_path,
        output_root=tmp_path / "run",
        fetch=True,
        session=_InventorySession(),
    )

    assert receipt["status"] == "CAPABILITY_OR_PAID_BLOCKED"
    assert receipt["network_attempted"] is True
    assert receipt["admitted_document_count"] == 0
    assert receipt["admitted_event_count"] == 0
    assert all(document["status"] == "RAW_UNADMITTED" for document in receipt["documents"])
    assert all(document["error_code"] == "EXTRACTOR_UNAVAILABLE" for document in receipt["documents"])
