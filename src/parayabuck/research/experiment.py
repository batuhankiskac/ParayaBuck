"""High-level experiment orchestration: resolve, fingerprint, run, resume, inspect, compare."""

from __future__ import annotations

import os
import pickle
import tempfile
import traceback
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.research.models import ResearchJob, ResearchTier, TrialResult
from parayabuck.research.persistence import ExperimentStore

# Reuse runner helpers for invariants
from parayabuck.research.runner import (
    _execute_single_trial,
    _pool_initializer,
    _pool_worker,
    _resolve_workers,
)
from parayabuck.research.spec import ExperimentSpec, parse_experiment_spec, resolved_trials


@dataclass(frozen=True)
class ResolvedExperiment:
    spec: ExperimentSpec
    job: ResearchJob
    experiment_id: str
    run_id: str
    trial_ids: tuple[str, ...]
    trials: tuple[dict[str, Any], ...]
    dataset_fingerprint: dict[str, Any]
    dataset_fingerprint_id: str
    universe_fingerprint: dict[str, Any]
    universe_fingerprint_id: str
    corporate_fingerprint: dict[str, Any]
    config_digest: dict[str, Any]
    config_digest_id: str
    timeframe: str | None
    workers_requested: int | str
    workers_resolved: int
    engine_version: str = "1.0.0"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def _effective_timeframe(
    config: Config, market: Market, strategy_name: str | None, base_params: dict[str, Any] | None
) -> str:
    try:
        from parayabuck.portfolio import portfolio_strategy_from

        strat = portfolio_strategy_from(
            strategy_name or ("equity_residual_momentum_v2" if market == Market.EQUITY else "crypto_low_variance_v2"),
            parameters=base_params or None,
        )
        return str(getattr(strat, "timeframe", "1d"))
    except Exception:
        return "1d"


def resolve_experiment(
    spec_path: str | Path,
    config: Config | None = None,
    workers_override: int | str | None = None,
    allow_large: bool = False,
    base_dir: str | Path | None = None,
) -> ResolvedExperiment:
    """Resolve YAML spec into deterministic ResolvedExperiment."""
    from parayabuck.research.fingerprint import (
        config_digest as cfg_digest_fn,
    )
    from parayabuck.research.fingerprint import (
        config_digest_id,
        corporate_actions_fingerprint,
        dataset_fingerprint,
        dataset_fingerprint_id,
        experiment_fingerprint,
        trial_fingerprint,
        universe_fingerprint,
        universe_fingerprint_id,
    )
    from parayabuck.research.fingerprint import (
        experiment_id as exp_id_fn,
    )
    from parayabuck.research.fingerprint import (
        run_id as gen_run_id,
    )
    from parayabuck.research.fingerprint import (
        trial_id as trial_id_fn,
    )

    spec = parse_experiment_spec(spec_path, allow_large=allow_large)
    cfg = config or load_config(spec.config_path)

    # Resolve workers
    workers_req = workers_override if workers_override is not None else spec.workers
    workers_resolved = _resolve_workers(workers_req)

    # Resolve trials
    trial_dicts = resolved_trials(spec)
    if not trial_dicts:
        trial_dicts = [{"label": "trial_0", "parameters": {}}]

    # Build ResearchTrial objects for job
    from parayabuck.research.spec import trials_to_research_trials

    research_trials = trials_to_research_trials(trial_dicts)

    # Build ResearchJob
    job = ResearchJob(
        market=spec.market,
        strategy_name=spec.strategy,
        dataset_path=spec.dataset,
        start=spec.start,
        end=spec.end,
        base_parameters=spec.base_parameters or None,
        trials=tuple(research_trials),
        tier=spec.tier,
        workers=workers_req,  # keep original for job, resolved separately
        persist_artifacts=None,
        persist_db=None,
        output_dir=spec.output_dir,
        fail_fast=spec.fail_fast,
        corporate_actions_path=spec.corporate_actions,
        initial_cash=spec.initial_cash,
        config_path=spec.config_path,
    )

    # Fingerprints
    dataset_fp = dataset_fingerprint(
        spec.dataset, spec.market, spec.tier.value if isinstance(spec.tier, ResearchTier) else str(spec.tier)
    )
    dataset_fp_id = dataset_fingerprint_id(dataset_fp)

    corporate_fp = corporate_actions_fingerprint(spec.corporate_actions)

    cfg_digest = cfg_digest_fn(cfg)
    cfg_digest_id_str = config_digest_id(cfg)

    timeframe = _effective_timeframe(cfg, spec.market, spec.strategy, spec.base_parameters)

    # Universe fingerprint needs scanner digest
    scanner_digest: str | None = None
    try:
        if spec.market == Market.EQUITY:
            from parayabuck.universe.scanner import scanner_config_digest

            scanner_digest = scanner_config_digest(cfg.universe.equity)
        else:
            from parayabuck.universe.crypto_scanner import crypto_scanner_config_digest

            scanner_digest = crypto_scanner_config_digest(cfg.universe.crypto)
    except Exception:
        scanner_digest = "unknown"

    scanner_rebalance = getattr(cfg.backtest, "scanner_rebalance", None) if hasattr(cfg, "backtest") else None

    universe_fp = universe_fingerprint(dataset_fp, spec.market, timeframe, spec.end, scanner_digest, scanner_rebalance)
    universe_fp_id = universe_fingerprint_id(universe_fp)

    # Trial fingerprints/ids
    trial_fps: list[dict[str, Any]] = []
    trial_ids: list[str] = []
    for td in trial_dicts:
        strat_name = spec.strategy or (
            cfg.strategy.equity.name if spec.market == Market.EQUITY else cfg.strategy.crypto.name
        )
        fp = trial_fingerprint(
            td.get("parameters"),
            td.get("execution_overrides"),
            float(td.get("cost_multiplier", 1.0)),
            td.get("metadata"),
            strat_name,
            td.get("label"),
        )
        trial_fps.append(fp)
        trial_ids.append(
            trial_id_fn(
                td.get("parameters"),
                td.get("execution_overrides"),
                float(td.get("cost_multiplier", 1.0)),
                td.get("metadata"),
                strat_name,
                td.get("label"),
            )
        )

    # Execution assumptions
    exec_assumptions: dict[str, Any] = {}
    try:
        if hasattr(cfg, "execution"):
            exec_assumptions = {
                "rebalance_band_pct": cfg.execution.rebalance_band_pct,
                "minimum_order_notional": cfg.execution.minimum_order_notional,
            }
    except Exception:
        pass

    # Experiment fingerprint
    strat_name_for_fp = spec.strategy
    # Strategy version attempt
    strat_version = None
    try:
        from parayabuck.portfolio import portfolio_strategy_from

        if strat_name_for_fp:
            s = portfolio_strategy_from(strat_name_for_fp, parameters={})
            strat_version = getattr(s, "version", None) or getattr(s, "strategy_version", None)
    except Exception:
        pass

    exp_fp = experiment_fingerprint(
        strategy_name=strat_name_for_fp,
        strategy_version=strat_version,
        market=spec.market,
        dataset_fp=dataset_fp,
        start=spec.start,
        end=spec.end,
        universe_fp=universe_fp,
        corporate_actions_fp=corporate_fp,
        config_digest_dict=cfg_digest,
        tier=spec.tier.value if isinstance(spec.tier, ResearchTier) else str(spec.tier),
        trial_fps=trial_fps,
        execution_assumptions=exec_assumptions,
        base_parameters=spec.base_parameters,
        engine_version="1.0.0",
    )
    exp_id = exp_id_fn(exp_fp)
    run = gen_run_id()

    return ResolvedExperiment(
        spec=spec,
        job=job,
        experiment_id=exp_id,
        run_id=run,
        trial_ids=tuple(trial_ids),
        trials=tuple(trial_dicts),
        dataset_fingerprint=dataset_fp,
        dataset_fingerprint_id=dataset_fp_id,
        universe_fingerprint=universe_fp,
        universe_fingerprint_id=universe_fp_id,
        corporate_fingerprint=corporate_fp,
        config_digest=cfg_digest,
        config_digest_id=cfg_digest_id_str,
        timeframe=timeframe,
        workers_requested=workers_req,
        workers_resolved=workers_resolved,
        engine_version="1.0.0",
    )


# ---------------------------------------------------------------------------
# Invariant preparation (shared with runner)
# ---------------------------------------------------------------------------


def _prepare_invariants(job: ResearchJob, config: Config, enable_cache: bool = True) -> dict[str, Any]:
    """Prepare bars/universe/prepared once, returning cache hit info.

    Mirrors ResearchRunner logic but exposes cache hits for provenance.
    """
    from parayabuck.backtest.product import _prepared_window, build_product_universe, load_csv_bars
    from parayabuck.research.cache import (
        load_cached_bars,
        load_cached_universe,
        store_cached_bars,
        store_cached_universe,
    )
    from parayabuck.research.runner import _synthetic_bars
    from parayabuck.research.tiers import default_dataset_for_tier, resolve_window

    start, end = resolve_window(job.tier, job.start, job.end)

    # Smoke handling
    universe: Any = None
    prepared: Any = None
    bars: list[Bar] | None = None
    universe_reused = False
    prepared_reused = False
    bars_cache_hit = False
    universe_cache_hit = False
    timeframe: str | None = None

    if job.tier == ResearchTier.SMOKE:
        market = job.market
        strat_name = job.strategy_name or (
            config.strategy.equity.name if market == Market.EQUITY else config.strategy.crypto.name
        )
        # Same as runner: derive per trial effective
        from parayabuck.research.runner import _effective_strategy_params

        per_trial_effective: list[dict[str, Any]] = []
        timeframes: set[str] = set()
        histories: list[int] = []
        for trial in job.trials:
            eff = _effective_strategy_params(config, market, strat_name, job.base_parameters, trial.parameters)
            per_trial_effective.append(eff)
            try:
                from parayabuck.portfolio import portfolio_strategy_from

                strat = portfolio_strategy_from(
                    strat_name
                    or ("equity_residual_momentum_v2" if market == Market.EQUITY else "crypto_low_variance_v2"),
                    parameters=eff or None,
                )
                timeframes.add(strat.timeframe)
                histories.append(int(getattr(strat, "minimum_history_bars", 274)))
            except Exception:
                histories.append(274)
                timeframes.add("1d")
        timeframe = sorted(timeframes)[0] if timeframes else "1d"
        max_hist = max(histories) if histories else 274
        idx_max = histories.index(max_hist) if histories else 0
        effective_for_gen = per_trial_effective[idx_max] if per_trial_effective else {}
        bars = _synthetic_bars(market, timeframe, strat_name, effective_for_gen)
        universe = build_product_universe(config, bars, market=market, end=end, timeframe=timeframe)
        prepared = _prepared_window(bars, market, timeframe, start, end)
        prepared_reused = True
        bars_cache_hit = False
        universe_cache_hit = False
    else:
        # Load bars
        cache_csv_path: Path | None = None
        if job.dataset_path:
            csv_path = Path(job.dataset_path)
        else:
            maybe = default_dataset_for_tier(job.tier, job.market)
            if maybe is None:
                raise ValueError("No dataset_path for tier without bars_override")
            csv_path = maybe
        cache_csv_path = csv_path
        if enable_cache and csv_path.exists():
            cached = load_cached_bars(csv_path, job.market)
            if cached is not None:
                bars = cached
                bars_cache_hit = True
            else:
                bars = load_csv_bars(csv_path, default_market=job.market)
                store_cached_bars(csv_path, job.market, bars)
                bars_cache_hit = False
        else:
            if not csv_path.exists():
                raise FileNotFoundError(f"Dataset not found: {csv_path}")
            bars = load_csv_bars(csv_path, default_market=job.market)
            bars_cache_hit = False

        # Determine timeframe
        strat_name = job.strategy_name or (
            config.strategy.equity.name if job.market == Market.EQUITY else config.strategy.crypto.name
        )
        base_params_tmp = job.base_parameters or {}
        try:
            from parayabuck.portfolio import portfolio_strategy_from

            tmp_strat2 = portfolio_strategy_from(strat_name, parameters=base_params_tmp or None)
            timeframe = tmp_strat2.timeframe
        except Exception:
            timeframe = "1d"

        # Universe cache
        if cache_csv_path is not None and enable_cache and cache_csv_path.exists():
            from parayabuck.universe.crypto_scanner import crypto_scanner_config_digest
            from parayabuck.universe.scanner import scanner_config_digest

            try:
                if job.market == Market.EQUITY:
                    digest = scanner_config_digest(config.universe.equity)
                else:
                    digest = crypto_scanner_config_digest(config.universe.crypto)
            except Exception:
                digest = "unknown"
            scanner_rebalance = config.backtest.scanner_rebalance
            universe_cached = load_cached_universe(
                cache_csv_path, job.market, timeframe, end, digest, scanner_rebalance
            )
            if universe_cached is not None:
                universe = universe_cached
                universe_reused = True
                universe_cache_hit = True
            else:
                assert bars is not None
                universe = build_product_universe(config, bars, market=job.market, end=end, timeframe=timeframe)
                store_cached_universe(cache_csv_path, job.market, timeframe, end, digest, scanner_rebalance, universe)
                universe_reused = False
                universe_cache_hit = False
        else:
            assert bars is not None
            universe = build_product_universe(config, bars, market=job.market, end=end, timeframe=timeframe)
            universe_reused = False

        assert bars is not None
        prepared = _prepared_window(bars, job.market, timeframe, start, end)
        prepared_reused = True

    assert bars is not None
    assert universe is not None
    assert prepared is not None

    return {
        "bars": bars,
        "universe": universe,
        "prepared": prepared,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "bars_cache_hit": bars_cache_hit,
        "universe_cache_hit": universe_cache_hit,
        "prepared_reused": prepared_reused,
        "universe_reused": universe_reused,
    }


# ---------------------------------------------------------------------------
# Experiment execution
# ---------------------------------------------------------------------------


def _trial_result_to_dict(res: TrialResult, trial_id: str, trial_dict: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "trial_id": trial_id,
        "label": res.label,
        "status": res.status,
        "parameters": res.parameters,
        "cost_multiplier": res.cost_multiplier,
        "execution_overrides": trial_dict.get("execution_overrides"),
        "metadata": res.metadata,
        "total_return": res.total_return,
        "cagr": res.cagr,
        "sharpe": res.sharpe,
        "sortino": res.sortino,
        "max_drawdown": res.max_drawdown,
        "calmar": res.calmar,
        "turnover": res.turnover,
        "costs": res.costs,
        "fee_total": res.fee_total,
        "trade_count": res.trade_count,
        "fill_count": res.fill_count,
        "rebalance_count": res.rebalance_count,
        "final_equity": res.final_equity,
        "elapsed_s": res.elapsed_s,
        "error": res.error,
        "worker_pid": res.worker_pid,
        "worker_peak_rss_mb": res.worker_peak_rss_mb,
        "trial_index": res.trial_index,
    }


def run_experiment(
    resolved: ResolvedExperiment,
    config: Config,
    output_base: str | Path | None = None,
    store: ExperimentStore | None = None,
    resume: bool = False,
    retry_failed: bool = False,
    enable_cache: bool = True,
) -> ExperimentStore:
    """Execute resolved experiment with parent-owned crash-safe persistence.

    If store is None, creates new bundle under output_base/research_runs.
    If resume=True, expects store to be existing (or output_base is run_dir).
    """
    # Determine store
    if store is None:
        if resume:
            # output_base is run_dir
            if output_base is None:
                raise ValueError("resume requires run directory")
            store = ExperimentStore.load(Path(output_base))
            # Validate
            store.validate_resume(resolved.experiment_id)
        else:
            # Create new
            base = output_base if output_base is not None else None
            # If spec has output_dir, it overrides base? Use resolved.spec.output_dir if set
            # Priority: output_base arg > spec.output_dir > default research_runs
            if resolved.spec.output_dir and output_base is None:
                # If spec output_dir is absolute, use its parent? For now treat output_base as parent
                # Spec's output_dir is intended as run_dir, not parent. Simplify: if spec output_dir, use it directly
                # But if spec output_dir is present, we should create store at that path (if it's a dir)
                spec_out = Path(resolved.spec.output_dir)
                if spec_out.exists() and (spec_out / "manifest.json").exists():
                    # It's an existing run_dir, treat as resume check
                    store = ExperimentStore.load(spec_out)
                    store.validate_resume(resolved.experiment_id)
                else:
                    # Create under spec_out parent? For now, let create handle
                    base = spec_out.parent if spec_out.suffix else spec_out
                    # If base is file-like, use its parent
                    store = ExperimentStore.create(base, resolved.spec, resolved, config)
                    # If spec_out was intended as exact dir, rename? Simpler: if spec_out not exists, use create's generated dir
                    # We'll keep generated dir, not spec_out exact, to avoid confusion
                    pass
            if store is None:
                store = ExperimentStore.create(base, resolved.spec, resolved, config)
    else:
        if resume:
            store.validate_resume(resolved.experiment_id)

    # Prepare invariants once (record cache hits)
    inv = _prepare_invariants(resolved.job, config, enable_cache=enable_cache)
    bars = inv["bars"]
    universe = inv["universe"]
    prepared = inv["prepared"]
    timeframe = inv["timeframe"]
    start = inv["start"]
    end = inv["end"]

    # Update manifest with invariants info
    try:
        store.update_manifest(
            {
                "timeframe": timeframe,
                "bars_cache_hit": inv["bars_cache_hit"],
                "universe_cache_hit": inv["universe_cache_hit"],
                "prepared_reused": inv["prepared_reused"],
                "universe_reused": inv["universe_reused"],
                "numeric_history_enabled": True,
            }
        )
    except Exception:
        pass

    # Determine pending trials
    existing_trials = store.load_trials()
    existing_by_id: dict[str, dict[str, Any]] = {
        str(t.get("trial_id")): t for t in existing_trials if t.get("trial_id") is not None
    }
    # Map trial_id -> idx in resolved
    pending_indices: list[int] = []
    for idx, tid in enumerate(resolved.trial_ids):
        existing = existing_by_id.get(tid)
        if existing is None:
            pending_indices.append(idx)
        else:
            status = existing.get("status")
            if status == "ok":
                continue  # completed, skip
            if status == "failed":
                if retry_failed:
                    pending_indices.append(idx)
                else:
                    continue
            else:
                # Unknown or pending, run
                pending_indices.append(idx)

    # If nothing pending, mark completed and return
    if not pending_indices:
        store.mark_completed("completed")
        return store

    # Corporate actions
    corporate_actions = None
    if resolved.job.corporate_actions_path:
        from parayabuck.backtest.product import load_csv_corporate_actions

        try:
            corporate_actions = load_csv_corporate_actions(resolved.job.corporate_actions_path)
        except Exception:
            corporate_actions = None

    # Resolve workers for execution
    requested_workers = resolved.workers_resolved
    workers = min(requested_workers, len(pending_indices)) if pending_indices else requested_workers

    # Need to decide execution path
    # Use same logic as runner: persist flags
    from parayabuck.research.tiers import resolve_persist_artifacts, resolve_persist_db

    persist_artifacts = resolve_persist_artifacts(resolved.job.tier, resolved.job.persist_artifacts)
    persist_db = resolve_persist_db(resolved.job.tier, resolved.job.persist_db)
    if persist_db and workers > 1:
        raise ValueError("persist_db with workers>1 not allowed")

    # Execute pending
    if workers == 1:
        for idx in pending_indices:
            trial_dict = resolved.trials[idx]
            trial_id = resolved.trial_ids[idx]
            # Build ResearchTrial
            from parayabuck.research.spec import trials_to_research_trials

            # Instead create single trial
            rt = trials_to_research_trials([trial_dict])[0]
            res = _execute_single_trial(
                config,
                bars,
                universe,
                prepared,
                resolved.job.market,
                resolved.job.strategy_name,
                resolved.job.base_parameters,
                rt,
                start,
                end,
                resolved.job.initial_cash,
                persist_db,
                persist_artifacts,
                None,
                corporate_actions,
            )
            res = replace(res, trial_index=idx)
            # Check fail_fast
            if res.status == "failed" and resolved.job.fail_fast:
                # Persist failure then raise
                d = _trial_result_to_dict(res, trial_id, trial_dict)
                store.append_trial(d)
                store.mark_completed("failed")
                raise RuntimeError(f"Trial {res.label} failed (fail_fast): {res.error}")
            d = _trial_result_to_dict(res, trial_id, trial_dict)
            store.append_trial(d)
    else:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor, as_completed

        ctx = multiprocessing.get_context("spawn")
        invariants = (
            config,
            universe,
            prepared,
            resolved.job.market,
            resolved.job.strategy_name,
            resolved.job.base_parameters,
            start,
            end,
            resolved.job.initial_cash,
            persist_db,
            persist_artifacts,
            None,
            corporate_actions,
        )
        fd, tmp_name = tempfile.mkstemp(suffix=".pkl", prefix="parayabuck_invariants_")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            with tmp_path.open("wb") as f:
                pickle.dump(invariants, f, protocol=4)
            # Build payloads for pending only
            from parayabuck.research.spec import trials_to_research_trials

            payloads: list[tuple[Any, int]] = []
            for idx in pending_indices:
                td = resolved.trials[idx]
                rt = trials_to_research_trials([td])[0]
                payloads.append((rt, idx))
            # Map idx-> trial dict/id for later
            idx_to_trial = {idx: (resolved.trials[idx], resolved.trial_ids[idx]) for idx in pending_indices}
            results_by_idx: dict[int, TrialResult] = {}
            with ProcessPoolExecutor(
                max_workers=workers,
                mp_context=ctx,
                initializer=_pool_initializer,
                initargs=(str(tmp_path),),
            ) as executor:
                future_to_idx = {executor.submit(_pool_worker, p): p[1] for p in payloads}
                future_to_payload = {fut: p for fut, p in zip(future_to_idx.keys(), payloads)}
                for fut in as_completed(future_to_idx):
                    idx = future_to_idx[fut]
                    payload = future_to_payload[fut]
                    trial, expected_idx = payload
                    try:
                        res = fut.result()
                    except Exception as exc:
                        tb = traceback.format_exc()
                        effective = {}
                        try:
                            from parayabuck.research.runner import _effective_strategy_params

                            effective = _effective_strategy_params(
                                config,
                                resolved.job.market,
                                resolved.job.strategy_name,
                                resolved.job.base_parameters,
                                trial.parameters,
                            )
                        except Exception:
                            effective = trial.parameters or {}
                        res = TrialResult(
                            trial_index=expected_idx,
                            label=trial.label,
                            status="failed",
                            parameters=effective,
                            cost_multiplier=trial.cost_multiplier,
                            elapsed_s=0.0,
                            error=f"Worker exception: {exc}\n{tb}",
                            metadata=dict(trial.metadata) if trial.metadata else None,
                        )
                    if res.trial_index == -1:
                        res = replace(res, trial_index=expected_idx)
                    results_by_idx[expected_idx] = res
                    # Parent-owned persistence immediately
                    trial_dict, trial_id = idx_to_trial[expected_idx]
                    d = _trial_result_to_dict(res, trial_id, trial_dict)
                    store.append_trial(d)
                    if res.status == "failed" and resolved.job.fail_fast:
                        for fut2 in future_to_idx:
                            fut2.cancel()
                        executor.shutdown(wait=False, cancel_futures=True)
                        store.mark_completed("failed")
                        raise RuntimeError(f"Trial {res.label} failed (fail_fast): {res.error}")
            # Ensure ordering? Already appended in completion order, but summary will be sorted? Keep as is.
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    # Mark completed
    store.mark_completed("completed")
    return store


def plan_experiment(resolved: ResolvedExperiment, inv: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return plan dict without running trials, for dry-run."""
    # If inv not provided, try to estimate cache hits without loading bars? We can peek cache.
    # For now, if inv is None, we will attempt to check cache files existence
    bars_hit = None
    universe_hit = None
    if inv is not None:
        bars_hit = inv.get("bars_cache_hit")
        universe_hit = inv.get("universe_cache_hit")
    else:
        # Quick peek
        try:
            if resolved.spec.tier != ResearchTier.SMOKE and resolved.spec.dataset:
                from pathlib import Path

                p = Path(resolved.spec.dataset)
                if p.exists():
                    from parayabuck.research.cache import CACHE_ROOT

                    # Approximate
                    bars_hit = (CACHE_ROOT / "bars").exists()
                    universe_hit = (CACHE_ROOT / "universe").exists()
        except Exception:
            pass

    return {
        "schema_version": 1,
        "experiment_id": resolved.experiment_id,
        "run_id": resolved.run_id,
        "name": resolved.spec.name,
        "strategy": resolved.spec.strategy,
        "market": str(resolved.spec.market.value if hasattr(resolved.spec.market, "value") else resolved.spec.market),
        "tier": str(resolved.spec.tier.value if hasattr(resolved.spec.tier, "value") else resolved.spec.tier),
        "dataset": resolved.spec.dataset,
        "dataset_fingerprint_id": resolved.dataset_fingerprint_id,
        "universe_fingerprint_id": resolved.universe_fingerprint_id,
        "timeframe": resolved.timeframe,
        "window": {
            "start": str(resolved.spec.start) if resolved.spec.start else None,
            "end": str(resolved.spec.end) if resolved.spec.end else None,
        },
        "workers_requested": resolved.workers_requested,
        "workers_resolved": resolved.workers_resolved,
        "trial_count": len(resolved.trials),
        "trials": [
            {"trial_id": tid, "label": td.get("label"), "parameters": td.get("parameters")}
            for tid, td in zip(resolved.trial_ids, resolved.trials)
        ],
        "bars_cache_hit": bars_hit,
        "universe_cache_hit": universe_hit,
        "numeric_history": "reuse" if resolved.timeframe else "build",
        "baseline": resolved.spec.baseline,
        "tags": resolved.spec.tags,
        "max_trials": resolved.spec.max_trials,
    }


def compare_experiments(store_a: ExperimentStore, store_b: ExperimentStore) -> dict[str, Any]:
    """Compare two experiment runs, matching by trial_id."""
    man_a = store_a.load_manifest()
    man_b = store_b.load_manifest()
    trials_a: dict[str, dict[str, Any]] = {
        str(t.get("trial_id")): t for t in store_a.load_trials() if t.get("trial_id") is not None
    }
    trials_b: dict[str, dict[str, Any]] = {
        str(t.get("trial_id")): t for t in store_b.load_trials() if t.get("trial_id") is not None
    }
    # Also try matching by label if trial_id missing? Use trial_id primary
    common_ids = set(trials_a.keys()) & set(trials_b.keys())
    # Also match by label for convenience if ids differ but label same? Spec says match where possible.
    # We'll also add label-based matching for trials with same label but different ids? Not needed.
    deltas: list[dict[str, Any]] = []
    for tid in sorted(common_ids):
        a = trials_a[tid]
        b = trials_b[tid]
        # Only compare ok trials
        if a.get("status") != "ok" or b.get("status") != "ok":
            continue

        def delta(key: str) -> float | None:
            av = a.get(key)
            bv = b.get(key)
            if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
                return float(bv) - float(av)
            return None

        deltas.append(
            {
                "trial_id": tid,
                "label": a.get("label"),
                "cagr_delta": delta("cagr"),
                "sharpe_delta": delta("sharpe"),
                "sortino_delta": delta("sortino"),
                "max_drawdown_delta": delta("max_drawdown"),
                "turnover_delta": delta("turnover"),
                "costs_delta": delta("costs"),
                "fill_count_delta": (int(b.get("fill_count")) - int(a.get("fill_count")))  # type: ignore[arg-type]
                if isinstance(a.get("fill_count"), int) and isinstance(b.get("fill_count"), int)
                else None,
                "total_return_delta": delta("total_return"),
                "calmar_delta": delta("calmar"),
                "final_equity_delta": delta("final_equity"),
                "a": {
                    k: a.get(k)
                    for k in [
                        "cagr",
                        "sharpe",
                        "sortino",
                        "max_drawdown",
                        "turnover",
                        "costs",
                        "fill_count",
                        "elapsed_s",
                    ]
                },
                "b": {
                    k: b.get(k)
                    for k in [
                        "cagr",
                        "sharpe",
                        "sortino",
                        "max_drawdown",
                        "turnover",
                        "costs",
                        "fill_count",
                        "elapsed_s",
                    ]
                },
            }
        )
    # Missing
    only_a = sorted(set(trials_a.keys()) - set(trials_b.keys()))
    only_b = sorted(set(trials_b.keys()) - set(trials_a.keys()))
    return {
        "schema_version": 1,
        "experiment_a": man_a.get("experiment_id"),
        "experiment_b": man_b.get("experiment_id"),
        "run_a": man_a.get("run_id"),
        "run_b": man_b.get("run_id"),
        "common_trials": len(common_ids),
        "deltas": deltas,
        "only_in_a": only_a,
        "only_in_b": only_b,
    }
