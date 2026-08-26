# Crypto Candidate Tournament — Pre-Registered Plan

**Freeze:** 2026-08-26 23:02 +03:00 — **before any T1-T6 performance**  
**Starting HEAD:** `de0441ab4fac8683ca8cf229cc86da8425f8628b` (after BTC-residual rejected)  
**Dataset:** `data/historical/crypto_expanded_1h.csv` **SHA `73425011c3e021a...`** (705,541 rows, 25 symbols)  
**Configured 22 pool SHA:** `744fa9c610a73b0...` **Universe PIT hash (prev) `92518890e5814...`** (snapshot 2037)  
**Status:** `PRE-REGISTERED — no candidate performance computed`

## 0. Contract

All candidate formulas, lookbacks, retention ranks, costs, folds, hard gates G1-G13, winner lex order, and Pareto rules below are **frozen** at Commit A. Do not change after seeing results. No follow-up tuning inside this mission.

## 1. Data / Universe

- Historical `data/historical/crypto_expanded_1h.csv` (expected SHA `73425011c3e021a...`; trade SHA `744fa9c...`).
- Use only configured 22: `BTC, ETH, SOL, XRP, ADA, DOGE, AVAX, LINK, LTC, BCH, DOT, UNI, AAVE, ARB, FIL, RENDER, ONDO, HYPE, SHIB, GRT, CRV, PEPE`. **MKR/NEAR/TRX must NOT enter** canonical research.
- PIT: `configured trade_symbols ∩ bars available at t → CryptoUniverseScanner (prod 14d 1h) → eligible pool → strategy`. `1h→1d` via `aggregate_historical_bars(require_complete=True)`; missing = unavailable; no forward-fill/interpolation.

## 2. Common Economics

- `maximum_allocation 0.05`, same risk limits `max_crypto_exposure 0.10 / max_crypto_position 0.05 / max_crypto_positions 2`, same band `0.005` min notional `10`, same `PortfolioBacktestEngine` (`run_product_backtest` + direct engine for research strategies) and production-parity costs (`taker 25 bps + slippage 2 bps + spread 2 bps` authoritative from `config.backtest.*`, persisted as `effective_cost`). Allocation/risk not varied.

## 3. Window

- **Full:** `2021-04-01 → 2026-07-31` (warmup `2021-01-01..03-31`, ≥63d for T2).
- **Walk-forward:** 9 comparable 6-month folds `2022-H1 .. 2026-H1` (fresh engine, independent PIT, pre-fold warmup, no curve slicing). Partial `2026-H2` excluded from 60% gate.

## 4. Candidates (exactly T0–T6)

| ID | Name | Suggested prod name | Formula (frozen) |
|---|---|---|---|
| **T0** | CONTROL `crypto_low_variance_v2` | — | `r = P_t/P_{t-1}-1` 22 returns, `sigma=sample_std` (n-1), 23 consecutive bars span 22d, lowest vol first, **top2 equal**, **96h** |
| **T1** | Rank hysteresis | `crypto_low_variance_hysteresis_v1` | Alpha **exactly T0** 22d vol. At 96h: held symbol stays if **scanner-eligible AND rank ≤4**; exits if not eligible OR rank>4; empty slots filled from rank1 upward; max2 equal. **Only** entry 2 / retention 4 (not 3/5/6). Hypothesis: costs make rank 3→4 churn destructive. |
| **T2** | Multi-horizon consensus | `crypto_multi_horizon_low_variance_v1` | `sigma22=std(22r)`, `sigma63=std(63r)` (require 23 and 64 consecutive bars). Cross-sectionally percentile-rank each `(rank-1)/(n-1)`, **score = mean(p22,p63) 50/50** no weights, lower better, top2 equal 96h. |
| **T3** | Downside-deviation | `crypto_downside_low_risk_v1` | Same 22r, `downside_t = min(r_t,0)`, `score = sqrt(mean(downside_t^2))` lower better, no mean subtraction, no Sortino optimization, 22 completes, top2 equal 96h, tie symbol. |
| **T4** | Correlation-aware pair | `crypto_diversified_low_variance_v1` | T0 22d ranking, **shortlist top5 vol**, pick #1 = rank1, for #2 among ranks 2..5 pick **lowest 22d correlation with #1** (timestamp-aligned Pearson, need 22 aligned returns; tie → lower vol rank → symbol). If no correlation fallback rank2, equal 96h. |
| **T5** | Inverse-vol weighting | `crypto_low_variance_invvol_v1` | **Selection exactly T0 top2**, weighting `raw=1/sigma22`, `w_i=raw_i/ sum(raw)` then **existing risk limits** authoritative (no extra cap), 96h. |
| **T6** | Hysteresis + inv-vol | `crypto_low_variance_hysteresis_invvol_v1` | **Only combination**: selection = **T1** (retention 4) + weighting = **T5** inv-vol. T1/T5 address separate economics (turnover vs allocation) so combined now avoids follow-up mission. No T2/T3/T4 combos. |

No other variants. Forbidden: top_n 3+, 72/120/168h, lookback alternatives, hysteresis 3/5/6, correlation threshold sweeps, residual/BTC/momentum/RSI/SMA/funding/sentiment/volume/meme/category/leverage/dynamic/stops/trailing, grid/Bayesian optimization.

## 5. Metrics

Full-window per §11 (return/CAGR/vol/Sharpe/Sortino/Calmar/maxDD/duration/turnover/fees/fills/avg/max exposure/avg holdings/holding duration/replacements/pos-period). WF §12, cost stress 1x/2x/3x §13, turnover §14 (fee savings vs T0 for T1/T6), concentration/jackknife §15 (BTC/ETH/SOL/SHIB + largest; strategy-level exclusion keeps factor history), regime §16 (BTC 126d >+15 BULL / -15..+15 NEUTRAL / <-15 BEAR), fold dependence §17 (largest positive excess / total positive excess >50% flag, separate full-PnL ratio).

## 6. Hard Rejection Gates G1–G13 (ALL must pass)

- **G1** PIT parity (no MKR/NEAR/TRX) PASS
- **G2** No lookahead/incomplete leakage PASS
- **G3** Full Sharpe >T0 OR MaxDD improves ≥10% while CAGR ≥90% T0
- **G4** WF median return ≥ T0
- **G5** Positive folds ≥ T0 −1 (with 9 folds, ≥5 if T0 6)
- **G6** Beats T0 in ≥60% (≥6/9)
- **G7** 2x cost >0
- **G8** 3x not catastrophic: return >-2% AND MaxDD ≤1.5× T0 3x MaxDD
- **G9** Top1 ≤50% OR LOO remains positive return & Sharpe
- **G10** Largest contributor LOO directionally positive
- **G11** No single fold supplies >50% of **total positive excess vs T0** (excess definition, not full PnL; flag >50%)
- **G12** Positive in ≥2 of BULL/NEUTRAL/BEAR
- **G13** Turnover ≤1.75× T0 unless Sharpe ≥1.20× T0

Do not alter after results.

## 7. Survivor Ranking (if ≥2 pass)

1. highest WF **median Sharpe**
2. tie within 0.05 → highest WF **median return**
3. tie within 0.25 pp → highest full-window **Sharpe**
4. tie within 0.05 → **lower turnover** wins
5. tie → **lower MaxDD** wins

No subjective selection.

## 8. Pareto

Columns `Return | Sharpe | maxDD | WF+ | WF median | Beats | Turnover | 2x | 3x | Concentration | Gates` marked `DOMINATED/NON-DOMINATED/REJECTED/SURVIVOR/WINNER`.

## 9. No Paper Promotion

Even if winner exists, **do not modify `config/settings.yaml`**, do not select winner in PAPER, do not enable runtime. Freeze only the winning strategy class (suggested names above) — other candidates stay research-only; promotion is separate operator decision.

## 10. Commit Structure

- **A** freeze plan — NO results (this commit)
- **B** implement candidates + unit tests — NO promotion
- **C** full-window + walk-forward
- **D** costs + turnover + regime + concentration + jackknife
- **E** gate matrix + Pareto + final verdict
- **F** only if winner: freeze ONE winning strategy — still NOT switch PAPER

## 11. Quality

`pytest -q`, `ruff check .`, `ruff format --check .`, `mypy src` must pass before verdict. Tests in `tests/test_crypto_candidate_tournament.py` (T0 parity, T1 retain 3/remove5/invalid, T2 22/63 percentile, T3 downside hand calc, T4 correlation pair/tie/fallback, T5 inverse-vol weights, T6 hysteresis+invvol, plus no-future/PIT/PAPER/equity/LIVE).

---

*Do not alter gates after seeing results.*
