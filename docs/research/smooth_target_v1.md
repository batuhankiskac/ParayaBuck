# Partial-adjustment / smoothed target weight on R2 (smooth_target_v1, 2026-10-15)

> Evidence (`research/smooth_target_v1/` code, input copies, `RESULTS.json`, `SUMMARY.txt`,
> `diagnostics.json`) is not on `main`; it is preserved in tag `archive/research/smooth-target-v1-2026-10`; the pre-registration,
> `screen.py`, `tax_engine.py`, `DATA_MANIFEST.json` and the input copies were committed in
> **`80bcbb69`**, before any candidate arm was simulated on real data. Nothing in `src/`,
> `config/`, `tests/`, `scripts/`, `benchmarks/` or `data/runtime/` was touched.

**Verdict: FAIL as pre-registered — no arm is selected; production R2 stays unchanged.** Idea I3
of `docs/research/idea_survey_v3.md` (trade to a *smoothed* target weight so the Turkish taxable
volume — ~91 % of which is vol-target trims — shrinks) does exactly what it says on the traded
volume: `Σ|Δw|` falls 29–40 % and the realised-tax drag falls 0.30–0.72 pp/yr. But the tax saving
is bought with stale-target return, and the trade nets out to ≈ 0 pre-tax and to a knife-edge
after tax:

- **S1 (5-session mean) is the closest miss and the best after-tax arm**: pre-tax 15.42 % against
  R2's 15.48 % with **max DD 43.74 % against 46.76 %** (−3.02 pp, longest underwater 5.78 → 3.79 y)
  and `Σ|Δw|` −29 %; after tax at $100k it **beats R2 in all three windows** (+0.35 / +0.27 /
  +0.36 pp) and passes B1-recent (+0.287 pp on 59.0 % of paths). It fails G1 by **0.054 pp of
  CAGR** (its Calmar is 1.065 × R2's, the bar is 1.10 ×), the new non-inferiority gate G5n by
  **0.009 pp** (base −0.109 pp against a −0.10 pp limit), G4 (only P2), G7 (median −0.42 pp),
  G8 (−0.18 pp) and **B2's share by 1.7 pp** (53.3 % against 55 %).
- **S3 (partial adjustment, λ = 0.2) is the tax-base arm**: `Σ|Δw|` −40 %, turnover 3.59 against
  5.99, realised net TRY gain −10 % and tax paid −13 % on the 2000 window — and it still fails,
  because it gives up 0.81 pp of pre-tax CAGR (2000 window after tax −0.08 pp, 2010 −0.35 pp,
  2017 +0.42 pp).
- **Every arm fails the after-tax block** (`T_after_tax` F for all four) and **G6** (G1 at m = 1).
  S4 is the only arm that passes G7 and G8 (median +0.10 pp, 6/6 windows) and all 21 instruments
  inside +3 pp of drawdown, and it fails every after-tax test.
- **The pre-registration's own prior held**: a pre-tax ≈ 0 arm is rejected by G1/G5n/G7/G8 almost
  by construction, and the arm's real effect (fewer lots consumed, smaller tax drag) is invisible
  to those gates. What is *not* construction is that the after-tax bump is 0.3–0.4 pp at best and
  the B2 share gate fails on both arms that have a real after-tax median.

## Question

Does trading to a smoothed version of R2's target weight — the same target reached over K sessions
instead of one resize, exits to flat still immediate — improve the after-Turkish-tax CAGR at $100k
against production R2, without a materially worse drawdown, at an unchanged vote, unchanged vol
target, unchanged early re-entry path and unchanged 0.5 pp resize band?

Context that motivated it: `deriv_vote_v1` measured that ~91 % of R2's realised Turkish taxable
gain is booked by daily vol-resize *trims*, not by vote exits, and that the resizes/yr are almost
independent of the vote (`deriv_vote_v1` §"the tax base is the vol target"). I3 therefore attacks
the *traded volume* `Σ|Δw|` rather than the signal.

## Method

- **C0 = production R2 exactly** (`equity_leveraged_trend_v1` 1.2.0): E2's ≥ 2-of-3 vote on the QQQ
  price close (`close > SMA200`; 252-session price return > 0; `close >` the 126-session max/min
  midpoint), sized `min(1, 0.35/vol20)` of the held instrument with the total capped at 0.98; while
  the vote is off, half that weight if `close > SMA50` **and** `vol20 <` its trailing 252-session
  median **and** `close > close[-20]`, exited below SMA50 and ended when the vote turns on.
- **Arms (4; every parameter fixed in the pre-registration).** Let `s_t = min(0.98, min(1, 0.35/vol20_t))`
  and R2's raw target `w*_t = s_t` on-state, `0.5·s_t` while the early path is active, `0` otherwise.
  - **S1** target `= mean(w*_{t−4..t})` (**K = 5**; the window includes the zeros of flat sessions).
  - **S2** target `= mean(w*_{t−9..t})` (**K = 10**).
  - **S3** `w̄_t = w̄_{t−1} + 0.2·(w*_t − w̄_{t−1})` (**λ = 0.2**), state reset to 0 on an exempt session.
  - **S4** target `= mean(s_{t−4..t})` where the vote is on; **`= 0.5·s_t` unsmoothed** where the
    early path is active; 0 otherwise.
  - **Flat exemption (S1–S3)**: where the vote is off *and* the early path is inactive the target
    is 0 — the exit is immediate at the next open, as in R2.
  - The 0.5 pp band is applied **after** smoothing (the arm's target against the held weight);
    entries and exits to flat trade in full.
- **Mechanics.** Synthetic daily-reset 2× QLD\* on QQQ total return, 0.95 %/yr expense, financing
  DTB3 (base) or DTB3 + 0.75 pp (calibrated); decide at the completed close, fill at the next open;
  4 bps × m; idle cash earns zero; main window 2000-03-09 → 2026-09-29 (6,679 sessions); block-20
  stationary bootstrap seed 20261011, 5,000 ten-year paths; six OOS windows (^GSPC 1957–1999 plus
  `e2_confirmation_v1`'s M1 ^IXIC 1975–1999, M2 ^GSPC 1935–1956, M3 ^N225, M4 ^GDAXI, M5 ^FTSE,
  calibrated, m = 2) and the pooled OOS bootstrap; the 21-instrument set of `fast_reentry_v2`
  (EWC excluded by the pre-existing quality rule), each against its own control.
- **After tax.** The corrected `vt_level_tax_v1` engine (vendored as `tax_engine.py`, byte copy of
  the file `deriv_vote_v1` archived: SHA-256 `7a5d54aa…`), $100k, base financing, m = 2, FIFO lots,
  Yİ-ÜFE indexation with the 10 % floor, 2026 progressive tariff deflated by `Yİ-ÜFE(Oct Y−1)/Yİ-ÜFE(Oct 2025)`,
  per-calendar-year netting with no loss carry-forward, liquidation at the window end; plus the
  clean B1-recent (progressive) and B2 (flat 20 %, losses carried forward) block-20 bootstraps of
  1,000 ten-year paths on the fixed 2015-09 → 2026-09 calendar with its real FX.
- **Gates (pre-registered).** G1–G4 and G6–G9 as `ewma_band_v1`; **G5 replaced** by a
  non-inferiority test on the pre-tax clean bootstrap (median Δ ≥ −0.10 pp, base and calibrated);
  gate I gated on the drawdown-spread condition only (share Δ max DD ≤ +3 pp ≥ 80 %); after tax
  above R2 in all three windows; B1/B2 median Δ ≥ 0 **and** share > 0 ≥ 55 %. Selection: all gates,
  then the highest base m = 2 CAGR; otherwise FAIL.
- **Harness (all PASS).** C0 reproduces `e2_integration_v1`'s production-E2 deviation row exactly
  (0.000000 pp on CAGR and max DD, base/calibrated × m = 1/2) and R2's `fast_reentry_v1` main-window
  rows (10 checks, 0 outside 0.01 pp); C0's vote equals production `e2_up`; C0's targets equal
  production E2's wants where the vote is on and `0.5 × vt` or flat where it is off; the band
  simulator at 0.5 pp equals `trend_robustness_v2.simulate_multi` bit for bit; the fast rolling
  median equals the v1 loop reference bit for bit; **the four smoothing transforms equal a direct
  per-session recomputation at 64 sampled sessions, the flat exemption holds and C0's target series
  is bit-identical to R2's raw target**; C0's after-tax anchors reproduce to ≤ 0.00004 pp
  (10.160 / 14.706 / 22.886 %) and the clean bootstrap medians to ≤ 0.00004 pp (B1 3.5155 %,
  B2 3.9678 %). All five arm audits pass (look-ahead truncation, trailing-window recompute, fill
  timing, cost linearity, state invariants including the flat-exemption invariant).

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD base (cal.) | Calmar | 2017+ | P1 / P2 / P3 | m = 1 | turn/yr | Σ\|Δw\| | sells/yr | mean target |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| **C0 (prod. R2)** | **15.48 %** | **14.90 %** | **46.76 % (46.88)** | **0.331** | **32.71 %** | −0.06 / 14.30 / 32.71 | **15.75 %** | **5.99** | **159.13** | 29.56 | 67.31 % |
| S1 K = 5 mean | 15.42 % | 14.85 % | 43.74 % (43.85) | 0.353 | 32.38 % | −0.15 / 14.59 / 32.38 | 15.62 % | 4.26 | 113.04 | 30.80 | 66.18 % |
| S2 K = 10 mean | 15.03 % | 14.46 % | **39.91 % (40.10)** | **0.377** | 31.34 % | +0.10 / 14.07 / 31.34 | 15.21 % | 3.90 | 103.62 | 29.33 | 65.35 % |
| S3 λ = 0.2 | 14.67 % | 14.11 % | 42.66 % (42.77) | 0.344 | 31.99 % | −0.56 / 13.04 / 31.99 | 14.84 % | **3.59** | **95.38** | 26.77 | 65.04 % |
| S4 on-state K = 5 | 15.44 % | 14.86 % | 45.84 % (45.97) | 0.337 | 31.75 % | +0.32 / 14.72 / 31.75 | 15.69 % | 5.41 | 143.67 | 32.42 | 67.23 % |

Cost stress (base): at m = 4 S1 (15.03 %) and S4 (14.94 %) are *above* C0 (14.92 %) — the smoothing
saves the commission it is designed to save; at m = 1 C0 (15.75 %) is still ahead of every arm.
Sharpe: C0 0.678, S1 0.679, S2 0.667, S3 0.660, S4 0.675.

Block-20 bootstrap (median arm − C0, base / calibrated): S1 −0.109 / −0.094 pp (46.9 % / 47.2 % of
paths ≥ 0), S2 −0.566 / −0.541 (38.0 / 38.0), S3 −0.070 / −0.039 (48.6 / 48.1), S4 −0.318 / −0.317
(34.1 / 34.1). C0's own medians are 7.89 % base and 7.43 % calibrated. **All four arms are centred
on zero or below — there is no pre-tax edge to resample** (S1's realised −0.054 pp is inside its own
bootstrap spread, but so is the whole distribution's mass below zero).

### Out of sample (calibrated, m = 2)

| Window | C0 | S1 | S2 | S3 | S4 |
|---|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.89 % / 39.63 % | 9.43 % / 36.39 % | 9.00 % / 36.74 % | 8.71 % / 38.60 % | 9.88 % / 39.80 % |
| M1 ^IXIC 1975–1999 | 21.18 % / 40.97 % | 20.10 % / 39.03 % | 18.87 % / 37.66 % | 19.18 % / 37.09 % | 21.49 % / 40.95 % |
| M2 ^GSPC 1935–1956 | 11.70 % / 58.33 % | 11.95 % / 61.62 % | 12.05 % / 61.57 % | 11.78 % / 61.12 % | 12.31 % / 57.63 % |
| M3 ^N225 1966–2026 | 6.40 % / 65.03 % | 6.13 % / 63.58 % | 6.08 % / 60.66 % | 5.90 % / 62.26 % | 6.45 % / 63.42 % |
| M4 ^GDAXI 1989–2026 | 9.47 % / 58.10 % | 8.24 % / 57.59 % | 8.45 % / 56.67 % | 7.44 % / 56.04 % | 9.64 % / 58.94 % |
| M5 ^FTSE 1985–2026 | −0.59 % / 85.45 % | −0.97 % / 85.62 % | −1.14 % / 86.76 % | −0.92 % / 84.29 % | −0.61 % / 85.48 % |
| Median Δ CAGR | — | −0.422 pp | −0.722 pp | −0.840 pp | **+0.103 pp** |
| Windows Δ CAGR ≥ −0.5 pp | — | 4/6 | 2/6 | 2/6 | 6/6 |
| Windows Δ DD ≤ +3 pp | — | 5/6 | 5/6 | 6/6 | 6/6 |
| Max Δ DD | — | +3.29 pp (M2) | +3.24 pp (M2) | +2.79 pp (M2) | +0.84 pp (M4) |
| Pooled OOS bootstrap median Δ | — | −0.183 pp | −0.286 pp | −0.394 pp | **+0.117 pp** |

Out of sample the smoothing loses on the long, fast US histories (^GSPC 1957–99, ^IXIC 1975–99,
^GDAXI) and wins on the slow ones (^GSPC 1935–56, ^N225, ^FTSE) — the same shape as the in-sample
2020 loss. S4 — the only arm that touches nothing but the on-state sizing and keeps R2's early leg
untouched — is the only arm that passes G7 and G8.

### Gate I — 21 independent instruments (calibrated, m = 2, each vs its own control)

| Arm | Median Δ CAGR | Share Δ > 0 | **Share Δ DD ≤ +3 pp (gated)** | Pooled bootstrap median Δ | Result |
|---|---:|---:|---:|---:|---|
| S1 | +0.326 pp | 71.4 % | **90.5 %** | +0.158 pp | pass |
| S2 | +0.385 pp | 71.4 % | **90.5 %** | +0.159 pp | pass |
| S3 | +0.212 pp | 66.7 % | **95.2 %** | +0.286 pp | pass |
| S4 | +0.131 pp | 71.4 % | **100.0 %** | −0.064 pp | pass |

(C0's own instrument bootstrap median is 2.29 %; 42,000 paths per arm.) The pre-registered gate I
condition passes for all four arms, and the *reference* components are positive on the instruments
for S1–S3 too (+0.13 … +0.39 pp on the median instrument, pooled bootstrap +0.16 … +0.29 pp; S4's
pooled median is −0.06 pp), with the drawdown improved or held on 19–21 of 21. The failures are
concentrated: on EWS S1 deepens the drawdown by +8.34 pp and on XLU by +3.48 pp, and the largest
drawdown *improvements* are on the country ETFs whose vol moves fastest (EWA −9.10, EWL −5.73,
EWJ −6.60). This is the mirror image of the main window: smoothing helps where the vol target
moves a lot and hurts where a fast re-lever is the whole edge.

### After tax ($100k, base financing, m = 2, progressive 2026 tariff, liquidated)

| Window | Arm | Pre-tax | After-tax | **Δ vs R2** | Max DD | Realised net TRY gain | Tax paid USD | Sells/yr | Σ\|Δw\| |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | C0 | 15.47 % | 10.160 % | — | 46.76 % | 59.79 m | 766,160 | 30.4 | 159.13 |
| | S1 | 15.42 % | **10.513 %** | **+0.353** | 43.92 % | 62.13 m | 773,559 | 31.3 | 113.04 |
| | S2 | 15.02 % | 10.357 % | +0.197 | 46.13 % | 57.95 m | 722,008 | 29.9 | 103.62 |
| | S3 | 14.67 % | 10.082 % | −0.078 | 45.98 % | 53.56 m | 666,196 | 27.3 | 95.38 |
| | S4 | 15.44 % | 10.428 % | +0.268 | 45.84 % | 61.83 m | 778,758 | 33.0 | 143.67 |
| 2010-01 → end | C0 | 22.16 % | 14.706 % | — | 43.78 % | 45.23 m | 553,536 | 30.9 | 159.13 |
| | S1 | 21.98 % | **14.974 %** | **+0.268** | 43.60 % | 44.97 m | 535,517 | 31.2 | 113.04 |
| | S2 | 21.05 % | 14.417 % | −0.289 | 45.58 % | 39.97 m | 472,646 | 30.9 | 103.62 |
| | S3 | 20.95 % | 14.353 % | −0.353 | 45.67 % | 39.23 m | 465,804 | 27.8 | 95.38 |
| | S4 | 21.69 % | 14.648 % | −0.058 | 43.71 % | 43.46 m | 520,776 | 33.3 | 143.67 |
| 2017-01 → end | C0 | 32.53 % | 22.886 % | — | 36.24 % | 33.27 m | 360,297 | 35.2 | 159.13 |
| | S1 | 32.18 % | **23.249 %** | **+0.363** | 31.66 % | 32.73 m | 344,057 | 37.5 | 113.04 |
| | S2 | 31.14 % | 22.844 % | −0.042 | 33.83 % | 30.57 m | 316,081 | 36.9 | 103.62 |
| | S3 | 31.79 % | **23.302 %** | **+0.416** | 31.53 % | 31.48 m | 331,067 | 34.4 | 95.38 |
| | S4 | 31.56 % | 22.653 % | −0.233 | 35.05 % | 31.62 m | 332,257 | 39.4 | 143.67 |

Tax drag on the 2000 window (pre-tax CAGR − after-tax CAGR): R2 **5.31 pp**, S1 4.91, S2 4.66,
S3 4.59, S4 5.01. The drag does fall by 0.30–0.72 pp — and in S1's case the pre-tax CAGR falls by
less than that, which is where its +0.35 pp comes from; in S3's case the pre-tax loss (0.81 pp)
eats the whole 0.72 pp.

Clean after-tax bootstrap (1,000 block-20 paths, fixed 2015-09 → 2026-09 calendar with real FX):

| Arm | B1-recent median | Δ vs C0 | share > 0 | B2 flat median | Δ vs C0 | share > 0 |
|---|---:|---:|---:|---:|---:|---:|
| C0 | 3.52 % | — | — | 3.97 % | — | — |
| S1 | 3.76 % | **+0.287 pp** | 59.0 % | 4.09 % | +0.100 pp | 53.3 % |
| S2 | 3.45 % | +0.040 pp | 51.2 % | 3.68 % | −0.237 pp | 43.8 % |
| S3 | 3.91 % | **+0.556 pp** | **62.0 %** | 4.10 % | +0.198 pp | 54.8 % |
| S4 | 3.46 % | −0.017 pp | 49.3 % | 3.78 % | −0.157 pp | 42.0 % |

## Gates

| Arm | G1 | G2 | G3 | G4 | **G5n** | G6 | G7 | G8 | G9 | gate I | **AT** | **B1** | **B2** | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | **F** (−0.054; Calmar ×1.065) | P | P | **F** (P2) | **F** (−0.109) | **F** | **F** (−0.42) | **F** (−0.18) | P (×0.71) | P (90.5 %) | **F** | P (+0.29, 59.0 %) | **F** (share 53.3 %) | **FAIL** |
| S2 | P (Calmar ×1.138) | P | **F** (−1.38) | **F** (P1) | **F** (−0.57) | **F** | **F** (−0.72) | **F** (−0.29) | P (×0.65) | P (90.5 %) | **F** | **F** (51.2 %) | **F** (43.8 %) | **FAIL** |
| S3 | **F** (−0.81) | P | P | **F** (none) | P (−0.070) | **F** | **F** (−0.84) | **F** (−0.39) | P (×0.60) | P (95.2 %) | **F** | P (+0.56, 62.0 %) | **F** (54.8 %) | **FAIL** |
| S4 | **F** (−0.034) | P | P | P (P1, P2) | **F** (−0.32) | **F** | **P** (+0.10) | **P** (+0.12) | P (×0.90) | P (100 %) | **F** | **F** (−0.02) | **F** (42.0 %) | **FAIL** |

After-tax gate detail: S1 passes all three windows and B1 outright and fails only **B2's share**
(53.3 % vs 55 %); S3 fails 2000 and 2010 (−0.078, −0.353) and B2's share (54.8 %); S2 and S4 fail
the window block and B1. Selected arm: **none** (`eligible = []`).

## Trading activity and where the tax base comes from

| Arm | Σ\|Δw\| | turn/yr | trades | resizes | sells | mean trade size | mean resize size | mean sell size | 2017+ sells/yr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 159.13 | 5.99 | 1,645 | 1,486 | 785 | 0.09674 | 0.03850 | 0.20272 | 34.3 |
| S1 | 113.04 | 4.26 | 1,899 | 1,740 | 818 | 0.05953 | 0.02996 | 0.13820 | 36.9 |
| S2 | 103.62 | 3.90 | 2,009 | 1,850 | 779 | 0.05158 | 0.02318 | 0.13302 | 36.5 |
| S3 | 95.38 | 3.59 | 2,177 | 2,018 | 711 | 0.04381 | 0.02398 | 0.13415 | 33.8 |
| S4 | 143.67 | 5.41 | 1,823 | 1,664 | 861 | 0.07881 | 0.02470 | 0.16687 | 38.7 |

Realised TRY gain by reason (2000 window, progressive):

| Arm | trims | exits | tax-funding | liquidation | total | trim share | deferred share | tax USD | drag pp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 54.83 m | −3.23 m | 0.69 m | 7.50 m | 59.79 m | 91.7 % | 7.1 % | 766,160 | 5.31 |
| S1 | 48.26 m | 2.78 m | 0.88 m | 10.21 m | 62.13 m | 77.7 % | 20.9 % | 773,559 | 4.91 |
| S2 | 40.00 m | 7.50 m | 0.67 m | 9.79 m | 57.95 m | 69.0 % | 29.8 % | 722,008 | 4.66 |
| S3 | 37.52 m | 6.51 m | 0.50 m | 9.03 m | 53.56 m | 70.1 % | 29.0 % | 666,196 | 4.59 |
| S4 | 48.41 m | 1.89 m | 0.86 m | 10.67 m | 61.83 m | 78.3 % | 20.3 % | 778,758 | 5.01 |

## Findings

- **The volume claim is true and the count claim is false.** `Σ|Δw|` falls 29 %/35 %/40 %/10 % and
  turnover with it, but the number of taxable events does *not*: resizes/yr rise from 55.9 to
  62.7–76.0 and sells/yr rise for S1, S2 and S4 (29.6 → 29.3–32.4). A smoothed target drifts
  continuously and re-crosses the 0.5 pp band more often, in smaller pieces (mean resize size
  0.0385 → 0.0232–0.0300; mean sell size 0.203 → 0.133–0.167). This is `deriv_vote_v1`'s finding
  from the other side: **the tax base is the vol target, and cutting its volume cuts the number of
  lots consumed per event, not the number of events.** S3 (continuous partial adjustment) is the
  only arm that cuts both (sells/yr 29.6 → 26.8, 2017+ 34.3 → 33.8) and it pays the most return.
- **What the arms actually do after tax is defer, not avoid.** S1's trims fall 6.57 m TRY (−12 %)
  while its exits go from −3.23 m to +2.78 m (+6.01 m) and its liquidation from 7.50 m to 10.21 m
  (+2.71 m), so its *total* realised gain is 3.9 % larger than R2's and its tax paid is 1 % higher —
  yet its after-tax CAGR is +0.35 pp, because more of the gain is booked at the end (deferred share
  7.1 % → 20.9 %) and the trims it does take sell older, more appreciated lots through a smaller
  annual gain at the same marginal rates. S3/S2 are the arms that genuinely cut the base (total
  gain −10 %/−3 %, tax −13 %/−6 %) and they pay for it with exposure: mean target weight
  67.31 % → 65.04 % (S3) with the same time in market (76.76 %).
- **The pre-tax loss is concentrated in the fast rebounds.** S1 gives up 8.78 pp in **2020**
  (44.68 % vs 53.46 %), 6.63 pp in 2004 and 3.66 pp in 2005 — the stale below-target weight misses
  the first sessions of the re-lever — and gains 7.89 pp in 2012 and 3.21 pp in 2009. That is the
  `ewma_band_v1` asymmetry the pre-registration predicted, and it is why the 2017+ window (which
  G3 measures) still passes while G1/G4/G7/G8 do not.
- **The drawdown improvement is real and large for S1/S2/S3** (−3.02 / −6.85 / −4.10 pp, longest
  underwater 5.78 y → 3.78–3.79 y, same 2003-03-31 trough for the 2000–03 max DD, Calmar 0.331 →
  0.353/0.377/0.344, Sharpe 0.678 → 0.679/0.667/0.660). Under G1's Calmar clause it is still short
  for S1 (×1.065 against ×1.10); S2 is the only arm that clears G1 and it does so while losing
  6.85 pp of drawdown and 0.45 pp of CAGR, then fails G3 by 0.38 pp more than the limit.
- **The turn of the trade is small and inside the noise.** A ≈ 0-effect pre-tax arm cannot clear
  G1/G5n/G7/G8; S1's margins are −0.054 pp (G1 CAGR), −0.009 pp (G5n), −0.42 pp (G7 median) and
  −0.18 pp (G8). The pre-registration's G5n relaxation (median ≥ −0.10 pp) was designed for exactly
  this and S1 misses it by 0.009 pp on the base leg while passing the calibrated leg. The honest
  reading is *not* "the gate is wrong": the arm has no pre-tax edge, and the after-tax bump
  (0.27–0.36 pp) is the tax drag it avoids (0.30–0.72 pp) priced at the pre-tax return it gives up.

## Reference: gain_band_v2's A1 (published numbers, not re-run here)

| | A1 (`gain_band_v2`) | S1 (this study) |
|---|---|---|
| pre-tax CAGR base / cal., m = 2 | 15.68 % / 15.07 % (Δ +0.21 / +0.17 pp) | 15.42 % / 14.85 % (Δ −0.054 / −0.042 pp) |
| max DD (Δ) | 47.46 % (+0.70 pp) | 43.74 % (−3.02 pp) |
| turnover/yr, sells/yr | 5.48, 10.4 | 4.26, 30.8 |
| E1 pre-tax bootstrap median Δ | +0.0002 pp base / −0.004 pp cal. (**G5 FAIL**) | −0.109 / −0.094 pp (**G5n FAIL**) |
| after-tax Δ at $100k (2000 / 2010 / 2017) | **+0.457 / +0.436 / +0.853 pp** | +0.353 / +0.268 / +0.363 pp |
| B1 / B2 median Δ, share > 0 | +0.228 pp / 75.5 %; +0.093 pp / 61.9 % | +0.287 pp / 59.0 %; +0.100 pp / 53.3 % |
| six OOS windows, median Δ CAGR | +0.040 pp | −0.422 pp |
| 21 instruments ΔDD ≤ +3 pp | 21/21 | 19/21 |
| verdict | FAIL (G5 only) | FAIL (G1, G4, G5n, G6, G7, G8, AT, B2) |

Both are "tax-only" arms that win after tax and lose the pre-tax bootstrap. A1 concentrates its
effect where the tax is (suppressing *only* downward resizes that would consume appreciated lots,
sells/yr 29.6 → 10.4) and pays almost nothing pre-tax (+0.21 pp); the smoothing in this study
moves *every* trade and pays 0.05–0.81 pp for a similar or smaller after-tax gain.

## Caveats

- **Single history, in-sample arms.** The four forms come from a survey written with 2000–2026 in
  view; every parameter was fixed in `80bcbb69` and none was tuned, but the record shows the arm's
  pre-tax cost is decided by 2020, 2004 and 2005 while its after-tax gain is decided by 2012 and
  2023.
- **`diagnostics.py` / `diagnostics.json` are post-hoc.** They (trade sizes, gate margins, the
  tax-channel split) were written **after** the pre-registered run, change no arm, gate or
  parameter, and no gate outcome depends on them.
- **Interpretation choice fixed in advance.** The mean arms include the zeros of flat sessions and
  the flat exemption overrides the mean; S4 smooths the on-state sizing series (vote-independent)
  and keeps R2's early leg exactly. Alternatives (an entry exemption, an on-state-only averaging
  window, a partial adjustment to the *held* weight rather than to the intended one) were not run
  and would change the numbers — the pre-registration states the reading used.
- **Synthetic instrument.** Signals on the QQQ price close, position on the synthetic daily-reset
  2× QLD\* leg; `instrument_cost_v1` showed real QLD bars move levels by a few tenths of a pp.
- **After-tax model.** Same assumptions and open items as `after_tax_tr_v1` / `tax_asym_v1–v3` /
  `gain_band_v2`: Yİ-ÜFE indexation from the acquisition month to the sale month, the assessment
  year anchored at the previous October, the 2026 tariff, liquidation at the window end, one CBRT
  buying-rate series; B1/B2 keep the calendar (and so FX and inflation) real on 2015–2026 and
  cannot explore other FX regimes. Deltas below ~0.1 pp are inside modelling uncertainty — which is
  where **all four arms' after-tax results sit**, and S1's G5n failure (0.009 pp) is inside it too.
  Not tax advice.
- **The gate set is the pre-registered one.** S1 would be a candidate under a rule that required
  only the after-tax block plus gate I, and B2's share gate (53.3 %) is 1.7 pp short of 55 %; that
  is a fact about the gate, not a reason to move it after the fact. Any such relaxation is a new
  pre-registration.

## Amendments

No change was made to `screen.py` after the pre-registration commit `80bcbb69`: the file that
produced `RESULTS.json` is byte-identical to the committed one (SHA-256
`96ba5d1b84207dfb96be97d538b14b0f567d2bb51d9bbc88b745aa90541ae103`), as is `tax_engine.py`
(`7a5d54aa0eca110d94c367113ac13c909407d34af54567703714ab7060f62776`) and
`PREREGISTRATION.md` (`3ad3265099a110964741d2985ccd73c0016abd1646472724e4c747e44cb0cc7a`). The arms,
parameters, gates, tolerances and selection rule are those of the pre-registration; no gate was
changed after seeing data.

Report-only additions, all after the run and none touching a gated metric:

1. `diagnostics.py` / `diagnostics.json` (trade-size decomposition, gate margins in each gate's own
   units, and the tax-channel split of the 2000 window).
2. `gain_band_v2`'s A1 numbers in `SUMMARY.txt` and §Reference — copied from
   `docs/research/gain_band_v2.md`, never re-run.
3. In `--run`'s `SUMMARY.txt`: the exposure/`Σ|Δw|` table, the realised-TRY-gain line in the
   after-tax table and the early-path-episode relabelling (the episodes are R2's own; the delta is
   arm-vs-C0 over those windows).

The G5 non-inferiority rule, the gate-I drawdown-only gating and every parameter in the
pre-registration were fixed **before** any candidate arm was simulated on real data.

## Reproduction

```
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/smooth_target_v1/screen.py --smoke
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/smooth_target_v1/screen.py --harness
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/smooth_target_v1/screen.py --run
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/smooth_target_v1/diagnostics.py
```

Two consecutive `--run` passes gave byte-identical `RESULTS.json` (SHA-256
`c539bb8d45ce701e9c49fddc1d6630df5b0552ba306a13d5fcf6c0f856242509`) and `SUMMARY.txt`
(`c016c277db3d887738a6a443d22b7a20dbed9d74f6e6821f58189c2462a36c26`). The run is offline and
takes about 8–9 minutes.
