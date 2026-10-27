# PREREGISTRATION: gates_v2 (items 28, 29, 30, 33, 34)

Worker `gates_v2`, branch `research/gates_v2`, dated 2026-10-27. Code: `research/gates_v2/screen.py`.
Engine: `research/fast_engine` (restored from tag `archive/research/closeout-2026-10`). No
per-day Python loops; simulation, tax and bootstrap-free statistics go through `fe_sim.simulate`.

## Question

Is K's edge over S&P buy-and-hold (after Turkish tax) explained by signal timing, by leverage, or by
the vote and vol-target layers? Are K's results distinguishable from alternative arms (SPA/MCS)? Does
an expanding-window (out-of-sample) choice of the vol target and trend windows beat fixed K after tax?

This is a diagnostic study. No candidate is promoted, no production configuration changes, and the
gates G1-G9 / B1-B2 of the candidate protocol are not applied.

## Control

K = production rule exactly: E2 vote + R2 early leg (`fe_signals.arm_signals`, "R2") on the
`fe_data.main_inputs(0.0)` target, fill rule `K` (A1u gain band + H2 December deferral, 0.5 pp
band). The `--harness` mode must reproduce the published `band_h2_combo_v1` RESULTS.json anchors
(K and R2 = arm "C0") within 0.01 pp, and `python -m research.fast_engine.parity --quick` must PASS.

## Data (fixed; see DATA_MANIFEST.json)

- Main window: session index `START` = 253, 2000-03-09 to 2026-09-29, from `fe_data.load()`.
- QQQ synthetic 2x (QLD*, expense 0.95%), base financing (spread 0.0); calibrated financing
  (spread 0.75%) only for pre-tax rows of the ablation table.
- Cost multiplier m = 2 for every run. Tax: Turkish progressive (2026 tariff, YI-UFE indexation,
  instalments), liquidated at window end, capital $100,000 (`fe_sim.RunEnv`, FX from CBRT).
- Tax windows (from `fe_config.TAX_WINDOWS`): 2000-03-09 to 2026-09-29; 2010-01-01 to end;
  2017-01-03 to end. Each run is its own account starting from cash.
- S&P 500 benchmark: SPY total-return index on the main calendar (close and dividend columns of
  `stress_robustness_v1_daily.csv`; open-to-close conversion identical to `synthetic_2x` with leverage
  1 and zero expense or financing). Caveat: SPY is an ETF proxy with its own expense ratio.

## Arms (fixed in advance; all parameters listed here)

Targets are daily target weights on the synthetic QLD*, cap CAP = 0.98. Rules are `fe_sim.RULE`.

| label | target series | fill rule | role |
|---|---|---|---|
| a_const_2x | 0.98 every session (no vote, no vol target) | C0 (plain 0.5 pp band) | leverage only |
| b_vote_2x_noVT | 0.98 x E2 vote (`e2_up`), no vol target | C0 | vote without vol target |
| c_VT_always_in | min(1, 0.35/vol20) x 0.98, always in | C0 | vol target without vote |
| d_E2 | `w_e2` (E2 production) | C0 | E2 reference |
| e_R2 | `w_r2` (R2 production) | C0 | R2 reference (= band_h2 "C0") |
| f_K | `w_r2` | K | production K (control) |
| SPX_buy_and_hold | SPY total return, weight 1.0 | C0 | S&P benchmark |

Models for SPA / MCS only (pre-tax after-cost returns, same main window): A1u = `w_r2` under rule
A1u; H2 = `w_r2` under rule H2 (band_h2 arms). The band_h2 arm C0 equals e_R2 and is not repeated.

vol20 is `fe_signals.vol_n(c)` on the synthetic 2x close, as in `main_inputs`. The E2 vote and the R2
early leg use the production windows (SMA200, TSM 252, DON 126) in the arms above.

## Items and procedures

**28a. Circular-lag null.** `rng = np.random.default_rng(20261027)`; offsets
`s = rng.integers(252, L - 252 + 1, size=1000)` where L = length of `w_r2` (all sessions). Null
series `np.roll(w_r2, s)` run under rule K, pre-tax, m = 2. Report K's CAGR and Sharpe percentile
among the 1,000 nulls (share of null values strictly below K). Sharpe = mean/sd of daily after-cost
NAV returns x sqrt(252), rf = 0. Pre-registered reading: a percentile >= 95 would mean K's timing
beats 95 % of shifted timings; below 95 means the timing is not separated from shifts at 5 %.

**28b. Block length.** `arch.bootstrap.optimal_block_length` (Politis-White 2004 with the
Patton-Politis-White 2009 correction) on K's daily pre-tax after-cost returns. Report stationary and
circular values next to the fixed block 20 used so far. The block used for SPA/MCS/power is
round(stationary value) (pre-registered; the value is reported, not tuned).

**29. Ablation.** For every arm: pre-tax CAGR (base, m=2, `fe_study.pre_stats`) and max DD; pre-tax
CAGR and max DD with calibrated financing; after-tax $100k liquidated CAGR and max DD in each tax
window.

**30. Pre/post-2020 gate.** After-tax $100k liquidated CAGR of each arm and of SPX_buy_and_hold on
2000-03-09 to 2019-12-31 and on 2020-01-01 to 2026-09-29 (each its own account). For every arm X:
delta_vs_E2_h = CAGR(X, h) - CAGR(d_E2, h); delta_vs_SPX_h = CAGR(X, h) - CAGR(SPX, h).
Gate_vs_E2 = PASS iff delta_vs_E2 > 0 in both halves. Gate_vs_SPX = PASS iff delta_vs_SPX > 0 in both
halves. Reported separately.

**33. SPA and MCS.** Hansen (2005) SPA via `arch.bootstrap.SPA` (arch 8.0.0; installed into the
pb-research venv only), benchmark = f_K daily after-cost returns, models = a, b, c, d, e, A1u, H2;
stationary bootstrap, block = item 28b value, 2,000 reps, studentized, seed 20261027; report lower,
consistent and upper p-values (decision on the consistent p-value at 0.10). MCS (Hansen, Lunde and
Nason 2011) via `arch.bootstrap.MCS` on losses = -daily returns for the 8 series (K + 7 models),
size 0.10, method "R", same bootstrap, block, reps and seed; report the included set and the p-values.

Power check: benchmark = observed K returns; model_t = K_t + delta + sigma * z_t, z iid N(0,1)
(`default_rng(20261028)`), sigma = daily sd of (e_R2 - f_K) returns (fixed noise scale), delta in
{0, 0.5, 1, 2} bp per day. 200 replications per delta, SPA with 500 reps (seed = replication index),
detection = consistent p <= 0.10. delta = 0 gives the empirical size.

**34. Expanding window (Cederburg-style).** Grid: vol target in {0.25, 0.30, 0.35, 0.40, 0.45} x
(TSM, DON) in {(252,126), (189,126), (252,63)} = 15 configurations, ordered window-major then
vol-minor. Each configuration: R2 target from its own E2 vote (SMA200 unchanged) with its own vol
target; each run in full on the main window, pre-tax, rule K, m = 2. For each year Y from 2005 to
2026: choose the configuration with the largest Sharpe of its daily after-cost returns over sessions
t >= START with year < Y (first in grid order on ties). Sessions of year Y use that configuration's
target (spliced series, one account). Years 2000 to 2004 use the fixed K configuration (0.35, 252/126).
Compare with fixed K (pre-tax and after tax at $100k in the three windows).

## Verdict rules (fixed)

- Item 30 gate as defined above. No threshold other than sign.
- SPA / MCS: p-value or inclusion at alpha = 0.10 (K is the benchmark; "K beaten" if SPA consistent
  p <= 0.10).
- Item 34: "expanding beats fixed" only if after-tax CAGR is higher in all three windows.
- Results are reported whichever way they fall; no arm, parameter or gate changes after data are seen.
  Code bugs go in an "Amendments" section of the report with the reason.
