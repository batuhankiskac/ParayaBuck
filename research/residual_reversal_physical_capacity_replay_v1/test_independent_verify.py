from __future__ import annotations

from copy import deepcopy

from .independent_verify import compare_arms, verify_arm


def _trace(cap: int, admitted: bool) -> list[dict[str, object]]:
    timestamp = "2026-01-02T21:00:00+00:00"
    rows: list[dict[str, object]] = [
        {
            "event": "decision",
            "market": "equity",
            "timestamp": timestamp,
            "label_time": "2026-01-02T05:00:00+00:00",
            "position_keys_before": [],
            "position_keys_after": [],
            "pending_opening_keys_before": [],
            "pending_sell_keys_before": [],
            "slot_claims_before": 0,
            "slot_claims_after": int(admitted),
            "crypto_claim_count_before": 0,
            "crypto_claim_count_after": 0,
        },
        {
            "event": "source_admission",
            "timestamp": timestamp,
            "market": "equity",
            "symbol": "ABC",
            "source": "reversal",
            "source_label": "reversal",
            "source_rank": 1,
            "existing_position": False,
            "working_opening_reservation": False,
            "pending_sell": False,
            "slot_claims_before": 0,
            "slot_claims_after": int(admitted),
            "crypto_count": 0,
            "requested_target_contribution": 0.02,
            "admitted_target_contribution": 0.02 if admitted else 0.0,
            "combined_account_target": 0.02 if admitted else 0.0,
            "overlap": False,
            "requested_opening": True,
            "admitted_opening": admitted,
            "denial_reason": None if admitted else "OTHER_EXPLICIT",
            "global_cap": cap,
        },
    ]
    if admitted:
        rows.extend(
            [
                {
                    "event": "reservation_created",
                    "market": "equity",
                    "symbol": "ABC",
                    "side": "buy",
                    "client_order_id": "o1",
                    "timestamp": timestamp,
                },
                {
                    "event": "source_order_claim",
                    "market": "equity",
                    "symbol": "ABC",
                    "side": "buy",
                    "client_order_id": "o1",
                    "timestamp": timestamp,
                    "desired_source_qty": {"reversal": 1.0},
                },
            ]
        )
    rows += [
        {"event": "account_mark", "timestamp": timestamp, "slot_claim_count": int(admitted), "position_count": 0},
        {"event": "final_state", "pending": [["equity", "o1", "ABC", "buy", 1.0]] if admitted else []},
    ]
    return rows


def _full_cap_trace(cap: int, admitted: bool) -> list[dict[str, object]]:
    created = "2026-01-02T21:00:00+00:00"
    filled = "2026-01-05T14:30:00+00:00"
    decision = "2026-01-05T21:00:00+00:00"
    symbols = [f"S{index}" for index in range(6)]
    keys = [["equity", symbol] for symbol in symbols]
    initial = dict(_trace(cap, True)[0])
    initial.update(timestamp=created, label_time="2026-01-02T05:00:00+00:00", slot_claims_after=6)
    rows: list[dict[str, object]] = [initial]
    for rank, symbol in enumerate(symbols, start=1):
        source = dict(_trace(cap, True)[1])
        source.update(
            timestamp=created,
            symbol=symbol,
            source="v3",
            source_label="v3",
            source_rank=rank,
            requested_target_contribution=0.05,
            admitted_target_contribution=0.05,
            combined_account_target=0.05,
            slot_claims_after=6,
        )
        rows.append(source)
    for symbol in symbols:
        rows += [
            {
                "event": "reservation_created",
                "market": "equity",
                "symbol": symbol,
                "side": "buy",
                "client_order_id": f"buy-{symbol}",
                "timestamp": created,
            },
            {
                "event": "source_order_claim",
                "market": "equity",
                "symbol": symbol,
                "side": "buy",
                "client_order_id": f"buy-{symbol}",
                "timestamp": created,
                "desired_source_qty": {"v3": 1.0},
            },
        ]
    rows.append({"event": "account_mark", "timestamp": created, "slot_claim_count": 6, "position_count": 0})
    for symbol in symbols:
        rows += [
            {
                "event": "fill",
                "market": "equity",
                "symbol": symbol,
                "side": "buy",
                "qty": 1.0,
                "client_order_id": f"buy-{symbol}",
                "timestamp": filled,
                "order_created_at": created,
                "source_price_event_time": filled,
            },
            {
                "event": "source_inventory",
                "market": "equity",
                "symbol": symbol,
                "cause": "fill",
                "client_order_id": f"buy-{symbol}",
                "source_quantities": {"v3": 1.0},
                "source_allocation": {"v3": 1.0},
                "desired_source_qty": {"v3": 1.0},
                "qty_delta": 1.0,
                "position_qty": 1.0,
            },
            {"event": "reservation_released", "market": "equity", "client_order_id": f"buy-{symbol}"},
        ]
    rows.append({"event": "account_mark", "timestamp": filled, "slot_claim_count": 6, "position_count": 6})
    candidate_decision = dict(_trace(cap, admitted)[0])
    candidate_decision.update(
        timestamp=decision,
        label_time="2026-01-05T05:00:00+00:00",
        position_keys_before=keys,
        position_keys_after=keys,
        slot_claims_before=6,
        slot_claims_after=6 + int(admitted),
    )
    candidate_source = dict(_trace(cap, admitted)[1])
    candidate_source.update(timestamp=decision, slot_claims_before=6, slot_claims_after=6 + int(admitted))
    if not admitted:
        candidate_source["denial_reason"] = "GLOBAL_POSITION_COUNT"
    rows.extend([candidate_decision, candidate_source])
    for symbol in symbols:
        rows.append(
            {
                "event": "source_inventory",
                "timestamp": decision,
                "market": "equity",
                "symbol": symbol,
                "cause": "decision",
                "client_order_id": None,
                "qty_delta": 0.0,
                "position_qty": 1.0,
                "source_quantities": {"v3": 1.0},
                "source_allocation": {},
                "desired_source_qty": {},
            }
        )
    if admitted:
        rows += [
            {
                "event": "reservation_created",
                "market": "equity",
                "symbol": "ABC",
                "side": "buy",
                "client_order_id": "candidate",
                "timestamp": decision,
            },
            {
                "event": "source_order_claim",
                "market": "equity",
                "symbol": "ABC",
                "side": "buy",
                "client_order_id": "candidate",
                "timestamp": decision,
                "desired_source_qty": {"reversal": 1.0},
            },
        ]
    rows += [
        {"event": "account_mark", "timestamp": decision, "slot_claim_count": 6 + int(admitted), "position_count": 6},
        {"event": "final_state", "pending": [["equity", "candidate", "ABC", "buy", 1.0]] if admitted else []},
    ]
    return rows


def test_rebuilds_source_capacity_from_raw_events() -> None:
    six = verify_arm(_full_cap_trace(6, False), cap=6)
    eight = verify_arm(_full_cap_trace(8, True), cap=8)
    assert six["status"] == eight["status"] == "PASS"
    assert six["global_count_denials"]["reversal"] == 1
    assert eight["global_count_denials"]["reversal"] == 0
    assert six["reversal_count_unused_target_exposure"] == 0.02
    assert compare_arms(six, eight, repeat6=deepcopy(six), repeat8=deepcopy(eight))["gates"]["M4"]


def test_count_denials_require_observed_capacity_pressure() -> None:
    for reason in ("GLOBAL_POSITION_COUNT", "PENDING_OPENING_RESERVATION"):
        forged = _trace(6, False)
        forged[1]["denial_reason"] = reason
        assert "global-count denial without a full slot ledger" in verify_arm(forged, cap=6)["errors"][0]

    forged_sell = _trace(6, False)
    forged_sell[1]["denial_reason"] = "PENDING_SELL_SLOT_NOT_RELEASED"
    assert "pending-SELL denial without a working SELL" in verify_arm(forged_sell, cap=6)["errors"][0]

    six_claims_under_eight = _full_cap_trace(8, False)
    assert "global-count denial without a full slot ledger" in verify_arm(six_claims_under_eight, cap=8)["errors"][0]

    crypto = _trace(6, False)
    crypto[0]["market"] = "crypto"
    crypto[1].update(market="crypto", source="crypto", source_label="crypto", denial_reason="CRYPTO_POSITION_COUNT")
    assert "crypto-count denial without two crypto claims" in verify_arm(crypto, cap=6)["errors"][0]


def test_below_cap_pending_sell_gate_is_not_count_induced() -> None:
    rows = _full_cap_trace(8, False)
    decision = next(
        row for row in rows if row["event"] == "decision" and row["timestamp"] == "2026-01-05T21:00:00+00:00"
    )
    source = next(row for row in rows if row["event"] == "source_admission" and row["source"] == "reversal")
    decision["pending_sell_keys_before"] = [["equity", "S0"]]
    source["pending_sell"] = True
    source["denial_reason"] = "PENDING_SELL_SLOT_NOT_RELEASED"
    reservation = {
        "event": "reservation_created",
        "market": "equity",
        "symbol": "S0",
        "side": "sell",
        "client_order_id": "sell-S0",
        "timestamp": "2026-01-05T21:00:00+00:00",
    }
    claim = {
        "event": "source_order_claim",
        "market": "equity",
        "symbol": "S0",
        "side": "sell",
        "client_order_id": "sell-S0",
        "timestamp": "2026-01-05T21:00:00+00:00",
        "desired_source_qty": {},
    }
    rows[rows.index(decision) : rows.index(decision)] = [reservation, claim]
    rows[-1]["pending"] = [["equity", "sell-S0", "S0", "sell", 1.0]]
    receipt = verify_arm(rows, cap=8)
    assert receipt["status"] == "PASS", receipt["errors"]
    assert receipt["reversal_count_unused_target_exposure"] == 0.0


def test_admitted_opening_needs_buy_reservation_in_same_cycle() -> None:
    rows = _trace(8, True)
    rows = [row for row in rows if row["event"] not in {"reservation_created", "source_order_claim"}]
    rows[-2]["slot_claim_count"] = 0
    rows[-1]["pending"] = []
    assert "admitted opening has no same-cycle BUY reservation" in verify_arm(rows, cap=8)["errors"][0]


def test_rejects_noncausal_fill_and_mismatched_claims() -> None:
    trace = _trace(6, False)
    trace.insert(
        0,
        {
            "event": "fill",
            "market": "equity",
            "symbol": "XYZ",
            "side": "buy",
            "qty": 1,
            "timestamp": "2026-01-02T14:30:00+00:00",
            "order_created_at": "2026-01-02T14:30:00+00:00",
            "source_price_event_time": "2026-01-02T14:30:00+00:00",
        },
    )
    assert verify_arm(trace, cap=6)["status"] == "FAIL"
    bad_claim = _trace(6, False)
    bad_claim[-2]["slot_claim_count"] = 1
    assert verify_arm(bad_claim, cap=6)["status"] == "FAIL"


def test_rejects_source_budget_and_economic_leakage() -> None:
    trace = _trace(8, True)
    trace[1]["admitted_target_contribution"] = 0.15
    assert verify_arm(trace, cap=8)["status"] == "FAIL"
    trace = _trace(8, True)
    trace[1]["pnl"] = 123
    try:
        verify_arm(trace, cap=8)
    except AssertionError:
        pass
    else:
        raise AssertionError("Economic field was accepted")


def test_reconstructs_fill_linked_source_inventory() -> None:
    trace = _trace(8, True)[:2]
    trace.extend(
        [
            {
                "event": "reservation_created",
                "market": "equity",
                "symbol": "ABC",
                "side": "buy",
                "client_order_id": "o1",
                "timestamp": "2026-01-02T21:00:00+00:00",
            },
            {
                "event": "source_order_claim",
                "market": "equity",
                "symbol": "ABC",
                "side": "buy",
                "client_order_id": "o1",
                "timestamp": "2026-01-02T21:00:00+00:00",
                "desired_source_qty": {"reversal": 1.0},
            },
            {
                "event": "account_mark",
                "timestamp": "2026-01-02T21:00:00+00:00",
                "slot_claim_count": 1,
                "position_count": 0,
            },
            {
                "event": "fill",
                "market": "equity",
                "symbol": "ABC",
                "side": "buy",
                "qty": 1.0,
                "client_order_id": "o1",
                "timestamp": "2026-01-05T14:30:00+00:00",
                "order_created_at": "2026-01-02T21:00:00+00:00",
                "source_price_event_time": "2026-01-05T14:30:00+00:00",
            },
            {
                "event": "source_inventory",
                "market": "equity",
                "symbol": "ABC",
                "cause": "fill",
                "client_order_id": "o1",
                "source_quantities": {"reversal": 1.0},
                "source_allocation": {"reversal": 1.0},
                "desired_source_qty": {"reversal": 1.0},
                "qty_delta": 1.0,
                "position_qty": 1.0,
            },
            {"event": "reservation_released", "market": "equity", "client_order_id": "o1"},
            {
                "event": "account_mark",
                "timestamp": "2026-01-05T14:30:00+00:00",
                "slot_claim_count": 1,
                "position_count": 1,
            },
            {"event": "final_state", "pending": []},
        ]
    )
    assert verify_arm(trace, cap=8)["status"] == "PASS"
    trace[6]["source_quantities"] = {"v3": 1.0}
    assert verify_arm(trace, cap=8)["status"] == "FAIL"
