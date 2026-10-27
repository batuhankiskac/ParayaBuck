# gates_v2: circular-lag null, ablation, pre/post-2020 gate, SPA/MCS, expanding window

Branch `research/gates_v2`. Code `research/gates_v2/screen.py`, preregistration and amendments
`research/gates_v2/PREREGISTRATION.md`, outputs `research/gates_v2/RESULTS.json` and `SUMMARY.txt`.
Main window 2000-03-09 to 2026-09-29, cost m = 2, $100,000 liquidated after tax with the Turkish progressive
engine. Wall clock of one full run: 42 s (two runs gave byte-identical RESULTS.json).

## Verdict

1. **K's timing is not explained by a shifted-timing null.** Shifting K's target series by 1,000 random
   circular offsets (at least 252 sessions from zero) puts K's CAGR at the 99.6th percentile (null median
   2.98 %, K 15.76 %) and its Sharpe at the 100th percentile (K 0.68, null median 0.28).
2. **The pre/post-2020 gate passes for K and R2 against both E2 and the S&P buy-and-hold.** Each
   after-tax delta is positive in both halves. Constant 2x and the vote-only 2x arm fail.
3. **The statistical tests do not separate K from its nearest alternatives.** SPA with K as benchmark
   gives consistent p = 0.58: no alternative beats K. MCS at alpha 0.10 keeps K with A1u, a, b, c and d
   and eliminates R2 (p = 0.074) and H2 (p = 0.086). The block-20 sensitivity eliminates the same two.
4. **In the ablation, the vol target carries most of the return and the vote carries most of the
   drawdown control.** Vol target without vote gives 15.6 % pre-tax with a 78 % max DD; E2 (vote on top)
   gives 14.7 % with a 50 % max DD. The after-tax benefit of the vote is window-dependent (see below).
5. **Expanding-window selection of the vol target and trend windows does not beat fixed K.** It gives up
   0.84 to 0.93 pp of after-tax CAGR in each of the three windows (and 1.1 pp pre-tax). This is consistent
   with the Cederburg critique for this grid.

No production configuration changes. These are diagnostics; no candidate is promoted.

## Control (harness)

`python -m research.gates_v2.screen --harness` reproduces band_h2_combo_v1 within 0.01 pp for K
(pre-tax 15.765 % base, max DD 46.67 %; calibrated 15.18 %; after tax 10.75 / 15.24 / 23.97 % on the
2000 / 2010 / 2017 windows) and for R2 (= arm C0: 15.48 %, after tax 10.16 / 14.71 / 22.89 %).
`research.fast_engine.parity --quick` PASS. The build of the target series is identical to
`fe_data.main_inputs`, and the parametric E2 vote and vol-target functions are bit-identical to the
production ones at the default windows.

## Item 28: circular-lag null and block length

| statistic | K | null median | null 5 % | null 95 % | K percentile |
|---|---|---|---|---|---|
| CAGR (pre-tax, m = 2) | 15.76 % | 2.98 % | -3.26 % | 11.55 % | **99.6** |
| Sharpe (daily, ann.) | 0.684 | 0.278 | 0.137 | 0.484 | **100.0** |

Seed 20261027, 1,000 shifts, offsets drawn uniformly from [252, L - 252].

Stationary-bootstrap block length by Politis-White with the Patton-Politis-White correction
(`arch` 8.0.0 `optimal_block_length`) on K's daily after-cost returns: stationary **1.34**, circular 1.53.
The pre-registered rule rounds the stationary value to block **1** (iid); the fixed block used so far is
20. Block-20 sensitivity is reported in item 33.

## Item 29: ablation

Pre-tax CAGR and max DD on the main window (base financing; calibrated pre-tax CAGR in the next column).
After-tax columns are $100k liquidated CAGR for the three windows; "DD" is the after-tax max DD on the
2000 window.

| arm | pre-tax CAGR | max DD | pre-tax CAGR (cal.) | sells / yr | after tax 2000 | after tax 2010 | after tax 2017 | DD (after tax, 2000) |
|---|---|---|---|---|---|---|---|---|
| S&P buy-and-hold (SPY TR) | 8.58 % | 55.2 % | n/a | 0 | 7.19 % | 11.27 % | 12.18 % | 55.2 % |
| a: constant 2x (0.98 always, rule C0) | 6.06 % | 98.6 % | 5.29 % | 1.6 | 5.04 % | 27.93 % | 28.59 % | 98.6 % |
| b: 2x on vote, no vol target (C0) | 12.13 % | 81.4 % | 11.53 % | 3.9 | 8.11 % | 16.05 % | 27.01 % | 81.4 % |
| c: vol target, always in (C0) | 15.60 % | 78.5 % | 14.90 % | 48.2 | 9.76 % | 19.70 % | 21.90 % | 78.5 % |
| d: E2 (C0) | 14.67 % | 49.6 % | 14.10 % | 27.4 | 9.53 % | 14.50 % | 22.69 % | 49.6 % |
| e: R2 (C0) | 15.48 % | 46.8 % | 14.90 % | 29.6 | 10.16 % | 14.71 % | 22.89 % | 46.8 % |
| f: K | **15.76 %** | **46.7 %** | 15.18 % | 10.2 | **10.75 %** | **15.24 %** | **23.97 %** | 46.7 % |

Reading the steps (pre-tax CAGR): leverage with no signal (a) gives 6.1 %. Adding the vote (b) adds 6.1 pp,
but leaves a 81 % DD. The vol target without vote (c) adds 9.5 pp over (a), with 78 % DD. The vote on top of
the vol target (d vs c) costs 0.9 pp of CAGR and cuts the DD by 29 pp. The early re-entry (e vs d) adds 0.8 pp
and K's execution layer (f vs e) adds 0.3 pp pre-tax and 0.6 pp after tax in the 2000 window. Most of the
CAGR comes from leverage combined with the vol target; the vote mainly controls drawdown.

After tax the vote is window-dependent: c beats d in the 2000 window (+0.23 pp) and the 2010 window (+5.2 pp),
and d beats c in the 2017 window (+0.8 pp). Read the window columns with that in mind; they are
single-path windows.

## Item 30: pre/post-2020 gate

After-tax CAGR at $100k liquidated for each half, each half its own account.

| arm | pre-2020 (2000-03-09 to 2019-12-31) | post-2020 (2020-01-01 to 2026-09-29) |
|---|---|---|
| S&P buy-and-hold | 5.12 % | 12.79 % |
| a: constant 2x | -1.73 % | 25.13 % |
| b: 2x on vote | 2.06 % | 25.57 % |
| c: vol target always in | 6.55 % | 19.69 % |
| d: E2 | 5.74 % | 21.28 % |
| e: R2 | 6.49 % | 21.55 % |
| f: K | 6.81 % | 22.89 % |

Deltas in percentage points (gate = positive in both halves):

| arm | vs E2 pre / post | gate vs E2 | vs S&P pre / post | gate vs S&P |
|---|---|---|---|---|
| a: constant 2x | -7.47 / +3.85 | FAIL | -6.85 / +12.33 | FAIL |
| b: 2x on vote | -3.68 / +4.29 | FAIL | -3.05 / +12.78 | FAIL |
| c: vol target always in | +0.81 / -1.59 | FAIL | +1.43 / +6.89 | PASS |
| d: E2 (reference) | 0 / 0 | n/a | +0.63 / +8.49 | PASS |
| e: R2 | +0.75 / +0.27 | **PASS** | +1.38 / +8.75 | **PASS** |
| f: K | +1.07 / +1.62 | **PASS** | +1.70 / +10.10 | **PASS** |

## Item 33: SPA and MCS

`arch` 8.0.0; stationary bootstrap, 2,000 reps, studentised, seed 20261027. Benchmark: K, daily pre-tax
after-cost returns. Models: a, b, c, d, e (R2), A1u and H2 (band_h2 arms under the R2 target).

| test | block | result |
|---|---|---|
| SPA (lower / consistent / upper p) | 1 (PW) | 0.50 / **0.58** / 0.58: no model beats K |
| SPA sensitivity | 20 | 0.46 / 0.54 / 0.54 |
| MCS, alpha 0.10, method R | 1 (PW) | included: K, A1u, a, b, c, d; eliminated: e (R2, p 0.074), H2 (p 0.086) |
| MCS sensitivity | 20 | same included set; eliminated: R2 (0.091), H2 (0.091) |

Power check (benchmark K; model = K + delta + sd * z, sd = daily sd of (R2 - K) = 3.9 bp; 200 replications;
detection = SPA consistent p <= 0.10):

| delta per day | 0 (size) | 0.5 bp | 1 bp | 2 bp |
|---|---|---|---|---|
| detection | 10.5 % | 100 % | 100 % | 100 % |

Caveat: the power check uses i.i.d. noise. The real differences between K and its alternatives are
clustered and carry larger variance (for example the constant-2x arm), so the detection rates overstate
power for the real arms. The SPA and MCS results above are the ones to read.

## Item 34: expanding window (Cederburg check)

Grid: vol target {0.25, 0.30, 0.35, 0.40, 0.45} x (TSM, DON) in {(252,126), (189,126), (252,63)}. Each
year from 2005 uses the configuration with the largest past pre-tax after-cost Sharpe; 2000 to 2004 use
fixed K. Selections: (0.35, 252/126) 9 years, (0.35, 252/63) 9 years, (0.30, 252/63) 2, (0.30, 252/126) 1,
(0.25, 252/63) 1; 7 configuration changes.

| series | pre-tax CAGR | after tax 2000 | after tax 2010 | after tax 2017 |
|---|---|---|---|---|
| fixed K | 15.76 % | 10.75 % | 15.24 % | 23.97 % |
| expanding window | 14.65 % | 9.91 % | 14.31 % | 23.04 % |
| difference (pp) | -1.12 | -0.84 | -0.92 | -0.93 |

Expanding-window selection does not beat fixed K in any window.

## Gates (as preregistered)

- Item 30 gate: passes for K and R2 vs E2 and vs the S&P benchmark; fails for constant 2x and the
  vote-only 2x arm; the vol-target-always-in arm passes only against the S&P benchmark.
- SPA/MCS significance at 0.10: no model significantly beats K; R2 and H2 are eliminated by MCS.
- Item 34: "expanding beats fixed" requires higher after-tax CAGR in all three windows. Not met.

## Caveats

- Circular shifts test alignment of the signal with returns. A high percentile says the timing matters; it
  does not separate skill from trend persistence.
- SPA/MCS use pre-tax after-cost returns (taxes are not in the daily series). The block-1 choice follows
  the preregistered PW rule and assumes i.i.d. resampling; the block-20 sensitivity gives the same decisions.
- The S&P benchmark is SPY total return (includes its expense ratio). The engine models no dividend tax,
  so the S&P after-tax figures are overstated relative to a Turkish dividend withholding regime. This makes
  the gate against the S&P conservative for K.
- Halves and tax windows are single paths of one history. Window results differ widely across arms and
  windows (for example constant 2x: 5.0 % in the 2000 window vs 27.9 % in the 2010 window) and should not
  be read as stable estimates.
- Amendments: A1 (SPA takes losses; first-run SPA and power numbers discarded) and A2 (block-20
  sensitivity added after the PW value was seen). See PREREGISTRATION.md.
- Restored inputs (engine, closeout data) are not committed on this branch; their SHA-256 values are in
  `research/gates_v2/DATA_MANIFEST.json`.
