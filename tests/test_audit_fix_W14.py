"""Regression tests for the W14 audit fixes (risk peak epochs, LIVE approvals, halt marker).

All state lives under tmp_path; nothing touches the network, credentials or
production paths.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import parayabuck.execution.risk_peak as risk_peak
import parayabuck.runtime as runtime_module
from parayabuck.config import Config, RiskLimits
from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.alpaca_models import RiskStatePersistenceError
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.risk_peak import RiskPeakService
from parayabuck.live_safety import (
    HALT_MARKER_MAX_BYTES,
    LiveApprovalError,
    LiveOperatorApproval,
    StandingLiveAuthorization,
    config_fingerprint,
    read_operator_halt,
)
from parayabuck.risk.state import RiskState
from parayabuck.runtime import EngineRuntime

_REPO = Path(__file__).resolve().parents[1]
_KEY = "k" * 40
_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}


def _sidecar(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# --- BK-1 / P7-2: sidecar commit point, rollback and expected-epoch guard --------


class _ServiceBroker:
    """The AlpacaBroker seam (_get_peak_service, _peak_lock, account peak update) over a real RiskPeakService."""

    account_id = "acct"

    def __init__(self, service: RiskPeakService, equity: float) -> None:
        self._service = service
        self._peak_lock = threading.Lock()
        self.equity = equity

    def _get_peak_service(self) -> RiskPeakService:
        return self._service

    def reconcile(self) -> dict[str, list[str]]:
        return dict(_CLEAN_RECONCILIATION)

    def account(self) -> AccountSnapshot:
        with self._peak_lock:
            self._service.ensure_reconciled("acct")
            if self.equity > self._service.peak_equity:
                self._service.peak_equity = self.equity
                self._service.save_peak_equity("acct")
            peak = self._service.peak_equity
        return AccountSnapshot(cash=self.equity, equity=self.equity, peak_equity=peak, daily_pnl=0.0)


class _Repository:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.resets: list[tuple[str, float]] = []

    def reset_high_water(self, account_id: str, equity: float) -> None:
        if self.fail:
            raise RuntimeError("database is locked")
        self.resets.append((account_id, equity))


def _epoch_setup(tmp_path: Path, *, repository: _Repository | None = None) -> tuple[EngineRuntime, _ServiceBroker, Path, ExecutionLedger]:
    """A running engine shell on a real ledger and a real peak sidecar, drawn down to 94k from a 100k peak."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    sidecar = tmp_path / "paper_equity_peak.json"
    service = RiskPeakService(paper=True, ledger=ledger, peak_state_path=sidecar)
    broker = _ServiceBroker(service, equity=100_000.0)
    broker.account()  # bootstrap: sidecar records the 100k peak at epoch 0
    broker.equity = 94_000.0
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = broker  # type: ignore[assignment]
    runtime.ledger = ledger
    runtime.app_config = Config(risk=RiskLimits(daily_loss_limit_pct=0.005, max_drawdown_pct=0.05))
    runtime._risk_state_lock = threading.Lock()  # type: ignore[attr-defined]
    runtime._state_lock = threading.RLock()  # type: ignore[attr-defined]
    runtime._circuit_breaker_reason = None  # type: ignore[attr-defined]
    runtime._entry_risk_latch = {Market.EQUITY: "portfolio_drawdown_halt"}  # type: ignore[attr-defined]
    runtime._running = True  # type: ignore[attr-defined]
    runtime._portfolio_repository = repository or _Repository()  # type: ignore[attr-defined]
    ledger.save_safety_incident("acct", "portfolio_drawdown_halt")
    return runtime, broker, sidecar, ledger


def test_failed_sidecar_write_leaves_peak_epoch_halt_and_database_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _Repository()
    runtime, broker, sidecar, ledger = _epoch_setup(tmp_path, repository=repository)
    before = sidecar.read_text(encoding="utf-8")

    def failing_write(path: Any, payload: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(risk_peak, "atomic_write", failing_write)
    with pytest.raises(OSError, match="disk full"):
        runtime.start_new_risk_epoch()

    service = broker._service
    assert service.peak_equity == pytest.approx(100_000.0)
    assert service.peak_epoch == 0
    assert sidecar.read_text(encoding="utf-8") == before
    assert repository.resets == []
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["portfolio_drawdown_halt"]
    ledger.close()


def test_database_failure_after_the_sidecar_commit_rolls_the_peak_back(tmp_path: Path) -> None:
    runtime, broker, sidecar, ledger = _epoch_setup(tmp_path, repository=_Repository(fail=True))

    with pytest.raises(RuntimeError, match="database is locked"):
        runtime.start_new_risk_epoch()

    service = broker._service
    assert service.peak_equity == pytest.approx(100_000.0)
    assert service.peak_epoch == 0
    assert _sidecar(sidecar)["peak_equity"] == pytest.approx(100_000.0)
    assert _sidecar(sidecar)["epoch"] == 0
    # The halt the operator still sees is re-recorded, not left cleared.
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["portfolio_drawdown_halt"]
    ledger.close()


def test_successful_reset_commits_the_sidecar_and_records_the_pre_reset_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _Repository()
    runtime, broker, sidecar, ledger = _epoch_setup(tmp_path, repository=repository)
    emitted: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        runtime_module.alerts,
        "emit",
        lambda kind, *, message, **fields: emitted.append((kind, dict(fields, message=message))),
    )

    baseline = runtime.start_new_risk_epoch()

    assert baseline == pytest.approx(94_000.0)
    assert _sidecar(sidecar) == {"account_id": "acct", "peak_equity": 94_000.0, "epoch": 1}
    assert repository.resets == [("acct", 94_000.0)]
    assert ledger.load_safety_incidents("acct") == ()
    reset_events = [fields for kind, fields in emitted if kind == "risk_epoch_reset"]
    assert len(reset_events) == 1
    assert reset_events[0]["previous_peak_equity"] == pytest.approx(100_000.0)
    assert reset_events[0]["previous_drawdown_pct"] == pytest.approx(0.06)
    ledger.close()


def test_reset_is_a_no_op_once_the_expected_epoch_is_superseded(tmp_path: Path) -> None:
    sidecar = tmp_path / "peak.json"
    service = RiskPeakService(peak_state_path=sidecar)
    service.peak_equity, service.peak_epoch = 100_000.0, 0
    service.peak_account_id, service.peak_reconciled = "acct", True
    assert service.reset_peak_equity(94_000.0, "acct", expected_epoch=0) is True
    assert (service.peak_equity, service.peak_epoch) == (94_000.0, 1)

    # A retry (or a concurrent reset) that expected epoch 0 finds epoch 1 active:
    # success without a second re-baseline at whatever equity is current.
    assert service.reset_peak_equity(70_000.0, "acct", expected_epoch=0) is False
    assert (service.peak_equity, service.peak_epoch) == (94_000.0, 1)
    assert _sidecar(sidecar) == {"account_id": "acct", "peak_equity": 94_000.0, "epoch": 1}


def test_reset_fails_closed_when_the_active_epoch_is_behind_the_expected_one(tmp_path: Path) -> None:
    sidecar = tmp_path / "peak.json"
    service = RiskPeakService(peak_state_path=sidecar)
    service.peak_equity, service.peak_epoch = 100_000.0, 0
    service.peak_account_id, service.peak_reconciled = "acct", True

    with pytest.raises(RiskStatePersistenceError, match="regressed"):
        service.reset_peak_equity(94_000.0, "acct", expected_epoch=3)
    assert (service.peak_equity, service.peak_epoch) == (100_000.0, 0)
    assert not sidecar.exists()


def test_restore_peak_state_rewrites_the_sidecar_and_memory(tmp_path: Path) -> None:
    sidecar = tmp_path / "peak.json"
    service = RiskPeakService(peak_state_path=sidecar)
    service.peak_equity, service.peak_epoch = 100_000.0, 0
    service.peak_account_id, service.peak_reconciled = "acct", True
    prior = (service.peak_equity, service.peak_epoch, service.peak_account_id, service.peak_reconciled)
    service.reset_peak_equity(94_000.0, "acct")

    service.restore_peak_state(prior)

    assert (service.peak_equity, service.peak_epoch) == (100_000.0, 0)
    assert _sidecar(sidecar) == {"account_id": "acct", "peak_equity": 100_000.0, "epoch": 0}


# --- R8-5: bootstrap is announced and recorded ----------------------------------


def test_bootstrap_without_sidecar_or_history_alerts_and_records_the_first_equity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    sidecar = tmp_path / "peak.json"
    alerts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        risk_peak,
        "emit_execution_alert",
        lambda kind, *, message, **fields: alerts.append(dict(fields, kind=kind, message=message)),
    )
    service = RiskPeakService(paper=True, ledger=ledger, peak_state_path=sidecar)
    service.ensure_reconciled("acct")
    assert service.peak_equity == 0.0  # nothing to bootstrap from

    service.peak_equity = 97_000.0  # what account() does with the first observed equity
    service.save_peak_equity("acct")

    assert len(alerts) == 1
    assert alerts[0]["kind"] == "paper_incident"
    assert alerts[0]["category"] == "peak_bootstrap"
    assert alerts[0]["baseline_equity"] == pytest.approx(97_000.0)
    assert _sidecar(sidecar) == {"account_id": "acct", "peak_equity": 97_000.0, "epoch": 0}

    service.save_peak_equity("acct")
    assert len(alerts) == 1  # announced once
    ledger.close()


def test_restart_with_a_sidecar_does_not_announce_a_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    sidecar = tmp_path / "peak.json"
    sidecar.write_text(json.dumps({"account_id": "acct", "peak_equity": 100_000.0, "epoch": 0}), encoding="utf-8")
    alerts: list[str] = []
    monkeypatch.setattr(risk_peak, "emit_execution_alert", lambda kind, **fields: alerts.append(kind))
    service = RiskPeakService(paper=True, ledger=ledger, peak_state_path=sidecar)
    service.ensure_reconciled("acct")
    service.peak_equity = 101_000.0
    service.save_peak_equity("acct")
    assert alerts == []
    ledger.close()


# --- BK-9: a risk_state row without its peak fails closed ------------------------


def test_risk_state_row_without_peak_equity_fails_closed(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_risk_state(
        RiskState(
            account_id="acct",
            session_date=datetime.now(UTC).date(),
            day_start_equity=100_000.0,
            peak_equity=100_000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees=0.0,
            circuit_breaker_reason=None,
            updated_at=datetime.now(UTC),
            peak_epoch=0,
        )
    )
    service = RiskPeakService(paper=True, ledger=ledger, peak_state_path=tmp_path / "peak.json")
    assert service.query_persisted_peak("acct") == (100_000.0, 0)

    ledger.connection.execute("UPDATE risk_state SET payload = ?", (json.dumps({"peak_epoch": 0}),))
    ledger.connection.commit()

    with pytest.raises(RiskStatePersistenceError, match="lacks peak_equity"):
        service.query_persisted_peak("acct")
    ledger.close()


# --- M-8 / S8-7: the fingerprint covers trading-relevant sections only -----------


def test_fingerprint_ignores_unrelated_sections_and_tracks_trading_settings() -> None:
    base_config = Config()
    base = config_fingerprint(base_config)

    unrelated = [
        replace(base_config, app=replace(base_config.app, log_level="DEBUG")),
        replace(base_config, observability=replace(base_config.observability, journal_path="elsewhere.sqlite3")),
        replace(base_config, backtest=replace(base_config.backtest, output_dir="elsewhere")),
    ]
    for config in unrelated:
        assert config_fingerprint(config) == base

    changed = replace(base_config, risk=replace(base_config.risk, max_drawdown_pct=0.5))
    assert config_fingerprint(changed) != base


# --- S-1: the tracked placeholder approval key is never accepted ----------------


def _tracked_placeholder() -> str:
    for line in (_REPO / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("PARAYABUCK_OPERATOR_APPROVAL_KEY="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("tracked .env.example has no operator approval key line")


def _approval(now: datetime) -> LiveOperatorApproval:
    return LiveOperatorApproval(
        operator_id="owner",
        account_id="acct",
        configuration_digests=("digest-a",),
        maximum_dollar_exposure=500.0,
        approved_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        nonce="n1",
    )


def test_tracked_placeholder_key_cannot_sign_or_verify_a_live_approval(tmp_path: Path) -> None:
    placeholder = _tracked_placeholder()
    assert len(placeholder) >= 32  # it passes a length-only check, which is the bug
    now = datetime.now(UTC)
    approval = _approval(now)

    with pytest.raises(ValueError, match="placeholder"):
        approval.calculated_signature(placeholder)
    with pytest.raises(ValueError, match="placeholder"):
        approval.calculated_signature("REPLACE_WITH_something_else_entirely_0123456789")

    # A file signed with a real key does not verify under the placeholder ...
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval.signed(_KEY).to_dict()), encoding="utf-8")
    with pytest.raises(LiveApprovalError, match="unusable"):
        LiveOperatorApproval.load_verified(
            path, secret=placeholder, account_id="acct", configuration_digests=("digest-a",), now=now
        )
    # ... and the same file verifies with the real key (the check is not a blanket refusal).
    assert LiveOperatorApproval.load_verified(
        path, secret=_KEY, account_id="acct", configuration_digests=("digest-a",), now=now
    ).nonce == "n1"


def test_tracked_placeholder_key_cannot_sign_a_standing_authorization() -> None:
    now = datetime.now(UTC)
    authorization = StandingLiveAuthorization(
        operator_id="owner",
        account_id="acct",
        configuration_digests=("digest-a",),
        config_fingerprint="fp",
        maximum_dollar_exposure=500.0,
        trading_host="host-a",
        approved_at=now,
        expires_at=now + timedelta(days=1),
        nonce="n1",
    )
    with pytest.raises(ValueError, match="placeholder"):
        authorization.signed(_tracked_placeholder())


# --- S-4: non-ASCII signature or binding values are refused, not a TypeError ----


def test_non_ascii_signature_is_refused_as_an_approval_error(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    path = tmp_path / "approval.json"
    raw = _approval(now).signed(_KEY).to_dict()
    raw["signature"] = "sïgnature-é" * 4
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LiveApprovalError, match="signature is invalid"):
        LiveOperatorApproval.load_verified(
            path, secret=_KEY, account_id="acct", configuration_digests=("digest-a",), now=now
        )


def test_non_ascii_standing_values_are_refused_as_an_approval_error(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    authorization = StandingLiveAuthorization(
        operator_id="owner",
        account_id="acct",
        configuration_digests=("digest-a",),
        config_fingerprint="fp",
        maximum_dollar_exposure=500.0,
        trading_host="host-a",
        approved_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(days=1),
        nonce="n1",
    ).signed(_KEY)
    path = tmp_path / "standing.json"
    path.write_text(json.dumps(authorization.to_dict(), ensure_ascii=False), encoding="utf-8")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["signature"] = "ünïcode-signature" * 3
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LiveApprovalError, match="signature is invalid"):
        StandingLiveAuthorization.read_signed(path, secret=_KEY)

    with pytest.raises(LiveApprovalError, match="trading host"):
        authorization.verify_binding(
            configuration_digests=("digest-a",),
            config_fingerprint="fp",
            hard_dollar_cap=500.0,
            trading_host="hôst-é",
            now=now,
        )


# --- S9-6: the halt marker is read only as a bounded regular file ---------------


def test_halt_marker_regular_file_is_read(tmp_path: Path) -> None:
    marker = tmp_path / "OPERATOR_HALT"
    marker.write_text(json.dumps({"reason": "maintenance", "created_at": "t", "user": "owner"}), encoding="utf-8")
    assert read_operator_halt(marker) == {"reason": "maintenance", "created_at": "t", "user": "owner"}
    assert read_operator_halt(tmp_path / "absent") is None


def test_halt_marker_directory_symlink_and_oversize_still_halt_as_unreadable(tmp_path: Path) -> None:
    unreadable = {"reason": "unreadable halt marker", "created_at": None, "user": None}

    directory = tmp_path / "dir-marker"
    directory.mkdir()
    assert read_operator_halt(directory) == unreadable

    target = tmp_path / "real.json"
    target.write_text(json.dumps({"reason": "via link"}), encoding="utf-8")
    link = tmp_path / "link-marker"
    link.symlink_to(target)
    assert read_operator_halt(link) == unreadable

    oversize = tmp_path / "big-marker"
    oversize.write_text(" " * (HALT_MARKER_MAX_BYTES + 10) + json.dumps({"reason": "x"}), encoding="utf-8")
    assert read_operator_halt(oversize) == unreadable


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO marker needs mkfifo")
def test_halt_marker_fifo_halts_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo-marker"
    os.mkfifo(fifo)
    assert read_operator_halt(fifo) == {"reason": "unreadable halt marker", "created_at": None, "user": None}
