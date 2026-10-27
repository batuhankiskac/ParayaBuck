"""Portfolio performance series for the dashboard chart (display only).

Reads the broker's own equity history and the configured benchmark (VOO by
default) over the same window. Nothing here feeds trading state, decisions, or the runtime; every
failure degrades to an explicit ``available: False`` / ``benchmark_error``.
Results are cached per (period, mode) for a short TTL because the browser
re-requests the series on every period switch.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from parayabuck.config import load_config
from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.services.performance")

# Default chart benchmark. ``app.benchmark_symbol`` in the config file overrides
# it (see benchmark_symbol). The backtest's SPY default is a separate,
# backtest-only choice and is not read here.
BENCHMARK_SYMBOL = "VOO"
CACHE_TTL_SECONDS = 60.0

# period key -> (broker period, broker timeframe, benchmark bar minutes)
PERIODS: dict[str, tuple[str, str, int]] = {
    "1D": ("1D", "5Min", 5),
    "1W": ("1W", "1H", 60),
    "1M": ("1M", "1D", 1440),
    "3M": ("3M", "1D", 1440),
    "1A": ("1A", "1D", 1440),
    "ALL": ("5A", "1D", 1440),
}

BenchmarkFetcher = Callable[[Any, datetime, datetime, int], list[tuple[datetime, float]]]

_cache_lock = threading.Lock()
_cache: dict[tuple[str, bool, str], tuple[float, dict[str, Any]]] = {}


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _series(points: list[tuple[datetime, float]]) -> list[dict[str, Any]]:
    return [{"t": stamp.isoformat(), "v": round(value, 4)} for stamp, value in points]


def benchmark_symbol(manager: Any) -> str:
    """The chart benchmark: ``app.benchmark_symbol`` from the manager's config file.

    Display only. An unreadable config falls back to :data:`BENCHMARK_SYMBOL`,
    which is also the shipped default, so the chart never goes blank over it.
    """
    path = getattr(manager, "config_path", None)
    if path is None:
        return BENCHMARK_SYMBOL
    try:
        return load_config(str(path)).app.benchmark_symbol
    except Exception as exc:
        log.warning("Benchmark symbol config unreadable; using %s: %s", BENCHMARK_SYMBOL, exc)
        return BENCHMARK_SYMBOL


def fetch_benchmark_bars(manager: Any, start: datetime, end: datetime, minutes: int) -> list[tuple[datetime, float]]:
    """Benchmark closes from the IEX feed (free plan) over ``[start, end]``.

    Dividend-adjusted, so the benchmark is a total return like the backtest's
    benchmark; raw closes would understate it by the yield.
    """
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    key, secret = manager.get_credentials()
    if not key or not secret:
        raise ValueError("Alpaca credentials are not configured")
    client = StockHistoricalDataClient(key, secret)
    session = getattr(client, "_session", None)
    if session is not None:
        from parayabuck.utils.http import patch_session_timeout

        patch_session_timeout(session, timeout=15.0)
    symbol = benchmark_symbol(manager)
    if minutes >= 1440:
        timeframe = TimeFrame.Day
    elif minutes >= 60:
        timeframe = TimeFrame.Hour
    else:
        timeframe = TimeFrame(minutes, TimeFrameUnit.Minute)
    try:
        response: Any = client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                feed=DataFeed.IEX,
                adjustment=Adjustment.ALL,
            )
        )
    finally:
        # A client is built per cache miss: release its pooled connections now
        # rather than leaving the requests.Session to the garbage collector.
        if session is not None:
            session.close()
    data = getattr(response, "data", None) or {}
    bars = data.get(symbol, []) if isinstance(data, dict) else []
    return [(bar.timestamp, float(bar.close)) for bar in bars if bar.close]


def _redacted(manager: Any, text: str) -> str:
    """``text`` with the configured Alpaca keys removed (managers without the helper pass through)."""
    redact = getattr(manager, "redact_secrets", None)
    return redact(text) if callable(redact) else text


def _ensure_broker(manager: Any) -> Any:
    broker = getattr(manager, "broker", None)
    if broker is None:
        # The account snapshot path owns lazy broker construction.
        fetch = getattr(manager, "fetch_account_snapshot", None)
        if callable(fetch):
            try:
                fetch()
            except Exception as exc:
                log.debug("Account snapshot before history failed: %s", exc)
        broker = getattr(manager, "broker", None)
    return broker


def _prior_close_benchmark(manager: Any, fetcher: BenchmarkFetcher, first_stamp: datetime) -> float | None:
    """Benchmark close of the last full session before ``first_stamp``'s UTC day, or ``None``."""
    day = first_stamp.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    closes = [
        (stamp, value)
        for stamp, value in fetcher(manager, day - timedelta(days=7), day, 1440)
        if stamp < day and value > 0
    ]
    return max(closes, key=lambda item: item[0])[1] if closes else None


def portfolio_performance(
    manager: Any,
    period: str,
    *,
    benchmark_fetcher: BenchmarkFetcher | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Equity + benchmark series for ``period`` (one of :data:`PERIODS`)."""
    key = period.upper()
    if key not in PERIODS:
        raise ValueError(f"Unknown period {period!r}; expected one of {', '.join(PERIODS)}")
    try:
        paper = bool(manager.is_paper_mode())
    except Exception:
        paper = True
    # The cache must not survive a credential rotation: broker identity (not
    # just period+paper) selects the account whose series is served.
    broker = _ensure_broker(manager)
    account_id = getattr(broker, "account_id", None) if broker is not None else None
    cache_key = (key, paper, str(account_id or ""))
    now = clock()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None and now < cached[0]:
            return cached[1]
    broker_period, broker_timeframe, bench_minutes = PERIODS[key]
    payload: dict[str, Any] = {
        "period": key,
        "benchmark_symbol": benchmark_symbol(manager),
        "available": False,
        "equity": [],
        "benchmark": [],
        # Prior-close bases for the 1D return; null for other periods, which
        # the browser rebases to their first point.
        "base_equity": None,
        "base_benchmark": None,
    }
    history = getattr(broker, "portfolio_history", None)
    history_with_base = getattr(broker, "portfolio_history_with_base", None)
    if broker is None or not callable(history):
        payload["reason"] = "Broker bağlantısı yok (API anahtarlarını kontrol edin)"
        return payload
    try:
        if callable(history_with_base):
            points, base_value = history_with_base(broker_period, broker_timeframe)
        else:
            points, base_value = history(broker_period, broker_timeframe), None
    except Exception as exc:
        log.warning("Portfolio history failed: %s", exc)
        payload["reason"] = f"Hesap geçmişi alınamadı: {_redacted(manager, str(exc))}"
        return payload
    payload["available"] = True
    payload["equity"] = _series(points)
    if len(points) >= 2:
        fetcher = benchmark_fetcher or fetch_benchmark_bars
        try:
            payload["benchmark"] = _series(fetcher(manager, points[0][0], points[-1][0], bench_minutes))
        except Exception as exc:
            log.info("Benchmark bars unavailable: %s", exc)
            payload["benchmark_error"] = _redacted(manager, str(exc))
        if key == "1D":
            # The 1D return is measured from the prior close (the broker's
            # base_value), not the first intraday point, so the equity line and
            # the benchmark line start from the same prior session.
            payload["base_equity"] = base_value
            if base_value is not None:
                fetcher = benchmark_fetcher or fetch_benchmark_bars
                try:
                    base_bench = _prior_close_benchmark(manager, fetcher, points[0][0])
                except Exception as exc:
                    log.info("Benchmark prior close unavailable: %s", exc)
                    base_bench = None
                if base_bench is None:
                    # Without a matching benchmark base the two lines would start
                    # at different times: drop the benchmark instead of misaligning it.
                    payload["benchmark"] = []
                    payload.setdefault("benchmark_error", "Önceki kapanış kıyas verisi alınamadı")
                else:
                    payload["base_benchmark"] = base_bench
    with _cache_lock:
        _cache[cache_key] = (now + CACHE_TTL_SECONDS, payload)
    return payload
