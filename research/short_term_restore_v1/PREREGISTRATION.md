# short_term_restore_v1 — a short-term cut on K that does not chase the rebound

Status: **candidate study with gates.** This file is committed before any non-control arm is
simulated on real data. Nothing here is proposed for adoption on its own; a passing arm would be a
candidate for an owner decision.

Control: production **K** exactly (`equity_leveraged_trend_v1` 1.2.0 = R2 + A1u gain band + H2
December deferral), reproduced through `research/fast_engine`. R2 is a reference row. Anchors that
`--harness` must reproduce within 0.01 pp are those of `short_term_reentry_v1` (pre-tax CAGR
15.7649 % base / 15.1785 % calibrated, max DD 46.6698 % / 46.7948 %, m = 1 16.0173 %; after tax
at $100k 10.7533 / 15.2354 / 23.9720 %; K − R2 = +0.5933 / +0.5294 / +1.0859 pp).

## 1. Question

`short_term_oracle_v1` put the prize for a short-term pullback detector at +4…+11 pp/yr after tax
and located all of it in the restore. `short_term_reentry_v1` showed why the causal version fails:
a restore that waits for a 2–4 % rebound off the post-exit low re-enters **above the exit fill on
78–85 % of round trips** (+2.6…+4.7 % on average), so every false alarm costs a round trip. Its §9
names the only axis left: a restore that is not above the exit on average, or a cut that is
cheaper to be wrong about. This study tests the three candidates the owner asked for:

1. **Non-chasing (bracket) restore.** Cut to zero, then restore when the close is back to the cut
   close (a false alarm costs ≈ one gap, not a +3 % chase) **or** when the close is a further
   5 % / 10 % below the cut close (buy the deeper dip lower).
2. **Partial cut.** Cut to **half** of K's weight instead of zero (≈ 1x Nasdaq-100 exposure on the
   2x leg), so a wrong cut forgoes half of the rebound and realises half of the gain.
3. **Deep staged cut.** Do nothing on ordinary wiggles; cut to half only at −8 % and to zero at
   −12 % from the trailing-20 high (the oracle's prize is concentrated in the ≥ 8–10 % class).

## 2. Data and windows

Identical to `short_term_reentry_v1` (its `DATA_MANIFEST.json` is copied unchanged; SHA-256 there):
QQQ/SPY/QLD bars and DTB3 (`stress_robustness_v1_*`), synthetic daily-reset 2x QLD* (financing
spread 0 base / 0.75 pp calibrated, expense 0.95 %), CBRT USD/TRY and Yİ-ÜFE; main window
2000-03-09 → 2026-09-29; after-tax windows 2000-03 / 2010-01 / 2017-01 → 2026-09 at $100k,
progressive 2026 tariff, liquidated, base financing, m = 2; cost 4 bp per side × m; idle cash
earns zero; clean bootstrap seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths,
B1-recent and B2-flat; OOS = `ewma_band_v1`'s six windows + the pooled M1–M5 bootstrap (2,000
paths each). October 2026 episode: the reentry study's report-only Yahoo extension. No new data.

## 3. The overlay rule (fixed here, before any arm ran)

Decisions on the **QQQ price close**, fills at the next open. K's own weight `w[t]` runs underneath
unchanged; the overlay only scales it. State ∈ {full, partial, zero}; mask codes 0 / 2 / 1;
target = `w`, `f1 × w`, `0`.

1. **Reference high** `H[t]` = max close over `t−19 … t`, truncated at the current in-stint start
   (exactly `short_term_reentry_v1` §3.1). A new stint starts at a restore and after a session with
   `w[t] ≤ 0`; the first measured session uses the full trailing-20 window.
2. **K is out** (`w[t] ≤ 0`): state = full, new stint, target 0; an open episode ends without a buy.
3. **First cut.** While full: if `close[t] ≤ (1 − d1) × H[t]`, state = partial (`f1 > 0`) or zero
   (`f1 = 0`); record `X = close[t]` (the cut close), `H_cut = H[t]`, `L = close[t]`. If `f1 > 0`
   and `close[t] ≤ (1 − d2) × H_cut` on the same session, state = zero at once.
4. **While cut** (`w[t] > 0`), from the session after the cut: update `L = min(L, close[t])`; then
   * **rebound restore** (kind 0): restore if `close[t] ≥ (1 + r) × L`;
   * **bracket restore** (kind 1): restore if `close[t] ≥ X` (upper leg), else if
     `close[t] ≤ (1 − e) × X` (lower leg; disabled when `e ≥ 1`).
   A restore sets state = full, new stint at `t`, target `w[t]`. Otherwise, if partial and
   `close[t] ≤ (1 − d2) × H_cut`, state = zero (second cut; disabled when `d2 ≥ 1`).
5. Comparisons are exact float comparisons as written. Episodes repeat freely.
6. K's execution layer governs every fill. A cut to zero and a restore from zero always fill;
   **a cut to half is a downward resize between two positive weights, so the A1u gain band and
   the H2 December deferral apply to it exactly as to any K downsize** (no new exemption; with
   drifts of ~20–50 pp it clears the 8 pp band whenever K is not near zero weight). A restore from
   half is an up-resize (0.5 pp band).

## 4. Arms (6, fixed; no others)

| arm | candidate | `d1` | `f1` | `d2` | restore | label |
|---|---|---|---|---|---|---|
| **A1** | 1 | 5 % | 0 | — | bracket: `≥ X` or `≤ 0.95 X` | `A1_cut0_brk5` |
| **A2** | 1 | 5 % | 0 | — | bracket: `≥ X` or `≤ 0.90 X` | `A2_cut0_brk10` |
| **B1** | 2 | 5 % | 0.5 | — | rebound `r` = 4 % above `L` | `B1_half_r4` |
| **B2** | 2 | 5 % | 0.5 | — | bracket: `≥ X` or `≤ 0.90 X` | `B2_half_brk10` |
| **C1** | 3 | 8 % | 0.5 | 12 % | rebound `r` = 4 % above `L` | `C1_deep_r4` |
| **C2** | 3 | 8 % | 0.5 | 12 % | upper leg only: `≥ X` | `C2_deep_regain` |

`d1 = 5 %` and `r = 4 %` are the less noisy trigger and the better rebound of `short_term_reentry_v1`
(its arm D), so B1 isolates the effect of cutting half instead of all. Candidate 2 is expressed as
half the QLD position (the same ≈ 1x exposure as a QLD → QQQ switch, which this single-instrument
engine cannot hold); it realises only the half it sells, so it is the tax-cheaper way to implement
the de-leverage, and the comparison to a two-instrument switch is a caveat, not an arm.

## 5. Gates (fixed in advance; identical to `short_term_reentry_v1` §5)

Pre-tax vs K, m = 2, base and calibrated: **G1** CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp;
**G2** max DD ≤ K + 1 pp; **G3** 2017+ CAGR ≥ K − 1 pp; **G4** CAGR above K in ≥ 2 of 3 sub-periods
(base); **G5** clean pre-tax bootstrap median Δ ≥ 0; **G6** G1 at m = 1; **G7** six OOS windows:
median Δ ≥ 0, Δ ≥ −0.5 pp and Δ DD ≤ +3 pp in ≥ 5 of 6, Δ DD never > +8 pp; **G8** pooled OOS
bootstrap median Δ ≥ 0; **G9** turnover ≤ 2 × K. After tax ($100k, progressive, liquidated):
**G10** CAGR > K in all three windows; **G11** B1-recent median Δ ≥ 0 and share > 0 ≥ 55 %;
**G12** B2-flat the same. All must pass.

**Selection.** Among passers, highest after-tax CAGR on 2000-03 → 2026-09; ties by pre-tax base m = 2
CAGR, then label. None passing → nothing selected; the trade-off is reported.

## 6. Diagnostics (report only)

Per arm (base model, main window): cuts/yr, second cuts, share of sessions at zero / half,
false-alarm share (episode depth from `H_cut` to its minimum close < 8 %), depth histogram, restores
by the lower leg, mean / median restore-vs-cut fill price (`open[re+1] / open[ex+1] − 1`) and the
share above the cut fill, the 2x leg's mean return on the cut sessions; tax paid and sells/yr.
October 2026 (report only): return from the peak, max DD inside the episode, sessions cut.

## 7. Implementation and reproducibility

`research/fast_engine` is mandatory and unmodified. The only new code is
`engine/fe_restore.py` (a numba signal kernel building the state mask) and `screen.py` (a copy of
`short_term_reentry_v1/screen.py` with the overlay swapped and the hedge variants removed); the
simulation, tax and bootstraps are `fe_sim` / `fe_tax` / `fe_boot` and the oracle study's
`fe_boot_targets`. `--harness`: parity `--quick` (must PASS), K anchors and K − R2, the kernel
against a Python reference for all six arms × both financings (mask and every episode record), a
never-firing overlay reproducing K's targets and NAV bit for bit, target construction (code 2 is
exactly `f1 × w`), `chunk_legs_pc`, and the bootstrap extension vs `fe_boot.boot` (200 paths,
bitwise). `--run` twice must give byte-identical `RESULTS.json`. No change to `src/`, `config/`,
`tests/`, `scripts/`, `research/fast_engine/`; no CLI run.

## Amendments

(none yet)
