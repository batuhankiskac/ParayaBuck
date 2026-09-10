from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.research import crossasset_retrospective_diagnostics_2026_09_09 as diagnostics


def test_protocol_is_frozen_before_outcomes_and_has_v2_lags() -> None:
    protocol = json.loads(diagnostics.PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["frozen_before_outcomes"] is True
    assert protocol["lags_days"] == [1, 3, 7]
    assert protocol["cost_scenarios"] == [1, 2, 3]
    assert protocol["overlay"]["status"] == "NOT_RUN_NO_ELIGIBLE_CANDIDATE"


def test_all_cards_remain_non_admissible() -> None:
    result = diagnostics.verify()
    assert result["status"] == "CROSSASSET_RETROSPECTIVE_DIAGNOSTICS_VERIFIED"
    payload = json.loads(diagnostics.RESULTS.read_text(encoding="utf-8"))
    assert set(payload["cards"]) == {f"X{i:02d}" for i in range(1, 7)}
    assert payload["counts"] == {"cards": 6, "diagnostic_cards": 3, "admitted_rows": 0, "economic_run": 0, "economic_eligible": 0, "promotion": 0}
    assert all(row["admitted_rows"] == 0 and row["economic_eligible"] is False for row in payload["cards"].values())


def test_protocol_source_mutation_is_rejected(tmp_path: Path) -> None:
    protocol = json.loads(diagnostics.PROTOCOL.read_text(encoding="utf-8"))
    protocol = copy.deepcopy(protocol)
    protocol["source_inputs"][0]["sha256"] = "0" * 64
    path = tmp_path / "protocol.json"
    path.write_bytes(diagnostics.canonical(protocol))
    with pytest.raises(ValueError, match="protocol_hash_mismatch"):
        diagnostics.verify(path)


def test_post_cutoff_mutation_is_rejected() -> None:
    with pytest.raises(ValueError, match="post_cutoff"):
        diagnostics.validate_curve_rows([{"date": "2026-08-01", "2y": "1", "10y": "2"}])


def test_verify_is_read_only_and_no_overlay() -> None:
    assert diagnostics.verify()["overlay_status"] == "NOT_RUN_NO_ELIGIBLE_CANDIDATE"
