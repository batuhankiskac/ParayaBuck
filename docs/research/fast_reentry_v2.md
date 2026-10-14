# Fast re-entry v2 (2026-10-14)

> Evidence for this report (`research/fast_reentry_v2/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/fast-reentry-v2-2026-10`.

**Verdict: FAIL — gate P fails, gate I passes.** R2's early re-entry path is not confirmed as
a parameter-robust rule. All six one-parameter neighbours beat C0 on main-window CAGR
(+0.36 … +1.08 pp), so the *return* edge is not an artefact of the v1 parameters, but the
SMA-40 neighbour deepens max DD to 51.33% — C0 + 1.77 pp, outside the pre-registered
+1 pp allowance. Under the pre-registered conjunction (≥ 5 of 6 neighbours on return **and**
no neighbour outside C0 + 1 pp) that is a fail. Gate I passes, and passes comfortably:
on 21 independent instruments, R2 beats its own E2 control by a median +0.30 pp CAGR
(15 of 21 positive, 17 of 21 within +3 pp drawdown) with a pooled bootstrap median of
+0.23 pp over 42,000 paths.

- v1's R2 is reproduced **exactly** (20 checks, all within 0.01 pp: CAGR, max DD and
  turnover, base/calibrated × m = 1/m = 2) and still passes all nine v1 gates G1–G9. The
  confirmation failure is the new plateau gate, not a contradiction of v1.
- **Gate P (plateau).** 6 of 6 neighbours with higher CAGR than C0 (R2_s40 +0.43,
  R2_s60 +0.36, R2_m126 +1.08, R2_m504 +0.65, R2_w13 +0.54, R2_w23 +1.05 pp), but
  R2_s40's max DD is 51.33% = C0 + 1.77 pp → **FAIL** (5 of 6 within the allowance).
- **Gate I (independent instruments).** Median Δ CAGR +0.299 pp; share Δ > 0 = 71.4%
  (15/21); share Δ DD ≤ +3 pp = 81.0% (17/21); pooled block-20 bootstrap median Δ
  +0.234 pp (42,000 paths) → **PASS**.
- The audit of v1's mechanics is clean: 64 truncation probes and 64 trailing-window
  recomputations with zero mismatches, exact next-open fill timing, cost drag doubling with
  the cost multiplier (ratio 1.88), and the state-machine invariants hold. The corrected
  `vt_level_tax_v1` after-tax engine reproduces its C0 anchors to < 0.0001 pp and puts R2
  ahead of C0 after Turkish tax in all three windows (+0.63 / +0.21 / +0.20 pp).
- The main-window edge stays concentrated: 32 episodes, the three largest still 95.3% of the
  +19.72 pp; leave-one-episode-out removes 0.16–0.36 pp of the +0.81 pp CAGR each.

Evidence: `research/fast_reentry_v2/` (`PREREGISTRATION.md`, `screen.py`, `tax_engine.py`,
`DATA_MANIFEST.json`, `RESULTS.json`, `SUMMARY.txt`). The pre-registration, `screen.py`,
`tax_engine.py`, `DATA_MANIFEST.json`, the byte-copied inputs and
`E2_INTEGRATION_DEVIATION.json` were committed in `0b385d5e`, before any non-control arm was
simulated on real data; only the synthetic-price smoke test of the run path ran before that
commit. The run is offline, takes about 6 minutes and is deterministic: two consecutive runs
produced byte-identical `RESULTS.json` (SHA-256
`a9e70af83157b4f6600f0d074513230e723b0798eb5ddc0376dbc2cc79502186`) and `SUMMARY.txt`.

## Question

`fast_reentry_v1` selected R2: production E2 plus an early re-entry path, active only while
E2 is off, at **half** production weight (enter if QQQ close > SMA50, QQQ 20-session vol <
its own trailing 252-session median, and close > close 20 sessions ago; exit if
close < SMA50; the path becomes plain production the moment E2's own vote turns on). It
cleared all of v1's gates (+0.81 pp base / +0.80 pp calibrated CAGR, max DD 46.8% vs 49.6%,
bootstrap median Δ +0.68 pp) but on a narrow base: the early path is active in 2.46% of
sessions, 3 of its 32 episodes (2001-10-31, 2002-11-04, 2023-01-20) produce 95% of the gain,
its out-of-sample median Δ was only +0.07 pp, and G7 passed at the margin (M5: Δ CAGR
−0.54 pp against a −0.5 pp bar, Δ DD +2.71 pp against +3 pp). v1's own reading was that the
pass is a *candidate*, not a decision. v2 pre-registered two tests before looking at any v2
result:

1. **Plateau (gate P).** Is the edge a property of the rule or of the exact parameters
   (SMA 50, vol-median window 252, weight 1/2)? Six one-parameter neighbours: SMA 40 / 60,
   vol-median window 126 / 504, early weight 1/3 and 2/3.
2. **Independent instruments (gate I).** Applied to 21 other equity instruments with **their
   own** E2 vote, **their own** R2 early path and **their own** 2x daily-reset synthetic leg,
   does the rule still add return over that instrument's own E2 control? This is
   `range_vol_v2`'s independent-instrument test (tag `archive/research/range-vol-v2-2026-10`);
   its quality rule drops `EWC`.

## Method

- **Control (C0).** Production E2 1.1.0: at least 2 of 3 votes on the instrument's own
  adjusted close (close > SMA200; 252-session price return > 0, zero hurdle; close >
  126-session max/min midpoint), sized `vt = min(1, 0.35/vol20)` on the synthetic 2x leg,
  total cap 0.98. Decide at the completed close, fill at the next open.
- **Mechanics.** Synthetic daily-reset 2x on the underlying's total return, 0.95%/yr
  expense plus DTB3 (base) or DTB3 + 0.75% (calibrated); cost 4 bps × m per unit of traded
  notional; 0.5 pp resize band; idle cash earns zero. Main window 2000-03-09 → 2026-09-29
  (6,679 sessions).
- **Arms (8; parameters fixed in the pre-registration).** C0; R2 = (SMA 50, median window
  252, early weight 1/2); and `R2_s40`, `R2_s60`, `R2_m126`, `R2_m504`, `R2_w13`, `R2_w23`,
  each moving exactly one parameter off R2. No other arm was run; nothing was tuned after
  seeing results. Entrance/exit mechanics, costs and the resize band are identical across
  arms.
- **Harness.** C0 reproduces `e2_integration_v1`'s production-E2 deviation row exactly
  (0.000000 pp on CAGR and max DD for base/calibrated × m = 1/m = 2); the band simulator at
  0.5 pp equals `trend_robustness_v2.simulate_multi` bit for bit; the fast rolling-median
  helper equals v1's loop reference bit for bit; and the recomputed C0 and R2 main-window
  rows equal `research/fast_reentry_v1/RESULTS.json` on all 20 checks (CAGR, max DD,
  turnover; base/calibrated × m = 1/m = 2) to 0.01 pp. v1's R2 is therefore reproduced
  exactly.
- **Gates.** Gate P and gate I as pre-registered; all of v1's `ewma_band_v1` gates G1–G9
  unchanged, reported for all 8 arms as the v1 confirmation.
- **Report only.** Leave-one-episode-out on the main window; the corrected
  `vt_level_tax_v1` after-tax engine at $100k on the 2000/2010/2017 windows; per-episode
  P&L; trade counts; an m = 4 cost stress; the v1 audit (truncation probes, trailing-window
  recomputation, fill timing, cost linearity, state-machine invariants).

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD base | Calmar | 2017+ | P1 / P2 / P3 | m = 1 CAGR | Boot. median Δ | OOS median Δ | Turnover/yr |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| **C0 (prod. E2)** | **14.67%** | **14.10%** | **49.56%** | **0.296** | **32.44%** | −1.10 / 13.26 / 32.44 | **14.95%** | — | — | **5.97** |
| **R2** | **15.48%** | **14.90%** | **46.76%** | **0.331** | **32.71%** | −0.06 / 14.30 / 32.71 | **15.75%** | **+0.683 pp** | **+0.068 pp** | **5.99** |
| R2_s40 | 15.11% | 14.53% | 51.33% | 0.294 | 32.73% | −0.90 / 14.12 / 32.73 | 15.39% | +0.718 pp | +0.072 pp | 6.20 |
| R2_s60 | 15.04% | 14.46% | 48.98% | 0.307 | 32.13% | −0.58 / 14.11 / 32.13 | 15.31% | +0.663 pp | +0.142 pp | 6.02 |
| R2_m126 | 15.75% | 15.17% | 48.62% | 0.324 | 33.10% | +0.07 / 14.63 / 33.10 | 16.03% | +0.716 pp | +0.153 pp | 6.00 |
| R2_m504 | 15.32% | 14.74% | 46.93% | 0.326 | 32.63% | +0.14 / 13.63 / 32.63 | 15.60% | +0.574 pp | −0.145 pp | 6.03 |
| R2_w13 | 15.22% | 14.64% | 47.68% | 0.319 | 32.63% | −0.40 / 13.95 / 32.63 | 15.49% | +0.473 pp | +0.057 pp | 5.98 |
| R2_w23 | 15.72% | 15.14% | 45.99% | 0.342 | 32.79% | +0.24 / 14.64 / 32.79 | 16.00% | +0.865 pp | +0.070 pp | 6.00 |

Calibrated max DD: C0 49.65%, R2 46.88%, R2_s40 51.44%, R2_s60 49.09%, R2_m126 48.74%,
R2_m504 47.05%, R2_w13 47.79%, R2_w23 46.13%. Sharpe (base, m = 2): C0 0.654, R2 0.678,
R2_s40 0.666, R2_s60 0.664, R2_m126 0.687, R2_m504 0.674, R2_w13 0.671, R2_w23 0.684. Time
in market: C0 74.31%, R2 76.76% (early path 2.46% of sessions), R2_s40 76.87% (2.56%),
R2_s60 76.42% (2.11%), R2_m126 77.69% (3.38%), R2_m504 76.03% (1.72%), R2_w13 76.76%
(2.46%), R2_w23 76.76% (2.46%) — only the entry/exit path differs, never the exposure while
E2 is on. Bootstrap medians (base): C0 7.16%, R2 7.89%, R2_s40 7.94%,
R2_s60 7.88%, R2_m126 7.97%, R2_m504 7.84%, R2_w13 7.75%, R2_w23 8.03%; R2 beats C0 on 68.9%
of paths with p25 −0.28 pp and p75 +1.66 pp (calibrated median Δ +0.655 pp). Stage B window
(2017-01-02 → 2026-07-30, base, m = 2): C0 31.42% / 32.5% DD, R2 31.70% / 31.1%,
R2_s40 31.72% / 31.0%; the SMA-40 breach is a 2000–2002 effect, not a modern one.

Report-only cost stress (base, not gated): CAGR at m = 1 / m = 2 / m = 4 is
C0 14.95 / 14.67 / 14.13, R2 15.75 / 15.48 / 14.92; R2's m = 4 max DD 47.44% against C0's
50.19%. R2's edge survives quadruple cost (+0.79 pp against C0's). Every arm's maximum
drawdown is the same episode — peak 2000-03-27, trough 2003-03-31 (C0 recovers 2007-05-30,
R2 2006-01-06) — so the whole plateau discussion is a discussion of how each arm behaves in
the 2000–2002 bear market.

### Plateau neighbours (gate P)

Base financing, m = 2. Δ is against C0.

| Neighbour | Parameter moved | Δ CAGR | Max DD | Δ max DD | Beats C0 | Within C0 + 1 pp |
|---|---|---:|---:|---:|---|---|
| R2 (v1's arm) | — | +0.81 pp | 46.76% | −2.80 pp | yes | yes |
| R2_s40 | SMA 50 → 40 | +0.43 pp | **51.33%** | **+1.77 pp** | yes | **NO** |
| R2_s60 | SMA 50 → 60 | +0.36 pp | 48.98% | −0.59 pp | yes | yes |
| R2_m126 | median window 252 → 126 | +1.08 pp | 48.62% | −0.94 pp | yes | yes |
| R2_m504 | median window 252 → 504 | +0.65 pp | 46.93% | −2.63 pp | yes | yes |
| R2_w13 | weight 1/2 → 1/3 | +0.54 pp | 47.68% | −1.89 pp | yes | yes |
| R2_w23 | weight 1/2 → 2/3 | +1.05 pp | 45.99% | −3.57 pp | yes | yes |

**Gate P: FAIL** — 6/6 neighbours beat C0 on CAGR (needs 5), but only 5/6 are within
C0 + 1 pp; R2_s40 breaches by 0.77 pp. The return plateau is broad and flat: every
one-parameter move preserves a positive CAGR edge over C0, between +0.36 pp (SMA 60) and
+1.08 pp (median window 126), and the six neighbours' edges are all within 0.45 pp of R2's
+0.81 pp. It is not flat in drawdown: shortening the SMA to 40 makes the early path fire
earlier and more often (2.56% of sessions, 40 episodes against 32) and the extra early
entries inside the 2000–2002 bear market push the peak-to-trough drawdown to 51.33% against
C0's 49.56%. R2's own parameter set sits at the *edge* of the +1 pp allowance, which is
exactly what a confirmation test should be able to detect.

### Independent instruments (gate I)

Each instrument carries its own E2 votes, its own R2 path and its own 2x daily-reset
synthetic leg on its own adjusted close (calibrated financing, m = 2). Δ = R2 − that
instrument's own C0.

| Instrument | Measured window | C0 CAGR | R2 CAGR | Δ CAGR | Δ max DD |
|---|---|---:|---:|---:|---:|
| XLP | 1999-12-23 → 2026-10-13 | 1.58% | 2.71% | +1.13 pp | −5.83 pp |
| IWM | 2001-05-30 → 2026-10-12 | 2.87% | 3.98% | +1.12 pp | −5.70 pp |
| EWH | 1997-03-18 → 2026-10-13 | 4.37% | 5.45% | +1.08 pp | −5.48 pp |
| XLK | 1999-12-23 → 2026-10-13 | 13.41% | 14.13% | +0.72 pp | −4.12 pp |
| EFA | 2002-09-04 → 2026-10-13 | 8.09% | 8.76% | +0.67 pp | +0.42 pp |
| EWA | 1997-03-18 → 2026-10-13 | 1.91% | 2.51% | +0.61 pp | −3.24 pp |
| XLU | 1999-12-23 → 2026-10-13 | 5.12% | 5.60% | +0.47 pp | −2.32 pp |
| XLF | 1999-12-23 → 2026-10-13 | 2.60% | 3.07% | +0.46 pp | −2.72 pp |
| XLB | 1999-12-23 → 2026-10-13 | 0.82% | 1.22% | +0.40 pp | +1.31 pp |
| EWG | 1997-03-18 → 2026-10-13 | 4.93% | 5.26% | +0.33 pp | +1.29 pp |
| XLE | 1999-12-23 → 2026-10-13 | 6.45% | 6.75% | +0.30 pp | −2.44 pp |
| EWJ | 1997-03-18 → 2026-10-13 | −0.06% | 0.15% | +0.21 pp | −1.26 pp |
| XLI | 1999-12-23 → 2026-10-13 | 6.01% | 6.21% | +0.19 pp | +0.46 pp |
| DIA | 1999-01-21 → 2026-10-12 | 5.35% | 5.50% | +0.15 pp | +1.24 pp |
| XLY | 1999-12-23 → 2026-10-13 | 6.40% | 6.43% | +0.03 pp | −0.34 pp |
| XLV | 1999-12-23 → 2026-10-13 | −0.05% | −0.08% | −0.03 pp | +1.14 pp |
| EWL | 1997-03-18 → 2026-10-13 | 4.26% | 4.17% | −0.09 pp | **+3.50 pp** |
| EEM | 2004-04-15 → 2026-10-13 | 4.79% | 4.48% | −0.31 pp | −0.00 pp |
| SPY | 1994-01-28 → 2026-10-12 | 11.60% | 11.25% | −0.35 pp | **+3.21 pp** |
| EWS | 1997-03-18 → 2026-10-13 | 8.94% | 8.58% | −0.37 pp | **+3.04 pp** |
| EWU | 1997-03-18 → 2026-10-13 | 3.51% | 2.77% | −0.74 pp | **+3.63 pp** |

`EWC` was excluded by the pre-registered quality rule before any simulation (5.62% of bars
invalid; all other instruments ≤ 3.55%). Gate I metrics: median Δ CAGR +0.299 pp; share
Δ CAGR > 0 = 15/21 = 71.4%; share Δ DD ≤ +3 pp = 17/21 = 81.0%; pooled block-20 bootstrap
(2,000 paths per instrument, 42,000 paths, seed 20261011) median Δ +0.234 pp, share of paths
≥ 0 = 57.7%. All four pass → **Gate I PASS**.

The four drawdown breaches (`EWL`, `SPY`, `EWS`, `EWU`) are the instruments whose own C0
already sits in a shallow, high-exposure regime; the early path's extra exposure there costs
drawdown without return. Note the absolute CAGRs: these are 1x equity instruments with a 2x
synthetic leg and the same 0.98 cap, so the instrument C0s span −0.06% to 13.41% (median
5.26%) and the test is about Δ, not levels. A Δ of +0.30 pp on a 5% CAGR control is a much
larger relative improvement than +0.30 pp on QLD's 14.67%.

### Out of sample (calibrated, m = 2)

| Window | C0 | R2 | R2_s40 | R2_s60 | R2_m126 | R2_m504 | R2_w13 | R2_w23 |
|---|---|---|---|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.98% / 38.99% | 9.89% / 39.63% | 10.08% / 39.30% | 9.79% / 38.16% | 10.27% / 38.77% | 9.64% / 40.27% | 9.93% / 39.40% | 9.85% / 39.87% |
| M1 ^IXIC 1975–1999 | 20.68% / 38.62% | 21.18% / 40.97% | 21.50% / 41.92% | 21.77% / 40.14% | 21.90% / 40.97% | 21.14% / 40.97% | 21.02% / 40.19% | 21.34% / 41.76% |
| M2 ^GSPC 1935–1956 | 11.26% / 62.52% | 11.70% / 58.33% | 11.70% / 60.52% | 11.70% / 56.38% | 11.40% / 56.43% | 12.01% / 51.47% | 11.57% / 59.71% | 11.83% / 57.48% |
| M3 ^N225 1966–2026 | 6.33% / 67.16% | 6.40% / 65.03% | 6.37% / 68.20% | 6.67% / 63.54% | 6.32% / 65.02% | 6.30% / 66.12% | 6.39% / 65.60% | 6.41% / 65.26% |
| M4 ^GDAXI 1989–2026 | 9.41% / 58.61% | 9.47% / 58.10% | 9.45% / 57.95% | 9.35% / 58.10% | 9.58% / 56.02% | 8.97% / 58.61% | 9.47% / 58.27% | 9.48% / 57.93% |
| M5 ^FTSE 1985–2026 | −0.05% / 82.74% | −0.59% / 85.45% | −0.64% / 86.16% | −0.46% / 84.56% | −0.60% / 84.85% | −0.31% / 84.07% | −0.40% / 84.57% | −0.78% / 86.31% |
| Median Δ CAGR | — | +0.068 pp | +0.072 pp | +0.142 pp | +0.153 pp | −0.145 pp | +0.057 pp | +0.070 pp |
| Pooled bootstrap median Δ | — | +0.360 pp | +0.381 pp | +0.326 pp | +0.375 pp | +0.303 pp | +0.252 pp | +0.457 pp |

Δ DD against C0: R2 +0.63 / +2.36 / −4.19 / −2.13 / −0.51 / +2.71 pp (all six within +3 pp).
The out-of-sample evidence remains thin for R2 and is thin for every neighbour: the median
Δ CAGR is a fraction of a basis point, five of six windows clear the −0.5 pp bar for R2
(M5 is −0.54 pp) and R2's Δ DD sits 0.29 pp inside the +3 pp allowance on M5. `R2_m504` and
`R2_w23` both fail G7 (median Δ CAGR −0.145 pp; M1 +3.14 pp and M5 +3.57 pp), `R2_s40` fails
G7 as well — the neighbours are **not** uniformly gate-safe.

### Unchanged v1 gates G1–G9

| Gate | R2 | R2_s40 | R2_s60 | R2_m126 | R2_m504 | R2_w13 | R2_w23 |
|---|---|---|---|---|---|---|---|
| G1 return (base / cal.) | pass | pass | pass | pass | pass | pass | pass |
| G2 DD ≤ C0 + 1 pp | pass | **fail** | pass | pass | pass | pass | pass |
| G3 2017+ ≥ C0 − 1 pp | pass | pass | pass | pass | pass | pass | pass |
| G4 ≥ 2 of 3 sub-periods | pass | pass | pass | pass | pass | pass | pass |
| G5 bootstrap Δ ≥ 0 | pass | pass | pass | pass | pass | pass | pass |
| G6 G1 at m = 1 | pass | pass | pass | pass | pass | pass | pass |
| G7 six OOS windows | pass | **fail** | pass | pass | **fail** | pass | **fail** |
| G8 pooled OOS bootstrap | pass | pass | pass | pass | pass | pass | pass |
| G9 turnover ≤ 2 × C0 | pass | pass | pass | pass | pass | pass | pass |
| **Result** | **pass** | **fail** | **pass** | **pass** | **fail** | **pass** | **fail** |

R2's v1 pass is reproduced unchanged: G1–G9 as in `fast_reentry_v1`'s report, with the same
margin (M5 Δ CAGR −0.54 pp, M5 Δ DD +2.71 pp).

### The early path: per-episode P&L and leave-one-episode-out

R2 has 32 episodes (base, m = 2), 18 of them positive, summing to +19.72 pp of relative
gain; the three largest are 95.3% of it.

| Entry decision | Last held | Sessions | Arm | C0 | Δ |
|---|---|---|---:|---:|---:|
| 2001-10-31 | 2001-12-05 | 24 | +8.97% | +0.39% | +8.58 pp |
| 2001-05-16 | 2001-06-13 | 19 | −2.49% | 0.00% | −2.49 pp |
| 2023-01-17 | 2023-01-19 | 2 | −1.85% | 0.00% | −1.85 pp |
| 2022-12-13 | 2022-12-15 | 2 | −3.10% | 0.00% | −3.10 pp |
| 2009-04-21 | 2009-04-30 | 7 | +3.50% | +0.32% | +3.18 pp |
| 2002-11-04 | 2002-12-02 | 19 | +2.12% | −1.33% | +3.45 pp |
| 2023-01-20 | 2023-02-01 | 8 | +8.74% | +1.99% | +6.75 pp |
| (25 more episodes) | | | | | +5.20 pp |

Leave-one-episode-out (the episode's decisions replaced by C0's, i.e. treated as flat; base,
m = 2; C0 CAGR 14.67%, R2 CAGR 15.48%):

| Episode removed | Episode Δ | R2 CAGR with it flat | Δ CAGR vs C0 that remains |
|---|---:|---:|---:|
| 2001-10-31 → 2001-12-05 | +8.58 pp | 15.12% | **+0.446 pp** |
| 2023-01-20 → 2023-02-01 | +6.75 pp | 15.20% | **+0.523 pp** |
| 2002-11-04 → 2002-12-02 | +3.45 pp | 15.33% | **+0.652 pp** |

Removing R2's single most valuable episode still leaves +0.45 pp of CAGR over C0, and removing
the second-largest still leaves +0.52 pp: the edge is not one event, but it is a handful of
events plus a long tail of ±0.1–0.5 pp episodes.

### After tax at $100k (report only, `vt_level_tax_v1` engine)

Turkish progressive regime, liquidated at the end of the window. The engine reproduces
`vt_level_tax_v1`'s C0 anchors to within 0.0001 pp on all three windows (all nine checks
pass), which validates the wiring.

| Window | Arm | Pre-tax CAGR | After-tax CAGR | After-tax max DD | Tax paid |
|---|---|---:|---:|---:|---:|
| 2000-03-09 → 2026-09-29 | C0 | 14.67% | 9.53% | 49.56% | $646,780 |
| 2000-03-09 → 2026-09-29 | **R2** | **15.47%** | **10.16%** | **46.76%** | $766,160 |
| 2010-01-01 → end | C0 | 21.89% | 14.50% | 44.69% | $534,273 |
| 2010-01-01 → end | **R2** | **22.16%** | **14.71%** | **43.78%** | $553,536 |
| 2017-01-03 → end | C0 | 32.26% | 22.69% | 37.61% | $352,698 |
| 2017-01-03 → end | **R2** | **32.53%** | **22.89%** | **36.24%** | $360,297 |

R2 stays ahead of C0 after tax in every window (+0.63 / +0.21 / +0.20 pp), i.e. the extra
realisations cost tax but less than they earn. Tax itself is the dominant drag: it removes
5.1 pp of CAGR over the full window at $100k and $119k of the extra terminal wealth that R2's
extra trades generate over 26 years.

## Audit of `fast_reentry_v1`'s mechanics

| Check | Result |
|---|---|
| Truncation probe: R2's target and early flag at `t` recomputed from closes ≤ `t`, 64 sampled sessions | 0 mismatches |
| Trailing-window recomputation of SMA50 / close[−20] / 252-session vol median, 64 sampled sessions | 0 mismatches |
| Fill timing: decision at the close of session `i−1` filled at the open of session `i` | exact (nav = close(last)/open(i), cost-free hand case) |
| Cost linearity: nav drag at 8 bps vs 4 bps per side, m = 2 | 22.49% vs 11.96%, ratio 1.88 |
| State machine: early path never active while E2 is on; targets ≤ cap and ≥ 0 | holds |
| v1 reproduction (C0 and R2 rows, CAGR / max DD / turnover, base/calibrated × m = 1/m = 2) | 20/20 within 0.01 pp |
| C0 vs `e2_integration_v1` production-E2 deviation row | 0.000000 pp on all 8 rows |

All checks pass. The truncation probe is sampled (64 sessions, seeded), not exhaustive; the
deterministic equivalents (SMA, close lag, vol median) are recomputed exhaustively by the
harness identity check on the main window.

## Findings

- **The confirmation fails on drawdown, not on return.** Every one-parameter neighbour beats
  C0 on CAGR, four of the six by more than R2's own +0.81 pp, and two (`R2_m126`, `R2_w23`)
  beat R2 itself. The short-SMA neighbour (40 instead of 50) buys +0.43 pp of return and
  pays +1.77 pp of extra maximum drawdown — outside the pre-registered allowance. R2's exact
  parameter set sits at the edge of the allowance, which is what a plateau test exists to
  detect.
- **The independent-instrument evidence is the strongest evidence for the idea.** 15 of 21
  instruments gain, the median gain is +0.30 pp, and the pooled bootstrap is positive over
  42,000 paths. This is much stronger than v1's price-only out-of-sample windows
  (median +0.07 pp) because there are 21 independent histories rather than 6 correlated
  index windows — but the instruments are 1x baskets whose own E2 controls are weak
  (5–13% CAGR), so the same absolute delta means a much larger relative improvement than on
  QLD.
- **The edge remains an episode effect.** 32 episodes, 3 of them 95.3% of the gain; each of
  the top three is worth 0.16–0.36 pp of CAGR. Removing all three leaves roughly the
  bootstrap's lower quartile.
- **R2's v1 pass is intact and reproduced exactly** — including the marginal parts: G7 clears
  on 5 of 6 windows and M5's Δ DD is +2.71 pp against +3 pp. Nothing in this study
  contradicts v1's numbers, only v1's implicit claim that the parameters are not the
  binding constraint.
- **After tax R2 still wins**, by less than pre-tax: +0.63 pp over 26 years at $100k
  (pre-tax +0.80 pp), and only +0.20 pp on the 2017+ window.
- **Neighbour comparison is not an endorsement of the neighbours.** `R2_m126` looks best
  in-sample (+1.08 pp) but is the *second* best on the instruments test's median and its
  OOS is no better than R2's; `R2_w23` fails G7. Selecting parameters off this table after
  seeing the results is exactly what the pre-registration forbids.

## Caveats

- **The verdict is the pre-registered conjunction and it fails on one neighbour's drawdown.**
  Gate P required *both* ≥ 5 of 6 neighbours on return *and* no neighbour outside C0 + 1 pp;
  the second clause fails by 0.77 pp (R2_s40). A reader who weights the return plateau only
  would read the same numbers as support; the pre-registration does not allow that reading
  and no gate was changed after the fact.
- The plateau neighbours are one-parameter moves, not a full grid: two-way interactions
  (e.g. SMA 40 with the 126-session median) were not tested.
- The instrument sample is independent by instrument but overlapping in calendar time and
  shares market-wide shocks; the ETF windows (1993/1996/1998/2000 → 2026) are shorter than
  the main window and include 2000–2002 and 2008.
- Gate I's drawdown clause passes at 81.0% against an 80% bar — one instrument fewer would
  fail it.
- The out-of-sample windows are price-only and many fills there are at the previous close
  (Yahoo's pre-1962 opens are placeholders), identically for every arm.
- After-tax results are report-only and depend on the `vt_level_tax_v1` model (Turkish
  progressive tariff, Yi-UFE indexation, USD/TRY at payment); they are not gated here.
- The audit is a set of targeted checks, not a proof; the strongest guarantee is structural
  (every signal helper reads only trailing windows) plus the truncation probes.

## Amendments (post-pre-registration changes to `screen.py`)

The pre-registration (commit `0b385d5e`) fixed the arms, parameters, gates and selection
rule; none of them changed, and no arm parameter or gated metric was modified after any
result was produced. Two plumbing defects were fixed **before** the first `--run` finished
writing `RESULTS.json` (the first attempt aborted inside the audit with a `ValueError`, after
the harness, main window, bootstrap, OOS and instrument stages had run but before any arm
table or `RESULTS.json` existed):

1. `_rolling_median_vol` (the fast sliding-window median helper): (a) the equality check
   against v1's loop reference used `np.array_equal`, which is always `False` when NaNs are
   present — replaced with `equal_nan=True`; (b) the helper raised when the truncated series
   in the audit probe was shorter than the trailing window (`window shape cannot be larger
   than input array shape`) and rejected all-NaN rows; both guarded, and the interior-NaN
   detection rewritten as "a finite value followed by a NaN".
2. The audit's truncation probes were restricted to `t ≥ 300` (before that the truncated
   series has no valid 252-session vol median, so the probe would compare two all-NaN
   states).

3. `screen.py` re-implements v1's `arm_signals` in a parameterised form (SMA, median window
   and early weight per arm, `spec`/`comps` arguments) instead of v1's four hard-coded arms.
   This is a refactor, not a behaviour change: the harness reproduces v1's R2 row on all 20
   checks to 0.01 pp, and the fast median helper is checked bit-for-bit against v1's loop.

## Decision

No production change. Production E2 (`equity_leveraged_trend_v1` 1.1.0) is unchanged: no
code, configuration or runtime was touched. R2 remains a v1 candidate, but this study does
**not** confirm it: the pre-registered plateau gate fails on R2_s40's drawdown, and the
independent-instrument evidence — while positive — is not sufficient to overturn a failed
pre-registered gate. The operator should read v1's pass as a parameter-specific, episode-driven
in-sample result.
