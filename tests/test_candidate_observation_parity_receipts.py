from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from parayabuck.backtest.observation_parity import canonical_digest
from scripts.compact_candidate_observation_parity_receipt import (
    _validate_e3,
    _validate_raw,
)


def _identity() -> dict[str, Any]:
    return {
        "market": "equity",
        "name": "candidate_v1",
        "version": "1.0.0",
        "timeframe": "1d",
        "parameters": [["top_n", 4]],
        "parameter_digest_sha256": "a" * 64,
    }


def _valid_e3() -> dict[str, Any]:
    identity = _identity()
    return {
        "candidate_economics_run": False,
        "fresh_instance_checks": True,
        "C|C": {"observation_basis": "configured", "execution_strategy_identity": deepcopy(identity)},
        "X|C": {"observation_basis": "configured", "execution_strategy_identity": deepcopy(identity)},
        "X|X": {
            "observation_basis": "candidate_owned",
            "observation_strategy_identity": deepcopy(identity),
            "execution_strategy_identity": deepcopy(identity),
            "observation_universe_hash": "b" * 64,
        },
    }


def test_compactor_accepts_complete_e3_boundary() -> None:
    _validate_e3(_valid_e3())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("fresh_instance_checks"),
        lambda value: value["X|X"].update(execution_strategy_identity={**_identity(), "parameters": [["top_n", 8]]}),
        lambda value: value["X|X"].update(observation_basis="configured"),
    ],
)
def test_compactor_rejects_incomplete_or_falsified_e3(mutate: Any) -> None:
    payload = _valid_e3()
    mutate(payload)
    with pytest.raises(ValueError):
        _validate_e3(payload)


def _minimal_raw(checks: dict[str, Any]) -> dict[str, Any]:
    digest = "a" * 64
    raw: dict[str, Any] = {
        "run_commit": "1" * 40,
        "checks": checks,
        "dirty_paths_before": [],
        "dirty_paths_after": [],
        "policy": {"candidate_economics_run": False},
        "config": {"path": "config/settings.yaml", "semantic_digest_sha256": digest},
        "config_digest_sha256": digest,
        "dataset": {
            "bars_path": "bars.csv",
            "bars_sha256": digest,
            "lifetimes_path": "lifetimes.csv",
            "lifetimes_sha256": digest,
            "corporate_actions_path": "actions.csv",
            "corporate_actions_sha256": digest,
            "artifacts": {
                name: {"path": path, "sha256": digest}
                for name, path in {
                    "bars": "bars.csv",
                    "lifetimes": "lifetimes.csv",
                    "corporate_actions": "actions.csv",
                }.items()
            },
        },
    }
    raw["receipt_sha256"] = canonical_digest(raw)
    return raw


def test_compactor_requires_exact_check_set(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.compact_candidate_observation_parity_receipt as compactor

    monkeypatch.setattr(compactor, "_validate_source_provenance", lambda raw: {})
    monkeypatch.setattr(compactor, "_require_commit", lambda value, field: value)
    monkeypatch.setattr(compactor, "_validate_artifact_provenance", lambda *args, **kwargs: None)
    monkeypatch.setattr(compactor, "_validate_e1", lambda *args, **kwargs: None)
    monkeypatch.setattr(compactor, "_validate_e2", lambda *args, **kwargs: None)
    monkeypatch.setattr(compactor, "_validate_e3", lambda *args, **kwargs: None)

    checks = {name: {} for name in compactor.EXPECTED_CHECKS}
    valid = _minimal_raw(checks)
    assert _validate_raw(valid, b"unused") == valid["receipt_sha256"]
    with pytest.raises(ValueError, match="exactly"):
        _validate_raw(_minimal_raw({**checks, "E4_unknown": {}}), b"unused")
    missing = dict(checks)
    missing.pop(next(iter(compactor.EXPECTED_CHECKS)))
    with pytest.raises(ValueError, match="exactly"):
        _validate_raw(_minimal_raw(missing), b"unused")
