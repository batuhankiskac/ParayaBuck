# fdm_v1: Carver's forecast diversification multiplier on the K rules, versus K after Turkish tax

Branch `research/fdm_v1`. Code `research/fdm_v1/screen.py` (derived from `research/carver_forecast_v1/screen.py`,
copied and changed for the arms below). Outputs `RESULTS.json`, `SUMMARY.txt`, report `docs/research/fdm_v1.md`.
Engine: `research/fast_engine` (`fe_sim.simulate`, `fe_tax`, `fe_boot.boot`). Written and committed before any
non-control arm or diagnostic was computed on real data.

## Question (audit item 54)

Carver's forecast diversification multiplier, FDM = 1 / sqrt(wᵀ ρ w), with w = 1/3 for each of the three rules and
ρ the forecast correlation matrix, rescales a combined forecast so that its average size returns to its target.
The three K rules all read QQQ, so ρ should be high and FDM close to 1. This study asks two things:

1. What are ρ and FDM for K's three rules, estimated on an expanding window with no look-ahead?
2. Does a forecast arm that uses FDM beat K after Turkish income tax (USD, $100k liquidated, progressive schedule),
   without materially worse drawdown?

## Control and reference

- **K (control).** Production rule exactly: target `w_r2` of the R2 vote path (E2 + early re-entry) with fill rule `K`
  (A1u gain band, H2 December deferral, 0.5 pp band). Reproduced by `g2.harness()` (gates_v2 harness).
- **R2 (reference).** Target `w_r2` with fill rule `C0` (plain 0.5 pp band).

## Rule forecasts (identical to carver_forecast_v1; no look-ahead)

- r1(t) = close / SMA200(t) − 1; r2(t) = close(t) / close(t − 252) − 1; r3(t) = 2 (close − min126) / (max126 − min126) − 1
  (0 when max126 = min126). All on the QQQ close.
- A_i(t) = mean of |r_i(s)| over all valid s ≤ t (expanding), defined once it has ≥ 20 valid observations.
- f_i(t) = clip(r_i(t) / A_i(t) × 10, −20, +20). Combined comb(t) = (f_1 + f_2 + f_3) / 3.

## Votes (K's three binary votes, for the vote-count arm and for "vote-on days")

v(t) = [close > SMA200] + [252-session price return > 0] + [close > midpoint of the 126-session channel] ∈ {0,1,2,3}.
K's E2 is on when v ≥ 2 (`fe_signals.e2_up`). "Vote-on days" are days with v ≥ 2. The harness checks v ≥ 2 ⇔ e2_up.

## Expanding correlation and FDM (the diagnostic and the multiplier)

- **Checkpoints.** The last session of each calendar year of the main series (real calendar). At checkpoint c, the
  sample is every session s ≤ c where all three forecasts are finite (joint sample n). ρ is the 3 × 3 Pearson
  correlation of (f_1, f_2, f_3) over that sample.
- **FDM at a checkpoint.** q = wᵀ ρ w with w = (1/3, 1/3, 1/3). FDM_c = min(2.5, 1 / sqrt(q)) when n ≥ 252 joint
  sessions, else 1.0. The cap 2.5 is Carver's standard FDM cap; it is a floor on q at 1/2.5² and binds only if the
  rules become uncorrelated.
- **FDM in force.** FDM(t) = FDM_c for the latest checkpoint c < t (applied from the session after c, so decisions at t
  use only data through t − 1 for FDM). FDM(t) = 1.0 before the first checkpoint with n ≥ 252. The first such
  checkpoint is the 2002 year-end on the main series.
- **Bootstrap paths.** A resampled price path has no calendar, so its checkpoints are synthetic: sessions 251, 503,
  755, ... (every 252 sessions from the path start), with the same n ≥ 252 rule.
- **Diagnostics reported (no gate).** ρ_12, ρ_13, ρ_23, q, n and FDM at every real checkpoint from 2001 to 2026,
  the FDM in force averaged over the main window, and a full-sample ρ and FDM that uses look-ahead. The full-sample
  values are reported for information only; no arm uses them.

## Arms (two; FIXED parameters; fill rule K for both)

- **F1 (carver's C3 × FDM, cap ±20).** comb_F1(t) = clip(comb(t) × FDM(t), −20, +20). fpos = max(comb_F1, 0), with
  undefined → 0. fs = EWMA of fpos with span 5 (alpha = 1/3, `adjust=False`, started at the first session). Weight
  w(t) = min(0.98, min(1, fs / 10) × vt), vt = min(1, 0.35 / vol20) on the synthetic 2× close, as K. With FDM ≡ 1 this
  arm equals carver's C3 bit-for-bit (harness check).
- **F2 (K's vote count as a forecast × FDM, then K's sizing).** f2(t) = min(20, v(t)/3 × 20 × FDM(t)). e2p(t) = [v(t) ≥ 1].
  While e2p: w(t) = min(0.98, min(1, f2 / 10) × vt). While not e2p: K's R2 early re-entry, i.e.
  `fe_signals.r2_state(price, e2p)` (SMA50 / vol-median / 20-session momentum entry, exit below SMA50) holds
  min(0.98, 0.5 × vt); otherwise 0.

  Interpretation of "K's sizing", declared here before any run: the map from forecast to position is the carver
  arms' map (full vt at a forecast of 10), so with FDM ≥ 1 a vote count of 2 or 3 gives exactly K's E2 weight vt,
  a vote count of 1 gives a partial weight min(1, 0.67 × FDM) × vt (K holds nothing on it unless the early path is
  on), and a count of 0 uses K's early re-entry. The harness checks that F2 equals `w_r2` on every vote-on day.

- **K** (control): target `w_r2`, fill rule K. **R2** (reference): target `w_r2`, fill rule C0.

Arm count: 2 arms + K + R2 = 4 rows, so no PBO is required (grid of at most 4 arms). The DSR trial count is
N = 375 + 2 = 377 (the registry count of 375 stated for this study plus the two arms).

## Exposure reporting (no gate)

For each row, on the main window from the first measured session: mean QLD target weight over all days, over
vote-on days (v ≥ 2) and over the other days; the QQQ-equivalent exposure is 2 × the QLD weight (QLD is 2× QQQ).
The carver study's arms ran at lower exposure than K (mean weight 0.31 to 0.53 against 0.67). The exposure table
shows whether F1 and F2 do the same.

## Data

No new downloads. Inputs restored from `archive/research/carver-forecast-v1-2026-10`: `research/fast_engine`,
`research/gates_v2`, `research/gates_v1` (stats, provenance and inputs), `research/band_h2_combo_v1`,
`research/trend_robustness_v2`, `research/carver_forecast_v1` (its `weights("C3")` is imported only for the
bit-equality check of F1 with FDM ≡ 1), and `data/historical`. SHA-256 of every input file and the origin
manifests are in `DATA_MANIFEST.json`. Main window 2000-03-09 → 2026-09-29, first measured session START = 253.
OOS: ^GSPC 1957–1999 and M1–M5 as in `fe_config.MARKETS` (calibrated financing, USD rule, m = 2).

**Sealed window.** `docs/research/sealed_window_v1.md` (commit 4c40d114 on `main`) reserves sessions after
2026-10-30. The screen asserts that every loaded series ends before SEALED_FROM = 2026-10-30 and records the last
session in RESULTS.json. The data used here end on or before 2026-09-29 for the main series.

## Simulation, tax, bootstraps (as carver_forecast_v1)

- Pre-tax: `fe_sim.simulate(..., mode="pre_tax")`, base financing (spread 0) and calibrated (0.75 %), m = 2 (m = 1 for G6).
- After tax: `fe_sim.simulate(..., mode="progressive")`, $100,000 liquidated, base financing, m = 2, on the windows
  2000-03-09 → 2026-09-29, 2010-01-01 → end and 2017-01-03 → end; halves pre-2020 (2000-03-09 → 2019-12-31) and
  post-2020 (2020-01-01 → 2026-09-29), each its own account.
- Clean bootstraps: `fe_boot.boot`, seed 20261011, block 20, 5,000 pre-tax ten-year paths (G5) and 1,000 after-tax
  paths on the fixed 2015-09 → 2026-09 calendar (B1 = progressive tax, B2 = flat 20 %). Each arm uses the same draws as
  K: `chunk_legs` is wrapped so that the arm's targets, including FDM on the synthetic checkpoint schedule, are
  recomputed on each resampled price path. Paired differences of CAGR per path.

## Gates (candidate vs K; "both" = base and calibrated financing)

These are the carver_forecast_v1 gates, unchanged.

- **G1 (m = 2, both):** CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp.
- **G2 (both):** max DD ≤ K + 1 pp.
- **G3 (both):** 2017-end CAGR ≥ K − 1 pp.
- **G4:** CAGR above K in ≥ 2 of 3 sub-periods: P1 2000–2008, P2 2009–2016, P3 2017–end.
- **G5 (both):** block-20 pre-tax bootstrap median of (arm − K) ≥ 0.
- **G6 (both):** G1 at m = 1.
- **G7:** six OOS windows, calibrated, m = 2: median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp in ≥ 5 of 6; Δ DD ≤ +3 pp in
  ≥ 5 of 6 and never > +8 pp.
- **G8:** pooled OOS bootstrap over M1–M5, 2,000 ten-year paths each (block 20, 2,520 sessions), paired on the same
  draws: median Δ CAGR ≥ 0 (vectorised block resampler, seed 20261011).
- **G9:** turnover per year ≤ 2 × K.
- **After tax (pre-registered gate):** Δ vs K at $100k positive in all three windows; B1 and B2 median Δ ≥ 0 with
  share of paths with Δ > 0 ≥ 55 %.
- **Reported, not gated:** pre-2020 and post-2020 after-tax Δ vs K (both must be > 0); sells per year (QLD sells and
  total sells including tax-funding sales); exposure table; the ρ and FDM diagnostics; deflated Sharpe.

## Deflated Sharpe ratio

`research/gates_v1/stats.py` on the base-financing pre-tax daily returns (m = 2) of K, R2 and each arm, with
N = 377 trials and the trial-variance floor 1/(T−1) (`var_trials_floor`). An upper bound on DSR, as in carver.

## Provenance

RESULTS.json carries `research/gates_v1/provenance.py` `stamp()`: SHA-256 of every input file, git commit of the code
and package versions. Two runs must produce byte-identical RESULTS.json.

## Harness checks (declared here)

1. `g2.harness()`: K and R2 anchors within 0.01 pp and `parity --quick` PASS.
2. Causality: perturbing prices from session 900 onward leaves the rule forecasts, comb, FDM and both arms unchanged
   on sessions before 900.
3. Identity: v ≥ 2 equals `fe_signals.e2_up` on the main series.
4. F1 with FDM ≡ 1 equals carver's C3 weights bit-for-bit on the main series.
5. F2 equals `w_r2` on vote-on days, up to 1e-12.

## Selection rule

If a candidate passes every gate above, the verdict is "beats K after tax" and the candidate with the highest mean
after-tax CAGR over the three windows is named. If none passes, no arm beats K. No gate, arm or parameter is changed
after seeing results; any bug fix is an amendment.

## Known limits (declared before running)

- The engine models no dividend tax; the S&P comparison is not part of this study.
- FDM uses forecast correlation, as the question states. Carver's own FDM tables use subsystem return correlation,
  which is not computed here.
- The first FDM estimate arrives in 2002 (n ≥ 252 rule). Before it, FDM = 1, so the arms are the carver arms for
  about the first two years.
- With rules that all read QQQ, FDM stays close to 1 unless the forecasts decorrelate. An FDM close to 1 means the
  multiplier is almost a no-op, and the arms then test the vote-count forecast and the exposure map, not FDM.
- The bootstrap uses a synthetic 252-session schedule for FDM. The main series uses the calendar.
