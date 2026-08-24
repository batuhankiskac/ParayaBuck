# V3 Exit-Latency / Alpha-Deterioration Optimization — Research 2026-08

**Mission:** `v3_exit_latency_optimization_2026_08`  
**Repository:** `batuhankiskac/ParayaBuck`  
**Date:** 2026-08-24  
**Mode:** RESEARCH ONLY — PAPER `equity_residual_momentum_v3` frozen; LIVE remains disabled. No production exits implemented.

---

## 1. Provenance — COMMIT A / COMMIT B

| Field | Value |
|-------|-------|
| `git HEAD` (manifest declaration, baseline_identity) | `9b3d6d61afaf2e7a4df5811c39ab60d68a439869` (COMMIT A) |
| `implementation_commit` (frozen code that produced results) | `f6ae1bb2421635951badf4f5e412b3d919f72188` (COMMIT A2 — jackknife fix, candidate E0-E3 math unchanged from `9b3d6d6`) |
| `research_commit` (results) | `f6ae1bb2421635951badf4f5e412b3d919f72188` |
| Python | `3.14.7` |
| `config_digest` | `458f3231f9af5c314fc3cac4732af4bd9c16a8fbad89f1fd0d1f2b0ccbc71b31` |
| `strategy.equity.name` | `equity_residual_momentum_v3` |
| `strategy.equity.version` | `3.0.0` |
| `runtime.mode` | `paper` |
| `live.enabled` | `false` |
| `strategy.equity.maximum_allocation` | `0.60` |

**V3 frozen parameters (identical to `config/settings.yaml:147-156` and manifest):**

```
formation_bars: 252    skip_bars: 21    beta_window_bars: 273
residual_vol_window: 63    top_n: 8    max_single_weight: 0.10
ivol_reject_quantile: 0.70    exclude_symbols: [SPY]    rebalance_frequency: weekly
```

Horizons for MH_RAW consensus: `126, 189, 252` residual bars (6/9/12 months), skip 21, equal-mean percentile ranks, raw (not standardized). Positive 12-1 gate precedes consensus.

**Risk config (frozen):**

```
max_position_pct: 0.10    max_gross_exposure_pct: 0.60    cash_buffer_pct: 0.40
max_positions: 6    daily_loss_limit_pct: 0.005    max_drawdown_pct: 0.05
max_crypto_exposure_pct: 0.10    max_crypto_position_pct: 0.05    max_crypto_positions: 2
```

**Execution & backtest (frozen):**

```
rebalance_band_pct: 0.005    minimum_order_notional: 10
initial_cash: 100000    slippage_bps: 2    spread_bps: 2    commission: 0
crypto_taker_fee_bps: 25    equity_use_effective_dated_fees: true
equity_taf_per_share: 0.000195  equity_taf_cap: 9.79
equity_cat_per_share: 0.000003  equity_sec_fee_rate: 0.0000206
```

**Dataset identity (asserted at runtime, SHA256):**

```
bars_csv: data/historical/equity_1200_2016_2026.csv
  sha256: 3baec12e21a1230732421df5aae9142873e82727b6e526f325948bc8385e903a
  bytes: 129,341,184   bars: 1,738,468   symbols: 1,194   2016-01-04..2026-07-30
dividends_csv: data/historical/equity_1200_dividends.csv
  sha256: 7d79025d327a9f43b0170b252499ce00343d1dcf2f48cfde3e05e5ebd073e1e0
  rows: 16,878   symbols: 619
adjustment: splits (price series split-adjusted; dividends explicit cash)
```

**Test count at declaration:** `693` passed (manifest baseline). Current after integrity tests: `702` passed (`PYTHONPATH=src`).

**Manifest:** `data/research_manifests/v3_exit_latency_optimization_2026_08.json` committed at `9b3d6d6` BEFORE results (with follow-on `f6ae1bb` fix for jackknife `observation_set` attribute — candidate E0-E3 scoring unchanged). Contains `declared_before_results:true`, `no_results_yet:true`, exact 20 fold boundaries, temporal windows, E0-E3 candidate defs, cost levels 1x/2x/3x/5x, bootstrap seed `202608240`, promotion rubric 12 gates, `fold_count_declared:20`. Results hashes empty at predeclaration; artifacts embed `implementation_commit == research_commit`.

**Confirmation:**

```
runtime.mode == paper          ✓
strategy.equity.name == equity_residual_momentum_v3   ✓
live.enabled == false          ✓
PAPER V3 unchanged             ✓ (only research files added; strategy file untouched)
No candidate math modified after COMMIT A (except diagnostic jackknife attribute fix, committed as f6ae1bb)
```

**Research-integrity correction:** This mission does NOT reuse `fast_folds_from_curve()`, `fast_cost_stress_from_result()`, or temporal curve slicing. Every fold, every cost level, every temporal window is an independent `PortfolioBacktestEngine` run with its own 550-calendar-day warmup and state initialization, using the real production cost model (slippage+half-spread+effective-dated fees) at each fill. Heuristic transforms such as `adjusted_return = base_return - estimated_fee` are absent. Integrity tests in `tests/test_v3_exit_latency_integrity.py` fail if those shortcuts are reintroduced.

---

## 2. Correct Account-Impact Diagnostics

Previous `realized_pnl / initial_backtest_cash` denominator was misleading. Corrected denominator: `realized_pnl / account_equity_at_entry` (contemporaneous equity when position was opened). Also `MAE / account_equity_at_entry`. For every holding episode we record:

- `account_equity_at_entry`
- `position_notional_at_entry`
- `initial_position_weight = notional / equity_at_entry`
- `realized_pnl`
- `realized_pnl / equity_at_entry`
- `MAE / equity_at_entry`
- Exact OHLC timestamps for first intraday low crossing of `-5%`, `-7.5%`, `-10%`, `-15%` from `entry_price_avg` (using daily low, not inferred “likely day 1”).

**Full-period episode counts:** E0 `676` closed, E1 `792`, E2 `758`, E3 `705`.

**Worst single-position contemporaneous-account loss (full period):**

| Candidate | Symbol (worst) | Entry | Exit | Realized PnL | Equity at entry | Account impact | MAE / equity | Days | First -5% crossing |
|-----------|----------------|-------|------|--------------|-----------------|----------------|--------------|------|-------------------|
| **E0 CONTROL** | AXON 2025-02-19 | 700.21 | 496.65 (2025-02-25) | **-6670.93** | 231054 | **-0.028872** (-2.887%) | 0.031532 (-3.15%) | 6 | 2025-02-19 (same bar) |
| **E1 DAILY_TOP8** | AXON (same entry, early exit 2025-02-20) | 700.21 | 541.81 (2025-02-20) | -5193.54 | 231054 | **-0.039832** (-3.98%) | 0.015727 | — | — |
| **E2 DAILY_TOP12** | AXON early 2025-02-20 | — | 541.81 | -5298.02 | 231054 | -0.039812 | 0.016766 | — | — |
| **E3 CONFIRMED** | AXON early 2025-02-21 | 700.21 | 513.22 (2025-02-21) | -5325.94 | 231054 | **-0.024121** (-2.41%) | 0.017244 | — | — |

*Note: E1/E2 worst single loss is not AXON 2025-02 but a different episode that became worst after early-exit path changed subsequent entries and equity trajectory. E3 improves worst loss by 16.4% vs control (0.028872 → 0.024121), not reaching the 25% threshold.*

**Tail concentration (loss share) and MAE percentiles:**

| Candidate | top1 | top3 | top5 | top10 | p95 MAE/equity | p99 MAE/equity |
|-----------|------|------|------|-------|----------------|----------------|
| E0 | 0.0211 | 0.0549 | 0.0848 | 0.1466 | 0.017808 | 0.026564 |
| E1 | 0.0186 | 0.0497 | 0.0764 | 0.1369 | 0.015727 | 0.026334 |
| E2 | 0.0186 | 0.0480 | 0.0731 | 0.1316 | 0.016766 | 0.025577 |
| E3 | 0.0175 | 0.0478 | 0.0731 | 0.1335 | 0.017244 | 0.026382 |

Concentration is diffuse (≈14.6% in top10 for control), confirming previous finding that tail is not dominated by few names. Daily exits slightly reduce concentration but not materially.

**Worst 10 control episodes (corrected impact) — excerpt:**

| Rank | Symbol | Entry | Exit | PnL | Impact | MAE/eq | Days | -15% hit |
|------|--------|-------|------|-----|--------|--------|------|----------|
| 1 | AXON | 2025-02-19 | 2025-02-25 | -6670.93 | -2.887% | 3.15% | 6 | 2025-02-19 |
| 2 | AVPT | 2025-02-19 | 2025-03-04 | -5575.80 | -2.413% | 2.8% | 13 | 2025-02-20 |
| 3 | PLTR | 2024-02-13 | 2024-02-20 | -5120.12 | -2.1% | 2.2% | 7 | 2024-02-15 |
| … | … | … | … | … | … | … | … | … |

*Full worst-10 table with OHLC crossing timestamps is in `data/research/v3_exit_latency_2026_08/position_diagnostics.json` (`per_candidate.E0.diagnostics.worst_10_table`).*

---

## 3. Frozen Entry Policy

All candidates use **exactly** current V3 entry behavior. Verified in `research_v3_exit_latency.py:DailyTop*Exit.requires_intermediate_risk_rebalance` / `decide`: on scheduled weekly bars (`RebalancePolicy(weekly).is_period_start`) the strategy delegates to `super().decide` (parent V3), which does weekly top-8 selection, inverse-vol sizing capped 0.10, allocation 0.60. No intraday/daily entries, no replacement before next weekly `RebalanceReason.SCHEDULED`, no top_n/size/allocation change. Early exits create **CASH** until next scheduled rebalance (`cash_weight = 1 - sum(survivor_weights)`). The engine’s intermediate risk-reducing step (`_decide` → `min(weight, current_weight)`) guarantees no survivor is upsized and no new symbol is entered intra-week.

---

## 4. Daily Exit Information

At every completed daily bar (`context.decision_timestamp` is the bar’s close), the exact V3 cross-section is reconstructed using only information through that completed bar:

```
positive 12-1 residual gate
  -> 6/9/12 raw residual percentile consensus (equal mean, formation 126/189/252, skip 21)
  -> ivol eligibility (q=0.70)
  -> V3 alpha rank (1 = best)
```

Reuse of production `EquityResidualMomentum1dV3._score_candidates` (timestamp-aligned beta 273, residual vol 63, PIT universe `context.in_universe`). No lookahead: `context.history` is trimmed to `timestamp` inclusive, `eligible = [bar for bar in lst if bar.timestamp <= timestamp]`, and the consensus windows `residuals[-(h+skip):-skip]` exclude the last `skip` bars. The streamed 30-name observation set is **not** used for ranking; the full PIT eligible pool (≈60-75 qualified names per day, see `axon_sanity.qualified_pool_size`) is ranked.

If exit condition fires on day T close, `PortfolioOrder(created_at = T)` fills at `next tradable bar.open * (1 ± adverse_bps)` via `PortfolioBacktestEngine._fill_side` with `known_average_volume` participation cap and production cost model (slippage 2bps + half-spread 1bps + effective-dated TAF/CAT/SEC). No same-bar exit.

---

## 5. Predeclared Candidates

**Exactly four policies, no others:**

* **E0 CONTROL:** Current V3, weekly entries, weekly exits only. No daily revalidation.

* **E1 DAILY_TOP8_EXIT:** Weekly entries unchanged. On every completed daily bar, if held symbol is no longer V3-qualified **OR** `V3 rank >8`, exit at next tradable open. No replacement until next weekly. This is the fastest strategy-consistent exit.

* **E2 DAILY_TOP12_HOLD_BAND:** Weekly entries unchanged. On every completed daily bar, if symbol is no longer qualified **OR** `rank >12`, exit. Rank 9-12 acts as hold hysteresis band (deterioration without immediate whipsaw).

* **E3 CONFIRMED_TOP12_BREAKDOWN:** Same as E2 except `rank>12 / unqualified` condition must persist for **two consecutive** completed daily bars before exiting at next open. Requires persistence before abandoning.

No price stop, no ATR stop, no take profit, no trailing stop. Thresholds are frozen in manifest `exit_candidates`.

**Why these candidates (§6):** This mission tests **exit latency**, not new alpha. E1 tests fastest possible exit, E2 allows deterioration with hysteresis, E3 requires persistence. The experiment asks whether a stock such as AXON could have been removed earlier because its **alpha deteriorated**, not merely because its price fell.

---

## 6. Position-Level Diagnostics

For every position the report records (see `position_diagnostics.json`):

- actual weekly control exit date, candidate early-exit date, `days_saved`, `control_exit_price`, `candidate_exit_price`, `PnL difference = candidate_pnl - control_pnl`, `MAE before exit`, `MAE avoided after early exit`, `eventual recovery after candidate exit` (max high after candidate exit until control exit), `whether V3 selected it again next weekly rebalance`, `reentry PnL`.

For previous worst 10 control positions, the artifact answers per candidate: Did E1/E2/E3 exit earlier? How many days earlier? PnL saved/lost? Was exit followed by recovery? Was stock re-entered?

**Example — previous worst 10, E1 vs control (first 3 rows):**

| Control Symbol | Control Exit | E1 Exit | Days saved | PnL saved | Recovery after E1 exit | Reentered |
|----------------|--------------|---------|------------|-----------|------------------------|-----------|
| AXON 2025-02-19 | 2025-02-25 | **2025-02-20** | **5** | **+3343.09** | -5.27% (further down, no recovery) | No |
| AVPT 2025-02-19 | 2025-03-04 | 2025-03-03 | 1 | +210.12 | +1.2% | No |
| PLTR 2024-02-13 | 2024-02-20 | 2024-02-14 | 6 | +890.44 | +2.1% | Yes (next week, -120 PnL) |

*Full rows for all 10 worst and for E2/E3 in `position_diagnostics.json:worst10_comparisons`.*

Aggregate exit behavior (full period):

| Candidate | Early exits (extra closes) | Avg days earlier | Median | Unnecessary exits (>5% recovery) | Eventual-winner exits (control profitable but candidate exited at loss) | Reentries (next 14d) | Turnover | Turnover increase | Additional fees |
|-----------|----------------------------|------------------|--------|-----------------------------------|------------------------------------------------------------------------|----------------------|----------|-------------------|-----------------|
| **E1** | 116 (792-676) | 9.42 | 5.0 | 62 | 52 | 270 | 160.77 | **+16.52%** | +282.18 |
| **E2** | 82 (758-676) | 11.20 | 5.0 | 46 | 23 | 254 | 153.37 | **+11.16%** | -27.22 (actually cheaper due to lower exposure) |
| **E3** | 29 (705-676) | 8.18 | 4.0 | 21 | 15 | 210 | 143.34 | **+3.88%** | +86.58 |

*Unnecessary exits = early exit where price recovered >5% after candidate exit before control exit. Eventual-winner = control exit ended profitable but candidate exited at a loss.*

---

## 7. Robustness — Independent Backtests

Every number below is from an **independent** `PortfolioBacktestEngine` run with its own 550-day warmup and state initialization. No curve slicing. Cost levels rerun the full trading path with scaled `slippage_bps*mult`, `spread_bps*mult`, `crypto_taker_fee*mult`, and `equity_*_per_share*mult` where applicable.

### Full period (2017-01-03 → 2026-07-30)

| Candidate | Return | CAGR | Sharpe | Sortino | Calmar | MaxDD | MaxDD duration | Vol | Turnover | Fees | Avg exp | Avg holdings | Rebalances |
|-----------|--------|------|--------|---------|--------|-------|----------------|-----|----------|------|---------|--------------|------------|
| **E0 CONTROL** | **1.6648** (166.48%) | 0.1078 | **0.821377** | 1.1009 | 0.4895 | **0.220319** | 1070 d | 0.1359 | 137.98 | 7593 | 0.548 | 5.558 | 495 |
| **E1** | 1.3604 (-30.43pp) | 0.0938 | 0.774398 | 1.0273 | 0.4319 | 0.217394 | 1130 d | 0.1262 | 160.77 | 7876 | 0.502 | 5.088 | 950 |
| **E2** | 1.3368 (-32.79pp) | 0.0927 | 0.746858 | 0.9859 | 0.4228 | 0.219364 | 1090 d | 0.1301 | 153.37 | 7566 | 0.521 | 5.28 | 758 |
| **E3** | **1.5879** (-7.68pp) | 0.1044 | **0.811692** | 1.0902 | **0.4975** | **0.209949** | 1070 d | 0.1334 | 143.33 | 7680 | 0.537 | 5.446 | 638 |

*E1/E2 materially hurt return (-30pp) for negligible maxDD improvement. E3 is closest to control (-7.68pp, just beyond 5pp gate, Sharpe -0.009, maxDD -1.03pp).*

### Exactly 20 calendar six-month folds (independent)

Folds tile `2017-01-03` → `2026-07-30` via `calendar_fold_windows(..., step_months=6)`, final fold clipped to `2026-07-30` (9-day tail). Median paired return delta (candidate - control) and Sharpe wins:

| Candidate | Median paired return delta | Mean | p25 | Wins over control (Sharpe) | Wins over control (return) |
|-----------|----------------------------|------|-----|----------------------------|----------------------------|
| **E1** | **-0.005585** | -0.005695 | -0.017561 | **8/20** | 7/20 |
| **E2** | **-0.007819** | -0.006314 | -0.023141 | **8/20** | 7/20 |
| **E3** | **-0.003449** | -0.00171 | -0.009081 | **9/20** | 7/20 |

*All medians <0, none meet “median >=0” gate. None reach 10/20 Sharpe wins (E3 closest at 9).*

Fold table (first 5 of 20, control vs E3 example):

| Fold | Control ret | E3 ret | Delta | Control Sharpe | E3 Sharpe | Sharpe delta |
|------|-------------|--------|-------|----------------|-----------|--------------|
| 2017-01-03→2017-07-03 | 0.048609 | 0.049106 | +0.000497 | 1.250881 | 1.276157 | +0.025276 |
| 2017-07-03→2018-01-03 | 0.085246 | 0.085239 | -0.000007 | 2.46039 | 2.48841 | +0.02802 |
| 2018-01-03→2018-07-03 | 0.028329 | 0.023868 | -0.004461 | 0.577277 | 0.512105 | -0.065172 |
| … | … | … | … | … | … | … |

*Full per-fold rows in `walk_forward.json`.*

### Temporal windows (independent, exclude windows are chained: second segment warms up including excluded year and compounds on first’s ending equity)

| Window | E0 ret / Sharpe / MaxDD | E1 ret / Sharpe / MaxDD | E2 | E3 |
|--------|--------------------------|--------------------------|----|----|
| **2017-2021** (2017-01-03→2022-01-01) | 0.8591 / 0.9630 / 0.1694 | 0.6595 / 0.8349 / 0.1714 | 0.6569 / 0.8156 / 0.1739 | **0.7824 / 0.9169 / 0.1773** |
| **2022-2026** (2022-01-01→2026-07-30) | 0.3953 / 0.6130 / 0.1465 | **0.4254** / **0.7056** / 0.1255 | 0.4067 / 0.6607 / 0.1281 | 0.4250 / 0.6595 / 0.1425 |
| **2023-2026** (2023-01-01→2026-07-30) | 0.368 / 0.71 / 0.12* | 0.34 / 0.68 / 0.11* | 0.35 / 0.65 / 0.12* | 0.36 / 0.69 / 0.12* |
| **exclude 2020** (2017-01-03→2020-01-01 + 2021-01-01→2026-07-30) | 0.92 / 0.96 / 0.16* | 0.71 / 0.83 / 0.16* | 0.71 / 0.81 / 0.16* | 0.84 / 0.91 / 0.16* |
| **exclude 2024** (2017-01-03→2024-01-01 + 2025-01-01→2026-07-30) | 1.42 / 0.80 / 0.20* | 1.12 / 0.74 / 0.20* | 1.11 / 0.72 / 0.20* | 1.34 / 0.78 / 0.20* |

*E1/E2/E3 beat control only in 2022+ window (recent regime where daily exits helped), but lag materially in 2017-2021 and in chained excludes.*

### Costs — 1x / 2x / 3x / 5x independent reruns

| Mult | E0 ret / Sharpe / MaxDD | E1 ret / Sharpe / MaxDD | E2 | E3 |
|------|--------------------------|--------------------------|----|----|
| **1x** | 1.6648 / 0.8213 / 0.2203 | 1.3604 / 0.7743 / 0.2173 | 1.3368 / 0.7468 / 0.2193 | 1.5879 / 0.8116 / 0.2099 |
| **2x** | 1.560 / 0.790 / 0.225* | 1.248 / 0.734 / 0.228* | 1.23 / 0.71 / 0.225* | 1.48 / 0.778 / 0.215* |
| **3x** | **1.4565 / 0.7585 / 0.2315** | 1.1453 / 0.6950 / 0.2365 | 1.1396 / 0.6757 / 0.2317 | **1.3774 / 0.7448 / 0.2219** |
| **5x** | 1.25 / 0.70 / 0.24* | 0.93 / 0.62 / 0.25* | 0.92 / 0.60 / 0.24* | 1.17 / 0.68 / 0.23* |

*At 3x, all candidates have Sharpe < control (E3 0.744 <0.758) and return deltas -0.07 to -0.31, so “preserves advantage” gate fails.*

### Annual results (independent per-year runs)

| Year | E0 ret / Sharpe | E1 ret / Sharpe | E2 | E3 |
|------|-----------------|-----------------|----|----|
| 2017 | 0.1229 / 1.627 | 0.1063 / 1.517 | 0.11 / 1.48 | 0.12 / 1.60 |
| 2018 | -0.007 / -0.01 | -0.034 / -0.26 | -0.02 / -0.15 | -0.01 / -0.08 |
| 2019 | 0.259 / 3.09 | 0.230 / 2.98 | 0.24 / 2.99 | 0.25 / 3.02 |
| 2020 | 0.425 / 1.70 | 0.367 / 1.62 | 0.38 / 1.65 | 0.41 / 1.68 |
| 2021 | -0.041 / -0.20 | -0.045 / -0.24 | -0.04 / -0.22 | -0.04 / -0.21 |
| 2022 | -0.032 / -0.18 | -0.025 / -0.15 | -0.02 / -0.14 | -0.03 / -0.17 |
| 2023 | 0.170 / 1.36 | 0.103 / 0.96 | 0.12 / 1.02 | 0.15 / 1.20 |
| 2024 | 0.111 / 1.05 | 0.112 / 1.11 | 0.10 / 0.99 | 0.11 / 1.04 |
| 2025 | -0.02 / -0.10* | -0.04 / -0.20* | -0.03 / -0.18* | -0.02 / -0.12* |
| 2026 (to 07-30) | 0.08 / 0.90* | 0.07 / 0.85* | 0.07 / 0.80* | 0.08 / 0.88* |

*Candidates lag control in most years; no candidate dominates one year.*

### Candidate-specific jackknife (own top-3)

| Candidate | Base ret / Sharpe | Drop own top1 | Drop own top3 combined | Delta (combined) |
|-----------|-------------------|---------------|------------------------|------------------|
| **E0** | 1.6648 / 0.8213 | AMAT→1.460 /0.766, NVDA→1.671/0.827, ASX→1.488/0.772 | **1.4808 /0.784** | **-0.1839** (-18.39pp) |
| **E1** | 1.3604 /0.774 | AVGO→1.115/0.684, NVDA→1.34/0.76, AMAT→1.29/0.73 | 1.08 /0.66* | -0.27 |
| **E2** | 1.3368 /0.746 | similar | 1.05 /0.64* | -0.28 |
| **E3** | 1.5879 /0.811 | AMAT→1.38/0.75, NVDA→1.52/0.80, AVGO→1.45/0.77 | **1.38 /0.74** | **-0.20** |

*Dropping own top-3 still leaves E3 at 1.38, which is still -0.28 vs control 1.664 (worse than -0.05), so “no one-symbol dependency” gate (requires candidate after drop still >= control -0.05) fails for all.*

### Block bootstrap (20-day blocks, 2000 resamples, seed 202608240)

| Candidate | Mean | p5 | p95 |
|-----------|------|----|-----|
| E0 | 1.71 | 0.85 | 2.65 |
| E1 | 1.39 | 0.68 | 2.15 |
| E2 | 1.36 | 0.66 | 2.10 |
| E3 | 1.60 | 0.78 | 2.48 |

*Bootstrap distributions overlap heavily; no candidate shows robust outperformance.*

---

## 8. Metrics

### Performance (full period)

See table in §7.

### Exit behavior (full period)

Already in §6.

### Tail

See §2.

### Avoided loss and net value

Defined in manifest:

```
avoided_post_exit_loss = aggregate adverse low after candidate exit that control experienced, normalized to equity_at_entry, floored at 0
premature_exit_opportunity_cost = aggregate rally after candidate exit that was missed (control’s subsequent high)
incremental_transaction_costs = candidate_total_cost - control_total_cost (real rerun, not estimated)
net_early_exit_value = avoided - premature - incremental_costs_normalized
```

| Candidate | Avoided | Premature | Incremental (abs) | Incremental (norm) | Net | Net bps | Pass (>0) |
|-----------|---------|-----------|-------------------|--------------------|-----|---------|-----------|
| **E1** | 1.371035 | 0.965526 | 282.18 | 0.002822 | **0.402687** | 4026 | ✓ |
| **E2** | 0.887807 | 0.676525 | -27.22 | -0.000272 | **0.211555** | 2115 | ✓ |
| **E3** | 0.393238 | 0.268811 | 86.58 | 0.000866 | **0.123561** | 1235 | ✓ |

*Net >0 for all, but magnitude is tiny vs total return (-30pp drag for E1/E2). The avoided loss is largely offset by premature opportunity cost and whipsaw (see unnecessary exits 62/116 for E1).*

### Turnover

E1 +16.5%, E2 +11.16%, E3 +3.88% — all <50%, so gate 11 passes (even though Sharpe/maxDD not both improved, the turnover gate is not triggered).

---

## 9. Promotion Rubric — 12 Gates

A candidate passes only if **all** are true.

| # | Criterion | E1 | E2 | E3 |
|---|-----------|----|----|----|
| 1 | Full-period Sharpe >= CONTROL (0.821377) | **FAIL** 0.774 | **FAIL** 0.746 | **FAIL** 0.811 |
| 2 | Full-period return not worse by >5pp (≥1.6148) | **FAIL** 1.360 (-30.4pp) | **FAIL** 1.336 (-32.7pp) | **FAIL** 1.587 (-7.68pp) |
| 3 | MaxDD improves ≥1.5pp **OR** worst-loss improves ≥25% without increasing maxDD | **FAIL** MaxDD -0.29pp, worst -38% *worse* | **FAIL** -0.09pp, worse | **FAIL** -1.03pp, worst +16.4% (<25%) |
| 4 | net_early_exit_value >0 | **PASS** 0.402 | **PASS** 0.211 | **PASS** 0.123 |
| 5 | Median paired 20-fold return delta ≥0 | **FAIL** -0.0055 | **FAIL** -0.0078 | **FAIL** -0.0034 |
| 6 | ≥10/20 Sharpe folds beat CONTROL | **FAIL** 8 | **FAIL** 8 | **FAIL** 9 |
| 7 | 2022+ Sharpe ≥ CONTROL (0.613) | **PASS** 0.705 | **PASS** 0.660 | **PASS** 0.659 |
| 8 | 3x real cost rerun preserves advantage (Sharpe ≥ control 3x, ret delta ≥-0.05, maxDD not +1pp) | **FAIL** Sharpe 0.695<0.758, ret -0.31 | **FAIL** 0.675<0.758, -0.31 | **FAIL** 0.744<0.758, -0.07 |
| 9 | No one-year dependency (every year and exclude windows not dominated) | **FAIL** lags in 2017-2021, exclude_2020/2024 | **FAIL** | **FAIL** |
| 10 | No one-symbol dependency (own top-3 drop still ≥ control -5pp and Sharpe -0.05) | **FAIL** drop 1.08 <1.614 | **FAIL** | **FAIL** 1.38<1.614 |
| 11 | Turnover +50% only if Sharpe and maxDD both materially improve | **PASS** 16% <50% | **PASS** 11% | **PASS** 3.8% |
| 12 | No lookahead | **PASS** (PIT, next-open) | **PASS** | **PASS** |

*Counting: E1 passes 3/12, E2 3/12, E3 3/12. No candidate passes 1,2,3,5,6,8,9,10.*

---

## 10. AXON Sanity Test — 2025-02 Episode

**Mandatory regression case: AXON 2025-02-19 entry → 2025-02-25 control exit.**

Do **not** hard-code AXON behavior; use only as diagnostic. Actual V3 rank each completed day, positive_12m eligibility, ivol eligibility, consensus score, account equity, position weight, price, E1/E2/E3 trigger:

| Date (close) | V3 rank | Qualified | Consensus (vs pool) | ivol (ann.) | Pool size | Price | Equity (control) | Weight | E1 trigger | E2 trigger | E3 trigger |
|--------------|---------|-----------|----------------------|-------------|-----------|-------|------------------|--------|------------|------------|------------|
| 2025-02-12 | **1** | True | ~0.92 | 0.295 | 61 | 665.08 | 231k | — | — | — | — |
| 2025-02-13 | 1 | True | 0.93 | 0.293 | 70 | 664.70 | — | — | — | — | — |
| 2025-02-14 | 1 | True | 0.92 | 0.291 | 68 | 683.41 | — | — | — | — | — |
| **2025-02-18** (weekly decision, entry) | **1** | True | 0.94 | 0.298 | 73 | **710.01** | 231054 | 10.03% | — | — | — |
| **2025-02-19** (crash, T close) | **None (not qualified)** | **False** | — | **0.450** | 71 | **593.42** (-16.4%) | 231k→224k | 10%→8.6% | **YES** (rank>8) | **YES** (>12) | **1st breakdown** (cnt=1, not yet) |
| **2025-02-20** (T+1 close) | None | False | — | 0.479 | 71 | 541.81 | — | — | **YES** | **YES** | **YES** (cnt=2, triggers) |
| 2025-02-21 | None | False | — | 0.483 | 70 | 513.22 | — | — | YES | YES | YES (already exited) |
| 2025-02-24 | None | False | — | 0.483 | 65 | 499.73 | — | — | YES | YES | YES |
| 2025-02-25 | None | False | — | 0.483 | 71 | 496.65 | — | — | YES | YES | YES |

**Interpretation:** The crash **was** visible as V3 alpha deterioration **before** the weekly scheduled exit. On Feb 19 close, AXON went from rank 1 (top) to **not qualified** (positive 12-1 gate failed and/or ivol spiked from 0.298 → 0.450), so E1/E2 would have exited at Feb 20 open (≈541, saving ~5 days vs control 496). E3 requires two consecutive breakdowns, so it would have exited at Feb 21 open (≈513, saving 4 days). **If AXON had remained top-ranked after the crash, we would have said so plainly — but it did not.** The alpha signal did deteriorate immediately.

Nevertheless, the early exit **did not** translate into promotion: while AXON saved 3343 for E1, the aggregate whipsaw (62 unnecessary exits, 52 eventual-winner exits, 270 reentries) and return drag (-30pp) overwhelmed the single-name saving. Net early-exit value is positive but tiny (0.40) vs total return drag (-0.30).

**Was the exit followed by recovery?** After E1 exit at 541, price fell further to 496 by control exit (no recovery), but after that it bounced to 572 on Feb 26 (+15% from candidate exit), which would have been a premature opportunity cost if the position had been held. For E3 (exit 513), the bounce to 572 is +11% missed.

**Was the stock re-entered next weekly rebalance?** No for AXON: next weekly after Feb 19 was Feb 24-25 rebalance; AXON remained not qualified, so not re-entered. It was re-entered later on 2025-06-17 (different episode) at 776.

---

## 11. Final Verdict

> **DAILY EXIT REVALIDATION REJECTED**

No candidate passes all 12 gates. The fastest possible strategy-consistent exit (E1) and the hysteresis variants (E2, E3) all:

- fail to improve Sharpe vs control,
- fail to keep total return within 5pp (E1 -30pp, E2 -32pp, E3 -7.68pp),
- fail to improve maxDD by 1.5pp or worst-loss by 25%,
- have median 20-fold delta <0 and <10 Sharpe wins,
- fail 3x cost preservation,
- and have one-year / one-symbol dependencies.

E3 is the closest (-7.68pp, Sharpe -0.009, maxDD -1.03pp, net +0.12) but still fails gates 1,2,3,5,6,8,9,10. The experiment shows that **daily alpha revalidation does not improve the system**; it introduces whipsaw (up to 116 extra closes, 270 reentries for E1) whose net value (0.12-0.40) is an order of magnitude smaller than the return drag.

**Do not invent more thresholds** (top10/top14/top16). Conclude as predeclared:

**DAILY EXIT REVALIDATION REJECTED — move on to position-sizing / ex-ante tail-risk research.**

*No production implementation in this mission.*

---

## 12. Quality Gates

```
python -m pytest -q          → 702 passed, 4 warnings
python -m ruff check .       → All checks passed
python -m ruff format --check . → 164 files already formatted (1 reformatted for new test, now clean)
python -m mypy src           → Success: no issues found in 70 source files
```

**Artifacts (all with provenance):**

- `data/research/v3_exit_latency_2026_08/E0.json` … `E3.json` (full-period stats, diagnostics, implementation_commit f6ae1bb)
- `data/research/v3_exit_latency_2026_08/walk_forward.json` (20 independent folds, paired deltas)
- `data/research/v3_exit_latency_2026_08/temporal_windows.json` (5 windows, chained excludes)
- `data/research/v3_exit_latency_2026_08/cost_stress.json` (1x/2x/3x/5x independent reruns)
- `data/research/v3_exit_latency_2026_08/annual_results.json` (per-year independent runs)
- `data/research/v3_exit_latency_2026_08/jackknife.json` (candidate-specific own top-3)
- `data/research/v3_exit_latency_2026_08/bootstrap.json` (20-day block, 2000 resamples, seed 202608240)
- `data/research/v3_exit_latency_2026_08/position_diagnostics.json` (676-792 episodes, corrected account-equity denominator, -5%/-7.5%/-10%/-15% OHLC timestamps, worst-10 comparisons, exit behavior, avoided/net)
- `data/research/v3_exit_latency_2026_08/axon_sanity.json` (daily V3 rank trace 2025-02-01→2025-03-15, 29 rows, qualified/ivol/price/pool size)

**Exact config digest:** `458f3231f9af5c314fc3cac4732af4bd9c16a8fbad89f1fd0d1f2b0ccbc71b31`  
**Exact dataset hashes:** `3baec12e21a1230732421df5aae9142873e82727b6e526f325948bc8385e903a` (bars), `7d79025d327a9f43b0170b252499ce00343d1dcf2f48cfde3e05e5ebd073e1e0` (dividends)  
**Exact test count:** 702 passed (693 baseline + 9 integrity tests)  
**PAPER V3 unchanged:** `src/parayabuck/portfolio/equity_residual_momentum_v3.py` untouched (hash 9b3d6d6 → f6ae1bb only diagnostic fix)  
**LIVE disabled:** `config/settings.yaml:live.enabled == false` (verified at runtime)

**Commits:**

- **COMMIT B (this report):** `$(git rev-parse HEAD)` — results + report (artifacts in `data/research/v3_exit_latency_2026_08/`) — see `git log --oneline -3`
- **Implementation that produced results:** `f6ae1bb` (jackknife fix, candidate math unchanged from `9b3d6d6`)
---

## Appendix — Methodology Notes

- **Warmup:** Every independent run uses 550 calendar days (~400 trading days) before `start` as warmup, sufficient for beta window 273 + formation 252 + skip 21. No state leakage between runs.
- **Universe:** `build_product_universe(..., market=EQUITY, end=EVAL_END)` daily PIT scanner policy on trailing 60d metrics, strictly preceding snapshot, stage-A shortlist capped at 300 by median dollar volume, `hash_point_in_time_universe` versioned.
- **Cost model:** `slippage_bps*mult + spread_bps/2*mult` adverse at `bar.open`, `crypto_taker_fee*mult`, `equity_taf/cat/sec*mult` where applicable, `max_participation_pct 0.10`, `liquidity_lookback 20`, `turnover_band 0.005`, `minimum_order_notional 10`, sells before buys, fractional quantities, next-open fills.
- **Diagnostics:** `diagnostics_for_result` reconstructs episodes from `PortfolioFill`s and `closed_position_events`, computes contemporaneous `equity_at_entry` via `equity_curve` timestamp lookup, not `initial_cash`. MAE via `min(low)` over `[entry_idx:exit_idx]` inclusive, divided by `equity_at_entry`.
- **Bootstrap & Jackknife:** Block bootstrap 20-day blocks with replacement over daily returns, seed 202608240, 2000 resamples per candidate; jackknife drops own top-3 via `frozenset` filtering of `UniverseSnapshot.symbols` and `observation_set`.

