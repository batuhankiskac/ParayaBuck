# PREREGISTRATION: dd_budget_v1 (audit item 55)

Worker `dd_budget_v1`, branch `research/dd_budget_v1`, dated 2026-10-27.
Code: `research/dd_budget_v1/screen.py` (driver) and `ddbudget.py` (drawdown budget signal, numba window and
expanding-median kernels). Engine: `research/fast_engine` (every simulation through `fe_sim.simulate`; bootstrap
through `fe_boot._boot_chunk`). Gate and DSR code: `research/gates_v1/stats.py`. Harness origin:
`research/slow_vol_blend_v1` (restored from tag `archive/research/slow-vol-blend-v1-2026-10`).

## Question

Does a drawdown-based risk budget, measured on the UNDERLYING signal (QQQ closes, not the account equity that the
drawdown governor of batch_bank_v3 / IC49 used), improve K after Turkish tax without a materially worse drawdown?

Hypothesis: when QQQ has been in a deep, persistent drawdown (high Ulcer Index or CDaR relative to its own history),
the vol target should fall; when the measure is calm, it may rise. The budget changes the target level only. K's vote,
its early re-entry, its vol20 sizing denominator and its execution layer (A1u gain band, H2 December deferral,
0.5 pp band) are unchanged.

Prior work not repeated: IC49 (account-equity drawdown governor, rejected in batch_bank_v3), CVaR and vol-regime
sizing (vol_regime_leverage_v1, idea_bank_v2 IC11 family), vol-target levels (exposure_k_v1, vt_level_k_v1) and
slow vol estimates (slow_vol_blend_v1). The three arms below differ from all of these in the signal: a drawdown
statistic of the underlying index path, with a budget relative to its own expanding median.

## Control and reference (unchanged)

- **K** (control) = production rule: E2 vote + R2 early re-entry (`fe_signals.r2_state`), sized as
  `on x factor x min(1, 0.35 / vol20)`, cap 0.98, with the K fill rule (A1u gain band, H2 December deferral, 0.5 pp
  band). Reproduced by `--harness` to 0.01 pp.
- **R2** (reference row) = same weights, C0 fill rule.
- Mechanics as in `slow_vol_blend_v1`: decide at the close, fill at the next open, cost m = 2 (base) and the
  calibrated financing spread (+0.75 %). Idle cash earns zero. Main window 2000-03-09 to 2026-09-29.

## Drawdown budget (definitions, fixed)

Let P_t be the QQQ close (the underlying price, financing-free) at session t. For session t, the trailing window is
the 252 closes P_{t-251} ... P_t (known at the close of t). Within the window the running peak starts at the window
start, and the depth of close i is d_i = 1 - P_i / max(P_{t-251} ... P_i), so d_i >= 0.

- **Ulcer Index** UI_t = sqrt( mean over the window of d_i^2 ).
- **CDaR 95 %** CD_t = mean of the worst 13 depths in the window (13 = ceil(0.05 x 252)).
- **Expanding median** M_t = median of all finite values of the same measure from its first defined session up to and
  including t (no look-ahead).
- **Budget ratio** rho_t = clip( M_t / current_t, 0.6, 1.2 ), with rho_t = 1.2 when current_t <= 0. The measure is
  UI or CD, as the arm says.
- **Target** T_t = 0.35 x rho_t (VOL_TARGET 0.35, fixed).
- **Weight** w_t = min(0.98, on_t x factor_t x min(1, T_t / vol20_t)), with vol20 and on/factor exactly as K.
- **Warm-up.** For t < 252 (fewer than 252 sessions) and whenever rho_t is undefined, every U arm uses K's own
  weight w_K,t.

## Arms (every parameter fixed here; no other arm)

| arm | budget measure | update | rule |
|---|---|---|---|
| **U1** | Ulcer Index, 252 sessions | every session | T_t = 0.35 x clip(M^UI_t / UI_t) |
| **U2** | CDaR 95 %, 252 sessions, worst 13 depths | every session | T_t = 0.35 x clip(M^CD_t / CD_t) |
| **U3** | Ulcer Index as in U1 | last session of each calendar month | rho held from the last session of each month (inclusive) to the next month-end close |

U3 is the monthly-update version of U1: its rho at session t is U1's rho at the last session s <= t that is the
last session of its calendar month (last session = the session before a session of a different month). It is
meant to cut the number of trims. The month index is `fe_tax.month_index`, the same table the tax engine reads.

Execution for every U arm: K's fill rule (`RULE_OF = K`), i.e. A1u gain band, H2 December deferral, 0.5 pp band.
Controls: K (rule K) and R2 (rule C0). Four arms in total are preregistered: U1, U2, U3 plus the controls.

## Data

Inputs are the same as `slow_vol_blend_v1` (restored from tag `archive/research/slow-vol-blend-v1-2026-10`; SHA-256
in `DATA_MANIFEST.json`): `stress_robustness_v1_daily.csv` (QQQ/SPY/QLD bars, 1999-03-10 onward),
`ewma_band_v1_index.csv` (^IXIC and the M1-M5 OOS indices), `ewma_band_v1_gspc.csv` (^GSPC 1957-1999),
`stress_robustness_v1_dtb3.csv` and `ewma_band_v1_tb3ms.csv` (rates), and the USD-TRY and YI-UFE series. No new market
data is downloaded. The drawdown budget uses the closes already in these files.

Price series used by the budget: main window = QQQ close; bootstrap = the QQQ path close (`pc` of `chunk_states`);
OOS windows = the index close of each window.

Turkish tax: the progressive engine (2026 tariff, YI-UFE indexation, instalments), $100k liquidated.

## Bootstraps and windows (unchanged)

- Pre-tax bootstrap: block 20, seed 20261011, 5,000 ten-year paths (`fe_boot` draws, identical across arms), both
  financings.
- After-tax bootstraps: B1-recent (real 2015-09 to 2026-09 calendar, first 1,000 paths), B2 (flat 20 % on the
  annual net gain), same draws.
- Bootstrap paths have no pre-history: each path starts its expanding median at its first session, and U3 uses the
  bootstrap calendar's month index (`win.env.mth_of`) as the calendar of the path's sessions. The warm-up applies
  to each path's first 252 sessions.

## Gates (pre-registered; all must pass for an arm to be a candidate)

Same definitions as `slow_vol_blend_v1` (which uses `vol_regime_leverage_v1` and ewma_band_v1's G1-G9 with K as C0).

- **G1 (both financings):** CAGR >= K, or Calmar >= 1.1 x K's Calmar with CAGR >= K - 0.5 pp.
- **G2 (both):** max DD <= K + 1 pp.
- **G3 (both):** 2017-end CAGR >= K - 1 pp.
- **G4 (base):** CAGR above K in >= 2 of 3 sub-periods (P1 2000-2008, P2 2009-2016, P3 2017-end).
- **G5 (both):** pre-tax bootstrap median (arm - K) >= 0.
- **G6 (both):** G1 at m = 1.
- **G7 (calibrated, m = 2):** six OOS windows (^GSPC 1957-1999 and M1-M5). Median Delta CAGR >= 0; Delta CAGR >= -0.5 pp
  in >= 5 of 6; Delta DD <= +3 pp in >= 5 of 6 and never > +8 pp.
- **G8 (reduced, as pre-registered in vol_regime_leverage_v1):** each M1-M5 daily Delta (arm - K, pre-tax, calibrated,
  m = 2) is resampled in circular blocks of 20 sessions, 2,000 replications, seed 20261011; the statistic per
  replication is the mean over M1-M5 of 252 x mean daily Delta. Pass iff its median >= 0.
- **G9:** annual turnover <= 2 x K's (base, m = 2).

After tax (progressive, $100k liquidated, base financing):

- **A1:** after-tax CAGR above K in all three windows (2000-03-09 to 2026-09-29; 2010-01-01 to end; 2017-01-03 to end).
- **A2 (B1-recent):** progressive-tax bootstrap on the same draws: median Delta >= 0 and share Delta > 0 >= 55 %.
- **A3 (B2, flat 20 %):** same paths: median Delta >= 0 and share >= 55 %.
- **A4:** pre-2020 (2000-03-09 to 2019-12-31) and post-2020 (2020-01-01 to end) after-tax Delta vs K both > 0. Each
  half is its own $100k account.

Reported, not gated: sells per year, mean QQQ-equivalent exposure on vote-on days (all days and pre-2010), pre-tax
pre-2020 / post-2020 CAGR deltas, the budget ratio distribution (mean, share at the clip bounds), DSR, the OOS table.

**DSR.** Bailey and Lopez de Prado deflated Sharpe (`research/gates_v1/stats.py`) on the pre-tax daily base m = 2
returns of each arm, and on the after-tax (progressive) daily series, with **N = 375 + 3 = 378** trials (375 is the
ledger count given to this worker; this study adds its three arms). Bar 0.95, reported only. Sanity check: K at
N = 365 reproduces gates_v1's 0.711 (the harness checks this).

**Selection.** An arm replaces K only if every G and A gate passes. Otherwise K stays and the report says so. No
production configuration changes in any case. The study is a candidate test, not a promotion.

## Controls, checks and anchors (before any non-control arm was simulated)

`python -m research.dd_budget_v1.screen --harness` reproduces band_h2_combo_v1's K and R2 numbers to 0.01 pp (pre-tax
15.765 % base / 15.18 % calibrated, max DD 46.67 %; after tax 10.75 / 15.24 / 23.97 %; R2 15.48 %, after tax 10.16 /
14.71 / 22.89 %), K's DSR at N = 365 = 0.711, K's weights equal `fe_boot.chunk_legs` on the same draws, and
`research.fast_engine.parity --quick` passes. It also checks the signal code against explicit definitions on
synthetic data only: the drawdown kernels (UI and CDaR) against a brute-force window, the expanding median against
`np.median` on every prefix, the monthly hold against an explicit month-end loop, the sizing with a constant target
of 0.35 against K's sizing, the warm-up, and the 0-cap bounds.

Disclosure: the harness was run on 2026-10-27 before this commit. It built the U-arm weight arrays on the real data
(`prepare`) but simulated no U arm and computed no gate. Result: HARNESS PASS.

## Amendments

None. Any bug fix found after results is recorded here with the reason; gates and arms stay as written.

## Known limits (stated before running)

- **Expanding median.** The main median starts at the first defined session (~2000-03), so the early ratios rest on
  a short history. The OOS median starts at each OOS window's first session. Read the first years as the start of
  the rule, not a full-history calibration.
- **Bootstrap paths have no pre-history** (see above). The month structure of U3 in a bootstrap path follows the fixed
  calendar, not the resampled sequence.
- **Clip bounds.** At 0.6 / 1.2 the target spans 0.21-0.42, i.e. the budget can cut the target by 40 % or raise it by
  20 %. The clip is not tuned.
- **One path.** The after-tax windows overlap heavily (2000 and 2010 share history). The bootstraps and OOS windows are
  the robustness checks.
- **Ledger count.** The DSR uses N = 378 as instructed (375 + 3). slow_vol_blend_v1 recorded 369 + 3 = 372. The report
  states the N used.
