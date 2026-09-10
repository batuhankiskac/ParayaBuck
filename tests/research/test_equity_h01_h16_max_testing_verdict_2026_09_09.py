from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.research import build_equity_h01_h16_max_testing_verdict_2026_09_09 as verdict


def test_matrix_has_exact_ids_and_no_admission() -> None:
    result = verdict.build()
    assert result["status"] == "EQUITY_MAX_TESTING_COMPLETE_V3"
    payload = json.loads(verdict.RESULTS.read_text(encoding="utf-8"))
    assert [row["candidate_id"] for row in payload["rows"]] == [*verdict.EXPECTED_IDS, "H14C"]
    assert payload["economic_run"] is False
    assert payload["integration_candidate_count"] == 0
    assert payload["rows"][5]["category"] == "RETROSPECTIVE_EVENT_DIAGNOSTIC_NOT_CANDIDATE"
    assert payload["rows"][7]["category"] == "RETROSPECTIVE_METADATA_EVENT_DIAGNOSTIC_NOT_CANDIDATE"
    assert payload["rows"][13]["category"] == "TESTED_INFEASIBLE_DIAGNOSTIC"
    assert payload["rows"][-1]["category"] == "POST_HOC_EXPLORATORY_DIAGNOSTIC_NOT_CANDIDATE"
    assert verdict.verify()["status"] == "EQUITY_MAX_TESTING_VERIFIED"


@pytest.mark.parametrize("target", [verdict.MANIFEST, verdict.RESULTS, verdict.RECEIPT])
def test_mutation_fails_closed(target: Path) -> None:
    original = target.read_bytes()
    try:
        payload = json.loads(original)
        if target == verdict.MANIFEST:
            payload["candidate_count"] = 16
        elif target == verdict.RESULTS:
            payload["integration_candidate_count"] = 1
        else:
            payload["receipt_sha256"] = "0" * 64
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            verdict.verify()
    finally:
        target.write_bytes(original)
