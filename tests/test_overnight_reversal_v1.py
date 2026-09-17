"""Frozen contract tests for the research-only overnight reversal candidate."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits, PortfolioRiskOverlay
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.universe.observation_set import build_observation_set
from research.overnight_reversal_v1.candidate import (
    CANDIDATE_NAME,
    CANDIDATE_VERSION,
    EQUITY_ALLOCATION,
    FIXED_PER_NAME_WEIGHT,
    FROZEN_PARAMETERS,
    POSITION_CAP,
    TOP_N,
    DailySessionRebalancePolicy,
    OvernightReversalStrategy,
    score_context,
    signal_from_bars,
)
from research.overnight_reversal_v1.data import EntityLifetime, session_date


def _timestamp(day: date, hour: int = 20) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _bar(
    symbol: str,
    day: date,
    *,
    opening: float,
    closing: float,
    timestamp: datetime | None = None,
) -> Bar:
    high = max(opening, closing) * 1.01
    low = min(opening, closing) * 0.99
    return Bar(
        timestamp=timestamp or _timestamp(day),
        symbol=symbol,
        timeframe="1d",
        open=opening,
        high=high,
        low=low,
        close=closing,
        volume=1_000_000.0,
        market=Market.EQUITY,
    )


def _signal_maps(
    *,
    stock_open: float = 90.0,
    stock_prior_close: float = 100.0,
    spy_open: float = 101.0,
    spy_prior_close: float = 100.0,
    decision_day: date = date(2024, 1, 3),
    prior_day: date = date(2024, 1, 2),
) -> tuple[dict[date, Bar], dict[date, Bar]]:
    stock = {
        prior_day: _bar("AAA", prior_day, opening=stock_prior_close, closing=stock_prior_close),
        decision_day: _bar("AAA", decision_day, opening=stock_open, closing=stock_open),
    }
    spy = {
        prior_day: _bar("SPY", prior_day, opening=spy_prior_close, closing=spy_prior_close),
        decision_day: _bar("SPY", decision_day, opening=spy_open, closing=spy_open),
    }
    return stock, spy


def _context(
    histories: dict[str, tuple[Bar, ...]],
    timestamp: datetime,
    *,
    universe: object | None = None,
) -> PortfolioContext:
    return PortfolioContext(
        decision_timestamp=timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history=histories,
        universe=universe,
        universe_version="test-universe",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )


def _two_day_context(
    stock_specs: tuple[tuple[str, float], ...],
    *,
    decision_day: date = date(2024, 1, 3),
) -> PortfolioContext:
    prior_day = date(2024, 1, 2)
    histories: dict[str, tuple[Bar, ...]] = {
        "SPY": (
            _bar("SPY", prior_day, opening=100.0, closing=100.0),
            _bar("SPY", decision_day, opening=101.0, closing=101.0),
        )
    }
    for symbol, opening in stock_specs:
        histories[symbol] = (
            _bar(symbol, prior_day, opening=100.0, closing=100.0),
            _bar(symbol, decision_day, opening=opening, closing=opening),
        )
    return _context(histories, _timestamp(decision_day))


def test_signal_formula_uses_stock_minus_spy_excess_and_negates_it() -> None:
    stock, spy = _signal_maps(stock_open=90.0, spy_open=101.0)

    signal = signal_from_bars("AAA", date(2024, 1, 3), date(2024, 1, 2), stock, spy)

    assert signal.stock_overnight == pytest.approx(-0.10)
    assert signal.spy_overnight == pytest.approx(0.01)
    assert signal.spy_excess == pytest.approx(-0.11)
    assert signal.score == pytest.approx(0.11)
    assert signal.reason == "valid_common_session_pair"


def test_more_negative_stock_overnight_excess_ranks_higher() -> None:
    context = _two_day_context(
        (
            ("AAA", 90.0),
            ("BBB", 95.0),
        )
    )

    assert OvernightReversalStrategy().rank_candidates(context) == ("AAA", "BBB")


def test_tie_break_is_canonical_symbol_order() -> None:
    context = _two_day_context(
        (
            ("ZZZ", 90.0),
            ("AAA", 90.0),
        )
    )

    assert OvernightReversalStrategy().rank_candidates(context) == ("AAA", "ZZZ")


def test_signal_requires_the_explicit_common_spy_prior_session() -> None:
    friday = date(2024, 1, 12)
    tuesday = date(2024, 1, 16)
    stock = {
        friday: _bar("AAA", friday, opening=100.0, closing=100.0),
        tuesday: _bar("AAA", tuesday, opening=90.0, closing=90.0),
    }
    spy = {
        friday: _bar("SPY", friday, opening=100.0, closing=100.0),
        tuesday: _bar("SPY", tuesday, opening=100.0, closing=100.0),
    }

    signal = signal_from_bars("AAA", tuesday, friday, stock, spy)

    assert signal.prior_date == friday
    assert signal.score == pytest.approx(0.10)


def test_missing_weekend_session_is_not_used_as_a_prior() -> None:
    friday = date(2024, 1, 12)
    saturday = date(2024, 1, 13)
    monday = date(2024, 1, 15)
    stock, spy = _signal_maps(decision_day=monday, prior_day=friday)

    signal = signal_from_bars("AAA", monday, friday, stock, spy)

    assert saturday not in stock
    assert signal.prior_date == friday


def test_missing_stock_current_bar_is_ineligible() -> None:
    stock, spy = _signal_maps()
    del stock[date(2024, 1, 3)]

    signal = signal_from_bars("AAA", date(2024, 1, 3), date(2024, 1, 2), stock, spy)

    assert signal.score is None
    assert signal.reason == "missing_provider_observation"


def test_missing_spy_pair_is_ineligible() -> None:
    stock, spy = _signal_maps()
    del spy[date(2024, 1, 3)]

    signal = signal_from_bars("AAA", date(2024, 1, 3), date(2024, 1, 2), stock, spy)

    assert signal.score is None
    assert signal.reason == "missing_spy_reference"


def test_no_prior_spy_session_is_ineligible() -> None:
    stock, spy = _signal_maps()

    signal = signal_from_bars("AAA", date(2024, 1, 3), None, stock, spy)

    assert signal.score is None
    assert signal.reason == "no_prior_spy_session"


def test_lifecycle_interval_is_inclusive_at_observed_boundaries() -> None:
    lifetime = EntityLifetime(
        symbol="AAA",
        valid_from=_timestamp(date(2024, 1, 2)),
        valid_to=_timestamp(date(2024, 1, 5)),
        first_observed_bar=_timestamp(date(2024, 1, 2)),
        last_observed_bar=_timestamp(date(2024, 1, 5)),
        exchange="NYSE",
        current_status="DELISTED",
        terminal_event_class="POSSIBLE_DELISTING",
    )

    assert lifetime.valid_at(_timestamp(date(2024, 1, 2)))
    assert lifetime.valid_at(_timestamp(date(2024, 1, 5)))
    assert not lifetime.valid_at(_timestamp(date(2024, 1, 6)))


def test_split_adjusted_continuity_does_not_create_a_false_overnight_jump() -> None:
    stock = {
        date(2024, 1, 2): _bar("AAA", date(2024, 1, 2), opening=100.0, closing=100.0),
        date(2024, 1, 3): _bar("AAA", date(2024, 1, 3), opening=100.0, closing=100.0),
    }
    spy = {
        date(2024, 1, 2): _bar("SPY", date(2024, 1, 2), opening=100.0, closing=100.0),
        date(2024, 1, 3): _bar("SPY", date(2024, 1, 3), opening=100.0, closing=100.0),
    }

    signal = signal_from_bars("AAA", date(2024, 1, 3), date(2024, 1, 2), stock, spy)

    assert signal.stock_overnight == pytest.approx(0.0)
    assert signal.score == pytest.approx(0.0)


def test_spy_is_reference_only_and_never_a_candidate_target() -> None:
    context = _two_day_context(tuple((f"S{index}", 90.0 + index) for index in range(7)))

    target = OvernightReversalStrategy().decide(context)

    assert "SPY" not in {weight.symbol for weight in target.weights}


def test_candidate_selects_at_most_six_names() -> None:
    context = _two_day_context(tuple((f"S{index}", 90.0 + index) for index in range(10)))

    target = OvernightReversalStrategy().decide(context)

    assert len(target.weights) <= TOP_N


def test_candidate_uses_exact_fixed_per_name_weight() -> None:
    context = _two_day_context(tuple((f"S{index}", 90.0 + index) for index in range(6)))

    target = OvernightReversalStrategy().decide(context)

    assert [weight.weight for weight in target.weights] == [FIXED_PER_NAME_WEIGHT] * TOP_N
    assert target.invested_weight == pytest.approx(EQUITY_ALLOCATION)
    assert target.cash_weight == pytest.approx(0.45)
    assert all(weight.weight <= POSITION_CAP for weight in target.weights)


def test_unused_capacity_remains_cash_when_fewer_than_six_pairs_exist() -> None:
    context = _two_day_context((("AAA", 90.0), ("BBB", 95.0)))

    target = OvernightReversalStrategy().decide(context)

    assert len(target.weights) == 2
    assert target.invested_weight == pytest.approx(2 * FIXED_PER_NAME_WEIGHT)
    assert target.cash_weight == pytest.approx(1.0 - 2 * FIXED_PER_NAME_WEIGHT)


def test_candidate_decision_is_timestamped_on_completed_current_bar() -> None:
    decision_day = date(2024, 1, 3)
    context = _two_day_context((("AAA", 90.0),), decision_day=decision_day)

    target = OvernightReversalStrategy().decide(context)

    assert target.decision_timestamp == _timestamp(decision_day)
    assert target.data_timestamp == _timestamp(decision_day)
    for record in target.metadata["signal_records"]:
        assert record["decision_date"] == decision_day.isoformat()
        assert record["prior_date"] == "2024-01-02"


def test_future_bar_cannot_change_a_past_signal() -> None:
    stock, spy = _signal_maps()
    baseline = signal_from_bars("AAA", date(2024, 1, 3), date(2024, 1, 2), stock, spy)
    stock[date(2024, 1, 4)] = _bar("AAA", date(2024, 1, 4), opening=1.0, closing=1.0)
    spy[date(2024, 1, 4)] = _bar("SPY", date(2024, 1, 4), opening=1.0, closing=1.0)

    replayed = signal_from_bars("AAA", date(2024, 1, 3), date(2024, 1, 2), stock, spy)

    assert replayed.score == pytest.approx(baseline.score)
    assert replayed.current_open == pytest.approx(baseline.current_open)


def test_candidate_parameter_set_is_frozen() -> None:
    strategy = OvernightReversalStrategy()

    assert strategy.parameters == FROZEN_PARAMETERS
    with pytest.raises(ValueError, match="frozen"):
        OvernightReversalStrategy({**FROZEN_PARAMETERS, "top_n": 7})


def test_candidate_identity_and_declared_cadence_are_research_only() -> None:
    strategy = OvernightReversalStrategy()

    assert strategy.name == CANDIDATE_NAME
    assert strategy.version == CANDIDATE_VERSION
    assert strategy.spec().rebalance_policy == "daily"
    assert strategy.spec().primary_timeframe == "1d"


def test_strategy_alpha_drives_observation_set_before_stream_truncation() -> None:
    symbols = tuple(f"S{index}" for index in range(8))
    context = _two_day_context(tuple((symbol, 90.0 + index) for index, symbol in enumerate(symbols)))
    observation = build_observation_set(
        OvernightReversalStrategy(),
        context.history,
        ("SPY", *symbols),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=context.decision_timestamp,
        maximum_stream_symbols=4,
        pinned_symbols=("SPY",),
    )

    assert observation.source == "strategy_alpha"
    assert observation.observation_set == ("SPY", "S0", "S1", "S2")
    assert observation.alpha_scored_count == len(symbols)


def test_observation_set_preserves_reference_pin_and_excludes_spy_from_alpha() -> None:
    context = _two_day_context((("AAA", 90.0), ("BBB", 95.0)))
    observation = build_observation_set(
        OvernightReversalStrategy(),
        context.history,
        ("AAA", "SPY", "BBB"),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=context.decision_timestamp,
        maximum_stream_symbols=2,
        pinned_symbols=("SPY",),
    )

    assert observation.observation_set[0] == "SPY"
    assert "SPY" not in observation.ranked_pool


def test_risk_overlay_keeps_frozen_asset_and_position_caps_authoritative() -> None:
    target = TargetPortfolio(
        strategy_name=CANDIDATE_NAME,
        strategy_version=CANDIDATE_VERSION,
        decision_timestamp=_timestamp(date(2024, 1, 3)),
        weights=tuple(TargetWeight(f"S{index}", 0.15, rank=index + 1) for index in range(6)),
        cash_weight=0.0,
    )
    limits = PortfolioRiskLimits(
        maximum_gross=1.0,
        maximum_equity=1.0,
        maximum_crypto=1.0,
        maximum_equity_asset=0.10,
        maximum_crypto_asset=1.0,
        minimum_cash=0.0,
        maximum_positions=3,
        maximum_crypto_positions=3,
    )
    decision = PortfolioRiskOverlay(limits).apply(
        target,
        AccountSnapshot(100_000.0, 100_000.0, 100_000.0, 0.0),
        market=Market.EQUITY,
        approved_symbols=tuple(f"S{index}" for index in range(6)),
        maximum_allocation=EQUITY_ALLOCATION,
        mode="backtest",
    )

    assert len(decision.target.weights) <= 3
    assert all(weight.weight <= POSITION_CAP for weight in decision.target.weights)
    assert decision.target.cash_weight >= 1.0 - EQUITY_ALLOCATION


def test_daily_policy_fires_once_per_new_exchange_session() -> None:
    policy = DailySessionRebalancePolicy()
    first = _timestamp(date(2024, 1, 3), 15)
    same_session_later = _timestamp(date(2024, 1, 3), 21)
    next_session = _timestamp(date(2024, 1, 4), 15)

    assert policy.is_period_start(None, first)
    assert not policy.is_period_start(first, same_session_later)
    assert policy.is_period_start(same_session_later, next_session)


def test_session_date_is_derived_from_new_york_not_utc_midnight() -> None:
    timestamp = datetime(2024, 1, 3, 1, 0, tzinfo=UTC)

    assert session_date(timestamp) == date(2024, 1, 2)


def test_engine_fills_only_after_the_decision_bar() -> None:
    start = date(2024, 1, 2)
    days = [start + timedelta(days=index) for index in range(4)]
    universe = PointInTimeUniverse(
        Market.EQUITY,
        (
            UniverseSnapshot(
                effective_at=_timestamp(start),
                symbols=frozenset({"SPY", "AAA"}),
                observation_set=("SPY", "AAA"),
                maximum_stream_symbols=2,
                pinned_symbols=("SPY",),
            ),
        ),
        "overnight-test",
    )
    bars = []
    for index, day in enumerate(days):
        bars.extend(
            (
                _bar("SPY", day, opening=100.0 + index, closing=100.0 + index),
                _bar("AAA", day, opening=90.0 + index, closing=90.0 + index),
            )
        )
    engine = PortfolioBacktestEngine(
        OvernightReversalStrategy(),
        DailySessionRebalancePolicy(),
        starting_equity=100_000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        universe=universe,
        universe_version="overnight-test",
        allow_static_universe=True,
    )

    result = engine.run(bars, liquidate_at_end=False)

    assert result.fills
    assert all(fill.timestamp > fill.decision_timestamp for fill in result.fills)
    assert all(fill.timestamp != fill.decision_timestamp for fill in result.fills)


def test_score_context_returns_one_row_per_visible_pool_symbol() -> None:
    context = _two_day_context((("AAA", 90.0), ("BBB", 95.0)))

    rows = score_context(context)

    assert {row.symbol for row in rows} == {"AAA", "BBB"}
    assert all(row.decision_date == date(2024, 1, 3) for row in rows)


def test_selected_rows_carry_one_based_alpha_ranks() -> None:
    context = _two_day_context(tuple((f"S{index}", 90.0 + index) for index in range(6)))

    target = OvernightReversalStrategy().decide(context)
    records = {row["symbol"]: row for row in target.metadata["signal_records"]}

    assert [records[f"S{index}"]["observation_rank"] for index in range(6)] == [1, 2, 3, 4, 5, 6]
    assert all(records[f"S{index}"]["selected"] is True for index in range(6))


def test_target_sum_is_never_levered() -> None:
    context = _two_day_context(tuple((f"S{index}", 90.0 + index) for index in range(20)))

    target = OvernightReversalStrategy().decide(context)

    assert target.invested_weight + target.cash_weight <= 1.0 + 1e-12
