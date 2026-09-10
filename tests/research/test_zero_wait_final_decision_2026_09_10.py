from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.research import build_zero_wait_final_decision_2026_09_10 as builder
from scripts.research import verify_zero_wait_final_decision_2026_09_10 as verifier

REPO = Path(__file__).parents[2]


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    for relative in builder.EXPECTED:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, destination)
    output = tmp_path / "data/research_runs/zero_wait_final_decision_2026_09_10"
    report = tmp_path / "docs/research/zero_wait_final_decision_2026_09_10.md"
    return tmp_path, output, report


def test_final_closure_bundle_is_closed_and_verifiable(tmp_path: Path) -> None:
    repo, output, report = _fixture(tmp_path)
    built = builder.write_bundle(repo, output, report)
    assert built["status"] == "ZERO_WAIT_FINAL_CLOSURE_COMPLETE"
    assert verifier.verify_bundle(repo, output, report)["verified"] is True
    decision = json.loads((output / "decision.json").read_text(encoding="utf-8"))
    assert decision["aggregate"]["integration_ready"] == 0
    assert decision["zero_wait_overlay"]["diagnostic_metrics_reuse_prohibited"] is True
    assert decision["aggregate"]["family_globally_disproven"] is False
    assert "PENDING" not in (output / "decision.json").read_text(encoding="utf-8")


def test_final_closure_verifier_rejects_decision_mutation(tmp_path: Path) -> None:
    repo, output, report = _fixture(tmp_path)
    builder.write_bundle(repo, output, report)
    path = output / "decision.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["aggregate"]["integration_ready"] = 1
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decision_self_hash_mismatch"):
        verifier.verify_bundle(repo, output, report)
