"""R1 (LIVE gap-safe BUY cap) and R3 (rejected exit-SELL escalation + one retry).

Offline: a simulated broker and a tmp_path ledger (docs/live_readiness_v1.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.alpaca_models import DefiniteOrderRejection
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, PortfolioRiskLimits
from parayabuck.monitoring import alerts
from parayabuck.portfolio.runtime_service import EXIT_SELL_RETRY_SUFFIX
from tests.test_two_phase_rebalance import _bars, _position, _runtime, _SimBroker

_LIMITS = PortfolioRiskLimits(
    daily_loss_limit=0.20,
    drawdown_halt=0.75,
    maximum_gross=0.98,
    maximum_equity=0.98,
    maximum_equity_asset=0.98,
    minimum_cash=0.02,
    maximum_positions=8,
)


@pytest.fixture(autouse=True)
def _alerts(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr("parayabuck.monitoring.journal.emit_journal", lambda event: None)
    alerts.reset_for_tests()
    alerts._throttle_seconds = 0.0
    yield
    alerts.reset_for_tests()


def _delivered(kind: str) -> int:
    delivered = alerts.alert_stats()["delivered"]
    assert isinstance(delivered, dict)
    return int(delivered.get(kind, 0))


# --- R1 ----------------------------------------------------------------------


class _CashBroker(_SimBroker):
    def __init__(self, *, non_marginable: float | None = None) -> None:
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


def _buy(quantity: float) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id="buy-qld",
        decision_id="decision",
        symbol="QLD",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=quantity,
        reference_price=100.0,
        decision_timestamp=datetime(2026, 10, 9, tzinfo=UTC),
        strategy_name="fixed_target",
        strategy_version="1.0.0",
    )


def _admitted(tmp_path, broker: _CashBroker, mode: str) -> float:  # type: ignore[no-untyped-def]
    runtime, _repository = _runtime(
        tmp_path, broker, {"QLD": 0.98}, mode=mode, cap=1_000_000.0 if mode == "live" else 0.0, limits=_LIMITS
    )
    admission = runtime._size_buys_to_safe_capacity((_buy(980),))
    return sum(order.quantity for order in admission.orders)


def test_paper_buy_sizing_is_unchanged(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # 0.98 x 100k from flat: the 2 % cash buffer alone bounds it (as before).
    assert _admitted(tmp_path, _CashBroker(non_marginable=50_000.0), "paper") == 980


def test_live_buy_leaves_room_for_a_three_percent_gap(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # 100k / 1.03 = 97,087 -> 970 shares at 100: a +3 % open costs 99,910.
    assert _admitted(tmp_path, _CashBroker(), "live") == 970


def test_live_buy_never_exceeds_non_marginable_buying_power(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # 50k / 1.03 = 48,543 -> 485 shares.
    assert _admitted(tmp_path, _CashBroker(non_marginable=50_000.0), "live") == 485


def test_live_gap_margin_is_configurable_and_validated(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from parayabuck.config import LiveConfig

    assert LiveConfig().buy_gap_margin_pct == 0.03
    with pytest.raises(ValueError, match="buy_gap_margin_pct"):
        LiveConfig(buy_gap_margin_pct=0.6)
    runtime, _repository = _runtime(tmp_path, _CashBroker(), {"QLD": 0.98}, mode="live", cap=1e6, limits=_LIMITS)
    runtime.live_buy_gap_margin = 0.0
    assert sum(order.quantity for order in runtime._size_buys_to_safe_capacity((_buy(980),)).orders) == 980


def test_alpaca_non_marginable_buying_power_is_parsed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tests.fake_client import FakeTradingClient
    from tests.helpers import make_broker

    client = FakeTradingClient()
    broker = make_broker(tmp_path, client=client)
    raw = client.get_account()
    raw.non_marginable_buying_power = "1234.5"
    client.script_get_account = lambda: raw
    snapshot = broker.account()
    assert snapshot.non_marginable_buying_power == 1234.5
    assert broker.account_number == "fake-acct-1234"
    raw.non_marginable_buying_power = None
    assert broker.account().non_marginable_buying_power is None


# --- R3 ----------------------------------------------------------------------


class _RejectingBroker(_SimBroker):
    """Rejects SELLs while ``reject_sells`` is set (a definite 4xx)."""

    def __init__(self) -> None:
        super().__init__((_position("QLD", 500),))
        self.reject_sells = True

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        if order.side == PortfolioOrderSide.SELL and self.reject_sells:
            self.submitted.append(order)
            raise DefiniteOrderRejection(f"rejected {order.client_order_id}")
        return super().submit_portfolio_order(order)


def _rejected_exit(tmp_path):  # type: ignore[no-untyped-def]
    broker = _RejectingBroker()
    runtime, repository = _runtime(tmp_path, broker, {"QLD": 0.2}, limits=_LIMITS)
    result = runtime.process_closed_bars(_bars(("QLD",)))
    assert result is not None
    return broker, runtime, repository, result.decision_id


def test_rejected_exit_sell_is_escalated(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, _runtime_, repository, decision_id = _rejected_exit(tmp_path)
    (row,) = repository.orders_for_decision(decision_id, side="sell")
    assert row["status"] == "rejected"
    assert _delivered("order_rejected") == 1
    assert _delivered("exit_sell_rejected") == 1


def test_rejected_exit_sell_is_retried_once_with_a_new_deterministic_id(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime, repository, decision_id = _rejected_exit(tmp_path)
    (original,) = repository.orders_for_decision(decision_id, side="sell")
    broker.reject_sells = False
    submitted = runtime.retry_rejected_exit_sells(broker.account())
    assert len(submitted) == 1
    retry = broker.submitted[-1]
    assert retry.client_order_id == f"{original['client_order_id']}{EXIT_SELL_RETRY_SUFFIX}"
    assert (retry.side, retry.symbol, retry.quantity, retry.decision_id) == (
        PortfolioOrderSide.SELL,
        "QLD",
        original["quantity"],
        decision_id,
    )
    # Never a second retry, whatever happens next.
    broker.open_symbols.clear()
    assert runtime.retry_rejected_exit_sells(broker.account()) == ()
    assert len(broker.submitted) == 2


def test_rejected_retry_escalates_and_never_loops(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime, repository, decision_id = _rejected_exit(tmp_path)
    assert runtime.retry_rejected_exit_sells(broker.account()) == ()
    statuses = sorted(
        (row["client_order_id"].endswith(EXIT_SELL_RETRY_SUFFIX), row["status"])
        for row in repository.orders_for_decision(decision_id, side="sell")
    )
    assert statuses == [(False, "rejected"), (True, "rejected")]
    assert _delivered("exit_sell_rejected") == 2
    for _ in range(3):
        assert runtime.retry_rejected_exit_sells(broker.account()) == ()
    assert len(broker.submitted) == 2


def test_retry_is_resized_to_the_managed_position_and_skipped_when_flat(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime, _repository, _decision_id = _rejected_exit(tmp_path)
    broker.reject_sells = False
    broker.positions = [_position("QLD", 120)]
    runtime.retry_rejected_exit_sells(broker.account())
    assert broker.submitted[-1].quantity == 120

    flat_broker, flat_runtime, _repo, _decision = _rejected_exit(tmp_path / "flat")
    flat_broker.reject_sells = False
    flat_broker.positions = []
    assert flat_runtime.retry_rejected_exit_sells(flat_broker.account()) == ()


def test_retry_waits_while_an_order_in_the_symbol_is_open(tmp_path) -> None:  # type: ignore[no-untyped-def]
    broker, runtime, _repository, _decision_id = _rejected_exit(tmp_path)
    broker.reject_sells = False
    broker.open_symbols.add("QLD")
    assert runtime.retry_rejected_exit_sells(broker.account()) == ()
    broker.open_symbols.clear()
    assert len(runtime.retry_rejected_exit_sells(broker.account())) == 1


def test_engine_retries_only_at_a_safe_opportunity() -> None:
    from parayabuck.runtime import EngineRuntime

    calls: list[object] = []
    portfolio = SimpleNamespace(
        market=Market.EQUITY, retry_rejected_exit_sells=lambda account, **_: calls.append(account)
    )
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._portfolio_runtimes = (portfolio,)
    runtime._portfolio_lock = __import__("threading").RLock()
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime._running = True
    runtime._startup_committed = True
    runtime._warming_up = False
    runtime._reconcile_failed = False
    for attribute, unsafe in (
        ("_circuit_breaker_reason", "ownership_drift"),
        ("_warming_up", True),
        ("_reconcile_failed", True),
    ):
        runtime._circuit_breaker_reason = None
        runtime._warming_up = False
        runtime._reconcile_failed = False
        setattr(runtime, attribute, unsafe)
        runtime._maybe_retry_rejected_exit_sells("account")  # type: ignore[arg-type]
    runtime._circuit_breaker_reason = None
    runtime._warming_up = False
    runtime._reconcile_failed = False
    runtime._market_entry_halts = {Market.EQUITY: "rebalance_reconciliation_required"}
    runtime._maybe_retry_rejected_exit_sells("account")  # type: ignore[arg-type]
    assert calls == []
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime._maybe_retry_rejected_exit_sells("account")  # type: ignore[arg-type]
    assert calls == ["account"]
