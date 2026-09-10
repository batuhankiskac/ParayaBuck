"""Adversarial tests for crossasset source and two-leg research gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.research.crossasset_preflight_2026_09_08 import x06_liquidation_evidence
from scripts.research.crossasset_receipt_verifier_2026_09_08 import verify_source_matrix
from scripts.research.crossasset_source_matrix_2026_09_08 import _parse_sec_document, _sec_headers
from scripts.research.crossasset_two_leg_ledger_2026_09_08 import build_ledger_report, validate_two_leg_rows


def test_existing_derivative_counts_and_fail_closed_statuses() -> None:
    report = build_ledger_report()
    assert report["X05"]["raw_rows"] == 1152
    assert report["X05"]["missing_available_at"] == 1152
    assert report["X05"]["admitted_rows"] == 0
    assert report["X06"]["raw_rows"] == 518
    assert report["X06"]["unique_event_keys"] == 259
    assert report["X06"]["exact_duplicate_rows"] == 259
    assert report["X06"]["missing_available_at"] == 518
    assert report["X06"]["admitted_rows"] == 0


def test_two_leg_schema_rejects_unpaired_or_duplicate_rows() -> None:
    row = {
        "family": "X04", "pair_id": "p1", "leg_id": "p1-long", "leg": "long", "venue": "public", "contract_type": "equity",
        "instrument": "ABC", "event_time_utc": "2026-01-01T00:00:00Z", "available_at": "2026-01-01T00:30:00Z", "decision_time_utc": "2026-01-01T01:00:00Z", "execution_time_utc": "2026-01-01T02:00:00Z", "execution_order": 1, "side": "BUY",
        "quantity": 1, "price": 10, "fee": 0, "slippage": 0.1, "borrow_rate": 0.01, "financing_rate": 0.0, "locate_status": "available", "recall_flag": False, "collateral": 100, "margin_requirement": 10, "source_file": "x", "source_sha256": "a" * 64,
    }
    result = validate_two_leg_rows([row, {**row, "event_time_utc": "2026-01-01T00:00:00Z"}], family="X04")
    assert result["synchronized_long_short"] is False
    assert result["admitted_rows"] == 0
    assert "duplicate_primary_key" in result["errors"]


def test_two_leg_schema_rejects_post_cutoff_and_bad_numbers() -> None:
    row = {"family": "X05", "pair_id": "p1", "leg_id": "p1-short", "leg": "short", "venue": "binance", "contract_type": "perpetual", "instrument": "BTCUSDT", "event_time_utc": "2026-08-01T00:00:00Z", "available_at": "2026-08-01T00:30:00Z", "decision_time_utc": "2026-08-01T01:00:00Z", "execution_time_utc": "2026-08-01T02:00:00Z", "execution_order": 2, "side": "SELL", "quantity": float("nan"), "price": 10, "fee": 0, "slippage": 0.1, "borrow_rate": 0.01, "financing_rate": 0.0, "locate_status": "available", "recall_flag": False, "collateral": 100, "margin_requirement": 10, "source_file": "x", "source_sha256": "a" * 64}
    result = validate_two_leg_rows([row], family="X05")
    assert result["admitted_rows"] == 0
    assert {"post_cutoff", "numeric:quantity"}.issubset(result["errors"])


def test_two_leg_rejects_mismatched_pair_key_and_blank_causal_fields() -> None:
    base = {
        "family": "X04", "pair_id": "p2", "leg": "long", "venue": "public", "contract_type": "equity",
        "instrument": "ABC", "event_time_utc": "2026-01-01T00:00:00Z", "available_at": "2026-01-01T00:30:00Z", "decision_time_utc": "2026-01-01T01:00:00Z", "execution_time_utc": "2026-01-01T02:00:00Z", "execution_order": 1, "side": "BUY",
        "quantity": 1, "price": 10, "fee": 0, "slippage": 0.1, "borrow_rate": 0.01, "financing_rate": 0.0, "locate_status": "available", "recall_flag": False, "collateral": 100, "margin_requirement": 10, "source_file": "fixture.csv", "source_sha256": "b" * 64, "leg_id": "p2-long",
    }
    short = {**base, "leg": "short", "leg_id": "p2-short", "execution_order": 2, "instrument": "DEF", "available_at": "", "source_sha256": ""}
    result = validate_two_leg_rows([base, short], family="X04")
    assert result["admitted_rows"] == 0
    assert {"pair_key_mismatch", "timestamp:available_at", "source_sha256"}.issubset(result["errors"])


def test_two_leg_rejects_missing_source_and_zero_margin() -> None:
    row = {
        "family": "X04", "pair_id": "p3", "leg_id": "p3-long", "leg": "long", "venue": "public", "contract_type": "equity",
        "instrument": "ABC", "event_time_utc": "2026-01-01T00:00:00Z", "available_at": "2026-01-01T00:30:00Z", "decision_time_utc": "2026-01-01T01:00:00Z", "execution_time_utc": "2026-01-01T02:00:00Z", "execution_order": 1, "side": "BUY",
        "quantity": 1, "price": 10, "fee": 0, "slippage": 0.1, "borrow_rate": 0.0, "financing_rate": 0.0, "locate_status": "available", "recall_flag": False, "collateral": 0, "margin_requirement": 0, "source_file": "data/research/external/does-not-exist.csv", "source_sha256": "a" * 64,
    }
    result = validate_two_leg_rows([row], family="X04")
    assert {"source_file", "margin_requirement_positive", "collateral_positive_or_sufficient"}.issubset(result["errors"])


def test_two_leg_valid_pair_requires_real_hash_pinned_source() -> None:
    source = Path("data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K09_binance_oi_metrics_archive_pilot.csv")
    import hashlib
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    base = {
        "family": "X05", "pair_id": "p4", "venue": "binance", "contract_type": "perpetual", "instrument": "BTCUSDT",
        "event_time_utc": "2026-01-01T00:00:00Z", "available_at": "2026-01-01T00:30:00Z", "decision_time_utc": "2026-01-01T01:00:00Z", "execution_time_utc": "2026-01-01T02:00:00Z", "side": "BUY",
        "quantity": 1, "price": 10, "fee": 0, "slippage": 0.1, "borrow_rate": 0.0, "financing_rate": 0.0, "locate_status": "available", "recall_flag": False, "collateral": 100, "margin_requirement": 10, "source_file": str(source), "source_sha256": digest,
    }
    result = validate_two_leg_rows([
        {**base, "leg_id": "p4-long", "leg": "long", "execution_order": 1},
        {**base, "leg_id": "p4-short", "leg": "short", "execution_order": 2, "side": "SELL", "borrow_rate": 0.01},
    ], family="X05")
    assert result["errors"] == []
    assert result["admitted_rows"] == 2


def test_matrix_records_are_hash_pinned_and_field_explicit() -> None:
    path = Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json")
    if not path.is_file():
        pytest.skip("network acquisition has not been run")
    matrix = json.loads(path.read_text(encoding="utf-8"))
    assert matrix["target_cutoff"] == "2026-07-31T23:59:59Z"
    assert matrix["records"]
    assert all("required_fields" in record and "status" in record for record in matrix["records"])


def test_nport_parser_extracts_actual_structured_holdings() -> None:
    path = Path("data/research/external/crossasset_public_2026_09_07/x03_sec_nport/MCHI_NPORT_2025-05-31_raw.xml")
    parsed = _parse_sec_document(path, "NPORT-P")
    assert parsed["structured_holdings_supported"] is True
    assert parsed["holding_count"] == 565
    assert parsed["holding_field_presence"]["issuer"] == 565
    assert parsed["holding_field_presence"]["value_usd"] == 565
    assert parsed["holding_rows"][0]["title"]
    assert parsed["holding_rows"][0]["document_identity"]["source_sha256"]
    assert parsed["holding_rows"][0]["document_identity"]["series_ids"] == ["S000031717"]


def test_x06_card_is_independent_liquidation_evidence() -> None:
    card = x06_liquidation_evidence()
    assert card["raw_rows"] == 518
    assert card["unique_event_keys"] == 259
    assert card["exact_duplicate_rows"] == 259
    assert card["missing_available_at"] == 518
    assert "spot_rows" not in card and "perpetual_rows" not in card


def test_sec_user_agent_is_process_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT_REQUIRED"):
        _sec_headers()
    monkeypatch.setenv("SEC_USER_AGENT", "test-agent")
    assert _sec_headers()["User-Agent"] == "test-agent"


def test_x03_matrix_retains_parse_count_without_claiming_binding() -> None:
    matrix = json.loads(Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json").read_text(encoding="utf-8"))
    record = next(row for row in matrix["records"] if row.get("source_id") == "SEC_existing_MCHI_NPORT_structured_parse_2025-05-31")
    assert record["parsed_holding_rows"] == 565
    assert record["holding_count"] == 0
    assert record["holding_rows"] == []
    assert record["binding_status"] == "METADATA_ONLY_UNBOUND_LOCAL_XML"
    assert record["admitted_holding_rows"] == 0
    assert record["document_identity"]["source_sha256"]
    assert record["document_identity"]["submission_index_sha256"]


def test_x03_overclaim_fails_closed(tmp_path: Path) -> None:
    source = Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json")
    value = json.loads(source.read_text(encoding="utf-8"))
    record = next(row for row in value["records"] if row.get("source_id") == "SEC_existing_MCHI_NPORT_structured_parse_2025-05-31")
    record["structured_holdings_supported"] = True
    record["holding_count"] = 565
    value["matrix_sha256"] = __import__("scripts.research.crossasset_receipt_verifier_2026_09_08", fromlist=["canonical_hash"]).canonical_hash(value, "matrix_sha256")
    target = tmp_path / source.name
    target.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(AssertionError, match="structured holding count/binding mismatch"):
        verify_source_matrix(target)
