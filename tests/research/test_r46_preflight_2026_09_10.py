from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import scripts.research.r46_v2_preflight_2026_09_10 as v2_preflight
from scripts.research.r46_preflight_2026_09_10 import verify_r46_manifest
from scripts.research.r46_treasury_auction_readiness_2026_09_10 import CUTOFF_UTC

REPO = Path(__file__).resolve().parents[2]
LIVE_MANIFEST = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json"
RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v1_2026_09_10"
EVENT_ID = "2024-02-08-30Y-ORIGINAL"


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    value["integrity"].pop("manifest_sha256", None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _hash_bytes(encoded)


def _write(root: Path, relative: str, content: bytes) -> tuple[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return relative, _hash_bytes(content)


def _write_json(root: Path, relative: str, payload: dict[str, Any]) -> tuple[str, str]:
    return _write(root, relative, (json.dumps(payload, sort_keys=True) + "\n").encode())


def _write_jsonl(root: Path, relative: str, rows: list[dict[str, Any]]) -> tuple[str, str]:
    return _write(root, relative, b"".join((json.dumps(row, sort_keys=True) + "\n").encode() for row in rows))


def _document(root: Path, kind: str, identifier: str, *, result_status: str | None = None) -> dict[str, Any]:
    raw_path, raw_hash = _write(root, f"raw/{identifier}.pdf", identifier.encode())
    document = {
        "document_id": identifier,
        "document_kind": kind,
        "canonical_event_id": EVENT_ID,
        "source_url": f"https://treasurydirect.gov/auctions/{identifier}.pdf",
        "raw_path": raw_path,
        "sha256": raw_hash,
        "security_type": "nominal",
        "instrument_type": "US_TREASURY_SECURITY",
        "issuer_identifier": "US_TREASURY",
        "original_tenor": 30,
        "issue_or_reopening": "original",
        "security_identifier": "912810TM2",
        "auction_session_date": "2024-02-08",
        "published_at": "2024-02-08T13:00:00-05:00",
        "available_at": "2024-02-08T13:00:00-05:00",
        "source_timezone": "America/New_York",
        "publication_at_source": "treasury_document_header",
        "retrieved_at": "2026-09-10T08:00:00Z",
        "revision_id": "original",
        "supersedes_document_id": None,
        "correction_or_amendment_chain": [],
        "result_status": result_status,
    }
    if kind == "result":
        document.update(
            {
                "official_publication_date": "2024-02-08",
                "published_at": None,
                "available_at": "2024-02-09T05:00:00Z",
                "availability_derivation": (
                    "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
                ),
                "result_signal_eligibility": "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT",
                "same_day_signal_allowed": False,
                "publication_time_proven": False,
                "release_time": None,
                "publication_at_source": "official_XML_release_time_missing",
            }
        )
    return document


def _write_bars(root: Path) -> tuple[str, str]:
    path = root / "market/tlt_bars.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
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
    ]
    dates = ["2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for index, value in enumerate(dates, start=1):
            writer.writerow(
                {
                    "permanent_security_identifier": "alpaca:T...",
                    "listing_and_tradability_interval": "2002-07-22/",
                    "session_date": value,
                    "regular_open_timestamp": f"{value}T14:30:00Z",
                    "source_vintage": "alpaca-sip-split-v1",
                    "available_at": "2024-01-01T00:00:00Z",
                    "adjustment": "split",
                    "open": 90 + index,
                    "high": 91 + index,
                    "low": 89 + index,
                    "close": 90.5 + index,
                    "volume": 1000000 + index,
                }
            )
    return str(path.relative_to(root)), _hash_bytes(path.read_bytes())


def _write_dividends(root: Path) -> tuple[str, str]:
    content = b"permanent_security_identifier,action_type,effective_date,cash_or_ratio,source_document_hash,duplicate_event_key,event_timestamp,currency,available_at\nalpaca:T...,cash_dividend,2024-02-01,0.28," + b"a" * 64 + b",alpaca:T...:cash_dividend:2024-02-01:0.28,2024-02-01T05:00:00Z,USD,2024-01-01T00:00:00Z\n"
    return _write(root, "market/tlt_dividends.csv", content)


def _write_calendar(root: Path) -> tuple[str, str]:
    rows = [
        {
            "regular_session_date": value,
            "regular_open_timestamp": f"{value}T14:30:00Z",
            "regular_close_timestamp": f"{value}T21:00:00Z",
            "holiday_or_closure_reason": None,
        }
        for value in ("2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16")
    ]
    return _write_jsonl(root, "calendar/nyse.jsonl", rows)


def _source_matrix() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "r46-source-matrix-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "integrity": {"artifact_sha256": ""},
    }
    payload["integrity"]["artifact_sha256"] = _canonical_artifact_hash(payload)
    return payload


def _contract() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "r46-holdout-custody-contract-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "integrity": {"artifact_sha256": ""},
    }
    payload["integrity"]["artifact_sha256"] = _canonical_artifact_hash(payload)
    return payload


def _canonical_artifact_hash(payload: dict[str, Any]) -> str:
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    value["integrity"].pop("artifact_sha256", None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _hash_bytes(encoded)


def _canonical_top_level_hash(payload: dict[str, Any], field: str) -> str:
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    value.pop(field, None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _hash_bytes(encoded)


def _write_receipt(root: Path, relative: str, manifest_hash: str, *, holdout: bool = False, contract_hash: str = "") -> tuple[str, str]:
    payload: dict[str, Any] = {
        "schema_version": "r46-holdout-custody-receipt-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "manifest_sha256": manifest_hash,
        "status": "SEALED_PROSPECTIVE_SINGLE_USE",
    }
    if holdout:
        payload.update(
            {
                "holdout_id": f"{RESEARCH_ID}:prospective_holdout",
                "holdout_contract_sha256": contract_hash,
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
            }
        )
    return _write_json(root, relative, payload)


def _build_fixture(root: Path) -> tuple[Path, Path]:
    documents = [_document(root, "announcement", "announcement-20240208"), _document(root, "result", "result-20240208", result_status="COMPLETED")]
    documents_path, documents_hash = _write_jsonl(root, "auction/documents.jsonl", documents)
    bars_path, bars_hash = _write_bars(root)
    dividends_path, dividends_hash = _write_dividends(root)
    calendar_path, calendar_hash = _write_calendar(root)
    eligibility = {
        "canonical_symbol": "TLT",
        "historical_symbol": "TLT",
        "permanent_security_identifier": "alpaca:T...",
        "provider_asset_id": "alpaca:T...",
        "security_identifier": "464287432",
        "venue": "NASDAQ",
        "valid_from": "2002-07-22",
        "valid_to": None,
        "first_observed_bar": "2002-07-22",
        "last_observed_bar": "2026-07-31",
        "identity_basis": "historical_provider_asset_map",
        "source_url": "https://data.alpaca.markets/v2/assets/TLT",
        "listing_and_tradability_interval": "2002-07-22/",
        "available_at": "2024-01-01T00:00:00Z",
    }
    eligibility_path, eligibility_hash = _write_jsonl(root, "pit/tlt_eligibility.jsonl", [eligibility])
    schema_path, schema_hash = _write(root, "governance.schema.json", b"{}\n")
    source_path, source_hash = _write_json(root, "source_matrix.json", _source_matrix())
    contract_path, contract_hash = _write_json(root, "holdout_contract.json", _contract())
    capability: dict[str, Any] = {
        "schema": "r46-treasury-source-capability-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "status": "CAPABILITY_OR_PAID_BLOCKED",
        "route_capability_status": "PUBLIC_ROUTES_IDENTIFIED",
        "r46_admission_status": "DATA_ACQUISITION_REQUIRED",
        "artifact_sha256": "",
    }
    capability["artifact_sha256"] = _canonical_top_level_hash(capability, "artifact_sha256")
    capability_path, capability_hash = _write_json(root, "capability_receipt.json", capability)
    governance: dict[str, Any] = {
        "schema_version": "r46-governance-manifest-v1",
        "research_id": RESEARCH_ID,
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
            "source_matrix_path": source_path,
            "source_matrix_sha256": source_hash,
            "pit_source_status": "READY_PIT_SOURCE",
            "pit_source_attestation_path": "pit_receipt.json",
            "pit_source_attestation_sha256": "a" * 64,
        },
        "execution_contract": {
            "native_one_ledger_required": True,
            "native_ledger_status": "READY_NATIVE_LEDGER",
            "native_ledger_attestation_path": "ledger_receipt.json",
            "native_ledger_attestation_sha256": "b" * 64,
            "long_only": True,
            "no_leverage": True,
            "no_pyramid": True,
            "caps": {"equity_gross_max": 0.55, "combined_gross_max": 0.60, "combined_cash_min": 0.40, "total_positions_max": 6},
        },
        "holdout": {
            "status": "SEALED_PROSPECTIVE_SINGLE_USE",
            "mode": "prospective_single_use",
            "historical_holdout_accepted": False,
            "contract_path": contract_path,
            "contract_sha256": contract_hash,
            "custody_receipt_path": "holdout_receipt.json",
            "accessed": False,
        },
        "statistics": {"status": "READY_STATISTICS"},
        "multiplicity": {"status": "READY_MULTIPLICITY"},
        "source_provenance": {
            "base_commit": "0" * 40,
            "source_commit": "0" * 40,
            "clean_tree_observed": True,
            "source_files": {},
            "runner": {"path": "runner.py", "sha256": "c" * 64, "required_in_base_commit": True},
            "verifier": {"path": "verifier.py", "sha256": "d" * 64, "required_in_base_commit": True},
            "preflight": {"path": "preflight.py", "sha256": "e" * 64, "required_in_base_commit": True},
            "custody_validator": {"path": "custody.py", "sha256": "f" * 64, "required_in_base_commit": True},
            "source_capability_receipt": {
                "path": capability_path,
                "sha256": capability_hash,
                "artifact_sha256": capability["artifact_sha256"],
                "status": "CAPABILITY_OR_PAID_BLOCKED",
                "route_capability_status": "PUBLIC_ROUTES_IDENTIFIED",
                "r46_admission_status": "DATA_ACQUISITION_REQUIRED",
            },
            "environment": {"command": "python -m runner", "dependency_lock_sha256": "1" * 64, "network": "disabled"},
        },
        "integrity": {"schema_path": schema_path, "schema_sha256": schema_hash, "manifest_sha256": ""},
    }
    governance["integrity"]["manifest_sha256"] = _canonical_hash(governance)
    governance_path = root / "governance.json"
    governance_path.write_text(json.dumps(governance, sort_keys=True) + "\n", encoding="utf-8")
    governance_hash = governance["integrity"]["manifest_sha256"]
    pit_path, pit_hash = _write_receipt(root, "pit_receipt.json", governance_hash)
    ledger_path, ledger_hash = _write_receipt(root, "ledger_receipt.json", governance_hash)
    holdout_path, holdout_hash = _write_receipt(root, "holdout_receipt.json", governance_hash, holdout=True, contract_hash=_canonical_artifact_hash(_contract()))
    package: dict[str, Any] = {
        "schema": "r46-treasury-auction-unwind-data-package-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "governance_manifest_path": "governance.json",
        "governance_manifest_sha256": governance_hash,
        "historical_cutoff": CUTOFF_UTC,
        "expected_event_count": 1,
        "expected_event_ids": [EVENT_ID],
        "market_data": {
            "bars": {"available_at": "2024-01-01T00:00:00Z"},
            "dividends": {"available_at": "2024-01-01T00:00:00Z"},
            "calendar": {"available_at": "2024-01-01T00:00:00Z"},
        },
        "artifacts": {
            "documents": {"path": documents_path, "sha256": documents_hash},
            "bars": {"path": bars_path, "sha256": bars_hash},
            "dividends": {"path": dividends_path, "sha256": dividends_hash},
            "eligibility": {"path": eligibility_path, "sha256": eligibility_hash},
            "calendar": {"path": calendar_path, "sha256": calendar_hash},
        },
        "pit_source_receipt": {"path": pit_path, "sha256": pit_hash},
        "native_ledger_receipt": {"path": ledger_path, "sha256": ledger_hash},
        "holdout_receipt": {"path": holdout_path, "sha256": holdout_hash},
        "integrity": {"manifest_sha256": ""},
    }
    package["integrity"]["manifest_sha256"] = _canonical_hash(package)
    package_path = root / "data_package.json"
    package_path.write_text(json.dumps(package, sort_keys=True) + "\n", encoding="utf-8")
    return governance_path, package_path


def _rewrite_package(path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    package = json.loads(path.read_text(encoding="utf-8"))
    mutate(package)
    package["integrity"]["manifest_sha256"] = _canonical_hash(package)
    path.write_text(json.dumps(package, sort_keys=True) + "\n", encoding="utf-8")


def test_verify_r46_manifest_reads_separate_pinned_data_package_but_stays_blocked(tmp_path: Path) -> None:
    governance_path, package_path = _build_fixture(tmp_path)

    result = verify_r46_manifest(governance_path, data_package_manifest_path=package_path, repo_root=tmp_path)

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "BLOCKED_SIGNATURE_VERIFICATION" in result["blockers"]
    assert "CODEX_VERIFICATION_READY" not in result["blockers"]
    assert result["economic_execution"] is False
    assert result["checked_artifacts"] == 5


def test_verify_r46_manifest_rejects_mutated_document_bytes(tmp_path: Path) -> None:
    governance_path, package_path = _build_fixture(tmp_path)
    (tmp_path / "raw/result-20240208.pdf").write_bytes(b"mutated")

    result = verify_r46_manifest(governance_path, data_package_manifest_path=package_path, repo_root=tmp_path)

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "auction_lineage" in result["blockers"]


def test_verify_r46_manifest_rejects_post_cutoff_source_availability(tmp_path: Path) -> None:
    governance_path, package_path = _build_fixture(tmp_path)
    _rewrite_package(package_path, lambda package: package["market_data"]["bars"].update(available_at="2026-08-01T00:00:00Z"))

    result = verify_r46_manifest(governance_path, data_package_manifest_path=package_path, repo_root=tmp_path)

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "market_data_validation" in result["blockers"]


def test_verify_r46_manifest_rejects_archive_count_drift(tmp_path: Path) -> None:
    governance_path, package_path = _build_fixture(tmp_path)
    _rewrite_package(package_path, lambda package: package.update(expected_event_count=2))

    result = verify_r46_manifest(governance_path, data_package_manifest_path=package_path, repo_root=tmp_path)

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "auction_archive_completeness" in result["blockers"]


def test_verify_r46_manifest_rejects_governance_crosslink_drift(tmp_path: Path) -> None:
    governance_path, package_path = _build_fixture(tmp_path)
    _rewrite_package(package_path, lambda package: package.update(governance_manifest_sha256="0" * 64))

    result = verify_r46_manifest(governance_path, data_package_manifest_path=package_path, repo_root=tmp_path)

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "governance_data_package_crosslink" in result["blockers"]


def test_verify_r46_manifest_rejects_mutated_capability_receipt_raw_bytes(tmp_path: Path) -> None:
    governance_path, package_path = _build_fixture(tmp_path)
    receipt_path = tmp_path / "capability_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["status"] = "PUBLIC_ROUTE_CAPABLE"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_r46_manifest(governance_path, data_package_manifest_path=package_path, repo_root=tmp_path)

    assert "governance_source_capability_receipt" in result["blockers"]


def test_verify_r46_manifest_rejects_capability_receipt_status_after_rebinding(tmp_path: Path) -> None:
    governance_path, package_path = _build_fixture(tmp_path)
    receipt_path = tmp_path / "capability_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["status"] = "PUBLIC_ROUTE_CAPABLE"
    receipt["artifact_sha256"] = _canonical_top_level_hash(receipt, "artifact_sha256")
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    governance = json.loads(governance_path.read_text(encoding="utf-8"))
    binding = governance["source_provenance"]["source_capability_receipt"]
    binding["sha256"] = _hash_bytes(receipt_path.read_bytes())
    governance["integrity"]["manifest_sha256"] = _canonical_hash(governance)
    governance_path.write_text(json.dumps(governance, sort_keys=True) + "\n", encoding="utf-8")
    _rewrite_package(package_path, lambda package: package.update(governance_manifest_sha256=governance["integrity"]["manifest_sha256"]))

    result = verify_r46_manifest(governance_path, data_package_manifest_path=package_path, repo_root=tmp_path)

    assert "governance_source_capability_receipt" in result["blockers"]


def test_verify_r46_manifest_rejects_receipt_path_substitution(tmp_path: Path) -> None:
    governance_path, package_path = _build_fixture(tmp_path)
    _rewrite_package(package_path, lambda package: package["pit_source_receipt"].update(path="ledger_receipt.json"))

    result = verify_r46_manifest(governance_path, data_package_manifest_path=package_path, repo_root=tmp_path)

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "BLOCKED_PIT_SOURCE" in result["blockers"]


def test_verify_r46_manifest_rejects_embedded_attestation_even_with_receipts(tmp_path: Path) -> None:
    governance_path, package_path = _build_fixture(tmp_path)
    _rewrite_package(package_path, lambda package: package.update(pit_source_attestation={"status": "READY"}))

    result = verify_r46_manifest(governance_path, data_package_manifest_path=package_path, repo_root=tmp_path)

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "embedded_attestation_forbidden" in result["blockers"]


def test_live_governance_manifest_routes_to_truthful_block_without_manifest_schema() -> None:
    result = verify_r46_manifest(LIVE_MANIFEST, repo_root=REPO)

    assert result["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "manifest_schema" not in result["blockers"]
    assert "data_package_manifest_required" in result["blockers"]
    assert "BLOCKED_STATISTICS" in result["blockers"]


def test_preflight_module_cli_accepts_frozen_invocation_for_live_governance_manifest() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.research.r46_preflight_2026_09_10", str(LIVE_MANIFEST)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "DATA_ACQUISITION_REQUIRED"
    assert "manifest_schema" not in payload["blockers"]


def test_v2_outcome_scanner_normalizes_keys_without_scanning_prose(tmp_path: Path) -> None:
    prose_path = tmp_path / "review.txt"
    prose_path.write_text('{"comment": "returns and sharpe are discussed"}\n', encoding="utf-8")
    key_path = tmp_path / "review.txt.jsonl"
    key_path.write_text('{"  PnL  ": 1}\n', encoding="utf-8")
    punctuation_key_path = tmp_path / "review.bin"
    punctuation_key_path.write_text('{" P & L ": 1}\n', encoding="utf-8")

    assert v2_preflight._artifact_contains_forbidden(prose_path) is False
    assert v2_preflight._artifact_contains_forbidden(key_path) is True
    assert v2_preflight._artifact_contains_forbidden(punctuation_key_path) is True


def test_v2_independent_review_has_strict_schema_and_path_allowlist(tmp_path: Path) -> None:
    manifest = {
        "research_id": "r46_treasury_auction_unwind_tlt_v2_2026_09_10",
        "candidate_id": "R46",
        "integrity": {"manifest_sha256": "a" * 64},
    }
    source = {"source_commit": "b" * 40, "source_files": {"runner.py": {"path": "runner.py"}}}
    review: dict[str, Any] = {
        "schema_version": "r46-independent-software-review-v1",
        "status": "REVIEWED",
        "research_id": manifest["research_id"],
        "candidate_id": "R46",
        "reviewed_commit": source["source_commit"],
        "governance_manifest_sha256": manifest["integrity"]["manifest_sha256"],
        "independence": "independent_software_review",
        "reviewed_paths": ["../runner.py"],
        "returns": "must not be an outcome field",
        "integrity": {"review_sha256": ""},
    }
    review["integrity"]["review_sha256"] = v2_preflight.canonical_hash(review, self_field="review_sha256")  # type: ignore[index]
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(review, sort_keys=True) + "\n", encoding="utf-8")

    reasons, _ = v2_preflight._validate_independent_review(review_path, manifest, source, root=tmp_path)
    assert "INDEPENDENT_SOFTWARE_REVIEW_OUTCOME_FIELDS" in reasons
    assert "INDEPENDENT_SOFTWARE_REVIEW_SCHEMA_KEYS" in reasons
    assert "INDEPENDENT_SOFTWARE_REVIEW_PATH_INVALID" in reasons


def test_v2_source_provenance_requires_base_commit_ancestor_and_required_blobs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    runner_path = tmp_path / "runner.py"
    lock_path = tmp_path / "uv.lock"
    runner_path.write_text("runner\n", encoding="utf-8")
    lock_path.write_text("lock\n", encoding="utf-8")
    runner_hash = v2_preflight.sha256_file(runner_path)
    lock_hash = v2_preflight.sha256_file(lock_path)
    base_commit = "b" * 40
    source_commit = "c" * 40
    freeze_commit = "d" * 40
    source_spec = {
        "path": "runner.py",
        "sha256": runner_hash,
        "git_blob_sha256": "a" * 64,
        "required_in_base_commit": True,
        "required_in_source_commit": True,
    }
    source_files: dict[str, Any] = {"runner.py": dict(source_spec)}
    for role in v2_preflight.REQUIRED_SOURCE_ROLES:
        source_files.setdefault(f"{role}.py", {**source_spec, "path": "runner.py"})
    source_files["uv.lock"] = {
        "path": "uv.lock",
        "sha256": lock_hash,
        "git_blob_sha256": "c" * 64,
        "required_in_base_commit": True,
        "required_in_source_commit": True,
    }
    source: dict[str, Any] = {
        "base_commit": base_commit,
        "source_commit": source_commit,
        "clean_tree_required": True,
        "clean_tree_observed": True,
        "source_files": source_files,
        "environment": {
            **v2_preflight._runtime_environment(),
            "dependency_lock_path": "uv.lock",
            "dependency_lock_sha256": lock_hash,
            "dependency_lock_git_blob_sha256": "c" * 64,
        },
    }
    for role in v2_preflight.REQUIRED_SOURCE_ROLES:
        source[role] = dict(source_spec)
    monkeypatch.setattr(v2_preflight, "_git_commit_exists", lambda root, commit: True)
    monkeypatch.setattr(v2_preflight, "_git_head", lambda root: freeze_commit)
    monkeypatch.setattr(v2_preflight, "_git_is_ancestor", lambda root, ancestor, descendant: ancestor != base_commit)
    monkeypatch.setattr(v2_preflight, "_git_diff_paths", lambda root, source, freeze: [])
    monkeypatch.setattr(v2_preflight, "_git_worktree_clean", lambda root, **kwargs: True)
    monkeypatch.setattr(
        v2_preflight,
        "_git_blob_sha256",
        lambda root, commit, relative: None if commit == base_commit else ("c" * 64 if relative == "uv.lock" else "a" * 64),
    )

    reasons = v2_preflight._validate_source_provenance(source, tmp_path, freeze_commit=freeze_commit)
    assert "BASE_COMMIT_NOT_ANCESTOR_OF_SOURCE" in reasons
    assert "SOURCE_FILE_NOT_IN_BASE_COMMIT:runner.py" in reasons


def test_v2_source_provenance_base_validation_skips_source_only_seam_but_checks_lifetimes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    lifetime_relative = "data/historical/equity_entity_lifetimes_full_v5.csv"
    seam_relative = "scripts/run_symmetric_v5_validation.py"
    lifetime_path = tmp_path / lifetime_relative
    seam_path = tmp_path / seam_relative
    lifetime_path.parent.mkdir(parents=True, exist_ok=True)
    seam_path.parent.mkdir(parents=True, exist_ok=True)
    lifetime_path.write_text("lifetime\n", encoding="utf-8")
    seam_path.write_text("seam\n", encoding="utf-8")
    source_commit = "c" * 40
    base_commit = "b" * 40
    freeze_commit = "d" * 40
    source_blobs = {
        lifetime_relative: v2_preflight.sha256_file(lifetime_path),
        seam_relative: v2_preflight.sha256_file(seam_path),
    }
    source_files = {
        relative: {
            "path": relative,
            "sha256": blob,
            "git_blob_sha256": blob,
            "required_in_base_commit": relative == lifetime_relative,
            "required_in_source_commit": True,
        }
        for relative, blob in source_blobs.items()
    }
    source = {
        "base_commit": base_commit,
        "source_commit": source_commit,
        "clean_tree_required": True,
        "clean_tree_observed": True,
        "source_files": source_files,
    }
    base_blob_calls: list[str] = []

    monkeypatch.setattr(v2_preflight, "_git_commit_exists", lambda *_args: True)
    monkeypatch.setattr(v2_preflight, "_git_head", lambda _root: freeze_commit)
    monkeypatch.setattr(v2_preflight, "_git_is_ancestor", lambda *_args: True)
    monkeypatch.setattr(v2_preflight, "_git_diff_paths", lambda *_args: [])
    monkeypatch.setattr(v2_preflight, "_git_worktree_clean", lambda *_args, **_kwargs: True)

    def git_blob(_root: Path, commit: str, relative: str) -> str | None:
        if commit == base_commit:
            base_blob_calls.append(relative)
            return "0" * 64
        return source_blobs.get(relative)

    monkeypatch.setattr(v2_preflight, "_git_blob_sha256", git_blob)

    reasons = v2_preflight._validate_source_provenance(source, tmp_path, freeze_commit=freeze_commit)

    assert f"SOURCE_BASE_GIT_BLOB_HASH_MISMATCH:{lifetime_relative}" in reasons
    assert not any(seam_relative in reason for reason in reasons)
    assert base_blob_calls == [lifetime_relative]


def _v5_source_provenance_contract() -> dict[str, Any]:
    paths = {
        "bars": "data/historical/equity_pit_symmetric_2016_2026_v5.csv",
        "lifetimes": "data/historical/equity_entity_lifetimes_full_v5.csv",
        "corporate_actions": "data/historical/equity_survivorship_full_corporate_actions_v5.csv",
        "bars_manifest": "data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json",
        "corporate_actions_manifest": "data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json",
        "seam": "scripts/run_symmetric_v5_validation.py",
    }
    external = {"bars", "corporate_actions", "bars_manifest", "corporate_actions_manifest"}
    dependencies: dict[str, Any] = {
        "schema_version": "r46-incumbent-data-contract-v1",
        "historical_cutoff": "2026-07-31T23:59:59Z",
    }
    source_files: dict[str, Any] = {}
    for key, path in paths.items():
        spec = {
            "path": path,
            "required_in_base_commit": key == "lifetimes",
            "required_in_source_commit": key in {"lifetimes", "seam"},
            "git_blob_sha256": None if key in external else "a" * 64,
        }
        dependencies[key] = dict(spec)
        if key not in external:
            source_files[path] = dict(spec)
    return {"incumbent_dependencies": dependencies, "source_files": source_files}


def test_v2_v5_incumbent_policy_accepts_exact_contract() -> None:
    assert v2_preflight._validate_v5_incumbent_policy(_v5_source_provenance_contract()) == []


def test_v2_v5_incumbent_policy_rejects_lifetime_base_requirement_downgrade() -> None:
    source = _v5_source_provenance_contract()
    source["incumbent_dependencies"]["lifetimes"]["required_in_base_commit"] = False
    source["source_files"]["data/historical/equity_entity_lifetimes_full_v5.csv"][
        "required_in_base_commit"
    ] = False

    reasons = v2_preflight._validate_v5_incumbent_policy(source)

    assert "INCUMBENT_DATA_BASE_COMMIT_REQUIREMENT:lifetimes" in reasons


def test_v2_v5_incumbent_policy_rejects_seam_path_substitution() -> None:
    source = _v5_source_provenance_contract()
    seam_path = "scripts/run_symmetric_v5_validation.py"
    substituted_path = "scripts/run_symmetric_v5_validation_tampered.py"
    source["incumbent_dependencies"]["seam"]["path"] = substituted_path
    seam_spec = source["source_files"].pop(seam_path)
    seam_spec["path"] = substituted_path
    source["source_files"][substituted_path] = seam_spec

    reasons = v2_preflight._validate_v5_incumbent_policy(source)

    assert "INCUMBENT_DATA_PATH_MISMATCH:seam" in reasons
    assert "INCUMBENT_DATA_SOURCE_PIN:seam" in reasons


def test_v2_v5_incumbent_policy_rejects_external_flag_and_blob_tampering() -> None:
    source = _v5_source_provenance_contract()
    bars = source["incumbent_dependencies"]["bars"]
    bars["required_in_base_commit"] = True
    bars["required_in_source_commit"] = True
    bars["git_blob_sha256"] = "b" * 64

    reasons = v2_preflight._validate_v5_incumbent_policy(source)

    assert "INCUMBENT_DATA_BASE_COMMIT_REQUIREMENT:bars" in reasons
    assert "INCUMBENT_DATA_SOURCE_COMMIT_REQUIREMENT:bars" in reasons
    assert "INCUMBENT_DATA_EXTERNAL_BLOB_FORBIDDEN:bars" in reasons
