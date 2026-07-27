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
        if (
            self.max_positions <= 0
            or self.minimum_risk_reward <= 0
            or self.consecutive_loss_reduction_after <= 0
        ):
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
    trading_enabled: bool = True
    equity_trading_enabled: bool = True
    crypto_trading_enabled: bool = False
    live_trading_enabled: bool = False
    manage_external_positions: bool = False
    reject_stale_signals: bool = True
    event_grace_seconds: float = 5.0
    data_finalization_grace_seconds: float = 2.0
    maximum_signal_age_seconds: float = 20.0
    maximum_queue_age_seconds: float = 30.0
    account_snapshot_ttl_seconds: float = 15.0
    account_poll_interval_seconds: float = 15.0
    maximum_account_age_for_entry_seconds: float = 30.0
    event_queue_capacity: int = 1024
    max_new_positions_per_cycle: int = 1
    strategy_profile: str = "equity_5m_v1"
    allow_short: bool = False
    risk: RiskLimits = field(default_factory=RiskLimits)

    def __post_init__(self) -> None:
        supported = {"1m", "5m", "15m", "30m", "1h", "1d"}
        if self.timeframe not in supported or self.higher_timeframe not in supported:
            raise ValueError("Unsupported timeframe")
        if not 0 <= self.minimum_signal_score <= 100 or self.warmup_bars < 50:
            raise ValueError("Invalid signal score or warmup")
        if self.prediction_horizon_bars <= 0:
            raise ValueError("prediction_horizon_bars must be positive")
        minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
        if minutes[self.higher_timeframe] <= minutes[self.timeframe]:
            raise ValueError("higher_timeframe must be greater than timeframe")
        if self.consecutive_values_invalid:
            raise ValueError("Engine timing and capacity values must be positive")
        if self.max_new_positions_per_cycle != 1:
            raise ValueError("Only one new position per cycle is supported")
        if self.trading_enabled and self.strategy_profile != "equity_5m_v1":
            raise ValueError("Broker trading requires the equity_5m_v1 strategy profile")
        if self.trading_enabled and (self.timeframe != "5m" or self.higher_timeframe != "1h"):
            raise ValueError("Broker trading is enabled only for the validated 5m + 1h profile")

    @property
    def consecutive_values_invalid(self) -> bool:
        return (
            self.event_grace_seconds <= 0
            or self.data_finalization_grace_seconds < 0
            or self.maximum_signal_age_seconds <= 0
            or self.maximum_queue_age_seconds <= 0
            or self.account_snapshot_ttl_seconds <= 0
            or self.account_poll_interval_seconds <= 0
            or self.maximum_account_age_for_entry_seconds <= 0
            or self.event_queue_capacity <= 0
        )


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
class DataConfig:
    equity_session: str = "regular"
    include_extended_hours: bool = False

    def __post_init__(self) -> None:
        if self.equity_session != "regular":
            raise ValueError("Only regular equity sessions are currently trading-enabled")
        if self.include_extended_hours:
            raise ValueError("Extended-hours trading is not enabled")


@dataclass(frozen=True)
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    data: DataConfig = field(default_factory=DataConfig)
    symbols: tuple[str, ...] = ()
    engine: EngineConfig = field(default_factory=EngineConfig)

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("At least one symbol must be configured")
        from parayabuck.markets.symbols import canonical_symbol

        canonical = tuple(canonical_symbol(symbol) for symbol in self.symbols)
        if len(set(canonical)) != len(canonical):
            raise ValueError("Configured symbols must be canonical and unique")
        equity_count = sum("/" not in symbol for symbol in canonical)
        if self.alpaca.stock_feed == "iex" and equity_count > 30:
            raise ValueError("Alpaca Basic IEX supports at most 30 equity stream symbols")


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
        data=DataConfig(**raw.get("data", {})),
        symbols=tuple(raw.get("symbols", ())),
        engine=EngineConfig(risk=risk, **engine_raw),
    )


__all__ = [
    "AlpacaConfig",
    "AppConfig",
    "Config",
    "DataConfig",
    "EngineConfig",
    "RiskLimits",
    "load_config",
]
