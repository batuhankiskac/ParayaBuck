"""``scripts/repair_local_state.py`` refuses to compete with a live engine and
never lifts the ``decision_worker_crashed`` halt without a verified reconcile.

The repair script quarantines the runtime state and rewrites the ledger, so two
of its behaviours are load-bearing for a PAPER deployment:

* a running engine (fresh health-snapshot heartbeat) makes it a hard refusal —
  nothing on disk or in the DB is touched;
* the ``decision_worker_crashed`` incident and its matching ``risk_state``
  breaker are cleared ONLY when ``broker.reconcile()`` actually ran. Without
  credentials or after a failed reconcile the halt stays active, because the
  script cannot know the crash left no unknown broker-side orders or foreign
  inventory.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.monitoring.snapshot import build_snapshot, write_snapshot_atomic

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str):
    """Load a ``scripts/`` module by path (``scripts/`` is not a package)."""
    path = _REPO_ROOT / "scripts" / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def repair():
    return _load_script("repair_local_state_under_test", "repair_local_state.py")


_SCHEMA = """
CREATE TABLE portfolio_runtime_sessions (
    session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
    configuration_digest TEXT NOT NULL, mode TEXT NOT NULL,
    started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL
);
CREATE TABLE runtime_sessions (
    session_id TEXT PRIMARY KEY, account_id TEXT, mode TEXT NOT NULL,
    state TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT
);
CREATE TABLE safety_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
    reason TEXT NOT NULL, scope TEXT NOT NULL, activated_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, UNIQUE(account_id, reason)
);
CREATE TABLE risk_state (
    account_id TEXT NOT NULL, session_date TEXT NOT NULL,
    payload TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(account_id, session_date)
);
"""


def _make_ledger(
    tmp_path: Path,
    *,
    incidents: tuple[str, ...] = ("decision_worker_crashed",),
    risk_rows: tuple[tuple[str, str | None], ...] = (),
) -> Path:
    """Synthetic ledger with only the tables the repair script touches."""
    ledger = tmp_path / "parayabuck.sqlite3"
    con = sqlite3.connect(ledger)
    con.executescript(_SCHEMA)
    stamp = "2026-09-01T20:00:00+00:00"
    con.execute(
        "INSERT INTO portfolio_runtime_sessions VALUES ('ps1','acct','digest','paper',?,NULL,'open')",
        (stamp,),
    )
    con.execute(
        "INSERT INTO runtime_sessions VALUES ('rs1','acct','paper','open',?,NULL)",
        (stamp,),
    )
    for reason in incidents:
        con.execute(
            "INSERT INTO safety_incidents (account_id, reason, scope, activated_at, updated_at)"
            " VALUES ('acct',?,'global',?,?)",
            (reason, stamp, stamp),
        )
    for session_date, breaker in risk_rows:
        con.execute(
            "INSERT INTO risk_state VALUES ('acct',?,?,?)",
            (session_date, json.dumps({"circuit_breaker_reason": breaker}), stamp),
        )
    con.commit()
    con.close()
    return ledger


def _incidents(ledger: Path) -> list[str]:
    con = sqlite3.connect(ledger)
    try:
        return [row[0] for row in con.execute("SELECT reason FROM safety_incidents ORDER BY reason")]
    finally:
        con.close()


def _breakers(ledger: Path) -> dict[str, str | None]:
    con = sqlite3.connect(ledger)
    try:
        return {
            row[0]: json.loads(row[1]).get("circuit_breaker_reason")
            for row in con.execute("SELECT session_date, payload FROM risk_state")
        }
    finally:
        con.close()


def _open_sessions(ledger: Path) -> tuple[int, int]:
    con = sqlite3.connect(ledger)
    try:
        return (
            con.execute("SELECT COUNT(*) FROM portfolio_runtime_sessions WHERE ended_at IS NULL").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM runtime_sessions WHERE ended_at IS NULL").fetchone()[0],
        )
    finally:
        con.close()


def _write_snapshot(path: Path, runtime_state: str, *, written_at: datetime | None = None) -> None:
    snapshot = build_snapshot(runtime_state=runtime_state)
    if written_at is not None:
        snapshot["written_at"] = written_at.isoformat()
    write_snapshot_atomic(path, snapshot)


@pytest.fixture
def offline(repair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every path the script touches at ``tmp_path`` and clear credentials."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    snapshot_path = tmp_path / "health_snapshot.json"
    monkeypatch.setattr(
        repair,
        "_heartbeat_config",
        lambda: SimpleNamespace(
            observability=SimpleNamespace(snapshot_path=str(snapshot_path), snapshot_interval_seconds=30.0)
        ),
    )
    warmup = tmp_path / "cache" / "warmup.sqlite3"
    warmup.parent.mkdir(parents=True, exist_ok=True)
    warmup.write_bytes(b"warmup")
    monkeypatch.setattr(repair, "WARMUP_FILES", [warmup])
    obs = tmp_path / "runtime" / "observability.sqlite3"
    obs.parent.mkdir(parents=True, exist_ok=True)
    obs.write_bytes(b"obs")
    monkeypatch.setattr(repair, "OBS_FILES", [obs])
    quarantine = tmp_path / "quarantine"
    monkeypatch.setattr(repair, "QUARANTINE_ROOT", quarantine)
    return SimpleNamespace(
        snapshot_path=snapshot_path,
        warmup=warmup,
        obs=obs,
        quarantine=quarantine,
    )


# --------------------------------------------------------------------- heartbeat


def test_missing_snapshot_does_not_block(repair, offline) -> None:
    assert repair.check_engine_not_running() is True


def test_stopped_snapshot_does_not_block(repair, offline) -> None:
    _write_snapshot(offline.snapshot_path, "stopped")

    assert repair.check_engine_not_running() is True


def test_stale_snapshot_does_not_block(repair, offline) -> None:
    _write_snapshot(offline.snapshot_path, "running", written_at=datetime.now(UTC) - timedelta(days=2))

    assert repair.check_engine_not_running() is True


def test_fresh_heartbeat_refuses_before_touching_anything(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _write_snapshot(offline.snapshot_path, "running")
    ledger = _make_ledger(tmp_path)
    monkeypatch.setattr(repair, "LEDGER", ledger)

    assert repair.main() == 1
    assert "REFUSED" in capsys.readouterr().out
    # Nothing quarantined, nothing deleted, sessions untouched.
    assert offline.quarantine.exists() is False
    assert offline.warmup.exists()
    assert offline.obs.exists()
    assert _incidents(ledger) == ["decision_worker_crashed"]
    assert _open_sessions(ledger) == (1, 1)


# ----------------------------------------------------------------- reconciliation


def test_no_credentials_means_unverified(repair) -> None:
    assert repair._run_broker_reconcile() is False


def test_unverified_reconcile_keeps_the_incident(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """No credentials => no reconcile => the halt and its breaker survive."""
    ledger = _make_ledger(tmp_path, risk_rows=(("2026-09-30", "decision_worker_crashed"),))
    monkeypatch.setattr(repair, "LEDGER", ledger)

    assert repair.repair_runtime_db() is False
    assert "HALT STILL ACTIVE" in capsys.readouterr().out
    assert _incidents(ledger) == ["decision_worker_crashed"]
    assert _breakers(ledger) == {"2026-09-30": "decision_worker_crashed"}


def test_failed_reconcile_keeps_the_incident(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A broker error is unverified, same as a reconcile that never ran."""
    import parayabuck.execution.alpaca as alpaca_module

    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")

    def _boom(self):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(alpaca_module.AlpacaBroker, "reconcile", _boom)
    ledger = _make_ledger(tmp_path)
    monkeypatch.setattr(repair, "LEDGER", ledger)

    assert repair.repair_runtime_db() is False
    assert "HALT STILL ACTIVE" in capsys.readouterr().out
    assert _incidents(ledger) == ["decision_worker_crashed"]


def test_verified_reconcile_clears_the_incident(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _make_ledger(tmp_path)
    monkeypatch.setattr(repair, "LEDGER", ledger)
    monkeypatch.setattr(repair, "_run_broker_reconcile", lambda: True)

    assert repair.repair_runtime_db() is True
    assert _incidents(ledger) == []
    # The interrupted-session part of the repair still happens.
    assert _open_sessions(ledger) == (0, 0)


def test_verified_reconcile_leaves_other_incident_reasons(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _make_ledger(tmp_path, incidents=("decision_worker_crashed", "risk_limit_breached"))
    monkeypatch.setattr(repair, "LEDGER", ledger)
    monkeypatch.setattr(repair, "_run_broker_reconcile", lambda: True)

    assert repair.repair_runtime_db() is True
    assert _incidents(ledger) == ["risk_limit_breached"]


# --------------------------------------------------------------------- risk_state


def test_verified_reconcile_clears_the_matching_breaker(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _make_ledger(
        tmp_path,
        risk_rows=(("2026-09-30", "decision_worker_crashed"), ("2026-09-29", None)),
    )
    monkeypatch.setattr(repair, "LEDGER", ledger)
    monkeypatch.setattr(repair, "_run_broker_reconcile", lambda: True)

    assert repair.repair_runtime_db() is True
    assert _breakers(ledger) == {"2026-09-30": None, "2026-09-29": None}


def test_unrelated_breaker_reason_survives_verified_reconcile(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _make_ledger(tmp_path, risk_rows=(("2026-09-30", "data_stale"),))
    monkeypatch.setattr(repair, "LEDGER", ledger)
    monkeypatch.setattr(repair, "_run_broker_reconcile", lambda: True)

    assert repair.repair_runtime_db() is True
    assert _breakers(ledger) == {"2026-09-30": "data_stale"}


def test_no_hardcoded_session_date_is_cleared(repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale non-latest row is not rewritten for any hard-coded date."""
    ledger = _make_ledger(
        tmp_path,
        risk_rows=(("2026-09-30", None), ("2026-08-29", "decision_worker_crashed")),
    )
    monkeypatch.setattr(repair, "LEDGER", ledger)
    monkeypatch.setattr(repair, "_run_broker_reconcile", lambda: True)

    assert repair.repair_runtime_db() is True
    assert _breakers(ledger) == {"2026-09-30": None, "2026-08-29": "decision_worker_crashed"}


# ------------------------------------------------------------ audit / locking


def test_corrupt_snapshot_fails_closed(repair, offline) -> None:
    offline.snapshot_path.write_text("{not json", encoding="utf-8")

    assert repair.check_engine_not_running() is False


def test_session_updates_are_committed_before_the_broker_opens_the_ledger(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broker opens its own connection to the ledger; an uncommitted
    UPDATE left open here made that "database is locked"."""
    ledger = _make_ledger(tmp_path)
    monkeypatch.setattr(repair, "LEDGER", ledger)

    def _second_writer() -> bool:
        other = sqlite3.connect(ledger, timeout=0.2)
        try:
            other.execute("UPDATE runtime_sessions SET mode='paper'")
            other.commit()
        finally:
            other.close()
        return True

    monkeypatch.setattr(repair, "_run_broker_reconcile", _second_writer)

    assert repair.repair_runtime_db() is True
    assert _open_sessions(ledger) == (0, 0)


def test_cleared_incident_is_recorded_in_the_resolution_log(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _make_ledger(tmp_path)
    monkeypatch.setattr(repair, "LEDGER", ledger)
    monkeypatch.setattr(repair, "_run_broker_reconcile", lambda: True)

    assert repair.repair_runtime_db() is True
    assert _incidents(ledger) == []
    log_lines = (tmp_path / "safety_incident_resolutions.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in log_lines]
    assert [(e["reason"], e["account_id"], e["activated_at"]) for e in entries] == [
        ("decision_worker_crashed", "acct", "2026-09-01T20:00:00+00:00")
    ]
    assert entries[0]["resolved_at"]


def test_reconcile_with_drift_is_not_verified(repair, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A reconcile that ran but still reports drift (e.g. QLD BROKER_STALE)
    must never lift the halt; the ownership providers are wired in."""
    import parayabuck.execution.alpaca as alpaca_module

    config = tmp_path / "settings.yaml"
    config.write_text((_REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setattr(repair, "LEDGER", tmp_path / "parayabuck.sqlite3")
    seen: dict[str, object] = {}

    def _reconcile(self):
        seen["owned"] = self.portfolio_owned_quantities is not None
        seen["open_orders"] = self.portfolio_open_orders is not None
        seen["tradable"] = set(self.bot_tradable_symbols())
        return {"MATCH": [], "BROKER_STALE": ["QLD"], "MISSING_LOCAL_ORDER": ["QLD"]}

    monkeypatch.setattr(alpaca_module.AlpacaBroker, "reconcile", _reconcile)

    assert repair._run_broker_reconcile() is False
    assert seen["owned"] is True and seen["open_orders"] is True
    assert "QLD" in seen["tradable"]

    monkeypatch.setattr(alpaca_module.AlpacaBroker, "reconcile", lambda self: {"MATCH": ["QLD"], "BROKER_STALE": []})
    assert repair._run_broker_reconcile() is True
