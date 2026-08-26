# Crypto BTC-Residual Low-Variance — Validation Report

**Research identity:** `crypto_residual_low_variance_v1` (pre-registered; only winning spec would become class)  
**Starting HEAD:** `973eff8e2f3536c024ba7885123f9d5ba355c27b`  
**Plan freeze HEAD (Commit A):** `98b3d90`  
**Harness + tests HEAD (Commit B):** `a04fef1`  
**Final result HEAD (this doc, Commit E):** to be filled after commit  
**Dataset:** `data/historical/crypto_expanded_1h.csv` (705,541 rows, 25 symbols)  
**Dataset SHA-256:** `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`  
**Configured 22-symbol pool SHA-256 (sorted-join):** `744fa9c610a73b0d1afefb1024ef8117894995b7b49483a02c7178219d8ab37e`  
**Dataset-symbols SHA-256:** `40c4a6f4e1738956db35c44a30e59bac44add78db49a8ffd597c4e0bc9c06942`  
**Universe hash (PIT, 1h scanner):** `92518890e5814de46d8144982259986bd32cd2a6152cce899657a06a4468fed9` (snapshots 2037, daily)  
**Config:** `strategy.crypto.name == crypto_low_variance_v2` (0.05 sleeve, 22d vol, 96h), `strategy.equity.name == equity_residual_momentum_v3`, `runtime.mode == paper`, `live.enabled == false` (frozen throughout)  
**Window:** Full `2021-04-01 → 2026-07-31` (warmup `2021-01-01..03-31`, 63d for R2). Walk-forward: 9 comparable 6-month calendar folds `2022-H1 .. 2026-H1` (partial `2026-H2` reported but excluded from 60% gate).  
**Costs (production parity):** `taker 25 bps + slippage 2 bps + spread 2 bps = 29 bps per side` (`config.backtest.*`), rebalance band `0.005`, min notional `10`. Stress `1x/2x/3x` on `slippage+spread+fee`.  
**BTC factor:** `r_i,t = alpha + beta * r_BTC,t + epsilon` OLS intercept over trailing window, `sigma_resid = sample_std(epsilon)` (n-1), timestamp-aligned, no forward-fill/interpolation/lookahead, consecutive-days `23 bars span 22d`, tie-break by symbol. **BTC special (Option A):** `BTC/USD` eligible via total vol `sample_std(BTC returns 22d)` — never regressed on itself (would be near-zero and force BTC first).  
**Candidates (exactly R0–R3, no sweep):** R0 CONTROL 22d top2 96h; R1 RESIDUAL 22 (beta22/resid22); R2 RESIDUAL 63 (beta63/resid22); R3 CONSENSUS mean percentile of 22 & 63.

## 1. Control Parity

CONTROL is exactly `crypto_low_variance_v2` via `run_product_backtest` on `PortfolioBacktestEngine` with same PIT universe and costs. Parity re-ran on current HEAD.

| Metric | R0 full window |
|---|---:|
| Total return | +5.25% |
| CAGR | 0.96% |
| Sharpe | 0.311 |
| Sortino | 0.446 |
| Calmar | 0.143 |
| MaxDD | 6.75% |
| Turnover | 8.72 |
| Fees total | $2,531 |
| Fills | 807 |
| Avg exposure | 0.050 |
| Avg holdings | 3.08 |
| Selected set (14 of 22 ever) | AAVE, AVAX, BCH, BTC, DOGE, DOT, ETH, GRT, LINK, LTC, SHIB, SOL, UNI, XRP |
| PIT violation (MKR/NEAR/TRX selected?) | **NO** |

Artifact: `data/research/crypto_residual_low_variance_2026_08/control_parity.json` — `pass: true`. If CONTROL had not reproduced, mission would have STOPPED per §3.

## 2. Full-Window Metrics (2021-04-01 → 2026-07-31, 1x costs)

| Metric | R0 CONTROL | R1 RES22 | R2 RES63 | R3 CONSENSUS |
|---|---:|---:|---:|---:|
| Return | **+5.25%** | +4.47% | **+8.50%** | +2.37% |
| CAGR | **0.96%** | 0.82% | **1.54%** | 0.44% |
| Sharpe | **0.311** | 0.237 | **0.436** | 0.142 |
| Sortino | 0.446 | 0.333 | **0.619** | 0.188 |
| Calmar | 0.143 | 0.101 | **0.209** | 0.045 |
| MaxDD | **6.75%** | 8.13% | 7.39% | 9.86% |
| Turnover | 8.72 | 12.05 | 10.68 | 10.14 |
| Fees | 2531 | 3484 | 3151 | 2882 |
| Fills | 807 | 1119 | 970 | 969 |
| Avg exposure | 0.050 | 0.049 | 0.048 | 0.043 |
| Max exposure | n/a | n/a | n/a | n/a |
| Avg holdings | 3.08 | 3.12 | 2.57 | 2.65 |
| Positive-period ratio | 0.492 | 0.495 | 0.488 | 0.464 |
| Avg selected beta to BTC (diag median) | 1.18 | 1.18 | 1.18 | 1.18 |
| Avg residual vol median | — | 0.018 | 0.018 | 0.018 |
| Selected symbols (ever) | 14 | 14 | 14 | 14 |

**Interpretation:** Only **R2 (RES63)** beats CONTROL on return, CAGR, Sharpe, Sortino, Calmar with modestly higher turnover (+22% vs R0) and slightly deeper MaxDD. R1 and R3 underperform CONTROL on all risk-adjusted metrics. R2’s edge is not via lower turnover — turnover is higher but economically justified (Sharpe uplift +40%). Artifact: `full_window_results.json`.

## 3. Walk-Forward (9 comparable folds, independent engine + PIT per fold)

| Fold | R0 ret | R0 Sharpe | R1 ret | R1 Sharpe | R2 ret | R2 Sharpe | R3 ret | R3 Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022-H1 | -4.75% | -2.83 | -4.93% | -2.48 | -4.34% | -2.12 | -4.45% | -2.17 |
| 2022-H2 | 1.33% | 0.91 | 1.67% | 0.96 | 1.78% | 1.07 | 1.78% | 1.07 |
| 2023-H1 | 0.78% | 0.65 | 3.49% | 2.02 | **4.19%** | 2.41 | 2.66% | 1.81 |
| 2023-H2 | 1.14% | 1.26 | 1.37% | 1.21 | 1.33% | 1.22 | 1.24% | 1.14 |
| 2024-H1 | 2.86% | 1.33 | 3.82% | 1.63 | **5.49%** | 2.24 | 5.49% | 2.24 |
| 2024-H2 | 2.48% | 1.73 | 2.76% | 1.62 | 3.30% | 2.09 | 2.63% | 1.92 |
| 2025-H1 | -0.52% | -0.36 | -2.70% | -1.54 | -2.11% | -1.19 | -2.60% | -1.44 |
| 2025-H2 | 1.41% | 1.08 | -0.19% | -0.12 | -1.65% | -1.30 | -1.04% | -0.82 |
| 2026-H1 | -2.46% | -2.02 | -2.86% | -1.83 | -2.10% | -1.44 | -2.77% | -2.01 |
| **Positive** | **6/9 (67%)** | | 5/9 (56%) | | **5/9 (56%)** | | 5/9 (56%) |
| **Mean** | 0.25% | | 0.27% | | **0.65%** | | 0.33% |
| **Median** | 1.14% | 0.91 | 1.37% | 0.96 | **1.33%** | **1.07** | 1.24% | 1.07 |
| **Beats CONTROL** | — | | **5/9 (56%)** | | **7/9 (78%)** | | **6/9 (67%)** |
| Worst | 2022-H1 -4.75% | | 2022-H1 -4.93% | | 2022-H1 -4.34% | | 2022-H1 -4.45% |
| Best | 2024-H1 2.86% | | 2024-H1 3.82% | | 2024-H1 5.49% | | 2024-H1 5.49% |

Artifact: `walk_forward.json`. Gate #3 requires `positive folds >= CONTROL (6) AND median >= CONTROL (1.14%)`. **R2 fails positive count by 1 (5 vs 6)** though median passes. Gate #4 requires beats >=60% — **R2 passes (78%)**, R1 fails (56%), R3 passes (67%) but R3 fails other gates.

## 4. Cost Stress (full window)

| Candidate | 1x | 2x | 3x |
|---|---:|---:|---:|
| **R0** | +5.25% (Sh 0.311, MDD 6.75%, fees 2.5k) | +2.78% (0.174, 6.99%, 5.0k) | +0.33% (0.035, 7.23%, 7.4k) |
| **R1** | +4.47% (0.237) | +0.79% (0.058) | **-2.49%** (-0.107) |
| **R2** | **+8.50% (0.436)** | **+4.97% (0.265)** | **+2.03% (0.120)** |
| **R3** | +2.37% (0.142) | -0.41% | -3.44% |

- Gate #5 (2x remains net positive): **R0/R2 pass**, R1 borderline pass (0.79%), R3 fails.
- Gate #6 (3x not catastrophic: MDD not >2× control 3x MDD and CAGR not < -5%): **All pass** (R2 3x still +2% and MDD 8.42% < 2×7.23=14.46). Artifact: `cost_stress.json`.

## 5. Regime Robustness (BTC 126d >+15% BULL / -15..+15% NEUTRAL / <-15% BEAR)

| Candidate | BULL (683d) | NEUTRAL (647d) | BEAR (580d) | ≥2 regimes positive? |
|---|---:|---:|---:|---|
| **R0** | **+11.79%** (Sh 1.79) | +0.22% (0.05) | **-7.84%** (-1.63) | **YES** (BULL+NEUTRAL) |
| R1 | +13.07% (1.72) | **-0.20%** (-0.01) | -9.46% (-1.68) | **NO** (only BULL) |
| **R2** | **+14.21%** (1.85) | **+1.33%** (0.23) | -8.31% (-1.54) | **YES** |
| R3 | +12.39% (1.66) | -0.92% (-0.14) | -10.09% (-1.97) | **NO** |

R1 and R3 are regime-sensitive (derive essentially all value from BULL only; NEUTRAL slightly negative). R2 and CONTROL are not exclusively one regime — R2 even has +1.33% in NEUTRAL where R1/R3 are negative. Gate #11 requires ≥2 regimes positive — **R0/R2 pass, R1/R3 fail**. Artifact: `regime_analysis.json`.

## 6. PnL Concentration

| Candidate | Top1 (share of positive PnL) | Top2 | Top3 | Symbol count | Max single weight |
|---|---:|---:|---:|---:|---:|
| **R0** | SHIB 33.0% (2,996) | 59.6% | 82.0% | 14 | 0.054 |
| R1 | SHIB 26.9% (3,380) | 52.5% | 74.2% | 14 | 0.054 |
| **R2** | **BTC 28.8% (4,100)** | 55.2% | 79.6% | 14 | 0.054 |
| R3 | SHIB 39.8% (3,700) | 77.8% | 91.7% | 14 | 0.054 |

- Gate #7 (no single symbol >50% unless LOO still attractive): **All pass** (max 39.8% for R3, still <50%). Artifact: `pnl_concentration.json`.

## 7. Jackknife — Leave-One-Symbol-Out (corrected methodology: exclude from selection, keep BTC factor history)

| Removed | R0 ret | R0 Sharpe | R1 ret | R1 Sharpe | R2 ret | R2 Sharpe | R3 ret |
|---|---:|---:|---:|---:|---:|---:|---:|
| **BTC/USD** | +5.25% 0.311 | +5.73% 0.288 | **+2.73% 0.155** | +1.67% | 
| **ETH/USD** | +5.25% 0.311 | +2.70% 0.146 | **+6.98% 0.348** | +1.89% |
| **SOL/USD** | +4.28% 0.377 | +7.35% 0.538 | **+8.17% 0.420** | +8.88% 0.666 |
| **Top contributor** (SHIB for R0/R1/R3, BTC for R2) | +4.46% 0.279 (SHIB) | +2.79% 0.160 (SHIB) | **+2.73%** (BTC, already) / +5.15% (SHIB) | -0.07% 0.014 (SHIB) |

- **Note:** Original harness had a bug where `filtered_bars = [b for b in bars_1d if b.symbol != sym]` removed BTC price history, causing residual strategies to have zero qualified candidates (0% return) when BTC or ETH was “removed”. This was corrected to a strategy-level exclusion (`_score_candidates` filter) that **keeps BTC bars for factor regression** while excluding the symbol from selection. The corrected numbers above are the patched `jackknife.json`.
- Gate #8 (remove-largest-contributor remains directionally positive): **R0 pass** (SHIB removal +4.46%), **R1 pass** (+2.79%), **R2 pass** (BTC removal +2.73%, SHIB removal +5.15%), **R3 FAIL** (SHIB removal -0.07% — largest contributor 39.8% removal turns slightly negative). Artifact: `jackknife.json` (patched).

## 8. Turnover Analysis (96h cadence, selection Jaccard)

| Candidate | Selection turnover (1-mean Jaccard) | Jaccard mean | Jaccard median | Holding duration mean (days) | Replacement frequency |
|---|---:|---:|---:|---:|---:|
| **R0** | 0.259 | 0.742 | — | — | 37.4% |
| R1 | **0.358** (+38% vs R0) | 0.642 | — | — | 50.7% |
| **R2** | **0.315** (+22% vs R0) | 0.685 | — | — | 44.5% |
| R3 | 0.310 (+20% vs R0) | 0.690 | — | — | 44.5% |

R1’s residual 22 is noisier (+38% turnover). R2’s stable 63d beta moderates turnover to +22% — economically justified by Sharpe uplift +40% (gate #10). R3 similar. Gate #10: turnover increase <2× unless Sharpe uplift >20% — **R2 passes**, R1 fails (marginal Sharpe negative), R3 fails (negative Sharpe). Artifact: `turnover_analysis.json`.

## 9. Beta / Residual Diagnostics

- Beta distribution (R1 22d, representative; R2/R3 share same 22d slice): median **1.175**, mean 1.27, p10 **0.687**, p90 **1.831**, min 0.12 max 4.8, valid **73.6%** of attempts (26.4% insufficient/missing consecutive data or non-finite; pathological huge beta >10: 0, near-zero BTC variance captured as insufficient). For R2 63d beta is more stable (not tabulated but residual vol median similar 0.018).
- Residual vol: median **0.018** (1.8% daily), mean 0.019, p10 0.009 p90 0.030.
- BTC correlation of selected portfolio: mean ~0.65 for all (low-variance selection picks lower-beta coins; residual selection picks slightly lower correlation but not dramatically). No pathological regressions observed; fail-safe exclusions trigger correctly when BTC variance ~0 or alignment missing. Artifact: `beta_diagnostics.json`.

## 10. Promotion-Gate Matrix (12 pre-registered gates)

| # Gate | R0 CONTROL | R1 RES22 | R2 RES63 | R3 CONSENSUS |
|---|---:|---:|---:|---:|
| 1 Sharpe > CONTROL | — | **FAIL** (0.237<0.311) | **PASS** (0.436>0.311) | FAIL |
| 2 CAGR > CONTROL or lower MaxDD comparable | — | FAIL | **PASS** (1.54>0.96) | FAIL |
| 3 WF positive >= CONTROL (6) AND median >= CONTROL (1.14%) | — | **FAIL** (5 vs 6) | **FAIL** (5 vs 6) — median passes but count fails by 1 | FAIL |
| 4 Beats CONTROL >=60% (≥6/9) | — | FAIL (5/9=56%) | **PASS** (7/9=78%) | PASS (6/9=67%) |
| 5 2x cost remains net positive | — | PASS (0.79%) | **PASS** (4.97%) | FAIL (-0.41%) |
| 6 3x not catastrophic | — | PASS | **PASS** | PASS |
| 7 No single symbol >50% (unless LOO attractive) | — | PASS (26.9%) | **PASS** (28.8%) | PASS (39.8%) |
| 8 Remove-largest-contributor still >0 | — | **PASS** (SHIB removal 2.79%)* | **PASS** (BTC 2.73%, SHIB 5.15%)* | **FAIL** (-0.07%)* |
| 9 No single fold explains >50% of total *excess* | — | FAIL (2023-H1 3.49% vs mean 0.27) | **FAIL** (2023-H1 4.19% vs mean 0.65; max 5.49 vs total 8.50 = 65% >50%) | FAIL |
| 10 Turnover justified (<2× unless Sharpe +20%) | — | FAIL (turnover +38% but Sharpe lower) | **PASS** (+22% & Sharpe +40%) | FAIL |
| 11 ≥2 regimes positive | — | FAIL (only BULL) | **PASS** (BULL+NEUTRAL) | FAIL (only BULL) |
| 12 No PIT parity violation | — | PASS | **PASS** | PASS |
| **Total PASS** | — | **5/12** | **9/12** (10/12 after jackknife patch would be 10/12, but #3 and #9 still fail) | **4/12** |

\* Original harness scored R1/R2/R0 0% for BTC/ETH removals due to bar-filter bug that removed factor history; patched to strategy-level exclusion (keeps BTC bars). After patch, gate #8 passes for R0/R1/R2.

**Conclusion on gates:** No candidate passes ALL 12. **R2 (RES63)** is the only one that passes a majority (9/12) with clear full-window, cost, regime, concentration, and turnover justification, but fails walk-forward positive count by one fold and single-fold dominance heuristic — concentrated strong folds 2023-H1 and 2024-H1. R1 and R3 fail decisively.

## 11. Comparison Table (§17)

| Metric | R0 CONTROL | R1 RES22 | R2 RES63 | R3 CONSENSUS |
|---|---:|---:|---:|---:|
| Return | 5.25% | 4.47% | **8.50%** | 2.37% |
| CAGR | 0.96% | 0.82% | **1.54%** | 0.44% |
| Sharpe | 0.311 | 0.237 | **0.436** | 0.142 |
| Sortino | 0.446 | 0.333 | **0.619** | 0.188 |
| Calmar | 0.143 | 0.101 | **0.209** | 0.045 |
| MaxDD | 6.75% | 8.13% | 7.39% | 9.86% |
| Turnover | 8.72 | 12.05 | 10.68 | 10.14 |
| Fees | 2531 | 3484 | 3151 | 2882 |
| WF positive | 6/9 | 5/9 | 5/9 | 5/9 |
| WF median | 1.14% | 1.37% | 1.33% | 1.24% |
| Beats control | — | 5/9 | **7/9** | 6/9 |
| 2x cost | +2.78% | +0.79% | **+4.97%** | -0.41% |
| 3x cost | +0.33% | -2.49% | **+2.03%** | -3.44% |
| Top contributor | SHIB 33% | SHIB 27% | BTC 29% | SHIB 40% |
| Ex-top contributor (LOO) | +4.46% | +2.79% | +2.73% (BTC) / +5.15% (SHIB) | -0.07% |

## 12. Sensitivity

- **Beta window 22 vs 63:** 22d is noisier (higher turnover, lower Sharpe), 63d stabilizes beta without harming residual horizon (still 22d resid). Consensus (mean percentile) does not help — it inherits 22d noise and underperforms both.
- **No other variants tested:** per §6/§15, no beta 30/45/90, no top_n 3/4, no 72/120/168h, no momentum/BTC-regime/RSI/volume/funding/sentiment/vol-target/leverage/inverse-vol/stops/meme/category tuning. Giant search explicitly avoided.

## 13. Quality Gates

```
pytest -q         -> 810 passed, 11 skipped, 2 warnings (deprecation on strategy.timeframe still validated)
ruff check .      -> All checks passed
ruff format --check . -> 232 files formatted (1 reformatted then pass)
mypy src          -> Success: no issues found in 74 source files
```

Focused tests: OLS arithmetic, residual calc, alignment, missing BTC, insufficient window, no lookahead, BTC special (total vol vs residual 0), tie-break deterministic, PIT exclusion (MKR/NEAR/TRX never selected), control parity — all in `tests/test_crypto_residual_low_variance.py` (16 passed, 11 skipped outline stubs that would have run if harness not present).

## 14. Paper Config Freeze

Verified post-run:

```
config.strategy.crypto.name == crypto_low_variance_v2
config.strategy.equity.name == equity_residual_momentum_v3
runtime.mode == paper
live.enabled == false
```

And `maximum_allocation == 0.05`, `max_crypto_exposure_pct == 0.10`, `max_crypto_position_pct == 0.05`, `max_crypto_positions == 2`. **EQUITY V3 UNCHANGED, CURRENT CRYPTO V2 PAPER SELECTION UNCHANGED, PAPER MODE UNCHANGED, LIVE DISABLED.**

## 15. Strategy Freeze

**No new strategy class frozen.** Per §20, only the winning frozen spec would become `crypto_residual_low_variance_v1`, and still not PAPER-selected (promotion is a SEPARATE mission). Since no candidate passes all 12 gates, **no production strategy is created** (research classes remain in `scripts/research_crypto_residual_low_variance.py` only).

## 16. Artifacts (under `data/research/crypto_residual_low_variance_2026_08/`)

- `research_plan.json` (pre-registered, commit A)
- `control_parity.json`
- `full_window_results.json`
- `walk_forward.json`
- `cost_stress.json`
- `regime_analysis.json`
- `pnl_concentration.json`
- `jackknife.json` (patched to correct BTC-factor handling)
- `turnover_analysis.json`
- `beta_diagnostics.json`
- `final_verdict.json` (prelim `FAMILY REJECTED`; this doc is the final verdict)

## 17. Final Verdict

```
CRYPTO BTC-RESIDUAL LOW-VARIANCE FAMILY REJECTED
```

**Why:** No pre-registered candidate (R1–R3) passes **ALL 12** promotion gates. The closest, **R2 (RESIDUAL 63: beta 63d / resid 22d)**, shows the only consistent improvement over CONTROL (return +62%, Sharpe +40%, 78% of folds beat control, 2x/3x cost resilient, turnover justified, regime not exclusive, concentration <50%, LOO still positive after correction), but it **fails gate #3** (positive folds 5 vs 6 required) by one fold and **fails gate #9** (single 2023-H1/2024-H1 folds explain >50% of total window PnL). Given the pre-registration contract that gates **must not be weakened after seeing results** (research_plan.json docs/crypto_residual_low_variance_research_plan.md), R2 cannot be labeled VALIDATED. R1 and R3 fail decisively (worse Sharpe/CAGR, regime-sensitive, turnover-unjustified, R3 fragile to SHIB removal).

**Do NOT run another residual-volatility parameter search.** Per §19, move to a genuinely different next hypothesis (not beta-window tuning). The rejection is informative: residual low-variance with 22d resid does not robustly dominate total low-variance; the only modest edge (R2’s 63d stable beta) is driven by a couple of strong half-years (2023-H1, 2024-H1) and a narrow miss on walk-forward breadth, so it remains **WEAK — NOT PROMOTED** in the rubric but formally **REJECTED** per strict gate logic. No `crypto_residual_low_variance_v1` class is frozen; PAPER stays on `crypto_low_variance_v2`.

## 18. Commit Structure

- **A** `98b3d90` research(crypto-residual): freeze pre-registered plan — no results yet
- **B** `a04fef1` research(crypto-residual): implement harness + arithmetic tests — no promotion
- **C/D** (this commit) research(crypto-residual): full-window + walk-forward + cost/regime/concentration/jackknife/turnover/diagnostics
- **E** (this doc) research(crypto-residual): final verdict FAMILY REJECTED

## 19. Reproducibility

```bash
.venv/bin/python scripts/research_crypto_residual_low_variance.py       # full
.venv/bin/python scripts/research_crypto_residual_low_variance.py --parity-only
.venv/bin/python -m pytest tests/test_crypto_residual_low_variance.py -q
.venv/bin/python -m pytest -q
ruff check . && ruff format --check . && mypy src
```

All outputs are deterministic (sorted symbols, `statistics.fmean`/`stdev`, `hash_point_in_time_universe`).

---

*Do not alter gates after seeing results. Promotion is a SEPARATE mission.*
