"""Research runner: invariant reuse + controlled multiprocessing."""

from __future__ import annotations

import os
import time
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.product import (
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    run_product_backtest,
)
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from

from .cache import load_cached_bars, load_cached_universe, store_cached_bars, store_cached_universe
from .models import ResearchBatchResult, ResearchJob, ResearchTier, ResearchTrial, TrialResult
from .tiers import resolve_window, tier_spec

# Worker globals for file-based initializer (spawn-safe)
_W_CONFIG: Config | None = None
_W_UNIVERSE: Any = None
_W_PREPARED: Any = None
_W_MARKET: Market | None = None
_W_STRATEGY_NAME: str | None = None
_W_BASE_PARAMS: dict[str, object] | None = None
_W_START: datetime | None = None
_W_END: datetime | None = None
_W_INITIAL_CASH: float | None = None
_W_PERSIST: bool = False
_W_PERSIST_ARTIFACTS: bool = False
_W_OUTPUT_DIR: str | None = None
_W_CORPORATE_ACTIONS: Any = None


def _pool_initializer(path: str) -> None:
    """Load invariants once per worker from temp file."""
    import pickle
    from pathlib import Path

    global _W_CONFIG, _W_UNIVERSE, _W_PREPARED, _W_MARKET, _W_STRATEGY_NAME, _W_BASE_PARAMS
    global _W_START, _W_END, _W_INITIAL_CASH, _W_PERSIST, _W_PERSIST_ARTIFACTS, _W_OUTPUT_DIR, _W_CORPORATE_ACTIONS
    with Path(path).open("rb") as f:
        (
            _W_CONFIG,
            _W_UNIVERSE,
            _W_PREPARED,
            _W_MARKET,
            _W_STRATEGY_NAME,
            _W_BASE_PARAMS,
            _W_START,
            _W_END,
            _W_INITIAL_CASH,
            _W_PERSIST,
            _W_PERSIST_ARTIFACTS,
            _W_OUTPUT_DIR,
            _W_CORPORATE_ACTIONS,
        ) = pickle.load(f)


def _pool_worker(payload: tuple[ResearchTrial, int]) -> TrialResult:
    trial, idx = payload
    assert _W_CONFIG is not None
    assert _W_MARKET is not None
    assert _W_UNIVERSE is not None
    assert _W_PREPARED is not None
    res = _execute_single_trial(
        _W_CONFIG,
        [],
        _W_UNIVERSE,
        _W_PREPARED,
        _W_MARKET,
        _W_STRATEGY_NAME,
        _W_BASE_PARAMS,
        trial,
        _W_START,
        _W_END,
        _W_INITIAL_CASH,
        _W_PERSIST,
        _W_PERSIST_ARTIFACTS,
        _W_OUTPUT_DIR,
        _W_CORPORATE_ACTIONS,
    )
    return replace(res, trial_index=idx)


def _resolve_workers(workers: int | str) -> int:
    if isinstance(workers, int):
        if workers < 1:
            raise ValueError("workers must be >=1")
        return workers
    if isinstance(workers, str):
        w = workers.strip().lower()
        if w == "auto":
            return _auto_workers()
        try:
            iv = int(w)
            if iv < 1:
                raise ValueError()
            return iv
        except Exception:
            raise ValueError(f"Invalid workers value: {workers!r}") from None
    raise ValueError(f"Invalid workers value: {workers!r}")


def _auto_workers() -> int:
    """Conservative auto: leave headroom, cap by RAM."""
    logical = os.cpu_count() or 1
    base = max(1, logical - 1)
    available_gb = 8.0
    try:
        import psutil

        available_gb = psutil.virtual_memory().available / (1024**3)
    except Exception:
        try:
            import subprocess

            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout.strip()
            total_gb = int(out) / (1024**3)
            available_gb = total_gb * 0.6
        except Exception:
            available_gb = 8.0
    per_worker_gb = 1.2
    headroom_gb = 2.0
    if available_gb <= headroom_gb:
        return 1
    memory_cap = max(1, int((available_gb - headroom_gb) // per_worker_gb))
    if available_gb < 32:
        memory_cap = min(memory_cap, 4)
    else:
        memory_cap = min(memory_cap, 8)
    return max(1, min(base, memory_cap))


def _synthetic_bars(market: Market, timeframe: str = "1d") -> list[Bar]:
    bars: list[Bar] = []
    start = datetime(2023, 1, 1, tzinfo=UTC)
    symbols = [f"S{i:03d}" for i in range(10)]
    symbols.append("SPY" if market == Market.EQUITY else "BTC/USD")
    for sym in symbols:
        for i in range(60):
            ts = start + _duration_for_timeframe(timeframe) * i
            if market == Market.EQUITY and ts.weekday() >= 5:
                continue
            price = 100.0 + (hash(f"{sym}{i}") % 1000) / 100.0
            bars.append(
                Bar(
                    timestamp=ts,
                    symbol=sym,
                    timeframe=timeframe,
                    open=price,
                    high=price * 1.01,
                    low=price * 0.99,
                    close=price,
                    volume=1_000_000.0,
                    market=market,
                )
            )
    return bars


def _duration_for_timeframe(timeframe: str) -> Any:
    from datetime import timedelta

    if timeframe == "1d":
        return timedelta(days=1)
    if timeframe == "1h":
        return timedelta(hours=1)
    return timedelta(days=1)


def _apply_execution_overrides(config: Config, overrides: dict[str, object] | None) -> Config:  # type: ignore[return]
    if not overrides:
        return config
    allowed = {"rebalance_band_pct", "minimum_order_notional"}
    for k in overrides:
        if k not in allowed:
            raise ValueError(f"Unknown execution override: {k}")
    exec_cfg = config.execution
    if "rebalance_band_pct" in overrides:
        exec_cfg = replace(exec_cfg, rebalance_band_pct=float(overrides["rebalance_band_pct"]))  # type: ignore[arg-type]
    if "minimum_order_notional" in overrides:
        exec_cfg = replace(exec_cfg, minimum_order_notional=float(overrides["minimum_order_notional"]))  # type: ignore[arg-type]


def _execute_single_trial(
    config: Config,
    bars: list[Bar],
    universe: Any,
    prepared: tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]],
    market: Market,
    strategy_name: str | None,
    base_params: dict[str, object] | None,
    trial: ResearchTrial,
    start: datetime | None,
    end: datetime | None,
    initial_cash: float | None,
    persist: bool,
    persist_artifacts: bool,
    output_dir: str | None,
    corporate_actions: list[Any] | None,
) -> TrialResult:
    params = dict(base_params or {})
    if trial.parameters:
        params.update(trial.parameters)
    t0 = time.perf_counter()
    try:
        trial_config = _apply_execution_overrides(config, trial.execution_overrides)
        # When prepared is provided, bars can be empty; run_product_backtest will use prepared
        effective_bars = bars if bars else []
        # For engine, if prepared provided, bars content not used beyond universe check
        # We pass effective_bars but if empty and prepared provided, run will succeed (it uses prepared)
        # However run_product_backtest expects bars list for universe fallback; we already have universe
        # So passing empty list is okay when prepared and universe are provided
        # But to satisfy type, we need at least not None; we pass bars or empty
        # We'll pass bars if not empty else prepared[0] + prepared[1] as dummy
        if not effective_bars and prepared is not None:
            # Use filtered+warmup as dummy bars to satisfy function signature without adding cost
            # Actually run will ignore bars when prepared is not None except for universe check
            effective_bars = prepared[0] + prepared[1]
        run = run_product_backtest(
            trial_config,
            effective_bars,
            market=market,
            strategy_name=strategy_name,
            parameters=params or None,
            start=start,
            end=end,
            initial_cash=initial_cash,
            output_dir=output_dir,
            cost_multiplier=trial.cost_multiplier,
            persist=persist,
            persist_artifacts=persist_artifacts,
            corporate_actions=corporate_actions,
            universe=universe,
            prepared_inputs=prepared,
        )
        elapsed = time.perf_counter() - t0
        result = run.result
        report = run.report
        summary = report.summary
        turnover_val = getattr(result, "turnover", None)
        if turnover_val is None:
            turnover_val = summary.get("turnover") or summary.get("expected_turnover")
        try:
            costs_val = float(result.fee_total + result.slippage_cost_total + result.spread_cost_total)
        except Exception:
            costs_val = None
        return TrialResult(
            trial_index=-1,
            label=trial.label,
            status="ok",
            parameters=params,
            cost_multiplier=trial.cost_multiplier,
            elapsed_s=elapsed,
            total_return=result.total_return,
            cagr=result.cagr,
            sharpe=result.sharpe,
            sortino=result.sortino,
            max_drawdown=result.max_drawdown,
            calmar=result.calmar,
            turnover=float(turnover_val) if isinstance(turnover_val, (int, float)) else None,
            costs=costs_val,
            fee_total=float(result.fee_total) if hasattr(result, "fee_total") else None,
            trade_count=int(summary.get("number_of_trades", 0))  # type: ignore[call-overload]
            if isinstance(summary.get("number_of_trades"), int)
            else None,
            fill_count=int(len(result.fills)) if hasattr(result, "fills") else None,
            rebalance_count=int(result.rebalance_count) if hasattr(result, "rebalance_count") else None,
            final_equity=float(result.ending_equity) if hasattr(result, "ending_equity") else None,
            error=None,
            summary=dict(summary),
        )
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        tb = traceback.format_exc()
        return TrialResult(
            trial_index=-1,
            label=trial.label,
            status="failed",
            parameters=params,
            cost_multiplier=trial.cost_multiplier,
            elapsed_s=elapsed,
            error=f"{type(exc).__name__}: {exc}\n{tb}",
            summary=None,
        )


def _trial_worker_payload(args: tuple[Any, ...]) -> TrialResult:
    (
        config,
        universe,
        prepared,
        market,
        strategy_name,
        base_params,
        trial,
        start,
        end,
        initial_cash,
        persist,
        persist_artifacts,
        output_dir,
        idx,
        corporate_actions,
    ) = args
    # bars not needed when prepared provided; pass empty
    res = _execute_single_trial(
        config,
        [],
        universe,
        prepared,
        market,
        strategy_name,
        base_params,
        trial,
        start,
        end,
        initial_cash,
        persist,
        persist_artifacts,
        output_dir,
        corporate_actions,
    )
    return replace(res, trial_index=idx)


class ResearchRunner:
    """Reusable research execution layer."""

    def __init__(self, config: Config | None = None, enable_cache: bool = True) -> None:
        self._config = config
        self._enable_cache = enable_cache

    def run(self, job: ResearchJob, bars_override: list[Bar] | None = None) -> ResearchBatchResult:
        start_time = time.perf_counter()
        workers = _resolve_workers(job.workers)
        start, end = resolve_window(job.tier, job.start, job.end)
        if not job.trials:
            raise ValueError("ResearchJob must have at least one trial")
        for tr in job.trials:
            if tr.cost_multiplier <= 0:
                raise ValueError(f"Trial {tr.label} has non-positive cost_multiplier")
        spec = tier_spec(job.tier)
        if spec.max_trials_recommended is not None and len(job.trials) > spec.max_trials_recommended:
            print(f"Warning: tier {job.tier} recommends <= {spec.max_trials_recommended} trials, got {len(job.trials)}")
        config = self._config or load_config(job.config_path)
        # Prepare invariants
        universe: Any = None
        prepared: Any = None
        bars: list[Bar] | None = bars_override
        universe_reused = False
        prepared_reused = False
        cache_csv_path: Path | None = None
        if job.tier == ResearchTier.SMOKE and bars_override is None:
            market = job.market
            strat_name = job.strategy_name or (
                config.strategy.equity.name if market == Market.EQUITY else config.strategy.crypto.name
            )
            smoke_base = job.base_parameters or {}
            tmp_strat = portfolio_strategy_from(strat_name, parameters=smoke_base or None)
            timeframe = tmp_strat.timeframe
            bars = _synthetic_bars(market, timeframe)
            universe = build_product_universe(config, bars, market=market, end=end, timeframe=timeframe)
            prepared = _prepared_window(bars, market, timeframe, start, end)
            prepared_reused = True
        else:
            # Load bars if not provided
            if bars is None:
                if job.dataset_path:
                    csv_path = Path(job.dataset_path)
                else:
                    from .tiers import default_dataset_for_tier

                    maybe = default_dataset_for_tier(job.tier, job.market)
                    if maybe is None:
                        raise ValueError("No dataset_path for tier without bars_override")
                    csv_path = maybe
                cache_csv_path = csv_path
                if self._enable_cache and csv_path.exists():
                    cached = load_cached_bars(csv_path)
                    if cached is not None:
                        bars = cached
                    else:
                        bars = load_csv_bars(csv_path, default_market=job.market)
                        store_cached_bars(csv_path, bars)
                else:
                    if not csv_path.exists():
                        raise FileNotFoundError(f"Dataset not found: {csv_path}")
                    bars = load_csv_bars(csv_path, default_market=job.market)
            else:
                # bars provided explicitly
                if job.dataset_path:
                    cache_csv_path = Path(job.dataset_path)
                else:
                    cache_csv_path = None
            # Build universe and prepared
            strat_name = job.strategy_name or (
                config.strategy.equity.name if job.market == Market.EQUITY else config.strategy.crypto.name
            )
            base_params_tmp = job.base_parameters or {}
            tmp_strat2 = portfolio_strategy_from(strat_name, parameters=base_params_tmp or None)
            timeframe = tmp_strat2.timeframe
            # Universe cache
            if cache_csv_path is not None and self._enable_cache and cache_csv_path.exists():
                from parayabuck.universe.scanner import scanner_config_digest

                try:
                    if job.market == Market.EQUITY:
                        digest = scanner_config_digest(config.universe.equity)
                    else:
                        import hashlib
                        import json

                        payload = json.dumps(str(config.universe.crypto), sort_keys=True).encode()
                        digest = hashlib.sha256(payload).hexdigest()
                except Exception:
                    digest = "unknown"
                universe_cached = load_cached_universe(cache_csv_path, job.market, timeframe, end, digest)
                if universe_cached is not None:
                    universe = universe_cached
                    universe_reused = True
                else:
                    universe = build_product_universe(config, bars, market=job.market, end=end, timeframe=timeframe)
                    store_cached_universe(cache_csv_path, job.market, timeframe, end, digest, universe)
            else:
                universe = build_product_universe(config, bars, market=job.market, end=end, timeframe=timeframe)
            prepared = _prepared_window(bars, job.market, timeframe, start, end)
            prepared_reused = True
        # Corporate actions
        corporate_actions = None
        if job.corporate_actions_path:
            from parayabuck.backtest.product import load_csv_corporate_actions

            corporate_actions = load_csv_corporate_actions(job.corporate_actions_path)
        market = job.market
        strategy_name = job.strategy_name
        base_params: dict[str, object] | None = job.base_parameters
        persist = job.persist_db
        persist_artifacts = job.persist_artifacts
        results: list[TrialResult] = []
        assert bars is not None, "bars not prepared"
        assert universe is not None, "universe not prepared"
        assert prepared is not None, "prepared not prepared"
        if workers == 1:
            for idx, trial in enumerate(job.trials):
                res = _execute_single_trial(
                    config,
                    bars,
                    universe,
                    prepared,
                    market,
                    strategy_name,
                    base_params,
                    trial,
                    start,
                    end,
                    job.initial_cash,
                    persist,
                    persist_artifacts,
                    job.output_dir,
                    corporate_actions,
                )
                res = replace(res, trial_index=idx)
                if res.status == "failed" and job.fail_fast:
                    raise RuntimeError(f"Trial {res.label} failed: {res.error}")
                results.append(res)
        else:
            import multiprocessing
            import pickle
            import tempfile

            ctx = multiprocessing.get_context("spawn")
            invariants = (
                config,
                universe,
                prepared,
                market,
                strategy_name,
                base_params,
                start,
                end,
                job.initial_cash,
                persist,
                persist_artifacts,
                job.output_dir,
                corporate_actions,
            )
            fd, tmp_name = tempfile.mkstemp(suffix=".pkl", prefix="parayabuck_invariants_")
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                with tmp_path.open("wb") as f:
                    pickle.dump(invariants, f, protocol=4)
                payloads_g: list[tuple[ResearchTrial, int]] = [(trial, idx) for idx, trial in enumerate(job.trials)]
                with ctx.Pool(
                    processes=workers,
                    initializer=_pool_initializer,
                    initargs=(str(tmp_path),),
                ) as pool:
                    async_results = [pool.apply_async(_pool_worker, (p,)) for p in payloads_g]
                    results_by_idx: dict[int, TrialResult] = {}
                    for async_res in async_results:
                        try:
                            res = async_res.get(timeout=600)
                        except Exception as exc:
                            tb = traceback.format_exc()
                            res = TrialResult(
                                trial_index=-1,
                                label="unknown",
                                status="failed",
                                parameters={},
                                cost_multiplier=1.0,
                                elapsed_s=0.0,
                                error=f"Worker exception: {exc}\n{tb}",
                            )
                        idx = res.trial_index
                        results_by_idx[idx] = res
                        if res.status == "failed" and job.fail_fast:
                            pool.terminate()
                            raise RuntimeError(f"Trial {res.label} failed (fail_fast): {res.error}")
                    pool.close()
                    pool.join()
                for idx in range(len(job.trials)):
                    results.append(results_by_idx[idx])
            finally:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except Exception:
                    pass
        elapsed = time.perf_counter() - start_time
        return ResearchBatchResult(
            job=job,
            results=tuple(results),
            elapsed_s=elapsed,
            tier=job.tier,
            workers=workers,
            prepared_reused=prepared_reused,
            universe_reused=universe_reused,
        )
