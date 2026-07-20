from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from parayabuck.logger import get_logger

log = get_logger("parayabuck.gui.settings_panel")

KNOWN_STRATEGIES = [
    "ema_crossover",
    "mean_reversion",
    "pairs_trading",
    "multi_factor",
    "vwap_reversion",
    "donchian_breakout",
    "orb",
    "gap_fade",
    "dual_momentum",
    "sector_rotation",
]


class EnvSection(QGroupBox):
    """Form for editing .env (Alpaca credentials + log level)."""

    credentials_changed = Signal()

    def __init__(self, env_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(".env — Alpaca Kimlik Bilgileri", parent)
        self._env_path = env_path

        form = QFormLayout(self)

        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("ALPACA_API_KEY")
        form.addRow("API Key:", self.key_edit)

        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_edit.setPlaceholderText("ALPACA_SECRET_KEY")
        form.addRow("Secret Key:", self.secret_edit)

        show_btn = QPushButton("Göster")
        show_btn.setCheckable(True)
        show_btn.toggled.connect(self._toggle_secret_visibility)
        form.addRow("", show_btn)

        self.log_level_combo = QComboBox()
        for lvl in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            self.log_level_combo.addItem(lvl)
        form.addRow("Log Level:", self.log_level_combo)

        self.load()

    def _toggle_secret_visibility(self, show: bool) -> None:
        self.secret_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password
        )

    def load(self) -> None:
        # Read .env as plain text (no overrides of process env).
        values: dict[str, str] = {}
        if self._env_path.exists():
            for line in self._env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
        self.key_edit.setText(values.get("ALPACA_API_KEY", ""))
        self.secret_edit.setText(values.get("ALPACA_SECRET_KEY", ""))
        self.log_level_combo.setCurrentText(values.get("LOG_LEVEL", "INFO"))

    def save(self) -> None:
        # Preserve any other keys present in .env (e.g. CONFIG_PATH).
        existing: dict[str, str] = {}
        if self._env_path.exists():
            for line in self._env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()

        existing["ALPACA_API_KEY"] = self.key_edit.text().strip()
        existing["ALPACA_SECRET_KEY"] = self.secret_edit.text().strip()
        existing["LOG_LEVEL"] = self.log_level_combo.currentText()

        lines = [
            "# Alpaca API credentials — managed by ParayaBuck GUI",
            f"ALPACA_API_KEY={existing['ALPACA_API_KEY']}",
            f"ALPACA_SECRET_KEY={existing['ALPACA_SECRET_KEY']}",
            f"ALPACA_ENDPOINT={'https://paper-api.alpaca.markets/v2'}",
            "",
            "# General",
            f"LOG_LEVEL={existing['LOG_LEVEL']}",
            f"DATA_DIR={existing.get('DATA_DIR', './data')}",
            f"CONFIG_PATH={existing.get('CONFIG_PATH', './config/settings.yaml')}",
            "",
        ]
        # Append any other keys we didn't categorize.
        for k, v in existing.items():
            if k not in {
                "ALPACA_API_KEY",
                "ALPACA_SECRET_KEY",
                "ALPACA_ENDPOINT",
                "LOG_LEVEL",
                "DATA_DIR",
                "CONFIG_PATH",
            }:
                lines.append(f"{k}={v}")

        self._env_path.write_text("\n".join(lines) + "\n")
        # Push values into os.environ so the running engine picks them up on
        # next restart.
        for k, v in existing.items():
            os.environ[k] = v
        self.credentials_changed.emit()

    def has_credentials(self) -> bool:
        return bool(self.key_edit.text().strip() and self.secret_edit.text().strip())


class SettingsPanel(QWidget):
    """Tab page: edit .env and settings.yaml without leaving the app."""

    settings_saved = Signal()  # emitted when settings.yaml is written
    credentials_saved = Signal()  # emitted when .env is written

    def __init__(
        self,
        config_path: Path,
        env_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self._env_path = env_path
        self._raw: dict[str, Any] = {}

        root = QVBoxLayout(self)

        # --- .env section ---
        self.env_section = EnvSection(env_path)
        self.env_section.credentials_changed.connect(self.credentials_saved.emit)
        root.addWidget(self.env_section)

        # --- settings.yaml: account + risk ---
        acct_box = QGroupBox("Hesap")
        acct_form = QFormLayout(acct_box)
        self.starting_balance = QDoubleSpinBox()
        self.starting_balance.setRange(0, 10_000_000)
        self.starting_balance.setDecimals(2)
        self.starting_balance.setSingleStep(100)
        acct_form.addRow("Başlangıç bakiyesi ($):", self.starting_balance)
        self.currency = QComboBox()
        self.currency.addItem("USD")
        acct_form.addRow("Para birimi:", self.currency)
        root.addWidget(acct_box)

        risk_box = QGroupBox("Risk")
        risk_form = QFormLayout(risk_box)
        self.max_position_size = QSpinBox()
        self.max_position_size.setRange(1, 100_000)
        risk_form.addRow("Maks. pozisyon (lot):", self.max_position_size)

        self.max_daily_loss = QDoubleSpinBox()
        self.max_daily_loss.setRange(0, 1_000_000)
        self.max_daily_loss.setDecimals(2)
        self.max_daily_loss.setSingleStep(10)
        risk_form.addRow("Günlük maks. kayıp ($):", self.max_daily_loss)

        self.max_drawdown = QDoubleSpinBox()
        self.max_drawdown.setRange(0, 1_000_000)
        self.max_drawdown.setDecimals(2)
        self.max_drawdown.setSingleStep(50)
        risk_form.addRow("Maks. drawdown ($):", self.max_drawdown)

        self.risk_per_trade = QDoubleSpinBox()
        self.risk_per_trade.setRange(0, 1_000_000)
        self.risk_per_trade.setDecimals(2)
        self.risk_per_trade.setSingleStep(10)
        risk_form.addRow("Trade başına risk ($):", self.risk_per_trade)

        self.commission_per_share = QDoubleSpinBox()
        self.commission_per_share.setRange(0, 10)
        self.commission_per_share.setDecimals(4)
        self.commission_per_share.setSingleStep(0.001)
        risk_form.addRow("Komisyon/pay ($):", self.commission_per_share)

        self.slippage_cents = QDoubleSpinBox()
        self.slippage_cents.setRange(0, 10)
        self.slippage_cents.setDecimals(4)
        self.slippage_cents.setSingleStep(0.01)
        risk_form.addRow("Slippage ($):", self.slippage_cents)
        root.addWidget(risk_box)

        # --- portfolio ---
        port_box = QGroupBox("Portföy Riski")
        port_form = QFormLayout(port_box)
        self.max_pct_per_position = QDoubleSpinBox()
        self.max_pct_per_position.setRange(0, 1)
        self.max_pct_per_position.setDecimals(3)
        self.max_pct_per_position.setSingleStep(0.01)
        port_form.addRow("Pozisyon başına maks. %:", self.max_pct_per_position)

        self.max_pct_per_sector = QDoubleSpinBox()
        self.max_pct_per_sector.setRange(0, 1)
        self.max_pct_per_sector.setDecimals(3)
        self.max_pct_per_sector.setSingleStep(0.01)
        port_form.addRow("Sektör başına maks. %:", self.max_pct_per_sector)

        self.max_positions = QSpinBox()
        self.max_positions.setRange(1, 100)
        port_form.addRow("Maks. eşzamanlı pozisyon:", self.max_positions)

        self.min_cash_pct = QDoubleSpinBox()
        self.min_cash_pct.setRange(0, 1)
        self.min_cash_pct.setDecimals(3)
        self.min_cash_pct.setSingleStep(0.01)
        port_form.addRow("Min. nakit %:", self.min_cash_pct)
        root.addWidget(port_box)

        # --- strategy ---
        strat_box = QGroupBox("Strateji")
        strat_form = QFormLayout(strat_box)
        self.strategy_name = QComboBox()
        for s in KNOWN_STRATEGIES:
            self.strategy_name.addItem(s)
        strat_form.addRow("Varsayılan strateji:", self.strategy_name)

        self.strategy_params = QTableWidget(0, 2)
        self.strategy_params.setHorizontalHeaderLabels(["Parametre", "Değer"])
        self.strategy_params.verticalHeader().setVisible(False)
        self.strategy_params.setAlternatingRowColors(True)
        strat_form.addRow("Parametreler:", self.strategy_params)
        root.addWidget(strat_box)

        # --- backtest defaults ---
        bt_box = QGroupBox("Backtest Varsayılanları")
        bt_form = QFormLayout(bt_box)
        self.bt_symbol = QLineEdit()
        bt_form.addRow("Sembol:", self.bt_symbol)
        self.bt_timeframe = QComboBox()
        for tf in ["1m", "5m", "15m", "30m", "1h", "1d"]:
            self.bt_timeframe.addItem(tf)
        bt_form.addRow("Timeframe:", self.bt_timeframe)
        self.bt_start = QLineEdit()
        bt_form.addRow("Başlangıç (YYYY-MM-DD):", self.bt_start)
        self.bt_end = QLineEdit()
        bt_form.addRow("Bitiş (YYYY-MM-DD):", self.bt_end)
        root.addWidget(bt_box)

        # --- alpaca ---
        alp_box = QGroupBox("Alpaca Bağlantısı")
        alp_form = QFormLayout(alp_box)
        self.alpaca_paper = QCheckBox("Paper hesap (demo)")
        alp_form.addRow("", self.alpaca_paper)
        self.alpaca_data_delayed = QCheckBox("Gecikmeli veri")
        alp_form.addRow("", self.alpaca_data_delayed)
        root.addWidget(alp_box)

        # --- symbols (checkbox list) ---
        sym_box = QGroupBox("Semboller")
        sym_layout = QVBoxLayout(sym_box)
        hint = QLabel(
            "İşlem yapılacak semboller. Tıklayarak aktif/pasif yapın. "
            "Pasiflenenler settings.yaml'den silinmez, sadece bot'a aktarılmaz."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        sym_layout.addWidget(hint)
        self.symbols_list = QListWidget()
        sym_layout.addWidget(self.symbols_list)
        root.addWidget(sym_box)

        # --- action row ---
        action_row = QHBoxLayout()
        action_row.addStretch()
        reload_btn = QPushButton("↻ Yeniden Yükle")
        reload_btn.clicked.connect(self.load)
        action_row.addWidget(reload_btn)
        save_btn = QPushButton("💾 Kaydet")
        save_btn.clicked.connect(self.save)
        action_row.addWidget(save_btn)
        raw_btn = QPushButton("raw YAML")
        raw_btn.clicked.connect(self._show_raw)
        action_row.addWidget(raw_btn)
        root.addLayout(action_row)

        self.load()

    def load(self) -> None:
        """Read settings.yaml + .env and populate the form."""
        load_dotenv(self._env_path, override=True)
        if not self._config_path.exists():
            return
        with open(self._config_path) as f:
            self._raw = yaml.safe_load(f) or {}

        acct = self._raw.get("account", {})
        self.starting_balance.setValue(float(acct.get("starting_balance", 10_000)))
        self.currency.setCurrentText(acct.get("currency", "USD"))

        risk = self._raw.get("risk", {})
        self.max_position_size.setValue(int(risk.get("max_position_size", 100)))
        self.max_daily_loss.setValue(float(risk.get("max_daily_loss", 300)))
        self.max_drawdown.setValue(float(risk.get("max_drawdown", 1500)))
        self.risk_per_trade.setValue(float(risk.get("risk_per_trade", 150)))
        self.commission_per_share.setValue(float(risk.get("commission_per_share", 0)))
        self.slippage_cents.setValue(float(risk.get("slippage_cents", 0.01)))

        port = self._raw.get("portfolio", {})
        self.max_pct_per_position.setValue(float(port.get("max_pct_per_position", 0.10)))
        self.max_pct_per_sector.setValue(float(port.get("max_pct_per_sector", 0.30)))
        self.max_positions.setValue(int(port.get("max_positions", 15)))
        self.min_cash_pct.setValue(float(port.get("min_cash_pct", 0.05)))

        strat = self._raw.get("strategy", {})
        sname = strat.get("name", "mean_reversion")
        idx = self.strategy_name.findText(sname)
        if idx >= 0:
            self.strategy_name.setCurrentIndex(idx)
        self._set_params_table(strat.get("params", {}))

        bt = self._raw.get("backtest", {})
        self.bt_symbol.setText(str(bt.get("symbol", "SPY")))
        tf_idx = self.bt_timeframe.findText(str(bt.get("timeframe", "15m")))
        if tf_idx >= 0:
            self.bt_timeframe.setCurrentIndex(tf_idx)
        self.bt_start.setText(str(bt.get("start", "2025-01-01")))
        self.bt_end.setText(str(bt.get("end", "2025-06-30")))

        alp = self._raw.get("alpaca", {})
        self.alpaca_paper.setChecked(bool(alp.get("paper", True)))
        self.alpaca_data_delayed.setChecked(bool(alp.get("data_delayed", False)))

        # Symbols: pre-existing list with check state.
        symbols = self._raw.get("symbols", {}) or {}
        self.symbols_list.clear()
        for name in symbols.keys():
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.symbols_list.addItem(item)

        # Also show symbols the user might have toggled off in the past — we
        # keep them in the file but mark them unchecked. Since we don't store
        # "off" symbols explicitly, this is informational only.

    def _set_params_table(self, params: dict[str, Any]) -> None:
        self.strategy_params.setRowCount(0)
        for k, v in params.items():
            row = self.strategy_params.rowCount()
            self.strategy_params.insertRow(row)
            k_item = QTableWidgetItem(str(k))
            k_item.setFlags(k_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            v_item = QTableWidgetItem(str(v))
            self.strategy_params.setItem(row, 0, k_item)
            self.strategy_params.setItem(row, 1, v_item)
        # Add one empty row for new entries.
        row = self.strategy_params.rowCount()
        self.strategy_params.insertRow(row)
        self.strategy_params.setItem(row, 0, QTableWidgetItem(""))
        self.strategy_params.setItem(row, 1, QTableWidgetItem(""))

    def _read_params_table(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for row in range(self.strategy_params.rowCount()):
            k_item = self.strategy_params.item(row, 0)
            v_item = self.strategy_params.item(row, 1)
            if k_item is None or v_item is None:
                continue
            key = k_item.text().strip()
            val = v_item.text().strip()
            if not key:
                continue
            out[key] = self._coerce(val)
        return out

    @staticmethod
    def _coerce(text: str) -> Any:
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text

    def save(self) -> None:
        # Update the raw dict in-place so we preserve unknown top-level keys
        # (e.g. strategy_allocations).
        self._raw.setdefault("account", {})
        self._raw["account"]["starting_balance"] = self.starting_balance.value()
        self._raw["account"]["currency"] = self.currency.currentText()

        self._raw.setdefault("risk", {})
        self._raw["risk"]["max_position_size"] = self.max_position_size.value()
        self._raw["risk"]["max_daily_loss"] = self.max_daily_loss.value()
        self._raw["risk"]["max_drawdown"] = self.max_drawdown.value()
        self._raw["risk"]["risk_per_trade"] = self.risk_per_trade.value()
        self._raw["risk"]["commission_per_share"] = self.commission_per_share.value()
        self._raw["risk"]["slippage_cents"] = self.slippage_cents.value()

        self._raw.setdefault("portfolio", {})
        self._raw["portfolio"]["max_pct_per_position"] = self.max_pct_per_position.value()
        self._raw["portfolio"]["max_pct_per_sector"] = self.max_pct_per_sector.value()
        self._raw["portfolio"]["max_positions"] = self.max_positions.value()
        self._raw["portfolio"]["min_cash_pct"] = self.min_cash_pct.value()

        self._raw.setdefault("strategy", {})
        self._raw["strategy"]["name"] = self.strategy_name.currentText()
        self._raw["strategy"]["params"] = self._read_params_table()

        self._raw.setdefault("backtest", {})
        self._raw["backtest"]["symbol"] = self.bt_symbol.text().strip() or "SPY"
        self._raw["backtest"]["timeframe"] = self.bt_timeframe.currentText()
        self._raw["backtest"]["start"] = self.bt_start.text().strip()
        self._raw["backtest"]["end"] = self.bt_end.text().strip()

        self._raw.setdefault("alpaca", {})
        self._raw["alpaca"]["paper"] = self.alpaca_paper.isChecked()
        self._raw["alpaca"]["data_delayed"] = self.alpaca_data_delayed.isChecked()

        # Symbols: keep existing specs (tick_size etc.) for checked items.
        existing_symbols = self._raw.get("symbols", {}) or {}
        active_symbols: dict[str, Any] = {}
        for i in range(self.symbols_list.count()):
            item = self.symbols_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                name = item.text()
                if name in existing_symbols:
                    active_symbols[name] = existing_symbols[name]
                else:
                    active_symbols[name] = {"tick_size": 0.01, "point_value": 1.0}
        self._raw["symbols"] = active_symbols

        # Write .env first (so credentials are available when we re-read).
        self.env_section.save()
        # Write settings.yaml.
        with open(self._config_path, "w") as f:
            yaml.safe_dump(self._raw, f, sort_keys=False, default_flow_style=False)
        log.info(f"Settings saved → {self._config_path}")
        self.settings_saved.emit()

    def _show_raw(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("settings.yaml (raw)")
        dlg.resize(700, 500)
        layout = QVBoxLayout(dlg)
        edit = QTextEdit()
        edit.setPlainText(
            yaml.safe_dump(self._raw, sort_keys=False, default_flow_style=False)
            if self._raw
            else self._config_path.read_text()
        )
        layout.addWidget(edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self._raw = yaml.safe_load(edit.toPlainText()) or {}
                with open(self._config_path, "w") as f:
                    yaml.safe_dump(self._raw, f, sort_keys=False, default_flow_style=False)
                self.load()
                self.settings_saved.emit()
            except yaml.YAMLError as e:
                QMessageBox.critical(self, "YAML hatası", str(e))

    def get_active_symbols(self) -> list[str]:
        active: list[str] = []
        for i in range(self.symbols_list.count()):
            item = self.symbols_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                active.append(item.text())
        return active

    def is_paper_mode(self) -> bool:
        return self.alpaca_paper.isChecked()

    def has_credentials(self) -> bool:
        return self.env_section.has_credentials()
