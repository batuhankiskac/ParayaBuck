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

### 6.2 2026-10-16 — reduced windows, the panel column calendar, two-leg fill options (framework v2)

Committed **before** any idea arm uses any of `spec["start"]`, `spec["inputs"]`, `spec["fill_knobs"]`
or `spec["kernel_scope"]`. The three plugin fixtures that exercise them (`probes/two_leg.py`'s
`gld_offstate`, `onex_offstate`, plus the harness's knob-routing cells) are engineering probes,
outside `research/ideas/`, registered as no trial. Reason: `idea_bank_v2`'s IC31–IC46 and
`idea_bank_v1`'s IB25/IB26 hold a second instrument or a second book that starts after 2000-03-09
or that only exists as a fill decision, and the framework as of §6.1 could only refuse them.

**Reduced window (`spec["start"]`, `spec["inputs"]`).** An arm may declare the window it is
evaluated on:

* no declaration — the published window (2000-03-09 → 2026-09-29), unchanged: every registered arm
  and every published number keeps its exact code path;
* `start = "YYYY-MM-DD"` — the first session at or after it, never before the published warm-up
  start (session `START` = 253 = 2000-03-09) and never after the panel's end;
* `start = "auto"` — the later of the published start and **the first session every declared input
  covers, plus one session** (the engine reads the previous close at `i0 - 1`). The declared inputs
  are `spec["inputs"]` plus `spec["leg_b"]` when that is an extra-data key (a panel leg has the
  window's own history). This is the rule that unblocks a second instrument which starts later
  (IEF 2002-07, TIP 2003-12, GLD 2004-11, GDX 2006-05, HYG 2007-04, LQD 2002-07 …): the framework
  refuses a leg-B series that does not reach every session the engine reads rather than invent a
  price, so before the input's own start the arm simply is not evaluated.
* A window shorter than **10 calendar years** (its first session's previous session to 2026-09-29)
  is `NotApplicable`, as is an `"auto"` window whose declared input has no value anywhere.

Stages on a reduced window: the pre-tax block (`main.*`), the three after-tax windows (each
declared window's start is **clipped up** to `i0`; no window key is dropped, the record carries the
effective window per key), the 21 January rolling starts (**the starts that begin before `i0` are
dropped**; below 15 applicable starts the arm is `holdout_insufficient`), and both block
bootstraps (**the drawn pool is the window's own sessions** — one `integers`, one `random`, one
`integers` per path, the published order, at the published seed and block, with `npool` = the
window's session count). The six OOS windows, the 21 ETFs and the pooled OOS bootstrap are
**unchanged**: they are other markets with their own pools. A pre-tax subperiod (P1–P3) whose end
precedes the window's first session is not covered (`subperiods_not_covered`), and G4 then judges
the covered ones and requires at least two of them covered.

**Control.** K is re-simulated **on the same window**: the same `i0`, the same clipped after-tax
windows, the same dropped rolling starts, the same restricted pool (`_control_<tag>.json`, e.g.
`_control_w20041119.json`; the published window keeps `_control.json`). Every Δ of such an arm is
therefore paired *inside its own window*. The record carries the window (`win`, the stage-1
`window` block, each after-tax cell's own window) and the scoreboard shows its tag.

**Deflation.** The B1 Δ vector is the arm's *paired advantage over K on the window it declared* —
which is the quantity comparable across arms — and `DEFLATION.json` names the pools in play
(`window_tags`). Caveat, stated: arms on different windows do not share a pool, so their p-values
are not the published single-pool p-values; the test is still on paired Δ vectors (each arm
against its own window's K), which is what the screening decision is built on. An arm whose Δ
vector is not finite is **left out** of the deflation (reported in `excluded_non_finite`) rather
than making every other arm's p-value NaN; it can never be declared (its stage-1 after-tax gate is
NaN and fails closed).

**Two-leg fill options (`spec["fill_knobs"]`, `spec["kernel_scope"]`).** An arm with `leg_b` may
declare options that `research/fast_engine2/fe2_opts.py` (a copy of the two-leg kernel; the
published `fe2_sim`/`fe2_boot` are untouched) implements. **Every option off reproduces the current
kernel bit for bit** (`fe2_opts_parity`, in the harness: 28 cells, `max_abs_diff == 0.0` on the
main window and the bootstrap):

* `band_score`: `"usd"` (the published K A1u fraction) | `"indexed"` (the TRY-indexed FIFO gain
  fraction of the units the sell would consume, Yİ-ÜFE indexation applied as the engine applies it)
  | `"max"` (the larger of the two). K's widths key on that fraction. `band_step4_pp` adds a fourth
  step above a fixed `0.15` fraction. Needs a rule with the A1u band on (K/A1u).
* `route_cap_pp`: on a session where both legs downsize, units are routed from the other leg to the
  leg with the lower indexed fraction, bounded by the cap (a weight), by the other leg's own sale
  and by the cheap leg's remaining units; the routed quantity is what the band test and the fill
  use.
* `loss_net_frac` (+ `loss_net_min_ytd_try`): after a permitted downsize fill with a positive
  realised TRY gain and only in a taxed mode, the other leg's loss lots are sold (FIFO, indexed per
  unit) up to that fraction of the gain, if the year-to-date realised gain exceeds the threshold.
* `kernel_scope`: `"all_stages"` (default) or `"after_tax_stage_only"` — the latter (IC42/IC43's
  own declaration) runs every pre-tax stage on the published kernel, so only the taxed stages carry
  the options.

Still `NEEDS_KERNEL`, named rather than approximated: a **per-session instrument switch** (no arm
needs it any more — IC31–IC38 are expressible as a fixed leg B whose weight varies, and
`probes/two_leg.py::gld_offstate`/`onex_offstate` demonstrate both forms), a **third instrument**
(IC36's GLD/IEF/SHY blend and IC34 arm C's XLU+XLP pair need three books; a derived blend series
would hide the sleeve's internal rebalancing from the taxpayer and leave it without a per-instrument
FIFO book, so it is not used), a **cash loan** (IC39), a **second leg family** (IC40's SPY-based 2x
leg), and **two books of the same instrument** (IB38's A/B/C).

**Unchanged.** The gate thresholds G1–G9, the after-tax rule, B1/B2, E1/E2, FRESH, the seeds, the
sample sizes and the deflation procedure are exactly as in §§ 1–5 and §6.1. An arm that declares
none of the four keys takes the v1/v2 code path byte for byte.

### 6.3 2026-10-16 — pre-screen amendment of `batch_bank_v3`: the `2x_spy` panel construction (B1)

Committed **before any arm of `batch_bank_v3` is screened** (no arm of that batch had been
simulated at this date). Reason: `research/plugins_d3/BUGS.md` B1. Three plugin branches wrote
their arms blind; one of them (`IC40`, three arms holding the option leg `2x_spy` of
`research/fast_engine_opts`) cannot run as committed, and the refusal is a framework plumbing bug,
not a missing mechanism. Fixing it before the batch is the only way to screen those three arms with
the batch's other arms instead of a separate study.

* **The bug.** `api.panel` builds only the window's own `2x`/`1x` legs; `MarketData.__post_init__`
  then reads `legs[self.leg]`. Every non-bootstrap call site built its panel with the arm's own leg
  (`windows.main_panel` / `oos_panel` / `instrument_panel` called from `stages.stage1`,
  `stage2_main_windows`, `stage2_oos`, `stage2_instruments`, `stage2_rolling`, plus
  `smoke.real_panels`), so a `leg = "2x_spy"` arm raised `KeyError: '2x_spy'` **while the panel was
  being built**, before `bridge.prepare_panel` (which adds the SPY leg) was ever reached. The
  bootstrap path worked only because `boot.boot` builds its panels with `kernel_opts.base_leg(...)`.
* **The fix.** `stages.panel_leg(arm)` returns `research.fast_engine_opts.bridge.base_leg(arm["leg"])`
  and is used at all six `windows.*_panel` call sites; `smoke.real_panels`/`smoke_panels` build with
  the base leg and prepare each panel for the arm (`smoke._prepared`, passing the drawn chunk
  positions to `prepare_panel` exactly as `boot.boot` does). `run_one` and `boot.boot` add the
  option leg as before, so **no** other arm's panel, gate or number changes: an arm that does not
  hold `2x_spy` takes the identical code path (the harness's 116 checks, the two-leg parity cells,
  `fe2_opts_parity` and every published anchor are unchanged; the regression test is
  `tests/test_spy_leg.py`).
* **`IC40`'s arms.** Their `needs_kernel` declaration is removed (the arm specs' own parameters —
  `rule`, `leg`, `cash_yield`, `vote`, `inputs` — are untouched), so `IC40/A`, `IC40/B`, `IC40/C`
  are screened by this batch like any other arm. The three arms were never registered in the ledger
  before this batch, so no `spec_hash` is superseded.
* **Batch composition (fixed here, before the run).** `bank_v3` screens the `idea_bank_v2` ideas
  `IC31`–`IC46`, `IC49`, `IC52`–`IC59` as committed by the plugin workers, the two `idea_bank_v1`
  re-arms `ib25_RW`/`IB26_RW` (reduced windows), and `IC59_K` — the *runnable* twins of `IC59`'s
  three arms under the `band_up` kernel option, in their own module because `IC59.py`'s module-level
  `NEEDS_KERNEL` applies to every arm of that module and re-hashing its pinned specs would make
  `verify_ledger` report `spec_drift`. `IC59`'s original arms are listed in the batch as well and
  stay registered-but-refused (`needs_kernel`).
* **Unchanged.** Every gate, threshold, seed, sample size, the control (production K), the reduced
  window rules of §6.2 and the deflation procedure are exactly as in §§ 1–5, §6.1 and §6.2. This
  amendment changes no arm's parameters, no gate and no number; it removes a construction refusal
  and records the batch's arm list.
