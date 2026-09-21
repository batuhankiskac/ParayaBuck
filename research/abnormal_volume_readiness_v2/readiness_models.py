"""Pure structural models for abnormal-volume G1 readiness v2.

This module deliberately contains no portfolio, return-after-formation, P&L,
or performance code.  The runner and the independent verifier may share the
wire-format dataclasses, but the verifier must implement reconstruction math
itself rather than importing the helpers below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from typing import Any

ZERO = Decimal(0)
ONE = Decimal(1)
TEN_PERCENT = Decimal("0.10")
SLEEVE_CAP = Decimal("0.55")
RESERVED_ROLES = frozenset({"SPY", "SH", "PSQ", "DOG"})


@dataclass(frozen=True, slots=True)
class ResearchBar:
    """One validated split-adjusted daily observation."""

    timestamp: datetime
    session: date
    symbol: str
    entity_id: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("bar timestamp must be timezone-aware")
        if self.timestamp.astimezone(UTC) != self.timestamp:
            raise ValueError("bar timestamp must be normalized to UTC")
        if not self.symbol or not self.entity_id:
            raise ValueError("bar symbol and entity_id are required")
        if any(not value.is_finite() for value in (self.open, self.high, self.low, self.close, self.volume)):
            raise ValueError("bar numerics must be finite")
        if self.volume < ZERO:
            raise ValueError("bar volume must be nonnegative")
        if self.low < ZERO or self.high < self.low or not self.low <= self.open <= self.high:
            raise ValueError("invalid OHLC values")
        if not self.low <= self.close <= self.high:
            raise ValueError("invalid close")

    @property
    def dollar_volume(self) -> Decimal:
        return self.close * self.volume


@dataclass(frozen=True, slots=True)
class SignalRecord:
    """Causal formation-time reconstruction for one eligible entity."""

    entity_id: str
    symbol: str
    reference_sessions: tuple[str, ...]
    reference_dollar_volumes: tuple[Decimal, ...]
    sorted_reference_dollar_volumes: tuple[Decimal, ...]
    q90: Decimal
    formation_dollar_volume: Decimal
    volume_rank: Decimal
    intensity: Decimal
    formation_return: Decimal
    return_rank: Decimal

    def to_dict(self, *, include_evidence: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "entity_id": self.entity_id,
            "symbol": self.symbol,
            "q90": decimal_text(self.q90),
            "formation_dollar_volume": decimal_text(self.formation_dollar_volume),
            "volume_rank": decimal_text(self.volume_rank),
            "intensity": decimal_text(self.intensity),
            "formation_return": decimal_text(self.formation_return),
            "return_rank": decimal_text(self.return_rank),
        }
        if include_evidence:
            payload.update(
                {
                    "reference_sessions": list(self.reference_sessions),
                    "reference_dollar_volumes": [decimal_text(value) for value in self.reference_dollar_volumes],
                    "sorted_reference_dollar_volumes": [
                        decimal_text(value) for value in self.sorted_reference_dollar_volumes
                    ],
                }
            )
        return payload


def decimal_text(value: Decimal) -> str:
    """Return a deterministic, non-exponent Decimal representation."""

    if not value.is_finite():
        raise ValueError("non-finite Decimal cannot be serialized")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def average_rank(values: tuple[Decimal, ...], index: int) -> Decimal:
    """One-based ascending average rank, preserving duplicate positions."""

    if not values:
        raise ValueError("rank input must not be empty")
    value = values[index]
    lower = sum(item < value for item in values)
    equal = sum(item == value for item in values)
    return Decimal(lower + 1) + Decimal(equal - 1) / Decimal(2)


def q90_order_statistic(prior: tuple[Decimal, ...]) -> Decimal:
    """Frozen V2 estimator: ``x_(44)`` for exactly 49 observations."""

    if len(prior) != 49:
        raise ValueError("Q0.90 requires exactly 49 prior observations")
    if any(not value.is_finite() or value <= ZERO for value in prior):
        raise ValueError("Q0.90 observations must be positive and finite")
    return sorted(prior)[43]


def reconstruct_signal(bars: tuple[ResearchBar, ...]) -> tuple[SignalRecord | None, str | None]:
    """Reconstruct the frozen signal from exactly 51 entity observations.

    ``bars`` is ``t-50 .. t``.  It provides the preceding close needed for
    the first of 50 close-to-close returns, while dollar-volume ranks use
    ``t-49 .. t``.  The result contains formation-time facts only.
    """

    if len(bars) != 51:
        return None, "insufficient_51_close_history"
    if any(right.session <= left.session for left, right in zip(bars, bars[1:])):
        return None, "non_strict_session_order"
    entity_ids = {bar.entity_id for bar in bars}
    if len(entity_ids) != 1:
        return None, "ambiguous_entity_history"
    if any(
        not bar.close.is_finite() or not bar.volume.is_finite() or bar.close <= ZERO or bar.volume <= ZERO
        for bar in bars
    ):
        return None, "nonpositive_or_nonfinite_signal_window"

    signal_bars = bars[1:]
    dollar_volumes = tuple(bar.dollar_volume for bar in signal_bars)
    volume_rank = average_rank(dollar_volumes, -1)
    if volume_rank < Decimal(46):
        return None, "volume_rank_below_46"

    with localcontext() as context:
        context.prec = 50
        returns = tuple(right.close / left.close - ONE for left, right in zip(bars, bars[1:]))
    return_rank = average_rank(returns, -1)
    if not Decimal(16) <= return_rank <= Decimal(35):
        return None, "formation_return_outside_middle_40"

    prior = dollar_volumes[:-1]
    q90 = q90_order_statistic(prior)
    formation_dv = dollar_volumes[-1]
    with localcontext() as context:
        context.prec = 50
        record = SignalRecord(
            entity_id=bars[-1].entity_id,
            symbol=bars[-1].symbol,
            reference_sessions=tuple(bar.session.isoformat() for bar in signal_bars[:-1]),
            reference_dollar_volumes=prior,
            sorted_reference_dollar_volumes=tuple(sorted(prior)),
            q90=q90,
            formation_dollar_volume=formation_dv,
            volume_rank=volume_rank,
            intensity=formation_dv / q90,
            formation_return=returns[-1],
            return_rank=return_rank,
        )
    return record, None


def rank_signals(records: tuple[SignalRecord, ...], *, top_n: int = 6) -> tuple[SignalRecord, ...]:
    """Apply the frozen total order and top-six cap."""

    if top_n < 0:
        raise ValueError("top_n must be nonnegative")
    if len({record.entity_id for record in records}) != len(records):
        raise ValueError("signal records must have unique entity_id values")
    eligible = tuple(record for record in records if record.symbol not in RESERVED_ROLES)
    return tuple(
        sorted(
            eligible,
            key=lambda record: (-record.volume_rank, -record.intensity, record.entity_id),
        )[:top_n]
    )


def target_weight(selected_count: int) -> Decimal:
    """Frozen equal weight ``min(0.10, 0.55/N)``."""

    if selected_count <= 0:
        raise ValueError("selected_count must be positive")
    with localcontext() as context:
        context.prec = 50
        return min(TEN_PERCENT, SLEEVE_CAP / Decimal(selected_count))


__all__ = [
    "RESERVED_ROLES",
    "ResearchBar",
    "SignalRecord",
    "average_rank",
    "decimal_text",
    "q90_order_statistic",
    "rank_signals",
    "reconstruct_signal",
    "target_weight",
]
