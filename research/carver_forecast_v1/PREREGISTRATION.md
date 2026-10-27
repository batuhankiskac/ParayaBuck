# carver_forecast_v1: a continuous trend forecast versus K, after Turkish tax

Branch `research/carver_forecast_v1`. Code `research/carver_forecast_v1/screen.py`. Outputs `RESULTS.json`,
`SUMMARY.txt`, report `docs/research/carver_forecast_v1.md`. Built on the gates_v2 harness and the fast engine
(`research/fast_engine`, `fe_sim.simulate`, `fe_tax`, `fe_boot.boot`). Written and committed before any non-control
arm was simulated on real data.

## Question (audit item 49)

Does a Carver-style continuous trend forecast, sized as a fraction of a volatility-targeted position, beat K
after Turkish income tax (USD, $100k liquidated, progressive schedule), without materially worse drawdown?

## Control and reference

- **K (control).** Production rule exactly: target `w_r2` of the R2 vote path (E2 + early re-entry, unchanged) with
  fill rule `K` (A1u gain band + H2 December deferral + 0.5 pp band). Reproduced by `g2.harness()`.
- **R2 (reference).** Target `w_r2` with fill rule `C0` (plain 0.5 pp band).

## Raw signals (per QQQ close, sessions t)

Using the same three QQQ rules as K, each as a continuous raw value:

- r1(t) = close / SMA200(t) − 1 (valid from the 200th session).
- r2(t) = close(t) / close(t − 252) − 1 (valid from the 253rd session).
- r3(t) = 2 · (close − min126) / (max126 − min126) − 1, max/min over the last 126 sessions including t, mapped
  to [−1, 1]; r3 = 0 when max126 = min126 (valid from the 126th session).

## Forecast (no look-ahead)

- Scalar: A_i(t) = mean of |r_i(s)| over all valid s ≤ t (expanding window), defined only once it has at least
  **MIN_OBS = 20** valid observations, else undefined.
- f_i(t) = clip( r_i(t) / A_i(t) × 10, −20, +20 ).
- Combined: comb(t) = (f_1 + f_2 + f_3) / 3. Undefined (no position) until all three exist.
- Long-only floor: fpos(t) = max(comb(t), 0); undefined → 0.

Volatility: vt(t) = min(1, 0.35 / vol20(t)) on the synthetic 2x QLD close, as in K (`fe_signals.vt_of`, `vol_n`).
Total cap 0.98 everywhere. Decisions at the close, fills at the next open (fast engine, unchanged).

## Arms (four; FIXED parameters; fill rule K for all)

- **C1:** w(t) = min(0.98, fpos/20 × vt).
- **C2:** w(t) = min(0.98, min(1, fpos/10) × vt). Full weight at forecast 10.
- **C3:** C2 with the forecast smoothed by an EWMA of span 5 (alpha = 1/3, `adjust=False`, started at the first
  session, undefined values = 0) before the min(1, ·/10) step.
- **C4:** C2, with the R2 early re-entry kept. e2p(t) = [comb(t) > 0]. While e2p is true, w = C2 weight. While
  e2p is false, the R2 early path (`fe_signals.r2_state` with e2 = e2p: SMA50 / vol-median / 20-session
  momentum entry; exit below SMA50) holds min(0.98, 0.5 × vt); otherwise w = 0.

Arm count: 4 forecast arms + K + R2 reference = 6 rows. DSR trial count N = 365 + 4 = 369.

## Data

No new downloads. Inputs restored from `archive/research/gates-v2-2026-10` (`research/gates_v2`, `research/fast_engine`,
`research/band_h2_combo_v1`, `research/trend_robustness_v2`, `data/historical`) and `archive/research/gates-v1-2026-10`
(`research/gates_v1`). SHA-256 of every restored data file and the originating manifests are in `DATA_MANIFEST.json`.
Main window 2000-03-09 → 2026-09-29, first measured session START = 253. OOS: ^GSPC 1957–1999 and M1–M5 as defined in
`fe_config.MARKETS` (calibrated financing, USD rule, m = 2).

## Simulation, tax, bootstraps

- Pre-tax: `fe_sim.simulate(..., mode="pre_tax")`, base financing (spread 0) and calibrated (0.75 %), m = 2 (m = 1
  for G6).
- After tax: `fe_sim.simulate(..., mode="progressive")`, $100,000 liquidated, base financing, m = 2, windows
  2000-03-09 → 2026-09-29, 2010-01-01 → end, 2017-01-03 → end; halves pre-2020 (2000-03-09 → 2019-12-31) and
  post-2020 (2020-01-01 → 2026-09-29), each its own account (gates_v2 convention).
- Clean bootstraps: `fe_boot.boot` with seed 20261011, block 20, 5,000 pre-tax ten-year paths (G5) and 1,000
  after-tax paths on the fixed 2015-09 → 2026-09 calendar (B1 = progressive tax, B2 = flat 20 %). For each forecast
  arm the same draws are used as for K: `chunk_legs` is wrapped so that the target weights of the forecast arm are
  computed on each resampled price path (same vectorised formulas); the K control comes from the unmodified run.
  Paired differences of CAGR per path.

## Gates (candidate vs K; "both" = base and calibrated financing)

- **G1 (m = 2, both):** CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp.
- **G2 (both):** max DD ≤ K + 1 pp.
- **G3 (both):** 2017-end CAGR ≥ K − 1 pp.
- **G4:** CAGR above K in ≥ 2 of 3 sub-periods: P1 2000–2008, P2 2009–2016, P3 2017–end.
- **G5 (both):** block-20 pre-tax bootstrap median of (arm − K) ≥ 0.
- **G6 (both):** G1 at m = 1.
- **G7:** six OOS windows, calibrated, m = 2: median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp in ≥ 5 of 6; Δ DD ≤ +3 pp in
  ≥ 5 of 6 and never > +8 pp.
- **G8:** pooled OOS bootstrap over M1–M5, 2,000 ten-year paths each (block 20, 2,520 sessions), paired on the same
  draws: median Δ CAGR ≥ 0. Implemented as a vectorised numpy block resampler of the engine's daily NAV returns
  (seed 20261011, `numpy.random.default_rng`); `fe_boot` has no OOS mode. Declared here before any run.
- **G9:** turnover per year ≤ 2 × K.
- **After tax (pre-registered gate):** Δ vs K at $100k positive in all three windows; B1 and B2 median Δ ≥ 0 with
  share of paths with Δ > 0 ≥ 55 %.
- **Reported, not gated:** pre-2020 and post-2020 after-tax Δ vs K (both must be > 0, gates_v2 gate); sells per
  year (QLD sells and total sells including tax-funding sales); deflated Sharpe.

## Deflated Sharpe ratio

`research/gates_v1/stats.py` on K's and each arm's base-financing pre-tax daily returns (m = 2), with N = 369 trials
and the trial-variance floor 1/(T−1) (`var_trials_floor`). This is the most lenient admissible trial variance, so
it is an upper bound on DSR.

## Selection rule

If a candidate passes every gate above, the verdict is "beats K after tax" and the candidate with the highest mean
after-tax CAGR over the three windows is named. If none passes, no arm beats K. No gate, arm or parameter is changed
after seeing results; any bug fix is an amendment.

## Known limits (declared before running)

- The engine models no dividend tax; the S&P comparison is not part of this study.
- Expanding scalars use the instrument's own past; the first 20 valid observations give no position, which costs
  roughly the first year of data relative to K. Both are in the arms' numbers.
- Two gates (G5, B1/B2) use the bootstrap; the B1/B2 windows are a single fixed 2015–2026 calendar.
