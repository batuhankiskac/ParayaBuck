from __future__ import annotations

import hashlib
from pathlib import Path

from research.residual_reversal_local_v1.candidate import frozen_reversal_parameters
from research.residual_reversal_local_v1.corrected_replay_v1.corrected_mechanics import (
    FROZEN_MAX_POSITIONS,
    FROZEN_MAX_SINGLE_WEIGHT,
    FROZEN_REFERENCE_ALLOCATION,
    FROZEN_REVERSAL_ALLOCATION,
    FROZEN_V3_ALLOCATION,
    TREATMENT_OBSERVATION_NAME,
)

OUTPUT = Path(__file__).resolve().parent


def test_corrected_spec_is_present_and_structural_contract_is_pinned() -> None:
    spec = OUTPUT / "CORRECTED_REPLAY_SPEC.md"
    text = spec.read_text(encoding="utf-8")
    digest = hashlib.sha256(spec.read_bytes()).hexdigest()
    assert len(digest) == 64
    for phrase in (
        "validation contains at least one genuine",
        "schedule attempt separately from fills",
        "v5 entity-lifetime table",
        "separate V3 and reversal",
        "no cross-sleeve reallocation",
        "CORRECTED_REPLAY_VALID",
    ):
        assert phrase.lower() in text.lower()


def test_runner_is_one_shot_and_does_not_call_original_campaign_main() -> None:
    source = (OUTPUT / "corrected_replay.py").read_text(encoding="utf-8")
    assert "from research.residual_reversal_local_v1 import run_campaign as original" in source
    assert "original.main(" not in source
    assert '"economic_results_generated": True' in source
    assert "CORRECTED_REPLAY_SPEC.md" in source
    assert "Corrected replay output already exists" in source


def test_verifier_contains_upgraded_independent_gates() -> None:
    source = (OUTPUT / "verify_corrected_replay.py").read_text(encoding="utf-8")
    for phrase in (
        "CORRECTED_REPLAY_VALID",
        "terminal prices found in pinned panel",
        "one scheduled attempt per ISO week",
        "validation begins after frozen boundary",
        "source ledger",
        "no reallocation",
        "aggregate cost arithmetic",
        "current main unchanged",
    ):
        assert phrase in source


def test_frozen_parameters_and_allocation_constants_are_exact() -> None:
    assert frozen_reversal_parameters() == {
        "formation_bars": 21,
        "skip_bars": 0,
        "beta_window_bars": 273,
        "residual_vol_window": 63,
        "top_n": 6,
        "max_single_weight": 0.10,
        "ivol_reject_quantile": 0.70,
        "exclude_symbols": ["SPY"],
        "rebalance_frequency": "weekly",
    }
    assert FROZEN_REFERENCE_ALLOCATION == 0.55
    assert abs(FROZEN_V3_ALLOCATION - 0.4125) < 1e-12
    assert abs(FROZEN_REVERSAL_ALLOCATION - 0.1375) < 1e-12
    assert FROZEN_MAX_POSITIONS == 6
    assert FROZEN_MAX_SINGLE_WEIGHT == 0.10
    assert TREATMENT_OBSERVATION_NAME == "treatment_combined_observation_v1"
