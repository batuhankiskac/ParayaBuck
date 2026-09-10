from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from scripts.research import equity_secondary_causal_gate_2026_09_08 as gate


def test_contracts_freeze_all_ten_candidates() -> None:
    assert set(gate.CONTRACTS) == {"H06", "H07", "H08", "H09", "H10", "H11", "H12", "H13", "H15", "H16"}
    for contract in gate.CONTRACTS.values():
        assert contract["fields"]
        assert contract["availability"]
        assert contract["formula"]
        assert contract["execution"]
        assert contract["controls"]
        assert contract["cost"] == [1.0, 2.0, 3.0]
        assert set(contract["cells"]) == {"full", "recent", "holdout"}


def test_report_is_fail_closed_and_no_economics() -> None:
    report = gate.build_report()
    assert report["status"] == "DONE_WITH_CONCERNS"
    assert report["economic_run"] is False
    assert set(report["cards"]) == set(gate.CONTRACTS)
    assert all(card["economic_signal_rows"] == 0 for card in report["cards"].values())
    assert report["cards"]["H06"]["acquired_valid"] >= 250
    assert report["cards"]["H06"]["acquired_invalid"] <= 7
    assert report["cards"]["H10"]["original_filing_documents"] == 333


def test_sec_receipt_backed_pilot_has_acceptance_and_identity() -> None:
    gate.verify_sec_documents()
    h06 = gate.gate_h06()
    h10 = gate.gate_h10()
    assert h06["form4_through_cutoff"] == h06["form4_documents"]
    assert h06["form4_documents"] >= 250
    assert h10["filings_through_cutoff"] == 333
    assert h10["acceptance_time_rows"] == 333


def test_daily_ohlcv_cannot_pass_h13() -> None:
    result = gate.gate_h13()
    assert result["daily_bar_rows"] > 1_000_000
    assert result["intraday_rows"] == 0
    assert result["status"] == "DATA_BLOCKED"
    assert result["economic_signal_rows"] == 0


def test_manifest_verifies_and_detects_mutation(tmp_path: Path) -> None:
    verified = gate.verify_manifest()
    assert verified["status"] == "FROZEN_EQUITY_SECONDARY_MANIFEST_VERIFIED"
    payload = json.loads(gate.MANIFEST.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(payload)
    mutated["economic_run"] = True
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_canonical_hash_mismatch"):
        gate.verify_manifest(path)


def test_report_text_matches_machine_statuses() -> None:
    report = gate.build_report()
    text = gate.REPORT_MD.read_text(encoding="utf-8")
    for key, card in report["cards"].items():
        assert f"| {key} | `{card['status']}` |" in text


def test_review2_pins_row_counts_and_provenance() -> None:
    bound = gate.verify_review2_package()
    assert bound == {
        "status": "REVIEW_R2_BOUND",
        "manifest_sha256": "d7f53f4abdd9ebcf094a910af6b952e20d54d90a8f7461f2402b913bcbaf3e50",
        "canonical_self_sha256": "423c747409931b2a39d5c5ff6e7f7ffcc5be1558ae4e02dee95298e621bb4cd1",
        "inventory_sha256": "76064376e5b961982fcb178a1ceddf8258f1966f34c13e6b000365f9bac3c10b",
        "report_sha256": "9c05871e92a2645f52c00694eb6aa19250bfd55e5350334ac9278aa5e21a1710",
        "parser_receipt_sha256": "79aba569ee7314122fd4745895d67a0070520e7c94a00d947a63561bc8bf79c2",
        "artifact_count": 41,
        "route_count": 11,
        "h09_rows": 2,
        "h09_admitted": 0,
        "h10_causal_revision_rows": 3,
        "h15_lifecycle_rows": 1,
        "h15_economic_rows": 0,
        "h16_rows": 0,
    }
    report = json.loads(gate.REPORT.read_text(encoding="utf-8"))
    assert report["review2_binding"] == bound
    assert report["cards"]["H09"]["relationship_rows"] == 2
    assert report["cards"]["H09"]["admitted_documents"] == 0
    assert report["cards"]["H10"]["causal_revision_event_rows"] == 3
    assert report["cards"]["H15"]["lifecycle_metadata_rows"] == 1
    assert report["cards"]["H15"]["economic_admissible_rows"] == 0
    assert report["cards"]["H16"]["spinoff_event_rows"] == 0


def test_local_normalization_package_bindings_are_receipt_pinned() -> None:
    report = gate.build_report()
    expected = {
        "H06": (3993, "0b1c827b246d8d95cbf1e1a92089766c13caa690095917094b8b12e5e3395f14"),
        "H08": (1407, "40013d4fbb9cfbae45cf501c5621fe2014434fc31c446ebbd0544a91ac6f1a08"),
        "H11": (565, "d2906147c6e4b5af4a9696ff4dcc0949a3d36af76f2dabb9cc743eb14032e7e0"),
        "H15": (1, "40013d4fbb9cfbae45cf501c5621fe2014434fc31c446ebbd0544a91ac6f1a08"),
        "H16": (0, "423c747409931b2a39d5c5ff6e7f7ffcc5be1558ae4e02dee95298e621bb4cd1"),
    }
    for card, (rows, canonical) in expected.items():
        binding = report["cards"][card]["package_bindings"][0]
        assert binding["status"] == "VERIFIED"
        assert binding["manifest_canonical_sha256"] == canonical
        assert binding["economic_run"] is False
        assert binding["local_engineering_gap"] is False
        assert binding["next_stage_status"] == "NOT_APPROVED"
        assert binding["files"]
        if card == "H06":
            assert report["cards"][card]["inventory_documents"] == rows
        elif card == "H08":
            assert report["cards"][card]["inventory_documents"] == rows
        elif card == "H11":
            assert report["cards"][card]["nport_investment_rows"] == rows
        elif card == "H15":
            assert report["cards"][card]["lifecycle_metadata_rows"] == rows
        else:
            assert report["cards"][card]["spinoff_event_rows"] == rows


def test_h08_h11_h15_normalized_statuses_are_explicit() -> None:
    report = gate.build_report()["cards"]
    assert (report["H08"]["any_valid_body_accessions"], report["H08"]["latest_attempt_valid_accessions"], report["H08"]["conflict_accessions"], report["H08"]["no_valid_body_accessions"]) == (999, 991, 8, 408)
    assert (report["H11"]["parsed_documents"], report["H11"]["identity_groups"], report["H11"]["duplicate_identity_rows"]) == (565, 516, 49)
    assert (report["H15"]["agreement_date"], report["H15"]["amendment_restated_date"], report["H15"]["announcement_at_rows"], report["H15"]["economic_admissible_rows"]) == ("2024-03-12", "2024-05-20", 0, 0)
    assert report["H16"]["zero_result_semantics"] == "NO_RECEIPT_VALID_SAMPLE_ACQUIRED; NOT_GLOBAL_ABSENCE"


def test_final_package_has_no_unresolved_acquisition_markers() -> None:
    matrix = json.loads(gate.SOURCE_MATRIX.read_text(encoding="utf-8"))
    sources = [source for card in matrix["cards"].values() for source in card["sources"]]
    assert all(source.get("status") != "NEXT_PUBLIC_PROBE" for source in sources)
    report = json.loads(gate.REPORT.read_text(encoding="utf-8"))
    assert all(card.get("pending_documents", 0) == 0 for card in report["cards"].values())
    assert all(card.get("retryable_documents", 0) == 0 for card in report["cards"].values())
    assert "NEXT_PUBLIC_PROBE" not in json.dumps(report)
    manifest = json.loads(gate.MANIFEST.read_text(encoding="utf-8"))
    assert all(str(gate.REVIEW2_ROOT.relative_to(gate.REPO)) in path for path in manifest["inputs"] if path.endswith("review_r2.json") or "review_r2/" in path)


def test_h06_disposition_is_complete_without_stale_retry_blockers() -> None:
    card = gate.gate_h06()
    assert card["inventory_documents"] == 3993
    assert card["acquired_valid"] == 3993
    assert card["acquired_invalid"] == 0
    assert card["terminal_documents"] == 0
    assert card["pending_documents"] == 0
    assert card["disposition_accounted_documents"] == 3993
    assert card["disposition_complete"] is True
    assert all("inventory" not in blocker.lower() or "incomplete" not in blocker.lower() for blocker in card["blockers"])
    assert all("retry" not in blocker.lower() for blocker in card["blockers"])


def test_h12_documented_official_routes_are_receipt_bound_and_nonadmissible() -> None:
    card = gate.gate_h12()
    urls = {row["url"] for row in card["route_statuses"]}
    assert card["route_probe_rows"] == 8
    assert {
        "https://www.nyse.com/market-data/indices",
        "https://www.nyse.com/indices/rebalance-schedule",
        "https://www.ice.com/fixed-income-data-services/index-solutions/equity-indices/products",
        "https://indices.ice.com",
    } <= urls
    assert card["membership_event_rows"] == 0
    assert card["announcement_time_rows"] == 0
    assert card["implementation_time_rows"] == 0
    gate.verify_public_probe_receipts()


def test_timestamp_and_content_parsers_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="naive"):
        gate.parse_accepted("2026-07-31T23:59:59")
    with pytest.raises(ValueError, match="invalid"):
        gate.parse_accepted("not-a-time")
    body = tmp_path / "bad_0000000001-26-000001.htm"
    body.write_text('<html><ix:nonNumeric name="dei:DocumentType">10-Q</ix:nonNumeric><ix:nonNumeric name="dei:EntityCentralIndexKey">0000000001</ix:nonNumeric>' + "x" * 300 + "</html>", encoding="utf-8")
    row = {"form": "10-K", "issuer_cik": "0000000001", "accn": "0000000001-26-000001", "url": "https://www.sec.gov/Archives/0000000001/000000000126000001/a.htm", "output": body.name}
    with pytest.raises(ValueError, match="type_mismatch"):
        gate.parse_sec_document_content(body, row)
    error_body = tmp_path / "error_0000000001-26-000002.htm"
    error_body.write_text("<html><title>File Unavailable</title>" + "x" * 400 + "</html>", encoding="utf-8")
    error_row = {**row, "form": "10-K", "accn": "0000000001-26-000002", "output": error_body.name}
    with pytest.raises(ValueError, match="body_invalid"):
        gate.parse_sec_document_content(error_body, error_row)


def test_global_accession_inventory_deduplicates_and_flags_conflicts() -> None:
    from scripts.research import acquire_equity_secondary_candidate_batches_2026_09_08 as acquisition

    inventory = acquisition.inventory()
    assert inventory["record_count"] == inventory["global_accession_count"]
    assert inventory["deduplicated_by_accession"] is True
    assert any(row["accn"] == "0001179110-12-017658" for row in inventory["conflicting_accessions"])


def test_h08_metadata_body_exhibit_counters_are_separate() -> None:
    analysis = json.loads((gate.PUBLIC_ROOT / "sec_batch_analysis.json").read_text(encoding="utf-8"))
    assert analysis["metadata_rows"] >= analysis["body_confirmed_rows"]
    assert "item_2_02_metadata" in analysis["event_metadata"]
    assert "item_2_02_body" in analysis["event_metadata"]
    assert analysis["event_metadata"]["item_2_02_metadata"] >= analysis["event_metadata"]["item_2_02_body"]


def test_manifest_receipt_body_mutation_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = json.loads(gate.SEC_DOC_RECEIPT.read_text(encoding="utf-8"))
    row = source["documents"][0]
    body_src = gate.REPO / row["output"]
    body_dst = tmp_path / row["output"]
    body_dst.parent.mkdir(parents=True)
    shutil.copy2(body_src, body_dst)
    body_dst.write_bytes(body_dst.read_bytes() + b"mutation")
    row["output"] = str(body_dst.relative_to(tmp_path))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setattr(gate, "REPO", tmp_path)
    monkeypatch.setattr(gate, "SEC_DOC_RECEIPT", receipt)
    monkeypatch.setattr(gate, "PIT_UNIVERSE", tmp_path / "missing.jsonl")
    with pytest.raises(ValueError, match="receipt_mismatch"):
        gate.verify_sec_documents()


def test_nport_http_error_is_checkpointed_without_skipping_or_overwriting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.research import acquire_equity_secondary_candidate_batches_2026_09_08 as acquisition

    out = tmp_path / "out"
    out.mkdir()
    payload = {"filings": {"recent": {"accessionNumber": ["0000000001-26-000001", "0000000001-26-000002"], "filingDate": ["2026-07-01", "2026-07-02"], "acceptanceDateTime": ["2026-07-01T12:00:00.000Z", "2026-07-02T12:00:00.000Z"], "form": ["NPORT-P", "NPORT-P/A"], "reportDate": ["2026-06-30", "2026-06-30"], "primaryDocument": ["one.xml", "two.xml"], "items": ["", ""]}}}
    (out / "sec_submissions_MCHI.body").write_text(json.dumps(payload), encoding="utf-8")
    receipt = {"rows": [{"accn": "0000000001-25-000001", "filing_cik": "0000000001", "status_code": 200, "body_path": "old.xml", "content_validation_error": ""}]}
    (out / "nport_batch_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    class Response:
        def __init__(self, status: int, body: bytes = b"") -> None:
            self.status_code = status
            self.content = body

        def json(self) -> dict[str, object]:
            return {"directory": {"item": [{"name": "primary.txt"}]}}

        def iter_content(self, chunk_size: int = 1):  # type: ignore[no-untyped-def]
            yield self.content

    class Session:
        def get(self, url: str, **kwargs: object) -> Response:
            if url.endswith("index.json"):
                return Response(404)
            if "000000000126000002" in url:
                return Response(200, b"<invstOrSec><issuerName>Example</issuerName></invstOrSec>")
            return Response(404)

    monkeypatch.setattr(acquisition, "REPO", tmp_path)
    monkeypatch.setattr(acquisition, "OUT", out)
    monkeypatch.setattr(acquisition.requests, "Session", Session)
    monkeypatch.setenv("SEC_USER_AGENT", "test@example.invalid")

    result = acquisition.download_nport_batch(1)
    assert result["attempted"] == 2
    first = json.loads((out / "nport_batch_receipt.json").read_text(encoding="utf-8"))
    assert len(first["rows"]) == 3
    assert {row["accn"] for row in first["rows"]} == {"0000000001-25-000001", "0000000001-26-000001", "0000000001-26-000002"}
    assert first["rows"][0]["accn"] == "0000000001-25-000001"
    assert first["rows"][-1]["accn"] == "0000000001-26-000002"

    acquisition.download_nport_batch(1)
    second = json.loads((out / "nport_batch_receipt.json").read_text(encoding="utf-8"))
    assert len(second["rows"]) == 4
    assert second["rows"][0]["accn"] == "0000000001-25-000001"
    assert sum(row["accn"] == "0000000001-26-000002" for row in second["rows"]) == 1
