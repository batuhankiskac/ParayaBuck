"""Synthetic economic-path tests; no production state or market data."""

from copy import deepcopy

import pytest

from research.position_exit_giveback_audit_v1.diagnostics import reconstruct, summarize
from research.position_exit_giveback_audit_v1.verify import verify


def raw_fixture():
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
        dict(
            type="mark",
            timestamp="2020-01-01",
            nav=1028,
            positions={"X": dict(qty=10, avg_price=10, entry_fee=2, mark=13)},
        ),
        dict(
            type="target",
            decision_timestamp="2020-01-01",
            weights=[dict(symbol="X", weight=0.1)],
            metadata=dict(selected_symbols=["X"], retained=["X"], explanations={"X": {"alpha_rank": 4}}),
        ),
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
        dict(
            type="fill",
            symbol="X",
            timestamp="2020-01-02",
            side="buy",
            qty=5,
            fill_price=10,
            fee=1,
            decision_timestamp="2020-01-01",
        ),
        dict(
            type="mark",
            timestamp="2020-01-02",
            nav=1036,
            positions={"X": dict(qty=10, avg_price=10, entry_fee=2, mark=12)},
        ),
        dict(type="target", decision_timestamp="2020-01-02", weights=[], metadata={}),
        dict(
            type="fill",
            symbol="X",
            timestamp="2020-01-03",
            side="sell",
            qty=10,
            fill_price=9,
            fee=1,
            decision_timestamp="2020-01-02",
        ),
        dict(type="mark", timestamp="2020-01-03", nav=1005, positions={}),
    ]
    return dict(strategy="crypto", events=events, starting_equity=1000, ending_equity=1005, cash_yield=0)


def test_economic_pnl_addition_partial_sale_and_rebalance_are_one_episode():
    raw = raw_fixture()
    diag = reconstruct(raw)
    assert len(diag["episodes"]) == 1
    ep = diag["episodes"][0]
    assert ep["capital"] == 153
    assert ep["final_pnl"] == 5
    assert ep["fees"] == 5
    assert ep["mfe"] == 36
    assert ep["mae"] == 5
    assert ep["giveback"] == 31
    assert ep["retention"] == pytest.approx(5 / 36)
    assert ep["final_return"] == pytest.approx(5 / 153)
    assert ep["fill_count"] == 4
    assert ep["rebalance_count"] == 3
    assert diag["reconciliation"]["residual"] == 0
    result = verify(raw, diag)
    assert result["status"] == "PASS", result["failures"]


def test_thresholds_are_descriptive_fixed_capital_and_never_synthetic_exits():
    diag = reconstruct(raw_fixture())
    ep = diag["episodes"][0]
    assert ep["levels"]["0.2"]["first_crossing"] == "2020-01-02"
    assert ep["levels"]["0.2"]["crossing_to_exit_days"] == 1
    assert ep["levels"]["0.3"]["ever_reached"] is False
    assert ep["exit_timestamp"] == "2020-01-03"
    summary = summarize(diag["episodes"])
    assert summary["profit_levels"]["0.2"]["count"] == 1
    assert summary["profit_levels"]["0.2"]["final_mean_return"] == pytest.approx(5 / 153)
    assert summary["right_tail"]["top1"]["pnl"] == 5


def test_hysteresis_rank_observation_preserves_actual_evidence():
    ep = reconstruct(raw_fixture())["episodes"][0]
    assert ep["decisions"][0]["retained"] is True
    assert ep["decisions"][0]["explanation"]["alpha_rank"] == 4
    assert ep["exit_reason"] == "hysteresis_selection_exit"
    # Selected-only explanations cannot establish the exact rank after omission.
    assert ep["decisions"][1]["explanation"] is None


def test_horizon_censor_exclusion_and_natural_lifecycle_distinction():
    raw = raw_fixture()
    raw["events"][-2] = dict(
        type="terminal",
        event=dict(symbol="X", timestamp="2020-01-03", quantity=10, fill_price=9, fee=1, policy="FINAL_BAR"),
    )
    episodes = reconstruct(raw)["episodes"]
    assert episodes[0]["censored"] is True
    assert summarize([ep for ep in episodes if not ep["censored"]])["count"] == 0
    natural = deepcopy(raw)
    natural["events"][-2]["event"]["policy"] = "TERMINAL_BAR"
    ep = reconstruct(natural)["episodes"][0]
    assert ep["censored"] is False
    assert ep["exit_reason"] == "lifecycle_terminal"


def test_rejects_unclosed_inventory_and_oversale():
    raw = raw_fixture()
    raw["events"] = raw["events"][:-2]
    with pytest.raises(ValueError, match="Unclosed"):
        reconstruct(raw)
    raw = raw_fixture()
    raw["events"][-2]["qty"] = 11
    with pytest.raises(ValueError, match="Sell exceeds"):
        reconstruct(raw)
