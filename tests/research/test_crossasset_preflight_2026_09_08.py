"""Focused checks for the research-only cross-asset preflight."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts.research.crossasset_preflight_2026_09_08 import (
    MANIFEST,
    X03_SOURCE_FIELDS,
    _hourly_rows,
    _iter_target_bars,
    _validate_kline_rows,
    _validate_ohlcv,
    _validate_raw_order,
    _validate_zip_ohlcv,
    _zip_rows,
    load_manifest,
    verify,
    x01_curve,
    x03_country,
    x04_pairs,
    x05_x06_derivatives,
)


def test_manifest_is_protocol_before_results() -> None:
    manifest = load_manifest()
    assert manifest["declared_before_results"] is True
    assert manifest["result_status"] == "NOT_RUN"
    assert manifest["no_economic_execution"] is True
    assert manifest["baseline_contract"]["equity_ceiling"] == 0.55
    assert MANIFEST.is_file()


def test_x01_cutoff_and_quarantine_preflight() -> None:
    result = x01_curve()
    assert result["main_rows"] == 2645
    assert result["main_first"] == "2016-01-04"
    assert result["main_last"] == "2026-07-31"
    assert result["main_sorted_unique"] is True
    assert result["main_in_cutoff"] is True
    assert result["quarantine_rows"] == 25
    assert result["quarantine_strictly_post_cutoff"] is True
    assert result["historical_etf_inputs"] == {"SHY": "MISSING", "IEF": "MISSING", "TLT": "MISSING"}
    assert result["fred_main_rows"] == 2761
    assert result["fred_main_nonempty_observations"] == 2645
    assert result["fred_quarantine_rows"] == 24
    assert result["fred_quarantine_nonempty_observations"] == 24
    assert result["fred_quarantine_first"] == "2026-08-03"
    assert result["fred_quarantine_last"] == "2026-09-03"
    assert result["fred_later_rows_excluded"] is True


def test_x04_short_volume_is_descriptive_only() -> None:
    result = x04_pairs()
    assert result["finra_rows"] == 2
    assert result["finra_symbols"] == ["QQQ", "SPY"]
    assert result["short_volume_is_not_borrow"] is True
    assert "BORROW" in result["economic_gate"]


def test_x03_structured_parse_is_explicitly_unbound_and_unadmitted() -> None:
    result = x03_country()
    assert result["nport_parsed_holding_rows"] == 565
    assert result["nport_admitted_holding_rows"] == 0
    assert result["nport_binding_status"] == "METADATA_ONLY_UNBOUND_LOCAL_XML"
    assert result["nport_accession"] is None
    assert result["nport_accepted_at"] is None
    assert result["nport_cik"] == "0001100663"
    assert result["nport_series_id"] == "S000031717"
    assert result["nport_class_id"] == "C000098688"
    assert result["public_sample_parsed_holding_rows"] == 239
    assert result["public_sample_admitted_holding_rows"] == 0
    assert result["public_sample_bindings"]["EWJ"]["series_id"] == "S000004249"
    assert result["public_sample_bindings"]["EWJ"]["class_id"] == "C000011953"
    assert result["public_sample_bindings"]["EWZ"]["series_id"] == "S000004264"
    assert result["public_sample_bindings"]["EWZ"]["class_id"] == "C000011968"


def test_binance_schema_has_aligned_streams_and_funding() -> None:
    result = x05_x06_derivatives()
    assert result["spot_rows"] == 745
    assert result["perpetual_rows"] == 745
    assert result["common_hourly_timestamps"] == 745
    assert result["hourly_interval_ms"] == [3600000]
    assert result["funding_rows"] == 94
    assert 28800000 in result["funding_interval_ms"]
    assert result["funding_has_mark_price"] is True
    assert result["dated_contract_expiry_rows"] == 1
    assert result["historical_oi"] == "REJECTED_BINANCE_-1130"


def test_zip_and_json_helpers_reject_or_read_bounded_fixtures(tmp_path: Path) -> None:
    json_path = tmp_path / "rows.json"
    json_path.write_text(json.dumps([[1, "2"], [2, "3"]]), encoding="utf-8")
    assert _hourly_rows(json_path) == [[1, "2"], [2, "3"]]
    archive_path = tmp_path / "one.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("rows.csv", "open_time,close\n1,2\n")
    assert _zip_rows(archive_path) == [{"open_time": "1", "close": "2"}]


def test_crossasset_bad_fixtures_fail_closed() -> None:
    equity = {"timestamp": "2024-01-02T00:00:00Z", "symbol": "MCHI", "timeframe": "1Day", "open": "11", "high": "10", "low": "12", "close": "11", "volume": "1", "market": "us_equity"}
    with pytest.raises(RuntimeError, match="OHLC"):
        _validate_ohlcv(equity, {"MCHI"})
    equity["EXTRA"] = "x"
    with pytest.raises(RuntimeError, match="row width"):
        _validate_ohlcv(equity, {"MCHI"})
    with pytest.raises(RuntimeError, match="strictly increasing"):
        _validate_raw_order(
            [
                {"symbol": "MCHI", "timestamp": "2024-01-02T00:00:00Z"},
                {"symbol": "MCHI", "timestamp": "2024-01-02T00:00:00Z"},
            ]
        )
    kline = [1, "12", "10", "11", "11", "1", 2, "1", 1, "1", "1", "0"]
    with pytest.raises(RuntimeError, match="OHLC"):
        _validate_kline_rows([kline], "bad perpetual")
    with pytest.raises(RuntimeError, match="header"):
        _validate_zip_ohlcv([{"open_time": "1"}], "bad dated stream")


def test_x03_rejects_reordered_source_header(tmp_path: Path) -> None:
    path = tmp_path / "equity.csv"
    fields = list(reversed(X03_SOURCE_FIELDS))
    path.write_text(",".join(fields) + "\n" + ",".join("1" for _ in fields) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="header/schema"):
        list(_iter_target_bars(path, {"MCHI"}))


def test_preflight_x03_route_pin_and_validation_receipt_are_current() -> None:
    output = Path("data/research/generated/crossasset_preflight_2026_09_08")
    result_path = output / "results.json"
    validation_path = output / "validation_receipt.json"
    route_path = Path("data/research/external/crossasset_public_2026_09_09/public_route_exhaustion/route_exhaustion_report.json")

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    result = json.loads(result_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    route_sha = digest(route_path)
    assert result["cards"]["X03"]["public_route_report_sha256"] == route_sha
    assert validation["result_sha256"] == digest(result_path)
    assert validation["generated_files"][str(result_path)] == digest(result_path)


def test_preflight_verify_cli_is_read_only() -> None:
    output = Path("data/research/generated/crossasset_preflight_2026_09_08")
    manifest_path = MANIFEST
    validation_path = output / "validation_receipt.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    paths = [manifest_path, output / "results.json", validation_path]
    paths.extend(Path(relative) for relative in validation["generated_files"])
    before = {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths}
    result = subprocess.run([sys.executable, "scripts/research/crossasset_preflight_2026_09_08.py", "--verify"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "CROSSASSET_PREFLIGHT_VERIFIED"
    assert verify()["generated_files"] == len(validation["generated_files"])
    after = {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths}
    assert after == before
