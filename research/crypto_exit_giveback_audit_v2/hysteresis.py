"""Observational decision-clock attribution; never proposes replacement exits."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _record(decision: dict[str, Any], symbol: str) -> dict[str, Any]:
    ranks = {item[1]: i + 1 for i, item in enumerate(decision["scores"])}
    rank = ranks.get(symbol)
    eligible = symbol in decision["eligible_symbols"]
    alpha = decision["alpha_target"]
    selected = symbol in alpha["metadata"].get("selected_symbols", [])
    retained = symbol in alpha["metadata"].get("retained", [])
    weight = next((w["weight"] for w in decision["target"]["weights"] if w["symbol"] == symbol), 0.0)
    if not eligible:
        reason = "SCANNER_INELIGIBLE"
    elif rank is None:
        reason = "QUALIFIED_HISTORY_LOSS"
    elif rank > 4:
        reason = "RANK_ABOVE_RETENTION"
    elif not selected:
        reason = "RETENTION_CAPACITY_DISPLACEMENT"
    elif weight <= 0:
        reason = "RISK_OVERLAY_ZERO_TARGET"
    else:
        reason = "SIZE_REBALANCE"
    if not selected or weight <= 0:
        state = "NOT_SELECTED_OR_ZERO_TARGET_PENDING_EXIT"
    elif rank is not None and rank <= 2:
        state = "TOP_TWO_SELECTED"
    elif retained and rank in (3, 4):
        state = "RANK_THREE_FOUR_HYSTERESIS_RETAINED"
    else:
        state = "OTHER_SELECTED"
    return {
        "timestamp": decision["timestamp"],
        "rank": rank,
        "scanner_eligible": eligible,
        "selected": selected,
        "retained": retained,
        "target_weight": weight,
        "held_quantity": decision["holdings"].get(symbol, 0.0),
        "held_symbols": sorted(s for s, q in decision["holdings"].items() if q > 1e-12),
        "selected_symbols": alpha["metadata"].get("selected_symbols", []),
        "state": state,
        "reason": reason,
    }


def _next_boundary(value: str) -> str:
    seconds = _time(value).timestamp()
    return datetime.fromtimestamp((int(seconds // (96 * 3600)) + 1) * 96 * 3600, UTC).isoformat()


def analyze(ep: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Enrich one inventory episode with observed ranks and legally due clocks.

    Rank at a non-decision MFE is explicitly last-observed, never recomputed using
    later prices. Cadence is epoch aligned and is not a holding-age expiry.
    """
    symbol = ep["symbol"]
    start, end, peak = ep["entry_timestamp"], ep["exit_timestamp"], ep["mfe_timestamp"]
    fills = ep["fills"]
    source = fills[0].get("decision_timestamp")
    all_records = [_record(d, symbol) for d in raw["decisions"]]
    path = [
        r
        for r in all_records
        if (start <= r["timestamp"] <= end and r["held_quantity"] > 1e-12) or r["timestamp"] == source
    ]
    asof = next((r for r in reversed(path) if r["timestamp"] <= peak), None)
    after = [r for r in path if peak < r["timestamp"] <= end and r["held_quantity"] > 1e-12]
    failures = [
        r
        for r in path
        if r["held_quantity"] > 1e-12 and (not r["scanner_eligible"] or r["rank"] is None or r["rank"] > 4)
    ]
    zeros = [r for r in path if r["held_quantity"] > 1e-12 and r["target_weight"] <= 0]
    sells = [f for f in fills if f.get("side") == "sell" and f.get("type") != "terminal"]
    lookup = {r["timestamp"]: r for r in all_records}
    final_decision = lookup.get(sells[-1].get("decision_timestamp")) if sells else None
    # A zero-target instruction may have partial fills and later be rescinded.
    # Locate the contiguous final zero-target run, rather than an old exit attempt.
    initiating = None
    if final_decision is not None and final_decision["target_weight"] <= 0:
        relevant = [r for r in path if r["timestamp"] <= final_decision["timestamp"]]
        for row in reversed(relevant):
            if row["target_weight"] > 0:
                break
            initiating = row
    next_actual = next((r["timestamp"] for r in all_records if r["timestamp"] > peak), None)
    boundary = _next_boundary(peak)
    return {
        "decision_path": path,
        "retained_only_after_peak": any(r["state"] == "RANK_THREE_FOUR_HYSTERESIS_RETAINED" for r in after),
        "entry_source_decision": lookup.get(source),
        "mfe_rank_asof": asof,
        "mfe_rank_observation_age_hours": (
            (_time(peak) - _time(asof["timestamp"])).total_seconds() / 3600 if asof else None
        ),
        "mfe_rank_is_contemporaneous_decision": bool(asof and asof["timestamp"] == peak),
        "after_peak_decision_states": after,
        "after_peak_state_counts": {
            state: sum(r["state"] == state for r in after)
            for state in (
                "TOP_TWO_SELECTED",
                "RANK_THREE_FOUR_HYSTERESIS_RETAINED",
                "NOT_SELECTED_OR_ZERO_TARGET_PENDING_EXIT",
                "OTHER_SELECTED",
            )
        },
        "first_rank_failure": failures[0] if failures else None,
        "first_zero_target": zeros[0] if zeros else None,
        "exit_initiating_decision": initiating,
        "exit_final_fill_decision": final_decision,
        "exit_reason": (
            "TERMINAL_CENSORED"
            if ep["censored"]
            else final_decision["reason"]
            if final_decision
            else "NO_EXIT_DECISION"
        ),
        "next_epoch_96h_boundary_after_mfe": boundary,
        "hours_mfe_to_next_epoch_boundary": (_time(boundary) - _time(peak)).total_seconds() / 3600,
        "next_actual_decision_after_mfe": next_actual,
        "hours_mfe_to_next_actual_decision": (
            (_time(next_actual) - _time(peak)).total_seconds() / 3600 if next_actual else None
        ),
        "first_legally_possible_replacement_opportunity_after_mfe": next_actual,
        "first_observed_zero_target_after_mfe": next((r for r in after if r["target_weight"] <= 0), None),
        "holding_age_lock_hours": None,
        "holding_age_expiry_timestamp": None,
        "clock_interpretation": "Epoch-aligned 96h decision opportunity, not mandatory replacement or holding-age expiry; fills occur later.",
    }
