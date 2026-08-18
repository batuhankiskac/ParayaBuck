from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from requests.exceptions import RequestException

from parayabuck.config import Config, RuntimeMode
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Market,
)
from parayabuck.data import AlpacaBarStream, AlpacaHistoricalData, TimeframeAggregator
from parayabuck.execution.alpaca import (
    AlpacaBroker,
    AmbiguousOrderState,
    CryptoPriceObservation,
    OwnershipDriftError,
    ProtectionReconciliationError,
)
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream, TradeUpdate
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.live_safety import LiveOperatorApproval
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    NEW_YORK,
    is_regular_equity_time,
)
from parayabuck.monitoring import alerts
from parayabuck.monitoring import metrics as runtime_metrics_store
from parayabuck.portfolio import portfolio_strategy_from, validate_strategy_timeframe
from parayabuck.portfolio.runtime_service import (
    REBALANCE_PHASE_BLOCKED,
    PortfolioRuntime,
    PortfolioStateRepository,
    is_terminal_order_status,
)
from parayabuck.risk.state import RiskState
from parayabuck.runtime_state import RuntimeMarketState
from parayabuck.storage import SQLiteStore
from parayabuck.universe import (
    ensure_active_universes,
    load_active_crypto_symbols,
    load_active_symbols,
    refresh_crypto_universe,
    refresh_equity_universe,
)
from parayabuck.universe.crypto_scanner import (
    crypto_scanner_config_digest,
    validate_active_crypto_universe,
)
from parayabuck.universe.scanner import scanner_config_digest, validate_active_universe
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.runtime")


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
            if status is not None and status != 429 and status < 500:
                return False
            return True
        if isinstance(current, (ConnectionError, TimeoutError)):
            return True
        current = current.__cause__ or current.__context__
    return False


_WARMUP_SYMBOL_BATCH_SIZE = 8
_WARMUP_RETRY_SECONDS = 600.0
# Per-market scanner-refresh backoff (seconds) after a failed scheduled
# refresh: ~5 min, then ~15, ~30, capped at ~60 min per consecutive failure.
_UNIVERSE_RETRY_BACKOFF_SECONDS = (300.0, 900.0, 1800.0, 3600.0)


def required_aggregation_timeframes(
    execution_timeframe: str,
    market_state_timeframe: str,
    strategy_specs: Sequence[tuple[object, tuple[str, ...], float]],
) -> frozenset[str]:
    """The union of cadences the bar aggregation pipeline must produce.

    Includes the execution and market-state cadences, every enabled
    strategy's primary signal timeframe, AND every strategy-declared
    ``required_timeframes`` (StrategySpec) so a strategy that needs a second
    series gets its bars aggregated and seeded like any other cadence.
    """
    timeframes: set[str] = {execution_timeframe, market_state_timeframe}
    for strategy, _symbols, _cap in strategy_specs:
        timeframes.add(strategy.timeframe)  # type: ignore[attr-defined]
        timeframes.update(strategy.required_timeframes)  # type: ignore[attr-defined]
    return frozenset(timeframes)


@dataclass
class WorkerHealth:
    started_at: datetime
    last_heartbeat: datetime
    last_event_at: datetime | None = None
    last_error: str | None = None
    restart_count: int = 0
    alive: bool = False


@dataclass(frozen=True)
class RuntimeHealth:
    state: str
    healthy: bool
    circuit_breaker_reason: str | None
    queue_size: int
    queue_age_seconds: float
    dropped_events: int
    coalesced_events: int
    workers: dict[str, dict[str, object]]
    markets: dict[str, dict[str, object]]
    strategy_status: str = "configured"
    strategies: tuple[dict[str, object], ...] = ()
    last_reconciliation_at: str | None = None
    universe_refresh: dict[str, object] = field(default_factory=dict)
    entry_risk_latch: dict[str, str | None] = field(default_factory=dict)


def _portfolio_catch_up_health(runtime: object) -> dict[str, str | None]:
    """Health-only catch-up snapshot for one portfolio runtime (or none).

    ``status`` is the last startup_catch_up result status (None before the
    first attempt), ``at`` the UTC attempt timestamp, and
    ``next_boundary_after`` the next cadence boundary strictly after the last
    processed timestamp (None when no history has been processed yet).
    """
    if runtime is None:
        return {"status": None, "at": None, "next_boundary_after": None}
    catch_up_at = getattr(runtime, "startup_catch_up_at", None)
    previous_timestamp = getattr(runtime, "previous_timestamp", None)
    next_boundary: str | None = None
    if previous_timestamp is not None:
        policy = getattr(runtime, "policy", None)
        if policy is not None:
            next_boundary = policy.next_boundary_after(previous_timestamp).isoformat()
    return {
        "status": getattr(runtime, "startup_catch_up_status", None),
        "at": catch_up_at.isoformat() if catch_up_at is not None else None,
        "next_boundary_after": next_boundary,
    }


@dataclass(frozen=True)
class _Event:
    kind: str
    bar: Bar
    received_at: datetime


class _EventQueue:
    """Bounded queue with domain-aware coalescing and overflow policy."""

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self._items: deque[_Event | None] = deque()
        self._condition = threading.Condition()

    def put_nowait(self, item: _Event | None) -> None:
        with self._condition:
            if len(self._items) >= self.maxsize:
                raise queue.Full
            self._items.append(item)
            self._condition.notify()

    def get(self, timeout: float | None = None) -> _Event | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._items:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise queue.Empty
                self._condition.wait(remaining)
            return self._items.popleft()

    def get_nowait(self) -> _Event | None:
        with self._condition:
            if not self._items:
                raise queue.Empty
            return self._items.popleft()

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)

    def empty(self) -> bool:
        return self.qsize() == 0

    def put_market_event(self, event: _Event) -> str:
        with self._condition:
            if len(self._items) < self.maxsize:
                self._items.append(event)
                self._condition.notify()
                return "added"
            key = (event.kind, event.bar.symbol, event.bar.timeframe, event.bar.timestamp)
            for index in range(len(self._items) - 1, -1, -1):
                candidate = self._items[index]
                if candidate is None:
                    continue
                candidate_key = (
                    candidate.kind,
                    candidate.bar.symbol,
                    candidate.bar.timeframe,
                    candidate.bar.timestamp,
                )
                if candidate_key == key:
                    self._items[index] = event
                    return "coalesced"
            if event.kind == "primary":
                return "overflow"
            victim = None
            for index in range(len(self._items) - 1, -1, -1):
                candidate = self._items[index]
                if candidate is not None and candidate.kind != "primary":
                    victim = index
                    break
            if victim is None:
                return "overflow"
            self._items[victim] = event
            return "replaced"

    def snapshot(self) -> tuple[_Event | None, ...]:
        with self._condition:
            return tuple(self._items)

    def oldest_received_at(self) -> datetime | None:
        with self._condition:
            received = [item.received_at for item in self._items if item is not None]
            return min(received) if received else None


class EngineRuntime:
    """Web-manageable streaming runtime for the ParayaBuck engine."""

    def __init__(self, api_key: str, secret_key: str, config: Config) -> None:
        self._validate_execution_mode(config)
        self.api_key = api_key
        self.secret_key = secret_key
        self.app_config = config
        self.config = config.engine
        self.mode = config.runtime.mode
        self.universe_preflight = ensure_active_universes(config, api_key, secret_key)
        active_equities = (
            load_active_symbols(config.universe.equity, log_rejection=False)
            if config.universe.equity.enabled
            else ()
        )
        active_crypto = (
            load_active_crypto_symbols(config.universe.crypto, log_rejection=False)
            if config.universe.crypto.enabled
            else ()
        )
        self._active_crypto_symbols = active_crypto if config.strategy.crypto.enabled else ()
        # Live observation membership is driven by the enabled strategies'
        # universe symbols plus managed-position/open-order pins. A market
        # whose strategy is disabled streams nothing from scanner discovery
        # (which stays enabled independently); pins are added later by
        # _pin_managed_symbols when managed inventory exists.
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
            strategy = portfolio_strategy_from(
                selection.name, selection.parameters, timeframe=selection.timeframe
            )
            if strategy.market != market:
                raise ValueError(f"Configured {market.value} strategy has the wrong market")
            # The strategy owns its signal timeframe; the configured value must
            # match one of its validated timeframes (never silently
            # reinterpreted). portfolio_strategy_from already enforces this,
            # and validate_strategy_timeframe keeps the invariant explicit.
            validate_strategy_timeframe(strategy, selection.timeframe or strategy.timeframe)
            strategy_specs.append((strategy, tuple(symbols), selection.maximum_allocation))
        self._strategy_specs = tuple(strategy_specs)
        if not self._strategy_specs:
            raise ValueError("At least one enabled strategy needs an active universe")
        self.strategy_status = "configured:" + ",".join(
            f"{strategy.market.value}/{strategy.name}"
            for strategy, _symbols, _cap in strategy_specs
        )
        self.paper = self.mode == RuntimeMode.PAPER
        database_path = config.runtime.decision_db_path
        self.store = SQLiteStore(database_path)
        self.ledger = ExecutionLedger(database_path, store=self.store)
        self._portfolio_repository = PortfolioStateRepository(database_path, store=self.store)
        self.broker = AlpacaBroker(
            api_key,
            secret_key,
            paper=self.paper,
            manage_external_positions=self.config.manage_external_positions,
            ledger=self.ledger,
            portfolio_owned_quantities=lambda: self._portfolio_repository.owned_quantities(
                self.broker.account_id or "unknown"
            ),
            portfolio_open_orders=lambda: self._portfolio_repository.open_orders(
                self.broker.account_id or "unknown"
            ),
            portfolio_fill_recovered=self._recover_portfolio_fill,
            portfolio_stop_recorded=lambda symbol, market, qty, client_order_id, reference_price: (
                self._portfolio_repository.record_protective_order(
                    self.broker.account_id or "unknown",
                    client_order_id,
                    symbol,
                    market,
                    qty,
                    reference_price,
                )
            ),
            portfolio_flat_reconciled=lambda symbol, market: (
                self._portfolio_repository.clear_stale_ownership(
                    self.broker.account_id or "unknown", symbol, market
                )
                or None
            ),
            crypto_stop_limit_offset_bps=self.config.crypto_stop_limit_offset_bps,
            crypto_emergency_exit_bps=self.config.crypto_emergency_exit_bps,
            crypto_stop_cancel_timeout_seconds=self.config.crypto_stop_cancel_timeout_seconds,
            crypto_stop_cancel_poll_interval_seconds=(
                self.config.crypto_stop_cancel_poll_interval_seconds
            ),
        )
        self._portfolio_runtimes: tuple[PortfolioRuntime, ...] = ()
        self.market_state = RuntimeMarketState(
            self.config,
            allowed_trade_symbols={
                *(
                    symbol
                    for symbol in active_equities
                    if config.universe.equity.trade_reference_symbols
                    or symbol not in config.universe.equity.reference_symbols
                ),
                *active_crypto,
            },
        )
        self._consecutive_losses = 0
        self.stream = AlpacaBarStream(api_key, secret_key, config.alpaca.stock_stream_feed)
        self.trading_updates = AlpacaTradingUpdateStream(
            api_key, secret_key, self.paper, self._on_trade_update
        )
        self._strategy_timeframes = {
            strategy.timeframe for strategy, _symbols, _cap in strategy_specs
        }
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
        self._last_reconciliation_at: datetime | None = None
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
        alerts.configure_alerting(
            enabled=config.alerting.enabled,
            webhook_url=config.alerting.webhook_url,
            events=config.alerting.events,
        )
        self._runtime_session_id: str | None = None
        self._reconcile_failed = False

    @staticmethod
    def _validate_execution_mode(config: Config) -> None:
        """Reject non-execution modes and unsafe live configuration before I/O."""
        if config.runtime.mode not in {RuntimeMode.PAPER, RuntimeMode.LIVE}:
            raise RuntimeError(
                f"EngineRuntime does not support runtime.mode={config.runtime.mode.value}"
            )
        if config.runtime.mode == RuntimeMode.LIVE:
            if not config.live.enabled:
                raise RuntimeError("Live runtime requires live.enabled=true")
            if config.live.hard_dollar_cap <= 0:
                raise RuntimeError("Live runtime requires a positive live.hard_dollar_cap")

    def _initial_universe_halt(self, market: Market) -> str | None:
        if market == Market.EQUITY:
            equity_config = self.app_config.universe.equity
            status = self.universe_preflight.equity_status
            enabled = equity_config.enabled
            dynamic = equity_config.dynamic_selection_enabled
            fallback_allowed = equity_config.allow_static_fallback_trading
        else:
            crypto_config = self.app_config.universe.crypto
            status = self.universe_preflight.crypto_status
            enabled = crypto_config.enabled
            dynamic = crypto_config.dynamic_selection_enabled
            fallback_allowed = crypto_config.allow_static_fallback_trading
        if enabled and dynamic and status != "success" and not fallback_allowed:
            return "invalid_dynamic_universe"
        return None

    @property
    def is_running(self) -> bool:
        return self.health().healthy

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
        account_id = (
            getattr(broker, "account_id", None) if broker is not None else None
        ) or "unknown"
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
        # Some lightweight diagnostics construct a runtime via ``__new__``.
        # A temporary lock keeps health reporting safe for those snapshots too.
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
        queue_age = (
            max(0.0, (now - self._oldest_received_at).total_seconds())
            if self._oldest_received_at is not None
            else 0.0
        )
        required = {"pb-decisions", "pb-trade-updates", "pb-account"}
        if "pb-universe-refresh" in workers:
            required.add("pb-universe-refresh")
        if market_enabled.get(Market.EQUITY, False) and symbols_by_market.get(Market.EQUITY):
            required.add("pb-stocks")
        if market_enabled.get(Market.CRYPTO, False) and symbols_by_market.get(Market.CRYPTO):
            required.add("pb-crypto")
        heartbeat_ttl = max(30.0, self.config.account_poll_interval_seconds * 3)

        def worker_healthy(name: str) -> bool:
            worker = workers.get(name)
            if worker is None or not worker.alive:
                return False
            if name == "pb-trade-updates":
                return True
            if name in {"pb-stocks", "pb-crypto"}:
                market = Market.EQUITY if name == "pb-stocks" else Market.CRYPTO
                event_required = market == Market.CRYPTO or is_regular_equity_time(now)
                if not event_required:
                    return True
                return bool(
                    worker.last_event_at
                    and (now - worker.last_event_at).total_seconds() <= max(180.0, heartbeat_ttl)
                )
            return (now - worker.last_heartbeat).total_seconds() <= heartbeat_ttl

        alive = all(worker_healthy(name) for name in required)
        account_fresh = (
            self._last_account_at is not None
            and (now - self._last_account_at).total_seconds()
            <= self.config.maximum_account_age_for_entry_seconds
        )
        # A scanner failure is operationally critical only for markets that
        # are actually relevant: the strategy is enabled, a managed position
        # exists, or an open managed order/protection exists. A disabled
        # market with no positions failing its discovery API must not take
        # the whole trading runtime down — it is informational/degraded.
        scanner_status = dict(getattr(self, "_universe_refresh_status", {}))
        scanner_degraded = any(
            status in {"refresh_failed", "apply_failed"}
            and self._market_operationally_relevant(market)
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
        state = self._state
        if self._running and not healthy and state == "running":
            state = "degraded"
        market_health: dict[str, dict[str, object]] = {}
        portfolio_runtime_by_market = {
            runtime.market: runtime for runtime in getattr(self, "_portfolio_runtimes", ())
        }
        for market, worker_name in (
            (Market.EQUITY, "pb-stocks"),
            (Market.CRYPTO, "pb-crypto"),
        ):
            enabled = market_enabled.get(market, False)
            symbols = symbols_by_market.get(market, set())
            worker = workers.get(worker_name)
            not_ready_symbols: set[str] = getattr(self, "_not_ready_symbols", set())
            market_not_ready = symbols & not_ready_symbols
            ready_symbol_count = len(symbols) - len(market_not_ready)
            # A sparse symbol (for example a low-volume IEX instrument) must
            # not keep an otherwise usable market in warmup forever. The
            # decision loop already excludes not-ready symbols individually.
            ready = not enabled or not symbols or ready_symbol_count > 0
            event_required = market == Market.CRYPTO or is_regular_equity_time(now)
            stream_healthy = (
                not enabled
                or not symbols
                or bool(
                    worker
                    and worker.alive
                    and (
                        not event_required
                        or (
                            worker.last_event_at is not None
                            and (now - worker.last_event_at).total_seconds()
                            <= max(180.0, heartbeat_ttl)
                        )
                    )
                )
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
                "scanner_critical": self._market_operationally_relevant(
                    market, strategy_enabled=market_enabled
                ),
                "catch_up": _portfolio_catch_up_health(portfolio_runtime_by_market.get(market)),
            }
        last_reconciliation = getattr(self, "_last_reconciliation_at", None)
        last_universe_refresh = getattr(self, "_last_universe_refresh_at", None)
        next_universe_refresh = getattr(self, "_next_universe_refresh_at", None)
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
            last_reconciliation_at=(
                last_reconciliation.isoformat() if last_reconciliation is not None else None
            ),
            universe_refresh={
                "last_refresh_at": (
                    last_universe_refresh.isoformat() if last_universe_refresh is not None else None
                ),
                "status": dict(getattr(self, "_universe_refresh_status", {})),
                "errors": dict(getattr(self, "_last_universe_refresh_error", {})),
                "next_refresh_at": (
                    next_universe_refresh.isoformat() if next_universe_refresh is not None else None
                ),
                "retry_at": {
                    market.value: (
                        datetime.now(UTC) + timedelta(seconds=max(0.0, deadline - time.monotonic()))
                    ).isoformat()
                    for market, deadline in getattr(self, "_universe_retry_at", {}).items()
                },
                "retry_attempt": {
                    market.value: attempt
                    for market, attempt in getattr(self, "_universe_retry_attempt", {}).items()
                },
            },
            entry_risk_latch={
                market.value: reason
                for market, reason in getattr(self, "_entry_risk_latch", {}).items()
            },
        )

    def start(self) -> None:
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
        self._stop_event.clear()
        self._state = "starting"
        try:
            self.broker.reconcile()
            self._last_reconciliation_at = datetime.now(UTC)
            # A verified startup reconciliation is the same recovery
            # condition the periodic path uses: reconcile() raises on
            # ownership drift / protection / ambiguous state, so success
            # means every persisted PERSISTENT_INCIDENT is resolved and a
            # restart must not resurrect it.
            self.ledger.clear_safety_incident(self.broker.account_id or "unknown")
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
                else "reconciliation_failed"
            )
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
        self._portfolio_runtimes = tuple(
            PortfolioRuntime(
                strategy,
                symbols,
                self.broker,
                self._portfolio_repository,
                mode=self.mode.value,
                maximum_allocation=maximum_allocation,
                risk_limits=portfolio_limits,
                universe_version=(
                    scanner_config_digest(self.app_config.universe.equity)
                    if strategy.market == Market.EQUITY
                    else crypto_scanner_config_digest(self.app_config.universe.crypto)
                ),
                account_id=self.broker.account_id or "unknown",
                live_dollar_cap=configured_live_cap,
                execution_policy=PortfolioExecutionPolicy(
                    rebalance_band_pct=self.app_config.execution.rebalance_band_pct,
                    minimum_order_notional=self.app_config.execution.minimum_order_notional,
                ),
            )
            for strategy, symbols, maximum_allocation in self._strategy_specs
        )
        if not self.paper:
            secret = os.getenv(self.app_config.live.operator_approval_key_env, "")
            approval = LiveOperatorApproval.load_verified(
                self.app_config.live.operator_approval_path,
                secret=secret,
                account_id=self.broker.account_id or "unknown",
                configuration_digests=tuple(
                    runtime.configuration_digest for runtime in self._portfolio_runtimes
                ),
            )
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
        # Restart recovery: a two-phase rebalance interrupted between phases
        # must resume here — SELL state is reconciled from the repository, a
        # fresh account is taken, and the deferred BUY phase continues exactly
        # once. No duplicate orders: the deferred BUY rows are only created at
        # submission time with deterministic broker idempotency keys.
        for runtime in self._portfolio_runtimes:
            if self._circuit_breaker_reason is not None:
                # A restored PERSISTENT_INCIDENT (cross-day restart) must
                # hold until its verified recovery; the persisted rebalance
                # stays deferred and resumes once the breaker clears.
                log.warning(
                    "Restart rebalance resume deferred: circuit breaker active (%s)",
                    self._circuit_breaker_reason,
                )
                break
            self._advance_portfolio_rebalance(runtime.market)
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
        if any(
            config.enabled and config.dynamic_selection_enabled
            for config in (
                self.app_config.universe.equity,
                self.app_config.universe.crypto,
            )
        ):
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
        for thread in self._threads:
            thread.start()
        try:
            self._warmup()
        except Exception as exc:
            self._state = "error"
            self.stop()
            if _is_transient_network_error(exc):
                raise TransientStartupError(
                    f"Startup warmup deferred after transient network failure: {exc}"
                ) from exc
            raise
        finally:
            self._warming_up = False
        try:
            account = self.broker.account()
            self._last_account_snapshot = account
            self._last_account_snapshot_at = datetime.now(UTC)
            self._last_account_at = datetime.now(UTC)
            self._persist_risk_state(account)
        except Exception as exc:
            self._state = "error"
            self.stop()
            if _is_transient_network_error(exc):
                raise TransientStartupError(
                    f"Startup account fetch deferred after transient network failure: {exc}"
                ) from exc
            raise
        # Seeded warmup history exists and the account snapshot is fresh: a
        # mid-period start must not leave a fresh account in cash until the
        # next cadence boundary (backtest decides on its first usable bar).
        # Catch-up failures are logged, never fatal — normal cadence resumes.
        self._run_startup_catch_up()
        self._refresh_readiness()
        self._state = "running" if self._has_ready_symbols() else "warming_up"
        record_session = getattr(self.ledger, "record_runtime_session", None)
        if record_session is not None:
            # Observability-only; fake ledgers in unit tests may omit it.
            self._runtime_session_id = record_session(
                self.broker.account_id or "unknown", self.mode.value
            )
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
        next_equity = self._next_equity_refresh_at(datetime.now(UTC))
        self._next_universe_refresh_at = self._next_universe_deadline(next_interval, next_equity)
        try:
            while self._running:
                worker.last_heartbeat = datetime.now(UTC)
                now_mono = time.monotonic()
                now = datetime.now(UTC)
                interval_due = now_mono >= next_interval
                equity_due = next_equity is not None and now >= next_equity
                retry_due = [
                    market
                    for market, deadline in self._universe_retry_at.items()
                    if now_mono >= deadline
                ]
                if interval_due or equity_due or retry_due:
                    markets_to_refresh: set[Market] = set()
                    if interval_due:
                        # Crypto trades 24/7: keep a fixed cadence.
                        markets_to_refresh.add(Market.CRYPTO)
                    if equity_due:
                        # Equity refreshes pre-market only, after the
                        # previous session's data is final, so a mid-session
                        # partial daily bar can never change the universe.
                        markets_to_refresh.add(Market.EQUITY)
                    markets_to_refresh.update(retry_due)
                    # Markets refresh independently: a failing market (or an
                    # apply failure that re-raises) must not skip the other
                    # market's slot, and its retry state stays per-market.
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
                    if equity_due:
                        next_equity = self._next_equity_refresh_at(now)
                    self._next_universe_refresh_at = self._next_universe_deadline(
                        next_interval, next_equity
                    )
                remaining = next_interval - now_mono
                if next_equity is not None:
                    remaining = min(remaining, (next_equity - now).total_seconds())
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

        Equity retries are capped at the next pre-market slot when the backoff
        deadline would land inside a regular session: a mid-session refresh
        would read a partial daily bar into the scanner metrics and could
        change the universe, which the pre-market-only cadence exists to
        prevent. Crypto trades 24/7 and retries on the plain backoff.
        """
        if ok:
            self._universe_retry_at.pop(market, None)
            self._universe_retry_attempt.pop(market, None)
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
        delay = _UNIVERSE_RETRY_BACKOFF_SECONDS[
            min(attempt, len(_UNIVERSE_RETRY_BACKOFF_SECONDS) - 1)
        ]
        deadline = time.monotonic() + delay
        if market == Market.EQUITY:
            candidate = datetime.now(UTC) + timedelta(seconds=delay)
            if is_regular_equity_time(candidate):
                next_premarket = self._next_equity_refresh_at(candidate)
                if next_premarket is not None:
                    deadline = time.monotonic() + max(
                        0.0, (next_premarket - datetime.now(UTC)).total_seconds()
                    )
                    log.warning(
                        "Equity scanner retry deferred to next pre-market slot %s "
                        "(backoff %ds would land mid-session)",
                        next_premarket.isoformat(),
                        delay,
                    )
        self._universe_retry_at[market] = deadline
        self._universe_retry_attempt[market] = attempt + 1

    def _next_universe_deadline(
        self, next_interval: float, next_equity: datetime | None
    ) -> datetime:
        now_mono = time.monotonic()
        interval_remaining = max(0.0, next_interval - now_mono)
        deadline = datetime.now(UTC) + timedelta(seconds=interval_remaining)
        if next_equity is not None:
            deadline = min(deadline, next_equity)
        for retry_at in getattr(self, "_universe_retry_at", {}).values():
            retry_remaining = max(0.0, retry_at - now_mono)
            deadline = min(deadline, datetime.now(UTC) + timedelta(seconds=retry_remaining))
        return deadline

    def _next_equity_refresh_at(self, now: datetime) -> datetime | None:
        """Next pre-market slot (06:00 ET) with the prior session finalized.

        ``None`` when the equity universe is not dynamically refreshed, so the
        loop never waits on a market that is not running.
        """
        equity_config = self.app_config.universe.equity
        if not (equity_config.enabled and equity_config.dynamic_selection_enabled):
            return None
        local = now.astimezone(NEW_YORK)
        premarket = local.replace(hour=6, minute=0, second=0, microsecond=0)
        if local.weekday() < 5 and local < premarket:
            return premarket.astimezone(UTC)
        days_ahead = 1
        while (local + timedelta(days=days_ahead)).weekday() >= 5:
            days_ahead += 1
        return (premarket + timedelta(days=days_ahead)).astimezone(UTC)

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
        for market in list(self._market_entry_halts):
            if self._market_entry_halts.get(market) == "subscription_state_unknown":
                self._market_entry_halts[market] = None
        log.warning("Stream membership reconnected from the authoritative desired set")

    def _refresh_runtime_universes(self, target: Market | None = None) -> None:
        """Refresh the scanner universes, optionally for one market only.

        ``target=None`` refreshes every enabled dynamic universe (startup and
        backfill paths); the scheduled loop passes a single market so equity
        can run on a pre-market cadence while crypto keeps its own interval.
        """
        if getattr(self, "_resync_pending", False):
            try:
                self._resync_stream_if_needed()
            except Exception as exc:
                log.error(
                    "Stream resync failed; subscription state stays unknown and "
                    "entries stay halted: %s",
                    exc,
                )
        desired: dict[Market, tuple[str, ...]] = {}

        def refresh_one(market: Market, operation: Callable[[], tuple[str, ...]]) -> None:
            try:
                selected = operation()
                if not selected:
                    raise RuntimeError(f"Scanner produced no valid {market.value} universe")
                desired[market] = tuple(selected)
                self._universe_refresh_status[market.value] = "success"
                self._last_universe_refresh_error[market.value] = None
            except Exception as exc:
                self._universe_refresh_status[market.value] = "refresh_failed"
                self._last_universe_refresh_error[market.value] = str(exc)
                log.error(
                    "%s universe refresh failed; active membership unchanged: %s", market, exc
                )
                if market == Market.EQUITY:
                    _symbols, snapshot_status = validate_active_universe(
                        self.app_config.universe.equity
                    )
                    fallback_allowed = self.app_config.universe.equity.allow_static_fallback_trading
                else:
                    _symbols, snapshot_status = validate_active_crypto_universe(
                        self.app_config.universe.crypto
                    )
                    fallback_allowed = self.app_config.universe.crypto.allow_static_fallback_trading
                if snapshot_status != "success" and not fallback_allowed:
                    self._market_entry_halts[market] = "invalid_dynamic_universe"

        equity_config = self.app_config.universe.equity
        if (
            target in {None, Market.EQUITY}
            and equity_config.enabled
            and equity_config.dynamic_selection_enabled
        ):

            def refresh_equity() -> tuple[str, ...]:
                refresh_equity_universe(self.app_config, self.api_key, self.secret_key)
                return load_active_symbols(equity_config, log_rejection=False)

            refresh_one(Market.EQUITY, refresh_equity)
        crypto_config = self.app_config.universe.crypto
        if (
            target in {None, Market.CRYPTO}
            and crypto_config.enabled
            and crypto_config.dynamic_selection_enabled
        ):

            def refresh_crypto() -> tuple[str, ...]:
                refresh_crypto_universe(self.app_config, self.api_key, self.secret_key)
                return load_active_crypto_symbols(crypto_config, log_rejection=False)

            refresh_one(Market.CRYPTO, refresh_crypto)
        if desired:
            try:
                self._apply_runtime_universes(desired)
            except Exception as exc:
                for market in desired:
                    self._universe_refresh_status[market.value] = "apply_failed"
                    self._last_universe_refresh_error[market.value] = str(exc)
                raise
            else:
                for market in desired:
                    if self._market_entry_halts.get(market) == "invalid_dynamic_universe":
                        self._market_entry_halts[market] = None
        self._last_universe_refresh_at = datetime.now(UTC)

    def _apply_runtime_universes(
        self, desired_by_market: dict[Market, tuple[str, ...]]
    ) -> dict[Market, dict[str, tuple[str, ...]]]:
        """Apply scanner changes while retaining every owned/open-order symbol."""
        account = self.broker.account()
        account_id = self.broker.account_id or "unknown"
        manage_external = bool(
            getattr(getattr(self, "config", None), "manage_external_positions", False)
        )
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
        protected.update(
            item.canonical_symbol for item in self.ledger.managed_entries_for_account(account_id)
        )
        applied: dict[Market, tuple[str, ...]] = {}
        changes: dict[Market, dict[str, tuple[str, ...]]] = {}
        strategy_enabled = getattr(self, "_strategy_enabled", {})
        for market, requested in desired_by_market.items():
            current = self._symbols_by_market[market]
            market_protected = tuple(
                sorted(
                    symbol for symbol in protected if is_crypto(symbol) == (market == Market.CRYPTO)
                )
            )
            if market == Market.EQUITY:
                equity_config = self.app_config.universe.equity
                # Reference symbols are strategy inputs; with the strategy
                # disabled, scanner discovery stays enabled but observation
                # keeps only the managed/protection pins.
                configured_pins = (
                    equity_config.reference_symbols if strategy_enabled.get(market, True) else ()
                )
                maximum_symbols = equity_config.maximum_stream_symbols
            else:
                crypto_config = self.app_config.universe.crypto
                configured_pins = (
                    crypto_config.pinned_symbols if strategy_enabled.get(market, True) else ()
                )
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
        staged: dict[tuple[str, str], list[Bar]] = {}
        try:
            if newly_added and self._historical is not None:
                staged = (
                    self._warmup_symbols(
                        self._historical, newly_added, retry=False, staged_only=True
                    )
                    or {}
                )
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
                previous_by_market = {
                    market: set(self._symbols_by_market[market]) for market in applied
                }
                previous_portfolio_symbols = {
                    runtime.market: getattr(runtime, "symbols", ())
                    for runtime in self._portfolio_runtimes
                }
                for market, selected in applied.items():
                    self._symbols_by_market[market] = set(selected)
                    for runtime in self._portfolio_runtimes:
                        if runtime.market == market:
                            runtime.update_symbols(selected)
                # New members were staged during warmup but could only be
                # seeded now that the runtimes include them; seeding inside
                # this transaction keeps the transition atomic.
                for (symbol, timeframe), bars in staged.items():
                    self.market_state.seed(bars)
                    for runtime in self._portfolio_runtimes:
                        if runtime.strategy.timeframe == timeframe and (
                            is_crypto(symbol) == (runtime.market == Market.CRYPTO)
                        ):
                            runtime.seed(bars)
                self.symbols = sorted(
                    self._symbols_by_market[Market.EQUITY] | self._symbols_by_market[Market.CRYPTO]
                )
                self._expected_all_symbols = set(self.symbols)
                self._not_ready_symbols.difference_update(removed_all)
                if self.market_state.allowed_trade_symbols is not None:
                    references = set(self.app_config.universe.equity.reference_symbols)
                    self.market_state.allowed_trade_symbols = {
                        *self._symbols_by_market[Market.CRYPTO],
                        *(
                            symbol
                            for symbol in self._symbols_by_market[Market.EQUITY]
                            if self.app_config.universe.equity.trade_reference_symbols
                            or symbol not in references
                        ),
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
                                log.exception(
                                    "Portfolio runtime restore failed for %s", market.value
                                )
                self.symbols = sorted(
                    self._symbols_by_market[Market.EQUITY] | self._symbols_by_market[Market.CRYPTO]
                )
                self._expected_all_symbols = set(self.symbols)
                self._not_ready_symbols = previous_not_ready
                if self.market_state.allowed_trade_symbols is not None:
                    self.market_state.allowed_trade_symbols = (
                        previous_allowed_trade
                        if previous_allowed_trade is not None
                        else self.market_state.allowed_trade_symbols
                    )
            try:
                self.stream.update_subscriptions(
                    list(old_symbols[Market.EQUITY]), list(old_symbols[Market.CRYPTO])
                )
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
            {
                market.value: {key: list(value) for key, value in result.items()}
                for market, result in changes.items()
            },
        )
        return changes

    def _recover_portfolio_fill(
        self,
        client_order_id: str,
        broker_order_id: str,
        filled_qty: float,
        avg_price: float,
        occurred_at: datetime,
        activity_id: str,
        fee: float,
    ) -> None:
        """Book a real fill recovered from Alpaca order history at its actual
        price (see AlpacaBroker._reconcile_stale_portfolio_ownership)."""
        self._portfolio_repository.apply_trade_update(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            status="filled",
            cumulative_filled_quantity=filled_qty,
            average_fill_price=avg_price,
            occurred_at=occurred_at,
            activity_id=activity_id,
            fee=fee,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )

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
            if canonical_symbol(position.symbol, position.market) in portfolio_symbols
            and not position.is_flat
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
            # Reference symbols are strategy inputs; a disabled equity
            # strategy only needs the managed pins themselves.
            references = (
                self.app_config.universe.equity.reference_symbols
                if strategy_enabled.get(Market.EQUITY, True)
                else ()
            )
            ordered = tuple(
                dict.fromkeys(
                    (*references, *equity_pins, *sorted(self._symbols_by_market[Market.EQUITY]))
                )
            )
            if len(set((*references, *equity_pins))) > maximum:
                raise RuntimeError(
                    "Managed equity pins and references exceed the configured IEX stream limit"
                )
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
            configured_pins = (
                crypto_config.pinned_symbols if strategy_enabled.get(Market.CRYPTO, True) else ()
            )
            reserved = tuple(dict.fromkeys((*configured_pins, *crypto_pins)))
            if len(reserved) > crypto_config.maximum_stream_symbols:
                raise RuntimeError(
                    "Configured and managed crypto pins exceed maximum_stream_symbols"
                )
            active_crypto = tuple(dict.fromkeys((*reserved, *current_crypto)))[
                : crypto_config.maximum_stream_symbols
            ]
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
                symbol
                for symbol in self.market_state.allowed_trade_symbols
                if not is_crypto(symbol)
            }
            self.market_state.allowed_trade_symbols.update(active_crypto)
        self.symbols = sorted(
            self._symbols_by_market[Market.EQUITY] | self._symbols_by_market[Market.CRYPTO]
        )
        self._expected_all_symbols = set().union(
            *(
                symbols
                for candidate_market, symbols in self._symbols_by_market.items()
                if getattr(self, "_market_enabled", {}).get(candidate_market, False)
            )
        )
        self._not_ready_symbols.update(equity_pins)
        self._not_ready_symbols.update(crypto_pins)
        for runtime in getattr(self, "_portfolio_runtimes", ()):
            market_symbols = tuple(sorted(self._symbols_by_market[runtime.market]))
            runtime.symbols = market_symbols
            for symbol in market_symbols:
                runtime.history.setdefault(symbol, [])

    def stop(self) -> bool:
        alive: list[str] = []
        previous_state = getattr(self, "_state", None)
        breaker_at_stop = getattr(self, "_circuit_breaker_reason", None)
        if self._running:
            self._state = "stopping"
            self._running = False
            self._stop_event.set()
            try:
                self._events.put_nowait(None)
            except queue.Full:
                try:
                    self._events.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._events.put_nowait(None)
                except queue.Full:
                    pass
            for stream in (self.stream, self.trading_updates):
                try:
                    stream.stop()
                except Exception as exc:
                    log.warning("Stream stop warning: %s", exc)
            for thread in self._threads:
                thread.join(timeout=5)
            alive = [thread.name for thread in self._threads if thread.is_alive()]
            reconcile_thread = self._reconcile_thread
            if reconcile_thread is not None:
                reconcile_thread.join(timeout=30)
                if reconcile_thread.is_alive():
                    alive.append(reconcile_thread.name)
            warmup_thread = self._warmup_retry_thread
            if warmup_thread is not None:
                warmup_thread.join(timeout=30)
                if warmup_thread.is_alive():
                    alive.append(warmup_thread.name)
            if alive:
                self._state = "stop_failed"
                log.error("Runtime workers did not stop cleanly: %s", ", ".join(alive))
                return False
            # Release broker/data HTTP session pools on every start/stop cycle.
            for candidate in (self.broker, self._historical):
                close = getattr(candidate, "close", None)
                if close is None:
                    continue
                try:
                    close()
                except Exception as exc:
                    log.warning("Session close warning during stop: %s", exc)
        # A stop from a halted/error state (or while a circuit breaker is
        # active) is an unexpected shutdown: alert and record the session as
        # such. Graceful stops just close the session row.
        unexpected = (
            previous_state in {"halted", "error", "stop_failed"} or breaker_at_stop is not None
        )
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
        if session_id is not None:
            try:
                self.ledger.end_runtime_session(
                    session_id,
                    "unexpected" if unexpected else "stopped",
                )
            except Exception as exc:
                log.warning("Runtime session end failed: %s", exc)
            self._runtime_session_id = None
        if not self._storage_closed:
            try:
                for runtime in getattr(self, "_portfolio_runtimes", ()):
                    runtime.close("stopped")
                # Give in-flight stream-thread writes a moment to land before
                # the SQLite connections below are released.
                time.sleep(0.25)
                self.ledger.close()
            except Exception as exc:
                log.exception("Runtime storage close failed: %s", exc)
            self._storage_closed = True
        self._state = "stopped"
        log.info("Engine runtime stopped")
        return True

    def _fresh_account_snapshot(self) -> AccountSnapshot:
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
        snapshot = getattr(self, "_last_account_snapshot", None)
        snapshot_at = getattr(self, "_last_account_snapshot_at", None)
        if snapshot is not None and snapshot_at is not None and (
            datetime.now(UTC) - snapshot_at
        ).total_seconds() <= self.config.maximum_account_age_for_entry_seconds:
            return snapshot
        account = self.broker.account()
        now = datetime.now(UTC)
        self._last_account_snapshot = account
        self._last_account_snapshot_at = now
        self._last_account_at = now
        return account

    def _account_loop(self) -> None:
        worker = self._workers["pb-account"]
        worker.alive = True
        try:
            while self._running:
                worker.last_heartbeat = datetime.now(UTC)
                try:
                    account = self.broker.account()
                    # Snapshot first, timestamps second: a concurrent reader
                    # sees either the old pair (age over-estimated -> fresh
                    # fetch) or the new pair, never a fresh timestamp with an
                    # older snapshot.
                    self._last_account_snapshot = account
                    self._last_account_snapshot_at = datetime.now(UTC)
                    self._last_account_at = datetime.now(UTC)
                    worker.last_event_at = self._last_account_at
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
                    self._halt_for_ownership_drift()
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
        new_latch = {
            market: (breach if breach is not None else None) for market in self._entry_risk_latch
        }
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
        """
        with getattr(self, "_state_lock", threading.RLock()):
            self._state = "halted"
        self._circuit_breaker_reason = "ownership_drift"
        self._entry_risk_latch = {
            market: "ownership_drift" for market in getattr(self, "_entry_risk_latch", {})
        }
        self._persist_safety_state("ownership_drift")
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
                )
            )

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
        """
        account_id = self.broker.account_id or "unknown"
        persistent_reasons = {
            "ambiguous_order_state",
            "protection_failure",
            "protection_mismatch",
            "ledger_mismatch",
            "post_fill_risk_violation",
            "final_market_event_queue_overflow",
            "ownership_drift",
        }
        incidents = self.ledger.load_safety_incidents(account_id)
        for incident in incidents:
            if incident.reason in persistent_reasons:
                self._circuit_breaker_reason = incident.reason
                break
        if any(incident.reason == "portfolio_drawdown_halt" for incident in incidents):
            # A drawdown breach is an operational incident with an explicit
            # recovery (verified risk-epoch reset). Restore the entry latch
            # from the persisted incident so the halt is visible immediately
            # after restart; the first account poll re-evaluates it against
            # the broker state (a recovered breach clears it).
            self._entry_risk_latch = {
                market: "portfolio_drawdown_halt" for market in self._entry_risk_latch
            }
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

    async def _on_trade_update(self, update: TradeUpdate) -> None:
        try:
            await self._handle_trade_update(update)
        except Exception as exc:
            # The TradingStream callback is the primary source of truth for
            # the ledger. An unhandled exception here can kill the stream
            # worker (or run unobserved inside the SDK loop), degrading the
            # whole runtime. Escalate to a halt instead of failing silently.
            with getattr(self, "_state_lock", threading.RLock()):
                self._state = "halted"
                self._circuit_breaker_reason = "trade_update_callback_failure"
            log.exception(
                "Trade update handling failed for order %s: %s",
                update.client_order_id,
                exc,
            )

    def _advance_portfolio_rebalance(self, market: Market) -> None:
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
                    result = portfolio_runtime.advance_rebalance()
            except Exception as exc:
                log.error(
                    "Two-phase rebalance advance failed: market=%s error=%s",
                    market.value,
                    exc,
                )
                continue
            if result is not None and result.status == "rebalance_blocked":
                with getattr(self, "_state_lock", threading.RLock()):
                    self._market_entry_halts[market] = "rebalance_reconciliation_required"
                log.critical(
                    "Two-phase rebalance outcome is ambiguous; %s entries halted "
                    "pending reconciliation (decision=%s)",
                    market.value,
                    result.decision_id,
                )
                runtime_metrics_store.record_event("ambiguous_order")
                alerts.emit(
                    "ambiguous_order",
                    message=(
                        "Two-phase rebalance outcome ambiguous; entries halted "
                        "pending reconciliation"
                    ),
                    market=market.value,
                    decision_id=result.decision_id,
                )

    async def _handle_trade_update(self, update: TradeUpdate) -> None:
        portfolio_repository = getattr(self, "_portfolio_repository", None)
        if portfolio_repository is not None and portfolio_repository.has_order(
            update.client_order_id
        ):
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
                    update.activity_id
                    or f"{update.client_order_id}:{update.event}:{update.timestamp.isoformat()}"
                ),
                fee=update.fee,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
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
            freshness = getattr(
                getattr(self, "config", None), "account_poll_interval_seconds", 15.0
            )
            snapshot_provider = getattr(self.broker, "snapshot_if_fresh", None)
            cached = None
            if snapshot_provider is not None and self._last_account_at is not None:
                if (now - self._last_account_at).total_seconds() <= freshness:
                    cached = snapshot_provider(freshness)
            if cached is not None:
                account = cached
            else:
                account = await asyncio.to_thread(self.broker.account)
                # The fill-path fetch is the freshest snapshot in the system;
                # share it with the decision-path cache.
                self._last_account_snapshot = account
                self._last_account_snapshot_at = now
                self._last_account_at = now
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
                try:
                    target()
                    if not self._running:
                        return
                    raise RuntimeError("stream worker returned unexpectedly")
                except Exception as exc:
                    worker.last_error = str(exc)
                    worker.restart_count += 1
                    market = {
                        "pb-stocks": Market.EQUITY,
                        "pb-crypto": Market.CRYPTO,
                    }.get(name)
                    if market is None:
                        self._state = "degraded"
                    if worker.restart_count > 5:
                        if market is None:
                            self._state = "halted"
                            self._circuit_breaker_reason = f"{name}_restart_limit"
                            log.exception("%s shared worker restart limit reached", name)
                            runtime_metrics_store.record_event("circuit_breaker")
                            alerts.emit(
                                "circuit_breaker",
                                message=(
                                    f"{name} shared worker restart limit reached; runtime halted"
                                ),
                                reason=self._circuit_breaker_reason,
                            )
                            return
                        else:
                            self._market_entry_halts[market] = f"{name}_restart_limit"
                        log.exception("%s stream worker restart limit reached", name)
                        runtime_metrics_store.record_event("circuit_breaker")
                        alerts.emit(
                            "circuit_breaker",
                            message=f"{name} stream worker restart limit reached; entries halted",
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
                    time.sleep(delay)
                    worker.last_heartbeat = datetime.now(UTC)
        finally:
            worker.alive = False

    def _mark_market_stream_recovered(self, market: Market) -> None:
        """Clear a market-only failure after a real event proves reconnect health."""
        worker_name = {Market.EQUITY: "pb-stocks", Market.CRYPTO: "pb-crypto"}[market]
        worker = self._workers.get(worker_name)
        if worker is None or worker.last_error is None:
            return
        previous_error = worker.last_error
        previous_restarts = worker.restart_count
        worker.last_error = None
        worker.restart_count = 0
        worker.last_event_at = datetime.now(UTC)
        if self._market_entry_halts.get(market) == f"{worker_name}_restart_limit":
            self._market_entry_halts[market] = None
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
        worker_name = {Market.EQUITY: "pb-stocks", Market.CRYPTO: "pb-crypto"}[bar.market]
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
                timeframe: (
                    aggregator.update_correction(bar) if corrected else aggregator.update(bar)
                )
                for timeframe, aggregator in self.aggregators.items()
            }
        for timeframe, closed in finalized.items():
            if closed is None:
                continue
            if corrected:
                self._put_event("correction", closed)
                continue
            if timeframe == self.app_config.runtime.execution_timeframe:
                self._put_event("primary", closed)
            elif timeframe == self.app_config.runtime.market_state_timeframe:
                self._put_event("higher", closed)
            if timeframe in self._strategy_timeframes:
                self._put_event("portfolio", closed)

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
            # clear runs under the halt lock because live and corrected
            # price passes can run concurrently.
            with getattr(self, "_event_stats_lock", threading.Lock()):
                if self._market_entry_halts.get(Market.CRYPTO) == "crypto_protection_failure":
                    self._market_entry_halts[Market.CRYPTO] = None
                    log.warning("Crypto protection watcher recovered; crypto entries unhalted")
        except AmbiguousOrderState as exc:
            with getattr(self, "_event_stats_lock", threading.Lock()):
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
            with getattr(self, "_event_stats_lock", threading.Lock()):
                self._market_entry_halts[Market.CRYPTO] = "crypto_protection_failure"
            log.exception(
                "Crypto protection watcher failed (entries halted until the "
                "next verified price pass): %s",
                exc,
            )

    def _put_event(self, kind: str, bar: Bar) -> None:
        event = _Event(kind, bar, datetime.now(UTC))
        result = self._events.put_market_event(event)
        # _put_event runs from several threads (stock stream, crypto stream,
        # decision loop); the diagnostics counters must not be mutated
        # unsynchronized.
        with getattr(self, "_event_stats_lock", threading.Lock()):
            if result == "coalesced":
                self._coalesced_events += 1
            elif result == "replaced":
                self._dropped_events += 1
            elif result == "overflow":
                self._circuit_breaker_reason = "final_market_event_queue_overflow"
                self._dropped_events += 1
                runtime_metrics_store.record_event("circuit_breaker")
                alerts.emit(
                    "circuit_breaker",
                    message="Final market event queue overflow; runtime halted",
                    reason="final_market_event_queue_overflow",
                )
            if self._oldest_received_at is None:
                self._oldest_received_at = event.received_at

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
                    item = self._events.get(
                        timeout=min(0.5, max(0.0, next_reconcile - time.monotonic()))
                    )
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
                        self.market_state.seed([item.bar])
                    elif item.kind == "correction":
                        self.market_state.ingest_corrections([item.bar])
                    elif item.bar.timestamp <= max(
                        self._series_watermarks.get(
                            (item.bar.symbol, item.bar.timeframe),
                            datetime.min.replace(tzinfo=UTC),
                        ),
                        self._portfolio_watermarks.get(
                            item.bar.market, datetime.min.replace(tzinfo=UTC)
                        ),
                    ):
                        batch = processed_batches.get(item.bar.timestamp)
                        if batch is not None and item.bar.symbol not in batch:
                            processed_batches[item.bar.timestamp] = batch | {item.bar.symbol}
                            expected: set[str] = getattr(self, "_expected_all_symbols", set())
                            self._process_timestamp(
                                item.bar.timestamp,
                                {item.bar.symbol: item.bar},
                                expected - (batch | {item.bar.symbol}),
                            )
                            log.info(
                                "Late finalized bar re-evaluated: symbol=%s "
                                "timeframe=%s timestamp=%s",
                                item.bar.symbol,
                                item.bar.timeframe,
                                item.bar.timestamp,
                            )
                        else:
                            self.market_state.ingest_corrections([item.bar])
                            log.debug(
                                "Late finalized bar ingested as correction: symbol=%s "
                                "timeframe=%s timestamp=%s",
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
                real_gate = getattr(gate, "__func__", None) is getattr(
                    EngineRuntime, "_entry_readiness_reasons"
                )
                batch_health: RuntimeHealth | None = None
                for timestamp in sorted(closable):
                    bars_by_symbol = pending.pop(timestamp)
                    first_seen.pop(timestamp, None)
                    if batch_health is None and real_gate:
                        batch_health = self.health()
                    self._process_timestamp(
                        timestamp, bars_by_symbol, expected_all, health=batch_health
                    )
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
                        now_mono - portfolio_first_seen[portfolio_key]
                        >= self.config.event_grace_seconds
                    ):
                        portfolio_closable.append(portfolio_key)
                for portfolio_key in sorted(
                    portfolio_closable, key=lambda item: (item[1], item[0])
                ):
                    candidate_bars = portfolio_pending.pop(portfolio_key)
                    portfolio_first_seen.pop(portfolio_key, None)
                    if batch_health is None and real_gate:
                        batch_health = self.health()
                    self._process_portfolio_timestamp(
                        list(candidate_bars.values()), health=batch_health
                    )
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
            self._state = "halted"
            if isinstance(exc, OwnershipDriftError):
                self._circuit_breaker_reason = "ownership_drift"
            else:
                self._circuit_breaker_reason = "decision_worker_crashed"
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
        local = now.astimezone(NEW_YORK)
        with self._ingestion_lock:
            finalized = {
                timeframe: aggregator.flush_until(now)
                for timeframe, aggregator in self.aggregators.items()
            }
            if local.weekday() < 5 and (local.hour, local.minute) >= (16, 0):
                for timeframe, aggregator in self.aggregators.items():
                    finalized[timeframe].extend(aggregator.flush_session_close(now))
        for timeframe, bars in finalized.items():
            for bar in bars:
                if timeframe == self.app_config.runtime.execution_timeframe:
                    self._put_event("primary", bar)
                elif timeframe == self.app_config.runtime.market_state_timeframe:
                    self._put_event("higher", bar)
                if timeframe in self._strategy_timeframes:
                    self._put_event("portfolio", bar)

    def _process_timestamp(
        self,
        timestamp: datetime,
        bars_by_symbol: dict[str, Bar],
        expected: set[str],
        *,
        health: RuntimeHealth | None = None,
    ) -> None:
        bars = list(bars_by_symbol.values())
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
        if health is None and getattr(gate, "__func__", None) is getattr(
            EngineRuntime, "_entry_readiness_reasons"
        ):
            health = self.health()
        gate_kwargs = {"health": health} if health is not None else {}
        market_reasons: dict[Market, tuple[str, ...]] = {}
        for candidate_market in {bar.market for bar in bars_by_symbol.values()}:
            market_reasons[candidate_market] = gate(candidate_market, **gate_kwargs)
        ready_bars = {
            symbol: bar
            for symbol, bar in bars_by_symbol.items()
            if symbol not in self._not_ready_symbols
            and not self._engine_has_symbol_gap(symbol)
            and not market_reasons[bar.market]
        }
        if not ready_bars:
            return
        missing = tuple(sorted(expected - set(bars_by_symbol)))
        if missing:
            log.warning(
                "Ranking universe is partial; missing symbols: %s",
                ", ".join(missing),
            )
        # This cadence updates bar and readiness state only. PortfolioRuntime
        # is the sole strategy/risk/order decision path.

    def _process_portfolio_timestamp(
        self, bars: list[Bar], *, health: RuntimeHealth | None = None
    ) -> None:
        if self._warming_up or self._circuit_breaker_reason is not None:
            return
        # One immutable account snapshot per processing cycle, shared by every
        # market's decision in this batch (single broker fetch instead of one
        # per market). Fetched lazily on the first actual decision; freshness
        # is enforced against maximum_account_age_for_entry_seconds.
        account: AccountSnapshot | None = None
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
            if health is None and getattr(gate, "__func__", None) is getattr(
                EngineRuntime, "_entry_readiness_reasons"
            ):
                health = self.health()
            gate_kwargs = {"health": health} if health is not None else {}
            if gate(portfolio_runtime.market, **gate_kwargs):
                continue
            # An entry-risk latch (daily loss / drawdown) must not freeze the
            # whole decision: risk-reducing SELL exits still run, only
            # entries are blocked.
            entry_latch = getattr(self, "_entry_risk_latch", {}).get(portfolio_runtime.market)
            try:
                with self._portfolio_lock:
                    if account is None and getattr(self, "broker", None) is not None:
                        account = self._fresh_account_snapshot()
                    kwargs = {"entry_latch": entry_latch}
                    if account is not None:
                        kwargs["account"] = account
                    portfolio_result = portfolio_runtime.process_closed_bars(
                        relevant, **kwargs
                    )
            except OwnershipDriftError:
                self._circuit_breaker_reason = "ownership_drift"
                self._state = "halted"
                log.exception(
                    "Portfolio ownership drift blocked decisions for market=%s",
                    portfolio_runtime.market.value,
                )
                runtime_metrics_store.record_event("circuit_breaker")
                alerts.emit(
                    "circuit_breaker",
                    message="Portfolio ownership drift blocked decisions; runtime halted",
                    reason="ownership_drift",
                    market=portfolio_runtime.market.value,
                )
                return
            except Exception as exc:
                self._circuit_breaker_reason = "portfolio_runtime_failure"
                self._state = "halted"
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
                    self._market_entry_halts[portfolio_runtime.market] = (
                        "rebalance_reconciliation_required"
                    )
                log.critical(
                    "Two-phase rebalance blocked; %s entries halted pending "
                    "reconciliation (decision=%s)",
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
            self._last_portfolio_status[portfolio_runtime.market] = portfolio_result.status

    def _run_startup_catch_up(self) -> None:
        """Force an immediate full-target decision on the latest warmup bars.

        A fresh account started mid-period would otherwise sit in cash until
        the next cadence boundary (the backtest engine decides on its first
        usable bar). The catch-up is an OPTIMIZATION: any gate, readiness
        failure or exception blocks only the catch-up and records a
        ``blocked:...`` status — normal cadence decisions continue exactly as
        before this mission. No alerts are emitted for catch-up results.
        """
        if (
            not self._running
            or self._warming_up
            or self._circuit_breaker_reason is not None
            or getattr(self, "_reconcile_failed", False)
        ):
            return
        for portfolio_runtime in getattr(self, "_portfolio_runtimes", ()):
            reasons = self._entry_readiness_reasons(portfolio_runtime.market)
            if reasons:
                portfolio_runtime.startup_catch_up_status = "blocked:" + ",".join(reasons)
                continue
            entry_latch = getattr(self, "_entry_risk_latch", {}).get(portfolio_runtime.market)
            try:
                with self._portfolio_lock:
                    result = portfolio_runtime.startup_catch_up(entry_latch=entry_latch)
            except Exception:
                log.exception(
                    "Startup catch-up failed for market=%s; normal cadence continues",
                    portfolio_runtime.market.value,
                )
                portfolio_runtime.startup_catch_up_status = "blocked:startup_catch_up_failed"
                continue
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
        if self._state in {"halted", "error", "stop_failed", "stopping", "stopped"}:
            reasons.append(f"runtime_state:{self._state}")
        if health.queue_age_seconds > self.config.maximum_queue_age_seconds:
            reasons.append("event_queue_stale")
        if (
            self._last_account_at is None
            or (datetime.now(UTC) - self._last_account_at).total_seconds()
            > self.config.maximum_account_age_for_entry_seconds
        ):
            reasons.append("account_snapshot_stale")
        if self._circuit_breaker_reason:
            reasons.append(self._circuit_breaker_reason)
        relevant_stream = (
            {
                Market.EQUITY: "pb-stocks",
                Market.CRYPTO: "pb-crypto",
            }.get(market)
            if market is not None
            else None
        )
        shared_workers = {"pb-decisions", "pb-account", "pb-trade-updates"}
        if relevant_stream:
            shared_workers.add(relevant_stream)
        else:
            shared_workers.update({"pb-stocks", "pb-crypto"})
        dead_workers = [
            name
            for name, worker in self._workers.items()
            if name in shared_workers and not worker.alive
        ]
        if dead_workers:
            reasons.append("worker_unavailable:" + ",".join(sorted(dead_workers)))
        market_halts = getattr(self, "_market_entry_halts", {})
        if market is not None and market_halts.get(market):
            reasons.append(str(market_halts[market]))
        return tuple(reasons)

    def _engine_has_market_gap(self, market: Market) -> bool:
        return bool(self.market_state.has_data_gaps_for_market(market))

    def _engine_has_symbol_gap(self, symbol: str) -> bool:
        return bool(self.market_state.has_data_gaps_for_symbol(symbol))

    def _repair_gap(self, bar: Bar) -> None:
        history = self._historical
        if history is None:
            return
        series = self.market_state.bars.get(bar.symbol, bar.timeframe)
        previous = next(
            (
                candidate
                for candidate in reversed(series[:-1])
                if candidate.timestamp < bar.timestamp
            ),
            None,
        )
        if previous is None:
            return
        try:
            backfill = history.get_bars(
                bar.symbol,
                bar.timeframe,
                start=previous.timestamp,
                end=bar.timestamp,
            )
            if backfill and self.market_state.repair_gap(backfill):
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

    def _refresh_readiness(self) -> None:
        for symbol in tuple(self._not_ready_symbols):
            market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
            relevant = [
                runtime
                for runtime in self._portfolio_runtimes
                if runtime.market == market and symbol in runtime.history
            ]
            if relevant and all(
                len(runtime.history[symbol]) >= runtime.strategy.minimum_history_bars
                for runtime in relevant
            ):
                self._not_ready_symbols.discard(symbol)
        if (
            self._running
            and self._not_ready_symbols
            and time.monotonic() - getattr(self, "_last_warmup_retry_at", time.monotonic())
            >= _WARMUP_RETRY_SECONDS
        ):
            self._schedule_warmup_retry()
        if self._running and self._has_ready_symbols() and self._state == "warming_up":
            self._state = "running"

    def _schedule_warmup_retry(self) -> None:
        current = getattr(self, "_warmup_retry_thread", None)
        if current is not None and current.is_alive():
            return
        self._last_warmup_retry_at = time.monotonic()
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
        return any(symbol not in self._not_ready_symbols for symbol in self.symbols)

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
                )
            )

    def _reconcile_safely(self) -> None:
        reconcile_lock = getattr(self, "_reconcile_lock", threading.Lock())
        with reconcile_lock:
            try:
                self.broker.reconcile()
                self._last_reconciliation_at = datetime.now(UTC)
                self._last_account_at = datetime.now(UTC)
                self._generic_reconcile_failures = 0
                account_id = self.broker.account_id or "unknown"
                # reconcile() raises on drift/protection issues, so reaching
                # this line proves every persisted halt resolved; clear the
                # incidents so a restart does not resurrect them.
                if getattr(self, "_reconcile_failed", False):
                    self._reconcile_failed = False
                    runtime_metrics_store.record_event("reconciliation_recovered")
                    alerts.emit(
                        "reconciliation_recovered",
                        message="Periodic reconciliation recovered",
                        account_id=account_id,
                    )
                self.ledger.clear_safety_incident(account_id)
                if self._circuit_breaker_reason == "reconciliation_failed":
                    self._circuit_breaker_reason = None
            except RequestException as exc:
                # Alpaca occasionally times out for a single request. The next
                # scheduled reconciliation retries it; a full traceback every
                # minute obscures actionable protection failures.
                log.warning("Position protection check deferred after Alpaca API error: %s", exc)
                self._circuit_breaker_reason = "reconciliation_failed"
                self._reconcile_failed = True
                runtime_metrics_store.record_event("reconciliation_failure")
                alerts.emit(
                    "reconciliation_failure",
                    message="Position protection check deferred after Alpaca API error",
                    reason="reconciliation_failed",
                    error=str(exc),
                )
            except OwnershipDriftError:
                log.exception("Portfolio ownership drift detected; entries halted")
                with getattr(self, "_state_lock", threading.RLock()):
                    self._state = "halted"
                self._circuit_breaker_reason = "ownership_drift"
                self._reconcile_failed = True
                self._persist_safety_state("ownership_drift")
                runtime_metrics_store.record_event("circuit_breaker")
                alerts.emit(
                    "circuit_breaker",
                    message="Portfolio ownership drift detected; runtime halted",
                    reason="ownership_drift",
                )
            except ProtectionReconciliationError:
                log.exception("Managed position protection reconciliation failed")
                self._circuit_breaker_reason = "protection_failure"
                self._reconcile_failed = True
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
                self._generic_reconcile_failures = (
                    getattr(self, "_generic_reconcile_failures", 0) + 1
                )
                log.exception("Periodic position protection check failed")
                self._reconcile_failed = True
                if self._generic_reconcile_failures >= 3:
                    self._circuit_breaker_reason = "reconciliation_bug_suspected"
                else:
                    self._circuit_breaker_reason = "reconciliation_failed"
                runtime_metrics_store.record_event("reconciliation_failure")
                alerts.emit(
                    "reconciliation_failure",
                    message="Periodic position protection check failed",
                    reason=self._circuit_breaker_reason,
                    consecutive_failures=self._generic_reconcile_failures,
                )

    def _schedule_reconcile(self) -> None:
        """Run the expensive broker scan without stalling finalized-bar decisions."""
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
            raise RuntimeError(
                "The engine is not running; start it before resetting the risk epoch"
            )
        if self._circuit_breaker_reason is not None:
            raise RuntimeError(
                "Cannot reset the risk epoch while a circuit breaker is active "
                f"({self._circuit_breaker_reason}); resolve that incident first"
            )
        self.broker.reconcile()  # verified recovery before rebasing
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
        self._evaluate_entry_risk_latch(rebased)
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
        }
        reason = self._circuit_breaker_reason
        if reason not in recoverable:
            raise ValueError(f"Safety lock is not operator-resettable: {reason or 'none'}")
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
            self.broker.reconcile()
            self._last_reconciliation_at = datetime.now(UTC)
            account_id = self.broker.account_id or "unknown"
            entries = self.ledger.managed_entries_for_account(account_id)
            if any(item.status == "manual_intervention_required" for item in entries):
                raise ValueError("A managed position still requires manual intervention")
            account = self.broker.account()
            self._last_account_at = datetime.now(UTC)
            self._circuit_breaker_reason = None
            # The operator verified THIS halt's recovery condition above;
            # clear exactly its incident, never unrelated incidents (those
            # need their own per-halt verification).
            self.ledger.clear_safety_incident(account_id, reason)
            market_halts = getattr(self, "_market_entry_halts", None)
            if market_halts is not None:
                self._verify_market_halts(market_halts)
            self._entry_risk_latch = {
                market: None for market in getattr(self, "_entry_risk_latch", {})
            }
            self._persist_risk_state(account)
        if self._running and self._state == "halted":
            self._state = "running"
        log.warning("Operator reset safety lock after successful reconciliation: %s", reason)

    def _verify_market_halts(self, market_halts: dict[Market, str | None]) -> None:
        """Clear each market halt only after its specific verified recovery.

        Runs INSIDE the reconcile lock; the successful ``broker.reconcile()``
        above already verified protection and exit state, so per-halt
        verifications may rely on it. Unverified halts stay active and are
        reported to the operator.
        """
        retained: list[str] = []
        for market in (Market.EQUITY, Market.CRYPTO):
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
                if market_halts.get(market) == halt:
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
                if market_halts.get(market) == halt:
                    retained.append(f"{market.value}:{halt}:refresh_did_not_clear")
                continue
            if halt == "crypto_protection_failure":
                # The broker reconciliation above is the verified protection
                # pass: it restores managed crypto stops and raises on
                # failure, so a completed reconcile proves the pass.
                market_halts[market] = None
                continue
            if halt == "ambiguous_crypto_exit":
                # Only authoritative broker reconciliation proving terminal
                # exit state clears it; the reconcile above runs
                # _reconcile_crypto_exit for every in-flight exit and raises
                # on ambiguity. Verify nothing remains unresolved.
                if self._crypto_exits_are_terminal():
                    market_halts[market] = None
                else:
                    retained.append(f"{market.value}:{halt}:exit_state_not_terminal")
                continue
            if halt.endswith("_restart_limit"):
                # Only a verified stream reconnect clears it: re-arm the
                # worker and require it to be alive and (when the market is
                # live) delivering fresh events.
                if self._restart_stream_worker(market):
                    market_halts[market] = None
                else:
                    retained.append(f"{market.value}:{halt}:worker_not_delivering")
                continue
            if halt == "rebalance_reconciliation_required":
                if self._rebalance_state_reconciled(market):
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
        worker_name = "pb-stocks" if market == Market.EQUITY else "pb-crypto"
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

    def _stream_worker_delivering(self, market: Market, worker: WorkerHealth | None) -> bool:
        if worker is None or not worker.alive:
            return False
        now = datetime.now(UTC)
        from parayabuck.markets.timeframes import is_regular_equity_time

        event_required = market == Market.CRYPTO or is_regular_equity_time(now)
        if not event_required:
            return True
        last_event = worker.last_event_at
        if last_event is None:
            return False
        heartbeat_ttl = max(30.0, self.config.account_poll_interval_seconds * 3)
        return (now - last_event).total_seconds() <= max(180.0, heartbeat_ttl)

    def _rebalance_state_reconciled(self, market: Market) -> bool:
        """True when the blocked two-phase rebalance for a market is resolved.

        The broker reconciliation above resolves working orders into terminal
        states; once every order of the blocked decision is terminal the halt
        can clear and the runtime completes the deferred BUY phase normally.
        """
        repository = getattr(self, "_portfolio_repository", None)
        if repository is None:
            return True
        current = repository.current_rebalance_execution(
            self.broker.account_id or "unknown", market
        )
        if current is None or current.phase != REBALANCE_PHASE_BLOCKED:
            return True
        orders = repository.orders_for_decision(current.decision_id)
        return bool(orders) and all(
            is_terminal_order_status(str(order["status"])) for order in orders
        )

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
        log.info("Warmup complete")

    def _warmup_symbols(
        self,
        history: AlpacaHistoricalData,
        symbols: list[str],
        *,
        retry: bool,
        staged_only: bool = False,
    ) -> dict[tuple[str, str], list[Bar]] | None:
        """Download warmup history for ``symbols``.

        With ``staged_only`` the bars are returned without seeding: the
        caller (universe hot-swap) commits the membership first and seeds
        afterwards so the new symbols' strategy history is never silently
        dropped. Otherwise bars are seeded into the market state and every
        matching portfolio runtime under the ingestion and portfolio locks.
        """
        staged: dict[tuple[str, str], list[Bar]] = {}
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
                        batched = history.get_bars_batch(
                            symbol_batch,
                            timeframe,
                            start=_warmup_start(symbol_batch[0], timeframe, requested_bars),
                        )
                        for symbol in symbol_batch:
                            bars = batched.get(symbol, [])
                            if staged_only:
                                staged[(symbol, timeframe)] = bars[-requested_bars:]
                            else:
                                with self._ingestion_lock, self._portfolio_lock:
                                    self.market_state.seed(bars[-requested_bars:])
                                    for runtime in self._portfolio_runtimes:
                                        runtime.seed(bars[-requested_bars:])
                            if len(bars) < requested_bars:
                                log.warning(
                                    "Warmup not ready %s %s: %d/%d bars",
                                    symbol,
                                    timeframe,
                                    len(bars),
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
    from parayabuck.markets.timeframes import bars_to_calendar_days

    days = bars_to_calendar_days(symbol, timeframe, bars)
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


__all__ = ["EngineRuntime", "TransientStartupError"]
