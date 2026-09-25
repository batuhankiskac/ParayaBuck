"""Portfolio performance series for the dashboard chart (display only).

Reads the broker's own equity history and a VOO benchmark over the same
window. Nothing here feeds trading state, decisions, or the runtime; every
failure degrades to an explicit ``available: False`` / ``benchmark_error``.
Results are cached per (period, mode) for a short TTL because the browser
re-requests the series on every period switch.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.services.performance")

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
_cache: dict[tuple[str, bool], tuple[float, dict[str, Any]]] = {}


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _series(points: list[tuple[datetime, float]]) -> list[dict[str, Any]]:
    return [{"t": stamp.isoformat(), "v": round(value, 4)} for stamp, value in points]


def fetch_benchmark_bars(manager: Any, start: datetime, end: datetime, minutes: int) -> list[tuple[datetime, float]]:
    """VOO closes from the IEX feed (free plan) over ``[start, end]``."""
    from alpaca.data.enums import DataFeed
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
    if minutes >= 1440:
        timeframe = TimeFrame.Day
    elif minutes >= 60:
        timeframe = TimeFrame.Hour
    else:
        timeframe = TimeFrame(minutes, TimeFrameUnit.Minute)
    response: Any = client.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=BENCHMARK_SYMBOL,
            timeframe=timeframe,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
    )
    data = getattr(response, "data", None) or {}
    bars = data.get(BENCHMARK_SYMBOL, []) if isinstance(data, dict) else []
    return [(bar.timestamp, float(bar.close)) for bar in bars if bar.close]


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
    cache_key = (key, paper)
    now = clock()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None and now < cached[0]:
            return cached[1]

    broker_period, broker_timeframe, bench_minutes = PERIODS[key]
    payload: dict[str, Any] = {
        "period": key,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "available": False,
        "equity": [],
        "benchmark": [],
    }
    broker = _ensure_broker(manager)
    history = getattr(broker, "portfolio_history", None)
    if broker is None or not callable(history):
        payload["reason"] = "Broker bağlantısı yok (API anahtarlarını kontrol edin)"
        return payload
    try:
        points = history(broker_period, broker_timeframe)
    except Exception as exc:
        log.warning("Portfolio history failed: %s", exc)
        payload["reason"] = f"Hesap geçmişi alınamadı: {exc}"
        return payload
    payload["available"] = True
    payload["equity"] = _series(points)
    if len(points) >= 2:
        fetcher = benchmark_fetcher or fetch_benchmark_bars
        try:
            payload["benchmark"] = _series(fetcher(manager, points[0][0], points[-1][0], bench_minutes))
        except Exception as exc:
            log.info("Benchmark bars unavailable: %s", exc)
            payload["benchmark_error"] = str(exc)
    with _cache_lock:
        _cache[cache_key] = (now + CACHE_TTL_SECONDS, payload)
    return payload
