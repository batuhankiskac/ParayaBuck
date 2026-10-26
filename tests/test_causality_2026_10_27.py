"""Causality invariants for the leveraged-trend strategy and the portfolio engine.

Everything runs on a seeded synthetic QQQ/QLD/SPY daily history: no network,
no committed data, no runtime state. Two invariants are pinned:

* a fill, order, decision or equity point stamped at or before bar k+1 never
  depends on bars strictly after k+1 (the engine fills a decision at the next
  bar's open, so bar k+1 is the last bar a fill from decision k may read);
* the strategy's decision at session d is the same whether the history is
  truncated at d or carries garbage after d, and it equals the decision the
  full-history engine run recorded for d.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import RebalancePolicy

START = datetime(2024, 1, 1, 21, 0, tzinfo=UTC)
SESSIONS = 700
# The first session the 397-bar warmup (SMA200, 252-session momentum, the R2
# replay) lets the shipped E2 + R2 rule decide.
FIRST_DECIDABLE = 396
SEED = 20261027
VOLUME = 5_000_000.0

PARAMETERS = {
    "signal_symbol": "QQQ",
    "hold_symbol": "QLD",
    "sma_bars": 200,
    "vol_target": 0.35,
    "vol_window": 20,
    "rebalance_frequency": "hours:24",
    "trend_rule": "majority_sma_tsm_don",
    "tsm_bars": 252,
    "donchian_bars": 126,
    "early_reentry_weight": 0.5,
}

# (exclusive end index, daily drift, daily sigma): an uptrend, a crash, a calm
# recovery below the channel midpoint, a dip through SMA50, a rally, a second
# decline and a final recovery, so the 2-of-3 vote and R2 both switch.
_REGIMES = (
    (300, 0.0009, 0.004),
    (340, -0.012, 0.006),
    (430, 0.0025, 0.003),
    (445, -0.005, 0.003),
    (561, 0.0035, 0.003),
    (621, -0.0055, 0.004),
    (SESSIONS, 0.0025, 0.003),
)

# Cut indices k for the causal fill test. In the baseline run the engine fills
# at bars 397, 404, 420, 435, 461, 610, 643 and 649 (the decisions at k = bar-1
# are the ones that trade), so each k below has a fill at k+1 and the cut is
# not vacuous (the test asserts this too).
CUT_INDICES = (396, 403, 419, 434, 460, 609, 642, 648)

# Sessions at which the lookahead test compares decide() with the recorded
# full-history decision; spread across the warmup end and every regime.
DECISION_INDICES = (396, 420, 437, 470, 500, 530, 560, 570, 600, 615, 630, 650, 680, 698)
# Sessions for the engine-level garbage check (one full re-run each).
GARBAGE_INDICES = (396, 437, 530, 610, 650)


def _market_bars() -> list[Bar]:
    """Daily QQQ, QLD (2x QQQ) and SPY bars with valid OHLC and a regime path."""
    rng = random.Random(SEED)
    qqq_closes = [100.0]
    qqq_returns = [0.0]
    for index in range(1, SESSIONS):
        drift, sigma = next((d, s) for end, d, s in _REGIMES if index < end)
        step = drift + sigma * rng.gauss(0.0, 1.0)
        qqq_returns.append(step)
        qqq_closes.append(qqq_closes[-1] * (1.0 + step))
    closes = {"QQQ": qqq_closes}
    for symbol, start, beta, noise in (("QLD", 50.0, 2.0, 0.002), ("SPY", 400.0, 0.9, 0.001)):
        series = [start]
        for index in range(1, SESSIONS):
            step = beta * qqq_returns[index] + noise * rng.gauss(0.0, 1.0)
            series.append(series[-1] * (1.0 + step))
        closes[symbol] = series

    bars: list[Bar] = []
    for symbol, series in closes.items():
        wick_rng = random.Random(f"{SEED}:{symbol}")
        previous = series[0]
        for index, close in enumerate(series):
            open_ = previous * (1.0 + 0.001 * wick_rng.gauss(0.0, 1.0)) if index else close
            high = max(open_, close) * (1.0 + abs(0.002 * wick_rng.gauss(0.0, 1.0)))
            low = min(open_, close) * (1.0 - abs(0.002 * wick_rng.gauss(0.0, 1.0)))
            bars.append(
                Bar(START + timedelta(days=index), symbol, "1d", open_, high, low, close, VOLUME, Market.EQUITY)
            )
            previous = close
    return bars


def _session_timestamps(bars: list[Bar]) -> list[datetime]:
    return sorted({bar.timestamp for bar in bars})


def _scaled_after(bars: list[Bar], boundary: datetime, seed: int) -> list[Bar]:
    """Multiply every bar strictly after ``boundary`` by its own random factor.

    All four prices of a bar share one factor, so OHLC stays valid.
    """
    rng = random.Random(seed)
    out: list[Bar] = []
    for bar in bars:
        if bar.timestamp <= boundary:
            out.append(bar)
            continue
        factor = rng.uniform(0.2, 5.0)
        out.append(
            Bar(
                bar.timestamp,
                bar.symbol,
                bar.timeframe,
                bar.open * factor,
                bar.high * factor,
                bar.low * factor,
                bar.close * factor,
                bar.volume,
                bar.market,
            )
        )
    return out


def _strategy():  # type: ignore[no-untyped-def]
    return portfolio_strategy_from("equity_leveraged_trend_v1", dict(PARAMETERS))


def _engine(strategy) -> PortfolioBacktestEngine:  # type: ignore[no-untyped-def]
    return PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(Market.EQUITY, "hours:24"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=2.0,
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        turnover_band=0.005,
        minimum_order_notional=10.0,
        maximum_allocation=0.98,
        allow_static_universe=True,
    )


def _run(bars: list[Bar]):  # type: ignore[no-untyped-def]
    return _engine(_strategy()).run(list(bars))


def _observable_up_to(result, boundary: datetime):  # type: ignore[no-untyped-def]
    """Every fill, decision, weight and equity point stamped at or before ``boundary``.

    Decision-side fields only: a rebalance record's realized turnover and cost
    are enriched from the fills that follow it, so they legitimately read the
    bar after the boundary and are excluded.
    """
    fills = tuple(
        (
            fill.timestamp,
            fill.symbol,
            fill.side.value,
            fill.qty,
            fill.fill_price,
            fill.slippage_cost,
            fill.spread_cost,
            fill.fee,
            fill.decision_timestamp,
            fill.client_order_id,
        )
        for fill in result.fills
        if fill.timestamp <= boundary
    )
    curve = tuple(
        (timestamp, equity)
        for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True)
        if timestamp <= boundary
    )
    decisions = tuple(
        (
            record.decision_timestamp,
            record.reason,
            record.target_weights,
            record.target_cash_weight,
            record.equity_at_decision,
            record.rebalance_number,
        )
        for record in result.rebalances
        if record.decision_timestamp <= boundary
    )
    weights = tuple(entry for entry in result.weight_history if entry[0] <= boundary)
    return fills, curve, decisions, weights


@pytest.fixture(scope="module")
def market_and_reference():  # type: ignore[no-untyped-def]
    bars = _market_bars()
    return bars, _run(bars)


def test_synthetic_run_trades_so_the_causal_checks_are_not_vacuous(market_and_reference) -> None:
    bars, reference = market_and_reference
    timestamps = _session_timestamps(bars)
    assert len(timestamps) == SESSIONS
    assert len(reference.fills) >= 20
    assert {fill.symbol for fill in reference.fills} == {"QLD"}
    filled_boundaries = {fill.timestamp for fill in reference.fills}
    for cut in CUT_INDICES:
        assert timestamps[cut + 1] in filled_boundaries, f"no fill at bar k+1 for cut {cut}"


@pytest.mark.parametrize("cut", CUT_INDICES)
def test_fills_orders_and_equity_up_to_bar_k_plus_one_ignore_later_bars(market_and_reference, cut: int) -> None:
    bars, reference = market_and_reference
    boundary = _session_timestamps(bars)[cut + 1]
    mutated = _scaled_after(bars, boundary, seed=cut)
    assert mutated != bars
    observed = _run(mutated)
    assert _observable_up_to(observed, boundary) == _observable_up_to(reference, boundary)


def _context(bars: list[Bar], decision_timestamp: datetime, *, truncate: bool) -> PortfolioContext:
    """A decision context over ``bars``; ``truncate`` drops bars after the decision."""
    history: dict[str, tuple[Bar, ...]] = {}
    for symbol in ("QQQ", "QLD", "SPY"):
        series = [bar for bar in bars if bar.symbol == symbol and (not truncate or bar.timestamp <= decision_timestamp)]
        history[symbol] = tuple(series)
    return PortfolioContext(
        decision_timestamp=decision_timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=None,
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )


def _summary(target) -> tuple[tuple[tuple[str, float], ...], float, str]:  # type: ignore[no-untyped-def]
    weights = tuple((weight.symbol, weight.weight) for weight in target.weights)
    return weights, target.cash_weight, target.metadata["trend_state"]


def _recorded_run(bars: list[Bar]) -> dict[datetime, object]:
    """Engine run with every ``decide()`` output recorded by decision timestamp."""
    strategy = _strategy()
    decided: dict[datetime, object] = {}
    original = strategy.decide

    def recording(context: PortfolioContext):  # type: ignore[no-untyped-def]
        target = original(context)
        decided[context.decision_timestamp] = target
        return target

    strategy.decide = recording
    _engine(strategy).run(list(bars))
    return decided


@pytest.fixture(scope="module")
def recorded_decisions(market_and_reference):  # type: ignore[no-untyped-def]
    bars, _ = market_and_reference
    return _recorded_run(bars)


def test_truncated_history_reproduces_the_recorded_full_run_decision(
    market_and_reference, recorded_decisions
) -> None:
    bars, _ = market_and_reference
    timestamps = _session_timestamps(bars)
    states = set()
    for index in DECISION_INDICES:
        decision_timestamp = timestamps[index]
        truncated = _strategy().decide(_context(bars, decision_timestamp, truncate=True))
        recorded = recorded_decisions[decision_timestamp]
        assert _summary(truncated) == _summary(recorded), f"session {index}"
        states.add(truncated.metadata["trend_state"])
    # Non-vacuous: the sample spans more than one trend state.
    assert len(states) >= 3, states


def test_full_run_decision_is_unchanged_when_bars_after_it_are_garbage(
    market_and_reference, recorded_decisions
) -> None:
    """Replace every bar after d with garbage and re-run the engine.

    The engine must hand the strategy only bars stamped at or before d, so the
    decision recorded for d cannot move. (A context that carries garbage bars
    with ``decision_timestamp=d`` is not a valid engine input and is not used:
    the strategy reads the last bar of its history as the decision bar.)
    """
    bars, _ = market_and_reference
    timestamps = _session_timestamps(bars)
    for index in GARBAGE_INDICES:
        decision_timestamp = timestamps[index]
        garbled = _recorded_run(_scaled_after(bars, decision_timestamp, seed=1000 + index))
        assert _summary(garbled[decision_timestamp]) == _summary(recorded_decisions[decision_timestamp]), (
            f"session {index}"
        )
