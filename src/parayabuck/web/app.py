from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import threading
import webbrowser
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from time import sleep

import uvicorn
import yaml
from uvicorn.server import HANDLED_SIGNALS

from parayabuck.logger import setup_logging
from parayabuck.trading_host import trading_host_authorized

# Bound on draining open connections (websockets stay open indefinitely)
# at shutdown. It must stay well below the unit's TimeoutStopSec (180 s) so systemd
# never SIGKILLs a process that is still persisting its journal.
_GRACEFUL_SHUTDOWN_SECONDS = 30

_log = logging.getLogger("parayabuck.web.app")


class _ExitingServer(uvicorn.Server):
    """uvicorn Server that returns after a stop signal instead of re-raising it.

    uvicorn's ``capture_signals`` re-raises the captured signal once it has put
    the previous handlers back. For SIGTERM that default action ends the process
    inside ``uvicorn.run``, so ``main`` never reached its engine-stop check and
    systemd saw a clean stop (R2-4-1). This keeps uvicorn's handler install,
    restore and graceful shutdown, and returns normally so ``main`` decides the
    exit status. It also refuses a pending engine start before uvicorn waits for
    open connections (R2-4-2).
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        # Signals can only be listened to from the main thread.
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        original_handlers = {sig: signal.signal(sig, self.handle_exit) for sig in HANDLED_SIGNALS}
        try:
            yield
        finally:
            for sig, handler in original_handlers.items():
                signal.signal(sig, handler)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        request_shutdown = getattr(getattr(self.config.app, "state", None), "engine_request_shutdown", None)
        if callable(request_shutdown):
            try:
                await asyncio.to_thread(request_shutdown)
            except Exception as exc:
                _log.warning("Startup cancellation before connection drain failed: %s", exc)
        await super().shutdown(sockets=sockets)


def _probe_web_bind(host: str, port: int) -> None:
    """Fail fast when the operator Web port cannot be bound.

    uvicorn starts the application lifespan (which auto-starts the trading
    engine) BEFORE it binds the listening socket, so a lost port race would
    otherwise launch a full engine in a process that can never serve a
    single request. Probe the exact bind uvicorn will attempt (asyncio's
    create_server sets SO_REUSEADDR on POSIX, so the probe mirrors that) and
    exit non-zero with a clear console error before any engine work begins.
    """
    last_error: OSError | None = None
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for family, socktype, proto, _canonname, sockaddr in infos:
        probe = socket.socket(family, socktype, proto)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(sockaddr)
            return
        except OSError as exc:
            last_error = exc
        finally:
            probe.close()
    raise SystemExit(
        f"ParayaBuck cannot start: Web bind on {host}:{port} failed "
        f"({last_error}). The address is already in use or not bindable, so "
        "the trading engine was NOT started. Stop the other instance or "
        "choose a different port (PORT env or app.port) and retry."
    )


def main(config_path: str | os.PathLike[str] | None = None) -> None:
    """ParayaBuck Modern Web Trading Terminal entry point.

    Launches Uvicorn server serving FastAPI + Glassmorphic Web UI.

    ``config_path`` is the ``--config`` the operator named. It is exported as
    ``CONFIG_PATH`` before the server module is imported, so the engine
    manager, alerting and metrics all resolve the very same settings file
    instead of silently falling back to the default one.
    """
    if config_path is not None:
        os.environ["CONFIG_PATH"] = str(config_path)
    settings_path = Path(os.getenv("CONFIG_PATH") or "config/settings.yaml")
    app_config: dict[str, object] = {}
    if settings_path.exists():
        with settings_path.open(encoding="utf-8") as config_file:
            app_config = (yaml.safe_load(config_file) or {}).get("app") or {}

    setup_logging(level=os.getenv("LOG_LEVEL", str(app_config.get("log_level", "INFO"))))

    host = os.getenv("HOST", str(app_config.get("host", "127.0.0.1")))
    container_bind = os.getenv("PARAYABUCK_CONTAINER") == "YES" and host == "0.0.0.0"
    # systemd sets INVOCATION_ID for every unit it starts. The container bind is for
    # the Docker image only, so a systemd-managed process (the trading host, whose
    # unit env can be overridden by an EnvironmentFile line) never listens off-loopback.
    if container_bind and (trading_host_authorized() or os.getenv("INVOCATION_ID")):
        raise ValueError(
            "The designated trading host (PARAYABUCK_TRADING_HOST) or a systemd-managed "
            "process must bind a loopback address; the container 0.0.0.0 bind is only "
            "allowed inside the container image, not under systemd."
        )
    if container_bind:
        import logging

        logging.getLogger("parayabuck.web.app").warning(
            "Container is binding 0.0.0.0:%s internally; HOST publication MUST remain 127.0.0.1:%s:8000 (loopback-only). Remote access = SSH tunnel, no public firewall for 8000.",
            os.getenv("PORT", str(app_config.get("port", 8000))),
            os.getenv("PORT", str(app_config.get("port", 8000))),
        )
    if host not in {"127.0.0.1", "localhost", "::1"} and not container_bind:
        raise ValueError(
            "ParayaBuck may only bind to loopback addresses because its trading "
            "control API is intentionally local-only."
        )
    port = int(os.getenv("PORT", str(app_config.get("port", 8000))))
    # bound must never start the engine (lifespan) just to tear it down.
    _probe_web_bind(host, port)
    url_host = f"[{host}]" if ":" in host else host
    url = f"http://{url_host}:{port}"

    print("=" * 60)
    print(" 🚀 PARAYABUCK MODERN WEB TRADING TERMINAL")
    print(f" 🌐 Dashboard URL: {url}")
    print("=" * 60)

    def open_browser() -> None:
        try:
            sleep(1.2)
            webbrowser.open_new_tab(url)
        except Exception:
            # A missing/default browser must not terminate the WebUI server.
            pass

    auto_open_browser = str(app_config.get("open_browser", True)).lower() not in {
        "0",
        "false",
        "no",
    }
    if os.getenv("OPEN_BROWSER", "1").lower() not in {"0", "false", "no"} and auto_open_browser:
        Thread(target=open_browser, daemon=True).start()

    from parayabuck.web.server import app

    server = _ExitingServer(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_SECONDS,
        )
    )
    server.run()
    if getattr(getattr(app, "state", None), "engine_stop_failed", False):
        # The engine's workers did not stop. Exit non-zero: a clean exit would
        # hide the still-running workers from systemd and the operator.
        raise SystemExit("ParayaBuck stopped with engine workers still running; restart the process before trading.")


if __name__ == "__main__":
    main()
