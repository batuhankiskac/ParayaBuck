# Multi-Market Strategy Validation — equity_residual_momentum_v1 & crypto_time_series_momentum_v1

Generated: 2026-08-12 · Research-only evaluation using the PRODUCTION strategy
classes, the production historical engine, the production point-in-time
universe policy, the production cost model and the production risk/execution
policy. Drawdown halts are SHADOW events (production would halt; the curve
kept trading), identical to prior research runs.

**Validation v2 (this document)** re-runs every candidate after the
broad-pool alpha pipeline was IMPLEMENTED (scanner emits the eligible pool;
the strategy alpha ranks the pool on batch history; the top
`maximum_stream_symbols` become the streamed observation set) and after the
residual-momentum beta was fixed to TIMESTAMP-ALIGNED returns. The v1 numbers
(`data/research/strategy_validation_v1/`) measured the OLD pipeline where the
scanner's quality ranking implicitly truncated the investable set to ~30
before alpha — they are RETRACTED as the representative claim for the new
pipeline and kept only as a controlled comparison.

Artifacts: `data/research/strategy_validation_v2/{equity,crypto,references,summary}.json`
· Harness: `scripts/validate_strategy_candidates.py`

## Data

| Market | Source | Bars | Window | Adjustment |
|---|---|---|---|---|
| Equity | `equity_1200_2016_2026.csv` (1,195 symbols, SIP/IEX hybrid daily) | 1,738,468 | 2016-01-04 .. 2026-07-30 | split-adjusted prices + explicit dividends |
| Crypto | `crypto_18_1h_2024_2026.csv` (18 Alpaca USD pairs) | 232,905 (1h) | 2024-08-01 .. 2026-07-31 | raw |

Known limitations (disclosed): the equity dataset reflects currently-supported
Alpaca assets — delisted names cannot be reconstructed (survivorship bias);
point-in-time scanner membership is applied within the supplied bar set.
Crypto coverage starts 2024-08; the evaluation window is 19 months
(2024-12 .. 2026-07), a materially adverse crypto regime (BTC -33.5%, ETH
-48.4% buy-and-hold). Historical spread/broad sector metadata is unavailable
and explicitly degraded.

## Costs (production model)

- Equity: adverse slippage (2 bps) + half spread (2 bps) per fill + SEC
  Section 31 / FINRA TAF / CAT effective-dated by trade date.
- Crypto: market orders at Alpaca tier-1 taker 25 bps per side + 2 bps
  slippage + 2 bps spread; 24/7 calendar.
- Stress: 1x / 2x / 3x of configured costs.

---

## EQUITY — `equity_residual_momentum_v1`

**Hypothesis:** after stripping the common market component (rolling
CAPM-style beta vs SPY on TIMESTAMP-ALIGNED return pairs), residual returns
retain cross-sectional momentum without the beta lottery of raw momentum
(Blitz, Huij & Martens 2011).

**Signal (1d, monthly):** 12m/1m residual momentum; OLS beta on the trailing
273 aligned bars; residuals; compounded over 252 bars skipping the last 21;
top 30% idiosyncratic-volatility names rejected; top-8 by score, inverse-vol
sized, 10% per-asset cap, SPY excluded. **Alpha ranks the FULL eligible pool
(avg 179 names, from the ~300 Stage-A shortlist); the streamed observation
set (30) is the alpha-top + references.** Production overlay: 50% equity
sleeve, 60% max gross, 40% min cash, 6 positions. Fixed specification.

### Full period 2017-01-03 .. 2026-07-30 (9.5y, 2016 warmup) — BROAD-POOL PIPELINE

| cost | return | CAGR | Sharpe | Sortino | Calmar | maxDD | profit factor | trades | avg holdings |
|---|---|---|---|---|---|---|---|---|---|
| 1x | +59.2% | 4.98% | 0.47 | 0.60 | 0.20 | 25.5% | 1.49 | 300 | 5.5 |
| 2x | +56.8% | 4.81% | 0.46 | 0.59 | 0.19 | 25.6% | 1.47 | 300 | 5.5 |
| 3x | +54.5% | 4.65% | 0.45 | 0.57 | 0.18 | 25.8% | 1.45 | 300 | 5.5 |

**Benchmarks (same window):** SPY buy-and-hold +269% (Sharpe 0.87, maxDD
34.2%); diversified ETF B0 CAGR 8.9% / Sharpe 0.80 / maxDD -24.0%; B1 CAGR
8.1% / Sharpe 0.81 / maxDD -22.8%. Frozen references on the same broad pool:
dynamic_trend V1 CAGR 4.4% / Sharpe 0.59 / maxDD 13.0%; long-horizon V2 CAGR
2.9% / Sharpe 0.41 / maxDD 16.0%.

### PIPELINE FINDING (why v1 numbers are retracted)

Controlled comparison, identical engine/cost/risk, same strategy code, only
the investable universe differs:

| Pipeline | investable universe | CAGR | Sharpe | maxDD | PF | folds+ |
|---|---|---|---|---|---|---|
| v1 (scanner-quality-30 before alpha) | ~30 | 6.93% | 0.88 | 11.0% | 2.29 | 15/18 |
| v2 (alpha on the broad pool) | ~179 | 4.98% | 0.47 | 25.5% | 1.49 | 11/18 |

The scanner's eligibility-quality ranking (liquidity + volatility-fit +
completeness) was an implicit FACTOR SCREEN: residual momentum on the most
liquid, moderately volatile names was materially stronger than on the full
eligible pool. This is a genuine, architecture-level finding — the same
effect shows on both frozen reference strategies (V1 9.0% -> 4.4% CAGR, V2
8.5% -> 2.9% CAGR when moved to the broad pool). It does NOT argue for
reverting to the scanner-truncated pipeline (that would recreate the
stream-limit==opportunity-universe anti-pattern); it argues for treating the
scanner as a factor screen too, and for a future mission to study
alpha-conditioned eligibility. The v2 numbers are the honest claim for the
product architecture.

### Walk-forward (anchored, 18 six-month folds, broad pool)

- Positive folds: **11/18 (61%)** · mean +2.32% · median +0.71% per fold
- Worst folds: 2018H2 -9.9%, 2021H2 -5.4%, 2020H2 -4.9%; best: 2020H1
  +17.9%, 2018H2-2019H1 +11.6%, 2022H2 +11.3%. No catastrophic regime
  collapse; 113/115 monthly rebalances executed (no data-gap skips).
- Per-fold return/sharpe/maxDD/turnover/trade count/top contributors in
  `equity.json`.

### Concentration

- Top-1 realized contributor ASX $8.0k; top-2 share 11.6%, top-3 16.1% —
  MORE diversified than the v1 run (23.7%/33.6%); no NVDA/TSLA dominance.
- Max single-symbol weight 11.5% (target 10% + drift); average holdings 5.5;
  average cash ~45-55%.

**EQUITY VERDICT: A — residual momentum VALIDATED → PAPER candidate**, with
the corrected broad-pool numbers. Net CAGR 4.98% > 0, Sharpe 0.47 > 0,
PF 1.49 > 1, majority of walk-forward folds positive, diversified, maxDD
25.5% below SPY's 34.2% and comparable to the B0/B1 ETF benchmarks, survives
3x cost. The v1 Sharpe 0.88 claim is retracted in favor of these numbers.

---

## CRYPTO — `crypto_time_series_momentum_v1`

**Hypothesis:** per-asset time-series momentum (Moskowitz, Ooi & Pedersen
2012) — asset vs its OWN history: 30d return > 0 AND close > own 15d SMA;
inverse-vol sized, per-asset cap, weekly decisions (168h). Validated 4h and
1d variants, identical calendar lookbacks; the crypto pool (all eligible
pairs) is alpha-ranked for the 12-symbol stream.

### Full period 2024-12-01 .. 2026-07-31 (19 months)

| variant | cost | return | CAGR | Sharpe | maxDD | PF | trades | WF folds+ |
|---|---|---|---|---|---|---|---|---|
| 1d | 1x | -12.2% | -7.6% | -1.64 | 13.4% | 0.41 | 64 | 1/5 |
| 1d | 2x | -13.9% | -8.7% | -1.88 | 14.9% | 0.35 | — | — |
| 1d | 3x | -15.5% | -9.8% | -2.12 | 16.3% | 0.30 | — | — |
| 4h | 1x | -7.7% | -4.7% | -0.85 | 9.6% | 0.55 | 60 | 1/5 |
| 4h | 2x | -9.1% | -5.6% | -1.03 | 11.0% | 0.49 | — | — |
| 4h | 3x | -10.6% | -6.6% | -1.21 | 12.4% | 0.44 | — | — |

**Benchmarks:** BTC -33.5% (Sharpe -0.41, maxDD 53.1%), ETH -48.4%, static
50/50 -40.9%; old `crypto_cross_sectional_momentum_1h_v1` -8.8% on the same
policy. The TSM candidate cut drawdown versus buy-and-hold but produced NO
positive edge: net CAGR < 0, Sharpe < 0, PF < 1 in both variants, 1/5 folds
positive. Fees ≈ 1.6-1.8% of equity over the period (not the cause of
failure — no gross edge). BTC weight share 7.7-11.5% of the sleeve; BTC not
the top contributor; stablecoin pairs never traded.

**CRYPTO VERDICT: C — time-series momentum family NOT VALIDATED.** No further
crypto momentum variants per the strategy-hopping stop rule. Crypto remains
DISABLED.

---

## Multi-market compatibility

- Equity (1d, monthly) and crypto (4h/1d, weekly) run simultaneously on one
  account: runtime aggregates each required timeframe, one
  `PortfolioRuntime` per market, one shared repository/risk layer (tests:
  `test_strategy_timeframes.py`, `test_multi_market_portfolio.py`).
- Combined gross ≤ 60%, cash ≥ 40%, per-sleeve caps, per-asset caps (a
  single crypto asset cannot absorb the sleeve), position limits, SELL→BUY,
  idempotency and restart recovery validated.
- Scanner isolation: each market's universe refreshes independently and
  fails closed on its own.
- Broad-pool alpha: the streamed observation set is the strategy-alpha top
  of the eligible pool (validated: `test_observation_set.py`); the backtest
  completeness gate follows the observation set (live parity), so a halted
  pool member cannot block decisions.

## Decision (this mission)

- **EQUITY: A** — `equity_residual_momentum_v1` enabled in PAPER config
  (maximum_allocation 0.50, broad-pool alpha, corrected v2 numbers).
- **CRYPTO: C** — `crypto_time_series_momentum_v1` NOT enabled; crypto
  strategy stays disabled.
- LIVE remains disabled. No parameters were tuned after seeing walk-forward
  folds; no further grid search performed.
- PAPER forward plan (next phase): observe scanner membership, candidate
  ranking, targets, fills, slippage/fees, positions, reconciliation, risk
  incidents, turnover, PnL for the equity candidate. No parameter tuning
  based on a few days of PAPER performance.
