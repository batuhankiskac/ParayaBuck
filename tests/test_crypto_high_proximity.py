"""CryptoHighProximity1dV1 contract tests.

Wave-2 correctness suite for ``crypto_high_proximity_v1``: the trailing-364-
day close-to-high proximity signal over COMPLETED bars (decision bar
included), descending proximity ranking with symbol-name tie-breaks,
rank_candidates == decide scoring parity, weekly hours:168 cadence,
strategy-alpha observation-set selection, risk-overlay caps and
next-tradable-bar execution ordering (T+1 fill at next open, 28 bps/side at
1x).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio import rebalance_policy_for, validate_strategy_timeframe
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_high_proximity import CryptoHighProximity1dV1
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason
from parayabuck.universe.observation_set import build_observation_set

HIGH_WINDOW_DAYS = 364
MIN_HISTORY_BARS = HIGH_WINDOW_DAYS + 1
CRYPTO_SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD")


def _bar(symbol: str, timestamp: datetime, close: float, market: Market = Market.CRYPTO) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        market=market,
    )


def _manual_series(
    symbol: str, closes: list[float], start: datetime | None = None
) -> tuple[Bar, ...]:
    start = start or datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        _bar(symbol, start + timedelta(days=index), close) for index, close in enumerate(closes)
    )


def _series(symbol: str, close: float, bars: int = MIN_HISTORY_BARS) -> tuple[Bar, ...]:
    """``bars`` aligned 1d bars whose trailing-364-day high is exactly 100.0
    and whose decision-bar close is ``close`` (score = close / 100)."""
    assert close <= 100.0  # the decision close can never exceed the in-window high
    closes = [100.0] * (bars - 1) + [close]
    return _manual_series(symbol, closes)


def _context(
    strategy: CryptoHighProximity1dV1,
    histories: dict[str, tuple[Bar, ...]],
    universe: object | None = None,
    decision_timestamp: datetime | None = None,
) -> PortfolioContext:
    if decision_timestamp is None:
        decision_timestamp = max(
            max((bar.timestamp for bar in series), default=datetime(2024, 1, 1, tzinfo=UTC))
            for series in histories.values()
        )
    return PortfolioContext(
        decision_timestamp=decision_timestamp,
        market=Market.CRYPTO,
        timeframe="1d",
        history=histories,
        universe=universe,
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="BTC/USD",
    )


class _Universe:
    """PIT pool-membership mock: ``contains`` decides pool membership."""

    def __init__(self, symbols: set[str]) -> None:
        self._symbols = symbols

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return symbol in self._symbols


def _risk_limits() -> PortfolioRiskLimits:
    return PortfolioRiskLimits(
        daily_loss_limit=0.05,
        drawdown_halt=0.20,
        maximum_gross=0.60,
        maximum_equity=0.60,
        maximum_crypto=0.10,
        maximum_equity_asset=0.10,
        maximum_crypto_asset=0.05,
        minimum_cash=0.40,
        maximum_positions=6,
        maximum_crypto_positions=2,
    )


def _declining_crypto_bars(symbols: tuple[str, ...], days: int = 370) -> list[Bar]:
    """Aligned 1d crypto bars in a persistent downtrend: every symbol's
    trailing-364-day high sits at the start of its window, so every score is
    in (0, 1) and the whole pool qualifies for high proximity."""
    start = datetime(2024, 1, 4, tzinfo=UTC)  # exactly on a 168h block boundary
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            close = 100.0 * (0.9995 - offset * 0.0005) ** index
            bars.append(_bar(symbol, timestamp, close))
    return bars


# ---------------------------------------------------------------------------
# Signal and ranking contracts
# ---------------------------------------------------------------------------


def test_formula_is_close_over_trailing_364_day_high() -> None:
    """score = closes[-1] / max(closes[-364:]) on a hand-computable series:
    363 flat closes, then a 120 in-window spike and a 114 decision close ->
    high = 120 and score = 114/120 = 0.95 exactly."""
    strategy = CryptoHighProximity1dV1()
    closes = [100.0] * 363 + [120.0, 114.0]
    context = _context(strategy, {"WIN/USD": _manual_series("WIN/USD", closes)})
    scores = strategy._score_candidates(context)
    assert len(scores) == 1 and scores[0][1] == "WIN/USD"
    assert scores[0][0] == pytest.approx(114.0 / 120.0, abs=1e-12)
    assert strategy.rank_candidates(context) == ("WIN/USD",)
    assert strategy.decide(context).weight_for("WIN/USD", Market.CRYPTO) == pytest.approx(1.0)


def test_window_is_exactly_trailing_364_closes_decision_bar_included() -> None:
    """Only the last 364 closes anchor the high (decision bar included): a
    spike at closes[-365] must NOT move the score, flipping closes[-364] must,
    and a decision close at the high scores exactly 1.0."""
    strategy = CryptoHighProximity1dV1()
    # Spike OUTSIDE the window (closes[-365] = 999): high stays 100, score 1.0.
    outside = [999.0] + [100.0] * (MIN_HISTORY_BARS - 1)
    context = _context(strategy, {"SPK/USD": _manual_series("SPK/USD", outside)})
    assert strategy._score_candidates(context) == ((1.0, "SPK/USD"),)
    # Flip ONLY closes[-364] (the first in-window close) 100 -> 105: the
    # window high becomes 105 and the score 100/105.
    flipped = [999.0, 105.0] + [100.0] * (MIN_HISTORY_BARS - 2)
    context = _context(strategy, {"SPK/USD": _manual_series("SPK/USD", flipped)})
    scores = strategy._score_candidates(context)
    assert scores[0][0] == pytest.approx(100.0 / 105.0, abs=1e-12)
    # Decision-bar close is part of the window: flat series scores 1.0.
    at_high = _manual_series("ATH/USD", [100.0] * MIN_HISTORY_BARS)
    assert strategy.rank_candidates(_context(strategy, {"ATH/USD": at_high})) == ("ATH/USD",)


def test_descending_score_ranking_with_symbol_tie_break() -> None:
    """Known proximity scores rank highest-first; equal scores tie-break by
    ascending symbol name; decide() keeps the top-2 at equal weight."""
    strategy = CryptoHighProximity1dV1()
    history = {
        "AAA/USD": _series("AAA/USD", 99.0),  # score 0.99
        "EEE/USD": _series("EEE/USD", 99.0),  # score 0.99 (ties AAA)
        "BBB/USD": _series("BBB/USD", 95.0),  # score 0.95
        "CCC/USD": _series("CCC/USD", 90.0),  # score 0.90
        "DDD/USD": _series("DDD/USD", 80.0),  # score 0.80
    }
    context = _context(strategy, history)
    assert strategy.rank_candidates(context) == (
        "AAA/USD",
        "EEE/USD",
        "BBB/USD",
        "CCC/USD",
        "DDD/USD",
    )
    target = strategy.decide(context)
    assert {weight.symbol for weight in target.weights} == {"AAA/USD", "EEE/USD"}
    assert all(weight.weight == pytest.approx(0.5) for weight in target.weights)
    assert target.cash_weight == pytest.approx(0.0)
    assert target.rebalance_reason == RebalanceReason.SCHEDULED
    assert target.metadata["selected_symbols"] == ("AAA/USD", "EEE/USD")
    assert target.metadata["qualified_symbols"] == 5


def test_rank_candidates_shares_exact_scoring_with_decide() -> None:
    """rank_candidates() and decide() MUST score identically (same private
    helper): the observation set is selected by the same alpha the strategy
    trades."""
    strategy = CryptoHighProximity1dV1({"top_n": 3})
    history = {
        "BTC/USD": _series("BTC/USD", 98.0),
        "ETH/USD": _series("ETH/USD", 93.0),
        "SOL/USD": _series("SOL/USD", 88.0),
        "XRP/USD": _series("XRP/USD", 84.0),
        "ADA/USD": _series("ADA/USD", 70.0),
    }
    context = _context(strategy, history)
    scores = strategy._score_candidates(context)
    ranked = strategy.rank_candidates(context)
    assert ranked == tuple(symbol for _score, symbol in scores)
    # Monotone non-increasing score order.
    assert all(scores[i][0] >= scores[i + 1][0] for i in range(len(scores) - 1))
    # decide() selects exactly the top-n of the identical ranking.
    target = strategy.decide(context)
    expected = ranked[:3]
    assert tuple(sorted(weight.symbol for weight in target.weights)) == tuple(sorted(expected))
    assert all(weight.weight == pytest.approx(1 / 3) for weight in target.weights)
    # Every score matches independent arithmetic on the series.
    for score, symbol in scores:
        closes = [bar.close for bar in history[symbol]]
        expected_score = closes[-1] / max(closes[-HIGH_WINDOW_DAYS:])
        assert score == pytest.approx(expected_score, abs=1e-12)


def test_qualification_requires_pool_membership_and_full_window() -> None:
    """Qualification gates: >= 365 completed bars, PIT pool membership, and a
    strictly positive in-window high (defensive)."""
    strategy = CryptoHighProximity1dV1()
    # Insufficient history: 364 bars is one short of the 365 required.
    short = {"SHORT/USD": _series("SHORT/USD", 95.0, bars=MIN_HISTORY_BARS - 1)}
    assert strategy.rank_candidates(_context(strategy, short)) == ()
    # Full history but outside the PIT pool -> never ranks.
    histories = {
        "IN/USD": _series("IN/USD", 99.0),
        "OUT/USD": _series("OUT/USD", 99.0),
    }
    context = _context(strategy, histories, universe=_Universe({"IN/USD"}))
    assert strategy.rank_candidates(context) == ("IN/USD",)
    # Defensive: in-window high not strictly positive -> not qualified.
    zeros = {"ZERO/USD": _manual_series("ZERO/USD", [0.0] * MIN_HISTORY_BARS)}
    assert strategy.rank_candidates(_context(strategy, zeros)) == ()


def test_all_cash_risk_off_when_none_and_full_weight_when_one() -> None:
    strategy = CryptoHighProximity1dV1()
    # Empty pool -> 100% cash with RISK_OFF.
    target = strategy.decide(
        _context(strategy, {}, decision_timestamp=datetime(2024, 3, 12, tzinfo=UTC))
    )
    assert target.weights == ()
    assert target.cash_weight == 1.0
    assert target.rebalance_reason == RebalanceReason.RISK_OFF
    assert target.metadata["risk_off_detail"] == "no_qualified_symbols"
    # One qualifier takes the whole (uncapped) target; the overlay caps it
    # downstream (covered by the backtest cap test).
    solo = {"SOLO/USD": _series("SOLO/USD", 95.0)}
    target = strategy.decide(_context(strategy, solo))
    assert [weight.symbol for weight in target.weights] == ["SOLO/USD"]
    assert target.weight_for("SOLO/USD", Market.CRYPTO) == pytest.approx(1.0)
    assert target.cash_weight == pytest.approx(0.0)
    assert target.rebalance_reason == RebalanceReason.SCHEDULED
    assert target.metadata["selected_symbols"] == ("SOLO/USD",)


def test_parameter_validation_and_custom_window_flow() -> None:
    with pytest.raises(ValueError, match="high_window_days must be at least 20"):
        CryptoHighProximity1dV1({"high_window_days": 19})
    for bad_top_n in (0, 6):
        with pytest.raises(ValueError, match="top_n must be between 1 and 5"):
            CryptoHighProximity1dV1({"top_n": bad_top_n})
    for bad_hours in (0, -168, 25):
        with pytest.raises(ValueError, match="rebalance_hours must be a positive multiple of 24"):
            CryptoHighProximity1dV1({"rebalance_hours": bad_hours})
    # Custom parameters flow into the window, the history gate and the score.
    strategy = CryptoHighProximity1dV1({"high_window_days": 30, "top_n": 3})
    assert strategy.minimum_history_bars == 31
    closes = [100.0] * 30 + [90.0]  # 31 closes, window 30 -> high 100, score 0.9
    context = _context(strategy, {"CUST/USD": _manual_series("CUST/USD", closes)})
    assert strategy.rank_candidates(context) == ("CUST/USD",)
    assert strategy._score_candidates(context)[0][0] == pytest.approx(0.9)
    assert strategy.decide(context).weight_for("CUST/USD", Market.CRYPTO) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Cadence contract
# ---------------------------------------------------------------------------


def test_weekly_cadence_hours_168_epoch_blocks() -> None:
    """rebalance_policy_for yields RebalancePolicy(CRYPTO, hours:168) and
    is_period_start fires exactly on the first closed 1d bar of each 168h
    epoch block (a weekly decision)."""
    strategy = CryptoHighProximity1dV1()
    policy = rebalance_policy_for(strategy)
    assert policy == RebalancePolicy(Market.CRYPTO, "hours:168")
    assert policy.market == Market.CRYPTO
    assert policy.frequency == "hours:168"
    assert policy.hours == 168
    # 2024-01-04T00:00Z sits exactly on a 168-hour block boundary.
    start = datetime(2024, 1, 4, tzinfo=UTC)
    assert int(start.timestamp()) % (168 * 3600) == 0
    # The first closed bar of the run always opens a rebalance period.
    assert policy.is_period_start(None, start) is True
    fired: list[datetime] = []
    previous: datetime | None = None
    for index in range(85):
        current = start + timedelta(days=index)
        if policy.is_period_start(previous, current):
            fired.append(current)
        previous = current
    # 168h / 24h = 7: exactly every 7th daily bar opens a new epoch block.
    assert fired == [start + timedelta(days=7 * index) for index in range(13)]


# ---------------------------------------------------------------------------
# Observation-set contracts
# ---------------------------------------------------------------------------


def test_observation_set_ranks_full_pool_and_keeps_top_alpha() -> None:
    """Pool larger than maximum_stream_symbols: the alpha runs on the FULL
    pool and the streamed observation set is the top-ranked subset."""
    strategy = CryptoHighProximity1dV1()
    pool = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOT/USD")
    history = {
        "BTC/USD": _series("BTC/USD", 99.0),
        "ETH/USD": _series("ETH/USD", 95.0),
        "SOL/USD": _series("SOL/USD", 90.0),
        "XRP/USD": _series("XRP/USD", 85.0),
        "ADA/USD": _series("ADA/USD", 80.0),
        "DOT/USD": _series("DOT/USD", 75.0),
    }
    observation = build_observation_set(
        strategy,
        history,
        pool,
        market=Market.CRYPTO,
        timeframe="1d",
        decision_timestamp=datetime(2024, 3, 12, tzinfo=UTC),
        maximum_stream_symbols=3,
    )
    assert observation.source == "strategy_alpha"
    assert observation.strategy_name == "crypto_high_proximity_v1"
    assert observation.observation_set == ("BTC/USD", "ETH/USD", "SOL/USD")
    assert set(observation.observation_set) <= set(pool)
    assert len(observation.observation_set) <= 3
    # A top-2 decision symbol of the FULL pool is inside the stream.
    top2 = strategy.decide(_context(strategy, history))
    assert {weight.symbol for weight in top2.weights} <= set(observation.observation_set)


def test_managed_positions_remain_pinned_regardless_of_ranking() -> None:
    """Pinned symbols survive even when they never rank (too young to have
    the 365-bar window)."""
    strategy = CryptoHighProximity1dV1()
    pool = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD")
    history = {
        "BTC/USD": _series("BTC/USD", 99.0),
        "ETH/USD": _series("ETH/USD", 50.0, bars=50),  # non-ranking pin
        "SOL/USD": _series("SOL/USD", 90.0),
        "XRP/USD": _series("XRP/USD", 80.0),
    }
    observation = build_observation_set(
        strategy,
        history,
        pool,
        market=Market.CRYPTO,
        timeframe="1d",
        decision_timestamp=datetime(2024, 3, 12, tzinfo=UTC),
        maximum_stream_symbols=3,
        pinned_symbols=("BTC/USD", "ETH/USD"),
    )
    assert observation.source == "strategy_alpha"
    assert observation.observation_set == ("BTC/USD", "ETH/USD", "SOL/USD")


def test_observation_set_length_respects_stream_capacity() -> None:
    """Length == pins + min(ranked, capacity) and never exceeds the stream
    limit."""
    strategy = CryptoHighProximity1dV1()
    pool = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOT/USD")
    history = {
        symbol: _series(symbol, close)
        for symbol, close in (
            ("BTC/USD", 99.0),
            ("ETH/USD", 95.0),
            ("SOL/USD", 90.0),
            ("XRP/USD", 85.0),
            ("ADA/USD", 80.0),
            ("DOT/USD", 75.0),
        )
    }
    # No pins, capacity 2: exactly min(6, 2) streamed.
    observation = build_observation_set(
        strategy,
        history,
        pool,
        market=Market.CRYPTO,
        timeframe="1d",
        decision_timestamp=datetime(2024, 3, 12, tzinfo=UTC),
        maximum_stream_symbols=2,
    )
    assert len(observation.observation_set) == 2
    assert observation.observation_set == ("BTC/USD", "ETH/USD")
    # One pin (ranked last) + capacity 3: pins + min(ranked, remaining) == 3.
    observation = build_observation_set(
        strategy,
        history,
        pool,
        market=Market.CRYPTO,
        timeframe="1d",
        decision_timestamp=datetime(2024, 3, 12, tzinfo=UTC),
        maximum_stream_symbols=3,
        pinned_symbols=("DOT/USD",),
    )
    assert observation.observation_set == ("DOT/USD", "BTC/USD", "ETH/USD")
    assert len(observation.observation_set) == 1 + min(3, 2) <= 3


# ---------------------------------------------------------------------------
# Engine / runtime contracts
# ---------------------------------------------------------------------------


def test_t_plus_1_fill_at_next_open_with_28bps_per_side_cost() -> None:
    """A weekly decision on a closed bar fills at the NEXT bar's open with
    28 bps/side cost at 1x (25 bps taker + 2 bps slippage + 1 bps
    half-spread)."""
    strategy = CryptoHighProximity1dV1()
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=100_000.0,
        benchmark_symbol="BTC/USD",
        slippage_bps=2.0,
        spread_bps=2.0,
        crypto_taker_fee_bps=25.0,
    )
    bars = _declining_crypto_bars(CRYPTO_SYMBOLS, days=366)
    # The first decision fires on the first 168h block boundary whose window
    # is full (2024-01-04 + 364 days); the fill happens one day later.
    decision_day = datetime(2024, 1, 4, tzinfo=UTC) + timedelta(days=364)
    fill_day = decision_day + timedelta(days=1)
    marked_opens = {symbol: 110.0 + offset * 10.0 for offset, symbol in enumerate(CRYPTO_SYMBOLS)}
    bars = [
        (
            replace(bar, open=marked_opens[bar.symbol], high=marked_opens[bar.symbol])
            if bar.timestamp == fill_day
            else bar
        )
        for bar in bars
    ]
    result = engine.run(bars, liquidate_at_end=False)
    assert result.rebalances
    assert len(result.rebalances) == 1
    assert result.rebalances[0].decision_timestamp == decision_day
    assert result.fills
    adverse = 2.0 + 2.0 / 2  # 3 bps adverse at the next open
    for fill in result.fills:
        # T+1: the decision bar and the fill bar are strictly one day apart.
        assert fill.decision_timestamp == decision_day
        assert fill.timestamp == fill_day
        assert fill.timestamp > fill.decision_timestamp
        assert fill.side.value == "buy"
        expected_open = marked_opens[fill.symbol]
        # Fill executes at the NEXT bar's OPEN with adverse adjustment.
        assert fill.reference_price == pytest.approx(expected_open)
        assert fill.fill_price == pytest.approx(expected_open * (1 + adverse / 10_000))
        # 25 bps taker on the fill notional + 2 bps slippage + 1 bps
        # half-spread = 28 bps per side at 1x.
        fill_notional = fill.qty * fill.fill_price
        assert fill.fee == pytest.approx(fill_notional * 25.0 / 10_000, rel=1e-9)
        notional = fill.qty * fill.reference_price
        assert fill.slippage_cost == pytest.approx(notional * 2.0 / 10_000, rel=1e-9)
        assert fill.spread_cost == pytest.approx(notional * 1.0 / 10_000, rel=1e-9)
        cost = fill.fee + fill.slippage_cost + fill.spread_cost
        expected_cost_bps = (25.0 * (1 + adverse / 10_000) + 2.0 + 1.0) / 10_000
        assert cost / notional == pytest.approx(expected_cost_bps, rel=1e-9)


def test_crypto_sleeve_per_asset_and_position_caps_in_backtest() -> None:
    """A tiny portfolio backtest: positions never exceed max_crypto_position_pct
    (0.05) per asset, total crypto exposure stays <= 0.10 and the position
    count stays <= 2 (maximum_crypto_positions)."""
    strategy = CryptoHighProximity1dV1()
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=100_000.0,
        benchmark_symbol="BTC/USD",
        risk_limits=_risk_limits(),
        maximum_allocation=0.10,
    )
    bars = _declining_crypto_bars(("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD"))
    result = engine.run(bars, liquidate_at_end=False)
    assert result.rebalances
    assert result.weight_history
    for _decision, weights in result.weight_history:
        assert len(weights) <= 2  # maximum_crypto_positions
        for symbol, weight in weights.items():
            assert weight <= 0.05 + 1e-9  # max_crypto_position_pct
        assert sum(weights.values()) <= 0.10 + 1e-9  # max crypto sleeve exposure
    # Final managed holdings respect the position-count cap too.
    assert len(result.state.holdings) <= 2
    assert all(qty >= 0 for qty in result.state.holdings.values())


# ---------------------------------------------------------------------------
# Warmup and registration contracts
# ---------------------------------------------------------------------------


def test_warmup_requires_365_bars() -> None:
    strategy = CryptoHighProximity1dV1()
    assert strategy.minimum_history_bars == MIN_HISTORY_BARS
    short = {symbol: _series(symbol, 95.0, bars=MIN_HISTORY_BARS - 1) for symbol in CRYPTO_SYMBOLS}
    assert strategy.supports(_context(strategy, short)) is False
    ready = {symbol: _series(symbol, 95.0) for symbol in CRYPTO_SYMBOLS}
    assert strategy.supports(_context(strategy, ready)) is True


def test_registration_spec_and_timeframe_validation() -> None:
    strategy = CryptoHighProximity1dV1()
    assert isinstance(strategy, CryptoHighProximity1dV1)
    assert strategy.name == "crypto_high_proximity_v1"
    assert strategy.version == "1.0.0"
    assert strategy.market == Market.CRYPTO
    assert strategy.timeframe == "1d"
    assert strategy.display_name == "High Proximity v1"
    assert strategy.supported_timeframes == ("1d",)
    assert strategy.default_rebalance_hours == 168
    assert strategy.rebalance_policy_declared == "hours:168"
    with pytest.raises(ValueError, match="not supported"):
        validate_strategy_timeframe(strategy, "4h")
    validate_strategy_timeframe(strategy, "1d")
    spec = strategy.spec()
    assert spec.primary_timeframe == "1d"
    assert spec.supported_timeframes == ("1d",)
    assert spec.rebalance_policy == "hours:168"
    assert spec.market == Market.CRYPTO
    assert spec.minimum_history_bars == MIN_HISTORY_BARS
    assert rebalance_policy_for(strategy) == RebalancePolicy(Market.CRYPTO, "hours:168")
