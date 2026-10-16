# batch_screen_v1 — a batch screening framework for ParayaBuck ideas (verdict: delivered, verified)

> Evidence: `research/batch_screen/` — `PREREGISTRATION.md` (committed before the demonstration
> batch ran), `README.md` (API, registry, stages, deflation method and its assumptions),
> `registry.py` + `REGISTRY.jsonl`, `api.py`, `boot.py`, `stages.py`, `deflation.py`, `run.py`,
> `windows.py`, `rules.py`, `tests/` (52 tests), `HARNESS.json` (59 checks), `SUMMARY.txt`,
> `DATA_MANIFEST.json`, `results/anchors_v1/` (the demonstration batch). Plugins:
> `research/ideas/{k_control,r2_control,vol_shape_p15}.py`. Engine: `research/fast_engine`
> (tag `archive/research/fast-engine-v1-2026-10`), the mandatory Numba research engine.

## 1. Verdict

The framework exists, runs, and is anchored to the published record. `--harness` passes **59/59**
checks in 36 s: it runs `python -m research.fast_engine.parity --quick` (PASS), then drives the
three example plugins through the plugin path and reproduces their published numbers to
**≤ 0.00005 pp** (the published files' own 6-decimal rounding), and it asserts the framework's own
parity — its bootstrap driver equals `fe_boot.boot` cell for cell and its pooled-OOS driver equals
the old pure-Python `boot_oos_market` cell for cell, both at **max |diff| = 0.0**.

No trading idea is evaluated: this is an engineering deliverable. The three example arms exist to
prove the plugin path reaches the engine unchanged and to exercise one pass and two fail paths.

**v2 follow-up (2026-10-16, §7):** extra-data series (`data.extra`), per-window `NotApplicable`
handling with the `holdout_insufficient` verdict, a metric-free smoke runner and a merge-safe
ledger were added on top, with the control record and the `anchors_v1` scoreboard still
byte-identical to what v1 wrote (`--harness` 59/59, 99 tests).

## 2. What the framework adds

The per-study harnesses each carry their own simulator, tax engine and bootstrap; screening
hundreds of ideas needs one engine (there is one: `fe_sim.sim_one`) plus

1. **a plugin API** — an idea is `research/ideas/<idea_id>.py` with `ARMS = {arm_id: spec}` and a
   vectorised `targets(data, spec)` over a `(P, T)` panel (paths × sessions), so the same code runs
   on the real main window (`P = 1`), on the six OOS windows, on the 21 ETFs and inside a bootstrap
   chunk (`P = 256`). Engine RULE options (`band`/`usd_rule`/`defer`) come from the spec; a new
   *combination* is registered into `fe_sim.RULE` (the engine's own extension point, no engine file
   edited), while a new *mechanism* is declared `NEEDS_KERNEL` and refused — the honest boundary,
   since it would need a kernel change and its own parity cell.
2. **a trial ledger** — `REGISTRY.jsonl`, append-only, with the runner refusing to screen an arm
   whose module's bytes differ from its bytes at HEAD (pre-registration *by commit*), so the
   `code_hash` in the ledger is the committed one and a spec cannot be edited after seeing results
   without a new line.
3. **two stages** — stage 1 screens on the main window and its own block bootstrap only; stage 2
   applies the whole holdout set (OOS windows, ETFs, rolling starts, after-tax windows, fresh-seed
   bootstraps, pooled OOS bootstrap) to the survivors.
4. **a deflation pass** — White's Reality Check, a Romano–Wolf stepdown and the deflated Sharpe,
   over the paired per-path Δ vectors of every arm ever registered; a candidate is declared only if
   stage 2 *and* the deflation pass at 5 %.

## 3. Example plugins: published anchors reproduced

| arm | anchors checked (published source) | max abs diff |
|---|---|---|
| `k_control/K` | pre-tax base/cal m1+m2 CAGR, max DD, turnover, after-tax $100k 2000/2010/2017, 6 OOS windows, 21 ETFs pre- and after-tax, 21 rolling starts, bootstrap levels (B1/B2 + pre-tax) — `band_h2_combo_v1/RESULTS.json`, `vol_shape_v1/RESULTS.json` | 4.6e-05 pp |
| `r2_control/R2` | pre-tax base/cal m1+m2 CAGR, max DD, after-tax 2000/2010/2017 — `band_h2_combo_v1/RESULTS.json` (the C0 row) | 4.0e-05 pp |
| `vol_shape_p15/p15` | pre-tax base/cal m1+m2 CAGR, max DD, turnover, after-tax 2000/2010/2017, B1/B2 median Δ + share, pre-tax bootstrap level + median Δ — `vol_shape_v1/RESULTS.json` (the E3 row) | 4.9e-05 pp |
| framework drivers | the plugin bootstrap vs `fe_boot.boot` (K and C0 rows, every cell) and the pooled-OOS driver vs the old `boot_oos_market` | **0.0** |

`p15` reproduces vol_shape_v1's raw sizing (`shapes.apply_shape(..., 1.0, 0.35)` is bit-identical to
`fe_signals.sized_wants`), its published after-tax deltas vs K (+0.4905 / +0.4404 / +0.2027 pp) and
its bootstrap cells (B1 +0.2507 pp at 63.5 %, B2 +0.3311 pp at 68.3 %, pre-tax median Δ +0.4231 pp).

## 4. The demonstration batch (`anchors_v1`)

| arm | stage 1 | stage 2 | failed gates | AT2000 Δ pp | B1 med/share | B2 med/share | RW p | final |
|---|---|---|---|---|---|---|---|---|
| `k_control/K` | control | control | — | +0.0000 | +0.0000 / 0.000 | +0.0000 / 0.000 | — | control |
| `r2_control/R2` | **FAIL** | reject_stage1 | — | −0.5933 | −0.2856 / 0.250 | −0.1311 / 0.360 | 1.0000 | rejected (stage 1) |
| `vol_shape_p15/p15` | PASS | reject_stage2 | **G8, E1** | +0.4905 | +0.2507 / 0.635 | +0.3311 / 0.683 | 0.0000 | rejected (stage 2) |

* `R2` fails stage 1 exactly where the K campaign recorded it: 10.1600 % vs K's 10.7533 % after tax
  at $100k on the 2000 window = −0.5933 pp (the published −0.593).
* `p15` clears every after-tax cell (Δ +0.4905 / +0.4404 / +0.2027 pp; B1 0.635, B2 0.683; all four
  fresh-seed cells PASS; 21/21 rolling starts) and dies on **G8** — the pooled OOS bootstrap median
  Δ is −0.0128 pp with 48.6 % of paths positive — and on the after-tax ETF gate (median −0.1101 pp,
  42.9 %). G8 is the gate `research/vol_shape_v1` itself failed, so the framework reproduces a
  *known* rejection on the holdout set it never used at stage 1.
* **Declared candidates: none.** The deflation's `declared` set is empty because stage 2 is empty;
  the deflation itself (RW p = 0.0000, DSR = 1.0 for p15 on the B1 Δ vector) is reported in
  `results/DEFLATION.json` and would have gated a stage-2 pass.

## 5. Cost and reproducibility

| job | wall clock |
|---|---|
| `--harness` (59 checks, incl. `parity --quick`) | 36 s |
| `--batch anchors_v1` cold (control computed) | 33 s |
| `--batch anchors_v1` warm (control cached) | 17 s |
| per arm: `r2_control/R2` (stage 1) | 5.7 s |
| per arm: `vol_shape_p15/p15` (stage 1 + whole stage 2) | 16.9 s |
| `tests/run.sh` (52 tests in v1; **99 in v2**, §7) | 9 s / 10.6 s |
| `smoke --ideas a,b,c` (v2, no simulation) | 1.2 s |
| `probes/run_probe_batch.py` (v2, isolated ledger, 3 arms, both stages) | 49 s |

Arms run in separate processes (`--workers`, default = the CPU count) and share one cached control
record, so ~100 stage-1-only arms is 1–2 minutes on this machine. Re-running a batch reproduces
`RESULTS.json`, `SCOREBOARD.md`, `_control.json`, the per-arm files and `DEFLATION.json` **byte for
byte** (`diff -r --exclude=timing.json` is silent); `timing.json` — per-arm wall clock plus the
ledger's line count — is the one documented non-deterministic file.

## 6. What a user does next

Add `research/ideas/<idea>.py`, `exit` the editor, `git commit` it (the runner will refuse
otherwise), append the arm to `PREREGISTRATION.md` if it introduces a new gate or sweep, then

```sh
PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.batch_screen.run \
    --batch <their_batch> --ideas <idea>,... [--stage1-only]
PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.batch_screen.run --deflate
```

The stage-1 pass rate then decides what the expensive holdout set is spent on, and the deflation
keeps the survivors of a hundreds-of-ideas search honest. `README.md` documents the API, the exact
gate thresholds, the deflation method and its four stated assumptions.

## 7. v2 follow-up (2026-10-16): extra data, applicability, smoke, merge safety

Before the plugin-writing workers start on their own branches, the framework gained the four things
they need. `research/batch_screen/PREREGISTRATION.md` §6.1 fixes the rules (committed **before** any
of it ran: `33a068ff`); nothing about the engine, the gates G1–G6/G8/G9, the seeds, the sample sizes
or the control changed.

**1. Extra data.** `data.extra(key[, ffill])` returns a registered series as `(P, T)` aligned to the
panel's sessions by date (`NaN` where the series has no value), and inside a bootstrap chunk to the
drawn *source* session each column replays. `EXTRA_DATA.json` declares 32 keys / 31 series — `^VIX`,
`^VIX3M`, `^VXN`, FRED `DGS10`/`T10YIE`/`DTWEXBGS`/`BAA10Y`/`CPIAUCSL`, `SMH`, `SOXX`, `HG=F`,
`SHY`/`GLD`/`TLT`/`IEF`, the nine sector ETFs, `^STOXX50E`/`^GDAXI`/`^N225`/`^FTSE`/`^IXIC`/`^GSPC`/
`^NDX`, and `local_index` (a window map: `^NDX` on main, the market's own index on each OOS window)
— each with its file, source URL, date range, rows and SHA-256, all 31 fetched from the Yahoo chart
API and FRED CSV and pinned in `DATA_MANIFEST.json` (`extra_data`). `extra.py --fetch-all` refreshes
them, `--verify` re-hashes every file and re-reads every declared range. No kernel, no engine file
and no published number is touched: `extra()` is numpy inside `targets`.

**2. Availability.** `targets` may raise `api.NotApplicable(reason)`. Stage 1 requires the main
window to be applicable (otherwise the arm is recorded `not_applicable` with the stage and the
reason, and never screened further). In stage 2 the six OOS windows and the 21 ETFs are evaluated
on the applicable ones only; G7 then allows one outlier among the `k ≥ 3` applicable windows
(`n_c ≥ k − 1`, `n_d ≥ k − 1`; the published rule at `k = 6` is unchanged) and G8 runs on the
applicable market subset with the control re-drawn in the *same* draw, so the Δ stays paired. An
arm with fewer than 3 applicable OOS windows or fewer than 15 applicable instruments is **not** a
pass: `verdict = holdout_insufficient`, listed in the scoreboard's "manual confirmation" section
with the counts and the reasons, for its own pre-registered confirmation study. Registry and N are
unchanged.

**3. Smoke.** `python -m research.batch_screen.smoke --ideas a,b` calls each arm's `targets` on a
synthetic panel, the real main window, the real `M2` OOS window, the real `XLK` instrument window and
a real block-bootstrap chunk, and computes **no** performance number (no return, no NAV, no gate),
writes no registry line and reads no result file. It reports shapes, the leg, the extra keys the arm
touched and each key's `NaN` share per panel (so an author sees that `^VIX` is 100 % missing on
`M2`), and prints the traceback tail of a genuine bug. `--keys` prints the extra-data key table with
per-window coverage.

**4. Merge safety.** `.gitattributes` marks `REGISTRY.jsonl` `merge=union` (append-only lines from
parallel plugin branches merge without conflict) and the runner re-verifies the whole ledger before
every batch (`--verify-registry` prints the report): each line's `code_hash` must be the SHA-256 of
that module at the commit the line names, and each registered arm must still exist at HEAD with its
latest recorded `spec_hash`. A failure aborts the run; a module that changed since its last screen
is reported as `stale` (information, not an error).

**Evidence (all on this branch).**

| check | result |
|---|---|
| `--harness` | **PASS, 59/59** checks, 33.8 s (unchanged count; runs `parity --quick` too) |
| control record vs the committed `results/anchors_v1/_control.json` | **byte-identical**, worst float diff 0.0 |
| `SCOREBOARD.md` regenerated from the committed `anchors_v1` files | **byte-identical** |
| `probes/run_probe_batch.py` (isolated ledger + results, real stage 1 + stage 2) | `yield_veto` extras veto → **reject_stage1**; `dgs10_gate` (4 applicable OOS) → G7 on 4 windows (`n_within_required` 3), G8 on `{M1, M3, M4, M5}`, **reject_stage2** on E1; `gld_gate` (1 applicable OOS) → **holdout_insufficient**, 21 applicable ETFs, listed for manual confirmation; 49 s |
| `tests/run.sh` | **99 passed**, 10.6 s (was 52) |
| the idea bank's `new_downloads` | 4 of 5 registered (`SMH`, `HG=F`, `SHY`, `CPIAUCSL`); the FOMC calendar (IB17) is not market data — it is registered by hand and `--verify`d |

`probes/` holds the two engineering fixtures (never registered as trials: they live outside
`research/ideas/`), and `TEMPLATE_plugin.py` is the copy-me starting point that exercises both
`data.extra` and `NotApplicable`.
