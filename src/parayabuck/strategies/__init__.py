from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)
from parayabuck.strategies.crypto_range import CryptoRangeReversion
from parayabuck.strategies.crypto_trend import CryptoTrendBreakout
from parayabuck.strategies.stock_mean_reversion import StockMeanReversion
from parayabuck.strategies.stock_trend import StockTrendPullback


def default_strategies() -> tuple[Strategy, ...]:
    return (
        StockTrendPullback(),
        StockMeanReversion(),
        CryptoTrendBreakout(),
        CryptoRangeReversion(),
    )


__all__ = [
    "CryptoRangeReversion",
    "CryptoTrendBreakout",
    "ConditionEvaluation",
    "StockMeanReversion",
    "StockTrendPullback",
    "Strategy",
    "StrategyContext",
    "StrategyEvaluation",
    "default_strategies",
]
