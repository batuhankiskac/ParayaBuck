from __future__ import annotations

import asyncio
import math
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from requests.exceptions import RequestException

from parayabuck.config import Config
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    ExecutionEvent,
    Fill,
    Market,
    OrderRole,
    OrderState,
)
from parayabuck.data import AlpacaBarStream, AlpacaHistoricalData, TimeframeAggregator
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import (
    AlpacaBroker,
    AmbiguousOrderState,
    CryptoPriceObservation,
    ProtectionReconciliationError,
)
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream, TradeUpdate
from parayabuck.execution.coordinator import ExecutionCoordinator
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    NEW_YORK,
    TIMEFRAME_MINUTES,
    is_regular_equity_time,
)
from parayabuck.monitoring import AIDecisionJournal, AsyncDecisionJournal, DecisionJournal
from parayabuck.portfolio.approval import (
    ApprovalError,
    ApprovalStage,
    LiveOperatorApproval,
    StrategyApprovalManifest,
)
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
)
from parayabuck.risk.manager import RiskState
from parayabuck.storage import SQLiteStore
from parayabuck.universe import (
    ensure_active_universes,
    load_active_crypto_symbols,
    load_active_symbols,
)
from parayabuck.universe.preflight import UniversePreflightResult
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
    strategy_status: str = "research_only_no_approved_portfolio_strategy"
    strategy_manifests: tuple[dict[str, object], ...] = ()


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
        self.api_key = api_key
        self.secret_key = secret_key
        self.app_config = config
        self.config = config.engine
        source_revision = _source_revision(self.config.runtime_source_revision)
        self.approval_manifests = tuple(
            StrategyApprovalManifest.load_verified(
                path,
                expected_checksum=checksum,
                current_source_revision=source_revision,
            )
            for path, checksum in zip(
                self.config.approval_manifest_paths,
                self.config.approval_manifest_checksums,
                strict=True,
            )
        )
        if len({manifest.market for manifest in self.approval_manifests}) != len(
            self.approval_manifests
        ):
            raise ApprovalError("Only one approval manifest per market sleeve is allowed")
        if any(
            manifest.instantiate_strategy().timeframe != self.config.timeframe
            for manifest in self.approval_manifests
        ):
            raise ApprovalError("Every approved strategy timeframe must match engine.timeframe")
        self._validate_manifest_account_mode()
        if getattr(self, "approval_manifests", ()):
            self.universe_preflight = UniversePreflightResult("manifest_locked", "manifest_locked")
            active_equities = tuple(
                symbol
                for manifest in self.approval_manifests
                if manifest.market == Market.EQUITY
                for symbol in manifest.symbols
            )
            active_crypto = tuple(
                symbol
                for manifest in self.approval_manifests
                if manifest.market == Market.CRYPTO
                for symbol in manifest.symbols
            )
        else:
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
            Market.EQUITY: None,
            Market.CRYPTO: None,
        }
        self.strategy_status = (
            "approved:"
            + ",".join(
                f"{manifest.market.value}/{manifest.approved_stage.value}"
                for manifest in self.approval_manifests
            )
            if self.approval_manifests
            else "research_only_no_approved_portfolio_strategy"
        )
        self.paper = config.alpaca.paper
        self.store = SQLiteStore(self.config.decision_db_path)
        self.journal = DecisionJournal(self.config.decision_db_path, store=self.store)
        self.journal_writer = AsyncDecisionJournal(
            self.journal,
            on_critical_failure=self._on_critical_journal_failure,
        )
        self.ledger = ExecutionLedger(self.config.decision_db_path, store=self.store)
        self.broker = AlpacaBroker(
            api_key,
            secret_key,
            paper=self.paper,
            manage_external_positions=self.config.manage_external_positions,
            ledger=self.ledger,
            crypto_stop_limit_offset_bps=self.config.crypto_stop_limit_offset_bps,
            crypto_emergency_exit_bps=self.config.crypto_emergency_exit_bps,
            crypto_stop_cancel_timeout_seconds=self.config.crypto_stop_cancel_timeout_seconds,
            crypto_stop_cancel_poll_interval_seconds=(
                self.config.crypto_stop_cancel_poll_interval_seconds
            ),
        )
        self._portfolio_repository = PortfolioStateRepository(
            self.config.decision_db_path, store=self.store
        )
        self._portfolio_runtimes: tuple[PortfolioRuntime, ...] = ()
        self.ai_journal = None
        if self.config.ai_logging_enabled:
            try:
                self.ai_journal = AIDecisionJournal(
                    self.config.ai_log_path,
                    "paper" if self.paper else "live",
                    self.config.timeframe,
                    self.config.prediction_horizon_bars,
                )
            except Exception:
                log.exception("AI decision journal initialization failed; trading continues")
        # The runtime deliberately runs with zero signal strategies: trading
        # decisions come from the portfolio-strategy layer (approval-gated),
        # so the engine's signal-strategy seam stays empty in production.
        self.engine = TradingEngine(
            self.broker,
            self.config,
            journal=self.journal_writer,
            ai_journal=self.ai_journal,
            enforce_live_safety=True,
            paper_mode=self.paper,
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
        self.execution = ExecutionCoordinator(
            self.engine.orders,
            self.engine.risk,
            self.ledger,
        )
        self.stream = AlpacaBarStream(api_key, secret_key, config.alpaca.stock_feed)
        self.trading_updates = AlpacaTradingUpdateStream(
            api_key, secret_key, self.paper, self._on_trade_update
        )
        self.primary_aggregator = TimeframeAggregator(
            self.config.timeframe,
            self.config.data_finalization_grace_seconds,
        )
        self.higher_aggregator = TimeframeAggregator(
            self.config.higher_timeframe,
            self.config.data_finalization_grace_seconds,
        )
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
        self._state_lock = threading.RLock()
        self._journal_closed = False
        self._state = "stopped"
        self._workers: dict[str, WorkerHealth] = {}
        self._dropped_events = 0
        self._coalesced_events = 0
        self._oldest_received_at: datetime | None = None
        self._event_stats_lock = threading.Lock()
        self._last_account_at: datetime | None = None
        self._circuit_breaker_reason: str | None = None
        self._series_watermarks: dict[tuple[str, str], datetime] = {}
        self._portfolio_watermarks: dict[Market, datetime] = {}
        self._warming_up = False
        self._not_ready_symbols: set[str] = set(self.symbols)
        self._historical: AlpacaHistoricalData | None = None

    def _validate_manifest_account_mode(self) -> None:
        stages = {manifest.approved_stage for manifest in self.approval_manifests}
        if self.app_config.alpaca.paper:
            invalid = stages - {ApprovalStage.SHADOW, ApprovalStage.PAPER}
            if invalid:
                raise ApprovalError("Paper credentials accept only shadow or paper manifests")
            return
        invalid = stages - {
            ApprovalStage.SHADOW,
            ApprovalStage.CANARY,
            ApprovalStage.LIVE,
        }
        if invalid:
            raise ApprovalError("Live credentials refuse paper-stage manifests")
        if stages & {ApprovalStage.CANARY, ApprovalStage.LIVE} and (
            not self.config.allow_live_portfolio_execution
            or self.config.live_portfolio_dollar_cap <= 0
        ):
            raise ApprovalError(
                "Canary/live manifests require explicit live execution and a dollar cap"
            )

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
        healthy = (
            self._running
            and self._state == "running"
            and alive
            and queue_age <= self.config.maximum_queue_age_seconds
            and account_fresh
            and self._circuit_breaker_reason is None
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
                "stream_healthy": stream_healthy,
                "data_gap": (enabled and self._engine_has_market_gap(market)),
                "entry_halt_reason": getattr(self, "_market_entry_halts", {}).get(market),
            }
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
            strategy_status=getattr(self, "strategy_status", "research_only"),
            strategy_manifests=tuple(
                {
                    "checksum": manifest.checksum,
                    "market": manifest.market.value,
                    "stage": manifest.approved_stage.value,
                    "strategy": manifest.strategy_name,
                    "expires_at": manifest.expires_at.isoformat(),
                }
                for manifest in getattr(self, "approval_manifests", ())
            ),
        )

    def start(self) -> None:
        if self._running:
            raise RuntimeError("Engine runtime is already running")
        if self._state == "stop_failed":
            raise RuntimeError("Previous runtime workers did not stop; process restart required")
        # Fail closed on the verified manifest objects themselves. A mutable
        # status label is telemetry and can never authorize broker writes.
        if not getattr(self, "approval_manifests", ()):
            raise RuntimeError(
                "Runtime start refused: no approved portfolio strategy "
                f"(strategy_status={self.strategy_status!r}); "
                "configure a GREEN approval manifest and its expected checksum"
            )
        self._stop_event.clear()
        self._state = "starting"
        try:
            self.broker.reconcile()
        except Exception as exc:
            self._circuit_breaker_reason = (
                "protection_failure"
                if isinstance(exc, ProtectionReconciliationError)
                else "reconciliation_failed"
            )
            self._state = "halted"
            raise
        authorized_live_cap = 0.0
        live_stages = {manifest.approved_stage for manifest in self.approval_manifests} & {
            ApprovalStage.CANARY,
            ApprovalStage.LIVE,
        }
        if not self.paper and live_stages:
            secret = os.getenv(self.config.live_operator_approval_key_env, "")
            approval = LiveOperatorApproval.load_verified(
                self.config.live_operator_approval_path,
                secret=secret,
                account_id=self.broker.account_id or "unknown",
                manifest_checksums=tuple(manifest.checksum for manifest in self.approval_manifests),
            )
            authorized_live_cap = min(
                self.config.live_portfolio_dollar_cap,
                approval.maximum_dollar_exposure,
            )
        self._portfolio_runtimes = tuple(
            PortfolioRuntime(
                manifest,
                self.broker,
                self._portfolio_repository,
                account_id=self.broker.account_id or "unknown",
                allow_live_execution=(
                    not self.paper and self.config.allow_live_portfolio_execution
                ),
                live_dollar_cap=authorized_live_cap,
            )
            for manifest in self.approval_manifests
        )
        with self._state_lock:
            self._pin_managed_symbols()
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
            self.config.timeframe,
        )

    def _pin_managed_symbols(self) -> None:
        """Keep managed positions observable even after scanner/config removal."""
        if getattr(self, "approval_manifests", ()):
            # Broker account snapshots still count outside positions against
            # risk, but a manifest-locked stream universe is never expanded.
            self.symbols = sorted(
                self._symbols_by_market[Market.EQUITY] | self._symbols_by_market[Market.CRYPTO]
            )
            self._expected_all_symbols = set(self.symbols)
            if self.engine.allowed_trade_symbols is not None:
                self.engine.allowed_trade_symbols = set(self.symbols)
            return
        account_id = self.broker.account_id or "unknown"
        entries = self.ledger.managed_entries_for_account(account_id)
        equity_pins = tuple(
            dict.fromkeys(item.canonical_symbol for item in entries if item.market == Market.EQUITY)
        )
        crypto_pins = tuple(
            dict.fromkeys(item.canonical_symbol for item in entries if item.market == Market.CRYPTO)
        )
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
        if self.engine.allowed_trade_symbols is not None:
            self.engine.allowed_trade_symbols = {
                symbol for symbol in self.engine.allowed_trade_symbols if not is_crypto(symbol)
            }
            self.engine.allowed_trade_symbols.update(active_crypto)
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
        if not self._journal_closed:
            try:
                for runtime in getattr(self, "_portfolio_runtimes", ()):
                    runtime.close("stopped")
                # Give in-flight stream-thread writes a moment to land before
                # the SQLite connections below are released.
                time.sleep(0.25)
                self.journal_writer.close()
                self.journal.close()
                self.ledger.close()
            except Exception as exc:
                log.exception("Runtime journal/ledger close failed: %s", exc)
            self._journal_closed = True
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
            self.engine.risk.consecutive_losses = previous.consecutive_losses
            if previous.circuit_breaker_reason in persistent_reasons:
                self._circuit_breaker_reason = previous.circuit_breaker_reason
        for order, remaining_qty in self.ledger.pending_entry_orders(account_id):
            self.engine.orders.create(order)
            self.engine.risk.reserve(order, remaining_qty)

    def _on_critical_journal_failure(self, reason: str) -> None:
        self._circuit_breaker_reason = "critical_journal_failure"
        log.error("Entry circuit breaker opened: %s", reason)

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
        account_id = self.broker.account_id or "unknown"
        portfolio_repository = getattr(self, "_portfolio_repository", None)
        if portfolio_repository is not None and portfolio_repository.has_order(
            update.client_order_id
        ):
            account = await asyncio.to_thread(self.broker.account)
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
                realized_pnl=account.realized_pnl,
                unrealized_pnl=account.unrealized_pnl,
            )
            self._last_account_at = datetime.now(UTC)
            self._persist_risk_state(account)
            return
        event = ExecutionEvent(
            account_id=account_id,
            client_order_id=update.client_order_id,
            broker_order_id=update.broker_order_id,
            parent_order_id=update.parent_order_id,
            role=update.role,
            symbol=update.symbol,
            market=update.market,
            state=update.state,
            requested_qty=update.requested_qty,
            cumulative_filled_qty=update.filled_qty,
            average_fill_price=update.average_fill_price,
            occurred_at=update.timestamp,
            fee=update.fee,
            activity_id=update.activity_id,
        )
        # TradingStream callbacks run on an asyncio loop. SQLite transactions
        # and journal file writes are synchronous, so keep them off that loop.
        transition = await asyncio.to_thread(self.execution.apply, event)
        if (
            update.market == Market.CRYPTO
            and transition.entry_client_order_id
            and update.state is not None
        ):
            if update.role == OrderRole.PROTECTIVE_STOP and update.state == OrderState.FILLED:
                await asyncio.to_thread(
                    self.ledger.set_exit_state,
                    account_id,
                    transition.entry_client_order_id,
                    stop_state="filled",
                    target_state="cancelled_by_stop_fill",
                )
            elif update.role == OrderRole.TAKE_PROFIT:
                target_state = {
                    OrderState.PARTIALLY_FILLED: "target_exit_partially_filled",
                    OrderState.FILLED: "target_exit_filled",
                }.get(update.state)
                if target_state:
                    await asyncio.to_thread(
                        self.ledger.set_exit_state,
                        account_id,
                        transition.entry_client_order_id,
                        target_state=target_state,
                    )
        lifecycle = self.engine.orders.orders.get(update.client_order_id)
        if (
            lifecycle is not None
            and transition.fill_delta > 0
            and update.average_fill_price is not None
        ):
            fill = Fill(
                update.client_order_id,
                update.symbol,
                transition.fill_delta,
                update.average_fill_price,
                lifecycle.order.direction,
                update.timestamp,
                commission=update.fee or 0.0,
            )
            if self.ai_journal is not None:
                await asyncio.to_thread(self.ai_journal.record_broker_fill, fill)
            actual_risk = abs(fill.price - lifecycle.order.stop_price) * fill.qty + fill.commission
            approved_risk = (
                abs(lifecycle.order.entry_price - lifecycle.order.stop_price) * lifecycle.order.qty
            )
            if approved_risk > 0 and actual_risk > approved_risk * 1.05:
                self._circuit_breaker_reason = "post_fill_risk_violation"
            if update.market == Market.CRYPTO and update.role == OrderRole.ENTRY:
                try:
                    if not await asyncio.to_thread(
                        self.broker.protect_recovered_crypto_entry, lifecycle.order
                    ):
                        with getattr(self, "_event_stats_lock", threading.Lock()):
                            self._market_entry_halts[Market.CRYPTO] = (
                                "protection_reconciliation_required"
                            )
                except AmbiguousOrderState as exc:
                    with getattr(self, "_event_stats_lock", threading.Lock()):
                        self._market_entry_halts[Market.CRYPTO] = (
                            "protection_reconciliation_required"
                        )
                    # A symbol-specific protection ambiguity must not tear down
                    # the shared TradingStream callback. Reconciliation keeps
                    # retrying while new crypto entries remain halted.
                    log.error(
                        "Recovered crypto entry protection is ambiguous for %s: %s",
                        update.symbol,
                        exc,
                    )

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
            if corrected:
                primary = self.primary_aggregator.update_correction(bar)
                higher = self.higher_aggregator.update_correction(bar)
            else:
                primary = self.primary_aggregator.update(bar)
                higher = self.higher_aggregator.update(bar)
        kind = "correction" if corrected else "primary"
        if higher is not None:
            self._put_event("higher" if not corrected else kind, higher)
        if primary is not None:
            self._put_event(kind, primary)

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
                    if item.kind == "higher":
                        self.engine.seed([item.bar])
                    elif item.kind == "correction":
                        self.engine.ingest_corrections([item.bar])
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
                            self.engine.ingest_corrections([item.bar])
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
                    self._oldest_received_at = min(
                        (
                            candidate
                            for candidate in (
                                self._events.oldest_received_at(),
                                pending_oldest,
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
            self._circuit_breaker_reason = "decision_worker_crashed"
            log.exception("Decision worker failed")
        finally:
            worker.alive = False

    def _flush_aggregators(self) -> None:
        now = datetime.now(UTC)
        local = now.astimezone(NEW_YORK)
        with self._ingestion_lock:
            primary = self.primary_aggregator.flush_until(now)
            higher = self.higher_aggregator.flush_until(now)
            if local.weekday() < 5 and (local.hour, local.minute) >= (16, 0):
                primary.extend(self.primary_aggregator.flush_session_close(now))
                higher.extend(self.higher_aggregator.flush_session_close(now))
        for bar in higher:
            self._put_event("higher", bar)
        for bar in primary:
            self._put_event("primary", bar)

    def _process_timestamp(
        self,
        timestamp: datetime,
        bars_by_symbol: dict[str, Bar],
        expected: set[str],
    ) -> None:
        bars = list(bars_by_symbol.values())
        ingestion = self.engine.ingest_closed_bars(bars)
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
        try:
            account = self.broker.account()
            self._last_account_at = datetime.now(UTC)
            self._persist_risk_state(account)
        except Exception as exc:
            self._circuit_breaker_reason = "account_snapshot_stale"
            log.warning("Account snapshot unavailable; bars ingested without trading: %s", exc)
            return
        if self._circuit_breaker_reason == "account_snapshot_stale":
            self._circuit_breaker_reason = None
        if self._circuit_breaker_reason is not None:
            return
        portfolio_bars = list(ready_bars.values())
        for portfolio_runtime in getattr(self, "_portfolio_runtimes", ()):
            try:
                portfolio_result = portfolio_runtime.process_closed_bars(portfolio_bars)
            except Exception as exc:
                self._circuit_breaker_reason = "portfolio_runtime_failure"
                self._state = "halted"
                log.exception("Approval-gated portfolio runtime failed closed: %s", exc)
                return
            if portfolio_result.orders:
                log.info(
                    "Portfolio decision: manifest=%s market=%s status=%s orders=%d submitted=%d",
                    portfolio_runtime.manifest.checksum[:12],
                    portfolio_runtime.manifest.market.value,
                    portfolio_result.status,
                    len(portfolio_result.orders),
                    len(portfolio_result.submitted_order_ids),
                )
        result = self.engine.evaluate_timestamp(
            timestamp,
            list(ready_bars),
            account,
            missing_symbols=missing,
        )
        if self.engine.entry_halt_reason is not None:
            self._circuit_breaker_reason = self.engine.entry_halt_reason
        for order in result.orders:
            lifecycle = self.engine.orders.orders[order.client_order_id]
            if lifecycle.state.value == "rejected":
                continue
            log.info(
                "Decision: %s %s qty=%s entry=%.4f stop=%.4f target=%.4f",
                order.strategy,
                order.symbol,
                order.qty,
                order.entry_price,
                order.stop_price,
                order.target_price,
            )

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
        return bool(self.engine.has_data_gaps_for_market(market))

    def _engine_has_symbol_gap(self, symbol: str) -> bool:
        return bool(self.engine.has_data_gaps_for_symbol(symbol))

    def _repair_gap(self, bar: Bar) -> None:
        history = self._historical
        if history is None:
            return
        series = self.engine.bars.get(bar.symbol, bar.timeframe)
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
            if backfill and self.engine.repair_gap(backfill):
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
            primary = self.engine.bars.get(symbol, self.config.timeframe)
            higher = self.engine.bars.get(symbol, self.config.higher_timeframe)
            if len(primary) >= self.config.warmup_bars and len(higher) >= self.config.warmup_bars:
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
            if (
                previous is not None
                and self.engine.risk.consecutive_losses == previous.consecutive_losses
            ):
                realized_delta = float(account.realized_pnl) - previous.realized_pnl
                if abs(realized_delta) > 1e-9:
                    # Insurance for fills recovered from broker activities
                    # while the TradingStream callback was unavailable. Live
                    # callbacks already change the counter, so the equality
                    # guard prevents counting the same close twice.
                    self.engine.risk.record_realized_pnl(realized_delta)
            self.ledger.save_risk_state(
                RiskState(
                    account_id=account_id,
                    session_date=today,
                    day_start_equity=float(account.equity - account.daily_pnl),
                    peak_equity=float(account.peak_equity),
                    realized_pnl=float(account.realized_pnl),
                    unrealized_pnl=float(account.unrealized_pnl),
                    fees=self.ledger.total_fees_for_account(account_id),
                    consecutive_losses=self.engine.risk.consecutive_losses,
                    circuit_breaker_reason=self._circuit_breaker_reason,
                    updated_at=datetime.now(UTC),
                )
            )

    def _reconcile_safely(self) -> None:
        reconcile_lock = getattr(self, "_reconcile_lock", threading.Lock())
        with reconcile_lock:
            try:
                self.broker.reconcile()
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
        if self.engine.has_data_gaps or self._not_ready_symbols:
            raise ValueError("All symbol data must be ready before resetting the safety lock")
        with self._reconcile_lock:
            self.broker.reconcile()
            account_id = self.broker.account_id or "unknown"
            entries = self.ledger.managed_entries_for_account(account_id)
            if any(item.status == "manual_intervention_required" for item in entries):
                raise ValueError("A managed position still requires manual intervention")
            account = self.broker.account()
            self._last_account_at = datetime.now(UTC)
            self.engine.entry_halt_reason = None
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
    ) -> None:
        groups = (
            [symbol for symbol in symbols if not is_crypto(symbol)],
            [symbol for symbol in symbols if is_crypto(symbol)],
        )
        for timeframe in {self.config.timeframe, self.config.higher_timeframe}:
            requested_bars = max(
                self.config.warmup_bars,
                *(
                    runtime.strategy.minimum_history_bars + 5
                    for runtime in self._portfolio_runtimes
                    if runtime.strategy.timeframe == timeframe
                ),
            )
            for group in groups:
                for symbol_batch in _symbol_batches(group):
                    try:
                        batched = history.get_bars_batch(
                            symbol_batch,
                            timeframe,
                            start=_warmup_start(symbol_batch[0], timeframe, requested_bars),
                        )
                        with self._ingestion_lock:
                            for symbol in symbol_batch:
                                bars = batched.get(symbol, [])
                                self.engine.seed(bars[-self.config.warmup_bars :])
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


def _symbol_batches(symbols: list[str]) -> tuple[list[str], ...]:
    return tuple(
        symbols[offset : offset + _WARMUP_SYMBOL_BATCH_SIZE]
        for offset in range(0, len(symbols), _WARMUP_SYMBOL_BATCH_SIZE)
    )


def _warmup_start(symbol: str, timeframe: str, bars: int) -> str:
    minutes = TIMEFRAME_MINUTES[timeframe]
    bars_per_day = (1440 if is_crypto(symbol) else 390) / minutes
    trading_days = bars * 1.5 / max(1, bars_per_day)
    calendar_days = trading_days if is_crypto(symbol) else trading_days * 1.5
    days = max(2, min(3650, math.ceil(calendar_days)))
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
