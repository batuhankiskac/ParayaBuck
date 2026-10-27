from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from parayabuck.core.models import Bar, Market
from parayabuck.execution.alerting import emit_execution_alert
from parayabuck.logger import get_logger

log = get_logger("parayabuck.data.alpaca_stream")

BarHandler = Callable[[Bar], Awaitable[None]]

# Reconnect backoff for alpaca-py's stream loops: 1s, 2s, 4s ... capped.
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 60.0
_BACKOFF_MAX_EXPONENT = 6
# Consecutive failed authentications before the operator is alerted.
_AUTH_ALERT_AFTER = 3
# Backoff sleeps in steps this long so a stop request is noticed promptly.
_SLEEP_STEP_SECONDS = 1.0
# A session that stays connected this long has proven itself: its drop starts
# a fresh backoff. A shorter session counts as a failure, so a server that
# accepts and then drops each connection cannot force a zero-delay loop.
_STABLE_SESSION_SECONDS = 60.0


def _backoff_seconds(failures: int) -> float:
    return float(min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * 2 ** min(failures - 1, _BACKOFF_MAX_EXPONENT)))


def guard_sdk_cast(stream: Any, fallback: Callable[[Any, Exception], Any]) -> None:
    """Make an undecodable frame non-fatal inside alpaca-py's dispatch.

    alpaca-py casts each frame before its handler runs, and a cast error
    escapes the consume loop, aborting the rest of the frame. The wrapped
    cast returns ``fallback(frame, error)`` to the handler instead.
    """
    cast = stream._cast

    def guarded_cast(frame: Any) -> Any:
        try:
            return cast(frame)
        except Exception as exc:
            return fallback(frame, exc)

    stream._cast = guarded_cast


def supervise_sdk_stream(
    stream: Any,
    *,
    label: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_reconnect: Callable[[], None] | None = None,
) -> None:
    """Wrap alpaca-py's connect step with backoff, socket cleanup and alerts.

    alpaca-py retries a failed connect or authentication after about 10 ms
    (trading) or with no delay (data) and never closes the half-open socket.
    The wrapped step closes that socket, waits an exponentially growing delay
    (interrupted by a stop request), then re-raises so the SDK loop retries.
    Repeated authentication failures raise one alert per streak.

    The SDK also re-enters this step with no delay after a dropped session.
    A session shorter than ``_STABLE_SESSION_SECONDS`` is therefore backed off
    before it is reopened, and the backoff resets only after a stable session.
    ``on_reconnect`` runs after each successful reconnect, so the owner can
    schedule a reconciliation for updates missed while the stream was down.
    """
    start = getattr(stream, "_start_ws", None)
    if start is None:
        return
    state: dict[str, Any] = {
        "failures": 0,
        "auth_failures": 0,
        "auth_failed": False,
        "session_started_at": None,
        "connected_before": False,
    }
    auth = getattr(stream, "_auth", None)
    if auth is not None:

        async def audited_auth() -> None:
            try:
                await auth()
            except Exception:
                state["auth_failed"] = True
                raise

        stream._auth = audited_auth

    async def supervised_start() -> None:
        state["auth_failed"] = False
        dropped_at = state["session_started_at"]
        state["session_started_at"] = None
        if dropped_at is not None:
            # The SDK re-enters this step straight after a dropped session.
            if clock() - dropped_at >= _STABLE_SESSION_SECONDS:
                state["failures"] = 0
            else:
                state["failures"] += 1
                delay = _backoff_seconds(state["failures"])
                log.warning("Alpaca %s stream dropped after a short session; reconnecting in %.0fs", label, delay)
                await _sleep_unless_stopped(stream, delay, sleep)
                if not getattr(stream, "_should_run", True):
                    raise ConnectionError(f"Alpaca {label} stream stopped during reconnect backoff")
        try:
            await start()
        except Exception as exc:
            state["failures"] += 1
            state["auth_failures"] = state["auth_failures"] + 1 if state["auth_failed"] else 0
            try:
                await stream.close()
            except Exception:
                log.warning("Could not close the failed %s stream socket", label, exc_info=True)
            if state["auth_failures"] == _AUTH_ALERT_AFTER:
                emit_execution_alert(
                    "paper_incident",
                    message=(
                        f"Alpaca {label} stream failed authentication {_AUTH_ALERT_AFTER} times in a row; "
                        "it keeps retrying with backoff. Check the API credentials."
                    ),
                    category="stream_auth_failed",
                    incident_id=f"stream_auth_failed:{label}",
                )
            delay = _backoff_seconds(state["failures"])
            log.warning(
                "Alpaca %s stream connect failed (attempt %d); retrying in %.0fs: %s",
                label,
                state["failures"],
                delay,
                exc,
            )
            await _sleep_unless_stopped(stream, delay, sleep)
            raise
        state["session_started_at"] = clock()
        state["auth_failures"] = 0
        if state["connected_before"] and on_reconnect is not None:
            try:
                on_reconnect()
            except Exception:
                log.warning("Reconnect hook for the Alpaca %s stream failed", label, exc_info=True)
        state["connected_before"] = True

    stream._start_ws = supervised_start


async def _sleep_unless_stopped(stream: Any, delay: float, sleep: Callable[[float], Awaitable[None]]) -> None:
    remaining = delay
    while remaining > 0 and getattr(stream, "_should_run", True):
        step = min(_SLEEP_STEP_SECONDS, remaining)
        await sleep(step)
        remaining -= step


class AlpacaBarStream:
    """Thin Alpaca equity WebSocket adapter. REST is intentionally not used for polling."""

    # Malformed bars dropped by this stream; instances count into their own copy.
    malformed_bar_count = 0
    # Set by stop(); a run() that starts after it returns without connecting.
    _stop_requested = False

    def __init__(self, api_key: str, secret_key: str, stock_stream_feed: str = "iex") -> None:
        from alpaca.data.enums import DataFeed
        from alpaca.data.live import StockDataStream

        # alpaca-py otherwise inherits websockets' 10 second close timeout.
        # EngineRuntime only waits a bounded amount of time for these worker
        # threads, so an unresponsive close handshake could leave the previous
        # connection alive while a replacement runtime starts.
        websocket_params = {
            "ping_interval": 10,
            "ping_timeout": 180,
            "max_queue": 1024,
            "close_timeout": 2,
        }
        self.stock = StockDataStream(
            api_key,
            secret_key,
            feed=DataFeed(stock_stream_feed),
            websocket_params=websocket_params,
        )
        self._install_guards()

    def _install_guards(self) -> None:
        guard_sdk_cast(self.stock, self._drop_malformed_frame)
        supervise_sdk_stream(self.stock, label="stock data")

    def _drop_malformed_frame(self, frame: Any, exc: BaseException) -> None:
        """Log and count one malformed frame; the frame's other messages still flow."""
        self.malformed_bar_count += 1
        log.error(
            "Malformed Alpaca stock stream bar dropped (%d so far): %s: %.300r",
            self.malformed_bar_count,
            exc,
            frame,
        )
        return None

    def _convert_or_drop(self, raw: Any, timeframe: str) -> Bar | None:
        if raw is None:  # the cast guard already logged and counted this frame
            return None
        try:
            return self._convert(raw, timeframe)
        except Exception as exc:
            self._drop_malformed_frame(raw, exc)
            return None

    @staticmethod
    def _convert(raw: Any, timeframe: str) -> Bar:
        return Bar(
            timestamp=raw.timestamp,
            symbol=str(raw.symbol),
            timeframe=timeframe,
            open=float(raw.open),
            high=float(raw.high),
            low=float(raw.low),
            close=float(raw.close),
            volume=float(raw.volume),
            market=Market.EQUITY,
        )

    def subscribe(
        self,
        stock_symbols: list[str],
        handler: BarHandler,
        timeframe: str = "1m",
        updated_handler: BarHandler | None = None,
    ) -> None:
        # alpaca-py dispatches every bar of a frame in one loop: an exception
        # escaping a handler would drop the frame's remaining bars.
        async def stock_handler(raw: Any) -> None:
            bar = self._convert_or_drop(raw, timeframe)
            if bar is not None:
                try:
                    await handler(bar)
                except Exception:
                    log.error("Alpaca stock bar handler failed; the frame's other bars still flow", exc_info=True)

        async def stock_updated_handler(raw: Any) -> None:
            bar = self._convert_or_drop(raw, timeframe)
            if bar is not None:
                target = updated_handler or handler
                try:
                    await target(bar)
                except Exception:
                    log.error(
                        "Alpaca stock updated-bar handler failed; the frame's other bars still flow", exc_info=True
                    )

        if stock_symbols:
            self.stock.subscribe_bars(stock_handler, *stock_symbols)
            subscribe_updated = getattr(self.stock, "subscribe_updated_bars", None)
            if subscribe_updated is not None:
                subscribe_updated(stock_updated_handler, *stock_symbols)

    def run_stock(self) -> None:
        # A stop that arrived before this run began must not be lost: the SDK
        # forgets it, and the run would then consume until process exit.
        if self._stop_requested:
            return
        self.stock.run()

    @staticmethod
    def _stop_sdk_stream(stream: Any) -> None:
        # alpaca-py creates the event loop only inside run(); its stop()
        # dereferences that loop, so a never-run stream has nothing to stop.
        if getattr(stream, "_loop", ...) is None:
            return
        stream.stop()

    def stop(self) -> None:
        self._stop_requested = True
        self._stop_sdk_stream(self.stock)
