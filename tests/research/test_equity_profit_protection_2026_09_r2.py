from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine
from parayabuck.backtest.terminal_aware_engine import TerminalInfo
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason, TargetPortfolio, TargetWeight
from scripts.research.equity_profit_protection_2026_09_r2 import (
    EpisodeState,
    ProfitProtectionStrategy,
    ResearchEngine,
    build_strategy,
    parity_result_components,
)


class FixtureProtectionStrategy(ProfitProtectionStrategy):
    """Small deterministic V3-decision seam for real engine integration tests."""

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def __init__(self, candidate: str, schedule: dict[datetime, float | None], default: float | None = 0.5):
        super().__init__(candidate)
        self.schedule = schedule
        self.default_target = default

    def _score_candidates(self, context):  # type: ignore[no-untyped-def]
        # E3 needs a deliberately bad alpha rank for the confirmation test.
        if self.candidate == "E3_PROFIT_ALPHA_CONFIRMED":
            return (), {}
        return ((1.0, "AAA"),), {"AAA": 1.0}

    def scheduled_v3_decide(self, context):  # type: ignore[no-untyped-def]
        target = self.schedule.get(context.decision_timestamp, self.default_target)
        weights = () if target is None or target <= 0 else (TargetWeight("AAA", target, "fixture"),)
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(weight.weight for weight in weights),
            rebalance_reason=RebalanceReason.SCHEDULED,
            parameters=self.parameter_tuple(),
        )


def _bars(
    closes: list[float],
    *,
    opens: list[float] | None = None,
    volumes: list[float] | None = None,
    timestamps: list[datetime] | None = None,
) -> list[Bar]:
    opens = opens or closes
    volumes = volumes or [1_000.0] * len(closes)
    timestamps = timestamps or [datetime(2026, 1, 5, tzinfo=UTC) + timedelta(days=i) for i in range(len(closes))]
    return [
        Bar(
            timestamp=timestamp,
            symbol="AAA",
            timeframe="1d",
            open=open_price,
            high=max(open_price, close),
            low=min(open_price, close),
            close=close,
            volume=volume,
            market=Market.EQUITY,
        )
        for timestamp, open_price, close, volume in zip(timestamps, opens, closes, volumes, strict=True)
    ]


def _engine(
    strategy,
    *,
    state=None,
    state_path=None,
    episode_state_path=None,
    max_participation_pct=1.0,
    liquidity_lookback_bars=20,
    terminal_map=None,
):  # type: ignore[no-untyped-def]
    return ResearchEngine(
        strategy,
        RebalancePolicy(Market.EQUITY, "weekly"),
        starting_equity=1_000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        turnover_band=0.0,
        minimum_order_notional=0.0,
        max_participation_pct=max_participation_pct,
        liquidity_lookback_bars=liquidity_lookback_bars,
        maximum_allocation=1.0,
        allow_static_universe=True,
        state=state,
        state_path=state_path,
        episode_state_path=episode_state_path,
        terminal_map=terminal_map or {},
        policy="T1",
    )


def test_e0_control_replay_is_hash_stable_on_current_engine_path():
    timestamps = [datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 6, tzinfo=UTC)]
    bars = _bars([100.0, 101.0], timestamps=timestamps)
    first_strategy = build_strategy("E0_CONTROL")
    second_strategy = build_strategy("E0_CONTROL")
    # E0 identity is the exact current roadmap control, not a protection subclass.
    assert type(first_strategy).__name__ == "RoadmapControl"
    first = _engine(first_strategy)
    second = _engine(second_strategy)
    first_result = first.run(bars, liquidate_at_end=False)
    second_result = second.run(bars, liquidate_at_end=False)
    assert parity_result_components(first_result, first, _window_stub()) == parity_result_components(
        second_result, second, _window_stub()
    )


def _window_stub():
    class Window:
        class Universe:
            def to_dict(self):
                return {}

        universe = Universe()

    return Window()


def test_scheduled_and_non_scheduled_five_percent_crossing_fills_next_open_gap():
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    strategy = FixtureProtectionStrategy("E1_TP5_CLOSE", {t0: 0.5})
    engine = _engine(strategy)
    result = engine.run(_bars([100.0, 105.0, 90.0], opens=[100.0, 100.0, 90.0]), liquidate_at_end=False)
    sells = [fill for fill in result.fills if fill.side == OrderSide.SELL]
    assert sells and sells[0].timestamp == t0 + timedelta(days=2)
    assert sells[0].fill_price == 90.0
    assert strategy.observation_events[0]["timestamp"] == (t0 + timedelta(days=1)).isoformat()


def test_profit_lock_activation_close_cannot_exit():
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    strategy = FixtureProtectionStrategy("E2_PROFIT_LOCK_50", {t0: 0.5})
    engine = _engine(strategy)
    result = engine.run(
        _bars([100.0, 105.0, 110.0, 104.0, 80.0], opens=[100.0, 100.0, 110.0, 104.0, 80.0]),
        liquidate_at_end=False,
    )
    sells = [fill for fill in result.fills if fill.side == OrderSide.SELL]
    assert sells and sells[0].timestamp == t0 + timedelta(days=4)
    assert [event["timestamp"] for event in strategy.observation_events] == [
        (t0 + timedelta(days=i)).isoformat() for i in (1, 2, 3)
    ]


def test_e3_requires_two_distinct_increasing_daily_timestamps():
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    strategy = FixtureProtectionStrategy("E3_PROFIT_ALPHA_CONFIRMED", {t0: 0.5})
    engine = _engine(strategy)
    result = engine.run(
        _bars([100.0, 105.0, 104.0, 103.0, 80.0], opens=[100.0, 100.0, 104.0, 103.0, 80.0]),
        liquidate_at_end=False,
    )
    sells = [fill for fill in result.fills if fill.side == OrderSide.SELL]
    assert sells and sells[0].timestamp == t0 + timedelta(days=4)
    assert strategy.states == {}
    assert strategy.duplicate_observation_attempts == 0


def test_duplicate_replay_does_not_consume_a_close_twice():
    state = EpisodeState(4.0, 100.0, episode_id="AAA:episode-1")
    timestamp = datetime(2026, 1, 6, tzinfo=UTC)
    assert not state.observe(105.0, "E2_PROFIT_LOCK_50", timestamp=timestamp)
    assert not state.observe(105.0, "E2_PROFIT_LOCK_50", timestamp=timestamp)
    assert state.last_observed_timestamp == timestamp.isoformat()
    assert state.armed is True
    assert state.breakdown_count == 0


def test_partial_exit_preserves_pending_quantity_and_protection_state():
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    strategy = FixtureProtectionStrategy("E1_TP5_CLOSE", {t0: 0.5})
    engine = _engine(strategy, max_participation_pct=0.1, liquidity_lookback_bars=1)
    engine.run(
        _bars([100.0, 105.0, 90.0], opens=[100.0, 100.0, 90.0], volumes=[1_000.0, 10.0, 10.0]),
        liquidate_at_end=False,
    )
    state = strategy.states["AAA"]
    assert state.pending is True
    assert state.quantity == 4.0
    assert engine._pending and engine._pending[0].side == OrderSide.SELL
    assert engine._pending[0].qty == 4.0


def test_segmented_restart_matches_uninterrupted_state_and_has_no_duplicate_fills(tmp_path):
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    bars = _bars([100.0, 105.0, 90.0, 90.0, 90.0], volumes=[1_000.0, 10.0, 10.0, 10.0, 10.0])
    uninterrupted_strategy = FixtureProtectionStrategy("E1_TP5_CLOSE", {t0: 0.5})
    uninterrupted = _engine(uninterrupted_strategy, max_participation_pct=0.1, liquidity_lookback_bars=1)
    uninterrupted_result = uninterrupted.run(bars, liquidate_at_end=False)

    state_path = tmp_path / "portfolio-state.json"
    episode_path = tmp_path / "episode-state.json"
    first_strategy = FixtureProtectionStrategy("E1_TP5_CLOSE", {t0: 0.5})
    first = _engine(
        first_strategy,
        max_participation_pct=0.1,
        liquidity_lookback_bars=1,
        state_path=state_path,
        episode_state_path=episode_path,
    )
    first_result = first.run(bars[:3], liquidate_at_end=False)
    restored_state = PortfolioBacktestEngine.load_state(state_path)
    assert restored_state is not None
    second_strategy = FixtureProtectionStrategy("E1_TP5_CLOSE", {t0: 0.5})
    second = _engine(
        second_strategy,
        state=restored_state,
        max_participation_pct=0.1,
        liquidity_lookback_bars=1,
        state_path=state_path,
        episode_state_path=episode_path,
    )
    second_result = second.run(bars, warmup=bars, liquidate_at_end=False)
    assert second_result.ending_equity == uninterrupted_result.ending_equity
    assert second_result.state.holdings == uninterrupted_result.state.holdings
    assert all(fill.timestamp > bars[2].timestamp for fill in second_result.fills)
    assert [fill.timestamp for fill in first_result.fills + second_result.fills] == [
        fill.timestamp for fill in uninterrupted_result.fills
    ]


def test_scheduled_signal_precedes_pending_sell_and_replaces_it_at_week_boundary():
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    t5 = datetime(2026, 1, 12, tzinfo=UTC)
    timestamps = [t0 + timedelta(days=i) for i in range(5)] + [t5]
    strategy = FixtureProtectionStrategy("E1_TP5_CLOSE", {t0: 0.5, t5: None})
    engine = _engine(strategy)
    engine.run(
        _bars(
            [100.0, 105.0, 90.0, 90.0, 90.0, 90.0], volumes=[1_000.0, 0.0, 0.0, 0.0, 0.0, 0.0], timestamps=timestamps
        ),
        liquidate_at_end=False,
    )
    event = next(item for item in strategy.decision_events if item["timestamp"] == t5.isoformat())
    assert event["attribution"]["AAA"] == "scheduled_v3_signal"
    assert engine._pending and engine._pending[0].decision_timestamp == t5


def test_no_intra_week_buy_or_replacement_after_protection_exit():
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    strategy = FixtureProtectionStrategy("E1_TP5_CLOSE", {t0: 0.5})
    engine = _engine(strategy)
    engine.run(_bars([100.0, 105.0, 90.0, 90.0]), liquidate_at_end=False)
    assert engine.off_schedule_buys == []
    assert engine.intermediate_exposure_violations == []
    assert all(not event["scheduled"] or event["timestamp"] == t0.isoformat() for event in strategy.decision_events)


def test_external_manual_quantity_is_not_candidate_owned():
    strategy = FixtureProtectionStrategy("E1_TP5_CLOSE", {})
    strategy.sync_authoritative_positions({"MANUAL": (4.0, 100.0)})
    assert strategy.states == {}
    assert strategy.managed_symbols == set()


def test_actual_split_adjusts_engine_and_episode_without_disarming():
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    timestamps = [t0 + timedelta(days=i) for i in range(4)]
    strategy = FixtureProtectionStrategy("E2_PROFIT_LOCK_50", {t0: 0.5})
    engine = _engine(strategy)
    engine.run(
        _bars([100.0, 105.0, 55.0, 55.0], opens=[100.0, 100.0, 52.5, 55.0], timestamps=timestamps),
        corporate_actions=[CorporateAction(timestamps[2], "AAA", CorporateActionType.SPLIT, 2.0)],
        liquidate_at_end=False,
    )
    assert engine._positions["AAA"].qty == 10.0
    assert engine._positions["AAA"].avg_price == 50.0
    assert strategy.states["AAA"].armed is True
    assert strategy.states["AAA"].quantity == 10.0
    assert strategy.states["AAA"].basis == 50.0


def test_terminal_t1_removes_position_and_episode_once():
    t0 = datetime(2026, 1, 5, tzinfo=UTC)
    t2 = t0 + timedelta(days=2)
    terminal = {"AAA": TerminalInfo("entity-aaa", "AAA", "AAA", t2, "DELISTING", "OBSERVED", True)}
    strategy = FixtureProtectionStrategy("E1_TP5_CLOSE", {t0: 0.5})
    engine = _engine(strategy, terminal_map=terminal)
    engine.run(_bars([100.0, 100.0, 100.0]), liquidate_at_end=False)
    assert len(engine.terminal_events_fired) == 1
    assert engine.terminal_events_fired[0]["effective_policy"] == "T1"
    assert engine._positions == {}
    assert strategy.states == {}
