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
    # Canonical requires explicit
    start2, end2 = resolve_window(ResearchTier.CANONICAL, None, None)
    assert start2 is None and end2 is None
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
    store_cached_bars(csv, bars)
    cached = load_cached_bars(csv)
    assert cached is not None
    assert len(cached) == len(bars)
    # Modify file
    csv.write_text(
        "timestamp,symbol,open,high,low,close,volume\n2023-01-02T00:00:00+00:00,AAPL,101,102,100,101,1000000\n"
    )
    cached2 = load_cached_bars(csv)
    assert cached2 is None  # invalidated
    # Universe cache
    from parayabuck.config import load_config

    config = load_config()
    from parayabuck.backtest.product import build_product_universe

    # Need at least some bars for universe
    # Use synthetic bars
    bars2 = load_csv_bars(csv, default_market=Market.EQUITY)
    uni = build_product_universe(config, bars2, market=Market.EQUITY, end=datetime(2023, 1, 10, tzinfo=UTC))
    store_cached_universe(csv, Market.EQUITY, "1d", datetime(2023, 1, 10, tzinfo=UTC), "test_digest", uni)
    cached_uni = load_cached_universe(csv, Market.EQUITY, "1d", datetime(2023, 1, 10, tzinfo=UTC), "test_digest")
    assert cached_uni is not None
    # Different digest should miss
    assert load_cached_universe(csv, Market.EQUITY, "1d", datetime(2023, 1, 10, tzinfo=UTC), "other") is None
    # Modify file should invalidate
    csv.write_text(
        "timestamp,symbol,open,high,low,close,volume\n2023-01-03T00:00:00+00:00,AAPL,102,103,101,102,1000000\n"
    )
    assert load_cached_universe(csv, Market.EQUITY, "1d", datetime(2023, 1, 10, tzinfo=UTC), "test_digest") is None


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
