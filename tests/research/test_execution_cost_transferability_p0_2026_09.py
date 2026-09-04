from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "research_manifests" / "execution_cost_transferability_p0_2026_09.json"


def test_p0_manifest_freezes_research_only_unknown_transferability_boundary() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert payload["status"] == "accepted"
    assert payload["research_only"] is True
    assert "declared_before_results" not in payload
    assert "no_results_yet" not in payload
    assert payload["predeclaration"] == {
        "human_design_brief_existed_before_implementation": True,
        "machine_manifest_committed_before_technical_results": False,
        "machine_manifest_committed_before_paper_economic_sample": True,
    }
    assert payload["results_state"] == {
        "technical_results_recorded": True,
        "paper_economic_results_recorded": False,
    }
    assert payload["commit_topology"]["result_recording_commit"] == "c0e167b2df8fc3cb2f5e4e4843a40d480fa56151"
    assert payload["acceptance"]["economic_verdict"] == "UNKNOWN_COST_TRANSFERABILITY"
    assert payload["acceptance"]["promotion"] == "none"
    assert payload["boundaries"]["broker_or_data_api_calls"] is False
    assert payload["boundaries"]["paper_or_live_activation"] is False
    assert payload["boundaries"]["orders"] is False
    assert payload["backtest_benchmark_contract"]["execution_model_change"] is False
    assert (
        payload["measurement_contract"]["fee_evidence"]
        == "event-local only; unknown fee blocks actual total-cost comparison"
    )
    assert payload["measurement_contract"]["cross_source_fee_reconciliation"].startswith("future work")
