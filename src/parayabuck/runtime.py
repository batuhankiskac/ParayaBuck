from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from parayabuck.app_config import Config
from parayabuck.data.historical import AlpacaDataSource
from parayabuck.logger import get_logger
from parayabuck.utils import is_crypto
from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data import AlpacaBarStream, TimeframeAggregator
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import AlpacaExecutionBroker
from parayabuck.monitoring import DecisionJournal

log = get_logger("parayabuck.runtime")


class TradingRuntime:
    """Web-manageable trading runtime."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        app_config: Config,
        engine_config: EngineConfig,
        symbols: list[str],
        paper: bool,
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.app_config = app_config
        self.config = replace(
            engine_config,
            timeframe=app_config.app.timeframe,
            shadow_mode=False,
        )
        self.symbols = symbols
        self.paper = paper
        self.broker = AlpacaExecutionBroker(api_key, secret_key, paper=paper)
        self.journal = DecisionJournal(self.config.decision_db_path)
        self.engine = TradingEngine(self.broker, self.config, journal=self.journal)
        self.stream = AlpacaBarStream(api_key, secret_key, app_config.alpaca.stock_feed)
        self.primary_aggregator = TimeframeAggregator(self.config.timeframe)
        self.higher_aggregator = TimeframeAggregator(self.config.higher_timeframe)
        self._events: queue.Queue[tuple[str, Bar] | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._running = False
        self._ingestion_lock = threading.Lock()
        self._journal_closed = False

    @property
    def is_running(self) -> bool:
        stream_threads = [thread for thread in self._threads if thread.name != "pb2-decisions"]
        return (
            self._running
            and bool(stream_threads)
            and any(thread.is_alive() for thread in stream_threads)
        )

    def start(self) -> None:
        if self._running:
            raise RuntimeError("Trading runtime is already running")
        self.broker.reconcile()
        self._warmup()
        stocks = [symbol for symbol in self.symbols if not is_crypto(symbol)]
        crypto = [symbol for symbol in self.symbols if is_crypto(symbol)]
        self.stream.subscribe(stocks, crypto, self._on_minute_bar)
        self._running = True
        self._threads = [
            threading.Thread(target=self._decision_loop, name="pb2-decisions", daemon=True)
        ]
        if stocks:
            self._threads.append(
                threading.Thread(target=self.stream.run_stock, name="pb2-stocks", daemon=True)
            )
        if crypto:
            self._threads.append(
                threading.Thread(target=self.stream.run_crypto, name="pb2-crypto", daemon=True)
            )
        for thread in self._threads:
            thread.start()
        log.info(
            "Runtime started: mode=%s symbols=%d timeframe=%s",
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
        if not self._journal_closed:
            self.journal.close()
            self._journal_closed = True
        log.info("Runtime stopped")

    async def _on_minute_bar(self, bar: Bar) -> None:
        with self._ingestion_lock:
            primary = self.primary_aggregator.update(bar)
            higher = self.higher_aggregator.update(bar)
        if higher is not None:
            self._events.put(("higher", higher))
        if primary is not None:
            self._events.put(("primary", primary))

    def _decision_loop(self) -> None:
        while self._running:
            first = self._events.get()
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
            if not primary:
                continue
            try:
                grouped: dict[datetime, list[Bar]] = {}
                for bar in primary:
                    grouped.setdefault(bar.timestamp, []).append(bar)
                for bars in grouped.values():
                    orders = self.engine.process_cycle(bars)
                    for order in orders:
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

    def _warmup(self) -> None:
        history = AlpacaDataSource(
            self.api_key,
            self.secret_key,
            stock_feed=self.app_config.alpaca.stock_feed,
            data_delayed=self.app_config.alpaca.data_delayed,
        )
        log.info("Warmup starting for %d symbols", len(self.symbols))
        for symbol in self.symbols:
            market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
            for timeframe in {self.config.timeframe, self.config.higher_timeframe}:
                try:
                    raw = history.get_bars(
                        symbol,
                        timeframe,
                        start=_warmup_start(symbol, timeframe, self.config.warmup_bars),
                        use_cache=False,
                    )
                    converted = [_convert_bar(bar, market) for bar in raw]
                    self.engine.seed(converted[-self.config.warmup_bars :])
                except Exception as exc:
                    log.warning("Warmup skipped %s %s: %s", symbol, timeframe, exc)
        log.info("Warmup complete")


def _convert_bar(bar: object, market: Market) -> Bar:
    timestamp = getattr(bar, "timestamp")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    else:
        timestamp = timestamp.astimezone(UTC)
    return Bar(
        timestamp=timestamp,
        symbol=str(getattr(bar, "symbol")),
        timeframe=str(getattr(bar, "timeframe")),
        open=float(getattr(bar, "open")),
        high=float(getattr(bar, "high")),
        low=float(getattr(bar, "low")),
        close=float(getattr(bar, "close")),
        volume=float(getattr(bar, "volume")),
        market=market,
    )


def _warmup_start(symbol: str, timeframe: str, bars: int) -> str:
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}[timeframe]
    bars_per_day = (1440 if is_crypto(symbol) else 390) / minutes
    trading_days = bars * 1.5 / max(1, bars_per_day)
    calendar_days = trading_days if is_crypto(symbol) else trading_days * 1.5
    days = max(2, min(180, math.ceil(calendar_days)))
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
