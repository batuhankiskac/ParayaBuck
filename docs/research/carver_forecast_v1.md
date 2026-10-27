# carver_forecast_v1: a continuous trend forecast does not beat K after tax

Branch `research/carver_forecast_v1`. Code `research/carver_forecast_v1/screen.py`, preregistration
`research/carver_forecast_v1/PREREGISTRATION.md`, outputs `RESULTS.json` and `SUMMARY.txt` (same directory).
Engine: `research/fast_engine` (`fe_sim.simulate`, `fe_tax`, `fe_boot.boot`). Main window 2000-03-09 to
2026-09-29, m = 2, $100,000 liquidated, progressive Turkish tax. Wall clock 22.0 s per run; two runs gave
byte-identical `RESULTS.json`.

## Verdict

**No.** Under the preregistered parameters, none of the four continuous-forecast arms beats K after Turkish tax.
Every arm is worse than K in all three after-tax windows (by 3.3 to 16.4 pp/yr across the 2000, 2010 and 2017
windows), fails both clean bootstraps, and is negative in both the pre-2020 and post-2020 halves. The only thing the
forecast arms do better is drawdown: max DD 19 to 35 % against K's 46.7 % (C1 19 %, C2 to C4 31 to 35 %), which comes
with 4.6 to 10.1 pp less pre-tax CAGR. Nothing is promoted; K stays.

Two features of the design make this a weak test of the signal, see Caveats: the arms hold much less exposure than K,
and they trade about four times as many times (taxable sells) per year.

## Arms (all fill rule K, decisions at the close, fills at the next open)

- Raw signals per QQQ close: close/SMA200 − 1; 252-session return; 126-session channel position in [−1, 1].
- Each forecast = raw / expanding mean |raw| × 10, capped at ±20 (minimum 20 valid observations); combined = mean
  of three, floored at 0.
- **C1** weight = min(0.98, combined/20 × min(1, 0.35/vol20)).
- **C2** weight = min(0.98, min(1, combined/10) × vt).
- **C3** = C2 on a span-5 EWMA of the forecast.
- **C4** = C2 while combined > 0; otherwise the R2 early re-entry at 0.5 × vt.
- Control **K** and reference **R2** are the production targets (unchanged).

## Pre-tax (main window, m = 2)

| arm | CAGR base | CAGR cal. | max DD | turnover /yr | QLD sells /yr | mean target weight |
|---|---:|---:|---:|---:|---:|---:|
| K (control) | 15.76 % | 15.18 % | 46.7 % | 5.46 | 10.2 | 0.67 |
| R2 (reference) | 15.48 % | 14.90 % | 46.8 % | 5.99 | 29.6 | 0.67 (same target as K) |
| C1 | 5.62 % | 5.38 % | 19.4 % | 5.69 | 41.8 | 0.31 |
| C2 | 9.89 % | 9.46 % | 35.1 % | 8.47 | 38.7 | 0.52 |
| C3 | 11.19 % | 10.75 % | 32.0 % | 4.00 | 35.8 | 0.53 |
| C4 | 10.51 % | 10.07 % | 31.4 % | 9.09 | 39.5 | 0.53 |

K's pre-tax figures reproduce gates_v2 and the production run to 0.01 pp (`--harness` PASS).

## After tax ($100k liquidated, progressive)

| arm | 2000 window Δ vs K | 2010 Δ | 2017 Δ | sells /yr (all) | pre-2020 Δ | post-2020 Δ | B1 median Δ (share > 0) | B2 median Δ (share > 0) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| K (control) | — (10.75 % / 15.24 % / 23.97 % CAGR) | — | — | 10.7 | — (6.81 % / 22.89 %) | — | — | — |
| C1 | −7.68 pp | −10.50 pp | −16.37 pp | 41.8 | −5.52 | −14.46 | −2.54 pp (30 %) | −2.53 pp (30 %) |
| C2 | −4.65 pp | −5.47 pp | −7.92 pp | 38.9 | −4.46 | −5.16 | −1.71 pp (30 %) | −1.65 pp (30 %) |
| C3 | −3.32 pp | −4.01 pp | −5.67 pp | 36.1 | −3.08 | −3.69 | −0.50 pp (45 %) | −0.64 pp (43 %) |
| C4 | −4.17 pp | −5.23 pp | −7.59 pp | 39.7 | −3.95 | −4.68 | −1.41 pp (34 %) | −1.34 pp (34 %) |

R2 after tax for reference (2000 / 2010 / 2017): 10.16 % / 14.71 % / 22.89 %.

## Gates (vs K)

| gate | C1 | C2 | C3 | C4 |
|---|---|---|---|---|
| G1 CAGR / Calmar (m = 2, both) | fail | fail | fail | fail |
| G2 max DD ≤ K + 1 pp | pass | pass | pass | pass |
| G3 2017-end CAGR ≥ K − 1 pp | fail (13.1 vs 33.2 %) | fail (23.7) | fail (25.7) | fail (24.1) |
| G4 CAGR above K in ≥ 2 of 3 sub-periods | fail (0 of 3) | fail (0 of 3) | fail (0 of 3) | fail (0 of 3) |
| G5 pre-tax bootstrap median Δ (base / cal) | −4.13 / −3.90 pp | −2.56 / −2.42 | −1.28 / −1.15 | −2.01 / −1.90 |
| G6 G1 at m = 1 | fail | fail | fail | fail |
| G7 six OOS windows, calibrated (median Δ, Δ ≥ −0.5 in ≥ 5, DD) | fail (median −5.2 pp) | fail (−1.8) | fail (−3.0) | fail (−1.7) |
| G8 pooled OOS bootstrap median Δ | fail (−4.10 pp) | fail (−1.38) | fail (−1.75) | fail (−1.45) |
| G9 turnover ≤ 2 × K | pass | pass | pass | pass |
| After tax: beats K in all three windows | fail | fail | fail | fail |
| After tax: B1 and B2 median Δ ≥ 0, share ≥ 55 % | fail | fail | fail | fail |
| Reported: pre-2020 and post-2020 Δ both > 0 | fail | fail | fail | fail |

Every arm fails every return gate; only the drawdown (G2) and turnover (G9) gates pass.

## Deflated Sharpe (N = 369 trials, trial-variance floor 1/(T−1), so an upper bound on DSR)

K 0.71 (R2 0.70); C1 0.33; C2 0.47; C3 0.56; C4 0.51. None reaches a conventional 0.95 level, and K itself does not
under this deflation.

## Caveats

1. **Exposure is not matched.** Mean target weight is 0.31 to 0.53 for the forecast arms against 0.67 for K. The
   preregistered scale (a forecast of 10 gives full weight; a typical forecast magnitude is 5 to 8) leaves the arms
   under-invested. Part of the after-tax gap is exposure, not signal quality. A forecast-versus-vote comparison at
   equal average exposure would need a new preregistration; it was not run here.
2. **Churn is taxed.** Sells per year rise from about 10 (K) to 36 to 42. The continuous weights move past K's 0.5 pp
   and gain-band thresholds often, so the arms realise many small taxable trims. The audit already showed that vol-target
   trims are about 91 % of R2's realised gain; these arms add more of them.
3. **Bootstrap mechanics.** The forecast arms are bootstrapped by wrapping `fe_boot.chunk_legs`: the same seeded draws
   are used, and only the target weights are recomputed on each resampled price path, so the paired differences are
   clean. G8 is a vectorised numpy block resampler of the engine's daily NAV returns (block 20, 2,000 paths per market,
   seed 20261011), not an `fe_boot` mode; `fe_boot` has no OOS mode.
4. **Single path.** The three after-tax windows and the two halves are single histories, and the 2017-end and P1 sub-period
   figures for these arms are far from K's in every case.

## Amendments

- **Amendment 1 (aggregation bug, before any results were written).** The first run crashed in the gate table, which
  read the bootstrap and G8 values from the wrong dictionary. The fix (commit on this branch, "fix gate aggregation
  lookup") only changes where those values are read from. Gate definitions, arms and parameters are unchanged.
- **Amendment 2 (inputs).** `parity --quick` needs the instrument CSVs `range_vol_v1_*` and `range_vol_v2_*`, which the
  gates_v2 restore did not include. They were restored from `archive/research/range-vol-v2-2026-10` and hashed in
  `DATA_MANIFEST.json`. No gate or result depends on them beyond the parity check.

## Commits

- `aa339558` restore gates_v2 harness, fast engine, K harness, gates_v1 stats and inputs.
- `d8a2c58a` preregistration, screen and data manifest (before non-control runs).
- Gate-lookup fix, results (`RESULTS.json`, `SUMMARY.txt`) and this report: see the branch head.
