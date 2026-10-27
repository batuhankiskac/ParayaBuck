"""Audit wave 2 regressions for the rebalance hold and startup catch-up paths (worker A2).

Covers P2-7-1 (a batch whose advance completes a rebalance is parked, not lost),
P2-7-2 (a superseded rebalance stays open while its SELL is in flight), P2-7-4
(a leg non-terminal for days is closed as stuck with an alert), P2-6-1 (catch-up
runs the signal cross-check), P2-6-3 (catch-up after a digest change keeps the
prior decision), R2-8-2 (a rebalance decided under another digest never sends its
BUY), R2-8-5 (the cadence survives a digest change), P2-2-2 (the submit gate reaches
the bar and catch-up advances), P2-2-4 (a held session is parked), and the Q2-9-3
resume gate and Q2-9-4 catch-up-then-scheduled test. Synthetic fixtures only.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.portfolio import PortfolioOrderSide, PortfolioRiskLimits
from parayabuck.monitoring import alerts
from parayabuck.portfolio.models import RebalancePolicy
from parayabuck.portfolio.runtime_models import (
    REBALANCE_PHASE_COMPLETE,
    REBALANCE_PHASE_SELL_PENDING,
)
from parayabuck.portfolio.runtime_service import (
    REBALANCE_STATUS_CLOSED_UNRESOLVED,
    REBALANCE_STATUS_STUCK_LEGS_UNRESOLVED,
    PortfolioRuntime,
    PortfolioStateRepository,
)
from parayabuck.portfolio.signal_cross_check import STATUS_TREND_MISMATCH, CrossCheckOutcome
from parayabuck.runtime import EngineRuntime
from tests.test_audit2_fix_A1 import (
    _AFTER_NEXT_CLOSE,
    _NEXT_OPEN,
    _SESSION,
    _bars,
    _Broker,
    _buy_ready,
    _engine,
    _fill,
    _orders,
    _phase,
    _position,
    _seed_history,
    _sell_submitted,
    _Strategy,
    clock,
)

__all__ = ["clock"]

_BUY = PortfolioOrderSide.BUY


def _runtime_with(repository, broker, *, targets=None, risk_limits=None) -> PortfolioRuntime:
    """A PortfolioRuntime on the A1 fixture; different ``risk_limits`` is a different configuration digest."""
    settings: dict[str, object] = {"mode": "paper", "maximum_allocation": 0.50, "account_id": "acct"}
    if risk_limits is not None:
        settings["risk_limits"] = risk_limits
    return PortfolioRuntime(
        _Strategy(targets or {"AAPL": 0.15, "MSFT": 0.20}), ("AAPL", "MSFT"), broker, repository, **settings
    )


def _age_order(repository: PortfolioStateRepository, client_order_id: str, sent_at: datetime) -> None:
    with repository.store.lock:
        repository.store.connection.execute(
            "UPDATE portfolio_orders SET created_at = ? WHERE client_order_id = ?",
            (sent_at.isoformat(), client_order_id),
        )
        repository.store.connection.commit()


def _capture_alerts(monkeypatch) -> list[dict[str, object]]:
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(alerts, "emit", lambda *_args, **kwargs: emitted.append(kwargs))
    return emitted


# --- R2-8-5: the cadence survives a configuration-digest change ------------


def test_a_digest_change_keeps_the_cadence_of_the_last_decision(tmp_path, clock) -> None:
    """Without this the seed reseeds the high-water to the newest bar, so a missed session depends on catch-up."""
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    broker = _Broker()
    old = _runtime_with(repository, broker)
    first = old.process_closed_bars(_bars(_SESSION), force_scheduled=True)
    assert first.decision_id is not None

    changed = _runtime_with(repository, broker, risk_limits=PortfolioRiskLimits(maximum_positions=2))

    assert changed.configuration_digest != old.configuration_digest
    assert changed.previous_timestamp == _SESSION


# --- R2-8-2: a rebalance decided under another digest never sends its BUY ---


def test_a_rebalance_decided_under_another_digest_never_sends_its_buy(tmp_path, clock) -> None:
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")
    changed = _runtime_with(repository, broker, risk_limits=PortfolioRiskLimits(maximum_positions=2))

    assert changed._stale_rebalance_decision(_SESSION) == first.decision_id
    assert changed.advance_rebalance() is None

    assert _orders(broker, _BUY) == []
    assert _phase(repository, first.decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)


def test_the_stale_check_ignores_an_open_rebalance_of_the_same_digest_and_session(tmp_path, clock) -> None:
    """Control: the digest rule does not close a rebalance of the running configuration."""
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)

    assert runtime._stale_rebalance_decision(_SESSION) is None


# --- P2-7-2: a superseded rebalance stays open while its SELL is in flight ----


def test_a_superseded_rebalance_with_a_sell_in_flight_stays_open(tmp_path, clock) -> None:
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)

    runtime._close_superseded_rebalance(first.decision_id)

    assert _phase(repository, first.decision_id)[0] == REBALANCE_PHASE_SELL_PENDING
    assert _orders(broker, _BUY) == []


def test_a_superseded_rebalance_closes_once_its_sell_has_settled(tmp_path, clock) -> None:
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _fill(repository, sell)

    runtime._close_superseded_rebalance(first.decision_id)

    assert _phase(repository, first.decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)


# --- P2-7-4: a leg non-terminal for days is closed as stuck, with an alert ---


def test_a_leg_open_past_its_age_limit_is_closed_as_stuck_with_an_alert(tmp_path, clock, monkeypatch) -> None:
    emitted = _capture_alerts(monkeypatch)
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _age_order(repository, sell.client_order_id, clock["value"] - timedelta(days=5))

    result = runtime.advance_rebalance()

    assert result is not None and result.status == "rebalance_complete"
    assert _phase(repository, first.decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_STUCK_LEGS_UNRESOLVED)
    assert any(kwargs.get("category") == "stuck_legs_unresolved" for kwargs in emitted)
    assert runtime._superseded_sells_working() is False


def test_a_young_open_leg_still_holds_the_rebalance(tmp_path, clock) -> None:
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _age_order(repository, sell.client_order_id, clock["value"] - timedelta(days=1))

    assert runtime.advance_rebalance() is None
    assert _phase(repository, first.decision_id)[0] == REBALANCE_PHASE_SELL_PENDING


# --- P2-2-2: the submit gate reaches the bar and catch-up advances -----------


def test_the_bar_path_passes_the_submit_gate_to_its_advance(tmp_path, clock, monkeypatch) -> None:
    clock["value"] = _AFTER_NEXT_CLOSE
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    seen: list[object] = []
    original_advance = runtime.advance_rebalance

    def spy(**kwargs):
        seen.append(kwargs.get("submit_gate"))
        return original_advance(**kwargs)

    monkeypatch.setattr(runtime, "advance_rebalance", spy)

    def gate(order):
        return None

    runtime.process_closed_bars(_bars(_NEXT_OPEN), submit_gate=gate)

    assert seen and all(passed is gate for passed in seen)


def test_the_catch_up_passes_the_submit_gate_to_its_advance(tmp_path, clock, monkeypatch) -> None:
    clock["value"] = _AFTER_NEXT_CLOSE
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    broker = _Broker()
    runtime = _runtime_with(repository, broker)
    _seed_history(runtime, _SESSION)
    seen: list[object] = []
    original_advance = runtime.advance_rebalance

    def spy(**kwargs):
        seen.append(kwargs.get("submit_gate"))
        return original_advance(**kwargs)

    monkeypatch.setattr(runtime, "advance_rebalance", spy)

    def gate(order):
        return None

    flat = AccountSnapshot(100_000.0, 100_000.0, 100_000.0, 0.0, ())
    runtime.startup_catch_up(account=flat, submit_gate=gate)

    assert seen and all(passed is gate for passed in seen)


# --- P2-6-3: catch-up after a digest change keeps the prior decision ----------


def test_catch_up_after_a_digest_change_does_not_refuse_a_held_session(tmp_path, clock, monkeypatch) -> None:
    clock["value"] = _AFTER_NEXT_CLOSE
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    broker = _Broker()
    old = _runtime_with(repository, broker, targets={"AAPL": 0.40})
    prior = old.process_closed_bars(_bars(_SESSION), force_scheduled=True)
    assert prior.decision_id is not None
    # The period start is what makes the session a recovered boundary.
    monkeypatch.setattr(RebalancePolicy, "is_period_start", lambda self, previous, current: True)

    changed = _runtime_with(repository, broker, risk_limits=PortfolioRiskLimits(maximum_positions=2))
    _seed_history(changed, _SESSION, _NEXT_OPEN)
    held = AccountSnapshot(60_000.0, 100_000.0, 100_000.0, 0.0, (_position("AAPL", 400),))
    result = changed.startup_catch_up(account=held)

    assert result.status not in {"already_invested", "catch_up_superseded"}


# --- P2-2-4 and P2-7-1: a held session is parked for the bounded retry -------


@pytest.fixture
def parking(monkeypatch):
    """Keep the deferral window open and record the alerts it raises."""
    monkeypatch.setattr(
        EngineRuntime,
        "_decision_session_close",
        staticmethod(lambda session: datetime(2099, 1, 1, 21, tzinfo=UTC)),
    )
    return _capture_alerts(monkeypatch)


def test_a_session_held_by_an_open_rebalance_is_parked(tmp_path, clock, parking) -> None:
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    engine = _engine(repository, runtime, broker)

    assert engine._park_held_session(runtime, _bars(_NEXT_OPEN), "rebalance_in_progress") is True
    assert (Market.EQUITY, _NEXT_OPEN) in engine._deferred_portfolio_batches


def test_a_completed_advance_with_no_decision_for_the_session_is_parked(tmp_path, clock, parking) -> None:
    """P2-7-1: the BUY that completed the rebalance leaves the session to be planned on the retry."""
    runtime, repository, broker, first = _buy_ready(tmp_path)
    engine = _engine(repository, runtime, broker)

    assert engine._park_held_session(runtime, _bars(_NEXT_OPEN), "rebalance_complete") is True


def test_a_session_already_decided_or_not_held_is_not_parked(tmp_path, clock, parking) -> None:
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    engine = _engine(repository, runtime, broker)

    assert engine._park_held_session(runtime, _bars(_SESSION), "rebalance_in_progress") is False
    assert engine._park_held_session(runtime, _bars(_NEXT_OPEN), "submitted") is False
    assert not getattr(engine, "_deferred_portfolio_batches", {})


# --- Q2-9-3: restart resume honours the circuit breaker, then sends the held BUY once --


def test_restart_resume_holds_the_buy_under_a_breaker_and_sends_it_once_clear(tmp_path, clock) -> None:
    runtime, repository, broker, first = _buy_ready(tmp_path)
    _seed_history(runtime, _NEXT_OPEN)
    engine = _engine(repository, runtime, broker)
    account = broker.account()

    engine._circuit_breaker_reason = "daily_loss_limit"
    engine._resume_startup_rebalances(account=account)
    assert _orders(broker, _BUY) == []

    engine._circuit_breaker_reason = None
    engine._resume_startup_rebalances(account=account)
    engine._resume_startup_rebalances(account=account)

    assert [order.symbol for order in _orders(broker, _BUY)] == ["MSFT"]


# --- P2-6-1 and Q2-9-4: catch-up runs the cross-check, then the scheduled path sees it ---


class _CatchUpRecorder:
    market = Market.EQUITY

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.startup_catch_up_status: str | None = None
        self.previous_timestamp = None
        self.history = {"AAPL": [SimpleNamespace(timestamp=_SESSION, market=Market.EQUITY)]}
        self.symbols = ("AAPL",)
        self.strategy = SimpleNamespace(timeframe="1d", minimum_history_bars=1)

    def prune_unfinished_signal_sessions(self) -> int:
        return 0

    def startup_catch_up(self, *, entry_latch=None, account=None, signal_cross_check=None, submit_gate=None):
        self.calls.append(signal_cross_check)
        return SimpleNamespace(status="submitted", decision_id=None)


def _catch_up_engine(recorder: _CatchUpRecorder, monkeypatch) -> EngineRuntime:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._running = True
    engine._warming_up = False
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._state = "warming_up"
    engine._portfolio_lock = threading.RLock()
    engine._entry_risk_latch = {}
    engine._portfolio_runtimes = (recorder,)
    engine._entry_readiness_reasons = lambda _market: []
    engine.broker = SimpleNamespace(account_id="acct", account=lambda: AccountSnapshot(100_000, 100_000, 100_000, 0.0))
    monkeypatch.setattr(engine, "_equity_signal_basis_latch", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(engine, "_record_paper_decision_receipt", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(engine, "_emit_equity_daily_summary", lambda *_args, **_kwargs: None, raising=False)
    return engine


def test_startup_catch_up_consults_the_signal_cross_check_before_deciding(monkeypatch) -> None:
    recorder = _CatchUpRecorder()
    engine = _catch_up_engine(recorder, monkeypatch)
    outcome = CrossCheckOutcome(status=STATUS_TREND_MISMATCH, session_date=_SESSION.date())
    monkeypatch.setattr(engine, "_signal_cross_check_step", lambda *_args, **_kwargs: outcome)

    engine._run_startup_catch_up()

    assert recorder.calls == [outcome]


def test_a_pending_cross_check_defers_the_catch_up_decision(monkeypatch) -> None:
    recorder = _CatchUpRecorder()
    engine = _catch_up_engine(recorder, monkeypatch)
    monkeypatch.setattr(engine, "_signal_cross_check_step", lambda *_args, **_kwargs: None)

    engine._run_startup_catch_up()

    assert recorder.calls == []
    assert recorder.startup_catch_up_status == "signal_cross_check_pending"


def test_catch_up_hold_is_recorded_and_the_scheduled_session_then_finds_it_decided(tmp_path, clock) -> None:
    """Q2-9-4: a catch-up with a trend mismatch holds the position and the scheduled path sees the decision."""
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    broker = _Broker()
    runtime = _runtime_with(repository, broker, targets={"AAPL": 0.40})
    _seed_history(runtime, _SESSION)
    outcome = CrossCheckOutcome(status=STATUS_TREND_MISMATCH, session_date=_SESSION.date())

    # Flat account: a catch-up entry is refused while a managed position is held.
    flat = AccountSnapshot(100_000.0, 100_000.0, 100_000.0, 0.0, ())
    caught_up = runtime.startup_catch_up(account=flat, signal_cross_check=outcome)

    assert caught_up.status == "signal_source_hold"
    recorded = repository.latest_decision_cross_check("acct", Market.EQUITY)
    assert recorded is not None and recorded["status"] == STATUS_TREND_MISMATCH
    scheduled = runtime.process_closed_bars(_bars(_SESSION), force_scheduled=True)
    assert scheduled.status == "duplicate_decision"


def test_every_open_rebalance_advance_status_parks_the_session() -> None:
    # The open-rebalance return carries the advance's own status (D2-3-1);
    # each one leaves the session undecided and must be parked (P2-2-4).
    import ast
    import inspect

    import parayabuck.portfolio.runtime_service as service_module
    from parayabuck.runtime import _HELD_SESSION_STATUSES

    tree = ast.parse(inspect.getsource(service_module))
    helpers = {
        "advance_rebalance",
        "_submit_deferred_buys",
        "_enter_buy_pending",
        "_submit_replan_sells",
        "_close_unsettled_sell_legs",
        "_close_stuck_rebalance",
        "_complete_rebalance_without_buys",
    }
    statuses: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in helpers:
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "PortfolioRuntimeResult":
                    for arg in [*call.args, *(keyword.value for keyword in call.keywords)]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            statuses.add(arg.value)
    assert statuses
    assert statuses <= _HELD_SESSION_STATUSES
