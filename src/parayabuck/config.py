from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


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
class EngineConfig:
    timeframe: str = "5m"
    higher_timeframe: str = "1h"
    minimum_signal_score: float = 65.0
    warmup_bars: int = 250
    decision_db_path: str = "data/runtime/parayabuck.sqlite3"
    ai_logging_enabled: bool = True
    ai_log_path: str = "data/runtime/ai_decisions"
    prediction_horizon_bars: int = 48
    risk: RiskLimits = field(default_factory=RiskLimits)

    def __post_init__(self) -> None:
        supported = {"1m", "5m", "15m", "30m", "1h", "1d"}
        if self.timeframe not in supported or self.higher_timeframe not in supported:
            raise ValueError("Unsupported timeframe")
        if not 0 <= self.minimum_signal_score <= 100 or self.warmup_bars < 50:
            raise ValueError("Invalid signal score or warmup")
        if self.prediction_horizon_bars <= 0:
            raise ValueError("prediction_horizon_bars must be positive")


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Web UI must bind to a loopback host")
        if not 1 <= self.port <= 65535:
            raise ValueError("Invalid port")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("Invalid log level")


@dataclass(frozen=True)
class AlpacaConfig:
    paper: bool = True
    data_delayed: bool = False
    stock_feed: str = "iex"

    def __post_init__(self) -> None:
        if self.stock_feed not in {"iex", "sip", "delayed_sip"}:
            raise ValueError("alpaca.stock_feed must be iex, sip, or delayed_sip")


@dataclass(frozen=True)
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    symbols: tuple[str, ...] = ()
    engine: EngineConfig = field(default_factory=EngineConfig)

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("At least one symbol must be configured")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Configured symbols must be unique")


def load_config(config_path: str | None = None) -> Config:
    load_dotenv()
    path = Path(config_path or os.getenv("CONFIG_PATH") or "config/settings.yaml")
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    engine_raw = dict(raw.get("engine", {}))
    risk = RiskLimits(**engine_raw.pop("risk", {}))
    return Config(
        app=AppConfig(**raw.get("app", {})),
        alpaca=AlpacaConfig(**raw.get("alpaca", {})),
        symbols=tuple(raw.get("symbols", ())),
        engine=EngineConfig(risk=risk, **engine_raw),
    )


__all__ = [
    "AlpacaConfig",
    "AppConfig",
    "Config",
    "EngineConfig",
    "RiskLimits",
    "load_config",
]
