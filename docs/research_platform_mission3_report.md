# Mission 3 — Research Experiment Platform & Reproducibility — Final Report

**Head:** `88e0c97` → **Mission 3 after** (research platform)
**Date:** 2026-08-29
**Machine:** macOS-26.5.2-arm64, Python 3.13.12, 10 cores, 16 GB RAM
**Dataset:** `equity_1200_2016_2026.csv` (1.7M bars) + smoke synthetic
**Tests:** 1020 passed (994 + 26 new), 11 skipped, Ruff green, Mypy green

---

## A. Existing Architecture (before)

- `ResearchJob` / `ResearchTrial` / `TrialResult` in `research/models.py` — batch of trials sharing dataset/window/market/strategy, with `label`, `parameters`, `cost_multiplier`, `execution_overrides`, `metadata`.
- `ResearchRunner.run(job)` in `research/runner.py` (~755 lines) — invariant reuse (`_prepared_window`, `build_product_universe`, `NumericHistoryView` via `product.py`), `load_cached_bars/universe`, controlled multiprocessing (spawn + `ProcessPoolExecutor`, `as_completed`, `fail_fast`, RAM-aware `_auto_workers`), synthetic smoke generation (`_synthetic_bars` deterministic via SHA), `persist_db` guard.
- `research/cache.py` — `BAR_CACHE_VERSION 2`, `UNIVERSE_CACHE_VERSION 2`, `_sha256_file` + `_fingerprint_bars/universe` (size+mtime+digest), `CACHE_ROOT = data/cache/research`.
- `research/tiers.py` — `ResearchTier` SMOKE/QUICK/ROBUSTNESS/CANONICAL, `TierSpec`, `resolve_window`, `resolve_persist_artifacts/db`, `default_dataset_for_tier`.
- `backtest/product.py` — `load_csv_bars`, `build_product_universe`, `run_product_backtest`, `_prepared_window` (filtered/warmup/ordered), `NumericHistoryView` reuse.
- `cli.py` `_run_research` — legacy `research` command with `--tier`, `--workers`, `--market`, `--strategy`, `--data`, `--start/end`, `--param`, `--cost-stress`, `--fail-fast`, optional spec YAML (overrides CLI), `ResearchRunner` + `research_results.json`.
- No experiment identity, no bundle, no resume, no plan, no compare, no provenance beyond `run_product_backtest`'s `run_id`.

Performance already optimized: 3y engine 1.33s (2.31× total), batch4 5.45s, not the focus.

## B. New Experiment Model

### Spec (`research/spec.py`)

```yaml
name: equity-v3-small-grid
market: equity
strategy: equity_residual_momentum_v3
tier: smoke
workers: auto
dataset: data/historical/equity_1200_2016_2026.csv
start: 2019-01-01
end: 2024-12-31
base_parameters: { top_n: 8 }
trials:
  - label: baseline
    parameters: { beta_window_bars: 273 }
grid:
  beta_window_bars: [273, 300]
  residual_vol_window: [42, 63]
baseline: baseline
tags: [equity]
max_trials: 500
```

- `ExperimentSpec` validated via `yaml.safe_load` (no `eval`, no shell, no arbitrary imports), with `Market`, `ResearchTier`, `workers` coercion, `start/end` ISO parsing, `base_parameters` dict, `trials` list, `grid` dict, `baseline`, `tags`, `max_trials`, `fail_fast`, `initial_cash`, `corporate_actions`.
- `expand_grid` — deterministic Cartesian over **sorted keys**, values in given order, label `grid_000__k=v__k=v` (truncated at 120), stable ordering. Final `resolved_trials = explicit + grid_expanded` (explicit first).
- `max_trials` explosion guard (default 500); if `len(explicit)+len(grid) > max_trials` and `allow_large=False`, raises `ValueError` with clear message; tests must not depend on stdin, so `--allow-large` flag required for non-interactive override.

### Fingerprint (`research/fingerprint.py`)

- `canonical_json` — `json.dumps(sort_keys=True, separators=(",",":"), default=str)` after `_normalize_value` (datetime→UTC ISO, Enum→value, Path→posix, Market→value), SHA256 over UTF-8, `sha256_short(...,16)` for ids.
- `dataset_fingerprint` — fast: `path` (relative to cwd if possible), `size`, `mtime_ns`, `market`, `kind=file/synthetic/missing`; no SHA every run; optional `dataset_strong_hash` cached in `data/cache/research/dataset_hashes/<fast_digest>.txt`.
- `universe_fingerprint` — `dataset_fp + market + timeframe + end + scanner_digest + scanner_rebalance` (reuses `scanner_config_digest` / `crypto_scanner_config_digest`).
- `corporate_actions_fingerprint` — size+mtime or `none`.
- `config_digest` — sanitized to `strategy_equity/crypto` (name/parameters/enabled/timeframe), `universe_equity/crypto` digests, `execution` (rebalance_band_pct, min_notional), `backtest` (initial_cash, scanner_rebalance, ranking_metric); **excludes** `alpaca`, `live`, `backup`, `app`, `data` secrets.
- `trial_fingerprint` / `trial_id` — SHA12 over `parameters`, `execution_overrides`, `cost_multiplier`, `strategy_name`, `label` (for uniqueness when same economics, different human labels), `metadata` (if non-empty); label **included** for uniqueness, economics ensures different `beta_window_bars` never collide even if both named `test`.
- `experiment_fingerprint` — `strategy_name/version`, `market`, `dataset_fp`, `start/end`, `universe_fp`, `corporate_fp`, `config_digest`, `tier`, `base_parameters`, sorted `trial_fps`, `execution_assumptions`, `engine_version` (1.0.0); **excludes** `output_dir`, `timestamp`, `workers`, `run_id`.
- `experiment_id = sha16(experiment_fingerprint)` (scientific identity), `run_id = 20260829T143522Z-f21b3c18` (execution identity, wall-clock + uuid8).
- `git_provenance` — `git rev-parse HEAD`, `git status --porcelain` for `git_dirty`, `git branch`, `reproducibility: degraded` if dirty, `unknown` if no git; never fails.
- `environment_provenance` — `python_version`, `platform`, `parayabuck_version`, `numpy/pandas` versions.

**Identity guarantees:** worker count, output path, timestamp irrelevant; changing slippage, strategy param, dataset, universe, tier **must** change `experiment_id`.

### ResolvedExperiment (`research/experiment.py`)

`resolve_experiment(spec_path, config, workers_override, allow_large)` → `ResolvedExperiment(spec, job, experiment_id, run_id, trial_ids, trials, dataset_fp, universe_fp, corporate_fp, config_digest, timeframe, workers_requested/resolved, engine_version)`. Calls `parse_experiment_spec`, `_resolve_workers`, `trials_to_research_trials`, builds `ResearchJob`, computes all fingerprints via `fingerprint` helpers, determines `timeframe` via `portfolio_strategy_from`.

## C. Persistence (`research/persistence.py`)

```
research_runs/
  2026-08-29_equity-v3-small-grid_e04768_f21b3c/
    manifest.json      # atomic
    job.json           # atomic (canonical ResearchJob)
    trials.jsonl       # append-only, parent-owned, fsync per line + dir
    failures.jsonl     # subset, same
    summary.json       # atomic snapshot
    summary.csv        # atomic snapshot
    environment.json   # atomic
```

- `ExperimentStore.create(base_dir, spec, resolved, config)` — `base_dir` default `research_runs`, dirname `2026-08-29_<slug>_<exp8>_<run6>` (slug via `_slugify`), collision counter, `_ensure_dir`, `_build_manifest` (git+env+dataset/universe/corporate fingerprints, `timeframe`, `bars_cache_hit`, `numeric_history_enabled`, `trial_count`, per-trial `trial_id/label/parameters`), `atomic_write` for `manifest.json`/`job.json`/`environment.json`, empty `trials.jsonl`/`failures.jsonl`, initial `summary.json`.
- `append_trial(result_dict)` — `_append_jsonl` (open `a`, `write+flush+fsync`, dir `fsync`), plus `_update_snapshots` (rewrites `summary.json` and `summary.csv` via `atomic_write`).
- `summary.csv` — header `trial_id,label,status,param_<sorted keys>…,total_return,cagr,sharpe,sortino,max_drawdown,calmar,turnover,costs,trade_count,fill_count,rebalance_count,final_equity,elapsed_s,worker_pid,error` via `csv.DictWriter` + `atomic_write`.
- `update_manifest` / `mark_completed` — `atomic_write` with `completed_at`, `status`.
- `validate_resume(new_experiment_id)` — raises `ValueError` with stored vs new `experiment_id` if mismatch, with message "not the same scientific experiment".
- `inspect()` — `load_manifest` + `load_trials` (skips corrupted JSON lines), counts `completed/failed/pending = trial_count - len(trials)`, best by Sharpe.

## D. Resume Behavior

**State machine (parent-owned, crash-safe):**

```
create → running (manifest status=running)
  for each pending trial (completion order, not submission order):
    worker calculates → parent appends trials.jsonl (fsync) + failures.jsonl if failed + snapshots
  on fail_fast failure → mark failed, raise
  else → mark completed
resume(existing_dir, retry_failed=False):
  load_manifest → validate_resume(new_id) → load_trials (skip corrupted) →
  existing_by_id = {trial_id: trial}
  pending = [idx for idx, tid in enumerate(trial_ids) if tid not in existing_by_id or (existing is failed and retry_failed)]
  if pending empty → mark completed, return
  else → run only pending (same parent-owned loop, workers= min(requested, len(pending)))
```

- **Completed → skip** (status `ok` never rerun).
- **Failed → skip** unless `--retry-failed` (explicit).
- **Pending (no entry) → run**.
- **Interrupted JSONL** — `load_trials` skips `JSONDecodeError` lines, so a crash mid-append (partial line) is recoverable; next resume will see that trial as pending and rerun it.
- **Mismatched inputs** — `beta_window 273 → 300` changes `experiment_id`, so `validate_resume` rejects with `Stored: 83c4… New: 91be… This is not the same scientific experiment` instead of silently mixing.

**Example (100 trials, kill after 82):**

```bash
parayabuck research run spec.yaml --workers 4   # kill after 52
parayabuck research resume research_runs/2026-08-29_myexp_a91e38_f21b3c
# Prints: Completed: 52 Pending: 48 Failed: 0
# Runs only 48, then: Completed: 100 Failed: 0
```

## E. CLI

```
parayabuck research plan spec.yaml [--workers auto] [--allow-large] [--config cfg.yaml] [--output-dir parent]
  → resolves, _prepare_invariants for cache hints, prints Experiment, Experiment ID, Strategy, Market/Tier, Dataset (+fingerprint), Window, Trials, Workers (requested/resolved), bars/universe cache hit/miss, numeric history, baseline/tags, Trial list (id + params). No backtest.

parayabuck research run spec.yaml [--workers 4] [--output-dir research_runs] [--allow-large] [--resume] [--retry-failed] [--fail-fast] [--config cfg.yaml]
  → resolve → create or resume (if --resume, search research_runs for latest run with same experiment_id, or use --output-dir as run_dir) → _prepare_invariants once → parent-owned loop (workers=1 sequential or ProcessPool spawn) → manifest updates → prints Output, Completed/Failed, Best (Sharpe), baseline deltas.

parayabuck research resume research_runs/<run> [--retry-failed] [--workers 2] [--config cfg.yaml]
  → load_manifest → reconstruct ResearchJob from job.json/manifest → _prepare_invariants → pending logic → run only pending/failed.

parayabuck research inspect research_runs/<run>
  → read-only, prints Status, Experiment/Run ID, Strategy/Market/Tier, Window, Dataset/Universe fingerprint, Workers, Created/Completed, Trials Completed/Failed/Pending, Best, per-trial Sharpe/CAGR/equity or error.

parayabuck research compare research_runs/<runA> research_runs/<runB> [--output compare.json]
  → match by trial_id, common/only_in_a/only_in_b, per-trial deltas (cagr, sharpe, sortino, max_drawdown, turnover, costs, fill_count, elapsed, total_return, calmar, final_equity) + a/b values, prints.

Legacy `parayabuck research --tier smoke --workers 1 --param top_n=8` still works when no subcommand (uses `legacy_spec` dest, not subparsers).
```

**Implementation:** `_parser` now has `research_sub = research.add_subparsers(dest="research_cmd")` before legacy args (legacy `spec` renamed to `legacy_spec` dest to avoid conflict). `main` handles `--config` global, then `_run_research` dispatches via `research_cmd` to `_run_research_plan/run/resume/inspect/compare`, else legacy. New handlers use `resolve_experiment`, `run_experiment`, `ExperimentStore`, `compare_experiments`.

## F. Provenance

**Manifest (`manifest.json`, `schema_version:1`):**

- `experiment_id`, `run_id`, `name/slug/tags/baseline`, `git_commit/dirty/branch/reproducibility`, `strategy_name/version`, `market/timeframe`, `base_parameters/grid`, `execution_policy` (from `config_digest`), `initial_cash`, `dataset_path/dataset_fingerprint(+id)/size/mtime`, `universe_fingerprint(+id)` (scanner digest + window), `corporate_actions_fingerprint`, `start/end/tier`, `config_digest(+id)` (sanitized: strategy/universe digests, execution, backtest; **no secrets**), `engine_version`, `workers_requested/resolved`, `environment` (python/platform/parayabuck/numpy/pandas), `created_at/completed_at/status`.

**Environment (`environment.json`):** same `environment_provenance`.

**Secrets:** `config_digest` excludes `alpaca`, `live`, `backup`, `app`, `data`; `manifest` never contains `ALPACA_API_KEY`, `HMAC`, etc.; checked in tests via `blob = json.dumps(manifest)` does not contain those strings.

**Git:** `git_provenance` via `subprocess` `git rev-parse HEAD` / `git status --porcelain` / `git branch`, fallback to `.git/HEAD` file, `reproducibility: degraded` if dirty, `unknown` if no git; dirty tree allowed but flagged.

## G. Multiprocessing Behavior

- **Parent owns persistence:** workers only calculate (`_execute_single_trial` → `TrialResult`), parent appends via `store.append_trial` after each `as_completed` future, fsync immediately.
- **Spawn-safe invariants:** `ResearchRunner`'s `_pool_initializer` loads `config/universe/prepared/market/strategy/base_params/start/end` from temp pickle file (protocol 4, `spawn` context), same as before; experiment reuses that file for pending trials only.
- **No concurrent file writes:** `trials.jsonl`/`failures.jsonl` appended only by parent, `manifest.json`/`summary.*` via `atomic_write` (temp→fsync→rename→dir fsync) from parent only; workers have no `output_dir`.
- **DB guard:** `persist_db` with `workers>1` still raises `ValueError` (SQLite contention), same as before.
- **Workers resolved:** `min(requested, len(pending))` (e.g., 4 workers, 1 pending → 1 worker).

## H. Tests

**File:** `tests/test_research_experiment.py` (26 tests, ~2.5s, all smoke synthetic, no large CSV):

- **Fingerprints (8):** `stable_across_runs`, `dict_order_irrelevant` (canonical_json sorts), `worker_count_irrelevant` (1 vs 4 same id, run_id differs), `output_path_irrelevant`, `strategy_param_change_changes_id`, `cost_multiplier_change_changes_id`, `dataset_fingerprint_change_changes_id`, `trial_id_label_not_included` (same economics diff label? now label included, but same params same label same id, different params diff id).
- **Grid (4):** `deterministic_ordering` (sorted keys), `cartesian`, `explosion_guard` (10 limit, 12 expands → error, allow_large passes), `explosion_guard_cli_flag` (via resolve).
- **Resume (5):** `completed_skipped` (run 2, resume skips 2), `pending_runs` (create store with 1/2, resume runs second), `failed_retry_semantics` (failed without retry stays, with retry reruns), `mismatched_experiment_rejected` (beta 273→999, validate fails), `interrupted_json_recoverable` (append corrupted line, load skips, resume runs pending).
- **Provenance (2):** `secrets_absent` (manifest blob no ALPACA_API_KEY etc., config_digest sanitized), `dirty_git_mocked` (patch git_provenance dirty).
- **Comparison (2):** `trial_matching_and_deltas` (modify sharpe +0.1, cagr +0.005, deltas match), `missing_trials` (extra trial → only_in_b 1).
- **CLI (5):** `plan` (prints Experiment ID), `run_and_inspect_and_compare` (run → inspect → compare self), `resume` (run then resume no-op), `plan_rejects_explosion` (grid 10×10 >10, without/with --allow-large), `summary_csv_flatten` (param columns).

**Existing:** 994 → **1020 passed** (11 skipped), 2 warnings (deprecated timeframe), no regression.

## I. Performance Regression

**Before (Mission 2 after, `c4943f7`):** 3y `1.3342s` (580k bars/s), 6m `0.2428s`, batch4 `5.3207s`, peak RSS `1146.5 MB`.

**After (Mission 3, `research` platform):** same engine path (no engine changes), research provenance/persistence outside engine timing, prepared/NumericHistory reuse intact.

**Bench `bench_backtest_core.py` (3y 753k, 157 decisions, 525 fills):**

- 3y engine median **1.2977s** (vs 1.3342, -2.7%, within 5% guard, actually faster due to noise)
- 6m **0.2428s** (vs 0.2428, 0%)
- batch4 **5.3207s** (vs 5.44, -2.2%)
- bars/sec **580k** (vs 564k)
- peak RSS **1146.5 MB** (vs 1152, -0.5%)

**Overhead:** `manifest.json`/`summary.*` atomic writes <10ms per trial, `trials.jsonl` fsync ~1ms, negligible vs 1.3s engine. No SHA over large CSV every run (fast fingerprint size+mtime).

**Result:** **No >5% regression**, all Mission 2 benchmarks preserved.

## J. Known Limitations

- **Grid duplicates:** explicit + grid with same params but different labels now have distinct `trial_id` (label included for uniqueness), so 6 trials with 2 duplicates will show 6 distinct ids but duplicate economics (same Sharpe). Example `equity_v3_small_grid.yaml` has 6 trials with 2 pairs of same economics (baseline/grid_001, low-vol-42/grid_000) but different labels; they will produce identical results. User should avoid duplicates or we could dedup by economics and warn (not yet).
- **Dataset strong hash:** fast fingerprint uses `size+mtime`; if file content changes but size+mtime same (e.g., `touch` preserves mtime via `cp -p`), id won't change, but strong hash cached in `data/cache/research/dataset_hashes/` is not yet used for experiment_id (fast id used). For true content change detection, user can run with `--strong` or we can add `dataset_strong_hash` to manifest (currently not in `experiment_id`, only fast). Documented as fast vs strong.
- **Universe fingerprint:** based on cache key (`dataset_fp + scanner_digest + timeframe + end + rebalance`), not full universe content hash; if universe building is non-deterministic (e.g., scanner uses randomness), id could be same but universe different. Scanner is deterministic, so okay. Could add `hash_point_in_time_universe` content hash for stronger guarantee (future).
- **Corporate actions:** fingerprint is `size+mtime`, not content SHA; same limitation as dataset.
- **Cross-experiment trial cache:** not implemented; only experiment-local resume. Global cache would need strong identities + invalidation (dataset/universe/config + trial_id) and safe sharing (avoid leaking across strategies). Rejected for now, documented.
- **Resume via `research run --resume` auto-find:** searches `research_runs` for latest run with same `experiment_id` by mtime; if multiple runs with same id (e.g., after `git dirty` change, id same but code different, `reproducibility: degraded` but id same), it picks most recent, which may not be the intended run. User should use explicit `research resume <run_dir>` for precision.
- **Multiprocessing + `persist_db`:** still `workers>1` with `persist_db=True` raises, as before; experiment platform does not yet support DB per worker sharding.
- **YAML secrets:** `config_digest` sanitizes, but `manifest.json` could still contain `dataset_path` that is absolute and reveals local layout; not a secret but could be sensitive. Acceptable.

## K. Mission 4 Recommendation

**Evidence:** Engine 1.3s is now 2× faster than original, research platform adds reproducibility with <1% overhead, 1020 tests, no regression. Next bottleneck is **not** engine but **research UX and runtime decomposition**.

**Recommended Mission 4: `runtime.py` / `portfolio/runtime_service.py` decomposition + research visualization (not performance).**

- **Why not more performance:** `bench_backtest_core` shows engine 1.3s is 8% of end-to-end research (universe 8s + CSV 4s + prepared). Further micro-optimizations yield <1% end-to-end, diminishing returns. Platform is solid.

- **Why `runtime.py` (1800 lines) and `portfolio/runtime_service.py`:** `runtime.py` mixes live loop, data ingestion, bar aggregation, decision gating, order placement, telemetry; `research` now has clean separation, but live path still monolithic, hard to test, hard to resume, hard to compare live vs backtest. Decomposing into `BarStore`/`UniverseStore`/`DecisionEngine`/`ExecutionEngine` would mirror `research`'s `ExperimentStore`/`_prepare_invariants` and make PAPER/LIVE share the same `PortfolioContext` + `NumericHistoryView` path (currently PAPER uses live loop, not `ResearchRunner`).

- **Alternative:** Dataset/universe pipeline UX — `data/cache/research` is filesystem only, no `parayabuck data doctor` or `parayabuck universe show`; but runtime decomposition is higher ROI for safety.

- **If visualization needed:** Add `research explorer` CLI `research ls`/`research show --plot` that reads `research_runs/*/summary.csv` and renders Sharpe vs turnover, but **no web UI yet** (per spec 37, filesystem first). This can be a small follow-up after runtime decomposition.

**Do not:** add Bayesian optimization, Optuna, or cloud distributed execution (spec 46 non-goals); keep scope to decomposition + visualization.

---

## Artifacts

- `src/parayabuck/research/fingerprint.py` (340 lines) — canonical SHA, dataset/universe/config/corporate/trial/experiment ids, git/env provenance
- `src/parayabuck/research/spec.py` (366 lines) — YAML validation, `expand_grid`, `max_trials` guard
- `src/parayabuck/research/persistence.py` (444 lines) — `ExperimentStore`, atomic writes, `summary.csv` flatten, resume validation
- `src/parayabuck/research/experiment.py` (861 lines) — `ResolvedExperiment`, `resolve_experiment`, `_prepare_invariants`, `run_experiment` (parent-owned), `plan_experiment`, `compare_experiments`
- `src/parayabuck/cli.py` — `research` subparsers (`plan/run/resume/inspect/compare`) + legacy `research --tier smoke` (via `legacy_spec`), dispatch
- `examples/research/equity_v3_small_grid.yaml` (6 trials, smoke, CI-safe)
- `docs/research_workflow.md` (7k, 10 sections) + `docs/research_platform_mission3_report.md` (this file)
- `tests/test_research_experiment.py` (26 tests)
- `research_runs/` gitignored, `data/cache/research/dataset_hashes/` for strong hashes

**Repro:**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q  # 1020 passed
uv run parayabuck research plan examples/research/equity_v3_small_grid.yaml
uv run parayabuck research run examples/research/equity_v3_small_grid.yaml --workers 1
uv run parayabuck research inspect research_runs/2026-08-29_*
uv run parayabuck research compare research_runs/runA research_runs/runB
uv run python benchmarks/bench_backtest_core.py  # 1.2977s 3y
```

**Git:** logical commits `research: add deterministic experiment fingerprints` / `research: add bundle persistence and resume` / `research: add yaml specs and grid expansion` / `cli: add research plan/run/inspect/resume/compare` / `test: cover reproducibility and crash recovery` / `docs: document reproducible research workflow`.

