from __future__ import annotations

import asyncio
import hashlib
import math
import os
import queue
import sqlite3
import threading
import time
from _thread import LockType
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from enum import Enum
from pathlib import Path
from typing import Any

from requests.exceptions import RequestException

from parayabuck.config import (
    DEFAULT_LIVE_DECISION_DB_PATH,
    Config,
    IntentWatchdogConfig,
    RuntimeMode,
    SignalCrossCheckConfig,
    same_ledger_path,
)
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Market,
    PositionSnapshot,
)
from parayabuck.data import AlpacaBarStream, AlpacaHistoricalData, TimeframeAggregator
from parayabuck.data.adjustment import adequate_coverage, covers_session, split_continuity_breach
from parayabuck.data.alpaca import BatchOutcome
from parayabuck.execution.alpaca import (
    AlpacaBroker,
    AmbiguousOrderState,
    OwnershipDriftError,
    ProtectionReconciliationError,
)
from parayabuck.execution.alpaca_models import (
    TERMINAL_BROKER_ORDER_STATUSES,
    DefiniteOrderRejection,
    EmptyPositionsReadError,
)
from parayabuck.execution.alpaca_reconciliation import (
    _WORKING_BROKER_STATUSES,
    ReconciliationReport,
    ReconciliationResultError,
    startup_reconcile_scope,
)
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream, TradeUpdate
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    PortfolioOrderSide,
    portfolio_execution_policy_from_config,
    portfolio_risk_limits_from_config,
)
from parayabuck.live_safety import (
    LiveOperatorApproval,
    LiveStartupProof,
    StandingLiveAuthorization,
    approval_nonce_consumed,
    config_fingerprint,
    operator_halt_active,
    operator_halt_marker_path,
    record_approval_consumed,
    verify_account_environment,
)
from parayabuck.logger import get_logger
from parayabuck.markets.nyse_calendar import sessions_around
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    NEW_YORK,
    bars_to_calendar_days,
    is_session_open,
    provider_day_bar_is_final,
    session_close_utc,
)
from parayabuck.monitoring import alerts
from parayabuck.monitoring import metrics as runtime_metrics_store
from parayabuck.monitoring import receipt as paper_receipt
from parayabuck.monitoring.health import (  # single liveness source of truth (R4A-2)
    _EVENT_DRIVEN_WORKERS,
    _SIGNAL_ADJUSTMENT_CHECK_SECONDS,
    _SIGNAL_ADJUSTMENT_WORKER,
)
from parayabuck.monitoring.journal import configure_global_journal
from parayabuck.monitoring.premarket import premarket_probe

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
    exit_sell_retry_client_order_id,
    is_exit_sell_retry,
    is_terminal_order_status,
)
from parayabuck.portfolio.signal_cross_check import (
    STATUS_SECONDARY_UNAVAILABLE,
    STATUS_TREND_MISMATCH,
    CrossCheckOutcome,
    SignalCrossCheckCoordinator,
)
from parayabuck.risk.state import RiskState
from parayabuck.runtime_events import _Event, _EventQueue
from parayabuck.runtime_health import RuntimeHealth, WorkerHealth, _portfolio_catch_up_health
from parayabuck.runtime_universe import ActiveProductUniverse
from parayabuck.storage import SQLiteStore
from parayabuck.trading_host import TRADING_HOST_REFUSAL, trading_host_authorized, trading_host_identity
from parayabuck.universe import equity_universe_digest
from parayabuck.utils import is_crypto
from parayabuck.utils.http import is_network_failure

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


class _TransientAccountFetch(RuntimeError):
    """A network-class failure fetching the account snapshot for a decision.

    Internal marker raised by ``_fresh_account_snapshot_lease``'s decision
    call sites so the batch is re-queued and retried instead of tripping the
    ``portfolio_runtime_failure`` halt. Only a network-class cause qualifies:
    a database or domain failure must still fail closed.
    """


class StartupCancelled(RuntimeError):
    """An operator stop request arrived while ``EngineRuntime.start()`` ran.

    Raised at the startup checkpoints immediately before the two steps that
    can submit orders (restart-rebalance resume and startup catch-up). The
    caller already tore the attempt down; no incident is recorded because
    nothing was placed and the engine ends in its stopped state.
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
    return is_network_failure(exc)


# No bare "locked": it substring-matches "blocked" (the broker's
# account-blocked error). Every SQLite lock message contains "database".
_DATABASE_ERROR_TOKENS = ("database", "integrity", "sqlite")


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


# Stream events stored verbatim as an order's row status: the broker states
# reconcile accepts plus the TradingStream fill event names. Anything else
# (``calculated`` aside, which is resolved by its cumulative fill) is left to
# reconciliation instead of pinning a row in a status nothing recognizes.
_STREAM_ORDER_STATUSES = TERMINAL_BROKER_ORDER_STATUSES | _WORKING_BROKER_STATUSES | {"fill", "partial_fill"}
# Bounded retry of a transient SQLite lock while booking a stream fill: the
# booking is one rolled-back-on-failure transaction, so a retry is safe.
_TRADE_UPDATE_LOCK_ATTEMPTS = 3
_TRADE_UPDATE_LOCK_BACKOFF_SECONDS = 0.5


def _is_transient_database_lock(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


_WARMUP_SYMBOL_BATCH_SIZE = 8
_WARMUP_RETRY_SECONDS = 600.0
# Ceiling of the doubling warmup-retry backoff: a symbol whose last bar lags
# its benchmark's is retried at most every 30 minutes, so a recovered provider
# makes the engine ready again within the same session.
_WARMUP_RETRY_MAX_SECONDS = 1800.0
_SIGNAL_ADJUSTMENT_STALE_SLACK_SECONDS = 300.0
# Safety margin added ON TOP of the strategy history window so the batched
# splits fetch always spans the full required signal window (never a short
# tail standing in for a long history).
_SIGNAL_ADJUSTMENT_WINDOW_MARGIN_DAYS = 10
# Stream worker name <-> market. Single source of truth so health
# reporting, halt tracking and worker restart handling cannot disagree.
_STREAM_WORKER_BY_MARKET = {Market.EQUITY: "pb-stocks"}


def _join_started(thread: threading.Thread, timeout: float) -> None:
    """Join ``thread`` only when it was started: joining an unstarted Thread raises (C2-9-3)."""
    if thread.ident is None:
        return
    thread.join(timeout=timeout)


_MARKET_BY_STREAM_WORKER = {worker: market for market, worker in _STREAM_WORKER_BY_MARKET.items()}
# The NYSE schedule owns the session close, so the daily decision bar is
# flushed at the real close (13:00 ET on an early-close day) and never at a
# fixed 16:00 ET.
#
# The session's last one-minute bar lands a few seconds after the close, so
# the session-close flush gets a LONGER grace than an ordinary bucket: a late
# final minute must not invalidate the whole session.
_SESSION_CLOSE_FLUSH_GRACE_SECONDS = 30.0
# Bounded retry for the provider (SIP) daily bar that replaces an incomplete
# stream-built session-close bar. The first attempt is at the session's SIP
# embargo (the earliest moment the provider's bar for that session is final).
_SESSION_CLOSE_FALLBACK_INTERVAL = timedelta(minutes=1)
_SESSION_CLOSE_FALLBACK_ATTEMPTS = 4
# Operator-facing text for inventory in the shared brokerage account that the
# bot does not own: crypto (support was removed) and equity symbols outside the
# strategy universe. ParayaBuck never trades, pins, streams or protects them;
# it excludes them from ownership/drift, alerts once, and the operator must
# handle them manually. They still count toward account gross and slot limits.
_FOREIGN_INVENTORY_MESSAGE = (
    "Foreign inventory in the shared broker account, left untouched by ParayaBuck "
    "(no orders, no stops, no streaming, excluded from ownership reconciliation) — "
    "crypto ({crypto}, crypto support was removed); equity outside the strategy "
    "universe ({equity}). The operator must close unwanted positions manually"
)


# Runtime states that block entries outright; a transient stream-worker
# blip must never overwrite one of them with "degraded".
_ENTRY_BLOCKING_STATES = frozenset({"halted", "error", "stop_failed", "stopping", "stopped"})


# Entry latch passed to the portfolio decision when a stale account snapshot
# is allowed to produce a sell-only decision (runtime.stale_account_sell_only).
STALE_ACCOUNT_SELL_ONLY_LATCH = "account_snapshot_stale_sell_only"


class TradingState(Enum):
    """Order permission summarised from the runtime's existing gate flags.

    ``ACTIVE``: entries and exits are allowed.
    ``REDUCING``: no new entries (reconciliation latch, per-market entry latch
    or halt); risk-reducing exits keep flowing where the path checks only
    entries.
    ``HALTED``: no orders (circuit breaker or warm-up window).

    Derived by ``EngineRuntime.trading_state``; the flags themselves stay the
    source of truth and keep their own recovery flows.
    """

    ACTIVE = "active"
    REDUCING = "reducing"
    HALTED = "halted"


# A shared stream worker (pb-trade-updates) that ran this long before failing
# was healthy: its failure starts a fresh restart budget and backoff, so
# sparse blips over weeks cannot add up to the restart-limit halt. A crash
# loop never runs this long and still hits the limit.
_SHARED_WORKER_HEALTHY_RESET_SECONDS = 600.0
# A market stream worker past its restart limit keeps retrying (auto-recovery)
# but backs off to minutes: a persistent failure at a 16 s cadence churned
# roughly 5,400 websocket handshakes a day. The first retry past the limit
# waits 60 s, doubling to this cap, so an operator reset is never more than
# this long away. While it keeps failing, one "still failing" alert repeats
# every _STREAM_STILL_FAILING_ALERT_SECONDS.
_STREAM_LIMITED_RETRY_MAX_SECONDS = 300.0
_STREAM_STILL_FAILING_ALERT_SECONDS = 3600.0
# Alpaca does not replay trade updates. Open orders with no update for this
# long during the regular session may hide a missed fill: the runtime then
# latches new entries and reconciles (see _maybe_check_trade_feed_freshness).
_TRADE_FEED_STALE_SECONDS = 300.0
# Consecutive failed account fetches (pb-account) before the breaker trips.
_ACCOUNT_FETCH_FAILURE_LIMIT = 5
_ACCOUNT_FETCH_HALT_REASON = "account_fetch_failures"
_RECONCILIATION_INCIDENT_REASONS = frozenset({"reconciliation_failed", "reconciliation_bug_suspected"})
# Operator halt marker seen by a RUNNING engine (Q-8). Checked from the decision
# loop at this interval; the marker is otherwise checked only at start.
_OPERATOR_HALT_REASON = "operator_halt"
_OPERATOR_HALT_CHECK_SECONDS = 10.0
# Halt reasons written to the incident table the moment they are raised (R-1)
# and restored at the next start until an operator-verified reset clears them.
# Kept in step with ``reset_safety_lock``'s recoverable set.
_PERSISTENT_HALT_REASONS = frozenset(
    {
        "ambiguous_order_state",
        "protection_failure",
        "final_market_event_queue_overflow",
        "ownership_drift",
        "reconciliation_failed",
        "reconciliation_bug_suspected",
        "portfolio_runtime_failure",
        "decision_worker_crashed",
        "trade_update_callback_failure",
        _OPERATOR_HALT_REASON,
    }
)
# Halt reasons a fresh process cannot still be in: they describe in-process
# worker or queue state (a crashed decision worker, a trade-update callback
# that raised, an event queue that overflowed) and this start re-arms all
# three. A row left behind by a previous build would otherwise halt the engine
# for a condition nothing can verify any more; a repeat failure raises the
# halt again by itself. They stay operator-resettable while they are live, and
# ``reset_safety_lock`` clears the same set after its own verification.
_RESTART_REARMED_REASONS = frozenset(
    {
        "decision_worker_crashed",
        "final_market_event_queue_overflow",
        "trade_update_callback_failure",
    }
)


# Health and event types are imported from runtime_health / runtime_events (see above)


@dataclass
class _SessionCloseFallback:
    """A session whose stream-built daily bar was withheld as incomplete.

    The provider's SIP daily bar for that session is the replacement input.
    ``retry_at`` starts at the session's SIP embargo — the earliest moment the
    provider's bar for it can be final — and backs off on each failed attempt.
    """

    session: date
    symbols: tuple[str, ...]
    retry_at: datetime
    attempts: int = 0


def _stored_signal_series(runtimes: Any, symbol: str) -> list[Any]:
    """Stored signal bars backing the coverage-hole check (T4).

    The verifier fails closed when the provider omits a stored in-span
    session: without the stored series a mid-window provider hole would
    pass, advance the watermark past the hole, and drop a good stored bar.
    """
    stored: list[Any] = []
    for runtime in runtimes or ():
        series = getattr(getattr(runtime, "history", None), "get", lambda _key: None)(symbol)
        if series:
            stored.extend(series)
    return stored


class _SymbolDriftOrderGate:
    """Refuses portfolio orders for symbols locked by per-symbol drift.

    Installed in front of the broker handed to the portfolio runtime, and only
    when ``runtime.per_symbol_drift_lock`` is on. Every portfolio submit goes
    through ``submit_portfolio_order``, so a locked symbol gets neither a BUY nor
    a SELL from the bot while other symbols submit normally. A refused order
    never reaches the broker; it is raised as ``DefiniteOrderRejection``, which
    the portfolio runtime books as ``rejected`` and keeps running on.
    """

    def __init__(self, broker: Any, is_locked: Callable[[str, Market], bool]) -> None:
        self._broker = broker
        self._is_locked = is_locked

    def __getattr__(self, name: str) -> Any:
        # Everything except order submission is the real broker's.
        return getattr(self._broker, name)

    def submit_portfolio_order(self, order: Any) -> str:
        if self._is_locked(order.symbol, order.market):
            raise DefiniteOrderRejection(
                f"Ownership drift lock on {order.symbol}: order refused locally until the broker "
                "and the ledger agree again; no broker call was made"
            )
        return str(self._broker.submit_portfolio_order(order))


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
        startup_cancel: threading.Event | None = None,
    ) -> None:
        self._validate_execution_mode(config)
        # Set by the operator's stop request while start() is still running.
        self._startup_cancel = startup_cancel if startup_cancel is not None else threading.Event()
        self.api_key = api_key
        self.secret_key = secret_key
        self.app_config = config
        self.config = config.engine
        # Stamped after every completed account-loop pass, failed or not: the
        # watchdog detects a hung loop, while broker outages are handled by the
        # account-fetch breaker. ``_watchdog_started_at`` gives startup a grace.
        self.last_account_loop_pass_at: datetime | None = None
        self._watchdog_started_at: datetime | None = None
        # NYSE session dates already probed this process (premarket probe).
        self._premarket_probed_sessions: set[date] = set()
        self.mode = config.runtime.mode
        active_equities = config.universe.equity.symbols if config.universe.equity.enabled else ()
        self._product_universes_by_market: dict[Market, ActiveProductUniverse] = {}
        if active_equities:
            self._product_universes_by_market[Market.EQUITY] = self._load_product_universe(tuple(active_equities))
        # Live observation membership is driven by the enabled strategy's
        # universe symbols plus managed-position/open-order pins, which are
        # added later by _pin_managed_symbols when managed inventory exists.
        self._strategy_enabled: dict[Market, bool] = {
            Market.EQUITY: bool(config.strategy.equity.enabled and active_equities),
        }
        self.symbols = [
            canonical_symbol(symbol) for symbol in (active_equities if self._strategy_enabled[Market.EQUITY] else ())
        ]
        self._symbols_by_market = {Market.EQUITY: set(self.symbols)}
        self._market_enabled = dict(self._strategy_enabled)
        self._market_entry_halts: dict[Market, str | None] = {Market.EQUITY: None}
        # Inventory in the shared brokerage account the bot does not own —
        # crypto holdings, or equity outside the strategy universe. Classified
        # only so the operator is alerted once at startup; never traded, pinned,
        # protected or counted as ownership drift.
        self._foreign_inventory: tuple[tuple[Market, str], ...] = ()
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
        self._entry_risk_latch: dict[Market, str | None] = {Market.EQUITY: None}
        strategy_specs = []
        selection = config.strategy.equity
        if selection.enabled and active_equities:
            strategy = product_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
            if strategy.market != Market.EQUITY:
                raise ValueError("Configured equity strategy has the wrong market")
            # The strategy owns its signal timeframe; the configured value must
            # match one of its validated timeframes (never silently
            # reinterpreted). product_strategy_from already enforces the
            # product boundary, and validate_strategy_timeframe keeps the
            # invariant explicit. Generic callers may use portfolio_strategy_from.
            validate_strategy_timeframe(strategy, selection.timeframe or strategy.timeframe)
            strategy_specs.append((strategy, tuple(active_equities), selection.maximum_allocation))
        self._strategy_specs = tuple(strategy_specs)
        if not self._strategy_specs:
            raise ValueError("At least one enabled strategy needs an active universe")
        self.strategy_status = "configured:" + ",".join(
            f"{strategy.market.value}/{strategy.name}" for strategy, _symbols, _cap in strategy_specs
        )
        # Only LIVE uses the live endpoint. DISABLED builds the PAPER one (no
        # call is made: start refuses first), so no mode reaches LIVE by accident (S2-5-1).
        self.paper = self.mode != RuntimeMode.LIVE
        database_path = config.runtime.decision_db_path
        self.store = SQLiteStore(database_path)
        self.ledger = ExecutionLedger(database_path, store=self.store)
        self._portfolio_repository = PortfolioStateRepository(database_path, store=self.store)
        # Derive runtime state dir from decision DB parent to avoid split state (peak vs sqlite).
        runtime_state_dir = str(Path(database_path).parent)
        # The running engine's in-memory keys join the log scrub set, so a rotation on
        # disk does not leave the live key unscrubbed in tracebacks and log lines.
        from parayabuck.monitoring.operational_event import register_secret_text

        register_secret_text(api_key)
        register_secret_text(secret_key)
        alerts.set_alert_context(mode=self.mode.value)
        self.broker = AlpacaBroker(
            api_key,
            secret_key,
            paper=self.paper,
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
            portfolio_ownership_resolved=self._resolve_portfolio_ownership,
            bot_tradable_symbols=self._bot_tradable_symbols,
            runtime_state_dir=runtime_state_dir,
        )
        # Attached after construction, as the cost-basis provider below.
        self.broker.portfolio_surplus_cleared = self._clear_portfolio_surplus_alert  # type: ignore[attr-defined]
        # Cost-basis drift check (alpaca_reconciliation._cost_basis_drift) reads
        # this provider. AlpacaBroker takes no constructor argument for it, so
        # it is attached right after construction.
        self.broker.portfolio_owned_cost_bases = lambda symbol, market: self._portfolio_repository.owned_cost_bases(
            self.broker.account_id or "unknown", symbol, market
        )
        if self._per_symbol_drift_lock_enabled():
            # Read by AccountService.account(): shortfall drift is collected
            # into ``drift_locked_symbols`` instead of raised. Off = unchanged.
            self.broker.per_symbol_drift_lock = True
        if getattr(config.risk, "adjust_for_cash_flows", False) is True:
            # Read by AccountService.account(): same-day deposits/withdrawals
            # net out of daily P&L and the peak (F9-2). Off = unchanged.
            self.broker.adjust_for_cash_flows = True
        self._portfolio_runtimes: tuple[PortfolioRuntime, ...] = ()
        self.stream = AlpacaBarStream(api_key, secret_key, config.alpaca.stock_stream_feed)
        # alpaca-py reconnects a dropped trade stream inside its own loop, where
        # the worker supervisor never sees it. Each reconnect latches entries
        # and reconciles, because the updates sent while it was down are not replayed.
        self.trading_updates = AlpacaTradingUpdateStream(
            api_key,
            secret_key,
            self.paper,
            self._on_trade_update,
            on_reconnect=lambda: self._set_reconcile_pending("trade_updates_restart"),
        )
        # The stream's one-minute bars are aggregated ONLY into the strategy
        # signal cadences (the daily decision bar for VT35): nothing else in
        # the engine consumes stream-built candles.
        self._strategy_timeframes = {strategy.timeframe for strategy, _symbols, _cap in strategy_specs}
        self.aggregators = {
            timeframe: TimeframeAggregator(
                timeframe,
                self.config.data_finalization_grace_seconds,
            )
            for timeframe in sorted(self._strategy_timeframes)
        }
        # Set when a session-close daily bar was withheld as incomplete and
        # the provider's SIP daily bar is being fetched to replace it.
        self._session_close_fallback: _SessionCloseFallback | None = None
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
        # Serializes _threads append/iterate/join (R5A-3): a reset-spawned
        # stream worker must never miss stop()'s join.
        self._threads_lock = threading.Lock()
        self._storage_closed = False
        self._state = "stopped"
        self._workers: dict[str, WorkerHealth] = {}
        self._dropped_events = 0
        self._coalesced_events = 0
        self._oldest_received_at: datetime | None = None
        self._event_stats_lock = threading.Lock()
        # Immutable AccountSnapshot shared across a processing cycle. The
        # account loop refreshes it every poll interval; the decision path
        # reuses it while it is fresh (<= maximum_account_age_for_entry_seconds)
        # instead of issuing a second full broker account() fetch per batch.
        self._last_account_snapshot: AccountSnapshot | None = None
        self._last_account_snapshot_at: datetime | None = None
        self._account_snapshot_lock = threading.Lock()
        self._account_snapshot_generation = 0
        self._account_snapshot_active_leases: set[int] = set()
        self._account_snapshot_invalidation_pending = False
        self._last_reconciliation_at: datetime | None = None
        # Last reconcile that came back clean (R9-4): a drift result refreshes
        # _last_reconciliation_at but never this, so health cannot read a drift
        # halt as a fresh reconciliation.
        self._last_clean_reconciliation_at: datetime | None = None
        # Every active halt reason, in raise order. _circuit_breaker_reason is
        # the strongest one and is what callers read; this list keeps the rest
        # so a reset cannot lift a halt that is still in force (R9-3).
        self._halt_reasons: list[str] = []
        # Edge-trigger state for the alerts that fire once per latch transition.
        self._signal_basis_latched = False
        self._event_queue_stale_latched = False
        self._next_operator_halt_check = 0.0
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
        # The process-global journal is the only writer of operational events
        # and the only thing `ops events` reads them back from. Configure it
        # here, before anything can emit, so a configured journal_path /
        # retention / queue size is the one the engine actually uses. Never
        # fatal: an unwritable journal must not stop the engine.
        try:
            configure_global_journal(self.app_config.observability)
        except Exception:
            log.warning("observability journal configuration failed", exc_info=True)
        # Opt-in e-mail alert delivery (alerting.email; disabled by default).
        # Never fatal: a broken mail setup must not stop the engine.
        try:
            from parayabuck.monitoring.email_alerts import configure_email_alerts

            configure_email_alerts(self.app_config.alerting.email, mode=self.mode.value)
        except Exception:
            log.warning("e-mail alert configuration failed", exc_info=True)
        self._circuit_breaker_reason: str | None = None
        self._warming_up = False
        self._not_ready_symbols: set[str] = set(self.symbols)
        self._historical: AlpacaHistoricalData | None = None
        self._managed_pin_symbols: set[str] = set()
        self._last_portfolio_status: dict[Market, str] = {}
        # Observability state: the runtime session row pairs with the
        # ledger's runtime_sessions table so restart counts are persisted.
        self._runtime_session_id: str | None = None
        self._reconcile_failed = False
        # Reconcile-pending latch: gates new entries exactly like
        # _reconcile_failed until a reconcile that started after the latch
        # was set completes cleanly (_reconcile_pending_seq orders the two).
        self._reconcile_pending_reason: str | None = None
        self._reconcile_pending_seq = 0
        self._last_trade_update_at: datetime | None = None
        self._trade_feed_probe_at: datetime | None = None
        self._consecutive_account_failures = 0
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

    def _load_product_universe(self, observation_symbols: tuple[str, ...]) -> ActiveProductUniverse:
        """Bind runtime observation membership to the static equity snapshot."""
        return ActiveProductUniverse.configured(Market.EQUITY, observation_symbols)

    def _expected_live_configuration_digests(self) -> tuple[str, ...]:
        risk_limits = portfolio_risk_limits_from_config(self.app_config.risk)
        execution_policy = portfolio_execution_policy_from_config(self.app_config.execution)
        digests: list[str] = []
        universe_version = equity_universe_digest(self.app_config.universe.equity)
        for strategy, _symbols, maximum_allocation in self._strategy_specs:
            digests.append(
                configuration_digest(
                    strategy,
                    self.mode.value,
                    maximum_allocation,
                    universe_version,
                    risk_limits,
                    execution_policy,
                    self.app_config.risk.drawdown_liquidation,
                )
            )
        return tuple(digests)

    def _expected_broker_account_id(self) -> str | None:
        """``broker.expected_account_id`` pin; None keeps the historical check."""
        broker_config = getattr(getattr(self, "app_config", None), "broker", None)
        return getattr(broker_config, "expected_account_id", None)

    def _verify_live_startup_proof(
        self, expected_digests: tuple[str, ...]
    ) -> LiveOperatorApproval | StandingLiveAuthorization:
        proof = self._live_startup_proof
        self._pending_approval_consumption = None
        if not isinstance(proof, LiveStartupProof) or not proof.is_fresh():
            raise RuntimeError("Live startup proof is missing or expired")
        expected_fingerprint = hashlib.sha256(f"{self.api_key}\0{self.secret_key}".encode()).hexdigest()
        if proof.credential_fingerprint != expected_fingerprint:
            raise RuntimeError("Live startup proof does not match the configured credentials")
        if proof.configuration_digests != expected_digests:
            raise RuntimeError("Live startup proof does not match the loaded configuration")
        # LIVE refuses to run without the second-source cross-check: with it off,
        # no decision is compared against a second source, and an approval given
        # while it was on must not carry over silently (R2-8-1).
        if not getattr(getattr(self.app_config, "signal_cross_check", None), "enabled", False):
            raise RuntimeError("LIVE start refused: signal_cross_check.enabled is false")
        approval: LiveOperatorApproval | StandingLiveAuthorization
        if proof.authorization == "standing":
            # Unattended start (reboot/deploy): the owner's standing LIVE
            # authorization is re-verified here against the loaded config,
            # the cap, this host and the account named in the proof.
            from parayabuck.live_authorization import load_standing_authorization

            approval = load_standing_authorization(
                self.app_config,
                account_id=proof.account_id,
                configuration_digests=expected_digests,
            )
        else:
            secret = os.getenv(self.app_config.live.operator_approval_key_env, "")
            approval_path = self.app_config.live.operator_approval_path
            approval = LiveOperatorApproval.load_verified(
                approval_path,
                secret=secret,
                account_id=proof.account_id,
                configuration_digests=expected_digests,
                config_fingerprint=config_fingerprint(self.app_config),
                trading_host=trading_host_identity(),
            )
            if approval_nonce_consumed(approval_path, approval.nonce):
                raise RuntimeError("Live operator approval was already used for a LIVE start; run authorize-live again")
            # Recorded only once the start has committed (see _start_impl), so a
            # transient failure before that point can be retried with the same approval.
            self._pending_approval_consumption = (approval_path, approval.nonce)
        self.broker.account()
        account_id = str(getattr(self.broker, "account_id", "") or "")
        if not account_id or account_id != proof.account_id:
            raise RuntimeError("Live startup proof does not match the verified broker account")
        # B3: the LIVE key must resolve to a live account (never a paper one).
        verify_account_environment(
            live=True,
            account_number=str(getattr(self.broker, "account_number", "") or ""),
            api_key=self.api_key,
            expected_account_id=self._expected_broker_account_id(),
        )
        return approval

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
        healthy = (
            self._running
            and self._state == "running"
            and alive
            and queue_age <= self.config.maximum_queue_age_seconds
            and account_fresh
            and self._circuit_breaker_reason is None
        )
        state = "degraded" if self._running and not healthy and self._state == "running" else self._state
        market_health = self._build_market_health(market_enabled, symbols_by_market, workers, now, heartbeat_ttl)
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
        # The limits are assigned once in the portfolio runtime's constructor and
        # never rebound, so this read needs no lock (C8-3).
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
                # A malformed (e.g. foreign, symbol-less) order cannot be
                # keyed to a slot; skip it as the decision path's open-order
                # reads do instead of blanking the whole read-only sample.
                continue
            # A foreign crypto order in the shared account still claims a
            # slot; it is only classified here, never managed.
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
        effective_global = len(claims)
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
            "effective_global_slot_claims": effective_global,
            "global_slot_cap": global_cap,
            "global_slots_available": max(0, global_cap - effective_global),
            "working_opening_order_count": len(opening_keys),
            "slot_claims": [
                {"market": market.value, "symbol": symbol, "sources": sorted(sources)}
                for (market, symbol), sources in sorted(claims.items(), key=lambda item: (item[0][0].value, item[0][1]))
            ],
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
                else "pending"
                if self._reconcile_pending()
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
            market_enabled = dict(getattr(self, "_market_enabled", {Market.EQUITY: bool(self.symbols)}))
            symbols_by_market = {
                market: set(symbols)
                for market, symbols in getattr(self, "_symbols_by_market", {Market.EQUITY: set(self.symbols)}).items()
            }
        return workers, market_enabled, symbols_by_market

    def _required_workers(
        self,
        workers: dict[str, WorkerHealth],
        market_enabled: dict[Market, bool],
        symbols_by_market: dict[Market, set[str]],
    ) -> set[str]:
        required = {"pb-decisions", "pb-trade-updates", "pb-account"}
        if market_enabled.get(Market.EQUITY, False) and symbols_by_market.get(Market.EQUITY):
            required.add("pb-stocks")
            if "pb-signal-adjustment" in workers:
                required.add("pb-signal-adjustment")
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
        if name in _EVENT_DRIVEN_WORKERS:
            return True
        if name == _SIGNAL_ADJUSTMENT_WORKER:
            # The monitor heartbeats once per check interval, not per poll.
            ttl = _SIGNAL_ADJUSTMENT_CHECK_SECONDS + max(300.0, heartbeat_ttl)
            return (now - worker.last_heartbeat).total_seconds() <= ttl
        if name in _MARKET_BY_STREAM_WORKER:
            return self._stream_worker_delivering(worker, now, heartbeat_ttl)
        return (now - worker.last_heartbeat).total_seconds() <= heartbeat_ttl

    def _build_market_health(
        self,
        market_enabled: dict[Market, bool],
        symbols_by_market: dict[Market, set[str]],
        workers: dict[str, WorkerHealth],
        now: datetime,
        heartbeat_ttl: float,
    ) -> dict[str, dict[str, object]]:
        portfolio_runtime_by_market = {runtime.market: runtime for runtime in getattr(self, "_portfolio_runtimes", ())}
        market_health: dict[str, dict[str, object]] = {}
        for market, worker_name in _STREAM_WORKER_BY_MARKET.items():
            enabled = market_enabled.get(market, False)
            symbols = symbols_by_market.get(market, set())
            worker = workers.get(worker_name)
            not_ready_symbols = self._not_ready_snapshot()
            market_not_ready = symbols & not_ready_symbols
            ready_symbol_count = len(symbols) - len(market_not_ready)
            ready = not enabled or not symbols or ready_symbol_count > 0
            stream_healthy = not enabled or not symbols or self._stream_worker_delivering(worker, now, heartbeat_ttl)
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
                "entry_halt_reason": getattr(self, "_market_entry_halts", {}).get(market),
                "trading_state": self.trading_state(market).value,
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
        last_clean_reconciliation = getattr(self, "_last_clean_reconciliation_at", None)
        return RuntimeHealth(
            state=state,
            healthy=healthy,
            circuit_breaker_reason=self._circuit_breaker_reason,
            halt_reasons=self._active_halt_reasons(),
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
            last_clean_reconciliation_at=(
                last_clean_reconciliation.isoformat() if last_clean_reconciliation is not None else None
            ),
            entry_risk_latch={
                market.value: reason for market, reason in getattr(self, "_entry_risk_latch", {}).items()
            },
            signal_adjustment=self.signal_adjustment_status(),
            intent_watchdog=self.intent_watchdog_status(),
            signal_cross_check=self.signal_cross_check_status(),
            market_data=self.market_data_status(),
        )

    def _intent_watchdog_config(self) -> IntentWatchdogConfig:
        configured = getattr(getattr(self, "app_config", None), "intent_watchdog", None)
        return configured if isinstance(configured, IntentWatchdogConfig) else IntentWatchdogConfig()

    def intent_watchdog_status(self) -> dict[str, object]:
        """Last intent-watchdog evaluation (copy); empty before the first pass."""
        state = getattr(self, "_intent_watchdog_state", None)
        if not isinstance(state, dict):
            return {
                "enabled": self._intent_watchdog_config().enabled,
                "evaluated_at": None,
                "alarms": [],
                "degraded_reasons": [],
            }
        return {
            **state,
            "alarms": [dict(alarm) for alarm in state.get("alarms", [])],
            "degraded_reasons": list(state.get("degraded_reasons", [])),
        }

    def _maybe_run_intent_watchdog(self) -> None:
        """Rate-limited watchdog pass from the account loop. Never raises."""
        try:
            config = self._intent_watchdog_config()
            now_mono = time.monotonic()
            last = getattr(self, "_intent_watchdog_last_mono", None)
            if last is not None and now_mono - last < config.evaluation_interval_seconds:
                return
            self._intent_watchdog_last_mono = now_mono
            self._intent_watchdog_pass()
        except Exception:
            log.warning("Intent watchdog pass failed", exc_info=True)

    def _maybe_retry_rejected_exit_sells(self, account: AccountSnapshot) -> None:
        """R3: retry a broker-rejected SELL once at the next safe opportunity.

        Safe means: the engine is running and committed, not warming up, no
        circuit breaker, no failed reconciliation and no market entry halt
        (an ambiguous rebalance). The portfolio runtime persists the single
        retry under a deterministic client id, so this never loops. Never
        raises; a submit failure schedules a reconciliation.
        """
        if self._exit_sell_retry_blocked(None):
            return
        for portfolio_runtime in getattr(self, "_portfolio_runtimes", ()):
            if self._exit_sell_retry_blocked(portfolio_runtime.market):
                continue
            retry = getattr(portfolio_runtime, "retry_rejected_exit_sells", None)
            if not callable(retry):
                continue
            # Most passes have no rejected SELL awaiting its retry: decide that
            # from the repository without taking the portfolio lock, so the
            # account loop never queues behind a decision or rebalance.
            if not self._exit_sell_retry_candidate(portfolio_runtime):
                continue
            try:
                with self._portfolio_lock:
                    # The gates above were read before the lock. A halt raised
                    # while this thread waited must still stop the SELL.
                    if self._exit_sell_retry_blocked(portfolio_runtime.market):
                        continue
                    submitted = retry(
                        account,
                        submit_gate=lambda order, market=portfolio_runtime.market: (
                            "exit_sell_retry_blocked" if self._exit_sell_retry_blocked(market) else None
                        ),
                    )
                if submitted:
                    self._invalidate_account_snapshot_after_execution()
            except Exception as exc:
                log.exception("Rejected SELL retry failed: market=%s", portfolio_runtime.market.value)
                # The retry may have submitted some SELLs before it raised: their
                # fills must reach the next account snapshot, so drop the cache.
                self._invalidate_account_snapshot_after_execution()
                alerts.emit(
                    "exit_sell_rejected",
                    message=f"Retry of a rejected SELL could not be submitted: {exc}",
                    market=portfolio_runtime.market.value,
                    state="retry_failed",
                    error=str(exc),
                )
                try:
                    self._schedule_reconcile()
                except Exception:
                    log.warning("Reconcile after a failed SELL retry could not be scheduled", exc_info=True)

    def _advance_submit_block_reason(self, market: Market, order: Any) -> str | None:
        """Why a rebalance advance must not submit ``order`` now, else None.

        Flags only, like ``_exit_sell_retry_blocked``. Stopping and the circuit
        breaker hold every order; a BUY also needs the market to be ACTIVE (no
        halt, entry latch or pending reconcile raised since the advance began)
        and an unverified equity signal basis must not hold it. SELLs keep
        flowing under latches, as on the decision path.
        """
        if not getattr(self, "_running", False):
            return "engine_not_running"
        # An operator stop during startup cancels the attempt; resume and catch-up
        # must not submit after that (C2-9-1). The event is the manager's per-attempt one.
        start_cancel = getattr(self, "_startup_cancel", None)
        if start_cancel is not None and start_cancel.is_set():
            return "startup_cancelled"
        if getattr(self, "_circuit_breaker_reason", None) is not None:
            return "circuit_breaker"
        if self._operator_halt_held():
            return "operator_halt"
        if getattr(order, "side", None) == PortfolioOrderSide.BUY and (
            self.trading_state(market) is not TradingState.ACTIVE or self._buy_held_by_signal_basis(market)
        ):
            return "buy_not_permitted"
        return None

    def _buy_held_by_signal_basis(self, market: Market) -> bool:
        """True while an unverified equity signal basis holds new BUYs.

        Reads the cached verifier state only (no provider call), so it is
        safe under the portfolio lock and at every broker submit.
        """
        return market == Market.EQUITY and self._equity_signal_basis_cached_latch() is not None

    def _reconcile_for_incident_recovery(self) -> Any:
        """Broker reconcile for ``automate_incident_recovery`` on the decision thread.

        The other reconcile callers hold ``_reconcile_lock``; this one runs on
        the decision thread, so it takes the lock too.
        """
        broker = getattr(self, "broker", None)
        if broker is None or not hasattr(broker, "reconcile"):
            return False
        with getattr(self, "_reconcile_lock", threading.Lock()):
            return broker.reconcile()

    def _exit_sell_retry_blocked(self, market: Market | None) -> bool:
        """True when a rejected-SELL retry must not be submitted now.

        Reads flags only (no lock, no broker I/O), so it is safe both before
        and under ``_portfolio_lock``. ``market`` adds that market's entry halt.
        """
        if not getattr(self, "_running", False) or not getattr(self, "_startup_committed", False):
            return True
        if getattr(self, "_warming_up", False) or getattr(self, "_circuit_breaker_reason", None) is not None:
            return True
        if self._operator_halt_held():
            return True
        if getattr(self, "_reconcile_failed", False) or self._reconcile_pending():
            return True
        return market is not None and getattr(self, "_market_entry_halts", {}).get(market) is not None

    def _exit_sell_retry_candidate(self, portfolio_runtime: Any) -> bool:
        """Cheap, lock-free check for a rejected SELL still owed its one retry.

        Mirrors the row filter of ``PortfolioRuntime.retry_rejected_exit_sells``
        and its skip rules that need only the ledger (a symbol with an order
        already open, or with another SELL of the same decision), so the
        portfolio lock is taken only when that method has work to do. A row
        skipped for one of these reasons is never retried, so it is not a
        candidate. A runtime without a repository (test doubles) keeps the
        locked path.
        """
        repository = getattr(portfolio_runtime, "repository", None)
        if repository is None:
            return True
        market = portfolio_runtime.market
        decision_id = repository.latest_decision_id(portfolio_runtime.account_id, market)
        if decision_id is None:
            return False
        sell_rows = repository.orders_for_decision(decision_id, side=PortfolioOrderSide.SELL.value)
        open_symbols = {
            canonical_symbol(order.symbol, order.market)
            for order in repository.open_orders(portfolio_runtime.account_id)
        }
        for row in sell_rows:
            client_order_id = str(row["client_order_id"])
            if not (
                str(row["status"]) == "rejected"
                and not is_exit_sell_retry(client_order_id)
                and not repository.has_order(exit_sell_retry_client_order_id(client_order_id))
            ):
                continue
            if canonical_symbol(str(row["symbol"]), market) in open_symbols:
                continue
            if any(
                str(other["symbol"]) == str(row["symbol"]) and str(other["client_order_id"]) != client_order_id
                for other in sell_rows
            ):
                continue
            return True
        return False

    _STANDING_CHECK_INTERVAL_SECONDS = 3600.0

    def _maybe_check_standing_authorization(self, now: datetime | None = None) -> None:
        """LIVE only, hourly: e-mail when the standing authorization nears or
        reaches its expiry (docs/live_readiness_v1.md B2). Never raises and
        never touches trading: a running engine keeps running; only the next
        unattended restart needs a fresh authorization."""
        try:
            if getattr(self, "mode", None) != RuntimeMode.LIVE:
                return
            now_mono = time.monotonic()
            last = getattr(self, "_standing_check_last_mono", None)
            if now is None and last is not None and now_mono - last < self._STANDING_CHECK_INTERVAL_SECONDS:
                return
            self._standing_check_last_mono = now_mono
            from parayabuck.live_authorization import STANDING_EXPIRED, standing_authorization_status

            current = now or datetime.now(UTC)
            status = standing_authorization_status(self.app_config, now=current)
            authorization = status.authorization
            if authorization is None:
                return
            notices = getattr(self, "_standing_notices", None)
            if notices is None:
                notices = set()
                self._standing_notices: set[str] = notices
            expires = authorization.expires_at.astimezone(UTC).isoformat()
            if status.status == STANDING_EXPIRED:
                key = f"expired:{expires}"
                if key not in notices:
                    notices.add(key)
                    alerts.emit(
                        "live_authorization_expired",
                        message=(
                            "The standing LIVE authorization has expired: after the next restart or deploy "
                            "LIVE waits for a manual re-arm (parayabuck authorize-live)"
                        ),
                        expires_at=expires,
                        state="expired",
                    )
                return
            if status.expiring(current):
                days_left = max(0, math.ceil(authorization.remaining(current).total_seconds() / 86400.0))
                key = f"expiring:{expires}:{days_left}"
                if key not in notices:
                    notices.add(key)
                    alerts.emit(
                        "live_authorization_expiring",
                        message=(
                            f"The standing LIVE authorization expires in {days_left} day(s) ({expires}); "
                            "renew it with parayabuck authorize-live --standing-days N"
                        ),
                        expires_at=expires,
                        state=f"days_left={days_left}",
                    )
        except Exception:
            log.warning("Standing LIVE authorization check failed", exc_info=True)

    def _intent_watchdog_pass(self, now: datetime | None = None) -> list[dict[str, str]]:
        """Compare scheduled intent with the decision DB and account.

        Alarms are edge-triggered: ``intent_alarm`` when a code becomes
        active, ``intent_recovered`` when it clears. Active codes are
        surfaced as degraded reasons in health/readiness; trading gates are
        never touched.
        """
        from parayabuck.monitoring.intent_watchdog import (
            IntentWatchdogSettings,
            LatestDecision,
            evaluate_equity,
        )

        config = self._intent_watchdog_config()
        now = now or datetime.now(UTC)
        previous = getattr(self, "_intent_watchdog_state", None)
        previous_codes = set(previous.get("degraded_reasons", ())) if isinstance(previous, dict) else set()
        if not config.enabled or not getattr(self, "_startup_committed", False):
            self._intent_watchdog_state = {
                "enabled": config.enabled,
                "evaluated_at": now.isoformat(),
                "alarms": [],
                "degraded_reasons": [],
            }
            return []
        settings = IntentWatchdogSettings(
            equity_decision_grace=timedelta(minutes=config.equity_decision_grace_minutes),
            equity_fill_grace=timedelta(minutes=config.equity_fill_grace_minutes),
            minimum_held_fraction=config.minimum_held_fraction,
        )
        account, _, _ = self._account_snapshot_cache_state()
        alarms = []
        for portfolio_runtime in getattr(self, "_portfolio_runtimes", ()):
            raw = portfolio_runtime.repository.latest_decision_target(
                portfolio_runtime.account_id, portfolio_runtime.market
            )
            cross_check_reader = getattr(portfolio_runtime.repository, "latest_decision_cross_check", None)
            cross_check = (
                cross_check_reader(portfolio_runtime.account_id, portfolio_runtime.market)
                if raw is not None and callable(cross_check_reader)
                else None
            )
            gain_band_reader = getattr(portfolio_runtime.repository, "latest_decision_gain_band", None)
            gain_band = (
                gain_band_reader(portfolio_runtime.account_id, portfolio_runtime.market)
                if raw is not None and callable(gain_band_reader)
                else None
            )
            deferral_reader = getattr(portfolio_runtime.repository, "latest_decision_december_deferral", None)
            december_deferral = (
                deferral_reader(portfolio_runtime.account_id, portfolio_runtime.market)
                if raw is not None and callable(deferral_reader)
                else None
            )
            latest = (
                LatestDecision(
                    raw[0],
                    raw[1],
                    cross_check if isinstance(cross_check, dict) else None,
                    gain_band if isinstance(gain_band, tuple) else None,
                    december_deferral if isinstance(december_deferral, tuple) else None,
                )
                if raw is not None
                else None
            )
            alarms.extend(
                evaluate_equity(
                    now,
                    latest,
                    account,
                    running_since=getattr(self, "_intent_watchdog_running_since", None),
                    settings=settings,
                )
            )
        codes = sorted({alarm.code for alarm in alarms})
        for alarm in alarms:
            if alarm.code not in previous_codes:
                log.warning("Intent watchdog alarm %s: %s", alarm.code, alarm.message)
                alerts.emit(
                    "intent_alarm",
                    message=alarm.message,
                    market=alarm.market,
                    category=alarm.code,
                    due_at=alarm.due_at,
                )
        for code in sorted(previous_codes - set(codes)):
            log.info("Intent watchdog recovered: %s", code)
            alerts.emit("intent_recovered", message=f"intent watchdog recovered: {code}", category=code)
        payload = [alarm.to_dict() for alarm in alarms]
        self._intent_watchdog_state = {
            "enabled": True,
            "evaluated_at": now.isoformat(),
            "alarms": payload,
            "degraded_reasons": codes,
        }
        return payload

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

    def market_data_status(self) -> dict[str, object]:
        """Configured vs effective market-data feeds, and the session-close
        fallback pending state. Never raises: an unreadable client reports
        ``available: false``."""
        client = getattr(self, "_historical", None)
        status = getattr(client, "feed_status", None)
        if not callable(status):
            return {"available": False}
        try:
            payload = dict(status())
        except Exception as exc:  # defensive: health must never raise
            log.warning("Market data feed status unavailable: %s", exc)
            return {"available": False}
        payload["available"] = True
        fallback = getattr(self, "_session_close_fallback", None)
        payload["session_close_fallback"] = (
            None
            if fallback is None
            else {
                "session_date": fallback.session.isoformat(),
                "symbols": list(fallback.symbols),
                "attempts": fallback.attempts,
                "retry_at": fallback.retry_at.isoformat(),
            }
        )
        return payload

    def _check_market_data_feed(self) -> None:
        """Alert once when the effective historical feed stops being the
        configured one, and once when it recovers.

        The SIP -> IEX downgrade used to be silent for the life of the
        process: SMA200 and vol history would quietly be adopted from one
        exchange's prints with nothing in health and nothing on the wire.
        """
        status = self.market_data_status()
        if not status.get("available"):
            return
        degraded = bool(status.get("sip_fallback_active")) or status.get("effective") != status.get("configured")
        previous = getattr(self, "_market_data_degraded", None)
        self._market_data_degraded = degraded
        # The first observation is a baseline only when healthy: a downgrade
        # already in effect at startup must alert too.
        if previous == degraded or (previous is None and not degraded):
            return
        if degraded:
            alerts.emit(
                "market_data_degraded",
                message=(
                    "Market data degraded: historical bars are served from "
                    f"{status.get('effective')} instead of the configured {status.get('configured')}. "
                )
                + str(status.get("note") or ""),
                state="degraded",
                configured=status.get("configured"),
                effective=status.get("effective"),
                quote_effective=status.get("quote_effective"),
            )
        else:
            alerts.emit(
                "market_data_degraded",
                message=f"Market data recovered: historical bars are served from {status.get('effective')}.",
                state="recovered",
                configured=status.get("configured"),
                effective=status.get("effective"),
                quote_effective=status.get("quote_effective"),
            )

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
        # A sticky operator halt (OPERATOR_HALT marker) refuses every start,
        # including one reached without the web manager's own gate.
        decision_db_path = getattr(
            getattr(getattr(self, "app_config", None), "runtime", None), "decision_db_path", None
        )
        if decision_db_path:
            halt_marker = operator_halt_marker_path(Path(decision_db_path).parent)
            if operator_halt_active(halt_marker):
                raise RuntimeError("operator halt is in force")
        if self._running:
            raise RuntimeError("Engine runtime is already running")
        self._watchdog_started_at = datetime.now(UTC)
        if self._state == "stop_failed":
            raise RuntimeError("Previous runtime workers did not stop; process restart required")
        if self.mode == RuntimeMode.DISABLED:
            raise RuntimeError("Runtime start refused because runtime.mode is disabled")
        if not trading_host_authorized():
            # Defence in depth behind the web manager: no code path may start
            # an engine on a machine that is not the designated trading host.
            raise RuntimeError(TRADING_HOST_REFUSAL)
        if (
            self.mode == RuntimeMode.PAPER
            and decision_db_path
            and same_ledger_path(decision_db_path, DEFAULT_LIVE_DECISION_DB_PATH)
        ):
            # A PAPER engine on the LIVE ledger would write PAPER decisions into
            # the LIVE books (S2-5-3). Refused before any state is touched.
            raise RuntimeError("PAPER start refused: the decision database is the LIVE ledger")
        if self.mode == RuntimeMode.LIVE:
            if not self.app_config.live.enabled:
                raise RuntimeError("Live runtime requires live.enabled=true")
            if self.app_config.live.hard_dollar_cap <= 0:
                raise RuntimeError("Live runtime requires a positive live.hard_dollar_cap")
            if os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
                raise RuntimeError("Live runtime requires PARAYABUCK_ENABLE_LIVE=YES")
        expected_live_digests: tuple[str, ...] = ()
        live_approval: LiveOperatorApproval | StandingLiveAuthorization | None = None
        if self.mode == RuntimeMode.LIVE:
            expected_live_digests = self._expected_live_configuration_digests()
            live_approval = self._verify_live_startup_proof(expected_live_digests)
        self._stop_event.clear()
        self._state = "starting"
        try:
            with startup_reconcile_scope():
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
            self._last_clean_reconciliation_at = self._last_reconciliation_at
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
        if self.paper:
            # B3: a PAPER engine never runs on a LIVE key. Refused only on
            # unambiguous live evidence; LIVE was verified with its proof.
            verify_account_environment(
                live=False,
                account_number=str(getattr(self.broker, "account_number", "") or ""),
                api_key=str(getattr(self, "api_key", "") or ""),
                expected_account_id=self._expected_broker_account_id(),
            )
        with self._state_lock:
            self._pin_managed_symbols()
        self._warn_foreign_inventory()
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
        universe_version = equity_universe_digest(self.app_config.universe.equity)
        for strategy, symbols, maximum_allocation in self._strategy_specs:
            product_universe = self._product_universes_by_market.get(strategy.market)
            if product_universe is None:
                raise RuntimeError(f"Missing product universe composition for {strategy.market.value}")
            product_universe = product_universe.with_observation_symbols(symbols)
            self._product_universes_by_market[strategy.market] = product_universe
            portfolio_runtimes.append(
                PortfolioRuntime(
                    strategy,
                    symbols,
                    self._portfolio_order_broker(),
                    self._portfolio_repository,
                    mode=self.mode.value,
                    maximum_allocation=maximum_allocation,
                    risk_limits=portfolio_limits,
                    universe=product_universe,
                    universe_version=universe_version,
                    account_id=self.broker.account_id or "unknown",
                    live_dollar_cap=configured_live_cap,
                    execution_policy=portfolio_execution_policy_from_config(self.app_config.execution),
                    sip_embargo=timedelta(minutes=self.app_config.signal_cross_check.sip_embargo_minutes),
                    live_buy_gap_margin=self.app_config.live.buy_gap_margin_pct,
                    drawdown_liquidation=self.app_config.risk.drawdown_liquidation,
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
        self.stream.subscribe(
            stocks,
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
        with self._state_lock:
            for thread in self._threads:
                now = datetime.now(UTC)
                self._workers[thread.name] = WorkerHealth(now)
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
                self._workers["pb-signal-adjustment"] = WorkerHealth(datetime.now(UTC))
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
        # Operator stop wins: from here on every remaining step can submit an
        # order, so a stop request during warmup aborts instead of trading.
        self._raise_if_startup_cancelled()
        # Restart recovery resumes pending rebalances against the one fresh
        # account snapshot.
        self._resume_startup_rebalances(account=account)
        # Seeded warmup history exists and the account snapshot is fresh: a
        # mid-period start must not leave a fresh account in cash until the
        # next cadence boundary (backtest decides on its first usable bar).
        # Normal no-trade/readiness outcomes are result statuses. Unexpected
        # exceptions are invalid composition/safety/provider failures and
        # must not be hidden behind an apparently running engine.
        # Catch-up forces a full-target decision and submits its orders, so
        # re-check between the two order-producing startup steps.
        self._raise_if_startup_cancelled()
        try:
            self._run_startup_catch_up(account=account)
        except StartupCancelled:
            # An operator stop, not a failure: start() runs the cleanup, and the
            # attempt reports stopped rather than error.
            raise
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
        self._intent_watchdog_running_since = datetime.now(UTC)
        pending_consumption = getattr(self, "_pending_approval_consumption", None)
        if pending_consumption is not None:
            record_approval_consumed(*pending_consumption)
            self._pending_approval_consumption = None
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
        if not self.paper:
            # Manual and standing-authorization LIVE starts both pass here; mailed,
            # so an unattended LIVE start is never silent.
            alerts.emit(
                "live_started",
                message="LIVE engine runtime started",
                mode=self.mode.value,
                symbols=len(self.symbols),
            )
        log.info(
            "Engine runtime started: mode=%s symbols=%d timeframe=%s",
            "paper" if self.paper else "live",
            len(self.symbols),
            ",".join(sorted(self.aggregators)),
        )

    def _raise_if_startup_cancelled(self) -> None:
        """Abort startup when the operator pressed Stop while it was running.

        ``stop_engine`` runs on its own worker thread, so a stop issued during
        warmup cannot reach the runtime directly; it signals this event
        instead. Only the order-producing tail of startup checks it, so an
        abort before the first order leaves the account exactly as the stop
        asked for.
        """
        cancel = getattr(self, "_startup_cancel", None)
        if cancel is not None and cancel.is_set():
            raise StartupCancelled("Engine startup was cancelled by a stop request before orders were submitted")

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

    def _resolve_portfolio_ownership(
        self,
        symbol: str,
        market: Market,
        broker_quantity: float,
        broker_average_price: float | None,
    ) -> float | None:
        """Rebase portfolio ownership onto a broker share count that moved
        without a fill of ours (stock split, foreign trade on the shared
        key). See PortfolioStateRepository.reconcile_broker_quantity."""
        resolved: float | None = self._portfolio_repository.reconcile_broker_quantity(
            self.broker.account_id or "unknown",
            symbol,
            market,
            broker_quantity,
            broker_average_price,
        )
        return resolved

    def _clear_portfolio_surplus_alert(self, symbol: str, market: Market) -> None:
        """Forget the unconfirmed-surplus alert of a symbol whose broker count
        agrees with the ledger again (F2-1-6). The account loop calls this only
        when the counts agree, so a self-reconciling symbol re-alerts on its
        next surplus episode instead of staying silent for the process lifetime."""
        repository = getattr(self, "_portfolio_repository", None)
        if repository is None:
            return
        with repository.store.lock:
            repository._resolve_surplus_alerts(self.broker.account_id or "unknown", symbol, market)

    def _bot_tradable_symbols(self) -> set[str]:
        """Equity symbols the bot itself may hold: the strategies' hold
        symbols plus managed pins.

        The brokerage account is shared, so this is the boundary between the
        bot's positions and the operator's. VT35 observes SPY/QQQ but only
        ever holds QLD; an operator SPY/QQQ position is foreign inventory,
        not ownership drift. A strategy that does not name a hold symbol is
        treated as able to trade its whole observed universe (fail-safe:
        more positions stay subject to drift).
        """
        tradable: set[str] = set()
        for strategy, symbols, _cap in getattr(self, "_strategy_specs", ()):
            hold = getattr(strategy, "hold_symbol", None)
            if isinstance(hold, str) and hold.strip():
                tradable.add(canonical_symbol(hold))
            else:
                tradable.update(canonical_symbol(symbol) for symbol in symbols)
        return tradable | set(getattr(self, "_managed_pin_symbols", ()))

    def _warn_foreign_inventory(self) -> None:
        """Alert once at startup for inventory the bot does not own.

        ParayaBuck is equity-only and the brokerage account is shared. Crypto
        positions, and equity positions outside the strategy universe that no
        bot lineage claims, are classified by ``_pin_managed_symbols`` from
        the broker account snapshot and the managed ledger rows. They are
        never pinned, streamed, protected, traded or counted as ownership
        drift; they still count toward account gross and slot limits. Startup
        is not blocked.
        """
        inventory = tuple(getattr(self, "_foreign_inventory", ()))
        if not inventory:
            return
        crypto = [symbol for market, symbol in inventory if market == Market.CRYPTO]
        equity = [symbol for market, symbol in inventory if market != Market.CRYPTO]
        message = _FOREIGN_INVENTORY_MESSAGE.format(
            crypto=", ".join(crypto) or "none", equity=", ".join(equity) or "none"
        )
        log.warning(message)
        alerts.emit(
            "risk_incident",
            message=message,
            reason="foreign_inventory",
            foreign=[{"market": market.value, "symbol": symbol} for market, symbol in inventory],
        )

    def _pin_managed_symbols(self) -> None:
        """Keep managed equity positions observable after config removal.

        Inventory the bot does not own — crypto, and equity outside the
        strategy universe — is classified into ``_foreign_inventory`` for the
        single startup alert and is never pinned.
        """
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
        managed_symbols = {item.canonical_symbol for item in entries} | set(portfolio_symbols)
        tradable = self._bot_tradable_symbols()
        foreign: dict[str, Market] = {}
        for position in broker_positions:
            if position.is_flat:
                continue
            # A crypto-shaped symbol classifies as crypto whatever the asset class.
            market = Market.CRYPTO if is_crypto(position.symbol) else position.market
            symbol = canonical_symbol(position.symbol, market)
            # Crypto is always foreign (this runtime never manages it, whoever
            # a legacy ownership row claims); equity is foreign only when no
            # lineage claims it and the strategy does not trade it.
            if market == Market.CRYPTO or (symbol not in managed_symbols and symbol not in tradable):
                foreign[symbol] = market
        for item in entries:
            if item.market == Market.CRYPTO:
                foreign.setdefault(item.canonical_symbol, Market.CRYPTO)
        self._foreign_inventory = tuple(
            sorted(((market, symbol) for symbol, market in foreign.items()), key=lambda pair: (pair[0].value, pair[1]))
        )
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
        self._managed_pin_symbols = set(equity_pins)
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
        # A market with pins is observed even when its strategy is disabled;
        # health reporting and worker requirements follow the actual
        # observation membership.
        self._market_enabled = getattr(self, "_market_enabled", {})
        for candidate_market, symbols in self._symbols_by_market.items():
            if symbols:
                self._market_enabled[candidate_market] = True
        self.symbols = sorted(self._symbols_by_market[Market.EQUITY])
        self._not_ready_symbols.update(equity_pins)

    def stop(self) -> bool:
        self._startup_cleanup_attempted = True
        alive: list[str] = []
        cleanup_errors: list[str] = []
        storage_errors: list[str] = []
        previous_state = getattr(self, "_state", None)
        breaker_at_stop = getattr(self, "_circuit_breaker_reason", None)
        if getattr(self, "_running", False):
            # Under the state lock: a stream worker restoring "running" after
            # its backoff checks _running under the same lock (CC-11).
            with getattr(self, "_state_lock", threading.RLock()):
                self._state = "stopping"
                self._running = False
            self._stop_event.set()
        try:
            session_id = getattr(self, "_runtime_session_id", None)
            if _observe is not None and (getattr(self, "_startup_committed", False) or session_id is not None):
                _observe.on_runtime_stopping(session_id=session_id, reason="stop")
        except Exception:
            pass
        snapshot_writer = None
        if getattr(self, "_snapshot_writer_started", False) and getattr(self, "_snapshot_writer", None) is not None:
            # The writer stops first so no periodic write runs during the joins.
            # The final snapshot is written once the outcome is known
            # (_publish_stop_snapshot), so a stop that then fails never reads
            # "stopped" (C2-9-4).
            snapshot_writer = self._snapshot_writer
            try:
                snapshot_writer.stop(timeout=1.0)  # type: ignore[union-attr]
            except Exception:
                pass
            self._snapshot_writer_started = False
        try:
            if hasattr(self, "_events") and hasattr(self._events, "put_nowait"):
                self._events.put_nowait(None)
        except queue.Full:
            # The queue never evicts a decision bar: a full queue means the
            # decision loop is wedged, and it already wakes every 0.5 s on
            # timeout and checks _running. Dropping the oldest bar to fit
            # the sentinel would silently lose a session's decision input.
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
        # The spawn locks are held only to take this snapshot. Every spawn site
        # checks _running under its lock and _running was cleared above, so no
        # pb-reconcile / pb-warmup-retry can appear after the snapshot and run
        # broker + ledger I/O after the pools below are closed. The joins run
        # OUTSIDE the locks (CC-5): a reconcile thread that re-enters a spawn
        # site would otherwise block on a lock stop() still holds until the
        # join timed out and stop() reported stop_failed.
        with self._reconcile_spawn_lock, self._warmup_retry_spawn_lock:
            with getattr(self, "_threads_lock", threading.Lock()):
                threads_snapshot = tuple(getattr(self, "_threads", ()))
            reconcile_thread = getattr(self, "_reconcile_thread", None)
            warmup_thread = getattr(self, "_warmup_retry_thread", None)
        for thread in threads_snapshot:
            _join_started(thread, 5)
        alive = [thread.name for thread in threads_snapshot if thread.is_alive()]
        if reconcile_thread is not None:
            _join_started(reconcile_thread, 30)
            if reconcile_thread.is_alive():
                alive.append(reconcile_thread.name)
        if warmup_thread is not None:
            _join_started(warmup_thread, 30)
            if warmup_thread.is_alive():
                alive.append(warmup_thread.name)
        # The signal-adjustment monitor MUST be joined before the historical
        # data client below is closed: a late verification pass would otherwise
        # hit a dead HTTP session mid-request.
        signal_adjustment_thread = getattr(self, "_signal_adjustment_thread", None)
        if signal_adjustment_thread is not None:
            _join_started(signal_adjustment_thread, 10)
            if signal_adjustment_thread.is_alive():
                alive.append(signal_adjustment_thread.name)
        if alive:
            self._state = "stop_failed"
            log.error("Runtime workers did not stop cleanly: %s", ", ".join(alive))
            self._publish_stop_snapshot(snapshot_writer)
            return False
        # Drain the process-global operational journal AFTER workers joined:
        # emit() only enqueues, so a clean stop would otherwise leave queued
        # decision_deferred / circuit_breaker events unwritten. Bounded
        # timeout; a SIGKILL loss window remains documented. Never fatal.
        try:
            from parayabuck.monitoring.journal import get_journal

            get_journal().stop(timeout=2.0)
        except Exception:
            log.warning("Operational journal drain failed", exc_info=True)
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
        # Close the e-mail sink after the last stop alert: intake stops, and the
        # mails already queued are still delivered by its worker.
        try:
            from parayabuck.monitoring.email_alerts import configure_email_alerts

            configure_email_alerts(None)
        except Exception:
            log.warning("E-mail alert sink close failed", exc_info=True)
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
            self._publish_stop_snapshot(snapshot_writer)
            return False
        self._publish_stop_snapshot(snapshot_writer)
        log.info("Engine runtime stopped")
        return True

    def _publish_stop_snapshot(self, writer: Any) -> None:
        """Write the stop outcome (``self._state``) as the final snapshot; no-op without a writer."""
        if writer is None:
            return
        try:
            writer.write_stopped(state=getattr(self, "_state", "stopped"))
        except Exception:
            log.warning("Final stop snapshot could not be written", exc_info=True)

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

    def _stale_account_sell_only_applies(self, readiness_reasons: tuple[str, ...] | list[str]) -> bool:
        """True when the stale-account sell-only path may replace the deferral.

        Only when the operator enabled ``runtime.stale_account_sell_only`` and
        account freshness is the SOLE readiness blocker. Any other reason
        (queue age, runtime state, breaker, ...) keeps the full hold.
        """
        runtime_config = getattr(getattr(self, "app_config", None), "runtime", None)
        if getattr(runtime_config, "stale_account_sell_only", False) is not True:
            return False
        if tuple(readiness_reasons) != ("account_snapshot_stale",):
            return False
        return self.trading_state() is not TradingState.HALTED

    def _stale_account_sell_only_snapshot(self, market: Market) -> AccountSnapshot | None:
        """Last known account snapshot with this market's long positions capped
        at the bot's own owned quantity from the portfolio repository.

        Shorts and positions the bot does not own (external or manual inventory)
        are dropped, so the sell-only decision can never size a SELL above the
        bot's own quantity. Returns None (caller defers) when there is no
        snapshot or the owned quantities cannot be read.
        """
        snapshot = self._account_snapshot_cache_state()[0]
        if snapshot is None:
            return None
        try:
            owned = self._portfolio_repository.owned_quantities(
                getattr(self.broker, "account_id", None) or "unknown", market=market
            )
        except Exception:
            log.warning("Stale-account sell-only skipped: owned quantities unavailable", exc_info=True)
            return None
        positions: list[PositionSnapshot] = []
        for position in snapshot.positions:
            if position.market != market:
                positions.append(position)
                continue
            if position.qty <= 0:
                continue
            owned_qty = max(0.0, float(owned.get(canonical_symbol(position.symbol, market), 0.0)))
            sellable = min(float(position.qty), owned_qty)
            if sellable <= 1e-12:
                continue
            positions.append(
                replace(
                    position,
                    qty=sellable,
                    qty_available=(
                        min(position.qty_available, sellable) if position.qty_available is not None else None
                    ),
                    managed_qty=min(position.managed_qty, sellable),
                )
            )
        return replace(snapshot, positions=tuple(positions))

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
            # No retry here: record the incident category and fail closed for this cycle.
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
        if snapshot is None and snapshot_at is None:
            # Test double without cache state: unvalidated lease.
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
                fetched = False
                try:
                    _, _, fetch_generation = self._account_snapshot_cache_state()
                    account = self.broker.account()
                    fetched = True
                    self._consecutive_account_failures = 0
                    if self._per_symbol_drift_lock_enabled():
                        # Lock newly short symbols and clear locks the broker
                        # now covers. Runs on every successful pass, so an
                        # unlock is decided by the same comparison as the lock.
                        self._sync_symbol_drift_locks(frozenset(getattr(self.broker, "drift_locked_symbols", ())))
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
                            self.broker.account_id or "unknown",
                            account.equity,
                            epoch=getattr(account, "peak_epoch", None),
                        )
                        self._persist_risk_state(account)
                        self._maybe_retry_rejected_exit_sells(account)
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
                    if not fetched:
                        self._note_account_fetch_failure(exc)
                    if isinstance(exc, EmptyPositionsReadError):
                        # Only a reconcile may accept a flat broker while the
                        # ledger owns inventory; it clears the stale ownership.
                        self._set_reconcile_pending("empty_positions_read")
                self._maybe_check_trade_feed_freshness()
                self._maybe_run_intent_watchdog()
                self._maybe_check_standing_authorization()
                # The periodic readiness refresh (warming_up -> running
                # promotion and the bounded warmup retry) runs on this loop:
                # the decision loop only wakes for the daily decision bar.
                if getattr(self, "_startup_committed", False) and not self._warming_up:
                    try:
                        self._refresh_readiness()
                    except Exception as exc:
                        worker.last_error = str(exc)
                        log.warning("Readiness refresh failed: %s", exc, exc_info=True)
                # Read by the web watchdog gate (EngineManager.watchdog_healthy).
                self.last_account_loop_pass_at = datetime.now(UTC)
                self._stop_event.wait(self.config.account_poll_interval_seconds)
        finally:
            worker.alive = False

    def _note_account_fetch_failure(self, exc: BaseException) -> None:
        """Count one failed account fetch; trip the breaker at the limit.

        A broker outage that outlasts the 15 s poll cadence would otherwise
        leave entries running on a stale account snapshot forever. The halt
        goes through ``_raise_halt`` like the other account-loop breakers and
        alerts once, on the transition into the halt.
        """
        failures = int(getattr(self, "_consecutive_account_failures", 0)) + 1
        self._consecutive_account_failures = failures
        if failures < _ACCOUNT_FETCH_FAILURE_LIMIT:
            return
        with getattr(self, "_state_lock", threading.RLock()):
            before = getattr(self, "_circuit_breaker_reason", None)
            self._raise_halt(_ACCOUNT_FETCH_HALT_REASON)
            tripped = (
                before != _ACCOUNT_FETCH_HALT_REASON and self._circuit_breaker_reason == _ACCOUNT_FETCH_HALT_REASON
            )
        if not tripped:
            return
        log.error("Account fetch failed %d times in a row; runtime halted: %s", failures, exc)
        runtime_metrics_store.record_event("circuit_breaker")
        alerts.emit(
            "circuit_breaker",
            message=f"Account fetch failed {failures} times in a row; runtime halted",
            reason=_ACCOUNT_FETCH_HALT_REASON,
            consecutive_failures=failures,
            error=str(exc),
        )

    def _evaluate_entry_risk_latch(
        self,
        account: AccountSnapshot,
        *,
        defer_alerts: list[tuple[str, str, dict[str, object]]] | None = None,
    ) -> None:
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
        breach recovers (e.g. an external deposit) in this process, or when the
        epoch is reset. A restored incident waits for the reset (R2-8-3).
        """
        account_id = self.broker.account_id or "unknown"
        equity = max(1e-9, account.equity)
        # The daily-loss limit is a fraction of the DAY-START equity: measured
        # against current equity a 0.20 limit would only trip at a 16.7% loss.
        day_start_equity = max(1e-9, account.equity - account.daily_pnl)
        daily_loss = max(0.0, -account.daily_pnl / day_start_equity)
        drawdown_breached = account.drawdown_pct >= self.app_config.risk.max_drawdown_pct
        # A drawdown incident restored from a previous process is held until an
        # operator starts a new risk epoch: a limit raised before the restart
        # must not clear it without that reset (R2-8-3).
        drawdown_held = drawdown_breached or bool(getattr(self, "_drawdown_halt_restored", False))
        # Drawdown is evaluated first and on its own: a concurrent daily-loss
        # breach must never clear (and the next poll re-save) the persisted
        # drawdown incident while the drawdown is still past its limit.
        if drawdown_held:
            self.ledger.save_safety_incident(account_id, "portfolio_drawdown_halt")
        else:
            self.ledger.clear_safety_incident(account_id, "portfolio_drawdown_halt")
        breach: str | None = None
        if drawdown_held:
            breach = "portfolio_drawdown_halt"
        elif daily_loss >= self.app_config.risk.daily_loss_limit_pct:
            breach = "daily_loss_limit"
        previous = dict(getattr(self, "_entry_risk_latch", {}))
        new_latch: dict[Market, str | None] = dict.fromkeys(self._entry_risk_latch, breach)
        # Alert only on latch TRANSITIONS (the account loop re-evaluates this
        # every poll); a persistent breach must not re-alert per poll. Compare
        # per market so a future market-specific latch stays correct.
        transitioned = any(new_latch.get(market) != previous.get(market) for market in new_latch)
        self._entry_risk_latch = new_latch
        # With ``defer_alerts`` the caller emits after it leaves _risk_state_lock, so a
        # stalled journal write cannot hold the risk state (and the account loop).
        pending: list[tuple[str, str, dict[str, object]]] = [] if defer_alerts is None else defer_alerts
        if transitioned:
            if breach == "daily_loss_limit":
                runtime_metrics_store.record_event("risk_incident")
                pending.append(
                    (
                        "risk_incident",
                        "Daily loss limit breached; entries latched",
                        {
                            "reason": "daily_loss_limit",
                            "daily_loss_pct": daily_loss,
                            "limit_pct": self.app_config.risk.daily_loss_limit_pct,
                            "equity": equity,
                        },
                    )
                )
            elif breach == "portfolio_drawdown_halt":
                runtime_metrics_store.record_event("drawdown_halt")
                pending.append(
                    (
                        "drawdown_halt",
                        "Drawdown limit breached; entries halted until risk-epoch reset",
                        {
                            "drawdown_pct": account.drawdown_pct,
                            "limit_pct": self.app_config.risk.max_drawdown_pct,
                            "equity": equity,
                        },
                    )
                )
        if defer_alerts is None:
            for kind, message, fields in pending:
                alerts.emit(kind, message=message, **fields)

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
            self._note_halt_reason("ownership_drift")
            self._state = "halted"
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

    def _per_symbol_drift_lock_enabled(self) -> bool:
        """True only when ``runtime.per_symbol_drift_lock`` is literally true."""
        runtime_config = getattr(getattr(self, "app_config", None), "runtime", None)
        return getattr(runtime_config, "per_symbol_drift_lock", False) is True

    def _symbol_drift_lock_map(self) -> dict[str, dict[str, object]]:
        """Per-symbol ownership-drift locks, in memory only (never persisted)."""
        locks = getattr(self, "_symbol_drift_locks", None)
        if locks is None:
            locks = {}
            self._symbol_drift_locks = locks
        return locks

    def _symbol_locked_for_order(self, symbol: str, market: Market) -> bool:
        """True while the symbol carries an ownership-drift lock (order gate)."""
        with getattr(self, "_state_lock", threading.RLock()):
            return canonical_symbol(symbol, market) in self._symbol_drift_lock_map()

    def _portfolio_order_broker(self) -> Any:
        """The broker the portfolio runtimes submit through.

        Flag off returns the broker itself (today's object). Flag on returns the
        order gate in front of it, so a locked symbol is refused at submit.
        """
        if not self._per_symbol_drift_lock_enabled():
            return self.broker
        return _SymbolDriftOrderGate(self.broker, self._symbol_locked_for_order)

    def _sync_symbol_drift_locks(self, drifting: frozenset[str]) -> None:
        """Make the lock set match the symbols the broker reports as short.

        A symbol newly in ``drifting`` is locked: one ``circuit_breaker`` alert
        (reason ``symbol_ownership_drift``) and one reconcile scheduled for the
        batch. A locked symbol no longer in ``drifting`` is unlocked with a log
        line and a recovery alert. The runtime state, the circuit breaker and
        the global entry latch are NOT touched: other symbols keep trading.
        """
        newly_locked: list[str] = []
        unlocked: list[str] = []
        with getattr(self, "_state_lock", threading.RLock()):
            locks = self._symbol_drift_lock_map()
            for symbol in sorted(drifting - set(locks)):
                locks[symbol] = {"first_seen_at": datetime.now(UTC), "reason": "symbol_ownership_drift"}
                newly_locked.append(symbol)
            for symbol in sorted(set(locks) - drifting):
                locks.pop(symbol, None)
                unlocked.append(symbol)
        if newly_locked:
            # Scheduled, not latched: the reconcile-pending latch would hold
            # every symbol's entries, which is what this lock avoids.
            self._schedule_reconcile()
        for symbol in newly_locked:
            log.critical("Ownership drift on %s: that symbol is locked; other symbols keep trading", symbol)
            alerts.emit(
                "circuit_breaker",
                message=f"Ownership drift on {symbol}: symbol locked, reconcile scheduled",
                reason="symbol_ownership_drift",
                symbol=symbol,
            )
        for symbol in unlocked:
            log.warning("Ownership drift on %s cleared: broker covers the owned quantity; symbol unlocked", symbol)
            alerts.emit(
                "reconciliation_recovered",
                message=f"Ownership drift on {symbol} cleared; symbol unlocked",
                reason="symbol_ownership_drift_cleared",
                symbol=symbol,
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
            today = datetime.now(UTC).astimezone(NEW_YORK).date()
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
            self._note_halt_reason(reason)
            current = getattr(self, "_circuit_breaker_reason", None)
            if current is None or current in _RECONCILIATION_INCIDENT_REASONS:
                self._circuit_breaker_reason = reason

    def _raise_halt(self, reason: str) -> None:
        """Halt the runtime for ``reason`` without masking a stronger halt.

        The state always becomes ``halted``. The breaker reason is replaced
        only when none is set or the current one is reconciliation-owned —
        the same rule as ``_raise_reconciliation_breaker``: overwriting
        ownership_drift or protection_failure would hand the operator a reset
        for the wrong incident while the stronger one's row stays persisted.
        The reason is kept in ``_halt_reasons`` either way (R9-3), and a
        persistent reason is written to the incident table before returning
        (R-1), so a crash before the next account pass cannot lose it.
        """
        with getattr(self, "_state_lock", threading.RLock()):
            self._note_halt_reason(reason)
            current = getattr(self, "_circuit_breaker_reason", None)
            if current is None or current in _RECONCILIATION_INCIDENT_REASONS:
                self._circuit_breaker_reason = reason
            self._state = "halted"
        if reason in _PERSISTENT_HALT_REASONS:
            self._save_halt_incident_best_effort(reason)

    def _note_halt_reason(self, reason: str) -> None:
        """Record ``reason`` as active. Caller holds the state lock."""
        halts = list(getattr(self, "_halt_reasons", []))
        if reason not in halts:
            halts.append(reason)
        self._halt_reasons = halts
        # Every raise bumps the reason's sequence number: a reset compares it
        # before and after its broker calls, so a re-raise is never lifted (R2-1-2).
        seq = int(getattr(self, "_halt_seq", 0)) + 1
        self._halt_seq = seq
        raised = dict(getattr(self, "_halt_raised_seq", {}))
        raised[reason] = seq
        self._halt_raised_seq = raised

    def _active_halt_reasons(self) -> tuple[str, ...]:
        """Every halt reason in force, the breaker reason first (R9-3)."""
        primary = getattr(self, "_circuit_breaker_reason", None)
        reasons = [] if primary is None else [primary]
        for item in getattr(self, "_halt_reasons", ()):
            if item not in reasons:
                reasons.append(item)
        return tuple(reasons)

    def _release_halt_reason(self, reason: str) -> None:
        """Lift one halt reason. Caller holds the state lock.

        The breaker moves to the next reason still in force, a non-
        reconciliation one first (as the restore picks it), so a reset of one
        halt leaves the others in force instead of reopening the runtime.
        """
        remaining = [item for item in getattr(self, "_halt_reasons", []) if item != reason]
        self._halt_reasons = remaining
        if getattr(self, "_circuit_breaker_reason", None) == reason:
            ordered = sorted(remaining, key=lambda item: item in _RECONCILIATION_INCIDENT_REASONS)
            self._circuit_breaker_reason = ordered[0] if ordered else None

    def _save_halt_incident_best_effort(self, reason: str) -> None:
        """Write one halt row now. A failure is retried once, then logged and
        alerted, never raised: the halt is already in force in memory, and the
        next successful account pass re-saves every active reason (R2-9-5).

        A reset may release and clear this reason between the in-memory raise
        and the write. The row is then removed again under the state lock, so a
        released incident is not resurrected (R2-9-10).
        """
        account_id = getattr(getattr(self, "broker", None), "account_id", None) or "unknown"
        ledger = getattr(self, "ledger", None)
        save = getattr(ledger, "save_safety_incident", None)
        if not callable(save):
            return
        saved = False
        for attempt in (1, 2):
            try:
                save(account_id, reason)
                saved = True
                break
            except Exception:
                if attempt == 2:
                    log.critical(
                        "Halt %s could not be persisted when raised; it stays in memory until a later save succeeds",
                        reason,
                        exc_info=True,
                    )
        if not saved:
            alerts.emit(
                "circuit_breaker",
                message=f"Halt {reason} could not be written to the incident table; a restart would drop it",
                reason="halt_persist_failed",
                halt_reason=reason,
            )
            return
        with getattr(self, "_state_lock", threading.RLock()):
            if reason in self._active_halt_reasons():
                return
            clear = getattr(ledger, "clear_safety_incident", None)
            if callable(clear):
                clear(account_id, reason)

    def _operator_halt_marker_active(self) -> bool:
        """True while the operator halt marker of this runtime's data dir exists."""
        decision_db_path = getattr(
            getattr(getattr(self, "app_config", None), "runtime", None), "decision_db_path", None
        )
        if not decision_db_path:
            return False
        return operator_halt_active(operator_halt_marker_path(Path(decision_db_path).parent))

    def _operator_halt_held(self) -> bool:
        """Marker check for the order gates (R2-9-4).

        The decision loop polls the marker only every ``_OPERATOR_HALT_CHECK_SECONDS``,
        so the per-order gates read it directly. Fails closed: a marker that
        cannot be checked holds the order.
        """
        try:
            return self._operator_halt_marker_active()
        except Exception:
            log.warning("Operator halt marker could not be checked; holding the order", exc_info=True)
            return True

    def _maybe_check_operator_halt(self) -> None:
        """Halt a RUNNING engine when the operator halt marker appears (Q-8).

        ``parayabuck halt`` used to be honoured only at start. Polled every
        ``_OPERATOR_HALT_CHECK_SECONDS`` from the decision loop; the halt is
        persisted and clears only through ``reset_safety_lock`` once the marker
        has been removed (``parayabuck resume``) and reconciliation passes.
        """
        now = time.monotonic()
        if now < float(getattr(self, "_next_operator_halt_check", 0.0)):
            return
        self._next_operator_halt_check = now + _OPERATOR_HALT_CHECK_SECONDS
        try:
            if not self._operator_halt_marker_active():
                return
        except Exception:
            log.warning("Operator halt marker could not be read", exc_info=True)
            return
        with getattr(self, "_state_lock", threading.RLock()):
            if _OPERATOR_HALT_REASON in self._active_halt_reasons():
                return
        log.critical("Operator halt marker found while the engine runs; halting new decisions")
        self._raise_halt(_OPERATOR_HALT_REASON)
        runtime_metrics_store.record_event("circuit_breaker")
        alerts.emit(
            "circuit_breaker",
            message="Operator halt marker found while the engine runs; runtime halted",
            reason=_OPERATOR_HALT_REASON,
        )

    def _respawn_decision_worker(self) -> bool:
        """Start a fresh pb-decisions thread if the previous one exited (R9-9).

        Called by the operator reset after its verification, so a crashed
        decision loop no longer needs a process restart to come back. Returns
        True when a thread was started.
        """
        if not getattr(self, "_running", False):
            return False
        worker = dict(getattr(self, "_workers", {})).get("pb-decisions")
        if worker is None or worker.alive:
            return False
        worker.last_error = None
        with getattr(self, "_threads_lock", threading.Lock()):
            # Checked under the lock stop() snapshots the threads under.
            if not getattr(self, "_running", False):
                return False
            thread = threading.Thread(target=self._decision_loop, name="pb-decisions", daemon=True)
            self._threads.append(thread)
            thread.start()
        log.warning("Decision worker respawned by the operator reset")
        return True

    def _clear_reconciliation_incidents(self) -> None:
        """Clear only incidents whose documented recovery is clean reconcile.

        Callers that can race ``_persist_risk_state`` hold ``_risk_state_lock``
        (C9-6); the operator reset calls it there after its own checks (R9-6).
        """
        account_id = getattr(self.broker, "account_id", None) or "unknown"
        clear = getattr(getattr(self, "ledger", None), "clear_safety_incident", None)
        if callable(clear):
            for reason in sorted(_RECONCILIATION_INCIDENT_REASONS):
                clear(account_id, reason)
        # Only reconciliation-owned reasons clear here; every other breaker
        # keeps its own verified recovery (reset_safety_lock).
        with getattr(self, "_state_lock", threading.RLock()):
            released = [reason for reason in self._active_halt_reasons() if reason in _RECONCILIATION_INCIDENT_REASONS]
            for reason in released:
                self._release_halt_reason(reason)
            if released and getattr(self, "_circuit_breaker_reason", None) is None:
                # A trade-update blip while this reason was set left the
                # runtime "degraded" (its restore never passes a breaker);
                # nothing else would un-pin it now that the reason is gone.
                # Same running/warming_up rule as that restore, and only
                # while the trade-update worker is alive and the runtime is
                # still running (a stop() in progress keeps its own state).
                trade_updates = dict(getattr(self, "_workers", {})).get("pb-trade-updates")
                if (
                    getattr(self, "_state", None) == "degraded"
                    and getattr(self, "_running", False)
                    and trade_updates is not None
                    and trade_updates.alive
                ):
                    self._state = "running" if self._has_ready_symbols() else "warming_up"

    def _restore_persistent_state(self) -> None:
        """Restore persisted runtime state at startup.

        Persisted state is classified by lifetime semantics:

        * SESSION_SCOPED — the daily-loss/drawdown numbers live in the
          per-session ``risk_state`` row; the latch is re-evaluated from the
          broker account on the first poll, so nothing is restored here.
        * PERSISTENT_INCIDENT — circuit-breaker reasons live in the
          ``safety_incidents`` table and are restored REGARDLESS of the
          session date, so a halt persisted at 23:59 survives a 00:01 restart
          until an operator verifies the recovery.
        * TRANSIENT_READINESS — the entry-risk latch and market entry halts are
          reconstructed from authoritative state at startup and never
          persisted.
        * WATERMARK — the decision watermark (``previous_timestamp`` per
          market) is restored by ``PortfolioRuntime`` itself from the latest
          persisted decision, so a restart never replays a duplicate decision
          or loses the cadence position.

        * RESTART_REARMED — a halt row whose condition a fresh process cannot
          still be in (``_RESTART_REARMED_REASONS``) is migrated, not
          restored: it is cleared with an audit record, because the workers and
          the event queue it described are re-armed by this start.
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
        # entry halt of the same name (pb-stocks) is a
        # different mechanism with their own verified recovery; see
        # ``reset_safety_lock``.
        persistent_reasons = _PERSISTENT_HALT_REASONS
        incidents = self.ledger.load_safety_incidents(account_id)
        # A halt row from a previous process whose condition is process-local
        # cannot still be true here: the trade-update and decision workers are
        # new threads and the final market event queue starts empty. Restoring
        # it would halt a healthy process over a condition only a repeat
        # failure can recreate — and a repeat failure re-raises the halt by
        # itself, exactly as the f"{name}_restart_limit" halts above do. The
        # startup reconciliation that ran before this call verified the broker,
        # so the row is migrated (cleared) with this audit record instead.
        rearmed = sorted({incident.reason for incident in incidents if incident.reason in _RESTART_REARMED_REASONS})
        if rearmed:
            for reason in rearmed:
                self.ledger.clear_safety_incident(account_id, reason)
            log.warning(
                "Persisted halt rows migrated at startup (worker state re-armed): %s",
                ", ".join(rearmed),
            )
            alerts.emit(
                "circuit_breaker",
                message="Persisted halt rows migrated at startup; their worker state was re-armed by this start",
                reason="halt_rows_migrated",
                reasons=rearmed,
            )
        # Newest first, but a reconciliation-owned reason never masks another
        # persisted halt: a clean reconcile would clear it and resume trading.
        restorable = [
            incident.reason
            for incident in incidents
            if incident.reason in persistent_reasons and incident.reason not in _RESTART_REARMED_REASONS
        ]
        preferred = [reason for reason in restorable if reason not in _RECONCILIATION_INCIDENT_REASONS]
        # Every persisted halt stays in force, not only the one restored as the
        # breaker: resetting the first must not lift the others (R9-3).
        self._halt_reasons = list(dict.fromkeys(restorable))
        if preferred or restorable:
            self._circuit_breaker_reason = (preferred or restorable)[0]
        self._drawdown_halt_restored = any(incident.reason == "portfolio_drawdown_halt" for incident in incidents)
        if self._drawdown_halt_restored:
            # A drawdown breach is an operational incident with an explicit
            # recovery (verified risk-epoch reset). Restore the entry latch
            # from the persisted incident so the halt is visible immediately
            # after restart. The account polls keep it latched until the
            # operator starts a new risk epoch (R2-8-3).
            self._entry_risk_latch = dict.fromkeys(self._entry_risk_latch, "portfolio_drawdown_halt")
            runtime_metrics_store.record_event("drawdown_halt")
            alerts.emit(
                "drawdown_halt",
                message="Persisted drawdown halt restored at startup; entries blocked",
                account_id=account_id,
                reason="portfolio_drawdown_halt",
            )

    async def _on_trade_update(self, update: TradeUpdate) -> None:
        try:
            await self._handle_trade_update(update)
        except Exception as exc:
            # The TradingStream callback is the primary source of truth for
            # the ledger. An unhandled exception here can kill the stream
            # worker (or run unobserved inside the SDK loop), degrading the
            # whole runtime. Escalate to a halt instead of failing silently.
            self._raise_halt("trade_update_callback_failure")
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
        cached_basis_only: bool = False,
    ) -> Any:
        """Advance the persisted two-phase rebalance for one market.

        Called from trade-update callbacks, the bar decision loop and engine
        startup. A blocked rebalance (ambiguous SELL outcome) halts market
        entries until a verified reconciliation clears it. ``cached_basis_only``
        (the trade-update path) evaluates the equity basis from cached state
        and never calls the provider; the signal monitor resumes the deferred
        BUY once the basis verifies again.
        """
        # A halt must not be the one moment the engine INCREASES risk: the
        # trade-update path still runs the rebalance's terminal bookkeeping
        # (a SELL fill must complete its phase) but the dependent BUY side
        # stays deferred until the operator's verified reset.
        # The entry-risk latch (daily loss, drawdown) and a market entry halt
        # block new entries everywhere else; a deferred BUY is an entry too.
        # Warmup gates the same way: signal history is half-seeded, so a
        # restart fill arriving mid-warmup must not size the dependent BUY
        # off decision-day reference prices. The post-warmup resume submits it.
        # An unverified equity signal basis (e.g. a split the provider has
        # not yet confirmed) latches entries on the decision and catch-up
        # paths; the same latch gates the deferred BUY here so the fill-driven,
        # startup and reset resumes cannot size it off an unverified basis.
        # Checked last. The trade-update path passes ``cached_basis_only``: a
        # synchronous provider call there would delay every serial fill
        # (T2-7-1), so it reads the cached verifier state only.
        basis_latch = self._equity_signal_basis_cached_latch if cached_basis_only else self._equity_signal_basis_latch
        active = self.trading_state(market) is TradingState.ACTIVE
        basis_held = active and market == Market.EQUITY and basis_latch() is not None
        if basis_held and cached_basis_only:
            # The fill path deferred a BUY on the basis: the signal monitor
            # resumes it once the basis verifies (see _signal_adjustment_monitor_pass).
            self._basis_buy_deferred = True
        submit_buys = active and not basis_held
        for portfolio_runtime in getattr(self, "_portfolio_runtimes", ()):
            if portfolio_runtime.market != market:
                continue
            try:
                with self._portfolio_lock:
                    # The halt, entry-latch and reconcile gates are raised
                    # without this lock, so the read above can be stale by
                    # the time the BUY is submitted: re-read the permission
                    # flags and the signal-basis latch here, under the lock,
                    # right before the advance.
                    submit_now = submit_buys and self.trading_state(market) is TradingState.ACTIVE
                    if submit_now and self._buy_held_by_signal_basis(market):
                        # The basis latched after the read above: hold the BUY
                        # the same way, and let the signal monitor resume it.
                        submit_now = False
                        if cached_basis_only:
                            self._basis_buy_deferred = True
                    advance_kwargs: dict[str, object] = {
                        "submit_deferred_buys": submit_now,
                        # Re-read again at each broker submit: a halt raised by a
                        # thread that does not take this lock must still stop the
                        # next order of the batch (CC-6).
                        "submit_gate": lambda order, market=market: self._advance_submit_block_reason(market, order),
                    }
                    if account is not None:
                        advance_kwargs["account"] = account
                    result = portfolio_runtime.advance_rebalance(**advance_kwargs)
            except Exception as exc:
                # The persisted two-phase state stays pending and every later
                # trade update retries — so the traceback is the only forensic
                # record of a persistent failure.
                log.exception(
                    "Two-phase rebalance advance failed: market=%s",
                    market.value,
                )
                # Not silent: an unadvanced rebalance is otherwise closed as
                # superseded by the next session and its deferred BUYs discarded.
                self._note_rebalance_advance_failure(market, exc)
                continue
            failures = getattr(self, "_rebalance_advance_failures", None)
            if failures and failures.pop(market, None) is not None:
                # The advance works again: lift the latch this failure path set
                # (not one an ambiguous outcome or a reconcile set), so a
                # transient broker error does not need an operator reset.
                with getattr(self, "_state_lock", threading.RLock()):
                    if self._market_entry_halts.get(
                        market
                    ) == "rebalance_reconciliation_required" and market in getattr(
                        self, "_advance_failure_latched", set()
                    ):
                        self._market_entry_halts[market] = None
                        log.warning("Two-phase rebalance advance recovered; %s entry latch lifted", market.value)
                    getattr(self, "_advance_failure_latched", set()).discard(market)
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

    def _note_rebalance_advance_failure(self, market: Market, exc: Exception) -> None:
        """Alert the operator and latch the market pending reconciliation.

        The persisted rebalance is left as it is, so every later advance
        retries it. The entry halt is the reconcile-pending latch: entries
        stay blocked (exits are not) until the next advance succeeds or a
        verified reset re-runs it. The alert throttles per market, and the count of
        consecutive failures is carried in its fields.
        """
        failures: dict[Market, int] | None = getattr(self, "_rebalance_advance_failures", None)
        if failures is None:
            failures = {}
            self._rebalance_advance_failures = failures
        failures[market] = failures.get(market, 0) + 1
        with getattr(self, "_state_lock", threading.RLock()):
            if self._market_entry_halts.get(market) is None:
                latched: set[Market] | None = getattr(self, "_advance_failure_latched", None)
                if latched is None:
                    latched = set()
                    self._advance_failure_latched = latched
                latched.add(market)
            self._market_entry_halts[market] = "rebalance_reconciliation_required"
        try:
            alerts.emit(
                "paper_incident",
                message="Two-phase rebalance advance failed; entries halted pending reconciliation",
                incident_id=f"rebalance_advance_failed:{market.value}",
                category="rebalance_advance_failed",
                market=market.value,
                failures=failures[market],
                error=str(exc),
            )
        except Exception:
            log.warning("Rebalance advance failure alert could not be emitted", exc_info=True)

    def _resume_startup_rebalances(self, *, account: AccountSnapshot) -> None:
        """Resume pending rebalances against one account snapshot."""
        for portfolio_runtime in getattr(self, "_portfolio_runtimes", ()):
            if self._circuit_breaker_reason is not None:
                # A restored persistent incident keeps every pending rebalance
                # deferred until the operator verifies recovery.
                log.warning(
                    "Restart rebalance resume deferred: circuit breaker active (%s)",
                    self._circuit_breaker_reason,
                )
                break
            self._advance_portfolio_rebalance(portfolio_runtime.market, account=account)

    def _calculated_stream_status(self, portfolio_repository: Any, update: TradeUpdate) -> str:
        """Final row status of a stream ``calculated`` event, as reconcile maps it.

        ``calculated`` means done for the day: fully filled reads ``filled``,
        anything less ``done_for_day``. An order that is no longer open keeps
        ``calculated``, which the caller leaves to reconciliation instead of
        rewriting a final row.
        """
        account_id = getattr(self.broker, "account_id", None) or "unknown"
        for order in portfolio_repository.open_orders(account_id):
            if order.client_order_id != update.client_order_id:
                continue
            ordered = float(order.quantity)
            filled = float(update.filled_qty)
            if not math.isfinite(filled):
                return "calculated"
            if ordered <= 0:
                return "filled" if filled > 0 else "done_for_day"
            return "filled" if filled >= ordered - max(1e-9, ordered * 1e-8) else "done_for_day"
        return "calculated"

    async def _apply_trade_update_with_lock_retry(
        self, portfolio_repository: Any, update: TradeUpdate, status: str
    ) -> bool | None:
        """Book one stream update, retrying a transient SQLite lock boundedly.

        Returns the repository's verdict: False when it refused the fill (the
        caller must reconcile it from the broker), None for a double that does
        not report one. Only ``database is locked``-class ``OperationalError``
        is retried; the last attempt's failure (and any other error) propagates
        to the callback's fail-closed halt.
        """
        for attempt in range(_TRADE_UPDATE_LOCK_ATTEMPTS):
            try:
                return await asyncio.to_thread(
                    portfolio_repository.apply_trade_update,
                    client_order_id=update.client_order_id,
                    broker_order_id=update.broker_order_id,
                    status=status,
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
            except Exception as exc:
                if not _is_transient_database_lock(exc) or attempt == _TRADE_UPDATE_LOCK_ATTEMPTS - 1:
                    raise
                log.warning(
                    "Trade update booking for %s hit a database lock (attempt %d/%d); retrying",
                    update.client_order_id,
                    attempt + 1,
                    _TRADE_UPDATE_LOCK_ATTEMPTS,
                )
                await asyncio.sleep(_TRADE_UPDATE_LOCK_BACKOFF_SECONDS * (attempt + 1))
        return None

    async def _handle_trade_update(self, update: TradeUpdate) -> None:
        # Freshness stamp for the stale-feed check, before any early return.
        self._last_trade_update_at = datetime.now(UTC)
        portfolio_repository = getattr(self, "_portfolio_repository", None)
        # Repository reads and writes are synchronous SQLite: they run off the
        # event loop, like the booking below.
        if portfolio_repository is not None and await asyncio.to_thread(
            portfolio_repository.has_order, update.client_order_id
        ):
            status = str(update.event).lower()
            if status == "calculated":
                status = await asyncio.to_thread(self._calculated_stream_status, portfolio_repository, update)
            if status not in _STREAM_ORDER_STATUSES:
                # Unknown vocabulary (restated, order_cancel_rejected, a
                # ``calculated`` for an order no longer open, ...) must neither
                # pin the row open nor mark it terminal: reconciliation reads
                # the broker's authoritative state and books any fill.
                log.warning(
                    "Trade update event %r for %s not booked; reconciliation scheduled",
                    update.event,
                    update.client_order_id,
                )
                self._schedule_reconcile()
                return
            # A terminal or partially filled order changes what the cached
            # account snapshot may claim. Invalidate before the booking as well
            # as after it (below): a decision must not claim the pre-fill
            # snapshot in the window in which the fill is being booked (CC-9).
            if status == "partial_fill" or is_terminal_order_status(status):
                self._invalidate_account_snapshot_after_execution()
            # Book the fill BEFORE the account snapshot: a SELL fill that
            # brings the broker position below the repository-owned quantity
            # would otherwise trip the fail-closed ownership-drift check
            # against the pre-fill owned quantity, aborting the handler and
            # losing the fill.
            applied = await self._apply_trade_update_with_lock_retry(portfolio_repository, update, status)
            if applied is False:
                # The repository refused the fill (for example a cumulative
                # quantity with no average price, BF-1), so nothing was booked.
                # Nothing to advance or persist from it: reconcile recovers the
                # fill from the broker, as the unknown-status branch does.
                log.warning(
                    "Trade update for %s refused by the portfolio ledger; reconciliation scheduled",
                    update.client_order_id,
                )
                self._schedule_reconcile()
                return
            # A rejected portfolio order is an operator-visible event: the
            # dependent rebalance replans smaller (or absent) buys, so the
            # rejection is not silent.
            if update.event == "rejected":
                runtime_metrics_store.record_event("order_rejected")
                # The journal write is synchronous: run it off the event loop so a slow
                # disk cannot stall the trade-update stream.
                await asyncio.to_thread(
                    alerts.emit,
                    "order_rejected",
                    message=f"Portfolio order rejected: {update.client_order_id}",
                    client_order_id=update.client_order_id,
                    symbol=update.symbol,
                    market=Market.EQUITY.value,
                )
                # R3: a rejected SELL exits/reduces a held position; escalate
                # it and let the account loop retry it once.
                try:
                    side = await asyncio.to_thread(portfolio_repository.order_side, update.client_order_id)
                except Exception:
                    side = None
                if side == "sell":
                    from parayabuck.portfolio.runtime_service import emit_exit_sell_rejected_safe

                    await asyncio.to_thread(
                        emit_exit_sell_rejected_safe,
                        client_order_id=update.client_order_id,
                        symbol=update.symbol,
                        market=Market.EQUITY.value,
                        decision_id=None,
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
            # A state-changing fill is booked after the account loop's snapshot,
            # so a reused snapshot is pre-fill. The generation fence cannot tell,
            # because the invalidation above already moved it; see the persist
            # guard below.
            fill_changed_state = status == "partial_fill" or is_terminal_order_status(status)
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
            account: AccountSnapshot | None = None
            try:
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
            except Exception as exc:
                # The fill is ALREADY booked above, so a post-booking account
                # fetch/publication failure is provider noise and must never
                # escalate into a persisted trade_update_callback_failure halt
                # (F10). The 15 s account loop re-verifies ownership drift and
                # re-persists risk state from its next fresh fetch.
                log.warning(
                    "Post-fill account refresh failed for %s (fill already booked, not halting): %s",
                    update.client_order_id,
                    exc,
                )
                await asyncio.to_thread(
                    alerts.emit,
                    "data_provider_unavailable",
                    message="Post-fill account refresh failed; fill booked, runtime not halted",
                    client_order_id=update.client_order_id,
                    error=str(exc),
                )
            if status == "partial_fill" or is_terminal_order_status(status):
                # A terminal order must not release its ledger slot while this
                # cache still proves only the pre-event position state. This
                # also covers a terminal SELL cancellation/rejection: the next
                # decision must reverify the still-held position before
                # reusing its account slot. A fetch that was in flight during
                # the booking is discarded by this bump (CC-9).
                self._invalidate_account_snapshot_after_execution()
            # A snapshot reused across a state-changing fill is pre-fill: it is
            # not persisted, and the account loop re-persists from its next fetch.
            if account is not None and not (cached is not None and fill_changed_state):
                try:
                    await asyncio.to_thread(self._persist_risk_state, account)
                except Exception:
                    # Non-fatal for the same reason: the account loop
                    # re-persists from its next successful poll.
                    log.warning(
                        "Post-fill risk-state persistence failed for %s",
                        update.client_order_id,
                        exc_info=True,
                    )
            # A terminal SELL fill may unlock the dependent BUY phase of a
            # two-phase rebalance: advance it now, event-driven, instead of
            # waiting for the next bar. A legacy crypto order row has no
            # runtime to advance.
            if not is_crypto(update.symbol):
                await asyncio.to_thread(self._advance_portfolio_rebalance, Market.EQUITY, cached_basis_only=True)
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
        # Monotonic time of the last restart-limit alert for a market worker.
        still_failing_since = 0.0
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
                    if name == "pb-trade-updates":
                        # Alpaca does not replay updates missed while the
                        # stream was down: latch entries and reconcile. The
                        # reconcile is best-effort here; the latch holds and
                        # the periodic reconcile retries it.
                        try:
                            self._set_reconcile_pending("trade_updates_restart")
                        except Exception:
                            log.warning("Reconcile after a trade-update restart could not be scheduled", exc_info=True)
                    market = _MARKET_BY_STREAM_WORKER.get(name)
                    # Market workers reset on a verified live event instead
                    # (_mark_market_stream_recovered).
                    if market is None and time.monotonic() - run_started >= _SHARED_WORKER_HEALTHY_RESET_SECONDS:
                        worker.restart_count = 0
                    worker.restart_count += 1
                    degraded_now = False
                    if market is None:
                        with getattr(self, "_state_lock", threading.RLock()):
                            if self._state not in _ENTRY_BLOCKING_STATES:
                                self._state = "degraded"
                                degraded_now = True
                    # Edge-triggered: only the transition into "degraded"
                    # emits, so the journal gets one event per degradation
                    # instead of one per retry.
                    if degraded_now and _observe is not None:
                        _observe.on_runtime_health_degraded(
                            reason=f"{name} worker failed: {exc}",
                            session_id=getattr(self, "_runtime_session_id", None),
                        )
                    if worker.restart_count > 5:
                        if market is None:
                            self._raise_halt(f"{name}_restart_limit")
                            log.exception("%s shared worker restart limit reached", name)
                            runtime_metrics_store.record_event("circuit_breaker")
                            alerts.emit(
                                "circuit_breaker",
                                message=(f"{name} shared worker restart limit reached; runtime halted"),
                                reason=f"{name}_restart_limit",
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
                            still_failing_since = time.monotonic()
                        elif time.monotonic() - still_failing_since >= _STREAM_STILL_FAILING_ALERT_SECONDS:
                            # Periodic reminder while the worker keeps failing past
                            # the limit; the threshold alert above fires only once.
                            log.error("%s stream worker still failing after restart limit", name)
                            alerts.emit(
                                "circuit_breaker",
                                message=(f"{name} stream worker still failing; entries halted"),
                                reason=f"{name}_restart_limit",
                                market=market.value,
                                restart_count=worker.restart_count,
                            )
                            still_failing_since = time.monotonic()
                    delay = min(16.0, 2 ** (worker.restart_count - 1))
                    if market is not None and worker.restart_count > 5:
                        # Past the limit the market worker retries at minutes, not
                        # 16 s: 60 s, doubling, capped (see the constants above).
                        delay = min(_STREAM_LIMITED_RETRY_MAX_SECONDS, 60.0 * 2 ** min(worker.restart_count - 6, 3))
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
                    recovered = False
                    with getattr(self, "_state_lock", threading.RLock()):
                        # _running is checked under the lock stop() clears it
                        # under: a worker that outlives stop() must not set
                        # the runtime back to running (CC-11).
                        if (
                            self._state == "degraded"
                            and getattr(self, "_circuit_breaker_reason", None) is None
                            and self._running
                        ):
                            self._state = "running" if self._has_ready_symbols() else "warming_up"
                            recovered = True
                    # Paired edge-trigger with the degraded emit above: the
                    # un-pin only happens off "degraded", so one recovery
                    # event closes exactly one degradation.
                    if recovered and _observe is not None:
                        _observe.on_runtime_health_recovered(session_id=getattr(self, "_runtime_session_id", None))
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
        await self._on_bar(bar, corrected=False)

    async def _on_updated_minute_bar(self, bar: Bar) -> None:
        await self._on_bar(bar, corrected=True)

    async def _on_bar(self, bar: Bar, *, corrected: bool) -> None:
        """Ingest a live or corrected bar into the aggregation pipeline."""
        if bar.market.value == "equity" and not is_session_open(bar.timestamp):
            return
        worker_name = _STREAM_WORKER_BY_MARKET[bar.market]
        if worker := self._workers.get(worker_name):
            worker.last_heartbeat = datetime.now(UTC)
            worker.last_event_at = datetime.now(UTC)
        if not corrected:
            # Only a live bar proves the stream recovered; a corrected bar
            # does not clear a market halt on its own.
            self._mark_market_stream_recovered(bar.market)
        if corrected:
            # A corrected minute revises the still-open candle it belongs to.
            # A correction of an already-emitted candle is not re-queued: the
            # revised bar is not used for a decision. That drop is logged so it
            # stays visible.
            with self._ingestion_lock:
                for timeframe, aggregator in self.aggregators.items():
                    if aggregator.update_correction(bar) is not None:
                        log.warning(
                            "Corrected minute dropped for an already-emitted bar: symbol=%s timeframe=%s minute=%s",
                            bar.symbol,
                            timeframe,
                            bar.timestamp.isoformat(),
                        )
            return
        with self._ingestion_lock:
            finalized = {timeframe: aggregator.update(bar) for timeframe, aggregator in self.aggregators.items()}
        for timeframe, closed in finalized.items():
            if closed is not None:
                self._emit_finalized_bar(timeframe, closed)

    def _emit_finalized_bar(self, timeframe: str, bar: Bar) -> None:
        """Queue one finalized strategy-cadence bar for the decision loop."""
        if timeframe in self._strategy_timeframes:
            self._put_event(bar)

    def _put_event(self, bar: Bar) -> None:
        event = _Event(bar, datetime.now(UTC))
        result = self._events.put_market_event(event)
        # _put_event runs from several threads (stock stream, decision loop);
        # the diagnostics counters must not be mutated
        # unsynchronized.
        overflowed = False
        with getattr(self, "_event_stats_lock", threading.Lock()):
            if result == "coalesced":
                self._coalesced_events += 1
            elif result == "overflow":
                overflowed = True
                self._dropped_events += 1
            if self._oldest_received_at is None:
                self._oldest_received_at = event.received_at
        if overflowed:
            # The halt takes the state lock only after the stats lock is
            # released: no stats -> state nesting from the stream threads.
            self._raise_halt("final_market_event_queue_overflow")
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
        portfolio_pending: dict[tuple[str, datetime], dict[str, Bar]] = {}
        portfolio_first_seen: dict[tuple[str, datetime], float] = {}
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
                    portfolio_key = (item.bar.timeframe, item.bar.timestamp)
                    portfolio_pending.setdefault(portfolio_key, {})[item.bar.symbol] = item.bar
                    portfolio_first_seen.setdefault(portfolio_key, time.monotonic())
                self._flush_aggregators()
                self._maybe_run_premarket_probe()
                self._maybe_check_operator_halt()
                now_mono = time.monotonic()
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
                self._run_due_deferred_portfolio_batches()
                # Recompute the queue-age diagnostic from both sources under
                # the stats lock so an event enqueued concurrently cannot be
                # missed (which would report a zero age while events wait).
                with self._event_stats_lock:
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
                            for candidate in (self._events.oldest_received_at(), portfolio_oldest)
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
            if isinstance(exc, OwnershipDriftError):
                # Ownership drift is the strongest halt: it always wins.
                reason = "ownership_drift"
                with getattr(self, "_state_lock", threading.RLock()):
                    self._circuit_breaker_reason = reason
                    self._state = "halted"
            else:
                reason = "decision_worker_crashed"
                self._raise_halt(reason)
            log.exception("Decision worker failed")
            runtime_metrics_store.record_event("circuit_breaker")
            alerts.emit(
                "circuit_breaker",
                message="Decision worker failed; runtime halted",
                reason=reason,
                error=str(exc),
            )
        finally:
            worker.alive = False

    def _flush_aggregators(self) -> None:
        now = datetime.now(UTC)
        # Same finalization grace as flush_until, applied to the session-close
        # candles as well: the last minute of a session can arrive just after
        # the close and must still land in them.
        closing = now - timedelta(seconds=self.config.data_finalization_grace_seconds)
        # The NYSE schedule owns the close. A 13:00 ET early close flushes the
        # daily decision bar at 13:00 ET; a holiday never flushes at all.
        session_close = session_close_utc(now.astimezone(NEW_YORK).date())
        close_grace = timedelta(
            seconds=max(self.config.data_finalization_grace_seconds, _SESSION_CLOSE_FLUSH_GRACE_SECONDS)
        )
        incomplete: dict[str, datetime] = {}
        with self._ingestion_lock:
            finalized = {timeframe: aggregator.flush_until(now) for timeframe, aggregator in self.aggregators.items()}
            if session_close is not None and now >= session_close + close_grace:
                for timeframe, aggregator in self.aggregators.items():
                    finalized[timeframe].extend(aggregator.flush_session_close(closing, expected_close=session_close))
                    if timeframe == "1d":
                        incomplete = dict(aggregator.incomplete_session_close)
        for timeframe, bars in finalized.items():
            for bar in bars:
                self._emit_finalized_bar(timeframe, bar)
        self._arm_session_close_fallback(incomplete, session_close)
        self._run_session_close_fallback(now)

    def _arm_session_close_fallback(
        self,
        incomplete: dict[str, datetime],
        session_close: datetime | None,
    ) -> None:
        """Record a session whose stream-built daily bar was withheld.

        A daily bar assembled from a close 1-4 minutes stale is a wrong
        decision input, not a slightly stale one, so the bar is not emitted
        and the provider's SIP daily bar is fetched for that session instead
        (see :meth:`_run_session_close_fallback`).
        """
        if not incomplete or session_close is None:
            return
        if getattr(self, "_session_close_fallback", None) is not None:
            return  # one fallback at a time; the armed one is already alerting
        configured = getattr(getattr(self, "app_config", None), "signal_cross_check", None)
        cross_check = configured if isinstance(configured, SignalCrossCheckConfig) else SignalCrossCheckConfig()
        session = session_close.astimezone(NEW_YORK).date()
        symbols = tuple(sorted(incomplete))
        self._session_close_fallback = _SessionCloseFallback(
            session=session,
            symbols=symbols,
            retry_at=session_close + timedelta(minutes=cross_check.sip_embargo_minutes),
        )
        stale = {symbol: value.astimezone(NEW_YORK).isoformat() for symbol, value in sorted(incomplete.items())}
        log.error(
            "Session-close daily bar incomplete; withheld and replaced by the SIP daily bar: "
            "session=%s symbols=%s newest_minute=%s",
            session.isoformat(),
            ",".join(symbols),
            stale,
        )
        alerts.emit(
            "session_close_bar_incomplete",
            message=(
                f"Equity session {session.isoformat()} closed with missing stream minutes ({','.join(symbols)}); "
                "the incomplete daily bar was withheld and the SIP daily bar is fetched for the decision"
            ),
            market=Market.EQUITY.value,
            session_date=session.isoformat(),
            symbols=list(symbols),
            newest_minute_by_symbol=stale,
        )

    def _run_session_close_fallback(self, now: datetime) -> None:
        """Emit the provider's SIP daily bar for an incomplete session-close.

        Runs OUTSIDE ``_ingestion_lock``: it is a network call and the stream
        must keep ingesting while it waits. The provider's bar for a session
        is final only after the SIP embargo, which is where ``retry_at``
        starts, so a fetched bar is never a partial one. Bounded attempts: a
        session with no decision bar places no order, and the intent watchdog
        owns the alarm if the fallback never lands.
        """
        pending = getattr(self, "_session_close_fallback", None)
        if pending is None or now < pending.retry_at:
            return
        client = getattr(self, "_historical", None)
        if client is None:
            self._session_close_fallback = None
            return
        pending.attempts += 1
        close = session_close_utc(pending.session) or pending.retry_at
        start = datetime(pending.session.year, pending.session.month, pending.session.day, tzinfo=NEW_YORK)
        try:
            bars = client.get_session_daily_bars(list(pending.symbols), start, close, feed="sip", adjustment="splits")
        except Exception as exc:
            log.warning(
                "SIP daily-bar fallback for session %s failed (attempt %d): %s",
                pending.session.isoformat(),
                pending.attempts,
                exc,
            )
            bars = {}
        usable = {
            symbol: bar
            for symbol, bar in bars.items()
            if symbol in pending.symbols
            and bar is not None
            and bar.timestamp.astimezone(NEW_YORK).date() == pending.session
        }
        for bar in sorted(usable.values(), key=lambda item: (item.timestamp, item.symbol)):
            self._emit_finalized_bar("1d", bar)
        if usable:
            log.warning(
                "Session-close daily bar replaced by the SIP daily bar: session=%s symbols=%s",
                pending.session.isoformat(),
                ",".join(sorted(usable)),
            )
            remaining = tuple(symbol for symbol in pending.symbols if symbol not in usable)
            if not remaining:
                self._session_close_fallback = None
                return
            # Partial provider response: keep the fallback armed for the
            # missing symbols so their decision input is still replaced.
            # The attempt budget bounds retries; the next pass re-fetches
            # only what is still missing.
            pending.symbols = remaining
            pending.retry_at = now + _SESSION_CLOSE_FALLBACK_INTERVAL
            return
        if pending.attempts >= _SESSION_CLOSE_FALLBACK_ATTEMPTS:
            log.error(
                "SIP daily-bar fallback exhausted for session %s: no equity decision this session",
                pending.session.isoformat(),
            )
            self._session_close_fallback = None
            return
        pending.retry_at = now + _SESSION_CLOSE_FALLBACK_INTERVAL

    def _decision_retry_settings(self) -> tuple[timedelta, timedelta]:
        """(retry interval, retry window) for deferred session-close decisions."""
        config = getattr(self, "config", None)
        return (
            timedelta(seconds=float(getattr(config, "decision_retry_interval_seconds", 60.0))),
            timedelta(minutes=float(getattr(config, "decision_retry_window_minutes", 40.0))),
        )

    @staticmethod
    def _decision_session_close(session: date) -> datetime:
        """The NYSE close of the session a decision bar belongs to.

        The same calendar the session-close flush and the intent watchdog use,
        so an early close (13:00 ET) really is the deadline the retry window
        is measured from. A date with no session (or a calendar that cannot
        load) falls back to the regular 16:00 ET close, which is the deadline
        the pre-calendar code used — a degraded calendar must not cancel the
        retry.
        """
        close = session_close_utc(session)
        if close is not None:
            return close
        return datetime.combine(session, clock_time(16, 0), tzinfo=NEW_YORK).astimezone(UTC)

    def _defer_portfolio_batch(self, bars: list[Bar], market: Market, reason: str) -> bool:
        """Re-queue a session-close decision batch for a bounded retry.

        A RECOVERABLE gate at decision time (an active reconciliation-owned
        breaker, a stale account snapshot, a snapshot lease invalidated by a
        concurrent fill, a transient broker fetch failure) must not cost the
        session: the batch is parked in ``_deferred_portfolio_batches`` and
        re-decided on the next decision-loop pass, until
        ``decision_retry_window_minutes`` after the SESSION's close. The NYSE
        schedule owns that close, so an early-close day gets 13:00 ET + the
        window (13:40 ET), not 16:40 ET on a session that ended three hours
        earlier — which would have kept retrying into the next session. That
        bound still sits inside the intent watchdog's decision grace and
        still covers the SIP embargo + cross-check retry window the deferral
        has to survive, so a deferred decision either lands or the watchdog
        still reports it missing.

        Returns True when the batch was re-queued; False once the window has
        expired (the session is then genuinely lost and the operator alert
        plus the watchdog's ``equity_decision_missing`` are the record).
        """
        if not bars:
            return False
        now = datetime.now(UTC)
        retry_interval, retry_window = self._decision_retry_settings()
        decision_timestamp = max(bar.timestamp for bar in bars)
        session = decision_timestamp.astimezone(NEW_YORK).date()
        session_close = self._decision_session_close(session)
        deadline = session_close + retry_window
        if now >= deadline:
            log.error(
                "Decision retry window expired; session NOT decided: market=%s session=%s reason=%s "
                "session_close=%s window_ended=%s",
                market.value,
                decision_timestamp.isoformat(),
                reason,
                session_close.isoformat(),
                deadline.isoformat(),
            )
            alerts.emit(
                "decision_deferred",
                message="Session-close decision retry window expired; session not decided",
                market=market.value,
                decision_timestamp=decision_timestamp.isoformat(),
                reason=reason,
                retry_window_ended=deadline.isoformat(),
            )
            return False
        retry_at = min(now + retry_interval, deadline)
        deferred = getattr(self, "_deferred_portfolio_batches", None)
        if not isinstance(deferred, dict):
            deferred = {}
            self._deferred_portfolio_batches = deferred
        key = (market, decision_timestamp)
        first_attempt = key not in deferred
        deferred[key] = (list(bars), retry_at)
        log.info(
            "Decision deferred for retry: market=%s session=%s reason=%s retry_at=%s",
            market.value,
            decision_timestamp.isoformat(),
            reason,
            retry_at.isoformat(),
        )
        if first_attempt:
            # One alert per deferred session, never one per 60 s retry.
            runtime_metrics_store.record_event("decision_deferred")
            alerts.emit(
                "decision_deferred",
                message="Session-close decision deferred by a recoverable gate; retrying",
                market=market.value,
                decision_timestamp=decision_timestamp.isoformat(),
                reason=reason,
                retry_at=retry_at.isoformat(),
            )
        return True

    def _soft_latch_keeps_waiting(self, bars: list[Bar], now: datetime | None = None) -> bool:
        """True while a soft reconcile-pending latch should still park the batch.

        The batch waits like any recoverable gate until two retry intervals
        before the session's retry deadline; after that it is decided with
        its BUYs held by the submit gate, so a latch that outlives the window
        never costs the session's exits (P2-10-1).
        """
        if not bars:
            return False
        now = now or datetime.now(UTC)
        retry_interval, retry_window = self._decision_retry_settings()
        session = max(bar.timestamp for bar in bars).astimezone(NEW_YORK).date()
        last_chance = self._decision_session_close(session) + retry_window - 2 * retry_interval
        return now < last_chance

    def _park_held_session(self, portfolio_runtime: Any, bars: list[Bar], status: str) -> bool:
        """Re-queue a session an open rebalance held without deciding it (P2-2-4, P2-7-1).

        ``rebalance_in_progress`` and ``rebalance_blocked`` leave the session
        due; so does ``rebalance_complete`` from an advance that finished the
        rebalance on this pass (its fill postdates the snapshot, so the batch
        is planned on the next pass from a fresh account). A session that was
        decided on this pass is never parked. True when re-queued.
        """
        if status not in _HELD_SESSION_STATUSES or not bars:
            return False
        decided = getattr(portfolio_runtime, "_session_decided", None)
        if callable(decided) and decided(max(bar.timestamp for bar in bars)):
            return False
        return self._defer_portfolio_batch(bars, portfolio_runtime.market, f"rebalance_held:{status}")

    @staticmethod
    def _release_seeded_watermark(portfolio_runtime: Any, bars: list[Bar]) -> None:
        """Let a parked catch-up batch retry as a cadence boundary.

        A never-decided runtime's high-water is the seeded newest bar, so the
        parked session would not be a period start on its retry and would
        close as ``not_scheduled`` with no order. Clearing the high-water lets
        the retry decide it through the ordinary scheduled path.
        """
        if bars and getattr(portfolio_runtime, "previous_timestamp", None) == max(bar.timestamp for bar in bars):
            portfolio_runtime.previous_timestamp = None

    def _defer_portfolio_bars(self, bars: list[Bar], reason: str) -> bool:
        """Re-queue each market's share of a session-close batch.

        True when at least one share was re-queued; False when none was, or
        every window has already expired (each expiry alerts in
        ``_defer_portfolio_batch``).
        """
        deferred_any = False
        for portfolio_runtime in getattr(self, "_portfolio_runtimes", ()):
            relevant = [
                bar
                for bar in bars
                if bar.market == portfolio_runtime.market
                and bar.timeframe == portfolio_runtime.strategy.timeframe
                and bar.symbol in portfolio_runtime.symbols
            ]
            if relevant and self._defer_portfolio_batch(relevant, portfolio_runtime.market, reason):
                deferred_any = True
        return deferred_any

    def _account_at_decision_close(self, account: AccountSnapshot, bars: list[Bar]) -> AccountSnapshot:
        """Re-mark a broker snapshot at the decision-close prices (F36).

        A deferred session-close decision (the SIP cross-check retry window,
        or a re-queued gate) fetches its account 15-25 min after the close,
        while every price the decision is computed from — signal closes,
        context prices, order reference prices — is the 16:00 ET close.
        Sizing against the later mark makes an after-hours dip look like
        drift and plans a trim the strategy never asked for. Positions
        outside the batch (foreign inventory) keep their broker mark, and a
        re-mark that would produce a non-positive equity is discarded.
        """
        closes = {canonical_symbol(bar.symbol, bar.market): bar.close for bar in bars if bar.close > 0}
        if not closes:
            return account
        positions = tuple(
            replace(position, current_price=closes[canonical_symbol(position.symbol, position.market)])
            if canonical_symbol(position.symbol, position.market) in closes and not position.is_flat
            else position
            for position in account.positions
        )
        equity = account.cash + sum(position.qty * position.current_price for position in positions)
        if equity <= 0:
            return account
        delta = equity - account.equity
        return replace(
            account,
            positions=positions,
            equity=equity,
            peak_equity=max(account.peak_equity, equity),
            daily_pnl=account.daily_pnl + delta,
            unrealized_pnl=account.unrealized_pnl + delta,
        )

    def _process_portfolio_timestamp(
        self,
        bars: list[Bar],
        *,
        health: RuntimeHealth | None = None,
        deferred: bool = False,
    ) -> bool:
        """Decide one closed batch.

        Returns False when the batch was re-queued for a later attempt: the
        session is not decided yet and the caller must keep the deferred key
        alive. True means the batch is finished with — decided, terminally
        skipped, halted, or dropped because the retry window expired.

        ``deferred=True`` marks a re-run of an already-parked batch, whose
        account is fetched after the close: it is re-marked at the
        decision-close prices so the decision is never sized against
        after-hours marks.
        """
        if self._warming_up:
            # A session-close batch that arrives during warmup is still a
            # session: re-queue it for the bounded retry instead of dropping it
            # (R8-7). The retry runs once warmup has finished and expires with
            # the same alert as every other deferral.
            log.debug("Portfolio batch deferred while warming up: bars=%d", len(bars))
            return not self._defer_portfolio_bars(bars, "warming_up")
        breaker = getattr(self, "_circuit_breaker_reason", None)
        if breaker is not None and breaker not in _RECONCILIATION_INCIDENT_REASONS:
            # A hard halt (ownership drift, portfolio runtime failure, a
            # crashed worker, ...) needs an operator-verified reset, which can
            # arrive at any time before the session's retry window closes. The
            # batch is re-queued for that window like any gated session, so a
            # reset inside the window still decides it (R9-2 / B-1); an expired
            # window alerts through _defer_portfolio_batch.
            log.warning(
                "Portfolio batch deferred: runtime halted (%s); bars=%d",
                breaker,
                len(bars),
            )
            return not self._defer_portfolio_bars(bars, breaker)
        if breaker is not None or getattr(self, "_reconcile_failed", False):
            # A reconciliation-owned gate clears on the next clean reconcile:
            # re-queue the session for the bounded retry. The soft
            # reconcile-pending latch does not park the session: the decision
            # runs, its planned SELLs go out, and every BUY is held by the
            # submit gate until the latch clears (see the decision loop below).
            reason = breaker or "reconciliation_failed"
            return not self._defer_portfolio_bars(bars, str(reason or "reconciliation_pending"))
        if self._reconcile_pending() and self._soft_latch_keeps_waiting(bars):
            # The soft latch usually clears within minutes: wait for it like any
            # recoverable gate so a BUY is not lost, and decide on the last
            # retry with BUYs held if it is still set (exits must go out).
            reason = getattr(self, "_reconcile_pending_reason", None) or "reconcile_pending"
            return not self._defer_portfolio_bars(bars, str(reason))
        # Fail-closed: a pending reconciliation (material drift detected) holds
        # new BUY orders until a verified ``reconcile()`` clears it (the submit
        # gate passed below holds them; planned SELLs still go out).
        # This is the engine-side complement to the broker's 7-category
        # ``reconcile()`` — holdings==broker is enforced before any new
        # decision can emit orders (restart scenarios A-G).
        # One immutable account snapshot per processing cycle, shared by every
        # market's decision in this batch (single broker fetch instead of one
        # per market). Fetched lazily on the first actual decision; freshness
        # is enforced against maximum_account_age_for_entry_seconds.
        account: AccountSnapshot | None = None
        account_lease: _AccountSnapshotLease | None = None
        deferred_any = False
        for portfolio_runtime in getattr(self, "_portfolio_runtimes", ()):
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
            stale_sell_only_account: AccountSnapshot | None = None
            if readiness_reasons and self._stale_account_sell_only_applies(readiness_reasons):
                stale_sell_only_account = self._stale_account_sell_only_snapshot(portfolio_runtime.market)
                if stale_sell_only_account is not None:
                    log.warning(
                        "Stale account snapshot: sell-only decision for market=%s (buys held, sells capped at owned)",
                        portfolio_runtime.market.value,
                    )
                    readiness_reasons = ()
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
                    msg = f"Hisse alınmadı: {tr}."
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
                # Recoverable readiness gate (stale account/queue, transient
                # degraded state): re-queue instead of losing the session.
                if self._defer_portfolio_batch(
                    relevant,
                    portfolio_runtime.market,
                    f"readiness:{readiness_reasons[0] if readiness_reasons else 'readiness_blocked'}",
                ):
                    deferred_any = True
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
            cross_check: CrossCheckOutcome | None = None
            if portfolio_runtime.market == Market.EQUITY:
                # Second-source check of the session-close signal. Waiting for
                # the SIP bar defers this batch (the decision loop retries it)
                # instead of blocking the worker; orders queue for the next
                # open either way.
                step = self._signal_cross_check_step(portfolio_runtime, relevant)
                if step is None:
                    # Already parked in _deferred_portfolio_batches by the
                    # cross-check coordinator with its own retry_at.
                    deferred_any = True
                    continue
                cross_check = step if isinstance(step, CrossCheckOutcome) else None
            if stale_sell_only_account is not None:
                # Sell-only over the last known snapshot: no broker fetch, no
                # lease, and the entry latch caps every target at its current
                # weight so the planner emits SELL orders only.
                account_lease = None
                entry_latch = (
                    STALE_ACCOUNT_SELL_ONLY_LATCH
                    if entry_latch is None
                    else f"{entry_latch}|{STALE_ACCOUNT_SELL_ONLY_LATCH}"
                )
            try:
                if stale_sell_only_account is None and account is None and getattr(self, "broker", None) is not None:
                    # Broker I/O happens before the portfolio lock. The lease
                    # is checked again at the decision boundary below so a
                    # fill cannot invalidate the cached snapshot in between.
                    try:
                        account_lease = self._fresh_account_snapshot_lease()
                    except Exception as exc:
                        if _is_transient_network_error(exc):
                            raise _TransientAccountFetch(str(exc)) from exc
                        raise
                    account = account_lease.snapshot
                if deferred and account is not None:
                    # The account was fetched after the close; re-mark it at
                    # the decision close so the deferred decision is sized on
                    # the same prices the strategy decided on (F36).
                    account = self._account_at_decision_close(account, relevant)
                if account_lease is not None and not self._account_snapshot_lease_is_current(account_lease):
                    log.warning(
                        "Portfolio decision deferred: account snapshot lease invalidated for market=%s",
                        portfolio_runtime.market.value,
                    )
                    if self._defer_portfolio_batch(
                        relevant, portfolio_runtime.market, "account_snapshot_lease_invalidated"
                    ):
                        deferred_any = True
                    continue
                with self._portfolio_lock:
                    # Claim under the narrow cache guard immediately before
                    # the call. Invalidation after this point is deferred
                    # until release, closing the check-to-use race without
                    # retaining the cache lock across order I/O.
                    lease_claimed = account_lease is None or self._claim_account_snapshot_lease(account_lease)
                    if not lease_claimed:
                        log.warning(
                            "Portfolio decision deferred: account snapshot lease invalidated before use for market=%s",
                            portfolio_runtime.market.value,
                        )
                        if self._defer_portfolio_batch(
                            relevant, portfolio_runtime.market, "account_snapshot_lease_invalidated"
                        ):
                            deferred_any = True
                        continue
                    try:
                        # The breaker, reconcile gates and entry latch above were
                        # read before the lease fetch and this lock. Re-read them
                        # here so a halt or latch raised in between cannot size
                        # a BUY (C9-2).
                        live_breaker = getattr(self, "_circuit_breaker_reason", None)
                        if live_breaker is not None and live_breaker not in _RECONCILIATION_INCIDENT_REASONS:
                            log.warning(
                                "Portfolio decision dropped: runtime halted (%s) before submit; market=%s",
                                live_breaker,
                                portfolio_runtime.market.value,
                            )
                            continue
                        if live_breaker is not None or getattr(self, "_reconcile_failed", False):
                            live_reason = live_breaker or "reconciliation_failed"
                            if self._defer_portfolio_batch(relevant, portfolio_runtime.market, live_reason):
                                deferred_any = True
                            continue
                        if self._reconcile_pending() and self._soft_latch_keeps_waiting(relevant):
                            live_reason = getattr(self, "_reconcile_pending_reason", None) or "reconcile_pending"
                            if self._defer_portfolio_batch(relevant, portfolio_runtime.market, str(live_reason)):
                                deferred_any = True
                            continue
                        live_latch = getattr(self, "_entry_risk_latch", {}).get(portfolio_runtime.market)
                        if live_latch is not None and (entry_latch is None or live_latch not in entry_latch.split("|")):
                            entry_latch = live_latch if entry_latch is None else f"{entry_latch}|{live_latch}"
                        kwargs: dict[str, Any] = {
                            "entry_latch": entry_latch,
                            # Per-order re-read of the permission flags (BUYs only, plus
                            # stop/breaker for everything): a halt or latch raised during
                            # the broker calls holds the BUY before it is sent.
                            "submit_gate": lambda order, market=portfolio_runtime.market: (
                                self._advance_submit_block_reason(market, order)
                            ),
                        }
                        if stale_sell_only_account is not None:
                            kwargs["account"] = stale_sell_only_account
                        elif account is not None:
                            kwargs["account"] = account
                        if cross_check is not None:
                            kwargs["signal_cross_check"] = cross_check
                        portfolio_result = portfolio_runtime.process_closed_bars(relevant, **kwargs)
                    finally:
                        if account_lease is not None:
                            self._release_account_snapshot_lease(account_lease)
            except _TransientAccountFetch as exc:
                # A broker/network hiccup at decision time is not a portfolio
                # failure: the session is re-queued and retried, and the
                # runtime is NOT halted (F10).
                log.warning("Portfolio decision deferred after account fetch failure: %s", exc)
                if self._defer_portfolio_batch(relevant, portfolio_runtime.market, f"account_fetch:{exc}"):
                    deferred_any = True
                continue
            except OwnershipDriftError as exc:
                # The portfolio lock was released when the exception left its
                # with-block, and this path never holds the ingestion lock.
                with getattr(self, "_state_lock", threading.RLock()):
                    self._circuit_breaker_reason = "ownership_drift"
                    self._note_halt_reason("ownership_drift")
                    self._state = "halted"
                _broker = getattr(self, "broker", None)
                automate_incident_recovery(
                    IncidentCategory.POSITION_DRIFT,
                    account_id=getattr(_broker, "account_id", "unknown") if _broker is not None else "unknown",
                    strategy=getattr(getattr(portfolio_runtime, "strategy", None), "name", "unknown"),
                    error=str(exc),
                    current_state={"market": portfolio_runtime.market.value},
                    reconcile_action=self._reconcile_for_incident_recovery,
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
                # Halt is terminal for this batch: the breaker persists and
                # the operator owns recovery, so no retry is queued.
                return True
            except Exception as exc:
                if self._is_deferrable_decision_failure(portfolio_runtime, relevant, exc):
                    # A network-class failure in a pre-submission broker read
                    # (open orders, asset rules, account, working buys) with
                    # NO decision persisted for this session is provider
                    # noise, not a portfolio failure: PortfolioRuntime
                    # restored its watermark, so the re-queued batch is
                    # decided afresh (F10).
                    log.warning(
                        "Portfolio decision deferred after a broker network error before any order was persisted: %s",
                        exc,
                    )
                    if self._defer_portfolio_batch(relevant, portfolio_runtime.market, f"broker_read:{exc}"):
                        deferred_any = True
                    continue
                self._raise_halt("portfolio_runtime_failure")
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
                return True
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
            if self._park_held_session(portfolio_runtime, relevant, portfolio_result.status):
                deferred_any = True
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
        return not deferred_any

    def _is_deferrable_decision_failure(self, portfolio_runtime: Any, bars: list[Bar], exc: BaseException) -> bool:
        """True when a decision failure may be retried instead of halting.

        Only a network-class failure qualifies, and only while no decision
        row exists for the batch's session: the decision row and its order
        rows are persisted in one transaction, so no decision row means no
        order of THIS session's decision was persisted (nor submitted). A
        previous decision's open rebalance may still send its residual SELLs
        before the retry; that is safe to repeat because residual ids are
        deterministic and an existing row is never sent again. Anything else — a domain
        or database failure, or a failure after the decision was written —
        fails closed. A repository that cannot answer fails closed too.
        """
        if isinstance(exc, OwnershipDriftError) or not _is_transient_network_error(exc):
            return False
        try:
            timestamp = max(bar.timestamp for bar in bars)
            # Session-key match, like the runtime's duplicate detection: an
            # equity session decided under its other daily label (provider
            # 00:00 ET vs stream 09:30 ET) is decided, not deferrable.
            session_decided = getattr(portfolio_runtime, "_session_decided", None)
            if callable(session_decided):
                return not session_decided(timestamp)
            return not portfolio_runtime.repository.has_decision(
                portfolio_runtime.account_id,
                portfolio_runtime.configuration_digest,
                timestamp,
                portfolio_runtime.market,
            )
        except Exception:
            log.warning("Decision persistence check failed; failing closed", exc_info=True)
            return False

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
            target_symbols = [weight.symbol for weight in target.weights]
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

            # Per side: planned orders and the ones submitted in this decision, so
            # submitted SELLs are not netted against BUYs the rebalance deferred.
            submitted_ids = set(result.submitted_order_ids)
            order_sides: dict[str, tuple[int, int]] = {}
            for side in ("sell", "buy"):
                planned_orders = [
                    order
                    for order in result.orders
                    if str(getattr(getattr(order, "side", None), "value", getattr(order, "side", ""))).lower() == side
                ]
                if planned_orders:
                    done = sum(
                        1 for order in planned_orders if getattr(order, "client_order_id", None) in submitted_ids
                    )
                    order_sides[side] = (done, len(planned_orders))
            message, fields = build_equity_daily_summary(
                result.target,
                account,
                status=str(result.status),
                decision_id=result.decision_id,
                orders=len(result.orders),
                submitted=len(result.submitted_order_ids),
                tax_ytd=self._daily_summary_tax_line(),
                order_sides=order_sides,
            )
            alerts.emit("daily_summary", message=message, **fields)
        except Exception:
            log.warning("Equity daily summary failed", exc_info=True)

    def _daily_summary_tax_line(self) -> str:
        """Best-effort YTD tax segment for the daily summary. Never raises.

        Reads the ledger through a short read-only connection and the
        configured USD/TRY and Yi-UFE rate files. Any failure yields the
        ``tax: unavailable`` text, so the summary is still sent without the
        figures instead of being dropped.
        """
        from parayabuck.monitoring.daily_summary import format_tax_ytd

        connection = None
        try:
            from datetime import datetime as _datetime

            from parayabuck.tax.export import ytd_status
            from parayabuck.tax.ledger import TURKEY_TIME

            runtime_config = getattr(getattr(self, "app_config", None), "runtime", None)
            database_path = getattr(runtime_config, "decision_db_path", None)
            if not database_path or not Path(str(database_path)).exists():
                return format_tax_ytd({"status": "unavailable"})
            connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=5.0)
            account_id = str(getattr(getattr(self, "broker", None), "account_id", "") or "unknown")
            year = _datetime.now(TURKEY_TIME).year
            return format_tax_ytd(ytd_status(connection, account_id, year))
        except Exception:
            log.warning("Daily summary tax line unavailable", exc_info=True)
            return format_tax_ytd({"status": "unavailable"})
        finally:
            if connection is not None:
                connection.close()

    def _run_startup_catch_up(self, *, account: AccountSnapshot | None = None, blocked_only: bool = False) -> None:
        """Force an immediate full-target decision on the latest warmup bars.

        A fresh account started mid-period would otherwise sit in cash until
        the next cadence boundary (the backtest engine decides on its first
        usable bar). Expected gates/readiness/no-trade outcomes are returned
        as ``PortfolioRuntimeResult`` statuses. Exceptions are invariant,
        persistence, safety, or provider failures and propagate to ``start``
        so an invalid engine cannot advertise itself as running.

        ``blocked_only`` (the operator-reset retry) re-runs only the runtimes
        whose earlier catch-up a breaker or readiness gate blocked.
        """
        if not self._running or self._warming_up:
            return
        if not getattr(self, "_startup_committed", False):
            # A stop that arrived after the checkpoint in _start_impl aborts the
            # catch-up before its first order (C2-9-1).
            self._raise_if_startup_cancelled()
        runtimes = tuple(getattr(self, "_portfolio_runtimes", ()))
        if blocked_only:
            runtimes = tuple(runtime for runtime in runtimes if _catch_up_blocked(runtime))
        breaker = self._circuit_breaker_reason or (
            "reconciliation_failed"
            if getattr(self, "_reconcile_failed", False)
            else getattr(self, "_reconcile_pending_reason", None)
        )
        if breaker is not None:
            # Recorded as blocked so reset_safety_lock knows to retry it.
            for portfolio_runtime in runtimes:
                portfolio_runtime.startup_catch_up_status = f"blocked:{breaker}"
            return
        for portfolio_runtime in runtimes:
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
            # The batch the catch-up decides on: the newest bar of each history
            # series, as startup_catch_up builds it.
            catch_up_bars = [series[-1] for series in portfolio_runtime.history.values() if series]
            cross_check: CrossCheckOutcome | None = None
            if portfolio_runtime.market == Market.EQUITY and catch_up_bars:
                # The catch-up is a session decision like any other, so it runs
                # the second-source signal check the scheduled path runs (P2-6-1).
                step = self._signal_cross_check_step(portfolio_runtime, catch_up_bars)
                if step is None:
                    # The coordinator parked the batch until the SIP bar is in;
                    # the due loop decides it with the check.
                    self._release_seeded_watermark(portfolio_runtime, catch_up_bars)
                    portfolio_runtime.startup_catch_up_status = "signal_cross_check_pending"
                    continue
                cross_check = step if isinstance(step, CrossCheckOutcome) else None
            if account is None:
                # Broker I/O stays outside _portfolio_lock (R2A4): the lock
                # below covers only the in-memory catch-up, matching every
                # other decision-path fetch in this file.
                account = self.broker.account()
            with self._portfolio_lock:
                result = portfolio_runtime.startup_catch_up(
                    entry_latch=entry_latch,
                    account=account,
                    signal_cross_check=cross_check,
                    # Per-order halt re-check, as on the decision path (P2-2-2).
                    submit_gate=lambda order, market=portfolio_runtime.market: self._advance_submit_block_reason(
                        market, order
                    ),
                )
            if self._park_held_session(portfolio_runtime, catch_up_bars, result.status):
                self._release_seeded_watermark(portfolio_runtime, catch_up_bars)
            self._record_paper_decision_receipt(portfolio_runtime, result, account)
            self._emit_equity_daily_summary(portfolio_runtime, result, account)
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
        queue_stale = health.queue_age_seconds > self.config.maximum_queue_age_seconds
        if queue_stale:
            reasons.append("event_queue_stale")
        if queue_stale != getattr(self, "_event_queue_stale_latched", False):
            # D7-6: one alert per transition. Before this, only ops status showed
            # the latch; decisions were held with no operator-facing signal.
            self._event_queue_stale_latched = queue_stale
            if queue_stale:
                alerts.emit(
                    "risk_incident",
                    message="Event queue stale: new entries blocked until the queue drains",
                    reason="event_queue_stale",
                    queue_age_seconds=round(health.queue_age_seconds, 1),
                )
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
        dead_workers = [
            name
            for name, worker in dict(getattr(self, "_workers", {})).items()
            if name in shared_workers and not worker.alive
        ]
        if dead_workers:
            reasons.append("worker_unavailable:" + ",".join(sorted(dead_workers)))
        # One atomic read: writers clear halts under _state_lock on other
        # threads, so a check-then-index pair could append the string "None".
        halt_reason = getattr(self, "_market_entry_halts", {}).get(market) if market is not None else None
        if halt_reason:
            reasons.append(str(halt_reason))
        return tuple(reasons)

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

    def _equity_signal_basis_symbols(self) -> tuple[str, ...]:
        """Equity symbols whose signal basis gates entries, sorted.

        Only the symbols an equity strategy reads at a decision
        (``_required_symbols``, the ``decision_symbols`` narrowing): a
        benchmark the strategy never reads (SPY) must not latch entries — or
        freeze exits via a basis hold — by missing a provider bar. Falls back
        to the whole equity universe when no runtime narrows it.
        """
        universe = tuple(getattr(self, "_symbols_by_market", {}).get(Market.EQUITY, ()))
        required: set[str] = set()
        for runtime in getattr(self, "_portfolio_runtimes", ()):
            if getattr(runtime, "market", None) != Market.EQUITY:
                continue
            reader = getattr(runtime, "_required_symbols", None)
            required.update(reader() if callable(reader) else getattr(runtime, "history", {}) or universe)
        narrowed = sorted(symbol for symbol in universe if symbol in required)
        return tuple(narrowed) if narrowed else tuple(sorted(universe))

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
        for symbol in self._equity_signal_basis_symbols():
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
        required symbol is provider-verified; callers must then hold the
        whole equity book on False, exits included (T2-8-3).

        Hardening (per-symbol trailing-window, watermark, retries, dedup):
        * Per-symbol trailing window — ``required_oldest_by_symbol`` is
          derived from EACH symbol's own trailing window (``series[-required]``
          or ``series[0]`` for new listings with short history, or a
          calendar fallback), so a newly listed 5-day symbol does not inherit
          a 300-bar global requirement and does not fail verification.
        * Final-only coverage — a partial provider bar (a session still inside
          the SIP embargo) is neither coverage nor watermark: the watermark
          names the newest FINAL bar, so a decision on a later session forces
          a fresh verification (T2-8-2).
        * Split continuity — a provider newest close that sits at a standard
          split ratio from the stored tail, while every prior session still
          carries the pre-split basis, fails closed (T2-8-1).
        * Verified-through watermark — per-symbol ``verified_through_by_symbol``
          is monotonic (``max`` timestamp, updated only on adequate coverage)
          and restored only via ``adopt_authoritative_signal_history``; a
          failed or partial provider response never advances or partially
          overwrites watermarks (fail-closed). A membership refresh recreates
          the runtimes, so watermarks start empty and new symbols must be
          verified before entries.
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
          re-hitting Alpaca. It never weakens fail-closed (a new decision
          timestamp or new symbol set invalidates the dedup key).
        """
        history_client = getattr(self, "_historical", None)
        runtimes = [
            runtime for runtime in self._portfolio_runtimes if runtime.market == Market.EQUITY and runtime.history
        ]
        if history_client is None or not runtimes:
            return True
        symbols = list(self._equity_signal_basis_symbols())
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
        fallback_span = bars_to_calendar_days("1d", required_bars)
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
            outcome = BatchOutcome()
            try:
                adjusted = history_client.get_bars_batch(
                    symbols, "1d", start=start, end=end, adjustment="splits", outcome=outcome
                )
                break
            except Exception as exc:
                if not _is_transient_network_error(exc) or attempt == 1:
                    self._record_signal_adjustment_failure(f"provider fetch failed: {exc}")
                    return False
                # Transient: bounded retry with short backoff.
                time.sleep(0.5 * (attempt + 1))
                continue
        # A provider outage is answered from the local warmup cache, and that
        # cache is the very history this call is supposed to verify against.
        # Counting it as a success is how an HTTP outage produced a FALSE
        # recovery alert and kept the staleness alarm quiet for days: nothing
        # was verified, so a cache-served response is unverified.
        if outcome.degraded:
            self._record_signal_adjustment_failure(
                "provider unavailable: response served from the local warmup cache, not verified"
            )
            return False
        # A provider window with a missing session or a dropped row is never
        # adopted, even when the stored series is empty and so cannot show the
        # hole: warmup refused to seed such a symbol, and so must the verifier.
        # Only FINAL provider bars are authority. A partial newest session
        # (still inside the SIP embargo) is answered to callers but never
        # adopted, so it must not count as coverage or as the watermark
        # either: a watermark on it would let a decision run on the stream
        # bar for a session whose final close was never adopted (T2-8-2).
        embargo = self._signal_cross_check_coordinator().sip_embargo
        final = {
            symbol: [bar for bar in adjusted.get(symbol, ()) if provider_day_bar_is_final(bar.timestamp, end, embargo)]
            for symbol in symbols
        }
        inadequate = [
            symbol
            for symbol in symbols
            if symbol in outcome.incomplete
            or not adequate_coverage(
                final.get(symbol, ()),
                required_oldest=required_oldest_by_symbol.get(symbol, fallback_oldest),
                required_newest=required_through,
                stored=_stored_signal_series(runtimes, symbol),
            )
        ]
        if inadequate:
            self._record_signal_adjustment_failure("provider coverage incomplete for symbols: " + ",".join(inadequate))
            return False
        # A provider window whose newest close sits at a standard split ratio
        # from the stored tail, while its prior sessions still carry the
        # pre-split basis, is a split the provider has not rebased yet.
        # Adopting it would pair post-split closes with pre-split history.
        discontinuous = [
            symbol
            for symbol in symbols
            if split_continuity_breach(final.get(symbol, ()), _stored_signal_series(runtimes, symbol))
        ]
        if discontinuous:
            self._record_signal_adjustment_failure(
                "split continuity unverified (newest provider close is a split ratio from the stored tail; "
                "prior sessions unrebased) for symbols: " + ",".join(discontinuous)
            )
            return False
        verified_through_by_symbol = {symbol: max(bar.timestamp for bar in final[symbol]) for symbol in symbols}
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
                        {symbol: final.get(symbol, []) for symbol in runtime.symbols},
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

    def _signal_cross_check_coordinator(self) -> SignalCrossCheckCoordinator:
        coordinator = getattr(self, "_signal_cross_check", None)
        if isinstance(coordinator, SignalCrossCheckCoordinator):
            return coordinator
        configured = getattr(getattr(self, "app_config", None), "signal_cross_check", None)
        config = configured if isinstance(configured, SignalCrossCheckConfig) else SignalCrossCheckConfig()
        coordinator = SignalCrossCheckCoordinator(
            enabled=config.enabled,
            sip_embargo=timedelta(minutes=config.sip_embargo_minutes),
            retry_window=timedelta(minutes=config.retry_window_minutes),
            retry_interval=timedelta(seconds=config.retry_interval_seconds),
            close_divergence_bps=config.close_divergence_bps,
            session_deadline=lambda session: self._decision_session_close(session) + self._decision_retry_settings()[1],
            rebalance_band=getattr(
                getattr(getattr(self, "app_config", None), "execution", None), "rebalance_band_pct", None
            ),
        )
        self._signal_cross_check = coordinator
        return coordinator

    def _signal_cross_check_step(self, portfolio_runtime: Any, relevant: list[Bar]) -> CrossCheckOutcome | bool | None:
        """Run one cross-check step for an equity decision batch.

        Returns None when the batch was deferred (retry pending), the
        outcome when the check finished, and False when no check applies
        (disabled, no second-source client, strategy without a trend input).
        Never raises: a checker failure must not cost a session.
        """
        try:
            client = getattr(self, "_historical", None)
            fetch = (
                (
                    lambda symbols, start, end: client.get_session_daily_bars(
                        symbols, start, end, feed="sip", adjustment="splits"
                    )
                )
                if client is not None
                else None
            )
            with getattr(self, "_portfolio_lock", threading.RLock()):
                history = {symbol: tuple(series) for symbol, series in portfolio_runtime.history.items()}
            step = self._signal_cross_check_coordinator().step(
                portfolio_runtime.strategy, history, relevant, fetch, datetime.now(UTC)
            )
        except Exception:
            log.warning("Signal cross-check failed; deciding on the primary source", exc_info=True)
            return False
        if step.action == "defer":
            decision_timestamp = max(bar.timestamp for bar in relevant)
            deferred = getattr(self, "_deferred_portfolio_batches", None)
            if not isinstance(deferred, dict):
                deferred = {}
                self._deferred_portfolio_batches = deferred
            deferred[(portfolio_runtime.market, decision_timestamp)] = (list(relevant), step.retry_at)
            log.info(
                "Equity decision %s waits for the SIP daily bar (signal cross-check), retry at %s",
                decision_timestamp.isoformat(),
                step.retry_at.isoformat() if step.retry_at is not None else "n/a",
            )
            return None
        if step.outcome is None:
            return False
        self._record_signal_cross_check(step.outcome)
        return step.outcome

    def _record_signal_cross_check(self, outcome: CrossCheckOutcome) -> None:
        """Log and alert one finished cross-check. Never raises."""
        try:
            fields: dict[str, Any] = {
                "market": Market.EQUITY.value,
                "session_date": outcome.session_date.isoformat(),
                "status": outcome.status,
                "close_gap_bps": dict(outcome.close_gap_bps),
                "attempts": outcome.attempts,
            }
            if outcome.status == STATUS_TREND_MISMATCH:
                category = "trend_mismatch"
            elif outcome.status == STATUS_SECONDARY_UNAVAILABLE:
                category = "secondary_unavailable"
                fields["reason"] = outcome.reason
            elif outcome.close_divergence:
                category = "close_divergence"
            elif outcome.weight_divergence:
                category = "weight_divergence"
            else:
                log.info(
                    "Signal cross-check %s: sources agree (close gaps bps %s)",
                    outcome.session_date.isoformat(),
                    outcome.close_gap_bps,
                )
                return
            fields["close_divergence"] = outcome.close_divergence
            fields["weight_gap"] = outcome.weight_gap
            log.warning("%s", outcome.summary())
            alerts.emit("signal_source_mismatch", message=outcome.summary(), category=category, **fields)
        except Exception:
            log.warning("Signal cross-check alert failed", exc_info=True)

    def _run_due_deferred_portfolio_batches(self, now: datetime | None = None) -> None:
        """Re-run decision batches deferred by a gate or the cross-check.

        The key is popped only AFTER ``_process_portfolio_timestamp`` reports
        the batch handled: a re-queued batch (readiness still blocking, lease
        invalidated again, cross-check still waiting) has already refreshed
        its own ``retry_at`` in place and must survive this pass.

        A batch whose session-close retry window has expired while parked
        (gate wait, cross-check wait, stalled loop) is expired with the same
        alert the gate path emits — never re-decided after its documented
        40-minute-after-close deadline, after the watchdog may already have
        reported it missing.
        """
        deferred = getattr(self, "_deferred_portfolio_batches", None)
        if not deferred:
            return
        now = now or datetime.now(UTC)
        _retry_interval, retry_window = self._decision_retry_settings()
        for key, (bars, retry_at) in sorted(deferred.items(), key=lambda item: item[0][1]):
            if retry_at is not None and retry_at > now:
                continue
            if bars:
                session = max(bar.timestamp for bar in bars).astimezone(NEW_YORK).date()
                deadline = self._decision_session_close(session) + retry_window
                # A batch parked past the session's decision deadline is
                # expired, never re-decided after it.
                if now >= deadline:
                    deferred.pop(key, None)
                    market = key[0]
                    decision_timestamp = max(bar.timestamp for bar in bars)
                    log.error(
                        "Decision retry window expired; session NOT decided: market=%s session=%s reason=%s",
                        market.value,
                        decision_timestamp.isoformat(),
                        "retry_window_ended_while_parked",
                    )
                    alerts.emit(
                        "decision_deferred",
                        message="Session-close decision retry window expired; session not decided",
                        market=market.value,
                        decision_timestamp=decision_timestamp.isoformat(),
                        reason="retry_window_ended_while_parked",
                        retry_window_ended=deadline.isoformat(),
                    )
                    continue
            if self._process_portfolio_timestamp(bars, deferred=True):
                deferred.pop(key, None)

    def signal_cross_check_status(self) -> dict[str, object]:
        """Last finished cross-check and any decision waiting for it."""
        coordinator = getattr(self, "_signal_cross_check", None)
        if not isinstance(coordinator, SignalCrossCheckCoordinator):
            configured = getattr(getattr(self, "app_config", None), "signal_cross_check", None)
            enabled = configured.enabled if isinstance(configured, SignalCrossCheckConfig) else True
            return {"enabled": enabled, "last": None, "pending": {}}
        last = coordinator.last_outcome
        return {
            "enabled": coordinator.enabled,
            "last": last.to_metadata() if last is not None else None,
            "pending": coordinator.pending_status(),
        }

    def _equity_signal_basis_latch(self, decision_timestamp: datetime | None = None) -> str | None:
        """Latch reason when equity signals are unverified/stale, else None.

        Verification runs when the last success is stale OR — always — when
        any required symbol's watermark predates the decision timestamp. The
        invariant: a new-equity-entry decision may only pass when
        ``signal_verified_through_by_symbol[s]`` covers the decision
        timestamp's New York session for EVERY required equity signal
        symbol. An unverifiable basis FAILS CLOSED: the latch holds the whole
        equity book, risk-reducing exits included, until the basis verifies
        again (runtime_service holds every target, because a pre-split close
        looks like a crash and a SELL on it is as wrong as a BUY; owner
        decision 8.3).

        Alerts once per transition into the latch (D7-3); the latch itself is
        re-evaluated on every call.
        """
        latch = self._equity_signal_basis_check(decision_timestamp)
        with getattr(self, "_state_lock", threading.RLock()):
            was_latched = bool(getattr(self, "_signal_basis_latched", False))
            self._signal_basis_latched = latch is not None
        if latch is not None and not was_latched:
            # Not ``signal_adjustment_failure``: that kind is already emitted once
            # per monitor failure episode, and this latch can enter without one.
            alerts.emit(
                "risk_incident",
                message=(
                    "Equity book held: signal basis unverified; new entries and risk-reducing "
                    "exits both wait until the basis verifies again"
                ),
                reason=latch,
            )
        return latch

    def _equity_signal_basis_cached_latch(self) -> str | None:
        """Latch reason from the CACHED verifier state only; never calls the provider.

        For the trade-update path, which must not block on a provider call
        (T2-7-1). A stale or unverified cache answers with the latch reason,
        so the deferred BUY waits for the signal monitor to verify again.
        """
        if not getattr(self, "_symbols_by_market", {}).get(Market.EQUITY):
            return None
        if self._equity_signal_basis_verified():
            return None
        return "signal_adjustment_unverified"

    def _equity_signal_basis_check(self, decision_timestamp: datetime | None = None) -> str | None:
        """The unlatched evaluation behind ``_equity_signal_basis_latch``."""
        if not getattr(self, "_symbols_by_market", {}).get(Market.EQUITY):
            return None
        try:
            if self._equity_signal_basis_verified(decision_timestamp):
                return None
            if not self.verify_equity_signal_basis(required_through=decision_timestamp):
                return "signal_adjustment_unverified"
            if not self._equity_signal_basis_verified(decision_timestamp):
                # Verification succeeded for history but the provider has not
                # published the decision-day bar yet: still fail closed.
                return "signal_adjustment_unverified"
        except Exception as exc:
            # An unexpected verifier failure must not crash the decision
            # thread: the equity book is held (entries and exits) until the
            # basis verifies again.
            log.warning("Equity signal-basis verification raised; entries fail closed: %s", exc, exc_info=True)
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
            if self.verify_equity_signal_basis() and getattr(self, "_basis_buy_deferred", False):
                # The trade-update path only reads the cache (T2-7-1), so a BUY
                # it deferred on the basis resumes here, off the fill path. Its
                # own failure must not stop the monitor; the flag then stays set.
                try:
                    self._advance_portfolio_rebalance(Market.EQUITY)
                    self._basis_buy_deferred = False
                except Exception:
                    log.warning("Deferred equity BUY resume after basis verification failed", exc_info=True)
            self._check_market_data_feed()
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
        # Warmup-retry threads and the decision worker both resize the live
        # not-ready set; comparing or copying it while another thread
        # resizes it raises RuntimeError. So every access is serialized here
        # and the local is an immutable snapshot. The state lock covers the
        # snapshot and the not-ready updates; nothing inside that block takes
        # another lock. The warmup-retry spawn (below, after the state lock is
        # released) takes its own spawn lock.
        state_lock = getattr(self, "_state_lock", threading.RLock())
        with state_lock:
            # Copy each history once. Portfolio-lock holders insert and replace
            # its keys, and iterating the live dict here could raise "dict
            # changed size during iteration" (CC-10); the copy is taken under
            # the state lock and is a snapshot for the rest of this pass.
            histories = [(runtime, dict(runtime.history)) for runtime in runtimes]
            # A managed equity pin outside the strategy universe is only held
            # for its exit. A pin with NO provider history can never become
            # ready, so it must not pin the engine in warming_up or block the
            # safety-lock reset (T2-5-2). A pin that has history is still gated.
            universe = getattr(getattr(getattr(self, "app_config", None), "universe", None), "equity", None)
            references = set(getattr(universe, "symbols", ()) or ())
            pin_only = set(getattr(self, "_managed_pin_symbols", ()) or ()) - references
            exempt = {symbol for symbol in pin_only if not any(symbol in history for _runtime, history in histories)}
            self._not_ready_symbols.difference_update(exempt)
            for symbol in required_symbols:
                if symbol in exempt:
                    continue
                relevant = [(runtime, history) for runtime, history in histories if symbol in history]
                ready = bool(relevant)
                for runtime, history in relevant:
                    series = history[symbol]
                    benchmark_series = history.get("SPY", ())
                    expected_latest = (
                        benchmark_series[-1].timestamp
                        if benchmark_series
                        else max(
                            (candidate[-1].timestamp for candidate in history.values() if candidate),
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
            # The counter describes the pending SET, not the process: it resets on
            # ANY membership change — the set emptying, a member recovering, or a
            # newly unready symbol that was never retried — so a chronically
            # lagging symbol cannot push a brand-new one out to the ceiling.
            # Only an unchanged set keeps doubling toward the cap. Every write to
            # the backoff fields happens under the state lock (C9-5).
            attempt = getattr(self, "_warmup_retry_attempt", 0)
            if not pending or pending != getattr(self, "_warmup_retry_symbols", frozenset()):
                attempt = 0
                self._warmup_retry_attempt = 0
        retry_delay = min(_WARMUP_RETRY_MAX_SECONDS, _WARMUP_RETRY_SECONDS * 2 ** min(attempt, 8))
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
        # Reached from the decision loop, startup and the retry thread
        # itself: keep check-spawn-assign atomic so overlapping
        # callers cannot start duplicate pb-warmup-retry threads and orphan
        # the overwritten reference (never joined by stop). Refuses to spawn
        # once stopping: stop() snapshots under this lock after clearing _running.
        with self._warmup_retry_spawn_lock:
            if not getattr(self, "_running", False):
                return
            current = getattr(self, "_warmup_retry_thread", None)
            if current is not None and current.is_alive():
                return
            # The backoff fields are written only under the state lock, the
            # same lock _refresh_readiness uses for its reset (C9-5). The copy
            # of the not-ready set cannot race a resize under that lock either.
            with getattr(self, "_state_lock", threading.RLock()):
                self._last_warmup_retry_at = time.monotonic()
                self._warmup_retry_attempt += 1
                self._warmup_retry_symbols = frozenset(self._not_ready_symbols)
            thread = threading.Thread(
                target=self._retry_incomplete_warmup,
                name="pb-warmup-retry",
                daemon=True,
            )
            self._warmup_retry_thread = thread
            thread.start()

    def _not_ready_snapshot(self) -> frozenset[str]:
        """Copy the live not-ready set under the state lock (R2A5)."""
        with getattr(self, "_state_lock", threading.RLock()):
            return frozenset(getattr(self, "_not_ready_symbols", set()))

    def _retry_incomplete_warmup(self) -> None:
        history = self._historical
        if history is None:
            return
        symbols = sorted(self._not_ready_snapshot())
        log.info("Retrying incomplete warmup for %d symbols", len(symbols))
        self._warmup_symbols(history, symbols, retry=True)
        self._refresh_readiness()

    def _has_ready_symbols(self) -> bool:
        required = {
            symbol for runtime in getattr(self, "_portfolio_runtimes", ()) for symbol in getattr(runtime, "symbols", ())
        }
        return bool(required) and not (required & self._not_ready_snapshot())

    def _persist_risk_state(self, account: AccountSnapshot) -> None:
        # _account_loop, the trade-update path and the operator resets call
        # this concurrently; the latch evaluation and the incident/risk-state
        # writes must not interleave.
        pending_alerts: list[tuple[str, str, dict[str, object]]] = []
        try:
            with getattr(self, "_risk_state_lock", threading.Lock()):
                account_id = self.broker.account_id or "unknown"
                if self.broker.account_id:
                    alerts.set_alert_context(account_id=self.broker.account_id)
                today = datetime.now(UTC).astimezone(NEW_YORK).date()
                # The epoch of the peak this account carries, not the live epoch: a
                # snapshot taken before a reset stored under the new epoch would be
                # restored as the new baseline after a restart (C2-4-5).
                peak_epoch = getattr(account, "peak_epoch", None)
                if peak_epoch is None:
                    peak_epoch = getattr(self.broker, "_peak_epoch", 0)
                self._evaluate_entry_risk_latch(account, defer_alerts=pending_alerts)
                reason = self._circuit_breaker_reason
                # The circuit_breaker_reason in the session row is kept for
                # backward compatibility; the incident table is authoritative for
                # restore, so every halt reason in force is recorded there (R9-3).
                with getattr(self, "_state_lock", threading.RLock()):
                    active_halts = self._active_halt_reasons()
                for halt in active_halts:
                    self.ledger.save_safety_incident(account_id, halt)
                self.ledger.save_risk_state(
                    RiskState(
                        account_id=account_id,
                        session_date=today,
                        day_start_equity=float(account.equity - account.daily_pnl),
                        peak_equity=float(account.peak_equity),
                        realized_pnl=float(account.realized_pnl),
                        unrealized_pnl=float(account.unrealized_pnl),
                        fees=self._fee_total_for_snapshot(account, account_id),
                        circuit_breaker_reason=reason,
                        updated_at=datetime.now(UTC),
                        peak_epoch=int(peak_epoch or 0),
                    )
                )
        finally:
            # Emitted after the lock (and also when the save raised, as before the move).
            for kind, message, fields in pending_alerts:
                alerts.emit(kind, message=message, **fields)

    def _fee_total_for_snapshot(self, account: AccountSnapshot, account_id: str) -> float:
        """Fee total for risk-state persistence.

        The snapshot's broker read already computed the account's fee total
        (alpaca_account.account), so reuse it instead of a second aggregate
        over the execution events on every pass. Only an account object without
        the field (a test double) falls back to the ledger.
        """
        fees = getattr(account, "fees", None)
        if fees is None:
            return self.ledger.total_fees_for_account(account_id)
        return float(fees)

    def _note_transient_reconcile_failure(self, exc: BaseException, message: str) -> bool:
        """Count one network-class reconciliation failure.

        Returns True while the failure count is still below
        ``engine.reconcile_failure_threshold`` (retry silently on the next
        60 s reconcile cycle), False once the threshold is reached and the
        caller must raise the breaker and persist the incident.

        A single Alpaca timeout must not become a circuit breaker that
        survives a restart and needs an operator reset: the decisions it
        blocks are re-queued for a bounded retry instead, and a clean
        reconcile clears ``_reconcile_failed`` on its own.
        """
        configured = getattr(getattr(self, "config", None), "reconcile_failure_threshold", None)
        try:
            threshold = max(1, int(configured)) if configured is not None else 3
        except (TypeError, ValueError):
            threshold = 3
        failures = int(getattr(self, "_transient_reconcile_failures", 0)) + 1
        self._transient_reconcile_failures = failures
        runtime_metrics_store.record_event("reconciliation_failure")
        if failures < threshold:
            log.warning(
                "%s (%d/%d consecutive, entries blocked, no incident yet): %s",
                message,
                failures,
                threshold,
                exc,
            )
            return True
        log.error("%s (%d consecutive, raising reconciliation breaker): %s", message, failures, exc)
        return False

    def _reconcile_safely(self) -> None:
        reconcile_lock = getattr(self, "_reconcile_lock", threading.Lock())
        with reconcile_lock:
            # A latch set after this point is not covered by this scan.
            requested_seq = int(getattr(self, "_reconcile_pending_seq", 0))
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
                        episode_key="reconciliation_drift",
                        categories=report.as_dict(),
                    )
                    # Still update last_reconciliation_at so the drift is visible,
                    # but do not clear incidents — require operator-verified recovery.
                    self._last_reconciliation_at = datetime.now(UTC)
                    self._last_account_at = datetime.now(UTC)
                    self._generic_reconcile_failures = 0
                    self._transient_reconcile_failures = 0
                    return
                self._last_reconciliation_at = datetime.now(UTC)
                self._last_clean_reconciliation_at = self._last_reconciliation_at
                self._last_account_at = datetime.now(UTC)
                self._generic_reconcile_failures = 0
                self._transient_reconcile_failures = 0
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
                with getattr(self, "_state_lock", threading.RLock()):
                    if int(getattr(self, "_reconcile_pending_seq", 0)) == requested_seq:
                        cleared_reason = getattr(self, "_reconcile_pending_reason", None)
                        self._reconcile_pending_reason = None
                        if cleared_reason is not None:
                            log.info("Reconcile-pending latch cleared after a clean reconciliation: %s", cleared_reason)
                # C9-6: under the risk-state lock, so a concurrent
                # _persist_risk_state cannot re-save a row this clean
                # reconcile has just cleared.
                with getattr(self, "_risk_state_lock", threading.Lock()):
                    self._clear_reconciliation_incidents()
                # F2-6-1: a clean reconcile can book a fill or a cancel that no
                # trade update advanced. Advance each market's rebalance now, so
                # its residual SELL or deferred BUYs do not wait for the next
                # trade update or closed bar. Only after a clean reconcile: the
                # drift path returned above.
                for market in dict.fromkeys(rt.market for rt in getattr(self, "_portfolio_runtimes", ())):
                    try:
                        self._advance_portfolio_rebalance(market)
                    except Exception:
                        log.warning(
                            "Rebalance advance after a clean reconciliation failed: market=%s",
                            market.value,
                            exc_info=True,
                        )
            except ReconciliationResultError as exc:
                if _is_transient_network_error(exc):
                    # F14: a network failure inside the FILL-activity backfill
                    # surfaces here as a chained ReconciliationResultError, so
                    # it must not be treated as a permanent invalid result.
                    # Escalate exactly like the RequestException branch below
                    # once the consecutive-failure threshold is reached: a
                    # persistent outage must raise the breaker and alert.
                    self._reconcile_failed = True
                    if self._note_transient_reconcile_failure(
                        exc, "Reconciliation deferred after a broker network error"
                    ):
                        return
                    self._raise_reconciliation_breaker("reconciliation_failed")
                    self._persist_reconciliation_incident("reconciliation_failed")
                    alerts.emit(
                        "reconciliation_failure",
                        message="Reconciliation deferred after a broker network error",
                        reason="reconciliation_failed",
                        episode_key="reconciliation_network_error",
                        error=str(exc),
                    )
                    return
                self._raise_reconciliation_breaker("reconciliation_failed")
                self._reconcile_failed = True
                self._persist_reconciliation_incident("reconciliation_failed")
                runtime_metrics_store.record_event("reconciliation_failure")
                alerts.emit(
                    "reconciliation_failure",
                    message="Reconciliation returned an invalid result shape",
                    reason="reconciliation_failed",
                    episode_key="reconciliation_invalid_result",
                    error=str(exc),
                )
            except RequestException as exc:
                # Alpaca occasionally times out for a single request. The next
                # scheduled reconciliation retries it; a full traceback every
                # minute obscures actionable protection failures.
                self._reconcile_failed = True
                if self._note_transient_reconcile_failure(
                    exc, "Position protection check deferred after Alpaca API error"
                ):
                    return
                self._raise_reconciliation_breaker("reconciliation_failed")
                self._persist_reconciliation_incident("reconciliation_failed")
                alerts.emit(
                    "reconciliation_failure",
                    message="Position protection check deferred after Alpaca API error",
                    reason="reconciliation_failed",
                    episode_key="protection_check_api_error",
                    error=str(exc),
                )
            except OwnershipDriftError:
                log.exception("Portfolio ownership drift detected; entries halted")
                # reconcile -> state is the established order: no thread
                # holding the state lock ever takes the reconcile lock.
                with getattr(self, "_state_lock", threading.RLock()):
                    first = self._circuit_breaker_reason != "ownership_drift"
                    self._circuit_breaker_reason = "ownership_drift"
                    self._note_halt_reason("ownership_drift")
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
            except Exception as exc:
                self._reconcile_failed = True
                if _is_transient_network_error(exc) and self._note_transient_reconcile_failure(
                    exc, "Position protection check deferred after a broker network error"
                ):
                    return
                # A generic failure is usually a code bug, not a broker
                # timeout. Keep retrying (defense in depth) but escalate after
                # three consecutive failures, which is strictly more lenient
                # than a transient network error.
                self._generic_reconcile_failures = getattr(self, "_generic_reconcile_failures", 0) + 1
                log.exception("Periodic position protection check failed")
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
                    episode_key="protection_check_generic",
                    consecutive_failures=self._generic_reconcile_failures,
                )

    def _reconcile_pending(self) -> bool:
        """True while the reconcile-pending latch gates new entries."""
        return getattr(self, "_reconcile_pending_reason", None) is not None

    def trading_state(self, market: Market | None = None) -> TradingState:
        """Order permission derived from the existing flags (see ``TradingState``).

        Mapping, first matching row wins:

        | flag(s)                                              | state    |
        |------------------------------------------------------|----------|
        | ``_circuit_breaker_reason`` is set, or ``_warming_up`` | HALTED   |
        | ``_reconcile_failed``, or the reconcile-pending latch | REDUCING |
        | ``_entry_risk_latch`` set (market, or any when ``market`` is None) | REDUCING |
        | ``_market_entry_halts`` set (same scope)              | REDUCING |
        | none of the above                                    | ACTIVE   |

        Deliberately not derived: ``_running``, ``_state`` (lifecycle and the
        stream-worker "degraded" blip) and ``_startup_committed``. Those keep
        their own gates at their call sites, so the derived state describes
        the permission flags only.
        """
        if getattr(self, "_circuit_breaker_reason", None) is not None or getattr(self, "_warming_up", False):
            return TradingState.HALTED
        if getattr(self, "_reconcile_failed", False) or self._reconcile_pending():
            return TradingState.REDUCING
        latches = getattr(self, "_entry_risk_latch", {})
        halts = getattr(self, "_market_entry_halts", {})
        if market is None:
            entry_blocked = any(value is not None for value in (*latches.values(), *halts.values()))
        else:
            entry_blocked = latches.get(market) is not None or halts.get(market) is not None
        return TradingState.REDUCING if entry_blocked else TradingState.ACTIVE

    def _set_reconcile_pending(self, reason: str) -> None:
        """Latch new entries until a reconcile started from here on is clean.

        Same gate as ``_reconcile_failed`` (buys and rejected-SELL retries
        wait), but not a halt: a clean reconcile clears it without an
        operator. The latch is set under the state lock and the reconcile is
        scheduled only after it is released, because the reconcile spawn lock
        is never taken while the state lock is held.
        """
        with getattr(self, "_state_lock", threading.RLock()):
            previous = getattr(self, "_reconcile_pending_reason", None)
            self._reconcile_pending_reason = reason
            self._reconcile_pending_seq = int(getattr(self, "_reconcile_pending_seq", 0)) + 1
        if previous != reason:
            # D7-5: the latch silently holds entries otherwise. Alerted once per
            # latch reason; a premarket probe failure that already alerted under
            # the same reason is coalesced by the alert throttle.
            alerts.emit(
                "reconciliation_failure",
                message="Reconcile-pending latch set: new entries wait for a clean reconciliation",
                reason=reason,
            )
        self._schedule_reconcile()

    def _maybe_run_premarket_probe(self, now: datetime | None = None) -> None:
        """Once per NYSE session, 30 to 5 minutes before the open: check the
        broker clock, calendar and open orders (monitoring/premarket.py).

        A failure alerts and latches the reconcile-pending gate, so new entries
        wait for a clean reconcile. Exits are never blocked and nothing halts.
        The session is marked before the probe runs, so a probe that raises is
        not retried every loop pass. Never raises (called from the decision loop).
        """
        try:
            moment = now or datetime.now(UTC)
            sessions = sessions_around(moment)
            due = [
                (session_date, opened)
                for session_date, opened, _closed in sessions
                if opened - timedelta(minutes=30) <= moment < opened - timedelta(minutes=5)
                and session_date not in self._premarket_probed_sessions
            ]
            if not due:
                return
            session_date, opened = min(due, key=lambda item: item[1])
            self._premarket_probed_sessions.add(session_date)
            account_id = self.broker.account_id or "unknown"
            open_local = [order.client_order_id for order in self._portfolio_repository.open_orders(account_id)]
            result = premarket_probe(
                self.broker,
                now=moment,
                calendar_sessions=sessions,
                open_orders_local=open_local,
            )
            if result.ok:
                log.info(
                    "Premarket probe passed: session=%s open=%s",
                    session_date.isoformat(),
                    opened.isoformat(),
                )
                return
            detail = "; ".join(result.reasons)
            log.error("Premarket probe failed: session=%s reasons=%s", session_date.isoformat(), detail)
            alerts.emit(
                "reconciliation_failure",
                message="Premarket probe failed; new entries wait for a clean reconcile",
                reason="premarket_probe_failed",
                session=session_date.isoformat(),
                detail=detail,
            )
            self._set_reconcile_pending("premarket_probe_failed")
        except Exception:
            log.exception("Premarket probe could not run")

    def _maybe_check_trade_feed_freshness(self, now: datetime | None = None) -> None:
        """Latch and reconcile when open orders outlive a silent trade feed.

        Only during the NYSE regular session, and only while the account holds
        non-terminal portfolio orders. The reference is the later of the last
        trade update and the last trigger, so one silent window causes at most
        one reconcile. Resting orders that simply have no fills also go quiet;
        that costs one reconcile per TTL, never a halt. Never raises.
        """
        try:
            now = now or datetime.now(UTC)
            if not getattr(self, "_running", False) or not is_session_open(now):
                return
            repository = getattr(self, "_portfolio_repository", None)
            if repository is None:
                return
            account_id = getattr(getattr(self, "broker", None), "account_id", None) or "unknown"
            if not repository.open_orders(account_id):
                return
            references = [
                stamp
                for stamp in (
                    getattr(self, "_last_trade_update_at", None),
                    getattr(self, "_trade_feed_probe_at", None),
                )
                if stamp is not None
            ]
            if not references:
                # First observation of open orders: start the silence clock.
                self._trade_feed_probe_at = now
                return
            silent_seconds = (now - max(references)).total_seconds()
            if silent_seconds < _TRADE_FEED_STALE_SECONDS:
                return
            self._trade_feed_probe_at = now
            log.warning(
                "No trade update for %.0fs with open orders; latching entries and reconciling",
                silent_seconds,
            )
            self._set_reconcile_pending("trade_feed_stale")
        except Exception:
            log.warning("Trade-feed freshness check failed", exc_info=True)

    def _schedule_reconcile(self) -> None:
        """Run the expensive broker scan without stalling finalized-bar decisions."""
        # Called from both the trade-update stream thread and the decision
        # loop: the is_alive() check, the assignment and start() must be one
        # atomic step or two callers can spawn duplicate pb-reconcile threads
        # and orphan the overwritten Thread reference (never joined by stop).
        # Refuses to spawn once the engine is stopping: stop() clears _running
        # before it takes this same lock to snapshot the threads it joins, so
        # a spawn that wins the lock is in that snapshot and a later one sees
        # _running False (CC-5: the join itself runs outside the lock).
        with self._reconcile_spawn_lock:
            if not getattr(self, "_running", False):
                return
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

        A failed reset leaves the peak and the halt as they were, so a retry
        starts from the same epoch. A reset whose epoch is already active
        returns the active baseline without re-baselining.

        Returns the new baseline equity. The epoch and outcome of the last
        call are kept in ``_last_risk_epoch_reset`` for the API response.
        """
        if not self._running:
            raise ValueError("The engine is not running; start it before resetting the risk epoch")
        if self._circuit_breaker_reason is not None:
            raise ValueError(
                "Cannot reset the risk epoch while a circuit breaker is active "
                f"({self._circuit_breaker_reason}); resolve that incident first"
            )
        get_peak_service = getattr(self.broker, "_get_peak_service", None)
        peak_service = get_peak_service() if callable(get_peak_service) else None
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
                raise ValueError(f"Cannot reset the risk epoch before reconciliation succeeds: {exc}") from exc
            if report.has_drift:
                self._raise_reconciliation_breaker("reconciliation_failed")
                self._reconcile_failed = True
                self._persist_reconciliation_incident("reconciliation_failed")
                raise ValueError(
                    f"Cannot reset the risk epoch while reconciliation reports drift: {report.drift_categories}"
                )
            self._last_reconciliation_at = datetime.now(UTC)
            with getattr(self, "_risk_state_lock", threading.Lock()):
                self._clear_reconciliation_incidents()
        # Captured before the account read: a reset that moves the epoch while
        # that read runs makes this one a no-op (see reset_peak_equity), not a
        # second rebase on top of the concurrent one (Q2-3-3).
        expected_epoch = peak_service.peak_epoch if peak_service is not None else None
        account = self.broker.account()  # fresh authoritative snapshot
        # A halt raised while the reconcile and the account read ran (a trade-update
        # failure, a worker crash) must stop the rebase: the operator verified the
        # drawdown, not that (R2-1-5).
        with getattr(self, "_state_lock", threading.RLock()):
            if self._circuit_breaker_reason is not None:
                raise ValueError(
                    "Cannot reset the risk epoch while a circuit breaker is active "
                    f"({self._circuit_breaker_reason}); resolve that incident first"
                )
        account_id = self.broker.account_id or "unknown"
        new_baseline = float(account.equity)
        if new_baseline <= 0:
            raise ValueError("Cannot reset the risk epoch with non-positive account equity")
        # Recorded on every epoch reset (log and journal) so the drawdown that
        # the operator re-baselined past stays on the record.
        pre_peak = float(account.peak_equity)
        pre_drawdown = float(account.drawdown_pct)
        repository = getattr(self, "_portfolio_repository", None)
        # Commit order: the peak sidecar first, then the incident clear, then the
        # DB high-water. A failure after the sidecar commit restores the peak
        # (memory and sidecar), so the halt stays latched and a retry starts from
        # the same epoch. account() reads its equity and epoch under the peak lock
        # and discards a snapshot whose epoch moved, so a pre-reset equity cannot
        # re-raise the new baseline (C2-4-1).
        # Without a peak service the broker's own reset takes its peak lock, so
        # it must not be called under that (non-reentrant) lock here.
        peak_lock = (getattr(self.broker, "_peak_lock", None) if peak_service is not None else None) or threading.Lock()
        with peak_lock:
            prior = None
            if peak_service is None:
                self.broker.reset_peak_equity(new_baseline)
            else:
                prior = (
                    peak_service.peak_equity,
                    peak_service.peak_epoch,
                    peak_service.peak_account_id,
                    peak_service.peak_reconciled,
                )
                if not peak_service.reset_peak_equity(new_baseline, expected_epoch=expected_epoch):
                    log.info(
                        "Risk epoch already active (epoch %s); the operator reset is a no-op",
                        peak_service.peak_epoch,
                    )
                    self._last_risk_epoch_reset: dict[str, object] = {
                        "epoch": int(peak_service.peak_epoch),
                        "baseline_equity": float(peak_service.peak_equity),
                        "changed": False,
                    }
                    # A crash after the sidecar commit left the incident clear or
                    # the DB high-water undone. Finish those steps for the epoch
                    # that is already active, or every later reset stays a no-op.
                    if repository is not None:
                        self.ledger.clear_safety_incident(account_id, "portfolio_drawdown_halt")
                        _heal_persisted_high_water(
                            repository, account_id, float(peak_service.peak_equity), int(peak_service.peak_epoch)
                        )
                    return float(peak_service.peak_equity)
            new_epoch = int(peak_service.peak_epoch) if peak_service is not None else None
            restored_before = bool(getattr(self, "_drawdown_halt_restored", False))
            try:
                self._drawdown_halt_restored = False
                self.ledger.clear_safety_incident(account_id, "portfolio_drawdown_halt")
                if repository is not None:
                    # The sidecar's epoch names the baseline in both stores (R2-6-3).
                    repository.reset_high_water(account_id, new_baseline, epoch=new_epoch)
            except Exception:
                # Roll back: the peak returns to its pre-reset epoch and the
                # drawdown halt the operator still sees stays on record.
                self._drawdown_halt_restored = restored_before
                if prior is not None and peak_service is not None:
                    peak_service.restore_peak_state(prior)
                if self._entry_risk_latch.get(Market.EQUITY) == "portfolio_drawdown_halt":
                    self.ledger.save_safety_incident(account_id, "portfolio_drawdown_halt")
                log.error(
                    "Risk epoch reset rolled back to epoch %s after a failed commit",
                    prior[1] if prior is not None else None,
                    exc_info=True,
                )
                raise
        # The epoch is committed. A failed post-reset persist is retried by the
        # next account pass; raising here would invite a retry that re-baselines.
        try:
            # Re-fetch the account AFTER the rebase: a pre-rebase snapshot would
            # re-evaluate the latch against the old monotonic peak and instantly
            # re-trigger the breach we just cleared.
            rebased = self.broker.account()
            self._persist_risk_state(rebased)
        except Exception:
            log.error("New risk epoch committed but the post-reset risk state was not persisted", exc_info=True)
        log.warning(
            "New risk epoch %s started: drawdown baseline rebased to %.2f (account=%s); pre-reset peak %.2f, drawdown %.2f%%",
            new_epoch,
            new_baseline,
            account_id,
            pre_peak,
            pre_drawdown * 100.0,
        )
        runtime_metrics_store.record_event("risk_epoch_reset")
        alerts.emit(
            "risk_epoch_reset",
            message="New risk epoch started; drawdown baseline rebased",
            baseline_equity=new_baseline,
            account_id=account_id,
            previous_peak_equity=pre_peak,
            previous_drawdown_pct=pre_drawdown,
            epoch=new_epoch,
        )
        self._last_risk_epoch_reset = {"epoch": new_epoch, "baseline_equity": new_baseline, "changed": True}
        return new_baseline

    def reset_safety_lock(self) -> None:
        """Acknowledge a recoverable entry lock after broker/data verification.

        Operator reset may INITIATE the verification action for each market
        halt, but may never simply erase a halt flag. Each halt category has
        its own verified recovery condition:

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
            "ownership_drift",
            # Verified by the broker reconcile below: an unreachable broker
            # fails that reconcile, so the halt cannot clear while it persists.
            _ACCOUNT_FETCH_HALT_REASON,
            "final_market_event_queue_overflow",
            "reconciliation_failed",
            "reconciliation_bug_suspected",
            "portfolio_runtime_failure",
            "decision_worker_crashed",
            "trade_update_callback_failure",
            # Q-8: resettable only once the operator marker is gone (checked below).
            _OPERATOR_HALT_REASON,
        }
        with getattr(self, "_state_lock", threading.RLock()):
            reason = self._circuit_breaker_reason
            # Raise counters as they are now: a halt of the same reason raised
            # while the broker calls below run must stay in force (R2-1-2).
            raised_before = dict(getattr(self, "_halt_raised_seq", {}))
        if reason not in recoverable:
            raise ValueError(f"Safety lock is not operator-resettable: {reason or 'none'}")
        if reason == _OPERATOR_HALT_REASON and self._operator_halt_marker_active():
            raise ValueError("Operator halt marker is still present; run `parayabuck resume` before resetting")
        # A reset cannot revive a shared worker that exited: clearing the
        # breaker would report "running" while no decision/account/fill
        # processing runs. A dead decision loop is respawned after the
        # verification below (R9-9); the account and trade-update workers
        # still need a process restart.
        dead_shared = sorted(
            name
            for name, worker in dict(getattr(self, "_workers", {})).items()
            if name in {"pb-decisions", "pb-account", "pb-trade-updates"} and not worker.alive
        )
        dead_unrecoverable = [name for name in dead_shared if name != "pb-decisions"]
        if getattr(self, "_running", False) and dead_unrecoverable:
            raise ValueError(
                "Safety lock cannot be reset while shared workers are stopped "
                f"({','.join(dead_unrecoverable)}); restart the process to recover"
            )
        if not self._events.empty():
            raise ValueError("Market event queue must drain before resetting the safety lock")
        if self._not_ready_symbols:
            raise ValueError(
                "All symbol data must be ready before resetting the safety lock (not ready: "
                f"{','.join(sorted(self._not_ready_symbols))})"
            )
        released = False
        pending_restarts: list[tuple[Market, str]] = []
        try:
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
                self._last_clean_reconciliation_at = self._last_reconciliation_at
                account_id = self.broker.account_id or "unknown"
                entries = self.ledger.managed_entries_for_account(account_id)
                if any(item.status == "manual_intervention_required" for item in entries):
                    raise ValueError("A managed position still requires manual intervention")
                account = self.broker.account()
                self._publish_account_snapshot(account)
                # The breaker and its incident rows clear together under the risk
                # state lock: the account loop's _persist_risk_state re-saves
                # whatever halts it read under that lock, so clearing outside it
                # could resurrect a row the operator just cleared. Clearing waits
                # for the manual-intervention check and the account fetch above
                # (R9-6): a refused reset must leave the incidents in place.
                with getattr(self, "_risk_state_lock", threading.Lock()):
                    with getattr(self, "_state_lock", threading.RLock()):
                        # Re-checked under the state lock, before anything is
                        # cleared: the marker may have appeared, and the same reason
                        # may have been raised again, during the broker calls above.
                        if reason == _OPERATOR_HALT_REASON and self._operator_halt_marker_active():
                            raise ValueError(
                                "Operator halt marker is still present; run `parayabuck resume` before resetting"
                            )
                        if getattr(self, "_halt_raised_seq", {}).get(reason) != raised_before.get(reason):
                            raise ValueError(
                                f"Safety lock remains active: {reason} was raised again during the reset; "
                                "reset again once it is verified"
                            )
                        # Only the reset's own reason, and the rearmed reasons that are
                        # not in force in this process, are released. A rearmed reason
                        # still in force keeps its halt (R2-1-1).
                        live_reasons = set(getattr(self, "_halt_reasons", ()))
                        leftover_rearmed = sorted(_RESTART_REARMED_REASONS - live_reasons)
                    self._clear_reconciliation_incidents()
                    with getattr(self, "_state_lock", threading.RLock()):
                        # Lift only the reason being reset (and the leftover rearmed
                        # set): a halt another thread raised during the broker calls
                        # above stays in force, and so does every other active reason
                        # (R9-3).
                        self._release_halt_reason(reason)
                        for cleared in leftover_rearmed:
                            self._release_halt_reason(cleared)
                    released = True
                    # The operator verified THIS halt's recovery condition above;
                    # clear exactly its incident, never unrelated incidents (those
                    # need their own per-halt verification). A leftover rearmed row
                    # from an earlier worker state has no verification left to
                    # perform, and clearing it here keeps a reset from leaving a row
                    # that would halt the next start behind the operator's back.
                    for cleared in sorted({reason, *leftover_rearmed}):
                        self.ledger.clear_safety_incident(account_id, cleared)
                market_halts = getattr(self, "_market_entry_halts", None)
                if market_halts is not None:
                    pending_restarts = self._verify_market_halts(market_halts)
                with getattr(self, "_risk_state_lock", threading.Lock()):
                    self._entry_risk_latch = dict.fromkeys(getattr(self, "_entry_risk_latch", {}))
                self._persist_risk_state(account)
            if pending_restarts:
                self._confirm_stream_restarts(getattr(self, "_market_entry_halts", {}), pending_restarts)
            # A halt withholds the risk-increasing BUY phase of a two-phase
            # rebalance (F12). The reset is the operator-verified recovery point,
            # so resume the persisted rebalances here — exactly what the startup
            # resume does, against the same fresh, reconciled snapshot.
            self._resume_startup_rebalances(account=account)
            # R9-9: a decision loop that exited is restarted now that the reset
            # has verified the broker; without it the cleared breaker would report
            # "running" over a dead decision worker.
            self._respawn_decision_worker()
        except Exception as exc:
            if not released:
                raise
            # The halt was released but the reset did not finish. Halt again with
            # a reason the operator can reset, so the state never stays "halted"
            # with no breaker that no reset can clear (R2-1-3).
            self._raise_halt("portfolio_runtime_failure")
            log.exception("Safety lock reset failed after the release; runtime halted")
            raise ValueError(f"Safety lock reset failed after the release; runtime halted: {exc}") from exc
        finally:
            with getattr(self, "_state_lock", threading.RLock()):
                # A halt raised by another thread since the breaker was cleared
                # above set a new reason: keep that halt. A trade-update blip while
                # a non-halting breaker (protection_failure) was set left the
                # runtime "degraded"; the blip restore never passes a breaker, so
                # un-pin it here too, while the trade-update worker is alive.
                # Re-derived here as well, so a reset that raised after the
                # release cannot leave "halted" with no breaker (R2-1-3).
                trade_updates = dict(getattr(self, "_workers", {})).get("pb-trade-updates")
                state = getattr(self, "_state", None)
                restorable = state == "halted" or (
                    state == "degraded" and trade_updates is not None and trade_updates.alive
                )
                if getattr(self, "_running", False) and restorable and self._circuit_breaker_reason is None:
                    self._state = "running"
                retained = self._active_halt_reasons()
        if retained:
            log.warning("Operator reset safety lock: halts still in force: %s", ", ".join(retained))
        log.warning("Operator reset safety lock after successful reconciliation: %s", reason)
        # Journaled and mailed: a halt cleared by hand is a safety-state change the
        # owner must see. Halts still in force are listed in the retained field.
        alerts.emit(
            "safety_lock_reset",
            message=f"Safety lock reset after a verified reconcile: {reason} cleared",
            reason=reason,
            account_id=account_id,
            retained=", ".join(retained),
        )
        # A startup catch-up the halt blocked never re-runs by itself: retry it
        # now, against the same verified snapshot. Its own gates still apply.
        if any(_catch_up_blocked(runtime) for runtime in getattr(self, "_portfolio_runtimes", ())):
            try:
                self._run_startup_catch_up(account=account, blocked_only=True)
            except Exception as exc:
                # Same fail-closed contract as a scheduled decision failure.
                self._raise_halt("portfolio_runtime_failure")
                log.exception("Startup catch-up retry after the safety-lock reset failed closed")
                runtime_metrics_store.record_event("circuit_breaker")
                alerts.emit(
                    "circuit_breaker",
                    message="Startup catch-up retry failed closed; runtime halted",
                    reason="portfolio_runtime_failure",
                    error=str(exc),
                )
                raise ValueError(f"Safety lock reset, but the startup catch-up retry failed: {exc}") from exc

    def _verify_market_halts(self, market_halts: dict[Market, str | None]) -> list[tuple[Market, str]]:
        """Clear each market halt only after its specific verified recovery.

        Runs INSIDE the reconcile lock; the successful ``broker.reconcile()``
        above already verified protection and order state, so per-halt
        verifications may rely on it. Unverified halts stay active and are
        reported to the operator. A restart-limit halt only re-arms its worker
        here; it is returned for ``_confirm_stream_restarts`` to clear once the
        reconcile lock is released (C2-10-2).

        Every dict access takes the state lock, so each individual
        read-modify-write is atomic against a concurrent halt writer (stream
        worker, rebalance advance): none of them can overwrite an update in
        flight. What is NOT guaranteed is atomicity across a whole
        verification — reading a halt and re-reading it after the recovery
        attempt is a pair of locked accesses, so a halt raised or replaced in
        between is left for the next reset instead of being reported in
        ``retained`` here. The lock is never held across the verifications
        themselves, which take the ingestion and portfolio locks and would
        invert the established lock order.
        """
        retained: list[str] = []
        pending_restarts: list[tuple[Market, str]] = []
        with getattr(self, "_state_lock", threading.RLock()):
            markets = list(market_halts)
        for market in markets:
            with getattr(self, "_state_lock", threading.RLock()):
                halt = market_halts.get(market)
            if halt is None:
                continue
            if halt.endswith("_restart_limit"):
                # Only a verified stream reconnect clears it: re-arm the worker
                # here, and confirm it is alive and delivering after the lock.
                if self._restart_stream_worker(market):
                    pending_restarts.append((market, halt))
                else:
                    retained.append(f"{market.value}:{halt}:worker_not_delivering")
                continue
            if halt == "rebalance_reconciliation_required":
                if self._rebalance_state_reconciled(market):
                    self._clear_market_halt_if_unchanged(market_halts, market, halt)
                else:
                    retained.append(f"{market.value}:{halt}:rebalance_state_unresolved")
                continue
            retained.append(f"{market.value}:{halt}:no_verification_path")
        if retained:
            log.warning("Market halts retained pending verification: %s", "; ".join(retained))
        return pending_restarts

    def _confirm_stream_restarts(
        self, market_halts: dict[Market, str | None], pending: list[tuple[Market, str]]
    ) -> None:
        """Clear each restart-limit halt whose re-armed stream worker is delivering.

        Runs after the reconcile lock is released: waiting for the worker must
        not hold the lock that reconcile, the risk resets and the stop path use.
        """
        retained: list[str] = []
        for market, halt in pending:
            if self._stream_restart_delivering(market):
                self._clear_market_halt_if_unchanged(market_halts, market, halt)
            else:
                retained.append(f"{market.value}:{halt}:worker_not_delivering")
        if retained:
            log.warning("Market halts retained pending verification: %s", "; ".join(retained))

    def _clear_market_halt_if_unchanged(
        self, market_halts: dict[Market, str | None], market: Market, verified: str
    ) -> None:
        """Clear ``market``'s halt only while it is still the one verified (C9-3).

        The verification ran unlocked; a halt another thread raised meanwhile
        is a new condition that this verification did not look at, so it must
        survive to the next reset instead of being erased here.
        """
        with getattr(self, "_state_lock", threading.RLock()):
            if market_halts.get(market) == verified:
                market_halts[market] = None

    def _restart_stream_worker(self, market: Market) -> bool:
        """Re-arm a restart-limited stream worker; True when it is alive or was spawned.

        A worker that hit its restart limit has exited; the operator reset
        spawns a fresh one. The spawn checks ``_running`` under ``_threads_lock``,
        the lock stop() snapshots the threads under, so no worker starts once a
        stop has begun (C2-10-2). It does not wait: ``_stream_restart_delivering``
        does, after the reconcile lock is released.
        """
        worker_name = _STREAM_WORKER_BY_MARKET[market]
        worker = self._workers.get(worker_name)
        if worker is not None and worker.alive:
            return True
        target = self.stream.run_stock
        with getattr(self, "_threads_lock", threading.Lock()):
            if not getattr(self, "_running", False):
                return False
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
        return True

    def _stream_restart_delivering(self, market: Market) -> bool:
        """Wait up to 5 s for a re-armed stream worker to come alive, then check delivery."""
        worker = self._workers.get(_STREAM_WORKER_BY_MARKET[market])
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if worker is not None and worker.alive:
                break
            time.sleep(0.05)
        return self._stream_worker_delivering(worker)

    def _stream_worker_delivering(
        self,
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
        # The NYSE schedule owns this: a holiday, a weekend or a 13:00 ET
        # early close owes no bars, so readiness must not report degraded for
        # windows that never trade.
        if not is_session_open(now):
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
        # No order rows: nothing of the block was sent, so it is resolved (an
        # empty ``all`` is true). Requiring rows kept the halt set forever.
        orders = repository.orders_for_decision(current.decision_id)
        return all(is_terminal_order_status(str(order["status"])) for order in orders)

    def _warmup(self) -> None:
        history = AlpacaHistoricalData(
            self.api_key,
            self.secret_key,
            stock_feed=self.app_config.alpaca.stock_historical_feed,
            data_delayed=self.app_config.alpaca.data_delayed,
            sip_embargo_minutes=self.app_config.signal_cross_check.sip_embargo_minutes,
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

    def _warmup_symbols(self, history: AlpacaHistoricalData, symbols: list[str], *, retry: bool) -> None:
        """Download split-adjusted signal history for ``symbols`` and seed it.

        Only the strategy signal cadences are fetched, on the SPLITS basis
        (the same basis as backtest historical data; bridged across live
        splits by ``data/adjustment.py``). Bars are seeded into every
        matching portfolio runtime under the portfolio lock.
        """
        group = list(symbols)
        for timeframe in sorted(self._strategy_timeframes):
            strategy_requirements = [
                runtime.strategy.minimum_history_bars + 5
                for runtime in self._portfolio_runtimes
                if runtime.market == Market.EQUITY and runtime.strategy.timeframe == timeframe
            ]
            if not strategy_requirements:
                continue
            requested_bars = max(strategy_requirements)
            for symbol_batch in _symbol_batches(group):
                try:
                    start = _warmup_start(timeframe, requested_bars)
                    outcome = BatchOutcome()
                    signal_bars = history.get_bars_batch(
                        symbol_batch,
                        timeframe,
                        start=start,
                        adjustment="splits",
                        outcome=outcome,
                    )
                    # A provider window with a missing session or a dropped row is
                    # never seeded: the decision-time verifier cannot see a hole that
                    # was never stored, so the signals would read one session further
                    # back. The symbol stays not ready and the warmup retry covers it.
                    incomplete = outcome.incomplete
                    for symbol in symbol_batch:
                        if symbol in incomplete:
                            log.warning(
                                "Warmup not ready %s %s: provider window incomplete, not seeded",
                                symbol,
                                timeframe,
                            )
                            continue
                        signal_tail = signal_bars.get(symbol, [])[-requested_bars:]
                        # Only the portfolio lock: seeding never touches the stream
                        # aggregators, and the stock stream must not wait behind it.
                        with self._portfolio_lock:
                            for runtime in self._portfolio_runtimes:
                                if runtime.strategy.timeframe == timeframe and runtime.market == Market.EQUITY:
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
                    if not retry and _is_transient_network_error(exc):
                        # The first warmup runs inside start(): a provider
                        # outage must reach its TransientStartupError path
                        # (discard + retry startup) instead of starting an
                        # engine with no history.
                        raise
                    log.warning(
                        "Warmup%s batch skipped %s symbols=%d: %s",
                        " retry" if retry else "",
                        timeframe,
                        len(symbol_batch),
                        exc,
                    )


_CATCH_UP_TRANSIENT_STATUSES = frozenset({"warming_up", "incomplete_strategy_batch"})
# Portfolio results that leave a session undecided because an open rebalance
# holds it. The batch is parked for the bounded retry instead of being lost.
# The open-rebalance return carries the advance's own status (D2-3-1), so
# every status that advance can return is listed here.
_HELD_SESSION_STATUSES = frozenset(
    {
        "rebalance_in_progress",
        "rebalance_blocked",
        "rebalance_complete",
        "rebalance_waiting_for_slot",
        "replan_snapshot_stale",
        "stale_decision_buy_held",
        "submit_blocked",
        "replan_sell_submitted",
        "buy_phase_submitted",
        "replan_order_in_flight",
    }
)


def _catch_up_blocked(runtime: object) -> bool:
    """True when a runtime's startup catch-up was blocked by a gate.

    Besides the ``blocked:`` gate statuses, transient non-deciding outcomes
    (``warming_up``, ``incomplete_strategy_batch``) are retried: warmup may
    have completed since, and no later path re-invokes startup catch-up
    in-process.
    """
    status = str(getattr(runtime, "startup_catch_up_status", None) or "")
    return status.startswith("blocked:") or status in _CATCH_UP_TRANSIENT_STATUSES


def _heal_persisted_high_water(repository: Any, account_id: str, active_peak: float, epoch: int) -> None:
    """Lower a persisted high-water that still sits above the active epoch's peak.

    Only a reset (``reset_high_water``) ever lowers it, so a stale value left by
    a crash inside the epoch reset would otherwise re-latch the drawdown halt
    on every restart. A value at or below the active peak is left alone.
    """
    if repository.high_water(account_id, epoch=epoch) > active_peak:
        repository.reset_high_water(account_id, active_peak, epoch=epoch)


def _symbol_batches(symbols: list[str]) -> tuple[list[str], ...]:
    return tuple(
        symbols[offset : offset + _WARMUP_SYMBOL_BATCH_SIZE]
        for offset in range(0, len(symbols), _WARMUP_SYMBOL_BATCH_SIZE)
    )


def _warmup_start(timeframe: str, bars: int) -> str:
    days = bars_to_calendar_days(timeframe, bars)
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


__all__ = ["EngineRuntime", "TransientStartupError"]
