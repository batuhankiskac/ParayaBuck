from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from parayabuck.core.models import Market
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: float = 0.005
    max_position_pct: float = 0.10
    max_crypto_exposure_pct: float = 0.10
    max_crypto_position_pct: float = 0.05
    max_crypto_positions: int = 2
    minimum_crypto_order_notional: float = 12.0
    maximum_crypto_order_notional: float = 200_000.0
    cash_buffer_pct: float = 0.05
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
            self.max_crypto_position_pct,
            self.max_sector_exposure_pct,
            self.max_gross_exposure_pct,
            self.daily_loss_limit_pct,
            self.max_drawdown_pct,
        )
        if any(not math.isfinite(value) or value <= 0 or value > 1 for value in ratios):
            raise ValueError("Risk ratios must be in (0, 1]")
        if (
            self.max_positions <= 0
            or self.max_crypto_positions <= 0
            or self.max_crypto_positions > self.max_positions
            or self.minimum_crypto_order_notional <= 0
            or self.maximum_crypto_order_notional <= self.minimum_crypto_order_notional
            or not 0 <= self.cash_buffer_pct < 1
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
    crypto_stop_limit_offset_bps: float = 25.0
    crypto_emergency_exit_bps: float = 75.0
    crypto_stop_cancel_timeout_seconds: float = 5.0
    crypto_stop_cancel_poll_interval_seconds: float = 0.25
    strategy_profile: str = "equity_5m_v1"
    allow_short: bool = False
    crypto_min_volume_bar_ratio: float = 0.5
    risk: RiskLimits = field(default_factory=RiskLimits)

    def __post_init__(self) -> None:
        supported = set(TIMEFRAME_MINUTES)
        if self.timeframe not in supported or self.higher_timeframe not in supported:
            raise ValueError("Unsupported timeframe")
        if (
            not math.isfinite(self.minimum_signal_score)
            or not 0 <= self.minimum_signal_score <= 100
            or self.warmup_bars < 50
        ):
            raise ValueError("Invalid signal score or warmup")
        if self.prediction_horizon_bars <= 0:
            raise ValueError("prediction_horizon_bars must be positive")
        if not math.isfinite(self.crypto_min_volume_bar_ratio) or not (
            0 < self.crypto_min_volume_bar_ratio <= 1
        ):
            raise ValueError("crypto_min_volume_bar_ratio must be in (0, 1]")
        if TIMEFRAME_MINUTES[self.higher_timeframe] < TIMEFRAME_MINUTES[self.timeframe]:
            raise ValueError("higher_timeframe must not be smaller than timeframe")
        if self.consecutive_values_invalid:
            raise ValueError("Engine timing and capacity values must be positive")
        if self.max_new_positions_per_cycle != 1:
            raise ValueError("Only one new position per cycle is supported")
        if self.live_trading_enabled:
            if self.trading_enabled and self.strategy_profile != "equity_5m_v1":
                raise ValueError("Broker trading requires the equity_5m_v1 strategy profile")
            if self.trading_enabled and (self.timeframe != "5m" or self.higher_timeframe != "1h"):
                raise ValueError("Broker trading is enabled only for the validated 5m + 1h profile")

    @property
    def consecutive_values_invalid(self) -> bool:
        float_values = (
            self.event_grace_seconds,
            self.data_finalization_grace_seconds,
            self.maximum_signal_age_seconds,
            self.maximum_queue_age_seconds,
            self.account_snapshot_ttl_seconds,
            self.account_poll_interval_seconds,
            self.maximum_account_age_for_entry_seconds,
            self.crypto_stop_limit_offset_bps,
            self.crypto_emergency_exit_bps,
            self.crypto_stop_cancel_timeout_seconds,
            self.crypto_stop_cancel_poll_interval_seconds,
        )
        return (
            any(not math.isfinite(value) for value in float_values)
            or self.event_grace_seconds <= 0
            or self.data_finalization_grace_seconds < 0
            or self.maximum_signal_age_seconds <= 0
            or self.maximum_queue_age_seconds <= 0
            or self.account_snapshot_ttl_seconds <= 0
            or self.account_poll_interval_seconds <= 0
            or self.maximum_account_age_for_entry_seconds <= 0
            or self.event_queue_capacity <= 0
            or self.crypto_stop_limit_offset_bps <= 0
            or self.crypto_emergency_exit_bps <= self.crypto_stop_limit_offset_bps
            or self.crypto_stop_cancel_timeout_seconds <= 0
            or self.crypto_stop_cancel_poll_interval_seconds <= 0
            or self.crypto_stop_cancel_poll_interval_seconds
            > self.crypto_stop_cancel_timeout_seconds
        )


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True
    auto_start_engine: bool = True
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
class EquityScannerConfig:
    minimum_price: float = 5.0
    minimum_median_dollar_volume: float = 20_000_000.0
    minimum_data_completeness: float = 0.95
    minimum_atr_pct: float = 0.005
    maximum_atr_pct: float = 0.12
    maximum_per_sector: int = 5
    volatility_optimum_pct: float = 0.03
    spread_quality_ceiling_bps: float = 50.0
    unknown_sector_penalty: float = 2.0

    def __post_init__(self) -> None:
        if self.minimum_price <= 0 or self.minimum_median_dollar_volume <= 0:
            raise ValueError("Equity scanner price and liquidity thresholds must be positive")
        if not 0 < self.minimum_data_completeness <= 1:
            raise ValueError("Equity scanner completeness must be in (0, 1]")
        if not 0 < self.minimum_atr_pct < self.maximum_atr_pct <= 1:
            raise ValueError("Equity scanner ATR range is invalid")
        if not self.minimum_atr_pct <= self.volatility_optimum_pct <= self.maximum_atr_pct:
            raise ValueError("Equity scanner volatility optimum must be inside the ATR range")
        if self.maximum_per_sector <= 0 or self.spread_quality_ceiling_bps <= 0:
            raise ValueError("Equity scanner sector and spread limits must be positive")
        if self.unknown_sector_penalty < 0:
            raise ValueError("Equity scanner unknown-sector penalty must not be negative")


@dataclass(frozen=True)
class EquityUniverseConfig:
    enabled: bool = True
    dynamic_selection_enabled: bool = True
    maximum_stream_symbols: int = 30
    reference_symbols: tuple[str, ...] = ("SPY",)
    fallback_trade_symbols: tuple[str, ...] = ()
    inverse_trade_symbols: tuple[str, ...] = ()
    candidate_universe_path: str = "data/universe/equity_candidates.json"
    active_universe_path: str = "data/runtime/active_equity_universe.json"
    trade_reference_symbols: bool = False
    active_universe_max_age_hours: float = 36.0
    require_scanner_config_digest_match: bool = True
    scanner: EquityScannerConfig = field(default_factory=EquityScannerConfig)

    def __post_init__(self) -> None:
        from parayabuck.markets.symbols import canonical_symbol

        references = tuple(canonical_symbol(item, Market.EQUITY) for item in self.reference_symbols)
        fallback = tuple(
            canonical_symbol(item, Market.EQUITY) for item in self.fallback_trade_symbols
        )
        inverse = tuple(
            canonical_symbol(item, Market.EQUITY) for item in self.inverse_trade_symbols
        )
        if len(set(references)) != len(references):
            raise ValueError("universe.equity.reference_symbols must be unique")
        if len(set(fallback)) != len(fallback):
            raise ValueError("universe.equity.fallback_trade_symbols must be unique")
        if len(set(inverse)) != len(inverse):
            raise ValueError("universe.equity.inverse_trade_symbols must be unique")
        if not set(inverse).issubset(fallback):
            raise ValueError(
                "universe.equity.inverse_trade_symbols must be included in fallback_trade_symbols"
            )
        from parayabuck.markets.sectors import is_inverse_equity

        if any(not is_inverse_equity(symbol) for symbol in inverse):
            raise ValueError("Only approved -1x inverse equity ETFs may be configured")
        if set(references) & set(fallback):
            raise ValueError(
                "Reference and fallback trade symbols must be unique; use "
                "trade_reference_symbols to make references tradeable"
            )
        if self.maximum_stream_symbols <= 0:
            raise ValueError("universe.equity.maximum_stream_symbols must be positive")
        if self.active_universe_max_age_hours <= 0:
            raise ValueError("universe.equity.active_universe_max_age_hours must be positive")
        if len(references) > self.maximum_stream_symbols:
            raise ValueError("Equity reference symbols exceed maximum_stream_symbols")
        object.__setattr__(self, "reference_symbols", references)
        object.__setattr__(self, "fallback_trade_symbols", fallback)
        object.__setattr__(self, "inverse_trade_symbols", inverse)

    @property
    def configured_stream_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.reference_symbols, *self.fallback_trade_symbols)))

    @property
    def trade_symbols(self) -> tuple[str, ...]:
        if self.trade_reference_symbols:
            return self.configured_stream_symbols
        references = set(self.reference_symbols)
        return tuple(item for item in self.fallback_trade_symbols if item not in references)


@dataclass(frozen=True)
class CryptoScannerConfig:
    historical_timeframe: str = "1h"
    lookback_days: int = 14
    minimum_data_completeness: float = 0.90
    minimum_median_dollar_volume: float = 1_000.0
    minimum_atr_pct: float = 0.004
    maximum_atr_pct: float = 0.20
    maximum_spread_bps: float = 200.0
    maximum_btc_correlation: float = 0.97
    maximum_per_category: int = 3
    maximum_meme_symbols: int = 1
    maximum_high_beta_symbols: int = 2
    volatility_optimum_pct: float = 0.045
    unknown_category_penalty: float = 2.0
    high_correlation_penalty: float = 8.0

    def __post_init__(self) -> None:
        if self.historical_timeframe != "1h":
            raise ValueError("universe.crypto.scanner.historical_timeframe must be 1h")
        if self.lookback_days <= 0:
            raise ValueError("universe.crypto.scanner.lookback_days must be positive")
        ratios = (
            self.minimum_data_completeness,
            self.minimum_atr_pct,
            self.maximum_atr_pct,
            self.maximum_btc_correlation,
            self.volatility_optimum_pct,
        )
        if any(not 0 < value <= 1 for value in ratios):
            raise ValueError("Crypto scanner ratios must be in (0, 1]")
        if self.minimum_atr_pct >= self.maximum_atr_pct:
            raise ValueError("Crypto scanner ATR range is invalid")
        if not self.minimum_atr_pct <= self.volatility_optimum_pct <= self.maximum_atr_pct:
            raise ValueError("Crypto scanner volatility optimum must be inside the ATR range")
        if self.minimum_median_dollar_volume <= 0 or self.maximum_spread_bps <= 0:
            raise ValueError("Crypto scanner liquidity and spread thresholds must be positive")
        if (
            self.maximum_per_category <= 0
            or self.maximum_meme_symbols < 0
            or self.maximum_high_beta_symbols < 0
        ):
            raise ValueError("Crypto scanner category limits are invalid")
        if self.unknown_category_penalty < 0 or self.high_correlation_penalty < 0:
            raise ValueError("Crypto scanner score penalties must not be negative")


@dataclass(frozen=True)
class CryptoUniverseConfig:
    enabled: bool = False
    dynamic_selection_enabled: bool = False
    maximum_stream_symbols: int = 12
    pinned_symbols: tuple[str, ...] = ()
    trade_symbols: tuple[str, ...] = ()
    active_universe_path: str = "data/runtime/active_crypto_universe.json"
    active_universe_max_age_hours: float = 12.0
    require_scanner_config_digest_match: bool = True
    scanner: CryptoScannerConfig = field(default_factory=CryptoScannerConfig)

    def __post_init__(self) -> None:
        from parayabuck.markets.symbols import canonical_symbol, is_stablecoin_pair

        symbols = tuple(canonical_symbol(item, Market.CRYPTO) for item in self.trade_symbols)
        pinned = tuple(canonical_symbol(item, Market.CRYPTO) for item in self.pinned_symbols)
        if len(set(symbols)) != len(symbols):
            raise ValueError("universe.crypto.trade_symbols must be canonical and unique")
        if len(set(pinned)) != len(pinned):
            raise ValueError("universe.crypto.pinned_symbols must be canonical and unique")
        if any(symbol.split("/", 1)[1] != "USD" for symbol in (*symbols, *pinned)):
            raise ValueError("Crypto universe symbols must use USD quote pairs")
        if any(is_stablecoin_pair(symbol) for symbol in (*symbols, *pinned)):
            raise ValueError("Stablecoin base pairs are not allowed in the crypto universe")
        if not set(pinned).issubset(symbols):
            raise ValueError("universe.crypto.pinned_symbols must be included in trade_symbols")
        if self.maximum_stream_symbols <= 0:
            raise ValueError("universe.crypto.maximum_stream_symbols must be positive")
        if len(pinned) > self.maximum_stream_symbols:
            raise ValueError("Pinned crypto symbols exceed maximum_stream_symbols")
        if self.active_universe_max_age_hours <= 0:
            raise ValueError("universe.crypto.active_universe_max_age_hours must be positive")
        object.__setattr__(self, "trade_symbols", symbols)
        object.__setattr__(self, "pinned_symbols", pinned)


@dataclass(frozen=True)
class UniverseConfig:
    equity: EquityUniverseConfig = field(default_factory=EquityUniverseConfig)
    crypto: CryptoUniverseConfig = field(default_factory=CryptoUniverseConfig)


@dataclass(frozen=True)
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    data: DataConfig = field(default_factory=DataConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("At least one enabled market symbol must be configured")
        if self.alpaca.stock_feed == "iex" and (
            self.universe.equity.maximum_stream_symbols > 30
            or len(self.universe.equity.configured_stream_symbols) > 30
        ):
            raise ValueError(
                "Alpaca Basic IEX equity stream symbols, including references, must not exceed 30"
            )
        if (
            len(self.universe.equity.configured_stream_symbols)
            > self.universe.equity.maximum_stream_symbols
        ):
            raise ValueError(
                "Configured equity reference and fallback symbols exceed "
                "universe.equity.maximum_stream_symbols"
            )
        if self.engine.equity_trading_enabled and not self.universe.equity.enabled:
            raise ValueError("equity_trading_enabled requires universe.equity.enabled")
        if self.engine.crypto_trading_enabled and not self.universe.crypto.enabled:
            raise ValueError("crypto_trading_enabled requires universe.crypto.enabled")

    @property
    def symbols(self) -> tuple[str, ...]:
        equity = (
            self.universe.equity.configured_stream_symbols if self.universe.equity.enabled else ()
        )
        crypto = self.universe.crypto.trade_symbols if self.universe.crypto.enabled else ()
        return (*equity, *crypto)


def load_config(config_path: str | None = None) -> Config:
    load_dotenv()
    path = Path(config_path or os.getenv("CONFIG_PATH") or "config/settings.yaml")
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    engine_raw = dict(raw.get("engine", {}))
    risk = RiskLimits(**engine_raw.pop("risk", {}))
    universe_raw = raw.get("universe")
    if universe_raw is None:
        legacy = tuple(raw.get("symbols", ()))
        from parayabuck.markets.symbols import normalize_symbol

        equities: list[str] = []
        crypto: list[str] = []
        for symbol in legacy:
            identity = normalize_symbol(str(symbol))
            (crypto if identity.market == Market.CRYPTO else equities).append(identity.canonical)
        # Legacy symbols were all tradeable. Avoid silently changing SPY ownership semantics.
        equity = EquityUniverseConfig(
            enabled=bool(equities),
            dynamic_selection_enabled=False,
            reference_symbols=(),
            fallback_trade_symbols=tuple(equities),
        )
        crypto_universe = CryptoUniverseConfig(
            enabled=bool(crypto),
            trade_symbols=tuple(crypto),
        )
        engine_raw.setdefault("equity_trading_enabled", bool(equities))
    else:
        universe_dict = dict(universe_raw)
        equity_raw = dict(universe_dict.get("equity", {}))
        equity_scanner = EquityScannerConfig(**dict(equity_raw.pop("scanner", {})))
        equity = EquityUniverseConfig(scanner=equity_scanner, **equity_raw)
        crypto_raw = dict(universe_dict.get("crypto", {}))
        scanner = CryptoScannerConfig(**dict(crypto_raw.pop("scanner", {})))
        crypto_universe = CryptoUniverseConfig(scanner=scanner, **crypto_raw)
    return Config(
        app=AppConfig(**raw.get("app", {})),
        alpaca=AlpacaConfig(**raw.get("alpaca", {})),
        data=DataConfig(**raw.get("data", {})),
        universe=UniverseConfig(equity=equity, crypto=crypto_universe),
        engine=EngineConfig(risk=risk, **engine_raw),
    )


__all__ = [
    "AlpacaConfig",
    "AppConfig",
    "Config",
    "CryptoScannerConfig",
    "CryptoUniverseConfig",
    "DataConfig",
    "EngineConfig",
    "EquityUniverseConfig",
    "RiskLimits",
    "UniverseConfig",
    "load_config",
]
