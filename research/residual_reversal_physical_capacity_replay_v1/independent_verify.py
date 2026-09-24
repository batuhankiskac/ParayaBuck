"""Independent, no-return checks over the serialized physical capacity trace."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from .no_economic_leakage import assert_no_economics

SOURCES = ("v3", "reversal", "crypto")
BUDGETS = {"v3": 0.4125, "reversal": 0.1375, "crypto": 0.05}
TOLERANCE = 1e-8
COUNT_REASONS = frozenset({"GLOBAL_POSITION_COUNT", "PENDING_OPENING_RESERVATION", "PENDING_SELL_SLOT_NOT_RELEASED"})
FULL_SLOT_REASONS = frozenset({"GLOBAL_POSITION_COUNT", "PENDING_OPENING_RESERVATION"})


def canonical_hash(events: list[dict[str, Any]]) -> str:
    """Hash the raw structural facts, without trusting a replay receipt."""
    assert_no_economics(events)
    payload = json.dumps(events, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row["market"]), str(row["symbol"])


def _count_pressure(row: dict[str, Any], cap: int, planned: set[tuple[str, str]]) -> bool:
    """Require a full ledger or a count-capped target plan observed in the decision."""
    if row["denial_reason"] not in COUNT_REASONS:
        return False
    actual_full = max(int(row["slot_claims_before"]), int(row["slot_claims_after"])) >= cap
    return actual_full or (
        row["denial_reason"] in FULL_SLOT_REASONS and (str(row["timestamp"]), str(row["market"])) in planned
    )


def _keys(raw: Any) -> set[tuple[str, str]]:
    return {(str(market), str(symbol)) for market, symbol in raw}


def _source_quantities(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError("source quantities must be a mapping")
    values = {str(source): float(quantity) for source, quantity in raw.items()}
    if set(values) - set(SOURCES) or any(value < -TOLERANCE for value in values.values()):
        raise ValueError("invalid source quantity")
    return {source: values.get(source, 0.0) for source in SOURCES}


def _source_quantities_signed(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError("source allocation must be a mapping")
    values = {str(source): float(quantity) for source, quantity in raw.items()}
    if set(values) - set(SOURCES):
        raise ValueError("unknown source allocation")
    return {source: values.get(source, 0.0) for source in SOURCES}


def _share(amount: float, basis: dict[str, float]) -> dict[str, float]:
    """Deterministic pro-rata allocation with the last source as remainder."""
    positive = [source for source in sorted(SOURCES) if basis[source] > 1e-12]
    if not positive:
        if amount > 1e-12:
            raise ValueError("no source for nonzero quantity")
        return dict.fromkeys(SOURCES, 0.0)
    denominator = sum(basis[source] for source in positive)
    assigned: dict[str, float] = dict.fromkeys(SOURCES, 0.0)
    used = 0.0
    for source in positive[:-1]:
        assigned[source] = amount * basis[source] / denominator
        used += assigned[source]
    assigned[positive[-1]] = amount - used
    return assigned


def _apply_source_fill(
    current: dict[str, float], desired: dict[str, float], side: str, quantity: float, market: str
) -> dict[str, float]:
    """Recompute physical source ownership using only pre-fill claims and qty."""
    if side == "buy":
        deficits = {source: max(0.0, desired[source] - current[source]) for source in SOURCES}
        first = min(quantity, sum(deficits.values()))
        initial = _share(first, deficits)
        remaining = quantity - first
        fallback = (
            desired
            if sum(desired.values()) > 1e-12
            else {source: float(source == ("crypto" if market == "crypto" else "v3")) for source in SOURCES}
        )
        extra = _share(remaining, fallback) if remaining > 1e-12 else dict.fromkeys(SOURCES, 0.0)
        return {source: current[source] + initial[source] + extra[source] for source in SOURCES}
    if side == "sell":
        if quantity > sum(current.values()) + TOLERANCE:
            raise ValueError("source SELL exceeds attributed inventory")
        excess = {source: max(0.0, current[source] - desired[source]) for source in SOURCES}
        first = min(quantity, sum(excess.values()))
        initial = _share(first, excess)
        residual = {source: max(0.0, current[source] - initial[source]) for source in SOURCES}
        remaining = quantity - first
        extra = _share(remaining, residual) if remaining > TOLERANCE else dict.fromkeys(SOURCES, 0.0)
        return {source: max(0.0, residual[source] - extra[source]) for source in SOURCES}
    raise ValueError("unknown fill side")


def verify_arm(events: list[dict[str, Any]], *, cap: int) -> dict[str, Any]:
    """Rebuild claims, reservations, attribution, denials and timing from events.

    Raises on an incomplete or contradictory trace. No replay summary is read.
    """
    if cap not in (6, 8):
        raise ValueError("Only the frozen six/eight arms are permitted")
    digest = canonical_hash(events)
    errors: list[str] = []
    positions: dict[tuple[str, str], float] = defaultdict(float)
    reservations: dict[tuple[str, str], tuple[str, str, str]] = {}
    reservation_origins: dict[tuple[str, str], str] = {}
    source_claims: dict[tuple[str, str], dict[str, float]] = {}
    source_inventory: dict[tuple[str, str], dict[str, float]] = {}
    expected_inventory: dict[tuple[str, str], tuple[str, dict[str, float], dict[str, float]]] = {}
    expected_decision_inventory: set[tuple[str, str]] = set()
    split_ratios: dict[tuple[tuple[str, str], str], float] = {}
    source_rows: list[dict[str, Any]] = []
    decision_labels: set[str] = set()
    decision_times_by_label: dict[str, str] = {}
    decision_snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    admitted_openings_awaiting_reservation: set[tuple[str, str, str]] = set()
    max_claims = max_crypto = 0
    fill_count = 0
    mark_count = final_state_count = 0
    account_marks: list[tuple[str, int, int, int]] = []
    reservation_counts = {"equity": 0, "crypto": 0}
    for index, row in enumerate(events):
        kind = row.get("event")
        try:
            if expected_inventory and kind != "source_inventory":
                raise ValueError("physical inventory change lacks immediate source update")
            if kind == "reservation_created":
                order = (str(row["market"]), str(row["client_order_id"]))
                if order in reservations:
                    raise ValueError("duplicate reservation")
                reservations[order] = (str(row["market"]), str(row["symbol"]), str(row["side"]))
                reservation_origins[order] = str(row.get("decision_label_time", ""))
                reservation_counts[str(row["market"])] += 1
                if row["side"] == "buy":
                    admitted_openings_awaiting_reservation.discard(
                        (str(row["timestamp"]), str(row["market"]), str(row["symbol"]))
                    )
            elif kind == "source_order_claim":
                order = (str(row["market"]), str(row["client_order_id"]))
                if order not in reservations or order in source_claims:
                    raise ValueError("source claim lacks a new reserved order")
                if reservations[order][:2] != _key(row) or reservations[order][2] != str(row["side"]):
                    raise ValueError("source claim identity differs from reservation")
                desired = _source_quantities(row["desired_source_qty"])
                origin_time = decision_times_by_label.get(reservation_origins[order], str(row["timestamp"]))
                claim_admitted = {
                    source: sum(
                        float(item.get("deferred_target_contribution", item["admitted_target_contribution"]))
                        for item in source_rows
                        if item["timestamp"] == origin_time and _key(item) == _key(row) and item["source"] == source
                    )
                    for source in SOURCES
                }
                scales = [desired[source] / weight for source, weight in claim_admitted.items() if weight > TOLERANCE]
                if any(desired[source] > TOLERANCE and claim_admitted[source] <= TOLERANCE for source in SOURCES) or (
                    scales and max(scales) - min(scales) > TOLERANCE * max(scales)
                ):
                    raise ValueError("frozen source claim changes admitted source proportions")
                source_claims[order] = desired
            elif kind == "source_order_adjustment":
                order = (str(row["market"]), str(row["client_order_id"]))
                if order not in reservations or order not in source_claims or row["cause"] != "split":
                    raise ValueError("unsupported source claim adjustment")
                if reservations[order][:2] != _key(row):
                    raise ValueError("source claim adjustment has wrong instrument")
                ratio = float(row["split_ratio"])
                adjusted = _source_quantities(row["desired_source_qty"])
                if (
                    ratio <= 0
                    or abs(split_ratios.get((_key(row), str(row["timestamp"])), 0.0) - ratio) > TOLERANCE
                    or any(
                        abs(adjusted[source] - source_claims[order][source] * ratio) > TOLERANCE for source in SOURCES
                    )
                ):
                    raise ValueError("source claim adjustment differs from split")
                source_claims[order] = adjusted
            elif kind == "reservation_released":
                order = (str(row["market"]), str(row["client_order_id"]))
                if order not in reservations:
                    raise ValueError("release without reservation")
                del reservations[order]
                reservation_origins.pop(order, None)
                source_claims.pop(order, None)
            elif kind == "fill":
                created = datetime.fromisoformat(str(row["order_created_at"]))
                price_event = datetime.fromisoformat(str(row["source_price_event_time"]))
                if not created < price_event or price_event != datetime.fromisoformat(str(row["timestamp"])):
                    raise ValueError("noncausal fill")
                key = _key(row)
                qty = float(row["qty"])
                if qty <= 0:
                    raise ValueError("nonpositive fill")
                positions[key] += qty if row["side"] == "buy" else -qty
                if positions[key] < -TOLERANCE:
                    raise ValueError("sell exceeds position")
                claim_key = (str(row["market"]), str(row["client_order_id"]))
                if claim_key not in source_claims or reservations.get(claim_key, ())[:2] != key:
                    raise ValueError("fill lacks frozen source order claim")
                previous = source_inventory.get(key, dict.fromkeys(SOURCES, 0.0))
                updated = _apply_source_fill(previous, source_claims[claim_key], str(row["side"]), qty, key[0])
                expected_inventory[key] = (
                    "fill",
                    updated,
                    {source: updated[source] - previous[source] for source in SOURCES},
                )
                fill_count += 1
            elif kind in ("terminal_liquidation", "final_liquidation"):
                key = _key(row)
                previous = source_inventory.get(key, dict.fromkeys(SOURCES, 0.0))
                positions[key] -= float(row["quantity"])
                if positions[key] < -TOLERANCE:
                    raise ValueError("liquidation exceeds position")
                expected_inventory[key] = (
                    str(kind),
                    dict.fromkeys(SOURCES, 0.0),
                    {source: -previous[source] for source in SOURCES},
                )
            elif kind == "corporate_action" and row.get("split_ratio") is not None:
                key = _key(row)
                ratio = float(row["split_ratio"])
                split_ratios[(key, str(row["timestamp"]))] = ratio
                previous = source_inventory.get(key, dict.fromkeys(SOURCES, 0.0))
                positions[key] *= ratio
                if sum(previous.values()) > TOLERANCE:
                    updated = {source: previous[source] * ratio for source in SOURCES}
                    expected_inventory[key] = (
                        "split",
                        updated,
                        {source: updated[source] - previous[source] for source in SOURCES},
                    )
            elif kind == "corporate_action" and row.get("action_type") == "dividend":
                key = _key(row)
                if positions[key] > TOLERANCE:
                    previous = source_inventory.get(key, dict.fromkeys(SOURCES, 0.0))
                    expected_inventory[key] = ("dividend_entitlement", previous, dict.fromkeys(SOURCES, 0.0))
            elif kind == "decision":
                before = _keys(row["position_keys_before"])
                actual = {key for key, qty in positions.items() if qty > TOLERANCE}
                if before != actual:
                    raise ValueError("decision position snapshot differs from fill ledger")
                opening = _keys(row["pending_opening_keys_before"])
                reserved_opening = {(market, symbol) for market, symbol, side in reservations.values() if side == "buy"}
                if opening != reserved_opening:
                    raise ValueError("decision opening reservations differ from order ledger")
                selling = _keys(row["pending_sell_keys_before"])
                reserved_selling = {
                    (market, symbol) for market, symbol, side in reservations.values() if side == "sell"
                }
                if selling != reserved_selling or not selling <= actual:
                    raise ValueError("pending SELL freed position early")
                claims = before | opening
                if len(claims) != int(row["slot_claims_before"]):
                    raise ValueError("decision slot count differs from ledger")
                if len(claims) > cap:
                    raise ValueError("decision exceeds global cap")
                target_symbols = [str(symbol) for symbol, weight in row.get("target_weights", []) if float(weight) > 0]
                if len(target_symbols) != len(set(target_symbols)) or len(target_symbols) > cap:
                    raise ValueError("decision target names exceed global cap")
                decision_labels.add(str(row["label_time"]))
                decision_times_by_label[str(row["label_time"])] = str(row["timestamp"])
                decision_snapshots[(str(row["timestamp"]), str(row["market"]))] = row
                expected_decision_inventory.update(
                    key for key in _keys(row["position_keys_after"]) if key[0] == str(row["market"])
                )
            elif kind == "source_admission":
                if row["source"] not in SOURCES or row["source_label"] != row["source"]:
                    raise ValueError("unknown source identity")
                if int(row["global_cap"]) != cap:
                    raise ValueError("arm cap mismatch")
                requested = float(row["requested_target_contribution"])
                admitted = float(row["admitted_target_contribution"])
                if requested < -TOLERANCE or admitted < -TOLERANCE or admitted > requested + TOLERANCE:
                    raise ValueError("invalid source contribution")
                if row["denial_reason"] == "GLOBAL_POSITION_COUNT" and bool(row["admitted_opening"]):
                    raise ValueError("count-denied opening was admitted")
                if row["source"] == "reversal" and row["market"] != "equity":
                    raise ValueError("reversal source is not equity")
                if int(row["source_rank"]) < 1:
                    raise ValueError("invalid source rank")
                snapshot = decision_snapshots.get((str(row["timestamp"]), str(row["market"])))
                if snapshot is None:
                    raise ValueError("source claim without physical decision")
                position_keys = _keys(snapshot["position_keys_before"])
                opening_keys = _keys(snapshot["pending_opening_keys_before"])
                sell_keys = _keys(snapshot["pending_sell_keys_before"])
                if bool(row["existing_position"]) != (_key(row) in position_keys):
                    raise ValueError("source existing-position flag differs from ledger")
                if bool(row["working_opening_reservation"]) != (_key(row) in opening_keys):
                    raise ValueError("source opening-reservation flag differs from ledger")
                after_sell_keys = _keys(snapshot.get("pending_sell_keys_after", []))
                if (sell_keys and not bool(row["pending_sell"])) or (
                    bool(row["pending_sell"]) and not (sell_keys or after_sell_keys)
                ):
                    raise ValueError("source pending-SELL flag differs from ledger")
                if int(row["slot_claims_before"]) != int(snapshot["slot_claims_before"]):
                    raise ValueError("source slot count differs from decision")
                if int(row["slot_claims_after"]) != int(snapshot["slot_claims_after"]):
                    raise ValueError("source post-decision slot count differs from decision")
                if int(row["slot_claims_after"]) > cap or int(row["crypto_count"]) > 2:
                    raise ValueError("source claim exceeds arm cap")
                reason = row["denial_reason"]
                actual_full = max(int(snapshot["slot_claims_before"]), int(snapshot["slot_claims_after"])) >= cap
                planned_full = len(
                    [symbol for symbol, weight in snapshot.get("target_weights", []) if float(weight) > 0]
                ) >= cap and "position_count_limit" in snapshot.get("risk_overlay_reasons", [])
                if reason in FULL_SLOT_REASONS and not (actual_full or planned_full):
                    raise ValueError("global-count denial without a full slot ledger")
                if reason == "PENDING_SELL_SLOT_NOT_RELEASED" and not (sell_keys or after_sell_keys):
                    raise ValueError("pending-SELL denial without a working SELL")
                if (
                    reason == "CRYPTO_POSITION_COUNT"
                    and max(int(snapshot["crypto_claim_count_before"]), int(snapshot["crypto_claim_count_after"])) < 2
                ):
                    raise ValueError("crypto-count denial without two crypto claims")
                if reason in COUNT_REASONS | {"CRYPTO_POSITION_COUNT"} and (
                    not bool(row["requested_opening"]) or bool(row["admitted_opening"])
                ):
                    raise ValueError("count denial is not a denied opening")
                if bool(row["admitted_opening"]):
                    admitted_openings_awaiting_reservation.add(
                        (str(row["timestamp"]), str(row["market"]), str(row["symbol"]))
                    )
                source_rows.append(row)
            elif kind == "source_inventory":
                key = _key(row)
                current = _source_quantities(row["source_quantities"])
                previous = source_inventory.get(key, dict.fromkeys(SOURCES, 0.0))
                expected = expected_inventory.pop(key, None)
                if expected is None:
                    if row["cause"] != "decision":
                        raise ValueError("source inventory has no physical cause")
                    if key not in expected_decision_inventory:
                        raise ValueError("unexpected decision source inventory")
                    expected_decision_inventory.remove(key)
                    expected = ("decision", previous, dict.fromkeys(SOURCES, 0.0))
                cause, quantities, allocation = expected
                if row["cause"] != cause:
                    raise ValueError("source inventory cause differs from physical event")
                if any(abs(current[source] - quantities[source]) > TOLERANCE for source in SOURCES):
                    raise ValueError("source inventory differs from independent allocation")
                reported_allocation = _source_quantities_signed(row["source_allocation"])
                if any(abs(reported_allocation[source] - allocation[source]) > TOLERANCE for source in SOURCES):
                    raise ValueError("source allocation delta differs from independent allocation")
                if abs(float(row["qty_delta"]) - sum(allocation.values())) > TOLERANCE:
                    raise ValueError("source allocation delta differs from reported quantity delta")
                if (
                    abs(sum(current.values()) - positions[key]) > TOLERANCE
                    or abs(float(row["position_qty"]) - positions[key]) > TOLERANCE
                ):
                    raise ValueError("source quantities differ from account inventory")
                if row.get("client_order_id") is not None:
                    claim_key = (key[0], str(row["client_order_id"]))
                    if _source_quantities(row["desired_source_qty"]) != source_claims.get(claim_key):
                        raise ValueError("frozen source claim changed after order creation")
                source_inventory[key] = current
            elif kind == "account_mark":
                mark_count += 1
                if admitted_openings_awaiting_reservation:
                    raise ValueError("admitted opening has no same-cycle BUY reservation")
                if expected_decision_inventory:
                    raise ValueError("decision holdings lack source inventory receipts")
                if any(order not in source_claims for order in reservations):
                    raise ValueError("working order lacks frozen source claim")
                active = {key for key, qty in positions.items() if qty > TOLERANCE}
                pending_open = {(market, symbol) for market, symbol, side in reservations.values() if side == "buy"}
                claims = active | pending_open
                crypto = sum(market == "crypto" for market, _symbol in active)
                if len(claims) != int(row["slot_claim_count"]) or len(active) != int(row["position_count"]):
                    raise ValueError("account mark disagrees with independent ledger")
                if len(claims) > cap or crypto > 2:
                    raise ValueError("slot or crypto cap exceeded")
                max_claims = max(max_claims, len(claims))
                max_crypto = max(max_crypto, crypto)
                account_marks.append((str(row["timestamp"]), len(active), len(claims), crypto))
            elif kind == "final_state":
                final_state_count += 1
                pending = {(str(market), str(order_id)) for market, order_id, _symbol, _side, _qty in row["pending"]}
                if pending != set(reservations):
                    raise ValueError("final pending orders differ from reservation ledger")
            elif kind == "slot_claim_violation":
                raise ValueError("scheduler reported slot violation")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"event {index} {kind}: {exc}")
    if not source_rows:
        errors.append("no source admission events")
    if mark_count == 0 or final_state_count != 1:
        errors.append("missing account marks or unique final state")
    if expected_inventory:
        errors.append("physical events missing source inventory updates")
    if expected_decision_inventory:
        errors.append("decision holdings missing source inventory updates")
    if admitted_openings_awaiting_reservation:
        errors.append("admitted openings missing BUY reservation receipts")
    if any(order not in source_claims for order in reservations):
        errors.append("working order missing frozen source claim")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        grouped[(str(row["timestamp"]), str(row["source"]))].append(row)
    for (stamp, source), rows in grouped.items():
        total = sum(float(row["admitted_target_contribution"]) for row in rows)
        if total > BUDGETS[source] + TOLERANCE:
            errors.append(f"{stamp} {source} exceeds sleeve budget")
    by_symbol: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        by_symbol[(str(row["timestamp"]), str(row["market"]), str(row["symbol"]))].append(row)
    for (stamp, _market, symbol), rows in by_symbol.items():
        total = sum(float(row["admitted_target_contribution"]) for row in rows)
        targets = {float(row["combined_account_target"]) for row in rows}
        if total > 0.10 + TOLERANCE or any(value > 0.10 + TOLERANCE for value in targets):
            errors.append(f"{stamp} {symbol} exceeds per-name cap")
        if len(targets) != 1 or total > next(iter(targets)) + TOLERANCE:
            errors.append(f"{stamp} {symbol} source attribution exceeds account target")
        if any(bool(row["overlap"]) != (len(rows) > 1) for row in rows):
            errors.append(f"{stamp} {symbol} overlap flag differs from source rows")
    unique_source_rows = {
        (str(row["timestamp"]), str(row["market"]), str(row["source"]), str(row["symbol"])) for row in source_rows
    }
    if len(unique_source_rows) != len(source_rows):
        errors.append("duplicate source-symbol claim")
    denials = {
        source: sum(
            row["source"] == source
            and row["denial_reason"] == "GLOBAL_POSITION_COUNT"
            and bool(row["requested_opening"])
            for row in source_rows
        )
        for source in SOURCES
    }
    planned_pressure = {
        key
        for key, snapshot in decision_snapshots.items()
        if len([symbol for symbol, weight in snapshot.get("target_weights", []) if float(weight) > 0]) >= cap
        and "position_count_limit" in snapshot.get("risk_overlay_reasons", [])
    }
    unused = sum(
        float(row["requested_target_contribution"]) - float(row["admitted_target_contribution"])
        for row in source_rows
        if row["source"] == "reversal" and _count_pressure(row, cap, planned_pressure)
    )
    broad_denials = {
        source: sum(
            row["source"] == source and _count_pressure(row, cap, planned_pressure) and bool(row["requested_opening"])
            for row in source_rows
        )
        for source in SOURCES
    }
    source_capacity = {}
    for source in SOURCES:
        rows = [row for row in source_rows if row["source"] == source]
        source_capacity[source] = {
            "requested_unique_openings": len(
                {(row["timestamp"], row["market"], row["symbol"]) for row in rows if row["requested_opening"]}
            ),
            "admitted_unique_openings": len(
                {(row["timestamp"], row["market"], row["symbol"]) for row in rows if row["admitted_opening"]}
            ),
            "requested_target_exposure_sum": sum(float(row["requested_target_contribution"]) for row in rows),
            "admitted_target_exposure_sum": sum(float(row["admitted_target_contribution"]) for row in rows),
            "count_induced_unused_target_exposure_sum": sum(
                float(row["requested_target_contribution"]) - float(row["admitted_target_contribution"])
                for row in rows
                if _count_pressure(row, cap, planned_pressure)
            ),
        }
    overlap = {
        (stamp, symbol)
        for (stamp, market, symbol), rows in by_symbol.items()
        if market == "equity" and {str(row["source"]) for row in rows} == {"v3", "reversal"}
    }
    candidate_states = {
        (str(row["timestamp"]), str(row["symbol"])): (bool(row["admitted_opening"]), row["denial_reason"])
        for row in source_rows
        if row["source"] == "reversal" and bool(row["requested_opening"])
    }
    if len(candidate_states) != sum(
        row["source"] == "reversal" and bool(row["requested_opening"]) for row in source_rows
    ):
        errors.append("duplicate candidate opening state")
    denial_ledger = [
        {
            "timestamp": row["timestamp"],
            "source": row["source"],
            "symbol": row["symbol"],
            "reason": row["denial_reason"],
            "requested_target_contribution": row["requested_target_contribution"],
            "admitted_target_contribution": row["admitted_target_contribution"],
        }
        for row in source_rows
        if row["denial_reason"] is not None
    ]
    equity_source_demand = [
        {
            "timestamp": row["timestamp"],
            "source": row["source"],
            "symbol": row["symbol"],
            "source_rank": row["source_rank"],
            "requested_target_contribution": row["requested_target_contribution"],
        }
        for row in source_rows
        if row["source"] in ("v3", "reversal")
    ]
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "event_sha256": digest,
        "denial_ledger_sha256": canonical_hash(denial_ledger),
        "equity_source_demand_sha256": canonical_hash(equity_source_demand),
        "event_count": len(events),
        "price_event_clock_sha256": hashlib.sha256(
            json.dumps([stamp for stamp, _position, _claim, _crypto in account_marks], separators=(",", ":")).encode()
        ).hexdigest(),
        "first_price_event": account_marks[0][0] if account_marks else None,
        "last_price_event": account_marks[-1][0] if account_marks else None,
        "fill_count": fill_count,
        "whole_account_decision_timestamp_count": len({stamp for stamp, _position, _claim, _crypto in account_marks}),
        "average_active_positions": sum(position for _stamp, position, _claim, _crypto in account_marks) / mark_count
        if mark_count
        else 0.0,
        "average_effective_claims": sum(claim for _stamp, _position, claim, _crypto in account_marks) / mark_count
        if mark_count
        else 0.0,
        "at_cap_timestamp_count": sum(claim == cap for _stamp, _position, claim, _crypto in account_marks),
        "crypto_average_active_positions": sum(crypto for _stamp, _position, _claim, crypto in account_marks)
        / mark_count
        if mark_count
        else 0.0,
        "reservations_created_by_market": reservation_counts,
        "maximum_claims": max_claims,
        "maximum_crypto_positions": max_crypto,
        "active_reservation_count": len(reservations),
        "final_position_keys": sorted([list(key) for key, qty in positions.items() if qty > TOLERANCE]),
        "global_count_denials": denials,
        "all_count_related_denials": broad_denials,
        "source_capacity": source_capacity,
        "equity_overlap_decision_count": len({stamp for stamp, _symbol in overlap}),
        "equity_overlap_symbol_event_count": len(overlap),
        "equity_overlap_slots_saved": len(overlap),
        "reversal_count_unused_target_exposure": unused,
        "eligible_reversal_decision_count": len(
            {str(row["timestamp"]) for row in source_rows if row["source"] == "reversal"}
        ),
        "candidate_opening_states": [
            {"timestamp": stamp, "symbol": symbol, "admitted": state[0], "denial_reason": state[1]}
            for (stamp, symbol), state in sorted(candidate_states.items())
        ],
        "decision_label_count": len(decision_labels),
        "source_admission_count": len(source_rows),
    }


def compare_arms(
    cap6: dict[str, Any], cap8: dict[str, Any], *, repeat6: dict[str, Any], repeat8: dict[str, Any]
) -> dict[str, Any]:
    """Calculate frozen M1-M8 from independently reconstructed arm receipts."""
    blocked = any(arm["status"] != "PASS" for arm in (cap6, cap8, repeat6, repeat8))
    blocked = blocked or cap6["equity_source_demand_sha256"] != cap8["equity_source_demand_sha256"]
    d6 = cap6["global_count_denials"]
    d8 = cap8["global_count_denials"]
    reversal6 = int(d6["reversal"])
    reversal8 = int(d8["reversal"])
    unused6 = float(cap6["reversal_count_unused_target_exposure"])
    unused8 = float(cap8["reversal_count_unused_target_exposure"])
    states6 = {(row["timestamp"], row["symbol"]): row for row in cap6["candidate_opening_states"]}
    states8 = {(row["timestamp"], row["symbol"]): row for row in cap8["candidate_opening_states"]}
    eligible = max(int(cap6["eligible_reversal_decision_count"]), int(cap8["eligible_reversal_decision_count"]))
    changed = {
        stamp
        for (stamp, symbol) in states6.keys() | states8.keys()
        if (states6.get((stamp, symbol), {}).get("admitted") != states8.get((stamp, symbol), {}).get("admitted"))
        and (
            states6.get((stamp, symbol), {}).get("denial_reason") == "GLOBAL_POSITION_COUNT"
            or states8.get((stamp, symbol), {}).get("denial_reason") == "GLOBAL_POSITION_COUNT"
        )
    }
    deterministic = all(
        a["event_sha256"] == b["event_sha256"]
        and a["denial_ledger_sha256"] == b["denial_ledger_sha256"]
        and a["final_position_keys"] == b["final_position_keys"]
        and a["global_count_denials"] == b["global_count_denials"]
        and a["reversal_count_unused_target_exposure"] == b["reversal_count_unused_target_exposure"]
        for a, b in ((cap6, repeat6), (cap8, repeat8))
    )
    same_clock = cap6["price_event_clock_sha256"] == cap8["price_event_clock_sha256"]
    gates = {
        "M1": reversal6 > 0 or unused6 > TOLERANCE,
        "M2": reversal6 - reversal8 >= 10 if reversal6 < 20 else reversal8 <= reversal6 * 0.75,
        "M3": unused6 > TOLERANCE and unused8 <= unused6 * 0.75,
        "M4": eligible > 0 and len(changed) / eligible >= 0.10,
        "M5": int(d8["v3"]) <= int(d6["v3"]) and int(d8["crypto"]) <= int(d6["crypto"]),
        "M6": deterministic,
        "M7": not blocked,  # verify_arm rejects every noncausal fill.
        "M8": not blocked
        and cap6["maximum_claims"] <= 6
        and cap8["maximum_claims"] <= 8
        and max(cap6["maximum_crypto_positions"], cap8["maximum_crypto_positions"]) <= 2,
    }
    result = {
        "status": "BLOCKED"
        if blocked or not same_clock or not all(gates[key] for key in ("M6", "M7", "M8"))
        else "MATERIALITY_PROVEN"
        if all(gates.values())
        else "MATERIALITY_NOT_PROVEN",
        "gates": gates,
        "candidate_global_count_denial_reduction": reversal6 - reversal8,
        "reversal_count_unused_target_exposure_reduction": unused6 - unused8,
        "changed_candidate_decision_count": len(changed),
        "eligible_candidate_decision_count": eligible,
        "arm_event_sha256": {"cap6": cap6["event_sha256"], "cap8": cap8["event_sha256"]},
        "same_price_event_clock": same_clock,
    }
    assert_no_economics(result)
    return result
