# Crash-recovery entry on R2 (2026-10-14)

> Evidence for this report (`research/crash_entry_v1/` code, data inputs and results) lives in
> the worktree `research/crash_entry_v1`; `research/fast_reentry_v1/RESULTS.json` is carried as
> the harness reference. Control and harness are the production R2 numbers.

**Verdict: FAIL — none of the three crash-recovery arms passes, and the motivating case (2020)
is not fixed by any of them.** `selected = none`; production R2 (`equity_leveraged_trend_v1`
1.2.0) is unchanged.

- **The arms do not add return pre-tax and they do add drawdown.** Base m = 2 CAGR is
  15.45 / 15.47 / 15.42% for K1 / K2 / K3 against C0's (production R2) 15.48%, while max drawdown
  rises to 49.85 / 48.32 / 48.43% against 46.76% — over the pre-registered C0 + 1 pp allowance in
  all three cases. Every arm fails **G1** (return), **G2** (drawdown), **G4** (sub-periods) and
  **G6** (G1 at m = 1); K1 and K2 also fail **G7** (out-of-sample median Δ CAGR of −0.028 and
  −0.009 pp).
- **The crash path never fires in 2020.** The path requires a ≥ 20% drawdown *and* `vol20 < 0.7 x`
  the trailing 40-session vol20 maximum on the same completed close. In 2020 the drawdown gate was
  open from 2020-03-12 to 2020-04-03 (13 sessions) and the vol condition was first satisfied on
  **2020-04-14** — never while the drawdown gate was open. Zero sessions with all entry conditions
  met, zero episodes in the whole 2020 window, and E2's own vote had already turned back on
  2020-04-08. The task's premise (a crash path that enters while E2 and the early path are off)
  cannot be expressed with these fixed parameters: the vol-spike condition and the 20% drawdown
  condition are mutually exclusive in the fastest crash on record.
- **What the path does trade is bear-market rallies, and it loses there.** 11 / 11 / 6 episodes over
  2000–2026; their deltas sum to **−0.52 / −0.25 / −1.25 pp**. The 2000–2002 bear contributes
  −5.93 / −2.98 / −3.14 pp (worst single episodes: 2001-06-29 −3.14 pp, 2003-02-18 −2.35 pp,
  2003-03-03 −1.74 pp), 2008–2009 contributes −2.16 / −1.05 / −1.34 pp, and only the 2022 bear is
  positive (+7.57 / +3.77 / +3.22 pp) — the same 2022–2023 stretch in which R2's own early path
  already trades.
- **After tax the arms are roughly neutral, and they fail the clean bootstrap.** At $100k the
  after-tax deltas vs R2 are +0.01 / +0.47 / +0.87 pp (K1), +0.01 / +0.23 / +0.43 (K2) and
  −0.05 / +0.20 / +0.36 (K3) on the 2000 / 2010 / 2017 windows — K3 fails the all-windows leg. In
  the clean bootstrap all three beat R2 on the median (+0.03 to +0.05 pp) but on only 52.5–53.5%
  of B1 paths (gate: ≥ 55%), so **TAX_B1 and TAX_B2 fail for all three arms**.

Evidence: `research/crash_entry_v1/` (`PREREGISTRATION.md`, `screen.py`, `tax_engine.py`,
`DATA_MANIFEST.json`, `RESULTS.json`, `SUMMARY.txt`). The pre-registration, `screen.py`,
`tax_engine.py`, `DATA_MANIFEST.json` and the byte-copied inputs were committed in `f9ff18d3`,
before any non-control arm was simulated on real data; only the synthetic `--smoke` path ran
before that commit. The run is offline, takes ≈ 5 min 20 s and is deterministic: two consecutive
runs produced byte-identical `RESULTS.json` (SHA-256
`afc62ddc843789febbf6d213d05e03e470c230fc8525ba3cb72a45e49603cc82`) and `SUMMARY.txt`
(`75f515ab42d79ced56d588d34907395a811b91b71642c2806c6789b91c26ed88`).

## Question

Production R2's early re-entry path needs `vol20 < its trailing 252-session median`, and right
after a crash `vol20` is far above that median (0.87 against 0.155 on 2020-04-14), so the path is
shut exactly when the V-recovery starts; E2's own 2-of-3 vote turned on 2020-04-08. `fast_reentry_v1`
flagged this ("2020 is the counter-example to the thesis"), `fast_reentry_v2` failed to confirm
R2's parameters on a plateau test, and `r2_stage_b_v1` showed R2's in-window edge is one 2023
episode. **Does a separate crash-recovery entry path — active only after a deep drawdown, while
E2 and R2's own early path are off — recover that V-recovery without worsening drawdown, pre-tax
and after Turkish income tax?**

## Method

- **Control (C0) = production R2 1.2.0 exactly**: E2 1.1.0's 2-of-3 vote on the QQQ price close
  (close > SMA200; 252-session price return > 0; close > the midpoint of the 126-session max/min)
  sized `min(1, 0.35/vol20)` on the synthetic QLD\* leg, capped at 0.98, plus the half-weight early
  path (SMA50, `vol20` below its trailing 252-session median, `close > close[-20]`; exit below
  SMA50; ended when E2 turns on).
- **Arms (3, all parameters fixed in the pre-registration).** C0 plus a crash-recovery path with
  one state: while E2's vote **and** the R2 early path are off **and** `close <= 0.80 x` the
  trailing 252-session high, enter at `w_crash x vt` when `close > SMA20` **and**
  `vol20 < r x` the trailing 40-session max of `vol20` **and** `close > close[-10]`; exit when
  `close < SMA20`, or when E2's vote or the R2 early path turns on (then that path's weight
  applies). The drawdown condition is an entry gate only. Weight priority: E2 (1.0) > early (0.5)
  > crash (`w_crash`).

| Arm | `w_crash` | vol condition `r` |
|---|---|---|
| K1 | 0.5 x production | 0.7 |
| K2 | 0.25 x production | 0.7 |
| K3 | 0.5 x production | 0.6 |

- **Mechanics.** Synthetic QLD\* = daily-reset 2x on QQQ total return, 0.95%/yr expense plus DTB3
  (base) or DTB3 + 0.75 pp (calibrated); decide at the completed close, fill at the next open; cost
  4 bps x m per unit traded; 0.5 pp resize band; idle cash earns zero. Main window 2000-03-09 →
  2026-09-29 (6,679 sessions).
- **Harness.** E2 reproduces `e2_integration_v1`'s production-E2 row at 0.000000 pp (all eight
  base/calibrated x m = 1/m = 2 CAGR and max DD cells); the recomputed E2 and C0 (= production R2)
  rows equal `fast_reentry_v1/RESULTS.json` on all 20 checks (CAGR, max DD, turnover) to
  ≤ 0.00005 pp; the band simulator equals `trend_robustness_v2.simulate_multi` bit for bit; the
  after-tax engine reproduces the published E2 rows (`vt_level_tax_v1`, 9.53 / 14.50 / 22.69) and
  R2 rows (`fast_reentry_v2`, 10.16 / 14.71 / 22.89) on all 18 checks to ≤ 0.00005 pp.
- **Gates.** `ewma_band_v1`'s G1–G9 unchanged, all versus C0, plus the after-tax legs: after-tax
  CAGR above C0's in all three $100k windows, and in the clean block-20 bootstrap (B1 progressive,
  B2 flat 20%, 1,000 paths, real 2015-09 → 2026-09 calendar, market returns resampled) a median
  Δ ≥ 0 **and** a share of positive paths ≥ 55%.
- **Selection.** The candidate with the highest base m = 2 CAGR among those passing everything.

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD base | Max DD cal. | Calmar | 2017+ | P1 / P2 / P3 | m = 1 CAGR | Boot. median Δ | Share ≥ C0 | Turnover/yr |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| **E2** (reference) | 14.67% | 14.10% | 49.56% | 49.65% | 0.296 | 32.44% | −1.10 / 13.26 / 32.44 | 14.95% | −0.683 pp | 31.1% | 5.97 |
| **C0 (prod. R2)** | **15.48%** | **14.90%** | **46.76%** | **46.88%** | **0.331** | **32.71%** | −0.06 / 14.30 / 32.71 | **15.75%** | — | — | **5.99** |
| K1 | 15.45% | 14.87% | 49.85% | 49.97% | 0.310 | 33.74% | −0.78 / 14.05 / 33.74 | 15.73% | +0.124 pp | 59.5% | 6.09 |
| K2 | 15.47% | 14.88% | 48.32% | 48.44% | 0.320 | 33.23% | −0.42 / 14.18 / 33.23 | 15.74% | +0.067 pp | 60.0% | 6.04 |
| K3 | 15.42% | 14.84% | 48.43% | 48.55% | 0.318 | 33.15% | −0.37 / 14.05 / 33.15 | 15.70% | +0.091 pp | 58.3% | 6.05 |

Bootstrap (base, 5,000 block-20 ten-year paths) median Δ: K1 +0.124 pp with p25 −0.29 / p75 +0.61,
K2 +0.067 (−0.14 / +0.31), K3 +0.091 (−0.28 / +0.50). Sharpe (base): C0 0.678, K1 0.677, K2 0.678,
K3 0.676. Time in market: C0 76.76% (early 2.46%), K1/K2 77.47% (early 2.46%, crash 0.70%),
K3 77.17% (crash 0.40%). Stage B window (2017-01-02 → 2026-07-30, base): C0 31.70% / 31.1% DD,
K1 32.73% / 27.8%, K2 32.22% / 28.5%, K3 32.13% / 28.9%.

Report-only cost stress (base CAGR at m = 1 / m = 2 / m = 4, m = 4 max DD): C0 15.75 / 15.48 / 14.92
(47.44%), K1 15.73 / 15.45 / 14.89 (50.54%), K2 15.74 / 15.47 / 14.91 (49.01%),
K3 15.70 / 15.42 / 14.86 (49.11%).

### Gates

| Gate | K1 | K2 | K3 |
|---|---|---|---|
| G1 return (base / cal.) | **fail / fail** | **fail / fail** | **fail / fail** |
| G2 DD ≤ C0 + 1 pp (both) | **fail** (49.85 / 49.97) | **fail** (48.32 / 48.44) | **fail** (48.43 / 48.55) |
| G3 2017+ ≥ C0 − 1 pp (both) | pass | pass | pass |
| G4 ≥ 2 of 3 sub-periods | **fail** (P3 only) | **fail** (P3 only) | **fail** (P3 only) |
| G5 bootstrap Δ ≥ 0 (both) | pass (+0.124 / +0.118) | pass (+0.067 / +0.064) | pass (+0.091 / +0.087) |
| G6 G1 at m = 1 | **fail / fail** | **fail / fail** | **fail / fail** |
| G7 six OOS windows | **fail** (median −0.028 pp) | **fail** (median −0.009 pp) | pass (median +0.112 pp) |
| G8 pooled OOS bootstrap | pass (+0.022 pp) | pass (+0.014 pp) | pass (+0.000 pp) |
| G9 turnover ≤ 2 x C0 | pass (6.09 vs 11.98) | pass | pass |
| TAX beats C0 in all 3 windows | pass (+0.01 / +0.47 / +0.87) | pass (+0.01 / +0.23 / +0.43) | **fail** (−0.05 / +0.20 / +0.36) |
| TAX_B1 median ≥ 0 and share ≥ 55% | **fail** (+0.046, 52.5%) | **fail** (+0.028, 53.1%) | **fail** (+0.039, 53.5%) |
| TAX_B2 median ≥ 0 and share ≥ 55% | **fail** (+0.045, 53.5%) | **fail** (+0.026, 54.3%) | **fail** (+0.034, 53.2%) |
| **Result** | **fail** | **fail** | **fail** |

G7 detail: K1 clears the −0.5 pp CAGR bar in 6/6 windows and the +3 pp drawdown bar in 5/6, but its
median Δ CAGR is negative (−0.028 pp) and M3's Δ DD is +3.82 pp; K2 is 6/6 and 6/6 with median
−0.009 pp; K3 is 6/6 and 5/6 with median +0.112 pp and M3 +4.81 pp.

### Out of sample (calibrated, m = 2)

| Window | C0 | K1 | K2 | K3 |
|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.89% / 39.63% | 10.26% / 39.63% | 10.08% / 39.63% | 10.08% / 39.63% |
| M1 ^IXIC 1975–1999 | 21.18% / 40.97% | 21.95% / 40.23% | 21.57% / 40.60% | 21.52% / 40.23% |
| M2 ^GSPC 1935–1956 | 11.70% / 58.33% | 11.37% / 60.90% | 11.54% / 59.61% | 11.89% / 56.67% |
| M3 ^N225 1966–2026 | 6.40% / 65.03% | 6.46% / 68.84% | 6.43% / 66.65% | 6.14% / 69.83% |
| M4 ^GDAXI 1989–2026 | 9.47% / 58.10% | 9.37% / 56.28% | 9.43% / 57.18% | 9.51% / 55.93% |
| M5 ^FTSE 1985–2026 | −0.59% / 85.45% | −0.83% / 86.83% | −0.70% / 86.15% | −0.71% / 86.06% |
| Median Δ CAGR | — | −0.028 pp | −0.009 pp | +0.112 pp |
| Max Δ DD | — | +3.82 pp (M3) | +1.62 pp (M3) | +4.81 pp (M3) |
| Pooled bootstrap median Δ (10,000 paths) | — | +0.022 pp | +0.014 pp | +0.000 pp |

### After tax at $100k (progressive Turkish regime, liquidated)

| Window | Arm | Pre-tax CAGR | After-tax CAGR | Δ vs C0 | Max DD | Trades/yr | Tax paid |
|---|---|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | C0 | 15.47% | 10.16% | — | 46.76% | 62.7 | $766,160 |
| 2000-03 → 2026-09 | K1 | 15.45% | 10.17% | **+0.01 pp** | 49.85% | 64.2 | $752,105 |
| 2000-03 → 2026-09 | K2 | 15.46% | 10.17% | **+0.01 pp** | 48.32% | 63.8 | $759,801 |
| 2000-03 → 2026-09 | K3 | 15.41% | 10.11% | −0.05 pp | 48.43% | 63.5 | $749,867 |
| 2010-01 → 2026-09 | C0 | 22.16% | 14.71% | — | 43.78% | 62.2 | $553,536 |
| 2010-01 → 2026-09 | K1 | 22.71% | 15.17% | **+0.47 pp** | 43.78% | 62.6 | $585,605 |
| 2010-01 → 2026-09 | K2 | 22.43% | 14.94% | +0.23 pp | 43.78% | 62.5 | $569,312 |
| 2010-01 → 2026-09 | K3 | 22.39% | 14.90% | +0.20 pp | 43.78% | 62.3 | $566,858 |
| 2017-01 → 2026-09 | C0 | 32.53% | 22.89% | — | 36.24% | 70.9 | $360,297 |
| 2017-01 → 2026-09 | K1 | 33.56% | 23.75% | **+0.87 pp** | 31.19% | 71.7 | $383,708 |
| 2017-01 → 2026-09 | K2 | 33.05% | 23.32% | +0.43 pp | 33.76% | 71.5 | $371,806 |
| 2017-01 → 2026-09 | K3 | 32.97% | 23.25% | +0.36 pp | 34.16% | 71.1 | $370,009 |

### Clean after-tax bootstrap (block-20, 1,000 paths, calendar 2015-09-01 → 2026-09-16)

Median after-tax CAGR of C0 on these paths: 3.52% (B1 progressive) and 3.97% (B2 flat).

| Arm | B1 median Δ | B1 share > 0 | B1 p05 / p95 | B1 median Δ DD | B2 median Δ | B2 share > 0 |
|---|---:|---:|---|---:|---:|---:|
| E2 | −0.485 pp | 35.4% | −2.57 / +1.77 | +0.29 pp | −0.489 pp | 35.3% |
| C0 | — | — | — | — | — | — |
| K1 | +0.046 pp | 52.5% | −1.03 / +1.13 | +0.00 pp | +0.045 pp | 53.5% |
| K2 | +0.028 pp | 53.1% | −0.51 / +0.57 | +0.00 pp | +0.026 pp | 54.3% |
| K3 | +0.039 pp | 53.5% | −0.89 / +0.95 | +0.00 pp | +0.034 pp | 53.2% |

The gate needs median Δ ≥ 0 and share > 0 ≥ 55%: every arm clears the median and misses the share
by 1.5–2.5 pp. For scale, E2's own B1 median Δ is −0.485 pp, i.e. the bootstrap reproduces the
known result that R2 (not E2) is the right control.

## The crash path: per-episode P&L (base, m = 2)

An episode runs from the close before the first crash fill to the close of the reconciliation
session (the next open after the crash path stops driving the target).

**K1's 11 episodes** (Δ = the arm's return minus C0's over the same span):

| Entry decision | Last held | Sessions | Arm | C0 | Δ |
|---|---|---|---:|---:|---:|
| 2001-06-29 | 2001-07-05 | 3 | −3.14% | 0.00% | **−3.14 pp** |
| 2003-02-18 | 2003-02-26 | 6 | −2.35% | 0.00% | **−2.35 pp** |
| 2003-02-27 | 2003-02-28 | 1 | −2.52% | −3.82% | +1.30 pp |
| 2003-03-03 | 2003-03-06 | 3 | −1.74% | 0.00% | **−1.74 pp** |
| 2008-12-18 | 2008-12-23 | 3 | −0.82% | 0.00% | −0.82 pp |
| 2008-12-30 | 2009-01-12 | 8 | −0.15% | 0.00% | −0.15 pp |
| 2009-01-28 | 2009-01-29 | 1 | −0.32% | 0.00% | −0.32 pp |
| 2009-02-03 | 2009-02-17 | 9 | −0.87% | 0.00% | −0.87 pp |
| 2022-07-13 | 2022-07-25 | 8 | +2.26% | −1.02% | **+3.28 pp** |
| 2023-01-10 | 2023-01-17 | 4 | +0.78% | −1.51% | **+2.29 pp** |
| 2023-01-19 | 2023-01-20 | 1 | +3.82% | +1.81% | +2.01 pp |

Sum **−0.52 pp**, 4 of 11 positive, median −0.32 pp. K2 (same entries, half size): sum
−0.25 pp; K3 (6 episodes): sum −1.25 pp.

### By period (sum of episode deltas)

| Arm | 2000–2002 bear | 2008–2009 | 2020 | 2022 bear | episodes |
|---|---:|---:|---:|---:|---:|
| K1 | −5.93 pp (4) | −2.16 pp (4) | **0.00 pp (0)** | +7.57 pp (3) | 11 |
| K2 | −2.98 pp (4) | −1.05 pp (4) | **0.00 pp (0)** | +3.77 pp (3) | 11 |
| K3 | −3.14 pp (1) | −1.34 pp (3) | **0.00 pp (0)** | +3.22 pp (2) | 6 |

Worst three episodes overall (K1): 2001-06-29 → 2001-07-05 −3.14 pp, 2003-02-18 → 2003-02-26
−2.35 pp, 2003-03-03 → 2003-03-06 −1.74 pp — all three inside the 2000–2002 bear. Best:
2022-07-13 +3.28 pp.

Leave-one-episode-out (base, m = 2; C0 CAGR 15.48%): dropping 2022-07-13 leaves K1 at 15.31%
(−0.17 pp vs C0), dropping 2023-01-10 15.35% (−0.13 pp), dropping 2023-01-19 15.36% (−0.11 pp).
The gain is concentrated in the same 2022–2023 window R2's own early path already covers.

### Why the crash path never fired in 2020

`crash_gap_report` (`RESULTS.json.crash_gap_report`) on the measured sessions, K1's parameters:

| Window | dd ≥ 20% open…close (sessions) | `vol20 < 0.7 x vmax40` first | …while dd ≥ 20% | First session with all entry conditions | Sessions with all conditions (both production paths off) | E2 turn-on | R2 early turn-on |
|---|---|---|---|---:|---:|---|---|
| 2020-02-01 → 2020-06-30 | 2020-03-12 … 2020-04-03 (13) | 2020-04-14 | **never** | **none** | 0 | 2020-03-10 | none |
| 2008-09-01 → 2009-06-30 | 2008-09-04 … 2009-06-30 (207) | 2008-09-03 | 2008-09-08 | 2008-12-18 | 18 | 2009-04-30 | 2009-04-21 |

In 2020 the vol-spike condition (`vol20` vs its own 40-session maximum) never came down while the
20% drawdown gate was open: `vol20 / vmax40` was 1.00 through 2020-03-27, 0.99 on 2020-03-30 and
0.95–0.98 from 2020-03-31 to 2020-04-08; it first fell below 0.7 on 2020-04-14 (0.65), by which
time the close (183.37 on 2020-04-03, 196.48 on 2020-04-06) had recovered above 0.8 x the
252-session high (236.98 x 0.8 = 189.58, a 17.1% drawdown on 2020-04-06 and 10.6% on 2020-04-14)
and E2's vote was back on from 2020-04-08. The `close > SMA20` and `close > close[-10]` conditions
were satisfied on 2020-04-06 and 2020-04-07. **The two gates the crash path is built on — "deep
drawdown" and "vol has come down from its spike" — do not overlap in the 2020 crash at all**, so
the design cannot fill the gap it was written for. The binding constraint is the vol gate, not the
drawdown gate: on 2020-04-06/07/08 the drawdown was 17.1 / 17.1 / 15.4% and `vol20 / vmax40` was
still 0.98 / 0.96 / 0.95, so even a 15% drawdown gate would not have entered. The parameters were
fixed in advance and were not changed after seeing this.

### Trading activity (base, m = 2; relevant to Turkish tax)

| Arm | Round trips/yr | Resizes/yr | Sells/yr | Turnover/yr | 2017+: round trips / sells / turnover |
|---|---:|---:|---:|---:|---|---|
| **C0** | **2.97** | **55.95** | **29.56** | **5.99** | **1.54 / 34.27 / 4.02** |
| K1 | 3.16 | 57.12 | 30.16 | 6.09 | 1.44 / 34.78 / 3.95 |
| K2 | 3.16 | 56.74 | 30.01 | 6.04 | 1.44 / 34.58 / 3.98 |
| K3 | 3.09 | 56.59 | 29.93 | 6.05 | 1.44 / 34.47 / 3.95 |

The crash path adds 0.70% (K1/K2) or 0.40% (K3) of sessions in market and 0.19 (K1/K2) or
0.12 (K3) round trips per year — 11 extra round trips in 26 years. Turnover stays inside G9's
2 x limit with room to spare; the reason it does not blow up is that the path barely trades.

## Audit

| Check | Result |
|---|---|
| Truncation probes: C0 and K1 targets plus early/crash flags at `t` recomputed from closes ≤ `t`, 64 sampled sessions each | 0 mismatches |
| Direct trailing-window recomputation of SMA50 / vol median / lag20 and SMA20 / 252-session high / 40-session `vol20` max / lag10, 64 sampled sessions | 0 mismatches |
| Fill timing: decision at the close of session `i-1` filled at the open of session `i` | exact |
| Cost linearity: nav drag at 8 bps vs 4 bps per side, m = 2 | 22.81% vs 12.14%, ratio 1.88 |
| State machine: no early/crash position while E2 is on; the crash path is never active while the early path is; targets within cap and ≥ 0 | holds |
| Harness: E2 deviation row; E2/C0 reproduction vs `fast_reentry_v1`; after-tax anchors E2 and R2; band simulator; rolling median | all pass |

## Findings

- **The 2020 gap cannot be closed by this design.** The gap is real — R2 sat out 2020-03-09 →
  2020-04-08 and re-entered with E2 — but a crash path that waits for `vol20` to fall to 0.7 x its
  spike maximum and for a ≥ 20% drawdown on the same close is waiting for two conditions that do
  not co-occur in a V-recovery: vol falls last, after the price has already recovered past the
  drawdown gate. Making the volume filter looser (K3, 0.6 x) fires even less often (6 episodes),
  because a *lower* threshold is *stricter*: 0.6 x is harder to satisfy than 0.7 x.
- **What is left is bear-market rallies, and they cost more than they earn.** 11 episodes,
  −0.52 pp net, 7 of them in 2001–2003 and 2008–2009, where buying a bounce below the 252-session
  high while `vol20` is off its peak pays −5.93 and −2.16 pp. The three positive episodes
  (2022-07-13, 2023-01-10, 2023-01-19, +7.57 pp) sit in the one stretch where production R2's own
  early path is also active, so the crash path is duplicating an existing signal a few sessions
  early rather than adding a new regime.
- **The drawdown cost is systematic, not an accident of one episode.** Every arm's worst episode
  is a failed entry at half weight inside the 2000–2002 bear, and that is what pushes max DD to
  49.85% (K1) against 46.76% and fails G2 by 3.09 pp. Halving the size (K2, 0.25 x) does not fix
  it: the drawdown is still 48.32% (+1.56 pp) and the return is still below C0.
- **After tax the arms are neutral-to-slightly-positive, and the clean bootstrap rejects them.**
  The realized-history deltas (+0.9 pp on 2017+) come from the 2022–23 episodes and survive
  liquidation, but on resampled paths the arms beat R2 on only 52.5–53.5% of them — below the
  pre-registered 55%. This is the same shape as `vt_level_tax_v1`'s finding that a rule can win on
  the realized path and lose (or barely win) in the bootstrap.
- **The one bright spot is not enough.** K1's Stage B window (2017–2026) is 32.73% CAGR against
  31.70% with a 27.8% against 31.1% max DD, and the 2017+ after-tax delta is +0.87 pp — the crash
  path genuinely helps in the modern window. But G1/G2/G4/G6 are all evaluated on the full
  2000–2026 window and all fail there, and the pre-registration does not allow selecting on the
  sub-window.

## Caveats

- **Single history, in-sample design.** The arms were written knowing which crashes are famous,
  all on the same 2000–2026 window; the bootstrap resamples it. The verdict is pre-registered and
  negative, so this cuts in the safe direction.
- **The crash path's drawdown condition is measured on the QQQ price close**, not on the synthetic
  QLD\* leg; the pre-registration fixed that reading, and the same convention sizes `vt`.
- **K2's max DD differs from K1's** (48.32% vs 49.85%) although the two arms share every entry and
  exit; the size of the early position inside the 2000–2002 bear changes the peak-to-trough path.
- **The after-tax bootstrap keeps the calendar real** (2015-09 → 2026-09 USD/TRY and Yi-UFE) while
  resampling QQQ blocks, exactly as `tax_asym_v2`/`vt_level_tax_v1` do; it cannot explore FX or
  inflation regimes outside that window, and its C0 median after-tax CAGR (3.52% B1 / 3.97% B2) is
  a property of that calendar, not a forecast.
- **The audit is a set of targeted checks, not a proof.** The strongest guarantee is structural
  (every crash-path input reads only trailing windows) plus the 128 truncation probes and the
  direct trailing-window recomputation.
- **OOS data.** The out-of-sample windows are price-only and many fills there are at the previous
  close (Yahoo's pre-1962 opens are placeholders), identically for every arm.
- The K1/K2 M3 Δ DD (+3.82 pp) and K3's (+4.81 pp) show the crash path's extra exposure is visible
  out of sample as well as in it; K3's G7 pass turns on the median Δ CAGR (+0.112 pp), not on
  drawdown.

## Amendments (post-pre-registration changes to `screen.py`)

The pre-registration (commit `f9ff18d3`) fixed the arms, their parameters, the gates and the
selection rule; none of them changed, and no arm parameter or gated metric was modified after any
result was produced. Three defects were fixed while building the reporting stage — one of them
after a first `--run` had completed:

1. `after_tax_bootstrap` used `arms[0]` (the `E2` reference) instead of `C0` as the baseline for
   the B1/B2 deltas, and the SUMMARY printed the p05/p95 percentiles without the x100 conversion to
   pp. Both are reporting defects: the first made C0 show a non-zero delta and E2 a zero one, the
   second mislabelled the tails by 100x. `RESULTS.json` carries `"control": "C0"` explicitly now.
   The gate outcome (TAX_B1/TAX_B2 fail for all three arms) is unchanged by the fix — the correct
   shares (52.5–53.5%) are further below the 55% bar than the earlier mislabelled read.
2. The `--run` path was fixed twice for the crash-path plumbing before the *first* completed run:
   `boot_main` passed the `arm_wants` tuple instead of the weights array, and the after-tax
   bootstrap passed a `[1, T]` target array where the tax engine indexes a 1-D one.
3. The report-only `crash_gap_report` was rewritten once (after the first completed `--run`) so
   that it reports the *transitions* of the entry conditions and restricts "both production paths
   off" to production R2's actual early-path state; the first version reported a window-start
   snapshot (`first_e2_on = 2020-02-03`) and an SMA50 flag instead of the state. No gated metric
   changed; every gated number in this report is from the runs before and after that rewrite
   (the two are identical on all gated fields, and the final version was run twice,
   byte-identically).

## Decision

No production change. Production E2/R2 (`equity_leveraged_trend_v1` 1.2.0) is unchanged: no code,
configuration or runtime was touched. The crash-recovery entry idea fails its pre-registered gates:
it does not fire in 2020 (its two gates are mutually exclusive there), it loses in the
2000–2002 and 2008–2009 bear rallies, its only gains are in a window production R2 already
trades, and it fails the after-tax bootstrap's share requirement.
