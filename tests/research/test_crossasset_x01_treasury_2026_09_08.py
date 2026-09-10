"""Offline schema and cutoff checks for the official Treasury acquisition."""

from __future__ import annotations

import json

import pytest

from scripts.research.acquire_crossasset_x01_treasury_2026_09_08 import (
    ALLOWED_HEADERS_BY_YEAR,
    OUT,
    REQUIRED_HEADERS,
    YEARS,
    _bundle_admission,
    _validate_offline_file,
    _validate_treasury_text,
)

HEADERS = ["Date", "1 Mo", "2 Mo", "3 Mo", "4 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr"]


def _payload_rows(year: int, *rows: tuple[str, dict[str, str]], value: str = "1.00") -> str:
    headers = list(ALLOWED_HEADERS_BY_YEAR[year])
    return ",".join(headers) + "\n" + "\n".join(
        ",".join([row_date, *[overrides.get(field, value) for field in headers[1:]]])
        for row_date, overrides in rows
    ) + "\n"


def _payload(*dates: str, value: str = "1.00") -> str:
    return _payload_rows(2024, *((row_date, {}) for row_date in dates), value=value)


def test_treasury_schema_accepts_requested_year() -> None:
    rows, coverage = _validate_treasury_text(_payload("01/02/2024"), 2024)
    assert len(rows) == 1
    assert coverage["all_dates_in_requested_year"] is True
    assert REQUIRED_HEADERS.issubset(HEADERS)


def test_treasury_optional_onset_allows_leading_blanks_only() -> None:
    text = _payload_rows(2018, ("10/15/2018", {"2 Mo": ""}), ("10/16/2018", {}))
    _validate_treasury_text(text, 2018)
    late_blank = _payload_rows(2024, ("01/02/2024", {"4 Mo": ""}))
    with pytest.raises(ValueError, match="4 Mo is blank after onset"):
        _validate_treasury_text(late_blank, 2024)


@pytest.mark.parametrize(
    "text,year,match",
    [
        ("", 2024, "empty"),
        ("Date,1 Mo\n01/02/2024,1\n", 2024, "header/schema"),
        (_payload("01/02/2023"), 2024, "outside requested year"),
        (_payload("01/02/2024", "01/02/2023"), 2024, "outside requested year"),
        (_payload("01/02/2024", value="nan"), 2024, "nonfinite"),
        (_payload("01/03/2024", "01/02/2024"), 2024, "not strictly increasing"),
        (_payload("01/02/2024", "01/02/2024"), 2024, "duplicated"),
        (_payload("01/02/2024").replace("30 Yr", "30 Yr,EXTRA", 1), 2024, "header/schema"),
    ],
)
def test_treasury_schema_rejects_bad_payloads(text: str, year: int, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _validate_treasury_text(text, year)


def test_authoritative_receipt_enumerates_full_requested_range() -> None:
    receipt = json.loads((OUT / "official_treasury_year_fetch_receipt.json").read_text(encoding="utf-8"))
    assert receipt["requested_years"] == list(YEARS)
    assert receipt["requested_start"] == "2016-01-01"
    assert receipt["requested_end"] == "2026-12-31"
    assert receipt["network_downloaded_years"] == list(YEARS)
    assert receipt["reused_years"] == []
    assert receipt["missing_years"] == []
    assert receipt["bundle_admitted"] is True
    assert receipt["status"] == "COMPLETE"


def test_treasury_bundle_admission_fails_closed_for_one_bad_year() -> None:
    records = [{"year": year, "status": "DOWNLOADED", "path": f"{year}.csv", "sha256": "hash", "coverage": {"dates_strictly_increasing": True}} for year in YEARS]
    records[4]["status"] = "PROBE_FAILED"
    admitted, status = _bundle_admission(records)
    assert admitted is False
    assert status == "FAILED"


def test_offline_validation_rejects_reversed_source_before_normalization(tmp_path) -> None:
    path = tmp_path / "treasury_2024.csv"
    path.write_text(_payload("01/03/2024", "01/02/2024"), encoding="utf-8")
    with pytest.raises(ValueError, match="not strictly increasing"):
        _validate_offline_file(path, 2024)
