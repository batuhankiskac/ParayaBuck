from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.research.crossasset_source_matrix_2026_09_08 as source_matrix
from scripts.research.crossasset_receipt_verifier_2026_09_08 import (
    verify_archive,
    verify_probe_receipt,
    verify_source_matrix,
    verify_x01_manifest,
)


def _receipt_fixture(tmp_path: Path) -> Path:
    body = tmp_path / "body.bin"
    body.write_bytes(b"fixture")
    import hashlib
    receipt = {
        "schema": "crossasset-sanitized-probe-receipt-v1",
        "source_id": "fixture",
        "card": "X02",
        "kind": "http_probe",
        "endpoint": "https://example.test/data",
        "retrieved_at": "2026-09-09T00:00:00+00:00",
        "status": "HTTP_SUCCESS",
        "http_status": 200,
        "error": None,
        "body_path": "body.bin",
        "body_sha256": hashlib.sha256(b"fixture").hexdigest(),
        "bytes": 7,
        "row_count": None,
        "count_semantics": "not_tabular_or_not_declared",
        "credential_fields_persisted": False,
    }
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


@pytest.mark.parametrize(("field", "value", "message"), [
    ("endpoint", "", "endpoint"),
    ("status", "ARBITRARY", "status"),
    ("http_status", "200", "http_status"),
])
def test_receipt_schema_mutations_fail_closed(tmp_path: Path, field: str, value: object, message: str) -> None:
    path = _receipt_fixture(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AssertionError, match=message):
        verify_probe_receipt(path, repo_root=tmp_path)


def test_source_matrix_fixture_build_is_reproducible_and_receipt_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status_code = 200
        url = "https://example.test/data"
        headers = {"content-type": "application/json"}
        content = b"[]"

        def json(self):
            return []

    monkeypatch.setattr(source_matrix, "_acquire_sec", lambda **_: ([], {"resolved_tickers": {}, "unresolved_tickers": []}))
    monkeypatch.setattr(source_matrix.requests, "get", lambda *args, **kwargs: Response())
    first = tmp_path / "first"
    second = tmp_path / "second"
    matrix1, _ = source_matrix.build_matrix(out_root=first)
    matrix2, _ = source_matrix.build_matrix(out_root=second)
    assert source_matrix._sha(first / "probes" / "receipts" / "cme_settlements.receipt.json")
    assert matrix1 == matrix2
    assert all(record.get("receipt_path") for record in matrix1["records"])


def test_real_crossasset_matrix_and_x01_manifest_are_canonical_and_pinned() -> None:
    result = verify_source_matrix(Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json"))
    assert result["records"] == 29
    assert verify_x01_manifest(Path("data/research_manifests/crossasset_x01_yahoo_fallback_2026_09_08.json"))["symbols"] == 3


def test_matrix_mutation_fails_closed(tmp_path: Path) -> None:
    source = Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json")
    value = json.loads(source.read_text(encoding="utf-8"))
    value["records"][0]["status"] = "MUTATED"
    target = tmp_path / source.name
    target.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(AssertionError, match="canonical hash"):
        verify_source_matrix(target)


def test_x01_manifest_mutation_fails_closed(tmp_path: Path) -> None:
    source = Path("data/research_manifests/crossasset_x01_yahoo_fallback_2026_09_08.json")
    value = json.loads(source.read_text(encoding="utf-8"))
    value["admission"]["admitted_rows"] = 1
    target = tmp_path / source.name
    target.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(AssertionError, match="canonical hash"):
        verify_x01_manifest(target)


def test_archive_crc_size_and_member_metadata_are_checked() -> None:
    result = verify_archive(Path("data/research/external/crossasset_public_2026_09_07/x05_x06_binance/BTCUSDT_240329-1h-2024-01-02.zip"))
    assert result["members"] == 1
    assert result["sizes"]
    assert result["crcs"]
