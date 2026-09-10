from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.research import refresh_h14_current_stage_semantics_2026_09_09 as refresh


def test_current_stage_binds_authoritative_h14_v3_semantics() -> None:
    stage = refresh.validate_stage()
    h14 = stage["frozen_contracts"]["H14"]
    assert h14["status"] == refresh.STATUS
    assert h14["current_v3_parity"] == "PASS"
    assert h14["current_v3_daily_nav_rows"] == 2658
    assert h14["correlation_rows"] == 18
    assert h14["fixed_sleeve"] == "fixed 95% V3 + 5% H14 normalized sleeve"
    assert h14["fixed_weighting"] == "95% V3 + 5% H14"
    assert h14["portfolio_contribution_run"] is True
    assert h14["portfolio_contribution_scope"].startswith("diagnostic only")
    assert h14["economic_eligible"] is False
    assert h14["integration_candidate"] is False
    assert h14["promotion"] is False
    assert all(value > 0 for value in h14["shared_position_cap_violations"].values())
    validation = stage["validation"]["h14_current_v3"]
    assert validation["artifact"] == str(refresh.V3_ARTIFACT.relative_to(refresh.REPO))
    assert validation["artifact_sha256"] == refresh.sha256(refresh.V3_ARTIFACT)
    assert validation["parity"] == "PASS"
    assert validation["daily_nav_rows"] == 2658
    assert validation["cell_count"] == validation["correlation_rows"] == 18
    assert validation["economic_run"] is False
    assert validation["portfolio_contribution_run"] is True
    for marker in refresh.STALE_H14_TEXT:
        assert marker not in json.dumps(h14)


def test_stage_h14_semantic_mutation_fails_closed(tmp_path: Path) -> None:
    stage = refresh.validate_stage()
    mutated = deepcopy(stage)
    mutated["frozen_contracts"]["H14"]["status"] = "PORTFOLIO_CONTRIBUTION_" + "UNTESTED"
    mutated["record_sha256"] = refresh.canonical_hash({key: value for key, value in mutated.items() if key != "record_sha256"})
    path = tmp_path / "stage.json"
    path.write_text(json.dumps(mutated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stage_h14_semantics_invalid"):
        refresh.validate_stage(path)


def test_stage_refresh_preserves_h01_h05_contracts() -> None:
    stage = refresh.validate_stage()
    artifact = refresh.load_json(refresh.V3_ARTIFACT)
    updated = refresh.build_stage(stage, artifact)
    for candidate_id in ("H01", "H02", "H03", "H04", "H05"):
        assert updated["frozen_contracts"][candidate_id] == stage["frozen_contracts"][candidate_id]
