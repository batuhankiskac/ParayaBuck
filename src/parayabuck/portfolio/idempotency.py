"""Deterministic, idempotent client order ids.

Random UUIDs break idempotency across restarts: a broker receiving the same
intent twice must be able to recognise it. The key is fully derived from the
strategy identity, decision timestamp, symbol, side and rebalance number, so
re-running the same decision with the same state produces the identical key
and the broker deduplicates the order.
"""

from __future__ import annotations

from datetime import datetime


class IdempotencyKeyFactory:
    def __init__(self, strategy_name: str, strategy_version: str) -> None:
        if not strategy_name.strip() or not strategy_version.strip():
            raise ValueError("Strategy identity is required for idempotency keys")
        self._strategy_name = strategy_name
        self._strategy_version = strategy_version

    def key(
        self,
        *,
        decision_timestamp: datetime,
        symbol: str,
        side: str,
        rebalance_number: int,
    ) -> str:
        if decision_timestamp.tzinfo is None:
            raise ValueError("Decision timestamps must be timezone-aware")
        if not symbol.strip() or not side.strip():
            raise ValueError("Symbol and side are required")
        return (
            f"{self._strategy_name}|{self._strategy_version}|"
            f"{decision_timestamp.isoformat()}|{symbol}|{side}|{rebalance_number}"
        )


__all__ = ["IdempotencyKeyFactory"]
