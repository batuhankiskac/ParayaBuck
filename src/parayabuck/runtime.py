from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
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
from parayabuck.execution.portfolio import portfolio_risk_limits_from_config
from parayabuck.live_safety import LiveOperatorApproval
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    NEW_YORK,
    is_regular_equity_time,
)
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
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
_WARMUP_SYMBOL_BATCH_SIZE = 8
_WARMUP_RETRY_SECONDS = 600.0


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
        self._active_crypto_symbols = active_crypto
        self.symbols = [canonical_symbol(symbol) for symbol in (*active_equities, *active_crypto)]
        self._symbols_by_market = {
            Market.EQUITY: {symbol for symbol in self.symbols if not is_crypto(symbol)},
            Market.CRYPTO: {symbol for symbol in self.symbols if is_crypto(symbol)},
        }
        self._market_enabled = {
            Market.EQUITY: bool(self._symbols_by_market[Market.EQUITY]),
            Market.CRYPTO: bool(self._symbols_by_market[Market.CRYPTO]),
        }
        self._market_entry_halts: dict[Market, str | None] = {
            Market.EQUITY: self._initial_universe_halt(Market.EQUITY),
            Market.CRYPTO: self._initial_universe_halt(Market.CRYPTO),
        }
        strategy_specs = []
        for market, selection, symbols in (
            (Market.EQUITY, config.strategy.equity, active_equities),
            (Market.CRYPTO, config.strategy.crypto, active_crypto),
        ):
            if not selection.enabled or not symbols:
                continue
            strategy = portfolio_strategy_from(selection.name, selection.parameters)
            if strategy.market != market:
                raise ValueError(f"Configured {market.value} strategy has the wrong market")
            if strategy.timeframe != selection.timeframe:
                raise ValueError(
                    f"Configured timeframe for {selection.name} must be {strategy.timeframe}"
                )
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
        self.stream = AlpacaBarStream(api_key, secret_key, config.alpaca.stock_feed)
        self.trading_updates = AlpacaTradingUpdateStream(
            api_key, secret_key, self.paper, self._on_trade_update
        )
        self._strategy_timeframes = {
            strategy.timeframe for strategy, _symbols, _cap in strategy_specs
        }
        required_timeframes = {
            self.app_config.runtime.execution_timeframe,
            self.app_config.runtime.risk_monitor_timeframe,
            *self._strategy_timeframes,
        }
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
        self._last_reconciliation_at: datetime | None = None
        self._circuit_breaker_reason: str | None = None
        self._series_watermarks: dict[tuple[str, str], datetime] = {}
        self._portfolio_watermarks: dict[Market, datetime] = {}
        self._warming_up = False
        self._not_ready_symbols: set[str] = set(self.symbols)
        self._historical: AlpacaHistoricalData | None = None
        self._last_universe_refresh_at: datetime | None = None
        self._next_universe_refresh_at: datetime | None = None
        self._managed_pin_symbols: set[str] = set()
        self._last_portfolio_status: dict[Market, str] = {}
        self._universe_refresh_status: dict[str, str] = {
            Market.EQUITY.value: self.universe_preflight.equity_status,
            Market.CRYPTO.value: self.universe_preflight.crypto_status,
        }
        self._last_universe_refresh_error: dict[str, str | None] = {
            Market.EQUITY.value: None,
            Market.CRYPTO.value: None,
        }

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
        scanner_degraded = any(
            status in {"refresh_failed", "apply_failed"}
            for status in getattr(self, "_universe_refresh_status", {}).values()
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
        except Exception as exc:
            self._circuit_breaker_reason = (
                "protection_failure"
                if isinstance(exc, ProtectionReconciliationError)
                else "reconciliation_failed"
            )
            self._state = "halted"
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
        except Exception:
            self._state = "error"
            self.stop()
            raise
        finally:
            self._warming_up = False
        try:
            account = self.broker.account()
            self._last_account_at = datetime.now(UTC)
            self._persist_risk_state(account)
        except Exception:
            self._state = "error"
            self.stop()
            raise
        self._refresh_readiness()
        self._state = "running" if self._has_ready_symbols() else "warming_up"
        log.info(
            "Engine runtime started: mode=%s symbols=%d timeframe=%s",
            "paper" if self.paper else "live",
            len(self.symbols),
            ",".join(sorted(self.aggregators)),
        )

    def _universe_refresh_loop(self) -> None:
        worker = self._workers["pb-universe-refresh"]
        worker.alive = True
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
                if interval_due or equity_due:
                    try:
                        if interval_due:
                            # Crypto trades 24/7: keep a fixed cadence.
                            self._refresh_runtime_universes(Market.CRYPTO)
                            worker.last_event_at = datetime.now(UTC)
                            worker.last_error = None
                        if equity_due:
                            # Equity refreshes pre-market only, after the
                            # previous session's data is final, so a mid-session
                            # partial daily bar can never change the universe.
                            self._refresh_runtime_universes(Market.EQUITY)
                            worker.last_event_at = datetime.now(UTC)
                            worker.last_error = None
                    except Exception as exc:
                        worker.last_error = str(exc)
                        log.exception("Scheduled universe refresh failed")
                    finally:
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
                self._stop_event.wait(min(5.0, max(0.0, remaining)))
        finally:
            worker.alive = False

    @staticmethod
    def _next_universe_deadline(next_interval: float, next_equity: datetime | None) -> datetime:
        interval_remaining = max(0.0, next_interval - time.monotonic())
        interval_deadline = datetime.now(UTC) + timedelta(seconds=interval_remaining)
        if next_equity is None:
            return interval_deadline
        return min(interval_deadline, next_equity)

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

    def _refresh_runtime_universes(self, target: Market | None = None) -> None:
        """Refresh the scanner universes, optionally for one market only.

        ``target=None`` refreshes every enabled dynamic universe (startup and
        backfill paths); the scheduled loop passes a single market so equity
        can run on a pre-market cadence while crypto keeps its own interval.
        """
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
        # Manually opened external positions are not the runtime's inventory
        # and must not consume stream slots unless the operator opted in.
        protected = {
            canonical_symbol(position.symbol, position.market)
            for position in account.positions
            if not position.is_flat and (position.managed_qty > 1e-12 or manage_external)
        }
        protected.update(self.broker.open_portfolio_order_symbols())
        protected.update(
            item.canonical_symbol for item in self.ledger.managed_entries_for_account(account_id)
        )
        applied: dict[Market, tuple[str, ...]] = {}
        changes: dict[Market, dict[str, tuple[str, ...]]] = {}
        for market, requested in desired_by_market.items():
            current = self._symbols_by_market[market]
            market_protected = tuple(
                sorted(
                    symbol for symbol in protected if is_crypto(symbol) == (market == Market.CRYPTO)
                )
            )
            if market == Market.EQUITY:
                equity_config = self.app_config.universe.equity
                configured_pins = equity_config.reference_symbols
                maximum_symbols = equity_config.maximum_stream_symbols
            else:
                crypto_config = self.app_config.universe.crypto
                configured_pins = crypto_config.pinned_symbols
                maximum_symbols = crypto_config.maximum_stream_symbols
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

    def _pin_managed_symbols(self) -> None:
        """Keep managed positions observable even after scanner/config removal."""
        account_id = self.broker.account_id or "unknown"
        entries = self.ledger.managed_entries_for_account(account_id)
        repository = getattr(self, "_portfolio_repository", None)
        # Only symbols still owned (net filled quantity > 0) or with an open
        # portfolio order are pinned; a fully exited symbol stops consuming a
        # stream slot.
        portfolio_symbols = (
            repository.owned_symbols(account_id) | self.broker.open_portfolio_order_symbols()
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
        if equity_pins:
            maximum = self.app_config.universe.equity.maximum_stream_symbols
            references = self.app_config.universe.equity.reference_symbols
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
            reserved = tuple(dict.fromkeys((*crypto_config.pinned_symbols, *crypto_pins)))
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

    def _account_loop(self) -> None:
        worker = self._workers["pb-account"]
        worker.alive = True
        try:
            while self._running:
                worker.last_heartbeat = datetime.now(UTC)
                try:
                    account = self.broker.account()
                    self._last_account_at = datetime.now(UTC)
                    worker.last_event_at = self._last_account_at
                    worker.last_error = None
                    self._portfolio_repository.update_high_water(
                        self.broker.account_id or "unknown", account.equity
                    )
                    self._persist_risk_state(account)
                except Exception as exc:
                    worker.last_error = str(exc)
                    log.warning("Account refresh failed: %s", exc)
                self._stop_event.wait(self.config.account_poll_interval_seconds)
        finally:
            worker.alive = False

    def _restore_persistent_state(self) -> None:
        account_id = self.broker.account_id or "unknown"
        previous = self.ledger.load_risk_state(account_id, datetime.now(UTC).date())
        persistent_reasons = {
            "ambiguous_order_state",
            "protection_failure",
            "protection_mismatch",
            "ledger_mismatch",
            "post_fill_risk_violation",
            "final_market_event_queue_overflow",
        }
        if previous is not None:
            self._consecutive_losses = previous.consecutive_losses
            if previous.circuit_breaker_reason in persistent_reasons:
                self._circuit_breaker_reason = previous.circuit_breaker_reason

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
            account = await asyncio.to_thread(self.broker.account)
            self._last_account_at = datetime.now(UTC)
            self._persist_risk_state(account)
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
                            return
                        else:
                            self._market_entry_halts[market] = f"{name}_restart_limit"
                        log.exception("%s stream worker restart limit reached", name)
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
            elif timeframe == self.app_config.runtime.risk_monitor_timeframe:
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
                for timestamp in sorted(closable):
                    bars_by_symbol = pending.pop(timestamp)
                    first_seen.pop(timestamp, None)
                    self._process_timestamp(timestamp, bars_by_symbol, expected_all)
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
                    self._process_portfolio_timestamp(list(candidate_bars.values()))
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
                elif timeframe == self.app_config.runtime.risk_monitor_timeframe:
                    self._put_event("higher", bar)
                if timeframe in self._strategy_timeframes:
                    self._put_event("portfolio", bar)

    def _process_timestamp(
        self,
        timestamp: datetime,
        bars_by_symbol: dict[str, Bar],
        expected: set[str],
    ) -> None:
        bars = list(bars_by_symbol.values())
        ingestion = self.market_state.ingest_closed_bars(bars)
        gap_bars = [
            bar
            for bar, mutation in zip(ingestion.bars, ingestion.mutations, strict=True)
            if mutation.value == "gap_detected"
        ]
        for bar in gap_bars:
            self._repair_gap(bar)
        if self._warming_up:
            return
        self._refresh_readiness()
        market_reasons: dict[Market, tuple[str, ...]] = {}
        for candidate_market in {bar.market for bar in bars_by_symbol.values()}:
            market_reasons[candidate_market] = self._entry_readiness_reasons(candidate_market)
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

    def _process_portfolio_timestamp(self, bars: list[Bar]) -> None:
        if self._warming_up or self._circuit_breaker_reason is not None:
            return
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
            if self._entry_readiness_reasons(portfolio_runtime.market):
                continue
            try:
                with self._portfolio_lock:
                    portfolio_result = portfolio_runtime.process_closed_bars(relevant)
            except OwnershipDriftError:
                self._circuit_breaker_reason = "ownership_drift"
                self._state = "halted"
                log.exception(
                    "Portfolio ownership drift blocked decisions for market=%s",
                    portfolio_runtime.market.value,
                )
                return
            except Exception as exc:
                self._circuit_breaker_reason = "portfolio_runtime_failure"
                self._state = "halted"
                log.exception("Portfolio runtime failed closed: %s", exc)
                return
            log.info(
                "Portfolio decision: strategy=%s market=%s status=%s orders=%d submitted=%d",
                portfolio_runtime.strategy.name,
                portfolio_runtime.market.value,
                portfolio_result.status,
                len(portfolio_result.orders),
                len(portfolio_result.submitted_order_ids),
            )
            self._last_portfolio_status[portfolio_runtime.market] = portfolio_result.status

    def _entry_readiness_reasons(self, market: Market | None = None) -> tuple[str, ...]:
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
                    circuit_breaker_reason=self._circuit_breaker_reason,
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
                if self._circuit_breaker_reason == "reconciliation_failed":
                    self._circuit_breaker_reason = None
            except RequestException as exc:
                # Alpaca occasionally times out for a single request. The next
                # scheduled reconciliation retries it; a full traceback every
                # minute obscures actionable protection failures.
                log.warning("Position protection check deferred after Alpaca API error: %s", exc)
                self._circuit_breaker_reason = "reconciliation_failed"
            except OwnershipDriftError:
                log.exception("Portfolio ownership drift detected; entries halted")
                self._circuit_breaker_reason = "ownership_drift"
            except ProtectionReconciliationError:
                log.exception("Managed position protection reconciliation failed")
                self._circuit_breaker_reason = "protection_failure"
            except Exception:
                # A generic failure is usually a code bug, not a broker
                # timeout. Keep retrying (defense in depth) but escalate after
                # repeated failures so the bug is not indistinguishable from
                # a transient network error.
                self._generic_reconcile_failures = (
                    getattr(self, "_generic_reconcile_failures", 0) + 1
                )
                log.exception("Periodic position protection check failed")
                if self._generic_reconcile_failures >= 3:
                    self._circuit_breaker_reason = "reconciliation_bug_suspected"
                else:
                    self._circuit_breaker_reason = "reconciliation_failed"

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

    def reset_safety_lock(self) -> None:
        """Acknowledge a recoverable entry lock after broker/data verification."""
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
            market_halts = getattr(self, "_market_entry_halts", None)
            if market_halts is not None:
                market_halts[Market.EQUITY] = None
                market_halts[Market.CRYPTO] = None
            self._persist_risk_state(account)
        if self._running and self._state == "halted":
            self._state = "running"
        log.warning("Operator reset safety lock after successful reconciliation: %s", reason)

    def _warmup(self) -> None:
        history = AlpacaHistoricalData(
            self.api_key,
            self.secret_key,
            stock_feed=self.app_config.alpaca.stock_feed,
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
                    self.app_config.runtime.risk_monitor_timeframe,
                }
                if not strategy_requirements and not operational:
                    continue
                requested_bars = max(2, *strategy_requirements)
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


def _source_revision(explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    try:
        completed = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


__all__ = ["EngineRuntime"]
