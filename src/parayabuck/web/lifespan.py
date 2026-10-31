"""Application lifespan isolation — paper auto-start with retry cancellation.

Extracted from web/server.py lines 1390-1418. Preserves:
* paper auto-start; LIVE auto-starts only with a valid standing LIVE
  authorization (the manager's gate; no LIVE arm weakening),
* bounded retry cancellation on shutdown (pending sleep cancelled promptly,
  in-flight start still allowed to finish),
* stop_engine on to_thread during shutdown to release Alpaca sockets.

The factory is parameterised over a WebState / EngineManager so tests can
inject a fake manager and TestClient sees the same lifespan contract without
touching real brokers.

No circular imports: WebState / EngineManager are imported only inside
functions or via typing.TYPE_CHECKING.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from parayabuck.logger import get_logger
from parayabuck.utils import sd_notify
from parayabuck.utils.io import sweep_orphan_temp_files

if TYPE_CHECKING:
    pass

log = get_logger("parayabuck.web.lifespan")

# Longest the shutdown waits for an in-flight start attempt before it stops the
# engine anyway. The attempt has been told to cancel, so it stops before its
# order-producing steps; this only bounds how long the unit can take to exit.
_SHUTDOWN_START_WAIT_SECONDS = 30.0


def _watchdog_gate(manager: Any) -> Any:
    """Predicate for the systemd watchdog pinger (manager-less apps always pass)."""
    gate = getattr(manager, "watchdog_healthy", None) if manager is not None else None
    return gate if callable(gate) else (lambda: True)


def _sweep_settings_temp_files(manager: Any) -> None:
    """Remove temp files a settings save left after a kill mid-write. Never raises.

    Only the directories of the managed settings YAML and .env are swept, and
    only the names ``atomic_write`` (``.<name>.XXXX``) and the validate step
    (``.<name>.validate``) create for those files. Each file is handled on its
    own so a failure on one does not skip the other.
    """
    for attribute in ("config_path", "env_path"):
        managed_value = getattr(manager, attribute, None)
        if managed_value is None:
            continue
        try:
            managed = Path(managed_value)
            for removed in sweep_orphan_temp_files(managed.parent, f".{managed.name}."):
                log.info("Removed orphan settings temp file %s", removed)
        except Exception as exc:
            log.warning("Orphan settings temp sweep skipped for %s: %s", attribute, exc)


async def _auto_start_paper_engine(manager: Any) -> None:
    """Run the manager's auto_start_engine with transient error logging.

    Mirrors server.py::_auto_start_paper_engine exactly — any exception
    (including TransientStartupError retries handled internally by the
    manager) is logged and not propagated, so the app can still serve
    /healthz even if auto-start failed.
    """
    try:
        await manager.auto_start_engine()
    except Exception as exc:  # pragma: no cover - exercised via manager stubs
        redact = getattr(manager, "redact_secrets", None)
        text = redact(str(exc)) if callable(redact) else str(exc)
        log.error("Automatic paper engine start failed: %s", text)


async def _await_startup_attempt(task: asyncio.Task[None], manager: Any) -> None:
    """Wait for the auto-start task for a bounded time while retry sleeps are cancelled.

    Past the bound the task is left to finish on its own: the stop that follows
    marks the attempt interrupted, and the attempt then stops its runtime itself
    while the process is still up. An attempt still blocked in a broker call when
    the process exits is abandoned; its open orders are left to reconciliation at
    the next start.
    """
    deadline = time.monotonic() + _SHUTDOWN_START_WAIT_SECONDS
    cancel = getattr(manager, "_cancel_pending_retry", None)
    while not task.done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.error(
                "Startup attempt still running %.0fs into shutdown; stopping the engine without waiting for it",
                _SHUTDOWN_START_WAIT_SECONDS,
            )
            return
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass
        await asyncio.wait({task}, timeout=min(0.05, remaining))
    with suppress(asyncio.CancelledError):
        await task


def create_lifespan(state_provider: Any) -> Any:
    """Build an ``@asynccontextmanager`` lifespan closing over ``state_provider``.

    ``state_provider`` is a zero-arg callable returning the current
    ``WebState | EngineManager | None``. It is evaluated at lifespan entry
    so a test that mutates the global state after ``create_app`` still sees
    the correct manager. The provider also allows ``create_app`` to capture
    the concrete state without importing it at module load time.

    The returned lifespan:

    * starts a paper engine automatically when ``manager.should_auto_start()``
      is true (the manager itself gates on credentials, paper mode, etc.),
    * on shutdown cancels any pending retry sleep via
      ``manager._cancel_pending_retry()`` so shutdown is not delayed up to
      the full backoff interval,
    * awaits the auto-start task (suppressing CancelledError),
    * stops the engine on a worker thread to avoid blocking the event loop
      and to ensure Alpaca HTTP pools are closed before the process exits.

    Papers and tests that never set a manager get a no-op lifespan (still
    yields, still no history growth).
    """

    @asynccontextmanager
    async def app_lifespan(_app: Any) -> Any:
        # Resolve the manager lazily — avoids importing WebState at file load.
        resolved = state_provider() if callable(state_provider) else state_provider
        manager = None
        if resolved is not None:
            # WebState vs direct EngineManager
            manager = getattr(resolved, "engine_manager", resolved)
            # Guard against accidental None engine_manager
            if manager is None:
                manager = resolved

        auto_start_task: asyncio.Task[None] | None = None
        request_shutdown = getattr(manager, "request_shutdown", None) if manager is not None else None
        if callable(request_shutdown) and getattr(_app, "state", None) is not None:
            # Read by the web server's shutdown before uvicorn waits for open
            # connections, so a start is refused and cancelled during that wait too (R2-4-2).
            _app.state.engine_request_shutdown = request_shutdown
        # Install the opt-in e-mail sink before anything can alert (a failed
        # auto-start, a LIVE engine waiting for re-arm). Never fatal.
        configure_alerts = getattr(manager, "configure_alert_delivery", None) if manager is not None else None
        if callable(configure_alerts):
            try:
                configure_alerts()
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Alert delivery configuration failed: %s", exc)
        if manager is not None:
            # Once per web start: a settings save killed mid-write leaves a temp file
            # behind, and nothing else removes it. Best effort; never blocks startup.
            _sweep_settings_temp_files(manager)
        should_auto_start = (
            getattr(manager, "should_auto_start", lambda: False) if manager is not None else (lambda: False)
        )
        if manager is not None and should_auto_start():
            log.info("Starting configured paper engine automatically")
            auto_start_task = asyncio.create_task(_auto_start_paper_engine(manager))
        # systemd Type=notify: READY=1 is sent by the server after the socket is
        # bound (web/app.py), not here: lifespan startup runs before the bind (R3-4-5).
        # Independent of the engine: a stopped or halted engine must not
        # time the unit out. The pinger keeps WATCHDOG=1 flowing only while
        # the engine gate allows it.
        watchdog = sd_notify.WatchdogPinger(_watchdog_gate(manager))
        watchdog.start()
        try:
            yield
        finally:
            if manager is not None:
                # Refuse a start that has not begun and cancel the in-flight one
                # BEFORE waiting on it: otherwise its resume/catch-up steps can
                # submit orders during the shutdown wait. The web server already
                # made this call before uvicorn's connection drain; repeating it is harmless.
                if callable(request_shutdown):
                    try:
                        request_shutdown()
                    except Exception as exc:  # pragma: no cover - defensive
                        log.warning("Startup cancellation during shutdown failed: %s", exc)
            if auto_start_task is not None and manager is not None:
                await _await_startup_attempt(auto_start_task, manager)
            engine_stop_failed = False
            # Uvicorn shutdown must release sockets. Use to_thread so the
            # event loop is not blocked while the runtime joins workers.
            if manager is not None:
                stop = getattr(manager, "stop_engine", None)
                if callable(stop):
                    try:
                        await asyncio.to_thread(stop)
                    except Exception as exc:  # pragma: no cover
                        # Workers may still be running: the process must exit
                        # non-zero so systemd records the failure.
                        engine_stop_failed = True
                        log.warning("Engine stop during lifespan shutdown failed: %s", exc)
                if getattr(manager, "engine_state", None) == "stop_failed":
                    engine_stop_failed = True
            if engine_stop_failed and _app is not None:
                # Read by app.main() after uvicorn returns: a stop that left workers
                # running must not exit as a clean shutdown.
                with suppress(AttributeError):
                    _app.state.engine_stop_failed = True
            # Stopped last: the unit keeps its watchdog while the engine stops.
            await asyncio.to_thread(watchdog.stop)

    return app_lifespan
