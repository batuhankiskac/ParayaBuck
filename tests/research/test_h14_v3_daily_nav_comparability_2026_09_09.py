from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.research.h14_v3_daily_nav_comparability_2026_09_09 import (
    H14V3ComparabilityError,
    _max_drawdown,
    _pearson,
    _returns,
    digest,
    validate_control_receipt,
)


def test_current_control_receipt_is_aggregate_only_and_self_verified() -> None:
    receipt = validate_control_receipt()
    assert receipt["components"]["equity_curve_sha256"] == "9d08f9c3b3a6959189fb0a482c7de6d294fd4a72e1cec3ae7fe7f79d6f2e02fb"
    assert "curve_timestamps" not in receipt
    assert "equity_curve" not in receipt


def test_receipt_mutation_fails_closed(tmp_path: Path) -> None:
    original = Path("data/research/equity_v3_current_parity_control_2026_08/control_r2.json")
    value = json.loads(original.read_text())
    value["components"]["equity_curve_sha256"] = "0" * 64
    mutated = tmp_path / "control.json"
    mutated.write_text(json.dumps(value))
    with pytest.raises(H14V3ComparabilityError, match="digest mismatch"):
        validate_control_receipt(mutated)


def test_daily_math_is_deterministic_and_requires_real_points() -> None:
    assert _returns([100.0, 110.0, 99.0]) == pytest.approx([0.1, -0.1])
    assert _max_drawdown([100.0, 110.0, 99.0]) == pytest.approx(0.1)
    assert digest({"b": 2, "a": 1}) == digest({"a": 1, "b": 2})


def test_pearson_returns_valid_correlation_and_rejects_constant_series() -> None:
    assert _pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
    assert _pearson([1.0, 2.0, 3.0], [6.0, 4.0, 2.0]) == pytest.approx(-1.0)
    assert _pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None


def test_frozen_h14_cells_persist_the_valid_daily_correlations() -> None:
    artifact = json.loads(Path("data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json").read_text())
    correlations = [row["v3_h14_daily_return_correlation"] for row in artifact["evaluation"]["cells"]]
    assert len(correlations) == 18
    assert all(value is not None for value in correlations)


def test_report_pins_current_artifact_and_focused_test_count() -> None:
    artifact_path = Path("data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json")
    artifact = json.loads(artifact_path.read_text())
    report = Path("docs/research/h14_v3_daily_nav_comparability_2026_09_09.md").read_text()
    collection_path = Path("data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/focused_test_collection_receipt.json")
    collection = json.loads(collection_path.read_text())
    file_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    test_file = Path(collection["test_file"])
    assert collection["collection_exit_code"] == 0
    assert collection["collected_tests"] == 6
    assert hashlib.sha256(test_file.read_bytes()).hexdigest() == collection["test_file_sha256"]
    assert file_sha in report
    assert artifact["artifact_sha256"] in report
    assert hashlib.sha256(collection_path.read_bytes()).hexdigest() in report
    assert f"focused\ntests completed `{collection['collected_tests']} passed`" in report
    assert "Existing H14 native validation remains 11 passed." in report
    assert "d40acc4d322458a89d378733b9ca4f6ea9e5ea1c423e6bc2cebb2387a86c5f76" not in report
    assert "efd54c602ffb7b2d786da64f34eb1eb415e2864d9808f5ff2c6b02fc5efba4b7" not in report
    assert "tests completed `3 passed`" not in report
