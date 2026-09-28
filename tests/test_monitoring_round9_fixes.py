"""Round-9 monitoring fix: doctor judges unclean sessions since the last clean stop."""

from __future__ import annotations

from pathlib import Path

import pytest

from parayabuck.monitoring import receipt as receipt_module
from parayabuck.monitoring.diagnostic import _run_doctor_checks
from parayabuck.monitoring.forensics import runtime_session_forensics
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.storage import SQLiteStore


def _sessions_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_first: bool) -> dict[str, object]:
    db = tmp_path / "ledger.sqlite3"
    repo = PortfolioStateRepository(str(db), store=SQLiteStore(str(db)))
    first = repo.begin_session("acct", "digest", "paper")
    if clean_first:
        repo.end_session(first, "stopped")
        repo.begin_session("acct", "digest", "paper")  # crashes: never ended
    else:
        # first crashes, the next one stops cleanly
        repo.end_session(repo.begin_session("acct", "digest", "paper"), "stopped")
    repo.begin_session("acct", "digest", "paper")  # current session
    monkeypatch.setattr(receipt_module, "default_receipt_dir", lambda: tmp_path / "receipts")
    doctor = _run_doctor_checks(
        config_path=tmp_path / "missing.yaml",
        snapshot_path=tmp_path / "missing.json",
        journal_path=tmp_path / "journal.sqlite3",
        decision_db_path=db,
        forensics={"sessions": runtime_session_forensics(db)},
    )
    return next(c for c in doctor["checks"] if c["code"] == "sessions")


def test_crash_followed_by_clean_stop_is_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check = _sessions_check(tmp_path, monkeypatch, clean_first=False)
    assert check["status"] == "OK"
    assert "1 historical unclean" in str(check["summary"])


def test_crash_after_clean_stop_is_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check = _sessions_check(tmp_path, monkeypatch, clean_first=True)
    assert check["status"] == "DEGRADED"
