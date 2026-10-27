"""Audit 2026-10-27 fixes owned by W02: operator CLI, doctor and diagnostics.

Each test pins one invariant. Every path lives under ``tmp_path``; the
doctor tests stub the account keys, the .env location and the tracked-file
scan so the result does not depend on the host.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

import parayabuck.config as config_module
from parayabuck import cli as cli_module
from parayabuck import live_safety
from parayabuck.cli import _parse_time_arg, _parser
from parayabuck.cli import main as cli_main
from parayabuck.monitoring import diagnostic as diagnostic_module
from parayabuck.monitoring import health as health_module
from parayabuck.monitoring.forensics import map_no_trade_reason
from parayabuck.monitoring.journal import EventJournal
from parayabuck.monitoring.operational_event import OperationalEvent

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ABSENT_CONFIG = "/nonexistent/parayabuck-test/settings.yaml"


def _run(*args: str) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(list(args))
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def _settings(tmp_path: Path, decision_db: str | None = None) -> Path:
    """The shipped settings with every state path inside ``tmp_path``."""
    raw = yaml.safe_load((_REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw.setdefault("runtime", {})["decision_db_path"] = decision_db or str(tmp_path / "decisions.sqlite3")
    raw["observability"] = {
        **(raw.get("observability") or {}),
        "snapshot_path": str(tmp_path / "health_snapshot.json"),
        "journal_path": str(tmp_path / "observability.sqlite3"),
    }
    raw.setdefault("backup", {})["path"] = str(tmp_path / "backups")
    raw.setdefault("backtest", {})["output_dir"] = str(tmp_path / "reports")
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _doctor_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, scan_stdout: bytes | None = b"README.md\0"
) -> None:
    """Deterministic doctor host: no .env, stub keys, scripted tracked-file scan.

    ``scan_stdout=None`` makes ``git ls-files`` fail, as it does when the service
    user hits git's dubious-ownership guard.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_module, "dotenv_path", lambda: tmp_path / "absent.env")
    monkeypatch.setattr(live_safety, "mode_credentials", lambda live=False: ("paper-key", "paper-secret"))
    real_run = subprocess.run

    def fake_run(cmd: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if list(cmd[:2]) == ["git", "ls-files"]:
            if scan_stdout is None:
                raise OSError("fatal: detected dubious ownership in repository")
            return subprocess.CompletedProcess(cmd, 0, stdout=scan_stdout, stderr=b"")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)


def _delete_mode_database(path: Path) -> Path:
    """A database as a restored backup leaves it: DELETE journal, not WAL."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE probe (value INTEGER)")
        conn.commit()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()
    return path


def _alert_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    alerts: list[str] = []
    monkeypatch.setattr(cli_module, "_emit_ops_alert", lambda kind, message, **fields: alerts.append(kind))
    return alerts


# --- R7-1: a DELETE-journal restore must not fail `doctor --strict` -------------


def test_doctor_strict_passes_on_a_restored_delete_journal_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _doctor_env(monkeypatch, tmp_path)
    database = _delete_mode_database(tmp_path / "decisions.sqlite3")
    config = _settings(tmp_path, decision_db=str(database))

    code, out, err = _run("--config", str(config), "doctor", "--strict")

    assert code == 0, out + err
    assert any(line.startswith("INFO") and "sqlite journal_mode" in line for line in out.splitlines())


# --- R7-7: a tracked-file scan that cannot run is a blocker under --strict ------


def test_strict_doctor_fails_closed_when_the_tracked_file_scan_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _doctor_env(monkeypatch, tmp_path, scan_stdout=None)
    config = _settings(tmp_path)

    strict_code, strict_out, _ = _run("--config", str(config), "doctor", "--strict")
    plain_code, _, _ = _run("--config", str(config), "doctor")

    assert strict_code == 1
    assert "tracked-file scan could not run" in strict_out
    assert plain_code == 0


# --- S7-2: installed versions against requirements.lock, non-blocking -----------


def test_doctor_reports_lock_pins_that_differ_from_the_installed_versions_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _doctor_env(monkeypatch, tmp_path)
    (tmp_path / "requirements.lock").write_text(
        "# generated\npytest==0.0.1\nparayabuck-no-such-pin==1.0\n", encoding="utf-8"
    )
    config = _settings(tmp_path)

    code, out, _ = _run("--config", str(config), "doctor", "--strict")

    assert code == 0
    locked = [line for line in out.splitlines() if "locked pins" in line]
    assert locked and locked[0].startswith("WARN")
    assert "parayabuck-no-such-pin not installed (locked 1.0)" in locked[0]


# --- D9-8: CONFIG_PATH reaches doctor and the ops commands ----------------------


def test_doctor_and_ops_commands_honour_config_path_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _doctor_env(monkeypatch, tmp_path)
    config = _settings(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config))
    captured: dict[str, Any] = {}

    def fake_checks(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"overall": "OK", "checks": []}

    monkeypatch.setattr(diagnostic_module, "_run_doctor_checks", fake_checks)
    bundles: dict[str, Any] = {}

    def fake_bundle(output_path: Any = None, **kwargs: Any) -> Path:
        bundles.update(kwargs)
        return tmp_path / "bundle.zip"

    monkeypatch.setattr(diagnostic_module, "create_bundle_zip", fake_bundle)

    code, out, _ = _run("doctor")
    assert code == 0
    assert f"config parses: {config}" in out
    _run("ops", "doctor")
    _run("ops", "bundle")

    assert captured["config_path"] == str(config)
    assert bundles["config_path"] == str(config)


# --- R9-1: an open circuit breaker is CRITICAL for ops doctor -------------------


def _write_snapshot(path: Path, *, runtime_health: dict[str, Any]) -> Path:
    path.write_text(
        json.dumps({"written_at": datetime.now(UTC).isoformat(), "runtime_health": runtime_health}),
        encoding="utf-8",
    )
    return path


def test_ops_doctor_is_critical_while_the_circuit_breaker_is_open(tmp_path: Path) -> None:
    snapshot = _write_snapshot(
        tmp_path / "snap.json", runtime_health={"circuit_breaker_reason": "reconciliation_failed"}
    )
    args = ["--config", _ABSENT_CONFIG, "ops", "doctor", "--snapshot", str(snapshot)]

    code, out, _ = _run(*args, "--db", str(tmp_path / "d.sqlite3"), "--journal", str(tmp_path / "j.sqlite3"))

    assert code == 2
    assert any(line.startswith("CRITICAL") and "circuit_breaker" in line for line in out.splitlines())


def test_ops_doctor_reports_the_breaker_check_ok_when_the_breaker_is_closed(tmp_path: Path) -> None:
    snapshot = _write_snapshot(tmp_path / "snap.json", runtime_health={"circuit_breaker_reason": None})

    _, out, _ = _run(
        "--config",
        _ABSENT_CONFIG,
        "ops",
        "doctor",
        "--snapshot",
        str(snapshot),
        "--db",
        str(tmp_path / "d.sqlite3"),
        "--journal",
        str(tmp_path / "j.sqlite3"),
    )

    assert any(line.startswith("OK") and "circuit_breaker" in line for line in out.splitlines())


# --- F8-4: --retention-days must be a finite count of at least one day ----------


@pytest.mark.parametrize("value", ["-1", "0", "0.0001", "0.5", "nan", "inf", "soon"])
def test_backup_retention_days_rejects_values_that_prune_or_alert_wrongly(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(["backup", "--retention-days", value])
    assert exc.value.code == 2


def test_backup_retention_days_accepts_one_day_and_more() -> None:
    assert _parser().parse_args(["backup", "--retention-days", "1"]).retention_days == 1.0
    assert _parser().parse_args(["backup", "--retention-days", "14.5"]).retention_days == 14.5


# --- F8-5: write commands need an existing directory and print absolute paths ---


def test_halt_refuses_to_write_when_the_runtime_directory_is_missing(tmp_path: Path) -> None:
    config = _settings(tmp_path, decision_db=str(tmp_path / "absent_dir" / "decisions.sqlite3"))

    code, _, err = _run("--config", str(config), "halt", "--reason", "test")

    assert code == 2
    assert "does not exist" in err
    assert not (tmp_path / "absent_dir").exists()


def test_halt_prints_an_absolute_marker_path_and_resume_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "runtime").mkdir()
    monkeypatch.chdir(tmp_path)
    config = _settings(tmp_path, decision_db="runtime/decisions.sqlite3")

    code, out, _ = _run("--config", str(config), "halt", "--reason", "test")

    assert code == 0
    line = next(line for line in out.splitlines() if line.startswith("Operator halt set: "))
    marker = Path(line.split(": ", 1)[1])
    assert marker.is_absolute()
    assert marker.is_file()

    resume_code, resume_out, _ = _run("--config", str(config), "resume")
    assert resume_code == 0
    assert "cleared" in resume_out
    assert not marker.exists()


# --- F8-8: verify-only on a missing file is not found, not corrupt -------------


def test_verify_only_on_a_missing_file_reports_not_found_without_an_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _alert_recorder(monkeypatch)
    config = _settings(tmp_path)

    code, _, err = _run("--config", str(config), "backup", "--verify-only", str(tmp_path / "nope.sqlite3"))

    assert code == 2
    assert "Backup not found" in err
    assert alerts == []


def test_backup_refuses_a_missing_runtime_database_without_creating_the_backup_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _alert_recorder(monkeypatch)
    config = _settings(tmp_path, decision_db=str(tmp_path / "absent.sqlite3"))

    code, _, err = _run("--config", str(config), "backup")

    assert code == 2
    assert "runtime database not found" in err
    assert alerts == ["backup_failed"]
    assert not (tmp_path / "backups").exists()


# --- S9-8 / S-11: the CLI entry point sets an owner-only umask -----------------


def test_cli_entry_point_sets_an_owner_only_umask(tmp_path: Path) -> None:
    config = _settings(tmp_path)
    previous = os.umask(0o022)
    try:
        _run("--config", str(config), "backup", "--verify-only", str(tmp_path / "nope.sqlite3"))
        current = os.umask(0o022)
    finally:
        os.umask(previous)

    assert current == 0o077


# --- F8-7: ops status exits like ops doctor -----------------------------------


@pytest.mark.parametrize(
    ("overall", "expected"),
    [("OK", 0), ("DEGRADED", 1), ("BLOCKED", 1), ("UNKNOWN", 1), ("CRITICAL", 2)],
)
def test_ops_status_exit_code_follows_the_overall_health(
    overall: str, expected: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        health_module, "evaluate_health", lambda **kwargs: {"overall_health": overall, "checks": []}
    )
    config = _settings(tmp_path)
    paths = ["--db", str(tmp_path / "d.sqlite3"), "--snapshot", str(tmp_path / "s.json")]

    text_code, _, _ = _run("--config", str(config), "ops", "status", *paths)
    json_code, _, _ = _run("--config", str(config), "ops", "status", "--json", *paths)

    assert text_code == expected
    assert json_code == expected


# --- T-2: --since/--until offsets are normalised to UTC -----------------------


def test_ops_time_bounds_with_an_offset_are_normalised_to_utc() -> None:
    bound = _parse_time_arg("2026-10-27T10:00:00+03:00")

    assert bound is not None
    assert bound.isoformat() == "2026-10-27T07:00:00+00:00"
    naive = _parse_time_arg("2026-10-27T10:00:00")
    assert naive is not None
    assert naive.isoformat() == "2026-10-27T10:00:00+00:00"


# --- R9-5: ops explain latest answers for the engine's session ----------------

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


def _ledger(
    path: Path,
    *,
    sessions: list[tuple[str, str, str]],
    decisions: list[tuple[str, str, str, str]],
) -> Path:
    """sessions: (session_id, account_id, started_at); decisions: (decision_id, session_id, account_id, timestamp)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_LEDGER_SCHEMA)
        for session_id, account_id, started_at in sessions:
            conn.execute(
                "INSERT INTO portfolio_runtime_sessions VALUES (?, ?, 'digest', 'paper', ?, NULL, 'running')",
                (session_id, account_id, started_at),
            )
        for decision_id, session_id, account_id, stamp in decisions:
            conn.execute(
                "INSERT INTO portfolio_decisions VALUES (?, ?, ?, 'digest', ?, 'equity', 'vt35', '{}', '{}')",
                (decision_id, session_id, account_id, stamp),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def _event(kind: str, *, decision_id: str, **fields: Any) -> OperationalEvent:
    return OperationalEvent(
        event_id=f"ev-{kind}-{decision_id}",
        occurred_at=datetime.now(UTC),
        kind=kind,
        severity="INFO",
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


def test_ops_explain_latest_names_the_session_with_no_persisted_decision(tmp_path: Path) -> None:
    database = _ledger(
        tmp_path / "ledger.sqlite3",
        sessions=[("s-old", "acct", "2026-10-27T08:00:00+00:00"), ("s-new", "acct", "2026-10-27T12:00:00+00:00")],
        decisions=[("d-old", "s-old", "acct", "2026-10-27T09:00:00+00:00")],
    )
    args = [
        "--config",
        _ABSENT_CONFIG,
        "ops",
        "explain",
        "latest",
        "--json",
        "--db",
        str(database),
        "--journal",
        str(tmp_path / "absent-journal.sqlite3"),
    ]

    code, out, _ = _run(*args)

    assert code == 0
    assert json.loads(out)["message"] == "no decision persisted for s-new"


def test_ops_explain_latest_explains_a_decision_of_the_engine_session(tmp_path: Path) -> None:
    database = _ledger(
        tmp_path / "ledger.sqlite3",
        sessions=[("s-old", "acct", "2026-10-27T08:00:00+00:00"), ("s-new", "acct", "2026-10-27T12:00:00+00:00")],
        decisions=[
            ("d-old", "s-old", "acct", "2026-10-27T09:00:00+00:00"),
            ("d-new", "s-new", "acct", "2026-10-27T13:00:00+00:00"),
        ],
    )

    code, out, _ = _run(
        "--config",
        _ABSENT_CONFIG,
        "ops",
        "explain",
        "latest",
        "--json",
        "--db",
        str(database),
        "--journal",
        str(tmp_path / "absent-journal.sqlite3"),
    )

    assert code == 0
    assert json.loads(out)["decision_id"] == "d-new"


# --- F9-10: the decision summary's hold reason is printed in canonical form ----


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("SIGNAL_SOURCE_MISMATCH", "SIGNAL_SOURCE_MISMATCH"),
        ("signal_source_mismatch", "SIGNAL_SOURCE_MISMATCH"),
        ("signal_adjustment_unverified", "SIGNAL_ADJUSTMENT_UNVERIFIED"),
        ("GAIN_BAND_HOLD", "REBALANCE_BAND"),
        ("something_new_nobody_mapped", "UNKNOWN"),
    ],
)
def test_no_trade_reason_canonicalisation_covers_the_engine_hold_reasons(raw: str, canonical: str) -> None:
    assert map_no_trade_reason(raw) == canonical


def test_ops_explain_prints_the_canonical_reason_from_the_decision_summary(tmp_path: Path) -> None:
    database = _ledger(
        tmp_path / "ledger.sqlite3",
        sessions=[("s1", "acct", "2026-10-27T08:00:00+00:00")],
        decisions=[("d1", "s1", "acct", "2026-10-27T09:00:00+00:00")],
    )
    journal = tmp_path / "journal.sqlite3"
    EventJournal(str(journal))._flush_batch(
        [_event("decision_summary", decision_id="d1", no_trade_reason="signal_adjustment_unverified")]
    )

    _, out, _ = _run(
        "--config",
        _ABSENT_CONFIG,
        "ops",
        "explain",
        "d1",
        "--json",
        "--db",
        str(database),
        "--journal",
        str(journal),
    )

    assert json.loads(out)["no_trade_reason"] == "SIGNAL_ADJUSTMENT_UNVERIFIED"


# --- S9-4: the diagnostic bundle is replaced atomically -----------------------


def test_bundle_is_written_through_a_temp_file_and_leaves_no_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostic_module, "collect_bundle_data", lambda **kwargs: {"manifest": {"ok": True}})
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    target = bundle_dir / "bundle.zip"
    target.write_bytes(b"previous bundle")

    out = diagnostic_module.create_bundle_zip(target)

    assert out == target
    with zipfile.ZipFile(target) as archive:
        assert "manifest.json" in archive.namelist()
    assert sorted(path.name for path in bundle_dir.iterdir()) == ["bundle.zip"]


def test_failed_bundle_write_keeps_the_previous_archive_and_leaves_no_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostic_module, "collect_bundle_data", lambda **kwargs: {"manifest": {"ok": True}})

    def broken_section(_content: Any) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(diagnostic_module, "_dump_section", broken_section)
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    target = bundle_dir / "bundle.zip"
    target.write_bytes(b"previous bundle")

    with pytest.raises(OSError):
        diagnostic_module.create_bundle_zip(target)

    assert target.read_bytes() == b"previous bundle"
    assert sorted(path.name for path in bundle_dir.iterdir()) == ["bundle.zip"]
