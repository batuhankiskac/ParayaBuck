"""Audit wave 2 fixes X3: tax export and rates, CLI exit codes, health, logging, journal, backtest root.

Each test pins one invariant with a synthetic fixture or ``tmp_path``. Nothing here
reaches the network, credentials or a production state path.
"""

from __future__ import annotations

import argparse
import csv
import inspect
import io
import logging
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.config as config_module
from parayabuck import logger as parayabuck_logger
from parayabuck.config import DEFAULT_LIVE_DECISION_DB_PATH, Config, RuntimeMode, load_config
from parayabuck.execution import portfolio
from parayabuck.live_safety import operator_halt_marker_path, write_operator_halt
from parayabuck.monitoring import health
from parayabuck.monitoring.health import HealthStatus
from parayabuck.monitoring.journal import EventJournal
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.tax import cli as tax_cli
from parayabuck.tax.dividends import IncomeRecord
from parayabuck.tax.errors import TaxDataError
from parayabuck.tax.export import YearReport, export_year, ytd_status
from parayabuck.tax.rates import DEFAULT_USDTRY_PATH, DEFAULT_YIUFE_PATH, fetch_rates_to_files
from parayabuck.web.managers.backtest import BacktestManager, _configured_reports_root

REPO_ROOT = Path(__file__).resolve().parents[1]


def _report(*, income: tuple[IncomeRecord, ...] = (), complete: bool = False) -> YearReport:
    return YearReport(
        year=2026,
        account_id="PA1234567890",
        disposals=(),
        income=income,
        gains_tl=0.0,
        losses_tl=0.0,
        net_gain_tl=0.0,
        gain_tax_base_tl=0.0,
        estimated_tax_tl=0.0,
        dividend_gross_usd=0.0,
        dividend_gross_tl=0.0,
        withholding_usd=0.0,
        withholding_tl=0.0,
        dividend_net_tl=0.0,
        interest_tl=0.0,
        income_threshold_tl=0.0,
        income_declaration_required=False,
        dividend_tax_estimate_tl=0.0,
        interest_tax_estimate_tl=0.0,
        foreign_tax_credit_tl=0.0,
        dividend_tax_due_tl=0.0,
        notes=(),
        income_coverage_complete=complete,
    )


def _dividend(symbol: str = "SPY", activity_id: str = "act-1") -> IncomeRecord:
    return IncomeRecord(
        kind="dividend",
        activity_id=activity_id,
        activity_type="DIV",
        symbol=symbol,
        paid_on=date(2026, 3, 1),
        usd_amount=1.0,
        rate=40.0,
        rate_published_on=date(2026, 2, 27),
        tl_amount=40.0,
    )


# -- F2-7-7: the export names the income-import status ---------------------------


def test_export_reports_a_missing_income_import() -> None:
    assert _report().to_dict()["income_import_status"] == "missing"


def test_export_reports_a_stale_income_import_that_does_not_reach_year_end() -> None:
    assert _report(income=(_dividend(),)).to_dict()["income_import_status"] == "stale"


def test_export_reports_a_complete_income_import() -> None:
    assert _report(income=(_dividend(),), complete=True).to_dict()["income_import_status"] == "complete"


# -- F2-7-9: formula-like CSV cells are neutralised ------------------------------


def test_csv_export_neutralises_formula_cells_and_keeps_numbers(tmp_path: Path) -> None:
    out = tmp_path / "tax_2026.csv"
    export_year(_report(income=(_dividend(symbol="@SUM(A1)", activity_id="=1+1"),)), out)
    with out.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "'@SUM(A1)"
    assert rows[0]["activity_id"] == "'=1+1"
    assert rows[0]["tl_amount"] == "40.0"
    assert rows[0]["section"] == "dividend"


# -- S2-2-1: tax status masks the account id -------------------------------------


def test_ytd_status_masks_the_account_id_on_every_path() -> None:
    status = ytd_status(None, "PA1234567890", 2026)
    assert status["status"] == "unavailable"
    assert status["account_id"] == "***7890"
    assert "PA1234567890" not in str(status)


# -- Q2-5-7 and F2-9-8: rate files are anchored and a stored rate is never overwritten --


def test_default_rate_files_resolve_under_the_checkout_not_the_working_directory() -> None:
    import parayabuck.tax.rates as rates_module

    # A checkout (editable install, the VM) anchors the files to it; an installed
    # wheel without a checkout (CI's site-packages) keeps the relative default.
    in_checkout = REPO_ROOT in Path(rates_module.__file__).resolve().parents
    base = REPO_ROOT if in_checkout else Path()
    assert Path(DEFAULT_USDTRY_PATH) == base / "data" / "tax" / "usdtry.csv"
    assert Path(DEFAULT_YIUFE_PATH) == base / "data" / "tax" / "yiufe.csv"


def _usd_fake_get(usd_rate: str) -> Any:
    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        if "TP.DK.USD.A.YTL" in url:
            return {"items": [{"Tarih": "09-03-2026", "TP_DK_USD_A_YTL": usd_rate}]}
        return {"items": [{"Tarih": "2026-02", "TP_TUFE1YI_T1": "3050.0"}]}

    return fake_get


def test_fetch_refuses_to_overwrite_a_differing_stored_usd_rate(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    yiufe = tmp_path / "yiufe.csv"
    usd.write_text("date,usdtry_forex_buying\n2026-03-09,42.0\n", encoding="utf-8")
    before = usd.read_text(encoding="utf-8")
    with pytest.raises(TaxDataError, match="USD/TRY differs"):
        fetch_rates_to_files(
            date(2026, 3, 1),
            date(2026, 3, 31),
            usdtry_path=usd,
            yiufe_path=yiufe,
            api_key="k",
            http_get=_usd_fake_get("43.1"),
            confirmed_series=True,
        )
    assert usd.read_text(encoding="utf-8") == before
    assert not yiufe.exists()


def test_fetch_keeps_an_agreeing_stored_usd_rate(tmp_path: Path) -> None:
    usd = tmp_path / "usdtry.csv"
    yiufe = tmp_path / "yiufe.csv"
    usd.write_text("date,usdtry_forex_buying\n2026-03-09,43.1\n", encoding="utf-8")
    counts = fetch_rates_to_files(
        date(2026, 3, 1),
        date(2026, 3, 31),
        usdtry_path=usd,
        yiufe_path=yiufe,
        api_key="k",
        http_get=_usd_fake_get("43.1"),
        confirmed_series=True,
    )
    assert counts["usdtry_rows"] == 1


def test_rates_fetch_command_names_the_files_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config_module, "load_env_file", lambda *args, **kwargs: None)
    monkeypatch.setenv("PARAYABUCK_EVDS_KEY", "test-key")
    monkeypatch.setattr(tax_cli, "fetch_rates_to_files", lambda *args, **kwargs: {"usdtry_rows": 1, "yiufe_rows": 1})
    usd = tmp_path / "usdtry.csv"
    yiufe = tmp_path / "yiufe.csv"
    args = argparse.Namespace(
        tax_cmd="rates-fetch",
        start="2026-03-01",
        end="2026-03-31",
        usdtry=str(usd),
        yiufe=str(yiufe),
        confirm_yiufe_series=False,
    )
    assert tax_cli.run_tax(args) == 0
    out = capsys.readouterr().out
    assert str(usd) in out and str(yiufe) in out


# -- S2-5-2 / D2-6-5: ledger identity and CLI exit codes -------------------------


def test_paper_import_refuses_a_symlink_to_the_live_ledger(tmp_path: Path) -> None:
    link = tmp_path / "paper_link.sqlite3"
    link.symlink_to(Path(DEFAULT_LIVE_DECISION_DB_PATH).absolute())
    paper = SimpleNamespace(mode=RuntimeMode.PAPER, decision_db_path=str(link))
    with pytest.raises(TaxDataError, match="LIVE ledger"):
        tax_cli._ledger_for_mode(paper, live=False, writing=True)


def test_paper_import_refuses_an_absolute_spelling_of_the_live_ledger() -> None:
    # The string differs from the relative default, but it names the same file.
    absolute = str(Path(DEFAULT_LIVE_DECISION_DB_PATH).absolute())
    paper = SimpleNamespace(mode=RuntimeMode.PAPER, decision_db_path=absolute)
    with pytest.raises(TaxDataError, match="LIVE ledger"):
        tax_cli._ledger_for_mode(paper, live=False, writing=True)


def test_run_tax_without_a_subcommand_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert tax_cli.run_tax(argparse.Namespace(tax_cmd=None)) == 64
    assert "usage" in capsys.readouterr().err


def test_run_tax_bad_date_is_a_usage_error_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config_module, "load_env_file", lambda *args, **kwargs: None)
    monkeypatch.setenv("PARAYABUCK_EVDS_KEY", "test-key")
    args = argparse.Namespace(
        tax_cmd="rates-fetch",
        start="2026/03/01",
        end=None,
        usdtry="x.csv",
        yiufe="y.csv",
        confirm_yiufe_series=False,
    )
    assert tax_cli.run_tax(args) == 64
    assert "--start must be YYYY-MM-DD" in capsys.readouterr().err


def test_run_tax_unexpected_failure_exits_one_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(_args: argparse.Namespace) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(tax_cli, "_export", broken)
    assert tax_cli.run_tax(argparse.Namespace(tax_cmd="export")) == 1
    assert "RuntimeError: boom" in capsys.readouterr().err


def test_run_tax_database_error_exits_one_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def locked(_args: argparse.Namespace) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(tax_cli, "_export", locked)
    assert tax_cli.run_tax(argparse.Namespace(tax_cmd="export")) == 1
    assert "database is locked" in capsys.readouterr().err


# -- D2-6-3 / D2-6-7: a planned stop is not stale, and the tolerance is passed in ----


def test_a_planned_stop_with_an_old_snapshot_is_blocked_not_critical(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    snapshot = tmp_path / "health_snapshot.json"
    written = (now - timedelta(hours=5)).isoformat()
    snapshot.write_text(f'{{"written_at": "{written}", "runtime_state": "stopped"}}', encoding="utf-8")
    assert health._check_runtime_heartbeat(snapshot, now, 90.0).status == HealthStatus.BLOCKED


def test_an_old_snapshot_of_a_running_engine_stays_critical(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    snapshot = tmp_path / "health_snapshot.json"
    written = (now - timedelta(hours=5)).isoformat()
    snapshot.write_text(f'{{"written_at": "{written}", "runtime_state": "running"}}', encoding="utf-8")
    assert health._check_runtime_heartbeat(snapshot, now, 90.0).status == HealthStatus.CRITICAL


def test_heartbeat_tolerance_is_required_from_the_caller() -> None:
    parameter = inspect.signature(health._check_runtime_heartbeat).parameters["heartbeat_tolerance_s"]
    assert parameter.default is inspect.Parameter.empty


# -- R2-9-9: the operator halt marker is a health row ----------------------------


def test_an_operator_halt_marker_is_reported_as_a_blocked_row(tmp_path: Path) -> None:
    assert health._check_operator_halt(tmp_path).status == HealthStatus.OK
    write_operator_halt(operator_halt_marker_path(tmp_path), reason="fixture halt", user="test")
    check = health._check_operator_halt(tmp_path)
    assert check.code == "operator_halt"
    assert check.status == HealthStatus.BLOCKED
    assert "fixture halt" in check.summary


# -- D2-6-2: a failed journal read can be made visible ---------------------------


def test_journal_query_failure_returns_empty_by_default_and_raises_when_strict(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    journal = EventJournal.open_readonly(missing)
    assert journal.query() == []
    with pytest.raises(sqlite3.Error):
        journal.query(strict=True)
    assert not missing.exists()


# -- D2-6-1: console log lines go to stderr, never to the stdout of a command ---------


def test_console_log_lines_go_to_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    root = logging.getLogger("parayabuck")
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "propagate", root.propagate)
    monkeypatch.setattr(parayabuck_logger, "_configured", False)
    log = parayabuck_logger.get_logger("parayabuck.x3_fixture")
    log.warning("x3 fixture warning")
    assert "x3 fixture warning" in stderr.getvalue()
    assert "x3 fixture warning" not in stdout.getvalue()


# -- T2-10-4: a December calendar fallback is recorded in the decision metadata --


def _decision(month: int) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="s",
        strategy_version="v",
        decision_timestamp=datetime(2026, month, 20, 15, tzinfo=UTC),
        weights=(),
        cash_weight=1.0,
    )


def test_an_unreadable_december_calendar_is_reported_for_the_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(year: int) -> tuple[date, ...]:
        raise RuntimeError("calendar offline")

    monkeypatch.setattr(portfolio, "_cached_december_season_sessions", unavailable)
    assert portfolio.december_calendar_fallback_year(_decision(11).decision_timestamp, 3) == 2026
    assert portfolio.december_calendar_fallback_year(_decision(6).decision_timestamp, 3) is None
    assert portfolio.december_calendar_fallback_year(_decision(11).decision_timestamp, 0) is None


def test_a_readable_december_calendar_reports_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portfolio, "_cached_december_season_sessions", lambda year: (date(year, 12, 1),))
    assert portfolio.december_calendar_fallback_year(_decision(11).decision_timestamp, 3) is None


def test_the_fallback_years_are_attached_once_and_sorted() -> None:
    target = portfolio.record_december_calendar_fallbacks(_decision(11), [2027, 2026, 2027])
    assert target.metadata[portfolio.DECEMBER_CALENDAR_FALLBACK_KEY] == (2026, 2027)
    assert portfolio.record_december_calendar_fallbacks(_decision(11), []).metadata == {}


# -- Q2-5-4: the Web backtest root is never a hard-coded repository path ---------


def test_a_backtest_manager_names_no_report_root_until_a_run_starts() -> None:
    assert BacktestManager().reports_root is None


def test_the_default_backtest_root_is_the_configured_output_dir() -> None:
    assert _configured_reports_root() == Path(load_config().backtest.output_dir)


def test_config_default_output_dir_is_reports() -> None:
    assert Config().backtest.output_dir == "reports"
