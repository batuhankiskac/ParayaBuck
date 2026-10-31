"""Audit wave 3 leftovers (X1): startup SIP-embargo gate, SELL unfilled alert,
LIVE checklist host binding, incomplete-session parking.

Covers T3-5-2 / P3-5-1 on the startup catch-up path (the basis latch ran ungated
within 15 minutes after the close), P3-5-2 (a SELL that ends canceled or expired
with unfilled quantity alerts the operator once per order), S3-1-3 (the LIVE
checklist approval row checks the trading-host binding the way the runtime start
does) and P3-2-4 (warming-up and incomplete batches are parked, not dropped).

All clocks are fixed. No network, no credentials, no data/ paths; everything
runs on in-memory stubs and ``tmp_path``.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.monitoring.alerts as alerts_module
import parayabuck.runtime as runtime_module
from parayabuck.config import EngineConfig, SignalCrossCheckConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.live_safety import LiveOperatorApproval
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.portfolio.runtime_service import PortfolioRuntime
from parayabuck.runtime import EngineRuntime, _AccountSnapshotLease
from parayabuck.web.managers import engine as web_engine_module
from tests.helpers import make_manager

# 2026-09-29 is a Tuesday; the stream's session-close daily bar is labelled 09:30 ET.
SESSION_LABEL = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
SESSION_CLOSE = datetime(2026, 9, 29, 16, 0, tzinfo=NEW_YORK).astimezone(UTC)
SYMBOLS = ("SPY", "QQQ", "QLD")


class _Clock:
    """Pins ``parayabuck.runtime.datetime.now`` (same pattern as the W01 suite)."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def freeze(self, moment: datetime) -> None:
        self.now = moment

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = self

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return clock.now.astimezone(tz) if tz is not None else clock.now

        monkeypatch.setattr(runtime_module, "datetime", _Frozen)


@pytest.fixture(autouse=True)
def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **fields: events.append((kind, fields)))
    monkeypatch.setattr(alerts_module, "emit", lambda kind, **fields: events.append((kind, fields)))
    monkeypatch.setattr(
        runtime_module,
        "runtime_metrics_store",
        SimpleNamespace(record_event=lambda *_args, **_kwargs: None),
    )
    return events


def _bars(close: float = 100.0) -> list[Bar]:
    return [
        Bar(SESSION_LABEL, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY) for symbol in SYMBOLS
    ]


def _result(status: str = "submitted") -> SimpleNamespace:
    return SimpleNamespace(status=status, decision_id="dec-1", target=None, orders=(), submitted_order_ids=())


def _engine(process: Any) -> EngineRuntime:
    """A decision-path shell for one equity portfolio runtime (cross-check off)."""
    runtime = SimpleNamespace(
        market=Market.EQUITY,
        symbols=SYMBOLS,
        strategy=SimpleNamespace(timeframe="1d", name="vt35"),
        process_closed_bars=process,
    )
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.config = EngineConfig()
    engine.app_config = SimpleNamespace(signal_cross_check=SignalCrossCheckConfig(enabled=False))
    engine.broker = SimpleNamespace(account_id="acct")
    engine._warming_up = False
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._state = "running"
    engine._state_lock = threading.RLock()
    engine._portfolio_lock = threading.RLock()
    engine._entry_risk_latch = {Market.EQUITY: None}
    engine._market_entry_halts = {Market.EQUITY: None}
    engine._portfolio_runtimes = (runtime,)
    engine._workers = {}
    engine._last_account_at = SESSION_CLOSE
    engine._signal_cross_check_step = lambda *_args, **_kwargs: False
    engine._deferred_portfolio_batches = {}
    engine._entry_readiness_reasons = lambda *_args, **_kwargs: ()
    engine._fresh_account_snapshot_lease = lambda **_kwargs: _AccountSnapshotLease(
        AccountSnapshot(100_000, 100_000, 100_000, 0.0), None
    )
    engine._last_portfolio_status = {}
    return engine


# --------------------------------------------------------------------------- #
# T3-5-2 on the startup path: the catch-up waits for the SIP embargo
# --------------------------------------------------------------------------- #


def _startup_engine(decided: list[list[Bar]]) -> tuple[EngineRuntime, SimpleNamespace]:
    portfolio_runtime = SimpleNamespace(
        market=Market.EQUITY,
        history={symbol: [bar] for symbol, bar in zip(SYMBOLS, _bars(), strict=True)},
        previous_timestamp=SESSION_LABEL,
        startup_catch_up_status=None,
        startup_catch_up_at=None,
    )

    def startup_catch_up(**_kwargs: Any) -> SimpleNamespace:
        decided.append(list(portfolio_runtime.history.values()))
        return _result()

    portfolio_runtime.startup_catch_up = startup_catch_up
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.app_config = SimpleNamespace(signal_cross_check=SignalCrossCheckConfig(enabled=False))
    engine._running = True
    engine._warming_up = False
    engine._startup_committed = True
    engine._portfolio_runtimes = (portfolio_runtime,)
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._reconcile_pending_reason = None
    engine._entry_readiness_reasons = lambda *_args, **_kwargs: ()
    engine._entry_risk_latch = {}
    engine._portfolio_lock = threading.RLock()
    engine._signal_cross_check_step = lambda *_args, **_kwargs: False
    engine._deferred_portfolio_batches = {}
    engine._record_paper_decision_receipt = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    engine._emit_equity_daily_summary = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    engine._state_lock = threading.RLock()
    return engine, portfolio_runtime


def test_startup_catch_up_inside_the_sip_embargo_parks_instead_of_latching(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    """A restart at close+30 s: no basis latch, no false alert, the batch waits for the SIP bar."""
    decided: list[list[Bar]] = []
    engine, portfolio_runtime = _startup_engine(decided)
    basis_calls: list[datetime | None] = []
    engine._equity_signal_basis_latch = lambda ts=None: (basis_calls.append(ts), None)[1]  # type: ignore[method-assign]
    engine._utcnow = lambda: SESSION_CLOSE + timedelta(seconds=30)  # type: ignore[method-assign]

    engine._run_startup_catch_up(account=AccountSnapshot(100_000, 100_000, 100_000, 0.0))

    assert basis_calls == []
    assert decided == []
    assert _capture_alerts == []
    assert portfolio_runtime.startup_catch_up_status == "sip_embargo_pending"
    # The seeded high-water is released, so the retry is a cadence boundary.
    assert portfolio_runtime.previous_timestamp is None
    _parked, retry_at = engine._deferred_portfolio_batches[(Market.EQUITY, SESSION_LABEL)]
    assert retry_at == SESSION_CLOSE + timedelta(minutes=15)


def test_startup_gate_judges_the_history_after_the_forming_session_is_pruned(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    """The forming session the restart adopted is pruned first: the gate sees the completed session only."""
    prior = datetime(2026, 9, 28, 13, 30, tzinfo=UTC)
    decided: list[list[Bar]] = []
    engine, portfolio_runtime = _startup_engine(decided)
    portfolio_runtime.history = {
        symbol: [
            Bar(prior, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY),
            Bar(SESSION_LABEL, symbol, "1d", 101.0, 101.0, 101.0, 101.0, 1_000_000, Market.EQUITY),
        ]
        for symbol in SYMBOLS
    }

    def prune_unfinished_signal_sessions() -> None:
        for symbol in SYMBOLS:
            portfolio_runtime.history[symbol] = [
                bar for bar in portfolio_runtime.history[symbol] if bar.timestamp < SESSION_LABEL
            ]

    portfolio_runtime.prune_unfinished_signal_sessions = prune_unfinished_signal_sessions
    basis_calls: list[datetime | None] = []
    engine._equity_signal_basis_latch = lambda ts=None: (basis_calls.append(ts), None)[1]  # type: ignore[method-assign]
    engine._utcnow = lambda: SESSION_CLOSE + timedelta(seconds=30)  # type: ignore[method-assign]

    engine._run_startup_catch_up(account=AccountSnapshot(100_000, 100_000, 100_000, 0.0))

    # The completed prior session is final, so nothing is parked and the basis is checked on it.
    assert basis_calls == [prior]
    assert engine._deferred_portfolio_batches == {}
    assert len(decided) == 1


def test_startup_catch_up_after_the_embargo_latches_and_decides_as_before(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    """Past close+embargo the gate is open: the basis is checked on the decision bar and the catch-up runs."""
    decided: list[list[Bar]] = []
    engine, portfolio_runtime = _startup_engine(decided)
    basis_calls: list[datetime | None] = []
    engine._equity_signal_basis_latch = lambda ts=None: (basis_calls.append(ts), None)[1]  # type: ignore[method-assign]
    engine._utcnow = lambda: SESSION_CLOSE + timedelta(minutes=16)  # type: ignore[method-assign]

    engine._run_startup_catch_up(account=AccountSnapshot(100_000, 100_000, 100_000, 0.0))

    assert basis_calls == [SESSION_LABEL]
    assert len(decided) == 1
    assert portfolio_runtime.startup_catch_up_status is None
    assert engine._deferred_portfolio_batches == {}


# --------------------------------------------------------------------------- #
# P3-5-2: a SELL that ends canceled or expired with unfilled quantity alerts once
# --------------------------------------------------------------------------- #


def _sell_runtime() -> PortfolioRuntime:
    runtime = PortfolioRuntime.__new__(PortfolioRuntime)
    runtime.market = Market.EQUITY
    runtime.account_id = "acct"
    runtime.repository = SimpleNamespace(
        decision_row=lambda _decision_id: None,
        update_rebalance_execution=lambda *_args, **_kwargs: None,
    )
    return runtime


def _sell_row(client_order_id: str, status: str, filled: float) -> dict[str, object]:
    return {
        "client_order_id": client_order_id,
        "symbol": "QLD",
        "side": "sell",
        "status": status,
        "quantity": 10.0,
        "cumulative_filled_quantity": filled,
        "reference_price": 50.0,
    }


def _unfilled_alerts(events: list[tuple[str, dict[str, object]]]) -> list[dict[str, object]]:
    return [
        fields
        for kind, fields in events
        if kind == "paper_incident" and fields.get("category") == "sell_ended_unfilled"
    ]


def test_canceled_sell_with_unfilled_quantity_alerts_once_per_order(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    runtime = _sell_runtime()
    rows = (_sell_row("parayabuck-dec1-QLD-sell", "canceled", filled=4.0),)

    runtime._residual_sell_result("dec-1", rows)
    runtime._residual_sell_result("dec-1", rows)

    alerts = _unfilled_alerts(_capture_alerts)
    assert len(alerts) == 1
    assert alerts[0]["client_order_id"] == "parayabuck-dec1-QLD-sell"
    assert alerts[0]["tail_status"] == "canceled"
    assert alerts[0]["unfilled_quantity"] == pytest.approx(6.0)


def test_expired_sell_with_no_fill_alerts_and_a_fully_filled_sell_does_not(
    monkeypatch: pytest.MonkeyPatch, _capture_alerts: list[tuple[str, dict[str, object]]]
) -> None:
    runtime = _sell_runtime()

    runtime._residual_sell_result("dec-1", (_sell_row("parayabuck-dec1-QLD-sell", "expired", filled=0.0),))
    runtime._residual_sell_result("dec-2", (_sell_row("parayabuck-dec2-QLD-sell", "filled", filled=10.0),))

    alerts = _unfilled_alerts(_capture_alerts)
    assert [alert["client_order_id"] for alert in alerts] == ["parayabuck-dec1-QLD-sell"]
    assert alerts[0]["tail_status"] == "expired"


# --------------------------------------------------------------------------- #
# S3-1-3: the LIVE checklist approval row checks the trading-host binding
# --------------------------------------------------------------------------- #


def _approval(trading_host: str) -> LiveOperatorApproval:
    now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    return LiveOperatorApproval(
        operator_id="operator-1",
        account_id="acct-live-1",
        configuration_digests=("digest-a",),
        maximum_dollar_exposure=1000,
        approved_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=1),
        nonce="n" * 16,
        trading_host=trading_host,
    )


def _binding_row(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, approval: LiveOperatorApproval, identity: Any
) -> dict[str, Any]:
    if callable(identity):
        monkeypatch.setattr(web_engine_module, "trading_host_identity", identity)
    else:
        monkeypatch.setattr(web_engine_module, "trading_host_identity", lambda: identity)
    manager = make_manager(tmp_path)
    collected: list[dict[str, Any]] = []

    def item(item_id: str, label: str, satisfied: bool, missing: str | None = None, state: str | None = None) -> None:
        collected.append({"id": item_id, "satisfied": satisfied, "missing": missing, "state": state})

    manager._append_live_binding_check(item, approval, "acct-live-1", None, ("digest-a",), None)
    (row,) = collected
    return row


def test_binding_row_is_green_for_the_same_trading_host(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    row = _binding_row(tmp_path, monkeypatch, _approval("this-host"), "this-host")
    assert row["satisfied"] is True
    assert row["state"] == "ok"


def test_binding_row_is_missing_for_an_approval_from_another_host(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _binding_row(tmp_path, monkeypatch, _approval("other-host"), "this-host")
    assert row["satisfied"] is False
    assert row["state"] == "missing"
    assert "bu işlem makinesi için" in row["missing"]


def test_binding_row_is_missing_for_a_legacy_approval_without_a_host(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _binding_row(tmp_path, monkeypatch, _approval(""), "this-host")
    assert row["satisfied"] is False
    assert row["state"] == "missing"


def test_binding_row_is_unverifiable_without_a_host_identity(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_identity() -> str:
        raise ValueError("host identity unavailable: no readable machine id (/etc/machine-id)")

    row = _binding_row(tmp_path, monkeypatch, _approval("this-host"), no_identity)
    assert row["satisfied"] is False
    assert row["state"] == "unverifiable"
    assert "işlem makinesi kimliği doğrulanamıyor" in row["missing"]


# --------------------------------------------------------------------------- #
# P3-2-4: warming-up and incomplete batches are parked for the bounded retry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["warming_up", "incomplete_strategy_batch", "incomplete_managed_batch"])
def test_incomplete_session_status_parks_the_batch_on_the_decision_path(
    monkeypatch: pytest.MonkeyPatch,
    _capture_alerts: list[tuple[str, dict[str, object]]],
    status: str,
) -> None:
    decided: list[list[Bar]] = []
    engine = _engine(lambda bars, **_kwargs: (decided.append(list(bars)), _result(status))[1])
    engine._equity_signal_basis_latch = lambda _ts=None: None  # type: ignore[method-assign]
    clock = _Clock(SESSION_CLOSE + timedelta(minutes=16))
    clock.install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is False

    _parked, retry_at = engine._deferred_portfolio_batches[(Market.EQUITY, SESSION_LABEL)]
    assert retry_at == clock.now + timedelta(seconds=60)
    assert not any(kind == "decision_window_expired" for kind, _fields in _capture_alerts)

    # The retry decides the same batch once it is due.
    clock.freeze(clock.now + timedelta(seconds=61))
    engine._run_due_deferred_portfolio_batches(now=clock.now)
    assert len(decided) >= 2


def test_decided_and_unlisted_statuses_are_not_parked(monkeypatch: pytest.MonkeyPatch) -> None:
    decided: list[list[Bar]] = []
    engine = _engine(lambda bars, **_kwargs: (decided.append(list(bars)), _result("submitted"))[1])
    engine._equity_signal_basis_latch = lambda _ts=None: None  # type: ignore[method-assign]
    _Clock(SESSION_CLOSE + timedelta(minutes=16)).install(monkeypatch)

    assert engine._process_portfolio_timestamp(_bars()) is True
    assert engine._deferred_portfolio_batches == {}

    unlisted = _engine(lambda bars, **_kwargs: _result("no_eligible_bars"))
    unlisted._equity_signal_basis_latch = lambda _ts=None: None  # type: ignore[method-assign]
    assert unlisted._process_portfolio_timestamp(_bars()) is True
    assert unlisted._deferred_portfolio_batches == {}


def test_park_helper_ignores_a_session_already_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(lambda bars, **_kwargs: _result())
    _Clock(SESSION_CLOSE + timedelta(minutes=16)).install(monkeypatch)
    portfolio_runtime = SimpleNamespace(market=Market.EQUITY, _session_decided=lambda _ts: True)

    assert engine._park_held_session(portfolio_runtime, _bars(), "incomplete_managed_batch") is False
    assert engine._deferred_portfolio_batches == {}
