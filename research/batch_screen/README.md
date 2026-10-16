# research/batch_screen — batch screening for ParayaBuck ideas

Screening hundreds of ideas needs two things the per-study harnesses do not give: a cheap way to
describe an idea (a plugin), and a way to stop the false positives that hundreds of trials
produce. This framework is built on the mandatory Numba engine `research/fast_engine` and adds

* a **plugin API** — an idea is a Python module with `ARMS = {arm_id: spec}` and a vectorised
  `targets(data, spec)`;
* a **trial ledger** (`REGISTRY.jsonl`) that refuses to screen an arm whose code is not committed
  *before* the run, and whose line count N feeds the multiple-testing correction;
* a two-stage protocol — **stage 1** screens on the main window and its block bootstrap only,
  **stage 2** applies the full holdout gate set to the survivors;
* a **deflation** pass (White's Reality Check, Romano–Wolf stepdown, deflated Sharpe) over every
  trial ever registered.

Everything the framework simulates goes through `fe_sim.sim_one`; the only new kernels are two
numba drivers over that same function (a per-arm target panel instead of production R2's single
series, and a per-arm fill rule), and `tests/test_parity.py` asserts both reproduce the engine's
`fe_boot.boot` and the old pure-Python `boot_oos_market` **bit for bit** (max |diff| = 0, not a
tolerance).

Run it from the worktree root with the research venv:

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.batch_screen.run --harness
PYTHONPATH=. $R -m research.batch_screen.run --batch anchors_v1 --ideas k_control,r2_control,vol_shape_p15
PYTHONPATH=. $R -m research.batch_screen.run --deflate --batch anchors_v1
research/batch_screen/tests/run.sh            # pytest
```

## 1. The plugin API

```python
# research/ideas/<idea_id>.py
ARMS = {"arm_id": {"rule": "K", "leg": "2x", "cash_yield": "none", ...fixed params...}}

def targets(data, spec):
    """(P, T) target weights of the held leg, or (wants, leg)."""
```

`targets` gets a `MarketData` panel whose arrays are all `(P, T)` — `P = 1` for a real window, a
bootstrap chunk otherwise — and must be written in vectorised numpy:

| field | meaning |
|---|---|
| `days` (T,), `year`, `month` (T,) | sessions of the window / chunk |
| `price` (P,T) | the underlying's price close: QQQ without dividends on main, the reconstructed price path on a bootstrap chunk, the index/ETF close on the OOS and instrument windows (the series the production vote runs on) |
| `legs` `{key: (open, close)}`, `leg` | every leg the window offers and the one this arm holds |
| `open`, `close`, `vol` (P,T) | the held leg's open/close (`vol = vol_n(close)`), the sizing input |
| `div`, `rate` (P,T) | per-session dividend and DTB3 rate of the window's construction |
| `high`, `low` (P,T or None) | underlying high/low where the data has them (ETFs) |
| `meta` | the window's own constants (`fin`, `exp_q`, `window`, …) |

Two legs are supported: `2x` is the campaign's published synthetic daily-reset 2x (this is what
every published number used) and `1x` is the same underlying at gearing 1 with no cost. The leg is
constant per arm; `targets` may *return* a per-session leg selection, but a varying one raises
`NeedsKernel` — the account holds one instrument, there is no portfolio layer.

Engine RULE options come from the spec and are the engine's own three booleans:

* `rule` — `K` (band + USD gain fraction + December deferral), `C0`/`R2` (plain 0.5 pp band),
  `A1u` (band only), `H2` (deferral only), or a name a plugin registers with
  `research.batch_screen.rules.register_rule(name, band, usd_rule, defer)` at import time
  (`fe_sim.RULE` is the engine's extension point, so no engine file is edited). A genuinely new
  mechanism — a bracket budget, a partial fill, a second leg — is **not** expressible: declare it
  `NEEDS_KERNEL = "why"` at module level (or `"needs_kernel": "why"` in the arm's spec), and the
  runner registers the trial and refuses to simulate it. That is the honest boundary: a new
  mechanism is a kernel change in a copy of the engine plus a parity cell, i.e. a separate study.
* `leg` — `2x` (default) or `1x`.
* `cash_yield` — `"none"` (default) or `"tbill"` (the session-folded DTB3 schedule of
  `engine README`). It changes every published cell, so it is a batch-level flag: an arm whose
  declaration disagrees with `--cash-yield` is refused.

Three example plugins reproduce published results exactly (`--harness`, and the `anchors_v1`
batch): `k_control/K` (production K — the framework's control), `r2_control/R2` (the same target
series under the unbanded rule) and `vol_shape_p15/p15` (`research/vol_shape_v1`'s I2 arm at
p = 1.5, c = 0.24593188954326456). All three match their published pre-tax cells, after-tax
windows and bootstrap medians to ≤ 0.0001 pp.

## 2. Registry and pre-registration by commit

`REGISTRY.jsonl` gets one line per arm ever screened, append-only:

```json
{"idea": "vol_shape_p15", "arm": "p15", "spec": {...}, "spec_hash": "sha256…",
 "code_hash": "sha256…", "batch": "anchors_v1", "date": "2026-10-16", "status": "screened",
 "git": {"head": "...", "idea_commit": "...", "commit_gate": "pass"}}
```

The runner **refuses to screen an arm whose module's working-tree bytes differ from its bytes at
HEAD** (`RegistryError`, see `registry.require_committed`) — the `code_hash` in the ledger is
therefore the committed one, and a spec cannot be edited after seeing results without a new
ledger line (`status: "rerun"` when the same spec/code has been screened before). Lines are never
removed. N = the number of lines is the multiple-testing count, including arms that were declared
but refused (`needs_kernel`).

## 3. Stages

**Stage 1 — screen.** Main window only (QQQ/QLD* from
`data/historical/stress_robustness_v1_daily.csv` and `…_dtb3.csv` as `fe_data` loads them,
2000-03-09 → 2026-09-29, base and calibrated financing, `m = 2`), the 2000 after-tax window at
$100k and the clean block bootstrap (seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax
paths). Pass rule (fixed, pre-registered):

```
after-tax 2000-window Δ > 0  AND  B1-recent median Δ > 0
AND pre-tax max DD ≤ K + 2 pp (base and calibrated, m = 2)
AND turnover ≤ 2 × K (base and calibrated, m = 2)
```

**Stage 2 — holdout**, for stage-1 survivors only: G1–G9 vs K (as `ewma_band_v1` defines them,
G9 = ≤ 2 × K), after tax at $100k on all three windows, B1/B2 median Δ ≥ 0 with share > 0 ≥ 55 %,
the 21 ETFs after tax (median > 0, share ≥ 60 %), the 21 January rolling starts (median ≥ 0,
share ≥ 60 %), the six OOS windows (median Δ ≥ 0, ≥ 5 of 6 within −0.5 pp CAGR / +3 pp DD, max DD
Δ ≤ +8 pp), the pooled OOS bootstrap (median ≥ 0) and the fresh-seed bootstraps (seed 20261016,
blocks 10/40, median ≥ 0 and share ≥ 55 % in all four cells). A stage-2 failure still writes its
record, so the registry counts it.

The Δ in every cell is `arm − K` in percentage points, on the *same* resampled paths (the
bootstrap draw is a deterministic function of `(seed, block, path index)`), which is what makes
the Δ vectors paired across arms.

## 4. Deflation

Three tests, all on the **B1-recent Δ vector** of every screened arm (seed 20261011, block 20,
1,000 paths, after-tax $100k progressive) — the statistic the screening decision is built on:

* `white_rc` — White (2000): `p = P*(max_a t*_a ≥ max_a t_a)`;
* `rw_stepdown` — Romano–Wolf (2005) stepM: studentized max statistic, arms ordered by the observed
  t, at step k the reference set is the arms still under test, p-values made monotone along that
  order; a rejection is `p_adjusted ≤ 0.05`;
* `deflated_sharpe` — Bailey & López de Prado (2014): the probability that the selected arm's
  advantage-Sharpe (mean/std of its Δ vector) exceeds `E[max SR]` over N = the ledger count trials,
  using the cross-sectional variance of the trial Sharpes and the Δ sample's skew/kurtosis.

A candidate is declared only if it passes stage 2 **and** `rw_stepdown` rejects at 5 % **and** the
deflated Sharpe is ≥ 0.95.

**Method.** The null distribution of the max statistic is built by a *second-level* bootstrap:
2,000 resamples of the B per-path Δ values (the same resampled path indices for every arm, which
preserves their cross-arm dependence), recentered per arm to its own mean, studentized by the
resample's own standard error.

**Assumptions, stated plainly.**

1. The first-level block bootstrap is taken as an adequate approximation of the sampling
   distribution of the Δ estimator (the campaign's pre-existing choice, not this framework's).
2. Arms are *paired*: every arm screened in a batch shares the control's paths for the same
   (seed, block, path count). Arms screened under a different draw are not poolable.
3. The second-level resampling adds no information beyond that distribution; it is a way of
   reading a p-value for the mean off a finite panel of path draws.
4. 20-session blocks cannot capture serial dependence longer than 20 sessions, so the p-values are
   optimistic under long memory — the same caveat the campaign's own bootstraps carry.
5. The deflated Sharpe here is the *advantage*-Sharpe of the bootstrap Δ distribution, not the
   daily-return Sharpe of the strategy (the screening decision is made on the after-tax CAGR
   advantage, and the deflation is applied to the same statistic).

## 5. Outputs, cost, and byte-identical reruns

```
results/<batch>/_control.json            the control's record + its raw per-path vectors
results/<batch>/<idea>__<arm>.json       one arm: stage 1, stage 2, gates, the B1 Δ vector
results/<batch>/RESULTS.json             the aggregate (the byte-identity check)
results/<batch>/SCOREBOARD.md            one line per arm
results/<batch>/timing.json              wall clock per arm (the only non-deterministic file)
results/DEFLATION.json                   the last deflation pass
HARNESS.json                             the --harness checks
```

Every number is rounded to 6 dp and every container sorted before serialising, so re-running a
batch reproduces `RESULTS.json` and the per-arm files byte for byte. Arms run in separate
processes (`--workers`, default = the CPU count); each arm is independent because it re-runs the
same deterministic path draw, and the control is computed once per batch and shared. One arm costs
about 1 s of stage 1 plus (if it survives) 5–10 s of stage 2 on this machine — the synthetic
scale factor is the full holdout set, not the arm.

## 6. Tests

`research/batch_screen/tests/run.sh` (the research venv's numba + the project venv's pytest, see
the header of that script). 51 tests: the plugin contract and its validation, the leg
constructions, the registry's append-only semantics and commit gate, every stage-1/stage-2 gate
branch on synthetic records, the deflation's null control/power/monotonicity/DSR properties, and
the parity cells (engine `fe_boot.boot`, old `boot_oos_market`, the production R2 target series,
`vol_shape_v1`'s sizing, and the control's published K cells stage by stage).
