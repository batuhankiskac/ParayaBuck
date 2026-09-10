from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.research import crypto_max_diagnostics_2026_09_09 as diagnostics
from scripts.research import freeze_crypto_reference_series_2026_09_09 as reference


def test_reference_series_is_receipt_bound_and_pit_pending() -> None:
    result = reference.verify()
    assert result["status"] == "CRYPTO_REFERENCE_SERIES_VERIFIED"
    receipt = json.loads(reference.RECEIPT.read_text(encoding="utf-8"))
    assert receipt["accepted_rows"] == 3346
    assert receipt["available_at_field_present"] is False
    assert receipt["causal_admissible"] is False


def test_protocol_v2_is_frozen_before_outcomes_and_exact_rules() -> None:
    protocol = json.loads(diagnostics.PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["frozen_before_outcomes"] is True
    assert protocol["lags_days"] == [1, 3, 7]
    assert protocol["cost_stress"]["multipliers"] == [1, 2, 3]
    assert protocol["rules"]["K01"]["threshold"] == {"operator": "<=", "value": 1.0}
    assert protocol["rules"]["K03"]["threshold"] == {"operator": ">", "value": 0.0}
    assert protocol["rules"]["K04"]["threshold"] == {"operator": "<=", "value": 0.0001}
    assert protocol["rules"]["K09"]["formula"] == "z_basis + z_OI_change <= -1.0; separate funding branch uses z_basis + z_funding <= -1.0"
    assert protocol["rules"]["K09"]["threshold"] == {"operator": "<=", "value": -1.0, "lookback": "expanding prior observations; minimum 20"}
    assert protocol["rules"]["K09"]["units_policy"].startswith("within-series standardized")


def test_results_have_both_diagnostic_and_terminal_cards() -> None:
    result = diagnostics.verify()
    assert result["status"] == "CRYPTO_RETROSPECTIVE_DIAGNOSTICS_VERIFIED"
    payload = json.loads(diagnostics.RESULTS.read_text(encoding="utf-8"))
    assert set(payload["cards"]) == {f"K{i:02d}" for i in range(1, 13)}
    assert payload["counts"] == {"cards": 12, "diagnostic_cards": 5, "economic_run": 0, "admitted": 0, "eligible": 0, "promotion": 0}
    assert payload["economic_run"] is False and payload["admitted"] == 0 and payload["eligible"] is False and payload["promotion"] is False
    assert len(payload["cards"]["K09"]["funding_short_history"]["arms"]) == 27


def test_protocol_source_mutation_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(diagnostics.PROTOCOL.read_text(encoding="utf-8"))
    payload = copy.deepcopy(payload)
    payload["feature_sources"][0]["sha256"] = "0" * 64
    path = tmp_path / "protocol.json"
    path.write_bytes(diagnostics.canonical(payload))
    with pytest.raises(ValueError, match="protocol_hash_mismatch"):
        diagnostics.verify(path)


def test_diagnostic_verify_is_network_free_and_read_only() -> None:
    files = (diagnostics.PROTOCOL, diagnostics.RESULTS, diagnostics.RECEIPT, diagnostics.REPORT)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    script = str(diagnostics.REPO / "scripts/research/crypto_max_diagnostics_2026_09_09.py")
    completed = subprocess.run([str(diagnostics.REPO / ".venv/bin/python"), script, "--verify"], cwd=Path("/tmp"), env=env, check=True, capture_output=True, text=True)
    assert "network_free" in completed.stdout
    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
    assert before == after


def test_signal_missing_availability_never_becomes_admission() -> None:
    payload = json.loads(diagnostics.RESULTS.read_text(encoding="utf-8"))
    assert all(row["causal_admissible"] is False and row["economic_eligible"] is False and row["admitted"] == 0 for row in payload["cards"].values())


def test_k11_one_day_archive_is_zero_signal_under_lag_grid() -> None:
    payload = json.loads(diagnostics.RESULTS.read_text(encoding="utf-8"))
    row = payload["cards"]["K11"]
    assert row["signal_rows"] == 1
    assert all(arm["active_days"] == 0 and arm["candidate_metrics"]["cagr"] == 0.0 for arm in row["arms"])


def test_result_mutation_is_rejected_by_receipt_pin(tmp_path: Path) -> None:
    mutated = tmp_path / "diagnostic_results.json"
    payload = json.loads(diagnostics.RESULTS.read_text(encoding="utf-8"))
    payload["cards"]["K01"]["signal_rows"] += 1
    mutated.write_bytes(diagnostics.canonical(payload))
    with pytest.raises(ValueError, match="results_hash_mismatch"):
        diagnostics.verify(result_path=mutated)
