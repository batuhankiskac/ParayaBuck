from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class SymbolSpec:
    tick_size: float
    point_value: float


@dataclass
class RiskConfig:
    max_position_size: int
    max_daily_loss: float
    max_drawdown: float
    risk_per_trade: float
    commission_per_share: float
    slippage_cents: float


@dataclass
class AccountConfig:
    starting_balance: float
    currency: str


@dataclass
class StrategyConfig:
    name: str
    params: dict[str, Any]


@dataclass
class BacktestConfig:
    symbol: str
    timeframe: str
    start: str
    end: str


@dataclass
class PortfolioConfig:
    max_pct_per_position: float
    max_pct_per_sector: float
    max_positions: int
    min_cash_pct: float


@dataclass
class AlpacaConfig:
    paper: bool
    data_delayed: bool
    # Enable fractional shares for stocks. Alpaca supports fractional qty on
    # market orders for most liquid US equities — essential for small accounts
    # so a $500 stock like NVDA can still be sized to a $50 risk target
    # instead of being rounded to 0 shares.
    fractional_shares: bool = True


@dataclass
class AppConfig:
    """General Web UI and live-engine runtime settings."""

    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True
    log_level: str = "INFO"
    timeframe: str = "15m"
    poll_interval: int = 60


@dataclass
class Config:
    account: AccountConfig
    risk: RiskConfig
    symbols: dict[str, SymbolSpec]
    strategy: StrategyConfig
    backtest: BacktestConfig
    alpaca: AlpacaConfig
    app: AppConfig
    raw: dict[str, Any]
    portfolio: PortfolioConfig | None = None
    strategy_allocations: dict[str, float] | None = None

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

    portfolio_raw = raw.get("portfolio")
    portfolio_cfg: PortfolioConfig | None = None
    if portfolio_raw:
        portfolio_cfg = PortfolioConfig(**portfolio_raw)

    return Config(
        account=AccountConfig(**raw["account"]),
        risk=RiskConfig(**raw["risk"]),
        symbols=symbols,
        strategy=StrategyConfig(**raw["strategy"]),
        backtest=BacktestConfig(**raw["backtest"]),
        alpaca=AlpacaConfig(**raw.get("alpaca", {"paper": True, "data_delayed": False})),
        app=AppConfig(**raw.get("app", {})),
        raw=raw,
        portfolio=portfolio_cfg,
        strategy_allocations=raw.get("strategy_allocations"),
    )
