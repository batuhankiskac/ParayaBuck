"""Structural implementation of the frozen candidate; no portfolio economics."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

RESERVED_ROLES = frozenset({"SPY", "SH", "PSQ", "DOG"})
LOOKBACK_SESSIONS = 252
TOP_N = 6
MAX_ALLOCATION = 0.55
MAX_WEIGHT = 0.10


@dataclass(frozen=True)
class CloseObservation:
    timestamp: datetime
    adjusted_close: float


@dataclass(frozen=True)
class CandidateRankResult:
    decision_timestamp: datetime
    eligible_pool: tuple[str, ...]
    ranked: tuple[tuple[str, float], ...]
    observation_set: tuple[str, ...]
    selected: tuple[str, ...]
    target_weights: tuple[tuple[str, float], ...]
    target_cash_weight: float


def score_history_through_t(
    history: Sequence[CloseObservation],
    *,
    decision_timestamp: datetime,
    expected_session_timestamps: Sequence[datetime],
    identity_resolved: bool,
) -> float | None:
    """Return the frozen close/high ratio, or fail closed as unscoreable."""
    if not identity_resolved or len(history) != LOOKBACK_SESSIONS:
        return None
    if len(expected_session_timestamps) != LOOKBACK_SESSIONS:
        return None
    timestamps = tuple(item.timestamp for item in history)
    if timestamps != tuple(expected_session_timestamps):
        return None
    if timestamps[-1] != decision_timestamp or any(timestamp > decision_timestamp for timestamp in timestamps):
        return None
    closes = tuple(item.adjusted_close for item in history)
    if any(not math.isfinite(value) or value <= 0 for value in closes):
        return None
    return closes[-1] / max(closes)


def rank_broad_pool_through_t(
    eligible_pool: Sequence[str],
    histories: Mapping[str, Sequence[CloseObservation]],
    *,
    decision_timestamp: datetime,
    expected_session_timestamps: Sequence[datetime],
    identity_resolved: Mapping[str, bool],
    observation_capacity: int,
) -> CandidateRankResult:
    """Rank the unchanged PIT eligible pool through completed decision bar t."""
    if observation_capacity < TOP_N:
        raise ValueError("observation capacity must accommodate the frozen top_n")
    canonical_pool = tuple(sorted(dict.fromkeys(symbol.upper() for symbol in eligible_pool)))
    scores: list[tuple[str, float]] = []
    for symbol in canonical_pool:
        if symbol in RESERVED_ROLES:
            continue
        score = score_history_through_t(
            histories.get(symbol, ()),
            decision_timestamp=decision_timestamp,
            expected_session_timestamps=expected_session_timestamps,
            identity_resolved=identity_resolved.get(symbol, False),
        )
        if score is not None:
            scores.append((symbol, score))
    ranked = tuple(sorted(scores, key=lambda item: (-item[1], item[0])))
    observation_set = tuple(symbol for symbol, _score in ranked[:observation_capacity])
    selected = observation_set[:TOP_N]
    weight = min(MAX_ALLOCATION / len(selected), MAX_WEIGHT) if selected else 0.0
    target_weights = tuple((symbol, weight) for symbol in selected)
    gross = sum(value for _symbol, value in target_weights)
    return CandidateRankResult(
        decision_timestamp=decision_timestamp,
        eligible_pool=canonical_pool,
        ranked=ranked,
        observation_set=observation_set,
        selected=selected,
        target_weights=target_weights,
        target_cash_weight=1.0 - gross,
    )
