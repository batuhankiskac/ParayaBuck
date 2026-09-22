"""Small synthetic invariants for the independent observational verifier."""

from copy import deepcopy

import pytest

from research.position_exit_giveback_audit_v1.verify import verify


def fixture():
    # 10 bought at 10 plus fee 2; half sold at 14 minus fee 1.
    # Remaining shares split 2:1 and receive a dividend, then close at 4.
    events = [
        dict(
            type="fill",
            symbol="X",
            timestamp="2020-01-01",
            side="buy",
            qty=10,
            fill_price=10,
            fee=2,
            decision_timestamp="2019-12-31",
        ),
        dict(type="mark", timestamp="2020-01-01", positions={"X": dict(qty=10, avg_price=10, entry_fee=2, mark=13)}),
        dict(
            type="fill",
            symbol="X",
            timestamp="2020-01-02",
            side="sell",
            qty=5,
            fill_price=14,
            fee=1,
            decision_timestamp="2020-01-01",
        ),
        dict(type="action", symbol="X", action_type="split", quantity_before=5, value=2),
        dict(type="action", symbol="X", action_type="dividend", quantity_before=10, value=0.2),
        dict(type="mark", timestamp="2020-01-02", positions={"X": dict(qty=10, avg_price=5, entry_fee=1, mark=6)}),
        dict(
            type="terminal",
            event=dict(symbol="X", timestamp="2020-01-03", quantity=10, fill_price=4, fee=1, policy="FINAL_BAR"),
        ),
    ]
    ep = dict(
        episode_id="equity:X:1",
        symbol="X",
        entry_timestamp="2020-01-01",
        exit_timestamp="2020-01-03",
        censored=True,
        capital=102,
        exit_reason="horizon_censored",
        path=[
            dict(timestamp="2020-01-01", pnl=28, realized=0, unrealized=28, qty=10, mark=13),
            dict(timestamp="2020-01-02", pnl=29, realized=20, unrealized=9, qty=10, mark=6),
            dict(timestamp="2020-01-03", pnl=8, realized=8, unrealized=0, qty=0, mark=4),
        ],
        final_pnl=8,
        mfe=29,
        mae=8,
        mfe_timestamp="2020-01-02",
        mae_timestamp="2020-01-03",
        giveback=21,
        retention=8 / 29,
        final_return=8 / 102,
        mfe_return=29 / 102,
        mae_return=8 / 102,
    )
    return dict(events=events), dict(strategy="equity", episodes=[ep])


def test_partial_sales_split_dividend_and_censored_close():
    raw, diag = fixture()
    result = verify(raw, diag)
    assert result["status"] == "PASS", result["failures"]
    assert result["episodes_checked"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("mfe", 30),
        ("mfe_timestamp", "2020-01-01"),
        ("retention", 0.9),
        ("giveback", 10),
        ("exit_reason", "lifecycle_terminal"),
        ("capital", 100),
    ],
)
def test_rejects_corrupted_persisted_metrics(field, value):
    raw, diag = fixture()
    diag["episodes"][0][field] = value
    assert verify(raw, diag)["status"] == "FAIL"


def test_rejects_missing_episode_and_inventory_drift():
    raw, diag = fixture()
    broken = deepcopy(raw)
    broken["events"][1]["positions"]["X"]["qty"] = 9
    assert verify(broken, diag)["status"] == "FAIL"
    diag["episodes"] = []
    assert verify(raw, diag)["status"] == "FAIL"


def test_does_not_double_charge_embedded_spread_slippage():
    raw, diag = fixture()
    raw["events"][0].update(slippage_cost=5, spread_cost=2)
    assert verify(raw, diag)["status"] == "PASS"


def test_addition_preserves_episode_and_reopen_starts_new_identity():
    raw, diag = fixture()
    # Replace the opening fill by two fills with identical aggregate cost.
    first = raw["events"].pop(0)
    first.update(qty=4, fee=0.8)
    second = {**first, "qty": 6, "fee": 1.2}
    raw["events"][0:0] = [first, second]
    assert verify(raw, diag)["status"] == "PASS"
    # A separate later flat-to-flat lifetime must receive a new ID.
    more_raw, more_diag = fixture()
    for event in more_raw["events"]:
        row = event["event"] if event["type"] == "terminal" else event
        if "timestamp" in row:
            row["timestamp"] = row["timestamp"].replace("2020-01", "2020-02")
    ep = more_diag["episodes"][0]
    ep["episode_id"] = "equity:X:2"
    for field in ("entry_timestamp", "exit_timestamp", "mfe_timestamp", "mae_timestamp"):
        ep[field] = ep[field].replace("2020-01", "2020-02")
    for point in ep["path"]:
        point["timestamp"] = point["timestamp"].replace("2020-01", "2020-02")
    raw["events"].extend(more_raw["events"])
    diag["episodes"].append(ep)
    assert verify(raw, diag)["status"] == "PASS"


@pytest.mark.parametrize(
    "strategy,metadata,weights,reason",
    [
        ("equity", {}, [], "alpha_no_longer_selected"),
        ("crypto", {}, [], "hysteresis_selection_exit"),
        ("crypto", {"selected_symbols": ["X"]}, [], "risk_overlay"),
        ("equity", {"risk_off_detail": "gate"}, [], "eligibility_gate_failure"),
        ("equity", {}, [{"symbol": "X", "weight": 0.1}], "rebalance_sizing"),
    ],
)
def test_exit_reason_reconstructed_from_decision_evidence(strategy, metadata, weights, reason):
    raw, diag = fixture()
    close = raw["events"].pop()["event"]
    raw["events"].append(dict(type="target", timestamp="2020-01-02", metadata=metadata, weights=weights))
    raw["events"].append(
        dict(
            type="fill",
            symbol="X",
            timestamp=close["timestamp"],
            side="sell",
            qty=close["quantity"],
            fill_price=close["fill_price"],
            fee=close["fee"],
            decision_timestamp="2020-01-02",
        )
    )
    diag["strategy"] = strategy
    ep = diag["episodes"][0]
    ep.update(episode_id=f"{strategy}:X:1", censored=False, exit_reason=reason)
    assert verify(raw, diag)["status"] == "PASS"
    ep["exit_reason"] = "made_up_reason"
    assert verify(raw, diag)["status"] == "FAIL"


def test_independent_account_nav_and_trade_realizations_exclude_dividends():
    raw, diag = fixture()
    raw.update(
        starting_equity=1000,
        ending_equity=1008,
        dividend_total=2,
        realized_pnl_events=[["2020-01-02", "X", 18], ["2020-01-03", "X", -12]],
        closed_position_events=[["2020-01-03", "X", 6]],
    )
    raw["events"][1]["nav"] = 1028
    raw["events"][5]["nav"] = 1029
    report = verify(raw, diag)
    assert report["status"] == "PASS", report["failures"]
    assert report["nav_marks_checked"] == 2
    assert report["realized_events_checked"] == 2
    raw["events"][5]["nav"] += 1
    assert verify(raw, diag)["status"] == "FAIL"
    raw["events"][5]["nav"] -= 1
    raw["closed_position_events"][0][2] += 2
    assert verify(raw, diag)["status"] == "FAIL"


def test_entity_cannot_change_during_continuous_episode():
    raw, diag = fixture()
    raw["entities"] = [dict(symbol="X", entity_id="one", valid_from="2019-01-01", valid_to=None)]
    diag["episodes"][0]["entity_id"] = "one"
    assert verify(raw, diag)["status"] == "PASS"
    raw["entities"][0]["valid_to"] = "2020-01-02"
    raw["entities"].append(dict(symbol="X", entity_id="two", valid_from="2020-01-03", valid_to=None))
    result = verify(raw, diag)
    assert result["status"] == "FAIL"
    assert any("continuous entity lifetime" in f for f in result["failures"])
