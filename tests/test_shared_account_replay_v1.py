from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioOrder, _Position
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason, TargetPortfolio, TargetWeight
from research.shared_account_replay_v1.replay import (
    MarketReplayInput,
    SharedAccount,
    SharedAccountReplay,
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


def test_flat_all_cash_start_and_single_market_replay() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, allow_test_strategies=True)
    assert replay.account.cash == 1_000
    assert replay.account.positions == {}
    assert replay.account.pending == {Market.EQUITY: [], Market.CRYPTO: []}

    engine = _engine(replay, Market.EQUITY, {"AAPL": 0.4})
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
    result = replay.run(liquidate_at_end=False)
    assert result.invariants["cash_conserved"] is True
    assert len(result.positions) == 1
    assert result.positions[0]["market"] == "equity"
    assert result.positions[0]["qty"] == pytest.approx(4)


def test_cross_market_same_timestamp_reservation_and_exit_release() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=1, allow_test_strategies=True)
    crypto = _engine(
        replay,
        Market.CRYPTO,
        {"BTC/USD": 0.4},
        schedule={2: {}, 3: {}},
        slots=1,
    )
    equity = _engine(replay, Market.EQUITY, {"AAPL": 0.4}, slots=1)
    shared_times = [
        "2025-01-01T00:00:00+00:00",
        "2025-01-02T00:00:00+00:00",
        "2025-02-01T00:00:00+00:00",
        "2025-02-02T00:00:00+00:00",
        "2025-03-01T00:00:00+00:00",
        "2025-03-02T00:00:00+00:00",
    ]
    _add(replay, crypto, _bars(Market.CRYPTO, "BTC/USD", [(ts, 100, 1_000) for ts in shared_times]))
    _add(replay, equity, _bars(Market.EQUITY, "AAPL", [(ts, 100, 1_000) for ts in shared_times]))
    result = replay.run(liquidate_at_end=False)

    equity_decisions = [
        event for event in result.events if event["event"] == "decision" and event["market"] == "equity"
    ]
    assert equity_decisions[0]["target_weights"] == []
    assert any(event["event"] == "reservation_released" for event in result.events)
    assert result.invariants["max_observed_position_count"] <= 1
    assert any(
        event["event"] == "reservation_created"
        and event["market"] == "equity"
        and event["timestamp"].startswith("2025-03-01")
        for event in result.events
    )


def test_later_market_decision_counts_prior_timestamp_open_order() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=1, allow_test_strategies=True)
    equity = _engine(replay, Market.EQUITY, {"AAPL": 0.4}, slots=1)
    crypto = _engine(replay, Market.CRYPTO, {"BTC/USD": 0.4}, slots=1)
    _add(replay, equity, _bars(Market.EQUITY, "AAPL", [("2025-01-06T00:00:00+00:00", 100, 1_000)]))
    _add(
        replay,
        crypto,
        _bars(
            Market.CRYPTO,
            "BTC/USD",
            [
                ("2025-01-07T00:00:00+00:00", 100, 1_000),
                ("2025-01-08T00:00:00+00:00", 100, 1_000),
            ],
        ),
    )

    result = replay.run(liquidate_at_end=False)
    later_crypto_decision = next(
        event
        for event in result.events
        if event["event"] == "decision" and event["market"] == "crypto" and event["timestamp"].startswith("2025-01-07")
    )
    assert later_crypto_decision["target_weights"] == []
    assert result.invariants["max_observed_slot_claim_count"] <= 1
    assert result.invariants["slot_claim_violation_count"] == 0


def test_deferred_buy_intent_reserves_cross_market_slot() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=1, allow_test_strategies=True)
    crypto = _engine(replay, Market.CRYPTO, {"BTC/USD": 0.4}, slots=1)
    _engine(replay, Market.EQUITY, {}, slots=1)
    replay.account.deferred_buys[Market.EQUITY].append(
        PortfolioOrder(
            "equity-deferred-aapl",
            "AAPL",
            Market.EQUITY,
            OrderSide.BUY,
            4.0,
            _ts("2025-01-01T00:00:00+00:00"),
            _ts("2025-01-01T00:00:00+00:00"),
        )
    )
    target = TargetPortfolio(
        strategy_name=crypto.strategy.name,
        strategy_version=crypto.strategy.version,
        decision_timestamp=_ts("2025-01-02T00:00:00+00:00"),
        weights=(TargetWeight("BTC/USD", 0.4, rank=1),),
        cash_weight=0.6,
    )

    risk = crypto._shared_overlay.apply(
        target,
        crypto._account_snapshot(),
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD",),
        maximum_allocation=0.8,
        mode="backtest",
    )

    assert risk.target.weights == ()


def test_market_does_not_count_its_superseded_deferred_buy_intent() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=1, allow_test_strategies=True)
    _engine(replay, Market.CRYPTO, {}, slots=1)
    equity = _engine(replay, Market.EQUITY, {"MSFT": 0.4}, slots=1)
    replay.account.deferred_buys[Market.EQUITY].append(
        PortfolioOrder(
            "equity-deferred-aapl",
            "AAPL",
            Market.EQUITY,
            OrderSide.BUY,
            4.0,
            _ts("2025-01-01T00:00:00+00:00"),
            _ts("2025-01-01T00:00:00+00:00"),
        )
    )
    target = TargetPortfolio(
        strategy_name=equity.strategy.name,
        strategy_version=equity.strategy.version,
        decision_timestamp=_ts("2025-01-02T00:00:00+00:00"),
        weights=(TargetWeight("MSFT", 0.4, rank=1),),
        cash_weight=0.6,
    )

    risk = equity._shared_overlay.apply(
        target,
        equity._account_snapshot(),
        market=Market.EQUITY,
        approved_symbols=("MSFT",),
        maximum_allocation=0.8,
        mode="backtest",
    )

    assert tuple(weight.symbol for weight in risk.target.weights) == ("MSFT",)


def test_weekend_crypto_mark_keeps_last_equity_close() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=4, allow_test_strategies=True)
    equity = _engine(replay, Market.EQUITY, {"AAPL": 0.2}, slots=4)
    crypto = _engine(replay, Market.CRYPTO, {"BTC/USD": 0.2}, slots=4)
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
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=2, allow_test_strategies=True)
    engine = _engine(
        replay,
        Market.CRYPTO,
        {"BTC/USD": 0.8},
        schedule={2: {}},
        slots=2,
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
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=4, allow_test_strategies=True)
    equity = _engine(replay, Market.EQUITY, {"AAPL": 0.2}, slots=4)
    crypto = _engine(replay, Market.CRYPTO, {"BTC/USD": 0.2}, slots=4)
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
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=4, allow_test_strategies=True)
    crypto = _engine(
        replay,
        Market.CRYPTO,
        {"BTC/USD": 0.2},
        schedule={2: {}},
        slots=4,
        frequency="hours:720",
        max_participation_pct=0.1,
    )
    equity = _engine(
        replay,
        Market.EQUITY,
        {"AAPL": 0.2},
        schedule={2: {"AAPL": 0.4}},
        slots=4,
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


def test_replacement_buys_do_not_reserve_slots_before_sell_terminal() -> None:
    replay = SharedAccountReplay(starting_nav=1_000, maximum_positions=1, allow_test_strategies=True)
    crypto = _engine(
        replay,
        Market.CRYPTO,
        {"BTC/USD": 0.4},
        schedule={2: {"ETH/USD": 0.4}},
        slots=1,
        maximum_allocation=0.8,
        frequency="hours:24",
        max_participation_pct=1.0,
    )
    equity = _engine(replay, Market.EQUITY, {}, slots=1, frequency="monthly")
    timestamps = [
        "2025-01-01T00:00:00+00:00",
        "2025-01-02T00:00:00+00:00",
        "2025-02-01T00:00:00+00:00",
        "2025-02-02T00:00:00+00:00",
        "2025-02-03T00:00:00+00:00",
    ]
    crypto_bars = _bars(Market.CRYPTO, "BTC/USD", [(ts, 100, 1_000) for ts in timestamps])
    crypto_bars += _bars(Market.CRYPTO, "ETH/USD", [(ts, 100, 1_000) for ts in timestamps])
    _add(replay, crypto, crypto_bars)
    _add(replay, equity, _bars(Market.EQUITY, "AAPL", [(ts, 100, 1_000) for ts in timestamps]))

    result = replay.run(liquidate_at_end=False)

    assert result.invariants["max_observed_position_count"] <= 1
    assert result.invariants["max_observed_slot_claim_count"] <= 1
    assert result.invariants["slot_claim_violation_count"] == 0
    deferred = [event for event in result.events if event["event"] == "buy_intent_deferred"]
    activated = [event for event in result.events if event["event"] == "buy_intent_activated"]
    assert deferred[0]["timestamp"] == "2025-02-01T00:00:00+00:00"
    assert activated[0]["timestamp"] == "2025-02-02T00:00:00+00:00"
    assert list(replay.account.positions) == [(Market.CRYPTO, "ETH/USD")]


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
