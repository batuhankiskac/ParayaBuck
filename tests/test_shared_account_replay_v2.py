from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioOrder, _Position
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    reserve_opening_order_slots,
)
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason, TargetPortfolio, TargetWeight
from research.shared_account_replay_v2.replay import (
    MarketReplayInput,
    SharedAccount,
    SharedAccountReplay,
    _admit_synthetic_orders,
    _SharedEngine,
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


class _FixedStrategy(PortfolioStrategy):
    name = "synthetic_fixed_test"
    version = "1.0.0"
    timeframe = "1d"

    def __init__(self, market: Market, weights: dict[str, float], schedule: dict[int, dict[str, float]] | None = None):
        self.market = market
        self._weights = dict(weights)
        self._schedule = schedule or {}
        super().__init__()

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weights = self._schedule.get(context.decision_timestamp.month, self._weights)
        target_weights = tuple(
            TargetWeight(symbol, weight, rank=index)
            for index, (symbol, weight) in enumerate(sorted(weights.items()), start=1)
            if weight > 0
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=target_weights,
            cash_weight=max(0.0, 1.0 - sum(weight.weight for weight in target_weights)),
            rebalance_reason=RebalanceReason.SCHEDULED if target_weights else RebalanceReason.RISK_OFF,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
        )


def _limits(*, slots: int = 6, gross: float = 0.8, sleeve: float = 0.8, per_name: float = 0.8) -> PortfolioRiskLimits:
    return PortfolioRiskLimits(
        daily_loss_limit=0.99,
        drawdown_halt=0.99,
        maximum_gross=gross,
        maximum_equity=sleeve,
        maximum_crypto=sleeve,
        maximum_equity_asset=per_name,
        maximum_crypto_asset=per_name,
        minimum_cash=max(0.0, 1.0 - gross),
        maximum_positions=slots,
        maximum_crypto_positions=slots,
    )


def _engine(
    replay: SharedAccountReplay,
    market: Market,
    weights: dict[str, float],
    *,
    schedule: dict[int, dict[str, float]] | None = None,
    slots: int = 6,
    gross: float = 0.8,
    sleeve: float = 0.8,
    per_name: float = 0.8,
    maximum_allocation: float = 0.8,
    max_participation_pct: float = 0.1,
    frequency: str | None = None,
) -> _SharedEngine:
    strategy = _FixedStrategy(market, weights, schedule)
    frequency = frequency or ("monthly" if market == Market.EQUITY else "hours:24")
    return _SharedEngine(
        replay.account,
        strategy,
        RebalancePolicy(market, frequency),
        starting_equity=replay.account.starting_nav,
        slippage_bps=0.0,
        spread_bps=0.0,
        commission=0.0,
        crypto_taker_fee_bps=0.0,
        maximum_allocation=maximum_allocation,
        risk_limits=_limits(slots=slots, gross=gross, sleeve=sleeve, per_name=per_name),
        allow_static_universe=True,
        max_participation_pct=max_participation_pct,
        liquidity_lookback_bars=1,
        turnover_band=0.0,
        minimum_order_notional=0.0,
    )


def _bars(market: Market, symbol: str, rows: list[tuple[str, float, float]]) -> list[Bar]:
    return [
        Bar(_ts(timestamp), symbol, "1d", price, price, price, price, volume, market)
        for timestamp, price, volume in rows
    ]


def _add(
    replay: SharedAccountReplay,
    engine: _SharedEngine,
    execution: list[Bar],
    *,
    actions: list[CorporateAction] | None = None,
) -> None:
    replay.add_market(MarketReplayInput(engine, execution, corporate_actions=actions or []))


def test_weekend_crypto_mark_keeps_last_equity_close() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=6, allow_test_strategies=True)
    equity = _engine(replay, Market.EQUITY, {"AAPL": 0.2}, slots=6)
    crypto = _engine(replay, Market.CRYPTO, {"BTC/USD": 0.2}, slots=6)
    _add(
        replay,
        equity,
        _bars(
            Market.EQUITY,
            "AAPL",
            [
                ("2025-01-03T21:00:00+00:00", 100, 1_000),
                ("2025-01-06T21:00:00+00:00", 100, 1_000),
            ],
        ),
    )
    _add(
        replay,
        crypto,
        _bars(
            Market.CRYPTO,
            "BTC/USD",
            [
                ("2025-01-03T21:00:00+00:00", 100, 1_000),
                ("2025-01-06T21:00:00+00:00", 100, 1_000),
                ("2025-01-11T12:00:00+00:00", 120, 1_000),
            ],
        ),
    )
    result = replay.run(liquidate_at_end=False)
    weekend = next(
        event
        for event in result.events
        if event["event"] == "account_mark" and event["timestamp"].startswith("2025-01-11")
    )
    assert weekend["nav"] == pytest.approx(1_040)
    assert replay.account.marks[(Market.EQUITY, "AAPL")] == 100
    assert replay.account.marks[(Market.CRYPTO, "BTC/USD")] == 120


def test_partial_fill_retains_order_reservation_until_target_cancels() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=6, allow_test_strategies=True)
    engine = _engine(
        replay,
        Market.CRYPTO,
        {"BTC/USD": 0.8},
        schedule={2: {}},
        slots=6,
        maximum_allocation=0.8,
        max_participation_pct=0.01,
        frequency="hours:720",
    )
    _add(
        replay,
        engine,
        _bars(
            Market.CRYPTO,
            "BTC/USD",
            [
                ("2025-01-01T00:00:00+00:00", 100, 10),
                ("2025-01-02T00:00:00+00:00", 100, 10),
                ("2025-02-01T00:00:00+00:00", 100, 10),
            ],
        ),
    )
    result = replay.run(liquidate_at_end=False)
    buy = [fill for fill in result.market_results[Market.CRYPTO].fills if fill.side.value == "buy"]
    assert len(buy) == 2
    assert buy[0].qty == pytest.approx(0.1)
    released = [
        event
        for event in result.events
        if event["event"] == "reservation_released" and event["reason"] == "decision_superseded"
    ]
    assert len(released) == 1
    assert replay.account.pending[Market.CRYPTO]
    assert replay.account.pending[Market.CRYPTO][0].side.value == "sell"


def test_dividend_cash_is_shared_while_crypto_position_is_open() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=6, allow_test_strategies=True)
    equity = _engine(replay, Market.EQUITY, {"AAPL": 0.2}, slots=6)
    crypto = _engine(replay, Market.CRYPTO, {"BTC/USD": 0.2}, slots=6)
    _add(
        replay,
        equity,
        _bars(
            Market.EQUITY,
            "AAPL",
            [
                ("2025-01-02T21:00:00+00:00", 100, 1_000),
                ("2025-01-03T21:00:00+00:00", 100, 1_000),
                ("2025-02-03T21:00:00+00:00", 100, 1_000),
            ],
        ),
        actions=[CorporateAction(_ts("2025-02-03T21:00:00+00:00"), "AAPL", CorporateActionType.DIVIDEND, 1.0)],
    )
    _add(
        replay,
        crypto,
        _bars(
            Market.CRYPTO,
            "BTC/USD",
            [
                ("2025-01-02T21:00:00+00:00", 100, 1_000),
                ("2025-01-03T21:00:00+00:00", 100, 1_000),
                ("2025-02-03T21:00:00+00:00", 100, 1_000),
            ],
        ),
    )
    result = replay.run(liquidate_at_end=False)
    assert replay.account.cash == pytest.approx(602)
    assert (Market.CRYPTO, "BTC/USD") in replay.account.positions
    assert result.invariants["corporate_action_dividend_cash"] == pytest.approx(2)
    assert result.invariants["cash_conserved"] is True


def test_buy_fills_wait_until_cross_market_sell_is_terminal() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=6, allow_test_strategies=True)
    crypto = _engine(
        replay,
        Market.CRYPTO,
        {"BTC/USD": 0.2},
        schedule={2: {}},
        slots=6,
        frequency="hours:720",
        max_participation_pct=0.1,
    )
    equity = _engine(
        replay,
        Market.EQUITY,
        {"AAPL": 0.2},
        schedule={2: {"AAPL": 0.4}},
        slots=6,
    )
    timestamps = [
        "2025-01-02T00:00:00+00:00",
        "2025-01-03T00:00:00+00:00",
        "2025-02-03T00:00:00+00:00",
        "2025-02-04T00:00:00+00:00",
        "2025-02-05T00:00:00+00:00",
        "2025-02-06T00:00:00+00:00",
    ]
    _add(replay, crypto, _bars(Market.CRYPTO, "BTC/USD", [(ts, 100, 10) for ts in timestamps]))
    _add(replay, equity, _bars(Market.EQUITY, "AAPL", [(ts, 100, 1_000) for ts in timestamps]))
    result = replay.run(liquidate_at_end=False)

    deferred = [event for event in result.events if event["event"] == "buy_fill_deferred"]
    assert deferred
    assert deferred[0]["timestamp"].startswith("2025-02-04")
    equity_fills = [fill for fill in result.market_results[Market.EQUITY].fills if fill.side.value == "buy"]
    assert len(equity_fills) == 2
    assert equity_fills[-1].timestamp == _ts("2025-02-06T00:00:00+00:00")


def test_risk_overlay_enforces_gross_sleeve_and_per_name_caps() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=6, allow_test_strategies=True)
    engine = _engine(
        replay,
        Market.EQUITY,
        {"AAPL": 0.5, "MSFT": 0.5},
        slots=6,
        gross=0.6,
        sleeve=0.5,
        per_name=0.2,
        maximum_allocation=0.5,
    )
    rows = _bars(
        Market.EQUITY, "AAPL", [("2025-01-02T21:00:00+00:00", 100, 1_000), ("2025-01-03T21:00:00+00:00", 100, 1_000)]
    )
    rows += _bars(
        Market.EQUITY, "MSFT", [("2025-01-02T21:00:00+00:00", 100, 1_000), ("2025-01-03T21:00:00+00:00", 100, 1_000)]
    )
    _add(replay, engine, rows)
    result = replay.run(liquidate_at_end=False)
    targets = [event["target_weights"] for event in result.events if event["event"] == "decision"]
    assert targets
    assert sum(weight for _symbol, weight in targets[0]) <= 0.5 + 1e-12
    assert all(weight <= 0.2 + 1e-12 for _symbol, weight in targets[0])


def test_same_symbol_text_has_asset_aware_ledger_identity_and_replay_is_deterministic() -> None:
    ledger = SharedAccount(1_000, 1_000)
    ledger.positions[(Market.EQUITY, "BTCUSD")] = _Position(
        "BTCUSD", Market.EQUITY, "1d", 1, 10, _ts("2025-01-01T00:00:00+00:00")
    )
    ledger.positions[(Market.CRYPTO, "BTC/USD")] = _Position(
        "BTC/USD", Market.CRYPTO, "1d", 1, 20, _ts("2025-01-01T00:00:00+00:00")
    )
    assert len(ledger.positions) == 2
    assert {(position.market, position.symbol) for position in ledger.snapshots()} == {
        (Market.EQUITY, "BTCUSD"),
        (Market.CRYPTO, "BTC/USD"),
    }

    deterministic_a = SharedAccountReplay(starting_nav=1_000, allow_test_strategies=True)
    deterministic_b = SharedAccountReplay(starting_nav=1_000, allow_test_strategies=True)
    for replay in (deterministic_a, deterministic_b):
        engine = _engine(replay, Market.EQUITY, {"AAPL": 0.2})
        _add(
            replay,
            engine,
            _bars(
                Market.EQUITY,
                "AAPL",
                [
                    ("2025-01-02T21:00:00+00:00", 100, 1_000),
                    ("2025-01-03T21:00:00+00:00", 100, 1_000),
                ],
            ),
        )
    result_a = deterministic_a.run(liquidate_at_end=False)
    result_b = deterministic_b.run(liquidate_at_end=False)
    assert result_a.event_sha256 == result_b.event_sha256


def _slot_account(symbols: tuple[tuple[Market, str], ...]) -> AccountSnapshot:
    positions = tuple(
        PositionSnapshot(symbol, 1, 100, 100, market, ownership="managed", managed_qty=1) for market, symbol in symbols
    )
    return AccountSnapshot(cash=100_000, equity=100_000, peak_equity=100_000, daily_pnl=0, positions=positions)


def _slot_order(
    market: Market, symbol: str, side: PortfolioOrderSide = PortfolioOrderSide.BUY
) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id=f"test:{market.value}:{symbol}:{side.value}",
        decision_id="synthetic",
        symbol=symbol,
        market=market,
        side=side,
        quantity=1,
        reference_price=100,
        decision_timestamp=_ts("2025-01-01T00:00:00+00:00"),
        strategy_name="synthetic_fixed_test",
        strategy_version="1.0.0",
        risk_reducing=side == PortfolioOrderSide.SELL,
    )


def test_cap_six_actual_positions_held_add_and_pending_sell() -> None:
    held = tuple((Market.EQUITY, symbol) for symbol in ("AAPL", "MSFT", "AMZN", "GOOG", "META", "NVDA"))
    account = _slot_account(held)
    new = _slot_order(Market.CRYPTO, "BTC/USD")
    add = _slot_order(Market.EQUITY, "AAPL")
    sell = _slot_order(Market.EQUITY, "MSFT", PortfolioOrderSide.SELL)
    assert reserve_opening_order_slots((new, add, sell), account, maximum_positions=6) == (add, sell)
    assert reserve_opening_order_slots((sell, new), account, maximum_positions=6) == (sell,)


def test_cap_six_five_occupied_one_reserved_and_two_competing_openings() -> None:
    held = tuple((Market.EQUITY, symbol) for symbol in ("AAPL", "MSFT", "AMZN", "GOOG", "META"))
    account = _slot_account(held)
    btc = _slot_order(Market.CRYPTO, "BTC/USD")
    eth = _slot_order(Market.CRYPTO, "ETH/USD")
    assert reserve_opening_order_slots((btc, eth), account, maximum_positions=6) == (btc,)
    assert (
        reserve_opening_order_slots(
            (eth,), account, maximum_positions=6, reserved_position_keys=((Market.CRYPTO, "BTC/USD"),)
        )
        == ()
    )
    # Cancellation releases the reservation when it is absent from the next snapshot.
    assert reserve_opening_order_slots((eth,), account, maximum_positions=6) == (eth,)


def test_cap_six_band_suppressed_exit_retains_slot() -> None:
    held = (
        (Market.CRYPTO, "LTC/USD"),
        (Market.EQUITY, "AAPL"),
        (Market.EQUITY, "MSFT"),
        (Market.EQUITY, "AMZN"),
        (Market.EQUITY, "GOOG"),
        (Market.EQUITY, "META"),
    )
    account = _slot_account(held)
    # The band suppressed LTC sell, so no zero-position snapshot exists.
    btc = _slot_order(Market.CRYPTO, "BTC/USD")
    bch = _slot_order(Market.CRYPTO, "BCH/USD")
    assert reserve_opening_order_slots((btc, bch), account, maximum_positions=6) == ()
    after_full_exit = _slot_account(held[1:])
    assert reserve_opening_order_slots((btc, bch), after_full_exit, maximum_positions=6) == (btc,)


def test_cap_six_partial_exit_retains_slot_and_asset_identity() -> None:
    account = _slot_account(
        (
            (Market.EQUITY, "BTCUSD"),
            (Market.EQUITY, "AAPL"),
            (Market.EQUITY, "MSFT"),
            (Market.EQUITY, "AMZN"),
            (Market.EQUITY, "GOOG"),
        )
    )
    crypto_open = _slot_order(Market.CRYPTO, "BTC/USD")
    crypto_second = _slot_order(Market.CRYPTO, "ETH/USD")
    assert reserve_opening_order_slots((crypto_open, crypto_second), account, maximum_positions=6) == (crypto_open,)
    assert (
        reserve_opening_order_slots(
            (crypto_second,), account, maximum_positions=6, reserved_position_keys=((Market.CRYPTO, "BTC/USD"),)
        )
        == ()
    )


def test_same_timestamp_cross_market_priority_is_stable_at_cap_six() -> None:
    replay = SharedAccountReplay(starting_nav=10_000, maximum_positions=6, allow_test_strategies=True)
    equity_names = ("AAPL", "MSFT", "AMZN", "GOOG", "META")
    crypto_names = ("BTC/USD", "ETH/USD")
    equity = _engine(replay, Market.EQUITY, {name: 0.1 for name in equity_names}, slots=6)
    crypto = _engine(replay, Market.CRYPTO, {name: 0.05 for name in crypto_names}, slots=6)
    timestamps = ("2025-01-02T00:00:00+00:00", "2025-01-03T00:00:00+00:00")
    _add(
        replay,
        equity,
        sum((_bars(Market.EQUITY, name, [(ts, 100, 1_000) for ts in timestamps]) for name in equity_names), []),
    )
    _add(
        replay,
        crypto,
        sum((_bars(Market.CRYPTO, name, [(ts, 100, 1_000) for ts in timestamps]) for name in crypto_names), []),
    )
    result = replay.run(liquidate_at_end=False)
    assert result.invariants["slot_claim_violation_count"] == 0
    assert result.invariants["max_observed_slot_claim_count"] == 6
    decisions = [event for event in result.events if event["event"] == "decision"]
    assert [event["market"] for event in decisions[:2]] == ["crypto", "equity"]
    assert len([key for key in replay.account.positions if key[0] == Market.CRYPTO]) == 2
    assert len([key for key in replay.account.positions if key[0] == Market.EQUITY]) == 4


def test_empty_all_cash_at_cap_six() -> None:
    replay = SharedAccountReplay(starting_nav=100_000, maximum_positions=6, allow_test_strategies=True)
    assert replay.account.cash == 100_000
    assert replay.account.nav() == 100_000
    assert replay.account.positions == {}
    assert not any(replay.account.pending.values())


def test_pending_opening_cancellation_releases_once() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=6, allow_test_strategies=True)
    timestamp = _ts("2025-01-01T00:00:00+00:00")
    order = PortfolioOrder("opening", "BTC/USD", Market.CRYPTO, OrderSide.BUY, 1, timestamp, timestamp)
    replay.account.pending[Market.CRYPTO].append(order)
    replay._sync_reservations(timestamp, set(), "submission", [])
    before = replay._pending_keys()
    replay.account.pending[Market.CRYPTO].clear()
    replay._sync_reservations(timestamp, before, "cancelled", [])
    replay._sync_reservations(timestamp, before, "cancelled", [])
    assert [event["event"] for event in replay._events] == ["reservation_created", "reservation_released"]
    assert replay.account.reservation_states[(Market.CRYPTO, "opening")] == "released"


def test_same_raw_symbol_is_distinct_across_asset_classes() -> None:
    account = SharedAccount(1_000, 1_000)
    timestamp = _ts("2025-01-01T00:00:00+00:00")
    account.positions[(Market.EQUITY, "ABC")] = _Position("ABC", Market.EQUITY, "1d", 1, 10, timestamp)
    account.positions[(Market.CRYPTO, "ABC")] = _Position("ABC", Market.CRYPTO, "1d", 1, 20, timestamp)
    assert len(account.positions) == 2
    assert account.nav() == pytest.approx(1_030)
    assert {(position.market, position.symbol) for position in account.snapshots()} == {
        (Market.EQUITY, "ABC"),
        (Market.CRYPTO, "ABC"),
    }


def test_synthetic_order_adapter_uses_current_six_slot_policy() -> None:
    timestamp = _ts("2025-01-01T00:00:00+00:00")
    account = _slot_account(tuple((Market.EQUITY, symbol) for symbol in ("AAPL", "MSFT", "AMZN", "GOOG", "META")))
    first = PortfolioOrder("first", "BTC/USD", Market.CRYPTO, OrderSide.BUY, 1, timestamp, timestamp)
    second = PortfolioOrder("second", "ETH/USD", Market.CRYPTO, OrderSide.BUY, 1, timestamp, timestamp)
    assert _admit_synthetic_orders((first, second), account, 6, ()) == (first,)
    assert _admit_synthetic_orders((second,), account, 6, ((Market.CRYPTO, "BTC/USD"),)) == ()
