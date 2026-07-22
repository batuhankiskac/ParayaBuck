from parayabuck_v2.strategies.base import Strategy, StrategyContext
from parayabuck_v2.strategies.crypto_range import CryptoRangeReversion
from parayabuck_v2.strategies.crypto_trend import CryptoTrendBreakout
from parayabuck_v2.strategies.stock_mean_reversion import StockMeanReversion
from parayabuck_v2.strategies.stock_trend import StockTrendPullback


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
    "StockMeanReversion",
    "StockTrendPullback",
    "Strategy",
    "StrategyContext",
    "default_strategies",
]
