from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

LOG_LEVELS: dict[int, tuple[QColor, str]] = {
    10: (QColor("#6c757d"), "DEBUG"),  # gray
    20: (QColor("#dee2e6"), "INFO"),  # light
    30: (QColor("#f59f00"), "WARNING"),  # amber
    40: (QColor("#fa5252"), "ERROR"),  # red
    50: (QColor("#fab005"), "CRITICAL"),  # bright red/yellow
}


class LogConsole(QWidget):
    """Read-only console that receives log records via a Qt signal."""

    log_signal = Signal(int, str)  # level, message

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_lines = 5000
        self._auto_scroll = True
        self._min_level = 20  # INFO

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Log seviyesi:"))
        self.level_combo = QComboBox()
        for lvl, label in [(10, "DEBUG"), (20, "INFO"), (30, "WARNING"), (40, "ERROR")]:
            self.level_combo.addItem(label, lvl)
        self.level_combo.setCurrentIndex(1)
        self.level_combo.currentIndexChanged.connect(self._on_level_changed)
        bar.addWidget(self.level_combo)

        self.autoscroll_check = QCheckBox("Otomatik kaydır")
        self.autoscroll_check.setChecked(True)
        self.autoscroll_check.toggled.connect(self._on_autoscroll_toggled)
        bar.addWidget(self.autoscroll_check)

        bar.addStretch()

        self.clear_btn = QPushButton("Temizle")
        self.clear_btn.clicked.connect(self._on_clear)
        bar.addWidget(self.clear_btn)

        layout.addLayout(bar)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.view.setStyleSheet(
            "QTextEdit { background-color: #1e1e2e; color: #cdd6f4; "
            "font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-size: 12px; }"
        )
        layout.addWidget(self.view, 1)

        # Hook the signal back into our handler. Connect via the signal so
        # threads can safely call ``console.log_signal.emit(...)``.
        self.log_signal.connect(self._on_log)

    def _on_log(self, level: int, message: str) -> None:
        if level < self._min_level:
            return

        color, _label = LOG_LEVELS.get(level, (QColor("#dee2e6"), "INFO"))

        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cursor.setCharFormat(fmt)
        cursor.insertText(message + "\n")

        # Cap the buffer so memory doesn't grow unbounded.
        doc = self.view.document()
        if doc.blockCount() > self._max_lines:
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                doc.blockCount() - self._max_lines,
            )
            cursor.removeSelectedText()

        if self._auto_scroll:
            self.view.setTextCursor(cursor)
            self.view.ensureCursorVisible()

    def _on_level_changed(self, _idx: int) -> None:
        self._min_level = self.level_combo.currentData()

    def _on_autoscroll_toggled(self, checked: bool) -> None:
        self._auto_scroll = checked

    def _on_clear(self) -> None:
        self.view.clear()
