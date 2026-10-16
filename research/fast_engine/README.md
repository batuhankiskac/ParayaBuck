# research/fast_engine — a shared Numba-accelerated research engine

One reusable engine for the ParayaBuck research studies, replacing the per-study copies of
`screen.py` + `tax_engine.py`. It runs the **same** simulations as the pure-Python harness — to
the last bit on the NAV path, see [Parity](#parity) — with the session loops (and the bootstrap)
in numba. `research/band_h2_combo_v1/` is untouched; `parity.py` runs the two side by side.

```
fe_config.py   the production constants and the data-historical paths the studies clone
fe_data.py     the market loaders (trv2.load, main_inputs, fx_at, load_ufe, load_gspc,
               load_market, load_instrument, quality_ok, rate_on, usd_market)
fe_signals.py  the signal/target pipeline, numpy, batched over paths
fe_sim.py      @njit simulation kernel (production R2's execution layer + the arms)
fe_tax.py      @njit Turkish after-tax engine over flat lot arrays
fe_boot.py     the block bootstrap: identical path draws, per-path simulation under `prange`
fe_study.py    the study-level stages (pre-tax main, after-tax windows, OOS, 21 ETFs, E1-E3, E5)
parity.py      old code vs new engine, and the published cells against RESULTS.json
bench.py       wall-clock before/after
```

## Why: where the old studies spend their time

cProfile on `band_h2_combo_v1`'s reduced `boot` (200 pre-tax / 100 after-tax paths, 4 arms, 2
capitals, 2 modes; 12.6 s clean, 22.4 s under the profiler):

| function | tottime | share |
|---|---|---|
| `screen.engine_run` (cum 17.3 s) | 9.28 s | **77 %** |
| `screen.r2_state` (the per-path signal loop) | 3.32 s | 15 % |
| `tax_engine.BaseAccount.sell` | 1.97 s | 9 % |
| `screen.rule_sale` | 1.36 s | 6 % |
| `tax_engine.prev_month` (string months) | 1.03 s | 5 % |
| `boot`'s own path-draw loop | 0.81 s | 4 % |

Per-path signal cost is 20 ms, of which `r2_state` 13.7 ms, the position recurrence 3.1 ms and
`early_components` 3.4 ms (2.9 ms of it the rolling median). Every other stage of the study is
under 3 s: `pre_tax_main` 0.48 s, `after_tax_windows` (1 capital) 0.21 s, `cost_stress` 0.19 s,
`oos_stages` 0.78 s, `instrument_stages_usd` 1.78 s, `stage_e1` 2.32 s, `stage_e2` 0.72 s. The
study is a bootstrap study: the clean bootstrap (5,000 pre-tax / 1,000 after-tax paths, 4 arms, 2
capitals, 2 modes) is ~58,000 session loops of 2,520 sessions and dominates everything.

So the design follows the profile: **the path-dependent state machine is the kernel, everything
else stays numpy.**

Re-profiling the *new* bootstrap once the kernel was in place moved the bottleneck: 1,000 paths
spend ~4 % of their time in the simulations and 96 % in one numpy call, `np.median` over the
sliding 252-session window of the vol series (it copies a `(paths, T, 252)` array -- 5.6 GB per
1,000 paths). `fe_signals.rolling_median_vol` is that median as a numba running median (the window
is kept sorted; 0.04 s instead of 2.36 s for 512 x 2,776), which took the clean bootstrap from
14.5 s to 2.6 s. Its values are the *same order statistics* `np.median` picks, so it is
bit-identical, not merely close.

## Design

* **`fe_sim.sim_one`** is a line-for-line port of `screen.engine_run`. One kernel serves pre-tax,
  progressive and flat because the Python engine is one loop with a `mode` switch: the account
  always runs, `mode` only decides whether `assess` charges anything.
* **Flat state.** The Python engine's objects become arrays: the FIFO lot book is four parallel
  arrays plus a `[head, tail]` pair (a popped lot is `head += 1`, the book resets exactly where
  the Python resets it to `[]`); the per-year tax dicts are dense arrays indexed by `year - Y0`,
  with a `has_due` flag standing in for "present in the dict"; months are
  `year * 12 + month - 1` and the YI-UFE month-before lookup is a dense table built once per run
  (slot `m` holds `ufe[prev_month(m)]`, the only month the indexation rule asks for).
* **Signals stay numpy**, batched `[paths, T]`: `fe_signals` is `screen`'s pipeline unchanged,
  except the rolling median above. Batching cannot change a value (each row is computed
  independently) and `parity.py` checks it bitwise against the per-path calls.
* **Bootstrap.** `fe_boot.draw_positions` replays `screen.boot`'s three rng calls per path in the
  same order, so `boot(seed, block, P)` draws the *same* paths; the sequential recurrence runs in
  numba, the market reconstruction in numpy over a chunk of paths, and the simulations under
  `prange` (one path per iteration, so results cannot depend on the thread count).
* **no fastmath, no reassociation.** Numba's default (`fastmath=False`) does not contract
  `a*b+c` into an FMA on this toolchain, and nothing here is vectorised or re-associated beyond
  what Python itself evaluates left to right. That is why the NAV paths come out *identical*, not
  merely close.
* **`cache=True`** on every kernel: `research/fast_engine/__pycache__/` holds the compiled
  kernels, so a second process skips compilation (numbers in [Benchmarks](#benchmarks)).

## API

### One run: `RunEnv` + `simulate`

`fe_sim.simulate` is `engine_run`: same arguments, same returned dict.

```python
from research.fast_engine import fe_data, fe_sim, fe_study

days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)      # the QQQ main window, base financing
fx, ufe = fe_study.try_market(days)                        # CBRT USD/TRY + YI-UFE
env = fe_sim.RunEnv(days, fx, ufe, fe_study.START, len(days) - 1)
for arm in ("C0", "A1u", "H2", "K"):
    for mode in ("pre_tax", "progressive", "flat"):
        r = fe_sim.simulate(o, c, w_r2, env, 100_000.0, fe_study.START, len(days) - 1,
                            arm=arm, mode=mode, m=2)
        r["cagr_usd_liquidated"], r["_nav"]                # engine_run's keys
```

`RunEnv` holds everything that only depends on the window: the month/year tables, the YI-UFE
table, the December-tail set, the instalment schedule and the per-year scale factors. One env
serves every arm and every mode; build it once per window.

* `arm` — a fill rule from `fe_sim.RULE`: `band` (A1u's gain-scaled downsize band), `usd_rule`
  (the band's gain fraction in USD rather than TRY) and `defer` (H2's December window). Arms are
  the 4 pre-registered combinations; **a study's arms are (target weights × fill rule)**, so the
  usual new arm is just different `wants` with the same rule (that is how every stage of
  `band_h2_combo_v1` works: all four labels share production R2's target series).
* `mode` — `"pre_tax"`, `"progressive"` (2026 tariff, YI-UFE indexation, instalments) or `"flat"`
  (B2: 20 % on the annual net realised USD gain, losses carried forward).
* `m` — the cost multiplier (`COST_PER_SIDE * m` per unit of traded notional).
* `trace=True` adds `_dec`, the per-session decision code (0 none, 1 up/entry/exit, 2 downsize).
* `dbg` is a `(nsessions, 6)` buffer for bisecting a divergence against the Python engine; it
  receives cash, units, lot head, live lot count, drift and `du` per session.

**Cash yield (off by default).** `RunEnv(..., cash_rate=rates)` turns on an idle-cash yield: once
per session, before the fill and before any tax payment, `cash += cash * rates[i]` when cash is
positive. `rates` is indexed by absolute session, so the caller owns the schedule; every published
study passes nothing, and `parity.py` asserts the empty and all-zero cases are bit-identical to
the Python engine (`cash_yield`). The reference for this knob is
`research/crypto_sleeve_decision_v1/account_sim.py`, which accrues `TBILL_APPROX[year] / 365` on
the idle balance (`max(idle, 0.0) * rf`) on *every calendar day*, weekends included, before it
executes the pending target; the session-based equivalent folds a gap into one rate:

```python
import numpy as np
from datetime import date, timedelta
from research.fast_engine import fe_data

def session_rates(days):
    """One per-session idle-cash yield: every calendar day since the previous session is folded
    in, with each day's own rate (the studies' own DTB3 series, as the QLD* financing uses)."""
    d3, tb = fe_data.load_rates()
    d0, d1 = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    cal = [(d0 + timedelta(days=k)).isoformat() for k in range((d1 - d0).days + 1)]
    daily = fe_data.rate_on(cal, d3, tb) / 365.0        # annual rate held on each calendar day
    rates, prev = np.zeros(len(days)), None
    for k, d in enumerate(days):
        idx = (date.fromisoformat(d) - d0).days
        if prev is not None:
            acc = 1.0
            for j in range(prev + 1, idx + 1):
                acc *= 1.0 + daily[j]
            rates[k] = acc - 1.0
        prev = idx
    return rates
```

(On the QQQ main window this yields +0.716 pp of after-tax CAGR for C0 and +0.722 pp for K at
$100k, progressive, m = 2 — a study that turns the knob on must re-run every published cell,
which is exactly why it is off by default.)

Nothing in this package imports that snippet: it is the bridge from a calendar-day reference to
the session engine, and a study that uses it owns the schedule (and its evidence).

Extending the *rule family* (a new mechanism, not a new combination) means editing `sim_one` and
adding a parity cell — the honest cost of having the state machine inside a kernel.

### Study stages: `fe_study`

```python
fe_study.pre_tax_main()                          # base/calibrated x m=1/2/4 + the R-USD row
fe_study.after_tax_windows(("10k", "100k", "1M"))# three windows, progressive, liquidated
fe_study.cost_stress()                           # m = 4 after-tax stress at the gate capital
fe_study.oos_stages()                            # the six OOS windows (GSPC 1957-1999, M1-M5)
fe_study.instrument_stages_usd()                 # the 21 quality-passing ETFs + the quality record
fe_study.stage_e1()                              # after tax on the 21 ETFs under R-INDEX
fe_study.stage_e2()                              # rolling January starts 2001..2021
fe_study.stage_e3()                              # fresh bootstraps (seed 20261015, blocks 10/40)
fe_study.stage_e5_decisions()                    # the arms' fill-decision traces
```

Each returns the dictionary the current studies write into their RESULTS.json, so a new study can
copy a block straight into its own report. `fe_study.window_indices(days, a, b)` gives the
`engine_run` bisect convention for a date window, and `pre_stats` / `window_stats` / `seg` are the
published statistic helpers.

### Bootstraps: `fe_boot.boot`

```python
from research.fast_engine import fe_boot, fe_config

window = fe_boot.BootWindow()                    # the fixed 2015-09..2026-09 calendar, built once
b = fe_boot.boot(seed=fe_config.SEED, block=fe_config.BLOCK, arms=("C0", "A1u", "H2", "K"),
                 n_pre=5000, n_tax=1000, capitals=("100k", "1M"),
                 modes=("progressive", "flat"), deltas=fe_config.CANDIDATES,
                 baselines=("C0", "A1u"), label="clean", window=window)
b["tax"]["100k"]["progressive"]["K_vs_C0"]       # boot()'s dict: medians, shares, p05/p95
b["pre"]["base"]["K"]["cagr_median"]             # the pre-tax leg (E4)
b["anchor_e2_100k"]["progressive"]               # the pure-E2 fidelity anchor
b["paths"]["tax"]["100k"]["flat"]["K"]           # keep_paths=True: the raw per-path CAGRs
```

`seed`/`block` reproduce the published draw exactly (`boot(20261011, 20, ...)` is B1/B2 of every
campaign; `20261015` with blocks 10/40 is E3), which is what makes the published bootstrap medians
and shares reproducible cell for cell. `chunk` controls how many paths are in flight at once
(memory vs. parallel efficiency); 256 paths is ~130 MB.

## Parity

`python -m research.fast_engine.parity` (70 s; `--quick` skips only the full published
bootstrap) writes `PARITY.json` and prints:

| check | cells | mismatches | max abs diff | tolerance |
|---|---|---|---|---|
| `signals` (batched numpy/numba vs per-path Python, bitwise) | 173 | 0 | 0 | 1e-9 rel |
| `engine_side_by_side` (`engine_run` vs `simulate`, 3 modes × 4 capitals × 4 arms + the trace) | 1009 | 0 | **0** | 1e-9 rel |
| `invariants` (per-session cash/units/lot-book invariants) | 48 | 0 | 0 | 1e-9 rel |
| `boot_side_by_side` (old `boot` vs new `boot`, 600/300 paths, every cell) | 178 | 0 | **0** | 1e-9 rel |
| `published.pre_tax_main` (C0/A1u/H2/K, base+calibrated × m1/m2/m4) | 554 | 0 | 4.990e-07 | 5e-07 abs |
| `published.pre_tax_main_r_usd` | 184 | 0 | 4.917e-07 | 5e-07 abs |
| `published.after_tax_windows` ($10k/$30k/$100k/$300k/$1M × 3 windows) | 1630 | 0 | 4.997e-07 | 5e-07 abs |
| `published.cost_stress_m4` | 60 | 0 | 4.987e-07 | 5e-07 abs |
| `published.oos` (6 OOS windows) | 186 | 0 | 5.000e-07 | 5e-07 abs |
| `published.instruments` (21 ETFs, pre-tax) | 630 | 0 | 5.000e-07 | 5e-07 abs |
| `published.quality` | 44 | 0 | 3.917e-07 | 5e-07 abs |
| `published.E1_after_tax_etfs_rindex` (21 ETFs, after tax) | 1050 | 0 | 4.996e-07 | 5e-07 abs |
| `published.E2_rolling_starts` | 672 | 0 | 4.992e-07 | 5e-07 abs |
| `published.E3_fresh_bootstraps` (blocks 10/40 × both modes) | 124 | 0 | 4.960e-07 | 5e-07 abs |
| `published.E5` | 33 | 0 | 4.644e-07 | 5e-07 abs |
| `published.bootstrap_clean_seed20261011` (5,000 / 1,000 paths, both modes, B1+B2) | 178 | 0 | 5.000e-07 | 5e-07 abs |
| `published.harness_reproduction_checks` (the R2 anchors) | 15 | 0 | 4.443e-07 | 5e-07 abs |
| `cash_yield` (inert by default; constant-rate cash compounding) | 2 | 0 | 0 | 1e-9 rel |
| `behaviour` (`rule_index` is inert on the band) | 1 | 0 | 0 | 1e-9 rel |

6,771 cells, 0 mismatches. The side-by-side boot (600 pre-tax / 300 after-tax paths) also runs
37.8 s on the old code against 0.44 s here (85x, warm cache), with every cell of the two
dictionaries equal.
Read that as: **the side-by-side quantities are identical (max |diff| = 0)**, and every published
cell matches to the precision `RESULTS.json` stores. That file is written through
`trend_robustness_v2.rnd`, i.e. `round(x, 6)`, so 5e-07 is the entire available precision — the
observed 4.4–5.0e-07 is the rounding, not a modelling difference. Nothing needs fastmath, and
nothing had to be re-ordered: the kernel reproduces the Python float ops exactly.

## Benchmarks

`python -m research.fast_engine.bench` writes `BENCH.json`. Both sides run in one process per side,
on the same machine, with the numba cache warm (the cold-cache compile cost is measured
separately).

| workload | old (pure Python) | new (engine) | speedup |
|---|---|---|---|
| **harness** -- the control anchors + the 1,000 / 1,000-path clean bootstrap | 63.4 s | 1.1 s | **59x** |
| **one full after-tax bootstrap** -- 1,000 paths, $100k, progressive, 4 arms (the published E3 run) | 31.8 s | 0.5 s | **61x** |
| **the whole study-sized run** -- every stage plus the 5,000 / 1,000-path clean bootstrap | 392.2 s (6.5 min) | 9.8 s | **40x** |
| the clean bootstrap alone (5,000 pre-tax / 1,000 after-tax paths, same dict) | (inside the row above) | 2.6 s | -- |

Same machine (10 threads), same inputs, both sides in one process each. For scale: the brief's
30-60 min figure is for the heavier studies in this line; `band_h2_combo_v1` -- the study this
engine was validated against -- measures 392 s end to end on the old code here. The dominant old
cost is the bootstrap (`stage_e3` alone is 127 s, the clean bootstrap ~5 min).

JIT: **6.0 s cold** (every kernel, empty `NUMBA_CACHE_DIR`) and **0.6 s warm** (cached kernels
loaded), thanks to `cache=True`. The three speedups above are warm-cache numbers, i.e. what a
study sees on its second and later runs; a first run on a new machine pays the 6 s once.

Threads: `prange` over paths is real but not the whole story -- the session loop is 3.8x faster on
ten threads than on one (`_boot_chunk`, 4,000 after-tax runs: 0.15 s -> 0.04 s), and the running
median 6.1x (512 x 2,776: 0.19 s -> 0.031 s, itself 78x the numpy `np.median` it replaces). The
remaining bootstrap time is numpy signal work, which is already threaded.

## Limitations — what a study still writes itself

* **The target weights.** The engine takes per-day target-weight arrays; computing them for a new
  signal is numpy work the study owns (`fe_signals` covers the production E2/R2 family — the
  ensemble/SSO/multi-leg arms of `trend_robustness_v2` are not ported, because no study in this
  line uses them).
* **A new mechanism.** `sim_one` implements this rule family (band / USD gain fraction / December
  deferral). A different kind of execution rule (a bracket budget, a partial fill, a second leg)
  needs a kernel change plus a parity cell. A new *combination* is just `fe_sim.RULE`.
* **A new cell.** `simulate` returns `engine_run`'s dict. A new reported quantity means new
  aggregation in the study (or in `fe_study`), not a new engine.
* **The tax model.** The 2026 tariff, the 10 % YI-UFE indexation threshold, the month-before
  conventions, the March/July instalments and the flat B2 variant are hard-coded in `fe_tax`
  (`TARIFF_2026`, `DIV_THRESHOLD_2026`, `ANCHOR`). Changing the model changes the parity anchor,
  so it is a deliberate edit.
* **One instrument per account.** The account holds a single synthetic QLD* leg; there is no
  portfolio layer (the studies run each window/instrument as its own account).
* **No dividends and no withholding.** `year_div` / `year_wht` exist as zero arrays because the
  Python engine has them; no study feeds them, so that branch is unexercised (the tariff path has
  no data for it). The `div <= DIV_THRESHOLD_2026 * scale` clause is therefore a no-op here.
* **Dictionary iteration order.** `outstanding()` and `tax_total_try` sum a `defaultdict` in
  insertion order; the port reproduces that by summing years in increasing order, which is the
  insertion order (instalments are paid chronologically and the year-boundary assess is
  chronological). A study that inserts tax years out of order would break this.
* **`rule_index` is inert.** It only reaches `rule_sale`, which is only called from the band's
  `else` branch — never taken, because every arm with a band also has `usd_rule` on. The parameter
  is kept for `engine_run` compatibility; `parity.py` asserts it changes nothing.
* **`rule_year` is not ported.** The Python engine accumulates a per-year "rule gain"
  (`screen.py:585` initialises it, lines 608/630/681 add to it) that nothing ever reads — dead
  weight in the original, dropped here.
* **First-call compile cost** is paid once per process (or never, with the warm cache); the
  benchmark reports both.
