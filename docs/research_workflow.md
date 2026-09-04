# Reproducible Research Workflow

The research platform turns the existing `ResearchRunner` into a **reproducible, resumable, inspectable** experiment system. Every experiment is defined once, fingerprinted deterministically, and persisted as a self-describing filesystem bundle under `research_runs/`.

## Version-control and retention lifecycle

See the [current research-record index and lifecycle policy](research/README.md)
for the distinction between local runs, canonical evidence and immutable
history.

- `research_runs/` contains generated, resumable local bundles. Caches,
  temporary trials and exploratory bundles remain local and are not canonical
  evidence.
- A run becomes canonical only through a reviewed manifest, receipt, required
  hash-pinned results and human report chain.
- The existing `data/research/` ignore rule keeps ordinary generated output
  local. Required canonical artifacts must be reviewed and intentionally added
  by explicit path; do not use blanket force-adds.

## 1. Create an experiment YAML

```yaml
# examples/research/equity_v3_small_grid.yaml
name: equity-v3-small-grid
market: equity
strategy: equity_residual_momentum_v3
tier: smoke            # smoke|quick|robustness|canonical
workers: auto          # 1, 2, 4, auto
dataset: data/historical/equity_1200_2016_2026.csv
start: 2019-01-01
end: 2024-12-31
base_parameters:
  top_n: 8
trials:
  - label: baseline
    parameters: { beta_window_bars: 273 }
grid:
  beta_window_bars: [252, 273, 300]
  residual_vol_window: [42, 63]
baseline: baseline
tags: [equity, residual-momentum]
max_trials: 500        # explosion guard
```

- `trials` — explicit list; each needs `label`, `parameters`, optional `execution_overrides`, `cost_multiplier`, `metadata`.
- `grid` — Cartesian expansion over sorted keys; deterministic order; merged after explicit trials.
- If `grid` expands beyond `max_trials` (default 500), the CLI rejects with a clear message; use `--allow-large` to override.

## 2. Plan (dry-run)

Resolve without running; shows scientific identity and cache hints.

```bash
parayabuck research plan examples/research/equity_v3_small_grid.yaml
# or with overrides
parayabuck research plan spec.yaml --workers 4 --allow-large
```

Output:

```
Experiment: equity-v3-small-grid
Experiment ID: a91e38f3c2d1e4a5
Strategy: equity_residual_momentum_v3
Dataset: data/historical/equity_1200_2016_2026.csv
Window: 2019-01-01T00:00:00+00:00 -> 2024-12-31T23:59:59+00:00
Trials: 6
Workers: 4 (requested auto)
Estimated reusable inputs: bars cache: hit
  universe cache: hit
  numeric history: reuse
Trial list:
  - baseline (id 3f2a1b) params={'beta_window_bars': 273}
  ...
```

No heavy work runs in `plan`.

## 3. Run

```bash
parayabuck research run examples/research/equity_v3_small_grid.yaml
parayabuck research run spec.yaml --workers 4 --output-dir research_runs --fail-fast
```

Creates:

```
research_runs/2026-08-29_equity-v3-small-grid_a91e38_f21b3c/
  manifest.json      # full provenance, git, env, fingerprints, config digest
  job.json           # canonical ResearchJob (for resume validation)
  trials.jsonl       # one JSON per completed trial (append-only, parent-owned)
  failures.jsonl     # subset of failures
  summary.json       # snapshot of all trials
  summary.csv        # flattened table (trial_id,label,params…,metrics)
  environment.json   # python/platform/package versions
```

`summary.csv` columns: `trial_id,label,status,param_<key>…,total_return,cagr,sharpe,sortino,max_drawdown,calmar,turnover,costs,trade_count,fill_count,rebalance_count,final_equity,elapsed_s,worker_pid,error`.

## 4. Interrupt / Resume

Kill halfway, return tomorrow, resume only pending.

```bash
parayabuck research resume research_runs/2026-08-29_equity-v3-small-grid_a91e38_f21b3c
parayabuck research resume research_runs/<run> --retry-failed   # retry failed only with explicit flag
# or via run with --resume
parayabuck research run spec.yaml --resume                       # auto-finds latest run with same experiment_id
parayabuck research run spec.yaml --resume --output-dir research_runs/existing_run
```

Resume prints:

```
Completed: 82
Pending: 18
Failed: 2
```

- `completed → skip`, `pending → run`, `failed → skip` unless `--retry-failed`.
- If scientific inputs changed (e.g., `beta_window_bars` 273→300), `experiment_id` differs and resume **rejects** with a clear mismatch error instead of silently mixing results.

## 5. Inspect (read-only)

```bash
parayabuck research inspect research_runs/<run>
```

Shows status, experiment/run IDs, strategy/window, trial counts, best by Sharpe, and per-trial Sharpe/CAGR/equity. No backtest runs.

## 6. Compare

```bash
parayabuck research compare research_runs/runA research_runs/runB
parayabuck research compare runA runB --output compare.json
```

Matches by `trial_id`; reports per-trial deltas:

```
CAGR delta
Sharpe delta
Sortino delta
drawdown delta
turnover delta
cost delta
fill-count delta
elapsed-time delta
```

Also reports `only_in_a` / `only_in_b` for missing trials. Never auto-declares a winner.

## 7. Provenance & Identity

### Experiment identity (`experiment_id`)

Deterministic SHA256 over canonical JSON of:

- strategy name/version, market, tier
- trial definitions (sorted)
- dataset fingerprint (size+mtime+path, not full SHA every run)
- universe fingerprint (scanner digest + window + rebalance)
- corporate actions fingerprint
- config digest (strategy/universe/execution/backtest only; no secrets)
- base_parameters, execution assumptions, engine version

Ignores `output_dir`, `timestamp`, `workers`.

### Run identity (`run_id`)

`20260829T143522Z-f21b3c18` — wall-clock + random; stored in `manifest.json` alongside `experiment_id`.

### Trial identity (`trial_id`)

SHA256 over `parameters`, `execution_overrides`, `cost_multiplier`, `metadata` (label excluded; two trials with same label but different economics never collide).

### Manifest (`manifest.json`, `schema_version: 1`)

`experiment_id`, `run_id`, git commit/dirty/branch + `reproducibility: degraded` if dirty, strategy/market/timeframe/base_parameters/grid, dataset/universe/corporate fingerprints, `start/end/tier`, `config_digest`, `Python/platform/parayabuck/numpy/pandas`, `workers_requested/resolved`, `created_at/completed_at/status`, `trial_count` + per-trial list, `output_dir`, `bars_cache_hit/universe_cache_hit`.

Secrets (API keys, HMAC) are never persisted; `config_digest` sanitizes to semantic fields only (`strategy`, `universe` digests, `execution`, `backtest`).

## 8. Safety

- **Crash-safe:** trials appended via parent after each `TrialResult`; `summary.json/csv` and `manifest.json` via `atomic_write` (temp→fsync→rename). Workers never write files.
- **Multiprocessing-safe:** parent owns all persistence; workers only compute via spawn + pickle invariants.
- **Atomic snapshots:** `manifest.json`, `summary.json`, `summary.csv` use `parayabuck.utils.io.atomic_write`.
- **Dataset fingerprint:** fast `size+mtime+path`; optional strong SHA cached in `data/cache/research/dataset_hashes/`.
- **Security:** experiment YAML uses `yaml.safe_load`; no `eval`, no arbitrary imports, no shell.

## 9. Legacy Research CLI

Old `parayabuck research --tier smoke --workers 1` still works (no subcommand). New platform is `research plan|run|resume|inspect|compare`.

## 10. Example (smoke, CI-safe)

```bash
parayabuck research plan examples/research/equity_v3_small_grid.yaml
parayabuck research run examples/research/equity_v3_small_grid.yaml --workers 1
parayabuck research inspect research_runs/2026-08-29_equity-v3-small-grid_*
parayabuck research compare research_runs/runA research_runs/runB
```

See `examples/research/equity_v3_small_grid.yaml` for a minimal grid that runs in seconds with synthetic bars (no large CSV needed).
