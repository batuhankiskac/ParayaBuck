"""Audit wave 2 fix D1: halt release on reset, risk-epoch and peak races.

Covers R2-1-1/2/3, R2-6-1/3/4, C2-4-1/2/5/6 and F2-1-6. Each test uses a
tmp_path ledger and stubs only what the method under test does not reach.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import EngineConfig
from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.risk_peak import RiskPeakService, RiskStatePersistenceError
from parayabuck.portfolio.runtime_service import PortfolioStateRepository
from parayabuck.runtime import EngineRuntime

CLEAN = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}
ACCOUNT = AccountSnapshot(10_000, 10_000, 10_000, 0)


@pytest.fixture(autouse=True)
def _no_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **fields: events.append((kind, fields)))
    return events


def _runtime(tmp_path: Path) -> tuple[EngineRuntime, ExecutionLedger]:
    """A running runtime shell whose reset path passes against a clean broker."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: dict(CLEAN), account=lambda: ACCOUNT)
    runtime.ledger = ledger
    runtime.app_config = SimpleNamespace(runtime=SimpleNamespace(decision_db_path=str(tmp_path / "decisions.sqlite3")))
    runtime.config = EngineConfig()
    runtime._state_lock = threading.RLock()
    runtime._risk_state_lock = threading.Lock()
    runtime._reconcile_lock = threading.Lock()
    runtime._circuit_breaker_reason = None
    runtime._halt_reasons = []
    runtime._state = "running"
    runtime._running = True
    runtime._warming_up = False
    runtime._workers = {}
    runtime._threads = []
    runtime._threads_lock = threading.Lock()
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._portfolio_runtimes = ()
    runtime._market_entry_halts = {}
    runtime._entry_risk_latch = {}
    runtime._reconcile_failed = False
    runtime._last_account_at = None
    runtime._last_reconciliation_at = None
    runtime._last_clean_reconciliation_at = None
    runtime._generic_reconcile_failures = 0
    runtime._transient_reconcile_failures = 0
    runtime._reconcile_pending_reason = None
    runtime._reconcile_pending_seq = 0
    runtime._deferred_portfolio_batches = {}
    runtime._publish_account_snapshot = lambda account: None  # type: ignore[method-assign]
    runtime._resume_startup_rebalances = lambda **_kwargs: None  # type: ignore[method-assign]
    runtime._persist_risk_state = lambda _account: None  # type: ignore[method-assign]
    return runtime, ledger


def _incident_reasons(ledger: ExecutionLedger) -> set[str]:
    return {incident.reason for incident in ledger.load_safety_incidents("acct")}


# --------------------------------------------------------------------------- #
# R2-1-1: a reset releases only the rearmed reasons that are not in force
# --------------------------------------------------------------------------- #


def test_reset_keeps_a_rearmed_reason_that_is_still_in_force(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    runtime._raise_halt("ownership_drift")
    runtime._raise_halt("trade_update_callback_failure")

    runtime.reset_safety_lock()

    # The live trade-update failure is not verified by an ownership reset.
    assert runtime._circuit_breaker_reason == "trade_update_callback_failure"
    assert runtime._state == "halted"
    assert _incident_reasons(ledger) == {"trade_update_callback_failure"}
    ledger.close()


def test_reset_releases_a_leftover_rearmed_row_that_is_not_in_force(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    # A row from an earlier worker state: not in memory, so nothing is live.
    # (decision_worker_crashed is no longer re-armed at restart, W05 / R3-4-1.)
    ledger.save_safety_incident("acct", "final_market_event_queue_overflow")
    runtime._raise_halt("ownership_drift")

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    assert _incident_reasons(ledger) == set()
    ledger.close()


# --------------------------------------------------------------------------- #
# R2-1-2: a same-reason halt raised during the reset is not lifted
# --------------------------------------------------------------------------- #


def test_same_reason_halt_raised_during_the_reset_stays_in_force(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    runtime._raise_halt("ownership_drift")

    def reconcile_with_a_fresh_halt() -> dict[str, list[object]]:
        # The same halt raised again while the broker call of the reset runs.
        runtime._raise_halt("ownership_drift")
        return dict(CLEAN)

    runtime.broker.reconcile = reconcile_with_a_fresh_halt
    with pytest.raises(ValueError, match="raised again"):
        runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason == "ownership_drift"
    assert runtime._state == "halted"
    assert _incident_reasons(ledger) == {"ownership_drift"}
    ledger.close()


# --------------------------------------------------------------------------- #
# R2-1-3: a reset that fails after the release never leaves a wedged halt
# --------------------------------------------------------------------------- #


def test_reset_failing_after_the_release_halts_again_and_can_be_reset(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    runtime._raise_halt("ownership_drift")

    def broken_resume(**_kwargs: object) -> None:
        raise RuntimeError("rebalance resume broke")

    runtime._resume_startup_rebalances = broken_resume  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="after the release"):
        runtime.reset_safety_lock()

    # Halted with a breaker the operator can reset, not "halted" with no breaker.
    assert runtime._circuit_breaker_reason == "portfolio_runtime_failure"
    assert runtime._state == "halted"

    runtime._resume_startup_rebalances = lambda **_kwargs: None  # type: ignore[method-assign]
    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    assert _incident_reasons(ledger) == set()
    ledger.close()


# --------------------------------------------------------------------------- #
# F2-1-6: a symbol whose counts agree again drops its surplus alert key
# --------------------------------------------------------------------------- #


def test_surplus_alert_key_is_dropped_when_the_counts_agree_again(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    repo = PortfolioStateRepository(str(tmp_path / "ledger.sqlite3"), store=ledger._store)
    runtime._portfolio_repository = repo
    repo._surplus_alerts = {  # type: ignore[attr-defined]
        ("acct", Market.EQUITY.value, "QLD", 10.0, 15.0),
        ("acct", Market.EQUITY.value, "SPY", 1.0, 2.0),
    }

    runtime._clear_portfolio_surplus_alert("QLD", Market.EQUITY)

    assert repo._surplus_alerts == {("acct", Market.EQUITY.value, "SPY", 1.0, 2.0)}  # type: ignore[attr-defined]
    ledger.close()


# --------------------------------------------------------------------------- #
# R2-6-1 / R2-6-4: the peak sidecar fails closed and picks the newer epoch
# --------------------------------------------------------------------------- #


def test_sidecar_bound_to_another_account_fails_closed(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "peak.sqlite3"))
    path = tmp_path / "paper_equity_peak.json"
    original = json.dumps({"account_id": "acct-A", "peak_equity": 120000.0, "epoch": 0})
    path.write_text(original)
    service = RiskPeakService(paper=True, ledger=ledger, peak_state_path=path)

    with pytest.raises(RiskStatePersistenceError):
        service.ensure_reconciled("acct-B")

    # The foreign sidecar is neither used nor overwritten.
    assert path.read_text() == original
    ledger.close()


def test_legacy_sidecar_takes_the_newer_epoch_not_the_higher_peak(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "peak.sqlite3"))
    repo = PortfolioStateRepository(str(tmp_path / "peak.sqlite3"), store=ledger._store)
    repo.reset_high_water("acct", 100_000.0, epoch=2)
    path = tmp_path / "paper_equity_peak.json"
    # Unbound legacy sidecar: a higher peak from an older epoch is stale.
    path.write_text(json.dumps({"peak_equity": 150_000.0, "epoch": 1}))
    service = RiskPeakService(paper=True, ledger=ledger, peak_state_path=path)

    service.ensure_reconciled("acct")

    assert service.peak_epoch == 2
    assert service.peak_equity == 100_000.0
    ledger.close()


# --------------------------------------------------------------------------- #
# C2-4-1 / C2-4-5: account() reads equity under the peak lock and stamps its epoch
# --------------------------------------------------------------------------- #


class _FakeAccount:
    def __init__(self, account_id: str, equity: float) -> None:
        self.equity = equity
        self.last_equity = equity
        self.cash = equity
        self.account_blocked = False
        self.trading_blocked = False
        self.trade_suspended_by_user = False
        self.id = account_id
        self.non_marginable_buying_power = None


def _broker(tmp_path: Path, ledger: ExecutionLedger, peak: Path, positions_hook=None) -> AlpacaBroker:
    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak, ledger=ledger)
    account = _FakeAccount("acct", 100_000.0)

    class FakeClient:
        _session = None

        def get_account(self) -> _FakeAccount:
            return account

        def get_all_positions(self) -> list[object]:
            if positions_hook is not None:
                positions_hook(broker)
            return []

    broker._client = FakeClient()
    return broker


def test_snapshot_read_across_a_reset_is_discarded_not_merged(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    peak = tmp_path / "paper_equity_peak.json"

    def reset_commits_mid_read(broker: AlpacaBroker) -> None:
        # A risk-epoch reset commits while this poll's broker reads are in flight.
        broker._get_peak_service().reset_peak_equity(60_000.0, "acct")

    broker = _broker(tmp_path, ledger, peak, positions_hook=reset_commits_mid_read)
    with pytest.raises(RuntimeError, match="Risk epoch changed"):
        broker.account()

    # The pre-reset equity of 100k did not re-raise the new 60k baseline.
    assert broker._peak_equity == 60_000.0
    assert broker._peak_epoch == 1
    ledger.close()


def test_snapshot_carries_the_epoch_of_the_peak_it_was_measured_in(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    broker = _broker(tmp_path, ledger, tmp_path / "paper_equity_peak.json")

    snapshot = broker.account()

    assert snapshot.peak_equity == 100_000.0
    assert snapshot.peak_epoch == broker._peak_epoch == 0
    ledger.close()


# --------------------------------------------------------------------------- #
# C2-4-2 / R2-6-3: the persisted high-water is fenced by epoch
# --------------------------------------------------------------------------- #


def test_stale_epoch_observation_cannot_raise_the_rebased_high_water(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    repo = PortfolioStateRepository(str(tmp_path / "ledger.sqlite3"), store=ledger._store)
    repo.reset_high_water("acct", 80_000.0, epoch=1)

    # A pre-reset snapshot (epoch 0) must not re-raise the rebased baseline.
    assert repo.update_high_water("acct", 150_000.0, epoch=0) == 80_000.0
    assert repo.high_water("acct") == 80_000.0
    # The current epoch still max-merges, and the legacy call is unchanged.
    assert repo.update_high_water("acct", 90_000.0, epoch=1) == 90_000.0
    assert repo.update_high_water("acct", 85_000.0) == 90_000.0
    ledger.close()


def test_reset_high_water_adopts_the_sidecar_epoch(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    repo = PortfolioStateRepository(str(tmp_path / "ledger.sqlite3"), store=ledger._store)

    repo.reset_high_water("acct", 80_000.0, epoch=5)

    row = ledger.connection.execute("SELECT epoch FROM portfolio_high_water WHERE account_id = ?", ("acct",)).fetchone()
    assert row[0] == 5
    ledger.close()


def test_high_water_ignores_a_row_from_a_superseded_epoch(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    repo = PortfolioStateRepository(str(tmp_path / "ledger.sqlite3"), store=ledger._store)
    repo.reset_high_water("acct", 150_000.0, epoch=1)

    # Sidecar at epoch 2: the epoch-1 row is a superseded baseline.
    assert repo.high_water("acct", epoch=2) == 0.0
    assert repo.high_water("acct", epoch=1) == 150_000.0
    assert repo.high_water("acct") == 150_000.0
    ledger.close()


def test_risk_epoch_reset_reports_its_epoch_in_the_result_and_alert(
    tmp_path: Path, _no_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    runtime, ledger = _runtime(tmp_path)
    service = RiskPeakService(paper=True, ledger=ledger, peak_state_path=tmp_path / "paper_equity_peak.json")
    service.ensure_reconciled("acct")
    service.reset_peak_equity(100_000.0, "acct")  # epoch 1 holds the 100k peak
    drawn = AccountSnapshot(94_000, 94_000, 100_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(CLEAN),
        account=lambda: drawn,
        _get_peak_service=lambda: service,
    )

    baseline = runtime.start_new_risk_epoch()

    assert baseline == 94_000.0
    assert runtime._last_risk_epoch_reset == {"epoch": 2, "baseline_equity": 94_000.0, "changed": True}
    alerts_fields = [fields for kind, fields in _no_alerts if kind == "risk_epoch_reset"]
    assert len(alerts_fields) == 1
    assert alerts_fields[0]["epoch"] == 2
    ledger.close()
