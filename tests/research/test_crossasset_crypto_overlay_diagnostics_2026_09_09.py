from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.research import crossasset_crypto_overlay_diagnostics_2026_09_09 as diagnostics


def test_protocol_freezes_crypto_grid_before_outcomes() -> None:
    protocol = json.loads(diagnostics.PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["frozen_before_outcomes"] is True
    assert protocol["lags_days"] == [1, 3, 7]
    assert protocol["cost_multipliers"] == [1, 2, 3]
    assert protocol["overlays"] == {"capacity_aware": "preserve V3 and allocate only dated spare capacity", "fixed": "0.95*V3 + 0.05*candidate"}


def test_measured_cards_bind_expected_raw_evidence_and_grid() -> None:
    payload = json.loads(diagnostics.RESULTS.read_text(encoding="utf-8"))
    assert payload["counts"] == {"cards": 6, "measured_cards": 3, "arms": 162, "economic_run": 0, "admitted_rows": 0, "economic_eligible": 0, "promotion": 0}
    assert payload["cards"]["X01"]["signal_rows"] == 2645
    assert payload["cards"]["X01"]["curve_rows"] == 2645
    assert payload["cards"]["X01"]["bars_by_asset"] == {"IEF": 2659, "SHY": 2659, "TLT": 2659}
    assert payload["cards"]["X05"]["raw_evidence_rows"] == 1152
    assert payload["cards"]["X06"]["raw_evidence_rows"] == 372
    assert all(len(payload["cards"][card]["arms"]) == 54 for card in ("X01", "X05", "X06"))


def test_all_cards_are_non_admissible_and_overlay_is_retrospective() -> None:
    result = diagnostics.verify()
    assert result["status"] == "CROSSASSET_MAX_TESTING_V3_VERIFIED"
    payload = json.loads(diagnostics.RESULTS.read_text(encoding="utf-8"))
    assert payload["overlay_status"] == "RETROSPECTIVE_ONLY_NO_PIT_ADMISSION"
    assert all(row["admitted_rows"] == 0 and row["economic_eligible"] is False for row in payload["cards"].values())


def test_protocol_source_mutation_fails_closed(tmp_path: Path) -> None:
    protocol = copy.deepcopy(json.loads(diagnostics.PROTOCOL.read_text(encoding="utf-8")))
    protocol["source_inputs"][0]["sha256"] = "0" * 64
    protocol["protocol_sha256"] = hashlib.sha256(diagnostics.canonical({key: value for key, value in protocol.items() if key != "protocol_sha256"})).hexdigest()
    path = tmp_path / "protocol.json"
    path.write_bytes(diagnostics.canonical(protocol))
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        diagnostics.protocol(path)


def test_capacity_aware_arms_preserve_v3_and_report_caps() -> None:
    payload = json.loads(diagnostics.RESULTS.read_text(encoding="utf-8"))
    arms = [arm for card in ("X01", "X05", "X06") for arm in payload["cards"][card]["arms"] if arm["mode"] == "capacity_aware"]
    assert arms and all(sum(arm["caps"].values()) == 0 for arm in arms)
