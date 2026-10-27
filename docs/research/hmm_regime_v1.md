# hmm_regime_v1: causal two-state HMM regime overlay on K after Turkish tax (audit item 52)

Branch `research/hmm_regime_v1`. Pre-registration `research/hmm_regime_v1/PREREGISTRATION.md` (commit `ac312b63`, before any non-control arm was simulated). Driver `research/hmm_regime_v1/screen.py`, kernels `research/hmm_regime_v1/hmm.py`, outputs `RESULTS.json` and `SUMMARY.txt`. Two runs gave byte-identical RESULTS.json (sha256 `319dbfc034f0476328b1e66cd6835ce375469f0adc7b2e8ffe9770c35432df7f`). Wall clock of one run: 6.8 s.

## Verdict

**No HMM arm beats K. No arm passes the gates; K stays in production.**

1. **All three arms lose to K after tax in every window.** The best arm, H3 (the H2 gate as an exit inside vote-on episodes), is -1.9 / -3.1 / -6.8 pp below K at $100k in the 2000 / 2010 / 2017 windows. H1 is -2.4 / -3.6 / -7.9 pp. H2 is -1.9 / -2.8 / -6.1 pp.
2. **The overlay cuts drawdown but pays much more in return.** H1 cuts max DD from 46.7 % to 38.4 %, but pre-tax CAGR falls 2.3 pp (15.8 → 13.5 %). Calmar rises only from 0.338 to 0.352, short of the 1.1 × K bar, so G1 fails.
3. **The loss is concentrated after 2020.** After-tax Δ vs K is -0.4 / +0.3 / +0.6 pp pre-2020 for H1 / H2 / H3, but -8.4 / -8.5 / -9.3 pp post-2020. The turbulent state covers about 31 % of main-window days and the gate is on for 27 % of days (73 episodes), so it removes exposure during recoveries as well as crashes.
4. **Out of sample it is worse, not better.** The median OOS CAGR Δ (calibrated, six windows) is -2.1 / -2.4 / -2.2 pp for H1 / H2 / H3 (G7 fails on the median and on the count of windows with ΔCAGR ≥ -0.5 pp). Part of this is the 10-year warm-up in each OOS window (the arms run as K until the first refit).
5. **The bootstrap is the one favourable signal, and it does not carry.** Pre-tax bootstrap median Δ is +0.7 / +1.3 / +0.9 pp (G5 passes), and B1/B2 medians are positive for H2 and H3. But the bootstrap pool is QQQ 2015–2026 and it uses the final fitted parameters (see "Bootstrap decision"), so it is in-sample for the state definition. B1 and B2 medians are also positive for all three arms (share above 0.5). It cannot outweigh a -2 to -8 pp after-tax loss in the main window.

Consistent with `vol_regime_leverage_v1`: K's own 0.35 / vol20 target already scales exposure in high-volatility periods, and a second, regime-based scale-down double-counts that signal and cuts exposure at the wrong times.

Not a production change. The study is a rejection, not a candidate.

## Arms (fixed before running)

All arms use K's execution layer (rule `K`: A1u gain band, H2 December deferral, 0.5 pp band, 0.98 cap). Only the target differs. P is the causal filtered probability of the turbulent state.

- **H1:** target = K × (1 − P).
- **H2:** target = 0 while the gate G is on. G turns on when P > 0.8 at two consecutive closes and off when P < 0.5. Otherwise K.
- **H3:** target = K × (1 − L). The latch L is on only while the E2 vote is on and G is on, and it was already on or the vote and H3 position were held at the previous close. L clears when the vote or G turns off.
- Controls: K (production) and R2 (reference, not in the gates).

## HMM specification (as pre-registered)

- Two-state Gaussian HMM on daily log returns, states ordered by sd (calm = 0, turbulent = 1), numba Baum-Welch from a fixed start, at most 1,000 iterations. No hmmlearn: the research venv was not modified.
- Observations: ^IXIC log returns from 1990-01-02 to 1999-03-09 (the prefix device of `vol_regime_leverage_v1`), then QQQ log returns from 1999-03-11.
- **Yearly expanding refit** at the first session of each year from 2000 (2000-01-03 first), fitted on all observations before that session. The filter runs forward with the year's parameters. Probabilities are filtered, never smoothed.
- Warm-up before 2000-01-03: K's weight in every arm.

Fitted parameters (main path): calm sd 0.73 → 0.91 %/day (2000 → 2026), turbulent sd 1.85 → 2.59 %/day, turbulent mean about -0.12 %/day late in the sample, persistence about 0.99 (calm) and 0.98 (turbulent). All 27 fits converged in 16–24 iterations.

## Bootstrap decision

Pre-registered before running: **the main path's final yearly fit (refit 2026-01-02, data through 2025-12-31) is applied to every resampled path by causal forward filtering from the stationary distribution; no refit within a path.** Reason: a synthetic 10-year path has at most nine years of history, too short for a stable two-state fit, and per-path refits (27 × 6,000 paths) would be dominated by warm-up. Caveat: those parameters were estimated on the same history the bootstrap resamples, so the bootstrap evidence is optimistic.

## Results (main window 2000-03-09 → 2026-09-29, m = 2; after tax = $100k liquidated, progressive)

| arm | pre CAGR base / cal | max DD | Calmar | sells / yr (pre) | after tax 2000 / 2010 / 2017 | Δ vs K 2000 / 2010 / 2017 (pp) | pre-2020 / post-2020 Δ AT (pp) | pre bootstrap Δ base (share > 0) | B1 Δ (share) | B2 Δ (share) | DSR pre / AT (N = 372) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| K (control) | 15.76 / 15.18 % | 46.7 % | 0.338 | 10.2 | 10.75 / 15.24 / 23.97 % | 0 | 0 / 0 | 0 | 0 | 0 | 0.709 / 0.422 |
| R2 (reference) | 15.48 / 14.90 % | 46.8 % | 0.331 | 29.6 | 10.16 / 14.71 / 22.89 % | -0.59 / -0.53 / -1.09 | -0.32 / -1.34 | +0.01 (0.51) | -0.29 (0.25) | -0.13 (0.36) | 0.699 / 0.384 |
| H1 | 13.50 / 13.00 % | 38.4 % | 0.352 | 19.7 | 8.34 / 11.62 / 16.08 % | -2.42 / -3.62 / -7.89 | -0.40 / -8.41 | +0.70 (0.56) | +0.14 (0.52) | +0.74 (0.56) | 0.680 / 0.300 |
| H2 | 14.55 / 14.01 % | 42.8 % | 0.340 | 10.2 | 8.87 / 12.42 / 17.88 % | -1.89 / -2.82 / -6.09 | +0.34 / -8.46 | +1.32 (0.61) | +0.71 (0.57) | +1.14 (0.61) | 0.699 / 0.318 |
| H3 | 14.62 / 14.08 % | 41.0 % | 0.356 | 11.8 | 8.85 / 12.12 / 17.18 % | -1.91 / -3.11 / -6.79 | +0.63 / -9.30 | +0.87 (0.57) | +0.36 (0.54) | +0.80 (0.58) | 0.697 / 0.313 |

Sells per year after tax (2000 / 2010 / 2017 windows): K 10.7 / 10.6 / 9.5; H1 20.3 / 21.0 / 24.3; H2 10.7 / 11.3 / 12.1; H3 12.3 / 12.4 / 12.9.

Pre-tax CAGR to 2017-end: K 33.2 %, H1 25.2 %, H2 29.2 %, H3 28.1 %.

OOS, calibrated, m = 2 (CAGR, K / H1 / H2 / H3; first refit in brackets):

| window | K | H1 | H2 | H3 |
|---|---|---|---|---|
| GSPC 1957–1999 [1964] | 9.97 % | 5.57 % | 4.33 % | 4.78 % |
| M1 ^IXIC 1975–1999 [1981] | 21.27 % | 14.40 % | 14.56 % | 14.56 % |
| M2 ^GSPC 1935–1956 [1944] | 11.73 % | 10.98 % | 11.67 % | 11.75 % |
| M3 1966– [1975] | 6.46 % | 4.00 % | 3.71 % | 3.56 % |
| M4 1989– [1997] | 9.71 % | 7.88 % | 7.64 % | 8.15 % |
| M5 1985– [1994] | -0.62 % | -0.22 % | -0.26 % | -0.12 % |

Median OOS CAGR Δ: H1 -2.14, H2 -2.40, H3 -2.23 pp. Reduced G8 medians: -2.78 / -2.40 / -2.31 pp.

Main-window diagnostics: P > 0.5 on 30.7 % of days; gate on for 27.3 % of days (73 episodes); H1 averages 0.89 × K's weight on vote-on days. Bootstrap paths spend 28.5 % of days with the gate on.

## Gates

| arm | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 | A1 | A2 | A3 | A4 | candidate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 | fail | pass | fail | fail | pass | fail | fail | fail | pass | fail | fail | pass | fail | no |
| H2 | fail | pass | fail | fail | pass | fail | fail | fail | pass | fail | pass | pass | fail | no |
| H3 | fail | pass | fail | pass | pass | fail | fail | fail | pass | fail | fail | pass | fail | no |

G1 CAGR or Calmar (both financings), G2 max DD, G3 2017-end CAGR, G4 sub-periods (base), G5 pre-tax bootstrap, G6 G1 at m = 1, G7 six OOS windows, G8 reduced pooled OOS bootstrap, G9 turnover ≤ 2 × K, A1 after-tax above K in all three windows, A2 B1 median ≥ 0 and share ≥ 55 %, A3 B2 likewise, A4 both halves positive.

The gate that binds for all three arms is A1, after-tax CAGR above K in all windows. The arms' DD improvement is real (G2 passes), but G1 and A1 fail on return.

## Deflated Sharpe

N = 369 + 3 = 372 trials, as specified (369 from the vol_regime count, plus the three arms here). Bar 0.95. K: 0.709 pre-tax, 0.422 after tax. No arm approaches the bar: H1 0.680 / 0.300, H2 0.699 / 0.318, H3 0.697 / 0.313. The K sanity check reproduces gates_v1's value (DSR at N = 365 = 0.7111).

## Verification

- `--harness` PASS: K and R2 reproduce the reference anchors to under 0.0001 pp (pre-tax base, after tax in three windows, calibrated); the bootstrap K-weight identity; the parity check `research.fast_engine.parity --quick` PASS.
- Kernel checks (synthetic data only, before the first real run): Baum-Welch recovers the generating parameters; the filter is causal (output unchanged when future observations are altered); the arm kernel matches an explicit per-close reference loop; the expanding schedule is causal and NaN before the first refit.
- Two runs gave byte-identical RESULTS.json.

## Deviations, amendments and anomalies

- **No amendments** to the pre-registration.
- **Unexplained first-run artefact.** The first `--run` copy I saved (taken at 09:06:03, after the first run) was a 26 KB RESULTS.json with keys (`exposure`, `pre_tax_halves_cagr`, `S1`–`S3`) that this code never writes, and grep finds no such keys in the worktree. The run wrote its SUMMARY (matching the numbers above) and was then overwritten by the second run at 09:06:10. I could not trace what wrote the 26 KB file. Runs 2 and 3 (from the same code and the same pre-registration commit) are byte-identical, and the reported results come from them. Treat the first run as unverified.
- **"Sealed window".** The orchestrator's brief names one, but neither WORKER_PROTOCOL.md nor the research docs define it, so none is applied.
- **Commit trailer.** The commit trailer is `Co-Authored-By: Claude Haiku 5.5`, as the orchestrator instructed; the protocol text says Opus 5.5.

## Caveats

- **Post-2020 is the main failure mode.** The state definition fitted through 2000–2019 flags recoveries as turbulent, and the cost concentrates in 2020–2026. This study does not test whether a different state count, an asymmetric threshold or a longer hysteresis would fix it; those would be new arms and not part of this pre-registration.
- **The bootstrap is optimistic.** Its parameters are the main path's final fit, and its pool is 2015–2026, which is the period where H2 and H3 look best. The bootstrap cannot show out-of-sample estimation error.
- **OOS windows are shorter in practice.** The 10-year lead means each OOS window's first 6–10 years run as K, so G7 and G8 are judged on a shorter, different window.
- **One path.** The after-tax windows overlap heavily (2000 and 2010 are the same history), as in vol_regime.
- **Data.** ^IXIC is a price index and QQQ is a price series (no dividends in the HMM observations), and the splice drops one return on 1999-03-10. The state probabilities depend only on price returns, and K's trades still use the engine's own dividend and financing treatment.

## Commits

- `ac312b63` pre-registration, causal HMM kernels, screen and restored inputs (before any non-control real-data run).
- The results commit: RESULTS.json, SUMMARY.txt and this report.
