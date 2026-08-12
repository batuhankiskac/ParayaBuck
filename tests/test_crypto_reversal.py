"""CryptoCrossSectionalReversal1dV1 contract tests.

Wave-2 correctness suite for ``crypto_cross_sectional_reversal_v1``: the
trailing-70-bar close-to-close signal over COMPLETED bars (decision bar
included), strict-negative qualification (positive/zero trailing returns are
NEVER bought, no matter the rank), ascending rank with symbol-name
tie-breaks, weekly hours:168 cadence, strategy-alpha observation-set
selection, risk-overlay caps and next-tradable-bar execution ordering.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioRiskLimits,
)
from parayabuck.portfolio import (
    CryptoCrossSectionalReversal1dV1,
    portfolio_strategy_from,
    rebalance_policy_for,
    validate_strategy_timeframe,
)
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
)
from parayabuck.universe.observation_set import build_observation_set

FORMATION_BARS = 70
MIN_HISTORY_BARS = FORMATION_BARS + 1
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


def _series(symbol: str, ret: float, bars: int = MIN_HISTORY_BARS) -> tuple[Bar, ...]:
    """``bars`` aligned 1d bars whose trailing-70-bar return equals ``ret``
    exactly: every formation close is flat at 100 and the decision bar closes
    at 100 * (1 + ret)."""
    closes = [100.0] * (bars - 1) + [100.0 * (1 + ret)]
    return _manual_series(symbol, closes)


def _context(
    strategy: CryptoCrossSectionalReversal1dV1, histories: dict[str, tuple[Bar, ...]]
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
        universe=None,
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


def _declining_crypto_bars(symbols: tuple[str, ...], days: int = 160) -> list[Bar]:
    """Aligned 1d crypto bars with a persistent downtrend: every symbol's
    trailing-70-bar return is negative, so the reversal qualifies and buys."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            close = 100.0 * (0.9995 - offset * 0.0005) ** index
            bars.append(_bar(symbol, timestamp, close))
    return bars


def _rising_equity_bars(symbols: tuple[str, ...], days: int = 320) -> list[Bar]:
    start = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            bars.append(
                _bar(
                    symbol,
                    timestamp,
                    100 * (1.001 + offset * 0.0001) ** index,
                    Market.EQUITY,
                )
            )
    return bars


class _Broker:
    def __init__(self) -> None:
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(100_000, 100_000, 100_000, 0.0)

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(symbol, market, 0.01, 0.01, 0.01, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


# ---------------------------------------------------------------------------
# Signal and ranking contracts
# ---------------------------------------------------------------------------


def test_most_negative_qualifying_return_ranks_first() -> None:
    """Known 70-bar trailing returns: most negative first, name tie-break,
    decide() keeps only the top-2 negatives at equal weight."""
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    history = {
        "AAA/USD": _series("AAA/USD", ret=-0.20),
        "EEE/USD": _series("EEE/USD", ret=-0.20),  # ties AAA: name tie-break
        "BBB/USD": _series("BBB/USD", ret=-0.05),
        "CCC/USD": _series("CCC/USD", ret=+0.20),
        "DDD/USD": _series("DDD/USD", ret=0.0),
    }
    context = _context(strategy, history)
    assert strategy.rank_candidates(context) == ("AAA/USD", "EEE/USD", "BBB/USD")
    target = strategy.decide(context)
    assert {weight.symbol for weight in target.weights} == {"AAA/USD", "EEE/USD"}
    assert all(weight.weight == pytest.approx(0.5) for weight in target.weights)
    assert target.cash_weight == pytest.approx(0.0)
    assert target.rebalance_reason == RebalanceReason.SCHEDULED


def test_positive_return_assets_never_bought_even_when_least_positive() -> None:
    """A positive-return symbol must NOT be selected just because it is the
    'least positive' of a full cross-section: only strictly negative trailing
    returns qualify."""
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    history = {
        "WIN1/USD": _series("WIN1/USD", ret=+0.30),
        "WIN2/USD": _series("WIN2/USD", ret=+0.20),
        "WIN3/USD": _series("WIN3/USD", ret=+0.10),  # 'least positive' never bought
    }
    context = _context(strategy, history)
    assert strategy.rank_candidates(context) == ()
    target = strategy.decide(context)
    assert target.weights == ()
    assert target.cash_weight == 1.0
    assert target.rebalance_reason == RebalanceReason.RISK_OFF


def test_formation_window_is_exactly_the_trailing_70_bars() -> None:
    """Only the close at closes[-71] anchors the ratio: flipping it flips the
    qualification, and the score equals closes[-1]/closes[-71] - 1 by
    independent arithmetic."""
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    # 71 flat closes -> trailing return 100/100 - 1 = 0: never selected.
    flat = _manual_series("FLIP/USD", [100.0] * MIN_HISTORY_BARS)
    assert strategy.rank_candidates(_context(strategy, {"FLIP/USD": flat})) == ()
    # Flip ONLY closes[-71] (the formation anchor) 100 -> 105, leaving the
    # decision-bar close at 100: the return becomes 100/105 - 1 < 0.
    flipped_closes = [105.0] + [100.0] * (MIN_HISTORY_BARS - 1)
    flipped = _manual_series("FLIP/USD", flipped_closes)
    context = _context(strategy, {"FLIP/USD": flipped})
    assert strategy.rank_candidates(context) == ("FLIP/USD",)
    scores = strategy._score_candidates(context)
    assert len(scores) == 1 and scores[0][1] == "FLIP/USD"
    expected = flipped_closes[-1] / flipped_closes[-71] - 1
    assert scores[0][0] == pytest.approx(expected, abs=1e-12)
    assert scores[0][0] == pytest.approx(100.0 / 105.0 - 1, abs=1e-12)
    assert strategy.decide(context).weight_for("FLIP/USD", Market.CRYPTO) == pytest.approx(1.0)


def test_signal_includes_the_decision_bar_close() -> None:
    """A 0-skip spec means the LAST completed bar (closes[-1]) is the signal
    point: the ratio must include it."""
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    # 70 flat formation closes then a final close of 95. If the decision
    # bar's close were skipped (closes[-2] vs closes[-72]) the return would
    # be 100/100 - 1 = 0 and the symbol would never qualify.
    closes = [100.0] * FORMATION_BARS + [95.0]
    history = {"LAST/USD": _manual_series("LAST/USD", closes)}
    context = _context(strategy, history)
    assert strategy.rank_candidates(context) == ("LAST/USD",)
    scores = strategy._score_candidates(context)
    assert scores[0][0] == pytest.approx(closes[-1] / closes[-71] - 1, abs=1e-12)
    assert scores[0][0] == pytest.approx(95.0 / 100.0 - 1, abs=1e-12)


# ---------------------------------------------------------------------------
# Cadence contract
# ---------------------------------------------------------------------------


def test_weekly_cadence_hours_168_epoch_blocks() -> None:
    """rebalance_policy_for yields RebalancePolicy(CRYPTO, hours:168) and
    is_period_start fires exactly on the first closed 1d bar of each 168h
    epoch block (a weekly decision)."""
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
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
# Portfolio output contracts
# ---------------------------------------------------------------------------


def test_all_cash_when_no_candidates_and_full_weight_when_one() -> None:
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    winners = {
        "UP1/USD": _series("UP1/USD", +0.10),
        "UP2/USD": _series("UP2/USD", +0.05),
    }
    target = strategy.decide(_context(strategy, winners))
    assert target.weights == ()
    assert target.cash_weight == 1.0
    assert target.rebalance_reason == RebalanceReason.RISK_OFF
    assert target.metadata["risk_off_detail"] == "no_qualified_losers"
    # One qualifier takes the whole (uncapped) target; the overlay caps it
    # downstream (covered by the backtest cap test).
    mixed = {
        "UP1/USD": _series("UP1/USD", +0.10),
        "DOWN/USD": _series("DOWN/USD", -0.25),
    }
    target = strategy.decide(_context(strategy, mixed))
    assert [weight.symbol for weight in target.weights] == ["DOWN/USD"]
    assert target.weight_for("DOWN/USD", Market.CRYPTO) == pytest.approx(1.0)
    assert target.cash_weight == pytest.approx(0.0)
    assert target.rebalance_reason == RebalanceReason.SCHEDULED
    assert target.metadata["selected_symbols"] == ("DOWN/USD",)


# ---------------------------------------------------------------------------
# Observation-set contracts
# ---------------------------------------------------------------------------


def test_observation_set_ranks_full_pool_and_keeps_top_alpha() -> None:
    """Pool larger than maximum_stream_symbols: the alpha runs on the FULL
    pool and the streamed observation set is the top-ranked subset."""
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    pool = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOT/USD")
    history = {
        "BTC/USD": _series("BTC/USD", -0.30),
        "ETH/USD": _series("ETH/USD", -0.25),
        "SOL/USD": _series("SOL/USD", -0.10),
        "XRP/USD": _series("XRP/USD", +0.20),
        "ADA/USD": _series("ADA/USD", +0.05),
        "DOT/USD": _series("DOT/USD", 0.0),
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
    assert observation.strategy_name == "crypto_cross_sectional_reversal_v1"
    assert observation.observation_set == ("BTC/USD", "ETH/USD", "SOL/USD")
    assert set(observation.observation_set) <= set(pool)
    assert len(observation.observation_set) <= 3
    # A top-2 decision symbol of the FULL pool is inside the stream.
    top2 = strategy.decide(_context(strategy, history))
    assert {weight.symbol for weight in top2.weights} <= set(observation.observation_set)


def test_managed_positions_remain_pinned_regardless_of_ranking() -> None:
    """Pinned symbols survive even when they never rank (positive return)."""
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    pool = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD")
    history = {
        "BTC/USD": _series("BTC/USD", -0.30),
        "ETH/USD": _series("ETH/USD", +0.20),  # non-ranking pin
        "SOL/USD": _series("SOL/USD", -0.10),
        "XRP/USD": _series("XRP/USD", +0.05),
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
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    pool = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOT/USD")
    history = {
        symbol: _series(symbol, ret)
        for symbol, ret in (
            ("BTC/USD", -0.30),
            ("ETH/USD", -0.25),
            ("SOL/USD", -0.20),
            ("XRP/USD", +0.10),
            ("ADA/USD", +0.05),
            ("DOT/USD", 0.0),
        )
    }
    # No pins, capacity 2, 3 ranked qualifiers: exactly min(3, 2) streamed.
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
    # One pin + capacity 3: pins + min(ranked, remaining) == 3.
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


def test_crypto_sleeve_per_asset_and_position_caps_in_backtest() -> None:
    """A tiny portfolio backtest: positions never exceed max_crypto_position_pct
    (0.05) per asset, total crypto exposure stays <= 0.10 and the position
    count stays <= 2 (maximum_crypto_positions)."""
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
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


def test_no_same_bar_execution_lookahead() -> None:
    """A decision on closed bar t may only fill on the NEXT tradable bar."""
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    engine = PortfolioBacktestEngine(strategy, rebalance_policy_for(strategy))
    bars = _declining_crypto_bars(CRYPTO_SYMBOLS)
    result = engine.run(bars, liquidate_at_end=False)
    assert result.fills
    for fill in result.fills:
        assert fill.timestamp > fill.decision_timestamp


def test_equity_and_crypto_1d_strategies_coexist_in_one_account(tmp_path) -> None:
    """Equity residual momentum (1d) and crypto reversal (1d) run at the same
    time, one account, one repository, without interfering."""
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "shared.sqlite3")
    equity = portfolio_strategy_from(
        "equity_residual_momentum_v1",
        {"top_n": 2, "exclude_symbols": ["SPY"]},
        timeframe="1d",
    )
    crypto = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    risk = _risk_limits()
    equity_runtime = PortfolioRuntime(
        equity,
        ("AAPL", "MSFT", "NVDA", "SPY"),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        risk_limits=risk,
    )
    crypto_runtime = PortfolioRuntime(
        crypto,
        CRYPTO_SYMBOLS,
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.10,
        risk_limits=risk,
        account_id="shared-account",
    )
    assert equity_runtime.strategy.timeframe == "1d"
    assert crypto_runtime.strategy.timeframe == "1d"
    assert crypto_runtime.policy.frequency == "hours:168"
    equity_result = equity_runtime.process_closed_bars(
        _rising_equity_bars(("AAPL", "MSFT", "NVDA", "SPY"))
    )
    assert equity_result.status == "submitted"
    crypto_result = crypto_runtime.process_closed_bars(_declining_crypto_bars(CRYPTO_SYMBOLS))
    assert crypto_result.status == "submitted"
    submitted_symbols = {order.symbol for order in broker.submitted}
    assert any("/" in symbol for symbol in submitted_symbols)
    assert any("/" not in symbol for symbol in submitted_symbols)
    equity_runtime.close()
    crypto_runtime.close()


def test_restart_does_not_duplicate_orders(tmp_path) -> None:
    """A restart on the same repository resumes the decision boundary: the
    same closed-bar batch yields duplicate_decision and NO new orders."""
    repository = PortfolioStateRepository(tmp_path / "shared.sqlite3")
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    bars = _declining_crypto_bars(CRYPTO_SYMBOLS)
    broker1 = _Broker()
    runtime1 = PortfolioRuntime(
        strategy,
        CRYPTO_SYMBOLS,
        broker1,
        repository,
        mode="paper",
        maximum_allocation=0.10,
        risk_limits=_risk_limits(),
    )
    result1 = runtime1.process_closed_bars(bars)
    assert result1.status == "submitted"
    assert broker1.submitted
    runtime1.close()

    broker2 = _Broker()
    runtime2 = PortfolioRuntime(
        strategy,
        CRYPTO_SYMBOLS,
        broker2,
        repository,
        mode="paper",
        maximum_allocation=0.10,
        risk_limits=_risk_limits(),
    )
    result2 = runtime2.process_closed_bars(bars)
    assert result2.status == "duplicate_decision"
    assert broker2.submitted == []
    runtime2.close()


# ---------------------------------------------------------------------------
# Warmup and registration contracts
# ---------------------------------------------------------------------------


def test_warmup_requires_71_bars() -> None:
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    assert strategy.minimum_history_bars == MIN_HISTORY_BARS
    short = {symbol: _series(symbol, -0.10, bars=70) for symbol in CRYPTO_SYMBOLS}
    assert strategy.supports(_context(strategy, short)) is False
    ready = {symbol: _series(symbol, -0.10, bars=71) for symbol in CRYPTO_SYMBOLS}
    assert strategy.supports(_context(strategy, ready)) is True


def test_registration_spec_and_timeframe_validation() -> None:
    strategy = portfolio_strategy_from("crypto_cross_sectional_reversal_v1")
    assert isinstance(strategy, CryptoCrossSectionalReversal1dV1)
    assert strategy.name == "crypto_cross_sectional_reversal_v1"
    assert strategy.market == Market.CRYPTO
    assert strategy.timeframe == "1d"
    assert strategy.supported_timeframes == ("1d",)
    assert strategy.default_rebalance_hours == 168
    assert strategy.rebalance_policy_declared == "hours:168"
    # '1h' is not a validated variant of this single-timeframe strategy.
    with pytest.raises(ValueError, match="cannot run on timeframe 1h"):
        portfolio_strategy_from("crypto_cross_sectional_reversal_v1", timeframe="1h")
    for bad in ("5m", "15m"):
        with pytest.raises(ValueError):
            portfolio_strategy_from("crypto_cross_sectional_reversal_v1", timeframe=bad)
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
