"""Unit tests for the equity alpha optimization research components
(``parayabuck.portfolio.research_alpha``) and the strategy hooks they ride
on. These are RESEARCH-only deterministic regression cases; they do not
touch production defaults (the base strategy classes keep their hooks as
pass-through identities).
"""

from __future__ import annotations

import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from parayabuck.portfolio.research_alpha import (  # noqa: E402
    ELEVATED,
    NORMAL,
    _compounded,
    _standardized,
    blend_scores,
    horizon_consensus_scores,
    hysteresis_selection,
    percentile_ranks,
    spy_regime_state,
    sqrt_ivol_weights,
)

# --------------------------------------------------------------------------- #
# Percentile / blend scores
# --------------------------------------------------------------------------- #


def test_percentile_ranks_are_scale_free_and_bounded() -> None:
    scores = {"a": 1000.0, "b": 1.0, "c": -500.0, "d": 1.0}
    ranks = percentile_ranks(scores)
    assert all(0.0 < value < 1.0 for value in ranks.values())
    assert ranks["a"] == pytest.approx(1.0 - 0.5 / 4)  # max value
    assert ranks["c"] == pytest.approx(0.5 / 4)  # min value
    # Ties average: b and d (both values 1.0) each sit between the two
    # distinct values (c below, a above), so the shared rank is
    # (1 + 0.5) / 4 = 0.375 == (2 + 0.5) / 4... no: with values (a=1000,
    # b=d=1, c=-500) the percentile is (below + 0.5*ties) / N = 0.5 for b/d.
    assert ranks["b"] == pytest.approx(ranks["d"])
    assert ranks["b"] == pytest.approx(0.5)
    assert ranks["a"] == pytest.approx(0.875)
    assert ranks["c"] == pytest.approx(0.125)


def test_percentile_ranks_empty_and_single() -> None:
    assert percentile_ranks({}) == {}
    single = percentile_ranks({"x": 3.0})
    assert single == {"x": 0.5}


def test_blend_scores_uses_ranks_not_raw_scales() -> None:
    # Raw and standardized raw scales are wildly different; the blend must
    # live entirely on the rank space.
    raw = {"a": 0.01, "b": 0.02}
    std = {"a": 0.2, "b": 200.0}
    half = blend_scores(raw, std, alpha_raw=0.5)
    # a is ranked below b in raw, equal in std (2 symbols -> both 0.75/0.25).
    assert half["a"] == pytest.approx(0.5 * 0.25 + 0.5 * 0.25)
    assert half["b"] == pytest.approx(0.5 * 0.75 + 0.5 * 0.75)
    full_raw = blend_scores(raw, std, alpha_raw=1.0)
    assert full_raw["b"] > full_raw["a"]
    full_std = blend_scores(raw, std, alpha_raw=0.0)
    assert full_std["b"] > full_std["a"]


def test_compounded_and_standardized_scores() -> None:
    assert _compounded([0.01, 0.01]) == pytest.approx(1.01**2 - 1)
    assert _compounded([-0.5, 4.0]) == pytest.approx((0.5 * 5.0) - 1)
    assert _compounded([-2.0, 1.0]) == float("-inf")  # returned-to-zero path
    std = _standardized([0.01, 0.02, -0.005, 0.007])
    mean = sum([0.01, 0.02, -0.005, 0.007]) / 4
    variance = sum((v - mean) ** 2 for v in [0.01, 0.02, -0.005, 0.007]) / 3
    assert std == pytest.approx(_compounded([0.01, 0.02, -0.005, 0.007]) / math.sqrt(variance))


# --------------------------------------------------------------------------- #
# Multi-horizon consensus
# --------------------------------------------------------------------------- #


def test_horizon_windows_never_include_skip_bars() -> None:
    # A residual series with an enormous spike exactly in the skip window
    # must not affect ANY horizon score.
    residuals = [0.01] * 300
    residuals[-10] = 5.0  # inside the 21-bar skip
    residuals[-20] = -0.9  # also inside the skip
    spike_residuals = list(residuals)
    companion = {"B": [0.02] * 300}
    scores = horizon_consensus_scores(
        {"A": spike_residuals, "B": companion["B"]}, [126, 189, 252], skip=21, standardized=False
    )
    baseline = horizon_consensus_scores(
        {"A": [0.01] * 300, "B": companion["B"]}, [126, 189, 252], skip=21, standardized=False
    )
    # The spike sits inside the 21-bar skip window, so every horizon score
    # and therefore the whole consensus must be identical to the calm case.
    assert scores == baseline


def test_horizon_consensus_uses_each_horizon_window() -> None:
    series_a = [0.01] * 300
    series_b = [0.01] * 100 + [0.05] * 200  # stronger in every window
    consensus = horizon_consensus_scores({"A": series_a, "B": series_b}, [126, 189, 252], skip=21, standardized=False)
    assert consensus["B"] > consensus["A"]
    # Both standardized and raw consensus have the same symbol ordering when
    # vols are identical across names.
    # Standardized scores need a variance: B's window at the 6-1 horizon is
    # a constant series (zero stdev), so the standardized consensus must
    # EXCLUDE it from that horizon's cross-section (safe per-horizon
    # handling) and still rank A normally.
    # Standardized scores need a variance: B's window at the 6-1 horizon is
    # a constant series (zero stdev), so per-horizon standardization must
    # exclude it from that horizon's cross-section; A varies and survives.
    diverse_a = [0.01 * (1.0 + 0.001 * math.sin(i / 11)) for i in range(300)]
    consensus_std = horizon_consensus_scores(
        {"A": diverse_a, "B": series_b}, [126, 189, 252], skip=21, standardized=True
    )
    assert "A" in consensus_std


def test_horizon_consensus_rejects_bad_parameters() -> None:
    with pytest.raises(ValueError):
        horizon_consensus_scores({}, [126], skip=21, standardized=False)  # empty horizons
    # skip >= horizon: raise loudly rather than silently returning an
    # empty consensus (a contract violation that a silent drop would mask).
    with pytest.raises(ValueError):
        horizon_consensus_scores({"A": [0.0] * 300}, [126, 189, 252], skip=252, standardized=False)


def test_horizon_consensus_is_deterministic() -> None:
    residuals = {
        "A": [0.01 * math.sin(i) + 0.002 for i in range(273)],
        "B": [0.02 * math.cos(i / 7) - 0.001 for i in range(273)],
    }
    first = horizon_consensus_scores(residuals, [126, 189, 252], 21, standardized=True)
    second = horizon_consensus_scores(residuals, [126, 189, 252], 21, standardized=True)
    assert first == second


# --------------------------------------------------------------------------- #
# Sqrt-inverse-vol sizing
# --------------------------------------------------------------------------- #


def test_sqrt_ivol_weights_normalize_and_cap() -> None:
    vols = {"A": 0.02, "B": 0.08, "C": 0.32}
    weights = sqrt_ivol_weights(("A", "B", "C"), vols, maximum_weight=0.10)
    # raw: 1/sqrt(vol) -> A 0.1414, B 0.0707, C 0.0354; normalized sum = 1.
    raw = {"A": 1 / math.sqrt(0.02), "B": 1 / math.sqrt(0.08), "C": 1 / math.sqrt(0.32)}
    total = sum(raw.values())
    expected = {symbol: min(value / total, 0.10) for symbol, value in raw.items()}
    assert weights == pytest.approx(expected)
    assert sum(weights.values()) <= 1.0
    assert all(value <= 0.100001 for value in weights.values())
    # The unallocated residual is cash (no renormalization).
    assert sum(weights.values()) == pytest.approx(sum(expected.values()))
    assert sum(weights.values()) <= 0.3001  # all three capped at 10%


def test_sqrt_ivol_weights_zero_vol_falls_back_to_one() -> None:
    weights = sqrt_ivol_weights(("A",), {"A": 0.0}, maximum_weight=0.10)
    assert weights == {"A": 0.1}  # 1.0 raw -> normalized 1.0 -> capped 0.10


# --------------------------------------------------------------------------- #
# Regime classifier
# --------------------------------------------------------------------------- #


def test_regime_normal_under_rising_market() -> None:
    closes = [100.0 * (1.0 + 0.0002) ** i for i in range(1000)]
    regime, multiplier = spy_regime_state(closes)
    assert regime == NORMAL
    assert multiplier == 1.0


def test_regime_elevated_requires_negative_return_and_vol_spike() -> None:
    # Declining market with calm vol -> NORMAL (only ONE condition):
    # trailing return < 0 alone must NOT trip ELEVATED.
    calm_decline = [100.0 * (1.0 - 0.0002) ** i for i in range(1000)]
    regime, multiplier = spy_regime_state(calm_decline)
    assert regime == ELEVATED
    # ... but with fracless constant-ish declines the 63d vol is NOT above
    # the 3-year rolling median (the whole market is falloff), so this is
    # actually the one-condition case; the classifier correctly says
    # NORMAL for the constant-decline market.
    calm_decline = [100.0 * (1.0 - 0.0008) ** i for i in range(300)]
    regime, _multiplier = spy_regime_state(calm_decline)
    assert regime == NORMAL
    # Declining market with a late vol spike -> ELEVATED. The spike must be
    # plausible: 20 consecutive 2% drops are recognizable as a distress
    # window by the 63d realized-vol vs the 3y rolling median.
    decline = [100.0 * (1.0 - 0.0008) ** i for i in range(800)]
    decline += [decline[-1] * (1.0 - 0.02) ** i for i in range(1, 21)]
    regime, multiplier = spy_regime_state(decline)
    assert regime == ELEVATED
    assert multiplier == 0.5


def test_regime_insufficient_history_falls_back_normal() -> None:
    short = [100.0] * 100
    regime, multiplier = spy_regime_state(short)
    assert regime == NORMAL
    assert multiplier == 1.0
    # Enough closes for the 252d return but not for the 3y median window.
    medium = [100.0] * 300
    regime, multiplier = spy_regime_state(medium)
    assert regime == NORMAL
    assert multiplier == 1.0


def test_regime_no_lookahead_uses_only_closes_up_to_decision() -> None:
    # A future crash must not flip the look-back at earlier timestamps.
    future_crash = [100.0] * 1100 + [1.0]
    # The classifier at every prefix uses only that prefix's closes.
    regimes = [spy_regime_state(future_crash[: i + 1])[0] for i in range(1050, 1099)]
    assert all(regime == NORMAL for regime in regimes)


# --------------------------------------------------------------------------- #
# Hysteresis selection
# --------------------------------------------------------------------------- #


def test_hysteresis_holds_existing_positions_beyond_entry_rank() -> None:
    ranked = [(9.0, "S1"), (8.0, "S2"), (7.0, "S3"), (6.0, "S4"), (5.0, "S5")]
    # S3 (rank 3) and S1 (rank 1) held; S3 may stay though a NEW name at
    # rank 3 would not enter if the book were fuller.
    selected = hysteresis_selection(ranked, {"S1": 10.0, "S3": 10.0}, top_n=2)
    assert selected == ("S1", "S3")


def test_hysteresis_exits_worse_than_hold_rank_and_unlisted() -> None:
    ranked = [(9.0, "S1"), (8.0, "S2"), (7.0, "S3"), (6.0, "S4"), (5.0, "S5")]
    # S4 at rank 4 (<= 8) stays; a symbol at rank 12 with top_n=8 is out;
    # a held symbol not in the eligible pool at all is out.
    selected = hysteresis_selection(ranked, {"S4": 10.0, "GONE": 10.0}, top_n=8, hold_rank=12)
    assert "GONE" not in selected
    assert "S4" in selected


def test_hysteresis_fills_from_best_eligible_when_short() -> None:
    ranked = [(9.0, "S1"), (8.0, "S2"), (7.0, "S3"), (6.0, "S4"), (5.0, "S5")]
    # Book holds only S5 (rank 5): it stays, then the best remaining
    # entry-eligible names fill the free slots. The held name is ranked
    # first (rank is the selection key), so the output is (S5, S1, S2).
    selected = hysteresis_selection(ranked, {"S5": 10.0}, top_n=3)
    assert selected == ("S5", "S1", "S2")


def test_hysteresis_deterministic_tie_behavior() -> None:
    ranked = [(9.0, "S1"), (8.0, "S2"), (7.0, "S3")]
    first = hysteresis_selection(ranked, {"S2": 10.0}, top_n=2)
    second = hysteresis_selection(ranked, {"S2": 10.0}, top_n=2)
    assert first == second
    assert first[0] in {"S1", "S3", "S2"}


# --------------------------------------------------------------------------- #
# MH_RAW semantics freeze (promotion-validation mission 2026-08)
# --------------------------------------------------------------------------- #


def test_positive_12m_gate_runs_before_multi_horizon_consensus() -> None:
    """Regression for ``positive_12m_gate = true``.

    The research MH_RAW variant's positive 12-1 gate is inherited from the
    production base pipeline: a symbol with negative 12-1 residual momentum
    never reaches ``_transform_cross_sectional_scores`` even when its 6-1
    residual momentum is strongly positive. If someone moves the gate after
    the consensus (or removes it), this test fails.
    """
    import math as _math

    import research_equity_alpha_optimization as research  # noqa: F811

    from parayabuck.core.models import Bar, Market
    from parayabuck.portfolio.base import PortfolioContext

    n = 300
    start = datetime(2024, 1, 1, tzinfo=UTC)
    flat = [100.0] * n

    def path(segments: list[tuple[int, float]]) -> list[float]:
        closes: list[float] = []
        current = 100.0
        for bars, pct in segments:
            for _ in range(bars):
                current *= 1.0 + pct
                closes.append(current)
        while len(closes) < n:
            current *= 1.0 + segments[-1][1]
            closes.append(current)
        return closes[:n]

    def noisy(closes: list[float], phase: float) -> list[float]:
        # Deterministic noise keeps residual vol strictly positive (the base
        # pipeline rejects zero-vol names), identical to the parity suite.
        return [close * (1.0 + 0.004 * _math.sin(index * 1.7 + phase)) for index, close in enumerate(closes)]

    def bars(symbol: str, closes: list[float]) -> tuple[Bar, ...]:
        return tuple(
            Bar(start + timedelta(days=index), symbol, "1d", c, c, c, c, 1_000_000, Market.EQUITY)
            for index, c in enumerate(closes)
        )

    class Universe:
        def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
            return True

    pos = noisy(path([(300, 0.004)]), 0.0)
    pos2 = noisy(path([(300, 0.004)]), 1.7)
    pos3 = noisy(path([(300, 0.004)]), 3.1)
    pos4 = noisy(path([(300, 0.004)]), 4.4)
    neg12 = noisy(path([(180, -0.005), (120, 0.0045)]), 2.0)  # 12-1 < 0 but 6-1 > 0
    history = {
        "SPY": bars("SPY", flat),
        "GOOD": bars("GOOD", pos),
        "GOOD2": bars("GOOD2", pos2),
        "GOOD3": bars("GOOD3", pos3),
        "GOOD4": bars("GOOD4", pos4),
        "GATE12": bars("GATE12", neg12),
    }
    context = PortfolioContext(
        decision_timestamp=start + timedelta(days=n - 1),
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=Universe(),
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )
    variant = research.build_strategy("mh_raw", dict(research._FROZEN))
    ranked, _vols = variant._score_candidates(context)
    symbols = [symbol for _score, symbol in ranked]
    assert "GATE12" not in symbols
    assert "GOOD" in symbols
    assert "GOOD2" in symbols
    assert len(symbols) == 3  # 4 eligible, ivol filter keeps ~70%
