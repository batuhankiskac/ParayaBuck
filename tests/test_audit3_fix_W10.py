"""Audit wave 3, W10: tax CLI, rate files, ledger notes and coverage (F3-1-1..6, F3-5-3, F3-5-9, D3-5-10/11, S3-2-8, S3-3-7)."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import parayabuck.config as config_module
import parayabuck.tax.cli as tax_cli
import parayabuck.tax.rates as rates_module
from parayabuck.core.models import Market
from parayabuck.live_safety import credential_env_names
from parayabuck.tax.dividends import coverage_spans_year, ensure_table, record_coverage
from parayabuck.tax.errors import LedgerError
from parayabuck.tax.export import build_year_report, export_year, ytd_status
from parayabuck.tax.ledger import (
    Fill,
    assert_no_ownership_adjustments,
    fee_gap_symbols,
    match_disposals,
    read_fills,
    stream_zero_fee_symbols,
)
from parayabuck.tax.rates import RateBook, fetch_rates_to_files

ACCOUNT = "ACC-123456789"
MASKED = "***6789"


def _rates() -> RateBook:
    days: dict[date, float] = {}
    day = date(2024, 11, 1)
    while day <= date(2027, 1, 31):
        days[day] = 40.0
        day += timedelta(days=1)
    months = {f"{year}-{month:02d}": 3000.0 for year in (2024, 2025, 2026, 2027) for month in range(1, 13)}
    return RateBook(usd_try=days, yiufe=months)


def _runtime_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE portfolio_runtime_sessions (session_id TEXT PRIMARY KEY, account_id TEXT, started_at TEXT);
        CREATE TABLE portfolio_decisions (decision_id TEXT PRIMARY KEY, session_id TEXT);
        CREATE TABLE portfolio_orders (
            client_order_id TEXT PRIMARY KEY, decision_id TEXT, symbol TEXT, side TEXT, market TEXT
        );
        CREATE TABLE portfolio_fills (
            activity_id TEXT PRIMARY KEY, client_order_id TEXT, occurred_at TEXT, quantity REAL,
            price REAL, fee REAL, fee_observed INTEGER
        );
        CREATE TABLE portfolio_execution_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, dedup_identity TEXT,
            client_order_id TEXT, fee_observed INTEGER, UNIQUE(source, dedup_identity)
        );
        """
    )


def _seed_fill(
    connection: sqlite3.Connection,
    fill_id: str,
    side: str,
    when: datetime,
    price: float,
    fee: float,
    *,
    observed: bool,
    journal: tuple[str, ...] = (),
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO portfolio_runtime_sessions VALUES ('S1', ?, '2026-01-01T00:00:00+00:00')", (ACCOUNT,)
    )
    connection.execute("INSERT INTO portfolio_decisions VALUES (?, 'S1')", (f"D-{fill_id}",))
    connection.execute(
        "INSERT INTO portfolio_orders VALUES (?, ?, 'QLD', ?, ?)",
        (f"O-{fill_id}", f"D-{fill_id}", side, Market.EQUITY.value),
    )
    connection.execute(
        "INSERT INTO portfolio_fills VALUES (?, ?, ?, 10.0, ?, ?, ?)",
        (fill_id, f"O-{fill_id}", when.isoformat(), price, fee, int(observed)),
    )
    for source in journal:
        connection.execute(
            "INSERT INTO portfolio_execution_events (source, dedup_identity, client_order_id, fee_observed) "
            "VALUES (?, ?, ?, 0)",
            (source, fill_id, f"O-{fill_id}"),
        )


def _seed_round_trip(connection: sqlite3.Connection, *, buy_journal: tuple[str, ...]) -> None:
    """One 2025 buy with an unobserved fee and one 2026 sell with an observed fee."""
    _runtime_tables(connection)
    _seed_fill(
        connection,
        "B1",
        "buy",
        datetime(2025, 3, 10, 12, tzinfo=UTC),
        100.0,
        0.0,
        observed=False,
        journal=buy_journal,
    )
    _seed_fill(connection, "S1", "sell", datetime(2026, 2, 12, 12, tzinfo=UTC), 100.0, 1.0, observed=True)


def _rate_files(tmp_path: Path) -> tuple[Path, Path]:
    usd = tmp_path / "usdtry.csv"
    yiufe = tmp_path / "yiufe.csv"
    days = [date(2024, 11, 1) + timedelta(days=offset) for offset in range(0, 820)]
    usd.write_text(
        "date,usdtry_forex_buying\n" + "".join(f"{day.isoformat()},40.0\n" for day in days),
        encoding="utf-8",
    )
    months = [f"{year}-{month:02d}" for year in (2024, 2025, 2026, 2027) for month in range(1, 13)]
    yiufe.write_text(
        "month,yiufe_index,source\n" + "".join(f"{month},3000,test\n" for month in months),
        encoding="utf-8",
    )
    return usd, yiufe


def _export_args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "tax_cmd": "export",
        "year": 2026,
        "out": str(tmp_path / "out.csv"),
        "account_id": None,
        "config": None,
        "live": False,
        "usdtry": str(tmp_path / "usdtry.csv"),
        "yiufe": str(tmp_path / "yiufe.csv"),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


# -- F3-1-1: the account id is masked in the ownership-adjustment refusal ---------


def test_ownership_adjustment_refusal_masks_the_account_id_on_the_dashboard_path(tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    _seed_round_trip(connection, buy_journal=())
    connection.execute(
        "CREATE TABLE portfolio_ownership_adjustments (symbol TEXT, account_id TEXT, market TEXT, created_at TEXT)"
    )
    connection.execute(
        "INSERT INTO portfolio_ownership_adjustments VALUES ('QLD', ?, ?, '2026-03-01T00:00:00+00:00')",
        (ACCOUNT, Market.EQUITY.value),
    )
    with pytest.raises(LedgerError) as raised:
        assert_no_ownership_adjustments(connection, ACCOUNT, {"QLD"})
    assert ACCOUNT not in str(raised.value)
    assert MASKED in str(raised.value)

    usd, yiufe = _rate_files(tmp_path)
    status = ytd_status(connection, ACCOUNT, 2026, usdtry_path=usd, yiufe_path=yiufe)
    assert status["status"] == "ledger_error"
    assert ACCOUNT not in str(status)
    connection.close()


# -- F3-1-2: the "unknown" placeholder is never exported as an account -------------


def test_latest_account_skips_the_unknown_placeholder() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE portfolio_runtime_sessions (session_id TEXT, account_id TEXT, started_at TEXT)")
    connection.executemany(
        "INSERT INTO portfolio_runtime_sessions VALUES (?, ?, ?)",
        [("S-OLD", ACCOUNT, "2026-01-01"), ("S-NEW", "unknown", "2026-06-01")],
    )
    assert tax_cli._latest_account(connection) == ACCOUNT
    connection.execute("DELETE FROM portfolio_runtime_sessions WHERE account_id = ?", (ACCOUNT,))
    with pytest.raises(tax_cli.TaxDataError, match="no account found"):
        tax_cli._latest_account(connection)
    connection.close()


def test_export_refuses_an_explicit_unknown_account_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "runtime.sqlite3"
    connection = sqlite3.connect(ledger)
    connection.execute("CREATE TABLE portfolio_runtime_sessions (session_id TEXT, account_id TEXT, started_at TEXT)")
    connection.commit()
    connection.close()
    monkeypatch.setattr(tax_cli, "_database_path", lambda *_a, **_k: ledger)

    code = tax_cli.run_tax(_export_args(tmp_path, account_id="unknown"))

    assert code == 1
    assert "placeholder 'unknown'" in capsys.readouterr().err
    assert not (tmp_path / "out.csv").exists()


# -- F3-1-3: rates-fetch keeps the optional selling column ------------------------


def test_rates_fetch_keeps_the_optional_selling_column(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    yiufe = tmp_path / "yiufe.csv"
    usd.write_text("date,usdtry_forex_buying,usdtry_forex_selling\n2026-01-02,43.10,43.20\n", encoding="utf-8")

    def http_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        if "TP.DK.USD" in url:
            return {
                "items": [
                    {"Tarih": "02-01-2026", "TP_DK_USD_A_YTL": "43.10"},
                    {"Tarih": "05-01-2026", "TP_DK_USD_A_YTL": "43.25"},
                ]
            }
        return {"items": [{"Tarih": "2025-12", "TP_TUFE1YI_T1": "3000"}]}

    counts = fetch_rates_to_files(
        date(2026, 1, 1),
        date(2026, 1, 5),
        usdtry_path=usd,
        yiufe_path=yiufe,
        api_key="test-key",
        http_get=http_get,
        confirmed_series=True,
    )

    assert counts == {"usdtry_rows": 2, "yiufe_rows": 1}
    with usd.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0].keys()) == ["date", "usdtry_forex_buying", "usdtry_forex_selling"]
    assert rows[0] == {"date": "2026-01-02", "usdtry_forex_buying": "43.1", "usdtry_forex_selling": "43.20"}
    assert rows[1]["date"] == "2026-01-05" and rows[1]["usdtry_forex_selling"] == ""


# -- F3-1-4: a 1 January import does not mark 31 December covered -----------------


def test_a_first_of_january_import_does_not_cover_the_previous_year() -> None:
    connection = sqlite3.connect(":memory:")
    ensure_table(connection)

    assert record_coverage(connection, ACCOUNT, date(2026, 1, 1), date(2027, 1, 1)) == date(2026, 12, 30)
    assert coverage_spans_year(connection, ACCOUNT, 2026) is False

    assert record_coverage(connection, ACCOUNT, date(2026, 1, 1), date(2027, 1, 2)) == date(2026, 12, 31)
    assert coverage_spans_year(connection, ACCOUNT, 2026) is True
    connection.close()


# -- F3-1-5: stream fills without a commission are a norm, real gaps stay gaps ----


def _fill(fill_id: str, side: str, *, observed: bool, stream_norm: bool) -> Fill:
    return Fill(
        fill_id=fill_id,
        symbol="QLD",
        side=side,
        occurred_at=datetime(2025, 3, 10, 12, tzinfo=UTC),
        quantity=10.0,
        price=100.0,
        fee=0.0 if not observed else 1.0,
        fee_observed=observed,
        fee_stream_norm=stream_norm,
    )


def test_fee_gap_and_stream_norm_symbols_are_split() -> None:
    buy_norm = _fill("B1", "buy", observed=False, stream_norm=True)
    sell = _fill("S1", "sell", observed=True, stream_norm=False)
    fills = [buy_norm, sell]
    disposals = match_disposals(fills, _rates())
    assert fee_gap_symbols(disposals, fills) == []
    assert stream_zero_fee_symbols(disposals, fills) == ["QLD"]

    buy_gap = _fill("B1", "buy", observed=False, stream_norm=False)
    fills = [buy_gap, sell]
    disposals = match_disposals(fills, _rates())
    assert fee_gap_symbols(disposals, fills) == ["QLD"]
    assert stream_zero_fee_symbols(disposals, fills) == []


def test_read_fills_marks_only_stream_only_unobserved_fees_as_the_norm(tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    _runtime_tables(connection)
    when = datetime(2025, 3, 10, 12, tzinfo=UTC)
    _seed_fill(connection, "STREAM-ONLY", "buy", when, 100.0, 0.0, observed=False, journal=("stream",))
    _seed_fill(connection, "POLL-TOO", "buy", when, 100.0, 0.0, observed=False, journal=("stream", "poll"))
    _seed_fill(connection, "NO-JOURNAL", "buy", when, 100.0, 0.0, observed=False)
    flags = {fill.fill_id: (fill.fee_observed, fill.fee_stream_norm) for fill in read_fills(connection, ACCOUNT)}
    assert flags == {
        "STREAM-ONLY": (False, True),
        "POLL-TOO": (False, False),
        "NO-JOURNAL": (False, False),
    }
    connection.close()


def test_year_report_notes_separate_the_stream_norm_from_a_real_gap() -> None:
    connection = sqlite3.connect(":memory:")
    _seed_round_trip(connection, buy_journal=("stream",))
    norm = build_year_report(connection, ACCOUNT, 2026, _rates())
    assert not any("bazı dolumların" in note for note in norm.notes)
    assert any("akış (stream)" in note and "QLD" in note for note in norm.notes)
    connection.close()

    connection = sqlite3.connect(":memory:")
    _seed_round_trip(connection, buy_journal=("stream", "poll"))
    gap = build_year_report(connection, ACCOUNT, 2026, _rates())
    assert any("bazı dolumların" in note and "QLD" in note for note in gap.notes)
    assert not any("akış (stream)" in note for note in gap.notes)
    connection.close()


# -- F3-1-6 and F3-5-3: a missing ledger is exit 3 and nothing is created ----------


def test_export_with_a_missing_ledger_exits_three_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "absent" / "runtime.sqlite3"
    monkeypatch.setattr(tax_cli, "_database_path", lambda *_a, **_k: ledger)

    assert tax_cli.run_tax(_export_args(tmp_path, account_id=ACCOUNT)) == 3

    err = capsys.readouterr().err
    assert "does not exist" in err and "before the subcommand" in err
    assert not ledger.exists()
    assert not ledger.parent.exists()


# -- F3-5-9: usage errors are checked before the environment ----------------------


def _forbid(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("the environment must not be read before the usage check")


def test_rates_fetch_bad_date_is_usage_error_before_the_key_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config_module, "load_env_file", _forbid)
    monkeypatch.delenv("PARAYABUCK_EVDS_KEY", raising=False)
    args = argparse.Namespace(
        tax_cmd="rates-fetch",
        start="2026/01/01",
        end=None,
        usdtry="x.csv",
        yiufe="y.csv",
        confirm_yiufe_series=False,
    )
    assert tax_cli.run_tax(args) == 64
    assert "--start must be YYYY-MM-DD" in capsys.readouterr().err


def test_import_bad_since_is_usage_error_before_the_environment_and_ledger(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config_module, "load_env_file", _forbid)
    monkeypatch.setattr(tax_cli, "_database_path", _forbid)
    args = argparse.Namespace(tax_cmd="import-activities", config=None, live=False, since="2026/01/01")
    assert tax_cli.run_tax(args) == 64
    assert "--since must be YYYY-MM-DD" in capsys.readouterr().err


# -- D3-5-10 and D3-5-11: hints name the right command, credentials are named ------


def test_live_configuration_refusal_points_to_a_paper_configuration_not_live(tmp_path: Path) -> None:
    live_config = SimpleNamespace(mode=config_module.RuntimeMode.LIVE, decision_db_path=str(tmp_path / "live.sqlite3"))
    with pytest.raises(tax_cli.TaxDataError) as raised:
        tax_cli._ledger_for_mode(live_config, live=False, writing=True)
    message = str(raised.value)
    assert "--live" not in message
    assert "PAPER configuration" in message


def test_import_without_credentials_names_the_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "runtime.sqlite3"
    ledger.touch()
    monkeypatch.setattr(config_module, "load_env_file", lambda *_a, **_k: None)
    monkeypatch.setattr(tax_cli, "_database_path", lambda *_a, **_k: ledger)
    monkeypatch.setattr("parayabuck.live_safety.mode_credentials", lambda live: ("", ""))

    assert tax_cli._import_activities(argparse.Namespace(config=None, live=False, since="2026-01-01")) == 1

    err = capsys.readouterr().err
    key_name, secret_name = credential_env_names(live=False)
    assert key_name in err and secret_name in err
    assert "--live" not in err


# -- S3-2-8: the tax import registers its credentials as secrets ------------------


def test_import_registers_its_key_and_secret_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "runtime.sqlite3"
    ledger.touch()
    registered: list[object] = []

    class _Client:
        def __init__(self, *_a: object, **_k: object) -> None:
            self._client = SimpleNamespace(get=self._get)

        def _get(self, *_a: object, **_k: object) -> None:
            raise RuntimeError("stop after credentials")

        def close(self) -> None:
            return None

    monkeypatch.setattr(config_module, "load_env_file", lambda *_a, **_k: None)
    monkeypatch.setattr(tax_cli, "_database_path", lambda *_a, **_k: ledger)
    monkeypatch.setattr("parayabuck.live_safety.mode_credentials", lambda live: ("KEY-AAAA", "SECRET-BBBB"))
    monkeypatch.setattr("parayabuck.execution.alpaca_client.AlpacaClient", _Client)
    monkeypatch.setattr("parayabuck.monitoring.operational_event.register_secret_text", registered.append)

    with pytest.raises(RuntimeError, match="stop after credentials"):
        tax_cli._import_activities(argparse.Namespace(config=None, live=False, since="2026-01-01"))
    assert registered == ["KEY-AAAA", "SECRET-BBBB"]


# -- S3-3-7: the CSV and the summary are replaced as a pair -----------------------


def _stub_report() -> SimpleNamespace:
    return SimpleNamespace(disposals=(), income=(), to_dict=lambda: {"net_gain_tl": 0.0})


def test_failed_summary_write_restores_the_previous_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "gains.csv"
    out.write_text("OLD-CSV\n", encoding="utf-8")
    real_atomic_write = rates_module.atomic_write

    def failing_summary(path: Path, content: str, mode: int | None = None) -> None:
        if str(path).endswith(".summary.json"):
            raise OSError("disk full")
        real_atomic_write(path, content, mode)

    monkeypatch.setattr(rates_module, "atomic_write", failing_summary)
    with pytest.raises(OSError, match="disk full"):
        export_year(_stub_report(), out)
    assert out.read_text(encoding="utf-8") == "OLD-CSV\n"


def test_failed_summary_write_removes_a_new_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "gains.csv"
    real_atomic_write = rates_module.atomic_write

    def failing_summary(path: Path, content: str, mode: int | None = None) -> None:
        if str(path).endswith(".summary.json"):
            raise OSError("disk full")
        real_atomic_write(path, content, mode)

    monkeypatch.setattr(rates_module, "atomic_write", failing_summary)
    with pytest.raises(OSError, match="disk full"):
        export_year(_stub_report(), out)
    assert not out.exists()


def test_successful_export_writes_both_files(tmp_path: Path) -> None:
    out = tmp_path / "gains.csv"
    csv_path, summary_path = export_year(_stub_report(), out)
    assert csv_path == out
    assert summary_path == tmp_path / "gains.summary.json"
    assert out.read_text(encoding="utf-8").startswith("section,symbol")
    assert '"net_gain_tl": 0.0' in summary_path.read_text(encoding="utf-8")
