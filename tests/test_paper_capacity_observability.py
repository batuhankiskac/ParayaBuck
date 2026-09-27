"""Effective slot payload uses the same canonical claim union as account admission."""

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
    limits = SimpleNamespace(maximum_positions=8, maximum_crypto_positions=2, maximum_gross=0.6)
    runtime = SimpleNamespace(
        current_buy_capacity=lambda: admission,
        risk_overlay=SimpleNamespace(limits=limits),
        last_buy_admission=None,
        market=Market.CRYPTO,
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
    ("positions", "local", "broker", "global_claims", "crypto_claims", "opening"),
    [
        (
            tuple((s, Market.EQUITY) for s in ("AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META"))
            + (("BTC/USD", Market.CRYPTO), ("LTC/USD", Market.CRYPTO)),
            (),
            (),
            8,
            2,
            0,
        ),
        (
            tuple((s, Market.EQUITY) for s in ("AAPL", "MSFT", "NVDA", "AMZN", "GOOG")) + (("BTC/USD", Market.CRYPTO),),
            (),
            (("LTC/USD", "buy"),),
            7,
            2,
            1,
        ),
        ((("BTC/USD", Market.CRYPTO),), (("BTCUSD", Market.CRYPTO, "buy"),), (("BTC/USD", "buy"),), 1, 1, 0),
        ((("BTC/USD", Market.CRYPTO), ("LTC/USD", Market.CRYPTO)), (), (("BTC/USD", "sell"),), 2, 2, 0),
        ((("BTC/USD", Market.CRYPTO), ("LTC/USD", Market.CRYPTO)), (), (("DOGE/USD", "buy"),), 3, 3, 1),
        ((("BTC/USD", Market.CRYPTO), ("LTC/USD", Market.CRYPTO)), (), (), 2, 2, 0),
        ((("DOGE/USD", Market.CRYPTO),), (("DOGE/USD", Market.CRYPTO, "buy"),), (("DOGE/USD", "buy"),), 1, 1, 0),
    ],
)
def test_effective_slot_claims(
    positions: tuple[tuple[str, Market], ...],
    local: tuple[tuple[str, Market, str], ...],
    broker: tuple[tuple[str, str], ...],
    global_claims: int,
    crypto_claims: int,
    opening: int,
) -> None:
    result = _capacity(positions, local, broker)
    assert result["effective_global_slot_claims"] == global_claims
    assert result["effective_crypto_slot_claims"] == crypto_claims
    assert result["working_opening_order_count"] == opening
    assert result["global_slot_cap"] == 8
    assert result["crypto_slot_cap"] == 2
    assert result["global_slots_available"] == max(0, 8 - global_claims)
    assert result["crypto_slots_available"] == max(0, 2 - crypto_claims)
    assert len(result["slot_claims"]) == global_claims


def test_local_nonterminal_sell_reservation_matches_admission_conservatism() -> None:
    result = _capacity((), (("BTC/USD", Market.CRYPTO, "sell"),))
    assert result["effective_crypto_slot_claims"] == 1
    assert result["working_opening_order_count"] == 0
    assert result["slot_claims"][0]["sources"] == ["RESERVED"]


def test_liquidated_account_keeps_the_capacity_sample() -> None:
    """A fully liquidated account reports equity 0.0, so the sleeve
    percentages are undefined. Both callers swallow the resulting error and
    publish ``{"unavailable": True}`` — losing the slot claims, the broker
    order count and the halt reasons, not just the four percentages."""
    account = AccountSnapshot(cash=0, equity=0, peak_equity=10_000, daily_pnl=-10_000)
    admission = BuyAdmission(account, (), 0, 0, 0, 0, None)
    limits = SimpleNamespace(maximum_positions=8, maximum_crypto_positions=2, maximum_gross=0.6)
    runtime = SimpleNamespace(
        current_buy_capacity=lambda: admission,
        risk_overlay=SimpleNamespace(limits=limits),
        last_buy_admission=None,
        market=Market.CRYPTO,
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
    engine._market_entry_halts = {Market.CRYPTO: "crypto_protection_failure"}
    engine._last_reconciliation_at = datetime(2026, 1, 1, tzinfo=UTC)
    engine._reconcile_failed = False

    sample = engine.capacity_health()

    assert "unavailable" not in sample
    # The rest of the sample survives; only the undefined ratios are absent.
    assert sample["halt_reasons"] == {"crypto": "crypto_protection_failure"}
    assert sample["global_slot_cap"] == 8
    assert sample["positions_count"] == 0
    for key in ("realized_gross_pct", "cash_ratio_pct", "equity_sleeve_pct", "crypto_sleeve_pct"):
        assert sample[key] is None
