# batch_screen_v1 — pre-registration (committed before any non-control arm is screened)

The deliverable is the **framework**; no trading idea is evaluated by it. This file fixes the
framework's protocol — every stage constant, gate threshold, seed, sample size and the deflation
method — and the arms of the demonstration batch, *before* that batch runs. The framework's code
is the reference for the mechanics; this file pins the numbers so that a later change to any of
them is visible as an amendment.

## 1. Control

`C0 = k_control/K`: production K (`equity_leveraged_trend_v1` 1.2.0) = the production target
series (E2's 2-of-3 vote on the QQQ price close + the half-weight early re-entry, sized
`min(1, 0.35/vol20)` on the held leg's vol, cap 0.98, decided at the close, filled next open,
0.5 pp band) under `fe_sim.RULE["K"]` (A1u's USD-gain-scaled downsize band + H2's December
postponement), leg `2x`, no cash yield. Its record is computed once per batch
(`results/<batch>/_control.json`) and every arm's Δ is measured against it on the *same*
resampled paths.

`R2` (`r2_control/R2`) is the same target series under `fe_sim.RULE["C0"]`: the campaign's
reference row, reported for every arm.

## 2. Demonstration arms (all parameters fixed)

| arm | rule | leg | sizing | p | c |
|---|---|---|---|---|---|
| `k_control/K` (control) | `K` | 2x | `min(1, 0.35/vol20)` | — | — |
| `r2_control/R2` | `C0` | 2x | `min(1, 0.35/vol20)` | — | — |
| `vol_shape_p15/p15` | `K` | 2x | `min(1, c/vol20**p)` | 1.5 | 0.24593188954326456 |

`vol_shape_p15` copies `research/vol_shape_v1`'s frozen `PRE_C["E3"]` (calibrated on
^NDX 1985-10-01 → 2000-03-08 by matching `mean(vt)` to K's, before its main window was looked at).
Nothing else is swept: the demonstration batch exists to prove the framework reproduces published
numbers and exercises one pass and two fail paths, not to search.

## 3. Data and mechanics (fixed)

* Main window: QQQ/QLD* (synthetic daily-reset 2x), `data/historical/stress_robustness_v1_daily.csv`
  + `…_dtb3.csv`, sessions 2000-03-09 → 2026-09-29 (`i0 = 253`, the last session), base
  financing (`spread = 0`) and calibrated (`0.0075`), `EXP_Q = 0.0095`, `COST_PER_SIDE = 4e-4 × m`,
  `m = 2` (m = 1 for G6, m = 4 reported as a stress), one instrument per account, next-open fills.
* After tax: the corrected multi-instrument Turkish engine (`research/fast_engine.fe_tax`) —
  CBRT USD/TRY, FIFO lots, Yİ-ÜFE indexation, the 2026 tariff, instalments, full liquidation at
  the window end; capital `$100k`; windows 2000-03-09→2026-09-29, 2010-01-01→end, 2017-01-03→end.
* Stage 1 bootstrap: seed **20261011**, block **20**, 5,000 pre-tax / 1,000 after-tax paths, $100k,
  `progressive` (B1-recent) and `flat` (B2). This is the campaign's published clean bootstrap.
* Stage 2 holdout: the six OOS windows (`^GSPC` 1957-1999, M1–M5, calibrated 2x, R-USD rule,
  m = 2), the 21 quality-passing ETFs (pre-tax R-USD and after-tax R-INDEX $100k), the 2010/2017
  after-tax windows, the 21 January rolling starts (2001–2021, after tax $100k), the fresh-seed
  bootstraps (seed **20261016**, blocks **10** and **40**, 1,000 after-tax paths, $100k, both
  modes) and the pooled OOS bootstrap (M1–M5, 2,000 paths each, one shared rng, seed 20261011,
  block 20).
* Legs: `2x` (published synthetic daily-reset 2x, expense 0.0095) and `1x` (the same input triple
  at gearing 1, no cost). Per-session instrument switching is **not** implemented (one instrument
  per account) and is refused as `needs_kernel`.
* Cash yield: off (`cash_yield = "none"`) for the whole batch. Turning it on changes every
  published cell, so it is a batch-level choice (`--cash-yield tbill`), never per arm.

## 4. Gates (fixed; one pass rule per stage)

**Stage 1 (screen — main window only).** All four must hold:

1. after-tax $100k CAGR delta on 2000-03-09→2026-09-29 `> 0` (strictly),
2. B1-recent bootstrap median delta `> 0`,
3. pre-tax max DD ≤ K + 2 pp (base **and** calibrated, m = 2),
4. pre-tax turnover ≤ 2 × K (base **and** calibrated, m = 2).

**Stage 2 (holdout — runs only for stage-1 survivors).** The framework's full set:

* G1 (base, calibrated): CAGR ≥ K, or Calmar ≥ 1.10 × K **and** CAGR ≥ K − 0.5 pp.
* G2 (both): max DD ≤ K + 1 pp.
* G3 (both): 2017+ CAGR ≥ K − 1 pp.
* G4: CAGR above K in ≥ 2 of 3 sub-periods (base m = 2).
* G5 (both): pre-tax bootstrap median Δ ≥ 0.
* G6 (both): G1 at m = 1.
* G7: six OOS windows — median Δ CAGR ≥ 0, ≥ 5 of 6 windows Δ CAGR ≥ −0.5 pp, ≥ 5 of 6
  Δ max DD ≤ +3 pp, and max Δ max DD ≤ +8 pp.
* G8: pooled OOS bootstrap median Δ ≥ 0.
* G9: turnover ≤ 2 × K (base and calibrated).
* After tax: Δ > 0 at $100k in **all three** windows; B1-recent and B2-flat median Δ ≥ 0 **and**
  share Δ > 0 ≥ 55 %.
* E1: the 21 ETFs after tax (R-INDEX, $100k) — median Δ > 0 and share Δ > 0 ≥ 60 %.
* E2: the 21 January rolling starts, after tax — median Δ ≥ 0 and share Δ ≥ 0 ≥ 60 %.
* FRESH: both fresh seeds' blocks (10, 40) × both modes — median Δ ≥ 0 and share Δ > 0 ≥ 55 %.

**Deflation (5 %).** A candidate is declared only if it passes stage 2 *and* the multiple-testing
test at α = 0.05 over every registered trial:
`rw_stepdown` (Romano–Wolf stepM, studentized max statistic on the B1 Δ vectors, 2,000
second-level resamples, seed 20261016) `p_adjusted ≤ 0.05`, **and** `deflated_sharpe`
(Bailey–López de Prado 2014) `dsr ≥ 0.95` with N = the number of lines in `REGISTRY.jsonl`.
`white_rc` is reported alongside. The method and its assumptions are documented in `README.md`.

## 5. Selection rule and reporting

* The control arm is reported for reference and is never a candidate (its Δ is 0 by construction).
* An arm that fails stage 1 is registered with `verdict = reject_stage1` and is not run at
  stage 2; an arm that fails stage 2 is registered with its full gate record (so N counts it).
* Stage-1 and stage-2 records are written to `results/<batch>/<idea>__<arm>.json`; the batch's
  `RESULTS.json` is the aggregate and must be byte-identical on a re-run.
* No gate, threshold, seed, sample size or arm may be changed after seeing data. Any change goes
  into an "Amendments" section here, dated, with the reason.
* Expected outcome of the demonstration batch (stated before running): `k_control/K` is the
  control; `r2_control/R2` fails stage 1 on the after-tax 2000-window gate (published Δ ≈ −0.59 pp
  vs K); `vol_shape_p15/p15` passes stage 1 and **fails stage 2 on G8** (its published pooled-OOS
  evidence is negative), matching `research/vol_shape_v1`'s own verdict.

## 6. Amendments

* None as of the first (demonstration) batch run. Any later change to a gate, threshold, seed,
  sample size or arm is appended here, dated, with the reason.

### 6.1 2026-10-16 — extra data, applicability, smoke, merge safety (framework v2)

Committed **before** any non-control idea arm of any follow-up batch is screened (no arm other than
the three demonstration arms above has been run at this date). Reason: the idea bank
(`docs/research/idea_bank_v1.json`, 119 arms) needs series beyond `MarketData`, windows where an
arm's inputs do not exist, a way to test a plugin without seeing results, and a registry that
survives several plugin branches merging into one.

* **Extra data.** `data.extra(key)` returns the registered series aligned to the panel's sessions
  as `(P, T)`, `NaN` where the series has no value on that session. The registry
  `research/batch_screen/EXTRA_DATA.json` declares, per series, its file, column, source URL,
  date range and SHA-256, and per key its window→series map. Keys are the same in every window; a
  key whose window map has a `"*"` default (and, for the bootstrap chunks, the `"main"` entry)
  resolves per window. Downloads are restricted to the Yahoo chart API and FRED CSV; the helper
  writes one wide CSV per source and recomputes the registry's hashes. Nothing about the engine
  changes: `extra()` is plain numpy evaluated inside `targets`, so the kernel and the published
  control numbers are untouched.
* **Applicability.** `targets` may raise `batch_screen.api.NotApplicable(reason)`. Stage 1
  requires the **main** window to be applicable: an arm that declares N/A on the main window (or
  inside the stage-1 bootstrap) is recorded with `verdict = not_applicable` and is not screened
  further. In stage 2 the six OOS windows and the 21 ETFs are evaluated **only on the applicable
  ones**, and the pooled OOS bootstrap (G8) runs on the applicable market subset with the control
  re-run in the *same* draw (so Δ stays paired); the applicable subset is recorded in the record.
  Reduced G7 at `k < 6` applicable windows (k ≥ 3 required): median Δ CAGR ≥ 0, ≤ 1 outlier
  (`n_c ≥ k − 1`, `n_d ≥ k − 1`), max Δ max DD ≤ +8 pp. E1 keeps its median/share rule on the
  applicable instruments.
* **`holdout_insufficient`.** An arm that comes through stage 1 but has fewer than **3**
  applicable OOS windows or fewer than **15** applicable instruments is **not** a pass: it is
  recorded with `verdict = holdout_insufficient`, is never declared, and is listed in the
  scoreboard under "manual confirmation" — the reduced-holdout convention the idea bank
  pre-registers for its non-US arms (as `credit_vote_v1` did). The registry and N are unchanged:
  every such arm keeps its ledger line.
* **Smoke.** `python -m research.batch_screen.smoke --ideas a,b` runs each arm's `targets` on a
  synthetic panel, on the real main window, on one OOS window, on one ETF and on a real bootstrap
  chunk, and computes **no** performance metric (no returns, no NAV, no CAGR). It reports shape,
  finiteness, the leg, the extra keys the arm touched and each key's NaN share; it never writes to
  the registry.
* **Merge safety.** `REGISTRY.jsonl` is append-only and marked `merge=union` in `.gitattributes`,
  so parallel plugin branches merge without conflict; the runner re-verifies before every batch
  that each ledger line's recorded `code_hash` is the SHA-256 of that module at the line's own
  recorded commit, and that every registered arm still exists at HEAD with its latest recorded
  `spec_hash`. A failed verification aborts the run (`--verify-registry` prints the report).
* **Unchanged.** The gate thresholds G1–G6, G8, G9, the after-tax windows, B1/B2, E1/E2, FRESH,
  the seeds, the sample sizes, the deflation and the control are exactly as in §§ 1–5. For an arm
  that is applicable everywhere the record, the scoreboard and the results files are byte-identical
  to what the v1 framework wrote.
