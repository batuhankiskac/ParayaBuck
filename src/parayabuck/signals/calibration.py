"""Score-to-expected-R calibration for backtest research only.

Nothing in the live runtime path calls ``fit_score_calibration`` /
``apply_score_calibration`` or sets ``Signal.normalized_expected_r``: the
ranking pipeline always takes its uncalibrated fallback in production. The
module is kept for research and tests; wiring it into the engine would be a
deliberate feature decision, not a bug fix.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from parayabuck.backtest.trades import TradeRecord
from parayabuck.core.models import Signal


@dataclass(frozen=True)
class ScoreBin:
    maximum_score: float
    expected_r: float
    observations: int


@dataclass(frozen=True)
class ScoreCalibration:
    strategy: str
    bins: tuple[ScoreBin, ...]
    monotonic: bool

    def expected_r(self, score: float) -> float | None:
        if not self.bins:
            return None
        for item in self.bins:
            if score <= item.maximum_score:
                return item.expected_r
        return self.bins[-1].expected_r


def fit_score_calibration(
    trades: tuple[TradeRecord, ...],
    *,
    strategy: str,
    bins: int = 10,
    minimum_observations: int = 20,
) -> ScoreCalibration:
    """Fit strategy-internal expected-R bins from training trades only."""
    eligible = sorted(
        (
            trade
            for trade in trades
            if trade.strategy == strategy and trade.initial_risk > 0 and trade.qty > 0
        ),
        key=lambda trade: trade.entry_score,
    )
    if len(eligible) < minimum_observations or bins <= 0:
        return ScoreCalibration(strategy, (), False)
    bucket_count = min(bins, len(eligible))
    buckets: list[list[TradeRecord]] = [[] for _ in range(bucket_count)]
    for index, trade in enumerate(eligible):
        bucket_index = min(bucket_count - 1, index * bucket_count // len(eligible))
        buckets[bucket_index].append(trade)
    fitted = tuple(
        ScoreBin(
            maximum_score=max(trade.entry_score for trade in bucket),
            expected_r=sum(
                trade.pnl / (trade.initial_risk * trade.qty) for trade in bucket
            )
            / len(bucket),
            observations=len(bucket),
        )
        for bucket in buckets
    )
    monotonic = all(
        later.expected_r >= earlier.expected_r
        for earlier, later in zip(fitted, fitted[1:])
    )
    return ScoreCalibration(strategy, fitted, monotonic)


def apply_score_calibration(signal: Signal, calibration: ScoreCalibration) -> Signal:
    if signal.strategy != calibration.strategy:
        raise ValueError("Calibration strategy does not match signal strategy")
    expected_r = calibration.expected_r(signal.score)
    return replace(signal, normalized_expected_r=expected_r)


__all__ = [
    "ScoreBin",
    "ScoreCalibration",
    "apply_score_calibration",
    "fit_score_calibration",
]
