"""Effective slot payload uses the same canonical claim union as account admission.

Foreign crypto positions/orders in the shared account are only classified:
they still claim global slots exactly as before crypto support was removed.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.portfolio.runtime_models import BuyAdmission
from parayabuck.runtime import EngineRuntime


def _position(symbol: str, market: Market) -> PositionSnapshot:
    return PositionSnapshot(symbol, 1, 100, 100, market, managed_qty=1)


def _capacity(
    positions: tuple[tuple[str, Market], ...],
    local: tuple[tuple[str, Market, str], ...] = (),
    broker: tuple[tuple[str, str], ...] = (),
) -> dict[str, object]:
    account = AccountSnapshot(
        cash=100_000 - len(positions) * 100,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0,
        positions=tuple(_position(symbol, market) for symbol, market in positions),
    )
    admission = BuyAdmission(account, (), len(positions) * 100, 0, 50_000, account.cash - 40_000, None)
    limits = SimpleNamespace(maximum_positions=8, maximum_gross=0.6)
    runtime = SimpleNamespace(
        current_buy_capacity=lambda: admission,
        risk_overlay=SimpleNamespace(limits=limits),
        last_buy_admission=None,
        market=Market.EQUITY,
    )
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._portfolio_lock = threading.RLock()
    engine._portfolio_runtimes = (runtime,)
    engine._portfolio_repository = SimpleNamespace(
        open_orders=lambda _: tuple(
            SimpleNamespace(symbol=symbol, market=market, side=side) for symbol, market, side in local
        )
    )
    engine.broker = SimpleNamespace(
        account_id="synthetic",
        working_orders=lambda: tuple(SimpleNamespace(symbol=symbol, side=side) for symbol, side in broker),
        _field=lambda order, name, default=None: getattr(order, name, default),
    )
    engine._market_entry_halts = {}
    engine._last_reconciliation_at = datetime(2026, 1, 1, tzinfo=UTC)
    engine._reconcile_failed = False
    return engine.capacity_health()


@pytest.mark.parametrize(
    ("positions", "local", "broker", "global_claims", "opening"),
    [
        (
            tuple((s, Market.EQUITY) for s in ("AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META"))
            + (("BTC/USD", Market.CRYPTO), ("LTC/USD", Market.CRYPTO)),
            (),
            (),
            8,
            0,
        ),
        (
            tuple((s, Market.EQUITY) for s in ("AAPL", "MSFT", "NVDA", "AMZN", "GOOG")) + (("BTC/USD", Market.CRYPTO),),
            (),
            (("LTC/USD", "buy"),),
            7,
            1,
        ),
        ((("QLD", Market.EQUITY),), (("QLD", Market.EQUITY, "buy"),), (("QLD", "buy"),), 1, 0),
        ((("QLD", Market.EQUITY), ("QQQ", Market.EQUITY)), (), (("QLD", "sell"),), 2, 0),
        ((("QLD", Market.EQUITY), ("QQQ", Market.EQUITY)), (), (("SPY", "buy"),), 3, 1),
        ((("QLD", Market.EQUITY), ("BTC/USD", Market.CRYPTO)), (), (), 2, 0),
        ((("BTC/USD", Market.CRYPTO),), (), (("BTC/USD", "sell"),), 1, 0),
    ],
)
def test_effective_slot_claims(
    positions: tuple[tuple[str, Market], ...],
    local: tuple[tuple[str, Market, str], ...],
    broker: tuple[tuple[str, str], ...],
    global_claims: int,
    opening: int,
) -> None:
    result = _capacity(positions, local, broker)
    assert result["effective_global_slot_claims"] == global_claims
    assert result["working_opening_order_count"] == opening
    assert result["global_slot_cap"] == 8
    assert result["global_slots_available"] == max(0, 8 - global_claims)
    assert len(result["slot_claims"]) == global_claims
    assert not any("crypto" in key for key in result)


def test_local_nonterminal_sell_reservation_matches_admission_conservatism() -> None:
    result = _capacity((), (("QLD", Market.EQUITY, "sell"),))
    assert result["effective_global_slot_claims"] == 1
    assert result["working_opening_order_count"] == 0
    assert result["slot_claims"][0]["sources"] == ["RESERVED"]


def test_liquidated_account_keeps_the_capacity_sample() -> None:
    """A fully liquidated account reports equity 0.0, so the sleeve
    percentages are undefined. Both callers swallow the resulting error and
    publish ``{"unavailable": True}`` — losing the slot claims, the broker
    order count and the halt reasons, not just the four percentages."""
    account = AccountSnapshot(cash=0, equity=0, peak_equity=10_000, daily_pnl=-10_000)
    admission = BuyAdmission(account, (), 0, 0, 0, 0, None)
    limits = SimpleNamespace(maximum_positions=8, maximum_gross=0.6)
    runtime = SimpleNamespace(
        current_buy_capacity=lambda: admission,
        risk_overlay=SimpleNamespace(limits=limits),
        last_buy_admission=None,
        market=Market.EQUITY,
    )
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._portfolio_lock = threading.RLock()
    engine._portfolio_runtimes = (runtime,)
    engine._portfolio_repository = SimpleNamespace(open_orders=lambda _: ())
    engine.broker = SimpleNamespace(
        account_id="synthetic",
        working_orders=lambda: (),
        _field=lambda order, name, default=None: getattr(order, name, default),
    )
    engine._market_entry_halts = {Market.EQUITY: "pb-stocks_restart_limit"}
    engine._last_reconciliation_at = datetime(2026, 1, 1, tzinfo=UTC)
    engine._reconcile_failed = False

    sample = engine.capacity_health()

    assert "unavailable" not in sample
    # The rest of the sample survives; only the undefined ratios are absent.
    assert sample["halt_reasons"] == {"equity": "pb-stocks_restart_limit"}
    assert sample["global_slot_cap"] == 8
    assert sample["positions_count"] == 0
    for key in ("realized_gross_pct", "cash_ratio_pct", "equity_sleeve_pct"):
        assert sample[key] is None


def test_capacity_sample_does_not_hold_portfolio_lock_across_broker_io() -> None:
    """Dashboard polls must not serialize order submission behind the broker
    and ledger round-trips of a capacity sample."""
    lock = threading.Lock()
    held: list[tuple[str, bool]] = []
    account = AccountSnapshot(cash=100_000, equity=100_000, peak_equity=100_000, daily_pnl=0)
    admission = BuyAdmission(account, (), 0, 0, 50_000, 60_000, None)

    def capacity() -> BuyAdmission:
        held.append(("admission", lock.locked()))
        return admission

    def working_orders() -> tuple[object, ...]:
        held.append(("working_orders", lock.locked()))
        return ()

    def open_orders(_account_id: str) -> tuple[object, ...]:
        held.append(("open_orders", lock.locked()))
        return ()

    engine = EngineRuntime.__new__(EngineRuntime)
    engine._portfolio_lock = lock
    engine._portfolio_runtimes = (
        SimpleNamespace(
            current_buy_capacity=capacity,
            risk_overlay=SimpleNamespace(
                limits=SimpleNamespace(maximum_positions=8, maximum_gross=0.6)
            ),
            last_buy_admission=None,
            market=Market.EQUITY,
        ),
    )
    engine._portfolio_repository = SimpleNamespace(open_orders=open_orders)
    engine.broker = SimpleNamespace(account_id="synthetic", working_orders=working_orders)
    engine._market_entry_halts = {}

    sample = engine.capacity_health()

    assert held == [("admission", False), ("working_orders", False), ("open_orders", False)]
    assert sample["global_slot_cap"] == 8
