"""Application lifespan isolation — paper auto-start with retry cancellation.

Extracted from web/server.py lines 1390-1418. Preserves:
* paper-only auto-start (no LIVE arm weakening),
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
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

from parayabuck.logger import get_logger

if TYPE_CHECKING:
    pass

log = get_logger("parayabuck.web.lifespan")


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
        log.error("Automatic paper engine start failed: %s", exc)


def create_lifespan(state_provider: Any):
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
        should_auto_start = (
            getattr(manager, "should_auto_start", lambda: False) if manager is not None else (lambda: False)
        )
        if manager is not None and should_auto_start():
            log.info("Starting configured paper engine automatically")
            auto_start_task = asyncio.create_task(_auto_start_paper_engine(manager))
        try:
            yield
        finally:
            if auto_start_task is not None and manager is not None:
                # Cancel a pending backoff sleep promptly; in-flight start
                # still finishes.
                cancel = getattr(manager, "_cancel_pending_retry", None)
                if callable(cancel):
                    try:
                        cancel()
                    except Exception:
                        pass
                with suppress(asyncio.CancelledError):
                    await auto_start_task
            # Uvicorn shutdown must release sockets. Use to_thread so the
            # event loop is not blocked while the runtime joins workers.
            if manager is not None:
                stop = getattr(manager, "stop_engine", None)
                if callable(stop):
                    try:
                        await asyncio.to_thread(stop)
                    except Exception as exc:  # pragma: no cover
                        log.warning("Engine stop during lifespan shutdown failed: %s", exc)

    return app_lifespan
