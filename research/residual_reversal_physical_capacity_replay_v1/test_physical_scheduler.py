from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioFill, PortfolioOrder, _Position
from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from research.residual_reversal_physical_capacity_replay_v1.physical_scheduler import (
    SharedAccountReplay,
    _structural_event,
    _structural_invariants,
)


def test_structural_scheduler_output_excludes_economics() -> None:
    event = _structural_event(
        {
            "event": "fill",
            "market": "equity",
            "timestamp": "2026-01-02T14:30:00+00:00",
            "symbol": "ABC",
            "qty": 2.0,
            "order_created_at": "2025-12-31T21:00:00+00:00",
            "source_price_event_time": "2026-01-02T14:30:00+00:00",
            "fill_price": 123.0,
            "fee": 1.0,
            "nav": 100_001.0,
            "pnl": 2.0,
        }
    )
    assert set(event) == {
        "event",
        "market",
        "timestamp",
        "symbol",
        "qty",
        "order_created_at",
        "source_price_event_time",
    }
    assert _structural_invariants(
        {"slot_claims_within_limit": True, "ending_cash": 99_000.0, "maximum_realized_gross_observed": 0.5}
    ) == {"slot_claims_within_limit": True}


def test_structural_scheduler_restricts_caps() -> None:
    assert SharedAccountReplay(maximum_positions=6).maximum_positions == 6
    assert SharedAccountReplay(maximum_positions=8).maximum_positions == 8


def test_decision_claim_instrumentation_does_not_change_authority_hash() -> None:
    replay = SharedAccountReplay(maximum_positions=6)
    authority = {"event": "decision", "market": "equity", "timestamp": "2026-01-02T21:00:00+00:00"}
    replay._events = [{**authority, "slot_claims_before": 0, "slot_claims_after": 0}]
    expected = hashlib.sha256(json.dumps([authority], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert replay.internal_event_sha256 == expected
    assert replay._claim_snapshot()["slot_claims"] == 0
    at = datetime(2026, 1, 2, tzinfo=UTC)
    replay.account.pending[Market.EQUITY].append(
        PortfolioOrder("buy-a", "ABC", Market.EQUITY, OrderSide.BUY, 2.0, at, at)
    )
    replay.account.pending[Market.CRYPTO].append(
        PortfolioOrder("sell-b", "BTC/USD", Market.CRYPTO, OrderSide.SELL, 1.0, at, at)
    )
    snapshot = replay._claim_snapshot()
    assert snapshot["pending_opening_keys"] == [["equity", "ABC"]]
    assert snapshot["pending_sell_keys"] == [["crypto", "BTC/USD"]]
    assert snapshot["slot_claims"] == 2
    assert snapshot["crypto_claim_count"] == 1


def test_source_admission_records_explicit_count_denial() -> None:
    replay = SharedAccountReplay(maximum_positions=6)
    at = datetime(2026, 1, 2, tzinfo=UTC)
    raw_target = TargetPortfolio(
        "equity_residual_momentum_v3",
        "3.0.0",
        at,
        (TargetWeight("ABC", 0.1, rank=1),),
        0.9,
    )
    engine = SimpleNamespace(
        strategy=SimpleNamespace(name="equity_residual_momentum_v3", market=Market.EQUITY),
        _shared_overlay=SimpleNamespace(last_raw_target=raw_target, limits=PortfolioRiskLimits()),
        maximum_allocation=0.55,
        _pending=[],
        _last_price={"ABC": 100.0},
    )
    before = {
        "position_keys": [["equity", f"OLD{i}"] for i in range(6)],
        "pending_opening_keys": [],
        "pending_sell_keys": [],
        "slot_claims": 6,
        "crypto_claim_count": 0,
    }
    after = {**before, "crypto_count": 0}
    record = SimpleNamespace(target_weights=(), risk_overlay_reasons=("position_count_limit",))
    replay._record_source_admissions(engine, at, at, record, before, after, set())
    row = replay._events[-1]
    assert row["event"] == "source_admission"
    assert row["source"] == "v3"
    assert row["requested_opening"] is True
    assert row["admitted_opening"] is False
    assert row["denial_reason"] == "GLOBAL_POSITION_COUNT"
    assert row["requested_target_contribution"] == 0.1
    assert row["admitted_target_contribution"] == 0.0


def test_held_position_target_removed_is_not_count_denial() -> None:
    replay = SharedAccountReplay(maximum_positions=6)
    at = datetime(2026, 1, 2, tzinfo=UTC)
    replay.account.positions[(Market.EQUITY, "ABC")] = _Position("ABC", Market.EQUITY, "1d", 1.0, 100.0, at)
    replay._source_qty[(Market.EQUITY, "ABC")] = {"v3": 1.0}
    target = TargetPortfolio("equity_residual_momentum_v3", "3.0.0", at, (TargetWeight("ABC", 0.1, rank=1),), 0.9)
    engine = SimpleNamespace(
        strategy=SimpleNamespace(name="equity_residual_momentum_v3", market=Market.EQUITY),
        _shared_overlay=SimpleNamespace(last_raw_target=target, limits=PortfolioRiskLimits()),
        maximum_allocation=0.55,
        _pending=[],
        _last_price={"ABC": 100.0},
    )
    claims = {
        "position_keys": [["equity", "ABC"]],
        "pending_opening_keys": [],
        "pending_sell_keys": [],
        "slot_claims": 1,
        "crypto_claim_count": 0,
        "crypto_count": 0,
    }
    record = SimpleNamespace(target_weights=(), risk_overlay_reasons=("position_count_limit",))
    replay._record_source_admissions(engine, at, at, record, claims, claims, set())
    row = replay._events[0]
    assert row["requested_opening"] is False
    assert row["existing_position"] is True
    assert row["denial_reason"] == "OTHER_EXPLICIT"
    assert row["denial_detail"] == "existing_position_target_removed"


@pytest.mark.parametrize(
    "reasons", (("position_count_limit",), ("daily_loss_limit", "portfolio_drawdown_halt", "position_count_limit"))
)
def test_halt_or_spare_slots_do_not_claim_count_causality(reasons: tuple[str, ...]) -> None:
    replay = SharedAccountReplay(maximum_positions=6)
    at = datetime(2026, 1, 2, tzinfo=UTC)
    target = TargetPortfolio("equity_residual_momentum_v3", "3.0.0", at, (TargetWeight("ASML", 0.05, rank=1),), 0.95)
    engine = SimpleNamespace(
        strategy=SimpleNamespace(name="equity_residual_momentum_v3", market=Market.EQUITY),
        _shared_overlay=SimpleNamespace(last_raw_target=target, limits=PortfolioRiskLimits()),
        maximum_allocation=0.55,
        _pending=[],
        _last_price={"ASML": 100.0},
    )
    claims = {
        "position_keys": [["equity", f"OLD{i}"] for i in range(3)],
        "pending_opening_keys": [],
        "pending_sell_keys": [],
        "slot_claims": 3,
        "crypto_claim_count": 0,
        "crypto_count": 0,
    }
    replay._record_source_admissions(
        engine, at, at, SimpleNamespace(target_weights=(), risk_overlay_reasons=reasons), claims, claims, set()
    )
    row = replay._events[0]
    assert row["denial_reason"] == "OTHER_EXPLICIT"
    assert row["denial_detail"] == ",".join(reasons)


def test_overlap_source_admission_shares_one_opening() -> None:
    replay = SharedAccountReplay(maximum_positions=8)
    at = datetime(2026, 1, 2, tzinfo=UTC)
    target = TargetPortfolio(
        "equity_v3_plus_residual_reversal_physical_capacity_v1",
        "1.0.0-research",
        at,
        (TargetWeight("ABC", 0.1, rank=1),),
        0.9,
    )
    strategy = SimpleNamespace(
        name="equity_v3_plus_residual_reversal_physical_capacity_v1",
        market=Market.EQUITY,
        raw_decisions=[
            {
                "label_time": at.isoformat(),
                "source_contributions": {"ABC": {"v3": 0.06, "reversal": 0.04}},
                "v3_weights": {"ABC": 0.08},
                "reversal_weights": {"ABC": 0.05},
                "v3_symbols": ["ABC"],
                "reversal_symbols": ["ABC"],
            }
        ],
    )
    engine = SimpleNamespace(
        strategy=strategy,
        _shared_overlay=SimpleNamespace(last_raw_target=target, limits=PortfolioRiskLimits()),
        maximum_allocation=0.55,
        _pending=[PortfolioOrder("buy-abc", "ABC", Market.EQUITY, OrderSide.BUY, 1.0, at, at)],
        _last_price={"ABC": 100.0},
    )
    before = {
        "position_keys": [],
        "pending_opening_keys": [],
        "pending_sell_keys": [],
        "slot_claims": 0,
        "crypto_claim_count": 0,
    }
    after = {**before, "slot_claims": 1, "crypto_count": 0}
    record = SimpleNamespace(target_weights=(("ABC", 0.1),), risk_overlay_reasons=())
    replay._record_source_admissions(engine, at, at, record, before, after, {"ABC"})
    rows = replay._events
    assert [row["source"] for row in rows] == ["v3", "reversal"]
    assert all(row["overlap"] and row["admitted_opening"] for row in rows)
    assert sum(row["admitted_target_contribution"] for row in rows) == 0.1
    assert all(row["combined_account_target"] == 0.1 for row in rows)


def test_existing_working_buy_is_not_a_new_opening_denial() -> None:
    replay = SharedAccountReplay(maximum_positions=8)
    at = datetime(2026, 1, 2, tzinfo=UTC)
    prior = datetime(2025, 12, 31, tzinfo=UTC)
    target = TargetPortfolio(
        "equity_residual_momentum_v3",
        "3.0.0",
        at,
        (TargetWeight("ABC", 0.1, rank=1),),
        0.9,
    )
    engine = SimpleNamespace(
        strategy=SimpleNamespace(name="equity_residual_momentum_v3", market=Market.EQUITY),
        _shared_overlay=SimpleNamespace(last_raw_target=target, limits=PortfolioRiskLimits()),
        maximum_allocation=0.55,
        _pending=[PortfolioOrder("old-buy", "ABC", Market.EQUITY, OrderSide.BUY, 1.0, prior, prior)],
        _last_price={"ABC": 100.0},
    )
    claims = {
        "position_keys": [],
        "pending_opening_keys": [["equity", "ABC"]],
        "pending_sell_keys": [],
        "slot_claims": 1,
        "crypto_claim_count": 0,
        "crypto_count": 0,
    }
    record = SimpleNamespace(target_weights=(("ABC", 0.1),), risk_overlay_reasons=())
    replay._record_source_admissions(engine, at, at, record, claims, claims, set())
    row = replay._events[0]
    assert row["working_opening_reservation"] is True
    assert row["requested_opening"] is False
    assert row["admitted_opening"] is False
    assert row["admitted_target_contribution"] == 0.1
    assert row["denial_reason"] is None


def test_crypto_market_cap_has_its_own_denial_reason() -> None:
    replay = SharedAccountReplay(maximum_positions=8)
    at = datetime(2026, 1, 2, tzinfo=UTC)
    target = TargetPortfolio(
        "crypto_low_variance_hysteresis_v1",
        "1.0.0",
        at,
        (TargetWeight("ETH/USD", 0.05, rank=1),),
        0.95,
    )
    engine = SimpleNamespace(
        strategy=SimpleNamespace(name="crypto_low_variance_hysteresis_v1", market=Market.CRYPTO),
        _shared_overlay=SimpleNamespace(
            last_raw_target=target,
            limits=PortfolioRiskLimits(maximum_positions=8, maximum_crypto_positions=2),
        ),
        maximum_allocation=0.05,
        _pending=[],
        _last_price={"ETH/USD": 100.0},
    )
    claims = {
        "position_keys": [["crypto", "BTC/USD"], ["crypto", "SOL/USD"]],
        "pending_opening_keys": [],
        "pending_sell_keys": [],
        "slot_claims": 2,
        "crypto_claim_count": 2,
        "crypto_count": 2,
    }
    record = SimpleNamespace(target_weights=(), risk_overlay_reasons=("position_count_limit",))
    replay._record_source_admissions(engine, at, at, record, claims, claims, set())
    assert replay._events[0]["denial_reason"] == "CRYPTO_POSITION_COUNT"


@pytest.mark.parametrize("other_positions,working_reservation", ((0, False), (5, False), (0, True)))
def test_pending_sell_deferral_records_effective_claims_even_below_cap(
    other_positions: int, working_reservation: bool
) -> None:
    replay = SharedAccountReplay(maximum_positions=6)
    at = datetime(2026, 1, 2, tzinfo=UTC)

    class EngineView:
        def __init__(self, market: Market) -> None:
            self.market = market

        @property
        def _pending(self) -> list[PortfolioOrder]:
            return replay.account.pending[self.market]

        @_pending.setter
        def _pending(self, orders: list[PortfolioOrder]) -> None:
            replay.account.pending[self.market][:] = orders

    replay.inputs = {
        Market.CRYPTO: SimpleNamespace(engine=EngineView(Market.CRYPTO)),
        Market.EQUITY: SimpleNamespace(engine=EngineView(Market.EQUITY)),
    }
    for index in range(other_positions):
        symbol = f"OLD{index}"
        replay.account.positions[(Market.EQUITY, symbol)] = _Position(symbol, Market.EQUITY, "1d", 1.0, 100.0, at)
    replay.account.positions[(Market.CRYPTO, "BTC/USD")] = _Position("BTC/USD", Market.CRYPTO, "1d", 1.0, 100.0, at)
    replay.account.pending[Market.CRYPTO].append(
        PortfolioOrder("sell-btc", "BTC/USD", Market.CRYPTO, OrderSide.SELL, 1.0, at, at)
    )
    replay.account.pending[Market.EQUITY].append(
        PortfolioOrder("buy-new", "NEW", Market.EQUITY, OrderSide.BUY, 1.0, at, at)
    )
    stamp = at.isoformat()
    replay._events = [
        {
            "event": "decision",
            "timestamp": stamp,
            "slot_claims_after": other_positions + 2,
            "position_keys_after": [],
            "pending_opening_keys_after": [],
            "pending_sell_keys_after": [],
            "crypto_count_after": 1,
            "crypto_claim_count_after": 1,
        },
        {
            "event": "source_admission",
            "timestamp": stamp,
            "market": "equity",
            "symbol": "NEW",
            "existing_position": False,
            "working_opening_reservation": working_reservation,
            "admitted_opening": True,
            "admitted_target_contribution": 0.05,
            "denial_reason": None,
            "slot_claims_after": other_positions + 2,
            "crypto_count": 1,
        },
    ]
    replay._defer_decision_buys_while_sells_work(at, [Market.CRYPTO, Market.EQUITY])
    replay._finalize_decision_capacity_records(at, 0)
    decision, source = replay._events[:2]
    assert decision["slot_claims_after"] == source["slot_claims_after"] == other_positions + 1
    assert source["admitted_opening"] is False
    assert source["admitted_target_contribution"] == 0.0
    assert source["deferred_target_contribution"] == 0.05
    assert _structural_event(source)["deferred_target_contribution"] == 0.05
    assert source["denial_reason"] == ("OTHER_EXPLICIT" if working_reservation else "PENDING_SELL_SLOT_NOT_RELEASED")
    assert source["denial_detail"] == (
        "working_opening_replaced_deferred" if working_reservation else "buy_intent_deferred_until_sell_terminal"
    )
    assert source["pending_sell"] is True
    assert replay.account.deferred_buys[Market.EQUITY][0].symbol == "NEW"


def test_source_inventory_tracks_partial_fill_and_source_exit() -> None:
    replay = SharedAccountReplay(maximum_positions=8)
    at = datetime(2026, 1, 2, tzinfo=UTC)
    key = (Market.EQUITY, "ABC")
    order_key = (Market.EQUITY, "order-1")
    replay.account.positions[key] = _Position("ABC", Market.EQUITY, "1d", 5.0, 100.0, at)
    replay._order_desired[order_key] = {"v3": 6.0, "reversal": 4.0}
    buy = PortfolioFill(at, "ABC", Market.EQUITY, OrderSide.BUY, 5.0, 100.0, 100.0, 0.0, 0.0, 0.0, at, "order-1")
    replay._apply_source_fill(buy)
    assert replay._source_qty[key] == {"v3": 3.0, "reversal": 2.0}
    replay.account.positions[key].qty = 10.0
    replay._apply_source_fill(buy)
    assert replay._source_qty[key] == {"v3": 6.0, "reversal": 4.0}
    replay._order_desired[(Market.EQUITY, "order-2")] = {"v3": 6.0}
    replay.account.positions[key].qty = 6.0
    sell = PortfolioFill(at, "ABC", Market.EQUITY, OrderSide.SELL, 4.0, 100.0, 100.0, 0.0, 0.0, 0.0, at, "order-2")
    replay._apply_source_fill(sell)
    assert replay._source_qty[key] == {"v3": 6.0}
    assert replay._events[-1]["source_allocation"] == {"reversal": -4.0}


def test_split_preserves_source_lineage_and_adjusts_pending_claim() -> None:
    replay = SharedAccountReplay(maximum_positions=8)
    at = datetime(2026, 1, 2, tzinfo=UTC)
    key = (Market.EQUITY, "ABC")
    replay._source_qty[key] = {"v3": 3.0, "reversal": 2.0}
    replay.account.positions[key] = _Position("ABC", Market.EQUITY, "1d", 10.0, 50.0, at)
    order = PortfolioOrder("buy-abc", "ABC", Market.EQUITY, OrderSide.BUY, 1.0, at, at)
    replay.account.pending[Market.EQUITY].append(order)
    replay._order_desired[(Market.EQUITY, "buy-abc")] = {"v3": 6.0, "reversal": 4.0}
    replay._apply_source_split(at, Market.EQUITY, "ABC", 2.0)
    assert replay._source_qty[key] == {"v3": 6.0, "reversal": 4.0}
    assert replay._order_desired[(Market.EQUITY, "buy-abc")] == {"v3": 12.0, "reversal": 8.0}
    assert [event["event"] for event in replay._events] == ["source_inventory", "source_order_adjustment"]
    assert replay._events[-1]["split_ratio"] == 2.0


@pytest.mark.parametrize("btc_residual,expect_doge", ((0.0582293832077028, False), (0.0, True)))
def test_deferred_crypto_buy_rechecks_crypto_slots_after_btc_sell(btc_residual: float, expect_doge: bool) -> None:
    """A terminal SELL order releases its reservation, not its residual inventory."""
    replay = SharedAccountReplay(maximum_positions=8)
    at = datetime(2026, 6, 29, 1, tzinfo=UTC)

    class EngineView:
        def _account_snapshot(self) -> AccountSnapshot:
            return AccountSnapshot(100_000.0, 100_000.0, 100_000.0, 0.0, replay.account.snapshots())

        _shared_overlay = SimpleNamespace(limits=PortfolioRiskLimits(maximum_positions=8, maximum_crypto_positions=2))

        @property
        def _pending(self) -> list[PortfolioOrder]:
            return replay.account.pending[Market.CRYPTO]

        @_pending.setter
        def _pending(self, orders: list[PortfolioOrder]) -> None:
            replay.account.pending[Market.CRYPTO][:] = orders

    replay.inputs = {Market.CRYPTO: SimpleNamespace(engine=EngineView())}
    for index in range(5):
        symbol = f"EQ{index}"
        replay.account.positions[(Market.EQUITY, symbol)] = _Position(symbol, Market.EQUITY, "1d", 1.0, 100.0, at)
    replay.account.positions[(Market.CRYPTO, "LTC/USD")] = _Position("LTC/USD", Market.CRYPTO, "1d", 1.0, 100.0, at)
    if btc_residual:
        replay.account.positions[(Market.CRYPTO, "BTC/USD")] = _Position(
            "BTC/USD", Market.CRYPTO, "1d", btc_residual, 100.0, at
        )
    doge = PortfolioOrder("buy-doge", "DOGE/USD", Market.CRYPTO, OrderSide.BUY, 100.0, at, at)
    replay.account.deferred_buys[Market.CRYPTO].append(doge)

    # BTC's SELL is terminal. A partial fill still leaves its physical position.
    assert not replay.account.pending[Market.CRYPTO]
    replay._activate_deferred_buys(at, [Market.CRYPTO])

    assert (doge in replay.account.pending[Market.CRYPTO]) is expect_doge
    assert replay.account.deferred_buys[Market.CRYPTO] == []
    assert [event["event"] for event in replay._events] == [
        "buy_intent_activated" if expect_doge else "buy_intent_cancelled"
    ]
    if not expect_doge:
        assert replay._events[0]["reason"] == "crypto_position_count_limit"
    assert replay._claim_snapshot()["crypto_claim_count"] <= 2


@pytest.mark.parametrize(
    "market,close_reason", ((Market.CRYPTO, "precommitted_terminal"), (Market.EQUITY, "final_liquidation"))
)
def test_market_close_cancels_deferred_buy_before_later_action_only_timestamp(
    market: Market, close_reason: str
) -> None:
    replay = SharedAccountReplay(maximum_positions=8)
    terminal_at = datetime(2026, 7, 31, tzinfo=UTC)
    later_action_at = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    symbol = "DOGE/USD" if market == Market.CRYPTO else "ABC"

    class EngineView:
        def _account_snapshot(self) -> AccountSnapshot:
            return AccountSnapshot(100_000.0, 100_000.0, 100_000.0, 0.0)

        _shared_overlay = SimpleNamespace(limits=PortfolioRiskLimits(maximum_positions=8, maximum_crypto_positions=2))

        @property
        def _pending(self) -> list[PortfolioOrder]:
            return replay.account.pending[market]

        @_pending.setter
        def _pending(self, orders: list[PortfolioOrder]) -> None:
            replay.account.pending[market][:] = orders

    replay.inputs = {market: SimpleNamespace(engine=EngineView())}
    replay.account.deferred_buys[market].append(
        PortfolioOrder("deferred-buy", symbol, market, OrderSide.BUY, 1.0, terminal_at, terminal_at)
    )

    replay._close_market_intents(terminal_at, market, close_reason)
    replay._activate_deferred_buys(later_action_at, [market])

    assert replay.account.deferred_buys[market] == replay.account.pending[market] == []
    assert replay._claim_snapshot()["slot_claims"] == 0
    assert [(event["event"], event["timestamp"], event["reason"]) for event in replay._events] == [
        ("buy_intent_cancelled", terminal_at.isoformat(), close_reason)
    ]
