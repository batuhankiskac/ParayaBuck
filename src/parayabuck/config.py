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
    stop_loss_pct: float = 0.04
    take_profit_pct: float = 0.06
    trail_pct: float = 0.03
    crypto_stop_loss_pct: float = 0.025
    crypto_take_profit_pct: float = 0.04
    atr_stop_multiplier: float = 2.0
    atr_tp_multiplier: float = 4.0


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
    # IEX is real-time and available on Alpaca Basic. Use SIP only when the
    # account has the required market-data subscription.
    stock_feed: str = "iex"


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

    config = Config(
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
    _validate_config(config)
    return config


def _validate_config(config: Config) -> None:
    """Reject unsafe or internally inconsistent runtime settings early."""
    if config.account.starting_balance <= 0:
        raise ValueError("account.starting_balance must be positive")
    if config.risk.max_position_size <= 0:
        raise ValueError("risk.max_position_size must be positive")
    for name in ("max_daily_loss", "max_drawdown", "risk_per_trade"):
        if getattr(config.risk, name) <= 0:
            raise ValueError(f"risk.{name} must be positive")
    for name in (
        "stop_loss_pct",
        "take_profit_pct",
        "trail_pct",
        "crypto_stop_loss_pct",
        "crypto_take_profit_pct",
        "atr_stop_multiplier",
        "atr_tp_multiplier",
    ):
        if getattr(config.risk, name) <= 0:
            raise ValueError(f"risk.{name} must be positive")
    if config.app.timeframe not in {"1m", "5m", "15m", "30m", "1h", "1d"}:
        raise ValueError(f"Unsupported app.timeframe: {config.app.timeframe}")
    if config.app.poll_interval < 5:
        raise ValueError("app.poll_interval must be at least 5 seconds")
    if config.alpaca.stock_feed not in {"iex", "sip", "delayed_sip"}:
        raise ValueError("alpaca.stock_feed must be iex, sip, or delayed_sip")
    for symbol, spec in config.symbols.items():
        if spec.tick_size <= 0 or spec.point_value <= 0:
            raise ValueError(f"symbols.{symbol} tick_size and point_value must be positive")
    if config.strategy_allocations is not None:
        if any(weight < 0 for weight in config.strategy_allocations.values()):
            raise ValueError("strategy_allocations may not contain negative weights")
        if sum(config.strategy_allocations.values()) <= 0:
            raise ValueError("strategy_allocations must have a positive total")
    if config.portfolio is not None:
        for name in ("max_pct_per_position", "max_pct_per_sector", "min_cash_pct"):
            value = getattr(config.portfolio, name)
            if not 0 <= value <= 1:
                raise ValueError(f"portfolio.{name} must be between 0 and 1")
        if config.portfolio.max_positions <= 0:
            raise ValueError("portfolio.max_positions must be positive")
