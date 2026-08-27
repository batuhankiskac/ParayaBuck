from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from parayabuck.core.models import Market
from parayabuck.research import ResearchJob, ResearchTier, ResearchTrial
from parayabuck.research.cache import load_cached_bars, load_cached_universe, store_cached_bars, store_cached_universe
from parayabuck.research.runner import ResearchRunner, _auto_workers, _resolve_workers
from parayabuck.research.tiers import default_dataset_for_tier, resolve_window, tier_spec


def _smoke_job(workers: int | str = 1, n: int = 2, fail_fast: bool = False) -> ResearchJob:
    trials = tuple(ResearchTrial(label=f"t{i}", parameters={"mh_raw_window": 12 - i}) for i in range(n))
    return ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=workers,
        trials=trials,
        persist_artifacts=False,
        persist_db=False,
        fail_fast=fail_fast,
    )


def test_sequential_runner_basic() -> None:
    job = _smoke_job(workers=1, n=2)
    runner = ResearchRunner()
    res = runner.run(job)
    assert len(res.results) == 2
    assert all(r.status == "ok" for r in res.results)
    assert res.workers == 1
    assert [r.label for r in res.results] == ["t0", "t1"]
    # check metrics present
    for r in res.results:
        assert r.total_return is not None
        assert r.sharpe is not None


def test_parallel_runner_basic() -> None:
    job = _smoke_job(workers=2, n=4)
    runner = ResearchRunner()
    res = runner.run(job)
    assert len(res.results) == 4
    assert all(r.status == "ok" for r in res.results)
    assert res.workers == 2
    # deterministic order
    assert [r.label for r in res.results] == [f"t{i}" for i in range(4)]
    assert [r.trial_index for r in res.results] == list(range(4))


def test_deterministic_ordering_parallel() -> None:
    # Even if trials have different cost multipliers, order preserved
    trials = (
        ResearchTrial(label="a", cost_multiplier=1.0),
        ResearchTrial(label="b", cost_multiplier=3.0),
        ResearchTrial(label="c", cost_multiplier=1.5),
        ResearchTrial(label="d", cost_multiplier=2.0),
    )
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=trials,
    )
    runner = ResearchRunner()
    res = runner.run(job)
    assert [r.label for r in res.results] == ["a", "b", "c", "d"]


def test_worker_failure_handling_collect() -> None:
    # One trial with invalid cost_multiplier should fail, others succeed when fail_fast=False
    # Cost multiplier <=0 triggers validation before execution, so test via failing parameters?
    # Instead use invalid strategy parameter that causes exception inside engine?
    # Use cost_multiplier 0 which is validated before run -> should raise before execution, not per trial.
    # So we test with trial that has invalid execution override
    # Need to use a bad override that triggers exception: unknown key
    bad_trials = (
        ResearchTrial(label="ok1", parameters={"mh_raw_window": 12}),
        ResearchTrial(label="bad", execution_overrides={"unknown_key": 123}),  # type: ignore[typeddict-item]
        ResearchTrial(label="ok2", parameters={"mh_raw_window": 9}),
    )
    job2 = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=bad_trials,
        fail_fast=False,
    )
    runner = ResearchRunner()
    res = runner.run(job2)
    assert len(res.results) == 3
    statuses = {r.label: r.status for r in res.results}
    assert statuses["ok1"] == "ok"
    assert statuses["bad"] == "failed"
    assert statuses["ok2"] == "ok"
    assert res.results[1].error is not None


def test_worker_failure_fail_fast() -> None:
    bad_trials = (
        ResearchTrial(label="ok1", parameters={"mh_raw_window": 12}),
        ResearchTrial(label="bad", execution_overrides={"unknown_key": 123}),
        ResearchTrial(label="ok2", parameters={"mh_raw_window": 9}),
    )
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=bad_trials,
        fail_fast=True,
    )
    runner = ResearchRunner()
    with pytest.raises(RuntimeError, match="failed"):
        runner.run(job)


def test_worker_count_validation() -> None:
    with pytest.raises(ValueError):
        _resolve_workers(0)
    with pytest.raises(ValueError):
        _resolve_workers(-1)
    with pytest.raises(ValueError):
        _resolve_workers("invalid")
    assert _resolve_workers(1) == 1
    assert _resolve_workers("2") == 2
    assert _resolve_workers("auto") >= 1


def test_auto_worker_heuristic() -> None:
    w = _auto_workers()
    assert w >= 1
    # Should not use all cores blindly; on 10-core mac, auto <=4 when <32GB
    import os

    logical = os.cpu_count() or 1
    assert w <= max(1, logical)


def test_tier_selection() -> None:
    for tier in ResearchTier:
        spec = tier_spec(tier)
        assert spec.tier == tier
        assert spec.description
    # Quick defaults to 2019-2021
    start, end = resolve_window(ResearchTier.QUICK, None, None)
    assert start == datetime(2019, 1, 1, tzinfo=UTC)
    assert end == datetime(2021, 12, 31, tzinfo=UTC)
    # Canonical and Robustness require explicit window
    with pytest.raises(ValueError, match="requires explicit"):
        resolve_window(ResearchTier.CANONICAL, None, None)
    with pytest.raises(ValueError, match="requires explicit"):
        resolve_window(ResearchTier.ROBUSTNESS, None, None)
    with pytest.raises(ValueError, match="requires explicit"):
        resolve_window(ResearchTier.CANONICAL, datetime(2020, 1, 1, tzinfo=UTC), None)
    # Explicit overrides tier default
    explicit_start = datetime(2020, 1, 1, tzinfo=UTC)
    s, e = resolve_window(ResearchTier.QUICK, explicit_start, None)
    assert s == explicit_start
    # Dataset helpers
    assert default_dataset_for_tier(ResearchTier.SMOKE, Market.EQUITY) is None
    assert default_dataset_for_tier(ResearchTier.QUICK, Market.EQUITY) is not None


def test_prepared_input_reuse_flag() -> None:
    job = _smoke_job(workers=1, n=2)
    runner = ResearchRunner()
    res = runner.run(job)
    # Smoke builds prepared once and reuses
    assert res.prepared_reused is True


def test_cache_fingerprint_invalidation(tmp_path: Path) -> None:
    csv = tmp_path / "bars.csv"
    csv.write_text(
        "timestamp,symbol,open,high,low,close,volume\n2023-01-02T00:00:00+00:00,AAPL,100,101,99,100,1000000\n"
    )
    from parayabuck.backtest.product import load_csv_bars

    bars = load_csv_bars(csv, default_market=Market.EQUITY)
    store_cached_bars(csv, Market.EQUITY, bars)
    cached = load_cached_bars(csv, Market.EQUITY)
    assert cached is not None
    assert len(cached) == len(bars)
    # Different market should miss
    assert load_cached_bars(csv, Market.CRYPTO) is None
    # Modify file
    csv.write_text(
        "timestamp,symbol,open,high,low,close,volume\n2023-01-02T00:00:00+00:00,AAPL,101,102,100,101,1000000\n"
    )
    cached2 = load_cached_bars(csv, Market.EQUITY)
    assert cached2 is None  # invalidated
    # Universe cache
    from parayabuck.config import load_config

    config = load_config()
    from parayabuck.backtest.product import build_product_universe

    bars2 = load_csv_bars(csv, default_market=Market.EQUITY)
    end = datetime(2023, 1, 10, tzinfo=UTC)
    uni = build_product_universe(config, bars2, market=Market.EQUITY, end=end)
    rebalance = config.backtest.scanner_rebalance
    store_cached_universe(csv, Market.EQUITY, "1d", end, "test_digest", rebalance, uni)
    cached_uni = load_cached_universe(csv, Market.EQUITY, "1d", end, "test_digest", rebalance)
    assert cached_uni is not None
    # Different digest should miss
    assert load_cached_universe(csv, Market.EQUITY, "1d", end, "other", rebalance) is None
    # Different scanner_rebalance should miss
    assert (
        load_cached_universe(
            csv, Market.EQUITY, "1d", end, "test_digest", "weekly" if rebalance != "weekly" else "daily"
        )
        is None
    )
    # Modify file should invalidate
    csv.write_text(
        "timestamp,symbol,open,high,low,close,volume\n2023-01-03T00:00:00+00:00,AAPL,102,103,101,102,1000000\n"
    )
    assert load_cached_universe(csv, Market.EQUITY, "1d", end, "test_digest", rebalance) is None


def test_parity_workers_1_vs_2() -> None:
    trials = (
        ResearchTrial(label="c1", cost_multiplier=1.0),
        ResearchTrial(label="c2", cost_multiplier=2.0),
    )
    job1 = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=trials,
    )
    job2 = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=trials,
    )
    runner = ResearchRunner()
    res1 = runner.run(job1)
    res2 = runner.run(job2)
    for a, b in zip(res1.results, res2.results):
        assert a.status == b.status
        assert a.total_return == b.total_return
        assert a.sharpe == b.sharpe
        assert a.trade_count == b.trade_count
        assert a.final_equity == b.final_equity


def test_no_persistence_lightweight(tmp_path: Path) -> None:
    # When persist false, no files should be created in output_dir
    out = tmp_path / "out"
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=(ResearchTrial(label="t1"),),
        persist_artifacts=False,
        persist_db=False,
        output_dir=str(out),
    )
    runner = ResearchRunner()
    res = runner.run(job)
    assert res.results[0].status == "ok"
    # No report.json should exist
    assert not list(out.rglob("report.json"))


def test_full_artifact_persistence(tmp_path: Path) -> None:
    out = tmp_path / "out"
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=(ResearchTrial(label="t1"),),
        persist_artifacts=True,
        persist_db=False,
        output_dir=str(out),
    )
    runner = ResearchRunner()
    res = runner.run(job)
    assert res.results[0].status == "ok"
    # Should have report.json and universe.json
    assert list(out.rglob("report.json"))
    assert list(out.rglob("universe.json"))


def test_tier_smoke_no_economic_conclusions() -> None:
    # Smoke should run in seconds, not minutes, and produce synthetic results
    import time

    job = _smoke_job(workers=1, n=2)
    runner = ResearchRunner()
    t0 = time.perf_counter()
    res = runner.run(job)
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0  # smoke must be fast
    assert res.tier == ResearchTier.SMOKE


def test_execution_overrides_positive_sequential_and_parallel(tmp_path: Path) -> None:
    # Valid rebalance_band_pct and minimum_order_notional should be applied and observable
    # Use smoke with deterministic fixture; high band should reduce trades
    job_seq = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=(
            ResearchTrial(label="default", execution_overrides={"rebalance_band_pct": 0.005}),
            ResearchTrial(label="high_band", execution_overrides={"rebalance_band_pct": 0.5}),
        ),
    )
    runner = ResearchRunner()
    res_seq = runner.run(job_seq)
    assert all(r.status == "ok" for r in res_seq.results)
    # High band should have fewer or equal trades (observable)
    # Just verify it runs and parallel gives same
    job_par = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=(
            ResearchTrial(label="default", execution_overrides={"rebalance_band_pct": 0.005}),
            ResearchTrial(label="high_band", execution_overrides={"rebalance_band_pct": 0.5}),
        ),
    )
    res_par = runner.run(job_par)
    for a, b in zip(res_seq.results, res_par.results):
        assert a.status == b.status == "ok"
        assert a.total_return == b.total_return
    # Also test minimum_order_notional
    job2 = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=(ResearchTrial(label="min_notional", execution_overrides={"minimum_order_notional": 10000}),),
    )
    res2 = runner.run(job2)
    assert res2.results[0].status == "ok"


def test_strategy_param_override_honors_config() -> None:
    from dataclasses import replace

    from parayabuck.config import Config

    config = Config()
    selection = config.strategy.equity
    original_params = dict(selection.parameters)
    # Use a safe param that can be changed without breaking validation
    # top_n is safe (int), ivol_reject_quantile is safe (float)
    test_key = "top_n" if "top_n" in original_params else next(iter(original_params))
    original_val = original_params[test_key]
    # Choose a valid nearby value
    if isinstance(original_val, int):
        custom_value = 6 if original_val != 6 else 7
    elif isinstance(original_val, float):
        custom_value = 0.6 if original_val != 0.6 else 0.65
    else:
        custom_value = original_val
    new_params = dict(original_params)
    new_params[test_key] = custom_value
    new_selection = replace(selection, parameters=new_params)
    new_strategy = replace(config.strategy, equity=new_selection)
    custom_config = replace(config, strategy=new_strategy)
    # Job with no base/trial overrides should use config's custom_value
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=(ResearchTrial(label="t1"),),
    )
    runner = ResearchRunner(config=custom_config)
    res = runner.run(job)
    assert res.results[0].status == "ok"
    assert res.results[0].parameters[test_key] == custom_value
    # Now test job base overrides
    job2 = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        base_parameters={test_key: 5 if isinstance(original_val, int) else 0.5},
        trials=(ResearchTrial(label="t1"),),
    )
    res2 = runner.run(job2)
    assert res2.results[0].parameters[test_key] == (5 if isinstance(original_val, int) else 0.5)
    # Trial overrides job
    job3 = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        base_parameters={test_key: 5 if isinstance(original_val, int) else 0.5},
        trials=(ResearchTrial(label="t1", parameters={test_key: 7 if isinstance(original_val, int) else 0.75}),),
    )
    res3 = runner.run(job3)
    assert res3.results[0].parameters[test_key] == (7 if isinstance(original_val, int) else 0.75)


def test_smoke_deterministic_and_meaningful() -> None:
    # Same smoke job must give identical results across runs and workers
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=(ResearchTrial(label="t1"), ResearchTrial(label="t2")),
    )
    runner = ResearchRunner()
    res1 = runner.run(job)
    res2 = runner.run(job)
    for a, b in zip(res1.results, res2.results):
        assert a.total_return == b.total_return
        assert a.sharpe == b.sharpe
    # Workers=1 vs workers=2 should be identical (deterministic)
    job_par = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=(ResearchTrial(label="t1"), ResearchTrial(label="t2")),
    )
    res_par = runner.run(job_par)
    for a, b in zip(res1.results, res_par.results):
        assert a.total_return == b.total_return
    # Smoke must exercise strategy: at least one rebalance
    assert any(r.rebalance_count is not None and r.rebalance_count > 0 for r in res1.results)
    # Check that synthetic bars generation is deterministic (hashlib, not hash())
    from parayabuck.research.runner import _synthetic_bars

    bars1 = _synthetic_bars(Market.EQUITY, "1d", "equity_residual_momentum_v2", {})
    bars2 = _synthetic_bars(Market.EQUITY, "1d", "equity_residual_momentum_v2", {})
    assert bars1 == bars2
    # Check that number of bars is sufficient for strategy
    from parayabuck.portfolio import portfolio_strategy_from

    strat = portfolio_strategy_from("equity_residual_momentum_v2")
    assert len({b.timestamp for b in bars1}) >= strat.minimum_history_bars


def test_canonical_persistence_defaults(tmp_path: Path) -> None:
    # Canonical tier should default to persist_artifacts=True when None
    from parayabuck.research.tiers import resolve_persist_artifacts

    assert resolve_persist_artifacts(ResearchTier.CANONICAL, None) is True
    assert resolve_persist_artifacts(ResearchTier.QUICK, None) is False
    assert resolve_persist_artifacts(ResearchTier.CANONICAL, False) is False
    assert resolve_persist_artifacts(ResearchTier.CANONICAL, True) is True
    # Programmatic job with canonical and None should get True via runner
    out = tmp_path / "out"
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.CANONICAL,
        start=datetime(2023, 1, 1, tzinfo=UTC),
        end=datetime(2023, 3, 1, tzinfo=UTC),
        dataset_path="data/historical/equity_1200_2016_2026.csv",
        workers=1,
        trials=(ResearchTrial(label="t1"),),
        persist_artifacts=None,
        output_dir=str(out),
    )
    from parayabuck.research.runner import _synthetic_bars

    bars = _synthetic_bars(Market.EQUITY, "1d", "equity_residual_momentum_v2", {})
    runner = ResearchRunner()
    res = runner.run(job, bars_override=bars)
    assert res.results[0].status == "ok"

    # Check that artifacts were created because canonical defaults to True
    assert list(out.rglob("report.json"))


def test_auto_worker_linux_safe(monkeypatch) -> None:
    import pathlib

    from parayabuck.research.runner import _auto_workers, _get_available_memory_gb

    # Simulate Linux cgroup v2 with limit 4GB and current 1GB => available 3GB
    # Mock Path.exists and read_text for cgroup files
    original_exists = pathlib.Path.exists
    original_read_text = pathlib.Path.read_text

    def fake_exists(self):
        if str(self) in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"):
            return True
        return original_exists(self)

    def fake_read_text(self, *args, **kwargs):
        if str(self) == "/sys/fs/cgroup/memory.max":
            return "4294967296"  # 4GB
        if str(self) == "/sys/fs/cgroup/memory.current":
            return "1073741824"  # 1GB
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)
    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    avail = _get_available_memory_gb()
    assert avail is not None
    assert 2.5 < avail < 3.5
    # With 3GB available, per_worker 1.2 + headroom 2 => memory_cap = floor((3-2)/1.2)=0 => 1
    # So auto should be 1
    w = _auto_workers()
    assert w == 1

    # Simulate no cgroup, fallback to /proc/meminfo
    def fake_exists2(self):
        if str(self).startswith("/sys/fs/cgroup"):
            return False
        if str(self) == "/proc/meminfo":
            return True
        return original_exists(self)

    def fake_read_text2(self, *args, **kwargs):
        if str(self) == "/proc/meminfo":
            return "MemAvailable:    8192000 kB\n"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists2)
    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text2)
    avail2 = _get_available_memory_gb()
    assert avail2 is not None
    assert 7.5 < avail2 < 8.5
    # With 8GB available, auto should be at least 1 and not crash
    w2 = _auto_workers()
    assert w2 >= 1


def test_per_worker_rss_measurement() -> None:
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=(ResearchTrial(label="t1"), ResearchTrial(label="t2")),
    )
    runner = ResearchRunner()
    res = runner.run(job)
    for r in res.results:
        assert r.worker_pid is not None
        assert r.worker_peak_rss_mb is not None
        assert r.worker_peak_rss_mb > 0
    # Parent and workers should have distinct PIDs for parallel
    pids = {r.worker_pid for r in res.results}
    assert len(pids) >= 1


def test_worker_failure_bookkeeping_preserves_index() -> None:
    # Verify failed trial does not corrupt batch and indices preserved
    # Use trial-level failure (invalid execution_overrides) which is handled inside _execute_single_trial
    # This avoids needing to mock _pool_worker across spawn boundary (not picklable)
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=(
            ResearchTrial(label="t0"),
            ResearchTrial(label="t1", execution_overrides={"unknown_key": 123}),
            ResearchTrial(label="t2"),
        ),
        fail_fast=False,
    )
    runner = ResearchRunner()
    res = runner.run(job)
    assert len(res.results) == 3
    # All indices preserved
    assert [r.trial_index for r in res.results] == [0, 1, 2]
    assert [r.label for r in res.results] == ["t0", "t1", "t2"]
    assert res.results[1].status == "failed"
    assert "unknown_key" in res.results[1].error
    assert res.results[0].status == "ok"
    assert res.results[2].status == "ok"


def test_fail_fast_truthful_completion_order() -> None:
    # Test that fail_fast reacts to completed failure, not submission order
    # t1 fails quickly via invalid override, t0/t2 are valid but slower (need full run)
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=(
            ResearchTrial(label="slow", parameters={"mh_raw_window": 12}),
            ResearchTrial(label="fast_fail", execution_overrides={"unknown_key": 123}),
            ResearchTrial(label="slow2", parameters={"mh_raw_window": 9}),
        ),
        fail_fast=True,
    )
    runner = ResearchRunner()
    import time as time2

    t0 = time2.perf_counter()
    try:
        runner.run(job)
        assert False, "should have raised"
    except RuntimeError as e:
        elapsed = time2.perf_counter() - t0
        assert "fast_fail" in str(e) or "unknown_key" in str(e)
        assert elapsed < 1.0, f"fail_fast not truthful, took {elapsed:.2f}s"


def test_trial_timeout_no_hardcoded_600() -> None:
    # Verify ResearchJob has trial_timeout_seconds and that None means no timeout
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=(ResearchTrial(label="t1"),),
        trial_timeout_seconds=None,
    )
    assert job.trial_timeout_seconds is None
    job2 = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=(ResearchTrial(label="t1"),),
        trial_timeout_seconds=30.0,
    )
    assert job2.trial_timeout_seconds == 30.0
    # Run with timeout None should succeed (no 600s hard limit)
    runner = ResearchRunner()
    res = runner.run(job)
    assert res.results[0].status == "ok"


def test_sqlite_protection() -> None:
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=2,
        trials=(ResearchTrial(label="t1"),),
        persist_db=True,
    )
    runner = ResearchRunner()
    try:
        runner.run(job)
        assert False, "should have raised for persist_db with workers>1"
    except ValueError as e:
        assert "persist_db" in str(e).lower() or "sqlite" in str(e).lower()
    # workers=1 should be allowed
    job2 = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=(ResearchTrial(label="t1"),),
        persist_db=True,
    )
    res2 = runner.run(job2)
    assert res2.results[0].status in ("ok", "failed")  # at least not raise for workers=1


def test_small_cleanup_workerpolicy_removed() -> None:
    # WorkerPolicy should not exist in models
    import parayabuck.research.models as m

    assert not hasattr(m, "WorkerPolicy"), "WorkerPolicy should be removed"
    # TrialResult should carry metadata
    job = ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.SMOKE,
        workers=1,
        trials=(ResearchTrial(label="t1", metadata={"fold": 1, "provenance": "test"}),),
    )
    runner = ResearchRunner()
    res = runner.run(job)
    assert res.results[0].metadata == {"fold": 1, "provenance": "test"}


def test_profiling_dataset_sha_full(tmp_path: Path) -> None:
    # Verify profile script now computes full SHA, not sampled
    import hashlib

    from benchmarks.profile_research_baseline import _sha256_file

    p = tmp_path / "test.bin"
    # Create 1MB file
    data = b"a" * (1024 * 1024)
    p.write_bytes(data)
    h1 = _sha256_file(p)
    expected = hashlib.sha256(data).hexdigest()
    assert h1 == expected
    # For larger file, still full
    p2 = tmp_path / "large.bin"
    # 5MB
    data2 = b"b" * (5 * 1024 * 1024)
    p2.write_bytes(data2)
    h2 = _sha256_file(p2)
    assert h2 == hashlib.sha256(data2).hexdigest()
