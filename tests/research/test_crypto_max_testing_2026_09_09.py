from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.research import acquire_crypto_max_public_routes_2026_09_09 as routes
from scripts.research import crypto_max_testing_2026_09_09 as package


def test_protocol_freezes_exact_cards_and_no_economic_outcome() -> None:
    result = package.verify()
    assert result["status"] == "CRYPTO_MAX_TESTING_PROTOCOL_VERIFIED"
    payload = package.read_json(package.MANIFEST)
    assert [row["candidate_id"] for row in payload["protocols"]] == [f"K{i:02d}" for i in range(1, 13)]
    assert len(payload["current_gate_snapshot"]) == 12
    assert payload["economic_run"] is False
    assert payload["integration_candidate_count"] == 0
    assert payload["promotion"] is False
    assert all(row["protocol_status"] == "FROZEN_BEFORE_OUTCOME" for row in payload["protocols"])


def test_protocol_has_explicit_pit_and_no_ohlcv_substitution() -> None:
    payload = package.read_json(package.MANIFEST)
    for row in payload["protocols"]:
        assert "available_at" in row["availability_boundary"]
        assert "retrieval time never substitutes" in row["availability_boundary"]
        assert "OHLCV substitution" in row["missing_data"]
        assert row["cost_components"] == ["fees", "slippage", "funding_if_applicable", "borrow_if_applicable"]
        assert row["cost_stress"] == ["1x", "2x", "3x"]
        assert row["portfolio_scope"].startswith("complementary diagnostic only")
    assert "CapMVRVCur" in next(row for row in payload["protocols"] if row["candidate_id"] == "K01")["native_fields"]
    assert "not net dilution" in next(row for row in payload["protocols"] if row["candidate_id"] == "K04")["formula"]
    assert "not order-book" not in next(row for row in payload["protocols"] if row["candidate_id"] == "K11")["native_fields"]


def test_manifest_mutation_is_rejected(tmp_path: Path) -> None:
    payload = package.read_json(package.MANIFEST)
    payload = copy.deepcopy(payload)
    payload["protocols"][0]["direction"] = "short"
    path = tmp_path / "manifest.json"
    path.write_bytes(package.canonical(payload))
    with pytest.raises(ValueError, match="manifest_canonical_hash_mismatch"):
        package.verify(path)


def test_result_flags_and_source_hashes_are_fail_closed() -> None:
    payload = package.read_json(package.MANIFEST)
    assert all(row["result_source"]["path"] and row["result_source"]["sha256"] for row in payload["current_gate_snapshot"])
    assert all(row["economic_run"] is False and row["economic_eligible"] is False and row["admitted"] == 0 for row in payload["current_gate_snapshot"])


def test_direct_verify_is_read_only_and_network_free() -> None:
    files = (package.MANIFEST, package.ASSESSMENT, package.RECEIPT)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    script = str(package.REPO / "scripts/research/crypto_max_testing_2026_09_09.py")
    result = subprocess.run([sys.executable, script, "--verify"], cwd=Path("/tmp"), env=env, check=True, capture_output=True, text=True)
    assert "read_only" in result.stdout or "CRYPTO_MAX_TESTING_PROTOCOL_VERIFIED" in result.stdout
    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
    assert before == after


def test_kraken_nested_analytics_arrays_are_parsed_without_pit_inference() -> None:
    body = b'{"result":{"timestamp":[1704240000,1704326400],"data":{"basis":["0.0002","0.0000"]},"more":false},"errors":[]}'
    parsed = routes.parse_kraken(body, 200, None)
    assert parsed["schema"] == "result.timestamp+result.data_arrays"
    assert parsed["row_count"] == 2
    assert parsed["value_fields"] == ["basis"]
    assert parsed["available_at_field_present"] is False
    assert parsed["publication_time_field_present"] is False


def test_kraken_ohlc_like_open_interest_payload_is_parsed_as_buckets() -> None:
    body = b'{"result":{"timestamp":[1704240000,1704326400],"data":[["1","2","0","1"],["1","3","1","2"]],"more":false},"errors":[]}'
    parsed = routes.parse_kraken(body, 200, None)
    assert parsed["schema"] == "result.timestamp+result.data_arrays"
    assert parsed["row_count"] == 2
    assert parsed["value_fields"] == ["data"]
    assert parsed["units_explicit_in_response"] is False


def test_public_route_manifest_verifier_is_read_only() -> None:
    result = routes.verify()
    assert result["status"] == "CRYPTO_MAX_PUBLIC_ROUTE_MANIFEST_VERIFIED"
    assert result["checked_probes"] == 22
    assert result["network_free"] is True


def test_long_kraken_window_reports_actual_coverage_and_stays_pit_pending() -> None:
    manifest = json.loads(routes.MANIFEST.read_text(encoding="utf-8"))
    long_rows = [row for row in manifest["probes"] if "full_2022_to_cutoff" in row["slug"]]
    assert len(long_rows) == 6
    assert all(row["status"] == 200 and row["parsed"]["more"] is False for row in long_rows)
    assert all(row["parsed"]["available_at_field_present"] is False for row in long_rows)
    assert all(row["parsed"]["units_explicit_in_response"] is False for row in long_rows)
    assert sum(int(row["parsed"]["row_count"] or 0) for row in long_rows) == 6172


def test_prior_exhaustion_evidence_is_hash_bound() -> None:
    payload = routes.verify()
    assert payload["manifest_sha256"]
    manifest = json.loads(routes.MANIFEST.read_text(encoding="utf-8"))
    prior = manifest["prior_route_evidence"]["source_files"]
    assert len(prior) == 3
    assert {Path(item["path"]).name for item in prior} == {"crypto_queue_causal_adapters_2026_09_08.json", "crypto_secondary_causal_gate_2026_09_08.json", "manifest.json"}
