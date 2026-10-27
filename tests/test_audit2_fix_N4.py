"""Audit wave 2 fix package N4: operator UX for the Turkish owner (D2-10-10..21).

Behavioural checks run on the Python backtest manager; the UI items are static
contracts on the shipped templates and scripts (no browser or network needed).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from parayabuck.web.managers import engine as engine_module
from parayabuck.web.managers.backtest import BacktestManager

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WEB = _REPO_ROOT / "src" / "parayabuck" / "web"
_TEMPLATES = _WEB / "templates"
_JS = _WEB / "static" / "js"
_RUNBOOK = _REPO_ROOT / "docs" / "operator_runbook.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _FakeThread:
    """Records the start request without running the CLI in-process."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return False


def _manager(tmp_path: Path) -> BacktestManager:
    manager = BacktestManager(engine_state_provider=lambda: "stopped")
    manager._thread_factory = _FakeThread
    manager.reports_root = tmp_path / "reports"
    return manager


# --- D2-10-10: backtest window default and the period shown with the result --


def test_backtest_form_defaults_to_years_not_ninety_days() -> None:
    html = _read(_TEMPLATES / "tools.html")
    assert 'id="btDays" min="0" max="3650" value="0"' in html
    assert "0'dan büyük gün, yıl alanını geçersiz kılar" in html
    script = _read(_JS / "tools.js")
    assert "days: Number.isNaN(days) ? 0 : days," in script
    assert "? 90 : days" not in script


def test_backtest_start_with_days_zero_uses_years_and_records_period(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.start({"market": "equity", "days": 0, "years": 3, "symbols": "SPY"})
    period = manager.status()["period"]
    assert period is not None
    assert period["days"] == 3 * 365
    assert period["start"] < period["end"]


def test_backtest_explicit_days_override_years_and_are_recorded(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.start({"market": "equity", "days": 90, "years": 3})
    assert manager.status()["period"]["days"] == 90


def test_backtest_period_is_none_before_any_run(tmp_path: Path) -> None:
    assert _manager(tmp_path).status()["period"] is None


def test_a_refused_start_keeps_the_previous_runs_period(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.start({"market": "equity", "days": 90})
    previous = manager.status()["period"]
    manager._thread = None
    manager.state = "idle"
    with pytest.raises(ValueError):
        manager.start({"market": "equity", "days": 90, "parameters": {"bad name": 1}})
    # A refused start publishes nothing: the shown period stays the previous
    # run's, alongside its status (S2-6-4).
    assert manager.status()["period"] == previous


def test_summary_renders_the_period_before_the_figures() -> None:
    script = _read(_JS / "tools.js")
    assert '["Dönem", period ? period.start + " – " + period.end' in script
    assert "const period = data.period;" in script


# --- D2-10-11: the live warning describes the real order type and gap risk ----


def test_live_warning_names_market_orders_and_gap_risk_not_stop_limit() -> None:
    html = _read(_TEMPLATES / "base.html")
    warning = html[html.index("Canlı işlem uyarısı") : html.index("Canlı işlem uyarısı") + 400]
    assert "Stop-limit" not in html
    assert "piyasa emri" in warning
    assert "zarar durdur (stop-loss) emri koymaz" in warning
    assert "boşluk" in warning


# --- D2-10-12: "canlı" means live money only; data and workers say something else --


def test_dashboard_and_tools_do_not_use_canli_for_data_or_workers() -> None:
    dashboard = _read(_JS / "dashboard.js")
    tools = _read(_TEMPLATES / "tools.html")
    assert "canlı" not in dashboard
    assert "canlı" not in tools
    assert '"güncel · "' in dashboard
    assert '"Çalışıyor" : "Durdu"' in dashboard


# --- D2-10-13: runbook names the on-screen controls and the arming expiry -----


def test_runbook_live_procedure_states_the_two_minute_arming_expiry() -> None:
    runbook = _read(_RUNBOOK)
    assert "The arming phrase expires 2 minutes after it is issued" in runbook
    assert "`LIVE <suffix>`" in runbook
    assert "Canlıya geç" in runbook
    assert "Başlat" in runbook
    # The arming token lifetime the code really uses.
    assert "timedelta(minutes=2)" in _read(_WEB / "managers" / "engine.py")


def test_runbook_names_the_reset_button_and_the_halt_on_screen() -> None:
    runbook = _read(_RUNBOOK)
    assert "**Kilidi doğrula**" in runbook
    assert '"Güvenlik kilidi devrede"' in runbook


# --- D2-10-14: the reset button is hidden when no safety lock is in force -----


def test_reset_button_only_shown_while_a_breaker_reason_is_present() -> None:
    source = _read(_JS / "ui.js")
    assert "if (resetBtn && !breaker) resetBtn.hidden = true;" in source
    assert source.index("if (resetBtn && !breaker) resetBtn.hidden = true;") < source.index(
        'if (breaker) { messages.push("Güvenlik kilidi devrede ('
    )


# --- D2-10-15: paper OFF has an inline warning next to the switch ------------


def test_paper_off_warning_is_inline_and_follows_the_switch() -> None:
    html = _read(_TEMPLATES / "base.html")
    switch = html.index('id="paperModeCheck"')
    warning = html.index('id="paperOffWarning"')
    assert switch < warning < html.index('id="equityTradingCheck"')
    assert re.search(r'id="paperOffWarning" class="note" hidden>', html)
    script = _read(_JS / "ui.js")
    assert 'paperCheck.addEventListener("change", syncPaperWarning);' in script
    assert "warn.hidden = paperCheck.checked;" in script
    assert "syncPaperWarning();" in script


# --- D2-10-16: the daily-change tile no longer claims a fixed inclusion rule ---


def test_daily_change_tile_states_that_cash_flows_may_change_the_figure() -> None:
    html = _read(_TEMPLATES / "index.html")
    assert "bugünkü yatırma ve çekmeler bu tutarı değiştirebilir" in html
    assert "yatırma ve çekmeler dahil<" not in html


# --- D2-10-17: one name for the halt, Turkish trading and health labels -------


def test_halt_is_named_one_way_across_the_ui() -> None:
    assert engine_module._ENGINE_STATE_TR["halted"] == "güvenlik kilidi devrede"
    for name in ("ui.js", "dashboard.js"):
        source = _read(_JS / name)
        assert "Güvenlik durdurdu" not in source
        assert "Kesici" not in source
    assert "Güvenlik kilidi: " in _read(_JS / "dashboard.js")


def test_trading_state_and_health_enums_are_shown_in_turkish() -> None:
    dashboard = _read(_JS / "dashboard.js")
    assert 'CIRCUIT_BREAKER: "Güvenlik kilidi devrede"' in dashboard
    assert 'UNKNOWN: "Bilinmiyor"' in dashboard
    assert 'DEGRADED: "Kısıtlı"' in dashboard
    assert "TRADING_STATE_LABELS[tradingState] || tradingState" in dashboard


# --- D2-10-18: the owner-facing numbers carry their units and meaning ---------


def test_restart_count_and_slot_counts_carry_units_and_meaning() -> None:
    dashboard = _read(_JS / "dashboard.js")
    html = _read(_TEMPLATES / "index.html")
    assert 'setText("healthRestarts", (h.restart_count || 0) + " kez");' in dashboard
    assert "Açık pozisyon / dolu slot (açık emirler dahil) / en çok pozisyon" in html
    assert "Pozisyon / dolu slot / limit" not in html


# --- D2-10-19: meter level in text, meter semantics, longer error toasts, tabs ---


def test_meter_level_is_written_out_and_exposed_to_assistive_tech() -> None:
    dashboard = _read(_JS / "dashboard.js")
    assert "sınırın %70'ine yaklaştı" in dashboard
    assert '"sınır aşıldı"' in dashboard
    assert 'bar.setAttribute("role", "meter");' in dashboard
    assert 'bar.setAttribute("aria-valuetext", valueText' in dashboard


def test_error_toasts_stay_long_enough_to_read() -> None:
    source = _read(_JS / "ui.js")
    assert 'level === "error" ? 15000 : 4500' in source


def test_period_and_order_tabs_expose_selection_to_assistive_tech() -> None:
    html = _read(_TEMPLATES / "index.html")
    assert html.count('role="tab" aria-selected=') == 8
    assert html.count('role="tabpanel"') == 2
    dashboard = _read(_JS / "dashboard.js")
    assert 't.setAttribute("aria-selected", String(t === tab));' in dashboard
    assert 'b.setAttribute("aria-selected", String(b === btn));' in dashboard


# --- D2-10-20: missing tax status is unknown, and a due declaration names its step ---


def test_missing_tax_threshold_is_unknown_not_a_verified_zero() -> None:
    dashboard = _read(_JS / "dashboard.js")
    assert (
        "const threshold = t.income_declaration_status || (t.income_declaration_required ? "
        '"aşıldı, beyan gerekir" : "veri eksik");' in dashboard
    )
    assert '"aşılmadı"' not in dashboard
    assert "Yıllık gelir beyannamesinde bildirin; son tarih bu ekranda hesaplanmaz." in dashboard


# --- D2-10-21: the equity switch says what happens to a held QLD position -----


def test_equity_switch_explains_a_held_position() -> None:
    html = _read(_TEMPLATES / "base.html")
    switch = html.index('id="equityTradingCheck"')
    note = html.index("Elde tutulan QLD pozisyonunu satmaz", switch)
    assert note > switch
    assert "yeni karar vermez" in html[switch:note]
