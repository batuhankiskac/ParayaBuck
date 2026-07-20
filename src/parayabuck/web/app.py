from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from threading import Thread
from time import sleep

import uvicorn
import yaml

from parayabuck.logger import setup_logging


def main() -> None:
    """ParayaBuck Modern Web Trading Terminal entry point.

    Launches Uvicorn server serving FastAPI + Glassmorphic Web UI.
    """
    config_path = Path(os.getenv("CONFIG_PATH") or "config/settings.yaml")
    app_config: dict[str, object] = {}
    if config_path.exists():
        with config_path.open() as config_file:
            app_config = (yaml.safe_load(config_file) or {}).get("app", {})

    setup_logging(level=os.getenv("LOG_LEVEL", str(app_config.get("log_level", "INFO"))))

    host = os.getenv("HOST", str(app_config.get("host", "127.0.0.1")))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            "ParayaBuck may only bind to loopback addresses because its trading "
            "control API is intentionally local-only."
        )
    port = int(os.getenv("PORT", str(app_config.get("port", 8000))))
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
