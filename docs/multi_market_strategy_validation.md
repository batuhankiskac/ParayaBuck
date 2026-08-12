# Multi-Market Strategy Validation — equity_residual_momentum_v1 & crypto_time_series_momentum_v1

Generated: 2026-08-12 · Research-only evaluation using the PRODUCTION strategy
classes, the production historical engine, the production point-in-time
universe policy, the production cost model and the production risk/execution
policy. Drawdown halts are SHADOW events (production would halt; the curve
kept trading), identical to prior research runs (policy version per
`strategy_foundation_v1`).

Artifacts: `data/research/strategy_validation_v1/{equity,crypto,references,summary}.json`
· Harness: `scripts/validate_strategy_candidates.py`

## Data

| Market | Source | Bars | Window | Adjustment |
|---|---|---|---|---|
| Equity | `equity_1200_2016_2026.csv` (1,195 symbols, SIP/IEX hybrid daily) | 1,738,468 | 2016-01-04 .. 2026-07-30 | split-adjusted prices + explicit dividends |
| Crypto | `crypto_18_1h_2024_2026.csv` (18 Alpaca USD pairs) | 232,905 (1h) | 2024-08-01 .. 2026-07-31 | raw |

Known limitations (disclosed, not hidden): the equity dataset reflects
currently-supported Alpaca assets — delisted names cannot be reconstructed
(survivorship bias); point-in-time scanner membership is applied within the
supplied bar set. Crypto coverage starts 2024-08; the evaluation window is
19 months (2024-12 .. 2026-07), a materially adverse crypto regime (BTC
-33.5%, ETH -48.4% buy-and-hold). Historical spread/broad sector metadata is
unavailable and explicitly degraded (universe methodology string).

## Costs (production model)

- Equity: adverse slippage (2 bps) + half spread (2 bps) per fill + SEC
  Section 31 / FINRA TAF / CAT effective-dated by trade date.
- Crypto: market orders at Alpaca tier-1 taker 25 bps per side + 2 bps
  slippage + 2 bps spread; 24/7 calendar.
- Stress: 1x / 2x / 3x of the configured costs.

---

## EQUITY — `equity_residual_momentum_v1`

**Hypothesis:** after stripping the common market component (rolling
CAPM-style beta vs SPY), residual returns retain cross-sectional momentum
predictability without the beta lottery of raw momentum (Blitz, Huij &
Martens 2011).

**Signal (1d, monthly rebalance):** 12m/1m residual momentum — OLS beta on
273 trailing bars; residual momentum = compounded residuals over 252 bars
skipping the last 21; defensive filter rejects the top 30% of the
cross-section by annualized idiosyncratic volatility (lottery-like names);
top-8 by score, inverse-vol sized, 10% per-asset cap, SPY excluded.
Production risk overlay: 50% equity sleeve, 60% max gross, 40% min cash, 6
positions. Fixed specification — no parameter grid.

### Full period 2017-01-03 .. 2026-07-30 (9.5y, 2016 warmup)

| cost | return | CAGR | Sharpe | Sortino | Calmar | maxDD | profit factor | trades | turnover | avg holdings |
|---|---|---|---|---|---|---|---|---|---|---|
| 1x | +89.9% | 6.93% | 0.88 | 1.12 | 0.63 | 11.0% | 2.29 | 264 | 44.5 | 5.7 |
| 2x | +87.4% | 6.78% | 0.86 | 1.09 | 0.62 | 11.0% | 2.25 | 264 | 44.5 | 5.7 |
| 3x | +84.9% | 6.63% | 0.84 | 1.07 | 0.60 | 11.0% | 2.21 | 264 | 44.5 | 5.7 |

**Benchmarks (same window):** SPY buy-and-hold +269% (Sharpe 0.87, maxDD
34.2%); diversified ETF B0 CAGR 8.9% / Sharpe 0.80 / maxDD -24.0%; B1 CAGR
8.1% / Sharpe 0.81 / maxDD -22.8% (B0/B1 from `strategy_foundation_v1`).
Frozen references on identical data: dynamic_trend V1 CAGR 9.0% / Sharpe
0.93 / maxDD 14.2%; long-horizon V2 CAGR 8.5% / Sharpe 0.92 / maxDD 15.0%.

The candidate does NOT beat 100% SPY CAGR (expected: 50% sleeve cap, 6
positions, defensive filter) — but it is positive net-of-cost with
comparable Sharpe to SPY/B0/B1 and **roughly one-third of their drawdown**,
lower turnover than V1/V2 (264 vs 780/652 trades), and it **survives 3x
cost** (Sharpe 0.84 at 3x).

### Walk-forward (anchored, 18 six-month folds)

- Positive folds: **15/18 (83%)** · mean fold return +3.12% · median +3.33%
- Fold returns range: -2.9% .. +7.3% per half-year; the three negative folds
  are 2021H2 (-0.6%), 2021H2-2022H1 (-2.9%), 2024H2-2025H1 (-2.5%) — no
  catastrophic regime collapse.
- Fold stats per fold (return/sharpe/maxDD/turnover/trade count/top
  contributors) recorded in `equity.json`.

### Concentration

- Top-1 realized PnL contributor: XLK (sector ETF), $13.9k; top-2 share of
  positive PnL 23.7%, top-3 33.6% — no NVDA/TSLA dominance.
- Max single-symbol weight 14.1% (target 10% + drift); 14 sectors traded
  (static map, "other" for unmapped names); average holdings 5.7; average
  cash ~45-55% (sleeve + buffer).
- Sector concentration limit (scanner `maximum_per_sector`) applies at the
  universe level.

**EQUITY VERDICT: A — residual momentum VALIDATED → PAPER candidate.**

---

## CRYPTO — `crypto_time_series_momentum_v1`

**Hypothesis:** time-series momentum per asset against its OWN history
(Moskowitz, Ooi & Pedersen 2012): qualify on 30d return > 0 AND close > own
15d SMA; inverse-vol sized, per-asset cap, weekly decisions (168h) to keep
turnover low against 25 bps taker fees. Two validated signal resolutions
compared — 4h and 1d — with identical calendar lookbacks (30d/15d), so the
comparison is sampling resolution, not parameter mining.

### Full period 2024-12-01 .. 2026-07-31 (19 months)

| variant | cost | return | CAGR | Sharpe | maxDD | profit factor | trades | turnover | fees % avg equity |
|---|---|---|---|---|---|---|---|---|---|
| 1d | 1x | -10.7% | -6.6% | -1.41 | 11.8% | 0.44 | 64 | 6.4 | 1.79% |
| 1d | 2x | -12.3% | -7.6% | -1.64 | 13.3% | 0.39 | 66 | 6.4 | — |
| 1d | 3x | -13.9% | -8.6% | -1.87 | 14.8% | 0.34 | 66 | 6.4 | — |
| 4h | 1x | -7.4% | -4.5% | -0.82 | 9.2% | 0.56 | 60 | 5.8 | 1.64% |
| 4h | 2x | -8.9% | -5.5% | -1.00 | 10.6% | 0.49 | 60 | 5.8 | — |
| 4h | 3x | -10.4% | -6.4% | -1.18 | 12.0% | 0.44 | 60 | 5.8 | — |

**Benchmarks (same window):** BTC -33.5% (Sharpe -0.41, maxDD 53.1%), ETH
-48.4%, static 50/50 -40.9%; old `crypto_cross_sectional_momentum_1h_v1`
-7.8% (Sharpe -0.94) on the same policy — also negative. The TSM candidate
cut drawdown versus buy-and-hold substantially but produced NO positive
edge: net CAGR < 0, Sharpe < 0, profit factor < 1 in both variants.

### Walk-forward (anchored, 5 four-month folds)

- Positive folds: **1/5** (both variants). Small sample (19 months of data)
  disclosed; the direction is consistent with the full period.

### Risk measures

- Average active coins 1.8-2.4 (sleeve capped at 2 positions by
  `max_crypto_positions`); average crypto exposure ≈ 5-7% of account.
- Turnover 5.8-6.4 (avg equity) over the period; fees ≈ 1.6-1.8% of equity —
  cost drag is material but NOT the reason for failure (edge is absent gross
  of the fee argument; the 4h variant's lower turnover did not save it).
- BTC dependence: average BTC weight share 7.7-11.5% of the sleeve; BTC not
  the top contributor (XRP/AAVE were); stablecoin pairs never traded
  (excluded by universe policy).
- No catastrophic single-event loss: maxDD 9-15% vs BTC 53%.

**CRYPTO VERDICT: C — time-series momentum family NOT VALIDATED.** Per the
strategy-hopping stop rule, no further crypto momentum variants will be
tested in this mission. Crypto remains DISABLED in strategy configuration.

---

## Multi-market compatibility

- Equity (1d, monthly) and crypto (4h/1d, weekly) run simultaneously on one
  account: runtime aggregates each required timeframe, one
  `PortfolioRuntime` per market, one shared repository/risk layer
  (validated by tests: `tests/test_strategy_timeframes.py`,
  `tests/test_multi_market_portfolio.py`).
- Combined gross ≤ 60%, cash buffer ≥ 40%, per-sleeve caps
  (`maximum_allocation`), per-asset caps (a single crypto asset cannot
  absorb the sleeve), position limits, SELL→BUY ordering, idempotency and
  restart recovery validated.
- Scanner isolation: each market's universe refreshes independently and
  fails closed on its own.

## Decision (this mission)

- **EQUITY: A** — `equity_residual_momentum_v1` enabled in PAPER config
  (maximum_allocation 0.50, fixed specification above).
- **CRYPTO: C** — `crypto_time_series_momentum_v1` NOT enabled; crypto
  strategy stays disabled.
- LIVE remains disabled. No parameters were tuned after seeing walk-forward
  folds; no further grid search performed.
- PAPER forward plan (next phase): observe scanner membership, candidate
  ranking, targets, fills, slippage/fees, positions, reconciliation, risk
  incidents, turnover, PnL for the equity candidate. No parameter tuning
  based on a few days of PAPER performance.
