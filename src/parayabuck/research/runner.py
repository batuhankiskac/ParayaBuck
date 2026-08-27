"""Research runner: invariant reuse + controlled multiprocessing."""

from __future__ import annotations

import hashlib
import os
import resource
import time
import traceback
from concurrent.futures import as_completed
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
from .tiers import (
    resolve_persist_artifacts,
    resolve_persist_db,
    resolve_window,
    tier_spec,
)

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


def _get_available_memory_gb() -> float | None:
    """Dependency-free available memory in GB, container-aware.

    Tries, in order:
    - cgroup v2 memory limit/current
    - cgroup v1 memory limit/usage
    - /proc/meminfo MemAvailable
    - os.sysconf SC_PHYS_PAGES * SC_PAGE_SIZE
    - macOS sysctl hw.memsize (with conservative available estimate)
    Returns None if cannot be determined confidently.
    """
    # cgroup v2
    try:
        # Current cgroup path from /proc/self/cgroup
        # For v2, check /sys/fs/cgroup/memory.max and memory.current
        for cand in [Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/system.slice/memory.max")]:
            if cand.exists():
                txt = cand.read_text().strip()
                if txt != "max":
                    limit = int(txt)
                    # Try current
                    cur_path = cand.parent / "memory.current"
                    if cur_path.exists():
                        cur = int(cur_path.read_text().strip())
                        available = limit - cur
                        if available > 0:
                            return available / (1024**3)
                    # If no current, use limit as available (conservative)
                    return limit / (1024**3)
        # cgroup v1
        for cand in [Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")]:
            if cand.exists():
                txt = cand.read_text().strip()
                limit = int(txt)
                # huge value means no limit ( > 1TB)
                if limit < 1 << 40:  # <1TB
                    usage_path = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")
                    if usage_path.exists():
                        usage = int(usage_path.read_text().strip())
                        return (limit - usage) / (1024**3)
                    return limit / (1024**3)
    except Exception:
        pass
    # /proc/meminfo
    try:
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            text = meminfo.read_text()
            for line in text.splitlines():
                if line.startswith("MemAvailable:"):
                    # e.g. MemAvailable:   123456 kB
                    parts = line.split()
                    kb = int(parts[1])
                    return kb / (1024 * 1024)
                if line.startswith("MemTotal:") and "MemAvailable" not in text:
                    parts = line.split()
                    kb = int(parts[1])
                    # No available, use total * 0.6 as conservative
                    return (kb * 0.6) / (1024 * 1024)
    except Exception:
        pass
    # os.sysconf
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = pages * page_size
        # Conservative available 60%
        return (total * 0.6) / (1024**3)
    except Exception:
        pass
    # macOS sysctl
    try:
        import subprocess

        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2).stdout.strip()
        total = int(out)
        return (total * 0.6) / (1024**3)
    except Exception:
        pass
    return None


def _auto_workers() -> int:
    """Conservative auto: leave headroom, cap by RAM, container-aware."""
    logical = os.cpu_count() or 1
    base = max(1, logical - 1)
    available_gb = _get_available_memory_gb()
    if available_gb is None:
        # Cannot determine, be conservative
        return 1
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


def _peak_rss_mb() -> float:
    """Current process peak RSS in MB, cross-platform."""
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS: bytes, Linux: kilobytes
        if os.uname().sysname == "Darwin":
            return rss / (1024 * 1024)
        return rss / 1024
    except Exception:
        # Fallback try /proc/self/status
        try:
            txt = Path("/proc/self/status").read_text()
            for line in txt.splitlines():
                if line.startswith("VmPeak:"):
                    # VmPeak: 123456 kB
                    kb = int(line.split()[1])
                    return kb / 1024
        except Exception:
            pass
        return 0.0


def _synthetic_bars(
    market: Market,
    timeframe: str = "1d",
    strategy_name: str | None = None,
    base_params: dict[str, object] | None = None,
) -> list[Bar]:
    """Deterministic synthetic bars for smoke, enough history for strategy."""
    # Determine required history
    try:
        strat = portfolio_strategy_from(
            strategy_name or ("equity_residual_momentum_v2" if market == Market.EQUITY else "crypto_low_variance_v2"),
            parameters=base_params or None,
        )
        min_hist = int(getattr(strat, "minimum_history_bars", 274))
    except Exception:
        min_hist = 274
    # Need at least min_hist + 30 bars per symbol to ensure at least a few decisions
    num_bars = max(300, min_hist + 40)
    bars: list[Bar] = []
    start = datetime(2023, 1, 1, tzinfo=UTC)
    # Keep symbol count tiny for speed: 8 symbols + benchmark
    if market == Market.EQUITY:
        symbols = [f"S{i:03d}" for i in range(8)]
        symbols.append("SPY")
    else:
        symbols = [f"TOK{i:03d}/USD" for i in range(8)]
        symbols.append("BTC/USD")
    timestamps: list[datetime] = []
    cur = start
    while len(timestamps) < num_bars:
        if market == Market.EQUITY and cur.weekday() >= 5:
            cur += _duration_for_timeframe(timeframe)
            continue
        timestamps.append(cur)
        cur += _duration_for_timeframe(timeframe)
    # Deterministic price via hashlib, not hash()
    for sym in symbols:
        for idx, ts in enumerate(timestamps):
            h = hashlib.sha256(f"{sym}:{idx}".encode()).hexdigest()
            val = int(h[:8], 16)
            price = 80.0 + (val % 4000) / 100.0  # 80.00 to 119.99
            price += (idx % 20) * 0.05
            bars.append(
                Bar(
                    timestamp=ts,
                    symbol=sym,
                    timeframe=timeframe,
                    open=price,
                    high=price * 1.01,
                    low=price * 0.99,
                    close=price,
                    volume=1_000_000.0 + (val % 500000),
                    market=market,
                )
            )
    # Ensure sorted by timestamp, symbol
    bars.sort(key=lambda b: (b.timestamp, b.symbol))
    return bars


def _duration_for_timeframe(timeframe: str) -> Any:
    from datetime import timedelta

    if timeframe == "1d":
        return timedelta(days=1)
    if timeframe == "1h":
        return timedelta(hours=1)
    return timedelta(days=1)


def _apply_execution_overrides(config: Config, overrides: dict[str, object] | None) -> Config:
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
    return replace(config, execution=exec_cfg)


def _effective_strategy_params(
    config: Config,
    market: Market,
    strategy_name: str | None,
    job_base: dict[str, object] | None,
    trial_params: dict[str, object] | None,
) -> dict[str, object]:
    """Build effective params: selection → job base → trial overrides.

    Case A: researching the configured market strategy (requested == selection)
        → start from configured selection parameters.
    Case B: researching a different strategy identity
        → do NOT inherit unrelated configured parameters; start from empty
          (requested strategy defaults are applied internally by
           portfolio_strategy_from; an empty mapping means "use defaults").
    TrialResult.parameters therefore reflects the explicit research inputs
    (selection-or-empty + job + trial). When internal defaults are not
    materialized, the mapping may be empty — this is intentional and
    documented rather than fabricated.
    """
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    requested_name = strategy_name or selection.name
    if requested_name == selection.name:
        # Case A: configured strategy — honor configured economics
        effective: dict[str, object] = dict(selection.parameters)
    else:
        # Case B: different identity — no leak
        effective = {}
    if job_base:
        effective.update(job_base)
    if trial_params:
        effective.update(trial_params)
    return effective


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
    # Build effective params: selection → job base → trial overrides
    effective = _effective_strategy_params(config, market, strategy_name, base_params, trial.parameters)
    t0 = time.perf_counter()
    pid = os.getpid()
    try:
        trial_config = _apply_execution_overrides(config, trial.execution_overrides)
        # Pass minimal valid object (empty list) to avoid huge allocation.
        effective_bars: list[Bar] = []
        # Only use bars if prepared is None (should not happen in normal runner)
        if prepared is None:
            effective_bars = bars
        run = run_product_backtest(
            trial_config,
            effective_bars,
            market=market,
            strategy_name=strategy_name,
            parameters=effective,
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
        peak = _peak_rss_mb()
        return TrialResult(
            trial_index=-1,
            label=trial.label,
            status="ok",
            parameters=effective,
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
            metadata=dict(trial.metadata) if trial.metadata else None,
            worker_pid=pid,
            worker_peak_rss_mb=peak,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        tb = traceback.format_exc()
        peak = _peak_rss_mb()
        return TrialResult(
            trial_index=-1,
            label=trial.label,
            status="failed",
            parameters=effective,
            cost_multiplier=trial.cost_multiplier,
            elapsed_s=elapsed,
            error=f"{type(exc).__name__}: {exc}\n{tb}",
            summary=None,
            metadata=dict(trial.metadata) if trial.metadata else None,
            worker_pid=pid,
            worker_peak_rss_mb=peak,
        )


class ResearchRunner:
    """Reusable research execution layer."""

    def __init__(self, config: Config | None = None, enable_cache: bool = True) -> None:
        self._config = config
        self._enable_cache = enable_cache

    def run(self, job: ResearchJob, bars_override: list[Bar] | None = None) -> ResearchBatchResult:
        start_time = time.perf_counter()
        requested_workers = _resolve_workers(job.workers)
        # Do not spawn useless workers
        workers = min(requested_workers, len(job.trials)) if job.trials else requested_workers
        # Enforce tier window requirements
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
        # Protect SQLite under multiprocessing
        # Resolve persist flags with tier defaults (tri-state)
        persist_artifacts = resolve_persist_artifacts(job.tier, job.persist_artifacts)
        persist_db = resolve_persist_db(job.tier, job.persist_db)
        if persist_db and workers > 1:
            raise ValueError(
                "persist_db=True with workers>1 would contend on SQLite; use workers=1 or persist_db=False"
            )
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
            # Smoke is tiny but must exercise real strategy path.
            # Resolve timeframe/min_history from SAME effective params the trials will use.
            # Use max history across trials so shared bars satisfy the most demanding trial.
            per_trial_effective: list[dict[str, object]] = []
            timeframes: set[str] = set()
            histories: list[int] = []
            for trial in job.trials:
                eff = _effective_strategy_params(config, market, strat_name, job.base_parameters, trial.parameters)
                per_trial_effective.append(eff)
                try:
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
            # Require single timeframe for smoke batch; if mixed, use sorted first and generate enough
            timeframe = sorted(timeframes)[0] if timeframes else "1d"
            max_hist = max(histories) if histories else 274
            # Pick effective that gave max_hist for generation (so _synthetic_bars derives same history)
            idx_max = histories.index(max_hist) if histories else 0
            effective_for_gen = per_trial_effective[idx_max] if per_trial_effective else {}
            bars = _synthetic_bars(market, timeframe, strat_name, effective_for_gen)
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
                    cached = load_cached_bars(csv_path, job.market)
                    if cached is not None:
                        bars = cached
                    else:
                        bars = load_csv_bars(csv_path, default_market=job.market)
                        store_cached_bars(csv_path, job.market, bars)
                else:
                    if not csv_path.exists():
                        raise FileNotFoundError(f"Dataset not found: {csv_path}")
                    bars = load_csv_bars(csv_path, default_market=job.market)
            else:
                if job.dataset_path:
                    cache_csv_path = Path(job.dataset_path)
                else:
                    cache_csv_path = None
            # Build universe and prepared
            strat_name = job.strategy_name or (
                config.strategy.equity.name if job.market == Market.EQUITY else config.strategy.crypto.name
            )
            base_params_tmp = job.base_parameters or {}
            try:
                tmp_strat2 = portfolio_strategy_from(strat_name, parameters=base_params_tmp or None)
                timeframe = tmp_strat2.timeframe
            except Exception:
                timeframe = "1d"
            # Universe cache with hardened fingerprint
            if cache_csv_path is not None and self._enable_cache and cache_csv_path.exists():
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
                else:
                    assert bars is not None
                    universe = build_product_universe(config, bars, market=job.market, end=end, timeframe=timeframe)
                    store_cached_universe(
                        cache_csv_path, job.market, timeframe, end, digest, scanner_rebalance, universe
                    )
            else:
                assert bars is not None
                universe = build_product_universe(config, bars, market=job.market, end=end, timeframe=timeframe)
            assert bars is not None
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
        persist = persist_db
        persist_artifacts_flag = persist_artifacts
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
                    persist_artifacts_flag,
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
            from concurrent.futures import ProcessPoolExecutor

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
                persist_artifacts_flag,
                job.output_dir,
                corporate_actions,
            )
            fd, tmp_name = tempfile.mkstemp(suffix=".pkl", prefix="parayabuck_invariants_")
            os.close(fd)
            tmp_path = Path(tmp_name)
            results_by_idx: dict[int, TrialResult] = {}
            try:
                with tmp_path.open("wb") as f:
                    pickle.dump(invariants, f, protocol=4)
                payloads: list[tuple[ResearchTrial, int]] = [(trial, idx) for idx, trial in enumerate(job.trials)]
                # Use ProcessPoolExecutor for completion-order handling
                with ProcessPoolExecutor(
                    max_workers=workers,
                    mp_context=ctx,
                    initializer=_pool_initializer,
                    initargs=(str(tmp_path),),
                ) as executor:
                    # Submit all
                    future_to_idx = {executor.submit(_pool_worker, p): p[1] for p in payloads}
                    future_to_payload = {fut: p for fut, p in zip(future_to_idx.keys(), payloads)}
                    for fut in as_completed(future_to_idx):  # completion order
                        idx = future_to_idx[fut]
                        payload = future_to_payload[fut]
                        trial, expected_idx = payload
                        try:
                            res = fut.result()
                        except Exception as exc:
                            tb = traceback.format_exc()
                            effective = _effective_strategy_params(
                                config, market, strategy_name, base_params, trial.parameters
                            )
                            res = TrialResult(
                                trial_index=expected_idx,
                                label=trial.label,
                                status="failed",
                                parameters=effective,
                                cost_multiplier=trial.cost_multiplier,
                                elapsed_s=0.0,
                                error=f"Worker exception: {exc}\n{tb}",
                                metadata=dict(trial.metadata) if trial.metadata else None,
                                worker_pid=None,
                                worker_peak_rss_mb=None,
                            )
                        # Ensure correct idx
                        if res.trial_index == -1:
                            res = replace(res, trial_index=expected_idx)
                        results_by_idx[res.trial_index] = res
                        if res.status == "failed" and job.fail_fast:
                            # Cancel remaining
                            for fut2 in future_to_idx:
                                fut2.cancel()
                            executor.shutdown(wait=False, cancel_futures=True)
                            raise RuntimeError(f"Trial {res.label} failed (fail_fast): {res.error}")
                for idx in range(len(job.trials)):
                    if idx not in results_by_idx:
                        trial = job.trials[idx]
                        effective = _effective_strategy_params(
                            config, market, strategy_name, base_params, trial.parameters
                        )
                        results_by_idx[idx] = TrialResult(
                            trial_index=idx,
                            label=trial.label,
                            status="failed",
                            parameters=effective,
                            cost_multiplier=trial.cost_multiplier,
                            elapsed_s=0.0,
                            error="Missing result for trial (worker did not return)",
                            metadata=dict(trial.metadata) if trial.metadata else None,
                        )
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
