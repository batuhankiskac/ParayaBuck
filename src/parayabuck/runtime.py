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
from parayabuck.core.models import AccountSnapshot, Bar, Fill, OrderState
from parayabuck.data import AlpacaBarStream, AlpacaHistoricalData, TimeframeAggregator
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream, TradeUpdate
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.monitoring import AIDecisionJournal, AsyncDecisionJournal, DecisionJournal
from parayabuck.risk.manager import RiskState
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.runtime")
_NEW_YORK = ZoneInfo("America/New_York")


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
        self.symbols = [canonical_symbol(symbol) for symbol in config.symbols]
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
        )
        self.stream = AlpacaBarStream(api_key, secret_key, config.alpaca.stock_feed)
        self.trading_updates = AlpacaTradingUpdateStream(
            api_key, secret_key, self.paper, self._on_trade_update
        )
        self.primary_aggregator = TimeframeAggregator(self.config.timeframe)
        self.higher_aggregator = TimeframeAggregator(self.config.higher_timeframe)
        self._events: queue.Queue[_Event | None] = queue.Queue(
            maxsize=self.config.event_queue_capacity
        )
        self._threads: list[threading.Thread] = []
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
        self._last_closed_timestamp: datetime | None = None
        self._warming_up = False
        self._not_ready_symbols: set[str] = set(self.symbols)

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
        required = {"pb-decisions", "pb-trade-updates"}
        if any(not is_crypto(symbol) for symbol in self.symbols):
            required.add("pb-stocks")
        if any(is_crypto(symbol) for symbol in self.symbols):
            required.add("pb-crypto")
        alive = all(self._workers.get(name) and self._workers[name].alive for name in required)
        account_fresh = (
            self._last_account_at is not None
            and (now - self._last_account_at).total_seconds()
            <= self.config.account_snapshot_ttl_seconds
        )
        healthy = (
            self._running
            and self._state == "running"
            and alive
            and queue_age <= self.config.maximum_queue_age_seconds
            and account_fresh
            and not self.engine.has_data_gaps
            and self._circuit_breaker_reason is None
        )
        state = self._state
        if self._running and not healthy and state == "running":
            state = "degraded"
        return RuntimeHealth(
            state=state,
            healthy=healthy,
            circuit_breaker_reason=self._circuit_breaker_reason,
            queue_size=self._events.qsize(),
            queue_age_seconds=queue_age,
            dropped_events=self._dropped_events,
            coalesced_events=self._coalesced_events,
            workers={name: asdict(item) for name, item in self._workers.items()},
        )

    def start(self) -> None:
        if self._running:
            raise RuntimeError("Engine runtime is already running")
        self._state = "starting"
        try:
            self.broker.reconcile()
        except Exception:
            self._circuit_breaker_reason = "reconciliation_failed"
            self._state = "halted"
            raise
        self._last_account_at = datetime.now(UTC)
        self._state = "warming_up"
        stocks = [symbol for symbol in self.symbols if not is_crypto(symbol)]
        crypto = [symbol for symbol in self.symbols if is_crypto(symbol)]
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

    def stop(self) -> None:
        if self._running:
            self._state = "stopping"
            self._running = False
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
                log.warning("Stream workers did not stop cleanly: %s", ", ".join(alive))
        if not self._journal_closed:
            self.journal_writer.close()
            self.journal.close()
            self.ledger.close()
            self._journal_closed = True
        self._state = "stopped"
        log.info("Engine runtime stopped")

    def _on_critical_journal_failure(self, reason: str) -> None:
        self._circuit_breaker_reason = "critical_journal_failure"
        log.error("Entry circuit breaker opened: %s", reason)

    async def _on_trade_update(self, update: TradeUpdate) -> None:
        self.ledger.record_order_update(
            update.client_order_id,
            update.broker_order_id,
            update.event,
            update.timestamp,
            update.requested_qty,
            update.filled_qty,
            update.average_fill_price,
            update.state.value
            if update.state
            in {
                OrderState.FILLED,
                OrderState.CANCELLED,
                OrderState.REJECTED,
                OrderState.EXPIRED,
            }
            else None,
        )
        lifecycle = self.engine.orders.orders.get(update.client_order_id)
        if lifecycle is None:
            return
        if update.state is not None and lifecycle.state != update.state:
            try:
                self.engine.orders.transition(
                    update.client_order_id, update.state, f"trade_update:{update.event}"
                )
            except ValueError:
                # Duplicate or out-of-order broker updates remain in the event
                # ledger but cannot move the local state machine backwards.
                pass
        if update.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
            self.engine.risk.reserve(
                lifecycle.order,
                update.requested_qty - update.filled_qty,
            )
        elif update.state is not None:
            self.engine.risk.release(update.client_order_id)
        if update.filled_qty > 0 and update.average_fill_price is not None:
            fill = Fill(
                update.client_order_id,
                update.symbol,
                update.filled_qty,
                update.average_fill_price,
                lifecycle.order.direction,
                update.timestamp,
            )
            self.ledger.record_entry_fill(
                self.broker.account_id or "unknown",
                lifecycle.order,
                fill,
                update.broker_order_id,
            )

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
                    self._state = "degraded"
                    if worker.restart_count > 5:
                        self._state = "halted"
                        self._circuit_breaker_reason = f"{name}_restart_limit"
                        log.exception("%s stream worker restart limit reached", name)
                        return
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

    async def _on_minute_bar(self, bar: Bar) -> None:
        if bar.market.value == "equity" and not _is_regular_equity_bar(bar):
            return
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
            try:
                self._events.get_nowait()
                self._dropped_events += 1
            except queue.Empty:
                pass
            try:
                self._events.put_nowait(event)
                self._coalesced_events += 1
            except queue.Full:
                self._dropped_events += 1
        if self._oldest_received_at is None:
            self._oldest_received_at = event.received_at

    def _decision_loop(self) -> None:
        worker = self._workers["pb-decisions"]
        worker.alive = True
        next_reconcile = time.monotonic() + 60.0
        pending: dict[datetime, dict[str, Bar]] = {}
        first_seen: dict[datetime, float] = {}
        expected = {symbol for symbol in self.symbols}
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
                    elif (
                        self._last_closed_timestamp is not None
                        and item.bar.timestamp <= self._last_closed_timestamp
                    ):
                        self.engine.ingest_closed_bars([item.bar])
                    else:
                        pending.setdefault(item.bar.timestamp, {})[item.bar.symbol] = item.bar
                        first_seen.setdefault(item.bar.timestamp, time.monotonic())
                self._flush_aggregators()
                now_mono = time.monotonic()
                closable = [
                    timestamp
                    for timestamp, bars in pending.items()
                    if expected.issubset(bars)
                    or now_mono - first_seen[timestamp] >= self.config.event_grace_seconds
                ]
                for timestamp in sorted(closable):
                    bars_by_symbol = pending.pop(timestamp)
                    first_seen.pop(timestamp, None)
                    self._process_timestamp(timestamp, bars_by_symbol, expected)
                    self._last_closed_timestamp = timestamp
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
        self, timestamp: datetime, bars_by_symbol: dict[str, Bar], expected: set[str]
    ) -> None:
        bars = list(bars_by_symbol.values())
        self.engine.ingest_closed_bars(bars)
        if self._warming_up:
            return
        self._refresh_readiness()
        ready_bars = {
            symbol: bar
            for symbol, bar in bars_by_symbol.items()
            if symbol not in self._not_ready_symbols
        }
        if not ready_bars:
            return
        missing = tuple(sorted(expected - set(bars_by_symbol)))
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
                fees=previous.fees if previous is not None else 0.0,
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
        log.info("Warmup starting for %d symbols", len(self.symbols))
        groups = (
            [symbol for symbol in self.symbols if not is_crypto(symbol)],
            [symbol for symbol in self.symbols if is_crypto(symbol)],
        )
        for timeframe in {self.config.timeframe, self.config.higher_timeframe}:
            for symbols in groups:
                if not symbols:
                    continue
                try:
                    batched = history.get_bars_batch(
                        symbols,
                        timeframe,
                        start=_warmup_start(symbols[0], timeframe, self.config.warmup_bars),
                    )
                    for symbol in symbols:
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
                        len(symbols),
                        exc,
                    )
        log.info("Warmup complete")


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
