# vt_level_k_v1 — what does LOWERING K's risk level cost and buy?

Pre-registered 2026-10-22, **before any non-control arm of this study was simulated on real data
on this branch**. Branch `research/vt_level_k_v1`; code in `research/vt_level_k_v1/`; engine
`research/fast_engine/` (Numba) plus a study-local target builder that changes only the vol-target
constant.

Control identity: production **K** = `equity_leveraged_trend_v1` 1.2.0 = E2 (2-of-3 QQQ vote) +
R2 early re-entry + **A1u** (gain-scaled downsize band 0.5 / 3 / 8 pp at a USD FIFO gain fraction
≤ 1 % / ≤ 5 % / > 5 %) + **H2** (a non-exit downward resize whose fill session is one of the last 10
December sessions is postponed), `vt = 0.35`.

## 0. Why this study exists

The owner asked after a ~12 % drawdown in October 2026 while QQQ fell ~6 %: **is production K too
risky, and what does a lower risk level cost?** `exposure_k_v1` measured the *upward* direction
under K (0.375 / 0.40 / 0.45: more after-tax return, more drawdown, negative clean-bootstrap
medians) and closed that axis. The *downward* direction has never been run under K. Under the
E2-era control, `leverage_frontier_v1` measured 2x at `vt = 0.30` as 12.52 % CAGR / 44.2 % DD vs
0.35's 13.37 % / 49.6 % — a hint, not evidence, because K's tax layer changes the trade count.

This is a **measurement study of a risk-preference knob, not an alpha hunt**. The deliverable is
the price list: how much after-tax CAGR each step down costs, what drawdown it removes, and how
the answer splits between pre-tax and after-tax paths.

## 1. Question

Lowering production K's vol target from 0.35 to 0.30 or 0.25: what does it cost in after-tax CAGR,
what does it buy in drawdown (depth, frequency, recovery, tails), and does the after-tax evidence
agree with the pre-tax evidence?

## 2. Data and provenance

All study inputs are committed in `data/historical/`; `DATA_MANIFEST.json` records source URL,
date range, byte count and SHA-256. The main study uses the same 29-file input set as
`band_h2_combo_v1` (QQQ/SPY/QLD bars + DTB3, CBRT USD/TRY, Yİ-ÜFE, the six OOS windows). The
committed bar file ends **2026-09-29**, so the October 2026 episode is *outside* the gated window.

**One downloaded input (report-only, outside every gate):**
`data/historical/vt_level_k_v1_{qqq,qld}_2026q4.csv`, Yahoo chart API daily bars 2026-09-10 →
2026-10-22, fetched 2026-10-22. Overlap check: QQQ 2026-09-18…2026-09-29 reproduces the committed
bars exactly (max open difference 0.005, closes identical). Used **only** for stage EPI (§6) and
never for a gate. `QLD` is context (buy-and-hold), not an input to the strategy.

## 3. Control (K)

Production K through `research/fast_engine`: the R2 target series (E2 2-of-3 vote on the QQQ
close + the 0.5·vt early re-entry leg, `vt = min(1, 0.35/vol20)`, cap 0.98), production execution
(decide on the completed close, fill at the next open, symmetric 0.5 pp resize band, entries and
exits to zero always fill, idle cash zero, cost 4 bps × m × |Δw|, synthetic QLD* = daily-reset 2×
on QQQ total return, 0.95 %/yr expense, financing DTB3 + {base 0.0 %, calibrated 0.75 %}/yr), plus
A1u and H2 composed H2-first exactly as shipped.

**Anchors asserted in `--harness`** (tolerance 0.01 pp): pre-tax CAGR **15.7649 %** base /
**15.1785 %** calibrated, max DD **46.6698 % / 46.7948 %**, m = 1 **16.0173 %**, turnover
**5.4561**; after tax at $100k (progressive, liquidated) **10.7533 / 15.2354 / 23.9720 %** on the
2000 / 2010 / 2017 windows; R2 reference row **15.4766 / 14.8961 %**, DD **46.7584 / 46.8801 %**,
after tax **10.160 / 14.706 / 22.886 %**; pure E2 anchor **14.9491 / 14.6749 %**, DD **49.2471 /
49.5643 %**. The clean-bootstrap medians are asserted in `--run`-preparation: K **3.8577 %**
(B1-recent) / **4.0376 %** (B2-flat), R2 **3.5155 / 3.9678 %**, pre-tax K **7.7761 / 7.3137 %**.

**Deep reference block (required before results are read).** Every numeric leaf of every **K**
cell that this run produces in `pre_tax_main` (base/calibrated × m = 1/2/4), `after_tax_windows`
at all five capitals, the OOS table and the clean bootstrap is compared against
`research/band_h2_combo_v1/RESULTS.json` (tolerance 2e-6, the file's stored precision), with that
campaign's control `C0` mapped to this study's `R2`. Any mismatch is a bug here, not a result.

**Engine parity.** `--harness` runs `python -m research.fast_engine.parity --quick` as a
subprocess and requires PASS (0 mismatches). Study-local target building is asserted **bitwise**
equal to `fe_signals.sized_wants` at `vt = 0.35`, and a 400-path local bootstrap is asserted
bitwise equal to `fe_boot.boot` on the same seed/block (so the lowered-vt cells differ only by the
`vt` constant, not by plumbing).

## 4. Arms (3 labels + 1 reference row; every parameter fixed)

`vt` is the **only** changed parameter. Signal, early re-entry, A1u, H2, resize band, cap, cost,
financing and windows are production K's.

| Arm | Rule |
|---|---|
| **K** | production K, `vt = 0.35` (control, asserted against `band_h2_combo_v1`). |
| **V30** | K with `vt = 0.30`. |
| **V25** | K with `vt = 0.25`. |
| **R2** | production R2 (band off, no deferral, `vt = 0.35`) — **reference row only**, never a candidate. |

Targets: `w = min(0.98, on_eff × factor × min(1, vt/vol20))`, where `on_eff`/`factor` are E2's
vote and the 0.5 early-re-entry factor, and vol20 is the 20-session annualised vol of the synthetic
QLD* leg. No arm is added, dropped or re-parameterised after seeing data.

## 5. Gates and the selection rule (pre-registered)

Every gate is versus **K**; "both" = base and calibrated financing, m = 2. G1–G9 are
`ewma_band_v1`'s definitions with C0 → K.

| Gate | Requirement |
|---|---|
| **G1** | CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp (both) |
| **G2** | max DD ≤ K + 1.0 pp (both) |
| **G3** | 2017+ CAGR ≥ K − 1.0 pp (both) |
| **G4** | CAGR above K in ≥ 2 of 3 sub-periods (P1 2000–08, P2 2009–16, P3 2017→) |
| **G5** | block-20 pre-tax bootstrap (seed 20261011, 5,000 ten-year paths): median Δ ≥ 0 (base and calibrated) |
| **G6** | G1 at m = 1 (both) |
| **G7** | six OOS windows (^GSPC 1957–1999, M1–M5; calibrated, m = 2, R-USD): median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp **and** Δ DD ≤ +3 pp in ≥ 5 of 6; Δ DD never > +8 pp |
| **G8** | pooled OOS bootstrap over M1–M5 (2,000 paths each): median Δ CAGR ≥ 0 |
| **G9** | turnover ≤ 2 × K |
| **AT** | after tax at $100k, liquidated: arm − K ≥ 0 in **all three** windows |
| **BOOT** | clean bootstrap at $100k, B1-recent (progressive) and B2 (flat 20 %): median Δ ≥ 0 and share > 0 ≥ 55 % |

**Selection rule (pre-registered):** an arm qualifies only if it passes **every** gate above. If
none qualifies, no arm is selected and the study's output is the measured trade-off table. Because
this is a risk-preference knob, a qualifying arm still requires the owner's preference decision
(reduced drawdown versus return given up); the study does not infer a utility function. The
protocol's after-tax gate is the binding direction here and is reported honestly.

## 6. Stages (all pre-registered before any non-control arm was simulated on real data)

Main window 2000-03-09 → 2026-09-29, m = 2 unless stated, base financing unless stated.

- **pre** — pre-tax main window (base/calibrated × m = 1/2/4) plus the R-USD row.
- **AT** — after tax at $100k (gate capital) and $10k/$30k/$300k/$1M (report) on the three windows
  2000-03-09→2026-09-29, 2010-01-01→2026-09-29, 2017-01-03→2026-09-29; progressive 2026 tariff,
  liquidated at the window end.
- **B12** — clean bootstrap, seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths, $100k,
  B1-recent (progressive) and B2 (flat 20 %), fixed 2015-09-01 → 2026-09-30 calendar with realised
  USD/TRY and Yİ-ÜFE, QQQ market returns block-resampled. Serves G5 and BOOT.
- **E4** — the pre-tax legs of B12 (base and calibrated) plus the main-window pre-tax max DD.
- **OOS** — the six `ewma_band_v1` windows (^GSPC 1957–1999, M1–M5), pre-tax, calibrated, m = 2,
  R-USD rule; plus the pooled block-20 bootstrap of G8 (2,000 paths per market, seed 20261011).
- **E2** — 21 rolling January starts 2001…2021, after tax at $100k, base, to 2026-09-29 (report).
- **E5** — report only: the $10k–$1M windows, the m = 4 cost stress, decision counters.
- **PROF** — drawdown profile of every arm on (i) the pre-tax base m = 2 main-window NAV and
  (ii) the after-tax progressive $100k main-window NAV (see §7).
- **EPI** — the October 2026 episode, report only, on the extended QQQ series (§2).

## 7. Drawdown-profile definitions (fixed before the run)

- **Episode.** A maximal underwater interval: starts at the session after an all-time-high NAV,
  ends at the first session whose NAV is ≥ that peak. `dd = 1 − min(NAV)/peak`. Recovery time =
  end index − peak index (sessions). Episodes not recovered by the window end are **censored**:
  counted, excluded from the recovery median/max, and reported separately.
- **Threshold counts.** Number of episodes with `dd` above 10 / 15 / 20 / 30 / 40 %.
- **Recovery.** Median and maximum days to recover over recovered episodes, per threshold.
- **Tail losses.** Worst 21-session and 63-session loss = `min_t NAV[t]/NAV[t−h] − 1`, h = 21 / 63.
- **Vol.** Annualised standard deviation of daily simple NAV returns, `ddof = 1`, × √252.
- **Cost per point.** `(K after-tax CAGR − arm after-tax CAGR) / (K after-tax max DD − arm after-tax
  max DD)` in pp per pp, at $100k on the 2000 window; the same ratio on the pre-tax base m = 2
  path is reported alongside. A non-positive denominator is reported as `n/a`.
- **EPI.** Return from the 2026-10-05 close to the last available close (2026-10-22), and the max
  drawdown inside 2026-10-06…2026-10-22 measured against the running peak established on or before
  2026-10-05. Reported for all three arms plus QQQ and real QLD buy-and-hold for context; pre-tax
  only (the committed Yİ-ÜFE series ends 2026-09 and USD/TRY ends 2026-10-09).

## 8. Run protocol and pre-registered expectations

`--smoke` (synthetic prices only) → this commit → `--harness` (engine parity, K anchors, deep K
reproduction, `vt = 0.35` bitwise identity) → `--run` twice with byte-identical `RESULTS.json`.

Expectations, stated before the run:

1. **Returns fall monotonically with `vt`** on the realised pre-tax and after-tax windows; the
   question is the slope, not the sign.
2. **Drawdown falls** — max DD, episode counts and tail losses — but by less than proportionally,
   because the trend vote, not the vol target, sets the big risk-off episodes.
3. **After-tax may fall less than pre-tax** (smaller positions realise smaller gains), and the
   clean-bootstrap deltas are expected to be negative on return; the BOOT gate is therefore
   expected to bind at both arms.
4. **G1 can only pass through the Calmar route** (`Calmar ≥ 1.1 × K` with CAGR ≥ K − 0.5 pp).
5. **The honest headline may be "the drawdown knob is expensive at the first step"** or "V30 is a
   defensible preference, V25 is not". Either is a valid measurement; nothing is re-tuned.

## 9. Caveats and limits

One history per cell; the after-tax windows are single paths and the clean bootstrap is the
counterweight; no true after-tax out-of-sample (the TRY/Yİ-ÜFE calendar starts 2000); the fixed
2015-09→2026-09 bootstrap calendar cannot price another FX/inflation regime; FIFO, per-year
netting without loss carry-forward, the 2026 tariff deflated by the previous October's Yİ-ÜFE,
instalments funded by selling, one CBRT buying-rate series; the liquidated convention charges
deferred tax at the window end. The October 2026 episode is outside the gated window, uses a
downloaded extension of the QQQ series, and cannot be taxed in this model. **Not tax advice.**
Nothing here touches `src/`, `config/`, `tests/`, `scripts/`, `benchmarks/`, `data/runtime/`,
`logs/` or the deployed PAPER bot.

## 10. Amendments

**A1 (2026-10-22, before any non-control arm ran on real data).** §7's EPI definition is clarified,
not changed: in addition to the drawdown measured from the peak on or before 2026-10-05, the report
also states the **peak-to-trough inside the episode** (running peak from 2026-10-06 onward). The
original measure understates a decline that makes a new high early in the episode; both are
reported so the reader can see which peak the owner's "~6 % QQQ" refers to. No gate uses EPI.

