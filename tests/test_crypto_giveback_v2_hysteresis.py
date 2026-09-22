from research.crypto_exit_giveback_audit_v2.hysteresis import analyze


def decision(day, rank, selected, held=None):
    stamp = f"2024-01-{day:02d}T00:00:00+00:00"
    others = [f"B{i}" for i in range(5)]
    symbols = others[: rank - 1] + ["A"] + others[rank - 1 :]
    return {
        "timestamp": stamp,
        "scores": [[i, s] for i, s in enumerate(symbols)],
        "eligible_symbols": symbols,
        "holdings": held or {"A": 1},
        "alpha_target": {"metadata": {"selected_symbols": selected, "retained": selected}},
        "target": {"weights": [{"symbol": s, "weight": 0.025} for s in selected]},
    }


def episode():
    return {
        "symbol": "A",
        "entry_timestamp": "2024-01-02T00:00:00+00:00",
        "exit_timestamp": "2024-01-14T00:00:00+00:00",
        "mfe_timestamp": "2024-01-06T00:00:00+00:00",
        "censored": False,
        "fills": [
            {"side": "buy", "decision_timestamp": "2024-01-01T00:00:00+00:00"},
            {"side": "sell", "decision_timestamp": "2024-01-13T00:00:00+00:00"},
        ],
    }


def test_partial_liquidation_rank_three_capacity_is_not_rank_failure():
    raw = {
        "decisions": [
            decision(1, 1, ["A"]),
            decision(5, 3, ["A", "B0"]),
            decision(9, 5, ["B0", "B1"]),
            decision(13, 3, ["B0", "B1"], {"A": 0.1, "B0": 1, "B1": 1}),
        ]
    }
    result = analyze(episode(), raw)
    assert result["exit_reason"] == "RETENTION_CAPACITY_DISPLACEMENT"
    assert result["exit_initiating_decision"]["reason"] == "RANK_ABOVE_RETENTION"
    assert result["first_rank_failure"]["timestamp"].startswith("2024-01-09")
    assert result["mfe_rank_asof"]["rank"] == 3
    assert result["mfe_rank_observation_age_hours"] == 24
    assert not result["mfe_rank_is_contemporaneous_decision"]
    assert result["hours_mfe_to_next_actual_decision"] == 72
    assert result["holding_age_expiry_timestamp"] is None


def test_cancelled_zero_target_does_not_initiate_final_exit():
    raw = {"decisions": [decision(1, 1, ["A"]), decision(5, 5, []), decision(9, 3, ["A"]), decision(13, 5, [])]}
    result = analyze(episode(), raw)
    assert result["first_zero_target"]["timestamp"].startswith("2024-01-05")
    assert result["exit_initiating_decision"]["timestamp"].startswith("2024-01-13")
    assert result["retained_only_after_peak"]


def test_terminal_not_natural_exit_and_boundary_is_epoch_aligned():
    ep = episode()
    ep["censored"] = True
    ep["fills"].append({"type": "terminal", "side": "sell"})
    raw = {"decisions": [decision(1, 1, ["A"]), decision(5, 1, ["A"]), decision(9, 1, ["A"])]}
    result = analyze(ep, raw)
    assert result["exit_reason"] == "TERMINAL_CENSORED"
    from datetime import datetime

    boundary = datetime.fromisoformat(result["next_epoch_96h_boundary_after_mfe"])
    assert boundary.timestamp() % (96 * 3600) == 0
    assert 0 < result["hours_mfe_to_next_epoch_boundary"] <= 96
