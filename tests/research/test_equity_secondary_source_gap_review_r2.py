import json

import pytest

from data.research.external.equity_secondary_source_gap_audit_2026_09_09.review_r2 import build_review as review


def test_review_r2_is_frozen_and_verifies():
    result = review.verify()
    assert result == {"status": "REVIEW_R2_VERIFIED", "routes": 11, "artifacts": 41, "successful": 3}


def test_manifest_declared_pins_and_self_hash_fail_closed():
    manifest_path = review.OUT / "review_manifest.json"
    original = manifest_path.read_text()
    try:
        value = json.loads(original)
        value["inventory_sha256"] = "0" * 64
        value["manifest_sha256"] = review.canonical_json_sha({k: v for k, v in value.items() if k != "manifest_sha256"})
        manifest_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        with pytest.raises(ValueError, match="manifest_inventory_pin_mismatch"):
            review.verify()
    finally:
        manifest_path.write_text(original)


def test_manifest_self_hash_mutation_fails_closed():
    manifest_path = review.OUT / "review_manifest.json"
    original = manifest_path.read_text()
    try:
        value = json.loads(original)
        value["report_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        with pytest.raises(ValueError, match="manifest_self_hash_mismatch"):
            review.verify()
    finally:
        manifest_path.write_text(original)


def test_manifest_report_pin_mutation_fails_closed():
    manifest_path = review.OUT / "review_manifest.json"
    original = manifest_path.read_text()
    try:
        value = json.loads(original)
        value["report_sha256"] = "0" * 64
        value["manifest_sha256"] = review.canonical_json_sha({k: v for k, v in value.items() if k != "manifest_sha256"})
        manifest_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        with pytest.raises(ValueError, match="manifest_report_pin_mismatch"):
            review.verify()
    finally:
        manifest_path.write_text(original)


def test_coherent_body_receipt_inventory_mutation_leaves_stale_route_binding():
    manifest_path = review.OUT / "review_manifest.json"
    inventory_path = review.OUT / "review_inventory.json"
    original_manifest = manifest_path.read_text()
    original_inventory = inventory_path.read_text()
    manifest = json.loads(original_manifest)
    inventory = json.loads(original_inventory)
    route = inventory["routes"][0]
    body_path = review.REPO / route["body_path"]
    receipt_path = review.REPO / route["receipt_path"]
    original_body = body_path.read_bytes()
    original_receipt = receipt_path.read_text()
    try:
        body_path.write_bytes(original_body + b"\ncoherent mutation")
        receipt = json.loads(original_receipt)
        receipt["body_sha256"] = review.sha(body_path)
        receipt["bytes"] = body_path.stat().st_size
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        route["receipt_sha256"] = review.sha(receipt_path)
        inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
        inventory_sha = review.sha(inventory_path)
        for item in manifest["artifacts"]:
            path = review.REPO / item["path"]
            if path in {body_path, receipt_path, inventory_path}:
                item["sha256"] = review.sha(path)
                item["bytes"] = path.stat().st_size
        manifest["inventory_sha256"] = inventory_sha
        manifest["manifest_sha256"] = review.canonical_json_sha({k: v for k, v in manifest.items() if k != "manifest_sha256"})
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        with pytest.raises(ValueError, match="inventory_body_binding"):
            review.verify()
    finally:
        body_path.write_bytes(original_body)
        receipt_path.write_text(original_receipt)
        inventory_path.write_text(original_inventory)
        manifest_path.write_text(original_manifest)


def test_all_receipts_bind_https_body_and_status():
    inventory = json.loads((review.OUT / "review_inventory.json").read_text())
    for route in inventory["routes"]:
        receipt = json.loads((review.REPO / route["receipt_path"]).read_text())
        body = review.REPO / route["body_path"]
        assert receipt["endpoint"].startswith("https://")
        assert receipt["endpoint"] == receipt["final_url"]
        assert receipt["body_sha256"] == review.sha(body)
        assert receipt["bytes"] == body.stat().st_size
        assert receipt["status"] == ("HTTP_SUCCESS" if receipt["http_status"] == 200 else "HTTP_ERROR")
        assert receipt["credential_fields_persisted"] is False


def test_companyfacts_and_current_pages_cannot_become_causal_rows():
    inventory = json.loads((review.OUT / "review_inventory.json").read_text())
    semantics = {r["source_id"]: r["parsed_semantics"] for r in inventory["routes"]}
    assert semantics["sec_companyfacts"]["pit_text_fields"] is False
    assert semantics["sec_companyfacts"]["satisfies_contract"] is False
    assert semantics["nyse_historical"]["historical_rows"] == 0
    assert semantics["nyse_historical"]["satisfies_contract"] is False


def test_mutated_receipt_status_fails_closed(tmp_path, monkeypatch):
    source = review.OUT / "receipts" / "sec_companyfacts.json"
    original = source.read_text()
    try:
        value = json.loads(original)
        value["status"] = "HTTP_SUCCESS" if value["status"] == "HTTP_ERROR" else "HTTP_ERROR"
        source.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        with pytest.raises(ValueError, match="(artifact_mismatch|status_binding)"):
            review.verify()
    finally:
        source.write_text(original)


def test_report_rejects_global_no_untried_claim():
    report = json.loads((review.OUT / "review_report.json").read_text())
    assert report["conclusion"] == "NEEDS_FIX"
    assert report["global_conclusion_valid"] is False
    assert report["no_economics"] is True


def test_offline_parsers_emit_only_provenance_bound_actionable_work():
    parsed = review.OUT / "parsed"
    h09 = json.loads((parsed / "h09_relationship_rows.json").read_text())
    assert h09["candidate_rows"] == 2
    assert h09["admitted_rows"] == 0
    for row in h09["rows"]:
        assert row["accession"] and row["cik"] and row["accepted_at"] and len(row["source_sha256"]) == 64
        assert row["receipt_path"] and len(row["receipt_sha256"]) == 64
        assert row["body_path"] and row["body_sha256"] == row["source_sha256"]
        assert row["direction"] == "issuer_to_customer" and row["vintage"] and row["termination"] is None
    h10 = json.loads((parsed / "h10_version_pairs.json").read_text())
    assert h10["valid_10k_bodies"] == 89
    assert h10["exact_pairs"] == 3
    assert h10["admitted_pairs"] == 3
    for pair in h10["pairs"]:
        assert pair["original"]["accession"] != pair["amended"]["accession"]
        assert pair["original"]["source_sha256"] and pair["amended"]["source_sha256"]
        assert pair["same_cik_and_report_period"] is True
        assert pair["filed_order_ok"] is True and pair["accepted_order_ok"] is True
        assert pair["admitted"] is True
    h15 = json.loads((parsed / "h15_lifecycle_rows.json").read_text())
    assert h15["source_rows"] == 1 and h15["admitted_rows"] == 1
    assert h15["economic_admissible_rows"] == 0
    assert h15["rows"][0]["target_name"] and h15["rows"][0]["acquirer_name"]
    assert h15["rows"][0]["receipt_path"] and len(h15["rows"][0]["receipt_sha256"]) == 64
    assert h15["rows"][0]["body_path"] and len(h15["rows"][0]["body_sha256"]) == 64
    assert json.loads((parsed / "h16_lifecycle_rows.json").read_text())["admitted_rows"] == 0


def test_parser_receipt_binds_inputs_and_outputs():
    receipt = json.loads((review.OUT / "parsed/parser_receipt.json").read_text())
    for path, digest in receipt["input_sha256"].items():
        assert len(digest) == 64
    for item in receipt["outputs"].values():
        path = review.REPO / item["path"]
        assert path.is_file() and review.sha(path) == item["sha256"] and path.stat().st_size == item["bytes"]
