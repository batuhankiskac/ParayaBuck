"""Deterministic, non-economic checks for the residual-reversal forensic audit."""

from __future__ import annotations

from .forensic_audit import _synthetic_overlay_probe, build_audit


def test_forensic_verdict_is_implementation_invalid() -> None:
    audit = build_audit()
    assert audit["forensic_verdict"] == "EXPERIMENT_INVALID_IMPLEMENTATION"
    assert audit["economic_rerun_performed"] is False


def test_no_normal_validation_decisions_are_present() -> None:
    audit = build_audit()
    finding = audit["finding_1_validation_coverage"]
    assert finding["normal_scheduled_decision_after_validation_start"] is False
    for run in audit["finding_1_validation_coverage"]["coverage"].values():
        assert run["validation"]["recorded_decisions"] == 0
        assert run["validation"]["fills"] == 0


def test_terminal_lifecycle_wedges_are_reconstructable() -> None:
    audit = build_audit()
    findings = audit["finding_2_terminal_lifecycle"]["reconstruction"]["findings"]
    stale = {row["strategy"]: {item["symbol"] for item in row["stale_managed_symbols"]} for row in findings}
    assert stale["v3"] == {"ESRX", "DVMT"}
    assert stale["treatment"] == {"ESRX", "DVMT"}
    assert stale["reversal"] == {"KSU"}


def test_synthetic_overlay_drops_reversal_under_unranked_cap() -> None:
    probe = _synthetic_overlay_probe()
    assert probe["merged_count"] == 14
    assert probe["merged_ranks"] == [0]
    assert len(probe["post_cap_names"]) == 6
    assert probe["effective_v3_exposure"] > 0
    assert probe["effective_reversal_exposure"] == 0
    assert "position_count_limit" in probe["overlay_reasons"]


def test_observation_identity_is_v3_for_all_persisted_snapshots() -> None:
    audit = build_audit()
    observation = audit["finding_4_observation_sets"]
    assert observation["v3_observation_set"]["strategy_names"] == ["equity_residual_momentum_v3"]
    assert observation["reversal_observation_set_actual"]["own_alpha_ranker_used"] is False
    assert observation["treatment_observation_set_actual"]["predeclared_union_policy_used"] is False


def test_verifier_gap_list_contains_coverage_lifecycle_and_identity() -> None:
    audit = build_audit()
    names = {item["invariant"] for item in audit["finding_5_verifier_gaps"]["important_invariants_not_checked"]}
    assert "minimum or expected weekly opportunity count" in names
    assert "terminal lifecycle correctness" in names
    assert "strategy-specific observation-set identity" in names
