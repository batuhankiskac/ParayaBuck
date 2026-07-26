from __future__ import annotations

import math
import queue
import threading
import time
from datetime import UTC, datetime, timedelta

from requests.exceptions import RequestException

from parayabuck.config import Config
from parayabuck.core.models import Bar
from parayabuck.data import AlpacaBarStream, AlpacaHistoricalData, TimeframeAggregator
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.logger import get_logger
from parayabuck.monitoring import AIDecisionJournal, DecisionJournal
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.runtime")


class EngineRuntime:
    """Web-manageable streaming runtime for the ParayaBuck engine."""

    def __init__(self, api_key: str, secret_key: str, config: Config) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.app_config = config
        self.config = config.engine
        self.symbols = list(config.symbols)
        self.paper = config.alpaca.paper
        self.broker = AlpacaBroker(api_key, secret_key, paper=self.paper)
        self.journal = DecisionJournal(self.config.decision_db_path)
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
            journal=self.journal,
            ai_journal=self.ai_journal,
        )
        self.stream = AlpacaBarStream(api_key, secret_key, config.alpaca.stock_feed)
        self.primary_aggregator = TimeframeAggregator(self.config.timeframe)
        self.higher_aggregator = TimeframeAggregator(self.config.higher_timeframe)
        self._events: queue.Queue[tuple[str, Bar] | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._running = False
        self._ingestion_lock = threading.Lock()
        self._journal_closed = False

    @property
    def is_running(self) -> bool:
        streams = [thread for thread in self._threads if thread.name != "pb-decisions"]
        return self._running and bool(streams) and any(thread.is_alive() for thread in streams)

    def start(self) -> None:
        if self._running:
            raise RuntimeError("Engine runtime is already running")
        self.broker.reconcile()
        self._warmup()
        stocks = [symbol for symbol in self.symbols if not is_crypto(symbol)]
        crypto = [symbol for symbol in self.symbols if is_crypto(symbol)]
        self.stream.subscribe(stocks, crypto, self._on_minute_bar)
        self._running = True
        self._threads = [
            threading.Thread(target=self._decision_loop, name="pb-decisions", daemon=True)
        ]
        if stocks:
            self._threads.append(
                threading.Thread(target=self.stream.run_stock, name="pb-stocks", daemon=True)
            )
        if crypto:
            self._threads.append(
                threading.Thread(target=self.stream.run_crypto, name="pb-crypto", daemon=True)
            )
        for thread in self._threads:
            thread.start()
        log.info(
            "Engine runtime started: mode=%s symbols=%d timeframe=%s",
            "paper" if self.paper else "live",
            len(self.symbols),
            self.config.timeframe,
        )

    def stop(self) -> None:
        if self._running:
            self._running = False
            self._events.put(None)
            try:
                self.stream.stop()
            except Exception as exc:
                log.warning("Stream stop warning: %s", exc)
            for thread in self._threads:
                thread.join(timeout=5)
            alive = [thread.name for thread in self._threads if thread.is_alive()]
            if alive:
                log.warning("Stream workers did not stop cleanly: %s", ", ".join(alive))
        if not self._journal_closed:
            self.journal.close()
            self._journal_closed = True
        log.info("Engine runtime stopped")

    async def _on_minute_bar(self, bar: Bar) -> None:
        with self._ingestion_lock:
            primary = self.primary_aggregator.update(bar)
            higher = self.higher_aggregator.update(bar)
        if higher is not None:
            self._events.put(("higher", higher))
        if primary is not None:
            self._events.put(("primary", primary))

    def _decision_loop(self) -> None:
        next_reconcile = time.monotonic() + 60.0
        while self._running:
            try:
                first = self._events.get(timeout=max(0.0, next_reconcile - time.monotonic()))
            except queue.Empty:
                self._reconcile_safely()
                next_reconcile = time.monotonic() + 60.0
                continue
            if first is None:
                break
            batch = [first]
            deadline = time.monotonic() + 0.25
            while time.monotonic() < deadline:
                try:
                    item = self._events.get(timeout=max(0.0, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if item is None:
                    self._running = False
                    break
                batch.append(item)
            primary: list[Bar] = []
            for kind, bar in batch:
                if kind == "higher":
                    self.engine.seed([bar])
                else:
                    primary.append(bar)
            grouped: dict[datetime, list[Bar]] = {}
            for bar in primary:
                grouped.setdefault(bar.timestamp, []).append(bar)
            for bars in grouped.values():
                try:
                    orders = self.engine.process_cycle(bars)
                    for order in orders:
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
                except Exception:
                    log.exception("Decision cycle failed")
            if time.monotonic() >= next_reconcile:
                self._reconcile_safely()
                next_reconcile = time.monotonic() + 60.0

    def _reconcile_safely(self) -> None:
        try:
            self.broker.reconcile()
        except RequestException as exc:
            # Alpaca occasionally times out for a single request. The next
            # scheduled reconciliation retries it; a full traceback every
            # minute obscures actionable protection failures.
            log.warning("Position protection check deferred after Alpaca API error: %s", exc)
        except Exception:
            log.exception("Periodic position protection check failed")

    def _warmup(self) -> None:
        history = AlpacaHistoricalData(
            self.api_key,
            self.secret_key,
            stock_feed=self.app_config.alpaca.stock_feed,
            data_delayed=self.app_config.alpaca.data_delayed,
        )
        log.info("Warmup starting for %d symbols", len(self.symbols))
        for symbol in self.symbols:
            for timeframe in {self.config.timeframe, self.config.higher_timeframe}:
                try:
                    bars = history.get_bars(
                        symbol,
                        timeframe,
                        start=_warmup_start(symbol, timeframe, self.config.warmup_bars),
                    )
                    self.engine.seed(bars[-self.config.warmup_bars :])
                except Exception as exc:
                    log.warning("Warmup skipped %s %s: %s", symbol, timeframe, exc)
        log.info("Warmup complete")


def _warmup_start(symbol: str, timeframe: str, bars: int) -> str:
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}[timeframe]
    bars_per_day = (1440 if is_crypto(symbol) else 390) / minutes
    trading_days = bars * 1.5 / max(1, bars_per_day)
    calendar_days = trading_days if is_crypto(symbol) else trading_days * 1.5
    days = max(2, min(180, math.ceil(calendar_days)))
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


__all__ = ["EngineRuntime"]
