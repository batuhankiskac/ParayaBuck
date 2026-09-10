import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/research/acquire_equity_h01_h05_price_security_retry_2026_09_09.py"
SPEC = importlib.util.spec_from_file_location("h01_h05_retry", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_retry_receipt_verifies_captured_routes() -> None:
    result = MODULE.verify_receipt()
    assert result == {
        "records": 36,
        "successful_schema_valid_routes": 16,
        "admitted_rows": 0,
        "status": "RETRY_EVIDENCE_CAPTURED_NO_PIT_ADMISSION",
    }


def test_retry_covers_frozen_symbols_and_window() -> None:
    receipt = json.loads((MODULE.OUT / "retry_receipt.json").read_text(encoding="utf-8"))
    assert receipt["symbols"] == list(MODULE.SYMBOLS)
    assert receipt["requested_window"] == {
        "start": "2016-01-01",
        "end_exclusive": "2025-01-01",
        "period1": 1451606400,
        "period2": 1735689600,
    }
    assert all(record["admitted"] is False for record in receipt["records"])


def test_retry_successes_bind_identity_and_expected_counts() -> None:
    receipt = json.loads((MODULE.OUT / "retry_receipt.json").read_text(encoding="utf-8"))
    successes = [r for r in receipt["records"] if r["status"] == "HTTP_200_SCHEMA_VALID_NO_PIT_ADMISSION"]
    assert successes
    assert all(r["identity"]["symbol"] == r["symbol"] for r in successes)
    assert all(r["rows"] == receipt["expected_rows_from_pinned_v3"][r["symbol"]] for r in successes)


def test_retry_mutated_chart_identity_fails_closed() -> None:
    receipt = json.loads((MODULE.OUT / "retry_receipt.json").read_text(encoding="utf-8"))
    record = next(r for r in receipt["records"] if r["status"] == "HTTP_200_SCHEMA_VALID_NO_PIT_ADMISSION" and r["variant"].startswith("chart"))
    payload = json.loads((ROOT / record["body_path"]).read_bytes())
    payload["chart"]["result"][0]["meta"]["symbol"] = "WRONG"
    with pytest.raises(ValueError, match="identity"):
        MODULE._validate_chart(payload, record["symbol"], receipt["expected_rows_from_pinned_v3"][record["symbol"]])


def test_retry_download_auth_failures_remain_truthful() -> None:
    receipt = json.loads((MODULE.OUT / "retry_receipt.json").read_text(encoding="utf-8"))
    failures = [r for r in receipt["records"] if r["variant"].startswith("download")]
    assert len(failures) == 16
    assert {r["status"] for r in failures} == {"HTTP_401"}
    assert all(r["source_absence_claim"] is False for r in failures)
