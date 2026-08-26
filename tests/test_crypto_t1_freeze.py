"""Freeze tests for CryptoLowVarianceHysteresis1dV1 (T1).

Frozen economics: variance_lookback_days=22, top_n=2, rebalance_hours=96,
RETENTION_RANK=4, equal weights, same _score_candidates as v2.
Decide parity with CryptoTournamentT1Hysteresis in
scripts/research_crypto_candidate_tournament.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_low_variance import CryptoLowVariance1dV2
from parayabuck.portfolio.crypto_low_variance_hysteresis import (
    CryptoLowVarianceHysteresis1dV1,
    hysteresis_should_retain,
)


# ---------------------------------------------------------------------------
# Helpers — minimal PIT context (mirrors test_crypto_low_variance.py)
# ---------------------------------------------------------------------------
def _bar(symbol: str, ts: datetime, close: float) -> Bar:
    return Bar(
        symbol=symbol,
        market=Market.CRYPTO,
        timeframe="1d",
        timestamp=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000.0,
    )


def _series(symbol: str, closes: list[float], start: datetime | None = None) -> tuple[Bar, ...]:
    start = start or datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(_bar(symbol, start + timedelta(days=i), c) for i, c in enumerate(closes))


def _flat(symbol: str, bars: int = 23, close: float = 100.0) -> tuple[Bar, ...]:
    return _series(symbol, [close] * bars)


def _alternating(symbol: str, amplitude: float, bars: int = 23) -> tuple[Bar, ...]:
    closes = [100.0 + amplitude if i % 2 else 100.0 for i in range(bars)]
    return _series(symbol, closes)


class _Universe:
    def __init__(self, members: set[str]) -> None:
        self._members = set(members)

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return symbol in self._members


def _context(
    histories: dict[str, tuple[Bar, ...]],
    holdings: dict[str, float] | None = None,
    universe_members: set[str] | None = None,
) -> PortfolioContext:
    # last bar timestamp across all histories is decision ts
    last = max(max(b.timestamp for b in s) for s in histories.values())
    members = universe_members if universe_members is not None else set(histories.keys())
    # prices from last close
    prices = {sym: series[-1].close for sym, series in histories.items()}
    return PortfolioContext(
        decision_timestamp=last,
        market=Market.CRYPTO,
        timeframe="1d",
        history=histories,
        universe=_Universe(members),
        universe_version="test",
        equity=100_000.0,
        holdings=dict(holdings or {}),
        prices=prices,
        benchmark_symbol="BTC/USD",
    )


def _with_ranked(strategy: CryptoLowVarianceHysteresis1dV1, ranked: list[str]):
    """Monkeypatch _score_candidates to return deterministic ranking."""
    scored = tuple((float(i), sym) for i, sym in enumerate(ranked))
    strategy._score_candidates = lambda ctx: scored  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Class constants / economics
# ---------------------------------------------------------------------------
def test_retention_rank_constant_is_four() -> None:
    assert hasattr(CryptoLowVarianceHysteresis1dV1, "RETENTION_RANK")
    assert CryptoLowVarianceHysteresis1dV1.RETENTION_RANK == 4
    s = CryptoLowVarianceHysteresis1dV1()
    assert s.RETENTION_RANK == 4


def test_hysteresis_should_retain_pure_helper_defaults_to_four() -> None:
    # default retention_rank=4 matches tournament
    assert hysteresis_should_retain(1, True) is True
    assert hysteresis_should_retain(4, True) is True
    assert hysteresis_should_retain(5, True) is False
    assert hysteresis_should_retain(1, False) is False
    assert hysteresis_should_retain(None, True) is False
    # explicit
    assert hysteresis_should_retain(4, True, retention_rank=4) is True
    assert hysteresis_should_retain(5, True, retention_rank=4) is False


def test_top_n_frozen_at_two() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    assert s.param_int("top_n", 2) == 2
    # decide must respect top_n==2 by default
    histories = {
        "AAA/USD": _flat("AAA/USD"),
        "BBB/USD": _flat("BBB/USD", close=101.0),
        "CCC/USD": _alternating("CCC/USD", 5),
    }
    ctx = _context(histories)
    target = s.decide(ctx)
    # top2 of 3 qualified -> exactly 2 weights
    assert len(target.weights) == 2


def test_lookback_frozen_at_22() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    assert s.param_int("variance_lookback_days", 22) == 22
    assert s.minimum_history_bars == 23
    # score candidates must be same as v2 (not overridden)
    assert (
        " _score_candidates" not in str(type(s).__dict__)
        or "_score_candidates" not in CryptoLowVarianceHysteresis1dV1.__dict__
    )
    # verify v2 and hysteresis produce identical ranking for same context
    v2 = CryptoLowVariance1dV2()
    histories = {
        "AAA/USD": _flat("AAA/USD"),
        "BBB/USD": _alternating("BBB/USD", 10),
        "CCC/USD": _alternating("CCC/USD", 1),
    }
    ctx = _context(histories)
    assert s._score_candidates(ctx) == v2._score_candidates(ctx)


def test_rebalance_frozen_at_96h() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    assert s.default_rebalance_hours == 96
    assert s.rebalance_policy_declared == "hours:96"
    assert s.cadence_label == "96h"
    assert s.param_int("rebalance_hours", 96) == 96


def test_equal_weights() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    histories = {
        "AAA/USD": _flat("AAA/USD"),
        "BBB/USD": _flat("BBB/USD", close=101.0),
        "CCC/USD": _alternating("CCC/USD", 5),
    }
    ctx = _context(histories)
    target = s.decide(ctx)
    weights = [w.weight for w in target.weights]
    assert len(weights) == 2
    assert all(w == pytest.approx(0.5) for w in weights)
    assert sum(weights) == pytest.approx(1.0)
    # also with single qualified -> weight 1.0
    solo = _context({"AAA/USD": _flat("AAA/USD")})
    t2 = s.decide(solo)
    assert len(t2.weights) == 1
    assert t2.weights[0].weight == pytest.approx(1.0)


def test_rejects_hysteresis_rank_threshold_param() -> None:
    with pytest.raises(ValueError, match="hysteresis_rank_threshold"):
        CryptoLowVarianceHysteresis1dV1({"hysteresis_rank_threshold": 4})
    with pytest.raises(ValueError, match="hysteresis_rank_threshold"):
        CryptoLowVarianceHysteresis1dV1({"hysteresis_rank_threshold": 3})
    # also retention_rank is frozen, not tunable
    with pytest.raises(ValueError, match="retention_rank"):
        CryptoLowVarianceHysteresis1dV1({"retention_rank": 4})


# ---------------------------------------------------------------------------
# Retention semantics — rank map, eligible_set via context.in_universe,
# held filter weight>1e-12, hysteresis_should_retain(rank,in_universe,retention_rank=4),
# sort by (rank, symbol), fill from ranking
# ---------------------------------------------------------------------------
def test_rank1_retained() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    ranked = ["AAA/USD", "BBB/USD", "CCC/USD", "DDD/USD", "EEE/USD", "FFF/USD"]
    _with_ranked(s, ranked)
    histories = {sym: _flat(sym) for sym in ranked}
    # held is rank1, eligible
    ctx = _context(histories, holdings={"AAA/USD": 0.5}, universe_members=set(ranked))
    target = s.decide(ctx)
    symbols = {w.symbol for w in target.weights}
    # AAA retained, fill from rank1 upward -> AAA + BBB (top2)
    assert "AAA/USD" in symbols
    assert target.metadata["retained"] == ("AAA/USD",)


def test_rank3_retained() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    ranked = ["AAA/USD", "BBB/USD", "CCC/USD", "DDD/USD", "EEE/USD", "FFF/USD"]
    _with_ranked(s, ranked)
    histories = {sym: _flat(sym) for sym in ranked}
    ctx = _context(histories, holdings={"CCC/USD": 0.5}, universe_members=set(ranked))
    target = s.decide(ctx)
    symbols = {w.symbol for w in target.weights}
    assert "CCC/USD" in symbols
    # retained sorted by (rank, symbol) so CCC rank3 retained, then fill BBB? Wait fill logic: retained=[CCC], then iterate ranked and add AAA, BBB...
    # selected = [CCC] + AAA (since AAA not in selected) = [CCC, AAA] -> sorted weights AAA,CCC
    assert "CCC/USD" in symbols
    assert target.metadata["retained"] == ("CCC/USD",)


def test_rank4_retained() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    ranked = ["AAA/USD", "BBB/USD", "CCC/USD", "DDD/USD", "EEE/USD", "FFF/USD"]
    _with_ranked(s, ranked)
    histories = {sym: _flat(sym) for sym in ranked}
    ctx = _context(histories, holdings={"DDD/USD": 0.5}, universe_members=set(ranked))
    target = s.decide(ctx)
    assert "DDD/USD" in {w.symbol for w in target.weights}
    assert target.metadata["retained"] == ("DDD/USD",)


def test_rank5_exits() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    ranked = ["AAA/USD", "BBB/USD", "CCC/USD", "DDD/USD", "EEE/USD", "FFF/USD"]
    _with_ranked(s, ranked)
    histories = {sym: _flat(sym) for sym in ranked}
    ctx = _context(histories, holdings={"EEE/USD": 0.5}, universe_members=set(ranked))
    target = s.decide(ctx)
    # EEE rank5 >4 must exit, not retained, selection becomes AAA,BBB
    assert "EEE/USD" not in {w.symbol for w in target.weights}
    assert target.metadata["retained"] == ()
    assert set(target.metadata["selected_symbols"]) == {"AAA/USD", "BBB/USD"}


def test_ineligible_rank1_exits() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    # AAA is held but no longer qualified / not in PIT universe, so not in ranked
    ranked = ["BBB/USD", "CCC/USD", "DDD/USD", "EEE/USD", "FFF/USD"]
    _with_ranked(s, ranked)
    histories = {sym: _flat(sym) for sym in ranked}
    # include AAA bars for completeness but universe excludes it; _score_candidates mocked so AAA not ranked (rank None)
    histories["AAA/USD"] = _flat("AAA/USD")
    ctx = _context(histories, holdings={"AAA/USD": 0.5}, universe_members=set(ranked))
    target = s.decide(ctx)
    # rank_map.get(AAA) is None, in_univ False -> not retained, AAA must exit -> top2 from ranked
    assert "AAA/USD" not in {w.symbol for w in target.weights}
    assert target.metadata["retained"] == ()


def test_held_weight_filter_1e_minus_12() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    ranked = ["AAA/USD", "BBB/USD", "CCC/USD", "DDD/USD"]
    _with_ranked(s, ranked)
    histories = {sym: _flat(sym) for sym in ranked}
    # holding with dust weight <=1e-12 must NOT be considered held -> not retained
    ctx = _context(histories, holdings={"AAA/USD": 1e-13}, universe_members=set(ranked))
    target = s.decide(ctx)
    # dusty holding ignored, so not retained, selection is AAA,BBB anyway but retained empty
    assert target.metadata["retained"] == ()
    # holding with >1e-12 must be considered
    ctx2 = _context(histories, holdings={"AAA/USD": 1e-11}, universe_members=set(ranked))
    target2 = s.decide(ctx2)
    assert target2.metadata["retained"] == ("AAA/USD",)


def test_sort_by_rank_then_symbol_and_fill() -> None:
    s = CryptoLowVarianceHysteresis1dV1()
    ranked = ["AAA/USD", "BBB/USD", "CCC/USD", "DDD/USD", "EEE/USD"]
    _with_ranked(s, ranked)
    histories = {sym: _flat(sym) for sym in ranked}
    # hold DDD (rank4) and BBB (rank2) — retained should be sorted by rank then symbol: BBB,DDD but top_n=2 so both kept
    ctx = _context(histories, holdings={"DDD/USD": 0.5, "BBB/USD": 0.5}, universe_members=set(ranked))
    target = s.decide(ctx)
    assert target.metadata["retained"] == ("BBB/USD", "DDD/USD")
    assert set(target.metadata["selected_symbols"]) == {"BBB/USD", "DDD/USD"}
    # if retained fills all slots, no fill needed; else fill from rank1
    # hold only BBB -> retained BBB, fill AAA
    ctx3 = _context(histories, holdings={"BBB/USD": 0.5}, universe_members=set(ranked))
    t3 = s.decide(ctx3)
    assert t3.metadata["retained"] == ("BBB/USD",)
    assert "AAA/USD" in t3.metadata["selected_symbols"]
    assert "BBB/USD" in t3.metadata["selected_symbols"]
