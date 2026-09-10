from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.research import build_immediate_candidates_invalidation_2026_09_10 as builder
from scripts.research import verify_immediate_candidates_invalidation_2026_09_10 as verifier

REPO = Path(__file__).parents[2]


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    for relative in (builder.MANIFEST, builder.RESULTS, builder.RUN_RECEIPT, builder.RUN_REPORT, builder.RUN_LEDGER, builder.RUNNER, builder.RUN_VERIFIER):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, destination)
    output = tmp_path / "data/research_runs/immediate_candidates_invalidation_2026_09_10"
    report = tmp_path / "docs/research/immediate_candidates_invalidation_2026_09_10.md"
    return tmp_path, output, report


def test_invalidation_bundle_is_closed_and_verifiable(tmp_path: Path) -> None:
    repo, output, report = _fixture(tmp_path)
    result = builder.write_bundle(repo, output, report)
    assert result["status"] == "IMMEDIATE_CANDIDATES_INVALIDATION_COMPLETE"
    assert result["classification"] == "POST_OUTCOME_PROTOCOL_CHANGE_INVALID"
    assert verifier.verify_bundle(repo, output, report)["verified"] is True
    decision = json.loads((output / "decision.json").read_text(encoding="utf-8"))
    assert decision["diagnostic_metrics_preserved"] is True
    assert decision["diagnostic_metrics_reuse_prohibited"] is True
    assert decision["full_rerun_required"] is False
    assert decision["capacity_joint_nav_missing"] is True
    assert decision["admission"] is False
    assert [row["status"] for row in decision["review_evidence"]] == ["NEEDS_FIX", "NEEDS_FIX"]


def test_verifier_rejects_decision_mutation(tmp_path: Path) -> None:
    repo, output, report = _fixture(tmp_path)
    builder.write_bundle(repo, output, report)
    path = output / "decision.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["admission"] = True
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decision_self_hash_mismatch"):
        verifier.verify_bundle(repo, output, report)
