from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.research import crypto_source_gap_audit_kraken_2026_09_09 as audit


def test_frozen_kraken_manifest_and_scope() -> None:
    result = audit.verify_manifest()
    assert result["status"] == "KRAKEN_R2_MANIFEST_VERIFIED"
    assert result["checked_probes"] == 20
    manifest = audit.read_json(audit.MANIFEST)
    assert manifest["economic_run"] is False
    assert manifest["next_stage_approval"] == "NOT_APPROVED"
    assert manifest["counts"] == {
        "admitted_rows": 0,
        "analytics_probes": 16,
        "liquidation_rows_observed": 0,
        "successful_http_200": 20,
        "total_probes": 20,
        "trade_history_probes": 4,
    }


def test_route_results_are_parsed_and_fail_closed() -> None:
    manifest = audit.read_json(audit.MANIFEST)
    analytics = [p for p in manifest["probes"] if p["kind"] == "public_market_analytics"]
    history = [p for p in manifest["probes"] if p["kind"] == "public_trade_history"]
    assert len(analytics) == 16 and all(p["status"] == 200 for p in analytics)
    assert all(p["parsed"]["schema"] == "result.timestamp+result.data_arrays" for p in analytics)
    assert all(not p["parsed"]["available_at_field_present"] for p in analytics)
    assert all(p["admission"].startswith("BLOCKED_FOR_K10") for p in analytics)
    assert len(history) == 4 and all(p["status"] == 200 for p in history)
    success = [p for p in history if p["status"] == 200]
    assert all(p["parsed"]["schema"] == "history_trade_records" for p in success)
    assert all(p["parsed"]["liquidation_rows"] == 0 for p in success)
    assert all(p["parsed"]["row_count"] == 0 for p in history if "cutoff" in p["slug"])
    assert all(p["admission"].startswith("BLOCKED_FOR_K10") for p in history)


def test_manifest_mutation_is_rejected(tmp_path: Path) -> None:
    payload = audit.read_json(audit.MANIFEST)
    mutated = copy.deepcopy(payload)
    mutated["next_stage_approval"] = "APPROVED"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_canonical_hash_mismatch"):
        audit.verify_manifest(path)


def test_receipt_body_mutation_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = audit.read_json(audit.MANIFEST)["probes"][0]
    record = copy.deepcopy(original)
    record["body"] = "probe.body"
    record["headers"] = "probe.headers"
    record["receipt"] = "probe.receipt.json"
    monkeypatch.setattr(audit, "REPO", tmp_path)
    body = tmp_path / record["body"]
    headers = tmp_path / record["headers"]
    receipt = tmp_path / record["receipt"]
    original_root = Path(__file__).resolve().parents[2]
    body.write_bytes((original_root / original["body"]).read_bytes())
    headers.write_bytes((original_root / original["headers"]).read_bytes())
    receipt.write_text((original_root / original["receipt"]).read_text(encoding="utf-8"), encoding="utf-8")
    body.write_bytes(body.read_bytes() + b"\nmutation")
    with pytest.raises(ValueError, match="body_receipt_mismatch"):
        audit._verify_receipt(record)


def test_receipt_status_mutation_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_root = Path(__file__).resolve().parents[2]
    original = audit.read_json(original_root / "data/research/external/crypto_source_gap_audit_2026_09_09/kraken_r2/manifest.json")["probes"][0]
    record = copy.deepcopy(original)
    record.update({"body": "probe.body", "headers": "probe.headers", "receipt": "probe.receipt.json"})
    monkeypatch.setattr(audit, "REPO", tmp_path)
    (tmp_path / record["body"]).write_bytes((original_root / original["body"]).read_bytes())
    (tmp_path / record["headers"]).write_bytes((original_root / original["headers"]).read_bytes())
    receipt_data = json.loads((original_root / original["receipt"]).read_text(encoding="utf-8"))
    receipt_data["http_status"] = 404
    (tmp_path / record["receipt"]).write_text(json.dumps(receipt_data), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_transport_binding_mismatch"):
        audit._verify_receipt(record)


def test_body_and_receipt_mutation_without_manifest_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_root = Path(__file__).resolve().parents[2]
    original = audit.read_json(original_root / "data/research/external/crypto_source_gap_audit_2026_09_09/kraken_r2/manifest.json")["probes"][0]
    record = copy.deepcopy(original)
    record.update({"body": "probe.body", "headers": "probe.headers", "receipt": "probe.receipt.json"})
    monkeypatch.setattr(audit, "REPO", tmp_path)
    body = tmp_path / record["body"]
    headers = tmp_path / record["headers"]
    body.write_bytes((original_root / original["body"]).read_bytes() + b"manifestless mutation")
    headers.write_bytes((original_root / original["headers"]).read_bytes())
    receipt_data = json.loads((original_root / original["receipt"]).read_text(encoding="utf-8"))
    receipt_data["body_sha256"] = audit.sha_file(body)
    receipt_data["body_bytes"] = body.stat().st_size
    (tmp_path / record["receipt"]).write_text(json.dumps(receipt_data), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_receipt_body_"):
        audit._verify_receipt(record)
