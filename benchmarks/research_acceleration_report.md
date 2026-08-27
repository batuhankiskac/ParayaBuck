# Mission 0A — Research Acceleration Report

## Git

- **Starting main HEAD:** `849555ed12fe8d5ec9410a59c07585cb908b5a92` (fix(risk): distinguish DB read failure from no-history)
- **Branch:** `mission-0a-research-acceleration`
- **Commit SHA(s):** `8fc70509d9fdbee9bdb9c12c23578f1311fbe5b3` (feat(research): reusable research runner with invariant reuse and parallel trials)
- **Files added (10):**
  - `src/parayabuck/research/__init__.py`
  - `src/parayabuck/research/models.py`
  - `src/parayabuck/research/runner.py`
  - `src/parayabuck/research/tiers.py`
  - `src/parayabuck/research/cache.py`
  - `tests/test_research_runner.py` (14 tests)
  - `benchmarks/bench_research_runner.py`
  - `benchmarks/profile_research_baseline.py`
  - `benchmarks/research_acceleration_baseline.json`
  - `benchmarks/research_acceleration_after.json`
- **Files modified (1):**
  - `src/parayabuck/cli.py` — added `research` command (spec file + tier/workers CLI)

Cache directory `data/cache/research/` is gitignored (derived data).

## Profiling (Before Optimization)

**Machine:** Darwin 25.5.0 arm64, Apple M5, 10 logical cores, 16 GB RAM, Python 3.13.12

**Dataset:** `data/historical/equity_1200_2016_2026.csv` — 1,738,468 bars (123 MB), SHA `3baec12e21a...`

**Scenario A — Single equity backtest 2019-01-01 → 2021-12-31, equity_residual_momentum_v2:**

| Phase | Wall time | Notes |
|-------|-----------|-------|
| CSV load | 4.71 s | 787 MB RSS delta |
| Universe construction | 16.83 s | 1509 snapshots, Stage-A 1200→300 sort per snapshot, median dollar volume per symbol |
| Prepared window | 0.26 s | filtered 428k, ordered 428k |
| Engine + report (with reuse) | 3.22 s | 178 trades, return 0.4068, Sharpe 0.79 |
| Engine + report (without reuse) | 20.14 s | includes universe+prepared rebuild |
| Single reuse median (3 runs) | 3.23 s | p95 3.24 s |
| Peak RSS | 1784 MB | single process |

**Scenario B — 4-trial batch (same window, cost multipliers 1.0/1.5/2.0/3.0):**

| Mode | Wall time (median, 1 run) | Throughput |
|------|---------------------------|------------|
| Naive (no reuse, per-trial universe+prepared) | 82.31 s | 0.049 trials/s |
| Reuse (universe+prepared once) | 13.28 s | 0.30 trials/s |
| Speedup reuse vs naive | **6.20×** | — |

**Scenario C — `optimize_grid()` 4 combos:**

| Mode | Median |
|------|--------|
| optimize_grid 4 combos (already reuses) | 31.54 s | 7.9 s per trial avg (includes OOS handling) |

**Top bottlenecks before optimization:**

1. **Universe construction 16.8 s (55% of single without-reuse, 80% of naive batch).** `build_trailing_point_in_time_universe` does per-snapshot sorting and median calculation for 1200 symbols × 1509 snapshots.
2. **CSV load 4.7 s** — Python csv DictReader + Bar construction for 1.7M rows.
3. **Engine 3.2 s** — already optimized via `_build_context` fast path (11% improvement in prior baseline), still non-trivial but not dominant.
4. **Prepared window 0.26 s** — small but repeated per trial if not reused.

**Existing optimization noted:** `optimize_grid()` already builds `train_universe` and `train_prepared` once and reuses via `run_product_backtest(..., universe=..., prepared_inputs=...)`. The mission avoided duplicating this; the runner generalizes it to any trial batch.

**Profiler top functions (single run, cProfile):** dominant is `profile_research_baseline.py:single_run` (8.96 s cumulative) which includes engine; per-symbol history trimming and scanner median dominate inside universe. No single Python function dominates engine after prior `_build_context` optimization.

## Implementation

### Research runner architecture

```
src/parayabuck/research/
  models.py  — ResearchTier (smoke/quick/robustness/canonical), ResearchTrial, ResearchJob, TrialResult, ResearchBatchResult
  tiers.py   — TierSpec, resolve_window(), default_dataset_for_tier(), TIER_SPECS
  cache.py   — Fingerprinted pickle cache for bars and universe
  runner.py  — ResearchRunner with prepare → execute → aggregate
  __init__.py — public exports
```

**ResearchJob** — dataset/window (start/end), market, strategy, base_parameters, trials (tuple), tier, workers (1/N/auto), persist flags, output_dir, fail_fast.

**ResearchTrial** — label, parameters (strategy overrides), cost_multiplier, execution_overrides (rebalance_band_pct, minimum_order_notional), metadata.

**TrialResult** — label, status, parameters, elapsed, total_return, CAGR, Sharpe, Sortino, maxDD, Calmar, turnover, costs, fee_total, trade/fill/rebalance counts, final_equity, error, summary.

Results returned in deterministic trial-index order regardless of worker completion order. `fail_fast` optionally raises on first failure; otherwise collect failures.

### Reused / prepared inputs

- **CSV bars** — loaded once per job, cached via `data/cache/research/bars/<hash>.pkl` keyed by source SHA256 + size + mtime + schema version. Pickle load 1.08 s vs CSV 4.71 s (4.3× faster). Invalidation on source change.
- **Universe** — built once per job, cached via `data/cache/research/universe/<hash>.pkl` keyed by source SHA + market + timeframe + end + config digest + schema version. Build 16.8 s vs pickle load 0.06 s (250× faster). Config digest via `scanner_config_digest` for equity, SHA of crypto config for crypto.
- **Prepared window** — `_prepared_window()` once per job (filtered 428k + warmup + ordered). 0.26 s saved per trial.
- **Persistence suppressed** — `persist=False` (no SQLite) and `persist_artifacts=False` (no report.json/universe.json) for lightweight tiers (smoke/quick/robustness). Canonical tier sets `persist_artifacts=True` to generate full artifacts.

All reuse preserves canonical `run_product_backtest` semantics: the accelerated path calls the same `run_product_backtest` with `universe` and `prepared_inputs` supplied, no second economic interpretation.

### Concurrency model

- **Processes, not threads** — `multiprocessing.get_context("spawn").Pool` for macOS spawn safety. `spawn` avoids fork copy-on-write issues and works on Linux/Kaggle.
- **File-based invariant sharing** — Main pickles invariants `(config, universe, prepared, market, strategy, base_params, window, persist flags, corporate_actions)` to a temp file (`tempfile.mkstemp`) once. Workers load via initializer `_pool_initializer` once per worker (0.06 s for universe + 0.3 s for prepared). Per-trial payload is then tiny `(ResearchTrial, idx)` only, avoiding 66–105 MB per-task pickle that previously caused 1.1–1.5 s overhead and limited speedup to ~1.1×.
- **Deterministic ordering** — `apply_async` + `results_by_idx` dict, final list ordered by trial_index.
- **Error isolation** — per-trial try/except inside `_execute_single_trial`; failed trial returns `TrialResult(status="failed", error=...)` without corrupting batch. `fail_fast` optionally terminates pool.
- **Throughput metric** — Wall time for 4 trials, not per-decision latency.

### Worker auto-selection

```python
def _auto_workers() -> int:
    base = max(1, logical - 1)  # leave one core
    available_gb = psutil.available or total*0.6 fallback
    per_worker_gb = 1.2  # bars 0.8 + prepared 0.3 + overhead
    headroom_gb = 2.0
    memory_cap = max(1, floor((available - headroom)/1.2))
    cap = 4 if available < 32 else 8
    return max(1, min(base, memory_cap, cap))
```

On 10-core/16 GB M5: `base=9`, `available~8–10 GB` (post OS), `memory_cap=5`, `cap=4` → `auto=4`. Conservative: never blindly uses all cores, leaves 2 GB headroom, caps at 4 on <32 GB to avoid swapping. Documented in `runner.py` and report.

### Tier system

| Tier | Purpose | Default window | Dataset | Persist | Max trials (rec) |
|------|---------|---------------|---------|---------|------------------|
| **0 Smoke** | Catch crashes, accounting bugs, seconds | Synthetic 10 symbols × 60 bars | Synthetic (no file) | False | 4 |
| **1 Quick** | Falsify bad ideas fast | 2019-01-01 → 2021-12-31 | `equity_1200_2016_2026.csv` | False | 12 |
| **2 Robustness** | Subperiods, cost stress, sensitivity | Explicit (caller) | 1200 sym | False | 32 |
| **3 Canonical** | Full PIT, full period, CA, walk-forward, artifacts | Explicit | `equity_pit_symmetric_2016_2026_v5.csv` | True | — |

`resolve_window()` supplies quick defaults only when caller omitted start/end; canonical/robustness require explicit windows to prevent silent expensive runs. `default_dataset_for_tier()` maps tier+market to canonical CSV. Agents must not auto-run canonical after every edit.

### Cache

- **Bars:** `BAR_CACHE_VERSION=1`, key = source SHA256 + size + mtime.
- **Universe:** `UNIVERSE_CACHE_VERSION=1`, key = bars key + market + timeframe + end + config_digest.
- Location `data/cache/research/` (gitignored). Atomic write via `.tmp` + `replace`. Stale detection: stored `_key` must equal current fingerprint, else miss. No new dependency (pickle, hashlib, stdlib). Canonical CSV remains source of truth.

**Why retained:** Bars cache saves 3.6 s per cold batch; universe cache saves 16.8 s (250×). Both measured end-to-end and fingerprint-safe.

### CLI / UX

```bash
parayabuck research [spec.json|spec.yaml] --tier quick --workers auto --market equity --strategy equity_residual_momentum_v2 --data bars.csv --start 2019-01-01 --end 2021-12-31 --cost-stress --param mh_raw_window=12 --output-dir out --persist-artifacts
```

- `spec` optional: JSON/YAML with `market, strategy, tier, workers, dataset, start, end, base_parameters, trials[]`. CLI `--tier`/`--workers` override spec if provided.
- Without spec, `--cost-stress` generates 3 trials (1×/2×/3×); otherwise single trial.
- Output: console summary + `research_results.json` if `--output-dir` given.
- Typed Python alternative: `ResearchRunner().run(ResearchJob(...))`.

### Rejected optimizations

| Optimization | Result | Reason reverted |
|--------------|--------|-----------------|
| Per-task pickle of full `bars` (1.7M Bar, 178 MB) + universe + prepared (105 MB) = 283 MB per task via `ProcessPoolExecutor` | Wall 14.5 s for 4 trials, per-trial 4.03 s (overhead 1.1 s), speedup 1.1× vs sequential 13.3 s | Too large per-task copy; switched to file-based initializer (per-worker load) + tiny per-trial payload |
| `gzip` pickle for bars (33 MB) | Dump 18.9 s, load 3.19 s (slower than plain pickle 1.08 s) | No benefit, added CPU |
| `BarStore.get` list-copy avoidance (return view) | Would change return type, break `is_contiguous` scan | Not needed for threshold, deferred |
| `equity_candidate_from_bars` sorted avoidance | <1% for 20-bar window | Not measurable |
| Polars/PyArrow/Parquet for bars | Not benchmarked; would add heavy dependency for 4.7 s → ~1 s (pickle already does) without fingerprint complexity | Dependency cost not justified; pickle suffices, stdlib |
| Shared memory / Ray/Dask/Redis/Celery | Distributed platform overkill for local 4–8 trials | Mission explicitly forbids |
| Vectorized NumPy backtester | Would be second economic interpretation, breaks parity | Deferred to Mission 0B, not in 0A |
| GPU / Rust / Cython | Not justified until Python bottleneck measured after low-risk improvements | Explicitly forbidden in 0A |

## Performance (Same Machine)

All measurements on same host (M5, 10 cores, 16 GB, Python 3.13.12, dataset `equity_1200_2016_2026.csv` 1.7M bars, 2019-2021 window, 4 cost-multiplier trials). Cache warm for “after” unless noted.

### Single equity run (2019-2021)

| Mode | Median | Notes |
|------|--------|-------|
| Before, without reuse (includes universe 16.8 s + CSV 4.7 s) | 20.14 s | Naive |
| Before, with reuse (engine only) | 3.23 s | Universe/prep already built |
| After, runner single trial (cached) | 4.52 s | Includes job overhead, still engine-bound |
| Speedup (without-reuse → after) | **4.5×** | Single-run latency secondary |

### 4-trial batch (cost 1.0/1.5/2.0/3.0, 2019-2021)

| Mode | Median wall | Peak RSS | Trials/s | Speedup vs naive |
|------|-------------|----------|----------|------------------|
| **Before, naive (per-trial rebuild)** | 82.31 s | 1784 MB | 0.049 | 1.0× |
| **Before, reuse sequential (optimize_grid style)** | 13.28 s | 1784 MB | 0.30 | 6.20× |
| **After, runner workers=1 (cached)** | 13.35 s | ~1800 MB | 0.30 | 6.17× |
| **After, runner workers=2 (cached, file init)** | 11.76 s | ~2100 MB | 0.34 | 7.00× |
| **After, runner workers=4 (cached, file init)** | **10.32 s** | **2456 MB** | **0.39** | **7.98×** |
| **After, runner workers=4 vs before reuse** | — | — | — | **1.29×** (additional parallel gain) |

**Throughput improvement for representative batch: 6.17× (sequential reuse) to 7.98× (parallel 4) vs naive — exceeds 3× preferred target and 2× minimum.**

### Optimizer batch (4 combos)

| Mode | Median |
|------|--------|
| Before, `optimize_grid` 4 combos | 31.54 s |
| After, runner not used (optimizer already optimal) | ~31.5 s (no change, reuse already) |

### Workers scaling (4 trials, cached, median of 3 runs)

| Workers | Median | Min | Max | Speedup vs 1 | Peak RSS | Notes |
|---------|--------|-----|-----|--------------|----------|-------|
| 1 | 13.35 s | 13.21 s | 13.52 s | 1.00× | ~1800 MB | Deterministic reference |
| 2 | 11.76 s | 11.71 s | 11.84 s | 1.14× | ~2100 MB | 2 waves |
| 4 | 10.32 s | 9.85 s | 10.76 s | 1.29× | 2456 MB | 1 wave, 4-way |

Memory per worker ~1.2 GB (bars 0.8 GB + prepared 0.3 GB + universe 0.02 GB + overhead). On 16 GB, 4 workers peak 2.4 GB, well under limit; auto caps at 4 to leave 2 GB headroom. 8 workers would be slower than 4 (throughput metric), so capped.

**CSV cache:** 4.71 s → 1.08 s (4.3×). **Universe cache:** 16.83 s → 0.06 s (250×). First cold batch still pays 16.8 s once; subsequent batches amortize to 0.06 s.

## Correctness

- **Parity:** `benchmarks/parity.py` combined_hash `93fd28374f3b48ac4d1684952bd80fe1c7f1b69dd9d688f7b4d0858bbe4dd10c` unchanged before/after. Single equity and crypto hashes unchanged. Runner vs canonical `run_product_backtest` parity verified for 2019-2021, 1-year and 3-year windows, sequential vs parallel (cost 1.0/1.5/2.0/3.0) — all metrics identical (total_return, CAGR, Sharpe, Sortino, maxDD, Calmar, trade count 178 for 3-year, 49 for 1-year).
- **Deterministic ordering:** Parallel completion order does not affect result order (verified with 4 trials, workers 2/4).
- **Tests:** 969 passed, 11 skipped (full suite). New `tests/test_research_runner.py` 14 passed:
  - sequential/parallel basic, deterministic ordering
  - failure handling (collect vs fail_fast)
  - worker count validation, auto heuristic
  - tier selection, window resolve
  - prepared reuse flag, cache invalidation, parity 1 vs N, no persistence (no report.json) vs full persistence (report.json + universe.json), smoke <5 s.
- **Ruff:** `ruff check .` passed
- **Format:** `ruff format --check .` passed (258 files)
- **Mypy:** `mypy src` passed (82 files, strict)

## Verdict

**PASS**

- Throughput improvement 6.17× (reuse alone) to 7.98× (parallel 4) exceeds preferred 3× and minimum 2×.
- Parity exact (hash and metrics).
- Architecture reusable for future strategy/cost/walk-forward/regime experiments.
- Memory safe (peak 2.45 GB on 16 GB, auto caps).
- No economic semantics changed, no new heavy dependency, no GPU/Rust/vectorized second engine.

## Next Recommendation

**Is the optimized canonical Python research path now fast enough for upcoming missions?**

**Yes, for representative 2019-2021 quick falsification and medium robustness.**

- 4-trial batch wall 10–13 s (cached) or 29 s cold (includes one-time 16.8 s universe build). This is interactive for strategy variants, cost stress (3 trials), and parameter perturbations. Weekly agent iteration is now feasible without full-history penalty.
- Full PIT canonical validation (10.5 years, 9010 symbols, 11.6M bars, PIT symmetric v5) remains expensive — universe for full period is larger (not benchmarked here) and engine scales with bars. The current event-driven engine is still O(bars × symbols) per rebalance, but with universe cache and parallel trials, wall is dominated by engine (3 s per 3-year window, likely 10–15 s per 10-year window). For walk-forward with many folds (e.g., 10 folds × 4 trials = 40 runs), wall would be 40 × 3 s = 120 s sequential or ~40 s parallel (4 workers) — acceptable for final gate, but not for inner loop of a large grid search (256 combos per mission limit).

**If NO (i.e., when would Mission 0B be justified?):**

- If upcoming missions require **large grid searches** (e.g., 64–256 combos) on full PIT, the remaining bottleneck is the **event-driven engine's per-decision O(symbols × history) loop** and **single-threaded Python**. Profiling showed engine 3 s per 3-year window; scaling to 10 years and 256 combos is ~768 s (12.8 min) sequential, ~200 s parallel — may be too slow for iterative research.
- Mission 0B (vectorized NumPy/Numba fast research engine) would be justified if measured batch wall for 16+ trials on full PIT exceeds ~60 s and profiling shows engine `_build_context` and strategy compute dominate despite reuse. Our profiling shows engine is now the dominant after universe cache, and prior `_build_context` fast path already gave 11% but is not enough for 10× more trials.
- Recommendation: **Do not start 0B now.** The current path is fast enough for Tier 1/2 (quick/robustness) which is the intended inner loop. Keep canonical Tier 3 as expensive gate, run sparingly. Re-evaluate after measuring a real Tier 3 batch (full PIT, 10 years, 8–16 trials) — if wall >120 s, then justify 0B.

**Remaining bottleneck to monitor:** Universe build is now cached (0.06 s) and no longer bottleneck; engine per trial (3 s) and per-trial pickle/file overhead (0.6 s) are the residual. Reducing engine time via NumPy would be the next lever, but not needed yet.
---

## Hardening Update (2026-08-28, head a66aafd82ba3)

Post-report audit fixed 15 reviewed items without changing economics or benchmark posture:

- **execution_overrides** now returns new Config correctly; added positive sequential+parallel test with high band reducing trades.
- **strategy param semantics** unified as `selection → job.base_parameters → trial.parameters`; validated with editable param (int/float) precedence test.
- **smoke deterministic & meaningful**: uses `hashlib.sha256` (not `hash()`), derives `minimum_history_bars` from strategy, generates `max(300, min_hist+40)` business days, asserts `rebalance_count>0` and cross-worker determinism.
- **tiers**: `ROBUSTNESS`/`CANONICAL` now require explicit `start`+`end` via `ValueError` (prevents silent expensive runs).
- **persist tri-state**: `persist_artifacts`/`persist_db` are `bool|None` with tier defaults; Canonical defaults to artifacts True, others False; smoke/robustness never persist unless explicit.
- **removed huge concat**: `_execute_single_trial` no longer does `prepared[0]+prepared[1]` (400k list copy); passes empty list when prepared is present, relies on `prepared_inputs`.
- **auto-worker Linux-safe**: `_get_available_memory_gb` checks cgroup v2 `memory.max`/`current`, cgroup v1 `limit_in_bytes`/`usage_in_bytes`, `/proc/meminfo MemAvailable`, `os.sysconf`, and `sysctl hw.memsize` fallback; available = limit-current, headroom 2GB, per-worker 1.2GB.
- **per-worker RSS**: `_peak_rss_mb` uses `resource.getrusage(RUSAGE_SELF)` conversion `bytes` on macOS vs `kbytes` on Linux; stored as `TrialResult.worker_peak_rss_mb` + `worker_pid`; bench shows w1 1874MB single process, w2 999MB/worker, w4 950MB/worker, parent peak 2146MB.
- **cache fingerprints**: bars key includes `market`; universe key includes `market`, `timeframe`, `end`, `config_digest` (scanner_config_digest), `scanner_rebalance`; invalidation on any change.
- **worker failure**: `results_by_idx` preserves `trial_index`/`label` even on `Worker exception`; fills missing indices with failed result; collect vs fail_fast tested.
- **fail_fast truthful**: uses `as_completed` (completion-order) not submission order; measured fast_fail finishes in <1s vs slow 0.5s; test verifies timing.
- **timeout**: removed hard-coded 600s; exposed `ResearchJob.trial_timeout_seconds=None` (no timeout by default) passed to `fut.result(timeout=...)`; test asserts not 600.
- **SQLite protection**: `persist_db` + `workers>1` raises `ValueError` to avoid concurrent SQLite contention.
- **profiling SHA**: `_sha256_file` now reads full file in 1MB chunks, not sampled head.
- **cleanup**: removed `WorkerPolicy` from models, kept `TrialResult.metadata` carry; carries `metadata` through runner.

**Verification after hardening (same machine M5 10-core/16GB, 2019-2021, 4 trials):**
- `pytest tests/test_research_runner.py` 26 passed
- `ruff check` All checks passed! `ruff format` 5 files reformatted, `mypy src` Success 82 files
- `parity.py` combined_hash `93fd28374f3b48ac4d1684952bd80fe1c7f1b69dd9d688f7b4d0858bbe4dd10c` unchanged
- `bench_research_runner` medians: w1 12.77s, w2 11.789s (1.08×), w4 11.46s (1.11×), peak parent 2400MB, speedup vs naive 82.3s → 7.18×
- `w1 15.08s peak 1874MB, w2 13.28s peak 2012MB worker 999MB, w4 12.20s peak 2146MB worker 950MB, auto=4`
---

## Final Cleanup (2026-08-28, head 5b53b0ba95a → next)

Post-hardening review addressed 6 remaining findings. No architecture redesign.

### 1. Removed fake trial timeout
- Deleted `ResearchJob.trial_timeout_seconds`, CLI `--trial-timeout`, and `future.result(timeout=…)` dead logic (which called `as_completed` then `timeout` on already-completed future).
- There is now NO hidden timeout; a trial may run as long as necessary.
- Test `test_trial_timeout_no_hardcoded_600` removed; no timeout field remains.

### 2. Smoke fully strategy-aware
- Resolves actual strategy first via `_effective_strategy_params` (selection→job→trial) for **each** trial.
- Derives `strategy.timeframe` and `minimum_history_bars` from the same effective params the trial will use.
- Uses `max(min_history)` across trials +40, `max(300, …)` bars, tiny 8+benchmark symbols; crypto now uses `TOKxxx/USD` valid pairs.
- Isolated `_duration_for_timeframe` (1d→1 day, 1h→1 hour) with doc; equity skips weekends, crypto 24/7.
- Example that must be prevented is now handled: config `beta_window=500/600` → smoke generates 641 bars (601+40) not 313, so both trials reach rebalance.
- Tests: `test_smoke_derives_timeframe_and_history` (500/600 still rebalance>0) and `test_smoke_timeframe_not_hardcoded` (patches to 1h, captures `_synthetic_bars` timeframe==1h).

### 3. Explicit strategy parameter identity
- `_effective_strategy_params` now:
  - **Case A** (`requested == selection`): `dict(selection.parameters) → job → trial`
  - **Case B** (`requested != selection`): `{} → job → trial` (no leak; requested defaults applied internally by `portfolio_strategy_from`; empty map means “use defaults”, documented).
- Fixed canonical helper `run_product_backtest` to `parameters if parameters is not None else selection.parameters` so `{}` correctly means defaults, not selection.
- `TrialResult.parameters` reflects explicit inputs (empty when defaults).
- Regression test with two identities (`equity_residual_momentum_v3` configured top_n=8 vs `equity_residual_momentum_v2` diff, plus crypto) proves no leak; production parity unchanged.

### 4. Corrected multiprocessing memory evidence
- `bench_research_runner.py` now uses `TrialResult.worker_pid`/`worker_peak_rss_mb` to build reproducible evidence.
- For each worker count reports: `parent_peak_rss_mb`, `unique_worker_count`, `worker_peak_rss_mb_by_pid`, `sum_worker_peak_rss_mb`, `upper_bound_parent_plus_worker_peaks_mb`.
- Explicit note: sum of independent peaks is an upper-bound estimate, not necessarily simultaneous.
- New table (M5, 2019-2021, 4×cost, cache warm):

| workers | median | parent_peak | unique | worker peaks (MB) | sum worker | upper bound | trials/s |
|---:|---:|---:|---:|---|---:|---:|---:|
|1|12.988s|2183.3|1|{48584:2183.3}|2183.3|2183.3|0.308|
|2|13.050s|2392.3|2|{951.2,951.1}|1902.4|4294.7|0.307|
|4|11.627s|2400.1|4|{941.3,940.3,942.9,915.8}|3740.3|6140.4|0.344|

Previous report incorrectly described 2.4GB parent peak as aggregate; 4×950 already ≈3.8GB child peaks plus parent → 6.1GB upper bound.
- `after.json` and this report now carry corrected numbers; historical timings not rewritten, new numbers distinguished as “final cleanup”.
- 1.2GB per-worker assumption revisited: measured ~950MB, so heuristic remains conservative (headroom 2GB, cap 4).

### 5. Do not spawn useless workers
- `workers = min(requested_workers, len(trials))` after `_resolve_workers`.
- `ResearchBatchResult.workers` now reports **actual** workers used.
- Tests: 1 trial workers=4→1, 2 trials workers=4→2, 1 trial workers=auto→1.
- Reduces memory/startup; `test_sqlite_protection` updated to 2 trials for contention case.

### 6. Strengthened execution override regression test
- `rebalance_band_pct` 0.005 (normal) vs 0.5 (extreme) now asserts `high_band.trade_count < default.trade_count` and `fill_count` diff, both seq and par (same totals).
- `minimum_order_notional` 0.0 vs 10000 asserts high notional ≤ low notional trades.
- Catches future recurrence of missing-return bug (override must change observable execution).

### Verification final
- `ruff check .` All checks passed! `ruff format --check` 259 formatted
- `mypy src` Success 82 files
- `pytest -q` 981 passed 11 skipped (research 29 passed)
- `python benchmarks/parity.py` combined_hash `93fd28374f3b48ac4d1684952bd80fe1c7f1b69dd9d688f7b4d0858bbe4dd10c` unchanged
- `python benchmarks/bench_research_runner.py` as above; no speed chase, still >6× vs naive (6.34×/6.31×/7.08×).

### Verdict
`MISSION 0A FINAL: PASS / MERGE READY`

- No economic change; cache warm hit verified.


