"""Audit wave 2 fixes M1: backup set members (peak sidecars and the operator halt
copy), the shared peak-record validator, the atomic_write symlink rule and the
repair script's halt clear.

Every test works on ``tmp_path`` or in-memory fixtures. Nothing touches the
network, credentials, data/ or logs/.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import parayabuck.backup as backup
import parayabuck.utils.io as io_module
from parayabuck.execution.alpaca_models import RiskStatePersistenceError
from parayabuck.execution.risk_peak import _parse_sidecar_file, check_peak_record
from parayabuck.live_safety import write_operator_halt
from parayabuck.monitoring.snapshot import build_snapshot, write_snapshot_atomic
from parayabuck.utils.io import atomic_write

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PEAK = json.dumps({"peak_equity": 1234.5, "epoch": 2, "account_id": "acct-1"})
_NOW = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)


def _age(path: Path, seconds: float) -> None:
    """Set ``path``'s mtime to ``seconds`` in the past."""
    stamp = datetime.now(UTC).timestamp() - seconds
    os.utime(path, (stamp, stamp))


def _make_db(path: Path) -> Path:
    """A small synthetic decision database (rollback journal, no WAL)."""
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("INSERT INTO decisions (payload) VALUES ('synthetic')")
    return path


def _runtime(tmp_path: Path) -> tuple[Path, Path]:
    """A runtime directory holding one synthetic database; returns (runtime dir, database)."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return runtime, _make_db(runtime / "parayabuck.sqlite3")


def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace the alert emitter; each call is recorded as kind + fields."""
    emitted: list[dict[str, object]] = []

    def fake_emit(kind: str, *, message: str, **fields: object) -> None:
        emitted.append({"kind": kind, "message": message, **fields})

    monkeypatch.setattr("parayabuck.monitoring.alerts.emit", fake_emit)
    return emitted


def _load_repair():
    path = _REPO_ROOT / "scripts" / "repair_local_state.py"
    spec = importlib.util.spec_from_file_location("repair_local_state_m1", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------- R2-3-1: invalid sidecar skipped


def test_invalid_sidecar_is_left_out_and_alerted_while_the_database_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, source = _runtime(tmp_path)
    (runtime / "paper_equity_peak.json").write_text('{"peak_equity": -5}', encoding="utf-8")
    (runtime / "live_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    emitted = _capture_alerts(monkeypatch)
    backup_dir = tmp_path / "backups"

    destination = backup.backup_database(source, backup_dir, retention_days=7, now=_NOW)

    live_copy = destination.with_name(f"{destination.stem}.live_equity_peak.json")
    assert live_copy.read_text(encoding="utf-8") == _PEAK
    assert not destination.with_name(f"{destination.stem}.paper_equity_peak.json").exists()
    assert backup.verify_backup_set(destination)
    assert [entry["kind"] for entry in emitted] == ["backup_failed"]
    assert emitted[0]["reason"] == "sidecar_invalid"


def test_unreadable_sidecar_is_left_out_and_alerted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, source = _runtime(tmp_path)
    (runtime / "paper_equity_peak.json").mkdir()  # reads as IsADirectoryError
    emitted = _capture_alerts(monkeypatch)

    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)

    assert backup.verify_backup(destination)
    assert emitted and emitted[0]["reason"] == "sidecar_invalid"


# ------------------------------------------------- R2-3-3: one validator, no OverflowError

_INVALID_SIDECARS = {
    "epoch_overflow": '{"peak_equity": 100.0, "epoch": 1e400}',
    "peak_integer_overflow": '{"peak_equity": 1' + "0" * 400 + "}",
    "negative_epoch": '{"peak_equity": 100.0, "epoch": -1}',
    "account_id_not_text": '{"peak_equity": 100.0, "account_id": 7}',
    "nan_peak": '{"peak_equity": NaN}',
    "negative_peak": '{"peak_equity": -1.0}',
}


@pytest.mark.parametrize("payload", list(_INVALID_SIDECARS.values()), ids=list(_INVALID_SIDECARS))
def test_backup_and_runtime_reject_the_same_sidecars(tmp_path: Path, payload: str) -> None:
    sidecar = tmp_path / "paper_equity_peak.json"
    sidecar.write_text(payload, encoding="utf-8")

    assert backup._valid_peak_payload(payload) is False
    with pytest.raises(RiskStatePersistenceError):
        _parse_sidecar_file(sidecar)


def test_valid_sidecar_is_accepted_by_both(tmp_path: Path) -> None:
    sidecar = tmp_path / "paper_equity_peak.json"
    sidecar.write_text(_PEAK, encoding="utf-8")

    assert backup._valid_peak_payload(_PEAK) is True
    assert _parse_sidecar_file(sidecar)["epoch"] == 2


def test_overflowing_epoch_is_a_runtime_error_not_an_overflow(tmp_path: Path) -> None:
    with pytest.raises(RiskStatePersistenceError):
        check_peak_record({"peak_equity": 1.0, "epoch": float("inf")}, tmp_path / "peak.json")


def test_backup_with_an_overflowing_epoch_publishes_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, source = _runtime(tmp_path)
    (runtime / "paper_equity_peak.json").write_text(_INVALID_SIDECARS["epoch_overflow"], encoding="utf-8")
    emitted = _capture_alerts(monkeypatch)

    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)

    assert backup.verify_backup(destination)
    assert not destination.with_name(f"{destination.stem}.paper_equity_peak.json").exists()
    assert emitted and emitted[0]["reason"] == "sidecar_invalid"


# ------------------------------------------------- R2-3-2: members before the snapshot


def _record_links(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every hard-link destination the backup publishes, in order."""
    links: list[str] = []
    real_link = os.link

    def recording_link(src: object, dst: object, **kwargs: object) -> None:
        links.append(os.fspath(dst))  # type: ignore[arg-type]
        real_link(src, dst, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(backup.os, "link", recording_link)
    return links


def test_members_are_linked_before_the_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, source = _runtime(tmp_path)
    (runtime / "paper_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    links = _record_links(monkeypatch)

    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)

    member = str(destination.with_name(f"{destination.stem}.paper_equity_peak.json"))
    assert links.index(member) < links.index(str(destination))


def test_a_crash_before_the_snapshot_link_publishes_no_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, source = _runtime(tmp_path)
    (runtime / "paper_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    real_link = os.link

    def crash_on_snapshot(src: object, dst: object, **kwargs: object) -> None:
        if os.fspath(dst).endswith(".sqlite3"):  # type: ignore[arg-type]
            raise RuntimeError("injected crash before the snapshot link")
        real_link(src, dst, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(backup.os, "link", crash_on_snapshot)
    backup_dir = tmp_path / "backups"

    with pytest.raises(RuntimeError, match="injected crash"):
        backup.backup_database(source, backup_dir, retention_days=7, now=_NOW)

    assert list(backup_dir.glob("parayabuck-*.sqlite3")) == []
    assert list(backup_dir.glob("parayabuck-*.paper_equity_peak.json")) == []
    assert list(backup_dir.glob(".parayabuck-stage-*")) == []


# ------------------------------------------------- R2-3-7: orphan temp files swept


def test_orphan_member_temp_files_from_earlier_runs_are_swept(tmp_path: Path) -> None:
    _, source = _runtime(tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    orphan = backup_dir / ".parayabuck-20261026T000000000000Z.paper_equity_peak.json.k3j2h1xq"
    orphan.write_text(_PEAK, encoding="utf-8")
    _age(orphan, 2 * 3600)
    fresh = backup_dir / ".parayabuck-20261027T095900000000Z.paper_equity_peak.json.live0000"
    fresh.write_text(_PEAK, encoding="utf-8")

    backup.backup_database(source, backup_dir, retention_days=7, now=_NOW)

    assert not orphan.exists()
    assert fresh.exists()


# ------------------------------------------------- R2-3-8: previous set's members sent again


def test_previous_set_sidecars_are_sent_off_host_again_after_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, source = _runtime(tmp_path)
    (runtime / "paper_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    _capture_alerts(monkeypatch)
    backup_dir = tmp_path / "backups"
    sent: list[Path] = []
    failing = {"on": True}

    def fake_copy(path: Path, **kwargs: object) -> bool:
        sent.append(Path(path))
        if failing["on"] and path.name.endswith(".paper_equity_peak.json"):
            raise backup.OffhostCopyError("injected remote failure")
        return True

    monkeypatch.setattr(backup, "copy_offhost", fake_copy)
    first = backup.backup_database(source, backup_dir, retention_days=7, now=_NOW)
    first_member = first.with_name(f"{first.stem}.paper_equity_peak.json")
    assert sent.count(first_member) == 1

    failing["on"] = False
    sent.clear()
    second = backup.backup_database(source, backup_dir, retention_days=7, now=_NOW + timedelta(hours=6))

    assert second != first
    assert first_member in sent
    assert second in sent


# ------------------------------------------------- R2-3-9: verify the set's members


def test_verify_backup_set_fails_when_a_member_is_corrupt(tmp_path: Path) -> None:
    runtime, source = _runtime(tmp_path)
    (runtime / "paper_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)
    member = destination.with_name(f"{destination.stem}.paper_equity_peak.json")
    assert backup.verify_backup_set(destination)

    member.write_text("not json", encoding="utf-8")

    assert backup.verify_backup(destination)  # the database alone still checks out
    assert not backup.verify_backup_set(destination)


def test_verify_backup_set_fails_when_a_member_is_not_a_regular_file(tmp_path: Path) -> None:
    runtime, source = _runtime(tmp_path)
    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)
    member = destination.with_name(f"{destination.stem}.paper_equity_peak.json")
    member.mkdir()

    assert not backup.verify_backup_set(destination)


def test_verify_backup_set_accepts_a_set_without_members(tmp_path: Path) -> None:
    _, source = _runtime(tmp_path)
    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)

    assert backup.verify_backup_set(destination)


# ------------------------------------------------- R2-3-10: prune unlinks guarded


def _expired_set(backup_dir: Path, stamp: str) -> Path:
    snapshot = backup_dir / f"parayabuck-{stamp}.sqlite3"
    snapshot.write_bytes(b"x")
    _age(snapshot, 30 * 86_400)
    return snapshot


def test_prune_keeps_going_when_one_snapshot_cannot_be_unlinked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    blocked = _expired_set(backup_dir, "20260101T000000000000Z")
    other = _expired_set(backup_dir, "20260102T000000000000Z")
    real_unlink = backup.Path.unlink

    def refuse_blocked(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == blocked.name:
            raise PermissionError("injected denial")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(backup.Path, "unlink", refuse_blocked)

    removed = backup.prune_backups(backup_dir, retention_days=7, now=_NOW)

    assert removed == [other]
    assert blocked.exists() and not other.exists()


def test_prune_tolerates_a_wal_sidecar_removed_concurrently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    expired = _expired_set(backup_dir, "20260101T000000000000Z")
    wal = Path(f"{expired}-wal")
    wal.write_bytes(b"x")
    real_unlink = backup.Path.unlink

    def vanish_wal(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.endswith("-wal"):
            raise FileNotFoundError(self)
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(backup.Path, "unlink", vanish_wal)

    assert backup.prune_backups(backup_dir, retention_days=7, now=_NOW) == [expired]


# ------------------------------------------------- R2-9-1: halt marker in the set


def test_halt_marker_is_copied_into_the_set_and_verified(tmp_path: Path) -> None:
    runtime, source = _runtime(tmp_path)
    write_operator_halt(runtime / "OPERATOR_HALT", reason="sell-off review", user="owner")

    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)

    halt_copy = destination.with_name(f"{destination.stem}.OPERATOR_HALT")
    record = json.loads(halt_copy.read_text(encoding="utf-8"))
    assert record["reason"] == "sell-off review"
    assert record["user"] == "owner"
    assert backup.verify_backup_set(destination)


def test_unreadable_halt_marker_is_still_kept_as_a_halt(tmp_path: Path) -> None:
    runtime, source = _runtime(tmp_path)
    (runtime / "OPERATOR_HALT").mkdir()  # a directory is not a readable halt record

    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)

    record = json.loads(destination.with_name(f"{destination.stem}.OPERATOR_HALT").read_text(encoding="utf-8"))
    assert record["reason"] == "unreadable halt marker"
    assert backup.verify_backup_set(destination)


def test_halt_copy_without_a_json_object_fails_the_set(tmp_path: Path) -> None:
    runtime, source = _runtime(tmp_path)
    write_operator_halt(runtime / "OPERATOR_HALT", reason="r", user="u")
    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)
    halt_copy = destination.with_name(f"{destination.stem}.OPERATOR_HALT")

    halt_copy.write_text("[1, 2]", encoding="utf-8")

    assert not backup.verify_backup_set(destination)


def test_no_halt_marker_means_no_halt_copy(tmp_path: Path) -> None:
    _, source = _runtime(tmp_path)

    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7, now=_NOW)

    assert sorted(path.name for path in (tmp_path / "backups").iterdir()) == [destination.name]


def test_halt_copy_ages_out_with_its_snapshot(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    expired = _expired_set(backup_dir, "20260101T000000000000Z")
    halt_copy = expired.with_name(f"{expired.stem}.OPERATOR_HALT")
    halt_copy.write_text("{}", encoding="utf-8")
    _age(halt_copy, 30 * 86_400)
    labelled = backup_dir / "parayabuck-keep-predeploy-20260101T000000000000Z.OPERATOR_HALT"
    labelled.write_text("{}", encoding="utf-8")
    _age(labelled, 30 * 86_400)

    removed = backup.prune_backups(backup_dir, retention_days=7, now=_NOW)

    assert set(removed) == {expired, halt_copy}
    assert labelled.exists()


# ------------------------------------------------- R2-3-6: symlinks inside their directory


def test_a_symlink_inside_its_own_directory_is_followed_and_kept(tmp_path: Path) -> None:
    real = tmp_path / "settings.real.yaml"
    real.write_text("mode: paper\n", encoding="utf-8")
    link = tmp_path / "settings.yaml"
    link.symlink_to(real.name)

    atomic_write(link, "mode: live\n")

    assert link.is_symlink()
    assert real.read_text(encoding="utf-8") == "mode: live\n"


def test_a_symlinked_parent_directory_is_not_a_refused_link(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(real_dir, target_is_directory=True)

    atomic_write(linked_dir / "state.json", "{}")

    assert (real_dir / "state.json").read_text(encoding="utf-8") == "{}"
    assert not (linked_dir / "state.json").is_symlink()


def test_unsafe_symlink_error_is_an_os_error(tmp_path: Path) -> None:
    assert issubclass(io_module.UnsafeSymlinkError, OSError)


# ------------------------------------------------- R2-3-4, R2-3-5, R2-3-11: repair script


@pytest.fixture(scope="module")
def repair():
    return _load_repair()


@pytest.fixture
def offline(repair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    return SimpleNamespace(snapshot_path=snapshot_path)


_REPAIR_SCHEMA = """
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


def _crashed_ledger(tmp_path: Path, accounts: tuple[str, ...]) -> Path:
    ledger = tmp_path / "parayabuck.sqlite3"
    con = sqlite3.connect(ledger)
    con.executescript(_REPAIR_SCHEMA)
    stamp = "2026-09-01T20:00:00+00:00"
    for account in accounts:
        con.execute(
            "INSERT INTO safety_incidents (account_id, reason, scope, activated_at, updated_at)"
            " VALUES (?,'decision_worker_crashed','global',?,?)",
            (account, stamp, stamp),
        )
    con.commit()
    con.close()
    return ledger


def _incident_accounts(ledger: Path) -> list[str]:
    con = sqlite3.connect(ledger)
    try:
        return [row[0] for row in con.execute("SELECT account_id FROM safety_incidents ORDER BY account_id")]
    finally:
        con.close()


def test_resolution_log_is_written_only_after_the_clear_commits(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _crashed_ledger(tmp_path, ("acct",))
    monkeypatch.setattr(repair, "LEDGER", ledger)
    monkeypatch.setattr(repair, "_run_broker_reconcile", lambda: _verified(repair, "acct"))
    seen_by_other_connection: list[list[str]] = []
    real_record = repair._record_resolutions

    def spy(rows, reason, now, ledger_path=None):  # type: ignore[no-untyped-def]
        seen_by_other_connection.append(_incident_accounts(ledger))
        return real_record(rows, reason, now, ledger_path)

    monkeypatch.setattr(repair, "_record_resolutions", spy)

    assert repair.repair_runtime_db(ledger) is True

    assert seen_by_other_connection == [[]]  # the clear was already committed
    entries = (tmp_path / "safety_incident_resolutions.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["account_id"] for line in entries] == ["acct"]


def _verified(repair, account: str) -> bool:
    """A reconcile stub that reports a clean verdict for ``account``, as the real one does."""
    repair._VERIFIED_ACCOUNT_ID = account
    return True


def test_halt_clear_is_scoped_to_the_verified_account(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _crashed_ledger(tmp_path, ("acct", "other-account"))
    monkeypatch.setattr(repair, "LEDGER", ledger)
    monkeypatch.setattr(repair, "_run_broker_reconcile", lambda: _verified(repair, "acct"))

    assert repair.repair_runtime_db(ledger) is True

    assert _incident_accounts(ledger) == ["other-account"]


def test_unknown_verified_account_clears_nothing_in_the_real_path(
    repair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reconcile with no broker account identity is not verified (fail closed)."""
    import parayabuck.execution.alpaca as alpaca_module

    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    config = tmp_path / "settings.yaml"
    config.write_text((_REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.setattr(alpaca_module.AlpacaBroker, "reconcile", lambda self: {"MATCH": ["QLD"]})
    ledger = tmp_path / "parayabuck.sqlite3"
    repair._VERIFIED_ACCOUNT_ID = None  # the module is shared across tests; repair_runtime_db resets it too

    assert repair._run_broker_reconcile(ledger=ledger) is False
    assert repair._VERIFIED_ACCOUNT_ID is None


def test_engine_started_during_the_reconcile_keeps_the_halt(
    repair, offline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _crashed_ledger(tmp_path, ("acct",))
    monkeypatch.setattr(repair, "LEDGER", ledger)

    def reconcile_while_engine_starts() -> bool:
        write_snapshot_atomic(offline.snapshot_path, build_snapshot(runtime_state="running"))
        return _verified(repair, "acct")

    monkeypatch.setattr(repair, "_run_broker_reconcile", reconcile_while_engine_starts)

    assert repair.repair_runtime_db(ledger) is False
    assert _incident_accounts(ledger) == ["acct"]
    assert "NOT cleared" in capsys.readouterr().out
