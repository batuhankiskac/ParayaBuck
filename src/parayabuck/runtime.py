from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import queue
import sqlite3
import threading
import time
from _thread import LockType
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from requests.exceptions import RequestException

from parayabuck.config import Config, RuntimeMode
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Market,
)
from parayabuck.data import AlpacaBarStream, AlpacaHistoricalData, TimeframeAggregator
from parayabuck.data.adjustment import adequate_coverage, covers_session
from parayabuck.execution.alpaca import (
    AlpacaBroker,
    AmbiguousOrderState,
    CryptoPriceObservation,
    OwnershipDriftError,
    ProtectionReconciliationError,
)
from parayabuck.execution.alpaca_reconciliation import ReconciliationReport, ReconciliationResultError
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream, TradeUpdate
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    PortfolioOrderSide,
    portfolio_risk_limits_from_config,
)
from parayabuck.live_safety import LiveOperatorApproval, LiveStartupProof
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    NEW_YORK,
    bars_to_calendar_days,
    is_regular_equity_time,
)
from parayabuck.monitoring import alerts
from parayabuck.monitoring import metrics as runtime_metrics_store
from parayabuck.monitoring import receipt as paper_receipt

try:
    from parayabuck.monitoring import observe as _observe
except Exception:  # pragma: no cover
    _observe = None  # type: ignore
try:
    from parayabuck.monitoring.snapshot import SnapshotWriter
except Exception:  # pragma: no cover
    SnapshotWriter = None  # type: ignore
from parayabuck.monitoring.incident import IncidentCategory
from parayabuck.monitoring.paper_incident import automate_incident_recovery
from parayabuck.portfolio import product_strategy_from, validate_strategy_timeframe
from parayabuck.portfolio.runtime_service import (
    REBALANCE_PHASE_BLOCKED,
    PortfolioRuntime,
    PortfolioStateRepository,
    configuration_digest,
    is_terminal_order_status,
)
from parayabuck.risk.state import RiskState
from parayabuck.runtime_events import _Event, _EventQueue
from parayabuck.runtime_health import RuntimeHealth, WorkerHealth, _portfolio_catch_up_health
from parayabuck.runtime_state import RuntimeMarketState
from parayabuck.runtime_universe import ActiveProductUniverse, load_active_product_universe
from parayabuck.storage import SQLiteStore
from parayabuck.universe import (
    ensure_active_universes,
    equity_universe_digest,
    load_active_crypto_symbols,
    refresh_crypto_universe,
)
from parayabuck.universe.crypto_scanner import (
    crypto_scanner_config_digest,
    validate_active_crypto_universe,
)
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.runtime")

_ACCOUNT_SNAPSHOT_INIT_LOCK = threading.Lock()


@dataclass(frozen=True)
class _AccountSnapshotLease:
    """A cache generation captured at the decision boundary.

    ``generation=None`` is reserved for lightweight test doubles that
    override the historical snapshot method without constructing cache state;
    production instances always carry an integer generation and validate it.
    """

    snapshot: AccountSnapshot
    generation: int | None


class TransientStartupError(RuntimeError):
    """A network-class failure during engine startup.

    Raised by ``EngineRuntime.start()`` only for transient infrastructure
    failures (broker/data API connection errors, timeouts, HTTP 5xx/429
    responses) during startup reconciliation or warmup. The runtime object is
    discarded by the caller, no permanent circuit-breaker incident is
    recorded, and the caller may retry startup.
    """


def _is_transient_network_error(exc: BaseException) -> bool:
    """True when ``exc`` is a network-class startup failure.

    Network-class failures are requests-level connection/timeout errors, HTTP
    5xx/429 responses, and builtin ``ConnectionError``/``TimeoutError`` (the
    builtins are not subclasses of ``RequestException``) — including the same
    failures wrapped as the ``__cause__``/``__context__`` of another exception
    (for example the Alpaca SDK's ``APIError`` wrapping an ``HTTPError``).
    HTTP 4xx responses are permanent (auth/config/entitlement problems) and
    the domain safety exceptions fail closed: neither is ever retried.
    """
    if isinstance(exc, (ProtectionReconciliationError, AmbiguousOrderState)):
        return False
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RequestException):
            response = getattr(current, "response", None)
            status = getattr(response, "status_code", None)
            return status is None or status == 429 or status >= 500
        if isinstance(current, (ConnectionError, TimeoutError)):
            return True
        current = current.__cause__ or current.__context__
    return False


_DATABASE_ERROR_TOKENS = ("database", "locked", "integrity", "sqlite")


def _is_database_error(exc: BaseException) -> bool:
    """True when ``exc`` is a local database failure rather than a provider one.

    ``sqlite3.Error`` recognises the driver's own classes (``OperationalError``,
    ``IntegrityError``) no matter how the message reads. Providers wrap and
    re-word driver failures, so a message scan stays as the fallback. Both
    fail-closed call sites must agree on this predicate: disagreeing token
    sets route the same failure into different incident categories and
    therefore different recovery paths.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, sqlite3.Error):
            return True
        current = current.__cause__ or current.__context__
    message = str(exc).lower()
    return any(token in message for token in _DATABASE_ERROR_TOKENS)


_WARMUP_SYMBOL_BATCH_SIZE = 8
_WARMUP_RETRY_SECONDS = 600.0
# Consecutive warmup retries before the delay reaches its ceiling
# (600s * 2**6 ~= 10.7h: roughly a daily cadence for a symbol whose last bar
# permanently lags its benchmark's).
_WARMUP_RETRY_MAX_EXPONENT = 6
# How often the split-adjustment reconciler compares raw vs split-adjusted
# daily tails (hourly; a split is applied at most one check after it trades).
_SIGNAL_ADJUSTMENT_CHECK_SECONDS = 3600.0
# A verification counts as fresh for one check interval plus this slack:
# the monitor waits a full interval AFTER each pass, so the next success
# always lands a little past the previous one + 3600 s.
_SIGNAL_ADJUSTMENT_STALE_SLACK_SECONDS = 300.0
# Safety margin added ON TOP of the strategy history window so the batched
# splits fetch always spans the full required signal window (never a short
# tail standing in for a long history).
_SIGNAL_ADJUSTMENT_WINDOW_MARGIN_DAYS = 10
# Per-market scanner-refresh backoff (seconds) after a failed scheduled
# refresh: ~5 min, then ~15, ~30, capped at ~60 min per consecutive failure.
_UNIVERSE_RETRY_BACKOFF_SECONDS = (300.0, 900.0, 1800.0, 3600.0)
# Stream worker name <-> market. Single source of truth so health
# reporting, halt tracking and worker restart handling cannot disagree.
_STREAM_WORKER_BY_MARKET = {Market.EQUITY: "pb-stocks", Market.CRYPTO: "pb-crypto"}
_MARKET_BY_STREAM_WORKER = {worker: market for market, worker in _STREAM_WORKER_BY_MARKET.items()}


def _reservation_sort_key(key: tuple[Any, str]) -> tuple[str, str]:
    """Deterministic (market, symbol) order for reserved position keys."""
    return (key[0].value if hasattr(key[0], "value") else str(key[0]), key[1])


# Runtime states that block entries outright; a transient stream-worker
# blip must never overwrite one of them with "degraded".
_ENTRY_BLOCKING_STATES = frozenset({"halted", "error", "stop_failed", "stopping", "stopped"})
# A shared stream worker (pb-trade-updates) that ran this long before failing
# was healthy: its failure starts a fresh restart budget and backoff, so
# sparse blips over weeks cannot add up to the restart-limit halt. A crash
# loop never runs this long and still hits the limit.
_SHARED_WORKER_HEALTHY_RESET_SECONDS = 600.0
_RECONCILIATION_INCIDENT_REASONS = frozenset({"reconciliation_failed", "reconciliation_bug_suspected"})


class _StrategyTimeframes(Protocol):
    timeframe: str
    required_timeframes: tuple[str, ...]


def required_aggregation_timeframes(
    execution_timeframe: str,
    market_state_timeframe: str,
    strategy_specs: Sequence[tuple[_StrategyTimeframes, tuple[str, ...], float]],
) -> frozenset[str]:
    """The union of cadences the bar aggregation pipeline must produce.

    Includes the execution and market-state cadences, every enabled
    strategy's primary signal timeframe, AND every strategy-declared
    ``required_timeframes`` (StrategySpec) so a strategy that needs a second
    series gets its bars aggregated and seeded like any other cadence.
    """
    timeframes: set[str] = {execution_timeframe, market_state_timeframe}
    for strategy, _symbols, _cap in strategy_specs:
        timeframes.add(strategy.timeframe)
        timeframes.update(strategy.required_timeframes)
    return frozenset(timeframes)


# Health and event types are imported from runtime_health / runtime_events (see above)


class EngineRuntime:
    """Web-manageable streaming runtime for the ParayaBuck engine."""

    # Serialize the check-then-spawn sequences in _schedule_reconcile and
    # _schedule_warmup_retry. Class-level so the lock exists even when a
    # test constructs an instance via __new__ and so two threads can never
    # create competing locks on first use.
    _reconcile_spawn_lock = threading.Lock()
    _warmup_retry_spawn_lock = threading.Lock()
    # Fallback for partially constructed runtimes (EngineRuntime.__new__ in
    # tests): fully constructed instances shadow this with their own
    # __init__-created lock, keeping per-instance isolation.
    _ingestion_lock = threading.Lock()

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        config: Config,
        live_startup_proof: LiveStartupProof | None = None,
    ) -> None:
        self._validate_execution_mode(config)
        self.api_key = api_key
        self.secret_key = secret_key
        self.app_config = config
        self.config = config.engine
        self.mode = config.runtime.mode
        self.universe_preflight = ensure_active_universes(config, api_key, secret_key)
        active_equities = config.universe.equity.symbols if config.universe.equity.enabled else ()
        active_crypto = (
            load_active_crypto_symbols(config.universe.crypto, log_rejection=False)
            if config.universe.crypto.enabled
            else ()
        )
        self._product_universes_by_market: dict[Market, ActiveProductUniverse] = {}
        if active_equities:
            self._product_universes_by_market[Market.EQUITY] = self._load_product_universe(
                Market.EQUITY,
                tuple(active_equities),
            )
        if active_crypto:
            self._product_universes_by_market[Market.CRYPTO] = self._load_product_universe(
                Market.CRYPTO,
                tuple(active_crypto),
            )
        self._active_crypto_symbols = active_crypto if config.strategy.crypto.enabled else ()
        # Live observation membership is driven by the enabled strategies'
        # universe symbols plus managed-position/open-order pins. A market
        # whose strategy is disabled streams nothing from its universe; pins
        # are added later by _pin_managed_symbols when managed inventory
        # exists.
        self._strategy_enabled: dict[Market, bool] = {
            Market.EQUITY: bool(config.strategy.equity.enabled and active_equities),
            Market.CRYPTO: bool(config.strategy.crypto.enabled and active_crypto),
        }
        self.symbols = [
            canonical_symbol(symbol)
            for symbol in (
                *(active_equities if self._strategy_enabled[Market.EQUITY] else ()),
                *(active_crypto if self._strategy_enabled[Market.CRYPTO] else ()),
            )
        ]
        self._symbols_by_market = {
            Market.EQUITY: ({symbol for symbol in self.symbols if not is_crypto(symbol)}),
            Market.CRYPTO: {symbol for symbol in self.symbols if is_crypto(symbol)},
        }
        self._market_enabled = dict(self._strategy_enabled)
        self._market_entry_halts: dict[Market, str | None] = {
            Market.EQUITY: self._initial_universe_halt(Market.EQUITY),
            Market.CRYPTO: self._initial_universe_halt(Market.CRYPTO),
        }
        # Entry-risk latch: reacts to daily-loss/drawdown breaches on the
        # account-poll cadence. A latched market still runs decisions, but
        # only risk-reducing SELLs may be planned; entries are blocked.
        # Account freshness is deliberately NOT part of the latch: a stale
        # broker account snapshot (position quantity/cash/exposure no longer
        # trusted) pauses ALL new portfolio rebalance decisions — buys AND
        # target sells — because even a risk-reducing target cannot be sized
        # safely against unknown positions. Broker-native protective exits
        # (stops already resting at the broker) keep working regardless.
        # Ownership drift is stricter still: it halts the runtime outright
        # (a broken ownership model must not be traded against at all).
        self._entry_risk_latch: dict[Market, str | None] = {
            Market.EQUITY: None,
            Market.CRYPTO: None,
        }
        strategy_specs = []
        for market, selection, symbols in (
            (Market.EQUITY, config.strategy.equity, active_equities),
            (Market.CRYPTO, config.strategy.crypto, active_crypto),
        ):
            if not selection.enabled or not symbols:
                continue
            strategy = product_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
            if strategy.market != market:
                raise ValueError(f"Configured {market.value} strategy has the wrong market")
            # The strategy owns its signal timeframe; the configured value must
            # match one of its validated timeframes (never silently
            # reinterpreted). product_strategy_from already enforces the
            # product boundary, and validate_strategy_timeframe keeps the
            # invariant explicit. Generic callers may use portfolio_strategy_from.
            validate_strategy_timeframe(strategy, selection.timeframe or strategy.timeframe)
            strategy_specs.append((strategy, tuple(symbols), selection.maximum_allocation))
        self._strategy_specs = tuple(strategy_specs)
        if not self._strategy_specs:
            raise ValueError("At least one enabled strategy needs an active universe")
        self.strategy_status = "configured:" + ",".join(
            f"{strategy.market.value}/{strategy.name}" for strategy, _symbols, _cap in strategy_specs
        )
        self.paper = self.mode == RuntimeMode.PAPER
        database_path = config.runtime.decision_db_path
        self.store = SQLiteStore(database_path)
        self.ledger = ExecutionLedger(database_path, store=self.store)
        self._portfolio_repository = PortfolioStateRepository(database_path, store=self.store)
        # Derive runtime state dir from decision DB parent to avoid split state (peak vs sqlite).
        runtime_state_dir = str(Path(database_path).parent)
        self.broker = AlpacaBroker(
            api_key,
            secret_key,
            paper=self.paper,
            manage_external_positions=self.config.manage_external_positions,
            ledger=self.ledger,
            portfolio_owned_quantities=lambda: self._portfolio_repository.owned_quantities(
                self.broker.account_id or "unknown"
            ),
            portfolio_open_orders=lambda: self._portfolio_repository.open_orders(self.broker.account_id or "unknown"),
            portfolio_fill_recovered=self._recover_portfolio_fill,
            portfolio_order_quantity_adopted=self._portfolio_repository.adopt_order_quantity,
            portfolio_flat_reconciled=lambda symbol, market: (
                self._portfolio_repository.clear_stale_ownership(self.broker.account_id or "unknown", symbol, market)
                or None
            ),
            crypto_stop_cancel_timeout_seconds=self.config.crypto_stop_cancel_timeout_seconds,
            crypto_stop_cancel_poll_interval_seconds=(self.config.crypto_stop_cancel_poll_interval_seconds),
            runtime_state_dir=runtime_state_dir,
        )
        self._portfolio_runtimes: tuple[PortfolioRuntime, ...] = ()
        self.market_state = RuntimeMarketState(
            self.config,
            allowed_trade_symbols={*active_equities, *active_crypto},
        )
        self._consecutive_losses = 0
        self.stream = AlpacaBarStream(api_key, secret_key, config.alpaca.stock_stream_feed)
        self.trading_updates = AlpacaTradingUpdateStream(api_key, secret_key, self.paper, self._on_trade_update)
        self._strategy_timeframes = {strategy.timeframe for strategy, _symbols, _cap in strategy_specs}
        required_timeframes = required_aggregation_timeframes(
            self.app_config.runtime.execution_timeframe,
            self.app_config.runtime.market_state_timeframe,
            strategy_specs,
        )
        self.aggregators = {
            timeframe: TimeframeAggregator(
                timeframe,
                self.config.data_finalization_grace_seconds,
            )
            for timeframe in required_timeframes
        }
        self._events = _EventQueue(self.config.event_queue_capacity)
        self._threads: list[threading.Thread] = []
        self._reconcile_thread: threading.Thread | None = None
        self._reconcile_lock = threading.Lock()
        self._risk_state_lock = threading.Lock()
        self._warmup_retry_thread: threading.Thread | None = None
        self._last_warmup_retry_at = time.monotonic()
        # Warmup-retry backoff: consecutive-attempt count plus the pending
        # symbol set it was raised for. A symbol whose bar lags its
        # benchmark's permanently must not drive a provider round-trip every
        # 10 minutes for the life of the process.
        self._warmup_retry_attempt = 0
        self._warmup_retry_symbols: frozenset[str] = frozenset()
        self._stop_event = threading.Event()
        self._running = False
        self._ingestion_lock = threading.Lock()
        self._portfolio_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._storage_closed = False
        self._state = "stopped"
        self._workers: dict[str, WorkerHealth] = {}
        self._dropped_events = 0
        self._coalesced_events = 0
        self._oldest_received_at: datetime | None = None
        self._event_stats_lock = threading.Lock()
        self._last_account_at: datetime | None = None
        # Immutable AccountSnapshot shared across a processing cycle. The
        # account loop refreshes it every poll interval; the decision path
        # reuses it while it is fresh (<= maximum_account_age_for_entry_seconds)
        # instead of issuing a second full broker account() fetch per batch.
        self._last_account_snapshot: AccountSnapshot | None = None
        self._last_account_snapshot_at: datetime | None = None
        self._account_snapshot_lock = threading.Lock()
        self._account_snapshot_generation = 0
        self._account_snapshot_lease_enabled = True
        self._account_snapshot_active_leases: set[int] = set()
        self._account_snapshot_invalidation_pending = False
        self._last_reconciliation_at: datetime | None = None
        # Observability snapshot writer — isolated, bounded, never blocks trading
        self._snapshot_writer = None
        try:
            if SnapshotWriter is not None:
                observability = self.app_config.observability
                self._snapshot_writer = SnapshotWriter(
                    path=observability.snapshot_path,
                    interval_seconds=observability.snapshot_interval_seconds,
                    provider=lambda: self.health() if hasattr(self, "health") else None,
                )
        except Exception:
            self._snapshot_writer = None
        self._circuit_breaker_reason: str | None = None
        self._series_watermarks: dict[tuple[str, str], datetime] = {}
        self._portfolio_watermarks: dict[Market, datetime] = {}
        self._warming_up = False
        self._not_ready_symbols: set[str] = set(self.symbols)
        self._historical: AlpacaHistoricalData | None = None
        self._last_universe_refresh_at: datetime | None = None
        self._next_universe_refresh_at: datetime | None = None
        # Per-market bounded retry state for the scheduled universe refresh:
        # monotonic deadline + consecutive-failure count, cleared on success.
        self._universe_retry_at: dict[Market, float] = {}
        self._universe_retry_attempt: dict[Market, int] = {}
        self._scanner_failed: dict[Market, bool] = {}
        self._managed_pin_symbols: set[str] = set()
        # Set when a hot-swap rollback failed and the socket membership is
        # unknown; cleared after a successful reconnect from the authoritative
        # desired membership.
        self._resync_pending = False
        self._last_portfolio_status: dict[Market, str] = {}
        self._universe_refresh_status: dict[str, str] = {
            Market.EQUITY.value: self.universe_preflight.equity_status,
            Market.CRYPTO.value: self.universe_preflight.crypto_status,
        }
        self._last_universe_refresh_error: dict[str, str | None] = {
            Market.EQUITY.value: None,
            Market.CRYPTO.value: None,
        }
        # Observability state: the alert sink is configured once from the
        # config block; the runtime session row pairs with the ledger's
        # runtime_sessions table so restart counts are persisted.
        # An empty ``alerting.webhook_url`` falls back to the documented
        # PARAYABUCK_ALERT_WEBHOOK_URL environment override, so the secret
        # URL can live in the service EnvironmentFile instead of the YAML.
        alerts.configure_alerting(
            enabled=config.alerting.enabled,
            webhook_url=config.alerting.webhook_url or os.getenv("PARAYABUCK_ALERT_WEBHOOK_URL", "").strip(),
            events=config.alerting.events,
        )
        self._runtime_session_id: str | None = None
        self._reconcile_failed = False
        self._live_startup_proof = live_startup_proof
        self._startup_committed = False
        self._snapshot_writer_started = False
        # Set once start() launches the stream worker threads. A start that
        # aborts earlier (e.g. startup reconciliation drift) never ran the
        # SDK sockets, whose stop() is not safe before run().
        self._stream_workers_launched = False

    @staticmethod
    def _validate_execution_mode(config: Config) -> None:
        """Reject non-execution modes and unsafe live configuration before I/O."""
        if config.runtime.mode not in {RuntimeMode.PAPER, RuntimeMode.LIVE}:
            raise RuntimeError(f"EngineRuntime does not support runtime.mode={config.runtime.mode.value}")
        if config.runtime.mode == RuntimeMode.LIVE:
            if not config.live.enabled:
                raise RuntimeError("Live runtime requires live.enabled=true")
            if config.live.hard_dollar_cap <= 0:
                raise RuntimeError("Live runtime requires a positive live.hard_dollar_cap")

    def _initial_universe_halt(self, market: Market) -> str | None:
        if market == Market.EQUITY:
            # The static equity universe cannot be invalid.
            return None
        crypto_config = self.app_config.universe.crypto
        status = self.universe_preflight.crypto_status
        if (
            crypto_config.enabled
            and crypto_config.dynamic_selection_enabled
            and status != "success"
            and not crypto_config.allow_static_fallback_trading
        ):
            return "invalid_dynamic_universe"
        return None

    def _load_product_universe(
        self,
        market: Market,
        observation_symbols: tuple[str, ...],
    ) -> ActiveProductUniverse:
        """Bind runtime observation membership to a concrete product snapshot."""

        if market == Market.EQUITY:
            policy_digest = equity_universe_digest(self.app_config.universe.equity)
        else:
            crypto_config = self.app_config.universe.crypto
            policy_digest = crypto_scanner_config_digest(crypto_config)
            if crypto_config.dynamic_selection_enabled and self.universe_preflight.crypto_status == "success":
                return load_active_product_universe(
                    crypto_config.active_universe_path,
                    market=market,
                    expected_observation_symbols=observation_symbols,
                    expected_config_digest=policy_digest,
                )
        # The static equity universe, and a disabled/failed crypto scanner that
        # keeps explicit fallback symbols observable for managed exits, bind a
        # configured snapshot. Entry-halt policy remains in
        # _initial_universe_halt.
        return ActiveProductUniverse.configured(
            market,
            observation_symbols,
            snapshot_id=f"{market.value}:configured:{policy_digest}",
            config_digest=policy_digest,
        )

    def _expected_live_configuration_digests(self) -> tuple[str, ...]:
        risk_limits = portfolio_risk_limits_from_config(self.app_config.risk)
        execution_policy = PortfolioExecutionPolicy(
            rebalance_band_pct=self.app_config.execution.rebalance_band_pct,
            minimum_order_notional=self.app_config.execution.minimum_order_notional,
        )
        digests: list[str] = []
        for strategy, _symbols, maximum_allocation in self._strategy_specs:
            universe_version = (
                equity_universe_digest(self.app_config.universe.equity)
                if strategy.market == Market.EQUITY
                else crypto_scanner_config_digest(self.app_config.universe.crypto)
            )
            digests.append(
                configuration_digest(
                    strategy,
                    self.mode.value,
                    maximum_allocation,
                    universe_version,
                    risk_limits,
                    execution_policy,
                )
            )
        return tuple(digests)

    def _verify_live_startup_proof(self, expected_digests: tuple[str, ...]) -> LiveOperatorApproval:
        proof = self._live_startup_proof
        if not isinstance(proof, LiveStartupProof) or not proof.is_fresh():
            raise RuntimeError("Live startup proof is missing or expired")
        expected_fingerprint = hashlib.sha256(f"{self.api_key}\0{self.secret_key}".encode()).hexdigest()
        if proof.credential_fingerprint != expected_fingerprint:
            raise RuntimeError("Live startup proof does not match the configured credentials")
        if proof.configuration_digests != expected_digests:
            raise RuntimeError("Live startup proof does not match the loaded configuration")
        secret = os.getenv(self.app_config.live.operator_approval_key_env, "")
        approval = LiveOperatorApproval.load_verified(
            self.app_config.live.operator_approval_path,
            secret=secret,
            account_id=proof.account_id,
            configuration_digests=expected_digests,
        )
        self.broker.account()
        account_id = str(getattr(self.broker, "account_id", "") or "")
        if not account_id or account_id != proof.account_id:
            raise RuntimeError("Live startup proof does not match the verified broker account")
        return approval

    def _market_operationally_relevant(
        self, market: Market, *, strategy_enabled: dict[Market, bool] | None = None
    ) -> bool:
        """Whether a market's scanner/stream/protection health is critical.

        A market is operationally relevant when its strategy is enabled, a
        managed position exists for it, or an open managed order/protection
        exists. A completely unobserved market (strategy disabled, no managed
        positions/orders) is not: its scanner failures are informational.
        """
        enabled = strategy_enabled or getattr(self, "_strategy_enabled", {})
        if enabled.get(market, False):
            return True
        broker = getattr(self, "broker", None)
        account_id = (getattr(broker, "account_id", None) if broker is not None else None) or "unknown"
        repository = getattr(self, "_portfolio_repository", None)
        if repository is not None:
            try:
                if repository.owned_symbols(account_id, market):
                    return True
                if repository.open_orders(account_id, market):
                    return True
            except Exception:
                # A repository read failure must not corrupt health; the
                # runtime will surface it through its own fail-closed paths.
                return True
        ledger = getattr(self, "ledger", None)
        if ledger is not None:
            try:
                for entry in ledger.managed_entries_for_account(account_id):
                    if entry.market == market:
                        return True
            except Exception:
                return True
        return False

    def health(self) -> RuntimeHealth:
        now = datetime.now(UTC)
        workers, market_enabled, symbols_by_market = self._snapshot_health_state()
        queue_age = (
            max(0.0, (now - self._oldest_received_at).total_seconds()) if self._oldest_received_at is not None else 0.0
        )
        required = self._required_workers(workers, market_enabled, symbols_by_market)
        heartbeat_ttl = max(30.0, self.config.account_poll_interval_seconds * 3)
        alive = all(self._is_worker_healthy(workers, name, now, heartbeat_ttl) for name in required)
        account_fresh = (
            self._last_account_at is not None
            and (now - self._last_account_at).total_seconds() <= self.config.maximum_account_age_for_entry_seconds
        )
        scanner_status = dict(getattr(self, "_universe_refresh_status", {}))
        scanner_degraded = any(
            status in {"refresh_failed", "apply_failed"} and self._market_operationally_relevant(market)
            for market, status in ((Market(key), status) for key, status in scanner_status.items())
        )
        healthy = (
            self._running
            and self._state == "running"
            and alive
            and queue_age <= self.config.maximum_queue_age_seconds
            and account_fresh
            and self._circuit_breaker_reason is None
            and not scanner_degraded
        )
        state = "degraded" if self._running and not healthy and self._state == "running" else self._state
        market_health = self._build_market_health(
            market_enabled, symbols_by_market, workers, scanner_status, now, heartbeat_ttl
        )
        return self._assemble_runtime_health(
            state=state,
            healthy=healthy,
            queue_age=queue_age,
            workers=workers,
            market_health=market_health,
        )

    def capacity_health(self) -> dict[str, object]:
        """Fresh read-only account capacity for the operator dashboard."""
        # The broker/ledger reads are side-effect-free and run OUTSIDE the
        # portfolio lock: holding it across three REST round-trips would
        # stall order submission and rebalance advancement behind Alpaca
        # latency on every dashboard poll. _portfolio_runtimes is an
        # immutable tuple assigned once at start.
        runtimes = tuple(getattr(self, "_portfolio_runtimes", ()))
        if not runtimes:
            return {"unavailable": True, "reason": "portfolio runtime not started"}
        admission = runtimes[0].current_buy_capacity()
        account = admission.account
        broker_orders = self.broker.working_orders()
        local_orders = self._portfolio_repository.open_orders(self.broker.account_id or "unknown")
        # risk_overlay is swapped under this lock by _apply_runtime_universes.
        with self._portfolio_lock:
            limits = runtimes[0].risk_overlay.limits
        claims: dict[tuple[Market, str], set[str]] = {}
        for position in account.positions:
            if not position.is_flat:
                claims.setdefault((position.market, canonical_symbol(position.symbol, position.market)), set()).add(
                    "POSITION"
                )
        for order in local_orders:
            key = (order.market, canonical_symbol(order.symbol, order.market))
            claims.setdefault(key, set()).add("RESERVED")
            if str(getattr(order.side, "value", order.side)).lower() == "buy":
                claims[key].add("WORKING_OPENING")
        for order in broker_orders:
            symbol = str(self.broker._field(order, "symbol", "") or "")
            if not symbol:
                raise ValueError("Broker OPEN order missing symbol")
            market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
            key = (market, canonical_symbol(symbol, market))
            raw_side = self.broker._field(order, "side", "")
            side = str(getattr(raw_side, "value", raw_side)).lower()
            if side == "buy":
                claims.setdefault(key, set()).add("WORKING_OPENING")
            elif side == "sell":
                claims.setdefault(key, set()).add("WORKING_ORDER")
            else:
                # Unknown OPEN side retains conservative capacity behavior.
                claims.setdefault(key, set()).add("WORKING_UNKNOWN")
        global_cap = limits.maximum_positions
        crypto_cap = limits.maximum_crypto_positions
        effective_global = len(claims)
        effective_crypto = sum(market == Market.CRYPTO for market, _ in claims)
        crypto_positions = sum(
            position.market == Market.CRYPTO and not position.is_flat for position in account.positions
        )
        open_positions = sum(not position.is_flat for position in account.positions)
        opening_keys = {
            key for key, sources in claims.items() if "WORKING_OPENING" in sources and "POSITION" not in sources
        }
        # A fully liquidated account reports equity 0.0; the sleeve
        # percentages are undefined, not infinite, so publish None
        # instead of losing the whole capacity sample to a ZeroDivisionError.
        equity = account.equity
        has_equity = equity > 0
        return {
            "sampled_at": datetime.now(UTC).isoformat(),
            "maximum_positions": global_cap,
            "positions_count": open_positions,
            "effective_slots": effective_global,
            "broker_working_order_count": len(broker_orders),
            "position_count": open_positions,
            "effective_global_slot_claims": effective_global,
            "global_slot_cap": global_cap,
            "global_slots_available": max(0, global_cap - effective_global),
            "crypto_position_count": crypto_positions,
            "effective_crypto_slot_claims": effective_crypto,
            "crypto_slot_cap": crypto_cap,
            "crypto_slots_available": max(0, crypto_cap - effective_crypto),
            "working_opening_order_count": len(opening_keys),
            "slot_claims": [
                {"market": market.value, "symbol": symbol, "sources": sorted(sources)}
                for (market, symbol), sources in sorted(claims.items(), key=lambda item: (item[0][0].value, item[0][1]))
            ],
            "target_gross_pct": limits.maximum_gross * 100,
            "realized_gross_pct": admission.realized_gross / equity * 100 if has_equity else None,
            "available_gross_buy_headroom": admission.gross_headroom,
            "committed_buy_notional": admission.committed_buy_notional,
            "cash_ratio_pct": account.cash / equity * 100 if has_equity else None,
            "available_cash_buy_headroom": admission.cash_headroom,
            "equity_sleeve_pct": (
                sum(position.market_value for position in account.positions if position.market == Market.EQUITY)
                / equity
                * 100
                if has_equity
                else None
            ),
            "crypto_sleeve_pct": (
                sum(position.market_value for position in account.positions if position.market == Market.CRYPTO)
                / equity
                * 100
                if has_equity
                else None
            ),
            "suppression_reasons": {
                runtime.market.value: runtime.last_buy_admission.reason
                for runtime in runtimes
                if runtime.last_buy_admission is not None and runtime.last_buy_admission.reason
            },
            "halt_reasons": {
                market.value: reason for market, reason in getattr(self, "_market_entry_halts", {}).items() if reason
            },
            "reconciliation_status": (
                "failed"
                if getattr(self, "_reconcile_failed", False)
                else "complete"
                if getattr(self, "_last_reconciliation_at", None) is not None
                else "pending"
            ),
        }

    def _snapshot_health_state(
        self,
    ) -> tuple[dict[str, WorkerHealth], dict[Market, bool], dict[Market, set[str]]]:
        state_lock = getattr(self, "_state_lock", threading.RLock())
        with state_lock:
            workers = dict(self._workers)
            market_enabled = dict(
                getattr(
                    self,
                    "_market_enabled",
                    {
                        Market.EQUITY: any(not is_crypto(symbol) for symbol in self.symbols),
                        Market.CRYPTO: any(is_crypto(symbol) for symbol in self.symbols),
                    },
                )
            )
            symbols_by_market = {
                market: set(symbols)
                for market, symbols in getattr(
                    self,
                    "_symbols_by_market",
                    {
                        Market.EQUITY: {symbol for symbol in self.symbols if not is_crypto(symbol)},
                        Market.CRYPTO: {symbol for symbol in self.symbols if is_crypto(symbol)},
                    },
                ).items()
            }
        return workers, market_enabled, symbols_by_market

    def _required_workers(
        self,
        workers: dict[str, WorkerHealth],
        market_enabled: dict[Market, bool],
        symbols_by_market: dict[Market, set[str]],
    ) -> set[str]:
        required = {"pb-decisions", "pb-trade-updates", "pb-account"}
        if "pb-universe-refresh" in workers:
            required.add("pb-universe-refresh")
        if market_enabled.get(Market.EQUITY, False) and symbols_by_market.get(Market.EQUITY):
            required.add("pb-stocks")
            if "pb-signal-adjustment" in workers:
                required.add("pb-signal-adjustment")
        if market_enabled.get(Market.CRYPTO, False) and symbols_by_market.get(Market.CRYPTO):
            required.add("pb-crypto")
        return required

    def _is_worker_healthy(
        self,
        workers: dict[str, WorkerHealth],
        name: str,
        now: datetime,
        heartbeat_ttl: float,
    ) -> bool:
        worker = workers.get(name)
        if worker is None or not worker.alive:
            return False
        if name == "pb-trade-updates":
            return True
        if name == "pb-signal-adjustment":
            # The monitor heartbeats once per check interval, not per poll.
            ttl = _SIGNAL_ADJUSTMENT_CHECK_SECONDS + max(300.0, heartbeat_ttl)
            return (now - worker.last_heartbeat).total_seconds() <= ttl
        if name in _MARKET_BY_STREAM_WORKER:
            return self._stream_worker_delivering(_MARKET_BY_STREAM_WORKER[name], worker, now, heartbeat_ttl)
        return (now - worker.last_heartbeat).total_seconds() <= heartbeat_ttl

    def _build_market_health(
        self,
        market_enabled: dict[Market, bool],
        symbols_by_market: dict[Market, set[str]],
        workers: dict[str, WorkerHealth],
        scanner_status: dict[str, str],
        now: datetime,
        heartbeat_ttl: float,
    ) -> dict[str, dict[str, object]]:
        portfolio_runtime_by_market = {runtime.market: runtime for runtime in getattr(self, "_portfolio_runtimes", ())}
        market_health: dict[str, dict[str, object]] = {}
        for market, worker_name in _STREAM_WORKER_BY_MARKET.items():
            enabled = market_enabled.get(market, False)
            symbols = symbols_by_market.get(market, set())
            worker = workers.get(worker_name)
            not_ready_symbols: set[str] = getattr(self, "_not_ready_symbols", set())
            market_not_ready = symbols & not_ready_symbols
            ready_symbol_count = len(symbols) - len(market_not_ready)
            ready = not enabled or not symbols or ready_symbol_count > 0
            stream_healthy = (
                not enabled or not symbols or self._stream_worker_delivering(market, worker, now, heartbeat_ttl)
            )
            market_health[market.value] = {
                "enabled": enabled,
                "symbol_count": len(symbols),
                "ready": ready,
                "ready_symbol_count": ready_symbol_count,
                "not_ready_symbol_count": len(market_not_ready),
                "warming_symbols": sorted(market_not_ready),
                "managed_pins": sorted(symbols & getattr(self, "_managed_pin_symbols", set())),
                "strategy_batch_status": getattr(self, "_last_portfolio_status", {}).get(market),
                "stream_healthy": stream_healthy,
                "data_gap": (enabled and self._engine_has_market_gap(market)),
                "entry_halt_reason": getattr(self, "_market_entry_halts", {}).get(market),
                "scanner_status": scanner_status.get(market.value),
                "scanner_critical": self._market_operationally_relevant(market, strategy_enabled=market_enabled),
                "catch_up": _portfolio_catch_up_health(portfolio_runtime_by_market.get(market)),
            }
        return market_health

    def _assemble_runtime_health(
        self,
        *,
        state: str,
        healthy: bool,
        queue_age: float,
        workers: dict[str, WorkerHealth],
        market_health: dict[str, dict[str, object]],
    ) -> RuntimeHealth:
        last_reconciliation = getattr(self, "_last_reconciliation_at", None)
        last_universe_refresh = getattr(self, "_last_universe_refresh_at", None)
        next_universe_refresh = getattr(self, "_next_universe_refresh_at", None)
        # The universe-refresh thread resizes these dicts without a lock and
        # health() runs off that thread: iterate one-call C copies, not the
        # live dicts, so a concurrent insert/pop cannot raise mid-iteration.
        retry_at = dict(getattr(self, "_universe_retry_at", {}))
        retry_attempt = dict(getattr(self, "_universe_retry_attempt", {}))
        return RuntimeHealth(
            state=state,
            healthy=healthy,
            circuit_breaker_reason=self._circuit_breaker_reason,
            queue_size=self._events.qsize(),
            queue_age_seconds=queue_age,
            dropped_events=self._dropped_events,
            coalesced_events=self._coalesced_events,
            workers={name: asdict(item) for name, item in workers.items()},
            markets=market_health,
            strategy_status=getattr(self, "strategy_status", "not_configured"),
            strategies=tuple(
                {
                    "market": strategy.market.value,
                    "strategy": strategy.name,
                    "version": strategy.version,
                    "timeframe": strategy.timeframe,
                    "parameters": dict(strategy.parameter_tuple()),
                    "maximum_allocation": maximum_allocation,
                }
                for strategy, _symbols, maximum_allocation in getattr(self, "_strategy_specs", ())
            ),
            last_reconciliation_at=(last_reconciliation.isoformat() if last_reconciliation is not None else None),
            universe_refresh={
                "last_refresh_at": (last_universe_refresh.isoformat() if last_universe_refresh is not None else None),
                "status": dict(getattr(self, "_universe_refresh_status", {})),
                "errors": dict(getattr(self, "_last_universe_refresh_error", {})),
                "next_refresh_at": (next_universe_refresh.isoformat() if next_universe_refresh is not None else None),
                "retry_at": {
                    market.value: (
                        datetime.now(UTC) + timedelta(seconds=max(0.0, deadline - time.monotonic()))
                    ).isoformat()
                    for market, deadline in retry_at.items()
                },
                "retry_attempt": {market.value: attempt for market, attempt in retry_attempt.items()},
            },
            entry_risk_latch={
                market.value: reason for market, reason in getattr(self, "_entry_risk_latch", {}).items()
            },
            signal_adjustment=self.signal_adjustment_status(),
        )

    def signal_adjustment_status(self) -> dict[str, object]:
        """Operational visibility for the equity signal-basis verifier."""
        equity_symbols = sorted(getattr(self, "_symbols_by_market", {}).get(Market.EQUITY, ()))
        state = self._signal_adjustment_state()
        stale = not self._equity_signal_basis_verified() and bool(equity_symbols)
        watermarks = state.get("verified_through_by_symbol")
        watermarks = watermarks if isinstance(watermarks, dict) else {}
        return {
            "healthy": state.get("healthy"),
            "verified": self._equity_signal_basis_verified(),
            "stale": stale,
            "last_success_at": (
                state["last_success_at"].isoformat() if isinstance(state["last_success_at"], datetime) else None
            ),
            "last_failure_at": (
                state["last_failure_at"].isoformat() if isinstance(state["last_failure_at"], datetime) else None
            ),
            "last_error": state["last_error"],
            "verified_through_by_symbol": {
                symbol: (watermark.isoformat() if isinstance((watermark := watermarks.get(symbol)), datetime) else None)
                for symbol in equity_symbols
            },
            "entry_gate_reason": (None if self._equity_signal_basis_verified() else "signal_adjustment_unverified"),
        }

    def start(self) -> None:
        state = getattr(self, "_state", None)
        preexisting = bool(getattr(self, "_running", False)) or state in {
            "starting",
            "running",
            "warming_up",
            "degraded",
            "halted",
            "stopping",
            "stop_failed",
        }
        self._startup_committed = False
        self._startup_cleanup_attempted = False
        try:
            self._start_impl()
        except BaseException:
            if not preexisting and not getattr(self, "_startup_cleanup_attempted", False):
                try:
                    self.stop()
                except BaseException:
                    log.exception("Runtime startup cleanup failed")
            raise

    def _start_impl(self) -> None:
        if self._running:
            raise RuntimeError("Engine runtime is already running")
        if self._state == "stop_failed":
            raise RuntimeError("Previous runtime workers did not stop; process restart required")
        if self.mode == RuntimeMode.DISABLED:
            raise RuntimeError("Runtime start refused because runtime.mode is disabled")
        if self.mode == RuntimeMode.LIVE:
            if not self.app_config.live.enabled:
                raise RuntimeError("Live runtime requires live.enabled=true")
            if self.app_config.live.hard_dollar_cap <= 0:
                raise RuntimeError("Live runtime requires a positive live.hard_dollar_cap")
            if os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
                raise RuntimeError("Live runtime requires PARAYABUCK_ENABLE_LIVE=YES")
        expected_live_digests: tuple[str, ...] = ()
        live_approval: LiveOperatorApproval | None = None
        if self.mode == RuntimeMode.LIVE:
            expected_live_digests = self._expected_live_configuration_digests()
            live_approval = self._verify_live_startup_proof(expected_live_digests)
        self._stop_event.clear()
        self._state = "starting"
        try:
            reconciliation = ReconciliationReport.from_result(self.broker.reconcile())
            if reconciliation.has_drift:
                self._reconcile_failed = True
                with getattr(self, "_state_lock", threading.RLock()):
                    self._circuit_breaker_reason = "reconciliation_failed"
                    self._state = "halted"
                raise ReconciliationResultError(
                    f"Startup reconciliation found drift: {reconciliation.drift_categories}"
                )
            self._last_reconciliation_at = datetime.now(UTC)
            self._clear_reconciliation_incidents()
            try:
                if _observe is not None:
                    _observe.on_reconciliation_complete(phase="startup")
            except Exception:
                log.warning("Startup reconciliation observation failed", exc_info=True)
        except Exception as exc:
            if _is_transient_network_error(exc):
                # G2/G3 boundary: a transient broker API failure is NOT a
                # protection incident. The caller discards this runtime and
                # may retry startup; nothing is persisted as permanent.
                raise TransientStartupError(
                    f"Startup reconciliation deferred after transient network failure: {exc}"
                ) from exc
            self._circuit_breaker_reason = (
                "protection_failure"
                if isinstance(exc, ProtectionReconciliationError)
                else "ownership_drift"
                if isinstance(exc, OwnershipDriftError)
                else "ambiguous_order_state"
                if isinstance(exc, AmbiguousOrderState)
                else "reconciliation_failed"
            )
            self._persist_reconciliation_incident(self._circuit_breaker_reason)
            with getattr(self, "_state_lock", threading.RLock()):
                self._state = "halted"
            alerts.emit(
                "reconciliation_failure",
                message="Startup reconciliation failed; runtime not started",
                reason=self._circuit_breaker_reason,
                error=str(exc),
            )
            raise
        with self._state_lock:
            self._pin_managed_symbols()
        refreshed_specs = []
        for strategy, _symbols, maximum_allocation in self._strategy_specs:
            refreshed_specs.append(
                (
                    strategy,
                    tuple(sorted(self._symbols_by_market[strategy.market])),
                    maximum_allocation,
                )
            )
        self._strategy_specs = tuple(refreshed_specs)
        configured_live_cap = self.app_config.live.hard_dollar_cap if not self.paper else 0.0
        portfolio_limits = portfolio_risk_limits_from_config(self.app_config.risk)
        portfolio_runtimes: list[PortfolioRuntime] = []
        for strategy, symbols, maximum_allocation in self._strategy_specs:
            product_universe = self._product_universes_by_market.get(strategy.market)
            if product_universe is None:
                raise RuntimeError(f"Missing product universe composition for {strategy.market.value}")
            product_universe = product_universe.with_observation_symbols(symbols)
            self._product_universes_by_market[strategy.market] = product_universe
            universe_version = (
                equity_universe_digest(self.app_config.universe.equity)
                if strategy.market == Market.EQUITY
                else crypto_scanner_config_digest(self.app_config.universe.crypto)
            )
            portfolio_runtimes.append(
                PortfolioRuntime(
                    strategy,
                    symbols,
                    self.broker,
                    self._portfolio_repository,
                    mode=self.mode.value,
                    maximum_allocation=maximum_allocation,
                    risk_limits=portfolio_limits,
                    universe=product_universe,
                    universe_version=universe_version,
                    account_id=self.broker.account_id or "unknown",
                    live_dollar_cap=configured_live_cap,
                    execution_policy=PortfolioExecutionPolicy(
                        rebalance_band_pct=self.app_config.execution.rebalance_band_pct,
                        minimum_order_notional=self.app_config.execution.minimum_order_notional,
                    ),
                )
            )
        self._portfolio_runtimes = tuple(portfolio_runtimes)
        if not self.paper:
            if live_approval is None:
                raise RuntimeError("Live startup approval was not verified")
            approval = live_approval
            actual_digests = tuple(runtime.configuration_digest for runtime in self._portfolio_runtimes)
            if actual_digests != expected_live_digests:
                raise RuntimeError("Live runtime configuration changed during startup")
            authorized_live_cap = min(configured_live_cap, approval.maximum_dollar_exposure)
            if authorized_live_cap <= 0:
                raise RuntimeError("Live operator approval does not authorize positive exposure")
            for runtime in self._portfolio_runtimes:
                runtime.live_dollar_cap = authorized_live_cap
        self._restore_persistent_state()
        if getattr(self, "_circuit_breaker_reason", None) is not None:
            # A persisted PERSISTENT_INCIDENT survives restarts until verified
            # recovery; surface it immediately so the operator is not surprised
            # by the halt on the first health poll.
            alerts.emit(
                "circuit_breaker",
                message="Persisted circuit breaker restored at startup",
                reason=self._circuit_breaker_reason,
            )
        self._last_account_at = datetime.now(UTC)
        self._state = "warming_up"
        stocks = sorted(self._symbols_by_market[Market.EQUITY])
        crypto = sorted(self._symbols_by_market[Market.CRYPTO])
        self.stream.subscribe(
            stocks,
            crypto,
            self._on_minute_bar,
            updated_handler=self._on_updated_minute_bar,
        )
        self._running = True
        self._warming_up = True
        self._threads = [
            threading.Thread(target=self._decision_loop, name="pb-decisions", daemon=True),
            threading.Thread(target=self._account_loop, name="pb-account", daemon=True),
            threading.Thread(
                target=self._run_stream_worker,
                args=("pb-trade-updates", self.trading_updates.run),
                name="pb-trade-updates",
                daemon=True,
            ),
        ]
        crypto_universe = self.app_config.universe.crypto
        if crypto_universe.enabled and crypto_universe.dynamic_selection_enabled:
            self._threads.append(
                threading.Thread(
                    target=self._universe_refresh_loop,
                    name="pb-universe-refresh",
                    daemon=True,
                )
            )
        if stocks:
            self._threads.append(
                threading.Thread(
                    target=self._run_stream_worker,
                    args=("pb-stocks", self.stream.run_stock),
                    name="pb-stocks",
                    daemon=True,
                )
            )
            # NOTE: pb-signal-adjustment is intentionally NOT started here.
            # It launches only AFTER _warmup() completes (see below): running
            # verification against half-seeded runtimes could pre-seed a
            # short provider tail that monotonic seed() would then never
            # backfill with the full warmup history.
        if crypto:
            self._threads.append(
                threading.Thread(
                    target=self._run_stream_worker,
                    args=("pb-crypto", self.stream.run_crypto),
                    name="pb-crypto",
                    daemon=True,
                )
            )
        with self._state_lock:
            for thread in self._threads:
                now = datetime.now(UTC)
                self._workers[thread.name] = WorkerHealth(now, now)
        self._stream_workers_launched = True
        for thread in self._threads:
            thread.start()
        try:
            self._warmup()
        except Exception as exc:
            self._state = "error"
            self.stop()
            if _is_transient_network_error(exc):
                raise TransientStartupError(f"Startup warmup deferred after transient network failure: {exc}") from exc
            raise
        finally:
            self._warming_up = False
        # Warmup is complete: every equity runtime holds its full seeded
        # history. Only NOW may signal-basis verification run — and even a
        # concurrent pass cannot truncate it, because verification merges
        # FULL-WINDOW provider authority rather than replacing with tails.
        if stocks:
            with self._state_lock:
                self._workers["pb-signal-adjustment"] = WorkerHealth(datetime.now(UTC), datetime.now(UTC))
            self._signal_adjustment_thread = threading.Thread(
                target=self._signal_adjustment_loop,
                name="pb-signal-adjustment",
                daemon=True,
            )
            self._signal_adjustment_thread.start()
        try:
            account = self.broker.account()
            self._publish_account_snapshot(account)
            self._persist_risk_state(account)
        except Exception as exc:
            self._state = "error"
            self.stop()
            if _is_transient_network_error(exc):
                raise TransientStartupError(
                    f"Startup account fetch deferred after transient network failure: {exc}"
                ) from exc
            raise
        # Restart recovery shares the one fresh account snapshot with every
        # market. The helper orders crypto first so its capped sleeve reserves
        # slots before equity, independent of runtime construction order.
        self._resume_startup_rebalances(account=account)
        # Seeded warmup history exists and the account snapshot is fresh: a
        # mid-period start must not leave a fresh account in cash until the
        # next cadence boundary (backtest decides on its first usable bar).
        # Normal no-trade/readiness outcomes are result statuses. Unexpected
        # exceptions are invalid composition/safety/provider failures and
        # must not be hidden behind an apparently running engine.
        try:
            self._run_startup_catch_up(account=account)
        except Exception as exc:
            self._state = "error"
            self.stop()
            if _is_transient_network_error(exc):
                raise TransientStartupError(
                    f"Startup catch-up deferred after transient network failure: {exc}"
                ) from exc
            raise
        self._refresh_readiness()
        with self._state_lock:
            # Worker threads have been live since before warmup: a halt they
            # raised (or a restored persistent incident) must not be
            # overwritten with a trading state here. A reconciliation reason
            # (e.g. a transient periodic-reconcile failure during warmup)
            # gates entries without halting, exactly as _reconcile_safely
            # leaves it: a clean reconcile clears it, and a pinned "halted"
            # would then have no reason left for reset_safety_lock to clear.
            breaker = self._circuit_breaker_reason
            if breaker is not None and breaker not in _RECONCILIATION_INCIDENT_REASONS:
                self._state = "halted"
            elif self._state not in _ENTRY_BLOCKING_STATES:
                self._state = "running" if self._has_ready_symbols() else "warming_up"
        record_session = getattr(self.ledger, "record_runtime_session", None)
        if record_session is not None:
            # Observability-only; fake ledgers in unit tests may omit it.
            self._runtime_session_id = record_session(self.broker.account_id or "unknown", self.mode.value)
        self._startup_committed = True
        try:
            if getattr(self, "_snapshot_writer", None) is not None:
                self._snapshot_writer.start()  # type: ignore[union-attr]
                self._snapshot_writer_started = True
        except Exception:
            # ``start()`` may have created a worker before raising.  Give the
            # writer its own best-effort stop even though the runtime commit
            # itself succeeded.
            try:
                self._snapshot_writer.stop(timeout=1.0)  # type: ignore[union-attr]
            except Exception:
                pass
            self._snapshot_writer_started = False
            log.warning("Runtime snapshot writer could not start", exc_info=True)
        try:
            if _observe is not None:
                _observe.on_runtime_started(
                    session_id=self._runtime_session_id,
                    mode=self.mode.value,
                    pid=None,
                )
        except Exception:
            pass
        runtime_metrics_store.record_event("runtime_started")
        alerts.emit(
            "runtime_started",
            message=f"Engine runtime started in {self.mode.value} mode",
            mode=self.mode.value,
            symbols=len(self.symbols),
            timeframes=",".join(sorted(self.aggregators)),
        )
        log.info(
            "Engine runtime started: mode=%s symbols=%d timeframe=%s",
            "paper" if self.paper else "live",
            len(self.symbols),
            ",".join(sorted(self.aggregators)),
        )

    def _universe_refresh_loop(self) -> None:
        worker = self._workers["pb-universe-refresh"]
        worker.alive = True
        # A fresh loop run starts with no pending retries: a bounded retry
        # scheduled by a previous run must not fire immediately on restart.
        self._universe_retry_at = {}
        self._universe_retry_attempt = {}
        interval = self.app_config.runtime.scanner_refresh_hours * 3600
        next_interval = time.monotonic() + interval
        self._next_universe_refresh_at = self._next_universe_deadline(next_interval)
        try:
            while self._running:
                worker.last_heartbeat = datetime.now(UTC)
                now_mono = time.monotonic()
                interval_due = now_mono >= next_interval
                retry_due = [market for market, deadline in self._universe_retry_at.items() if now_mono >= deadline]
                if interval_due or retry_due:
                    # Only crypto has a dynamic universe; it trades 24/7 on a
                    # fixed cadence.
                    markets_to_refresh: set[Market] = set(retry_due)
                    if interval_due:
                        markets_to_refresh.add(Market.CRYPTO)
                    for market in sorted(markets_to_refresh, key=lambda m: m.value):
                        try:
                            self._refresh_runtime_universes(market)
                        except Exception as exc:
                            worker.last_error = str(exc)
                            log.exception("Scheduled universe refresh failed: %s", market)
                            self._mark_universe_refresh_outcome(market, False)
                            continue
                        worker.last_event_at = datetime.now(UTC)
                        worker.last_error = None
                        status = getattr(self, "_universe_refresh_status", {}).get(market.value)
                        if status == "success":
                            self._mark_universe_refresh_outcome(market, True)
                        elif status in {"refresh_failed", "apply_failed"}:
                            self._mark_universe_refresh_outcome(market, False)
                    # Advance the deadlines even when the refresh failed:
                    # a stuck past deadline would otherwise busy-spin the
                    # loop and hammer Alpaca during an outage.
                    if interval_due:
                        next_interval = now_mono + interval
                    self._next_universe_refresh_at = self._next_universe_deadline(next_interval)
                remaining = next_interval - now_mono
                # Wake for a pending per-market retry before the next slot.
                for retry_at in self._universe_retry_at.values():
                    remaining = min(remaining, retry_at - now_mono)
                self._stop_event.wait(min(5.0, max(0.0, remaining)))
        finally:
            worker.alive = False

    def _mark_universe_refresh_outcome(self, market: Market, ok: bool) -> None:
        """Track per-market retry state for the scheduled refresh loop.

        A failed refresh schedules a bounded backoff retry (first ~5 min,
        then ~15, ~30, capped at ~60 min per consecutive failure) instead of
        waiting for the next full schedule slot; a success clears the market's
        retry state so a later failure restarts from the first rung.
        """
        if ok:
            self._universe_retry_at.pop(market, None)
            self._universe_retry_attempt.pop(market, None)
            try:
                if _observe is not None:
                    _observe.on_universe_refresh_complete(
                        market=market.value, session_id=getattr(self, "_runtime_session_id", None)
                    )
            except Exception:
                log.warning("Scanner refresh observation failed for %s", market.value, exc_info=True)
            # "Recovered" is a TRANSITION event: a refresh succeeding after a
            # prior failure. A healthy first success must not count as a
            # recovery (that would inflate scanner-recovery metrics).
            failed = getattr(self, "_scanner_failed", {})
            if failed.pop(market, False):
                runtime_metrics_store.record_event("scanner_recovered")
                alerts.emit(
                    "scanner_recovered",
                    message=f"Scanner refresh recovered for {market.value}",
                    market=market.value,
                )
            return
        attempt = self._universe_retry_attempt.get(market, 0)
        getattr(self, "_scanner_failed", {})[market] = True
        runtime_metrics_store.record_event("scanner_refresh_failure")
        alerts.emit(
            "scanner_refresh_failure",
            message=f"Scanner refresh failed for {market.value}",
            market=market.value,
            attempt=attempt + 1,
            error=getattr(self, "_last_universe_refresh_error", {}).get(market.value),
        )
        delay = _UNIVERSE_RETRY_BACKOFF_SECONDS[min(attempt, len(_UNIVERSE_RETRY_BACKOFF_SECONDS) - 1)]
        self._universe_retry_at[market] = time.monotonic() + delay
        self._universe_retry_attempt[market] = attempt + 1

    def _next_universe_deadline(self, next_interval: float) -> datetime:
        now_mono = time.monotonic()
        interval_remaining = max(0.0, next_interval - now_mono)
        deadline = datetime.now(UTC) + timedelta(seconds=interval_remaining)
        for retry_at in getattr(self, "_universe_retry_at", {}).values():
            retry_remaining = max(0.0, retry_at - now_mono)
            deadline = min(deadline, datetime.now(UTC) + timedelta(seconds=retry_remaining))
        return deadline

    def _resync_stream_if_needed(self) -> None:
        """Re-converge the live stream from the authoritative membership.

        Called after a hot-swap rollback failure (socket state unknown) so the
        stream is forced back to the desired observation membership. Clears
        the subscription-state-unknown entry halt on success; keeps it on
        failure so entries stay blocked.
        """
        if not getattr(self, "_resync_pending", False):
            return
        clear = getattr(self.stream, "clear_rollback_failure", None)
        if clear is not None:
            clear()
        self.stream.update_subscriptions(
            sorted(self._symbols_by_market[Market.EQUITY]),
            sorted(self._symbols_by_market[Market.CRYPTO]),
        )
        self._resync_pending = False
        with getattr(self, "_state_lock", threading.RLock()):
            for market in list(self._market_entry_halts):
                if self._market_entry_halts.get(market) == "subscription_state_unknown":
                    self._market_entry_halts[market] = None
        log.warning("Stream membership reconnected from the authoritative desired set")

    def _refresh_runtime_universes(self, target: Market | None = None) -> None:
        """Refresh the dynamic (crypto) scanner universe.

        ``target=None`` refreshes every enabled dynamic universe (startup and
        backfill paths); the scheduled loop passes a single market. The equity
        universe is static and never refreshed.
        """
        if getattr(self, "_resync_pending", False):
            try:
                self._resync_stream_if_needed()
            except Exception as exc:
                log.error(
                    "Stream resync failed; subscription state stays unknown and entries stay halted: %s",
                    exc,
                )
        desired: dict[Market, tuple[str, ...]] = {}
        product_universes: dict[Market, ActiveProductUniverse] = {}

        def refresh_one(
            market: Market,
            operation: Callable[[], tuple[tuple[str, ...], ActiveProductUniverse]],
        ) -> None:
            try:
                selected, product_universe = operation()
                if not selected:
                    raise RuntimeError(f"Scanner produced no valid {market.value} universe")
                desired[market] = tuple(selected)
                product_universes[market] = product_universe
                self._universe_refresh_status[market.value] = "success"
                self._last_universe_refresh_error[market.value] = None
            except Exception as exc:
                self._universe_refresh_status[market.value] = "refresh_failed"
                self._last_universe_refresh_error[market.value] = str(exc)
                log.error("%s universe refresh failed; active membership unchanged: %s", market, exc)
                _symbols, snapshot_status = validate_active_crypto_universe(self.app_config.universe.crypto)
                fallback_allowed = self.app_config.universe.crypto.allow_static_fallback_trading
                if snapshot_status != "success" and not fallback_allowed:
                    with getattr(self, "_state_lock", threading.RLock()):
                        self._market_entry_halts[market] = "invalid_dynamic_universe"

        crypto_config = self.app_config.universe.crypto
        if target in {None, Market.CRYPTO} and crypto_config.enabled and crypto_config.dynamic_selection_enabled:

            def refresh_crypto() -> tuple[tuple[str, ...], ActiveProductUniverse]:
                refresh_crypto_universe(self.app_config, self.api_key, self.secret_key)
                selected = load_active_crypto_symbols(crypto_config, log_rejection=False)
                return selected, load_active_product_universe(
                    crypto_config.active_universe_path,
                    market=Market.CRYPTO,
                    expected_observation_symbols=selected,
                    expected_config_digest=crypto_scanner_config_digest(crypto_config),
                )

            refresh_one(Market.CRYPTO, refresh_crypto)
        if desired:
            try:
                self._apply_runtime_universes(desired, product_universes=product_universes)
            except Exception as exc:
                for market in desired:
                    self._universe_refresh_status[market.value] = "apply_failed"
                    self._last_universe_refresh_error[market.value] = str(exc)
                raise
            else:
                with getattr(self, "_state_lock", threading.RLock()):
                    for market in desired:
                        if self._market_entry_halts.get(market) == "invalid_dynamic_universe":
                            self._market_entry_halts[market] = None
        self._last_universe_refresh_at = datetime.now(UTC)

    def _apply_runtime_universes(
        self,
        desired_by_market: dict[Market, tuple[str, ...]],
        *,
        product_universes: dict[Market, ActiveProductUniverse] | None = None,
    ) -> dict[Market, dict[str, tuple[str, ...]]]:
        """Apply scanner changes while retaining every owned/open-order symbol."""
        product_universes = product_universes or {}
        account = self.broker.account()
        account_id = self.broker.account_id or "unknown"
        manage_external = bool(getattr(getattr(self, "config", None), "manage_external_positions", False))
        # Only bot-owned exposure is pinned: ledger lots, portfolio-owned
        # positions (managed quantity > 0), and open portfolio orders.
        # Manually opened external positions and manual open orders are not
        # the runtime's inventory and must not consume stream slots unless
        # the operator opted in.
        protected = {
            canonical_symbol(position.symbol, position.market)
            for position in account.positions
            if not position.is_flat and (position.managed_qty > 1e-12 or manage_external)
        }
        protected.update(self.broker.portfolio_open_order_symbols())
        protected.update(item.canonical_symbol for item in self.ledger.managed_entries_for_account(account_id))
        applied: dict[Market, tuple[str, ...]] = {}
        changes: dict[Market, dict[str, tuple[str, ...]]] = {}
        strategy_enabled = getattr(self, "_strategy_enabled", {})
        for market, requested in desired_by_market.items():
            current = self._symbols_by_market[market]
            market_protected = tuple(
                sorted(symbol for symbol in protected if is_crypto(symbol) == (market == Market.CRYPTO))
            )
            if market == Market.EQUITY:
                equity_config = self.app_config.universe.equity
                # Universe symbols are strategy inputs; with the strategy
                # disabled observation keeps only the managed/protection pins.
                configured_pins = equity_config.symbols if strategy_enabled.get(market, True) else ()
                maximum_symbols = equity_config.maximum_stream_symbols
            else:
                crypto_config = self.app_config.universe.crypto
                configured_pins = crypto_config.pinned_symbols if strategy_enabled.get(market, True) else ()
                maximum_symbols = crypto_config.maximum_stream_symbols
            if not strategy_enabled.get(market, True):
                requested = ()
            reserved = tuple(dict.fromkeys((*configured_pins, *market_protected)))
            if len(reserved) > maximum_symbols:
                raise RuntimeError(f"Protected {market.value} symbols exceed stream capacity")
            ordered = tuple(dict.fromkeys((*reserved, *requested)))
            selected = ordered[:maximum_symbols]
            # A scanner-requested removal is legal only when the symbol has no
            # position or open-order lineage. Protected symbols were reserved
            # above and therefore replace lower-ranked ephemeral members.
            selected_set = set(selected)
            added = tuple(sorted(selected_set - current))
            removed = tuple(sorted(current - selected_set))
            applied[market] = selected
            changes[market] = {"added": added, "removed": removed}

        if not any(result["added"] or result["removed"] for result in changes.values()):
            if product_universes:
                state_lock = getattr(self, "_state_lock", threading.RLock())
                portfolio_lock = getattr(self, "_portfolio_lock", threading.RLock())
                with self._ingestion_lock, state_lock, portfolio_lock:
                    current_snapshots = dict(getattr(self, "_product_universes_by_market", {}))
                    for market, snapshot in product_universes.items():
                        bound = snapshot.with_observation_symbols(applied[market])
                        current_snapshots[market] = bound
                        for runtime in self._portfolio_runtimes:
                            if runtime.market == market:
                                runtime.universe = bound
                    self._product_universes_by_market = current_snapshots
            log.info("Universe hot-swap produced no membership change; subscription kept")
            return changes

        # Fail-closed transactional transition: warmup the new members before
        # the stream changes, then converge the stream, then commit runtime
        # state. Any failure before the commit leaves the previous universe
        # fully subscribed and stateful.
        old_symbols = {
            Market.EQUITY: tuple(sorted(self._symbols_by_market[Market.EQUITY])),
            Market.CRYPTO: tuple(sorted(self._symbols_by_market[Market.CRYPTO])),
        }
        newly_added = sorted(symbol for result in changes.values() for symbol in result["added"])
        removed_all = {symbol for result in changes.values() for symbol in result["removed"]}
        state_lock = getattr(self, "_state_lock", threading.RLock())
        with state_lock:
            previous_not_ready = set(self._not_ready_symbols)
            previous_allowed_trade = (
                set(self.market_state.allowed_trade_symbols)
                if self.market_state.allowed_trade_symbols is not None
                else None
            )
        with state_lock:
            self._not_ready_symbols.update(newly_added)
        staged: dict[tuple[str, str], tuple[list[Bar], list[Bar]]] = {}
        try:
            if newly_added and self._historical is not None:
                staged = self._warmup_symbols(self._historical, newly_added, retry=False, staged_only=True) or {}
        except Exception:
            with state_lock:
                self._not_ready_symbols = previous_not_ready
            raise

        try:
            self.stream.update_subscriptions(
                sorted(applied.get(Market.EQUITY, old_symbols[Market.EQUITY])),
                sorted(applied.get(Market.CRYPTO, old_symbols[Market.CRYPTO])),
            )
        except Exception:
            # The stream refused the new membership: the runtime state was
            # never committed, so drop the warming marks again.
            with state_lock:
                self._not_ready_symbols = previous_not_ready
            if getattr(self.stream, "rollback_failed", False):
                # The rollback itself failed: the socket membership is UNKNOWN.
                # Never claim the previous subscription survived. Halt entries
                # for every touched market and force a reconnect from the
                # authoritative desired membership on the next refresh/reset.
                log.critical(
                    "Stream rollback failed after a hot-swap error; socket "
                    "membership is unknown. Halting entries until the stream "
                    "reconnects from the authoritative membership."
                )
                with state_lock:
                    for market in applied:
                        self._market_entry_halts[market] = "subscription_state_unknown"
                self._resync_pending = True
            raise
        portfolio_lock = getattr(self, "_portfolio_lock", threading.RLock())
        try:
            with self._ingestion_lock, state_lock, portfolio_lock:
                previous_by_market = {market: set(self._symbols_by_market[market]) for market in applied}
                previous_portfolio_symbols = {
                    runtime.market: getattr(runtime, "symbols", ()) for runtime in self._portfolio_runtimes
                }
                previous_portfolio_universes = [
                    (runtime, getattr(runtime, "universe", None)) for runtime in self._portfolio_runtimes
                ]
                previous_product_universes = dict(getattr(self, "_product_universes_by_market", {}))
                for market, selected in applied.items():
                    self._symbols_by_market[market] = set(selected)
                    bound_universe = (
                        product_universes[market].with_observation_symbols(selected)
                        if market in product_universes
                        else None
                    )
                    for runtime in self._portfolio_runtimes:
                        if runtime.market == market:
                            runtime.update_symbols(selected)
                            if bound_universe is not None:
                                runtime.universe = bound_universe
                    if bound_universe is not None:
                        self._product_universes_by_market[market] = bound_universe
                # New members were staged during warmup but could only be
                # seeded now that the runtimes include them; seeding inside
                # this transaction keeps the transition atomic.
                for (symbol, timeframe), (raw_tail, signal_tail) in staged.items():
                    # RAW bars feed the execution-side market state; the
                    # strategy's signal history receives its own SPLITS-basis
                    # series (crypto: the same RAW bars).
                    self.market_state.seed(raw_tail)
                    for runtime in self._portfolio_runtimes:
                        if runtime.strategy.timeframe == timeframe and (
                            is_crypto(symbol) == (runtime.market == Market.CRYPTO)
                        ):
                            runtime.seed(signal_tail)
                self.symbols = sorted(self._symbols_by_market[Market.EQUITY] | self._symbols_by_market[Market.CRYPTO])
                self._expected_all_symbols = set(self.symbols)
                self._not_ready_symbols.difference_update(removed_all)
                if self.market_state.allowed_trade_symbols is not None:
                    self.market_state.allowed_trade_symbols = {
                        *self._symbols_by_market[Market.CRYPTO],
                        *self._symbols_by_market[Market.EQUITY],
                    }
        except Exception:
            # Commit failed after the stream converged: restore both the
            # runtime state and the stream to the previous universe so the
            # runtime never observes a half-applied membership.
            log.exception("Universe hot-swap commit failed; rolling back")
            with self._ingestion_lock, state_lock, portfolio_lock:
                self._symbols_by_market.update(previous_by_market)
                for market, previous in previous_portfolio_symbols.items():
                    for runtime in self._portfolio_runtimes:
                        if runtime.market == market:
                            try:
                                runtime.update_symbols(previous)
                            except Exception:
                                # A failed restore must not abort the rest of
                                # the rollback; the symbol set is restored
                                # below and the stream reverts regardless.
                                log.exception("Portfolio runtime restore failed for %s", market.value)
                for runtime, previous_universe in previous_portfolio_universes:
                    runtime.universe = previous_universe
                self._product_universes_by_market = previous_product_universes
                self.symbols = sorted(self._symbols_by_market[Market.EQUITY] | self._symbols_by_market[Market.CRYPTO])
                self._expected_all_symbols = set(self.symbols)
                self._not_ready_symbols = previous_not_ready
                if self.market_state.allowed_trade_symbols is not None:
                    self.market_state.allowed_trade_symbols = (
                        previous_allowed_trade
                        if previous_allowed_trade is not None
                        else self.market_state.allowed_trade_symbols
                    )
            try:
                self.stream.update_subscriptions(list(old_symbols[Market.EQUITY]), list(old_symbols[Market.CRYPTO]))
            except Exception:
                log.exception("Stream rollback after failed hot-swap commit also failed")
                # The socket membership is now unknown: halt entries for the
                # touched markets and force a reconnect from the authoritative
                # desired membership instead of claiming the old state holds.
                with state_lock:
                    for market in applied:
                        self._market_entry_halts[market] = "subscription_state_unknown"
                self._resync_pending = True
            raise

        self._refresh_readiness()
        log.info(
            "Universe hot-swap applied: %s",
            {market.value: {key: list(value) for key, value in result.items()} for market, result in changes.items()},
        )
        return changes

    def _recover_portfolio_fill(
        self,
        client_order_id: str,
        broker_order_id: str,
        filled_qty: float,
        avg_price: float | None,
        occurred_at: datetime,
        activity_id: str,
        fee: float | None,
        *,
        status: str = "filled",
    ) -> None:
        """Book a real fill recovered from Alpaca order history at its actual
        price (see AlpacaBroker._reconcile_stale_portfolio_ownership)."""
        applied = self._portfolio_repository.apply_trade_update(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            status=status,
            cumulative_filled_quantity=filled_qty,
            average_fill_price=avg_price,
            occurred_at=occurred_at,
            activity_id=activity_id,
            fee=fee,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            source="recovered_submission",
            received_at=datetime.now(UTC),
            fee_observed=fee is not None,
        )
        if not applied:
            raise RuntimeError(f"Recovered broker order has no durable portfolio row: {client_order_id}")

    def _pin_managed_symbols(self) -> None:
        """Keep managed positions observable even after scanner/config removal."""
        account_id = self.broker.account_id or "unknown"
        entries = self.ledger.managed_entries_for_account(account_id)
        repository = getattr(self, "_portfolio_repository", None)
        # Only symbols still owned (net filled quantity > 0) or with an open
        # portfolio order are pinned; a fully exited symbol stops consuming a
        # stream slot. Manual broker orders are never pins.
        portfolio_symbols = (
            repository.owned_symbols(account_id) | self.broker.portfolio_open_order_symbols()
            if repository is not None
            else set()
        )
        try:
            broker_positions = self.broker.account().positions
        except Exception:
            broker_positions = ()
        portfolio_pins = tuple(
            canonical_symbol(position.symbol, position.market)
            for position in broker_positions
            if canonical_symbol(position.symbol, position.market) in portfolio_symbols and not position.is_flat
        )
        equity_pins = tuple(
            dict.fromkeys(
                (
                    *(item.canonical_symbol for item in entries if item.market == Market.EQUITY),
                    *(symbol for symbol in portfolio_pins if not is_crypto(symbol)),
                )
            )
        )
        crypto_pins = tuple(
            dict.fromkeys(
                (
                    *(item.canonical_symbol for item in entries if item.market == Market.CRYPTO),
                    *(symbol for symbol in portfolio_pins if is_crypto(symbol)),
                )
            )
        )
        self._managed_pin_symbols = {*equity_pins, *crypto_pins}
        strategy_enabled = getattr(self, "_strategy_enabled", {})
        if equity_pins:
            maximum = self.app_config.universe.equity.maximum_stream_symbols
            # Universe symbols are strategy inputs; a disabled equity
            # strategy only needs the managed pins themselves.
            references = self.app_config.universe.equity.symbols if strategy_enabled.get(Market.EQUITY, True) else ()
            ordered = tuple(dict.fromkeys((*references, *equity_pins, *sorted(self._symbols_by_market[Market.EQUITY]))))
            if len({*references, *equity_pins}) > maximum:
                raise RuntimeError("Managed equity pins and references exceed the configured IEX stream limit")
            self._symbols_by_market[Market.EQUITY] = set(ordered[:maximum])
        if crypto_pins:
            log.info(
                "Managed crypto positions pinned to active universe: %s",
                ",".join(crypto_pins),
            )
        crypto_config = self.app_config.universe.crypto
        current_crypto = getattr(
            self,
            "_active_crypto_symbols",
            tuple(sorted(self._symbols_by_market[Market.CRYPTO])),
        )
        if crypto_config.dynamic_selection_enabled:
            # Configured crypto pins belong to the crypto strategy; with the
            # strategy disabled only managed positions are observed.
            configured_pins = crypto_config.pinned_symbols if strategy_enabled.get(Market.CRYPTO, True) else ()
            reserved = tuple(dict.fromkeys((*configured_pins, *crypto_pins)))
            if len(reserved) > crypto_config.maximum_stream_symbols:
                raise RuntimeError("Configured and managed crypto pins exceed maximum_stream_symbols")
            active_crypto = tuple(dict.fromkeys((*reserved, *current_crypto)))[: crypto_config.maximum_stream_symbols]
        else:
            active_crypto = tuple(dict.fromkeys((*current_crypto, *crypto_pins)))
        self._active_crypto_symbols = active_crypto
        self._symbols_by_market[Market.CRYPTO] = set(active_crypto)
        # A market with pins is observed (and protected) even when its
        # strategy is disabled; health reporting and worker requirements
        # follow the actual observation membership.
        self._market_enabled = getattr(self, "_market_enabled", {})
        for candidate_market, symbols in self._symbols_by_market.items():
            if symbols:
                self._market_enabled[candidate_market] = True
        if self.market_state.allowed_trade_symbols is not None:
            self.market_state.allowed_trade_symbols = {
                symbol for symbol in self.market_state.allowed_trade_symbols if not is_crypto(symbol)
            }
            self.market_state.allowed_trade_symbols.update(active_crypto)
        self.symbols = sorted(self._symbols_by_market[Market.EQUITY] | self._symbols_by_market[Market.CRYPTO])
        self._expected_all_symbols = set().union(
            *(
                symbols
                for candidate_market, symbols in self._symbols_by_market.items()
                if getattr(self, "_market_enabled", {}).get(candidate_market, False)
            )
        )
        self._not_ready_symbols.update(equity_pins)
        self._not_ready_symbols.update(crypto_pins)

    def stop(self) -> bool:
        self._startup_cleanup_attempted = True
        alive: list[str] = []
        cleanup_errors: list[str] = []
        storage_errors: list[str] = []
        previous_state = getattr(self, "_state", None)
        breaker_at_stop = getattr(self, "_circuit_breaker_reason", None)
        if getattr(self, "_running", False):
            self._state = "stopping"
            self._running = False
            self._stop_event.set()
        try:
            session_id = getattr(self, "_runtime_session_id", None)
            if _observe is not None and (getattr(self, "_startup_committed", False) or session_id is not None):
                _observe.on_runtime_stopping(session_id=session_id, reason="stop")
        except Exception:
            pass
        if getattr(self, "_snapshot_writer_started", False) and getattr(self, "_snapshot_writer", None) is not None:
            try:
                self._snapshot_writer.write_stopped()  # type: ignore[union-attr]
            except Exception:
                pass
            finally:
                try:
                    self._snapshot_writer.stop(timeout=1.0)  # type: ignore[union-attr]
                except Exception:
                    pass
                self._snapshot_writer_started = False
        try:
            if hasattr(self, "_events") and hasattr(self._events, "put_nowait"):
                self._events.put_nowait(None)
        except queue.Full:
            try:
                with contextlib.suppress(queue.Empty):
                    if hasattr(self, "_events"):
                        self._events.get_nowait()
                with contextlib.suppress(queue.Full):
                    if hasattr(self, "_events"):
                        self._events.put_nowait(None)
            except Exception:
                pass
        # A start that aborted before launching the stream workers has no
        # socket to stop. getattr default True: partially constructed
        # runtimes (EngineRuntime.__new__ in tests) keep the stop attempt.
        streams = (
            (getattr(self, "stream", None), getattr(self, "trading_updates", None))
            if getattr(self, "_stream_workers_launched", True)
            else ()
        )
        for stream in streams:
            if stream is None:
                continue
            stop_stream = getattr(stream, "stop", None)
            if not callable(stop_stream):
                continue
            try:
                stop_stream()
            except Exception as exc:
                log.warning("Stream stop warning: %s", exc)
                cleanup_errors.append(f"stream:{type(exc).__name__}")
        # The join phase runs on EVERY stop attempt, not only the first: a
        # prior attempt may have timed out (stop_failed) with workers still
        # finishing. Re-running it guarantees storage below is closed only
        # once no worker can still be mid-write.
        # getattr defaults: partially constructed runtimes (tests use
        # EngineRuntime.__new__) may stop before start() created the workers.
        for thread in getattr(self, "_threads", ()):
            thread.join(timeout=5)
        alive = [thread.name for thread in getattr(self, "_threads", ()) if thread.is_alive()]
        reconcile_thread = getattr(self, "_reconcile_thread", None)
        if reconcile_thread is not None:
            reconcile_thread.join(timeout=30)
            if reconcile_thread.is_alive():
                alive.append(reconcile_thread.name)
        warmup_thread = getattr(self, "_warmup_retry_thread", None)
        if warmup_thread is not None:
            warmup_thread.join(timeout=30)
            if warmup_thread.is_alive():
                alive.append(warmup_thread.name)
        # The signal-adjustment monitor MUST be joined before the historical
        # data client below is closed: a late verification pass would otherwise
        # hit a dead HTTP session mid-request.
        signal_adjustment_thread = getattr(self, "_signal_adjustment_thread", None)
        if signal_adjustment_thread is not None:
            signal_adjustment_thread.join(timeout=10)
            if signal_adjustment_thread.is_alive():
                alive.append(signal_adjustment_thread.name)
        if alive:
            self._state = "stop_failed"
            log.error("Runtime workers did not stop cleanly: %s", ", ".join(alive))
            return False
        # Release broker/data HTTP session pools on every start/stop cycle,
        # only after every worker joined (never under a stuck request).
        for candidate in (
            getattr(self, "broker", None),
            getattr(self, "_historical", None),
        ):
            close = getattr(candidate, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception as exc:
                log.warning("Session close warning during stop: %s", exc)
                cleanup_errors.append(f"session:{type(exc).__name__}")
        # A stop from a halted/error state (or while a circuit breaker is
        # active) is an unexpected shutdown: alert and record the session as
        # such. Graceful stops just close the session row.
        unexpected = previous_state in {"halted", "error", "stop_failed"} or breaker_at_stop is not None
        if unexpected and getattr(self, "_runtime_session_id", None) is not None:
            # Only a stop after a completed start is an unexpected shutdown;
            # a failed start (no session row, no runtime_started) must not
            # produce a phantom stopped-unexpected event.
            runtime_metrics_store.record_event("runtime_stopped_unexpected")
            alerts.emit(
                "runtime_stopped_unexpected",
                message="Engine runtime stopped unexpectedly",
                previous_state=previous_state,
                circuit_breaker_reason=breaker_at_stop,
            )
        session_id = getattr(self, "_runtime_session_id", None)
        ledger = getattr(self, "ledger", None)
        if session_id is not None and ledger is not None:
            try:
                ledger.end_runtime_session(
                    session_id,
                    "unexpected" if unexpected else "stopped",
                )
            except Exception as exc:
                log.warning("Runtime session end failed: %s", exc)
                storage_errors.append(f"runtime_session:{type(exc).__name__}")
            else:
                self._runtime_session_id = None
        if not getattr(self, "_storage_closed", False):
            for runtime in getattr(self, "_portfolio_runtimes", ()):
                try:
                    runtime.close("stopped")
                except Exception as exc:
                    storage_errors.append(f"portfolio:{type(exc).__name__}")
                    log.exception("Portfolio runtime storage close failed: %s", exc)
            # Give in-flight stream-thread writes a moment to land before the
            # SQLite connection below is released.  The ledger owns the final
            # reference to the store used by these session-finalization writes.
            close_ledger = getattr(ledger, "close", None)
            if callable(close_ledger) and not storage_errors:
                time.sleep(0.25)
                try:
                    close_ledger()
                except Exception as exc:
                    storage_errors.append(f"ledger:{type(exc).__name__}")
                    log.exception("Execution ledger close failed: %s", exc)
            cleanup_errors.extend(storage_errors)
            # Session finalization and ledger release share one SQLite store.
            # Defer the final release after any write failure so a later stop
            # can retry those writes on a live connection.
            self._storage_closed = not storage_errors
        # Preserve a pre-commit safety halt/error for operator visibility;
        # transient and ordinary setup failures return to the reusable
        # stopped state after their attempt-owned resources are closed.
        if not getattr(self, "_startup_committed", False) and previous_state in {"halted", "error"}:
            self._state = previous_state
        else:
            self._state = "stopped"
        self._startup_committed = False
        if cleanup_errors:
            self._state = "stop_failed"
            log.error("Runtime cleanup did not complete: %s", ", ".join(cleanup_errors))
            return False
        log.info("Engine runtime stopped")
        return True

    def _account_snapshot_guard(self) -> LockType:
        """Return the narrow lock protecting the account cache metadata."""
        lock = getattr(self, "_account_snapshot_lock", None)
        if lock is None:
            # Production instances initialize this in __init__; the guarded
            # lazy path keeps small EngineRuntime.__new__ test shells safe too.
            with _ACCOUNT_SNAPSHOT_INIT_LOCK:
                lock = getattr(self, "_account_snapshot_lock", None)
                if lock is None:
                    lock = threading.Lock()
                    self._account_snapshot_lock = lock
        return lock

    def _account_snapshot_cache_state(self) -> tuple[AccountSnapshot | None, datetime | None, int]:
        """Read the immutable cache pair and its invalidation generation."""
        with self._account_snapshot_guard():
            return (
                getattr(self, "_last_account_snapshot", None),
                getattr(self, "_last_account_snapshot_at", None),
                int(getattr(self, "_account_snapshot_generation", 0)),
            )

    def _publish_account_snapshot(
        self,
        account: AccountSnapshot,
        *,
        expected_generation: int | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """Publish a snapshot only if no execution invalidated its fetch.

        Broker calls happen outside this lock. The generation check makes an
        in-flight pre-fill fetch lose the race rather than republishing stale
        account state as fresh; callers choose whether to refetch or fail
        closed after a rejected publication.
        """
        with self._account_snapshot_guard():
            generation = int(getattr(self, "_account_snapshot_generation", 0))
            if expected_generation is not None and generation != expected_generation:
                return False
            published_at = timestamp or datetime.now(UTC)
            self._last_account_snapshot = account
            self._last_account_snapshot_at = published_at
            self._last_account_at = published_at
            return True

    def _fresh_account_snapshot(self, *, _allow_retry: bool = True) -> AccountSnapshot:
        """The freshest account snapshot, fetched at most once per freshness
        boundary.

        Returns the account-loop snapshot while it is within
        ``maximum_account_age_for_entry_seconds`` (the same freshness
        contract the entry-readiness gate enforces), and otherwise fetches a
        fresh snapshot from the broker and updates the shared cache. This
        turns the per-batch decision-path account fetch into a cache read in
        the common case; the broker is still polled every
        ``account_poll_interval_seconds`` by the account loop, which is
        strictly fresher than the tolerated boundary.
        """
        snapshot, snapshot_at, fetch_generation = self._account_snapshot_cache_state()
        if (
            snapshot is not None
            and snapshot_at is not None
            and (datetime.now(UTC) - snapshot_at).total_seconds() <= self.config.maximum_account_age_for_entry_seconds
        ):
            return snapshot
        # Stale account -> BROKER_ACCOUNT_STALE incident: reconcile-first style fetch with bounded handling
        # Never blind retry; stale snapshot pauses decisions via entry latch, but we still produce canonical record
        try:
            account: AccountSnapshot = self.broker.account()
        except Exception as exc:
            # Provider/broker temporary -> bounded retry 5 attempts; DB errors fail closed immediately is handled elsewhere
            if _is_database_error(exc):
                automate_incident_recovery(
                    IncidentCategory.DATABASE_ERROR,
                    account_id=getattr(self.broker, "account_id", "unknown"),
                    error=str(exc),
                    current_state={"fresh_account_fetch": True},
                )
                raise
            # For broker timeouts / stale account we emit BROKER_ACCOUNT_STALE and fail closed for this cycle
            automate_incident_recovery(
                IncidentCategory.BROKER_ACCOUNT_STALE,
                account_id=getattr(self.broker, "account_id", "unknown"),
                error=str(exc),
                current_state={
                    "fresh_account_fetch": True,
                    "age_seconds": (datetime.now(UTC) - snapshot_at).total_seconds() if snapshot_at else None,
                },
            )
            raise
        now = datetime.now(UTC)
        if self._publish_account_snapshot(account, expected_generation=fetch_generation, timestamp=now):
            return account
        if _allow_retry:
            # An execution invalidated this fetch while the broker call was in
            # flight. Refetch once against the new generation; a second race
            # fails closed instead of ever returning an unverified snapshot.
            return self._fresh_account_snapshot(_allow_retry=False)
        raise RuntimeError("Account snapshot invalidated during broker fetch")

    def _fresh_account_snapshot_lease(self, *, _allow_retry: bool = True) -> _AccountSnapshotLease:
        """Fetch a snapshot and retain the cache generation used by a decision.

        The account fetch itself remains outside the portfolio lock. The
        lease is a narrow compare-at-use fence: an execution invalidating the
        cache after this method returns makes the decision skip rather than
        sizing against pre-fill positions. Lightweight ``__new__`` test
        doubles that override ``_fresh_account_snapshot`` and have no cache
        metadata receive an unvalidated lease for compatibility.
        """
        account = self._fresh_account_snapshot()
        snapshot, snapshot_at, generation = self._account_snapshot_cache_state()
        if snapshot is account and snapshot_at is not None:
            return _AccountSnapshotLease(account, generation)
        if not getattr(self, "_account_snapshot_lease_enabled", False):
            return _AccountSnapshotLease(account, None)
        if _allow_retry:
            # Invalidation after _fresh_account_snapshot returned but before
            # metadata was captured: reacquire against the new generation.
            return self._fresh_account_snapshot_lease(_allow_retry=False)
        raise RuntimeError("Account snapshot invalidated between fetch return and decision lease")

    def _account_snapshot_lease_is_current(self, lease: _AccountSnapshotLease) -> bool:
        """Atomically validate a decision lease immediately before use."""
        if lease.generation is None:
            return True
        with self._account_snapshot_guard():
            active = getattr(self, "_account_snapshot_active_leases", None) or set()
            return (
                int(getattr(self, "_account_snapshot_generation", 0)) == lease.generation
                and getattr(self, "_last_account_snapshot", None) is lease.snapshot
                and getattr(self, "_last_account_snapshot_at", None) is not None
                and (not getattr(self, "_account_snapshot_invalidation_pending", False) or id(lease) in active)
            )

    def _claim_account_snapshot_lease(self, lease: _AccountSnapshotLease) -> bool:
        """Claim a valid lease without retaining the cache lock.

        The claim is the linearization point for decision use. An execution
        invalidation that arrives after it is deferred until release, so it
        cannot fall into the check-to-call gap; no broker or order I/O runs
        while the cache lock is held.
        """
        if lease.generation is None:
            return True
        with self._account_snapshot_guard():
            active = getattr(self, "_account_snapshot_active_leases", None)
            if not (
                int(getattr(self, "_account_snapshot_generation", 0)) == lease.generation
                and getattr(self, "_last_account_snapshot", None) is lease.snapshot
                and getattr(self, "_last_account_snapshot_at", None) is not None
                and not getattr(self, "_account_snapshot_invalidation_pending", False)
            ):
                return False
            if active is None:
                active = set()
                self._account_snapshot_active_leases = active
            active.add(id(lease))
            return True

    def _release_account_snapshot_lease(self, lease: _AccountSnapshotLease) -> None:
        """Release a decision claim and publish any deferred invalidation."""
        if lease.generation is None:
            return
        with self._account_snapshot_guard():
            active = getattr(self, "_account_snapshot_active_leases", None)
            if active is None or id(lease) not in active:
                return
            active.remove(id(lease))
            if not active and getattr(self, "_account_snapshot_invalidation_pending", False):
                self._account_snapshot_generation = int(getattr(self, "_account_snapshot_generation", 0)) + 1
                self._last_account_snapshot_at = None
                self._account_snapshot_invalidation_pending = False

    def _invalidate_account_snapshot_after_execution(self) -> None:
        """Force the next entry decision to refetch post-fill positions."""
        with self._account_snapshot_guard():
            if getattr(self, "_account_snapshot_active_leases", None):
                self._account_snapshot_invalidation_pending = True
            else:
                self._account_snapshot_generation = int(getattr(self, "_account_snapshot_generation", 0)) + 1
                self._last_account_snapshot_at = None

    def _account_loop(self) -> None:
        worker = self._workers["pb-account"]
        worker.alive = True
        try:
            while self._running:
                worker.last_heartbeat = datetime.now(UTC)
                try:
                    _, _, fetch_generation = self._account_snapshot_cache_state()
                    account = self.broker.account()
                    now = datetime.now(UTC)
                    if not self._publish_account_snapshot(
                        account,
                        expected_generation=fetch_generation,
                        timestamp=now,
                    ):
                        # An execution invalidated the fetch while the broker
                        # call was in flight. Do not publish or persist the
                        # stale result; the next poll fetches a new generation.
                        worker.last_error = "account snapshot invalidated during fetch"
                        log.warning("Discarded account snapshot invalidated during broker fetch")
                    else:
                        worker.last_event_at = now
                        worker.last_error = None
                        self._portfolio_repository.update_high_water(
                            self.broker.account_id or "unknown", account.equity
                        )
                        self._persist_risk_state(account)
                except OwnershipDriftError:
                    # Ownership drift is not a transient broker/API error: the
                    # broker holds fewer shares than the repository claims as
                    # managed. Halt the runtime, persist the safety state and
                    # keep entries blocked instead of logging and continuing
                    # until the next strategy decision.
                    try:
                        self._halt_for_ownership_drift()
                    except Exception as exc:
                        # The halt is already asserted in memory before the
                        # persist; a failed ledger write must not kill the
                        # only drift detector. The next poll re-enters and
                        # retries the persist.
                        worker.last_error = str(exc)
                        log.exception("Ownership drift halt could not be persisted; retrying next poll")
                except Exception as exc:
                    # Temporary broker/API errors are retried on the next
                    # poll; only drift escalates to a runtime halt here.
                    worker.last_error = str(exc)
                    log.warning("Account refresh failed: %s", exc)
                self._stop_event.wait(self.config.account_poll_interval_seconds)
        finally:
            worker.alive = False

    def _evaluate_entry_risk_latch(self, account: AccountSnapshot) -> None:
        """Re-evaluate the entry-risk latch from the latest account state.

        Daily loss and drawdown breach limits latch entries (SELL-only
        decisions still run); the latch clears when the condition recovers.
        Account freshness is enforced by the readiness gate on the same data.

        A drawdown breach is additionally mirrored as a persisted
        ``portfolio_drawdown_halt`` safety incident: the drawdown peak is
        monotonic and entries stay blocked, so the breach is an operational
        incident whose explicit recovery is a verified risk-epoch reset
        (``start_new_risk_epoch``) — the incident keeps that state visible
        across restarts until the operator acts. The incident clears when the
        breach recovers (e.g. an external deposit) or the epoch is reset.
        """
        account_id = self.broker.account_id or "unknown"
        equity = max(1e-9, account.equity)
        daily_loss = max(0.0, -account.daily_pnl / equity)
        breach: str | None = None
        if daily_loss >= self.app_config.risk.daily_loss_limit_pct:
            breach = "daily_loss_limit"
        elif account.drawdown_pct >= self.app_config.risk.max_drawdown_pct:
            breach = "portfolio_drawdown_halt"
        if breach == "portfolio_drawdown_halt":
            self.ledger.save_safety_incident(account_id, "portfolio_drawdown_halt")
        else:
            self.ledger.clear_safety_incident(account_id, "portfolio_drawdown_halt")
        previous = dict(getattr(self, "_entry_risk_latch", {}))
        new_latch: dict[Market, str | None] = dict.fromkeys(self._entry_risk_latch, breach)
        # Alert only on latch TRANSITIONS (the account loop re-evaluates this
        # every poll); a persistent breach must not re-alert per poll. Compare
        # per market so a future market-specific latch stays correct.
        transitioned = any(new_latch.get(market) != previous.get(market) for market in new_latch)
        self._entry_risk_latch = new_latch
        if transitioned:
            if breach == "daily_loss_limit":
                runtime_metrics_store.record_event("risk_incident")
                alerts.emit(
                    "risk_incident",
                    message="Daily loss limit breached; entries latched",
                    daily_loss_pct=daily_loss,
                    limit_pct=self.app_config.risk.daily_loss_limit_pct,
                    equity=equity,
                )
            elif breach == "portfolio_drawdown_halt":
                runtime_metrics_store.record_event("drawdown_halt")
                alerts.emit(
                    "drawdown_halt",
                    message="Drawdown limit breached; entries halted until risk-epoch reset",
                    drawdown_pct=account.drawdown_pct,
                    limit_pct=self.app_config.risk.max_drawdown_pct,
                    equity=equity,
                )

    def _halt_for_ownership_drift(self) -> None:
        """Fail closed on ownership drift from the account-polling loop.

        Runtime state becomes halted, the circuit breaker records
        ``ownership_drift`` and the safety state is persisted so a restart
        resumes the halt until an operator reconciles the inventory. Ownership
        drift halts orders entirely: a broken ownership model must not be
        traded against even with risk-reducing sells.

        The account loop re-enters this on every poll while drift persists:
        the state assertions re-apply each time, but the log/metric/alert fire
        only on the transition into the ownership-drift halt.
        """
        first = getattr(self, "_circuit_breaker_reason", None) != "ownership_drift"
        with getattr(self, "_state_lock", threading.RLock()):
            self._circuit_breaker_reason = "ownership_drift"
            self._state = "halted"
        # Serialize against the account loop's _evaluate_entry_risk_latch,
        # which runs under this lock inside _persist_risk_state: its
        # read-copy-rebuild-assign would otherwise drop this halt.
        with getattr(self, "_risk_state_lock", threading.Lock()):
            self._entry_risk_latch = dict.fromkeys(getattr(self, "_entry_risk_latch", {}), "ownership_drift")
        try:
            self._persist_safety_state("ownership_drift")
        finally:
            # The transition alert fires even when the persist raises: later
            # polls see the reason already set and never alert, so skipping
            # it here would leave the halt unannounced for good.
            if first:
                log.critical("Ownership drift detected; runtime halted and safety state persisted")
                runtime_metrics_store.record_event("circuit_breaker")
                alerts.emit(
                    "circuit_breaker",
                    message="Ownership drift detected; runtime halted and safety state persisted",
                    reason="ownership_drift",
                )

    def _persist_safety_state(self, reason: str) -> None:
        """Persist the halted circuit-breaker reason with the last known
        risk numbers when a fresh account snapshot is unavailable.

        The incident table is the authoritative PERSISTENT_INCIDENT store
        (restored regardless of date); the session-scoped risk_state row is
        kept for backward compatibility.
        """
        with getattr(self, "_risk_state_lock", threading.Lock()):
            account_id = self.broker.account_id or "unknown"
            today = datetime.now(UTC).date()
            self.ledger.save_safety_incident(account_id, reason)
            previous = self.ledger.load_risk_state(account_id, today)
            if previous is None:
                return
            self.ledger.save_risk_state(
                RiskState(
                    account_id=account_id,
                    session_date=today,
                    day_start_equity=previous.day_start_equity,
                    peak_equity=previous.peak_equity,
                    realized_pnl=previous.realized_pnl,
                    unrealized_pnl=previous.unrealized_pnl,
                    fees=previous.fees,
                    consecutive_losses=previous.consecutive_losses,
                    circuit_breaker_reason=reason,
                    updated_at=datetime.now(UTC),
                    # The persisted-peak restore groups risk_state rows by
                    # epoch: dropping it would re-file a post-reset peak
                    # under epoch 0.
                    peak_epoch=previous.peak_epoch,
                )
            )

    def _persist_reconciliation_incident(self, reason: str) -> None:
        """Persist a reconciliation-owned failure before returning to callers."""
        account_id = getattr(self.broker, "account_id", None) or "unknown"
        save = getattr(getattr(self, "ledger", None), "save_safety_incident", None)
        if callable(save):
            save(account_id, reason)
            return
        log.critical("Safety incident could not be persisted; ledger has no incident writer: %s", reason)

    def _raise_reconciliation_breaker(self, reason: str) -> None:
        """Set a reconciliation-raised breaker without masking a stronger one.

        A reconciliation failure may only replace no reason or another
        reconciliation-owned reason (e.g. escalate to
        ``reconciliation_bug_suspected``). Replacing ownership_drift,
        portfolio_runtime_failure, a worker crash etc. would let the next
        clean reconcile clear it via ``_clear_reconciliation_incidents`` while
        its incident row still demands operator-verified recovery.
        """
        with getattr(self, "_state_lock", threading.RLock()):
            current = getattr(self, "_circuit_breaker_reason", None)
            if current is None or current in _RECONCILIATION_INCIDENT_REASONS:
                self._circuit_breaker_reason = reason

    def _clear_reconciliation_incidents(self) -> None:
        """Clear only incidents whose documented recovery is clean reconcile."""
        account_id = getattr(self.broker, "account_id", None) or "unknown"
        clear = getattr(getattr(self, "ledger", None), "clear_safety_incident", None)
        if callable(clear):
            for reason in sorted(_RECONCILIATION_INCIDENT_REASONS):
                clear(account_id, reason)
        # Only a reconciliation-owned reason clears here; every other breaker
        # keeps its own verified recovery (reset_safety_lock).
        with getattr(self, "_state_lock", threading.RLock()):
            if getattr(self, "_circuit_breaker_reason", None) in _RECONCILIATION_INCIDENT_REASONS:
                self._circuit_breaker_reason = None
                # A trade-update blip while this reason was set left the
                # runtime "degraded" (its restore never passes a breaker);
                # nothing else would un-pin it now that the reason is gone.
                # Same running/warming_up rule as that restore, and only
                # while the trade-update worker is alive.
                trade_updates = dict(getattr(self, "_workers", {})).get("pb-trade-updates")
                if (
                    getattr(self, "_state", None) == "degraded"
                    and trade_updates is not None
                    and trade_updates.alive
                ):
                    self._state = "running" if self._has_ready_symbols() else "warming_up"

    def _restore_persistent_state(self) -> None:
        """Restore persisted runtime state at startup.

        Persisted state is classified by lifetime semantics:

        * SESSION_SCOPED — daily-loss/drawdown baselines (``consecutive_losses``
          and the risk numbers) live in the per-session ``risk_state`` row and
          are restored from TODAY's row only: a new session day deliberately
          starts with a clean baseline.
        * PERSISTENT_INCIDENT — circuit-breaker reasons live in the
          ``safety_incidents`` table and are restored REGARDLESS of the
          session date, so a halt persisted at 23:59 survives a 00:01 restart
          until an operator verifies the recovery.
        * TRANSIENT_READINESS — the entry-risk latch and market entry halts are
          reconstructed from authoritative state at startup and never
          persisted.
        * WATERMARK — decision watermarks (``previous_timestamp`` per market
          and ``portfolio_watermark``) are restored from the latest persisted
          decision so a restart never replays a duplicate decision or loses
          the cadence position.
        """
        account_id = self.broker.account_id or "unknown"
        # Reasons in this set are exactly the ones ``reset_safety_lock``
        # accepts as recoverable: a restored incident must never be a halt
        # the operator cannot clear, so the two sets are kept in step and a
        # reason added here must be added there in the same change.
        # Deliberately NOT in the set, and therefore neither restored nor
        # operator-resettable: the shared-worker f"{name}_restart_limit"
        # halts (pb-decisions / pb-account / pb-trade-updates). The row is
        # still written by ``_persist_risk_state`` (it records whatever
        # reason is in effect) and stays as an audit trail. A restart
        # re-arms those workers (restart_count starts at 0 again) and a
        # repeat failure re-raises the halt by itself, so restoring the old
        # row would halt a process whose workers are healthy. The market
        # entry halts of the same name (pb-stocks / pb-crypto) are a
        # different mechanism with their own verified recovery; see
        # ``reset_safety_lock``.
        persistent_reasons = {
            "ambiguous_order_state",
            "protection_failure",
            "protection_mismatch",
            "ledger_mismatch",
            "post_fill_risk_violation",
            "final_market_event_queue_overflow",
            "ownership_drift",
            "reconciliation_failed",
            "reconciliation_bug_suspected",
            "portfolio_runtime_failure",
            "decision_worker_crashed",
            "trade_update_callback_failure",
        }
        incidents = self.ledger.load_safety_incidents(account_id)
        # Newest first, but a reconciliation-owned reason never masks another
        # persisted halt: a clean reconcile would clear it and resume trading.
        restorable = [incident.reason for incident in incidents if incident.reason in persistent_reasons]
        preferred = [reason for reason in restorable if reason not in _RECONCILIATION_INCIDENT_REASONS]
        if preferred or restorable:
            self._circuit_breaker_reason = (preferred or restorable)[0]
        if any(incident.reason == "portfolio_drawdown_halt" for incident in incidents):
            # A drawdown breach is an operational incident with an explicit
            # recovery (verified risk-epoch reset). Restore the entry latch
            # from the persisted incident so the halt is visible immediately
            # after restart; the first account poll re-evaluates it against
            # the broker state (a recovered breach clears it).
            self._entry_risk_latch = dict.fromkeys(self._entry_risk_latch, "portfolio_drawdown_halt")
            runtime_metrics_store.record_event("drawdown_halt")
            alerts.emit(
                "drawdown_halt",
                message="Persisted drawdown halt restored at startup; entries blocked",
                account_id=account_id,
                reason="portfolio_drawdown_halt",
            )
        previous = self.ledger.load_risk_state(account_id, datetime.now(UTC).date())
        if previous is not None:
            self._consecutive_losses = previous.consecutive_losses
        # --- Watermark restoration (restart scenarios A-G) ---
        # PortfolioRuntime already restores ``previous_timestamp`` from the
        # repository's latest_decision_timestamp in its __init__. Here we
        # mirror that into the engine-level watermark maps so
        # ``_process_portfolio_timestamp`` and the bar coalescer start with
        # the persisted high-water instead of ``datetime.min`` and never
        # re-emit a duplicate decision after a restart (before/after decision,
        # after order submission, after partial/complete fill, while
        # verification or universe refresh was running).
        try:
            for rt in getattr(self, "_portfolio_runtimes", ()):
                ts = self._portfolio_repository.latest_decision_timestamp(
                    account_id, rt.configuration_digest, rt.market
                )
                if ts is not None:
                    self._portfolio_watermarks[rt.market] = max(
                        ts, self._portfolio_watermarks.get(rt.market, datetime.min.replace(tzinfo=UTC))
                    )
                    for sym in getattr(rt, "symbols", ()):
                        key = (sym, rt.strategy.timeframe)
                        self._series_watermarks[key] = max(
                            ts, self._series_watermarks.get(key, datetime.min.replace(tzinfo=UTC))
                        )
        except Exception:
            # Watermark restore is best-effort; a repository hiccup must not
            # brick startup — the duplicate-decision guard in
            # PortfolioRuntime still prevents double orders.
            log.warning("Watermark restoration skipped after repository hiccup", exc_info=True)

    async def _on_trade_update(self, update: TradeUpdate) -> None:
        try:
            await self._handle_trade_update(update)
        except Exception as exc:
            # The TradingStream callback is the primary source of truth for
            # the ledger. An unhandled exception here can kill the stream
            # worker (or run unobserved inside the SDK loop), degrading the
            # whole runtime. Escalate to a halt instead of failing silently.
            with getattr(self, "_state_lock", threading.RLock()):
                self._circuit_breaker_reason = "trade_update_callback_failure"
                self._state = "halted"
            log.exception(
                "Trade update handling failed for order %s: %s",
                update.client_order_id,
                exc,
            )
            runtime_metrics_store.record_event("circuit_breaker")
            alerts.emit(
                "circuit_breaker",
                message="Trade update handling failed; runtime halted",
                reason="trade_update_callback_failure",
                client_order_id=update.client_order_id,
                error=str(exc),
            )

    def _advance_portfolio_rebalance(
        self,
        market: Market,
        *,
        account: AccountSnapshot | None = None,
        reserved_position_keys: tuple[tuple[Market, str], ...] = (),
    ) -> Any:
        """Advance the persisted two-phase rebalance for one market.

        Called from trade-update callbacks, the bar decision loop and engine
        startup. A blocked rebalance (ambiguous SELL outcome) halts market
        entries until a verified reconciliation clears it.
        """
        for portfolio_runtime in getattr(self, "_portfolio_runtimes", ()):
            if portfolio_runtime.market != market:
                continue
            try:
                with self._portfolio_lock:
                    advance_kwargs: dict[str, object] = {}
                    if account is not None:
                        advance_kwargs["account"] = account
                    if reserved_position_keys:
                        advance_kwargs["reserved_position_keys"] = reserved_position_keys
                    result = portfolio_runtime.advance_rebalance(**advance_kwargs)
            except Exception:
                # The bar-path twin of this handler (in _process_timestamp)
                # halts with a circuit breaker; here the persisted two-phase
                # state stays pending and every later trade update retries —
                # so the traceback is the only forensic record of a persistent
                # failure. Keep it, unlike the old single-line log.error.
                log.exception(
                    "Two-phase rebalance advance failed: market=%s",
                    market.value,
                )
                continue
            if result is not None and result.status == "rebalance_blocked":
                with getattr(self, "_state_lock", threading.RLock()):
                    self._market_entry_halts[market] = "rebalance_reconciliation_required"
                log.critical(
                    "Two-phase rebalance outcome is ambiguous; %s entries halted pending reconciliation (decision=%s)",
                    market.value,
                    result.decision_id,
                )
                runtime_metrics_store.record_event("ambiguous_order")
                alerts.emit(
                    "ambiguous_order",
                    message=("Two-phase rebalance outcome ambiguous; entries halted pending reconciliation"),
                    market=market.value,
                    decision_id=result.decision_id,
                )
            return result
        return None

    def _resume_startup_rebalances(self, *, account: AccountSnapshot) -> None:
        """Resume pending rebalances crypto-first against one account snapshot."""
        reserved_position_keys: set[tuple[Market, str]] = set()
        for portfolio_runtime in self._crypto_first_portfolio_order(getattr(self, "_portfolio_runtimes", ())):
            if self._circuit_breaker_reason is not None:
                # A restored persistent incident keeps every pending rebalance
                # deferred until the operator verifies recovery.
                log.warning(
                    "Restart rebalance resume deferred: circuit breaker active (%s)",
                    self._circuit_breaker_reason,
                )
                break
            ordered_reservations = tuple(
                sorted(
                    reserved_position_keys,
                    key=_reservation_sort_key,
                )
            )
            result = self._advance_portfolio_rebalance(
                portfolio_runtime.market,
                account=account,
                reserved_position_keys=ordered_reservations,
            )
            reserved_position_keys.update(self._ordered_buy_position_keys(portfolio_runtime, result))

    async def _handle_trade_update(self, update: TradeUpdate) -> None:
        portfolio_repository = getattr(self, "_portfolio_repository", None)
        if portfolio_repository is not None and portfolio_repository.has_order(update.client_order_id):
            # Book the fill BEFORE the account snapshot: a SELL fill that
            # brings the broker position below the repository-owned quantity
            # would otherwise trip the fail-closed ownership-drift check
            # against the pre-fill owned quantity, aborting the handler and
            # losing the fill.
            await asyncio.to_thread(
                portfolio_repository.apply_trade_update,
                client_order_id=update.client_order_id,
                broker_order_id=update.broker_order_id,
                status=update.event,
                cumulative_filled_quantity=update.filled_qty,
                average_fill_price=update.average_fill_price,
                occurred_at=update.timestamp,
                activity_id=(
                    update.activity_id or f"{update.client_order_id}:{update.event}:{update.timestamp.isoformat()}"
                ),
                fee=update.fee,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                source=update.source,
                received_at=update.received_at,
                fee_observed=update.fee_observed,
            )
            # A rejected portfolio order is an operator-visible event: the
            # dependent rebalance replans smaller (or absent) buys, so the
            # rejection is not silent.
            if update.event == "rejected":
                runtime_metrics_store.record_event("order_rejected")
                alerts.emit(
                    "order_rejected",
                    message=f"Portfolio order rejected: {update.client_order_id}",
                    client_order_id=update.client_order_id,
                    symbol=update.symbol,
                    market=Market.CRYPTO.value if is_crypto(update.symbol) else Market.EQUITY.value,
                )
            # Reuse the account-loop snapshot when it is fresh (one poll
            # interval) instead of issuing a full get_account +
            # get_all_positions round-trip per fill. The fill was already
            # booked into the repository above, and the 15s account loop is
            # the authoritative REST re-verification of ownership drift; the
            # reused snapshot only feeds risk-state persistence (equity/peak),
            # which the loop re-persists from its next fresh fetch. When the
            # loop has NOT fetched within the poll interval (e.g. a broker
            # hiccup), fetch fresh exactly like before.
            now = datetime.now(UTC)
            freshness = getattr(getattr(self, "config", None), "account_poll_interval_seconds", 15.0)
            snapshot_provider = getattr(self.broker, "snapshot_if_fresh", None)
            cached = None
            _, _, fetch_generation = self._account_snapshot_cache_state()
            with self._account_snapshot_guard():
                last_account_at = getattr(self, "_last_account_at", None)
            if (
                snapshot_provider is not None
                and last_account_at is not None
                and (now - last_account_at).total_seconds() <= freshness
            ):
                cached = snapshot_provider(freshness)
                if cached is not None:
                    with self._account_snapshot_guard():
                        if int(getattr(self, "_account_snapshot_generation", 0)) != fetch_generation:
                            cached = None
            if cached is not None:
                account = cached
            else:
                # If the cache read raced with an invalidation, use the latest
                # generation as the publication fence for this broker fetch.
                _, _, fetch_generation = self._account_snapshot_cache_state()
                account = await asyncio.to_thread(self.broker.account)
                # The fill-path fetch is the freshest snapshot in the system;
                # share it with the decision-path cache.
                now = datetime.now(UTC)
                if not self._publish_account_snapshot(
                    account,
                    expected_generation=fetch_generation,
                    timestamp=now,
                ):
                    raise RuntimeError("Account snapshot invalidated during trade-update fetch")
            event = str(update.event).lower()
            if event == "partial_fill" or is_terminal_order_status(event):
                # A terminal order must not release its ledger slot while this
                # cache still proves only the pre-event position state. This
                # also covers a terminal SELL cancellation/rejection: the next
                # decision must reverify the still-held position before
                # reusing its account slot.
                self._invalidate_account_snapshot_after_execution()
            self._persist_risk_state(account)
            # A terminal SELL fill may unlock the dependent BUY phase of a
            # two-phase rebalance: advance it now, event-driven, instead of
            # waiting for the next bar.
            market = Market.CRYPTO if is_crypto(update.symbol) else Market.EQUITY
            await asyncio.to_thread(self._advance_portfolio_rebalance, market)
            return
        # Never adopt orders from the retired signal engine. Broker/local
        # reconciliation reports them as unmanaged drift.
        log.warning(
            "Trade update for unmanaged order %s ignored; reconciliation scheduled",
            update.client_order_id,
        )
        self._schedule_reconcile()

    def _run_stream_worker(self, name: str, target: Callable[[], None]) -> None:
        worker = self._workers[name]
        worker.alive = True
        worker.last_heartbeat = datetime.now(UTC)
        try:
            while self._running:
                run_started = time.monotonic()
                try:
                    target()
                    if not self._running:
                        return
                    raise RuntimeError("stream worker returned unexpectedly")
                except Exception as exc:
                    worker.last_error = str(exc)
                    market = _MARKET_BY_STREAM_WORKER.get(name)
                    # Market workers reset on a verified live event instead
                    # (_mark_market_stream_recovered).
                    if market is None and time.monotonic() - run_started >= _SHARED_WORKER_HEALTHY_RESET_SECONDS:
                        worker.restart_count = 0
                    worker.restart_count += 1
                    if market is None:
                        with getattr(self, "_state_lock", threading.RLock()):
                            if self._state not in _ENTRY_BLOCKING_STATES:
                                self._state = "degraded"
                    if worker.restart_count > 5:
                        if market is None:
                            with getattr(self, "_state_lock", threading.RLock()):
                                self._circuit_breaker_reason = f"{name}_restart_limit"
                                self._state = "halted"
                            log.exception("%s shared worker restart limit reached", name)
                            runtime_metrics_store.record_event("circuit_breaker")
                            alerts.emit(
                                "circuit_breaker",
                                message=(f"{name} shared worker restart limit reached; runtime halted"),
                                reason=self._circuit_breaker_reason,
                            )
                            return
                        with getattr(self, "_state_lock", threading.RLock()):
                            self._market_entry_halts[market] = f"{name}_restart_limit"
                        if worker.restart_count == 6:
                            # Edge-triggered: the alert/metric fire once at the
                            # threshold crossing; retries past it only re-assert
                            # the halt above until a verified reconnect clears it.
                            log.exception("%s stream worker restart limit reached", name)
                            runtime_metrics_store.record_event("circuit_breaker")
                            alerts.emit(
                                "circuit_breaker",
                                message=(f"{name} stream worker restart limit reached; entries halted"),
                                reason=f"{name}_restart_limit",
                                market=market.value,
                            )
                    delay = min(16.0, 2 ** (worker.restart_count - 1))
                    log.warning(
                        "%s stream worker failed; retrying in %.1fs: %s",
                        name,
                        delay,
                        exc,
                    )
                    # Interruptible backoff: stop() joins stream workers with
                    # a 5 s timeout, shorter than the 16 s max delay.
                    if self._stop_event.wait(delay):
                        return
                    worker.last_heartbeat = datetime.now(UTC)
                    # A shared-worker blip marks the runtime "degraded" above;
                    # un-pin it before retrying so one transient failure does
                    # not hold /readyz at 503 until a process restart. Only a
                    # breaker-free degraded state is restored (never a halt),
                    # to the same running/warming_up choice start() makes.
                    with getattr(self, "_state_lock", threading.RLock()):
                        if self._state == "degraded" and getattr(self, "_circuit_breaker_reason", None) is None:
                            self._state = "running" if self._has_ready_symbols() else "warming_up"
        finally:
            worker.alive = False

    def _mark_market_stream_recovered(self, market: Market) -> None:
        """Clear a market-only failure after a real event proves reconnect health."""
        worker_name = _STREAM_WORKER_BY_MARKET[market]
        worker = self._workers.get(worker_name)
        if worker is None or worker.last_error is None:
            return
        previous_error = worker.last_error
        previous_restarts = worker.restart_count
        worker.last_error = None
        worker.restart_count = 0
        worker.last_event_at = datetime.now(UTC)
        with getattr(self, "_state_lock", threading.RLock()):
            cleared = self._market_entry_halts.get(market) == f"{worker_name}_restart_limit"
            if cleared:
                self._market_entry_halts[market] = None
        if cleared:
            log.warning(
                "Market halt cleared after verified reconnect: market=%s "
                "worker_restart_count=%d recovery_action=market_halt_cleared",
                market.value,
                previous_restarts,
            )
        log.info(
            "Market worker recovered: market=%s worker_restart_count=%d "
            "recovery_action=stream_event_verified previous_error=%s",
            market.value,
            previous_restarts,
            previous_error,
        )

    async def _on_minute_bar(self, bar: Bar) -> None:
        await self._on_bar(bar, source="one_minute_bar", corrected=False)

    async def _on_updated_minute_bar(self, bar: Bar) -> None:
        await self._on_bar(bar, source="updated_minute_bar", corrected=True)

    async def _on_bar(self, bar: Bar, *, source: str, corrected: bool) -> None:
        """Ingest a live or corrected bar into the aggregation pipeline."""
        if bar.market.value == "equity" and not is_regular_equity_time(bar.timestamp):
            return
        worker_name = _STREAM_WORKER_BY_MARKET[bar.market]
        if worker := self._workers.get(worker_name):
            worker.last_heartbeat = datetime.now(UTC)
            worker.last_event_at = datetime.now(UTC)
        if not corrected:
            # Only a live bar proves the stream recovered; a corrected bar
            # does not clear a market halt on its own.
            self._mark_market_stream_recovered(bar.market)
        if bar.market == Market.CRYPTO:
            await self._process_crypto_protection(bar, source=source)
        with self._ingestion_lock:
            finalized = {
                timeframe: (aggregator.update_correction(bar) if corrected else aggregator.update(bar))
                for timeframe, aggregator in self.aggregators.items()
            }
        for timeframe, closed in finalized.items():
            if closed is None:
                continue
            if corrected:
                self._put_event("correction", closed)
                continue
            self._emit_finalized_bar(timeframe, closed)

    def _emit_finalized_bar(self, timeframe: str, bar: Bar) -> None:
        """Queue one finalized bar for every cadence it feeds."""
        if timeframe == self.app_config.runtime.execution_timeframe:
            self._put_event("primary", bar)
        elif timeframe == self.app_config.runtime.market_state_timeframe:
            self._put_event("higher", bar)
        if timeframe in self._strategy_timeframes:
            self._put_event("portfolio", bar)

    async def _process_crypto_protection(self, bar: Bar, source: str) -> None:
        """Feed a crypto price observation to the protective-stop watcher.

        Called for both live bars and corrected/updated bars so a corrected
        price never leaves the watcher on a stale quote.
        """
        try:
            await asyncio.to_thread(
                self.broker.process_crypto_price,
                CryptoPriceObservation(
                    symbol=bar.symbol,
                    high=bar.high,
                    low=bar.low,
                    source=source,
                    timestamp=bar.timestamp,
                ),
            )
            # A verified protection pass proves the watcher is healthy
            # again: clear a TRANSIENT halt so a one-off API blip does
            # not permanently brick crypto entries. Ambiguity is never
            # cleared here (reconciliation decides that). The check-then-
            # clear runs under the state lock because live and corrected
            # price passes can run concurrently and share this dict with
            # every other halt writer.
            with getattr(self, "_state_lock", threading.RLock()):
                recovered = self._market_entry_halts.get(Market.CRYPTO) == "crypto_protection_failure"
                if recovered:
                    self._market_entry_halts[Market.CRYPTO] = None
            if recovered:
                log.warning("Crypto protection watcher recovered; crypto entries unhalted")
        except AmbiguousOrderState as exc:
            with getattr(self, "_state_lock", threading.RLock()):
                self._market_entry_halts[Market.CRYPTO] = "ambiguous_crypto_exit"
            log.critical("Crypto exit state is ambiguous; crypto entries halted: %s", exc)
            runtime_metrics_store.record_event("ambiguous_order")
            alerts.emit(
                "ambiguous_order",
                message="Crypto exit state ambiguous; crypto entries halted",
                market="crypto",
                error=str(exc),
            )
        except Exception as exc:
            with getattr(self, "_state_lock", threading.RLock()):
                self._market_entry_halts[Market.CRYPTO] = "crypto_protection_failure"
            log.exception(
                "Crypto protection watcher failed (entries halted until the next verified price pass): %s",
                exc,
            )

    def _put_event(self, kind: str, bar: Bar) -> None:
        event = _Event(kind, bar, datetime.now(UTC))
        result = self._events.put_market_event(event)
        # _put_event runs from several threads (stock stream, crypto stream,
        # decision loop); the diagnostics counters must not be mutated
        # unsynchronized.
        overflowed = False
        with getattr(self, "_event_stats_lock", threading.Lock()):
            if result == "coalesced":
                self._coalesced_events += 1
            elif result == "replaced":
                self._dropped_events += 1
            elif result == "overflow":
                overflowed = True
                self._dropped_events += 1
            if self._oldest_received_at is None:
                self._oldest_received_at = event.received_at
        if overflowed:
            # The halt takes the state lock only after the stats lock is
            # released: no stats -> state nesting from the stream threads.
            with getattr(self, "_state_lock", threading.RLock()):
                self._circuit_breaker_reason = "final_market_event_queue_overflow"
                self._state = "halted"
            runtime_metrics_store.record_event("circuit_breaker")
            alerts.emit(
                "circuit_breaker",
                message="Final market event queue overflow; runtime halted",
                reason="final_market_event_queue_overflow",
            )

    def _decision_loop(self) -> None:
        worker = self._workers["pb-decisions"]
        worker.alive = True
        next_reconcile = time.monotonic() + 60.0
        pending: dict[datetime, dict[str, Bar]] = {}
        first_seen: dict[datetime, float] = {}
        portfolio_pending: dict[tuple[str, datetime], dict[str, Bar]] = {}
        portfolio_first_seen: dict[tuple[str, datetime], float] = {}
        # Symbols already evaluated per closed batch. A late bar whose symbol
        # was missing from the closed batch must be re-evaluated instead of
        # being silently ingested as a correction (which never reaches the
        # decision pipeline).
        processed_batches: dict[datetime, frozenset[str]] = {}
        try:
            while self._running:
                worker.last_heartbeat = datetime.now(UTC)
                try:
                    item = self._events.get(timeout=min(0.5, max(0.0, next_reconcile - time.monotonic())))
                except queue.Empty:
                    item = None
                if item is None:
                    if not self._running:
                        break
                else:
                    worker.last_event_at = item.received_at
                    if item.kind == "portfolio":
                        portfolio_key = (item.bar.timeframe, item.bar.timestamp)
                        portfolio_pending.setdefault(portfolio_key, {})[item.bar.symbol] = item.bar
                        portfolio_first_seen.setdefault(portfolio_key, time.monotonic())
                    elif item.kind == "higher":
                        # Every market_state writer shares _ingestion_lock so
                        # a concurrent hot-swap commit (which seeds under the
                        # same lock) can never interleave with a BarStore
                        # read-modify-write and lose a bar.
                        with self._ingestion_lock:
                            self.market_state.seed([item.bar])
                    elif item.kind == "correction":
                        with self._ingestion_lock:
                            self.market_state.ingest_corrections([item.bar])
                    elif item.bar.timestamp <= max(
                        self._series_watermarks.get(
                            (item.bar.symbol, item.bar.timeframe),
                            datetime.min.replace(tzinfo=UTC),
                        ),
                        self._portfolio_watermarks.get(item.bar.market, datetime.min.replace(tzinfo=UTC)),
                    ):
                        batch = processed_batches.get(item.bar.timestamp)
                        if batch is not None and item.bar.symbol not in batch:
                            processed_batches[item.bar.timestamp] = batch | {item.bar.symbol}
                            expected: set[str] = getattr(self, "_expected_all_symbols", set())
                            self._process_timestamp(
                                {item.bar.symbol: item.bar},
                                expected - (batch | {item.bar.symbol}),
                            )
                            log.info(
                                "Late finalized bar re-evaluated: symbol=%s timeframe=%s timestamp=%s",
                                item.bar.symbol,
                                item.bar.timeframe,
                                item.bar.timestamp,
                            )
                        else:
                            with self._ingestion_lock:
                                self.market_state.ingest_corrections([item.bar])
                            log.debug(
                                "Late finalized bar ingested as correction: symbol=%s timeframe=%s timestamp=%s",
                                item.bar.symbol,
                                item.bar.timeframe,
                                item.bar.timestamp,
                            )
                    else:
                        key = item.bar.timestamp
                        pending.setdefault(key, {})[item.bar.symbol] = item.bar
                        first_seen.setdefault(key, time.monotonic())
                self._flush_aggregators()
                now_mono = time.monotonic()
                # Symbol membership only changes on start/pin; the union is
                # refreshed by _pin_managed_symbols instead of rebuilt on
                # every loop iteration.
                expected_all: set[str] = getattr(self, "_expected_all_symbols", set())
                closable = [
                    timestamp
                    for timestamp, bars in pending.items()
                    if expected_all.issubset(bars)
                    or now_mono - first_seen[timestamp] >= self.config.event_grace_seconds
                ]
                # RuntimeHealth is expensive to build; build it ONCE per loop
                # iteration (lazily, only when a batch is actually processed)
                # and reuse it for every readiness gate in this batch.
                # Unit-test fixtures that stub _entry_readiness_reasons skip
                # the build entirely.
                gate = self._entry_readiness_reasons
                # Avoid referencing the global EngineRuntime class name so a
                # test that monkeypatches the global to FakeRuntime does not
                # crash a running PAPER engine's decision worker (P0).
                real_gate = getattr(gate, "__func__", None) is getattr(type(self), "_entry_readiness_reasons", None)
                batch_health: RuntimeHealth | None = None
                for timestamp in sorted(closable):
                    bars_by_symbol = pending.pop(timestamp)
                    first_seen.pop(timestamp, None)
                    if batch_health is None and real_gate:
                        batch_health = self.health()
                    self._process_timestamp(bars_by_symbol, expected_all, health=batch_health)
                    processed_batches[timestamp] = frozenset(bars_by_symbol)
                    for bar in bars_by_symbol.values():
                        self._series_watermarks[(bar.symbol, bar.timeframe)] = max(
                            timestamp,
                            self._series_watermarks.get(
                                (bar.symbol, bar.timeframe),
                                datetime.min.replace(tzinfo=UTC),
                            ),
                        )
                    for candidate_market in {bar.market for bar in bars_by_symbol.values()}:
                        self._portfolio_watermarks[candidate_market] = max(
                            timestamp,
                            self._portfolio_watermarks.get(
                                candidate_market,
                                datetime.min.replace(tzinfo=UTC),
                            ),
                        )
                portfolio_closable = []
                for portfolio_key, candidate_bars in portfolio_pending.items():
                    timeframe, _timestamp = portfolio_key
                    expected_portfolio = {
                        symbol
                        for runtime in self._portfolio_runtimes
                        if runtime.strategy.timeframe == timeframe
                        for symbol in runtime.symbols
                    }
                    if expected_portfolio.issubset(candidate_bars) or (
                        now_mono - portfolio_first_seen[portfolio_key] >= self.config.event_grace_seconds
                    ):
                        portfolio_closable.append(portfolio_key)
                for portfolio_key in sorted(portfolio_closable, key=lambda item: (item[1], item[0])):
                    candidate_bars = portfolio_pending.pop(portfolio_key)
                    portfolio_first_seen.pop(portfolio_key, None)
                    if batch_health is None and real_gate:
                        batch_health = self.health()
                    self._process_portfolio_timestamp(list(candidate_bars.values()), health=batch_health)
                if len(processed_batches) > 256:
                    cutoff = max(processed_batches) - timedelta(hours=6)
                    for stale in [ts for ts in processed_batches if ts < cutoff]:
                        del processed_batches[stale]
                # Recompute the queue-age diagnostic from both sources under
                # the stats lock so an event enqueued concurrently cannot be
                # missed (which would report a zero age while events wait).
                with self._event_stats_lock:
                    pending_oldest = min(
                        (
                            datetime.now(UTC) - timedelta(seconds=max(0.0, now_mono - seen))
                            for seen in first_seen.values()
                        ),
                        default=None,
                    )
                    portfolio_oldest = min(
                        (
                            datetime.now(UTC) - timedelta(seconds=max(0.0, now_mono - seen))
                            for seen in portfolio_first_seen.values()
                        ),
                        default=None,
                    )
                    self._oldest_received_at = min(
                        (
                            candidate
                            for candidate in (
                                self._events.oldest_received_at(),
                                pending_oldest,
                                portfolio_oldest,
                            )
                            if candidate is not None
                        ),
                        default=None,
                    )
                if time.monotonic() >= next_reconcile:
                    self._schedule_reconcile()
                    next_reconcile = time.monotonic() + 60.0
        except Exception as exc:
            worker.last_error = str(exc)
            # Every with-block of the loop body has exited here: no lock held.
            with getattr(self, "_state_lock", threading.RLock()):
                self._circuit_breaker_reason = (
                    "ownership_drift" if isinstance(exc, OwnershipDriftError) else "decision_worker_crashed"
                )
                self._state = "halted"
            log.exception("Decision worker failed")
            runtime_metrics_store.record_event("circuit_breaker")
            alerts.emit(
                "circuit_breaker",
                message="Decision worker failed; runtime halted",
                reason=self._circuit_breaker_reason,
                error=str(exc),
            )
        finally:
            worker.alive = False

    def _flush_aggregators(self) -> None:
        now = datetime.now(UTC)
        # Same finalization grace as flush_until: the 15:59 minute can arrive
        # just after 16:00:00 and must still land in the session-close candles.
        closing = now - timedelta(seconds=self.config.data_finalization_grace_seconds)
        local = closing.astimezone(NEW_YORK)
        with self._ingestion_lock:
            finalized = {timeframe: aggregator.flush_until(now) for timeframe, aggregator in self.aggregators.items()}
            if local.weekday() < 5 and (local.hour, local.minute) >= (16, 0):
                for timeframe, aggregator in self.aggregators.items():
                    finalized[timeframe].extend(aggregator.flush_session_close(closing))
        for timeframe, bars in finalized.items():
            for bar in bars:
                self._emit_finalized_bar(timeframe, bar)

    def _process_timestamp(
        self,
        bars_by_symbol: dict[str, Bar],
        expected: set[str],
        *,
        health: RuntimeHealth | None = None,
    ) -> None:
        bars = list(bars_by_symbol.values())
        # Same shared-writer discipline as the "higher"/"correction" branches
        # above: the hot-swap commit seeds under _ingestion_lock.
        with self._ingestion_lock:
            ingestion = self.market_state.ingest_closed_bars(bars)
        gap_bars = [
            bar
            for bar, mutation in zip(ingestion.bars, ingestion.mutations, strict=True)
            if mutation.value == "gap_detected"
        ]
        if gap_bars:
            # One data-gap episode per finalized batch with detected gaps
            # (runtime_metrics() reads this counter).
            runtime_metrics_store.record_event("data_gap")
        for bar in gap_bars:
            self._repair_gap(bar)
        if self._warming_up:
            return
        self._refresh_readiness()
        gate = self._entry_readiness_reasons
        if health is None and (
            getattr(gate, "__func__", None) is getattr(type(self), "_entry_readiness_reasons", None)
        ):
            health = self.health()
        gate_kwargs = {"health": health} if health is not None else {}
        market_reasons: dict[Market, tuple[str, ...]] = {}
        for candidate_market in {bar.market for bar in bars_by_symbol.values()}:
            try:
                market_reasons[candidate_market] = gate(candidate_market, **gate_kwargs)
            except TypeError:
                market_reasons[candidate_market] = gate(candidate_market)

        if not any(
            symbol not in self._not_ready_symbols
            and not self._engine_has_symbol_gap(symbol, bar.market)
            and not market_reasons[bar.market]
            for symbol, bar in bars_by_symbol.items()
        ):
            return
        missing = tuple(sorted(expected - set(bars_by_symbol)))
        if missing:
            log.warning(
                "Ranking universe is partial; missing symbols: %s",
                ", ".join(missing),
            )
        # This cadence updates bar and readiness state only. PortfolioRuntime
        # is the sole strategy/risk/order decision path.

    @staticmethod
    def _crypto_first_portfolio_order(portfolio_runtimes: Sequence[Any]) -> tuple[Any, ...]:
        """Order simultaneous portfolio decisions by the explicit slot policy."""
        return tuple(
            sorted(
                portfolio_runtimes,
                key=lambda runtime: 0 if runtime.market == Market.CRYPTO else 1,
            )
        )

    @staticmethod
    def _ordered_buy_position_keys(portfolio_runtime: Any, result: Any) -> tuple[tuple[Market, str], ...]:
        """Reserve only positive slots represented by an actual planned BUY."""
        keys: set[tuple[Market, str]] = set()
        for order in getattr(result, "orders", ()) or ():
            side = getattr(order, "side", None)
            side_value = getattr(side, "value", side)
            if str(side_value).lower() != PortfolioOrderSide.BUY.value:
                continue
            order_market = getattr(order, "market", portfolio_runtime.market)
            if not isinstance(order_market, Market):
                order_market = Market(str(order_market))
            keys.add((order_market, canonical_symbol(order.symbol, order_market)))
        return tuple(sorted(keys, key=lambda key: (key[0].value, key[1])))

    def _process_portfolio_timestamp(self, bars: list[Bar], *, health: RuntimeHealth | None = None) -> None:
        if self._warming_up or self._circuit_breaker_reason is not None or getattr(self, "_reconcile_failed", False):
            return
        # Fail-closed: a pending reconciliation (material drift detected) blocks
        # new portfolio orders until a verified ``reconcile()`` clears it.
        # This is the engine-side complement to the broker's 7-category
        # ``reconcile()`` — holdings==broker is enforced before any new
        # decision can emit orders (restart scenarios A-G).
        # One immutable account snapshot per processing cycle, shared by every
        # market's decision in this batch (single broker fetch instead of one
        # per market). Fetched lazily on the first actual decision; freshness
        # is enforced against maximum_account_age_for_entry_seconds.
        account: AccountSnapshot | None = None
        account_lease: _AccountSnapshotLease | None = None
        reserved_position_keys: set[tuple[Market, str]] = set()
        for portfolio_runtime in self._crypto_first_portfolio_order(getattr(self, "_portfolio_runtimes", ())):
            relevant = [
                bar
                for bar in bars
                if bar.market == portfolio_runtime.market
                and bar.timeframe == portfolio_runtime.strategy.timeframe
                and bar.symbol in portfolio_runtime.symbols
            ]
            if not relevant:
                continue
            # A stale broker account snapshot pauses the whole decision
            # pipeline for this market: position quantity, cash and exposure
            # are no longer trusted enough to size ANY new rebalance order —
            # not even risk-reducing target sells. Broker-native protective
            # exits (resting stops) continue independently at the broker.
            gate = self._entry_readiness_reasons
            if health is None and (
                getattr(gate, "__func__", None) is getattr(type(self), "_entry_readiness_reasons", None)
            ):
                health = self.health()
            gate_kwargs = {"health": health} if health is not None else {}
            # Robust dispatch: overridden stubs may not accept health kwarg
            try:
                readiness_reasons = gate(portfolio_runtime.market, **gate_kwargs)
            except TypeError:
                readiness_reasons = gate(portfolio_runtime.market)
            if readiness_reasons:
                if portfolio_runtime.market == Market.EQUITY:
                    log.info(
                        "Equity PAPER decision skipped: strategy=%s reason=%s",
                        portfolio_runtime.strategy.name,
                        ",".join(readiness_reasons),
                    )
                # Visibility: readiness gate blocked decision — emit no-trade
                try:
                    from datetime import datetime as _dt

                    from parayabuck.monitoring.journal import emit_journal
                    from parayabuck.monitoring.operational_event import create_event

                    # Build single sentence message
                    market_name = (
                        portfolio_runtime.market.value
                        if hasattr(portfolio_runtime.market, "value")
                        else str(portfolio_runtime.market)
                    )
                    first_reason = readiness_reasons[0] if readiness_reasons else "readiness_blocked"
                    msg_map = {
                        "account_snapshot_stale": "hesap görüntüsü eski",
                        "event_queue_stale": "olay kuyruğu eski",
                        "runtime_state:halted": "motor durduruldu",
                    }
                    tr = msg_map.get(first_reason, first_reason)
                    market_tr = "Kripto" if market_name.lower() == "crypto" else "Hisse"
                    msg = f"{market_tr} alınmadı: {tr}."
                    day = _dt.now(UTC).date().isoformat()
                    evt = create_event(
                        kind="portfolio_no_trade",
                        message=msg,
                        market=market_name,
                        strategy=portfolio_runtime.strategy.name,
                        session_id=getattr(self, "_runtime_session_id", None),
                        account_id=getattr(self.broker, "account_id", None) if hasattr(self, "broker") else None,
                        deterministic_extra=f"{market_name}:{portfolio_runtime.strategy.name}:{first_reason}:{day}",
                        fields={
                            "reason": first_reason,
                            "readiness_reasons": list(readiness_reasons),
                            "relevant_bar_count": len(relevant),
                            "decision_timestamp": max(bar.timestamp for bar in relevant).isoformat()
                            if relevant
                            else None,
                        },
                    )
                    emit_journal(evt)
                except Exception:
                    # Best-effort, but never silent: this is the operator's
                    # durable "why did nothing trade" record.
                    log.warning(
                        "No-trade journal emit failed for market=%s",
                        getattr(portfolio_runtime.market, "value", portfolio_runtime.market),
                        exc_info=True,
                    )
                continue
            # basis) must not freeze the whole decision: risk-reducing SELL
            # exits still run, only entries are blocked. The equity signal
            # basis is verified synchronously here (batched, at most hourly)
            # so a finalized daily decision NEVER executes on an
            # unverified corporate-action basis.
            entry_latch = getattr(self, "_entry_risk_latch", {}).get(portfolio_runtime.market)
            if portfolio_runtime.market == Market.EQUITY:
                decision_timestamp = max(bar.timestamp for bar in relevant)
                basis_reason = self._equity_signal_basis_latch(decision_timestamp)
                if basis_reason is not None:
                    entry_latch = basis_reason if entry_latch is None else f"{entry_latch}|{basis_reason}"
            try:
                if account is None and getattr(self, "broker", None) is not None:
                    # Broker I/O happens before the portfolio lock. The lease
                    # is checked again at the decision boundary below so a
                    # fill cannot invalidate the cached snapshot in between.
                    account_lease = self._fresh_account_snapshot_lease()
                    account = account_lease.snapshot
                if account_lease is not None and not self._account_snapshot_lease_is_current(account_lease):
                    log.warning(
                        "Portfolio decision skipped: account snapshot lease invalidated for market=%s",
                        portfolio_runtime.market.value,
                    )
                    continue
                with self._portfolio_lock:
                    # Claim under the narrow cache guard immediately before
                    # the call. Invalidation after this point is deferred
                    # until release, closing the check-to-use race without
                    # retaining the cache lock across order I/O.
                    lease_claimed = account_lease is None or self._claim_account_snapshot_lease(account_lease)
                    if not lease_claimed:
                        log.warning(
                            "Portfolio decision skipped: account snapshot lease invalidated before use for market=%s",
                            portfolio_runtime.market.value,
                        )
                        continue
                    try:
                        kwargs = {"entry_latch": entry_latch}
                        if account is not None:
                            kwargs["account"] = account
                        if reserved_position_keys:
                            kwargs["reserved_position_keys"] = tuple(
                                sorted(
                                    reserved_position_keys,
                                    key=_reservation_sort_key,
                                )
                            )
                        portfolio_result = portfolio_runtime.process_closed_bars(relevant, **kwargs)
                    finally:
                        if account_lease is not None:
                            self._release_account_snapshot_lease(account_lease)
            except OwnershipDriftError as exc:
                # The portfolio lock was released when the exception left its
                # with-block, and this path never holds the ingestion lock.
                with getattr(self, "_state_lock", threading.RLock()):
                    self._circuit_breaker_reason = "ownership_drift"
                    self._state = "halted"
                _broker = getattr(self, "broker", None)
                automate_incident_recovery(
                    IncidentCategory.POSITION_DRIFT,
                    account_id=getattr(_broker, "account_id", "unknown") if _broker is not None else "unknown",
                    strategy=getattr(getattr(portfolio_runtime, "strategy", None), "name", "unknown"),
                    error=str(exc),
                    current_state={"market": portfolio_runtime.market.value},
                    reconcile_action=lambda: (
                        _broker.reconcile() if _broker is not None and hasattr(_broker, "reconcile") else False
                    ),
                )
                log.exception(
                    "Portfolio ownership drift blocked decisions for market=%s",
                    portfolio_runtime.market.value,
                )
                alerts.emit(
                    "circuit_breaker",
                    message="Portfolio ownership drift blocked decisions; runtime halted",
                    reason="ownership_drift",
                    market=portfolio_runtime.market.value,
                )
                return
            except Exception as exc:
                with getattr(self, "_state_lock", threading.RLock()):
                    self._circuit_breaker_reason = "portfolio_runtime_failure"
                    self._state = "halted"
                # DB integrity: fail closed immediately, never retry
                err_lower = str(exc).lower()
                if _is_database_error(exc):
                    automate_incident_recovery(
                        IncidentCategory.DATABASE_ERROR,
                        account_id=getattr(self.broker, "account_id", "unknown")
                        if hasattr(self, "broker")
                        else "unknown",
                        strategy=portfolio_runtime.strategy.name,
                        error=str(exc),
                        current_state={"market": portfolio_runtime.market.value},
                    )
                elif isinstance(exc, TimeoutError) or "timeout" in err_lower:
                    automate_incident_recovery(
                        IncidentCategory.PROVIDER_UNAVAILABLE,
                        account_id=getattr(self.broker, "account_id", "unknown")
                        if hasattr(self, "broker")
                        else "unknown",
                        strategy=portfolio_runtime.strategy.name,
                        error=str(exc),
                        current_state={"market": portfolio_runtime.market.value},
                    )
                else:
                    automate_incident_recovery(
                        IncidentCategory.PENDING_ORDER_UNKNOWN,
                        account_id=getattr(self.broker, "account_id", "unknown")
                        if hasattr(self, "broker")
                        else "unknown",
                        strategy=portfolio_runtime.strategy.name,
                        error=str(exc),
                        current_state={"market": portfolio_runtime.market.value},
                        reconcile_action=lambda: self.broker.reconcile() if hasattr(self.broker, "reconcile") else None,
                    )
                log.exception("Portfolio runtime failed closed: %s", exc)
                runtime_metrics_store.record_event("circuit_breaker")
                alerts.emit(
                    "circuit_breaker",
                    message="Portfolio runtime failed closed; runtime halted",
                    reason="portfolio_runtime_failure",
                    error=str(exc),
                    market=portfolio_runtime.market.value,
                )
                return
            if portfolio_result.status == "rebalance_blocked":
                with getattr(self, "_state_lock", threading.RLock()):
                    self._market_entry_halts[portfolio_runtime.market] = "rebalance_reconciliation_required"
                log.critical(
                    "Two-phase rebalance blocked; %s entries halted pending reconciliation (decision=%s)",
                    portfolio_runtime.market.value,
                    portfolio_result.decision_id,
                )
                runtime_metrics_store.record_event("ambiguous_order")
                alerts.emit(
                    "ambiguous_order",
                    message="Two-phase rebalance blocked; entries halted pending reconciliation",
                    market=portfolio_runtime.market.value,
                    decision_id=portfolio_result.decision_id,
                )
            log.info(
                "Portfolio decision: strategy=%s market=%s status=%s orders=%d submitted=%d",
                portfolio_runtime.strategy.name,
                portfolio_runtime.market.value,
                portfolio_result.status,
                len(portfolio_result.orders),
                len(portfolio_result.submitted_order_ids),
            )
            if portfolio_runtime.market == Market.EQUITY and (
                portfolio_result.status not in {"not_scheduled", "warming_up", "incomplete_strategy_batch"}
            ):
                observability = (
                    portfolio_result.target.metadata.get("paper_observability", {})
                    if portfolio_result.target is not None
                    else {}
                )
                log.info(
                    "Equity PAPER decision telemetry: strategy=%s version=%s status=%s "
                    "decision_timestamp=%s signal_verified_through=%s trend_state=%s "
                    "gross_after=%s planned_delta=%s blocked=%s",
                    portfolio_runtime.strategy.name,
                    portfolio_result.target.strategy_version if portfolio_result.target is not None else "",
                    portfolio_result.status,
                    observability.get("decision_timestamp"),
                    observability.get("signal_verified_through"),
                    observability.get("trend_state"),
                    observability.get("risk_overlay_gross_after"),
                    observability.get("symbols"),
                    observability.get("blocked_or_skipped_reason"),
                )
            self._record_paper_decision_receipt(portfolio_runtime, portfolio_result, account)
            self._emit_equity_daily_summary(portfolio_runtime, portfolio_result, account)
            self._last_portfolio_status[portfolio_runtime.market] = portfolio_result.status
            reserved_position_keys.update(self._ordered_buy_position_keys(portfolio_runtime, portfolio_result))

    def _record_paper_decision_receipt(
        self, portfolio_runtime: Any, result: Any, account: AccountSnapshot | None
    ) -> None:
        """Mirror one persisted PAPER decision; the repository owns later fills."""
        if (
            getattr(self, "mode", None) != RuntimeMode.PAPER
            or result is None
            or result.decision_id is None
            or result.target is None
        ):
            return
        try:
            target = result.target
            origin = str(target.metadata.get("decision_origin", "scheduled"))
            if origin not in {"scheduled", "startup_catch_up"}:
                raise ValueError(f"Unsupported decision origin: {origin}")
            orders = tuple(portfolio_runtime.repository.orders_for_decision(result.decision_id))
            positions = (
                {
                    f"{position.market.value}:{canonical_symbol(position.symbol, position.market)}": float(position.qty)
                    for position in account.positions
                    if not position.is_flat
                }
                if account is not None
                else {}
            )
            realized_gross = (
                sum(position.market_value for position in account.positions if not position.is_flat) / account.equity
                if account is not None and account.equity > 0
                else None
            )
            admission = getattr(portfolio_runtime, "last_buy_admission", None)
            has_submitted_buy = result.status == "submitted" and any(
                str(getattr(order.side, "value", order.side)).lower() == "buy" for order in result.orders
            )
            try:
                capacity = self.capacity_health()
            except Exception:
                capacity = {"unavailable": True, "reason": "capacity_sample_failed"}
            metadata = target.metadata or {}
            explanations = metadata.get("explanations", {})
            explanations = explanations if isinstance(explanations, dict) else {}
            target_symbols = [weight.symbol for weight in target.weights]
            retained = set(metadata.get("retained", ()))
            receipt = {
                "schema_version": 2,
                "receipt_id": result.decision_id,
                "decision_id": result.decision_id,
                "decision_timestamp": target.decision_timestamp.isoformat(),
                "origin": origin,
                "market": portfolio_runtime.market.value,
                "mode": self.mode.value,
                "strategy": {
                    "name": target.strategy_name,
                    "version": target.strategy_version,
                    "maximum_allocation": portfolio_runtime.maximum_allocation,
                },
                "source_code_sha": os.getenv("PARAYABUCK_SOURCE_SHA") or None,
                "configuration_digest": portfolio_runtime.configuration_digest,
                "universe": {
                    "version": target.universe_version or portfolio_runtime.universe_version,
                    "snapshot_id": target.universe_snapshot_id,
                    "data_timestamp": target.data_timestamp.isoformat() if target.data_timestamp else None,
                    "active_universe_version": portfolio_runtime.universe_version,
                    "observation_symbols": sorted(portfolio_runtime.symbols),
                    "observation_strategy_digest": (
                        self.app_config.universe.crypto.observation_strategy_parameters_digest
                        if portfolio_runtime.market == Market.CRYPTO
                        else ""
                    ),
                },
                "account_before": {
                    "equity": float(account.equity) if account is not None else None,
                    "cash": float(account.cash) if account is not None else None,
                    "cash_ratio": float(account.cash / account.equity)
                    if account is not None and account.equity > 0
                    else None,
                    "realized_gross_ratio": realized_gross,
                    "positions": positions,
                },
                "capacity_snapshot": {**capacity, "phase": "post_decision_observation"},
                "final_buy_admission": (
                    {
                        "gross_headroom": admission.gross_headroom,
                        "cash_headroom": admission.cash_headroom,
                        "committed_buy_notional": admission.committed_buy_notional,
                        "realized_gross": admission.realized_gross,
                        "account_equity": admission.account.equity,
                        "account_cash": admission.account.cash,
                        "admitted_client_order_ids": [order.client_order_id for order in admission.orders],
                    }
                    if has_submitted_buy and admission is not None
                    else None
                ),
                "target_state": {
                    "kind": "INTENDED_POST_RISK",
                    "rebalance_reason": target.rebalance_reason.value,
                    "weights": {weight.symbol: float(weight.weight) for weight in target.weights},
                    "selected_symbols": sorted(target_symbols),
                    "cash_weight": float(target.cash_weight),
                },
                "planned_orders": [
                    {
                        "kind": "INTENDED",
                        "client_order_id": order.client_order_id,
                        "symbol": order.symbol,
                        "market": order.market.value,
                        "side": order.side.value,
                        "quantity": float(order.quantity),
                        "reference_price": float(order.reference_price),
                    }
                    for order in result.orders
                ],
                "submission_snapshot": [{**order, "kind": "SUBMISSION_STATUS_AT_DECISION_WRITE"} for order in orders],
                "execution_confirmation": {
                    "source": "portfolio_orders + portfolio_fills + portfolio_execution_events",
                    "state": "QUERY_AUTHORITATIVE_DB_AT_AUDIT_TIME",
                    "confirmed_positions_after": None,
                },
                "crypto_provenance": {
                    "ranks": {
                        symbol: detail.get("alpha_rank")
                        for symbol, detail in explanations.items()
                        if symbol in target_symbols
                        and isinstance(detail, dict)
                        and isinstance(detail.get("alpha_rank"), int)
                    },
                    "retained": {
                        symbol: symbol in retained
                        or bool(
                            explanations.get(symbol, {}).get("retained", False)
                            if isinstance(explanations.get(symbol), dict)
                            else False
                        )
                        for symbol in target_symbols
                    },
                    "previous_holdings": {key: qty for key, qty in positions.items() if key.startswith("crypto:")},
                }
                if portfolio_runtime.market == Market.CRYPTO
                else None,
                "database_link": {
                    "decision_table": "portfolio_decisions",
                    "order_table": "portfolio_orders",
                    "fill_table": "portfolio_fills",
                    "execution_event_table": "portfolio_execution_events",
                },
            }
            path = paper_receipt.write_decision_receipt_v2(receipt, paper_receipt.default_receipt_dir())
            runtime_metrics_store.record_event("paper_receipt_written")
            log.info("PAPER decision receipt v2 written: %s", path)
        except Exception:
            log.warning("PAPER decision receipt v2 failed for %s", result.decision_id, exc_info=True)
            try:
                alerts.emit(
                    "paper_incident",
                    message="PAPER decision receipt unavailable; forward observation invalid until repaired",
                    decision_id=result.decision_id,
                    reason="receipt_write_failed",
                )
            except Exception:
                log.warning("Could not emit PAPER receipt incident for %s", result.decision_id, exc_info=True)

    def _emit_equity_daily_summary(self, portfolio_runtime: Any, result: Any, account: AccountSnapshot | None) -> None:
        """One-line operator summary of a persisted equity decision. Never raises."""
        try:
            if (
                getattr(portfolio_runtime, "market", None) != Market.EQUITY
                or result is None
                or getattr(result, "decision_id", None) is None
                or getattr(result, "target", None) is None
            ):
                return
            app_config = getattr(self, "app_config", None)
            alerting_config = getattr(app_config, "alerting", None)
            if alerting_config is not None and not getattr(alerting_config, "daily_summary", True):
                return
            from parayabuck.monitoring.daily_summary import build_equity_daily_summary

            message, fields = build_equity_daily_summary(
                result.target,
                account,
                status=str(result.status),
                decision_id=result.decision_id,
                orders=len(result.orders),
                submitted=len(result.submitted_order_ids),
            )
            alerts.emit("daily_summary", message=message, **fields)
        except Exception:
            log.warning("Equity daily summary failed", exc_info=True)

    def _run_startup_catch_up(self, *, account: AccountSnapshot | None = None) -> None:
        """Force an immediate full-target decision on the latest warmup bars.

        A fresh account started mid-period would otherwise sit in cash until
        the next cadence boundary (the backtest engine decides on its first
        usable bar). Expected gates/readiness/no-trade outcomes are returned
        as ``PortfolioRuntimeResult`` statuses. Exceptions are invariant,
        persistence, safety, or provider failures and propagate to ``start``
        so an invalid engine cannot advertise itself as running.
        """
        if (
            not self._running
            or self._warming_up
            or self._circuit_breaker_reason is not None
            or getattr(self, "_reconcile_failed", False)
        ):
            return
        reserved_position_keys: set[tuple[Market, str]] = set()
        for portfolio_runtime in self._crypto_first_portfolio_order(getattr(self, "_portfolio_runtimes", ())):
            reasons = self._entry_readiness_reasons(portfolio_runtime.market)
            if reasons:
                portfolio_runtime.startup_catch_up_status = "blocked:" + ",".join(reasons)
                continue
            entry_latch = getattr(self, "_entry_risk_latch", {}).get(portfolio_runtime.market)
            if portfolio_runtime.market == Market.EQUITY:
                # Latch on the last COMPLETED session, never on a forming
                # provider bar adopted by an intra-session verification.
                prune = getattr(portfolio_runtime, "prune_unfinished_signal_sessions", None)
                if prune is not None:
                    with self._portfolio_lock:
                        prune()
                newest = max(
                    (series[-1].timestamp for series in portfolio_runtime.history.values() if series),
                    default=None,
                )
                basis_reason = self._equity_signal_basis_latch(newest) if newest is not None else None
                if basis_reason is not None:
                    entry_latch = basis_reason if entry_latch is None else f"{entry_latch}|{basis_reason}"
            with self._portfolio_lock:
                catch_up_kwargs = {"entry_latch": entry_latch}
                if account is not None:
                    catch_up_kwargs["account"] = account
                if reserved_position_keys:
                    catch_up_kwargs["reserved_position_keys"] = tuple(
                        sorted(
                            reserved_position_keys,
                            key=_reservation_sort_key,
                        )
                    )
                result = portfolio_runtime.startup_catch_up(**catch_up_kwargs)
            self._record_paper_decision_receipt(portfolio_runtime, result, account)
            self._emit_equity_daily_summary(portfolio_runtime, result, account)
            reserved_position_keys.update(self._ordered_buy_position_keys(portfolio_runtime, result))
            log.info(
                "Startup catch-up: market=%s status=%s decision_id=%s",
                portfolio_runtime.market.value,
                result.status,
                result.decision_id,
            )

    def _entry_readiness_reasons(
        self,
        market: Market | None = None,
        *,
        health: RuntimeHealth | None = None,
    ) -> tuple[str, ...]:
        # health() is expensive; the decision loop builds ONE RuntimeHealth
        # per batch and shares it across every market's gate in the batch.
        if health is None:
            health = self.health()
        reasons: list[str] = []
        if self._state in _ENTRY_BLOCKING_STATES:
            reasons.append(f"runtime_state:{self._state}")
        if health.queue_age_seconds > self.config.maximum_queue_age_seconds:
            reasons.append("event_queue_stale")
        stale = (
            self._last_account_at is None
            or (datetime.now(UTC) - self._last_account_at).total_seconds()
            > self.config.maximum_account_age_for_entry_seconds
        )
        if stale:
            reasons.append("account_snapshot_stale")
            try:
                if _observe is not None:
                    age = None
                    if self._last_account_at is not None:
                        age = (datetime.now(UTC) - self._last_account_at).total_seconds()
                    _observe.on_account_stale(
                        age_seconds=age,
                        session_id=getattr(self, "_runtime_session_id", None),
                        account_id=getattr(self.broker, "account_id", None) if hasattr(self, "broker") else None,
                    )
            except Exception:
                pass
        else:
            try:
                if _observe is not None:
                    _observe.on_account_recovered(
                        session_id=getattr(self, "_runtime_session_id", None),
                        account_id=getattr(self.broker, "account_id", None) if hasattr(self, "broker") else None,
                    )
            except Exception:
                pass
        if self._circuit_breaker_reason:
            reasons.append(self._circuit_breaker_reason)
        relevant_stream = _STREAM_WORKER_BY_MARKET.get(market) if market is not None else None
        shared_workers = {"pb-decisions", "pb-account", "pb-trade-updates"}
        if relevant_stream:
            shared_workers.add(relevant_stream)
        else:
            shared_workers.update(_MARKET_BY_STREAM_WORKER)
        dead_workers = [name for name, worker in self._workers.items() if name in shared_workers and not worker.alive]
        if dead_workers:
            reasons.append("worker_unavailable:" + ",".join(sorted(dead_workers)))
        # One atomic read: writers clear halts under _state_lock on other
        # threads, so a check-then-index pair could append the string "None".
        halt_reason = getattr(self, "_market_entry_halts", {}).get(market) if market is not None else None
        if halt_reason:
            reasons.append(str(halt_reason))
        return tuple(reasons)

    def _engine_has_market_gap(self, market: Market) -> bool:
        return bool(self.market_state.has_data_gaps_for_market(market))

    def _engine_has_symbol_gap(self, symbol: str, market: Market | None = None) -> bool:
        try:
            return bool(self.market_state.has_data_gaps_for_symbol(symbol, market))
        except TypeError:
            return bool(self.market_state.has_data_gaps_for_symbol(symbol))

    def _repair_gap(self, bar: Bar) -> None:
        history = self._historical
        if history is None:
            return
        series = self.market_state.bars.get(bar.symbol, bar.timeframe, bar.market)
        previous = next(
            (candidate for candidate in reversed(series[:-1]) if candidate.timestamp < bar.timestamp),
            None,
        )
        if previous is None:
            return
        try:
            # RAW basis: this repairs the EXECUTION-side market state, whose
            # bars are as-traded stream truth. The strategy's signal history
            # is a separate splits-basis series bridged by
            # data/adjustment.py and must never be fed mixed bases here.
            backfill = history.get_bars(
                bar.symbol,
                bar.timeframe,
                start=previous.timestamp,
                end=bar.timestamp,
                adjustment="raw",
            )
            # Lock only the mutation: history.get_bars above is a network
            # fetch and must never hold _ingestion_lock against a hot-swap.
            repaired = False
            if backfill:
                with self._ingestion_lock:
                    repaired = self.market_state.repair_gap(backfill)
            if repaired:
                log.info(
                    "Historical gap repaired: %s %s %s..%s",
                    bar.symbol,
                    bar.timeframe,
                    previous.timestamp,
                    bar.timestamp,
                )
            else:
                log.warning(
                    "Historical gap remains unresolved; symbol stays entry-disabled: %s %s",
                    bar.symbol,
                    bar.timeframe,
                )
        except Exception as exc:
            log.warning(
                "Historical gap repair failed; symbol stays entry-disabled: %s %s: %s",
                bar.symbol,
                bar.timeframe,
                exc,
            )

    # -- equity signal-basis authority (provider splits series) -------------

    def _signal_adjustment_state(self) -> dict[str, object]:
        state: dict[str, object] | None = getattr(self, "_signal_adjustment", None)
        if state is None:
            state = {
                "healthy": None,
                "last_success_at": None,
                "last_failure_at": None,
                "last_error": None,
                "verified_through_by_symbol": {},
            }
            self._signal_adjustment = state
        return state

    def _record_signal_adjustment_success(self, verified_through_by_symbol: dict[str, datetime]) -> None:
        """Record a successful reconciliation. "Recovery" is reserved for the
        FAILED -> healthy transition: initial unknown -> healthy is silent
        (the RuntimeHealth `signal_adjustment` block already reports it), so
        exactly one recovery alert exists per failure episode and repeated
        healthy checks never emit duplicates."""
        state = self._signal_adjustment_state()
        was_failed = state["healthy"] is False
        state["healthy"] = True
        state["last_success_at"] = datetime.now(UTC)
        state["verified_through_by_symbol"] = dict(verified_through_by_symbol)
        state["last_error"] = None
        if was_failed:
            runtime_metrics_store.record_event("signal_adjustment_recovery")
            alerts.emit(
                "signal_adjustment_recovery",
                message="Equity signal basis verified against provider split-adjusted history",
                symbols=sorted(verified_through_by_symbol),
            )

    def _record_signal_adjustment_failure(self, error: str) -> None:
        """Record a failed verification. Exactly ONE failure alert per
        episode: repeated failed checks stay silent until recovery."""
        state = self._signal_adjustment_state()
        was_healthy = state["healthy"] is not False
        state["healthy"] = False
        state["last_failure_at"] = datetime.now(UTC)
        state["last_error"] = error
        if was_healthy:
            runtime_metrics_store.record_event("signal_adjustment_failure")
            alerts.emit(
                "signal_adjustment_failure",
                message=(
                    "Equity signal basis UNVERIFIED; new equity entries fail "
                    "closed until a batched provider reconciliation succeeds"
                ),
                error=error,
            )

    def _equity_signal_basis_verified(self, decision_timestamp: datetime | None = None) -> bool:
        """Fresh verification (last hour) AND — when a decision timestamp is
        given — PER-SYMBOL watermarks covering that timestamp's New York
        session (``covers_session``: the provider labels a session at 00:00
        ET, the stream decision bar at 09:30 ET). A healthy check from an
        earlier session never authorizes today's decision bar."""
        if not getattr(self, "_symbols_by_market", {}).get(Market.EQUITY):
            return True  # no equity universe: nothing to verify
        state = self._signal_adjustment_state()
        # An explicit healthy=True is mandatory: a failure (or a manual/
        # membership-driven reset to unknown) must invalidate freshness even
        # if an older success timestamp is still recent.
        if state.get("healthy") is not True:
            return False
        last_success = state["last_success_at"]
        if not isinstance(last_success, datetime):
            return False
        if (datetime.now(UTC) - last_success).total_seconds() > (
            _SIGNAL_ADJUSTMENT_CHECK_SECONDS + _SIGNAL_ADJUSTMENT_STALE_SLACK_SECONDS
        ):
            return False
        if decision_timestamp is None:
            return True
        watermarks = state.get("verified_through_by_symbol")
        if not isinstance(watermarks, dict):
            return False
        for symbol in self._symbols_by_market.get(Market.EQUITY, ()):
            verified = watermarks.get(symbol)
            if not isinstance(verified, datetime) or not covers_session(verified, decision_timestamp):
                return False
        return True

    def verify_equity_signal_basis(self, required_through: datetime | None = None) -> bool:
        """Batched provider-authority reconciliation for the equity SIGNAL.

        ONE batched splits request covering the ENTIRE required strategy
        history window (max minimum_history_bars + margin) — never a short
        tail standing in for a long history. Adequacy is enforced PER SYMBOL
        BEFORE anything is adopted:

        * non-empty coverage reaching back to the required window start;
        * when ``required_through`` (a decision-day timestamp) is given, the
          symbol's provider series must reach its New York session — a
          provider that has not yet published the decision-day bar leaves
          that day UNVERIFIED.

        Any inadequate symbol fails the WHOLE check closed (no partial
        adoption, no cross-symbol watermark). Returns True only when every
        required symbol is provider-verified; callers must then block new
        equity entries on False while risk-reducing exits keep flowing.

        Hardening (per-symbol trailing-window, watermark, retries, dedup):
        * Per-symbol trailing window — ``required_oldest_by_symbol`` is
          derived from EACH symbol's own trailing window (``series[-required]``
          or ``series[0]`` for new listings with short history, or a
          calendar fallback), so a newly listed 5-day symbol does not inherit
          a 300-bar global requirement and does not fail verification.
        * Verified-through watermark — per-symbol ``verified_through_by_symbol``
          is monotonic (``max`` timestamp, updated only on adequate coverage)
          and restored only via ``adopt_authoritative_signal_history``; a
          failed or partial provider response never advances or partially
          overwrites watermarks (fail-closed). Membership changes
          (``PortfolioRuntime.update_symbols``) reset watermarks conservatively
          so new symbols must be verified before entries.
        * Bounded retries — provider fetch is retried at most once on
          transient network errors (``_is_transient_network_error``) with a
          short backoff; adequacy failures (empty/partial/missing bars) are
          NOT retried — they fail closed immediately. Retries are bounded to
          keep provider-call budgets predictable.
        * New listings / missing bars / partial provider — an empty series,
          a partially returned symbol set, or a provider that has not yet
          published the decision-day bar leaves that symbol ``inadequate``
          and the whole check fails closed with no partial adoption.
        * Dedup identical provider calls — consecutive calls with identical
          sorted symbols, same computed start/end window, same
          ``required_through`` and same timeframe/adjustment within a short
          TTL (45 s) reuse the last successful verified_through without
          re-hitting Alpaca. This is measured via
          ``provider_symbols_requested``/``provider_bars_returned``/
          ``provider_calls`` and never weakens fail-closed (a new decision
          timestamp or new symbol set invalidates the dedup key).
        """
        history_client = getattr(self, "_historical", None)
        runtimes = [
            runtime for runtime in self._portfolio_runtimes if runtime.market == Market.EQUITY and runtime.history
        ]
        if history_client is None or not runtimes:
            return True
        symbols = sorted(self._symbols_by_market.get(Market.EQUITY, ()))
        if not symbols:
            return True
        end = datetime.now(UTC)
        # The coverage REQUIREMENT comes from what the strategy can actually
        # READ — the oldest bar of its trailing required window — NOT from
        # the network request boundary: Alpaca's inclusive `start` yields a
        # first daily bar at/after it (later still across weekends/holidays),
        # so comparing provider_oldest against the request start would fail
        # every honest response. We derive the strategy-required oldest
        # timestamp, then request with a safety margin BEFORE it.
        required_bars = max(
            (runtime.strategy.minimum_history_bars + 1 for runtime in runtimes),
            default=1,
        )
        fallback_span = bars_to_calendar_days("SPY", "1d", required_bars)
        fallback_oldest = end - timedelta(days=fallback_span)

        required_oldest_by_symbol: dict[str, datetime] = {}
        for symbol in symbols:
            candidates: list[datetime] = []
            for runtime in runtimes:
                required = runtime.strategy.minimum_history_bars + 1
                series = runtime.history.get(symbol)
                if series:
                    if len(series) >= required:
                        candidates.append(series[-required].timestamp)
                    else:
                        candidates.append(series[0].timestamp)
            if candidates:
                required_oldest_by_symbol[symbol] = min(candidates)
            else:
                required_oldest_by_symbol[symbol] = fallback_oldest

        earliest_required = min(required_oldest_by_symbol.values(), default=fallback_oldest)
        start = min(
            earliest_required - timedelta(days=_SIGNAL_ADJUSTMENT_WINDOW_MARGIN_DAYS),
            fallback_oldest,
        )
        # — Dedup identical provider calls (bounded, measured, fail-closed) —
        # Key is symbols + required_through + timeframe/adjustment; start/end
        # (derived from strategy window + now()) are intentionally excluded so
        # rapid repeated latches within the same second deduplicate even though
        # `end = now()` drifts by milliseconds. A new symbol set or a new
        # decision timestamp invalidates the key.
        dedup_key = (
            tuple(symbols),
            required_through.isoformat() if required_through is not None else None,
            "1d",
            "splits",
        )
        dedup_state: dict[str, object] | None = getattr(self, "_signal_verify_dedup", None)
        if dedup_state is not None:
            last_key = dedup_state.get("key")
            last_at = dedup_state.get("at")
            last_verified = dedup_state.get("verified_through_by_symbol")
            # Dedup only when current health is still healthy (not forced to
            # None/False) — a manual reset (test or membership change) must
            # force a real provider fetch, otherwise a rebased split would be
            # missed and history would stay unverified.
            current_healthy = self._signal_adjustment_state().get("healthy") is True
            if (
                current_healthy
                and last_key == dedup_key
                and isinstance(last_at, datetime)
                and (end - last_at).total_seconds() < 45.0
                and isinstance(last_verified, dict)
                and dedup_state.get("result") is True
            ):
                # Watermark still fresh for this exact window: reuse without
                # provider call, but re-validate per-symbol through-gate.
                if required_through is not None:
                    if all(
                        isinstance(last_verified.get(symbol), datetime)
                        and covers_session(last_verified[symbol], required_through)
                        for symbol in symbols
                    ):
                        return True
                else:
                    return True
        # Every loop exit either breaks with ``adjusted`` assigned or returns
        # False from the except branch (always on the second attempt).
        adjusted: dict[str, list[Any]] = {}
        for attempt in range(2):
            try:
                adjusted = history_client.get_bars_batch(symbols, "1d", start=start, end=end, adjustment="splits")
                break
            except Exception as exc:
                if not _is_transient_network_error(exc) or attempt == 1:
                    self._record_signal_adjustment_failure(f"provider fetch failed: {exc}")
                    return False
                # Transient: bounded retry with short backoff.
                time.sleep(0.5 * (attempt + 1))
                continue
        inadequate = [
            symbol
            for symbol in symbols
            if not adequate_coverage(
                adjusted.get(symbol, ()),
                required_oldest=required_oldest_by_symbol.get(symbol, fallback_oldest),
                required_newest=required_through,
            )
        ]
        if inadequate:
            self._record_signal_adjustment_failure("provider coverage incomplete for symbols: " + ",".join(inadequate))
            return False
        verified_through_by_symbol = {
            symbol: max(bar.timestamp for bar in adjusted.get(symbol, ())) for symbol in symbols
        }
        try:
            changed_total = 0
            # ``adopt_authoritative_signal_history`` REPLACES the per-symbol
            # list object. ``process_closed_bars`` runs under this same lock
            # and appends to the list it read, so an unlocked swap between
            # its read and its append would orphan the completed bar. The
            # lock is taken here alone — no caller holds the ingestion lock
            # on the way in, so the ingestion -> portfolio order holds.
            with getattr(self, "_portfolio_lock", threading.RLock()):
                for runtime in runtimes:
                    changed_total += runtime.adopt_authoritative_signal_history(
                        {symbol: adjusted.get(symbol, []) for symbol in runtime.symbols},
                        verified_through_by_symbol,
                    )
        except Exception as exc:
            self._record_signal_adjustment_failure(f"rebuild failed: {exc}")
            return False
        self._record_signal_adjustment_success(verified_through_by_symbol)
        # Store dedup entry (measured) only on success; failures never cache
        # so a transient outage is retried on the next decision latch.
        self._signal_verify_dedup = {
            "key": dedup_key,
            "at": end,
            "verified_through_by_symbol": dict(verified_through_by_symbol),
            "result": True,
        }
        if changed_total:
            log.warning(
                "Signal history merged from provider authority: %d symbol(s) changed",
                changed_total,
            )
        return True

    def _equity_signal_basis_latch(self, decision_timestamp: datetime | None = None) -> str | None:
        """Latch reason when equity signals are unverified/stale, else None.

        Verification runs when the last success is stale OR — always — when
        any required symbol's watermark predates the decision timestamp. The
        invariant: a new-equity-entry decision may only pass when
        ``signal_verified_through_by_symbol[s]`` covers the decision
        timestamp's New York session for EVERY required equity signal
        symbol. Unverifiable basis FAILS CLOSED for entries; risk-reducing
        exits keep flowing because callers only cap targets at current
        weights.
        """
        if not getattr(self, "_symbols_by_market", {}).get(Market.EQUITY):
            return None
        if self._equity_signal_basis_verified(decision_timestamp):
            return None
        if not self.verify_equity_signal_basis(required_through=decision_timestamp):
            return "signal_adjustment_unverified"
        if not self._equity_signal_basis_verified(decision_timestamp):
            # Verification succeeded for history but the provider has not
            # published the decision-day bar yet: still fail closed.
            return "signal_adjustment_unverified"
        return None

    def _signal_adjustment_loop(self) -> None:
        """Health monitor for the equity signal basis.

        Not decision-critical: decisions verify synchronously. This loop
        keeps a heartbeat, refreshes verification during quiet periods so a
        split is absorbed before the next session, detects staleness and
        emits failure/recovery transitions.
        """
        worker = self._workers["pb-signal-adjustment"]
        worker.alive = True
        try:
            while self._running:
                worker.last_heartbeat = datetime.now(UTC)
                self._signal_adjustment_monitor_pass(worker)
                self._stop_event.wait(_SIGNAL_ADJUSTMENT_CHECK_SECONDS)
        finally:
            worker.alive = False

    def _signal_adjustment_monitor_pass(self, worker: WorkerHealth) -> None:
        """One heartbeat of the health monitor (extracted for tests)."""
        if getattr(self, "_warming_up", False):
            # Never verify against a half-seeded runtime: warmup owns the
            # history until it completes (startup-race guard).
            return
        try:
            self.verify_equity_signal_basis()
        except Exception as exc:  # defensive: never kill the monitor
            worker.last_error = str(exc)
            log.warning("Signal adjustment monitor pass failed: %s", exc)
        # Staleness detection: equity active but nothing verified for over a
        # full trading day + buffer means the basis cannot be trusted even
        # if no exception fired (empty responses etc.).
        state = self._signal_adjustment_state()
        last_success = state["last_success_at"]
        if (
            getattr(self, "_symbols_by_market", {}).get(Market.EQUITY)
            and isinstance(last_success, datetime)
            and datetime.now(UTC) - last_success > timedelta(hours=26)
        ):
            self._record_signal_adjustment_failure("stale: no successful reconciliation in over 26h")

    def _refresh_readiness(self) -> None:
        runtimes = tuple(getattr(self, "_portfolio_runtimes", ()))
        required_symbols = {symbol for runtime in runtimes for symbol in runtime.symbols}
        # The universe/hot-swap thread mutates the live set from
        # ``_apply_runtime_universes`` under this same lock, and comparing or
        # copying it while a swap resizes it raises RuntimeError inside the
        # decision worker. So every access is serialized here and the local
        # is an immutable snapshot; state lock only, nothing inside the loop
        # takes another lock.
        state_lock = getattr(self, "_state_lock", threading.RLock())
        with state_lock:
            for symbol in required_symbols:
                market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
                relevant = [runtime for runtime in runtimes if runtime.market == market and symbol in runtime.history]
                ready = bool(relevant)
                for runtime in relevant:
                    series = runtime.history[symbol]
                    benchmark_symbol = "BTC/USD" if market == Market.CRYPTO else "SPY"
                    benchmark_series = runtime.history.get(benchmark_symbol, ())
                    expected_latest = (
                        benchmark_series[-1].timestamp
                        if benchmark_series
                        else max(
                            (candidate[-1].timestamp for candidate in runtime.history.values() if candidate),
                            default=None,
                        )
                    )
                    ready = ready and (
                        len(series) >= runtime.strategy.minimum_history_bars
                        and expected_latest is not None
                        and series[-1].timestamp == expected_latest
                    )
                if ready:
                    self._not_ready_symbols.discard(symbol)
                else:
                    self._not_ready_symbols.add(symbol)
            pending = frozenset(self._not_ready_symbols)
        attempt = getattr(self, "_warmup_retry_attempt", 0)
        # The counter describes the pending SET, not the process: it resets on
        # ANY membership change — the set emptying, a member recovering, or a
        # newly unready symbol that was never retried — so a chronically
        # lagging symbol cannot push a brand-new one out to the ceiling.
        # Only an unchanged set keeps doubling toward the cap.
        if not pending or pending != getattr(self, "_warmup_retry_symbols", frozenset()):
            attempt = 0
            self._warmup_retry_attempt = 0
        retry_delay = _WARMUP_RETRY_SECONDS * 2 ** min(attempt, _WARMUP_RETRY_MAX_EXPONENT)
        if (
            self._running
            and pending
            and time.monotonic() - getattr(self, "_last_warmup_retry_at", time.monotonic()) >= retry_delay
        ):
            self._schedule_warmup_retry()
        with state_lock:
            # Atomic with every halt writer, and never promotes past a breaker.
            if (
                self._running
                and self._state == "warming_up"
                and getattr(self, "_circuit_breaker_reason", None) is None
                and self._has_ready_symbols()
            ):
                self._state = "running"

    def _schedule_warmup_retry(self) -> None:
        # Reached from the decision loop, the universe-refresh thread and the
        # retry thread itself: keep check-spawn-assign atomic so overlapping
        # callers cannot start duplicate pb-warmup-retry threads and orphan
        # the overwritten reference (never joined by stop).
        with self._warmup_retry_spawn_lock:
            current = getattr(self, "_warmup_retry_thread", None)
            if current is not None and current.is_alive():
                return
            self._last_warmup_retry_at = time.monotonic()
            self._warmup_retry_attempt += 1
            # Same lock the universe/hot-swap thread takes: a resize racing
            # this copy would raise out of the spawn lock.
            with getattr(self, "_state_lock", threading.RLock()):
                self._warmup_retry_symbols = frozenset(self._not_ready_symbols)
            thread = threading.Thread(
                target=self._retry_incomplete_warmup,
                name="pb-warmup-retry",
                daemon=True,
            )
            self._warmup_retry_thread = thread
            thread.start()

    def _retry_incomplete_warmup(self) -> None:
        history = self._historical
        if history is None:
            return
        symbols = sorted(self._not_ready_symbols)
        log.info("Retrying incomplete warmup for %d symbols", len(symbols))
        self._warmup_symbols(history, symbols, retry=True)
        self._refresh_readiness()

    def _has_ready_symbols(self) -> bool:
        required = {
            symbol for runtime in getattr(self, "_portfolio_runtimes", ()) for symbol in getattr(runtime, "symbols", ())
        }
        return bool(required) and not (required & self._not_ready_symbols)

    def _persist_risk_state(self, account: AccountSnapshot) -> None:
        # _account_loop, the decision loop and reconciliation call this
        # concurrently; the load-compare-record sequence must be atomic or
        # the same realized PnL delta is counted twice.
        with getattr(self, "_risk_state_lock", threading.Lock()):
            account_id = self.broker.account_id or "unknown"
            today = datetime.now(UTC).date()
            previous = self.ledger.load_risk_state(account_id, today)
            current_losses = getattr(
                self,
                "_consecutive_losses",
                previous.consecutive_losses if previous is not None else 0,
            )
            if previous is not None and current_losses == previous.consecutive_losses:
                realized_delta = float(account.realized_pnl) - previous.realized_pnl
                if abs(realized_delta) > 1e-9:
                    # Insurance for fills recovered from broker activities
                    # while the TradingStream callback was unavailable. Live
                    # callbacks already change the counter, so the equality
                    # guard prevents counting the same close twice.
                    if realized_delta < 0:
                        current_losses += 1
                    elif realized_delta > 0:
                        current_losses = 0
            self._consecutive_losses = current_losses
            self._evaluate_entry_risk_latch(account)
            reason = self._circuit_breaker_reason
            # The circuit_breaker_reason in the session row is kept for
            # backward compatibility; the incident table is authoritative for
            # restore, so any halt reason in effect is also recorded there.
            if reason is not None:
                self.ledger.save_safety_incident(account_id, reason)
            self.ledger.save_risk_state(
                RiskState(
                    account_id=account_id,
                    session_date=today,
                    day_start_equity=float(account.equity - account.daily_pnl),
                    peak_equity=float(account.peak_equity),
                    realized_pnl=float(account.realized_pnl),
                    unrealized_pnl=float(account.unrealized_pnl),
                    fees=self.ledger.total_fees_for_account(account_id),
                    consecutive_losses=current_losses,
                    circuit_breaker_reason=reason,
                    updated_at=datetime.now(UTC),
                    peak_epoch=int(getattr(self.broker, "_peak_epoch", 0) or 0),
                )
            )

    def _reconcile_safely(self) -> None:
        reconcile_lock = getattr(self, "_reconcile_lock", threading.Lock())
        with reconcile_lock:
            try:
                report = ReconciliationReport.from_result(self.broker.reconcile())
                if report.has_drift:
                    log.warning(
                        "Reconciliation categories show drift (fail-closed, new orders blocked): %s",
                        "; ".join(f"{key}={values}" for key, values in report.drift_categories.items()),
                    )
                    self._raise_reconciliation_breaker("reconciliation_failed")
                    self._reconcile_failed = True
                    self._persist_reconciliation_incident("reconciliation_failed")
                    runtime_metrics_store.record_event("reconciliation_failure")
                    alerts.emit(
                        "reconciliation_failure",
                        message="Reconciliation drift detected; new orders blocked pending verified reconcile",
                        reason="reconciliation_failed",
                        categories=report.as_dict(),
                    )
                    # Still update last_reconciliation_at so the drift is visible,
                    # but do not clear incidents — require operator-verified recovery.
                    self._last_reconciliation_at = datetime.now(UTC)
                    self._last_account_at = datetime.now(UTC)
                    self._generic_reconcile_failures = 0
                    return
                self._last_reconciliation_at = datetime.now(UTC)
                self._last_account_at = datetime.now(UTC)
                self._generic_reconcile_failures = 0
                try:
                    if _observe is not None:
                        _observe.on_reconciliation_complete(
                            phase="periodic", session_id=getattr(self, "_runtime_session_id", None)
                        )
                except Exception:
                    log.warning("Periodic reconciliation observation failed", exc_info=True)
                account_id = self.broker.account_id or "unknown"
                if getattr(self, "_reconcile_failed", False):
                    self._reconcile_failed = False
                    runtime_metrics_store.record_event("reconciliation_recovered")
                    alerts.emit(
                        "reconciliation_recovered",
                        message="Periodic reconciliation recovered",
                        account_id=account_id,
                    )
                self._clear_reconciliation_incidents()
            except ReconciliationResultError as exc:
                self._raise_reconciliation_breaker("reconciliation_failed")
                self._reconcile_failed = True
                self._persist_reconciliation_incident("reconciliation_failed")
                runtime_metrics_store.record_event("reconciliation_failure")
                alerts.emit(
                    "reconciliation_failure",
                    message="Reconciliation returned an invalid result shape",
                    reason="reconciliation_failed",
                    error=str(exc),
                )
            except RequestException as exc:
                # Alpaca occasionally times out for a single request. The next
                # scheduled reconciliation retries it; a full traceback every
                # minute obscures actionable protection failures.
                log.warning("Position protection check deferred after Alpaca API error: %s", exc)
                self._raise_reconciliation_breaker("reconciliation_failed")
                self._reconcile_failed = True
                self._persist_reconciliation_incident("reconciliation_failed")
                runtime_metrics_store.record_event("reconciliation_failure")
                alerts.emit(
                    "reconciliation_failure",
                    message="Position protection check deferred after Alpaca API error",
                    reason="reconciliation_failed",
                    error=str(exc),
                )
            except OwnershipDriftError:
                log.exception("Portfolio ownership drift detected; entries halted")
                # reconcile -> state is the established order: no thread
                # holding the state lock ever takes the reconcile lock.
                with getattr(self, "_state_lock", threading.RLock()):
                    first = self._circuit_breaker_reason != "ownership_drift"
                    self._circuit_breaker_reason = "ownership_drift"
                    self._state = "halted"
                self._reconcile_failed = True
                try:
                    self._persist_safety_state("ownership_drift")
                finally:
                    # Edge-triggered like the account-loop drift halt:
                    # persistent drift re-asserts the halt every pass but
                    # alerts once, even when this pass's persist raises.
                    if first:
                        runtime_metrics_store.record_event("circuit_breaker")
                        alerts.emit(
                            "circuit_breaker",
                            message="Portfolio ownership drift detected; runtime halted",
                            reason="ownership_drift",
                        )
            except ProtectionReconciliationError:
                log.exception("Managed position protection reconciliation failed")
                self._raise_reconciliation_breaker("protection_failure")
                self._reconcile_failed = True
                self._persist_reconciliation_incident("protection_failure")
                runtime_metrics_store.record_event("reconciliation_failure")
                alerts.emit(
                    "reconciliation_failure",
                    message="Managed position protection reconciliation failed",
                    reason="protection_failure",
                )
            except Exception:
                # A generic failure is usually a code bug, not a broker
                # timeout. Keep retrying (defense in depth) but escalate after
                # three consecutive failures, which is strictly more lenient
                # than a transient network error.
                self._generic_reconcile_failures = getattr(self, "_generic_reconcile_failures", 0) + 1
                log.exception("Periodic position protection check failed")
                self._reconcile_failed = True
                failure_reason = (
                    "reconciliation_bug_suspected" if self._generic_reconcile_failures >= 3 else "reconciliation_failed"
                )
                self._raise_reconciliation_breaker(failure_reason)
                self._persist_reconciliation_incident(failure_reason)
                runtime_metrics_store.record_event("reconciliation_failure")
                alerts.emit(
                    "reconciliation_failure",
                    message="Periodic position protection check failed",
                    reason=failure_reason,
                    consecutive_failures=self._generic_reconcile_failures,
                )

    def _schedule_reconcile(self) -> None:
        """Run the expensive broker scan without stalling finalized-bar decisions."""
        # Called from both the trade-update stream thread and the decision
        # loop: the is_alive() check, the assignment and start() must be one
        # atomic step or two callers can spawn duplicate pb-reconcile threads
        # and orphan the overwritten Thread reference (never joined by stop).
        with self._reconcile_spawn_lock:
            current = self._reconcile_thread
            if current is not None and current.is_alive():
                log.warning("Periodic reconciliation still running; overlapping scan skipped")
                return
            thread = threading.Thread(
                target=self._reconcile_safely,
                name="pb-reconcile",
                daemon=True,
            )
            self._reconcile_thread = thread
            thread.start()

    def start_new_risk_epoch(self) -> float:
        """Operator-verified drawdown-baseline reset (explicit recovery path
        for the ``portfolio_drawdown_halt`` operational incident).

        The drawdown peak is monotonic, so a >limit drawdown cannot recover
        on its own while entries stay blocked. This action rebases the peak
        baseline to the current account equity after a verified broker
        reconciliation and a fresh account snapshot, clears the drawdown
        incident and re-evaluates the entry latch. It NEVER touches other
        incidents (ownership drift, protection failure, ...) or the circuit
        breaker: those keep their own recovery flows.

        Returns the new baseline equity.
        """
        if not self._running:
            raise RuntimeError("The engine is not running; start it before resetting the risk epoch")
        if self._circuit_breaker_reason is not None:
            raise RuntimeError(
                "Cannot reset the risk epoch while a circuit breaker is active "
                f"({self._circuit_breaker_reason}); resolve that incident first"
            )
        # Serialize against pb-reconcile and reset_safety_lock: the broker's
        # reconcile holds no lock of its own, and two interleaved runs would
        # race stop restores, order cancels and ledger writes.
        with getattr(self, "_reconcile_lock", threading.Lock()):
            try:
                report = ReconciliationReport.from_result(self.broker.reconcile())
            except Exception as exc:
                self._raise_reconciliation_breaker("reconciliation_failed")
                self._reconcile_failed = True
                self._persist_reconciliation_incident("reconciliation_failed")
                raise RuntimeError(f"Cannot reset the risk epoch before reconciliation succeeds: {exc}") from exc
            if report.has_drift:
                self._raise_reconciliation_breaker("reconciliation_failed")
                self._reconcile_failed = True
                self._persist_reconciliation_incident("reconciliation_failed")
                raise RuntimeError(
                    f"Cannot reset the risk epoch while reconciliation reports drift: {report.drift_categories}"
                )
            self._last_reconciliation_at = datetime.now(UTC)
            self._clear_reconciliation_incidents()
        account = self.broker.account()  # fresh authoritative snapshot
        account_id = self.broker.account_id or "unknown"
        new_baseline = float(account.equity)
        if new_baseline <= 0:
            raise RuntimeError("Cannot reset the risk epoch with non-positive account equity")
        self.broker.reset_peak_equity(new_baseline)
        repository = getattr(self, "_portfolio_repository", None)
        if repository is not None:
            repository.reset_high_water(account_id, new_baseline)
        self.ledger.clear_safety_incident(account_id, "portfolio_drawdown_halt")
        # Re-fetch the account AFTER the rebase: a pre-rebase snapshot would
        # re-evaluate the latch against the old monotonic peak and instantly
        # re-trigger the breach we just cleared.
        rebased = self.broker.account()
        self._persist_risk_state(rebased)
        log.warning(
            "New risk epoch started: drawdown baseline rebased to %.2f (account=%s)",
            new_baseline,
            account_id,
        )
        runtime_metrics_store.record_event("risk_epoch_reset")
        alerts.emit(
            "risk_epoch_reset",
            message="New risk epoch started; drawdown baseline rebased",
            baseline_equity=new_baseline,
            account_id=account_id,
        )
        return new_baseline

    def reset_safety_lock(self) -> None:
        """Acknowledge a recoverable entry lock after broker/data verification.

        Operator reset may INITIATE the verification action for each market
        halt, but may never simply erase a halt flag. Each halt category has
        its own verified recovery condition:

        * ``subscription_state_unknown`` — only a successful authoritative
          stream resync clears it (initiated here if needed);
        * ``invalid_dynamic_universe`` — only a valid scanner refresh (or a
          valid permitted fallback) clears it (initiated here);
        * ``crypto_protection_failure`` — only a verified protection pass
          (the successful broker reconciliation above) clears it;
        * ``ambiguous_crypto_exit`` — only authoritative broker
          reconciliation proving terminal exit state clears it;
        * ``{worker}_restart_limit`` — only a verified stream reconnect
          (worker re-armed and delivering fresh events) clears it;
        * ``rebalance_reconciliation_required`` — only reconciled repository
          order state clears it.

        An unverified halt REMAINS ACTIVE while the global circuit breaker
        clears; the operator is told exactly which halts were retained.
        """
        recoverable = {
            "ambiguous_order_state",
            "protection_failure",
            "protection_mismatch",
            "ledger_mismatch",
            "ownership_drift",
            "post_fill_risk_violation",
            "final_market_event_queue_overflow",
            "reconciliation_failed",
            "reconciliation_bug_suspected",
            "portfolio_runtime_failure",
            "decision_worker_crashed",
            "trade_update_callback_failure",
        }
        reason = self._circuit_breaker_reason
        if reason not in recoverable:
            raise ValueError(f"Safety lock is not operator-resettable: {reason or 'none'}")
        # A reset cannot revive a shared worker that exited (e.g. the decision
        # loop after decision_worker_crashed): clearing the breaker would
        # report "running" while no decision/account/fill processing runs.
        dead_shared = sorted(
            name
            for name, worker in dict(getattr(self, "_workers", {})).items()
            if name in {"pb-decisions", "pb-account", "pb-trade-updates"} and not worker.alive
        )
        if getattr(self, "_running", False) and dead_shared:
            raise ValueError(
                "Safety lock cannot be reset while shared workers are stopped "
                f"({','.join(dead_shared)}); restart the process to recover"
            )
        if not self._events.empty():
            raise ValueError("Market event queue must drain before resetting the safety lock")
        if self.market_state.has_data_gaps or self._not_ready_symbols:
            raise ValueError("All symbol data must be ready before resetting the safety lock")
        if getattr(self, "_resync_pending", False):
            try:
                self._resync_stream_if_needed()
            except Exception as exc:
                raise ValueError(f"Stream membership cannot be reconnected: {exc}") from exc
        with self._reconcile_lock:
            try:
                report = ReconciliationReport.from_result(self.broker.reconcile())
            except Exception as exc:
                self._reconcile_failed = True
                self._persist_reconciliation_incident("reconciliation_failed")
                raise ValueError(f"Safety lock remains active until reconciliation succeeds: {exc}") from exc
            if report.has_drift:
                self._reconcile_failed = True
                self._persist_reconciliation_incident("reconciliation_failed")
                raise ValueError(
                    f"Safety lock remains active while reconciliation reports drift: {report.drift_categories}"
                )
            self._last_reconciliation_at = datetime.now(UTC)
            self._clear_reconciliation_incidents()
            account_id = self.broker.account_id or "unknown"
            entries = self.ledger.managed_entries_for_account(account_id)
            if any(item.status == "manual_intervention_required" for item in entries):
                raise ValueError("A managed position still requires manual intervention")
            account = self.broker.account()
            self._publish_account_snapshot(account)
            with getattr(self, "_state_lock", threading.RLock()):
                # Clear only the reason being reset: a halt another thread
                # raised during the broker calls above set its own reason.
                if self._circuit_breaker_reason == reason:
                    self._circuit_breaker_reason = None
            # The operator verified THIS halt's recovery condition above;
            # clear exactly its incident, never unrelated incidents (those
            # need their own per-halt verification).
            self.ledger.clear_safety_incident(account_id, reason)
            market_halts = getattr(self, "_market_entry_halts", None)
            if market_halts is not None:
                self._verify_market_halts(market_halts)
            with getattr(self, "_risk_state_lock", threading.Lock()):
                self._entry_risk_latch = dict.fromkeys(getattr(self, "_entry_risk_latch", {}))
            self._persist_risk_state(account)
        with getattr(self, "_state_lock", threading.RLock()):
            # A halt raised by another thread since the breaker was cleared
            # above set a new reason: keep that halt. A trade-update blip while
            # a non-halting breaker (protection_failure) was set left the
            # runtime "degraded"; the blip restore never passes a breaker, so
            # un-pin it here too, while the trade-update worker is alive.
            trade_updates = dict(getattr(self, "_workers", {})).get("pb-trade-updates")
            restorable = self._state == "halted" or (
                self._state == "degraded" and trade_updates is not None and trade_updates.alive
            )
            if self._running and restorable and self._circuit_breaker_reason is None:
                self._state = "running"
        log.warning("Operator reset safety lock after successful reconciliation: %s", reason)

    def _verify_market_halts(self, market_halts: dict[Market, str | None]) -> None:
        """Clear each market halt only after its specific verified recovery.

        Runs INSIDE the reconcile lock; the successful ``broker.reconcile()``
        above already verified protection and exit state, so per-halt
        verifications may rely on it. Unverified halts stay active and are
        reported to the operator.

        Every dict access takes the state lock, so each individual
        read-modify-write is atomic against a concurrent halt writer (stream
        worker, universe refresh, crypto protection): none of them can
        overwrite an update in flight. What is NOT guaranteed is atomicity
        across a whole verification — reading a halt and re-reading it after
        the recovery attempt is a pair of locked accesses, so a halt raised
        or replaced in between is left for the next reset instead of being
        reported in ``retained`` here. The lock is never held across the
        verifications themselves, which take the ingestion and portfolio
        locks and would invert the established lock order.
        """
        retained: list[str] = []
        for market in (Market.EQUITY, Market.CRYPTO):
            with getattr(self, "_state_lock", threading.RLock()):
                halt = market_halts.get(market)
            if halt is None:
                continue
            if halt == "subscription_state_unknown":
                # Only a successful authoritative stream resync clears it.
                if not getattr(self, "_resync_pending", False):
                    self._resync_pending = True
                try:
                    self._resync_stream_if_needed()
                except Exception as exc:
                    retained.append(f"{market.value}:{halt}:{exc}")
                with getattr(self, "_state_lock", threading.RLock()):
                    still_halted = market_halts.get(market) == halt
                if still_halted:
                    retained.append(f"{market.value}:{halt}:resync_did_not_clear")
                continue
            if halt == "invalid_dynamic_universe":
                # Only a valid scanner refresh (or a valid permitted
                # fallback) clears it; the refresh itself re-asserts the halt
                # on failure.
                try:
                    self._refresh_runtime_universes(market)
                except Exception as exc:
                    retained.append(f"{market.value}:{halt}:{exc}")
                with getattr(self, "_state_lock", threading.RLock()):
                    still_halted = market_halts.get(market) == halt
                if still_halted:
                    retained.append(f"{market.value}:{halt}:refresh_did_not_clear")
                continue
            if halt == "crypto_protection_failure":
                # The broker reconciliation above is the verified protection
                # pass: it restores managed crypto stops and raises on
                # failure, so a completed reconcile proves the pass.
                with getattr(self, "_state_lock", threading.RLock()):
                    market_halts[market] = None
                continue
            if halt == "ambiguous_crypto_exit":
                # Only authoritative broker reconciliation proving terminal
                # exit state clears it; the reconcile above runs
                # _reconcile_crypto_exit for every in-flight exit and raises
                # on ambiguity. Verify nothing remains unresolved.
                if self._crypto_exits_are_terminal():
                    with getattr(self, "_state_lock", threading.RLock()):
                        market_halts[market] = None
                else:
                    retained.append(f"{market.value}:{halt}:exit_state_not_terminal")
                continue
            if halt.endswith("_restart_limit"):
                # Only a verified stream reconnect clears it: re-arm the
                # worker and require it to be alive and (when the market is
                # live) delivering fresh events.
                if self._restart_stream_worker(market):
                    with getattr(self, "_state_lock", threading.RLock()):
                        market_halts[market] = None
                else:
                    retained.append(f"{market.value}:{halt}:worker_not_delivering")
                continue
            if halt == "rebalance_reconciliation_required":
                if self._rebalance_state_reconciled(market):
                    with getattr(self, "_state_lock", threading.RLock()):
                        market_halts[market] = None
                else:
                    retained.append(f"{market.value}:{halt}:rebalance_state_unresolved")
                continue
            retained.append(f"{market.value}:{halt}:no_verification_path")
        if retained:
            log.warning("Market halts retained pending verification: %s", "; ".join(retained))

    def _crypto_exits_are_terminal(self) -> bool:
        """True when no crypto managed exit remains in an ambiguous state."""
        account_id = self.broker.account_id or "unknown"
        for entry in self.ledger.managed_entries_for_account(account_id):
            if entry.market != Market.CRYPTO:
                continue
            if getattr(entry, "exit_client_order_id", None):
                target_state = str(getattr(entry, "target_state", ""))
                if target_state not in {"target_exit_filled", "emergency_exit_filled"}:
                    return False
        return True

    def _restart_stream_worker(self, market: Market) -> bool:
        """Re-arm a restart-limited stream worker and verify delivery.

        A worker that hit its restart limit has exited; the operator reset
        spawns a fresh worker. The halt clears only when the worker is alive
        and — while the market is live — delivering fresh stream events.
        """
        worker_name = _STREAM_WORKER_BY_MARKET[market]
        worker = self._workers.get(worker_name)
        target = self.stream.run_stock if market == Market.EQUITY else self.stream.run_crypto
        if worker is None or not worker.alive:
            if worker is not None:
                worker.restart_count = 0
                worker.last_error = None
            thread = threading.Thread(
                target=self._run_stream_worker,
                args=(worker_name, target),
                name=worker_name,
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if worker is not None and worker.alive:
                    break
                time.sleep(0.05)
        return self._stream_worker_delivering(market, worker)

    def _stream_worker_delivering(
        self,
        market: Market,
        worker: WorkerHealth | None,
        now: datetime | None = None,
        heartbeat_ttl: float | None = None,
    ) -> bool:
        """Single source of truth for market stream-worker liveness.

        Shared by the runtime health gate, the per-market health panel and
        the verified-reconnect check so they can never disagree.
        """
        if worker is None or not worker.alive:
            return False
        if now is None:
            now = datetime.now(UTC)
        event_required = market == Market.CRYPTO or is_regular_equity_time(now)
        if not event_required:
            return True
        last_event = worker.last_event_at
        if last_event is None:
            return False
        if heartbeat_ttl is None:
            heartbeat_ttl = max(30.0, self.config.account_poll_interval_seconds * 3)
        return (now - last_event).total_seconds() <= max(180.0, heartbeat_ttl)

    def _rebalance_state_reconciled(self, market: Market) -> bool:
        """True when the blocked two-phase rebalance for a market is resolved.

        The broker reconciliation above resolves working orders into terminal
        states; once every order of the blocked decision is terminal the halt
        can clear. The next gated decision then closes the blocked rebalance
        WITHOUT submitting its stale deferred BUYs and plans from a fresh
        target (``PortfolioRuntime._close_settled_blocked_rebalance``); a
        block whose decision cannot be restored stays blocked.
        """
        repository = getattr(self, "_portfolio_repository", None)
        if repository is None:
            return True
        current = repository.current_rebalance_execution(self.broker.account_id or "unknown", market)
        if current is None or current.phase != REBALANCE_PHASE_BLOCKED:
            return True
        orders = repository.orders_for_decision(current.decision_id)
        return bool(orders) and all(is_terminal_order_status(str(order["status"])) for order in orders)

    def _warmup(self) -> None:
        history = AlpacaHistoricalData(
            self.api_key,
            self.secret_key,
            stock_feed=self.app_config.alpaca.stock_historical_feed,
            data_delayed=self.app_config.alpaca.data_delayed,
        )
        self._historical = history
        log.info("Warmup starting for %d symbols", len(self.symbols))
        self._warmup_symbols(history, self.symbols, retry=False)
        self._refresh_readiness()
        if self._has_ready_symbols():
            log.info("Warmup complete")
        else:
            log.warning(
                "Warmup incomplete; runtime remains warming_up symbols=%s",
                ",".join(sorted(self._not_ready_symbols)),
            )

    def _warmup_config_digest(self) -> str:
        """Cache-scope digest for the runtime warmup fetch.

        Gates warm-cache reuse on the identity of everything that determines
        the warmup window: the enabled strategies (name, parameters, signal
        timeframe, minimum history) and the operational aggregation
        timeframes. Any change invalidates the scope and fails closed to a
        full refetch; the head-coverage check inside the incremental fetch
        additionally guards against a shorter window silently standing in
        for a longer one.
        """
        import json as _json

        payload = {
            "timeframes": sorted(self.aggregators),
            "execution_timeframe": self.app_config.runtime.execution_timeframe,
            "market_state_timeframe": self.app_config.runtime.market_state_timeframe,
            # The signal-history adjustment basis is part of the cache
            # identity: switching it must fail the scope closed to a full
            # refetch (rows are additionally keyed by adjustment).
            "signal_adjustment_basis": {"EQUITY": "splits", "CRYPTO": "raw"},
            "strategies": [
                {
                    "name": getattr(runtime.strategy, "name", ""),
                    "timeframe": getattr(runtime.strategy, "timeframe", None),
                    "minimum_history_bars": getattr(runtime.strategy, "minimum_history_bars", 0),
                    "parameters": getattr(runtime.strategy, "parameters", {}),
                }
                for runtime in self._portfolio_runtimes
            ],
        }
        return hashlib.sha256(_json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _warmup_symbols(
        self,
        history: AlpacaHistoricalData,
        symbols: list[str],
        *,
        retry: bool,
        staged_only: bool = False,
    ) -> dict[tuple[str, str], tuple[list[Bar], list[Bar]]] | None:
        """Download warmup history for ``symbols``.

        EQUITY symbols are fetched on TWO explicit bases: RAW seeds the
        execution-side market state (as-traded stream truth), SPLITS seeds
        the portfolio runtimes' strategy signal history (the same basis as
        backtest historical data; bridged across live splits by
        ``data/adjustment.py``). Crypto has no corporate actions: one RAW
        fetch feeds both consumers.

        With ``staged_only`` the bars are returned without seeding: the
        caller (universe hot-swap) commits the membership first and seeds
        afterwards so the new symbols' strategy history is never silently
        dropped. Otherwise bars are seeded into the market state and every
        matching portfolio runtime under the ingestion and portfolio locks.
        """
        staged: dict[tuple[str, str], tuple[list[Bar], list[Bar]]] = {}
        warmup_digest = self._warmup_config_digest()
        groups = (
            (Market.EQUITY, [symbol for symbol in symbols if not is_crypto(symbol)]),
            (Market.CRYPTO, [symbol for symbol in symbols if is_crypto(symbol)]),
        )
        warmup_timeframes = set(self.aggregators)
        for timeframe in warmup_timeframes:
            for market, group in groups:
                strategy_requirements = [
                    runtime.strategy.minimum_history_bars + 5
                    for runtime in self._portfolio_runtimes
                    if runtime.market == market and runtime.strategy.timeframe == timeframe
                ]
                operational = timeframe in {
                    self.app_config.runtime.execution_timeframe,
                    self.app_config.runtime.market_state_timeframe,
                }
                if not strategy_requirements and not operational:
                    continue
                # ``max(2, *[])`` would reduce to ``max(2)`` — a single
                # argument, which Python tries to iterate. Operational
                # timeframes with no strategy requirement still need a
                # baseline window.
                requested_bars = max(2, *strategy_requirements) if strategy_requirements else 2
                for symbol_batch in _symbol_batches(group):
                    try:
                        start = _warmup_start(symbol_batch[0], timeframe, requested_bars)
                        fetcher = getattr(history, "get_bars_batch_incremental", None)

                        def _fetch(
                            adjustment: str,
                            scope: str,
                            batch: list[str] = symbol_batch,
                            window_start: str = start,
                        ) -> dict[str, list[Bar]]:
                            if fetcher is None:
                                # Fakes/tests without the incremental fetcher
                                # use the plain download contract unchanged.
                                return history.get_bars_batch(
                                    batch, timeframe, start=window_start, adjustment=adjustment
                                )
                            result: dict[str, list[Bar]] = fetcher(
                                batch,
                                timeframe,
                                start=window_start,
                                adjustment=adjustment,
                                scope=scope,
                                config_digest=warmup_digest,
                                identity=f"{timeframe}:{requested_bars}",
                            )
                            return result

                        execution_bars = _fetch("raw", "runtime_warmup")
                        signal_bars = (
                            _fetch("splits", "runtime_warmup_signal") if market == Market.EQUITY else execution_bars
                        )
                        for symbol in symbol_batch:
                            raw_tail = execution_bars.get(symbol, [])[-requested_bars:]
                            signal_tail = signal_bars.get(symbol, [])[-requested_bars:]
                            if staged_only:
                                staged[(symbol, timeframe)] = (raw_tail, signal_tail)
                            else:
                                with self._ingestion_lock, self._portfolio_lock:
                                    self.market_state.seed(raw_tail)
                                    for runtime in self._portfolio_runtimes:
                                        # Only the runtime whose strategy reads
                                        # this cadence: an operational pass
                                        # (5m execution) would otherwise call
                                        # seed() with an all-filtered list and
                                        # still bump previous_timestamp.
                                        if runtime.strategy.timeframe == timeframe and runtime.market == market:
                                            runtime.seed(signal_tail)
                            if len(signal_tail) < requested_bars:
                                log.warning(
                                    "Warmup not ready %s %s: %d/%d bars",
                                    symbol,
                                    timeframe,
                                    len(signal_tail),
                                    requested_bars,
                                )
                    except Exception as exc:
                        log.warning(
                            "Warmup%s batch skipped %s symbols=%d: %s",
                            " retry" if retry else "",
                            timeframe,
                            len(symbol_batch),
                            exc,
                        )
        return staged or None


def _symbol_batches(symbols: list[str]) -> tuple[list[str], ...]:
    return tuple(
        symbols[offset : offset + _WARMUP_SYMBOL_BATCH_SIZE]
        for offset in range(0, len(symbols), _WARMUP_SYMBOL_BATCH_SIZE)
    )


def _warmup_start(symbol: str, timeframe: str, bars: int) -> str:
    days = bars_to_calendar_days(symbol, timeframe, bars)
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


__all__ = ["EngineRuntime", "TransientStartupError"]
