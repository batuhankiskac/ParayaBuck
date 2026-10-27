"""Audit wave 2, worker C: LIVE gap pricing, BUY submit gates, clock gate, premarket skew.

Offline and deterministic: simulated brokers, tmp_path ledgers and scripted
monotonic time. No network, no credentials, no production paths.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.trading.enums import TimeInForce

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.alpaca_client import BrokerClock
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, PortfolioRiskLimits
from parayabuck.monitoring import premarket as premarket_module
from parayabuck.monitoring.premarket import premarket_probe
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.runtime_models import PortfolioRuntimeResult
from parayabuck.portfolio.runtime_service import RUNTIME_ORDER_TIME_IN_FORCE, market_clock_refusal
from parayabuck.runtime import EngineRuntime
from tests.test_live_readiness_r1_r3 import _buy as _live_buy
from tests.test_submit_gate_shadow_tl_2026_10_27 import (
    CLOSED_CLOCK,
    OPEN_CLOCK,
    _ClockBroker,
    _runtime_with_decision,
    _status_of,
)
from tests.test_two_phase_rebalance import _bars, _runtime, _SimBroker

# The R1 limits of tests/test_live_readiness_r1_r3.py: 98 % gross, 2 % cash buffer.
_LIMITS_98 = PortfolioRiskLimits(
    daily_loss_limit=0.20,
    drawdown_halt=0.75,
    maximum_gross=0.98,
    maximum_equity=0.98,
    maximum_equity_asset=0.98,
    minimum_cash=0.02,
    maximum_positions=8,
)


class _CashBroker(_SimBroker):
    """Flat 100k account with an optional non-marginable buying power figure."""

    def __init__(self, non_marginable: float | None = None) -> None:
        super().__init__(())
        self.non_marginable = non_marginable

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            cash=100_000.0,
            equity=100_000.0,
            peak_equity=100_000.0,
            daily_pnl=0.0,
            positions=(),
            non_marginable_buying_power=self.non_marginable,
        )


def _admitted(tmp_path, broker: _SimBroker, *, mode: str, cap: float) -> float:
    runtime, _repository = _runtime(tmp_path, broker, {"QLD": 0.98}, mode=mode, cap=cap, limits=_LIMITS_98)
    admission = runtime._size_buys_to_safe_capacity((_live_buy(980),))
    return sum(order.quantity for order in admission.orders)


# --- P2-8-1 / P2-9-1: LIVE fill-basis cap and cash terms -----------------------


def test_live_dollar_cap_is_priced_at_the_gap_so_the_fill_stays_under_cap(tmp_path) -> None:
    # Cap 90,000 at reference 100 with a 3 % gap: the fill-basis cap allows
    # 90,000 / 1.03 = 87,378 reference dollars -> 873 shares; filled at 103
    # that is 89,919. The old sizing allowed 900 shares, filled at 92,700.
    admitted = _admitted(tmp_path, _CashBroker(), mode="live", cap=90_000.0)

    assert admitted == 873
    assert admitted * 100.0 * 1.03 <= 90_000.0


def test_live_cash_term_keeps_the_buffer_after_a_gap_fill(tmp_path) -> None:
    # (100k - 2k buffer) / 1.03 = 95,146 -> 951 shares; a +3 % fill costs 97,980 and keeps the buffer.
    admitted = _admitted(tmp_path, _CashBroker(), mode="live", cap=1_000_000.0)

    assert admitted == 951
    assert admitted * 100.0 * 1.03 <= 100_000.0 - 2_000.0 + 1e-9


# --- P2-8-2: committed open BUYs are gap-priced in the headroom -----------------


def test_gap_headroom_prices_committed_buys_at_the_gap(tmp_path, monkeypatch) -> None:
    # Committed open BUYs of 10,000 can fill at 10,300 under a +3 % gap, so the
    # new BUY is bounded by (100k - 2k - 10,300) / 1.03 = 85,146 -> 851 shares.
    # The old gap headroom (100k - 10k) / 1.03 would have allowed 873.
    runtime, _repository = _runtime(
        tmp_path,
        _CashBroker(),
        {"QLD": 0.98},
        mode="live",
        cap=1_000_000.0,
        limits=_LIMITS_98,
    )
    monkeypatch.setattr(runtime, "_committed_buy_notional", lambda *args, **kwargs: 10_000.0)

    admission = runtime._size_buys_to_safe_capacity((_live_buy(980),))
    admitted = sum(order.quantity for order in admission.orders)

    assert admitted == 851
    assert (10_000.0 + admitted * 100.0) * 1.03 <= 100_000.0 - 2_000.0 + 1e-9


# --- P2-10-4: PAPER uses non-marginable buying power when reported --------------


def test_paper_cash_term_uses_non_marginable_buying_power(tmp_path) -> None:
    # (50k - 2k buffer) = 48k -> 480 shares; without the report the buffer alone gives 980.
    assert _admitted(tmp_path, _CashBroker(non_marginable=50_000.0), mode="paper", cap=0.0) == 480
    assert _admitted(tmp_path, _CashBroker(), mode="paper", cap=0.0) == 980


# --- P2-9-2: DAY batches skip the clock; the gate reads the same TIF source ----


def test_time_in_force_constant_is_the_alpaca_day_value() -> None:
    assert RUNTIME_ORDER_TIME_IN_FORCE == TimeInForce.DAY.value == "day"


def _sell_order(decision_id: str, client_order_id: str) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id=client_order_id,
        decision_id=decision_id,
        symbol="AAPL",
        market=Market.EQUITY,
        side=PortfolioOrderSide.SELL,
        quantity=10.0,
        reference_price=100.0,
        decision_timestamp=datetime(2026, 10, 27, tzinfo=UTC),
        strategy_name="fixed_target",
        strategy_version="1.0.0",
    )


def test_non_day_clock_gate_reads_the_clock_and_refuses_opg_after_cutoff(tmp_path, monkeypatch) -> None:
    # Only a non-DAY batch reads the clock. An OPG order with the clock past the
    # 09:28 ET cutoff of the session it targets is refused locally.
    monkeypatch.setattr(runtime_service, "RUNTIME_ORDER_TIME_IN_FORCE", "opg")
    late_clock = BrokerClock(
        timestamp=datetime(2026, 10, 28, 13, 29, tzinfo=UTC),
        is_open=False,
        next_open=datetime(2026, 10, 28, 13, 30, tzinfo=UTC),
        next_close=datetime(2026, 10, 28, 20, 0, tzinfo=UTC),
    )
    broker = _ClockBroker(late_clock)
    runtime, repository, decision_id = _runtime_with_decision(tmp_path, broker)
    broker.clock_calls = 0

    kept = runtime._market_clock_gate((_sell_order(decision_id, "opg-aapl"),))

    assert broker.clock_calls == 1
    assert kept == ()
    assert _status_of(repository, decision_id, "opg-aapl") == "rejected"


def test_day_batch_never_reads_the_clock(tmp_path) -> None:
    broker = _ClockBroker(CLOSED_CLOCK)
    runtime, _repository, decision_id = _runtime_with_decision(tmp_path, broker)
    broker.clock_calls = 0

    kept = runtime._market_clock_gate((_sell_order(decision_id, "day-aapl"),))

    assert broker.clock_calls == 0
    assert len(kept) == 1


def test_market_clock_refusal_never_refuses_day() -> None:
    assert market_clock_refusal(CLOSED_CLOCK, time_in_force="day") is None
    assert market_clock_refusal(OPEN_CLOCK, time_in_force="day") is None


# --- P2-10-2: the decision-path BUY is held by the per-order submit gate -------


def test_decision_path_buy_is_held_by_the_submit_gate(tmp_path) -> None:
    broker = _SimBroker()
    runtime, _repository = _runtime(tmp_path, broker, {"AAPL": 0.5})

    result = runtime.process_closed_bars(
        _bars(("AAPL",)),
        submit_gate=lambda order: "buy_not_permitted" if order.side == PortfolioOrderSide.BUY else None,
    )

    assert broker.submitted == []
    assert result.submitted_order_ids == ()


def test_decision_path_buy_is_sent_without_a_gate(tmp_path) -> None:
    # Control for the test above: the same decision submits when no gate holds it.
    broker = _SimBroker()
    runtime, _repository = _runtime(tmp_path, broker, {"AAPL": 0.5})

    runtime.process_closed_bars(_bars(("AAPL",)))

    assert [order.symbol for order in broker.submitted] == ["AAPL"]


# --- P2-10-1: the soft reconcile-pending latch runs the decision ---------------


def _submitted_result() -> PortfolioRuntimeResult:
    return PortfolioRuntimeResult(None, None, None, (), (), "submitted")


def _batch_runtime(calls: list[dict[str, Any]], **attrs: Any) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._state = "running"
    runtime._running = True
    runtime._reconcile_failed = False
    runtime._reconcile_pending_reason = None
    runtime._reconcile_pending_seq = 0
    runtime._last_portfolio_status = {}
    runtime._portfolio_lock = threading.RLock()
    runtime._entry_readiness_reasons = lambda _market: []
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(timeframe="1d", name="fixed_target"),
            symbols=("QLD",),
            process_closed_bars=lambda _bars, **kwargs: calls.append(kwargs) or _submitted_result(),
        ),
    )
    for name, value in attrs.items():
        setattr(runtime, name, value)
    return runtime


def _qld_bar() -> Bar:
    return Bar(datetime(2025, 1, 1, tzinfo=UTC), "QLD", "1d", 100.0, 101.0, 99.0, 100.0, 1000.0, Market.EQUITY)


def test_soft_reconcile_pending_runs_the_decision_and_gates_only_buys() -> None:
    calls: list[dict[str, Any]] = []
    runtime = _batch_runtime(calls, _reconcile_pending_reason="trade_feed_stale")

    runtime._process_portfolio_timestamp([_qld_bar()])

    assert len(calls) == 1
    gate = calls[0]["submit_gate"]
    assert gate(_live_buy(1)) == "buy_not_permitted"
    assert gate(_sell_order("decision", "exit-qld")) is None


def test_hard_reconcile_failure_still_defers_the_decision() -> None:
    calls: list[dict[str, Any]] = []
    runtime = _batch_runtime(calls, _reconcile_failed=True)
    deferred: list[str] = []
    runtime._defer_portfolio_bars = lambda _bars, reason: deferred.append(reason) or True  # type: ignore[method-assign]

    runtime._process_portfolio_timestamp([_qld_bar()])

    assert calls == []
    assert deferred == ["reconciliation_failed"]


# --- P2-9-4: a slow broker round trip is not clock skew -------------------------

_NOW = datetime(2026, 10, 27, 13, 10, tzinfo=UTC)
_SESSIONS = (
    (date(2026, 10, 27), datetime(2026, 10, 27, 13, 30, tzinfo=UTC), datetime(2026, 10, 27, 20, 0, tzinfo=UTC)),
    (date(2026, 10, 28), datetime(2026, 10, 28, 13, 30, tzinfo=UTC), datetime(2026, 10, 28, 20, 0, tzinfo=UTC)),
)


class _TimedClockBroker:
    """Broker whose clock() call takes ``latency`` seconds and reports ``stamp``."""

    def __init__(self, ticks: list[float], latency: float, stamp: datetime) -> None:
        self._ticks = ticks
        self._latency = latency
        self._stamp = stamp

    def clock(self) -> BrokerClock:
        self._ticks[0] += self._latency
        return BrokerClock(
            timestamp=self._stamp,
            is_open=False,
            next_open=datetime(2026, 10, 27, 13, 30, tzinfo=UTC),
            next_close=datetime(2026, 10, 27, 20, 0, tzinfo=UTC),
        )

    def working_orders(self) -> tuple[Any, ...]:
        return ()


def _probe_with_timing(monkeypatch: pytest.MonkeyPatch, *, latency: float, stamp: datetime) -> Any:
    ticks = [0.0]
    monkeypatch.setattr(premarket_module, "time", SimpleNamespace(monotonic=lambda: ticks[0]))
    return premarket_probe(
        _TimedClockBroker(ticks, latency, stamp),
        now=_NOW,
        calendar_sessions=_SESSIONS,
        open_orders_local=(),
        calendar_degraded=True,
    )


def test_slow_round_trip_with_in_bounds_stamp_is_not_clock_skew(monkeypatch) -> None:
    # A 4 s call whose broker stamp is 3.5 s after the caller's reference: the
    # true skew lies in [-0.5 s, 3.5 s] and is not provably over 2 s. The old
    # check read 3.5 s and raised a false alert.
    result = _probe_with_timing(monkeypatch, latency=4.0, stamp=_NOW + timedelta(seconds=3.5))

    assert result.ok is True
    assert not any("clock skew" in reason for reason in result.reasons)


def test_real_skew_is_still_reported_on_a_fast_call(monkeypatch) -> None:
    result = _probe_with_timing(monkeypatch, latency=0.05, stamp=_NOW + timedelta(seconds=10.0))

    assert result.ok is False
    assert any("clock skew" in reason for reason in result.reasons)
