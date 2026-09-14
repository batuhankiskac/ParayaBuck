from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.research_testkit import canonical_hash

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/research/h06_structural_blocker_closure_2026_09_14.py"
SPEC = importlib.util.spec_from_file_location("h06_closure", SCRIPT)
assert SPEC and SPEC.loader
h06 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h06)
pytestmark = pytest.mark.research


def _fetch(status: int, body: bytes | None):
    return lambda: (status, body)


def _fact(**updates):
    row = {
        "tag": "dei:EntityCommonStockSharesOutstanding",
        "form": "10-Q",
        "issuer_cik": "0000000001",
        "accession": "0000000001-24-000001",
        "accepted_at": "2024-05-06T16:00:00Z",
        "available_at": "2024-05-08T13:30:00Z",
        "fact_instant": "2024-05-01",
        "value": "100",
        "unit": "shares",
        "context_id": "c-1",
        "dimensions": [],
        "security_class": "Common Stock",
        "decimals": "0",
        "scale": "0",
        "cover_text_agrees": True,
        "source_sha256": "f" * 64,
    }
    row.update(updates)
    return row


def test_filing_specific_success_fixture() -> None:
    result = h06.retrieve_with_fallback("0001-24-000001", _fetch(200, b"0001-24-000001"), _fetch(500, None), _fetch(500, None))
    assert result["status"] == "SEC_RECEIPT_BOUND"
    assert result["route"] == "FILING_SPECIFIC"
    assert len(result["attempts"]) == 1


def test_filing_specific_403_uses_feed() -> None:
    result = h06.retrieve_with_fallback("a", _fetch(403, None), _fetch(200, b"a"), _fetch(500, None))
    assert result["route"] == "FEED"


def test_feed_requires_exact_accession() -> None:
    result = h06.retrieve_with_fallback(
        "wanted", _fetch(403, None), _fetch(200, b"unwanted-accession"), _fetch(200, b"wanted")
    )
    assert result["route"] == "OLDLOADS"


def test_oldloads_exact_accession() -> None:
    result = h06.retrieve_with_fallback("wanted", _fetch(403, None), _fetch(404, None), _fetch(200, b"wanted"))
    assert result["status"] == "SEC_RECEIPT_BOUND"


def test_browser_only_evidence_cannot_admit() -> None:
    assert not h06.receipt_bound({"persisted": False, "path": None, "sha256": None})


def test_common_single_class_identity_exact() -> None:
    status = h06.resolve_identity(receipt_ok=True, cik_exact=True, cover_symbol="ABC", form4_symbol="ABC",
                                  cover_title="Class A Common Stock", transaction_title="Common Stock",
                                  v5_matches=1, provider_id="uuid", ticker_reuse_resolved=True)
    assert status == "IDENTITY_EXACT"


def test_ambiguous_ticker_change_excluded() -> None:
    status = h06.resolve_identity(receipt_ok=True, cik_exact=True, cover_symbol="OLD", form4_symbol="NEW",
                                  cover_title="Common Stock", transaction_title="Common Stock",
                                  v5_matches=1, provider_id="uuid", ticker_reuse_resolved=False)
    assert status == "IDENTITY_AMBIGUOUS"


def test_multi_class_unresolved_excluded() -> None:
    assert h06.resolve_identity(receipt_ok=True, cik_exact=True, cover_symbol="GOOGL", form4_symbol="GOOGL",
                                cover_title="Class A Common Stock", transaction_title="Class C Capital Stock",
                                v5_matches=1, provider_id="uuid", ticker_reuse_resolved=True) == "IDENTITY_AMBIGUOUS"


def test_non_common_excluded() -> None:
    assert h06.classify_security("0.875% Notes due 2025") == "NOTE_OR_DEBT"


def test_future_filing_cannot_rewrite_past() -> None:
    facts = [_fact(), _fact(accepted_at="2024-06-01T00:00:00Z", available_at="2024-06-04T13:30:00Z", value="200")]
    result = h06.select_shares_fact(facts, datetime(2024, 5, 31, 20, tzinfo=UTC), "Common Stock")
    assert result["fact"]["value"] == "100"


def test_h06_timing_v2_enforced() -> None:
    accepted = datetime.fromisoformat("2024-05-06T12:00:00-04:00")
    assert h06.h06_timing_v2(accepted).astimezone(h06.NY).isoformat().startswith("2024-05-08T09:30:00")


def test_exact_shares_fact_selection() -> None:
    assert h06.select_shares_fact([_fact()], datetime(2024, 5, 31, tzinfo=UTC), "Common Stock")["status"] == "SHARES_FACT_EXACT"


def test_future_shares_filing_excluded() -> None:
    fact = _fact(accepted_at="2024-06-01T00:00:00Z", available_at="2024-06-04T13:30:00Z")
    assert h06.select_shares_fact([fact], datetime(2024, 5, 31, tzinfo=UTC), "Common Stock")["status"] == "SHARES_FACT_MISSING"


def test_future_fact_instant_excluded() -> None:
    fact = _fact(fact_instant="2024-06-01")
    assert h06.select_shares_fact([fact], datetime(2024, 5, 31, tzinfo=UTC), "Common Stock")["status"] == "SHARES_FACT_MISSING"


def test_conflicting_contexts_excluded() -> None:
    facts = [_fact(), _fact(context_id="c-2", value="101")]
    assert h06.select_shares_fact(facts, datetime(2024, 5, 31, tzinfo=UTC), "Common Stock")["status"] == "SHARES_FACT_CONTEXT_CONFLICT"


def test_scaling_suspect_excluded() -> None:
    fact = _fact(value="1000000000000000")
    assert h06.select_shares_fact([fact], datetime(2024, 5, 31, tzinfo=UTC), "Common Stock")["status"] == "SHARES_FACT_SCALING_SUSPECT"


def test_raw_price_request_uses_sip() -> None:
    assert h06.raw_request_params("ABC", "2024-01-01", "2024-01-02")["feed"] == "sip"


def test_raw_request_uses_raw_adjustment() -> None:
    assert h06.raw_request_params("ABC", "2024-01-01", "2024-01-02")["adjustment"] == "raw"


def test_split_canary_uses_split_adjustment() -> None:
    assert h06.split_request_params("ABC", "2024-01-01", "2024-01-02")["adjustment"] == "split"


def test_raw_split_no_split_equality_fixture() -> None:
    assert h06.canary_ratio(Decimal("100"), Decimal("100")) == 1


def test_forward_split_mechanical_canary() -> None:
    assert h06.canary_ratio(Decimal("1000"), Decimal("100")) == 10


def test_reverse_split_mechanical_canary() -> None:
    assert h06.canary_ratio(Decimal("12.5"), Decimal("100")) == Decimal("0.125")


def test_persisted_canaries_and_v5_parity_are_rederived() -> None:
    summary = h06.derive_split_canary()
    assert summary["raw_split_semantics"] == "PROVIDER_MECHANICAL_CANARY_PASS"
    assert summary["v5_sample_split_parity"] == "V5_SAMPLE_SPLIT_PARITY_PASS"
    assert [(row["case"], row["v5_exact_ohlcv_parity_rows"]) for row in summary["derived_cases"]] == [
        ("no_split", 5),
        ("forward_split", 5),
        ("reverse_split", 5),
    ]


def test_raw_market_cap_structural_multiplication() -> None:
    result = h06.structural_market_cap(identity_status="IDENTITY_EXACT", shares_status="SHARES_FACT_EXACT",
                                       price_status="RAW_COMPLETED_SESSION_CLOSE_EXACT", shares=Decimal("100"), raw_close=Decimal("25"))
    assert result == {"status": "RAW_MARKET_CAP_BASIS_EXACT", "label": h06.DENOMINATOR_LABEL,
                      "shares": "100", "raw_close": "25", "structural_denominator": "2500"}


def test_intervening_split_blocks_direct_multiplication() -> None:
    result = h06.structural_market_cap(identity_status="IDENTITY_EXACT", shares_status="SHARES_FACT_EXACT",
                                       price_status="RAW_COMPLETED_SESSION_CLOSE_EXACT", shares=Decimal("100"),
                                       raw_close=Decimal("25"), intervening_split=True)
    assert result["status"] == "BLOCKED_INTERVENING_SPLIT_BASIS"


def test_no_h06_score_division() -> None:
    source = SCRIPT.read_text().casefold()
    assert "purchase value" not in source
    assert "score_i" not in source


def test_no_ranking_or_returns_or_pnl() -> None:
    closure = json.loads((ROOT / "data/research_manifests/h06_structural_blocker_closure_2026_09_14.json").read_text())
    assert closure["boundaries"]["ranking"] is False
    assert closure["boundaries"]["returns_inspected"] is False
    assert closure["boundaries"]["pnl"] is False


def test_no_economic_runner_import_or_use() -> None:
    source = SCRIPT.read_text().casefold()
    assert "backtest" not in "\n".join(line for line in source.splitlines() if line.startswith(("import ", "from ")))
    assert "run_equity_local_diagnostics" not in source


def test_v5_bytes_unchanged() -> None:
    for relative, expected in h06.EXPECTED_V5.items():
        assert h06.sha256_file(ROOT / relative) == expected


def test_artifact_self_hashes() -> None:
    for path, field in ((h06.SEC_RECEIPT, "receipt_sha256"), (h06.SIP_RECEIPT, "receipt_sha256"),
                        (h06.RAW_MANIFEST, "manifest_sha256"), (h06.CLOSURE, "manifest_sha256")):
        payload = json.loads(path.read_text())
        assert payload["integrity"][field] == h06.nested_self_hash(payload, field)
        assert payload["integrity"][field] == canonical_hash(payload, field, container="integrity")


def test_external_evidence_hashes() -> None:
    for name, expected in h06.EVIDENCE_SHA.items():
        assert h06.sha256_file(h06.EXT / name) == expected


def test_submission_header_and_primary_document_are_exactly_bound() -> None:
    parsed = h06.parse_submission(
        h06.EXT / "aapl-0000320193-24-000123.txt",
        "0000320193-24-000123",
        "aapl-20240928.htm",
    )
    assert parsed["accepted_at"] == "2024-11-01T10:01:36Z"
    assert parsed["primary_sha256"] == h06.EVIDENCE_SHA["aapl-20240928.htm"]
    with pytest.raises(ValueError, match="submission_header_mismatch"):
        h06.parse_submission(h06.EXT / "aapl-0000320193-24-000123.txt", "wrong-accession")


def test_wrapped_exhibit_is_bound_to_submission_document() -> None:
    parsed = h06.parse_submission(
        h06.EXT / "meta-0001326801-22-000070.txt",
        "0001326801-22-000070",
        "may312022-exhibit991.htm",
    )
    assert parsed["primary_sha256"] == h06.EVIDENCE_SHA["may312022-exhibit991.htm"]


@pytest.mark.parametrize(
    ("symbol", "shares", "scale", "instant", "decision_at", "close", "denominator"),
    [
        ("AAPL", "15115823000", "0", "2024-10-18", "2024-11-29T18:00:00Z", "237.33", "3587438272590.00"),
        ("NVDA", "2460000000", "9", "2024-05-24", "2024-05-31T20:00:00Z", "1096.33", "2696971800000.00"),
    ],
)
def test_admission_is_derived_from_receipt_bound_sources(
    symbol: str, shares: str, scale: str, instant: str, decision_at: str, close: str, denominator: str
) -> None:
    row = h06.derive_admission(symbol)
    assert row["identity_status"] == "IDENTITY_EXACT"
    assert row["v5"]["ticker_reuse_resolved"] is True
    assert row["submission_inventory"]["latest_visible_filing"]["accession"] == row["submission"]["accession"]
    assert row["submission_inventory"]["later_visible_amendment"] is False
    assert row["shares_status"] == "SHARES_FACT_EXACT"
    assert row["shares"]["value"] == shares
    assert row["shares"]["scale"] == scale
    assert row["shares"]["instant"] == instant
    assert row["shares"]["dimensions"] == []
    assert row["raw_price"]["decision_at"] == decision_at
    assert row["raw_price"]["close"] == close
    assert row["intervening_splits"] == []
    assert row["denominator"]["structural_denominator"] == denominator


def test_receipt_records_bind_official_urls_and_accessions() -> None:
    receipt = json.loads(h06.SEC_RECEIPT.read_text())
    evidence = [item for case in receipt["cases"] for item in case["evidence"]]
    assert all(item["official_url"].startswith("https://www.sec.gov/Archives/edgar/data/") for item in evidence)
    assert all(item["accession"] and item["role"] for item in evidence)
    assert all(item["accepted_at"].endswith("Z") and item["filing_date"] for item in evidence)


def test_ge_2021_identity_is_not_backfilled_from_2026_form4() -> None:
    closure = json.loads(h06.CLOSURE.read_text())
    ge = next(row for row in closure["identity_cases"] if row["case"] == "GE")
    assert ge["status"] == "IDENTITY_MISSING"
    assert ge["disposition"] == "EXCLUDED_FAIL_CLOSED"


def test_selected_filing_must_be_latest_visible() -> None:
    decision = h06.parse_time("2024-11-29T18:00:00Z")
    with pytest.raises(ValueError, match="selected_filing_not_latest_visible"):
        h06.submission_inventory(
            h06.EXT / "CIK0000320193-submissions.json", "0000320193", decision, "0000320193-24-000081"
        )


def test_read_only_verifier_rederives_complete_package() -> None:
    assert h06.verify() == {
        "status": "H06_STRUCTURAL_BLOCKER_CLOSURE_VERIFIED",
        "read_only": True,
        "economic_run": False,
    }


def test_final_structural_status_and_protocols() -> None:
    closure = json.loads(h06.CLOSURE.read_text())
    assert closure["status"] == "H06_STRUCTURAL_BLOCKERS_CLOSED"
    assert closure["remaining_blockers"] == []
    assert closure["protocol"] == {
        "shares": "LATEST_ACCEPTED_REPORTED_SHARES_IS_PROTOCOL_COMPATIBLE",
        "raw_price": "RAW_COMPLETED_SESSION_CLOSE_IS_PROTOCOL_COMPATIBLE",
        "formula": "H06_RAW_MARKETCAP_IS_FAITHFUL_IMPLEMENTATION_OF_FROZEN_FORMULA",
    }


def test_v5_split_basis_is_irrelevant_to_denominator() -> None:
    raw = json.loads(h06.RAW_MANIFEST.read_text())
    assert raw["v5_split_basis"] == "V5_SPLIT_BASIS_IRRELEVANT_TO_H06_DENOMINATOR"
    assert raw["future_split_normalization"] == "H06_FUTURE_SPLIT_NORMALIZATION_NOT_REQUIRED_FOR_DENOMINATOR"


def test_receipts_do_not_contain_credentials() -> None:
    combined = h06.SEC_RECEIPT.read_text() + h06.SIP_RECEIPT.read_text()
    assert "APCA-API-KEY-ID" not in combined
    assert "APCA-API-SECRET-KEY" not in combined
