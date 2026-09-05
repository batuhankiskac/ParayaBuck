"""Contract tests for the bounded six-cell corrected high-proximity runner."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from scripts.research.high_proximity364_history_fixed_2026_09_05 import (
    HighProximity364HistoryFixedAdapter,
)
from scripts.research.high_proximity364_history_fixed_runner_2026_09_05 import (
    ARM,
    COST_MULTIPLIERS,
    MATRIX,
    validate_manifest,
)
from scripts.research.portfolio_expansion_crypto_screen_2026_09 import HighProximity364Adapter


REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "data/research_manifests/high_proximity364_history_fixed_2026_09_05.json"


def test_corrected_manifest_is_strictly_frozen_and_has_exact_six_cells() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["status"] == "FROZEN_CORRECTED_HIGH_PROXIMITY_PROTOCOL"
    assert tuple(manifest["matrix"]["cells"]) == MATRIX
    assert manifest["matrix"]["cell_count"] == 6
    assert MATRIX == (
        "full/high_proximity364_history_fixed/1x",
        "full/high_proximity364_history_fixed/2x",
        "full/high_proximity364_history_fixed/3x",
        "recent/high_proximity364_history_fixed/1x",
        "recent/high_proximity364_history_fixed/2x",
        "recent/high_proximity364_history_fixed/3x",
    )
    assert COST_MULTIPLIERS == (1.0, 2.0, 3.0)
    assert ARM == "high_proximity364_history_fixed"
    assert manifest["candidate"]["history_bars"] == 364
    assert manifest["candidate"]["change_from_frozen_screen"] == "minimum_history_bars declaration only"
    assert manifest["execution"]["run_status"] == "FROZEN_NO_RUN_STARTED"

    if manifest["manifest_sha256"] in ("", "DRAFT_COMPUTE_AFTER_FREEZE"):
        with pytest.raises(RuntimeError, match="BLOCKED_MANIFEST_DIGEST_PLACEHOLDER"):
            validate_manifest(MANIFEST)
    else:
        validate_manifest(MANIFEST)


def test_corrected_adapter_changes_only_history_declaration() -> None:
    original = HighProximity364Adapter({"top_n": 2, "rebalance_hours": 96})
    corrected = HighProximity364HistoryFixedAdapter({"top_n": 2, "rebalance_hours": 96})

    assert isinstance(corrected, HighProximity364Adapter)
    assert original.minimum_history_bars == 23
    assert corrected.minimum_history_bars == 364
    assert corrected.name != original.name
    assert corrected.market == original.market
    assert corrected.timeframe == original.timeframe
    assert corrected.parameter_tuple() == original.parameter_tuple()
    assert corrected._score_candidates.__func__ is original._score_candidates.__func__
    assert corrected.decide.__func__ is original.decide.__func__


def test_runner_reuses_native_inputs_and_does_not_add_candidate_search() -> None:
    from scripts.research import high_proximity364_history_fixed_runner_2026_09_05 as runner

    source = inspect.getsource(runner.run_case)
    assert "aggregate_historical_bars(bars, \"1d\", require_complete=True)" in source
    assert "timestamp + timedelta(days=1)" in source
    assert "RecordingEngine(" in source
    assert "HighProximity364HistoryFixedAdapter({\"top_n\": 2, \"rebalance_hours\": 96})" in source
    assert "run_matrix" not in source
    assert "parameter" not in source.lower() or "parameter_or_weight_search" in source.lower()
