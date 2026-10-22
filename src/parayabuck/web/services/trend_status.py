"""Pure display helper: trend-vote thresholds from a persisted decision.

The equity strategy records its trend vote detail in ``target.metadata``
(``trend_state``, ``signal_close``, ``trend_signals``). This module turns that
into the price at which the vote would flip, so the dashboard can show during
a drawdown that the bot is still working as designed and where it would exit.

Display-only: no trading state, ledger or runtime is read or written here.

For the 2-of-3 rule the votes are ``close > threshold`` for SMA, the
126-session channel midpoint (DON) and the TSM base price implied by the
252-session return (``close / (1 + return)``). With three thresholds the
second-highest (== second-lowest) is the level whose crossing flips the vote:
in trend a close below it turns at least two votes down; out of trend a close
above it turns at least two votes up. Older decisions predate
``trend_signals``; the SMA-only rule then has a single threshold.
"""

from __future__ import annotations

import math
from typing import Any

_VOTE_KEYS = ("sma", "tsm", "don")


def _number(value: Any) -> float | None:
    """A finite float from JSON metadata, else None (booleans are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _signal(signals: dict[str, Any], key: str) -> dict[str, Any]:
    value = signals.get(key)
    return value if isinstance(value, dict) else {}


def _vote_thresholds(signals: dict[str, Any], close: float | None) -> dict[str, float]:
    """Per-vote price thresholds; TSM is the base price implied by its return."""
    thresholds: dict[str, float] = {}
    sma = _number(_signal(signals, "sma").get("sma"))
    if sma is not None:
        thresholds["sma"] = sma
    don = _number(_signal(signals, "don").get("midpoint"))
    if don is not None:
        thresholds["don"] = don
    tsm_return = _number(_signal(signals, "tsm").get("return"))
    # A return of -100% or worse has no finite base price.
    if close is not None and tsm_return is not None and tsm_return > -1.0:
        thresholds["tsm"] = close / (1.0 + tsm_return)
    return thresholds


def _in_trend(
    state: str | None,
    votes_up: int | None,
    votes_total: int | None,
    close: float | None,
    levels: list[float],
) -> bool | None:
    if state == "uptrend":
        return True
    if state in {"downtrend", "early_reentry"}:
        return False
    if votes_up is not None and votes_total:
        return votes_up * 2 > votes_total
    if len(levels) == 1 and close is not None:
        return close > levels[0]
    return None


def _drawdown_pct(account: dict[str, Any] | None) -> float | None:
    """Drawdown from the peak, in percent, when the account snapshot carries it."""
    if not isinstance(account, dict):
        return None
    equity = _number(account.get("equity"))
    drawdown = _number(account.get("drawdown"))
    if equity is None or drawdown is None:
        return None
    peak = equity + drawdown
    if peak <= 0:
        return None
    return (drawdown / peak) * 100.0


def trend_status_from_metadata(
    metadata: dict[str, Any] | None,
    account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trend state plus the vote thresholds from one decision's metadata.

    ``available`` is false when the metadata is missing or predates both the
    vote detail and the SMA value. Every price/volatility field is None when
    the decision does not carry it; nothing here raises on partial metadata.
    Distances are in percent (``-6.7`` means 6.7% below the last close).
    """
    meta = metadata if isinstance(metadata, dict) else {}
    signals_value = meta.get("trend_signals")
    signals = signals_value if isinstance(signals_value, dict) else {}

    close = _number(meta.get("signal_close"))
    if close is None:
        close = _number(_signal(signals, "sma").get("close"))
    if close is None:
        close = _number(_signal(signals, "don").get("close"))

    thresholds = _vote_thresholds(signals, close)
    if not thresholds:
        # VT35 / decisions from before trend_signals: one SMA vote.
        sma = _number(meta.get("signal_sma"))
        if sma is not None:
            thresholds["sma"] = sma
    levels = sorted(thresholds.values())

    state_value = meta.get("trend_state")
    state = state_value if isinstance(state_value, str) else None

    evaluated = [
        key
        for key in _VOTE_KEYS
        if isinstance(signals.get(key), dict) and isinstance(signals[key].get("up"), bool)
    ]
    votes_total: int | None
    if evaluated:
        votes_total = len(evaluated)
    elif thresholds:
        votes_total = 1
    else:
        votes_total = None

    raw_votes = signals.get("votes_up")
    votes_up = raw_votes if isinstance(raw_votes, int) and not isinstance(raw_votes, bool) else None
    if votes_up is None and evaluated:
        votes_up = sum(1 for key in evaluated if signals[key]["up"])

    in_trend = _in_trend(state, votes_up, votes_total, close, levels)
    if votes_up is None and votes_total == 1 and in_trend is not None:
        votes_up = 1 if in_trend else 0

    exit_price: float | None = None
    reentry_price: float | None = None
    exit_distance_pct: float | None = None
    reentry_distance_pct: float | None = None
    if in_trend is not None and levels and close is not None and close > 0:
        exit_price = levels[-2] if len(levels) >= 2 else levels[0]
        reentry_price = levels[1] if len(levels) >= 2 else levels[0]
        exit_distance_pct = (exit_price / close - 1.0) * 100.0
        reentry_distance_pct = (reentry_price / close - 1.0) * 100.0

    volatility = _number(meta.get("realized_volatility"))
    vol_target = _number(meta.get("vol_target"))
    vol_trim_at: float | None = None
    vol_trim: bool | None = None
    if vol_target is not None and vol_target > 0:
        vol_trim_at = vol_target
        vol_trim = volatility is not None and volatility > vol_target

    early_value = meta.get("early_reentry")
    early = early_value if isinstance(early_value, dict) else {}

    signal_symbol_value = meta.get("signal_symbol")
    rule_value = meta.get("trend_rule")

    return {
        "available": bool(thresholds or state),
        "state": state,
        "in_trend": in_trend,
        "rule": rule_value if isinstance(rule_value, str) else None,
        "signal_symbol": signal_symbol_value if isinstance(signal_symbol_value, str) else None,
        "signal_close": close,
        "votes_up": votes_up,
        "votes_total": votes_total,
        "thresholds": thresholds or None,
        "exit_price": exit_price,
        "exit_distance_pct": exit_distance_pct,
        "reentry_price": reentry_price,
        "reentry_distance_pct": reentry_distance_pct,
        "volatility": volatility,
        "vol_target": vol_target,
        "vol_trim": vol_trim,
        "vol_trim_at": vol_trim_at,
        "drawdown_pct": _drawdown_pct(account),
        "early_reentry_active": bool(early.get("active")),
        "early_reentry_weight": _number(early.get("weight")),
    }


__all__ = ["trend_status_from_metadata"]
