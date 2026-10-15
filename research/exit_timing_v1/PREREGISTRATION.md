# exit_timing_v1 — exit-leg tax timing under K (idea_survey_v4 I4)

Pre-registered 2026-10-16, **before any non-control arm of this study was simulated on real
data on this branch** (see the pre-commit honesty note in section 10 for the three probes that
did run). Working branch `research/exit_timing_v1`; the code lives in
`research/exit_timing_v1/` (`screen.py`, `tax_engine.py`). Control identity: production **K** =
`equity_leveraged_trend_v1` 1.2.0 = E2 + R2 early re-entry + A1u gain-scaled downsize band + H2
December deferral.

## 0. Why this study exists

`docs/research/idea_survey_v4.md` §4 ranks **I4 — "exit-leg tax timing: the cuts the band cannot
reach"** fourth. The observation behind it: A1u defers a downsize between two positive weights
and **never** an exit ("entries, exits to zero and up-resizes keep 0.5 pp"), and H2 defers a
December downsize and again never an exit. But an exit to zero is the largest single realisation
the system makes — it clears the **whole** FIFO book in one fill, and after K that book is ~3×
older than R2's (33 → 8.7 sells/yr; the terminal liquidation gain grew 0.86 m → 3.41 m TRY).
A vote-off and the early path's SMA50 exit are 20–50 pp drifts, so the band cannot touch them.
The survey proposes two tax-only devices on that event: **X1** bring the year of the exit's fill
into the neighbour tax year (H2's mechanism, on the event H2 exempts), and **X2** stage it —
"sell only to the early weight when the consumed lots are gain-rich". It prices the pair at
**+0.02 … +0.15 pp** after tax and says the honest reason to run it is to *close the axis*.

## 1. Question

Do X1, X2 or X3 — the three exit-timing devices, alone and composed — beat production **K** on
the after-tax objective at $100k without a materially worse drawdown, on the pre-registered gates
of the common protocol (G1–G9 vs K, then T1–T3 after tax)? Because all three *delay de-risking*,
the drawdown and the named worst episodes are reported explicitly per arm (section 5), and no
non-inferiority relaxation is taken for these arms.

## 2. Data and provenance

**Nothing is downloaded.** Every input is an existing committed research file in this worktree
(`data/historical/`, unchanged from `band_h2_combo_v1`; `DATA_MANIFEST.json` records source, byte
count and SHA-256 of each). Uses: `stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY/QLD bars,
DTB3), `after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (TRY conversion, Yİ-ÜFE indexation),
`ewma_band_v1_{gspc,index,tb3ms}.csv` (the six OOS windows), `range_vol_v2_*.csv` (19) +
`range_vol_v1_{spy,dia,iwm}.csv` (the 21 quality-passing ETFs; `EWC` excluded by
`range_vol_v2`'s fixed quality rule). The TRY/Yİ-ÜFE calendar starts 2000-01 / 1999-01, so no
after-tax stage starts before 2000-03-09. Three further files are **evidence**, not inputs:
`research/gain_band_v3/RESULTS.json` (A1u's published cells, reproduced cell-by-cell),
`research/exit_timing_v1/H2_ANCHORS.json` (H2's published cells, copied from
`tax_harvest_v2`'s tagged `RESULTS.json`) and `research/band_h2_combo_v1/RESULTS.json` (K's
published cells — the source of every number in the `REF_K` / `TAX_ANCHOR_K` / `BOOT_ANCHOR_K`
constants, asserted against the file in `--harness`).

## 3. Control (C0 = K)

**Production K, bit for bit**, through the single sequential engine `tax_engine.py` (a byte copy
of `band_h2_combo_v1/tax_engine.py` = `gain_band_v3/tax_engine.py` = the `vt_level_tax_v1` /
`r2_tbill_combo_v1` corrected engine): R2's target series (E2 2-of-3 vote on the QQQ close + the
`0.5·vt` early re-entry leg, sized `vt = min(1, 0.35/vol20)`, cap 0.98) with K's execution layer
(decide on the completed close, fill at the next open, symmetric 0.5 pp band, H2's December
window tested first, then A1u's gain-scaled band, entries and exits to zero always fill, idle cash
zero, cost 4 bps × m × |Δw|, synthetic QLD\* = daily-reset 2× on QQQ total return, 0.95 %/yr
expense, financing DTB3 + {base 0.0 %, calibrated 0.75 %}/yr).

**Anchors asserted in `--harness`** against `research/band_h2_combo_v1/RESULTS.json` (pre-tax TOL
1e-4, after-tax 0.005 pp, the same rules the parent campaign used): pre-tax CAGR **15.7649 % base /
15.1785 % calibrated** (m = 2), m = 1 **16.0173 / 15.4303 %**, max DD **46.6698 / 46.7948 %**,
turnover **5.456136**; the R-USD main-window row (base 15.7649 %, calibrated 15.1785 %); after tax
at $100k **10.753 / 15.235 / 23.972 %** with max DD 46.670 / 44.627 / 34.977 % and the postponement
counters (1,836 band + 81 December for the main window); the m = 4 cost stress; the clean-bootstrap
medians (B1-recent 3.8577 %, B2-flat 4.0376 %); `H2 arm == simulate_band_overlay` to 1e-9. The two
reference labels are asserted too: **"C0" = production R2** against `fast_reentry_v1`'s anchors
(15.48 / 14.90 %, DD 46.76 %, after tax 10.16 / 14.71 / 22.89 %) and the pure-E2 series against
`e2_integration_v1`'s, so a drift of either label fails the harness. The `--harness` deep part then
reproduces the parent campaigns' published cells cell-by-cell (A1u vs `gain_band_v3`, H2 vs the
tagged `tax_harvest_v2`).

## 4. Arms (7 labels; every parameter fixed, no tuning)

| Arm | Rule |
|---|---|
| **K** | production K, verbatim (the control). |
| **C0** | production **R2** (K's execution layer off) — the report-only reference row, and the label the R2/E2 anchors are asserted under. |
| **A1u** | `gain_band_v3`'s A1u alone — reference label, needed by the deep reproduction block. |
| **H2** | `tax_harvest_v2`'s H2 alone — reference label, needed by the deep reproduction block. |
| **X1** | A fill that would be an **exit to zero** (`tgt ≤ 0` with a live position) whose **fill** session is one of the **last 10 December sessions** (the same `DEC_TAIL = 10` window H2 uses) is **not executed**. The target is re-evaluated at every close, so the exit fills at the first January decision; the window cannot re-apply (it is never postponed twice). Entries, up-resizes and every non-exit fill are K's, unchanged. |
| **X2** | A fill that would be an **exit to zero** whose **consumed FIFO lots have a USD gain fraction > 5 %** (the A1u USD formula, `rule_sale_usd` over the units the exit would consume, `max(0, gain) / proceeds`) **sells only down to the current early-leg weight** (`0.5 × vt` at that decision, cap-free because `0.5·vt ≤ 0.5 < 0.98`) instead of to zero. The remainder is held under a **hard time cap of `X2_CAP_SESSIONS = 21` sessions** after the staging fill, and is **exited in full at the first decision close at which the early conditions fail**; whenever the ordinary target turns positive again the rule takes the position over (the staged state ends) and the exit episode may not be staged again. |
| **X3** | **X1 + X2**: X1's December window is tested first, then X2's staging (so a December exit-to-zero is postponed whole and can be staged in January; a staged sale is an ordinary downsize between two positive weights and is therefore subject to K's H2 window and A1u band exactly like any other). |

**Shared mechanics.** Every arm runs the *same* target series (production R2's); only the fill
decision differs. Exits to zero always fill except where X1/X2 act; a suppressed or postponed
fill is never re-tried (the target is re-evaluated at every close); `Δw` and the fee are the same
as the arm's own decision.

### 4.1 X1's parameters

`DEC_TAIL = 10` (the last 10 NYSE December sessions per calendar year, by the **fill** session —
H2's exact window, reused so X1 is the same device on the event H2 exempts); the postponed exit
re-fills at the first session outside the window (the first January decision), never twice;
entries, up-resizes and non-exit downsizes keep K's rules.

### 4.2 X2's parameters

`X2_GAIN = 0.05` (the USD FIFO gain fraction of the units the exit-to-zero fill would consume, the
A1u-USD formula, floored at 0 by `max(0, ·)`); staged target `0.5 × vt` = the production early-leg
weight at that decision close; `X2_CAP_SESSIONS = 21` (the staged position is fully exited within
21 sessions of the staging fill); release: the early conditions failing, evaluated at every
subsequent decision close. One staging per exit episode.

### 4.3 The X2 reading, and why the device is structurally self-defeating on this rule

I4's X2 text is: *"A fill that would be an exit to zero and whose consumed FIFO lots have a USD
gain fraction > 5 % sells only down to the current early-leg weight (0.5 × vt), with a hard cap of
21 sessions and an immediate full exit if the early conditions fail."* **"The early conditions" is
read as the brief's own early-leg three-condition set** — QQQ close > SMA50 **and** QQQ vol20
below its 252-session median **and** QQQ close > its close 20 sessions ago. Every parameter is
fixed; the reading was chosen before any arm was simulated, on the mechanics alone:

1. **A three-condition *entry* gate is provably inert.** Under production R2, `tgt = 0` iff the
   early-leg factor is 0 at that close, and the factor is 0 only if the three-condition entry
   conjunction is false there (`r2_state`: `entry_cond[t] ⇒ factor[t] = 0.5` while E2 is off). So
   "stage the exit only if the early conditions hold" can never fire — there is no exit to zero
   with the conditions satisfied. X2 therefore takes the text's *stated* entry condition only
   (the gain fraction > 5 %) and uses "the early conditions" as the **release** clause, which is
   what the sentence says.
2. **A probe (see section 10) shows the two are anti-correlated on the real path**, which is why
   the release fires almost immediately: of the 79 exit-to-zero decisions on K's main path, the 9
   with a USD gain fraction > 5 % are *all* below SMA50 (a gain-rich book means an old, long-held
   position, and the exit that clears it happens after a decline), while the 10 exits that are
   above SMA50 all have a gain fraction ≤ 0.8 %. So X2 stages 10 times on the main path and is
   released by the early conditions 6 times, with the ordinary rule taking the position over 4
   times; the 21-session cap is expected never to bind. **This is a finding about the device, not
   a tuned choice**: any faithful reading of the sentence — the three-condition conjunction, the
   SMA50-only reading, or an entry-gated variant — leaves X2 either inert or a one-session fill
   split, because the device needs the early leg to be *plausible* exactly when the tax base is
   richest.

### 4.4 What is reported per arm because these arms delay de-risking

Per-arm pre-tax and after-tax max DD (all three after-tax windows), the **five named episodes**
(2000-03-09 → 2002-12-31, 2007-10-31 → 2009-03-09, 2015-12-01 → 2016-01-31, 2018-12-01 →
2018-12-31, 2021-12-01 → 2022-01-31): total return and max DD inside each window with the delta
against K, plus the whole-path max DD; the mechanism counters (`deferred_exit`, `staged_events`,
`staged_sessions`, `staged_release_cap` / `_gate` / `_target`); `sells/yr`, realised TRY gain and
negative realised gain by reason (`neg_gain_try_by_reason`, for the I4 risk that deferring an exit
destroys a same-year loss offset), tax paid and Yİ-ÜFE relief per arm.

## 5. Stages (all pre-registered before any non-control arm was simulated on real data)

Main window 2000-03-09 → 2026-09-29 (6,679 measured sessions, session 253 onwards), m = 2, cost
4 bps × m × |Δw|, base financing unless stated, progressive 2026 tariff deflated by the previous
October's Yİ-ÜFE where after tax, **liquidated** at the window end.

- **pre** — pre-tax main window (base/calibrated × m = 1/2/4) plus the R-USD row.
- **AT** — after tax at the $100k gate capital (and $10k/$30k/$300k/$1M report-only) on the three
  windows 2000-03-09 → 2026-09-29, 2010-01-01 → 2026-09-29, 2017-01-03 → 2026-09-29.
- **B12** — clean bootstrap, `seed = 20261011`, `block = 20`, 5,000 pre-tax / 1,000 after-tax
  paths, $100k (+$1M report-only), B1-recent (progressive) and B2 (flat 20 %), fixed
  2015-09-01 → 2026-09-30 calendar with its realised USD/TRY and Yİ-ÜFE, QQQ market returns
  block-resampled. The pre-tax leg is G5.
- **EP** — the drawdown/episode block of section 4.4 (report only).
- **OOS** — the six `ewma_band_v1` windows (^GSPC 1957–1999, M1–M5), pre-tax, calibrated, m = 2,
  R-USD rule (G7); **G8** = the pooled OOS bootstrap over M1–M5, block 20, seed 20261011, 2,000
  paths per market, arms = K + candidates.
- **E1/E2/E3** — the parent campaign's reporting stages: after tax on the 21 quality-passing ETFs
  under R-INDEX ($100k, calibrated, m = 2), 21 rolling January starts 2001…2021 on the QQQ main
  series ($100k, base), and fresh after-tax bootstraps (`seed = 20261015`, blocks 10/40, 1,000
  paths, $100k). `gain_band_v3`'s grid and `fast_reentry_v2`'s plateau neighbours are **not** part
  of this study: the candidates are not parameter neighbours of anything.
- **COST / E5** — report only: the m = 4 cost stress at $100k, the $10k–$1M after-tax windows, and
  the fill-decision traces of all arms.

## 6. Gates (pre-registered; every candidate arm vs the control K)

All must hold for adoption; no non-inferiority relaxation (these arms change exposure).

| Gate | Requirement |
|---|---|
| **G1** | pre-tax CAGR ≥ K's, or Calmar ≥ 1.1 × K's with CAGR ≥ K − 0.5 pp (m = 2, base and calibrated). |
| **G2** | pre-tax max DD ≤ K + 1.0 pp (m = 2, base and calibrated). |
| **G3** | 2017+ CAGR ≥ K − 1.0 pp (m = 2, base and calibrated). |
| **G4** | CAGR above K in ≥ 2 of the 3 sub-periods P1 2000–08 / P2 2009–16 / P3 2017→end (base, m = 2). |
| **G5** | block-20 pre-tax bootstrap (seed 20261011, 5,000 paths) median of arm − K ≥ 0 (base and calibrated) — `ewma_band_v1`'s G5 as written, no share clause. |
| **G6** | G1 at m = 1 (base and calibrated). |
| **G7** | six OOS windows: median Δ CAGR ≥ 0; ≥ 5 of 6 with Δ CAGR ≥ −0.5 pp **and** Δ max DD ≤ +3 pp; Δ max DD never above +8 pp. |
| **G8** | pooled OOS bootstrap (M1–M5, 2,000 paths each) median Δ ≥ 0. |
| **G9** | turnover ≤ 2 × K (the ratio is also reported). |
| **T1** | after tax at $100k: arm − K ≥ 0 in **all three** windows. |
| **T2** | B1-recent (progressive) at $100k: median Δ ≥ 0 **and** share of paths with Δ > 0 ≥ 55 %. |
| **T3** | B2 (flat 20 %) at $100k: median Δ ≥ 0 **and** share of paths with Δ > 0 ≥ 55 %. |

**Selection rule.** An arm is adopted only if G1–G9 and T1–T3 all pass; among survivors the study
adopts the highest after-tax $100k CAGR on the main window. If no arm passes, the axis is closed
with the per-arm table and the arm set is not re-run. No arm, parameter, stage, seed, threshold
or gate is changed after seeing a result. Expected-share caveat, stated in advance: X1 and X2
touch a handful of sessions per 26.5 years, so on bootstrap paths that contain none of those
sessions Δ is **exactly 0**, which counts against T2/T3's `share > 0` clause; a FAIL there is a
statement about how rarely the device acts, not only about its sign.

## 7. Differing conventions (disclosed)

1. **The pre-tax bootstrap's `diff_vs_C0_*` cells are measured against the reference label "C0"
   (production R2)**, the convention `gain_band_v3` published, so the deep reference block can
   compare them cell-by-cell; the candidates' G5 cells are the separate paired `{arm}_vs_K` cells.
2. **The postponement counter is split** into `blocked_band` (A1u), `deferred` (H2) and
   `deferred_exit` (X1); `postponements` is their sum, so G9's turnover ratio and the H2 anchors
   are unaffected.
3. **The engine is the parent's, unmodified except for the X1/X2 target adjustment**; C0/A1u/H2
   are byte-identical code paths to `band_h2_combo_v1` (asserted cell-by-cell in `--harness`).
4. **The X2 staged sale is an ordinary downsize** and is therefore subject to H2's window and
   A1u's band; the staged target is `0.5 × vt`, which can differ from the previously held weight,
   so the staged fill is a real (small) resize.

## 8. Run protocol and pre-registered expectations

`--smoke` (synthetic random-walk prices only: the probe identities, `C0 == simulate_band`,
`H2 == simulate_band_overlay`, `K with an empty window == A1u`, the tax modes, and the six new
X1/X2 identities — `X1` with no December window is K, `X2` with an unreachable threshold is K,
an engineered December exit is postponed by X1/X3 and not by K, an engineered gain-heavy exit
stages and is released by the cap and by the gate) → commit → `--harness` (the K anchors, the two
reference labels, the deep reproduction block) → `--run` (all stages, the gates, `RESULTS.json`
and `SUMMARY.txt`), executed **twice** with byte-identical `RESULTS.json`.

Pre-registered expectations, stated before the run:

1. The deep reference block reproduces `gain_band_v3`'s A1u cells and `tax_harvest_v2`'s H2 cells
   with **0 mismatches**; any mismatch is a bug in this study.
2. **X1 is small and its sign is not obvious.** It defers ~5 December exit events (24 fill
   sessions on the main path), i.e. it holds the whole position through the year end. The
   survey's H2 episodes (2015-12 → 2016-01 −0.70 pp, 2024-12 → 2025-01 −0.32 pp on the R2 path)
   are the risk model; the expected after-tax effect is order ±0.05 pp.
3. **X2 cannot help**: section 4.3's anti-correlation means it stages for one session and hands
   the position to a recovering early leg or exits it. Expected |Δ| ≤ 0.02 pp with Δ = 0 on most
   bootstrap paths.
4. **Drawdown**: both devices hold *more* in exactly the sessions the vote wants out, so the DD
   and episode columns are the interesting ones; G2 (≤ K + 1 pp) and the episode table are where a
   FAIL is most likely, T2/T3 (share clauses) second.
5. If both X1 and X2 fail, the correct reading is the survey's own: the axis is closed —
   exits are 1–3/yr, only ~4 % of their fill sessions can cross a year boundary, and the
   gain-richness that X2 wants is precisely what makes the early conditions false.

## 9. Caveats and limits

As `band_h2_combo_v1` §7 / `gain_band_v3` §7: one history per cell, no true after-tax
out-of-sample (the TRY calendar starts 2000), the fixed 2015-09 → 2026-09 bootstrap calendar
cannot price another FX/inflation regime, the liquidated convention charges deferred tax at the
window end, FIFO with per-year netting and no loss carry-forward, the 2026 tariff deflated by the
previous October's Yİ-ÜFE, instalments funded by selling. **Not tax advice.** The X2 reading is a
documented interpretation of an ambiguous sentence (section 4.3); the study's X2 row should be
read as "this device, as specified, cannot act", not as a test of every possible staging rule.

## 10. Amendments and honesty notes

*(no gate, arm, parameter, stage, seed or threshold was changed after any result existed)*

**A1. Pre-commit state (disclosed in full).** Before this pre-registration was committed, three
probes ran; none produced a performance statistic, a gate outcome or a stored result:

1. A **target-series probe** (main window, no engine): 79 sessions where the ordinary target
   transitions to 0 with a live position, 5 of them inside a `DEC_TAIL` window, 10 with
   `QQQ close > SMA50` at the decision close and **0** with the three early conditions satisfied.
   That is what established the section-4.3 reading (the conjunction reading is vacuous).
2. A **lot-book probe** on the K path (a `/tmp` copy of this file with an `X2PROBE` print added;
   the repository copy was never modified): per exit-to-zero decision, the held weight, the USD
   FIFO gain fraction, the SMA50 state and the three-condition state. Result: the 9 decisions with
   gain fraction > 5 % are all below SMA50, and the 10 decisions above SMA50 all have gain
   fraction ≤ 0.8 %. That is the anti-correlation section 4.3 records.
3. **Engine calls on the main path**: the K/C0/E2/A1u/H2 reference labels (the K anchors in
   section 3 were reproduced to ≤ 3.1e-7 before this commit, plus `H2 == simulate_band_overlay`
   at 1e-13), and **X1, X2 and X3 once each** (pre-tax, capital 1.0) observing only the
   mechanism counters and the NAV-path difference: X1 24 deferred exits, X2 10 staging events /
   10 staged sessions (6 gate releases, 4 ordinary-rule takeovers, 0 cap releases). No CAGR,
   no after-tax number, no bootstrap path, no gate and no stage was computed for any X arm before
   this commit.

**A2. Implementation fixes, all made before the first complete run and none touching an arm,
parameter, stage, seed, threshold or gate.** Every one was either a `NameError`/`KeyError` crash
with a non-zero exit and no `RESULTS.json`, or a smoke-time assertion:

1. The harness's first draft compared `K` against `simulate_band_overlay`, which is the H2-only
   reference (K also carries A1u's band); the identity is now asserted for the H2 arm, where it
   holds to 1e-13. Caught by the pre-commit probe, before any run.
2. The R-USD anchor was a single tuple; the calibrated financing model has its own published
   row. Both are now anchored. Caught by the same probe.
3. `X1_defers_december_exits_only` initially asserted that X1's December window leaves the band
   and H2 counters at zero; on the engineered uptrend they are non-zero for K as well, so the
   assertion now compares X1's counters *with K's* (band and December counters equal, the exit
   counter added).
4. The X2 release path could re-stage a fresh 21-session window on the release session itself
   (the exit is still pending and still gain-heavy), which would make the cap unbounded. The
   staged state now forbids a second staging within the same exit episode
   (`staged_done`), and a takeover by the ordinary rule is counted separately
   (`staged_release_target`). Caught in the first smoke run, before any arm touched real data.
5. `boot()`'s pre-tax delta block was hard-wired to the control label; it now emits paired
   `{arm}_vs_{baseline}` cells for both the control and the reference label, so the published
   `diff_vs_C0_*` cells stay comparable with `gain_band_v3` while G5 reads `{arm}_vs_K`.

**A3. Amended after the pre-registration commit — see `docs/research/exit_timing_v1.md`.** Any
issue found while running is recorded there and in this section, with the run it invalidated.
