"""Audit-2 (2026-10-11) W5 regressions: monitoring, CLI, backup and ops tooling.

Each test pins one invariant: read-only ops/diagnostic paths never create the
journal, honour the configured journal path and the current account, report
the engine's persisted counters, and the CLI/scripts behave as documented.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.monitoring import journal as journal_module
from parayabuck.monitoring import receipt as receipt_module
from parayabuck.monitoring.journal import DEFAULT_JOURNAL_PATH, EventJournal
from parayabuck.monitoring.operational_event import OperationalEvent
from parayabuck.web.services import ops as ops_svc

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str) -> Any:
    """Load a ``scripts/`` module by path (``scripts/`` is not a package)."""
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "scripts" / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(kind: str, *, decision_id: str | None = None, severity: str = "INFO", **fields: Any) -> OperationalEvent:
    return OperationalEvent(
        event_id=f"ev-{kind}-{decision_id}-{severity}",
        occurred_at=datetime.now(UTC),
        kind=kind,
        severity=severity,
        message="probe",
        session_id=None,
        account_id=None,
        mode="paper",
        market=None,
        strategy=None,
        decision_id=decision_id,
        client_order_id=None,
        broker_order_id=None,
        incident_id=None,
        correlation_id=None,
        fields=fields,
    )


def _journal_with(path: Path, *events: OperationalEvent) -> Path:
    journal = EventJournal(str(path))
    journal._flush_batch(list(events))
    return path


_LEDGER_SCHEMA = """
CREATE TABLE portfolio_runtime_sessions (
    session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
    configuration_digest TEXT NOT NULL, mode TEXT NOT NULL,
    started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL
);
CREATE TABLE portfolio_decisions (
    decision_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT '', configuration_digest TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL, market TEXT NOT NULL,
    strategy_name TEXT NOT NULL, target_json TEXT NOT NULL, risk_json TEXT NOT NULL
);
"""


def _ledger(path: Path, *, sessions: list[tuple[str, str, str]], decisions: list[tuple[str, str, str]]) -> Path:
    """sessions: (session_id, account_id, started_at); decisions: (decision_id, account_id, timestamp)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_LEDGER_SCHEMA)
        for session_id, account_id, started_at in sessions:
            conn.execute(
                "INSERT INTO portfolio_runtime_sessions VALUES (?, ?, 'digest', 'paper', ?, NULL, 'running')",
                (session_id, account_id, started_at),
            )
        # Each decision belongs to its account's session, as the engine writes it.
        session_of_account = {account_id: session_id for session_id, account_id, _ in sessions}
        for decision_id, account_id, stamp in decisions:
            conn.execute(
                "INSERT INTO portfolio_decisions VALUES (?, ?, ?, 'digest', ?, 'equity', 'vt35', '{}', '{}')",
                (decision_id, session_of_account.get(account_id, "s"), account_id, stamp),
            )
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def fresh_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """No process-global journal and an empty cwd; records any get_journal() call."""
    calls: list[object] = []
    monkeypatch.setattr(journal_module, "_global_journal", None)
    monkeypatch.setattr(journal_module, "get_journal", lambda path=None: calls.append(path))
    monkeypatch.chdir(tmp_path)
    return calls


def _config(db: Path, journal: Path) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(decision_db_path=str(db)),
        observability=SimpleNamespace(journal_path=str(journal)),
    )


# --- Q2D-2: journal_stats never builds (and so never creates) a journal ---


def test_journal_stats_without_a_global_never_constructs_one(fresh_host: list[object], tmp_path: Path) -> None:
    stats = journal_module.journal_stats()

    assert stats["event_journal_written"] == 0
    assert fresh_host == []
    assert not (tmp_path / DEFAULT_JOURNAL_PATH).exists()


# --- Q1D-02: /api/ops/events fallback never creates the default journal ---


def test_query_events_with_absent_journal_never_constructs_the_default(
    fresh_host: list[object], tmp_path: Path
) -> None:
    config = _config(tmp_path / "ledger.sqlite3", tmp_path / "absent.sqlite3")

    assert ops_svc.query_events(10, config) == []
    assert fresh_host == []
    assert not (tmp_path / DEFAULT_JOURNAL_PATH).exists()
    assert not (tmp_path / "absent.sqlite3").exists()


# --- Q2D-1 / Q2D-6: timeline reads the configured journal, read-only ---


def test_timeline_reads_the_configured_journal_and_creates_no_default(fresh_host: list[object], tmp_path: Path) -> None:
    journal = _journal_with(tmp_path / "custom-journal.sqlite3", _event("decision_summary", decision_id="d9"))
    db = _ledger(tmp_path / "ledger.sqlite3", sessions=[("s", "acct", "2026-10-01")], decisions=[("d9", "acct", "t")])

    result = ops_svc.get_timeline("d9", _config(db, journal))

    assert [event["decision_id"] for event in result["operational_events"]] == ["d9"]
    assert fresh_host == []
    assert not (tmp_path / DEFAULT_JOURNAL_PATH).exists()


# --- Q2D-4 / Q2D-6: latest decision is scoped to the current account ---


def test_latest_decision_is_scoped_to_the_current_account(tmp_path: Path) -> None:
    db = _ledger(
        tmp_path / "ledger.sqlite3",
        sessions=[("s-old", "acct-old", "2026-10-01T00:00:00"), ("s-new", "acct-new", "2026-10-02T00:00:00")],
        decisions=[("d-old", "acct-old", "2026-10-01T20:00:00")],
    )
    config = _config(db, tmp_path / "journal.sqlite3")

    # Rotated, the new account has not decided yet: the old one is not "latest".
    assert ops_svc.get_latest_decision(config)["decision"] is None

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO portfolio_decisions VALUES ('d-new', 's-new', 'acct-new', 'digest', "
        "'2026-10-01T10:00:00', 'equity', 'vt35', '{}', '{}')"
    )
    conn.commit()
    conn.close()
    assert ops_svc.get_latest_decision(config)["decision"]["decision_id"] == "d-new"


def test_latest_decision_is_unscoped_when_the_account_is_unknown(tmp_path: Path) -> None:
    db = _ledger(tmp_path / "ledger.sqlite3", sessions=[], decisions=[("d1", "acct", "2026-10-01T20:00:00")])

    assert ops_svc.get_latest_decision(_config(db, tmp_path / "j.sqlite3"))["decision"]["decision_id"] == "d1"


def _run_cli(*args: str) -> tuple[int, str, str]:
    from parayabuck.cli import main as cli_main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(list(args))
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def test_cli_explain_latest_is_scoped_to_the_current_account(tmp_path: Path) -> None:
    db = _ledger(
        tmp_path / "ledger.sqlite3",
        sessions=[("s-old", "acct-old", "2026-10-01T00:00:00"), ("s-new", "acct-new", "2026-10-02T00:00:00")],
        decisions=[("d-old", "acct-old", "2026-10-02T20:00:00"), ("d-new", "acct-new", "2026-10-01T20:00:00")],
    )

    code, out, _ = _run_cli(
        "--config", str(tmp_path / "absent.yaml"), "ops", "explain", "--json",
        "--db", str(db), "--journal", str(tmp_path / "absent-journal.sqlite3"),
    )  # fmt: skip

    assert code == 0
    assert json.loads(out)["decision_id"] == "d-new"


# --- Q3D-3: no evidence is UNKNOWN, never the "Normal" NO_TARGET_CHANGE ---


def test_cli_explain_without_evidence_reports_unknown(tmp_path: Path) -> None:
    db = _ledger(tmp_path / "ledger.sqlite3", sessions=[], decisions=[("d1", "acct", "2026-10-01T20:00:00")])

    code, out, _ = _run_cli(
        "--config", str(tmp_path / "absent.yaml"), "ops", "explain", "d1", "--json",
        "--db", str(db), "--journal", str(tmp_path / "absent-journal.sqlite3"),
    )  # fmt: skip

    payload = json.loads(out)
    assert code == 0
    assert payload["no_trade_reason"] == "UNKNOWN"
    assert "decision_summary" in payload["detail"]


def test_map_no_trade_reason_does_not_relabel_unknown_input_as_normal() -> None:
    from parayabuck.monitoring.forensics import map_no_trade_reason

    assert map_no_trade_reason("") == "UNKNOWN"
    assert map_no_trade_reason("something new") == "UNKNOWN"
    assert map_no_trade_reason("NO_TARGET_CHANGE") == "NO_TARGET_CHANGE"


# --- Q3D-6: --severity is case-insensitive and validated ---


def test_cli_events_severity_is_case_insensitive(tmp_path: Path) -> None:
    journal = _journal_with(tmp_path / "j.sqlite3", _event("market_data_degraded", severity="WARNING"))

    code, out, _ = _run_cli(
        "--config", str(tmp_path / "absent.yaml"), "ops", "events", "--json",
        "--severity", "warning", "--journal", str(journal),
    )  # fmt: skip

    assert code == 0
    assert [event["severity"] for event in json.loads(out)] == ["WARNING"]


def test_cli_events_rejects_an_unknown_severity(tmp_path: Path) -> None:
    code, _, err = _run_cli("ops", "events", "--severity", "error", "--journal", str(tmp_path / "j.sqlite3"))

    assert code == 64  # a usage error (L1: argparse exits EXIT_USAGE, not the CRITICAL code 2)
    assert "WARNING" in err


# --- Q3D-2: ops doctor honours --journal ---


def test_doctor_reports_the_journal_file_it_was_given(tmp_path: Path) -> None:
    from parayabuck.monitoring.diagnostic import _run_doctor_checks

    present = _journal_with(tmp_path / "present.sqlite3", _event("runtime_started"))

    def _journal_check(journal_path: Path) -> dict[str, Any]:
        result = _run_doctor_checks(
            config_path=tmp_path / "absent.yaml",
            snapshot_path=tmp_path / "absent.json",
            journal_path=journal_path,
            decision_db_path=None,
        )
        return next(check for check in result["checks"] if check["code"] == "journal_file")

    assert _journal_check(present)["status"] == "OK"
    assert _journal_check(present)["path"] == str(present)
    missing = _journal_check(tmp_path / "missing.sqlite3")
    assert missing["status"] == "UNKNOWN"
    assert not (tmp_path / "missing.sqlite3").exists()


# --- Q3D-1: the bundle ships the engine's persisted counters ---


def test_bundle_stats_come_from_the_engine_snapshot(tmp_path: Path) -> None:
    from parayabuck.monitoring.diagnostic import create_bundle_zip

    persisted_journal = {"event_journal_written": 41, "event_journal_failed": 0, "event_journal_dropped": 0}
    persisted_alerts = {"delivered": {"intent_alarm": 3}, "suppressed": 7}
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "written_at": datetime.now(UTC).isoformat(),
                "observability": persisted_journal,
                "alerts": persisted_alerts,
            }
        ),
        encoding="utf-8",
    )

    out = create_bundle_zip(
        tmp_path / "bundle.zip",
        config_path=tmp_path / "absent.yaml",
        snapshot_path=snapshot,
        journal_path=tmp_path / "absent-journal.sqlite3",
    )

    with zipfile.ZipFile(out) as archive:
        alert_stats = json.loads(archive.read("alert_stats.json"))
        journal_stats = json.loads(archive.read("journal_stats.json"))
    assert alert_stats["delivered"] == {"intent_alarm": 3}
    assert alert_stats["suppressed"] == 7
    assert journal_stats["event_journal_written"] == 41


def test_bundle_without_snapshot_marks_stats_unavailable(tmp_path: Path) -> None:
    from parayabuck.monitoring.diagnostic import collect_bundle_data

    data = collect_bundle_data(
        config_path=tmp_path / "absent.yaml",
        snapshot_path=tmp_path / "absent.json",
        journal_path=tmp_path / "absent-journal.sqlite3",
    )

    assert data["alert_stats"]["unavailable"] is True
    assert data["journal_stats"]["unavailable"] is True


# --- Q2D-3: forensic reads open the ledger read-only ---


def test_forensic_connection_is_read_only(tmp_path: Path) -> None:
    from parayabuck.monitoring.forensics import _connect_readonly

    db = _ledger(tmp_path / "ledger.sqlite3", sessions=[], decisions=[])
    conn = _connect_readonly(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE probe (a)")
    finally:
        conn.close()


# --- Q1D-04: `parayabuck backup` owns receipt retention at the audit floor ---


def test_backup_prunes_receipts_only_past_the_audit_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck import cli

    receipts = tmp_path / "receipts"
    receipts.mkdir()
    old = receipts / "old_equity_scheduled_a.v2.json"
    young = receipts / "young_equity_scheduled_b.v2.json"
    for path in (old, young):
        path.write_text("{}", encoding="utf-8")
    floor = receipt_module.MIN_RECEIPT_RETENTION_DAYS
    stale = (datetime.now(UTC) - timedelta(days=floor + 5)).timestamp()
    kept = (datetime.now(UTC) - timedelta(days=floor - 5)).timestamp()
    os.utime(old, (stale, stale))
    os.utime(young, (kept, kept))
    monkeypatch.setattr(receipt_module, "default_receipt_dir", lambda: receipts)

    cli._prune_decision_receipts()

    assert not old.exists()
    assert young.exists()


def test_receipt_prune_failure_never_fails_the_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck import cli

    def _boom(*_a: object, **_k: object) -> list[Path]:
        raise OSError("disk")

    monkeypatch.setattr(receipt_module, "prune_receipts", _boom)

    cli._prune_decision_receipts()  # does not raise


# --- Q1E-2: repair backup_ledger skips an absent ledger without creating it ---


def test_repair_backup_ledger_skips_an_absent_ledger(tmp_path: Path) -> None:
    repair = _load_script("repair_local_state_w5", "repair_local_state.py")
    qdir = tmp_path / "quarantine"
    (qdir / "runtime").mkdir(parents=True)
    ledger = tmp_path / "absent.sqlite3"

    assert repair.backup_ledger(qdir, ledger) is None
    assert not ledger.exists()
    assert list((qdir / "runtime").iterdir()) == []
    assert repair.repair_runtime_db(ledger) is False


# --- Q4E-3: run_paper_chaos --help prints usage and runs nothing ---


def test_run_paper_chaos_help_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    chaos = _load_script("run_paper_chaos_w5", "run_paper_chaos.py")
    ran: list[object] = []
    monkeypatch.setattr(chaos.subprocess, "run", lambda *a, **k: ran.append(a))

    with pytest.raises(SystemExit) as exc, contextlib.redirect_stdout(io.StringIO()) as out:
        chaos.main(["--help"])

    assert exc.value.code == 0
    assert "paper_chaos" in out.getvalue()
    assert ran == []
