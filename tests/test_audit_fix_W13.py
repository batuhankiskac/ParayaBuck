"""Regression tests for the 2026-10-27 audit fixes in the TL tax ledger (worker W13).

Every test uses tmp_path SQLite and synthetic fills, rates and activities. EVDS
and the Alpaca GET are reached only through injected fakes or patched urllib.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from parayabuck.config import DEFAULT_DECISION_DB_PATH, DEFAULT_LIVE_DECISION_DB_PATH, RuntimeConfig, RuntimeMode
from parayabuck.tax import cli as tax_cli
from parayabuck.tax import rates as rates_module
from parayabuck.tax.dividends import ensure_table, income_records, parse_activity, store_activities
from parayabuck.tax.errors import LedgerError, MissingRateError, TaxDataError
from parayabuck.tax.export import build_year_report, export_year, ytd_status
from parayabuck.tax.rates import (
    EVDS_BASE_URL,
    FX_LOOKBACK_CALENDAR_DAYS,
    RateBook,
    default_http_get,
    fetch_rates_to_files,
    load_usdtry_csv,
)
from tests.test_tax_ledger_2026_10_27 import ACCOUNT, add_fill, make_book, new_ledger, reopen

# -- helpers --------------------------------------------------------------------


def _utc(day: date) -> str:
    return f"{day.isoformat()}T12:00:00+00:00"


def _activity(activity_id: str, kind: str, on: date, net: float, symbol: str = "QLD") -> dict[str, Any]:
    return {
        "id": activity_id,
        "activity_type": kind,
        "date": on.isoformat(),
        "symbol": symbol,
        "net_amount": str(net),
        "qty": "10",
        "per_share_amount": str(net / 10),
    }


def _store_activities(path: Path, rows: list[dict[str, Any]]) -> None:
    connection = sqlite3.connect(str(path))
    try:
        ensure_table(connection)
        store_activities(connection, [parse_activity(row, ACCOUNT) for row in rows])
        connection.commit()
    finally:
        connection.close()


def _report(path: Path, book: RateBook, year: int = 2026) -> Any:
    readonly = reopen(path)
    try:
        return build_year_report(readonly, ACCOUNT, year, book)
    finally:
        readonly.close()


def _worked_example_ledger(tmp_path: Path) -> Path:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    add_fill(path, fill_id="B1", symbol="QLD", side="buy", when=_utc(date(2025, 3, 10)), quantity=10, price=80.0)
    add_fill(path, fill_id="S1", symbol="QLD", side="sell", when=_utc(date(2026, 2, 12)), quantity=10, price=100.0)
    return path


def _worked_example_rate_files(tmp_path: Path) -> tuple[Path, Path]:
    usd = tmp_path / "usdtry.csv"
    usd.write_text("date,usdtry_forex_buying\n2025-03-07,36.0\n2026-02-11,43.0\n", encoding="utf-8")
    yiufe = tmp_path / "yiufe.csv"
    yiufe.write_text("month,yiufe_index,source\n2025-02,3200.0,TCMB EVDS\n2026-01,3700.0,TCMB EVDS\n", encoding="utf-8")
    return usd, yiufe


def _insert_adjustment(path: Path, symbol: str) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "INSERT INTO portfolio_ownership_adjustments (adjustment_id, account_id, symbol, market, quantity, "
            "sign, kind, note, created_at, consumed_quantity) "
            "VALUES (?, ?, ?, 'equity', 2.0, -1.0, 'liquidation', '', '2026-01-01T00:00:00+00:00', 0.0)",
            (f"ADJ-{symbol}", ACCOUNT, symbol),
        )
        connection.commit()
    finally:
        connection.close()


# -- F7-1: withholding credit capped at the tax on dividends only ---------------


def test_withholding_credit_is_capped_at_tax_on_dividends_not_interest(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    # 4625 USD at 40 = 185,000 TL of dividends; 700 USD withheld = 28,000 TL; 250 USD interest = 10,000 TL.
    _store_activities(
        path,
        [
            _activity("A-DIV-BIG", "DIV", date(2026, 3, 16), 4625.0),
            _activity("A-NRA-BIG", "DIVNRA", date(2026, 3, 16), -700.0),
            _activity("A-INT-BIG", "INT", date(2026, 4, 1), 250.0, symbol=""),
        ],
    )
    report = _report(path, make_book())
    # Dividend tax: 185,000 x 15% = 27,750. Interest tax on top of the dividends: 1,750.
    assert report.dividend_tax_estimate_tl == pytest.approx(27_750.0)
    assert report.interest_tax_estimate_tl == pytest.approx(1_750.0)
    # The 28,000 TL withholding is capped at the dividend tax, not at dividend plus interest tax (29,500).
    assert report.foreign_tax_credit_tl == pytest.approx(27_750.0)
    assert report.dividend_tax_due_tl == pytest.approx(0.0)
    assert report.to_dict()["interest_tax_estimate_tl"] == pytest.approx(1_750.0)


# -- F7-2 / N-5: bounded FX lookback --------------------------------------------


def test_fx_lookback_accepts_a_holiday_gap_and_refuses_beyond_the_window() -> None:
    book = RateBook(usd_try={date(2026, 10, 22): 45.0}, yiufe={})
    # Monday 2026-10-26 after a Friday holiday: Thursday's published rate is used.
    assert book.usd_try_for_trade_date(date(2026, 10, 26)).published_on == date(2026, 10, 22)
    edge = date(2026, 10, 22) + timedelta(days=FX_LOOKBACK_CALENDAR_DAYS)
    assert book.usd_try_for_trade_date(edge).rate == 45.0
    with pytest.raises(MissingRateError, match="between"):
        book.usd_try_for_trade_date(edge + timedelta(days=1))


# -- F7-3: completeness note for the income types that are not imported ---------


def test_report_notes_the_income_types_that_are_not_imported(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    report = _report(path, make_book())
    assert any("DIVCGL, DIVCGS, DIVROC" in note and "not imported" in note for note in report.notes)


# -- F7-4: income FX rule matches the code --------------------------------------


def test_income_uses_the_previous_business_day_rate_like_trade_legs() -> None:
    book = make_book(usd={date(2026, 3, 13): 41.0, date(2026, 3, 16): 99.0})
    # 2026-03-16 is a Monday; the Friday 2026-03-13 rate applies, not the payment-day decoy.
    (record,) = income_records([parse_activity(_activity("A-DIV", "DIV", date(2026, 3, 16), 10.0), ACCOUNT)], book)
    assert record.rate == 41.0
    assert record.rate_published_on == date(2026, 3, 13)


# -- F7-5 / N-3: missing tables, OS errors and bad values never escape ----------


def test_ytd_status_separates_missing_tables_os_errors_and_bad_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _worked_example_ledger(tmp_path)
    usd, yiufe = _worked_example_rate_files(tmp_path)

    def deny(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("denied")

    def bad_value(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("bad row")

    readonly = reopen(path)
    try:
        # No bracket table for 2019: its own status, not "rates_missing".
        assert ytd_status(readonly, ACCOUNT, 2019, usdtry_path=usd, yiufe_path=yiufe)["status"] == "tables_missing"
        monkeypatch.setattr("parayabuck.tax.export.load_rate_book", deny)
        assert ytd_status(readonly, ACCOUNT, 2026, usdtry_path=usd, yiufe_path=yiufe)["status"] == "rates_missing"
        monkeypatch.undo()
        monkeypatch.setattr("parayabuck.tax.export.build_year_report", bad_value)
        status = ytd_status(readonly, ACCOUNT, 2026, usdtry_path=usd, yiufe_path=yiufe)
        assert status["status"] == "error"
        assert "bad row" in status["error"]
    finally:
        readonly.close()


# -- F9-4: zero income rows read as "veri eksik" with notes ---------------------


def test_ytd_status_with_no_income_rows_says_data_is_missing(tmp_path: Path) -> None:
    path = _worked_example_ledger(tmp_path)
    usd, yiufe = _worked_example_rate_files(tmp_path)
    readonly = reopen(path)
    try:
        status = ytd_status(readonly, ACCOUNT, 2026, usdtry_path=usd, yiufe_path=yiufe)
    finally:
        readonly.close()
    assert status["status"] == "ok"
    assert status["net_gain_tl"] == pytest.approx(9_700.0)
    assert status["income_row_count"] == 0
    assert status["income_declaration_status"] == "veri eksik"
    assert any("no dividend or interest" in note for note in status["notes"])


# -- F8-1: import --live targets the LIVE ledger or refuses ---------------------


def test_live_import_targets_the_live_ledger_and_mismatches_are_refused() -> None:
    # No database is opened here: the function only resolves paths.
    paper_default = RuntimeConfig(mode=RuntimeMode.PAPER)
    assert tax_cli._ledger_for_mode(paper_default, live=True, writing=True) == Path(DEFAULT_LIVE_DECISION_DB_PATH)
    assert tax_cli._ledger_for_mode(paper_default, live=False, writing=True) == Path(DEFAULT_DECISION_DB_PATH)

    live_config = RuntimeConfig(mode=RuntimeMode.LIVE)
    assert tax_cli._ledger_for_mode(live_config, live=True, writing=True) == Path(DEFAULT_LIVE_DECISION_DB_PATH)
    assert tax_cli._ledger_for_mode(live_config, live=False, writing=False) == Path(DEFAULT_LIVE_DECISION_DB_PATH)
    with pytest.raises(TaxDataError, match="LIVE ledger"):
        tax_cli._ledger_for_mode(live_config, live=False, writing=True)

    custom_paper = RuntimeConfig(mode=RuntimeMode.PAPER, decision_db_path="data/runtime/custom.sqlite3")
    with pytest.raises(TaxDataError, match="PAPER ledger"):
        tax_cli._ledger_for_mode(custom_paper, live=True, writing=True)


# -- F10-5: an adjustment blocks only the symbol it touches ---------------------


def test_adjustment_on_one_symbol_blocks_only_that_symbols_disposals(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    add_fill(path, fill_id="B1", symbol="VTI", side="buy", when=_utc(date(2025, 3, 10)), quantity=10, price=80.0)
    add_fill(path, fill_id="S1", symbol="VTI", side="sell", when=_utc(date(2026, 2, 12)), quantity=10, price=100.0)
    _insert_adjustment(path, "QLD")

    report = _report(path, make_book())
    assert [item.symbol for item in report.disposals] == ["VTI"]

    # Distinct days: the decision timestamp is unique per account, digest and market.
    add_fill(path, fill_id="B2", symbol="QLD", side="buy", when=_utc(date(2025, 3, 11)), quantity=10, price=80.0)
    add_fill(path, fill_id="S2", symbol="QLD", side="sell", when=_utc(date(2026, 2, 13)), quantity=10, price=100.0)
    with pytest.raises(LedgerError, match="QLD"):
        _report(path, make_book())


# -- D8-13: non-finite numbers are rejected -------------------------------------


def test_non_finite_activity_amounts_are_rejected() -> None:
    row = _activity("A-NAN", "DIV", date(2026, 3, 16), 10.0)
    row["net_amount"] = "nan"
    with pytest.raises(TaxDataError, match="finite"):
        parse_activity(row, ACCOUNT)
    row = _activity("A-INF", "DIV", date(2026, 3, 16), 10.0)
    row["qty"] = "inf"
    with pytest.raises(TaxDataError, match="finite"):
        parse_activity(row, ACCOUNT)


def test_non_finite_rates_are_rejected(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    usd.write_text("date,usdtry_forex_buying\n2026-01-02,nan\n", encoding="utf-8")
    with pytest.raises(TaxDataError, match="finite"):
        load_usdtry_csv(usd)


# -- N-1: fetch never overwrites a differing Yİ-ÜFE month -----------------------


def _fake_get(yiufe_value: str) -> Callable[[str, dict[str, str]], dict[str, Any]]:
    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        if "TP.DK.USD.A.YTL" in url:
            return {"items": [{"Tarih": "09-03-2026", "TP_DK_USD_A_YTL": "43.1"}]}
        return {"items": [{"Tarih": "2026-02", "TP_TUFE1YI_T1": yiufe_value}]}

    return fake_get


def test_fetch_refuses_to_overwrite_a_differing_yiufe_month(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    yiufe = tmp_path / "yiufe.csv"
    yiufe.write_text("month,yiufe_index,source\n2026-02,3000.0,operator\n", encoding="utf-8")
    before = yiufe.read_text(encoding="utf-8")
    with pytest.raises(TaxDataError, match="2026-02"):
        fetch_rates_to_files(
            date(2026, 3, 1),
            date(2026, 3, 31),
            usdtry_path=usd,
            yiufe_path=yiufe,
            api_key="k",
            http_get=_fake_get("3050.0"),
        )
    assert yiufe.read_text(encoding="utf-8") == before
    assert not usd.exists()


def test_fetch_keeps_the_source_label_of_existing_months(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    yiufe = tmp_path / "yiufe.csv"
    yiufe.write_text("month,yiufe_index,source\n2025-12,3100.0,operator\n", encoding="utf-8")
    fetch_rates_to_files(
        date(2026, 1, 1),
        date(2026, 3, 31),
        usdtry_path=usd,
        yiufe_path=yiufe,
        api_key="k",
        http_get=_fake_get("3050.0"),
    )
    with yiufe.open(encoding="utf-8", newline="") as handle:
        rows = {row["month"]: row for row in csv.DictReader(handle)}
    assert rows["2025-12"]["source"] == "operator"
    assert rows["2026-02"]["source"].startswith("TCMB EVDS ")


# -- N-4: EVDS transport and decoding failures are TaxDataError -----------------


def test_evds_transport_failure_becomes_a_tax_data_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: Any, timeout: float) -> Any:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(rates_module.urllib.request, "urlopen", refuse)
    with pytest.raises(TaxDataError, match="EVDS request failed"):
        default_http_get(f"{EVDS_BASE_URL}series=TP.DK.USD.A.YTL", {"key": "k"})


def test_evds_non_json_body_becomes_a_tax_data_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Body:
        def __enter__(self) -> _Body:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def read(self) -> bytes:
            return b"<html>maintenance</html>"

    monkeypatch.setattr(rates_module.urllib.request, "urlopen", lambda request, timeout: _Body())
    with pytest.raises(TaxDataError, match="not JSON"):
        default_http_get(f"{EVDS_BASE_URL}series=TP.DK.USD.A.YTL", {"key": "k"})


def test_run_tax_reports_os_errors_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def disk_full(_args: argparse.Namespace) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(tax_cli, "_export", disk_full)
    assert tax_cli.run_tax(argparse.Namespace(tax_cmd="export")) == 1
    assert "disk full" in capsys.readouterr().err


# -- S9-3: the export writes through atomic_write, not a fixed temporary name ----


def test_export_does_not_follow_a_planted_temporary_symlink(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    report = _report(path, make_book())
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    out = tmp_path / "tax_2026.csv"
    (tmp_path / "tax_2026.csv.tmp").symlink_to(victim)
    csv_path, summary_path = export_year(report, out)
    assert victim.read_text(encoding="utf-8") == "keep"
    assert csv_path.exists()
    assert summary_path.exists()
