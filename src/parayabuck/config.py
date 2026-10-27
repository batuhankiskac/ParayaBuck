from __future__ import annotations

import math
import os
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from parayabuck.core.models import Market
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES

# Single source of truth for the default ledger location: monitoring/metrics
# and the web dashboard fall back to this path when config resolution fails,
# and they must never point at a different database than the writer.
DEFAULT_DECISION_DB_PATH = "data/runtime/parayabuck.sqlite3"
# LIVE never shares the PAPER ledger: a LIVE configuration that keeps the
# PAPER default path is resolved to this separate directory, which also gives
# LIVE its own runtime-state directory (risk peak sidecar) next to it.
DEFAULT_LIVE_DECISION_DB_PATH = "data/runtime/live/parayabuck.sqlite3"


class RuntimeMode(StrEnum):
    DISABLED = "disabled"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class RuntimeConfig:
    mode: RuntimeMode = RuntimeMode.PAPER
    decision_db_path: str = DEFAULT_DECISION_DB_PATH
    # Default off. When on, a stale account snapshot that is the ONLY readiness
    # blocker allows a sell-only decision (buys held, sells capped at the bot's
    # owned quantity) instead of holding the whole rebalance, sells included.
    stale_account_sell_only: bool = False
    # Default off (whole-runtime ownership-drift halt). When on, an unexplained
    # broker shortfall locks only the drifting symbol: its buys and sells are
    # refused while other symbols keep trading, and the lock clears itself when
    # the broker again covers the owned quantity. Locks are in memory only.
    per_symbol_drift_lock: bool = False

    def __post_init__(self) -> None:
        # LIVE and PAPER state must never mix: the PAPER default ledger path
        # (explicit or implicit) resolves to the separate LIVE default in
        # live mode. A different explicit path is kept as configured.
        if self.mode == RuntimeMode.LIVE and _same_path(self.decision_db_path, DEFAULT_DECISION_DB_PATH):
            object.__setattr__(self, "decision_db_path", DEFAULT_LIVE_DECISION_DB_PATH)


def _same_path(first: str, second: str) -> bool:
    return os.path.normpath(str(first)) == os.path.normpath(str(second))


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
        if not math.isfinite(self.maximum_allocation) or not 0 < self.maximum_allocation <= 1:
            raise ValueError("strategy maximum_allocation must be in (0, 1]")


def _default_equity_strategy() -> MarketStrategyConfig:
    return MarketStrategyConfig(
        enabled=True,
        name="equity_leveraged_trend_v1",
        timeframe="1d",
        # Defaults mirror the shipped identity (config/settings.yaml): a
        # deleted yaml key must not silently select an unvalidated strategy.
        maximum_allocation=0.98,
        # ``tsm_bars``/``donchian_bars`` are left to the strategy class
        # (252/126) so a YAML block selecting ``trend_rule: sma`` (VT35) does
        # not inherit majority-only keys it would reject; that rule-specific
        # inheritance is dropped by ``_inheritable_default_parameters`` for the
        # keys the shipped identity must mirror (``early_reentry_weight``).
        parameters={
            "signal_symbol": "QQQ",
            "hold_symbol": "QLD",
            "sma_bars": 200,
            "vol_target": 0.35,
            "vol_window": 20,
            "trend_rule": "majority_sma_tsm_don",
            # 2026-10-14 (R2, docs/research/fast_reentry_integration_v1.md).
            "early_reentry_weight": 0.5,
        },
    )


@dataclass(frozen=True)
class StrategyConfig:
    equity: MarketStrategyConfig = field(default_factory=_default_equity_strategy)


@dataclass(frozen=True)
class LiveConfig:
    enabled: bool = False
    hard_dollar_cap: float = 0.0
    operator_approval_path: str = ""
    operator_approval_key_env: str = "PARAYABUCK_OPERATOR_APPROVAL_KEY"
    # LIVE-only gap-safe BUY cap (docs/live_readiness_v1.md R1): a BUY batch
    # is capped so that notional x (1 + margin) fits inside
    # min(cash, non_marginable_buying_power) minus committed BUYs, so a
    # gap-up open of up to this fraction never borrows on margin. PAPER
    # sizing is unchanged (the PAPER soak keeps its identity).
    buy_gap_margin_pct: float = 0.03

    def __post_init__(self) -> None:
        if not math.isfinite(self.hard_dollar_cap) or self.hard_dollar_cap < 0:
            raise ValueError("live.hard_dollar_cap must be non-negative")
        if not math.isfinite(self.buy_gap_margin_pct) or not 0 <= self.buy_gap_margin_pct < 0.5:
            raise ValueError("live.buy_gap_margin_pct must be in [0, 0.5)")
        if self.enabled and self.hard_dollar_cap <= 0:
            raise ValueError("Enabled live mode requires a positive hard dollar cap")
        if self.enabled and not self.operator_approval_path:
            raise ValueError("Enabled live mode requires an operator approval file")


EMAIL_SECURITY_MODES = ("starttls", "ssl")


def _plausible_address(value: str) -> bool:
    text = str(value).strip()
    local, _, domain = text.partition("@")
    return bool(local and domain and "." in domain and not any(char in text for char in "\r\n,; <>"))


@dataclass(frozen=True)
class EmailAlertConfig:
    """Opt-in e-mail delivery of operator alerts (``alerting.email:``).

    Disabled by default: alerts stay on the host (log + event journal) unless
    the owner enables this block. SMTP runs over TLS only (STARTTLS, usually
    port 587, or implicit TLS ``ssl``, usually 465) with the stdlib. The SMTP
    username and password are read ONLY from the environment
    (``PARAYABUCK_SMTP_USERNAME`` / ``PARAYABUCK_SMTP_PASSWORD``), never from
    this file, and are never logged.
    """

    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    security: str = "starttls"
    from_address: str = ""
    to_addresses: tuple[str, ...] = ()
    # Also e-mail the one-line daily summary after each session-close decision.
    daily_summary: bool = True
    # Per-incident repeat window (same kind + correlation + reason + state).
    min_interval_minutes: float = 30.0
    # Hard ceiling across all kinds; extra mails are dropped and journaled.
    max_per_hour: int = 20
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        raw_to = self.to_addresses
        if isinstance(raw_to, str):
            raw_to = (raw_to,)
        recipients = tuple(str(item).strip() for item in (raw_to or ()) if str(item).strip())
        object.__setattr__(self, "to_addresses", recipients)
        object.__setattr__(self, "smtp_host", str(self.smtp_host or "").strip())
        object.__setattr__(self, "from_address", str(self.from_address or "").strip())
        object.__setattr__(self, "security", str(self.security or "").strip().lower())
        if self.security not in EMAIL_SECURITY_MODES:
            raise ValueError("alerting.email.security must be starttls or ssl")
        if isinstance(self.smtp_port, bool) or not isinstance(self.smtp_port, int) or not 1 <= self.smtp_port <= 65535:
            raise ValueError("alerting.email.smtp_port must be an integer in 1..65535")
        for name in ("min_interval_minutes", "timeout_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"alerting.email.{name} must be non-negative")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise ValueError("alerting.email.timeout_seconds must be in (0, 60]")
        if self.max_per_hour < 1:
            raise ValueError("alerting.email.max_per_hour must be at least 1")
        if not self.enabled:
            return
        if not self.smtp_host or any(char in self.smtp_host for char in " /\r\n"):
            raise ValueError("alerting.email.smtp_host is required when e-mail alerts are enabled")
        if not _plausible_address(self.from_address):
            raise ValueError("alerting.email.from_address must be one e-mail address")
        if not self.to_addresses:
            raise ValueError("alerting.email.to_addresses needs at least one recipient")
        bad = [item for item in self.to_addresses if not _plausible_address(item)]
        if bad:
            raise ValueError(f"alerting.email.to_addresses has an invalid address: {bad[0]}")


@dataclass(frozen=True)
class AlertingConfig:
    """Alert sink configuration: log + event journal always; e-mail opt-in."""

    # One-line summary (trend vs SMA, target vs held weight, positions,
    # equity, daily change) after each equity session-close decision,
    # written to the log and the event journal.
    daily_summary: bool = True
    email: EmailAlertConfig = field(default_factory=EmailAlertConfig)

    def __post_init__(self) -> None:
        if isinstance(self.email, dict):
            object.__setattr__(self, "email", EmailAlertConfig(**self.email))
        elif self.email is None:
            object.__setattr__(self, "email", EmailAlertConfig())
        elif not isinstance(self.email, EmailAlertConfig):
            # ``email: true`` must not silently disable delivery.
            raise ValueError("alerting.email must be a mapping")


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    slippage_bps: float = 2.0
    spread_bps: float = 2.0
    commission: float = 0.0
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
    output_dir: str = "reports"
    ranking_metric: str = "risk_adjusted"

    def __post_init__(self) -> None:
        costs = (
            self.initial_cash,
            self.slippage_bps,
            self.spread_bps,
            self.commission,
            self.equity_taf_per_share,
            self.equity_taf_cap,
            self.equity_cat_per_share,
            self.equity_sec_fee_rate,
        )
        if self.initial_cash <= 0 or any(not math.isfinite(value) or value < 0 for value in costs):
            raise ValueError("Invalid backtest cash or cost configuration")
        if self.ranking_metric not in {"risk_adjusted", "sharpe", "sortino", "calmar"}:
            raise ValueError("Unsupported backtest ranking metric")


# Operator-safety ceilings (audit V6). Each one still admits the shipped
# config/settings.yaml value; a value above it is almost certainly a typo or
# a setting that silently disables the control it governs.
_MAX_BAND_PCT = 0.2
_MAX_MINIMUM_ORDER_NOTIONAL = 1000.0
_MAX_DECEMBER_DEFERRAL_SESSIONS = 20
_MAX_DAILY_LOSS_LIMIT_PCT = 0.5
_MAX_DRAWDOWN_PCT = 0.9
# The web watchdog gate (web/managers/engine.py, _WATCHDOG_ACCOUNT_LOOP_MAX_AGE_SECONDS)
# allows 120 s per account-loop pass; the poll sleep is part of that gap, so
# keep half of it free for the pass itself.
_MAX_ACCOUNT_POLL_INTERVAL_SECONDS = 60.0
_MAX_DECISION_RETRY_WINDOW_MINUTES = 60.0
_MAX_VOL_TARGET_CONFIG = 0.5


@dataclass(frozen=True)
class DownsizeGainBand:
    """Gain-scaled downsize band (A1u, ``docs/research/gain_band_v3.md``).

    A downward resize between two positive weights fills only when the drift
    exceeds the band of the tier selected by the embedded USD gain fraction
    ``g_frac`` of the units the sell would consume: ``bands[0]`` while
    ``g_frac <= thresholds[0]``, then one band per threshold, and ``bands[-1]``
    above the last threshold. Entries, exits to zero and up-resizes are never
    affected. The schedule is a tax-timing rule: it postpones discretionary
    resizes that would realise an embedded gain.
    """

    thresholds: tuple[float, ...] = (0.01, 0.05)
    bands: tuple[float, ...] = (0.005, 0.03, 0.08)

    def __post_init__(self) -> None:
        if len(self.bands) != len(self.thresholds) + 1:
            raise ValueError("execution.downsize_gain_band needs one more band than thresholds")
        if any(not math.isfinite(value) or not 0 < value < 1 for value in self.thresholds):
            raise ValueError("execution.downsize_gain_band thresholds must be in (0, 1)")
        if any(left >= right for left, right in zip(self.thresholds, self.thresholds[1:], strict=False)):
            raise ValueError("execution.downsize_gain_band thresholds must be strictly increasing")
        # A percent typo (0.5 for 0.5 %) must not become a 50 % band.
        if any(not math.isfinite(value) or not 0 <= value <= _MAX_BAND_PCT for value in self.bands):
            raise ValueError(f"execution.downsize_gain_band bands must be in [0, {_MAX_BAND_PCT:g}]")
        if any(left > right for left, right in zip(self.bands, self.bands[1:], strict=False)):
            raise ValueError("execution.downsize_gain_band bands must be non-decreasing")

    def band_for(self, g_frac: float) -> float:
        """``B_down(g_frac)``: the band of the tier ``g_frac`` falls into."""
        for threshold, band in zip(self.thresholds, self.bands, strict=False):
            if g_frac <= threshold:
                return band
        return self.bands[-1]


@dataclass(frozen=True)
class ExecutionConfig:
    """Shared order-existence policy: identical semantics for backtest, paper
    and live. Values change order behavior, so they are part of the
    configuration digest and the live operator authorization digest.

    ``downsize_gain_band`` defaults to the shipped A1u schedule (mirroring
    ``config/settings.yaml``, like the R2 strategy parameter): a deleted or
    renamed YAML key must not silently drop a shipped order-existence rule.
    Set the key explicitly to ``null`` to restore the constant band.
    ``december_downsize_deferral_sessions`` (H2,
    ``docs/research/band_h2_combo_v1.md``) defaults to the shipped 10 for the
    same reason; ``0`` disables the deferral.
    """

    rebalance_band_pct: float = 0.005
    minimum_order_notional: float = 10.0
    downsize_gain_band: DownsizeGainBand | None = DownsizeGainBand()
    december_downsize_deferral_sessions: int = 10

    def __post_init__(self) -> None:
        if not math.isfinite(self.rebalance_band_pct) or not 0 <= self.rebalance_band_pct <= _MAX_BAND_PCT:
            raise ValueError(f"execution.rebalance_band_pct must be in [0, {_MAX_BAND_PCT:g}]")
        if (
            not math.isfinite(self.minimum_order_notional)
            or not 0 <= self.minimum_order_notional <= _MAX_MINIMUM_ORDER_NOTIONAL
        ):
            raise ValueError(f"execution.minimum_order_notional must be in [0, {_MAX_MINIMUM_ORDER_NOTIONAL:g}]")
        schedule = self.downsize_gain_band
        if schedule is not None and not isinstance(schedule, DownsizeGainBand):
            raise ValueError("execution.downsize_gain_band must be a schedule or null")
        # A gain-band tier replaces the constant band for downsizes, so a tier
        # below rebalance_band_pct would let those downsizes fill at a smaller
        # drift than the operator's base band allows.
        if schedule is not None and any(band < self.rebalance_band_pct for band in schedule.bands):
            raise ValueError("execution.downsize_gain_band bands must be at least rebalance_band_pct")
        if (
            isinstance(self.december_downsize_deferral_sessions, bool)
            or not isinstance(self.december_downsize_deferral_sessions, int)
            or self.december_downsize_deferral_sessions < 0
        ):
            raise ValueError("execution.december_downsize_deferral_sessions must be a non-negative integer")
        # December has about 21 NYSE sessions; a larger window defers the whole month.
        if self.december_downsize_deferral_sessions > _MAX_DECEMBER_DEFERRAL_SESSIONS:
            raise ValueError(
                f"execution.december_downsize_deferral_sessions must be at most {_MAX_DECEMBER_DEFERRAL_SESSIONS}"
            )


@dataclass(frozen=True)
class DrawdownLiquidationConfig:
    """Optional catastrophe-brake liquidation (``risk.drawdown_liquidation``).

    Off by default. When enabled, a drawdown from peak equity at or above
    ``threshold_pct`` sets every MANAGED target to zero (external inventory is
    never sold). The brake re-arms once the drawdown recovers below
    ``rearm_recovery_pct`` and ``cooldown_sessions`` sessions have passed in
    cash. Research (IC49 drawdown governor) rejected exposure-scaling
    governors; this is a catastrophe brake, not a scaling rule.
    """

    enabled: bool = False
    threshold_pct: float = 0.45
    rearm_recovery_pct: float = 0.30
    cooldown_sessions: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("risk.drawdown_liquidation.enabled must be a boolean")
        if not math.isfinite(self.threshold_pct) or not 0 < self.threshold_pct < 1:
            raise ValueError("risk.drawdown_liquidation.threshold_pct must be in (0, 1)")
        if not math.isfinite(self.rearm_recovery_pct) or not 0 <= self.rearm_recovery_pct < self.threshold_pct:
            raise ValueError("risk.drawdown_liquidation.rearm_recovery_pct must be in [0, threshold_pct)")
        if (
            isinstance(self.cooldown_sessions, bool)
            or not isinstance(self.cooldown_sessions, int)
            or self.cooldown_sessions < 0
        ):
            raise ValueError("risk.drawdown_liquidation.cooldown_sessions must be a non-negative integer")


@dataclass(frozen=True)
class RiskLimits:
    # Defaults mirror the shipped identity (config/settings.yaml, VT35
    # paper_config_v2): a deleted yaml key must not install the pre-VT35
    # 0.5 % daily latch, which trips near-daily on a 2x ETF.
    max_position_pct: float = 0.98
    cash_buffer_pct: float = 0.02
    max_gross_exposure_pct: float = 0.98
    max_positions: int = 8
    daily_loss_limit_pct: float = 0.20
    max_drawdown_pct: float = 0.75
    # Default-off catastrophe brake; absent key keeps the shipped behaviour.
    drawdown_liquidation: DrawdownLiquidationConfig = field(default_factory=DrawdownLiquidationConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.drawdown_liquidation, DrawdownLiquidationConfig):
            raise ValueError("risk.drawdown_liquidation must be a mapping")
        ratios = (
            self.max_position_pct,
            self.max_gross_exposure_pct,
            self.daily_loss_limit_pct,
            self.max_drawdown_pct,
        )
        if any(not math.isfinite(value) or value <= 0 or value > 1 for value in ratios):
            raise ValueError("Risk ratios must be in (0, 1]")
        # A 100 % loss or drawdown latch can never trip: reject it instead of
        # shipping a safety control that cannot fire.
        if self.daily_loss_limit_pct >= 1 or self.max_drawdown_pct >= 1:
            raise ValueError("risk.daily_loss_limit_pct and risk.max_drawdown_pct must be below 1")
        # A 2x ETF needs a usable latch: above these ceilings the halt cannot
        # trip before the account is nearly wiped out.
        if self.daily_loss_limit_pct > _MAX_DAILY_LOSS_LIMIT_PCT:
            raise ValueError(f"risk.daily_loss_limit_pct must be at most {_MAX_DAILY_LOSS_LIMIT_PCT:g}")
        if self.max_drawdown_pct > _MAX_DRAWDOWN_PCT:
            raise ValueError(f"risk.max_drawdown_pct must be at most {_MAX_DRAWDOWN_PCT:g}")
        # A float such as 8.0 passes the comparisons below, then raises
        # TypeError on every decision; reject it at load instead.
        if isinstance(self.max_positions, bool) or not isinstance(self.max_positions, int):
            raise ValueError("risk.max_positions must be an integer")
        if self.max_positions <= 0 or not math.isfinite(self.cash_buffer_pct) or not 0 <= self.cash_buffer_pct < 1:
            raise ValueError("Invalid risk limits")


@dataclass(frozen=True)
class EngineConfig:
    event_grace_seconds: float = 5.0
    data_finalization_grace_seconds: float = 2.0
    maximum_queue_age_seconds: float = 30.0
    account_poll_interval_seconds: float = 15.0
    maximum_account_age_for_entry_seconds: float = 30.0
    event_queue_capacity: int = 1024
    # Consecutive TRANSIENT (network-class) reconciliation failures before
    # the reconciliation breaker is raised and its incident persisted. One
    # Alpaca timeout must not turn into a cross-restart halt; the decisions
    # it blocks are re-queued for retry instead (see
    # ``decision_retry_*``), so a single blip costs no session.
    reconcile_failure_threshold: int = 3
    # A session-close decision blocked by a RECOVERABLE gate (breaker,
    # reconcile failure, readiness gate, invalidated account-snapshot lease,
    # transient account fetch) is re-queued and retried on this cadence
    # until ``decision_retry_window_minutes`` after the session's NYSE close
    # (16:00 ET, 13:00 ET on an early close).
    decision_retry_interval_seconds: float = 60.0
    decision_retry_window_minutes: float = 40.0

    def __post_init__(self) -> None:
        if self.timing_capacity_values_invalid:
            raise ValueError("Engine timing and capacity values must be positive")
        if self.reconcile_failure_threshold < 1:
            raise ValueError("engine.reconcile_failure_threshold must be at least 1")
        # A retry cadence longer than the retry window never retries.
        if self.decision_retry_interval_seconds > self.decision_retry_window_minutes * 60:
            raise ValueError("engine.decision_retry_interval_seconds must not exceed decision_retry_window_minutes")
        if self.decision_retry_window_minutes > _MAX_DECISION_RETRY_WINDOW_MINUTES:
            raise ValueError(
                f"engine.decision_retry_window_minutes must be at most {_MAX_DECISION_RETRY_WINDOW_MINUTES:g}"
            )
        # The account loop sleeps this long between passes; above the gate the
        # web watchdog restarts the unit in a loop (and loses in-memory halts).
        if self.account_poll_interval_seconds > _MAX_ACCOUNT_POLL_INTERVAL_SECONDS:
            raise ValueError(
                f"engine.account_poll_interval_seconds must be at most {_MAX_ACCOUNT_POLL_INTERVAL_SECONDS:g}"
            )
        # A snapshot can never be fresh enough for entries if it is older than
        # the poll that refreshes it, so every entry would be blocked.
        if self.maximum_account_age_for_entry_seconds < self.account_poll_interval_seconds:
            raise ValueError(
                "engine.maximum_account_age_for_entry_seconds must be at least account_poll_interval_seconds"
            )

    @property
    def timing_capacity_values_invalid(self) -> bool:
        float_values = (
            self.event_grace_seconds,
            self.data_finalization_grace_seconds,
            self.maximum_queue_age_seconds,
            self.account_poll_interval_seconds,
            self.maximum_account_age_for_entry_seconds,
            self.decision_retry_interval_seconds,
            self.decision_retry_window_minutes,
        )
        return (
            any(not math.isfinite(value) for value in float_values)
            or self.event_grace_seconds <= 0
            or self.data_finalization_grace_seconds < 0
            or self.maximum_queue_age_seconds <= 0
            or self.account_poll_interval_seconds <= 0
            or self.maximum_account_age_for_entry_seconds <= 0
            or self.event_queue_capacity <= 0
            or self.decision_retry_interval_seconds <= 0
            or self.decision_retry_window_minutes <= 0
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
class BrokerConfig:
    """Broker identity pins (``broker:`` block).

    ``expected_account_id`` is the Alpaca account number (the ``PA...`` value)
    the engine must trade. ``None`` keeps the historical behaviour; when set,
    any other broker account refuses startup in PAPER and LIVE alike.
    """

    expected_account_id: str | None = None

    def __post_init__(self) -> None:
        value = str(self.expected_account_id or "").strip()
        object.__setattr__(self, "expected_account_id", value or None)


@dataclass(frozen=True)
class DataConfig:
    """Placeholder ``data:`` block (kept so old configs still parse)."""


def dotenv_path() -> Path:
    """The one credential file: ``ENV_PATH`` or ``.env`` in the working directory.

    Never an upward search: a path-less ``load_dotenv()`` walks up from the
    CWD and would honor a planted ancestor ``.env`` (credentials, CONFIG_PATH).
    """
    return Path(os.getenv("ENV_PATH") or ".env")


def load_env_file(*, override: bool = False) -> None:
    """Load :func:`dotenv_path` (a missing file is a no-op)."""
    path = dotenv_path()
    if path.is_file():
        load_dotenv(path, override=override)


CREDENTIALS_DIRECTORY_ENV = "CREDENTIALS_DIRECTORY"


def credential_from_directory(name: str, env: Mapping[str, str | None] | None = None) -> str | None:
    """Secret read from ``$CREDENTIALS_DIRECTORY/<name>`` (systemd credentials).

    ``LoadCredential=`` and ``LoadCredentialEncrypted=`` expose each secret as
    a file in the unit's private credentials directory. Returns ``None`` when
    the variable is unset, the file is missing or unreadable, or it is empty,
    so callers fall back to the plain environment variable.
    """
    source: Mapping[str, str | None] = os.environ if env is None else env
    directory = str(source.get(CREDENTIALS_DIRECTORY_ENV) or "").strip()
    if not directory or not name or "/" in name or name in {".", ".."}:
        return None
    try:
        value = (Path(directory) / name).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value or None


def read_credential(name: str, env: Mapping[str, str | None] | None = None) -> str:
    """Credential ``name``: the systemd credential file first, then the env var.

    Returns the stripped value, or an empty string when neither is set.
    """
    source: Mapping[str, str | None] = os.environ if env is None else env
    return credential_from_directory(name, source) or str(source.get(name) or "").strip()


def credential_file_mode_warning(path: Path) -> str | None:
    """Warning text when the credential file is group- or world-readable.

    New files are written ``0o600``; a pre-existing looser file is reported
    (not refused, so a VM restart never fails on it).
    """
    try:
        mode = path.stat().st_mode
    except OSError:
        return None
    if mode & 0o077:
        return (
            f"Credential file {path} is accessible by group/other users (mode {mode & 0o777:o}); run chmod 600 {path}"
        )
    return None


@dataclass(frozen=True)
class EquityUniverseConfig:
    """Static equity universe: the configured symbols are streamed and tradable.

    The equity strategy reads a fixed signal/hold pair, so there is no dynamic
    equity scanner. Managed positions outside ``symbols`` are pinned by the
    runtime until they are sold.
    """

    enabled: bool = True
    maximum_stream_symbols: int = 30
    symbols: tuple[str, ...] = ("SPY", "QQQ", "QLD")

    def __post_init__(self) -> None:
        from parayabuck.markets.symbols import canonical_symbol

        symbols = tuple(canonical_symbol(item, Market.EQUITY) for item in self.symbols)
        if len(set(symbols)) != len(symbols):
            raise ValueError("universe.equity.symbols must be unique")
        if self.maximum_stream_symbols <= 0:
            raise ValueError("universe.equity.maximum_stream_symbols must be positive")
        if len(symbols) > self.maximum_stream_symbols:
            raise ValueError("universe.equity.symbols exceed maximum_stream_symbols")
        object.__setattr__(self, "symbols", symbols)


@dataclass(frozen=True)
class UniverseConfig:
    equity: EquityUniverseConfig = field(default_factory=EquityUniverseConfig)


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
class ObservabilityConfig:
    """Local observability visibility/history/diagnostics (``observability:`` block).

    Alerting (``alerting``) remains notification delivery; observability is
    local visibility/history. Keep knobs minimal.

    Every field here is applied, not just validated: the runtime constructor
    hands this block to ``journal.configure_global_journal`` (journal_path,
    event_retention_days, event_max_events, event_queue_size) and builds the
    snapshot writer from it (snapshot_path, snapshot_interval_seconds).
    """

    snapshot_interval_seconds: float = 30.0
    event_retention_days: int = 30
    event_queue_size: int = 2048
    event_max_events: int = 50000
    journal_path: str = "data/runtime/observability.sqlite3"
    snapshot_path: str = "data/runtime/health_snapshot.json"

    def __post_init__(self) -> None:
        if not math.isfinite(self.snapshot_interval_seconds) or self.snapshot_interval_seconds <= 0:
            raise ValueError("observability.snapshot_interval_seconds must be positive")
        if self.event_retention_days < 0:
            raise ValueError("observability.event_retention_days must be non-negative")
        if self.event_queue_size <= 0:
            raise ValueError("observability.event_queue_size must be positive")
        if self.event_max_events < 0:
            raise ValueError("observability.event_max_events must be non-negative")
        if not self.journal_path.strip():
            raise ValueError("observability.journal_path must not be empty")
        if not self.snapshot_path.strip():
            raise ValueError("observability.snapshot_path must not be empty")


@dataclass(frozen=True)
class IntentWatchdogConfig:
    """Intent-vs-reality watchdog (``intent_watchdog:`` block).

    Observability only: alarms are alerted (``intent_alarm``) and surfaced
    as degraded reasons in RuntimeHealth and /readyz; they never gate or
    change trading.
    """

    enabled: bool = True
    # Minutes after the expected equity decision (NYSE close, never before
    # 16:00 ET) before a missing decision alarms.
    equity_decision_grace_minutes: float = 45.0
    # Minutes after the next NYSE open following a decision before a
    # missing/under-target position alarms.
    equity_fill_grace_minutes: float = 30.0
    # Held weight below this fraction of the decided target weight counts as
    # "under target" (drift alone rarely halves a position in one session).
    minimum_held_fraction: float = 0.5
    # When an alarm is active and trading is expected, /readyz reports
    # ready=false (HTTP 503) with the alarm codes as degraded_reasons.
    fail_readiness: bool = True
    evaluation_interval_seconds: float = 60.0

    def __post_init__(self) -> None:
        for name in ("equity_decision_grace_minutes", "equity_fill_grace_minutes"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"intent_watchdog.{name} must be non-negative")
        if not math.isfinite(self.minimum_held_fraction) or not 0 < self.minimum_held_fraction <= 1:
            raise ValueError("intent_watchdog.minimum_held_fraction must be in (0, 1]")
        if not math.isfinite(self.evaluation_interval_seconds) or self.evaluation_interval_seconds <= 0:
            raise ValueError("intent_watchdog.evaluation_interval_seconds must be positive")


@dataclass(frozen=True)
class SignalCrossCheckConfig:
    """Second-source cross-check of the equity decision (``signal_cross_check:``).

    At each scheduled equity session-close decision the runtime recomputes
    the strategy's trend state and target weight on the Alpaca consolidated
    SIP daily bar of the same session and compares it with the decision on
    the IEX stream-built bar. Disagreement on the trend state holds the
    current position for that session; an unavailable second source never
    blocks the decision. Runtime-only: the offline backtest has one source.
    See docs/research/signal_cross_check_v1.md.
    """

    enabled: bool = True
    # Minutes after the NYSE close before the SIP daily bar is requested.
    # Alpaca's Basic data plan rejects SIP queries that reach into the last
    # 15 minutes; an account with a real-time SIP subscription can use 0.
    # The historical data client clamps every SIP bar request end to
    # now - embargo with this same value (warmup, split-basis verifier).
    sip_embargo_minutes: float = 15.0
    # Minutes the second source is retried (from the first allowed attempt)
    # before the decision proceeds on the primary source alone.
    retry_window_minutes: float = 10.0
    retry_interval_seconds: float = 60.0
    # Flag a session whose two closes differ by more than this (either
    # symbol), even when both sources agree on the trend.
    close_divergence_bps: float = 50.0

    def __post_init__(self) -> None:
        for name in ("sip_embargo_minutes", "retry_window_minutes", "close_divergence_bps"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"signal_cross_check.{name} must be non-negative")
        if not math.isfinite(self.retry_interval_seconds) or self.retry_interval_seconds <= 0:
            raise ValueError("signal_cross_check.retry_interval_seconds must be positive")


@dataclass(frozen=True)
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
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
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    intent_watchdog: IntentWatchdogConfig = field(default_factory=IntentWatchdogConfig)
    signal_cross_check: SignalCrossCheckConfig = field(default_factory=SignalCrossCheckConfig)

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("At least one enabled market symbol must be configured")
        if self.alpaca.stock_stream_feed == "iex" and (
            self.universe.equity.maximum_stream_symbols > 30 or len(self.universe.equity.symbols) > 30
        ):
            raise ValueError("Alpaca Basic IEX equity stream symbols must not exceed 30")
        if self.strategy.equity.enabled and not self.universe.equity.enabled:
            raise ValueError("Enabled equity strategy requires universe.equity.enabled")
        if self.runtime.mode == RuntimeMode.LIVE and not self.live.enabled:
            raise ValueError("runtime.mode=live requires live.enabled=true")
        if self.runtime.mode != RuntimeMode.LIVE and self.live.enabled:
            raise ValueError("live.enabled=true requires runtime.mode=live")
        # A decision waiting for the second source must still land before
        # the intent watchdog would call it missing.
        cross_check = self.signal_cross_check
        if (
            cross_check.enabled
            and self.intent_watchdog.enabled
            and (
                cross_check.sip_embargo_minutes + cross_check.retry_window_minutes
                >= self.intent_watchdog.equity_decision_grace_minutes
            )
        ):
            raise ValueError(
                "signal_cross_check.sip_embargo_minutes + retry_window_minutes must be below "
                "intent_watchdog.equity_decision_grace_minutes"
            )
        # Capital invariant: the equity allocation stays inside the gross limit
        # and the cash buffer, so a stale account snapshot cannot plan above
        # the gross cap.
        # invariant: equity allocation <= max_gross <= 1 - cash_buffer
        effective_alloc = 0.0
        if self.strategy.equity.enabled:
            effective_alloc += float(self.strategy.equity.maximum_allocation)
        max_gross = float(self.risk.max_gross_exposure_pct)
        cash_buf = float(self.risk.cash_buffer_pct)
        # allow tiny epsilon for floating point
        eps = 1e-9
        if effective_alloc - max_gross > eps:
            raise ValueError(
                f"Capital invariant violated: equity allocation {effective_alloc:.4f} > max_gross {max_gross:.4f}"
            )
        # The per-asset cap must admit the whole equity allocation; a smaller
        # cap would shrink the allocation without any operator-visible error.
        if effective_alloc - float(self.risk.max_position_pct) > eps:
            raise ValueError(
                f"risk.max_position_pct {float(self.risk.max_position_pct):.4f} is below equity "
                f"maximum_allocation {effective_alloc:.4f}; the cap would silently shrink the allocation"
            )
        # SMA(0) is the buy-and-hold control, not a trend filter; a production
        # config must not load it silently (the strategy itself accepts 0).
        equity_parameters = self.strategy.equity.parameters
        sma_bars = equity_parameters.get("sma_bars")
        if (
            self.strategy.equity.enabled
            and isinstance(sma_bars, int)
            and not isinstance(sma_bars, bool)
            and sma_bars < 2
        ):
            raise ValueError("strategy.equity.parameters.sma_bars must be at least 2 (0 is buy-and-hold)")
        vol_target = equity_parameters.get("vol_target")
        if (
            self.strategy.equity.enabled
            and isinstance(vol_target, (int, float))
            and not isinstance(vol_target, bool)
            and vol_target > _MAX_VOL_TARGET_CONFIG
        ):
            raise ValueError(
                f"strategy.equity.parameters.vol_target must be at most {_MAX_VOL_TARGET_CONFIG:g} "
                "(higher targets leave the 2x hold near full weight, so volatility targeting is effectively off)"
            )
        # A retried session-close decision must still land before the intent
        # watchdog would call it missing, and must outlive the second-source
        # deferral it has to cover (a retry deadline inside the cross-check
        # window would silently cancel the cross-check).
        if self.intent_watchdog.enabled and self.engine.decision_retry_window_minutes >= (
            self.intent_watchdog.equity_decision_grace_minutes
        ):
            raise ValueError(
                "engine.decision_retry_window_minutes must be below intent_watchdog.equity_decision_grace_minutes"
            )
        if self.signal_cross_check.enabled and self.engine.decision_retry_window_minutes < (
            cross_check.sip_embargo_minutes + cross_check.retry_window_minutes
        ):
            raise ValueError(
                "engine.decision_retry_window_minutes must cover "
                "signal_cross_check.sip_embargo_minutes + retry_window_minutes"
            )
        if max_gross + cash_buf - 1.0 > eps:
            raise ValueError(
                f"Capital invariant violated: max_gross {max_gross:.4f} + cash_buffer {cash_buf:.4f} > 1.0"
            )

    @property
    def symbols(self) -> tuple[str, ...]:
        return self.universe.equity.symbols if self.universe.equity.enabled else ()


def _inheritable_default_parameters(defaults: MarketStrategyConfig, parameters: dict[str, object]) -> dict[str, object]:
    """The packaged defaults an explicit YAML block may inherit.

    A block that switches ``equity_leveraged_trend_v1`` off its majority rule
    must not inherit that rule's packaged parameters: ``trend_rule: sma``
    (VT35) rejects them. Only packaged values are dropped — a key the block
    itself sets is still the strategy's to validate.
    """
    inherited = dict(defaults.parameters)
    if defaults.name == "equity_leveraged_trend_v1":
        from parayabuck.portfolio.equity_leveraged_trend import MAJORITY_ONLY_PARAMETERS, TREND_RULE_MAJORITY

        if parameters.get("trend_rule", inherited.get("trend_rule")) != TREND_RULE_MAJORITY:
            for key in MAJORITY_ONLY_PARAMETERS:
                inherited.pop(key, None)
    return inherited


def _strategy_from_raw(
    defaults: MarketStrategyConfig, enabled_default: bool, raw: dict[str, Any]
) -> MarketStrategyConfig:
    """Merge one ``strategy.<market>`` YAML block over the packaged defaults.

    A ``parameters`` block for the default strategy is merged key by key over
    the packaged parameters: a partial block (say only ``sma_bars``) must keep
    the packaged values of the omitted keys instead of silently falling back
    to the strategy class's different built-in defaults (``vol_target: 0``).
    """
    base = {
        "enabled": enabled_default,
        "name": defaults.name,
        # No market default timeframe: the STRATEGY supplies it.
        "timeframe": None,
        "maximum_allocation": defaults.maximum_allocation,
        "parameters": defaults.parameters,
    }
    merged = {**base, **dict(raw)}
    if "parameters" in raw:
        if raw["parameters"] is not None and not isinstance(raw["parameters"], dict):
            raise ValueError("strategy parameters must be a mapping")
        parameters = _block(raw["parameters"])
        if merged["name"] == defaults.name:
            parameters = {**_inheritable_default_parameters(defaults, parameters), **parameters}
        merged["parameters"] = dict(parameters)
    return MarketStrategyConfig(**merged)


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


def _validate_strategy_symbols(strategy: MarketStrategyConfig, equity: EquityUniverseConfig) -> None:
    """Fail loading when the strategy reads or holds a symbol outside the universe.

    The runtime streams, warms up and decides only on ``universe.equity.symbols``;
    a signal or hold symbol outside it would never get history and the engine
    would sit warming up (or decide on a missing series) with no error.
    """
    if not (strategy.enabled and equity.enabled):
        return
    from parayabuck.markets.symbols import canonical_symbol
    from parayabuck.portfolio import product_strategy_from

    resolved = product_strategy_from(strategy.name, strategy.parameters, timeframe=strategy.timeframe)
    universe = {canonical_symbol(str(symbol)) for symbol in equity.symbols}
    missing = []
    for key in ("signal_symbol", "hold_symbol"):
        symbol = getattr(resolved, key, None)
        if isinstance(symbol, str) and symbol.strip() and canonical_symbol(symbol) not in universe:
            missing.append(f"strategy.equity.parameters.{key}={symbol}")
    if missing:
        raise ValueError(
            "Strategy symbols must be in universe.equity.symbols "
            f"({', '.join(sorted(universe)) or 'empty'}): {', '.join(missing)}"
        )


def _block(value: Any) -> dict[str, Any]:
    """Copy one YAML mapping block; a present-but-empty block (``None``) is empty."""
    return {} if value is None else dict(value)


def _execution_from_raw(value: Any) -> ExecutionConfig:
    """``execution`` block with its nested downsize-gain-band schedule.

    Absent key: the packaged default (the shipped A1u schedule). Present with
    ``null``: the rule is explicitly disabled. Present as a mapping: that
    schedule (an empty block keeps the shipped thresholds/bands). The December
    deferral window is a plain integer in the same block and needs no special
    handling: absent keeps the shipped 10, ``0`` disables it.
    """
    block = _block(value)
    if "downsize_gain_band" not in block:
        return ExecutionConfig(**block)
    schedule = block["downsize_gain_band"]
    if schedule is None:
        return ExecutionConfig(**{**block, "downsize_gain_band": None})
    if not isinstance(schedule, dict):
        raise ValueError("execution.downsize_gain_band must be a mapping or null")
    return ExecutionConfig(
        **{
            **block,
            "downsize_gain_band": DownsizeGainBand(
                **{key: tuple(float(item) for item in items) for key, items in schedule.items()}
            ),
        }
    )


_CRYPTO_REMOVED = (
    "Crypto support was removed from ParayaBuck (equity-only since 2026-09-30; docs/research/crypto_removal_v1.md)"
)


def _reject_removed_crypto_settings(raw: dict[str, Any]) -> None:
    """Fail loudly on configuration keys of the removed crypto sleeve.

    A stale operator configuration must not load with its crypto intent
    silently dropped; the operator removes the keys explicitly.
    """
    found: list[str] = []
    for section in ("universe", "strategy"):
        block = raw.get(section)
        if isinstance(block, dict) and "crypto" in block:
            found.append(f"{section}.crypto")
    prefixes = {
        "risk": "max_crypto_",
        "engine": "crypto_",
        "backtest": "crypto_",
        "intent_watchdog": "crypto_",
    }
    for section, prefix in prefixes.items():
        block = raw.get(section)
        if isinstance(block, dict):
            found.extend(f"{section}.{key}" for key in block if str(key).startswith(prefix))
    # The crypto universe scanner was the only user of these two keys.
    for section, key in (("runtime", "scanner_refresh_hours"), ("backtest", "scanner_rebalance")):
        block = raw.get(section)
        if isinstance(block, dict) and key in block:
            found.append(f"{section}.{key}")
    if found:
        raise ValueError(f"{_CRYPTO_REMOVED}; remove these settings: {', '.join(sorted(found))}")


_WEBHOOK_REMOVED = (
    "Webhook alert delivery was removed on 2026-10-06: alerts stay local "
    "(log + event journal; read them with `parayabuck ops events`, "
    "`parayabuck ops status`, the dashboard or journalctl); the only "
    "off-host channel is the opt-in alerting.email block"
)
_REMOVED_ALERTING_KEYS = ("enabled", "webhook_url", "events")


def _reject_removed_alerting_settings(raw: dict[str, Any]) -> None:
    """Fail loudly on the removed webhook keys instead of silently dropping them."""
    block = raw.get("alerting")
    found = [f"alerting.{key}" for key in _REMOVED_ALERTING_KEYS if isinstance(block, dict) and key in block]
    if found:
        raise ValueError(f"{_WEBHOOK_REMOVED}; remove these settings: {', '.join(found)}")


def load_config(config_path: str | None = None) -> Config:
    load_env_file()
    path = Path(config_path or os.getenv("CONFIG_PATH") or "config/settings.yaml")
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _reject_removed_crypto_settings(raw)
    _reject_removed_alerting_settings(raw)
    runtime_raw = _block(raw.get("runtime"))
    for _removed_runtime_key in ("execution_timeframe", "market_state_timeframe", "risk_monitor_timeframe"):
        if _removed_runtime_key in runtime_raw:
            warnings.warn(
                f"runtime.{_removed_runtime_key} is removed and ignored; the strategy owns its signal timeframe",
                DeprecationWarning,
                stacklevel=2,
            )
            runtime_raw.pop(_removed_runtime_key)
    data_raw = _block(raw.get("data"))
    for _removed_data_key in ("equity_session", "include_extended_hours"):
        if _removed_data_key in data_raw:
            warnings.warn(
                f"data.{_removed_data_key} is removed and ignored; sessions follow markets/nyse_calendar",
                DeprecationWarning,
                stacklevel=2,
            )
            data_raw.pop(_removed_data_key)
    if data_raw:
        # The block has no keys left; a typo must not vanish silently.
        warnings.warn(
            f"Unknown data settings are ignored: {', '.join(sorted(map(str, data_raw)))}",
            UserWarning,
            stacklevel=2,
        )
    observability_raw = _block(raw.get("observability"))
    if "enabled" in observability_raw:
        warnings.warn(
            "observability.enabled is removed and ignored; observability is always on",
            DeprecationWarning,
            stacklevel=2,
        )
        observability_raw.pop("enabled")
    if "mode" in runtime_raw:
        raw_mode = str(runtime_raw["mode"]).lower()
        if raw_mode == "backtest":
            raise ValueError("runtime.mode=backtest is CLI-only; use disabled, paper, or live")
        runtime_raw["mode"] = RuntimeMode(raw_mode)
    engine_raw = _block(raw.get("engine"))
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
    risk_raw = _block(raw.get("risk"))
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
    liquidation_raw = risk_raw.pop("drawdown_liquidation", None)
    if liquidation_raw is not None and not isinstance(liquidation_raw, Mapping):
        raise ValueError("risk.drawdown_liquidation must be a mapping")
    risk = RiskLimits(
        **risk_raw,
        drawdown_liquidation=DrawdownLiquidationConfig(**_block(liquidation_raw)),
    )
    universe_raw = raw.get("universe")
    if universe_raw is None:
        legacy = tuple(raw.get("symbols", ()))
        from parayabuck.markets.symbols import normalize_symbol

        identities = [normalize_symbol(str(symbol)) for symbol in legacy]
        unsupported = [identity.canonical for identity in identities if identity.market != Market.EQUITY]
        if unsupported:
            raise ValueError(f"{_CRYPTO_REMOVED}; remove the crypto symbols: {', '.join(unsupported)}")
        equity = EquityUniverseConfig(
            enabled=bool(identities), symbols=tuple(identity.canonical for identity in identities)
        )
    else:
        equity = EquityUniverseConfig(**_block(dict(universe_raw).get("equity")))
    strategy_raw = _block(raw.get("strategy"))
    if isinstance(strategy_raw.get("equity"), dict) and "timeframe" in strategy_raw["equity"]:
        warnings.warn(
            "strategy.equity.timeframe is deprecated: the strategy "
            "owns its signal timeframe. The value is still validated "
            "against the selected strategy's supported timeframes and "
            "rejected on mismatch, never silently reinterpreted.",
            DeprecationWarning,
            stacklevel=2,
        )
    # Fail-closed: when the equity universe is enabled the strategy.equity
    # block MUST be present with an explicit name if the strategy is intended
    # to trade. A disabled strategy (explicit ``enabled: false``) may omit the
    # name. Dataclass defaults remain for direct construction, but YAML
    # loading is strict for an enabled market.
    if equity.enabled:
        _eq_block = strategy_raw.get("equity")
        _eq_enabled = True if not isinstance(_eq_block, dict) else bool(_eq_block.get("enabled", True))
        if _eq_enabled:
            if not isinstance(_eq_block, dict) or not str(_eq_block.get("name", "")).strip():
                raise ValueError("strategy.equity block with name is required when universe.equity.enabled is true")
    default_strategy = StrategyConfig()
    equity_strategy = _strategy_from_raw(
        default_strategy.equity,
        default_strategy.equity.enabled if "equity" in strategy_raw else equity.enabled,
        _block(strategy_raw.get("equity")),
    )
    equity_strategy = _resolved_timeframe(equity_strategy)
    _validate_strategy_symbols(equity_strategy, equity)
    alpaca_raw = _block(raw.get("alpaca"))
    legacy_paper = alpaca_raw.pop("paper", None)
    if legacy_paper is False:
        raise ValueError(
            "alpaca.paper=false is unsafe and no longer supported; configure "
            "runtime.mode=live plus live.enabled and live safety controls"
        )
    return Config(
        app=AppConfig(**dict(raw.get("app") or {})),
        runtime=RuntimeConfig(**runtime_raw),
        alpaca=AlpacaConfig(**alpaca_raw),
        broker=BrokerConfig(**_block(raw.get("broker"))),
        data=DataConfig(),
        universe=UniverseConfig(equity=equity),
        strategy=StrategyConfig(equity_strategy),
        risk=risk,
        backtest=BacktestConfig(**_block(raw.get("backtest"))),
        live=LiveConfig(**_block(raw.get("live"))),
        engine=EngineConfig(**engine_raw),
        execution=_execution_from_raw(raw.get("execution")),
        backup=BackupConfig(**_block(raw.get("backup"))),
        alerting=AlertingConfig(**_block(raw.get("alerting"))),
        observability=ObservabilityConfig(**observability_raw),
        intent_watchdog=IntentWatchdogConfig(**_block(raw.get("intent_watchdog"))),
        signal_cross_check=SignalCrossCheckConfig(**_block(raw.get("signal_cross_check"))),
    )


__all__ = [
    "DEFAULT_DECISION_DB_PATH",
    "DEFAULT_LIVE_DECISION_DB_PATH",
    "AlertingConfig",
    "EmailAlertConfig",
    "AlpacaConfig",
    "AppConfig",
    "BacktestConfig",
    "BackupConfig",
    "BrokerConfig",
    "Config",
    "DataConfig",
    "DownsizeGainBand",
    "DrawdownLiquidationConfig",
    "EngineConfig",
    "EquityUniverseConfig",
    "ExecutionConfig",
    "IntentWatchdogConfig",
    "LiveConfig",
    "MarketStrategyConfig",
    "ObservabilityConfig",
    "RiskLimits",
    "RuntimeConfig",
    "RuntimeMode",
    "SignalCrossCheckConfig",
    "StrategyConfig",
    "UniverseConfig",
    "load_config",
]
