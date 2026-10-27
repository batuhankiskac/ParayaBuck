"""Audit wave 2 reports and exports (F2-7 / D2-9-5): regression tests.

Every input is a small synthetic fixture held in memory or under ``tmp_path``.
"""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.backtest import portfolio_report as report_module
from parayabuck.core.models import Bar
from parayabuck.monitoring import receipt as receipt_module
from parayabuck.web.services import dashboard as svc

# ---------------------------------------------------------------------------
# D2-9-5: the REST overview account_view carries fees.
# ---------------------------------------------------------------------------


def _stub_overview(monkeypatch: pytest.MonkeyPatch, account: dict[str, Any]) -> dict[str, Any]:
    monkeypatch.setattr(svc, "_dashboard_config", lambda: None)
    monkeypatch.setattr(svc, "_dashboard_runtime_health", lambda: {})
    monkeypatch.setattr(svc, "_dashboard_account", lambda: account)
    monkeypatch.setattr(svc, "_dashboard_account_id", lambda: "ACCT_K2")
    monkeypatch.setattr(svc, "_dashboard_market_decisions", lambda _account_id: {})
    # Each panel is a separate read; the account view is what this test pins.
    monkeypatch.setattr(svc, "_dashboard_safe_panel", lambda _name, _builder: {})
    return svc._build_dashboard_payload_uncached("stopped")


def test_overview_account_view_carries_fees(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _stub_overview(
        monkeypatch,
        {
            "equity": 1000.0,
            "cash": 500.0,
            "daily_pnl": 3.0,
            "realized_pnl": 2.0,
            "unrealized_pnl": 1.0,
            "fees": 4.25,
            "source": "live",
            "stale": False,
        },
    )
    assert payload["account"]["fees"] == 4.25


def test_overview_account_view_nulls_fees_when_account_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _stub_overview(
        monkeypatch,
        {"equity": 0.0, "fees": 0.0, "source": "unavailable", "stale": True, "account_suffix": ""},
    )
    # The zero fallback is not a balance: fees must not render as 0.00 either.
    assert payload["account"]["fees"] is None


# ---------------------------------------------------------------------------
# F2-7-5: a same-identity retry keeps the first write and says what it dropped.
# ---------------------------------------------------------------------------


def _receipt_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 2,
        "receipt_id": "d1",
        "decision_id": "d1",
        "decision_timestamp": "2026-09-24T00:00:00+00:00",
        "market": "equity",
        "origin": "scheduled",
        "configuration_digest": "digest",
        "account_equity": 100.0,
    }
    payload.update(overrides)
    return payload


def test_same_identity_retry_keeps_first_write_and_logs_dropped_fields(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = receipt_module.write_decision_receipt_v2(_receipt_payload(), tmp_path)
    before = path.read_text(encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=receipt_module.__name__):
        retried = receipt_module.write_decision_receipt_v2(_receipt_payload(account_equity=999.0), tmp_path)
    assert retried == path
    assert path.read_text(encoding="utf-8") == before
    assert "account_equity" in caplog.text
    assert "kept the first write" in caplog.text


def test_identical_retry_is_silent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    receipt_module.write_decision_receipt_v2(_receipt_payload(), tmp_path)
    with caplog.at_level(logging.WARNING, logger=receipt_module.__name__):
        receipt_module.write_decision_receipt_v2(_receipt_payload(), tmp_path)
    assert "kept the first write" not in caplog.text


# ---------------------------------------------------------------------------
# F2-7-9: text cells that a spreadsheet would read as formulas are neutralised.
# ---------------------------------------------------------------------------


def _empty_report(**overrides: Any) -> report_module.PortfolioReport:
    fields: dict[str, Any] = {
        "summary": {},
        "rebalances": (),
        "fills": (),
        "weight_history": (),
        "universe_history": (),
        "monthly_returns": {},
        "yearly_returns": (),
        "regime_performance": {},
        "rolling_beta": (),
        "per_symbol": (),
        "equity_curve": (),
        "drawdowns": (),
    }
    fields.update(overrides)
    return report_module.PortfolioReport(**fields)


def test_csv_export_neutralises_formula_like_symbols(tmp_path: Path) -> None:
    report = _empty_report(
        per_symbol=(
            {
                "symbol": '=HYPERLINK("http://x")',
                "market": "equity",
                "realized_pnl": -1.5,
                "fees": 0.25,
                "final_weight": 0.0,
            },
        ),
    )
    report.write_csvs(str(tmp_path))
    with (tmp_path / "per_symbol.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    symbol, _market, realized, _fees, _weight = rows[1]
    assert symbol == '\'=HYPERLINK("http://x")'
    # Numbers are not text cells: a negative P&L keeps its sign and is not quoted.
    assert realized == "-1.5"


def test_csv_cell_leaves_plain_text_and_numbers_alone() -> None:
    assert report_module._csv_cell("AAPL") == "AAPL"
    assert report_module._csv_cell(-2.0) == -2.0
    assert report_module._csv_cell("@cmd") == "'@cmd"
    assert report_module._csv_cell("+1") == "'+1"


# ---------------------------------------------------------------------------
# F2-7-13: an unusable benchmark anchor is reported as missing, never as 0.0.
# ---------------------------------------------------------------------------


def _bar(day: datetime, close: float) -> Bar:
    return Bar(
        timestamp=day,
        symbol="SPY",
        timeframe="1D",
        open=close,
        high=close,
        low=close,
        close=close,
    )


def test_benchmark_year_after_zero_close_has_no_return() -> None:
    # Bar refuses non-positive prices, so this anchor is fed as a duck-typed
    # stand-in: the helper only reads ``timestamp`` and ``close``.
    returns = report_module._benchmark_yearly_returns(
        [
            SimpleNamespace(timestamp=datetime(2024, 6, 3, tzinfo=UTC), close=0.0),
            SimpleNamespace(timestamp=datetime(2025, 6, 3, tzinfo=UTC), close=10.0),
        ]  # type: ignore[list-item]
    )
    assert returns[2024] is None
    assert returns[2025] is None


def test_benchmark_valid_years_keep_their_returns() -> None:
    returns = report_module._benchmark_yearly_returns(
        [
            _bar(datetime(2024, 1, 2, tzinfo=UTC), 100.0),
            _bar(datetime(2024, 12, 30, tzinfo=UTC), 120.0),
            _bar(datetime(2025, 6, 3, tzinfo=UTC), 132.0),
        ]
    )
    assert returns[2024] == pytest.approx(0.2)
    assert returns[2025] == pytest.approx(0.1)


def test_yearly_benchmark_missing_year_is_none_not_zero() -> None:
    # The caller reads ``.get(year)``: a year without benchmark bars must map to None.
    returns = report_module._benchmark_yearly_returns([_bar(datetime(2024, 1, 2, tzinfo=UTC), 100.0)])
    assert returns.get(2030) is None
