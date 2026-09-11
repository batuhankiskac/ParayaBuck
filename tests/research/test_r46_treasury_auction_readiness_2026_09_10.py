from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import scripts.research.r46_treasury_auction_readiness_2026_09_10 as readiness
from scripts.research.r46_treasury_auction_readiness_2026_09_10 import (
    CUTOFF_UTC,
    normalize_auction_documents,
    validate_tlt_bars,
    validate_tlt_dividends,
    validate_tlt_eligibility,
    verify_r46_package,
)

RESULT_AVAILABILITY_POLICY = "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
RESULT_SIGNAL_ELIGIBILITY = "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
NEW_YORK = ZoneInfo("America/New_York")


def _write_document(root: Path, name: str, content: bytes = b"official treasury fixture\n") -> tuple[str, str]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path.relative_to(root)), hashlib.sha256(content).hexdigest()


def _document(
    root: Path,
    *,
    kind: str,
    document_id: str,
    publication_at: str | None = "2024-02-08T13:00:00-05:00",
    revision_id: str = "original",
    supersedes_document_id: str | None = None,
    result_status: str | None = None,
) -> dict[str, object]:
    raw_path, digest = _write_document(root, f"documents/{document_id}.pdf", document_id.encode())
    availability_at = (
        datetime.combine(date(2024, 2, 9), time.min, tzinfo=NEW_YORK).isoformat()
        if kind == "result"
        else publication_at
    )
    return {
        "document_id": document_id,
        "document_kind": kind,
        "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
        "source_url": f"https://treasurydirect.gov/auctions/announcements/{document_id}.pdf",
        "raw_path": raw_path,
        "sha256": digest,
        "security_type": "nominal",
        "instrument_type": "US_TREASURY_SECURITY",
        "issuer_identifier": "US_TREASURY",
        "original_tenor": 30,
        "issue_or_reopening": "original",
        "security_identifier": "912810TM2",
        "auction_session_date": "2024-02-08",
        "published_at": publication_at,
        "available_at": availability_at,
        "source_timezone": "America/New_York",
        "publication_at_source": "official_release_timestamp" if kind == "result" else "treasury_document_header",
        "retrieved_at": "2026-09-10T08:00:00Z",
        "revision_id": revision_id,
        "supersedes_document_id": supersedes_document_id,
        "correction_or_amendment_chain": [],
        "result_status": result_status,
        "release_time": "13:00" if kind == "result" else None,
        "publication_time_proven": True if kind == "result" else None,
        "official_publication_date": "2024-02-08" if kind == "result" else None,
        "availability_derivation": "official_release_timestamp" if kind == "announcement" else RESULT_AVAILABILITY_POLICY,
        "result_signal_eligibility": RESULT_SIGNAL_ELIGIBILITY if kind == "result" else None,
        "same_day_signal_allowed": False if kind == "result" else None,
    }


def _valid_documents(root: Path) -> list[dict[str, object]]:
    return [
        _document(root, kind="announcement", document_id="announcement-20240208"),
        _document(root, kind="result", document_id="result-20240208", result_status="COMPLETED"),
    ]


def _valid_bars() -> list[dict[str, object]]:
    return [
        {
            "permanent_security_identifier": "alpaca:T...",
            "listing_and_tradability_interval": "2002-07-22/",
            "session_date": "2024-02-09",
            "regular_open_timestamp": "2024-02-09T14:30:00Z",
            "source_vintage": "alpaca-sip-split-v1",
            "available_at": "2024-01-01T00:00:00Z",
            "adjustment": "split",
            "open": 92.0,
            "high": 93.0,
            "low": 91.0,
            "close": 92.5,
            "volume": 1000000,
        },
        {
            "permanent_security_identifier": "alpaca:T...",
            "listing_and_tradability_interval": "2002-07-22/",
            "session_date": "2024-02-12",
            "regular_open_timestamp": "2024-02-12T14:30:00Z",
            "source_vintage": "alpaca-sip-split-v1",
            "available_at": "2024-01-01T00:00:00Z",
            "adjustment": "split",
            "open": 92.5,
            "high": 94.0,
            "low": 92.0,
            "close": 93.5,
            "volume": 1100000,
        },
        {
            "permanent_security_identifier": "alpaca:T...",
            "listing_and_tradability_interval": "2002-07-22/",
            "session_date": "2024-02-13",
            "regular_open_timestamp": "2024-02-13T14:30:00Z",
            "source_vintage": "alpaca-sip-split-v1",
            "available_at": "2024-01-01T00:00:00Z",
            "adjustment": "split",
            "open": 93.5,
            "high": 94.0,
            "low": 93.0,
            "close": 93.75,
            "volume": 1200000,
        },
        {
            "permanent_security_identifier": "alpaca:T...",
            "listing_and_tradability_interval": "2002-07-22/",
            "session_date": "2024-02-14",
            "regular_open_timestamp": "2024-02-14T14:30:00Z",
            "source_vintage": "alpaca-sip-split-v1",
            "available_at": "2024-01-01T00:00:00Z",
            "adjustment": "split",
            "open": 93.75,
            "high": 95.0,
            "low": 93.5,
            "close": 94.5,
            "volume": 1300000,
        },
        {
            "permanent_security_identifier": "alpaca:T...",
            "listing_and_tradability_interval": "2002-07-22/",
            "session_date": "2024-02-15",
            "regular_open_timestamp": "2024-02-15T14:30:00Z",
            "source_vintage": "alpaca-sip-split-v1",
            "available_at": "2024-01-01T00:00:00Z",
            "adjustment": "split",
            "open": 94.5,
            "high": 95.0,
            "low": 94.0,
            "close": 94.75,
            "volume": 1400000,
        },
        {
            "permanent_security_identifier": "alpaca:T...",
            "listing_and_tradability_interval": "2002-07-22/",
            "session_date": "2024-02-16",
            "regular_open_timestamp": "2024-02-16T14:30:00Z",
            "source_vintage": "alpaca-sip-split-v1",
            "available_at": "2024-01-01T00:00:00Z",
            "adjustment": "split",
            "open": 94.75,
            "high": 95.5,
            "low": 94.5,
            "close": 95.0,
            "volume": 1500000,
        },
    ]


def _valid_calendar() -> list[dict[str, object]]:
    return [
        {
            "regular_session_date": value,
            "regular_open_timestamp": f"{value}T14:30:00Z",
            "regular_close_timestamp": f"{value}T21:00:00Z",
            "holiday_or_closure_reason": None,
        }
        for value in ("2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16")
    ]


def _valid_dividends() -> list[dict[str, object]]:
    return [
        {
            "permanent_security_identifier": "alpaca:T...",
            "action_type": "cash_dividend",
            "effective_date": "2024-02-01",
            "cash_or_ratio": 0.28,
            "source_document_hash": "a" * 64,
            "duplicate_event_key": "alpaca:T...:cash_dividend:2024-02-01:0.28",
            "event_timestamp": "2024-02-01T05:00:00Z",
            "currency": "USD",
            "available_at": "2024-01-01T00:00:00Z",
        }
    ]


def _valid_eligibility() -> list[dict[str, object]]:
    return [
        {
            "canonical_symbol": "TLT",
            "historical_symbol": "TLT",
            "permanent_security_identifier": "alpaca:T...",
            "provider_asset_id": "alpaca:T...",
            "security_identifier": "464287432",
            "venue": "NASDAQ",
            "listing_and_tradability_interval": "2002-07-22/",
            "valid_from": "2002-07-22",
            "valid_to": None,
            "first_observed_bar": "2002-07-22",
            "last_observed_bar": "2026-07-31",
            "identity_basis": "historical_provider_asset_map",
            "source_url": "https://data.alpaca.markets/v2/assets/TLT",
            "available_at": "2024-01-01T00:00:00Z",
        }
    ]


def _valid_manifest() -> dict[str, object]:
    return {
        "schema_version": "r46-governance-manifest-v1",
        "research_id": "r46_treasury_auction_unwind_tlt_v1_2026_09_10",
        "candidate_id": "R46",
        "status": "FROZEN_PRE_OUTCOME",
        "declared_before_outcomes": True,
        "economic_execution": False,
        "economic_run": False,
        "economic_outcomes_accessed": False,
        "outcome_artifacts_present": False,
        "research_only": True,
        "promotion": False,
        "integration_candidate": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "event_contract": {
            "decision_cutoff": "16:15 America/New_York on the auction session",
            "retrieval_time_substitution_forbidden": True,
            "entry": "next regular-session TLT open after the qualifying event",
            "exit": "fifth regular-session open after entry, producing five open-to-open holding intervals",
            "overlap": "Overlapping signals do not pyramid or extend the first event's exit.",
        },
        "data_contract": {
            "source_matrix_path": "source_matrix.json",
            "source_matrix_sha256": "a" * 64,
            "pit_source_status": "READY_PIT_SOURCE",
            "pit_source_attestation_path": "pit_receipt.json",
            "pit_source_attestation_sha256": "b" * 64,
        },
        "execution_contract": {
            "native_one_ledger_required": True,
            "native_ledger_status": "READY_NATIVE_LEDGER",
            "native_ledger_attestation_path": "ledger_receipt.json",
            "native_ledger_attestation_sha256": "c" * 64,
            "long_only": True,
            "no_leverage": True,
            "no_pyramid": True,
            "caps": {
                "equity_gross_max": 0.55,
                "combined_gross_max": 0.60,
                "combined_cash_min": 0.40,
                "total_positions_max": 6,
            },
        },
        "holdout": {
            "status": "SEALED_PROSPECTIVE_SINGLE_USE",
            "mode": "prospective_single_use",
            "historical_holdout_accepted": False,
            "contract_path": "holdout_contract.json",
            "contract_sha256": "b" * 64,
            "attestation_ref": "holdout://r46-untouched-v1",
            "custody_receipt_path": "holdout_receipt.json",
            "accessed": False,
        },
        "statistics": {"status": "READY_STATISTICS"},
        "multiplicity": {"status": "READY_MULTIPLICITY"},
        "source_provenance": {
            "base_commit": "0" * 40,
            "clean_tree_observed": True,
            "runner": {"path": "runner.py", "sha256": "d" * 64, "required_in_base_commit": True},
            "verifier": {"path": "verifier.py", "sha256": "e" * 64, "required_in_base_commit": True},
            "preflight": {"path": "preflight.py", "sha256": "f" * 64, "required_in_base_commit": True},
            "custody_validator": {"path": "custody.py", "sha256": "1" * 64, "required_in_base_commit": True},
            "environment": {"command": "python -m runner", "dependency_lock_sha256": "2" * 64, "network": "disabled"},
        },
    }


def _valid_holdout_attestation() -> dict[str, object]:
    return {
        "schema_version": "r46-holdout-custody-receipt-v1",
        "research_id": "r46_treasury_auction_unwind_tlt_v1_2026_09_10",
        "holdout_id": "r46_treasury_auction_unwind_tlt_v1_2026_09_10:prospective_holdout",
        "status": "SEALED_PROSPECTIVE_SINGLE_USE",
        "attestation_ref": "holdout://r46-untouched-v1",
        "manifest_sha256": "c" * 64,
        "holdout_contract_sha256": "b" * 64,
        "data_root_sha256": "d" * 64,
        "access_log_sha256": "e" * 64,
        "sealed_at": "2026-09-10T00:00:00Z",
        "holdout_start": "2026-09-11T00:00:00Z",
        "holdout_end_exclusive": "2026-10-01T00:00:00Z",
        "storage_class": "WORM",
        "encrypted_at_rest": True,
        "access_log_append_only": True,
        "raw_holdout_hidden_until_release": True,
        "operator": {"id": "operator-a", "signature": "sig-a"},
        "custodian": {"id": "custodian-b", "signature": "sig-b"},
        "independent_reviewer": {"id": "reviewer-c", "signature": "sig-c"},
        "outcomes_accessed_before_release": False,
        "one_release_only": True,
        "release_count": 0,
        "accessed": False,
        "_hash_pinned": True,
    }


def test_normalize_auction_documents_pairs_completed_nominal_30y_event(tmp_path: Path) -> None:
    result = normalize_auction_documents(_valid_documents(tmp_path), repo_root=tmp_path)

    assert result["status"] == "COMPLETE"
    assert result["admitted_event_count"] == 1
    event = result["events"][0]
    assert event["canonical_event_id"] == "2024-02-08-30Y-ORIGINAL"
    assert event["entry_available_at"] == "2024-02-09T05:00:00Z"
    assert event["result_status"] == "COMPLETED"


def test_normalize_auction_documents_accepts_missing_release_only_with_policy(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    result = documents[1]
    result.update(
        {
            "published_at": None,
            "release_time": None,
            "publication_time_proven": False,
            "publication_at_source": "official_XML_release_time_missing",
        }
    )

    normalized = normalize_auction_documents(documents, repo_root=tmp_path)

    event = normalized["events"][0]
    assert event["published_at"] is None
    assert event["available_at"] == "2024-02-09T05:00:00Z"
    assert event["result_available_at"] == "2024-02-09T05:00:00Z"


@pytest.mark.parametrize(
    ("auction_date", "expected_available_at"),
    (
        ("2024-03-09", "2024-03-10T05:00:00Z"),
        ("2024-11-02", "2024-11-03T04:00:00Z"),
        ("2024-11-03", "2024-11-04T05:00:00Z"),
    ),
)
def test_normalize_auction_documents_policy_is_dst_safe(
    tmp_path: Path,
    auction_date: str,
    expected_available_at: str,
) -> None:
    documents = _valid_documents(tmp_path)
    for document in documents:
        document["canonical_event_id"] = f"{auction_date}-30Y-ORIGINAL"
        document["auction_session_date"] = auction_date
        if document["document_kind"] == "result":
            result_date = date.fromisoformat(auction_date)
            publication = datetime.combine(result_date, time(13), tzinfo=NEW_YORK)
            available = datetime.combine(result_date + timedelta(days=1), time.min, tzinfo=NEW_YORK)
            document["published_at"] = publication.isoformat()
            document["available_at"] = available.isoformat()
            document["official_publication_date"] = auction_date
        else:
            announcement_date = date.fromisoformat(auction_date) - timedelta(days=1)
            announcement = datetime.combine(announcement_date, time(23, 59, 59), tzinfo=NEW_YORK)
            document["published_at"] = announcement.isoformat()
            document["available_at"] = announcement.isoformat()

    normalized = normalize_auction_documents(documents, repo_root=tmp_path)

    assert normalized["events"][0]["available_at"] == expected_available_at


def test_normalize_auction_documents_rejects_forged_result_offset_or_date(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents[1]["available_at"] = "2024-02-09T00:00:00Z"

    with pytest.raises(ValueError, match="result available_at policy"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_normalize_auction_documents_rejects_last_modified_publication_substitution(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents[1]["publication_at_source"] = "server_last_modified"
    documents[1]["published_at"] = "2026-05-22T15:11:25Z"

    with pytest.raises(ValueError, match="publication_at_source"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_normalize_auction_documents_rejects_published_at_not_matching_release_time(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents[1]["published_at"] = "2026-05-22T15:11:25Z"

    with pytest.raises(ValueError, match="release_time"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_normalize_auction_documents_rejects_null_published_when_release_is_present(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents[1]["published_at"] = None
    documents[1]["publication_time_proven"] = True

    with pytest.raises(ValueError, match="published_at"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_normalize_auction_documents_rejects_missing_source_publication_time(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents[1]["available_at"] = None

    with pytest.raises(ValueError, match="available_at"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_normalize_auction_documents_rejects_retrieval_time_as_publication_evidence(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents[0]["publication_at_source"] = "retrieved_at"

    with pytest.raises(ValueError, match="publication_at_source"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_normalize_auction_documents_rejects_result_availability_policy_substitution(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents[1]["available_at"] = "2024-02-08T16:16:00-05:00"

    with pytest.raises(ValueError, match="result available_at policy"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_normalize_auction_documents_rejects_wrong_security_term(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents[0]["original_tenor"] = 20

    with pytest.raises(ValueError, match="30Y"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_normalize_auction_documents_requires_revision_chain_for_duplicates(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents.append(_document(tmp_path, kind="result", document_id="result-duplicate", result_status="COMPLETED"))

    with pytest.raises(ValueError, match="duplicate|revision"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_normalize_auction_documents_rejects_amendment_without_original_predecessor(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    documents[1]["revision_id"] = "amended"

    with pytest.raises(ValueError, match="revision chain"):
        normalize_auction_documents(documents, repo_root=tmp_path)


def test_validate_tlt_bars_requires_split_adjustment_and_timezone(tmp_path: Path) -> None:
    bars = _valid_bars()
    bars[0]["adjustment"] = "all"

    with pytest.raises(ValueError, match="split-adjusted"):
        validate_tlt_bars(bars, available_at="2024-01-01T00:00:00Z")

    bars[0]["adjustment"] = "split"
    bars[0]["regular_open_timestamp"] = "2024-02-16T14:30:00"
    with pytest.raises(ValueError, match="timezone"):
        validate_tlt_bars(bars, available_at="2024-01-01T00:00:00Z")


def test_validate_tlt_dividends_is_separate_cash_ledger() -> None:
    dividends = _valid_dividends()

    result = validate_tlt_dividends(dividends, available_at="2024-01-01T00:00:00Z")

    assert result["status"] == "COMPLETE"
    assert result["rows"] == 1
    assert result["accounting"] == "separate_cash_dividends"

    with pytest.raises(ValueError, match="cash_or_ratio"):
        validate_tlt_dividends([{**dividends[0], "cash_or_ratio": 0}], available_at="2024-01-01T00:00:00Z")


def test_validate_tlt_eligibility_rejects_current_snapshot_identity() -> None:
    row = _valid_eligibility()[0]
    row["identity_basis"] = "current_product_page"

    with pytest.raises(ValueError, match="historical identity"):
        validate_tlt_eligibility([row])


def test_cross_validate_tlt_data_selects_same_id_historical_interval_by_date() -> None:
    identifier = "sec:TLT:S000004360:C000012090"
    old_interval = "2002-07-22/2024-02-01"
    new_interval = "2024-02-01/"
    eligibility = {
        "records": [
            {
                "permanent_security_identifier": identifier,
                "listing_and_tradability_interval": old_interval,
                "valid_from": "2002-07-22",
                "valid_to": "2024-02-01",
            },
            {
                "permanent_security_identifier": identifier,
                "listing_and_tradability_interval": new_interval,
                "valid_from": "2024-02-01",
                "valid_to": None,
            },
        ]
    }
    bars = {
        "available_at": "2024-02-02T00:00:00Z",
        "records": [
            {
                "permanent_security_identifier": identifier,
                "listing_and_tradability_interval": old_interval,
                "session_date": "2024-01-31",
                "regular_open_timestamp": "2024-01-31T14:30:00Z",
                "available_at": "2024-01-31T15:00:00Z",
            },
            {
                "permanent_security_identifier": identifier,
                "listing_and_tradability_interval": new_interval,
                "session_date": "2024-02-01",
                "regular_open_timestamp": "2024-02-01T14:30:00Z",
                "available_at": "2024-02-01T15:00:00Z",
            },
        ],
    }
    dividends = {
        "available_at": "2024-02-02T00:00:00Z",
        "records": [
                {
                    "permanent_security_identifier": identifier,
                    "effective_date": "2024-01-31",
                    "event_timestamp": "2024-01-31T14:30:00Z",
                    "available_at": "2024-01-31T14:00:00Z",
                }
        ],
    }
    calendar = {
        "records": [
            {
                "regular_session_date": session_date,
                "regular_open_timestamp": f"{session_date}T14:30:00Z",
                "holiday_or_closure_reason": None,
            }
            for session_date in ("2024-01-31", "2024-02-01")
        ]
    }

    assert readiness._cross_validate_tlt_data(
        bars=bars,
        dividends=dividends,
        eligibility=eligibility,
        calendar=calendar,
    ) == []


def test_cross_validate_tlt_data_fails_closed_on_overlapping_identity_intervals() -> None:
    identifier = "sec:TLT:S000004360:C000012090"
    interval = {
        "permanent_security_identifier": identifier,
        "listing_and_tradability_interval": "2002-07-22/",
        "valid_from": "2002-07-22",
        "valid_to": None,
    }
    bars = {
        "available_at": "2024-02-02T00:00:00Z",
        "records": [
            {
                "permanent_security_identifier": identifier,
                "listing_and_tradability_interval": "2002-07-22/",
                "session_date": "2024-01-31",
                "regular_open_timestamp": "2024-01-31T14:30:00Z",
                "available_at": "2024-01-31T15:00:00Z",
            }
        ],
    }
    dividends = {"available_at": "2024-02-02T00:00:00Z", "records": []}
    calendar = {
        "records": [
            {
                "regular_session_date": "2024-01-31",
                "regular_open_timestamp": "2024-01-31T14:30:00Z",
                "holiday_or_closure_reason": None,
            }
        ]
    }

    assert readiness._cross_validate_tlt_data(
        bars=bars,
        dividends=dividends,
        eligibility={"records": [interval, dict(interval)]},
        calendar=calendar,
    ) == ["tlt_identity_binding"]


def test_verify_r46_package_remains_blocked_even_when_data_and_holdout_shapes_pass(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    auction = normalize_auction_documents(documents, repo_root=tmp_path)
    bars = validate_tlt_bars(_valid_bars(), available_at="2024-01-01T00:00:00Z")
    dividends = validate_tlt_dividends(_valid_dividends(), available_at="2024-01-01T00:00:00Z")
    eligibility = validate_tlt_eligibility(_valid_eligibility())
    calendar = readiness.validate_nyse_calendar(_valid_calendar(), available_at="2024-01-01T00:00:00Z")

    result = verify_r46_package(
        manifest=_valid_manifest(),
        auction=auction,
        bars=bars,
        dividends=dividends,
        eligibility=eligibility,
        calendar=calendar,
        holdout_attestation=_valid_holdout_attestation(),
    )

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "BLOCKED_SIGNATURE_VERIFICATION" in result["blockers"]
    assert result["economic_execution"] is False


def test_verify_r46_package_never_clears_embedded_signatures_without_verifier(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    auction = normalize_auction_documents(documents, repo_root=tmp_path)
    bars = validate_tlt_bars(_valid_bars(), available_at="2024-01-01T00:00:00Z")
    dividends = validate_tlt_dividends(_valid_dividends(), available_at="2024-01-01T00:00:00Z")
    eligibility = validate_tlt_eligibility(_valid_eligibility())

    result = verify_r46_package(
        manifest=_valid_manifest(),
        auction=auction,
        bars=bars,
        dividends=dividends,
        eligibility=eligibility,
        holdout_attestation=_valid_holdout_attestation(),
    )

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "BLOCKED_SIGNATURE_VERIFICATION" in result["blockers"]


def test_validate_tlt_bars_requires_each_row_available_by_artifact_time() -> None:
    bars = _valid_bars()
    bars[0]["available_at"] = "2024-01-02T00:00:00Z"

    with pytest.raises(ValueError, match="artifact available_at"):
        validate_tlt_bars(bars, available_at="2024-01-01T00:00:00Z")


def test_validate_tlt_dividends_requires_each_row_available_by_artifact_time() -> None:
    dividends = _valid_dividends()
    dividends[0]["available_at"] = "2024-01-02T00:00:00Z"

    with pytest.raises(ValueError, match="artifact available_at"):
        validate_tlt_dividends(dividends, available_at="2024-01-01T00:00:00Z")


def test_cross_validate_tlt_data_rejects_bar_available_before_exact_session_open() -> None:
    bars = {
        "available_at": "2024-02-02T00:00:00Z",
        "records": [
            {
                "permanent_security_identifier": "sec:TLT",
                "listing_and_tradability_interval": "2002-07-22/",
                "session_date": "2024-01-31",
                "regular_open_timestamp": "2024-01-31T14:30:00Z",
                "available_at": "2024-01-31T14:29:59Z",
            }
        ],
    }
    dividends = {"available_at": "2024-02-02T00:00:00Z", "records": []}
    eligibility = {
        "records": [
            {
                "permanent_security_identifier": "sec:TLT",
                "listing_and_tradability_interval": "2002-07-22/",
                "valid_from": "2002-07-22",
                "valid_to": None,
            }
        ]
    }
    calendar = {
        "records": [
            {
                "regular_session_date": "2024-01-31",
                "regular_open_timestamp": "2024-01-31T14:30:00Z",
                "holiday_or_closure_reason": None,
            }
        ]
    }

    blockers = readiness._cross_validate_tlt_data(
        bars=bars,
        dividends=dividends,
        eligibility=eligibility,
        calendar=calendar,
    )

    assert "tlt_bar_availability" in blockers


def test_cross_validate_tlt_data_rejects_dividend_before_event_open_or_event_mismatch() -> None:
    bars = {"available_at": "2024-02-02T00:00:00Z", "records": []}
    dividends = {
        "available_at": "2024-02-02T00:00:00Z",
        "records": [
            {
                "permanent_security_identifier": "sec:TLT",
                "effective_date": "2024-01-31",
                "event_timestamp": "2024-01-31T14:29:59Z",
                "available_at": "2024-01-31T14:30:00Z",
            }
        ],
    }
    eligibility = {
        "records": [
            {
                "permanent_security_identifier": "sec:TLT",
                "listing_and_tradability_interval": "2002-07-22/",
                "valid_from": "2002-07-22",
                "valid_to": None,
            }
        ]
    }
    calendar = {
        "records": [
            {
                "regular_session_date": "2024-01-31",
                "regular_open_timestamp": "2024-01-31T14:30:00Z",
                "holiday_or_closure_reason": None,
            }
        ]
    }

    blockers = readiness._cross_validate_tlt_data(
        bars=bars,
        dividends=dividends,
        eligibility=eligibility,
        calendar=calendar,
    )

    assert "tlt_dividend_event_timestamp" in blockers
    assert "tlt_dividend_availability" in blockers


def test_validate_nyse_calendar_rejects_non_regular_open_timestamp() -> None:
    calendar = _valid_calendar()
    calendar[0]["regular_open_timestamp"] = "2024-02-09T14:29:00Z"

    calendar_validator = getattr(readiness, "validate_nyse_calendar", None)
    assert callable(calendar_validator), "calendar validator must be implemented"
    with pytest.raises(ValueError, match="regular open"):
        calendar_validator(calendar, available_at="2024-01-01T00:00:00Z")


def test_verify_r46_package_stops_when_holdout_was_accessed(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    auction = normalize_auction_documents(documents, repo_root=tmp_path)
    bars = validate_tlt_bars(_valid_bars(), available_at="2024-01-01T00:00:00Z")
    dividends = validate_tlt_dividends(_valid_dividends(), available_at="2024-01-01T00:00:00Z")
    eligibility = validate_tlt_eligibility(_valid_eligibility())

    result = verify_r46_package(
        manifest=_valid_manifest(),
        auction=auction,
        bars=bars,
        dividends=dividends,
        eligibility=eligibility,
        holdout_attestation={**_valid_holdout_attestation(), "accessed": True},
    )

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "holdout" in result["blockers"]


def test_verify_r46_package_rejects_unsigned_holdout_attestation(tmp_path: Path) -> None:
    documents = _valid_documents(tmp_path)
    auction = normalize_auction_documents(documents, repo_root=tmp_path)
    bars = validate_tlt_bars(_valid_bars(), available_at="2024-01-01T00:00:00Z")
    dividends = validate_tlt_dividends(_valid_dividends(), available_at="2024-01-01T00:00:00Z")
    eligibility = validate_tlt_eligibility(_valid_eligibility())
    attestation = _valid_holdout_attestation()
    attestation.pop("custodian")

    result = verify_r46_package(
        manifest=_valid_manifest(),
        auction=auction,
        bars=bars,
        dividends=dividends,
        eligibility=eligibility,
        holdout_attestation=attestation,
    )

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "holdout" in result["blockers"]


def test_verify_r46_package_rejects_rule_relaxation() -> None:
    manifest = _valid_manifest()
    manifest["event_contract"]["overlap"] = "changed"

    result = verify_r46_package(
        manifest=manifest,
        auction={"schema": "r46-treasury-auction-ledger-v1", "status": "COMPLETE", "admitted_event_count": 1, "events": []},
        bars={"schema": "r46-tlt-split-adjusted-bars-v1", "status": "COMPLETE", "adjustment": "split", "records": []},
        dividends={"schema": "r46-tlt-separate-dividends-v1", "status": "COMPLETE", "accounting": "separate_cash_dividends", "records": []},
        eligibility={"schema": "r46-tlt-pit-eligibility-v1", "status": "COMPLETE", "historical_identity": True, "records": []},
        holdout_attestation=_valid_holdout_attestation(),
    )

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "r46_rule_contract" in result["blockers"]


def test_verify_r46_package_rejects_shared_capital_cap_drift() -> None:
    manifest = _valid_manifest()
    manifest["execution_contract"]["caps"]["combined_gross_max"] = 0.70

    result = verify_r46_package(
        manifest=manifest,
        auction={"schema": "r46-treasury-auction-ledger-v1", "status": "COMPLETE", "admitted_event_count": 1, "events": []},
        bars={"schema": "r46-tlt-split-adjusted-bars-v1", "status": "COMPLETE", "adjustment": "split", "records": []},
        dividends={"schema": "r46-tlt-separate-dividends-v1", "status": "COMPLETE", "accounting": "separate_cash_dividends", "records": []},
        eligibility={"schema": "r46-tlt-pit-eligibility-v1", "status": "COMPLETE", "historical_identity": True, "records": []},
        holdout_attestation=_valid_holdout_attestation(),
    )

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "shared_capital_contract" in result["blockers"]


def test_r46_manifest_serializes_utc_cutoff_as_immutable_contract() -> None:
    assert datetime.fromisoformat(CUTOFF_UTC.replace("Z", "+00:00")).tzinfo == UTC
    assert json.loads(json.dumps(_valid_manifest()))["declared_before_outcomes"] is True
