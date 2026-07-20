from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from parayabuck.config import load_config
from parayabuck.gui.widgets.account_panel import AccountPanel
from parayabuck.gui.widgets.bot_panel import BotPanel
from parayabuck.gui.widgets.log_console import LogConsole
from parayabuck.gui.widgets.positions_table import PositionsTable
from parayabuck.gui.widgets.settings_panel import SettingsPanel
from parayabuck.gui.workers.account_worker import AccountWorker
from parayabuck.gui.workers.trading_worker import TradingWorker
from parayabuck.logger import attach_qt_signal, get_logger

log = get_logger("parayabuck.gui.main_window")


class MainWindow(QWidget):
    """Top-level window: tabbed UI (Bot / Account / Settings) + log dock.

    On launch it tries to auto-start the engine. If credentials or config are
    missing, it shows a banner and switches to the Settings tab instead.
    """

    def __init__(self, config_path: Path, env_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ParayaBuck — Trading Bot")
        self.resize(1100, 720)

        self._config_path = config_path
        self._env_path = env_path

        self._trading_worker: TradingWorker | None = None
        self._account_worker: AccountWorker | None = None
        self._broker: Any = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        self.tabs = QTabWidget()
        self.bot_panel = BotPanel()
        self.account_panel = AccountPanel()
        self.settings_panel = SettingsPanel(config_path, env_path)
        self.positions_table = PositionsTable()
        self.log_console = LogConsole()

        # Bot tab: left = controls, right = positions + log
        bot_tab = QWidget()
        bot_layout = QVBoxLayout(bot_tab)
        bot_layout.setContentsMargins(0, 0, 0, 0)
        top_row = QWidget()
        top_layout = QVBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self.bot_panel)
        top_layout.addWidget(self.positions_table)
        bot_layout.addWidget(top_row, 1)
        bot_layout.addWidget(self.log_console, 1)
        self.tabs.addTab(bot_tab, "Bot")

        self.tabs.addTab(self.account_panel, "Hesap")
        self.tabs.addTab(self.settings_panel, "Ayarlar")
        root.addWidget(self.tabs, 1)

        self.status_bar = QStatusBar()
        root.addWidget(self.status_bar)
        self.status_bar.showMessage("Hazır")

        # --- signal wiring ---
        attach_qt_signal(self.log_console.log_signal)
        self.bot_panel.start_requested.connect(self.start_engine)
        self.bot_panel.stop_requested.connect(self.stop_engine)
        self.account_panel.refresh_requested.connect(self._on_account_refresh)
        self.account_panel.mode_toggle_requested.connect(self._on_mode_toggle)
        self.settings_panel.settings_saved.connect(self._on_settings_saved)
        self.settings_panel.credentials_saved.connect(self._on_credentials_saved)

        # Initial UI state.
        self.bot_panel.set_state("stopped")
        self.account_panel.set_mode(self.settings_panel.is_paper_mode())

        # Auto-start shortly after the window is shown so the event loop has
        # a chance to paint first. Use a single-shot timer.
        QTimer.singleShot(300, self._auto_start)

    # ------------------------------------------------------------------
    # Auto-start logic
    # ------------------------------------------------------------------
    def _auto_start(self) -> None:
        if not self.settings_panel.has_credentials():
            self.status_bar.showMessage("Alpaca kimlik yok — Ayarlar sekmesini doldurun.")
            self.tabs.setCurrentIndex(2)
            QMessageBox.information(
                self,
                "Hoş geldiniz",
                "Alpaca API anahtarınız yok. Ayarlar sekmesinden girip "
                "kaydettikten sonra bot otomatik başlayacak.",
            )
            return
        self.start_engine()

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------
    def start_engine(self) -> None:
        if self._trading_worker is not None and self._trading_worker.isRunning():
            return
        try:
            # Reload .env + settings so manual edits take effect.
            from dotenv import load_dotenv

            load_dotenv(self._env_path, override=True)
            cfg = load_config(str(self._config_path))

            key = os.getenv("ALPACA_API_KEY")
            secret = os.getenv("ALPACA_SECRET_KEY")
            if not key or not secret:
                QMessageBox.critical(
                    self,
                    "Kimlik yok",
                    "Alpaca API anahtarı/secret yok. Ayarlar sekmesinden girin.",
                )
                self.tabs.setCurrentIndex(2)
                return

            from parayabuck.data.alpaca import AlpacaDataSource
            from parayabuck.execution.alpaca import AlpacaBroker
            from parayabuck.execution.live_engine import LiveTradingEngine

            paper = cfg.alpaca.paper
            self.account_panel.set_mode(paper)

            broker = AlpacaBroker(key, secret, paper=paper)
            data_source = AlpacaDataSource(key, secret)
            symbols = list(cfg.symbols.keys())

            engine = LiveTradingEngine(
                broker=broker,
                data_source=data_source,
                symbols=symbols,
                timeframe=self._read_timeframe(cfg),
                poll_interval=self.bot_panel.get_poll_interval(),
                max_positions=cfg.portfolio.max_positions if cfg.portfolio else 5,
                risk_pct=0.02,
                stop_loss_pct=0.04,
                take_profit_pct=0.06,
                trail_pct=0.03,
                max_daily_loss=cfg.risk.max_daily_loss,
                max_drawdown=cfg.risk.max_drawdown,
                strategy_allocations=cfg.strategy_allocations,
            )
            self._broker = broker

            self._trading_worker = TradingWorker(engine, self)
            self._trading_worker.started_state.connect(
                lambda: self.bot_panel.set_state("running")
            )
            self._trading_worker.stopped_state.connect(
                lambda: self.bot_panel.set_state("stopped")
            )
            self._trading_worker.error.connect(self._on_engine_error)

            self._account_worker = AccountWorker(
                broker, interval_seconds=10, parent=self
            )
            self._account_worker.account_snapshot.connect(self._on_account_snapshot)
            self._account_worker.account_snapshot.connect(
                self.positions_table.update_account
            )
            self._account_worker.account_snapshot.connect(
                self.bot_panel.update_account
            )
            self._account_worker.account_snapshot.connect(
                self.account_panel.update_account
            )
            self._account_worker.error.connect(
                lambda e: self.status_bar.showMessage(f"Hesap hatası: {e}")
            )

            self._account_worker.start()
            self._trading_worker.start()
            self.status_bar.showMessage(
                f"Motor başlatıldı — {'PAPER' if paper else 'REAL'} mod, "
                f"{len(symbols)} sembol"
            )
        except Exception as e:
            log.exception("Failed to start engine")
            self.bot_panel.set_state("error")
            QMessageBox.critical(self, "Başlatma hatası", str(e))

    def stop_engine(self) -> None:
        if self._trading_worker is not None and self._trading_worker.isRunning():
            self.status_bar.showMessage("Motor durduruluyor...")
            self._trading_worker.stop()
            self._trading_worker.requestInterruption()
            self._trading_worker.wait(3000)
        if self._account_worker is not None and self._account_worker.isRunning():
            self._account_worker.stop()
            self._account_worker.wait(3000)
        self.status_bar.showMessage("Motor durdu.")
        self.bot_panel.set_state("stopped")

    def _on_engine_error(self, msg: str) -> None:
        self.bot_panel.set_state("error")
        self.status_bar.showMessage(f"Motor hatası: {msg}")
        QMessageBox.critical(self, "Motor hatası", msg)

    # ------------------------------------------------------------------
    # Account / settings slots
    # ------------------------------------------------------------------
    def _on_account_snapshot(self, account: Any) -> None:
        # Status bar shows equity + daily PnL for at-a-glance monitoring.
        self.status_bar.showMessage(
            f"Equity ${account.equity:,.2f}  |  "
            f"Günlük PnL ${account.daily_pnl:+,.2f}  |  "
            f"Pozisyon {len(account.positions)}"
        )

    def _on_account_refresh(self) -> None:
        if self._broker is None:
            return
        try:
            acct = self._broker.get_account()
            self.account_panel.update_account(acct)
            self.positions_table.update_account(acct)
            self.bot_panel.update_account(acct)
        except Exception as e:
            self.status_bar.showMessage(f"Yenileme hatası: {e}")

    def _on_mode_toggle(self, paper: bool) -> None:
        if self._trading_worker is not None and self._trading_worker.isRunning():
            QMessageBox.warning(
                self,
                "Mod değiştirilemiyor",
                "Önce botu durdurun, sonra paper/real arasında geçiş yapın.",
            )
            # Revert the UI toggle.
            self.account_panel.set_mode(not paper)
            return
        # Write the new mode to settings.yaml.
        try:
            import yaml

            raw: dict[str, Any] = {}
            if self._config_path.exists():
                with open(self._config_path) as f:
                    raw = yaml.safe_load(f) or {}
            raw.setdefault("alpaca", {})["paper"] = paper
            with open(self._config_path, "w") as f:
                yaml.safe_dump(raw, f, sort_keys=False, default_flow_style=False)
            self.account_panel.set_mode(paper)
            self.status_bar.showMessage(
                f"Mod ayarlandı: {'PAPER' if paper else 'REAL'} — kaydedildi."
            )
            # Refresh the settings panel so the checkbox reflects the change.
            self.settings_panel.load()
        except Exception as e:
            QMessageBox.critical(self, "Mod hatası", str(e))

    def _on_settings_saved(self) -> None:
        self.status_bar.showMessage("Ayarlar kaydedildi.")
        # If the engine is running, ask whether to restart.
        if self._trading_worker is not None and self._trading_worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Yeniden başlat",
                "Ayarlar değişti. Botu yeni ayarlarla yeniden başlatmak ister misiniz?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.stop_engine()
                QTimer.singleShot(500, self.start_engine)

    def _on_credentials_saved(self) -> None:
        self.status_bar.showMessage("Kimlik bilgileri kaydedildi (.env).")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _read_timeframe(self, cfg: Any) -> str:
        # Live engine uses its own default (15m); we expose the backtest
        # setting as a hint when reasonable.
        return "15m"

    # ------------------------------------------------------------------
    # Close handling — stop threads before quitting.
    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:
        if (
            self._trading_worker is not None
            and self._trading_worker.isRunning()
        ):
            answer = QMessageBox.question(
                self,
                "Çıkış",
                "Bot çalışıyor. Açık pozisyonlar Alpaca'da kalacak.\n\nÇıkmak istiyor musunuz?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.stop_engine()
        event.accept()


def _apply_dark_palette(app: QApplication) -> None:
    """A modest dark theme so the bot UI looks like a trading app."""
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e2e"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#181825"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1e1e2e"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#cdd6f4"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#cdd6f4"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#313244"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#cdd6f4"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#89b4fa"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#11111b"))
    app.setPalette(palette)
    app.setStyleSheet(
        "QToolTip { color: #cdd6f4; background-color: #313244; border: 1px solid #45475a; }"
        " QGroupBox { border: 1px solid #45475a; border-radius: 4px; "
        "             margin-top: 12px; padding-top: 8px; color: #bac2de; }"
        " QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        " QTableWidget { gridline-color: #45475a; }"
        " QHeaderView::section { background-color: #313244; color: #cdd6f4; "
        "                         padding: 4px; border: 0; }"
        " QStatusBar { background-color: #181825; color: #cdd6f4; }"
    )
