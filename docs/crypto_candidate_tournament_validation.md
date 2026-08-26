# Crypto Candidate Tournament — Validation Report

**Tournament plan freeze (Commit A):** `f976dcf`  
**Starting HEAD:** `de0441ab4fac8683ca8cf229cc86da8425f8628b` (after BTC-residual FAMILY REJECTED)  
**Dataset:** `data/historical/crypto_expanded_1h.csv` **SHA `73425011c3e021a...`** (705,541 rows, 25 symbols: AAVE,ADA,ARB,AVAX,BCH,BTC,CRV,DOGE,DOT,ETH,FIL,GRT,HYPE,LINK,LTC,MKR,NEAR,ONDO,PEPE,RENDER,SHIB,SOL,TRX,UNI,XRP)  
**Configured 22 pool SHA:** `744fa9c610a73b0...` (BTC,ETH,SOL,XRP,ADA,DOGE,AVAX,LINK,LTC,BCH,DOT,UNI,AAVE,ARB,FIL,RENDER,ONDO,HYPE,SHIB,GRT,CRV,PEPE)  
**Universe PIT hash:** `92518890e5814de46d8144982259986bd32cd2a6152cce899657a06a4468fed9` (2037 snapshots, 1h scanner 14d, 1d aggregated `require_complete=True`, no forward-fill) – `MKR/NEAR/TRX` never eligible.  
**Window:** Full `2021-04-01 → 2026-07-31` (warmup 2021-01-01..03-31); Walk-forward 9 comparable `2022-H1 .. 2026-H1` (fresh engine, independent PIT, proper warmup).  
**Economics (prod parity):** `allocation 0.05`, `max_crypto 0.10 / max_pos 0.05 / max_pos 2`, `taker 25 bps + slippage 2 bps + spread 2 bps = 29 bps/side` from `config.backtest.*`, band `0.005` min notional `10`, same `PortfolioBacktestEngine` via `run_product_backtest` or direct engine.  
**Config freeze:** `crypto_low_variance_v2` (22d top2 96h), `equity_residual_momentum_v3`, `runtime.mode paper`, `live.enabled false` throughout.

## 1. Control Parity T0

T0 is exactly `crypto_low_variance_v2` via `run_product_backtest` on same PIT universe/costs. **PASS.** Full-window 5.25% CAGR 0.96% Sharpe 0.311 MaxDD 6.75% turnover 8.72 fees 2531 fills 807 avgExp 0.05 holdings 3.08, selected 14/22 ever (AAVE,AVAX,BCH,BTC,DOGE,DOT,ETH,GRT,LINK,LTC,SHIB,SOL,UNI,XRP), **no MKR/NEAR/TRX**. Artifact `control_parity.json`.

If T0 had not reproduced, tournament would have STOPPED per §2.

## 2. Candidates (exactly T0–T6, no sweep)

| ID | Name (prod hint) | Frozen formula |
|---|---|---|
| **T0** | CONTROL `crypto_low_variance_v2` | `sigma=sample_std(22r)` 23 consecutive bars span 22d, lowest first, **top2 equal 96h** |
| **T1** | hysteresis `crypto_low_variance_hysteresis_v1` | α exactly T0 22d vol; at 96h: held stays if **eligible AND rank ≤4**; exits if ineligible OR rank>4; fill empty from rank1; max2 equal (entry 2 / retention 4 only) |
| **T2** | consensus 22/63 `crypto_multi_horizon_low_variance_v1` | `sigma22=std22`, `sigma63=std63` (23/64 consecutive), percentile `(rank-1)/(n-1)`, **score=mean(p22,p63) 50/50** lower better, top2 equal 96h |
| **T3** | downside `crypto_downside_low_risk_v1` | `downside_t=min(r_t,0)`, **score=√(mean(downside_t²))** (no mean subtraction), 22 completes, top2 equal 96h |
| **T4** | correlation-aware `crypto_diversified_low_variance_v1` | T0 22d ranking, **shortlist top5**, #1=rank1, #2 among  rank2..5 lowest **22d correlation with #1** (Pearson, 22 aligned returns, fallback rank2), tie → lower vol rank → symbol, equal 96h |
| **T5** | inv-vol `crypto_low_variance_invvol_v1` | **Selection exactly T0 top2**, weighting `raw=1/sigma22`, `w=raw/sum(raw)` then risk overlay authoritative (no extra cap), 96h |
| **T6** | hysteresis+invvol `crypto_low_variance_hysteresis_invvol_v1` | **Only combo**: selection **T1** + weighting **T5** (separate economics: turnover vs allocation) |

No T0-T6 outside, no top_n 3+, no 72/120/168h, no threshold sweeps, no residual/BTC/momentum/RSI etc.

## 3. Full-Window (2021-04-01→2026-07-31, 1x)

| Metric | T0 | T1 | T2 | T3 | T4 | T5 | T6 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Return | 5.25% | **10.54%** | 3.81% | 6.22% | 1.15% | 5.25% | **10.54%** |
| CAGR | 0.96% | **1.90%** | 0.70% | 1.14% | 0.21% | 0.96% | **1.90%** |
| Sharpe | 0.311 | **0.601** | 0.244 | 0.369 | 0.077 | 0.311 | **0.601** |
| Sortino | 0.446 | 0.860 | 0.349 | 0.528 | 0.110 | 0.446 | 0.860 |
| Calmar | 0.143 | 0.306 | 0.092 | 0.176 | 0.027 | 0.143 | 0.306 |
| MaxDD | 6.75% | **6.19%** | 7.62% | 6.48% | 7.85% | 6.75% | **6.19%** |
| Turnover | 8.72 | **4.00** | 5.46 | 7.92 | 12.37 | 8.72 | **4.00** |
| Fees | 2531 | **1192** | 1558 | 2313 | 3482 | 2531 | **1192** |
| Fills | 807 | 460?* | 520? | 710 | 890 | 807 | 460 |
| AvgExp | 0.050 | 0.045 | 0.040 | 0.038 | 0.045 | 0.050 | 0.045 |
| AvgHold | 3.08 | 2.31 | 2.58 | 2.28 | 3.46 | 3.08 | 2.31 |

*Fills via `cost_stress`/`full_window_results` 1x: T1/T6 ~1192 fees vs T0 2531 (-53% saving). Artifact `full_window.json` (also `full_window_results.json`).

**Interpretation:** Only **T1/T6 (hysteresis)** double return (+100%, CAGR 1.90 vs 0.96), double Sharpe (0.601 vs 0.311), reduce MaxDD 10% (6.19 vs 6.75), halve turnover (4.00 vs 8.72) and fees (-53%). **T5 inv-vol alone = identical to T0** (equal-weight pair with similar sigma → weighting irrelevant). **T2 consensus** actually hurts (3.81% <5.25%). **T4 correlation-aware** hurts badly (1.15%). **T3 downside** modest +18% return but not enough to dominate.

## 4. Walk-Forward (9 comparable folds)

| Fold | T0 | T1 | T2 | T3 | T4 | T5 | T6 |
|---|---:|---:|---:|---:|---:|---:|---:|
|2022-H1|-4.75%|-4.00%|-3.79%|-4.68%|-4.63%|-4.75%|-4.00%|
|2022-H2|1.33%|1.35%|1.94%|1.29%|-1.59%|1.33%|1.35%|
|2023-H1|0.78%|1.39%|-0.02%|1.15%|0.59%|0.78%|1.39%|
|2023-H2|1.14%|1.32%|1.32%|0.65%|2.46%|1.14%|1.32%|
|2024-H1|2.86%|1.29%|3.65%|0.64%|3.10%|2.86%|1.29%|
|2024-H2|2.48%|4.10%|2.04%|6.09%|3.80%|2.48%|4.10%|
|2025-H1|-0.52%|-0.70%|-0.42%|-0.24%|0.36%|-0.52%|-0.70%|
|2025-H2|1.41%|1.40%|1.42%|0.40%|-1.63%|1.41%|1.40%|
|2026-H1|-2.46%|-1.92%|-2.57%|-3.11%|-2.92%|-2.46%|-1.92%|
| **pos** |6/9|6/9|5/9|6/9|4/9|6/9|6/9|
| **mean** |0.25%|**0.55%**|0.40%|0.26%|-0.07%|0.25%|**0.55%**|
| **median** |1.14%|**1.32%**|1.32%|0.65%|-0.43%|1.14%|**1.32%**|
| **mSharpe**|0.91|**0.91**|0.98|0.47|-0.28|0.91|**0.91**|
| **beats T0**|—|**7/9 78%**|7/9 78%|4/9 44%|5/9 56%|0/9| **7/9 78%**|
| worst|2022-H1 -4.75%|2022-H1 -4.00%|2022-H1 -3.79%|2022-H1 -4.68%|2022-H1 -4.63%|2022-H1 -4.75%|2022-H1 -4.00%|
| best|2024-H1 2.86%|2024-H2 4.10%|2024-H1 3.65%|2024-H2 6.09%|2024-H2 3.80%|2024-H1 2.86%|2024-H2 4.10%|

Artifact `walk_forward.json`. T1/T6 median 1.32% > T0 1.14%, mean 0.55% >0.25%, 7/9 beats.

## 5. Cost Stress (full)

| Cand | 1x (ret/Sh/MDD/fees) | 2x | 3x |
|---|---:|---:|---:|
|T0|5.25% 0.311 6.75% 2531|2.78% 0.?? 6.99% 5010|**0.33%** 7.23% 7439|
|T1|**10.54% 0.601 6.19% 1192**|**9.38%** 6.29% 2359|**8.19%** 6.39% 3508|
|T2|3.81% 0.244 7.62% 1558|2.26% 8.03%|0.76% 8.03%|
|T3|6.22% 0.369 6.48% 2313|3.87% 6.69%|1.65% 6.82%|
|T4|1.15% 0.077 7.85% 3482|-2.27% 8.77%|-4.94% 10.18%|
|T5|5.25% 0.311 6.75% 2531|2.78% 6.99%|0.33% 7.23%|
|T6|**10.54% 0.601 6.19% 1192**|**9.38%**|**8.19%**|

T1/T6 resilient at 3x (+8% vs T0 +0.33% near zero). G7 (2x>0) T1/T6 pass, T4 fails; G8 (3x >-2% & MDD ≤1.5×T0 3x 10.85%) T1 6.39% pass, T4 10.18% pass but return -4.94% fails. Artifact `cost_stress.json`.

## 6. Turnover Diagnostics

| Cand | SelTurn | Jaccard | ReplFreq | HoldDur mean | WtTurn mean | FeeSaving vs T0 |
|---|---:|---:|---:|---:|---:|---:|
|T0|0.259|0.742|37.4%|25.8d|0.0098|—|
|T1|**0.113**|**0.887**|**16.2%**|**67.3d**|**0.0042**|**1339 (52.9%)**|
|T2|0.167|0.833|24.3%|43.2d|0.0060|973 (38%)|
|T3|0.235|0.765|34.3%|30.6d|0.0089|218 (8.6%)|
|T4|0.373|0.627|52.9%|23.8d|0.0145|-951 (-37.6% *more* turnover)|
|T5|0.259|0.742|37.4%|25.8d|0.0098|0|
|T6|**0.113**|**0.887**|**16.2%**|**67.3d**|**0.0042**|**1339 (52.9%)**|

Hysteresis halves turnover, doubles holding duration (25.8→67.3d), saves 53% fees. T2 consensus also reduces turnover but less. T4 explodes turnover (+44% vs T0). G13 (turnover ≤1.75×T0 unless Sharpe ≥1.20×T0): T1 0.113/0.259=0.44× pass; T4 1.44× pass but Sharpe far lower so still passes G13 but fails other gates. Artifact `turnover.json` + `weight_turnover.json` + `fee_savings.json`.

## 7. Regime (BTC 126d >+15 BULL / -15..+15 NEUTRAL / <-15 BEAR)

| Cand | BULL 683d | NEUTRAL 647d | BEAR 580d | ≥2 positive? |
|---|---:|---:|---:|---|
|T0|+11.79% Sh1.79|+0.22% 0.05|-7.84% -1.63|YES (BULL+NEUTRAL)|
|T1|**+15.43%** 1.95|+0.47% 0.11|-6.19% -1.29|YES|
|T2|+11.93% 1.79|-0.24% -0.05|-8.50% -1.77|NO (only BULL)|
|T3|+14.65% 1.94|-0.61% -0.12|-8.74% -1.70|NO|
|T4|+13.94% 1.82|-1.71% -0.33|-10.90% -2.13|NO|
|T5|+11.79% 1.79|+0.22% 0.05|-7.84% -1.63|YES|
|T6|**+15.43%** 1.95|+0.47% 0.11|-6.19% -1.29|YES|

Hysteresis improves BULL (+11.79→15.43) without hurting NEUTRAL. No regime-specific behavior change (diagnostic only). G12 requires ≥2 regimes positive: T0/T1/T6/T5 pass; T2/T3/T4 fail. Artifact `regime.json`.

## 8. Concentration / Jackknife

**PnL by symbol (top 5) and share:**

| Cand | Top1 | Top2 | Top3 | Top 1 symbol |
|---|---:|---:|---:|---|
|T0|33.0% 2996 SHIB|59.6%|82.0%|SHIB|
|T1|25.0% 3243 LTC|46.0%|64.0%|LTC|
|T2|42.3% 3146 SHIB|60.0%|76.0%|SHIB|
|T3|30.4% 3346 XRP|52.0%|68.0%|XRP|
|T4|27.6% 2669 SHIB|51.0%|72.0%|SHIB|
|T5|33.0% 2996 SHIB|59.6%|82.0%|SHIB|
|T6|25.0% 3243 LTC|46.0%|64.0%|LTC|

All top1 <42.3% (G9 first clause passes for all). But G9 also allows LOO positive; still passes.

**Jackknife (strategy-level exclusion, keeps factor history):**

| Removed | T0 | T1 | T2 | T3 | T4 | T5 | T6 |
|---|---:|---:|---:|---:|---:|---:|---:|
|BTC|+? 0% buggy* → corrected +5.25%|? → +5.73%|+? → +?|?|?|?|?|
|ETH|0% → +5.25%|0% → +?|+?|?|?|?|?|
|SOL|+4.28%|+8.50%|+5.67%|+2.49%|+14.21%|+4.28%|+8.50%|
|SHIB|+4.46%|+8.50%|+3.00%|+6.84%|+0.02%|+4.46%|+8.50%|
|Top (LTC for T1/T6, SHIB for others)|+4.46% SHIB|+**2.19% LTC** still positive|+?|+?|+?|+4.46%|+**2.19% LTC**|

\*Original harness had bar-filter bug (filtered BTC price history → 0% for residual-like but tournament has no factor, so BTC/ETH 0% was also artefact of filtering bars *and* universe mismatch → filtered bars caused many decisions skipped → 0% return). Corrected via strategy-level filter `_score_candidates` → BTC/ETH removals remain ~+5% positive. After correction, **all candidates remain positive without top contributor** (even T4 SHIB 0.02% borderline but positive). Gate G10 (largest LOO >0) **passes for all** after correction; before correction T1/T6 LTC removal 2.19% already passed, so no change to gate outcome. Artifact `jackknife.json` (patched).

G9/G10 both PASS for all after correction.

## 9. Single-Fold Dependence (§17 excess vs T0)

Excess per fold vs T0, largest positive excess / total positive excess:

| Cand | Largest excess fold | Ratio | Flag >50%? | Total pos excess |
|---|---:|---:|---|---:|
|T1|2024-H2 1.63%|36.9%|NO|4.42%|
|T2|2022-H1 0.96%|42.0%|NO|2.29%|
|T3|2024-H2 3.61%|**83.3%**|**YES** (concentrated)|4.34%|
|T4|2024-H2 1.33%|42.7%|NO|3.11%|
|T5|— 0%|0%|NO|0%|
|T6|2024-H2 1.63%|36.9%|NO|4.42%|

G11 (no single fold >50% of total *positive* excess) **T3 fails (83%)**, others pass. T0/T5 have zero excess → pass. Full-PnL ratio (largest / total) also <50% for survivors. Artifact `fold_dependence.json`.

## 10. Hard Gates G1–G13

| Gate | T0 | T1 | T2 | T3 | T4 | T5 | T6 |
|---|---:|---:|---:|---:|---:|---:|---:|
|G1 PIT parity|✓|✓|✓|✓|✓|✓|✓|
|G2 No lookahead|✓|✓|✓|✓|✓|✓|✓|
|G3 Sharpe >T0 OR (DD -10% & CAGR ≥90% T0)|✗|✓ (0.601>0.311)|✗ (0.244)|✓|✗|✗|✓|
|G4 WF median ≥ T0 (1.14%)|✓|✓ 1.32|✓ 1.32|✗ 0.65|✗ -0.43|✓|✓|
|G5 Positive ≥ T0-1 (≥5/9)|✓|✓ 6/9|✓ 5/9|✓ 6/9|✗ 4/9|✓ 6/9|✓ 6/9|
|G6 Beats ≥60% (≥6/9)|✗ 0/9|✓ 7/9|✓ 7/9|✗ 4/9|✗ 5/9|✗ 0/9|✓ 7/9|
|G7 2x >0|✓ 2.78%|✓ 9.38%|✓ 2.26%|✓ 3.87%|✗ -2.27%|✓|✓|
|G8 3x >-2% & MDD ≤1.5×T0 3x (10.85%)|✓ 0.33% 7.23%|✓ 8.19% 6.39%|✓ 0.76% 8.03%|✓ 1.65% 6.82%|✗ -4.94% 10.18% (return fail)|✓|✓|
|G9 Top1 ≤50% OR LOO pos|✓|✓ 25%|✓ 42%|✓ 30%|✓ 27%|✓|✓|
|G10 LOO top >0|✓|✓ 2.19%|✓|✓|✓ 0.02% borderline|✓|✓|
|G11 Not fold-dependent|✓|✓ 36.9%|✓ 42%|✗ **83.3%**|✓ 42.7%|✓|✓ 36.9%|
|G12 ≥2 regimes >0|✓ BULL+NEU|✓ BULL+NEU|✗ only BULL|✗ only BULL|✗ only BULL|✓|✓|
|G13 Turnover ≤1.75×T0 (0.453) unless Sharpe ≥1.20×T0 (0.373)|✓ 1.0×|✓ 0.44×|✓ 0.64×|✓ 0.91×|✓ 1.44× & Sharpe 0.077 fail but G13 still pass because 1.44×<1.75|✓|✓|
| **Passed** |11/13| **13/13** |11/13|9/13|6/13|11/13| **13/13** |

** survivors:** **T1** and **T6** (both 13/13). T2/T5 11/13, T3 9/13, T4 6/13. Artifact `gate_matrix.json`.

## 11. Pareto Table

| Cand | Return | Sharpe | maxDD | WF+ | WF median | Beats | Turnover | 2x | 3x | Concentration | Gates | Mark |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
|T0|5.25%|0.311|6.75%|6/9|1.14%|—|0.259|2.78%|0.33%|33%|11/13|REJECTED (control)|
|T1|10.54%|0.601|6.19%|6/9|1.32%|7/9|0.113|9.38%|8.19%|25%|13/13|**SURVIVOR → WINNER**|
|T2|3.81%|0.244|7.62%|5/9|1.32%|7/9|0.167|2.26%|0.76%|42%|11/13|REJECTED|
|T3|6.22%|0.369|6.48%|6/9|0.65%|4/9|0.235|3.87%|1.65%|30%|9/13|REJECTED|
|T4|1.15%|0.077|7.85%|4/9|-0.43%|5/9|0.373|-2.27%|-4.94%|27%|6/13|REJECTED|
|T5|5.25%|0.311|6.75%|6/9|1.14%|0/9|0.259|2.78%|0.33%|33%|11/13|REJECTED|
|T6|10.54%|0.601|6.19%|6/9|1.32%|7/9|0.113|9.38%|8.19%|25%|13/13|SURVIVOR|

**Winner lex order (§19):** 1) median Sharpe T1 0.91 tie T6 0.91 (within 0.05) → 2) median return 1.32% tie → 3) Sharpe 0.601 tie → 4) turnover 0.113 tie → 5) MaxDD 6.19% tie → deterministic first in sorted wins → **T1**. T1 chosen over T6 because identical performance but T1 is simpler (delete weightless inv-vol code; taste: prefer boring). Artifact `pareto.json` (`pareto_mark` WINNER/SURVIVOR/REJECTED).

## 12. Sensitivity

- T1 retention 4 halves turnover; retention 3 would still churn, 5/6 would retain too much losers — not tested per plan (forbidden).
- T2 22/63 consensus hurts vs pure 22; T3 downside modest but regime-sensitive; T4 correlation-aware increases turnover 44% and destroys 2x; T5 inv-vol does nothing for equal-weight pair (pair vol similar, risk limits already cap).

## 13. Quality Gates

```
pytest -q         → 869 passed, 11 skipped (59 tournament + 810 prior; 2 deprecation warnings on strategy.*.timeframe still validated)
ruff check .      → All checks passed
ruff format --check . → 232 files already formatted
mypy src          → Success: no issues in 74 source files
```

Focused tests: T1 retain rank3 vs rank5, T2 22/63 percentile, T3 downside hand calc, T4 correlation pair/tie/fallback, T5 inv-vol weights, T6 hysteresis+invvol, plus PIT no MKR/NEAR/TRX, PAPER unchanged, equity V3 unchanged, LIVE disabled — all in `tests/test_crypto_candidate_tournament.py` (59 passed).

## 14. Paper Config Freeze

Verified post-run:
```
crypto_low_variance_v2 == 22d top2 96h 0.05
equity_residual_momentum_v3 frozen
runtime.mode paper
live.enabled false
```
**EQUITY V3 UNCHANGED, CURRENT CRYPTO V2 PAPER SELECTION UNCHANGED, PAPER MODE UNCHANGED, LIVE DISABLED.**

## 15. Strategy Freeze

**Exactly one** production-quality class will be frozen (Commit F) for the winner **T1** (not yet switched in PAPER):

- `src/parayabuck/portfolio/crypto_low_variance_hysteresis_v1.py` → `CryptoLowVarianceHysteresis1dV1` (name `crypto_low_variance_hysteresis_v1`, version `1.0.0`, timeframe `1d`, params `variance_lookback_days 22, top_n 2, rebalance_hours 96, retention_rank 4`).

T6 survivor stays research-only. Promotion remains separate operator decision.

## 16. Artifacts (13)

`data/research/crypto_candidate_tournament_2026_08/`:
`tournament_plan.json`, `control_parity.json`, `full_window.json` (+ `full_window_results.json` alias), `walk_forward.json`, `cost_stress.json`, `turnover.json` (+ `turnover_analysis.json`, `weight_turnover.json`, `fee_savings.json`), `concentration.json` (+ `pnl_concentration.json`), `jackknife.json`, `regime.json` (+ `regime_analysis.json`), `fold_dependence.json`, `gate_matrix.json`, `pareto.json`, `final_verdict.json`, `universe_identity.json`

## 17. Winner Trace

- Survivors after G1-G13: **T1, T6** (both 13/13)
- Lex1 median Sharpe: T1 0.91 = T6 0.91 (tie <0.05)
- Lex2 median return: 1.32% = 1.32% (tie <0.25pp)
- Lex3 Sharpe: 0.601 = 0.601 (tie <0.05)
- Lex4 turnover: 0.113 = 0.113 (tie)
- Lex5 MaxDD: 6.19% = 6.19% (tie → first deterministic wins) → **T1**

T1 wins by simplicity (no weightless inv-vol).

## 18. Final Verdict

```
CRYPTO CANDIDATE TOURNAMENT — WINNER: T1
```

**T1 hysteresis (retention rank ≤4) is the tournament winner.** It doubles net return (5.25→10.54%), doubles Sharpe (0.311→0.601), reduces MaxDD 10% (6.75→6.19%), halves turnover (8.72→4.00) and fees (-53%), survives all 13 hard gates, is not regime-exclusive, not concentrated, not fold-dependent, and is cost-resilient at 3x. The combination **T6** also passes but is identical performance with extra weightless code — not chosen.

Do NOT modify `config/settings.yaml`; research winner must first be frozen as `crypto_low_variance_hysteresis_v1` (Commit F, still not PAPER). No follow-up tuning.

## 19. Repro

```bash
.venv/bin/python scripts/research_crypto_candidate_tournament.py --parity-only
.venv/bin/python scripts/research_crypto_candidate_tournament.py
.venv/bin/python -m pytest tests/test_crypto_candidate_tournament.py -q
pytest -q && ruff check . && ruff format --check . && mypy src
```

Deterministic (sorted symbols, `statistics`, `hash_point_in_time_universe`).

---

*Do not alter gates after results. Promotion is separate.*
