import json
from pathlib import Path

import pytest

from scripts.research import crossasset_max_testing_verdict_matrix_2026_09_09 as matrix


def test_matrix_has_all_cards_and_no_best_arm() -> None:
    value = json.loads(matrix.MATRIX.read_text(encoding="utf-8"))
    assert set(value["cards"]) == set(matrix.CARDS)
    assert value["counts"] == {"cards": 6, "measured_cards": 3, "blocked_cards": 3, "branches": 21, "arms": 162, "admitted_rows": 0, "economic_eligible": 0, "integration_candidate": 0}
    assert all(item["no_best_arm_cherry_pick"] for item in value["cards"].values())
    assert all(len(item["arms"]) == 9 for item in value["branches"] if item["card"] in matrix.MEASURED)


def test_matrix_verifier_and_mutation(tmp_path: Path) -> None:
    for name in (matrix.MATRIX.name, matrix.REPORT.name, matrix.MANIFEST.name, matrix.RECEIPT.name):
        (tmp_path / name).write_bytes((matrix.OUT / name).read_bytes())
    matrix.verify(tmp_path)
    value = json.loads((tmp_path / matrix.MATRIX.name).read_text(encoding="utf-8"))
    value["cards"]["X05"]["research_verdict"] = "passed"
    (tmp_path / matrix.MATRIX.name).write_bytes(matrix.canonical(value))
    with pytest.raises(ValueError, match="matrix_hash_mismatch|manifest_artifact_mismatch"):
        matrix.verify(tmp_path)


def test_protocol_formulae_and_numeric_no_admission() -> None:
    value = json.loads(matrix.MATRIX.read_text(encoding="utf-8"))
    assert value["cards"]["X01"]["formula_and_direction"]["formula"] == "(10y-2y)/max(abs(2y),1e-9); negative selects TLT, positive SHY, zero IEF"
    assert "adjusted-close" in value["cards"]["X01"]["formula_and_direction"]["caveat"]
    for card in matrix.CARDS:
        assert value["cards"][card]["admitted_rows"] == 0
        assert value["cards"][card]["economic_eligible"] is False


def test_blocker_decisions_preserve_card_specific_gate_reason() -> None:
    value = json.loads(matrix.MATRIX.read_text(encoding="utf-8"))
    assert {card: value["cards"][card]["economic_decision"] for card in matrix.CARDS} == {
        "X01": "NOT_ELIGIBLE_MISSING_PIT_AVAILABILITY",
        "X02": "NOT_ELIGIBLE_MISSING_DATED_CONTRACT_AND_EXECUTION_FIELDS",
        "X03": "NOT_ELIGIBLE_MISSING_PIT_VALUATION_AND_MEMBERSHIP_HISTORY",
        "X04": "NOT_ELIGIBLE_MISSING_BORROW_HISTORY_AND_LONG_SHORT_CAPABILITY",
        "X05": "NOT_ELIGIBLE_MISSING_PIT_AVAILABILITY_AND_TWO_LEG_EXECUTION_FIELDS",
        "X06": "NOT_ELIGIBLE_MISSING_PIT_AVAILABILITY_AND_TWO_LEG_EXECUTION_FIELDS",
    }


def test_current_verifier_is_read_only() -> None:
    before = {name: (matrix.OUT / name).stat().st_mtime_ns for name in (matrix.MATRIX.name, matrix.REPORT.name, matrix.MANIFEST.name, matrix.RECEIPT.name)}
    assert matrix.verify()["read_only"] is True
    after = {name: (matrix.OUT / name).stat().st_mtime_ns for name in before}
    assert before == after
