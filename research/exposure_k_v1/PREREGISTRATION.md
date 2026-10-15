# exposure_k_v1 — the after-tax exposure frontier under K (idea I1 of `idea_survey_v4`)

Pre-registered 2026-10-15, **before any non-control arm of this study was simulated on real data
on this branch**. Working branch `research/exposure_k_v1`; the code lives in
`research/exposure_k_v1/` (`screen.py`, `tax_engine.py`, a byte copy of
`research/band_h2_combo_v1/tax_engine.py`). Control identity: production **K** =
`equity_leveraged_trend_v1` 1.2.0 = E2 (2-of-3 QQQ vote) + R2 early re-entry + **A1u**
(gain-scaled downsize band 0.5 / 3 / 8 pp at a USD FIFO gain fraction ≤ 1 % / ≤ 5 % / > 5 %) +
**H2** (a non-exit downward resize whose fill session is one of the last 10 December sessions is
postponed).

## 0. Why this study exists

`docs/research/idea_survey_v4.md` §3 establishes that K's taxed base is the strategy's own sells
(88 % of realised gain; the terminal liquidation is 11 %), that indexation relief is nearly
saturated, and that the effective tax rate on realised gain is unchanged from R2 — but that the
*marginal tax cost of one more unit of exposure* has fallen, because the resizes a higher vol
target needs are exactly the gain-heavy ones A1u already defers. K already holds **more** exposure
than R2 at a **lower** max DD (46.670 % vs 46.758 %), so the drawdown budget every arm must respect
has not been spent. I1 is the survey's rank-1 idea: **trace the after-tax exposure frontier under
K** — what is the price, in drawdown, of the after-tax-optimal vol-target level once K's tax logic
is in place?

The two nearest studies are both pre-K: `leverage_frontier_v1` (no tax model, plain SMA200,
synthetic Lx cells) and `vt_level_tax_v1` (control = production **E2**, no early path, no band; its
0.40 arm won on the realised after-tax path by +0.61 / +0.96 / +1.72 pp but lost on the clean
bootstrap: −0.088 / −0.153 pp median). Under K neither the DD budget nor the tax arithmetic of a
higher target has been re-measured.

## 1. Question

Does raising the vol-target level of production K (0.35) improve the **after-tax** CAGR for a
Turkish-resident owner without spending more than 1.0 pp of pre-tax max drawdown, and if it does
not, where is the frontier and what does it cost?

## 2. Data and provenance

**Nothing is downloaded.** Every input is an existing committed research file in this worktree
(`data/historical/`, 29 CSVs), restored unchanged from branch `research/band_h2_combo_v1`; all 31
entries of that campaign's `DATA_MANIFEST.json` were re-hashed here with **0 mismatches** before
this commit. `DATA_MANIFEST.json` records source, byte count and SHA-256 of each file, plus the one
**evidence** file used for reproduction (`research/band_h2_combo_v1/RESULTS.json`, whose K cells are
compared cell-by-cell). Uses: `stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY/QLD bars, DTB3),
`after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (TRY conversion, Yİ-ÜFE indexation),
`ewma_band_v1_{gspc,index,tb3ms}.csv` (the six OOS windows), `range_vol_v2_*.csv` (19) +
`range_vol_v1_{spy,dia,iwm}.csv` (the 21 quality-passing ETFs; `EWC` excluded by `range_vol_v2`'s
fixed quality rule). The TRY/Yİ-ÜFE calendar starts 2000-01 / 1999-01, so no after-tax stage starts
before 2000-03-09.

## 3. Control (K)

**Production K, bit for bit**, through the single sequential engine (`tax_engine.py`, a byte copy
of `band_h2_combo_v1/tax_engine.py`): the R2 target series (E2 2-of-3 vote on the QQQ close + the
0.5·vt early re-entry leg, `vt = min(1, 0.35/vol20)`, cap 0.98) with the production execution layer
(decide on the completed close, fill at the next open, symmetric 0.5 pp band, entries and exits to
zero always fill, idle cash zero, cost 4 bps × m × |Δw|, synthetic QLD* = daily-reset 2× on QQQ
total return, 0.95 %/yr expense, financing DTB3 + {base 0.0 %, calibrated 0.75 %}/yr) plus the
A1u band and the H2 December window, composed H2-first exactly as `band_h2_combo_v1` shipped it.

**Anchors asserted in `--harness`** (pre-tax TOL 1e-4, after-tax 0.005 pp):
pre-tax CAGR **16.0173 / 15.7649 %** (m = 1 / m = 2, base) and **15.4303 / 15.1785 %** (calibrated),
max DD **46.3529 / 46.6698 %** and **46.4786 / 46.7948 %**, turnover **5.4568 / 5.4561**;
after tax at $100k (liquidated) **10.7533 / 15.2354 / 23.9720 %** with max DD
**46.6698 / 44.6272 / 34.9774 %**; the R2 reference row (band off, no deferral) against the R2
anchors of `fast_reentry_v1` / `vt_level_tax_v1` (15.7538 / 15.4766 %, DD 46.4143 / 46.7584 %,
after tax 10.160 / 14.706 / 22.886 %); the pure-E2 anchor series against `e2_integration_v1`
(14.9491 / 14.6749 %, DD 49.2471 / 49.5643 %); the R2 arm's NAV path == the reference
`simulate_band` (band off, no deferral) to 1e-9; K's NAV path differs from both R2's and the pure-E2
series'; K's band and deferral counters fire (published K counters: 1,239 band blocks and 71
December deferrals over the main window). In `--run` mode the clean-bootstrap medians are also
asserted: K 3.8577 % (B1-recent) / 4.0376 % (B2-flat), R2 3.5155 / 3.9678 %, pure-E2 3.079 / 3.520 %,
and K's pre-tax leg medians 7.7761 / 7.3137 %.

**Deep reference block (required for `--run`).** Every numeric leaf of every **K** cell that
`band_h2_combo_v1` published is compared against `research/band_h2_combo_v1/RESULTS.json`: the
pre-tax main window (m = 1/2/4 × base/calibrated), the R-USD row, the after-tax windows at all five
capitals, the m = 4 cost stress, the seed-20261011 clean bootstrap (pre-tax and after-tax,
$100k/$1M), the six OOS windows, the 21 R-USD instruments, E1 (after-tax 21 ETFs, R-INDEX), E2
(rolling starts) and E3 (fresh bootstraps) — with R2's cells (that campaign's control "C0") mapped
label-for-label. Any mismatch is a bug in this study, not a result.

## 4. Arms (4 labels; every parameter fixed, no tuning)

`vt` is the **only** changed parameter. Everything else — signal, early re-entry, band, H2 window,
cost, financing, cap — is production K's.

| Arm | Rule |
|---|---|
| **K** | production K, `vt = 0.35` (control, asserted against `band_h2_combo_v1`). |
| **R2** | production R2: K's execution layer with the band off and no deferral, `vt = 0.35`. **Reference row only** — never a candidate, never selected. |
| **F1** | `vt = 0.375` — a 1/14 increase (the survey's smallest step). |
| **F2** | `vt = 0.40` — reproduces `vt_level_tax_v1`'s cell on the 0.30/0.35/0.40/0.45/0.50 grid under the new control. |
| **F3** | `vt = 0.45` — the top of the measured grid; expected to fail the drawdown budget and reported as the frontier's endpoint. |

Arms are run through the **same** engine call with a different target series; the target series are
built from one `r2_state`/`vol20` pass, so K's and R2's are bit-identical and the candidates' differ
only in the VT constant. No arm is added, dropped or re-parameterised after seeing data.

## 5. Gates and the selection rule (pre-registered)

Every gate is versus **K** (the control). "Both" means base and calibrated financing, m = 2.

| Gate | Requirement |
|---|---|
| **G1** | CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp |
| **G2** | max DD ≤ K + 1.0 pp |
| **G3** | 2017+ CAGR ≥ K − 1.0 pp |
| **G4** | CAGR above K in ≥ 2 of the 3 sub-periods (P1 2000–08, P2 2009–16, P3 2017→) |
| **G5** | block-20 pre-tax bootstrap (seed 20261011, 5,000 ten-year paths, draws identical to `signal_ensemble_v1`): median (arm − K) ≥ 0 |
| **G6** | G1 at m = 1 |
| **G7** | six OOS windows (^GSPC 1957–1999, M1–M5; calibrated, m = 2, R-USD rule): median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp **and** Δ DD ≤ +3 pp in ≥ 5 of 6; Δ DD never above +8 pp |
| **G8** | pooled OOS bootstrap over M1–M5 (2,000 paths each): median Δ CAGR ≥ 0 |
| **G9** | turnover ≤ 2 × K |
| **TAIL** | `leverage_frontier_v1`'s tail gate: P(DD > 70 %) in the block-20 **ten-year** bootstrap ≤ K's + 5 pp, computed pre-tax (base and calibrated) **and** after tax at $100k and $1M (progressive); plus its second leg, median max DD ≤ 65 % |
| **AT** | after tax at $100k (progressive 2026 tariff, liquidated): arm − K ≥ 0 in **all three** windows |
| **BOOT** | clean bootstrap at $100k, B1-recent and B2-flat: median Δ ≥ 0 and share > 0 ≥ 55 % (the common protocol's floor; the survey states 50 %) |

**Selection rule — pre-registered by the survey (I1) and DECIDING here:** *the highest after-tax
CAGR at $100k on the 2000-03 → 2026-09 window among F1–F3 whose pre-tax max DD ≤ K + 1.0 pp (base
and calibrated) and whose G1–G9 all pass; if none, close the exposure axis with the frontier table
and do not re-run it.*

**Secondary (common protocol) rule, reported:** the highest after-tax CAGR at $100k on the same
window among F1–F3 that pass G1–G9 **and** beat K after tax at $100k in all three windows **and**
have B1-recent/B2-flat median Δ ≥ 0 with share > 0 ≥ 55 %.

Where the two rules disagree, **the survey rule decides** (it is the idea's own pre-registration,
and this study exists to answer I1). Both verdicts, and the full per-arm gate matrix, are reported;
the protocol rule's extra conditions are reported as gates, not as a re-selection.

The survey's pre-registered reporting block — the **after-tax CAGR-versus-max-DD frontier** (all
arms, all three windows at $100k, pre-tax max DD base/calibrated) and the tail gate at $100k and
$1M — is emitted in `RESULTS.json` (`gates.frontier`, `gates.arms.*.TAIL_detail`) and in
`SUMMARY.txt`.

## 6. Stages (all pre-registered before any non-control arm was simulated on real data)

Main window 2000-03-09 → 2026-09-29 (6,679 measured sessions, session 253 onwards), m = 2, cost
4 bps × m × |Δw|, base financing unless stated, progressive 2026 tariff deflated by the previous
October's Yİ-ÜFE where after tax, **liquidated** at the window end.

- **pre** — pre-tax main window (base/calibrated × m = 1/2/4) plus the R-USD row.
- **AT** — after tax at the $100k gate capital and $10k/$30k/$300k/$1M on the three windows
  2000-03-09→2026-09-29, 2010-01-01→2026-09-29, 2017-01-03→2026-09-29.
- **B12** — clean bootstrap, `seed = 20261011`, `block = 20`, 1,000 after-tax / 5,000 pre-tax paths,
  $100k (+$1M report-only), B1-recent (progressive) and B2 (flat 20 %), fixed
  2015-09-01 → 2026-09-30 calendar with its realised USD/TRY and Yİ-ÜFE, QQQ market returns
  block-resampled. The pre-tax leg is also the tail gate's and G5's path set.
- **E1** — after tax on the 21 quality-passing ETFs under R-INDEX (real USD/TRY + Yİ-ÜFE), own E2 +
  own R2 early path, own synthetic QLD* leg (calibrated, m = 2), $100k, liquidated at the ETF's own
  end; start = max(warm-up, 2000-03-09).
- **E2** — 21 rolling January starts 2001…2021 on the QQQ main series, after tax at $100k, base,
  each run to 2026-09-29.
- **E3** — fresh after-tax bootstraps, `seed = 20261015`, blocks 10 and 40, 1,000 paths, $100k,
  progressive and flat (reproduction + robustness; report-only, not a gate).
- **E4** — the pre-tax leg of B12 (base and calibrated) plus the main-window pre-tax max DD.
- **E5** — report only: the $10k–$1M after-tax windows, the m = 4 cost stress, and the
  fill-decision traces of all five arms on the main path.
- **OOS** — the six `ewma_band_v1` windows (^GSPC 1957–1999, M1–M5), pre-tax, calibrated, m = 2,
  R-USD rule; plus the pooled block-20 bootstrap (G8).

E1–E5 are the parent campaign's stages; here they are reporting cells for every arm against K
(E1 additionally gates, as the common protocol requires).

## 7. Run protocol and pre-registered expectations

`--smoke` (synthetic prices only) → this commit → `--harness` (control anchors + the deep K
reproduction block) → `--run` (stages, gates, selection, `RESULTS.json` + `SUMMARY.txt`), executed
**twice** with byte-identical `RESULTS.json`.

Pre-registered expectations, stated before the run:

1. The control reproduces K's published cells **exactly** (0 mismatches); any mismatch is a bug
   here.
2. **The drawdown budget, not the tax code, decides.** K's pre-tax max DD is 46.670 % and the budget
   is K + 1.0 pp; `vt_level_tax_v1` measured 0.40 at +4.9 pp of DD under E2, so F1 (0.375) is the
   only arm plausibly inside the budget, and F2/F3 are expected to fail **G2** and the tail gate.
   G1 is expected to pass at all three (a higher target bought +0.50 pp CAGR at 0.40 under E2).
3. **G5 is the second-likely binding gate.** Higher targets trade *less often* but in *larger*
   slices, and the extra return is deferred exposure; `vt_level_tax_v1`'s clean bootstrap medians
   were negative at every higher level (−0.088 pp at 0.40), and the same channel is expected here.
4. **The realised after-tax windows should favour F1/F2** (fewer, larger realisations, more
   deferred exposure), as they did under E2 (+0.61 / +0.96 / +1.72 pp at 0.40); the study's job is
   to say whether that survives the DD budget and the bootstrap.
5. **The honest headline may well be "the after-tax optimum is beyond a drawdown the owner
   accepts"** — the survey states this as the likely outcome. If so the exposure axis closes with
   the frontier table and is not re-run.

## 8. Caveats and limits

As `band_h2_combo_v1` / `gain_band_v3` / `vt_level_tax_v1`: one history per cell; no true after-tax
out-of-sample (the TRY/Yİ-ÜFE calendar starts 2000); the fixed 2015-09 → 2026-09 bootstrap calendar
cannot price another FX/inflation regime; the liquidated convention charges deferred tax at the
window end (so an arm that defers more is charged at the window end, not never); FIFO with per-year
netting and no loss carry-forward; the 2026 tariff deflated by the previous October's Yİ-ÜFE;
instalments funded by selling; one CBRT buying-rate series; `A1u`'s `g_frac` is USD-only by
construction (the implementable rule). The pre-tax frontier's **shape** is partly in-sample from
`leverage_frontier_v1` / `vt_level_tax_v1`; only the tax interaction and the DD budget are new.
**Not tax advice.** Nothing here touches `src/`, `config/`, `tests/`, `scripts/`, `benchmarks/`,
`data/runtime/`, `logs/` or the deployed PAPER bot.

## 9. Amendments and honesty notes

*(added after the fact; none touches an arm, parameter, stage, seed, threshold or gate)*

**A1. Pre-commit state, disclosed.** Before the pre-registration commit, on this branch:

1. The parent harness (`research/band_h2_combo_v1/screen.py`, imported read-only) was executed three
   times in a **timing probe** on the main window for the *control* labels only (C0/A1u/H2/K of that
   campaign, i.e. R2, A1u, H2 and production K) to size the compute; no candidate arm of *this*
   study existed.
2. `--smoke` (synthetic random-walk prices only, no market data) was run.
3. `--harness` was run: it executes only the control label K, the reference row R2 and the pure-E2
   anchor series on real data, plus a **structural** check that the three candidate target series
   differ from K's (sessions differing, max |Δweight|, mean weight). That check reports no
   performance number for any candidate arm — no CAGR, no drawdown, no tax, no bootstrap cell
   existed for F1–F3 at the time of the commit.

**A2. Reference-row convention.** `band_h2_combo_v1`'s bootstrap and reference files label the R2
row "C0"; here it is "R2" and the control is "K". The comparison maps the labels explicitly
(`arm_map`) instead of renaming cells on either side.

**A3. Tail gate on two path sets.** `leverage_frontier_v1` defined P(DD > 70 %) on 10-year (2,520
measured session) block-20 bootstrap paths with no tax. The pre-tax leg of this study's clean
bootstrap is that path set, and is reported as `pre_base` / `pre_calibrated`; the after-tax legs
($100k/$1M) are the same 1,000 paths' liquidated NAV paths, reported because the task asks for the
gate at both capitals. The 65 % level leg uses the median of those path maxima. All six cells are
reported; the gate is evaluated on each.

**A4. `E3` is report-only here.** It exists because the deep reference block must reproduce
`band_h2_combo_v1`'s E3 cells; it is not part of this study's gate set.

**A5. Code fixes made after the pre-registration commit (`c333da35`), before the recorded
`--run`.** All four are plumbing bugs of this study's own harness adaptation; **none touches an
arm, a parameter, a stage, a seed, a threshold, a gate or the selection rule**, and each was found
by running the harness, not by reading a candidate's performance:

1. `targets_of` returned only the first path of the sized target series (`[P, T]` → `[0]`), which
   broke the pooled OOS bootstrap (G8), whose paths are re-sized in chunks of 500. It now returns
   the full `[P, T]` arrays; the frozen smoke test asserts the per-path identities this changed.
2. `run()`/`stage_e3()` built the bootstrap's delta cells with `deltas=CANDIDATES + (CONTROL,)`,
   so the R2 reference row had no `R2_vs_K` cell and the SUMMARY writer raised `KeyError`. The
   delta list now also carries the reference row (`deltas=CANDIDATES + (CONTROL, BASELINE)`), which
   *adds* two reporting cells and changes no existing one.
3. `gates_exposure`'s frontier table was built out of the candidate-only gate dict, so it raised
   `KeyError: 'R2'`. It is now built from the stage results for **all five arms**, which is what the
   survey's "after-tax CAGR-versus-max-DD frontier" reporting asks for (K and R2 appear as the
   frontier's origin and reference point, with no candidate gates).
4. `harness()` passed a `[1, T]` target array where the reference `simulate_band` needs 1-D
   (`IndexError`), fixed with `.ravel()`.

**A6. Post-commit probe on real data (disclosed).** After the fixes in A5 and **before** the two
recorded `--run`s, `run()` was exercised end-to-end once with deliberately tiny path counts (40
pre-tax / 12 after-tax / 12 E3 / 12 OOS-bootstrap paths instead of 5,000 / 1,000 / 1,000 / 2,000)
with `OUT`/`SUMMARY` redirected to `/tmp`, so it could not touch `research/exposure_k_v1/`. That
probe therefore **did** display the candidates' *deterministic* cells before the recorded run: the
pre-tax CAGR/max-DD, the three after-tax windows, E1/E2/E3's deterministic parts and the tail-gate
shares (which are path-count dependent and were meaningless at 12 paths). **No arm, parameter,
stage, seed, threshold, gate or selection rule was changed after that probe**; the failures it
foreshadowed (G2 at every arm, G5, the tail gate from 0.40) are the failures the recorded run
reports. The pre-registration (arms with every parameter fixed, stages, gates, selection rule) was
committed in `c333da35` **before any candidate arm was simulated on real data at all**, and the
`--harness` run that preceded the commit only executed the control, the reference row and the
pure-E2 anchor series plus the structural target-series check (A1).
