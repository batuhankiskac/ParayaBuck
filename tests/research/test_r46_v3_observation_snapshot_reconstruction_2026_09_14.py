from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

from scripts.research import reconstruct_r46_v3_observation_snapshot_2026_09_14 as reconstruction


def ts(value: str) -> datetime:
    return reconstruction.aware(value)


def snapshot(
    effective_at: str,
    *,
    symbols: tuple[str, ...] = ("SPY", "AAA", "BBB"),
    observation_set: tuple[str, ...] = ("SPY", "AAA", "BBB"),
    capacity: int = 3,
    pinned: tuple[str, ...] = ("SPY",),
) -> reconstruction.StructuralSnapshot:
    return reconstruction.StructuralSnapshot(
        effective_at=ts(effective_at),
        symbols=symbols,
        observation_set=observation_set,
        maximum_stream_symbols=capacity,
        pinned_symbols=pinned,
        observation_strategy_name="equity_residual_momentum_v3",
        observation_strategy_version="3.0.0",
        scanner_version="scanner",
        config_digest="config",
        candidate_count=len(symbols),
    )


def test_effective_snapshot_uses_latest_at_or_before_semantics() -> None:
    snapshots = (
        snapshot("2017-02-06T05:00:00Z"),
        snapshot("2017-02-07T05:00:00Z", observation_set=("SPY", "BBB", "AAA")),
    )

    result = reconstruction.latest_snapshot_at_or_before(snapshots, ts("2017-02-06T14:30:00Z"))

    assert result == snapshots[0]


def test_ordered_observation_digest_is_deterministic_and_order_sensitive() -> None:
    first = reconstruction.ordered_observation_digest(("SPY", "AAA", "BBB"))
    repeat = reconstruction.ordered_observation_digest(("SPY", "AAA", "BBB"))
    reordered = reconstruction.ordered_observation_digest(("SPY", "BBB", "AAA"))

    assert first == repeat
    assert first != reordered


def test_completeness_pass_fixture() -> None:
    decision = ts("2017-02-06T05:00:00Z")
    history = {symbol: (ts("2017-02-03T05:00:00Z"), decision) for symbol in ("SPY", "AAA")}

    result = reconstruction.completeness_record(("SPY", "AAA"), history, decision)

    assert result["result"] == "PASS"
    assert result["complete_symbols"] == ["SPY", "AAA"]
    assert result["missing_symbols"] == []
    assert result["stale_symbols"] == []


def test_completeness_missing_symbol_fails() -> None:
    decision = ts("2017-02-06T05:00:00Z")
    history = {"SPY": (decision,)}

    result = reconstruction.completeness_record(("SPY", "AAA"), history, decision)

    assert result["result"] == "FAIL"
    assert result["missing_symbols"] == ["AAA"]
    assert result["stale_symbols"] == []


def test_completeness_stale_symbol_is_not_missing() -> None:
    decision = ts("2017-02-06T05:00:00Z")
    history = {"SPY": (ts("2017-02-03T05:00:00Z"),)}

    result = reconstruction.completeness_record(("SPY",), history, decision)

    assert result["result"] == "FAIL"
    assert result["missing_symbols"] == []
    assert result["stale_symbols"] == ["SPY"]


def test_terminal_filtering_removes_inactive_observation_member() -> None:
    decision = ts("2017-02-06T05:00:00Z")
    result = reconstruction.effective_observation_symbols(
        snapshot("2017-02-06T05:00:00Z"),
        decision,
        {"AAA": {"last_bar": "2017-02-03T05:00:00Z"}},
    )

    assert result == ("SPY", "BBB")


def test_managed_and_pending_pins_are_reserved_first() -> None:
    result = reconstruction.effective_observation_symbols(
        snapshot("2017-02-06T05:00:00Z", capacity=4, observation_set=("SPY", "AAA", "BBB", "CCC")),
        ts("2017-02-06T05:00:00Z"),
        managed_symbols=("MANAGED",),
        pending_symbols=("PENDING",),
    )

    assert result[:3] == ("SPY", "MANAGED", "PENDING")
    assert len(result) == 4


def test_two_build_identity_comparison_requires_byte_identity() -> None:
    identity = {
        "full_universe_hash": "a" * 64,
        "snapshot_count": 2,
        "feb06_snapshot_digest": "b" * 64,
        "feb10_snapshot_digest": "c" * 64,
        "observation_set_digest": "d" * 64,
    }
    first = {"python_hash_seed": 1, "head": "h", "wrapper": {"identity": identity}}
    second = {"python_hash_seed": 777, "head": "h", "wrapper": {"identity": dict(identity)}}

    result = reconstruction.compare_process_identities(first, second)

    assert result["identities_byte_identical"] is True
    assert result["process_count"] == 2


def test_contradiction_classifier_detects_missing_execution_state() -> None:
    result = reconstruction.classify_control_flow_consistency(True, True)

    assert result["status"] == "DETERMINISTIC_RECONSTRUCTION_CONTRADICTS_OBSERVED_EXECUTION"
    assert result["execution_state_input_missing"] is True


def test_structural_snapshot_emits_no_economic_fields() -> None:
    record = reconstruction.snapshot_structural_dict(snapshot("2017-02-06T05:00:00Z"))

    assert reconstruction.has_forbidden_economic_fields(record) is False
    assert "observation_set" in record
    assert "score_breakdown" not in record


def test_diagnostic_ast_has_no_engine_run_or_strategy_decide_calls() -> None:
    source_path = Path(reconstruction.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    assert not any(isinstance(node.func, ast.Attribute) and node.func.attr in {"run_dataset", "decide"} for node in calls)
    assert not any(isinstance(node, ast.Name) and node.id == "PortfolioBacktestEngine" for node in ast.walk(tree))
