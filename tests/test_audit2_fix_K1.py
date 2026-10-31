"""Audit wave 2 fixes K1: TL tax import window, re-import content, notes and list fixes (F2-3, F2-10).

Every test uses tmp_path SQLite and synthetic rates. No network: EVDS and Alpaca are
reached only through injected fake functions.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from parayabuck.config import DEFAULT_DECISION_DB_PATH, DEFAULT_LIVE_DECISION_DB_PATH, RuntimeConfig, RuntimeMode
from parayabuck.core.models import Market
from parayabuck.monitoring import paper_incident
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.tax import cli as tax_cli
from parayabuck.tax.dividends import (
    OTHER_TYPES_NOTE,
    coverage_spans_year,
    ensure_table,
    fetch_activities,
    load_activities,
    parse_activity,
    record_coverage,
    store_activities,
)
from parayabuck.tax.errors import LedgerError, TaxDataError
from parayabuck.tax.export import build_year_report, ytd_status
from parayabuck.tax.ledger import assert_no_ownership_adjustments
from parayabuck.tax.rates import RateBook, fetch_rates_to_files

ACCOUNT = "ACC-K1"


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


def _ledger(tmp_path: Path, name: str = "ledger") -> Path:
    """The real portfolio schema in a tmp_path database, with one runtime session for ACCOUNT."""
    path = tmp_path / f"{name}.sqlite3"
    repository = PortfolioStateRepository(path)
    connection = repository.store.connection
    connection.execute(
        "INSERT INTO portfolio_runtime_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("SESSION-K1", ACCOUNT, "cfg-digest", "paper", "2024-11-01T00:00:00+00:00", None, "running"),
    )
    connection.commit()
    repository.close()
    return path


def _store(path: Path, rows: list[dict[str, Any]], coverage: tuple[date, date] | None = None) -> None:
    connection = sqlite3.connect(str(path))
    try:
        ensure_table(connection)
        store_activities(connection, [parse_activity(row, ACCOUNT) for row in rows])
        if coverage is not None:
            record_coverage(connection, ACCOUNT, coverage[0], coverage[1])
        connection.commit()
    finally:
        connection.close()


def _report(path: Path, rates: RateBook, year: int = 2026) -> Any:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return build_year_report(connection, ACCOUNT, year, rates)
    finally:
        connection.close()


def _rate_book() -> RateBook:
    """A 40.0 USD/TRY rate on every weekday of 2026, so each payment date has a previous business day."""
    rates: dict[date, float] = {}
    day = date(2026, 1, 1)
    while day <= date(2026, 12, 31):
        if day.weekday() < 5:
            rates[day] = 40.0
        day += timedelta(days=1)
    return RateBook(usd_try=rates, yiufe={})


def _rate_files(tmp_path: Path) -> tuple[Path, Path]:
    usd = tmp_path / "usdtry.csv"
    lines = ["date,usdtry_forex_buying"]
    day = date(2025, 12, 1)
    while day <= date(2026, 12, 31):
        if day.weekday() < 5:
            lines.append(f"{day.isoformat()},40.0")
        day += timedelta(days=1)
    usd.write_text("\n".join(lines) + "\n", encoding="utf-8")
    yiufe = tmp_path / "yiufe.csv"
    yiufe.write_text("month,yiufe_index,source\n", encoding="utf-8")
    return usd, yiufe


# -- F2-10-2: after is exclusive -----------------------------------------------


def test_activity_window_starts_one_day_before_since() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def get(path: str, params: Any) -> Any:
        calls.append((path, dict(params)))
        return {"id": ACCOUNT} if path == "/account" else []

    fetch_activities(get, date(2026, 1, 1))
    activity_calls = [params for path, params in calls if path == "/account/activities"]
    assert activity_calls and all(params["after"] == "2025-12-31" for params in activity_calls)


# -- F2-10-3: re-import with changed content --------------------------------------


def test_reimport_with_changed_content_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    _store(
        path,
        [_activity("A-DIV-1", "DIV", date(2026, 3, 16), 10.0), _activity("A-NRA-1", "DIVNRA", date(2026, 3, 16), -2.0)],
    )
    restated = [
        _activity("A-DIV-1", "DIV", date(2026, 3, 16), 12.0),  # restated amount
        _activity("A-NRA-1", "DIVNRA", date(2026, 3, 16), -2.0),  # unchanged
        _activity("A-INT-9", "INT", date(2026, 4, 1), 5.0, symbol=""),  # new, must not be written either
    ]
    connection = sqlite3.connect(str(path))
    try:
        with pytest.raises(TaxDataError) as caught:
            store_activities(connection, [parse_activity(row, ACCOUNT) for row in restated])
        message = str(caught.value)
        assert "1 stored activity changed content" in message
        assert "A-DIV-1" in message
        assert "A-NRA-1" not in message and "A-INT-9" not in message
        stored = load_activities(connection, ACCOUNT)
    finally:
        connection.close()
    assert [row.net_amount for row in stored if row.activity_id == "A-DIV-1"] == [10.0]
    assert {row.activity_id for row in stored} == {"A-DIV-1", "A-NRA-1"}


def test_reimport_of_the_same_content_is_still_a_no_op(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    rows = [parse_activity(_activity("A-DIV-1", "DIV", date(2026, 3, 16), 10.0), ACCOUNT)]
    connection = sqlite3.connect(str(path))
    try:
        ensure_table(connection)
        assert store_activities(connection, rows) == (1, 0)
        assert store_activities(connection, rows) == (0, 1)
    finally:
        connection.close()


def test_same_id_under_another_type_is_refused(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    _store(path, [_activity("A-X-1", "DIV", date(2026, 3, 16), 10.0)])
    connection = sqlite3.connect(str(path))
    try:
        with pytest.raises(TaxDataError, match="A-X-1"):
            store_activities(connection, [parse_activity(_activity("A-X-1", "INT", date(2026, 3, 16), 10.0), ACCOUNT)])
    finally:
        connection.close()


# -- F2-3-1 / F2-3-3 / F2-10-1: notes and the import window -------------------------


def test_withholding_above_the_cap_is_explicit_and_the_credit_is_unchanged(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    _store(
        path,
        [_activity("A-DIV-1", "DIV", date(2026, 3, 16), 10.0), _activity("A-NRA-1", "DIVNRA", date(2026, 3, 16), -4.0)],
    )
    report = _report(path, _rate_book())
    # 400 TL of dividends at 15% is 60 TL; the 40% withholding (160 TL) is credited only up to that.
    assert report.foreign_tax_credit_tl == pytest.approx(60.0)
    cap_notes = [note for note in report.notes if "treaty cap" in note]
    assert len(cap_notes) == 1
    assert "2.00 USD" in cap_notes[0]  # 4 USD withheld minus 20% of 10 USD gross


def test_withholding_without_gross_is_flagged(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    _store(path, [_activity("A-NRA-1", "DIVNRA", date(2026, 3, 16), -2.0)])
    report = _report(path, _rate_book())
    assert report.dividend_net_tl < 0
    assert any("stopaj" in note and "brüt temettü" in note for note in report.notes)


def test_dividend_tax_due_under_the_threshold_carries_a_note(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    _store(path, [_activity("A-DIV-1", "DIV", date(2026, 3, 16), 10.0)])
    report = _report(path, _rate_book())
    assert report.income_declaration_required is False
    assert report.dividend_tax_due_tl == pytest.approx(60.0)  # kept as before: a conservative figure
    assert any("beyan eşiğinin altında" in note for note in report.notes)


def test_report_names_the_income_types_that_are_not_fetched(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    _store(path, [_activity("A-DIV-1", "DIV", date(2026, 3, 16), 10.0)])
    assert OTHER_TYPES_NOTE in _report(path, _rate_book()).notes


def test_income_status_needs_coverage_that_spans_the_year(tmp_path: Path) -> None:
    usd, yiufe = _rate_files(tmp_path)
    dividend = [_activity("A-DIV-1", "DIV", date(2026, 3, 16), 10.0)]
    # coverage is (since, imported_on): the first import ran in October, the second in January 2027.
    partial = _ledger(tmp_path, "partial")
    _store(partial, dividend, coverage=(date(2026, 6, 1), date(2026, 10, 27)))
    spanning = _ledger(tmp_path, "full")
    _store(spanning, dividend, coverage=(date(2026, 1, 1), date(2027, 1, 2)))

    readonly = sqlite3.connect(f"file:{partial}?mode=ro", uri=True)
    try:
        status = ytd_status(readonly, ACCOUNT, 2026, usdtry_path=usd, yiufe_path=yiufe)
    finally:
        readonly.close()
    assert status["status"] == "ok"
    assert status["income_coverage_complete"] is False
    assert status["income_declaration_status"] == "veri eksik"

    readonly = sqlite3.connect(f"file:{spanning}?mode=ro", uri=True)
    try:
        status = ytd_status(readonly, ACCOUNT, 2026, usdtry_path=usd, yiufe_path=yiufe)
    finally:
        readonly.close()
    assert status["income_coverage_complete"] is True
    assert status["income_declaration_status"] == "aşılmadı"


def test_ledger_imported_before_coverage_existed_reads_veri_eksik(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    _store(path, [_activity("A-DIV-1", "DIV", date(2026, 3, 16), 10.0)])
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("DROP TABLE tax_import_coverage")
        connection.commit()
    finally:
        connection.close()
    usd, yiufe = _rate_files(tmp_path)
    readonly = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert coverage_spans_year(readonly, ACCOUNT, 2026) is False
        status = ytd_status(readonly, ACCOUNT, 2026, usdtry_path=usd, yiufe_path=yiufe)
    finally:
        readonly.close()
    assert status["income_declaration_status"] == "veri eksik"


def test_coverage_must_start_by_january_and_end_by_december(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    connection = sqlite3.connect(str(path))
    try:
        ensure_table(connection)
        assert coverage_spans_year(connection, ACCOUNT, 2026) is False
        record_coverage(connection, ACCOUNT, date(2026, 6, 1), date(2027, 1, 2))  # starts too late
        assert coverage_spans_year(connection, ACCOUNT, 2026) is False
        record_coverage(connection, ACCOUNT, date(2026, 1, 1), date(2026, 10, 27))  # ends too early
        assert coverage_spans_year(connection, ACCOUNT, 2026) is False
        through = record_coverage(connection, ACCOUNT, date(2026, 1, 1), date(2027, 1, 2))
        assert through == date(2026, 12, 31)  # two days before the import ran (F3-1-4)
        assert coverage_spans_year(connection, ACCOUNT, 2026) is True
        assert coverage_spans_year(connection, "OTHER-ACCOUNT", 2026) is False
    finally:
        connection.close()


def test_unknown_account_reads_unavailable_not_zero_figures(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    usd, yiufe = _rate_files(tmp_path)
    readonly = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        status = ytd_status(readonly, "unknown", 2026, usdtry_path=usd, yiufe_path=yiufe)
    finally:
        readonly.close()
    assert status["status"] == "unavailable"


# -- F2-3-6: the ownership guard names the creation dates ----------------------------


def test_ownership_adjustment_refusal_names_the_creation_date(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "INSERT INTO portfolio_ownership_adjustments (adjustment_id, account_id, symbol, market, quantity, "
            "sign, kind, note, created_at, consumed_quantity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ADJ-K1",
                ACCOUNT,
                "QLD",
                Market.EQUITY.value,
                1.0,
                -1.0,
                "liquidation",
                "",
                "2027-02-03T10:00:00+00:00",
                0.0,
            ),
        )
        connection.commit()
        with pytest.raises(LedgerError, match="2027-02-03"):
            assert_no_ownership_adjustments(connection, ACCOUNT, {"QLD"})
    finally:
        connection.close()


# -- F2-3-4: PAPER import into the LIVE ledger path ----------------------------------


def test_paper_import_refuses_a_config_whose_ledger_is_the_live_path() -> None:
    paper_on_live = RuntimeConfig(mode=RuntimeMode.PAPER, decision_db_path=DEFAULT_LIVE_DECISION_DB_PATH)
    with pytest.raises(TaxDataError, match="LIVE ledger path"):
        tax_cli._ledger_for_mode(paper_on_live, live=False, writing=True)
    # Reading (export) still resolves the configured ledger.
    assert tax_cli._ledger_for_mode(paper_on_live, live=False, writing=False) == Path(DEFAULT_LIVE_DECISION_DB_PATH)
    assert tax_cli._ledger_for_mode(RuntimeConfig(mode=RuntimeMode.PAPER), live=False, writing=True) == Path(
        DEFAULT_DECISION_DB_PATH
    )


# -- F2-3-2: Yİ-ÜFE series without an overlapping month ----------------------------


def _fake_get(yiufe_value: str) -> Any:
    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        if "TP.DK.USD.A.YTL" in url:
            return {"items": [{"Tarih": "09-03-2026", "TP_DK_USD_A_YTL": "43.1"}]}
        return {"items": [{"Tarih": "2026-02", "TP_TUFE1YI_T1": yiufe_value}]}

    return fake_get


def test_yiufe_months_without_overlap_are_refused_until_confirmed(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    yiufe = tmp_path / "yiufe.csv"
    yiufe.write_text("month,yiufe_index,source\n2025-12,3100.0,operator\n", encoding="utf-8")
    before = yiufe.read_text(encoding="utf-8")
    with pytest.raises(TaxDataError, match="confirm-yiufe-series"):
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

    counts = fetch_rates_to_files(
        date(2026, 3, 1),
        date(2026, 3, 31),
        usdtry_path=usd,
        yiufe_path=yiufe,
        api_key="k",
        http_get=_fake_get("3050.0"),
        confirmed_series=True,
    )
    assert counts["yiufe_rows"] == 2


def test_yiufe_months_that_agree_with_an_overlap_need_no_confirmation(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    yiufe = tmp_path / "yiufe.csv"
    yiufe.write_text("month,yiufe_index,source\n2026-02,3050.0,operator\n", encoding="utf-8")
    counts = fetch_rates_to_files(
        date(2026, 3, 1),
        date(2026, 3, 31),
        usdtry_path=usd,
        yiufe_path=yiufe,
        api_key="k",
        http_get=_fake_get("3050.0"),
    )
    assert counts["yiufe_rows"] == 1


# -- F2-10-4: PAPER equity bridge income classes -------------------------------------


def test_paper_bridge_books_withholding_and_interest_as_income() -> None:
    # The script's request list is covered by tests/test_paper_incident.py (paginated cash activities).
    assert {"DIV", "DIVNRA", "INT"} <= paper_incident.INCOME_ACTIVITIES
    assert not {"DIVNRA", "INT"} & paper_incident.CASH_CHARGE_ACTIVITIES
