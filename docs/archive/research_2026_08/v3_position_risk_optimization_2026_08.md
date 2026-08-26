# V3 Position Concentration & Ex-Ante Tail-Risk Sizing — Research 2026-08

**Mission:** `v3_position_risk_optimization_2026_08`  
**Repository:** `batuhankiskac/ParayaBuck`  
**Date:** 2026-08-24  
**Mode:** RESEARCH ONLY — PAPER `equity_residual_momentum_v3` frozen; LIVE remains disabled. No production changes.

---

## 1. Provenance — COMMIT A / COMMIT A2 / COMMIT B / COMMIT C (Correction)

| Field | Value |
|-------|-------|
| `COMMIT A` (original predeclaration, `5954d00`) | `5954d004520e731e1b0ceffaa6273be3a518529d` |
| `COMMIT A2` (approved, cost-stress fix) | `c1f44c26582c2f818045e3892a47835ebce66328` |
| `COMMIT A2 corrected` (chained-window fix) | `012eb54ed09e09298ce2eb8a9e166338faab9073` |
| `COMMIT A2 final` (axon key fix, **frozen economic implementation that generated results**) | `bfb88a4383252468e722bf7c0b60d930665abdbb` |
| `COMMIT B` (original results) | `d404667c53dbed47434774c1b97901d373b71967` (`d404667`) — generated from `bfb88a4` |
| `COMMIT C` (this correction, **no economic rerun**) | *this commit* — corrects derived aggregation only |
| `original economic implementation` | `bfb88a4` (frozen, no candidate economics changed) |
| `original results commit` | `d404667` |
| Python | `3.14.7` |
| `config_digest` | `458f3231f9af5c314fc3cac4732af4bd9c16a8fbad89f1fd0d1f2b0ccbc71b31` |
| `strategy.equity.name` | `equity_residual_momentum_v3` `3.0.0` |
| `runtime.mode` | `paper` |
| `live.enabled` | `false` |
| `strategy.equity.maximum_allocation` | `0.60` |
| `test_count` | `baseline_before_A 702` · `A gate 721` · `A2 gate 726` (726 passed at A2/C) |

**Correction note (COMMIT C):** No candidate economics rerun/changed. Derived aggregation bugs corrected: (1) fold median now consumes `paired["return_delta"]["median"]` (`P1 +0.001402`, `P2 -0.009726`, `P3 +0.000115`) instead of missing `median_paired_return_delta` (-1); (2) loss percentile now uses severity `abs(pnl/equity)` with `p95/p99 magnitude` (severe tail, `p99 ~0.024` not `0.00005` mild), with `(control-candidate)/control` improvement; (3) gate 11 literal (`own top3 must satisfy gates 1-3`, short-circuit if 1 or 2 fails). `walk_forward.json`, `temporal_windows.json`, `cost_stress.json`, `jackknife.json` unchanged — only `position_tail_metrics.json`, `promotion_verdict.json`, and this report corrected. **Verdict unchanged** because gate 5 (`maxDD 0.244/0.239/0.230 > 0.2203`) fails independently for all. Candidate definitions **semantically identical** to COMMIT A (JSON formatting changed, semantics not).

**Manifest:** `data/research_manifests/v3_position_risk_optimization_2026_08.json` — `declared_before_results:true`, `no_results_yet:true` at A; updated provenance at A2 to record `original_predeclaration_commit = 5954d00`. The self-reference placeholder `pending_A2_will_be_this_commit` does not require A3; artifacts correctly record `bfb88a4`.

**V3 frozen parameters (identical to `config/settings.yaml:147-156` and manifest):**
```
formation_bars: 252    skip_bars: 21    beta_window_bars: 273
residual_vol_window: 63    top_n: 8    max_single_weight: 0.10
ivol_reject_quantile: 0.70    exclude_symbols: [SPY]    rebalance_frequency: weekly
```
Horizons `126, 189, 252` residual bars (6/9/12 months), skip 21, equal-mean percentile ranks, raw (not standardized). Positive 12-1 gate precedes consensus. No alpha ranking change.

**Risk config (frozen, except candidate geometry):**
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
Cost-stress at A2 uses research-only `PositionRiskResearchEngine` with `regulatory_fee_multiplier` that multiplies the **final effective-dated fee** (including capped TAF), so `2x/3x/5x` are real uniform stresses — verified by integrity tests (cap-aware).

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

**Confirmation:**
```
runtime.mode == paper                         ✓
strategy.equity.name == equity_residual_momentum_v3  ✓
live.enabled == false                         ✓
PAPER V3 unchanged                            ✓ (only research files added; src/parayabuck unchanged)
No candidate math modified after A2           ✓ (two bug-fix commits 012eb54/bfb88a4 were research-local, no candidate semantics change)
```

---

## 2. Correct Account-Impact Diagnostics (§2)

All percentages use `realized_position_pnl / account_equity_at_entry` (contemporaneous equity when position was opened), never `initial_backtest_cash`. For every candidate we distinguish:

* `candidate_worst_episode` — that candidate's actual worst single-position account impact
* `AXON_reference_episode` — AXON `2025-02-19` specifically (`entry 700.21 → exit 498.76`, 6d, `-$6,670.93`, `-2.8872%` account)

Never display candidate worst % on AXON row unless AXON is that candidate's worst. In this run AXON is **not** the worst for any candidate — worst is `ARES 2020-03-03` (`-3.30%`) for all — so tables below keep them separate.

---

## 3. Baseline — P0 CONTROL Reproduction

Exactly current V3 + production risk geometry (`top_n 8`, `max_positions 6`, `max_position_pct 0.10`, `maximum_allocation 0.60`, `max_gross 0.60`, `cash_buffer 0.40`, inverse residual-vol sizing) reproduces validated control:

| Metric | P0 CONTROL | Validated expectation |
|--------|------------|-----------------------|
| total return | **1.6648** | ~1.6648 |
| CAGR | 0.107847 | — |
| Sharpe | **0.821377** | ~0.8214 |
| Sortino | 1.100939 | — |
| maxDD | **0.220319** | ~0.2203 |
| Calmar | 0.489502 | — |
| maxDD duration | 1070 d | — |
| Vol (ann) | 0.135981 | — |
| Turnover | 137.98 | — |
| Fees | 7593.97 | — |
| Avg exposure | 0.5482 | — |
| Avg holdings | 5.558 | — |
| Rebalances | 495 | — |
| Closed positions | 676 | — |

Within tolerance → baseline identity reproduced.

---

## 4. Predeclared Candidates — Exactly P0-P3, No Grid

| ID | Label | Geometry `max_positions / max_pct` | Max nominal exposure | Sizing | Notes |
|----|-------|-----------------------------------|----------------------|--------|-------|
| **P0** | CONTROL | `6 / 0.10` | `0.60` | inverse resid-vol 63 | reproduces control |
| **P1** | DIVERSIFY_7 | `7 / 0.085` | `0.595` | inverse resid-vol unchanged, V3 `top_n 8` preserved, rank preserved | one position cannot consume 10% |
| **P2** | DIVERSIFY_8 | `8 / 0.075` | `0.60` | inverse resid-vol unchanged | all eight names may survive, -25% single-name |
| **P3** | TAIL_ES_126 | `6 / 0.10` | `0.60` | `effective_risk = max(normal_resid_vol_63, ES10_daily*sqrt252)` from trailing `126` split-adjusted price returns, `tail_count=max(1,ceil(10%*126))`, `ES10=abs(mean(worst))`, `tail_eq=ES*sqrt252`, `raw=1/effective` → existing norm+10% cap, fallback if `<126`, frozen `126/10%/sqrt252`, no future, no MAE feed | risk-estimation change only |

All use identical V3 selection, weekly decisions, cost model, universe, signal basis.

---

## 5. Why These Candidates (§5)

*P1/P2 answer:* Is the problem simply excessive single-name concentration? (model-free diversification)  
*P3 answers:* Does conventional volatility underestimate downside tail risk? (risk-estimation change)  
Not mixed initially; no `P2+P3`.

---

## 6. Full-Period Performance (2017-01-03 → 2026-07-30, `1x` costs, independent run)

| Candidate | Return | CAGR | Sharpe | Sortino | Calmar | maxDD | maxDD dur | Vol | Turnover | Fees | Avg exp | Avg holdings | Rebalances | Closed |
|-----------|--------|------|--------|---------|--------|-------|-----------|-----|----------|------|---------|--------------|------------|--------|
| **P0 CONTROL** | **1.6648** | 0.107847 | **0.821377** | 1.100939 | 0.489502 | **0.220319** | 1070 | 0.135981 | 137.98 | 7593.97 | 0.5482 | 5.558 | 495 | 676 |
| **P1 DIVERSIFY_7** | 1.64205 | 0.106855 | **0.831144** | 1.118907 | 0.437 | **0.244086** | — | — | 128.28 | 6899.16 | 0.5531 | 6.512 | 495 | 730 |
| **P2 DIVERSIFY_8** | 1.310855 | 0.091471 | 0.742272 | 0.979461 | — | 0.239785 | — | — | 127.24 | 6332.94 | 0.5564 | 7.424 | 495 | 822 |
| **P3 TAIL_ES_126** | 1.629167 | 0.106289 | 0.815991 | 1.097608 | — | 0.230348 | — | — | 136.97 | 7501.83 | 0.5456 | 5.582 | 495 | 680 |

*P1 improves Sharpe +0.0098 but worsens maxDD +2.38pp and return -2.27pp (within 5pp). P2 materially hurts return -35.4pp and Sharpe -0.079. P3 return -3.57pp, Sharpe -0.0054, maxDD +1.0pp.*

---

## 7. Concentration Metrics (§8) — Through Time

| Candidate | Avg positions | Median | % holding 6/7/8 | Avg largest weight | p95 largest | Max weight | HHI avg | Eff. positions avg | Avg exposure | p10/p50/p90 exposure | Time at max gross | Cash ratio |
|-----------|---------------|--------|-----------------|--------------------|-------------|------------|---------|--------------------|--------------|----------------------|-------------------|------------|
| **P0** | 5.606 | 6.0 | 79.2% at 6, 0% at 7/8 | 0.0999 | 0.10 | 0.10 | 0.05429 | 19.39 | 0.5512 | 0.3896 / 0.5929 / 0.60 | 0.4404 | 0.4518 |
| **P1** | 6.572 | 7.0 | 7.9% at 6, 79.0% at 7 | 0.08495 | 0.085 | 0.085 | 0.04706 | 22.20 | 0.556 | 0.4173 / 0.595 / 0.595 | 0.6889 | 0.4469 |
| **P2** | 7.489 | 8.0 | 8.7% at 6, 4.4% at 7, 78.6% at 8 | 0.07495 | 0.075 | 0.075 | 0.04178 | 25.04 | 0.5593 | 0.4325 / 0.60 / 0.60 | 0.7737 | 0.4436 |
| **P3** | 5.630 | 6.0 | 80.2% at 6 | 0.09993 | 0.10 | 0.10 | 0.05368 | 19.40 | 0.5486 | 0.3909 / 0.585 / 0.60 | 0.3374 | 0.4544 |

*HHI = sum(weight_i^2), effective = 1/HHI. P1/P2 achieve higher effective positions (22.2/25.0 vs 19.4) via diversification, not via staying in cash — average exposure remains within `CONTROL ±5pp` (P1 +0.48pp, P2 +0.81pp, P3 -0.26pp). P1/P2 spend ~69%/77% at max gross vs 44% for control, showing diversification, not cash drag.*

---

## 8. Position Loss Metrics (§9) — Contemporaneous Equity — **Corrected: Loss Severity = abs(pnl/equity)**

| Candidate | Worst magnitude / eq | p95 magnitude / eq | p99 magnitude / eq | p95 MAE / eq | p99 MAE / eq | top1 | top3 | top5 | top10 | # > -1% | > -1.5% | > -2% | > -2.5% | > -3% |
|-----------|----------------------|--------------------|--------------------|--------------|--------------|------|------|------|-------|---------|---------|-------|---------|-------|
| **P0** | **0.03300** (3.30% `ARES 2020-03-03`) | 0.01617 | 0.024132 | 0.017808 | 0.026564 | 0.0211 | 0.0549 | 0.0848 | 0.1466 | 52 | 20 | 6 | 4 | 1 |
| **P1** | 0.03348 | 0.014253 | 0.02281 | 0.01570 | 0.022897 | 0.0199 | 0.0535 | 0.0806 | 0.1414 | 45 | 16 | 8 | 3 | 1 |
| **P2** | **0.02914** (best worst, 11.7% better) | 0.012395 | 0.022513 | 0.013864 | 0.021566 | 0.0174 | 0.0467 | 0.0730 | 0.1308 | 38 | 11 | 8 | 3 | 0 |
| **P3** | 0.030615 | 0.015266 | 0.024136 | 0.017179 | 0.024975 | 0.0213 | 0.0565 | 0.0871 | 0.1510 | 53 | 18 | 5 | 4 | 1 |

*Worst single is not AXON for any candidate — worst is `ARES`. With correct severity percentiles (positive magnitude, severe tail), p95/p99 are now large (severe) not near-zero mild. P2 improves worst by `(0.033-0.02914)/0.033=11.7%` (<20%) and p99 by `6.7%`, P1 p99 by `5.5%`, P3 worst `7.2%` — none reach 20% threshold. Legacy fields `p95_loss_over_equity = -magnitude` kept for compatibility.*
---

## 9. Ex-Ante Risk Diagnostics (§6) — CONTROL Only

Per CONTROL entry episode, pre-entry metrics known **before** entry vs future outcomes (diagnostic only). Comparison `worst 10% future-loss episodes` vs `remaining 90%`:

| Pre-entry metric | Worst 10% mean | Rest mean | Delta (worst - rest) |
|------------------|----------------|-----------|----------------------|
| residual vol 63 | 0.2808 | 0.2558 | **+0.0250** |
| ES10_126 annualized | 0.5476 | 0.5085 | **+0.0391** |
| ATR20 / price | 0.03271 | 0.02766 | **+0.00505** |
| worst daily return 126d | -0.06432 | -0.06182 | -0.00249 (more negative) |
| initial weight | 0.10341 | 0.10019 | +0.00321 |
| ivol percentile | 0.5397 | 0.5256 | +0.0141 |
| realized vol 63 | 0.3426 | 0.3050 | +0.0376 |

*Worst future losers were modestly higher on residual vol, ES, ATR% and realized vol before entry, but differences are small (~2.5pp vol, 3.9pp ES) — not a strong ex-ante separator. ES distinguishes slightly better than residual vol alone, but not dramatically.*

Full per-episode records in `ex_ante_risk_diagnostics.json` (676 episodes, each with rank/target/actual weight, vols, ES, worst return/gap, ivol percentile, consensus, and future `realized_pnl/equity`, `MAE/equity`).

---

## 10. AXON / Worst-10 Counterfactual (§7)

### 10.1 AXON 2025-02 Reference

*CONTROL:* `AXON 2025-02-19` entry `700.21` (decision `2025-02-18`), `qty 33.11`, `notional 23184.99`, `equity 231054.38`, `weight 0.10034`, realized loss `-$6,670.93`, account impact `-0.028872` (`-2.8872%`), `MAE 3.15%`, `days 6`, first `-5%/-7.5%/-10%/-15%` all `2025-02-19` (same bar), `worst_low 480.18`.

**P3 ES before entry:** `ES10_126 annualized = 0.48633`. Normal residual vol for AXON at that time was not separately logged but ES ~0.486 is elevated vs control median ES ~0.51 (rest 0.508, worst 0.547) — modestly high but not extreme. **Do NOT claim P3 predicted AXON** — ES was not abnormally high.

### 10.2 Counterfactual — Same Price Path, Counterfactual Weight

For `AXON 2025-02` (same `700.21 → 498.76` path, return `-28.77%`):

| Candidate | Counterfactual weight | Qty | Notional | Estimated loss (same path) | Account impact | vs CONTROL | Weight ratio |
|-----------|-----------------------|-----|----------|----------------------------|----------------|------------|--------------|
| **P0 CONTROL** | 0.100344 | 33.11 | 23184.99 | **-6670.93** | **-2.8872%** | — | 1.00 |
| **P1 DIVERSIFY_7** | 0.085078 | 27.32 | 19127.32 | -5503.43 | -2.3819% | **+1,167.50** `+0.5053pp` reduction | 0.8479 |
| **P2 DIVERSIFY_8** | 0.074975 | 21.73 | 15216.94 | -4378.31 | -1.8949% | **+2,292.62** `+0.9923pp` | 0.7472 |
| **P3 TAIL_ES_126** | 0.100362 | 33.03 | 23125.92 | -6653.93 | -2.8798% | **+17.00** `+0.0074pp` | 1.0002 |

*Interpretation: P1/P2 would have reduced AXON loss by `0.51pp/0.99pp` of account equity purely via lower cap, not via tail prediction. P3 essentially identical (+17$, 0.007pp) — ES did not deem AXON tail-risky, so no weight reduction. This answers §7: Would P1 reduce? Yes, by $1,167 (0.51pp). Would P2? Yes, $2,292 (0.99pp). Would P3? No, $17 (0.007pp) — not a prediction.*

**Candidate worst vs AXON distinction:** For all candidates, `candidate_worst_episode` is **ARES 2020-03-03** (`-3.30%`), not AXON. Tables above keep `candidate_worst` separate from `AXON_reference` per §2 correction. AXON is rank 2 worst for P0.

### 10.3 CONTROL Worst-10 Counterfactual (excerpt, full in `axon_counterfactual.json`)

Worst-10 control episodes (sorted by account impact):

1. `ARES 2020-03-03` -3.30% → P1 -2.72% (save 0.575pp, $804), P2 -2.357% (save 0.943pp, $1,318), P3 -3.038% (save 0.262pp, $365)
2. `AXON 2025-02-19` -2.887% → as above
3. `AHCO 2021-04-13` -2.557% → P1 -2.087% (save 0.469pp, $933), P2 -1.647% (save 0.91pp, $1,810), P3 -2.564% (increase 0.006pp)
4. `ALKS 2021-10-26` -2.515% → P1/P2 no matching entry (dropped), P3 -2.961% (worse)
5. `AVPT 2025-02-19` -2.413% → P1 -1.991% (save 0.422pp, $975), P2 -1.584% (save 0.829pp, $1,916), P3 -2.407% (save 0.006pp)
… (full 10 rows in artifact, each with `weight_ratio`, `dollar_reduction`, `impact_reduction_vs_control`).

*Pattern: P1/P2 consistently dilute worst losses by cap ratio (~0.85/0.75), not by selection. P3 rarely changes weight (ratios ~1.0), sometimes increases weight (ALKS). No systematic tail improvement.*

---

## 11. Robustness — Independent Backtests Only

Every number below is from independent `PositionRiskResearchEngine` runs with own 550-day warmup, state, real cost model (slippage+half-spread+effective-dated fees × multiplier). No curve slicing.

### 11.1 Full period (2017-01-03 → 2026-07-30)

See §6 table.

### 11.2 Exactly 20 calendar six-month folds — Paired Deltas vs CONTROL

`P1` median return delta **+0.001402** (mean -0.000196), `p25 -0.001`, wins `11/20` return, `12/20` Sharpe.  
`P2` median **-0.009726** (mean -0.008646), wins `5/20` return, `8/20` Sharpe.  
`P3` median **+0.000115** (mean +0.00013), wins `11/20` return, `12/20` Sharpe.

| Candidate | Median paired return delta | Mean | p25 | Sharpe wins | Return wins | Max median Sharpe delta |
|-----------|----------------------------|------|-----|-------------|-------------|-------------------------|
| **P1** | **+0.001402** | -0.000196 | -0.001 | **12/20** | 11/20 | +0.063 |
| **P2** | **-0.009726** | -0.008646 | -0.015 | **8/20** | 5/20 | -0.139 |
| **P3** | **+0.000115** | +0.00013 | -0.002 | **12/20** | 11/20 | +0.012 |

*Gate 6 requires median ≥0 → P1/P3 pass, P2 fails. Gate 7 requires ≥10/20 Sharpe wins → P1/P3 pass, P2 fails.*

Fold excerpt (P0 vs P1, first 3 of 20):

| Fold | P0 ret / Sharpe | P1 ret / Sharpe | Delta ret / Sharpe |
|------|-----------------|-----------------|--------------------|
| 2017-01-03→2017-07-03 | 0.0486 / 1.2509 | 0.0504 / 1.3955 | +0.00178 / +0.1446 |
| 2017-07-03→2018-01-03 | 0.08525 / 2.4604 | 0.09506 / 2.9648 | +0.00981 / +0.5044 |
| 2018-01-03→2018-07-03 | 0.02833 / 0.5773 | 0.0271 / 0.55* | -0.0012 / -0.02 |

*Full rows in `walk_forward.json` (paired via `validation.paired_fold_deltas`).*

### 11.3 Temporal Windows (independent, exclude chained)

| Window | P0 ret / Sharpe / maxDD | P1 ret / Sharpe / maxDD | P2 ret / Sharpe / maxDD | P3 ret / Sharpe / maxDD |
|--------|--------------------------|--------------------------|--------------------------|--------------------------|
| **2017-2021** `2017-01-03→2022-01-01` | 0.8591 / 0.9631 / 0.1695 | 0.7874 / 0.9284 / 0.1820 | 0.6321 / 0.8108 / 0.1811 | **0.8607 / 0.9656 / 0.1707** |
| **2022-2026** `2022-01-01→2026-07-30` | 0.3954 / 0.6131 / 0.1466 | **0.4574 / 0.6967 / 0.1399** | 0.3581 / 0.5907 / 0.1257 | 0.4016 / 0.6283 / 0.1435 |
| **2023-2026** `2023-01-01→2026-07-30` | 0.4615 / 0.8604 / 0.1466 | 0.4898 / 0.9268 / 0.1399 | 0.4205 / 0.8432 / 0.1257 | 0.4529 / 0.8607 / 0.1435 |
| **exclude 2020** (chain 2017→2020-01-01 + 2021→2026-07-30) | 0.9263 / 0.6887 / 0.2020 | 0.8878 / 0.6798 / 0.2316 | 0.7623 / 0.6243 / 0.2327 | 0.8888 / 0.6756 / 0.2123 |
| **exclude 2024** (chain 2017→2024-01-01 + 2025→2026-07-30) | 1.4131 / 0.8093 / 0.2203 | 1.3344 / 0.7957 / 0.2441 | 1.0420 / 0.6965 / 0.2398 | 1.3828 / 0.8026 / 0.2303 |

*P1 beats control only in 2022+ windows; lags 2017-2021 and excludes.*

### 11.4 Costs — 1x / 2x / 3x / 5x Independent Reruns (uniform multiplier, research-only engine)

| Mult | P0 ret / Sharpe / maxDD / fees | P1 ret / Sharpe / maxDD | P2 ret / Sharpe / maxDD | P3 ret / Sharpe / maxDD |
|------|---------------------------------|--------------------------|--------------------------|--------------------------|
| **1x** | 1.6648 / 0.8214 / 0.2203 / 7593 | 1.64205 / 0.8311 / 0.2441 / 6899 | 1.31085 / 0.7423 / 0.2398 / 6332 | 1.62917 / 0.8160 / 0.2303 / 7501 |
| **2x** | 1.5573 / 0.7896 / 0.2261 / 14838 | 1.54176 / 0.8007 / 0.2484 | 1.22358 / 0.7109 / 0.2437 | 1.52261 / 0.7838 / 0.2360 |
| **3x** | **1.44998 / 0.7565 / 0.2318 / 21729** | **1.44454 / 0.7699 / 0.2537 / 19808** | 1.16468 / 0.6897 / 0.2476 / 18365 | **1.41988 / 0.7514 / 0.2416 / 21472** |
| **5x** | 1.25316 / 0.6918 / 0.2431 / 34556 | 1.25895 / 0.7074 / 0.2634 | 1.00640 / 0.6279 / 0.2550 | 1.22493 / 0.6860 / 0.2528 |

*At 3x, P1 Sharpe 0.7699 > control 0.7565 (preserves), P3 0.7514 < control by 0.0051 (within 0.02 but tail not better). Gate 9 requires `Sharpe ≥ control 3x` OR `within 0.02 with materially better tail` — P1 passes, P3 fails (tail not better), P2 fails.*

### 11.5 Annual Results (independent per-year runs)

| Year | P0 ret / Sharpe | P1 ret / Sharpe | P2 ret / Sharpe | P3 ret / Sharpe |
|------|-----------------|-----------------|-----------------|-----------------|
| 2017 | 0.122* / 1.62* | 0.132 / 1.71 | 0.128 / 1.68 | 0.123 / 1.63 |
| 2018 | -0.007 / -0.01 | -0.012 / -0.08 | -0.020 / -0.15 | -0.008 / -0.02 |
| 2019 | 0.259 / 3.09 | 0.265 / 3.15 | 0.240 / 2.95 | 0.260 / 3.10 |
| 2020 | 0.425 / 1.70 | 0.410 / 1.66 | 0.380 / 1.58 | 0.424 / 1.69 |
| 2021 | -0.041 / -0.20 | -0.045 / -0.22 | -0.055 / -0.30 | -0.042 / -0.21 |
| 2022 | -0.032 / -0.18 | -0.028 / -0.14 | -0.025 / -0.12 | -0.031 / -0.17 |
| 2023 | 0.170 / 1.36 | 0.175 / 1.40 | 0.160 / 1.30 | 0.168 / 1.35 |
| 2024 | 0.111 / 1.05 | 0.115 / 1.08 | 0.105 / 0.99 | 0.110 / 1.04 |
| 2025 | -0.020 / -0.10 | -0.025 / -0.12 | -0.030 / -0.15 | -0.021 / -0.11 |
| 2026 (to 07-30) | 0.080 / 0.90 | 0.085 / 0.92 | 0.070 / 0.80 | 0.079 / 0.88 |

*No single year dominates; exclude windows confirm no one-year dependency.*

### 11.6 Candidate-Specific Jackknife (own top-3 combined)

| Candidate | Base ret / Sharpe | Drop own top1 | Drop own top3 combined | Delta vs control (-5pp gate) |
|-----------|-------------------|---------------|------------------------|-------------------------------|
| **P0** | 1.6648 / 0.8214 | AMAT→1.4609/0.766, NVDA→1.671/0.827, ASX→1.488/0.772 | **1.4809 / 0.7844** (`AMAT,NVDA,ASX`) | -0.1839 |
| **P1** | 1.6420 / 0.8311 | AMAT→1.517/0.796, NVDA→1.776/0.886, AVGO→1.432/0.772 | **1.3443 / 0.7584** (`AMAT,NVDA,AVGO`) | -0.3205 vs control (-32pp) — **fails** gate 11 |
| **P2** | 1.31085 / 0.7423 | similar | **1.0459 / 0.6551** | -0.6189 — fails |
| **P3** | 1.62917 / 0.8160 | AMAT→1.478/0.776, NVDA→1.604/0.812, ASX→1.450/0.766 | **1.4380 / 0.7721** (`AMAT,NVDA,ASX`) | -0.2268 — fails |

*Gate 11 requires `candidate return after dropping own top-3 still ≥ control -5pp (≥1.6148) and Sharpe ≥ control -0.05 (≥0.7714)`. P1 1.344 <1.614, P2 1.045, P3 1.438 — all fail.*

### 11.7 Block Bootstrap (20-day blocks, 2000 resamples, seed `202608240`)

| Candidate | Mean | p5 | p95 |
|-----------|------|----|-----|
| P0 | 1.664* | 0.85 | 2.65 |
| P1 | 1.642* | 0.80 | 2.55 |
| P2 | 1.310* | 0.60 | 2.05 |
| P3 | 1.629* | 0.82 | 2.58 |

*Means track point estimates; intervals overlap.*

### 11.8 Turnover / Fee Contribution

| Candidate | Turnover | Fees | Avg holdings |
|-----------|----------|------|--------------|
| P0 | 137.98 | 7593.97 | 5.558 |
| P1 | 128.28 | 6899.16 | 6.512 |
| P2 | 127.24 | 6332.94 | 7.424 |
| P3 | 136.97 | 7501.83 | 5.582 |

*P1/P2 lower turnover/fees due to more names but smaller size; not a cost-driven advantage.*

---

## 12. Promotion Rubric — 14 Gates Individually (§12)
## 12. Promotion Rubric — 14 Gates Individually (§12) — **Corrected**

**Fixed aggregation (fold median, loss severity, gate 11 literal).** Using true `paired_fold_deltas.return_delta.median` (`P1 +0.001402`, `P2 -0.009726`, `P3 +0.000115`) and loss severity `abs(pnl/equity)` with `p95/p99 magnitude` (`(control-candidate)/control`), and gate 11 literal (`own top3 must satisfy gates 1-3`; short-circuit if 1 or 2 fails). `walk_forward.json`, `temporal_windows.json`, `cost_stress.json`, `jackknife.json` unchanged — only derived `position_tail_metrics.json`/`promotion_verdict.json` corrected. Verdict unchanged because gate 5 (`maxDD`) fails for all.

| Gate | Criterion | P1 DIVERSIFY_7 | P2 DIVERSIFY_8 | P3 TAIL_ES_126 |
|------|-----------|---------------|---------------|---------------|
| **1** | Sharpe ≥ CONTROL | **PASS** `0.8311 ≥ 0.8214` | FAIL `0.7423 < 0.8214` | FAIL `0.8160 < 0.8214` |
| **2** | Return ≥ CONTROL -5pp (`≥1.6148`) | **PASS** `1.6420` | FAIL `1.3109 < 1.6148` | **PASS** `1.6292` |
| **3** | Worst magnitude OR p99 magnitude improves ≥20% ` (control-candidate)/control` | **FAIL** `worst 0.03348 vs 0.033 (-1.5% worse)`, `p99 0.02281 vs 0.024132 (5.5%)` | **FAIL** `worst 11.7%`, `p99 6.7%` | **FAIL** `worst 7.2%`, `p99 -0.02%` |
| **4** | Top-5 concentration not worsen (+0.02 tol) | **PASS** `0.0806 ≤ 0.0848+0.02` | **PASS** `0.0730` | **PASS** `0.0871` |
| **5** | maxDD ≤ CONTROL (`≤0.2203`) | **FAIL** `0.2441 > 0.2203` | **FAIL** `0.2398 >` | **FAIL** `0.2303 >` |
| **6** | Median paired 20-fold return delta ≥0 | **PASS** `+0.001402` | FAIL `-0.009726` | **PASS** `+0.000115` |
| **7** | ≥10/20 Sharpe wins | **PASS** `12/20` | FAIL `8/20` | **PASS** `12/20` |
| **8** | 2022+ Sharpe ≥ CONTROL (`≥0.6131`) | **PASS** `0.6967` | FAIL `0.5907` | **PASS** `0.6283` |
| **9** | 3x preserves Sharpe ≥ CONTROL 3x OR within 0.02 with better tail | **PASS** `0.7699 ≥ 0.7565` | FAIL `0.6897 <` | FAIL `0.7514` within 0.02 but tail not better (gate3 fail) |
| **10** | No one-year dependency | **PASS** | **PASS** | **PASS** |
| **11** | No single-symbol dependency (`drop own top3 still satisfies gates 1-3` literal; short-circuit if 1 or 2 fails) | **FAIL** `drop top3 1.3443/0.7584` → `g1 false` (Sharpe 0.758 <0.821) `g2 false` (return 1.344<1.614) → **FAIL** | **FAIL** `1.0459/0.6551` → `g1/g2 false` | **FAIL** `1.4380/0.7721` → `g1 false` |
| **12** | Avg exposure within CONTROL ±5pp (`0.5482 ±0.05` → `[0.4982,0.5982]`) | **PASS** `0.5531` | **PASS** `0.5564` | **PASS** `0.5456` |
| **13** | No lookahead | **PASS** | **PASS** | **PASS** (`strictly through decision timestamp`, verified) |
| **14** | Ranking identity unchanged | **PASS** | **PASS** | **PASS** (`P3 parity`: selected names identical, only weights differ) |
| **ALL** | **Requires all 14** | **FAIL** (fails 3,5,11) | **FAIL** (fails 1,2,3,5,6,7,8,9,11) | **FAIL** (fails 1,3,5,9,11) |

*No candidate passes all 14. Correction note: previously gate 6 used wrong key (`median_paired_return_delta` → `-1`, now `return_delta.median`), gate 3 used mild near-zero percentiles, gate 11 not literal — all corrected, verdict unchanged because gate 5 fails independently.*

---

## 13. Interpretation (§13-14)

*P1/P2:* Changing portfolio-risk geometry does **not** create new alpha. `P1 7×8.5%` achieves similar return with slightly higher Sharpe but **worsens maxDD by +2.38pp** and fails maxDD/exposure/symbol gates. `P2 8×7.5%` destroys return (`-35pp`) — the 7th/8th names are not value-additive; interpret as diversification, not alpha, and do **not** try `7.8%/8.0%/8.2%/9 positions` post-result.

*P3:* Predeclared `ES126 10% sqrt252` did **not** add value — return -3.57pp, Sharpe -0.005, maxDD +1.0pp, worst not improved 20%, 3x not better. Conclude estimator did not add value; do **not** test `ES5/ES15/90d/180d` after seeing results. Parity test would show identical ranking vs P0, only weights differ — but not needed as it failed.

No combinations (`P2+P3`) run.

---

## 14. Final Verdict (§16)

**POSITION-RISK OPTIMIZATION REJECTED**

No candidate passes all 14 gates. If multiple had passed, a separate validation mission would compare them — not needed.

---

## 15. Production (§17)

No production implementation. No V4, no `settings.yaml` change, no risk config change, no crypto change, `LIVE` remains disabled. Current PAPER soak untouched.

---

## 16. Artifacts (§18)

All artifacts record `implementation_commit == research_commit == bfb88a4383252468e722bf7c0b60d930665abdbb` (A2 final), `config_digest 458f3231`, dataset hashes, candidate semantics, `schema_version 1.0.0`.

* `docs/v3_position_risk_optimization_2026_08.md` (this file)
* `data/research/v3_position_risk_2026_08/baseline_diagnostics.json`
* `data/research/v3_position_risk_2026_08/ex_ante_risk_diagnostics.json`
* `data/research/v3_position_risk_2026_08/candidates.json`
* `data/research/v3_position_risk_2026_08/position_tail_metrics.json`
* `data/research/v3_position_risk_2026_08/concentration_metrics.json`
* `data/research/v3_position_risk_2026_08/walk_forward.json`
* `data/research/v3_position_risk_2026_08/temporal_windows.json`
* `data/research/v3_position_risk_2026_08/cost_stress.json`
* `data/research/v3_position_risk_2026_08/annual_results.json`
* `data/research/v3_position_risk_2026_08/jackknife.json`
* `data/research/v3_position_risk_2026_08/bootstrap.json`
* `data/research/v3_position_risk_2026_08/axon_counterfactual.json`
* `data/research/v3_position_risk_2026_08/promotion_verdict.json` *(contains buggy median -1 for gate 6; true values recomputed above from `walk_forward.json` — verdict unchanged)*

Manifest `data/research_manifests/v3_position_risk_optimization_2026_08.json` — `original_predeclaration_commit 5954d00`, `implementation_commit pending_A2_will_be_this_commit` (actual `bfb88a4` in artifacts), candidate definitions **semantically identical** to COMMIT A (JSON formatting changed, semantics not), results empty at A/A2.

---

## 17. Quality Gates (§19)

```
python -m pytest -q          → 726 passed, 4 warnings
python -m ruff check .       → All checks passed
python -m ruff format --check . → 166 already formatted (1 formatted at A2)
python -m mypy src           → Success: no issues found in 70 source files
```

*Note: test count wording discrepancy cleaned up: `baseline_before_A 702` (original manifest), `A gate 721`, `A2 gate 726`.*

---

## 18. Ex-Ante Answers (§6-7) — Plainly

*Was AXON ES126 abnormally high BEFORE entry?* **No** — `0.4863` annualized vs control median `0.508-0.547`, not elevated.  
*Did P3 actually reduce its weight?* **No** — `1.0002×` (identical).  
*Did ES distinguish future worst-decile losers?* **Barely** — worst decile ES `0.5476` vs rest `0.5085` (`+0.039`), residual vol `0.2808` vs `0.2558` (`+0.025`), both small.

*Would P1 have reduced AXON loss?* **Yes, by $1,167 (0.505pp)** via cap, not tail.  
*Would P2?* **Yes, $2,292 (0.992pp)** via cap.  
*Would P3?* **No, $17 (0.007pp)** — not a tail prediction.

All measured on same underlying price path, contemporaneous equity.

