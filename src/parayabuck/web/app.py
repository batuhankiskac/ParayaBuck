from __future__ import annotations

import os
import webbrowser
from threading import Thread
from time import sleep

import uvicorn

from parayabuck.logger import setup_logging


def main() -> None:
    """ParayaBuck Modern Web Trading Terminal entry point.

    Launches Uvicorn server serving FastAPI + Glassmorphic Web UI.
    """
    setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    url = f"http://{host}:{port}"

    print("=" * 60)
    print(" 🚀 PARAYABUCK MODERN WEB TRADING TERMINAL")
    print(f" 🌐 Dashboard URL: {url}")
    print("=" * 60)

    def open_browser():
        try:
            sleep(1.2)
            webbrowser.open_new_tab(url)
        except Exception:
            # A missing/default browser must not terminate the WebUI server.
            pass

    if os.getenv("OPEN_BROWSER", "1").lower() not in {"0", "false", "no"}:
        Thread(target=open_browser, daemon=True).start()

    from parayabuck.web.server import app

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
