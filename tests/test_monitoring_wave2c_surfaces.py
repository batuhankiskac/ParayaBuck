"""Audit wave 2c — operator health surfaces must show what the ENGINE reports.

Three findings, one invariant: ``ops status``, ``ops doctor`` (and its exit
code), the dashboard badge and ``/api/ops/status`` run in processes other than
the engine, so they may only report state the engine itself published.

* F22 — an active intent-watchdog alarm (and the signal cross-check state)
  must appear on all four surfaces, not only on ``/readyz``.
* F23 — alert/webhook and journal state must come from the snapshot the engine
  persists; with no such copy the answer is UNKNOWN, never OK.
* dashboard badge — a runtime that is alive and "healthy" while a session went
  undecided must not read "Sağlıklı".

All offline: synthetic snapshots under ``tmp_path``, no network, no engine.
"""

from __future__ import annotations

import contextlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.config import Config, ObservabilityConfig, RuntimeConfig
from parayabuck.monitoring.diagnostic import _run_doctor_checks
from parayabuck.monitoring.health import HealthStatus, _check_alerts, evaluate_health
from parayabuck.monitoring.snapshot import write_snapshot_atomic

_NOW = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)

_WORKING_WEBHOOK: dict[str, Any] = {
    "webhook_configured": True,
    "webhook_attempted": 4,
    "webhook_succeeded": 4,
    "webhook_failed": 0,
    "webhook_dropped": 0,
    "webhook_last_success_at": _NOW.isoformat(),
}
_HEALTHY_JOURNAL: dict[str, Any] = {
    "event_journal_written": 42,
    "event_journal_failed": 0,
    "event_journal_dropped": 0,
}


def _runtime_health(
    *,
    alarms: list[str] | None = None,
    cross_check_last: dict[str, Any] | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "state": "running",
        "healthy": True,
        "workers": {},
        "markets": {"equity": {"enabled": True, "symbol_count": 1, "entry_halt_reason": None}},
        "last_reconciliation_at": _NOW.isoformat(),
        "entry_risk_latch": {"equity": None},
        "signal_adjustment": {"healthy": True, "stale": False},
        "intent_watchdog": {
            "enabled": True,
            "evaluated_at": evaluated_at if evaluated_at is not None else _NOW.isoformat(),
            "alarms": [{"code": code, "message": code} for code in (alarms or [])],
            "degraded_reasons": list(alarms or []),
        },
        "signal_cross_check": {
            "enabled": True,
            "pending": {},
            "last": (
                cross_check_last if cross_check_last is not None else {"status": "agree", "session_date": "2026-09-29"}
            ),
        },
    }


def _snapshot(
    directory: Path,
    *,
    runtime_health: dict[str, Any] | None = None,
    alerts_state: dict[str, Any] | None = None,
    journal_state: dict[str, Any] | None = None,
    persist_alerts: bool = True,
    persist_journal: bool = True,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "written_at": _NOW.isoformat(),
        "pid": 4242,
        "session_id": "sess-1",
        "mode": "paper",
        "runtime_state": "running",
        "runtime_health": runtime_health if runtime_health is not None else _runtime_health(),
    }
    if persist_journal:
        payload["observability"] = journal_state if journal_state is not None else _HEALTHY_JOURNAL
    if persist_alerts:
        payload["alerts"] = alerts_state if alerts_state is not None else _WORKING_WEBHOOK
    path = directory / "health_snapshot.json"
    write_snapshot_atomic(path, payload)
    return path


def _check(payload: dict[str, Any], code: str) -> dict[str, Any]:
    return next(c for c in payload["checks"] if c["code"] == code)


# --------------------------------------------------------------------------- #
# F23 — alert state comes from the engine's persisted copy, not this process
# --------------------------------------------------------------------------- #


def test_no_persisted_alert_copy_is_unknown_never_ok() -> None:
    # The pre-fix check read THIS process's counters, which in a CLI or web
    # process are all zero, and answered "webhook OK".
    check = _check_alerts(None, None, mode="paper", now=_NOW)

    assert check.status is HealthStatus.UNKNOWN
    assert "OK" not in check.summary


def test_snapshot_without_alert_block_degrades_to_unknown(tmp_path: Path) -> None:
    path = _snapshot(tmp_path, persist_alerts=False)

    health = evaluate_health(snapshot_path=path, database_path=tmp_path / "none.db", now=_NOW)

    assert _check(health, "alerts")["status"] == "UNKNOWN"
    assert _check(health, "observability")["status"] == "OK"


def test_missing_webhook_in_paper_is_degraded(tmp_path: Path) -> None:
    """A VM with no webhook configured sends alerts nowhere; the deploy check
    used to print 'webhook OK' anyway."""
    path = _snapshot(tmp_path, alerts_state=dict(_WORKING_WEBHOOK, webhook_configured=False))

    health = evaluate_health(snapshot_path=path, database_path=tmp_path / "none.db", now=_NOW)
    alerts_check = _check(health, "alerts")

    assert alerts_check["status"] == "DEGRADED"
    assert "no alert webhook configured in paper" in alerts_check["summary"]
    assert health["overall_health"] == "DEGRADED"


def test_failing_webhook_with_a_stale_last_success_is_degraded(tmp_path: Path) -> None:
    state = dict(
        _WORKING_WEBHOOK,
        webhook_attempted=9,
        webhook_succeeded=1,
        webhook_failed=8,
        webhook_last_success_at=(_NOW - timedelta(days=3)).isoformat(),
    )
    path = _snapshot(tmp_path, alerts_state=state)

    alerts_check = _check(evaluate_health(snapshot_path=path, now=_NOW), "alerts")

    assert alerts_check["status"] == "DEGRADED"
    assert "failing 8/9" in alerts_check["summary"]
    assert "last success" in alerts_check["summary"]


def test_dropped_webhooks_are_read_from_the_engine_copy(tmp_path: Path) -> None:
    # Throttling is by design; a dropped delivery is not.
    path = _snapshot(
        tmp_path,
        alerts_state=dict(_WORKING_WEBHOOK, webhook_dropped=2, suppressed=7),
        journal_state=dict(_HEALTHY_JOURNAL, event_journal_dropped=0),
    )

    alerts_check = _check(evaluate_health(snapshot_path=path, now=_NOW), "alerts")

    assert alerts_check["status"] == "DEGRADED"
    assert alerts_check["summary"] == "alerts dropped 2"


def test_working_webhook_from_the_engine_is_ok(tmp_path: Path) -> None:
    health = evaluate_health(snapshot_path=_snapshot(tmp_path), now=_NOW)

    assert _check(health, "alerts")["status"] == "OK"
    assert _check(health, "observability")["status"] == "OK"


# --------------------------------------------------------------------------- #
# F22 — the intent watchdog is a health check on every surface
# --------------------------------------------------------------------------- #


def test_missing_equity_decision_is_critical_on_evaluate_health(tmp_path: Path) -> None:
    """2026-09-29: no decision persisted for a session while the runtime stayed
    alive and green. The health surface must not say OK."""
    path = _snapshot(tmp_path, runtime_health=_runtime_health(alarms=["equity_decision_missing"]))

    health = evaluate_health(snapshot_path=path, now=_NOW)

    assert _check(health, "intent_watchdog")["status"] == "CRITICAL"
    assert health["overall_health"] == "CRITICAL"


def test_other_watchdog_alarms_degrade_without_gating_trading(tmp_path: Path) -> None:
    """Observability only: an active alarm must not change what trading is
    allowed to do, so the trading state equals the same runtime without it."""
    alarmed = _snapshot(tmp_path / "alarmed", runtime_health=_runtime_health(alarms=["equity_position_over_target"]))
    quiet = _snapshot(tmp_path / "quiet", runtime_health=_runtime_health())

    health = evaluate_health(snapshot_path=alarmed, now=_NOW)

    assert _check(health, "intent_watchdog")["status"] == "DEGRADED"
    assert health["trading_state"] == evaluate_health(snapshot_path=quiet, now=_NOW)["trading_state"]


def test_unevaluated_watchdog_is_unknown(tmp_path: Path) -> None:
    path = _snapshot(tmp_path, runtime_health=_runtime_health(evaluated_at=""))

    assert _check(evaluate_health(snapshot_path=path, now=_NOW), "intent_watchdog")["status"] == "UNKNOWN"


def test_signal_cross_check_hold_is_degraded(tmp_path: Path) -> None:
    path = _snapshot(
        tmp_path,
        runtime_health=_runtime_health(
            cross_check_last={"status": "trend_mismatch", "session_date": "2026-09-29", "hold": True}
        ),
    )

    cross = _check(evaluate_health(snapshot_path=path, now=_NOW), "signal_cross_check")

    assert cross["status"] == "DEGRADED"
    assert "position held" in cross["summary"]


def test_pending_cross_check_session_keeps_the_last_reading(tmp_path: Path) -> None:
    """The post-close SIP embargo window must not make every healthy run
    UNKNOWN — the last completed comparison is still a real reading."""
    runtime_health = _runtime_health()
    runtime_health["signal_cross_check"]["pending"] = {"2026-09-30": "waiting"}
    path = _snapshot(tmp_path, runtime_health=runtime_health)

    cross = _check(evaluate_health(snapshot_path=path, now=_NOW), "signal_cross_check")

    assert cross["status"] == "OK"
    assert "waiting for the SIP daily bar: 2026-09-30" in cross["summary"]


def test_cross_check_without_any_completed_session_is_unknown(tmp_path: Path) -> None:
    runtime_health = _runtime_health()
    runtime_health["signal_cross_check"]["last"] = None
    path = _snapshot(tmp_path, runtime_health=runtime_health)

    assert _check(evaluate_health(snapshot_path=path, now=_NOW), "signal_cross_check")["status"] == "UNKNOWN"


# --------------------------------------------------------------------------- #
# ops doctor — same readings, and the exit code follows
# --------------------------------------------------------------------------- #


def _doctor(snapshot: Path, tmp_path: Path) -> dict[str, Any]:
    return _run_doctor_checks(
        config_path=tmp_path / "absent.yaml",
        snapshot_path=snapshot,
        journal_path=tmp_path / "journal.sqlite3",
        decision_db_path=None,
    )


def _doctor_check(result: dict[str, Any], code: str) -> dict[str, Any]:
    return next(c for c in result["checks"] if c["code"] == code)


def test_doctor_reports_the_watchdog_alarm_as_critical(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, runtime_health=_runtime_health(alarms=["equity_decision_missing"]))

    result = _doctor(snapshot, tmp_path)

    assert _doctor_check(result, "intent_watchdog")["status"] == "CRITICAL"
    assert result["overall"] == "CRITICAL"


def test_doctor_alert_check_reads_the_snapshot_not_this_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ops doctor` runs in its own process: its own counters are always empty,
    which is why they must not be what it reports."""
    from parayabuck.monitoring import alerts

    monkeypatch.setattr(alerts, "alert_stats", lambda: dict(_WORKING_WEBHOOK))
    snapshot = _snapshot(tmp_path, persist_alerts=False)

    assert _doctor_check(_doctor(snapshot, tmp_path), "alerts")["status"] == "UNKNOWN"


def test_doctor_reports_an_unconfigured_webhook(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, alerts_state=dict(_WORKING_WEBHOOK, webhook_configured=False))

    assert _doctor_check(_doctor(snapshot, tmp_path), "alerts")["status"] == "DEGRADED"


def _run_cli(*args: str) -> tuple[int, str]:
    from parayabuck.cli import main as cli_main

    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(list(args))
        except SystemExit as exc:  # pragma: no cover - argparse failure path
            code = int(exc.code) if isinstance(exc.code, int) else 1
    return code, out.getvalue()


def test_doctor_exit_code_is_two_on_a_missing_decision(tmp_path: Path) -> None:
    """The deploy gate reads the exit code, so the alarm has to reach it."""
    snapshot = _snapshot(tmp_path, runtime_health=_runtime_health(alarms=["equity_decision_missing"]))

    code, _ = _run_cli("ops", "doctor", "--config", str(tmp_path / "absent.yaml"), "--snapshot", str(snapshot))

    assert code == 2


# --------------------------------------------------------------------------- #
# /api/ops/status — the same evaluation, served to the dashboard
# --------------------------------------------------------------------------- #


def test_ops_status_api_carries_the_watchdog_check(tmp_path: Path) -> None:
    from parayabuck.web.services import ops as ops_service

    snapshot = _snapshot(tmp_path, runtime_health=_runtime_health(alarms=["equity_decision_missing"]))
    config = Config(
        runtime=RuntimeConfig(decision_db_path=str(tmp_path / "none.db")),
        observability=ObservabilityConfig(
            snapshot_path=str(snapshot),
            journal_path=str(tmp_path / "journal.sqlite3"),
        ),
    )

    payload = ops_service.get_ops_status(config)

    assert payload["overall_health"] == "CRITICAL"
    assert _check(payload, "intent_watchdog")["status"] == "CRITICAL"
    assert _check(payload, "alerts")["status"] == "OK"


# --------------------------------------------------------------------------- #
# Dashboard badge — "Sağlıklı" must not hide an active alarm
# --------------------------------------------------------------------------- #


def _dashboard_panel(runtime_health: dict[str, Any]) -> dict[str, Any]:
    from parayabuck.web.services import dashboard as svc

    return svc._dashboard_health(
        SimpleNamespace(engine=SimpleNamespace(maximum_account_age_for_entry_seconds=30)),
        runtime_health,
        {},
        runtime_metrics_payload={"restart_count": 0},
    )


def test_dashboard_health_panel_publishes_degraded_reasons() -> None:
    panel = _dashboard_panel(_runtime_health(alarms=["equity_decision_missing"]))

    assert panel["healthy"] is True
    assert panel["degraded_reasons"] == ["equity_decision_missing"]


def test_dashboard_health_panel_has_no_degraded_reasons_when_clear() -> None:
    assert _dashboard_panel(_runtime_health())["degraded_reasons"] == []


def test_snapshot_persists_the_alert_copy_for_other_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this the engine's counters never leave the engine and every
    out-of-process surface is structurally UNKNOWN."""
    from parayabuck.monitoring import alerts
    from parayabuck.monitoring import snapshot as snapshot_module

    monkeypatch.setattr(alerts, "_enqueue_webhook", lambda url, payload: None)
    alerts.reset_for_tests()
    alerts.configure_alerting(enabled=True, webhook_url="http://127.0.0.1:1/hook", events=("paper_incident",))
    snap = snapshot_module.build_snapshot(runtime_health=_runtime_health())
    alerts.reset_for_tests()

    assert snap["alerts"]["webhook_configured"] is True
    assert snap["alerts"]["webhook_attempted"] == 0
    assert json.dumps(snap)  # persisted verbatim
