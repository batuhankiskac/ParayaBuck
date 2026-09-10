from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.research import crypto_queue_causal_adapters_2026_09_08 as adapters


def test_coinmetrics_k01_is_normalized_but_pit_closed() -> None:
    result = adapters.k01_mvrv()
    assert result["recipe_id"] == "K01_CM_CapMVRVCur_v1"
    assert result["assets"] == {"btc": 2769, "eth": 2769}
    assert result["pit_status"] == "PIT_PENDING"
    assert result["economic_signal_rows"] == 0


def test_k03_uses_fees_and_does_not_claim_sybil_adjusted_usage() -> None:
    result = adapters.k03_fee_growth()
    assert result["metric_semantics"].startswith("native transaction fees")
    assert result["feature_rows"] > 0
    assert result["pit_status"] == "PIT_PENDING"
    assert result["economic_signal_rows"] == 0


def test_k04_keeps_gross_issuance_distinct_from_net_dilution() -> None:
    result = adapters.k04_supply_audit()
    assert result["gross_issuance_is_net_dilution"] is False
    assert result["burn_metric_present"] is False
    assert result["net_supply_candidate_status"] == "BLOCKED_BURN_MISSING"
    assert result["gross_audit_rows"] > 0
    assert result["gross_audit_preview"][0]["gross_issuance_over_prior_supply"] > 0
    assert result["economic_signal_rows"] == 0


def test_k04_numeric_alignment_missing_prior_and_zero_guard() -> None:
    rows = [
        {"asset": "btc", "event_time": "2024-01-01T00:00:00Z", "metric": "SplyCur", "value": "100", "available_at": "2024-01-02T00:00:00Z"},
        {"asset": "btc", "event_time": "2024-01-02T00:00:00Z", "metric": "IssTotNtv", "value": "5", "available_at": "2024-01-03T00:00:00Z"},
        {"asset": "btc", "event_time": "2024-01-02T00:00:00Z", "metric": "SplyCur", "value": "105", "available_at": "2024-01-03T00:00:00Z"},
    ]
    audit = adapters.compute_gross_issuance_audit(rows)
    assert audit[0]["gross_issuance_over_prior_supply"] == pytest.approx(0.05)
    assert audit[0]["causally_available"] is True
    missing_prior = [row for row in rows if row["event_time"] != "2024-01-01T00:00:00Z"]
    assert adapters.compute_gross_issuance_audit(missing_prior) == []
    zero_prior = [dict(row) for row in rows]
    zero_prior[0]["value"] = "0"
    with pytest.raises(ValueError, match="invalid_prior_supply"):
        adapters.compute_gross_issuance_audit(zero_prior)


def test_k04_generator_materializes_once_and_matches_list() -> None:
    rows = [
        {"asset": "btc", "event_time": "2024-01-01T00:00:00Z", "metric": "SplyCur", "value": "100", "available_at": ""},
        {"asset": "btc", "event_time": "2024-01-02T00:00:00Z", "metric": "IssTotNtv", "value": "5", "available_at": ""},
    ]
    assert adapters.compute_gross_issuance_audit(rows) == adapters.compute_gross_issuance_audit(iter(rows))


def test_k03_rejects_nonpositive_or_malformed_fee_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_validate_source", lambda _row: None)
    base = {
        "asset": "btc", "event_time": "2024-01-01T00:00:00Z", "metric": "FeeTotNtv",
        "unit": "native", "source_file": "x", "source_sha256": "x", "available_at": "",
    }
    for value in ("0", "-1", "nan", "abc"):
        with pytest.raises(ValueError, match="invalid_(value|numeric_value)"):
            adapters._validate_coinmetrics([{**base, "value": value}], {"FeeTotNtv"}, strict_positive_metrics={"FeeTotNtv"})


def test_coinmetrics_duplicate_key_normalizes_equivalent_utc_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_validate_source", lambda _row: None)
    rows = [
        {"asset": "btc", "event_time": "2024-01-01T00:00:00Z", "metric": "FeeTotNtv", "value": "1", "unit": "native", "source_file": "x", "source_sha256": "x", "available_at": ""},
        {"asset": "btc", "event_time": "2024-01-01T00:00:00+00:00", "metric": "FeeTotNtv", "value": "1", "unit": "native", "source_file": "x", "source_sha256": "x", "available_at": ""},
    ]
    with pytest.raises(ValueError, match="duplicate_primary_key"):
        adapters._validate_coinmetrics(rows, {"FeeTotNtv"})


def test_binance_duplicate_key_normalizes_equivalent_utc_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_validate_source", lambda _row: None)
    rows = [
        {"venue": "binance", "instrument": "BTCUSDT", "event_time_utc": "2024-01-01T00:00:00Z", "source_file": "x", "source_sha256": "x", "available_at": "", "agg_trade_id": "1", "price": "1", "quantity": "1", "is_buyer_maker": "false"},
        {"venue": "binance", "instrument": "BTCUSDT", "event_time_utc": "2024-01-01T00:00:00+00:00", "source_file": "x", "source_sha256": "x", "available_at": "", "agg_trade_id": "2", "price": "1", "quantity": "1", "is_buyer_maker": "false"},
    ]
    with pytest.raises(ValueError, match="duplicate_primary_key"):
        adapters._validate_binance(rows, ("venue", "instrument", "event_time_utc"))


def _valid_bar_row() -> dict[str, str]:
    return {
        "venue": "binance", "instrument": "BTCUSDT", "event_time_utc": "2024-01-01T00:00:00Z",
        "bar_kind": "spot", "open": "100", "high": "101", "low": "99", "close": "100.5",
        "close_time_utc": "2024-01-01T00:59:59Z", "source_file": "x", "source_sha256": "x",
    }


@pytest.mark.parametrize("field,value", [("instrument", " "), ("open", "abc"), ("open", "nan"), ("high", "inf"), ("low", "-inf")])
def test_binance_bar_schema_rejects_empty_or_invalid_fields(field: str, value: str) -> None:
    row = _valid_bar_row()
    row[field] = value
    with pytest.raises(ValueError):
        adapters.validate_binance_row_schema(row, "bars")


def test_binance_bar_schema_direct_validation_does_not_consult_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_row: dict[str, str]) -> None:
        raise AssertionError("source verification must not run")

    monkeypatch.setattr(adapters, "_validate_source", fail)
    adapters.validate_binance_row_schema(_valid_bar_row(), "bars")


def test_k09_resolves_bar_identity_from_official_source_endpoint() -> None:
    result = adapters.k09_archive_join()
    assert result["assets"] == ["BTCUSDT", "ETHUSDT"]
    assert result["identity_status"] == "IDENTITY_OK_SOURCE_VERIFIED"
    assert result["bar_kinds"] == ["futures", "index", "mark"]
    assert result["raw_bar_kinds"] == ["index", "mark", "spot_or_futures"]
    assert result["raw_join_rows"] == 6
    assert result["causal_join_rows_by_cutoff"] == 0
    assert result["join_status"] == "COMMON_PILOT_ROWS_PRESENT_FUNDING_AFTER_CUTOFF"
    assert "publication/announcement time unknown" in result["funding_time_semantics"]
    assert result["pit_status"] == "PIT_PENDING"
    assert result["economic_signal_rows"] == 0


def test_k11_actual_archive_is_signed_trade_flow_only() -> None:
    result = adapters.k11_signed_flow()
    assert result["hourly_rows"] == 24
    assert result["pit_status"] == "ARCHIVE_EVIDENCE_ONLY"
    assert result["causal_trade_rows"] == 0
    assert result["depth_imbalance_inferred"] is False
    assert result["economic_signal_rows"] == 0
    assert result["expected_archive_set_status"] == "EXPECTED_ARCHIVE_SET_MATCH"
    assert result["expected_archive_set"]["id_contract"] == "unique_strict_contiguous_range_inclusive"
    assert result["pit_status"] == "ARCHIVE_EVIDENCE_ONLY"


def _write_k11_fixture(root: Path, *, duplicate: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source.zip"
    source.write_bytes(b"fixture archive")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    path = root / "trades.csv"
    fields = [
        "venue", "instrument", "event_time_utc", "agg_trade_id", "price", "quantity",
        "is_buyer_maker", "source_file", "source_sha256", "available_at",
    ]
    rows = [
        ["binance_usdm", "BTCUSDT", "2026-07-30T00:00:00.100000Z", "1", "100", "2", "false", "source.zip", digest, ""],
        ["binance_usdm", "BTCUSDT", "2026-07-30T00:00:00.200000Z", "2", "101", "1", "true", "source.zip", digest, ""],
    ]
    if duplicate:
        rows.append(rows[-1])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(rows)
    return path


def test_k11_sign_convention_and_duplicate_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "DATA_ROOT", tmp_path)
    path = _write_k11_fixture(tmp_path)
    result = adapters.k11_signed_flow(path)
    assert result["preview"][0]["buy_notional"] == 200.0
    assert result["preview"][0]["sell_notional"] == 101.0
    assert result["preview"][0]["signed_notional"] == 99.0
    duplicate_path = _write_k11_fixture(tmp_path / "duplicate", duplicate=True)
    with pytest.raises(ValueError, match="duplicate_primary_key"):
        adapters.k11_signed_flow(duplicate_path)


def test_k11_explicit_post_cutoff_availability_overrides_archive_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "DATA_ROOT", tmp_path)
    path = _write_k11_fixture(tmp_path)
    rows = adapters.read_rows(path)
    expectation = {
        "symbol_day": {"venue": "binance_usdm", "instrument": "BTCUSDT", "event_date_utc": "2026-07-30"},
        "window": {"start_utc": "2026-07-30T00:00:00Z", "end_utc": "2026-07-31T00:00:00Z"},
        "archive": {"kind": "synthetic", "expected_row_count": 2, "first_agg_trade_id": 1, "last_agg_trade_id": 2, "expected_id_set": [1, 2], "id_contract": "explicit_expected_id_set"},
    }
    rows[0]["available_at"] = "2026-08-01T00:00:00Z"
    monkeypatch.setattr(adapters, "read_rows", lambda _path: rows)
    from scripts.research import k11_signed_flow_candidate_2026_09_08 as candidate

    monkeypatch.setattr(candidate, "load_archive_expectation", lambda: expectation)
    result = adapters.k11_signed_flow(path)
    assert result["expected_archive_set_status"] == "EXPECTED_ARCHIVE_SET_MATCH"
    assert result["explicit_availability_rows_after_cutoff"] == 1
    assert result["causal_trade_rows"] == 0


def test_k11_blank_availability_stays_blocked_with_early_archive_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "DATA_ROOT", tmp_path)
    path = _write_k11_fixture(tmp_path)
    rows = adapters.read_rows(path)
    expectation = {
        "symbol_day": {"venue": "binance_usdm", "instrument": "BTCUSDT", "event_date_utc": "2026-07-30"},
        "window": {"start_utc": "2026-07-30T00:00:00Z", "end_utc": "2026-07-31T00:00:00Z"},
        "archive": {"kind": "synthetic", "expected_row_count": 2, "first_agg_trade_id": 1, "last_agg_trade_id": 2, "expected_id_set": [1, 2], "id_contract": "explicit_expected_id_set"},
    }
    monkeypatch.setattr(adapters, "read_rows", lambda _path: rows)
    monkeypatch.setattr(adapters, "archive_available_at", lambda _source: adapters.utc("2026-07-31T07:02:44Z"))
    from scripts.research import k11_signed_flow_candidate_2026_09_08 as candidate

    monkeypatch.setattr(candidate, "load_archive_expectation", lambda: expectation)
    result = adapters.k11_signed_flow(path)
    assert result["causal_trade_rows"] == 0
    assert result["pit_status"] == "ARCHIVE_EVIDENCE_ONLY"


def test_frozen_manifest_hash_is_reproducible_and_isolated(tmp_path: Path) -> None:
    current_files = (adapters.MANIFEST, adapters.REPORT)
    before = [(path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in current_files]
    target = tmp_path / "crypto_queue_causal_adapters_2026_09_08.json"
    manifest = adapters.freeze_manifest(target)
    selected = dict(manifest)
    selected["manifest_sha256"] = "PENDING_FREEZE"
    expected = hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert manifest["manifest_sha256"] == expected
    assert manifest["economic_run"] is False
    assert target.is_file()
    after = [(path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in current_files]
    assert after == before


def test_all_frozen_inputs_receipts_and_zips_verify() -> None:
    result = adapters.verify_frozen_manifest()
    assert result["status"] == "FROZEN_MANIFEST_VERIFIED"
    assert result["checked_declared_files"] == 45
    assert result["checked_zip_integrity"] > 0


def test_verify_cli_is_read_only() -> None:
    tracked = (adapters.MANIFEST, adapters.REPORT)
    before = [(path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in tracked]
    result = subprocess.run(
        [sys.executable, str(adapters.Path(__file__).parents[2] / "scripts/research/crypto_queue_causal_adapters_2026_09_08.py"), "--verify"],
        cwd=adapters.REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["read_only"] is True
    after = [(path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in tracked]
    assert after == before
