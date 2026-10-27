"""TL tax ledger invariants (decision record: docs/tax/turkish_tax_rules.md).

Every test uses tmp_path SQLite and synthetic fills and rates. No network: EVDS
and Alpaca are reached only through injected fake functions.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from parayabuck.monitoring.daily_summary import format_tax_ytd
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.tax.dividends import (
    ensure_table,
    fetch_activities,
    income_records,
    load_activities,
    parse_activity,
    store_activities,
)
from parayabuck.tax.errors import LedgerError, MissingIndexError, MissingRateError, TaxDataError
from parayabuck.tax.export import COLUMNS, build_year_report, export_year, ytd_status
from parayabuck.tax.ledger import build_disposals
from parayabuck.tax.rates import (
    EVDS_KEY_ENV,
    RateBook,
    fetch_rates_to_files,
    fetch_usd_try,
    fetch_yiufe,
    load_rate_book,
    load_usdtry_csv,
    load_yiufe_csv,
    previous_weekday,
    prior_month_key,
)
from parayabuck.tax.rules import declaration_threshold_tl, progressive_tax

ACCOUNT = "ACC-TEST-1"


# -- fixtures -----------------------------------------------------------------


def _weekday_rates(start: date, end: date, rate: float) -> dict[date, float]:
    rates: dict[date, float] = {}
    day = start
    while day <= end:
        if day.weekday() < 5:
            rates[day] = rate
        day += timedelta(days=1)
    return rates


def _months(first: str, last: str) -> list[str]:
    year, month = map(int, first.split("-"))
    stop_year, stop_month = map(int, last.split("-"))
    months: list[str] = []
    while (year, month) <= (stop_year, stop_month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def make_book(usd: dict[date, float] | None = None, yiufe: dict[str, float] | None = None) -> RateBook:
    """Constant rates by default; tests override the exact days they check."""
    rates = _weekday_rates(date(2024, 11, 1), date(2027, 1, 31), 40.0)
    indices = {month: 3000.0 for month in _months("2024-10", "2026-12")}
    rates.update(usd or {})
    indices.update(yiufe or {})
    return RateBook(usd_try=rates, yiufe=indices)


def worked_example_book() -> RateBook:
    """Rates and indices of the decision record's worked example, plus decoys.

    The decoys sit on the trade dates themselves: a trade must use the rate of the
    previous business day, so a decoy on the trade date would give a wrong answer.
    """
    return make_book(
        usd={
            date(2025, 3, 7): 36.0,  # Friday before the Monday 2025-03-10 buy
            date(2025, 3, 10): 99.0,  # decoy: trade date itself is not used
            date(2026, 2, 11): 43.0,  # Wednesday before the Thursday 2026-02-12 sale
            date(2026, 2, 12): 99.0,  # decoy
        },
        yiufe={"2025-02": 3200.0, "2026-01": 3700.0},
    )


def _utc(day: date, hour: int = 12) -> str:
    return f"{day.isoformat()}T{hour:02d}:00:00+00:00"


def new_ledger(path: Path) -> None:
    """Create the real portfolio schema at ``path`` with one runtime session for ACCOUNT."""
    repository = PortfolioStateRepository(path)
    connection = repository.store.connection
    connection.execute(
        "INSERT INTO portfolio_runtime_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("SESSION-1", ACCOUNT, "cfg-digest", "paper", "2024-11-01T00:00:00+00:00", None, "running"),
    )
    connection.commit()
    repository.close()


def _writable(connection_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(connection_path))


def add_fill(
    path: Path,
    *,
    fill_id: str,
    symbol: str,
    side: str,
    when: str,
    quantity: float,
    price: float,
    fee: float = 0.0,
) -> None:
    """Insert one bot fill through the same tables the runtime writes."""
    connection = _writable(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        decision_id = f"DEC-{fill_id}"
        order_id = f"ORD-{fill_id}"
        connection.execute(
            "INSERT INTO portfolio_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (decision_id, "SESSION-1", ACCOUNT, "cfg-digest", when, "equity", "test", "{}", "{}"),
        )
        connection.execute(
            """INSERT INTO portfolio_orders (client_order_id, decision_id, symbol, market, side, quantity,
                   reference_price, status, broker_order_id, cumulative_filled_quantity, average_fill_price,
                   created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id,
                decision_id,
                symbol,
                "equity",
                side,
                quantity,
                price,
                "filled",
                "B-" + fill_id,
                quantity,
                price,
                when,
            ),
        )
        connection.execute(
            """INSERT INTO portfolio_fills (activity_id, client_order_id, occurred_at, quantity, price, fee,
                   realized_pnl, unrealized_pnl, slippage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fill_id, order_id, when, quantity, price, fee, 0.0, 0.0, 0.0),
        )
        connection.commit()
    finally:
        connection.close()


def reopen(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# -- rates ----------------------------------------------------------------------


def test_worked_example_reproduces_9700_tl_exactly(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    add_fill(path, fill_id="B1", symbol="QLD", side="buy", when=_utc(date(2025, 3, 10)), quantity=10, price=80.0)
    add_fill(path, fill_id="S1", symbol="QLD", side="sell", when=_utc(date(2026, 2, 12)), quantity=10, price=100.0)
    connection = reopen(path)
    try:
        (disposal,) = build_disposals(connection, ACCOUNT, worked_example_book())
    finally:
        connection.close()
    assert disposal.cost_tl_nominal == pytest.approx(28_800.0, abs=1e-9)
    assert disposal.proceeds_tl == pytest.approx(43_000.0, abs=1e-9)
    assert disposal.index_ratio == pytest.approx(1.15625, abs=1e-12)
    assert disposal.indexation_applied is True
    assert disposal.cost_tl_used == pytest.approx(33_300.0, abs=1e-9)
    assert disposal.gain_tl == 9_700.0
    assert disposal.buy_rate_published_on == date(2025, 3, 7)
    assert disposal.sell_rate_published_on == date(2026, 2, 11)
    assert (disposal.index_buy_month, disposal.index_sell_month) == ("2025-02", "2026-01")


def test_rule4_uses_previous_business_day_and_weekend_gap() -> None:
    book = make_book(usd={date(2026, 10, 22): 45.0, date(2026, 10, 23): 46.0, date(2026, 10, 26): 99.0})
    # Monday trade: the previous business day is Friday 2026-10-23.
    assert book.usd_try_for_trade_date(date(2026, 10, 26)).published_on == date(2026, 10, 23)
    # Friday trade: Thursday's published rate, not the same day.
    assert book.usd_try_for_trade_date(date(2026, 10, 23)).rate == 45.0
    assert previous_weekday(date(2026, 10, 26)) == date(2026, 10, 23)
    assert prior_month_key(date(2026, 1, 15)) == "2025-12"


def test_missing_rate_raises_and_names_the_date() -> None:
    book = RateBook(usd_try={date(2026, 10, 22): 45.0}, yiufe={"2026-09": 3000.0})
    # Thursday 2026-10-22 is inside the lookback for Monday 2026-10-26 (see the audit
    # fix tests). A trade date 14 days after the file's last rate is not covered.
    with pytest.raises(MissingRateError, match="2026-10-22"):
        book.usd_try_for_trade_date(date(2026, 11, 5))
    # A trade date with no earlier rate at all also raises.
    with pytest.raises(MissingRateError):
        RateBook(usd_try={date(2026, 10, 26): 45.0}, yiufe={}).usd_try_for_trade_date(date(2026, 10, 26))


def test_missing_index_month_raises_and_names_the_month() -> None:
    book = RateBook(usd_try=_weekday_rates(date(2025, 1, 1), date(2026, 12, 31), 40.0), yiufe={"2026-01": 3700.0})
    with pytest.raises(MissingIndexError, match="2025-02"):
        book.yiufe_for_month("2025-02")


def test_research_csv_formats_are_accepted(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    usd.write_text(
        "date,usdtry_forex_buying,usdtry_forex_selling\n2026-01-02,43.10,43.20\n2026-01-05,43.25,43.40\n",
        encoding="utf-8",
    )
    yiufe = tmp_path / "yiufe.csv"
    yiufe.write_text("month,yiufe_index,source\n2025-12,3100.5,TCMB EVDS\n2026-01,3150.0,TCMB EVDS\n", encoding="utf-8")
    book = load_rate_book(usd, yiufe)
    assert book.usd_try_for_trade_date(date(2026, 1, 5)).rate == 43.10
    assert book.yiufe_for_month("2026-01") == 3150.0


def test_missing_rate_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(TaxDataError, match="usdtry.csv"):
        load_usdtry_csv(tmp_path / "usdtry.csv")


def test_duplicate_or_invalid_rows_raise(tmp_path: Path) -> None:
    dup = tmp_path / "dup.csv"
    dup.write_text("date,usdtry_forex_buying\n2026-01-02,43.1\n2026-01-02,43.2\n", encoding="utf-8")
    with pytest.raises(TaxDataError, match="duplicate"):
        load_usdtry_csv(dup)
    bad = tmp_path / "bad.csv"
    bad.write_text("month,yiufe_index\n2026-13,3000\n", encoding="utf-8")
    with pytest.raises(TaxDataError):
        load_yiufe_csv(bad)


def test_evds_fetch_uses_injected_http_and_keeps_key_out_of_url() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        calls.append((url, dict(headers)))
        if "TP.DK.USD.A.YTL" in url:
            return {
                "items": [
                    {"Tarih": "09-03-2026", "TP_DK_USD_A_YTL": "43.1234"},
                    {"Tarih": "10-03-2026", "TP_DK_USD_A_YTL": None},
                ]
            }
        return {"items": [{"Tarih": "2026-01", "TP_TUFE1YI_T1": "3100.5"}]}

    rates = fetch_usd_try(date(2026, 3, 1), date(2026, 3, 31), "secret-key", fake_get)
    indices = fetch_yiufe(date(2026, 1, 1), date(2026, 1, 31), "secret-key", fake_get)
    assert rates == {date(2026, 3, 9): 43.1234}
    assert indices == {"2026-01": 3100.5}
    assert all("secret-key" not in url for url, _ in calls)
    assert all(headers.get("key") == "secret-key" for _, headers in calls)
    assert "startDate=01-03-2026" in calls[0][0]


def test_evds_fetch_without_key_raises_before_any_request() -> None:
    calls: list[str] = []

    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        calls.append(url)
        return {"items": []}

    with pytest.raises(TaxDataError, match=EVDS_KEY_ENV):
        fetch_usd_try(date(2026, 3, 1), date(2026, 3, 31), "  ", fake_get)
    assert calls == []


def test_fetch_rates_to_files_merges_and_writes_loadable_csv(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    usd.write_text("date,usdtry_forex_buying\n2026-03-02,42.0\n", encoding="utf-8")
    yiufe = tmp_path / "yiufe.csv"

    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        if "TP.DK.USD.A.YTL" in url:
            return {"items": [{"Tarih": "09-03-2026", "TP_DK_USD_A_YTL": "43.1"}]}
        return {"items": [{"Tarih": "2026-02", "TP_TUFE1YI_T1": "3050.0"}]}

    counts = fetch_rates_to_files(
        date(2026, 3, 1),
        date(2026, 3, 31),
        usdtry_path=usd,
        yiufe_path=yiufe,
        api_key="k",
        http_get=fake_get,
        confirmed_series=True,  # the empty yiufe file has no overlapping month to cross-check against
    )
    assert counts == {"usdtry_rows": 2, "yiufe_rows": 1}
    book = load_rate_book(usd, yiufe)
    assert book.usd_try_for_trade_date(date(2026, 3, 10)).rate == 43.1


# -- ledger ---------------------------------------------------------------------


def test_indexation_below_ten_percent_uses_nominal_cost(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    add_fill(path, fill_id="B1", symbol="QLD", side="buy", when=_utc(date(2025, 3, 10)), quantity=10, price=80.0)
    add_fill(path, fill_id="S1", symbol="QLD", side="sell", when=_utc(date(2026, 2, 12)), quantity=10, price=100.0)
    book = worked_example_book()
    book = RateBook(usd_try=book.usd_try, yiufe={**book.yiufe, "2026-01": 3450.0})  # ratio 1.078125
    connection = reopen(path)
    try:
        (disposal,) = build_disposals(connection, ACCOUNT, book)
    finally:
        connection.close()
    assert disposal.indexation_applied is False
    assert disposal.cost_tl_used == pytest.approx(28_800.0)
    assert disposal.gain_tl == pytest.approx(14_200.0)


def test_fifo_across_two_buys_and_fees(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    add_fill(
        path, fill_id="B1", symbol="QLD", side="buy", when=_utc(date(2025, 3, 10)), quantity=10, price=80.0, fee=8.0
    )
    add_fill(path, fill_id="B2", symbol="QLD", side="buy", when=_utc(date(2025, 6, 10)), quantity=5, price=90.0)
    add_fill(
        path, fill_id="S1", symbol="QLD", side="sell", when=_utc(date(2026, 2, 12)), quantity=12, price=100.0, fee=4.0
    )
    book = make_book(
        usd={date(2025, 6, 9): 37.0, date(2026, 2, 11): 43.0, date(2025, 3, 7): 36.0},
        yiufe={"2025-02": 3200.0, "2025-05": 3600.0, "2026-01": 3700.0},
    )
    connection = reopen(path)
    try:
        disposals = build_disposals(connection, ACCOUNT, book)
    finally:
        connection.close()
    assert [(item.buy_fill_id, item.quantity) for item in disposals] == [("B1", 10.0), ("B2", 2.0)]
    first, second = disposals
    # Lot 1 carries its full 8 USD buy fee and the sale fee share of 10/12 of 4 USD.
    assert first.cost_usd == pytest.approx(808.0)
    assert first.sell_fee_usd == pytest.approx(4.0 * 10 / 12)
    # Lot 2 index ratio 3700/3600 is below 10%, so its cost stays nominal.
    assert second.indexation_applied is False
    assert second.cost_tl_used == pytest.approx(2 * 90.0 * 37.0)
    assert first.gain_tl == pytest.approx(first.proceeds_tl - first.cost_tl_used)
    assert sum(item.quantity for item in disposals) == 12.0


def test_sell_beyond_bot_lots_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    add_fill(path, fill_id="B1", symbol="QLD", side="buy", when=_utc(date(2025, 3, 10)), quantity=5, price=80.0)
    add_fill(path, fill_id="S1", symbol="QLD", side="sell", when=_utc(date(2026, 2, 12)), quantity=6, price=100.0)
    connection = reopen(path)
    try:
        with pytest.raises(LedgerError, match="exceeds the open bot lots"):
            build_disposals(connection, ACCOUNT, worked_example_book())
    finally:
        connection.close()


def test_ownership_adjustments_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    connection = _writable(path)
    connection.execute(
        "INSERT INTO portfolio_ownership_adjustments (adjustment_id, account_id, symbol, market, quantity, sign, kind, note, created_at, consumed_quantity) "
        "VALUES ('ADJ-1', ?, 'QLD', 'equity', 2.0, -1.0, 'liquidation', '', '2026-01-01T00:00:00+00:00', 0.0)",
        (ACCOUNT,),
    )
    connection.commit()
    connection.close()
    readonly = reopen(path)
    try:
        with pytest.raises(LedgerError, match="ownership adjustment"):
            build_disposals(readonly, ACCOUNT, worked_example_book())
    finally:
        readonly.close()


def test_same_year_gains_and_losses_net_without_carry_forward(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    add_fill(path, fill_id="B1", symbol="QLD", side="buy", when=_utc(date(2026, 1, 5)), quantity=10, price=100.0)
    add_fill(path, fill_id="B2", symbol="QLD", side="buy", when=_utc(date(2026, 1, 6)), quantity=10, price=120.0)
    # Sale 1 (lot B1, 100 -> 130) gains 12,000 TL; sale 2 (lot B2, 120 -> 100) loses 8,000 TL.
    add_fill(path, fill_id="S1", symbol="QLD", side="sell", when=_utc(date(2026, 2, 12)), quantity=10, price=130.0)
    add_fill(path, fill_id="S2", symbol="QLD", side="sell", when=_utc(date(2026, 3, 12)), quantity=10, price=100.0)
    connection = reopen(path)
    try:
        report = build_year_report(connection, ACCOUNT, 2026, make_book())
    finally:
        connection.close()
    assert report.gains_tl == pytest.approx(12_000.0)
    assert report.losses_tl == pytest.approx(-8_000.0)
    # Netted inside the year: 4,000 TL is the base for the estimate.
    assert report.net_gain_tl == pytest.approx(4_000.0)
    assert report.estimated_tax_tl == pytest.approx(600.0)  # 15% bracket


def test_loss_year_has_zero_estimated_tax(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    add_fill(path, fill_id="B1", symbol="QLD", side="buy", when=_utc(date(2026, 1, 5)), quantity=10, price=100.0)
    add_fill(path, fill_id="S1", symbol="QLD", side="sell", when=_utc(date(2026, 2, 12)), quantity=10, price=60.0)
    book = make_book(usd={date(2026, 1, 2): 40.0, date(2026, 2, 11): 40.0})
    connection = reopen(path)
    try:
        report = build_year_report(connection, ACCOUNT, 2026, book)
    finally:
        connection.close()
    assert report.net_gain_tl < 0
    assert report.estimated_tax_tl == 0.0
    assert any("not carried forward" in note for note in report.notes)


def test_brackets_and_declaration_threshold() -> None:
    assert progressive_tax(0.0, 2026) == 0.0
    assert progressive_tax(100_000.0, 2026) == pytest.approx(15_000.0)
    assert progressive_tax(500_000.0, 2026) == pytest.approx(97_500.0)
    assert progressive_tax(190_000.0, 2026) == pytest.approx(28_500.0)
    assert declaration_threshold_tl(2026) == 22_000.0
    with pytest.raises(TaxDataError, match="bracket table"):
        progressive_tax(1.0, 2019)


# -- dividends ------------------------------------------------------------------


class FakeAlpaca:
    """Fake raw GET: ``/account`` and paged ``/account/activities``. No network."""

    def __init__(self, rows: list[dict[str, Any]], *, page_size: int | None = None) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.page_size = page_size

    def __call__(self, path: str, params: Any) -> Any:
        self.calls.append((path, dict(params)))
        if path == "/account":
            return {"id": ACCOUNT}
        if path != "/account/activities":
            raise AssertionError(f"unexpected path {path}")
        size = int(params["page_size"])
        token = params.get("page_token")
        start = 0
        if token is not None:
            start = next(index for index, row in enumerate(self.rows) if row["id"] == token) + 1
        return self.rows[start : start + size]


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


def _dividend_rows() -> list[dict[str, Any]]:
    return [
        _activity("A-DIV-1", "DIV", date(2026, 3, 16), 10.0),
        _activity("A-NRA-1", "DIVNRA", date(2026, 3, 16), -2.0),
        _activity("A-INT-1", "INT", date(2026, 4, 1), 5.0, symbol=""),
        _activity("A-DIV-2", "DIV", date(2026, 6, 15), 1.0),
    ]


def _dividend_book() -> RateBook:
    return make_book(usd={date(2026, 3, 13): 40.0, date(2026, 3, 31): 41.0, date(2026, 6, 12): 42.0})


def _report_with_activities(tmp_path: Path, rows: list[dict[str, Any]], book: RateBook) -> Any:
    path = tmp_path / "activities.sqlite3"
    new_ledger(path)
    connection = _writable(path)
    try:
        ensure_table(connection)
        store_activities(connection, [parse_activity(row, ACCOUNT) for row in rows])
        connection.commit()
    finally:
        connection.close()
    readonly = reopen(path)
    try:
        return build_year_report(readonly, ACCOUNT, 2026, book)
    finally:
        readonly.close()


def test_activity_import_is_paged_and_idempotent(tmp_path: Path) -> None:
    fake = FakeAlpaca(_dividend_rows(), page_size=2)
    fetched = fetch_activities(fake, date(2026, 1, 1), page_size=2)
    assert [row["id"] for row in fetched] == ["A-DIV-1", "A-NRA-1", "A-INT-1", "A-DIV-2"]
    assert any(params.get("page_token") == "A-NRA-1" for _, params in fake.calls)
    assert all(params["activity_types"] == "DIV,DIVNRA,INT" for _, params in fake.calls if "activity_types" in params)

    parsed = [parse_activity(row, ACCOUNT) for row in fetched]
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    connection = _writable(path)
    try:
        ensure_table(connection)
        first = store_activities(connection, parsed, raw_by_id={row["id"]: row for row in fetched})
        second = store_activities(connection, parsed, raw_by_id={row["id"]: row for row in fetched})
        connection.commit()
        stored = load_activities(connection, ACCOUNT, 2026)
    finally:
        connection.close()
    assert first == (4, 0)
    assert second == (0, 4)
    assert len(stored) == 4
    assert {row.activity_id for row in stored} == {"A-DIV-1", "A-NRA-1", "A-INT-1", "A-DIV-2"}


def test_pagination_that_does_not_advance_raises() -> None:
    def stuck(path: str, params: Any) -> Any:
        if path == "/account":
            return {"id": ACCOUNT}
        return [{"id": "X1"}, {"id": "X1"}]

    with pytest.raises(TaxDataError, match="did not advance"):
        fetch_activities(stuck, date(2026, 1, 1), page_size=2)


def test_dividend_withholding_and_credit_cap(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    parsed = [parse_activity(row, ACCOUNT) for row in _dividend_rows()]
    connection = _writable(path)
    try:
        ensure_table(connection)
        store_activities(connection, parsed)
        connection.commit()
    finally:
        connection.close()

    readonly = reopen(path)
    try:
        report = build_year_report(readonly, ACCOUNT, 2026, _dividend_book())
    finally:
        readonly.close()
    # Dividends 11 USD: 10 at 40 and 1 at 42; withheld 2 USD at 40; interest 5 USD at 41.
    assert report.dividend_gross_tl == pytest.approx(10 * 40.0 + 1 * 42.0)
    assert report.withholding_tl == pytest.approx(2 * 40.0)
    assert report.dividend_net_tl == pytest.approx(442.0 - 80.0)
    assert report.interest_tl == pytest.approx(5 * 41.0)
    assert report.income_declaration_required is False  # 647 TL is under 22,000 TL
    # Credit is the lower of the withheld TL and the Turkish tax on the dividends only.
    # Interest sits above the dividends in the bracket stack; its tax is reported
    # separately and is not creditable (no US tax is withheld on it).
    assert report.dividend_tax_estimate_tl == pytest.approx(442.0 * 0.15)
    assert report.interest_tax_estimate_tl == pytest.approx(205.0 * 0.15)
    assert report.foreign_tax_credit_tl == pytest.approx(442.0 * 0.15)
    assert report.dividend_tax_due_tl == pytest.approx(0.0)
    # 2 USD withheld on 11 USD gross is about 18%, under the 20% treaty cap.
    assert not any("treaty cap" in note for note in report.notes)


def test_credit_is_capped_at_turkish_tax_on_the_income(tmp_path: Path) -> None:
    rows = [
        _activity("A-DIV-1", "DIV", date(2026, 3, 16), 10.0),
        _activity("A-NRA-1", "DIVNRA", date(2026, 3, 16), -20.0),
    ]
    report = _report_with_activities(tmp_path, rows, _dividend_book())
    assert report.withholding_tl == pytest.approx(800.0)
    # Turkish tax on 400 TL of dividends at the 15% bracket is 60 TL: the 800 TL US tax cannot exceed it.
    assert report.dividend_tax_estimate_tl == pytest.approx(60.0)
    assert report.foreign_tax_credit_tl == pytest.approx(60.0)
    assert report.dividend_tax_due_tl == pytest.approx(0.0)
    assert any("treaty cap" in note for note in report.notes)  # 20 USD on 10 USD gross is above 20%


def test_declaration_threshold_flag_above_22000() -> None:
    rows = [_activity("A-DIV-BIG", "DIV", date(2026, 3, 16), 600.0)]
    path_rows = [parse_activity(row, ACCOUNT) for row in rows]
    book = make_book(usd={date(2026, 3, 13): 40.0})
    records = income_records(path_rows, book)
    assert sum(item.tl_amount for item in records) == pytest.approx(24_000.0)
    assert sum(item.tl_amount for item in records) > declaration_threshold_tl(2026)


# -- export ---------------------------------------------------------------------


def test_export_csv_columns_and_summary(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    add_fill(path, fill_id="B1", symbol="QLD", side="buy", when=_utc(date(2025, 3, 10)), quantity=10, price=80.0)
    add_fill(path, fill_id="S1", symbol="QLD", side="sell", when=_utc(date(2026, 2, 12)), quantity=10, price=100.0)
    connection = _writable(path)
    try:
        ensure_table(connection)
        store_activities(connection, [parse_activity(row, ACCOUNT) for row in _dividend_rows()])
        connection.commit()
    finally:
        connection.close()
    book = make_book(
        usd={
            date(2025, 3, 7): 36.0,
            date(2026, 2, 11): 43.0,
            date(2026, 3, 13): 40.0,
            date(2026, 3, 31): 41.0,
            date(2026, 6, 12): 42.0,
        },
        yiufe={"2025-02": 3200.0, "2026-01": 3700.0},
    )
    readonly = reopen(path)
    try:
        report = build_year_report(readonly, ACCOUNT, 2026, book)
    finally:
        readonly.close()

    out = tmp_path / "tax_2026.csv"
    csv_path, summary_path = export_year(report, out)
    assert csv_path == out
    assert summary_path == tmp_path / "tax_2026.summary.json"

    with out.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(COLUMNS)
        rows = list(reader)
    sections = [row["section"] for row in rows]
    assert sections.count("disposal") == 1
    assert sections.count("dividend") == 2
    assert sections.count("withholding") == 1
    assert sections.count("interest") == 1
    disposal = next(row for row in rows if row["section"] == "disposal")
    assert float(disposal["gain_tl"]) == pytest.approx(9_700.0)
    assert disposal["indexation_applied"] == "true"

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["year"] == 2026
    assert summary["net_gain_tl"] == pytest.approx(9_700.0)
    assert summary["dividends"]["withheld_tl"] == pytest.approx(80.0)
    assert summary["dividends"]["net_tl"] == pytest.approx(362.0)
    assert summary["income_declaration_required"] is False


def test_ytd_status_reports_rates_missing_and_daily_line(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    new_ledger(path)
    readonly = reopen(path)
    try:
        status = ytd_status(
            readonly,
            ACCOUNT,
            2026,
            usdtry_path=tmp_path / "missing_usd.csv",
            yiufe_path=tmp_path / "missing_yiufe.csv",
        )
    finally:
        readonly.close()
    assert status["status"] == "rates_missing"
    assert format_tax_ytd(status) == "tax: rates missing"
    assert ytd_status(None, ACCOUNT, 2026)["status"] == "unavailable"


def test_daily_summary_tax_line_format() -> None:
    line = format_tax_ytd({"status": "ok", "year": 2026, "net_gain_tl": 9700.0, "estimated_tax_tl": 1940.0})
    assert line == "tax YTD 2026: net gain +9,700.00 TL, est. tax 1,940.00 TL"


def test_tax_cli_without_subcommand_returns_usage_code(capsys: pytest.CaptureFixture[str]) -> None:
    from parayabuck.cli import main

    # Usage error (64), not 2: exit 2 is reserved for CRITICAL health (D2-6-5, X3).
    assert main(["tax"]) == 64
    assert "usage: parayabuck tax" in capsys.readouterr().err
