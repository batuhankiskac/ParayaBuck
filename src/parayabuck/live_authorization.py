"""LIVE authorization helpers shared by the CLI, the web manager and the runtime.

- ``live_configuration_digests``: the strategy configuration digests every
  LIVE approval binds (strategy, allocation, universe, risk limits, order
  policy), computed for ``mode=live``.
- The standing LIVE authorization (docs/live_readiness_v1.md B2): creation,
  verification against the current configuration/host, and a status summary
  for the doctor, the auto-start gate and the expiry notices.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from parayabuck.live_safety import (
    STANDING_AUTHORIZATION_EXPIRY_WARNING,
    STANDING_AUTHORIZATION_MAX_DAYS,
    LiveApprovalError,
    StandingLiveAuthorization,
    config_fingerprint,
    standing_authorization_path,
)
from parayabuck.trading_host import trading_host_identity

STANDING_ABSENT = "absent"
STANDING_VALID = "valid"
STANDING_EXPIRED = "expired"
STANDING_INVALID = "invalid"


def live_configuration_digests(config: Any) -> tuple[str, ...]:
    """Digests a LIVE approval binds (one per enabled strategy)."""
    from parayabuck.execution.portfolio import portfolio_execution_policy_from_config, portfolio_risk_limits_from_config
    from parayabuck.portfolio import product_strategy_from
    from parayabuck.portfolio.runtime_service import configuration_digest
    from parayabuck.universe import equity_universe_digest

    equity_universe = config.universe.equity
    selection = config.strategy.equity
    symbols = equity_universe.symbols if equity_universe.enabled else ()
    if not selection.enabled or not symbols:
        return ()
    strategy = product_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    return (
        configuration_digest(
            strategy,
            "live",
            selection.maximum_allocation,
            equity_universe_digest(equity_universe),
            portfolio_risk_limits_from_config(config.risk),
            portfolio_execution_policy_from_config(config.execution),
            config.risk.drawdown_liquidation,
        ),
    )


def standing_authorization_file(config: Any) -> Path | None:
    return standing_authorization_path(config.live.operator_approval_path)


def _approval_secret(config: Any, env: Mapping[str, str | None] | None) -> str:
    source: Mapping[str, str | None] = os.environ if env is None else env
    return str(source.get(config.live.operator_approval_key_env) or "")


def create_standing_authorization(
    config: Any,
    *,
    operator_id: str,
    account_id: str,
    days: int,
    secret: str,
    now: datetime | None = None,
    trading_host: str | None = None,
) -> StandingLiveAuthorization:
    """A signed standing authorization for the current config and this host."""
    if not 1 <= int(days) <= STANDING_AUTHORIZATION_MAX_DAYS:
        raise ValueError(f"--standing-days must be between 1 and {STANDING_AUTHORIZATION_MAX_DAYS}")
    digests = live_configuration_digests(config)
    if not digests:
        raise ValueError("No enabled strategy to authorize")
    issued = now or datetime.now(UTC)
    return StandingLiveAuthorization(
        operator_id=operator_id,
        account_id=account_id,
        configuration_digests=digests,
        config_fingerprint=config_fingerprint(config),
        maximum_dollar_exposure=float(config.live.hard_dollar_cap),
        trading_host=trading_host if trading_host is not None else trading_host_identity(),
        approved_at=issued,
        expires_at=issued + timedelta(days=int(days)),
        nonce=secrets.token_hex(16),
    ).signed(secret)


def load_standing_authorization(
    config: Any,
    *,
    account_id: str | None = None,
    configuration_digests: tuple[str, ...] | None = None,
    now: datetime | None = None,
    env: Mapping[str, str | None] | None = None,
) -> StandingLiveAuthorization:
    """The verified standing authorization, or ``LiveApprovalError``."""
    path = standing_authorization_file(config)
    if path is None:
        raise LiveApprovalError("live.operator_approval_path is not configured")
    return StandingLiveAuthorization.load_verified(
        path,
        secret=_approval_secret(config, env),
        configuration_digests=(
            configuration_digests if configuration_digests is not None else live_configuration_digests(config)
        ),
        config_fingerprint=config_fingerprint(config),
        hard_dollar_cap=float(config.live.hard_dollar_cap),
        trading_host=trading_host_identity(),
        account_id=account_id,
        now=now,
    )


@dataclass(frozen=True)
class StandingStatus:
    status: str
    detail: str
    authorization: StandingLiveAuthorization | None = None

    @property
    def valid(self) -> bool:
        return self.status == STANDING_VALID

    def expiring(self, now: datetime | None = None) -> bool:
        return (
            self.authorization is not None
            and self.valid
            and self.authorization.remaining(now) <= STANDING_AUTHORIZATION_EXPIRY_WARNING
        )


def standing_authorization_status(
    config: Any,
    *,
    now: datetime | None = None,
    env: Mapping[str, str | None] | None = None,
) -> StandingStatus:
    """Classify the standing authorization without any network access."""
    path = standing_authorization_file(config)
    if path is None or not path.exists():
        return StandingStatus(STANDING_ABSENT, "no standing LIVE authorization")
    current = now or datetime.now(UTC)
    try:
        signed = StandingLiveAuthorization.read_signed(path, secret=_approval_secret(config, env))
    except LiveApprovalError as exc:
        return StandingStatus(STANDING_INVALID, str(exc))
    if signed.expires_at <= current:
        return StandingStatus(STANDING_EXPIRED, f"standing LIVE authorization expired at {signed.expires_at}", signed)
    try:
        signed.verify_binding(
            configuration_digests=live_configuration_digests(config),
            config_fingerprint=config_fingerprint(config),
            hard_dollar_cap=float(config.live.hard_dollar_cap),
            trading_host=trading_host_identity(),
            now=current,
        )
    except LiveApprovalError as exc:
        return StandingStatus(STANDING_INVALID, str(exc), signed)
    return StandingStatus(STANDING_VALID, f"valid until {signed.expires_at.isoformat()}", signed)


__all__ = [
    "STANDING_ABSENT",
    "STANDING_EXPIRED",
    "STANDING_INVALID",
    "STANDING_VALID",
    "StandingStatus",
    "create_standing_authorization",
    "live_configuration_digests",
    "load_standing_authorization",
    "standing_authorization_file",
    "standing_authorization_status",
]
