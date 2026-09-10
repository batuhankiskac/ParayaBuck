from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.research import build_strategy_queue_zero_wait_decision_2026_09_09 as builder
from scripts.research import verify_strategy_queue_zero_wait_decision_2026_09_09 as verifier

REPO = Path(__file__).parents[2]


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    for relative in (builder.AGGREGATE, builder.CANDIDATES):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, destination)
    output = tmp_path / "data/research_runs/strategy_queue_zero_wait_2026_09_09"
    report = tmp_path / "docs/research/strategy_queue_zero_wait_decision_2026_09_09.md"
    return tmp_path, output, report


def test_zero_wait_inventory_and_h06_h10_gate(tmp_path: Path) -> None:
    repo, output, report = _fixture(tmp_path)
    decision = builder.build_decision(repo)
    assert decision["candidate_count"] == 34
    assert decision["counts"] == {
        "CLOSE_ZERO_WAIT": 2,
        "CLOSE_NOT_IMMEDIATE": 7,
        "CLOSE_UNAUTHORIZED_PAID_DATA": 25,
        "IMMEDIATE_RETEST": 0,
    }
    rows = {row["candidate_id"]: row for row in decision["decisions"]}
    assert rows["H06"]["classification"] == "CLOSE_NOT_IMMEDIATE"
    assert rows["H10"]["classification"] == "CLOSE_NOT_IMMEDIATE"
    assert "rank" in rows["H06"]["detail"] and "notional" in rows["H06"]["detail"]
    assert "rank" in rows["H10"]["detail"] and "notional" in rows["H10"]["detail"]
    result = builder.write_bundle(repo, output, report)
    assert result["status"] == "ZERO_WAIT_PRUNE_COMPLETE"
    assert verifier.verify_bundle(repo, output, report)["verified"] is True


def test_bundle_verifier_rejects_decision_mutation(tmp_path: Path) -> None:
    repo, output, report = _fixture(tmp_path)
    builder.write_bundle(repo, output, report)
    decision_path = output / builder.DECISION_NAME
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["decisions"][0]["immediate_retest"] = True
    decision_path.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decision_self_hash_mismatch"):
        verifier.verify_bundle(repo, output, report)


def test_report_has_explicit_zero_wait_boundary(tmp_path: Path) -> None:
    repo, output, report = _fixture(tmp_path)
    builder.write_bundle(repo, output, report)
    text = report.read_text(encoding="utf-8")
    assert "34 adayın bugün gerçek `true holdout + cost + caps + V3 complement` sınavına ulaşan sayısı `0`" in text
    assert "Strateji ailesi hakkında global çürütme" in text
    assert "ZERO_WAIT_PRUNE_COMPLETE" in text
