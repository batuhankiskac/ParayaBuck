from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class SymbolSpec:
    tick_size: float
    point_value: float


@dataclass
class AlpacaConfig:
    paper: bool = True
    data_delayed: bool = False
    fractional_shares: bool = True
    stock_feed: str = "iex"


@dataclass
class AppConfig:
    """General runtime settings."""

    log_level: str = "INFO"
    timeframe: str = "5m"


@dataclass
class Config:
    symbols: dict[str, SymbolSpec]
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    app: AppConfig = field(default_factory=AppConfig)
    raw: dict[str, Any] = field(default_factory=dict)

    def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        if symbol not in self.symbols:
            raise KeyError(f"Symbol '{symbol}' not configured in settings.yaml")
        return self.symbols[symbol]


def load_config(config_path: str | None = None) -> Config:
    load_dotenv()

    path = Path(config_path or os.getenv("CONFIG_PATH") or "config/settings.yaml")
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    symbols = {
        name: SymbolSpec(
            tick_size=s["tick_size"],
            point_value=s["point_value"],
        )
        for name, s in raw.get("symbols", {}).items()
    }

    config = Config(
        symbols=symbols,
        alpaca=AlpacaConfig(**raw.get("alpaca", {})),
        app=AppConfig(**raw.get("app", {})),
        raw=raw,
    )
    _validate_config(config)
    return config


def _validate_config(config: Config) -> None:
    """Reject unsafe or internally inconsistent runtime settings early."""
    if config.app.timeframe not in {"1m", "5m", "15m", "30m", "1h", "1d"}:
        raise ValueError(f"Unsupported app.timeframe: {config.app.timeframe}")
    if config.alpaca.stock_feed not in {"iex", "sip", "delayed_sip"}:
        raise ValueError("alpaca.stock_feed must be iex, sip, or delayed_sip")
    for symbol, spec in config.symbols.items():
        if spec.tick_size <= 0 or spec.point_value <= 0:
            raise ValueError(f"symbols.{symbol} tick_size and point_value must be positive")
