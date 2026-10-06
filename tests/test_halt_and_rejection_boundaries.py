"""Wave 2a: runtime halts and broker rejections must not cost a session.

F10 post-booking account fetch, F12 deferred BUY while halted, F13 cross-check
hold with an unrepresentable weight, F14 FILL-activity network errors, F15
definite broker rejections, F33 the seed decision watermark, F36 sizing a
deferred decision on after-hours marks.
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from requests import Response
from requests.exceptions import HTTPError

import parayabuck.runtime as runtime_module
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.alpaca_models import DefiniteOrderRejection
from parayabuck.execution.alpaca_orders import OrderService
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide
from parayabuck.portfolio.runtime_models import REBALANCE_PHASE_BUY_READY
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
)
from parayabuck.runtime import EngineRuntime
from tests.helpers import make_broker

SESSION_LABEL = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)


def _bar(symbol: str, close: float = 100.0, timestamp: datetime = SESSION_LABEL) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)


@pytest.fixture(autouse=True)
def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        runtime_module.alerts,
        "emit",
        lambda kind, **fields: events.append((kind, fields)),
    )
    return events


# --------------------------------------------------------------------------- #
# F10: a post-booking account fetch failure is provider noise, not a halt
# --------------------------------------------------------------------------- #


def _trade_update(client_order_id: str = "pbp-buy", symbol: str = "QLD") -> Any:
    from parayabuck.execution.alpaca_updates import TradeUpdate

    return TradeUpdate(
        event="fill",
        client_order_id=client_order_id,
        broker_order_id="broker-1",
        symbol=symbol,
        market=Market.EQUITY,
        filled_qty=1.0,
        average_fill_price=100.0,
        timestamp=datetime(2026, 9, 29, 13, 31, tzinfo=UTC),
        fee=0.0,
        activity_id="activity-1",
    )


def _fill_runtime(account_error: Exception | None) -> EngineRuntime:
    class _Repository:
        def __init__(self) -> None:
            self.booked: list[dict[str, object]] = []

        def has_order(self, client_order_id: str) -> bool:
            return client_order_id == "pbp-buy"

        def apply_trade_update(self, **kwargs: object) -> None:
            self.booked.append(kwargs)

    class _Broker:
        account_id = "acct"

        def __init__(self) -> None:
            self.account_calls = 0

        def account(self) -> AccountSnapshot:
            self.account_calls += 1
            if account_error is not None:
                raise account_error
            return AccountSnapshot(100_000, 100_000, 100_000, 0.0)

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._portfolio_repository = _Repository()
    runtime.broker = _Broker()
    runtime._last_account_at = None  # force the fresh fetch path
    runtime._last_account_snapshot = None
    runtime._last_account_snapshot_at = None
    runtime._account_snapshot_generation = 0
    runtime._state_lock = threading.RLock()
    runtime._circuit_breaker_reason = None
    runtime._state = "running"
    runtime._persist_risk_state = lambda *_args, **_kwargs: None
    runtime._advance_portfolio_rebalance = lambda _market: None
    return runtime


def test_account_fetch_failure_after_a_fill_books_the_fill_and_keeps_trading(
    _capture_alerts: list[tuple[str, dict[str, object]]],
) -> None:
    """F10: the fill is already durable; a broker timeout while refreshing the
    account must not become a persisted trade_update_callback_failure halt."""
    runtime = _fill_runtime(ConnectionError("account() timed out"))

    asyncio.run(runtime._on_trade_update(_trade_update()))

    assert runtime._portfolio_repository.booked  # the fill is booked
    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    assert [kind for kind, _ in _capture_alerts] == ["data_provider_unavailable"]


def test_risk_state_persist_failure_after_a_fill_is_also_non_fatal(
    _capture_alerts: list[tuple[str, dict[str, object]]],
) -> None:
    runtime = _fill_runtime(None)
    runtime._persist_risk_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full"))

    asyncio.run(runtime._on_trade_update(_trade_update()))

    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"


# --------------------------------------------------------------------------- #
# F12: a halt must not submit the risk-increasing BUY phase
# --------------------------------------------------------------------------- #


class _RebalanceRuntime:
    """Minimal stand-in recording how the BUY phase was advanced."""

    def __init__(self, market: Market = Market.EQUITY) -> None:
        self.market = market
        self.calls: list[bool] = []

    def advance_rebalance(self, **kwargs: Any) -> None:
        self.calls.append(bool(kwargs.get("submit_deferred_buys", True)))
        return None


def _halted_engine(portfolio_runtime: _RebalanceRuntime, reason: str | None) -> EngineRuntime:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._portfolio_runtimes = (portfolio_runtime,)
    engine._portfolio_lock = threading.RLock()
    engine._state_lock = threading.RLock()
    engine._circuit_breaker_reason = reason
    engine._reconcile_failed = False
    return engine


@pytest.mark.parametrize("reason", ["trade_update_callback_failure", "reconciliation_failed", None])
def test_deferred_buys_are_withheld_while_halted_and_released_after(
    reason: str | None,
) -> None:
    """F12: a SELL fill during a halt completes its phase bookkeeping but must
    not submit the dependent BUY; the next un-halted advance does."""
    portfolio = _RebalanceRuntime()
    engine = _halted_engine(portfolio, reason)

    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert portfolio.calls == [reason is None]

    engine._circuit_breaker_reason = None
    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert portfolio.calls == [reason is None, True]


# --------------------------------------------------------------------------- #
# F13: a cross-check hold must not crash on an unrepresentable held weight
# --------------------------------------------------------------------------- #


class _HoldBroker:
    account_id = "acct"

    def __init__(self, account: AccountSnapshot) -> None:
        self._account = account

    def account(self) -> AccountSnapshot:
        return self._account

    def asset_trading_rules(self, symbol: str, market: Market) -> Any:
        from parayabuck.execution.portfolio import AssetTradingRules

        return AssetTradingRules(symbol, market, 1.0, 0.01, 0.01, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        raise AssertionError("a signal-source hold must never submit")


class _TestUniverse:
    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return True


def _hold_runtime(tmp_path: Path, account: AccountSnapshot) -> PortfolioRuntime:
    from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1

    return PortfolioRuntime(
        EquityLeveragedTrendV1({"signal_symbol": "SPY", "hold_symbol": "AAPL", "sma_bars": 5}),
        ("SPY", "AAPL"),
        _HoldBroker(account),
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
    )


class _TradingBroker(_HoldBroker):
    """Accepts submissions and records them."""

    def __init__(self, account: AccountSnapshot | None = None) -> None:
        super().__init__(account or AccountSnapshot(100_000, 100_000, 100_000, 0.0))
        self.submitted: list[PortfolioOrderRequest] = []

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return f"broker-{order.client_order_id}"


def _trading_runtime(tmp_path: Path) -> PortfolioRuntime:
    from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1

    return PortfolioRuntime(
        EquityLeveragedTrendV1({"signal_symbol": "SPY", "hold_symbol": "AAPL", "sma_bars": 5}),
        ("SPY", "AAPL"),
        _TradingBroker(),
        PortfolioStateRepository(tmp_path / "f33.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
    )


def _warming_history(runtime: PortfolioRuntime, *, hold_last: bool = False) -> list[Bar]:
    """Seed a full warmup and return the batch to decide.

    ``hold_last`` leaves the newest session out of the seed so it arrives as a
    real cadence boundary rather than the bar the seed already consumed.
    """
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(220):
        timestamp = start + timedelta(days=index)
        close = 100.0 * (1.01**index)
        bars.extend([_bar("SPY", close, timestamp), _bar("AAPL", close, timestamp)])
    runtime.seed(bars[:-2] if hold_last else bars)
    return bars[-2:]


def _hold_outcome() -> Any:
    from parayabuck.portfolio.signal_cross_check import STATUS_TREND_MISMATCH, CrossCheckOutcome

    return CrossCheckOutcome(
        status=STATUS_TREND_MISMATCH,
        session_date=SESSION_LABEL.date(),
        attempts=1,
        reason="trend disagreement",
    )


def test_signal_source_hold_survives_a_held_weight_above_one(tmp_path: Path) -> None:
    """F13: a gap-up fill can leave cash negative, so the held weight exceeds
    1.0. The hold must not raise (which halted the runtime for the day)."""
    # Equity 100,000 with a 120,000 holding: weight 1.2 and negative cash.
    account = AccountSnapshot(
        cash=-20_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0.0,
        positions=(
            PositionSnapshot(
                "AAPL",
                1_200.0,
                100.0,
                100.0,
                Market.EQUITY,
                qty_available=1_200.0,
                ownership="managed",
                managed_qty=1_200.0,
            ),
        ),
    )
    runtime = _hold_runtime(tmp_path, account)
    # Seed all but the last session, so that session is a real boundary.
    bars = _warming_history(runtime, hold_last=True)

    result = runtime.process_closed_bars(bars, signal_cross_check=_hold_outcome())

    assert result.status == "signal_source_hold"
    assert result.orders == () and result.submitted_order_ids == ()
    assert result.target is not None
    assert all(weight.weight <= 1.0 for weight in result.target.weights)
    assert 0.0 <= result.target.cash_weight <= 1.0


# --------------------------------------------------------------------------- #
# F33: a warmup retry must not move the decision watermark
# --------------------------------------------------------------------------- #


def test_seed_after_a_decision_does_not_suppress_the_same_session_decision(tmp_path: Path) -> None:
    """F33: hours:24 buckets by UTC day, so a warmup RETRY that advanced
    previous_timestamp to today's provider bar (labelled 00:00 ET, 04:00 UTC)
    made the real 16:00 stream bar (09:30 ET, 13:30 UTC) a non-boundary —
    silently skipping that session's decision."""
    from parayabuck.markets.timeframes import NEW_YORK

    runtime = _trading_runtime(tmp_path)
    session = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(220):
        day = (session + timedelta(days=index)).date()
        close = 100.0 * (1.01**index)
        bars.extend(
            [
                _bar("SPY", close, datetime.combine(day, datetime.min.time(), tzinfo=UTC)),
                _bar("AAPL", close, datetime.combine(day, datetime.min.time(), tzinfo=UTC)),
            ]
        )
    runtime.seed(bars)
    decided_day = bars[-2].timestamp
    runtime.process_closed_bars(
        [bar for bar in bars if bar.timestamp == decided_day],
        force_scheduled=True,
        decision_origin="test",
    )
    assert runtime.previous_timestamp == decided_day

    next_day = decided_day + timedelta(days=1)
    provider_label = datetime.combine(next_day.date(), datetime.min.time(), tzinfo=NEW_YORK).astimezone(UTC)
    runtime.seed([dataclasses.replace(bar, timestamp=provider_label) for bar in bars[-2:]])
    # The watermark is the last DECIDED session, not the last bar ever seen.
    assert runtime.previous_timestamp == decided_day

    stream_label = datetime.combine(next_day.date(), datetime.min.time(), tzinfo=NEW_YORK).replace(hour=9, minute=30)
    result = runtime.process_closed_bars(
        [
            _bar("SPY", 150.0, stream_label.astimezone(UTC)),
            _bar("AAPL", 150.0, stream_label.astimezone(UTC)),
        ]
    )

    assert result.status != "not_scheduled"
    assert runtime.previous_timestamp == stream_label.astimezone(UTC)


# --------------------------------------------------------------------------- #
# F15: a definite broker rejection alerts and does not halt
# --------------------------------------------------------------------------- #


def _http_error(status: int) -> HTTPError:
    """A requests-level HTTP error carrying the broker's status code."""
    error = HTTPError(f"{status} error", response=Response())
    error.response.status_code = status
    error.status_code = status  # type: ignore[attr-defined]
    return error


def _rejected_submit_broker(tmp_path: Path, submit_error: Exception) -> Any:
    return make_broker(
        tmp_path,
        client=SimpleNamespace(
            submit_order=lambda _request: (_ for _ in ()).throw(submit_error),
            get_order_by_client_id=lambda _cid: (_ for _ in ()).throw(_http_error(404)),
        ),
    )


def test_definite_broker_rejection_is_typed_not_ambiguous(tmp_path: Path) -> None:
    """A 422 plus a 404 lookup proves the order never reached the broker."""
    service = OrderService(_rejected_submit_broker(tmp_path, _http_error(422)))

    with pytest.raises(DefiniteOrderRejection) as excinfo:
        service._submit_with_recovery(SimpleNamespace(), "cid-1")

    assert isinstance(excinfo.value.__cause__, HTTPError)


def test_a_non_http_submit_failure_keeps_its_original_type(tmp_path: Path) -> None:
    """Without a broker status there is no 4xx to classify, so the caller's
    existing fail-closed handling is unchanged."""
    submit_error = ValueError("insufficient buying power")
    broker = _rejected_submit_broker(tmp_path, submit_error)

    with pytest.raises(ValueError) as excinfo:
        OrderService(broker)._submit_with_recovery(SimpleNamespace(), "cid-1")

    assert excinfo.value is submit_error


class _RejectingRuntimeBroker:
    account_id = "acct"

    def __init__(self) -> None:
        self.calls: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(100_000, 100_000, 100_000, 0.0)

    def asset_trading_rules(self, symbol: str, market: Market) -> Any:
        from parayabuck.execution.portfolio import AssetTradingRules

        return AssetTradingRules(symbol, market, 1.0, 0.01, 0.01, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.calls.append(order)
        if order.symbol == "SPY":
            raise DefiniteOrderRejection("422 rejected")
        return f"broker-{order.client_order_id}"


def _risk_decision(target: Any) -> Any:
    """A real risk-overlay decision: the repository serializes both."""
    from parayabuck.execution.portfolio import RiskOverlayDecision

    return RiskOverlayDecision(
        target=target,
        halted=False,
        reasons=(),
        gross_before=0.0,
        gross_after=0.0,
    )


def _cash_target() -> Any:
    from parayabuck.portfolio.models import TargetPortfolio

    return TargetPortfolio(
        strategy_name="test",
        strategy_version="v1",
        decision_timestamp=SESSION_LABEL,
        weights=(),
        cash_weight=1.0,
    )


def test_definite_rejection_is_booked_rejected_and_the_batch_continues(tmp_path: Path) -> None:
    """F15: a 4xx the broker definitively refused is a re-plannable outcome,
    not a portfolio_runtime_failure halt."""
    from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1

    broker = _RejectingRuntimeBroker()
    runtime = PortfolioRuntime(
        EquityLeveragedTrendV1({"signal_symbol": "SPY", "hold_symbol": "AAPL", "sma_bars": 5}),
        ("SPY", "AAPL"),
        broker,  # type: ignore[arg-type]
        PortfolioStateRepository(tmp_path / "orders.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
    )
    target = _cash_target()
    decision_id = runtime.repository.record_decision_with_orders(
        runtime.session_id,
        runtime.account_id,
        runtime.configuration_digest,
        target,
        _risk_decision(target),
        runtime.market,
        (),
    )
    orders = tuple(
        PortfolioOrderRequest(
            client_order_id=f"pbp-{symbol}",
            decision_id=decision_id,
            symbol=symbol,
            market=Market.EQUITY,
            side=PortfolioOrderSide.BUY,
            quantity=1.0,
            reference_price=100.0,
            decision_timestamp=SESSION_LABEL,
            strategy_name="test",
            strategy_version="v1",
            risk_reducing=False,
        )
        for symbol in ("SPY", "AAPL")
    )

    submitted = runtime._submit_orders(orders)

    # The rejected order is terminal, the independent one still went out.
    statuses = {
        str(row["client_order_id"]): str(row["status"]) for row in runtime.repository.orders_for_decision(decision_id)
    }
    assert statuses["pbp-SPY"] == "rejected"
    assert statuses["pbp-AAPL"] == "submitted"
    assert submitted == ("broker-pbp-AAPL",)


# --------------------------------------------------------------------------- #
# F36: a deferred decision is sized at the close, not at after-hours marks
# --------------------------------------------------------------------------- #


def test_deferred_decision_re_marks_the_account_at_the_decision_close() -> None:
    engine = EngineRuntime.__new__(EngineRuntime)
    # The broker reported a 5% after-hours drop; the decision used the close.
    account = AccountSnapshot(
        cash=0.0,
        equity=95_000.0,
        peak_equity=100_000.0,
        daily_pnl=-5_000.0,
        unrealized_pnl=-5_000.0,
        positions=(
            PositionSnapshot(
                "QLD",
                1_000.0,
                100.0,
                95.0,
                Market.EQUITY,
                qty_available=1_000.0,
                ownership="managed",
                managed_qty=1_000.0,
            ),
        ),
    )

    remarked = engine._account_at_decision_close(account, [_bar("QLD", 100.0)])

    assert remarked.equity == pytest.approx(100_000.0)
    assert remarked.positions[0].current_price == pytest.approx(100.0)
    # The move is applied consistently to the P&L fields, not equity alone.
    assert remarked.daily_pnl == pytest.approx(0.0)
    assert remarked.unrealized_pnl == pytest.approx(0.0)
    # The peak is a high-water mark: a re-mark never lowers it.
    assert remarked.peak_equity == pytest.approx(100_000.0)


def test_a_non_positive_re_mark_is_discarded() -> None:
    engine = EngineRuntime.__new__(EngineRuntime)
    account = AccountSnapshot(
        cash=-200_000.0,
        equity=100_000.0,
        peak_equity=100_000.0,
        daily_pnl=0.0,
    )

    assert engine._account_at_decision_close(account, [_bar("QLD", 100.0)]) is account


def test_buy_ready_phase_is_persisted_while_the_buy_is_withheld(tmp_path: Path) -> None:
    """The withheld BUY leaves buy_ready persisted, so it is submitted by the
    first advance after the operator's verified reset."""
    from parayabuck.portfolio.runtime_models import REBALANCE_PHASE_SELL_PENDING

    repository = PortfolioStateRepository(tmp_path / "rebalance.sqlite3")
    runtime = PortfolioRuntime(
        _hold_runtime(tmp_path, AccountSnapshot(100_000, 100_000, 100_000, 0.0)).strategy,
        ("SPY", "AAPL"),
        _HoldBroker(AccountSnapshot(100_000, 100_000, 100_000, 0.0)),  # type: ignore[arg-type]
        repository,
        mode="paper",
        maximum_allocation=0.50,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
    )
    order = PortfolioOrderRequest(
        client_order_id="pbp-sell",
        decision_id="dec-sell",
        symbol="AAPL",
        market=Market.EQUITY,
        side=PortfolioOrderSide.SELL,
        quantity=1.0,
        reference_price=100.0,
        decision_timestamp=SESSION_LABEL,
        strategy_name="test",
        strategy_version="v1",
        risk_reducing=True,
    )
    target = _cash_target()
    repository.record_decision_with_orders(
        runtime.session_id,
        runtime.account_id,
        runtime.configuration_digest,
        target,
        _risk_decision(target),
        runtime.market,
        (dataclasses.replace(order, decision_id="pending"),),
        initial_status="filled",
        rebalance_phase=REBALANCE_PHASE_SELL_PENDING,
    )

    assert runtime.advance_rebalance(submit_deferred_buys=False) is None

    current = repository.current_rebalance_execution(runtime.account_id, runtime.market)
    assert current is not None and current.phase == REBALANCE_PHASE_BUY_READY
