"""Regression tests for audit wave 2 worker D2 (R2-8-1/3/4, R2-9-2..10)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck import live_safety
from parayabuck.cli import _run_halt
from parayabuck.core.models import Market
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import PortfolioOrderSide
from parayabuck.live_safety import (
    LiveApprovalError,
    LiveOperatorApproval,
    LiveStartupProof,
    clear_operator_halt,
    operator_halt_active,
    read_operator_halt,
)
from parayabuck.runtime import EngineRuntime

SECRET = "audit2-fix-d2-test-secret-0123456789abcdef"


class _Recorder:
    """Stands in for the alerts module and the metrics store."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, kind: str, *, message: str, **fields: object) -> None:
        self.events.append((kind, {"message": message, **fields}))

    def record_event(self, kind: str) -> None:
        self.events.append((kind, {}))


# --- R2-8-1: manual LIVE approval is bound to the config fingerprint ---------


def _write_approval(path: Path, *, fingerprint: str, now: datetime) -> Path:
    approval = LiveOperatorApproval(
        operator_id="operator",
        account_id="live-acct",
        configuration_digests=("digest-a",),
        maximum_dollar_exposure=500.0,
        approved_at=now,
        expires_at=now + timedelta(minutes=15),
        nonce="nonce-1",
        config_fingerprint=fingerprint,
    ).signed(SECRET)
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")
    return path


def test_manual_approval_refuses_a_different_config_fingerprint(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    path = _write_approval(tmp_path / "approval.json", fingerprint="fp-a", now=now)
    kwargs = {"secret": SECRET, "account_id": "live-acct", "configuration_digests": ("digest-a",), "now": now}

    loaded = LiveOperatorApproval.load_verified(path, config_fingerprint="fp-a", **kwargs)
    assert loaded.config_fingerprint == "fp-a"
    with pytest.raises(LiveApprovalError, match="current settings"):
        LiveOperatorApproval.load_verified(path, config_fingerprint="fp-b", **kwargs)
    with pytest.raises(LiveApprovalError, match="current settings"):
        LiveOperatorApproval.load_verified(path, config_fingerprint="", **kwargs)


def test_approval_without_a_signed_fingerprint_is_refused(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    path = _write_approval(tmp_path / "approval.json", fingerprint="", now=now)
    with pytest.raises(LiveApprovalError, match="current settings"):
        LiveOperatorApproval.load_verified(
            path,
            secret=SECRET,
            account_id="live-acct",
            configuration_digests=("digest-a",),
            config_fingerprint="fp-a",
            now=now,
        )


def test_editing_the_fingerprint_in_the_file_breaks_the_signature(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    path = _write_approval(tmp_path / "approval.json", fingerprint="fp-a", now=now)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["config_fingerprint"] = "fp-b"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(LiveApprovalError, match="signature"):
        LiveOperatorApproval.load_verified(
            path,
            secret=SECRET,
            account_id="live-acct",
            configuration_digests=("digest-a",),
            config_fingerprint="fp-b",
            now=now,
        )


def test_live_start_is_refused_when_signal_cross_check_is_disabled() -> None:
    now = datetime.now(UTC)
    api_key, secret_key = "api-key", "secret-key"
    proof = LiveStartupProof(
        account_id="live-acct",
        credential_fingerprint=hashlib.sha256(f"{api_key}\0{secret_key}".encode()).hexdigest(),
        configuration_digests=("digest-a",),
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5),
    )
    fake = SimpleNamespace(
        _live_startup_proof=proof,
        api_key=api_key,
        secret_key=secret_key,
        app_config=SimpleNamespace(signal_cross_check=SimpleNamespace(enabled=False)),
    )
    with pytest.raises(RuntimeError, match="signal_cross_check.enabled is false"):
        EngineRuntime._verify_live_startup_proof(fake, ("digest-a",))


# --- R2-8-3: a restored drawdown halt waits for an operator risk-epoch reset -


class _IncidentLedger:
    def __init__(self) -> None:
        self.saves: list[tuple[str, str]] = []
        self.clears: list[tuple[str, str | None]] = []

    def save_safety_incident(self, account_id: str, reason: str) -> None:
        self.saves.append((account_id, reason))

    def clear_safety_incident(self, account_id: str, reason: str | None = None) -> None:
        self.clears.append((account_id, reason))


def _latch_runtime(ledger: _IncidentLedger, *, restored: bool, monkeypatch: pytest.MonkeyPatch) -> EngineRuntime:
    recorder = _Recorder()
    monkeypatch.setattr("parayabuck.runtime.alerts", recorder)
    monkeypatch.setattr("parayabuck.runtime.runtime_metrics_store", recorder)
    runtime = object.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = ledger
    runtime.app_config = SimpleNamespace(risk=SimpleNamespace(max_drawdown_pct=0.20, daily_loss_limit_pct=0.10))
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._drawdown_halt_restored = restored
    return runtime


def test_restored_drawdown_halt_is_kept_when_the_limit_was_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _IncidentLedger()
    runtime = _latch_runtime(ledger, restored=True, monkeypatch=monkeypatch)
    account = SimpleNamespace(equity=100.0, daily_pnl=0.0, drawdown_pct=0.05)
    runtime._evaluate_entry_risk_latch(account)
    assert ledger.clears == []
    assert ledger.saves == [("acct", "portfolio_drawdown_halt")]
    assert runtime._entry_risk_latch[Market.EQUITY] == "portfolio_drawdown_halt"


def test_drawdown_recovery_in_this_process_still_clears_the_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _IncidentLedger()
    runtime = _latch_runtime(ledger, restored=False, monkeypatch=monkeypatch)
    account = SimpleNamespace(equity=100.0, daily_pnl=0.0, drawdown_pct=0.05)
    runtime._evaluate_entry_risk_latch(account)
    assert ledger.clears == [("acct", "portfolio_drawdown_halt")]
    assert ledger.saves == []
    assert runtime._entry_risk_latch[Market.EQUITY] is None


# --- R2-9-2: halt rows commit at synchronous=FULL ----------------------------


def test_halt_row_is_committed_at_full_synchronous_and_the_level_is_restored(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "decisions.db"))
    try:
        statements: list[str] = []
        ledger.connection.set_trace_callback(statements.append)
        ledger.save_safety_incident("acct", "ambiguous_order_state")
        ledger.connection.set_trace_callback(None)
        normalized = [" ".join(text.split()).lower() for text in statements]
        sync_full = normalized.index("pragma synchronous=full")
        insert = next(i for i, text in enumerate(normalized) if text.startswith("insert into safety_incidents"))
        sync_normal = normalized.index("pragma synchronous=normal")
        assert sync_full < insert < sync_normal
        assert ledger.connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert [item.reason for item in ledger.load_safety_incidents("acct")] == ["ambiguous_order_state"]
    finally:
        ledger.close()


# --- R2-9-3: an unreadable marker is a halt -----------------------------------


def test_operator_halt_fails_closed_when_the_marker_cannot_be_stat(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "OPERATOR_HALT"
    assert operator_halt_active(marker) is False
    real_lstat = os.lstat

    def denied(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(marker):
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(live_safety.os, "lstat", denied)
    assert operator_halt_active(marker) is True
    assert read_operator_halt(marker) == {"reason": "unreadable halt marker", "created_at": None, "user": None}


# --- R2-9-5 / R2-9-10: halt persistence retry, alert and release race --------


class _HaltLedger:
    def __init__(self, *, failures: int = 0, on_save=None) -> None:
        self.failures = failures
        self.on_save = on_save
        self.saves: list[tuple[str, str]] = []
        self.clears: list[tuple[str, str | None]] = []

    def save_safety_incident(self, account_id: str, reason: str) -> None:
        if self.failures:
            self.failures -= 1
            raise sqlite3.OperationalError("disk I/O error")
        self.saves.append((account_id, reason))
        if self.on_save is not None:
            self.on_save()

    def clear_safety_incident(self, account_id: str, reason: str | None = None) -> None:
        self.clears.append((account_id, reason))


def _halt_runtime(ledger: _HaltLedger, active: tuple[str, ...]) -> EngineRuntime:
    runtime = object.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = ledger
    runtime._state_lock = threading.RLock()
    runtime._halt_reasons = list(active)
    runtime._circuit_breaker_reason = active[0] if active else None
    return runtime


def test_one_failed_halt_write_is_retried_without_an_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("parayabuck.runtime.alerts", recorder)
    ledger = _HaltLedger(failures=1)
    runtime = _halt_runtime(ledger, ("trade_update_callback_failure",))
    runtime._save_halt_incident_best_effort("trade_update_callback_failure")
    assert ledger.saves == [("acct", "trade_update_callback_failure")]
    assert recorder.events == []
    assert ledger.clears == []


def test_halt_that_cannot_be_persisted_is_alerted(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("parayabuck.runtime.alerts", recorder)
    ledger = _HaltLedger(failures=5)
    runtime = _halt_runtime(ledger, ("trade_update_callback_failure",))
    runtime._save_halt_incident_best_effort("trade_update_callback_failure")
    assert ledger.saves == []
    assert [(kind, fields["reason"]) for kind, fields in recorder.events] == [
        ("circuit_breaker", "halt_persist_failed")
    ]


def test_a_halt_released_during_its_write_is_not_left_in_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parayabuck.runtime.alerts", _Recorder())
    holder: dict[str, EngineRuntime] = {}

    def release_by_reset() -> None:
        # A reset releases the reason in memory before it clears the row.
        holder["runtime"]._halt_reasons = []
        holder["runtime"]._circuit_breaker_reason = None

    ledger = _HaltLedger(on_save=release_by_reset)
    runtime = _halt_runtime(ledger, ("ownership_drift",))
    holder["runtime"] = runtime
    runtime._save_halt_incident_best_effort("ownership_drift")
    assert ledger.clears == [("acct", "ownership_drift")]


def test_a_halt_still_in_force_keeps_its_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parayabuck.runtime.alerts", _Recorder())
    ledger = _HaltLedger()
    runtime = _halt_runtime(ledger, ("ownership_drift",))
    runtime._save_halt_incident_best_effort("ownership_drift")
    assert ledger.saves == [("acct", "ownership_drift")]
    assert ledger.clears == []


# --- R2-9-4: the order gates read the halt marker ----------------------------


def test_order_gates_hold_every_order_while_the_operator_marker_is_present(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    marker = runtime_dir / "OPERATOR_HALT"
    marker.write_text("{}", encoding="utf-8")
    runtime = object.__new__(EngineRuntime)
    runtime._running = True
    runtime._circuit_breaker_reason = None
    runtime._startup_committed = True
    runtime._warming_up = False
    runtime._reconcile_failed = False
    runtime.app_config = SimpleNamespace(runtime=SimpleNamespace(decision_db_path=str(runtime_dir / "decisions.db")))
    buy = SimpleNamespace(side=PortfolioOrderSide.BUY)
    sell = SimpleNamespace(side=PortfolioOrderSide.SELL)

    assert runtime._advance_submit_block_reason(Market.EQUITY, buy) == "operator_halt"
    assert runtime._advance_submit_block_reason(Market.EQUITY, sell) == "operator_halt"
    assert runtime._exit_sell_retry_blocked(Market.EQUITY) is True

    marker.unlink()
    assert runtime._advance_submit_block_reason(Market.EQUITY, sell) is None


# --- R2-9-6 / R2-9-7 / R2-9-8: the halt and resume commands -------------------


def _halt_config(runtime_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(decision_db_path=str(runtime_dir / "decisions.db"), mode=SimpleNamespace(value="paper"))
    )


def test_resume_refuses_a_marker_it_cannot_remove_and_keeps_the_halt(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    (runtime_dir / "OPERATOR_HALT").mkdir(parents=True)
    assert _run_halt(_halt_config(runtime_dir), SimpleNamespace(command="resume")) == 1
    err = capsys.readouterr().err
    assert "NOT cleared" in err
    assert "parayabuck resume" in err
    assert (runtime_dir / "OPERATOR_HALT").is_dir()


def test_clear_operator_halt_names_the_remedy_for_a_directory(tmp_path: Path) -> None:
    marker = tmp_path / "OPERATOR_HALT"
    marker.mkdir()
    with pytest.raises(IsADirectoryError, match="remove it by hand"):
        clear_operator_halt(marker)
    assert marker.is_dir()


def test_halt_states_its_scope_and_resume_mentions_the_persisted_row(tmp_path: Path, capsys) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = _halt_config(runtime_dir)
    assert _run_halt(config, SimpleNamespace(command="halt", reason="investigating")) == 0
    assert "Scope: only the engine that uses" in capsys.readouterr().out

    assert _run_halt(config, SimpleNamespace(command="resume")) == 0
    out = capsys.readouterr().out
    assert "operator_halt incident is not removed by resume" in out
    assert not (runtime_dir / "OPERATOR_HALT").exists()
