from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from parayabuck.logger import setup_logging


def main() -> None:
    """ParayaBuck GUI entry point.

    Replaces the previous Click CLI. Launches the Qt application with the
    main window, which auto-starts the trading engine if credentials are
    found.
    """
    setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))

    # Resolve config/env paths. Allow override via CONFIG_PATH env for tests.
    config_path = Path(os.getenv("CONFIG_PATH") or "config/settings.yaml")
    env_path = Path(".env")

    # If the user has no config, point them at the example so the GUI shows
    # something useful instead of crashing on load.
    if not config_path.exists():
        example = Path("config/settings.yaml")
        if example.exists():
            config_path = example
    if not env_path.exists():
        example_env = Path(".env.example")
        if example_env.exists():
            env_path = example_env

    app = QApplication(sys.argv)
    app.setApplicationName("ParayaBuck")
    app.setQuitOnLastWindowClosed(True)

    # Apply dark theme (imported lazily so a missing PySide6 doesn't break
    # imports for headless environments).
    from parayabuck.gui.main_window import MainWindow, _apply_dark_palette

    _apply_dark_palette(app)

    window = MainWindow(config_path=config_path, env_path=env_path)
    window.show()

    # Make sure Ctrl+C in the terminal can kill the app.
    import signal

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
