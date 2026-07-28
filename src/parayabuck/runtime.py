from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

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
from parayabuck.monitoring import AIDecisionJournal, AsyncDecisionJournal, DecisionJournal
from parayabuck.risk.manager import RiskState
from parayabuck.universe import load_active_crypto_symbols, load_active_symbols
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.runtime")
_NEW_YORK = ZoneInfo("America/New_York")
_WARMUP_SYMBOL_BATCH_SIZE = 8


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


@dataclass(frozen=True)
class _Event:
    kind: str
    bar: Bar
    received_at: datetime


class EngineRuntime:
    """Web-manageable streaming runtime for the ParayaBuck engine."""

    def __init__(self, api_key: str, secret_key: str, config: Config) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.app_config = config
        self.config = config.engine
        active_equities = (
            load_active_symbols(config.universe.equity) if config.universe.equity.enabled else ()
        )
        active_crypto = (
            load_active_crypto_symbols(config.universe.crypto)
            if config.universe.crypto.enabled
            else ()
        )
        self.symbols = [canonical_symbol(symbol) for symbol in (*active_equities, *active_crypto)]
        self._symbols_by_market = {
            Market.EQUITY: {symbol for symbol in self.symbols if not is_crypto(symbol)},
            Market.CRYPTO: {symbol for symbol in self.symbols if is_crypto(symbol)},
        }
        self._market_enabled = {
            Market.EQUITY: config.universe.equity.enabled,
            Market.CRYPTO: config.universe.crypto.enabled,
        }
        self._market_entry_halts: dict[Market, str | None] = {
            Market.EQUITY: None,
            Market.CRYPTO: None,
        }
        self.paper = config.alpaca.paper
        self.journal = DecisionJournal(self.config.decision_db_path)
        self.journal_writer = AsyncDecisionJournal(
            self.journal,
            on_critical_failure=self._on_critical_journal_failure,
        )
        self.ledger = ExecutionLedger(self.config.decision_db_path)
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
        self._events: queue.Queue[_Event | None] = queue.Queue(
            maxsize=self.config.event_queue_capacity
        )
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._running = False
        self._ingestion_lock = threading.Lock()
        self._journal_closed = False
        self._state = "stopped"
        self._workers: dict[str, WorkerHealth] = {}
        self._dropped_events = 0
        self._coalesced_events = 0
        self._oldest_received_at: datetime | None = None
        self._last_account_at: datetime | None = None
        self._circuit_breaker_reason: str | None = None
        self._series_watermarks: dict[tuple[str, str], datetime] = {}
        self._portfolio_watermarks: dict[Market, datetime] = {}
        self._warming_up = False
        self._last_entry_cycle_timestamp: datetime | None = None
        self._not_ready_symbols: set[str] = set(self.symbols)
        self._historical: AlpacaHistoricalData | None = None

    @property
    def is_running(self) -> bool:
        return self.health().healthy

    def health(self) -> RuntimeHealth:
        now = datetime.now(UTC)
        queue_age = (
            max(0.0, (now - self._oldest_received_at).total_seconds())
            if self._oldest_received_at is not None
            else 0.0
        )
        required = {"pb-decisions", "pb-trade-updates", "pb-account"}
        market_enabled = getattr(
            self,
            "_market_enabled",
            {
                Market.EQUITY: any(not is_crypto(symbol) for symbol in self.symbols),
                Market.CRYPTO: any(is_crypto(symbol) for symbol in self.symbols),
            },
        )
        symbols_by_market = getattr(
            self,
            "_symbols_by_market",
            {
                Market.EQUITY: {symbol for symbol in self.symbols if not is_crypto(symbol)},
                Market.CRYPTO: {symbol for symbol in self.symbols if is_crypto(symbol)},
            },
        )
        if market_enabled.get(Market.EQUITY, False) and symbols_by_market.get(Market.EQUITY):
            required.add("pb-stocks")
        if market_enabled.get(Market.CRYPTO, False) and symbols_by_market.get(Market.CRYPTO):
            required.add("pb-crypto")
        heartbeat_ttl = max(30.0, self.config.account_poll_interval_seconds * 3)
        blocking_stream_workers = {"pb-stocks", "pb-crypto", "pb-trade-updates"}
        alive = all(
            self._workers.get(name)
            and self._workers[name].alive
            and (
                name in blocking_stream_workers
                or (now - self._workers[name].last_heartbeat).total_seconds() <= heartbeat_ttl
            )
            for name in required
        )
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
            worker = self._workers.get(worker_name)
            not_ready_symbols: set[str] = getattr(self, "_not_ready_symbols", set())
            ready = not any(symbol in not_ready_symbols for symbol in symbols)
            stream_healthy = not enabled or not symbols or bool(worker and worker.alive)
            market_health[market.value] = {
                "enabled": enabled,
                "symbol_count": len(symbols),
                "ready": not enabled or ready,
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
            workers={name: asdict(item) for name, item in self._workers.items()},
            markets=market_health,
        )

    def start(self) -> None:
        if self._running:
            raise RuntimeError("Engine runtime is already running")
        if self._state == "stop_failed":
            raise RuntimeError("Previous runtime workers did not stop; process restart required")
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
        for thread in self._threads:
            now = datetime.now(UTC)
            self._workers[thread.name] = WorkerHealth(now, now)
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
        self._state = "running" if not self._not_ready_symbols else "warming_up"
        log.info(
            "Engine runtime started: mode=%s symbols=%d timeframe=%s",
            "paper" if self.paper else "live",
            len(self.symbols),
            self.config.timeframe,
        )

    def _pin_managed_symbols(self) -> None:
        """Keep managed positions observable even after scanner/config removal."""
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
        active_crypto = load_active_crypto_symbols(
            self.app_config.universe.crypto,
            pinned_symbols=crypto_pins,
        )
        self._symbols_by_market[Market.CRYPTO] = set(active_crypto)
        if self.engine.allowed_trade_symbols is not None:
            self.engine.allowed_trade_symbols = {
                symbol for symbol in self.engine.allowed_trade_symbols if not is_crypto(symbol)
            }
            self.engine.allowed_trade_symbols.update(active_crypto)
        self.symbols = sorted(
            self._symbols_by_market[Market.EQUITY] | self._symbols_by_market[Market.CRYPTO]
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
            if alive:
                self._state = "stop_failed"
                log.error("Runtime workers did not stop cleanly: %s", ", ".join(alive))
                return False
        if not self._journal_closed:
            self.journal_writer.close()
            self.journal.close()
            self.ledger.close()
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
        account_id = self.broker.account_id or "unknown"
        transition = self.execution.apply(
            ExecutionEvent(
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
        )
        if (
            update.market == Market.CRYPTO
            and transition.entry_client_order_id
            and update.state is not None
        ):
            if update.role == OrderRole.PROTECTIVE_STOP and update.state == OrderState.FILLED:
                self.ledger.set_exit_state(
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
                    self.ledger.set_exit_state(
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
            )
            if self.ai_journal is not None:
                self.ai_journal.record_broker_fill(fill)
            actual_risk = abs(fill.price - lifecycle.order.stop_price) * fill.qty + fill.commission
            approved_risk = (
                abs(lifecycle.order.entry_price - lifecycle.order.stop_price) * lifecycle.order.qty
            )
            if approved_risk > 0 and actual_risk > approved_risk * 1.05:
                self._circuit_breaker_reason = "post_fill_risk_violation"
            if update.market == Market.CRYPTO and update.role == OrderRole.ENTRY:
                try:
                    if not self.broker.protect_recovered_crypto_entry(lifecycle.order):
                        self._market_entry_halts[Market.CRYPTO] = (
                            "protection_reconciliation_required"
                        )
                except AmbiguousOrderState:
                    self._market_entry_halts[Market.CRYPTO] = "protection_reconciliation_required"
                    raise

    def _run_stream_worker(self, name: str, target: object) -> None:
        worker = self._workers[name]
        worker.alive = True
        worker.last_heartbeat = datetime.now(UTC)
        try:
            while self._running:
                try:
                    target()  # type: ignore[operator]
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
        if bar.market.value == "equity" and not _is_regular_equity_bar(bar):
            return
        self._mark_market_stream_recovered(bar.market)
        if bar.market == Market.CRYPTO:
            try:
                self.broker.process_crypto_price(
                    CryptoPriceObservation(
                        symbol=bar.symbol,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        source="one_minute_bar",
                        timestamp=bar.timestamp,
                    )
                )
            except AmbiguousOrderState as exc:
                self._market_entry_halts[Market.CRYPTO] = "ambiguous_crypto_exit"
                log.critical("Crypto exit state is ambiguous; crypto entries halted: %s", exc)
            except Exception as exc:
                self._market_entry_halts[Market.CRYPTO] = "crypto_protection_failure"
                log.exception("Crypto protection watcher failed: %s", exc)
        with self._ingestion_lock:
            primary = self.primary_aggregator.update(bar)
            higher = self.higher_aggregator.update(bar)
        if higher is not None:
            self._put_event("higher", higher)
        if primary is not None:
            self._put_event("primary", primary)

    async def _on_updated_minute_bar(self, bar: Bar) -> None:
        if bar.market.value == "equity" and not _is_regular_equity_bar(bar):
            return
        with self._ingestion_lock:
            primary = self.primary_aggregator.update_correction(bar)
            higher = self.higher_aggregator.update_correction(bar)
        if higher is not None:
            self._put_event("correction", higher)
        if primary is not None:
            self._put_event("correction", primary)

    def _put_event(self, kind: str, bar: Bar) -> None:
        event = _Event(kind, bar, datetime.now(UTC))
        try:
            self._events.put_nowait(event)
        except queue.Full:
            key = (kind, bar.symbol, bar.timeframe, bar.timestamp)
            replaced = False
            with self._events.mutex:
                queued = self._events.queue
                for index in range(len(queued) - 1, -1, -1):
                    candidate = queued[index]
                    if candidate is None:
                        continue
                    candidate_key = (
                        candidate.kind,
                        candidate.bar.symbol,
                        candidate.bar.timeframe,
                        candidate.bar.timestamp,
                    )
                    if candidate_key == key:
                        queued[index] = event
                        replaced = True
                        self._coalesced_events += 1
                        break
                if not replaced and kind == "primary":
                    self._circuit_breaker_reason = "final_market_event_queue_overflow"
                elif not replaced:
                    priorities = {"higher": 1, "correction": 2, "primary": 3}
                    victim = min(
                        range(len(queued)),
                        key=lambda index: priorities.get(
                            queued[index].kind if queued[index] is not None else "", 0
                        ),
                    )
                    queued[victim] = event
                    replaced = True
                    self._dropped_events += 1
            if not replaced:
                self._dropped_events += 1
        if self._oldest_received_at is None:
            self._oldest_received_at = event.received_at

    def _decision_loop(self) -> None:
        worker = self._workers["pb-decisions"]
        worker.alive = True
        next_reconcile = time.monotonic() + 60.0
        pending: dict[datetime, dict[str, Bar]] = {}
        first_seen: dict[datetime, float] = {}
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
                        self.engine.ingest_closed_bars([item.bar])
                    else:
                        key = item.bar.timestamp
                        pending.setdefault(key, {})[item.bar.symbol] = item.bar
                        first_seen.setdefault(key, time.monotonic())
                self._flush_aggregators()
                now_mono = time.monotonic()
                expected_all = set().union(
                    *(
                        symbols
                        for candidate_market, symbols in self._symbols_by_market.items()
                        if self._market_enabled.get(candidate_market, False)
                    )
                )
                closable = [
                    timestamp
                    for timestamp, bars in pending.items()
                    if expected_all.issubset(bars)
                    or now_mono - first_seen[timestamp] >= self.config.event_grace_seconds
                ]
                for timestamp in sorted(closable):
                    bars_by_symbol = pending.pop(timestamp)
                    first_seen.pop(timestamp, None)
                    self._process_timestamp(
                        timestamp,
                        bars_by_symbol,
                        expected_all,
                        market=None,
                    )
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
                if self._events.empty():
                    self._oldest_received_at = min(
                        (
                            datetime.now(UTC) - timedelta(seconds=max(0.0, now_mono - seen))
                            for seen in first_seen.values()
                        ),
                        default=None,
                    )
                if time.monotonic() >= next_reconcile:
                    self._reconcile_safely()
                    next_reconcile = time.monotonic() + 60.0
        except Exception as exc:
            worker.last_error = str(exc)
            self._state = "error"
            log.exception("Decision worker failed")
        finally:
            worker.alive = False

    def _flush_aggregators(self) -> None:
        now = datetime.now(UTC)
        local = now.astimezone(_NEW_YORK)
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
        *,
        market: Market | None = None,
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
            try:
                market_reasons[candidate_market] = self._entry_readiness_reasons(candidate_market)
            except TypeError:
                # Compatibility for injected test runtimes and older adapters.
                market_reasons[candidate_market] = self._entry_readiness_reasons()
        ready_bars = {
            symbol: bar
            for symbol, bar in bars_by_symbol.items()
            if symbol not in self._not_ready_symbols and not market_reasons[bar.market]
        }
        if not ready_bars:
            return
        missing = tuple(sorted(expected - set(bars_by_symbol)))
        if missing:
            log.warning(
                "Ranking universe is partial; evaluating available symbols: %s",
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
        result = self.engine.evaluate_timestamp(
            timestamp,
            list(ready_bars),
            account,
            missing_symbols=missing,
        )
        if self.engine.entry_halt_reason is not None:
            self._circuit_breaker_reason = self.engine.entry_halt_reason
        capacity_states = {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.RECONCILIATION_REQUIRED,
            OrderState.UNKNOWN,
        }
        if any(
            self.engine.orders.orders[order.client_order_id].state in capacity_states
            for order in result.orders
        ):
            self._last_entry_cycle_timestamp = timestamp
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
        if market is None and self.engine.has_data_gaps:
            reasons.append("data_gap")
        elif market is not None and self._engine_has_market_gap(market):
            reasons.append(f"{market.value}_data_gap")
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
        checker = getattr(self.engine, "has_data_gaps_for_market", None)
        return bool(checker(market)) if checker is not None else bool(self.engine.has_data_gaps)

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
        if self._running and not self._not_ready_symbols and self._state == "warming_up":
            self._state = "running"

    def _persist_risk_state(self, account: AccountSnapshot) -> None:
        account_id = self.broker.account_id or "unknown"
        today = datetime.now(UTC).date()
        previous = self.ledger.load_risk_state(account_id, today)
        if previous is not None:
            self.engine.risk.consecutive_losses = previous.consecutive_losses
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
        try:
            self.broker.reconcile()
            self._last_account_at = datetime.now(UTC)
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
            log.exception("Periodic position protection check failed")
            self._circuit_breaker_reason = "reconciliation_failed"

    def _warmup(self) -> None:
        history = AlpacaHistoricalData(
            self.api_key,
            self.secret_key,
            stock_feed=self.app_config.alpaca.stock_feed,
            data_delayed=self.app_config.alpaca.data_delayed,
        )
        self._historical = history
        log.info("Warmup starting for %d symbols", len(self.symbols))
        groups = (
            [symbol for symbol in self.symbols if not is_crypto(symbol)],
            [symbol for symbol in self.symbols if is_crypto(symbol)],
        )
        for timeframe in {self.config.timeframe, self.config.higher_timeframe}:
            for symbols in groups:
                for symbol_batch in _symbol_batches(symbols):
                    try:
                        batched = history.get_bars_batch(
                            symbol_batch,
                            timeframe,
                            start=_warmup_start(
                                symbol_batch[0], timeframe, self.config.warmup_bars
                            ),
                        )
                        for symbol in symbol_batch:
                            bars = batched.get(symbol, [])
                            self.engine.seed(bars[-self.config.warmup_bars :])
                            if len(bars) < self.config.warmup_bars:
                                log.warning(
                                    "Warmup not ready %s %s: %d/%d bars",
                                    symbol,
                                    timeframe,
                                    len(bars),
                                    self.config.warmup_bars,
                                )
                    except Exception as exc:
                        log.warning(
                            "Warmup batch skipped %s symbols=%d: %s",
                            timeframe,
                            len(symbol_batch),
                            exc,
                        )
        log.info("Warmup complete")


def _symbol_batches(symbols: list[str]) -> tuple[list[str], ...]:
    return tuple(
        symbols[offset : offset + _WARMUP_SYMBOL_BATCH_SIZE]
        for offset in range(0, len(symbols), _WARMUP_SYMBOL_BATCH_SIZE)
    )


def _warmup_start(symbol: str, timeframe: str, bars: int) -> str:
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}[timeframe]
    bars_per_day = (1440 if is_crypto(symbol) else 390) / minutes
    trading_days = bars * 1.5 / max(1, bars_per_day)
    calendar_days = trading_days if is_crypto(symbol) else trading_days * 1.5
    days = max(2, min(180, math.ceil(calendar_days)))
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


def _is_regular_equity_bar(bar: Bar) -> bool:
    local = bar.timestamp.astimezone(_NEW_YORK)
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and 9 * 60 + 30 <= minute < 16 * 60


__all__ = ["EngineRuntime"]
