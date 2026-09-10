"""Contract checks for the outcome-blind immediate candidate freeze."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "data/research_manifests/immediate_candidate_freeze_2026_09_09.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_without_self_hash(payload: dict[str, object]) -> str:
    value = dict(payload)
    value.pop("protocol_sha256", None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load() -> dict[str, object]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_freeze_is_pre_outcome_and_has_six_unique_candidates() -> None:
    payload = _load()
    ids = payload["candidate_ids"]
    assert payload["status"] == "FROZEN_PRE_OUTCOME"
    assert payload["economic_run"] is False
    assert payload["outcome_scan_performed"] is False
    assert payload["production_paper_live_untouched"] is True
    assert payload["candidate_count"] == 6
    assert len(ids) == len(set(ids)) == 6
    assert {candidate["id"] for candidate in payload["candidates"]} == set(ids)


def test_protocol_self_hash_and_input_pins_are_current() -> None:
    payload = _load()
    assert payload["protocol_sha256"] == _canonical_without_self_hash(payload)
    for relative, record in payload["data_inventory"].items():
        if relative == "config/settings.yaml" or relative.endswith(".csv") or relative.endswith(".json") or relative.endswith(".jsonl"):
            assert _sha(REPO / relative) == record["sha256"]


def test_protocol_matrix_and_overlay_are_frozen() -> None:
    payload = _load()
    evaluation = payload["evaluation"]
    assert evaluation["cutoff"] == "2026-07-31T23:59:59Z"
    assert evaluation["windows"]["full"]["start"] == "2022-01-01T00:00:00Z"
    assert evaluation["windows"]["recent"]["start"] == "2024-01-01T00:00:00Z"
    assert evaluation["windows"]["true_holdout"]["start"] == "2026-01-01T00:00:00Z"
    assert evaluation["windows"]["true_holdout"]["selection_use_forbidden"] is True
    assert evaluation["lag_days"] == [1, 3, 7]
    assert evaluation["cost_multipliers"] == [1.0, 2.0, 3.0]
    assert evaluation["v3_overlay"]["recipe"] == "95% unchanged V3 daily NAV/dated weights + 5% candidate sleeve"
    assert evaluation["v3_overlay"]["v3_weights_preserved"] is True
    assert evaluation["v3_overlay"]["candidate_position_rule"]["max_positions"] == "min(candidate_top_n, max_total_positions - dated_v3_position_count)"
    assert evaluation["v3_overlay"]["candidate_position_rule"]["zero_when_no_slots"] is True
    assert "2026-07-29" in evaluation["v3_overlay"]["date_alignment"]
    assert evaluation["robustness"]["mechanical_complementarity"]["required"] is True
    assert evaluation["trade_ledger"]["turnover_definition"] == "gross traded notional divided by lagged candidate-sleeve NAV"
    assert "fill_timestamp" in evaluation["trade_ledger"]["required_fields"]
    assert "signal_asof_timestamp" in evaluation["trade_ledger"]["required_fields"]
    assert "no cost or turnover is imputed" in evaluation["trade_ledger"]["missing_or_nonfinite_policy"]
    assert evaluation["admission_gates"]["promotion"] is False
    assert evaluation["admission_gates"]["integration"] is False


def test_candidate_formulas_are_direct_ohlcv_and_exclude_v3_signal_path() -> None:
    payload = _load()
    forbidden = ("residual", "12-1", "consensus", "inverse-residual")
    direct_fields = {"close", "volume", "high"}
    for candidate in payload["candidates"]:
        text = str(candidate["formula"]).lower()
        assert not any(token in text for token in forbidden)
        assert direct_fields.intersection(candidate["required_fields"])
        assert candidate["top_n"] == 8
        assert candidate["weighting"] == "equal capped 55% sleeve"


def test_data_basis_and_a03_zero_volume_policy_are_explicit() -> None:
    payload = _load()
    policy = payload["selection_basis"]["corporate_action_policy"]
    assert "split-adjusted" in policy["signal_basis"]
    assert "dividend-adjusted" in policy["signal_basis"]
    assert "at or before" in policy["outcome_basis"]
    assert "quarantined" in policy["post_cutoff_policy"]
    a03 = next(candidate for candidate in payload["candidates"] if candidate["id"] == "A03")
    assert "strictly positive volume" in a03["missing_data"]
    assert "nonnegative volume" not in a03["missing_data"]
