from parayabuck.config import EngineConfig
from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)
from parayabuck.strategies.crypto_range import CryptoRangeReversion
from parayabuck.strategies.crypto_trend import CryptoTrendBreakout
from parayabuck.strategies.specs import (
    CryptoRangeParams,
    CryptoTrendParams,
    StockMeanReversionParams,
    StockTrendParams,
    StrategySpec,
)
from parayabuck.strategies.stock_mean_reversion import StockMeanReversion
from parayabuck.strategies.stock_trend import StockTrendPullback


def default_strategies(config: EngineConfig | None = None) -> tuple[Strategy, ...]:
    volume_bar_ratio = (config or EngineConfig()).crypto_min_volume_bar_ratio
    return (
        StockTrendPullback(),
        StockMeanReversion(),
        CryptoTrendBreakout(min_volume_bar_ratio=volume_bar_ratio),
        CryptoRangeReversion(min_volume_bar_ratio=volume_bar_ratio),
    )


__all__ = [
    "CryptoRangeReversion",
    "CryptoTrendBreakout",
    "CryptoRangeParams",
    "CryptoTrendParams",
    "ConditionEvaluation",
    "StockMeanReversion",
    "StockMeanReversionParams",
    "StockTrendPullback",
    "StockTrendParams",
    "Strategy",
    "StrategyContext",
    "StrategyEvaluation",
    "StrategySpec",
    "default_strategies",
]
