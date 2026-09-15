from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.research import h06_final_economic_2026_09_15 as h06


def _sessions(count: int = 100) -> list[str]:
    return [f"2026-01-{index + 1:02d}" for index in range(count)]


def test_top_decile_uses_ceil_and_deterministic_tie_break() -> None:
    rows = [{"score": 1, "issuer_cik": f"{index:010d}", "symbol": f"S{index:02d}"} for index in range(11, 0, -1)]
    selected = h06.top_decile(rows)
    assert len(selected) == 2
    assert [row["issuer_cik"] for row in selected] == ["0000000001", "0000000002"]


def test_top_decile_empty_stays_cash() -> None:
    assert h06.top_decile([]) == []


def test_score_deduplicates_joint_owner_transaction() -> None:
    base = {"economic_transaction_id": "tx", "final_admitted": True, "issuer_cik": "1", "issuer_symbol": "A",
            "information_session": "2026-01-02", "shares": "2", "price": "5", "decision_session": "2026-01-03", "market_cap": "100"}
    structural = {"economic_input_rows": [{**base, "reporting_owner_cik": "10"}, {**base, "reporting_owner_cik": "11"}]}
    decisions = [{"decision_session": "2026-01-03", "decision_timestamp": "2026-01-03T21:00:00Z", "prior_21_sessions": ["2026-01-02"]}]
    result = h06.score_selections(structural, decisions, ["2026-01-02", "2026-01-03"])
    assert result[0]["selected"][0]["purchase_value"] == 10


def test_placebo_shifts_information_session_by_seven_supported_sessions() -> None:
    sessions = [f"2026-01-{day:02d}" for day in range(1, 16)]
    structural = {"economic_input_rows": [{"economic_transaction_id": "tx", "final_admitted": True, "issuer_cik": "1",
        "issuer_symbol": "A", "information_session": sessions[0], "shares": "1", "price": "1",
        "decision_session": sessions[8], "market_cap": "10"}]}
    decisions = [{"decision_session": sessions[8], "decision_timestamp": sessions[8] + "T21:00:00Z", "prior_21_sessions": [sessions[7]]}]
    assert not h06.score_selections(structural, decisions, sessions)[0]["selected"]
    assert h06.score_selections(structural, decisions, sessions, shift=7)[0]["selected"]


def test_holding_expires_after_63_completed_sessions() -> None:
    sessions = [f"S{index:03d}" for index in range(70)]
    selections = [{"decision_session": sessions[0], "selected": [{"symbol": "A", "score": 1, "issuer_cik": "1"}]}]
    events = h06.holding_events(selections, sessions)
    assert events[sessions[1]]["active"] == ["A"]
    assert events[sessions[64]]["active"] == []


def test_reselection_extends_expiry_without_duplicate_leverage() -> None:
    sessions = [f"S{index:03d}" for index in range(100)]
    selected = [{"symbol": "A", "score": 1, "issuer_cik": "1"}]
    events = h06.holding_events([{"decision_session": sessions[0], "selected": selected}, {"decision_session": sessions[20], "selected": selected}], sessions)
    assert sessions[64] not in events
    assert events[sessions[84]]["active"] == []


def test_standalone_weights_obey_55_and_ten_percent_caps() -> None:
    one = h06.standalone_targets({"D": {"active": ["A"], "latest": {}}})["D"]
    many = h06.standalone_targets({"D": {"active": list("ABCDEFG"), "latest": {}}})["D"]
    assert one == {"A": 0.10}
    assert sum(many.values()) == pytest.approx(0.55)
    assert max(many.values()) <= 0.10


def test_overlay_capacity_overlap_and_missing_v3() -> None:
    events = {"2026-01-03": {"decision_session": "2026-01-02", "active": ["A", "Z"], "latest": {
        "A": {"symbol": "A", "score": 2, "issuer_cik": "1"}, "Z": {"symbol": "Z", "score": 1, "issuer_cik": "2"}}}}
    history = [{"timestamp": "2026-01-02T21:00:00Z", "weights": {letter: 0.09 for letter in "ABCDEF"}}]
    targets, audit = h06.overlay_targets(events, history, "2026-12-31")
    assert "A" in targets["2026-01-03"] and "Z" not in targets["2026-01-03"]
    assert audit["capacity_blocked_dates"] == ["2026-01-02"]
    assert audit["overlap_dates"] == ["2026-01-02"]
    targets, audit = h06.overlay_targets(events, [], "2026-12-31")
    assert targets["2026-01-03"] == {} and audit["candidate_cash_dates"] == ["2026-01-02"]


def test_simulator_next_event_open_cost_multiplier_and_continuity() -> None:
    sessions = ["2026-01-02", "2026-01-03"]
    bars = {"A": {session: {"open": 10.0, "close": 10.0} for session in sessions}}
    daily1, trades1 = h06.simulate({sessions[0]: {"A": 0.1}}, bars, sessions, {}, cost_multiplier=1, portfolio="x")
    daily3, trades3 = h06.simulate({sessions[0]: {"A": 0.1}}, bars, sessions, {}, cost_multiplier=3, portfolio="x")
    assert trades1[0]["session"] == sessions[0]
    assert trades3[0]["fill_price"] > trades1[0]["fill_price"]
    assert daily1[1]["nav"] == pytest.approx(daily1[0]["nav"])


def test_continuous_window_slice_does_not_force_close() -> None:
    daily = [{"session": "2025-12-31", "nav": 100.0, "return": 0.0, "exposure": 0.1},
             {"session": "2026-01-02", "nav": 101.0, "return": 0.01, "exposure": 0.1}]
    metric = h06.window_metrics(daily, [], h06.date(2026, 1, 1), h06.date(2026, 7, 31), signal_count=0)
    assert metric["starting_nav"] == pytest.approx(100.0)
    assert metric["active_sessions"] == 1


def test_bootstrap_and_permutation_are_deterministic() -> None:
    values = [0.001 * ((index % 5) - 2) for index in range(42)]
    assert h06.moving_block_bootstrap(values, draws=100) == h06.moving_block_bootstrap(values, draws=100)
    assert h06.paired_sign_permutation(values, draws=100) == h06.paired_sign_permutation(values, draws=100)


def test_sealed_output_schema_detects_mutation() -> None:
    value = h06.seal({"schema": "synthetic", "integrity": {}})
    h06.verify_seal(value)
    value["schema"] = "changed"
    with pytest.raises(ValueError, match="self_hash_mismatch"):
        h06.verify_seal(value)


def test_atomic_one_shot_write_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "reserved.json"
    h06.exclusive_json(path, {"status": "RESERVED"})
    with pytest.raises(FileExistsError):
        h06.exclusive_json(path, {"status": "RESERVED"})


def test_runner_has_no_broker_submission_or_promotion_write() -> None:
    source = Path(h06.__file__).read_text(encoding="utf-8")
    forbidden = ("submit_order", "TradingClient", "portfolio_holdout_registry", "config/settings.yaml", "promotion_authorized\": true")
    assert not any(token in source for token in forbidden)


def test_protocol_declares_no_rerun_or_tuning_when_present() -> None:
    if h06.PROTOCOL.exists():
        protocol = json.loads(h06.PROTOCOL.read_text(encoding="utf-8"))
        assert protocol["rerun_allowed"] is False
        assert protocol["tuning_allowed"] is False
