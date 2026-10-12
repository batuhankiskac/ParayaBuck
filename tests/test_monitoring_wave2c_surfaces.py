"""Audit wave 2c — operator health surfaces must show what the ENGINE reports.

Three findings, one invariant: ``ops status``, ``ops doctor`` (and its exit
code), the dashboard badge and ``/api/ops/status`` run in processes other than
the engine, so they may only report state the engine itself published.

* F22 — an active intent-watchdog alarm (and the signal cross-check state)
  must appear on all four surfaces, not only on ``/readyz``.
* F23 — journal state must come from the snapshot the engine persists; with
  no such copy the answer is UNKNOWN, never OK. Alerts are local-only
  (2026-10-06): no surface may degrade because nothing is delivered off-host.
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
from typing import Any

import pytest

from parayabuck.config import Config, ObservabilityConfig, RuntimeConfig
from parayabuck.monitoring.diagnostic import _run_doctor_checks
from parayabuck.monitoring.health import HealthStatus, _check_observability, evaluate_health
from parayabuck.monitoring.snapshot import write_snapshot_atomic

_NOW = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)

_ALERT_COUNTERS: dict[str, Any] = {"suppressed": 0, "delivered": {"runtime_started": 1}}
_HEALTHY_JOURNAL: dict[str, Any] = {
    "event_journal_written": 42,
    "event_journal_failed": 0,
    "event_journal_dropped": 0,
    "event_journal_last_success_at": _NOW.isoformat(),
    "event_journal_last_failure_at": None,
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
        payload["alerts"] = alerts_state if alerts_state is not None else _ALERT_COUNTERS
    path = directory / "health_snapshot.json"
    write_snapshot_atomic(path, payload)
    return path


def _check(payload: dict[str, Any], code: str) -> dict[str, Any]:
    return next(c for c in payload["checks"] if c["code"] == code)


# --------------------------------------------------------------------------- #
# F23 — journal state comes from the engine's persisted copy; alerts are local
# --------------------------------------------------------------------------- #


def test_no_persisted_journal_copy_is_unknown_never_ok() -> None:
    check = _check_observability(None)

    assert check.status is HealthStatus.UNKNOWN
    assert "OK" not in check.summary


def test_local_only_alerts_never_degrade_health(tmp_path: Path) -> None:
    """Outbound delivery was removed: there is no alerts check, and neither a
    missing alert block nor zero counters can make the verdict DEGRADED."""
    for name, kwargs in (("plain", {}), ("no_block", {"persist_alerts": False})):
        health = evaluate_health(snapshot_path=_snapshot(tmp_path / name, **kwargs), now=_NOW)
        assert "alerts" not in {c["code"] for c in health["checks"]}
        assert not [c for c in health["checks"] if c["status"] == "DEGRADED"]
        assert _check(health, "observability")["status"] == "OK"


def test_recovered_journal_burst_does_not_pin_degraded(tmp_path: Path) -> None:
    """The counters are process-lifetime totals: a burst the journal has since
    written past must not stay DEGRADED until the next restart."""
    recovered = dict(
        _HEALTHY_JOURNAL,
        event_journal_failed=50,
        event_journal_dropped=20,
        event_journal_last_failure_at=(_NOW - timedelta(hours=2)).isoformat(),
    )
    check = _check(
        evaluate_health(snapshot_path=_snapshot(tmp_path, journal_state=recovered), now=_NOW), "observability"
    )

    assert check["status"] == "OK"
    assert "recovered" in check["summary"]


def test_currently_failing_journal_is_degraded(tmp_path: Path) -> None:
    failing = dict(
        _HEALTHY_JOURNAL,
        event_journal_failed=1,
        event_journal_last_success_at=(_NOW - timedelta(hours=2)).isoformat(),
        event_journal_last_failure_at=_NOW.isoformat(),
    )
    check = _check(evaluate_health(snapshot_path=_snapshot(tmp_path, journal_state=failing), now=_NOW), "observability")

    assert check["status"] == "DEGRADED"


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


def test_doctor_journal_check_reads_the_snapshot_not_this_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ops doctor` runs in its own process: its own counters are always empty,
    which is why they must not be what it reports. It has no alerts check."""
    from parayabuck.monitoring import journal

    monkeypatch.setattr(journal, "journal_stats", lambda *a, **k: dict(_HEALTHY_JOURNAL))
    result = _doctor(_snapshot(tmp_path, persist_journal=False), tmp_path)

    assert _doctor_check(result, "observability")["status"] == "UNKNOWN"
    assert "alerts" not in {c["code"] for c in result["checks"]}


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
    assert "alerts" not in {c["code"] for c in payload["checks"]}


# --------------------------------------------------------------------------- #
# Dashboard badge — "Sağlıklı" must not hide an active alarm
# --------------------------------------------------------------------------- #


def _dashboard_panel(runtime_health: dict[str, Any]) -> dict[str, Any]:
    from parayabuck.web.services import dashboard as svc

    return svc._dashboard_health(runtime_health, {})


def test_dashboard_health_panel_publishes_degraded_reasons() -> None:
    panel = _dashboard_panel(_runtime_health(alarms=["equity_decision_missing"]))

    assert panel["healthy"] is True
    assert panel["degraded_reasons"] == ["equity_decision_missing"]


def test_dashboard_health_panel_has_no_degraded_reasons_when_clear() -> None:
    assert _dashboard_panel(_runtime_health())["degraded_reasons"] == []


def test_snapshot_persists_the_alert_copy_for_other_processes() -> None:
    """Without this the engine's counters never leave the engine and
    `ops status` cannot show what the engine emitted."""
    from parayabuck.monitoring import alerts
    from parayabuck.monitoring import snapshot as snapshot_module

    alerts.reset_for_tests()
    alerts.emit("runtime_started", message="up")
    snap = snapshot_module.build_snapshot(runtime_health=_runtime_health())
    alerts.reset_for_tests()

    assert snap["alerts"] == {"suppressed": 0, "delivered": {"runtime_started": 1}}
    assert json.dumps(snap)  # persisted verbatim
