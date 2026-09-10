from __future__ import annotations

import copy
import csv
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.research import crypto_secondary_causal_gate_2026_09_08 as gate


def test_all_seven_contracts_are_frozen_before_results() -> None:
    assert set(gate.CONTRACTS) == {"K02", "K05", "K06", "K07", "K08", "K10", "K12"}
    for card, contract in gate.CONTRACTS.items():
        assert contract["availability_boundary"]
        assert contract["execution_shift"]
        assert contract["controls"]
        assert contract["cost_stress"] == [1.0, 2.0, 3.0]
        assert set(contract["cells"]) == {"full", "recent", "holdout"}
        assert contract["formula"]
        assert contract["minimum_breadth_history"]
    assert len(gate.K05_FROZEN_ASSETS) == 22
    assert gate.CONTRACTS["K05"]["configured_assets"] == gate.K05_FROZEN_ASSETS


def test_report_has_no_economic_output_and_preserves_blockers() -> None:
    report = gate.build_report()
    assert report["status"] == "DONE_WITH_CONCERNS"
    assert report["economic_run"] is False
    assert set(report["cards"]) == set(gate.CONTRACTS)
    assert all(card["economic_signal_rows"] == 0 for card in report["cards"].values())
    assert report["cards"]["K02"]["http_status"] == 403
    assert report["cards"]["K12"]["http_status"] == 403
    assert report["cards"]["K08"]["probe_http_status"] == 400


def test_k07_is_full_target_but_mutable_and_non_causal() -> None:
    result = gate.gate_k07()
    assert result["rows"] == 5538
    assert result["assets"] == {"btc": 2769, "eth": 2769}
    assert result["available_at_rows"] == 0
    assert result["rows_with_status_time_after_event"] > 0
    assert result["economic_signal_rows"] == 0


def test_k10_is_one_day_duplicate_pilot_and_blocks() -> None:
    result = gate.gate_k10()
    assert result["coverage_days"] == 1
    assert result["rows"] == 518
    assert result["rows_with_blank_available_at"] == 518
    assert result["duplicate_event_records"] == 259
    assert result["archive_probe_2026_07_30"]["BTCUSD_PERP"]["status"] == 404


def test_k06_does_not_treat_post_cutoff_snapshot_as_history() -> None:
    result = gate.gate_k06()
    report_text = gate.REPORT_MD.read_text(encoding="utf-8")
    assert result["btc_snapshot_rows"] == 13
    assert "BTC snapshot 13 rows" in report_text
    assert result["btc_rows_through_cutoff"] == 0
    assert result["available_at_rows"] == 0
    assert result["economic_signal_rows"] == 0
    inventory = json.loads(gate.PROBE_INDEX.read_text(encoding="utf-8"))
    statuses = {item["slug"]: item["status"] for item in inventory["probes"]}
    assert result["eth_page_status"] == statuses["k06_farside_eth_page"]
    assert result["all_data_probe_status"] == {"bitcoin": statuses["k06_farside_wp_page997"], "ethereum": statuses["k06_farside_wp_eth_slug"]}


def test_k06_html_and_normalized_snapshot_have_same_thirteen_rows() -> None:
    html = gate.K06_BTC.read_text(encoding="utf-8", errors="replace")
    html_dates = set(re.findall(r"\b\d{1,2} [A-Z][a-z]{2} 2026\b", html))
    snapshot_dates = {row["date"] for row in csv.DictReader(gate.K06_BTC_SNAPSHOT.open(newline="", encoding="utf-8"))}
    assert len(html_dates) == 13
    assert html_dates == snapshot_dates


def test_manifest_verifies_and_detects_canonical_mutation(tmp_path: Path) -> None:
    verified = gate.verify_manifest()
    assert verified["status"] == "FROZEN_SECONDARY_MANIFEST_VERIFIED"
    payload = json.loads(gate.MANIFEST.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(payload)
    mutated["economic_run"] = True
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_canonical_hash_mismatch"):
        gate.verify_manifest(path)


def test_saved_report_equals_canonical_build() -> None:
    assert gate.read_json(gate.REPORT) == gate.build_report()


def test_verify_cli_is_read_only_and_network_free(monkeypatch: pytest.MonkeyPatch) -> None:
    tracked = (gate.MANIFEST, gate.REPORT, gate.VALIDATION_RECEIPT)
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in tracked]

    def fail_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access during frozen verification")

    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    direct = gate.verify()
    assert direct["read_only"] is True
    assert direct["network_free"] is True
    result = subprocess.run(
        [sys.executable, str(gate.REPO / "scripts/research/crypto_secondary_causal_gate_2026_09_08.py"), "--verify"],
        cwd=gate.REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["read_only"] is True
    assert payload["network_free"] is True
    after = [(path.read_bytes(), path.stat().st_mtime_ns) for path in tracked]
    assert after == before


def test_manifest_declares_all_receipt_backed_probe_bodies() -> None:
    payload = json.loads(gate.MANIFEST.read_text(encoding="utf-8"))
    assert len(payload["inputs"]) >= 34
    for name in (
        "data/research/external/crypto_secondary_acquisition_2026_09_08/probes/farside/bitcoin_all_data_403.body",
        "data/research/external/crypto_secondary_acquisition_2026_09_08/probes/farside/bitcoin_all_data_403.body.receipt.json",
        "data/research/external/crypto_secondary_acquisition_2026_09_08/probes/farside/ethereum_all_data_403.body",
        "data/research/external/crypto_secondary_acquisition_2026_09_08/probes/farside/ethereum_all_data_403.body.receipt.json",
    ):
        assert name in payload["inputs"]


def test_receipt_verifier_rejects_truncated_or_mutated_body_hash(tmp_path: Path) -> None:
    body = tmp_path / "probe.body"
    receipt = tmp_path / "probe.body.receipt.json"
    body.write_bytes(b"immutable body")
    receipt.write_text(json.dumps({"endpoint": "https://example.test/probe", "status": 403, "bytes": body.stat().st_size, "sha256": gate.sha256(body)[:-1]}), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_sha256_format"):
        gate.verify_receipt_pair(body, receipt)
    receipt.write_text(json.dumps({"endpoint": "https://example.test/probe", "status": 403, "bytes": body.stat().st_size, "sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_payload_hash_mismatch"):
        gate.verify_receipt_pair(body, receipt)
    receipt.write_text(json.dumps({
        "endpoint": "https://example.test/probe", "final_url": "https://example.test/probe",
        "provider": "example.test", "card": "TEST", "kind": "public_endpoint",
        "timestamp_precision": "second", "retrieved_at_utc": "2026-09-08T00:00:00Z",
        "content_type": "text/plain", "credential_fields_persisted": False,
        "error": None, "row_count": None, "count_semantics": "not_applicable",
        "status": "403", "bytes": body.stat().st_size, "sha256": gate.sha256(body),
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_status_invalid"):
        gate.verify_receipt_pair(body, receipt)
    receipt.write_text(json.dumps({"endpoint": "https://example.test/probe", "status": 403, "bytes": 99, "sha256": gate.sha256(body)}), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_bytes_mismatch"):
        gate.verify_receipt_pair(body, receipt)


def test_receipt_verifier_checks_successful_zip_member_metadata(tmp_path: Path) -> None:
    archive_path = tmp_path / "sample.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("events.csv", "id,value\n1,10\n2,20\n")
    with ZipFile(archive_path) as archive:
        info = archive.getinfo("events.csv")
    receipt = archive_path.with_name("sample.zip.receipt.json")
    receipt.write_text(
        json.dumps(
            {
                "endpoint": "https://example.test/sample.zip",
                "final_url": "https://example.test/sample.zip",
                "provider": "example.test",
                "card": "TEST",
                "kind": "public_endpoint",
                "timestamp_precision": "second",
                "retrieved_at_utc": "2026-09-08T00:00:00Z",
                "content_type": "application/zip",
                "credential_fields_persisted": False,
                "attempts": 1,
                "retry_policy": "single_attempt",
                "error": None,
                "status": 200,
                "bytes": archive_path.stat().st_size,
                "sha256": gate.sha256(archive_path),
                "archive_sha256": gate.sha256(archive_path),
                "row_count": 2,
                "count_semantics": "zip_member_data_rows",
                "recipe": {"recipe_id": gate._zip_recipe_id("TEST", "example.test", "https://example.test/sample.zip", gate.sha256(archive_path), [{"name": "events.csv", "crc": info.CRC, "size": info.file_size, "header": ["id", "value"], "row_count": 2, "first_id": "1", "last_id": "2"}])[0], "date": None, "symbol": None, "request_url": "https://example.test/sample.zip", "final_url": "https://example.test/sample.zip", "archive_sha256": gate.sha256(archive_path), "receipt_binding": {"kind": "self_contained_raw_http", "endpoint": "https://example.test/sample.zip", "final_url": "https://example.test/sample.zip", "status": 200, "retrieved_at_utc": "2026-09-08T00:00:00Z", "content_type": "application/zip", "body_sha256": gate.sha256(archive_path)}},
                "members": [
                    {
                        "name": "events.csv",
                        "crc": info.CRC,
                        "size": info.file_size,
                        "header": ["id", "value"],
                        "row_count": 2,
                        "first_id": "1",
                        "last_id": "2",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert gate.verify_receipt_pair(archive_path, receipt) == 1
    mutated = json.loads(receipt.read_text(encoding="utf-8"))
    mutated["members"][0]["crc"] += 1
    receipt.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_zip_(?:member_identity|recipe_canonical)_mismatch"):
        gate.verify_receipt_pair(archive_path, receipt)


def test_inventory_binding_rejects_status_mutation_and_numeric_non_tabular_count(tmp_path: Path) -> None:
    inventory = json.loads(gate.PROBE_INDEX.read_text(encoding="utf-8"))
    record = next(item for item in inventory["probes"] if item["slug"] == "k02_catalog_asset_metrics_all")
    body = gate.REPO / record["body"]
    receipt = tmp_path / "mutated.body.receipt.json"
    original = json.loads((gate.REPO / record["receipt"]).read_text(encoding="utf-8"))
    original["status"] = 403
    receipt.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_inventory_field_mismatch:status"):
        gate.verify_receipt_pair(body, receipt, expected=record)
    original["status"] = record["status"]
    original["endpoint"] = "https://example.test/wrong-endpoint"
    receipt.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_inventory_field_mismatch:endpoint"):
        gate.verify_receipt_pair(body, receipt, expected=record)
    for field, value, error in (
        ("provider", "other.example", "receipt_inventory_field_mismatch:provider"),
        ("final_url", "https://example.test/wrong-final", "receipt_inventory_field_mismatch:final_url"),
        ("timestamp_precision", "day", "receipt_inventory_field_mismatch:timestamp_precision"),
    ):
        mutated = json.loads((gate.REPO / record["receipt"]).read_text(encoding="utf-8"))
        mutated[field] = value
        receipt.write_text(json.dumps(mutated), encoding="utf-8")
        with pytest.raises(ValueError, match=error):
            gate.verify_receipt_pair(body, receipt, expected=record)
    mutated = json.loads((gate.REPO / record["receipt"]).read_text(encoding="utf-8"))
    mutated.pop("endpoint")
    receipt.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_endpoint_https_required"):
        gate.verify_receipt_pair(body, receipt, expected=record)
    mutated = json.loads((gate.REPO / record["receipt"]).read_text(encoding="utf-8"))
    mutated["final_url"] = "http://example.test/insecure"
    receipt.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_final_url_https_required"):
        gate.verify_receipt_pair(body, receipt, expected=record)
    body2 = tmp_path / "page.body"
    receipt2 = tmp_path / "page.body.receipt.json"
    body2.write_text("plain page", encoding="utf-8")
    receipt2.write_text(json.dumps({"sha256": gate.sha256(body2), "bytes": body2.stat().st_size, "endpoint": "https://example.test/page", "final_url": "https://example.test/page", "provider": "example.test", "card": "TEST", "kind": "public_endpoint", "timestamp_precision": "second", "retrieved_at_utc": "2026-09-08T00:00:00Z", "content_type": "text/plain", "credential_fields_persisted": False, "attempts": 1, "retry_policy": "single_attempt", "error": None, "status": 200, "row_count": 1, "count_semantics": "not_applicable"}), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_count_not_applicable_numeric"):
        gate.verify_receipt_pair(body2, receipt2)


def test_expanded_public_inventory_keeps_access_separate_from_pit() -> None:
    inventory = json.loads(gate.PROBE_INDEX.read_text(encoding="utf-8"))
    by_slug = {item["slug"]: item for item in inventory["probes"]}
    assert by_slug["k02_catalog_assets_btc_eth"]["status"] == 200
    assert by_slug["k02_catalog_asset_metrics_all"]["status"] in {200, 400, 403}
    assert by_slug["k02_timeseries_txtfrvaladjntv"]["status"] == 403
    records = inventory["asset_specific_source_records"]
    assert {record["asset"] for record in records} == set(gate.K05_FROZEN_ASSETS)
    assert len({record["confirmation_probe"] for record in records}) < len(records)
    for asset in gate.K05_FROZEN_ASSETS:
        slug = asset.lower().replace("/", "_")
        assert f"k05_{slug}_official_archive" in by_slug
    assert all(record["contract_status"] == "UNRESOLVED" for record in records)
    assert by_slug["k06_farside_wp_page997"]["status"] == 200
    assert by_slug["k10_binance_cm_btc_2024_01_03"]["status"] == 200
    assert "K07 remains PIT blocked" in inventory["pit_conclusion"]
