from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from parayabuck.utils import is_crypto


class PositionsTable(QWidget):
    """Live table of open positions from an :class:`Account` snapshot."""

    COLUMNS = ["Sembol", "Yön", "Miktar", "Giriş $", "Anlık $", "PnL $"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

    def update_account(self, account: Any) -> None:
        positions = [
            p for p in account.positions.values() if not p.is_flat
        ]
        self.table.setRowCount(len(positions))
        for row, pos in enumerate(sorted(positions, key=lambda p: p.symbol)):
            direction = "LONG" if pos.is_long else "SHORT"
            qty_fmt = (
                f"{abs(pos.qty):.4f}" if is_crypto(pos.symbol) else f"{abs(pos.qty):.0f}"
            )
            cells = [
                pos.symbol,
                direction,
                qty_fmt,
                f"${pos.avg_price:.2f}",
                "—",
                "—",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (0, 1):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 1:
                    item.setForeground(
                        Qt.GlobalColor.green if pos.is_long else Qt.GlobalColor.red
                    )
                self.table.setItem(row, col, item)
