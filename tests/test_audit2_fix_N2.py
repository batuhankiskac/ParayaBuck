"""Audit wave 2, D2-4: the dashboard must not show engine or ledger state it does not have.

Display only: none of these tests touch trading gates. The ledger, runtime and
config reads are monkeypatched or point at tmp_path; nothing reads data/.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.runtime_health import RuntimeHealth
from parayabuck.web.services import dashboard as dash

JS = Path(__file__).resolve().parents[1] / "src" / "parayabuck" / "web" / "static" / "js"


def _equity_cfg(enabled: bool = True) -> SimpleNamespace:
    selection = SimpleNamespace(
        enabled=enabled,
        name="equity_leveraged_trend_v1",
        maximum_allocation=1.0,
    )
    return SimpleNamespace(strategy=SimpleNamespace(equity=selection))


# --- D2-4-7: a degraded or warming engine is not "active" -------------------


@pytest.mark.parametrize(
    ("engine_state", "latched", "expected"),
    [
        ("running", False, "active"),
        ("degraded", False, "degraded"),
        ("warming_up", False, "warming"),
        ("halted", False, "halted"),
        ("running", True, "halted"),
    ],
)
def test_market_status_reflects_engine_degradation_and_latch(engine_state: str, latched: bool, expected: str) -> None:
    status = dash._dashboard_market_status(engine_state, True, {}, {}, latched=latched)

    assert status == expected


def test_market_status_keeps_disabled_and_stopped_states() -> None:
    assert dash._dashboard_market_status("degraded", False, {}, {}) == "disabled"
    assert dash._dashboard_market_status("stopped", True, {}, None) == "stopped"


# --- D2-4-1 / D2-4-2: an unknown account is nulls, never a flat book --------


def test_strategy_card_without_account_snapshot_has_no_zero_holdings() -> None:
    card = dash._dashboard_market_card(
        "equity",
        cfg=_equity_cfg(),
        health={},
        account={"source": "unavailable", "equity": 0.0, "positions": []},
        meta={},
        decision=None,
        engine_state="running",
    )

    assert card["positions_count"] is None
    assert card["current_allocation_pct"] is None
    assert card["holdings"] is None


def test_strategy_card_with_account_snapshot_reports_flat_book_as_zero() -> None:
    card = dash._dashboard_market_card(
        "equity",
        cfg=_equity_cfg(),
        health={},
        account={"source": "broker", "equity": 1000.0, "positions": []},
        meta={},
        decision=None,
        engine_state="running",
    )

    assert card["positions_count"] == 0
    assert card["current_allocation_pct"] == pytest.approx(0.0)
    assert card["holdings"] == []


def test_risk_panel_without_account_snapshot_nulls_account_figures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash, "_dashboard_ledger_risk", lambda: ([], None))
    monkeypatch.setattr(dash, "_dashboard_buy_capacity", lambda: {"unavailable": True})

    unavailable = dash._dashboard_risk(None, {}, {"source": "unavailable", "equity": 0.0, "positions": []})
    known = dash._dashboard_risk(None, {}, {"source": "broker", "equity": 1000.0, "cash": 1000.0, "positions": []})

    for key in ("gross_exposure_pct", "cash_pct", "equity_exposure_pct", "external_exposure_pct", "daily_pnl"):
        assert unavailable[key] is None, key
    assert known["cash_pct"] == pytest.approx(100.0)
    assert known["daily_pnl"] == pytest.approx(0.0)


# --- D2-4-3: a failed ledger read is not an empty list ----------------------


def test_ledger_risk_missing_database_is_a_fresh_install_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dash, "_dashboard_account_id", lambda: "acct")
    monkeypatch.setattr(dash, "_dashboard_db_path", lambda: tmp_path / "absent.sqlite3")

    assert dash._dashboard_ledger_risk() == ([], None)


def test_ledger_risk_existing_but_unopenable_database_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    present = tmp_path / "present.sqlite3"
    present.write_bytes(b"")
    monkeypatch.setattr(dash, "_dashboard_account_id", lambda: "acct")
    monkeypatch.setattr(dash, "_dashboard_db_path", lambda: present)
    monkeypatch.setattr(dash, "_dashboard_readonly_connection", lambda: None)

    assert dash._dashboard_ledger_risk() == (None, None)


def test_ledger_risk_read_failure_is_unknown_not_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty_db = tmp_path / "empty.sqlite3"
    sqlite3.connect(empty_db).close()  # no tables: the SELECT raises sqlite3.Error
    monkeypatch.setattr(dash, "_dashboard_account_id", lambda: "acct")
    monkeypatch.setattr(dash, "_dashboard_db_path", lambda: empty_db)

    assert dash._dashboard_ledger_risk() == (None, None)


def test_ledger_risk_readable_database_without_rows_is_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE safety_incidents (id INTEGER PRIMARY KEY, account_id TEXT, reason TEXT, scope TEXT,"
        " activated_at TEXT, updated_at TEXT)"
    )
    conn.execute("CREATE TABLE risk_state (account_id TEXT, session_date TEXT, payload TEXT, updated_at TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(dash, "_dashboard_account_id", lambda: "acct")
    monkeypatch.setattr(dash, "_dashboard_db_path", lambda: db)

    assert dash._dashboard_ledger_risk() == ([], None)


def test_execution_panel_without_ledger_file_has_no_orders_or_fills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dash, "_dashboard_account_id", lambda: "acct")
    monkeypatch.setattr(dash, "_dashboard_db_path", lambda: tmp_path / "absent.sqlite3")

    panel = dash._dashboard_execution()

    assert panel == {"open_orders": [], "recent_fills": []}


def test_execution_panel_read_failure_is_unknown_not_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty_db = tmp_path / "empty.sqlite3"
    sqlite3.connect(empty_db).close()
    monkeypatch.setattr(dash, "_dashboard_account_id", lambda: "acct")
    monkeypatch.setattr(dash, "_dashboard_db_path", lambda: empty_db)

    panel = dash._dashboard_execution()

    assert panel == {"open_orders": None, "recent_fills": None}


# --- D2-4-4: a reconciliation halt behind ownership_drift is shown ----------


def _health_panel(monkeypatch: pytest.MonkeyPatch, health: dict[str, Any] | None) -> dict[str, Any]:
    monkeypatch.setattr(dash, "_manager_engine_state", lambda: "halted")
    monkeypatch.setattr(dash, "_dashboard_restart_count", lambda: 0)
    monkeypatch.setattr(dash, "_dashboard_database_state", lambda: {"exists": False, "size_bytes": None})
    monkeypatch.setattr(dash, "_dashboard_degraded_reasons", lambda _health: [])
    return dash._dashboard_health(health, {"source": "broker"})


def test_reconciliation_tile_sees_halt_hidden_behind_ownership_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    health = {
        "circuit_breaker_reason": "ownership_drift",
        "halt_reasons": ["ownership_drift", "reconciliation_failed"],
    }

    panel = _health_panel(monkeypatch, health)

    assert panel["reconciliation"]["failed"] is True


def test_reconciliation_tile_ignores_unrelated_halts(monkeypatch: pytest.MonkeyPatch) -> None:
    health = {"circuit_breaker_reason": "ownership_drift", "halt_reasons": ["ownership_drift"]}

    panel = _health_panel(monkeypatch, health)

    assert panel["reconciliation"]["failed"] is False


def test_runtime_health_carries_every_halt_reason() -> None:
    names = {item.name for item in fields(RuntimeHealth)}
    assert "halt_reasons" in names

    health = RuntimeHealth(
        state="halted",
        healthy=False,
        circuit_breaker_reason="ownership_drift",
        queue_size=0,
        queue_age_seconds=0.0,
        dropped_events=0,
        coalesced_events=0,
        workers={},
        markets={},
        halt_reasons=("ownership_drift", "reconciliation_failed"),
    )

    assert health.halt_reasons == ("ownership_drift", "reconciliation_failed")


# --- Browser code: pinned fragments (no DOM harness in this suite) ----------


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_api_reads_have_a_timeout_and_writes_do_not() -> None:
    source = _read("api.js")
    body = source[source.index("async function fetchJson") : source.index("global.PBApi")]

    assert "READ_TIMEOUT_MS" in source
    assert "AbortController" in body
    assert "controller.abort()" in source
    assert "clearTimeout" in body
    assert "readJson(await controlFetch(url, opts))" in body


def test_failed_status_poll_moves_the_engine_pill_to_unknown() -> None:
    source = _read("ui.js")
    catch = source[source.index("async function refreshStatus") : source.index("// ---------- engine actions")]

    assert 'renderEngineState("unknown")' in catch
    assert 'state: "unknown"' in catch
    assert 'setText("statusText", STATE_LABELS[shell.state] || "Bilinmiyor")' in source


def test_breaker_codes_are_shown_in_turkish_with_raw_fallback() -> None:
    source = _read("ui.js")

    assert "function breakerLabel(code)" in source
    assert "breakerLabel: breakerLabel" in source
    assert "ownership_drift" in source[source.index("BREAKER_LABELS") : source.index("function breakerLabel")]


def test_trading_state_badges_have_classes_for_block_and_halt_states() -> None:
    source = _read("ui.js")
    badge = source[source.index("function badgeClass") : source.index("// Breaker codes")]

    assert '"circuit_breaker"' in badge
    assert '"market_halted"' in badge
    assert '"entry_blocked"' in badge
    assert '"rebalance_blocked"' in badge


def test_dashboard_maps_trading_state_to_turkish_labels() -> None:
    source = _read("dashboard.js")

    assert 'CIRCUIT_BREAKER: "Güvenlik kilidi devrede"' in source
    assert 'ENTRY_BLOCKED: "Alım kilitli"' in source
    assert "TRADING_STATE_LABELS[tradingState] || tradingState" in source


def test_dashboard_story_does_not_claim_within_limits_while_latched() -> None:
    source = _read("dashboard.js")
    story = source[source.index("function renderStory") : source.index("function ddPct")]

    assert "Risk limitleri içinde." in story
    assert "Yeni alımlar durduruldu." in story
    assert "accountKnown" in story
    assert "latchEntries(risk)" in story


def test_dashboard_story_names_stopping_and_stop_failed() -> None:
    source = _read("dashboard.js")
    story = source[source.index("function renderStory") : source.index("function ddPct")]

    assert 'state === "stopping"' in story
    assert 'state === "stop_failed"' in story
    assert '"unknown"' in story


def test_next_check_in_the_past_reads_as_pending_not_as_a_relative_time() -> None:
    source = _read("dashboard.js")

    assert "function nextCheckText(iso)" in source
    assert '"bekleniyor"' in source
    assert "fmt.relative(card.next_rebalance_at)" not in source


def test_failed_ledger_reads_render_as_unknown_not_as_green_empty() -> None:
    source = _read("dashboard.js")

    assert "Güvenlik olayı kaydı okunamadı" in source
    assert "Emir kaydı okunamadı" in source
    assert "İşlem kaydı okunamadı" in source


def test_latest_decision_failure_is_told_apart_from_no_decision() -> None:
    source = _read("dashboard.js")

    assert "latest.unavailable === true && Boolean(latest.error)" in source


def test_failed_overview_marks_the_freshness_label_stale() -> None:
    source = _read("dashboard.js")
    refresh = source[source.index("async function refreshOverview") : source.index("let opsBusy")]

    assert "markOverviewStale()" in refresh
    assert "güncellenemedi" in source


def test_tax_failure_clears_the_last_figures() -> None:
    source = _read("dashboard.js")
    tax = source[source.index("function renderTax") : source.index("let overviewBusy")]

    assert "clearFigures()" in tax
    assert 'renderTax({ status: "unavailable" })' in source


def test_healthy_label_is_not_shown_without_a_market_feed() -> None:
    source = _read("dashboard.js")

    assert "markets.equity ? markets.equity.stream_healthy !== false : null" in source
    assert 'dataOk === null ? "Bilinmiyor" : dataOk ? "Güncel" : "Gecikme var"' in source


def test_trend_card_names_the_cash_state_before_the_signal() -> None:
    source = _read("dashboard.js")
    trend = source[source.index("function renderTrendStatus") : source.index("// ---------- risk")]

    assert '"Nakitte · sembol yok"' in trend
    assert trend.index("inCash") < trend.index('"Yukarı"')


def test_dashboard_status_labels_include_degraded() -> None:
    source = _read("dashboard.js")

    assert 'degraded: "Kısıtlı çalışıyor"' in source


def test_browser_files_parse_as_javascript() -> None:
    """Syntax check only when a JS runtime is installed; the pins above run everywhere."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node runtime installed")
    for name in ("api.js", "ui.js", "dashboard.js"):
        result = subprocess.run(
            [node, "--check", str(JS / name)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
