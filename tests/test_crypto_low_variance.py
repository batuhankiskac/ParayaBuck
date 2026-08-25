"""CryptoLowVariance1dV1 contract tests.

Wave-2 correctness suite for ``crypto_low_variance_v1``: the exact frozen
AHXZ signal (sample std, n-1, of the trailing 22 daily simple returns over
COMPLETED bars — decision bar included), the consecutive-calendar-days rule
(a missing day inside the lookback window disqualifies), ascending-rank with
symbol-name tie-breaks, rank_candidates == decide scoring parity, weekly
hours:168 cadence, strategy-alpha observation-set selection, risk-overlay
caps and next-tradable-bar execution ordering with the 28 bps/side cost
model (25 bps taker + 2 bps slippage + 1 bp half-spread).
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio import rebalance_policy_for, validate_strategy_timeframe
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_low_variance import CryptoLowVariance1dV1
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason
from parayabuck.universe.observation_set import build_observation_set

VARIANCE_LOOKBACK = 22
MIN_HISTORY_BARS = VARIANCE_LOOKBACK + 1
CRYPTO_SYMBOLS = ("LOVOL/USD", "MIDVOL/USD", "HIVOL/USD")


def _strategy(**parameters: object) -> CryptoLowVariance1dV1:
    # The registry entry is added by the lead after implementation; contract
    # tests construct the class directly so they run pre-registration.
    return CryptoLowVariance1dV1(parameters or None)


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


def _manual_series(symbol: str, closes: list[float], start: datetime | None = None) -> tuple[Bar, ...]:
    start = start or datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(_bar(symbol, start + timedelta(days=index), close) for index, close in enumerate(closes))


def _flat_series(symbol: str, bars: int = MIN_HISTORY_BARS, close: float = 100.0) -> tuple[Bar, ...]:
    """``bars`` aligned 1d bars all closing at ``close`` -> realized vol 0."""
    return _manual_series(symbol, [close] * bars)


def _alternating_series(symbol: str, amplitude: float, bars: int = MIN_HISTORY_BARS) -> tuple[Bar, ...]:
    """Bars oscillating 100 / (100 + amplitude) -> vol grows with amplitude."""
    closes = [100.0 + amplitude if index % 2 else 100.0 for index in range(bars)]
    return _manual_series(symbol, closes)


def _trend_series(symbol: str, bars: int = MIN_HISTORY_BARS) -> tuple[Bar, ...]:
    """Constant 1% daily growth -> every return identical -> realized vol ~0."""
    closes = [100.0 * 1.01**index for index in range(bars)]
    return _manual_series(symbol, closes)


def _holey_series(symbol: str) -> tuple[Bar, ...]:
    """23 completed bars spanning 24 calendar days: day 11 is MISSING inside
    the lookback window, so the last-23 span is 23 days, not 22."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [_bar(symbol, start + timedelta(days=index), 100.0) for index in range(24) if index != 11]
    assert len(bars) == MIN_HISTORY_BARS
    return tuple(bars)


class _Universe:
    """Fake PIT universe snapshot: ``contains`` answers from a member set."""

    def __init__(self, members: set[str]) -> None:
        self._members = set(members)

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return symbol in self._members


def _context(
    strategy: CryptoLowVariance1dV1,
    histories: dict[str, tuple[Bar, ...]],
    universe: object | None = None,
) -> PortfolioContext:
    last_bar = max(
        max((bar.timestamp for bar in series), default=datetime(2024, 1, 1, tzinfo=UTC))
        for series in histories.values()
    )
    return PortfolioContext(
        decision_timestamp=last_bar,
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


def _crypto_bars(symbols: tuple[str, ...], days: int = 40) -> list[Bar]:
    """Aligned consecutive 1d crypto bars with distinct realized volatilities:
    LOVOL flat (vol 0), then increasing amplitude per symbol."""
    amplitudes = {
        "LOVOL/USD": 0.0,
        "MIDVOL/USD": 1.0,
        "XRP/USD": 5.0,
        "HIVOL/USD": 10.0,
    }
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for symbol in symbols:
            close = 100.0 + amplitudes.get(symbol, 0.0) if index % 2 else 100.0
            bars.append(_bar(symbol, timestamp, close))
    return bars


# ---------------------------------------------------------------------------
# Signal and ranking contracts
# ---------------------------------------------------------------------------


def test_lowest_realized_variance_ranks_first() -> None:
    """Ascending sigma ranking: flat (vol 0) first, constant-growth ~0 next,
    then mild then wild; decide() keeps the top-2 at equal weight."""
    strategy = _strategy()
    history = {
        "WILD/USD": _alternating_series("WILD/USD", 10.0),
        "MILD/USD": _alternating_series("MILD/USD", 1.0),
        "TREND/USD": _trend_series("TREND/USD"),
        "FLAT/USD": _flat_series("FLAT/USD"),
    }
    context = _context(strategy, history)
    assert strategy.rank_candidates(context) == (
        "FLAT/USD",
        "TREND/USD",
        "MILD/USD",
        "WILD/USD",
    )
    scores = {symbol: score for score, symbol in strategy._score_candidates(context)}
    assert scores["FLAT/USD"] == 0.0
    assert scores["TREND/USD"] == pytest.approx(0.0, abs=1e-12)
    assert 0.0 < scores["MILD/USD"] < scores["WILD/USD"]
    target = strategy.decide(context)
    assert {weight.symbol for weight in target.weights} == {"FLAT/USD", "TREND/USD"}
    assert all(weight.weight == pytest.approx(0.5) for weight in target.weights)
    assert target.cash_weight == pytest.approx(0.0)
    assert target.rebalance_reason == RebalanceReason.SCHEDULED


def test_symbol_name_tie_break_for_equal_sigma() -> None:
    """Identical sigma (all zero) ties are broken by ascending symbol name."""
    strategy = _strategy()
    history = {
        "ZZZ/USD": _flat_series("ZZZ/USD"),
        "AAA/USD": _flat_series("AAA/USD"),
        "MMM/USD": _flat_series("MMM/USD"),
    }
    context = _context(strategy, history)
    assert strategy.rank_candidates(context) == ("AAA/USD", "MMM/USD", "ZZZ/USD")


def test_score_matches_independent_sample_std_arithmetic() -> None:
    """The frozen formula on hand-computable series: sigma_t is the sample
    std (n-1) of the last 22 daily simple returns, decision bar included."""
    strategy = _strategy()
    for symbol, closes in (
        ("MILD/USD", [100.0 if index % 2 == 0 else 101.0 for index in range(23)]),
        ("WILD/USD", [100.0 if index % 2 == 0 else 110.0 for index in range(23)]),
        ("JUMP/USD", [100.0] * 22 + [110.0]),
        ("FLAT/USD", [100.0] * 23),
    ):
        history = {symbol: _manual_series(symbol, closes)}
        context = _context(strategy, history)
        ((score, scored_symbol),) = strategy._score_candidates(context)
        assert scored_symbol == symbol
        returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
        assert len(returns) == VARIANCE_LOOKBACK
        assert score == pytest.approx(statistics.stdev(returns), rel=1e-12)


def test_signal_includes_the_decision_bar_close() -> None:
    """The LAST completed bar (closes[-1]) is the signal point: a final jump
    close must enter the 22 returns. If it were skipped the window would be
    flat and sigma would be 0."""
    strategy = _strategy()
    closes = [100.0] * (MIN_HISTORY_BARS - 1) + [110.0]
    history = {"LAST/USD": _manual_series("LAST/USD", closes)}
    context = _context(strategy, history)
    assert strategy.rank_candidates(context) == ("LAST/USD",)
    ((score, symbol),) = strategy._score_candidates(context)
    assert symbol == "LAST/USD"
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
    assert score == pytest.approx(statistics.stdev(returns), rel=1e-12)
    assert score > 0.0
    assert strategy.decide(context).weight_for("LAST/USD", Market.CRYPTO) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Qualification contracts
# ---------------------------------------------------------------------------


def test_missing_calendar_day_inside_lookback_disqualifies() -> None:
    """Consecutive-days rule: 23 bars spanning 24 calendar days (one missing
    day inside the window) must NOT qualify even though the bar count is
    exactly the minimum; a consecutive control series does qualify."""
    strategy = _strategy()
    history = {
        "HOLY/USD": _holey_series("HOLY/USD"),
        "FULL/USD": _flat_series("FULL/USD"),
    }
    context = _context(strategy, history)
    assert len(context.history["HOLY/USD"]) == MIN_HISTORY_BARS
    assert strategy.rank_candidates(context) == ("FULL/USD",)
    scores = {symbol: score for score, symbol in strategy._score_candidates(context)}
    assert "HOLY/USD" not in scores
    assert scores["FULL/USD"] == 0.0
    # Single qualifier takes the whole (uncapped) target; the overlay caps it
    # downstream (covered by the backtest cap test).
    target = strategy.decide(context)
    assert [weight.symbol for weight in target.weights] == ["FULL/USD"]
    assert target.weight_for("FULL/USD", Market.CRYPTO) == pytest.approx(1.0)
    assert target.cash_weight == pytest.approx(0.0)
    assert target.rebalance_reason == RebalanceReason.SCHEDULED


def test_extra_history_beyond_lookback_is_ignored() -> None:
    """Only the LAST 23 completed bars must be consecutive: older bars do not
    matter once 22 consecutive complete days accumulate."""
    strategy = _strategy()
    closes = [100.0] * 30  # 30 consecutive calendar days
    context = _context(strategy, {"EXTRA/USD": _manual_series("EXTRA/USD", closes)})
    assert strategy.rank_candidates(context) == ("EXTRA/USD",)


def test_insufficient_history_not_qualified() -> None:
    """Fewer than 23 completed bars never qualifies (warmup edge)."""
    strategy = _strategy()
    short = {"SHORT/USD": _manual_series("SHORT/USD", [100.0] * (MIN_HISTORY_BARS - 1))}
    context = _context(strategy, short)
    assert strategy.rank_candidates(context) == ()
    target = strategy.decide(context)
    assert target.weights == ()
    assert target.cash_weight == 1.0
    assert target.rebalance_reason == RebalanceReason.RISK_OFF


def test_non_pool_member_never_qualifies() -> None:
    """Qualification starts with PIT pool membership: a perfect low-vol
    series outside the pool never ranks."""
    strategy = _strategy()
    history = {
        "AAA/USD": _flat_series("AAA/USD"),
        "BBB/USD": _flat_series("BBB/USD"),
    }
    context = _context(strategy, history, universe=_Universe({"AAA/USD"}))
    assert strategy.rank_candidates(context) == ("AAA/USD",)
    assert strategy.decide(context).metadata["selected_symbols"] == ("AAA/USD",)


def test_all_cash_when_no_qualified() -> None:
    """Zero qualified symbols -> 100% cash RISK_OFF."""
    strategy = _strategy()
    history = {
        "HOLY1/USD": _holey_series("HOLY1/USD"),
        "HOLY2/USD": _holey_series("HOLY2/USD"),
    }
    target = strategy.decide(_context(strategy, history))
    assert target.weights == ()
    assert target.cash_weight == 1.0
    assert target.rebalance_reason == RebalanceReason.RISK_OFF
    assert target.metadata["risk_off_detail"] == "no_qualified_low_variance"


# ---------------------------------------------------------------------------
# rank_candidates == decide parity
# ---------------------------------------------------------------------------


def test_rank_candidates_matches_decide_scoring() -> None:
    """decide() must select exactly the top-n of rank_candidates() with the
    same scores (shared _score_candidates), equal weight, cash remainder."""
    strategy = _strategy()
    history = {
        "FLAT/USD": _flat_series("FLAT/USD"),
        "TREND/USD": _trend_series("TREND/USD"),
        "MILD/USD": _alternating_series("MILD/USD", 1.0),
        "WILD/USD": _alternating_series("WILD/USD", 10.0),
    }
    context = _context(strategy, history)
    ranked = strategy.rank_candidates(context)
    scores = strategy._score_candidates(context)
    assert ranked == tuple(symbol for _score, symbol in scores)
    assert [score for score, _symbol in scores] == sorted(score for score, _symbol in scores)
    target = strategy.decide(context)
    assert tuple(weight.symbol for weight in target.weights) == ranked[:2]
    assert all(weight.weight == pytest.approx(0.5) for weight in target.weights)
    assert target.cash_weight == pytest.approx(0.0)
    assert target.rebalance_reason == RebalanceReason.SCHEDULED
    assert target.metadata["qualified_symbols"] == len(scores)
    assert target.metadata["selected_symbols"] == ranked[:2]


# ---------------------------------------------------------------------------
# Cadence contract
# ---------------------------------------------------------------------------


def test_weekly_cadence_hours_168_epoch_blocks() -> None:
    """rebalance_policy_for yields RebalancePolicy(CRYPTO, hours:168) and
    is_period_start fires exactly on the first closed 1d bar of each 168h
    epoch block (a weekly decision)."""
    strategy = _strategy()
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
    strategy = _strategy()
    pool = ("LOVOL/USD", "MIDVOL/USD", "HIVOL/USD", "XRP/USD", "ADA/USD", "DOT/USD")
    history = {
        "LOVOL/USD": _flat_series("LOVOL/USD"),
        "MIDVOL/USD": _alternating_series("MIDVOL/USD", 1.0),
        "HIVOL/USD": _alternating_series("HIVOL/USD", 10.0),
        "XRP/USD": _alternating_series("XRP/USD", 15.0),
        "ADA/USD": _alternating_series("ADA/USD", 12.0),
        "DOT/USD": _holey_series("DOT/USD"),  # hole: never ranks
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
    assert observation.strategy_name == "crypto_low_variance_v1"
    assert observation.observation_set == ("LOVOL/USD", "MIDVOL/USD", "HIVOL/USD")
    assert set(observation.observation_set) <= set(pool)
    assert len(observation.observation_set) <= 3
    # A top-2 decision symbol of the FULL pool is inside the stream.
    top2 = strategy.decide(_context(strategy, history))
    assert {weight.symbol for weight in top2.weights} <= set(observation.observation_set)


# ---------------------------------------------------------------------------
# Engine / runtime contracts
# ---------------------------------------------------------------------------


def test_engine_fills_next_bar_open_with_28bps_side_cost() -> None:
    """A decision on closed bar t fills at the NEXT bar's OPEN (T+1, no
    same-bar execution) at 28 bps/side: 2 bps slippage + 1 bp half-spread
    price impact plus 25 bps crypto taker fee, at 1x allocation. The
    two lowest-variance names are bought."""
    strategy = _strategy()
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=100_000.0,
        benchmark_symbol="BTC/USD",
        slippage_bps=2.0,
        spread_bps=2.0,  # half-spread impact = 1 bp
        crypto_taker_fee_bps=25.0,
    )
    bars = _crypto_bars(CRYPTO_SYMBOLS)
    result = engine.run(bars, liquidate_at_end=False)
    assert result.rebalances
    assert result.fills
    assert len(result.fills) == 2  # one initial buy batch, no churn
    assert {fill.symbol for fill in result.fills} == {"LOVOL/USD", "MIDVOL/USD"}
    for fill in result.fills:
        # T+1: fills at the next tradable bar after the decision bar.
        assert fill.timestamp > fill.decision_timestamp
        assert fill.timestamp - fill.decision_timestamp == timedelta(days=1)
        # Filled at the next bar's OPEN with adverse 3 bps price impact.
        assert fill.fill_price == pytest.approx(fill.reference_price * (1 + 3 / 10_000), rel=1e-12)
        assert fill.slippage_cost == pytest.approx(fill.qty * fill.reference_price * 2 / 10_000, rel=1e-9)
        assert fill.spread_cost == pytest.approx(fill.qty * fill.reference_price * 1 / 10_000, rel=1e-9)
        # 25 bps taker fee on the fill notional.
        assert fill.fee == pytest.approx(fill.qty * fill.fill_price * 25 / 10_000, rel=1e-9)
        per_side_bps = (fill.slippage_cost + fill.spread_cost + fill.fee) / (fill.qty * fill.reference_price) * 10_000
        assert per_side_bps == pytest.approx(28.0, rel=1e-3)
    # The first rebalance targets the two lowest-variance names at 50/50.
    assert result.rebalances[0].target_weights == (
        ("LOVOL/USD", 0.5),
        ("MIDVOL/USD", 0.5),
    )
    assert "HIVOL/USD" not in {symbol for fill in result.fills for symbol in (fill.symbol,)}


def test_crypto_sleeve_per_asset_and_position_caps_in_backtest() -> None:
    """A tiny portfolio backtest: positions never exceed max_crypto_position_pct
    (0.05) per asset, total crypto exposure stays <= 0.10 and the position
    count stays <= 2 (maximum_crypto_positions)."""
    strategy = _strategy()
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=100_000.0,
        benchmark_symbol="BTC/USD",
        risk_limits=_risk_limits(),
        maximum_allocation=0.10,
    )
    bars = _crypto_bars(("LOVOL/USD", "MIDVOL/USD", "XRP/USD", "HIVOL/USD"))
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


def test_no_same_bar_execution_lookahead() -> None:
    """A decision on closed bar t may only fill on the NEXT tradable bar."""
    strategy = _strategy()
    engine = PortfolioBacktestEngine(strategy, rebalance_policy_for(strategy))
    result = engine.run(_crypto_bars(CRYPTO_SYMBOLS), liquidate_at_end=False)
    assert result.fills
    for fill in result.fills:
        assert fill.timestamp > fill.decision_timestamp


# ---------------------------------------------------------------------------
# Warmup and parameter contracts
# ---------------------------------------------------------------------------


def test_warmup_requires_23_bars() -> None:
    strategy = _strategy()
    assert strategy.minimum_history_bars == MIN_HISTORY_BARS
    short = {symbol: _flat_series(symbol, bars=MIN_HISTORY_BARS - 1) for symbol in CRYPTO_SYMBOLS}
    assert strategy.supports(_context(strategy, short)) is False
    ready = {symbol: _flat_series(symbol, bars=MIN_HISTORY_BARS) for symbol in CRYPTO_SYMBOLS}
    assert strategy.supports(_context(strategy, ready)) is True


def test_registration_spec_and_timeframe_validation() -> None:
    strategy = _strategy()
    assert isinstance(strategy, CryptoLowVariance1dV1)
    assert strategy.name == "crypto_low_variance_v1"
    assert strategy.market == Market.CRYPTO
    assert strategy.timeframe == "1d"
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
    # Explicit frozen parameter values surface in the portfolio output.
    frozen = _strategy(variance_lookback_days=22, top_n=2, rebalance_hours=168)
    assert frozen.parameter_tuple() == (
        ("rebalance_hours", 168),
        ("top_n", 2),
        ("variance_lookback_days", 22),
    )
    assert frozen.decide(_context(frozen, {"X/USD": _flat_series("X/USD")})).parameters == (
        ("rebalance_hours", 168),
        ("top_n", 2),
        ("variance_lookback_days", 22),
    )


def test_parameter_validation() -> None:
    with pytest.raises(ValueError, match="variance_lookback_days"):
        _strategy(variance_lookback_days=19)
    for bad_top_n in (0, 6):
        with pytest.raises(ValueError, match="top_n"):
            _strategy(top_n=bad_top_n)
    for bad_hours in (0, -168, 12, 100):
        with pytest.raises(ValueError, match="rebalance_hours"):
            _strategy(rebalance_hours=bad_hours)
    # Boundary values are accepted.
    _strategy(variance_lookback_days=20, top_n=5, rebalance_hours=336)
    # A lookback parameter must propagate into warmup and scoring.
    custom = _strategy(variance_lookback_days=20)
    assert custom.minimum_history_bars == 21
    closes = [100.0 if index % 2 == 0 else 101.0 for index in range(21)]
    context = _context(custom, {"CUST/USD": _manual_series("CUST/USD", closes)})
    assert custom.rank_candidates(context) == ("CUST/USD",)
    ((score, symbol),) = custom._score_candidates(context)
    assert symbol == "CUST/USD"
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
    assert len(returns) == 20
    assert score == pytest.approx(statistics.stdev(returns), rel=1e-12)
