"""Audit wave 3 fix package W08: Turkish UI and alert text consistency (D3-2, Q3-4).

Pins the owner-facing Turkish texts of the dashboard, the shell, the settings
drawer, the live checklist and the health checks, and the JS behaviour that
the audit found unpinned (Stop confirmation, control-token header, reset
button rule, Enter in the LIVE phrase field). Offline: no network, no data/.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from parayabuck.monitoring import health
from parayabuck.web.schemas.settings import SettingsPayload
from parayabuck.web.security import _CONTROL_TOKEN_HEADER

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WEB = _REPO_ROOT / "src" / "parayabuck" / "web"
_JS = _WEB / "static" / "js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end, source.index(start))]


# --- D3-2-1: refusals and 422 messages are Turkish ----------------------------


def test_settings_percent_validator_message_is_turkish() -> None:
    with pytest.raises(ValidationError) as caught:
        SettingsPayload(daily_loss_limit_pct=0)
    message = str(caught.value)
    assert "yüzde olarak sonlu bir sayı olmalı" in message
    assert "must be a finite percent" not in message


def test_settings_service_refusals_are_turkish() -> None:
    service = _read(_WEB / "services" / "settings.py")
    assert "Motor çalışırken motor ayarı değiştirilemez" in service
    assert "Stop the bot before saving settings" not in service
    assert "Bot güvenli şekilde durdurulamadı" in service
    assert "Bot could not stop safely" not in service


# --- D3-2-2: live checklist labels and reasons are Turkish --------------------


def test_live_checklist_labels_and_reasons_are_turkish() -> None:
    engine = _read(_WEB / "managers" / "engine.py")
    for text in (
        "Yapılandırma: live.enabled=true",
        "Operatör onay dosyası mevcut",
        "Operatör onay HMAC anahtarı tanımlı",
        "Onay imzası ve süresi geçerli",
        "Onay bu hesabı ve yapılandırmayı bağlıyor",
        "Alpaca CANLI kimlik bilgileri tanımlı",
        "İşlem motoru durmuş",
    ):
        assert text in engine, text
    for english in (
        "Operator approval file present",
        "Trading engine is stopped",
        "Alpaca LIVE credentials configured",
    ):
        assert english not in engine, english


# --- D3-2-3: the paper switch dialog names the real refusal -------------------


def test_paper_switch_dialog_says_the_engine_must_be_stopped() -> None:
    ui = _read(_JS / "ui.js")
    assert "Mod yalnızca motor durmuşken değiştirilebilir." in ui
    assert "Bot demo hesapla çalışmaya devam eder." not in ui


# --- D3-2-4: the equity switch hint says the bot does not start or monitor ----


def test_equity_switch_hint_states_the_engine_does_not_start() -> None:
    html = _read(_WEB / "templates" / "base.html")
    assert "Kapatılırsa bot başlatılmaz: hiçbir strateji kalmadığı için motor çalışmaz" in html
    assert "pozisyon izlenmeye devam eder" not in html


# --- D3-2-5: every breaker code has a label; buy-capacity reasons have their own row


def test_breaker_labels_cover_the_runtime_codes() -> None:
    ui = _read(_JS / "ui.js")
    labels = _between(ui, "const BREAKER_LABELS = {", "// Restart-limit halts")
    for code in (
        "portfolio_runtime_failure",
        "decision_worker_crashed",
        "trade_update_callback_failure",
        "final_market_event_queue_overflow",
        "account_fetch_failures",
    ):
        assert f"{code}:" in labels, code
    assert 'if (/_restart_limit$/.test(text)) return "yeniden başlatma sınırı aşıldı";' in ui


def test_buy_capacity_reasons_have_their_own_row() -> None:
    html = _read(_WEB / "templates" / "index.html")
    dashboard = _read(_JS / "dashboard.js")
    assert '<span>Alım engel nedenleri</span><b id="riskCapacityReasons">' in html
    assert 'setText("riskCapacityReasons", ready ? (reasons || "Yok") : "—");' in dashboard
    assert 'setText("riskAdmission", ready ? ({ complete: "Tamam"' in dashboard
    assert (
        '"riskAdmission", ready ? ({ complete: "Tamam", pending: "Bekleniyor", failed: "Başarısız" }[cap.reconciliation_status] || cap.reconciliation_status || "—") : "—");'
        in dashboard
    )


# --- D3-2-6: known English refusals are translated in the UI ------------------


def test_known_refusals_are_translated_before_the_toast() -> None:
    ui = _read(_JS / "ui.js")
    assert "const err = new Error(translateRefusal(api.detailText(data," in ui
    assert "Operatör durdurma işareti hâlâ var; sıfırlamadan önce sunucuda parayabuck resume çalıştırın." in ui
    assert "Güvenlik kilidi operatör tarafından sıfırlanamaz: " in ui


# --- D3-2-7 / D3-2-8: health summaries and the market-halt action are Turkish --


def test_health_summaries_are_turkish_for_disabled_and_halted_checks() -> None:
    watchdog_off = health._check_intent_watchdog({"intent_watchdog": {"enabled": False}})
    assert watchdog_off.summary == "bekçi kapalı"
    halted = health._check_market_halts({"markets": {"equity": {"entry_halt_reason": "protection_failure"}}})
    assert halted.summary == "alımlar durduruldu: equity=protection_failure"
    assert health._check_market_halts(None).summary == "piyasa verisi yok"


def test_market_halt_action_no_longer_points_to_a_hidden_button() -> None:
    source = _read(_REPO_ROOT / "src" / "parayabuck" / "monitoring" / "health.py")
    assert "Durdurma nedenini giderin. Bu durdurma “Kilidi doğrula” ile açılmaz" in source
    assert "ardından “Kilidi doğrula” ile kilit sistem kontrolünden sonra açılır" not in source


# --- D3-2-9 / D3-2-10 / D3-2-15: toasts and the LIVE wording -------------------


def test_stop_toast_follows_the_server_answer() -> None:
    ui = _read(_JS / "ui.js")
    assert 'if (result && result.status === "stopping") toast(' in ui
    assert "Motor durduruluyor; devam eden başlatma emir göndermeden iptal edilecek." in ui


def test_restart_toast_names_every_restart_only_setting() -> None:
    ui = _read(_JS / "ui.js")
    assert (
        "Adres, port, tarayıcı açma ve log seviyesi değişiklikleri uygulamanın bir sonraki açılışında etkinleşir." in ui
    )


def test_live_confirmation_does_not_call_sent_orders_reversible() -> None:
    ui = _read(_JS / "ui.js")
    assert "Gönderilen emirler geri alınamaz ve canlı paraya dokunur." in ui
    assert "geri alınabilir ama canlı paraya dokunur" not in ui


# --- D3-2-11 / D3-2-12 / D3-2-13: dashboard tokens and terms ------------------


def test_dashboard_maps_raw_tokens_to_turkish() -> None:
    dashboard = _read(_JS / "dashboard.js")
    assert 'equity_leveraged_trend_v1: "Kaldıraçlı endeks trendi v1"' in dashboard
    assert 'broker: "broker", cache: "önbellek", unavailable: "yok"' in dashboard
    assert 'static: "Sabit liste", disabled: "Kapalı"' in dashboard
    assert 'operator_acquisition: "operatör alımı"' in dashboard
    assert "STRATEGY_NAMES[lin.strategy] || lin.strategy" in dashboard
    assert "STRATEGY_NAMES[card.name] || strategy.display_name" in dashboard


def test_event_feed_shows_turkish_kind_labels_and_keeps_message_as_tooltip() -> None:
    dashboard = _read(_JS / "dashboard.js")
    assert 'runtime_started: "Motor başladı"' in dashboard
    assert 'const label = EVENT_KIND_LABELS[ev.kind] || ev.message || ev.kind || "olay";' in dashboard
    assert "title: ev.message || ev.kind || null" in dashboard


def test_health_panel_uses_turkish_status_and_check_titles() -> None:
    dashboard = _read(_JS / "dashboard.js")
    assert 'OK: "Tamam", DEGRADED: "Kısıtlı", BLOCKED: "Engelli", CRITICAL: "Kritik"' in dashboard
    assert 'const statusText = HEALTH_STATUS_LABELS[String(c.status || "").toUpperCase()] || c.status;' in dashboard
    assert "text: CHECK_TITLES[c.code] || c.code" in dashboard
    assert 'intent_watchdog: "Niyet bekçisi"' in dashboard


def test_tools_comparison_uses_the_dashboard_term() -> None:
    tools = _read(_JS / "tools.js")
    assert '["Kıyas (" + (s.benchmark_symbol || "—") + ")"' in tools
    assert '"Benchmark ("' not in tools


# --- D3-2-14: cosmetic and network-error texts ---------------------------------


def test_network_failures_are_shown_in_turkish_not_as_failed_to_fetch() -> None:
    api = _read(_JS / "api.js")
    assert 'throw new Error("Sunucuya ulaşılamadı; bağlantıyı kontrol edin");' in api
    assert "return await fetch(url, init);" in api
    assert (
        'readJson(await send(url, { cache: "no-store", signal: controller ? controller.signal : undefined }));' in api
    )
    assert "Failed to fetch" not in api


def test_validator_prefix_is_removed_from_operator_text() -> None:
    api = _read(_JS / "api.js")
    assert 'String(item.msg).replace(/^Value error, /, "")' in api


def test_banner_has_no_literal_backticks() -> None:
    ui = _read(_JS / "ui.js")
    banner = _between(ui, "function renderStatus(status)", "let statusInFlight")
    assert "`" not in banner


# --- D3-5-7: the ownership-drift hint names the rebase order -------------------


def test_ownership_drift_banner_names_the_rebase_order() -> None:
    ui = _read(_JS / "ui.js")
    assert (
        'if (breaker === "ownership_drift") text += " Sahiplik uyuşmazlığında sıra: motoru durdurun, sunucuda parayabuck rebase-ownership çalıştırın, motoru başlatın, sonra “Kilidi doğrula”ya basın.";'
        in ui
    )


# --- Q3-4-1 / Q3-4-8: catch-up label prefix and missing keys -------------------


def test_recovered_session_label_slices_by_the_prefix_length() -> None:
    dashboard = _read(_JS / "dashboard.js")
    assert 'const RECOVERED_PREFIX = "recovered_session:";' in dashboard
    assert "CATCH_UP[status.slice(RECOVERED_PREFIX.length)] || status.slice(RECOVERED_PREFIX.length)" in dashboard
    assert "status.slice(19)" not in dashboard


def test_catch_up_labels_cover_the_nested_statuses() -> None:
    dashboard = _read(_JS / "dashboard.js")
    catch_up = _between(dashboard, "const CATCH_UP = {", "const RECOVERED_PREFIX")
    for status in ("buy_phase_submitted", "replan_order_in_flight", "signal_cross_check_pending"):
        assert f"{status}:" in catch_up, status


# --- Q3-4-2: the mode is known before Start, and the LIVE gate is pinned -------


def test_shell_mode_starts_unknown_and_start_waits_for_it() -> None:
    ui = _read(_JS / "ui.js")
    assert "const shell = { paper: null," in ui
    assert "if (shell.paper === null) start.disabled = true;" in ui


def test_live_start_gate_pins_the_full_statement() -> None:
    ui = _read(_JS / "ui.js")
    assert "if (!shell.paper && !(await confirmLiveStart())) return;" in ui


# --- Q3-4-3: the Stop confirmation gates the request ---------------------------


def test_stop_request_is_sent_only_after_confirmation() -> None:
    ui = _read(_JS / "ui.js")
    handler = _between(ui, 'if (stop) stop.addEventListener("click"', 'if (reset) reset.addEventListener("click"')
    confirm_at = handler.index("const ok = await dialog.confirm({")
    gate_at = handler.index("if (!ok) return;")
    send_at = handler.index('postControl("/api/engine/stop")')
    assert confirm_at < gate_at < send_at


# --- Q3-4-4: the JS control-token header matches the server's header -----------


def test_js_control_token_header_matches_the_server_header() -> None:
    api = _read(_JS / "api.js")
    assert '{ "X-ParayaBuck-Control-Token": controlToken }' in api
    assert "X-ParayaBuck-Control-Token".lower() == _CONTROL_TOKEN_HEADER


# --- Q3-4-5: one rule shows the reset button ----------------------------------


def test_reset_button_visibility_has_one_rule() -> None:
    ui = _read(_JS / "ui.js")
    assert "if (reset) reset.hidden = !shell.breaker;" in ui
    assert 'shell.state !== "halted" && shell.state !== "degraded"' not in ui
    assert "if (resetBtn && !breaker) resetBtn.hidden = true;" not in ui


# --- Q3-4-6: the operator-halt banner text is pinned ---------------------------


def test_operator_halt_banner_text_is_pinned() -> None:
    ui = _read(_JS / "ui.js")
    assert (
        'Operatör durdurması etkin (sebep: " + (shell.operatorHalt.reason || "belirtilmemiş") + "). '
        "Motor başlatılamaz; kaldırmak için sunucuda parayabuck resume komutunu çalıştırın."
    ) in ui


# --- Q3-4-7: Enter in the LIVE phrase field confirms, not cancels --------------


def test_enter_in_the_phrase_field_confirms_the_dialog() -> None:
    ui = _read(_JS / "ui.js")
    assert 'if (opts.input && e.key === "Enter") { e.preventDefault(); ok.click(); }' in ui
