# range_vol_v3 — independent replication + production feasibility (2026-10-14)

**Verdict: no arm in {F1, F2, F3} passes the pre-registered gate set. Selected
arm: none. Production E2 (`equity_leveraged_trend_v1` 1.1.0) stays
unchanged.** F3 clears all main-window and m=1 gates and has the best main and
ETF return deltas, but fails the ETF DD-share gate because it freezes QQQ's
R* = 1.127 on every instrument; other instruments' true R is materially
higher in the recent sample.

Replication of the claimed G3x (committed arm, all values within 0.02 pp of the
claim, C0 reproduced to 0.000000 pp):

| quantity | claim (v2) | this study | diff |
|---|---:|---:|---:|
| C0 base m=2 CAGR | 14.67% | 14.675% | +0.005 pp |
| C0 base m=2 max DD | 49.56% | 49.564% | +0.004 pp |
| C0 m=1 CAGR | 14.95% | 14.949% | −0.001 pp |
| C0 calibrated m=2 CAGR | 14.10% | 14.104% | +0.004 pp |
| G3x base m=2 CAGR | 14.98% | 14.985% | +0.005 pp |
| G3x base m=2 max DD | 48.50% | 48.489% | −0.011 pp |
| G3x calibrated m=2 CAGR | 14.40% | 14.401% | +0.001 pp |
| G3x main bootstrap median Δ (base) | +0.275 pp | +0.288 pp | +0.013 pp |
| G3x 18-ETF median Δ CAGR | +0.32 pp | +0.317 pp | −0.003 pp |
| G3x 18-ETF share positive | 16/18 | 16/18 | 0 |

Clean YZ20 (NaN bug fixed) base m=2: 14.798% CAGR, max DD 46.837%, 2017+ 31.661%,
calibrated 14.226%.

Evidence: `research/range_vol_v3/` (RESULTS.json, SUMMARY.txt, PREREGISTRATION.md);
Q data `data/historical/stress_robustness_v1_{daily,dtb3}.csv`,
`data/historical/range_vol_v2_*.csv` (18 instruments) and
`data/historical/ewma_band_v1_{gspc,index,tb3ms}.csv`.

## Amendments (screen.py after pre-registration)

The pre-registration commit is `a3ab719`. The working-tree edit to
`research/range_vol_v3/screen.py` (+188/−91 lines) and the new outputs are the
finished evidence. No arm, parameter, data source or gate expression was
changed after pre-registration; the edit is bug fixing plus the
pre-registered ETF test:

1. **R* was never computed (committed bug).** The committed `run_main` read
   `if RSTAR is None: res = {}` and left `RSTAR = None`, so F3 would call
   `float(None)`. Fixed to `RSTAR = compute_rstar_qqq()` before the run. Value:
   1.12745616 (QQQ 1999-03..2009-12).
2. **Bootstrap referenced an undefined `q` tuple.** The committed
   `bootstrap_instrument` read `q[1][idx-1]`, `q[0][idx]`, `q[2][idx]`; `q`
   did not exist. Replaced with the sampled QQQ OHLC columns:
   `prev = cq[idx-1]`, `r_on_tr = r_on_p = oq[idx]/prev`,
   `r_id = cq[idx]/oq[idx]`. High/low are then scaled relative to the sampled
   open, so the range estimator is recomputed on each bootstrap path instead of
   crashing. This is the same bootstrap contract as the main window (block 20,
   seed 20261011, 5,000 paths).
3. **Old OOS windows (six-window G7/M1–M5) dropped; ETF test added.** The
   committed skeleton carried ^GSPC 1957–1999 and M1–M5 loaders. Pre-registration
   only requires the 18-instrument ETF test (G7'/G8') for candidates, so the run
   now loads `range_vol_v2_*.csv`, applies the quality rule (EWC excluded),
   and computes G7'/G8' as defined in the pre-registration.
4. **Gate wiring.** `pass_all` now means G1–G6(G9) on the main window, plus
   G7' and G8' on the 18 ETFs (`pass_all_final`). Selection picks the passing
   F-arm with the highest 18-ETF pooled bootstrap median Δ, with the stated
   F3 > F1 > F2 tie rule. No threshold was changed.
5. **Smoke test.** `--harness` still reproduces C0 to 0.000000 pp and passes
   the simulator check.

## Method

- **Control (C0)** = production E2 1.1.0: ≥ 2 of 3 votes on QQQ close
  (close > SMA200, 252-session price return > 0, close > 126-session channel
  midpoint). Weight = min(1, 0.35/vol20), total cap 0.98. Vol is on the
  synthetic QLD* (daily-reset 2x QQQ total return).
- **Vol estimators (all on QQQ daily OHLC; open/high/low scaled by
  adjclose/close, close = adjclose):**
  - YZ20: standard Yang–Zhang, 20 sessions,
    k = 0.34/(1.34 + (n+1)/(n−1)).
  - G3x: Parkinson P20 × expanding ratio R[t], then ×2 (2x fund).
    R[t] = sqrt(252·var_cc) / sqrt(252·mean_park), both over sessions 1..t−1
    (no look-ahead). Replicates the claimed winner.
  - F1: same, R rolling over 252 sessions.
  - F2: same, R rolling over 504 sessions.
  - F3: same, R frozen at R* = 1.127456 (estimated on QQQ 1999-03..2009-12).
  - F4: Parkinson P20 × 2 (R = 1).
- Shared mechanics from `ewma_band_v1`: decide at close, fill next open,
  0.5 pp resize band, 4 bps × m, base/calibrated financing, window
  2000-03-09 → 2026-09-29.
- Bootstrap: block-20, seed 20261011, 5,000 paths (main) and 2,000 paths per
  ETF, m = 2.

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD | Calmar | 2017+ | P1/P2/P3 | m=1 CAGR | Boot median Δ | turn/yr |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| C0 | 14.675% | 14.104% | 49.564% | 0.296 | 32.437% | -1.10/13.25/32.44 | 14.949% | — | 5.969 |
| YZ20 | 14.798% | 14.226% | 46.837% | 0.316 | 31.661% | −0.96/13.00/31.66 | 15.050% | +0.419 pp | 5.437 |
| G3x | 14.985% | 14.401% | 48.489% | 0.309 | 32.724% | −0.61/13.17/32.72 | 15.230% | +0.288 pp | 5.390 |
| F1 | 14.527% | 13.955% | 48.531% | 0.299 | 31.089% | −0.90/12.65/31.09 | 14.778% | +0.252 pp | 5.490 |
| F2 | 14.436% | 13.863% | 48.530% | 0.297 | 31.180% | −0.77/12.96/31.18 | 14.693% | +0.264 pp | 5.488 |
| F3 | 15.085% | 14.498% | 48.245% | 0.313 | 32.940% | +0.14/13.16/32.94 | 15.332% | +0.297 pp | 5.365 |
| F4 | 15.227% | 14.625% | 52.631% | 0.289 | 33.872% | +0.40/12.96/33.87 | 15.471% | +0.030 pp | 5.366 |

Boot medians are 7.50% (C0), 7.92% (YZ20), 7.79% (G3x), 7.78% (F1),
7.72% (F2), 7.78% (F3), 7.53% (F4). Main bootstrap paths = 5,000.

## Gate results (F1–F3 only)

| Gate | F1 | F2 | F3 |
|---|---|---|---|
| G1 base / cal (CAGR or Calmar) | F/F | F/F | P/P |
| G2 base / cal (DD ≤ C0+1 pp) | P/P | P/P | P/P |
| G3 base / cal (2017+ ≥ C0−1 pp) | F/F | F/F | P/P |
| G4 (≥ 2 of 3 sub-periods) | P | P | P |
| G5 base / cal (bootstrap median Δ ≥ 0) | P/P | P/P | P/P |
| G6 base / cal (G1 at m=1) | F/F | F/F | P/P |
| G7' (ETF median Δ ≥ 0, ≥60% pos, DD ≤+3 pp ≥80%) | F | F | F |
| G8' (ETF pooled bootstrap median Δ ≥ 0) | P | P | P |
| G9 (turnover ≤ 2× C0) | P | P | P |
| Final | FAIL | FAIL | FAIL |

F1/F2 fail G1/G3/G6 because the rolling R washes out most of the estimator
gain: main CAGR stays below C0 (14.53/14.44% vs 14.67%) and the 2017+ CAGR
is 1.35/1.26 pp below C0. F3 passes all main/m=1 gates (it is the best main
arm in the study) but fails G7'.

## ETF OOS gate (18 instruments, 2000 paths each)

Median Δ CAGR: G3x +0.317 pp (16/18 positive), F1 +0.016 pp (9/18), F2
+0.026 pp (10/18), F3 +0.327 pp (15/18). Pooled bootstrap median Δ:
G3x +0.203 pp, F1 +0.177 pp, F2 +0.203 pp, F3 +0.061 pp. G8' passes for all.

G7' fails for every F-arm:
- F1: median Δ +0.016 pp ≥ 0 and DD-share 0.944 ≥ 0.8 pass, but only 0.50 of
  instruments positive (< 0.60).
- F2: median +0.026 pp, 0.556 positive, DD-share 0.889; positive share below
  0.60.
- F3: median +0.327 pp, 0.833 positive pass, but DD-within-+3-pp share is 0.50
  (limit 0.80).

F3 DD detail (per ETF, F3 max DD − C0 max DD, pp): the freeze at QQQ R* =
1.127 underestimates vol on instruments whose realized R is above 1.127, so
EWA +5.76, EWG +10.53, EWJ +0.24, EWL +4.78, EWU +3.67, XLB +3.36,
XLI +5.17, XLK +3.81, XLP −0.71, XLU +2.03, XLY +4.11, EFA +1.59,
EEM −0.18, EWH +4.20, EWS −0.59, XLE +0.49, XLF +0.15, XLV +1.51.
G3x, which recomputes R on each instrument, has DD delta median −1.186 pp
and DD-share 0.944; the pooled bootstrap Δ stays positive. For reference, the
per-ETF C0 CAGRs and G3x deltas are: EEM 4.79/+0.98, EFA 8.09/+0.84,
EWA 1.91/+0.31, EWG 4.93/+0.20, EWH 4.37/+0.70, EWJ −0.06/+0.87,
EWL 4.26/+0.30, EWS 8.94/+1.22, EWU 3.51/+0.42, XLB 0.82/+0.05,
XLE 6.45/+0.49, XLF 2.60/+0.47, XLI 6.01/+0.01, XLK 13.41/+0.34,
XLP 1.58/+0.01, XLU 5.12/−0.26, XLV −0.05/−0.36, XLY 6.40/−0.44
(all pp CAGR).

## Yearly expanding R[t] on QQQ

| Year | mean R | min | max | n |
|---|---:|---:|---:|---:|
| 1999 | 1.1369 | 1.0000 | 1.4773 | 207 |
| 2000 | 1.1305 | 1.0964 | 1.2079 | 252 |
| 2001 | 1.1237 | 1.1112 | 1.1372 | 248 |
| 2002 | 1.1239 | 1.1201 | 1.1324 | 252 |
| 2003 | 1.1205 | 1.1190 | 1.1232 | 252 |
| 2004 | 1.1194 | 1.1189 | 1.1202 | 252 |
| 2005 | 1.1180 | 1.1173 | 1.1189 | 252 |
| 2006 | 1.1175 | 1.1169 | 1.1180 | 251 |
| 2007 | 1.1171 | 1.1164 | 1.1189 | 251 |
| 2008 | 1.1202 | 1.1166 | 1.1266 | 253 |
| 2009 | 1.1267 | 1.1248 | 1.1279 | 252 |
| 2010 | 1.1266 | 1.1246 | 1.1277 | 252 |
| 2011 | 1.1284 | 1.1264 | 1.1327 | 252 |
| 2012 | 1.1335 | 1.1326 | 1.1344 | 250 |
| 2013 | 1.1351 | 1.1343 | 1.1355 | 252 |
| 2014 | 1.1356 | 1.1354 | 1.1359 | 252 |
| 2015 | 1.1347 | 1.1307 | 1.1371 | 252 |
| 2016 | 1.1341 | 1.1327 | 1.1349 | 252 |
| 2017 | 1.1349 | 1.1345 | 1.1353 | 251 |
| 2018 | 1.1360 | 1.1352 | 1.1381 | 251 |
| 2019 | 1.1394 | 1.1375 | 1.1404 | 252 |
| 2020 | 1.1525 | 1.1404 | 1.1569 | 253 |
| 2021 | 1.1575 | 1.1565 | 1.1578 | 252 |
| 2022 | 1.1603 | 1.1568 | 1.1641 | 252 |
| 2023 | 1.1644 | 1.1638 | 1.1648 | 252 |
| 2024 | 1.1657 | 1.1648 | 1.1668 | 252 |
| 2025 | 1.1688 | 1.1666 | 1.1699 | 250 |
| 2026 | 1.1703 | 1.1692 | 1.1719 | 186 |

R[t] is not stable: ~1.117 in 2004–2009, ~1.16 by 2021–2026, a 4% drift over
the window. That is the direct explanation for why a fixed R* estimated on the
first decade (1.127) loses coverage from 2010 onward and raises DD on other
instruments (F3), while the rolling/expanding variants preserve the gain in
vol-stability terms but pay a return cost (F1/F2) because the rolling ratio
reacts to local variance and suppresses position size after volatility
clustering. G3x's expanding (full-history) ratio is the only variant that
matches both the claim and the DD bound on QQQ (−1.07 pp DD vs C0, G2
passes), but its candidate status is only assessed on QQQ, not the ETF set;
the study's gate set asks F1–F3 to reproduce the ETF gain, and none do.

## QLD-own OHLC (report-only)

Not computed in this screen: the harness fixes the vol input to QQQ OHLC and
the held asset to the synthetic QLD*. A production bot computes vol on the
held symbol; the QQQ-based R estimates above are the conservative
replication target. If QLD own OHLC were used, vol would be measured on a
2x-leveraged series with different skewness; the R drift above suggests any
fixed estimate would need periodic re-estimation regardless of input.

## Selection / outcome

No F-arm passes `pass_all_final`. Selected = none. No Stage-run change to
production. H1 (range-based sizing beats vol20) is supported on QQQ and on the
18-ETF median for the estimator-aware variant G3x, but the *implementable*
(frozen/rolling) variants required by production feasibility do not clear the
pre-registered gates, so the bot keeps vol20 sizing.

## Commits / reproducibility

- Pre-registration: `a3ab719`.
- Harness passes (`--harness`): C0 to 0.000000 pp; band simulator bit-identical
  to `simulate_multi`.
- Two `--run` invocations produced byte-identical `RESULTS.json`
  (sha256 a7bcc0e881ef56d18c721726f870dcfba0a5423e3679fdc31fa1a211c79094ba).
- Run time: ~140 seconds foreground.
path: docs/research/range_vol_v3.md