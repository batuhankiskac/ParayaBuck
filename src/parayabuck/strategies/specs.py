from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TypeAlias

from parayabuck.core.models import ExitPolicy, Market


def _positive(name: str, value: float | int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class StockTrendParams:
    fast_ema_period: int = 20
    slow_ema_period: int = 50
    rsi_period: int = 14
    rsi_floor: float = 40.0
    rsi_ceiling: float = 68.0
    atr_period: int = 14
    pullback_lookback: int = 5
    touch_tolerance_atr: float = 0.25
    maximum_distance_atr: float = 1.2
    stop_atr_multiple: float = 2.0
    minimum_stop_pct: float = 0.012
    target_r_multiple: float = 2.2

    def __post_init__(self) -> None:
        for name in (
            "fast_ema_period",
            "slow_ema_period",
            "rsi_period",
            "atr_period",
            "pullback_lookback",
            "touch_tolerance_atr",
            "maximum_distance_atr",
            "stop_atr_multiple",
            "minimum_stop_pct",
            "target_r_multiple",
        ):
            _positive(name, getattr(self, name))
        if self.fast_ema_period >= self.slow_ema_period:
            raise ValueError("Fast EMA period must be below slow EMA period")
        if not 0 <= self.rsi_floor < self.rsi_ceiling <= 100:
            raise ValueError("Invalid RSI window")


@dataclass(frozen=True)
class StockMeanReversionParams:
    bollinger_period: int = 20
    bollinger_deviations: float = 2.0
    rsi_period: int = 14
    rsi_ceiling: float = 45.0
    atr_period: int = 14
    stop_atr_multiple: float = 2.0
    minimum_stop_pct: float = 0.012
    minimum_middle_band_rr: float = 1.2
    minimum_abs_zscore: float = 1.0
    maximum_atr_pct: float = 0.08
    maximum_adx: float = 28.0
    maximum_adx_increase: float = 4.0

    def __post_init__(self) -> None:
        for name in (
            "bollinger_period",
            "bollinger_deviations",
            "rsi_period",
            "atr_period",
            "stop_atr_multiple",
            "minimum_stop_pct",
            "minimum_middle_band_rr",
            "minimum_abs_zscore",
            "maximum_atr_pct",
            "maximum_adx",
            "maximum_adx_increase",
        ):
            _positive(name, getattr(self, name))
        if not 0 < self.rsi_ceiling <= 100:
            raise ValueError("Invalid RSI ceiling")


@dataclass(frozen=True)
class CryptoTrendParams:
    fast_ema_period: int = 20
    slow_ema_period: int = 100
    atr_period: int = 14
    donchian_period: int = 20
    stop_atr_multiple: float = 2.2
    minimum_stop_pct: float = 0.018
    safety_target_r_multiple: float = 6.0
    minimum_volume_bar_ratio: float = 0.5
    reentry_cooldown_bars: int = 20

    def __post_init__(self) -> None:
        for name in (
            "fast_ema_period",
            "slow_ema_period",
            "atr_period",
            "donchian_period",
            "stop_atr_multiple",
            "minimum_stop_pct",
            "safety_target_r_multiple",
            "reentry_cooldown_bars",
        ):
            _positive(name, getattr(self, name))
        if self.fast_ema_period >= self.slow_ema_period:
            raise ValueError("Fast EMA period must be below slow EMA period")
        if not 0 <= self.minimum_volume_bar_ratio <= 1:
            raise ValueError("Volume bar ratio must be in [0, 1]")


@dataclass(frozen=True)
class CryptoRangeParams:
    bollinger_period: int = 20
    bollinger_deviations: float = 2.2
    rsi_period: int = 14
    rsi_ceiling: float = 43.0
    atr_period: int = 14
    stop_atr_multiple: float = 1.8
    minimum_stop_pct: float = 0.015
    minimum_middle_band_rr: float = 1.2
    maximum_adx: float = 25.0
    maximum_atr_pct: float = 0.10
    minimum_volume_bar_ratio: float = 0.5
    reentry_cooldown_bars: int = 24

    def __post_init__(self) -> None:
        for name in (
            "bollinger_period",
            "bollinger_deviations",
            "rsi_period",
            "atr_period",
            "stop_atr_multiple",
            "minimum_stop_pct",
            "minimum_middle_band_rr",
            "maximum_adx",
            "maximum_atr_pct",
            "reentry_cooldown_bars",
        ):
            _positive(name, getattr(self, name))
        if not 0 < self.rsi_ceiling <= 100:
            raise ValueError("Invalid RSI ceiling")
        if not 0 <= self.minimum_volume_bar_ratio <= 1:
            raise ValueError("Volume bar ratio must be in [0, 1]")


StrategyParams: TypeAlias = (
    StockTrendParams | StockMeanReversionParams | CryptoTrendParams | CryptoRangeParams
)


@dataclass(frozen=True)
class StrategySpec:
    name: str
    version: str
    market: Market
    timeframe: str
    parameters: StrategyParams
    exit_policy: ExitPolicy = ExitPolicy()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip() or not self.timeframe.strip():
            raise ValueError("Strategy name, version and timeframe are required")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "market": self.market.value,
            "timeframe": self.timeframe,
            "parameters": asdict(self.parameters),
            "exit_policy": asdict(self.exit_policy),
        }


__all__ = [
    "CryptoRangeParams",
    "CryptoTrendParams",
    "StockMeanReversionParams",
    "StockTrendParams",
    "StrategyParams",
    "StrategySpec",
]
