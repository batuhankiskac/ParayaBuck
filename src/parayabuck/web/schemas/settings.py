"""Pydantic payloads for Web control surface."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


@dataclass(frozen=True)
class ValidatedSettings:
    """Validated settings-save result: the new config mapping and credentials."""

    cfg_raw: dict[str, Any]
    old_app: dict[str, Any]
    api_key: str
    secret_key: str
    credentials_changed: bool


_RISK_PERCENT_CAPS = {"daily_loss_limit_pct": 50.0, "max_drawdown_pct": 90.0}


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field("", max_length=256, description="Alpaca API Key")
    secret_key: str = Field("", max_length=256, description="Alpaca Secret Key")
    paper_mode: bool = Field(True, description="Paper mode toggle")
    # Percent limits. No made-up default: an omitted limit is left unchanged
    # (PATCH semantics), and an explicit null is refused by the validator below.
    # Caps match the config.py bounds (0.5 and 0.9 as fractions).
    daily_loss_limit_pct: float | None = Field(None, description="Daily loss limit, percent (0 < x <= 50)")
    max_drawdown_pct: float | None = Field(None, description="Drawdown halt, percent (0 < x <= 90)")
    host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    open_browser: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Off by default: a save restarts the engine only when the caller asks for it.
    restart_engine: bool = False
    equity_trading_enabled: bool = True
    live_trading_enabled: bool = False
    # Upper bound is a schema sanity limit, not a trading policy; the shipped default stays 0.
    live_dollar_cap: float = Field(0.0, ge=0, le=1_000_000, allow_inf_nan=False)
    live_operator_approval_path: str = Field("", max_length=4096)
    expected_config_digest: str | None = Field(default=None, max_length=128, description="Optimistic concurrency digest")

    @field_validator("daily_loss_limit_pct", "max_drawdown_pct")
    @classmethod
    def _risk_percent_when_sent(cls, value: float | None, info: ValidationInfo) -> float:
        """A sent limit must be a finite percent in (0, cap]; null is refused."""
        cap = _RISK_PERCENT_CAPS[str(info.field_name)]
        if value is None or not math.isfinite(value) or not 0 < value <= cap:
            raise ValueError(f"must be a finite percent greater than 0 and at most {cap:g}")
        return value


class ModePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper: bool


class LiveConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phrase: str = Field(..., max_length=256)


class WebSocketTicketPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["stream", "logs"]


class BacktestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["equity"] = "equity"
    strategy: str = Field("", max_length=128)
    parameters: dict[str, object] = Field(default_factory=dict, max_length=64)
    years: int = Field(5, ge=1, le=30)
    days: int = Field(0, ge=0, le=366 * 30)
    symbols: str = Field("", max_length=4096)
