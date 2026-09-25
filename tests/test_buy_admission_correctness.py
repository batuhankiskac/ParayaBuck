"""Offline production BUY admission against realized account capacity."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck import load_config
from parayabuck.core.models import Bar, Market, PositionSnapshot
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.portfolio import (
    BrokerWorkingBuyOrder,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio.runtime_models import OpenPortfolioOrder
from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository
from parayabuck.runtime import EngineRuntime
from tests.test_two_phase_rebalance import _bars, _FixedTargetStrategy, _position, _runtime, _SimBroker

_NAMES = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOG")


def _over_gross_broker() -> _SimBroker:
    positions = [_position(symbol, 75) for symbol in _NAMES]
    positions.append(
        PositionSnapshot(
            "BTC/USD",
            155,
            100,
            100,
            Market.CRYPTO,
            qty_available=155,
            ownership="external",
            managed_qty=0,
        )
    )
    return _SimBroker(tuple(positions))


def _decide(tmp_path, targets: dict[str, float]):  # type: ignore[no-untyped-def]
    broker = _over_gross_broker()
    limits = PortfolioRiskLimits(
        maximum_gross=0.60,
        maximum_equity=0.60,
        maximum_crypto=0.10,
        minimum_cash=0.40,
        maximum_positions=8,
        maximum_crypto_positions=2,
    )
    runtime, _repository = _runtime(tmp_path, broker, targets, limits=limits)
    runtime.maximum_allocation = 0.55
    result = runtime.process_closed_bars(_bars(tuple(targets)))
    return broker, result


def test_over_gross_new_symbol_buy_is_suppressed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, result = _decide(tmp_path, {**{name: 0.071 for name in _NAMES}, "TSLA": 0.019})
    assert result.risk is not None and result.risk.gross_before == 0.605
    assert broker.submitted == []


def test_over_gross_add_to_held_buy_is_suppressed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, result = _decide(tmp_path, {**{name: 0.071 for name in _NAMES[1:]}, "AAPL": 0.09})
    assert result.risk is not None and result.risk.gross_before == 0.605
    assert broker.submitted == []


def test_over_gross_sell_remains_available(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, _result = _decide(tmp_path, {**{name: 0.071 for name in _NAMES[1:]}, "AAPL": 0.04})
    assert [(order.side, order.symbol) for order in broker.submitted] == [(PortfolioOrderSide.SELL, "AAPL")]


def test_shipped_cap8_config_still_blocks_over_gross_buy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = load_config(str(Path(__file__).resolve().parents[1] / "config" / "settings.yaml"))
    # Account already invested 0.99: past the shipped 0.98 gross cap.
    broker = _SimBroker(
        tuple(
            [_position(symbol, 130) for symbol in _NAMES]
            + [
                PositionSnapshot(
                    "BTC/USD",
                    210,
                    100,
                    100,
                    Market.CRYPTO,
                    qty_available=210,
                    ownership="external",
                    managed_qty=0,
                )
            ]
        )
    )
    # Held names are targeted at their current weight (inside the rebalance
    # band), so the only planned order is the new TSLA BUY.
    targets = {**{name: 0.13 for name in _NAMES}, "TSLA": 0.019}
    runtime, _repository = _runtime(
        tmp_path,
        broker,
        targets,
        limits=portfolio_risk_limits_from_config(config.risk),
    )
    runtime.maximum_allocation = config.strategy.equity.maximum_allocation
    result = runtime.process_closed_bars(_bars(tuple(targets)))
    assert result.risk is not None and result.risk.gross_before == 0.99
    assert broker.submitted == []


def test_shipped_cap8_config_counts_working_buy_before_next_admission(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = load_config(str(Path(__file__).resolve().parents[1] / "config" / "settings.yaml"))
    # Account invested 0.90 leaves 0.98 - 0.90 - 0.05 (working buy) == 0.03.
    broker = _SimBroker((_position("AAPL", 900),))
    broker.working_buy_orders = lambda: (_working("existing", "MSFT", 50, Market.EQUITY),)  # type: ignore[attr-defined]
    runtime, _repository = _runtime(
        tmp_path,
        broker,
        {"TSLA": 0.10},
        limits=portfolio_risk_limits_from_config(config.risk),
    )
    admission = runtime._size_buys_to_safe_capacity((_buy(),))
    assert admission.committed_buy_notional == 5000
    assert admission.orders[0].quantity == 30
    assert admission.gross_headroom == admission.cash_headroom == 3000


def _admission_runtime(tmp_path, current_qty: int = 500):  # type: ignore[no-untyped-def]
    broker = _SimBroker((_position("AAPL", current_qty),))
    limits = PortfolioRiskLimits(maximum_gross=0.60, minimum_cash=0.40, maximum_positions=8)
    runtime, _repository = _runtime(tmp_path, broker, {"TSLA": 0.10}, limits=limits)
    return broker, runtime


def _buy(symbol: str = "TSLA", quantity: float = 80, market: Market = Market.EQUITY) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id=f"candidate-{symbol}",
        decision_id="candidate",
        symbol=symbol,
        market=market,
        side=PortfolioOrderSide.BUY,
        quantity=quantity,
        reference_price=100,
        decision_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        strategy_name="fixed_target",
        strategy_version="1",
        risk_reducing=False,
    )


def _working(client_id: str, symbol: str, remaining: float, market: Market) -> BrokerWorkingBuyOrder:
    return BrokerWorkingBuyOrder(client_id, symbol, market, remaining, 100, None)


def test_partial_gross_headroom_scales_to_broker_increment(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _broker, runtime = _admission_runtime(tmp_path, current_qty=550)
    admission = runtime._size_buys_to_safe_capacity((_buy(),))
    assert len(admission.orders) == 1
    assert admission.orders[0].quantity == 50
    assert admission.reason == "buy_scaled_to_headroom"


def test_cash_buffer_headroom_scales_without_margin(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime = _admission_runtime(tmp_path)
    original = broker.account
    broker.account = lambda: original().__class__(  # type: ignore[method-assign]
        cash=41_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0,
        positions=tuple(broker.positions),
    )
    admission = runtime._size_buys_to_safe_capacity((_buy(),))
    assert admission.orders[0].quantity == 10
    assert admission.cash_headroom == 1000


def test_working_buy_consumes_headroom_and_broker_local_identity_counts_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime = _admission_runtime(tmp_path)
    broker.working_buy_orders = lambda: (_working("existing", "MSFT", 50, Market.EQUITY),)  # type: ignore[attr-defined]
    runtime.repository.open_orders = lambda _account: (  # type: ignore[method-assign]
        OpenPortfolioOrder("existing", "MSFT", Market.EQUITY, "buy", 50, 0, "broker-existing", 100),
    )
    admission = runtime._size_buys_to_safe_capacity((_buy(),))
    assert admission.committed_buy_notional == 5000
    assert admission.orders[0].quantity == 50


def test_local_submitting_buy_reserves_headroom_before_broker_ack(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _broker, runtime = _admission_runtime(tmp_path)
    runtime.repository.open_orders = lambda _account: (  # type: ignore[method-assign]
        OpenPortfolioOrder("local-only", "MSFT", Market.EQUITY, "buy", 50, 0, "", 100),
    )
    admission = runtime._size_buys_to_safe_capacity((_buy(),))
    assert admission.committed_buy_notional == 5000
    assert admission.orders[0].quantity == 50


def test_unpriceable_manual_working_buy_blocks_new_buy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime = _admission_runtime(tmp_path)
    broker.working_buy_orders = lambda: (  # type: ignore[attr-defined]
        BrokerWorkingBuyOrder("manual", "MSFT", Market.EQUITY, 50, None, None),
    )
    admission = runtime._size_buys_to_safe_capacity((_buy(),))
    assert admission.orders == ()
    assert admission.reason == "committed_buy_unpriced"


def test_unknown_remaining_notional_blocks_new_buy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime = _admission_runtime(tmp_path)
    broker.working_buy_orders = lambda: (  # type: ignore[attr-defined]
        BrokerWorkingBuyOrder("manual", "MSFT", Market.EQUITY, None, None, None),
    )
    admission = runtime._size_buys_to_safe_capacity((_buy(),))
    assert admission.orders == ()
    assert admission.reason == "committed_buy_unpriced"


def test_broker_open_failure_never_means_zero_commitment(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime = _admission_runtime(tmp_path)
    broker.working_buy_orders = lambda: (_ for _ in ()).throw(TimeoutError("OPEN failed"))  # type: ignore[attr-defined]
    with pytest.raises(TimeoutError, match="OPEN failed"):
        runtime._size_buys_to_safe_capacity((_buy(),))


def test_alpaca_working_buy_adapter_uses_remaining_quantity() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._get_open_orders = lambda _request: (  # type: ignore[method-assign]
        SimpleNamespace(
            client_order_id="broker-working",
            symbol="AAPL",
            side="buy",
            qty="50",
            filled_qty="20",
            limit_price="100",
            stop_price=None,
            filled_avg_price="99",
            notional=None,
        ),
        SimpleNamespace(client_order_id="sell", symbol="MSFT", side="sell"),
    )
    (order,) = broker.working_buy_orders()
    assert order.client_order_id == "broker-working"
    assert order.remaining_quantity == 30
    assert order.reference_price == 100


def test_partial_fill_counts_position_plus_remaining_buy_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime = _admission_runtime(tmp_path, current_qty=520)
    broker.working_buy_orders = lambda: (_working("partial", "AAPL", 30, Market.EQUITY),)  # type: ignore[attr-defined]
    runtime.repository.open_orders = lambda _account: (  # type: ignore[method-assign]
        OpenPortfolioOrder("partial", "AAPL", Market.EQUITY, "buy", 50, 20, "broker-partial", 100),
    )
    admission = runtime._size_buys_to_safe_capacity((_buy(),))
    assert admission.realized_gross == 52_000
    assert admission.committed_buy_notional == 3000
    assert admission.orders[0].quantity == 50


def test_pending_cancel_does_not_free_headroom_until_terminal(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime = _admission_runtime(tmp_path, current_qty=520)
    broker.working_buy_orders = lambda: (_working("canceling", "MSFT", 30, Market.EQUITY),)  # type: ignore[attr-defined]
    pending = runtime._size_buys_to_safe_capacity((_buy(),))
    assert pending.orders[0].quantity == 50
    broker.working_buy_orders = lambda: ()  # type: ignore[attr-defined]
    terminal = runtime._size_buys_to_safe_capacity((_buy(),))
    assert terminal.orders[0].quantity == 80


def test_cross_market_working_buy_consumes_shared_headroom_both_directions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime = _admission_runtime(tmp_path)
    for working_market, candidate_market in ((Market.EQUITY, Market.CRYPTO), (Market.CRYPTO, Market.EQUITY)):
        symbol = "MSFT" if working_market == Market.EQUITY else "BTC/USD"
        candidate = "ETH/USD" if candidate_market == Market.CRYPTO else "TSLA"
        broker.working_buy_orders = lambda: (_working("cross", symbol, 80, working_market),)  # type: ignore[attr-defined]
        admission = runtime._size_buys_to_safe_capacity((_buy(candidate, 50, candidate_market),))
        assert admission.committed_buy_notional == 8000
        assert admission.orders[0].quantity == 20


def test_sequential_equity_crypto_decisions_share_committed_headroom(tmp_path) -> None:  # type: ignore[no-untyped-def]
    class CryptoTarget(_FixedTargetStrategy):
        name = "crypto_target"
        market = Market.CRYPTO

    for first_market in (Market.EQUITY, Market.CRYPTO):
        # Invested 0.88 leaves 0.98 - 0.88 == 0.10 of shared headroom.
        broker = _SimBroker((PositionSnapshot("MANUAL", 880, 100, 100, Market.EQUITY, managed_qty=0),))
        broker.working_buy_orders = lambda: tuple(  # type: ignore[attr-defined]
            _working(order.client_order_id, order.symbol, order.quantity, order.market)
            for order in broker.submitted
            if order.side == PortfolioOrderSide.BUY
        )
        repository = PortfolioStateRepository(tmp_path / f"shared-{first_market.value}.sqlite3")
        config = load_config(str(Path(__file__).resolve().parents[1] / "config" / "settings.yaml"))
        limits = portfolio_risk_limits_from_config(config.risk)
        runtimes = {
            Market.EQUITY: PortfolioRuntime(
                _FixedTargetStrategy({"AAPL": 0.08}),
                ("AAPL",),
                broker,
                repository,
                mode="paper",
                maximum_allocation=config.strategy.equity.maximum_allocation,
                risk_limits=limits,
                account_id="acct",
            ),
            Market.CRYPTO: PortfolioRuntime(
                CryptoTarget({"BTC/USD": 0.08}),
                ("BTC/USD",),
                broker,
                repository,
                mode="paper",
                maximum_allocation=config.strategy.crypto.maximum_allocation,
                risk_limits=limits,
                account_id="acct",
            ),
        }
        for market in (first_market, Market.CRYPTO if first_market == Market.EQUITY else Market.EQUITY):
            symbol = "AAPL" if market == Market.EQUITY else "BTC/USD"
            bar = Bar(datetime(2026, 1, 1, tzinfo=UTC), symbol, "1d", 100, 100, 100, 100, 1_000_000, market)
            runtimes[market].process_closed_bars([bar])
        assert len(broker.submitted) == 2
        assert sum(order.quantity * order.reference_price for order in broker.submitted) <= 10_000
        assert sorted(order.quantity for order in broker.submitted) == (
            [20, 80] if first_market == Market.EQUITY else [50, 50]
        )
        for runtime in runtimes.values():
            runtime.close()


def test_risk_reducing_sell_frees_capacity_only_after_broker_position_changes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime = _admission_runtime(tmp_path, current_qty=600)
    assert runtime._size_buys_to_safe_capacity((_buy(quantity=50),)).orders == ()
    broker.positions = [_position("AAPL", 550)]
    assert runtime._size_buys_to_safe_capacity((_buy(quantity=50),)).orders[0].quantity == 50


def test_operator_capacity_snapshot_reports_slots_orders_and_headroom(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, portfolio = _admission_runtime(tmp_path)
    broker.account_id = "acct"  # type: ignore[attr-defined]
    broker.working_buy_orders = lambda: ()  # type: ignore[attr-defined]
    broker.working_orders = lambda: (SimpleNamespace(symbol="MSFT"),)  # type: ignore[attr-defined]
    broker._field = lambda order, name, default=None: getattr(order, name, default)  # type: ignore[attr-defined]
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._portfolio_lock = threading.RLock()
    engine._portfolio_runtimes = (portfolio,)
    engine._portfolio_repository = portfolio.repository
    engine.broker = broker
    engine._market_entry_halts = {}
    engine._last_reconciliation_at = datetime(2026, 1, 1, tzinfo=UTC)
    engine._reconcile_failed = False
    snapshot = engine.capacity_health()
    assert snapshot["positions_count"] == 1
    assert snapshot["effective_slots"] == 2
    assert snapshot["broker_working_order_count"] == 1
    assert snapshot["available_gross_buy_headroom"] == 10_000
    assert snapshot["available_cash_buy_headroom"] == 10_000
    assert snapshot["reconciliation_status"] == "complete"
