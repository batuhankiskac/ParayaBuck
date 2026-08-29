"""Pydantic payloads for Web control surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class ValidatedSettings:
    """Validated settings-save result: the new config mapping and credentials."""

    cfg_raw: dict[str, Any]
    old_app: dict[str, Any]
    api_key: str
    secret_key: str
    credentials_changed: bool


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field("", description="Alpaca API Key")
    secret_key: str = Field("", description="Alpaca Secret Key")
    paper_mode: bool = Field(True, description="Paper mode toggle")
    daily_loss_limit_pct: float = Field(1.5, gt=0, le=10)
    max_drawdown_pct: float = Field(10.0, gt=0, le=30)
    execution_timeframe: Literal["5m"] = "5m"
    host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    open_browser: bool = Field(True)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    restart_engine: bool = True
    equity_trading_enabled: bool = True
    crypto_trading_enabled: bool = True
    live_trading_enabled: bool = False
    live_dollar_cap: float = Field(0.0, ge=0)
    live_operator_approval_path: str = ""
    expected_config_digest: str | None = Field(default=None, description="Optimistic concurrency digest")


class ModePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper: bool


class LiveConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phrase: str


class WebSocketTicketPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["stream", "logs"]


class BacktestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["equity", "crypto"] = "equity"
    strategy: str = ""
    parameters: dict[str, object] = Field(default_factory=dict)
    years: int = Field(5, ge=1, le=30)
    days: int = Field(0, ge=0)
    symbols: str = ""
    max_symbols: int = Field(0, ge=0, le=2000)
