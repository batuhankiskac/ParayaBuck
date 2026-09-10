from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.research import crypto_capacity_aware_diagnostics_2026_09_09 as capacity


def test_capacity_package_verifies_and_all_caps_are_zero() -> None:
    result = capacity.verify()
    assert result["status"] == "CRYPTO_CAPACITY_AWARE_DIAGNOSTICS_VERIFIED"
    assert result["arms"] == 162
    assert result["cap_violations"] == 0
    payload = json.loads(capacity.RESULTS.read_text(encoding="utf-8"))
    assert payload["counts"]["candidate_branches"] == 5
    assert set(payload["cards"]) == {"K01", "K03", "K04", "K09", "K09_funding_short_history", "K11"}
    assert all(arm["capacity"]["all_caps_zero_violations"] for row in payload["cards"].values() for arm in row["arms"])


def test_amendment_is_explicitly_post_hoc_and_v3_is_preserved() -> None:
    protocol = json.loads(capacity.PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["amendment_status"] == "POST_HOC_EXPLORATORY"
    assert protocol["outcome_after_prior_diagnostic"] is True
    assert protocol["incumbent_policy"].endswith("at 100%")
    assert protocol["position_rule"].startswith("active candidate assets sorted lexicographically")


def test_capacity_arms_contain_required_diagnostic_metrics() -> None:
    payload = json.loads(capacity.RESULTS.read_text(encoding="utf-8"))
    for row in payload["cards"].values():
        for arm in row["arms"]:
            assert "portfolio_metrics" in arm and "cvar_p05" in arm["portfolio_metrics"]
            assert "candidate_bootstrap" in arm
            assert arm["permutation_placebo"]["replicates"] in (0, 128)
            assert "regimes" in arm and "active_days" in arm


def test_capacity_result_hash_mutation_fails(tmp_path: Path) -> None:
    payload = json.loads(capacity.RESULTS.read_text(encoding="utf-8"))
    payload = copy.deepcopy(payload)
    payload["cards"]["K01"]["arms"][0]["capacity"]["position_violations"] = 1
    path = tmp_path / "mutated.json"
    path.write_bytes(capacity.base.canonical(payload))
    assert capacity.base.sha(path) != capacity.base.sha(capacity.RESULTS)


def test_capacity_verify_is_read_only_from_arbitrary_cwd() -> None:
    files = (capacity.PROTOCOL, capacity.RESULTS, capacity.REPORT, capacity.RECEIPT)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run([str(capacity.REPO / ".venv/bin/python"), str(capacity.REPO / "scripts/research/crypto_capacity_aware_diagnostics_2026_09_09.py"), "--verify"], cwd=Path("/tmp"), env=env, capture_output=True, text=True, check=True)
    assert "network_free" in completed.stdout
    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
    assert before == after


def test_capacity_protocol_mutation_is_fail_closed(tmp_path: Path) -> None:
    payload = json.loads(capacity.PROTOCOL.read_text(encoding="utf-8"))
    payload["constraints"]["cash_min"] = 0.0
    path = tmp_path / "protocol.json"
    path.write_bytes(capacity.base.canonical(payload))
    with pytest.raises(ValueError, match="capacity_protocol_hash_mismatch"):
        capacity.verify(protocol_path=path)
