from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from parayabuck.utils import is_crypto


class AccountPanel(QWidget):
    """Tab page: account balances, positions, paper/real toggle + refresh."""

    refresh_requested = Signal()
    mode_toggle_requested = Signal(bool)  # True = paper, False = real

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)

        # --- Mode toggle (paper/real) ---
        mode_box = QGroupBox("İşlem Modu")
        mb = QHBoxLayout(mode_box)
        self.paper_btn = QPushButton("📜 PAPER (Demo)")
        self.real_btn = QPushButton("💰 REAL (Gerçek Para)")
        self.paper_btn.setCheckable(True)
        self.real_btn.setCheckable(True)
        self.paper_btn.clicked.connect(lambda: self._on_mode_clicked(True))
        self.real_btn.clicked.connect(lambda: self._on_mode_clicked(False))
        mb.addWidget(self.paper_btn)
        mb.addWidget(self.real_btn)
        mb.addStretch()
        self.mode_warning = QLabel("")
        self.mode_warning.setStyleSheet("color: #fa5252; font-weight: bold;")
        self.mode_warning.setWordWrap(True)
        mb.addWidget(self.mode_warning)
        root.addWidget(mode_box)

        # --- Balances ---
        bal_box = QGroupBox("Bakiyeler")
        b_grid = QVBoxLayout(bal_box)
        self.equity_label = self._kv("Equity")
        self.cash_label = self._kv("Cash")
        self.realized_label = self._kv("Realized PnL")
        self.unrealized_label = self._kv("Unrealized PnL")
        self.daily_label = self._kv("Günlük PnL")
        self.drawdown_label = self._kv("Drawdown")
        b_grid.addWidget(self.equity_label)
        b_grid.addWidget(self.cash_label)
        b_grid.addWidget(self.realized_label)
        b_grid.addWidget(self.unrealized_label)
        b_grid.addWidget(self.daily_label)
        b_grid.addWidget(self.drawdown_label)
        b_grid.addStretch()
        root.addWidget(bal_box)

        # --- Positions table ---
        pos_box = QGroupBox("Açık Pozisyonlar")
        pb = QVBoxLayout(pos_box)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Sembol", "Yön", "Miktar", "Giriş $", "Anlık $", "PnL $"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        pb.addWidget(self.table)
        root.addWidget(pos_box, 1)

        # --- Refresh button ---
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.refresh_btn = QPushButton("↻ Yenile")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        action_row.addWidget(self.refresh_btn)
        root.addLayout(action_row)

        self._current_mode: bool | None = None

    def _kv(self, label: str) -> QLabel:
        w = QLabel(f"<b>{label}:</b> —")
        w.setTextFormat(Qt.TextFormat.RichText)
        return w

    def _on_mode_clicked(self, paper: bool) -> None:
        # Confirm before switching to real.
        if not paper and self._current_mode is not False:
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.warning(
                self,
                "REAL moda geç",
                "Gerçek para ile işlem yapacaksınız.\n\n"
                "Bu, kayıplarınızın gerçek para olacağı anlamına gelir.\n\n"
                "Devam etmek istiyor musunuz?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                # Revert selection.
                self._set_mode_buttons(self._current_mode)
                return
        self.mode_toggle_requested.emit(paper)

    def _set_mode_buttons(self, paper: bool | None) -> None:
        self.paper_btn.setChecked(paper is True)
        self.real_btn.setChecked(paper is False)
        if paper is False:
            self.mode_warning.setText("⚠️ REAL MOD — gerçek para ile işlem yapılıyor")
            self.real_btn.setStyleSheet(
                "QPushButton { background-color: #fa5252; color: white; font-weight: bold; }"
            )
            self.paper_btn.setStyleSheet("")
        else:
            self.mode_warning.setText("")
            self.paper_btn.setStyleSheet(
                "QPushButton { background-color: #51cf66; color: white; font-weight: bold; }"
            )
            self.real_btn.setStyleSheet("")

    def set_mode(self, paper: bool) -> None:
        self._current_mode = paper
        self._set_mode_buttons(paper)

    def update_account(self, account: Any) -> None:
        self.equity_label.setText(f"<b>Equity:</b> ${account.equity:,.2f}")
        self.cash_label.setText(f"<b>Cash:</b> ${account.balance:,.2f}")
        self.realized_label.setText(f"<b>Realized PnL:</b> ${account.realized_pnl:,.2f}")
        self.unrealized_label.setText(
            f"<b>Unrealized PnL:</b> ${account.unrealized_pnl:,.2f}"
        )
        self.daily_label.setText(f"<b>Günlük PnL:</b> ${account.daily_pnl:+,.2f}")
        self.drawdown_label.setText(f"<b>Drawdown:</b> ${account.drawdown:,.2f}")

        positions = [p for p in account.positions.values() if not p.is_flat]
        self.table.setRowCount(len(positions))
        for row, pos in enumerate(sorted(positions, key=lambda p: p.symbol)):
            direction = "LONG" if pos.is_long else "SHORT"
            qty_fmt = (
                f"{abs(pos.qty):.4f}" if is_crypto(pos.symbol) else f"{abs(pos.qty):.0f}"
            )
            cells = [pos.symbol, direction, qty_fmt, f"${pos.avg_price:.2f}", "—", "—"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setForeground(
                        Qt.GlobalColor.green if pos.is_long else Qt.GlobalColor.red
                    )
                self.table.setItem(row, col, item)
