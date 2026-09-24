from target_demand_diagnostic import arbitrate


def test_overlap_keeps_both_source_contributions_in_one_slot() -> None:
    components = {
        "A": {"v3": 0.2, "reversal": 0.1},
        "B": {"v3": 0.2, "reversal": 0.0},
        "C": {"v3": 0.0, "reversal": 0.1},
    }
    ranks = {"v3": ["A", "B"], "reversal": ["A", "C"]}
    result = arbitrate(components, ranks, ["BTC/USD", "ETH/USD"], 3)
    assert result["slots"] == 3
    assert result["selected_equity"] == ["A"]
    assert result["accepted"] == {"v3": 0.2, "reversal": 0.1, "crypto": 0.05}
    assert result["denials"] == {"v3": 1, "reversal": 1, "crypto": 0}
