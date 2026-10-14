# R2 + T-bill combo v1 (2026-10-14)

**Verdict: FAIL.** The combination passes every pre-tax gate and is the best of the four arms
before tax, but it dies after tax. The pre-registered rule required RS to beat R2 after
Turkish tax in **all three** windows; it loses in all three.

- **RS passes all nine `ewma_band_v1` gates against C0 and both extra gates against R2.**
  On the main window (m = 2) it is the best arm on every headline measure: **15.67 % base /
  15.09 % calibrated CAGR** against R2's 15.48 / 14.90 and C0's 14.67 / 14.10; **max DD
  43.08 %** against 46.76 % and 49.56 %; Calmar 0.364; 2017+ 32.95 %; block-20 bootstrap
  median **+0.716 pp vs C0** and **+0.065 pp vs R2** (both ≥ 0 in both financings).
- **RS loses to R2 alone after tax in all three windows at $100k**: 9.90 % vs 10.16 %
  (2000), 14.34 % vs 14.71 % (2010), 22.68 % vs 22.89 % (2017), i.e. −0.26 / −0.37 / −0.21 pp.
- **The clean after-tax bootstraps are decisive, not marginal**: B1-recent (progressive)
  median Δ (RS − R2) **−1.077 pp** with RS ahead on only **1.5 %** of paths; B2 (flat 20 %)
  **−1.511 pp**, **0.0 %** of paths. The pre-registered bar was median ≥ 0 and share > 0 ≥ 55 %.
- **The FAIL does not hinge on the one modelling choice that could be argued** (BIL\* is a
  total-return synthetic, so its accrual is taxed as a capital gain on sale; real BIL pays
  monthly distributions). With the bill book made **tax-free** — an upper bound — RS still
  misses the bar: 10.30 / 14.51 / 23.10 %, i.e. +0.14 / **−0.20** / +0.21 pp against R2.
  The 2010 window fails even **before** tax (RS 21.93 % < R2 22.16 %).
- Second-order finding for the owner: **S2_10 alone also loses to C0 in all three after-tax
  windows** (9.44 / 14.15 / 22.55 % vs 9.53 / 14.50 / 22.69 %). Its +0.21 pp pre-tax edge does
  not survive the tariff. This is the same outcome `riskoff_after_tax_v1` found for the L2
  bond/gold sleeve (+0.82 pp pre-tax → +0.04 pp after tax).
- The two candidates are **substitutes that compose additively pre-tax** but not after:
  RS's pre-tax edge over R2 comes almost entirely from S2_10's residual sweep (+4.91 % of
  relative gain over the window), not from the early path — whose 32 episodes, identical in
  date to R2's, earn +18.88 pp under RS against +19.72 pp under R2, because the bill leg's
  round trips cost more on the dozen one-session episodes than its interest earns on the long
  ones.

Evidence: `research/r2_tbill_combo_v1/` (`PREREGISTRATION.md`, `screen.py`, `tax_engine.py`,
`DATA_MANIFEST.json`, `RESULTS.json`, `SUMMARY.txt`). The pre-registration, `screen.py`,
`tax_engine.py`, `DATA_MANIFEST.json` and the input copies were committed in **`6f61ae08`**
before any non-control arm was simulated on real data; only the synthetic-price smoke test
(`screen.py --smoke`) ran before that commit. The run is offline, takes about 9 minutes and is
deterministic: two consecutive `--run` invocations produced byte-identical `RESULTS.json`
(SHA-256 `1949eab6bca77b7c6ba2169c65d749ebd9db3a18ce7cbd7f971e76a0469f9080`) and `SUMMARY.txt` (SHA-256 `c3570b068ca14c6bc0879065da0a98014617179e6ff0df5c745df177a24f8765`).

## Question

`fast_reentry_v1` selected **R2** (production E2 plus an early re-entry path, active only while
E2 is off, at **half** production weight; enter if QQQ close > SMA50, QQQ 20-session vol < its
own trailing 252-session median and close > close 20 sessions ago, exit if close < SMA50; the
path merges into production the moment E2's own vote turns on). It is now on main as
`equity_leveraged_trend_v1` 1.2.0.

`tbill_sweep_v1` selected **S2_10**: idle cash in a T-bill ETF. BIL\* holds 0.98 while E2 is
off; while E2 is on it parks the residual `0.98 − w` in BIL\*, re-sized only when the BIL
weight leaves a 10 pp band.

Both arms spend the same idle cash: R2 fills part of the trend-off gap with `0.5·vt` of QLD\*,
S2_10 fills all of it with BIL\*. Does **combining them (RS)** beat each alone — pre-tax **and**
after Turkish tax? The after-tax leg is decisive: S2_10's edge is +0.21 pp pre-tax, and a
frequently re-sized bill sleeve realises gains that the tariff taxes.

## Method

- **Control (C0).** Production `equity_leveraged_trend_v1` 1.1.0 E2: at least 2 of 3 votes on
  the QQQ price close (close > SMA200; 252-session price return > 0, zero hurdle; close >
  126-session max/min midpoint), sized `vt = min(1, 0.35/vol20)` on synthetic QLD\*, total cap
  0.98, decide at the completed close, fill at the next open, 0.5 pp resize band.
- **Mechanics.** Synthetic QLD\* = daily-reset 2x on QQQ total return, 0.95 %/yr expense plus
  DTB3 (base) or DTB3 + 0.75 % (calibrated); cost 4 bps × m per side per leg; idle cash earns
  zero; main window 2000-03-09 → 2026-09-29 (6,679 sessions).
- **Bill instrument BIL\*** (identical to `tbill_sweep_v1`). Session `i` accrues
  `(rate[i] − 0.1356 %)/252` between its open and close; open = previous close; traded at
  4 bps × m.
- **Arms (4; no others).** C0; **R2** (single leg, `fast_reentry_v1` exactly); **S2**
  (`tbill_sweep_v1`'s S2_10 exactly, X = 10 pp); **RS** = R2's QLD\* leg + S2_10's BIL\* leg,
  with the arm's own `active = E2 on OR early path active`. While E2 is off **and** the early
  path is active, the remainder `0.98 − w` is idle and goes to BIL\* under the same 10 pp band;
  while fully off, BIL\* is held at 0.98 as in S2_10.
- **Harness (all checks pass; `--run` stops otherwise).** C0 reproduces `e2_integration_v1`'s
  production-E2 row exactly (36 checks — CAGR, max DD and turnover, cumulative, base/calibrated
  × m = 1/m = 2 — 0 outside 0.01 pp); R2 reproduces `research/fast_reentry_v1/RESULTS.json`
  and S2 reproduces `research/tbill_sweep_v1/RESULTS.json` S2_10 to < 0.0001 pp; the two-leg
  simulator with an empty bill leg equals `trend_robustness_v2.simulate_multi` and the
  `fast_reentry_v1` band simulator **bit for bit**; the after-tax engine reproduces
  `vt_level_tax_v1`'s C0 anchors to < 0.0001 pp on all three windows (pre-tax CAGR, progressive
  CAGR, progressive max DD) and its session loop equals the vendored single-leg engine exactly
  (identical NAV path) when the bill book is empty.
- **Gates.** `ewma_band_v1`'s G1–G9 unchanged, evaluated **against C0** for RS, plus the two
  pre-registered extras against R2 (G1R = `g_return(RS, R2)` in both financings; G5R = the main
  block-20 bootstrap median of RS − R2 ≥ 0 in both financings). G7/R2's windows: the six
  `ewma_band_v1` windows; BIL\* is built from the same T-bill series each market's financing
  uses, so none is dropped.

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD base | Max DD cal. | Calmar | Sharpe | 2017+ | P1 / P2 / P3 | m = 1 CAGR | Boot. med. Δ vs C0 | Δ vs R2 | Turnover/yr | Bill trades |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| **C0 (prod. E2)** | **14.67 %** | **14.10 %** | **49.56 %** | **49.65 %** | **0.296** | **0.654** | **32.44 %** | −1.10 / 13.26 / 32.44 | **14.95 %** | — | −0.683 pp | **5.97** | 0 |
| R2 | 15.48 % | 14.90 % | 46.76 % | 46.88 % | 0.331 | 0.678 | 32.71 % | −0.06 / 14.30 / 32.71 | 15.75 % | +0.683 pp | 0.000 | 5.99 | 0 |
| S2 | 14.88 % | 14.31 % | 45.99 % | 46.09 % | 0.324 | 0.661 | 32.73 % | −0.21 / 12.53 / 32.73 | 15.41 % | +0.181 pp | −0.518 pp | 11.42 | 469 |
| **RS** | **15.67 %** | **15.09 %** | **43.08 %** | **43.21 %** | **0.364** | **0.684** | **32.95 %** | +0.83 / 13.57 / 32.95 | **16.20 %** | **+0.716 pp** | **+0.065 pp** | **11.43** | 504 |

Calibrated bootstrap median Δ: RS +0.691 pp vs C0, +0.064 pp vs R2. Bootstrap medians (base):
C0 7.16 %, R2 7.89 %, S2 7.37 %, RS 7.96 %; RS beats C0 on 69.8 % of paths (p25 −0.22 pp,
p75 +1.73 pp) and beats R2 on only 61.5 % (median Δ +0.065 pp) — the pre-tax margin over R2 is
thin on resampled paths, which is what makes the after-tax sign flip decide the study. Time in market: C0 74.31 %, R2/RS 76.76 % (early path
2.46 % of sessions), S2 74.31 %; bill weight > 0 on 46.88 % of sessions for both bill arms.

Report-only cost stress (base CAGR at m = 1 / m = 2 / m = 4, with m = 4 max DD):
C0 14.95 / 14.67 / 14.13 (50.19 %), R2 15.75 / 15.48 / **14.92** (47.44 %),
S2 15.41 / 14.88 / 13.84 (47.35 %), RS 16.20 / 15.67 / **14.61** (44.53 %).
RS's edge over C0 (±0.48 pp) survives quadruple cost, but **its 0.19 pp edge over R2 does not**:
at m = 4 the bill leg's doubled cost puts R2 (14.92 %) ahead of RS (14.61 %).

### Year-by-year, base m = 2 (partial first/last years included)

| Year | C0 | R2 | S2 | RS | RS − R2 |
|---|---:|---:|---:|---:|---:|
| 2000 | −28.31 | −28.31 | −26.06 | −26.06 | +2.24 |
| 2001 | −12.19 | −8.30 | −9.57 | −5.74 | +2.56 |
| 2002 | −6.94 | −6.12 | −5.90 | −5.10 | +1.02 |
| 2009 | 48.33 | 56.80 | 47.85 | 56.40 | −0.39 |
| 2010 | 11.54 | 10.29 | 10.54 | 9.21 | −1.08 |
| 2012 | 8.67 | 10.08 | 7.66 | 9.06 | −1.02 |
| 2016 | −1.72 | −0.18 | −2.88 | −1.27 | −1.08 |
| 2020 | 53.46 | 53.46 | 52.88 | 52.88 | −0.58 |
| 2021 | 43.90 | 43.90 | 43.69 | 43.69 | −0.21 |
| 2022 | −20.75 | −22.70 | −19.59 | −21.77 | +0.93 |
| 2023 | 53.91 | 61.05 | 54.27 | 61.20 | +0.14 |

The pattern is a rate effect (as in `tbill_sweep_v1`): RS gains when bills pay and the trend
whipsaws (2001, 2002, 2022) and gives back in the ZIRP years (2010, 2012, 2016, 2020, 2021),
where the bill sleeve earns ~0 and pays costs and the early path is diluted by the bill leg's
round trips. Every other year is within ±0.7 pp of R2.

## The early path and the residual sweep

RS's QLD\* leg is R2's, so the early path's 32 episodes have **identical dates** in both arms;
they are measured over the identical spans against C0.

| Arm | Early share of sessions | Episodes | Positive | Median Δ | Sum Δ | Best | Worst |
|---|---:|---:|---:|---:|---:|---|---|
| R2 | 2.46 % | 32 | 18 | +0.24 pp | **+19.72 pp** | 2001-10-31 +8.58 pp | 2022-12-13 −3.10 pp |
| RS | 2.46 % | 32 | 18 | +0.19 pp | **+18.88 pp** | 2001-10-31 +8.68 pp | 2022-12-13 −3.13 pp |

Per episode, RS is *better* where the episode is long (+0.17 pp on the 19-session 2001-05-16
episode, +0.10 pp on 2001-10-31, +0.08 pp on 2022-07-25 — the bill sleeve earns while the early
position is held) and *worse* on the dozen one-session episodes (−0.08 pp each: the early path
starts and stops within a day, so the BIL\* round trip costs more than the interest it earns).
Net −0.84 pp of episode sum.

Total relative gain against C0 over the whole window (ending-multiple ratio − 1):
**R2 +20.33 %, S2 +4.91 %, RS +25.72 %**. So RS ≈ (R2's early path, marginally degraded) ×
(1.051 from S2's residual sweep): the pre-tax combination is additive, and RS's pre-tax edge
over R2 (+0.19 pp CAGR) is the residual sweep, not the early path.

## Trading activity (base, m = 2)

| Arm | Round trips/yr | Resizes/yr | Sells/yr | Turnover/yr (QLD / bill) | Bill trades | Bill sells | 2017+: round trips / sells / turnover |
|---|---:|---:|---:|---|---:|---:|---|
| C0 | 2.94 | 51.51 | 27.41 | 5.97 (5.97 / 0.00) | 0 | 0 | 1.23 / 32.42 / 3.75 |
| R2 | 2.97 | 55.95 | 29.56 | 5.99 (5.99 / 0.00) | 0 | 0 | 1.54 / 34.27 / 4.02 |
| S2 | 2.94 | 51.51 | 27.41 | 11.42 (5.97 / 5.45) | 469 | 401 | 1.23 / 32.42 / 3.75 |
| RS | 2.97 | 55.95 | 29.56 | 11.43 (5.99 / 5.44) | 504 | 436 | 1.54 / 34.27 / 4.02 |

The combination's QLD\* trade counts are R2's exactly; the bill leg adds 504 trades (436 sells)
over the window, 35 more than S2_10's 469 because the early path moves the residual and can
breach the band. RS's turnover (11.43/yr) is inside G9's 2 × C0 limit (11.94) but only just:
that is the constraint that makes the bill leg expensive at higher cost.

## Out of sample (calibrated, m = 2)

| Window | C0 | R2 | S2 | RS | RS Δ CAGR | RS Δ DD |
|---|---|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.98 % / 38.99 % | 9.89 % / 39.63 % | 11.61 % / 33.41 % | 11.35 % / 34.62 % | +1.37 pp | −4.38 pp |
| M1 ^IXIC 1975–1999 | 20.68 % / 38.62 % | 21.18 % / 40.97 % | 22.52 % / 35.69 % | 22.80 % / 36.24 % | +2.12 pp | −2.37 pp |
| M2 ^GSPC 1935–1956 | 11.26 % / 62.52 % | 11.70 % / 58.33 % | 10.97 % / 63.73 % | 11.33 % / 59.80 % | +0.07 pp | −2.73 pp |
| M3 ^N225 1966–2026 | 6.33 % / 67.16 % | 6.40 % / 65.03 % | 7.22 % / 60.28 % | 7.18 % / 60.92 % | +0.85 pp | −6.24 pp |
| M4 ^GDAXI 1989–2026 | 9.41 % / 58.61 % | 9.47 % / 58.10 % | 9.75 % / 58.24 % | 9.74 % / 57.74 % | +0.33 pp | −0.87 pp |
| M5 ^FTSE 1985–2026 | −0.05 % / 82.74 % | −0.59 % / 85.45 % | 0.07 % / 83.67 % | −0.54 % / 86.38 % | −0.49 pp | **+3.64 pp** |
| Median Δ CAGR | — | +0.07 pp | — | **+0.59 pp** | | |
| Pooled bootstrap median Δ (10,000 paths) | — | +0.360 pp | +0.496 pp | **+0.686 pp** | | |

Each cell is CAGR / max DD; N225, GDAXI and FTSE use US T-bills as a USD proxy, the same proxy
that market's financing uses. RS clears G7 with 6 of 6 windows at Δ CAGR ≥ −0.5 pp and 5 of 6
within +3 pp of drawdown — M5 breaches (+3.64 pp). Note RS is *worse* than S2 alone out of
sample on four of six windows (the early path is the drag where bills already pay), and both
bill arms beat C0 nearly everywhere.

## Gates

`ewma_band_v1` G1–G9, all against C0.

| Gate | Rule | R2 | S2 | RS |
|---|---|---|---|---|
| G1 return (base / cal.) | CAGR ≥ C0, or Calmar ≥ 1.1 × C0 and CAGR ≥ C0 − 0.5 pp | pass / pass | pass / pass | pass / pass |
| G2 DD ≤ C0 + 1 pp (base / cal.) | m = 2 max DD | pass / pass | pass / pass | pass / pass |
| G3 2017+ ≥ C0 − 1 pp (base / cal.) | | pass / pass | pass / pass | pass / pass |
| G4 sub-periods | CAGR above C0 in ≥ 2 of 3 | pass (P1, P2, P3) | pass (P2, P3) | pass (P1, P2, P3) |
| G5 bootstrap median Δ ≥ 0 (base / cal.) | | pass (+0.683 / +0.655) | pass (+0.181 / +0.180) | pass (+0.716 / +0.691) |
| G6 G1 at m = 1 (base / cal.) | | pass / pass | pass / pass | pass / pass |
| G7 six OOS windows | median Δ ≥ 0, ≥ 5 of 6 ≥ −0.5 pp, ≥ 5 of 6 DD ≤ +3 pp, never > +8 pp | pass | pass | pass (median +0.59 pp; M5 DD +3.64 pp) |
| G8 pooled OOS bootstrap | median Δ ≥ 0 | pass (+0.360) | pass (+0.496) | pass (+0.686) |
| G9 turnover ≤ 2 × C0 | ≤ 11.94/yr | pass (5.99) | pass (11.42) | pass (11.43) |
| **Result** | | **PASS** | **PASS** | **PASS** |
| **G1R vs R2 (base / cal.)** | `g_return(RS, R2)` at m = 2 | — | — | **pass / pass** |
| **G5R vs R2 (base / cal.)** | bootstrap median (RS − R2) ≥ 0 | — | — | **pass (+0.065 / +0.064)** |

RS clears the whole pre-tax gate set, exactly as pre-registered. S2 and R2 are reported here
because they are the same measurements for the two components; both keep passing.

## After tax ($100k, base financing, m = 2, progressive 2026 tariff, liquidated at the end)

| Window | Arm | Pre-tax CAGR → terminal | After-tax CAGR → terminal | Max DD | Tax (USD at payment FX) | Trades/yr | Sells/yr |
|---|---|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | C0 | 14.67 % → $3,794,313 | 9.53 % → $1,123,105 | 49.56 % | 646,780 | 58.10 | 28.50 |
| | R2 | 15.47 % → $4,565,589 | **10.16 % → $1,306,627** | 46.76 % | 766,160 | 62.65 | 30.69 |
| | S2 | 14.88 % → $3,980,534 | 9.44 % → $1,097,436 | 46.09 % | 686,519 | 75.98 | 38.59 |
| | **RS** | 15.66 % → $4,770,199 | **9.90 % → $1,226,011** | 45.69 % | 781,884 | 81.85 | 41.75 |
| 2010-01 → end | C0 | 21.89 % → $2,749,293 | 14.50 % → $964,887 | 44.69 % | 534,273 | 59.90 | 30.16 |
| | R2 | 22.16 % → $2,854,450 | **14.71 % → $994,825** | 43.78 % | 553,536 | 62.17 | 31.35 |
| | S2 | 21.68 % → $2,674,325 | 14.15 % → $917,637 | 45.35 % | 546,529 | 77.82 | 40.49 |
| | **RS** | 21.93 % → $2,765,695 | **14.34 % → $942,652** | 44.39 % | 565,253 | 80.74 | 42.04 |
| 2017-01 → end | C0 | 32.26 % → $1,525,522 | 22.69 % → $734,028 | 37.61 % | 352,698 | 67.41 | 33.65 |
| | R2 | 32.53 % → $1,556,901 | **22.89 % → $745,349** | 36.24 % | 360,297 | 70.90 | 35.60 |
| | S2 | 32.54 % → $1,558,226 | 22.55 % → $725,443 | 36.98 % | 380,709 | 86.29 | 44.73 |
| | **RS** | 32.77 % → $1,583,963 | **22.68 % → $733,434** | 35.84 % | 387,822 | 90.39 | 46.89 |

- **RS beats C0 after tax in the 2000 window (+0.37 pp) but loses in 2010 (−0.16 pp) and 2017
  (−0.01 pp); it loses to R2 in all three** (−0.26 / −0.37 / −0.21 pp) → the pre-registered
  after-tax condition fails.
- **S2_10 loses to C0 in all three windows after tax** (9.44 / 14.15 / 22.55 vs 9.53 / 14.50 /
  22.69) — the bill sleeve's +0.21 pp pre-tax edge is more than eaten by the tariff.
- R2 beats C0 after tax in all three windows (+0.63 / +0.21 / +0.20 pp), reproducing
  `fast_reentry_v2`'s after-tax finding.
- RS's bill leg adds only 35 more trades/yr than R2 over 2017+ (90.4 vs 70.9) but pushes
  sells/yr to 46.9 — the tax-relevant realisation count is 32 % higher than R2's.
- The `Tax` column is USD converted at each payment date's FX, so it must not be read as an
  end-of-period dollar cost; the comparison that matters is the after-tax terminal wealth.

**Report-only diagnostic — the bill book made tax-free** (an upper bound for a bill sleeve
whose real monthly distributions were treated as dividends rather than a taxed capital gain):
RS 10.30 / 14.51 / 23.10 %, S2 9.70 / 14.32 / 22.95 %. Against R2 that is **+0.14 / −0.20 /
+0.21 pp** — the pre-registered "all three windows" bar still fails, so the verdict does not
depend on the BIL\* capital-gain assumption. (Removing the bill tax lifts RS by 0.40 / 0.17 /
0.42 pp; the rest of the pre-tax detail is unchanged.)

### Clean after-tax bootstraps (B1-recent progressive, B2 flat 20 %)

Block-20 stationary bootstrap, 1,000 paths, seed 20261011, 10-year paths on the fixed
2015-09-01 → 2026-09-30 calendar with its fixed FX, $100k, liquidated at the end (the
`vt_level_tax_v1` / `tax_asym_v2` construction; the calendar is identical to
`vt_level_tax_v1`'s). Median after-tax CAGR: RS 2.23 %, C0 3.08 %, R2 3.52 %, S2 2.20 % (B1);
RS 2.34 %, C0 3.52 %, R2 3.97 %, S2 2.13 % (B2).

| Δ | B1-recent (progressive) | B2 (flat 20 %) |
|---|---|---|
| **RS − R2** median | **−1.077 pp** | **−1.511 pp** |
| RS − R2 share > 0 | **1.5 %** | **0.0 %** |
| RS − R2 p05 / p95 | −2.094 / −0.254 pp | −2.275 / −0.787 pp |
| RS − C0 median | −0.616 pp | −1.018 pp |
| RS − C0 share > 0 | 31.0 % | 21.9 % |

Both bootstraps put RS behind R2 on essentially every path. Note that RS is *also* behind C0
in the bootstrap while being ahead of C0 on the full sample — the resampled paths give the
bill leg's after-tax drag more weight than its in-sample wins.

## Findings

- **Pre-tax the combination is real and strictly better than either component.** RS is the
  best of the four arms on CAGR (both financings), max DD, Calmar, Sharpe, 2017+ and all three
  sub-periods, and it passes every gate. Its drawdown (43.1 %) is 3.7 pp better than R2's and
  6.5 pp better than C0's, the largest DD improvement of any arm here — a rate effect on the
  bill sleeve, concentrated in 2000–2002, exactly as `tbill_sweep_v1` described.
- **But the pre-tax gain over R2 is not the early path.** RS's early episodes earn 0.84 pp less
  than R2's over the identical 32 episodes-starts; the entire +0.19 pp edge comes from the
  residual sweep the two candidates share. The early path and the bill sleeve are substitutes
  for the same idle cash, and the sleeve taxes the early path's short round trips.
- **After tax the sleeve is a net negative, and that decides the study.** A second USD
  instrument whose gains are realised on every resize is taxed at the progressive marginal
  tariff, and the removal happens earlier in the compounding. RS therefore converts a
  +0.19 pp pre-tax edge into a −0.26 to −0.37 pp after-tax deficit against R2 and a −0.6 to
  −1.5 pp deficit on the resampled paths. `riskoff_after_tax_v1` found the same for the L2
  bond/gold sleeve (+0.82 → +0.04 pp), `tax_hedge_v1` for an inverse hedge: in this tax
  regime a second instrument has to clear roughly **0.6–0.8 pp** of pre-tax edge before it
  adds anything after tax. S2_10's +0.21 pp never came close.
- **The verdict is not an artefact of modelling BIL\* as a capital gain.** With the bill book
  exempt from tax altogether, RS still loses the 2010 window (which it also loses *before*
  tax, because 2010–2015 bills paid nothing) and its two wins are +0.14 and +0.21 pp. The
  pre-registered bar is "all three windows", so it fails either way.
- **RS's pre-tax edge over R2 does not survive doubled cost either**: at m = 4, R2 14.92 %
  against RS 14.61 %. The bill leg is the reason (turnover 11.43/yr against R2's 5.99), and
  G9's 2 × C0 limit is passed only because C0 is itself a high-turnover strategy.
- **S2_10 alone is also closed by this study after tax**, on the same evidence as RS: 9.44 vs
  9.53, 14.15 vs 14.50, 22.55 vs 22.69 vs C0.

## Caveats

- **Single history, in-sample selection.** Both components were selected on this window; RS is
  a combination of two in-sample winners and nothing was tuned here. The pre-tax result is the
  expected superposition and adds little new information; the informative part is the after-tax
  sign flip.
- **The after-tax model is not tax advice** and simplifies several things: a single marginal
  investor, no BIST or other offsets, FIFO lots, indexation relief applied per lot when Yi-UFE
  rose ≥ 10 %, instalments assumed fundable by selling the portfolio at the market, no
  transaction costs on the FX conversion of the tax payment beyond the modelled sale cost, and
  the 2026 tariff and thresholds frozen. It reproduces `vt_level_tax_v1` exactly, which is the
  point of the check.
- **BIL\* pays no distributions.** The model taxes its accrual as a capital gain on sale; real
  BIL pays monthly distributions taxed as dividend income (with the model's indexed 22,000 TRY
  declaration threshold). The report-only diagnostic above bounds the effect: the verdict is
  unchanged.
- **The bill leg's after-tax cost is dominated by realisations, not by yield.** Because the
  model works in TRY, a USD gain realised after TRY depreciation is a much larger TRY gain; the
  frequent resizes of the residual therefore create a tax base that the passive cash it
  replaces does not. That is a property of the tax regime, not of BIL.
- **Fills.** Close decision, next-open fill, as everywhere in this series; the real BIL/QLD
  open prints and the T+1 settlement constraints listed in `tbill_sweep_v1` are not re-tested.
- The OOS windows use US T-bills as the USD proxy for ^N225/^GDAXI/^FTSE, as `tbill_sweep_v1`
  did; the after-tax stage has no out-of-sample window (the TRY/Yi-UFE calendar starts in 2000).

## Amendments (post-pre-registration changes to `screen.py`)

The pre-registration (commit `6f61ae08`) fixed the arms, parameters, gates, after-tax windows,
bootstraps, the verdict rule and the report-only diagnostic *before* any non-control arm ran on
real data. Nothing gated changed afterwards. Four implementation defects were found and fixed
while building the run path (the harness checks below are what caught them, and the smoke test
ran before the pre-registration commit):

1. The shared cash box was reset by the second account constructor; the capital is restored
   after both books are created (before the loop, no gated effect).
2. `tax_run` now accepts window dates as well as indices (`bisect`, as `te.run_strategy` does).
3. The harness's `simulate_multi` comparison indexed one dimension too deep (a shape mismatch
   inside the check, not in the simulator).
4. `FlatAccount` is not in the `fast_reentry_v2` copy of `tax_engine.py`; it was appended
   verbatim from `vt_level_tax_v1/screen.py` lines 1319–1334 for B2, with the provenance and
   the file's SHA-256 recorded in `DATA_MANIFEST.json`.

Also, as promised in the pre-registration, the fixed after-tax bootstrap calendar was built from
this campaign's own day series; it is **identical** to `vt_level_tax_v1`'s, and the fixed FX is
the same forward-filled CBRT rate, so the B1/B2 construction matches that study's.

## Reproduction

```
cd <worktree>
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/r2_tbill_combo_v1/screen.py --smoke    # synthetic only
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/r2_tbill_combo_v1/screen.py --harness  # reproduction + tax anchors
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/r2_tbill_combo_v1/screen.py --run      # ~9 min, writes RESULTS.json/SUMMARY.txt
```
