# state_defer_v1 -- state-keyed realization deferral with a hard drift cap (idea_survey_v5 I3)

Pre-registration. Written and committed **before any non-control arm was simulated on real data**.
Control = production **K** (`equity_leveraged_trend_v1` 1.2.0 = E2 + R2 early re-entry + A1u + H2);
**R2** (the engine's `C0`) is reported as a reference row, never as a candidate.

## 1. Question

`monthly_defer_v1` generalized H2's December deferral to all twelve YI-UFE month boundaries and
failed only its pre-tax bootstrap (G5), and its own caveat named the missing arm: "*a version that
deferred only when the indexed gain is positive (dropped from the arm set) is the natural next
test*". `idea_survey_v5` I3 turns that into a **state-keyed** deferral: defer a downsize when the
*fill's tax state* says so, not when the calendar does, and bound the stale weight with a hard drift
cap (the risk control H2_20 lacked).

Does a downsize deferred on the fill's own tax state -- with a hard drift cap -- beat K after
Turkish tax, at the same pre-tax return and drawdown?

## 2. Arms (every parameter fixed here; nothing is searched)

| Arm | Rule |
|---|---|
| **C0 = K** | production K, bit for bit: A1u's USD gain-scaled downsize band (0.5 / 3 / 8 pp at `g_frac ≤ 1 % / ≤ 5 % / > 5 %`) + H2 (a discretionary downsize filling in one of the last **10** December sessions is postponed). |
| **D1** | K with H2 replaced by: a pending discretionary downsize whose **fill** session is one of the last **10** sessions of its **calendar month (all twelve, December included)** is postponed while the read-only FIFO walk of that sale nets a **positive indexed-TRY gain** (GVK mük. 81: cost basis rescaled by `Yİ-ÜFE(M−1)/Yİ-ÜFE(purchase month)`, 10 % floor). One postponement episode lasts at most **21** consecutive suppressed sessions. No drift cap. |
| **D2** | D1 + **hard drift cap**: never postpone while `held − target > 10 pp` (fill immediately at the next open). |
| **D3** | K with H2 replaced by, with **no calendar at all**: postpone while the estimated tax of the fill exceeds **0.05 % of NAV**, where `est_tax = marginal_rate(fill year's YTD realised TRY gain + this sale's indexed-TRY gain) × this sale's indexed-TRY gain` (0 when that gain is not positive), `marginal_rate` = the 2026 tariff bracket the base falls in (190 k / 400 k / 1.0 m / 5.3 m TRY at 15/20/27/35/40 %, deflated by the previous October's Yİ-ÜFE), and NAV is the session's **open** NAV in USD converted at that session's CBRT buying rate. Episode cap **21** sessions. Drift cap **10 pp**. |
| **D4** | K's H2 with the December window widened from the last 10 to the last **20** December sessions, plus the drift cap **10 pp**. (The survey's measured upper bound, H2_20, with the risk control it lacked.) |

Shared, fixed: every arm keeps **A1u** and the 0.5 pp band; the target series is production R2's,
bit for bit; an overlay can only *suppress* a fill, never create one; **exits to zero, up-resizes
and tax-funding sales are never postponed**; the ordinary rule is re-evaluated at every close; H2's
own `defer` device is **off** in D1-D4 (the new device replaces it, it does not stack with it); the
state is computed with indexation **on in every mode**, so the pre-tax leg prices the *same*
decision series tax-free.

### 2.1 Reading of the survey's arm text (fixed before any run)

`idea_survey_v5` §4.3 specifies D1 as "*positive indexed-TRY gain; max 21 sessions; all twelve month
boundaries, December included*". This pre-registration fixes the reading:

- "max 21 sessions" is the **cap on one postponement episode** (the same parameter, with the same
  meaning, as in D3, where no calendar exists for it to describe otherwise). It is *not* the tail
  width: D1's tail width is **10**, the window of the arm it is explicitly derived from (the dropped
  `monthly_defer_v1` variant of its A1). With a 10-session tail the cap is a backstop that rarely
  binds; the harness reports the realized maximum episode length so a reader can see whether it did.
- "all twelve month boundaries, December included" is the **trigger** (the last 10 sessions of every
  calendar month), and the state condition is an additional gate on it.
- "defer" means **suppress and re-evaluate at every later close** (H2's proven mechanism, as
  `monthly_defer_v1` resolved it), not a forced fill at the first session of the next month.

### 2.2 D1-D4 are the LAST arms on this mechanism

The band wave (`gain_band_v1/v2/v3`, `gain_band_integration_v1`) closed because the loop "one more
threshold" never ends. This pre-registration therefore states: **D1, D2, D3, D4 are the last arms
ever run on the state-keyed deferral mechanism.** No threshold, window length, cap, episode length,
state variable or composition of them may be added, widened, narrowed or re-tuned after this file is
committed -- including after a FAIL. The four thresholds that exist (10-session tail, 21-session
cap, 0.05 %-of-NAV tax fraction, 10 pp drift cap) are quoted verbatim from the survey and none is
searched.

## 3. Data

No download. Every input is an existing committed repository file, restored into this worktree from
its tag; source, date range and SHA-256 are in `DATA_MANIFEST.json`. The engine, the reference
campaign's `RESULTS.json`/`H2_ANCHORS.json` and the harness inputs (`trend_robustness_v2`,
`band_h2_combo_v1`) are restored from `archive/research/fast-engine-v1-2026-10` and
`archive/research/main-research-dir-2026-10`; the reference numbers are read out of the restored
`research/band_h2_combo_v1/RESULTS.json`.

## 4. Mechanics

- Decide on the completed close, fill at the **next open**; 0.5 pp resize band; entries and exits to
  zero always trade; idle cash earns **zero**; cost `4 bps × m × |Δw|`; synthetic **QLD\*** =
  daily-reset 2× on QQQ total return, 0.95 %/yr expense, financing DTB3 + {base 0.0 %, calibrated
  +0.75 %}/yr.
- Main window **2000-03-09 → 2026-09-29** (6,679 sessions), m = 1/2/4, base/calibrated, plus the
  R-USD row; sub-periods P1 ≤ 2008-12-31, P2 2009-2016, P3 2017 →.
- **After tax**: the corrected Turkish engine (`fe_tax`: per-calendar-year netting in TRY,
  trade-date years, Yİ-ÜFE indexation with the 10 % floor, the 2026 tariff scaled by the previous
  October's index, FIFO, instalments 31 Mar / 31 Jul funded by selling, liquidation at the window
  end), windows 2000-03-09 →, 2010-01-01 →, 2017-01-03 →; capitals **$10k / $100k / $1M** (gates at
  $100k).
- **Bootstraps**: clean B1-recent (progressive) and B2-flat (20 %, losses carried forward) on the
  fixed real 2015-09 → 2026-09 calendar with its realised USD/TRY and Yİ-ÜFE, block 20, seed
  **20261011**, 1,000 after-tax / 5,000 pre-tax paths, $100k and $1M; E3 (seed 20261015, blocks
  10/40) as a reporting robustness block. The pooled OOS bootstrap (G8) is block-20 over M1-M5, seed
  20261011, 2,000 paths per market, one shared generator, markets in `MARKETS` order.
- **Yİ-ÜFE month convention** (fixed here): the state uses the engine's own convention --
  `Yİ-ÜFE(M−1)` for a sale in month `M` -- everywhere, and `Yİ-ÜFE(M−2)` for the first **three**
  sessions of a month is reported as a **report-only sensitivity row** for D3 (the only arm whose
  trigger can fall in a month's opening sessions, where `M−1` is not yet published). The sensitivity
  row is not a selection device and no arm is re-run with it.
- **Disclosed asymmetry**: D3's threshold is NAV-relative, so its decisions are a function of the
  account's own NAV and differ slightly between the pre-tax leg and the tax-charging runs (the tax
  paid lowers NAV). Both are reported. The calendar/gain conditions of D1/D2/D4 are mode-independent.

## 5. Engine and parity

The mandatory fast engine (`research/fast_engine/`) drives every simulation, after-tax account and
bootstrap. The new execution mechanism lives in a **fork** of the engine under
`research/state_defer_v1/engine/` (never an in-place edit): `fe_sim.sim_one` gains one option
(`dfr_trig`, `dfr_state`, `dfr_max`, `dfr_drift`, `dfr_ufe_lag` + the `mtail`/`dec_wide`/`early_m`/
`upm2` tables), `fe_boot._boot_chunk` passes it, `fe_tax` exports its tariff arrays, and `fe_study`
gains the pooled OOS bootstrap. With `dfr_trig == 0` the option is off and the fork must be
**bit-identical** to `research/fast_engine` on the returned dicts, the NAV path and the decision
trace -- the `--harness` **option-off parity cell** asserts `max|diff| == 0` over the engine's four
arms × three modes × two capitals × two cost levels.

## 6. Gates (all must pass; control = K)

`ewma_band_v1`'s G1-G9, unchanged, with K as the control:

- **G1 (m = 2, base and calibrated)**: CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp.
- **G2 (both)**: max DD ≤ K + 1 pp.
- **G3 (both)**: 2017+ CAGR ≥ K − 1 pp.
- **G4**: CAGR above K in ≥ 2 of 3 sub-periods.
- **G5 (both)**: block-20 pre-tax bootstrap (seed 20261011, 5,000 ten-year paths) median of arm − K ≥ 0.
- **G6 (both)**: G1 at m = 1.
- **G7**: six OOS windows (^GSPC 1957-1999, M1-M5), calibrated, m = 2: median Δ CAGR ≥ 0; Δ CAGR ≥
  −0.5 pp in ≥ 5 of 6; Δ DD ≤ +3 pp in ≥ 5 of 6 and never > +8 pp.
- **G8**: pooled out-of-sample bootstrap over M1-M5 (2,000 paths each) median Δ ≥ 0.
- **G9**: turnover/yr ≤ 2 × K.
- **T1**: after tax at $100k, **strictly beats K in all three windows** (Δ > 0).
- **T2**: clean bootstrap B1-recent at $100k: median Δ ≥ 0 **and** share of positive paths ≥ 55 %.
- **T3**: clean bootstrap B2-flat at $100k: the same.

**Two-tier treatment, pre-declared**: the `idea_survey_v3` §6.1 exposure-neutral non-inferiority rule
does **not** apply to this study. These arms hold a deliberately stale, above-target weight for up to
a month, i.e. they change realized average exposure by construction, so G1/G2 apply as written from
the first line. The realized mean weight and the mean |Δw| are reported for every arm anyway.

**Selection rule**: the passing candidate with the highest base m = 2 CAGR; if no arm passes every
gate, **no arm is selected** and the mechanism closes. No gate, arm, parameter or threshold is
changed after seeing any result; bug fixes go in an "Amendments" section of the report.

## 7. Mandatory fixed reporting

For every arm, beyond the gate table: the deferred **indexed-TRY gain** (per event and per episode),
the tax paid (USD and TRY), **deferrals per year by year**, the number and length of postponement
episodes, the distribution of **`held − target`** at deferral events (max and p95) and the path-wide
maximum, **sells/yr**, turnover, the `realised_gain_try_by_reason` split, and the **January-gap
episodes** (`ln(NAV at the last January close / NAV at the last December close)` per year, per arm
and Δ vs K, with the two published R2 episodes -- 2015-12→2016-01 and 2024-12→2025-01 -- reproduced
as a harness check). E1/E2/E3, the 21-ETF block and the six OOS windows are reported as reporting
cells (not gates), as the parent campaigns do.

## 8. Harness (must pass before `--run`; 0 checks outside tolerance)

`python -m research.fast_engine.parity --quick` must **PASS**; the option-off parity cell must be
exactly 0; the control must reproduce, within **0.01 pp**, K's and R2's pre-tax main-window anchors
(15.7649 / 15.1785 % and 15.4766 / 14.8961 % CAGR, max DD 46.6698 / 46.7948 % and 46.7584 /
46.8797 %) *and* the corresponding cells of the restored `band_h2_combo_v1/RESULTS.json`; the
after-tax $100k anchors (K 10.753 / 15.235 / 23.972 %, R2 10.160 / 14.706 / 22.886 %); H2's
deferral counts (51 pre-tax, 53/32/19 in the three after-tax windows) exactly; the mechanism
invariants (events == counter, only genuine downsizes, D1/D2 events inside the month tail, D4 inside
the 20-session December tail, episode starts within the drift cap, episode lengths within the cap,
sells ≤ K's); and the two published R2 January-gap episodes.

## 9. Ex-ante expectation (stated before the run)

The honest prior, from the survey and from `monthly_defer_v1`: **D1/D2 probably fail G5** (the
monthly overlay's pre-tax bootstrap median was −0.048 pp for its best arm) and **D3 probably
degenerates into "delay every downsize by 21 sessions"**, i.e. a different strategy with a large
tracking error; the most likely single outcome is that **the drift cap turns D2/D4 back into H2**
(or into something worse) and that no arm passes. D4 is the arm with a measured upper bound
(H2_20: +0.332 / +0.437 / +0.769 pp after tax at $100k) and its only recorded blemish (+1.87 pp worst
ETF Δ max DD against a +2 pp bar) is exactly what the drift cap is supposed to remove. A null result
closes the realization-timing family (`idea_survey_v5` bound C caps it at ~+1.6 pp/yr, unreachable)
and is a useful answer.

## 10. Reproduction

```
cd /Users/batuhankiskac/Desktop/ParayaBuck-wt/state_defer_v1
/Users/batuhankiskac/.venvs/pb-research/bin/python -m research.state_defer_v1.screen --smoke     # synthetic only
/Users/batuhankiskac/.venvs/pb-research/bin/python -m research.state_defer_v1.screen --parity    # the option-off cell
/Users/batuhankiskac/.venvs/pb-research/bin/python -m research.state_defer_v1.screen --harness   # anchors + parity + invariants
/Users/batuhankiskac/.venvs/pb-research/bin/python -m research.state_defer_v1.screen --run       # writes RESULTS.json / SUMMARY.txt
```

`--run` is executed twice; the two `RESULTS.json` must be byte-identical.

Not tax advice. The model caveats of `vt_level_tax_v1` / `tax_harvest_v2` / `monthly_defer_v1` apply
verbatim.
