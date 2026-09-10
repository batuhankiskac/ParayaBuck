"""Focused H14 adapter and native accounting checks.

These tests cover the frozen SPY-only data contract and a tiny native engine
fixture. They do not run the canonical H14 economic cells.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType, DatasetLineage, MarketDataset
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight
from scripts.research_h14_turn_of_month import (
    CELLS,
    END,
    H14DataError,
    H14SchedulePolicy,
    H14TurnOfMonthStrategy,
    _sessions,
    event_tuples,
    load_spy_dividend_actions,
    preflight_cell,
    validate_manifest,
)

MANIFEST = "data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r2.json"
CA_PATH = "data/historical/equity_survivorship_full_corporate_actions_v5.csv"


class _FixedSpyFivePercent(PortfolioStrategy):
    name = "h14_fixture"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(TargetWeight("SPY", 0.05),),
            cash_weight=0.95,
            universe_version="h14-fixture",
        )


def _bar(timestamp: datetime, price: float = 100.0) -> Bar:
    return Bar(timestamp, "SPY", "1d", price, price, price, price, 1_000_000.0, Market.EQUITY)


def _dataset(bars: tuple[Bar, ...], actions: tuple[CorporateAction, ...] = ()) -> MarketDataset:
    lineage = DatasetLineage(
        dataset_id="h14-native-fixture",
        parent_dataset_ids=(),
        provider="fixture",
        source_feed="sip",
        adjustment="split",
        downloaded_at=datetime(2026, 1, 1, tzinfo=UTC),
        data_hash="h14-fixture-hash",
    )
    return MarketDataset(Market.EQUITY, "1d", bars, bars, lineage, corporate_actions=actions)


def _engine() -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        _FixedSpyFivePercent(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=100_000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        max_participation_pct=1.0,
        liquidity_lookback_bars=1,
        turnover_band=0.0,
        minimum_order_notional=0.0,
        state_path=None,
    )


def test_h14_preflight_cells_and_first_calendar_boundary() -> None:
    manifest = validate_manifest(MANIFEST)
    full = preflight_cell(manifest, "full_2022")
    recent = preflight_cell(manifest, "recent_2024")

    assert full["status"] == "PASS"
    assert full["spy_sessions"] == 1147
    assert len(full["events"]) == 54
    assert recent["spy_sessions"] == 646
    assert len(recent["events"]) == 30
    assert full["events"][0] == {
        "month": "2022-01",
        "p0": date(2022, 1, 28),
        "l0": date(2022, 1, 31),
        "f1": date(2022, 2, 1),
        "f2": date(2022, 2, 2),
        "f3": date(2022, 2, 3),
        "f4": date(2022, 2, 4),
    }


def test_h14_fresh_start_and_terminal_partial_month_fail_closed() -> None:
    fresh = event_tuples(date(2022, 1, 29), date(2022, 3, 10))
    assert fresh[0].month == "2022-02"
    assert all(event.p0 >= date(2022, 1, 29) for event in fresh)

    terminal_partial = event_tuples(date(2025, 12, 1), date(2026, 1, 6))
    assert all(event.month != "2025-12" for event in terminal_partial)
    assert all(event.f4 <= date(2026, 1, 6) for event in terminal_partial)


def test_h14_holiday_tuple_uses_sessions_not_calendar_days() -> None:
    events = event_tuples(*CELLS["full_2022"])
    march = next(event for event in events if event.month == "2022-03")
    assert (march.f1, march.f2, march.f3, march.f4) == (
        date(2022, 4, 1),
        date(2022, 4, 4),
        date(2022, 4, 5),
        date(2022, 4, 6),
    )


def test_h14_fixed_control_has_four_complete_open_to_open_intervals() -> None:
    sessions = _sessions(date(2022, 1, 1), END)
    events = event_tuples(*CELLS["full_2022"])
    session_index = {session: index for index, session in enumerate(sessions)}

    for event in events:
        candidate_indices = [session_index[item] for item in (event.l0, event.f1, event.f2, event.f3, event.f4)]
        assert candidate_indices == list(range(candidate_indices[0], candidate_indices[0] + 5))

        control_start = session_index[event.f4]
        control_indices = list(range(control_start, control_start + 5))
        assert control_indices[-1] < len(sessions)
        assert sessions[control_indices[-1]] <= END
        assert len(control_indices) - 1 == 4


def test_h14_real_spy_actions_are_native_cash_dividends_only() -> None:
    actions = load_spy_dividend_actions(CA_PATH, end=END)
    assert len(actions) == 40
    assert all(action.symbol == "SPY" for action in actions)
    assert all(action.action_type == CorporateActionType.DIVIDEND for action in actions)
    assert all(action.timestamp.tzinfo is UTC for action in actions)


def test_h14_native_engine_credits_dividend_to_pre_ex_date_holder() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = tuple(_bar(start + timedelta(days=index)) for index in range(3))
    action = CorporateAction(start + timedelta(days=2), "SPY", CorporateActionType.DIVIDEND, 1.0)

    result = _engine().run_dataset(_dataset(bars, (action,)), liquidate_at_end=False)

    assert result.fills[0].timestamp == start + timedelta(days=1)
    assert result.dividend_total == pytest.approx(result.fills[0].qty)


def test_h14_native_engine_does_not_credit_ex_date_open_purchase() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = tuple(_bar(start + timedelta(days=index)) for index in range(2))
    action = CorporateAction(start + timedelta(days=1), "SPY", CorporateActionType.DIVIDEND, 1.0)

    result = _engine().run_dataset(_dataset(bars, (action,)), liquidate_at_end=False)

    assert result.fills[0].timestamp == start + timedelta(days=1)
    assert result.dividend_total == 0.0


def test_h14_strategy_marks_exact_five_percent_target() -> None:
    decision = date(2025, 1, 2)
    strategy = H14TurnOfMonthStrategy({decision}, set(), mode="passive")
    context = PortfolioContext(
        decision_timestamp=datetime(2025, 1, 2, tzinfo=UTC),
        market=Market.EQUITY,
        timeframe="1d",
        history={},
        universe=None,
        universe_version="h14_static_spy_v2",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )

    target = strategy.decide(context)

    assert target.weight_for("SPY", Market.EQUITY) == pytest.approx(0.05)
    assert target.cash_weight == pytest.approx(0.95)
    assert target.weight_for("SPY", Market.EQUITY) != pytest.approx(0.05 * 0.05)


def test_h14_passive_entry_uses_first_in_window_open_and_keeps_first_day_return() -> None:
    decision = datetime(2025, 1, 2, tzinfo=UTC)
    first_open = datetime(2025, 1, 3, tzinfo=UTC)
    bars = (_bar(decision, 100.0), _bar(first_open, 120.0))
    strategy = H14TurnOfMonthStrategy({decision.date()}, set(), mode="passive")
    engine = PortfolioBacktestEngine(
        strategy,
        H14SchedulePolicy({decision.date()}),
        starting_equity=100_000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        max_participation_pct=1.0,
        liquidity_lookback_bars=1,
        turnover_band=0.0,
        minimum_order_notional=0.0,
        state_path=None,
        allow_static_universe=True,
    )

    result = engine.run_dataset(_dataset(bars), liquidate_at_end=False)

    assert len(result.fills) == 1
    assert result.fills[0].timestamp == first_open
    assert result.fills[0].decision_timestamp == decision
    assert result.fills[0].reference_price == pytest.approx(120.0)
    assert result.equity_curve[-1] == pytest.approx(100_000.0)


def test_h14_passive_first_open_gain_is_marked_after_fill() -> None:
    decision = datetime(2025, 1, 2, tzinfo=UTC)
    first_open = datetime(2025, 1, 3, tzinfo=UTC)
    first_close = _bar(first_open, 120.0)
    first_close = Bar(
        first_close.timestamp,
        first_close.symbol,
        first_close.timeframe,
        120.0,
        125.0,
        120.0,
        125.0,
        first_close.volume,
        first_close.market,
    )
    bars = (_bar(decision, 100.0), first_close)
    strategy = H14TurnOfMonthStrategy({decision.date()}, set(), mode="passive")
    engine = PortfolioBacktestEngine(
        strategy,
        H14SchedulePolicy({decision.date()}),
        starting_equity=100_000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        max_participation_pct=1.0,
        liquidity_lookback_bars=1,
        turnover_band=0.0,
        minimum_order_notional=0.0,
        state_path=None,
        allow_static_universe=True,
    )

    result = engine.run_dataset(_dataset(bars), liquidate_at_end=False)

    assert result.fills[0].timestamp == first_open
    # The frozen 5% target is marked from the decision close (100), so the
    # next-open gap produces 50 shares and a 6% post-fill mark at 120. The
    # first-day move from 120 to 125 must still be captured.
    assert result.rebalances[0].target_weights == (("SPY", 0.05),)
    assert result.equity_curve[-1] == pytest.approx(100_000.0 + 50.0 * (125.0 - 120.0))


def test_h14_adapter_rejects_spy_split_instead_of_double_applying_it(tmp_path) -> None:
    source = tmp_path / "corporate_actions.csv"
    source.write_text(
        "action_type,symbol,ex_date,record_date,payable_date,rate,old_rate,new_rate,source_symbol,target_symbol,raw_id\n"
        "forward_splits,SPY,2025-01-02,,,2,,,SPY,SPY,split-1\n",
        encoding="utf-8",
    )

    with pytest.raises(H14DataError, match="unsupported SPY corporate action"):
        load_spy_dividend_actions(source)
