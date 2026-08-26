from __future__ import annotations

import math
import os
import warnings
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from parayabuck.core.models import Market
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES
from parayabuck.monitoring.events import ALERT_KINDS, ALERT_KINDS_ORDERED, DEFAULT_ALERT_EVENTS

# Single source of truth for the default ledger location: monitoring/metrics
# and the web dashboard fall back to this path when config resolution fails,
# and they must never point at a different database than the writer.
DEFAULT_DECISION_DB_PATH = "data/runtime/parayabuck.sqlite3"


class RuntimeMode(StrEnum):
    DISABLED = "disabled"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class RuntimeConfig:
    mode: RuntimeMode = RuntimeMode.PAPER
    execution_timeframe: str = "5m"
    market_state_timeframe: str = "5m"
    decision_db_path: str = DEFAULT_DECISION_DB_PATH
    scanner_refresh_hours: float = 24.0

    def __post_init__(self) -> None:
        if self.execution_timeframe not in TIMEFRAME_MINUTES:
            raise ValueError("runtime.execution_timeframe is unsupported")
        if self.market_state_timeframe not in TIMEFRAME_MINUTES:
            raise ValueError("runtime.market_state_timeframe is unsupported")
        if self.scanner_refresh_hours <= 0:
            raise ValueError("runtime.scanner_refresh_hours must be positive")


@dataclass(frozen=True)
class MarketStrategyConfig:
    enabled: bool
    name: str
    maximum_allocation: float
    # The strategy owns its signal timeframe: None means "resolve from the
    # selected strategy's primary timeframe at load time". A value here is a
    # deprecated backward-compatibility override that must be one of the
    # strategy's VALIDATED timeframes (validated at load, never reinterpreted).
    timeframe: str | None = None
    parameters: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("strategy name must not be empty")
        if self.timeframe is not None and self.timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(f"Unsupported strategy timeframe: {self.timeframe}")
        if not 0 < self.maximum_allocation <= 1:
            raise ValueError("strategy maximum_allocation must be in (0, 1]")


def _default_equity_strategy() -> MarketStrategyConfig:
    return MarketStrategyConfig(
        enabled=True,
        name="equity_residual_momentum_v1",
        timeframe="1d",
        maximum_allocation=0.50,
        parameters={
            "formation_bars": 252,
            "skip_bars": 21,
            "beta_window_bars": 273,
            "residual_vol_window": 63,
            "top_n": 8,
            "max_single_weight": 0.10,
            "ivol_reject_quantile": 0.70,
            "exclude_symbols": ["SPY"],
        },
    )


def _default_crypto_strategy() -> MarketStrategyConfig:
    return MarketStrategyConfig(
        enabled=False,
        name="crypto_low_variance_v1",
        timeframe="1d",
        maximum_allocation=0.10,
        parameters={
            "variance_lookback_days": 22,
            "top_n": 2,
            "rebalance_hours": 168,
        },
    )


@dataclass(frozen=True)
class StrategyConfig:
    equity: MarketStrategyConfig = field(default_factory=_default_equity_strategy)
    crypto: MarketStrategyConfig = field(default_factory=_default_crypto_strategy)


@dataclass(frozen=True)
class LiveConfig:
    enabled: bool = False
    hard_dollar_cap: float = 0.0
    operator_approval_path: str = ""
    operator_approval_key_env: str = "PARAYABUCK_OPERATOR_APPROVAL_KEY"

    def __post_init__(self) -> None:
        if self.hard_dollar_cap < 0:
            raise ValueError("live.hard_dollar_cap must be non-negative")
        if self.enabled and self.hard_dollar_cap <= 0:
            raise ValueError("Enabled live mode requires a positive hard dollar cap")
        if self.enabled and not self.operator_approval_path:
            raise ValueError("Enabled live mode requires an operator approval file")


# Canonical alert vocabulary is single-sourced in parayabuck.monitoring.events.
# Keep private alias for any external introspection that imported config._ALERT_KINDS.
_ALERT_KINDS = ALERT_KINDS_ORDERED


@dataclass(frozen=True)
class AlertingConfig:
    """Event-notification sink configuration (console/log always on)."""

    enabled: bool = True
    webhook_url: str = ""
    # Default webhook-enabled events exclude high-volume paper_receipt (see events.py).
    # Paper_incident remains enabled; operators may add paper_receipt explicitly.
    events: tuple[str, ...] = DEFAULT_ALERT_EVENTS

    def __post_init__(self) -> None:
        known = set(ALERT_KINDS)
        unknown = set(self.events) - known
        if unknown:
            raise ValueError(f"alerting.events contains unknown kinds: {sorted(unknown)}")
        object.__setattr__(
            self,
            "events",
            tuple(dict.fromkeys(kind for kind in ALERT_KINDS_ORDERED if kind in self.events)),
        )


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    slippage_bps: float = 2.0
    spread_bps: float = 2.0
    commission: float = 0.0
    # Crypto execution uses market orders: a low-volume account pays Alpaca's
    # tier-1 taker rate of 0.25% (25 bps), not a 5 bps limit-order cost.
    crypto_taker_fee_bps: float = 25.0
    # 2026 regulatory rates (SEC Section 31 $20.60/$1M from 2026-04-04, FINRA
    # TAF $0.000195/share cap $9.79 from 2026-01-01, CAT 2026-1 + Historical
    # Assessment 1A $0.000003/share). These are fallbacks for fixed-rate mode;
    # the effective-dated schedule is the default and tracks the official
    # rate changes by trade date.
    equity_taf_per_share: float = 0.000195
    equity_taf_cap: float = 9.79
    equity_cat_per_share: float = 0.000003
    equity_sec_fee_rate: float = 0.0000206
    equity_use_effective_dated_fees: bool = True
    scanner_rebalance: str = "daily"
    output_dir: str = "reports"
    ranking_metric: str = "risk_adjusted"

    def __post_init__(self) -> None:
        costs = (
            self.initial_cash,
            self.slippage_bps,
            self.spread_bps,
            self.commission,
            self.crypto_taker_fee_bps,
            self.equity_taf_per_share,
            self.equity_taf_cap,
            self.equity_cat_per_share,
            self.equity_sec_fee_rate,
        )
        if self.initial_cash <= 0 or any(not math.isfinite(value) or value < 0 for value in costs):
            raise ValueError("Invalid backtest cash or cost configuration")
        if self.scanner_rebalance not in {"daily", "weekly", "monthly"}:
            raise ValueError("backtest.scanner_rebalance must be daily, weekly, or monthly")
        if self.ranking_metric not in {"risk_adjusted", "sharpe", "sortino", "calmar"}:
            raise ValueError("Unsupported backtest ranking metric")


@dataclass(frozen=True)
class ExecutionConfig:
    """Shared order-existence policy: identical semantics for backtest, paper
    and live. Values change order behavior, so they are part of the
    configuration digest and the live operator authorization digest."""

    rebalance_band_pct: float = 0.005
    minimum_order_notional: float = 10.0

    def __post_init__(self) -> None:
        if not 0 <= self.rebalance_band_pct < 1:
            raise ValueError("execution.rebalance_band_pct must be in [0, 1)")
        if not math.isfinite(self.minimum_order_notional) or self.minimum_order_notional < 0:
            raise ValueError("execution.minimum_order_notional must be non-negative")


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = 0.10
    max_crypto_exposure_pct: float = 0.10
    max_crypto_position_pct: float = 0.05
    max_crypto_positions: int = 2
    cash_buffer_pct: float = 0.40
    max_gross_exposure_pct: float = 0.60
    max_positions: int = 5
    daily_loss_limit_pct: float = 0.005
    max_drawdown_pct: float = 0.05

    def __post_init__(self) -> None:
        ratios = (
            self.max_position_pct,
            self.max_crypto_exposure_pct,
            self.max_crypto_position_pct,
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
            or not 0 <= self.cash_buffer_pct < 1
        ):
            raise ValueError("Invalid risk limits")


@dataclass(frozen=True)
class EngineConfig:
    manage_external_positions: bool = False
    event_grace_seconds: float = 5.0
    data_finalization_grace_seconds: float = 2.0
    maximum_queue_age_seconds: float = 30.0
    account_poll_interval_seconds: float = 15.0
    maximum_account_age_for_entry_seconds: float = 30.0
    event_queue_capacity: int = 1024
    crypto_stop_limit_offset_bps: float = 25.0
    crypto_emergency_exit_bps: float = 75.0
    crypto_stop_cancel_timeout_seconds: float = 5.0
    crypto_stop_cancel_poll_interval_seconds: float = 0.25

    def __post_init__(self) -> None:
        if self.timing_capacity_values_invalid:
            raise ValueError("Engine timing and capacity values must be positive")

    @property
    def timing_capacity_values_invalid(self) -> bool:
        float_values = (
            self.event_grace_seconds,
            self.data_finalization_grace_seconds,
            self.maximum_queue_age_seconds,
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
            or self.maximum_queue_age_seconds <= 0
            or self.account_poll_interval_seconds <= 0
            or self.maximum_account_age_for_entry_seconds <= 0
            or self.event_queue_capacity <= 0
            or self.crypto_stop_limit_offset_bps <= 0
            or self.crypto_emergency_exit_bps <= self.crypto_stop_limit_offset_bps
            or self.crypto_stop_cancel_timeout_seconds <= 0
            or self.crypto_stop_cancel_poll_interval_seconds <= 0
            or self.crypto_stop_cancel_poll_interval_seconds > self.crypto_stop_cancel_timeout_seconds
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
    data_delayed: bool = False
    # Real-time Basic stock stream feed. IEX is the free default and reflects
    # IEX exchange prints only, NOT the full consolidated (SIP/NBBO) tape.
    stock_stream_feed: str = "iex"
    # Historical stock bar/quote feed. SIP offers consolidated coverage where
    # the account data entitlement allows it; the client falls back to IEX
    # gracefully when the entitlement is missing.
    stock_historical_feed: str = "sip"

    def __post_init__(self) -> None:
        for name in ("stock_stream_feed", "stock_historical_feed"):
            if getattr(self, name) not in {"iex", "sip", "delayed_sip"}:
                raise ValueError(f"alpaca.{name} must be iex, sip, or delayed_sip")
        # delayed_sip is a historical-feed concept only; live Basic streaming
        # cannot consume it.
        if self.stock_stream_feed == "delayed_sip":
            raise ValueError("alpaca.stock_stream_feed must be iex or sip")


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
    allow_static_fallback_trading: bool = False
    maximum_stream_symbols: int = 30
    target_shortlist: int = 300
    reference_symbols: tuple[str, ...] = ("SPY",)
    fallback_trade_symbols: tuple[str, ...] = ()
    allowlist_symbols: tuple[str, ...] = ()
    denylist_symbols: tuple[str, ...] = ()
    supported_exchanges: tuple[str, ...] = ("NASDAQ", "NYSE", "ARCA", "AMEX", "BATS")
    inverse_trade_symbols: tuple[str, ...] = ()
    candidate_universe_path: str = "data/universe/equity_candidates.json"
    active_universe_path: str = "data/runtime/active_equity_universe.json"
    trade_reference_symbols: bool = False
    active_universe_max_age_hours: float = 36.0
    require_scanner_config_digest_match: bool = True
    # Runtime-only identity of the strategy that produced the persisted
    # strategy-alpha observation set.  These fields are deliberately excluded
    # from the scanner digest: changing alpha must invalidate the observation
    # snapshot without pretending that scanner economics changed.
    observation_strategy_name: str = ""
    observation_strategy_version: str = ""
    observation_strategy_timeframe: str | None = None
    scanner: EquityScannerConfig = field(default_factory=EquityScannerConfig)

    def __post_init__(self) -> None:
        from parayabuck.markets.symbols import canonical_symbol

        references = tuple(canonical_symbol(item, Market.EQUITY) for item in self.reference_symbols)
        fallback = tuple(canonical_symbol(item, Market.EQUITY) for item in self.fallback_trade_symbols)
        inverse = tuple(canonical_symbol(item, Market.EQUITY) for item in self.inverse_trade_symbols)
        allowlist = tuple(canonical_symbol(item, Market.EQUITY) for item in self.allowlist_symbols)
        denylist = tuple(canonical_symbol(item, Market.EQUITY) for item in self.denylist_symbols)
        if len(set(references)) != len(references):
            raise ValueError("universe.equity.reference_symbols must be unique")
        if len(set(fallback)) != len(fallback):
            raise ValueError("universe.equity.fallback_trade_symbols must be unique")
        if len(set(inverse)) != len(inverse):
            raise ValueError("universe.equity.inverse_trade_symbols must be unique")
        if len(set(allowlist)) != len(allowlist) or len(set(denylist)) != len(denylist):
            raise ValueError("Equity allowlist and denylist symbols must be unique")
        if set(allowlist) & set(denylist):
            raise ValueError("Equity allowlist and denylist must not overlap")
        if not set(inverse).issubset(fallback):
            raise ValueError("universe.equity.inverse_trade_symbols must be included in fallback_trade_symbols")
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
        if self.target_shortlist < self.maximum_stream_symbols:
            raise ValueError("universe.equity.target_shortlist must cover the active universe")
        if not self.supported_exchanges:
            raise ValueError("universe.equity.supported_exchanges must not be empty")
        if self.active_universe_max_age_hours <= 0:
            raise ValueError("universe.equity.active_universe_max_age_hours must be positive")
        if len(references) > self.maximum_stream_symbols:
            raise ValueError("Equity reference symbols exceed maximum_stream_symbols")
        object.__setattr__(self, "reference_symbols", references)
        object.__setattr__(self, "fallback_trade_symbols", fallback)
        object.__setattr__(self, "inverse_trade_symbols", inverse)
        object.__setattr__(self, "allowlist_symbols", allowlist)
        object.__setattr__(self, "denylist_symbols", denylist)
        object.__setattr__(
            self,
            "supported_exchanges",
            tuple(dict.fromkeys(exchange.upper() for exchange in self.supported_exchanges)),
        )

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
        if self.maximum_per_category <= 0 or self.maximum_meme_symbols < 0 or self.maximum_high_beta_symbols < 0:
            raise ValueError("Crypto scanner category limits are invalid")
        if self.unknown_category_penalty < 0 or self.high_correlation_penalty < 0:
            raise ValueError("Crypto scanner score penalties must not be negative")


@dataclass(frozen=True)
class CryptoUniverseConfig:
    enabled: bool = False
    dynamic_selection_enabled: bool = False
    allow_static_fallback_trading: bool = False
    maximum_stream_symbols: int = 12
    pinned_symbols: tuple[str, ...] = ()
    trade_symbols: tuple[str, ...] = ()
    allowlist_symbols: tuple[str, ...] = ()
    denylist_symbols: tuple[str, ...] = ()
    active_universe_path: str = "data/runtime/active_crypto_universe.json"
    active_universe_max_age_hours: float = 12.0
    require_scanner_config_digest_match: bool = True
    # Runtime-only identity of the strategy that produced the persisted
    # strategy-alpha observation set. Excluded from scanner digest: changing
    # alpha must invalidate observation without pretending scanner economics
    # changed.
    observation_strategy_name: str = ""
    observation_strategy_version: str = ""
    observation_strategy_timeframe: str | None = None
    observation_strategy_parameters_digest: str = ""
    scanner: CryptoScannerConfig = field(default_factory=CryptoScannerConfig)

    def __post_init__(self) -> None:
        from parayabuck.markets.symbols import canonical_symbol, is_stablecoin_pair

        symbols = tuple(canonical_symbol(item, Market.CRYPTO) for item in self.trade_symbols)
        pinned = tuple(canonical_symbol(item, Market.CRYPTO) for item in self.pinned_symbols)
        allowlist = tuple(canonical_symbol(item, Market.CRYPTO) for item in self.allowlist_symbols)
        denylist = tuple(canonical_symbol(item, Market.CRYPTO) for item in self.denylist_symbols)
        if len(set(symbols)) != len(symbols):
            raise ValueError("universe.crypto.trade_symbols must be canonical and unique")
        if len(set(pinned)) != len(pinned):
            raise ValueError("universe.crypto.pinned_symbols must be canonical and unique")
        if len(set(allowlist)) != len(allowlist) or len(set(denylist)) != len(denylist):
            raise ValueError("Crypto allowlist and denylist symbols must be unique")
        if set(allowlist) & set(denylist):
            raise ValueError("Crypto allowlist and denylist must not overlap")
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
        object.__setattr__(self, "allowlist_symbols", allowlist)
        object.__setattr__(self, "denylist_symbols", denylist)
        # Category metadata integrity: every configured production symbol must
        # have an explicit category; silent unknown_category_penalty is not
        # acceptable for the frozen PAPER pool. 22/22 completeness is mandatory
        # (no ImportError bypass, no enabled-guard).
        if symbols:
            from parayabuck.universe.crypto_metadata import CRYPTO_CATEGORIES

            if len(CRYPTO_CATEGORIES) != 22:
                raise ValueError(f"CRYPTO_CATEGORIES must contain 22 entries, found {len(CRYPTO_CATEGORIES)}")
            missing = [s for s in symbols if s not in CRYPTO_CATEGORIES]
            if missing:
                raise ValueError(
                    f"Missing crypto category metadata for configured trade_symbols: {','.join(missing)}; "
                    "add them to CRYPTO_CATEGORIES"
                )


@dataclass(frozen=True)
class UniverseConfig:
    equity: EquityUniverseConfig = field(default_factory=EquityUniverseConfig)
    crypto: CryptoUniverseConfig = field(default_factory=CryptoUniverseConfig)


@dataclass(frozen=True)
class BackupConfig:
    """Runtime decision-database backup policy (``backup:`` block)."""

    path: str = "data/backups"
    retention_days: float = 7.0

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("backup.path must not be empty")
        if not math.isfinite(self.retention_days) or self.retention_days <= 0:
            raise ValueError("backup.retention_days must be positive")


@dataclass(frozen=True)
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    data: DataConfig = field(default_factory=DataConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskLimits = field(default_factory=RiskLimits)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    alerting: AlertingConfig = field(default_factory=AlertingConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("At least one enabled market symbol must be configured")
        if self.alpaca.stock_stream_feed == "iex" and (
            self.universe.equity.maximum_stream_symbols > 30 or len(self.universe.equity.configured_stream_symbols) > 30
        ):
            raise ValueError("Alpaca Basic IEX equity stream symbols, including references, must not exceed 30")
        if len(self.universe.equity.configured_stream_symbols) > self.universe.equity.maximum_stream_symbols:
            raise ValueError(
                "Configured equity reference and fallback symbols exceed universe.equity.maximum_stream_symbols"
            )
        if self.strategy.equity.enabled and not self.universe.equity.enabled:
            raise ValueError("Enabled equity strategy requires universe.equity.enabled")
        if self.strategy.crypto.enabled and not self.universe.crypto.enabled:
            raise ValueError("Enabled crypto strategy requires universe.crypto.enabled")
        if self.runtime.mode == RuntimeMode.LIVE and not self.live.enabled:
            raise ValueError("runtime.mode=live requires live.enabled=true")
        if self.runtime.mode != RuntimeMode.LIVE and self.live.enabled:
            raise ValueError("live.enabled=true requires runtime.mode=live")

    @property
    def symbols(self) -> tuple[str, ...]:
        equity = self.universe.equity.configured_stream_symbols if self.universe.equity.enabled else ()
        crypto = self.universe.crypto.trade_symbols if self.universe.crypto.enabled else ()
        return (*equity, *crypto)


def _strategy_from_raw(
    defaults: MarketStrategyConfig, enabled_default: bool, raw: dict[str, Any]
) -> MarketStrategyConfig:
    """Merge one ``strategy.<market>`` YAML block over the packaged defaults."""
    base = {
        "enabled": enabled_default,
        "name": defaults.name,
        # No market default timeframe: the STRATEGY supplies it.
        "timeframe": None,
        "maximum_allocation": defaults.maximum_allocation,
        "parameters": defaults.parameters,
    }
    return MarketStrategyConfig(**{**base, **dict(raw)})


def _resolved_timeframe(strategy: MarketStrategyConfig) -> MarketStrategyConfig:
    """Resolve the strategy's signal timeframe from the strategy itself.

    The primary (or a validated alternative explicitly selected in the YAML)
    comes from the strategy class, so a configured name NEVER inherits another
    market default.
    """
    from parayabuck.portfolio import product_strategy_from

    return replace(
        strategy,
        timeframe=product_strategy_from(strategy.name, strategy.parameters, timeframe=strategy.timeframe).timeframe,
    )


def load_config(config_path: str | None = None) -> Config:
    load_dotenv()
    path = Path(config_path or os.getenv("CONFIG_PATH") or "config/settings.yaml")
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    runtime_raw = dict(raw.get("runtime", {}))
    # The pre-v1 ``risk_monitor_timeframe`` key was renamed to
    # ``market_state_timeframe`` (the cadence observes market state; risk
    # reactions live in the entry-risk latch). Accept the old key so an
    # existing operator configuration keeps loading, and normalize it.
    if "risk_monitor_timeframe" in runtime_raw:
        warnings.warn(
            "runtime.risk_monitor_timeframe is deprecated; rename it to runtime.market_state_timeframe (same cadence)",
            DeprecationWarning,
            stacklevel=2,
        )
        runtime_raw.setdefault("market_state_timeframe", runtime_raw.pop("risk_monitor_timeframe"))
    if "mode" in runtime_raw:
        raw_mode = str(runtime_raw["mode"]).lower()
        if raw_mode == "backtest":
            raise ValueError("runtime.mode=backtest is CLI-only; use disabled, paper, or live")
        runtime_raw["mode"] = RuntimeMode(raw_mode)
    engine_raw = dict(raw.get("engine", {}))
    obsolete_engine = {
        "timeframe",
        "higher_timeframe",
        "warmup_bars",
        "ai_logging_enabled",
        "ai_log_path",
        "prediction_horizon_bars",
    } & set(engine_raw)
    if obsolete_engine:
        raise ValueError(
            "Obsolete engine timeframe settings must be removed: "
            + ", ".join(sorted(obsolete_engine))
            + "; use runtime execution/risk roles and strategy timeframes"
        )
    risk_raw = dict(raw.get("risk", {}))
    obsolete_risk = {
        "risk_per_trade_pct",
        "minimum_crypto_order_notional",
        "maximum_crypto_order_notional",
        "max_sector_exposure_pct",
        "max_open_risk_pct",
        "minimum_risk_reward",
        "consecutive_loss_reduction_after",
        "volatility_targeting_enabled",
        "annualized_volatility_target",
        "minimum_volatility_scale",
        "maximum_volatility_scale",
    } & set(risk_raw)
    if obsolete_risk:
        raise ValueError(
            "Obsolete risk settings have no portfolio effect and must be removed: " + ", ".join(sorted(obsolete_risk))
        )
    risk = RiskLimits(**risk_raw)
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
    else:
        universe_dict = dict(universe_raw)
        equity_raw = dict(universe_dict.get("equity", {}))
        equity_scanner = EquityScannerConfig(**dict(equity_raw.pop("scanner", {})))
        equity = EquityUniverseConfig(scanner=equity_scanner, **equity_raw)
        crypto_raw = dict(universe_dict.get("crypto", {}))
        scanner = CryptoScannerConfig(**dict(crypto_raw.pop("scanner", {})))
        crypto_universe = CryptoUniverseConfig(scanner=scanner, **crypto_raw)
    strategy_raw = dict(raw.get("strategy", {}))
    for _market_key in ("equity", "crypto"):
        if _market_key in strategy_raw and "timeframe" in dict(strategy_raw[_market_key]):
            warnings.warn(
                f"strategy.{_market_key}.timeframe is deprecated: the strategy "
                "owns its signal timeframe. The value is still validated "
                "against the selected strategy's supported timeframes and "
                "rejected on mismatch, never silently reinterpreted.",
                DeprecationWarning,
                stacklevel=2,
            )
    # Fail-closed: when a market universe is enabled the corresponding
    # strategy.<market> block MUST be present with an explicit name **if that
    # market's strategy is intended to trade**.  A disabled strategy
    # (explicit ``enabled: false``) may omit the name — the loader will fill
    # the packaged default name but keep it disabled, so no silent v1
    # selection can produce orders.  Dataclass defaults remain for direct
    # construction, but YAML loading is strict for enabled markets.
    if equity.enabled:
        _eq_block = strategy_raw.get("equity")
        _eq_enabled = True if not isinstance(_eq_block, dict) else bool(_eq_block.get("enabled", True))
        if _eq_enabled:
            if not isinstance(_eq_block, dict) or not str(_eq_block.get("name", "")).strip():
                raise ValueError("strategy.equity block with name is required when universe.equity.enabled is true")
    if crypto_universe.enabled:
        _cr_block = strategy_raw.get("crypto")
        _cr_enabled = True if not isinstance(_cr_block, dict) else bool(_cr_block.get("enabled", True))
        if _cr_enabled:
            if not isinstance(_cr_block, dict) or not str(_cr_block.get("name", "")).strip():
                raise ValueError("strategy.crypto block with name is required when universe.crypto.enabled is true")
    default_strategy = StrategyConfig()
    equity_strategy = _strategy_from_raw(
        default_strategy.equity,
        default_strategy.equity.enabled if "equity" in strategy_raw else equity.enabled,
        strategy_raw.get("equity", {}),
    )
    crypto_strategy = _strategy_from_raw(
        default_strategy.crypto,
        default_strategy.crypto.enabled if "crypto" in strategy_raw else crypto_universe.enabled,
        strategy_raw.get("crypto", {}),
    )
    equity_strategy = _resolved_timeframe(equity_strategy)
    crypto_strategy = _resolved_timeframe(crypto_strategy)
    if equity_strategy.enabled and equity.dynamic_selection_enabled:
        from parayabuck.portfolio import product_strategy_from

        resolved_equity = product_strategy_from(
            equity_strategy.name,
            equity_strategy.parameters,
            timeframe=equity_strategy.timeframe,
        )
        equity = replace(
            equity,
            observation_strategy_name=resolved_equity.name,
            observation_strategy_version=resolved_equity.version,
            observation_strategy_timeframe=resolved_equity.timeframe,
        )
    if crypto_strategy.enabled and crypto_universe.dynamic_selection_enabled:
        from parayabuck.portfolio import product_strategy_from

        resolved_crypto = product_strategy_from(
            crypto_strategy.name,
            crypto_strategy.parameters,
            timeframe=crypto_strategy.timeframe,
        )
        import hashlib
        import json as _json

        params_digest = hashlib.sha256(
            _json.dumps(crypto_strategy.parameters, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        crypto_universe = replace(
            crypto_universe,
            observation_strategy_name=resolved_crypto.name,
            observation_strategy_version=resolved_crypto.version,
            observation_strategy_timeframe=resolved_crypto.timeframe,
            observation_strategy_parameters_digest=params_digest,
        )
    alpaca_raw = dict(raw.get("alpaca", {}))
    legacy_paper = alpaca_raw.pop("paper", None)
    if legacy_paper is False:
        raise ValueError(
            "alpaca.paper=false is unsafe and no longer supported; configure "
            "runtime.mode=live plus live.enabled and live safety controls"
        )
    return Config(
        app=AppConfig(**raw.get("app", {})),
        runtime=RuntimeConfig(**runtime_raw),
        alpaca=AlpacaConfig(**alpaca_raw),
        data=DataConfig(**raw.get("data", {})),
        universe=UniverseConfig(equity=equity, crypto=crypto_universe),
        strategy=StrategyConfig(equity_strategy, crypto_strategy),
        risk=risk,
        backtest=BacktestConfig(**dict(raw.get("backtest", {}))),
        live=LiveConfig(**dict(raw.get("live", {}))),
        engine=EngineConfig(**engine_raw),
        execution=ExecutionConfig(**dict(raw.get("execution", {}))),
        backup=BackupConfig(**dict(raw.get("backup", {}))),
        alerting=AlertingConfig(**dict(raw.get("alerting", {}))),
    )


__all__ = [
    "AlertingConfig",
    "AlpacaConfig",
    "AppConfig",
    "BacktestConfig",
    "BackupConfig",
    "Config",
    "CryptoScannerConfig",
    "CryptoUniverseConfig",
    "DataConfig",
    "EngineConfig",
    "EquityUniverseConfig",
    "ExecutionConfig",
    "LiveConfig",
    "MarketStrategyConfig",
    "RiskLimits",
    "RuntimeConfig",
    "RuntimeMode",
    "StrategyConfig",
    "UniverseConfig",
    "load_config",
]
