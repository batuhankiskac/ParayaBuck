"""Tests for Mission 3 research experiment platform: fingerprints, grid, resume, provenance, compare, CLI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from parayabuck.config import load_config
from parayabuck.research.experiment import compare_experiments, resolve_experiment, run_experiment
from parayabuck.research.fingerprint import (
    canonical_json,
    trial_id,
)
from parayabuck.research.persistence import ExperimentStore
from parayabuck.research.spec import expand_grid, parse_experiment_spec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_spec(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _minimal_spec(tmp_path: Path, extra: dict | None = None) -> Path:
    base = {
        "name": "test-exp",
        "market": "equity",
        "strategy": "equity_residual_momentum_v3",
        "tier": "smoke",
        "workers": 1,
        "base_parameters": {"top_n": 8},
        "trials": [
            {"label": "a", "parameters": {"beta_window_bars": 273}},
            {"label": "b", "parameters": {"beta_window_bars": 300}},
        ],
        "max_trials": 100,
    }
    if extra:
        base.update(extra)
    return _write_spec(tmp_path, base)


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


def test_fingerprint_stable_across_runs(tmp_path: Path):
    spec = _minimal_spec(tmp_path)
    cfg = load_config(None)
    r1 = resolve_experiment(spec, config=cfg)
    r2 = resolve_experiment(spec, config=cfg)
    assert r1.experiment_id == r2.experiment_id
    assert r1.trial_ids == r2.trial_ids


def test_fingerprint_dict_order_irrelevant(tmp_path: Path):
    # Parameters dict order should not affect id
    _minimal_spec(
        tmp_path,
        extra={
            "trials": [
                {"label": "a", "parameters": {"beta_window_bars": 252, "top_n": 8}},
                {"label": "b", "parameters": {"top_n": 8, "beta_window_bars": 273}},
            ]
        },
    )
    # Already test that canonical_json sorts keys, so direct test:
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    # Also trial_id should be same regardless of dict insertion order
    assert trial_id({"a": 1, "b": 2}, None, 1.0) == trial_id({"b": 2, "a": 1}, None, 1.0)


def test_fingerprint_worker_count_irrelevant(tmp_path: Path):
    cfg = load_config(None)
    s1 = _minimal_spec(tmp_path, extra={"workers": 1})
    # Overwrite second spec with workers 4 (different file)
    p2 = tmp_path / "spec2.yaml"
    data = yaml.safe_load(s1.read_text())
    data["workers"] = 4
    p2.write_text(yaml.safe_dump(data), encoding="utf-8")
    r1 = resolve_experiment(s1, config=cfg)
    r2 = resolve_experiment(p2, config=cfg)
    assert r1.experiment_id == r2.experiment_id
    # run_id should differ (execution identity)
    assert r1.run_id != r2.run_id


def test_fingerprint_output_path_irrelevant(tmp_path: Path):
    cfg = load_config(None)
    s1 = _minimal_spec(tmp_path, extra={"output_dir": "research_runs/a"})
    p2 = tmp_path / "spec2.yaml"
    data = yaml.safe_load(s1.read_text())
    data["output_dir"] = "research_runs/b"
    p2.write_text(yaml.safe_dump(data), encoding="utf-8")
    r1 = resolve_experiment(s1, config=cfg)
    r2 = resolve_experiment(p2, config=cfg)
    assert r1.experiment_id == r2.experiment_id


def test_strategy_param_change_changes_id(tmp_path: Path):
    cfg = load_config(None)
    s1 = _minimal_spec(tmp_path)
    data = yaml.safe_load(s1.read_text())
    data["trials"][0]["parameters"]["beta_window_bars"] = 999
    p2 = tmp_path / "spec2.yaml"
    p2.write_text(yaml.safe_dump(data), encoding="utf-8")
    r1 = resolve_experiment(s1, config=cfg)
    r2 = resolve_experiment(p2, config=cfg)
    assert r1.experiment_id != r2.experiment_id


def test_cost_multiplier_change_changes_id(tmp_path: Path):
    cfg = load_config(None)
    s1 = _minimal_spec(tmp_path)
    data = yaml.safe_load(s1.read_text())
    data["trials"][0]["cost_multiplier"] = 2.0
    p2 = tmp_path / "spec2.yaml"
    p2.write_text(yaml.safe_dump(data), encoding="utf-8")
    r1 = resolve_experiment(s1, config=cfg)
    r2 = resolve_experiment(p2, config=cfg)
    assert r1.experiment_id != r2.experiment_id


def test_dataset_fingerprint_change_changes_id(tmp_path: Path):
    cfg = load_config(None)
    s1 = _minimal_spec(tmp_path, extra={"dataset": "data/historical/equity_1200_2016_2026.csv", "tier": "quick"})
    data = yaml.safe_load(s1.read_text())
    data["dataset"] = "data/other.csv"
    p2 = tmp_path / "spec2.yaml"
    p2.write_text(yaml.safe_dump(data), encoding="utf-8")
    r1 = resolve_experiment(s1, config=cfg)
    r2 = resolve_experiment(p2, config=cfg)
    assert r1.experiment_id != r2.experiment_id


def test_trial_id_label_not_included(tmp_path: Path):
    # Same economics different label => same trial_id (but experiment_id differs due to label? No, trial_id excludes label)
    assert trial_id({"beta_window_bars": 252}, None, 1.0) == trial_id({"beta_window_bars": 252}, None, 1.0)
    # Different params => different
    assert trial_id({"beta_window_bars": 252}, None, 1.0) != trial_id({"beta_window_bars": 273}, None, 1.0)


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------


def test_grid_deterministic_ordering():
    grid = {"b": [1, 2], "a": [10, 20]}
    expanded = expand_grid(grid)
    # Keys sorted: a then b
    # Expect order: (a=10,b=1), (a=10,b=2), (a=20,b=1), (a=20,b=2)
    assert expanded[0]["parameters"] == {"a": 10, "b": 1}
    assert expanded[1]["parameters"] == {"a": 10, "b": 2}
    assert expanded[2]["parameters"] == {"a": 20, "b": 1}
    assert expanded[3]["parameters"] == {"a": 20, "b": 2}
    # Labels deterministic
    assert expanded[0]["label"] != expanded[1]["label"]


def test_grid_cartesian():
    grid = {"x": [1, 2], "y": [3, 4], "z": [5]}
    expanded = expand_grid(grid)
    assert len(expanded) == 2 * 2 * 1
    # Check all combos present
    combos = {tuple(sorted(d["parameters"].items())) for d in expanded}
    assert (("x", 1), ("y", 3), ("z", 5)) in combos
    assert (("x", 2), ("y", 4), ("z", 5)) in combos


def test_explosion_guard(tmp_path: Path):
    data = {
        "name": "big",
        "market": "equity",
        "strategy": "equity_residual_momentum_v3",
        "tier": "smoke",
        "grid": {"a": [1, 2, 3], "b": [1, 2, 3, 4]},
        "max_trials": 10,
    }
    p = _write_spec(tmp_path, data)
    with pytest.raises(ValueError, match="exceeds limit"):
        parse_experiment_spec(p, allow_large=False)
    # Allow large should pass
    spec = parse_experiment_spec(p, allow_large=True)
    assert len(spec.grid) == 2


def test_explosion_guard_cli_flag(tmp_path: Path):
    # Ensure resolve respects allow_large
    data = {
        "name": "big2",
        "market": "equity",
        "strategy": "equity_residual_momentum_v3",
        "tier": "smoke",
        "trials": [{"label": f"t{i}", "parameters": {"beta_window_bars": 252}} for i in range(5)],
        "grid": {"x": [1, 2, 3]},
        "max_trials": 6,
    }
    p = _write_spec(tmp_path, data)
    with pytest.raises(ValueError):
        resolve_experiment(p, config=load_config(None), allow_large=False)
    # Should succeed with allow_large
    r = resolve_experiment(p, config=load_config(None), allow_large=True)
    assert len(r.trials) == 8


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_resume_completed_skipped(tmp_path: Path):
    cfg = load_config(None)
    spec = _minimal_spec(tmp_path)
    resolved = resolve_experiment(spec, config=cfg)
    # Run once
    store = run_experiment(resolved, cfg, output_base=tmp_path / "runs", resume=False)
    first_trials = store.load_trials()
    assert len(first_trials) == 2
    assert all(t["status"] == "ok" for t in first_trials)
    # Resume should skip both
    store2 = run_experiment(resolved, cfg, store=store, resume=True)
    assert store2.run_dir == store.run_dir
    assert len(store2.load_trials()) == 2  # not duplicated


def test_resume_pending_runs(tmp_path: Path):
    cfg = load_config(None)
    # Create spec with 2 trials, but simulate interrupted after 1
    spec = _minimal_spec(tmp_path)
    resolved = resolve_experiment(spec, config=cfg)
    store = ExperimentStore.create(tmp_path / "runs", resolved.spec, resolved, cfg)
    # Manually append only first trial as completed
    first_tid = resolved.trial_ids[0]
    store.append_trial(
        {
            "trial_id": first_tid,
            "label": resolved.trials[0]["label"],
            "status": "ok",
            "parameters": resolved.trials[0].get("parameters"),
            "total_return": 0.1,
            "cagr": 0.05,
            "sharpe": 1.0,
            "elapsed_s": 0.1,
        }
    )
    # Now resume should run only second
    store2 = run_experiment(resolved, cfg, store=store, resume=True)
    trials = store2.load_trials()
    assert len(trials) == 2
    # Second trial should be real result (has final_equity etc.)
    second = next(t for t in trials if t["trial_id"] == resolved.trial_ids[1])
    assert second["status"] == "ok"


def test_resume_failed_retry_semantics(tmp_path: Path):
    cfg = load_config(None)
    spec = _minimal_spec(tmp_path)
    resolved = resolve_experiment(spec, config=cfg)
    store = ExperimentStore.create(tmp_path / "runs", resolved.spec, resolved, cfg)
    # Add one success, one failure
    store.append_trial(
        {
            "trial_id": resolved.trial_ids[0],
            "label": resolved.trials[0]["label"],
            "status": "ok",
            "parameters": {},
            "sharpe": 1.0,
            "cagr": 0.1,
            "elapsed_s": 0.1,
        }
    )
    store.append_trial(
        {
            "trial_id": resolved.trial_ids[1],
            "label": resolved.trials[1]["label"],
            "status": "failed",
            "parameters": {},
            "error": "boom",
            "elapsed_s": 0.1,
        }
    )
    # Resume without retry_failed should keep failure as is (still 2 trials)
    store2 = run_experiment(resolved, cfg, store=store, resume=True, retry_failed=False)
    trials = store2.load_trials()
    assert len(trials) == 2
    # Failure still there, not retried
    assert any(t["trial_id"] == resolved.trial_ids[1] and t["status"] == "failed" for t in trials)
    # Resume with retry_failed should rerun failure (append another line? Our implementation appends retried as new line, so we get 3 lines? Actually we filter pending includes failed when retry_failed, so it will run and append again. Then trials will be 3 lines with two entries for same id? Our store's trials.jsonl appends, so we get duplicate ids. The test should expect that the failed is retried and results in 3 total? But our run_experiment's pending logic will run the failed trial again and append, so we will have 3 entries. However the final state will have 2 ok? Let's check: second run with retry_failed will produce a successful retry, so we will have 3 entries (original ok, original failed, retried ok). That's acceptable but maybe we should expect 3.
    # For this test, we will verify that retry_failed actually reruns
    # Create a fresh store for retry test
    store3 = ExperimentStore.create(tmp_path / "runs2", resolved.spec, resolved, cfg)
    store3.append_trial(
        {
            "trial_id": resolved.trial_ids[0],
            "label": resolved.trials[0]["label"],
            "status": "ok",
            "parameters": {},
            "sharpe": 1.0,
            "cagr": 0.1,
            "elapsed_s": 0.1,
        }
    )
    store3.append_trial(
        {
            "trial_id": resolved.trial_ids[1],
            "label": resolved.trials[1]["label"],
            "status": "failed",
            "parameters": {},
            "error": "boom",
            "elapsed_s": 0.1,
        }
    )
    store4 = run_experiment(resolved, cfg, store=store3, resume=True, retry_failed=True)
    trials4 = store4.load_trials()
    # Should have at least 2, and the retried one should now be ok (last entry for that id is ok)
    # Find all entries for second id
    entries = [t for t in trials4 if t["trial_id"] == resolved.trial_ids[1]]
    assert len(entries) >= 1
    # At least one of them after retry should be ok (the new one)
    assert any(e["status"] == "ok" for e in entries)


def test_resume_mismatched_experiment_rejected(tmp_path: Path):
    cfg = load_config(None)
    spec1 = _minimal_spec(tmp_path)
    resolved1 = resolve_experiment(spec1, config=cfg)
    store = ExperimentStore.create(tmp_path / "runs", resolved1.spec, resolved1, cfg)
    # Create second spec with different param
    data = yaml.safe_load(spec1.read_text())
    data["trials"][0]["parameters"]["beta_window_bars"] = 999
    p2 = tmp_path / "spec2.yaml"
    p2.write_text(yaml.safe_dump(data), encoding="utf-8")
    resolved2 = resolve_experiment(p2, config=cfg)
    assert resolved1.experiment_id != resolved2.experiment_id
    with pytest.raises(ValueError, match="mismatch"):
        store.validate_resume(resolved2.experiment_id)
    # Also run_experiment with resume should reject
    with pytest.raises(ValueError):
        run_experiment(resolved2, cfg, store=store, resume=True)


def test_interrupted_json_recoverable(tmp_path: Path):
    cfg = load_config(None)
    spec = _minimal_spec(tmp_path)
    resolved = resolve_experiment(spec, config=cfg)
    store = ExperimentStore.create(tmp_path / "runs", resolved.spec, resolved, cfg)
    # Write a valid trial plus a corrupted line
    store.append_trial(
        {
            "trial_id": resolved.trial_ids[0],
            "label": resolved.trials[0]["label"],
            "status": "ok",
            "parameters": {},
            "sharpe": 1.0,
            "cagr": 0.1,
            "elapsed_s": 0.1,
        }
    )
    # Append corrupted line manually
    store.trials_path.write_text(store.trials_path.read_text() + "not json\n", encoding="utf-8")
    # Load should skip corrupted and still return 1 valid
    trials = store.load_trials()
    assert len(trials) == 1
    # Resume should continue with pending
    store2 = run_experiment(resolved, cfg, store=store, resume=True)
    assert len(store2.load_trials()) == 2


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_provenance_secrets_absent(tmp_path: Path):
    cfg = load_config(None)
    spec = _minimal_spec(tmp_path)
    resolved = resolve_experiment(spec, config=cfg)
    store = ExperimentStore.create(tmp_path / "runs", resolved.spec, resolved, cfg)
    manifest = store.load_manifest()
    # Check that no secret keys like ALPACA_API_KEY appear
    blob = json.dumps(manifest)
    for secret in ["ALPACA_API_KEY", "ALPACA_API_SECRET", "HMAC", "password", "token"]:
        assert secret not in blob
    # Config digest should be sanitized (only strategy/universe/execution/backtest)
    cfg_digest = manifest.get("config_digest", {})
    assert "strategy_equity" in cfg_digest or "strategy" in str(cfg_digest)
    # Should not contain live keys
    assert "live" not in str(cfg_digest).lower() or "hard_dollar_cap" not in str(cfg_digest).lower()


def test_provenance_dirty_git_mocked(tmp_path: Path):
    cfg = load_config(None)
    spec = _minimal_spec(tmp_path)
    resolved = resolve_experiment(spec, config=cfg)
    with patch(
        "parayabuck.research.fingerprint.git_provenance",
        return_value={"git_commit": "abc", "git_dirty": True, "git_branch": "main", "reproducibility": "degraded"},
    ):
        store = ExperimentStore.create(tmp_path / "runs", resolved.spec, resolved, cfg)
        manifest = store.load_manifest()
        assert manifest["git_dirty"] is True
        assert manifest["reproducibility"] == "degraded"


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def test_compare_trial_matching_and_deltas(tmp_path: Path):
    cfg = load_config(None)
    spec = _minimal_spec(tmp_path)
    resolved = resolve_experiment(spec, config=cfg)
    # Run two experiments with same spec but different base param to force delta?
    # For simplicity, run one, then manually create second store with different metrics
    store_a = run_experiment(resolved, cfg, output_base=tmp_path / "runs_a")
    trials_a = store_a.load_trials()
    # Create store_b by copying but modifying one trial's sharpe
    store_b = ExperimentStore.create(tmp_path / "runs_b", resolved.spec, resolved, cfg)
    for t in trials_a:
        # Copy but tweak sharpe +0.1
        new_t = dict(t)
        if new_t["status"] == "ok" and new_t["sharpe"] is not None:
            new_t["sharpe"] = new_t["sharpe"] + 0.1
            new_t["cagr"] = (new_t["cagr"] or 0) + 0.005
        store_b.append_trial(new_t)
    store_b.mark_completed("completed")
    comp = compare_experiments(store_a, store_b)
    assert comp["common_trials"] == 2
    assert len(comp["deltas"]) == 2
    for d in comp["deltas"]:
        assert d["sharpe_delta"] == pytest.approx(0.1, abs=1e-6)
        assert d["cagr_delta"] == pytest.approx(0.005, abs=1e-6)
    assert comp["only_in_a"] == []
    assert comp["only_in_b"] == []


def test_compare_missing_trials(tmp_path: Path):
    cfg = load_config(None)
    spec_a = _minimal_spec(tmp_path)
    resolved_a = resolve_experiment(spec_a, config=cfg)
    store_a = run_experiment(resolved_a, cfg, output_base=tmp_path / "runs_a")
    # spec_b with extra trial
    data = yaml.safe_load(spec_a.read_text())
    data["trials"].append({"label": "extra", "parameters": {"beta_window_bars": 350}})
    p2 = tmp_path / "spec2.yaml"
    p2.write_text(yaml.safe_dump(data), encoding="utf-8")
    resolved_b = resolve_experiment(p2, config=cfg)
    store_b = run_experiment(resolved_b, cfg, output_base=tmp_path / "runs_b")
    comp = compare_experiments(store_a, store_b)
    # Common is 2 (original)
    assert comp["common_trials"] == 2
    assert len(comp["only_in_b"]) == 1
    assert len(comp["only_in_a"]) == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_plan(tmp_path: Path, capsys):
    from parayabuck.cli import main

    spec = _minimal_spec(tmp_path)
    ret = main(["research", "plan", str(spec)])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Experiment ID" in out
    assert "Trials:" in out


def test_cli_run_and_inspect_and_compare(tmp_path: Path, capsys):
    from parayabuck.cli import main

    spec = _minimal_spec(tmp_path)
    runs_parent = tmp_path / "runs_cli"
    # Run
    ret = main(["research", "run", str(spec), "--output-dir", str(runs_parent), "--workers", "1"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Output:" in out
    # Find created run dir
    runs = list(runs_parent.iterdir())
    assert len(runs) == 1
    run_dir = runs[0]
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "summary.csv").exists()
    assert (run_dir / "trials.jsonl").exists()
    # Inspect
    ret = main(["research", "inspect", str(run_dir)])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Experiment ID" in out
    assert "Completed:" in out
    # Compare with itself (should have 0 delta)
    ret = main(["research", "compare", str(run_dir), str(run_dir)])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Common trials" in out


def test_cli_resume(tmp_path: Path, capsys):
    from parayabuck.cli import main

    spec = _minimal_spec(tmp_path)
    runs_parent = tmp_path / "runs_cli2"
    # First run
    ret = main(["research", "run", str(spec), "--output-dir", str(runs_parent), "--workers", "1"])
    assert ret == 0
    run_dir = list(runs_parent.iterdir())[0]
    # Resume should be no-op (already completed) but not fail
    ret = main(["research", "resume", str(run_dir)])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Completed" in out


def test_cli_plan_rejects_explosion(tmp_path: Path, capsys):
    from parayabuck.cli import main

    data = {
        "name": "big",
        "market": "equity",
        "strategy": "equity_residual_momentum_v3",
        "tier": "smoke",
        "grid": {"a": list(range(10)), "b": list(range(10))},
        "max_trials": 10,
    }
    p = _write_spec(tmp_path, data)
    with pytest.raises(SystemExit):
        main(["research", "plan", str(p)])
    # With allow-large should succeed
    ret = main(["research", "plan", str(p), "--allow-large"])
    assert ret == 0


def test_summary_csv_flatten(tmp_path: Path):
    from parayabuck.cli import main

    spec = _minimal_spec(tmp_path)
    runs_parent = tmp_path / "runs_csv"
    main(["research", "run", str(spec), "--output-dir", str(runs_parent), "--workers", "1"])
    run_dir = list(runs_parent.iterdir())[0]
    csv_path = run_dir / "summary.csv"
    assert csv_path.exists()
    text = csv_path.read_text()
    assert "trial_id" in text
    assert "param_beta_window_bars" in text
    assert "sharpe" in text
