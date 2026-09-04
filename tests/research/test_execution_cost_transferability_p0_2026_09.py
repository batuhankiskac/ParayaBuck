from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "research_manifests" / "execution_cost_transferability_p0_2026_09.json"


def test_p0_manifest_freezes_research_only_unknown_transferability_boundary() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert payload["research_only"] is True
    assert payload["declared_before_results"] is True
    assert payload["acceptance"]["economic_verdict"] == "UNKNOWN_COST_TRANSFERABILITY"
    assert payload["acceptance"]["promotion"] == "none"
    assert payload["boundaries"]["broker_or_data_api_calls"] is False
    assert payload["boundaries"]["paper_or_live_activation"] is False
    assert payload["boundaries"]["orders"] is False
    assert payload["backtest_benchmark_contract"]["execution_model_change"] is False
