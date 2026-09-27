from __future__ import annotations

import os
import socket
import webbrowser
from pathlib import Path
from threading import Thread
from time import sleep

import uvicorn
import yaml

from parayabuck.logger import setup_logging


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


def main() -> None:
    """ParayaBuck Modern Web Trading Terminal entry point.

    Launches Uvicorn server serving FastAPI + Glassmorphic Web UI.
    """
    config_path = Path(os.getenv("CONFIG_PATH") or "config/settings.yaml")
    app_config: dict[str, object] = {}
    if config_path.exists():
        with config_path.open(encoding="utf-8") as config_file:
            app_config = (yaml.safe_load(config_file) or {}).get("app") or {}

    setup_logging(level=os.getenv("LOG_LEVEL", str(app_config.get("log_level", "INFO"))))

    host = os.getenv("HOST", str(app_config.get("host", "127.0.0.1")))
    container_bind = os.getenv("PARAYABUCK_CONTAINER") == "YES" and host == "0.0.0.0"
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

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
