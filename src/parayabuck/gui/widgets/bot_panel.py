from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLCDNumber,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from parayabuck.logger import get_logger

log = get_logger("parayabuck.gui.bot_panel")


class StatusIndicator(QWidget):
    """Tiny colored dot + label that shows engine state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #888; font-size: 20px;")
        self.label = QLabel("Hazır")
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        layout.addStretch()

    def set_state(self, state: str) -> None:
        colors = {
            "running": ("#51cf66", "Çalışıyor"),
            "stopped": ("#888", "Durdu"),
            "halted": ("#fa5252", "Durduruldu (kill-switch)"),
            "starting": ("#f59f00", "Başlatılıyor..."),
            "error": ("#fa5252", "Hata"),
        }
        color, text = colors.get(state, ("#888", state))
        self.dot.setStyleSheet(f"color: {color}; font-size: 20px;")
        self.label.setText(text)


class BotPanel(QWidget):
    """Tab page: bot state, start/stop, key parameters, runtime metrics."""

    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)

        # --- State row ---
        state_row = QHBoxLayout()
        self.status = StatusIndicator()
        state_row.addWidget(self.status)
        state_row.addStretch()

        self.start_btn = QPushButton("▶ Başlat")
        self.start_btn.clicked.connect(self.start_requested.emit)
        self.stop_btn = QPushButton("■ Durdur")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.stop_btn.setEnabled(False)
        state_row.addWidget(self.start_btn)
        state_row.addWidget(self.stop_btn)
        root.addLayout(state_row)

        # --- Quick metrics ---
        metrics_box = QGroupBox("Hesap Durumu")
        m_grid = QHBoxLayout(metrics_box)
        self.equity_lcd = self._make_lcd()
        self.cash_lcd = self._make_lcd()
        self.pnl_lcd = self._make_lcd()
        self.positions_lcd = self._make_lcd(digits=0)
        m_grid.addWidget(self._lcd_with_label(self.equity_lcd, "Equity"))
        m_grid.addWidget(self._lcd_with_label(self.cash_lcd, "Cash"))
        m_grid.addWidget(self._lcd_with_label(self.pnl_lcd, "Günlük PnL"))
        m_grid.addWidget(self._lcd_with_label(self.positions_lcd, "Pozisyon"))
        root.addWidget(metrics_box)

        # --- Poll interval ---
        poll_box = QGroupBox("Çalışma Ayarı")
        p_layout = QHBoxLayout(poll_box)
        p_layout.addWidget(QLabel("Yenileme aralığı (sn):"))
        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(5, 600)
        self.poll_spin.setValue(60)
        self.poll_spin.setSingleStep(5)
        p_layout.addWidget(self.poll_spin)
        p_layout.addStretch()
        root.addWidget(poll_box)

        # Hint: changing poll interval only applies on next start.
        hint = QLabel(
            "Not: Yenileme aralığı bir sonraki başlatmada devreye girer. "
            "Diğer tüm ayarlar için Ayarlar sekmesini kullanın."
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addStretch()

    def _make_lcd(self, digits: int = 7) -> QLCDNumber:
        lcd = QLCDNumber()
        lcd.setDigitCount(digits)
        lcd.setSegmentStyle(QLCDNumber.SegmentStyle.Flat)
        lcd.setMinimumHeight(48)
        lcd.setStyleSheet("QLCDNumber { color: #51cf66; }")
        lcd.display("—")
        return lcd

    def _lcd_with_label(self, lcd: QLCDNumber, label: str) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(lbl)
        layout.addWidget(lcd)
        return w

    def set_state(self, state: str) -> None:
        self.status.set_state(state)
        if state == "running":
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        elif state in ("stopped", "error"):
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        elif state == "starting":
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)

    def update_account(self, account: Any) -> None:
        self.equity_lcd.display(f"{account.equity:,.2f}")
        self.cash_lcd.display(f"{account.balance:,.2f}")
        self.pnl_lcd.display(f"{account.daily_pnl:+.2f}")
        active = sum(1 for p in account.positions.values() if not p.is_flat)
        self.positions_lcd.display(active)

    def get_poll_interval(self) -> int:
        return self.poll_spin.value()
