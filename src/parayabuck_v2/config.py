from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: float = 0.005
    max_position_pct: float = 0.10
    max_crypto_exposure_pct: float = 0.20
    max_sector_exposure_pct: float = 0.25
    max_gross_exposure_pct: float = 0.60
    max_positions: int = 5
    daily_loss_limit_pct: float = 0.015
    max_drawdown_pct: float = 0.10
    minimum_risk_reward: float = 1.8
    consecutive_loss_reduction_after: int = 3

    def __post_init__(self) -> None:
        ratios = (
            self.risk_per_trade_pct,
            self.max_position_pct,
            self.max_crypto_exposure_pct,
            self.max_sector_exposure_pct,
            self.max_gross_exposure_pct,
            self.daily_loss_limit_pct,
            self.max_drawdown_pct,
        )
        if any(value <= 0 or value > 1 for value in ratios):
            raise ValueError("Risk ratios must be in (0, 1]")
        if self.max_positions <= 0 or self.minimum_risk_reward <= 0:
            raise ValueError("Invalid risk limits")


@dataclass(frozen=True)
class V2Config:
    timeframe: str = "5m"
    higher_timeframe: str = "1h"
    minimum_signal_score: float = 65.0
    warmup_bars: int = 250
    shadow_mode: bool = True
    decision_db_path: str = "data/runtime/parayabuck_v2.sqlite3"
    risk: RiskLimits = field(default_factory=RiskLimits)

    def __post_init__(self) -> None:
        if self.timeframe not in {"1m", "5m", "15m", "30m", "1h", "1d"}:
            raise ValueError("Unsupported timeframe")
        if not 0 <= self.minimum_signal_score <= 100 or self.warmup_bars < 50:
            raise ValueError("Invalid signal score or warmup")


def load_v2_config(path: str = "config/settings_v2.yaml") -> V2Config:
    config_path = Path(path)
    if not config_path.exists():
        return V2Config()
    raw = yaml.safe_load(config_path.read_text()) or {}
    risk = RiskLimits(**raw.pop("risk", {}))
    return V2Config(risk=risk, **raw)
