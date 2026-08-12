"""CryptoLowDisagreement1dV1 contract tests.

Wave-2 correctness suite for ``crypto_low_disagreement_v1``: the GKM
50-day-interval abnormal-volume ratio over COMPLETED bars (formation 5d vs
disjoint reference 45d, nonzero-only means, decision bar EXCLUDED),
zero-volume gates (a no-trade day NEVER becomes the low-volume buy signal),
ascending rank with symbol-name tie-breaks, weekly hours:168 cadence,
strategy-alpha observation-set selection, risk-overlay caps and
next-tradable-bar execution ordering at 28 bps/side (25 taker + 2 slippage +
1 half-spread).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio import rebalance_policy_for, validate_strategy_timeframe
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_low_disagreement import CryptoLowDisagreement1dV1
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason
from parayabuck.universe.observation_set import build_observation_set

VOLUME_SHORT_DAYS = 5
VOLUME_LONG_DAYS = 45
MIN_NONZERO_SHORT = 4
MIN_NONZERO_LONG = 40
MIN_HISTORY_BARS = 51
CRYPTO_SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD")


def _bar(
    symbol: str,
    timestamp: datetime,
    close: float,
    volume: float = 1_000_000.0,
    market: Market = Market.CRYPTO,
) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        market=market,
    )


def _manual_series(
    symbol: str,
    closes: list[float],
    volumes: list[float] | None = None,
    start: datetime | None = None,
) -> tuple[Bar, ...]:
    start = start or datetime(2024, 1, 1, tzinfo=UTC)
    if volumes is None:
        volumes = [1_000_000.0] * len(closes)
    return tuple(
        _bar(symbol, start + timedelta(days=index), close, volume)
        for index, (close, volume) in enumerate(zip(closes, volumes, strict=True))
    )


def _series(symbol: str, ratio: float, bars: int = MIN_HISTORY_BARS) -> tuple[Bar, ...]:
    """``bars`` aligned 1d bars whose formation/reference volume ratio equals
    ``ratio`` exactly: 45 reference bars at 1e6, 5 formation bars at 1e6 *
    ratio, decision bar at 1e6 (its volume never enters the signal)."""
    volumes = [1_000_000.0] * (bars - 6) + [1_000_000.0 * ratio] * 5 + [1_000_000.0]
    return _manual_series(symbol, [100.0] * bars, volumes)


def _context(
    strategy: CryptoLowDisagreement1dV1,
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


class _Universe:
    def __init__(self, members: set[str]) -> None:
        self._members = set(members)

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return symbol in self._members


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


def _declining_crypto_bars(symbols: tuple[str, ...], days: int = 160) -> list[Bar]:
    """Aligned 1d crypto bars with a persistent downtrend and nonzero volume:
    every symbol qualifies with a 1.0 abnormal-volume ratio (all bars at the
    same volume), so the strategy buys the top-2 by name tie-break."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
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


def test_ratio_is_formation_mean_over_reference_mean_nonzero_only() -> None:
    """Hand-computed frozen formula: ratio = mean(volume t-5..t-1, nonzero) /
    mean(volume t-50..t-6, nonzero). Zero bars are EXCLUDED from the means
    and the gates pass at exactly their boundary (4/5 and 40/45 nonzero)."""
    strategy = CryptoLowDisagreement1dV1()
    # Reference: 40 bars at 2000 + 5 bars at 0 -> 40 nonzero, mean 2000.
    # Formation: [0, 100, 200, 300, 400] -> 4 nonzero, mean 250.
    # ratio = 250 / 2000 = 0.125; the zeros must not drag the means down.
    volumes = [2000.0] * 40 + [0.0] * 5 + [0.0, 100.0, 200.0, 300.0, 400.0] + [1_000_000.0]
    series = _manual_series("VOL/USD", [100.0] * MIN_HISTORY_BARS, volumes)
    context = _context(strategy, {"VOL/USD": series})
    assert strategy.rank_candidates(context) == ("VOL/USD",)
    scores = strategy._score_candidates(context)
    assert len(scores) == 1 and scores[0][1] == "VOL/USD"
    assert scores[0][0] == pytest.approx(250.0 / 2000.0, abs=1e-12)
    assert scores[0][0] == pytest.approx(0.125, abs=1e-12)
    assert strategy.decide(context).weight_for("VOL/USD", Market.CRYPTO) == pytest.approx(1.0)


def test_zero_volume_days_never_produce_a_buy_signal() -> None:
    """Zero-volume bars can never manufacture the low-volume signal: a symbol
    dominated by no-trade days fails the min-nonzero gates no matter how
    small its (excluded) volumes would make the ratio."""
    strategy = CryptoLowDisagreement1dV1()
    # Formation has only 3 of 5 nonzero bars -> below min_nonzero_short.
    volumes = [1_000_000.0] * 45 + [0.0, 0.0, 100.0, 200.0, 300.0] + [1_000_000.0]
    series = _manual_series("FEW/USD", [100.0] * MIN_HISTORY_BARS, volumes)
    assert strategy.rank_candidates(_context(strategy, {"FEW/USD": series})) == ()
    # Reference has only 39 of 45 nonzero bars -> below min_nonzero_long.
    volumes = [1_000_000.0] * 39 + [0.0] * 6 + [1_000_000.0] * 5 + [1_000_000.0]
    series = _manual_series("REF/USD", [100.0] * MIN_HISTORY_BARS, volumes)
    assert strategy.rank_candidates(_context(strategy, {"REF/USD": series})) == ()
    # All-zero history: a 0/0 ratio would be meaningless; never a signal.
    volumes = [0.0] * MIN_HISTORY_BARS
    series = _manual_series("ZERO/USD", [100.0] * MIN_HISTORY_BARS, volumes)
    assert strategy.rank_candidates(_context(strategy, {"ZERO/USD": series})) == ()
    # Only the decision bar trades: the signal windows are all no-trade days.
    volumes = [0.0] * (MIN_HISTORY_BARS - 1) + [1_000_000_000.0]
    series = _manual_series("ONE/USD", [100.0] * MIN_HISTORY_BARS, volumes)
    assert strategy.rank_candidates(_context(strategy, {"ONE/USD": series})) == ()


def test_decision_bar_volume_never_enters_the_ratio() -> None:
    """The signal is decided on completed bars t-50..t-1: the decision bar's
    own volume (zero or astronomically large) must not change the score."""
    strategy = CryptoLowDisagreement1dV1()
    base = _series("DEC/USD", ratio=0.5)
    context = _context(strategy, {"DEC/USD": base})
    scores = strategy._score_candidates(context)
    assert len(scores) == 1 and scores[0][1] == "DEC/USD"
    # Hand-computed from bars[-6:-1] (5 bars at 500_000) and bars[-51:-6]
    # (45 bars at 1_000_000): 500_000 / 1_000_000 = 0.5.
    assert scores[0][0] == pytest.approx(500_000.0 / 1_000_000.0, abs=1e-12)
    bars = list(base)
    decision = bars[-1]
    # 1000x the decision bar's volume: score unchanged.
    bars[-1] = _bar(decision.symbol, decision.timestamp, decision.close, 1_000_000_000.0)
    boosted = _context(strategy, {"DEC/USD": tuple(bars)})
    assert strategy._score_candidates(boosted)[0][0] == pytest.approx(scores[0][0], abs=1e-12)
    # Zero decision-bar volume: score unchanged (the bar is outside both windows).
    bars[-1] = _bar(decision.symbol, decision.timestamp, decision.close, 0.0)
    zeroed = _context(strategy, {"DEC/USD": tuple(bars)})
    assert strategy._score_candidates(zeroed)[0][0] == pytest.approx(scores[0][0], abs=1e-12)


def test_lowest_ratio_ranks_first_with_symbol_name_tie_break() -> None:
    """Ascending ratio (LOWEST abnormal volume first); ties broken by
    ascending symbol name; decide() keeps the top-2 at equal weight."""
    strategy = CryptoLowDisagreement1dV1()
    history = {
        "AAA/USD": _series("AAA/USD", ratio=0.25),
        "EEE/USD": _series("EEE/USD", ratio=0.25),  # ties AAA: name tie-break
        "BBB/USD": _series("BBB/USD", ratio=2.0),
        "CCC/USD": _series("CCC/USD", ratio=10.0),
    }
    context = _context(strategy, history)
    assert strategy.rank_candidates(context) == ("AAA/USD", "EEE/USD", "BBB/USD", "CCC/USD")
    target = strategy.decide(context)
    assert {weight.symbol for weight in target.weights} == {"AAA/USD", "EEE/USD"}
    assert all(weight.weight == pytest.approx(0.5) for weight in target.weights)
    assert target.cash_weight == pytest.approx(0.0)
    assert target.rebalance_reason == RebalanceReason.SCHEDULED


def test_rank_candidates_and_decide_share_exact_scoring() -> None:
    """rank_candidates and decide run through the same _score_candidates:
    the top-2 decision set is exactly the first two of the ranked order."""
    strategy = CryptoLowDisagreement1dV1()
    history = {
        "B/USD": _series("B/USD", ratio=1.5),
        "D/USD": _series("D/USD", ratio=0.8),
        "A/USD": _series("A/USD", ratio=0.5),
        "C/USD": _series("C/USD", ratio=3.0),
    }
    context = _context(strategy, history)
    ranked = strategy.rank_candidates(context)
    scores = strategy._score_candidates(context)
    # rank_candidates is exactly the symbol projection of _score_candidates.
    assert tuple(symbol for _score, symbol in scores) == ranked
    # The scores themselves are ascending (lowest ratio first).
    assert [score for score, _symbol in scores] == sorted(score for score, _symbol in scores)
    assert ranked == ("A/USD", "D/USD", "B/USD", "C/USD")
    target = strategy.decide(context)
    assert {weight.symbol for weight in target.weights} == set(ranked[:2]) == {"A/USD", "D/USD"}


# ---------------------------------------------------------------------------
# Qualification contracts
# ---------------------------------------------------------------------------


def test_qualification_requires_pool_membership_and_full_history() -> None:
    """PIT pool membership (in_universe) and the 51-bar warmup are hard
    gates: a non-member or a 50-bar symbol never qualifies even with a tiny
    ratio."""
    strategy = CryptoLowDisagreement1dV1()
    # 50 completed bars: one short of min_history_days 51.
    short = _manual_series(
        "SHORT/USD",
        [100.0] * (MIN_HISTORY_BARS - 1),
        volumes=[1_000_000.0] * (MIN_HISTORY_BARS - 1),
    )
    assert strategy.rank_candidates(_context(strategy, {"SHORT/USD": short})) == ()
    assert strategy.decide(_context(strategy, {"SHORT/USD": short})).weights == ()
    # Non-pool member: skipped by in_universe even with a qualifying ratio.
    out = _series("OUT/USD", ratio=0.1)
    universe = _Universe({"IN/USD"})
    context = _context(strategy, {"OUT/USD": out, "IN/USD": _series("IN/USD", ratio=0.1)}, universe)
    assert strategy.rank_candidates(context) == ("IN/USD",)
    # Same series, in-pool: qualifies.
    context = _context(strategy, {"OUT/USD": out}, _Universe({"OUT/USD"}))
    assert strategy.rank_candidates(context) == ("OUT/USD",)


def test_all_cash_when_no_qualifiers_and_full_weight_when_one() -> None:
    strategy = CryptoLowDisagreement1dV1()
    # Everyone fails the nonzero gates -> 100% cash RISK_OFF.
    empty = {
        "UP1/USD": _manual_series(
            "UP1/USD", [100.0] * MIN_HISTORY_BARS, volumes=[0.0] * MIN_HISTORY_BARS
        ),
        "UP2/USD": _manual_series(
            "UP2/USD", [100.0] * MIN_HISTORY_BARS, volumes=[0.0] * MIN_HISTORY_BARS
        ),
    }
    target = strategy.decide(_context(strategy, empty))
    assert target.weights == ()
    assert target.cash_weight == 1.0
    assert target.rebalance_reason == RebalanceReason.RISK_OFF
    assert target.metadata["risk_off_detail"] == "no_qualified_low_volume"
    # One qualifier takes the whole (uncapped) target; the overlay caps it
    # downstream (covered by the backtest cap test).
    mixed = {
        "ZERO/USD": _manual_series(
            "ZERO/USD", [100.0] * MIN_HISTORY_BARS, volumes=[0.0] * MIN_HISTORY_BARS
        ),
        "LOW/USD": _series("LOW/USD", ratio=0.5),
    }
    target = strategy.decide(_context(strategy, mixed))
    assert [weight.symbol for weight in target.weights] == ["LOW/USD"]
    assert target.weight_for("LOW/USD", Market.CRYPTO) == pytest.approx(1.0)
    assert target.cash_weight == pytest.approx(0.0)
    assert target.rebalance_reason == RebalanceReason.SCHEDULED
    assert target.metadata["selected_symbols"] == ("LOW/USD",)


# ---------------------------------------------------------------------------
# Cadence contract
# ---------------------------------------------------------------------------


def test_weekly_cadence_hours_168_epoch_blocks() -> None:
    """rebalance_policy_for yields RebalancePolicy(CRYPTO, hours:168) and
    is_period_start fires exactly on the first closed 1d bar of each 168h
    epoch block (a weekly decision)."""
    strategy = CryptoLowDisagreement1dV1()
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
    strategy = CryptoLowDisagreement1dV1()
    pool = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOT/USD")
    history = {
        "BTC/USD": _series("BTC/USD", 0.1),
        "ETH/USD": _series("ETH/USD", 0.2),
        "SOL/USD": _series("SOL/USD", 0.3),
        "XRP/USD": _series("XRP/USD", 5.0),
        "ADA/USD": _series("ADA/USD", 2.0),
        "DOT/USD": _series("DOT/USD", 1.0),
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
    assert observation.strategy_name == "crypto_low_disagreement_v1"
    assert observation.observation_set == ("BTC/USD", "ETH/USD", "SOL/USD")
    assert set(observation.observation_set) <= set(pool)
    assert len(observation.observation_set) <= 3
    # A top-2 decision symbol of the FULL pool is inside the stream.
    top2 = strategy.decide(_context(strategy, history))
    assert {weight.symbol for weight in top2.weights} <= set(observation.observation_set)


def test_managed_positions_remain_pinned_regardless_of_ranking() -> None:
    """Pinned symbols survive even when they never rank (high ratio)."""
    strategy = CryptoLowDisagreement1dV1()
    pool = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD")
    history = {
        "BTC/USD": _series("BTC/USD", 0.1),
        "ETH/USD": _series("ETH/USD", 5.0),  # non-ranking pin
        "SOL/USD": _series("SOL/USD", 0.2),
        "XRP/USD": _series("XRP/USD", 2.0),
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


# ---------------------------------------------------------------------------
# Engine / runtime contracts
# ---------------------------------------------------------------------------


def test_engine_fills_at_next_bar_open_with_28bps_one_way_cost() -> None:
    """A decision on closed bar t may only fill on the NEXT tradable bar at
    that bar's open; with crypto_taker_fee_bps=25, slippage_bps=2 and
    spread_bps=2 (half-spread 1), every one-way fill costs 28 bps of its
    filled notional."""
    strategy = CryptoLowDisagreement1dV1()
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=100_000.0,
        benchmark_symbol="BTC/USD",
        crypto_taker_fee_bps=25.0,
        slippage_bps=2.0,
        spread_bps=2.0,
    )
    bars = _declining_crypto_bars(CRYPTO_SYMBOLS)
    result = engine.run(bars, liquidate_at_end=False)
    assert result.fills
    open_by_key = {(bar.symbol, bar.timestamp): bar.open for bar in bars}
    adverse_bps = 2.0 + 2.0 / 2  # 3 bps adverse on the fill price
    for fill in result.fills:
        # T+1: never the same bar as the decision.
        assert fill.timestamp > fill.decision_timestamp
        # Fill at the NEXT bar's open, adjusted by 3 bps adverse.
        assert (fill.symbol, fill.timestamp) in open_by_key
        side = 1.0 if fill.side == OrderSide.BUY else -1.0
        expected_price = open_by_key[(fill.symbol, fill.timestamp)] * (
            1 + side * adverse_bps / 10_000
        )
        assert fill.fill_price == pytest.approx(expected_price, rel=1e-12)
        # 25 taker + 2 slippage + 1 half-spread = 28 bps one-way cost.
        one_way_cost = fill.slippage_cost + fill.spread_cost + fill.fee
        assert one_way_cost / (fill.qty * fill.fill_price) == pytest.approx(28.0 / 10_000, rel=1e-3)


def test_crypto_sleeve_per_asset_and_position_caps_in_backtest() -> None:
    """A tiny portfolio backtest: positions never exceed max_crypto_position_pct
    (0.05) per asset, total crypto exposure stays <= 0.10 and the position
    count stays <= 2 (maximum_crypto_positions)."""
    strategy = CryptoLowDisagreement1dV1()
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
# Warmup and contract metadata
# ---------------------------------------------------------------------------


def test_warmup_requires_51_bars() -> None:
    strategy = CryptoLowDisagreement1dV1()
    assert strategy.minimum_history_bars == MIN_HISTORY_BARS
    short = {symbol: _series(symbol, 0.5, bars=50) for symbol in CRYPTO_SYMBOLS}
    assert strategy.supports(_context(strategy, short)) is False
    ready = {symbol: _series(symbol, 0.5, bars=51) for symbol in CRYPTO_SYMBOLS}
    assert strategy.supports(_context(strategy, ready)) is True


def test_spec_and_timeframe_validation() -> None:
    strategy = CryptoLowDisagreement1dV1()
    assert isinstance(strategy, CryptoLowDisagreement1dV1)
    assert strategy.name == "crypto_low_disagreement_v1"
    assert strategy.version == "1.0.0"
    assert strategy.market == Market.CRYPTO
    assert strategy.timeframe == "1d"
    assert strategy.supported_timeframes == ("1d",)
    assert strategy.default_rebalance_hours == 168
    assert strategy.rebalance_policy_declared == "hours:168"
    # '1h' is not a validated variant of this single-timeframe strategy.
    assert strategy.supports_timeframe("1d") is True
    assert strategy.supports_timeframe("1h") is False
    with pytest.raises(ValueError, match="not supported"):
        validate_strategy_timeframe(strategy, "1h")
    validate_strategy_timeframe(strategy, "1d")
    spec = strategy.spec()
    assert spec.primary_timeframe == "1d"
    assert spec.supported_timeframes == ("1d",)
    assert spec.rebalance_policy == "hours:168"
    assert spec.market == Market.CRYPTO
    assert spec.minimum_history_bars == MIN_HISTORY_BARS
    assert rebalance_policy_for(strategy) == RebalancePolicy(Market.CRYPTO, "hours:168")


@pytest.mark.parametrize(
    ("parameters", "match"),
    [
        ({"volume_short_days": 2}, "volume_short_days must be at least 3"),
        ({"volume_long_days": 10}, "volume_long_days must be at least 20"),
        ({"min_history_days": 50}, "min_history_days must be at least"),
        ({"min_nonzero_short": 6}, "min_nonzero_short must be between"),
        ({"min_nonzero_short": 0}, "min_nonzero_short must be between"),
        ({"min_nonzero_long": 46}, "min_nonzero_long must be between"),
        ({"min_nonzero_long": 0}, "min_nonzero_long must be between"),
        ({"top_n": 6}, "top_n must be between 1 and 5"),
        ({"top_n": 0}, "top_n must be between 1 and 5"),
        ({"rebalance_hours": 100}, "rebalance_hours must be a positive multiple of 24"),
        ({"rebalance_hours": 0}, "rebalance_hours must be a positive multiple of 24"),
        ({"rebalance_hours": -24}, "rebalance_hours must be a positive multiple of 24"),
    ],
)
def test_parameter_validation(parameters: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        CryptoLowDisagreement1dV1(parameters)


def test_valid_custom_parameters_accepted() -> None:
    strategy = CryptoLowDisagreement1dV1(
        {
            "volume_short_days": 3,
            "volume_long_days": 20,
            "min_nonzero_short": 1,
            "min_nonzero_long": 1,
            "min_history_days": 24,
            "top_n": 1,
            "rebalance_hours": 24,
        }
    )
    assert strategy.minimum_history_bars == 24
    assert rebalance_policy_for(strategy) == RebalancePolicy(Market.CRYPTO, "hours:24")
