"""Audit wave 2 fix package N1: D2-3 status observability and D2-9 performance chart.

Covers the BUY-hold alerts and journal rows, the per-decision alert key, the
scheduled-path status, the closed-unresolved health check, the no-trade
vocabulary and ``ops explain`` output, the dividend-adjusted benchmark request,
and the chart labels. Offline: fake brokers, fake clients and tmp_path ledgers.
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.data import historical
from alpaca.data.enums import Adjustment

from parayabuck.cli import main as cli_main
from parayabuck.monitoring import alerts, health
from parayabuck.monitoring.forensics import find_closed_unresolved_rebalances, map_no_trade_reason
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.runtime_models import PortfolioRuntimeResult
from parayabuck.web.services import performance
from tests.test_audit_fix_W06 import _NEXT_MONTH, _bars, _phase, _sell_submitted

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DASHBOARD_JS = _REPO_ROOT / "src" / "parayabuck" / "web" / "static" / "js" / "dashboard.js"
_ABSENT = "/nonexistent/parayabuck-test/settings.yaml"
_T0 = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Capture operator alerts and no-trade journal rows instead of emitting them."""
    alert_calls: list[dict[str, Any]] = []
    journal_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(alerts, "emit", lambda kind, *, message, **fields: alert_calls.append({"kind": kind, **fields}))
    monkeypatch.setattr(runtime_service, "_emit_no_trade_safe", lambda **fields: journal_calls.append(fields))
    return SimpleNamespace(alerts=alert_calls, journal=journal_calls)


# ---------------------------------------------------------------- D2-3-1 / D2-3-2 / D2-3-3


def test_scheduled_decision_reports_the_held_advance_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D2-3-1: an open rebalance returns the advance outcome, not a fixed token."""
    runtime, _repository, _broker, first, _sell = _sell_submitted(tmp_path)
    held = PortfolioRuntimeResult(first.decision_id, None, None, (), (), "stale_decision_buy_held")
    monkeypatch.setattr(runtime, "advance_rebalance", lambda **_kwargs: held)
    monkeypatch.setattr(runtime, "_stale_rebalance_decision", lambda _timestamp: None)

    result = runtime.process_closed_bars(_bars(_NEXT_MONTH), force_scheduled=True)

    assert result.status == "stale_decision_buy_held"
    assert result.decision_id == first.decision_id


def test_buy_side_hold_alerts_once_per_decision_and_status(tmp_path: Path, recorded: SimpleNamespace) -> None:
    """D2-3-2: each BUY-hold outcome is alerted and journaled once per decision."""
    runtime, *_ = _sell_submitted(tmp_path)

    runtime._alert_buy_side_held("d1", "rebalance_waiting_for_slot")
    runtime._alert_buy_side_held("d1", "rebalance_waiting_for_slot")
    runtime._alert_buy_side_held("d2", "rebalance_waiting_for_slot")
    runtime._alert_buy_side_held("d1", "replan_snapshot_stale")

    assert [(call["decision_id"], call["category"]) for call in recorded.alerts] == [
        ("d1", "rebalance_waiting_for_slot"),
        ("d2", "rebalance_waiting_for_slot"),
        ("d1", "replan_snapshot_stale"),
    ]
    # The no-trade row has no decision field; the decision is carried by the alert's journal entry.
    assert [row["reason"] for row in recorded.journal] == [
        "rebalance_waiting_for_slot",
        "rebalance_waiting_for_slot",
        "replan_snapshot_stale",
    ]


def test_closure_without_buys_alerts_and_keeps_the_complete_status(tmp_path: Path, recorded: SimpleNamespace) -> None:
    """D2-3-2: a rebalance closed with an unsettled SELL leg is alerted, not silent."""
    runtime, repository, _broker, first, _sell = _sell_submitted(tmp_path)

    result = runtime._close_unsettled_sell_legs(first.decision_id, ["AAPL"])

    assert result.status == "rebalance_complete"
    assert _phase(repository, first.decision_id) == ("complete", "closed_unresolved")
    closures = [call for call in recorded.alerts if call["category"] == "closed_unresolved"]
    assert [call["decision_id"] for call in closures] == [first.decision_id]


def test_held_sell_leg_alert_is_keyed_by_decision(tmp_path: Path, recorded: SimpleNamespace) -> None:
    """D2-3-3: a recurring SELL-leg episode in a later decision is alerted again."""
    runtime, *_ = _sell_submitted(tmp_path)

    runtime._hold_unsettled_sell_leg("d1", "AAPL", "replan_sell_dropped")
    runtime._hold_unsettled_sell_leg("d1", "AAPL", "replan_sell_dropped")
    runtime._hold_unsettled_sell_leg("d2", "AAPL", "replan_sell_dropped")

    assert [call["decision_id"] for call in recorded.alerts] == ["d1", "d2"]


def test_closed_unresolved_rebalance_is_a_health_degradation(tmp_path: Path) -> None:
    """D2-3-2: the closure reaches the execution health check (ops and doctor)."""
    runtime, _repository, _broker, first, _sell = _sell_submitted(tmp_path)
    runtime._close_unsettled_sell_legs(first.decision_id, ["AAPL"])

    check = health._check_execution(str(tmp_path / "runtime.sqlite3"))

    assert check.status == health.HealthStatus.DEGRADED
    assert "closed with an unsettled SELL leg" in check.summary


def _rebalance_db(path: Path, rows: list[tuple[str, str, str, str, str]]) -> Path:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE portfolio_rebalance_executions (decision_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, "
            "market TEXT NOT NULL, phase TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', "
            "buy_orders_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO portfolio_rebalance_executions VALUES (?, 'acct', ?, ?, ?, '[]', ?)",
            [(decision, market, phase, status, stamp) for decision, market, phase, status, stamp in rows],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_closed_unresolved_query_reports_only_recent_equity_closures(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    db = _rebalance_db(
        tmp_path / "ledger.sqlite3",
        [
            ("d-recent", "equity", "complete", "closed_unresolved", (now - timedelta(hours=1)).isoformat()),
            ("d-old", "equity", "complete", "closed_unresolved", (now - timedelta(days=2)).isoformat()),
            ("d-clean", "equity", "complete", "active", (now - timedelta(hours=1)).isoformat()),
            ("d-other", "crypto", "complete", "closed_unresolved", (now - timedelta(hours=1)).isoformat()),
        ],
    )

    rows = find_closed_unresolved_rebalances(db, strict=True)

    assert [row["decision_id"] for row in rows] == ["d-recent"]


# ---------------------------------------------------------------- D2-3-8


@pytest.mark.parametrize(
    ("token", "canonical"),
    [
        ("rebalance_in_progress", "REBALANCE_IN_PROGRESS"),
        ("rebalance_blocked", "REBALANCE_BLOCKED"),
        ("rebalance_waiting_for_slot", "REBALANCE_WAITING_FOR_SLOT"),
        ("replan_snapshot_stale", "ACCOUNT_STALE"),
        ("stale_decision_buy_held", "STALE_DECISION_BUY_HELD"),
        ("submit_blocked", "SUBMIT_BLOCKED"),
        ("replan_sell_submitted", "SELL_PHASE_PENDING"),
        ("closed_unresolved", "REBALANCE_CLOSED_UNRESOLVED"),
    ],
)
def test_rebalance_hold_tokens_map_to_the_canonical_vocabulary(token: str, canonical: str) -> None:
    assert map_no_trade_reason(token) == canonical


def test_ops_explain_no_trade_prints_the_rebalance_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D2-3-8: the NO TRADE branch names the rebalance phase it read."""
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            CREATE TABLE portfolio_decisions (decision_id TEXT PRIMARY KEY, session_id TEXT, account_id TEXT,
                configuration_digest TEXT, decision_timestamp TEXT, market TEXT, strategy_name TEXT,
                target_json TEXT, risk_json TEXT);
            CREATE TABLE portfolio_orders (client_order_id TEXT PRIMARY KEY, decision_id TEXT, symbol TEXT,
                side TEXT, quantity REAL, status TEXT);
            CREATE TABLE portfolio_rebalance_executions (decision_id TEXT PRIMARY KEY, account_id TEXT,
                market TEXT, phase TEXT, status TEXT, buy_orders_json TEXT, updated_at TEXT);
            INSERT INTO portfolio_decisions VALUES ('d1', 's1', 'acct', 'digest', '2026-10-27T09:00:00+00:00',
                'equity', 'vt35', '{}', '{}');
            INSERT INTO portfolio_rebalance_executions VALUES ('d1', 'acct', 'equity', 'complete',
                'closed_unresolved', '[]', '2026-10-27T10:00:00+00:00');
            """
        )
        conn.commit()
    finally:
        conn.close()
    # The health read is not under test; stub it so no snapshot file is touched.
    monkeypatch.setattr(health, "evaluate_health", lambda **_kwargs: {"checks": []})

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli_main(
            [
                "--config",
                _ABSENT,
                "ops",
                "explain",
                "d1",
                "--db",
                str(db),
                "--journal",
                str(tmp_path / "absent-journal.sqlite3"),
            ]
        )

    text = out.getvalue()
    assert code == 0
    assert "NO TRADE" in text
    assert "Rebalance phase: complete" in text
    assert "decision_summary (none)" in text


# ---------------------------------------------------------------- D2-9-1 / D2-9-2 / D2-9-3


class _BarsClient:
    requests: list[Any] = []

    def __init__(self, key: str, secret: str) -> None:
        self.key = key

    def get_stock_bars(self, request: Any) -> Any:
        _BarsClient.requests.append(request)
        return SimpleNamespace(data={"VOO": [SimpleNamespace(timestamp=_T0, close=500.0)]})


def test_benchmark_bars_request_dividend_adjusted_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2-9-2: the live benchmark is a total return, matching the backtest."""
    _BarsClient.requests = []
    monkeypatch.setattr(historical, "StockHistoricalDataClient", _BarsClient)
    manager = SimpleNamespace(get_credentials=lambda: ("key", "secret"))

    points = performance.fetch_benchmark_bars(manager, _T0, _T0 + timedelta(days=1), 1440)

    (request,) = _BarsClient.requests
    assert request.adjustment == Adjustment.ALL
    assert request.symbol_or_symbols == performance.BENCHMARK_SYMBOL == "VOO"
    assert points == [(_T0, 500.0)]


def test_risk_panel_reports_the_external_share_of_equity_exposure(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2-9-7: the risk panel exposes the shares the bot did not open, so the two exposure figures reconcile."""
    from parayabuck.web.services import dashboard as dashboard_service

    # Ledger and capacity reads are stubbed so the test never touches data/.
    monkeypatch.setattr(dashboard_service, "_dashboard_ledger_risk", lambda: ([], None))
    monkeypatch.setattr(dashboard_service, "_dashboard_buy_capacity", lambda: None)
    account = {
        "equity": 1000.0,
        "cash": 0.0,
        "gross_exposure": 1000.0,
        "positions": [
            {"market": "equity", "symbol": "QLD", "qty": 10, "total_qty": 10, "market_value": 800.0, "managed_qty": 5},
            {"market": "equity", "symbol": "SPY", "qty": 2, "total_qty": 2, "market_value": 200.0, "managed_qty": 0},
        ],
    }

    risk = dashboard_service._dashboard_risk(None, None, account)

    assert risk["equity_exposure_pct"] == pytest.approx(100.0)
    assert risk["external_exposure_pct"] == pytest.approx(60.0)


def test_dashboard_chart_labels_deposits_and_names_the_benchmark() -> None:
    """D2-9-1 and D2-9-3: the portfolio line says deposits are included and the legend names the benchmark."""
    js = _DASHBOARD_JS.read_text(encoding="utf-8")

    assert "yatırma ve çekmeler dahildir. Bu bir performans karşılaştırması değildir." in js
    assert 'data.benchmark_symbol || "VOO"' in js
    assert 'h.cls === "main" ? "Portföy" : benchSymbol' in js


def test_dashboard_catch_up_labels_cover_the_rebalance_hold_statuses() -> None:
    """D2-3-1: the catch-up label never shows a raw token for a held rebalance."""
    js = _DASHBOARD_JS.read_text(encoding="utf-8")

    for token in (
        "replan_snapshot_stale",
        "rebalance_waiting_for_slot",
        "stale_decision_buy_held",
        "submit_blocked",
        "replan_sell_submitted",
        "closed_unresolved",
    ):
        assert f"{token}: " in js
