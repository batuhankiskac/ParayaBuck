"""execution_quality_report scopes its metrics to the active account (Q5 leftover)."""

from __future__ import annotations

import importlib.util
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "execution_quality_report_under_test", ROOT / "scripts" / "execution_quality_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ledger(path: Path, sessions: list[tuple[str, str]]) -> Path:
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE portfolio_runtime_sessions(session_id TEXT, account_id TEXT, started_at TEXT)")
        for number, (account, started) in enumerate(sessions):
            con.execute("INSERT INTO portfolio_runtime_sessions VALUES (?, ?, ?)", (f"s{number}", account, started))
    con.close()
    return path


def test_defaults_to_the_newest_session_account(tmp_path: Path) -> None:
    module = _load()
    db = _ledger(
        tmp_path / "runtime.sqlite3",
        [("old-acct", "2026-09-01T00:00:00+00:00"), ("new-acct", "2026-10-01T00:00:00+00:00")],
    )
    assert module.resolve_account_id(db, None) == "new-acct"
    assert module.resolve_account_id(db, "explicit") == "explicit"


def test_no_session_keeps_the_whole_database_view(tmp_path: Path) -> None:
    module = _load()
    assert module.resolve_account_id(_ledger(tmp_path / "runtime.sqlite3", []), None) is None
    assert module.resolve_account_id(tmp_path / "missing.sqlite3", None) is None


def test_main_passes_the_account_to_both_metric_queries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load()
    db = _ledger(tmp_path / "runtime.sqlite3", [("acct-1", "2026-10-01T00:00:00+00:00")])
    seen: dict[str, object] = {}

    def _metrics(name: str) -> Any:
        def _call(path: object, *, account_id: str | None = None) -> dict[str, object]:
            seen[name] = account_id
            return {"available": False}

        return _call

    monkeypatch.setattr(
        module,
        "cost_calibration",
        lambda *_: {
            "available": True,
            "configured": {"available": False},
            "observed": defaultdict(dict),
            "comparison": {},
        },
    )
    monkeypatch.setattr(module, "execution_metrics", _metrics("execution"))
    monkeypatch.setattr(module, "forward_metrics", _metrics("forward"))
    monkeypatch.setattr(module, "latest_decision_ids", lambda *_: {})
    monkeypatch.setattr(module, "_print_summary", lambda *_: None)

    assert module.main(["--db", str(db), "--config", str(ROOT / "config" / "settings.yaml")]) == 0
    assert seen == {"execution": "acct-1", "forward": "acct-1"}
